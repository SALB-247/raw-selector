"""Calibrates the colour of a new camera model locally, against the
camera's embedded JPEG.

Background
----------
LibRaw carries colour information per model, and a body just out is not in
that table. Then the demosaic result comes out different from the picture
the camera made - measured, the EOS R6 Mark III had its black pedestal off
and came up yellow-green.

Rather than waiting for the library to be updated, the calibration is
derived here on this PC, using the JPEG the camera made itself as the
ground truth. It is the same scene developed separately by the camera and
by us, so the difference in channel balance between the two is exactly the
amount we missed.

What is derived
---------------
Three channel gains, and nothing else. This is about matching a reference
point rather than making the colour "pretty", so estimating a matrix with
many degrees of freedom overfits to the scene. The median over several
frames is used so it is not dragged by the colour cast of one scene.

Where it is stored
------------------
Only in this PC's data/calibration/. The measurements depend on the
individual unit, the firmware and the shooting conditions, so they are not
the sort of thing to carry over to anyone else.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Sequence

import cv2
import numpy as np

log = logging.getLogger(__name__)

#: At least this many have to be measured for the calibration to be
#: trusted. One or two frames mistake the colour cast of that scene for a
#: property of the camera.
MIN_SAMPLES = 4

#: Past this, accuracy barely rises and it only costs time.
MAX_SAMPLES = 12

#: A gain outside this range is taken as a bad measurement. A normal
#: difference between camera models is a few % to a few tens of %, and a
#: gap of more than 2x means the calculation or the sample is wrong.
GAIN_LIMIT = (0.5, 2.0)

#: If the channel ratios are off by less than this there is nothing to
#: calibrate. Making a value anyway only gets in the way at the next
#: library update.
NEGLIGIBLE = 0.02

_UNSAFE = re.compile(r'[<>:"/\\|?*\s]+')


def camera_key(make: str | None, model: str | None) -> str:
    """The storage key. Joins make and model and cleans it up so it can be
    used as a file name."""
    parts = [p.strip() for p in (make or "", model or "") if p and p.strip()]
    if not parts:
        return ""
    # Canon already puts the make in the model ("Canon EOS R6 Mark III")
    if len(parts) == 2 and parts[1].lower().startswith(parts[0].lower()):
        parts = [parts[1]]
    return _UNSAFE.sub("_", " ".join(parts)).strip("_")


@dataclass(frozen=True)
class CameraCalibration:
    """The channel gains of one camera model."""

    camera: str
    gain: tuple[float, float, float]  # B, G, R order (OpenCV channel order)
    samples: int = 0
    created: str = ""
    app_version: str = ""
    note: str = ""
    #: The key that becomes the stored file name. Built by
    #: `camera_key(make, model)`.
    #:
    #: It must not be rebuilt from the name (`camera`) each time. There was
    #: a period where reading built the key from make+model while writing
    #: used the model alone, and the only case where those two come out the
    #: same was Canon, which already has the make in the model. Sony,
    #: Nikon, Panasonic and Fuji were stored but never read back, so the
    #: calibration was quietly not applied and the calculation was
    #: suggested again every time the folder was opened.
    key: str = ""

    def storage_key(self) -> str:
        """The key for storing and deleting. Old files have no key, so it
        falls back to the name."""
        return self.key or _UNSAFE.sub("_", self.camera).strip("_")

    def is_neutral(self) -> bool:
        return all(abs(g - 1.0) < 1e-3 for g in self.gain)

    def to_dict(self) -> dict:
        return {
            "camera": self.camera,
            "gain": list(self.gain),
            "samples": self.samples,
            "created": self.created,
            "app_version": self.app_version,
            "note": self.note,
            "key": self.key,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CameraCalibration | None":
        try:
            gain = tuple(float(v) for v in data["gain"])
            if len(gain) != 3:
                return None
            low, high = GAIN_LIMIT
            if not all(low <= g <= high for g in gain):
                log.warning("보정값이 허용 범위를 벗어나 무시합니다: %s", gain)
                return None
            return cls(
                camera=str(data.get("camera", "")),
                gain=gain,  # type: ignore[arg-type]
                samples=int(data.get("samples", 0)),
                created=str(data.get("created", "")),
                app_version=str(data.get("app_version", "")),
                note=str(data.get("note", "")),
                key=str(data.get("key", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None


def calibration_dir() -> Path:
    """The folder calibrations are stored in. Kept inside this PC only."""
    from ..appinfo import data_dir

    return data_dir() / "calibration"


def _path_for(key: str) -> Path:
    return calibration_dir() / f"{key}.json"


def load(key: str) -> CameraCalibration | None:
    """The stored calibration. None if it is missing or damaged."""
    if not key:
        return None
    path = _path_for(key)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Catch only JSONDecodeError and a UnicodeDecodeError leaks out of
        # a file that is not UTF-8. Both sit under ValueError, so they are
        # caught in one go. The place that calls this function directly
        # (gui/calibration_dialog.py) does not wrap exceptions.
        return None
    return CameraCalibration.from_dict(data)


def save(calibration: CameraCalibration) -> Path | None:
    """Stores the calibration and returns the path.

    The key used is `calibration.key` - it has to be the same value as when
    reading (`load`).
    """
    key = calibration.storage_key()
    if not key:
        return None
    path = _path_for(key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(calibration.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("보정값을 저장하지 못했습니다: %s", exc)
        return None
    return path


def remove(key: str) -> bool:
    """Deletes the calibration. It becomes unnecessary once the library is
    updated."""
    try:
        _path_for(key).unlink()
        return True
    except OSError:
        return False


def stored_cameras() -> list[CameraCalibration]:
    """Every stored calibration."""
    folder = calibration_dir()
    if not folder.is_dir():
        return []
    result = []
    for path in sorted(folder.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # one damaged file must not block the whole list
        item = CameraCalibration.from_dict(data)
        if item is not None:
            result.append(item)
    return result


# ---------------------------------------------------------------- detection


def looks_unsupported(path: Path) -> bool:
    """Whether LibRaw appears not to fully know this camera model.

    Missing the black pedestal means it is not in the model table, and then
    the colour information is likely missing along with it. Calibration is
    only suggested for models this signal fires on - asking every time even
    for well-supported models is nothing but a nuisance.
    """
    import rawpy

    from ..raw_io import _repair_black_level

    try:
        with rawpy.imread(str(path)) as raw:
            return _repair_black_level(raw) is not None
    except Exception:  # noqa: BLE001
        return False


@dataclass
class CalibrationNeed:
    """A camera model that appears to need calibration, and its samples."""

    camera: str
    key: str
    samples: list[Path] = field(default_factory=list)


def find_uncalibrated(
    paths: Iterable[Path], limit: int = MAX_SAMPLES, force: bool = False
) -> CalibrationNeed | None:
    """Finds a camera model in the folder that needs calibration.

    Suggesting it automatically (force=False) has three conditions: there
    is no stored calibration, LibRaw support looks incomplete, and there
    are enough samples. If even one of them fails it says nothing at all -
    asking every time even for well-supported models is nothing but a
    nuisance.

    force=True is when the user asked for it directly. It measures again
    even for a model the library knows and a model that already has a
    calibration. It is a path left open for when the library's default
    colour is not to the user's liking and they want this PC's measurements
    to take precedence.
    """
    from ..raw_io import read_metadata

    by_camera: dict[str, CalibrationNeed] = {}
    checked_support: dict[str, bool] = {}

    for path in paths:
        try:
            metadata = read_metadata(path)
        except Exception:  # noqa: BLE001
            continue
        key = camera_key(metadata.camera_make, metadata.camera_model)
        if not key:
            continue
        if not force:
            if load(key) is not None:
                continue
            if key not in checked_support:
                checked_support[key] = looks_unsupported(path)
            if not checked_support[key]:
                continue

        need = by_camera.setdefault(
            key, CalibrationNeed(camera=metadata.camera_model or key, key=key)
        )
        if len(need.samples) >= limit:
            continue
        # A file with no embedded preview has no ground truth and cannot
        # be measured. Without filtering here, it fails with "not enough
        # samples" only after the calculation has started.
        if has_embedded_preview(path):
            need.samples.append(path)

    for need in by_camera.values():
        if len(need.samples) >= MIN_SAMPLES:
            return need
    return None


# ---------------------------------------------------------------- measurement


NEUTRAL_SATURATION = 0.18
"""Below this saturation it is taken as 'neutral to begin with'."""

MIN_NEUTRAL_PIXELS = 200
"""Fewer neutral pixels than this cannot be trusted - it falls back to the
whole-image mean."""


def _neutral_means(
    camera_bgr: np.ndarray, ours_bgr: np.ndarray
) -> tuple[np.ndarray, np.ndarray] | None:
    """Picks only the pixels close to neutral and takes the channel means
    of both sides.

    Measure with the whole-image mean and the subject's colour is mixed
    straight in. Fill the frame with red clothing and it learns "this body
    is red", and learns the opposite on the next scene. So the value shakes
    from frame to frame, and that shaking is exactly "the colour differs
    from frame to frame".

    Look only at the places that **should have no colour to begin with** -
    a grey wall, a white shirt, concrete - and the remaining difference is
    not the subject but the difference between the body and our develop.

    Measured (R6M3, 10 frames):
    - Frame-to-frame shake 0.0122 -> 0.0071 (41.6% down, R 47% down,
      B 61% down, G about the same)
    - **Verified on photos not used to fit** (derived from 5 frames and
      applied to the other 5, 60 splits): remaining colour difference
      0.0428 -> 0.0355 (17.1% down)

    The second one is what matters. Derive on the same photos and measure
    on the same photos and the whole-image mean method always wins -
    because the value was derived to fit that metric. The real use is
    'apply to a photo it has not seen', so that is what has to be measured.

    The reference is taken from the camera JPEG - picking from the ground
    truth side keeps the bias in our own result out of the selection.
    """
    hsv = cv2.cvtColor(camera_bgr, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1].astype(np.float32) / 255.0
    value = hsv[:, :, 2].astype(np.float32) / 255.0
    # Places that are too dark or blown out cannot be trusted for their
    # channel ratios
    mask = (saturation < NEUTRAL_SATURATION) & (value > 0.15) & (value < 0.92)
    if int(np.count_nonzero(mask)) < MIN_NEUTRAL_PIXELS:
        return None

    camera_mean = camera_bgr[mask].astype(np.float64).mean(axis=0)
    ours_mean = ours_bgr[mask].astype(np.float64).mean(axis=0)
    return camera_mean, ours_mean


def _channel_means(image_bgr: np.ndarray) -> np.ndarray:
    """The channel means. Extreme pixels are left out of the measurement.

    Saturated highlights and crushed shadows have their channels clipped
    together, so they carry no balance information. Put them in as they
    are and the brighter the scene, the closer the gain gets to 1.
    """
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    usable = (gray > 20) & (gray < 235)
    if np.count_nonzero(usable) < gray.size // 20:
        usable = np.ones_like(gray, dtype=bool)
    return np.array(
        [float(image_bgr[:, :, c][usable].mean()) for c in range(3)], dtype=np.float64
    )


def embedded_preview(path: Path) -> np.ndarray | None:
    """The embedded preview the camera made (BGR). This is the ground
    truth.

    There are two formats:
      - JPEG   : most models. Comes as a byte string.
      - BITMAP : an uncompressed RGB array. Some models and converters put
                 it in this way.

    There are also files with none at all (a DNG whose converter stripped
    the preview, and so on). In that case this file cannot be used for
    calibration, so None is returned and the caller moves on to another
    file.
    """
    import rawpy

    try:
        with rawpy.imread(str(path)) as raw:
            thumb = raw.extract_thumb()
            if thumb.format == rawpy.ThumbFormat.JPEG:
                return cv2.imdecode(
                    np.frombuffer(thumb.data, np.uint8), cv2.IMREAD_COLOR
                )
            # BITMAP is an already-decoded RGB array
            array = np.asarray(thumb.data)
            if array.ndim != 3 or array.shape[2] < 3:
                return None
            return cv2.cvtColor(array[:, :, :3], cv2.COLOR_RGB2BGR)
    except Exception:  # noqa: BLE001 - missing or damaged files are skipped
        return None


def has_embedded_preview(path: Path) -> bool:
    """Whether calibration can be measured from this file (does it have a
    preview)."""
    return embedded_preview(path) is not None


def sample_gain(path: Path) -> np.ndarray | None:
    """Derives the channel gains from one frame. None on failure.

    The camera JPEG and our develop result are reduced to the same size and
    their channel means compared. Brightness itself cannot be matched
    because the camera's tone curve is mixed into it, so it is divided by
    the overall brightness and **only the balance** is looked at.
    """
    from ..raw_io import load_demosaiced, to_display

    camera = embedded_preview(path)
    if camera is None:
        return None

    try:
        # The profile is deliberate colour styling, so it is left out and
        # only the plain develop is compared. calibration=False matters -
        # derive a calibration again from a result that already has the
        # stored calibration applied and it feeds back on itself and the
        # value keeps drifting.
        ours = load_demosaiced(
            path, half_size=True, apply_profile=False, calibration=False
        )
    except Exception:  # noqa: BLE001
        return None
    # The camera JPEG is sRGB. Our value is in the working space (a wide
    # gamut), so comparing them as they are mixes the two spaces - neutrals
    # are the same in both spaces so the gain itself barely moves, but the
    # fallback for a scene with no neutrals (the whole-image mean) is off
    # wholesale.
    ours = to_display(ours)

    size = (320, 213)
    camera_small = cv2.resize(camera, size, interpolation=cv2.INTER_AREA)
    ours_small = cv2.resize(ours, size, interpolation=cv2.INTER_AREA)

    pair = _neutral_means(camera_small, ours_small)
    if pair is None:
        # A scene with almost no neutrals (single-colour lighting, a frame
        # filled with a primary) falls back to the whole-image mean. It is
        # less accurate, but better than producing no value at all.
        camera_mean = _channel_means(camera_small)
        ours_mean = _channel_means(ours_small)
    else:
        camera_mean, ours_mean = pair
    if np.any(ours_mean <= 1.0) or np.any(camera_mean <= 1.0):
        return None

    # Compare only the balance, with brightness taken out
    camera_ratio = camera_mean / camera_mean.mean()
    ours_ratio = ours_mean / ours_mean.mean()
    gain = camera_ratio / ours_ratio

    low, high = GAIN_LIMIT
    if not np.all((gain >= low) & (gain <= high)):
        return None
    return gain


def measure(
    paths: Sequence[Path],
    camera: str,
    progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    app_version: str = "",
    key: str = "",
) -> CameraCalibration | None:
    """Derives this model's channel gains from several frames.

    The gain is measured per frame and the **median** is used. A mean is
    dragged by even one odd frame, whereas a median holds up as long as
    half are sound - a backlit or single-colour scene may be mixed in.

    It takes time (1~2 seconds per frame). Callbacks are provided so the
    caller can show progress and accept a cancellation.
    """
    selected = list(paths)[:MAX_SAMPLES]
    total = len(selected)
    gains: list[np.ndarray] = []

    for index, path in enumerate(selected, start=1):
        if should_cancel and should_cancel():
            log.info("보정 측정 취소 (%d/%d)", index - 1, total)
            return None
        gain = sample_gain(path)
        if gain is not None:
            gains.append(gain)
        if progress:
            progress(index, total)

    if len(gains) < MIN_SAMPLES:
        log.info("보정에 쓸 표본이 부족합니다 (%d/%d)", len(gains), MIN_SAMPLES)
        return None

    median = np.median(np.stack(gains), axis=0)
    # Normalised so the product of the gains is 1 - brightness is left
    # alone and only the balance changes.
    median = median / float(np.exp(np.mean(np.log(median))))

    drift = float(np.max(np.abs(median - 1.0)))
    if drift < NEGLIGIBLE:
        log.info("이 기종은 보정이 필요 없습니다 (최대 편차 %.3f)", drift)
        return CameraCalibration(
            camera=camera, gain=(1.0, 1.0, 1.0), samples=len(gains),
            created=datetime.now().isoformat(timespec="seconds"),
            app_version=app_version,
            note="편차가 작아 보정하지 않습니다",
            key=key,
        )

    return CameraCalibration(
        camera=camera,
        gain=(float(median[0]), float(median[1]), float(median[2])),
        samples=len(gains),
        created=datetime.now().isoformat(timespec="seconds"),
        app_version=app_version,
        note=f"내장 JPEG {len(gains)}장 기준",
        key=key,
    )


def apply(image_bgr: np.ndarray, calibration: CameraCalibration | None) -> np.ndarray:
    """Multiplies by the calibration gains. Returns the input as it is if
    there is none or it is neutral."""
    if calibration is None or calibration.is_neutral():
        return image_bgr
    gain = np.array(calibration.gain, dtype=np.float32)
    return image_bgr.astype(np.float32) * gain
