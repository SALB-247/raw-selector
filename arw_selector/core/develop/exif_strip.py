"""Attaches an info strip with the shooting data to the bottom of the image.

EXIF hides inside the file, so most of it is gone once you post to social.
Burnt in as letters visible on screen, it survives wherever it goes.

The strip is appended below the image, so it does not cover the photo area.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ..raw_io import RawMetadata
from .settings import STRIP_FIELDS, ExifStripSettings

log = logging.getLogger(__name__)

_FONT = cv2.FONT_HERSHEY_SIMPLEX

__all__ = ["STRIP_FIELDS", "ExifStripSettings", "apply_exif_strip", "build_lines"]


def build_lines(
    metadata: RawMetadata | None, source: Path, settings: ExifStripSettings
) -> tuple[str, str]:
    """Builds (left text, right text)."""
    parts: list[str] = []

    if "filename" in settings.include:
        parts.append(source.name)

    if metadata:
        if "camera" in settings.include and metadata.camera_model:
            parts.append(metadata.camera_model)
        if "lens" in settings.include and metadata.lens_model:
            parts.append(metadata.lens_model)
        if "focal_length" in settings.include and metadata.focal_length:
            parts.append(f"{metadata.focal_length:g}mm")
        if "aperture" in settings.include and metadata.aperture:
            parts.append(f"f/{metadata.aperture:g}")
        if "shutter" in settings.include and metadata.shutter_speed:
            parts.append(metadata.shutter_display)
        if "iso" in settings.include and metadata.iso:
            parts.append(f"ISO {metadata.iso}")
        if "datetime" in settings.include and metadata.capture_time:
            parts.append(metadata.capture_time.strftime("%Y-%m-%d %H:%M"))
    elif "filename" not in settings.include:
        parts.append(source.name)

    return "  ·  ".join(parts), settings.custom_text


def _measure_text(text: str, scale: float, thickness: int) -> int:
    """The pixel width once drawn. Hangul goes through the PIL path, so it
    is measured separately."""
    if not text:
        return 0

    if any(ord(ch) > 0x2000 for ch in text):
        font = _korean_font(max(10, int(scale * 30)))
        if font is not None:
            try:
                from PIL import Image, ImageDraw

                box = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox(
                    (0, 0), text, font=font
                )
                return box[2] - box[0]
            except Exception:  # noqa: BLE001
                pass
        # with no font found it cannot be drawn either, so width is 0
        return 0

    (width, _), _ = cv2.getTextSize(text, _FONT, scale, thickness)
    return width


def _korean_font(size: int):
    """Finds a system Hangul font. None if there is not one."""
    try:
        from PIL import ImageFont
    except ImportError:
        return None

    for candidate in (
        "C:/Windows/Fonts/malgun.ttf",
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    ):
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return None


def _fit_texts(
    left: str, right: str, available: int, scale: float, thickness: int
) -> tuple[str, float]:
    """Adjusts the size and the content so the left and right texts fit
    within the width.

    Drawn as they are, long shooting data overwrites the right-hand text
    and the letters overlap. First the letters are shrunk, and if it still
    overflows the left text is cut down.
    """
    right_width = _measure_text(right, scale, thickness)
    gap = int(available * 0.03)
    room = max(40, available - right_width - gap)

    if _measure_text(left, scale, thickness) <= room:
        return left, scale

    # step 1: try shrinking the letters (down to 65% of the original)
    shrunk = scale
    for _ in range(8):
        shrunk *= 0.94
        if shrunk < scale * 0.65:
            break
        right_width = _measure_text(right, shrunk, thickness)
        room = max(40, available - right_width - gap)
        if _measure_text(left, shrunk, thickness) <= room:
            return left, shrunk

    # step 2: if it still overflows, drop items from the back
    parts = left.split("  ·  ")
    while len(parts) > 1:
        parts.pop()
        candidate = "  ·  ".join(parts) + "  ·  …"
        if _measure_text(candidate, shrunk, thickness) <= room:
            return candidate, shrunk

    return parts[0] if parts else "", shrunk


def _draw_text(
    canvas: np.ndarray, text: str, x: int, baseline: int, scale: float,
    color: tuple[int, int, int], thickness: int, right_align: bool = False,
) -> None:
    """Drawn with PIL if Hangul is mixed in, otherwise with OpenCV."""
    if not text:
        return

    if any(ord(ch) > 0x2000 for ch in text):
        _draw_text_pil(canvas, text, x, baseline, scale, color, right_align)
        return

    if right_align:
        (width, _), _ = cv2.getTextSize(text, _FONT, scale, thickness)
        x -= width
    cv2.putText(canvas, text, (x, baseline), _FONT, scale, color, thickness, cv2.LINE_AA)


def _draw_text_pil(
    canvas: np.ndarray, text: str, x: int, baseline: int, scale: float,
    color: tuple[int, int, int], right_align: bool,
) -> None:
    """OpenCV's default font draws Hangul as empty rectangles. We use a
    system font."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return

    font = _korean_font(max(10, int(scale * 30)))
    if font is None:
        return

    pil = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil)
    box = draw.textbbox((0, 0), text, font=font)
    if right_align:
        x -= box[2] - box[0]
    draw.text((x, baseline - (box[3] - box[1]) - box[1]), text, font=font,
              fill=(color[2], color[1], color[0]))
    canvas[:] = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def apply_exif_strip(
    image: np.ndarray,
    source: Path,
    metadata: RawMetadata | None,
    settings: ExifStripSettings,
) -> np.ndarray:
    """Returns a new image with the info strip appended below it."""
    if not settings.is_active():
        return image

    try:
        height, width = image.shape[:2]
        # **The strip thickness is taken from the width.** The letters run
        # horizontally, and making the thickness (= the letter size)
        # proportional to the height makes the strip thicker the more
        # portrait the photo is, so the letters grow while the information
        # actually carried shrinks - measured: at 933x1400 an 84px strip
        # grew the letters and `16mm·f/4.5·1/30s·ISO` was cut wholesale,
        # and at 500x1400 only the body name was left.
        #
        # The meaning of height_percent is kept, but the reference becomes
        # "the height it would have at this width if it were 3:2". A 3:2
        # landscape photo is exactly as before (55px at 1400px), and only
        # portrait and narrow crops come back to normal.
        reference = width * 2.0 / 3.0
        strip_height = max(24, int(reference * settings.height_percent / 100.0))

        background = (18, 18, 20) if settings.dark_background else (245, 245, 245)
        foreground = (225, 225, 228) if settings.dark_background else (30, 30, 32)

        strip = np.full((strip_height, width, 3), background, np.uint8)

        left_text, right_text = build_lines(metadata, source, settings)
        scale = strip_height / 46.0
        thickness = max(1, int(round(scale * 1.6)))
        margin = int(width * 0.02)
        baseline = int(strip_height * 0.62)

        # fit them so the left and right do not overlap
        left_text, scale = _fit_texts(
            left_text, right_text, width - margin * 2, scale, thickness
        )
        thickness = max(1, int(round(scale * 1.6)))

        _draw_text(strip, left_text, margin, baseline, scale, foreground, thickness)
        _draw_text(
            strip, right_text, width - margin, baseline, scale,
            foreground, thickness, right_align=True,
        )

        # a thin divider between the photo and the strip
        line_color = (60, 60, 64) if settings.dark_background else (200, 200, 203)
        cv2.line(strip, (0, 0), (width, 0), line_color, 1)

        # The strip is drawn in 8 bits (it is letters and background, so it
        # needs no tonal range) but attached in the photo's dtype. Exported
        # at 16 bits, vstacking it as it is puts the strip's values (0~255)
        # on a 0~65535 scale where they come out almost black.
        return np.vstack([image, strip.astype(image.dtype)])
    except Exception as exc:  # noqa: BLE001 - a failed strip must not block the export
        log.warning("EXIF 띠 생성 실패: %s", exc)
        return image
