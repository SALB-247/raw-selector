"""이미지 하단에 촬영 정보 띠를 붙입니다.

EXIF는 파일 안에 숨어 있어서 SNS에 올리면 대부분 사라집니다. 화면에 보이는
글자로 박아 두면 어디로 가든 남습니다.

띠는 이미지 아래에 덧붙이는 방식이라 사진 영역을 가리지 않습니다.
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
    """(왼쪽 텍스트, 오른쪽 텍스트)를 만듭니다."""
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
    """그렸을 때의 픽셀 폭. 한글은 PIL 경로라 따로 잽니다."""
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
        # 폰트를 못 찾으면 그리지도 못하므로 폭 0으로 봅니다
        return 0

    (width, _), _ = cv2.getTextSize(text, _FONT, scale, thickness)
    return width


def _korean_font(size: int):
    """시스템 한글 폰트를 찾습니다. 없으면 None."""
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
    """좌우 텍스트가 폭 안에 들어가도록 크기와 내용을 조정합니다.

    그리면 긴 촬영 정보가 오른쪽 문구를 덮어써서 글자가 겹칩니다.
    먼저 글자를 줄이고, 그래도 넘치면 왼쪽 텍스트를 잘라냅니다.
    """
    right_width = _measure_text(right, scale, thickness)
    gap = int(available * 0.03)
    room = max(40, available - right_width - gap)

    if _measure_text(left, scale, thickness) <= room:
        return left, scale

    # 1단계: 글자 크기를 줄여 본다 (원래의 65%까지)
    shrunk = scale
    for _ in range(8):
        shrunk *= 0.94
        if shrunk < scale * 0.65:
            break
        right_width = _measure_text(right, shrunk, thickness)
        room = max(40, available - right_width - gap)
        if _measure_text(left, shrunk, thickness) <= room:
            return left, shrunk

    # 2단계: 그래도 넘치면 뒤에서부터 항목을 덜어냅니다
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
    """한글이 섞이면 PIL로, 아니면 OpenCV로 그립니다."""
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
    """OpenCV 기본 폰트는 한글을 빈 사각형으로 그립니다. 시스템 폰트를 씁니다."""
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
    """이미지 아래에 정보 띠를 덧붙인 새 이미지를 반환합니다."""
    if not settings.is_active():
        return image

    try:
        height, width = image.shape[:2]
        strip_height = max(24, int(height * settings.height_percent / 100.0))

        background = (18, 18, 20) if settings.dark_background else (245, 245, 245)
        foreground = (225, 225, 228) if settings.dark_background else (30, 30, 32)

        strip = np.full((strip_height, width, 3), background, np.uint8)

        left_text, right_text = build_lines(metadata, source, settings)
        scale = strip_height / 46.0
        thickness = max(1, int(round(scale * 1.6)))
        margin = int(width * 0.02)
        baseline = int(strip_height * 0.62)

        # 좌우가 겹치지 않게 맞춥니다
        left_text, scale = _fit_texts(
            left_text, right_text, width - margin * 2, scale, thickness
        )
        thickness = max(1, int(round(scale * 1.6)))

        _draw_text(strip, left_text, margin, baseline, scale, foreground, thickness)
        _draw_text(
            strip, right_text, width - margin, baseline, scale,
            foreground, thickness, right_align=True,
        )

        # 사진과 띠 사이 얇은 구분선
        line_color = (60, 60, 64) if settings.dark_background else (200, 200, 203)
        cv2.line(strip, (0, 0), (width, 0), line_color, 1)

        return np.vstack([image, strip])
    except Exception as exc:  # noqa: BLE001 - 띠 실패로 내보내기를 막지 않습니다
        log.warning("EXIF 띠 생성 실패: %s", exc)
        return image
