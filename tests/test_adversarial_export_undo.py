"""내보내기와 되돌리기가 이상한 상태에서도 버티는지.

되돌리기 로그는 이동 모드에서 **유일한 안전망**입니다. 4000장을 옮긴 뒤
로그가 조금 어긋났다고 되돌리기 자체가 예외로 죽으면, 사용자에게는
복구 수단이 하나도 남지 않습니다.

로그가 어긋나는 경로는 실재합니다: 내보내는 중에 전원이 나가거나,
다른 버전이 만든 로그를 열거나, 사용자가 파일을 열어 봤다가 고쳤을 때.
"""

from __future__ import annotations

import json

import pytest

from arw_selector.core import export
from arw_selector.core.export_options import ExportOptions
from arw_selector.core.types import Grade, ImageRecord


def make_record(path, grade=Grade.KEEP):
    record = ImageRecord(path=path)
    record.grade = grade
    return record


@pytest.fixture
def sources(tmp_path):
    folder = tmp_path / "원본 (카드1)"
    folder.mkdir()
    paths = []
    for index in range(4):
        path = folder / f"DSC0{index}.ARW"
        path.write_bytes(b"raw" * 8)
        paths.append(path)
    return paths


def write_log(tmp_path, payload) -> "object":
    path = tmp_path / "export_손상.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class TestUndoWithBrokenLog:
    """로그가 어떤 모양이어도 되돌리기가 예외로 끝나면 안 됩니다."""

    @pytest.mark.parametrize(
        "payload",
        [
            {"version": 1, "mode": "copy", "operations": []},           # root 없음
            {"version": 1, "mode": "copy", "root": "X"},                # operations 없음
            {"version": 1, "mode": "copy", "root": "X", "operations": {"a": 1}},
            {"version": 1, "mode": "copy", "root": "X", "operations": ["문자열"]},
            {"version": 1, "mode": "copy", "root": "X", "operations": [None]},
            {"version": 1, "mode": "copy", "root": "X", "operations": [{}]},
            {"version": 1, "mode": "copy", "root": "X",
             "operations": [{"source": "a.ARW"}]},                      # destination 없음
            {"version": 1, "mode": "copy", "root": "X",
             "operations": [{"destination": "b.ARW"}]},                 # source 없음
            {"version": 1, "mode": "move", "root": 12345, "operations": []},
            {"version": 1, "mode": 999, "root": "X", "operations": []},
            {"version": 9999, "mode": "copy", "root": "X", "operations": []},
            {},
        ],
    )
    def test_broken_log_does_not_raise(self, tmp_path, payload):
        if payload.get("root") == "X":
            payload["root"] = str(tmp_path)
        result = export.undo_export(write_log(tmp_path, payload))
        assert result.moved == 0

    def test_one_broken_entry_does_not_block_the_rest(self, tmp_path, sources):
        """항목 하나가 깨졌다고 나머지 되돌리기를 포기하면 안 됩니다."""
        destination = tmp_path / "출력"
        result = export.export_records(
            [make_record(p) for p in sources], destination,
            options=ExportOptions(apply_develop=False),
        )
        assert result.moved == len(sources)

        payload = json.loads(result.log_path.read_text(encoding="utf-8"))
        payload["operations"].insert(2, {"source": "깨짐"})  # destination 없음
        payload["operations"].insert(0, "문자열")
        result.log_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        undone = export.undo_export(result.log_path)
        assert undone.moved == len(sources)
        assert undone.failed  # 깨진 항목은 실패로 보고돼야 합니다

    def test_still_reports_a_failure_it_could_not_undo(self, tmp_path):
        payload = {
            "version": 1, "mode": "copy", "root": str(tmp_path),
            "operations": [{"source": "a", "destination": "없는파일.jpg"}],
        }
        result = export.undo_export(write_log(tmp_path, payload))
        assert result.failed


class TestUndoRoundTrip:
    def test_copy_export_is_fully_reversible(self, tmp_path, sources):
        destination = tmp_path / "출력"
        result = export.export_records(
            [make_record(p) for p in sources], destination,
            options=ExportOptions(apply_develop=False),
        )
        export.undo_export(result.log_path)
        assert all(p.exists() for p in sources)
        assert not (destination / "_keep").exists()

    def test_move_export_returns_originals(self, tmp_path, sources):
        destination = tmp_path / "출력"
        result = export.export_records(
            [make_record(p) for p in sources], destination, move=True,
            options=ExportOptions(move=True, apply_develop=False),
        )
        assert result.moved == len(sources)
        assert not any(p.exists() for p in sources)

        export.undo_export(result.log_path)
        assert all(p.exists() for p in sources)

    def test_cancelled_export_is_reversible(self, tmp_path, sources):
        """중단해도 그때까지 한 일은 되돌릴 수 있어야 합니다."""
        seen = {"count": 0}

        def cancel_after_two() -> bool:
            seen["count"] += 1
            return seen["count"] > 2

        destination = tmp_path / "출력"
        result = export.export_records(
            [make_record(p) for p in sources], destination,
            options=ExportOptions(apply_develop=False),
            should_cancel=cancel_after_two,
        )
        assert result.cancelled and result.moved == 2

        undone = export.undo_export(result.log_path)
        assert undone.moved == 2


class TestExportAgainstAMovingTarget:
    def test_source_vanishes_after_planning(self, tmp_path, sources):
        """계획을 세운 뒤 원본이 사라져도 나머지는 나가야 합니다.

        카드를 뽑거나 다른 프로그램이 옮기면 실제로 일어납니다.
        """
        records = [make_record(p) for p in sources]
        sources[1].unlink()

        result = export.export_records(
            records, tmp_path / "출력", options=ExportOptions(apply_develop=False))
        assert result.moved == len(sources) - 1

    def test_name_collision_never_overwrites(self, tmp_path):
        """다른 카드의 같은 파일명이 서로를 지우면 원본을 잃습니다."""
        destination = tmp_path / "출력"
        contents = []
        for card in range(3):
            folder = tmp_path / f"카드{card}"
            folder.mkdir()
            path = folder / "DSC0001.ARW"
            payload = f"카드{card}".encode("utf-8")
            path.write_bytes(payload)
            contents.append(payload)
            export.export_records(
                [make_record(path)], destination,
                options=ExportOptions(apply_develop=False))

        written = sorted(
            (destination / "_keep").iterdir(), key=lambda p: p.name)
        assert len(written) == 3
        assert sorted(p.read_bytes() for p in written) == sorted(contents)

    def test_destination_is_a_file_not_a_directory(self, tmp_path, sources):
        blocker = tmp_path / "출력"
        blocker.write_text("디렉터리가 아님", encoding="utf-8")
        result = export.export_records(
            [make_record(p) for p in sources], blocker,
            options=ExportOptions(apply_develop=False))
        assert result.moved == 0 and result.failed

    def test_empty_batch_writes_no_log(self, tmp_path):
        result = export.export_records([], tmp_path / "출력")
        assert result.moved == 0 and result.log_path is None


class TestFilenamePattern:
    """파일명 규칙이 파일을 만들 수 없는 이름을 내놓으면 안 됩니다."""

    @pytest.mark.parametrize(
        "pattern",
        ["{name}", "", "   ", "///", "{unknown}", "{index}_{grade}_{score}",
         "..", ".", "{name}...", "  {name}  ", "🎉{name}🎉", "{name}" * 40],
    )
    def test_pattern_produces_a_writable_name(self, tmp_path, pattern, sources):
        from arw_selector.core.export_options import format_filename

        record = make_record(sources[0])
        name = format_filename(pattern, record, 1, ".jpg")

        # 이름이 통째로 비거나 점으로 시작하면 숨김 파일이 됩니다
        assert name and not name.startswith(".")
        assert not set(name) & set('<>:"/\\|?*')
        # 끝의 점·공백은 Windows가 만들 때 말없이 잘라내 이름이 어긋납니다
        stem = name[: -len(".jpg")]
        assert stem == stem.rstrip(" .")

        target = tmp_path / name[:200]
        target.write_bytes(b"x")
        assert target.exists()

    def test_empty_stem_still_gets_a_name(self, tmp_path):
        """확장자만 있는 파일(.ARW)에서 이름이 통째로 비면 숨김 파일이 됩니다."""
        from arw_selector.core.export_options import format_filename

        record = make_record(tmp_path / ".ARW")
        for pattern in ("{name}", "///", ""):
            name = format_filename(pattern, record, 7, ".jpg")
            assert not name.startswith(".")
