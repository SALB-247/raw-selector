"""내보내기 색공간 — 화소 변환과 ICC 프로파일 임베드.

**태그만 붙이면 색이 틀어집니다.** Adobe RGB로 내보낸다는 것은 화소를
그 공간의 숫자로 바꾸고, 그렇게 바꿨다고 파일에 적는 것 둘 다입니다.
하나만 하면 뷰어가 sRGB 숫자를 Adobe RGB로 읽거나 그 반대가 됩니다.

## 어느 공간에서 변환하는가

우리 작업값(0~255)은 디코더 감마와 프로파일 곡선을 지난 표시용 값이고,
화면에는 **sRGB로 해석되어** 그려집니다. 그래서 "겉보기를 보존한 채
Adobe RGB로" 라는 뜻의 변환은 sRGB에서 출발해야 맞습니다 — 합성 곡선
(engine._baseline_transfer)이 아닙니다. 그쪽은 노출이 빛의 양을 바꿀 때
쓰는 것이고, 여기는 "지금 보이는 색"을 다른 숫자로 옮기는 일입니다.

## ICC를 어떻게 넣는가

라이브러리로 다시 저장하면 화소가 상합니다 — 실측: PIL로 JPEG을 다시
저장하면 14,880 → 6,895바이트로 재압축되고 화소가 최대 14레벨 달라집니다.
16비트 PNG·TIFF는 아예 8비트로 떨어집니다.

그래서 **인코딩된 바이트는 손대지 않고 세그먼트·청크·태그만 끼워
넣습니다.** 실측으로 세 형식 모두 화소가 원본과 완전히 일치하고 ICC가
읽히는 것을 확인했습니다.

WebP는 지원하지 않습니다. ICC를 넣으려면 RIFF를 확장 형식(VP8X)으로
바꿔야 하는데, 그 손이 이 형식의 쓰임에 비해 큽니다. 호출부가 색공간
선택을 잠급니다.
"""

from __future__ import annotations

import logging
import struct
import zlib
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

#: 색공간별 (프라이머리 xy, 감마). D65 백점은 공통입니다.
#:
#: Adobe RGB (1998)은 초록만 sRGB와 다릅니다 — R·B는 같고 G가
#: (0.30, 0.60) 대신 (0.21, 0.71)이라 그만큼 색역이 넓습니다.
_PRIMARIES = {
    "srgb": ((0.6400, 0.3300), (0.3000, 0.6000), (0.1500, 0.0600)),
    "adobe_rgb": ((0.6400, 0.3300), (0.2100, 0.7100), (0.1500, 0.0600)),
}
_D65 = (0.3127, 0.3290)

#: ICC의 연결 공간(PCS)은 **D50으로 고정**입니다. 촬영 색공간은 D65이므로
#: 프로파일에 적는 XYZ는 Bradford로 D50에 적응시킨 값이어야 합니다.
#:
#: 이걸 빼먹으면 libpng이 "PCS illuminant is not D50"으로 경고하고, 무엇보다
#: littleCMS 왕복 오차가 최대 88레벨까지 벌어집니다(실측). 화소 변환
#: 자체는 D65→D65라 적응이 필요 없습니다 — 프로파일 파일에만 씁니다.
_D50_XYZ = np.array([0.9642, 1.0000, 0.8249])
_BRADFORD = np.array([
    [0.8951, 0.2664, -0.1614],
    [-0.7502, 1.7135, 0.0367],
    [0.0389, -0.0685, 1.0296]])

#: Adobe RGB의 전달함수는 순수 거듭제곱 563/256 = 2.19921875입니다
#: (sRGB처럼 어두운 쪽 직선 구간이 없습니다).
_ADOBE_GAMMA = 563.0 / 256.0


def _xy_to_xyz(x: float, y: float) -> np.ndarray:
    return np.array([x / y, 1.0, (1.0 - x - y) / y])


def _rgb_to_xyz(space: str) -> np.ndarray:
    """프라이머리와 백점에서 RGB→XYZ 행렬을 만듭니다 (Bradford 없이 D65)."""
    red, green, blue = _PRIMARIES[space]
    matrix = np.stack([_xy_to_xyz(*red), _xy_to_xyz(*green),
                       _xy_to_xyz(*blue)], axis=1)
    scale = np.linalg.solve(matrix, _xy_to_xyz(*_D65))
    return matrix * scale


def _adapt_to_d50(matrix: np.ndarray) -> np.ndarray:
    """D65 기준 RGB→XYZ 행렬을 D50 기준으로 (Bradford)."""
    source = _BRADFORD @ _xy_to_xyz(*_D65)
    target = _BRADFORD @ _D50_XYZ
    adapt = np.linalg.inv(_BRADFORD) @ np.diag(target / source) @ _BRADFORD
    return adapt @ matrix


def srgb_to_linear(value: np.ndarray) -> np.ndarray:
    value = np.asarray(value, dtype=np.float64)
    return np.where(value <= 0.04045, value / 12.92,
                    np.power((np.abs(value) + 0.055) / 1.055, 2.4))


def _srgb_encode(value: np.ndarray) -> np.ndarray:
    value = np.clip(np.asarray(value, dtype=np.float64), 0.0, None)
    return np.where(value <= 0.0031308, value * 12.92,
                    1.055 * np.power(value, 1.0 / 2.4) - 0.055)


def to_srgb_from(image: np.ndarray, space: str) -> np.ndarray:
    """다른 색공간의 BGR 값을 sRGB 값으로 — `convert_from_srgb`의 역.

    **원본을 읽어 올 때** 씁니다. Adobe RGB로 찍은 JPEG을 그대로 두면
    화면(Qt는 무조건 sRGB로 그립니다)에서 채도가 빠져 보이고, 노출도
    어긋난 곡선으로 되돌리게 됩니다.

    float 입력(보정 베이스라인)은 0~255 눈금을 지킵니다.
    """
    if space == "srgb":
        return image

    if image.dtype == np.uint16:
        peak = 65535.0
    else:
        peak = 255.0
    rgb = np.asarray(image, dtype=np.float64)[..., ::-1] / peak
    linear = np.power(np.clip(rgb, 0.0, None), _ADOBE_GAMMA)
    matrix = np.linalg.inv(_rgb_to_xyz("srgb")) @ _rgb_to_xyz(space)
    converted = np.clip(linear @ matrix.T, 0.0, 1.0)
    out = np.clip(_srgb_encode(converted)[..., ::-1] * peak, 0.0, peak)
    return out.astype(image.dtype)


def convert_from_srgb(image: np.ndarray, space: str) -> np.ndarray:
    """sRGB로 해석되는 BGR 값을 목표 색공간의 BGR 값으로.

    dtype과 눈금(8비트 0~255 / 16비트 0~65535)을 그대로 지킵니다.
    """
    if space == "srgb":
        return image

    peak = 65535.0 if image.dtype == np.uint16 else 255.0
    rgb = np.asarray(image, dtype=np.float64)[..., ::-1] / peak
    matrix = np.linalg.inv(_rgb_to_xyz(space)) @ _rgb_to_xyz("srgb")
    linear = np.clip(srgb_to_linear(rgb) @ matrix.T, 0.0, 1.0)
    encoded = np.power(linear, 1.0 / _ADOBE_GAMMA)
    out = np.clip(encoded[..., ::-1] * peak, 0.0, peak)
    return out.astype(image.dtype)


# ---------------------------------------------------------------- 프로파일


def _tag(signature: bytes, payload: bytes) -> tuple[bytes, bytes]:
    return signature, payload


def _xyz_type(xyz: np.ndarray) -> bytes:
    return b"XYZ " + b"\x00" * 4 + b"".join(
        struct.pack(">i", int(round(v * 65536.0))) for v in xyz)


def _curve_type(gamma: float) -> bytes:
    # curveType, 항목 1개 = u8Fixed8Number 감마
    return (b"curv" + b"\x00" * 4 + struct.pack(">I", 1)
            + struct.pack(">H", int(round(gamma * 256.0))))


def _text_type(text: str) -> bytes:
    raw = text.encode("ascii", "replace") + b"\x00"
    return (b"desc" + b"\x00" * 4 + struct.pack(">I", len(raw)) + raw
            + b"\x00" * (12 + 67 + 3))


def build_profile(space: str) -> bytes:
    """행렬/TRC 방식의 최소 ICC v2 프로파일을 직접 만듭니다.

    PIL의 ImageCms는 sRGB만 만들어 주고 프라이머리를 받지 않습니다.
    Adobe RGB는 프라이머리와 감마가 공개 규격이라 그 숫자로 직접
    구성합니다 — 어도비가 배포하는 프로파일 **파일**은 재배포 조건이
    있어 담지 않습니다. 그래서 이름도 "Adobe RGB (1998)"이 아니라
    호환 표기를 씁니다.
    """
    if space == "srgb":
        try:
            from PIL import ImageCms

            return ImageCms.ImageCmsProfile(
                ImageCms.createProfile("sRGB")).tobytes()
        except Exception as exc:  # noqa: BLE001 - 아래 직접 구성으로 물러섭니다
            log.debug("ImageCms sRGB 생성 실패, 직접 구성합니다: %s", exc)

    # 프로파일에 적는 값은 PCS(D50) 기준입니다 — _adapt_to_d50 참고.
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
        struct.pack(">I", total)          # 크기
        + b"none" + struct.pack(">I", 0x02100000)   # CMM, 버전 2.1
        + b"mntr" + b"RGB " + b"XYZ "     # 종류, 데이터 공간, 연결 공간
        + b"\x00" * 12                    # 생성 일시 (0 = 미지정)
        + b"acsp" + b"MSFT" + struct.pack(">I", 0)
        + b"none" + b"none"               # 제조사, 모델
        + b"\x00" * 8                     # 속성
        + struct.pack(">I", 0)            # 렌더링 의도 = perceptual
        + b"".join(struct.pack(">i", int(round(v * 65536.0)))
                   for v in _D50_XYZ)     # PCS 광원 — 규격상 D50 고정
        + b"none"                         # 생성자
        + b"\x00" * 44
    )
    assert len(header) == 128, len(header)
    return header + table + body


# ---------------------------------------------------------------- 임베드


def _embed_jpeg(data: bytes, blob: bytes) -> bytes:
    """APP2 세그먼트로. 세그먼트당 상한이 있어 큰 프로파일은 나눠 담습니다."""
    if data[:2] != b"\xff\xd8":
        raise ValueError("JPEG이 아닙니다")
    room = 65533 - 16
    chunks = [blob[i:i + room] for i in range(0, len(blob), room)] or [b""]
    payload = b""
    for index, chunk in enumerate(chunks, start=1):
        body = b"ICC_PROFILE\x00" + bytes([index, len(chunks)]) + chunk
        payload += b"\xff\xe2" + struct.pack(">H", len(body) + 2) + body

    at = 2
    if data[2:4] == b"\xff\xe0":          # JFIF가 있으면 그 뒤에
        (length,) = struct.unpack_from(">H", data, 4)
        at = 4 + length
    return data[:at] + payload + data[at:]


def _embed_png(data: bytes, blob: bytes) -> bytes:
    """iCCP 청크를 IHDR 뒤에."""
    at = data.index(b"IHDR") - 4
    (length,) = struct.unpack_from(">I", data, at)
    end = at + 4 + 4 + length + 4
    payload = b"ICCProfile\x00\x00" + zlib.compress(blob)
    chunk = (struct.pack(">I", len(payload)) + b"iCCP" + payload
             + struct.pack(">I", zlib.crc32(b"iCCP" + payload) & 0xFFFFFFFF))
    return data[:end] + chunk + data[end:]


def _embed_tiff(data: bytes, blob: bytes) -> bytes:
    """ICCProfile 태그(34675)를 추가합니다.

    IFD를 파일 끝에 다시 쓰고 헤더의 첫 IFD 오프셋을 그리로 돌립니다 —
    기존 엔트리를 밀지 않아도 되고 화소 데이터를 건드리지 않습니다.
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


#: 이 형식들만 화소를 건드리지 않고 ICC를 넣을 수 있습니다.
EMBEDDABLE = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def embed(path: Path, space: str) -> bool:
    """저장된 파일에 색공간 프로파일을 끼워 넣습니다. 넣었으면 True.

    **실패해도 예외를 던지지 않습니다.** 내보내기 한복판이라 파일 하나의
    태그 때문에 배치가 멈추면 안 됩니다 — 대신 로그를 남깁니다. 화소는
    이미 목표 공간으로 변환되어 저장돼 있으므로, 태그가 빠지면 뷰어가
    sRGB로 읽어 색이 달라 보입니다(조용히 넘길 일이 아닙니다).
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
