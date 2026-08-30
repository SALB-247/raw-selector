"""Reads just the values we need straight out of Nikon NEFs LibRaw cannot
open.

**The pixels are not decoded.** The Nikon Z9's high-efficiency (HE/HE*)
compression is intoPIX TicoRAW, and inside is a JPEG XS marker structure
with a vendor custom profile (Ppih=0x0000). The string
`CONTACT_INTOPIX_` sits verbatim in front of the raw stream - meaning it
cannot be unpacked without a licence. LibRaw, dcraw, darktable and
RawTherapee are all unsupported, so this is not a problem a version bump
solves.

Even so, **the metadata is ordinary TIFF** and reads fine. So we salvage
two things.

1. White balance - without it the colour temperature control in the
   adjustment window is dead outright.
2. The compression method - so we can tell the user exactly "why it will
   not open".

The Z9 puts NEFCompression not in the MakerNote (0x0093) but at 0x000D of
a Nikon TIFF block nested inside SubIFD tag 51157 (0xC7D5).
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path

log = logging.getLogger(__name__)

NEF_COMPRESSION_NAMES = {
    1: "손실 압축(type 1)",
    2: "무압축",
    3: "무손실 압축",
    4: "손실 압축(type 2)",
    5: "스트라이프 12비트",
    6: "무압축 14비트",
    7: "언팩 12비트",
    8: "스몰 raw",
    9: "팩 12비트",
    13: "고효율(HE)",
    14: "고효율(HE*)",
}

UNSUPPORTED_COMPRESSIONS = (13, 14)
"""The methods LibRaw cannot unpack. Being intoPIX TicoRAW based, there is
no public decoder."""


def _read_ifd(data: bytes, offset: int, endian: str) -> dict[int, tuple]:
    """Reads one IFD as tag -> (kind, count, value/offset)."""
    entries: dict[int, tuple] = {}
    if offset + 2 > len(data):
        return entries
    count = struct.unpack_from(endian + "H", data, offset)[0]
    for index in range(count):
        base = offset + 2 + index * 12
        if base + 12 > len(data):
            break
        tag, kind, number = struct.unpack_from(endian + "HHI", data, base)
        entries[tag] = (kind, number, base + 8)
    return entries


def _value_bytes(data: bytes, entry: tuple, endian: str) -> bytes:
    """The actual bytes of a tag value. Past 4 bytes it follows the
    offset."""
    kind, number, position = entry
    sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8,
             11: 4, 12: 8}
    length = sizes.get(kind, 1) * number
    if length <= 4:
        return data[position:position + length]
    pointer = struct.unpack_from(endian + "I", data, position)[0]
    return data[pointer:pointer + length]


def _nikon_block(payload: bytes) -> tuple[bytes, int, str] | None:
    """Unpacks the Nikon nested TIFF block into (buffer, first IFD
    position, endianness).

    **Slicing the buffer out separately is the whole point.** Value offsets
    inside this block are relative to this block's TIFF header, not to the
    start of the file. Apply them to the whole-file buffer as they are and
    you read the wrong place (it really was built that way once and got
    caught against the ground truth - the white balance was a completely
    different value).
    """
    if payload[:6] != b"Nikon\x00":
        return None
    inner = payload[10:]          # "Nikon\0" + 2 version + 2 padding
    if inner[:2] not in (b"II", b"MM"):
        return None
    endian = "<" if inner[:2] == b"II" else ">"
    first = struct.unpack_from(endian + "I", inner, 4)[0]
    return inner, first, endian


def _maker_note_payload(data: bytes, endian: str, base: int) -> bytes | None:
    """Slices out the raw MakerNote bytes as they are."""
    root = _read_ifd(data, base, endian)
    if 0x8769 not in root:
        return None
    exif_ifd = struct.unpack_from(endian + "I", data, root[0x8769][2])[0]
    exif = _read_ifd(data, exif_ifd, endian)
    if 0x927C not in exif:
        return None
    _kind, number, position = exif[0x927C]
    start = struct.unpack_from(endian + "I", data, position)[0]
    return data[start:start + number]


VERSION_PREFIX = 4
"""Nikon's '0100' style tags carry a version string in their first 4 bytes.

Miss it and you read the character '0' (0x30 = 48) instead of the value.
The compression method really did come out as 48 for everything.
"""


def read_white_balance_levels(path: Path) -> tuple[float, float, float] | None:
    """Reads the R/G/B multipliers from Nikon MakerNote 0x000C
    (WB_RBGGLevels).

    This reads even when LibRaw cannot open the file at all. Checked
    against a file LibRaw does open, the values matched
    `camera_whitebalance`.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return None

    if len(data) < 8:
        return None
    endian = "<" if data[:2] == b"II" else ">" if data[:2] == b"MM" else None
    if endian is None:
        return None

    try:
        base = struct.unpack_from(endian + "I", data, 4)[0]
        payload = _maker_note_payload(data, endian, base)
        if payload is None:
            return None
        block = _nikon_block(payload)
        if block is None:
            return None
        buffer, first, maker_endian = block
        maker = _read_ifd(buffer, first, maker_endian)
        if 0x000C not in maker:
            return None
        raw = _value_bytes(buffer, maker[0x000C], maker_endian)
        # 4 RATIONALs: R, B, G1, G2 (Nikon WB_RBGGLevels)
        if len(raw) < 32:
            return None
        values = []
        for index in range(4):
            num, den = struct.unpack_from(maker_endian + "II", raw, index * 8)
            values.append(num / den if den else 0.0)
        red, blue, green1, green2 = values
        if red <= 0 or blue <= 0:
            return None
        green = green1 if green1 > 0 else (green2 or 1.0)
        return (red, green, blue)
    except (struct.error, IndexError, ZeroDivisionError):
        log.debug("%s: 니콘 WB 읽기 실패", path.name, exc_info=True)
        return None


def read_compression(path: Path) -> int | None:
    """The NEFCompression value. The Z9 puts it at 0x000D inside SubIFD
    0xC7D5."""
    try:
        data = path.read_bytes()
    except OSError:
        return None

    if len(data) < 8:
        return None
    endian = "<" if data[:2] == b"II" else ">" if data[:2] == b"MM" else None
    if endian is None:
        return None

    try:
        base = struct.unpack_from(endian + "I", data, 4)[0]
        root = _read_ifd(data, base, endian)
        if 0x014A not in root:      # SubIFDs
            return None
        kind, number, position = root[0x014A]
        pointers = []
        if number == 1:
            pointers.append(struct.unpack_from(endian + "I", data, position)[0])
        else:
            table = struct.unpack_from(endian + "I", data, position)[0]
            for index in range(number):
                pointers.append(
                    struct.unpack_from(endian + "I", data, table + index * 4)[0])

        for pointer in pointers:
            sub = _read_ifd(data, pointer, endian)
            if 0xC7D5 not in sub:
                continue
            payload = _value_bytes(data, sub[0xC7D5], endian)
            block = _nikon_block(payload)
            if block is None:
                continue
            buffer, first, inner_endian = block
            entries = _read_ifd(buffer, first, inner_endian)
            if 0x000D not in entries:
                continue
            value = _value_bytes(buffer, entries[0x000D], inner_endian)
            # the first 4 bytes are the "0100" version string; the real
            # value follows them
            if len(value) < VERSION_PREFIX + 2:
                continue
            return int(struct.unpack_from(
                inner_endian + "H", value, VERSION_PREFIX)[0])
    except (struct.error, IndexError):
        log.debug("%s: NEF 압축 방식 읽기 실패", path.name, exc_info=True)
    return None


def unsupported_reason(path: Path) -> str | None:
    """One line on why this file cannot be opened. None if it is unknown.

    It used to show LibRaw's own `Unsupported file format or not RAW file`
    as it was. The file is a perfectly good RAW, so that only invites
    misunderstanding.
    """
    if path.suffix.lower() != ".nef":
        return None
    compression = read_compression(path)
    if compression is None:
        return None
    name = NEF_COMPRESSION_NAMES.get(compression, f"방식 {compression}")
    if compression in UNSUPPORTED_COMPRESSIONS:
        return (f"니콘 {name} 압축입니다. 이 방식은 제조사 독점 규격이라"
                " RAW 디코더가 풀 수 없습니다 — 내장 JPEG으로 표시합니다.")
    return None
