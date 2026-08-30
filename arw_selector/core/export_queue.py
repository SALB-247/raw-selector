"""The export queue.

You go round several folders piling up "these frames with this preset", and
export the lot at the end. Developing a 4000-frame batch takes tens of
minutes, so gathering it up and running it once is better than waiting
every time you work.

An entry is a (source path, adjustment settings) pair. The source is held
only as a path, so the queue can be saved as JSON and picked up again in
the next session.
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
    """One line of the queue - one source and the adjustment to apply to
    it."""

    source: Path
    develop: DevelopSettings | None = None
    grade: Grade = Grade.KEEP
    preset_name: str | None = None
    """Which preset it came from. For showing in the list; it has no effect
    on behaviour."""

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
    """Piles entries up without duplicates.

    Queuing the same source again does not add a new entry, it just updates
    the adjustment. A user changing the values and queuing again means
    "overwrite", not exporting the same photo twice.
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
        """True if newly queued, False if an existing entry was updated."""
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
        """Queues several records. Returns the (added, updated) counts.

        Without a develop given, the adjustment already assigned to each
        record is used as it is.
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
        """Entries whose source has gone. The user has to be told before
        exporting."""
        return [e.source for e in self.entries if not e.source.exists()]

    def to_records(self) -> list[ImageRecord]:
        """Converts to the shape export_records takes.

        The queue holds only paths, so there is no analysis information.
        All the export needs is the path, the grade and the adjustment, so
        that is not a problem.
        """
        records = []
        for entry in self.entries:
            record = ImageRecord(path=entry.source)
            record.grade = entry.grade
            record.develop = entry.develop
            records.append(record)
        return records

    # ------------------------------------------------------------ saving

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
        # If entries itself is not a list (hand editing, another version)
        # there is nothing to iterate over. Iterating a dict as it is would
        # bring the key strings in as entries.
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, (list, tuple)):
            entries = ()
        for item in entries:
            try:
                queue.entries.append(QueueEntry.from_dict(item))
            except (KeyError, ValueError, TypeError, AttributeError) as exc:
                # one broken entry is no reason to throw the whole queue away
                log.warning("대기열 항목을 건너뛴다: %s", exc)
        return queue
