"""exifread가 못 닿는 제조사 메타데이터 — RW2 ISO·렌즈, 소니·니콘 AF 위치.

세 곳 모두 암호화가 아니라 **파서 부재**가 원인이었습니다(RESEARCH_METADATA.md,
exiftool 13.55 대조 전건 일치로 검증):

- Panasonic RW2는 TIFF 매직이 42가 아니라 85라 exifread가 파일째 거부합니다.
  ISO는 IFD0 0x0017에, 렌즈는 내장 JPEG(IFD0 0x002E) 안 MakerNote 0x0051에
  평문으로 있습니다.
- 소니 MakerNote는 (최신 바디) 헤더 없이 IFD가 바로 시작하고, 주 초점 위치는
  평문 태그 0x2027 = (이미지W, 이미지H, x, y)입니다. 0x94xx 암호 블록은
  건드리지 않습니다.
- 니콘 AFInfo2(MakerNote 0x00B7)는 평문이고, AF 영역 (X,Y,W,H)의 LE u16
  오프셋이 버전별로 고정입니다. Z9의 HE/HE*에서도 그대로 읽힙니다.
- 캐논 CR3의 AFInfo2(CMT3 박스 안 MakerNote 0x0026)도 평문입니다. 좌표는
  부호 있는 중심 원점이고 Y는 위가 양수입니다(얼굴 대조 실증). CR2는 검증할
  실파일이 없어 지원하지 않습니다.

여기 함수들은 **절대 예외를 던지지 않습니다** — 배치 분석 한가운데에서
장당 한 번씩 불리므로, 못 읽으면 None/빈 dict로 물러납니다. 워커(spawn)에서
그대로 쓰이므로 모듈 최상위 함수만 둡니다.

좌표 의미 주의: 소니 FocusLocation은 존 AF에서 눈이 아니라 존(몸통)을
가리킵니다(실측 47장 — RESEARCH_METADATA.md). 얼굴 검출의 대체가 아니라
"카메라가 AF를 건 곳"의 기록으로만 쓰십시오.
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path

log = logging.getLogger(__name__)

#: 헤더에서 이만큼만 읽어 파싱합니다. 실측(다섯 기종)에서 우리가 건드리는
#: 값은 전부 앞쪽에 있습니다 — 소니 MakerNote 6KB, 니콘 33KB, RW2의 ISO는
#: 파일 머리, RW2 렌즈(내장 JPEG)도 578KB로 이 안에 들어옵니다.
#:
#: 40MB RAW를 통째로 읽거나 mmap할 이유가 없습니다 — 워커 9개가 장당
#: 한 번씩 부르는 자리라 IO를 정직하게 2MB로 묶어 두고, 값 오프셋이 이
#: 범위를 넘는 파일은 파서가 조용히 None으로 물러섭니다.
_HEADER_BYTES = 2 * 1024 * 1024

_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8,
               11: 4, 12: 8}

#: AFInfo2 버전별 AF 영역 (X, Y, W, H)의 LE u16 시작 오프셋.
#: 실측 검증: v0400 = Z9(무손실·HE·HE*), v0402 = Z50 II(40장 전수),
#: v0301 = Z5. v0402는 exiftool 13.55도 아직 모르는데 같은 레이아웃입니다.
_NIKON_AF_OFFSETS = {
    "0300": 0x2E, "0301": 0x2E,
    "0400": 0x42, "0401": 0x42, "0402": 0x42,
}


def _tiff_header(buf, base: int = 0) -> tuple[str, int] | None:
    """(엔디안, 첫 IFD 오프셋). RW2의 매직 85도 받습니다 — 그게 요점입니다."""
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
    """IFD 하나 → {태그: (형식, 개수, 값바이트)}. 값 오프셋은 base 기준입니다."""
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
    """ExifIFD의 0x927C 값 포인터(**파일 기준**). 값이 아니라 위치가 필요합니다.

    base는 TIFF 헤더가 파일 어디서 시작하는가입니다 — RAW는 0이고, JPEG는
    EXIF가 APP1 세그먼트 안에 있어 0이 아닙니다. IFD 안의 오프셋은 전부
    TIFF 헤더 기준이라 base를 더해야 파일 위치가 됩니다. 돌려주는 값은
    호출부가 바로 쓰도록 base를 더한 파일 기준입니다(base=0이면 종전과 동일).
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


#: JPEG는 TIFF가 아니라 APP1 세그먼트 안에 EXIF(TIFF 스트림)를 답니다.
#: SOI(FFD8) 뒤로 세그먼트를 훑어 'Exif\0\0' 뒤 TIFF 헤더 위치를 찾습니다.
def _jpeg_exif_base(buf) -> int | None:
    """JPEG APP1 안 TIFF 헤더의 파일 오프셋. JPEG가 아니거나 없으면 None."""
    if buf[:2] != b"\xff\xd8":
        return None
    pos = 2
    while pos + 4 <= len(buf):
        if buf[pos] != 0xFF:
            return None
        marker = buf[pos + 1]
        if marker == 0xFF:
            pos += 1                  # 마커 앞 채움 바이트(규격 허용)
            continue
        if marker in (0xDA, 0xD9):    # SOS·EOI — 이 뒤는 화소거나 끝입니다
            return None
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2                  # 길이 필드가 없는 마커
            continue
        (seg_len,) = struct.unpack_from(">H", buf, pos + 2)
        if seg_len < 2:
            return None
        if marker == 0xE1 and buf[pos + 4:pos + 10] == b"Exif\x00\x00":
            return pos + 10
        pos += 2 + seg_len
    return None


def _with_header(path: Path, reader):
    """파일 앞부분(_HEADER_BYTES)만 읽어 reader(buf)를 돌립니다. 실패는 None."""
    try:
        with path.open("rb") as fh:
            buf = fh.read(_HEADER_BYTES)
        return reader(buf)
    except Exception as exc:  # noqa: BLE001 - 배치 한가운데라 물러섭니다
        log.debug("maker_meta 실패 %s: %s", path.name, exc)
        return None


# ---------------------------------------------------------------- 색공간


#: Adobe RGB로 찍은 파일은 **ColorSpace가 2가 아니라 65535(Uncalibrated)** 로
#: 나옵니다. 규격이 그렇게 정해 두었고, 실제 구분은 Interoperability IFD의
#: InteropIndex('R98'=sRGB, 'R03'=AdobeRGB)로 합니다.
#:
#: 실측(2026-07-28) — 소니 JPEG·ARW, 파나소닉 RW2(내장 JPEG), 캐논 CR2,
#: 카메라 JPEG 전반에서 읽힙니다. 다만 **니콘은 이 태그를 아예 쓰지 않고**
#: MakerNote 0x001E에 적습니다(Z9 실파일 확인). 캐논 CR3(ISO-BMFF)와 애플
#: HEIC은 못 읽습니다.
#:
#: RAW는 우리가 직접 디모자이크하므로 이 설정과 무관합니다 — 카메라가 굽는
#: JPEG에만 걸립니다. 그래서 판별이 필요한 대상도 JPEG·HEIF 원본뿐이고,
#: 그쪽은 전 제조사에서 읽힙니다.
_INTEROP_SPACES = {"R98": "srgb", "R03": "adobe_rgb"}

#: InteropIndex가 없을 때의 폴백. 실측 보관함 125개 폴더 중 **19개**가 이
#: 경우로, ColorSpace=1(sRGB)만 있고 Interop IFD가 없었습니다 — 폴백 없이는
#: 그만큼이 판별 불가로 떨어집니다.
_COLORSPACE_TAG = {1: "srgb", 2: "adobe_rgb"}


def colour_space(path: Path) -> str:
    """이 파일의 색공간 — 'srgb' 또는 'adobe_rgb'. 모르면 'srgb'.

    **모를 때 sRGB로 답하는 것이 안전합니다.** 실측 보관함 125개 폴더 중
    Adobe RGB는 6개였습니다. 잘못 Adobe RGB로 보면 멀쩡한 사진의 채도를 깎게
    되므로, 반대 방향보다 손해가 훨씬 큽니다.

    **폴더가 아니라 파일마다 봅니다.** 한 폴더 2,159장 중 마지막 2장만
    sRGB인 경우가 실제로 있었습니다 — 촬영 도중 설정을 바꾼 것입니다.
    파일명의 밑줄 접두사(DCF의 Adobe RGB 표시)는 신호로 쓰지 않습니다.
    Adobe RGB 6개 폴더 중 밑줄이 붙은 것은 1개뿐이었습니다.

    소니 HEIF는 카메라가 HEIF 촬영에서 sRGB만 고르게 하므로 언제나
    sRGB입니다 — InteropIndex가 없어도 됩니다.
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
    """RW2에서 exifread가 놓치는 것 — {'iso': int, 'lens': str} (있는 것만).

    지금 앱에 나오는 모델·시각·초점거리는 내장 프리뷰 EXIF 폴백이고,
    그 프리뷰에는 ISO·렌즈가 없어서 이 둘만 비어 보였습니다.
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
        # 렌즈는 내장 JPEG(JpgFromRaw) 안 EXIF의 MakerNote에 있습니다.
        # 환산 초점거리(0xA405)도 같은 내장 EXIF에 평문입니다(실측 105mm).
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


# ---------------------------------------------------------------- AF 위치


def sony_focus_location(path: Path) -> tuple[int, int, int, int] | None:
    """소니 평문 태그 0x2027 = (이미지W, 이미지H, x, y). 센서 표시 방향 기준."""

    def reader(buf):
        header = _tiff_header(buf)
        if header is None:
            return None
        endian, first = header
        maker_off = _maker_note_offset(buf, endian, first)
        if maker_off is None or maker_off + 2 > len(buf):
            return None
        # 최신 소니는 MakerNote 헤더 없이 IFD가 바로 시작합니다. 첫 u16이
        # 그럴듯한 엔트리 수가 아니면 "SONY DSC " 헤더형(+12)입니다.
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
        # 0x2037 FocusFrameSize — 카메라가 실제로 표시한 AF 프레임 크기.
        # 있으면 합성 상자(긴변 8%) 대신 이 실값을 씁니다(D2). 실측 A6700:
        # (135, 138) = exiftool 'FocusFrameSize 135x138' 그대로.
        frame_w = frame_h = None
        size_entry = maker.get(0x2037)
        if size_entry is not None:
            size = _shorts(size_entry, endian)
            if (len(size) >= 2 and 0 < size[0] < img_w and 0 < size[1] < img_h):
                frame_w, frame_h = int(size[0]), int(size[1])
        return int(img_w), int(img_h), int(x), int(y), frame_w, frame_h

    return _with_header(path, reader)


def nikon_af_area(path: Path) -> tuple[int, int, int, int, int, int] | None:
    """니콘 AFInfo2의 (x, y, w, h, 기준W, 기준H).

    좌표는 출력 이미지 공간입니다. 기준 치수는 type-0 SubIFD(원본 화소)의
    폭·높이를 씁니다 — 출력과 최대 0.3% 차이(Z9 8280 vs 8256)로, 힌트
    상자 용도에는 무시할 수 있습니다.
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
        # "Nikon\0"+버전 4바이트 뒤 **자체 TIFF 헤더** — 오프셋은 그 헤더
        # 기준입니다. 파일 기준으로 읽으면 엉뚱한 자리가 나옵니다(nef_meta의
        # 정답 대조에서 이미 걸린 함정).
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
    """캐논 AFInfo2 블롭 → (x, y, w, h, 기준W, 기준H). 담긴 그릇과 무관합니다.

    CR3(CMT3 박스)든 JPEG(APP1 EXIF)든 이 SHORT 배열의 구조는 같습니다:
    [크기, 모드, 점수 N, 유효 수, 이미지W, H, AF기준W, H,
     폭[N], 높이[N], X[N], Y[N], 초점비트[(N+15)//16], 선택비트[…]].
    X·Y는 **부호 있는 중심 원점**이고 Y는 위가 양수입니다 — 실측(R6M3,
    검출 얼굴과 대조): Y-up이 얼굴 안, Y-down은 얼굴 훨씬 아래였습니다.

    **유효 수가 1보다 크면 첫 점을 쓰면 안 됩니다.** 미러리스(R3·R5·R6)는
    추적 상자 하나만 실어 유효 수가 1이지만, 구형 DSLR(5D3·5D4·1DX2)은
    고정 AF 점 61·81개를 **전부** 싣고 초점이 맞은 점만 AFPointsInFocus
    비트마스크로 표시합니다. 실측(5D3): 유효 61, 초점 점 #59 — 첫 점을
    쓰면 엉뚱한 고정 자리를 가리킵니다. 그래서 다점일 때는 비트가 선
    점들의 **합집합 상자**를 씁니다(확장 4·8점처럼 인접 다발이면 그
    피사체를 덮고, 흩어져 있으면 자연히 넓은 상자가 됩니다).
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
    xs = struct.unpack_from(endian + f"{num}h", blob, 16 + num * 4)   # 부호!
    ys = struct.unpack_from(endian + f"{num}h", blob, 16 + num * 6)

    if valid == 1:
        picked = [0]
    else:
        words_n = (num + 15) // 16
        focus_at = 16 + num * 8
        if len(blob) < focus_at + words_n * 2:
            return None       # 어느 점이 맞았는지 모르면 찍지 않습니다
        words = struct.unpack_from(endian + f"{words_n}H", blob, focus_at)
        picked = [i for i in range(num) if words[i // 16] & (1 << (i % 16))]
        if not picked:
            return None

    # 합집합 상자 (단일점이면 그 점 그대로)
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
    """캐논 CR3 AFInfo2(MakerNote 0x0026)의 (x, y, w, h, 기준W, 기준H).

    CR3의 캐논 MakerNote는 moov/uuid 안 CMT3 박스이고, 그 자체가 완전한
    TIFF 스트림입니다(cr3.py와 같은 경로). AFInfo2는 SHORT 배열:
    [크기, 모드, 점수 N, 유효 수, 이미지W, H, AF기준W, H,
     폭[N], 높이[N], X[N], Y[N], …]. X·Y는 **부호 있는 중심 원점**이고
    Y는 위가 양수입니다 — 실측(R6M3, 검출 얼굴과 대조): Y-up이 얼굴 안,
    Y-down은 얼굴 훨씬 아래였습니다.

    돌려주는 (x, y)는 이미지 좌상단 원점 픽셀로 변환한 값입니다.
    """
    blob = _cr3_afinfo2_blob(path)
    if blob is None:
        return None
    payload, endian = blob
    return _canon_afinfo2(payload, endian)


def _cr3_afinfo2_blob(path: Path) -> tuple[bytes, str] | None:
    """CR3의 AFInfo2(0x0026) 블롭과 엔디안. AF 상자·모드가 공유합니다."""
    # CR3는 TIFF가 아니라 ISO BMFF라 _with_mmap 공통 경로를 못 씁니다.
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
    except Exception as exc:  # noqa: BLE001 - 배치 한가운데라 물러섭니다
        log.debug("cr3 AFInfo2 읽기 실패 %s: %s", path.name, exc)
        return None


def jpeg_af_area(path: Path) -> tuple[int, int, int, int, int, int] | None:
    """카메라가 직접 뽑은 JPEG의 AF 영역 (x, y, w, h, 기준W, 기준H).

    RAW와 같은 MakerNote가 JPEG의 APP1 EXIF 안에 그대로 들어갑니다 —
    막혔던 것은 데이터가 아니라 파서였습니다(RAW 확장자만 보고 갈랐음).
    확장자로는 제조사를 알 수 없으니 IFD0의 Make(0x010F)로 가릅니다.

    캐논은 CR3와 **완전히 같은** AFInfo2(0x0026)라 파서를 공유합니다.
    니콘은 NEF와 같은 AFInfo2(0x00B7)이되 기준 치수를 SubIFD가 아니라
    EXIF의 PixelXDimension에서 얻습니다(JPEG엔 SubIFD가 없음).

    소니(0x2027)는 넣지 않았습니다 — 실파일이 없어 검증을 못 했고,
    ARW는 MakerNote 값 오프셋이 파일 기준인데 JPEG는 TIFF 기준이라
    그대로 옮기면 조용히 틀립니다. CR2와 같은 이유의 보류입니다.
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
            # 캐논 MakerNote는 헤더 없이 IFD가 바로 시작하고, 값 오프셋은
            # TIFF 헤더 기준입니다 — base를 그대로 태웁니다.
            maker = _read_ifd(buf, maker_off - base, endian, base)
            if 0x0026 not in maker:
                return None
            return _canon_afinfo2(maker[0x0026][2], endian)

        if make.startswith(b"NIKON"):
            if buf[maker_off:maker_off + 5] != b"Nikon":
                return None
            # NEF와 같은 함정 — "Nikon\0"+버전 뒤 **자체 TIFF 헤더**이고
            # 오프셋은 그 헤더 기준입니다.
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
            # 기준 치수는 EXIF의 화소 치수 (JPEG엔 type-0 SubIFD가 없음)
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
    """센서 방향의 중심 상자를 EXIF 회전 뒤 좌표로 옮깁니다. (x,y)는 중심."""
    if orientation == 6:      # 90° CW — 표시 크기는 (H, W)
        return frame_h - 1 - y, x, h, w
    if orientation == 8:      # 90° CCW
        return y, frame_w - 1 - x, h, w
    if orientation == 3:      # 180°
        return frame_w - 1 - x, frame_h - 1 - y, w, h
    return x, y, w, h


#: AF를 읽어 볼 JPEG 확장자. HEIF는 컨테이너가 달라(APP1이 아님) 별건입니다.
JPEG_SUFFIXES = (".jpg", ".jpeg")


# ---------------------------------------------------------------- AF 영역 모드
#
# 상세정보 패널 표시용. 값은 카메라 용어 그대로(영문) 돌려주고 번역하지
# 않습니다 — 렌즈명처럼 고유 명사에 가깝습니다. 모르는 값은 None: 조용히
# 틀린 이름을 붙이는 것보다 그 줄을 안 보여 주는 편이 낫습니다.

#: 캐논 AFInfo2 블롭 오프셋 2(u16). exiftool Canon.pm의 공개 표이고 바디
#: 공통입니다. 사용자 실사진에서 2·6·8·9·10·13이 exiftool과 일치함을 확인.
CANON_AF_AREA_MODES = {
    0: "Off (Manual Focus)", 1: "AF Point Expansion (surround)",
    2: "Single-point AF", 4: "Auto", 5: "Face Detect AF",
    6: "Face + Tracking", 7: "Zone AF", 8: "AF Point Expansion (4 point)",
    9: "Spot AF", 10: "AF Point Expansion (8 point)",
    11: "Flexizone Multi (49 point)", 12: "Flexizone Multi (9 point)",
    13: "Flexizone Single", 14: "Large Zone AF",
}

#: 니콘 AFInfo2 블롭 오프셋 5(u8). 차분법으로 자리를 찾았고(같은 버전에서
#: 모드만 다른 컷 대조, RESEARCH_METADATA.md 7절) 값 표는 DSLR과 Z가
#: 완전히 다릅니다. **실파일 86장으로 대조한 값만** 싣습니다.
NIKON_AF_AREA_MODES_DSLR = {      # AFInfo2 0100·0101
    0: "Single Area", 4: "Dynamic Area (9 points)", 14: "Dynamic Area (25 points)",
}
NIKON_AF_AREA_MODES_Z = {         # AFInfo2 0300 이상
    197: "Auto-area", 207: "3D-tracking", 208: "Wide (C1/C2)",
}

#: 소니 MakerNote 0x201C AFAreaModeSetting(1바이트, 평문 — 0x94xx 암호화
#: 블록 밖). 실파일 대조로 검증된 값만: A6700 존 AF 48장에서 11=Zone.
SONY_AF_AREA_MODES = {
    11: "Zone",
}


def _nikon_mode_from_blob(blob: bytes) -> str | None:
    if len(blob) < 6:
        return None
    try:
        version = int(blob[:4].decode("ascii", "replace"))
    except ValueError:
        return None
    table = NIKON_AF_AREA_MODES_Z if version >= 300 else NIKON_AF_AREA_MODES_DSLR
    return table.get(blob[5])


def af_area_mode(path: Path) -> str | None:
    """카메라가 기록한 AF 영역 모드 이름. 못 읽거나 미검증 값은 None.

    소니는 MakerNote 평문 태그, 캐논·니콘은 af_preview_box가 여는 것과
    같은 AFInfo2 블롭의 다른 오프셋입니다. JPEG은 Make로 제조사를 가립니다.
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
            entry = maker.get(0x201C)
            if entry is None or not entry[2]:
                return None
            return SONY_AF_AREA_MODES.get(entry[2][0])

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
        return CANON_AF_AREA_MODES.get(
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
                return CANON_AF_AREA_MODES.get(
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

#: 소니 0x2027은 점만 줍니다. 힌트 상자는 프리뷰 긴변의 이 비율로 만듭니다.
#: A6700의 존 표시 상자(FocusFrameSize 135px ≈ 긴변 2.2%)보다 넉넉히 잡아
#: 존 중심이 조금 어긋나도 피사체가 상자에 들어오게 합니다.
SONY_POINT_BOX_RATIO = 0.08


def af_preview_box(path: Path, orientation: int,
                   preview_w: int, preview_h: int
                   ) -> tuple[int, int, int, int] | None:
    """AF 위치를 **프리뷰 픽셀 좌표의 (x, y, w, h)** 로 돌려줍니다.

    소니는 점 → SONY_POINT_BOX_RATIO 상자, 니콘은 기록된 상자 그대로.
    EXIF 방향(세로 촬영)을 반영하고 프리뷰 크기로 클램프합니다.
    지원 밖 형식·태그 없음·좌표 이상은 전부 None입니다.
    """
    suffix = path.suffix.lower()
    if suffix == ".arw":
        location = sony_focus_location(path)
        if location is None:
            return None
        img_w, img_h, ax, ay, frame_w, frame_h = location
        if frame_w and frame_h:
            box_w, box_h = float(frame_w), float(frame_h)   # 실측값 (0x2037)
        else:
            side = max(img_w, img_h) * SONY_POINT_BOX_RATIO  # 구형 바디 폴백
            box_w = box_h = side
        cx, cy, bw, bh = _rotate_box(ax, ay, box_w, box_h, img_w, img_h, orientation)
        base_w, base_h = (img_h, img_w) if orientation in (6, 8) else (img_w, img_h)
    elif suffix == ".nef":
        area = nikon_af_area(path)
        if area is None:
            return None
        ax, ay, aw, ah, full_w, full_h = area
        cx, cy, bw, bh = _rotate_box(ax, ay, aw, ah, full_w, full_h, orientation)
        base_w, base_h = (full_h, full_w) if orientation in (6, 8) else (full_w, full_h)
    elif suffix == ".cr3":
        # CR2는 검증할 실파일이 없어 넣지 않습니다 — 미검증 지원은 조용히
        # 틀리는 지원입니다.
        area = canon_af_area(path)
        if area is None:
            return None
        ax, ay, aw, ah, full_w, full_h = area
        cx, cy, bw, bh = _rotate_box(ax, ay, aw, ah, full_w, full_h, orientation)
        base_w, base_h = (full_h, full_w) if orientation in (6, 8) else (full_w, full_h)
    elif suffix in JPEG_SUFFIXES:
        # 카메라가 직접 뽑은 JPEG에도 같은 MakerNote가 들어 있습니다.
        # 보정 프로그램이 내보낸 JPEG은 대개 MakerNote가 없어 자연히 None.
        area = jpeg_af_area(path)
        if area is None:
            return None
        ax, ay, aw, ah, full_w, full_h = area
        cx, cy, bw, bh = _rotate_box(ax, ay, aw, ah, full_w, full_h, orientation)
        base_w, base_h = (full_h, full_w) if orientation in (6, 8) else (full_w, full_h)
    else:
        return None

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
