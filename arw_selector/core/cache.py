"""Cache of the analysis results.

Analysing 4000 frames takes minutes. Repeating that every time you adjust a
threshold or reopen the GUI makes the tool unusable. If the file has not
changed and the analysis parameters are the same, the stored result is used
as it is.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import sys
import unicodedata
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .raw_io import RawMetadata
from .types import FocusResult, FocusSource, ImageRecord

log = logging.getLogger(__name__)

from .appinfo import (CACHE_DIR_NAME, LEGACY_CACHE_DIR_NAMES, is_writable_dir,
                      user_state_dir)

CACHE_FILE_NAME = "analysis.sqlite"

ROOT_MARKER = "folder.txt"
"""Written into a cache that does not live inside its shoot folder: the
folder the keys are relative to, as an absolute path. See cache_root."""

MTIME_TOLERANCE = 2.0
"""Seconds of modification-time drift a cached row survives.

FAT32 keeps modification times to 2 seconds, and the two operating systems
that read the same card turn an exFAT timestamp into a float by different
arithmetic. Compared for exact equality, a file looked changed when only
the reading of its clock had. The size still has to match exactly."""


def relative_key(root: Path, path: Path) -> str:
    """The key a file is cached under: its path relative to the shoot folder.

    The cache lives inside the shoot folder, so the folder is the one thing
    guaranteed to be wherever the cache is. Keying on the absolute path
    broke the cache's own premise ("move the folder and it comes along"):
    the same card mounted as /Volumes/Untitled 1 instead of /Volumes/Untitled,
    given another drive letter on Windows, or read on the other machine,
    missed every row and analysed everything again. POSIX separators and
    NFC, so the key is the same string on macOS and Windows.

    A file outside the folder (Open files across folders) gets a `..` key.
    One on another Windows drive, where no relative path exists, keeps its
    absolute path.
    """
    try:
        relative = os.path.relpath(path, root)
    except ValueError:
        return unicodedata.normalize("NFC", str(path))
    return unicodedata.normalize("NFC", Path(relative).as_posix())


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


def _mount_root(path: Path) -> Path:
    """The mount point the path sits on (the drive root on Windows)."""
    current = path
    while not os.path.ismount(current):
        if current.parent == current:
            break
        current = current.parent
    return current


def _windows_volume_id(root: Path) -> str | None:
    import ctypes
    from ctypes import wintypes

    label = ctypes.create_unicode_buffer(261)
    filesystem = ctypes.create_unicode_buffer(261)
    serial = wintypes.DWORD()
    longest = wintypes.DWORD()
    flags = wintypes.DWORD()
    ok = ctypes.windll.kernel32.GetVolumeInformationW(  # type: ignore[attr-defined]
        str(root), label, 261, ctypes.byref(serial), ctypes.byref(longest),
        ctypes.byref(flags), filesystem, 261)
    if not ok:
        return None
    return f"{label.value}-{serial.value:08X}"


def _macos_volume_id(root: Path) -> str | None:
    import plistlib

    for tool in ("/usr/sbin/diskutil", "diskutil"):
        try:
            run = subprocess.run([tool, "info", "-plist", str(root)],
                                 capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        if run.returncode != 0:
            return None
        info = plistlib.loads(run.stdout)
        uuid = info.get("VolumeUUID")
        name = info.get("VolumeName") or root.name
        return f"{name}-{uuid}" if uuid else None
    return None


def volume_identity(folder: Path) -> tuple[str, str] | None:
    """What stays the same about a folder on removable media across mounts:
    the volume's own identity and the path inside the volume.

    The absolute path does not. A card reader gets whatever drive letter is
    free that day, and macOS mounts a second volume of the same name as
    "Untitled 1" - this machine has "Backup 1" to "Backup 9" in /Volumes
    from one backup disk. Windows gives every volume a serial number; macOS
    gives even an exFAT card a volume UUID (diskutil), and asking takes
    about 0.1s. Both survive re-formatting only as a new identity, which
    is right: a re-formatted card is a different card.

    None for the system disk, whose paths are stable anyway and for which
    the question would cost a diskutil call on every folder, and whenever
    the platform cannot answer; the caller then falls back to the path.
    """
    try:
        resolved = Path(folder).resolve()
        root = _mount_root(resolved)
        if sys.platform == "win32":
            system = (os.environ.get("SystemDrive", "C:") + "\\").upper()
            if root.anchor.upper() == system:
                return None
            volume = _windows_volume_id(root)
        elif sys.platform == "darwin":
            if root == Path("/"):
                return None
            volume = _macos_volume_id(root)
        else:
            volume = root.name if root != Path("/") else None
        if not volume:
            return None
        inside = unicodedata.normalize("NFC", resolved.relative_to(root).as_posix())
        return volume, inside
    except (OSError, ValueError):
        return None


def _fallback_cache_dir(folder: Path) -> Path:
    """Where the cache goes when the shoot folder cannot be written: a
    per-user directory named after the folder.

    A card with its lock switch on, an NTFS drive on macOS, a DVD or a
    read-only share cannot hold a cache. The name carries the folder's
    basename for the human and a hash for uniqueness - two cards both
    ending in DCIM/100MSDCF must not share one cache. The hash is of the
    volume's identity plus the path inside it (volume_identity), so the
    same card found at another drive letter or mount name comes back to
    the same cache; only when no identity can be had is it the absolute
    path.
    """
    folder = Path(folder)
    identity = volume_identity(folder)
    if identity is not None:
        text = f"{identity[0]}|{identity[1]}"
    else:
        text = unicodedata.normalize("NFC", folder.resolve().as_posix())
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    return user_state_dir() / "cache" / f"{folder.name or 'root'}-{digest}"


def cache_dir_for(folder: Path) -> Path:
    """The cache directory to use for this folder, created if needed.

    Inside the folder whenever that can be written - the cache then travels
    with the folder. One that already exists inside is used even when it
    cannot be written (the card was analysed, then locked): its rows still
    hit, see AnalysisCache.open. Only when the folder refuses a new
    directory does the per-user fallback take over. Before 0.15.12 such a
    folder simply had no cache: every opening analysed everything again.
    """
    folder = Path(folder)
    inside = resolve_cache_dir(folder)
    if inside.is_dir() or is_writable_dir(inside):
        return inside
    fallback = _fallback_cache_dir(folder)
    # The marker names the folder the keys are relative to. It is brought
    # up to date on every visit: the same card can come back under another
    # drive letter, and the keys must then be taken relative to that path.
    current = unicodedata.normalize("NFC", str(folder))
    try:
        fallback.mkdir(parents=True, exist_ok=True)
        marker = fallback / ROOT_MARKER
        if not marker.exists() or marker.read_text(encoding="utf-8").strip() != current:
            staging = marker.with_suffix(".tmp")
            staging.write_text(current, encoding="utf-8")
            os.replace(staging, marker)
    except OSError as exc:
        log.warning("캐시 폴더를 만들 수 없다 (%s), 캐시 없이 진행: %s", fallback, exc)
        return inside
    log.info("폴더에 쓸 수 없어 캐시를 사용자 폴더에 둔다: %s", fallback)
    return fallback


def existing_cache_dir(folder: Path) -> Path | None:
    """The cache directory that exists for this folder, if any - inside it
    or the per-user fallback. Nothing is created."""
    inside = resolve_cache_dir(folder)
    if inside.exists():
        return inside
    fallback = _fallback_cache_dir(folder)
    return fallback if fallback.exists() else None


def cache_root(cache_dir: Path) -> Path:
    """The folder a cache's keys are relative to: its parent, or the folder
    a fallback cache was made for (recorded in ROOT_MARKER)."""
    cache_dir = Path(cache_dir)
    marker = cache_dir / ROOT_MARKER
    try:
        if marker.is_file():
            text = marker.read_text(encoding="utf-8").strip()
            if text:
                return Path(text)
    except OSError:
        pass
    return cache_dir.parent

SCHEMA_VERSION = 11
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

v10: Sony's AF tracking state (0x2021) joins af_area_mode. A tracking
shot had no AF line at all - the area mode value it comes with is one
we have not verified, so it stayed None. Same reason for the bump as v9.
And a tracking frame now outranks face detection for the ROI (the face
it sits in, or the frame itself when it sits in none), so the scores of
tracking shots change with it.

v11: an AF area mode value with no verified name is kept as "Mode N"
instead of None - a Sony A1 writes 0/1/3 for its everyday modes, and up
to v10 every one of those frames had an empty AF line. Same reason as v9.
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
    it comes along. A folder that cannot be written gets a per-user cache
    instead - see cache_dir_for."""
    return cache_dir_for(folder) / CACHE_FILE_NAME


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
    cache_dir = existing_cache_dir(folder)
    if cache_dir is None:
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
    cache_dir = existing_cache_dir(folder)
    if cache_dir is None:
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
        # <shoot folder>/.raw_selector_cache/analysis.sqlite - the folder
        # the keys are relative to is two levels up; a cache kept outside
        # its folder records that folder instead (cache_root).
        self.root = cache_root(self.db_path.parent)
        self._conn: sqlite3.Connection | None = None
        #: Set when the cache could only be opened for reading. Rows still
        #: hit; put_many and clear do nothing.
        self.read_only = False

    def key_for(self, path: Path) -> str:
        return relative_key(self.root, path)

    def _wanted(self, paths: list[Path]) -> dict[str, Path]:
        """Key -> path, under the current key and under the absolute path
        versions before 0.15.12 wrote, so a cache they built still hits."""
        wanted: dict[str, Path] = {}
        for path in paths:
            wanted[self.key_for(path)] = path
            wanted.setdefault(str(path), path)
        return wanted

    @staticmethod
    def _unchanged(current: tuple[float, int] | None, mtime: float, size: int) -> bool:
        return (current is not None and current[1] == size
                and abs(current[0] - mtime) <= MTIME_TOLERANCE)

    def __enter__(self) -> "AnalysisCache":
        self.open()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def open(self, retry: bool = True) -> None:
        self.read_only = False
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path)
        except (OSError, sqlite3.Error) as exc:
            self._open_read_only(exc)
            return
        try:
            # The PRAGMAs have to be set right after connecting, before any
            # transaction opens. Push them behind the schema creation or an
            # INSERT and sqlite refuses with "Safety level may not be changed
            # inside a transaction".
            # Writing 4000 entries is far too slow in the default synchronous
            # mode, and losing the cache only costs a re-analysis, so we give
            # up a little durability.
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            # A file on read-only media opens without complaint and only
            # refuses the first write. Asking for the write lock now finds
            # that out before any row is trusted to it.
            conn.execute("BEGIN IMMEDIATE")
            conn.rollback()
            conn.executescript(_SCHEMA)
            self._conn = conn
            self._check_schema_version()
            conn.commit()
        except sqlite3.Error as exc:
            self._conn = None
            conn.close()
            # Plain DatabaseError is what SQLite raises for a file that is
            # not a database or whose image is malformed; its subclasses
            # (OperationalError and the rest) cover locks and permissions,
            # which must not cost anyone their cache.
            if retry and type(exc) is sqlite3.DatabaseError and self._set_aside_corrupt(exc):
                self.open(retry=False)
                return
            self._open_read_only(exc)

    def _set_aside_corrupt(self, cause: Exception) -> bool:
        """Renames a corrupt cache file out of the way so a fresh one can
        be built in its place.

        A cache is only ever a saved re-analysis, so a broken one is worth
        nothing; left there, it broke every later opening too and the
        folder never got a cache again. It is renamed rather than deleted,
        in case someone wants to look at what happened.
        """
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        aside = self.db_path.with_name(f"{self.db_path.name}.corrupt-{stamp}")
        try:
            os.replace(self.db_path, aside)
            for suffix in ("-wal", "-shm"):
                Path(str(self.db_path) + suffix).unlink(missing_ok=True)
        except OSError as exc:
            log.warning("깨진 캐시 파일을 치울 수 없다 (%s): %s", self.db_path, exc)
            return False
        log.warning("캐시 파일이 깨져 새로 만든다 (%s → %s): %s", self.db_path.name, aside.name, cause)
        return True

    def _open_read_only(self, cause: Exception) -> None:
        """A cache that cannot be written is still worth reading.

        A card with the lock switch on, an NTFS drive on macOS or a
        read-only share refuses the directory, the WAL switch or the write
        lock. Opened for reading only, the rows it holds still hit and
        put_many becomes a no-op.

        Two attempts. mode=ro alone works when the -shm file is there to
        read (another program still has the cache open), and then sees
        everything in the write-ahead log. A cleanly closed cache has no
        -shm, a read-only directory refuses to create one, and SQLite
        gives up - so the second attempt adds immutable=1, which reads the
        main file alone; anything not yet checkpointed into it is
        invisible, and a cleanly closed cache has nothing pending.
        """
        if not self.db_path.is_file():
            raise cause
        base = self.db_path.resolve().as_uri()
        row = None
        conn = None
        for query in ("?mode=ro", "?mode=ro&immutable=1"):
            try:
                conn = sqlite3.connect(base + query, uri=True)
                row = conn.execute(
                    "SELECT value FROM meta WHERE key = 'schema_version'"
                ).fetchone()
                break
            except sqlite3.Error:
                if conn is not None:
                    conn.close()
                conn = None
        if conn is None:
            log.info("캐시를 읽기 전용으로도 열 수 없다 (%s): %s", self.db_path, cause)
            return
        if row is None or row[0] != str(SCHEMA_VERSION):
            conn.close()
            log.info("읽기 전용 캐시의 스키마가 달라 쓰지 않는다: %s", self.db_path)
            return
        self._conn = conn
        self.read_only = True
        log.info("캐시를 읽기 전용으로 연다 (%s): %s", self.db_path, cause)

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
            if not self.read_only:
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

        wanted = self._wanted(paths)
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
                if path in hits:
                    continue  # already found under the other key form
                if not self._unchanged(self.fingerprint(path), mtime, size):
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
        wanted = self._wanted(paths)
        ready: set[Path] = set()
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
                path = wanted[path_str]
                if path not in ready and self._unchanged(self.fingerprint(path), mtime, size):
                    ready.add(path)
        return len(ready)

    def put_many(self, records: list[ImageRecord]) -> None:
        if self._conn is None or self.read_only or not records:
            return

        rows = []
        stale = []
        for record in records:
            fingerprint = self.fingerprint(record.path)
            if fingerprint is None:
                continue
            key = self.key_for(record.path)
            rows.append((key, fingerprint[0], fingerprint[1], self.params_key,
                         _serialize(record)))
            # Versions before 0.15.12 keyed on the absolute path. Left in
            # place, that row would sit next to this one for the same file.
            legacy = str(record.path)
            if legacy != key:
                stale.append((legacy,))

        with closing(self._conn.cursor()) as cursor:
            if stale:
                cursor.executemany("DELETE FROM analysis WHERE path = ?", stale)
            cursor.executemany(
                "INSERT OR REPLACE INTO analysis (path, mtime, size, params_key, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
        self._conn.commit()

    def clear(self) -> None:
        if self._conn is not None and not self.read_only:
            self._conn.execute("DELETE FROM analysis")
            self._conn.commit()
