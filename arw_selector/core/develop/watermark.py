"""워터마크 합성.

텍스트와 이미지 둘 다 지원합니다. 크기는 항상 이미지 긴 변 대비 비율로
정하므로, 미리보기(1400px)에서 맞춘 위치와 크기가 원본(6192px)에서도
같은 비율로 나옵니다.
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
    """설치된 글꼴 (표시이름, 파일경로) 목록. 한 번만 훑고 캐시합니다."""
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
    """워터마크 좌상단 좌표를 구합니다.

    3×3 정렬로 대략 자리를 잡고, offset으로 미세조정합니다. offset은 이미지
    크기 대비 %라서 해상도가 달라도 같은 위치에 옵니다.
    """
    height, width = image_shape
    item_height, item_width = item_shape
    horizontal, vertical = settings.position.anchor

    # 정렬 비율로 여백 안쪽에서 위치를 잡습니다
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
    """워터마크를 회전합니다. 잘리지 않도록 캔버스를 넓힙니다."""
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
    """알파 채널로 합성합니다. 이미지 밖으로 나가는 부분은 잘라냅니다."""
    height, width = base.shape[:2]
    item_height, item_width = overlay.shape[:2]

    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(width, x + item_width), min(height, y + item_height)
    if x1 <= x0 or y1 <= y0:
        return base

    overlay_crop = overlay[y0 - y:y1 - y, x0 - x:x1 - x].astype(np.float32)
    alpha_crop = alpha[y0 - y:y1 - y, x0 - x:x1 - x].astype(np.float32)[:, :, None]

    region = base[y0:y1, x0:x1].astype(np.float32)
    base[y0:y1, x0:x1] = np.clip(
        region * (1.0 - alpha_crop) + overlay_crop * alpha_crop, 0, 255
    ).astype(np.uint8)
    return base


def _render_text(
    text: str, settings: WatermarkSettings, image_shape: tuple[int, int]
) -> tuple[np.ndarray, np.ndarray] | None:
    """텍스트를 렌더링해 (BGR, 알파)를 반환합니다.

    OpenCV 기본 폰트는 한글을 그리지 못한다(빈 사각형이 됩니다). 한글이
    섞여 있으면 PIL로 시스템 폰트를 찾아 그립니다.
    """
    height, width = image_shape
    long_edge = max(height, width)
    target_height = max(12, int(long_edge * settings.scale / 100.0))

    # 글꼴을 고른 경우와 한글이 섞인 경우는 PIL로 그립니다. OpenCV 기본 폰트는
    # 글꼴 지정을 지원하지 않고 한글도 빈 사각형이 됩니다.
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
        # 밝은 배경에서도 읽히도록 어두운 외곽선을 먼저 깝니다
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
    """텍스트를 시스템 글꼴로 그립니다. font_path를 주면 그 글꼴을 씁니다."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None

    candidates = [
        # 사용자가 고른 글꼴을 가장 먼저 시도합니다
        *( [font_path] if font_path else [] ),
        "C:/Windows/Fonts/malgun.ttf",           # Windows 맑은 고딕
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
    # PIL은 RGB, OpenCV는 BGR이라 뒤집어 줍니다
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
    """PNG 등 워터마크 이미지를 읽어 크기를 맞춥니다. 알파가 있으면 씁니다."""
    # cv2.imread는 한글 경로에 실패하므로 유니코드 안전 헬퍼를 씁니다.
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
    """워터마크를 얹습니다. 실패해도 원본을 그대로 돌려줍니다."""
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
    except Exception as exc:  # noqa: BLE001 - 워터마크 실패로 내보내기를 막지 않습니다
        log.warning("워터마크 합성 실패: %s", exc)
        return image
