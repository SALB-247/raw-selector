"""썸네일 캐시.

GUI에서 4000장 격자를 스크롤하려면 썸네일이 즉시 나와야 합니다. RAW 프리뷰를
그때그때 디코딩하면 장당 100ms라 사용할 수 없습니다.

분석 단계에서 프리뷰가 이미 메모리에 올라와 있으므로, 그 김에 작은 JPEG로
떨어뜨려 둡니다. 추가 비용은 장당 2~3ms 수준입니다.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import cv2
import numpy as np

from .raw_io import imwrite_unicode, resize_long_edge

log = logging.getLogger(__name__)

THUMB_DIR_NAME = "thumbs"
THUMB_LONG_EDGE = 512
THUMB_QUALITY = 82


def thumbnail_dir(cache_dir: Path) -> Path:
    return Path(cache_dir) / THUMB_DIR_NAME


def thumbnail_path(cache_dir: Path, source: Path) -> Path:
    """원본 경로를 해시해서 썸네일 파일명을 만듭니다.

    파일명을 그대로 쓰면 하위 폴더에 같은 이름(DSC001.ARW)이 있을 때
    서로를 덮어씁니다. 4000장 배치에서 흔한 상황입니다.
    """
    digest = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:20]
    return thumbnail_dir(cache_dir) / f"{digest}.jpg"


def write_thumbnail(image_bgr: np.ndarray, destination: Path, long_edge: int = THUMB_LONG_EDGE) -> bool:
    """썸네일을 저장합니다. 실패해도 분석을 막지 않습니다."""
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        small = resize_long_edge(image_bgr, long_edge)
        # cv2.imwrite는 한글 경로에 실패하므로 유니코드 안전 헬퍼를 씁니다.
        return imwrite_unicode(
            destination, small, [cv2.IMWRITE_JPEG_QUALITY, THUMB_QUALITY]
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("썸네일 저장 실패 %s: %s", destination.name, exc)
        return False


def clear_thumbnails(cache_dir: Path) -> int:
    """썸네일을 모두 지웁니다. 지운 개수를 반환."""
    directory = thumbnail_dir(cache_dir)
    if not directory.exists():
        return 0
    count = 0
    for path in directory.glob("*.jpg"):
        try:
            path.unlink()
            count += 1
        except OSError:
            pass
    return count
