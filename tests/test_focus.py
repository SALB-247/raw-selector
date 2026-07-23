"""focus.py 단위 테스트.

ARW 파일 없이 합성 이미지로 지표의 핵심 성질을 고정합니다.
가장 중요한 성질은 "블러가 심해지면 점수가 반드시 내려갑니다"입니다.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from arw_selector.core import focus
from arw_selector.core.types import FocusSource

BLUR_SIGMAS = [0.0, 0.5, 1.0, 2.0, 4.0, 8.0]


def _synthetic_photo(height: int = 600, width: int = 900, seed: int = 20260720) -> np.ndarray:
    """사진과 비슷한 주파수 분포를 갖는 합성 BGR 이미지를 만듭니다.

    일부러 순수 백색 노이즈를 쓰지 않습니다. 백색 노이즈는 에너지가 Nyquist
    한계에 몰려 있어 자연 이미지와 통계가 정반대이고, 아주 약한 블러(sigma
    0.5)에서 정규화 지표가 비단조로 튀는 병리적 케이습니다. 실제 ARW 프리뷰로
    측정하면 단조성이 유지됩니다. 그래서 픽스처는 그라디언트 + 도형 + 다양한
    굵기의 선처럼 광대역 스펙트럼을 갖도록 구성합니다.
    """
    rng = np.random.default_rng(seed)

    gradient = np.linspace(30, 200, width, dtype=np.float32)
    image = np.repeat(gradient[None, :], height, axis=0)
    image = np.dstack([image, image * 0.85, image * 0.7]).astype(np.uint8)

    for _ in range(12):
        x, y = rng.integers(0, width - 80), rng.integers(0, height - 80)
        w, h = rng.integers(30, 80), rng.integers(30, 80)
        color = tuple(int(c) for c in rng.integers(0, 256, 3))
        cv2.rectangle(image, (x, y), (x + w, y + h), color, -1)

    for _ in range(10):
        center = (int(rng.integers(0, width)), int(rng.integers(0, height)))
        color = tuple(int(c) for c in rng.integers(0, 256, 3))
        cv2.circle(image, center, int(rng.integers(10, 45)), color, -1)

    # 굵기가 다른 선들이 여러 주파수 대역을 채워 줍니다
    for thickness in (1, 2, 3):
        for _ in range(15):
            p1 = (int(rng.integers(0, width)), int(rng.integers(0, height)))
            p2 = (int(rng.integers(0, width)), int(rng.integers(0, height)))
            color = tuple(int(c) for c in rng.integers(0, 256, 3))
            cv2.line(image, p1, p2, color, thickness)

    grain = rng.normal(0, 6, image.shape)
    return np.clip(image.astype(np.float32) + grain, 0, 255).astype(np.uint8)


@pytest.fixture
def detailed_image() -> np.ndarray:
    """초점이 맞은 사진에 준하는, 고주파 디테일이 풍부한 합성 이미지."""
    return _synthetic_photo()


def _blur(image: np.ndarray, sigma: float) -> np.ndarray:
    return image if sigma == 0 else cv2.GaussianBlur(image, (0, 0), sigma)


class TestMeasurePatch:
    def test_uniform_patch_scores_zero(self):
        """완전 균일한 면은 초점을 판정할 근거가 없습니다."""
        assert focus.measure_patch(np.full((100, 100), 128, np.uint8)) == (0.0, 0.0)

    def test_empty_patch_scores_zero(self):
        assert focus.measure_patch(np.empty((0, 0), np.uint8)) == (0.0, 0.0)

    def test_monotonically_decreasing_with_blur(self, detailed_image):
        """ROI가 고정되면 두 지표 모두 블러에 대해 단조 감소해야 합니다."""
        gray = cv2.cvtColor(detailed_image, cv2.COLOR_BGR2GRAY)
        laplacians, tenengrads = [], []
        for sigma in BLUR_SIGMAS:
            lap, ten = focus.measure_patch(_blur(gray, sigma))
            laplacians.append(lap)
            tenengrads.append(ten)

        assert laplacians == sorted(laplacians, reverse=True), laplacians
        assert tenengrads == sorted(tenengrads, reverse=True), tenengrads

    def test_contrast_invariance(self):
        """콘트라스트만 낮춘 이미지가 초점 흐림으로 오판되면 안 됩니다.

        정규화 없이 raw Laplacian variance를 쓰면 저조도/저대비 장면이
        전부 낮은 점수를 받는데, 이것이 오판의 가장 큰 원인입니다.
        """
        rng = np.random.default_rng(7)
        base = rng.integers(0, 256, size=(300, 300), dtype=np.uint8)
        low_contrast = (base.astype(np.float32) * 0.25 + 96).astype(np.uint8)

        lap_high, ten_high = focus.measure_patch(base)
        lap_low, ten_low = focus.measure_patch(low_contrast)

        assert lap_low == pytest.approx(lap_high, rel=0.15)
        assert ten_low == pytest.approx(ten_high, rel=0.15)


class TestDarkNoiseRejection:
    """어두운 노이즈 영역이 피사체로 뽑히면 안 됩니다.

    실제로 겪은 버급니다. 어두운 무대 사진에서 빈 배경(평균밝기 5, 분산 0.9)이
    정규화 때문에 실제 피사체(분산 2000)보다 높은 점수를 받아 ROI로 뽑혔고,
    점수 95점짜리 keep이 됐습니다.
    """

    @staticmethod
    def _dark_noise(height=200, width=200, seed=1) -> np.ndarray:
        """센서 노이즈만 있는 검은 영역."""
        rng = np.random.default_rng(seed)
        return np.clip(rng.normal(5, 1, (height, width)), 0, 255).astype(np.uint8)

    def test_noise_scores_below_real_detail(self):
        noise = self._dark_noise()
        detail = cv2.cvtColor(_synthetic_photo(200, 200), cv2.COLOR_BGR2GRAY)

        _, noise_ten = focus.measure_patch(noise)
        _, detail_ten = focus.measure_patch(detail)

        assert noise_ten < detail_ten, f"노이즈 {noise_ten:.2f} >= 디테일 {detail_ten:.2f}"

    def test_signal_gate_returns_zero(self):
        """신호가 없는 영역은 판정 불가(0)로 처리합니다.

        분모에 하한만 두는 것으로는 부족했습니다. 백색 노이즈는 Laplacian
        응답 자체가 하한과 비슷해서 여전히 높은 점수로 통과했습니다.
        """
        assert focus.measure_patch(self._dark_noise()) == (0.0, 0.0)

    def test_gate_threshold_is_at_min_variance(self):
        """표준편차 5 부근이 경곕니다. 그 위는 정상 판정되어야 합니다."""
        rng = np.random.default_rng(11)
        below = np.clip(rng.normal(128, 2, (200, 200)), 0, 255).astype(np.uint8)
        above = np.clip(rng.normal(128, 25, (200, 200)), 0, 255).astype(np.uint8)

        assert focus.measure_patch(below) == (0.0, 0.0)
        assert focus.measure_patch(above)[1] > 0.0

    def test_tile_selection_prefers_the_subject(self):
        """어두운 배경 위에 밝은 피사체가 있으면 피사체를 골라야 합니다."""
        frame = np.clip(
            np.random.default_rng(2).normal(5, 1, (600, 900, 3)), 0, 255
        ).astype(np.uint8)
        # 우하단에만 실제 디테일을 넣습니다
        frame[350:550, 550:850] = _synthetic_photo(200, 300, seed=3)

        result = focus.analyze_focus(frame)
        x, y, w, h = result.roi
        center = (x + w // 2, y + h // 2)

        assert 400 < center[0] < 950, f"ROI 중심 x={center[0]} — 피사체를 벗어났다"
        assert 250 < center[1] < 650, f"ROI 중심 y={center[1]} — 피사체를 벗어났다"

    def test_dark_frame_does_not_score_high(self):
        """전체가 어두운 노이즈면 높은 점수가 나오면 안 됩니다."""
        frame = np.clip(
            np.random.default_rng(4).normal(5, 1, (600, 900, 3)), 0, 255
        ).astype(np.uint8)
        result = focus.analyze_focus(frame)
        assert result.sharpness < 25.0, f"노이즈 프레임 점수가 높다: {result.sharpness:.1f}"


class TestBackgroundTile:
    """얼굴 우선 모드의 배경 선명도 측정에 쓰는 헬퍼들."""

    def test_boxes_overlap(self):
        assert focus._boxes_overlap((0, 0, 100, 100), (50, 50, 100, 100))
        assert not focus._boxes_overlap((0, 0, 100, 100), (100, 0, 100, 100))
        assert not focus._boxes_overlap((0, 0, 100, 100), (0, 200, 100, 100))

    def _two_detail_regions(self) -> np.ndarray:
        """좌상단과 우하단에만 디테일이 있는 어두운 프레임."""
        gray = np.full((400, 600), 8, np.uint8)
        gray[0:100, 0:100] = cv2.cvtColor(_synthetic_photo(100, 100, seed=1), cv2.COLOR_BGR2GRAY)
        gray[300:400, 500:600] = cv2.cvtColor(_synthetic_photo(100, 100, seed=2), cv2.COLOR_BGR2GRAY)
        return gray

    def test_exclude_skips_the_face_region(self):
        """얼굴 박스(좌상단)를 빼면 배경 최고 선명 영역은 우하단이어야 합니다."""
        gray = self._two_detail_regions()
        box = focus._best_tile(gray, 1.0, (400, 600), exclude=(0, 0, 100, 100))
        assert box is not None
        x, y, w, h = box
        cx, cy = x + w // 2, y + h // 2
        assert cx > 400 and cy > 250, f"배경 ROI 중심 ({cx},{cy}) — 우하단이 아니다"

    def test_exclude_returns_none_when_everything_excluded(self):
        gray = self._two_detail_regions()
        assert focus._best_tile(gray, 1.0, (400, 600), exclude=(0, 0, 600, 400)) is None

    def test_measure_sharpness_detail_beats_flat(self):
        gray = np.full((200, 200), 30, np.uint8)
        gray[20:180, 20:180] = cv2.cvtColor(
            _synthetic_photo(160, 160, seed=4), cv2.COLOR_BGR2GRAY
        )
        detail = focus._measure_sharpness(gray, (20, 20, 160, 160), focus.LAPLACIAN_K, focus.TENENGRAD_K)
        flat = focus._measure_sharpness(
            np.full((200, 200), 30, np.uint8), (20, 20, 160, 160),
            focus.LAPLACIAN_K, focus.TENENGRAD_K,
        )
        assert detail > flat
        assert flat == 0.0

    def test_faceless_image_has_zero_background_sharpness(self, detailed_image):
        """얼굴이 없으면 background_sharpness는 해당 없음(0)입니다."""
        result = focus.analyze_focus(detailed_image)
        assert result.face_count == 0
        assert result.background_sharpness == 0.0


class TestGradientEnergy:
    def test_empty_patch(self):
        assert focus.gradient_energy(np.empty((0, 0), np.uint8)) == 0.0

    def test_flat_patch_has_no_energy(self):
        assert focus.gradient_energy(np.full((50, 50), 128, np.uint8)) == pytest.approx(0.0)

    def test_detail_beats_noise_without_normalization(self):
        noise = np.clip(
            np.random.default_rng(5).normal(5, 1, (200, 200)), 0, 255
        ).astype(np.uint8)
        detail = cv2.cvtColor(_synthetic_photo(200, 200), cv2.COLOR_BGR2GRAY)
        assert focus.gradient_energy(detail) > focus.gradient_energy(noise) * 10


class TestSaturate:
    def test_bounds(self):
        assert focus._saturate(0.0, 1.0) == 0.0
        assert 0.0 < focus._saturate(0.5, 1.0) < 100.0
        assert focus._saturate(1e12, 1.0) == pytest.approx(100.0, abs=1e-3)

    def test_k_is_the_midpoint(self):
        """K에서 정확히 50점이 되어야 캘리브레이션이 직관적입니다."""
        assert focus._saturate(0.053, 0.053) == pytest.approx(50.0)

    def test_monotonic(self):
        values = [focus._saturate(v, 0.05) for v in [0.01, 0.05, 0.2, 1.0, 5.0]]
        assert values == sorted(values)


class TestPickMainFace:
    """여러 얼굴 중 주 피사체는 '초점이 맞은' 얼굴이어야 합니다.

    실측 배경(EOS R6 Mark III, 2인 컷 6장): 앞쪽에 더 크게 잡힌 얼굴이
    초점에서 벗어나 있고 작은 쪽이 맞은 경우가 5장이었습니다. 면적×신뢰도만
    보던 예전 방식은 전부 흐린 쪽을 골랐습니다(선명도 7.9 vs 24.2).
    """

    def _face(self, x, y, w, h, confidence=0.95):
        """YuNet 형식의 한 줄 — 앞 4개가 박스, 마지막이 신뢰도."""
        row = np.zeros(15, dtype=np.float32)
        row[0:4] = (x, y, w, h)
        row[14] = confidence
        return row

    def _canvas(self, sharp_box, blurry_box, size=(600, 900)):
        """두 영역의 선명도가 확실히 다른 흑백 이미지."""
        rng = np.random.default_rng(7)
        image = np.full(size, 110, np.uint8)
        for box, blur in ((sharp_box, 0.0), (blurry_box, 4.0)):
            x, y, w, h = box
            patch = rng.integers(0, 255, size=(h, w), dtype=np.uint8)
            if blur:
                patch = cv2.GaussianBlur(patch, (0, 0), blur)
            image[y:y + h, x:x + w] = patch
        return image

    def test_sharp_small_face_beats_blurry_large_face(self):
        big_blurry = (60, 60, 200, 200)
        small_sharp = (500, 120, 140, 140)
        gray = self._canvas(small_sharp, big_blurry)
        faces = np.stack([
            self._face(*big_blurry),     # #0 — 더 큼
            self._face(*small_sharp),    # #1 — 더 선명
        ])

        index = focus._pick_main_face(
            faces, gray, 1.0, gray.shape,
            focus.LAPLACIAN_K, focus.TENENGRAD_K,
        )

        assert index == 1, "큰 얼굴이 흐린데도 주 피사체로 골랐습니다"

    def test_equally_sharp_falls_back_to_size(self):
        """같은 거리 단체 사진에서는 예전처럼 크고 확실한 쪽입니다."""
        rng = np.random.default_rng(3)
        gray = np.full((600, 900), 110, np.uint8)
        big = (60, 60, 220, 220)
        small = (520, 120, 130, 130)
        for x, y, w, h in (big, small):
            gray[y:y + h, x:x + w] = rng.integers(0, 255, size=(h, w), dtype=np.uint8)
        faces = np.stack([self._face(*small), self._face(*big)])

        index = focus._pick_main_face(
            faces, gray, 1.0, gray.shape,
            focus.LAPLACIAN_K, focus.TENENGRAD_K,
        )

        assert index == 1, "선명도가 같으면 큰 얼굴이어야 합니다"

    def test_single_face_is_trivially_main(self):
        gray = np.full((400, 400), 120, np.uint8)
        faces = np.stack([self._face(10, 10, 100, 100)])

        assert focus._pick_main_face(
            faces, gray, 1.0, gray.shape,
            focus.LAPLACIAN_K, focus.TENENGRAD_K,
        ) == 0

    def test_confidence_still_matters(self):
        """오검출(낮은 신뢰도)이 선명하다고 주 피사체가 되면 안 됩니다."""
        rng = np.random.default_rng(11)
        gray = np.full((600, 900), 110, np.uint8)
        real = (500, 120, 200, 200)
        false_positive = (60, 60, 200, 200)
        for x, y, w, h in (real, false_positive):
            gray[y:y + h, x:x + w] = rng.integers(0, 255, size=(h, w), dtype=np.uint8)
        faces = np.stack([
            self._face(*false_positive, confidence=0.05),
            self._face(*real, confidence=0.99),
        ])

        index = focus._pick_main_face(
            faces, gray, 1.0, gray.shape,
            focus.LAPLACIAN_K, focus.TENENGRAD_K,
        )

        assert index == 1


class TestAnalyzeFocus:
    def test_returns_valid_ranges(self, detailed_image):
        result = focus.analyze_focus(detailed_image)
        assert 0.0 <= result.sharpness <= 100.0
        assert 0.0 <= result.frame_sharpness <= 100.0
        assert 0.0 <= result.clipped_highlights <= 1.0
        assert 0.0 <= result.clipped_shadows <= 1.0
        assert isinstance(result.source, FocusSource)

    def test_roi_within_image_bounds(self, detailed_image):
        h, w = detailed_image.shape[:2]
        x, y, rw, rh = focus.analyze_focus(detailed_image).roi
        assert 0 <= x and 0 <= y
        assert x + rw <= w and y + rh <= h
        assert rw > 0 and rh > 0

    def test_frame_sharpness_monotonic_under_blur(self, detailed_image):
        """frame_sharpness는 ROI 선정과 무관하게 항상 비교 가능해야 합니다.

        ROI 기반 sharpness는 얼굴 검출 성패에 따라 ROI가 바뀌면 값이 튈 수
        있습니다. frame_sharpness가 그 경우의 안정적인 기준선 역할을 합니다.
        """
        scores = [
            focus.analyze_focus(_blur(detailed_image, sigma)).frame_sharpness
            for sigma in BLUR_SIGMAS
        ]
        assert scores == sorted(scores, reverse=True), scores

    def test_frame_sharpness_ignores_detect_resolution(self, detailed_image):
        """detect_long_edge를 바꿔도 frame_sharpness는 고정 스케일이라 안 변합니다."""
        a = focus.analyze_focus(detailed_image, detect_long_edge=512).frame_sharpness
        b = focus.analyze_focus(detailed_image, detect_long_edge=1024).frame_sharpness
        assert a == pytest.approx(b, abs=0.01)

    def test_flat_image_does_not_crash(self):
        """단색 프레임(렌즈캡 등)도 예외 없이 처리되어야 합니다."""
        result = focus.analyze_focus(np.full((400, 600, 3), 40, np.uint8))
        assert result.sharpness == 0.0
        assert result.face_count == 0

    def test_tiny_image_falls_back_to_frame(self):
        """ROI를 잡을 수 없을 만큼 작으면 전체 프레임으로 폴백합니다."""
        rng = np.random.default_rng(3)
        tiny = rng.integers(0, 256, size=(20, 30, 3), dtype=np.uint8)
        result = focus.analyze_focus(tiny)
        assert result.source == FocusSource.FRAME
        assert result.roi == (0, 0, 30, 20)
