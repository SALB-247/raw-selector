"""Watermark compositing.

Both text and images are supported. The size is always set as a proportion
of the image's long edge, so the position and size set in the preview
(1400px) come out at the same proportion on the original (6192px).
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from .settings import WatermarkPosition, WatermarkSettings

log = logging.getLogger(__name__)

_FONT = cv2.FONT_HERSHEY_SIMPLEX

_FONT_DIRS = (
    "C:/Windows/Fonts",
    "/System/Library/Fonts",
    "/Library/Fonts",
    "/usr/share/fonts",
)
_FONT_SUFFIXES = (".ttf", ".otf", ".ttc")
_font_cache: list[tuple[str, str]] | None = None


def available_fonts() -> list[tuple[str, str]]:
    """The installed fonts as (display name, file path). Swept once and
    cached."""
    global _font_cache
    if _font_cache is not None:
        return _font_cache

    found: dict[str, str] = {}
    for directory in _FONT_DIRS:
        root = Path(directory)
        if not root.is_dir():
            continue
        try:
            for path in sorted(root.rglob("*")):
                if path.suffix.lower() in _FONT_SUFFIXES and path.is_file():
                    found.setdefault(path.stem, str(path))
        except OSError:
            continue

    _font_cache = sorted(found.items())
    return _font_cache


def _anchor(
    settings: WatermarkSettings,
    image_shape: tuple[int, int],
    item_shape: tuple[int, int],
    margin: int,
) -> tuple[int, int]:
    """Works out the top-left coordinate of the watermark.

    A 3x3 alignment places it roughly, and offset fine-tunes it. offset is
    a % of the image size, so it lands in the same place at any resolution.
    """
    height, width = image_shape
    item_height, item_width = item_shape
    horizontal, vertical = settings.position.anchor

    # position it inside the margin using the alignment ratio
    available_width = max(0, width - item_width - margin * 2)
    available_height = max(0, height - item_height - margin * 2)
    x = margin + available_width * horizontal
    y = margin + available_height * vertical

    x += width * settings.offset_x / 100.0
    y += height * settings.offset_y / 100.0

    return int(round(x)), int(round(y))


def _rotate_layer(
    overlay: np.ndarray, alpha: np.ndarray, degrees: int
) -> tuple[np.ndarray, np.ndarray]:
    """Rotates the watermark. The canvas is widened so nothing is cut."""
    if not degrees:
        return overlay, alpha

    height, width = overlay.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), degrees, 1.0)

    cos, sin = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_width = int(height * sin + width * cos)
    new_height = int(height * cos + width * sin)
    matrix[0, 2] += new_width / 2 - width / 2
    matrix[1, 2] += new_height / 2 - height / 2

    rotated = cv2.warpAffine(
        overlay, matrix, (new_width, new_height), flags=cv2.INTER_LINEAR
    )
    rotated_alpha = cv2.warpAffine(
        alpha, matrix, (new_width, new_height), flags=cv2.INTER_LINEAR
    )
    return rotated, rotated_alpha


def _blend(
    base: np.ndarray, overlay: np.ndarray, alpha: np.ndarray, x: int, y: int
) -> np.ndarray:
    """Composites with the alpha channel. Anything past the image edge is
    cut."""
    height, width = base.shape[:2]
    item_height, item_width = overlay.shape[:2]

    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(width, x + item_width), min(height, y + item_height)
    if x1 <= x0 or y1 <= y0:
        return base

    overlay_crop = overlay[y0 - y:y1 - y, x0 - x:x1 - x].astype(np.float32)
    alpha_crop = alpha[y0 - y:y1 - y, x0 - x:x1 - x].astype(np.float32)[:, :, None]

    # **The dtype we were given is returned as it is.** Exporting at 16
    # bits, the photo comes in as float 0~255, and dropping it to uint8
    # here would kill the tonal range for no reason other than the
    # watermark being on. The overlay's pixel values are on a 0~255 scale
    # either way, so the compositing formula is the same.
    region = base[y0:y1, x0:x1].astype(np.float32)
    blended = np.clip(
        region * (1.0 - alpha_crop) + overlay_crop * alpha_crop, 0, 255)
    base[y0:y1, x0:x1] = blended.astype(base.dtype)
    return base


def _render_text(
    text: str, settings: WatermarkSettings, image_shape: tuple[int, int]
) -> tuple[np.ndarray, np.ndarray] | None:
    """Renders the text and returns (BGR, alpha).

    OpenCV's default font cannot draw Hangul (it becomes empty rectangles).
    If Hangul is mixed in, PIL is used to find a system font and draw with
    it.
    """
    height, width = image_shape
    long_edge = max(height, width)
    target_height = max(12, int(long_edge * settings.scale / 100.0))

    # A chosen font, or Hangul mixed in, is drawn with PIL. OpenCV's
    # default font does not support choosing a font, and Hangul comes out
    # as empty rectangles.
    if settings.font_path or any(ord(ch) > 0x2000 for ch in text):
        rendered = _render_text_pil(
            text, target_height, settings.color, settings.font_path
        )
        if rendered is not None:
            return rendered
        log.warning("글꼴을 찾지 못해 워터마크를 건너뛴다")
        return None

    scale = target_height / 30.0
    thickness = max(1, int(round(scale * 2)))
    (text_width, text_height), baseline = cv2.getTextSize(text, _FONT, scale, thickness)

    pad = max(2, target_height // 6)
    canvas_height = text_height + baseline + pad * 2
    canvas_width = text_width + pad * 2

    layer = np.zeros((canvas_height, canvas_width, 3), np.uint8)
    mask = np.zeros((canvas_height, canvas_width), np.uint8)
    origin = (pad, pad + text_height)

    if settings.shadow:
        # lay a dark outline down first so it reads on a light background
        cv2.putText(mask, text, origin, _FONT, scale, 255, thickness + 2, cv2.LINE_AA)
        layer[:] = (0, 0, 0)
        shadow_alpha = mask.astype(np.float32) / 255.0 * 0.5
        layer_text = np.zeros_like(layer)
        cv2.putText(
            layer_text, text, origin, _FONT, scale,
            tuple(int(c) for c in settings.color), thickness, cv2.LINE_AA,
        )
        text_mask = np.zeros_like(mask)
        cv2.putText(text_mask, text, origin, _FONT, scale, 255, thickness, cv2.LINE_AA)
        text_alpha = text_mask.astype(np.float32) / 255.0

        alpha = np.clip(shadow_alpha + text_alpha, 0.0, 1.0)
        combined = layer * (1.0 - text_alpha[:, :, None]) + layer_text * text_alpha[:, :, None]
        return combined.astype(np.uint8), alpha

    cv2.putText(
        layer, text, origin, _FONT, scale,
        tuple(int(c) for c in settings.color), thickness, cv2.LINE_AA,
    )
    cv2.putText(mask, text, origin, _FONT, scale, 255, thickness, cv2.LINE_AA)
    return layer, mask.astype(np.float32) / 255.0


def _render_text_pil(
    text: str, target_height: int, color: tuple[int, int, int],
    font_path: str = "",
) -> tuple[np.ndarray, np.ndarray] | None:
    """Draws the text with a system font. Given a font_path, that font is
    used."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None

    candidates = [
        # the font the user chose is tried first of all
        *( [font_path] if font_path else [] ),
        "C:/Windows/Fonts/malgun.ttf",           # Windows Malgun Gothic
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",  # macOS
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    ]
    font = None
    for path in candidates:
        if Path(path).exists():
            try:
                font = ImageFont.truetype(path, target_height)
                break
            except OSError:
                continue
    if font is None:
        return None

    dummy = Image.new("RGB", (1, 1))
    box = ImageDraw.Draw(dummy).textbbox((0, 0), text, font=font)
    pad = max(2, target_height // 6)
    size = (box[2] - box[0] + pad * 2, box[3] - box[1] + pad * 2)

    layer = Image.new("RGB", size, (0, 0, 0))
    mask = Image.new("L", size, 0)
    # PIL is RGB and OpenCV is BGR, so we reverse it
    ImageDraw.Draw(layer).text(
        (pad - box[0], pad - box[1]), text, font=font, fill=tuple(reversed(color))
    )
    ImageDraw.Draw(mask).text((pad - box[0], pad - box[1]), text, font=font, fill=255)

    return (
        cv2.cvtColor(np.array(layer), cv2.COLOR_RGB2BGR),
        np.array(mask).astype(np.float32) / 255.0,
    )


def _render_image(
    path: Path, settings: WatermarkSettings, image_shape: tuple[int, int]
) -> tuple[np.ndarray, np.ndarray] | None:
    """Reads a watermark image (PNG and so on) and fits its size. Alpha is
    used if there is any."""
    # cv2.imread fails on Hangul paths, so we use the unicode-safe helper.
    from ..raw_io import imread_unicode

    logo = imread_unicode(path, cv2.IMREAD_UNCHANGED)
    if logo is None:
        log.warning("워터마크 이미지를 열 수 없습니다: %s", path)
        return None

    long_edge = max(image_shape)
    target_width = max(8, int(long_edge * settings.scale / 100.0))
    scale = target_width / logo.shape[1]
    resized = cv2.resize(
        logo,
        (target_width, max(1, int(round(logo.shape[0] * scale)))),
        interpolation=cv2.INTER_AREA,
    )

    if resized.ndim == 3 and resized.shape[2] == 4:
        return resized[:, :, :3], resized[:, :, 3].astype(np.float32) / 255.0
    if resized.ndim == 2:
        resized = cv2.cvtColor(resized, cv2.COLOR_GRAY2BGR)
    return resized[:, :, :3], np.ones(resized.shape[:2], np.float32)


def apply_watermark(image: np.ndarray, settings: WatermarkSettings) -> np.ndarray:
    """Lays the watermark on. On failure the original is returned as it
    is."""
    if not settings.is_active():
        return image

    try:
        rendered = None
        if settings.image_path:
            path = Path(settings.image_path)
            if path.exists():
                rendered = _render_image(path, settings, image.shape[:2])
            else:
                log.warning("워터마크 이미지가 없습니다: %s", path)
        if rendered is None and settings.text:
            rendered = _render_text(settings.text, settings, image.shape[:2])
        if rendered is None:
            return image

        overlay, alpha = rendered
        overlay, alpha = _rotate_layer(overlay, alpha, settings.rotation)
        alpha = alpha * (settings.opacity / 100.0)

        margin = int(max(image.shape[:2]) * settings.margin / 100.0)
        x, y = _anchor(settings, image.shape[:2], overlay.shape[:2], margin)
        return _blend(image.copy(), overlay, alpha, x, y)
    except Exception as exc:  # noqa: BLE001 - a failed watermark must not block the export
        log.warning("워터마크 합성 실패: %s", exc)
        return image
