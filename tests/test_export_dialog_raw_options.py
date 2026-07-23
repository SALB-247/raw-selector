"""RAW가 없는 배치에서는 RAW 전용 옵션을 잠급니다.

'원본 RAW도 함께 내보내기'와 '함께 저장된 JPG/HIF/XMP도 내보내기'는
원본이 RAW일 때만 의미가 있습니다. JPEG·HIF만 있는 배치에서 켤 수 있게
두면, 켜 놓고 결과가 그대로인 것을 보며 이유를 찾게 됩니다.

조용히 무시하지 않고 왜 못 쓰는지 적습니다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from arw_selector.core.types import ImageRecord  # noqa: E402
from conftest import destroy_all_widgets, destroy_widget  # noqa: E402

SUMMARY = {"keep": 3, "review": 0, "reject": 0}


@pytest.fixture(scope="module")
def app():
    from arw_selector.gui import theme

    instance = QApplication.instance() or QApplication([])
    theme.apply_app_theme(instance)
    yield instance
    destroy_all_widgets(instance)


def _dialog(app, raw_count, tmp_path):
    from arw_selector.gui.export_dialog import ExportDialog

    return ExportDialog(tmp_path, SUMMARY, 1, None, None, raw_count=raw_count)


# ------------------------------------------------------- 잠금


def test_jpeg_only_locks_the_raw_options(app, tmp_path):
    dialog = _dialog(app, 0, tmp_path)
    try:
        assert not dialog.copy_raw.isEnabled()
        assert not dialog.copy_raw.isChecked()
        assert not dialog.include_companions.isEnabled()
        assert not dialog.include_companions.isChecked()
    finally:
        destroy_widget(dialog, app)


def test_it_says_why(app, tmp_path):
    """잠긴 이유가 없으면 고장으로 보입니다."""
    dialog = _dialog(app, 0, tmp_path)
    try:
        assert not dialog.raw_note.isHidden()
        assert "RAW" in dialog.raw_note.text()
    finally:
        destroy_widget(dialog, app)


def test_the_summary_does_not_claim_to_exclude_anything(app, tmp_path):
    """'원본 RAW 제외'는 뺄 것이 있다는 뜻으로 읽힙니다. 뺄 것이 없습니다."""
    dialog = _dialog(app, 0, tmp_path)
    try:
        assert "원본 RAW" not in dialog.summary_label.text()
    finally:
        destroy_widget(dialog, app)


# ------------------------------------------------------- 잠그지 않는 경우


def test_raw_present_keeps_them(app, tmp_path):
    dialog = _dialog(app, 3, tmp_path)
    try:
        assert dialog.copy_raw.isEnabled()
        assert dialog.include_companions.isEnabled()
        assert dialog.raw_note.isHidden()
    finally:
        destroy_widget(dialog, app)


def test_a_mixed_batch_keeps_them(app, tmp_path):
    """RAW 한 장이라도 있으면 그 컷에는 필요합니다.

    JPEG 쪽은 export가 파일 단위로 알아서 건너뜁니다.
    """
    dialog = _dialog(app, 1, tmp_path)
    try:
        assert dialog.copy_raw.isEnabled()
        assert dialog.raw_note.isHidden()
    finally:
        destroy_widget(dialog, app)


def test_not_knowing_leaves_them_alone(app, tmp_path):
    """raw_count를 안 넘기는 호출부가 남아 있어도 예전처럼 동작해야 합니다."""
    dialog = _dialog(app, None, tmp_path)
    try:
        assert dialog.copy_raw.isEnabled()
        assert dialog.raw_note.isHidden()
    finally:
        destroy_widget(dialog, app)


# ------------------------------------------------------- 세는 쪽


def test_raw_count_counts_only_raw():
    from arw_selector.gui.main_window import _raw_count

    records = [
        ImageRecord(path=Path("a.ARW")),
        ImageRecord(path=Path("b.jpg")),
        ImageRecord(path=Path("c.HIF")),
        ImageRecord(path=Path("d.cr3")),
    ]
    assert _raw_count(records) == 2
    assert _raw_count([]) == 0
