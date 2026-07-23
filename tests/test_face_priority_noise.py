"""얼굴 우선 노이즈 감소 (#122).

지키려는 성질은 셋입니다.
  1. 얼굴 밖 강도가 실제로 줄어든다 (그래야 옷·머리카락이 산다).
  2. 얼굴 안 강도는 그대로다 (그래야 피부 알갱이가 지워진다).
  3. 얼굴이 없으면 이 값은 무시하고 예전처럼 화면 전체에 건다
     — 풍경 사진에서 기능이 통째로 사라지면 안 됩니다.
"""

from __future__ import annotations

import numpy as np
import pytest

from arw_selector.core.develop import engine
from arw_selector.core.develop.settings import DetailSettings, NoiseAlgorithm


def _faces(x=100.0, y=100.0, w=120.0, h=140.0) -> np.ndarray:
    return np.array([[x, y, w, h] + [0.0] * 10 + [0.99]], np.float64)


def _noisy(height=400, width=400) -> np.ndarray:
    """**휘도** 노이즈가 있는 회색 판.

    채널마다 다른 난수를 넣으면 그 대부분은 색 노이즈가 되어, 휘도 노이즈
    감소는 손댈 것이 없습니다(실제로 그렇게 만든 첫 판에서는 채널 표준편차가
    8.91 → 9.79로 오히려 늘었습니다). 세 채널에 같은 값을 더해야 휘도
    노이즈입니다.
    """
    rng = np.random.default_rng(11)
    grain = rng.normal(0, 9.0, (height, width, 1)).astype(np.float32)
    return np.full((height, width, 3), 128.0, np.float32) + grain


def _luma(image: np.ndarray) -> np.ndarray:
    import cv2

    return cv2.cvtColor(np.clip(image, 0, 255).astype(np.float32),
                        cv2.COLOR_BGR2GRAY)


# ------------------------------------------------------- 가중치 맵


def test_weight_is_one_on_the_face_and_lower_outside():
    weight = engine._face_weight_map(400, 400, _faces(), 100)
    assert weight is not None
    assert weight[170, 160] == pytest.approx(1.0, abs=0.02)  # 얼굴 중심
    assert weight[10, 10] < 0.9                              # 구석


def test_priority_sets_the_outside_floor():
    """우선도는 얼굴 밖 강도를 비례해서 덜어냅니다."""
    weight = engine._face_weight_map(400, 400, _faces(), 60)
    assert weight is not None
    expected = 1.0 - engine.FACE_PRIORITY_MAX_CUT * 0.6
    assert weight[10, 10] == pytest.approx(expected, abs=0.02)


def test_maximum_priority_still_denoises_outside():
    """얼굴 밖을 0으로 만들면 안 됩니다.

    노이즈가 제일 심한 곳은 얼굴이 아니라 어두운 배경입니다 —
    실측(A6700 ISO3200, DSC02434)에서 얼굴 안 σ 1.52 / 얼굴 밖 σ 3.05.
    거기를 통째로 건너뛰니 노이즈 감소를 100으로 올려도 화면이 그대로였습니다.
    """
    weight = engine._face_weight_map(400, 400, _faces(), 100)
    assert weight is not None
    assert weight[10, 10] >= 0.3, "얼굴 밖이 사실상 꺼져 있습니다"


def test_no_faces_means_no_weighting():
    """None을 돌려줘야 부르는 쪽이 화면 전체에 같은 강도를 겁니다."""
    assert engine._face_weight_map(400, 400, None, 100) is None
    assert engine._face_weight_map(400, 400, np.empty((0, 15)), 100) is None


def test_zero_priority_means_no_weighting():
    assert engine._face_weight_map(400, 400, _faces(), 0) is None


def test_weight_edge_is_feathered():
    """경계가 딱 끊기면 얼굴 테두리에 노이즈가 달라지는 선이 보입니다."""
    weight = engine._face_weight_map(400, 400, _faces(), 100)
    assert weight is not None
    column = weight[170, :]
    steps = np.abs(np.diff(column))
    assert steps.max() < 0.25, "가중치가 한 화소에서 급격히 변합니다"


# ------------------------------------------------------- 실제 효과


def _flat_std(image: np.ndarray, box) -> float:
    y0, y1, x0, x1 = box
    return float(_luma(image)[y0:y1, x0:x1].std())


def test_outside_keeps_more_noise_than_inside():
    image = _noisy()
    detail = DetailSettings(
        noise_reduction=80, noise_detail=0, face_priority=100,
        noise_algorithm=NoiseAlgorithm.BILATERAL,
    )
    result = engine.apply_noise_reduction(image, detail, _faces())

    inside = _flat_std(result, (140, 200, 130, 190))
    outside = _flat_std(result, (320, 380, 320, 380))
    assert inside < outside * 0.8, (
        f"얼굴 안({inside:.2f})이 밖({outside:.2f})보다 확실히 매끄러워야 합니다")


def test_inside_is_as_smooth_as_a_global_pass():
    """얼굴 안에서는 우선도를 켜도 강도가 그대로여야 합니다."""
    image = _noisy()
    common = dict(noise_reduction=80, noise_detail=0,
                  noise_algorithm=NoiseAlgorithm.BILATERAL)

    globally = engine.apply_noise_reduction(
        image, DetailSettings(**common, face_priority=0), _faces())
    prioritised = engine.apply_noise_reduction(
        image, DetailSettings(**common, face_priority=100), _faces())

    box = (140, 200, 130, 190)
    assert _flat_std(prioritised, box) == pytest.approx(
        _flat_std(globally, box), rel=0.15)


def test_without_faces_the_whole_frame_is_denoised():
    """얼굴을 못 찾은 사진에서 노이즈 감소가 사라지면 안 됩니다."""
    image = _noisy()
    detail = DetailSettings(
        noise_reduction=80, noise_detail=0, face_priority=100,
        noise_algorithm=NoiseAlgorithm.BILATERAL,
    )
    result = engine.apply_noise_reduction(image, detail, None)

    corner = (320, 380, 320, 380)
    assert _flat_std(result, corner) < _flat_std(image, corner) * 0.8


def test_face_priority_alone_is_not_an_edit():
    """이 값만 바뀐 상태가 '보정 있음'으로 표시되면 프리셋 비교가 거짓말합니다."""
    assert DetailSettings(face_priority=100).is_neutral()
    assert DetailSettings(face_priority=0).is_neutral()


# ------------------------------------------------------- 계산 범위


def test_active_bounds_covers_only_the_live_region():
    """가중치가 0인 곳까지 비싼 필터를 돌리지 않습니다."""
    weight = np.zeros((400, 400), np.float32)
    weight[120:260, 100:230] = 1.0
    bounds = engine._active_bounds(weight)
    assert bounds is not None
    y0, y1, x0, x1 = bounds
    assert (y0, y1, x0, x1) == (120, 260, 100, 230)


def test_face_priority_never_lets_us_skip_the_background():
    """얼굴 밖에 하한이 생겨서 이 최적화는 더는 걸리지 않습니다.

    얼굴 우선을 100까지 올려도 배경이 절반 세기를 받으므로, 전면에
    필터를 돌려야 합니다. 26MP에서 비국소 평균 한 번이 약 2초입니다 —
    배경 노이즈를 실제로 지우는 대가입니다.
    """
    for priority in (60, 100):
        weight = engine._face_weight_map(400, 400, _faces(), priority)
        assert engine._active_bounds(weight) == (0, 400, 0, 400)


def test_active_bounds_of_an_empty_map():
    assert engine._active_bounds(np.zeros((10, 10), np.float32)) is None
