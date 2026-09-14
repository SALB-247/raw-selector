"""Lens vignetting profiles measured from the camera's own JPEG.

A lens missing from the lensfun database gets no automatic correction at
all, and it fails quietly (measured: the Tamron 20-40mm A062 is on 16 of 23
A1 frames and is not in the DB). The camera, though, has already corrected
its own embedded JPEG - every A1 frame carries `VignettingCorrection = auto`
- while the demosaic we develop from has not been touched. The ratio of the
two is the correction the camera applied, and it can be written out as a
lensfun profile that the existing automatic path picks up unchanged.

**What comes out reproduces the camera JPEG, not a flat field.** Measured
on the Tamron 150-500 A057 at 150mm f/5, the camera's own correction is
x1.26 at the corner where lensfun's profile (fitted from the correction
tables Sony embeds) says x2.21 - Sony's in-camera correction is partial.
The JPEG is the render people cull from, so matching it is the right
target; for a lens lensfun already knows, its stronger profile stays
available beside this one.

How the measurement is made (each step was needed - see PLAN_LENS_PROFILE):

  1. The JPEG sits in the centre of the sensor (8640x5760 inside 8660x5784),
     so the two are aligned by a centred crop first. Without it the corner
     radius is off and the profile folds over at the edge.
  2. Only mid-tones are compared. The camera's tone curve is steep in the
     shadows and flat near white, so the ratio there is tone, not falloff.
  3. The ratio is taken in linear light, on the engine's own transfer.
  4. Several frames of the same lens / focal / aperture can be averaged;
     the scene cancels out of the ratio, but averaging tightens it further
     (four frames agree to +-0.03 at the corner).

The model is lensfun's "pa": falloff f(r) = 1 + k1 r^2 + k2 r^4 + k3 r^6
with r normalised so the image corner is 1 (half-diagonal), and lensfun
stores the *falloff* - the correction it applies is 1/f. Both conventions
were verified against the bundled DB (reproduction error 0.0011).

Distortion comes from the same pair, as a displacement rather than a
brightness ratio. Content in the camera JPEG has moved relative to the
neutral render by the correction the camera applied; phase correlation on
a grid of patches reads that movement, and lensfun's "ptlens" terms are
fitted **through lensfunpy itself** - a candidate is written to a private
XML, lensfun renders its field, and the gap to the measured field is what
gets minimised. That sidesteps every convention question (normalisation,
auto-scale) because the thing being fitted is exactly what the engine will
apply. Validated on a lens the bundled DB knows (Tamron 150-500 at 500mm):
the measured field has the DB field's shape and size, and the fit lands at
0.8px against the camera JPEG where the DB's own terms leave 1.8px.

Distortion needs the camera's distortion correction switched on in the
body; with it off the JPEG is as distorted as the RAW and the field is
zero. (Not quite zero as lensfun applies it: even all-zero ptlens terms
come with a +0.1% uniform scale - measured, residual 0.001 - so the fit
for "no correction" is the small term set that cancels it, about 1px.)
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

FIT_SIZE = (1200, 800)
"""Width and height the pair is shrunk to before comparing. Vignetting is a
smooth radial field, so resolution buys nothing above this and the demosaic
is 50MP."""

MIDTONE_RENDER = (0.05, 0.45)
"""Linear luma window of the neutral render that counts as mid-tone."""

MIDTONE_TARGET = (0.03, 0.70)
"""The same for the camera JPEG - a little wider, because the correction
itself lifts the corners."""

INNER_RADIUS = 0.3
"""Frames are brought onto one scale before averaging by the median of the
ratio inside this radius. The fit itself carries a free scale, so a single
frame never depends on what happens to sit in the centre - a subject in
the middle used to bend the whole profile."""

RADIAL_BINS = 8
MIN_BIN_PIXELS = 200
"""A radial bin needs this many mid-tone pixels to count as measured."""

MAX_FIT_ERROR = 0.1
"""Relative fit error above which a vignetting measurement is refused.
A frame the method can read lands at 0.02~0.05 (Sony A1 and Canon R6M3
alike, once the tone is matched); scene residue the model cannot follow
shows up here first."""

TONE_QUANTILES = 33
TONE_INNER_RADIUS = 0.3
TONE_MIN_RANGE = 8.0
"""The tone map between the neutral render and the camera JPEG is learnt
on the inner frame only. Vignetting is ~1 there, so what the quantile
match learns is the picture style and nothing else; learnt on the whole
frame it would absorb the corner falloff it is meant to expose.

This is what made another body work. Canon's picture style differs far
more from the neutral render than Sony's, and on ten R6M3 frames of one
lens the raw ratio scattered x1.00~x1.65 at the corner (fit error
0.46~0.67); tone-matched, x1.25~x1.42 at 0.017~0.034."""

MIN_COVERAGE = 0.75
"""Share of the radial bins that have to be measured before a profile is
worth saving."""

MIN_REACH = 0.85
"""The outermost measured bin has to reach at least this radius. A stage
shot with black corners can have a clean centre and still say nothing
about the corners - which is the only place a vignetting profile is
decided. Coverage alone let that through (half the bins measured, all of
them inner)."""

FIT_BINS = 40
"""The fit runs on radial-bin medians, not on pixels. Per pixel, the inner
frame outnumbers the corner hundreds to one and the fit undershoots
exactly where the falloff is - measured 2% short at r = 0.98. Medians
because a real frame is never flat: the scene leaves residue in the ratio,
and a mean chases it.

The fit is also constrained to be monotone (every term pulls the falloff
down with radius). Left free, the three polynomial terms are nearly
collinear on [0, 1] and follow that residue - on four A057 frames the
unconstrained curve dipped below 1 in the middle and folded over at the
corner. A camera's partial correction is monotone; the model should be."""

FIELD_WIDTH = 2160
"""Working width the pair is brought to before the displacement field is
read. Displacements are a few tens of pixels at full resolution; at this
size they are still several pixels, which phase correlation resolves to a
fraction, and the demosaic no longer has to be handled at 50MP."""

FIELD_PATCH = 96
FIELD_STEP = 48
FIELD_MIN_TEXTURE = 0.75
"""A patch has to carry this much high-pass detail (std) to be worth
correlating; flat sky locks onto nothing.

Low on purpose. A real 50MP frame brought down to the working size and
high-passed has patch std of only 1.7~2.7 - the downscale averages the
fine grain away - so a gate at 1.5 sat on the edge and dropped half the
frame (583 of 1255 patches on a 500mm shot). Phase correlation reads a
clean radial field out of that low-contrast content (tangential rms 1.3px,
fit residual 1.9px); the response gate below is what rejects the truly
empty patches."""
FIELD_COARSE_MIN = 4 * FIELD_PATCH
FIELD_PASSES = 2
"""Coarse-to-fine. A patch reads a move of up to a third of its size -
32px at 96 - and only a move that is nearly the same across the patch: a
wide zoom's in-camera correction moves the corner of a 1280px Panasonic
preview by 60px and more (S5M2X + 24-105 at 24mm: 2.4% of scale plus
the barrel) and stretches a patch there by a tenth, and the readings
collapsed past r=0.6 (reach 0.79). So the field is read at half size
first, where the same move is half as long, and at each size the render
is pulled forward by the field known so far before the patches are read
again - the read is then of what is left, small and unstretched. Each
size takes FIELD_PASSES reads, the second on its own first read, which
is what carries the field out to a corner the coarser size could not
reach. A frame narrower than FIELD_COARSE_MIN has no half size worth a
grid and is read at one size."""

FIELD_MIN_RESPONSE = 0.12
"""Phase-correlation peak below this is noise, not a match."""
MIN_PATCHES = 150
"""Fewer matched patches than this and the field is too thin to fit."""

ORACLE_WIDTH = 540
"""The size lensfun's field is rendered at during the fit. Measured on a
real frame: 540 and 2160 land on the same terms to four decimals and the
same residual; the cost is in re-reading the candidate, not the pixels."""
ORACLE_ITERS = 45

SUBJECT_DISTANCE = 10.0
"""The distance lensfun's entries are written at. EXIF does not carry one,
and the engine initialises the modifier at 10m as well."""


@dataclass(frozen=True)
class Measured:
    """One fitted vignetting entry."""

    lens: str
    focal: float
    aperture: float
    terms: tuple[float, float, float]
    corner_gain: float
    """The fitted correction at r = 0.95, centre = 1. This is the figure
    to show people - "the camera brightens the corners by x1.12"."""
    fit_error: float
    """Relative mean absolute error of the fitted curve against the
    per-bin medians. Scene residue the monotone model refuses to follow
    lands here, so 0.02~0.04 on a real frame is normal."""
    coverage: float
    """Share of radial bins that had enough mid-tone pixels."""
    reach: float
    """Outer edge of the outermost bin that was measured (0~1)."""
    frames: int
    distortion: "DistortionMeasured | None" = None
    """The distortion fit from the same pair, when one was asked for."""

    @property
    def usable(self) -> bool:
        return (self.coverage >= MIN_COVERAGE and self.reach >= MIN_REACH
                and self.fit_error <= MAX_FIT_ERROR)


@dataclass(frozen=True)
class DistortionMeasured:
    """One fitted ptlens entry."""

    lens: str
    focal: float
    terms: tuple[float, float, float]
    """lensfun ptlens a, b, c."""
    residual_px: float
    """Mean gap between the fitted field and the measured one, in pixels
    of the full-resolution frame. 3~4px on a 50MP frame is what a good
    fit leaves; the DB's own terms leave about twice that against the
    camera JPEG."""
    patches: int
    reach: float
    """Outer radius (0~1) the matched patches got to."""
    shift_px: tuple[float, float]
    """The whole-frame shift that was taken out before fitting - a large
    one means the camera corrected around an off-centre point."""

    @property
    def usable(self) -> bool:
        return (self.patches >= MIN_PATCHES and self.reach >= MIN_REACH
                and self.residual_px < 12.0)


# ------------------------------------------------------------ measurement


ALIGN_CROP_TOLERANCE = 0.97
"""Below this width ratio the smaller image is a *downscaled* preview, not
a crop of the sensor, and the larger one is resized to it instead. Sony
and Canon embed a full-size JPEG (8640 in 8660, 6960 in 6959) - a crop of
a few pixels; Panasonic RW2 embeds 1920px, a third of the sensor, and
centre-cropping the demosaic to that would compare the middle of the
frame against the whole of it."""


def _align(render: np.ndarray, target: np.ndarray,
           area: tuple[int, int] | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Put the pair into one frame.

    The embedded JPEG is normally the middle of the sensor at full size:
    the larger of the two is centre-cropped to the other. Comparing the
    full demosaic against it uncropped puts the corner of one on the
    near-corner of the other, which is exactly where the profile is
    decided. A JPEG that is a downscaled preview instead (see
    ALIGN_CROP_TOLERANCE) has the render resized to it.

    `area` is the camera's own image area (width, height, in sensor
    orientation) when the file says it - the frame the JPEG covers. The
    demosaic is cut to it first: LibRaw hands back a few rows and columns
    more than the camera keeps (Panasonic S1R: 8392x5620 around a
    8368x5584 JPEG), and resizing the whole of it onto the preview
    stretches it by that margin - 0.3% one way, 0.6% the other - which
    the fit then reads as distortion. For a full-size JPEG the cut and the
    crop below are the same thing.
    """
    rh, rw = render.shape[:2]
    if area is not None:
        aw, ah = (int(v) for v in area)
        if (rw < rh) != (aw < ah):
            aw, ah = ah, aw
        if 0 < aw <= rw and 0 < ah <= rh:
            y0, x0 = (rh - ah) // 2, (rw - aw) // 2
            render = render[y0:y0 + ah, x0:x0 + aw]
            rh, rw = render.shape[:2]
    th, tw = target.shape[:2]
    ratio = min(rw, tw) / max(rw, tw)
    if ratio < ALIGN_CROP_TOLERANCE:
        if tw < rw:
            render = cv2.resize(render, (tw, int(round(rh * tw / rw))),
                                interpolation=cv2.INTER_AREA)
        else:
            target = cv2.resize(target, (rw, int(round(th * rw / tw))),
                                interpolation=cv2.INTER_AREA)
        rh, rw = render.shape[:2]
        th, tw = target.shape[:2]
    h, w = min(rh, th), min(rw, tw)

    def cut(image: np.ndarray) -> np.ndarray:
        ih, iw = image.shape[:2]
        y0, x0 = (ih - h) // 2, (iw - w) // 2
        return image[y0:y0 + h, x0:x0 + w]

    return cut(render), cut(target)


def _luma(bgr8: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(bgr8, cv2.COLOR_BGR2YCrCb)[:, :, 0].astype(np.float32)


def _linear_luma(bgr8: np.ndarray) -> np.ndarray:
    """Luma on the engine's own light scale (0~1)."""
    from .engine import to_light

    return to_light(_luma(bgr8))


def _match_tone_on_centre(luma_from: np.ndarray, luma_to: np.ndarray) -> np.ndarray:
    """Map luma_from onto luma_to's tone, learnt on the inner frame only -
    see TONE_INNER_RADIUS. Called with the JPEG as luma_from."""
    inner = _radius(luma_from.shape) < TONE_INNER_RADIUS
    if inner.sum() < MIN_BIN_PIXELS:
        return luma_from
    quantiles = np.linspace(0.02, 0.98, TONE_QUANTILES)
    src = np.quantile(luma_from[inner], quantiles)
    dst = np.quantile(luma_to[inner], quantiles)
    if src[-1] - src[0] < TONE_MIN_RANGE:
        # A flat centre carries no tone information; a map learnt from it
        # is quantisation noise. The gain the ratio needs is taken by the
        # fit's own scale anyway.
        return luma_from
    luma_r = luma_from
    src = np.maximum.accumulate(src)
    dst = np.maximum.accumulate(dst)
    # Only tones the centre actually contains can be mapped. Outside that
    # range no extrapolation has a basis - anchored to (0,0) a picture
    # style's toe under-read the dark corners (x1.20 for a true x1.34),
    # carried on at the end slope it over-read them (x1.45) - so those
    # pixels are marked NaN and drop out of the comparison. A real frame's
    # centre spans the tones its corners have; a flat one has nothing to
    # map and is left alone (TONE_MIN_RANGE).
    mapped = np.interp(luma_r, src, dst).astype(np.float32)
    mapped[(luma_r < src[0]) | (luma_r > src[-1])] = np.nan
    return mapped


def _radius(shape: tuple[int, int]) -> np.ndarray:
    """Distance from the centre, corner = 1 (lensfun's normalisation)."""
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w]
    return np.hypot(xx - (w - 1) / 2.0, yy - (h - 1) / 2.0) / (0.5 * np.hypot(w, h))


def ratio_map(render: np.ndarray, target: np.ndarray, *,
              area: tuple[int, int] | None = None) -> tuple[np.ndarray, np.ndarray]:
    """The camera's correction, pixel by pixel: JPEG light / render light.

    Returns (ratio, valid). ratio is normalised so the centre is 1; valid
    marks the mid-tone pixels the ratio was actually taken from - the rest
    hold nothing meaningful.
    """
    render, target = _align(render, target, area)
    render_s = cv2.resize(render, FIT_SIZE, interpolation=cv2.INTER_AREA)
    target_s = cv2.resize(target, FIT_SIZE, interpolation=cv2.INTER_AREA)
    luma_r = _luma(render_s)
    luma_t = _luma(target_s)
    # The JPEG is brought onto the neutral render's tone, not the other
    # way round. Both are then on the near-linear tone the render has, and
    # the ratio in light is the falloff itself; mapped the other way the
    # ratio carried the picture style's local slope and over-read the
    # corners (synthetic S-curve: x1.45 for a true x1.34).
    luma_t = _match_tone_on_centre(luma_t, luma_r)
    from .engine import to_light

    lin_r = to_light(luma_r)
    lin_t = to_light(luma_t)

    valid = (np.isfinite(lin_t)
             & (lin_r > MIDTONE_RENDER[0]) & (lin_r < MIDTONE_RENDER[1])
             & (lin_t > MIDTONE_TARGET[0]) & (lin_t < MIDTONE_TARGET[1]))
    ratio = (np.nan_to_num(lin_t, nan=1.0) / np.maximum(lin_r, 1e-4)).astype(np.float32)
    return ratio, valid


def _inner_scale(ratio: np.ndarray, valid: np.ndarray) -> float:
    """The frame's own scale - the median ratio inside INNER_RADIUS. 1.0
    when the inner frame has no mid-tones to read it from; the fit's free
    scale takes over then."""
    inner = valid & (_radius(ratio.shape) < INNER_RADIUS)
    if inner.sum() < MIN_BIN_PIXELS:
        return 1.0
    return float(np.median(ratio[inner]))


def average_ratio(maps: list[tuple[np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
    """Average several (ratio, valid) maps; a pixel counts where any frame
    had it. The scene cancels out of each map already; this tightens the
    corners, which few frames light evenly."""
    total = np.zeros(FIT_SIZE[::-1], np.float64)
    count = np.zeros(FIT_SIZE[::-1], np.int32)
    for ratio, valid in maps:
        # frames differ in exposure; bring each onto its own inner scale
        scale = _inner_scale(ratio, valid)
        total[valid] += ratio[valid] / scale
        count[valid] += 1
    valid = count > 0
    ratio = np.where(valid, total / np.maximum(count, 1), 1.0).astype(np.float32)
    return ratio, valid


def fit(ratio: np.ndarray, valid: np.ndarray, *, lens: str, focal: float,
        aperture: float, frames: int = 1) -> Measured:
    """Fit lensfun's PA falloff to the measured correction."""
    r = _radius(ratio.shape)

    # coverage and reach: which radial bins actually got measured
    edges = np.linspace(0.0, 1.0, RADIAL_BINS + 1)
    measured_bins, reach = 0, 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        if (valid & (r >= lo) & (r < hi)).sum() >= MIN_BIN_PIXELS:
            measured_bins += 1
            reach = hi
    coverage = measured_bins / RADIAL_BINS

    # the fit itself runs on fine radial-bin medians so every radius weighs
    # the same and the scene does not - see FIT_BINS
    fine = np.linspace(0.0, 1.0, FIT_BINS + 1)
    rs, fs = [], []
    for lo, hi in zip(fine[:-1], fine[1:]):
        m = valid & (r >= lo) & (r < hi)
        if m.sum() >= MIN_BIN_PIXELS // 4:
            rs.append(float(r[m].mean()))
            fs.append(float(np.median(np.clip(ratio[m], 0.2, 5.0))))
    if len(rs) < 6:
        return Measured(lens, focal, aperture, (0.0, 0.0, 0.0), 1.0, 0.0,
                        coverage, reach, frames)

    rv, ys = np.array(rs), np.array(fs)
    # lensfun stores the falloff f; the camera applied 1/f. With a free
    # scale c and every term pulling f down: 1/y = c - a r^2 - b r^4 - d r^6
    design = np.stack([np.ones_like(rv), -rv ** 2, -rv ** 4, -rv ** 6], axis=1)
    c, a, b, d = _nonnegative_lstsq(design, 1.0 / ys)
    if c <= 0:
        return Measured(lens, focal, aperture, (0.0, 0.0, 0.0), 1.0, 0.0,
                        coverage, reach, frames)
    k1, k2, k3 = -a / c, -b / c, -d / c

    def curve(x: np.ndarray | float) -> np.ndarray | float:
        return 1.0 / (1.0 + k1 * x ** 2 + k2 * x ** 4 + k3 * x ** 6)

    # The model is 1/y = c * f, so the measured correction on the model's
    # scale is y * c (not y / c - that slip read 0.88 on every frame whose
    # ratio kept a global gain, and refused them).
    fit_error = float(np.abs(curve(rv) - ys * c).mean() / max(np.median(ys * c), 1e-6))
    corner_gain = float(curve(0.95))
    return Measured(lens, focal, aperture, (k1, k2, k3), corner_gain,
                    fit_error, coverage, reach, frames)


def _nonnegative_lstsq(design: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Least squares with every coefficient held at or above zero - a small
    active-set solve, since the design has four columns."""
    active = list(range(design.shape[1]))
    while active:
        coef, *_ = np.linalg.lstsq(design[:, active], target, rcond=None)
        if (coef >= 0.0).all():
            out = np.zeros(design.shape[1])
            out[active] = coef
            return out
        active = [column for column, value in zip(active, coef) if value >= 0.0]
    return np.zeros(design.shape[1])


def measure_pair(render: np.ndarray, target: np.ndarray, *, lens: str,
                 focal: float, aperture: float,
                 area: tuple[int, int] | None = None) -> Measured:
    """One frame: neutral render (display uint8) against its camera JPEG."""
    ratio, valid = ratio_map(render, target, area=area)
    return fit(ratio, valid, lens=lens, focal=focal, aperture=aperture)


def measure_pairs(pairs: list[tuple[np.ndarray, np.ndarray]], *, lens: str,
                  focal: float, aperture: float) -> Measured:
    """Several frames of the same lens / focal / aperture, averaged."""
    maps = [ratio_map(render, target) for render, target in pairs]
    ratio, valid = average_ratio(maps)
    return fit(ratio, valid, lens=lens, focal=focal, aperture=aperture,
               frames=len(pairs))


# ------------------------------------------------------------ distortion


def _highpass(gray: np.ndarray) -> np.ndarray:
    # high-pass: tone and vignetting differ between the two, and the
    # correlation must lock onto edges, not onto brightness
    return gray - cv2.GaussianBlur(gray, (0, 0), 8)


def _working_pair(render: np.ndarray, target: np.ndarray,
                  area: tuple[int, int] | None = None):
    """Both frames aligned, at FIELD_WIDTH, grey."""
    sensor_w = render.shape[1]
    render, target = _align(render, target, area)
    h0, w0 = render.shape[:2]
    # never upscale: a downscaled preview is read at its own size
    width = min(FIELD_WIDTH, w0)
    size = (width, int(round(h0 * width / w0)))
    # scale is working / *sensor* pixels, not working / aligned. With a
    # downscaled preview the aligned pair is the preview, and a residual
    # quoted in its pixels would read four times too small on a Panasonic
    # (1920 of 8392) - the thresholds are meant in sensor pixels.
    scale = width / sensor_w

    def prepared(image: np.ndarray) -> np.ndarray:
        return cv2.cvtColor(cv2.resize(image, size, interpolation=cv2.INTER_AREA),
                            cv2.COLOR_BGR2GRAY).astype(np.float32)

    return prepared(render), prepared(target), scale


def _predict(field: np.ndarray, centres: np.ndarray) -> np.ndarray:
    """The field's Gaussian-weighted mean at each centre, fading to zero
    where the field has nothing near rather than cutting off - a step in
    the prediction would be a seam in the warped render."""
    sigma = float(FIELD_PATCH)
    out = np.zeros_like(centres)
    for i in range(0, len(centres), 256):
        c = centres[i:i + 256]
        d2 = ((c[:, None, 0] - field[None, :, 0]) ** 2
              + (c[:, None, 1] - field[None, :, 1]) ** 2)
        weight = np.exp(-d2 / (2.0 * sigma * sigma))
        total = weight.sum(axis=1) + np.exp(-2.0)
        out[i:i + 256] = (weight @ field[:, 2:4]) / total[:, None]
    return out


def _dense(field: np.ndarray, h: int, w: int) -> np.ndarray:
    """The field as an (h, w, 2) map over the frame: _predict on a grid
    of FIELD_STEP, resized."""
    ys = np.arange(FIELD_STEP / 2, h, FIELD_STEP)
    xs = np.arange(FIELD_STEP / 2, w, FIELD_STEP)
    gx, gy = np.meshgrid(xs, ys)
    grid = _predict(field, np.c_[gx.ravel(), gy.ravel()])
    grid = grid.reshape(len(ys), len(xs), 2).astype(np.float32)
    return cv2.resize(grid, (w, h), interpolation=cv2.INTER_LINEAR)


def _field_from_pair(a: np.ndarray, b: np.ndarray,
                     predicted: np.ndarray | None = None) -> np.ndarray:
    """Phase-correlate a grid of patches; (N, 4) of x, y, dx, dy at the
    working size, whole-frame shift not yet removed.

    Phase correlation, not normalised cross-correlation: on the low-
    contrast content a downscaled real frame has, NCC finds no peak worth
    trusting while phase correlation - every frequency at unit weight -
    still reads a clean radial field. Its one weakness, a sub-pixel bias
    on narrow-band textures, does not arise on photographs.

    `predicted` is a field in this level's pixels the read starts from
    (see FIELD_PASSES): the *render* is pulled forward by it, the patches
    read what is left, and the prediction at each centre is added back.

    The render, not the target. The field is indexed the way lensfun
    draws it - by where content lands, since the oracle's field at a
    point is the move of the content that arrives there. Pulling the
    render forward by the prediction and reading the rest gives the move
    of the content arriving at the patch; warping the target back instead
    read the move of the content *leaving* the patch, which differs by
    the move times the local stretch - nothing at Sony's 30px moves, 4px
    of a 75px move at 5% stretch (measured on the synthetic wide zoom,
    and the whole 11px residual of the Panasonic 24mm frame).
    """
    h, w = a.shape
    size, step = FIELD_PATCH, FIELD_STEP
    window = cv2.createHanningWindow((size, size), cv2.CV_32F)
    limit = size / 3
    known = None
    if predicted is not None and len(predicted):
        known = _dense(predicted, h, w)
        gy, gx = np.mgrid[0:h, 0:w].astype(np.float32)
        a = cv2.remap(a, gx - known[:, :, 0], gy - known[:, :, 1],
                      cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                      borderValue=0.0)
    points = []
    for y in range(0, h - size + 1, step):
        for x in range(0, w - size + 1, step):
            pa = a[y:y + size, x:x + size]
            if pa.std() < FIELD_MIN_TEXTURE:
                continue
            pb = b[y:y + size, x:x + size]
            # copies: at a DFT-optimal patch size phaseCorrelate applies the
            # window *in place*, and a and b are read again by the next
            # patch (half of it overlaps) and by the next pass
            (dx, dy), response = cv2.phaseCorrelate(pa.copy(), pb.copy(), window)
            if response < FIELD_MIN_RESPONSE or abs(dx) > limit or abs(dy) > limit:
                continue
            cx, cy = x + size // 2, y + size // 2
            if known is not None:
                dx += float(known[cy, cx, 0])
                dy += float(known[cy, cx, 1])
            points.append((x + size / 2, y + size / 2, dx, dy))
    if not points:
        return np.zeros((0, 4), np.float64)
    return np.array(points, np.float64)


def _read_field(a: np.ndarray, b: np.ndarray,
                predicted: np.ndarray | None) -> np.ndarray:
    """FIELD_PASSES reads at one size, each starting from the last."""
    points = predicted
    for _ in range(FIELD_PASSES):
        points = _field_from_pair(a, b, predicted=points)
    return points


def displacement_field(render: np.ndarray, target: np.ndarray, *,
                       area: tuple[int, int] | None = None):
    """Where the camera JPEG moved each patch of the neutral render.

    Returns (points, width, height, scale): points is an (N, 4) array of
    patch centre x, y and displacement dx, dy - all at the working size -
    with the whole-frame shift already removed; scale is working pixels
    per sensor pixel, so displacement / scale is in sensor pixels whatever
    size the embedded JPEG came at.
    """
    a, b, scale = _working_pair(render, target, area)
    h, w = a.shape
    coarse = None
    if min(w, h) >= FIELD_COARSE_MIN:
        half = (w // 2, h // 2)
        coarse = _read_field(
            _highpass(cv2.resize(a, half, interpolation=cv2.INTER_AREA)),
            _highpass(cv2.resize(b, half, interpolation=cv2.INTER_AREA)), None)
        coarse = coarse * 2.0  # centres and moves alike, back to this size
    points = _read_field(_highpass(a), _highpass(b), coarse)
    if len(points):
        points[:, 2:4] -= _frame_shift(points, w, h)
    return points, w, h, scale


def _frame_shift(points: np.ndarray, w: int, h: int) -> np.ndarray:
    """The whole-frame shift, read from the inner quarter where distortion
    is smallest."""
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    r = np.hypot(points[:, 0] - cx, points[:, 1] - cy) / (0.5 * np.hypot(w, h))
    inner = r < 0.25
    if inner.sum() < 5:
        return np.zeros(2)
    return np.median(points[inner, 2:4], axis=0)


REACH_RING = 0.1
REACH_RING_MIN = 15
"""How far out a field counts as read: the outer edge of the outermost
ring of REACH_RING that still holds REACH_RING_MIN patches. Not a
percentile of the radii - on a 3:2 grid only a few percent of the
patches lie beyond r=0.8 whatever the frame shows, so the 97th
percentile mostly counted how many corner patches had texture: a
Panasonic 24mm frame read to r=0.9 with 57 patches past 0.8 came out at
0.83, below a 105mm frame of the same body at 0.86 with 63."""


def _points_reach(points: np.ndarray, w: int, h: int) -> float:
    if len(points) == 0:
        return 0.0
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    r = np.hypot(points[:, 0] - cx, points[:, 1] - cy) / (0.5 * np.hypot(w, h))
    for edge in np.arange(1.0, REACH_RING / 2, -REACH_RING):
        if ((r >= edge - REACH_RING) & (r < edge)).sum() >= REACH_RING_MIN:
            return float(round(edge, 2))
    return 0.0


class _Oracle:
    """lensfun's own field for candidate ptlens terms, at the patch points.

    Each candidate goes through a private one-lens XML so that what is
    minimised is precisely what the engine will later apply - lensfun's
    normalisation and its auto-scale included, neither of which we have
    to know.
    """

    def __init__(self, points: np.ndarray, w: int, h: int, focal: float,
                 crop_factor: float) -> None:
        import tempfile

        self.focal = focal
        self.crop_factor = crop_factor
        self.cw = ORACLE_WIDTH
        self.ch = max(8, int(round(h * ORACLE_WIDTH / w)))
        s = self.cw / w
        self.xs = np.clip((points[:, 0] * s).round().astype(int), 0, self.cw - 1)
        self.ys = np.clip((points[:, 1] * s).round().astype(int), 0, self.ch - 1)
        self.scale = s
        self.points_xy = points[:, 0:2]
        self.w, self.h = w, h
        self.path = Path(tempfile.mkdtemp(prefix="lens_probe_")) / "probe.xml"

    def _write(self, terms) -> None:
        a, b, c = (float(v) for v in terms)
        self.path.write_text(
            '<lensdatabase version="1"><lens><maker>Probe</maker>'
            '<model>Probe 10-1000mm f/1.0</model><mount>Probe</mount>'
            f'<cropfactor>{self.crop_factor:g}</cropfactor><calibration>'
            f'<distortion model="ptlens" focal="{self.focal:g}" '
            f'a="{a:.8f}" b="{b:.8f}" c="{c:.8f}"/></calibration></lens>'
            '</lensdatabase>', encoding="utf-8")

    def field(self, terms) -> np.ndarray | None:
        import lensfunpy

        a, b, c = (float(v) for v in terms)
        self.path.write_text(
            '<lensdatabase version="1"><lens><maker>Probe</maker>'
            '<model>Probe 10-1000mm f/1.0</model><mount>Probe</mount>'
            f'<cropfactor>{self.crop_factor:g}</cropfactor><calibration>'
            f'<distortion model="ptlens" focal="{self.focal:g}" '
            f'a="{a:.8f}" b="{b:.8f}" c="{c:.8f}"/></calibration></lens>'
            '</lensdatabase>', encoding="utf-8")
        db = lensfunpy.Database(paths=[str(self.path)], load_common=False)
        lens = [l for l in db.lenses if (l.model or "").startswith("Probe")][0]
        modifier = lensfunpy.Modifier(lens, self.crop_factor, self.cw, self.ch)
        modifier.initialize(self.focal, 8.0, SUBJECT_DISTANCE,
                            pixel_format=np.float32)
        coords = modifier.apply_geometry_distortion()
        if coords is None:
            return None
        # coords: for each corrected pixel, where it came from. Content
        # therefore moved by (corrected - source).
        dx = self.xs - coords[self.ys, self.xs, 0]
        dy = self.ys - coords[self.ys, self.xs, 1]
        field = np.stack([dx, dy], axis=1) / self.scale
        # The measurement had its whole-frame shift taken out; take the
        # same out of the oracle, or a constant of lensfun's own (pixel
        # centre convention) has to be absorbed by a spurious radial term.
        # Measured: with identical frames the fit still produced a 1px
        # field until this was done.
        return field - _frame_shift(np.column_stack([self.points_xy, field]),
                                    self.w, self.h)


def _nelder_mead(fn, x0, step: float, iters: int):
    n = len(x0)
    simplex = [np.array(x0, float)] + [np.array(x0, float) + step * np.eye(n)[i]
                                       for i in range(n)]
    values = [fn(p) for p in simplex]
    for _ in range(iters):
        order = np.argsort(values)
        simplex = [simplex[i] for i in order]
        values = [values[i] for i in order]
        centre = np.mean(simplex[:-1], axis=0)
        reflected = centre + (centre - simplex[-1])
        fr = fn(reflected)
        if fr < values[0]:
            expanded = centre + 2.0 * (centre - simplex[-1])
            fe = fn(expanded)
            simplex[-1], values[-1] = (expanded, fe) if fe < fr else (reflected, fr)
        elif fr < values[-2]:
            simplex[-1], values[-1] = reflected, fr
        else:
            contracted = centre + 0.5 * (simplex[-1] - centre)
            fc = fn(contracted)
            if fc < values[-1]:
                simplex[-1], values[-1] = contracted, fc
            else:
                simplex = [simplex[0] + 0.5 * (p - simplex[0]) for p in simplex]
                values = [fn(p) for p in simplex]
    best = int(np.argmin(values))
    return simplex[best], values[best]


def fit_distortion(points: np.ndarray, w: int, h: int, scale: float, *,
                   lens: str, focal: float, crop_factor: float = 1.0,
                   iters: int = ORACLE_ITERS) -> DistortionMeasured:
    """Fit lensfun ptlens terms to a measured displacement field."""
    reach = _points_reach(points, w, h)
    shift = _frame_shift(points, w, h) if len(points) else np.zeros(2)
    shift_px = tuple(float(v) for v in (np.asarray(shift) / scale))
    if len(points) < MIN_PATCHES:
        return DistortionMeasured(lens, focal, (0.0, 0.0, 0.0), float("inf"),
                                  len(points), reach, shift_px)

    oracle = _Oracle(points, w, h, focal, crop_factor)
    measured = points[:, 2:4]
    # A uniform scale between the two is not the lens. lensfun scales its
    # corrected frame to fill the image, so its corner stays put whatever
    # the terms; a camera may keep the centre instead and let the corners
    # run off - Panasonic at 24mm moves the corner 70px outward where
    # lensfun's arch returns to 0 - and no terms can bridge that. The
    # profile is the shape; the scale is solved for each candidate and
    # left out of the residual. The engine applies with lensfun's own
    # scale, so the develop matches the camera JPEG up to a zoom.
    rel = points[:, 0:2] - np.array([(w - 1) / 2.0, (h - 1) / 2.0])
    rel_norm = float((rel * rel).sum()) or 1.0

    def cost(terms) -> float:
        field = oracle.field(terms)
        if field is None:
            return 1e9
        gap = measured - field
        gap -= (gap * rel).sum() / rel_norm * rel
        return float(np.mean(np.hypot(gap[:, 0], gap[:, 1])))

    best, err = _nelder_mead(cost, (0.0, 0.0, 0.0), step=0.01, iters=iters)
    return DistortionMeasured(lens, focal, tuple(float(v) for v in best),
                              err / scale, len(points), reach, shift_px)


def measure_distortion(render: np.ndarray, target: np.ndarray, *, lens: str,
                       focal: float, crop_factor: float = 1.0,
                       area: tuple[int, int] | None = None) -> DistortionMeasured:
    """One frame: the displacement field, then the fit."""
    points, w, h, scale = displacement_field(render, target, area=area)
    return fit_distortion(points, w, h, scale, lens=lens, focal=focal,
                          crop_factor=crop_factor)


# ------------------------------------------------------------------ storage


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower() or "lens"


def profile_path(lens: str) -> Path:
    from .optics import user_lens_db_dir

    return user_lens_db_dir() / f"measured_{_slug(lens)}.xml"


def _fmt(value: float) -> str:
    return f"{value:.6f}"


def write_profile(measured: Measured, *, maker: str, mount: str,
                  crop_factor: float, path: Path | None = None,
                  camera: tuple[str, str] | None = None) -> Path:
    """Write (or merge into) the lensfun XML for this lens.

    One file per lens. Another focal / aperture of the same lens is added
    to the same <calibration>; the same pair measured again replaces the
    old entry, so re-measuring on a better frame is one click, not a
    hunt through a folder.
    """
    path = path or profile_path(measured.lens)
    path.parent.mkdir(parents=True, exist_ok=True)

    root = None
    if path.exists():
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            log.warning("측정 프로필 XML이 깨져 있어 새로 씁니다: %s", path)
            root = None
    if root is None or root.tag != "lensdatabase":
        root = ET.Element("lensdatabase", version="1")

    if camera is not None:
        cam_maker, cam_model = camera
        have = any((c.find("model") is not None and (c.find("model").text or "") == cam_model)
                   for c in root.findall("camera"))
        if not have:
            cam_el = ET.Element("camera")
            ET.SubElement(cam_el, "maker").text = cam_maker
            ET.SubElement(cam_el, "model").text = cam_model
            ET.SubElement(cam_el, "mount").text = mount
            ET.SubElement(cam_el, "cropfactor").text = f"{crop_factor:g}"
            root.insert(0, cam_el)

    lens_el = None
    for candidate in root.findall("lens"):
        model = candidate.find("model")
        if model is not None and (model.text or "") == measured.lens:
            lens_el = candidate
            break
    if lens_el is None:
        lens_el = ET.SubElement(root, "lens")
        ET.SubElement(lens_el, "maker").text = maker
        ET.SubElement(lens_el, "model").text = measured.lens
        ET.SubElement(lens_el, "mount").text = mount
        ET.SubElement(lens_el, "cropfactor").text = f"{crop_factor:g}"
        ET.SubElement(lens_el, "calibration")
    calibration = lens_el.find("calibration")
    if calibration is None:
        calibration = ET.SubElement(lens_el, "calibration")

    # replace an entry for the same focal / aperture
    for entry in list(calibration.findall("vignetting")):
        same = (abs(float(entry.get("focal", "0")) - measured.focal) < 0.5
                and abs(float(entry.get("aperture", "0")) - measured.aperture) < 0.05)
        if same:
            calibration.remove(entry)

    k1, k2, k3 = measured.terms
    ET.SubElement(
        calibration, "vignetting", model="pa",
        focal=f"{measured.focal:g}", aperture=f"{measured.aperture:g}",
        distance=f"{SUBJECT_DISTANCE:g}",
        k1=_fmt(k1), k2=_fmt(k2), k3=_fmt(k3),
    )

    distortion = measured.distortion
    if distortion is not None and distortion.usable:
        for entry in list(calibration.findall("distortion")):
            if abs(float(entry.get("focal", "0")) - distortion.focal) < 0.5:
                calibration.remove(entry)
        a, b, c = distortion.terms
        ET.SubElement(
            calibration, "distortion", model="ptlens",
            focal=f"{distortion.focal:g}",
            a=f"{a:.8f}", b=f"{b:.8f}", c=f"{c:.8f}",
        )

    comment = (" measured by RAW selector from the camera's embedded JPEG: "
               "this reproduces the camera's own correction, not a flat field ")
    if not any(isinstance(child, ET.Element) and child.tag is ET.Comment
               for child in list(calibration)):
        calibration.insert(0, ET.Comment(comment))

    ET.indent(root, space="    ")
    path.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")
    return path


def camera_geometry(metadata) -> tuple[str, float, bool]:
    """(mount, crop factor, known) of the body.

    From the lensfun DB when it has the body. When it does not, a mount
    string of our own made from the body name and the crop factor from
    EXIF (35mm-equivalent over real focal length; 1.0 without it). The
    profile then carries its own <camera> entry with that mount, so
    lensfun can pair the two - a body the DB has never heard of otherwise
    gets no correction from a profile measured on it.
    """
    from .optics import _database, _find_cameras_loose

    # The same loose lookup find_lens uses, deliberately. If it settles on
    # a neighbouring body, both the fit and the later application settle
    # on the same one - the same mount and the same crop factor at both
    # ends - so the profile still lands. Only a body the lookup returns
    # nothing at all for gets an entry of its own.
    db = _database()
    if db is not None and metadata is not None and metadata.camera_model:
        cameras = _find_cameras_loose(db, metadata.camera_model, metadata.camera_make)
        if cameras:
            return (cameras[0].mount or "Sony E",
                    float(cameras[0].crop_factor or 1.0), True)
    make = (getattr(metadata, "camera_make", None) or "Measured").strip()
    model = (getattr(metadata, "camera_model", None) or "body").strip()
    crop_factor = 1.0
    focal = float(getattr(metadata, "focal_length", 0) or 0)
    focal35 = float(getattr(metadata, "focal_length_35mm", 0) or 0)
    if focal > 0 and focal35 > 0:
        crop_factor = round(focal35 / focal, 2)
    return f"{make} {model} (measured)", crop_factor, False


def store(measured: Measured, metadata) -> Path:
    """Write the profile for this camera's mount and reload the DB, so the
    automatic correction sees it on the next render."""
    from .optics import reload_database

    mount, crop_factor, known = camera_geometry(metadata)
    maker = (getattr(metadata, "camera_make", None) or "Measured").strip()

    camera = None
    if not known:
        camera = (maker, (getattr(metadata, "camera_model", None) or "body").strip())
    path = write_profile(measured, maker=maker, mount=mount, crop_factor=crop_factor,
                         camera=camera)
    reload_database()
    return path


def measure_photo(path: Path, render: np.ndarray, metadata, *,
                  distortion: bool = True) -> Measured | None:
    """The current shot: its neutral render against its own embedded JPEG.

    Vignetting always; distortion too unless switched off (it is the slow
    part - several seconds of lensfun round trips). None when the file has
    no lens / focal / aperture to file the profile under, or no readable
    preview.
    """
    from ..raw_io import image_area, load_preview

    if metadata is None or not metadata.lens_model:
        return None
    focal = float(metadata.focal_length or 0.0)
    aperture = float(metadata.aperture or 0.0)
    if focal <= 0.0 or aperture <= 0.0:
        return None
    try:
        target = load_preview(path)
    except Exception as exc:  # noqa: BLE001 - no preview, nothing to measure
        log.info("%s: 내장 JPEG을 읽지 못해 비네팅을 잴 수 없습니다 (%s)", path.name, exc)
        return None
    area = image_area(path)
    measured = measure_pair(render, target, lens=metadata.lens_model,
                            focal=focal, aperture=aperture, area=area)
    if not distortion:
        return measured
    from dataclasses import replace

    _, crop_factor, _ = camera_geometry(metadata)
    fitted = measure_distortion(render, target, lens=metadata.lens_model,
                                focal=focal, crop_factor=crop_factor, area=area)
    return replace(measured, distortion=fitted)
