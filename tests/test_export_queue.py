"""내보내기 대기열 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from arw_selector.core.develop import BasicSettings, DevelopSettings
from arw_selector.core.export_queue import ExportQueue, QueueEntry
from arw_selector.core.types import Grade, ImageRecord


def make_record(path: Path, grade: Grade = Grade.KEEP, develop=None) -> ImageRecord:
    record = ImageRecord(path=path)
    record.grade = grade
    record.develop = develop
    return record


@pytest.fixture
def queue() -> ExportQueue:
    return ExportQueue()


class TestAdd:
    def test_add_appends(self, queue, tmp_path):
        assert queue.add(tmp_path / "a.ARW") is True
        assert len(queue) == 1

    def test_same_source_updates_instead_of_duplicating(self, queue, tmp_path):
        """값을 고쳐서 다시 담는 건 덮어쓰기지 두 번 내보내기가 아닙니다."""
        path = tmp_path / "a.ARW"
        queue.add(path, DevelopSettings(basic=BasicSettings(exposure=0.5)))
        assert queue.add(path, DevelopSettings(basic=BasicSettings(exposure=1.5))) is False

        assert len(queue) == 1
        assert queue.entries[0].develop.basic.exposure == 1.5

    def test_add_records_uses_their_own_develop(self, queue, tmp_path):
        records = [
            make_record(tmp_path / "a.ARW", develop=DevelopSettings(basic=BasicSettings(contrast=10))),
            make_record(tmp_path / "b.ARW", develop=DevelopSettings(basic=BasicSettings(contrast=20))),
        ]
        added, updated = queue.add_records(records)

        assert (added, updated) == (2, 0)
        assert [e.develop.basic.contrast for e in queue] == [10, 20]

    def test_explicit_develop_overrides_record(self, queue, tmp_path):
        """프리셋을 지정해 담으면 그 값이 이깁니다."""
        records = [
            make_record(tmp_path / "a.ARW", develop=DevelopSettings(basic=BasicSettings(contrast=10)))
        ]
        queue.add_records(
            records, DevelopSettings(basic=BasicSettings(contrast=99)), preset_name="무대"
        )

        assert queue.entries[0].develop.basic.contrast == 99
        assert queue.entries[0].preset_name == "무대"

    def test_add_records_reports_updates(self, queue, tmp_path):
        records = [make_record(tmp_path / "a.ARW")]
        queue.add_records(records)
        assert queue.add_records(records) == (0, 1)

    def test_grade_carried_from_record(self, queue, tmp_path):
        record = make_record(tmp_path / "a.ARW", grade=Grade.REVIEW)
        queue.add_records([record])
        assert queue.entries[0].grade == Grade.REVIEW

    def test_manual_grade_wins(self, queue, tmp_path):
        record = make_record(tmp_path / "a.ARW", grade=Grade.REJECT)
        record.manual_grade = Grade.KEEP
        queue.add_records([record])
        assert queue.entries[0].grade == Grade.KEEP


class TestRemove:
    def test_remove_by_source(self, queue, tmp_path):
        queue.add(tmp_path / "a.ARW")
        queue.add(tmp_path / "b.ARW")
        assert queue.remove([tmp_path / "a.ARW"]) == 1
        assert [e.source.name for e in queue] == ["b.ARW"]

    def test_remove_missing_is_zero(self, queue, tmp_path):
        assert queue.remove([tmp_path / "nope.ARW"]) == 0

    def test_clear(self, queue, tmp_path):
        queue.add(tmp_path / "a.ARW")
        queue.clear()
        assert len(queue) == 0


class TestDevelopCount:
    def test_counts_only_real_adjustments(self, queue, tmp_path):
        queue.add(tmp_path / "a.ARW", DevelopSettings(basic=BasicSettings(exposure=1.0)))
        queue.add(tmp_path / "b.ARW", DevelopSettings())  # 기본값
        queue.add(tmp_path / "c.ARW", None)
        assert queue.develop_count == 1


class TestMissingSources:
    def test_detects_deleted_originals(self, queue, tmp_path):
        real = tmp_path / "real.ARW"
        real.write_bytes(b"x")
        queue.add(real)
        queue.add(tmp_path / "gone.ARW")

        missing = queue.missing_sources()
        assert [p.name for p in missing] == ["gone.ARW"]


class TestToRecords:
    def test_produces_exportable_records(self, queue, tmp_path):
        queue.add(
            tmp_path / "a.ARW",
            DevelopSettings(basic=BasicSettings(exposure=0.5)),
            Grade.REVIEW,
        )
        records = queue.to_records()

        assert len(records) == 1
        assert records[0].path == tmp_path / "a.ARW"
        assert records[0].final_grade == Grade.REVIEW
        assert records[0].develop.basic.exposure == 0.5


class TestPersistence:
    def test_round_trip(self, queue, tmp_path):
        queue.add(
            tmp_path / "a.ARW",
            DevelopSettings(basic=BasicSettings(exposure=0.8, contrast=15)),
            Grade.KEEP,
            preset_name="무대 조명",
        )
        queue.add(tmp_path / "b.ARW", None, Grade.REVIEW)

        path = queue.save(tmp_path / "queue.json")
        restored = ExportQueue.load(path)

        assert len(restored) == 2
        assert restored.entries[0].develop.basic.exposure == 0.8
        assert restored.entries[0].preset_name == "무대 조명"
        assert restored.entries[1].develop is None
        assert restored.entries[1].grade == Grade.REVIEW

    def test_corrupt_entry_is_skipped_not_fatal(self, tmp_path):
        """항목 하나가 깨졌다고 대기열 전체를 버릴 이유는 없습니다."""
        import json

        path = tmp_path / "queue.json"
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "entries": [
                        {"source": str(tmp_path / "good.ARW"), "grade": "keep"},
                        {"no_source_key": True},
                    ],
                }
            ),
            encoding="utf-8",
        )

        restored = ExportQueue.load(path)
        assert len(restored) == 1
        assert restored.entries[0].source.name == "good.ARW"

    def test_creates_parent_directory(self, queue, tmp_path):
        queue.add(tmp_path / "a.ARW")
        queue.save(tmp_path / "deep" / "nested" / "queue.json")
        assert (tmp_path / "deep" / "nested" / "queue.json").exists()


class TestQueueEntry:
    def test_neutral_develop_is_not_counted(self, tmp_path):
        assert QueueEntry(tmp_path / "a.ARW", DevelopSettings()).has_develop is False

    def test_real_develop_is_counted(self, tmp_path):
        entry = QueueEntry(
            tmp_path / "a.ARW", DevelopSettings(basic=BasicSettings(shadows=30))
        )
        assert entry.has_develop is True
