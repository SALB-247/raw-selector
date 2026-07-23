"""판정 기준 UI가 ScoreConfig를 다 담고 있는지.

판정 기준을 새로 추가할 때마다 UI에 넣는 걸 잊습니다. 실제로 네 개가
빠져 있었습니다 — `bonus_focus_on_face`, `penalty_no_face`,
`penalty_eyes_closed`, `eyes_closed_below`. 화면에 없으면 사용자는 그
기준이 존재하는지조차 모르고, 왜 이런 점수가 나오는지 설명할 방법도
없습니다.

**여기서 실패하면 UI에 위젯을 추가하십시오.** 이 목록을 늘려서 통과시키면
안 됩니다.
"""

from __future__ import annotations

import os
from dataclasses import fields, replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from arw_selector.core.config import Config, GroupConfig, ScoreConfig  # noqa: E402
from conftest import destroy_all_widgets, destroy_widget  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from arw_selector.gui import theme

    instance = QApplication.instance() or QApplication([])
    theme.apply_app_theme(instance)
    yield instance
    destroy_all_widgets(instance)


@pytest.fixture
def panel(app, tmp_path):
    """테스트마다 패널을 만들고 **확실히 파괴합니다.**

    `close()`만 부르면 위젯은 숨겨질 뿐 파괴되지 않습니다. 부모가 없는
    패널이 테스트 수만큼(29개) 살아남아 모듈 스코프 app 픽스처 teardown에서
    한꺼번에 정리되는데, 그때 힙이 손상되어 파이썬이 죽었습니다
    (0xC0000374, pytest가 요약을 찍기도 전에).

    테스트 전부가 통과한 **뒤** 죽기 때문에 더 나쁩니다 — CI에서는 '통과'로
    보이는데 종료 코드만 비정상입니다.

    `deleteLater()` + `processEvents()`로 각 테스트가 끝날 때 그 자리에서
    파괴하면, 정리가 한곳에 몰리지 않아 문제가 드러나지 않습니다.
    """
    from arw_selector.gui.settings_panel import SettingsPanel

    widget = SettingsPanel(Config())
    yield widget
    destroy_widget(widget, app)


#: UI에 두지 않는 필드와 그 이유.
_INTENTIONALLY_HIDDEN: dict[str, str] = {}


def _numeric_fields(config_class) -> list[str]:
    return [f.name for f in fields(config_class)
            if f.type in ("float", "int", "bool")
            or isinstance(getattr(config_class(), f.name), (int, float, bool))]


# 위젯 **이름**으로 존재 여부를 세는 테스트는 두지 않습니다. 위젯 이름과
# 필드 이름이 다른 경우가 많아(max_clipped_highlights ↔ max_highlight)
# 예외 목록을 계속 늘리게 되고, 그 목록이 곧 구멍이 됩니다.
#
# 아래 왕복 테스트가 더 강한 보증입니다 — 위젯이 아예 없으면 값이 넘어오지
# 않아 실패하고, 위젯만 있고 apply_to_config에 연결이 빠져도 실패합니다.


@pytest.mark.parametrize("field_name", _numeric_fields(ScoreConfig))
def test_score_field_round_trips(panel, field_name):
    """값을 넣고 다시 읽었을 때 그대로 나와야 합니다.

    위젯만 놓고 apply_to_config에 연결을 빠뜨리면, 사용자가 값을 바꿔도
    아무 일도 일어나지 않습니다 — 화면에 있으니 더 헷갈립니다.
    """
    if field_name in _INTENTIONALLY_HIDDEN:
        pytest.skip(_INTENTIONALLY_HIDDEN[field_name])

    default = getattr(ScoreConfig(), field_name)
    if isinstance(default, bool):
        changed = not default
    elif isinstance(default, int) and not isinstance(default, bool):
        changed = int(default) + 1
    else:
        # 범위 안에서 확실히 다른 값. 임계값류는 0~1이라 작게 움직입니다
        changed = default + 0.03 if default <= 1.0 else default + 3.0

    # load_from_config는 인자를 받지 않고 panel.config를 읽습니다
    panel.config = Config(score=replace(ScoreConfig(), **{field_name: changed}),
                          group=GroupConfig())
    panel.load_from_config()

    # 위젯 → 새 config. 같은 객체를 쓰면 원래 값이 남아 있어도 통과합니다.
    panel.config = Config(score=ScoreConfig(), group=GroupConfig())
    panel.apply_to_config()
    actual = getattr(panel.config.score, field_name)

    assert actual == pytest.approx(changed, abs=1e-6), (
        f"{field_name}: {changed} 를 넣었는데 {actual} 이 나왔습니다 "
        "— 위젯이 apply_to_config에 연결되지 않았습니다")


def test_formula_label_matches_the_real_calculation(panel):
    """화면에 적힌 공식이 실제 계산과 같아야 합니다.

    실제로 어긋나 있었습니다 — 선명도 항에 0.5를 곱하게 바꾸고 라벨을 안
    고쳐서, 사용자가 keep 절대 점수를 왜 35로 두는지 알 수 없었습니다.

    라벨에 숫자를 직접 적으면 척도를 바꿀 때 또 어긋납니다. 여기서 두 값이
    같은지 봅니다.
    """
    import re

    from PySide6.QtWidgets import QLabel

    from arw_selector.core.scoring import SHARPNESS_SCALE

    # 문구로 라벨을 찾지 않습니다 — 번역되면 못 찾습니다. 위젯을 직접 씁니다.
    formula = panel.formula.text()

    numbers = [float(n) for n in re.findall(r"×\s*([0-9]*\.?[0-9]+)", formula)]
    assert numbers, f"공식에 배율이 없습니다: {formula!r}"
    assert any(abs(n - SHARPNESS_SCALE) < 1e-9 for n in numbers), (
        f"라벨의 배율 {numbers} 이 실제 SHARPNESS_SCALE({SHARPNESS_SCALE})와 "
        f"다릅니다: {formula!r}")


def test_formula_label_keeps_the_bonus_and_penalty_terms(panel):
    """배율만 맞추다 보너스·감점 항을 빠뜨리면 공식이 또 거짓말이 됩니다.

    현재 로케일의 문구로 확인합니다. 영어 단어를 박아 두면 한국어 화면에서
    검사가 통과하지 못하고, 한국어를 박아 두면 그 반대가 됩니다.
    """
    from arw_selector.core.scoring import SHARPNESS_SCALE
    from arw_selector.gui.i18n import tr

    formula = panel.formula.text()
    expected = tr("score = (ROI sharpness × trust\n"
                  "     + frame sharpness × (1 − trust))\n")
    assert formula.startswith(expected), f"공식 본문이 다릅니다: {formula!r}"

    tail = tr("     × {scale:g} + bonuses − penalties").format(
        scale=SHARPNESS_SCALE)
    assert formula.endswith(tail), f"보너스·감점 항이 빠졌습니다: {formula!r}"


def test_eye_fields_are_present(panel):
    """이번에 넣은 눈 감김 항목이 실제로 화면에 있는지."""
    assert hasattr(panel, "penalty_eyes_closed")
    assert hasattr(panel, "eyes_closed_below")
    assert panel.eyes_closed_below.minimum() <= 0.20 <= panel.eyes_closed_below.maximum()


def test_face_priority_only_fields_follow_the_toggle(panel):
    """얼굴 우선 모드에서만 쓰이는 값은 모드를 끄면 만질 수 없어야 합니다.

    눈 감김도 여기 들어갑니다. 예전에는 모드와 무관하게 점수에 반영돼서
    이 테스트도 "꺼도 살아 있어야 한다"였습니다. 지금은 얼굴·눈 신호가
    모드 안으로 전부 들어갔습니다 — 배수가 모드에 따라 달라지므로,
    얼굴 보너스가 모드 밖에서 살아 있으면 배수 1.0과 겹쳐 점수가 100에
    붙습니다(실측 42장).
    """
    panel.face_priority.setChecked(True)
    assert panel.penalty_no_face.isEnabled()
    assert panel.bonus_focus_on_face.isEnabled()
    assert panel.penalty_eyes_closed.isEnabled()

    panel.face_priority.setChecked(False)
    assert not panel.penalty_no_face.isEnabled()
    assert not panel.bonus_focus_on_face.isEnabled()
    assert not panel.penalty_eyes_closed.isEnabled()
