"""Camera look matching - a starting point that brings a neutral develop
close to the embedded JPEG, taken as the answer key.

The develop window's base is a neutral demosaic (+ the standard profile),
so it differs considerably from the embedded JPEG the camera baked
(picture style, tone mapping). Here that difference is fitted as
(1) exposure, (2) a luma quantile curve and (3) a saturation scalar
(exactly the maths of the research script
tools/research/research_camera_look.py), and the result is recorded as
**the app's real adjustment values**:

  - exposure   -> BasicSettings.exposure (quantised to the slider's
                  0.01EV precision)
  - curve      -> the CurveSettings parametric 4 (highlights/lights/
                  darks/shadows), falling back to a point curve
                  (points_rgb) when they cannot express it
  - saturation -> BasicSettings.saturation

The reason a LUT is not slipped in behind the scenes but written as
settings is the what-you-see-is-what-you-get guarantee - it shows up on
the sliders as it is, and preset saving, batch apply and export all read
the same values.

Measured evidence (RESEARCH_METADATA.md sections 9 and 9-1, 1,035 frames
in total):
  - luma MAE 21.5 -> 8.9 (31 varied scenes) / 15.2 -> 10.6 (1,004 frames,
    mostly concert)
  - quantising to 4 control points is effectively equal to a 256-step LUT
    (10.9 vs 10.6)
  - per-channel RGB curves are no better than the luma curve (8.9 vs 8.9,
    and saturation gets worse)
  - fitting costs 7ms/frame - it can be fitted on the spot every time the
    develop window opens
  - highlight clipping vs residual correlation r=-0.02 - no ceiling
    warning needed

The source of the residual error is the camera's local tone mapping (the
DRO family), which is an inherent limit of a global curve. This feature is
'the most similar starting point', not a complete reproduction.
"""

from __future__ import annotations

from dataclasses import replace

import cv2
import numpy as np

from .settings import BasicSettings, CurveSettings, DevelopSettings

SIZE = 256
"""Fitting and evaluation resolution (long edge). The same value as the
research - a look is a low-frequency phenomenon, so raising it above this
does not change the result, and only at this size is fitting on every open
free."""

POINTS = 16
"""Number of quantile curve control points (the same as the research). It
is quantised to app values in the end, so the sample count here only
governs the stability of the fit."""

EXPOSURE_DECIMALS = 2
"""Decimal places the exposure is recorded to. The slider
(QDoubleSpinBox decimals=2) is at this precision, so computing any more
finely only gets rounded the moment it goes on screen - to keep what you
see being what you get, it has to be quantised at the computation stage
first."""

PARAMETRIC_MAX_ERR = 2.5
"""The ceiling for accepting the parametric 4-value approximation
(weighted mean error, in 8-bit levels).

Worse than this and it falls back to the point curve. Measured in the
research, the whole residual of the 4-point quantisation came to only
+0.3 against 256 steps, so a LUT approximation error at this level makes
practically no difference to the image residual."""

_CURVE_SAMPLE_XS = (0, 4, 10, 22, 40, 64, 96, 136, 192, 255)
"""The input positions the LUT is sampled at in the point-curve fallback.

Why the shadow end is dense: the sharp bends of a camera look are
clustered in the toe lift, and at even spacing (32 apart) the spline
missed by 2.4 levels on average over that stretch (measured at gamma
0.45). This grid passes the same curve at 0.4 levels on average. Sample
any denser and the curve editor swarms with points, which makes it hard
for the user to touch."""


# ------------------------------------------------------------ the fitting core
# (the maths of the research script's fit_look, carried over as it is.
#  This is the reference implementation; the research side stays as a copy
#  for reproduction.)


def _luma(bgr: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)[..., 0].astype(np.float32)


def _chroma(bgr: np.ndarray) -> float:
    ycc = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb).astype(np.float32)
    return float(np.abs(ycc[..., 1:] - 128.0).mean())


def _small(bgr: np.ndarray) -> np.ndarray:
    height, width = bgr.shape[:2]
    scale = SIZE / max(height, width)
    if scale >= 1.0:
        return bgr
    return cv2.resize(bgr, (max(8, int(width * scale)),
                            max(8, int(height * scale))),
                      interpolation=cv2.INTER_AREA)


def _pair(render: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Shrink both to the evaluation resolution and match their shapes.

    The embedded JPEG can differ slightly from the sensor in aspect ratio
    (a margin crop and so on). We use only quantile and mean statistics,
    not a pixel-level registration, so a forced resize is enough - the
    research measured it the same way.
    """
    render_s, target_s = _small(render), _small(target)
    if render_s.shape != target_s.shape:
        target_s = cv2.resize(target_s, (render_s.shape[1], render_s.shape[0]))
    return render_s, target_s


def fit_look(render: np.ndarray, target: np.ndarray) -> dict:
    """The raw parameters that bring the neutral develop (render) close to
    target (the embedded JPEG).

    Returns: {"exposure": EV, "lut": float32[256], "saturation": factor}.
    lut is the mapping for the luma **after** exposure has been applied.
    """
    from .engine import apply_exposure_with_shoulder, to_light

    render_s, target_s = _pair(render, target)
    luma_r, luma_t = _luma(render_s), _luma(target_s)

    # (1) Exposure: the median log ratio (insensitive to extreme
    # clipping).
    #
    # The ratio is taken **in the space the engine multiplies in**. What
    # we are after is "how much does engine.apply_exposure have to
    # multiply the light by to move render's display value to target's
    # display value", so both values have to be undone with the engine's
    # transfer function (to_light). We used to undo with sRGB, but render
    # is a value that has been through postprocess, the body correction
    # and the profile curve, so it is not sRGB - the value printed on the
    # slider was off by 0.24~0.81 EV (measured).
    #
    # Undoing target with sRGB on the grounds of where it came from (a
    # camera JPEG = genuine sRGB) is **wrong.** Match the same picture to
    # itself and the exposure ought to be 0, but the two spaces split and
    # 0.59 comes out. The target is simply a display value to reach.
    #
    # As the exposure grows, the pixels pinned at 255 in the exposure
    # stage rise from 0.76% to 2.25%, which looks like losing highlights.
    # But **count the final gradation and it is the opposite** - the
    # unique levels left in the bright 10% band of the target rise from
    # 121.6 to 125.4. Those pixels pinned at 255 were the ones already
    # close to white anyway, and raising the exposure properly lays the
    # rest onto the dense stretch of the curve. It must not be judged by
    # the clip ratio of an intermediate stage.
    lin_r = float(to_light(np.median(luma_r)))
    lin_t = float(to_light(np.median(luma_t)))
    exposure = float(np.log2((lin_t + 1e-4) / (lin_r + 1e-4)))
    # The render path applies a highlight shoulder after exposure
    # (_tone_lut). Use only the pure multiply here and the quantile curve
    # below gets matched on top of a brightness that knows nothing of the
    # shoulder, which breaks this file's own "fitting and rendering have
    # to use the same operation" all over again.
    luma_r2 = np.clip(apply_exposure_with_shoulder(luma_r, exposure), 0, 255)

    # (2) Luma quantile curve: pair like quantiles into a monotone LUT
    quantiles = np.linspace(0.02, 0.98, POINTS)
    src = np.quantile(luma_r2, quantiles)
    dst = np.quantile(luma_t, quantiles)
    src = np.maximum.accumulate(np.concatenate([[0.0], src, [255.0]]))
    dst = np.maximum.accumulate(np.concatenate([[0.0], dst, [255.0]]))
    lut = np.interp(np.arange(256), src, dst).astype(np.float32)

    # (3) Saturation: the chroma ratio after the curve is applied
    matched = apply_look(render_s, {"exposure": exposure, "lut": lut,
                                    "saturation": 1.0})
    chroma_ratio = (_chroma(target_s) + 1e-6) / (_chroma(matched) + 1e-6)
    return {"exposure": exposure, "lut": lut,
            "saturation": float(np.clip(chroma_ratio, 0.4, 2.5))}


def apply_look(bgr: np.ndarray, look: dict) -> np.ndarray:
    """Research-side look application (YCrCb space). Used only for the
    synthetic verification and the saturation fit.

    The product render does not use this - what is final is recorded as
    settings and drawn by engine.apply_settings, and the residual against
    here is absorbed by match_settings at the saturation stage using the
    real engine response.
    """
    from .engine import apply_exposure

    ycc = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb).astype(np.float32)
    luma = np.clip(apply_exposure(ycc[..., 0], look["exposure"]), 0, 255)
    ycc[..., 0] = np.interp(luma, np.arange(256), look["lut"])
    ycc[..., 1:] = np.clip(
        (ycc[..., 1:] - 128.0) * look["saturation"] + 128.0, 0, 255)
    return cv2.cvtColor(ycc.astype(np.uint8), cv2.COLOR_YCrCb2BGR)


def score(render: np.ndarray, target: np.ndarray) -> tuple[float, float]:
    """(luma MAE, chroma MAE) - lower is more similar. The same ruler as the
    research. The second term is measured on Cr/Cb, so it is chroma rather
    than a saturation slider value - the local variable and _chroma() below
    use the same name."""
    render_s, target_s = _pair(render, target)
    luma = float(np.abs(_luma(render_s) - _luma(target_s)).mean())
    ycc_r = cv2.cvtColor(render_s, cv2.COLOR_BGR2YCrCb).astype(np.float32)
    ycc_t = cv2.cvtColor(target_s, cv2.COLOR_BGR2YCrCb).astype(np.float32)
    chroma = float(np.abs(ycc_r[..., 1:] - ycc_t[..., 1:]).mean())
    return luma, chroma


# ----------------------------------------------------------- LUT -> app values


def _weights(render_s: np.ndarray, exposure: float) -> np.ndarray:
    """Weights for the LUT approximation error - the luma histogram after
    exposure is applied.

    Treat all 256 LUT slots alike and the error over a tonal stretch that
    holds not a single pixel drags the fit around. The point is to reduce
    the image residual (MAE), so we look hard at where the pixels actually
    lie. A floor is laid down so that empty stretches are not thrown away
    entirely - the same curve gets transplanted onto the next frame over,
    whose exposure is slightly different.
    """
    from .engine import apply_exposure

    luma = np.clip(apply_exposure(_luma(render_s), exposure), 0, 255)
    hist = np.bincount(luma.astype(np.int64).ravel(), minlength=256).astype(np.float64)
    hist = hist / max(hist.sum(), 1.0)
    hist += 1.0 / 1024.0
    return (hist / hist.sum()).astype(np.float64)


def _parametric_error(amounts: tuple[int, int, int, int], lut: np.ndarray,
                      weights: np.ndarray) -> float:
    """Weighted mean error (levels) between the curve the parametric 4
    values make and the target LUT."""
    from .engine import parametric_tone_lut

    shadows, darks, lights, highlights = amounts
    approx = parametric_tone_lut(shadows, darks, lights, highlights)
    return float((np.abs(approx.astype(np.float64) - lut) * weights).sum())


def fit_parametric(lut: np.ndarray,
                   weights: np.ndarray | None = None) -> tuple[int, int, int, int]:
    """The app parametric 4 closest to the target LUT (shadows/darks/
    lights/highlights).

    It is matched against the engine's real response
    (engine.parametric_tone_lut). The response applies the per-region
    Gaussians **in sequence**, so strictly it is non-linear, but a least
    squares linearised about identity is a good starting point, and on
    top of that an integer coordinate descent (a ternary search per
    region) finds the optimum against the real response. The return
    values are integers in the slider range (-100~100), so they go on
    screen as they are.
    """
    from .engine import (
        _PARAMETRIC_STRENGTH,
        _PARAMETRIC_WIDTH,
        PARAMETRIC_REGIONS,
    )

    lut = np.asarray(lut, dtype=np.float64)
    if weights is None:
        weights = np.full(256, 1.0 / 256.0)

    # -- Linearised start: identity + sum (amount/100)*strength*gaussian ~ lut
    x = np.arange(256, dtype=np.float64) / 255.0
    basis = np.stack([
        _PARAMETRIC_STRENGTH * np.exp(-((x - center) ** 2)
                                      / (2 * _PARAMETRIC_WIDTH ** 2)) * 255.0
        for _name, center in PARAMETRIC_REGIONS
    ], axis=1)                                    # (256, 4) shadow..highlight
    delta = lut - np.arange(256, dtype=np.float64)
    w_col = np.sqrt(weights)[:, None]
    solution, *_ = np.linalg.lstsq(basis * w_col, delta * w_col[:, 0], rcond=None)
    amounts = [int(np.clip(round(v * 100.0), -100, 100)) for v in solution]

    # -- Integer coordinate descent, against the real response. Measured,
    #    the error curve along one coordinate is unimodal, so a ternary
    #    search works, and any flat bottom there might be is tidied up by
    #    the final local scan (+-2). The ternary search and the local scan
    #    keep hitting the same values, so evaluations are cached - this
    #    code runs every time the develop window opens, so the waste turns
    #    straight into waiting time.
    cache: dict[tuple[int, int, int, int], float] = {}

    def err_at(index: int, value: int) -> float:
        candidate = list(amounts)
        candidate[index] = value
        key = tuple(candidate)
        found = cache.get(key)
        if found is None:
            found = cache[key] = _parametric_error(key, lut, weights)
        return found

    for _round in range(3):
        changed = False
        for index in range(4):
            low, high = -100, 100
            while high - low > 2:
                third = (high - low) // 3
                mid1, mid2 = low + third, high - third
                if err_at(index, mid1) <= err_at(index, mid2):
                    high = mid2
                else:
                    low = mid1
            best_value = amounts[index]
            best_err = err_at(index, best_value)
            for value in range(low - 2, high + 3):
                value = int(np.clip(value, -100, 100))
                candidate_err = err_at(index, value)
                if candidate_err < best_err - 1e-9:
                    best_err, best_value = candidate_err, value
            if best_value != amounts[index]:
                amounts[index] = best_value
                changed = True
        if not changed:
            break
    return tuple(amounts)  # type: ignore[return-value]


def lut_to_curve_points(lut: np.ndarray) -> tuple[tuple[int, int], ...]:
    """Sample the LUT into curve editor points (for the parametric
    fallback).

    The input x uses a fixed grid - use the quantile positions and the
    point positions jump about from frame to frame, so nothing can be
    compared in the curve editor. It is a monotone LUT plus a monotone
    spline, so at this spacing it passes within less than a level.
    """
    lut = np.asarray(lut, dtype=np.float64)
    points = []
    last_x = None
    for x in _CURVE_SAMPLE_XS:
        if x == last_x:
            continue
        y = int(np.clip(round(float(np.interp(x, np.arange(256), lut))), 0, 255))
        points.append((int(x), y))
        last_x = x
    return tuple(points)


# ------------------------------------------------------- product entry points


def curve_for_lut(lut: np.ndarray, weights: np.ndarray,
                  base_curve: CurveSettings) -> CurveSettings:
    """The fitted LUT into app curve settings - parametric first, points
    if that is not enough.

    Why the parametric 4 are the default: they show up on the sliders as
    they are, so the user can carry on adjusting from there, and measured
    in the research they were effectively equal to a 256-step LUT (10.9
    vs 10.6). But the parametric response has its amplitude tied to
    +-0.22 per region, so it cannot hold a sharp bend - quietly squashing
    it in at that point leaves the state 'matched, and yet it does not
    look alike', so we cross over to the point curve. Either way it is a
    value the curve editor shows as it is.

    base_curve's per-channel curves (the R/G/B points) are not owned by
    matching and pass straight through.
    """
    parametric = fit_parametric(lut, weights)
    if _parametric_error(parametric, lut, weights) <= PARAMETRIC_MAX_ERR:
        return replace(base_curve,
                       shadows=parametric[0], darks=parametric[1],
                       lights=parametric[2], highlights=parametric[3],
                       points_rgb=())
    return replace(base_curve,
                   shadows=0, darks=0, lights=0, highlights=0,
                   points_rgb=lut_to_curve_points(lut))


def _wb_log_ratios(mean_bgr: np.ndarray) -> tuple[float, float]:
    """Display-value channel means -> linear -> (log R/G, log B/G)."""
    from .engine import srgb_to_linear

    lin = srgb_to_linear(np.maximum(np.asarray(mean_bgr, np.float64), 1.0)
                         / 255.0)
    return float(np.log(lin[2] / lin[1])), float(np.log(lin[0] / lin[1]))


def _wb_means(render: np.ndarray, target: np.ndarray):
    """The (target, render) channel means to compare with.

    If there are neutral candidates we use those (they are not swayed by
    the light's colour), otherwise the overall mean - a coloured-light
    scene (an izakaya's LEDs and the like) has no neutrals at all, and it
    is precisely those frames that need this fit the most.
    """
    from .calibration import _neutral_means

    pair = _neutral_means(target, np.clip(render, 0, 255).astype(np.uint8))
    if pair is not None:
        return pair
    return (target.reshape(-1, 3).mean(axis=0),
            np.clip(render, 0, 255).reshape(-1, 3).mean(axis=0))


def _wb_gap(render: np.ndarray, target: np.ndarray) -> float:
    """The colour balance gap between two pictures - the absolute sum of
    log(R/G) and log(B/G)."""
    t_mean, r_mean = _wb_means(render, target)
    want, got = _wb_log_ratios(t_mean), _wb_log_ratios(r_mean)
    return abs(got[0] - want[0]) + abs(got[1] - want[1])


CHANNEL_POINTS = 8
"""Quantile sample count of the per-channel residual curve. It is for a
residual, so it is set coarse."""

CHANNEL_MAX_SHIFT = 12.0
"""The maximum number of levels a channel curve may move. It only stops
the quantile mapping from running away - the side where the colour gets
worse is caught by the score decision (match_settings)."""

CHANNEL_MIN_GAIN = 0.05
"""The minimum relative improvement required to adopt a channel curve.
The score here is measured on a small approximate render (approximate WB,
no optical correction), and a marginal gain can flip once it moves to the
real settled path (re-demosaic and optics included) - measured: P1032946,
a +2~3% gain internally, reversed into a -2% loss on the settled render.
Frames that genuinely benefit are at -21~-30% internally, a whole digit
clear of the 5% threshold."""


def _fit_channel_curve(render_ch: np.ndarray, target_ch: np.ndarray) -> tuple:
    """One channel's residual by quantile mapping - points in editor
    coordinates. () if there is none."""
    quantiles = np.linspace(0.03, 0.97, CHANNEL_POINTS)
    src = np.quantile(render_ch, quantiles)
    dst = np.clip(np.quantile(target_ch, quantiles),
                  src - CHANNEL_MAX_SHIFT, src + CHANNEL_MAX_SHIFT)
    xs = np.clip(np.round(src), 1, 254)
    ys = np.clip(np.round(dst), 0, 255)
    points, seen = [], set()
    for x, y in zip(xs, ys):
        if int(x) in seen:
            continue
        seen.add(int(x))
        points.append((int(x), int(y)))
    if not points or max(abs(y - x) for x, y in points) < 1.5:
        return ()                      # residual is rounding-level - identity
    return ((0, 0), *points, (255, 255))


def _apply_matched_wb(render: np.ndarray, kelvin: int, tint: int,
                      wb) -> np.ndarray:
    """Predict the picture (temperature, tint) really makes **once
    settled**.

    Once the sliders are set, the temperature is applied **in linear** by
    the re-demosaic (the anchor multipliers in raw_io.load_demosaiced),
    while the tint stays a display-value G multiply
    (engine._apply_white_balance). If the fit's verification and its
    pre-application use a space different from that combination - we
    first used the drag-time approximation (multiply everything in
    gamma), and the round-trip test caught it: a target with kelvin 3000
    applied came back as 2200 and tint -87. It is because reading a gain
    that was multiplied in gamma under a linear assumption inflates it by
    the power of 2.4.
    """
    from ..raw_io import _estimate_as_shot_kelvin
    from .engine import _kelvin_to_rgb, linear_to_srgb, srgb_to_linear

    camera = np.array(wb[0][:3], dtype=np.float64)
    daylight = np.array(wb[1][:3], dtype=np.float64)
    est = _estimate_as_shot_kelvin(tuple(camera), tuple(daylight))
    gain = _kelvin_to_rgb(float(est)) / _kelvin_to_rgb(float(kelvin))
    gain = gain / gain[1]

    linear = srgb_to_linear(np.clip(render, 0, 255).astype(np.float64) / 255.0)
    linear[..., 2] *= gain[0]                 # R
    linear[..., 0] *= gain[2]                 # B
    out = linear_to_srgb(np.clip(linear, 0.0, 1.0)) * 255.0
    if tint:
        out[..., 1] *= 1.0 - tint / 100.0 * 0.18
    return np.clip(out, 0, 255).astype(np.float32)


def _kelvin_working(working: np.ndarray, kelvin: int, wb) -> np.ndarray:
    """Apply the anchor kelvin gain to the working-space float up front
    (an approximation of settling).

    Settling applies the multipliers in sensor linear (before the colour
    matrix), while here the same multipliers are applied to the working
    linear after the matrix - at the small gains near the anchor (the
    estimated kelvin) the difference is small, and adoption is decided on
    the real render score anyway, so a bad approximation is simply
    rejected. The working space transfer function is the sRGB curve
    (Melissa).
    """
    from ..raw_io import _estimate_as_shot_kelvin
    from .engine import _kelvin_to_rgb, linear_to_srgb, srgb_to_linear

    camera = np.array(wb[0][:3], dtype=np.float64)
    daylight = np.array(wb[1][:3], dtype=np.float64)
    est = _estimate_as_shot_kelvin(tuple(camera), tuple(daylight))
    gain = _kelvin_to_rgb(float(est)) / _kelvin_to_rgb(float(kelvin))
    gain = gain / gain[1]

    linear = srgb_to_linear(np.clip(working, 0, 255).astype(np.float64)
                            / 255.0)
    linear[..., 2] *= gain[0]                 # R
    linear[..., 0] *= gain[2]                 # B
    return (linear_to_srgb(np.clip(linear, 0.0, 1.0))
            * 255.0).astype(np.float32)


def fit_white_balance(render: np.ndarray, target: np.ndarray,
                      wb) -> tuple[int, int] | None:
    """The (temperature, tint) that matches render's colour balance to
    target. None if it cannot be matched.

    Taking the channel ratios (R/G, B/G) as the goal, it solves back for
    the kelvin of the anchor model (camera x K(estimated)/K(t)) and, as
    the tint, the green-magenta component that is not on the kelvin axis.
    Because it is the anchor formula, the linear gain "temperature=t"
    gives the render is exactly K(estimated)/K(t), and on top of that
    prediction the two axes are solved as two variables.

    **It only answers when things improve.** The colour difference also
    has components off the kelvin and tint axes mixed in (the maker's
    colour rendering), so forcing a match shrinks one axis while growing
    the other - measured, the R/G of a Panasonic frame got worse, from
    2.0% to 4.9%. If the matched result does not shrink the colour
    balance gap by 10% or more it returns None, and the caller leaves the
    existing values alone.
    """
    from ..raw_io import _estimate_as_shot_kelvin
    from .engine import _kelvin_to_rgb, linear_to_srgb

    if wb is None:
        return None
    camera = np.array(wb[0][:3], dtype=np.float64)
    daylight = np.array(wb[1][:3], dtype=np.float64)
    if camera[1] <= 0 or daylight[1] <= 0:
        return None

    t_mean, r_mean = _wb_means(render, target)
    want = _wb_log_ratios(t_mean)
    got = _wb_log_ratios(r_mean)
    want_rg, want_bg = want[0] - got[0], want[1] - got[1]

    est = _estimate_as_shot_kelvin(tuple(camera), tuple(daylight))
    anchor = _kelvin_to_rgb(float(est))

    best = None
    for kelvin in range(2000, 12001, 25):
        gain = anchor / _kelvin_to_rgb(float(kelvin))
        model_rg = float(np.log(gain[0] / gain[1]))
        model_bg = float(np.log(gain[2] / gain[1]))
        # Tint (multiplies G only) is the (+d, +d) direction in the
        # (log R/G, log B/G) space
        delta = ((want_rg - model_rg) + (want_bg - model_bg)) / 2.0
        residual = ((want_rg - model_rg - delta) ** 2
                    + (want_bg - model_bg - delta) ** 2)
        if best is None or residual < best[0]:
            best = (residual, kelvin, delta)

    _, kelvin, delta = best
    # delta = the common linear G component. tint is defined as the
    # **display-value** G gain (1 - 0.18*tint/100), so it is converted
    # exactly at middle grey.
    grey = 0.18
    disp_gain = float(linear_to_srgb(np.float64(grey * np.exp(-delta)))
                      / linear_to_srgb(np.float64(grey)))
    tint = int(np.clip(round((1.0 - disp_gain) * 100.0 / 0.18), -100, 100))

    adjusted = _apply_matched_wb(render, int(kelvin), tint, wb)
    if _wb_gap(adjusted, target) >= _wb_gap(render, target) * 0.9:
        return None
    return int(kelvin), tint


def match_settings(
    render: np.ndarray,
    target: np.ndarray,
    base: DevelopSettings | None = None,
    wb=None,
    working: np.ndarray | None = None,
) -> DevelopSettings:
    """The DevelopSettings that bring the neutral develop render close to
    the embedded JPEG target.

    render is the 8-bit BGR of the develop window base (demosaic +
    profile), target is the load_preview result. Given base, it returns a
    copy of those settings with **only the temperature, tint, exposure,
    saturation and tone curve** changed - other edits such as detail,
    masks and crop are left alone (a one-click button must not wipe out
    existing edits).

    working is the **working-space float** of the same frame (the develop
    window's self._source). Given it, every fitting and verification
    render is run down the real screen path (applied in the working
    space, then converted with output_space="srgb"). Fitting and
    verifying on top of display values disagrees with the fact that the
    curve and saturation really apply in the wider working space -
    measured, the same settings make colours differing by up to 12% in
    R/G between the two spaces (the more saturated the frame, the
    larger). Without it, the fit is done on display values as before.

    wb is (camera_whitebalance, daylight_whitebalance). Given it, the
    colour balance is matched first (fit_white_balance) - exposure, curve
    and saturation only deal with brightness and the size of the chroma,
    so with the colour balance off you get "I pressed match and the
    colour is still different" (measured: under izakaya LEDs a gap of
    8.1% in R/G and 11.2% in B/G becomes 0.3% and 1.2% with the fit).
    Frames where the colour does not improve (dominated by the maker's
    colour rendering) are skipped automatically.

    Saturation is measured on the **real engine render**
    (apply_settings), not on a YCrCb approximation as in the research.
    The engine applies the curve per channel so the chroma moves along
    with it, which means the same saturation value gives a different
    result from the YCrCb approximation - it has to be measured on the
    path that will reach the screen for what you see to be what you get.
    """
    from ..raw_io import to_display
    from .engine import apply_settings

    base = base or DevelopSettings()
    if working is not None:
        # The real frame: the render we compare against is derived from
        # the working image too. (Effectively the same as the render that
        # was passed in, but coming out of one original is what makes it
        # impossible for them to disagree.)
        working_s = _small(np.clip(working, 0.0, 255.0).astype(np.float32))
        render_s = to_display(working_s)
        _, target_s = _pair(render_s, target)
    else:
        working_s = None
        render_s, target_s = _pair(render, target)

    def fit_tone(source: np.ndarray, source_working, tone_tint: int):
        """Fit exposure, curve and saturation on a source whose colour
        balance is already decided.

        With source_working present, the render uses the real screen path
        (applied in the working space, then converted to sRGB). tone_tint
        is the tint carried along in that render - the settled screen
        applies the tint in the engine as well.
        """
        def real(applied: DevelopSettings) -> np.ndarray:
            if source_working is None:
                return apply_settings(source, applied)
            return apply_settings(source_working, applied, output_space="srgb")

        fitted = fit_look(source, target_s)
        exposure = float(np.clip(round(fitted["exposure"], EXPOSURE_DECIMALS),
                                 -5.0, 5.0))
        weights = _weights(source, exposure)
        curve = curve_for_lut(fitted["lut"], weights, base.curve)

        # Saturation is measured on the real engine response after the
        # tone is settled. Why a bare settings object holding only tone
        # and saturation is used: let base's crop, masks or info bar in
        # and the small comparison image gets cut or drawn over, which
        # breaks the measurement itself.
        tone_only = DevelopSettings(
            basic=BasicSettings(exposure=exposure, tint=tone_tint),
            curve=CurveSettings(
                highlights=curve.highlights, lights=curve.lights,
                darks=curve.darks, shadows=curve.shadows,
                points_rgb=curve.points_rgb,
            ),
        )
        toned = real(tone_only)
        ratio = (_chroma(target_s) + 1e-6) / (_chroma(toned) + 1e-6)
        saturation = int(np.clip(round((ratio - 1.0) * 100.0), -100, 100))
        rendered = real(replace(tone_only,
                                basic=replace(tone_only.basic,
                                              saturation=saturation)))
        luma_err, chroma_err = score(rendered, target_s)
        return exposure, curve, saturation, luma_err + chroma_err, rendered

    # The colour balance is matched first, and exposure, curve and
    # saturation are measured on top of it. The real screen is in the
    # same order too (white balance -> tone). The pre-application uses
    # the same space as the real thing after settling - on a real frame
    # the kelvin gain is applied in the working linear (_kelvin_working)
    # and the tint is carried in the engine render, while the
    # display-value fallback uses _apply_matched_wb.
    #
    # **Adoption is decided on the final picture.** Going by the colour
    # balance metric alone there are frames where the neutrals improve
    # but the result with the curve and saturation laid on gets worse
    # (measured, Panasonic: the balance metric improves while the final
    # B/G goes 0.0% -> 4.6%). So both candidates (the fitted WB / the
    # current WB) are fitted all the way through and whichever real
    # engine render lands closer to the target is used - the same
    # principle as measuring saturation on the engine response.
    temperature = base.basic.temperature
    tint = base.basic.tint
    source, source_working = render_s, working_s
    cur_tint = tint if working_s is not None else 0
    exposure, curve, saturation, err, rendered = fit_tone(
        render_s, working_s, cur_tint)

    fitted_wb = fit_white_balance(render_s, target_s, wb)
    if fitted_wb is not None:
        if working_s is not None:
            cand_working = _kelvin_working(working_s, fitted_wb[0], wb)
            cand_display = to_display(cand_working)
            wb_fit = fit_tone(cand_display, cand_working, fitted_wb[1])
        else:
            cand_working = None
            cand_display = np.clip(
                _apply_matched_wb(render_s, fitted_wb[0], fitted_wb[1], wb),
                0, 255).astype(np.uint8)
            wb_fit = fit_tone(cand_display, None, 0)
        if wb_fit[3] < err:
            temperature, tint = fitted_wb
            exposure, curve, saturation, err, rendered = wb_fit
            source, source_working = cand_display, cand_working
            cur_tint = tint if working_s is not None else 0

    # The colour residual left over is narrowed once more with
    # **per-channel curves** - a per-channel quantile mapping between the
    # winning render and the target (it is a SIZE render, so the round
    # trip is in milliseconds). The research rejected per-channel curves
    # (saturation got worse), but back then there was neither a preceding
    # WB nor a score decision. Now they are adopted only when the score
    # of the real engine render improves, so the decision catches that
    # worry directly - measured: the izakaya LED frame's sum
    # 8.87 -> 7.02, the mixed-fluorescent frame 12.05 -> 9.52, and frames
    # with no improvement are rejected automatically.
    #
    # Repeating it twice was useless (the curve is replaced rather than
    # accumulated, so it always got worse).
    #
    # If base holds channel curves the user put there, it is not
    # attempted - the contract that channel curves are not owned by
    # matching (curve_for_lut) takes priority.
    if not (base.curve.points_red or base.curve.points_green
            or base.curve.points_blue):
        rendered_s, tgt_s = _pair(rendered, target_s)
        channelled = replace(
            curve,
            points_red=_fit_channel_curve(rendered_s[..., 2].ravel(),
                                          tgt_s[..., 2].ravel()),
            points_green=_fit_channel_curve(rendered_s[..., 1].ravel(),
                                            tgt_s[..., 1].ravel()),
            points_blue=_fit_channel_curve(rendered_s[..., 0].ravel(),
                                           tgt_s[..., 0].ravel()),
        )
        if channelled != curve:
            trial_settings = DevelopSettings(
                basic=BasicSettings(exposure=exposure, saturation=saturation,
                                    tint=cur_tint),
                curve=channelled)
            if source_working is None:
                trial = apply_settings(source, trial_settings)
            else:
                trial = apply_settings(source_working, trial_settings,
                                       output_space="srgb")
            if sum(score(trial, target_s)) < err * (1.0 - CHANNEL_MIN_GAIN):
                curve = channelled

    return replace(
        base,
        basic=replace(base.basic, exposure=exposure, saturation=saturation,
                      temperature=temperature, tint=tint),
        curve=curve,
    )
