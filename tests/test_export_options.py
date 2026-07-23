"""내보내기 옵션과 EXIF 정보 띠 테스트."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import pytest

from arw_selector.core import export
from arw_selector.core.develop import BasicSettings, DevelopSettings, ExifStripSettings
from arw_selector.core.develop.exif_strip import apply_exif_strip, build_lines
from arw_selector.core.export_options import (
    ExportFormat,
    ExportOptions,
    ResizeMode,
    format_filename,
)
from arw_selector.core.raw_io import RawMetadata
from arw_selector.core.types import Grade, ImageRecord


def make_record(tmp_path: Path, name: str = "DSC001.ARW", grade=Grade.KEEP) -> ImageRecord:
    path = tmp_path / name
    path.write_bytes(b"fake raw")
    record = ImageRecord(path=path)
    record.grade = grade
    record.score = 72.4
    record.metadata = RawMetadata(
        path=path,
        camera_model="ILCE-6700",
        lens_model="E 50-300mm F4.5-6.3 A069",
        iso=3200,
        shutter_speed=1 / 200,
        aperture=7.1,
        focal_length=290.0,
        capture_time=datetime(2026, 7, 12, 16, 1, 48),
    )
    return record


class TestFilenamePattern:
    def test_default_keeps_original_name(self, tmp_path):
        record = make_record(tmp_path)
        assert format_filename("{name}", record, 1, ".jpg") == "DSC001.jpg"

    def test_index_is_zero_padded(self, tmp_path):
        record = make_record(tmp_path)
        assert format_filename("{index}_{name}", record, 7, ".jpg") == "0007_DSC001.jpg"

    def test_grade_and_score(self, tmp_path):
        record = make_record(tmp_path)
        assert format_filename("{grade}_{score}", record, 1, ".jpg") == "keep_72.jpg"

    def test_capture_date_used(self, tmp_path):
        record = make_record(tmp_path)
        assert format_filename("{date}", record, 1, ".jpg") == "20260712.jpg"

    def test_unknown_placeholder_left_alone(self, tmp_path):
        """조용히 지우면 사용자가 오타를 알아차리지 못합니다."""
        record = make_record(tmp_path)
        assert "{nope}" in format_filename("{name}{nope}", record, 1, ".jpg")

    def test_invalid_characters_replaced(self, tmp_path):
        record = make_record(tmp_path)
        result = format_filename("a/b:c*d", record, 1, ".jpg")
        assert not any(ch in result for ch in '<>:"/\\|?*')

    def test_empty_pattern_falls_back_to_stem(self, tmp_path):
        record = make_record(tmp_path)
        assert format_filename("", record, 1, ".jpg") == "DSC001.jpg"


class TestExportOptions:
    def test_defaults(self):
        options = ExportOptions()
        assert options.image_format is ExportFormat.JPEG
        assert options.resize_mode is ResizeMode.NONE
        assert options.target_long_edge() is None

    def test_long_edge_resize(self):
        options = ExportOptions(resize_mode=ResizeMode.LONG_EDGE, resize_long_edge=2048)
        assert options.target_long_edge() == 2048

    def test_percent_resize_needs_source_size(self):
        options = ExportOptions(resize_mode=ResizeMode.PERCENT, resize_percent=50)
        assert options.target_long_edge(6000) == 3000
        assert options.target_long_edge() is None

    def test_string_enums_normalized(self):
        """위젯 데이터 왕복에서 문자열로 돌아와도 동작해야 합니다."""
        options = ExportOptions(image_format="png", resize_mode="long_edge")
        assert options.image_format is ExportFormat.PNG
        assert options.resize_mode is ResizeMode.LONG_EDGE

    def test_unknown_enum_falls_back(self):
        assert ExportOptions(image_format="bmp").image_format is ExportFormat.JPEG

    def test_round_trip(self):
        options = ExportOptions(quality=80, resize_mode=ResizeMode.LONG_EDGE)
        assert ExportOptions.from_dict(options.to_dict()) == options


class TestExportWithOptions:
    @pytest.fixture
    def fake_preview(self, monkeypatch):
        image = np.random.default_rng(4).integers(40, 200, (200, 300, 3), dtype=np.uint8)
        monkeypatch.setattr("arw_selector.core.raw_io.load_preview", lambda *a, **k: image)
        return image

    def test_filename_pattern_applied(self, tmp_path, fake_preview):
        record = make_record(tmp_path)
        record.develop = DevelopSettings(basic=BasicSettings(exposure=0.5))
        options = ExportOptions(filename_pattern="{index}_{grade}")

        export.export_records([record], tmp_path, options=options)

        assert (tmp_path / "_keep" / "0001_keep.jpg").exists()

    def test_png_output(self, tmp_path, fake_preview):
        record = make_record(tmp_path)
        record.develop = DevelopSettings(basic=BasicSettings(exposure=0.5))
        options = ExportOptions(image_format=ExportFormat.PNG)

        export.export_records([record], tmp_path, options=options)

        assert (tmp_path / "_keep" / "DSC001.png").exists()

    def test_resize_applied(self, tmp_path, fake_preview):
        record = make_record(tmp_path)
        record.develop = DevelopSettings(basic=BasicSettings(exposure=0.5))
        options = ExportOptions(resize_mode=ResizeMode.LONG_EDGE, resize_long_edge=100)

        export.export_records([record], tmp_path, options=options)

        rendered = cv2.imread(str(tmp_path / "_keep" / "DSC001.jpg"))
        assert max(rendered.shape[:2]) == 100

    def test_skip_raw_copy(self, tmp_path, fake_preview):
        """현상본만 필요할 때 원본까지 복사하면 용량이 두 배가 됩니다."""
        record = make_record(tmp_path)
        record.develop = DevelopSettings(basic=BasicSettings(exposure=0.5))
        options = ExportOptions(copy_raw=False)

        result = export.export_records([record], tmp_path, options=options)

        assert (tmp_path / "_keep" / "DSC001.jpg").exists()
        assert not (tmp_path / "_keep" / "DSC001.ARW").exists()
        assert result.rendered == 1

    def test_move_always_moves_even_when_raw_copy_off(self, tmp_path, fake_preview):
        """이동인데 원본이 제자리에 남으면 '이동'이 아닙니다."""
        record = make_record(tmp_path)
        record.develop = DevelopSettings(basic=BasicSettings(exposure=0.5))
        options = ExportOptions(copy_raw=False, move=True)

        export.export_records([record], tmp_path, move=True, options=options)

        assert not (tmp_path / "DSC001.ARW").exists()
        assert (tmp_path / "_keep" / "DSC001.ARW").exists()

    def test_neutral_photo_still_rendered_when_raw_copy_off(
        self, tmp_path, fake_preview
    ):
        """보정을 안 건드린 컷이라고 결과물에서 통째로 빠지면 안 됩니다.

        원본 복사를 껐으면 그 컷의 산출물은 렌더링본뿐입니다. 보정값이
        없다는 이유로 렌더까지 건너뛰면 사진 한 장이 조용히 사라집니다.
        """
        record = make_record(tmp_path)
        record.develop = None
        options = ExportOptions(copy_raw=False)

        result = export.export_records([record], tmp_path, options=options)

        assert (tmp_path / "_keep" / "DSC001.jpg").exists()
        assert result.rendered == 1

    def test_nothing_to_export_reports_failure(self, tmp_path, fake_preview):
        """결과물이 하나도 없는 조합인데 '완료'로 끝나면 안 됩니다."""
        record = make_record(tmp_path)
        options = ExportOptions(copy_raw=False, apply_develop=False)

        result = export.export_records(
            [record], tmp_path, apply_develop=False, options=options
        )

        assert result.failed, "아무것도 안 나오는데 성공으로 끝났습니다"
        assert result.rendered == 0 and result.moved == 0
        assert not (tmp_path / "_keep").exists()

    def test_raw_only_when_develop_off(self, tmp_path, fake_preview):
        """'보정 적용해서 이미지 만들기'를 끄면 RAW만 나가야 합니다.

        보정값이 남아 있는 컷이라도 체크를 껐으면 JPEG가 나오면 안 됩니다.
        """
        record = make_record(tmp_path)
        record.develop = DevelopSettings(basic=BasicSettings(exposure=0.5))
        options = ExportOptions(copy_raw=True, apply_develop=False)

        result = export.export_records(
            [record], tmp_path, apply_develop=False, options=options
        )

        assert (tmp_path / "_keep" / "DSC001.ARW").exists()
        assert not (tmp_path / "_keep" / "DSC001.jpg").exists()
        assert result.rendered == 0 and result.moved == 1

    def test_flat_output_without_grade_folders(self, tmp_path):
        record = make_record(tmp_path)
        options = ExportOptions(subfolder_by_grade=False)

        export.export_records([record], tmp_path, options=options)

        assert (tmp_path / "DSC001.ARW").exists()
        assert not (tmp_path / "_keep").exists()


class TestExifStrip:
    @pytest.fixture
    def image(self):
        return np.full((300, 500, 3), 120, np.uint8)

    def test_disabled_is_noop(self, image, tmp_path):
        settings = ExifStripSettings(enabled=False)
        result = apply_exif_strip(image, tmp_path / "a.ARW", None, settings)
        assert np.array_equal(result, image)

    def test_strip_is_appended_below(self, image, tmp_path):
        settings = ExifStripSettings(enabled=True)
        record = make_record(tmp_path)
        result = apply_exif_strip(image, record.path, record.metadata, settings)

        assert result.shape[0] > image.shape[0]
        assert result.shape[1] == image.shape[1]
        # 사진 영역은 그대로여야 합니다
        assert np.array_equal(result[: image.shape[0]], image)

    def test_dark_and_light_backgrounds_differ(self, image, tmp_path):
        record = make_record(tmp_path)
        dark = apply_exif_strip(
            image, record.path, record.metadata,
            ExifStripSettings(enabled=True, dark_background=True),
        )
        light = apply_exif_strip(
            image, record.path, record.metadata,
            ExifStripSettings(enabled=True, dark_background=False),
        )
        assert dark[-5].mean() < light[-5].mean()

    def test_height_is_proportional(self, tmp_path):
        record = make_record(tmp_path)
        settings = ExifStripSettings(enabled=True, height_percent=10.0)

        small = apply_exif_strip(
            np.full((200, 300, 3), 120, np.uint8), record.path, record.metadata, settings
        )
        large = apply_exif_strip(
            np.full((800, 1200, 3), 120, np.uint8), record.path, record.metadata, settings
        )

        assert (small.shape[0] - 200) / 200 == pytest.approx(
            (large.shape[0] - 800) / 800, abs=0.02
        )

    def test_lines_contain_selected_fields_only(self, tmp_path):
        record = make_record(tmp_path)
        settings = ExifStripSettings(enabled=True, include=("camera", "iso"))
        left, _ = build_lines(record.metadata, record.path, settings)

        assert "ILCE-6700" in left
        assert "ISO 3200" in left
        assert "50-300mm" not in left

    def test_custom_text_goes_right(self, tmp_path):
        record = make_record(tmp_path)
        settings = ExifStripSettings(enabled=True, custom_text="© 2026 홍길동")
        _, right = build_lines(record.metadata, record.path, settings)
        assert right == "© 2026 홍길동"

    def test_long_text_does_not_overlap_custom_text(self, tmp_path):
        """긴 촬영 정보가 오른쪽 문구를 덮어써서 글자가 겹치던 문제.

        좌우 텍스트 폭을 재서 크기를 줄이거나 항목을 덜어내야 합니다.
        """
        from arw_selector.core.develop.exif_strip import _fit_texts, _measure_text

        left = "ILCE-6700  ·  E 50-300mm F4.5-6.3 A069  ·  290mm  ·  f/7.1  ·  1/200s  ·  ISO 3200"
        right = "© 2026 STUDIO"
        available = 600
        scale = 1.0
        thickness = 2

        fitted, fitted_scale = _fit_texts(left, right, available, scale, thickness)

        left_width = _measure_text(fitted, fitted_scale, thickness)
        right_width = _measure_text(right, fitted_scale, thickness)
        assert left_width + right_width < available, "좌우 텍스트가 여전히 겹친다"

    def test_short_text_is_not_shrunk(self, tmp_path):
        from arw_selector.core.develop.exif_strip import _fit_texts

        fitted, scale = _fit_texts("ISO 100", "", 2000, 1.0, 2)
        assert fitted == "ISO 100"
        assert scale == 1.0

    def test_strip_renders_without_overlap_at_narrow_width(self, tmp_path):
        record = make_record(tmp_path)
        settings = ExifStripSettings(
            enabled=True,
            include=("camera", "lens", "focal_length", "aperture", "shutter", "iso"),
            custom_text="© 2026 STUDIO",
        )
        narrow = np.full((200, 400, 3), 120, np.uint8)
        result = apply_exif_strip(narrow, record.path, record.metadata, settings)
        assert result.shape[0] > narrow.shape[0]
        assert result.shape[1] == narrow.shape[1]

    def test_missing_metadata_falls_back_to_filename(self, tmp_path):
        settings = ExifStripSettings(enabled=True, include=("camera",))
        left, _ = build_lines(None, tmp_path / "DSC999.ARW", settings)
        assert "DSC999.ARW" in left

    def test_settings_round_trip(self):
        settings = DevelopSettings(
            exif_strip=ExifStripSettings(
                enabled=True, dark_background=False, include=("camera", "iso"),
                custom_text="테스트",
            )
        )
        restored = DevelopSettings.from_dict(settings.to_dict())
        assert restored.exif_strip == settings.exif_strip

    def test_strip_makes_settings_non_neutral(self):
        settings = DevelopSettings(exif_strip=ExifStripSettings(enabled=True))
        assert not settings.is_neutral()
