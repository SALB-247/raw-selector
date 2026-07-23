"""내보내기 대기열.

여러 폴더를 돌아다니며 "이 컷들은 이 프리셋으로" 를 쌓아두고, 마지막에
한 번에 내보냅니다. 4000장 배치에서 현상까지 하면 수십 분이 걸리므로,
작업할 때마다 기다리는 대신 모아서 한 번에 돌리는 편이 낫습니다.

항목은 (원본 경로, 보정 설정) 쌍입니다. 원본은 경로로만 들고 있으므로
대기열을 JSON으로 저장했다가 다음 세션에서 이어서 쓸 수 있습니다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .develop import DevelopSettings
from .types import Grade, ImageRecord

log = logging.getLogger(__name__)

QUEUE_VERSION = 1


@dataclass
class QueueEntry:
    """대기열 한 줄 — 원본 하나와 거기 적용할 보정."""

    source: Path
    develop: DevelopSettings | None = None
    grade: Grade = Grade.KEEP
    preset_name: str | None = None
    """어떤 프리셋에서 왔는지. 목록에 보여주기 위한 것으로 동작에는 영향 없습니다."""

    @property
    def has_develop(self) -> bool:
        return self.develop is not None and not self.develop.is_neutral()

    def to_dict(self) -> dict:
        return {
            "source": str(self.source),
            "develop": self.develop.to_dict() if self.develop else None,
            "grade": self.grade.value,
            "preset_name": self.preset_name,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "QueueEntry":
        if not isinstance(data, dict):
            raise TypeError(f"대기열 항목이 dict가 아닙니다: {type(data).__name__}")
        develop = data.get("develop")
        return cls(
            source=Path(data["source"]),
            develop=DevelopSettings.from_dict(develop) if develop else None,
            grade=Grade(data.get("grade", "keep")),
            preset_name=data.get("preset_name"),
        )


@dataclass
class ExportQueue:
    """중복 없이 항목을 쌓습니다.

    같은 원본을 다시 담으면 새로 추가하지 않고 보정만 갱신합니다. 사용자가
    값을 고쳐서 다시 담는 것은 "덮어쓰기"를 의도한 것이지 같은 사진을 두 번
    내보내려는 게 아닙니다.
    """

    entries: list[QueueEntry] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries)

    @property
    def develop_count(self) -> int:
        return sum(1 for e in self.entries if e.has_develop)

    def index_of(self, source: Path) -> int | None:
        for index, entry in enumerate(self.entries):
            if entry.source == source:
                return index
        return None

    def add(
        self,
        source: Path,
        develop: DevelopSettings | None = None,
        grade: Grade = Grade.KEEP,
        preset_name: str | None = None,
    ) -> bool:
        """새로 담았으면 True, 기존 항목을 갱신했으면 False."""
        existing = self.index_of(source)
        entry = QueueEntry(source, develop, grade, preset_name)
        if existing is None:
            self.entries.append(entry)
            return True
        self.entries[existing] = entry
        return False

    def add_records(
        self,
        records: list[ImageRecord],
        develop: DevelopSettings | None = None,
        preset_name: str | None = None,
    ) -> tuple[int, int]:
        """레코드 여러 개를 담습니다. (새로 추가, 갱신) 개수를 반환.

        develop을 주지 않으면 각 레코드에 이미 지정된 보정을 그대로 씁니다.
        """
        added = updated = 0
        for record in records:
            settings = develop if develop is not None else record.develop
            if self.add(record.path, settings, record.final_grade, preset_name):
                added += 1
            else:
                updated += 1
        return added, updated

    def remove(self, sources: list[Path]) -> int:
        targets = set(sources)
        before = len(self.entries)
        self.entries = [e for e in self.entries if e.source not in targets]
        return before - len(self.entries)

    def clear(self) -> None:
        self.entries.clear()

    def missing_sources(self) -> list[Path]:
        """원본이 사라진 항목. 내보내기 전에 알려줘야 합니다."""
        return [e.source for e in self.entries if not e.source.exists()]

    def to_records(self) -> list[ImageRecord]:
        """export_records가 받는 형태로 바꿉니다.

        대기열은 경로만 들고 있으므로 분석 정보는 없습니다. 내보내기에 필요한
        것은 경로·등급·보정뿐이라 문제되지 않습니다.
        """
        records = []
        for entry in self.entries:
            record = ImageRecord(path=entry.source)
            record.grade = entry.grade
            record.develop = entry.develop
            records.append(record)
        return records

    # ------------------------------------------------------------ 저장

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": QUEUE_VERSION,
            "saved": datetime.now().isoformat(timespec="seconds"),
            "entries": [e.to_dict() for e in self.entries],
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path

    @classmethod
    def load(cls, path: Path) -> "ExportQueue":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        queue = cls()
        # entries 자체가 리스트가 아니면(손 편집, 다른 버전) 순회 대상이 없습니다.
        # dict를 그냥 돌면 키 문자열이 항목으로 들어옵니다.
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, (list, tuple)):
            entries = ()
        for item in entries:
            try:
                queue.entries.append(QueueEntry.from_dict(item))
            except (KeyError, ValueError, TypeError, AttributeError) as exc:
                # 항목 하나가 깨졌다고 대기열 전체를 버릴 이유는 없습니다
                log.warning("대기열 항목을 건너뛴다: %s", exc)
        return queue
