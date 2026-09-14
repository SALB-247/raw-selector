"""RAW file input and output.

Getting a 4000-frame batch through in a practical amount of time rules out a
full demosaic (1~2 seconds per frame). We pull out the full-size JPEG preview
embedded in the RAW instead. The A6700 embeds roughly a 6000x4000 preview,
which is plenty for focus scoring.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import exifread
import numpy as np
import rawpy
from PIL import Image

log = logging.getLogger(__name__)

RAW_EXTENSIONS = {
    ".arw", ".srf", ".sr2",           # Sony
    ".cr2", ".cr3", ".crw",           # Canon (CR3 needs LibRaw 0.20+)
    ".nef", ".nrw",                   # Nikon
    ".raf",                           # Fujifilm
    ".orf",                           # Olympus / OM System
    ".rw2",                           # Panasonic
    ".pef", ".ptx",                   # Pentax
    ".dng",                           # Adobe, general purpose
    ".srw",                           # Samsung
    ".3fr", ".fff",                   # Hasselblad
    ".iiq",                           # Phase One
    ".mrw",                           # Minolta
    ".rwl",                           # Leica
    ".x3f",                           # Sigma
    ".dcr", ".kdc",                   # Kodak
    ".erf",                           # Epson
    ".mef",                           # Mamiya
    ".bay",                           # Casio
    ".raw",                           # Panasonic/Leica legacy, general use
}
"""The supported extensions.

These are the formats LibRaw 0.22.1 handles. The A6700 (ARW) is the main
target, but the same culling flow applies unchanged to other bodies.

What we verified against real files is ARW (ILCE-6700) and CR3 (EOS R6
Mark II). The remainder are based on LibRaw's supported list; a file that
will not open is recorded as an error during analysis and the batch
carries on.

`.raw` is a general-purpose extension every manufacturer uses differently, so
a file that is not RAW at all can end up here. In that case LibRaw fails to
open it and only that one frame is left as a failure.

Extension comparison always goes through lower(). Cameras differ on case
(.ARW/.arw, .NEF/.nef), and so does whether the filesystem distinguishes it -
the Mac's default APFS is **case-insensitive**, Linux's ext4 is
case-sensitive, and a Mac can be formatted to be case-sensitive as well.
"""

#: Working space name -> the output colour space LibRaw knows
#: (WORKING_SPACE in develop/icc.py).
_RAWPY_COLOR_SPACE = {
    "srgb": rawpy.ColorSpace.sRGB,
    "adobe_rgb": rawpy.ColorSpace.Adobe,
    "prophoto": rawpy.ColorSpace.ProPhoto,
}

JPEG_EXTENSIONS = {".jpg", ".jpeg"}
"""Compressed images that open directly. cv2 decodes them - measured."""

HEIF_EXTENSIONS = {".hif", ".heic", ".heif"}
"""The HEIF family. Sony writes .HIF, Apple writes .HEIC.

Both are ISO-BMFF containers (ftyp heix and the like), so none of cv2, PIL or
rawpy can open them. That is why `pillow-heif` (libheif) is a hard
dependency - confirmed by measurement:

    DSC02290.HIF (ftyp heix, 8.7MB) -> 6192x4128, 6 faces
    sharpness 61.6 against 60.8 for the ARW of the same scene

libheif is LGPL-3. See THIRD_PARTY.md for the distribution terms.
"""

EDITABLE_IMAGE_EXTENSIONS = JPEG_EXTENSIONS | HEIF_EXTENSIONS
"""Formats we can score and adjust in place of a RAW when there is none.

Some people only ever shoot JPEG. Those files have to be cullable and
adjustable too - the latitude is simply different. They are not sensor data
but an already developed result squeezed into 8 bits, so blown highlights do
not come back and gradation breaks up under large exposure or colour
temperature adjustments. `is_editable_image()` tells them apart and says so
on screen.
"""

RAW_FILE_FILTER = (
    "RAW 파일 (" + " ".join(f"*{e}" for e in sorted(RAW_EXTENSIONS)) + ")"
    ";;이미지 (" + " ".join(f"*{e}" for e in sorted(EDITABLE_IMAGE_EXTENSIONS)) + ")"
)
"""Filter string for the file dialog."""

SIDECAR_EXTENSIONS = {".jpg", ".jpeg", ".xmp", ".arw.xmp"}
"""Files that are paired with a RAW and have to move along with it."""

SMALL_PREVIEW_EXTENSIONS = {".rw2"}
"""Formats **known** to embed a preview far smaller than the sensor.

Used only to decide whether the analysis start dialog offers the option and
what time estimate to quote - it has to count the files without opening
them. The real decision is made per file, by measuring the preview/sensor
ratio directly (load_preview). So a body that is not on this list but is in
the same situation is rescued along with the rest once the option is on, and
a body that is on the list is simply passed over if its preview is large
enough.
"""


def has_small_preview(path: Path) -> bool:
    """Is this file a known 'small preview' format (extension only)?"""
    return path.suffix.lower() in SMALL_PREVIEW_EXTENSIONS


def is_raw(path: Path) -> bool:
    return path.suffix.lower() in RAW_EXTENSIONS


def is_editable_image(path: Path) -> bool:
    """Whether this is not a RAW but can still be scored and adjusted."""
    return path.suffix.lower() in EDITABLE_IMAGE_EXTENSIONS


@dataclass(frozen=True)
class RawMetadata:
    """The minimum pulled from RAW EXIF that grouping and diagnostics need."""

    path: Path
    capture_time: datetime | None = None
    camera_model: str | None = None
    camera_make: str | None = None
    """Manufacturer (EXIF Make). Used to build the body name for lens DB
    lookups.

    The camera's EXIF Model does not carry the manufacturer ("EOS R6 Mark
    II"), whereas lensfun writes it with the manufacturer attached
    ("Canon EOS R6m2").
    """

    lens_model: str | None = None
    iso: int | None = None
    shutter_speed: float | None = None  # in seconds
    aperture: float | None = None
    focal_length: float | None = None
    focal_length_35mm: float | None = None
    """Equivalent focal length (35mm basis). For the details panel.

    Sony and Nikon put it straight into EXIF FocalLengthIn35mmFilm, but
    **Canon does not use that tag at all** (measured: 0 Canon frames out of
    305), so we work the sensor size back out of the FocalPlane resolution
    tags and multiply by the scale factor - the same method as exiftool's
    ScaleFactor35efl, measured error ±0.3% (R6M3 1.002, R3 1.000, R5 0.998).
    RW2 carries it in plain form in the embedded JPEG's ExifIFD 0xA405.
    """

    af_area_mode: str | None = None
    """The AF area mode the camera recorded (the camera's own wording, in
    English).

    maker_meta.af_area_mode fills this in. Only verified values get a name;
    an unknown value stays None - a blank beats a quietly wrong name. It is
    close to a proper noun, much like a lens name, so we do not translate it.
    """

    orientation: int = 1  # EXIF Orientation (1~8)

    latitude: float | None = None
    longitude: float | None = None
    """Capture location (in degrees, south/west negative). None if absent.

    Even a body without GPS picks this up when shot paired with a phone.
    Measured (A6700, 300 frames): **not one frame had it** - shot without
    the pairing, nothing is recorded at all.

    This value is **read only**. It is never written to an exported file
    under any circumstances (see develop/metadata.py) - location is the most
    dangerous item there is to leak by accident.
    """

    @property
    def has_location(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def shutter_display(self) -> str:
        if not self.shutter_speed:
            return "-"
        if self.shutter_speed >= 1:
            return f"{self.shutter_speed:g}s"
        return f"1/{round(1 / self.shutter_speed)}s"


class PreviewError(RuntimeError):
    """Raised when no path at all produced a preview."""


def iter_raw_files(folder: Path, recursive: bool = True) -> list[Path]:
    """Find the files to score in a folder and return them as a sorted list.

    Returns the RAWs, plus JPEG/HEIF **where there is no RAW**.

    When a RAW and a JPEG of the same name sit side by side (the camera's
    RAW+JPEG recording), only the RAW is used. Take both and the same photo
    appears twice, so the frame count and the keep ratio are both off by a
    factor of two. The RAW is the better one for scoring and for adjustment
    alike.

    The `_keep` / `_review` / `_reject` folders export creates are excluded,
    since on a rescan they would make us process the originals twice.

    The cache folder (`.raw_selector_cache`) is excluded for the same reason.
    Its `thumbs/*.jpg` started being picked up as photos once this function
    began returning JPEGs too - reopening an already analysed folder inflated
    the frame count by the number of thumbnails and threw scene grouping and
    the keep ratio off completely.
    """
    from .appinfo import CACHE_DIR_NAME, LEGACY_CACHE_DIR_NAMES
    from .types import OUTPUT_DIR_NAMES  # types uses raw_io, so import late

    skip_dirs = OUTPUT_DIR_NAMES | {CACHE_DIR_NAME, *LEGACY_CACHE_DIR_NAMES}
    pattern = "**/*" if recursive else "*"
    raws: list[Path] = []
    others: list[Path] = []
    for path in folder.glob(pattern):
        if not path.is_file():
            continue
        # Hidden files are not photos. In particular the AppleDouble files
        # macOS creates on exFAT/SMB/NTFS (`._DSC1234.JPG`) carry a .JPG
        # extension, so left alone they get picked up as photos - a 4KB
        # resource fork doubles the frame count and every one of them fails,
        # halving the keep ratio (measured 16.0% -> 8.0%). Their names start
        # with `._`, so the pairing check (stem comparison) does not catch
        # them either.
        if path.name.startswith("."):
            continue
        relative_parts = path.relative_to(folder).parts[:-1]
        if any(part in skip_dirs for part in relative_parts):
            continue
        if is_raw(path):
            raws.append(path)
        elif is_editable_image(path):
            others.append(path)

    # Pairing looks only at **the same name in the same folder**. A file
    # with the same name in a different folder may be from another shoot.
    raw_keys = {(p.parent, p.stem.lower()) for p in raws}
    unpaired = [p for p in others if (p.parent, p.stem.lower()) not in raw_keys]
    return sorted(raws + unpaired)


# ---------------------------------------------------------------- orientation


def apply_orientation(image: np.ndarray, orientation: int) -> np.ndarray:
    """Apply EXIF Orientation (1~8) to the image."""
    if orientation <= 1 or orientation > 8:
        return image
    if orientation == 2:
        return cv2.flip(image, 1)
    if orientation == 3:
        return cv2.rotate(image, cv2.ROTATE_180)
    if orientation == 4:
        return cv2.flip(image, 0)
    if orientation == 5:
        return cv2.rotate(cv2.flip(image, 1), cv2.ROTATE_90_COUNTERCLOCKWISE)
    if orientation == 6:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    if orientation == 7:
        return cv2.rotate(cv2.flip(image, 1), cv2.ROTATE_90_CLOCKWISE)
    return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)  # 8


def _jpeg_orientation(data: bytes) -> int:
    """Read only the EXIF Orientation out of JPEG bytes.

    PIL's open() is lazy, so it reads the EXIF and stops without decoding
    any pixels.
    """
    try:
        with Image.open(io.BytesIO(data)) as im:
            return int(im.getexif().get(0x0112, 1))
    except Exception:  # noqa: BLE001 - broken EXIF means no orientation
        return 1


def resize_long_edge(image: np.ndarray, target: int) -> np.ndarray:
    """Shrink so the long edge is target. Returns as-is if already smaller."""
    h, w = image.shape[:2]
    long_edge = max(h, w)
    if long_edge <= target:
        return image
    scale = target / long_edge
    return cv2.resize(
        image,
        (max(1, round(w * scale)), max(1, round(h * scale))),
        interpolation=cv2.INTER_AREA,
    )


def imwrite_unicode(path: Path, image: np.ndarray, params: list | None = None) -> bool:
    """Unicode-safe replacement for cv2.imwrite.

    On Windows, cv2.imwrite fails silently when writing to a Hangul or other
    non-ASCII path (returns False, no file appears). Hangul paths are common
    - KakaoTalk's received-files folder, for one - so we encode in memory and
    write with Python's open.
    """
    path = Path(path)
    ext = path.suffix if path.suffix else ".png"
    try:
        ok, buffer = cv2.imencode(ext, image, params or [])
        if not ok:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(buffer.tobytes())
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("imwrite_unicode 실패 %s: %s", path.name, exc)
        return False


def imread_unicode(path: Path, flags: int = cv2.IMREAD_COLOR) -> "np.ndarray | None":
    """Unicode-safe replacement for cv2.imread. None on failure."""
    try:
        data = np.frombuffer(Path(path).read_bytes(), dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, flags)


# ---------------------------------------------------------------- non-RAW decode


def _decode_heif(path: Path) -> np.ndarray | None:
    """Decode HEIF (.HIF/.HEIC) to BGR. None if it cannot be read.

    The libheif bundled inside `pillow-heif` is what actually unpacks it.
    Measured: DSC02290.HIF (ftyp heix, 8.7MB) -> 6192x4128, 6 faces, the same
    as its paired ARW.

    The missing-library case is wrapped here as well. It is a hard dependency
    so it cannot normally be absent, but if it is, failing that one file
    beats an ImportError killing the whole app. It does have to be worded
    apart from 'the file is broken' - when it is missing from a build, no
    amount of swapping files will get anything to open.
    """
    try:
        import pillow_heif  # noqa: PLC0415
    except ImportError as exc:
        raise PreviewError(
            f"{path.name}: HEIF 디코더가 없습니다 — pillow-heif 를 설치하십시오."
        ) from exc

    try:
        heif = pillow_heif.read_heif(str(path))
        rgb = np.asarray(heif.to_pillow().convert("RGB"))
    except Exception as exc:  # noqa: BLE001 - any failure fails one frame only
        log.debug("HEIF 디코드 실패 %s: %s", path.name, exc)
        return None
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _decode_heif_16(path: Path) -> np.ndarray | None:
    """Decode HEIF at its native bit depth, returning float32 BGR (0~255).

    Sony HIF is 10-bit, but the 8-bit path above (convert("RGB")) squeezes
    that down to 256 steps. Enough for analysis and thumbnails, but as **the
    starting point for adjustment** it loses latitude - raise the exposure
    and the 8-bit gradation opens right up into banding. Measured
    (DSC02290.HIF): this path takes the distinct levels per channel from
    256 -> 1,024.

    Rotation matches the 8-bit path - libheif applies the container
    transforms (irot/imir) at decode time, and the caller (load_demosaiced)
    checks EXIF orientation once more with the same function the 8-bit path
    uses.

    None when 10 bits cannot be had (older pillow-heif, 8-bit HEIC) - the
    caller falls back to the 8-bit path.
    """
    try:
        import pillow_heif  # noqa: PLC0415
    except ImportError:
        return None

    try:
        heif = pillow_heif.open_heif(str(path), convert_hdr_to_8bit=False)
        image = heif[0] if hasattr(heif, "__getitem__") else heif
        mode = str(getattr(image, "mode", ""))
        if ";16" not in mode:          # 8-bit source (iPhone HEIC) - no gain
            return None
        array = np.asarray(image)
    except Exception as exc:  # noqa: BLE001 - failure falls back to 8-bit
        log.debug("HEIF 16비트 디코드 실패 %s: %s", path.name, exc)
        return None

    if array.ndim != 3 or array.dtype != np.uint16:
        return None
    if array.shape[2] == 4:            # alpha is not something we adjust
        array = array[:, :, :3]
    if array.shape[2] != 3:
        return None
    # RGB 16-bit (0~65535) -> float BGR 0~255. The 10-bit gradation survives
    # in the fractional part.
    return (array[:, :, ::-1].astype(np.float32) / 257.0)


def _tags_from_heif(path: Path) -> dict:
    """Read the EXIF block inside a HEIF container with exifread.

    The block is a 6-byte `Exif\\0\\0` followed by an ordinary TIFF. Strip
    that header off and hand it over, and it parses through exactly the same
    path as a RAW.
    """
    try:
        import pillow_heif  # noqa: PLC0415

        payload = pillow_heif.open_heif(str(path)).info.get("exif")
    except Exception as exc:  # noqa: BLE001
        log.debug("HEIF EXIF 추출 실패 %s: %s", path.name, exc)
        return {}
    if not payload:
        return {}

    if payload[:6] == b"Exif\x00\x00":
        payload = payload[6:]
    try:
        return exifread.process_file(io.BytesIO(payload), details=False)
    except Exception as exc:  # noqa: BLE001
        log.debug("HEIF EXIF 파싱 실패 %s: %s", path.name, exc)
        return {}


def load_image_file(path: Path) -> np.ndarray:
    """Read a JPEG or HEIF as orientation-corrected BGR.

    This is not a RAW but **an already developed result**. Some things
    cannot be undone: blown highlights have no data left to bring back, and
    being 8-bit, gradation breaks up under large exposure or colour
    temperature adjustments. It is still enough for culling and for light
    adjustment.
    """
    suffix = path.suffix.lower()
    if suffix in HEIF_EXTENSIONS:
        image = _decode_heif(path)
        if image is None:
            raise PreviewError(f"HEIF를 열지 못했습니다: {path.name}")
    else:
        # Given only IMREAD_COLOR, cv2.imdecode **applies EXIF orientation
        # automatically**. Left that way, apply_orientation below runs a
        # second time and a portrait frame ends up 180° off (measured on a
        # Z9 portrait JPEG: stored 8256x5504 -> cv2 stood it up as 5504x8256,
        # and we laid it back down to 8256x5504). Faces ended up upside
        # down, so detection failed outright. Orientation is handled in
        # exactly one place, apply_orientation.
        image = imread_unicode(
            path, cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
        if image is None:
            raise PreviewError(f"이미지를 열지 못했습니다: {path.name}")

    # EXIF orientation is common in JPEG. Ignore it and portrait frames end
    # up lying on their side.
    try:
        image = apply_orientation(image, _jpeg_orientation(path.read_bytes()))
    except OSError:
        pass
    return image


# ---------------------------------------------------------------- preview extraction


#: An embedded preview smaller than this fraction of the sensor's long edge
#: counts as a "small preview".
#:
#: Measured - ARW (A6700) 99%, CR3 99%, CR2 99%, **RW2 (DC-S5M2X) 32%**.
#: The gap between the normal bodies and Panasonic is wide, so 0.6 sits
#: safely close to neither. A half demosaic result (50%) falls below this
#: line, but scoring only ever runs on the preview, so there is no infinite
#: recursion.
SMALL_PREVIEW_RATIO = 0.6


def load_preview(path: Path, max_long_edge: int | None = None,
                 demosaic_small: bool = False) -> np.ndarray:
    """Return the preview as an orientation-corrected BGR image.

    For a RAW:
      1) the embedded JPEG preview (fastest, the normal path)
      2) the embedded bitmap thumbnail
      3) a half-size demosaic as a last resort - slow, so it logs a warning

    For a non-RAW (JPEG/HEIF) the file itself is the preview.

    With demosaic_small on, **an embedded preview much smaller than the
    sensor** (Panasonic RW2) is thrown away and rebuilt with a half
    demosaic. That restores the scoring premise of measuring sharpness at
    the original resolution, at the cost of speed (see
    AnalyzeConfig.demosaic_small_preview).
    """
    if is_editable_image(path):
        image = load_image_file(path)
        if max_long_edge:
            image = resize_long_edge(image, max_long_edge)
        return image

    try:
        with rawpy.imread(str(path)) as raw:
            try:
                thumb = raw.extract_thumb()
            except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
                thumb = None

            if thumb is not None and thumb.format == rawpy.ThumbFormat.JPEG:
                # IMREAD_IGNORE_ORIENTATION is required - without it
                # imdecode has already applied the embedded preview's EXIF
                # orientation, and apply_orientation below turns it once
                # more, leaving a portrait frame 180° off (measured on
                # DSC_0007.NEF orientation=8: standing correctly at
                # 3712x5568, laid back down to 5568x3712).
                image = cv2.imdecode(
                    np.frombuffer(thumb.data, dtype=np.uint8),
                    cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION,
                )
                if image is None:
                    raise PreviewError(f"내장 JPEG 프리뷰 디코딩 실패: {path.name}")
                image = apply_orientation(image, _jpeg_orientation(thumb.data))
            elif thumb is not None and thumb.format == rawpy.ThumbFormat.BITMAP:
                image = cv2.cvtColor(thumb.data, cv2.COLOR_RGB2BGR)
                image = apply_orientation(image, _flip_to_orientation(raw.sizes.flip))
            else:
                log.warning("프리뷰 없음, 디모자이크로 폴백 (느림): %s", path.name)
                rgb = raw.postprocess(
                    half_size=True,
                    use_camera_wb=True,
                    no_auto_bright=True,
                    output_bps=8,
                )
                image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

            # Is this preview too small compared with the sensor? It has to
            # be measured **while raw is still open** to know the sensor
            # size at all.
            too_small = False
            if demosaic_small:
                sensor_long = max(raw.sizes.width, raw.sizes.height)
                preview_long = max(image.shape[:2])
                too_small = (sensor_long > 0
                             and preview_long < sensor_long * SMALL_PREVIEW_RATIO)
    except PreviewError:
        raise
    except Exception as exc:  # noqa: BLE001 - fail the file, not the batch
        raise PreviewError(f"{path.name}: {exc}") from exc

    if too_small:
        # Reopen the file. The demosaic cost dominates (measured 15.9 ->
        # 79.9ms per frame), so the cost of opening disappears into it. It
        # uses the same baseline as the adjust window, so the tone lands in
        # the normal range like a camera JPEG preview - measure off a flat
        # neutral develop instead and the same sharpness coefficients would
        # be applied on top of a different contrast.
        try:
            image = to_display(load_demosaiced(path, half_size=True))
        except Exception as exc:  # noqa: BLE001 - on failure use the preview
            log.warning("작은 프리뷰 디모자이크 실패, 내장 프리뷰 사용 %s: %s",
                        path.name, exc)

    if max_long_edge:
        image = resize_long_edge(image, max_long_edge)
    return image


@dataclass(frozen=True)
class WhiteBalance:
    """White balance information from a RAW.

    `camera` is the as-shot multipliers, `daylight` the camera's daylight
    calibration multipliers. Knowing both lets us work out the camera
    multipliers for any target colour temperature relative to that camera's
    own calibration.
    """

    camera: tuple[float, ...]
    daylight: tuple[float, ...]
    as_shot_kelvin: int

    @property
    def engine_wb(self) -> tuple[tuple, tuple]:
        """(camera, daylight) tuple for the engine's _apply_white_balance."""
        return (self.camera, self.daylight)


_PER_FILE_CACHE_LIMIT = 512
_WB_CACHE: dict = {}
"""White balance by file key (path, mtime, size). Filled by the demosaic,
which has the file open anyway - reading it separately opens and unpacks
the RAW again, 0.18s on a 50MP ARW, on every switch of shot."""


def _file_key(path: Path):
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    return (str(path), stat.st_mtime_ns, stat.st_size)


def _remember(cache: dict, key, value) -> None:
    if key is None:
        return
    if len(cache) >= _PER_FILE_CACHE_LIMIT:
        cache.clear()
    cache[key] = value


def _white_balance_of(raw) -> "WhiteBalance | None":
    camera = tuple(float(x) for x in raw.camera_whitebalance)
    daylight = tuple(float(x) for x in raw.daylight_whitebalance)
    if len(camera) < 3 or len(daylight) < 3 or daylight[1] == 0:
        return None
    return WhiteBalance(camera, daylight, _estimate_as_shot_kelvin(camera, daylight))


def read_white_balance(path: Path) -> "WhiteBalance | None":
    """Read WB multipliers from a RAW and estimate the as-shot kelvin.

    Fast, because it reads metadata only, with no demosaic. On failure it
    returns None so the caller can carry on without any colour temperature
    adjustment. A file the demosaic has already opened is answered from
    the cache without touching the file.
    """
    key = _file_key(path)
    if key in _WB_CACHE:
        return _WB_CACHE[key]
    try:
        with rawpy.imread(str(path)) as raw:
            wb = _white_balance_of(raw)
    except Exception:  # noqa: BLE001 - adjustment goes on even without WB
        return _white_balance_without_libraw(path)
    if wb is None:
        return _white_balance_without_libraw(path)
    _remember(_WB_CACHE, key, wb)
    return wb


NIKON_DAYLIGHT_FALLBACK = (1.9578, 0.945, 1.1413)
"""The daylight multipliers LibRaw uses for the Nikon Z line.

The daylight values are a decoder-internal constant rather than something in
the file, so when LibRaw cannot open the file at all there is nowhere to get
them from. Colour temperature estimation needs a baseline, so we use these -
without them the colour temperature slider does not work at all.
"""


def _white_balance_without_libraw(path: Path) -> "WhiteBalance | None":
    """Salvage WB from metadata alone for files LibRaw cannot open.

    Nikon High Efficiency (HE/HE*) compressed NEF lands here. The pixels
    cannot be unpacked, but the MakerNote is ordinary TIFF and reads fine.
    Without this, the colour temperature control in the adjust window dies
    completely.

    Cross-checked against Nikon files LibRaw does open, the values matched
    camera_whitebalance.
    """
    if path.suffix.lower() != ".nef":
        return None
    try:
        from .nef_meta import read_white_balance_levels

        levels = read_white_balance_levels(path)
    except Exception:  # noqa: BLE001
        log.debug("%s: 니콘 WB 폴백 실패", path.name, exc_info=True)
        return None
    if not levels:
        return None

    camera = (levels[0], levels[1], levels[2], levels[1])
    daylight = (*NIKON_DAYLIGHT_FALLBACK, NIKON_DAYLIGHT_FALLBACK[1])
    log.info("%s: LibRaw 대신 메타데이터에서 화이트밸런스를 읽었습니다", path.name)
    return WhiteBalance(camera, daylight,
                        _estimate_as_shot_kelvin(camera, daylight))


def _estimate_as_shot_kelvin(camera: tuple, daylight: tuple) -> int:
    """Find which colour temperature's camera multipliers the as-shot
    multipliers come closest to.

    Anchoring on the camera's daylight calibration is more stable than
    comparing blackbody colours directly (a direct comparison gets pushed
    around by a saturated R channel).
    """
    from .develop.engine import NEUTRAL_KELVIN, _kelvin_to_rgb

    day = np.array(daylight[:3], dtype=np.float64)
    cam = np.array(camera[:3], dtype=np.float64)
    if cam[1] == 0:
        return NEUTRAL_KELVIN  # G multiplier 0: cannot normalise - go neutral
    cam = cam / cam[1]
    ref = _kelvin_to_rgb(NEUTRAL_KELVIN)
    best_t, best_err = NEUTRAL_KELVIN, float("inf")
    for kelvin in range(2000, 12001, 25):
        mult = day * (ref / _kelvin_to_rgb(kelvin))
        mult = mult / mult[1]
        err = float(np.sum((mult - cam) ** 2))
        if err < best_err:
            best_err, best_t = err, kelvin
    return int(best_t)


def load_demosaiced(
    path: Path,
    target_kelvin: int | None = None,
    half_size: bool = False,
    apply_profile: bool = True,
    calibration=None,
    highlight_recovery: bool = False,
) -> np.ndarray:
    """Actually demosaic a RAW into an orientation-corrected BGR image.

    It develops the sensor data directly rather than using the embedded JPEG
    preview, so it is slow (1~2 seconds at 24MP) but the colour, gradation
    and detail are accurate. This is the baseline for the adjust screen and
    for export.

    Given target_kelvin, it demosaics with the white balance set to that
    absolute colour temperature. The multipliers are computed relative to
    the camera's daylight calibration, so unlike the preview's approximation
    this is a real colour temperature conversion. Without it, as-shot
    (camera WB).

    With apply_profile True, the default camera profile (standard) is laid
    on top to give a natural starting point. A neutral demosaic is flat, and
    used as-is it looks lifeless.

    calibration is the per-body calibration measured on this PC. None looks
    it up from the file's body and applies it; passing False develops purely,
    with no calibration at all (needed so that measuring the calibration
    values does not feed back into itself).

    With highlight_recovery on, saturated highlights are rebuilt from the
    channels that are left (see BasicSettings.highlight_recovery). RAW only.

    **For a non-RAW (JPEG/HEIF) there is nothing to demosaic.** The file is
    lifted straight to float BGR and used as the starting point of the
    adjustment pipeline. Colour temperature, profile and body calibration
    all need sensor data to make sense, so none of them are applied - the
    camera has already applied its own and baked the result in. HEIF alone
    is taken at its native bit depth (10-bit) - squeezed to 8 bits it loses
    adjustment latitude.
    """
    if is_editable_image(path):
        image = None
        if path.suffix.lower() in HEIF_EXTENSIONS:  # noqa: SIM102
            # Only the adjustment starting point is taken at the native bit
            # depth. The analysis and thumbnail path (load_preview ->
            # load_image_file) stays 8-bit - so as not to disturb scoring
            # and the cache.
            image = _decode_heif_16(path)
            if image is not None:
                try:
                    image = apply_orientation(
                        image, _jpeg_orientation(path.read_bytes()))
                except OSError:
                    pass
        if image is None:
            image = load_image_file(path).astype(np.float32)

        # **An Adobe RGB original is moved into the working space (sRGB).**
        #
        # Left alone, two things go wrong. The screen (Qt) draws whatever it
        # is given as sRGB, so the preview looks desaturated; and exposure
        # linearises back through the sRGB curve while the actual encoding
        # is pure gamma 2.2, so the amount of light comes out wrong.
        #
        # Rather than carry a second space through the pipeline, we convert
        # once on the way in - preview, exposure and export then all agree
        # in one space. Choosing Adobe RGB again at export converts back at
        # that point.
        #
        # The analysis path (load_preview) is left alone. Scoring is mostly
        # about brightness so the gain is small, while it would mean
        # rebuilding the whole cache.
        from .develop.icc import WORKING_SPACE, to_working
        from .maker_meta import colour_space

        origin = colour_space(path)
        if origin != WORKING_SPACE:
            image = to_working(image, origin)

        if half_size:
            image = cv2.resize(image, (0, 0), fx=0.5, fy=0.5,
                               interpolation=cv2.INTER_AREA)
        return image

    with rawpy.imread(str(path)) as raw:
        # Dropping a 14-bit sensor straight to 8 bits crushes the gradation.
        # We take 16 bits and normalise to float 0~255 to keep the precision
        # (regardless of the file's bit depth).
        params = dict(no_auto_bright=True, output_bps=16, half_size=half_size)

        # The working space is decided here too. The camera -> working space
        # conversion happens inside LibRaw, so taking it narrow clips the
        # pixels before our code ever sees them (see WORKING_SPACE in
        # develop/icc.py).
        from .develop.icc import WORKING_SPACE

        if WORKING_SPACE != "srgb":
            params["output_color"] = _RAWPY_COLOR_SPACE[WORKING_SPACE]

        # Highlight recovery (blend) - rebuilds a saturated channel from the
        # channels that are left. LibRaw's default (0) simply clips at the
        # white level. blend reserves headroom equal to the WB gain, so it
        # comes out **1~1.5 stops darker overall** (measured: a linear
        # uniform factor, constant per file to within ±0.1%). The exposure
        # slider is the exact inverse, so bringing it back up is the user's
        # call - doing it silently would clip the headroom straight back off
        # and make the option pointless. See
        # BasicSettings.highlight_recovery.
        if highlight_recovery:
            params["highlight_mode"] = 2

        # A recent body LibRaw does not know cannot find the black pedestal
        # (the EOS R6 Mark III, for instance, reads as [0,38,113,78]), so
        # the pedestal is never subtracted, the whole frame lifts, and the
        # per-channel offset difference casts it magenta. We estimate it
        # from the sensor data directly and correct it. Supported bodies are
        # left alone.
        file_key = _file_key(path)
        black_override = _black_level_for(raw, file_key)
        if black_override is not None:
            params["user_black"] = black_override

        if target_kelvin and target_kelvin > 0:
            from .develop.engine import NEUTRAL_KELVIN, _kelvin_to_rgb

            # **Anchor on the camera's measured multipliers.** The model's
            # absolute values (daylight x K(5500)/K(target)) throw the
            # camera's as-shot multipliers away entirely, and since the
            # kelvin model has no tint (green-magenta) axis, that component
            # disappears along with them. Measured (DSC06598, izakaya LED):
            # at the estimated kelvin the model multipliers were off from
            # the measured ones by 8.2% in R, so the moment the slider was
            # committed at its as-shot displayed position, a yellow-green
            # cast appeared (18.8% of pixels off by more than 5 levels).
            #
            # We start from the camera multipliers and apply only the
            # model's **relative change**. When the target is the as-shot
            # estimate this lands exactly on the camera multipliers
            # (measured 0.00 levels) and the tint is preserved. It has to be
            # the same anchor as the preview gain in engine._wb_gain - if
            # the two diverge, the colour jumps the moment the slider is
            # released.
            camera = np.array(raw.camera_whitebalance[:3], dtype=np.float64)
            daylight = np.array(raw.daylight_whitebalance[:3], dtype=np.float64)
            if camera[1] > 0 and daylight[1] > 0:
                est = _estimate_as_shot_kelvin(tuple(camera), tuple(daylight))
                mult = camera * (_kelvin_to_rgb(est)
                                 / _kelvin_to_rgb(target_kelvin))
            else:
                # a file whose multipliers we cannot read - fall back to the
                # old general approximation
                mult = daylight * (_kelvin_to_rgb(NEUTRAL_KELVIN)
                                   / _kelvin_to_rgb(target_kelvin))
            params["user_wb"] = [float(mult[0]), float(mult[1]), float(mult[2]), float(mult[1])]
        else:
            params["use_camera_wb"] = True
        rgb = raw.postprocess(**params)
        # The file is open here - keep what the develop view asks for
        # next, so it does not open the file again. After postprocess:
        # read before it, the multipliers cost 0.16s.
        try:
            wb = _white_balance_of(raw)
            if wb is not None:
                _remember(_WB_CACHE, file_key, wb)
        except Exception:  # noqa: BLE001 - only a cache
            pass
    # postprocess has already applied the camera flip. We move 16-bit
    # (0~65535) to float 0~255 - the fraction survives, so the gradation
    # stays intact.
    image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR).astype(np.float32) / 257.0

    # If there is a body calibration measured on this PC, apply it first. It
    # has to come before the profile (the colour look) - calibration is
    # "matching the reference", the profile is a look laid on top of that,
    # so reversing the order twists the look along with it.
    if calibration is not False:
        image = _apply_calibration(image, path, calibration)

    if apply_profile:
        from .develop.engine import apply_camera_profile

        image = apply_camera_profile(image)
    return image


def _apply_calibration(image, path: Path, calibration):
    """Apply the stored body calibration. Returns the image as-is if none."""
    from .develop import calibration as calib

    try:
        if calibration is None:
            metadata = read_metadata(path)
            key = calib.camera_key(metadata.camera_make, metadata.camera_model)
            calibration = calib.load(key)
        return calib.apply(image, calibration)
    except Exception:  # noqa: BLE001 - a failure here must not stop develop
        log.debug("기종 보정 적용 실패: %s", path.name, exc_info=True)
        return image


def _channel_floors(raw) -> list[float] | None:
    """Measure the sensor floor directly, per Bayer position.

    This is the yardstick for deciding whether the per-channel black LibRaw
    reports is genuine. If it is a real offset, the same difference has to
    show up in the per-channel floor measured off the sensor.
    """
    try:
        colors = raw.raw_colors_visible
        image = raw.raw_image_visible
    except Exception:  # noqa: BLE001
        return None
    if image.size == 0:
        return None

    phase = _bayer_phase(colors)
    floors: list[float] = []
    for index in range(4):
        if phase is not None:
            # A 2x2 Bayer: each colour is one strided plane. Four boolean
            # masks over a 32MP frame took 0.55s (R6M3, on every load).
            oy, ox = phase[index]
            values = image[oy::2, ox::2].ravel()
        else:
            values = image[colors == index]
        if values.size < 256:
            return None
        # Median of the darkest 0.1%. A median rather than a mean, so hot
        # pixels do not shift it.
        count = max(64, values.size // 1000)
        darkest = np.partition(values, count)[:count]
        floors.append(float(np.median(darkest)))
    return floors


def _bayer_phase(colors: np.ndarray) -> "dict[int, tuple[int, int]] | None":
    """Colour index -> (row, column) offset of its plane in a 2x2 Bayer
    mosaic, read off the array's own first cell. None for anything else
    (X-Trans), where the caller falls back to masks."""
    try:
        cell = np.asarray(colors[:2, :2])
    except Exception:  # noqa: BLE001
        return None
    if cell.shape != (2, 2):
        return None
    phase = {int(cell[y, x]): (y, x) for y in range(2) for x in range(2)}
    if len(phase) != 4 or colors.shape[0] < 4 or colors.shape[1] < 4:
        return None
    # the pattern has to actually repeat with period 2
    if not np.array_equal(np.asarray(colors[2:4, 2:4]), cell):
        return None
    return phase


_BLACK_CACHE: dict = {}
"""The black-level decision by file key: (user_black or None, per-colour
pedestal to add back). Measuring the floors is a quarter second on a
32MP body LibRaw does not know, and the develop view demosaics the same
file twice (half for the screen, full for Full Render)."""


def _add_pedestal(raw, extras: "list[int]") -> None:
    """Add extras[colour] back into the sensor data in place (see
    _repair_black_level), clamped at the white level."""
    image = raw.raw_image  # writable view - postprocess uses these values
    colors = raw.raw_colors
    white = int(raw.white_level)
    phase = _bayer_phase(colors)
    for index in range(4):
        extra = int(extras[index])
        if extra <= 0:
            continue
        if phase is not None:
            oy, ox = phase[index]
            plane = image[oy::2, ox::2]
            plane[...] = np.minimum(plane.astype(np.int32) + extra,
                                    white).astype(image.dtype)
        else:
            mask = colors == index
            # Adding near saturation overflows white. Clamp at the white
            # level.
            image[mask] = np.minimum(
                image[mask].astype(np.int32) + extra, white
            ).astype(image.dtype)


def _black_level_for(raw, file_key) -> int | None:
    """_repair_black_level, with the decision kept by file key (see
    _file_key) between the two demosaics of one file - measuring the
    floors is a quarter second on a 32MP body LibRaw does not know. The
    pedestal is still added to this raw's own pixels, which are fresh."""
    if file_key is None:
        return _repair_black_level(raw)
    if file_key in _BLACK_CACHE:
        override, extras, fallback = _BLACK_CACHE[file_key]
        if extras is not None:
            try:
                _add_pedestal(raw, extras)
            except Exception:  # noqa: BLE001 - a failure here must not stop develop
                return fallback
        return override
    decision = _decide_black_level(raw)
    _remember(_BLACK_CACHE, file_key, decision)
    return decision[0]


def _repair_black_level(raw) -> int | None:
    """Correct bodies where LibRaw missed the black pedestal and return the
    value to use.

    On a supported body, black_level_per_channel matches the sensor floor
    (e.g. [2048]x4). On a body LibRaw does not know, the pedestal is missed
    entirely and very low values come out (measured: the EOS R6 Mark III
    reads [0,38,113,78] where the real floor is ~2000); left alone, the
    pedestal is never subtracted and the image lifts.

    But user_black only changes LibRaw's **global** black. The per-channel
    cblack is still subtracted on top of it, so in the example above blue
    alone loses another 113. Filling in the pedestal by itself therefore
    shaves blue down and the image lifts yellow-green instead (measured: on
    the R6 Mark III, B 0.750 -> 0.468 against the camera JPEG).

    We compare against the per-channel floor measured off the sensor, and if
    that channel difference turns out to be imaginary, we add it back into
    the pixels beforehand so the subtraction ends up uniform. Bodies where
    it is a real offset are left alone - applying this correction to a
    normal body throws it off badly instead (measured: R6 Mark II error
    0.109 -> 0.785).
    """
    return _decide_black_level(raw)[0]


def _decide_black_level(raw) -> "tuple[int | None, list[int] | None, int | None]":
    """(user_black or None, pedestal added per colour or None, the value
    to fall back on when adding the pedestal fails). Adds the pedestal to
    this raw's pixels on the way."""
    try:
        black = list(raw.black_level_per_channel)
        sample = raw.raw_image_visible[::3, ::3]
    except Exception:  # noqa: BLE001
        return None, None, None
    if not black or sample.size == 0:
        return None, None, None

    floor = float(np.percentile(sample, 0.5))
    # Reported black far below the sensor floor = the pedestal was missed.
    # Anything else is a supported body, so we do nothing.
    if max(black) >= floor * 0.5:
        return None, None, None

    reported_spread = max(black) - min(black)
    if reported_spread <= 32:
        # Uniform channels mean we take LibRaw as having read it correctly.
        # A bright scene has no true black, so the sensor floor measures
        # high; without this condition we would wrongly crush a bright photo
        # from a camera whose black really is 0.
        return None, None, None

    measured = _channel_floors(raw)
    if measured is None:
        return int(floor), None, None
    measured_spread = max(measured) - min(measured)

    # A reported channel spread much larger than the measured one is
    # imaginary. The margin (32) is there to clear the measurement spread
    # that noise produces.
    if reported_spread <= measured_spread + 32:
        return int(floor), None, None  # real channel offset - fill the pedestal only

    low = min(black)
    extras = [int(b - low) for b in black]
    try:
        _add_pedestal(raw, extras)
    except Exception:  # noqa: BLE001 - a failure here must not stop develop
        return int(floor), None, None

    # We added (cblack[c] - low) to the pixels, so the global black has to
    # come down by the same amount for exactly floor to be subtracted from
    # every channel.
    return int(floor) - low, extras, int(floor)


def to_display(image: np.ndarray) -> np.ndarray:
    """Working-space float 0~255 to 8-bit sRGB for display (last step only).

    **The screen draws whatever it is given as sRGB.** The working space is
    wider than that, so the conversion has to happen here - skip it and
    everything looks desaturated.

    uint8 is already display-ready (embedded JPEG previews and thumbnails).
    The analysis path uses those values, so touching this moves scoring and
    the cache with it.

    **The input is either float 0~255 or uint8.** Pass uint16 (the result of
    a 16-bit export) and it clips at 255 and is ruined - that side is for
    saving rather than for display, and converts directly through
    icc.working_to (engine.export_image).
    """
    if image.dtype == np.uint8:
        return image
    from .develop.icc import WORKING_SPACE, working_to

    if WORKING_SPACE != "srgb":
        image = working_to(np.clip(image, 0.0, 255.0).astype(np.float32),
                           "srgb")
    return np.clip(image, 0.0, 255.0).astype(np.uint8)


def _flip_to_orientation(flip: int) -> int:
    """Convert LibRaw's flip value to an EXIF Orientation."""
    return {0: 1, 3: 3, 5: 8, 6: 6}.get(flip, 1)


def image_area(path: Path) -> tuple[int, int] | None:
    """The camera's own image area (width, height) in sensor orientation -
    the frame its JPEG covers - from LibRaw's crop rectangle. None when
    the file carries none. Opens the file without decoding it."""
    try:
        with rawpy.imread(str(path)) as raw:
            sizes = raw.sizes
    except Exception:  # noqa: BLE001 - not a RAW, or one LibRaw cannot open
        return None
    width = int(getattr(sizes, "crop_width", 0) or 0)
    height = int(getattr(sizes, "crop_height", 0) or 0)
    if width <= 0 or height <= 0:
        return None
    return width, height


# ---------------------------------------------------------------- metadata


def _ratio_to_float(tag) -> float | None:
    try:
        value = tag.values[0]
        return float(value.num) / float(value.den) if value.den else None
    except Exception:  # noqa: BLE001
        return None


def _element_to_float(value) -> float | None:
    """**One tag value** to float. `_ratio_to_float` takes a tag object.

    Confusing the two silently yields None - we actually did make the
    mistake of calling `_ratio_to_float` per element in the GPS parser, and
    since the code swallows exceptions, the location vanished entirely with
    no warning at all. It only came to light running a real Nikon Z9 file.

    EXIF's degrees/minutes/seconds arrive with integers and fractions mixed
    in the one array (e.g. `[44, 382467/10000, 0]`).
    """
    if value is None:
        return None
    numerator = getattr(value, "num", None)
    denominator = getattr(value, "den", None)
    if numerator is not None and denominator is not None:
        return float(numerator) / float(denominator) if denominator else None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_tag(tag) -> int | None:
    try:
        return int(tag.values[0])
    except Exception:  # noqa: BLE001
        return None


def _tags_from_preview(path: Path) -> dict:
    """Read EXIF out of the embedded preview JPEG.

    exifread cannot parse the original of a container that is not TIFF-based
    (ISO BMFF), such as CR3. Fortunately the preview JPEG carries the EXIF
    intact, so camera, ISO, shutter, aperture and focal length can all be
    salvaged.
    """
    try:
        with rawpy.imread(str(path)) as raw:
            thumb = raw.extract_thumb()
        if thumb.format != rawpy.ThumbFormat.JPEG:
            return {}
        return exifread.process_file(io.BytesIO(thumb.data), details=False)
    except Exception as exc:  # noqa: BLE001
        log.debug("프리뷰 EXIF 읽기 실패 %s: %s", path.name, exc)
        return {}


_LENS_TAGS = (
    "EXIF LensModel",        # standard (Sony, Canon, newer Nikon, Fujifilm...)
    "Image LensModel",       # sub-IFD read as a standalone TIFF (CR3's CMT2)
    "MakerNote LensModel",
    "MakerNote Lens",        # Pentax and Minolta put a readable name here
    "MakerNote LensType",    # Canon, Pentax - sometimes a numeric ID, so last
)

def _focal_35mm_from_tags(tags, focal: "float | None") -> "float | None":
    """Equivalent focal length. Order: (1) the standard tag, (2) working it
    back out of FocalPlane (Canon).

    Canon does not use FocalLengthIn35mmFilm at all (measured: 0 of 305
    frames). Instead we work the real sensor size back out of the FocalPlane
    resolution and the output pixel count, and convert by the diagonal ratio
    (43.27mm basis) - the same method as exiftool's ScaleFactor35efl, with a
    measured error of ±0.3%. If the derived value makes no physical sense
    (sensor width outside 2~60mm) the calculation is discarded.
    """
    tag = tags.get("EXIF FocalLengthIn35mmFilm")
    if tag:
        value = _int_tag(tag)
        if value:
            return float(value)
    if not focal:
        return None
    fp_x = tags.get("EXIF FocalPlaneXResolution")
    fp_y = tags.get("EXIF FocalPlaneYResolution")
    width = tags.get("EXIF ExifImageWidth")
    height = tags.get("EXIF ExifImageLength")
    if not (fp_x and fp_y and width and height):
        return None
    unit_tag = tags.get("EXIF FocalPlaneResolutionUnit")
    unit_mm = {2: 25.4, 3: 10.0, 4: 1.0}.get(
        _int_tag(unit_tag) if unit_tag else 2, 25.4)
    try:
        res_x = _ratio_to_float(fp_x)
        res_y = _ratio_to_float(fp_y)
        pixels_w = _int_tag(width)
        pixels_h = _int_tag(height)
        if not (res_x and res_y and pixels_w and pixels_h):
            return None
        sensor_w = pixels_w / res_x * unit_mm
        sensor_h = pixels_h / res_y * unit_mm
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if not (2.0 < sensor_w < 60.0 and 1.5 < sensor_h < 45.0):
        return None
    crop = (36.0 ** 2 + 24.0 ** 2) ** 0.5 / (sensor_w ** 2 + sensor_h ** 2) ** 0.5
    return round(focal * crop)


_LENS_PLACEHOLDERS = {"unknown", "n/a", "na", "----", "none", "manual lens"}


def _lens_from_tags(tags) -> str | None:
    """Sweep the various manufacturer spellings to find the lens name.

    Look only at the standard EXIF LensModel and the lens comes out entirely
    empty on bodies that write it only in the MakerNote (Pentax, Minolta and
    the like), so automatic optical correction does nothing.
    """
    for key in _LENS_TAGS:
        if key not in tags:
            continue
        value = str(tags[key]).strip()
        if not value or value.lower() in _LENS_PLACEHOLDERS:
            continue
        # LensType and friends sometimes come out as a numeric ID like
        # "61182". If it is not a name we cannot use it for a DB lookup, so
        # we skip it.
        if not any(ch.isalpha() for ch in value):
            continue
        return value
    return None


def read_metadata(path: Path) -> RawMetadata:
    """Read a RAW's EXIF. On failure it returns empty metadata, not an
    exception.

    The A6700 bursts at up to 11fps, so we have to read down to the subsecond
    to order a burst group correctly in time.
    """
    try:
        with path.open("rb") as fh:
            tags = exifread.process_file(fh, details=False)
    except Exception as exc:  # noqa: BLE001
        log.warning("EXIF 읽기 실패 %s: %s", path.name, exc)
        tags = {}

    if not tags:
        # CR3 is ISO BMFF rather than TIFF, so exifread fails outright. A
        # dedicated parser reads the CMT boxes inside moov/uuid (lens and
        # capture time included).
        from .cr3 import is_cr3, read_exif_tags

        if is_cr3(path):
            tags = read_exif_tags(path)

    if not tags and path.suffix.lower() in HEIF_EXTENSIONS:
        # HEIF is ISO BMFF too, so exifread cannot open it. The container
        # holds the EXIF whole (Exif\0\0 + TIFF), so we pull just that block
        # out and read it again. Without a capture time, scene grouping is
        # left relying on visual change alone.
        tags = _tags_from_heif(path)

    if not tags:
        # Failing even that, we use the preview JPEG's thin EXIF
        tags = _tags_from_preview(path)
    if not tags:
        return RawMetadata(path=path)

    capture_time = None
    # Preview EXIF sometimes lacks DateTimeOriginal, so we check the
    # alternatives alongside it
    dt_tag = (
        tags.get("EXIF DateTimeOriginal")
        or tags.get("Image DateTime")
        or tags.get("EXIF DateTimeDigitized")
    )
    if dt_tag:
        try:
            capture_time = datetime.strptime(str(dt_tag), "%Y:%m:%d %H:%M:%S")
            subsec = tags.get("EXIF SubSecTimeOriginal")
            if subsec:
                fraction = float(f"0.{str(subsec).strip()}")
                capture_time = capture_time.replace(microsecond=int(fraction * 1_000_000))
        except (ValueError, TypeError):
            capture_time = None

    shutter = tags.get("EXIF ExposureTime")
    aperture = tags.get("EXIF FNumber")
    focal = tags.get("EXIF FocalLength")
    iso = tags.get("EXIF ISOSpeedRatings")
    orientation = tags.get("Image Orientation")
    focal_value = _ratio_to_float(focal) if focal else None

    from .maker_meta import af_area_mode as _af_area_mode

    metadata = RawMetadata(
        path=path,
        capture_time=capture_time,
        camera_model=str(tags["Image Model"]).strip() if "Image Model" in tags else None,
        camera_make=str(tags["Image Make"]).strip() if "Image Make" in tags else None,
        lens_model=_lens_from_tags(tags),
        iso=_int_tag(iso) if iso else None,
        shutter_speed=_ratio_to_float(shutter) if shutter else None,
        aperture=_ratio_to_float(aperture) if aperture else None,
        focal_length=focal_value,
        focal_length_35mm=_focal_35mm_from_tags(tags, focal_value),
        af_area_mode=_af_area_mode(path),
        orientation=_int_tag(orientation) or 1 if orientation else 1,
        latitude=_gps_degrees(tags, "GPS GPSLatitude", "GPS GPSLatitudeRef"),
        longitude=_gps_degrees(tags, "GPS GPSLongitude", "GPS GPSLongitudeRef"),
    )

    if path.suffix.lower() == ".rw2" and (
            metadata.iso is None or not metadata.lens_model
            or metadata.focal_length_35mm is None):
        # RW2's TIFF magic is 85, so exifread rejects the whole file. The
        # values above came from the embedded preview EXIF fallback, and
        # that has no ISO or lens. They are in fact sitting there in plain
        # form in IFD0 0x0017 (ISO) and the embedded JPEG's MakerNote 0x0051
        # (lens), so we read just those two directly and fill them in
        # (RESEARCH_METADATA.md).
        from dataclasses import replace as _replace

        from .maker_meta import rw2_extras

        extras = rw2_extras(path)
        patch = {}
        if metadata.iso is None and "iso" in extras:
            patch["iso"] = extras["iso"]
        if not metadata.lens_model and "lens" in extras:
            patch["lens_model"] = extras["lens"]
        if metadata.focal_length_35mm is None and "focal_35mm" in extras:
            patch["focal_length_35mm"] = extras["focal_35mm"]
        if patch:
            metadata = _replace(metadata, **patch)

    return metadata


def _gps_degrees(tags: dict, value_key: str, ref_key: str) -> float | None:
    """EXIF's three degree/minute/second elements to signed decimal degrees.

    EXIF splits latitude into '35 degrees 41 minutes 12.3 seconds' + 'N'.
    In the southern and western hemispheres the ref is S/W while the value
    itself stays positive, so ignore the ref and you land on the opposite
    side of the planet.
    """
    value = tags.get(value_key)
    if value is None:
        return None
    parts = getattr(value, "values", None)
    if not parts or len(parts) < 3:
        return None

    numbers = [_element_to_float(p) for p in parts[:3]]
    if any(n is None for n in numbers):
        return None
    degrees, minutes, seconds = numbers

    decimal = float(degrees) + float(minutes) / 60.0 + float(seconds) / 3600.0
    ref = str(tags.get(ref_key, "")).strip().upper()
    if ref in ("S", "W"):
        decimal = -decimal
    if not (-180.0 <= decimal <= 180.0):
        return None
    return decimal
