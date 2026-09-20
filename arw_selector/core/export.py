"""Sorting into folders by grade.

The default is to copy. Moving 4000 frames is hard to undo, and a user
trying automatic scoring for the first time must never be put in a position
to lose their originals. Moving has to be chosen explicitly.

Every operation is recorded in a JSON log, and that log alone is enough to
undo it completely.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from concurrent.futures import BrokenExecutor
from typing import Callable, Iterable

from .raw_io import is_raw
from .types import Grade, ImageRecord

log = logging.getLogger(__name__)

from .appinfo import CACHE_DIR_NAME as LOG_DIR_NAME  # the undo log lives in the same folder

LOG_VERSION = 1

COMPANION_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    # Shooting RAW+HEIF also produces a .HIF (Sony). Leave it out and a
    # move export takes only the RAW, orphaning the HIF in the source folder.
    ".hif",
    ".heif",
    ".heic",
    ".xmp",
)
"""File extensions that have to move together with the RAW.

If you shot RAW+JPEG, or Lightroom left a sidecar behind, moving only the
RAW breaks the pair.
"""


@dataclass(frozen=True)
class ExportOp:
    """One operation on a single file."""

    source: Path
    destination: Path
    grade: Grade
    develop: object | None = None
    """DevelopSettings. If present, a developed image is made as well."""

    rendered_name: str | None = None
    """Filename of the develop result. None just swaps destination's
    extension."""

    main_face_box: tuple[float, float, float, float] | None = None
    """Normalised coordinates of the main subject face the analysis picked
    (or the user changed).

    The face mask's 'main subject' follows this face. Without it the mask
    picks one again by itself at save time, which may not be the face you
    saw on screen.
    """


@dataclass
class ExportPlan:
    """What actually goes where. You can check it first with a dry run."""

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
    """How many frames were developed to JPEG with adjustments applied."""
    failed: list[tuple[Path, str]] = field(default_factory=list)
    log_path: Path | None = None
    mode: str = "copy"
    cancelled: bool = False


def find_companions(raw_path: Path) -> list[Path]:
    """Finds the files paired with a RAW.

    Compared case-insensitively - opening a folder made on Windows from
    macOS would otherwise treat DSC001.JPG and DSC001.jpg as different.
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
        # the DSC001.ARW.xmp form (the sidecar Lightroom makes)
        if name_lower == f"{raw_name_lower}.xmp":
            companions.append(sibling)
        # the DSC001.jpg / DSC001.xmp form
        elif (
            sibling.stem.lower() == stem_lower
            and sibling.suffix.lower() in COMPANION_EXTENSIONS
        ):
            companions.append(sibling)

    return sorted(companions)


def _unique_destination(destination: Path) -> Path:
    """On a name clash, a suffix is appended instead of overwriting.

    It prevents the accident where identically named files from different
    cards erase each other.
    """
    if not destination.exists():
        return destination
    for index in range(1, 10000):
        candidate = destination.with_name(f"{destination.stem}_{index}{destination.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"이름 충돌을 해소하지 못했다: {destination}")


NO_PLACE_FOLDER = "_위치없음"
"""The folder frames with no GPS go to.

They must not be mixed into some arbitrary place. Not knowing the location
and having been shot at that location are different things. Measured (300
A6700 frames): shooting without the phone linked puts GPS into **not one
frame**, so it is common for this folder to be all of it.
"""


# ------------------------------------------------------------ parallel rendering

RENDER_MB_PER_MP = 80.0
"""Peak memory of developing one photo to a file, per megapixel, with no
noise reduction and no lens profile. Measured on an A1 50MP ARW: 3.9GB."""
RENDER_MB_PER_MP_NOISE = 52.0
"""On top of that when noise reduction is on: measured 6.5GB at 50MP."""
RENDER_MB_PER_MP_LENS = 112.0
"""On top of that when a lens profile is on: measured 9.5GB at 50MP - the
lensfun coordinate maps and the per-channel resampling at full size."""
RENDER_MB_FLOOR = 300.0
RENDER_MEGAPIXELS_ASSUMED = 50.0
"""The export plan does not know the sensor size, and guessing low would
mean swapping, so the budget assumes a 50MP body. A 24MP body could take
twice the workers; the dialog lets the user say so."""
MAX_RENDER_WORKERS = 4
"""Developing is memory-bound long before it is core-bound."""


def _will_render(op: "ExportOp", options: "ExportOptions", apply_develop: bool) -> bool:
    """Whether this operation writes a developed image (the same test the
    loop makes: a companion never, an original only when developing is on
    and either it has edits or the original itself is not going out)."""
    return (apply_develop and op.rendered_name is not None
            and (op.develop is not None or not options.copy_raw))


def render_memory_mb(ops: list["ExportOp"], megapixels: float = RENDER_MEGAPIXELS_ASSUMED) -> float:
    """Peak memory one render worker needs for the heaviest of these ops."""
    heaviest = RENDER_MB_PER_MP
    for op in ops:
        develop = op.develop
        if develop is None:
            continue
        per_mp = RENDER_MB_PER_MP
        detail = getattr(develop, "detail", None)
        if detail is not None and (getattr(detail, "noise_reduction", 0)
                                   or getattr(detail, "color_noise_reduction", 0)):
            per_mp += RENDER_MB_PER_MP_NOISE
        optics = getattr(develop, "optics", None)
        if optics is not None and getattr(optics, "auto_enabled", False):
            per_mp += RENDER_MB_PER_MP_LENS
        heaviest = max(heaviest, per_mp)
    return RENDER_MB_FLOOR + heaviest * megapixels


def resolve_render_workers(options: "ExportOptions", plan: ExportPlan,
                           apply_develop: bool = True) -> int:
    """How many photos to develop at once.

    Exporting two hundred keepers with noise reduction and a lens profile
    took 53 minutes one photo at a time (16s each on a 50MP body). The
    render of one photo is independent of the next, so they can overlap -
    but each one holds 4~9GB at its peak (render_memory_mb), and a laptop
    that runs two of those at once swaps and ends up slower than one. So
    the count comes from free memory first, then the cores, capped at
    MAX_RENDER_WORKERS, and the user can pin it in the export dialog.
    """
    renders = [op for op in plan.operations if _will_render(op, options, apply_develop)]
    if len(renders) < 2:
        return 1
    requested = int(getattr(options, "render_workers", 0) or 0)
    if requested > 0:
        return max(1, min(requested, len(renders), MAX_RENDER_WORKERS))
    from .pipeline import _available_memory_mb

    workers = max(1, min(MAX_RENDER_WORKERS, (os.cpu_count() or 2) - 1, len(renders)))
    available = _available_memory_mb()
    if available:
        workers = max(1, min(workers, int(available // render_memory_mb(renders))))
    return workers


def _rendered_path(op: "ExportOp", options: "ExportOptions") -> Path:
    name = op.rendered_name or op.destination.with_suffix(options.image_format.suffix).name
    return op.destination.with_name(name)


def _reserve_destination(destination: Path, taken: set[Path]) -> Path:
    """_unique_destination for names handed out before the files exist:
    two workers must not both settle on DSC001.jpg because neither had
    written it yet when they looked."""
    candidate = destination
    index = 0
    while candidate.exists() or candidate in taken:
        index += 1
        if index >= 10000:
            raise RuntimeError(f"이름 충돌을 해소하지 못했다: {destination}")
        candidate = destination.with_name(f"{destination.stem}_{index}{destination.suffix}")
    taken.add(candidate)
    return candidate


def _init_render_worker(threads: int) -> None:
    """Quiet logging (as analysis workers do) and a share of the cores.

    One render already spreads OpenCV's resizes and filters over every
    core. Two workers each doing that oversubscribe the machine and gain
    nothing (measured: 4 x 50MP, 18.3s alone vs 19.8s as two), so each
    worker gets cores / workers.
    """
    from .pipeline import _init_worker

    _init_worker()
    try:
        import cv2

        cv2.setNumThreads(max(1, threads))
    except Exception:  # noqa: BLE001 - a thread cap is a nicety
        pass


def _render_task(source: Path, destination: Path, settings, grade_value: str,
                 quality: int, long_edge: int | None, main_face_box,
                 bit_depth: int, color_space: str) -> dict[str, str]:
    """One render in a worker process. Returns the undo log entry."""
    from .develop.engine import export_image

    destination.parent.mkdir(parents=True, exist_ok=True)
    export_image(
        source, destination, settings, quality=quality, long_edge=long_edge,
        main_face_box=main_face_box, bit_depth=bit_depth, color_space=color_space,
    )
    return {"source": str(source), "destination": str(destination),
            "grade": grade_value, "rendered": True}


def _drain_renders(pool, pending: dict, sources: dict, completed: list,
                   result: ExportResult) -> None:
    """After a cancel or a failure: nothing new starts, the renders already
    running finish (their files exist, so the undo log must know them), and
    the pool is joined."""
    pool.shutdown(wait=False, cancel_futures=True)
    for position, future in pending.items():
        if future.cancelled():
            continue
        try:
            completed.append(future.result())
            result.rendered += 1
        except Exception as exc:  # noqa: BLE001 - reported like any other failed op
            result.failed.append((sources[position], str(exc)))
    pool.shutdown(wait=True)


def _place_folder_names(records: list[ImageRecord]) -> dict[int, str]:
    """place_id -> folder name. If they are not grouped yet, they are
    grouped here.

    Right after analysis place_id is filled in, but there are paths such as
    the queue that build records separately, so it is checked once more
    here.
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
    """Works out which file goes where. Does not touch the filesystem."""
    from .export_options import ExportOptions, format_filename

    options = options or ExportOptions()
    plan = ExportPlan()
    destination_root = Path(destination_root)

    # Only the selected grades are exported. The filtering has to happen
    # before enumerate so the {index} number runs on from 1 without skips.
    records = [r for r in records if options.wants_grade(r.final_grade)]

    place_names = _place_folder_names(records) if options.subfolder_by_place else {}

    for index, record in enumerate(records, start=1):
        grade = record.final_grade
        target_dir = destination_root
        # Place first, grade inside it. The other way round, the keep and
        # review of the same place sit far apart and you cannot see "the
        # result for this place" at a glance.
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
            develop = None  # nothing but defaults, so no reason to develop

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
    """Copies (the default) or moves into folders by grade.

    Frames with an adjustment assigned get a developed image made
    alongside. By default the original RAW goes out as it is too, so there
    is room to develop it again later.

    The undo log is written before the work starts - even if it dies
    partway, what it has done up to then has to be undoable.
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

    # Not exporting the original and not making an adjusted copy either
    # means there is no output at all. And yet it ends as "done", so the
    # user believes it was exported. Instead of succeeding quietly, we say
    # why nothing comes out and stop. (Move mode moves the original, so
    # there is output.)
    if not options.copy_raw and not apply_develop and not move:
        message = "원본 복사와 보정 적용이 모두 꺼져 있어 내보낼 것이 없습니다"
        log.error(message)
        result.failed.append((destination_root, message))
        return result

    log_path = _new_log_path(destination_root)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # If the export location itself cannot be created, do not start.
        # With no log there is no undo either, so going on is dangerous.
        log.error("내보낼 위치를 만들 수 없습니다 (%s): %s", destination_root, exc)
        result.failed.append((destination_root, f"폴더를 만들 수 없습니다: {exc}"))
        return result

    completed: list[dict[str, str]] = []
    total = len(plan.operations)

    # Several photos develop at once when memory allows - see
    # resolve_render_workers. Every render is handed out up front with its
    # final name settled here, and the loop below waits for each one when
    # its turn comes, so progress, cancel and the move-after-render rule
    # all keep the old order.
    workers = resolve_render_workers(options, plan, apply_develop)
    pool = None
    broken_pool = None
    pending: dict[int, object] = {}
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor

        from .develop.settings import DevelopSettings

        log.info("현상 렌더 %d개 동시 진행", workers)
        threads = max(1, (os.cpu_count() or 2) // workers)
        pool = ProcessPoolExecutor(max_workers=workers, initializer=_init_render_worker,
                                   initargs=(threads,))
        taken: set[Path] = set()
        for position, op in enumerate(plan.operations):
            if not _will_render(op, options, apply_develop):
                continue
            target = _reserve_destination(_rendered_path(op, options), taken)
            pending[position] = pool.submit(
                _render_task, op.source, target, op.develop or DevelopSettings(),
                op.grade.value, options.quality, options.target_long_edge(),
                op.main_face_box, options.bit_depth, options.color_space.value,
            )
    sources = {position: op.source for position, op in enumerate(plan.operations)}

    try:
        for index, op in enumerate(plan.operations, start=1):
            if should_cancel and should_cancel():
                # what was done up to here is in the log, so it can be undone
                log.info("사용자 취소 — %d개 처리 후 중단", result.moved)
                result.cancelled = True
                break
            future = pending.pop(index - 1, None)
            try:
                op.destination.parent.mkdir(parents=True, exist_ok=True)

                # build_plan makes companion files (camera JPEG, .xmp) with
                # no rendered_name. They are **attached files**, not
                # photographs, so they must not go through the develop
                # engine (the JPEG would be lossily re-compressed and the
                # .xmp recorded as a failure), and they have nothing to do
                # with the copy_raw option - that option decides whether to
                # keep the original RAW, it is not a switch that undoes the
                # "include companions" the user explicitly turned on.
                # Companion files are always copied/moved as they are.
                is_companion = op.rendered_name is None

                # Develop first. In move mode, trying to read after the
                # original has been moved finds the source already gone.
                # If the original is not copied, the only output for this
                # photo is the rendered version. Skipping it because there
                # is no adjustment would make that one photo vanish
                # quietly, so in that case it is exported without fail,
                # even if only with a neutral adjustment.
                rendered = apply_develop and not is_companion and (
                    op.develop is not None or not options.copy_raw
                )
                if rendered:
                    if future is not None:
                        try:
                            entry = future.result()
                        except BrokenExecutor as exc:
                            # A worker was killed (the OS reclaiming memory,
                            # usually). The pool is done for; this photo and
                            # the rest are developed here, one at a time,
                            # rather than reported as failed.
                            if pool is not None:
                                log.warning("현상 워커가 죽어 한 장씩 이어간다: %s", exc)
                                broken_pool, pool = pool, None
                            entry = _render_operation(op, options)
                    else:
                        entry = _render_operation(op, options)
                    completed.append(entry)
                    result.rendered += 1

                # The original RAW is not exported if the option is off.
                # But skipping it in move mode as well would leave the
                # original in place and stop it being a "move", so on a
                # move it is always moved.
                #
                # If the original is not a RAW, copy_raw has nothing to
                # protect. The reason for keeping the RAW is the room to
                # develop it again later, but a JPEG original has the same
                # format and the same name as the developed version, so
                # IMG_0001_1.jpg appears next to IMG_0001.jpg and there is
                # no telling which one is the adjusted copy. The original
                # stays in its own folder as it is.
                skip_original = not is_companion and (
                    not options.copy_raw or (rendered and not is_raw(op.source))
                )
                if skip_original and not move:
                    if progress_cb:
                        progress_cb(index, total)
                    continue

                completed.append(_transfer_operation(op, move=move))
                result.moved += 1
            except Exception as exc:  # noqa: BLE001
                # One file failing does not stop the rest. Not just
                # OSError but demosaic/preview failures (PreviewError) and
                # adjustment errors (cv2.error) have to be swallowed here,
                # or one damaged file stops the whole batch.
                # A render a worker is doing for this op ends up on disk
                # whatever happened here, so the undo log must know it: wait
                # for it (one render at most) and log it, unless it failed too.
                if future is not None and not future.cancelled():
                    try:
                        completed.append(future.result())
                    except Exception:  # noqa: BLE001 - the render itself failed: nothing to log
                        pass
                log.warning("%s 실패: %s", op.source.name, exc)
                result.failed.append((op.source, str(exc)))

            if progress_cb:
                progress_cb(index, total)
    finally:
        if pool is not None:
            _drain_renders(pool, pending, sources, completed, result)
        elif broken_pool is not None:
            broken_pool.shutdown(wait=False, cancel_futures=True)
        # The files are already in place by now. A log that cannot be
        # written (the destination filled up on the last frame) costs the
        # undo, not the export - raising here reported a finished export
        # as failed and invited a second, duplicating run.
        try:
            _write_log(log_path, mode, destination_root, completed)
            result.log_path = log_path
        except OSError as exc:
            log.warning("내보내기 기록을 쓸 수 없어 되돌리기는 안 된다 (%s): %s", log_path, exc)
            result.log_path = None

    log.info(
        "%s 완료: %d개 (현상 %d, 실패 %d) -> %s",
        mode, result.moved, result.rendered, len(result.failed), destination_root,
    )
    return result


def _render_operation(op: "ExportOperation", options: "ExportOptions") -> dict[str, str]:
    """Exports the image with the adjustment applied and makes the undo log
    entry."""
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
        bit_depth=options.bit_depth,
        color_space=options.color_space.value,
    )
    return {
        "source": str(op.source),
        "destination": str(rendered),
        "grade": op.grade.value,
        "rendered": True,
    }


def _transfer_operation(op: "ExportOperation", *, move: bool) -> dict[str, str]:
    """Copies or moves the original file and makes the undo log entry."""
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


# --------------------------------------------------------------------- undo


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
    """Returns them sorted so that the newest log comes first.

    Records exported before the product name changed have to be found too.
    If they are not, the way to undo the 4000 frames exported back then
    disappears.
    """
    from .cache import resolve_cache_dir

    log_dir = resolve_cache_dir(destination_root)
    if not log_dir.exists():
        return []
    return sorted(log_dir.glob("export_*.json"), reverse=True)


def undo_export(log_path: Path) -> ExportResult:
    """Reads the log and undoes the export.

    If it was a copy, the copies it created are deleted; if it was a move,
    they are put back where they were. Since a new file was always created
    on a name clash, everything being deleted is something this tool made.
    Files the user already had are not touched.

    Even when the log is a little out of step it **undoes as much as it
    can.** In move mode this log is the only safety net there is - throwing
    an exception over the whole thing because one entry is broken would
    leave a user who moved 4000 frames with no means of recovery at all.
    Entries it could not undo go into failed so the user can deal with them
    by hand.
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
        # Do not guess on a mode we do not know. Treating it as copy and
        # going on deletes the destination file, and if it really was a
        # move that is the **only** copy the user has. Deleting costs far
        # more than failing to undo.
        log.error("되돌리기 모드를 알 수 없습니다 (%s): %r", log_path.name, mode)
        result.failed.append((log_path, f"되돌리기 모드를 알 수 없습니다: {mode!r}"))
        return result

    operations = payload.get("operations")
    if not isinstance(operations, (list, tuple)):
        operations = ()

    # undoing in reverse order avoids colliding with state made along the way
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

            # A file newly created by developing was not moved in from
            # somewhere - it **did not exist** before. Undoing it means
            # deleting it. Going by the batch-wide mode alone and saying
            # "it was a move, so put it back" meant the original had
            # already been restored at the source position (transfer
            # entries are undone first), so they all failed with "another
            # file is in the original position" and only the developed
            # copies were left in the destination folder.
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
    """Clears away _keep/_review/_reject folders that have become empty."""
    for grade in Grade:
        directory = destination_root / f"_{grade.value}"
        try:
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        except OSError:
            pass
