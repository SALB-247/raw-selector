"""내보낸 JPEG에 EXIF를 선택적으로 기록합니다.

기본은 아무것도 넣지 않습니다. 사진을 밖으로 내보낼 때 촬영 장비·시각·위치가
딸려 나가는 것을 원치 않는 경우가 많으므로, 넣는 쪽을 명시적 선택으로 둡니다.

GPS는 다루지 않습니다. 위치 정보는 실수로 흘러나갔을 때 가장 위험한
항목이라 선택지에서 빼는 편이 낫습니다.
"""

from __future__ import annotations

import logging
from pathlib import Path

import piexif

from ..raw_io import read_metadata
from .settings import MetadataSettings

log = logging.getLogger(__name__)

from ..appinfo import APP_NAME as SOFTWARE_NAME  # noqa: F401 (EXIF Software 태그)


def _ascii(value: str) -> bytes:
    """EXIF ASCII 태그용 인코딩.

    piexif는 str이 아니라 bytes를 요구합니다. 규격상 ASCII 전용 필드지만
    한글 저작권 표기를 UTF-8로 넣으면 대부분의 뷰어가 읽어 줍니다.
    """
    return value.encode("utf-8")


def _rational(value: float, denominator: int = 100) -> tuple[int, int]:
    return int(round(value * denominator)), denominator


def _shutter_rational(seconds: float) -> tuple[int, int]:
    """셔터 속도를 EXIF 유리수로. 1/200 같은 값이 그대로 보이게 합니다."""
    if seconds >= 1.0:
        return int(round(seconds * 10)), 10
    return 1, max(1, int(round(1.0 / seconds)))


def build_exif(source: Path, settings: MetadataSettings) -> bytes | None:
    """선택된 항목만 담은 EXIF 바이트를 만듭니다. 넣을 게 없으면 None."""
    if not settings.enabled or not settings.include:
        return None

    meta = read_metadata(source)
    zeroth: dict = {}
    exif: dict = {}

    if settings.wants("camera"):
        # 예전에는 "SONY"로 박아 두었습니다. 소니에서 출발한 도구라 그랬는데,
        # 지금은 캐논·니콘 RAW도 다룹니다 — 내보낸 CR3가 Make=SONY,
        # Model=Canon EOS R6 Mark III 로 나갔습니다.
        if meta.camera_make:
            zeroth[piexif.ImageIFD.Make] = _ascii(meta.camera_make)
        if meta.camera_model:
            zeroth[piexif.ImageIFD.Model] = _ascii(meta.camera_model)

    if settings.wants("lens") and meta.lens_model:
        exif[piexif.ExifIFD.LensModel] = _ascii(meta.lens_model)

    if settings.wants("exposure"):
        if meta.shutter_speed:
            exif[piexif.ExifIFD.ExposureTime] = _shutter_rational(meta.shutter_speed)
        if meta.aperture:
            exif[piexif.ExifIFD.FNumber] = _rational(meta.aperture, 10)
        if meta.iso:
            exif[piexif.ExifIFD.ISOSpeedRatings] = int(meta.iso)

    if settings.wants("focal_length") and meta.focal_length:
        exif[piexif.ExifIFD.FocalLength] = _rational(meta.focal_length, 10)

    if settings.wants("datetime") and meta.capture_time:
        stamp = _ascii(meta.capture_time.strftime("%Y:%m:%d %H:%M:%S"))
        zeroth[piexif.ImageIFD.DateTime] = stamp
        exif[piexif.ExifIFD.DateTimeOriginal] = stamp
        exif[piexif.ExifIFD.DateTimeDigitized] = stamp

    if settings.wants("artist") and settings.artist:
        zeroth[piexif.ImageIFD.Artist] = _ascii(settings.artist)

    if settings.wants("copyright") and settings.copyright:
        zeroth[piexif.ImageIFD.Copyright] = _ascii(settings.copyright)

    if settings.wants("software"):
        zeroth[piexif.ImageIFD.Software] = _ascii(SOFTWARE_NAME)

    if not zeroth and not exif:
        return None

    try:
        # GPS와 썸네일은 의도적으로 비워 둡니다
        return piexif.dump({"0th": zeroth, "Exif": exif, "GPS": {}, "1st": {}, "thumbnail": None})
    except Exception as exc:  # noqa: BLE001
        log.warning("EXIF 생성 실패: %s", exc)
        return None


def write_metadata(source: Path, destination: Path, settings: MetadataSettings) -> bool:
    """내보낸 JPEG에 EXIF를 써 넣습니다. 실패해도 사진은 그대로 남습니다."""
    payload = build_exif(source, settings)
    if payload is None:
        return False
    try:
        piexif.insert(payload, str(destination))
        return True
    except Exception as exc:  # noqa: BLE001 - 메타데이터 실패로 내보내기를 망치지 않습니다
        log.warning("EXIF 기록 실패 %s: %s", destination.name, exc)
        return False


def read_written_metadata(path: Path) -> dict:
    """검증용 — 기록된 EXIF를 읽어 사람이 볼 수 있는 형태로."""
    try:
        data = piexif.load(str(path))
    except Exception:  # noqa: BLE001
        return {}

    def text(section: str, tag: int) -> str | None:
        value = data.get(section, {}).get(tag)
        return value.decode("utf-8", "replace") if isinstance(value, bytes) else value

    return {
        "make": text("0th", piexif.ImageIFD.Make),
        "model": text("0th", piexif.ImageIFD.Model),
        "artist": text("0th", piexif.ImageIFD.Artist),
        "copyright": text("0th", piexif.ImageIFD.Copyright),
        "software": text("0th", piexif.ImageIFD.Software),
        "datetime": text("0th", piexif.ImageIFD.DateTime),
        "lens": text("Exif", piexif.ExifIFD.LensModel),
        "iso": data.get("Exif", {}).get(piexif.ExifIFD.ISOSpeedRatings),
        "exposure_time": data.get("Exif", {}).get(piexif.ExifIFD.ExposureTime),
        "fnumber": data.get("Exif", {}).get(piexif.ExifIFD.FNumber),
        "focal_length": data.get("Exif", {}).get(piexif.ExifIFD.FocalLength),
        "gps": data.get("GPS", {}),
    }
