"""얼굴 크기 스핀박스가 쓸모 있는 구간에서 잘게 움직이는지.

망원 촬영은 주 피사체 얼굴이 0.1~0.6%에 다 들어갑니다. 0.5% 고정 간격이면
그 구간을 한 번에 건너뛰어 설정을 맞출 수가 없습니다.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from arw_selector.core.config import Config, FACE_BONUS_AREA_RANGE  # noqa: E402
from conftest import destroy_all_widgets, destroy_widget  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from arw_selector.gui import theme

    instance = QApplication.instance() or QApplication([])
    theme.apply_app_theme(instance)
    yield instance
    destroy_all_widgets(instance)


@pytest.fixture
def spin(app):
    from arw_selector.gui.settings_panel import SettingsPanel

    widget = SettingsPanel(Config())
    yield widget.face_bonus_full_area
    destroy_widget(widget, app)


def test_range_and_default(spin):
    low, high = FACE_BONUS_AREA_RANGE
    assert spin.minimum() == pytest.approx(low * 100.0)
    assert spin.maximum() == pytest.approx(high * 100.0)
    assert spin.value() == pytest.approx(3.0)


def test_steps_finely_in_the_small_range(spin):
    """0.1~0.4 구간은 0.1씩 움직여야 합니다."""
    spin.setValue(0.1)
    for expected in (0.2, 0.3, 0.4, 0.5):
        spin.stepBy(1)
        assert spin.value() == pytest.approx(expected, abs=0.001)


def test_steps_coarsely_above(spin):
    """3%에서 20%까지 0.1씩 가면 170번을 눌러야 합니다."""
    spin.setValue(3.0)
    spin.stepBy(1)
    assert spin.value() == pytest.approx(3.5, abs=0.001)


def test_stepping_down_from_the_boundary_stays_fine(spin):
    """0.5에서 내려갈 때 굵은 간격을 쓰면 0.0으로 떨어져 하한에 처박힙니다."""
    spin.setValue(0.5)
    spin.stepBy(-1)
    assert spin.value() == pytest.approx(0.4, abs=0.001)


def test_cannot_go_below_the_configured_floor(spin):
    spin.setValue(0.1)
    spin.stepBy(-1)
    assert spin.value() == pytest.approx(FACE_BONUS_AREA_RANGE[0] * 100.0)
