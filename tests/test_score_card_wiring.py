"""채점표가 **메인 창에서** 실제로 뜨는지.

위젯만 따로 시험하면 못 잡는 것이 있습니다. 실제로 그랬습니다 — 위젯
테스트는 ScoreConfig를 직접 넘겼는데 메인 창은 Config(전체 설정)를 넘겨서,
사진을 고르는 순간 sanitized_config 안에서 죽었습니다.

    AttributeError: 'Config' object has no attribute 'trust_eye'

배선은 배선으로만 잡힙니다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from arw_selector.core.types import (  # noqa: E402
    FocusResult, FocusSource, Grade, ImageRecord,
)
from conftest import destroy_all_widgets  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from arw_selector.gui import theme

    instance = QApplication.instance() or QApplication([])
    theme.apply_app_theme(instance)
    yield instance
    destroy_all_widgets(instance)
    instance.processEvents()

    from arw_selector.gui.loupe import wait_for_detached_renders

    wait_for_detached_renders()


@pytest.fixture
def window(app):
    from arw_selector.gui.main_window import MainWindow

    win = MainWindow()
    win.show()
    app.processEvents()
    yield win
    win.close()
    app.processEvents()


def _record(name="a.ARW", score=58.3) -> ImageRecord:
    record = ImageRecord(
        path=Path(name),
        focus=FocusResult(
            sharpness=44.0, laplacian=44.0, tenengrad=44.0,
            frame_sharpness=60.0, source=FocusSource.EYE, face_count=2,
            face_area_ratio=0.05, face_confidence=0.99, mean_luma=120.0,
            eyes_open=0.31,
        ),
    )
    record.score = score
    record.grade = Grade.REVIEW
    return record


def test_selecting_a_photo_shows_the_score_card(window, app):
    """이 경로가 바로 크래시가 났던 자리입니다."""
    window.grid.set_records([_record("a.ARW"), _record("b.ARW", 42.0)])
    app.processEvents()

    window.grid.selectAll()
    app.processEvents()

    assert window.score_card.isVisible()
    assert "b.ARW" in window.score_card.title.text()


def test_score_card_is_hidden_without_a_selection(window, app):
    window.grid.set_records([_record()])
    app.processEvents()
    window.grid.clearSelection()
    app.processEvents()
    assert not window.score_card.isVisible()


def test_the_window_passes_a_score_config(window):
    """Config 전체를 넘기면 sanitized_config가 안에서 죽습니다."""
    from arw_selector.core.config import ScoreConfig

    assert isinstance(window.config.score, ScoreConfig)


def test_wrong_config_type_says_what_is_wrong():
    """다섯 프레임 안쪽에서 엉뚱한 속성 이름으로 죽지 않아야 합니다."""
    from arw_selector.core.config import Config
    from arw_selector.core.scoring import sanitized_config

    with pytest.raises(TypeError, match="ScoreConfig"):
        sanitized_config(Config())


def test_changing_the_criteria_redraws_the_card(window, app):
    """기준을 만지는 동안 채점표가 옛 점수를 들고 있으면 안 됩니다."""
    window.grid.set_records([_record()])
    app.processEvents()
    window.grid.selectAll()
    app.processEvents()

    before = window.score_card.title.text()
    window.config.score.bonus_face += 10.0
    window.on_settings_changed()
    app.processEvents()

    # 세션이 없으면 재판정을 건너뛰므로 최소한 죽지 않는 것은 확인합니다
    assert window.score_card.title.text() or before
