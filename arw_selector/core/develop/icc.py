"""Export colour space - pixel conversion and ICC profile embedding.

**Attach only the tag and the colour goes wrong.** Exporting as Adobe RGB
means both turning the pixels into that space's numbers and writing in the
file that they were turned. Do only one and the viewer reads sRGB numbers
as Adobe RGB, or the other way round.

## Which space we convert from

Our working values (0~255) are display values that have been through the
decoder gamma and the profile curve, and they are drawn on screen
**interpreted as sRGB**. So a conversion that means "to Adobe RGB with the
appearance preserved" is right to set out from sRGB - not from the
composite curve (engine._baseline_transfer). That one is for when exposure
changes the amount of light; this is the job of moving "the colour you see
right now" onto different numbers.

## How the ICC goes in

Re-saving through a library damages the pixels - measured: re-saving a
JPEG with PIL recompresses it from 14,880 to 6,895 bytes and moves pixels
by up to 14 levels. 16-bit PNG and TIFF drop to 8 bits outright.

So **the encoded bytes are left untouched and only a segment, chunk or tag
is slipped in.** Measured, all three formats were confirmed to match the
original pixel for pixel with the ICC readable.

WebP is not supported. Getting an ICC in means turning the RIFF into the
extended form (VP8X), and that is a lot of work next to what this format
is used for. The caller locks the colour space choice.
"""

from __future__ import annotations

import logging
import struct
import zlib
from functools import lru_cache
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

#: (primary xy, gamma) per colour space. The D65 white point is common.
#:
#: Adobe RGB (1998) differs from sRGB in green alone - R and B are the
#: same and G is (0.21, 0.71) instead of (0.30, 0.60), which is exactly
#: how much wider its gamut is.
_PRIMARIES = {
    "srgb": ((0.6400, 0.3300), (0.3000, 0.6000), (0.1500, 0.0600)),
    "adobe_rgb": ((0.6400, 0.3300), (0.2100, 0.7100), (0.1500, 0.0600)),
    # ROMM RGB (ProPhoto). Its green and blue primaries are **outside**
    # the spectral locus, so it holds colours that do not physically
    # exist - which is why it carries almost everything the camera
    # caught (measured: it recovers 98% of the pixels clipped in sRGB,
    # against 61% for Adobe RGB).
    "prophoto": ((0.7347, 0.2653), (0.1596, 0.8404), (0.0366, 0.0001)),
}
_D65 = (0.3127, 0.3290)
_D50 = (0.3457, 0.3585)

#: The colour space's white point. **ProPhoto is D50.** Leave the
#: chromatic adaptation out when moving between spaces with different
#: white points and grey does not stay grey - a colour cast runs over the
#: whole screen.
_WHITE = {"srgb": _D65, "adobe_rgb": _D65, "prophoto": _D50}

#: ICC's connection space (PCS) is **fixed at D50**. The capture colour
#: space is D65, so the XYZ written into the profile has to be adapted to
#: D50 with Bradford.
#:
#: Leave this out and libpng warns "PCS illuminant is not D50" and, above
#: all, the littleCMS round-trip error opens up to 88 levels (measured).
#: The pixel conversion itself is D65->D65 and needs no adaptation - this
#: is used in the profile file only. It is a value the specification
#: nailed down, so it differs slightly from what the `_D50` xy above
#: computes (solving from xy gives 0.9643, 1, 0.8251 - 2e-4). The numbers
#: written into the profile must be exactly these, so they are left as
#: they are rather than derived.
_D50_XYZ = np.array([0.9642, 1.0000, 0.8249])
_BRADFORD = np.array([
    [0.8951, 0.2664, -0.1614],
    [-0.7502, 1.7135, 0.0367],
    [0.0389, -0.0685, 1.0296]])

#: Adobe RGB's transfer function is the pure power 563/256 = 2.19921875
#: (there is no straight segment at the dark end as sRGB has).
_ADOBE_GAMMA = 563.0 / 256.0


def _xy_to_xyz(x: float, y: float) -> np.ndarray:
    return np.array([x / y, 1.0, (1.0 - x - y) / y])


def _rgb_to_xyz(space: str) -> np.ndarray:
    """Build the RGB->XYZ matrix from the primaries and **that space's
    white point**."""
    red, green, blue = _PRIMARIES[space]
    matrix = np.stack([_xy_to_xyz(*red), _xy_to_xyz(*green),
                       _xy_to_xyz(*blue)], axis=1)
    scale = np.linalg.solve(matrix, _xy_to_xyz(*_WHITE[space]))
    return matrix * scale


def _bradford(source_xyz: np.ndarray, target_xyz: np.ndarray) -> np.ndarray:
    """The matrix that moves XYZ from one white point onto another."""
    source = _BRADFORD @ source_xyz
    target = _BRADFORD @ target_xyz
    return np.linalg.inv(_BRADFORD) @ np.diag(target / source) @ _BRADFORD


def _adapt_to_d50(matrix: np.ndarray) -> np.ndarray:
    """The RGB->XYZ matrix onto D50 (needed because ICC's PCS is fixed at
    D50).

    A space that is already D50 (ProPhoto) is left alone - apply it once
    more and it goes wrong.
    """
    white = matrix @ np.ones(3)
    if np.allclose(white, _D50_XYZ, atol=1e-3):
        return matrix
    return _bradford(white, _D50_XYZ) @ matrix


def convert_space(rgb_linear: np.ndarray, source: str, target: str) -> np.ndarray:
    """Linear RGB into another colour space's linear RGB. Applies the
    chromatic adaptation when the white points differ.

    **Grey staying grey is this function's minimum condition.** Between
    spaces with different white points (ProPhoto is D50, sRGB is D65),
    multiplying by the matrix alone without adaptation twists the neutral
    axis and a colour cast runs over the whole screen.
    """
    if source == target:
        return rgb_linear
    matrix = _space_matrix(source, target)
    if rgb_linear.dtype == np.float32:
        matrix = matrix.astype(np.float32)
    return rgb_linear @ matrix.T


@lru_cache(maxsize=16)
def _space_matrix(source: str, target: str) -> np.ndarray:
    matrix = _rgb_to_xyz(source)
    if _WHITE[source] != _WHITE[target]:
        matrix = _bradford(_xy_to_xyz(*_WHITE[source]),
                           _xy_to_xyz(*_WHITE[target])) @ matrix
    return np.linalg.inv(_rgb_to_xyz(target)) @ matrix



#: The colour space the adjustments happen in.
#:
#: The camera->working space conversion happens inside LibRaw - before
#: our code has even seen the pixels. So if this is narrow, the colours
#: clipped at that moment cannot be got back. Measured (_DSC5914.ARW):
#: taken as sRGB, 0.79% of the pixels are clipped outside the gamut, 88%
#: of those are orange and yellow, and the largest single blob is 24,478
#: pixels - one strongly lit surface is smeared out wholesale.
#:
#: Recovery rates: Adobe RGB 61%, Wide Gamut 92%, **ProPhoto 98%**.
#: ProPhoto's white point is D50, so moving to sRGB (D65) needs the
#: chromatic adaptation; convert_space handles it, and grey staying grey
#: is pinned down by a test.
#:
#: It must not be used at 8 bits - it holds wider colour in the same
#: number of levels, so the spacing per level opens up. It only holds
#: once the pipeline is float and the export supports 16 bits.
#:
#: **The transfer function is the sRGB curve, not ProPhoto's 1.8**
#: (_decode/_encode handle adobe_rgb separately and treat the rest as
#: sRGB). Our values are already rendered display-referred values
#: (BT.709 -> body correction -> profile curve), so there is no reason to
#: lay ProPhoto's encoding on top of them; the only thing that needs
#: widening is the primaries. Adobe uses the same combination too
#: (ProPhoto primaries + sRGB tone response = Melissa RGB).
#:
#: This convention does not leak outward - because we do **not export**
#: as ProPhoto. The export choices are sRGB and Adobe RGB only, and both
#: go out encoded with their own curve. So the only thing that ever sees
#: this assumption is our own code.
WORKING_SPACE = "prophoto"


def _decode(image: np.ndarray, space: str, peak: float) -> np.ndarray:
    """That space's display-value BGR -> linear RGB (0~1). **The curve
    differs per space.**"""
    rgb = np.clip(np.asarray(image, dtype=np.float32)[..., ::-1]
                  / np.float32(peak), 0.0, 1.0)
    if space == "adobe_rgb":
        return np.power(rgb, np.float32(_ADOBE_GAMMA))
    return np.where(rgb <= np.float32(0.04045), rgb / np.float32(12.92),
                    np.power((rgb + np.float32(0.055)) / np.float32(1.055),
                             np.float32(2.4)))


def _encode(linear: np.ndarray, space: str, peak: float) -> np.ndarray:
    """Linear RGB -> that space's display-value BGR."""
    value = np.clip(np.asarray(linear, dtype=np.float32), 0.0, 1.0)
    if space == "adobe_rgb":
        encoded = np.power(value, np.float32(1.0 / _ADOBE_GAMMA))
    else:
        encoded = np.where(
            value <= np.float32(0.0031308), value * np.float32(12.92),
            np.float32(1.055) * np.power(value, np.float32(1.0 / 2.4))
            - np.float32(0.055))
    return encoded[..., ::-1] * np.float32(peak)


def _move(image: np.ndarray, source: str, target: str) -> np.ndarray:
    """Display values into another space's display values. It flattens
    the curve, moves the primaries, and re-encodes with the target
    space's curve.

    **The computation is in float32.** The preview rides this conversion
    on every render, and in float64 it takes 189ms for one 1400px frame,
    doubling the render time (200ms). float32 is 101ms and the round-trip
    error stays 0.

    Looking it up nearest-neighbour in a 4096-slot table was measured too
    (71ms). It wins another 30ms but introduces a round-trip error of
    0.03 levels (0.66 at most) - next to a 200ms render 30ms is not felt,
    and a 16-bit export would carry that error as it is. Rejected.

    **If a NaN comes in, all three channels of that pixel turn to
    rubbish.** It is because the matrix mixes the channels. We do not
    block it - nan_to_num costs 11.3ms per render (11%), and a NaN
    arriving here means upstream is already broken, so quietly covering
    it with 0 hides the cause. Lens correction, the real source, detects
    and discards them in its own place (the isfinite check in
    optics.apply_auto_correction).

    **The spread is not that one pixel.** The convolution stages that
    follow scatter it to the neighbours. Measured by putting a single NaN
    at the centre of a 128x128 and comparing the final result:

        no adjustment / exposure / grain   1 pixel (1x1)
        dehaze 100                         1 pixel, three channels
        sharpen 100                        81 pixels (9x9)
        clarity 100                        625 pixels (25x25)
        noise reduction 100                828 pixels, all three channels

    Even so it is local (5% of 16,384 pixels) and stays black, so it
    stands out - the uint8 cast turns it into 0. The judgement "do not
    cover it up, expose it" stands, but it is now one made knowing the
    size.

    There is nowhere in the pipeline that **makes** a NaN. Standing numpy
    up with seterr(invalid='raise', divide='raise') and running 540
    random extreme settings through it gave zero of them.
    """
    if source == target:
        return image
    peak = 65535.0 if image.dtype == np.uint16 else 255.0
    linear = convert_space(_decode(image, source, peak), source, target)
    out = np.clip(_encode(linear, target, peak), 0.0, peak)
    return out.astype(image.dtype)


def to_working(image: np.ndarray, space: str) -> np.ndarray:
    """Move another space's display values into the **working space**.

    Used when reading in an original JPEG or HEIF. It used to move them
    to sRGB, but the destination changed once the working space stopped
    being sRGB.
    """
    return _move(image, space, WORKING_SPACE)


def working_to(image: np.ndarray, space: str) -> np.ndarray:
    """The working space's display values into the target space - the
    inverse of `to_working`.

    Used by the screen (always sRGB) and by the export.
    """
    return _move(image, WORKING_SPACE, space)


def to_srgb_from(image: np.ndarray, space: str) -> np.ndarray:
    """Another colour space's BGR values into sRGB values - the inverse
    of `convert_from_srgb`."""
    return _move(image, space, "srgb")


def convert_from_srgb(image: np.ndarray, space: str) -> np.ndarray:
    """BGR values interpreted as sRGB into the target colour space's BGR
    values.

    The dtype and the scale (8-bit 0~255 / 16-bit 0~65535) are kept as
    they are.
    """
    return _move(image, "srgb", space)


# ---------------------------------------------------------------- profile


def _tag(signature: bytes, payload: bytes) -> tuple[bytes, bytes]:
    return signature, payload


def _xyz_type(xyz: np.ndarray) -> bytes:
    return b"XYZ " + b"\x00" * 4 + b"".join(
        struct.pack(">i", int(round(v * 65536.0))) for v in xyz)


def _curve_type(gamma: float) -> bytes:
    # curveType, 1 entry = a u8Fixed8Number gamma
    return (b"curv" + b"\x00" * 4 + struct.pack(">I", 1)
            + struct.pack(">H", int(round(gamma * 256.0))))


def _text_type(text: str) -> bytes:
    raw = text.encode("ascii", "replace") + b"\x00"
    return (b"desc" + b"\x00" * 4 + struct.pack(">I", len(raw)) + raw
            + b"\x00" * (12 + 67 + 3))


def build_profile(space: str) -> bytes:
    """Build a minimal matrix/TRC ICC v2 profile ourselves.

    PIL's ImageCms only makes sRGB and does not take primaries. Adobe
    RGB's primaries and gamma are a published specification, so it is
    composed directly from those numbers - the profile **file** Adobe
    distributes carries redistribution conditions, so it is not shipped.
    That is also why the name is a compatibility wording rather than
    "Adobe RGB (1998)".
    """
    if space == "srgb":
        try:
            from PIL import ImageCms

            return ImageCms.ImageCmsProfile(
                ImageCms.createProfile("sRGB")).tobytes()
        except Exception as exc:  # noqa: BLE001 - falls back to building below
            log.debug("ImageCms sRGB 생성 실패, 직접 구성합니다: %s", exc)

    # The values written into the profile are PCS (D50) based - see
    # _adapt_to_d50.
    matrix = _adapt_to_d50(_rgb_to_xyz(space))
    gamma = _ADOBE_GAMMA if space == "adobe_rgb" else 2.2
    name = ("Compatible with Adobe RGB (1998)" if space == "adobe_rgb"
            else "sRGB")

    tags = [
        _tag(b"desc", _text_type(name)),
        _tag(b"wtpt", _xyz_type(_D50_XYZ)),
        _tag(b"rXYZ", _xyz_type(matrix[:, 0])),
        _tag(b"gXYZ", _xyz_type(matrix[:, 1])),
        _tag(b"bXYZ", _xyz_type(matrix[:, 2])),
        _tag(b"rTRC", _curve_type(gamma)),
        _tag(b"gTRC", _curve_type(gamma)),
        _tag(b"bTRC", _curve_type(gamma)),
        _tag(b"cprt", _text_type("Public Domain")),
    ]

    table_size = 4 + len(tags) * 12
    offset = 128 + table_size
    table, body = b"", b""
    for signature, payload in tags:
        padded = payload + b"\x00" * (-len(payload) % 4)
        table += signature + struct.pack(">II", offset + len(body), len(payload))
        body += padded
    table = struct.pack(">I", len(tags)) + table

    total = 128 + len(table) + len(body)
    header = (
        struct.pack(">I", total)          # size
        + b"none" + struct.pack(">I", 0x02100000)   # CMM, version 2.1
        + b"mntr" + b"RGB " + b"XYZ "     # class, data, connection space
        + b"\x00" * 12                    # creation date (0 = unspecified)
        + b"acsp" + b"MSFT" + struct.pack(">I", 0)
        + b"none" + b"none"               # manufacturer, model
        + b"\x00" * 8                     # attributes
        + struct.pack(">I", 0)            # rendering intent = perceptual
        + b"".join(struct.pack(">i", int(round(v * 65536.0)))
                   for v in _D50_XYZ)     # PCS illuminant - D50 per spec
        + b"none"                         # creator
        + b"\x00" * 44
    )
    assert len(header) == 128, len(header)
    return header + table + body


# ---------------------------------------------------------------- embedding


def _embed_jpeg(data: bytes, blob: bytes) -> bytes:
    """As an APP2 segment. There is a per-segment ceiling, so a large
    profile is split across several."""
    if data[:2] != b"\xff\xd8":
        raise ValueError("JPEG이 아닙니다")
    room = 65533 - 16
    chunks = [blob[i:i + room] for i in range(0, len(blob), room)] or [b""]
    payload = b""
    for index, chunk in enumerate(chunks, start=1):
        body = b"ICC_PROFILE\x00" + bytes([index, len(chunks)]) + chunk
        payload += b"\xff\xe2" + struct.pack(">H", len(body) + 2) + body

    at = 2
    if data[2:4] == b"\xff\xe0":          # after the JFIF, if there is one
        (length,) = struct.unpack_from(">H", data, 4)
        at = 4 + length
    return data[:at] + payload + data[at:]


def _embed_png(data: bytes, blob: bytes) -> bytes:
    """An iCCP chunk, after IHDR."""
    at = data.index(b"IHDR") - 4
    (length,) = struct.unpack_from(">I", data, at)
    end = at + 4 + 4 + length + 4
    payload = b"ICCProfile\x00\x00" + zlib.compress(blob)
    chunk = (struct.pack(">I", len(payload)) + b"iCCP" + payload
             + struct.pack(">I", zlib.crc32(b"iCCP" + payload) & 0xFFFFFFFF))
    return data[:end] + chunk + data[end:]


def _embed_tiff(data: bytes, blob: bytes) -> bytes:
    """Add the ICCProfile tag (34675).

    The IFD is rewritten at the end of the file and the header's first
    IFD offset is pointed there - no existing entry has to be pushed
    along, and the pixel data is not touched.
    """
    buffer = bytearray(data)
    endian = "<" if buffer[:2] == b"II" else ">"
    (first,) = struct.unpack_from(endian + "I", buffer, 4)
    (count,) = struct.unpack_from(endian + "H", buffer, first)
    entries = [bytes(buffer[first + 2 + i * 12:first + 2 + (i + 1) * 12])
               for i in range(count)]
    (next_ifd,) = struct.unpack_from(endian + "I", buffer, first + 2 + count * 12)

    profile_at = len(buffer)
    buffer.extend(blob)
    if len(buffer) % 2:
        buffer.extend(b"\x00")
    entries.append(struct.pack(endian + "HHII", 34675, 7, len(blob), profile_at))
    entries.sort(key=lambda e: struct.unpack_from(endian + "H", e, 0)[0])

    ifd_at = len(buffer)
    buffer.extend(struct.pack(endian + "H", len(entries)))
    for entry in entries:
        buffer.extend(entry)
    buffer.extend(struct.pack(endian + "I", next_ifd))
    struct.pack_into(endian + "I", buffer, 4, ifd_at)
    return bytes(buffer)


#: Only these formats can take an ICC without touching the pixels.
EMBEDDABLE = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def embed(path: Path, space: str) -> bool:
    """Slip the colour space profile into a saved file. True if it went
    in.

    **It does not raise on failure.** This is in the middle of an export,
    so a batch must not stop over the tag of a single file - it logs
    instead. The pixels are already converted to the target space and
    saved, so with the tag missing the viewer reads them as sRGB and the
    colour looks different (not something to pass over quietly).
    """
    suffix = path.suffix.lower()
    if suffix not in EMBEDDABLE:
        return False
    try:
        data = path.read_bytes()
        blob = build_profile(space)
        if suffix in (".jpg", ".jpeg"):
            out = _embed_jpeg(data, blob)
        elif suffix == ".png":
            out = _embed_png(data, blob)
        else:
            out = _embed_tiff(data, blob)
        path.write_bytes(out)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: 색 프로파일을 넣지 못했습니다 — 뷰어가 sRGB로 "
                    "읽어 색이 달라 보일 수 있습니다 (%s)", path.name, exc)
        return False
