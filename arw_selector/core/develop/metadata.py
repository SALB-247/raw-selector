"""Optionally records EXIF into the exported JPEG.

By default nothing goes in. Sending a photo out with the gear, the time and
the location attached is often not what people want, so putting it in is
left as an explicit choice.

GPS is not handled at all. Location is the most dangerous item to leak by
accident, so it is better left out of the options entirely.
"""

from __future__ import annotations

import logging
from pathlib import Path

import piexif

from ..raw_io import read_metadata
from .settings import MetadataSettings

log = logging.getLogger(__name__)

from ..appinfo import APP_NAME as SOFTWARE_NAME  # noqa: F401 (the EXIF Software tag)


def _ascii(value: str) -> bytes:
    """Encoding for the EXIF ASCII tags.

    piexif demands bytes, not str. The field is ASCII-only by the spec, but
    putting a Hangul copyright notice in as UTF-8 is read by most viewers.
    """
    return value.encode("utf-8")


def _rational(value: float, denominator: int = 100) -> tuple[int, int]:
    return int(round(value * denominator)), denominator


def _shutter_rational(seconds: float) -> tuple[int, int]:
    """The shutter speed as an EXIF rational. It makes a value like 1/200
    show up as it is."""
    if seconds >= 1.0:
        return int(round(seconds * 10)), 10
    return 1, max(1, int(round(1.0 / seconds)))


def build_exif(source: Path, settings: MetadataSettings) -> bytes | None:
    """Builds EXIF bytes holding only the selected items. None if there is
    nothing to put in."""
    if not settings.enabled or not settings.include:
        return None

    meta = read_metadata(source)
    zeroth: dict = {}
    exif: dict = {}

    if settings.wants("camera"):
        # It used to be hardcoded to "SONY". That came of the tool having
        # started on Sony, but it now handles Canon and Nikon RAW too - an
        # exported CR3 went out as Make=SONY, Model=Canon EOS R6 Mark III.
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
        # GPS and the thumbnail are deliberately left empty
        return piexif.dump({"0th": zeroth, "Exif": exif, "GPS": {}, "1st": {}, "thumbnail": None})
    except Exception as exc:  # noqa: BLE001
        log.warning("EXIF 생성 실패: %s", exc)
        return None


def write_metadata(source: Path, destination: Path, settings: MetadataSettings) -> bool:
    """Writes EXIF into the exported JPEG. On failure the photo is left as
    it is."""
    payload = build_exif(source, settings)
    if payload is None:
        return False
    try:
        piexif.insert(payload, str(destination))
        return True
    except Exception as exc:  # noqa: BLE001 - failed metadata must not ruin the export
        log.warning("EXIF 기록 실패 %s: %s", destination.name, exc)
        return False


def read_written_metadata(path: Path) -> dict:
    """For verification - reads the recorded EXIF into a form a person can
    look at."""
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
