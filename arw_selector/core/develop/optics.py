"""Optical correction - lens distortion, vignetting, chromatic aberration.

It works down two paths.

1. **Automatic**: look the camera and lens up in the lensfun database and
   apply the profile. Accurate, but a lens the DB does not carry cannot be
   used. Measured: Sony's own E PZ 16-50mm matched, but the Tamron A069
   (50-300mm) was not in the DB.
2. **Manual**: adjust distortion, vignetting and chromatic aberration by
   hand. Used for a lens the DB does not carry, or when the automatic
   result is not to your liking.

Manual correction has to work even without lensfunpy - it is kept an
optional dependency.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from ..raw_io import RawMetadata

log = logging.getLogger(__name__)

try:
    import lensfunpy

    LENSFUN_AVAILABLE = True
except ImportError:  # pragma: no cover - branches on whether it is installed
    lensfunpy = None
    LENSFUN_AVAILABLE = False


# OpticsSettings lives in settings.py and nowhere else. There used to be a
# copy of the same name here as well, and the two drifted apart as only the
# settings.py side kept growing (lens_override, defringe_green, hue
# selection and so on). Import it from this module by mistake and you get a
# different class with fields missing, so save/load quietly goes wrong.


@dataclass(frozen=True)
class LensMatch:
    """The result of a lens DB lookup. The UI has to show what was matched."""

    camera: str | None = None
    lens: str | None = None
    found: bool = False
    reason: str = ""

    @property
    def summary(self) -> str:
        if self.found:
            return f"{self.lens}"
        return self.reason or "프로필 없음"


def user_lens_db_dir() -> "Path":
    """The folder the user drops extra lens profiles (.xml) into.

    The bundled DB is a snapshot taken at the lensfunpy release, so recent
    lenses are missing from it (measured: the Tamron A069 is not
    registered). So that coverage can be widened without rebuilding the
    app, the XML in this folder is read on top of the bundled DB. Drop in
    profiles from the official lensfun repository, or ones you made
    yourself, as they are.
    """
    from pathlib import Path as _Path

    from ..presets import user_config_dir

    return _Path(user_config_dir()) / "lensfun"


V1_CACHE_DIR = ".v1cache"
"""Where version 2 XML is converted to version 1 (under the user folder)."""


def _prepare_user_xmls(user_dir: "Path") -> list[str]:
    """Prepare the XML in the user folder in a form the library can read.

    The latest DB in the lensfun repository is format version 2, while the
    installed library only reads up to 1. So that the user can drop the
    file they downloaded in as it is, a converted copy is made when it is
    version 2 and that is what gets handed over. The original is left
    untouched.

    lensfunpy's paths takes a **list of files**, not a folder (give it a
    folder and it fails with Permission denied).
    """
    from .lensfun_db import convert_to_v1, needs_conversion

    cache = user_dir / V1_CACHE_DIR
    prepared: list[str] = []
    for source in sorted(user_dir.glob("*.xml")):
        try:
            text = source.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        if not needs_conversion(text):
            prepared.append(str(source))
            continue

        target = cache / source.name
        try:
            if (
                not target.exists()
                or target.stat().st_mtime < source.stat().st_mtime
            ):
                cache.mkdir(parents=True, exist_ok=True)
                target.write_text(convert_to_v1(text), encoding="utf-8")
            prepared.append(str(target))
        except OSError as exc:
            log.warning("렌즈 DB 변환 실패 (%s): %s", source.name, exc)
    return prepared


@lru_cache(maxsize=1)
def _database():
    """The lensfun DB is expensive to load, so it is built only once.

    If there is XML in the user folder it is read along with it. Even with
    that folder broken, this has to keep working on the bundled DB alone.
    """
    if not LENSFUN_AVAILABLE:
        return None

    extra: list[str] = []
    try:
        user_dir = user_lens_db_dir()
        if user_dir.is_dir():
            extra = _prepare_user_xmls(user_dir)
    except OSError as exc:
        log.debug("사용자 렌즈 DB 폴더 확인 실패: %s", exc)

    if extra:
        try:
            db = lensfunpy.Database(paths=extra)
            log.info("사용자 렌즈 프로필 %d개를 함께 읽었습니다: %s", len(extra), user_dir)
            return db
        except Exception as exc:  # noqa: BLE001
            log.warning("사용자 렌즈 DB를 읽지 못해 번들만 씁니다: %s", exc)

    try:
        return lensfunpy.Database()
    except Exception as exc:  # noqa: BLE001
        log.warning("lensfun DB 로딩 실패: %s", exc)
        return None


def reload_database() -> tuple[int, int]:
    """Re-read the lens DB. Returns the new (body count, lens count).

    The DB is expensive to load, so it is read once and cached. That means
    profile XML dropped in while the app is running does not take effect -
    this lets the user re-read it themselves.
    """
    _database.cache_clear()
    return database_coverage()


def ensure_user_lens_db_dir() -> "Path":
    """Create the user lens profile folder and return its path.

    With no folder there is no way to know where to put anything. It is
    created before we open it up to show.
    """
    folder = user_lens_db_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        readme = folder / "읽어보세요.txt"
        if not readme.exists():
            readme.write_text(
                "여기에 lensfun 렌즈 프로필 XML을 넣으면 함께 인식됩니다.\n"
                "번들 DB에 없는 렌즈(신형·서드파티)를 추가할 때 씁니다.\n\n"
                "받는 곳: https://github.com/lensfun/lensfun (data/db)\n"
                "넣은 뒤 광학 섹션의 '렌즈 DB 다시 읽기'를 누르면 바로 반영됩니다.\n",
                encoding="utf-8",
            )
    except OSError as exc:
        log.debug("렌즈 프로필 폴더를 만들지 못했습니다: %s", exc)
    return folder


def database_coverage() -> tuple[int, int]:
    """(body count, lens count). The final coverage, user folder added in."""
    db = _database()
    if db is None:
        return (0, 0)
    return (len(db.cameras), len(db.lenses))


_APERTURE = re.compile(r"\bF(\d)", re.IGNORECASE)
_MODEL_CODE = re.compile(r"\s+[A-Z]\d{3,4}\b")  # e.g. Tamron A069, Sigma C013

_GLUED_MOUNT = re.compile(
    r"^(RF|EF-S|EF|FE|E|Z|XF|XC|DT|DA|FA)(?=\d)", re.IGNORECASE
)
"""A mount marking glued straight onto the focal length (RF100-500mm,
XF18-55mm ...)."""

_PENTAX_PREFIX = re.compile(r"^(smc|hd)\s+pentax-?[a-z*]*\s+", re.IGNORECASE)
"""Pentax prefixes such as smc PENTAX-DA / HD PENTAX-D FA*."""


def _lens_name_variants(name: str) -> list[str]:
    """Expand an EXIF lens name into candidates in lensfun's notation.

    Every maker writes EXIF differently:
      "E 50-300mm F4.5-6.3 A069"  (Sony/Tamron EXIF)
      "50-300mm f/4.5-6.3"        (lensfun notation)
    If it is not found in one go, the name is loosened a little at a time
    and tried again.
    """
    variants = [name]

    # F4.5 -> f/4.5 (lensfun uses the slash notation)
    slashed = _APERTURE.sub(r"f/\1", name)
    if slashed != name:
        variants.append(slashed)

    # Try stripping the maker's model code on the end (A069 and the like)
    for candidate in list(variants):
        stripped = _MODEL_CODE.sub("", candidate).strip()
        if stripped and stripped != candidate:
            variants.append(stripped)

    # Each maker prefixes something different. lensfun mostly drops it.
    #   Sony      "FE 70-200mm F2.8 GM OSS II" / "E 18-135mm ..." / "DT ..."
    #   Canon     "RF100-500mm ..." / "EF24-70mm ..."
    #   Nikon     "NIKKOR Z 24-70mm f/2.8 S" / "AF-S NIKKOR ..."
    #   Fujifilm  "XF18-55mmF2.8-4 R LM OIS" / "XC ..."
    #   Olympus   "OLYMPUS M.12-40mm F2.8" / "M.Zuiko Digital ..."
    #   Panasonic "LUMIX G VARIO 12-60/F3.5-5.6"
    #   Pentax    "smc PENTAX-DA 18-55mm ..." / "HD PENTAX-DA ..."
    for candidate in list(variants):
        parts = candidate.split()
        if len(parts) > 1 and parts[0].upper() in {
            "E", "FE", "RF", "EF", "EF-S", "Z", "DT", "SEL", "XF", "XC",
            "NIKKOR", "OLYMPUS", "LUMIX", "SMC", "HD", "DA", "FA",
        }:
            variants.append(" ".join(parts[1:]))

    # When the mount marking is glued to the focal length ("RF100-500mm",
    # "XF18-55mm"). Splitting on whitespace cannot strip it, so we cut in
    # front of the digits.
    for candidate in list(variants):
        stripped = _GLUED_MOUNT.sub("", candidate).strip()
        if stripped and stripped != candidate:
            variants.append(stripped)

    # Pentax attaches a sub-classifier: "smc PENTAX-DA", "HD PENTAX-D FA*".
    for candidate in list(variants):
        stripped = _PENTAX_PREFIX.sub("", candidate).strip()
        if stripped and stripped != candidate:
            variants.append(stripped)

    # Two-word prefixes are tried as well (AF-S NIKKOR, LUMIX G,
    # M.Zuiko Digital ...)
    for candidate in list(variants):
        lowered = candidate.lower()
        for prefix in (
            "af-s nikkor ", "af-p nikkor ", "nikkor z ", "lumix g vario ",
            "lumix g ", "m.zuiko digital ed ", "m.zuiko digital ",
            "olympus m.", "smc pentax-", "hd pentax-", "samyang af ",
        ):
            if lowered.startswith(prefix):
                variants.append(candidate[len(prefix):].strip())
                break

    seen, unique = set(), []
    for candidate in variants:
        key = candidate.lower()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


_FOCAL = re.compile(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*mm|(\d+(?:\.\d+)?)\s*mm",
                    re.IGNORECASE)


def _focal_range_from_name(name: str) -> tuple[float, float] | None:
    """Pull the focal range out of a lens name. "50-300mm" -> (50, 300)."""
    match = _FOCAL.search(name)
    if not match:
        return None
    if match.group(1) and match.group(2):
        low, high = float(match.group(1)), float(match.group(2))
    else:
        low = high = float(match.group(3))
    return (min(low, high), max(low, high))


def _focal_matches(lens, wanted: tuple[float, float] | None) -> bool:
    """Whether the candidate lens's focal range overlaps the real lens.

    lensfun's loose_search is extremely generous: it hands back some lens
    or other even for a completely different name (measured: a
    made-up "nonexistent lens 999mm" -> "E 24mm F2.8"). Used as it is, a
    wrong distortion/vignetting profile gets applied to the photo. That is
    worse than doing no correction at all. The focal length gives us a
    minimal check.
    """
    if wanted is None:
        return True
    try:
        low, high = float(lens.min_focal), float(lens.max_focal)
    except (AttributeError, TypeError, ValueError):
        return True  # with no information, we do not block it
    if low <= 0 or high <= 0:
        return True
    # Letting it through on the ranges merely 'overlapping' is not
    # allowed. With the name 24-105, a 100-500 lens gets through on the
    # grounds that it overlaps over 100~105. If it is the same lens, both
    # ends have to be close.
    #
    # The tolerance is 10%. Left at 20% it was too loose at the telephoto
    # end, and once the DB grew an 800mm lens caught a 999mm request
    # (199 < 999*0.2). Real notation rounds at about the 1% level, so 10%
    # is enough.
    return (
        abs(low - wanted[0]) <= max(2.0, wanted[0] * 0.1)
        and abs(high - wanted[1]) <= max(2.0, wanted[1] * 0.1)
    )


# Putting a \b in front misses notation glued straight onto a digit, as in
# "E-M1MarkIII" (there is no word boundary between '1' and 'M').
_MARK = re.compile(r"mark\s*([ivx]+)\b", re.IGNORECASE)
_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6"}


def _camera_name_variants(model: str, make: str | None = None) -> list[str]:
    """Expand an EXIF body name into candidates in lensfun's notation.

    The Model field of a camera's EXIF does not carry the maker ("EOS R6
    Mark II"). lensfun, on the other hand, attaches the maker and writes
    it short ("Canon EOS R6m2"). We used to hand the first word of the
    model name over as the maker, which looked up maker="EOS" and meant
    Canon bodies were not matched at all.
    """
    variants = [model]

    # "Mark II" -> "m2" (lensfun notation)
    def _to_m(match: "re.Match[str]") -> str:
        return "m" + _ROMAN.get(match.group(1).lower(), match.group(1))

    shortened = _MARK.sub(_to_m, model)
    shortened = re.sub(r"\s+(m\d)\b", r"\1", shortened)  # "R6 m2" -> "R6m2"
    if shortened != model:
        variants.append(shortened)

    # The form with the maker prefixed is tried as well. If EXIF Make is
    # there we use it, otherwise we guess from the shape of the model name
    # (the per-maker prefixes are fairly distinctive).
    guessed = None
    upper = model.upper()
    if upper.startswith("EOS") or upper.startswith("POWERSHOT"):
        guessed = "Canon"
    elif upper.startswith(("ILCE", "DSC", "SLT", "NEX")):
        guessed = "Sony"
    elif upper.startswith(("Z ", "D", "COOLPIX")):
        guessed = "Nikon"
    elif upper.startswith(("X-", "GFX", "FINEPIX")):
        guessed = "Fujifilm"
    elif upper.startswith(("E-M", "OM-", "PEN-")):
        guessed = "Olympus"
    elif upper.startswith(("DC-", "DMC-")):
        guessed = "Panasonic"
    elif upper.startswith("K-"):
        guessed = "Pentax"

    for maker in (make, guessed):
        if not maker:
            continue
        maker = maker.strip().title()
        for candidate in list(variants):
            if not candidate.lower().startswith(maker.lower()):
                variants.append(f"{maker} {candidate}")

    seen, unique = set(), []
    for candidate in variants:
        key = candidate.lower()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _find_cameras_loose(db, camera_model: str, make: str | None = None):
    """Try the body-name notation variants in turn. maker is not passed.

    EXIF Model does not carry the maker, so handing the first word over as
    the maker makes the search fail instead. lensfun's loose_search finds
    it well enough without a maker.
    """
    for candidate in _camera_name_variants(camera_model, make):
        try:
            found = db.find_cameras(None, candidate, loose_search=True)
        except Exception:  # noqa: BLE001
            continue
        if found:
            return found
    return []


def _is_generic_placeholder(lens) -> bool:
    """Whether this is one of lensfun's generic stand-in lenses.

    When the name does not match at all, lensfun hands back a generic
    entry such as "Rectilinear 10-1000mm f/1.0". It is a placeholder with
    no measured correction values, so reporting it as 'found' leaves the
    user believing a lens profile was applied.
    """
    model = (getattr(lens, "model", "") or "").lower()
    if "rectilinear" in model:
        return True
    try:
        low, high = float(lens.min_focal), float(lens.max_focal)
    except (AttributeError, TypeError, ValueError):
        return False
    # A real zoom is at most around 20x however wide it goes (18-300mm ~ 16x)
    return low > 0 and high / low > 25.0


def _covers_focal(lens, focal: float | None) -> bool:
    """Whether this lens can actually shoot at that focal length.

    The capture focal length in EXIF is firmer evidence than guessing from
    the name. Attach a 24-105mm profile to a photo shot at 363mm and the
    distortion correction goes in completely wrong.
    """
    if not focal or focal <= 0:
        return True
    try:
        low, high = float(lens.min_focal), float(lens.max_focal)
    except (AttributeError, TypeError, ValueError):
        return True
    if low <= 0 or high <= 0:
        return True
    return low * 0.9 <= focal <= high * 1.1


def _find_lenses_loose(db, camera, lens_model: str, focal: float | None = None):
    """Find the lens by trying notation variants in turn. [] if not found.

    It filters on both the focal range pulled out of the name and the real
    capture focal length (EXIF) - better to fall through to manual
    correction than to apply the wrong profile.
    """
    wanted = _focal_range_from_name(lens_model)
    for candidate in _lens_name_variants(lens_model):
        try:
            found = db.find_lenses(camera, None, candidate, loose_search=True)
        except Exception:  # noqa: BLE001
            continue
        verified = [
            lens for lens in found
            if _focal_matches(lens, wanted)
            and _covers_focal(lens, focal)
            and not _is_generic_placeholder(lens)
        ]
        if verified:
            return verified
    return []


def find_lens(metadata: RawMetadata | None) -> LensMatch:
    """Look the camera and lens up from EXIF."""
    if not LENSFUN_AVAILABLE:
        return LensMatch(reason="lensfunpy 미설치")
    if metadata is None or not metadata.camera_model:
        return LensMatch(reason="카메라 정보 없음")

    db = _database()
    if db is None:
        return LensMatch(reason="렌즈 DB를 열 수 없음")

    try:
        cameras = _find_cameras_loose(db, metadata.camera_model, metadata.camera_make)
        if not cameras:
            return LensMatch(reason=f"DB에 {metadata.camera_model} 없음")

        camera = cameras[0]
        if not metadata.lens_model:
            return LensMatch(
                camera=camera.model, reason="EXIF에 렌즈 정보 없음"
            )

        lenses = _find_lenses_loose(
            db, camera, metadata.lens_model, metadata.focal_length
        )
        if not lenses:
            return LensMatch(
                camera=camera.model,
                # No absolute path is baked into the wording. It differs
                # per PC, and a development machine's path showing through
                # reads like somebody else's path. The folder is opened by
                # the 'lens profile folder' button just below.
                reason=(
                    f"DB에 {metadata.lens_model} 없음 — 수동 보정을 쓰거나, "
                    "'렌즈 프로필 폴더' 버튼을 눌러 XML을 넣으십시오"
                ),
            )

        return LensMatch(
            camera=camera.model, lens=lenses[0].model, found=True
        )
    except Exception as exc:  # noqa: BLE001
        return LensMatch(reason=f"조회 실패: {exc}")


def available_lenses(
    maker: str | None = None, keyword: str | None = None, limit: int = 0
) -> list[str]:
    """The list of lenses registered in the database.

    When the EXIF lens name is empty or differs from the DB name, the user
    has to be able to pick one themselves. It happens often with
    third-party lenses or adapters.

    maker is **a sort priority, not a filter condition**. We used to
    filter a Sony body with maker='Sony', which made third-party lenses
    such as Tamron and Sigma disappear from the list wholesale - even
    though mounting a third-party lens is far more common, there was no
    way to pick one. limit defaulted to 200 as well, so only the front of
    the 1304 entries came out (Tamron was cut off). The default now shows
    all of them, and limit 0 means unlimited.
    """
    db = _database()
    if db is None:
        return []

    names: set[str] = set()
    for lens in db.lenses:
        label = f"{lens.maker} {lens.model}".strip()
        if keyword and keyword.lower() not in label.lower():
            continue
        names.add(label)

    def sort_key(label: str) -> tuple[int, str]:
        # Lift the same maker to the top, but leave the rest visible
        same_maker = bool(maker) and label.lower().startswith(maker.lower())
        return (0 if same_maker else 1, label.lower())

    ordered = sorted(names, key=sort_key)
    return ordered[:limit] if limit and limit > 0 else ordered


def available_cameras(keyword: str | None = None, limit: int = 200) -> list[str]:
    """The list of cameras registered in the database."""
    db = _database()
    if db is None:
        return []

    names: list[str] = []
    for camera in db.cameras:
        label = f"{camera.maker} {camera.model}".strip()
        if keyword and keyword.lower() not in label.lower():
            continue
        names.append(label)
        if len(names) >= limit:
            break
    return sorted(set(names))


def find_lens_by_name(camera_model: str, lens_name: str) -> LensMatch:
    """Find the lens by the name the user picked themselves."""
    if not LENSFUN_AVAILABLE:
        return LensMatch(reason="lensfunpy가 설치되어 있지 않습니다")

    db = _database()
    if db is None:
        return LensMatch(reason="렌즈 데이터베이스를 열 수 없습니다")

    try:
        cameras = _find_cameras_loose(db, camera_model)
        if not cameras:
            return LensMatch(reason=f"데이터베이스에 {camera_model}이(가) 없습니다")

        lenses = _find_lenses_loose(db, cameras[0], lens_name)
        if not lenses:
            return LensMatch(
                camera=cameras[0].model,
                reason=f"데이터베이스에 {lens_name}이(가) 없습니다",
            )
        return LensMatch(
            camera=cameras[0].model, lens=lenses[0].model, found=True
        )
    except Exception as exc:  # noqa: BLE001
        return LensMatch(reason=f"조회 실패: {exc}")


Region = "tuple[int, int, int, int]"
"""(left, top, right, bottom) pixel bounds of a piece of the frame."""

GAIN_MAP_STEP = 8
"""The vignetting gain is read from lensfun on a frame this many times
smaller and interpolated. Falloff is a low-order polynomial in the
radius, so a bilinear read between points 8px apart is off by well under
a thousandth; reading it per pixel meant handing lensfun a float copy of
the whole frame (0.6GB at 50MP) even when only a corner was wanted."""


def _piece(image: np.ndarray, region: "Region | None") -> np.ndarray:
    if region is None:
        return image
    left, top, right, bottom = region
    if (left, top, right, bottom) == (0, 0, image.shape[1], image.shape[0]):
        return image                     # the whole frame: no 600MB copy
    return np.ascontiguousarray(image[top:bottom, left:right])


def _vignetting_gain(lens, crop_factor: float, width: int, height: int,
                     focal: float, aperture: float,
                     box: "Region") -> "np.ndarray | None":
    """The vignetting gain over `box`, (h, w) float32, from a
    GAIN_MAP_STEP-times smaller modifier of the same frame. None when the
    profile has no vignetting for this setting or produces a value that is
    not a number."""
    small_w = max(2, -(-width // GAIN_MAP_STEP))
    small_h = max(2, -(-height // GAIN_MAP_STEP))
    small = lensfunpy.Modifier(lens, crop_factor, small_w, small_h)
    small.initialize(focal, aperture, 10.0, pixel_format=np.float32)
    ones = np.ones((small_h, small_w, 3), np.float32)
    if not small.apply_color_modification(ones):
        return None
    gain = np.ascontiguousarray(ones[:, :, 1])
    if not np.all(np.isfinite(gain)):
        return None
    left, top, right, bottom = box
    # The box's pixels in the small map's pixel coordinates. lensfun
    # normalises a frame by its **last pixel index**, n - 1, not by n:
    # pixel 0 of the small map is pixel 0 of the frame and its last pixel
    # is the frame's last, so the two grids line up with a plain scale of
    # (n_small - 1) / (n - 1). Read as "pixel centres over n" the map was
    # stretched by about GAIN_MAP_STEP - 1 pixels at the corner - on the
    # steepest part of the falloff, 5% of gain on a 1200px preview.
    xs = np.arange(left, right, dtype=np.float32) \
        * np.float32((small_w - 1) / max(1, width - 1))
    ys = np.arange(top, bottom, dtype=np.float32) \
        * np.float32((small_h - 1) / max(1, height - 1))
    map_x, map_y = np.meshgrid(xs, ys)
    # cubic, not linear: the gain curves upward towards the corner and a
    # linear read between points 8px apart missed it by 0.25% there
    return cv2.remap(gain, map_x, map_y, cv2.INTER_CUBIC,
                     borderMode=cv2.BORDER_REPLICATE)


def apply_auto_correction(
    image: np.ndarray, metadata: RawMetadata | None, settings: OpticsSettings,
    region: "Region | None" = None,
) -> np.ndarray:
    """Correct distortion and vignetting with the lensfun profile.

    With no profile it returns the original as it is - the failure passes
    quietly and manual correction carries on working after it.

    `region` asks for one piece of the frame: `image` is still the whole
    frame (every correction here is relative to the frame's centre and
    size), but only that piece comes back, and only the pixels it draws
    from are touched. The zoomed Full Render used to correct the whole
    50MP frame and then cut the piece out - 13 seconds on every pan.
    """
    if not settings.auto_enabled or not LENSFUN_AVAILABLE or metadata is None:
        return _piece(image, region)

    db = _database()
    if db is None or not metadata.camera_model:
        return _piece(image, region)
    if not (settings.lens_override or metadata.lens_model):
        return _piece(image, region)

    try:
        cameras = _find_cameras_loose(db, metadata.camera_model, metadata.camera_make)
        if not cameras:
            return _piece(image, region)
        camera = cameras[0]

        # A lens the user picked themselves takes priority over EXIF
        lens_name = settings.lens_override or metadata.lens_model
        lenses = _find_lenses_loose(db, camera, lens_name)
        if not lenses:
            return _piece(image, region)

        height, width = image.shape[:2]
        modifier = lensfunpy.Modifier(
            lenses[0], camera.crop_factor, width, height
        )
        # pixel_format has to be matched. Leave the declaration out and
        # lensfun applies a 0~255-based computation to 0~1 values, so the
        # result runs away. float32 is taken as 0~1 - the vignetting
        # correction below hands over linear light, so it has to be this
        # format.
        focal = metadata.focal_length or 50.0
        aperture = metadata.aperture or 5.6
        modifier.initialize(
            focal, aperture,
            10.0,            # subject distance (m) - EXIF lacks it, so typical
            pixel_format=np.float32,
        )

        left, top, right, bottom = region or (0, 0, width, height)
        piece_w, piece_h = right - left, bottom - top

        # Geometry first, as coordinates only: where each pixel of the
        # piece draws from. Chromatic aberration and distortion combined
        # are one lensfun call and one resampling per channel - they used
        # to be two resamplings in a row (linear, then Lanczos), which is
        # neither sharper nor what lensfun itself does.
        coords = None
        interpolation = cv2.INTER_LANCZOS4
        # the whole frame is asked for without arguments - lensfun's
        # sub-rectangle evaluation rounds a shade differently (its own
        # float32 accumulation: ~0.3px on even frame sizes, up to ~0.6px
        # on odd ones and tiny windows, measured), and the whole frame
        # should stay bit for bit what it was
        window = () if region is None else (left, top, piece_w, piece_h)
        if settings.auto_chromatic and settings.auto_distortion:
            coords = modifier.apply_subpixel_geometry_distortion(*window)
        elif settings.auto_chromatic:
            coords = modifier.apply_subpixel_distortion(*window)
            interpolation = cv2.INTER_LINEAR
        elif settings.auto_distortion:
            coords = modifier.apply_geometry_distortion(*window)

        # The source pixels the piece draws from: the piece itself, or
        # the box the coordinates reach into (plus the Lanczos support).
        box = (left, top, right, bottom)
        if coords is not None:
            xy = coords.reshape(-1, 2)
            margin = 4
            box = (max(0, int(np.floor(xy[:, 0].min())) - margin),
                   max(0, int(np.floor(xy[:, 1].min())) - margin),
                   min(width, int(np.ceil(xy[:, 0].max())) + margin + 1),
                   min(height, int(np.ceil(xy[:, 1].max())) + margin + 1))
        result = _piece(image, box)

        if settings.auto_vignetting:
            # **It has to be applied to the amount of light.** Vignetting
            # is the lens having cut light away, so the multiplier that
            # undoes it has to multiply the amount of light too. But
            # lensfun multiplies the value it was handed as it is -
            # measured, the multiplier was constant regardless of
            # brightness (fitting a single constant left a residual of
            # 0.49 levels = the uint8 rounding limit), which is to say it
            # does not know the gamma.
            #
            # Multiply into a gamma'd 0~255 and the effective light
            # multiplier becomes g^2.2. Over 58 DB samples the corner
            # over-correction had a median of +1.50 stops, and in the
            # synthetic verification **after correction was further off
            # than before correction** (flatness error 20.9 -> 31.1).
            #
            # The curve we undo with is engine.to_light, not sRGB. The
            # picture arriving here has already been through
            # postprocess (BT.709), the body correction and the profile
            # curve. Undo with sRGB and the corner residual runs
            # -0.36 ~ +0.21 stops depending on the centre level, so **even
            # the sign flips** - and it goes wrong on precisely the
            # brightness gradient vignetting is trying to flatten
            # (flatness 1.57 levels, maximum 3.44). Undone with to_light
            # it is 0.00.
            #
            # Why the old verification missed this: it laid the vignette
            # on in sRGB and undid it in sRGB. A round trip that applies
            # and undoes with the same curve comes out 0 even when that
            # curve is wrong. It only shows up if you lay it on in light.
            #
            # Automatic correction is switched off by apply_settings on an
            # editable image, so the picture arriving here is always
            # profiled - no branch is needed.
            #
            # We used to hand it over as uint8, which threw away the float
            # precision demosaicing gave us along with it. The input dtype
            # is preserved on the way back.
            from .engine import from_light, to_light

            # The gain is applied where the light was lost - at the source
            # pixels, before they are moved - so the box is corrected and
            # the resampling below reads corrected pixels, exactly as the
            # whole-frame correction did.
            gain = _vignetting_gain(lenses[0], camera.crop_factor, width,
                                    height, focal, aperture, box)
            if gain is not None:
                light = to_light(result)
                light *= gain[:, :, None]
                result = np.clip(from_light(light), 0, 255).astype(image.dtype)
            else:
                # If the profile and the shooting conditions disagree,
                # abnormal values can still come out. Used as they are
                # the pixels turn to rubbish, so we check and throw away.
                log.warning(
                    "비네팅 프로필이 비정상 값을 냈다 — 건너뛴다 (%s)",
                    lenses[0].model,
                )

        if coords is not None:
            origin = np.array([box[0], box[1]], dtype=np.float32)
            if coords.ndim == 4:
                # lensfun gives (h, w, 3, 2) - per-channel (x, y) coords
                channels = list(cv2.split(result))
                for index in range(3):
                    channels[index] = cv2.remap(
                        channels[index],
                        np.ascontiguousarray(coords[:, :, index, :]) - origin,
                        None, interpolation, borderMode=cv2.BORDER_REPLICATE,
                    )
                result = cv2.merge(channels)
            else:
                result = cv2.remap(
                    result, np.ascontiguousarray(coords) - origin, None,
                    interpolation, borderMode=cv2.BORDER_REPLICATE,
                )
        elif box != (left, top, right, bottom):
            result = _piece(image, (left, top, right, bottom))

        return result
    except Exception as exc:  # noqa: BLE001 - a failure must not block develop
        log.warning("자동 렌즈 보정 실패: %s", exc)
        return _piece(image, region)


def apply_manual_distortion(image: np.ndarray, amount: int) -> np.ndarray:
    """Manual distortion correction, using a simplified radial model.

    Negative flattens barrel distortion (convex), positive flattens
    pincushion (concave).
    """
    if not amount:
        return image

    height, width = image.shape[:2]
    k = amount / 100.0 * 0.35

    # In normalised coordinates, r' = r * (1 + k*r^2)
    center_x, center_y = width / 2.0, height / 2.0
    scale = max(center_x, center_y)

    y, x = np.indices((height, width), dtype=np.float32)
    nx = (x - center_x) / scale
    ny = (y - center_y) / scale
    r2 = nx * nx + ny * ny
    factor = 1.0 + k * r2

    map_x = (nx * factor * scale + center_x).astype(np.float32)
    map_y = (ny * factor * scale + center_y).astype(np.float32)

    return cv2.remap(
        image, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
    )


#: How much the manual vignetting slider at 100 applies to the four
#: corners of the image (in stops).
#:
#: Manual is the stand-in for lenses lensfun has no profile for (the
#: Tamron A069 and the like), so **by hand it has to reach as far as
#: automatic reaches.** Measured, what automatic applies to the corners
#: of the E PZ 16-50mm is +2.14 stops, so this was set a little above it.
MANUAL_VIGNETTE_MAX_STOPS = 2.2


def apply_manual_vignetting(image: np.ndarray, amount: int,
                            profiled: bool = True) -> np.ndarray:
    """Manual vignetting correction. Positive brightens the periphery to
    offset the darkening.

    **It is applied to the amount of light.** This fixes the same physical
    phenomenon as automatic does, so it has to be applied in the same
    space. We used to multiply straight into 0~255, which meant the same
    slider value meant something different at every brightness - slider 25
    was +0.43 stops at level 40 and +1.16 stops at level 190 (a spread of
    0.74). It split even within a single frame, so matching by eye on a
    dark corner blew out a bright one. On a real photo at slider 50, 1.68%
    of the pixels were blown (0.08% in the original); applied in light it
    is 0.13%.

    **The slider is linear in stops, not in the multiplier.** With the old
    formula (1 + k*r²) the front half did 60% of the whole effect, which
    made fine adjustment hard. Now 50 is exactly half of 100, and the
    scale reads as "raise the corners by this many stops".

    The falloff is r² - seen in stops, the cos^4 drop is roughly that
    shape.
    """
    if not amount:
        return image

    height, width = image.shape[:2]
    y, x = np.indices((height, width), dtype=np.float32)
    center_x, center_y = width / 2.0, height / 2.0
    radius = np.sqrt(
        ((x - center_x) / center_x) ** 2 + ((y - center_y) / center_y) ** 2
    )
    # At the corners r²=2, so dividing by 2 makes that point line up with
    # the slider scale.
    stops = ((amount / 100.0) * MANUAL_VIGNETTE_MAX_STOPS
             * np.clip(radius, 0.0, 1.5) ** 2 / 2.0)
    gain = np.exp2(stops)

    from .engine import from_light, to_light

    # The input dtype is preserved. Optical correction is at the very
    # front of the pipeline, so dropping to uint8 here makes every later
    # tone and curve compute on top of 256 steps, which bands somewhere
    # smooth like a sky - the 14-bit precision demosaicing handed over as
    # float would be lost over a single vignetting slider.
    lit = to_light(image, profiled) * gain[:, :, None]
    return np.clip(from_light(lit, profiled), 0, 255).astype(image.dtype)


def sample_hue(image: np.ndarray, x: int, y: int, radius: int = 4) -> int:
    """Get the representative hue around a given point (the eyedropper).

    Fringe colour differs per lens and per scene, so a fixed value does
    not match well. Sampling the real fringe and taking that hue as the
    reference is more accurate.

    The value returned is the same 8-bit HSV hue (0~179) apply_defringe
    uses. The preview the screen hands over is a demosaic result, so it is
    float, and converting float straight to HSV has OpenCV hand hue back
    as 0~359 and saturation as 0~1. Then the eyedropper produces a value
    nothing like the real one (purple 145 -> 110) and fringe removal
    catches the wrong colour. We match it to 8 bits before computing.
    """
    height, width = image.shape[:2]
    x0, x1 = max(0, x - radius), min(width, x + radius + 1)
    y0, y1 = max(0, y - radius), min(height, y + radius + 1)
    if x1 <= x0 or y1 <= y0:
        return 0

    region = image[y0:y1, x0:x1]
    if region.dtype != np.uint8:
        region = np.clip(region, 0, 255).astype(np.uint8)
    patch = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    hue = patch[:, :, 0].astype(np.float32)
    saturation = patch[:, :, 1].astype(np.float32)

    # Hue is unstable on low-saturation pixels, so their weight is reduced
    weights = saturation + 1.0
    # Hue is circular, so a vector mean has to be used, not a plain mean
    angles = hue * 2.0 * np.pi / 180.0
    x_mean = float(np.sum(np.cos(angles) * weights))
    y_mean = float(np.sum(np.sin(angles) * weights))
    return int(round(np.degrees(np.arctan2(y_mean, x_mean)) / 2.0)) % 180


def apply_defringe(
    image: np.ndarray,
    purple: int,
    green: int,
    purple_hue: int = 145,
    green_hue: int = 65,
) -> np.ndarray:
    """Remove the purple/green fringing chromatic aberration leaves.

    Only the pixels carrying that hue on a high-contrast edge are picked
    out and desaturated. The range is narrowed to near the edge so that
    the real subject colour is not touched as well.
    """
    if not purple and not green:
        return image

    # **The computation is in float32 HSV.** We used to round-trip through
    # uint8, which dropped the gradation to 8 bits over this stretch alone
    # (measured: 228 unique levels after the pass).
    #
    # An older comment warned that "handing float over as it is turns the
    # photo black and white", but that was a **range convention** problem.
    # float32 HSV is H 0~360, S 0~1, and V in the input range as it is
    # (measured and confirmed). Write values computed on the 8-bit scale
    # (S 0~255, H 0~179) on top of that and the saturation is wiped out
    # wholesale. Match the scale and it is accurate; the 8-bit scale
    # constants (the hue the eyedropper gives) are converted to degrees
    # here.
    from .engine import HUE_UINT8_TO_DEGREES

    source = np.clip(image, 0, 255).astype(np.float32)

    hsv = cv2.cvtColor(source, cv2.COLOR_BGR2HSV)
    hue, saturation = hsv[:, :, 0], hsv[:, :, 1]

    # Edge mask - fringing only appears where the contrast is large
    gray = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY)
    edges = cv2.dilate(
        cv2.Laplacian(gray, cv2.CV_32F).__abs__(), np.ones((3, 3), np.uint8)
    )
    edge_mask = np.clip(edges / max(1.0, edges.max()) * 4.0, 0.0, 1.0)

    for amount, center, width in (
        (purple, purple_hue * HUE_UINT8_TO_DEGREES, 20 * HUE_UINT8_TO_DEGREES),
        (green, green_hue * HUE_UINT8_TO_DEGREES, 18 * HUE_UINT8_TO_DEGREES),
    ):
        if not amount:
            continue
        distance = np.abs(hue - center)
        distance = np.minimum(distance, 360.0 - distance)
        band = np.exp(-(distance ** 2) / (2 * width * width))
        saturation *= 1.0 - (amount / 100.0) * band * edge_mask

    hsv[:, :, 1] = np.clip(saturation, 0.0, 1.0)
    result = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    if image.dtype == np.float32:
        return result
    return np.clip(result, 0, 255).astype(image.dtype)


def apply_optics(
    image: np.ndarray, settings: OpticsSettings,
    metadata: RawMetadata | None = None, profiled: bool = True,
    region: "Region | None" = None,
) -> np.ndarray:
    """All of optical correction, applied automatic profile -> manual.

    profiled is the space this picture sits in (see
    engine._baseline_transfer). It is needed because vignetting has to
    multiply into light. Automatic is switched off by apply_settings on an
    editable image, but **manual is not locked**, so it applies to a JPEG
    original as well - and there the curve to undo with is different.
    """
    if settings.is_neutral():
        return _piece(image, region)

    if region is not None and (settings.distortion or settings.manual_vignetting):
        # The manual corrections work from the frame's centre and know
        # nothing of a piece; correct the whole frame and cut afterwards.
        return _piece(apply_optics(image, settings, metadata, profiled), region)

    result = apply_auto_correction(image, metadata, settings, region)
    result = apply_manual_distortion(result, settings.distortion)
    result = apply_manual_vignetting(result, settings.manual_vignetting,
                                     profiled)
    result = apply_defringe(
        result,
        settings.defringe_purple,
        settings.defringe_green,
        settings.defringe_purple_hue,
        settings.defringe_green_hue,
    )
    return result
