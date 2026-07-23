"""LED월 가로 줄무늬 제거 (#123).

가장 중요한 성질은 **줄무늬가 없는 사진은 건드리지 않는 것**입니다.
구현 도중 실제로 그 반대가 됐습니다 — 기준선 시그마를 줄무늬 주기(103px)
보다 크게(133) 잡았더니 줄무늬가 기준선에 흡수되어 신호가 사라지고, 주기가
전부 하한으로 나오면서 정상 컷까지 67% 보정했습니다.

실측(디모자이크 원본): 줄무늬 컷 DSC02751·DSC03868 주기 103px, 잔차
71~78% 감소. 정상 컷 DSC03360·DSC02435는 **미검출**이라 무변경.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from arw_selector.core.develop import engine
from arw_selector.core.develop.settings import DetailSettings


def _striped(height=600, width=400, period=103, amplitude=4.0) -> np.ndarray:
    """세로 방향으로 주기적인 밝기 변동이 있는 회색 판."""
    rng = np.random.default_rng(5)
    base = np.full((height, width, 3), 110.0, np.float32)
    base += rng.normal(0, 1.5, (height, width, 1)).astype(np.float32)
    rows = np.arange(height, dtype=np.float32)
    band = np.sin(2 * np.pi * rows / period) * amplitude
    return base + band[:, None, None]


def _clean(height=600, width=400) -> np.ndarray:
    rng = np.random.default_rng(5)
    base = np.full((height, width, 3), 110.0, np.float32)
    return base + rng.normal(0, 1.5, (height, width, 1)).astype(np.float32)


def _row_residual_std(image: np.ndarray) -> float:
    gray = cv2.cvtColor(np.clip(image, 0, 255).astype(np.float32),
                        cv2.COLOR_BGR2GRAY)
    residual, _period, _strength = engine.measure_stripe(gray)
    return float(residual.std())


# ------------------------------------------------------- 검출


def test_period_is_found():
    gray = cv2.cvtColor(np.clip(_striped(), 0, 255).astype(np.float32),
                        cv2.COLOR_BGR2GRAY)
    _residual, period, strength = engine.measure_stripe(gray)
    assert abs(period - 103) <= 3, f"주기 {period}px (103 근처여야 합니다)"
    assert strength >= engine.STRIPE_MIN_STRENGTH


def test_clean_image_has_no_period():
    """줄무늬가 없으면 주기 0 — 그래야 보정을 건너뜁니다."""
    gray = cv2.cvtColor(np.clip(_clean(), 0, 255).astype(np.float32),
                        cv2.COLOR_BGR2GRAY)
    _residual, period, _strength = engine.measure_stripe(gray)
    assert period == 0


def test_baseline_sigma_is_below_the_stripe_period():
    """시그마가 주기보다 크면 줄무늬가 기준선에 흡수되어 사라집니다.

    실제로 133으로 뒀다가 신호를 통째로 잃었습니다.
    """
    assert engine.STRIPE_BASELINE_SIGMA * 3 < 103


# ------------------------------------------------------- 보정


def test_stripes_are_reduced():
    """한 번에 전부 지워지지는 않습니다 — 알려진 한계입니다.

    기준선(시그마 13.5)이 주기 103px 진동을 조금 흡수하므로, 잔차로 뽑히는
    것은 실제 줄무늬보다 약합니다. 그만큼만 빼니 일부가 남습니다.

    실측(실사진, 디모자이크 원본): 71~78% 감소. 여기 합성 표본은 진폭이
    크고 순수한 사인파라 더 불리한 조건입니다.
    """
    image = _striped()
    before = _row_residual_std(image)
    after = _row_residual_std(engine.apply_destripe(image, 100))
    assert after < before * 0.75, f"{before:.3f} → {after:.3f} (덜 지워졌습니다)"


def test_clean_image_is_untouched():
    """가장 중요한 성질 — 줄무늬가 없으면 한 화소도 바뀌면 안 됩니다."""
    image = _clean()
    result = engine.apply_destripe(image, 100)
    assert np.array_equal(result, image)


def test_horizontal_detail_survives():
    """행마다 같은 값을 빼므로 가로 방향 그래디언트는 불변이어야 합니다."""
    image = _striped()
    image[:, 150:250] += 40.0  # 세로 줄무늬(가로 방향 엣지)
    fixed = engine.apply_destripe(image, 100)

    def horizontal_energy(source):
        gray = cv2.cvtColor(np.clip(source, 0, 255).astype(np.float32),
                            cv2.COLOR_BGR2GRAY)
        return float(np.abs(np.diff(gray, axis=1)).mean())

    assert horizontal_energy(fixed) == pytest.approx(
        horizontal_energy(image), rel=0.02)


def test_amount_scales_the_correction():
    image = _striped()
    full = _row_residual_std(engine.apply_destripe(image, 100))
    half = _row_residual_std(engine.apply_destripe(image, 50))
    none = _row_residual_std(engine.apply_destripe(image, 0))
    assert full < half < none


def test_zero_amount_returns_the_same_object():
    image = _striped()
    assert engine.apply_destripe(image, 0) is image


def test_tiny_image_does_not_crash():
    """주기를 잴 수 없을 만큼 작은 이미지."""
    tiny = np.full((8, 8, 3), 100.0, np.float32)
    assert engine.apply_destripe(tiny, 100) is tiny


# ------------------------------------------------------- 설정


def test_destripe_is_off_by_default():
    """줄무늬는 특정 촬영장에서만 나옵니다. 늘 켜 둘 기능이 아닙니다."""
    assert DetailSettings().destripe == 0


def test_destripe_alone_counts_as_an_edit():
    """이 값만 올린 상태도 '보정 있음'이어야 프리셋 비교가 맞습니다."""
    assert not DetailSettings(destripe=50).is_neutral()
    assert DetailSettings().is_neutral()


def test_runs_before_noise_reduction():
    """노이즈 감소가 먼저 돌면 줄무늬를 일부 뭉개 놓아 얼룩덜룩해집니다."""
    import inspect

    source = inspect.getsource(engine._apply_detail)
    assert source.index("apply_destripe") < source.index("apply_noise_reduction")
