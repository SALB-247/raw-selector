"""마스크(국소 보정) 렌더.

전역 보정이 끝난 이미지에, 마스크 영역만 국소 조정을 적용해 알파로 합성합니다.
미리보기와 내보내기가 같은 엔진을 쓰므로 화면과 결과가 일치합니다.

핵심 설계
  - 얼굴/눈/배경/방사형/선형 마스크는 정규화 파라미터만 저장하고 여기서
    이미지 크기에 맞춰 매번 알파를 다시 만든다 → 해상도 독립.
  - 브러시만 축소된 알파 비트맵을 들고 다니고, 여기서 이미지 크기로 늘린다.
  - 국소 연산은 알파의 bounding box 안에서만 돌려 6000×4000에서도 비용을 묶는다.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field

import cv2
import numpy as np

from .. import face_mesh
from ..focus import DETECT_LONG_EDGE, detect_faces
from .engine import (
    _IDENTITY,
    _apply_lut,
    _apply_saturation,
    _local_contrast,
    _tone_lut,
)
from .settings import BasicSettings, LocalAdjustments, Mask, MaskType

log = logging.getLogger(__name__)

_FACE_KINDS = frozenset({MaskType.FACE, MaskType.EYE, MaskType.BACKGROUND})

SIZE_KINDS = frozenset({MaskType.FACE, MaskType.EYE, MaskType.RADIAL})
"""mask.size(범위 %)가 실제로 영역을 줄이는 종류. 나머지는 무시됩니다."""


def _size_factor(mask: Mask) -> float:
    """범위 % → 도형 반경에 곱할 배율. 0~200%(기본 100)."""
    return float(np.clip(mask.size, 0, 200)) / 100.0


def _param(params: dict, key: str, default: float) -> float:
    """도형 파라미터 하나를 유한한 실수로 읽습니다.

    params는 종류마다 다른 자유 형식 dict라 dataclass의 타입 정리를 못
    거칩니다. 프리셋 YAML에 `.nan`이나 문자열이 들어오면 그대로 계산에
    실려 **알파 전체가 NaN**이 됩니다. 알파의 NaN은 합성을 그냥 통과해
    저장본에 쓰레기 화소로 남고 마스크 오버레이도 깨집니다 — 예외가 아니라
    잘못된 그림이라 알아채기 어렵습니다.
    """
    value = params.get(key, default)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if np.isfinite(number) else default


# ---------------------------------------------------------------- 얼굴 검출


def _detect_faces_full(detect_bgr: np.ndarray) -> np.ndarray | None:
    """검출은 축소본에서 하고 좌표를 원본 스케일로 되돌립니다.

    반환 좌표(0~13열: x,y,w,h,랜드마크 5쌍)는 detect_bgr 좌표계입니다.
    14열(score)은 그대로 둡니다. 면적 큰 순으로 정렬해 index 0이 주 피사쳅니다.
    """
    h, w = detect_bgr.shape[:2]
    long_edge = max(h, w)
    scale = min(1.0, DETECT_LONG_EDGE / long_edge) if long_edge else 1.0
    if scale < 1.0:
        small = cv2.resize(
            detect_bgr, (max(1, round(w * scale)), max(1, round(h * scale))),
            interpolation=cv2.INTER_AREA,
        )
    else:
        small = detect_bgr

    faces = detect_faces(small)
    if faces is None:
        return None
    faces = faces.astype(np.float64).copy()
    if scale < 1.0:
        faces[:, :14] /= scale
    order = np.argsort(-(faces[:, 2] * faces[:, 3]))  # 면적 큰 순
    return faces[order]


def _pick_face(faces: np.ndarray | None, index: int) -> np.ndarray | None:
    if faces is None or len(faces) == 0:
        return None
    return faces[min(max(0, index), len(faces) - 1)]


FACE_TARGET_MAIN = "main"
FACE_TARGET_ALL = "all"
FACE_TARGET_INDEX = "index"


def _nearest_face(faces: np.ndarray, hint: tuple[float, float, float, float],
                  h: int, w: int) -> np.ndarray:
    """정규화 힌트 상자의 중심에 가장 가까운 검출 얼굴.

    힌트는 분석 프리뷰에서 나온 좌표라 지금 이미지의 검출 결과와 크기·개수가
    다를 수 있습니다. 인덱스로 맞추면 어긋나므로 위치로 맞춥니다.
    """
    cx = (hint[0] + hint[2] / 2.0) * w
    cy = (hint[1] + hint[3] / 2.0) * h
    centres = np.stack([faces[:, 0] + faces[:, 2] / 2.0,
                        faces[:, 1] + faces[:, 3] / 2.0], axis=1)
    distance = np.hypot(centres[:, 0] - cx, centres[:, 1] - cy)
    return faces[int(np.argmin(distance))]


def select_faces(
    faces: np.ndarray | None, mask: Mask, detect_bgr: np.ndarray,
    main_face_box: tuple[float, float, float, float] | None = None,
) -> list[np.ndarray]:
    """이 마스크가 대상으로 삼을 얼굴들.

    예전에는 **면적이 가장 큰 얼굴 하나**에 무조건 걸었습니다. 단체 사진에서
    앞줄 행인이 주인공보다 크게 잡히면 엉뚱한 사람이 밝아졌고, 여러 명을
    한꺼번에 손볼 방법도 없었습니다.

    - main  : 초점 판정이 고른 주 피사체 (화면의 빨간 박스와 같은 얼굴)
    - all   : 검출된 얼굴 전부
    - index : 사용자가 고른 번호 (면적 큰 순)
    """
    if faces is None or len(faces) == 0:
        return []

    target = str(mask.params.get("target", FACE_TARGET_MAIN))
    if target == FACE_TARGET_ALL:
        return list(faces)
    if target == FACE_TARGET_INDEX:
        face = _pick_face(faces, int(mask.params.get("index", 0)))
        return [face] if face is not None else []

    # 분석이 이미 고른 얼굴이 있으면 그것을 씁니다. 여기서 다시 고르면
    # 해상도가 달라 다른 답이 나올 수 있고, 무엇보다 사용자가 화면에서
    # 주 피사체를 바꿔도 마스크만 옛 얼굴에 남습니다.
    if main_face_box is not None:
        h, w = detect_bgr.shape[:2]
        return [_nearest_face(faces, main_face_box, h, w)]

    # 힌트가 없으면 초점 쪽과 **같은 기준**으로 직접 고릅니다
    try:
        from ..focus import LAPLACIAN_K, TENENGRAD_K, _pick_main_face

        gray = cv2.cvtColor(detect_bgr, cv2.COLOR_BGR2GRAY)
        index = _pick_main_face(faces, gray, 1.0, gray.shape[:2],
                                LAPLACIAN_K, TENENGRAD_K)
        return [faces[index]]
    except Exception:  # noqa: BLE001 - 못 고르면 가장 큰 얼굴로 물러섭니다
        log.debug("주 피사체 얼굴 선정 실패", exc_info=True)
        return [faces[0]]


# ---------------------------------------------------------------- 알파 생성


def _feather(alpha: np.ndarray, feather: int, reference_px: float | None = None) -> np.ndarray:
    """경계를 가우시안으로 부드럽게 합니다.

    도형 마스크는 reference_px(그 도형의 짧은 반경)를 기준으로 번짐을 잡습니다.
    이미지 크기를 기준으로 잡으면 작은 마스크(눈밑 등)가 통째로 씻겨 나가,
    범위를 줄일수록 효과가 사라지는 문제가 생깁니다. 기준이 없는 브러시·배경만
    이미지 짧은 변 기준으로 갑니다.
    """
    h, w = alpha.shape[:2]
    if reference_px and reference_px > 0:
        sigma = max(0.0, feather) / 100.0 * float(reference_px)
    else:
        sigma = max(0.0, feather) / 100.0 * 0.05 * min(h, w)
    if sigma >= 0.6:
        alpha = cv2.GaussianBlur(alpha, (0, 0), sigma)
    return np.clip(alpha, 0.0, 1.0)


def _radial_alpha(params: dict, h: int, w: int, size: float = 1.0) -> np.ndarray:
    cx = _param(params, "cx", 0.5) * w
    cy = _param(params, "cy", 0.5) * h
    # 하한은 반경 '전체'에 걸어야 합니다. params만 막으면 범위(size) 슬라이더를
    # 0%까지 내렸을 때 곱한 결과가 0이 되어 아래 나눗셈이 0으로 나누기가 되고,
    # 알파에 NaN이 섞입니다. NaN은 합성에서 그대로 살아남아 화면과 저장본에
    # 쓰레기 화소로 남고, 마스크 오버레이도 깨집니다.
    rx = max(1e-3, _param(params, "rx", 0.3) * w * size)
    ry = max(1e-3, _param(params, "ry", 0.3) * h * size)
    angle = np.radians(_param(params, "rotation", 0.0))

    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dx, dy = xx - cx, yy - cy
    ca, sa = np.cos(angle), np.sin(angle)
    xr = (dx * ca + dy * sa) / rx
    yr = (-dx * sa + dy * ca) / ry
    dist = np.sqrt(xr * xr + yr * yr)
    # 안쪽은 꽉 찬 1, 경계까지 선형으로 0. feather는 알파 생성 뒤 따로 안 건다.
    return np.clip(1.0 - dist, 0.0, 1.0).astype(np.float32)


def _linear_alpha(params: dict, h: int, w: int) -> np.ndarray:
    x0 = _param(params, "x0", 0.5) * w
    y0 = _param(params, "y0", 0.0) * h
    x1 = _param(params, "x1", 0.5) * w
    y1 = _param(params, "y1", 0.4) * h
    dx, dy = x1 - x0, y1 - y0
    length2 = dx * dx + dy * dy
    if length2 < 1e-6:
        return np.ones((h, w), np.float32)

    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    proj = ((xx - x0) * dx + (yy - y0) * dy) / length2
    return np.clip(proj, 0.0, 1.0).astype(np.float32)


def _brush_alpha(mask: Mask, h: int, w: int) -> np.ndarray | None:
    if not mask.bitmap:
        return None
    try:
        raw = base64.b64decode(mask.bitmap)
        buffer = np.frombuffer(raw, dtype=np.uint8)
        small = cv2.imdecode(buffer, cv2.IMREAD_GRAYSCALE)
    except (ValueError, cv2.error):
        return None
    if small is None:
        return None
    alpha = cv2.resize(small.astype(np.float32) / 255.0, (w, h), interpolation=cv2.INTER_LINEAR)
    return _feather(alpha, mask.feather)


def _ellipse_poly(center, axes, angle: float = 0.0) -> np.ndarray:
    """타원을 윤곽점으로. 폴백 경로도 윤곽과 같은 래스터라이저를 타게 합니다."""
    return cv2.ellipse2Poly(
        (int(round(center[0])), int(round(center[1]))),
        (max(1, int(round(axes[0]))), max(1, int(round(axes[1])))),
        int(angle), 0, 360, 5,
    )


@dataclass
class _Shapes:
    """알파를 이루는 윤곽 목록. 래스터화는 `_rasterise`가 맡습니다.

    구멍(눈·눈썹·입)의 페더 기준을 바깥 경계와 **따로** 들고 다니는 것이
    핵심입니다. 예전 구현은 타원을 파낸 알파에 `_feather`를 한 번 걸었는데,
    그 시그마는 얼굴 크기 기준이라(얼굴 400px이면 88px) 눈만 한 45px짜리
    구멍이 흔적도 없이 씻겨 나갔습니다. 그래서 '피부만' 마스크의 덮인
    면적이 일반 얼굴 마스크와 **소수점까지 같았고**(둘 다 33.75%), 피부를
    매끄럽게 하면 눈썹과 입술이 같이 뭉개졌습니다.
    """

    fill: list[np.ndarray]
    reference: float
    holes: list[np.ndarray] = field(default_factory=list)
    hole_reference: float = 2.0


def _sigma(feather: int, reference_px: float) -> float:
    return max(0.0, feather) / 100.0 * max(2.0, float(reference_px))


def _rasterise(shapes: _Shapes | None, feather: int,
               h: int, w: int) -> np.ndarray | None:
    """윤곽을 알파로. **경계상자 창 안에서만** 흐림을 돌립니다.

    전체 프레임에 가우시안을 걸면 6192×4128에서 마스크 하나에 0.9초가
    걸립니다(실측). 얼굴은 화면의 일부일 뿐이므로 번짐 여유(3σ)만 두고
    잘라서 돌리면 수십 ms로 끝납니다.
    """
    if shapes is None or not shapes.fill:
        return None

    sigma = _sigma(feather, shapes.reference)

    # 구멍의 번짐은 구멍 반지름의 1/3로 묶습니다. 시그마가 반지름에 가까워지면
    # 가우시안이 구멍 자체를 메워 버려서(반지름 40px·시그마 40px이면 중심
    # 알파가 0.26까지 차오릅니다) 눈·입술이 도로 스무딩 대상이 됩니다.
    # 3σ = 반지름이면 경계는 충분히 부드럽고 중심은 확실히 뚫려 있습니다.
    hole_sigma = 0.0
    if shapes.holes:
        hole_sigma = min(_sigma(feather, shapes.hole_reference),
                         shapes.hole_reference / 3.0)
    pad = int(3.0 * max(sigma, hole_sigma)) + 2

    xs = np.concatenate([poly[:, 0] for poly in shapes.fill])
    ys = np.concatenate([poly[:, 1] for poly in shapes.fill])
    x0 = max(0, int(xs.min()) - pad)
    y0 = max(0, int(ys.min()) - pad)
    x1 = min(w, int(xs.max()) + pad + 1)
    y1 = min(h, int(ys.max()) + pad + 1)
    if x1 <= x0 or y1 <= y0:
        return None

    offset = np.array([x0, y0], np.int32)
    window = np.zeros((y1 - y0, x1 - x0), np.float32)
    cv2.fillPoly(window, [poly - offset for poly in shapes.fill], 1.0)
    if sigma >= 0.6:
        window = cv2.GaussianBlur(window, (0, 0), sigma)

    if shapes.holes:
        holes = np.zeros_like(window)
        cv2.fillPoly(holes, [poly - offset for poly in shapes.holes], 1.0)
        if hole_sigma >= 0.6:
            holes = cv2.GaussianBlur(holes, (0, 0), hole_sigma)
        window *= 1.0 - holes

    np.clip(window, 0.0, 1.0, out=window)
    alpha = np.zeros((h, w), np.float32)
    alpha[y0:y1, x0:x1] = window
    return alpha


def _mesh_points(detect_bgr: np.ndarray | None,
                 face: np.ndarray) -> np.ndarray | None:
    """이 얼굴의 468점. 모델이 없거나 실패하면 None(→ 예전 타원 방식)."""
    if detect_bgr is None or not face_mesh.available():
        return None
    return face_mesh.landmarks(
        detect_bgr, (float(face[0]), float(face[1]),
                     float(face[2]), float(face[3])))


def _contour(points: np.ndarray, indices, scale_x: float = 1.0,
             scale_y: float = 1.0, shift_y: float = 0.0) -> np.ndarray:
    """윤곽점을 중심 기준으로 늘리고(범위 %) 아래로 밀어 정수 좌표로.

    shift_y는 **늘리기 전** 높이에 대한 비율입니다. 늘린 뒤 높이를 쓰면
    범위를 키울수록 마스크가 얼굴 아래로 흘러내립니다.
    """
    poly = np.array([[points[i][0], points[i][1]] for i in indices], np.float64)
    centre = poly.mean(axis=0)
    height = float(poly[:, 1].max() - poly[:, 1].min())
    poly[:, 0] = centre[0] + (poly[:, 0] - centre[0]) * scale_x
    poly[:, 1] = centre[1] + (poly[:, 1] - centre[1]) * scale_y + height * shift_y
    return np.round(poly).astype(np.int32)


def _contour_radius(polygon: np.ndarray) -> float:
    """페더 기준이 될 크기 — 윤곽 경계상자의 짧은 쪽 절반."""
    span_x = float(polygon[:, 0].max() - polygon[:, 0].min())
    span_y = float(polygon[:, 1].max() - polygon[:, 1].min())
    return max(2.0, min(span_x, span_y) / 2.0)


# 이목구비 구멍은 윤곽보다 조금 넉넉하게 잡습니다. 딱 맞추면 속눈썹·입술
# 경계선 한 줄이 마스크에 남아 그 선만 뭉갭니다.
_HOLE_MARGIN = 1.22

_FACE_HOLES = (face_mesh.LEFT_EYE, face_mesh.RIGHT_EYE,
               face_mesh.LEFT_BROW, face_mesh.RIGHT_BROW, face_mesh.LIPS)


def _smallest_radius(polygons: list[np.ndarray]) -> float:
    return min((_contour_radius(poly) for poly in polygons), default=2.0)


def _mesh_face_shapes(region: str, points: np.ndarray, size: float) -> _Shapes:
    if region == "mouth":
        poly = _contour(points, face_mesh.LIPS, size, size)
        return _Shapes([poly], _contour_radius(poly))

    if region == "teeth":
        poly = _contour(points, face_mesh.INNER_LIPS, size, size)
        return _Shapes([poly], _contour_radius(poly))

    if region == "brow":
        polys = [_contour(points, ring, size, size)
                 for ring in (face_mesh.LEFT_BROW, face_mesh.RIGHT_BROW)]
        return _Shapes(polys, _smallest_radius(polys))

    # skin — 얼굴 윤곽에서 이목구비를 뺀 '피부만'
    oval = _contour(points, face_mesh.FACE_OVAL, size, size)
    holes = [_contour(points, ring, _HOLE_MARGIN, _HOLE_MARGIN)
             for ring in _FACE_HOLES]
    return _Shapes([oval], _contour_radius(oval),
                   holes, _smallest_radius(holes))


def _mesh_eye_shapes(region: str, points: np.ndarray, size: float) -> _Shapes:
    rings = (face_mesh.LEFT_EYE, face_mesh.RIGHT_EYE)

    if region == "iris":
        polys = [_contour(points, ring, size, size) for ring in rings]
        return _Shapes(polys, _smallest_radius(polys))

    # 눈가(under_eye) — 눈꼬리 주름과 눈밑 다크서클이 대상입니다. 눈 윤곽을
    # 가로로 넓히고(주름) 아래로 밀어(다크서클) 잡은 뒤, 눈알은 도로 뺍니다.
    #
    # 아래로 미는 양이 관건입니다. 눈 중심 기준으로만 늘리면 위쪽 절반이
    # 눈꺼풀과 눈썹을 덮어, '언더아이'라면서 쌍꺼풀을 뭉개게 됩니다.
    # 윗변이 눈 중심보다 살짝 위(눈꼬리 높이)에 오도록 맞춥니다.
    polys = [_contour(points, ring, 2.0 * size, 2.1 * size, shift_y=0.85)
             for ring in rings]
    holes = [_contour(points, ring, _HOLE_MARGIN, _HOLE_MARGIN)
             for ring in rings]
    return _Shapes(polys, _smallest_radius(polys),
                   holes, _smallest_radius(holes))


def _box_face_shapes(region: str, face: np.ndarray, size: float) -> _Shapes:
    """폴백 — 모델이 없으면 YuNet 점 5개로 타원을 어림합니다."""
    fx, fy, fw, fh = face[0], face[1], face[2], face[3]
    r_mouth = np.array([face[10], face[11]])
    l_mouth = np.array([face[12], face[13]])
    mouth_width = max(4.0, float(np.linalg.norm(l_mouth - r_mouth)))

    if region in ("mouth", "teeth"):
        # 점 5개로는 입술 안팎을 구분할 수 없어 치아도 입 전체로 갑니다
        poly = _ellipse_poly((r_mouth + l_mouth) / 2.0,
                             (mouth_width * 0.75 * size, mouth_width * 0.42 * size))
        return _Shapes([poly], mouth_width * 0.42 * size)

    right_eye = np.array([face[4], face[5]])
    left_eye = np.array([face[6], face[7]])
    eye_distance = max(4.0, float(np.linalg.norm(left_eye - right_eye)))

    if region == "brow":
        polys = [_ellipse_poly(eye - np.array([0.0, eye_distance * 0.30]),
                               (eye_distance * 0.32 * size, eye_distance * 0.14 * size))
                 for eye in (right_eye, left_eye)]
        return _Shapes(polys, eye_distance * 0.14 * size)

    oval = _ellipse_poly((fx + fw / 2, fy + fh * 0.52),
                         (fw * 0.55 * size, fh * 0.68 * size))
    holes = []
    for eye in (right_eye, left_eye):
        holes.append(_ellipse_poly(eye, (eye_distance * 0.30, eye_distance * 0.20)))
        holes.append(_ellipse_poly(eye - np.array([0.0, eye_distance * 0.30]),
                                   (eye_distance * 0.32, eye_distance * 0.14)))
    holes.append(_ellipse_poly((r_mouth + l_mouth) / 2.0,
                               (mouth_width * 0.80, mouth_width * 0.42)))
    return _Shapes([oval], min(fw * 0.55, fh * 0.68) * size,
                   holes, eye_distance * 0.20)


def _box_eye_shapes(region: str, face: np.ndarray, size: float) -> _Shapes:
    """폴백 — 눈 중심점 2개뿐이라 눈 모양을 알 수 없어 타원으로 어림합니다."""
    right_eye = np.array([face[4], face[5]])
    left_eye = np.array([face[6], face[7]])
    eye_distance = max(4.0, float(np.linalg.norm(left_eye - right_eye)))

    if region == "iris":
        radius = eye_distance * 0.22 * size
        polys = [_ellipse_poly(eye, (radius, radius))
                 for eye in (right_eye, left_eye)]
        return _Shapes(polys, radius)

    reference = eye_distance * 0.34 * size
    polys = [_ellipse_poly(eye, (eye_distance * 0.50 * size, reference))
             for eye in (right_eye, left_eye)]
    holes = [_ellipse_poly(eye, (eye_distance * 0.21 * size,
                                 eye_distance * 0.13 * size))
             for eye in (right_eye, left_eye)]
    return _Shapes(polys, reference, holes, eye_distance * 0.13 * size)


def _face_alpha(mask: Mask, face: np.ndarray, h: int, w: int,
                detect_bgr: np.ndarray | None = None) -> np.ndarray | None:
    region = str(mask.params.get("region", "skin"))
    size = _size_factor(mask)
    points = _mesh_points(detect_bgr, face)
    shapes = (_mesh_face_shapes(region, points, size) if points is not None
              else _box_face_shapes(region, face, size))
    return _rasterise(shapes, mask.feather, h, w)


def _eye_alpha(mask: Mask, face: np.ndarray, h: int, w: int,
               detect_bgr: np.ndarray | None = None) -> np.ndarray | None:
    region = str(mask.params.get("region", "under_eye"))
    size = _size_factor(mask)
    points = _mesh_points(detect_bgr, face)
    shapes = (_mesh_eye_shapes(region, points, size) if points is not None
              else _box_eye_shapes(region, face, size))
    return _rasterise(shapes, mask.feather, h, w)


def _background_alpha(faces: np.ndarray | None, detect_bgr: np.ndarray,
                      h: int, w: int, feather: int) -> np.ndarray | None:
    """GrabCut으로 인물을 분리한 뒤 배경(인물 밖)을 돌려줍니다.

    속도를 위해 축소본에서 돌리고 결과 마스크만 원본 크기로 늘립니다. 얼굴이
    있으면 얼굴+상체를 전경 시드로, 없으면 중앙 사각형을 시드로 씁니다.
    """
    scale = min(1.0, 480.0 / max(h, w))
    sw, sh = max(1, round(w * scale)), max(1, round(h * scale))
    small = cv2.resize(detect_bgr, (sw, sh), interpolation=cv2.INTER_AREA)

    gc = np.full((sh, sw), cv2.GC_PR_BGD, np.uint8)
    seeded = False
    if faces is not None:
        for face in faces:
            fx, fy, fw, fh = (v * scale for v in (face[0], face[1], face[2], face[3]))
            # 얼굴은 확실한 전경, 그 아래 상체는 아마 전경
            bx0, by0 = int(fx - fw * 0.6), int(fy)
            bx1, by1 = int(fx + fw * 1.6), int(fy + fh * 4.5)
            cv2.rectangle(gc, (max(0, bx0), max(0, by0)),
                          (min(sw, bx1), min(sh, by1)), cv2.GC_PR_FGD, -1)
            cv2.rectangle(gc, (int(fx), int(fy)),
                          (int(fx + fw), int(fy + fh)), cv2.GC_FGD, -1)
            seeded = True
    if not seeded:
        cv2.rectangle(gc, (int(sw * 0.3), int(sh * 0.15)),
                      (int(sw * 0.7), int(sh * 0.95)), cv2.GC_PR_FGD, -1)
    # 테두리는 확실한 배경
    gc[0, :] = gc[-1, :] = gc[:, 0] = gc[:, -1] = cv2.GC_BGD

    try:
        bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
        cv2.grabCut(small, gc, None, bgd, fgd, 3, cv2.GC_INIT_WITH_MASK)
    except cv2.error as exc:
        log.debug("GrabCut 실패: %s", exc)
        return None

    foreground = np.where((gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD), 1.0, 0.0).astype(np.float32)
    background = cv2.resize(1.0 - foreground, (w, h), interpolation=cv2.INTER_LINEAR)
    return _feather(background, feather)


def build_mask_alpha(
    mask: Mask, shape: tuple[int, int], detect_bgr: np.ndarray,
    faces: np.ndarray | None,
    main_face_box: tuple[float, float, float, float] | None = None,
) -> np.ndarray | None:
    """마스크의 알파(float32 HxW, 0~1)를 만듭니다. 못 만들면 None."""
    h, w = shape[:2]
    try:
        if mask.kind is MaskType.RADIAL:
            return _radial_alpha(mask.params, h, w, _size_factor(mask))
        if mask.kind is MaskType.LINEAR:
            return _linear_alpha(mask.params, h, w)
        if mask.kind is MaskType.BRUSH:
            return _brush_alpha(mask, h, w)

        if mask.kind is MaskType.BACKGROUND:
            return _background_alpha(faces, detect_bgr, h, w, mask.feather)

        chosen = select_faces(faces, mask, detect_bgr, main_face_box)
        if not chosen:
            return None

        builder = _face_alpha if mask.kind is MaskType.FACE else _eye_alpha
        alpha = None
        for face in chosen:
            piece = builder(mask, face, h, w, detect_bgr)
            if piece is None:
                continue
            alpha = piece if alpha is None else np.maximum(alpha, piece)
        return alpha
    except (cv2.error, ValueError) as exc:
        log.debug("마스크 알파 생성 실패(%s): %s", mask.kind, exc)
    return None


# ---------------------------------------------------------------- 국소 조정


def _local_white_balance(image: np.ndarray, temperature: int, tint: int) -> np.ndarray:
    """국소 색온도(상대 이동)와 색조. 전역의 절대 Kelvin과 달리 단순 채널 게인."""
    result = image.copy()
    if temperature:
        warm = temperature / 100.0 * 0.30
        result[:, :, 2] *= 1.0 + warm  # R
        result[:, :, 0] *= 1.0 - warm  # B
    if tint:
        result[:, :, 1] *= 1.0 - tint / 100.0 * 0.18  # G
    return result


def _smooth(image: np.ndarray, amount: int) -> np.ndarray:
    """피부 부드럽게 — 엣지를 보존하는 bilateral 블러를 비율만큼 섞습니다."""
    strength = amount / 100.0
    as_uint8 = np.clip(image, 0, 255).astype(np.uint8)
    d = int(5 + 4 * strength)
    sigma = int(20 + 90 * strength)
    filtered = cv2.bilateralFilter(as_uint8, d, sigma, sigma).astype(np.float32)
    return image * (1.0 - strength) + filtered * strength


def apply_local(image: np.ndarray, adjust: LocalAdjustments) -> np.ndarray:
    """float BGR 이미지(마스크 bbox 잘린 조각)에 국소 조정을 적용합니다."""
    result = image.astype(np.float32, copy=True)

    tone = BasicSettings(
        exposure=adjust.exposure, contrast=adjust.contrast,
        highlights=adjust.highlights, shadows=adjust.shadows,
        whites=adjust.whites, blacks=adjust.blacks,
    )
    if tone != BasicSettings():
        result = _apply_lut(result, _tone_lut(tone))

    if adjust.temperature or adjust.tint:
        result = _local_white_balance(result, adjust.temperature, adjust.tint)

    if adjust.clarity:
        radius = max(3.0, min(result.shape[:2]) / 120)
        result = _local_contrast(result, adjust.clarity, radius=radius)
    if adjust.texture:
        result = _local_contrast(result, adjust.texture, radius=1.2)

    if adjust.saturation:
        result = _apply_saturation(result, BasicSettings(saturation=adjust.saturation))

    if adjust.smoothing:
        result = _smooth(result, adjust.smoothing)

    if adjust.sharpen:
        blurred = cv2.GaussianBlur(result, (0, 0), 1.0)
        result = result + (result - blurred) * (adjust.sharpen / 100.0)

    return result


# ---------------------------------------------------------------- 진입점


def apply_masks(image: np.ndarray, masks, detect_bgr: np.ndarray | None = None,
                main_face_box: tuple[float, float, float, float] | None = None) -> np.ndarray:
    """전역 보정이 끝난 float 이미지에 마스크들을 순서대로 합성합니다.

    detect_bgr(얼굴 검출용 uint8)를 안 주면 image에서 만듭니다. 얼굴 검출은
    얼굴 계열 마스크가 하나라도 있을 때만 한 번 수행해 재사용합니다.

    main_face_box는 분석이 고른 주 피사체의 정규화 좌표입니다(apply_settings 참고).
    """
    active = [m for m in masks if not m.is_neutral()]
    if not active:
        return image

    if detect_bgr is None:
        detect_bgr = np.clip(image, 0, 255).astype(np.uint8)

    faces = None
    if any(m.kind in _FACE_KINDS for m in active):
        faces = _detect_faces_full(detect_bgr)

    result = image
    for mask in active:
        alpha = build_mask_alpha(mask, result.shape, detect_bgr, faces,
                                 main_face_box)
        if alpha is None:
            continue
        if mask.invert:
            alpha = 1.0 - alpha
        alpha = alpha * (mask.opacity / 100.0)

        ys, xs = np.where(alpha > 0.004)
        if ys.size == 0:
            continue
        y0, y1 = int(ys.min()), int(ys.max()) + 1
        x0, x1 = int(xs.min()), int(xs.max()) + 1

        sub = result[y0:y1, x0:x1]
        local = apply_local(sub, mask.adjust)
        a = alpha[y0:y1, x0:x1][:, :, None]
        result[y0:y1, x0:x1] = sub * (1.0 - a) + local * a

    return result


# ---------------------------------------------------------------- 브러시 인코딩


def mask_overlay_alpha(
    mask: Mask, image_bgr: np.ndarray,
    main_face_box: tuple[float, float, float, float] | None = None,
) -> np.ndarray | None:
    """UI 오버레이용 알파. 필요하면 얼굴을 검출해 build_mask_alpha에 넘깁니다.

    invert는 여기서 반영해, 사용자가 실제 영향받는 영역을 보게 합니다.
    """
    faces = None
    if mask.kind in _FACE_KINDS:
        detect = image_bgr if image_bgr.dtype == np.uint8 else np.clip(image_bgr, 0, 255).astype(np.uint8)
        faces = _detect_faces_full(detect)
    alpha = build_mask_alpha(mask, image_bgr.shape, image_bgr, faces,
                             main_face_box)
    if alpha is None:
        return None
    if mask.invert:
        alpha = 1.0 - alpha
    return alpha


def encode_brush(alpha_small: np.ndarray) -> str:
    """축소된 알파(0~1 또는 0~255)를 base64 PNG로. 브러시 UI에서 씁니다."""
    if alpha_small.dtype != np.uint8:
        alpha_small = np.clip(alpha_small * 255.0, 0, 255).astype(np.uint8)
    ok, buffer = cv2.imencode(".png", alpha_small)
    if not ok:
        return ""
    return base64.b64encode(buffer.tobytes()).decode("ascii")
