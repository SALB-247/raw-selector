"""LibRaw이 열지 못하는 니콘 NEF에서 필요한 값만 직접 읽습니다.

**화소는 디코드하지 않습니다.** 니콘 Z9의 고효율(HE/HE*) 압축은 intoPIX
TicoRAW이고, 그 안은 JPEG XS 마커 구조에 벤더 커스텀 프로파일
(Ppih=0x0000)입니다. raw 스트림 앞에 `CONTACT_INTOPIX_` 라는 문자열이
그대로 박혀 있습니다 — 라이선스 없이는 못 푼다는 뜻입니다. LibRaw·dcraw·
darktable·RawTherapee 모두 미지원이라 버전만 올린다고 되는 문제가 아닙니다.

그래도 **메타데이터는 평범한 TIFF**라 읽힙니다. 그래서 두 가지를 건집니다.

1. 화이트밸런스 — 없으면 보정 창의 색온도 조절이 아예 죽습니다.
2. 압축 방식 — "왜 안 열리는지"를 사용자에게 정확히 말해 주기 위해서.

Z9은 NEFCompression을 MakerNote(0x0093)가 아니라 SubIFD의 태그
51157(0xC7D5) 안에 중첩된 니콘 TIFF 블록의 0x000D에 둡니다.
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
"""LibRaw이 못 푸는 방식. intoPIX TicoRAW 기반이라 공개 디코더가 없습니다."""


def _read_ifd(data: bytes, offset: int, endian: str) -> dict[int, tuple]:
    """IFD 하나를 태그 → (형식, 개수, 값/오프셋)으로 읽습니다."""
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
    """태그 값의 실제 바이트. 4바이트를 넘으면 오프셋을 따라갑니다."""
    kind, number, position = entry
    sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8,
             11: 4, 12: 8}
    length = sizes.get(kind, 1) * number
    if length <= 4:
        return data[position:position + length]
    pointer = struct.unpack_from(endian + "I", data, position)[0]
    return data[pointer:pointer + length]


def _nikon_block(payload: bytes) -> tuple[bytes, int, str] | None:
    """니콘 중첩 TIFF 블록을 (버퍼, 첫 IFD 위치, 엔디안)으로 풉니다.

    **버퍼를 따로 잘라 내는 것이 핵심입니다.** 이 블록 안의 값 오프셋은
    파일 처음이 아니라 이 블록의 TIFF 헤더를 기준으로 합니다. 파일 전체
    버퍼에 그대로 대면 엉뚱한 자리를 읽습니다(실제로 그렇게 만들었다가
    정답 대조에서 걸렸습니다 — 화이트밸런스가 전혀 다른 값이었습니다).
    """
    if payload[:6] != b"Nikon\x00":
        return None
    inner = payload[10:]          # "Nikon\0" + 버전 2 + 패딩 2
    if inner[:2] not in (b"II", b"MM"):
        return None
    endian = "<" if inner[:2] == b"II" else ">"
    first = struct.unpack_from(endian + "I", inner, 4)[0]
    return inner, first, endian


def _maker_note_payload(data: bytes, endian: str, base: int) -> bytes | None:
    """MakerNote 원문 바이트를 그대로 잘라 옵니다."""
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
"""니콘의 '0100' 형식 태그는 앞 4바이트가 버전 문자열입니다.

이걸 빼먹으면 값 대신 문자 '0'(0x30 = 48)을 읽습니다. 실제로 압축 방식이
전부 48로 나왔습니다.
"""


def read_white_balance_levels(path: Path) -> tuple[float, float, float] | None:
    """니콘 MakerNote 0x000C(WB_RBGGLevels)에서 R/G/B 배수를 읽습니다.

    LibRaw이 파일을 아예 못 여는 경우에도 이건 읽힙니다. LibRaw이 여는
    파일로 대조했을 때 `camera_whitebalance`와 값이 일치했습니다.
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
        # RATIONAL 4개: R, B, G1, G2 (니콘 WB_RBGGLevels)
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
    """NEFCompression 값. Z9은 SubIFD의 0xC7D5 안 0x000D에 둡니다."""
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
            # 앞 4바이트는 "0100" 버전 문자열이고 그 뒤가 실제 값입니다
            if len(value) < VERSION_PREFIX + 2:
                continue
            return int(struct.unpack_from(
                inner_endian + "H", value, VERSION_PREFIX)[0])
    except (struct.error, IndexError):
        log.debug("%s: NEF 압축 방식 읽기 실패", path.name, exc_info=True)
    return None


def unsupported_reason(path: Path) -> str | None:
    """이 파일을 왜 못 여는지 한 줄로. 알 수 없으면 None.

    예전에는 LibRaw 원문 `Unsupported file format or not RAW file`을 그대로
    보여 줬습니다. 파일은 멀쩡한 RAW이라 오해만 삽니다.
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
