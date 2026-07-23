"""분석 결과 캐시.

4000장 분석은 몇 분이 걸립니다. 임계값을 조정하거나 GUI를 다시 열 때맙니다
그걸 반복하면 사용할 수 없습니다. 파일이 안 바뀌었고 분석 파라미터도 같으면
저장해둔 결과를 그대로 씁니다.
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
    """폴더의 캐시 디렉터리. 예전 이름으로 만들어진 것도 계속 씁니다.

    제품명이 바뀌어도 이미 분석해 둔 폴더를 다시 분석하게 만들지 않기
    위해서입니다. 새 이름이 없고 예전 이름이 있으면 그쪽을 그대로 씁니다.
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

SCHEMA_VERSION = 6
"""스키마나 payload 구성이 바뀌면 올립니다. 기존 캐시는 버려집니다.

v2: 그룹핑용 dhash 추가. 예전 캐시는 dhash가 없어 그룹핑이 시각 정보만으로
조용히 퇴화하므로, 다시 분석하게 만듭니다.

v3: 얼굴 박스 전체(faces)와 주 피사체 인덱스(main_face) 추가. 없으면 화면에
얼굴을 하나도 못 그리고, 주 피사체 선정도 예전(면적 기준) 결과가 그대로
남습니다. 초점 기준으로 다시 고르게 하려면 재분석이 필요합니다.

v4: roi·faces 좌표의 기준 크기(source_width/height)와 얼굴 검출 임계값 변경.
기준 크기가 없으면 화면 쪽에서 "내장 프리뷰 가로 = 센서 가로"로 어림잡는데,
파나소닉 S1R처럼 4700만 화소에 1920px 프리뷰만 넣는 바디에서 박스가 4.37배
어긋났습니다. 임계값도 올려서 오검출(모자의 고양이 귀 등)을 걸러냅니다.

v5: 주 피사체 얼굴 선정 기준 교체. 선명도를 패치 분산으로 나눠 비교하던 것을
정규화 없는 그래디언트 에너지로 바꾸고, 명암이 거의 없는 조각을 후보에서
뺐습니다. **main_face는 캐시에 저장되는 값이라 버전을 올리지 않으면 예전
결과가 그대로 보입니다** — 실제로 고친 뒤에도 화면이 그대로여서 한참 헤맸습니다.

v6: 주 피사체의 눈 개폐(eyes_open) 추가. 없으면 -1(못 잼)로 남아 눈 감김
감점이 영원히 걸리지 않습니다. 값이 캐시에 들어가는 종류라 재분석해야
합니다.
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
    """촬영 폴더 옆에 캐시를 둡니다. 폴더를 전체가 옮겨도 따라갑니다."""
    return resolve_cache_dir(folder) / CACHE_FILE_NAME


@dataclass(frozen=True)
class CacheStats:
    """캐시가 지금 얼마나 자리를 차지하고 있는지."""

    exists: bool = False
    analysis_entries: int = 0
    thumbnail_count: int = 0
    analysis_bytes: int = 0
    thumbnail_bytes: int = 0
    log_count: int = 0

    @property
    def total_bytes(self) -> int:
        return self.analysis_bytes + self.thumbnail_bytes

    # 표시 단위는 전부 MiB로 통일합니다. 부분과 합계에서 단위가 갈리면
    # 사용자가 보기에 숫자가 안 맞습니다.
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
    """폴더의 캐시 상태를 조사합니다. 없거나 읽을 수 없으면 빈 값."""
    cache_dir = resolve_cache_dir(folder)
    if not cache_dir.exists():
        return CacheStats()

    db_path = cache_dir / CACHE_FILE_NAME
    analysis_bytes = 0
    entries = 0

    if db_path.exists():
        # WAL/SHM 파일도 캐시 용량에 포함됩니다
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
            entries = 0  # 손상된 캐시 — 개수는 몰라도 삭제는 할 수 있습니다

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
    """캐시를 지웁니다. 지우기 직전 상태를 반환합니다.

    내보내기 로그는 기본적으로 남긴다 — 그게 사라지면 되돌리기를 할 수
    없게 되는데, 사용자는 '캐시 삭제'가 되돌리기를 없앨 거라고 예상하지
    않습니다.
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

    # 안이 비었으면 폴더 자체도 치웁니다
    try:
        if not any(cache_dir.iterdir()):
            cache_dir.rmdir()
    except OSError:
        pass

    return stats


# ---------------------------------------------------------------- 직렬화


def _serialize(record: ImageRecord) -> str:
    """캐시에 넣을 부분만 직렬화합니다.

    group_id / grade / score는 배치 전체를 봐야 정해지는 값이라 캐시하지
    않습니다. 파일 하나만 보고 결정되는 focus와 metadata만 저장합니다.
    """
    metadata = None
    if record.metadata:
        metadata = asdict(record.metadata)
        metadata.pop("path", None)  # 키가 곧 경롭니다
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
    """캐시 손상은 캐시 미스로 취급합니다 — 배치를 죽이지 않습니다."""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(data, dict):
        return None

    # 페이로드가 조금이라도 어긋나면 (예전 버전이 남긴 필드 구성, 손상 등)
    # 조용히 캐시 미스로 떨어뜨립니다. 다시 분석하면 그만이고, 무리하게 복원해서
    # 틀린 값을 쓰는 것보다 훨씬 낫습니다.
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
            # JSON은 튜플을 리스트로 되돌려 줍니다. 그대로 두면 저장 전후의
            # FocusResult가 서로 달라져 비교와 테스트가 어긋납니다.
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


# ---------------------------------------------------------------- 캐시 본체


class AnalysisCache:
    """파일 지문 + 파라미터 지문이 모두 맞을 때만 히트로 칩니다."""

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
        # PRAGMA는 반드시 연결 직후, 트랜잭션이 열리기 전에 걸어야 합니다.
        # 스키마 생성이나 INSERT 뒤로 밀면 sqlite가 "Safety level may not be
        # changed inside a transaction"으로 거부합니다.
        # 4000건 쓰기는 기본 동기화 모드에서 너무 느린데, 캐시는 유실돼도
        # 재분석하면 그만이라 내구성을 조금 양보합니다.
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
        """캐시에 있는 것만 골라 돌려줍니다. 없으면 빠집니다."""
        if self._conn is None or not paths:
            return {}

        wanted = {str(p): p for p in paths}
        hits: dict[Path, ImageRecord] = {}

        # SQLite 변수 개수 제한(기본 999)을 넘지 않게 나눠 조회합니다
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
                    continue  # 파일이 바뀌었다 — 다시 분석해야 합니다
                record = _deserialize(path, payload)
                if record is not None:
                    hits[path] = record

        return hits

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
