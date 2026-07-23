"""광학 보정과 워터마크 배치 테스트."""

from __future__ import annotations

import numpy as np
import pytest

from arw_selector.core.develop import (
    DevelopSettings,
    OpticsSettings,
    WatermarkPosition,
    WatermarkSettings,
)
from arw_selector.core.develop import engine, optics
from arw_selector.core.develop.watermark import apply_watermark
from arw_selector.core.raw_io import RawMetadata


@pytest.fixture
def grid_image() -> np.ndarray:
    """직선 격자 — 왜곡 보정 효과를 눈에 보이것이 합니다."""
    image = np.full((300, 400, 3), 40, np.uint8)
    for x in range(0, 400, 40):
        image[:, x:x + 2] = 220
    for y in range(0, 300, 40):
        image[y:y + 2, :] = 220
    return image


class TestOpticsSettings:
    def test_default_is_neutral(self):
        assert OpticsSettings().is_neutral()

    def test_manual_values_break_neutrality(self):
        assert not OpticsSettings(distortion=20).is_neutral()
        assert not OpticsSettings(defringe_purple=30).is_neutral()

    def test_auto_breaks_neutrality(self):
        assert not OpticsSettings(auto_enabled=True).is_neutral()

    def test_round_trip(self):
        settings = DevelopSettings(
            optics=OpticsSettings(
                auto_enabled=True, distortion=-15, defringe_purple=40
            )
        )
        assert DevelopSettings.from_dict(settings.to_dict()).optics == settings.optics


class TestManualDistortion:
    def test_zero_is_noop(self, grid_image):
        assert np.array_equal(
            optics.apply_manual_distortion(grid_image, 0), grid_image
        )

    def test_changes_image(self, grid_image):
        result = optics.apply_manual_distortion(grid_image, 40)
        assert not np.array_equal(result, grid_image)
        assert result.shape == grid_image.shape

    def test_opposite_signs_differ(self, grid_image):
        barrel = optics.apply_manual_distortion(grid_image, -40)
        pincushion = optics.apply_manual_distortion(grid_image, 40)
        assert not np.array_equal(barrel, pincushion)

    def test_center_is_stable(self, grid_image):
        """방사 왜곡은 중심을 움직이지 않습니다."""
        result = optics.apply_manual_distortion(grid_image, 50)
        h, w = grid_image.shape[:2]
        assert np.array_equal(result[h // 2, w // 2], grid_image[h // 2, w // 2])


class TestManualVignetting:
    def test_zero_is_noop(self, grid_image):
        assert np.array_equal(
            optics.apply_manual_vignetting(grid_image, 0), grid_image
        )

    def test_positive_brightens_corners_more_than_center(self):
        flat = np.full((200, 200, 3), 100, np.uint8)
        result = optics.apply_manual_vignetting(flat, 80)

        corner_gain = int(result[2, 2].mean()) - 100
        center_gain = int(result[100, 100].mean()) - 100
        assert corner_gain > center_gain

    def test_stays_in_range(self):
        bright = np.full((100, 100, 3), 250, np.uint8)
        result = optics.apply_manual_vignetting(bright, 100)
        assert result.max() <= 255


class TestDefringe:
    def test_zero_is_noop(self, grid_image):
        assert np.array_equal(optics.apply_defringe(grid_image, 0, 0), grid_image)

    def test_reduces_purple_at_edges(self):
        """고대비 경계의 보라 언저리만 걷어내야 합니다."""
        image = np.zeros((100, 100, 3), np.uint8)
        image[:, :50] = (20, 20, 20)
        image[:, 50:] = (230, 230, 230)
        # 경계에 보라색 띠를 넣는다 (BGR)
        image[:, 48:52] = (200, 40, 200)

        result = optics.apply_defringe(image, 90, 0)
        before = image[:, 48:52].astype(int)
        after = result[:, 48:52].astype(int)

        before_spread = (before.max(axis=2) - before.min(axis=2)).mean()
        after_spread = (after.max(axis=2) - after.min(axis=2)).mean()
        assert after_spread < before_spread

    def test_output_shape_and_type(self, grid_image):
        result = optics.apply_defringe(grid_image, 50, 50)
        assert result.shape == grid_image.shape
        assert result.dtype == np.uint8


class TestFloatInput:
    """파이프라인 중간값은 float 0~255입니다 — 광학 보정도 그것을 받습니다.

    디모자이크가 float으로 넘겨 주므로 실제 사용 경로가 전부 float입니다.
    uint8만 가정하면 색이 통째로 날아가거나 계조가 8비트로 뭉갭니다.
    """

    @pytest.fixture
    def color_image(self) -> np.ndarray:
        rng = np.random.default_rng(7)
        return rng.integers(0, 256, (80, 120, 3), dtype=np.uint8)

    def test_defringe_keeps_color_on_float_input(self, color_image):
        """float으로 넣어도 흑백이 되면 안 됩니다."""
        result = optics.apply_defringe(color_image.astype(np.float32), 80, 80)
        spread = float(np.mean(result.max(axis=2) - result.min(axis=2)))
        original = float(np.mean(
            color_image.max(axis=2).astype(np.int16) - color_image.min(axis=2)
        ))
        assert spread > original * 0.5

    def test_defringe_float_matches_uint8(self, color_image):
        as_u8 = optics.apply_defringe(color_image, 80, 80)
        as_float = optics.apply_defringe(color_image.astype(np.float32), 80, 80)
        assert as_float.dtype == np.float32
        assert np.array_equal(as_float.astype(np.uint8), as_u8)

    def test_manual_vignetting_keeps_float_precision(self, color_image):
        """8비트로 떨구면 이후 톤 곡선에서 밴딩이 생깁니다."""
        source = color_image.astype(np.float32) + 0.5
        result = optics.apply_manual_vignetting(source, 60)
        assert result.dtype == np.float32
        assert not np.array_equal(result, np.floor(result))

    def test_optics_pipeline_float_equals_uint8(self, color_image):
        settings = OpticsSettings(
            distortion=40, manual_vignetting=50,
            defringe_purple=60, defringe_green=60,
        )
        as_u8 = engine.apply_settings(color_image, DevelopSettings(optics=settings))
        as_float = engine.apply_settings(
            color_image.astype(np.float32), DevelopSettings(optics=settings)
        )
        # float 쪽은 중간에 8비트로 떨구지 않으므로 반올림 차이가 남습니다.
        # 예전에는 float 입력만 채도가 통째로 날아가 평균이 크게 벌어졌습니다.
        difference = np.abs(as_u8.astype(np.int16) - as_float.astype(np.int16))
        assert difference.mean() < 1.0

        def spread(array):
            return float(np.mean(
                array.max(axis=2).astype(np.int16) - array.min(axis=2)
            ))

        assert spread(as_float) == pytest.approx(spread(as_u8), rel=0.05)

    @pytest.mark.parametrize("hue", [5, 65, 120, 145, 175])
    def test_sample_hue_matches_on_float_input(self, hue):
        """스포이드는 화면(float 미리보기)에서 찍어도 같은 색조를 내야 합니다.

        apply_defringe가 쓰는 8비트 HSV 색조(0~179)와 같은 눈금이어야
        언저리 제거가 찍은 색에 걸립니다.
        """
        import cv2

        hsv = np.zeros((30, 30, 3), np.uint8)
        hsv[:, :, 0] = hue
        hsv[:, :, 1] = 220
        hsv[:, :, 2] = 200
        bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)

        assert optics.sample_hue(bgr, 15, 15) == pytest.approx(hue, abs=2)
        assert optics.sample_hue(bgr.astype(np.float32), 15, 15) == pytest.approx(
            hue, abs=2
        )


class TestLensLookup:
    def test_missing_metadata_reports_reason(self):
        match = optics.find_lens(None)
        assert not match.found
        assert match.reason

    def test_unknown_camera_reports_reason(self, tmp_path):
        match = optics.find_lens(
            RawMetadata(path=tmp_path / "x.ARW", camera_model="존재하지않는카메라")
        )
        assert not match.found
        assert match.summary

    @pytest.mark.skipif(not optics.LENSFUN_AVAILABLE, reason="lensfunpy 없음")
    def test_known_sony_lens_is_found(self, tmp_path):
        """실측으로 확인된 조합 — 소니 순정 E PZ 16-50mm는 DB에 있습니다."""
        match = optics.find_lens(
            RawMetadata(
                path=tmp_path / "x.ARW",
                camera_model="ILCE-6700",
                lens_model="E PZ 16-50mm F3.5-5.6 OSS II",
                focal_length=31.0,
                aperture=5.0,
            )
        )
        assert match.found
        assert "16-50" in match.summary

    @pytest.mark.skipif(not optics.LENSFUN_AVAILABLE, reason="lensfunpy 없음")
    def test_third_party_lens_reports_missing(self, tmp_path):
        """탐론 A069는 DB에 없습니다. 수동 보정을 안내해야 합니다."""
        match = optics.find_lens(
            RawMetadata(
                path=tmp_path / "x.ARW",
                camera_model="ILCE-6700",
                lens_model="E 50-300mm F4.5-6.3 A069",
            )
        )
        assert not match.found
        assert "수동" in match.reason or "없음" in match.reason

    def test_auto_correction_without_profile_returns_original(self, grid_image, tmp_path):
        """프로필이 없으면 조용히 원본을 돌려주고 수동 보정으로 이어져야 합니다."""
        metadata = RawMetadata(
            path=tmp_path / "x.ARW",
            camera_model="존재하지않는카메라",
            lens_model="존재하지않는렌즈",
        )
        settings = OpticsSettings(auto_enabled=True)
        assert np.array_equal(
            optics.apply_auto_correction(grid_image, metadata, settings), grid_image
        )


class TestAutoCorrectionSafety:
    def test_non_finite_result_is_discarded(self, grid_image, tmp_path, monkeypatch):
        """프로필이 inf/NaN을 내면 그대로 쓰면 픽셀이 쓰레기가 됩니다.

        실제로 lensfun 비네팅 보정이 overflow를 일으킨 적이 있습니다.
        """
        if not optics.LENSFUN_AVAILABLE:
            pytest.skip("lensfunpy 없음")

        class BrokenModifier:
            def initialize(self, *args, **kwargs):
                return None

            def apply_color_modification(self, image):
                image[:] = np.inf
                return True

            def apply_geometry_distortion(self):
                return None

        monkeypatch.setattr(
            optics.lensfunpy, "Modifier", lambda *a, **k: BrokenModifier()
        )
        metadata = RawMetadata(
            path=tmp_path / "x.ARW",
            camera_model="ILCE-6700",
            lens_model="E PZ 16-50mm F3.5-5.6 OSS II",
        )
        result = optics.apply_auto_correction(
            grid_image, metadata, OpticsSettings(auto_enabled=True)
        )

        assert np.all(np.isfinite(result.astype(np.float32)))
        assert np.array_equal(result, grid_image), "비정상 결과를 버리지 않았습니다"


class TestAutoCorrectionWorks:
    @pytest.mark.skipif(not optics.LENSFUN_AVAILABLE, reason="lensfunpy 없음")
    def test_vignetting_actually_changes_image(self, tmp_path):
        """가드만 있고 기능이 죽어 있으면 안 됩니다.

        pixel_format을 선언하지 않아 lensfun이 0~255 연산을 0~1 값에
        적용하는 바람에 결과가 폭주하고, 가드가 매번 결과를 버렸었습니다.
        """
        metadata = RawMetadata(
            path=tmp_path / "x.ARW",
            camera_model="ILCE-6700",
            lens_model="E PZ 16-50mm F3.5-5.6 OSS II",
            focal_length=16.0,
            aperture=3.5,
        )
        # 16mm f/3.5는 주변부 감광이 커서 보정 이득도 큽니다. 밝은 회색으로
        # 하면 모서리가 255에 물려 비교가 무의미해지므로 어둡게 잡습니다.
        flat = np.full((400, 600, 3), 60, np.uint8)
        original = flat.copy()
        result = optics.apply_auto_correction(
            flat, metadata,
            OpticsSettings(auto_enabled=True, auto_distortion=False),
        )

        assert not np.array_equal(result, original), "비네팅 보정이 적용되지 않았습니다"
        assert np.all(np.isfinite(result.astype(np.float32)))

        # 중앙은 거의 그대로, 주변부만 밝아져야 합니다
        corner = int(result[5, 5].mean()) - 60
        center = int(result[200, 300].mean()) - 60
        assert corner > center, f"모서리 {corner} <= 중앙 {center}"

    @pytest.mark.skipif(not optics.LENSFUN_AVAILABLE, reason="lensfunpy 없음")
    def test_input_image_is_not_mutated(self, tmp_path):
        """lensfun은 배열을 제자리에서 고칩니다.

        사본을 만들지 않으면 호출자가 넘긴 이미지가 파괴됩니다. 미리보기
        소스를 그대로 넘기면 원본이 망가진 채로 남습니다.
        """
        metadata = RawMetadata(
            path=tmp_path / "x.ARW",
            camera_model="ILCE-6700",
            lens_model="E PZ 16-50mm F3.5-5.6 OSS II",
            focal_length=16.0,
            aperture=3.5,
        )
        source = np.full((200, 300, 3), 90, np.uint8)
        snapshot = source.copy()

        optics.apply_auto_correction(
            source, metadata,
            OpticsSettings(auto_enabled=True, auto_distortion=False),
        )

        assert np.array_equal(source, snapshot), "입력 이미지가 변형됐다"

    @pytest.mark.skipif(not optics.LENSFUN_AVAILABLE, reason="lensfunpy 없음")
    def test_distortion_actually_changes_image(self, grid_image, tmp_path):
        metadata = RawMetadata(
            path=tmp_path / "x.ARW",
            camera_model="ILCE-6700",
            lens_model="E PZ 16-50mm F3.5-5.6 OSS II",
            focal_length=16.0,
            aperture=3.5,
        )
        result = optics.apply_auto_correction(
            grid_image, metadata,
            OpticsSettings(auto_enabled=True, auto_vignetting=False),
        )
        assert not np.array_equal(result, grid_image)


class TestOpticsInPipeline:
    def test_engine_applies_optics(self, grid_image):
        settings = DevelopSettings(optics=OpticsSettings(distortion=40))
        result = engine.apply_settings(grid_image, settings)
        assert not np.array_equal(result, grid_image)

    def test_neutral_optics_does_not_change_image(self, grid_image):
        settings = DevelopSettings(optics=OpticsSettings())
        assert np.array_equal(engine.apply_settings(grid_image, settings), grid_image)


class TestWatermarkPositions:
    @pytest.fixture
    def canvas(self) -> np.ndarray:
        return np.full((400, 600, 3), 128, np.uint8)

    def test_all_nine_positions_exist(self):
        assert len(list(WatermarkPosition)) == 9

    @pytest.mark.parametrize(
        "position,x_range,y_range",
        [
            (WatermarkPosition.TOP_LEFT, (0, 300), (0, 200)),
            (WatermarkPosition.TOP_CENTER, (150, 450), (0, 200)),
            (WatermarkPosition.TOP_RIGHT, (300, 600), (0, 200)),
            (WatermarkPosition.MIDDLE_LEFT, (0, 300), (100, 300)),
            (WatermarkPosition.CENTER, (150, 450), (100, 300)),
            (WatermarkPosition.BOTTOM_RIGHT, (300, 600), (200, 400)),
            (WatermarkPosition.BOTTOM_CENTER, (150, 450), (200, 400)),
        ],
    )
    def test_position_lands_in_expected_area(self, canvas, position, x_range, y_range):
        settings = WatermarkSettings(
            enabled=True, text="MARK", position=position, scale=6, margin=2
        )
        changed = np.any(canvas != apply_watermark(canvas, settings), axis=2)
        ys, xs = np.nonzero(changed)
        assert len(xs) > 0, "워터마크가 그려지지 않았습니다"

        center_x, center_y = xs.mean(), ys.mean()
        assert x_range[0] <= center_x <= x_range[1], f"x={center_x:.0f}"
        assert y_range[0] <= center_y <= y_range[1], f"y={center_y:.0f}"

    def test_offset_shifts_watermark(self, canvas):
        base = WatermarkSettings(enabled=True, text="M", position=WatermarkPosition.CENTER)
        shifted = WatermarkSettings(
            enabled=True, text="M", position=WatermarkPosition.CENTER, offset_x=20.0
        )

        def center_x(settings):
            changed = np.any(canvas != apply_watermark(canvas, settings), axis=2)
            return np.nonzero(changed)[1].mean()

        assert center_x(shifted) > center_x(base) + 50

    def test_offset_is_resolution_independent(self):
        """미세조정도 해상도와 무관하게 같은 비율이어야 합니다."""
        settings = WatermarkSettings(
            enabled=True, text="M", position=WatermarkPosition.TOP_LEFT,
            offset_x=10.0, scale=8,
        )
        ratios = []
        for shape in ((200, 300, 3), (400, 600, 3)):
            canvas = np.full(shape, 128, np.uint8)
            changed = np.any(canvas != apply_watermark(canvas, settings), axis=2)
            ratios.append(np.nonzero(changed)[1].mean() / shape[1])

        assert ratios[0] == pytest.approx(ratios[1], abs=0.05)

    def test_rotation_changes_footprint(self, canvas):
        upright = WatermarkSettings(enabled=True, text="MARK", scale=8)
        rotated = WatermarkSettings(enabled=True, text="MARK", scale=8, rotation=45)

        def footprint(settings):
            return np.any(canvas != apply_watermark(canvas, settings), axis=2).sum()

        assert footprint(rotated) != footprint(upright)

    def test_settings_round_trip_with_offsets(self):
        settings = DevelopSettings(
            watermark=WatermarkSettings(
                enabled=True, text="© 2026", position=WatermarkPosition.MIDDLE_RIGHT,
                offset_x=-12.5, offset_y=7.5, rotation=30,
            )
        )
        restored = DevelopSettings.from_dict(settings.to_dict())
        assert restored.watermark == settings.watermark
        assert restored.watermark.position is WatermarkPosition.MIDDLE_RIGHT
