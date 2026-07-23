"""채점표 위젯. 고른 컷이 왜 그 점수인지 보여 줍니다."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from arw_selector.core.config import ScoreConfig  # noqa: E402
from arw_selector.core.types import (  # noqa: E402
    FocusResult, FocusSource, Grade, ImageRecord,
)
from conftest import destroy_all_widgets, destroy_widget  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from arw_selector.gui import theme

    instance = QApplication.instance() or QApplication([])
    theme.apply_app_theme(instance)
    yield instance
    destroy_all_widgets(instance)


@pytest.fixture
def card(app):
    from arw_selector.gui.score_card import ScoreCard

    widget = ScoreCard()
    yield widget
    destroy_widget(widget, app)


def _record(score=58.3, grade=Grade.REJECT, **focus_kwargs) -> ImageRecord:
    fields = dict(
        sharpness=44.0, laplacian=44.0, tenengrad=44.0, frame_sharpness=60.0,
        source=FocusSource.EYE, face_count=2, face_area_ratio=0.05,
        face_confidence=0.99, mean_luma=120.0,
    )
    fields.update(focus_kwargs)
    record = ImageRecord(path=Path("239A0249.CR3"), focus=FocusResult(**fields))
    record.score = score
    record.grade = grade
    return record


def _texts(card) -> list[str]:
    return [
        card._grid.itemAt(i).widget().text()
        for i in range(card._grid.count())
        if isinstance(card._grid.itemAt(i).widget(), QLabel)
    ]


def test_hidden_without_a_selection(card):
    card.show_record(None)
    assert not card.isVisible()


def test_shows_the_name_grade_and_score(card):
    card.show_record(_record())
    assert "239A0249.CR3" in card.title.text()
    assert "reject" in card.title.text()
    assert "58.3" in card.title.text()


def _label(key: str) -> str:
    """지금 로케일에서 그 키가 어떤 문구로 나오는지.

    문구를 직접 적으면 번역을 바꿀 때마다 테스트가 깨집니다. 화면에 무엇이
    나오는지는 여전히 검사하되, 기준을 키로 잡습니다.
    """
    from arw_selector.gui.score_card import _label_for

    return _label_for(key)


def test_lists_the_total(card):
    from arw_selector.gui.i18n import tr

    card.show_record(_record())
    assert tr("Total") in _texts(card)


def test_reports_the_eye_state(card):
    from arw_selector.core import scoring

    for eyes_open, key in ((0.10, scoring.LINE_EYES_CLOSED),
                           (0.40, scoring.LINE_EYES_OPEN),
                           (-1.0, scoring.LINE_EYES_UNKNOWN)):
        card.show_record(_record(eyes_open=eyes_open))
        assert _label(key) in _texts(card)


def test_rows_are_cleared_between_records(card):
    """지우지 않으면 이전 컷의 항목이 남아 두 컷이 섞여 보입니다."""
    from arw_selector.core import scoring

    closed = _label(scoring.LINE_EYES_CLOSED)
    card.show_record(_record(eyes_open=0.10))
    assert closed in _texts(card)
    card.show_record(_record(eyes_open=0.40))
    assert closed not in _texts(card)


def test_total_matches_the_breakdown_shown(card):
    """화면의 항목을 더한 값이 화면의 합계와 같아야 합니다."""
    from arw_selector.core.scoring import score_breakdown

    record = _record(eyes_open=0.10, face_area_ratio=0.002)
    card.show_record(record)
    lines, total = score_breakdown(record)

    shown = _texts(card)
    assert f"{total:+.1f}" in shown
    for line in lines:
        assert _label(line.key) in shown


def test_every_key_has_a_label(card):
    """키를 추가하고 문구를 안 넣으면 화면에 'face_detected'가 그대로 뜹니다."""
    from arw_selector.core import scoring

    keys = [
        value for name, value in vars(scoring).items()
        if name.startswith("LINE_") and isinstance(value, str)
    ]
    assert keys, "키 상수를 못 찾았습니다"
    for key in keys:
        label = _label(key)
        assert label != key, f"{key}에 표시 문구가 없습니다"
        assert label.isascii(), f"{key}의 기본 문구가 영어가 아닙니다: {label}"


def test_config_changes_are_reflected(card):
    """기준을 만지면 채점표도 같이 움직여야 합니다."""
    record = _record(face_area_ratio=0.003)
    card.show_record(record, ScoreConfig(face_bonus_full_area=0.003))
    generous = _texts(card)
    card.show_record(record, ScoreConfig(face_bonus_full_area=0.30))
    strict = _texts(card)
    assert generous != strict


def test_failed_record_does_not_crash(card):
    record = ImageRecord(path=Path("x.ARW"), error="PreviewError")
    card.show_record(record)
    assert card.isVisible()
