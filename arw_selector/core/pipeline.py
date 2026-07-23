"""배치 분석 파이프라인.

4000장을 코어 수만큼 나눠 병렬로 처리합니다. 워커 함수와 인자는 전부
모듈 최상위 + picklable이어야 합니다 — macOS의 ProcessPoolExecutor는
spawn 방식이라 fork처럼 부모 상태를 물려받지 못합니다.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import time
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from . import focus as focus_module
from .cache import AnalysisCache, default_cache_path
from .config import AnalyzeConfig, Config
from .grouping import dhash
from .raw_io import PreviewError, iter_raw_files, load_preview, read_metadata
from .thumbs import thumbnail_path, write_thumbnail
from .types import ImageRecord

log = logging.getLogger(__name__)

ProgressCallback = Callable[["Progress"], None]
CancelCheck = Callable[[], bool]


@dataclass(frozen=True)
class Progress:
    done: int
    total: int
    cached: int
    failed: int
    elapsed: float
    current: Path | None = None

    @property
    def ratio(self) -> float:
        return self.done / self.total if self.total else 1.0

    @property
    def eta_seconds(self) -> float | None:
        """남은 예상 시간. 초반 몇 장으로는 추정이 튀므로 5장 이후부터."""
        if self.done < 5 or self.done >= self.total:
            return None
        return self.elapsed / self.done * (self.total - self.done)


# ---------------------------------------------------------------- 워커


def analyze_file(
    path: Path, config: AnalyzeConfig, cache_dir: Path | None = None
) -> ImageRecord:
    """한 장을 분석합니다. 예외를 던지지 않고 record.error에 담아 돌려줍니다.

    4000장 중 한 장이 손상됐다고 배치 전체가 중단되면 안 됩니다.
    """
    try:
        metadata = read_metadata(path)
    except Exception as exc:  # noqa: BLE001
        metadata = None
        log.debug("메타데이터 실패 %s: %s", path.name, exc)

    try:
        preview = load_preview(path)
        result = focus_module.analyze_focus(
            preview,
            detect_long_edge=config.detect_long_edge,
            laplacian_k=config.laplacian_k,
            tenengrad_k=config.tenengrad_k,
        )
        # 프리뷰가 메모리에 올라와 있는 지금 장면 지문과 썸네일을 같이 뜹니다.
        # 나중에 구하려면 4000장을 전부 다시 디코딩해야 합니다.
        scene_hash = dhash(preview)
        if cache_dir is not None:
            # 경로 해시로 이름을 짓습니다. stem을 쓰면 하위 폴더의 동명 파일이
            # 서로의 썸네일을 덮어씁니다.
            write_thumbnail(preview, thumbnail_path(Path(cache_dir), path))

        return ImageRecord(path=path, metadata=metadata, focus=result, dhash=scene_hash)
    except PreviewError as exc:
        return ImageRecord(path=path, metadata=metadata, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        log.warning("분석 실패 %s: %s", path.name, exc)
        return ImageRecord(path=path, metadata=metadata, error=f"{type(exc).__name__}: {exc}")


def _init_worker() -> None:
    """워커 프로세스에서 새는 로그를 막습니다.

    spawn으로 뜨는 워커는 부모의 로깅 설정을 물려받지 못합니다(핸들러 0개).
    그 상태에서 exifread가 CR3·HEIF마다 뱉는 warning은 logging의 최후 수단
    핸들러를 타고 stderr로 그대로 새어 나갑니다.

    여기서 파일 핸들러를 붙이지는 않습니다 — 여러 프로세스가 같은 회전
    로그를 함께 쓰면 회전 시점에 서로의 파일을 덮어씁니다. 워커의 실패는
    ImageRecord.error에 담겨 부모로 돌아오고, 부모가 로그에 남깁니다.
    """
    logging.getLogger("exifread").setLevel(logging.ERROR)


def _worker(payload: tuple[str, AnalyzeConfig, str | None]) -> ImageRecord:
    """ProcessPoolExecutor 진입점. 최상위 함수여야 pickle이 됩니다."""
    path_str, config, cache_dir = payload
    return analyze_file(Path(path_str), config, Path(cache_dir) if cache_dir else None)


#: 워커 하나가 쓰는 대략적인 메모리(MB). 파이썬·OpenCV·rawpy를 올린 값에
#: 프리뷰(73MB)와 흑백 사본(24MB)을 더한 실측 근사치입니다.
WORKER_MEMORY_MB = 350

#: 이 지점을 넘으면 오히려 느려집니다.
#:
#: 실측(300장, 32코어): 6워커 3.40배 / 8워커 3.60배 / **12워커 3.77배** /
#: 16워커 3.61배 / 24워커 3.44배. 32코어를 다 써도 3.8배에서 멈추는 것은
#: 40MB짜리 RAW를 읽는 디스크가 병목이기 때문입니다. 그 지점을 넘겨 워커를
#: 늘리면 서로 디스크를 다투느라 되레 손해입니다.
#:
#: (표본이 작으면 워커 시작 비용에 가려 곡선이 왜곡됩니다. 48장으로 쟀을
#: 때는 6에서 포화하는 것처럼 보였습니다.)
MAX_USEFUL_WORKERS = 12


def _available_memory_mb() -> int | None:
    """쓸 수 있는 물리 메모리(MB). 알 수 없으면 None."""
    try:  # 리눅스
        return int(os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1024 / 1024)
    except (AttributeError, ValueError, OSError):
        pass

    if sys.platform == "darwin":
        # macOS에는 SC_AVPHYS_PAGES가 아예 없습니다 — 이름 자체가 없어서
        # ValueError로 떨어지고, 위 경로는 리눅스에서만 성립합니다. 그래서
        # 맥에서는 램 제한이 통째로 걸리지 않은 채 코어 수만으로 워커를
        # 정하고 있었습니다 (8GB 맥북에어에서 9워커 = 스왑).
        #
        # vm_stat의 free·inactive·speculative가 당장 되돌려받을 수 있는 쪽입니다.
        # active와 wired는 빼야 합니다 — 지금 쓰이고 있는 메모리입니다.
        try:
            output = subprocess.run(
                ["vm_stat"], capture_output=True, text=True, timeout=5,
            ).stdout
            match = re.search(r"page size of (\d+) bytes", output)
            page = int(match.group(1)) if match else 4096
            pages = 0
            for name in ("Pages free", "Pages inactive", "Pages speculative"):
                found = re.search(rf"{name}:\s+(\d+)", output)
                if found:
                    pages += int(found.group(1))
            if pages:
                return int(pages * page / 1024 / 1024)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        return None

    if os.name != "nt":
        return None
    try:
        import ctypes

        class _Status(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _Status()
        status.dwLength = ctypes.sizeof(_Status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return int(status.ullAvailPhys / 1024 / 1024)
    except Exception:  # noqa: BLE001
        return None


def resolve_workers(requested: int | None) -> int:
    """워커 수를 정합니다.

    코어 수만 보고 늘리면 저사양 PC에서 램이 모자라 스왑이 걸립니다.
    스왑이 시작되면 코어를 더 써도 오히려 느려집니다. 그래서 세 가지로
    묶습니다: 코어 수(한 코어는 UI/OS 몫), 쓸 수 있는 램, 그리고 실측상
    이득이 사라지는 지점.
    """
    if requested and requested > 0:
        return requested

    workers = max(1, (os.cpu_count() or 2) - 1)
    workers = min(workers, MAX_USEFUL_WORKERS)

    available = _available_memory_mb()
    if available:
        # 절반만 씁니다. 나머지는 UI와 OS 몫입니다.
        by_memory = int(available * 0.5 // WORKER_MEMORY_MB)
        workers = max(1, min(workers, by_memory))
    return workers


SECONDS_PER_PHOTO_PER_WORKER = 0.45
"""워커 하나가 사진 한 장을 처리하는 데 걸리는 시간(초).

실측: 720장을 워커 12개로 26.9초 → 장당 0.037초, 워커당 0.45초.
프리뷰 추출 + 얼굴 검출 + 선명도 측정 + 썸네일 쓰기까지 포함한 값입니다.
바디나 디스크에 따라 달라지므로 어림값으로만 씁니다.
"""

PROCESS_POOL_STARTUP_SECONDS = 2.0
"""프로세스 풀이 뜨는 데 걸리는 시간. spawn 방식이라 무시할 수 없습니다."""


def estimate_analysis_seconds(count: int, workers: int | None = None) -> float:
    """사진 count장을 분석하는 데 걸릴 시간(초) 어림값.

    "캐시를 지우면 다시 만듭니다"라고만 하면 사용자는 그게 10초인지 10분인지
    모릅니다. 정확할 필요는 없고, 판단할 수 있을 정도면 됩니다.
    """
    if count <= 0:
        return 0.0
    workers = workers or resolve_workers(None)
    return (PROCESS_POOL_STARTUP_SECONDS
            + count * SECONDS_PER_PHOTO_PER_WORKER / max(1, workers))


def format_duration(seconds: float) -> str:
    """사람이 읽는 소요 시간. 초만 찍으면 큰 배치에서 감이 안 옵니다."""
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"약 {seconds:.0f}초"
    if seconds < 3600:
        minutes = seconds / 60
        return f"약 {minutes:.0f}분" if minutes >= 2 else "약 1분"
    return f"약 {seconds / 3600:.1f}시간"


# ---------------------------------------------------------------- 배치 실행


def analyze_paths(
    paths: Sequence[Path],
    config: Config | None = None,
    cache_path: Path | None = None,
    use_cache: bool = True,
    progress_cb: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> list[ImageRecord]:
    """주어진 RAW 목록을 분석합니다. 입력 순서를 유지해 반환합니다."""
    config = config or Config()
    paths = list(paths)
    total = len(paths)
    started = time.perf_counter()

    if not total:
        return []

    results: dict[Path, ImageRecord] = {}
    cached_count = 0
    cache: AnalysisCache | None = None

    if use_cache and cache_path is not None:
        try:
            cache = AnalysisCache(cache_path, config.analyze.cache_key())
            cache.open()
            results = cache.get_many(paths)
            cached_count = len(results)
            log.info("캐시 히트 %d/%d", cached_count, total)
        except Exception as exc:  # noqa: BLE001 - 캐시 문제로 분석을 막지 않습니다
            log.warning("캐시 사용 불가, 전체 재분석: %s", exc)
            cache = None

    pending = [p for p in paths if p not in results]
    failed = sum(1 for r in results.values() if r.error)
    done = len(results)

    if progress_cb:
        progress_cb(Progress(done, total, cached_count, failed, 0.0))

    if pending:
        workers = resolve_workers(config.workers)
        fresh: list[ImageRecord] = []
        log.info("%d장 분석 시작 (워커 %d개)", len(pending), workers)

        with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker) as executor:
            cache_dir = str(cache_path.parent) if cache_path is not None else None
            futures: dict[Future, Path] = {
                executor.submit(_worker, (str(p), config.analyze, cache_dir)): p
                for p in pending
            }
            try:
                for future in as_completed(futures):
                    if should_cancel and should_cancel():
                        log.info("사용자 취소")
                        for f in futures:
                            f.cancel()
                        break

                    path = futures[future]
                    try:
                        record = future.result()
                    except Exception as exc:  # noqa: BLE001 - 워커 프로세스 자체가 죽은 경우
                        record = ImageRecord(path=path, error=f"워커 오류: {exc}")

                    results[path] = record
                    fresh.append(record)
                    done += 1
                    if record.error:
                        failed += 1
                        # 실패는 반드시 여기서 남깁니다. 워커는 spawn으로 떠서
                        # 부모의 로깅 설정을 물려받지 못하므로(핸들러 0개),
                        # 워커 안에서 부른 log.warning은 로그 파일에 닿지
                        # 않습니다. 창만 있는 .app에서는 아예 사라집니다.
                        log.warning("분석 실패 %s: %s", path.name, record.error)

                    if progress_cb:
                        progress_cb(
                            Progress(
                                done, total, cached_count, failed,
                                time.perf_counter() - started, path,
                            )
                        )

                    # 중간에 끊겨도 여기까지는 건지도록 주기적으로 흘려 씁니다
                    if cache and len(fresh) >= 200:
                        cache.put_many(fresh)
                        fresh.clear()
            finally:
                if cache and fresh:
                    cache.put_many(fresh)

    if cache:
        cache.close()

    ordered = [results[p] for p in paths if p in results]
    log.info(
        "분석 완료: %d장 (캐시 %d, 실패 %d) %.1fs",
        len(ordered), cached_count, failed, time.perf_counter() - started,
    )
    return ordered


def analyze_folder(
    folder: Path,
    config: Config | None = None,
    use_cache: bool = True,
    progress_cb: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> list[ImageRecord]:
    """폴더를 스캔해서 분석합니다. 캐시는 폴더 옆에 둡니다."""
    config = config or Config()
    folder = Path(folder)
    paths = iter_raw_files(folder, recursive=config.recursive)
    return analyze_paths(
        paths,
        config=config,
        cache_path=default_cache_path(folder),
        use_cache=use_cache,
        progress_cb=progress_cb,
        should_cancel=should_cancel,
    )
