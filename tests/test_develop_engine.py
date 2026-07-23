"""확장된 보정 엔진 테스트."""

from __future__ import annotations

import numpy as np
import pytest

from arw_selector.core.develop import (
    BasicSettings,
    ColorGradeSettings,
    ColorGradeZone,
    CropRatio,
    CurveSettings,
    DetailSettings,
    DevelopSettings,
    EffectSettings,
    GeometrySettings,
    HSLBand,
    HSLSettings,
)
from arw_selector.core.develop import engine


class TestOutputContract:
    """apply_settings는 언제나 8비트 BGR을 돌려줘야 합니다.

    중립일 때 입력(디모자이크 float 0~255)을 그대로 돌려주던 탓에, QImage가
    4바이트 float를 화소로 잘못 읽어 '최종 미리보기'가 화면 전체 컬러
    노이즈가 됐습니다. 같은 이유로 내보내기도 인코더에서 깨집니다.
    """

    def test_neutral_settings_convert_float_input_to_uint8(self):
        image = np.full((20, 30, 3), 120.0, np.float32)
        out = engine.apply_settings(image, DevelopSettings())
        assert out.dtype == np.uint8
        assert float(out.mean()) == pytest.approx(120.0, abs=1.0)

    def test_neutral_settings_keep_uint8_input_identical(self):
        image = np.full((20, 30, 3), 120, np.uint8)
        out = engine.apply_settings(image, DevelopSettings())
        assert out.dtype == np.uint8
        assert np.array_equal(out, image)

    def test_active_settings_return_uint8_from_float_input(self):
        image = np.full((20, 30, 3), 120.0, np.float32)
        out = engine.apply_settings(
            image, DevelopSettings(basic=BasicSettings(exposure=0.5))
        )
        assert out.dtype == np.uint8

    def test_out_of_range_float_is_clipped_not_wrapped(self):
        """float 340.0이 uint8로 wrap돼 84가 되면 하이라이트가 검게 뒤집힙니다."""
        image = np.full((10, 10, 3), 340.0, np.float32)
        out = engine.apply_settings(image, DevelopSettings())
        assert out.max() == 255


@pytest.fixture
def image() -> np.ndarray:
    rng = np.random.default_rng(11)
    base = np.linspace(20, 235, 240, dtype=np.float32)
    canvas = np.repeat(base[None, :], 180, axis=0)
    stacked = np.dstack([canvas, canvas * 0.85, canvas * 0.7])
    noise = rng.normal(0, 4, stacked.shape)
    return np.clip(stacked + noise, 0, 255).astype(np.uint8)


def luma(image: np.ndarray) -> float:
    return float(image.mean())


class TestNeutral:
    def test_neutral_is_noop(self, image):
        assert np.array_equal(engine.apply_settings(image, DevelopSettings()), image)

    def test_default_settings_are_neutral(self):
        assert DevelopSettings().is_neutral()

    def test_watermark_makes_it_non_neutral(self):
        from arw_selector.core.develop import WatermarkSettings

        settings = DevelopSettings(
            watermark=WatermarkSettings(enabled=True, text="© 2026")
        )
        assert not settings.is_neutral()


class TestBasic:
    def test_exposure_one_stop_doubles(self):
        flat = np.full((20, 20, 3), 60, np.uint8)
        result = engine.apply_settings(
            flat, DevelopSettings(basic=BasicSettings(exposure=1.0))
        )
        assert result.mean() == pytest.approx(120, abs=3)

    def test_contrast_widens_spread(self, image):
        result = engine.apply_settings(
            image, DevelopSettings(basic=BasicSettings(contrast=50))
        )
        assert result.std() > image.std()

    def test_shadows_lift_darks_more(self):
        dark = np.full((20, 20, 3), 30, np.uint8)
        bright = np.full((20, 20, 3), 210, np.uint8)
        settings = DevelopSettings(basic=BasicSettings(shadows=70))

        dark_delta = engine.apply_settings(dark, settings).mean() - 30
        bright_delta = engine.apply_settings(bright, settings).mean() - 210
        assert dark_delta > bright_delta

    def test_highlights_pull_brights_more(self):
        dark = np.full((20, 20, 3), 30, np.uint8)
        bright = np.full((20, 20, 3), 205, np.uint8)
        settings = DevelopSettings(basic=BasicSettings(highlights=-70))

        dark_delta = abs(engine.apply_settings(dark, settings).mean() - 30)
        bright_delta = abs(engine.apply_settings(bright, settings).mean() - 205)
        assert bright_delta > dark_delta

    def test_clarity_increases_local_contrast(self, image):
        result = engine.apply_settings(
            image, DevelopSettings(basic=BasicSettings(clarity=80))
        )
        assert not np.array_equal(result, image)

    def test_dehaze_increases_contrast(self, image):
        result = engine.apply_settings(
            image, DevelopSettings(basic=BasicSettings(dehaze=70))
        )
        assert result.std() > image.std()

    def test_high_kelvin_warms(self, image):
        """색온도는 절대 Kelvin. 5500(기준)보다 높이면 따뜻해집니다."""
        result = engine.apply_settings(
            image, DevelopSettings(basic=BasicSettings(temperature=9000))
        )
        # OpenCV는 BGR이라 인덱스 2가 R
        assert result[:, :, 2].mean() > image[:, :, 2].mean()
        assert result[:, :, 0].mean() < image[:, :, 0].mean()

    def test_low_kelvin_cools(self, image):
        """5500보다 낮추면 차가워집니다 (파랑↑, 빨강↓)."""
        result = engine.apply_settings(
            image, DevelopSettings(basic=BasicSettings(temperature=3000))
        )
        assert result[:, :, 0].mean() > image[:, :, 0].mean()
        assert result[:, :, 2].mean() < image[:, :, 2].mean()

    def test_temperature_zero_is_noop(self, image):
        """0은 '손대지 않음' — as-shot을 그대로 둡니다."""
        result = engine.apply_settings(
            image, DevelopSettings(basic=BasicSettings(temperature=0))
        )
        assert np.array_equal(result, image)


class TestCameraProfile:
    def test_profile_brightens_and_saturates(self):
        """기본 프로파일은 평탄한 디모자이크를 밝게 + 채도 있게 만듭니다."""
        rng = np.random.default_rng(3)
        flat = rng.integers(60, 170, (120, 160, 3), dtype=np.uint8)
        out = engine.apply_camera_profile(flat)
        # 중간톤을 끌어올려 전체가 밝아집니다
        assert out.mean() > flat.mean()
        # 정밀도 유지를 위해 float로 다룹니다 (표시 직전에만 8비트)
        assert out.shape == flat.shape and out.dtype == np.float32

    def test_profile_keeps_endpoints(self):
        """검정과 흰색은 그대로 둡니다 (곡선 끝점 고정)."""
        black = np.zeros((4, 4, 3), np.uint8)
        white = np.full((4, 4, 3), 255, np.uint8)
        assert engine.apply_camera_profile(black).max() <= 2
        assert engine.apply_camera_profile(white).min() >= 250

    def test_desaturation_approaches_gray(self, image):
        result = engine.apply_settings(
            image, DevelopSettings(basic=BasicSettings(saturation=-100))
        )
        spread = float(np.mean(result.max(axis=2) - result.min(axis=2)))
        assert spread < 4.0


class TestBrightnessIsNotExposure:
    """밝기와 노출이 같은 일을 하면 슬라이더가 둘일 이유가 없습니다.

    실측(558A8911.CR3): 중간톤을 비슷하게 올렸을 때 하이라이트는 노출
    +22.70 vs 밝기 +0.04, 날아간 화소는 +5.83% vs +0.03%.
    """

    def _ramp(self):
        """0~255가 고르게 퍼진 이미지 — 구간별 거동을 보기 좋습니다."""
        row = np.linspace(0, 255, 256, dtype=np.float32)
        return np.repeat(row[None, :, None], 3, axis=2).repeat(8, axis=0)

    def _apply(self, image, **basic):
        return engine.apply_settings(
            image, DevelopSettings(basic=BasicSettings(**basic))
        )

    def test_brightness_keeps_white_and_black(self):
        """감마라서 양 끝은 고정입니다. 노출은 흰색을 더 밀지 못하고 자릅니다."""
        ramp = self._ramp()

        out = self._apply(ramp, brightness=80)

        assert out[:, 0].mean() == pytest.approx(0.0, abs=1.0), "검정이 떴습니다"
        assert out[:, -1].mean() == pytest.approx(255.0, abs=1.0), "흰색이 내려갔습니다"

    def test_brightness_lifts_midtones(self):
        ramp = self._ramp()

        brighter = self._apply(ramp, brightness=60)
        darker = self._apply(ramp, brightness=-60)

        mid = 128
        assert brighter[:, mid].mean() > ramp[:, mid].mean() + 10
        assert darker[:, mid].mean() < ramp[:, mid].mean() - 10

    def test_exposure_clips_highlights_brightness_does_not(self):
        """이것이 두 슬라이더를 나누는 이유입니다."""
        ramp = self._ramp()

        by_exposure = self._apply(ramp, exposure=1.0)
        by_brightness = self._apply(ramp, brightness=100)

        def clipped(image):
            return float(np.count_nonzero(image >= 254.5)) / image.size

        assert clipped(by_exposure) > clipped(by_brightness) * 2, (
            "노출이 하이라이트를 더 날려야 합니다"
        )

    def test_zero_is_neutral(self):
        ramp = self._ramp()

        assert np.allclose(self._apply(ramp, brightness=0), ramp, atol=1.0)

    def test_round_trip_through_preset(self):
        settings = DevelopSettings(basic=BasicSettings(brightness=-45))

        restored = DevelopSettings.from_dict(settings.to_dict())

        assert restored.basic.brightness == -45


class TestCurve:
    def test_parametric_shadows_lift(self):
        dark = np.full((20, 20, 3), 25, np.uint8)
        result = engine.apply_settings(
            dark, DevelopSettings(curve=CurveSettings(shadows=80))
        )
        assert result.mean() > 25

    def test_point_curve_applied(self, image):
        settings = DevelopSettings(curve=CurveSettings(points_rgb=((128, 200),)))
        result = engine.apply_settings(image, settings)
        assert luma(result) > luma(image)

    def test_channel_curve_affects_single_channel(self, image):
        settings = DevelopSettings(curve=CurveSettings(points_red=((128, 220),)))
        result = engine.apply_settings(image, settings)
        assert result[:, :, 2].mean() > image[:, :, 2].mean()
        assert result[:, :, 0].mean() == pytest.approx(image[:, :, 0].mean(), abs=1.5)

    def test_empty_curve_is_neutral(self):
        assert CurveSettings().is_neutral()

    def test_black_point_is_not_overwritten(self):
        """x=0의 제어점(블랙 포인트)이 엔진의 끝점에 먹히면 안 됩니다."""
        dark = np.zeros((20, 20, 3), np.uint8)
        result = engine.apply_settings(
            dark, DevelopSettings(curve=CurveSettings(points_rgb=((0, 120),)))
        )
        assert result.mean() > 100

    def test_white_point_is_not_overwritten(self):
        white = np.full((20, 20, 3), 255, np.uint8)
        result = engine.apply_settings(
            white, DevelopSettings(curve=CurveSettings(points_rgb=((255, 120),)))
        )
        assert result.mean() < 150

    def test_inverting_curve_does_not_black_out(self):
        """끝점을 뒤집은 반전 곡선. 예전에는 사진이 통째로 검게 나왔습니다.

        (0,0)/(255,255)를 무조건 덧붙이면서 왼쪽 끝만 사용자 점을 덮어,
        0→0 · 255→0인 단조 감소 곡선이 만들어졌습니다.
        """
        ramp = np.tile(
            np.linspace(0, 255, 64, dtype=np.uint8)[None, :, None], (8, 1, 3)
        )
        result = engine.apply_settings(
            ramp, DevelopSettings(curve=CurveSettings(points_rgb=((0, 255), (255, 0))))
        )
        assert result.max() > 200, "반전 곡선인데 전체가 어두워졌습니다"
        assert result[:, 0].mean() > result[:, -1].mean(), "반전이 되지 않았습니다"

    def test_channel_curve_endpoint_applies(self, image):
        result = engine.apply_settings(
            image, DevelopSettings(curve=CurveSettings(points_red=((0, 150),)))
        )
        assert result[:, :, 2].mean() > image[:, :, 2].mean()

    def test_control_points_fill_only_missing_ends(self):
        from arw_selector.core.develop.engine import curve_control_points

        assert curve_control_points(((128, 200),)) == [
            (0.0, 0.0), (128.0, 200.0), (255.0, 255.0)
        ]
        assert curve_control_points(((0, 60),)) == [(0.0, 60.0), (255.0, 255.0)]
        assert curve_control_points(((255, 60),)) == [(0.0, 0.0), (255.0, 60.0)]


class TestHSL:
    def test_band_saturation_changes_that_hue(self):
        """빨강 영역만 조정하면 파랑은 그대로여야 합니다."""
        image = np.zeros((40, 40, 3), np.uint8)
        image[:, :20] = (0, 0, 200)    # 빨강 (BGR)
        image[:, 20:] = (200, 0, 0)    # 파랑

        hsl = HSLSettings(bands={"red": HSLBand(saturation=-100)})
        result = engine.apply_settings(image, DevelopSettings(hsl=hsl))

        red_before = image[:, :20].max(axis=2).astype(int) - image[:, :20].min(axis=2)
        red_after = result[:, :20].max(axis=2).astype(int) - result[:, :20].min(axis=2)
        blue_before = image[:, 20:].max(axis=2).astype(int) - image[:, 20:].min(axis=2)
        blue_after = result[:, 20:].max(axis=2).astype(int) - result[:, 20:].min(axis=2)

        assert red_after.mean() < red_before.mean()
        assert blue_after.mean() == pytest.approx(blue_before.mean(), rel=0.2)

    def test_neutral_hsl_is_noop(self, image):
        assert np.array_equal(
            engine.apply_settings(image, DevelopSettings(hsl=HSLSettings())), image
        )


class TestColorGrade:
    def test_shadow_tint_shifts_darks(self):
        dark = np.full((20, 20, 3), 40, np.uint8)
        grade = ColorGradeSettings(shadows=ColorGradeZone(hue=240, saturation=80))
        result = engine.apply_settings(dark, DevelopSettings(color_grade=grade))
        assert not np.array_equal(result, dark)

    def test_neutral_is_noop(self, image):
        assert np.array_equal(
            engine.apply_settings(image, DevelopSettings(color_grade=ColorGradeSettings())),
            image,
        )


class TestDetail:
    def test_sharpening_increases_edge_contrast(self, image):
        result = engine.apply_settings(
            image, DevelopSettings(detail=DetailSettings(sharpen_amount=100))
        )
        assert result.std() >= image.std()

    def test_noise_reduction_smooths(self):
        rng = np.random.default_rng(5)
        noisy = np.clip(
            np.full((80, 80, 3), 128.0) + rng.normal(0, 25, (80, 80, 3)), 0, 255
        ).astype(np.uint8)
        result = engine.apply_settings(
            noisy, DevelopSettings(detail=DetailSettings(noise_reduction=100))
        )
        assert result.std() < noisy.std()


@pytest.fixture
def noisy_photo() -> np.ndarray:
    """평탄 영역과 잔무늬가 함께 있는 합성 사진.

    평탄한 곳만 두면 디테일 손실이 안 보이고 무늬만 두면 노이즈 감소가
    안 보입니다. 둘을 나란히 둬야 트레이드오프가 측정됩니다. 왼쪽 절반은
    세로 줄무늬(디테일), 오른쪽 절반은 평탄면입니다.

    노이즈도 두 종류를 따로 얹습니다 — 세 채널에 같은 값을 더한 것이
    휘도 노이즈, 채널마다 다른 값을 더한 것이 색 노이즈입니다.
    """
    rng = np.random.default_rng(7)
    canvas = np.full((256, 256, 3), 150.0, np.float32)
    for x in range(10, 120, 10):
        canvas[:, x:x + 5] = 200.0
    canvas += rng.normal(0, 6, (256, 256, 1))   # 휘도 노이즈
    canvas += rng.normal(0, 5, (256, 256, 3))   # 색 노이즈
    return np.clip(canvas, 0, 255).astype(np.uint8)


def _flat_noise(image: np.ndarray) -> float:
    """평탄 영역(오른쪽 절반)에 남은 휘도 노이즈.

    BGR 표준편차를 그냥 재면 색 노이즈가 섞여 들어옵니다. 휘도 노이즈
    감소는 색을 건드리지 않는 것이 맞는 동작이라, 그대로 재면 잘 지우고도
    '아무 일도 안 했다'로 읽힙니다.
    """
    import cv2

    ycc = cv2.cvtColor(image[8:-8, 140:-8], cv2.COLOR_BGR2YCrCb)
    plane = ycc[:, :, 0].astype(np.float32)
    return float((plane - cv2.GaussianBlur(plane, (0, 0), 3)).std())


def _stripe_detail(image: np.ndarray) -> float:
    """줄무늬 영역(왼쪽 절반)에 남은 디테일."""
    import cv2

    patch = cv2.cvtColor(image[8:-8, 8:126], cv2.COLOR_BGR2GRAY).astype(np.float32)
    return float(np.abs(cv2.Sobel(patch, cv2.CV_32F, 1, 0, ksize=3)).mean())


def _color_noise(image: np.ndarray) -> float:
    """평탄 영역에 남은 색 노이즈.

    색 얼룩은 수십 화소에 걸쳐 있으므로 넓게 흐린 값을 뺀 편차로 잽니다.
    화소 단위 편차만 보면 얼룩이 통째로 빠져 측정에 안 잡힙니다.
    """
    import cv2

    ycc = cv2.cvtColor(image[8:-8, 140:-8], cv2.COLOR_BGR2YCrCb).astype(np.float32)
    return float(sum(
        (ycc[:, :, ch] - cv2.GaussianBlur(ycc[:, :, ch], (0, 0), 6)).std()
        for ch in (1, 2)
    ))


def _develop(image, **detail_kwargs) -> np.ndarray:
    return engine.apply_settings(
        image, DevelopSettings(detail=DetailSettings(**detail_kwargs))
    )


class TestNoiseReduction:
    """노이즈 감소 회귀.

    예전 구현은 슬라이더가 사실상 동작하지 않았습니다. 휘도는 지름 5로
    고정된 양방향 필터라 60 이상이 100과 같았고(실측 차이 0.05), 색은
    3×3/5×5 메디안 중 하나를 고르는 구조라 1~53이 전부 같은 결과였습니다.
    그러면서 R6 Mark III ISO 6400 실파일에서 디테일을 절반 잃었습니다.
    여기 있는 것은 그 두 가지가 되돌아오지 않게 막는 테스트입니다.
    """

    def test_stronger_setting_removes_more_noise(self, noisy_photo):
        """슬라이더 전 구간이 실제로 움직여야 합니다."""
        levels = [_flat_noise(_develop(noisy_photo, noise_reduction=n))
                  for n in (20, 40, 60, 80, 100)]
        for a, b in zip(levels, levels[1:]):
            assert b < a * 0.995, f"움직이지 않는 구간이 있습니다: {levels}"
        assert levels[-1] < _flat_noise(noisy_photo) * 0.25

    def test_maximum_beats_old_implementation(self, noisy_photo):
        """같은 최대치에서 예전 방식보다 더 지우고 더 남겨야 합니다.

        예전 방식은 이 둘을 맞바꿨습니다 — 디테일을 크게 잃고도 노이즈는
        더 많이 남겼습니다.
        """
        from arw_selector.core.develop import NoiseAlgorithm

        kwargs = dict(noise_reduction=100, color_noise_reduction=100)
        new = _develop(noisy_photo, **kwargs)
        old = _develop(noisy_photo, noise_algorithm=NoiseAlgorithm.LEGACY, **kwargs)

        assert _flat_noise(new) < _flat_noise(old)
        assert _color_noise(new) < _color_noise(old)
        assert _stripe_detail(new) > _stripe_detail(old)

    def test_color_slider_moves_continuously(self, noisy_photo):
        """색 노이즈 슬라이더가 2단 스위치로 돌아가면 안 됩니다."""
        levels = [_color_noise(_develop(noisy_photo, color_noise_reduction=n))
                  for n in (20, 40, 60, 80, 100)]
        assert levels == sorted(levels, reverse=True), f"단조롭지 않습니다: {levels}"
        for a, b in zip(levels, levels[1:]):
            assert a - b > 0.01, f"같은 값이 반복됩니다: {levels}"

    def test_color_reduction_keeps_luma_detail(self, noisy_photo):
        """색만 지울 때 휘도 디테일은 그대로여야 합니다."""
        result = _develop(noisy_photo, color_noise_reduction=100,
                          color_noise_radius=100)
        assert _color_noise(result) < _color_noise(noisy_photo) * 0.5
        assert _stripe_detail(result) == pytest.approx(
            _stripe_detail(noisy_photo), rel=0.05
        )

    def test_color_radius_is_continuous(self, noisy_photo):
        """반경도 축소 배수의 반올림 때문에 계단이 되면 안 됩니다."""
        levels = [_color_noise(_develop(noisy_photo, color_noise_reduction=60,
                                        color_noise_radius=r))
                  for r in (0, 25, 50, 75, 100)]
        assert levels == sorted(levels, reverse=True), f"단조롭지 않습니다: {levels}"
        for a, b in zip(levels, levels[1:]):
            assert a - b > 0.01, f"같은 값이 반복됩니다: {levels}"

    def test_detail_protection_restores_texture(self, noisy_photo):
        """디테일 보존을 올리면 무늬가 돌아오되 평탄면은 그대로여야 합니다."""
        from arw_selector.core.develop import NoiseAlgorithm

        kwargs = dict(noise_reduction=100, noise_algorithm=NoiseAlgorithm.BILATERAL)
        off = _develop(noisy_photo, noise_detail=0, **kwargs)
        on = _develop(noisy_photo, noise_detail=100, **kwargs)
        assert _stripe_detail(on) > _stripe_detail(off) * 1.05
        # 평탄면까지 되살리면 노이즈 감소를 한 의미가 없습니다
        assert _flat_noise(on) < _flat_noise(off) * 1.15

    def test_every_algorithm_reduces_noise(self, noisy_photo):
        from arw_selector.core.develop import NoiseAlgorithm

        for algorithm in NoiseAlgorithm:
            result = _develop(noisy_photo, noise_reduction=80,
                              color_noise_reduction=80, noise_algorithm=algorithm)
            assert _flat_noise(result) < _flat_noise(noisy_photo), algorithm

    def test_legacy_algorithm_is_frozen(self, noisy_photo):
        """'기존 방식'은 예전 코드와 화소까지 같아야 합니다.

        구버전 재현이 이 선택지의 존재 이유라, 여기서 결과가 달라지면
        선택지 자체가 거짓말이 됩니다.
        """
        import cv2
        from arw_selector.core.develop import NoiseAlgorithm

        expected = np.clip(noisy_photo.astype(np.float32), 0, 255).astype(np.uint8)
        lab = cv2.cvtColor(expected, cv2.COLOR_BGR2Lab)
        lab[:, :, 1] = cv2.medianBlur(lab[:, :, 1], 5)
        lab[:, :, 2] = cv2.medianBlur(lab[:, :, 2], 5)
        expected = cv2.cvtColor(lab, cv2.COLOR_Lab2BGR)
        expected = cv2.bilateralFilter(expected, 5, 70, 70)

        result = _develop(noisy_photo, noise_reduction=100,
                          color_noise_reduction=100,
                          noise_algorithm=NoiseAlgorithm.LEGACY)
        assert np.array_equal(result, expected)

    def test_strength_follows_measured_noise(self):
        """같은 슬라이더 값이 ISO에 따라 다른 세기가 되어야 합니다.

        고정 강도로 두면 저감도에서는 과하게 뭉개고 고감도에서는 손도
        못 댑니다. 강도를 사진의 실제 σ에 비례시키는 것이 이 구현의
        핵심이라, 추정기가 노이즈 크기를 따라가는지 못 박아 둡니다.
        """
        rng = np.random.default_rng(3)
        base = np.full((200, 200), 128.0, np.float32)
        quiet = engine.estimate_noise_sigma(base + rng.normal(0, 2, base.shape))
        loud = engine.estimate_noise_sigma(base + rng.normal(0, 8, base.shape))
        assert quiet == pytest.approx(2.0, rel=0.25)
        assert loud == pytest.approx(8.0, rel=0.25)

    def test_flat_image_estimate_has_floor(self):
        """완전 평탄한 입력에서도 강도가 0이 되면 안 됩니다.

        σ가 0이면 필터 강도도 0이 되어 슬라이더를 끝까지 올려도 아무 일도
        일어나지 않습니다 (축소된 미리보기에서 실제로 그렇게 됩니다).
        """
        assert engine.estimate_noise_sigma(np.full((64, 64), 100.0, np.float32)) > 0

    def test_algorithm_alone_changes_nothing(self, noisy_photo):
        """조정량이 0이면 방식·보조값을 바꿔도 화소가 그대로여야 합니다."""
        from arw_selector.core.develop import NoiseAlgorithm

        for algorithm in NoiseAlgorithm:
            settings = DevelopSettings(
                detail=DetailSettings(noise_algorithm=algorithm,
                                      noise_detail=100, color_noise_radius=100)
            )
            assert settings.is_neutral(), algorithm
            assert np.array_equal(
                engine.apply_settings(noisy_photo, settings), noisy_photo
            ), algorithm


class TestEffects:
    def test_vignette_darkens_corners(self):
        flat = np.full((100, 100, 3), 180, np.uint8)
        result = engine.apply_settings(
            flat, DevelopSettings(effects=EffectSettings(vignette_amount=-80))
        )
        assert result[0, 0].mean() < result[50, 50].mean()

    def test_grain_adds_variation(self):
        flat = np.full((100, 100, 3), 128, np.uint8)
        result = engine.apply_settings(
            flat, DevelopSettings(effects=EffectSettings(grain_amount=80))
        )
        assert result.std() > 1.0

    def test_grain_is_reproducible(self):
        """미리보기가 조작할 때마다 깜빡이면 안 됩니다."""
        flat = np.full((60, 60, 3), 128, np.uint8)
        settings = DevelopSettings(effects=EffectSettings(grain_amount=60))
        assert np.array_equal(
            engine.apply_settings(flat, settings),
            engine.apply_settings(flat, settings),
        )


class TestGeometry:
    def test_crop_reduces_size(self, image):
        geometry = GeometrySettings(crop_left=0.25, crop_right=0.75)
        result = engine.apply_settings(image, DevelopSettings(geometry=geometry))
        assert result.shape[1] == pytest.approx(image.shape[1] * 0.5, abs=2)
        assert result.shape[0] == image.shape[0]

    def test_rotate_swaps_dimensions(self, image):
        geometry = GeometrySettings(rotate_quarters=1)
        result = engine.apply_settings(image, DevelopSettings(geometry=geometry))
        assert result.shape[:2] == image.shape[1::-1]

    def test_two_rotations_preserve_shape(self, image):
        geometry = GeometrySettings(rotate_quarters=2)
        result = engine.apply_settings(image, DevelopSettings(geometry=geometry))
        assert result.shape == image.shape

    def test_flip_horizontal(self):
        image = np.zeros((10, 10, 3), np.uint8)
        image[:, :5] = 255
        result = engine.apply_settings(
            image, DevelopSettings(geometry=GeometrySettings(flip_horizontal=True))
        )
        assert result[:, 5:].mean() > result[:, :5].mean()

    def test_straighten_keeps_size(self, image):
        geometry = GeometrySettings(straighten=5.0)
        result = engine.apply_settings(image, DevelopSettings(geometry=geometry))
        assert result.shape == image.shape

    def test_degenerate_crop_is_ignored(self, image):
        """뒤집힌 크롭 값이 들어와도 중단되면 안 됩니다."""
        geometry = GeometrySettings(crop_left=0.8, crop_right=0.2)
        result = engine.apply_settings(image, DevelopSettings(geometry=geometry))
        assert result.shape == image.shape

    def test_crop_is_resolution_independent(self):
        """미리보기에서 지정한 크롭이 원본에서도 같은 비율이어야 합니다."""
        small = np.zeros((100, 200, 3), np.uint8)
        large = np.zeros((400, 800, 3), np.uint8)
        geometry = GeometrySettings(crop_left=0.25, crop_right=0.75)

        small_out = engine.apply_geometry(small, geometry)
        large_out = engine.apply_geometry(large, geometry)

        assert small_out.shape[1] / small.shape[1] == pytest.approx(
            large_out.shape[1] / large.shape[1], abs=0.01
        )


class TestPipelineInvariants:
    def test_output_is_uint8_and_bounded(self, image):
        settings = DevelopSettings(
            basic=BasicSettings(
                exposure=3.0, contrast=100, highlights=100, shadows=100,
                whites=100, blacks=100, clarity=100, dehaze=100,
                vibrance=100, saturation=100, temperature=100, tint=100,
            ),
            effects=EffectSettings(grain_amount=100, vignette_amount=-100),
            detail=DetailSettings(sharpen_amount=150),
        )
        result = engine.apply_settings(image, settings)
        assert result.dtype == np.uint8
        assert result.min() >= 0 and result.max() <= 255

    def test_pixel_mapping_is_resolution_independent(self):
        """같은 색이면 이미지 크기와 무관하게 같은 결과여야 합니다.

        비선형 연산은 리샘플링과 교환되지 않으므로 픽셀 단위 일치를
        요구할 수는 없지만, 매핑 자체는 해상도에 의존하면 안 됩니다.
        국소 연산(명료도/노이즈/비네팅)은 성질상 제외합니다.
        """
        settings = DevelopSettings(
            basic=BasicSettings(exposure=0.6, contrast=25, saturation=20, shadows=30)
        )
        for value in (20, 90, 160, 230):
            small = np.full((8, 8, 3), value, np.uint8)
            large = np.full((300, 400, 3), value, np.uint8)
            assert np.array_equal(
                engine.apply_settings(small, settings)[0, 0],
                engine.apply_settings(large, settings)[0, 0],
            )

    def test_render_preview_matches_apply_settings(self, image):
        from arw_selector.core.raw_io import resize_long_edge

        settings = DevelopSettings(basic=BasicSettings(exposure=0.4, contrast=20))
        rendered = engine.render_preview(image, settings, long_edge=120)
        expected = engine.apply_settings(resize_long_edge(image, 120), settings)
        assert np.array_equal(rendered, expected)


class TestWithoutGeometry:
    """크롭은 컷마다 구도가 달라서 일괄 적용하면 안 됩니다."""

    def test_strips_geometry_only(self):
        settings = DevelopSettings(
            basic=BasicSettings(exposure=0.7, contrast=20),
            geometry=GeometrySettings(crop_left=0.2, crop_right=0.8, straighten=3.0),
        )
        shared = settings.without_geometry()

        assert shared.geometry.is_neutral(), "크롭이 남아 있습니다"
        assert shared.basic == settings.basic, "색보정까지 지워졌습니다"

    def test_original_is_unchanged(self):
        settings = DevelopSettings(
            geometry=GeometrySettings(crop_left=0.3, rotate_quarters=1)
        )
        settings.without_geometry()
        assert settings.geometry.crop_left == 0.3
        assert settings.geometry.rotate_quarters == 1

    def test_neutral_stays_neutral(self):
        assert DevelopSettings().without_geometry().is_neutral()

    def test_geometry_only_becomes_neutral(self):
        """크롭만 있던 설정은 공유할 것이 없습니다."""
        settings = DevelopSettings(geometry=GeometrySettings(crop_top=0.1))
        assert settings.without_geometry().is_neutral()


class TestSerialization:
    def test_full_round_trip(self):
        settings = DevelopSettings(
            basic=BasicSettings(exposure=0.7, clarity=30, dehaze=10),
            curve=CurveSettings(shadows=20, points_rgb=((100, 120), (200, 190))),
            detail=DetailSettings(sharpen_amount=60, noise_reduction=25),
            hsl=HSLSettings(bands={"blue": HSLBand(hue=10, saturation=-20)}),
            color_grade=ColorGradeSettings(
                shadows=ColorGradeZone(hue=210, saturation=30), balance=15
            ),
            effects=EffectSettings(grain_amount=20, vignette_amount=-30),
            geometry=GeometrySettings(crop_left=0.1, straighten=2.5, ratio=CropRatio.THREE_TWO),
        )
        restored = DevelopSettings.from_dict(settings.to_dict())

        assert restored.basic == settings.basic
        assert restored.curve.points_rgb == settings.curve.points_rgb
        assert restored.hsl.bands["blue"].hue == 10
        assert restored.color_grade.shadows.hue == 210
        assert restored.geometry.ratio == CropRatio.THREE_TWO
        assert restored == settings

    def test_unknown_keys_ignored(self):
        """예전 프리셋 파일도 열려야 합니다."""
        data = DevelopSettings().to_dict()
        data["basic"]["removed_field"] = 5
        data["brand_new_section"] = {"x": 1}
        assert DevelopSettings.from_dict(data).is_neutral()

    def test_empty_dict_gives_defaults(self):
        assert DevelopSettings.from_dict({}).is_neutral()

    def test_corrupt_curve_points_ignored(self):
        data = DevelopSettings().to_dict()
        data["curve"]["points_rgb"] = "not a list of points"
        assert DevelopSettings.from_dict(data).curve.points_rgb == ()

    def test_string_enums_are_normalized(self):
        """PySide6는 str 상속 Enum을 위젯 데이터로 넣으면 평범한 str로 바꿔 줍니다.

        여기서 흡수하지 않으면 to_dict()에서 .value 접근이 터집니다.
        """
        from arw_selector.core.develop import WatermarkPosition, WatermarkSettings

        geometry = GeometrySettings(ratio="16:9")
        watermark = WatermarkSettings(position="top_left")

        assert geometry.ratio is CropRatio.SIXTEEN_NINE
        assert watermark.position is WatermarkPosition.TOP_LEFT
        # to_dict가 예외 없이 돌아야 합니다
        DevelopSettings(geometry=geometry, watermark=watermark).to_dict()

    def test_unknown_enum_value_falls_back(self):
        assert GeometrySettings(ratio="누가봐도이상한값").ratio is CropRatio.FREE

    def test_noise_algorithm_survives_round_trip(self):
        from arw_selector.core.develop import NoiseAlgorithm

        settings = DevelopSettings(
            detail=DetailSettings(
                noise_reduction=40, color_noise_reduction=60,
                noise_algorithm=NoiseAlgorithm.NLMEANS_HQ,
                noise_detail=30, color_noise_radius=80,
            )
        )
        data = settings.to_dict()
        # 프리셋은 YAML로 저장됩니다. Enum 인스턴스가 그대로 남아 있으면
        # safe_dump가 통째로 실패합니다.
        assert data["detail"]["noise_algorithm"] == "nlmeans_hq"
        assert DevelopSettings.from_dict(data) == settings

    def test_noise_algorithm_accepts_plain_string(self):
        """PySide6 콤보 데이터는 Enum이 평범한 str로 돌아옵니다."""
        from arw_selector.core.develop import NoiseAlgorithm

        assert DetailSettings(noise_algorithm="bilateral").noise_algorithm is (
            NoiseAlgorithm.BILATERAL
        )
        assert DetailSettings(noise_algorithm="없는방식").noise_algorithm is (
            NoiseAlgorithm.NLMEANS
        )

    def test_preset_without_noise_algorithm_still_loads(self):
        """이 기능이 생기기 전에 저장한 프리셋도 열려야 합니다."""
        from arw_selector.core.develop import NoiseAlgorithm

        data = DevelopSettings().to_dict()
        data["detail"] = {"sharpen_amount": 40, "noise_reduction": 30}
        restored = DevelopSettings.from_dict(data)

        assert restored.detail.sharpen_amount == 40
        assert restored.detail.noise_reduction == 30
        assert restored.detail.noise_algorithm is NoiseAlgorithm.NLMEANS
        assert restored.detail.noise_detail == 50
        assert restored.detail.color_noise_radius == 50
