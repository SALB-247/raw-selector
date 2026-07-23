"""크래시 방지 테스트.

사용자의 원본 사진을 다루는 도구이므로 어떤 입력에도 중단되면 안 됩니다.
손상 파일 한 장이 4000장 배치를 중단시키거나, 극단적인 설정값이 예외를
던지는 일이 없어야 합니다.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from arw_selector.core import export, focus, grouping, scoring
from arw_selector.core.config import AnalyzeConfig, Config, GroupConfig, ScoreConfig
from arw_selector.core.develop import (
    BasicSettings,
    ColorGradeSettings,
    ColorGradeZone,
    CurveSettings,
    DetailSettings,
    DevelopSettings,
    EffectSettings,
    ExifStripSettings,
    GeometrySettings,
    HSLBand,
    HSLSettings,
    LocalAdjustments,
    Mask,
    MaskType,
    MetadataSettings,
    OpticsSettings,
    WatermarkSettings,
)
from arw_selector.core.develop import engine
from arw_selector.core.export_options import ExportOptions
from arw_selector.core.export_queue import ExportQueue
from arw_selector.core.pipeline import analyze_file
from arw_selector.core.types import Grade, ImageRecord


class TestCorruptFiles:
    """손상된 파일이 배치를 중단시키면 안 됩니다."""

    @pytest.mark.parametrize(
        "content",
        [
            b"",                        # 빈 파일
            b"\x00" * 1024,             # 널 바이트
            b"not a raw file at all",   # 텍스트
            bytes(range(256)) * 40,     # 임의 바이너리
            b"\xff\xd8\xff\xe0" + b"\x00" * 500,  # JPEG 헤더만
        ],
    )
    def test_analyze_survives_garbage(self, tmp_path, content):
        path = tmp_path / "broken.ARW"
        path.write_bytes(content)

        record = analyze_file(path, AnalyzeConfig())

        assert record.path == path
        assert record.error is not None
        assert not record.ok

    def test_missing_file(self, tmp_path):
        record = analyze_file(tmp_path / "없음.ARW", AnalyzeConfig())
        assert record.error is not None

    def test_directory_instead_of_file(self, tmp_path):
        directory = tmp_path / "폴더.ARW"
        directory.mkdir()
        record = analyze_file(directory, AnalyzeConfig())
        assert record.error is not None

    def test_metadata_of_garbage_is_empty_not_raised(self, tmp_path):
        from arw_selector.core.raw_io import read_metadata

        path = tmp_path / "broken.CR3"
        path.write_bytes(b"\x00" * 200)
        meta = read_metadata(path)
        assert meta.path == path
        assert meta.camera_model is None


class TestExtremeImages:
    """비정상적인 크기와 내용의 이미지."""

    @pytest.mark.parametrize(
        "shape",
        [(1, 1, 3), (1, 500, 3), (500, 1, 3), (2, 2, 3), (7, 13, 3)],
    )
    def test_focus_on_tiny_images(self, shape):
        image = np.zeros(shape, np.uint8)
        result = focus.analyze_focus(image)
        assert 0.0 <= result.sharpness <= 100.0

    @pytest.mark.parametrize("value", [0, 255])
    def test_focus_on_uniform_images(self, value):
        image = np.full((200, 300, 3), value, np.uint8)
        result = focus.analyze_focus(image)
        assert 0.0 <= result.sharpness <= 100.0

    def test_develop_on_tiny_image(self):
        image = np.full((2, 2, 3), 128, np.uint8)
        settings = DevelopSettings(
            basic=BasicSettings(exposure=2.0, clarity=100),
            detail=DetailSettings(sharpen_amount=150, noise_reduction=100),
            effects=EffectSettings(grain_amount=100, vignette_amount=-100),
        )
        result = engine.apply_settings(image, settings)
        assert result.shape == image.shape
        assert result.dtype == np.uint8

    def test_develop_on_single_pixel(self):
        image = np.full((1, 1, 3), 200, np.uint8)
        assert engine.apply_settings(
            image, DevelopSettings(basic=BasicSettings(contrast=100))
        ).shape == (1, 1, 3)

    @pytest.mark.parametrize("shape", [(0, 10, 3), (10, 0, 3), (0, 0, 3)])
    def test_develop_on_zero_size_image(self, shape):
        """폭이나 높이가 0이면 OpenCV가 전부 예외를 던집니다.

        한 장 때문에 미리보기 스레드나 배치 내보내기가 통째로 멈추면 안 됩니다.
        """
        image = np.zeros(shape, np.uint8)
        settings = DevelopSettings(
            basic=BasicSettings(exposure=1.0, clarity=100, texture=100, dehaze=50),
            optics=OpticsSettings(distortion=50, manual_vignetting=50,
                                  defringe_purple=50),
            effects=EffectSettings(grain_amount=100, vignette_amount=-100),
            watermark=WatermarkSettings(enabled=True, text="A"),
        )
        result = engine.apply_settings(image, settings)
        assert result.dtype == np.uint8
        assert result.shape == shape


class TestExtremeSettings:
    """모든 파라미터를 끝까지 밀어도 죽지 않아야 합니다."""

    @pytest.fixture
    def image(self):
        rng = np.random.default_rng(1)
        return rng.integers(0, 256, (120, 160, 3), dtype=np.uint8)

    def test_all_maximum(self, image):
        settings = DevelopSettings(
            basic=BasicSettings(
                temperature=100, tint=100, exposure=5.0, contrast=100,
                highlights=100, shadows=100, whites=100, blacks=100,
                texture=100, clarity=100, dehaze=100, vibrance=100, saturation=100,
            ),
            curve=CurveSettings(
                highlights=100, lights=100, darks=100, shadows=100,
                points_rgb=((1, 254), (254, 1)),
            ),
            detail=DetailSettings(
                sharpen_amount=150, sharpen_radius=3.0,
                noise_reduction=100, color_noise_reduction=100,
            ),
            hsl=HSLSettings(bands={
                name: HSLBand(100, 100, 100)
                for name in ("red", "orange", "yellow", "green",
                             "aqua", "blue", "purple", "magenta")
            }),
            color_grade=ColorGradeSettings(
                shadows=ColorGradeZone(359, 100, 100),
                midtones=ColorGradeZone(180, 100, -100),
                highlights=ColorGradeZone(0, 100, 100),
                blending=100, balance=100,
            ),
            effects=EffectSettings(
                grain_amount=100, grain_size=100,
                vignette_amount=-100, vignette_midpoint=100,
            ),
            optics=OpticsSettings(
                distortion=100, manual_vignetting=100,
                defringe_purple=100, defringe_green=100,
            ),
        )
        result = engine.apply_settings(image, settings)
        assert result.dtype == np.uint8
        assert result.min() >= 0 and result.max() <= 255

    def test_all_minimum(self, image):
        settings = DevelopSettings(
            basic=BasicSettings(
                temperature=-100, tint=-100, exposure=-5.0, contrast=-100,
                highlights=-100, shadows=-100, whites=-100, blacks=-100,
                texture=-100, clarity=-100, dehaze=-100,
                vibrance=-100, saturation=-100,
            ),
            optics=OpticsSettings(distortion=-100, manual_vignetting=-100),
        )
        result = engine.apply_settings(image, settings)
        assert result.dtype == np.uint8

    def test_degenerate_crop_values(self, image):
        for geometry in (
            GeometrySettings(crop_left=1.0, crop_right=0.0),
            GeometrySettings(crop_top=0.9, crop_bottom=0.1),
            GeometrySettings(crop_left=-5.0, crop_right=99.0),
            GeometrySettings(crop_left=0.5, crop_right=0.5),
        ):
            result = engine.apply_settings(
                image, DevelopSettings(geometry=geometry)
            )
            assert result.size > 0

    def test_extreme_rotation_values(self, image):
        for quarters in (-8, -1, 0, 5, 400):
            result = engine.apply_settings(
                image, DevelopSettings(
                    geometry=GeometrySettings(rotate_quarters=quarters)
                )
            )
            assert result.size > 0

    def test_watermark_with_absurd_scale(self, image):
        settings = DevelopSettings(
            watermark=WatermarkSettings(
                enabled=True, text="X" * 200, scale=40, rotation=180,
                offset_x=50.0, offset_y=-50.0,
            )
        )
        assert engine.apply_settings(image, settings).shape[:2] == image.shape[:2]

    @pytest.mark.parametrize("size", [0, 1, 100, 200])
    def test_radial_mask_extreme_size(self, image, size):
        """범위 0%에서 반경이 0이 되어 알파에 NaN이 섞이면 안 됩니다.

        NaN은 합성을 통과해 저장본에 쓰레기 화소로 남습니다.
        """
        from arw_selector.core.develop.masks import build_mask_alpha

        mask = Mask(
            kind=MaskType.RADIAL, size=size,
            adjust=LocalAdjustments(exposure=2.0),
            params={"cx": 0.5, "cy": 0.5, "rx": 0.3, "ry": 0.3},
        )
        alpha = build_mask_alpha(mask, image.shape, image, None)
        assert alpha is not None
        assert np.all(np.isfinite(alpha)), "마스크 알파에 NaN/inf가 있습니다"

        for invert in (False, True):
            result = engine.apply_settings(
                image, DevelopSettings(masks=(replace(mask, invert=invert),))
            )
            assert result.dtype == np.uint8
            assert np.all(np.isfinite(result.astype(np.float32)))

    def test_exif_strip_with_huge_height(self, image, tmp_path):
        settings = DevelopSettings(
            exif_strip=ExifStripSettings(
                enabled=True, height_percent=100.0, custom_text="A" * 200
            )
        )
        result = engine.apply_settings(
            image, settings, tmp_path / "x.ARW", None
        )
        assert result.shape[1] == image.shape[1]


class TestScoringRobustness:
    def test_empty_batch(self):
        assert scoring.grade_records([]) == []
        assert scoring.summarize([]) == {"keep": 0, "review": 0, "reject": 0}
        assert scoring.groups_without_keep([]) == set()

    def test_all_failed_records(self, tmp_path):
        records = [
            ImageRecord(path=tmp_path / f"{i}.ARW", error="손상") for i in range(5)
        ]
        scoring.grade_records(records)
        assert all(r.grade is Grade.REJECT for r in records)

    @pytest.mark.parametrize(
        "config",
        [
            ScoreConfig(target_keep_ratio=0.0),
            ScoreConfig(target_keep_ratio=1.0),
            ScoreConfig(target_keep_ratio=-0.5),
            ScoreConfig(target_keep_ratio=5.0),
            ScoreConfig(keep_per_group=0, target_keep_ratio=None),
            ScoreConfig(reject_percentile=100.0),
            ScoreConfig(trust_eye=5.0, trust_frame=-3.0),
        ],
    )
    def test_extreme_score_configs(self, config, tmp_path):
        from tests.test_scoring import make_record

        records = [make_record(f"{i}.ARW", 50.0, group_id=i % 3) for i in range(9)]
        scoring.grade_records(records, config)
        assert all(0.0 <= r.score <= 100.0 for r in records)

    def test_grouping_with_broken_records(self, tmp_path):
        records = [
            ImageRecord(path=tmp_path / "a.ARW", error="손상"),
            ImageRecord(path=tmp_path / "b.ARW", dhash=None),
        ]
        grouping.assign_groups(records, GroupConfig())
        assert all(r.group_id is not None for r in records)


class TestExportRobustness:
    def test_empty_records(self, tmp_path):
        result = export.export_records([], tmp_path)
        assert result.moved == 0

    def test_all_missing_sources(self, tmp_path):
        records = []
        for i in range(3):
            record = ImageRecord(path=tmp_path / f"없음{i}.ARW")
            record.grade = Grade.KEEP
            records.append(record)

        result = export.export_records(records, tmp_path)
        assert result.moved == 0

    def test_readonly_destination_is_reported(self, tmp_path):
        """쓸 수 없는 위치를 지정해도 예외가 새어나가면 안 됩니다."""
        source = tmp_path / "a.ARW"
        source.write_bytes(b"x")
        record = ImageRecord(path=source)
        record.grade = Grade.KEEP

        # 파일을 폴더 자리에 두어 mkdir가 실패하게 만듭니다
        blocker = tmp_path / "출력"
        blocker.write_bytes(b"not a directory")

        result = export.export_records([record], blocker)
        assert result.failed or result.moved == 0

    def test_undo_of_missing_log(self, tmp_path):
        with pytest.raises((OSError, ValueError)):
            export.undo_export(tmp_path / "없는로그.json")

    def test_corrupt_undo_log(self, tmp_path):
        log = tmp_path / "export_bad.json"
        log.write_text("{ 깨진 JSON", encoding="utf-8")
        with pytest.raises(ValueError):
            export.undo_export(log)


class TestQueueRobustness:
    def test_load_of_garbage(self, tmp_path):
        path = tmp_path / "queue.json"
        path.write_text("not json at all", encoding="utf-8")
        with pytest.raises(ValueError):
            ExportQueue.load(path)

    def test_load_of_empty_object(self, tmp_path):
        path = tmp_path / "queue.json"
        path.write_text("{}", encoding="utf-8")
        assert len(ExportQueue.load(path)) == 0

    def test_remove_from_empty(self, tmp_path):
        assert ExportQueue().remove([tmp_path / "x.ARW"]) == 0


class TestConfigRobustness:
    @pytest.mark.parametrize(
        "content",
        ["", "not: [valid", "just a string", "123", "- 리스트\n- 항목"],
    )
    def test_broken_config_files(self, tmp_path, content):
        path = tmp_path / "config.yaml"
        path.write_text(content, encoding="utf-8")
        try:
            config = Config.load(path)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"설정 로딩이 예외를 던졌습니다: {exc}")
        assert config.analyze.detect_long_edge > 0

    def test_settings_from_garbage_dict(self):
        for data in ({}, {"basic": "문자열"}, {"unknown": 1}, {"basic": None}):
            settings = DevelopSettings.from_dict(data)
            assert isinstance(settings, DevelopSettings)

    def test_export_options_from_garbage(self):
        options = ExportOptions.from_dict({"image_format": 12345, "quality": "높음"})
        assert options.image_format is not None
