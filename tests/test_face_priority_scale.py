"""얼굴 우선 모드를 끄면 배수와 얼굴 항목이 **같이** 움직이는지.

두 가지가 한 문제였습니다.

  - 모드를 끄면 얼굴·눈 신호가 빠지는데 배수가 0.5 그대로라 점수의 상단
    절반이 통째로 비었습니다. 실측(A6700 2845장) 최대 45.1점 —
    keep 기준 65에 닿는 컷이 하나도 없습니다.
  - 그렇다고 배수만 1.0으로 올리면 얼굴 보너스가 그대로 붙어 42장이
    100점에 붙습니다. ×0.5를 도입한 이유가 그대로 되돌아옵니다.

그래서 배수와 얼굴 항목은 반드시 함께 켜지고 함께 꺼져야 합니다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arw_selector.core import scoring
from arw_selector.core.config import ScoreConfig
from arw_selector.core.types import FocusResult, FocusSource, ImageRecord


def _record(**focus_kwargs) -> ImageRecord:
    fields = dict(
        sharpness=80.0, laplacian=80.0, tenengrad=80.0, frame_sharpness=80.0,
        source=FocusSource.EYE, face_count=2, face_area_ratio=0.05,
        face_confidence=0.99, mean_luma=120.0, eyes_open=0.40,
    )
    fields.update(focus_kwargs)
    return ImageRecord(path=Path("x.ARW"), focus=FocusResult(**fields))


# ------------------------------------------------------- 배수


def test_scale_follows_the_mode():
    assert scoring.sharpness_scale(ScoreConfig(face_priority=True)) == \
        scoring.SHARPNESS_SCALE
    assert scoring.sharpness_scale(ScoreConfig(face_priority=False)) == \
        scoring.SHARPNESS_SCALE_NO_FACE


def test_no_face_mode_uses_the_full_range():
    """상단 절반이 비면 절대 점수 기준이 무의미해집니다."""
    assert scoring.SHARPNESS_SCALE_NO_FACE > scoring.SHARPNESS_SCALE
    assert scoring.SHARPNESS_SCALE_NO_FACE == 1.0


def test_sharp_landscape_can_reach_the_keep_threshold():
    """얼굴 없는 선명한 컷이 keep 기준에 닿을 수 있어야 합니다."""
    config = ScoreConfig(face_priority=False)
    record = _record(sharpness=90.0, frame_sharpness=90.0, face_count=0,
                     source=FocusSource.FRAME, eyes_open=-1.0)
    assert scoring.compute_score(record, config) >= config.keep_above


def test_the_same_photo_cannot_reach_it_with_the_old_scale():
    """예전 동작을 그대로 두면 왜 안 되는지 못박아 둡니다."""
    config = ScoreConfig(face_priority=False)
    record = _record(sharpness=100.0, frame_sharpness=100.0, face_count=0,
                     source=FocusSource.FRAME, eyes_open=-1.0)
    old = scoring.SHARPNESS_SCALE * 100.0
    assert old < config.keep_above


# ------------------------------------------------------- 얼굴 항목 차단


#: 얼굴 우선 모드 안에서만 나와야 하는 채점표 항목들.
FACE_ONLY_KEYS = (
    scoring.LINE_FACE_DETECTED, scoring.LINE_EYE_DETECTED,
    scoring.LINE_FACE_SIZE, scoring.LINE_FOCUS_ON_FACE,
    scoring.LINE_NO_FACE, scoring.LINE_FACE_DEFOCUS,
    *scoring.EYE_STATE_KEYS,
)


@pytest.mark.parametrize("key", FACE_ONLY_KEYS)
def test_face_lines_are_absent_without_the_mode(key):
    """모드를 끄면 얼굴·눈 항목이 채점표에서 아예 사라져야 합니다."""
    config = ScoreConfig(face_priority=False, bonus_face_size=5.0)
    for kwargs in ({}, {"eyes_open": 0.10}, {"eyes_open": -1.0},
                   {"face_count": 0, "source": FocusSource.FRAME},
                   {"background_sharpness": 99.0}):
        lines, _ = scoring.score_breakdown(_record(**kwargs), config)
        assert all(line.key != key for line in lines), (
            f"{kwargs}에서 '{key}'가 남아 있습니다")


def test_faces_add_nothing_without_the_mode():
    """배수 1.0에 얼굴 보너스가 남으면 실측에서 42장이 100점에 붙습니다.

    같은 선명도라면 얼굴이 있든 없든 점수가 같아야 합니다.
    """
    config = ScoreConfig(face_priority=False)
    with_faces = _record(face_count=3, face_area_ratio=0.2, eyes_open=0.40)
    without = _record(face_count=0, face_area_ratio=0.0, eyes_open=-1.0)
    assert scoring.compute_score(with_faces, config) == pytest.approx(
        scoring.compute_score(without, config))


def test_score_is_purely_sharpness_without_the_mode():
    """선명도 항만 남으므로 배수를 곱한 값과 정확히 같아야 합니다."""
    config = ScoreConfig(face_priority=False, trust_eye=0.75)
    record = _record(sharpness=60.0, frame_sharpness=40.0)
    expected = scoring.SHARPNESS_SCALE_NO_FACE * (0.75 * 60.0 + 0.25 * 40.0)
    assert scoring.compute_score(record, config) == pytest.approx(expected)


def test_eye_state_does_not_change_the_score_without_the_mode():
    config = ScoreConfig(face_priority=False)
    opened = scoring.compute_score(_record(eyes_open=0.40), config)
    closed = scoring.compute_score(_record(eyes_open=0.10), config)
    assert opened == pytest.approx(closed)


def test_face_lines_are_present_with_the_mode():
    """반대로 켜면 다 나와야 합니다 — 차단이 과하지 않은지 확인."""
    lines, _ = scoring.score_breakdown(
        _record(), ScoreConfig(face_priority=True, bonus_face_size=5.0))
    keys = {line.key for line in lines}
    assert {scoring.LINE_FACE_DETECTED, scoring.LINE_EYE_DETECTED,
            scoring.LINE_FACE_SIZE, scoring.LINE_EYES_OPEN} <= keys


def test_reasons_omit_the_eye_state_without_the_mode():
    """점수에 안 쓰이는 값을 근거에 적으면 그걸 보고 임계를 맞추게 됩니다."""
    eye_keys = {scoring.REASON_EYES_CLOSED, scoring.REASON_EYES_OPEN,
                scoring.REASON_EYES_UNKNOWN}
    record = _record(eyes_open=0.10)
    record.score = scoring.compute_score(record)

    off = scoring._reasons(
        record, ScoreConfig(face_priority=False), threshold=0.0)
    assert not any(r.key in eye_keys for r in off)

    on = scoring._reasons(
        record, ScoreConfig(face_priority=True), threshold=0.0)
    assert any(r.key == scoring.REASON_EYES_CLOSED for r in on)


def test_breakdown_still_adds_up_without_the_mode():
    config = ScoreConfig(face_priority=False)
    for kwargs in ({}, {"eyes_open": 0.10}, {"face_count": 0},
                   {"mean_luma": 2.0}):
        lines, total = scoring.score_breakdown(_record(**kwargs), config)
        assert sum(line.value for line in lines) == pytest.approx(total, abs=1e-6)
