"""Focus scoring.

The basic strategy: find a face, cut an ROI out of the eye positions in
the face landmarks, and measure sharpness only inside it. However sharp
the background is, a frame with the eyes out of focus is a discard, and
the reverse holds just as well. With no face, the sharpest tile of a grid
is taken to be the main subject.

Sharpness is normalised by contrast. Laplacian variance grows in
proportion to the square of contrast, so used without normalising,
low-light / low-contrast scenes all score low regardless of focus. That is
the single largest source of wrong calls.

Every function in this module is pure - it takes an ndarray and returns a
dataclass.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from pathlib import Path

import cv2
import numpy as np

from .raw_io import resize_long_edge
from .types import FocusResult, FocusSource

log = logging.getLogger(__name__)

ALGORITHM_VERSION = 5
"""Measurement algorithm version. It goes into the cache key.

5: The scene fingerprint (dhash) is taken from the 1024px reduction used
   for face detection rather than from the original preview. Focus scoring
   does not move by a single pixel (measured: 60/60 exact match) and only
   the fingerprint differs, by at most 1 bit (4 of 60 frames). The scene
   grouping threshold is 40 of 64 bits so the result was the same (150
   frames, the same 12 groups), but if old-style and new-style
   fingerprints mix inside one folder the anchor comparison flips between
   the two methods, so we roll the cache over.


Even with the settings unchanged, old results are invalid once the
algorithm changes. Fail to bump this value and the cache hands back the
old scores while you believe you "fixed" something that never took effect.
That has actually happened.

v2: Added the low-variance region gate (MIN_VARIANCE) and changed tile
    selection to go by raw gradient energy - dark background noise was
    being picked as the subject
v3: Changed main-subject face selection to area x confidence (a large
    false detection was hijacking the ROI), and measured background
    sharpness (background_sharpness) separately when the ROI is a face -
    to screen out frames "focused on the background, not the face"
v4: Subtract the noise contribution from sharpness analytically (noise_var
    in measure_patch). Noise inflated sharpness (measured 1.63x) so that
    in 3 of 11 measured ROIs across 5 camera bodies a 'noisy blurry frame'
    beat a 'sharp frame' - after the subtraction, 0
"""

MODEL_PATH = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"

DETECT_LONG_EDGE = 1024
"""Reduced resolution for face detection. YuNet is accurate enough at this
size and far faster."""

MIN_ROI_PX = 24
"""An ROI smaller than this makes a sharpness measurement meaningless."""

FACE_DISPLAY_MIN_SCORE = 0.80
"""Minimum confidence for drawing a face box on screen.

Set higher than the detection threshold (0.6). Going through real shooting
samples by eye, the 0.60~0.75 band is where the false detections pile up -
speaker cones, white gloves, dark blotches. Drawing them leaves nothing
behind but "why is that a face".

Detection itself is not cut at this value - the same band also holds many
**real faces**, in profile, in motion blur, and under stage lighting, and
cutting there loses a measured 16%.
"""

FACE_MAIN_MIN_SCORE = 0.75
"""Minimum confidence to become the main subject (the basis of the eye ROI).

If only detections below this exist we have no choice but to pick from
among them, but if there is even one more confident face we use that one.
This slot is the reference point for focus scoring, so a false detection
sitting in it makes the whole frame's score wrong.
"""

_EPS = 1e-6

MIN_VARIANCE = 25.0
"""Minimum variance that can be scored (a standard deviation of 5).

Normalising is there for contrast invariance, but in a region with no
signal the ratio itself loses its meaning. An empty dark stage background
(variance 0.9) has raw gradients at noise level, yet the moment you divide
by the variance it scored higher than a real subject (variance 2000).

Clamping the denominator was not enough on its own. With white noise the
Laplacian response itself is about the size of the clamp, so it still gets
through. So it is used as a gate rather than a floor - below a standard
deviation of 5 is sensor noise territory, with no basis for scoring focus,
so the answer is 0.

Measured: the offending noise tile was variance 0.9, real subject tiles
227~2000.
"""

FRAME_LONG_EDGE = 1024
"""Fixed resolution for measuring frame_sharpness.

Whole-frame sharpness must always be measured at the same scale.
Laplacian-family metrics are sensitive to resolution, so at a different
scale the values themselves stop being comparable.
"""

# Saturation constants that squeeze the normalised metrics into 0~100 - a
# metric scores 50 points at its constant. Set to the median of a real
# A6700 batch (ILCE-6700, 85 frames mixing telephoto and standard).
# The distribution shifts with shooting style, so config has to be able to
# override them.
LAPLACIAN_K = 0.053
TENENGRAD_K = 1.63
FRAME_LAPLACIAN_K = 0.053
FRAME_TENENGRAD_K = 1.63


# -------------------------------------------------------------- face detection

_detector_local = threading.local()


@contextlib.contextmanager
def _quiet_opencv():
    """Block OpenCV's C++ warnings for the duration of this block only.

    Every time a YuNet is built, OpenCV 5.0 prints this line:

        setPreferableTarget Targets are not supported by the new graph engine

    It only means the execution-target hint is ignored; detection is fine -
    confirmed by measurement (8 frames, same face count, 0 pixel difference
    in the main face box). But the detector is rebuilt every time the main
    subject changes, so these keep piling up in the console, and then the
    warnings that actually matter get buried.

    The scope is narrowed to this block. Lowering it globally would make
    real errors disappear too.
    """
    logging_api = getattr(getattr(cv2, "utils", None), "logging", None)
    if logging_api is None:  # may be absent depending on the build
        yield
        return
    previous = logging_api.getLogLevel()
    logging_api.setLogLevel(logging_api.LOG_LEVEL_ERROR)
    try:
        yield
    finally:
        logging_api.setLogLevel(previous)


def _get_detector(size: tuple[int, int]) -> "cv2.FaceDetectorYN | None":
    """Reuse one YuNet detector per process/thread.

    Loading ONNX is too expensive to repeat per frame. At 4000 frames that
    cost is the whole bill.
    """
    if not MODEL_PATH.exists():
        return None

    detector = getattr(_detector_local, "detector", None)
    if detector is None:
        try:
            with _quiet_opencv():
                detector = cv2.FaceDetectorYN.create(
                    str(MODEL_PATH), "", size, 0.6, 0.3, 5000
                )
        except cv2.error as exc:
            log.warning("YuNet 초기화 실패, 타일 기반으로 폴백: %s", exc)
            _detector_local.detector = False
            return None
        _detector_local.detector = detector
    elif detector is False:
        return None

    detector.setInputSize(size)
    return detector


def detect_faces(image_bgr: np.ndarray) -> np.ndarray | None:
    """Detect faces in a reduced BGR image.

    Returns: an (N, 15) array - x, y, w, h, right eye xy, left eye xy, nose
    xy, mouth corners xy, score. The coordinates are in the input image's
    coordinate system.
    """
    h, w = image_bgr.shape[:2]
    detector = _get_detector((w, h))
    if detector is None:
        return None
    try:
        _, faces = detector.detect(image_bgr)
    except cv2.error as exc:
        log.debug("얼굴 검출 실패: %s", exc)
        return None
    return faces if faces is not None and len(faces) else None


# ------------------------------------------------------- sharpness measurement


def measure_patch(gray_patch: np.ndarray, noise_var: float = 0.0) -> tuple[float, float]:
    """(normalised Laplacian, normalised Tenengrad) of a greyscale patch.

    Both are divided by the patch variance to make them contrast-invariant.
    Tenengrad is more sensitive than Laplacian to directional motion blur,
    so it catches camera-shake frames better.

    Given noise_var (the frame noise variance σ², see frame_noise_sigma),
    the noise contribution is subtracted before measuring. That division
    was the root of the noise trap - noise raises the numerator and the
    denominator together, but it raises the numerator more, so a noisy soft
    frame at high ISO gets a sharp score (measured 1.63x inflation, rank
    reversal in 3 of 11 eye ROIs across 5 camera bodies).

    The contribution of white noise σ² is computed exactly from the sum of
    squared kernel coefficients: **20σ²** on the 3x3 Laplacian variance,
    **24σ²** on the mean of the summed squares of Sobel x/y, and **σ²** on
    the patch variance. Subtracting each brings the reversals to 0 while
    separating power and motion-blur response hold up
    (RESEARCH_FOCUS_NOISE.md). At the default 0.0 this is identical to
    before.
    """
    if gray_patch.size == 0:
        return 0.0, 0.0

    patch = gray_patch.astype(np.float32)
    variance = float(patch.var()) - noise_var

    # A region with no signal is treated as unscoreable (see the
    # MIN_VARIANCE docstring). Without filtering here, dark background
    # noise beats the subject. Since scoring runs on the variance with the
    # noise removed, a patch that is 'nothing but noise' drops out from the
    # correction alone - the gate stays on as a second line of defence.
    if variance < MIN_VARIANCE:
        return 0.0, 0.0

    laplacian = max(
        float(cv2.Laplacian(patch, cv2.CV_32F).var()) - 20.0 * noise_var, 0.0
    ) / variance

    gx = cv2.Sobel(patch, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(patch, cv2.CV_32F, 0, 1, ksize=3)
    tenengrad = max(
        float(np.mean(gx * gx + gy * gy)) - 24.0 * noise_var, 0.0
    ) / variance

    return laplacian, tenengrad


_NOISE_KERNEL = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], np.float32)
"""Immerkær (1996) noise estimation mask. Its response to white noise has a
standard deviation of exactly 6σ (sum of squared coefficients 36), so σ can
be recovered back out of the median absolute deviation."""


def frame_noise_sigma(gray: np.ndarray, grid: int = 5, tile: int = 96) -> float:
    """Estimate the noise standard deviation of the whole frame (the value
    to pass to measure_patch).

    **Measured once per frame, not per patch.** Measured per patch on an
    eye ROI, real texture such as eyelashes and hair leaks into the mask
    response, so the sharper the patch the more σ̂ inflates (measured
    2~3x) - which then over-subtracts on sharp eyes and eats into
    separating power. Being a median, it is not dragged around by texture
    as long as more than half the frame is flat.

    ISO is not used as prior information. Measured (a 90-frame A6700
    sample), preview σ̂ at the same ISO 2000 spread from 0.49 to 6.42 -
    scene brightness and in-camera NR dominate, so ISO carries almost no
    information, and there are files, such as Panasonic RW2, where the ISO
    itself cannot be read.

    Running filter2D over the whole image blows the read budget (~30ms per
    frame) at 26MP, so it runs only on a grid of sample tiles. The outer
    2px of a tile is contaminated by filter2D's border reflection.
    """
    h, w = gray.shape[:2]
    if h < 8 or w < 8:
        return 0.0

    patches = []
    if h <= tile * 2 or w <= tile * 2:
        patches.append(gray.astype(np.float32))
    else:
        ys = np.linspace(0, h - tile, grid).astype(int)
        xs = np.linspace(0, w - tile, grid).astype(int)
        for y in ys:
            for x in xs:
                patches.append(gray[y:y + tile, x:x + tile].astype(np.float32))

    samples = []
    for patch in patches:
        response = cv2.filter2D(patch, -1, _NOISE_KERNEL)
        trimmed = response[2:-2, 2:-2]
        if trimmed.size:
            samples.append(np.abs(trimmed).ravel())
    if not samples:
        return 0.0
    pooled = np.concatenate(samples)
    return float(1.4826 * np.median(pooled) / 6.0)


def gradient_energy(gray_patch: np.ndarray) -> float:
    """Gradient energy, un-normalised.

    Used when comparing regions within one image. Tiles of the same photo
    share one exposure, so normalising is unnecessary; worse, normalising
    lets a dark noisy region beat the real subject.
    """
    if gray_patch.size == 0:
        return 0.0
    patch = gray_patch.astype(np.float32)
    gx = cv2.Sobel(patch, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(patch, cv2.CV_32F, 0, 1, ksize=3)
    return float(np.mean(gx * gx + gy * gy))


def _saturate(value: float, k: float) -> float:
    """Map a 0~inf value monotonically onto 0~100. 50 points at k."""
    return 100.0 * value / (value + k) if value > 0 else 0.0


# --------------------------------------------------------------- ROI selection


def _eye_roi(face: np.ndarray, scale: float, shape: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """ROI enclosing both landmark eyes, back-projected into the original
    coordinate system."""
    right_eye = np.array([face[4], face[5]], dtype=np.float32)
    left_eye = np.array([face[6], face[7]], dtype=np.float32)
    eye_distance = float(np.linalg.norm(left_eye - right_eye))
    if eye_distance < 2.0:
        return None

    center = (right_eye + left_eye) / 2.0 / scale
    half_w = (eye_distance * 0.9) / scale
    half_h = (eye_distance * 0.45) / scale
    return _clip_box(center[0] - half_w, center[1] - half_h, half_w * 2, half_h * 2, shape)


#: How many faces to go as far as measuring sharpness on as main-subject
#: candidates. Measuring dozens of people in a group photo at full
#: resolution is slow, so only the largest few by area are looked at.
MAX_FOCUS_CANDIDATES = 8

#: At or above this fraction of the best sharpness counts as an "in-focus
#: face".
IN_FOCUS_RATIO = 0.85


FACE_MAIN_MIN_CONTRAST = 8.0
"""A face needs at least this much contrast to be the main subject
(8-bit standard deviation).

A real face always has structure, because of the shadows of the eyes, nose
and mouth. A dark blotch in the audience is flat - measured, real faces
came in at 34~72 and false-detection blotches at 5.7~6.6.

**Set no higher than it takes to screen out the blotches.** Left at 12.0
to begin with, there was plenty of room for genuinely dark-exposed real
faces to fall out of the candidate pool as well. At 8.0 the blotches
(5.7~6.6) are still caught and there is headroom.
"""


def _patch_contrast(face: np.ndarray, gray_full: np.ndarray,
                    scale: float, shape: tuple[int, int]) -> float:
    """How much contrast there is inside the face box. 0 if unmeasurable."""
    box = _clip_box(face[0] / scale, face[1] / scale,
                    face[2] / scale, face[3] / scale, shape)
    x, y, w, h = box
    if w <= 0 or h <= 0:
        return 0.0
    patch = gray_full[y:y + h, x:x + w]
    return float(patch.std()) if patch.size else 0.0


def _pick_central_face(faces: np.ndarray, gray_full: np.ndarray,
                       scale: float, shape: tuple[int, int]) -> int:
    """Single-subject composition first - picked by the validated weighting
    area 0.25 : centre 1 : sharpness 0.25.

    An option for genres that put the protagonist dead centre, such as
    portraits and fan-site shooting. Validated at 95.4% on a held-out set
    of 47,990 frames with implicit ground truth (spot-selected AF = the
    photographer's protagonist); drop the sharpness term and it is 90.0%,
    so it cannot be dropped. On 110 frames labelled as the stage/group
    genre the current pick wins instead (75.5% vs 68.2%) - which is why
    this is an **analysis start option** rather than the default
    (RESEARCH_METADATA.md section 8).

    Sharpness is computed the same way as in the research: the face patch
    is reduced to a 160px long edge and the Laplacian variance taken - so
    that size does not contaminate the sharpness measurement.
    """
    height, width = shape[:2]
    diag_half = (width ** 2 + height ** 2) ** 0.5 / 2
    max_area = max(float(f[2]) * float(f[3]) for f in faces) or 1.0

    sharps = []
    for f in faces:
        box = _clip_box(f[0] / scale, f[1] / scale,
                        f[2] / scale, f[3] / scale, shape)
        x, y, w, h = box
        patch = gray_full[y:y + h, x:x + w] if w > 7 and h > 7 else None
        if patch is None or not patch.size:
            sharps.append(0.0)
            continue
        if max(w, h) > 160:
            ratio = 160.0 / max(w, h)
            patch = cv2.resize(patch, (max(8, int(w * ratio)),
                                       max(8, int(h * ratio))))
        sharps.append(float(cv2.Laplacian(patch, cv2.CV_64F).var()))
    max_sharp = max(sharps) or 1.0

    best, best_score = 0, -1.0
    for index, f in enumerate(faces):
        x, y, w, h = (float(v) for v in f[:4])
        area = w * h / max_area
        cx, cy = x + w / 2, y + h / 2
        central = 1.0 - min(
            (((cx - width / 2) ** 2 + (cy - height / 2) ** 2) ** 0.5) / diag_half,
            1.0)
        score = 0.25 * area + 1.0 * central + 0.25 * (sharps[index] / max_sharp)
        if score > best_score:
            best, best_score = index, score
    return best


def _pick_main_face(
    faces: np.ndarray,
    gray_full: np.ndarray,
    scale: float,
    shape: tuple[int, int],
    laplacian_k: float,
    tenengrad_k: float,
) -> int:
    """Pick the index of the main subject out of several faces.

    This used to look only at area x confidence. That lets a passer-by
    caught large in the foreground beat the person in focus behind them;
    the ROI goes to the blurred face and a well-shot frame gets a low
    score.

    In a photograph the "main subject" is whatever the photographer focused
    on. So we first measure the actual sharpness of each face, keep only
    the faces that fall on the in-focus side, and pick among those by
    area x confidence. In a group photo where everyone is at the same
    distance they are all on the in-focus side, so the result comes out the
    same as before - the only case that changes is a shallow depth of field
    where some are in focus and some are not.
    """
    area = faces[:, 2] * faces[:, 3]
    confidence = np.clip(faces[:, 14], 0.0, None)
    size_rank = area * confidence

    if len(faces) == 1:
        return 0

    # Drop low-confidence detections from the main-subject candidates. If a
    # speaker cone or a dark blotch sits in this slot, the whole frame's
    # focus scoring is wrong. If only low ones exist we have no choice but
    # to pick from among them - better than picking nobody.
    trusted = [i for i in range(len(faces))
               if confidence[i] >= FACE_MAIN_MIN_SCORE]
    pool = trusted if trusted else list(range(len(faces)))

    # Drop patches with almost no structure too. A real face always has
    # contrast, because of the shadows of the eyes, nose and mouth.
    # Measured (DSC03360): six real faces had standard deviations of 43~72
    # while two dark blotches in the audience were 5.7 and 6.6, and one of
    # those cleared the threshold at confidence 0.80 and was picked as the
    # main subject.
    detailed = [i for i in pool
                if _patch_contrast(faces[i], gray_full, scale, shape)
                >= FACE_MAIN_MIN_CONTRAST]
    if detailed:
        pool = detailed

    if len(pool) == 1:
        return int(pool[0])

    # Only the top candidates by area are measured at full resolution
    ordered = sorted(pool, key=lambda i: size_rank[i], reverse=True)
    candidates = ordered[:MAX_FOCUS_CANDIDATES]

    sharpness: dict[int, float] = {}
    for index in candidates:
        box = _clip_box(
            faces[index][0] / scale, faces[index][1] / scale,
            faces[index][2] / scale, faces[index][3] / scale, shape,
        )
        if min(box[2], box[3]) < MIN_ROI_PX:
            continue
        x, y, w, h = box
        # Compare on **un-normalised** gradient energy.
        #
        # This used to use _measure_sharpness (the value divided by the
        # patch variance). Dark regions have a small variance, so noise
        # alone sends the value soaring. Measured (DSC04240): the
        # protagonist's face on stage was brightness 140, gradient 4886,
        # yet normalised sharpness 51; a dark audience face at the back was
        # brightness 19, gradient 233, yet normalised sharpness 83. So the
        # audience got picked as the main subject every time (all 3
        # user-reported frames showed the same pattern).
        #
        # Faces within one photo share one exposure, so normalising is
        # unnecessary. Tile selection already uses gradient_energy for the
        # same reason.
        #
        # **Take the square root.** Gradient energy is proportional to the
        # square of contrast, so used raw it overwhelmingly favours bright,
        # busy faces. Then only whoever is lit hardest keeps becoming the
        # main subject. The square root squashes it back down to something
        # proportional to contrast, so several faces clear the
        # IN_FOCUS_RATIO threshold below together and the final call passes
        # to area and confidence.
        energy = gradient_energy(gray_full[y:y + h, x:x + w])
        sharpness[int(index)] = float(np.sqrt(max(0.0, energy)))

    if not sharpness:
        return int(max(pool, key=lambda i: size_rank[i]))

    best = max(sharpness.values())
    if best <= 0:
        return int(max(pool, key=lambda i: size_rank[i]))

    # The faces on the in-focus side - among those, the larger and more
    # certain one is the main subject
    in_focus = [i for i, value in sharpness.items() if value >= best * IN_FOCUS_RATIO]
    return max(in_focus, key=lambda i: size_rank[i])


def _clip_box(x: float, y: float, w: float, h: float, shape: tuple[int, int]) -> tuple[int, int, int, int]:
    """Clip a box to inside the image bounds."""
    height, width = shape
    x0 = max(0, int(round(x)))
    y0 = max(0, int(round(y)))
    x1 = min(width, int(round(x + w)))
    y1 = min(height, int(round(y + h)))
    return x0, y0, max(0, x1 - x0), max(0, y1 - y0)


def _nearest_face(af_box: tuple[int, int, int, int],
                  faces: tuple[tuple[int, int, int, int], ...]) -> int:
    """Index of the face nearest the AF box centre. For the confidence
    signal (af_face).

    Zone AF points at the torso, but in frames with two or more people the
    faces are separated horizontally, so nearest-to-centre is enough -
    validated on 117 labelled frames, and a "torso below the row of faces"
    rule gave the same result (research_af_confidence.py).
    """
    ax, ay = af_box[0] + af_box[2] / 2.0, af_box[1] + af_box[3] / 2.0
    best, best_dist = -1, float("inf")
    for index, (x, y, w, h) in enumerate(faces):
        dx = ax - (x + w / 2.0)
        dy = ay - (y + h / 2.0)
        dist = dx * dx + dy * dy
        if dist < best_dist:
            best_dist, best = dist, index
    return best


def _boxes_overlap(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> bool:
    """Whether two (x, y, w, h) boxes overlap. Used to subtract the face
    region out of the background tiles."""
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    return not (ax0 + aw <= bx0 or bx0 + bw <= ax0 or ay0 + ah <= by0 or by0 + bh <= ay0)


def _best_tile(gray_small: np.ndarray, scale: float, shape: tuple[int, int],
               grid: tuple[int, int] = (6, 4),
               exclude: tuple[float, float, float, float] | None = None
               ) -> tuple[int, int, int, int] | None:
    """Take the grid tile with the most real detail as the subject (or the
    background).

    Picked on raw gradient energy rather than on the normalised value.
    Tiles within one photo share one exposure so normalising is
    unnecessary, and normalising lets noise in a dark background beat the
    subject (measured: noise tile ten_raw 13 vs subject 7808, but after
    normalising 14.4 vs 3.9 - reversed).

    Given exclude (a face box in the reduced image's coordinate system),
    tiles overlapping it are skipped. Used when looking for the sharpest
    background region outside the face. In that mode, None is returned if
    there is no usable tile or the grid cannot be formed.
    """
    cols, rows = grid
    h, w = gray_small.shape[:2]
    tile_h, tile_w = h // rows, w // cols
    if tile_h < 8 or tile_w < 8:
        return None if exclude is not None else (0, 0, shape[1], shape[0])

    best_value, best_rc = -1.0, None
    for r in range(rows):
        for c in range(cols):
            if exclude is not None and _boxes_overlap(
                (c * tile_w, r * tile_h, tile_w, tile_h), exclude
            ):
                continue
            tile = gray_small[r * tile_h:(r + 1) * tile_h, c * tile_w:(c + 1) * tile_w]
            energy = gradient_energy(tile)
            if energy > best_value:
                best_value, best_rc = energy, (r, c)

    if best_rc is None:
        return None

    r, c = best_rc
    # Reach a little into the neighbouring tiles to absorb the case where
    # the subject straddles a tile boundary
    x = (c * tile_w - tile_w * 0.25) / scale
    y = (r * tile_h - tile_h * 0.25) / scale
    return _clip_box(x, y, (tile_w * 1.5) / scale, (tile_h * 1.5) / scale, shape)


def _measure_sharpness(
    gray_full: np.ndarray, box: tuple[int, int, int, int],
    laplacian_k: float, tenengrad_k: float, noise_var: float = 0.0,
) -> float:
    """Final sharpness (0~100) of a box region, measured the same way as
    the ROI."""
    x, y, w, h = box
    lap_raw, ten_raw = measure_patch(gray_full[y:y + h, x:x + w], noise_var)
    return 0.4 * _saturate(lap_raw, laplacian_k) + 0.6 * _saturate(ten_raw, tenengrad_k)


MIN_EYE_PX = 12.0
"""Below this eye width, openness is not measured.

On a distant face the eye is only a few pixels across, and a person
looking at it cannot call it either. Forcing a value out means penalising
on that value, so it is left as 'not measured' outright.
"""


def _measure_eye_opening(image_bgr: np.ndarray, box) -> float:
    """Eye aspect ratio (EAR) of the main subject. -1 if unmeasurable.

    **Uses whichever of the two eyes is more open.** On a face in profile
    the far eye is barely visible and always comes out as 'closed', and
    penalising on that drops every profile frame.

    The cost is 1.3ms per face. Only the one main subject is measured, so
    4000 frames comes to a little over 5 seconds - no effect on analysis
    time.
    """
    try:
        from . import face_mesh

        if not face_mesh.available():
            return -1.0
        points = face_mesh.landmarks(
            image_bgr, (float(box[0]), float(box[1]),
                        float(box[2]), float(box[3])))
        if points is None:
            return -1.0

        best = -1.0
        for ring, ear_points in (
            (face_mesh.LEFT_EYE, face_mesh.LEFT_EAR_POINTS),
            (face_mesh.RIGHT_EYE, face_mesh.RIGHT_EAR_POINTS),
        ):
            xs = [points[i][0] for i in ring]
            if float(max(xs) - min(xs)) < MIN_EYE_PX:
                continue
            p = [points[i][:2] for i in ear_points]
            vertical = (float(np.linalg.norm(p[1] - p[5]))
                        + float(np.linalg.norm(p[2] - p[4])))
            horizontal = float(np.linalg.norm(p[0] - p[3]))
            best = max(best, vertical / (2.0 * horizontal + 1e-6))
        return best
    except Exception:  # noqa: BLE001 - a failed eye must not stop analysis
        log.debug("눈 개폐 측정 실패", exc_info=True)
        return -1.0


# ----------------------------------------------------------------- entry point


def reduce_for_detection(image_bgr: np.ndarray,
                         detect_long_edge: int = DETECT_LONG_EDGE) -> np.ndarray:
    """Reduced copy for face detection. The most expensive resize in
    analysing one frame (measured 28.5ms).

    Split out so callers can use it too. When the scene fingerprint and the
    thumbnail reuse the same reduction, the cost of shrinking down from the
    6192x4128 original again disappears - measured on a Mac, dhash
    10.9->2.7ms, thumbnail 47.0->2.3ms.
    """
    full_h, full_w = image_bgr.shape[:2]
    long_edge = max(full_h, full_w)
    scale = min(1.0, detect_long_edge / long_edge) if long_edge else 1.0
    if scale >= 1.0:
        return image_bgr
    return cv2.resize(
        image_bgr,
        (max(1, round(full_w * scale)), max(1, round(full_h * scale))),
        interpolation=cv2.INTER_AREA,
    )


def analyze_focus(
    image_bgr: np.ndarray,
    detect_long_edge: int = DETECT_LONG_EDGE,
    laplacian_k: float = LAPLACIAN_K,
    tenengrad_k: float = TENENGRAD_K,
    force_main_face: int | None = None,
    af_box: tuple[int, int, int, int] | None = None,
    use_af_roi: bool = False,
    center_priority: bool = False,
    noise_compensation: bool = True,
    reduced: np.ndarray | None = None,
) -> FocusResult:
    """Measure the focus state of one preview image.

    Detection runs on the reduced copy, the ROI sharpness measurement at
    full resolution. Sharpness only means anything measured on the original
    pixels.

    Note: change detect_long_edge and the face detection result changes,
    which can change the ROI. frame_sharpness is measured at
    FRAME_LONG_EDGE regardless of this value, so comparisons between frames
    hold up across a settings change.

    Given force_main_face, automatic selection is skipped and that face is
    taken as the main subject. This is the case where the user picked a
    different face on screen. The ROI, the sharpness and the background
    sharpness all have to be recomputed **entirely against that face** for
    the scoring to actually follow - change only the display and the score
    stays on the wrong face. So rather than keeping a separate path, this
    function is simply run again.
    """
    full_h, full_w = image_bgr.shape[:2]
    shape = (full_h, full_w)

    long_edge = max(full_h, full_w)
    scale = min(1.0, detect_long_edge / long_edge) if long_edge else 1.0
    small = reduced if reduced is not None else reduce_for_detection(
        image_bgr, detect_long_edge)

    gray_small = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray_full = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)

    # Exposure state - measuring on the reduced copy is enough
    mean_luma = float(gray_small.mean())
    total = gray_small.size
    clipped_highlights = float(np.count_nonzero(gray_small >= 250)) / total
    clipped_shadows = float(np.count_nonzero(gray_small <= 5)) / total

    faces = detect_faces(small)
    roi: tuple[int, int, int, int] | None = None
    source = FocusSource.FRAME
    face_count = 0
    face_confidence = 0.0
    face_area_ratio = 0.0
    background_sharpness = 0.0
    face_box_small: tuple[float, float, float, float] | None = None
    face_boxes: tuple[tuple[int, int, int, int], ...] = ()
    face_scores: tuple[float, ...] = ()
    main_face = -1

    if faces is not None:
        face_count = len(faces)
        if force_main_face is not None and 0 <= force_main_face < len(faces):
            index = int(force_main_face)
        else:
            if center_priority:
                index = _pick_central_face(faces, gray_full, scale, shape)
            else:
                index = _pick_main_face(faces, gray_full, scale, shape,
                                        laplacian_k, tenengrad_k)
        face = faces[index]
        main_face = index
        face_boxes = tuple(
            _clip_box(f[0] / scale, f[1] / scale, f[2] / scale, f[3] / scale, shape)
            for f in faces
        )
        face_scores = tuple(float(f[14]) for f in faces)
        face_confidence = float(face[14])
        face_area_ratio = float(face[2] * face[3]) / float(small.shape[0] * small.shape[1])
        face_box_small = (float(face[0]), float(face[1]), float(face[2]), float(face[3]))

        candidate = _eye_roi(face, scale, shape)
        if candidate and min(candidate[2], candidate[3]) >= MIN_ROI_PX:
            roi, source = candidate, FocusSource.EYE
        else:
            candidate = _clip_box(
                face[0] / scale, face[1] / scale, face[2] / scale, face[3] / scale, shape
            )
            if min(candidate[2], candidate[3]) >= MIN_ROI_PX:
                roi, source = candidate, FocusSource.FACE

    if roi is None and af_box is not None and use_af_roi:
        # Use the AF position the camera recorded as the ROI (a precise
        # analysis option). We never get here if a face or eye ROI exists -
        # what zone AF records is the zone (the torso), not the eye
        # (measured on 47 frames, RESEARCH_METADATA.md), so it cannot beat
        # face detection. Only on frames with no face do we use the
        # camera's actual focus position instead of guessing at "the
        # sharpest tile".
        candidate = _clip_box(af_box[0], af_box[1], af_box[2], af_box[3], shape)
        if candidate and min(candidate[2], candidate[3]) >= MIN_ROI_PX:
            roi, source = candidate, FocusSource.AF

    if roi is None:
        candidate = _best_tile(gray_small, scale, shape)
        if candidate and min(candidate[2], candidate[3]) >= MIN_ROI_PX:
            roi, source = candidate, FocusSource.TILE

    if roi is None:
        roi, source = (0, 0, full_w, full_h), FocusSource.FRAME

    # The face the camera's AF pointed at - a confidence signal (af_face).
    # It does not touch the score. Matched to the face nearest the AF box
    # centre. Zone AF points at the torso, but in two-person frames the
    # faces are separated horizontally, so nearest-to-centre is accurate
    # enough (validated on 117 labelled frames: 87% correct on the 54
    # frames of the same person, 49% on the 63 frames of a different
    # person, research_af_confidence.py). -1 if there is no af_box or no
    # face.
    af_face = -1
    if af_box is not None and face_boxes:
        af_face = _nearest_face(af_box, face_boxes)

    # Frame σ² for the noise subtraction. The ROI and the background are
    # measured at full resolution, so the σ of the full resolution is used.
    # On the reduced copy (frame_gray) the reduction averages the noise
    # away and σ comes out completely different, so that one is measured
    # separately.
    # With noise_compensation=False this is the same measurement as v3
    # (no subtraction).
    noise_var = frame_noise_sigma(gray_full) ** 2 if noise_compensation else 0.0

    x, y, w, h = roi
    laplacian_raw, tenengrad_raw = measure_patch(gray_full[y:y + h, x:x + w], noise_var)

    laplacian = _saturate(laplacian_raw, laplacian_k)
    tenengrad = _saturate(tenengrad_raw, tenengrad_k)
    # Weight Tenengrad more - it discriminates motion blur better
    sharpness = 0.4 * laplacian + 0.6 * tenengrad

    # If a face was used as the ROI, also measure the sharpest background
    # outside the face. When the face is soft but the background is crisp
    # (focus fell behind the subject), face-priority mode penalises it.
    if source in (FocusSource.EYE, FocusSource.FACE) and face_box_small is not None:
        bg_box = _best_tile(gray_small, scale, shape, exclude=face_box_small)
        if bg_box and min(bg_box[2], bg_box[3]) >= MIN_ROI_PX:
            background_sharpness = _measure_sharpness(
                gray_full, bg_box, laplacian_k, tenengrad_k, noise_var
            )

    # A baseline independent of the ROI. It must be measured at the
    # FRAME_LONG_EDGE scale. If detect_long_edge happens to be the same,
    # the gray_small already built is reused.
    if max(gray_small.shape[:2]) == FRAME_LONG_EDGE:
        frame_gray = gray_small
    else:
        frame_gray = resize_long_edge(gray_full, FRAME_LONG_EDGE)
    frame_lap_raw, frame_ten_raw = measure_patch(
        frame_gray,
        frame_noise_sigma(frame_gray) ** 2 if noise_compensation else 0.0,
    )
    frame_sharpness = 0.4 * _saturate(frame_lap_raw, FRAME_LAPLACIAN_K) + 0.6 * _saturate(
        frame_ten_raw, FRAME_TENENGRAD_K
    )

    eyes_open = -1.0
    if 0 <= main_face < len(face_boxes):
        eyes_open = _measure_eye_opening(image_bgr, face_boxes[main_face])

    return FocusResult(
        eyes_open=eyes_open,
        sharpness=sharpness,
        laplacian=laplacian,
        tenengrad=tenengrad,
        source=source,
        frame_sharpness=frame_sharpness,
        roi=roi,
        face_count=face_count,
        face_confidence=face_confidence,
        face_area_ratio=face_area_ratio,
        background_sharpness=background_sharpness,
        faces=face_boxes,
        face_scores=face_scores,
        main_face=main_face,
        af_face=af_face,
        clipped_highlights=clipped_highlights,
        clipped_shadows=clipped_shadows,
        mean_luma=mean_luma,
        # Record alongside them which size roi and faces are coordinates
        # against. Without this the display side can only guess, and that
        # guess was in fact wrong, so boxes got drawn in the wrong place.
        source_width=full_w,
        source_height=full_h,
    )
