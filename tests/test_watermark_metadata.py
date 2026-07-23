"""워터마크와 EXIF 삽입 테스트."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from arw_selector.core.develop import (
    EXIF_FIELDS,
    MetadataSettings,
    WatermarkPosition,
    WatermarkSettings,
)
from arw_selector.core.develop.watermark import apply_watermark


@pytest.fixture
def canvas() -> np.ndarray:
    """중간 회색 — 워터마크가 얹혔는지 차이로 확인하기 좋습니다."""
    return np.full((400, 600, 3), 128, np.uint8)


def changed_region(before: np.ndarray, after: np.ndarray) -> np.ndarray:
    return np.any(before != after, axis=2)


class TestWatermarkText:
    def test_disabled_is_noop(self, canvas):
        settings = WatermarkSettings(enabled=False, text="© 2026")
        assert np.array_equal(apply_watermark(canvas, settings), canvas)

    def test_empty_text_is_noop(self, canvas):
        settings = WatermarkSettings(enabled=True, text="")
        assert np.array_equal(apply_watermark(canvas, settings), canvas)

    def test_text_is_drawn(self, canvas):
        settings = WatermarkSettings(enabled=True, text="ARW 2026")
        result = apply_watermark(canvas, settings)
        assert not np.array_equal(result, canvas)

    def test_original_is_not_mutated(self, canvas):
        original = canvas.copy()
        apply_watermark(canvas, WatermarkSettings(enabled=True, text="test"))
        assert np.array_equal(canvas, original)

    def test_output_shape_preserved(self, canvas):
        result = apply_watermark(canvas, WatermarkSettings(enabled=True, text="test"))
        assert result.shape == canvas.shape
        assert result.dtype == np.uint8

    @pytest.mark.parametrize(
        "position,quadrant",
        [
            (WatermarkPosition.TOP_LEFT, (slice(0, 200), slice(0, 300))),
            (WatermarkPosition.TOP_RIGHT, (slice(0, 200), slice(300, 600))),
            (WatermarkPosition.BOTTOM_LEFT, (slice(200, 400), slice(0, 300))),
            (WatermarkPosition.BOTTOM_RIGHT, (slice(200, 400), slice(300, 600))),
        ],
    )
    def test_position_places_in_expected_quadrant(self, canvas, position, quadrant):
        settings = WatermarkSettings(
            enabled=True, text="MARK", position=position, scale=6, margin=2
        )
        mask = changed_region(canvas, apply_watermark(canvas, settings))
        assert mask.any(), "워터마크가 그려지지 않았습니다"
        assert mask[quadrant].sum() > mask.sum() * 0.7

    def test_opacity_scales_effect(self, canvas):
        faint = apply_watermark(
            canvas, WatermarkSettings(enabled=True, text="MARK", opacity=15)
        )
        strong = apply_watermark(
            canvas, WatermarkSettings(enabled=True, text="MARK", opacity=100)
        )
        faint_delta = np.abs(faint.astype(int) - canvas.astype(int)).sum()
        strong_delta = np.abs(strong.astype(int) - canvas.astype(int)).sum()
        assert strong_delta > faint_delta

    def test_scale_changes_size(self, canvas):
        small = changed_region(
            canvas, apply_watermark(canvas, WatermarkSettings(enabled=True, text="M", scale=3))
        )
        large = changed_region(
            canvas, apply_watermark(canvas, WatermarkSettings(enabled=True, text="M", scale=12))
        )
        assert large.sum() > small.sum()

    def test_scale_is_resolution_independent(self):
        """미리보기에서 맞춘 크기가 원본에서도 같은 비율이어야 합니다."""
        small = np.full((200, 300, 3), 128, np.uint8)
        large = np.full((800, 1200, 3), 128, np.uint8)
        settings = WatermarkSettings(enabled=True, text="MARK", scale=10)

        small_ratio = changed_region(small, apply_watermark(small, settings)).sum() / small[:, :, 0].size
        large_ratio = changed_region(large, apply_watermark(large, settings)).sum() / large[:, :, 0].size
        assert small_ratio == pytest.approx(large_ratio, rel=0.35)

    def test_korean_text_does_not_crash(self, canvas):
        """OpenCV 기본 폰트는 한글을 못 그린다 — PIL 경로를 타야 합니다."""
        result = apply_watermark(
            canvas, WatermarkSettings(enabled=True, text="촬영 홍길동")
        )
        assert result.shape == canvas.shape

    def test_oversized_watermark_is_clipped_not_fatal(self, canvas):
        settings = WatermarkSettings(enabled=True, text="VERY LONG WATERMARK", scale=90)
        result = apply_watermark(canvas, settings)
        assert result.shape == canvas.shape


class TestWatermarkImage:
    def test_png_with_alpha(self, canvas, tmp_path):
        logo = np.zeros((40, 80, 4), np.uint8)
        logo[:, :, 2] = 255       # 빨강
        logo[:, :, 3] = 255       # 불투명
        path = tmp_path / "logo.png"
        cv2.imwrite(str(path), logo)

        settings = WatermarkSettings(enabled=True, image_path=str(path), scale=20)
        result = apply_watermark(canvas, settings)
        assert not np.array_equal(result, canvas)

    def test_missing_image_falls_back_to_text(self, canvas, tmp_path):
        settings = WatermarkSettings(
            enabled=True, image_path=str(tmp_path / "nope.png"), text="FALLBACK"
        )
        result = apply_watermark(canvas, settings)
        assert not np.array_equal(result, canvas)

    def test_missing_image_without_text_is_noop(self, canvas, tmp_path):
        settings = WatermarkSettings(enabled=True, image_path=str(tmp_path / "nope.png"))
        assert np.array_equal(apply_watermark(canvas, settings), canvas)


class TestMetadataSettings:
    def test_disabled_wants_nothing(self):
        settings = MetadataSettings(enabled=False, include=("camera", "lens"))
        assert not settings.wants("camera")

    def test_only_included_fields_wanted(self):
        settings = MetadataSettings(enabled=True, include=("camera",))
        assert settings.wants("camera")
        assert not settings.wants("lens")

    def test_default_is_off(self):
        """내보낸 사진에 촬영 정보가 기본으로 딸려 나가면 안 됩니다."""
        settings = MetadataSettings()
        assert settings.enabled is False
        assert settings.include == ()
        assert all(not settings.wants(key) for key in EXIF_FIELDS)


class TestMetadataWriting:
    @pytest.fixture
    def jpeg(self, tmp_path):
        path = tmp_path / "out.jpg"
        cv2.imwrite(str(path), np.full((60, 90, 3), 140, np.uint8))
        return path

    def test_writes_selected_fields_only(self, jpeg, tmp_path, monkeypatch):
        from datetime import datetime

        from arw_selector.core.develop import metadata as metadata_module
        from arw_selector.core.raw_io import RawMetadata

        source = tmp_path / "DSC001.ARW"
        source.write_bytes(b"fake")
        monkeypatch.setattr(
            metadata_module,
            "read_metadata",
            lambda p: RawMetadata(
                path=p,
                camera_model="ILCE-6700",
                lens_model="E 50-300mm F4.5-6.3 A069",
                iso=3200,
                shutter_speed=1 / 200,
                aperture=7.1,
                focal_length=290.0,
                capture_time=datetime(2026, 7, 12, 16, 1, 48),
            ),
        )

        settings = MetadataSettings(
            enabled=True, include=("camera", "copyright"), copyright="© 2026 홍길동"
        )
        assert metadata_module.write_metadata(source, jpeg, settings) is True

        written = metadata_module.read_written_metadata(jpeg)
        assert written["model"] == "ILCE-6700"
        assert "2026" in written["copyright"]
        # 선택하지 않은 항목은 들어가면 안 됩니다
        assert written["lens"] is None
        assert written["iso"] is None
        assert written["datetime"] is None

    def test_never_writes_gps(self, jpeg, tmp_path, monkeypatch):
        """위치 정보는 실수로 흘러나갔을 때 가장 위험합니다."""
        from arw_selector.core.develop import metadata as metadata_module
        from arw_selector.core.raw_io import RawMetadata

        source = tmp_path / "DSC001.ARW"
        source.write_bytes(b"fake")
        monkeypatch.setattr(
            metadata_module, "read_metadata",
            lambda p: RawMetadata(path=p, camera_model="ILCE-6700"),
        )

        settings = MetadataSettings(enabled=True, include=tuple(EXIF_FIELDS))
        metadata_module.write_metadata(source, jpeg, settings)

        assert metadata_module.read_written_metadata(jpeg)["gps"] == {}

    def test_disabled_writes_nothing(self, jpeg, tmp_path):
        from arw_selector.core.develop import metadata as metadata_module

        source = tmp_path / "DSC001.ARW"
        source.write_bytes(b"fake")
        assert metadata_module.write_metadata(source, jpeg, MetadataSettings()) is False

    def test_write_failure_is_not_fatal(self, tmp_path, monkeypatch):
        """메타데이터 실패로 내보내기 전체를 망치면 안 됩니다."""
        from arw_selector.core.develop import metadata as metadata_module
        from arw_selector.core.raw_io import RawMetadata

        source = tmp_path / "DSC001.ARW"
        source.write_bytes(b"fake")
        monkeypatch.setattr(
            metadata_module, "read_metadata",
            lambda p: RawMetadata(path=p, camera_model="ILCE-6700"),
        )

        settings = MetadataSettings(enabled=True, include=("camera",))
        missing = tmp_path / "does_not_exist.jpg"
        assert metadata_module.write_metadata(source, missing, settings) is False
