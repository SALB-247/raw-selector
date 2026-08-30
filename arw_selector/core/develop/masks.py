"""Mask (local adjustment) rendering.

On an image whose global adjustments are finished, the local adjustment is
applied to the mask region only and composited by alpha. The preview and
the export use the same engine, so what you see is what you get.

Core design
  - Face/eye/background/radial/linear masks store only normalised
    parameters and rebuild the alpha here, to the image size, every time
    -> resolution independent.
  - Only the brush carries a shrunken alpha bitmap around, and it is
    stretched to the image size here.
  - The local operations run inside the alpha's bounding box only, which
    ties the cost down even at 6000x4000.
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
    _curve_lut,
    _local_contrast,
    _spline_lut,
    _tone_lut,
    curve_control_points,
)
from .settings import (BasicSettings, LocalAdjustments, Mask, MaskCombine,
                       MaskType)

log = logging.getLogger(__name__)

_FACE_KINDS = frozenset({MaskType.FACE, MaskType.EYE,
                        MaskType.BACKGROUND, MaskType.SUBJECT})
"""Masks that need face detection. The subject one needs faces to decide
whether the model discarded a person (_subject_alpha)."""

SIZE_KINDS = frozenset({MaskType.FACE, MaskType.EYE, MaskType.RADIAL})
"""The kinds where mask.size (range %) really shrinks the region. The rest
ignore it."""


def _size_factor(mask: Mask) -> float:
    """Range % -> the factor multiplying the shape radius. 0~200%
    (100 by default)."""
    return float(np.clip(mask.size, 0, 200)) / 100.0


def _param(params: dict, key: str, default: float) -> float:
    """Read one shape parameter as a finite real number.

    params is a free-form dict that differs per kind, so it cannot go
    through the dataclass's type tidying. Let a `.nan` or a string into
    the preset YAML and it rides straight into the computation, making
    **the whole alpha NaN**. A NaN in the alpha passes right through
    compositing, stays as rubbish pixels in the saved file and breaks the
    mask overlay too - it is not an exception but a wrong picture, which
    makes it hard to notice.
    """
    value = params.get(key, default)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if np.isfinite(number) else default


# -------------------------------------------------------------- face detection


def _detect_faces_full(detect_bgr: np.ndarray) -> np.ndarray | None:
    """Detect on a shrunken copy and put the coordinates back at the
    original scale.

    The returned coordinates (columns 0~13: x, y, w, h, 5 landmark pairs)
    are in the detect_bgr coordinate system. Column 14 (score) is left
    alone. They are sorted by descending area, so index 0 is the main
    subject.
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
    order = np.argsort(-(faces[:, 2] * faces[:, 3]))  # descending area
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
    """The detected face closest to the centre of the normalised hint box.

    The hint is a coordinate that came out of the analysis preview, so it
    can differ in size and count from the detection result on the current
    image. Matching by index goes wrong, so we match by position.
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
    """The faces this mask takes as its target.

    It used to apply unconditionally to **the single largest face by
    area**. In a group photo, a passer-by in the front row caught larger
    than the protagonist meant the wrong person got brightened, and there
    was no way to work on several people at once either.

    - main  : the main subject the focus scoring picked (the same face as
              the red box on screen)
    - all   : every detected face
    - index : the number the user picked (in descending area)
    """
    if faces is None or len(faces) == 0:
        return []

    target = str(mask.params.get("target", FACE_TARGET_MAIN))
    if target == FACE_TARGET_ALL:
        return list(faces)
    if target == FACE_TARGET_INDEX:
        face = _pick_face(faces, int(mask.params.get("index", 0)))
        return [face] if face is not None else []

    # If the analysis already picked a face, that is what we use. Picking
    # again here can give a different answer because the resolution
    # differs, and above all, when the user changes the main subject on
    # screen the mask alone would stay on the old face.
    if main_face_box is not None:
        h, w = detect_bgr.shape[:2]
        return [_nearest_face(faces, main_face_box, h, w)]

    # With no hint we pick it ourselves, by **the same criterion** as focus
    try:
        from ..focus import LAPLACIAN_K, TENENGRAD_K, _pick_main_face

        gray = cv2.cvtColor(detect_bgr, cv2.COLOR_BGR2GRAY)
        index = _pick_main_face(faces, gray, 1.0, gray.shape[:2],
                                LAPLACIAN_K, TENENGRAD_K)
        return [faces[index]]
    except Exception:  # noqa: BLE001 - cannot pick -> the largest face
        log.debug("주 피사체 얼굴 선정 실패", exc_info=True)
        return [faces[0]]


# ------------------------------------------------------------ alpha generation


def _feather(alpha: np.ndarray, feather: int, reference_px: float | None = None) -> np.ndarray:
    """Soften the edge with a Gaussian.

    A shape mask sets the spread against reference_px (that shape's short
    radius). Set it against the image size and a small mask (under the
    eye and so on) gets washed away wholesale, so the effect disappears
    the more the range is shrunk. Only the brush and background, which
    have no reference, go by the image's short edge.
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
    # The floor has to apply to the radius 'as a whole'. Guard params
    # alone and taking the range (size) slider down to 0% makes the
    # product 0, so the division below divides by zero and NaN gets mixed
    # into the alpha. NaN survives compositing as it is, staying as
    # rubbish pixels on screen and in the saved file, and breaking the
    # mask overlay too.
    rx = max(1e-3, _param(params, "rx", 0.3) * w * size)
    ry = max(1e-3, _param(params, "ry", 0.3) * h * size)
    angle = np.radians(_param(params, "rotation", 0.0))

    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dx, dy = xx - cx, yy - cy
    ca, sa = np.cos(angle), np.sin(angle)
    xr = (dx * ca + dy * sa) / rx
    yr = (-dx * sa + dy * ca) / ry
    dist = np.sqrt(xr * xr + yr * yr)
    # 1 solid on the inside, linear down to 0 at the boundary. feather is
    # not applied separately after the alpha is built.
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
    """An ellipse as contour points. This lets the fallback path ride the
    same rasteriser as the contours."""
    return cv2.ellipse2Poly(
        (int(round(center[0])), int(round(center[1]))),
        (max(1, int(round(axes[0]))), max(1, int(round(axes[1])))),
        int(angle), 0, 360, 5,
    )


@dataclass
class _Shapes:
    """The list of contours that make up the alpha. `_rasterise` handles
    the rasterisation.

    The key point is carrying the feather reference of the holes (eyes,
    brows, mouth) **separately** from the outer boundary. The old
    implementation applied `_feather` once to an alpha with the ellipses
    dug out, and that sigma went by face size (88px for a 400px face), so
    a 45px hole the size of an eye was washed away without a trace. That
    is why the covered area of the 'skin only' mask was **identical down
    to the decimal** with the ordinary face mask (33.75% for both), and
    smoothing the skin smeared the brows and lips along with it.
    """

    fill: list[np.ndarray]
    reference: float
    holes: list[np.ndarray] = field(default_factory=list)
    hole_reference: float = 2.0


def _sigma(feather: int, reference_px: float) -> float:
    return max(0.0, feather) / 100.0 * max(2.0, float(reference_px))


def _rasterise(shapes: _Shapes | None, feather: int,
               h: int, w: int) -> np.ndarray | None:
    """Contours into an alpha. The blur runs **inside the bounding box
    window only**.

    Run the Gaussian over the whole frame and one mask takes 0.9 seconds
    at 6192x4128 (measured). A face is only part of the frame, so cutting
    it out with just the spread margin (3σ) and running there finishes in
    tens of milliseconds.
    """
    if shapes is None or not shapes.fill:
        return None

    sigma = _sigma(feather, shapes.reference)

    # A hole's spread is tied to 1/3 of the hole radius. As sigma
    # approaches the radius the Gaussian fills the hole itself back in
    # (at radius 40px and sigma 40px the centre alpha climbs to 0.26), so
    # the eyes and lips become smoothing targets again. At 3σ = the
    # radius the boundary is soft enough and the centre is definitely
    # open.
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
    """The 468 points of this face. None if the model is missing or fails
    (-> the old ellipse method)."""
    if detect_bgr is None or not face_mesh.available():
        return None
    return face_mesh.landmarks(
        detect_bgr, (float(face[0]), float(face[1]),
                     float(face[2]), float(face[3])))


def _contour(points: np.ndarray, indices, scale_x: float = 1.0,
             scale_y: float = 1.0, shift_y: float = 0.0) -> np.ndarray:
    """Stretch the contour points about their centre (range %), push them
    down, and turn them into integer coordinates.

    shift_y is a ratio of the height **before** the stretch. Use the
    height after the stretch and the mask slides down off the face the
    more the range is raised.
    """
    poly = np.array([[points[i][0], points[i][1]] for i in indices], np.float64)
    centre = poly.mean(axis=0)
    height = float(poly[:, 1].max() - poly[:, 1].min())
    poly[:, 0] = centre[0] + (poly[:, 0] - centre[0]) * scale_x
    poly[:, 1] = centre[1] + (poly[:, 1] - centre[1]) * scale_y + height * shift_y
    return np.round(poly).astype(np.int32)


def _contour_radius(polygon: np.ndarray) -> float:
    """The size the feather goes by - half the short side of the
    contour's bounding box."""
    span_x = float(polygon[:, 0].max() - polygon[:, 0].min())
    span_y = float(polygon[:, 1].max() - polygon[:, 1].min())
    return max(2.0, min(span_x, span_y) / 2.0)


# The holes for the features are set a little more generously than the
# contours. Fit them exactly and a single line of the eyelash and lip
# boundary stays in the mask, and that line alone gets smeared.
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

    # skin - the face contour with the features subtracted, 'skin only'
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

    # The eye area (under_eye) - crow's feet and the dark circles under
    # the eye are the target. The eye contour is widened horizontally
    # (the wrinkles) and pushed down (the dark circles), then the eyeball
    # itself is subtracted back out.
    #
    # How far it is pushed down is the crux. Stretch about the eye centre
    # alone and the upper half covers the eyelid and the brow, so an
    # 'under-eye' adjustment ends up smearing the eyelid crease. The top
    # edge is set to land just above the eye centre (at the height of the
    # outer corner).
    polys = [_contour(points, ring, 2.0 * size, 2.1 * size, shift_y=0.85)
             for ring in rings]
    holes = [_contour(points, ring, _HOLE_MARGIN, _HOLE_MARGIN)
             for ring in rings]
    return _Shapes(polys, _smallest_radius(polys),
                   holes, _smallest_radius(holes))


def _box_face_shapes(region: str, face: np.ndarray, size: float) -> _Shapes:
    """Fallback - with no model, approximate ellipses from YuNet's 5 points."""
    fx, fy, fw, fh = face[0], face[1], face[2], face[3]
    r_mouth = np.array([face[10], face[11]])
    l_mouth = np.array([face[12], face[13]])
    mouth_width = max(4.0, float(np.linalg.norm(l_mouth - r_mouth)))

    if region in ("mouth", "teeth"):
        # 5 points cannot tell the inside of the lips from the outside,
        # so teeth go as the whole mouth too
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
    """Fallback - with only the 2 eye centre points the eye shape is
    unknown, so it is approximated with an ellipse."""
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


SUBJECT_MISSED = 0.35
"""The alpha that divides off what the subject model saw as "this face is
not the protagonist".

Measured (5 people on stage): the person it adopted had a mean of 0.96
over the face region, the discarded ones 0.000 and 0.018. Drawing the line
anywhere in between gives the same decision, so it was set in the middle
to avoid leaning either way."""


def _subject_alpha(faces: np.ndarray | None, detect_bgr: np.ndarray,
                   h: int, w: int, feather: int) -> np.ndarray | None:
    """The subject alpha. If the model discarded anybody, only they get
    filled in.

    The model (U²-Netp) has fine edges but picks **only one
    protagonist** - on a stage frame only the person in the centre scored
    0.96 while the other four came in at 0.000~0.018. Quietly discarding
    a detected face is a worse failure than a blurry edge (you apply
    "emphasise the people" and get a photo where four of the five are
    untouched). So only when there are discarded faces do we run the
    older GrabCut once with those people as seeds and merge it in - a
    frame with one or two people does not pay this cost at all (measured
    100ms against 587ms).

    With no model (the file is missing) it falls back to the inverse of
    the background mask.
    """
    from .. import saliency

    alpha = saliency.subject_alpha(detect_bgr)
    if alpha is None:
        background = _background_alpha(faces, detect_bgr, h, w, feather)
        return None if background is None else 1.0 - background

    if faces is not None and len(faces):
        missed = []
        for face in faces:
            x, y, fw, fh = (int(face[0]), int(face[1]),
                            int(face[2]), int(face[3]))
            patch = alpha[max(0, y):y + fh, max(0, x):x + fw]
            if patch.size and float(patch.mean()) < SUBJECT_MISSED:
                missed.append(face)
        if missed:
            background = _background_alpha(np.array(missed), detect_bgr,
                                           h, w, feather)
            if background is not None:
                alpha = np.maximum(alpha, 1.0 - background)

    return _feather(np.clip(alpha, 0.0, 1.0).astype(np.float32), feather)


_GRABCUT_SEED = 20250725
"""The GrabCut random seed. The value itself means nothing; the point is
that it is **fixed** (see _background_alpha below)."""


def _background_alpha(faces: np.ndarray | None, detect_bgr: np.ndarray,
                      h: int, w: int, feather: int) -> np.ndarray | None:
    """Separate the people with GrabCut and return the background
    (everything outside them).

    For speed it runs on a shrunken copy and only the resulting mask is
    stretched to the original size. With faces present, face + upper body
    is the foreground seed; without, a central rectangle is the seed.
    """
    scale = min(1.0, 480.0 / max(h, w))
    sw, sh = max(1, round(w * scale)), max(1, round(h * scale))
    small = cv2.resize(detect_bgr, (sw, sh), interpolation=cv2.INTER_AREA)

    gc = np.full((sh, sw), cv2.GC_PR_BGD, np.uint8)
    seeded = False
    if faces is not None:
        for face in faces:
            fx, fy, fw, fh = (v * scale for v in (face[0], face[1], face[2], face[3]))
            # The face is definite foreground, the upper body below it
            # probable foreground
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
    # The border is definite background
    gc[0, :] = gc[-1, :] = gc[:, 0] = gc[:, -1] = cv2.GC_BGD

    try:
        bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
        # **The random seed is fixed.** GrabCut's GMM initialisation uses
        # k-means(++), and those initial centres are drawn from OpenCV's
        # **global** RNG. So the same input does not give the same answer
        # - measured: running the same image four times changed 5.8% of
        # the pixels (alpha up to 0.90), and as a result the background
        # mask on screen and in the exported file differed by up to 30
        # levels. Every other mask kind was identical bit for bit, so it
        # is this one's problem alone.
        #
        # What you see is what you get is this app's core contract, so
        # reproducibility matters more than randomness. The value can be
        # any constant (it has nothing to do with whether the result is
        # good or bad), and this is the only place in the app that uses
        # OpenCV's randomness, so touching the global seed changes
        # nothing elsewhere.
        cv2.setRNGSeed(_GRABCUT_SEED)
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
    """The mask's alpha (float32 HxW, 0~1). None if it cannot be built."""
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

        if mask.kind is MaskType.SUBJECT:
            return _subject_alpha(faces, detect_bgr, h, w, mask.feather)

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


def build_combined_alpha(
    mask: Mask, shape: tuple[int, int], detect_bgr: np.ndarray,
    faces: np.ndarray | None,
    main_face_box: tuple[float, float, float, float] | None = None,
) -> np.ndarray | None:
    """The mask's **final** alpha - the inversion and the refine pieces
    included.

    The order matters: the parent's invert is applied first and the
    pieces are laid on top of it. The other way round, "subtract the eyes
    from the face, then invert the whole thing" becomes "invert the face,
    then subtract the eyes", which disagrees with the order the user sees
    in the list.

    A piece's alpha reflects its own invert as well - "subtract the eyes"
    and "subtract everything but the eyes" both have to be expressible
    with a single piece.
    """
    alpha = build_mask_alpha(mask, shape, detect_bgr, faces, main_face_box)
    if alpha is None:
        return None
    if mask.invert:
        alpha = 1.0 - alpha

    for piece in mask.refine:
        if not piece.enabled:
            continue
        other = build_mask_alpha(piece, shape, detect_bgr, faces,
                                 main_face_box)
        if other is None:
            # When a piece could not be built (a face piece on a frame
            # with no face, and so on) we skip it quietly. Throw the
            # parent away here too and you get "no face found, so the sky
            # adjustment disappears wholesale".
            continue
        if piece.invert:
            other = 1.0 - other
        other = other * (piece.opacity / 100.0)

        if piece.combine is MaskCombine.SUBTRACT:
            alpha = alpha * (1.0 - other)
        elif piece.combine is MaskCombine.INTERSECT:
            alpha = alpha * other
        else:
            alpha = np.maximum(alpha, other)

    return np.clip(alpha, 0.0, 1.0).astype(np.float32)


# ------------------------------------------------------------ local adjustment


def _local_white_balance(image: np.ndarray, temperature: int, tint: int) -> np.ndarray:
    """Local temperature (a relative shift) and tint. Unlike the global
    absolute Kelvin, this is a plain channel gain."""
    result = image.copy()
    if temperature:
        warm = temperature / 100.0 * 0.30
        result[:, :, 2] *= 1.0 + warm  # R
        result[:, :, 0] *= 1.0 - warm  # B
    if tint:
        result[:, :, 1] *= 1.0 - tint / 100.0 * 0.18  # G
    return result


def _smooth(image: np.ndarray, amount: int) -> np.ndarray:
    """Skin smoothing - an edge-preserving bilateral blur mixed in by the
    given proportion."""
    strength = amount / 100.0
    as_uint8 = np.clip(image, 0, 255).astype(np.uint8)
    d = int(5 + 4 * strength)
    sigma = int(20 + 90 * strength)
    filtered = cv2.bilateralFilter(as_uint8, d, sigma, sigma).astype(np.float32)
    return image * (1.0 - strength) + filtered * strength


def apply_local(image: np.ndarray, adjust: LocalAdjustments,
                profiled: bool = True) -> np.ndarray:
    """Apply the local adjustment to a float BGR image (the piece cut out
    at the mask bbox).

    profiled is the space this piece sits in - it has to be undone with
    **the same curve** as the global exposure for a local +1EV and a
    global +1EV to mean the same amount of light (see
    engine._baseline_transfer).
    """
    result = image.astype(np.float32, copy=True)

    tone = BasicSettings(
        exposure=adjust.exposure, contrast=adjust.contrast,
        highlights=adjust.highlights, shadows=adjust.shadows,
        whites=adjust.whites, blacks=adjust.blacks,
    )
    # Tone and curve are both 256-slot tables, so they are merged into
    # one and applied once - the global pipeline (engine.apply_settings)
    # does the same, and interpolating twice slips in one more
    # intermediate rounding, which changes the picture slightly.
    lut = None
    if tone != BasicSettings():
        lut = _tone_lut(tone, profiled)
    if not adjust.curve.is_neutral():
        curve = _curve_lut(adjust.curve)
        lut = curve if lut is None else np.interp(lut, _IDENTITY, curve)
    if lut is not None:
        result = _apply_lut(result, lut.astype(np.float32))

    # Per-channel curves go per channel - the same rule as the global one.
    for channel, points in ((2, adjust.curve.points_red),
                            (1, adjust.curve.points_green),
                            (0, adjust.curve.points_blue)):
        if points:
            channel_lut = _spline_lut(curve_control_points(points))
            result[:, :, channel] = _apply_lut(
                result[:, :, channel][:, :, None], channel_lut)[:, :, 0]

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


# ---------------------------------------------------------------- entry point


def apply_masks(image: np.ndarray, masks, detect_bgr: np.ndarray | None = None,
                main_face_box: tuple[float, float, float, float] | None = None,
                profiled: bool = True) -> np.ndarray:
    """Composite the masks in order onto a float image whose global
    adjustments are finished.

    If detect_bgr (uint8, for face detection) is not given, it is built
    from image. Face detection is performed once, and only when there is
    at least one face-family mask, then reused.

    main_face_box is the normalised coordinate of the main subject the
    analysis picked (see apply_settings). profiled picks the curve the
    local exposure will undo with (see apply_local).
    """
    active = [m for m in masks if not m.is_neutral()]
    if not active:
        return image

    if detect_bgr is None:
        detect_bgr = np.clip(image, 0, 255).astype(np.uint8)

    # The refine pieces have to be counted too - there are combinations
    # like "subtract the face from the sky mask" where the parent is not
    # face-family but the piece is. Miss it and faces is None, so that
    # piece is quietly ignored.
    faces = None
    kinds = {m.kind for m in active}
    kinds.update(piece.kind for m in active for piece in m.refine)
    if kinds & _FACE_KINDS:
        faces = _detect_faces_full(detect_bgr)

    result = image
    for mask in active:
        alpha = build_combined_alpha(mask, result.shape, detect_bgr, faces,
                                     main_face_box)
        if alpha is None:
            continue
        alpha = alpha * (mask.opacity / 100.0)

        ys, xs = np.where(alpha > 0.004)
        if ys.size == 0:
            continue
        y0, y1 = int(ys.min()), int(ys.max()) + 1
        x0, x1 = int(xs.min()), int(xs.max()) + 1

        sub = result[y0:y1, x0:x1]
        local = apply_local(sub, mask.adjust, profiled)
        a = alpha[y0:y1, x0:x1][:, :, None]
        result[y0:y1, x0:x1] = sub * (1.0 - a) + local * a

    return result


# -------------------------------------------------------------- brush encoding


def mask_overlay_alpha(
    mask: Mask, image_bgr: np.ndarray,
    main_face_box: tuple[float, float, float, float] | None = None,
) -> np.ndarray | None:
    """The alpha for the UI overlay. Detects faces and passes them along
    if needed.

    It uses **the same compositing as the render**
    (build_combined_alpha) - the inversion and the refine pieces are
    reflected here too. Let them diverge and the region painted red
    differs from the region actually adjusted, and that makes the masks
    impossible to trust.
    """
    faces = None
    kinds = {mask.kind, *(piece.kind for piece in mask.refine)}
    if kinds & _FACE_KINDS:
        detect = image_bgr if image_bgr.dtype == np.uint8 else np.clip(image_bgr, 0, 255).astype(np.uint8)
        faces = _detect_faces_full(detect)
    return build_combined_alpha(mask, image_bgr.shape, image_bgr, faces,
                                main_face_box)


def encode_brush(alpha_small: np.ndarray) -> str:
    """A shrunken alpha (0~1 or 0~255) into a base64 PNG. Used by the
    brush UI."""
    if alpha_small.dtype != np.uint8:
        alpha_small = np.clip(alpha_small * 255.0, 0, 255).astype(np.uint8)
    ok, buffer = cv2.imencode(".png", alpha_small)
    if not ok:
        return ""
    return base64.b64encode(buffer.tobytes()).decode("ascii")
