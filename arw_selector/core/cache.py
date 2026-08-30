"""Cache of the analysis results.

Analysing 4000 frames takes minutes. Repeating that every time you adjust a
threshold or reopen the GUI makes the tool unusable. If the file has not
changed and the analysis parameters are the same, the stored result is used
as it is.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .raw_io import RawMetadata
from .types import FocusResult, FocusSource, ImageRecord

log = logging.getLogger(__name__)

from .appinfo import CACHE_DIR_NAME, LEGACY_CACHE_DIR_NAMES

CACHE_FILE_NAME = "analysis.sqlite"


def resolve_cache_dir(folder: Path) -> Path:
    """The folder's cache directory. One made under the old name keeps
    being used.

    This is so that a change of product name does not force a folder that
    has already been analysed to be analysed again. If the new name is not
    there and an old one is, that one is used as it is.
    """
    folder = Path(folder)
    current = folder / CACHE_DIR_NAME
    if current.exists():
        return current
    for legacy_name in LEGACY_CACHE_DIR_NAMES:
        legacy = folder / legacy_name
        if legacy.exists():
            return legacy
    return current

SCHEMA_VERSION = 9
"""Bumped whenever the schema or the payload layout changes. The existing
cache is then thrown away.

v2: dhash added for grouping. Old caches have no dhash, so grouping quietly
degrades to the visual signal alone - we make them analyse again.

v3: the full set of face boxes (faces) and the main subject index
(main_face) added. Without them not one face can be drawn on screen, and
the main subject pick stays the old (area-based) result. Re-analysis is
needed to have it picked on focus instead.

v4: the reference size for the roi and faces coordinates
(source_width/height), plus a change to the face detection threshold.
Without the reference size the screen side guesses "embedded preview width
= sensor width", and on a body like the Panasonic S1R that puts only a
1920px preview into 47 megapixels the boxes were 4.37x off. The threshold
was raised too, to filter out false detections (cat ears on a hat and the
like).

v5: the criterion for picking the main subject face was replaced. Comparing
sharpness divided by patch variance became gradient energy with no
normalisation, and patches with almost no contrast were dropped from the
candidates. **main_face is a value stored in the cache, so without a
version bump the old result keeps showing** - the screen really did stay
the same after the fix and it took a long time to work out why.

v6: eye open/closed for the main subject (eyes_open) added. Without it it
stays -1 (could not measure) and the closed-eye penalty never applies at
all. It is the kind of value that goes into the cache, so it has to be
re-analysed.

v7: the face the camera's AF pointed at (af_face) added - an AF <-> main
subject mismatch is used as an "uncertain" confidence signal. Without it it
stays -1 and the signal never appears. The score does not change, but it is
a value that goes into the cache, so re-analyse.

v8: the AF position is now read from JPEGs the camera produced itself
(Canon, Nikon). Up to v7 JPEGs were frozen at af_face=-1, so without a bump
JPEGs already analysed would never show the signal. Handling of multi-point
AFInfo2 on older DSLRs went in at the same time, so if there were
multi-point CR3 frames the boxes change.

v9: the 35mm-equivalent focal length (focal_length_35mm) and the AF area
mode (af_area_mode) added to the metadata - for display in the details
panel. Without a bump the old cache quietly comes back with None and just
those two lines stay empty in the panel forever.
"""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS analysis (
    path       TEXT PRIMARY KEY,
    mtime      REAL    NOT NULL,
    size       INTEGER NOT NULL,
    params_key TEXT    NOT NULL,
    payload    TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def default_cache_path(folder: Path) -> Path:
    """The cache sits next to the shooting folder. Move the whole folder and
    it comes along."""
    return resolve_cache_dir(folder) / CACHE_FILE_NAME


@dataclass(frozen=True)
class CacheStats:
    """How much room the cache is taking up right now."""

    exists: bool = False
    analysis_entries: int = 0
    thumbnail_count: int = 0
    analysis_bytes: int = 0
    thumbnail_bytes: int = 0
    log_count: int = 0

    @property
    def total_bytes(self) -> int:
        return self.analysis_bytes + self.thumbnail_bytes

    # The display unit is MiB throughout. If the parts and the total use
    # different units, the numbers do not add up as the user sees them.
    @staticmethod
    def _mb(value: int) -> float:
        return value / (1024 * 1024)

    @property
    def analysis_mb(self) -> float:
        return self._mb(self.analysis_bytes)

    @property
    def thumbnail_mb(self) -> float:
        return self._mb(self.thumbnail_bytes)

    @property
    def total_mb(self) -> float:
        return self._mb(self.total_bytes)

    def summary(self) -> str:
        if not self.exists:
            return "캐시 없음"
        return (
            f"분석 {self.analysis_entries}건 · 썸네일 {self.thumbnail_count}개 · "
            f"{self.total_mb:.1f}MB"
        )


def cache_stats(folder: Path) -> CacheStats:
    """Inspects the folder's cache state. Empty values if it is missing or
    unreadable."""
    cache_dir = resolve_cache_dir(folder)
    if not cache_dir.exists():
        return CacheStats()

    db_path = cache_dir / CACHE_FILE_NAME
    analysis_bytes = 0
    entries = 0

    if db_path.exists():
        # the WAL/SHM files count towards the cache size too
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(db_path) + suffix)
            if candidate.exists():
                try:
                    analysis_bytes += candidate.stat().st_size
                except OSError:
                    pass
        try:
            with closing(sqlite3.connect(db_path)) as conn:
                entries = conn.execute("SELECT COUNT(*) FROM analysis").fetchone()[0]
        except sqlite3.Error:
            entries = 0  # damaged cache - we cannot count it but we can delete it

    thumb_dir = cache_dir / "thumbs"
    thumbnail_count = 0
    thumbnail_bytes = 0
    if thumb_dir.exists():
        for path in thumb_dir.glob("*.jpg"):
            thumbnail_count += 1
            try:
                thumbnail_bytes += path.stat().st_size
            except OSError:
                pass

    return CacheStats(
        exists=True,
        analysis_entries=entries,
        thumbnail_count=thumbnail_count,
        analysis_bytes=analysis_bytes,
        thumbnail_bytes=thumbnail_bytes,
        log_count=len(list(cache_dir.glob("export_*.json"))),
    )


def clear_cache(folder: Path, keep_logs: bool = True) -> CacheStats:
    """Clears the cache. Returns the state as it was just before clearing.

    Export logs are kept by default - if they go, undo becomes impossible,
    and a user does not expect 'clear cache' to take undo away.
    """
    stats = cache_stats(folder)
    cache_dir = resolve_cache_dir(folder)
    if not cache_dir.exists():
        return stats

    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(cache_dir / CACHE_FILE_NAME) + suffix)
        try:
            candidate.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("캐시 파일 삭제 실패 %s: %s", candidate.name, exc)

    thumb_dir = cache_dir / "thumbs"
    if thumb_dir.exists():
        for path in thumb_dir.glob("*.jpg"):
            try:
                path.unlink()
            except OSError:
                pass
        try:
            thumb_dir.rmdir()
        except OSError:
            pass

    if not keep_logs:
        for path in cache_dir.glob("export_*.json"):
            try:
                path.unlink()
            except OSError:
                pass

    # if the inside is empty, clear away the folder itself too
    try:
        if not any(cache_dir.iterdir()):
            cache_dir.rmdir()
    except OSError:
        pass

    return stats


# ------------------------------------------------------------ serialisation


def _serialize(record: ImageRecord) -> str:
    """Serialises only the part that goes into the cache.

    group_id / grade / score are values that can only be settled by looking
    at the whole batch, so they are not cached. Only focus and metadata,
    which are decided from the one file alone, are stored.
    """
    metadata = None
    if record.metadata:
        metadata = asdict(record.metadata)
        metadata.pop("path", None)  # the key is the path
        if record.metadata.capture_time:
            metadata["capture_time"] = record.metadata.capture_time.isoformat()

    focus_data = None
    if record.focus:
        focus_data = asdict(record.focus)
        focus_data["source"] = record.focus.source.value
        if record.focus.roi:
            focus_data["roi"] = list(record.focus.roi)

    return json.dumps(
        {
            "metadata": metadata,
            "focus": focus_data,
            "error": record.error,
            "dhash": record.dhash,
        },
        ensure_ascii=False,
    )


def _deserialize(path: Path, payload: str) -> ImageRecord | None:
    """A damaged cache is treated as a cache miss - it does not kill the
    batch."""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(data, dict):
        return None

    # If the payload is off in any way (a field layout left by an older
    # version, damage, and so on) it is quietly dropped to a cache miss.
    # Analysing again is all it costs, and that is far better than forcing
    # a restore and using wrong values.
    try:
        metadata = None
        if data.get("metadata"):
            values = dict(data["metadata"])
            capture = values.get("capture_time")
            values["capture_time"] = datetime.fromisoformat(capture) if capture else None
            metadata = RawMetadata(path=path, **values)

        focus_result = None
        if data.get("focus"):
            values = dict(data["focus"])
            values["source"] = FocusSource(values["source"])
            if values.get("roi"):
                values["roi"] = tuple(values["roi"])
            # JSON hands tuples back as lists. Left as they are, the
            # FocusResult before and after storing differ from each other
            # and comparisons and tests go out of step.
            if values.get("faces"):
                values["faces"] = tuple(tuple(box) for box in values["faces"])
            if values.get("face_scores"):
                values["face_scores"] = tuple(
                    float(score) for score in values["face_scores"])
            focus_result = FocusResult(**values)
    except (TypeError, ValueError, KeyError, AttributeError):
        return None

    return ImageRecord(
        path=path,
        metadata=metadata,
        focus=focus_result,
        error=data.get("error"),
        dhash=data.get("dhash"),
    )


# -------------------------------------------------------- the cache itself


class AnalysisCache:
    """It counts as a hit only when the file fingerprint and the parameter
    fingerprint both match."""

    def __init__(self, db_path: Path, params_key: str):
        self.db_path = Path(db_path)
        self.params_key = params_key
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> "AnalysisCache":
        self.open()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def open(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        # The PRAGMAs have to be set right after connecting, before any
        # transaction opens. Push them behind the schema creation or an
        # INSERT and sqlite refuses with "Safety level may not be changed
        # inside a transaction".
        # Writing 4000 entries is far too slow in the default synchronous
        # mode, and losing the cache only costs a re-analysis, so we give
        # up a little durability.
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.executescript(_SCHEMA)
        self._check_schema_version()
        self._conn.commit()

    def _check_schema_version(self) -> None:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        elif row[0] != str(SCHEMA_VERSION):
            log.info("캐시 스키마 버전 불일치 (%s != %s), 캐시를 비운다", row[0], SCHEMA_VERSION)
            self._conn.execute("DELETE FROM analysis")
            self._conn.execute(
                "UPDATE meta SET value = ? WHERE key = 'schema_version'",
                (str(SCHEMA_VERSION),),
            )

    def close(self) -> None:
        if self._conn is not None:
            self._conn.commit()
            self._conn.close()
            self._conn = None

    @staticmethod
    def fingerprint(path: Path) -> tuple[float, int] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return stat.st_mtime, stat.st_size

    def get_many(self, paths: list[Path]) -> dict[Path, ImageRecord]:
        """Returns only what is in the cache. Anything missing drops out."""
        if self._conn is None or not paths:
            return {}

        wanted = {str(p): p for p in paths}
        hits: dict[Path, ImageRecord] = {}

        # split the query so it stays under SQLite's variable limit (999)
        keys = list(wanted)
        for start in range(0, len(keys), 500):
            chunk = keys[start:start + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = self._conn.execute(
                f"SELECT path, mtime, size, payload FROM analysis "
                f"WHERE params_key = ? AND path IN ({placeholders})",
                (self.params_key, *chunk),
            ).fetchall()

            for path_str, mtime, size, payload in rows:
                path = wanted[path_str]
                current = self.fingerprint(path)
                if current is None or current[0] != mtime or current[1] != size:
                    continue  # the file changed - it has to be analysed again
                record = _deserialize(path, payload)
                if record is not None:
                    hits[path] = record

        return hits

    def count_ready(self, paths: list[Path]) -> int:
        """How many of these paths can use the cache as it is.

        Used by the start-of-analysis dialog to show "this many instantly,
        this many analysed afresh". It uses the same test as get_many (the
        parameter fingerprint plus the file fingerprint) but does not
        deserialise the payload, so it answers instantly even for thousands
        of frames.
        """
        if self._conn is None or not paths:
            return 0
        wanted = {str(p): p for p in paths}
        ready = 0
        keys = list(wanted)
        for start in range(0, len(keys), 500):
            chunk = keys[start:start + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = self._conn.execute(
                f"SELECT path, mtime, size FROM analysis "
                f"WHERE params_key = ? AND path IN ({placeholders})",
                (self.params_key, *chunk),
            ).fetchall()
            for path_str, mtime, size in rows:
                current = self.fingerprint(wanted[path_str])
                if current is not None and current[0] == mtime and current[1] == size:
                    ready += 1
        return ready

    def put_many(self, records: list[ImageRecord]) -> None:
        if self._conn is None or not records:
            return

        rows = []
        for record in records:
            fingerprint = self.fingerprint(record.path)
            if fingerprint is None:
                continue
            rows.append(
                (
                    str(record.path),
                    fingerprint[0],
                    fingerprint[1],
                    self.params_key,
                    _serialize(record),
                )
            )

        with closing(self._conn.cursor()) as cursor:
            cursor.executemany(
                "INSERT OR REPLACE INTO analysis (path, mtime, size, params_key, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
        self._conn.commit()

    def clear(self) -> None:
        if self._conn is not None:
            self._conn.execute("DELETE FROM analysis")
            self._conn.commit()
