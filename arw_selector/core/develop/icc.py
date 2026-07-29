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
from functools import lru_cache
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
    # ROMM RGB(ProPhoto). 초록·파랑 프라이머리가 스펙트럼 궤적 **밖**이라
    # 실재하지 않는 색까지 담습니다 — 그래서 카메라가 잡은 색을 거의 다
    # 안고 갑니다(실측: sRGB에서 잘린 화소의 98%를 되살림, Adobe RGB는 61%).
    "prophoto": ((0.7347, 0.2653), (0.1596, 0.8404), (0.0366, 0.0001)),
}
_D65 = (0.3127, 0.3290)
_D50 = (0.3457, 0.3585)

#: 색공간의 백점. **ProPhoto는 D50입니다.** 백점이 다른 공간끼리 옮길 때
#: 색순응을 빠뜨리면 회색이 회색으로 남지 않습니다 — 화면 전체에 색이 돕니다.
_WHITE = {"srgb": _D65, "adobe_rgb": _D65, "prophoto": _D50}

#: ICC의 연결 공간(PCS)은 **D50으로 고정**입니다. 촬영 색공간은 D65이므로
#: 프로파일에 적는 XYZ는 Bradford로 D50에 적응시킨 값이어야 합니다.
#:
#: 이걸 빼먹으면 libpng이 "PCS illuminant is not D50"으로 경고하고, 무엇보다
#: littleCMS 왕복 오차가 최대 88레벨까지 벌어집니다(실측). 화소 변환
#: 자체는 D65→D65라 적응이 필요 없습니다 — 프로파일 파일에만 씁니다.
#: 규격이 못박은 값이라 위 `_D50` xy에서 계산한 것과 미세하게 다릅니다
#: (xy로 풀면 0.9643, 1, 0.8251 — 2e-4). 프로파일에 적는 숫자는 반드시 이
#: 값이어야 하므로 유도하지 않고 그대로 둡니다.
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
    """프라이머리와 **그 공간의 백점**에서 RGB→XYZ 행렬을 만듭니다."""
    red, green, blue = _PRIMARIES[space]
    matrix = np.stack([_xy_to_xyz(*red), _xy_to_xyz(*green),
                       _xy_to_xyz(*blue)], axis=1)
    scale = np.linalg.solve(matrix, _xy_to_xyz(*_WHITE[space]))
    return matrix * scale


def _bradford(source_xyz: np.ndarray, target_xyz: np.ndarray) -> np.ndarray:
    """한 백점의 XYZ를 다른 백점 기준으로 옮기는 행렬."""
    source = _BRADFORD @ source_xyz
    target = _BRADFORD @ target_xyz
    return np.linalg.inv(_BRADFORD) @ np.diag(target / source) @ _BRADFORD


def _adapt_to_d50(matrix: np.ndarray) -> np.ndarray:
    """RGB→XYZ 행렬을 D50 기준으로 (ICC의 PCS가 D50 고정이라 필요합니다).

    이미 D50인 공간(ProPhoto)은 그대로 둡니다 — 한 번 더 걸면 어긋납니다.
    """
    white = matrix @ np.ones(3)
    if np.allclose(white, _D50_XYZ, atol=1e-3):
        return matrix
    return _bradford(white, _D50_XYZ) @ matrix


def convert_space(rgb_linear: np.ndarray, source: str, target: str) -> np.ndarray:
    """선형 RGB를 다른 색공간의 선형 RGB로. 백점이 다르면 색순응을 겁니다.

    **회색이 회색으로 남는 것이 이 함수의 최소 조건입니다.** 백점이 다른
    공간끼리(ProPhoto는 D50, sRGB는 D65) 순응 없이 행렬만 곱하면 중립축이
    틀어져 화면 전체에 색이 돕니다.
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



#: 보정이 이뤄지는 색공간.
#:
#: 카메라→작업공간 변환은 LibRaw 안에서 일어납니다 — 우리 코드가 화소를
#: 보기도 전입니다. 그래서 여기가 좁으면 그 시점에 잘린 색은 되찾을 길이
#: 없습니다. 실측(_DSC5914.ARW): sRGB로 받으면 화소의 0.79%가 색역 밖으로
#: 잘리고, 그중 88%가 주황·노랑이며 가장 큰 덩어리가 24,478화소 — 조명이
#: 센 면 하나가 통째로 뭉개집니다.
#:
#: 되살리는 비율: Adobe RGB 61%, Wide Gamut 92%, **ProPhoto 98%**.
#: ProPhoto는 백점이 D50이라 sRGB(D65)로 옮길 때 색순응이 필요한데,
#: convert_space가 처리하고 회색이 회색으로 남는 것을 테스트로 고정했습니다.
#:
#: 8비트로는 쓰면 안 됩니다 — 같은 레벨 수에 더 넓은 색을 담으므로 레벨당
#: 간격이 벌어집니다. 파이프라인이 float이고 내보내기가 16비트를 지원하게
#: 된 뒤에야 성립합니다.
#:
#: **전달함수는 ProPhoto의 1.8이 아니라 sRGB 곡선을 씁니다**(_decode/_encode가
#: adobe_rgb만 따로 다루고 나머지는 sRGB로 처리합니다). 우리 값은 이미 렌더된
#: 표시 기준 값이라(BT.709 → 기종보정 → 프로파일 곡선) 그 위에 ProPhoto의
#: 인코딩을 덧씌울 이유가 없고, 넓혀야 하는 것은 프라이머리뿐입니다. 어도비도
#: 같은 조합을 씁니다(ProPhoto 프라이머리 + sRGB 톤 응답 = Melissa RGB).
#:
#: 이 규약이 밖으로 새지 않습니다 — ProPhoto로 **내보내지 않기** 때문입니다.
#: 내보내기 선택지는 sRGB와 Adobe RGB뿐이고 둘 다 자기 곡선으로 인코딩해
#: 나갑니다. 그래서 이 가정을 보는 것은 우리 코드뿐입니다.
WORKING_SPACE = "prophoto"


def _decode(image: np.ndarray, space: str, peak: float) -> np.ndarray:
    """그 공간의 표시값 BGR → 선형 RGB(0~1). **공간마다 곡선이 다릅니다.**"""
    rgb = np.clip(np.asarray(image, dtype=np.float32)[..., ::-1]
                  / np.float32(peak), 0.0, 1.0)
    if space == "adobe_rgb":
        return np.power(rgb, np.float32(_ADOBE_GAMMA))
    return np.where(rgb <= np.float32(0.04045), rgb / np.float32(12.92),
                    np.power((rgb + np.float32(0.055)) / np.float32(1.055),
                             np.float32(2.4)))


def _encode(linear: np.ndarray, space: str, peak: float) -> np.ndarray:
    """선형 RGB → 그 공간의 표시값 BGR."""
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
    """표시값을 다른 공간의 표시값으로. 곡선을 펴고, 프라이머리를 옮기고,
    목표 공간의 곡선으로 다시 인코딩합니다.

    **float32로 계산합니다.** 미리보기가 매 렌더마다 이 변환을 타는데,
    float64로는 1400px 한 장에 189ms로 렌더 시간(200ms)이 두 배가 됩니다.
    float32는 101ms이고 왕복 오차는 그대로 0입니다.

    4096칸 표에서 최근접으로 찾는 길도 재 봤습니다(71ms). 30ms를 더 벌지만
    왕복 오차가 0.03레벨(최대 0.66)로 생깁니다 — 렌더 200ms 옆에서 30ms는
    체감되지 않고, 16비트 내보내기가 그 오차를 그대로 안습니다. 기각했습니다.

    **NaN이 들어오면 그 화소는 세 채널 모두 쓰레기가 됩니다.** 행렬이 채널을
    섞기 때문입니다. 막지 않습니다 — nan_to_num이 렌더당 11.3ms(11%)인데,
    여기 NaN이 왔다는 것은 상류가 이미 깨졌다는 뜻이라 조용히 0으로 덮으면
    원인을 가립니다. 실제 발생원인 렌즈 보정은 자기 자리에서 검출해 버립니다
    (optics.apply_auto_correction의 isfinite 검사).

    **번지는 범위는 그 화소 하나가 아닙니다.** 뒤에 오는 컨볼루션 단계가
    이웃으로 퍼뜨립니다. 128×128 중앙에 NaN 하나를 넣고 최종 결과를
    비교한 실측:

        보정 없음·노출·그레인      1화소 (1×1)
        디헤이즈 100              1화소, 세 채널
        샤픈 100                 81화소 (9×9)
        클래리티 100             625화소 (25×25)
        노이즈 감소 100          828화소, 세 채널 전부

    그래도 국소이고(16,384화소 중 5%) 검게 남아 눈에 띕니다 — uint8 캐스팅에서
    0이 됩니다. "덮지 말고 드러내라"는 판단은 그대로지만, 크기를 알고 정한
    것으로 바꿔 둡니다.

    파이프라인이 NaN을 **만드는** 자리는 없습니다. numpy를
    seterr(invalid='raise', divide='raise')로 세워 놓고 무작위 극단 설정
    540회를 태워 0종이었습니다.
    """
    if source == target:
        return image
    peak = 65535.0 if image.dtype == np.uint16 else 255.0
    linear = convert_space(_decode(image, source, peak), source, target)
    out = np.clip(_encode(linear, target, peak), 0.0, peak)
    return out.astype(image.dtype)


def to_working(image: np.ndarray, space: str) -> np.ndarray:
    """다른 공간의 표시값을 **작업 공간**으로 옮깁니다.

    원본 JPEG·HEIF를 읽어 올 때 씁니다. 예전에는 sRGB로 옮겼는데, 작업
    공간이 sRGB가 아니게 되면서 목적지가 바뀌었습니다.
    """
    return _move(image, space, WORKING_SPACE)


def working_to(image: np.ndarray, space: str) -> np.ndarray:
    """작업 공간의 표시값을 목표 공간으로 — `to_working`의 역.

    화면(항상 sRGB)과 내보내기가 씁니다.
    """
    return _move(image, WORKING_SPACE, space)


def to_srgb_from(image: np.ndarray, space: str) -> np.ndarray:
    """다른 색공간의 BGR 값을 sRGB 값으로 — `convert_from_srgb`의 역."""
    return _move(image, space, "srgb")


def convert_from_srgb(image: np.ndarray, space: str) -> np.ndarray:
    """sRGB로 해석되는 BGR 값을 목표 색공간의 BGR 값으로.

    dtype과 눈금(8비트 0~255 / 16비트 0~65535)을 그대로 지킵니다.
    """
    return _move(image, "srgb", space)


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
