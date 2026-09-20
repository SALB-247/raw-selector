"""What the user decided about each photo, saved next to the cache.

Manual grades, develop edits and hand-picked main subjects used to live in
memory only: cull two thousand frames, close the app, and every grade was
gone the next morning. The analysis cache could not hold them - it is
keyed on the analysis options and thrown away when the algorithm changes,
and a decision is not a measurement.

They go into edits.json in the cache directory, keyed like the cache
(path relative to the folder, so the file follows the folder and the
card). When that directory cannot be written - a locked card - the
per-user cache directory takes it, and reading looks in both and takes
the newer. Clearing the cache leaves the file alone: it is the user's
work, not a re-computable result.

A save merges. The list in hand is rarely the whole folder - a few files
opened directly, a run cancelled at 300 of 4000, a re-analysis with
subfolders off, a grade given while the run is still going - and writing
the file from that list alone threw away every decision about the photos
that were not in it. The file is read first, the photos in hand replace
their own entries, and the rest stays (see merge).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .cache import _fallback_cache_dir, cache_dir_for, cache_root, relative_key
from .config import AnalyzeConfig
from .develop.settings import DevelopSettings
from .types import Grade, ImageRecord

log = logging.getLogger(__name__)

EDITS_FILE_NAME = "edits.json"
FORMAT_VERSION = 1

ProgressCallback = Callable[[int, int], None]
CancelCheck = Callable[[], bool]


def candidate_paths(folder: Path) -> list[Path]:
    """Where the edits of this folder may be: beside its cache first, then
    the per-user cache directory (a locked card's only writable place)."""
    folder = Path(folder)
    first = cache_dir_for(folder) / EDITS_FILE_NAME
    second = _fallback_cache_dir(folder) / EDITS_FILE_NAME
    return [first] if first == second else [first, second]


def edits_root(folder: Path) -> Path:
    """The folder the keys are relative to: the shoot folder itself, also
    when its cache had to go to the user folder."""
    return cache_root(cache_dir_for(Path(folder)))


def _read(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("edits"), dict):
        return None
    return payload


def load_edits(folder: Path) -> dict[str, dict]:
    """The saved decisions, keyed by relative path. Empty when none."""
    newest, newest_stamp = {}, ""
    for path in candidate_paths(folder):
        payload = _read(path)
        if payload is None:
            continue
        stamp = str(payload.get("saved", ""))
        if stamp >= newest_stamp:
            newest, newest_stamp = payload["edits"], stamp
    return newest


def _entry(record: ImageRecord) -> dict:
    """One record's decisions, as they go into the file. Empty when none."""
    entry: dict = {}
    if record.manual_grade is not None:
        entry["grade"] = record.manual_grade.value
    if record.develop is not None:
        entry["develop"] = record.develop.to_dict()
    if record.manual_main_face is not None:
        entry["main_face"] = int(record.manual_main_face)
    return entry


def collect(records: list[ImageRecord], root: Path) -> dict[str, dict]:
    """The decisions worth keeping, keyed for the file."""
    out: dict[str, dict] = {}
    for record in records:
        entry = _entry(record)
        if entry:
            out[relative_key(root, record.path)] = entry
    return out


def merge(base: dict[str, dict], records: list[ImageRecord], root: Path) -> dict[str, dict]:
    """The saved decisions with the records in hand written over them.

    A record replaces its own entry, so a grade cleared in the grid leaves
    the file. The one exception is the main-subject pick: a record without
    one keeps the pick on file. Nothing in the app clears a pick, so a
    record without one either never had it put back - a photo that has
    only just landed in a running analysis, a re-scoring that could not
    read the file, a run cancelled during the restore - or has faces this
    run did not find; dropping the pick for that would lose it for good.
    Entries of photos not in hand stay exactly as they are.
    """
    merged = dict(base)
    for record in records:
        key = relative_key(root, record.path)
        entry = _entry(record)
        previous = merged.get(key)
        if previous and "main_face" in previous and "main_face" not in entry:
            entry["main_face"] = previous["main_face"]
        if entry:
            merged[key] = entry
        else:
            merged.pop(key, None)
    return merged


def save_edits(folder: Path, records: list[ImageRecord]) -> Path | None:
    """Writes the decisions of these records into the folder's file, keeping
    what it says about every other photo. Returns where, or None when
    nowhere would take them. Written whole and swapped in, so a crash
    mid-write leaves the previous file, never half of one."""
    folder = Path(folder)
    payload = {
        "version": FORMAT_VERSION,
        "saved": datetime.now().isoformat(timespec="seconds"),
        "edits": merge(load_edits(folder), records, edits_root(folder)),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    for path in candidate_paths(folder):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            staging = path.with_suffix(".json.tmp")
            staging.write_text(text, encoding="utf-8")
            os.replace(staging, path)
            return path
        except OSError as exc:
            log.warning("판정·편집을 저장할 수 없다 (%s): %s", path, exc)
    return None


def apply_edits(records: list[ImageRecord], edits: dict[str, dict], root: Path,
                config: AnalyzeConfig | None = None, *,
                progress_cb: ProgressCallback | None = None,
                should_cancel: CancelCheck | None = None) -> tuple[int, int, int]:
    """Puts saved decisions back onto fresh records. Returns how many
    grades, develops and main-subject picks were restored.

    A decision already on the record wins over the file: a grade given
    while the analysis was still running is newer than anything saved
    before it started. Grades and develop edits are a dictionary lookup
    each; the main-subject picks are only put back when the analysis
    config is given, because each one is a preview read and a detector
    pass (apply_main_faces) - the caller re-grades the batch afterwards.
    A file that cannot be read keeps the automatic result.
    """
    grades = develops = 0
    for record in records:
        entry = edits.get(relative_key(root, record.path))
        if not entry:
            continue
        grade = entry.get("grade")
        if grade in {g.value for g in Grade} and record.manual_grade is None:
            record.manual_grade = Grade(grade)
            grades += 1
        develop = entry.get("develop")
        if isinstance(develop, dict) and record.develop is None:
            try:
                record.develop = DevelopSettings.from_dict(develop)
                develops += 1
            except Exception:  # noqa: BLE001 - one unreadable edit must not lose the rest
                log.warning("%s: 저장된 현상 설정을 읽을 수 없다", record.path.name, exc_info=True)
    faces = 0
    if config is not None:
        faces = apply_main_faces(records, edits, root, config,
                                 progress_cb=progress_cb, should_cancel=should_cancel)
    return grades, develops, faces


def apply_main_faces(records: list[ImageRecord], edits: dict[str, dict], root: Path,
                     config: AnalyzeConfig, *,
                     progress_cb: ProgressCallback | None = None,
                     should_cancel: CancelCheck | None = None) -> int:
    """Puts saved main-subject picks back. Returns how many.

    A pick changes the measurement, so the photo is scored again against
    that face (main_face.reanalyze_with_main_face): the preview is read
    and the detectors run, 0.5 to 1.5 s a photo, a minute for fifty. That
    is why this half of the restore is separate - it belongs on the
    analysis worker, not on the GUI thread at the moment "analysis
    complete" appears. progress_cb(done, total) is called before the
    first photo and after each; should_cancel is checked between photos,
    and a pick not reached stays on file (see merge). A pick beyond the
    faces found this run is left alone, and a record that already has a
    pick keeps it.
    """
    from .main_face import reanalyze_with_main_face

    todo: list[tuple[ImageRecord, int]] = []
    for record in records:
        if record.focus is None or record.manual_main_face is not None:
            continue
        entry = edits.get(relative_key(root, record.path))
        index = entry.get("main_face") if entry else None
        if (isinstance(index, int) and not isinstance(index, bool)
                and 0 <= index < len(record.focus.faces)):
            todo.append((record, index))
    if not todo:
        return 0
    total = len(todo)
    if progress_cb is not None:
        progress_cb(0, total)
    faces = 0
    for done, (record, index) in enumerate(todo, 1):
        if should_cancel is not None and should_cancel():
            break
        focus = reanalyze_with_main_face(record, config, index)
        if focus is not None:
            record.focus = focus
            record.manual_main_face = index
            faces += 1
        if progress_cb is not None:
            progress_cb(done, total)
    return faces


def restore(folder: Path, records: list[ImageRecord],
            config: AnalyzeConfig | None = None, *,
            progress_cb: ProgressCallback | None = None,
            should_cancel: CancelCheck | None = None) -> tuple[int, int, int]:
    """load_edits + apply_edits for a folder that was just analysed."""
    folder = Path(folder)
    edits = load_edits(folder)
    if not edits:
        return 0, 0, 0
    return apply_edits(records, edits, edits_root(folder), config,
                       progress_cb=progress_cb, should_cancel=should_cancel)


def restore_main_faces(folder: Path, records: list[ImageRecord], config: AnalyzeConfig, *,
                       progress_cb: ProgressCallback | None = None,
                       should_cancel: CancelCheck | None = None) -> int:
    """load_edits + apply_main_faces: the slow half of restore, for the
    analysis worker. Returns how many picks were put back."""
    folder = Path(folder)
    edits = load_edits(folder)
    if not edits:
        return 0
    return apply_main_faces(records, edits, edits_root(folder), config,
                            progress_cb=progress_cb, should_cancel=should_cancel)
