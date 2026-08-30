"""Export options.

Even from the same culling result, the files you need differ with the
purpose - full size for print, 2048px on the long edge for social, a small
watermarked file for a client to check.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from enum import Enum
from pathlib import Path

from .types import Grade, ImageRecord

log = logging.getLogger(__name__)

ALL_GRADES: tuple[str, ...] = tuple(grade.value for grade in Grade)


class ExportFormat(str, Enum):
    """The format to save the develop result in.

    HEIF/AVIF **cannot go in.** This OpenCV build (the 5.0.0 pip wheel)
    carries no encoder for them, so `cv2.imwrite('x.heic', ...)` throws
    (measured). Adding them would mean pulling in a new dependency such as
    pillow-heif. **Copying as it is** the .HIF original next to the RAW is
    already possible with include_companions - that path needs no encoder.
    """

    JPEG = "jpeg"
    PNG = "png"
    WEBP = "webp"
    TIFF = "tiff"

    @property
    def suffix(self) -> str:
        return {"jpeg": ".jpg", "png": ".png",
                "webp": ".webp", "tiff": ".tif"}[self.value]

    @property
    def supports_icc(self) -> bool:
        """Whether the format can carry a colour profile **without touching
        the pixels**.

        Only WebP falls out - putting ICC in would mean turning the RIFF
        into the extended form (VP8X), and that is a lot of work for what
        this format is used for. Saving it again is an option too, but that
        is a re-compression so the quality drops (measured: re-saving a
        JPEG with PIL gave 14,880 -> 6,895 bytes, up to 14 levels of pixel
        difference).
        """
        return self in (ExportFormat.JPEG, ExportFormat.PNG, ExportFormat.TIFF)

    @property
    def supports_16bit(self) -> bool:
        """Whether the format can be saved in 16 bits.

        **Give uint16 to JPEG or WebP and it quietly drops to 8 bits** -
        cv2 leaves a warning and carries on (measured: "Unsupported depth
        ... fallbacked to CV_8U"). It has to be locked out on screen in
        advance so you never get "I saved it as 16-bit and 8-bit came
        out".
        """
        return self in (ExportFormat.PNG, ExportFormat.TIFF)


class ExportColorSpace(str, Enum):
    """The colour space of the exported file.

    **It does not just attach the tag, it converts the pixels too** - do
    only one and the viewer reads the numbers as belonging to a different
    space and the colours go wrong (core/develop/icc.py).

    sRGB is the default for the screen and for handing files over on
    social. Adobe RGB is wider on the green and cyan side and is used in
    print workflows - in exchange, loaded into a viewer that does no colour
    management it looks desaturated, so unless the file is going there sRGB
    is the safe choice.
    """

    SRGB = "srgb"
    ADOBE_RGB = "adobe_rgb"

    @property
    def label(self) -> str:
        return {"srgb": "sRGB",
                "adobe_rgb": "Adobe RGB"}[self.value]


class ResizeMode(str, Enum):
    NONE = "none"
    LONG_EDGE = "long_edge"
    PERCENT = "percent"


@dataclass
class ExportOptions:
    """The whole of the export behaviour."""

    move: bool = False

    include_companions: bool = False
    """Whether to export the JPG/HIF/XMP saved next to the RAW as well.

    Off by default. Shooting RAW+HEIF doubles the number of files per
    frame, and for a culling result the RAW alone is usually all that is
    needed. Better that whoever needs it turns it on than that the size
    doubles without them knowing.
    """

    apply_develop: bool = True

    grades: tuple[str, ...] = ALL_GRADES
    """The grades to export. ("keep",) sends out keep only.

    Handing over a culling result usually needs keep alone, but a backup
    needs all of it. Combined with move mode it also does "shift only the
    rejects into another folder".
    """

    copy_raw: bool = True
    """Whether to export the original RAW too. Off, only the developed
    image goes out."""

    image_format: ExportFormat = ExportFormat.JPEG
    quality: int = 95

    bit_depth: int = 8
    """Bit depth to save at. Only PNG and TIFF take 16 (see
    supports_16bit).

    The pipeline flows in float32 to begin with, so choosing 16 does not
    change the computation, only the final quantisation. Measured, the
    tonal steps really are preserved - 5.44 million unique levels per
    channel right after demosaic, and 4.77~6.04 million even after passing
    each adjustment stage (the 8-bit ceiling is 256).
    """

    color_space: ExportColorSpace = ExportColorSpace.SRGB
    """Colour space of the exported file. The pixel conversion and the ICC
    embed go together.

    WebP cannot carry ICC (supports_icc), so it is turned back to sRGB -
    converting and then failing to attach the tag means the viewer reads it
    as sRGB and **a file with the wrong colours** goes out. Better not to
    convert at all than that.
    """

    resize_mode: ResizeMode = ResizeMode.NONE
    resize_long_edge: int = 2048
    resize_percent: int = 50

    filename_pattern: str = "{name}"
    """The filename rule. {name} {index} {grade} {date} {score} can be
    used."""

    subfolder_by_grade: bool = True
    """Off, no grade folders are made and everything collects in one
    place."""

    subfolder_by_place: bool = False
    """Whether to split frames with the same GPS location into place
    folders (core/places.py).

    Off by default. If the body has no GPS the coordinates never go in at
    all, so left on everything lands in the single "no location" folder
    (export.NO_PLACE_FOLDER) and all it does is add one more folder
    (measured: 0 of 300 A6700 frames had GPS). It only means something on a
    batch that has locations.

    Place is on the outside, grade on the inside - the other way round, the
    keep and review of the same place sit far apart and you cannot see "the
    result for this place" at a glance.
    """

    def __post_init__(self) -> None:
        # coerce to the enum even if a string came in (widget round-trips)
        if not isinstance(self.image_format, ExportFormat):
            try:
                self.image_format = ExportFormat(self.image_format)
            except ValueError:
                self.image_format = ExportFormat.JPEG
        if not isinstance(self.resize_mode, ResizeMode):
            try:
                self.resize_mode = ResizeMode(self.resize_mode)
            except ValueError:
                self.resize_mode = ResizeMode.NONE

        if not isinstance(self.color_space, ExportColorSpace):
            try:
                self.color_space = ExportColorSpace(self.color_space)
            except ValueError:
                self.color_space = ExportColorSpace.SRGB

        # Depths and colour spaces the format cannot take are reverted
        # here. There is a path that queues something up and then changes
        # only the format to JPEG, so the screen lock alone cannot
        # guarantee nothing leaks through.
        self.bit_depth = 16 if self.bit_depth == 16 else 8
        if self.bit_depth == 16 and not self.image_format.supports_16bit:
            self.bit_depth = 8
        if not self.image_format.supports_icc:
            self.color_space = ExportColorSpace.SRGB

        # The grade list may arrive as a single string or have unknown
        # values mixed in. If filtering leaves it empty it reverts to
        # 'all' - better than nothing going out at all.
        selected = self.grades
        if isinstance(selected, str):
            selected = (selected,)
        cleaned = tuple(g for g in (selected or ()) if g in ALL_GRADES)
        self.grades = cleaned or ALL_GRADES

    def wants_grade(self, grade) -> bool:
        """Whether to export this grade."""
        return getattr(grade, "value", grade) in self.grades

    def target_long_edge(self, source_long_edge: int | None = None) -> int | None:
        """Long-edge pixels to apply when developing. None is full size."""
        if self.resize_mode is ResizeMode.LONG_EDGE:
            return max(64, self.resize_long_edge)
        if self.resize_mode is ResizeMode.PERCENT and source_long_edge:
            return max(64, int(source_long_edge * self.resize_percent / 100))
        return None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["image_format"] = self.image_format.value
        data["resize_mode"] = self.resize_mode.value
        data["color_space"] = self.color_space.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "ExportOptions":
        valid = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in valid})


_INVALID_NAME = re.compile(r'[<>:"/\\|?*]')


def _capture_time(record: ImageRecord) -> datetime:
    """When the shot was taken, for {date} and {time}.

    The analysis metadata is the fast path, but it is not always there -
    the queue can hold shots that were never analysed in this session, and
    a record restored from an old cache can carry none. Falling straight
    back to "now" is the wrong answer: the user asks for {date} because
    they want the *capture* date, and a filename quietly stamped with the
    export date is indistinguishable from a correct one until much later.
    So we read the file's EXIF before giving up.

    The last resort is the file's own modification time, which for a card
    straight out of the camera is the capture time. Only if even that
    fails do we use now().
    """
    if record.metadata and record.metadata.capture_time:
        return record.metadata.capture_time

    try:
        from .raw_io import read_metadata

        meta = read_metadata(record.path)
        if meta and meta.capture_time:
            return meta.capture_time
    except Exception:  # noqa: BLE001 - a filename must never stop an export
        log.debug("촬영 시각을 EXIF에서 읽지 못했습니다: %s",
                  record.path.name, exc_info=True)

    try:
        return datetime.fromtimestamp(record.path.stat().st_mtime)
    except OSError:
        return datetime.now()


def format_filename(
    pattern: str, record: ImageRecord, index: int, suffix: str
) -> str:
    """Builds the filename according to the rule.

    An unknown placeholder is left as it is - delete it quietly and the
    user never notices their typo.
    """
    capture = _capture_time(record)

    values = {
        "name": record.path.stem,
        "index": f"{index:04d}",
        "grade": record.final_grade.value,
        "date": capture.strftime("%Y%m%d"),
        "time": capture.strftime("%H%M%S"),
        "score": f"{record.score:.0f}",
    }

    result = pattern
    for key, value in values.items():
        result = result.replace("{" + key + "}", str(value))

    # Strip the dots and spaces off both ends. Starting with a dot makes it
    # a nameless hidden file (with the pattern "." the result is just
    # ".jpg") that Explorer does not show, and a trailing dot or space is
    # silently trimmed by Windows when it creates the file, so the name we
    # checked and the real name diverge. Dots in the middle are left alone
    # - the user wrote those.
    result = _INVALID_NAME.sub("_", result).strip(" .")
    if not result:
        result = _INVALID_NAME.sub("_", record.path.stem).strip(" .")
    return f"{result or '이름없음'}{suffix}"
