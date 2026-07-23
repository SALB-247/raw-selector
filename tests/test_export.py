"""export.py 단위 테스트.

이 모듈은 사용자의 원본 사진을 직접 건드립니다. 데이터 유실 가능성을
테스트로 촘촘히 막아야 합니다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from arw_selector.core import export
from arw_selector.core.develop import BasicSettings, DevelopSettings
from arw_selector.core.types import Grade, ImageRecord


def make_shoot(tmp_path: Path, names_and_grades: list[tuple[str, Grade]]) -> list[ImageRecord]:
    records = []
    for name, grade in names_and_grades:
        path = tmp_path / name
        path.write_bytes(f"raw data for {name}".encode())
        record = ImageRecord(path=path)
        record.grade = grade
        records.append(record)
    return records


class TestFindCompanions:
    def test_finds_paired_jpeg(self, tmp_path):
        raw = tmp_path / "DSC001.ARW"
        raw.touch()
        jpeg = tmp_path / "DSC001.JPG"
        jpeg.touch()
        assert export.find_companions(raw) == [jpeg]

    def test_finds_lightroom_sidecar(self, tmp_path):
        raw = tmp_path / "DSC001.ARW"
        raw.touch()
        sidecar = tmp_path / "DSC001.ARW.xmp"
        sidecar.touch()
        assert sidecar in export.find_companions(raw)

    def test_ignores_other_files(self, tmp_path):
        raw = tmp_path / "DSC001.ARW"
        raw.touch()
        (tmp_path / "DSC002.JPG").touch()
        (tmp_path / "notes.txt").touch()
        assert export.find_companions(raw) == []

    def test_does_not_include_itself(self, tmp_path):
        raw = tmp_path / "DSC001.ARW"
        raw.touch()
        assert raw not in export.find_companions(raw)


class TestBuildPlan:
    def test_routes_by_grade(self, tmp_path):
        records = make_shoot(
            tmp_path,
            [("a.ARW", Grade.KEEP), ("b.ARW", Grade.REVIEW), ("c.ARW", Grade.REJECT)],
        )
        plan = export.build_plan(records, tmp_path)
        destinations = {
            op.source.name: op.destination.parent.name for op in plan.operations
        }
        assert destinations == {"a.ARW": "_keep", "b.ARW": "_review", "c.ARW": "_reject"}

    def test_manual_grade_wins(self, tmp_path):
        records = make_shoot(tmp_path, [("a.ARW", Grade.REJECT)])
        records[0].manual_grade = Grade.KEEP
        plan = export.build_plan(records, tmp_path)
        assert plan.operations[0].destination.parent.name == "_keep"

    def test_missing_source_is_skipped_not_fatal(self, tmp_path):
        records = [ImageRecord(path=tmp_path / "gone.ARW")]
        plan = export.build_plan(records, tmp_path)
        assert plan.operations == []
        assert plan.skipped[0][1] == "원본이 없음"

    def test_touches_no_files(self, tmp_path):
        records = make_shoot(tmp_path, [("a.ARW", Grade.KEEP)])
        export.build_plan(records, tmp_path)
        assert not (tmp_path / "_keep").exists()


class TestExportCopy:
    def test_copy_preserves_originals(self, tmp_path):
        """기본 모드에서 원본은 절대 사라지면 안 됩니다."""
        records = make_shoot(tmp_path, [("a.ARW", Grade.KEEP), ("b.ARW", Grade.REJECT)])
        result = export.export_records(records, tmp_path)

        assert result.moved == 2
        assert (tmp_path / "a.ARW").exists()
        assert (tmp_path / "b.ARW").exists()
        assert (tmp_path / "_keep" / "a.ARW").exists()
        assert (tmp_path / "_reject" / "b.ARW").exists()

    def test_copy_carries_companions(self, tmp_path):
        """켰을 때는 짝 파일이 따라가야 합니다."""
        records = make_shoot(tmp_path, [("DSC001.ARW", Grade.KEEP)])
        (tmp_path / "DSC001.JPG").write_bytes(b"jpeg")
        export.export_records(records, tmp_path, include_companions=True)
        assert (tmp_path / "_keep" / "DSC001.JPG").exists()

    def test_companions_are_off_by_default(self, tmp_path):
        """기본은 RAW만. RAW+HEIF로 찍으면 파일이 두 배가 되므로 켜는 쪽이 선택입니다."""
        records = make_shoot(tmp_path, [("DSC001.ARW", Grade.KEEP)])
        (tmp_path / "DSC001.JPG").write_bytes(b"jpeg")
        (tmp_path / "DSC001.HIF").write_bytes(b"heif")

        export.export_records(records, tmp_path)

        assert (tmp_path / "_keep" / "DSC001.ARW").exists()
        assert not (tmp_path / "_keep" / "DSC001.JPG").exists()
        assert not (tmp_path / "_keep" / "DSC001.HIF").exists()

    def test_companions_can_be_disabled(self, tmp_path):
        records = make_shoot(tmp_path, [("DSC001.ARW", Grade.KEEP)])
        (tmp_path / "DSC001.JPG").write_bytes(b"jpeg")
        export.export_records(records, tmp_path, include_companions=False)
        assert not (tmp_path / "_keep" / "DSC001.JPG").exists()

    def test_content_is_intact(self, tmp_path):
        records = make_shoot(tmp_path, [("a.ARW", Grade.KEEP)])
        export.export_records(records, tmp_path)
        assert (tmp_path / "_keep" / "a.ARW").read_bytes() == b"raw data for a.ARW"

    def test_dry_run_changes_nothing(self, tmp_path):
        records = make_shoot(tmp_path, [("a.ARW", Grade.KEEP)])
        result = export.export_records(records, tmp_path, dry_run=True)
        assert result.moved == 0
        assert not (tmp_path / "_keep").exists()


class TestExportMove:
    def test_move_removes_originals(self, tmp_path):
        records = make_shoot(tmp_path, [("a.ARW", Grade.KEEP)])
        export.export_records(records, tmp_path, move=True)
        assert not (tmp_path / "a.ARW").exists()
        assert (tmp_path / "_keep" / "a.ARW").exists()


class TestCollisions:
    def test_never_overwrites_existing_file(self, tmp_path):
        """다른 카드에서 온 같은 파일명이 서로를 지우면 안 됩니다."""
        keep_dir = tmp_path / "_keep"
        keep_dir.mkdir()
        (keep_dir / "a.ARW").write_bytes(b"precious existing data")

        records = make_shoot(tmp_path, [("a.ARW", Grade.KEEP)])
        export.export_records(records, tmp_path)

        assert (keep_dir / "a.ARW").read_bytes() == b"precious existing data"
        assert (keep_dir / "a_1.ARW").read_bytes() == b"raw data for a.ARW"


class TestCancel:
    def test_cancel_stops_partway(self, tmp_path):
        """중단해도 그때까지 한 일은 로그에 남아 되돌릴 수 있어야 합니다."""
        records = make_shoot(tmp_path, [(f"{i}.ARW", Grade.KEEP) for i in range(10)])
        calls = {"n": 0}

        def should_cancel():
            calls["n"] += 1
            return calls["n"] > 3

        result = export.export_records(records, tmp_path, should_cancel=should_cancel)

        assert result.cancelled is True
        assert 0 < result.moved < 10
        assert result.log_path.exists()

    def test_cancelled_work_can_be_undone(self, tmp_path):
        records = make_shoot(tmp_path, [(f"{i}.ARW", Grade.KEEP) for i in range(10)])
        calls = {"n": 0}

        def should_cancel():
            calls["n"] += 1
            return calls["n"] > 3

        result = export.export_records(records, tmp_path, should_cancel=should_cancel)
        export.undo_export(result.log_path)

        assert not (tmp_path / "_keep").exists()
        assert len(list(tmp_path.glob("*.ARW"))) == 10


class TestUndo:
    def test_undo_copy_removes_copies_only(self, tmp_path):
        records = make_shoot(tmp_path, [("a.ARW", Grade.KEEP), ("b.ARW", Grade.REJECT)])
        result = export.export_records(records, tmp_path)

        undo = export.undo_export(result.log_path)

        assert undo.moved == 2
        assert not (tmp_path / "_keep" / "a.ARW").exists()
        assert not (tmp_path / "_reject" / "b.ARW").exists()
        assert (tmp_path / "a.ARW").exists()  # 원본은 그대로
        assert (tmp_path / "b.ARW").exists()

    def test_undo_move_restores_originals(self, tmp_path):
        records = make_shoot(tmp_path, [("a.ARW", Grade.KEEP)])
        result = export.export_records(records, tmp_path, move=True)
        assert not (tmp_path / "a.ARW").exists()

        export.undo_export(result.log_path)

        assert (tmp_path / "a.ARW").exists()
        assert (tmp_path / "a.ARW").read_bytes() == b"raw data for a.ARW"

    def test_undo_does_not_delete_preexisting_files(self, tmp_path):
        """충돌 시 새 이름으로 복사했으므로, 되돌리기는 원래 있던 파일을
        건드리면 안 됩니다."""
        keep_dir = tmp_path / "_keep"
        keep_dir.mkdir()
        (keep_dir / "a.ARW").write_bytes(b"precious existing data")

        records = make_shoot(tmp_path, [("a.ARW", Grade.KEEP)])
        result = export.export_records(records, tmp_path)
        export.undo_export(result.log_path)

        assert (keep_dir / "a.ARW").read_bytes() == b"precious existing data"
        assert not (keep_dir / "a_1.ARW").exists()

    def test_undo_cleans_up_empty_grade_dirs(self, tmp_path):
        records = make_shoot(tmp_path, [("a.ARW", Grade.KEEP)])
        result = export.export_records(records, tmp_path)
        export.undo_export(result.log_path)
        assert not (tmp_path / "_keep").exists()

    def test_undo_is_idempotent(self, tmp_path):
        """두 번 눌러도 사고가 나지 않아야 합니다."""
        records = make_shoot(tmp_path, [("a.ARW", Grade.KEEP)])
        result = export.export_records(records, tmp_path)
        export.undo_export(result.log_path)
        second = export.undo_export(result.log_path)

        assert second.moved == 0
        assert (tmp_path / "a.ARW").exists()

    def test_undo_companions_too(self, tmp_path):
        records = make_shoot(tmp_path, [("DSC001.ARW", Grade.KEEP)])
        (tmp_path / "DSC001.JPG").write_bytes(b"jpeg")
        result = export.export_records(records, tmp_path, move=True)
        export.undo_export(result.log_path)
        assert (tmp_path / "DSC001.ARW").exists()
        assert (tmp_path / "DSC001.JPG").exists()


class TestDevelopExport:
    """보정이 지정된 컷은 현상한 JPEG가 함께 나와야 합니다."""

    def _shoot_with_preview(self, tmp_path, settings):
        """load_preview를 가로채 합성 이미지를 쓴다 (ARW 파일 없이 검증)."""
        import numpy as np

        path = tmp_path / "DSC001.ARW"
        path.write_bytes(b"fake raw")
        record = ImageRecord(path=path)
        record.grade = Grade.KEEP
        record.develop = settings

        rng = np.random.default_rng(3)
        fake = rng.integers(40, 200, size=(80, 120, 3), dtype=np.uint8)
        return record, fake, None

    def test_renders_jpeg_alongside_raw(self, tmp_path, monkeypatch):
        record, fake, _ = self._shoot_with_preview(
            tmp_path, DevelopSettings(basic=BasicSettings(exposure=1.0))
        )
        monkeypatch.setattr(
            "arw_selector.core.raw_io.load_preview", lambda *a, **k: fake
        )

        result = export.export_records([record], tmp_path)

        assert result.rendered == 1
        assert (tmp_path / "_keep" / "DSC001.jpg").exists()
        # 원본 ARW도 그대로 나가야 나중에 다시 현상할 수 있습니다
        assert (tmp_path / "_keep" / "DSC001.ARW").exists()

    def test_neutral_settings_render_nothing(self, tmp_path):
        record, _, _ = self._shoot_with_preview(tmp_path, DevelopSettings())
        result = export.export_records([record], tmp_path)

        assert result.rendered == 0
        assert not (tmp_path / "_keep" / "DSC001.jpg").exists()

    def test_apply_develop_can_be_disabled(self, tmp_path, monkeypatch):
        record, fake, _ = self._shoot_with_preview(
            tmp_path, DevelopSettings(basic=BasicSettings(exposure=1.0))
        )
        monkeypatch.setattr(
            "arw_selector.core.raw_io.load_preview", lambda *a, **k: fake
        )

        result = export.export_records([record], tmp_path, apply_develop=False)

        assert result.rendered == 0
        assert (tmp_path / "_keep" / "DSC001.ARW").exists()

    def test_plan_counts_develop(self, tmp_path):
        record, _, _ = self._shoot_with_preview(
            tmp_path, DevelopSettings(basic=BasicSettings(contrast=20))
        )
        assert export.build_plan([record], tmp_path).develop_count == 1


class TestLogs:
    def test_log_records_every_operation(self, tmp_path):
        records = make_shoot(tmp_path, [("a.ARW", Grade.KEEP), ("b.ARW", Grade.REVIEW)])
        result = export.export_records(records, tmp_path)

        payload = json.loads(result.log_path.read_text(encoding="utf-8"))
        assert payload["mode"] == "copy"
        assert len(payload["operations"]) == 2
        assert {op["grade"] for op in payload["operations"]} == {"keep", "review"}

    def test_find_logs_returns_newest_first(self, tmp_path):
        records = make_shoot(tmp_path, [("a.ARW", Grade.KEEP)])
        first = export.export_records(records, tmp_path).log_path
        # 로그 파일명은 초 단위라 이름을 직접 만들어 두 번째를 흉내냅니다
        second = first.with_name("export_29991231_235959.json")
        second.write_text(first.read_text(encoding="utf-8"), encoding="utf-8")

        logs = export.find_logs(tmp_path)
        assert logs[0] == second
