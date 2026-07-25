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


def _maker_note_offset(buf, endian: str, first: int) -> int | None:
    """ExifIFD의 0x927C 값 포인터(파일 기준). 값이 아니라 위치가 필요합니다."""
    ifd0 = _read_ifd(buf, first, endian)
    if 0x8769 not in ifd0:
        return None
    exif_off = _longs(ifd0[0x8769], endian)[0]
    if exif_off + 2 > len(buf):
        return None
    (count,) = struct.unpack_from(endian + "H", buf, exif_off)
    for index in range(min(count, 512)):
        at = exif_off + 2 + index * 12
        if at + 12 > len(buf):
            break
        tag, _, _ = struct.unpack_from(endian + "HHI", buf, at)
        if tag == 0x927C:
            return struct.unpack_from(endian + "I", buf, at + 8)[0]
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
        # 렌즈는 내장 JPEG(JpgFromRaw) 안 EXIF의 MakerNote에 있습니다
        jpg = ifd0.get(0x002E)
        if jpg is not None:
            data = jpg[2]
            exif_at = data.find(b"Exif\x00\x00")
            if exif_at >= 0:
                sub = data[exif_at + 6:]
                sub_header = _tiff_header(sub)
                if sub_header is not None:
                    maker_off = _maker_note_offset(sub, sub_header[0], sub_header[1])
                    if (maker_off is not None
                            and sub[maker_off:maker_off + 9] == b"Panasonic"):
                        maker = _read_ifd(sub, maker_off + 12, sub_header[0])
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
        return int(img_w), int(img_h), int(x), int(y)

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
        blob = maker[0x0026][2]
        if len(blob) < 16:
            return None
        _, _, num, valid, _, _, af_w, af_h = struct.unpack_from(endian + "H" * 8, blob, 0)
        if not (num and valid and af_w and af_h):
            return None
        need = 16 + num * 8
        if len(blob) < need:
            return None
        widths = struct.unpack_from(endian + f"{num}H", blob, 16)
        heights = struct.unpack_from(endian + f"{num}H", blob, 16 + num * 2)
        xs = struct.unpack_from(endian + f"{num}h", blob, 16 + num * 4)   # 부호!
        ys = struct.unpack_from(endian + f"{num}h", blob, 16 + num * 6)
        x = af_w / 2 + xs[0]
        y = af_h / 2 - ys[0]          # Y-up (실측 확정)
        w, h = widths[0], heights[0]
        if not (w and h and 0 <= x < af_w and 0 <= y < af_h):
            return None
        return int(x), int(y), int(w), int(h), int(af_w), int(af_h)
    except Exception as exc:  # noqa: BLE001 - 배치 한가운데라 물러섭니다
        log.debug("canon_af_area 실패 %s: %s", path.name, exc)
        return None


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
        img_w, img_h, ax, ay = location
        side = max(img_w, img_h) * SONY_POINT_BOX_RATIO
        cx, cy, bw, bh = _rotate_box(ax, ay, side, side, img_w, img_h, orientation)
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
