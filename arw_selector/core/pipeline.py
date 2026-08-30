"""The batch analysis pipeline.

4000 frames are split across the core count and processed in parallel. The
worker function and its arguments all have to be module top-level and
picklable - macOS's ProcessPoolExecutor uses spawn, so it cannot inherit
the parent's state the way fork does.
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
from typing import Callable, Iterable, Sequence

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
        """The estimated time remaining. The estimate jumps around over the
        first few frames, so only from 5 frames on."""
        if self.done < 5 or self.done >= self.total:
            return None
        return self.elapsed / self.done * (self.total - self.done)


# ---------------------------------------------------------------- worker


def analyze_file(
    path: Path, config: AnalyzeConfig, cache_dir: Path | None = None
) -> ImageRecord:
    """Analyses one frame. Throws no exception; puts it in record.error and
    returns.

    One damaged frame out of 4000 must not stop the whole batch.
    """
    try:
        metadata = read_metadata(path)
    except Exception as exc:  # noqa: BLE001
        metadata = None
        log.debug("메타데이터 실패 %s: %s", path.name, exc)

    try:
        preview = load_preview(
            path, demosaic_small=config.demosaic_small_preview)

        # The camera AF position. The preview already has the EXIF
        # orientation applied, so maker_meta applies the orientation too
        # and hands it back in preview coordinates. It is read **always** -
        # because af_face (the AF <-> main subject mismatch confidence
        # signal) is not an option but a basic signal. Only using it as an
        # ROI (af_roi_hint) is optional. A file whose metadata cannot be
        # read (read_metadata failed) has an unknown orientation too, so it
        # is skipped.
        af_box = None
        if metadata is not None:
            from .maker_meta import af_preview_box

            af_box = af_preview_box(
                path, metadata.orientation, preview.shape[1], preview.shape[0]
            )

        # The reduced copy is made once here and shared by all three.
        # Previously the three each reduced from 6192x4128 on their own -
        # having the fingerprint and the thumbnail reuse what is made for
        # face detection anyway removes 53ms per frame (measured on a Mac).
        reduced = focus_module.reduce_for_detection(
            preview, config.detect_long_edge)

        result = focus_module.analyze_focus(
            preview,
            detect_long_edge=config.detect_long_edge,
            laplacian_k=config.laplacian_k,
            tenengrad_k=config.tenengrad_k,
            af_box=af_box,
            use_af_roi=config.af_roi_hint,
            center_priority=config.center_priority,
            noise_compensation=config.noise_compensation,
            reduced=reduced,
        )
        # While the preview is up in memory, the scene fingerprint and the
        # thumbnail are taken at the same time. Getting them later would
        # mean decoding all 4000 frames again.
        scene_hash = dhash(reduced)
        if cache_dir is not None:
            # Named by a hash of the path. Use the stem and same-named
            # files in subfolders overwrite each other's thumbnails.
            write_thumbnail(reduced, thumbnail_path(Path(cache_dir), path))

        return ImageRecord(path=path, metadata=metadata, focus=result, dhash=scene_hash)
    except PreviewError as exc:
        return ImageRecord(path=path, metadata=metadata, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        log.warning("분석 실패 %s: %s", path.name, exc)
        return ImageRecord(path=path, metadata=metadata, error=f"{type(exc).__name__}: {exc}")


def _init_worker() -> None:
    """Stops logs leaking out of the worker processes.

    A worker started by spawn does not inherit the parent's logging setup
    (0 handlers). In that state, the warning exifread emits for every CR3
    and HEIF rides logging's last-resort handler and leaks straight out to
    stderr.

    No file handler is attached here - if several processes write to the
    same rotating log together, they overwrite each other's files at the
    moment of rotation. A worker's failure is carried back to the parent in
    ImageRecord.error, and the parent writes it to the log.
    """
    logging.getLogger("exifread").setLevel(logging.ERROR)


def _worker(payload: tuple[str, AnalyzeConfig, str | None]) -> ImageRecord:
    """The ProcessPoolExecutor entry point. It has to be a top-level
    function to be pickled."""
    path_str, config, cache_dir = payload
    return analyze_file(Path(path_str), config, Path(cache_dir) if cache_dir else None)


#: The maximum memory one worker uses (MB). The denominator when working
#: out the worker count.
#:
#: Measured on a Mac (M1 Pro) - the high-water ru_maxrss of the worker
#: process, on batches of 30~100 frames
#: (tools/research/research_worker_memory.py):
#:
#:   NEF Z50II 20.7MP    385MB   ARW A6700 25.6MP    448MB
#:   CR3 R6M3  32.3MP    525MB   JPEG 45MP         1,347MB
#:   HIF(HEIF) 25.6MP  1,225MB
#:
#: RAW is proportional to the preview pixels (~65MB + 15MB/MP - on top of
#: the 65MB floor of loading Python, OpenCV and rawpy sit the preview BGR
#: and a float copy for the measurements). JPEG decodes the original whole
#: and then reads the file once more to get the EXIF orientation, and takes
#: a rotated copy as well, so it is double per pixel; HEIF is triple
#: because of the libheif decoder.
#:
#: 350 did not even reach the cheapest path (NEF 385MB). Open a HEIF folder
#: on an 8GB Mac and 4 workers take 4.3GB (measured 4,261MB) and swapping
#: starts - the total RSS is linear in the worker count (ARW at 1/4/8
#: workers: 448 / 1,748 / 3,268MB).
#:
#: **One value is used, not split per format.** There used to be a separate
#: 550 for RAW-only batches, but a single JPEG mixed into the batch tips it
#: over to the worst-case value, and the value itself was far off the
#: measurements (measured JPEG 402~546MB on Mac and 134~196MB on Windows,
#: against a setting of 1,300). The loss from being off was larger than
#: what splitting was worth - a JPEG batch on a 16GB Mac was held to 2
#: workers.
#:
#: 800 is **not the measured worst case but a deliberately lowered value**.
#: The measured worst is 1,109~1,144MB for HEIF 25.6MP (Mac), so dividing
#: by 800 starts more workers on a HEIF batch than the budget allows. On a
#: 16GB Mac that is 7 instead of 5.
#:
#: The cost of that was measured (60 HIF frames each, non-overlapping
#: regions):
#:
#:   2 workers   2.83 frames/s   2,287MB   swap +0
#:   5 workers   3.18 frames/s   5,543MB   swap +0     <- optimum
#:   7 workers   2.56 frames/s   7,224MB   swap +0     <- what this picks
#:   9 workers   2.46 frames/s   8,012MB   swap +664MB
#:
#: That is -19.5% on HEIF. 7 workers do not reach swap, so the cause of
#: the drop is contention, not swapping. In exchange there is no loss on
#: RAW and JPEG (the measured range is flat) and machines with more RAM
#: get more workers. **We chose to accept a loss inside 20% on the single
#: HEIF format and give the rest plenty.**
#:
#: If a report comes in that HEIF batches are slow, start here - raise it
#: to 1100 and that format goes back to its optimum.
WORKER_MEMORY_MB = 800

#: Past this point it actually gets slower.
#:
#: Measured (300 frames, 32 cores): 6 workers 3.40x / 8 workers 3.60x /
#: **12 workers 3.77x** / 16 workers 3.61x / 24 workers 3.44x. Even using
#: all 32 cores it stops at 3.8x because the disk reading 40MB RAWs is the
#: bottleneck. Add workers past that point and they only fight each other
#: for the disk, which is a loss.
#:
#: (With a small sample the worker start-up cost masks the curve and
#: distorts it. Measured with 48 frames it looked as if it saturated at 6.)
MAX_USEFUL_WORKERS = 12


def _available_memory_mb() -> int | None:
    """The usable physical memory (MB). None if it cannot be determined."""
    try:  # Linux
        return int(os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1024 / 1024)
    except (AttributeError, ValueError, OSError):
        pass

    if sys.platform == "darwin":
        # macOS has no SC_AVPHYS_PAGES at all - the name itself does not
        # exist, so it falls through with a ValueError, and the path above
        # only holds on Linux. That meant that on a Mac the RAM limit was
        # not applied at all and the worker count was decided on the core
        # count alone (9 workers on an 8GB MacBook Air = swapping).
        #
        # vm_stat's free, inactive and speculative are the parts that can
        # be reclaimed straight away. active and wired have to be left out
        # - that is memory in use right now.
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


def resolve_workers(requested: int | None,
                    paths: Iterable[Path] | None = None) -> int:
    """Decides the worker count.

    Raise it on the core count alone and a low-spec PC runs short of RAM
    and starts swapping. Once swapping starts, using more cores actually
    makes it slower. So it is capped by three things: the core count (one
    core is the UI/OS's share), the usable RAM, and the point at which the
    measurements show the gain disappearing.

    It is not split per format - see the WORKER_MEMORY_MB comment. paths is
    left in for caller compatibility and is not used at present.
    """
    if requested and requested > 0:
        return requested

    workers = max(1, (os.cpu_count() or 2) - 1)
    workers = min(workers, MAX_USEFUL_WORKERS)

    available = _available_memory_mb()
    if available:
        # The available amount is divided as it is. It used to be
        # multiplied by 0.5 to use only half, but this value is already
        # the "reclaimable straight away" share (free+inactive+speculative
        # on Mac, ullAvailPhys on Windows), so the memory the UI uses is
        # excluded from the start. Take another half off and it is cut
        # twice over - that was what held workers to 2 on a 16GB Mac.
        by_memory = int(available // WORKER_MEMORY_MB)
        workers = max(1, min(workers, by_memory))
    return workers


SECONDS_PER_PHOTO_PER_WORKER = 0.45
"""The time one worker takes to process one photo (seconds).

Measured: 720 frames on 12 workers in 26.9 seconds -> 0.037 seconds per
frame, 0.45 seconds per worker. The value covers preview extraction, face
detection, sharpness measurement and writing the thumbnail. It varies with
the body and the disk, so it is used only as a rough figure.
"""

SECONDS_PER_PHOTO_PER_WORKER_DEMOSAIC = 0.96
"""The same value when analysing by demosaic (seconds).

Measured (DC-S5M2X RW2, 12 workers, spawn overhead subtracted out):
embedded preview 15.9ms/frame against half demosaic 79.9ms/frame -> 0.96
seconds per worker. The sample is 4 frames, so it is a rough figure.
"""

PROCESS_POOL_STARTUP_SECONDS = 2.0
"""The time it takes the process pool to come up. It uses spawn, so it
cannot be ignored."""


def estimate_analysis_seconds(count: int, workers: int | None = None,
                              demosaic_count: int = 0) -> float:
    """A rough figure for the time (seconds) to analyse count photos.

    Saying only "clear the cache and it will be rebuilt" leaves the user
    not knowing whether that is 10 seconds or 10 minutes. It does not have
    to be accurate, only good enough to decide on.

    demosaic_count is how many of those will be analysed by demosaic (RAWs
    with a small preview).
    """
    if count <= 0:
        return 0.0
    workers = workers or resolve_workers(None)
    heavy = max(0, min(demosaic_count, count))
    light = count - heavy
    return (PROCESS_POOL_STARTUP_SECONDS
            + (light * SECONDS_PER_PHOTO_PER_WORKER
               + heavy * SECONDS_PER_PHOTO_PER_WORKER_DEMOSAIC) / max(1, workers))


# Turning an elapsed time into wording a person reads lives in gui.i18n.
# Build it here and no translation can be attached, so Korean gets mixed
# into the English UI (which is what actually happened).


# ---------------------------------------------------------------- batch run


def analyze_paths(
    paths: Sequence[Path],
    config: Config | None = None,
    cache_path: Path | None = None,
    use_cache: bool = True,
    progress_cb: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> list[ImageRecord]:
    """Analyses the given list of RAWs. Returned in the input order."""
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
        except Exception as exc:  # noqa: BLE001 - cache must not block this
            log.warning("캐시 사용 불가, 전체 재분석: %s", exc)
            cache = None

    pending = [p for p in paths if p not in results]
    failed = sum(1 for r in results.values() if r.error)
    done = len(results)

    if progress_cb:
        progress_cb(Progress(done, total, cached_count, failed, 0.0))

    if pending:
        workers = resolve_workers(config.workers, pending)
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
                    except Exception as exc:  # noqa: BLE001 - the worker died
                        record = ImageRecord(path=path, error=f"워커 오류: {exc}")

                    results[path] = record
                    fresh.append(record)
                    done += 1
                    if record.error:
                        failed += 1
                        # Failures must be recorded here. Workers start by
                        # spawn and do not inherit the parent's logging
                        # setup (0 handlers), so a log.warning called
                        # inside a worker never reaches the log file. In a
                        # window-only .app it disappears altogether.
                        log.warning("분석 실패 %s: %s", path.name, record.error)

                    if progress_cb:
                        progress_cb(
                            Progress(
                                done, total, cached_count, failed,
                                time.perf_counter() - started, path,
                            )
                        )

                    # Flushed periodically so that everything up to here
                    # is saved even if it is cut off part way
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
    """Scans a folder and analyses it. The cache is placed next to the
    folder."""
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
