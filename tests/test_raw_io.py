"""raw_io.py 단위 테스트 (ARW 파일 불필요한 부분만)."""

from __future__ import annotations

import numpy as np
import pytest

from arw_selector.core import raw_io


class TestUnicodeImageIO:
    """한글·비ASCII 경로 이미지 입출력 — cv2.imwrite/imread는 여기서 실패합니다."""

    def test_write_and_read_korean_path(self, tmp_path):
        import cv2

        folder = tmp_path / "카카오톡 받은 사진"
        folder.mkdir()
        dest = folder / "썸네일.jpg"
        rng = np.random.default_rng(0)
        img = rng.integers(0, 256, (60, 80, 3), dtype=np.uint8)

        assert raw_io.imwrite_unicode(dest, img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        assert dest.exists() and dest.stat().st_size > 0

        loaded = raw_io.imread_unicode(dest)
        assert loaded is not None
        assert loaded.shape == img.shape

    def test_cv2_would_fail_here(self, tmp_path):
        """이 경로에서 cv2.imwrite가 실패함을 확인 (회귀 방지의 근거)."""
        import cv2

        dest = tmp_path / "한글" / "x.jpg"
        dest.parent.mkdir()
        img = np.zeros((10, 10, 3), np.uint8)
        # cv2 원본은 False(또는 예외)로 실패하지만 우리 헬퍼는 성공해야 합니다
        assert raw_io.imwrite_unicode(dest, img) is True

    def test_imread_missing_returns_none(self, tmp_path):
        assert raw_io.imread_unicode(tmp_path / "없음.jpg") is None


class TestEstimateBlackLevel:
    """LibRaw이 최신 미지원 기종의 블랙 페데스탈을 놓쳤을 때 센서에서 추정."""

    class _FakeRaw:
        def __init__(self, black, sensor):
            self.black_level_per_channel = black
            self.raw_image_visible = sensor

    def _sensor(self, floor, spread=200):
        rng = np.random.default_rng(0)
        return rng.integers(floor, floor + spread, size=(300, 300), dtype=np.uint16)

    def test_supported_camera_not_overridden(self):
        # 지원 기종: 보고 블랙이 센서 바닥과 맞음 → 건드리지 않음
        raw = self._FakeRaw([2048, 2048, 2048, 2048], self._sensor(2050))
        assert raw_io._repair_black_level(raw) is None

    def test_unsupported_camera_estimated(self):
        # 미지원 기종(R6 III): 보고 블랙이 0 근처인데 센서 바닥은 ~2027
        raw = self._FakeRaw([0, 38, 113, 78], self._sensor(2020))
        override = raw_io._repair_black_level(raw)
        assert override is not None
        assert 1990 <= override <= 2060, f"추정 블랙 {override}"

    def test_empty_or_bad_input(self):
        assert raw_io._repair_black_level(self._FakeRaw([], self._sensor(2000))) is None
        broken = self._FakeRaw([2048] * 4, None)
        assert raw_io._repair_black_level(broken) is None  # 예외 → None

    def test_bright_scene_consistent_black_not_overridden(self):
        """밝은 장면(진짜 검정 없음)에 균일한 블랙 0 → 오탐하면 안 됩니다.

        페데스탈만 보면 센서 바닥이 높아 오판할 수 있지만, 채널이 균일하면
        (LibRaw이 제대로 읽은 것) 건드리지 않아야 합니다.
        """
        # 균일한 블랙 [0,0,0,0]인데 장면이 밝아 센서 바닥이 800
        raw = self._FakeRaw([0, 0, 0, 0], self._sensor(800, spread=3000))
        assert raw_io._repair_black_level(raw) is None


class TestSpuriousChannelBlack:
    """LibRaw의 채널별 블랙이 허수일 때만 상쇄해야 합니다.

    실측 배경: EOS R6 Mark III에서 LibRaw이 페데스탈(~2000)을 통째로 놓치고
    [0,38,113,78]을 내놓습니다. user_black은 전역 블랙만 바꾸므로 이 채널
    값이 그 위에 그대로 더 빠져 파랑만 113을 더 잃고, 노란-초록으로 뜹니다
    (카메라 JPEG 대비 B 0.750 → 0.468).

    반대로 이 상쇄를 정상 기종에 걸면 크게 틀어집니다(R6 Mark II 오차
    0.109 → 0.785). 그래서 '허수인지' 판별이 핵심입니다.
    """

    class _FakeRaw:
        def __init__(self, black, floors, size=240):
            rng = np.random.default_rng(1)
            self.black_level_per_channel = list(black)
            self.white_level = 16383
            # 2x2 베이어 배치
            pattern = np.array([[0, 1], [3, 2]], dtype=np.uint8)
            self.raw_colors = np.tile(pattern, (size // 2, size // 2))
            self.raw_colors_visible = self.raw_colors
            image = np.zeros((size, size), dtype=np.uint16)
            for index, floor in enumerate(floors):
                mask = self.raw_colors == index
                image[mask] = rng.integers(
                    floor, floor + 400, size=int(mask.sum()), dtype=np.uint16
                )
            self.raw_image = image
            self.raw_image_visible = image

    def test_spurious_spread_is_compensated(self):
        """보고된 채널 차이가 크고 실측은 균일 → 허수. 화소로 상쇄합니다."""
        raw = self._FakeRaw([0, 38, 113, 78], [2000, 2005, 1998, 2004])
        before = raw.raw_image[raw.raw_colors == 2].mean()

        override = raw_io._repair_black_level(raw)

        after = raw.raw_image[raw.raw_colors == 2].mean()
        assert override is not None
        # 전역 블랙은 min(cblack)=0 만큼 낮춰 둡니다
        assert 1990 <= override <= 2060, f"블랙 {override}"
        assert after > before, "파랑 채널을 상쇄하지 않았습니다"
        assert after - before == pytest.approx(113, abs=1)

    def test_genuine_offsets_are_left_alone(self):
        """실측 채널 차이가 보고값과 비슷하면 진짜 오프셋 — 건드리지 않습니다."""
        raw = self._FakeRaw([0, 38, 113, 78], [2000, 2040, 2115, 2080])
        before = raw.raw_image[raw.raw_colors == 2].mean()

        override = raw_io._repair_black_level(raw)

        assert override is not None
        assert raw.raw_image[raw.raw_colors == 2].mean() == before, (
            "진짜 오프셋인데 화소를 건드렸습니다"
        )

    def test_supported_body_untouched(self):
        """정상 기종에는 어떤 개입도 없어야 합니다."""
        raw = self._FakeRaw([2048] * 4, [2050, 2052, 2049, 2052])
        before = raw.raw_image.copy()

        assert raw_io._repair_black_level(raw) is None
        assert np.array_equal(raw.raw_image, before)

    def test_compensation_does_not_exceed_white_level(self):
        """포화 근처에 더하면 흰색이 넘칩니다."""
        raw = self._FakeRaw([0, 38, 113, 78], [2000, 2005, 1998, 2004])
        raw.raw_image[:] = raw.white_level

        raw_io._repair_black_level(raw)

        assert raw.raw_image.max() <= raw.white_level


class TestApplyOrientation:
    @pytest.fixture
    def image(self) -> np.ndarray:
        rng = np.random.default_rng(1)
        return rng.integers(0, 256, size=(40, 60, 3), dtype=np.uint8)

    def test_identity_orientations_are_noop(self, image):
        for orientation in (0, 1, 9, 99):
            assert np.array_equal(raw_io.apply_orientation(image, orientation), image)

    @pytest.mark.parametrize("orientation", [5, 6, 7, 8])
    def test_rotations_swap_axes(self, image, orientation):
        """90도 계열은 가로/세로가 뒤바뀐다 — 세로 촬영 컷의 방향 보정 근거."""
        rotated = raw_io.apply_orientation(image, orientation)
        assert rotated.shape[:2] == image.shape[1::-1]

    @pytest.mark.parametrize("orientation", [2, 3, 4])
    def test_flips_preserve_shape(self, image, orientation):
        assert raw_io.apply_orientation(image, orientation).shape == image.shape

    def test_rot180_is_self_inverse(self, image):
        once = raw_io.apply_orientation(image, 3)
        assert np.array_equal(raw_io.apply_orientation(once, 3), image)


class TestResizeLongEdge:
    def test_downscales_to_target(self):
        image = np.zeros((400, 800, 3), np.uint8)
        assert raw_io.resize_long_edge(image, 200).shape[:2] == (100, 200)

    def test_preserves_aspect_ratio_on_portrait(self):
        image = np.zeros((900, 300, 3), np.uint8)
        assert raw_io.resize_long_edge(image, 300).shape[:2] == (300, 100)

    def test_does_not_upscale(self):
        """이미 작은 이미지를 키워봐야 디테일이 늘지 않습니다."""
        image = np.zeros((50, 80, 3), np.uint8)
        assert raw_io.resize_long_edge(image, 1024).shape[:2] == (50, 80)


class TestMetadata:
    def test_shutter_display_formats_fractions(self):
        assert raw_io.RawMetadata(path=None, shutter_speed=1 / 200).shutter_display == "1/200s"

    def test_shutter_display_formats_long_exposure(self):
        assert raw_io.RawMetadata(path=None, shutter_speed=2.5).shutter_display == "2.5s"

    def test_shutter_display_handles_missing(self):
        assert raw_io.RawMetadata(path=None).shutter_display == "-"

    def test_read_metadata_survives_non_raw_file(self, tmp_path):
        """손상/비정상 파일 한 장이 4000장 배치를 죽이면 안 됩니다."""
        bogus = tmp_path / "not_a_raw.ARW"
        bogus.write_bytes(b"definitely not a raw file")
        meta = raw_io.read_metadata(bogus)
        assert meta.path == bogus
        assert meta.capture_time is None


class TestRawFormats:
    def test_supports_major_manufacturers(self):
        """A6700이 주 대상이지만 같은 흐름이 다른 기종에도 통해야 합니다."""
        for extension in (".arw", ".cr2", ".cr3", ".nef", ".raf", ".orf",
                          ".rw2", ".dng", ".pef"):
            assert extension in raw_io.RAW_EXTENSIONS

    def test_extensions_are_lowercase(self):
        """macOS는 대소문자를 구분하므로 비교는 항상 lower()로 합니다."""
        assert all(e == e.lower() for e in raw_io.RAW_EXTENSIONS)

    def test_file_filter_is_usable(self):
        assert "*.arw" in raw_io.RAW_FILE_FILTER
        assert "*.cr3" in raw_io.RAW_FILE_FILTER


class TestIterRawFiles:
    def test_finds_arw_case_insensitively(self, tmp_path):
        """Windows는 대소문자를 무시하지만 macOS는 구분합니다.

        0.15부터 짝 없는 JPEG도 함께 잡힙니다(RAW를 안 찍는 사람들). 여기서
        보려는 것은 대소문자 처리이므로 RAW만 세어 확인합니다 — JPEG 규칙은
        test_jpeg_source.py 가 따로 봅니다.
        """
        (tmp_path / "a.ARW").touch()
        (tmp_path / "b.arw").touch()
        (tmp_path / "c.jpg").touch()

        found = raw_io.iter_raw_files(tmp_path)
        assert len([p for p in found if raw_io.is_raw(p)]) == 2

    def test_finds_other_raw_formats(self, tmp_path):
        for name in ("a.CR3", "b.NEF", "c.RAF", "d.txt"):
            (tmp_path / name).touch()
        assert len(raw_io.iter_raw_files(tmp_path)) == 3

    def test_skips_export_output_dirs(self, tmp_path):
        """_keep 등을 다시 스캔하면 원본을 중복 처리하게 됩니다."""
        (tmp_path / "original.ARW").touch()
        keep = tmp_path / "_keep"
        keep.mkdir()
        (keep / "original.ARW").touch()

        found = raw_io.iter_raw_files(tmp_path)
        assert [p.name for p in found] == ["original.ARW"]
        assert found[0].parent == tmp_path

    def test_returns_sorted(self, tmp_path):
        for name in ["DSC003.ARW", "DSC001.ARW", "DSC002.ARW"]:
            (tmp_path / name).touch()
        assert [p.name for p in raw_io.iter_raw_files(tmp_path)] == [
            "DSC001.ARW",
            "DSC002.ARW",
            "DSC003.ARW",
        ]
