"""Adjustment engine.

Preview and final export run through the **same function**. If the two
diverged, what the user tuned in the preview would differ from the actual
file, and that would bring down the whole of this feature's credibility.
Only the resolution differs; the operations are identical.

The order of operations follows the Lightroom pipeline. The order decides
the result, so it must not be changed arbitrarily:
  geometry -> white balance -> exposure -> tone -> curves ->
  local contrast -> HSL -> colour grading -> saturation ->
  detail (sharpen/noise) -> effects -> watermark
"""

from __future__ import annotations

import logging
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from .settings import (
    HSL_BAND_CENTERS,
    BasicSettings,
    ColorGradeSettings,
    CurveSettings,
    DetailSettings,
    DevelopSettings,
    EffectSettings,
    GeometrySettings,
    HSLSettings,
    NoiseAlgorithm,
    OpticsSettings,
)

log = logging.getLogger(__name__)

_IDENTITY = np.arange(256, dtype=np.float32)


# ---------------------------------------------------------------- geometry


def apply_geometry(image: np.ndarray, geometry: GeometrySettings) -> np.ndarray:
    """Rotate -> flip -> straighten -> crop.

    Cropping has to come last so that the empty corners left behind by the
    rotation can be cut away.
    """
    if geometry.is_neutral():
        return image

    result = image
    for _ in range(geometry.rotate_quarters % 4):
        result = cv2.rotate(result, cv2.ROTATE_90_CLOCKWISE)

    if geometry.flip_horizontal:
        result = cv2.flip(result, 1)
    if geometry.flip_vertical:
        result = cv2.flip(result, 0)

    if geometry.straighten:
        height, width = result.shape[:2]
        matrix = cv2.getRotationMatrix2D(
            (width / 2, height / 2), geometry.straighten, 1.0
        )
        result = cv2.warpAffine(
            result, matrix, (width, height),
            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE,
        )

    if geometry.has_crop():
        height, width = result.shape[:2]
        x0 = int(round(np.clip(geometry.crop_left, 0.0, 1.0) * width))
        x1 = int(round(np.clip(geometry.crop_right, 0.0, 1.0) * width))
        y0 = int(round(np.clip(geometry.crop_top, 0.0, 1.0) * height))
        y1 = int(round(np.clip(geometry.crop_bottom, 0.0, 1.0) * height))
        # It must not die even if the crop comes in inverted or zero-sized
        if x1 - x0 >= 8 and y1 - y0 >= 8:
            result = result[y0:y1, x0:x1]

    return result


# ---------------------------------------------------------------- tone (LUT)


def smooth_curve_lut(points: list[tuple[float, float]]) -> np.ndarray:
    """Build a 256-entry LUT from a smooth curve through the control points.

    Linear interpolation kinks at every control point, which makes the tone
    transitions harsh. A Fritsch-Carlson monotone cubic spline joins them
    smoothly while ruling out overshoot (the curve doubling back, which
    reverses the gradation). The editor and the engine share this function,
    so what you see is what you get.
    """
    # Duplicate input x values put a 0 into h=diff(xs), which makes
    # delta=diff(ys)/h inf/nan and breaks the whole LUT (possible with
    # hand-edited or legacy presets). For a repeated x only the first
    # value is kept.
    pts = []
    last_x = None
    for x, y in sorted(points):
        if x != last_x:
            pts.append((x, y))
            last_x = x

    xs = np.array([p[0] for p in pts], dtype=np.float64)
    ys = np.array([p[1] for p in pts], dtype=np.float64)
    n = len(xs)
    if n < 2:
        value = ys[0] if n else 0.0
        return np.clip(np.full(256, value), 0, 255).astype(np.float32)

    h = np.diff(xs)
    delta = np.diff(ys) / h

    # Tangent (slope) at each control point. Start from the average of the
    # slopes of the two neighbouring intervals.
    m = np.empty(n)
    m[0], m[-1] = delta[0], delta[-1]
    for i in range(1, n - 1):
        m[i] = 0.0 if delta[i - 1] * delta[i] <= 0 else (delta[i - 1] + delta[i]) / 2

    # Monotonicity fix - too large a tangent makes the curve bulge, which
    # reverses the gradation.
    for i in range(n - 1):
        if delta[i] == 0:
            m[i] = m[i + 1] = 0.0
            continue
        a, b = m[i] / delta[i], m[i + 1] / delta[i]
        s = a * a + b * b
        if s > 9.0:
            t = 3.0 / np.sqrt(s)
            m[i], m[i + 1] = t * a * delta[i], t * b * delta[i]

    # Evaluate each input with a Hermite cubic on the interval it falls in
    # (vectorised).
    x = _IDENTITY.astype(np.float64)
    idx = np.clip(np.searchsorted(xs, x, side="right") - 1, 0, n - 2)
    t = (x - xs[idx]) / h[idx]
    t2, t3 = t * t, t * t * t
    result = (
        (2 * t3 - 3 * t2 + 1) * ys[idx]
        + (t3 - 2 * t2 + t) * h[idx] * m[idx]
        + (-2 * t3 + 3 * t2) * ys[idx + 1]
        + (t3 - t2) * h[idx] * m[idx + 1]
    )
    return np.clip(result, 0, 255).astype(np.float32)


# Backwards-compatible old name
_spline_lut = smooth_curve_lut


EXPOSURE_LIMIT_EV = 20.0
"""Ceiling (EV) on the exposure actually fed into the LUT computation.
Separate from the slider range (±5).

Presets are YAML the user edits by hand, so numbers outside the widget
range do come in. Fed through unchanged they break in two ways:

  - From `2.0 ** 1024` on, Python raises OverflowError. On export that is
    one exception per frame, so the whole batch ends with no output.
  - Below that (say +200 EV) there is no exception; instead the LUT
    becomes float32 inf, and `0 * inf` turns into **NaN** and stays in
    the first entry of the table. Black pixels go out as garbage values
    with no warning at all.

The clamp sits generously wide. Since exposure started being applied in
linear space, "±8 saturates everything" no longer holds - in linear the
dark values get pushed up too, so at +8 EV level 1 only reaches 78. Even
so, ±20 is far past anything a person can use, and the float64
intermediates do not go inf (there is plenty of headroom up to 2^20).
"""


def srgb_to_linear(value: np.ndarray) -> np.ndarray:
    """Inverse sRGB transfer function (0~1 -> 0~1) - display back to light."""
    value = np.asarray(value, dtype=np.float64)
    return np.where(value <= 0.04045, value / 12.92,
                    np.power((np.abs(value) + 0.055) / 1.055, 2.4))


def linear_to_srgb(value: np.ndarray) -> np.ndarray:
    """Light to display value (0~1 -> 0~1)."""
    value = np.clip(np.asarray(value, dtype=np.float64), 0.0, None)
    return np.where(value <= 0.0031308, value * 12.92,
                    1.055 * np.power(value, 1.0 / 2.4) - 0.055)


def _bt709_encode(linear: np.ndarray) -> np.ndarray:
    """LibRaw's default output transfer function. **It is not sRGB.**

    Confirmed by measurement - pairing the postprocess output with the
    gamma=(1,1) result and recovering the curve gives 0.02 levels against
    BT.709 and up to 15.9 levels against sRGB.
    """
    linear = np.clip(np.asarray(linear, dtype=np.float64), 0.0, None)
    return np.where(linear < 0.018, linear * 4.5,
                    1.099 * np.power(linear, 1.0 / 2.222) - 0.099)


@lru_cache(maxsize=4)
def _baseline_transfer(profiled: bool) -> tuple[np.ndarray, np.ndarray]:
    """Transfer function of the space the adjustments live in - a
    (linear, display 0~1) sample pair. Do not write to it.

    **The space exposure actually sees is not the decoder output.** For
    RAW, what reaches apply_settings has already been through
    `postprocess(BT.709) -> camera-model correction -> standard profile
    curve`. That composite curve has to be inverted for exposure to become
    an operation on light.

    Pixel difference against the physically correct answer (+2EV from
    sensor linear via LibRaw exp_shift):

        formula               A6700   S5M2X   R6M3 (corrected)
        assume sRGB           11.65    8.78    13.96
        assume BT.709         19.18   10.20    20.86
        **composite invert**   1.61    0.19     0.98

    The point is that assuming BT.709 is actually worse - matching only
    the decoder gamma gets you nowhere, because the profile curve
    dominates.

    profiled=False means a JPEG/HEIF original. It went through neither
    demosaic nor profile, so it is **true sRGB** and has to be inverted
    as sRGB.

    Camera-model correction (channel gains) and the profile's saturation
    gain both mix channels, so they do not fit into a 1D curve. Only the
    grey axis is exact and saturated colours are an approximation, but
    even for R6M3 in the table above (a body that does get correction)
    that is 0.98 levels, which is good enough in practice.
    """
    linear = np.linspace(0.0, 1.0, 4096)
    if not profiled:
        display = linear_to_srgb(linear)
    else:
        encoded = _bt709_encode(linear) * 255.0
        profile = smooth_curve_lut(list(_STANDARD_PROFILE_CURVE))
        display = np.interp(encoded, _IDENTITY, profile) / 255.0
    # It has to be monotone to be used for inverse interpolation. The
    # profile curve is built monotone, but sampling error can flip it very
    # slightly.
    display = np.maximum.accumulate(display)
    # These arrays come back from the cache, so writing is blocked. They
    # used to be tuples, hence immutable, but turning 4096 values back into
    # an array on every call was 43% of apply_exposure's cost (141µs out of
    # 328µs), and splitting into to_light/from_light doubled that.
    for array in (linear, display):
        array.flags.writeable = False
    return linear, display


def apply_exposure(levels: np.ndarray, ev: float,
                   profiled: bool = True) -> np.ndarray:
    """Apply exposure ev (EV) to 0~255 display values. Multiplies **the
    light**.

    0~255 is not light; it is a display value that has been through the
    transfer function and the profile curve. Multiplying that directly by
    2^EV is not exposure correction but a much cruder, different operation
    - that is what the old implementation did, and at +2EV everything
    above level 64 (25% grey) turned pure white (measured on
    P1032946.RW2: 25.7% of pixels lost their information with all three
    channels saturated).

    `_baseline_transfer` supplies the curve that undoes this - its
    docstring has a table of how close each formula gets to the
    physically correct answer.

    The fitting side (camera_look) and the render side (_tone_lut) **must
    use the same operation**. Fixing only one of them makes an exposure
    value tuned to the camera JPEG draw differently on screen.
    """
    values = np.asarray(levels, dtype=np.float64)
    if not ev:
        return values.astype(np.float32)
    ev = float(np.clip(ev, -EXPOSURE_LIMIT_EV, EXPOSURE_LIMIT_EV))
    return from_light(to_light(values, profiled) * (2.0 ** ev), profiled)


def to_light(levels: np.ndarray, profiled: bool = True) -> np.ndarray:
    """0~255 display value -> light (0~1). The space `apply_exposure`
    multiplies in.

    The side that **estimates** exposure has to use this function too. If
    estimation and application use different spaces, the exposure value
    found means something different on screen - camera_look actually did
    that, and the value shown on the slider was off by 0.24~0.81 EV.
    """
    linear, display = _baseline_transfer(profiled)
    return np.interp(np.clip(np.asarray(levels, dtype=np.float64), 0.0, 255.0)
                     / 255.0, display, linear)


def from_light(light: np.ndarray, profiled: bool = True) -> np.ndarray:
    """Light (0~1) -> 0~255 display value. The inverse of `to_light`.

    **Every operation on light has to happen between this pair.** If only
    the direction into light is factored out into a function, the formula
    for coming back gets rewritten at every call site, and one of those
    copies going stale is something this file has already lived through
    twice (camera_look's exposure estimation, optics' vignetting).

    Light above 1 pins to 255 - the screen cannot draw brighter than
    that, so it is physically right as well.
    """
    linear, display = _baseline_transfer(profiled)
    return (np.interp(np.asarray(light, dtype=np.float64), linear, display)
            * 255.0).astype(np.float32)


#: The light level where the highlight shoulder starts. Below this
#: nothing is touched.
#:
#: 0.85 compresses only the top 0.23 stops. Measured (exposure matched to
#: the camera JPEG brightness, then run through the whole render path):
#:
#:     frame               no shoulder     0.55     0.85
#:     Panasonic +2.82EV         3.19%    0.00%    0.00%
#:     Sony +0.86EV              0.32%    0.09%    0.09%
#:     Canon +0.36EV             0.02%    0.00%    0.00%
#:     unique levels (bright)  256/141  247/131  254/138
#:
#: **Pushing harder buys nothing.** 0.55 removes exactly as much clipping
#: while cutting more gradation (247 against 254), and it visibly presses
#: down every bright surface. 0.85 is the value that does only as much as
#: is needed.
HIGHLIGHT_KNEE = 0.85


def _shoulder(light: np.ndarray, ev: float) -> np.ndarray:
    """Apply the shoulder to light. The value must be **pre-clip**.

    Because from_light pins light above 1 to 255, what arrives here has
    to be a value that has not been through that step yet. The first
    attempt applied this to the result of apply_exposure and had no
    effect at all for exactly that reason - it was already clipped, so
    there was nothing left for the shoulder to look at.
    """
    white = float(2.0 ** float(ev))
    knee = float(HIGHLIGHT_KNEE)
    # knee at 1 means "no shoulder". Bail out here so we never divide by
    # zero - this actually gets hit when comparing on and off by editing
    # the constant.
    if white <= 1.0 or knee >= 1.0:
        return light           # exposure pulled down never reaches the top

    lit = np.array(light, dtype=np.float32, copy=True)
    over = lit > np.float32(knee)
    if not np.any(over):
        return lit

    a = np.float32((white - knee) / (1.0 - knee))
    t = np.clip((lit[over] - np.float32(knee))
                / np.float32(white - knee), 0.0, 1.0)
    lit[over] = np.float32(knee) + np.float32(1.0 - knee) * (
        a * t / (np.float32(1.0) + (a - np.float32(1.0)) * t))
    return lit


def apply_exposure_with_shoulder(levels: np.ndarray, ev: float,
                                 profiled: bool = True) -> np.ndarray:
    """Compress light approaching the ceiling - packed together instead
    of spilling over.

    **Why this is needed.** Our neutral render is darker than the camera
    JPEG (measured +0.77~+2.82EV). When the user raises exposure by that
    much, `apply_exposure` multiplies the light, light above 1 pins to
    255, and **bright surfaces go flat** - this is what the user
    described as "the bright parts get mushed". The camera reaches the
    same brightness through a shoulder, so nothing pins there (measured
    0.00~0.17%).

    **This is a stage kept separate from exposure.** apply_exposure has
    to keep the property "multiplies light by exactly 2^EV" - the slider
    value has to be a physical quantity, and camera_look's exposure
    estimation leans on that property. A shoulder is not a
    multiplication, so folding it in breaks that identity.

    Below knee not a single grain is touched. We have lost gradation
    before by pressing down a range where the pixels are packed in the
    profile curve (see the comment on that constant), so avoiding the
    places where pixels actually are is a design condition here.

    **Pure white stays pure white.** ev fixes the white point W = 2^EV,
    and [knee, W] is moved onto [knee, 1]. The first version used an
    exponential shoulder asymptotic to 1, which meant nothing could ever
    reach 1 and 255 sank to 246 at +1EV - an existing test caught that.

    The curve used is s(t) = a*t / (1 + (a-1)*t), a = (W-knee)/(1-knee).
    s(0)=0, s(1)=1 and s'(0)=a, so the derivative joins at exactly 1 at
    the seam - the place where the shoulder starts does not show as a
    band.

    **`apply_exposure` is left alone.** That side has to keep the
    property "multiplies light by exactly 2^EV" - the slider value has to
    be a physical quantity and camera_look's exposure estimation leans on
    it. A shoulder is not a multiplication, so merging them breaks that
    identity. So the multiply-and-shoulder combination gets its own
    function, and only the render path (_tone_lut) uses this one.
    """
    ev = float(np.clip(ev, -EXPOSURE_LIMIT_EV, EXPOSURE_LIMIT_EV))
    lit = np.asarray(to_light(levels, profiled), dtype=np.float32) \
        * np.float32(2.0 ** ev)
    return from_light(_shoulder(lit, ev), profiled)


def _tone_lut(basic: BasicSettings, profiled: bool = True) -> np.ndarray:
    """Fold exposure, contrast and highlights/shadows/whites/blacks into
    a single LUT.

    Building the 256-entry table once and applying it, instead of
    computing per pixel, costs almost nothing even at 6000x4000.

    profiled is the space these values live in (see _baseline_transfer).
    Only exposure looks at it - the remaining terms are unitless sliders
    and are defined on top of display values.
    """
    lut = _IDENTITY.copy()

    if basic.exposure:
        # Multiply and shoulder in one step. Split apart, the multiplied
        # result is already clipped at 255 and the shoulder has nothing
        # left to look at (apply_exposure_with_shoulder).
        lut = apply_exposure_with_shoulder(lut, basic.exposure, profiled)

    normalized = np.clip(lut / 255.0, 0.0, 1.0)

    # Each range is pushed with a Gaussian weight so the boundaries do not
    # show up as bands
    if basic.shadows:
        weight = np.exp(-((normalized - 0.25) ** 2) / (2 * 0.25 ** 2))
        normalized = normalized + (basic.shadows / 100.0) * 0.28 * weight
    if basic.highlights:
        weight = np.exp(-((normalized - 0.75) ** 2) / (2 * 0.25 ** 2))
        normalized = normalized + (basic.highlights / 100.0) * 0.28 * weight
    if basic.blacks:
        weight = np.exp(-((normalized - 0.05) ** 2) / (2 * 0.15 ** 2))
        normalized = normalized + (basic.blacks / 100.0) * 0.20 * weight
    if basic.whites:
        weight = np.exp(-((normalized - 0.95) ** 2) / (2 * 0.15 ** 2))
        normalized = normalized + (basic.whites / 100.0) * 0.20 * weight

    # Brightness is a gamma - it leaves white and black alone and pushes
    # only the midtones up. That is a different character from exposure
    # (which multiplies everything by 2^EV). Raising exposure blows the
    # highlights first, whereas brightness does not make an already blown
    # area any brighter. This is the one to use when you only want to
    # rescue the face of a backlit subject.
    if basic.brightness:
        # +100 -> gamma 0.5 (brighter), -100 -> gamma 2.0 (darker)
        gamma = 2.0 ** (-basic.brightness / 100.0)
        normalized = np.power(np.clip(normalized, 0.0, 1.0), gamma)

    if basic.contrast:
        factor = 1.0 + basic.contrast / 100.0
        normalized = (normalized - 0.5) * factor + 0.5

    return np.clip(normalized * 255.0, 0, 255).astype(np.float32)


# The default camera profile (standard). A neutral demosaic is flat, so
# it looks duller than Lightroom's "Adobe Color" default. A gentle S-curve
# plus a little saturation makes a natural starting point.
#
# **This is where an attempt to fix the 0.60 slope of the last segment,
# taken for a gradation problem, was reverted.** Read the following
# before trying again.
#
# Measured in light, the top 1 stop is squeezed from 75.1 levels before
# the profile down to 46.7 levels (62%). That looks plausible, but
# **there are no pixels in that range.** Right after BT.709, the pixels
# landing on display values 190~255 come to 0.3% across 5 real photos
# (0~25 is 29.4%, 26~95 is 44.2%, 96~189 is 26.1%).
#
# Weighted by the pixel distribution the slope is 1.365 - overall this
# curve **spreads gradation out rather than pressing it down**. By
# contribution, 0~25 is 0.613, 26~95 is 0.494, 96~189 is 0.234, and
# 190~255 is 0.002.
#
# Actually lowering (190,216) to 202 dropped the 96~189 slope from 0.89
# to 0.75, and on real photos the unique highlight levels **fell** by
# 3~20 and the midtones by up to 24. It amounted to pressing down where
# the pixels are packed and handing that away to where there are none.
#
# The curve itself is needed. Removing it altogether makes the luma error
# against the camera JPEG worse on Sony, from 18.24 to 21.16.
_STANDARD_PROFILE_CURVE = ((0, 0), (26, 54), (96, 132), (190, 216), (255, 255))
_STANDARD_PROFILE_SATURATION = 12


def apply_camera_profile(image_bgr: np.ndarray) -> np.ndarray:
    """Apply the default camera profile (standard) to a neutral demosaic.

    This result becomes the starting point for the "no adjustments"
    state. It corresponds to Lightroom's default profile, and the user's
    adjustments and profile presets are laid on top of it.
    """
    lut = smooth_curve_lut(list(_STANDARD_PROFILE_CURVE))
    result = _apply_lut(image_bgr.astype(np.float32), lut)
    if _STANDARD_PROFILE_SATURATION:
        result = _apply_saturation_gain(result, 1.0 + _STANDARD_PROFILE_SATURATION / 100.0)
    return result.astype(np.float32)


def _apply_saturation_gain(image: np.ndarray, gain: float) -> np.ndarray:
    """Multiply the saturation of a float image (an HSV round trip is
    8-bit, so this works on the floats directly).

    Each pixel is pushed away from or pulled towards the grey axis while
    its luminance is preserved.
    """
    gray = image[:, :, 0] * 0.114 + image[:, :, 1] * 0.587 + image[:, :, 2] * 0.299
    gray = gray[:, :, None]
    return np.clip(gray + (image - gray) * gain, 0.0, 255.0).astype(np.float32)


# The standard regions of the parametric curve - the same quartile
# centres Lightroom uses. Shadows (0~25%) / darks (25~50%) /
# lights (50~75%) / highlights (75~100%).
PARAMETRIC_REGIONS = (
    ("shadows", 0.125),
    ("darks", 0.375),
    ("lights", 0.625),
    ("highlights", 0.875),
)
_PARAMETRIC_WIDTH = 0.15
_PARAMETRIC_STRENGTH = 0.22


def parametric_tone_lut(
    shadows: float, darks: float, lights: float, highlights: float
) -> np.ndarray:
    """The four parametric regions as a 256-entry LUT. Shared by the
    curve editor and the engine.

    Each region is pushed with a Gaussian weight centred on its quartile,
    so they join smoothly and the boundaries do not show up as bands.
    """
    normalized = _IDENTITY / 255.0
    amounts = {"shadows": shadows, "darks": darks, "lights": lights, "highlights": highlights}
    for name, center in PARAMETRIC_REGIONS:
        amount = amounts[name]
        if amount:
            weight = np.exp(-((normalized - center) ** 2) / (2 * _PARAMETRIC_WIDTH ** 2))
            normalized = normalized + (amount / 100.0) * _PARAMETRIC_STRENGTH * weight
    return np.clip(normalized * 255.0, 0, 255).astype(np.float32)


def curve_control_points(points) -> list[tuple[float, float]]:
    """Fill in the endpoints of the control points. If the user has put a
    point at x=0/255, that one is used.

    The old version unconditionally prepended (0,0) and appended
    (255,255). smooth_curve_lut keeps only the first value when x values
    collide, so an asymmetry appeared: at the left end the added (0,0)
    covered the user's point, while at the right end the user's point
    covered (255,255). Given an inverting curve ((0,255),(255,0)) that
    made 0->0 and 255->0, and the photo came out entirely black. The
    black/white points are values the user decides, so they are left as
    they are.
    """
    resolved = [(float(a), float(b)) for a, b in points]
    xs = {x for x, _ in resolved}
    if 0.0 not in xs:
        resolved.insert(0, (0.0, 0.0))
    if 255.0 not in xs:
        resolved.append((255.0, 255.0))
    return resolved


def _curve_lut(curve: CurveSettings) -> np.ndarray:
    """A LUT combining the parametric curve and the RGB point curve."""
    lut = _IDENTITY.copy()

    if curve.shadows or curve.darks or curve.lights or curve.highlights:
        lut = parametric_tone_lut(
            curve.shadows, curve.darks, curve.lights, curve.highlights
        )

    if curve.points_rgb:
        lut = np.interp(
            lut, _IDENTITY, _spline_lut(curve_control_points(curve.points_rgb))
        )

    return np.clip(lut, 0, 255).astype(np.float32)


def _apply_lut(image: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Apply a curve LUT to a 0~255 float image.

    The old version quantised to 8 bits and used cv2.LUT, but that
    crushes every intermediate stage down to 256 steps and puts banding
    into smooth gradations. Keeping the precision of 14-bit RAW means
    interpolating and applying the curve while still in float.
    """
    clipped = np.clip(image, 0.0, 255.0)
    return np.interp(clipped, _IDENTITY, np.clip(lut, 0.0, 255.0)).astype(np.float32)


# --------------------------------------------------------------- white balance


# Colour temperature is handled as absolute Kelvin. 0 signals "do not
# touch" (keep as-shot).
NEUTRAL_KELVIN = 5500


def _kelvin_to_rgb(kelvin: float) -> np.ndarray:
    """Approximate the black-body colour of a colour temperature (Kelvin)
    as 0~255 RGB (Tanner Helland)."""
    t = float(np.clip(kelvin, 1000.0, 40000.0)) / 100.0
    if t <= 66:
        r = 255.0
        g = 99.4708025861 * np.log(t) - 161.1195681661
    else:
        r = 329.698727446 * ((t - 60) ** -0.1332047592)
        g = 288.1221695283 * ((t - 60) ** -0.0755148492)
    if t >= 66:
        b = 255.0
    elif t <= 19:
        b = 0.0
    else:
        b = 138.5177312231 * np.log(t - 10) - 305.0447927307
    return np.clip(np.array([r, g, b], dtype=np.float64), 1e-3, 255.0)


def _wb_gain(target_kelvin: float, wb: "tuple | None",
             base_kelvin: float = 0) -> np.ndarray:
    """Work out the R/G/B gain that moves the base to the target colour
    temperature.

    The camera multipliers for the target colour temperature are obtained
    by shifting that camera's daylight coefficients by
    scale = rgb(5500)/rgb(target). The gain applied is
    (target multipliers / base multipliers). Without RAW information
    (wb), a generic approximation anchored at 5500K is used - that is for
    tests and non-RAW input.

    base_kelvin is **which colour temperature the base was already
    demosaiced at**. 0 means as-shot (camera_wb). White balance is
    physically an operation on sensor-linear values, so multiplying a
    gain onto encoded values is an approximation - the right answer is
    E(g*L) but this produces g*E(L), and they only agree when E is
    linear. The gap widens the further you are from as-shot (measured:
    0.95 levels at 8 mired, 9.2 levels at 183 mired with 64% of pixels
    off by more than 5 levels).

    That is why the screen uses a base re-demosaiced at the target colour
    temperature. At that point base_kelvin equals the target, this gain
    becomes exactly 1, and the approximation disappears. While the slider
    is being dragged, this gain carries the preview on the old base.
    """
    if wb is not None:
        camera_wb, daylight_wb = wb
        camera = np.array(camera_wb[:3], dtype=np.float64)
        daylight = np.array(daylight_wb[:3], dtype=np.float64)
        if camera[1] > 0 and daylight[1] > 0:
            # **Anchor on the camera's measured multipliers** - it has
            # to use the same reference as raw_io.load_demosaiced's
            # target_kelvin path, or the colour jumps the moment the
            # slider is released (gain approximation -> re-demosaic).
            #
            # The multipliers have the form camera x K(estimate)/K(value),
            # so in the gain (target/base) camera cancels out and only
            # the pure model ratio remains. The tint component, which the
            # Kelvin model does not have, never rides along in the gain,
            # so the base's tint is preserved, and if the target is the
            # as-shot estimate the gain is exactly 1 - previously the
            # residual between the model's absolute values and the
            # measurement (measured R 8.2%) was applied as-is, so merely
            # committing the slider at its as-shot display position gave
            # a green-yellow cast.
            from ..raw_io import _estimate_as_shot_kelvin

            est = _estimate_as_shot_kelvin(tuple(camera), tuple(daylight))
            base_ref = (float(base_kelvin)
                        if base_kelvin and base_kelvin > 0 else float(est))
            gain = _kelvin_to_rgb(base_ref) / _kelvin_to_rgb(target_kelvin)
        else:
            # File whose multipliers cannot be read - fall back to the
            # old mixed approximation
            target_mult = daylight * (_kelvin_to_rgb(NEUTRAL_KELVIN)
                                      / _kelvin_to_rgb(target_kelvin))
            if base_kelvin and base_kelvin > 0:
                base_mult = daylight * (_kelvin_to_rgb(NEUTRAL_KELVIN)
                                        / _kelvin_to_rgb(base_kelvin))
            else:
                base_mult = np.maximum(camera, 1e-6)
            gain = target_mult / np.maximum(base_mult, 1e-6)
    else:
        gain = _kelvin_to_rgb(NEUTRAL_KELVIN) / _kelvin_to_rgb(target_kelvin)
        if base_kelvin and base_kelvin > 0:
            gain = gain * (_kelvin_to_rgb(base_kelvin)
                           / _kelvin_to_rgb(NEUTRAL_KELVIN))
    return gain / gain[1]  # normalise to G=1 to keep the brightness


def _apply_white_balance(
    image: np.ndarray, basic: BasicSettings, wb: "tuple | None" = None,
    base_kelvin: float = 0
) -> np.ndarray:
    """Apply colour temperature (absolute Kelvin) plus tint as channel
    gains.

    A temperature of 0 or below is read as "do not touch" and as-shot is
    kept.

    If base_kelvin equals the target, the colour temperature gain is 1
    and nothing happens - it means the base was already demosaiced at
    that colour temperature, and that is the accurate path (see
    _wb_gain).

    **Tint is not covered by that.** Re-demosaicing only hands LibRaw the
    multipliers derived from Kelvin (raw_io.load_demosaiced's
    target_kelvin), so even after the colour temperature is matched, tint
    remains a G gain on top of display values. It does not move as much
    as colour temperature does, so it was left as it is.
    """
    if basic.temperature <= 0 and not basic.tint:
        return image

    result = image.copy()
    if basic.temperature > 0:
        gain = _wb_gain(basic.temperature, wb, base_kelvin)
        result[:, :, 2] *= float(gain[0])  # R
        result[:, :, 1] *= float(gain[1])  # G
        result[:, :, 0] *= float(gain[2])  # B
    if basic.tint:
        result[:, :, 1] *= 1.0 - basic.tint / 100.0 * 0.18  # G
    return result


# -------------------------------------------------------------- local contrast


#: The reference resolution (long edge) the pixel-unit radius
#: adjustments were tuned at.
#:
#: The place where the user tunes the sliders is the preview in the
#: develop window, and that is 1400px (PREVIEW_LONG_EDGE in
#: gui/loupe.py). Fixing a radius in pixels means the same value lands
#: relatively smaller on anything larger - Full Render (viewport size)
#: and export (original size) come out less sharp than the screen.
#:
#: Measured (A6700, developed at 1400px against 2800px and compared at
#: the same size):
#:
#:     no adjustment   0.15 levels   resolution-independent
#:     clarity +50     0.18          independent (already size-scaled)
#:     texture +50     1.13          sensitive (saturation +2.15)
#:     sharpness +80   1.51          sensitive (saturation +3.39)
#:     grain +50       6.27          very sensitive (48% of pixels)
#:
#: So that what you see is what you get, **we match the screen side.**
#: That means exports of existing edits come out sharper than before.
TUNED_LONG_EDGE = 1400


def scale_for(image: np.ndarray) -> float:
    """The factor to multiply a pixel-unit radius by for this image."""
    return max(image.shape[:2]) / float(TUNED_LONG_EDGE)


def _local_contrast(image: np.ndarray, amount: int, radius: float) -> np.ndarray:
    """Unsharp mask. Shared by clarity (large radius) and texture (small
    radius)."""
    if not amount:
        return image
    blurred = cv2.GaussianBlur(image, (0, 0), radius)
    return image + (image - blurred) * (amount / 100.0)


def _apply_dehaze(image: np.ndarray, amount: int) -> np.ndarray:
    """Dehaze approximation - pulls the dark end down and raises the
    saturation.

    Doing it properly would mean estimating a transmission map with a
    dark channel prior, but for preview-grade adjustment a global
    approximation is enough.
    """
    if not amount:
        return image

    strength = amount / 100.0
    normalized = np.clip(image / 255.0, 0.0, 1.0)
    # Move the black point up or down to create contrast
    black_point = 0.12 * strength
    normalized = np.clip((normalized - black_point) / max(1e-3, 1.0 - black_point), 0.0, 1.0)

    luma = normalized.mean(axis=2, keepdims=True)
    normalized = luma + (normalized - luma) * (1.0 + 0.4 * strength)
    return np.clip(normalized, 0.0, 1.0) * 255.0


# ---------------------------------------------------------------- HSL


#: Scale ratio between uint8 HSV (H 0~179) and float32 HSV (H 0~360).
#:
#: HSL_BAND_CENTERS and the fringe hues the eyedropper produces are
#: values shared with the screen (the hue gradient), so they are **left
#: on the 8-bit scale.** Only the computation is lifted to degrees here -
#: changing the constant would throw the screen colours and the settings
#: file off together.
HUE_UINT8_TO_DEGREES = 2.0


def _band_weight(hue: np.ndarray, center_degrees: float,
                 width_degrees: float = 44.0) -> np.ndarray:
    """Weight by circular hue distance. Handles red straddling 0/360.

    The scale is float32 HSV degrees (0~360). The default width of 44 is
    the same span as 22 on the old 8-bit scale.
    """
    distance = np.abs(hue - center_degrees)
    distance = np.minimum(distance, 360.0 - distance)
    return np.exp(-(distance ** 2) / (2 * width_degrees * width_degrees))


def _apply_hsl(image: np.ndarray, hsl: HSLSettings) -> np.ndarray:
    """Hue/saturation/luminance adjustment per each of the 8 hue bands.

    **The computation runs in float32 HSV.** The old version round-tripped
    through uint8, which dropped the gradation to 8 bits in this stage
    alone (measured: 5.44 million unique levels -> 256 after the pass).
    When exporting 16-bit it becomes a silent trap where "touching HSL
    kills the gradation".

    float32 has a different range convention from uint8 (confirmed by
    measurement): **H 0~360, S 0~1, and V keeps the input range** (0~255
    for us). Round-trip error 1.5e-04. Getting this convention wrong
    drives saturation to 0 across the board and the photo turns black and
    white - an accident we actually had in optics' fringe removal.
    """
    if hsl.is_neutral():
        return image

    hsv = cv2.cvtColor(
        np.clip(image, 0, 255).astype(np.float32), cv2.COLOR_BGR2HSV)
    hue, saturation, value = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    hue_shift = np.zeros_like(hue)
    saturation_scale = np.ones_like(saturation)
    value_scale = np.ones_like(value)

    for name, band in hsl.bands.items():
        if band.is_neutral():
            continue
        weight = _band_weight(
            hue, HSL_BAND_CENTERS[name] * HUE_UINT8_TO_DEGREES)
        if band.hue:
            hue_shift += weight * (band.hue / 100.0 * 15.0 * HUE_UINT8_TO_DEGREES)
        if band.saturation:
            saturation_scale += weight * (band.saturation / 100.0)
        if band.luminance:
            value_scale += weight * (band.luminance / 100.0 * 0.5)

    hsv[:, :, 0] = np.mod(hue + hue_shift, 360.0)
    hsv[:, :, 1] = np.clip(saturation * saturation_scale, 0.0, 1.0)
    hsv[:, :, 2] = np.clip(value * value_scale, 0.0, 255.0)

    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


# -------------------------------------------------------------- colour grading


def _zone_color(hue_degrees: int, saturation: int) -> np.ndarray:
    """Colour wheel value as a BGR direction vector."""
    hsv = np.uint8([[[int(hue_degrees / 2) % 180, 255, 255]]])
    bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0].astype(np.float32) / 255.0
    return (bgr - bgr.mean()) * (saturation / 100.0)


def _apply_color_grade(image: np.ndarray, grade: ColorGradeSettings) -> np.ndarray:
    """Lay a different colour over the shadow / midtone / highlight
    regions."""
    if grade.is_neutral():
        return image

    normalized = np.clip(image / 255.0, 0.0, 1.0)
    luma = normalized.mean(axis=2, keepdims=True)

    # balance shifts the region boundaries, deciding which side gets the
    # wider span
    balance = grade.balance / 100.0 * 0.25
    shadow_mask = np.clip(1.0 - (luma - balance) * 2.5, 0.0, 1.0)
    highlight_mask = np.clip((luma - balance - 0.6) * 2.5, 0.0, 1.0)
    midtone_mask = np.clip(1.0 - shadow_mask - highlight_mask, 0.0, 1.0)

    strength = grade.blending / 100.0 * 0.5
    for zone, mask in (
        (grade.shadows, shadow_mask),
        (grade.midtones, midtone_mask),
        (grade.highlights, highlight_mask),
    ):
        if zone.is_neutral():
            continue
        if zone.saturation:
            normalized = normalized + _zone_color(zone.hue, zone.saturation) * mask * strength
        if zone.luminance:
            normalized = normalized + (zone.luminance / 100.0) * 0.3 * mask

    return np.clip(normalized, 0.0, 1.0) * 255.0


# ---------------------------------------------------------------- saturation


def _apply_saturation(image: np.ndarray, basic: BasicSettings) -> np.ndarray:
    """Saturation and vibrance.

    Vibrance touches already-saturated colours less - that is to stop
    skin tones burning in portraits.
    """
    if not basic.saturation and not basic.vibrance:
        return image

    luma = image.mean(axis=2, keepdims=True)
    delta = image - luma

    factor = 1.0 + basic.saturation / 100.0
    if basic.vibrance:
        current = np.abs(delta).max(axis=2, keepdims=True) / 255.0
        factor = factor + (basic.vibrance / 100.0) * (1.0 - np.clip(current * 2.0, 0.0, 1.0))

    return luma + delta * factor


# ---------------------------------------------------------------- detail


# 3x3 mask for estimating the noise σ (Immerkær). The standard deviation
# of the response to i.i.d. noise of σ is exactly 6σ, so dividing the
# response by 6 gives σ.
_NOISE_MASK = np.array(
    [[1.0, -2.0, 1.0], [-2.0, 4.0, -2.0], [1.0, -2.0, 1.0]], dtype=np.float32
)

_MIN_NOISE_SIGMA = 0.4
"""Lower bound on the estimated σ.

Synthetic images and already-smoothed downscales come out with σ close
to 0. Used as-is the filter strength becomes 0, so raising the slider
does nothing at all.
"""


def _high_frequency_sigma(luma: np.ndarray) -> float:
    """White (high-frequency) noise σ - MAD over the Immerkær mask.

    Using the median absolute deviation rather than the mean keeps it
    from being dragged around by the subject's edges and texture. It
    samples only every 7th pixel, so even 32MP takes 0.05 s.
    """
    response = cv2.filter2D(luma.astype(np.float32), cv2.CV_32F, _NOISE_MASK)
    sample = np.abs(response[::7, ::7])
    if sample.size == 0:
        return _MIN_NOISE_SIGMA
    return max(_MIN_NOISE_SIGMA, float(1.4826 * np.median(sample) / 6.0))


def _total_sigma_floor(luma: np.ndarray, tile: int = 96) -> float:
    """Approximation of the total noise σ including the low-frequency
    components - the bottom 20% of the tile residual MADs.

    Flat tiles settle at the bottom of the distribution, so a low
    percentile approximates the noise floor. **It must not be used on its
    own** - on photos that are all texture with no flat area it
    overestimates by up to 2x (measured). It is only used to work out the
    correction ratio below.
    """
    h, w = luma.shape[:2]
    if h < tile or w < tile:
        return 0.0
    mads = []
    ys = np.linspace(0, h - tile, min(10, max(2, h // tile))).astype(int)
    xs = np.linspace(0, w - tile, min(10, max(2, w // tile))).astype(int)
    for y in ys:
        for x in xs:
            patch = luma[y:y + tile, x:x + tile].astype(np.float32)
            mean = float(patch.mean())
            if not 12 <= mean <= 243:   # a clipped tile shows a pressed σ
                continue
            residual = patch - cv2.GaussianBlur(patch, (0, 0), 3.0)
            mads.append(float(1.4826 * np.median(
                np.abs(residual - np.median(residual)))))
    if not mads:
        return 0.0
    return float(np.percentile(mads, 20.0))


def estimate_noise_sigma(luma: np.ndarray) -> float:
    """Estimate the noise standard deviation of the flat areas.

    For the same slider value to be "about right" at ISO 100 and at
    ISO 6400 alike, the filter strength has to be proportional to the
    actual noise of that photo. Left at a fixed strength it smears too
    much at low ISO and cannot touch anything at high ISO.

    A high-frequency estimate (assuming white noise) is not enough.
    Demosaic noise is spatially correlated because of the interpolation,
    so looking only at high frequencies misses the total σ - measured at
    1/1.53~1/1.22 of the true value (worse the larger the noise). That is
    why the same slider grew relatively weaker the higher the ISO ("the
    noise reduction is far too weak").

    The total σ floor is measured separately from the tile residuals, and
    only that **ratio** is used as a correction (clamped to 1.0~1.4 - the
    total σ estimate overestimates on photos that are all texture, so it
    must not be trusted as-is). Measured error: -22~-35% before the
    correction -> ±14% after.

        file               true   before   after
        A6700 ISO3200      4.92    3.21     4.49
        A6700 ISO3200 b    3.81    2.97     4.16
        R6M3 ISO6400       3.53    2.72     3.88
        R6M2 ISO800        1.26    0.99     1.39
        S1R                5.75    4.69     6.57

    With white noise (no correlation) the ratio clamps to 1.0 and the
    behaviour is the same as before.
    """
    hf = _high_frequency_sigma(luma)
    total = _total_sigma_floor(luma)
    if total <= 0.0:
        return hf
    correction = min(max(total / hf, 1.0), 1.4)
    return hf * correction


SHADOW_CHROMA_LUMA = 70.0
"""Below this luminance counts as a "dark area" (a linear ramp from 0
to 1).

Colour noise is especially bad in dark areas because of shadow
amplification, but tuning a uniform blur for that also smears the real
colour edges in the bright areas. The ramp is gentle enough that no
boundary line shows.
"""


SHADOW_RAMP = 0.2
"""How the shadow-only treatment is weighted in across the slider.

The midpoint has to land on what used to be the top of the slider, so the
extra comes in early and then keeps deepening. Averaged over the real
shadows of five high-ISO files, colour noise remaining:

    weight power    25    50   100
        0.2        25%   15%    9%
        1.0        41%   22%    8%
        3.0        67%   39%    8%

At 0.2 the midpoint sits on 15%, which is where the old maximum was
(14%), and the travel above it goes to 9%.
"""


SHADOW_PASS_SCALE = 4
"""How far the repeated shadow passes are downscaled before running.

They only ever touch blotches tens of pixels wide, so the resolution is
not doing anything for them. Full resolution costs 13.2s on 24MP against
a 3.2s baseline; a quarter brings it back to 3.6s for the same result.
"""


SHADOW_PASSES = 4.0
"""How many times the shadow component is filtered at the top of the slider.

Widening the window alone plateaus - past SHADOW_HEAVY_RADIUS the noise
stops falling while colour edges keep going. What keeps removing noise is
running the whole narrow-plus-wide step again, and that is what carries
the slider past what used to be its maximum: 15% at the midpoint, 9% at
the top.

Only the shadow component is repeated. The bright areas are blended back
from the single pass, so they come out unchanged however high this goes -
measured 97% of the original colour edge at the midpoint against 94% for
the old maximum.
"""


SHADOW_HEAVY_RADIUS_TOP = 8
"""The shadow window at the top of the slider (from SHADOW_HEAVY_RADIUS).

Widening is not what buys the extra - the passes are - so this moves only
a little. Going further trades dark colour for nothing: measured at the
top of the slider, x8 leaves 9% of the noise and keeps 65% of a dark
colour edge, while x16 leaves the same 9% and keeps 39%.
"""


SHADOW_HEAVY_RADIUS = 6
"""How much wider the shadow-only window is at the bottom of the slider.

The dark areas are where the colour blotches are largest, and the window
has to be wide enough to span one. At the old x3 it could not reach the
coarse end of them, and widening costs nothing - the guided filter is
O(1) in the radius. What it does cost is colour edges that the luminance
guide cannot see, so this stops at the knee. Measured on a synthetic dark
patch that is almost iso-luminant with its surroundings, which is the
worst case for a luma-guided filter (residual shadow colour noise /
colour edge kept):

    x3  3.29 / 24.74      x8  2.76 / 21.65
    x4  3.03 / 24.35      x10 2.74 / 20.30
    x6  2.83 / 23.08      x14 2.73 / 18.16

Past x6 the noise stops falling but the colour edge keeps going.
"""


COARSE_CHROMA_RADIUS = 4
"""Window radius of the coarse blotch pass, at 1/4 of the already-downscaled
chroma. r=8 removes a little more blotching (0.67 vs 0.84) but starts to
bleed (edge error +0.8); r=4 costs the edges nothing (+0.09)."""

GUIDED_EPS = 1e-3
"""Base edge sensitivity of the guided chroma filter, on a 0~1 luminance
scale. This is the value at the *bottom* of the slider.

Where the luminance varies more than this within the window, the filter
stops averaging across it. Smaller keeps more colour edges and removes less
blotching; larger behaves more like a plain blur, which is why
_reduce_color_noise scales it up with the amount."""


def _guided_by_luma(plane: np.ndarray, guide: np.ndarray,
                    radius: int, eps: float = GUIDED_EPS) -> np.ndarray:
    """Smooth a chroma plane while following the luminance edges.

    A guided filter (He et al.): five box filters, so the cost is O(1) in
    the radius rather than growing with it. It fits a local linear model
    plane ~= a*guide + b, which means it averages hard inside a region and
    stops at a luminance edge.

    Why not the plain Gaussian this replaces: colour noise is blotches tens
    of pixels wide, so it needs a wide kernel, and a wide *unguided* kernel
    drags colour across boundaries. Measured on a synthetic scene with hard
    colour edges (residual colour noise in flat areas / colour error at the
    edges, lower is better in both):

        noisy input             27.75 / 32.35
        Gaussian sigma 8        22.94 / 37.32   <- edges worse than the input
        guided radius 16        21.90 / 32.46   <- flat better, edges intact

    The Gaussian pushes the edge error *above* the noisy input because it
    bleeds one region's colour into the next. The guided filter removes more
    noise than that and leaves the edges where they were.
    """
    window = (radius * 2 + 1, radius * 2 + 1)
    mean_guide = cv2.blur(guide, window)
    mean_plane = cv2.blur(plane, window)
    var_guide = cv2.blur(guide * guide, window) - mean_guide * mean_guide
    cov = cv2.blur(guide * plane, window) - mean_guide * mean_plane
    scale_a = cov / (var_guide + eps)
    offset_b = mean_plane - scale_a * mean_guide
    return cv2.blur(scale_a, window) * guide + cv2.blur(offset_b, window)


def _reduce_color_noise(ycc: np.ndarray, amount: int, radius: int,
                        shadow: int = 0) -> None:
    """Remove colour noise (modified in place).

    Colour noise is not pixel-level grain but blotches spanning tens of
    pixels. Measured (R6M3 ISO6400): shrinking to 1/4 still leaves 54% of
    the original colour noise (luminance leaves 32%). A small kernel such
    as a 3x3 median therefore cannot touch it at all.

    Downscaling, removing, then upscaling again catches the large
    blotches at 1/16 of the cost. The luminance channel is never touched,
    so *luminance* detail loss is zero in principle (measured: edge gradient
    97.720 -> 97.724, within measurement error). Colour edges are a
    different matter, which is why the smoothing is guided by the luminance
    rather than a plain blur - see _guided_by_luma.

    Given shadow (0~100), the dark areas (below SHADOW_CHROMA_LUMA) are
    filtered as if the slider were at the top: a SHADOW_HEAVY_RADIUS-times
    wider window, the relaxed edge sensitivity of full strength, and the
    coarse correction at full gain. The bright areas keep whatever the
    slider says. The midpoint of the slider now lands on what used to be
    its maximum, and the travel above that keeps going by repeating the
    shadow pass - see SHADOW_PASSES. Measured on the real shadows of five
    high-ISO files (ISO 8000/5000/4000/3200 and an A1 at 1600), colour
    noise remaining in the shadow itself:

        slider      25    50    75   100
        shadow 0    68%   46%   38%   25%
        shadow 100  25%   15%   12%    9%

    The bright areas are untouched throughout - that is the whole point of
    the gate. What the top of the slider spends instead is colour in the
    dark: on a synthetic check a dark colour edge goes 85% at the midpoint
    to 65% at the top, while the bright one holds at 97 -> 94%. Pushing
    the entire frame this hard instead bleeds colour edges everywhere
    (64.95 -> 35.58), which is what the gate exists to avoid. At 0 the
    behaviour is as it was.
    """
    height, width = ycc.shape[:2]
    strength = amount / 100.0
    push = strength
    # Blur radius in terms of the original resolution. The amount and the
    # radius are multiplied into a single continuous value. The old code
    # picked the kernel size (3 or 5) directly, which made the slider
    # effectively a two-position switch - 1~53 all gave the same result.
    blur = (0.5 + 2.5 * strength) * (0.4 + 1.6 * (radius / 100.0))
    # Large radii are far cheaper to handle downscaled. Even downscaled
    # the radius itself is set by blur, so the slider still moves
    # continuously.
    scale = max(1, min(4, int(blur / 2.0)))
    small_sigma = max(0.3, blur / scale)
    small_size = (max(1, width // scale), max(1, height // scale))

    shadow_weight = None
    if shadow > 0:
        # The weight is built in the downscaled space - the channel blend
        # happens there too, so it is effectively free. The luminance is
        # blurred slightly to soften the boundary.
        luma_small = cv2.resize(ycc[:, :, 0], small_size,
                                interpolation=cv2.INTER_AREA)
        luma_small = cv2.GaussianBlur(luma_small, (0, 0), 4.0)
        # Scaled by the slider as well, or the extra would arrive all at
        # once at the bottom of it - see SHADOW_RAMP.
        shadow_weight = np.clip(
            (SHADOW_CHROMA_LUMA - luma_small) / SHADOW_CHROMA_LUMA, 0.0, 1.0
        ) * (shadow / 100.0) * (strength ** SHADOW_RAMP)

    # The guide for the filter below. It is the luminance at the same
    # (downscaled) size, normalised to 0~1 so that GUIDED_EPS means the same
    # thing whatever the image.
    guide = cv2.resize(ycc[:, :, 0], small_size,
                       interpolation=cv2.INTER_AREA) / 255.0         if scale > 1 else ycc[:, :, 0] / 255.0
    radius = max(1, int(round(small_sigma * 1.2)))
    # A second, much coarser pass for the blotches. Colour noise that
    # survives the pass above is mottling tens of pixels wide (A1 ISO 1600
    # lifted +2EV, measured in the 4~32px chroma band: the fine pass leaves
    # 61% of it). The window here cannot reach that without bleeding, so the
    # correction is computed two pyramid levels further down and added back,
    # scaled by the slider. The eps stays at the strict base value - at that
    # scale the blotches are high-frequency but real colour edges are still
    # edges, and relaxing it there is what bled colour in the prototype
    # (edge error 33.70 -> 36.35; strict keeps it at 33.79). Measured whole
    # slider, blotch band remaining vs no NR: 25 -> 70%, 50 -> 52%,
    # 100 -> 33% (the fine pass alone floors at 61%).
    run_coarse = min(small_size) >= 64
    coarse_gain = strength
    coarse_guide = None
    # Edge sensitivity is relaxed as the slider goes up, so the top of the
    # slider can still flatten everything the way the old plain blur did.
    # Without this the guided filter floors at ~15% residual - it refuses to
    # smooth across luminance structure - and the strongest setting would
    # come out weaker than before. At 100 the two now land on the same
    # figure (9% residual, measured on R6M3 ISO 6400).
    eps = GUIDED_EPS * (1.0 + strength ** 2 * 10.0)
    # The shadow branch runs at full strength whatever the slider says.
    # Clamping to the top of the slider rather than multiplying keeps it a
    # no-op once the slider is already there.
    shadow_eps = max(eps, GUIDED_EPS * 11.0)
    shadow_gain = coarse_gain
    if shadow_weight is not None:
        shadow_gain = coarse_gain * (1.0 - shadow_weight) + shadow_weight

    # The window and the pass count both open up with the slider. Only the
    # shadow component is iterated - see the blend below.
    wide = max(1, int(round(radius * (
        SHADOW_HEAVY_RADIUS
        + (SHADOW_HEAVY_RADIUS_TOP - SHADOW_HEAVY_RADIUS) * push))))
    passes = 1.0 + (SHADOW_PASSES - 1.0) * push
    whole = int(passes)
    part = passes - whole

    def one_pass(plane: np.ndarray) -> np.ndarray:
        """The narrow filter, with the wide one mixed into the dark areas."""
        out = _guided_by_luma(plane, guide, radius, eps)
        if shadow_weight is not None:
            heavy = _guided_by_luma(plane, guide, wide, shadow_eps)
            out = out * (1.0 - shadow_weight) + heavy * shadow_weight
        return out

    # The repeats run downscaled. What they remove is blotching tens of
    # pixels across and the window is already SHADOW_HEAVY_RADIUS_TOP wide,
    # so there is nothing at full resolution for them to act on - measured
    # identical to the digit, at a quarter of the cost. Without this the
    # top of the slider costs 13.2s on 24MP against a 3.2s baseline.
    rep_size = (max(16, small_size[0] // SHADOW_PASS_SCALE),
                max(16, small_size[1] // SHADOW_PASS_SCALE))
    rep_ready = shadow_weight is not None and passes > 1.0
    if rep_ready:
        rep_guide = cv2.resize(guide, rep_size, interpolation=cv2.INTER_AREA)
        rep_weight = cv2.resize(shadow_weight, rep_size,
                                interpolation=cv2.INTER_AREA)
        rep_radius = max(1, radius // SHADOW_PASS_SCALE)
        rep_wide = max(1, wide // SHADOW_PASS_SCALE)

    def repeat_pass(plane: np.ndarray) -> np.ndarray:
        out = _guided_by_luma(plane, rep_guide, rep_radius, eps)
        heavy = _guided_by_luma(plane, rep_guide, rep_wide, shadow_eps)
        return out * (1.0 - rep_weight) + heavy * rep_weight

    for channel in (1, 2):
        plane = ycc[:, :, channel]
        if scale > 1:
            small = cv2.resize(plane, small_size, interpolation=cv2.INTER_AREA)
        else:
            small = plane
        single = one_pass(small)
        blurred = single
        if rep_ready:
            repeated = cv2.resize(single, rep_size, interpolation=cv2.INTER_AREA)
            for _ in range(whole - 1):
                repeated = repeat_pass(repeated)
            if part > 0.0:
                # Fractional pass, so the slider does not step.
                repeated = (repeated * (1.0 - part)
                            + repeat_pass(repeated) * part)
            repeated = cv2.resize(repeated, (single.shape[1], single.shape[0]),
                                  interpolation=cv2.INTER_LINEAR)
            # The bright areas take the single pass back, unchanged.
            blurred = single * (1.0 - shadow_weight) + repeated * shadow_weight
        if run_coarse:
            if coarse_guide is None:
                coarse_guide = cv2.pyrDown(cv2.pyrDown(guide))
            coarse = cv2.pyrDown(cv2.pyrDown(blurred))
            corrected = _guided_by_luma(coarse, coarse_guide,
                                        COARSE_CHROMA_RADIUS, GUIDED_EPS)
            blurred = blurred + cv2.resize(
                corrected - coarse, (blurred.shape[1], blurred.shape[0]),
                interpolation=cv2.INTER_LINEAR) * shadow_gain
        if scale > 1:
            blurred = cv2.resize(blurred, (width, height),
                                 interpolation=cv2.INTER_LINEAR)
        ycc[:, :, channel] = blurred


_PASS_H_FACTOR = {1: 1.0, 2: 0.556, 3: 0.465, 4: 0.432}
"""h factor by pass count. Makes the same slider produce a similar
amount of reduction.

Running several passes removes just as much even with a smaller h per
pass. Measured (A6700 ISO3200 and R6M3 ISO6400, geometric mean of the h
needed relative to 1 pass at the point where the flat areas drop 70%):
2 passes 0.556, 3 passes 0.465, 4 passes 0.432. It spreads by about
±0.15 depending on the file - but the h/σ curve itself is a measured
calibration on a single camera model, so that is inside the same error
range. In the weak-reduction range multi-pass leans towards removing a
little more at the same slider position, which is harmless there
because detail preservation is 100% anyway.
"""


def _denoise_luma_plane(luma: np.ndarray, algorithm, strength: float, sigma: float,
                        passes: int = 1) -> np.ndarray:
    """Denoise one luminance channel with the chosen algorithm. Input and
    output are both float 0~255.

    OpenCV's non-local means only accepts 8 bits. But the value at this
    point is a float that still carries the fractional part coming from
    14-bit RAW, so converting to 8 bits and handing that back would crush
    the gradation by one step. Only the **delta** is taken from the 8-bit
    result and added to the float original, which keeps the precision.

    passes (1~4) only applies to the non-local means family. For the same
    amount of reduction, several weak passes hurt detail far less - see
    the measurement table on DetailSettings.noise_passes. The bilateral
    filter gains nothing from repetition, so it ignores this.
    """
    as_uint8 = np.clip(luma, 0.0, 255.0).astype(np.uint8)

    if algorithm is NoiseAlgorithm.BILATERAL:
        # Passing a diameter of 0 makes OpenCV derive it from sigmaSpace.
        # The old code pinned the diameter at 5 and only raised sigma,
        # but with a small diameter no amount of sigma gets past a 5x5
        # average - which is why everything from slider 60 up was
        # effectively the same as 100 (measured difference 0.05).
        space_sigma = 1.0 + 2.5 * strength
        color_sigma = float(sigma * (1.0 + 4.0 * strength))
        denoised = cv2.bilateralFilter(as_uint8, 0, color_sigma, space_sigma)
        return luma + (denoised.astype(np.float32) - as_uint8.astype(np.float32))

    # h means "a difference this large counts as noise", so it has to be
    # proportional to the photo's actual σ. Measured (R6M3 ISO6400) h/σ
    # against noise reduction and detail preservation: at 0.8σ 11%/100%,
    # at 1.2σ 57%/99%, at 1.6σ 74%/91%, at 1.8σ 77%/84%, and from 2.0σ
    # the detail collapses (75%).
    # It is spread over 0.7σ~1.8σ so the whole slider range is useful.
    # (With the correlation correction added to σ̂, the actual h became
    #  1.0~1.4x larger than before - that fixed the slider growing weak
    #  at high ISO.)
    passes = max(1, min(4, int(passes)))
    h = float(sigma * (0.7 + 1.1 * strength)) * _PASS_H_FACTOR[passes]
    template, search = (
        (7, 21) if algorithm is NoiseAlgorithm.NLMEANS_HQ else (5, 11)
    )
    out = luma
    for _ in range(passes):
        as_uint8 = np.clip(out, 0.0, 255.0).astype(np.uint8)
        denoised = cv2.fastNlMeansDenoising(as_uint8, None, h, template, search)
        out = out + (denoised.astype(np.float32) - as_uint8.astype(np.float32))
    return out


def _protect_detail(original: np.ndarray, denoised: np.ndarray,
                    amount: int, sigma: float) -> np.ndarray:
    """Bring the original back where there is fine detail.

    Noise reduction is good for flat sky and skin, but in places with
    fine detail such as hair and leaves it erases the detail itself. The
    key is measuring the local contrast on the **denoised result** -
    measured on the original, the places with heavy noise get mistaken
    for places with detail and the noise gets brought back.
    """
    # The weight map is gentle anyway, so it is computed at half size. At
    # 32MP, running the box filter at the original resolution costs 0.4 s
    # on its own. Shrinking further to a quarter buries fine detail such
    # as hair in the average and drops it out of the protected set.
    height, width = denoised.shape[:2]
    small = cv2.resize(denoised, (max(1, width // 2), max(1, height // 2)),
                       interpolation=cv2.INTER_AREA)
    kernel = (5, 5)
    mean = cv2.boxFilter(small, cv2.CV_32F, kernel)
    mean_square = cv2.boxFilter(small * small, cv2.CV_32F, kernel)
    local = np.sqrt(np.maximum(mean_square - mean * mean, 0.0))
    # Local contrast above 0.5x the noise σ counts as "detail", and at 2x
    # the original is fully restored. The values were chosen by
    # measurement (R6M3 ISO6400, bilateral NR=75) - across this range the
    # edges came back from 52.95 to 71.27 while the flat-area noise
    # stayed put at 1.217 -> 1.222. Lowering the threshold further (0.3σ)
    # takes the edges up to 79, but the flat-area noise comes back to
    # 1.36 and the noise reduction becomes pointless.
    weight = np.clip((local - sigma * 0.5) / max(sigma * 1.5, 1e-3), 0.0, 1.0)
    weight *= amount / 100.0
    weight = cv2.resize(weight, (width, height), interpolation=cv2.INTER_LINEAR)
    return denoised + (original - denoised) * weight


FACE_NR_MARGIN = 0.35
"""The face box is widened by this much to form the noise reduction
target.

The neck, ears and the forehead boundary have to come inside. Fitting
the box exactly gives a cut-and-pasted look, a smooth face on a rough
neck.
"""

_FACE_WEIGHT_LONG_EDGE = 512
"""The resolution the weight map is built at. It is a gentle map anyway,
so there is no reason to build it at the original size."""

FACE_PRIORITY_MAX_CUT = 0.5
"""The largest fraction that can be taken away outside the face, even
with face priority raised all the way to 100.

100 used to mean "0 outside the face". But the noisiest place is not the
face, it is the dark background - measured (A6700 ISO3200, DSC02434)
σ 1.52 inside the face against σ 3.05 outside. Leaving the noisy side
entirely untouched meant the screen did not change even with noise
reduction raised to 100.

With this cap, at face priority 100 the area outside the face still gets
half strength. The reduction rate inside the face and the detail
preservation are unchanged (neither moved in the measurements).
"""


def _face_weight_map(
    height: int, width: int, faces: np.ndarray | None, priority: int
) -> np.ndarray | None:
    """Weight map that is 1.0 inside the face and (1 - priority) outside.
    None if there is no face.

    On None the caller applies the same strength to the whole frame.
    Switching noise reduction off just because no face was found would
    make the feature disappear on landscape photos.
    """
    if faces is None or len(faces) == 0 or priority <= 0:
        return None

    floor = 1.0 - FACE_PRIORITY_MAX_CUT * (min(100, max(0, priority)) / 100.0)
    scale = min(1.0, _FACE_WEIGHT_LONG_EDGE / max(height, width))
    sh = max(8, int(round(height * scale)))
    sw = max(8, int(round(width * scale)))
    sx, sy = sw / width, sh / height

    small = np.full((sh, sw), floor, np.float32)
    for face in faces:
        x, y, fw, fh = (float(v) for v in face[:4])
        cx = (x + fw / 2.0) * sx
        cy = (y + fh / 2.0) * sy
        ax = max(2, int(fw * (0.5 + FACE_NR_MARGIN) * sx))
        ay = max(2, int(fh * (0.5 + FACE_NR_MARGIN) * sy))
        cv2.ellipse(small, (int(cx), int(cy)), (ax, ay), 0, 0, 360, 1.0, -1)

    # Soften the boundary. A hard cut puts a line around the face where
    # the noise grain changes abruptly, which stands out more than the
    # noise itself.
    sigma = max(1.5, min(sh, sw) * 0.03)
    small = cv2.GaussianBlur(small, (0, 0), sigma)
    np.clip(small, floor, 1.0, out=small)
    return cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR)


def _active_bounds(weight: np.ndarray, threshold: float = 0.01):
    """(y0, y1, x0, x1) of the area where the weight is still alive. None
    if it is all zero.

    At 100% face priority the weight outside the face is 0, so there is
    no reason to run an expensive filter out there. A full-frame NLM at
    32MP takes several seconds.
    """
    rows = np.where(weight.max(axis=1) > threshold)[0]
    cols = np.where(weight.max(axis=0) > threshold)[0]
    if rows.size == 0 or cols.size == 0:
        return None
    return int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1


def apply_noise_reduction(
    image: np.ndarray, detail: DetailSettings, faces: np.ndarray | None = None
) -> np.ndarray:
    """Remove luminance noise and colour noise separately. float 0~255
    BGR in and out.

    Trying to remove both with one filter fails at both, because they are
    different in character. Luminance noise is pixel-level grain, so it
    overlaps detail in frequency; colour noise is blotches tens of pixels
    across, so a small kernel never reaches it. The old approach (5x5
    bilateral on BGR plus a 3x3 median on a/b) did exactly that - in
    measurements it lost 50% of the detail of an R6M3 ISO6400 photo and
    the noise left over was still more than an unadjusted ISO800 photo.

    faces are face boxes in this image's coordinate system. Together with
    detail.face_priority they lower the strength outside the faces.
    """
    if not detail.noise_reduction and not detail.color_noise_reduction:
        return image

    if detail.noise_algorithm is NoiseAlgorithm.LEGACY:
        return _legacy_noise_reduction(image, detail)

    ycc = cv2.cvtColor(
        np.clip(image, 0.0, 255.0).astype(np.float32), cv2.COLOR_BGR2YCrCb
    )

    # Colour goes first. Removing luminance first would run the detail
    # preservation decision with the colour blotches still in place, and
    # mistake the blotches for detail.
    if detail.color_noise_reduction:
        _reduce_color_noise(
            ycc, detail.color_noise_reduction, detail.color_noise_radius,
            getattr(detail, "color_noise_shadow", 0),
        )

    if detail.noise_reduction:
        luma = ycc[:, :, 0]
        height, width = luma.shape[:2]
        weight = _face_weight_map(
            height, width, faces, getattr(detail, "face_priority", 0)
        )

        bounds = (0, height, 0, width)
        if weight is not None:
            active = _active_bounds(weight)
            if active is None:
                return cv2.cvtColor(ycc, cv2.COLOR_YCrCb2BGR)
            bounds = active

        y0, y1, x0, x1 = bounds
        patch = np.ascontiguousarray(luma[y0:y1, x0:x1])
        # σ is measured over the **whole frame**. Measured on the cropped
        # patch, the same slider would be a different strength with face
        # priority on and off - inside a face is usually quiet (measured
        # σ 1.52), half of the full frame (3.21), so h shrinks by that
        # much and removes less. To the user it looks like "turning on
        # face priority made the noise reduction weaker".
        sigma = estimate_noise_sigma(luma)
        denoised = _denoise_luma_plane(
            patch, detail.noise_algorithm, detail.noise_reduction / 100.0, sigma,
            getattr(detail, "noise_passes", 1),
        )
        if detail.noise_detail:
            denoised = _protect_detail(patch, denoised, detail.noise_detail, sigma)
        if weight is not None:
            denoised = patch + (denoised - patch) * weight[y0:y1, x0:x1]
        ycc[y0:y1, x0:x1, 0] = denoised

    return cv2.cvtColor(ycc, cv2.COLOR_YCrCb2BGR)


def _legacy_noise_reduction(image: np.ndarray, detail: DetailSettings) -> np.ndarray:
    """The old approach, unchanged. Used only when a photo denoised with
    it has to be reproduced.

    It is left unfixed - changing even one character here removes the
    reason this function exists, which is reproducing the old version.
    """
    as_uint8 = np.clip(image, 0, 255).astype(np.uint8)
    if detail.color_noise_reduction:
        lab = cv2.cvtColor(as_uint8, cv2.COLOR_BGR2Lab)
        strength = detail.color_noise_reduction / 100.0 * 15
        lab[:, :, 1] = cv2.medianBlur(lab[:, :, 1], 3 if strength < 8 else 5)
        lab[:, :, 2] = cv2.medianBlur(lab[:, :, 2], 3 if strength < 8 else 5)
        as_uint8 = cv2.cvtColor(lab, cv2.COLOR_Lab2BGR)
    if detail.noise_reduction:
        strength = detail.noise_reduction / 100.0
        as_uint8 = cv2.bilateralFilter(
            as_uint8, 5, int(10 + 60 * strength), int(10 + 60 * strength)
        )
    return as_uint8.astype(np.float32)


STRIPE_MIN_PERIOD = 16
STRIPE_MAX_PERIOD = 400
"""The period range (pixels) that counts as striping.

Measured: the period of the LED-wall striped frames was **103px**, while
frames without striping came in at 4~5px (just the autocorrelation of
noise). The bottom is held at 16 so noise is not mistaken for striping,
and the top is cut at 400 to exclude the gentle brightness variation
that covers only part of the frame height.
"""

STRIPE_BASELINE_SIGMA = 13.5
"""The Gaussian sigma used to build the baseline.

**It has to be clearly smaller than the stripe period (measured
103px).** Set larger, the striping itself gets absorbed into the
baseline and disappears from the residual - with it at 133 the signal
was lost entirely and even clean frames were being corrected.
"""

STRIPE_MIN_STRENGTH = 0.35
"""An autocorrelation peak below this is taken to mean no striping.

Measured: striped frames 0.717 / 0.669, clean frames 0.618 / 0.447.
Clean frames come out fairly high as well, so **this value alone cannot
separate them** - it has to be read together with the period
(STRIPE_MIN_PERIOD).
"""


def measure_stripe(gray: np.ndarray) -> tuple[np.ndarray, int, float]:
    """Find the periodic component of the row brightness.

    Returns: (per-row correction, period, strength of the periodicity). A
    period of 0 means no striping.

    It uses the row **mean** and keeps the baseline sigma **smaller** than
    the stripe period. The first version used the median plus a large
    sigma (133) and lost the signal entirely - with sigma larger than the
    period the 103px oscillation is absorbed into the baseline and
    disappears from the residual, every period comes out at the lower
    bound (16), and **even clean frames got 67% corrected**.
    """
    profile = gray.mean(axis=1).astype(np.float32)
    if profile.size < STRIPE_MIN_PERIOD * 4:
        return np.zeros_like(profile), 0, 0.0

    # Gentle brightness variation (gradients, vignetting) is part of the
    # photo and has to be removed, but the sigma must always be smaller
    # than the stripe period.
    baseline = cv2.GaussianBlur(profile.reshape(-1, 1), (0, 0),
                                STRIPE_BASELINE_SIGMA).ravel()
    residual = profile - baseline

    centred = residual - residual.mean()
    spread = float(centred.std())
    if spread < 1e-3:
        return np.zeros_like(profile), 0, 0.0

    norm = centred / spread
    auto = np.correlate(norm, norm, mode="full")[len(norm):] / len(norm)
    window = auto[STRIPE_MIN_PERIOD:min(STRIPE_MAX_PERIOD, len(auto))]
    if window.size == 0:
        return np.zeros_like(profile), 0, 0.0

    strength = float(window.max())
    period = int(np.argmax(window)) + STRIPE_MIN_PERIOD
    if strength < STRIPE_MIN_STRENGTH:
        return np.zeros_like(profile), 0, strength
    return residual, period, strength


def apply_destripe(image: np.ndarray, amount: int) -> np.ndarray:
    """Remove the horizontal striping produced by LED walls and the like.
    float 0~255 BGR.

    When the PWM flicker of an LED panel and the rolling-shutter readout
    fall out of step, horizontal bands are left on the frame. Both
    measured frames had **the same 103px period** - ISO and shutter both
    differed, so the fact that it is the same means it comes from the
    readout period and not from the subject.

    The correction only subtracts one value per row, so detail in the
    horizontal direction is not touched.
    """
    if amount <= 0:
        return image

    gray = cv2.cvtColor(np.clip(image, 0.0, 255.0).astype(np.float32),
                        cv2.COLOR_BGR2GRAY)
    residual, period, _strength = measure_stripe(gray)
    if period <= 0:
        return image  # do nothing when no striping is detected

    correction = residual * (min(100, amount) / 100.0)
    return image - correction[:, None, None]


def _apply_detail(image: np.ndarray, detail: DetailSettings,
                  render_scale: float | None = None) -> np.ndarray:
    """Sharpening and noise reduction.

    Noise reduction runs first. In the other order, removing the noise
    that sharpening has amplified smears the detail along with it.
    """
    if detail.is_neutral():
        return image

    faces = None
    if getattr(detail, "face_priority", 0) and detail.noise_reduction:
        # Detected once more here, separately from the masks. Masks may
        # not exist at all, and even when they do they run later than
        # this point, whereas noise reduction has to finish before
        # sharpening. Detection runs on a downscale, so it is about 50ms.
        from .masks import _detect_faces_full  # noqa: PLC0415 - cyclic import

        try:
            faces = _detect_faces_full(np.clip(image, 0, 255).astype(np.uint8))
        except cv2.error:
            log.debug("얼굴 우선 노이즈 감소용 검출 실패", exc_info=True)

    # Striping is removed **before noise reduction**. In the other order,
    # noise reduction mistakes the striping for flat texture and smears
    # part of it, leaving only half to be subtracted here, which comes
    # out blotchy.
    result = apply_destripe(image, getattr(detail, "destripe", 0))
    result = apply_noise_reduction(result, detail, faces)

    if detail.sharpen_amount:
        # The radius was tuned on screen (1400px), so it is scaled with
        # the size - otherwise the exported file is less sharp than the
        # screen (TUNED_LONG_EDGE). The caller may pass the factor in
        # scene terms (zoomed-region render).
        scale = render_scale if render_scale is not None else scale_for(result)
        radius = max(0.3, detail.sharpen_radius * scale)
        blurred = cv2.GaussianBlur(result, (0, 0), radius)
        result = result + (result - blurred) * (detail.sharpen_amount / 100.0)

    return result


# ---------------------------------------------------------------- effects


def _apply_effects(image: np.ndarray, effects: EffectSettings,
                   render_scale: float | None = None) -> np.ndarray:
    """Grain and vignetting."""
    if effects == EffectSettings():
        return image

    result = image
    height, width = result.shape[:2]

    if effects.vignette_amount:
        y, x = np.ogrid[:height, :width]
        center_y, center_x = height / 2.0, width / 2.0
        distance = np.sqrt(
            ((x - center_x) / center_x) ** 2 + ((y - center_y) / center_y) ** 2
        )
        midpoint = max(0.1, effects.vignette_midpoint / 100.0 * 1.5)
        mask = np.clip((distance - midpoint) / max(1e-3, 1.5 - midpoint), 0.0, 1.0)
        result = result * (1.0 + (effects.vignette_amount / 100.0) * mask[:, :, None])

    if effects.grain_amount:
        # Grain size is produced by upscaling low-resolution noise.
        #
        # The grain size was also tuned on screen (1400px), so it is
        # scaled with the size. Fixed, the grain gets relatively finer at
        # large resolutions and the texture ends up nothing like the
        # screen - measured, 48% of pixels differed by over 5 levels.
        base = effects.grain_size / 100.0 * 4 + 1
        scale = render_scale if render_scale is not None else scale_for(result)
        size = max(1, int(round(base * scale)))
        rng = np.random.default_rng(12345)  # reproducible: no preview flicker
        small = rng.normal(0, 1, (max(1, height // size), max(1, width // size), 1))
        noise = cv2.resize(
            small.astype(np.float32), (width, height), interpolation=cv2.INTER_LINEAR
        )
        result = result + noise[:, :, None] * (effects.grain_amount / 100.0 * 18.0)

    return result


# ---------------------------------------------------------------- entry points


def quantize(image: np.ndarray, bit_depth: int = 8) -> np.ndarray:
    """Drop a float 0~255 image to the output bit depth. **Only as the
    last step.**

    8-bit **truncates**, exactly as it always has (switching to rounding
    would put every file exported so far off by half a level). 16-bit
    follows the same scheme - 255.0 x 257 = 65535, so the upper bound
    lands exactly.
    """
    if bit_depth == 16:
        if image.dtype == np.uint16:
            return image
        return np.clip(image.astype(np.float32) * 257.0,
                       0.0, 65535.0).astype(np.uint16)
    if image.dtype == np.uint8:
        return image
    if image.dtype == np.uint16:
        return (image // 257).astype(np.uint8)
    return np.clip(image, 0.0, 255.0).astype(np.uint8)


def apply_optics_stage(
    image_bgr: np.ndarray,
    settings: DevelopSettings,
    source: "Path | None" = None,
    metadata=None,
) -> "tuple[np.ndarray, DevelopSettings]":
    """Apply only the optical corrections - **a stage that needs the
    whole frame**.

    Distortion, vignetting and chromatic aberration are all defined
    relative to the frame centre and size. Applied to a cropped patch,
    the patch itself is taken for the whole frame, and in measurements
    (A6700 + E PZ 16-50mm, the middle 40% as a patch) that came out
    25.90 levels off on average with 71.9% of pixels off by more than
    5 levels (+52.7 levels at the patch corners).

    To develop only the region visible when zoomed in, call this
    **before cropping** and then call apply_settings with the settings it
    hands back. The returned settings have neutral optics, so it can
    never be applied twice - this is a place where the picture comes out
    wrong if the order is not kept, so the contract blocks it.

    apply_settings uses this function too. If the implementation split in
    two, one of the halves would go stale.
    """
    if settings.optics.is_neutral():
        return image_bgr, settings

    from ..raw_io import is_editable_image
    from .optics import apply_optics

    optics = settings.optics
    # JPEG/HEIF are results the camera already baked with lens correction
    # applied. Applying it once more is a double correction and bends the
    # edges the other way. The screen locks the checkbox, but that alone
    # is not enough - even a locked checkbox returns True from
    # isChecked(), and pressing a preset brings the value back. Blocking
    # it here makes every path - CLI, batch, preset - behave the same.
    # (Manual correction is left alone. That is a value the user enters
    # after looking at the picture.)
    if optics.auto_enabled and source is not None and is_editable_image(source):
        optics = replace(optics, auto_enabled=False)

    profiled = source is None or not is_editable_image(source)
    corrected = apply_optics(image_bgr, optics, metadata, profiled)
    return corrected, replace(settings, optics=OpticsSettings())


def apply_settings(
    image_bgr: np.ndarray,
    settings: DevelopSettings,
    source: "Path | None" = None,
    metadata=None,
    wb: "tuple | None" = None,
    main_face_box: "tuple[float, float, float, float] | None" = None,
    bit_depth: int = 8,
    base_kelvin: float = 0,
    scene_hw: "tuple[int, int] | None" = None,
    output_space: "str | None" = None,
) -> np.ndarray:
    """Apply the whole set of adjustments to a BGR uint8 image.

    Given output_space, the result is moved into that colour space before
    being returned - "srgb" for the screen, the space the user chose for
    export. Adjustments run in the working space (wider than sRGB), so
    this conversion is needed at the last point on the way out, and it
    has to come **before quantisation**. Dropping to 8 bits in the
    working space and converting afterwards leaves ProPhoto's coarse
    8-bit steps sitting in the sRGB shadows (measured against going via
    16 bits: at most 1 level for the screen vs at most 28 levels for the
    after-the-fact conversion).

    None (the default) returns the working space values unchanged - that
    is for calls used as an intermediate stage (the camera look's working
    space verification, for example).

    Which space the input is in is decided by dtype. float is the
    **working space** produced by the demosaic or an editable load;
    uint8 is already sRGB for the screen (embedded JPEG preview, degraded
    fallback - the same contract as to_display). Editable (JPEG/HEIF)
    sources used to be excluded as well, but that premise was wrong -
    since 0.15.7 editable files also go through to_working on load and
    end up in the working space (measured: the HIF base differs from an
    sRGB decode by 6.58 and from the ProPhoto conversion by 0.19). That
    is why only JPEG/HEIF in the develop window had the wrong colour.

    With bit_depth=16 the result comes back as uint16 (0~65535). The
    pipeline flows in float32 to begin with, so only the final
    quantisation differs - measurement confirmed the gradation really is
    preserved (5.44 million levels right after the demosaic, and still
    4.77~6.04 million after each adjustment stage).

    source/metadata are only used when drawing the info strip at the
    bottom. Without them the strip is skipped. wb is a
    (camera_whitebalance, daylight_whitebalance) tuple used for absolute
    colour temperature conversion. Without it, a generic approximation
    anchored at 5500K is used.

    base_kelvin is **which colour temperature image_bgr was already
    demosaiced at** (0 = as-shot). If it equals the target, the white
    balance gain becomes 1 and is skipped - it means it was already
    applied in sensor linear, and that is the physically correct side.

    scene_hw is **the size image_bgr would have if it were the whole
    scene**. It is passed when rendering only the cropped region visible
    at a zoom - measuring the radius from the patch size makes sharpness,
    texture and clarity land weakly in the zoomed preview only (because
    the information that the patch is 1/zoom of the scene is lost).
    Without it, the image itself is taken to be the whole scene.

    main_face_box is the normalised coordinates of the main subject's
    face as picked by the analysis (or changed by the user on screen).
    The face mask's "main subject" target follows this face. Without it
    the mask picks one itself from the image at this point, which can
    land on a different face from the red box on screen.
    """
    # Almost every OpenCV operation throws on an array with zero width or
    # height. Even if one frame comes out that way from a bad crop
    # calculation or a corrupt file, the preview thread or a whole batch
    # export must not stop, so we keep only the return contract and hand
    # it straight back.
    if image_bgr.size == 0:
        return quantize(image_bgr, bit_depth)

    # The space the adjustment values live in. RAW has been through the
    # decoder gamma and the standard profile, whereas JPEG/HEIF is true
    # sRGB baked by the camera and goes through neither the demosaic nor
    # the profile (_baseline_transfer). Every operation on light has to
    # look at this value - exposure, local exposure, manual vignetting.
    from ..raw_io import is_editable_image

    editable = source is not None and is_editable_image(source)
    profiled = not editable

    # uint8 input is already an sRGB value for the screen (embedded JPEG
    # preview, degraded fallback - the same contract as to_display).
    # Working space values only ever arrive as float. It must not be
    # decided by source kind (editable) - even for editable, a float base
    # has been through to_working on load and is working space (see the
    # docstring above).
    came_as_display = image_bgr.dtype == np.uint8

    def _to_output(array: np.ndarray) -> np.ndarray:
        """Into output_space if there is one. Once, before quantisation."""
        if output_space is None:
            return array
        from . import icc

        source_space = "srgb" if came_as_display else icc.WORKING_SPACE
        if source_space == output_space:
            return array
        if came_as_display:
            # The rare case of exporting the degraded fallback (uint8
            # sRGB) to something like Adobe RGB. working_to would mistake
            # an sRGB value for a working space value - that is what the
            # old after-the-fact conversion actually did.
            return icc.convert_from_srgb(array, output_space)
        return icc.working_to(
            np.clip(array, 0.0, 255.0).astype(np.float32), output_space)

    if settings.is_neutral():
        # The return contract (integer BGR) has to hold even with no
        # adjustments. Demosaic input is float 0~255, so handing it back
        # unchanged turns the preview into colour noise and breaks saving
        # in the encoder. This was an actual bug in the "final preview".
        return quantize(_to_output(image_bgr), bit_depth)

    # JPEG/HEIF have no sensor data, so load_demosaiced **silently
    # ignores** target_kelvin. Accepting base_kelvin anyway would make the
    # gain 1 and white balance would disappear entirely. Rather than
    # scattering guards across the three call sites, it is blocked here
    # once.
    if editable:
        base_kelvin = 0

    # Optical correction comes first. Straightening the lens distortion,
    # then cropping and working the tone, is the right order - the other
    # way round applies the distortion model to a cropped patch.
    working, settings = apply_optics_stage(image_bgr, settings, source,
                                           metadata)

    # **Cropping and rotation are deferred until after the masks.** Mask
    # coordinates are normalised 0~1, so they get multiplied by the image
    # size at that point. Applied after cropping they become relative to
    # the cropped frame, and the moment the crop changes, a mask already
    # drawn moves across the scene (measured: cropping to the right half
    # moves a (0.25,0.25) mask to (0.625,0.25) in the original).
    #
    # Applying the masks first here makes the coordinates naturally
    # **scene-relative**, and the geometry stage that follows carries the
    # masks along with it. Rotation and flipping come out right without
    # hand-writing any coordinate transform - we chose to remove the
    # places where it could go wrong.
    #
    # The face box (main_face_box) is relative to the analysis image
    # (before cropping) as well, so it fits here.
    #
    # The price: global adjustments run on the whole frame before the
    # crop, so the more is cropped away the more is wasted. And the
    # clarity and texture radii look at the pre-crop size - adjusting the
    # crop does not change the texture, which is actually more consistent.
    result = working.astype(np.float32)

    # The radius scale is decided **in one place**. A zoomed-region render
    # receives a patch of the scene, so looking only at the image size
    # shrinks the scale to 1/zoom - scene_hw preserves that information
    # (see the docstring). Clarity keeps to the short edge and the rest to
    # the long edge, following the existing formulas unchanged.
    scale_base = tuple(scene_hw) if scene_hw else working.shape[:2]
    render_scale = max(scale_base) / float(TUNED_LONG_EDGE)
    clarity_radius = max(3.0, min(scale_base) / 120)

    basic = settings.basic
    result = _apply_white_balance(result, basic, wb, base_kelvin)

    # Tone and curves are both RGB LUTs, so they are composed in advance
    # on the 256-entry table and applied to the image only once. Float
    # interpolation costs per pixel, so we cut the number of passes.
    tone = _tone_lut(basic, profiled)
    curve = _curve_lut(settings.curve)
    combined = np.interp(tone, _IDENTITY, curve).astype(np.float32)
    if not np.array_equal(combined, _IDENTITY):
        result = _apply_lut(result, combined)

    # Per-channel curves apply their LUT to each channel separately
    for channel, points in (
        (2, settings.curve.points_red),
        (1, settings.curve.points_green),
        (0, settings.curve.points_blue),
    ):
        if points:
            lut = _spline_lut(curve_control_points(points))
            result[:, :, channel] = _apply_lut(
                result[:, :, channel][:, :, None], lut
            )[:, :, 0]

    if basic.dehaze:
        result = _apply_dehaze(result, basic.dehaze)
    if basic.clarity:
        result = _local_contrast(result, basic.clarity, radius=clarity_radius)
    if basic.texture:
        # Clarity was already proportional to the size (above), but
        # texture was fixed at 1.2px.
        result = _local_contrast(result, basic.texture,
                                 radius=max(0.5, 1.2 * render_scale))

    result = _apply_hsl(result, settings.hsl)
    result = _apply_color_grade(result, settings.color_grade)
    result = _apply_saturation(result, basic)
    result = _apply_detail(result, settings.detail, render_scale)

    # Local adjustments (masks) go on top of the finished global
    # adjustments. Face/eye/background are detected again from the image
    # at this point, so the masks match the screen exactly.
    if settings.masks:
        from .masks import apply_masks

        result = apply_masks(result, settings.masks,
                             main_face_box=main_face_box, profiled=profiled)

    # Now crop. The masks have been applied in scene coordinates, so
    # rotation, flipping and cropping carry them along (see the comment
    # above).
    result = apply_geometry(result, settings.geometry).astype(np.float32)

    # The presentation effects (vignette, grain) go on **after cropping**.
    # Unlike the masks, which attach to the scene, these two attach to
    # the final composition - a vignette darkens the four corners of the
    # frame, so applied before the crop it ends up off-centre on a crop
    # that is not centred (measured: on a right-half crop the left-right
    # brightness is reversed). Grain is the grain of the print paper, so
    # the final size is the right reference. It is the same reason the
    # watermark and the info strip are added after cropping.
    #
    # This move reversed the order of masks and effects (previously:
    # effects -> masks). The difference is that a mask's local tone now
    # sits under the vignette multiply, which we accept as the price of
    # each of them sitting in the coordinate system that is right for it.
    #
    # The grain scale is scene-relative (render_scale). Measured from the
    # post-crop size it would be consistent between preview and export
    # but would break the zoomed-region render, and physically it is also
    # right that cutting a piece of film and enlarging it makes the grain
    # bigger.
    result = _apply_effects(result, settings.effects, render_scale)

    # The overlays are carried in float too, and quantisation happens
    # **only once**. This used to drop to uint8 here, so a 16-bit output
    # ended up with 8-bit gradation even with the watermark and the info
    # strip switched off.
    output = apply_overlays(
        np.clip(result, 0.0, 255.0).astype(np.float32),
        settings, source, metadata)
    return quantize(_to_output(output), bit_depth)


def apply_overlays(
    image_bgr: np.ndarray,
    settings: DevelopSettings,
    source: "Path | None" = None,
    metadata=None,
) -> np.ndarray:
    """Lay the watermark and the info strip onto a finished 8-bit image.

    These two are not adjustments that work on gradation; they are
    annotations added to the result. Keeping them separate is what lets
    the preview compute the histogram and the clipping warnings from the
    gradation of "the photo itself". If the black bar of the info strip
    mixed into the histogram, it would look as though the adjustment
    values had changed.
    """
    output = image_bgr
    if settings.watermark.is_active():
        from .watermark import apply_watermark

        output = apply_watermark(output, settings.watermark)

    # The info strip is added below the photo, so it comes after the
    # watermark. Added first, the watermark could land on top of the
    # strip.
    if settings.exif_strip.is_active() and source is not None:
        from .exif_strip import apply_exif_strip

        output = apply_exif_strip(output, source, metadata, settings.exif_strip)

    return output


def export_image(
    source: Path,
    destination: Path,
    settings: DevelopSettings,
    quality: int = 95,
    long_edge: int | None = None,
    main_face_box: "tuple[float, float, float, float] | None" = None,
    bit_depth: int = 8,
    color_space: str = "srgb",
) -> Path:
    """Read a RAW, apply the adjustments and save.

    This is the full demosaic path (load_demosaiced), the same baseline the
    adjust window renders from - the embedded JPEG is only a fallback for
    when the decode fails, and it is logged when that happens. What it does
    not do is reach past the white level: LibRaw clips there unless
    highlight recovery is on, so for large highlight rescues Lightroom is
    still the right tool.

    Passing main_face_box makes the face mask's "main subject" land on
    the face seen on screen. Without it, the saved copy alone can end up
    on a different face.
    """
    from ..raw_io import (
        load_demosaiced,
        load_preview,
        read_metadata,
        read_white_balance,
        resize_long_edge,
    )

    # The same baseline (demosaic) as the develop window has to be used,
    # or what you see is not what you get. The embedded JPEG has the
    # camera picture style baked in, which changes what the adjustment
    # values mean.
    #
    # **White balance is applied here.** It is physically an operation on
    # sensor-linear values, so multiplying a channel gain onto developed
    # values is an approximation (measured: 9.2 levels on average at
    # 183 mired away from as-shot). Export is not racing the clock, so it
    # always takes the accurate side. The screen uses the approximation
    # only while the slider is being dragged, and on release it arrives
    # at the same place as here (_settle_white_balance in gui/loupe.py).
    base_kelvin = int(settings.basic.temperature) \
        if settings.basic.temperature > 0 else 0
    try:
        image = load_demosaiced(
            source, target_kelvin=base_kelvin or None,
            highlight_recovery=settings.basic.highlight_recovery)
    except Exception as exc:  # noqa: BLE001 - JPEG fallback if demosaic fails
        # This must not pass silently. A result does come out, but it
        # comes from a JPEG with the camera picture style baked in, so
        # its colour and gradation differ from a RAW develop. Measured:
        # LibRaw 0.22 cannot decode the high-efficiency compressed NEF
        # of the Nikon Z9.
        log.warning(
            "%s: RAW를 현상하지 못해 내장 JPEG으로 내보냅니다 "
            "(색·계조가 RAW 현상과 다릅니다) — %s",
            source.name, exc,
        )
        image = load_preview(source)
        # The fallback is a JPEG baked by the camera, so sensor-linear WB
        # was never applied. Pretending it was would make white balance
        # disappear entirely - we go back to the gain.
        base_kelvin = 0
    if long_edge:
        image = resize_long_edge(image, long_edge)

    # Needed for the info strip, EXIF insertion and **automatic lens
    # correction**.
    #
    # The automatic correction was once left out. lensfun looks up a
    # profile by camera model and lens name, so without metadata it
    # silently returns the original (optics.apply_auto_correction). The
    # correction was therefore applied on screen but not in the exported
    # file, and only started being applied once the info strip was turned
    # on - a combination that looks entirely unrelated to what is
    # switched on or off, and is hard to notice.
    metadata = None
    if settings.exif_strip.is_active() or settings.optics.auto_enabled:
        try:
            metadata = read_metadata(source)
        except Exception:  # noqa: BLE001
            metadata = None

    # WB is read only when an absolute colour temperature is used (to keep
    # the preview and the result matched)
    wb = None
    if settings.basic.temperature > 0:
        reference = read_white_balance(source)
        wb = reference.engine_wb if reference else None

    # Handing uint16 to a format that cannot take 16 bits makes cv2 **log
    # a warning and drop to 8 bits.** It is checked here as well so that a
    # file different from the one requested does not go out silently (the
    # screen already locks it, but there is a path that queues a job and
    # then changes only the format).
    suffix = destination.suffix.lower()
    if bit_depth == 16 and suffix not in (".png", ".tif", ".tiff"):
        log.info("%s는 16비트를 저장하지 못해 8비트로 내보냅니다", suffix)
        bit_depth = 8

    from . import icc

    # WebP cannot carry an ICC profile, so it only goes out as sRGB -
    # exporting numbers from another space with no tag makes the
    # receiving side read them as sRGB and the colour goes wrong.
    #
    # ExportOptions already makes the same decision (__post_init__). It is
    # still blocked once more here - this function takes the string
    # directly, so there are paths that never go through that dataclass,
    # such as the CLI, tests and the queue. Automatic lens correction is
    # blocked twice for the same reason (apply_settings above).
    target = "srgb" if suffix.lstrip(".") in ("webp",) else color_space

    # The colour space conversion happens inside apply_settings, **before
    # quantisation**. It used to be applied here to the quantised result,
    # which leaves the working space's coarse 8-bit steps in the sRGB
    # shadows (measured against going via 16 bits: at most 28 levels then
    # vs 1 level now). It passes through the same place as the screen
    # (output_space="srgb"), so screen == exported file holds regardless
    # of bit depth. The degraded fallback (uint8 sRGB preview) is told
    # apart by dtype too, which fixes mistaking an sRGB value for a
    # working space value at the same time. The profile tag is attached
    # after saving (so as not to touch the encoded bytes).
    result = apply_settings(image, settings, source, metadata, wb=wb,
                            main_face_box=main_face_box, bit_depth=bit_depth,
                            base_kelvin=base_kelvin, output_space=target)

    destination.parent.mkdir(parents=True, exist_ok=True)

    params = []
    if suffix in (".jpg", ".jpeg"):
        params = [cv2.IMWRITE_JPEG_QUALITY, int(quality)]
    elif suffix == ".png":
        # PNG uses a 0~9 compression level, so the quality value is
        # mapped inverted
        params = [cv2.IMWRITE_PNG_COMPRESSION, max(0, min(9, 9 - int(quality / 11)))]
    elif suffix in (".webp",):
        params = [cv2.IMWRITE_WEBP_QUALITY, int(quality)]
    elif suffix in (".tif", ".tiff"):
        # Lossless compression. The quality slider is meaningless here
        # and is ignored - without compression a single 32MP frame comes
        # close to 100MB.
        params = [cv2.IMWRITE_TIFF_COMPRESSION, 8]  # 8 = Adobe Deflate

    # cv2.imwrite fails on Korean paths, so a Unicode-safe helper is used.
    from ..raw_io import imwrite_unicode

    if not imwrite_unicode(destination, result, params):
        raise OSError(f"저장 실패: {destination}")

    # EXIF can only be embedded in JPEG
    if settings.metadata.enabled and suffix in (".jpg", ".jpeg"):
        from .metadata import write_metadata

        write_metadata(source, destination, settings.metadata)

    # The colour profile is embedded **after the EXIF**, because the EXIF
    # writer can rearrange the segments and drop an APP2 that was written
    # first. The profile follows the space the pixels are actually in
    # (target) - the only case where that diverges from the color_space
    # argument is the forced sRGB for WebP, and WebP is not an embed
    # target, but this leaves no structure at all in which the pixels and
    # the tag could disagree.
    if suffix in icc.EMBEDDABLE:
        icc.embed(destination, target)

    return destination
