"""등급별 폴더 분류.

기본은 복삽니다. 4000장을 옮기는 건 되돌리기 어렵고, 자동 판정을 처음
써보는 사용자가 원본을 잃는 상황은 만들면 안 됩니다. 이동은 명시적으로
선택해야 합니다.

모든 작업은 JSON 로그로 남기고, 그 로그만으로 완전히 되돌릴 수 있습니다.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from .raw_io import is_raw
from .types import Grade, ImageRecord

log = logging.getLogger(__name__)

from .appinfo import CACHE_DIR_NAME as LOG_DIR_NAME  # 되돌리기 로그도 같은 폴더에 둡니다

LOG_VERSION = 1

COMPANION_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    # RAW+HEIF로 찍으면 .HIF가 함께 생깁니다(소니). 이걸 빠뜨리면 이동
    # 내보내기에서 RAW만 옮겨 가고 HIF가 원본 폴더에 고아로 남습니다.
    ".hif",
    ".heif",
    ".heic",
    ".xmp",
)
"""RAW와 함께 움직여야 하는 파일 확장자.

RAW+JPEG로 찍었거나 Lightroom이 사이드카를 만들어 둔 경우, RAW만 옮기면
짝이 끊어집니다.
"""


@dataclass(frozen=True)
class ExportOp:
    """파일 하나에 대한 작업."""

    source: Path
    destination: Path
    grade: Grade
    develop: object | None = None
    """DevelopSettings. 있으면 현상한 이미지를 추가로 만듭니다."""

    rendered_name: str | None = None
    """현상 결과 파일명. None이면 destination의 확장자만 바꿔 씁니다."""

    main_face_box: tuple[float, float, float, float] | None = None
    """분석이 고른(또는 사용자가 바꾼) 주 피사체 얼굴의 정규화 좌표.

    얼굴 마스크의 '주 피사체'가 이 얼굴을 따라갑니다. 안 넘기면 마스크가
    저장 시점에 스스로 다시 골라서, 화면에서 본 얼굴과 다를 수 있습니다.
    """


@dataclass
class ExportPlan:
    """실제로 무엇이 어디로 갈지. dry-run으로 먼저 확인할 수 있습니다."""

    operations: list[ExportOp] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        counts = {grade.value: 0 for grade in Grade}
        for op in self.operations:
            counts[op.grade.value] += 1
        return counts

    @property
    def develop_count(self) -> int:
        return sum(1 for op in self.operations if op.develop is not None)


@dataclass
class ExportResult:
    moved: int = 0
    rendered: int = 0
    """보정을 적용해 JPEG로 현상한 장수."""
    failed: list[tuple[Path, str]] = field(default_factory=list)
    log_path: Path | None = None
    mode: str = "copy"
    cancelled: bool = False


def find_companions(raw_path: Path) -> list[Path]:
    """RAW와 짝지어진 파일들을 찾습니다.

    대소문자를 구분하지 않고 비교합니다 — Windows에서 만든 폴더를 macOS에서
    열면 DSC001.JPG와 DSC001.jpg가 다르게 취급되기 때문입니다.
    """
    companions: list[Path] = []
    stem_lower = raw_path.stem.lower()
    raw_name_lower = raw_path.name.lower()

    try:
        siblings = list(raw_path.parent.iterdir())
    except OSError:
        return companions

    for sibling in siblings:
        if not sibling.is_file() or sibling == raw_path:
            continue
        name_lower = sibling.name.lower()
        # DSC001.ARW.xmp 형태 (Lightroom이 만드는 사이드카)
        if name_lower == f"{raw_name_lower}.xmp":
            companions.append(sibling)
        # DSC001.jpg / DSC001.xmp 형태
        elif (
            sibling.stem.lower() == stem_lower
            and sibling.suffix.lower() in COMPANION_EXTENSIONS
        ):
            companions.append(sibling)

    return sorted(companions)


def _unique_destination(destination: Path) -> Path:
    """이름이 겹치면 덮어쓰지 않고 접미사를 붙입니다.

    다른 카드에서 온 같은 파일명이 서로를 지우는 사고를 막습니다.
    """
    if not destination.exists():
        return destination
    for index in range(1, 10000):
        candidate = destination.with_name(f"{destination.stem}_{index}{destination.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"이름 충돌을 해소하지 못했다: {destination}")


NO_PLACE_FOLDER = "_위치없음"
"""GPS가 없는 컷이 갈 폴더.

임의의 장소에 섞으면 안 됩니다. 위치를 모르는 것과 그 장소에서 찍은 것은
다릅니다. 실측(A6700 300장): 폰 연동 없이 찍으면 GPS가 **한 장도** 안
들어가므로, 이 폴더가 전부가 되는 경우가 흔합니다.
"""


def _place_folder_names(records: list[ImageRecord]) -> dict[int, str]:
    """place_id → 폴더 이름. 아직 안 묶였으면 여기서 묶습니다.

    분석 직후에는 place_id가 채워져 있지만, 대기열처럼 레코드를 따로
    만들어 넣는 경로도 있어서 여기서 한 번 더 확인합니다.
    """
    from .places import assign_places, place_labels

    if any(getattr(r, "place_id", None) is not None for r in records):
        from .places import Place

        by_id: dict[int, list[ImageRecord]] = {}
        for record in records:
            place_id = getattr(record, "place_id", None)
            if place_id is not None:
                by_id.setdefault(place_id, []).append(record)
        places = []
        for place_id, members in sorted(by_id.items()):
            coords = [(m.metadata.latitude, m.metadata.longitude)
                      for m in members
                      if m.metadata is not None and m.metadata.has_location]
            if not coords:
                continue
            places.append(Place(
                index=place_id,
                latitude=sum(c[0] for c in coords) / len(coords),
                longitude=sum(c[1] for c in coords) / len(coords),
                records=members,
            ))
        return place_labels(places)

    return place_labels(assign_places(list(records)))


def build_plan(
    records: Iterable[ImageRecord],
    destination_root: Path,
    include_companions: bool = False,
    options: "ExportOptions | None" = None,
) -> ExportPlan:
    """어떤 파일이 어디로 갈지 계산합니다. 파일시스템은 건드리지 않습니다."""
    from .export_options import ExportOptions, format_filename

    options = options or ExportOptions()
    plan = ExportPlan()
    destination_root = Path(destination_root)

    # 선택한 등급만 내보냅니다. enumerate 앞에서 걸러야 {index} 번호가
    # 건너뛰지 않고 1부터 이어집니다.
    records = [r for r in records if options.wants_grade(r.final_grade)]

    place_names = _place_folder_names(records) if options.subfolder_by_place else {}

    for index, record in enumerate(records, start=1):
        grade = record.final_grade
        target_dir = destination_root
        # 장소를 먼저, 등급을 그 안에. 반대로 하면 같은 장소의 keep과
        # review가 멀리 떨어져 "이 장소 결과"를 한눈에 볼 수 없습니다.
        if options.subfolder_by_place:
            target_dir = target_dir / place_names.get(
                record.place_id, NO_PLACE_FOLDER)
        if options.subfolder_by_grade:
            target_dir = target_dir / f"_{grade.value}"

        if not record.path.exists():
            plan.skipped.append((record.path, "원본이 없음"))
            continue

        develop = getattr(record, "develop", None)
        if develop is not None and getattr(develop, "is_neutral", lambda: True)():
            develop = None  # 기본값뿐이면 현상할 이유가 없습니다

        raw_name = format_filename(
            options.filename_pattern, record, index, record.path.suffix
        )
        rendered_name = format_filename(
            options.filename_pattern, record, index, options.image_format.suffix
        )

        plan.operations.append(
            ExportOp(
                record.path,
                target_dir / raw_name,
                grade,
                develop,
                rendered_name=rendered_name,
                main_face_box=record.main_face_norm,
            )
        )

        if include_companions:
            for companion in find_companions(record.path):
                plan.operations.append(
                    ExportOp(companion, target_dir / companion.name, grade)
                )

    return plan


def export_records(
    records: Iterable[ImageRecord],
    destination_root: Path,
    move: bool = False,
    include_companions: bool = False,
    dry_run: bool = False,
    apply_develop: bool = True,
    options: "ExportOptions | None" = None,
    progress_cb: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> ExportResult:
    """등급별 폴더로 복사(기본) 또는 이동합니다.

    보정이 지정된 컷은 현상한 이미지를 함께 만듭니다. 기본적으로 원본 RAW도
    그대로 나가므로 나중에 다시 현상할 여지가 남습니다.

    되돌리기 로그를 먼저 쓰고 작업을 시작합니다 — 중간에 죽어도 그때까지
    한 일을 되돌릴 수 있어야 합니다.
    """
    from .export_options import ExportOptions

    options = options or ExportOptions(
        move=move, include_companions=include_companions, apply_develop=apply_develop
    )
    destination_root = Path(destination_root)
    plan = build_plan(records, destination_root, include_companions, options)
    mode = "move" if move else "copy"
    result = ExportResult(mode=mode)

    if dry_run:
        log.info("dry-run: %d개 파일, %s", len(plan.operations), plan.counts)
        return result

    if not plan.operations:
        return result

    # 원본도 안 내보내고 보정본도 안 만들면 결과물이 하나도 없습니다.
    # 그런데도 "완료"로 끝나서 사용자는 내보낸 줄 압니다. 조용히 성공하는
    # 대신 왜 아무것도 안 나오는지 알려 주고 멈춥니다. (이동 모드는
    # 원본을 옮기므로 결과물이 남습니다.)
    if not options.copy_raw and not apply_develop and not move:
        message = "원본 복사와 보정 적용이 모두 꺼져 있어 내보낼 것이 없습니다"
        log.error(message)
        result.failed.append((destination_root, message))
        return result

    log_path = _new_log_path(destination_root)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # 내보낼 위치 자체를 만들 수 없으면 시작하지 않습니다.
        # 로그를 못 남기면 되돌리기도 불가능하므로 진행이 위험합니다.
        log.error("내보낼 위치를 만들 수 없습니다 (%s): %s", destination_root, exc)
        result.failed.append((destination_root, f"폴더를 만들 수 없습니다: {exc}"))
        return result

    completed: list[dict[str, str]] = []
    total = len(plan.operations)

    try:
        for index, op in enumerate(plan.operations, start=1):
            if should_cancel and should_cancel():
                # 여기까지 한 일은 로그에 남으므로 그대로 되돌릴 수 있습니다
                log.info("사용자 취소 — %d개 처리 후 중단", result.moved)
                result.cancelled = True
                break
            try:
                op.destination.parent.mkdir(parents=True, exist_ok=True)

                # 현상을 먼저 합니다. 이동 모드에서 원본을 옮긴 뒤에 읽으려 하면
                # 소스가 이미 사라져 있습니다.
                # 원본을 복사하지 않는다면 이 사진의 결과물은 렌더링본뿐입니다.
                # 보정값이 없다고 건너뛰면 그 사진만 조용히 사라지므로,
                # 이때는 중립 보정으로라도 반드시 내보냅니다.
                rendered = apply_develop and (
                    op.develop is not None or not options.copy_raw
                )
                if rendered:
                    completed.append(_render_operation(op, options))
                    result.rendered += 1

                # 원본 RAW는 옵션을 껐으면 내보내지 않습니다. 다만 이동
                # 모드에서까지 건너뛰면 원본이 제자리에 남아 "이동"이
                # 아니게 되므로, 이동일 때는 항상 옮깁니다.
                #
                # 원본이 RAW가 아니면 copy_raw는 지킬 것이 없습니다. RAW를
                # 남기는 이유는 나중에 다시 현상할 여지인데, JPEG 원본은
                # 현상본과 같은 형식·같은 이름이라 IMG_0001.jpg 옆에
                # IMG_0001_1.jpg가 생기고 어느 쪽이 보정본인지 알 수 없게
                # 됩니다. 원본은 원래 폴더에 그대로 있습니다.
                skip_original = not options.copy_raw or (
                    rendered and not is_raw(op.source)
                )
                if skip_original and not move:
                    if progress_cb:
                        progress_cb(index, total)
                    continue

                completed.append(_transfer_operation(op, move=move))
                result.moved += 1
            except Exception as exc:  # noqa: BLE001
                # 한 파일이 실패해도 나머지는 계속 처리합니다. OSError뿐 아니라
                # 디모자이크/프리뷰 실패(PreviewError)나 보정 연산 오류(cv2.error)도
                # 여기서 삼켜야 손상 파일 한 장이 배치 전체를 멈추지 않습니다.
                log.warning("%s 실패: %s", op.source.name, exc)
                result.failed.append((op.source, str(exc)))

            if progress_cb:
                progress_cb(index, total)
    finally:
        _write_log(log_path, mode, destination_root, completed)
        result.log_path = log_path

    log.info(
        "%s 완료: %d개 (현상 %d, 실패 %d) -> %s",
        mode, result.moved, result.rendered, len(result.failed), destination_root,
    )
    return result


def _render_operation(op: "ExportOperation", options: "ExportOptions") -> dict[str, str]:
    """보정을 적용한 이미지를 내보내고 되돌리기 로그 항목을 만듭니다."""
    from .develop.engine import export_image
    from .develop.settings import DevelopSettings

    name = op.rendered_name or (
        op.destination.with_suffix(options.image_format.suffix).name
    )
    rendered = _unique_destination(op.destination.with_name(name))
    export_image(
        op.source,
        rendered,
        op.develop or DevelopSettings(),
        quality=options.quality,
        long_edge=options.target_long_edge(),
        main_face_box=op.main_face_box,
    )
    return {
        "source": str(op.source),
        "destination": str(rendered),
        "grade": op.grade.value,
        "rendered": True,
    }


def _transfer_operation(op: "ExportOperation", *, move: bool) -> dict[str, str]:
    """원본 파일을 복사하거나 이동하고 되돌리기 로그 항목을 만듭니다."""
    final_destination = _unique_destination(op.destination)
    if move:
        shutil.move(str(op.source), str(final_destination))
    else:
        shutil.copy2(str(op.source), str(final_destination))
    return {
        "source": str(op.source),
        "destination": str(final_destination),
        "grade": op.grade.value,
    }


# ---------------------------------------------------------------- 되돌리기


def _new_log_path(destination_root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return destination_root / LOG_DIR_NAME / f"export_{stamp}.json"


def _write_log(
    log_path: Path, mode: str, destination_root: Path, operations: list[dict[str, str]]
) -> None:
    payload = {
        "version": LOG_VERSION,
        "created": datetime.now().isoformat(),
        "mode": mode,
        "root": str(destination_root),
        "operations": operations,
    }
    log_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def find_logs(destination_root: Path) -> list[Path]:
    """최신 로그가 앞에 오도록 정렬해 반환합니다.

    제품명이 바뀌기 전에 내보낸 기록도 찾아야 합니다. 못 찾으면 그때
    내보낸 4000장을 되돌릴 방법이 사라집니다.
    """
    from .cache import resolve_cache_dir

    log_dir = resolve_cache_dir(destination_root)
    if not log_dir.exists():
        return []
    return sorted(log_dir.glob("export_*.json"), reverse=True)


def undo_export(log_path: Path) -> ExportResult:
    """로그를 읽어 내보내기를 되돌립니다.

    copy였으면 만들어낸 사본을 지우고, move였으면 원위치로 되돌립니다.
    이름 충돌 시 항상 새 파일을 만들었으므로, 지우는 대상은 전부 이 툴이
    만든 것입니다. 사용자가 원래 갖고 있던 파일은 건드리지 않습니다.

    로그가 조금 어긋나 있어도 **할 수 있는 만큼은 되돌립니다.** 이동 모드에서
    이 로그는 유일한 안전망입니다 — 항목 하나가 깨졌다고 통째로 예외를 내면
    4000장을 옮긴 사용자에게 복구 수단이 하나도 남지 않습니다. 되돌리지 못한
    항목은 failed에 담아 사용자가 직접 처리할 수 있게 합니다.
    """
    log_path = Path(log_path)
    payload = json.loads(log_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"되돌리기 로그 형식이 아닙니다: {log_path.name}")

    mode = payload.get("mode", "copy")
    result = ExportResult(
        mode=mode if mode in ("copy", "move") else "copy", log_path=log_path
    )

    if mode not in ("copy", "move"):
        # 모르는 모드에서 추측하면 안 됩니다. copy로 보고 진행하면 대상
        # 파일을 지우는데, 실제가 move였다면 그게 사용자가 가진 **유일한**
        # 사본입니다. 되돌리기를 못 하는 것보다 지우는 쪽이 훨씬 비쌉니다.
        log.error("되돌리기 모드를 알 수 없습니다 (%s): %r", log_path.name, mode)
        result.failed.append((log_path, f"되돌리기 모드를 알 수 없습니다: {mode!r}"))
        return result

    operations = payload.get("operations")
    if not isinstance(operations, (list, tuple)):
        operations = ()

    # 역순으로 되돌려야 중간에 만들어진 상태와 부딪히지 않습니다
    for operation in reversed(list(operations)):
        if (
            not isinstance(operation, dict)
            or not isinstance(operation.get("source"), str)
            or not isinstance(operation.get("destination"), str)
        ):
            result.failed.append((log_path, f"되돌릴 수 없는 항목: {operation!r}"))
            continue

        source = Path(operation["source"])
        destination = Path(operation["destination"])

        try:
            if not destination.exists():
                result.failed.append((destination, "대상이 이미 없음"))
                continue

            # 현상해서 새로 만든 파일은 옮겨 온 것이 아니라 **없던 것**입니다.
            # 되돌리기는 지우는 것이 맞습니다. 배치 전체의 mode만 보고
            # "이동이었으니 되돌려 놓자"고 하면, source 자리에는 이미 원본이
            # 복구돼 있어서(전송 항목을 먼저 되돌립니다) 전부 "원위치에 다른
            # 파일이 있음"으로 실패하고 현상본만 대상 폴더에 남았습니다.
            if mode == "move" and not operation.get("rendered"):
                if source.exists():
                    result.failed.append((source, "원위치에 다른 파일이 있음"))
                    continue
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(destination), str(source))
            else:
                destination.unlink()

            result.moved += 1
        except OSError as exc:
            result.failed.append((destination, str(exc)))

    root = payload.get("root")
    if isinstance(root, str):
        _cleanup_empty_dirs(Path(root))
    log.info("되돌리기 완료: %d개 (실패 %d)", result.moved, len(result.failed))
    return result


def _cleanup_empty_dirs(destination_root: Path) -> None:
    """비게 된 _keep/_review/_reject 폴더를 치웁니다."""
    for grade in Grade:
        directory = destination_root / f"_{grade.value}"
        try:
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        except OSError:
            pass
