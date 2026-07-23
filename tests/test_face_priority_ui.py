"""얼굴 우선 모드를 끄면 그 모드 전용 입력이 잠기는지.

살아 있는 위젯을 보면 사용자는 그 값이 쓰인다고 믿습니다. 실제로는 점수에
반영되지 않으니 "값을 바꿨는데 아무 일도 안 일어난다"가 됩니다 — ROI
신뢰도에서 이미 한 번 겪은 일입니다.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from arw_selector.core.config import Config  # noqa: E402
from arw_selector.core.scoring import (  # noqa: E402
    SHARPNESS_SCALE, SHARPNESS_SCALE_NO_FACE,
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
def panel(app):
    from arw_selector.gui.settings_panel import SettingsPanel

    widget = SettingsPanel(Config())
    yield widget
    destroy_widget(widget, app)


def test_face_only_widgets_are_locked_when_off(panel):
    panel.face_priority.setChecked(False)
    for widget in panel._face_only_widgets():
        assert not widget.isEnabled()


def test_face_only_widgets_are_live_when_on(panel):
    panel.face_priority.setChecked(False)
    panel.face_priority.setChecked(True)
    for widget in panel._face_only_widgets():
        assert widget.isEnabled()


def test_the_locked_set_covers_the_eye_and_bonus_inputs(panel):
    """목록을 만들어 두면 위젯을 추가할 때 여기 넣는 것을 잊습니다.

    점수에서 얼굴 우선 모드에 묶인 것들이 전부 들어 있어야 합니다.
    """
    locked = set(panel._face_only_widgets())
    for widget in (panel.penalty_face_defocus, panel.bonus_focus_on_face,
                   panel.penalty_no_face, panel.bonus_eyes_open,
                   panel.penalty_eyes_closed, panel.eyes_closed_below,
                   panel.face_bonus_full_area,
                   *panel.bonus_spins.values()):
        assert widget in locked


def test_trust_stays_live_when_off(panel):
    """신뢰도는 모드와 무관하게 쓰입니다 — 잠그면 안 됩니다."""
    panel.face_priority.setChecked(False)
    for spin in panel.trust_spins.values():
        assert spin.isEnabled()


def test_exposure_penalties_stay_live_when_off(panel):
    """하이라이트·섀도우 감점은 얼굴과 무관합니다."""
    panel.face_priority.setChecked(False)
    for spin in panel.penalty_spins.values():
        assert spin.isEnabled()


def test_formula_shows_the_scale_actually_in_use(panel):
    panel.face_priority.setChecked(True)
    assert f"× {SHARPNESS_SCALE:g}" in panel.formula.text()

    panel.face_priority.setChecked(False)
    assert f"× {SHARPNESS_SCALE_NO_FACE:g}" in panel.formula.text()

    # 배수만 맞고 "왜 달라졌는지"가 없으면 사용자는 이유를 모릅니다.
    # 문구는 로케일에 맞춰 가져옵니다.
    from arw_selector.gui.i18n import tr

    tail = tr("     × {scale:g}  (face priority off — no face or eye "
              "terms)").format(scale=SHARPNESS_SCALE_NO_FACE)
    assert panel.formula.text().endswith(tail)


def test_loading_a_config_applies_the_locked_state(panel):
    """토글을 거치지 않고 프리셋을 불러와도 상태가 맞아야 합니다.

    불러오기는 위젯 값만 채우고 잠금 상태는 토글 쪽에만 있던 적이 있어,
    얼굴 우선이 꺼진 프리셋을 열면 잠기지 않은 채로 떴습니다.
    """
    panel.config.score.face_priority = False
    panel.load_from_config()
    assert not panel.bonus_eyes_open.isEnabled()
    assert f"× {SHARPNESS_SCALE_NO_FACE:g}" in panel.formula.text()

    panel.config.score.face_priority = True
    panel.load_from_config()
    assert panel.bonus_eyes_open.isEnabled()
    assert f"× {SHARPNESS_SCALE:g}" in panel.formula.text()
