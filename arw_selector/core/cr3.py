"""CR3(ISO/IEC 14496-12 BMFF) 메타데이터 파서.

exifread는 TIFF 기반 RAW(ARW, NEF, CR2 …)만 읽습니다. CR3는 컨테이너가 아예
다른 ISO BMFF라 "File format not recognized"로 떨어지고, 그 결과 렌즈 정보가
통째로 비어 자동 렌즈 보정이 동작하지 않았습니다.

구조 (실제 파일에서 확인):

    ftyp                      brand 'crx '
    moov
      uuid 85c0b687-820f-11e0-8111-f4ce462b6a48   ← 캐논 메타데이터 컨테이너
        CMT1   II*\\0 …   IFD0     (Make, Model, Orientation)
        CMT2   II*\\0 …   ExifIFD  (노출, ISO, 렌즈, 촬영시각)
        CMT3   II*\\0 …   캐논 MakerNote
        CMT4   II*\\0 …   GPS

CMT 박스는 각각 **완전한 TIFF 스트림**(엔디안 표식 + 매직 + IFD 오프셋)이라,
잘라내서 그대로 exifread에 먹이면 됩니다. TIFF 파싱을 새로 짜지 않습니다.

파일 전체(수십 MB)를 읽지 않습니다. 박스 헤더만 따라가며 seek해서 필요한
조각만 읽습니다 — 4000장 배치에서 이 차이가 큽니다.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Iterator

import exifread

log = logging.getLogger(__name__)

CANON_UUID = bytes.fromhex("85c0b687820f11e08111f4ce462b6a48")
"""캐논이 CR3 메타데이터를 담는 uuid 박스 식별자."""

META_BOXES = (b"CMT1", b"CMT2", b"CMT3", b"CMT4")

_MAX_BOX_BYTES = 8 * 1024 * 1024
"""한 박스에서 읽어들일 상한. 손상 파일이 터무니없는 크기를 주장해도 막습니다."""


def _iter_boxes(fh, end: int) -> Iterator[tuple[bytes, int, int]]:
    """[크기][타입] 박스를 순회합니다. (타입, 페이로드 시작, 박스 끝)."""
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

        if size == 1:  # 64비트 확장 크기
            extended = fh.read(8)
            if len(extended) < 8:
                return
            size = int.from_bytes(extended, "big")
            header_length = 16
        elif size == 0:  # 파일 끝까지
            size = end - position

        if size < header_length or position + size > end:
            return

        yield box_type, position + header_length, position + size
        fh.seek(position + size)


def _tiff_tags(payload: bytes) -> dict:
    """CMT 박스 페이로드(완전한 TIFF)를 exifread로 읽습니다."""
    if len(payload) < 8 or payload[:2] not in (b"II", b"MM"):
        return {}
    try:
        return exifread.process_file(io.BytesIO(payload), details=False) or {}
    except Exception as exc:  # noqa: BLE001 - 한 박스가 깨져도 나머지는 씁니다
        log.debug("CMT 박스 파싱 실패: %s", exc)
        return {}


def read_exif_tags(path: Path) -> dict:
    """CR3에서 EXIF 태그를 모아 돌려줍니다. 못 읽으면 빈 dict.

    CMT1~CMT4를 각각 독립 TIFF로 읽어 합칩니다. 각 박스가 자기 스트림의
    IFD0이라 exifread는 전부 "Image ..." 접두사를 붙입니다. 호출부가 헷갈리지
    않도록 흔히 쓰는 키는 표준 이름으로도 함께 넣어 줍니다.
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
                break  # moov는 하나뿐입니다
    except OSError as exc:
        log.debug("CR3 읽기 실패 %s: %s", path.name, exc)
        return {}

    return _normalize(tags)


# exifread가 붙이는 접두사가 박스마다 달라, 표준 이름으로도 찾을 수 있게 합니다.
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
}


def _normalize(tags: dict) -> dict:
    """표준 키 이름으로도 접근할 수 있게 별칭을 채워 넣습니다."""
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
    """확장자가 아니라 실제 브랜드로 판별합니다 (이름만 바뀐 파일 대비)."""
    try:
        with Path(path).open("rb") as fh:
            header = fh.read(12)
    except OSError:
        return False
    return len(header) >= 12 and header[4:8] == b"ftyp" and header[8:12] == b"crx "
