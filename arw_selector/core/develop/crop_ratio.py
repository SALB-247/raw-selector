"""The crop ratio, as arithmetic on the normalised crop rectangle.

A crop is (left, top, right, bottom) in 0~1 of the frame it is applied
to - the frame **after** the quarter-turn rotation, since that is the
order the engine applies geometry in (rotate -> flip -> straighten ->
crop). A ratio is width / height in pixels of that frame, so a 1:1 crop
on a 3:2 frame is 2/3 wide and full height in normalised terms; every
function here takes the frame's own width/height ratio to convert.

Everything that touches the ratio goes through here - the handles on the
picture, the ratio combo, entering and leaving crop mode, a quarter turn,
a crop slider, a saved file - so that one set of tests can hold the whole
of it. The ratio crop was broken three times over before this module:
laid out afresh on entering and leaving crop mode, fitted against a
screen frame that still carried the previous crop and the info strip, and
pushed off its ratio at the picture's edge by a drag.
"""
from __future__ import annotations

Crop = tuple[float, float, float, float]

FULL: Crop = (0.0, 0.0, 1.0, 1.0)
MIN_SIDE = 0.05
"""No side of a crop is shorter than this share of the frame."""
TOLERANCE = 0.02
"""A crop within this relative distance of the ratio counts as having it -
the sliders round to a hundredth, and the engine rounds to whole pixels."""


def frame_ratio(width: int, height: int, rotate_quarters: int = 0) -> float:
    """The frame's width / height once the quarter turns are applied - the
    frame a crop is measured against."""
    if height <= 0 or width <= 0:
        return 1.0
    if rotate_quarters % 2:
        width, height = height, width
    return width / height


def pixel_ratio(crop: Crop, frame: float) -> float | None:
    """The crop's own width / height in pixels, None for a degenerate crop."""
    left, top, right, bottom = crop
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return None
    return (width * frame) / height


def fits(crop: Crop, ratio: float | None, frame: float,
         tolerance: float = TOLERANCE) -> bool:
    """Whether the crop already has the ratio (None = free: always)."""
    if not ratio:
        return True
    actual = pixel_ratio(crop, frame)
    if actual is None:
        return False
    return abs(actual / ratio - 1.0) <= tolerance


def _clamp_inside(left: float, top: float, width: float, height: float) -> Crop:
    width, height = min(width, 1.0), min(height, 1.0)
    left = min(max(left, 0.0), 1.0 - width)
    top = min(max(top, 0.0), 1.0 - height)
    return (left, top, left + width, top + height)


def fit(crop: Crop, ratio: float | None, frame: float,
        tolerance: float = TOLERANCE) -> Crop:
    """The crop with the ratio applied, keeping its centre: the side that
    is too long is shortened, the other left alone, so a crop that already
    has the ratio (within tolerance) comes back unchanged and the full
    frame comes back as the largest centred crop at the ratio. Nothing
    ever grows past the frame. The engine passes tolerance 0 - the cut
    itself is exact; the screen leaves a rectangle the sliders rounded
    alone."""
    if not ratio:
        return crop
    left, top, right, bottom = crop
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        width, height = 1.0, 1.0
        left, top = 0.0, 0.0
    if fits(crop, ratio, frame, tolerance):
        return crop
    target = ratio / frame                     # wanted width / height, normalised
    centre_x, centre_y = left + width / 2.0, top + height / 2.0
    if width / height > target:
        width = height * target
    else:
        height = width / target
    return _clamp_inside(centre_x - width / 2.0, centre_y - height / 2.0, width, height)


def fit_from_corner(crop: Crop, ratio: float | None, frame: float, *,
                    fixed_right: bool, fixed_bottom: bool) -> Crop:
    """The crop with the ratio applied while a handle is dragged: the
    corner opposite the handle stays put, the side that is too long is
    shortened, and the crop never runs past the frame - when it would,
    it is shortened further on both sides so the ratio holds at the edge
    (it used to be pushed back inside and clipped, which broke the
    ratio exactly where a drag reaches the picture's border)."""
    if not ratio:
        return crop
    left, top, right, bottom = crop
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return crop
    target = ratio / frame
    if width / height > target:
        width = height * target
    else:
        height = width / target
    fixed_x = right if fixed_right else left
    fixed_y = bottom if fixed_bottom else top
    room_x = fixed_x if fixed_right else 1.0 - fixed_x
    room_y = fixed_y if fixed_bottom else 1.0 - fixed_y
    if width > room_x:
        width = room_x
        height = width / target
    if height > room_y:
        height = room_y
        width = height * target
    left = fixed_x - width if fixed_right else fixed_x
    top = fixed_y - height if fixed_bottom else fixed_y
    return (max(0.0, left), max(0.0, top),
            min(1.0, left + width), min(1.0, top + height))


def largest_centred(ratio: float | None, frame: float) -> Crop:
    """The largest crop at the ratio, centred on the frame."""
    return fit(FULL, ratio, frame)


def turned(crop: Crop, direction: int, mirrored: bool = False) -> Crop:
    """The crop after the picture under it is turned a quarter: +1 is the
    clockwise button, -1 the other one. The crop keeps framing the same
    picture content - it used to stay put in normalised terms while the
    frame transposed under it, so a turn moved it onto other content.

    The crop lives in the frame after the turns *and* the flips, and a
    single flip mirrors the sense of a turn: with one flip on, pass
    mirrored=True and the turn goes the other way in this frame. Whether
    the result still has its ratio is the caller's business (a fixed 3:2
    does not survive a turn; ORIGINAL and 1:1 do)."""
    if mirrored:
        direction = -direction
    left, top, right, bottom = crop
    for _ in range(direction % 4):
        # clockwise: a point (x, y) lands at (1 - y, x)
        left, top, right, bottom = 1.0 - bottom, left, 1.0 - top, right
    return (left, top, right, bottom)


def mirrored(crop: Crop, horizontal: bool) -> Crop:
    """The crop after the picture under it is flipped - it keeps framing
    the same content, so it flips with it."""
    left, top, right, bottom = crop
    if horizontal:
        return (1.0 - right, top, 1.0 - left, bottom)
    return (left, 1.0 - bottom, right, 1.0 - top)
