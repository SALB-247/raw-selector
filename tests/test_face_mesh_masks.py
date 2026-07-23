"""FaceMesh 윤곽 마스크 — 실제로 고쳐진 두 가지를 잠급니다.

1. '피부만' 마스크가 눈·눈썹·입을 **정말로** 제외하는가.
   예전에는 구멍을 판 뒤 얼굴 크기 기준으로 페더를 걸어(얼굴 400px이면
   시그마 88px) 눈만 한 구멍이 씻겨 나갔습니다. 그래서 덮인 면적이 일반
   얼굴 마스크와 소수점까지 같았습니다.
2. 창(window) 안에서만 흐리는 최적화가 결과를 바꾸지 않는가.
   전체 프레임 가우시안은 6192×4128에서 마스크 하나에 0.9초였습니다.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from arw_selector.core import face_mesh
from arw_selector.core.develop import masks as masks_mod
from arw_selector.core.develop.settings import Mask, MaskType


def _face_row(x=200.0, y=200.0, w=400.0, h=440.0) -> np.ndarray:
    """YuNet 한 줄(x,y,w,h + 랜드마크 5쌍 + score)을 흉내 냅니다."""
    eye_y = y + h * 0.36
    return np.array([
        x, y, w, h,
        x + w * 0.32, eye_y,          # 오른쪽 눈
        x + w * 0.68, eye_y,          # 왼쪽 눈
        x + w * 0.50, y + h * 0.56,   # 코
        x + w * 0.38, y + h * 0.74,   # 오른쪽 입꼬리
        x + w * 0.62, y + h * 0.74,   # 왼쪽 입꼬리
        0.99,
    ], np.float64)


# ------------------------------------------------------- 구멍이 살아남는가


def test_skin_mask_actually_excludes_features():
    """'피부만'은 눈·입 **자리에서** 값이 죽어 있어야 합니다.

    예전 구현은 전체 면적이 일반 얼굴 마스크와 소수점까지 같았습니다
    (둘 다 33.75%) — 구멍을 판 뒤 얼굴 크기 기준 페더를 걸어 씻어냈기
    때문입니다. 면적 비교만으로는 잡기 어려우니(이목구비는 얼굴의 10%
    남짓입니다) 지점 값을 직접 봅니다.
    """
    face = _face_row()
    mask = Mask(kind=MaskType.FACE, params={"region": "skin"},
                size=100, opacity=100, feather=55)
    skin = masks_mod._face_alpha(mask, face, 900, 900, None)
    assert skin is not None

    right_eye = (int(face[5]), int(face[4]))
    left_eye = (int(face[7]), int(face[6]))
    mouth = (int((face[11] + face[13]) / 2), int((face[10] + face[12]) / 2))
    cheek = (int(face[1] + face[3] * 0.62), int(face[0] + face[2] * 0.20))

    for label, point in (("오른쪽 눈", right_eye), ("왼쪽 눈", left_eye),
                         ("입", mouth)):
        assert skin[point] < 0.15, (
            f"{label} 자리가 피부 마스크에 남아 있습니다 ({skin[point]:.3f})")
    assert skin[cheek] > 0.7, "정작 볼이 마스크에서 빠졌습니다"


def test_hole_survives_large_outer_feather():
    """바깥 경계를 아무리 부드럽게 해도 구멍의 중심은 뚫려 있어야 합니다."""
    fill = [masks_mod._ellipse_poly((300, 300), (200, 200))]
    hole = [masks_mod._ellipse_poly((300, 300), (40, 40))]
    shapes = masks_mod._Shapes(fill, 200.0, hole, 40.0)

    alpha = masks_mod._rasterise(shapes, 100, 600, 600)
    assert alpha is not None
    assert alpha[300, 300] < 0.05, "구멍 중심이 페더에 씻겨 나갔습니다"
    assert alpha[300, 150] > 0.3, "정작 채워야 할 곳이 비었습니다"


# ------------------------------------------------------- 창 최적화 등가성


def _rasterise_whole_frame(shapes, feather, h, w):
    """최적화 전 방식 — 전체 프레임에 그대로 그리고 흐립니다."""
    alpha = np.zeros((h, w), np.float32)
    cv2.fillPoly(alpha, list(shapes.fill), 1.0)
    sigma = masks_mod._sigma(feather, shapes.reference)
    if sigma >= 0.6:
        alpha = cv2.GaussianBlur(alpha, (0, 0), sigma)
    if shapes.holes:
        holes = np.zeros((h, w), np.float32)
        cv2.fillPoly(holes, list(shapes.holes), 1.0)
        hole_sigma = min(masks_mod._sigma(feather, shapes.hole_reference),
                         shapes.hole_reference / 3.0)
        if hole_sigma >= 0.6:
            holes = cv2.GaussianBlur(holes, (0, 0), hole_sigma)
        alpha *= 1.0 - holes
    return np.clip(alpha, 0.0, 1.0)


@pytest.mark.parametrize("region", ["skin", "mouth", "brow"])
def test_window_matches_whole_frame(region):
    """창 안에서만 흐린 결과가 전체 프레임 결과와 같아야 합니다."""
    shapes = masks_mod._box_face_shapes(region, _face_row(), 1.0)
    windowed = masks_mod._rasterise(shapes, 50, 1200, 1200)
    reference = _rasterise_whole_frame(shapes, 50, 1200, 1200)

    assert windowed is not None
    # 창 가장자리는 3σ 밖이라 값이 거의 0입니다. 그래도 눈에 보일 만한
    # 차이(1/255)는 없어야 합니다.
    assert np.abs(windowed - reference).max() < 1.0 / 255.0


def test_window_is_smaller_than_frame():
    """최적화가 실제로 작동하는지 — 마스크 밖은 손도 대지 않아야 합니다."""
    shapes = masks_mod._box_face_shapes("skin", _face_row(), 1.0)
    alpha = masks_mod._rasterise(shapes, 40, 3000, 3000)
    assert alpha is not None
    assert alpha[2900:, 2900:].max() == 0.0


# ------------------------------------------------------- 윤곽 변형 규칙


def test_contour_shift_is_relative_to_original_height():
    """범위를 키워도 마스크가 얼굴 아래로 흘러내리면 안 됩니다.

    늘린 뒤 높이를 기준으로 밀면 범위 200%에서 이동량이 두 배가 됩니다.
    """
    points = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0],
                       [10.0, 10.0, 0.0], [0.0, 10.0, 0.0]])
    indices = (0, 1, 2, 3)

    small = masks_mod._contour(points, indices, 1.0, 1.0, shift_y=1.0)
    large = masks_mod._contour(points, indices, 2.0, 2.0, shift_y=1.0)

    # 중심은 둘 다 원래 중심 + 원래 높이(10)만큼 아래
    assert small[:, 1].mean() == pytest.approx(15.0, abs=0.5)
    assert large[:, 1].mean() == pytest.approx(15.0, abs=0.5)


def test_size_zero_does_not_produce_nan():
    """범위 0%에서도 NaN이 섞이면 안 됩니다 — 합성에서 그대로 살아남습니다."""
    mask = Mask(kind=MaskType.FACE, params={"region": "skin"},
                size=0, opacity=100, feather=50)
    alpha = masks_mod._face_alpha(mask, _face_row(), 900, 900, None)
    if alpha is not None:
        assert np.isfinite(alpha).all()


# ------------------------------------------------------- 모델 없을 때


def test_falls_back_when_model_missing(monkeypatch):
    """모델이 없어도 마스크는 나와야 합니다(예전 타원 방식)."""
    monkeypatch.setattr(face_mesh, "available", lambda: False)
    mask = Mask(kind=MaskType.EYE, params={"region": "under_eye"},
                size=100, opacity=100, feather=50)
    alpha = masks_mod._eye_alpha(mask, _face_row(), 900, 900,
                                 np.zeros((900, 900, 3), np.uint8))
    assert alpha is not None and alpha.max() > 0.5


def test_landmarks_reject_tiny_crop():
    """너무 작은 얼굴은 랜드마크를 내지 않고 None으로 물러섭니다."""
    image = np.zeros((40, 40, 3), np.uint8)
    assert face_mesh.landmarks(image, (0.0, 0.0, 4.0, 4.0)) is None


# ------------------------------------------------------- EAR


def test_eye_aspect_ratio_drops_when_closed():
    """감은 눈의 EAR이 뜬 눈보다 작아야 합니다 — 부호가 뒤집히면 감점이 반대."""
    points = np.zeros((468, 3))

    def place(indices, height):
        x0, x1 = 0.0, 30.0
        p0, p1, p2, p3, p4, p5 = indices
        points[p0] = (x0, 0.0, 0.0)
        points[p3] = (x1, 0.0, 0.0)
        points[p1] = (10.0, -height, 0.0)
        points[p2] = (20.0, -height, 0.0)
        points[p5] = (10.0, height, 0.0)
        points[p4] = (20.0, height, 0.0)

    place(face_mesh.LEFT_EAR_POINTS, 6.0)
    place(face_mesh.RIGHT_EAR_POINTS, 6.0)
    wide = face_mesh.eye_aspect_ratio(points)

    place(face_mesh.LEFT_EAR_POINTS, 0.5)
    place(face_mesh.RIGHT_EAR_POINTS, 0.5)
    narrow = face_mesh.eye_aspect_ratio(points)

    assert narrow < wide
    assert narrow < 0.10 < wide


# ------------------------------------------------------- 인덱스 무결성


@pytest.mark.parametrize("name", [
    "FACE_OVAL", "LEFT_EYE", "RIGHT_EYE", "LEFT_BROW", "RIGHT_BROW",
    "LIPS", "INNER_LIPS", "LEFT_EAR_POINTS", "RIGHT_EAR_POINTS",
])
def test_contour_indices_in_range(name):
    """468점 모델의 범위를 벗어난 인덱스는 IndexError로 터집니다."""
    indices = getattr(face_mesh, name)
    assert indices, f"{name}이 비었습니다"
    assert all(0 <= i < 468 for i in indices)
    assert len(set(indices)) == len(indices), f"{name}에 중복 인덱스"
