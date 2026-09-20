"""Maker metadata exifread cannot reach - RW2 ISO/lens, Sony and Nikon AF
position.

In all three cases the cause was not encryption but a **missing parser**
(RESEARCH_METADATA.md, verified against exiftool 13.55 with every case
matching):

- Panasonic RW2 has a TIFF magic of 85 rather than 42, so exifread rejects
  the whole file. The ISO is in IFD0 0x0017, and the lens is in plain text
  in MakerNote 0x0051 inside the embedded JPEG (IFD0 0x002E).
- The Sony MakerNote (on recent bodies) starts the IFD straight away with
  no header, and the main focus position is the plain-text tag 0x2027 =
  (image W, image H, x, y). The 0x94xx encrypted blocks are left alone.
- Nikon AFInfo2 (MakerNote 0x00B7) is plain text, and the LE u16 offset of
  the AF area (X,Y,W,H) is fixed per version. It reads the same way on the
  Z9's HE/HE*.
- Canon CR3's AFInfo2 (MakerNote 0x0026 inside the CMT3 box) is plain text
  too. The coordinates are signed with a centre origin and Y is positive
  upwards (demonstrated against detected faces). CR2 is not supported
  because there is no real file to verify against.

The functions here **never throw an exception** - they are called once per
frame in the middle of a batch analysis, so if something cannot be read
they fall back to None or an empty dict. They are used as they are from
(spawned) workers, so only module top-level functions live here.

Careful with what the coordinates mean: Sony FocusLocation points at the
zone (the torso), not the eye, under zone AF (measured on 47 frames -
RESEARCH_METADATA.md). Use it only as a record of "where the camera put
the AF", not as a replacement for face detection.
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path

log = logging.getLogger(__name__)

#: Only this much of the header is read and parsed. Measured (five bodies),
#: every value we touch is near the front - Sony MakerNote 6KB, Nikon 33KB,
#: the RW2 ISO at the head of the file, and the RW2 lens (embedded JPEG) at
#: 578KB also falls inside this.
#:
#: There is no reason to read or mmap a 40MB RAW whole - this is called
#: once per frame by 9 workers, so the IO is honestly capped at 2MB, and
#: for a file whose value offsets run past this range the parser quietly
#: falls back to None.
_HEADER_BYTES = 2 * 1024 * 1024

_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8,
               11: 4, 12: 8}

#: The LE u16 start offset of the AF area (X, Y, W, H) per AFInfo2 version.
#: Measured and verified: v0400 = Z9 (lossless/HE/HE*), v0402 = Z50 II (all
#: 40 frames), v0301 = Z5. exiftool 13.55 does not know v0402 yet, but it
#: has the same layout.
_NIKON_AF_OFFSETS = {
    "0300": 0x2E, "0301": 0x2E,
    "0400": 0x42, "0401": 0x42, "0402": 0x42,
}


def _tiff_header(buf, base: int = 0) -> tuple[str, int] | None:
    """(endian, first IFD offset). Accepts RW2's magic 85 too - that is the
    point."""
    if buf[base:base + 2] == b"II":
        endian = "<"
    elif buf[base:base + 2] == b"MM":
        endian = ">"
    else:
        return None
    (magic,) = struct.unpack_from(endian + "H", buf, base + 2)
    if magic not in (42, 85):        # 42 = TIFF, 85 = Panasonic RW2
        return None
    (first,) = struct.unpack_from(endian + "I", buf, base + 4)
    return endian, first


def _read_ifd(buf, offset: int, endian: str, base: int = 0) -> dict[int, tuple]:
    """One IFD -> {tag: (kind, count, value bytes)}. Value offsets are
    relative to base."""
    entries: dict[int, tuple] = {}
    offset += base
    if offset < base or offset + 2 > len(buf):
        return entries
    (count,) = struct.unpack_from(endian + "H", buf, offset)
    for index in range(min(count, 512)):
        at = offset + 2 + index * 12
        if at + 12 > len(buf):
            break
        tag, kind, number = struct.unpack_from(endian + "HHI", buf, at)
        size = _TYPE_SIZES.get(kind, 1) * number
        if size <= 4:
            raw = bytes(buf[at + 8:at + 8 + size])
        else:
            (pointer,) = struct.unpack_from(endian + "I", buf, at + 8)
            pointer += base
            if pointer + size > len(buf):
                continue
            raw = bytes(buf[pointer:pointer + size])
        entries[tag] = (kind, number, raw)
    return entries


def _shorts(entry: tuple, endian: str) -> tuple[int, ...]:
    _, number, raw = entry
    count = min(number, len(raw) // 2)
    return struct.unpack(endian + "H" * count, raw[:count * 2])


def _longs(entry: tuple, endian: str) -> tuple[int, ...]:
    _, number, raw = entry
    count = min(number, len(raw) // 4)
    return struct.unpack(endian + "I" * count, raw[:count * 4])


def _maker_note_offset(buf, endian: str, first: int, base: int = 0) -> int | None:
    """The 0x927C value pointer in the ExifIFD (**relative to the file**).
    What is needed is the position, not the value.

    base is where the TIFF header starts in the file - 0 for RAW, and not 0
    for JPEG because the EXIF sits inside an APP1 segment. Offsets inside
    an IFD are all relative to the TIFF header, so base has to be added to
    get a file position. The returned value has base added so callers can
    use it directly (with base=0 it is the same as before).
    """
    ifd0 = _read_ifd(buf, first, endian, base)
    if 0x8769 not in ifd0:
        return None
    exif_off = base + _longs(ifd0[0x8769], endian)[0]
    if exif_off + 2 > len(buf):
        return None
    (count,) = struct.unpack_from(endian + "H", buf, exif_off)
    for index in range(min(count, 512)):
        at = exif_off + 2 + index * 12
        if at + 12 > len(buf):
            break
        tag, _, _ = struct.unpack_from(endian + "HHI", buf, at)
        if tag == 0x927C:
            return base + struct.unpack_from(endian + "I", buf, at + 8)[0]
    return None


#: JPEG is not TIFF; it hangs the EXIF (a TIFF stream) inside an APP1
#: segment. Segments are walked from the SOI (FFD8) to find the TIFF header
#: position after 'Exif\0\0'.
def _jpeg_exif_base(buf) -> int | None:
    """The file offset of the TIFF header inside a JPEG APP1. None if it is
    not a JPEG or there is none."""
    if buf[:2] != b"\xff\xd8":
        return None
    pos = 2
    while pos + 4 <= len(buf):
        if buf[pos] != 0xFF:
            return None
        marker = buf[pos + 1]
        if marker == 0xFF:
            pos += 1                  # fill byte before a marker (allowed)
            continue
        if marker in (0xDA, 0xD9):    # SOS/EOI - past this is pixels or end
            return None
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2                  # a marker with no length field
            continue
        (seg_len,) = struct.unpack_from(">H", buf, pos + 2)
        if seg_len < 2:
            return None
        if marker == 0xE1 and buf[pos + 4:pos + 10] == b"Exif\x00\x00":
            return pos + 10
        pos += 2 + seg_len
    return None


def _with_header(path: Path, reader):
    """Reads only the front of the file (_HEADER_BYTES) and runs
    reader(buf). Failure gives None."""
    try:
        with path.open("rb") as fh:
            buf = fh.read(_HEADER_BYTES)
        return reader(buf)
    except Exception as exc:  # noqa: BLE001 - mid-batch, so it backs off
        log.debug("maker_meta 실패 %s: %s", path.name, exc)
        return None


# ---------------------------------------------------------------- colour space


#: A file shot in Adobe RGB comes out with **ColorSpace 65535
#: (Uncalibrated) rather than 2**. The spec lays it down that way, and the
#: real distinction is made by InteropIndex ('R98'=sRGB, 'R03'=AdobeRGB) in
#: the Interoperability IFD.
#:
#: Measured (2026-07-28) - it reads on Sony JPEG/ARW, Panasonic RW2
#: (embedded JPEG), Canon CR2 and camera JPEGs generally. **Nikon, though,
#: does not use this tag at all** and writes to MakerNote 0x001E (confirmed
#: on a real Z9 file). Canon CR3 (ISO-BMFF) and Apple HEIC cannot be read.
#:
#: RAW has nothing to do with this setting because we demosaic it
#: ourselves - it only applies to the JPEGs the camera bakes. So the only
#: things that need the distinction are JPEG/HEIF originals, and those read
#: on every manufacturer.
_INTEROP_SPACES = {"R98": "srgb", "R03": "adobe_rgb"}

#: The fallback when there is no InteropIndex. Measured, **19** of the 125
#: folders in the archive were this case, holding only ColorSpace=1 (sRGB)
#: with no Interop IFD - without the fallback that many fall through as
#: undecidable.
_COLORSPACE_TAG = {1: "srgb", 2: "adobe_rgb"}


def colour_space(path: Path) -> str:
    """This file's colour space - 'srgb' or 'adobe_rgb'. 'srgb' if unknown.

    **Answering sRGB when unknown is the safe choice.** Measured, 6 of the
    125 folders in the archive were Adobe RGB. Reading one as Adobe RGB by
    mistake cuts the saturation of a perfectly good photo, so the loss is
    far larger than in the other direction.

    **Looked at per file, not per folder.** There really was a case where
    only the last 2 of 2,159 frames in one folder were sRGB - the setting
    was changed mid-shoot. The underscore prefix in the file name (DCF's
    Adobe RGB marker) is not used as a signal. Only 1 of the 6 Adobe RGB
    folders had the underscore.

    Sony HEIF is always sRGB because the camera only lets you pick sRGB for
    HEIF shooting - it does not need an InteropIndex.
    """

    def reader(buf) -> str:
        base = _jpeg_exif_base(buf)
        header = _tiff_header(buf, base if base is not None else 0)
        if header is None:
            return "srgb"
        endian, first = header
        offset = base if base is not None else 0
        ifd0 = _read_ifd(buf, first, endian, offset)
        if 0x8769 not in ifd0:
            return "srgb"
        exif = _read_ifd(buf, _longs(ifd0[0x8769], endian)[0], endian, offset)

        if 0xA005 in exif:
            interop = _read_ifd(buf, _longs(exif[0xA005], endian)[0],
                                endian, offset)
            entry = interop.get(0x0001)
            if entry is not None:
                index = entry[2].split(b"\x00")[0].decode("ascii", "replace")
                if index in _INTEROP_SPACES:
                    return _INTEROP_SPACES[index]

        if 0xA001 in exif:
            values = _shorts(exif[0xA001], endian)
            if values:
                return _COLORSPACE_TAG.get(values[0], "srgb")
        return "srgb"

    return _with_header(path, reader) or "srgb"


# ---------------------------------------------------------------- Panasonic


def rw2_extras(path: Path) -> dict:
    """What exifread misses on RW2 - {'iso': int, 'lens': str} (only what
    is there).

    The model, time and focal length the app shows now come from the
    embedded preview EXIF fallback, and that preview has no ISO or lens, so
    those two alone looked empty.
    """

    def reader(buf) -> dict:
        header = _tiff_header(buf)
        if header is None:
            return {}
        endian, first = header
        ifd0 = _read_ifd(buf, first, endian)
        out: dict = {}
        if 0x0017 in ifd0:
            values = _shorts(ifd0[0x0017], endian)
            if values and 0 < values[0] < 10_000_000:
                out["iso"] = int(values[0])
        # The lens is in the MakerNote of the EXIF inside the embedded
        # JPEG (JpgFromRaw). The 35mm-equivalent focal length (0xA405) is
        # in plain text in that same embedded EXIF too (measured 105mm).
        jpg = ifd0.get(0x002E)
        if jpg is not None:
            data = jpg[2]
            exif_at = data.find(b"Exif\x00\x00")
            if exif_at >= 0:
                sub = data[exif_at + 6:]
                sub_header = _tiff_header(sub)
                if sub_header is not None:
                    sub_endian, sub_first = sub_header
                    sub_ifd0 = _read_ifd(sub, sub_first, sub_endian)
                    if 0x8769 in sub_ifd0:
                        exif_ifd = _read_ifd(
                            sub, _longs(sub_ifd0[0x8769], sub_endian)[0], sub_endian)
                        if 0xA405 in exif_ifd:
                            values = _shorts(exif_ifd[0xA405], sub_endian)
                            if values and 0 < values[0] < 5000:
                                out["focal_35mm"] = float(values[0])
                    maker_off = _maker_note_offset(sub, sub_endian, sub_first)
                    if (maker_off is not None
                            and sub[maker_off:maker_off + 9] == b"Panasonic"):
                        maker = _read_ifd(sub, maker_off + 12, sub_endian)
                        if 0x0051 in maker:
                            lens = maker[0x0051][2].split(b"\x00")[0]
                            text = lens.decode("ascii", "replace").strip()
                            if text and text != "N/A":
                                out["lens"] = text
        return out

    return _with_header(path, reader) or {}


# ---------------------------------------------------------------- AF position


def sony_focus_location(path: Path) -> tuple[int, int, int, int] | None:
    """Sony plain-text tag 0x2027 = (image W, image H, x, y). Relative to
    the sensor display orientation."""

    def reader(buf):
        header = _tiff_header(buf)
        if header is None:
            return None
        endian, first = header
        maker_off = _maker_note_offset(buf, endian, first)
        if maker_off is None or maker_off + 2 > len(buf):
            return None
        # Recent Sony starts the IFD straight away with no MakerNote
        # header. If the first u16 is not a plausible entry count, it is
        # the "SONY DSC " header form (+12).
        (count,) = struct.unpack_from(endian + "H", buf, maker_off)
        start = maker_off if 0 < count < 512 else maker_off + 12
        maker = _read_ifd(buf, start, endian)
        if 0x2027 not in maker:
            return None
        values = _shorts(maker[0x2027], endian)
        if len(values) < 4:
            return None
        img_w, img_h, x, y = values[:4]
        if not (img_w and img_h and x < img_w and y < img_h):
            return None
        # 0x2037 FocusFrameSize - the AF frame size the camera actually
        # displayed. If present, this measured value is used instead of
        # the synthesised box (8% of the long edge) (D2). Measured on the
        # A6700: (135, 138) = exiftool 'FocusFrameSize 135x138' exactly.
        frame_w = frame_h = None
        size_entry = maker.get(0x2037)
        if size_entry is not None:
            size = _shorts(size_entry, endian)
            if (len(size) >= 2 and 0 < size[0] < img_w and 0 < size[1] < img_h):
                frame_w, frame_h = int(size[0]), int(size[1])
        return int(img_w), int(img_h), int(x), int(y), frame_w, frame_h

    return _with_header(path, reader)


def nikon_af_area(path: Path) -> tuple[int, int, int, int, int, int] | None:
    """Nikon AFInfo2's (x, y, w, h, reference W, reference H).

    The coordinates are in output image space. The reference dimensions use
    the width/height of the type-0 SubIFD (the original pixels) - at most a
    0.3% difference from the output (Z9 8280 vs 8256), which is negligible
    for use as a hint box.
    """

    def reader(buf):
        header = _tiff_header(buf)
        if header is None:
            return None
        endian, first = header
        ifd0 = _read_ifd(buf, first, endian)

        full_w = full_h = 0
        subs = ifd0.get(0x014A)
        if subs is not None:
            for sub_off in _longs(subs, endian):
                sub = _read_ifd(buf, sub_off, endian)
                kind = sub.get(0x00FE)
                if kind is None or _longs(kind, endian)[:1] != (0,):
                    continue
                w_entry, h_entry = sub.get(0x0100), sub.get(0x0101)
                if w_entry and h_entry:
                    full_w = (_longs(w_entry, endian) or _shorts(w_entry, endian))[0]
                    full_h = (_longs(h_entry, endian) or _shorts(h_entry, endian))[0]
                    break
        if not (full_w and full_h):
            return None

        maker_off = _maker_note_offset(buf, endian, first)
        if maker_off is None or buf[maker_off:maker_off + 5] != b"Nikon":
            return None
        # After "Nikon\0" plus 4 version bytes there is **its own TIFF
        # header** - the offsets are relative to that header. Read them
        # relative to the file and you land somewhere else entirely (a
        # trap already hit in nef_meta's ground-truth comparison).
        block_base = maker_off + 10
        sub_header = _tiff_header(buf, block_base)
        if sub_header is None:
            return None
        sub_endian, sub_first = sub_header
        maker = _read_ifd(buf, sub_first, sub_endian, base=block_base)
        if 0x00B7 not in maker:
            return None
        blob = maker[0x00B7][2]
        version = blob[:4].decode("ascii", "replace")
        offset = _NIKON_AF_OFFSETS.get(version)
        if offset is None or offset + 8 > len(blob):
            return None
        x, y, w, h = struct.unpack_from("<HHHH", blob, offset)
        if not (w and h and x < full_w and y < full_h):
            return None
        return int(x), int(y), int(w), int(h), int(full_w), int(full_h)

    return _with_header(path, reader)


def _canon_afinfo2(blob: bytes, endian: str
                   ) -> tuple[int, int, int, int, int, int] | None:
    """A Canon AFInfo2 blob -> (x, y, w, h, reference W, reference H).
    Independent of the container it came in.

    Whether from CR3 (the CMT3 box) or JPEG (APP1 EXIF), the structure of
    this SHORT array is the same:
    [size, mode, point count N, valid count, image W, H, AF ref W, H,
     width[N], height[N], X[N], Y[N], focus bits[(N+15)//16],
     selected bits[...]].
    X and Y are **signed with a centre origin** and Y is positive upwards -
    measured (R6M3, compared against detected faces): Y-up landed inside
    the face, Y-down landed far below it.

    **If the valid count is greater than 1, the first point must not be
    used.** Mirrorless bodies (R3/R5/R6) carry only one tracking box so the
    valid count is 1, but older DSLRs (5D3/5D4/1DX2) carry **all** 61 or 81
    fixed AF points and mark only the points in focus with the
    AFPointsInFocus bitmask. Measured (5D3): valid 61, focus point #59 -
    use the first point and it points at some unrelated fixed position. So
    when there are multiple points, the **union box** of the points whose
    bits are set is used (with an adjacent cluster such as 4- or 8-point
    expansion it covers that subject, and when they are scattered it
    naturally becomes a wide box).
    """
    if len(blob) < 16:
        return None
    _, _, num, valid, _, _, af_w, af_h = struct.unpack_from(endian + "H" * 8, blob, 0)
    if not (num and valid and af_w and af_h):
        return None
    if len(blob) < 16 + num * 8:
        return None
    widths = struct.unpack_from(endian + f"{num}H", blob, 16)
    heights = struct.unpack_from(endian + f"{num}H", blob, 16 + num * 2)
    xs = struct.unpack_from(endian + f"{num}h", blob, 16 + num * 4)   # signed!
    ys = struct.unpack_from(endian + f"{num}h", blob, 16 + num * 6)

    if valid == 1:
        picked = [0]
    else:
        words_n = (num + 15) // 16
        focus_at = 16 + num * 8
        if len(blob) < focus_at + words_n * 2:
            return None       # if we cannot tell which point hit, no guess
        words = struct.unpack_from(endian + f"{words_n}H", blob, focus_at)
        picked = [i for i in range(num) if words[i // 16] & (1 << (i % 16))]
        if not picked:
            return None

    # The union box (for a single point, that point as it is)
    left = min(af_w / 2 + xs[i] - widths[i] / 2 for i in picked)
    right = max(af_w / 2 + xs[i] + widths[i] / 2 for i in picked)
    top = min(af_h / 2 - ys[i] - heights[i] / 2 for i in picked)
    bottom = max(af_h / 2 - ys[i] + heights[i] / 2 for i in picked)
    x, y = (left + right) / 2, (top + bottom) / 2
    w, h = right - left, bottom - top
    if not (w and h and 0 <= x < af_w and 0 <= y < af_h):
        return None
    return int(x), int(y), int(w), int(h), int(af_w), int(af_h)


def canon_af_area(path: Path) -> tuple[int, int, int, int, int, int] | None:
    """Canon CR3 AFInfo2 (MakerNote 0x0026)'s (x, y, w, h, reference W,
    reference H).

    The Canon MakerNote in a CR3 is the CMT3 box inside moov/uuid, and it
    is itself a complete TIFF stream (the same path as cr3.py). AFInfo2 is
    a SHORT array:
    [size, mode, point count N, valid count, image W, H, AF ref W, H,
     width[N], height[N], X[N], Y[N], ...]. X and Y are **signed with a
    centre origin** and Y is positive upwards - measured (R6M3, compared
    against detected faces): Y-up landed inside the face, Y-down landed
    far below it.

    The returned (x, y) is converted to pixels with the origin at the top
    left of the image.
    """
    blob = _cr3_afinfo2_blob(path)
    if blob is None:
        return None
    payload, endian = blob
    return _canon_afinfo2(payload, endian)


def _cr3_afinfo2_blob(path: Path) -> tuple[bytes, str] | None:
    """The CR3 AFInfo2 (0x0026) blob and its endian. Shared by the AF box
    and the AF mode."""
    # CR3 is ISO BMFF rather than TIFF, so the common _with_mmap path
    # cannot be used.
    try:
        from . import cr3

        payload = None
        total = path.stat().st_size
        with path.open("rb") as fh:
            for box_type, start, end in cr3._iter_boxes(fh, total):
                if box_type != b"moov":
                    continue
                fh.seek(start)
                for sub_type, sub_start, sub_end in cr3._iter_boxes(fh, end):
                    if sub_type != b"uuid":
                        continue
                    fh.seek(sub_start)
                    if fh.read(16) != cr3.CANON_UUID:
                        continue
                    for meta_type, meta_start, meta_end in cr3._iter_boxes(fh, sub_end):
                        if meta_type == b"CMT3":
                            fh.seek(meta_start)
                            payload = fh.read(min(meta_end - meta_start, 8_000_000))
                            break
                break
        if payload is None:
            return None
        header = _tiff_header(payload)
        if header is None:
            return None
        endian, first = header
        maker = _read_ifd(payload, first, endian)
        if 0x0026 not in maker:
            return None
        return maker[0x0026][2], endian
    except Exception as exc:  # noqa: BLE001 - mid-batch, so it backs off
        log.debug("cr3 AFInfo2 읽기 실패 %s: %s", path.name, exc)
        return None


def jpeg_af_area(path: Path) -> tuple[int, int, int, int, int, int] | None:
    """The AF area of a JPEG the camera produced itself (x, y, w, h,
    reference W, reference H).

    The same MakerNote as in RAW goes straight into the JPEG's APP1 EXIF -
    what was blocked was not the data but the parser (it split on the RAW
    extension alone). The extension does not tell us the manufacturer, so
    it is split on IFD0's Make (0x010F).

    Canon is **exactly the same** AFInfo2 (0x0026) as CR3, so the parser is
    shared. Nikon is the same AFInfo2 (0x00B7) as NEF, but the reference
    dimensions come from the EXIF PixelXDimension rather than a SubIFD (a
    JPEG has no SubIFD).

    Sony (0x2027) was not included - there is no real file to verify
    against, and on ARW the MakerNote value offsets are relative to the
    file whereas on JPEG they are relative to the TIFF header, so carrying
    it across as it is would be quietly wrong. Held back for the same
    reason as CR2.
    """

    def reader(buf):
        base = _jpeg_exif_base(buf)
        if base is None:
            return None
        header = _tiff_header(buf, base)
        if header is None:
            return None
        endian, first = header
        ifd0 = _read_ifd(buf, first, endian, base)
        make = b""
        if 0x010F in ifd0:
            make = ifd0[0x010F][2].split(b"\x00")[0].upper()
        maker_off = _maker_note_offset(buf, endian, first, base)
        if maker_off is None:
            return None

        if make.startswith(b"CANON"):
            # The Canon MakerNote starts the IFD straight away with no
            # header, and the value offsets are relative to the TIFF
            # header - so base is passed straight through.
            maker = _read_ifd(buf, maker_off - base, endian, base)
            if 0x0026 not in maker:
                return None
            return _canon_afinfo2(maker[0x0026][2], endian)

        if make.startswith(b"NIKON"):
            if buf[maker_off:maker_off + 5] != b"Nikon":
                return None
            # The same trap as NEF - after "Nikon\0" plus the version
            # there is **its own TIFF header**, and the offsets are
            # relative to that header.
            block_base = maker_off + 10
            sub_header = _tiff_header(buf, block_base)
            if sub_header is None:
                return None
            sub_endian, sub_first = sub_header
            maker = _read_ifd(buf, sub_first, sub_endian, base=block_base)
            if 0x00B7 not in maker:
                return None
            blob = maker[0x00B7][2]
            offset = _NIKON_AF_OFFSETS.get(blob[:4].decode("ascii", "replace"))
            if offset is None or offset + 8 > len(blob):
                return None
            x, y, w, h = struct.unpack_from("<HHHH", blob, offset)
            # The reference dimensions are the EXIF pixel dimensions (a
            # JPEG has no type-0 SubIFD)
            full_w = full_h = 0
            if 0x8769 in ifd0:
                exif = _read_ifd(buf, _longs(ifd0[0x8769], endian)[0], endian, base)
                for tag, into in ((0xA002, "w"), (0xA003, "h")):
                    if tag in exif:
                        entry = exif[tag]
                        value = (_longs(entry, endian) or _shorts(entry, endian))
                        if value:
                            if into == "w":
                                full_w = value[0]
                            else:
                                full_h = value[0]
            if not (full_w and full_h):
                return None
            if not (w and h and x < full_w and y < full_h):
                return None
            return int(x), int(y), int(w), int(h), int(full_w), int(full_h)

        return None

    return _with_header(path, reader)


def _rotate_box(x: float, y: float, w: float, h: float,
                frame_w: float, frame_h: float, orientation: int):
    """Moves a centre box in sensor orientation to coordinates after the
    EXIF orientation is applied. (x,y) is the centre.

    Mirrors the eight cases of raw_io.apply_orientation exactly, so the box
    lands on the same pixels the preview was moved to. The mirrored ones
    (2, 4, 5, 7) come from scanners rather than cameras, but a file that
    carries one would otherwise get a box on the wrong side of the frame.
    """
    if orientation == 2:      # mirrored horizontally
        return frame_w - 1 - x, y, w, h
    if orientation == 3:      # 180°
        return frame_w - 1 - x, frame_h - 1 - y, w, h
    if orientation == 4:      # mirrored vertically
        return x, frame_h - 1 - y, w, h
    if orientation == 5:      # mirrored, then 90° CCW = transpose
        return y, x, h, w
    if orientation == 6:      # 90° CW - the display size is (H, W)
        return frame_h - 1 - y, x, h, w
    if orientation == 7:      # mirrored, then 90° CW
        return frame_h - 1 - y, frame_w - 1 - x, h, w
    if orientation == 8:      # 90° CCW
        return y, frame_w - 1 - x, h, w
    return x, y, w, h


def _rotates_frame(orientation: int) -> bool:
    """Whether the orientation turns the frame on its side (W and H swap)."""
    return orientation in (5, 6, 7, 8)


#: The JPEG extensions to try reading AF from. HEIF has a different
#: container (not APP1), so it is a separate matter.
JPEG_SUFFIXES = (".jpg", ".jpeg")


# ---------------------------------------------------------------- AF area mode
#
# For display in the details panel. The values are returned in the camera's
# own terms (in English) and not translated - they are closer to proper
# nouns, like lens names. A value with no verified name is shown as its
# number ("Mode 3"): a number is honest where a guessed name would not be,
# and the line stays so the shooter can tell the modes apart (it used to
# be dropped altogether, which hid a Sony body's most-used modes).

#: Canon AFInfo2 blob offset 2 (u16). It is the public table from
#: exiftool's Canon.pm and is common across bodies. Confirmed on the user's
#: real photos that 2, 6, 8, 9, 10 and 13 match exiftool.
CANON_AF_AREA_MODES = {
    0: "Off (Manual Focus)", 1: "AF Point Expansion (surround)",
    2: "Single-point AF", 4: "Auto", 5: "Face Detect AF",
    6: "Face + Tracking", 7: "Zone AF", 8: "AF Point Expansion (4 point)",
    9: "Spot AF", 10: "AF Point Expansion (8 point)",
    11: "Flexizone Multi (49 point)", 12: "Flexizone Multi (9 point)",
    13: "Flexizone Single", 14: "Large Zone AF",
}

#: Nikon AFInfo2 blob offset 5 (u8). The position was found by differencing
#: (comparing frames of the same version that differ only in mode,
#: RESEARCH_METADATA.md section 7), and the value tables for DSLR and Z are
#: completely different. **Only values compared against 86 real files** are
#: carried here.
NIKON_AF_AREA_MODES_DSLR = {      # AFInfo2 0100/0101
    0: "Single Area", 4: "Dynamic Area (9 points)", 14: "Dynamic Area (25 points)",
}
NIKON_AF_AREA_MODES_Z = {         # AFInfo2 0300 and above
    197: "Auto-area", 207: "3D-tracking", 208: "Wide (C1/C2)",
}

#: Sony MakerNote 0x201C AFAreaModeSetting (1 byte, plain text - outside
#: the 0x94xx encrypted block). Only values verified against real files:
#: 11=Zone from 48 A6700 zone-AF frames. An A1 card of 620 frames held
#: 0 (332), 3 (195) and 1 (90) as well - shown as "Mode 0/3/1" until
#: someone matches them to the menu.
SONY_AF_AREA_MODES = {
    11: "Zone",
}

#: Sony MakerNote 0x2021 AFTracking (1 byte, plain text). exiftool's names;
#: 2 is what an A1 writes for real-time tracking (20 frames, all with
#: 0x201C = 0 - the area mode value that goes with it is not yet verified
#: and stays unnamed). A tracking shot used to show no AF line at all,
#: because the area mode was the only thing read and its value was
#: unknown.
SONY_AF_TRACKING = {
    1: "Face tracking",
    2: "Tracking",
}


def _mode_name(table: dict[int, str], value: int) -> str:
    """The verified name, or the bare number as "Mode N"."""
    return table.get(value) or f"Mode {value}"


def _nikon_mode_from_blob(blob: bytes) -> str | None:
    if len(blob) < 6:
        return None
    try:
        version = int(blob[:4].decode("ascii", "replace"))
    except ValueError:
        return None
    table = NIKON_AF_AREA_MODES_Z if version >= 300 else NIKON_AF_AREA_MODES_DSLR
    return _mode_name(table, blob[5])


def af_area_mode(path: Path) -> str | None:
    """The name of the AF area mode the camera recorded - "Mode N" for a
    value with no verified name. None if it cannot be read at all.

    Sony is a plain-text MakerNote tag; Canon and Nikon are a different
    offset in the same AFInfo2 blob af_preview_box opens. For JPEG the
    manufacturer is split on Make.
    """
    suffix = path.suffix.lower()

    if suffix == ".arw":
        def sony_reader(buf):
            header = _tiff_header(buf)
            if header is None:
                return None
            endian, first = header
            maker_off = _maker_note_offset(buf, endian, first)
            if maker_off is None or maker_off + 2 > len(buf):
                return None
            (count,) = struct.unpack_from(endian + "H", buf, maker_off)
            start = maker_off if 0 < count < 512 else maker_off + 12
            maker = _read_ifd(buf, start, endian)
            mode = None
            entry = maker.get(0x201C)
            if entry is not None and entry[2]:
                mode = _mode_name(SONY_AF_AREA_MODES, entry[2][0])
            tracking = None
            entry = maker.get(0x2021)
            if entry is not None and entry[2] and entry[2][0]:
                # 0 is "not tracking" - no word for it. Another value we
                # have no name for is still tracking of some kind.
                tracking = SONY_AF_TRACKING.get(entry[2][0]) or f"Tracking {entry[2][0]}"
            if mode and tracking:
                return f"{mode} + {tracking}"
            return mode or tracking

        return _with_header(path, sony_reader)

    if suffix == ".nef":
        def nef_reader(buf):
            header = _tiff_header(buf)
            if header is None:
                return None
            endian, first = header
            maker_off = _maker_note_offset(buf, endian, first)
            if maker_off is None or buf[maker_off:maker_off + 5] != b"Nikon":
                return None
            block_base = maker_off + 10
            sub_header = _tiff_header(buf, block_base)
            if sub_header is None:
                return None
            sub_endian, sub_first = sub_header
            maker = _read_ifd(buf, sub_first, sub_endian, base=block_base)
            if 0x00B7 not in maker:
                return None
            return _nikon_mode_from_blob(maker[0x00B7][2])

        return _with_header(path, nef_reader)

    if suffix == ".cr3":
        blob = _cr3_afinfo2_blob(path)
        if blob is None or len(blob[0]) < 4:
            return None
        payload, endian = blob
        return _mode_name(CANON_AF_AREA_MODES,
                          struct.unpack_from(endian + "H", payload, 2)[0])

    if suffix in JPEG_SUFFIXES:
        def jpeg_reader(buf):
            base = _jpeg_exif_base(buf)
            if base is None:
                return None
            header = _tiff_header(buf, base)
            if header is None:
                return None
            endian, first = header
            ifd0 = _read_ifd(buf, first, endian, base)
            make = b""
            if 0x010F in ifd0:
                make = ifd0[0x010F][2].split(b"\x00")[0].upper()
            maker_off = _maker_note_offset(buf, endian, first, base)
            if maker_off is None:
                return None
            if make.startswith(b"CANON"):
                maker = _read_ifd(buf, maker_off - base, endian, base)
                entry = maker.get(0x0026)
                if entry is None or len(entry[2]) < 4:
                    return None
                return _mode_name(CANON_AF_AREA_MODES,
                                  struct.unpack_from(endian + "H", entry[2], 2)[0])
            if make.startswith(b"NIKON"):
                if buf[maker_off:maker_off + 5] != b"Nikon":
                    return None
                block_base = maker_off + 10
                sub_header = _tiff_header(buf, block_base)
                if sub_header is None:
                    return None
                sub_endian, sub_first = sub_header
                maker = _read_ifd(buf, sub_first, sub_endian, base=block_base)
                if 0x00B7 not in maker:
                    return None
                return _nikon_mode_from_blob(maker[0x00B7][2])
            return None

        return _with_header(path, jpeg_reader)

    return None

#: Sony 0x2027 gives only a point. The hint box is built at this ratio of
#: the preview's long edge. It is set more generously than the A6700's zone
#: display box (FocusFrameSize 135px ~ 2.2% of the long edge) so that the
#: subject still falls inside the box when the zone centre is a little off.
SONY_POINT_BOX_RATIO = 0.08


def af_preview_box(path: Path, orientation: int,
                   preview_w: int, preview_h: int
                   ) -> tuple[int, int, int, int] | None:
    """Returns the AF position as **(x, y, w, h) in preview pixel
    coordinates**.

    Sony: point -> a SONY_POINT_BOX_RATIO box; Nikon: the recorded box as
    it is. The EXIF orientation (portrait shooting) is applied and the
    result is clamped to the preview size. An unsupported format, a missing
    tag or bad coordinates all give None.
    """
    suffix = path.suffix.lower()
    if suffix == ".arw":
        location = sony_focus_location(path)
        if location is None:
            return None
        img_w, img_h, ax, ay, frame_w, frame_h = location
        if frame_w and frame_h:
            box_w, box_h = float(frame_w), float(frame_h)   # measured (0x2037)
        else:
            side = max(img_w, img_h) * SONY_POINT_BOX_RATIO  # older bodies
            box_w = box_h = side
        area = (ax, ay, box_w, box_h, img_w, img_h)
    elif suffix == ".nef":
        area = nikon_af_area(path)
    elif suffix == ".cr3":
        # CR2 is not included because there is no real file to verify
        # against - unverified support is support that is quietly wrong.
        area = canon_af_area(path)
    elif suffix in JPEG_SUFFIXES:
        # A JPEG the camera produced itself carries the same MakerNote. A
        # JPEG exported by an editor usually has no MakerNote, so it
        # naturally gives None.
        area = jpeg_af_area(path)
    else:
        return None
    if area is None:
        return None

    ax, ay, aw, ah, full_w, full_h = area
    cx, cy, bw, bh = _rotate_box(ax, ay, aw, ah, full_w, full_h, orientation)
    base_w, base_h = (full_h, full_w) if _rotates_frame(orientation) else (full_w, full_h)
    if not (base_w and base_h):
        return None
    scale_x = preview_w / base_w
    scale_y = preview_h / base_h
    x = int(round((cx - bw / 2) * scale_x))
    y = int(round((cy - bh / 2) * scale_y))
    w = int(round(bw * scale_x))
    h = int(round(bh * scale_y))

    x = max(0, min(preview_w - 1, x))
    y = max(0, min(preview_h - 1, y))
    w = max(1, min(preview_w - x, w))
    h = max(1, min(preview_h - y, h))
    return x, y, w, h
