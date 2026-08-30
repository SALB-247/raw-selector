"""CR3 (ISO/IEC 14496-12 BMFF) metadata parser.

exifread reads only TIFF-based RAWs (ARW, NEF, CR2 ...). CR3's container is
an entirely different ISO BMFF, so it falls out with "File format not
recognized", and as a result the lens information was empty wholesale and
automatic lens correction did not work.

Structure (verified on a real file):

    ftyp                      brand 'crx '
    moov
      uuid 85c0b687-820f-11e0-8111-f4ce462b6a48   <- Canon metadata container
        CMT1   II*\\0 ...   IFD0     (Make, Model, Orientation)
        CMT2   II*\\0 ...   ExifIFD  (exposure, ISO, lens, capture time)
        CMT3   II*\\0 ...   Canon MakerNote
        CMT4   II*\\0 ...   GPS

Each CMT box is a **complete TIFF stream** (endianness marker + magic + IFD
offset), so it can be sliced out and fed to exifread as it is. We do not
write a new TIFF parser.

The whole file (tens of MB) is not read. It seeks along the box headers and
reads only the pieces it needs - on a 4000-frame batch that difference is
large.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Iterator

import exifread

log = logging.getLogger(__name__)

CANON_UUID = bytes.fromhex("85c0b687820f11e08111f4ce462b6a48")
"""The uuid box identifier Canon puts CR3 metadata in."""

META_BOXES = (b"CMT1", b"CMT2", b"CMT3", b"CMT4")

_MAX_BOX_BYTES = 8 * 1024 * 1024
"""The ceiling on how much is read from one box. It holds even when a
damaged file claims an absurd size."""


def _iter_boxes(fh, end: int) -> Iterator[tuple[bytes, int, int]]:
    """Walks the [size][type] boxes. (type, payload start, box end)."""
    while True:
        position = fh.tell()
        if position + 8 > end:
            return
        header = fh.read(8)
        if len(header) < 8:
            return

        size = int.from_bytes(header[:4], "big")
        box_type = header[4:8]
        header_length = 8

        if size == 1:  # 64-bit extended size
            extended = fh.read(8)
            if len(extended) < 8:
                return
            size = int.from_bytes(extended, "big")
            header_length = 16
        elif size == 0:  # to the end of the file
            size = end - position

        if size < header_length or position + size > end:
            return

        yield box_type, position + header_length, position + size
        fh.seek(position + size)


def _tiff_tags(payload: bytes) -> dict:
    """Reads a CMT box payload (a complete TIFF) with exifread."""
    if len(payload) < 8 or payload[:2] not in (b"II", b"MM"):
        return {}
    try:
        return exifread.process_file(io.BytesIO(payload), details=False) or {}
    except Exception as exc:  # noqa: BLE001 - one broken box, the rest still used
        log.debug("CMT 박스 파싱 실패: %s", exc)
        return {}


def read_exif_tags(path: Path) -> dict:
    """Gathers the EXIF tags out of a CR3. An empty dict if it cannot be
    read.

    CMT1~CMT4 are each read as an independent TIFF and merged. Each box is
    the IFD0 of its own stream, so exifread prefixes them all with
    "Image ...". So that callers are not confused, the commonly used keys
    are also put in under their standard names.
    """
    path = Path(path)
    tags: dict = {}
    try:
        total = path.stat().st_size
        with path.open("rb") as fh:
            for box_type, start, end in _iter_boxes(fh, total):
                if box_type != b"moov":
                    continue
                fh.seek(start)
                for sub_type, sub_start, sub_end in _iter_boxes(fh, end):
                    if sub_type != b"uuid":
                        continue
                    fh.seek(sub_start)
                    if fh.read(16) != CANON_UUID:
                        continue
                    for meta_type, meta_start, meta_end in _iter_boxes(fh, sub_end):
                        if meta_type not in META_BOXES:
                            continue
                        length = min(meta_end - meta_start, _MAX_BOX_BYTES)
                        fh.seek(meta_start)
                        tags.update(_tiff_tags(fh.read(length)))
                break  # there is only one moov
    except OSError as exc:
        log.debug("CR3 읽기 실패 %s: %s", path.name, exc)
        return {}

    return _normalize(tags)


# The prefix exifread attaches differs per box, so we make the standard
# names findable too.
_ALIASES = {
    "EXIF LensModel": ("Image LensModel", "MakerNote LensModel", "EXIF LensModel"),
    "Image Model": ("Image Model",),
    "Image Make": ("Image Make",),
    "EXIF DateTimeOriginal": ("Image DateTimeOriginal", "EXIF DateTimeOriginal"),
    "EXIF ExposureTime": ("Image ExposureTime", "EXIF ExposureTime"),
    "EXIF FNumber": ("Image FNumber", "EXIF FNumber"),
    "EXIF ISOSpeedRatings": ("Image ISOSpeedRatings", "EXIF ISOSpeedRatings"),
    "EXIF FocalLength": ("Image FocalLength", "EXIF FocalLength"),
    "EXIF SubSecTimeOriginal": ("Image SubSecTimeOriginal", "EXIF SubSecTimeOriginal"),
    "Image Orientation": ("Image Orientation",),
    # For computing the 35mm-equivalent focal length. Canon does not write
    # FocalLengthIn35mmFilm, so the sensor size is worked back out from the
    # FocalPlane resolution (raw_io._focal_35mm_from_tags).
    "EXIF FocalLengthIn35mmFilm": (
        "Image FocalLengthIn35mmFilm", "EXIF FocalLengthIn35mmFilm"),
    "EXIF FocalPlaneXResolution": (
        "Image FocalPlaneXResolution", "EXIF FocalPlaneXResolution"),
    "EXIF FocalPlaneYResolution": (
        "Image FocalPlaneYResolution", "EXIF FocalPlaneYResolution"),
    "EXIF FocalPlaneResolutionUnit": (
        "Image FocalPlaneResolutionUnit", "EXIF FocalPlaneResolutionUnit"),
    "EXIF ExifImageWidth": ("Image ExifImageWidth", "EXIF ExifImageWidth"),
    "EXIF ExifImageLength": ("Image ExifImageLength", "EXIF ExifImageLength"),
}


def _normalize(tags: dict) -> dict:
    """Fills in aliases so the standard key names work for access too."""
    if not tags:
        return {}
    result = dict(tags)
    for standard, candidates in _ALIASES.items():
        if standard in result:
            continue
        for candidate in candidates:
            if candidate in tags:
                result[standard] = tags[candidate]
                break
    return result


def is_cr3(path: Path) -> bool:
    """Decided by the real brand, not the extension (in case a file has
    only been renamed)."""
    try:
        with Path(path).open("rb") as fh:
            header = fh.read(12)
    except OSError:
        return False
    return len(header) >= 12 and header[4:8] == b"ftyp" and header[8:12] == b"crx "
