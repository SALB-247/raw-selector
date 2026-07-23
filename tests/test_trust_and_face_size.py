"""ROI 신뢰도가 실제로 먹는지, 얼굴 크기가 보너스에 반영되는지.

둘 다 실사용에서 나온 리포트입니다.
  - 신뢰도를 바꿔도 점수가 한 자리도 안 움직였습니다 (얼굴 우선 모드가
    눈 0.85 / 얼굴 0.75로 설정을 덮어쓰고 있었습니다)
  - 객석에 잡힌 작은 얼굴이 주 피사체 얼굴과 같은 보너스를 받았습니다
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arw_selector.core import scoring
from arw_selector.core.config import FACE_BONUS_AREA_RANGE, ScoreConfig
from arw_selector.core.types import FocusResult, FocusSource, ImageRecord


def _record(source=FocusSource.EYE, roi=30.0, frame=90.0,
            face_area=0.05, face_count=1) -> ImageRecord:
    return ImageRecord(
        path=Path("x.ARW"),
        focus=FocusResult(
            sharpness=roi, laplacian=roi, tenengrad=roi,
            frame_sharpness=frame, source=source,
            roi=(0, 0, 10, 10), face_count=face_count,
            face_confidence=0.99, face_area_ratio=face_area,
        ),
    )


# ------------------------------------------------------- ROI 신뢰도


@pytest.mark.parametrize("source", list(FocusSource))
def test_trust_setting_is_used_as_is(source):
    """설정한 신뢰도가 그대로 쓰여야 합니다.

    얼굴 우선 모드가 하한으로 덮어쓰면 눈 0.75 / 얼굴 0.60 같은 값이
    아무 반응 없이 무시됩니다.
    """
    for value in (0.0, 0.25, 0.6, 0.75, 1.0):
        config = ScoreConfig(trust_eye=value, trust_face=value,
                             trust_tile=value, trust_frame=value)
        actual = scoring._effective_trust(_record(source).focus, config)
        assert actual == pytest.approx(value), (
            f"{source}: 설정 {value} → 실제 {actual}")


@pytest.mark.parametrize("source", list(FocusSource))
def test_lower_trust_raises_the_score_when_the_frame_is_sharper(source):
    """ROI가 흐리고 전체가 선명하면, 신뢰도를 낮출수록 점수가 올라야 합니다."""
    record = _record(source, roi=30.0, frame=90.0)

    def score_at(value):
        return scoring.compute_score(record, ScoreConfig(
            trust_eye=value, trust_face=value,
            trust_tile=value, trust_frame=value))

    high, low = score_at(1.0), score_at(0.0)
    assert low > high, f"{source}: 신뢰도 0에서 {low}, 1에서 {high}"


def test_face_priority_no_longer_overrides_trust():
    """얼굴 우선 모드를 켜도 신뢰도는 설정값 그대로여야 합니다."""
    record = _record(FocusSource.EYE)
    for priority in (True, False):
        config = ScoreConfig(face_priority=priority, trust_eye=0.30)
        assert scoring._effective_trust(record.focus, config) == pytest.approx(0.30)


def test_trust_is_clamped_to_zero_one():
    """프리셋은 손으로 고칠 수 있어 범위 밖 값이 들어옵니다."""
    record = _record(FocusSource.EYE)
    assert scoring._effective_trust(record.focus, ScoreConfig(trust_eye=5.0)) == 1.0
    assert scoring._effective_trust(record.focus, ScoreConfig(trust_eye=-2.0)) == 0.0


# ------------------------------------------------------- 얼굴 크기 가중


def test_small_face_gets_less_bonus():
    """객석에 잡힌 작은 얼굴이 주 피사체와 같은 보너스를 받으면 안 됩니다."""
    config = ScoreConfig(bonus_face=15.0, bonus_eye=15.0, bonus_face_size=0.0)
    big = scoring.compute_score(_record(face_area=0.05), config)
    tiny = scoring.compute_score(_record(face_area=0.0002), config)
    assert tiny < big - 10.0, f"큰 얼굴 {big:.1f} / 작은 얼굴 {tiny:.1f}"


def test_normal_sized_face_keeps_the_full_bonus():
    """평범한 인물컷까지 깎이면 안 됩니다."""
    config = ScoreConfig()
    focus = _record(face_area=config.face_bonus_full_area).focus
    assert scoring._face_bonus_weight(focus, config) == pytest.approx(1.0)
    bigger = _record(face_area=0.6).focus
    assert scoring._face_bonus_weight(bigger, config) == pytest.approx(1.0)


def test_weight_uses_square_root_not_area():
    """면적을 그대로 쓰면 절반 크기 얼굴이 1/4로 떨어져 과하게 깎입니다."""
    config = ScoreConfig()
    half_area = config.face_bonus_full_area / 4.0  # 한 변이 절반
    weight = scoring._face_bonus_weight(_record(face_area=half_area).focus, config)
    assert weight == pytest.approx(0.5, abs=0.01)


def test_weight_is_never_negative():
    config = ScoreConfig()
    assert scoring._face_bonus_weight(_record(face_area=-1.0).focus, config) == 0.0


def test_eye_bonus_is_also_weighted():
    """눈 보너스를 안 걸면 객석 얼굴이 크기 가중을 우회합니다."""
    config = ScoreConfig(bonus_face=0.0, bonus_eye=20.0, bonus_face_size=0.0)
    big = scoring.compute_score(_record(FocusSource.EYE, face_area=0.05), config)
    tiny = scoring.compute_score(_record(FocusSource.EYE, face_area=0.0002), config)
    assert tiny < big - 10.0


# ------------------------------------------------------- 기준 크기 설정


def test_threshold_is_configurable():
    """기준을 올리면 그 아래 얼굴이 전부 덜 받아야 합니다."""
    focus = _record(face_area=0.05).focus
    loose = ScoreConfig(face_bonus_full_area=0.05)
    strict = ScoreConfig(face_bonus_full_area=0.20)

    assert scoring._face_bonus_weight(focus, loose) == pytest.approx(1.0)
    assert scoring._face_bonus_weight(focus, strict) == pytest.approx(0.5, abs=0.01)


def test_threshold_changes_the_score():
    """설정만 있고 점수에 반영되지 않으면 슬라이더가 죽은 것입니다."""
    record = _record(face_area=0.01)
    loose = scoring.compute_score(record, ScoreConfig(face_bonus_full_area=0.01))
    strict = scoring.compute_score(record, ScoreConfig(face_bonus_full_area=0.30))
    assert loose > strict + 5.0, f"완화 {loose:.1f} / 엄격 {strict:.1f}"


def test_tiny_faces_get_nothing_at_all():
    """제곱근 곡선은 0에 닿지 않아서, 끊어 주지 않으면 티끌도 받아 갑니다."""
    config = ScoreConfig(face_bonus_full_area=0.05)
    speck = _record(face_area=0.05 * 1e-4).focus  # 26MP에서 열몇 화소
    assert scoring._face_bonus_weight(speck, config) == 0.0


def test_default_threshold_is_three_percent():
    """사용자가 정한 값입니다. 바꾸려면 사용자에게 확인하십시오."""
    assert ScoreConfig().face_bonus_full_area == pytest.approx(0.03)


def test_range_reaches_the_sizes_that_actually_occur():
    """망원 촬영은 주 피사체 얼굴이 0.1~0.6%에 몰려 있습니다.

    하한이 그 위에 있으면 그런 촬영에서는 설정이 아무 의미가 없습니다.
    """
    low, high = FACE_BONUS_AREA_RANGE
    assert low <= 0.001
    assert high >= 0.20


def test_out_of_range_threshold_is_clamped():
    """YAML 프리셋은 손으로 고칠 수 있습니다.

    0이면 모든 얼굴이 온전한 보너스를 받아 크기 가중이 통째로 사라지고,
    1을 넘으면 어떤 얼굴도 기준에 못 미쳐 인물 보너스가 전부 죽습니다.
    """
    low, high = FACE_BONUS_AREA_RANGE

    assert scoring.sanitized_config(
        ScoreConfig(face_bonus_full_area=0.0)).face_bonus_full_area == low
    assert scoring.sanitized_config(
        ScoreConfig(face_bonus_full_area=-1.0)).face_bonus_full_area == low
    assert scoring.sanitized_config(
        ScoreConfig(face_bonus_full_area=5.0)).face_bonus_full_area == high


def test_nan_threshold_falls_back_to_default():
    """YAML의 .nan은 그대로 float으로 읽힙니다."""
    fixed = scoring.sanitized_config(ScoreConfig(face_bonus_full_area=float("nan")))
    assert fixed.face_bonus_full_area == pytest.approx(0.03)


# ------------------------------------------------------- 사용자 기본값


def test_defaults_match_the_users_settings():
    """사용자가 화면에서 정한 값입니다. 바꾸려면 사용자에게 확인하십시오.

    **근거가 따로 문서화된 값은 여기서 빼 두었습니다.** 두 곳에서 같은
    숫자를 지키면 값 하나를 조정할 때 테스트가 둘씩 깨지는데, 근거 없는
    쪽까지 고치느라 정작 근거가 적힌 쪽을 대충 맞추게 됩니다. 실제로
    이 테스트만 한 세션에 세 번 깨졌고 전부 사용자의 값 조정이었습니다.

    각자 근거를 들고 따로 검사하는 값들:
      - face_bonus_full_area  → test_default_threshold_is_three_percent
      - eyes_closed_below     → test_eyes_closed_penalty.py (임계별 실측표)
      - bonus_eyes_open,
        penalty_eyes_closed   → test_eyes_closed_penalty.py
    """
    config = ScoreConfig()
    assert config.keep_above == 65.0
    assert config.target_keep_ratio is None
    assert config.reject_below == 15.0
    assert config.reject_below_group_best == 10.0
    assert config.trust_eye == 0.75
    assert config.trust_face == 0.60
    assert config.trust_tile == 0.55
    assert config.trust_frame == 0.40
    assert config.bonus_face == 20.0
    assert config.bonus_eye == 15.0
    assert config.bonus_face_size == 0.0
    assert config.penalty_face_defocus == 15.0
    assert config.bonus_focus_on_face == 5.0
    assert config.penalty_no_face == 10.0
