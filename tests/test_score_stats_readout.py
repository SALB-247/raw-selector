"""목표 keep 비율 모드에서 점수 분포를 보여 주는지.

비율만 적어 놓으면 "10%면 몇 점에서 잘리는지"를 알 수 없습니다. 사용자가
평균/최소/최대를 같이 보고 싶다고 해서 넣었습니다.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from arw_selector.core.config import Config  # noqa: E402
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
    """close()는 숨기기일 뿐입니다. 매번 확실히 파괴합니다."""
    from arw_selector.gui.settings_panel import SettingsPanel

    widget = SettingsPanel(Config())
    yield widget
    destroy_widget(widget, app)


def test_hidden_when_absolute_threshold_mode(panel):
    """절대 점수 모드에서는 잘리는 점수라는 개념이 없습니다."""
    panel.use_ratio.setChecked(False)
    panel.set_score_stats([10.0, 50.0, 90.0])
    assert panel.score_stats.text() == ""


def test_prompts_before_analysis(panel):
    """분석 전에는 빈 칸이 아니라 왜 비었는지 알려 줘야 합니다.

    문구를 직접 적으면 번역이나 표현을 고칠 때마다 깨집니다. 비어 있지
    않은지만 봅니다 — 이 테스트가 지키려는 것이 그것입니다.
    """
    panel.use_ratio.setChecked(True)
    panel.set_score_stats([])
    assert panel.score_stats.text().strip()


def test_shows_count_min_mean_max(panel):
    panel.use_ratio.setChecked(True)
    panel.set_score_stats([20.0, 40.0, 60.0, 80.0])
    text = panel.score_stats.text()

    assert "4" in text     # 장수
    assert "20.0" in text  # 최소
    assert "50.0" in text  # 평균
    assert "80.0" in text  # 최대


def _cutoff(panel) -> float:
    """화면에 적힌 컷오프 점수. 문구가 아니라 숫자를 읽습니다."""
    import re

    numbers = re.findall(r"\d+\.\d+", panel.score_stats.text())
    assert numbers, panel.score_stats.text()
    return float(numbers[-1])  # 마지막 줄의 컷오프


def test_cutoff_follows_the_ratio(panel):
    """상위 25%를 남기라고 하면 상위 25% 경계 점수가 나와야 합니다.

    4장의 25%는 1장이고, 그 1장은 40점짜리입니다. 백분위로 계산하면
    30점이 나오는데 그러면 두 장이 남습니다 — 실제 컷과 어긋납니다.
    """
    panel.use_ratio.setChecked(True)
    panel.set_score_stats([10.0, 20.0, 30.0, 40.0])

    panel.target_ratio.setValue(25.0)
    assert _cutoff(panel) == 40.0

    panel.target_ratio.setValue(50.0)
    assert _cutoff(panel) == 30.0

    panel.target_ratio.setValue(100.0)
    assert _cutoff(panel) == 10.0


def test_cutoff_keeps_the_promised_number_of_photos(panel):
    """표시한 점수 이상인 장수가 목표 장수와 같아야 합니다.

    grade_records도 round(장수 × 비율)로 목표 장수를 잡습니다. 화면이
    다른 셈법을 쓰면 "10%로 뒀는데 왜 이만큼 남았나"가 설명이 안 됩니다.
    """
    scores = [float(i) for i in range(1, 41)]
    panel.use_ratio.setChecked(True)
    panel.set_score_stats(scores)

    for percent in (5.0, 10.0, 25.0, 50.0, 90.0):
        panel.target_ratio.setValue(percent)
        cutoff = _cutoff(panel)
        kept = sum(1 for s in scores if s >= cutoff)
        assert kept == round(len(scores) * percent / 100.0), (
            f"{percent}% → {cutoff}점, {kept}장 남음")


def test_ratio_change_refreshes_without_reanalysis(panel):
    """비율 슬라이더를 돌리는 동안 바로 반응해야 합니다."""
    panel.use_ratio.setChecked(True)
    panel.set_score_stats([10.0, 20.0, 30.0, 40.0])

    panel.target_ratio.setValue(25.0)
    first = panel.score_stats.text()
    panel.target_ratio.setValue(75.0)
    assert panel.score_stats.text() != first


def test_toggling_the_mode_refreshes(panel):
    """체크만 켜도 이미 들고 있는 점수로 채워져야 합니다."""
    panel.use_ratio.setChecked(False)
    panel.set_score_stats([10.0, 20.0, 30.0])
    panel.use_ratio.setChecked(True)
    assert "3" in panel.score_stats.text()


def test_single_photo_does_not_crash(panel):
    """한 장만 분석한 경우에도 인덱스가 나가면 안 됩니다."""
    panel.use_ratio.setChecked(True)
    panel.set_score_stats([42.0])
    assert "42.0" in panel.score_stats.text()


def test_none_is_treated_as_empty(panel):
    """분석 결과를 지우면 None이 들어옵니다. 빈 목록과 같아야 합니다."""
    panel.use_ratio.setChecked(True)
    panel.set_score_stats([])
    empty = panel.score_stats.text()
    panel.set_score_stats(None)
    assert panel.score_stats.text() == empty
    assert empty.strip()
