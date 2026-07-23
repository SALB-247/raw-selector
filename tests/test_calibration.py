"""기종 색 보정 단위 테스트.

핵심 보장: 보정값이 이 PC 밖으로 나가지 않고, 이상한 값은 저장·적용되지
않으며, 이미 보정된 결과로 다시 보정을 구하지 않는다(되먹임 방지).
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from arw_selector.core.develop import calibration as calib


class TestCameraKey:
    def test_canon_model_already_has_make(self):
        """캐논은 모델에 제조사가 들어 있어 두 번 붙이면 안 됩니다."""
        assert calib.camera_key("Canon", "Canon EOS R6 Mark III") == (
            "Canon_EOS_R6_Mark_III"
        )

    def test_sony_make_and_model_are_joined(self):
        assert calib.camera_key("SONY", "ILCE-6700") == "SONY_ILCE-6700"

    def test_missing_values(self):
        assert calib.camera_key(None, None) == ""
        assert calib.camera_key("", "  ") == ""

    def test_path_unsafe_characters_removed(self):
        key = calib.camera_key("Maker", 'X/Y:Z*?"<>|')
        assert not set(key) & set('/\\:*?"<>| ')


class TestStorage:
    @pytest.fixture(autouse=True)
    def local_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(calib, "calibration_dir", lambda: tmp_path)
        return tmp_path

    def _sample(self, gain=(0.97, 1.01, 1.02)):
        return calib.CameraCalibration(
            camera="Canon EOS R6 Mark III", gain=gain, samples=8,
            created="2026-07-21T20:00:00", app_version="0.13.0",
        )

    def test_round_trip(self, local_dir):
        saved = calib.save(self._sample())
        assert saved is not None and saved.parent == local_dir

        loaded = calib.load("Canon_EOS_R6_Mark_III")
        assert loaded is not None
        assert loaded.gain == pytest.approx((0.97, 1.01, 1.02))
        assert loaded.samples == 8

    def test_missing_returns_none(self):
        assert calib.load("없는기종") is None
        assert calib.load("") is None

    def test_corrupt_file_is_ignored(self, local_dir):
        (local_dir / "깨진것.json").write_text("{ not json", encoding="utf-8")
        assert calib.load("깨진것") is None
        assert calib.stored_cameras() == []

    def test_absurd_gain_is_rejected(self, local_dir):
        """이득이 2배를 넘으면 측정이 잘못된 것입니다. 사진을 망치면 안 됩니다."""
        (local_dir / "이상한것.json").write_text(
            json.dumps({"camera": "이상한것", "gain": [0.1, 1.0, 9.0]}),
            encoding="utf-8",
        )
        assert calib.load("이상한것") is None

    def test_remove(self, local_dir):
        calib.save(self._sample())
        assert calib.remove("Canon_EOS_R6_Mark_III") is True
        assert calib.load("Canon_EOS_R6_Mark_III") is None
        assert calib.remove("Canon_EOS_R6_Mark_III") is False


class TestApply:
    def test_neutral_returns_input_unchanged(self):
        image = np.full((4, 4, 3), 100, np.float32)
        neutral = calib.CameraCalibration(camera="x", gain=(1.0, 1.0, 1.0))

        assert calib.apply(image, neutral) is image
        assert calib.apply(image, None) is image

    def test_gain_is_applied_per_channel(self):
        image = np.full((4, 4, 3), 100.0, np.float32)
        item = calib.CameraCalibration(camera="x", gain=(0.9, 1.0, 1.1))

        out = calib.apply(image, item)

        assert out[:, :, 0].mean() == pytest.approx(90.0)
        assert out[:, :, 1].mean() == pytest.approx(100.0)
        assert out[:, :, 2].mean() == pytest.approx(110.0)


class TestEmbeddedPreview:
    """내장 미리보기는 JPEG만 있는 게 아닙니다.

    rawpy는 JPEG 또는 BITMAP(비압축 RGB)을 돌려주고, 아예 없는 파일도
    있습니다(변환기가 미리보기를 떼어 낸 DNG 등). JPEG만 받게 짜 두면
    BITMAP 기종에서 보정이 통째로 안 되고, 없는 파일에서는 예외가 샙니다.
    """

    class _Thumb:
        def __init__(self, fmt, data):
            self.format = fmt
            self.data = data

    class _FakeRaw:
        def __init__(self, thumb):
            self._thumb = thumb

        def extract_thumb(self):
            if self._thumb is None:
                raise RuntimeError("no thumbnail")
            return self._thumb

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def _patch(self, monkeypatch, thumb):
        import rawpy

        monkeypatch.setattr(
            rawpy, "imread", lambda _path: self._FakeRaw(thumb), raising=True
        )

    def test_bitmap_thumbnail_is_accepted(self, monkeypatch):
        import rawpy

        array = np.zeros((8, 12, 3), np.uint8)
        array[:, :, 0] = 200  # RGB 기준 빨강
        self._patch(monkeypatch, self._Thumb(rawpy.ThumbFormat.BITMAP, array))

        image = calib.embedded_preview("x.dng")

        assert image is not None
        assert image.shape == (8, 12, 3)
        # BGR로 뒤집혀야 합니다 — 빨강이 마지막 채널
        assert image[:, :, 2].mean() == pytest.approx(200)
        assert image[:, :, 0].mean() == pytest.approx(0)

    def test_missing_thumbnail_returns_none(self, monkeypatch):
        self._patch(monkeypatch, None)

        assert calib.embedded_preview("x.dng") is None
        assert calib.has_embedded_preview("x.dng") is False

    def test_broken_bitmap_returns_none(self, monkeypatch):
        import rawpy

        # 흑백 2차원 배열 — 채널이 없어 쓸 수 없습니다
        self._patch(
            monkeypatch,
            self._Thumb(rawpy.ThumbFormat.BITMAP, np.zeros((8, 12), np.uint8)),
        )

        assert calib.embedded_preview("x.dng") is None

    def test_files_without_preview_are_not_offered(self, monkeypatch, tmp_path):
        """미리보기가 없는 파일만 있으면 보정을 권하지 않아야 합니다.

        걸러 두지 않으면 계산을 시작한 뒤에야 '표본 부족'으로 실패합니다.
        """
        monkeypatch.setattr(calib, "load", lambda key: None)
        monkeypatch.setattr(calib, "looks_unsupported", lambda path: True)
        monkeypatch.setattr(calib, "has_embedded_preview", lambda path: False)

        class _Meta:
            camera_make = "Maker"
            camera_model = "새기종"

        monkeypatch.setattr(
            "arw_selector.core.raw_io.read_metadata", lambda path: _Meta()
        )

        paths = [tmp_path / f"a{i}.dng" for i in range(10)]
        assert calib.find_uncalibrated(paths) is None


class TestMeasure:
    def test_too_few_samples_returns_none(self, monkeypatch):
        """한두 장으로 낸 값은 그 장면의 색조입니다. 저장하면 안 됩니다."""
        monkeypatch.setattr(
            calib, "sample_gain", lambda path: np.array([1.0, 1.0, 1.05])
        )
        paths = [f"a{i}.CR3" for i in range(calib.MIN_SAMPLES - 1)]

        assert calib.measure(paths, "테스트") is None

    def test_median_ignores_one_odd_sample(self, monkeypatch):
        """역광 한 장이 섞여도 나머지가 멀쩡하면 값이 끌려가면 안 됩니다."""
        values = [
            np.array([0.95, 1.0, 1.05]),
            np.array([0.95, 1.0, 1.05]),
            np.array([0.96, 1.0, 1.04]),
            np.array([0.95, 1.0, 1.05]),
            np.array([1.45, 1.0, 0.60]),  # 이상값
        ]
        it = iter(values)
        monkeypatch.setattr(calib, "sample_gain", lambda path: next(it))

        result = calib.measure([f"a{i}" for i in range(5)], "테스트")

        assert result is not None
        assert result.gain[0] == pytest.approx(0.95, abs=0.02)
        assert result.gain[2] == pytest.approx(1.05, abs=0.02)

    def test_negligible_drift_is_marked_neutral(self, monkeypatch):
        """차이가 거의 없으면 굳이 보정하지 않습니다."""
        monkeypatch.setattr(
            calib, "sample_gain", lambda path: np.array([1.001, 1.0, 0.999])
        )

        result = calib.measure([f"a{i}" for i in range(6)], "테스트")

        assert result is not None
        assert result.is_neutral()

    def test_cancel_stops_and_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            calib, "sample_gain", lambda path: np.array([0.9, 1.0, 1.1])
        )

        assert calib.measure(
            [f"a{i}" for i in range(8)], "테스트", should_cancel=lambda: True
        ) is None

    def test_progress_is_reported(self, monkeypatch):
        monkeypatch.setattr(
            calib, "sample_gain", lambda path: np.array([0.9, 1.0, 1.1])
        )
        seen = []

        calib.measure([f"a{i}" for i in range(5)], "테스트",
                      progress=lambda done, total: seen.append((done, total)))

        assert seen[0] == (1, 5)
        assert seen[-1] == (5, 5)

    def test_gain_is_normalised_so_brightness_is_untouched(self, monkeypatch):
        """이득은 균형만 바꿔야 합니다. 전체가 밝아지거나 어두워지면 안 됩니다."""
        monkeypatch.setattr(
            calib, "sample_gain", lambda path: np.array([1.2, 1.2, 1.2])
        )

        result = calib.measure([f"a{i}" for i in range(6)], "테스트")

        assert result is not None
        assert result.is_neutral(), f"밝기까지 바꾸고 있습니다: {result.gain}"
