"""Data model for the adjustment parameters.

It follows the panel layout of Lightroom / Camera Raw. Using names and
ranges the user already knows is easier to learn, and it also keeps the
mapping simple when we later export to XMP.

Most value ranges are -100~+100; only exposure is in EV.
Every dataclass has to be picklable and round-trip through a dict - the
preset files and the export workers both need that.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, fields, replace
from enum import Enum
from typing import Any

# The 8 colour bands the HSL / colour mix panel works with. The same set
# as Lightroom.
HSL_BANDS = ("red", "orange", "yellow", "green", "aqua", "blue", "purple", "magenta")
HSL_BAND_LABELS = {
    "red": "빨강", "orange": "주황", "yellow": "노랑", "green": "녹색",
    "aqua": "아쿠아", "blue": "파랑", "purple": "자주", "magenta": "마젠타",
}
# Centre hue of each colour band (OpenCV HSV basis, 0~179)
HSL_BAND_CENTERS = {
    "red": 0, "orange": 15, "yellow": 30, "green": 60,
    "aqua": 90, "blue": 120, "purple": 140, "magenta": 160,
}


def _as_dict(values: Any) -> dict[str, Any]:
    """A section value as a dict. An empty dict if it is not one.

    Presets are YAML the user can open and edit by hand. Turn one section
    into a string or a list by mistake and `dict(values)` blows up with
    "dictionary update sequence element..." while `values.get(...)` blows up
    with AttributeError. That exception stops the whole adjust panel from
    opening - one odd value and a file that will not open are entirely
    different events as far as the user is concerned.
    """
    return dict(values) if isinstance(values, dict) else {}


def _as_int(value: Any, default: int) -> int:
    """A value that will not read as a number falls back to the default.

    bool is a subtype of int and so passes straight through, which would
    make `opacity: true` mean 1. That is exactly the sort of mistake hand
    editing produces, so we filter it explicitly and use the default.
    """
    if isinstance(value, bool) or value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    # inf and NaN fall back to the default. The float side (_coerce_scalar)
    # already did this and only the integer side was missing it - `int(inf)`
    # raises **OverflowError**, not ValueError, so it slipped past the
    # except below and propagated straight up. Presets are hand-editable
    # YAML and a single line of `contrast: .inf` gets you there. Loading a
    # preset used to fail outright.
    if not math.isfinite(number):
        return default
    try:
        return int(number)
    except (TypeError, ValueError, OverflowError):
        return default


def _as_key_tuple(value: Any, allowed) -> tuple[str, ...]:
    """Tidy up a list of items, keeping only the keys we know.

    Given a single string (e.g. `include: camera`), iterating it as-is
    splits it letter by letter and silently yields an empty list. We treat
    it as one item.
    """
    if isinstance(value, str):
        value = (value,)
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    return tuple(k for k in value if k in allowed)


def _coerce_scalar(value: Any, default: Any) -> Any:
    """Coerce a value to the default's type. Falls back to the default.

    **A dataclass does not check types.** `sharpen_amount: "strong"` passes
    straight through the constructor, so loading succeeds quietly and then,
    tens of minutes later, export dies with a TypeError on every frame.
    That is the hardest shape of failure to trace back, so we block it at
    the point of entry.

    NaN and inf are put back to the default as well. Left alone they raise
    nothing and merely turn the pixels to garbage (arbitrary values out of
    the clip -> uint8 cast), which then stays in the result.
    """
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        return bool(value) if isinstance(value, (int, float)) else default
    if isinstance(default, int):
        return _as_int(value, default)
    if isinstance(default, float):
        if isinstance(value, bool) or value is None:
            return default
        try:
            result = float(value)
        except (TypeError, ValueError):
            return default
        return result if math.isfinite(result) else default
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    return value


def _merge_known(cls, values: Any, base: dict[str, Any]) -> Any:
    """Drop unknown keys; apply the rest coerced to the default's type.

    A preset saved by an older version has to open even when it holds fields
    that no longer exist. A hand-edited file can also hand us a section that
    is not a dict (a string, a list and so on), in which case we fall
    through to the defaults.

    For why the types are coerced here, see _coerce_scalar - the dataclass
    constructor does not check types, so anything we do not filter out
    survives intact and blows up in the render much later.
    """
    if not isinstance(values, dict):
        return cls(**base)

    valid = {f.name for f in fields(cls)}
    merged = dict(base)
    merged.update({
        k: _coerce_scalar(v, base[k])
        for k, v in values.items()
        if k in valid and k in base
    })
    try:
        return cls(**merged)
    except (TypeError, ValueError):
        # a shape _coerce_scalar could not filter out (a nested structure
        # and the like) means falling back to the defaults wholesale
        return cls(**base)


@dataclass(frozen=True)
class BasicSettings:
    """The Basic panel - white balance and tone."""

    temperature: int = 0      # absolute kelvin. 0 = "untouched" (as-shot)
    tint: int = 0             # -100 (green) ~ +100 (magenta)
    exposure: float = 0.0     # EV, -5 ~ +5

    brightness: int = 0
    """Midtone brightness (-100 ~ +100). Not the same as exposure.

    Exposure multiplies everything by 2^EV, so the highlights blow first.
    Brightness is a gamma, so it holds white and black in place and pushes
    only the midtones up - this is the right one when you want to rescue
    just the face of a backlit subject.
    """

    contrast: int = 0
    highlights: int = 0
    shadows: int = 0
    whites: int = 0
    blacks: int = 0
    texture: int = 0          # mid-frequency detail
    clarity: int = 0          # local contrast
    dehaze: int = 0
    vibrance: int = 0
    saturation: int = 0

    highlight_recovery: bool = False
    """Rebuild saturated highlights during demosaic (LibRaw blend).

    It applies **at decode time**, not through the tone LUT - where only one
    channel saturated on the sensor (stage LEDs and the like), the remaining
    channels bring it back. It means something only for RAW and is ignored
    for JPEG/HEIF. Measured (A6700 concert frames): 50~78 levels of
    difference on average in the highlights, with the internal structure of
    the LEDs recovered.

    Turn it on and **everything gets 1~1.5 stops darker** - LibRaw reserves
    headroom equal to the white balance gain, which lands as a uniform
    factor in linear space (measured: constant per file, spread ±0.1%).
    Exposure multiplies in linear too, so the user can take it back exactly
    with the exposure slider, and at that point the highlights roll off
    instead of clipping. That is the intended workflow for this method, so
    we do not correct for it behind the user's back - lifting it back
    silently would clip away the headroom just reserved and make the option
    pointless. Off by default.
    """


@dataclass(frozen=True)
class CurveSettings:
    """The Curve panel - parametric curve and per-channel point curves."""

    highlights: int = 0
    lights: int = 0
    darks: int = 0
    shadows: int = 0

    # (input, output) points. Empty means identity.
    points_rgb: tuple[tuple[int, int], ...] = ()
    points_red: tuple[tuple[int, int], ...] = ()
    points_green: tuple[tuple[int, int], ...] = ()
    points_blue: tuple[tuple[int, int], ...] = ()

    def is_neutral(self) -> bool:
        return (
            self.highlights == 0 and self.lights == 0
            and self.darks == 0 and self.shadows == 0
            and not (self.points_rgb or self.points_red
                     or self.points_green or self.points_blue)
        )


class NoiseAlgorithm(str, Enum):
    """The method used to remove luminance noise.

    Even at the same "noise reduction 50", the detail that survives and the
    time it takes differ greatly from method to method. The figures below
    were measured on a real R6 Mark III ISO 6400 file (2048² crop): the
    proportion of edge gradient left once flat-area noise was reduced to 50%
    of the original, and the processing time scaled to 32MP.
    """

    LEGACY = "legacy"
    """The old method. Detail retention 78.7%, 0.29 seconds.

    Used only when a photo that was already processed has to be reproduced
    exactly as before. The slider effectively does nothing (above 60, the
    difference from 100 is 0.05).
    """

    BILATERAL = "bilateral"
    """Bilateral filter. Detail retention 79.9%, 0.34 seconds.

    The fastest. For a light touch-up on a low-ISO photo with little noise.
    """

    NLMEANS = "nlmeans"
    """Non-local means (standard). Detail retention 99.4%, 0.95 seconds.

    It averages similar patterns from far apart, so it barely loses any
    edges. The default for high-ISO photos.
    """

    NLMEANS_HQ = "nlmeans_hq"
    """Non-local means, wide search window. 2.7x slower than standard.

    The name oversells it. The original 99.9% was measured at a *matched*
    reduction level; used at the same slider value it removes more fine
    noise than the standard window and pays for it heavily. Measured
    (2026-08-30, four files ISO 800~12800, strong-edge retention):

        strength 50:  standard 92~100%   HQ 85~99%
        strength 80:  standard 73~98%    HQ 50~97%

    It also leaves a **blotchier** residual - the coarse/fine ratio of what
    is left is higher than the standard window in nearly every cell, which
    is the "smeared" look rather than grain.

    Worth reaching for when you want the last of the fine grain gone and
    the frame has little fine detail to lose. The standard window is the
    better default.
    """


NOISE_ALGORITHM_LABELS = {
    NoiseAlgorithm.NLMEANS: "표준 (비국소 평균)",
    NoiseAlgorithm.NLMEANS_HQ: "고품질 (비국소 평균, 느림)",
    NoiseAlgorithm.BILATERAL: "빠름 (양방향 필터)",
    NoiseAlgorithm.LEGACY: "기존 방식 (구버전 재현용)",
}
"""Combo box display order and names. The recommended one goes first."""


@dataclass(frozen=True)
class DetailSettings:
    """The Detail panel - sharpening and noise reduction."""

    sharpen_amount: int = 0       # 0~150
    sharpen_radius: float = 1.0   # 0.5~3.0
    noise_reduction: int = 0      # 0~100 (luminance)
    color_noise_reduction: int = 0  # 0~100

    noise_algorithm: NoiseAlgorithm = NoiseAlgorithm.NLMEANS
    """Luminance noise removal method. See NoiseAlgorithm for measurements."""

    noise_passes: int = 2
    """Number of luminance noise reduction passes (1~4). Non-local means
    only.

    For the same amount of reduction, several weak passes damage detail far
    less than one strong pass - the smaller h is, the less the judgement
    "a difference this large is noise" touches a real edge.

    Measured (A6700 ISO3200, retention of strong edge gradient at equal
    flat-area noise reduction):

        reduce 70%:  1 pass 74% / 2 pass 97% / 3 pass 99% / 4 pass 99%
        reduce 80%:  1 pass unreachable / 2 pass 43% / 3 pass 68% /
                     4 pass 85%

    The stronger the reduction (concert shooting at ISO2000+), the more
    decisive the pass count is. A deep reduction like 80% cannot be reached
    at all in one pass. Time is proportional to the pass count (about 0.9
    seconds per pass at 32MP).
    A second, ground-truth measurement (A1 ISO 400 + synthetic noise
    matched to ISO 1600 lifted +2EV, through the real entry point) backs
    the panel's nudge past strength 70: from 75 up the second pass is a
    strict win, not a trade - PSNR equal or better *and* 9~12%p more
    edge retained (NR 75: 39.92dB/80.6% -> 39.91dB/92.1%; NR 100:
    39.57dB/75.6% -> 39.66dB/84.3%). At NR 50 it is still a trade
    (-0.6dB for +12%p edge), and a third pass was diminishing everywhere.

    A second sweep (2026-08-30, four files across ISO 800~12800, two
    strengths) showed the gain is **not** confined to strong reduction -
    strong-edge retention by pass count:

        strength 50:  1 pass 56~94%   2 pass 92~100%   4 pass 98~100%
        strength 80:  1 pass 36~84%   2 pass 73~98%    4 pass 82~99%

    A single pass costs 44%p of the edges even at a middling setting, so
    **2 is the default**. Files saved earlier carry an explicit 1 and go
    on rendering exactly as they did.
    """

    noise_detail: int = 50
    """Detail retention (0~100). How much of the original to bring back
    where there is texture.

    Noise reduction helps in flat areas and hurts where there is fine
    texture, such as hair or leaves. We blend the original back in only
    where the local contrast is clearly larger than the noise. 0 applies it
    everywhere; 100 leaves textured areas almost as they were.
    """

    color_noise_radius: int = 50
    """Colour noise radius (0~100). How large a colour blotch to look for.

    High-ISO colour noise is not per pixel but blotches tens of pixels
    across (measured: on the R6M3 at ISO6400, 54% of the colour noise is at
    a scale larger than 4 pixels). Raise it and it catches the large
    blotches too, but real colour edges bleed along with them.
    """

    color_noise_shadow: int = 100
    """Extra colour noise suppression in dark areas (0~100).

    Colour noise is especially bad in the dark - amplifying the shadows
    grows the colour blotches along with them. But sizing a uniform colour
    blur for the dark areas bleeds the real colour edges in the bright ones
    as well. So the dark areas only (luminance below 70, with a soft
    boundary) get a wider, stronger pass, and it is weighted in over the
    top of the colour noise slider rather than all at once at the bottom.

    Measured on the real shadows of five high-ISO files (ISO 8000, 5000,
    4000, 3200 and an A1 at 1600) - colour noise remaining in the shadow:

        colour noise slider    25    50    75   100
        this at 0              68%   46%   38%   25%
        this at 100            25%   15%   12%    9%

    The bright areas keep their colour throughout (97% of the original
    colour edge at the midpoint). What the top of the slider spends is
    colour in the dark - 85% at the midpoint down to 65% at the top - so
    it is there for people who would rather lose some shadow saturation
    than see the blotching. Turning the whole frame up instead bleeds
    colour edges everywhere (64.95 -> 35.58), which is the reason this is
    gated to the shadows at all.

    Like the radius, this is a helper: it does nothing while colour noise
    reduction is 0, which is why is_neutral ignores it. It defaults to on
    so that turning colour noise reduction on gets the shadows too.
    """

    destripe: int = 0
    """Remove horizontal LED wall striping (0~100).

    When an LED panel's PWM flicker and the rolling shutter readout fall out
    of step, horizontal banding is left behind. Both measured frames
    (DSC02751 ISO2500 1/800, DSC03868 ISO3200 1/1000) had **the same 103px
    period** - the same period at different ISO and different shutter means
    it comes from the readout cycle, not from the subject.

    The default is 0 (off). Striping only turns up at particular venues, and
    leaving it on all the time can disturb the subtle gradation of the sky
    in a landscape with a horizon in it.

    If no period is detected (outside 16~400px), raising the value does
    nothing at all.
    """

    face_priority: int = 85
    """Face priority (0~100). How much luminance noise reduction to take
    away outside faces.

    What grates at high ISO is usually **grain on skin**. Skin is smooth to
    begin with, so there is nothing to lose by erasing hard; but apply the
    same strength across the whole frame and the weave of clothing, hair
    and the audience lighting get mushed along with it.

    0 applies the same strength across the whole frame (the old behaviour);
    100 leaves everything outside faces completely untouched.

    Measured (DSC03360, A6700 ISO3200, RAW demosaic 6240x4168, noise
    reduction 70):

        face priority   skin noise   background detail   time
              0           -39%             -20%          1.37s
             50           -36%             -13%          1.33s
             85           -33%              -6%          1.32s
            100           -34%              -2%          0.75s

    The default of 85 means exactly what it says: 'mostly faces'. 100 is
    better for background detail and twice as fast (it does not compute
    outside the face boxes at all), but noise reduction in the background
    becomes **0**, so on a landscape that happens to catch one face in a
    corner, noise reduction disappears from the whole frame. At 85 the
    background still gets 15% of the strength even in that case.

    If no face is detected, this value is ignored entirely and the same
    strength is applied to the whole frame - the feature must not vanish
    just because no face was found.

    It is not applied to colour noise. Removing colour blotches barely hurts
    detail, so there is no reason to do it on faces alone, and colour
    blotches left only in the background stand out more.
    """

    def __post_init__(self) -> None:
        """Coerce the method to the enum even when it comes in as a string.

        Preset files store it as a string, and a file holding a value we do
        not know still has to open (the same reason as
        GeometrySettings.ratio).
        """
        if not isinstance(self.noise_algorithm, NoiseAlgorithm):
            try:
                object.__setattr__(
                    self, "noise_algorithm", NoiseAlgorithm(self.noise_algorithm)
                )
            except ValueError:
                object.__setattr__(self, "noise_algorithm", NoiseAlgorithm.NLMEANS)

    def is_neutral(self) -> bool:
        """Whether not one value actually touches the pixels.

        The method and the helper parameters (detail retention, colour
        radius) do nothing at all while the adjustment amount is 0. If a
        state where only those changed showed as 'has adjustments', the
        panel's ● marker and preset comparison would be lying.
        """
        return (
            self.sharpen_amount == 0
            and self.noise_reduction == 0
            and self.color_noise_reduction == 0
            and self.destripe == 0
        )


@dataclass(frozen=True)
class HSLBand:
    hue: int = 0
    saturation: int = 0
    luminance: int = 0

    def is_neutral(self) -> bool:
        return self.hue == 0 and self.saturation == 0 and self.luminance == 0


@dataclass(frozen=True)
class HSLSettings:
    """The Colour Mix panel - hue/saturation/luminance per colour band."""

    bands: dict[str, HSLBand] = field(
        default_factory=lambda: {name: HSLBand() for name in HSL_BANDS}
    )

    def __post_init__(self) -> None:
        """Always keep all 8 bands filled in.

        Allow construction with only some of them specified and settings
        that mean the same thing become different objects, which throws off
        comparison and the preset round trip.
        """
        normalized = {name: HSLBand() for name in HSL_BANDS}
        normalized.update(
            {k: v for k, v in (self.bands or {}).items() if k in HSL_BANDS}
        )
        object.__setattr__(self, "bands", normalized)

    def is_neutral(self) -> bool:
        return all(band.is_neutral() for band in self.bands.values())

    def to_dict(self) -> dict:
        return {name: asdict(band) for name, band in self.bands.items()}

    @classmethod
    def from_dict(cls, data: dict) -> "HSLSettings":
        bands = {name: HSLBand() for name in HSL_BANDS}
        for name, values in _as_dict(data).items():
            if name in bands and isinstance(values, dict):
                bands[name] = _merge_known(HSLBand, values, asdict(HSLBand()))
        return cls(bands=bands)


@dataclass(frozen=True)
class ColorGradeZone:
    """One zone of colour grading (shadow/midtone/highlight region)."""

    hue: int = 0          # 0~359
    saturation: int = 0   # 0~100
    luminance: int = 0    # -100~100

    def is_neutral(self) -> bool:
        return self.saturation == 0 and self.luminance == 0


@dataclass(frozen=True)
class ColorGradeSettings:
    """The Colour Grading panel - colour grading per zone."""

    shadows: ColorGradeZone = field(default_factory=ColorGradeZone)
    midtones: ColorGradeZone = field(default_factory=ColorGradeZone)
    highlights: ColorGradeZone = field(default_factory=ColorGradeZone)
    blending: int = 50
    balance: int = 0

    def is_neutral(self) -> bool:
        return (
            self.shadows.is_neutral()
            and self.midtones.is_neutral()
            and self.highlights.is_neutral()
        )


@dataclass(frozen=True)
class EffectSettings:
    """The Effects panel - grain and vignetting."""

    grain_amount: int = 0    # 0~100
    grain_size: int = 25     # 1~100
    vignette_amount: int = 0  # -100 (darker) ~ +100 (brighter)
    vignette_midpoint: int = 50


class CropRatio(str, Enum):
    FREE = "free"
    ORIGINAL = "original"
    SQUARE = "1:1"
    FOUR_THREE = "4:3"
    THREE_TWO = "3:2"
    SIXTEEN_NINE = "16:9"

    @property
    def value_ratio(self) -> float | None:
        """Width/height ratio. FREE and ORIGINAL are decided when computed."""
        return {
            CropRatio.SQUARE: 1.0,
            CropRatio.FOUR_THREE: 4 / 3,
            CropRatio.THREE_TWO: 3 / 2,
            CropRatio.SIXTEEN_NINE: 16 / 9,
        }.get(self)


@dataclass(frozen=True)
class GeometrySettings:
    """The Geometry panel - crop, straightening, rotation.

    The crop is stored in normalised 0~1 coordinates, because a value set on
    the preview (a downscaled copy) has to hold at the original resolution
    just the same.
    """

    crop_left: float = 0.0
    crop_top: float = 0.0
    crop_right: float = 1.0
    crop_bottom: float = 1.0
    straighten: float = 0.0    # in degrees, -45 ~ +45
    rotate_quarters: int = 0   # rotation in 90° steps (0~3)
    flip_horizontal: bool = False
    flip_vertical: bool = False
    ratio: CropRatio = CropRatio.FREE

    def __post_init__(self) -> None:
        """Coerce ratio to the enum even when it comes in as a string.

        When PySide6 stores an Enum that inherits from str as combo box
        data, it turns it into a plain str. Absorb that here or accessing
        .value blows up.
        """
        if not isinstance(self.ratio, CropRatio):
            try:
                object.__setattr__(self, "ratio", CropRatio(self.ratio))
            except ValueError:
                object.__setattr__(self, "ratio", CropRatio.FREE)

    def has_crop(self) -> bool:
        return (
            self.crop_left > 0.0 or self.crop_top > 0.0
            or self.crop_right < 1.0 or self.crop_bottom < 1.0
        )

    def is_neutral(self) -> bool:
        return (
            not self.has_crop()
            and self.straighten == 0.0
            and self.rotate_quarters == 0
            and not self.flip_horizontal
            and not self.flip_vertical
        )


class WatermarkPosition(str, Enum):
    """The 3x3 alignment positions. Fine placement is done with offset."""

    TOP_LEFT = "top_left"
    TOP_CENTER = "top_center"
    TOP_RIGHT = "top_right"
    MIDDLE_LEFT = "middle_left"
    CENTER = "center"
    MIDDLE_RIGHT = "middle_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_CENTER = "bottom_center"
    BOTTOM_RIGHT = "bottom_right"

    @property
    def anchor(self) -> tuple[float, float]:
        """(horizontal, vertical) alignment ratio. 0=left/top, 0.5=centre,
        1=right/bottom.
        """
        horizontal = {"left": 0.0, "center": 0.5, "right": 1.0}
        vertical = {"top": 0.0, "middle": 0.5, "bottom": 1.0}
        if self is WatermarkPosition.CENTER:
            return 0.5, 0.5
        parts = self.value.split("_")
        return horizontal[parts[1]], vertical[parts[0]]


@dataclass(frozen=True)
class WatermarkSettings:
    """Watermark - text or image."""

    enabled: bool = False
    text: str = ""
    image_path: str = ""
    position: WatermarkPosition = WatermarkPosition.BOTTOM_RIGHT
    opacity: int = 70          # 0~100
    scale: int = 5             # % of the image's long edge
    margin: int = 3            # margin %

    offset_x: float = 0.0
    """Horizontal fine adjustment (% of image width). Positive is right."""

    offset_y: float = 0.0
    """Vertical fine adjustment (% of image height). Positive is down."""

    rotation: int = 0
    """Watermark rotation (degrees). For a diagonal placement."""

    font_path: str = ""
    """Path to the watermark font file. Empty means the default font.

    We store the file path rather than the font 'name'. PIL does the
    rendering and PIL has to open the file directly, and the name -> file
    mapping differs per OS and breaks easily.
    """

    color: tuple[int, int, int] = (255, 255, 255)
    shadow: bool = True        # so it reads on a bright background too

    def __post_init__(self) -> None:
        """Coerce position to the enum from a string (as GeometrySettings)."""
        if not isinstance(self.position, WatermarkPosition):
            try:
                object.__setattr__(self, "position", WatermarkPosition(self.position))
            except ValueError:
                object.__setattr__(self, "position", WatermarkPosition.BOTTOM_RIGHT)

    def is_active(self) -> bool:
        return self.enabled and bool(self.text or self.image_path)


# EXIF items that can be embedded on export. The key is the internal name,
# the value is the display name.
EXIF_FIELDS = {
    "camera": "카메라 (제조사/모델)",
    "lens": "렌즈",
    "exposure": "노출 (셔터/조리개/ISO)",
    "focal_length": "초점거리",
    "datetime": "촬영 일시",
    "artist": "작가",
    "copyright": "저작권",
    "software": "소프트웨어",
}


@dataclass(frozen=True)
class MetadataSettings:
    """EXIF embedding - only the selected items go out.

    Everything off by default. People often do not want the gear they shot
    with or the time they shot at tagging along when a photo leaves, so
    including it is left as an explicit choice.
    """

    enabled: bool = False
    include: tuple[str, ...] = ()
    artist: str = ""
    copyright: str = ""

    def wants(self, key: str) -> bool:
        return self.enabled and key in self.include


@dataclass(frozen=True)
class OpticsSettings:
    """The Optics panel - lens distortion, vignetting, chromatic aberration.

    Automatic uses the lensfun DB profiles, manual is direct adjustment.
    Lenses missing from the DB are common (measured: the Tamron A069 is not
    registered), so we need both.
    """

    auto_enabled: bool = False
    auto_distortion: bool = True
    auto_vignetting: bool = True
    auto_chromatic: bool = True

    lens_override: str = ""
    """A lens name the user picked by hand.

    The automatic lookup fails when the EXIF lens name is empty (an adapter
    was used) or differs from the database name. It has to be possible to
    specify it directly in that case.
    """

    distortion: int = 0
    manual_vignetting: int = 0
    defringe_purple: int = 0
    defringe_green: int = 0

    defringe_purple_hue: int = 145
    """Centre hue treated as purple fringing. Set with the eyedropper."""

    defringe_green_hue: int = 65
    """Centre hue treated as green fringing."""

    def is_neutral(self) -> bool:
        return (
            not self.auto_enabled
            and self.distortion == 0
            and self.manual_vignetting == 0
            and self.defringe_purple == 0
            and self.defringe_green == 0
        )

@dataclass(frozen=True)
class ExifStripSettings:
    """The information strip along the bottom of the image.

    EXIF is mostly stripped once a photo is posted to social media. Burned
    in as visible text, it stays wherever the photo goes.
    """

    enabled: bool = False
    dark_background: bool = True
    include: tuple[str, ...] = (
        "camera", "lens", "focal_length", "aperture", "shutter", "iso",
    )
    height_percent: float = 6.0
    custom_text: str = ""

    def is_active(self) -> bool:
        return self.enabled and bool(self.include or self.custom_text)


# Items that can go in the strip
STRIP_FIELDS = {
    "filename": "파일명",
    "camera": "카메라",
    "lens": "렌즈",
    "focal_length": "초점거리",
    "aperture": "조리개",
    "shutter": "셔터",
    "iso": "ISO",
    "datetime": "촬영 일시",
}


# ---------------------------------------------------------------- masks (local)


class MaskCombine(str, Enum):
    """How a refinement piece changes the mask area (the same three as
    Lightroom).

    The alpha is soft (feathered), so the set operations are defined softly
    too:
      ADD        max(a, b)      - widens
      SUBTRACT   a x (1 - b)    - takes away
      INTERSECT  a x b          - keeps only the overlap

    Why the product: where two alphas half overlap at a boundary, cutting
    with min/max leaves a stair step. The product bridges the gap between
    them. ADD is the only max, so that the overlapping interior does not go
    past 1 and clump (a+b-ab has the same property, but leaving flat areas
    as they are makes the result easier to predict).
    """

    ADD = "add"
    SUBTRACT = "subtract"
    INTERSECT = "intersect"


class MaskType(str, Enum):
    """Mask kinds. Face/eye/background are rebuilt from the image each time."""

    BRUSH = "brush"          # hand-painted alpha bitmap
    RADIAL = "radial"        # elliptical gradient
    LINEAR = "linear"        # linear gradient
    FACE = "face"            # face detection (skin/mouth)
    EYE = "eye"              # from face landmarks (under-eye/iris)
    BACKGROUND = "background"  # background excluding people (GrabCut)
    SUBJECT = "subject"      # main subject (U²-Netp trained model)


@dataclass(frozen=True)
class LocalAdjustments:
    """Adjustments applied only inside a mask. A subset of BasicSettings
    plus local-only ones.

    Colour temperature is a relative shift (-100~+100, positive is warmer),
    not an absolute Kelvin. For a local adjustment, 'how far to push it
    against the background' is the natural framing, so it is handled
    differently from the global one.
    """

    exposure: float = 0.0      # EV
    contrast: int = 0
    highlights: int = 0
    shadows: int = 0
    whites: int = 0
    blacks: int = 0
    temperature: int = 0       # relative shift (-100 cooler ~ +100 warmer)
    tint: int = 0
    texture: int = 0
    clarity: int = 0
    saturation: int = 0
    sharpen: int = 0           # 0~150
    smoothing: int = 0         # skin smoothing (0~100), surface blur
    curve: CurveSettings = field(default_factory=CurveSettings)
    """A tone curve applied only inside the mask (advanced).

    The same editor and the same values as the global curve. It is for
    gradation the sliders cannot produce - holding down just the sky, or
    pulling only the subject towards a film tone. It stays collapsed in the
    UI by default - leaving a curve editor open per mask would bury the
    panel in curves.
    """

    def is_neutral(self) -> bool:
        return (all(getattr(self, f.name) == 0 for f in fields(self)
                    if f.name != "curve")
                and self.curve.is_neutral())


@dataclass(frozen=True)
class Mask:
    """One mask = an area definition + the local adjustment for that area.

    Face/eye/background/radial/linear store only params (normalised
    coordinates) and are rebuilt at render time - the same position comes
    out at any resolution. Only brush carries a bitmap directly (a
    downscaled alpha PNG as base64).
    """

    kind: MaskType
    adjust: LocalAdjustments = field(default_factory=LocalAdjustments)
    enabled: bool = True
    invert: bool = False
    opacity: int = 100         # 0~100
    feather: int = 50          # edge softness 0~100
    size: int = 100
    """Detected area size (%, 0~200). 100 is the default.

    Applies only to areas built from shapes, such as face/eye/radial. Parts
    that have to be taken narrowly, like the under-eye, need adjusting
    because the right range differs from person to person.
    """
    params: dict[str, Any] = field(default_factory=dict)
    """Normalised parameters per kind.
      RADIAL:  cx, cy, rx, ry, rotation
      LINEAR:  x0, y0, x1, y1
      FACE:    index (which face), region("skin"|"mouth")
      EYE:     index, region("under_eye"|"iris")
    """
    bitmap: str = ""           # BRUSH only, base64 PNG (1 channel, ≈512px)
    label: str = ""            # the name shown to the user
    combine: MaskCombine = MaskCombine.ADD
    """Meaningful **only when used as a refinement piece** - whether to
    widen the parent area (ADD), take away from it (SUBTRACT) or keep only
    the overlap (INTERSECT).

    It means nothing for a mask in the list itself. Masks relate to one
    another by laying their own adjustments on in order, not by combining
    areas."""
    refine: tuple["Mask", ...] = ()
    """The pieces that refine this mask's area. combine is applied in order.

    **Only the parent's adjust is used** - a piece defines an area and
    nothing else. "Smooth the face but leave out the eyes" being one set of
    adjustments rather than two is the natural reading, and Lightroom uses
    the same model.

    The depth is one level (a piece's refine is ignored). Allowing nesting
    would leave no way to express it in the UI, and every combination that
    is actually needed can be built at one level.
    """

    def __post_init__(self) -> None:
        if not isinstance(self.kind, MaskType):
            try:
                object.__setattr__(self, "kind", MaskType(self.kind))
            except ValueError:
                object.__setattr__(self, "kind", MaskType.RADIAL)
        if not isinstance(self.combine, MaskCombine):
            try:
                object.__setattr__(self, "combine", MaskCombine(self.combine))
            except ValueError:
                object.__setattr__(self, "combine", MaskCombine.ADD)
        if not isinstance(self.refine, tuple):
            object.__setattr__(self, "refine", tuple(self.refine or ()))

    def is_neutral(self) -> bool:
        return not self.enabled or self.opacity <= 0 or self.adjust.is_neutral()

    def to_dict(self) -> dict:
        data = {
            "kind": self.kind.value,
            "adjust": _local_to_dict(self.adjust),
            "enabled": self.enabled,
            "invert": self.invert,
            "opacity": self.opacity,
            "feather": self.feather,
            "size": self.size,
            "params": dict(self.params),
            "bitmap": self.bitmap,
            "label": self.label,
        }
        # Most frames do not use it, so at the default value we leave the
        # key out - an older version still reads it (unknown keys ignored)
        # and the file does not grow.
        if self.combine is not MaskCombine.ADD:
            data["combine"] = self.combine.value
        if self.refine:
            data["refine"] = [piece.to_dict() for piece in self.refine]
        return data

    @classmethod
    def from_dict(cls, data: Any) -> "Mask | None":
        if not isinstance(data, dict) or "kind" not in data:
            return None
        try:
            kind = MaskType(data["kind"])
        except ValueError:
            return None
        adjust = _local_from_dict(data.get("adjust"))
        params = data.get("params")
        try:
            combine = MaskCombine(data.get("combine", MaskCombine.ADD.value))
        except ValueError:
            combine = MaskCombine.ADD
        # Refinement pieces are read **one level only**. Cutting nesting off
        # here means the render never has to worry about depth, even if a
        # nested refine comes in.
        refine = []
        for item in data.get("refine") or ():
            piece = cls.from_dict(item)
            if piece is not None:
                refine.append(replace(piece, refine=()))
        # Every number is read leniently. An int() blowing up here would
        # stop not just this mask but the whole preset (and the queue) from
        # opening.
        return cls(
            kind=kind,
            adjust=adjust,
            enabled=bool(data.get("enabled", True)),
            invert=bool(data.get("invert", False)),
            opacity=_as_int(data.get("opacity"), 100),
            feather=_as_int(data.get("feather"), 50),
            size=_as_int(data.get("size"), 100),
            params=dict(params) if isinstance(params, dict) else {},
            bitmap=str(data.get("bitmap", "")),
            label=str(data.get("label", "")),
            combine=combine,
            refine=tuple(refine),
        )


@dataclass(frozen=True)
class DevelopSettings:
    """The complete set of adjustment settings."""

    basic: BasicSettings = field(default_factory=BasicSettings)
    curve: CurveSettings = field(default_factory=CurveSettings)
    detail: DetailSettings = field(default_factory=DetailSettings)
    hsl: HSLSettings = field(default_factory=HSLSettings)
    color_grade: ColorGradeSettings = field(default_factory=ColorGradeSettings)
    effects: EffectSettings = field(default_factory=EffectSettings)
    optics: OpticsSettings = field(default_factory=OpticsSettings)
    geometry: GeometrySettings = field(default_factory=GeometrySettings)
    watermark: WatermarkSettings = field(default_factory=WatermarkSettings)
    metadata: MetadataSettings = field(default_factory=MetadataSettings)
    exif_strip: ExifStripSettings = field(default_factory=ExifStripSettings)
    masks: tuple[Mask, ...] = ()
    """Local adjustment masks. They differ from frame to frame (much like the
    crop), so they are excluded from batch apply."""

    def is_neutral(self) -> bool:
        """Whether nothing at all has been changed.

        Watermark, metadata and the info strip are not pixel operations but
        they do affect the output, so they are checked alongside the rest.
        """
        return (
            self.basic == BasicSettings()
            and self.curve.is_neutral()
            and self.detail.is_neutral()
            and self.hsl.is_neutral()
            and self.color_grade.is_neutral()
            and self.effects == EffectSettings()
            and self.optics.is_neutral()
            and self.geometry.is_neutral()
            and not self.watermark.is_active()
            and not self.metadata.enabled
            and not self.exif_strip.is_active()
            and all(m.is_neutral() for m in self.masks)
        )

    # ------------------------------------------------------------ serialise

    def to_dict(self) -> dict:
        return {
            "basic": asdict(self.basic),
            "curve": _curve_to_dict(self.curve),
            # Presets are saved as YAML. safe_dump cannot represent an Enum
            # (the same reason as CropRatio/WatermarkPosition), so we unwrap
            # it to a string.
            "detail": {
                **asdict(self.detail),
                "noise_algorithm": self.detail.noise_algorithm.value,
            },
            "hsl": self.hsl.to_dict(),
            "color_grade": {
                "shadows": asdict(self.color_grade.shadows),
                "midtones": asdict(self.color_grade.midtones),
                "highlights": asdict(self.color_grade.highlights),
                "blending": self.color_grade.blending,
                "balance": self.color_grade.balance,
            },
            "effects": asdict(self.effects),
            "optics": asdict(self.optics),
            "geometry": {**asdict(self.geometry), "ratio": self.geometry.ratio.value},
            "watermark": {
                **asdict(self.watermark),
                "position": self.watermark.position.value,
                "color": list(self.watermark.color),
            },
            "metadata": {
                **asdict(self.metadata),
                "include": list(self.metadata.include),
            },
            "exif_strip": {
                **asdict(self.exif_strip),
                "include": list(self.exif_strip.include),
            },
            "masks": [m.to_dict() for m in self.masks],
        }

    def without_geometry(self) -> "DevelopSettings":
        """A copy without geometry (crop, straighten, rotate) or masks. Used
        for batch apply.

        The crop must not be applied in batch, because the composition
        differs from frame to frame. Put a crop taken on one frame onto
        another and the subject gets cut away. Masks are the same: they were
        built to fit that frame's faces and composition, so they must not be
        shared. They are of a different nature from colour work, which is
        fine to share across everything.
        """
        from dataclasses import replace

        return replace(self, geometry=GeometrySettings(), masks=())

    def for_preset(self) -> "DevelopSettings":
        """A copy with the values an adjustment preset does not carry taken
        out.

        **Geometry is dropped wholesale** - the same reason as batch apply
        (without_geometry above). Crop, straightening and rotation are
        values fitted to that frame's composition and horizon, so putting
        them on another photo does not fit anything; it cuts the subject
        away and tilts it by that much. The same goes for masks.

        **Watermarks** are stored separately (presets.watermark_presets).
        They are of a different nature from colour work, and putting the
        same watermark on several looks, or different watermarks on the same
        look, is common - bundle them into one lump and you have to make a
        preset per combination.
        """
        from dataclasses import replace

        return replace(self.without_geometry(), watermark=WatermarkSettings())

    def with_preset(self, preset: "DevelopSettings") -> "DevelopSettings":
        """Lay a preset over the current values, keeping what it does not
        carry.

        The things for_preset takes out (geometry, masks, watermark) have to
        survive loading a preset with the current frame's values intact.
        Swap the whole thing out and every time you pick a preset the crop
        you set comes undone and the watermark disappears.
        """
        from dataclasses import replace

        return replace(
            preset,
            geometry=self.geometry,
            watermark=self.watermark,
            masks=self.masks,
        )

    @classmethod
    def from_dict(cls, data: Any) -> "DevelopSettings":
        """Unknown keys are ignored. Older presets have to open too.

        A damaged file handing us something that is not a dict falls through
        to the defaults.
        """
        if not isinstance(data, dict):
            data = {}
        return cls(
            basic=_merge_known(BasicSettings, data.get("basic"), asdict(BasicSettings())),
            curve=_curve_from_dict(data.get("curve")),
            detail=_detail_from_dict(data.get("detail")),
            hsl=HSLSettings.from_dict(data.get("hsl")),
            color_grade=_color_grade_from_dict(data.get("color_grade")),
            effects=_merge_known(EffectSettings, data.get("effects"), asdict(EffectSettings())),
            optics=_merge_known(OpticsSettings, data.get("optics"), asdict(OpticsSettings())),
            geometry=_geometry_from_dict(data.get("geometry")),
            watermark=_watermark_from_dict(data.get("watermark")),
            metadata=_metadata_from_dict(data.get("metadata")),
            exif_strip=_exif_strip_from_dict(data.get("exif_strip")),
            masks=_masks_from_dict(data.get("masks")),
        )


def _local_to_dict(adjust: LocalAdjustments) -> dict:
    """Serialise LocalAdjustments. asdict cannot be used because there is a
    curve inside - asdict leaves points as tuples rather than turning them
    into lists, and YAML safe_dump cannot represent a Python tuple, so
    saving the preset blows up."""
    data = {f.name: getattr(adjust, f.name) for f in fields(adjust)
            if f.name != "curve"}
    if not adjust.curve.is_neutral():
        # Most frames do not use it, so when neutral we leave the key out
        data["curve"] = _curve_to_dict(adjust.curve)
    return data


def _local_from_dict(data: Any) -> LocalAdjustments:
    """Restore LocalAdjustments - the curve separately, the rest leniently.

    _merge_known cannot be used because it only handles scalars. The policy
    of dropping unknown keys and coercing to the default's type is the same.
    """
    defaults = LocalAdjustments()
    if not isinstance(data, dict):
        return defaults
    values = {}
    for field_ in fields(LocalAdjustments):
        if field_.name == "curve" or field_.name not in data:
            continue
        values[field_.name] = _coerce_scalar(data[field_.name],
                                             getattr(defaults, field_.name))
    return LocalAdjustments(curve=_curve_from_dict(data.get("curve")),
                            **values)


def _curve_to_dict(curve: CurveSettings) -> dict:
    return {
        "highlights": curve.highlights,
        "lights": curve.lights,
        "darks": curve.darks,
        "shadows": curve.shadows,
        "points_rgb": [list(p) for p in curve.points_rgb],
        "points_red": [list(p) for p in curve.points_red],
        "points_green": [list(p) for p in curve.points_green],
        "points_blue": [list(p) for p in curve.points_blue],
    }


def _curve_from_dict(data: dict | None) -> CurveSettings:
    data = _as_dict(data)

    def points(key: str) -> tuple[tuple[int, int], ...]:
        raw = data.get(key) or []
        try:
            return tuple((int(a), int(b)) for a, b in raw)
        except (TypeError, ValueError):
            return ()

    return CurveSettings(
        highlights=_as_int(data.get("highlights"), 0),
        lights=_as_int(data.get("lights"), 0),
        darks=_as_int(data.get("darks"), 0),
        shadows=_as_int(data.get("shadows"), 0),
        points_rgb=points("points_rgb"),
        points_red=points("points_red"),
        points_green=points("points_green"),
        points_blue=points("points_blue"),
    )


def _detail_from_dict(data: dict | None) -> DetailSettings:
    """Restore the Detail settings.

    An older preset with no noise reduction method opens with the default
    (non-local means). To reproduce an older result exactly, switch the
    method to the legacy one.
    """
    data = _as_dict(data)
    algorithm = data.pop("noise_algorithm", NoiseAlgorithm.NLMEANS)
    merged = _merge_known(DetailSettings, data, asdict(DetailSettings()))
    try:
        resolved = NoiseAlgorithm(algorithm)
    except ValueError:
        resolved = NoiseAlgorithm.NLMEANS
    return DetailSettings(**{**asdict(merged), "noise_algorithm": resolved})


def _color_grade_from_dict(data: dict | None) -> ColorGradeSettings:
    data = _as_dict(data)
    base = asdict(ColorGradeZone())
    return ColorGradeSettings(
        shadows=_merge_known(ColorGradeZone, data.get("shadows"), base),
        midtones=_merge_known(ColorGradeZone, data.get("midtones"), base),
        highlights=_merge_known(ColorGradeZone, data.get("highlights"), base),
        blending=_as_int(data.get("blending"), 50),
        balance=_as_int(data.get("balance"), 0),
    )


def _geometry_from_dict(data: dict | None) -> GeometrySettings:
    data = _as_dict(data)
    ratio = data.pop("ratio", CropRatio.FREE.value)
    merged = _merge_known(GeometrySettings, data, asdict(GeometrySettings()))
    try:
        return GeometrySettings(**{**asdict(merged), "ratio": CropRatio(ratio)})
    except ValueError:
        return merged


_DEFAULT_WATERMARK_COLOR = (255, 255, 255)


def _as_color(value: Any) -> tuple[int, int, int]:
    """Always coerce the watermark colour to three channels.

    cv2.putText blows up **at draw time** if the channel count does not
    match. Let it pass quietly at load time and the user sees the failure
    around the time a batch that took tens of minutes finishes.
    """
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return _DEFAULT_WATERMARK_COLOR
    channels = []
    for component in value[:3]:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            return _DEFAULT_WATERMARK_COLOR
        channels.append(int(min(255, max(0, component))))
    return (channels[0], channels[1], channels[2])


def _watermark_from_dict(data: dict | None) -> WatermarkSettings:
    data = _as_dict(data)
    position = data.pop("position", WatermarkPosition.BOTTOM_RIGHT.value)
    color = data.pop("color", _DEFAULT_WATERMARK_COLOR)
    merged = _merge_known(WatermarkSettings, data, asdict(WatermarkSettings()))
    try:
        resolved = WatermarkPosition(position)
    except (ValueError, TypeError):
        resolved = WatermarkPosition.BOTTOM_RIGHT
    return WatermarkSettings(
        **{
            **asdict(merged),
            "position": resolved,
            "color": _as_color(color),
        }
    )


def _exif_strip_from_dict(data: dict | None) -> ExifStripSettings:
    data = _as_dict(data)
    include = data.pop("include", None)
    merged = _merge_known(ExifStripSettings, data, asdict(ExifStripSettings()))
    if include is None:
        return merged
    return ExifStripSettings(
        **{**asdict(merged), "include": _as_key_tuple(include, STRIP_FIELDS)}
    )


def _masks_from_dict(data: Any) -> tuple[Mask, ...]:
    """Restore the mask list. Broken entries are skipped silently."""
    if not isinstance(data, (list, tuple)):
        return ()
    masks = []
    for item in data:
        mask = Mask.from_dict(item)
        if mask is not None:
            masks.append(mask)
    return tuple(masks)


def _metadata_from_dict(data: dict | None) -> MetadataSettings:
    data = _as_dict(data)
    include = data.pop("include", ())
    merged = _merge_known(MetadataSettings, data, asdict(MetadataSettings()))
    return MetadataSettings(
        **{**asdict(merged), "include": _as_key_tuple(include, EXIF_FIELDS)}
    )
