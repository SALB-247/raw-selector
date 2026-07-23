"""눈 감김 감점 (#109).

임계값과 감점 크기는 **사용자가 라벨한 실사진 107장**(감음 28 / 뜸 79)에서
정했습니다. 임계 0.20에서 뜬 눈을 한 장도 깎지 않으면서 감은 컷 8장을
걸러냅니다.

여기서 잠그는 것은 "모르는 것을 나쁘게 취급하지 않는다"입니다 — 눈을 못 잰
컷(-1)까지 감점하면 멀리 찍은 원경 컷이 통째로 밀려납니다.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from arw_selector.core import focus as focus_mod
from arw_selector.core.config import ScoreConfig
from arw_selector.core.scoring import _eyes_closed, compute_score
from arw_selector.core.types import FocusResult, FocusSource, Grade, ImageRecord


def _focus(eyes_open: float, **kwargs) -> FocusResult:
    base = dict(sharpness=60.0, laplacian=60.0, tenengrad=60.0,
                frame_sharpness=60.0, source=FocusSource.EYE,
                roi=(0, 0, 100, 100), face_count=1, face_confidence=0.99,
                eyes_open=eyes_open)
    base.update(kwargs)
    return FocusResult(**base)


def _record(eyes_open: float) -> ImageRecord:
    record = ImageRecord(path=Path("x.ARW"), focus=_focus(eyes_open))
    record.grade = Grade.KEEP
    return record


# ------------------------------------------------------- 판정


def test_closed_eyes_are_penalised():
    config = ScoreConfig()
    assert _eyes_closed(_focus(0.10), config)


def test_open_eyes_are_not_penalised():
    config = ScoreConfig()
    assert not _eyes_closed(_focus(0.35), config)


def test_unmeasured_is_not_penalised():
    """못 잰 것과 감은 것은 다릅니다.

    -1은 얼굴이 없거나 눈이 너무 작아 못 잰 경우입니다. 이걸 감점하면
    원경·풍경 컷이 통째로 밀려납니다.
    """
    config = ScoreConfig()
    assert not _eyes_closed(_focus(-1.0), config)


def test_exactly_at_the_threshold_is_open():
    """경계값은 '뜸' 쪽입니다 — 애매하면 깎지 않는 쪽으로 기웁니다."""
    config = ScoreConfig()
    assert not _eyes_closed(_focus(config.eyes_closed_below), config)


def test_penalty_can_be_turned_off():
    config = ScoreConfig(penalty_eyes_closed=0.0)
    assert not _eyes_closed(_focus(0.05), config)


# ------------------------------------------------------- 점수


def test_open_and_closed_differ_by_bonus_plus_penalty():
    """뜨면 +, 감으면 −. 둘의 간격은 두 설정값의 합입니다."""
    config = ScoreConfig(penalty_eyes_closed=10.0, bonus_eyes_open=6.0)
    open_score = compute_score(_record(0.35), config)
    closed_score = compute_score(_record(0.10), config)
    assert open_score - closed_score == pytest.approx(16.0, abs=0.01)


def test_open_eyes_are_rewarded():
    """감점만 있으면 '떴다'와 '못 쟀다'가 점수에서 같아집니다."""
    config = ScoreConfig(bonus_eyes_open=8.0)
    assert compute_score(_record(0.35), config) - compute_score(
        _record(-1.0), config) == pytest.approx(8.0, abs=0.01)


def test_unmeasured_gets_neither_bonus_nor_penalty():
    """모르는 것을 좋게도 나쁘게도 보지 않습니다.

    보너스를 주면 옆얼굴이라 못 잰 원경이 정면 인물과 같은 대우를 받고,
    감점을 주면 멀쩡한 원경이 통째로 밀려납니다.
    """
    config = ScoreConfig(bonus_eyes_open=8.0, penalty_eyes_closed=10.0)
    neutral = compute_score(_record(-1.0), config)
    without_either = compute_score(
        _record(-1.0),
        ScoreConfig(bonus_eyes_open=0.0, penalty_eyes_closed=0.0))
    assert neutral == pytest.approx(without_either)


def test_reason_is_shown():
    from arw_selector.core.scoring import grade_records

    records = [_record(0.10), _record(0.35)]
    for index, record in enumerate(records):
        record.path = Path(f"{index}.ARW")
        record.group_id = index
    grade_records(records, ScoreConfig())

    from arw_selector.core.scoring import REASON_EYES_CLOSED

    assert any(r.key == REASON_EYES_CLOSED for r in records[0].reasons)
    assert not any(r.key == REASON_EYES_CLOSED for r in records[1].reasons)


# ------------------------------------------------------- 측정


def test_measurement_returns_minus_one_without_a_face():
    """랜드마크를 못 얻으면 -1이어야 합니다 — 0을 돌려주면 감점됩니다."""
    image = np.zeros((200, 200, 3), np.uint8)
    assert focus_mod._measure_eye_opening(image, (10, 10, 30, 30)) == -1.0


def test_measurement_survives_a_broken_box():
    """분석 중 눈을 못 재도 전체가 멈추면 안 됩니다."""
    image = np.zeros((100, 100, 3), np.uint8)
    for box in ((0, 0, 0, 0), (-50, -50, 10, 10), (95, 95, 400, 400)):
        assert focus_mod._measure_eye_opening(image, box) == -1.0


def test_analyze_focus_fills_the_field():
    """얼굴이 없는 사진에서도 필드는 있어야 합니다(값은 -1)."""
    rng = np.random.default_rng(3)
    image = rng.integers(0, 255, (300, 400, 3), dtype=np.uint8)
    result = focus_mod.analyze_focus(image)
    assert hasattr(result, "eyes_open")
    assert result.eyes_open == -1.0 or 0.0 <= result.eyes_open <= 2.0


# ------------------------------------------------------- 설정 근거


def test_default_threshold_is_where_raising_it_stops_helping():
    """사용자 라벨 107장(감음 28 / 뜸 79) 재실측 — 잡아냄 / 거짓감점:

        0.28 — 24/28 · 16/79
        0.30 — 25/28 · 19/79   ← 기본값
        0.32 — 25/28 · 26/79
        0.35 — 26/28 · 40/79

    0.30 위로는 잡는 수가 늘지 않고 거짓감점만 늡니다. 0.32는 0.30과
    똑같이 25장을 잡으면서 멀쩡한 컷 7장을 더 깎습니다.

    바꾸려면 사용자에게 확인하십시오.
    """
    assert ScoreConfig().eyes_closed_below == pytest.approx(0.30)


def test_default_bonus_and_penalty_are_both_set():
    """한쪽만 켜져 있으면 '떴다'와 '못 쟀다'가 구분되지 않습니다."""
    config = ScoreConfig()
    assert config.bonus_eyes_open == 10.0
    assert config.penalty_eyes_closed == 20.0


def test_default_penalty_pushes_out_of_keep_but_not_into_reject():
    """reject로 떨어뜨리는 것이 아니라 자동 keep에서 밀어내는 크기여야 합니다.

    수치를 직접 박아 두면 keep/reject 기준이 바뀔 때 같이 안 움직입니다.
    관계로 적습니다.
    """
    config = ScoreConfig()
    assert config.penalty_eyes_closed > 0
    # keep 턱걸이 컷은 자동 keep에서 밀려나야 합니다
    assert config.keep_above - config.penalty_eyes_closed < config.keep_above
    # 그렇다고 reject까지 떨어지면 눈 감은 것만으로 버려집니다
    assert config.keep_above - config.penalty_eyes_closed > config.reject_below
