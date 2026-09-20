"""Thumbnail cache.

Scrolling a 4000-frame grid in the GUI needs thumbnails to appear
instantly. Decoding the RAW preview on the spot is 100ms per frame, which
is unusable.

The analysis stage already has the preview in memory, so while it is there
it is dropped out as a small JPEG. The extra cost is around 2~3ms per
frame.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

import cv2
import numpy as np

from .cache import cache_root, relative_key
from .raw_io import imwrite_unicode, resize_long_edge

log = logging.getLogger(__name__)

THUMB_DIR_NAME = "thumbs"
THUMB_LONG_EDGE = 512
THUMB_QUALITY = 82


def thumbnail_dir(cache_dir: Path) -> Path:
    return Path(cache_dir) / THUMB_DIR_NAME


def thumbnail_path(cache_dir: Path, source: Path) -> Path:
    """Hashes the source path to build the thumbnail filename.

    Using the filename as it is means that when a subfolder holds the same
    name (DSC001.ARW) they overwrite each other. That is common in a
    4000-frame batch.

    What is hashed is the path relative to the shoot folder (the cache's
    parent, or the folder a cache kept elsewhere was made for - see
    cache.cache_root), the same key the analysis cache uses.
    Hashing the absolute path meant the same card mounted somewhere else
    had every thumbnail built again. A thumbnail an older version wrote
    under the absolute-path name is renamed to the new name the first time
    it is asked for.
    """
    cache_dir = Path(cache_dir)
    folder = thumbnail_dir(cache_dir)
    current = folder / f"{_digest(relative_key(cache_root(cache_dir), source))}.jpg"
    if not current.exists():
        legacy = folder / f"{_digest(str(source))}.jpg"
        if legacy.exists():
            try:
                os.replace(legacy, current)
            except OSError:
                pass  # left where it is; a fresh thumbnail gets written instead
    return current


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]


def write_thumbnail(image_bgr: np.ndarray, destination: Path, long_edge: int = THUMB_LONG_EDGE) -> bool:
    """Saves the thumbnail. A failure does not block the analysis."""
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        small = resize_long_edge(image_bgr, long_edge)
        # cv2.imwrite fails on Hangul paths, so we use the unicode-safe helper.
        return imwrite_unicode(
            destination, small, [cv2.IMWRITE_JPEG_QUALITY, THUMB_QUALITY]
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("썸네일 저장 실패 %s: %s", destination.name, exc)
        return False


def clear_thumbnails(cache_dir: Path) -> int:
    """Deletes every thumbnail. Returns how many were deleted."""
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
