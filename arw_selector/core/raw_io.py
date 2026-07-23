"""RAW 파일 입출력.

4000장 배치를 실용적인 시간 안에 처리하려면 풀 디모자이크는 쓸 수 없습니다
(장당 1~2초). 대신 RAW에 내장된 full-size JPEG 프리뷰를 꺼내 씁니다.
A6700은 약 6000x4000 프리뷰를 넣어주므로 초점 판정에는 충분합니다.
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
    ".cr2", ".cr3", ".crw",           # Canon (CR3는 LibRaw 0.20 이상)
    ".nef", ".nrw",                   # Nikon
    ".raf",                           # Fujifilm
    ".orf",                           # Olympus / OM System
    ".rw2",                           # Panasonic
    ".pef", ".ptx",                   # Pentax
    ".dng",                           # Adobe 범용
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
    ".raw",                           # Panasonic/Leica 구형 및 범용
}
"""지원 확장자입니다.

LibRaw 0.22.1이 다루는 포맷들입니다. A6700(ARW)이 주 대상이지만 같은
셀렉트 흐름이 다른 기종에도 그대로 적용됩니다.

실제 파일로 확인한 것은 ARW(ILCE-6700)와 CR3(EOS R6 Mark II)입니다.
나머지는 LibRaw 지원 목록에 근거하며, 열리지 않는 파일은 분석 단계에서
오류로 기록되고 배치는 계속 진행됩니다.

`.raw`는 제조사마다 다르게 쓰는 범용 확장자라 RAW가 아닌 파일이 걸릴 수
있습니다. 그 경우 LibRaw가 열지 못하고 해당 장만 실패로 남습니다.

확장자 비교는 항상 lower()로 합니다. 카메라마다 대소문자가 제각각이고
(.ARW/.arw, .NEF/.nef), 파일시스템이 구분하는지도 제각각이기 때문입니다 —
맥의 기본 APFS는 대소문자를 **구분하지 않고**, 리눅스의 ext4는 구분하며,
맥도 포맷할 때 구분하도록 고를 수 있습니다.
"""

JPEG_EXTENSIONS = {".jpg", ".jpeg"}
"""바로 열 수 있는 압축 이미지. cv2로 디코드됩니다 — 실측 확인."""

HEIF_EXTENSIONS = {".hif", ".heic", ".heif"}
"""HEIF 계열. 소니는 .HIF, 애플은 .HEIC로 씁니다.

둘 다 ISO-BMFF 컨테이너(ftyp heix 등)라 cv2·PIL·rawpy 어느 것도 못 엽니다.
`pillow-heif`(libheif)가 필수 의존인 이유입니다 — 실측으로 확인:

    DSC02290.HIF (ftyp heix, 8.7MB) → 6192×4128, 얼굴 6개
    같은 장면 ARW와 선명도 61.6 대 60.8

libheif는 LGPL-3입니다. 배포 조건은 THIRD_PARTY.md 를 보십시오.
"""

EDITABLE_IMAGE_EXTENSIONS = JPEG_EXTENSIONS | HEIF_EXTENSIONS
"""RAW가 없을 때 대신 판정·보정할 수 있는 형식.

JPEG만 찍는 사람들이 있습니다. 그런 파일도 셀렉트와 보정을 할 수 있어야
합니다 — 다만 latitude가 다릅니다. 센서 데이터가 아니라 이미 현상되어
8비트로 눌린 결과라, 날아간 하이라이트는 돌아오지 않고 큰 노출·색온도
조정에서 계조가 끊깁니다. `is_editable_image()`로 구분해 화면에 알립니다.
"""

RAW_FILE_FILTER = (
    "RAW 파일 (" + " ".join(f"*{e}" for e in sorted(RAW_EXTENSIONS)) + ")"
    ";;이미지 (" + " ".join(f"*{e}" for e in sorted(EDITABLE_IMAGE_EXTENSIONS)) + ")"
)
"""파일 대화상자용 필터 문자열."""

SIDECAR_EXTENSIONS = {".jpg", ".jpeg", ".xmp", ".arw.xmp"}
"""RAW와 짝지어 함께 옮겨야 하는 파일들."""


def is_raw(path: Path) -> bool:
    return path.suffix.lower() in RAW_EXTENSIONS


def is_editable_image(path: Path) -> bool:
    """RAW가 아니지만 직접 판정·보정할 수 있는 파일인지."""
    return path.suffix.lower() in EDITABLE_IMAGE_EXTENSIONS


@dataclass(frozen=True)
class RawMetadata:
    """RAW EXIF에서 뽑아낸, 그룹핑과 진단에 필요한 최소 정보."""

    path: Path
    capture_time: datetime | None = None
    camera_model: str | None = None
    camera_make: str | None = None
    """제조사(EXIF Make). 렌즈 DB 조회에서 바디 이름을 만들 때 씁니다.

    카메라 EXIF의 Model에는 제조사가 안 들어갑니다("EOS R6 Mark II"). 반면
    lensfun은 제조사를 붙여 씁니다("Canon EOS R6m2").
    """

    lens_model: str | None = None
    iso: int | None = None
    shutter_speed: float | None = None  # 초 단위
    aperture: float | None = None
    focal_length: float | None = None
    orientation: int = 1  # EXIF Orientation (1~8)

    latitude: float | None = None
    longitude: float | None = None
    """촬영 위치 (도 단위, 남/서는 음수). 없으면 None.

    바디에 GPS가 없어도 폰과 연동해 찍으면 들어옵니다. 실측(A6700 300장):
    **한 장도 없었습니다** — 연동 없이 찍으면 아예 기록되지 않습니다.

    이 값은 **읽기만** 합니다. 내보내는 파일에는 어떤 경우에도 쓰지 않습니다
    (develop/metadata.py 참고) — 위치 정보는 실수로 흘러나갔을 때 가장
    위험한 항목입니다.
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
    """프리뷰를 어떤 경로로도 얻지 못했을 때."""


def iter_raw_files(folder: Path, recursive: bool = True) -> list[Path]:
    """폴더에서 판정할 파일을 찾아 정렬된 리스트로 반환합니다.

    RAW와, **RAW가 없는 자리의** JPEG·HEIF를 함께 돌려줍니다.

    같은 이름의 RAW와 JPEG이 나란히 있으면(카메라의 RAW+JPEG 기록) RAW만
    씁니다. 둘 다 넣으면 같은 사진이 두 번 나와 장수와 keep 비율이 전부
    두 배로 어긋납니다. RAW 쪽이 판정에도 보정에도 낫습니다.

    export가 만든 `_keep` / `_review` / `_reject` 폴더는 재스캔 시
    원본을 중복 처리하게 되므로 제외합니다.

    캐시 폴더(`.raw_selector_cache`)도 같은 이유로 제외합니다. 그 안의
    `thumbs/*.jpg`는 이 함수가 JPEG도 돌려주기 시작한 뒤로 사진으로
    잡혔습니다 — 한 번 분석한 폴더를 다시 열면 썸네일 수만큼 장수가
    부풀고 장면 묶기와 keep 비율이 통째로 어긋납니다.
    """
    from .appinfo import CACHE_DIR_NAME, LEGACY_CACHE_DIR_NAMES
    from .types import OUTPUT_DIR_NAMES  # types가 raw_io를 쓰므로 지연 import

    skip_dirs = OUTPUT_DIR_NAMES | {CACHE_DIR_NAME, *LEGACY_CACHE_DIR_NAMES}
    pattern = "**/*" if recursive else "*"
    raws: list[Path] = []
    others: list[Path] = []
    for path in folder.glob(pattern):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(folder).parts[:-1]
        if any(part in skip_dirs for part in relative_parts):
            continue
        if is_raw(path):
            raws.append(path)
        elif is_editable_image(path):
            others.append(path)

    # 짝은 **같은 폴더의 같은 이름**으로만 봅니다. 이름만 같고 다른 폴더에
    # 있는 파일은 다른 촬영일 수 있습니다.
    raw_keys = {(p.parent, p.stem.lower()) for p in raws}
    unpaired = [p for p in others if (p.parent, p.stem.lower()) not in raw_keys]
    return sorted(raws + unpaired)


# ---------------------------------------------------------------- 방향 보정


def apply_orientation(image: np.ndarray, orientation: int) -> np.ndarray:
    """EXIF Orientation(1~8)을 이미지에 적용합니다."""
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
    """JPEG 바이트에서 EXIF Orientation만 읽습니다.

    PIL의 open()은 지연 로딩이라 픽셀 디코딩 없이 EXIF만 읽고 끝납니다.
    """
    try:
        with Image.open(io.BytesIO(data)) as im:
            return int(im.getexif().get(0x0112, 1))
    except Exception:  # noqa: BLE001 - 손상된 EXIF는 방향 미적용으로 넘깁니다
        return 1


def resize_long_edge(image: np.ndarray, target: int) -> np.ndarray:
    """긴 변이 target이 되도록 축소합니다. 이미 작으면 그대로 반환."""
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
    """cv2.imwrite의 유니코드 안전 대체.

    Windows에서 cv2.imwrite는 한글·비ASCII 경로에 쓰면 조용히 실패합니다
    (False 반환, 파일 안 생김). 카카오톡 받은 파일 폴더처럼 한글 경로가
    흔해서, 메모리에서 인코딩한 뒤 파이썬 open으로 씁니다.
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
    """cv2.imread의 유니코드 안전 대체. 실패하면 None."""
    try:
        data = np.frombuffer(Path(path).read_bytes(), dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, flags)


# ---------------------------------------------------------------- 비RAW 디코드


def _decode_heif(path: Path) -> np.ndarray | None:
    """HEIF(.HIF/.HEIC)를 BGR로 디코드합니다. 못 읽으면 None.

    `pillow-heif`가 담고 있는 libheif가 실제로 푸는 부분입니다. 실측:
    DSC02290.HIF(ftyp heix, 8.7MB) → 6192×4128, 얼굴 6개로 짝 ARW와 동일.

    라이브러리가 없는 경우까지 여기서 감쌉니다. 필수 의존이라 정상적으로는
    없을 수 없지만, 없을 때 ImportError로 앱이 통째로 죽는 것보다 그 파일만
    실패하는 편이 낫습니다. 다만 '파일이 깨졌다'와는 구분해서 말해야
    합니다 — 배포본에서 빠진 경우 파일을 아무리 바꿔도 안 열립니다.
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
    except Exception as exc:  # noqa: BLE001 - 어떤 실패든 한 장만 실패시킵니다
        log.debug("HEIF 디코드 실패 %s: %s", path.name, exc)
        return None
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _tags_from_heif(path: Path) -> dict:
    """HEIF 컨테이너 안의 EXIF 블록을 exifread로 읽습니다.

    블록은 `Exif\\0\\0` 6바이트 뒤에 평범한 TIFF가 이어지는 형태입니다.
    그 앞머리를 떼고 넘기면 RAW와 똑같은 경로로 파싱됩니다.
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
    """JPEG·HEIF를 방향 보정된 BGR로 읽습니다.

    RAW가 아니라 **이미 현상된 결과**입니다. 되돌릴 수 없는 것들이 있습니다:
    날아간 하이라이트는 데이터가 없어 살아나지 않고, 8비트라 큰 노출·색온도
    조정에서 계조가 끊깁니다. 그래도 셀렉트와 가벼운 보정에는 충분합니다.
    """
    suffix = path.suffix.lower()
    if suffix in HEIF_EXTENSIONS:
        image = _decode_heif(path)
        if image is None:
            raise PreviewError(f"HEIF를 열지 못했습니다: {path.name}")
    else:
        image = imread_unicode(path, cv2.IMREAD_COLOR)
        if image is None:
            raise PreviewError(f"이미지를 열지 못했습니다: {path.name}")

    # EXIF 방향은 JPEG에 흔합니다. 무시하면 세로 컷이 눕습니다.
    try:
        image = apply_orientation(image, _jpeg_orientation(path.read_bytes()))
    except OSError:
        pass
    return image


# ---------------------------------------------------------------- 프리뷰 추출


def load_preview(path: Path, max_long_edge: int | None = None) -> np.ndarray:
    """프리뷰를 방향 보정된 BGR 이미지로 반환합니다.

    RAW라면:
      1) 내장 JPEG 프리뷰 (가장 빠름, 정상 경로)
      2) 내장 비트맵 썸네일
      3) 최후의 수단으로 half-size 디모자이크 — 느리므로 경고를 남깁니다

    RAW가 아니면(JPEG·HEIF) 파일 자체가 프리뷰입니다.
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
                image = cv2.imdecode(
                    np.frombuffer(thumb.data, dtype=np.uint8), cv2.IMREAD_COLOR
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
    except PreviewError:
        raise
    except Exception as exc:  # noqa: BLE001 - 손상 파일을 배치 전체 실패로 만들지 않습니다
        raise PreviewError(f"{path.name}: {exc}") from exc

    if max_long_edge:
        image = resize_long_edge(image, max_long_edge)
    return image


@dataclass(frozen=True)
class WhiteBalance:
    """RAW의 화이트밸런스 정보.

    camera는 as-shot 배수, daylight는 카메라의 주광 보정 배수입니다. 둘을
    함께 알면 임의의 목표 색온도에 대한 카메라 배수를 그 카메라의 보정을
    기준으로 계산할 수 있습니다.
    """

    camera: tuple[float, ...]
    daylight: tuple[float, ...]
    as_shot_kelvin: int

    @property
    def engine_wb(self) -> tuple[tuple, tuple]:
        """엔진 _apply_white_balance에 넘길 (camera, daylight) 튜플."""
        return (self.camera, self.daylight)


def read_white_balance(path: Path) -> "WhiteBalance | None":
    """RAW에서 화이트밸런스 배수를 읽고 as-shot 색온도를 추정합니다.

    디모자이크 없이 메타데이터만 읽으므로 빠릅니다. 실패하면 None을
    돌려주어 호출부가 색온도 보정 없이 진행할 수 있게 합니다.
    """
    try:
        with rawpy.imread(str(path)) as raw:
            camera = tuple(float(x) for x in raw.camera_whitebalance)
            daylight = tuple(float(x) for x in raw.daylight_whitebalance)
    except Exception:  # noqa: BLE001 - WB를 못 읽어도 보정 자체는 진행합니다
        return _white_balance_without_libraw(path)

    if len(camera) < 3 or len(daylight) < 3 or daylight[1] == 0:
        return _white_balance_without_libraw(path)
    return WhiteBalance(camera, daylight, _estimate_as_shot_kelvin(camera, daylight))


NIKON_DAYLIGHT_FALLBACK = (1.9578, 0.945, 1.1413)
"""LibRaw이 니콘 Z 계열에 쓰는 daylight 배수.

daylight 값은 파일이 아니라 디코더 내부 상수라, LibRaw이 파일을 아예 못
열면 가져올 데가 없습니다. 색온도 추정에는 기준선이 필요하므로 이 값을
씁니다 — 없으면 색온도 슬라이더 자체가 동작하지 않습니다.
"""


def _white_balance_without_libraw(path: Path) -> "WhiteBalance | None":
    """LibRaw이 못 여는 파일에서 메타데이터만으로 WB를 건집니다.

    니콘 고효율(HE/HE*) 압축 NEF가 여기 해당합니다. 화소는 못 풀어도
    MakerNote는 평범한 TIFF라 읽힙니다. 이게 없으면 보정 창에서 색온도
    조절이 통째로 죽습니다.

    LibRaw이 여는 니콘 파일로 대조했을 때 camera_whitebalance와 값이
    일치했습니다.
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
    """as-shot 배수가 어느 색온도의 카메라 배수와 가장 가까운지 찾습니다.

    카메라의 daylight 보정을 기준으로 삼기 때문에 흑체색 직접 비교보다
    안정적입니다 (직접 비교는 포화된 R 채널에 휘둘립니다).
    """
    from .develop.engine import NEUTRAL_KELVIN, _kelvin_to_rgb

    day = np.array(daylight[:3], dtype=np.float64)
    cam = np.array(camera[:3], dtype=np.float64)
    if cam[1] == 0:
        return NEUTRAL_KELVIN  # 카메라 G 배수가 0이면 정규화 불가 — 중립으로
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
) -> np.ndarray:
    """RAW를 실제로 디모자이크해 방향 보정된 BGR 이미지로 반환합니다.

    내장 JPEG 프리뷰가 아니라 센서 데이터를 직접 현상하므로 느리지만
    (24MP 기준 1~2초) 색·계조·디테일이 정확합니다. 보정 화면과 내보내기의
    베이스라인입니다.

    target_kelvin이 주어지면 그 절대 색온도로 화이트밸런스를 맞춰
    디모자이크합니다. 카메라의 daylight 보정을 기준으로 배수를 계산하므로
    프리뷰의 근사와 달리 실제 색온도 변환입니다. 없으면 as-shot(카메라 WB).

    apply_profile이 True면 기본 카메라 프로파일(표준)을 얹어 자연스러운
    출발점을 만듭니다. 중립 디모자이크는 평탄해서 그대로 쓰면 밋밋합니다.

    calibration은 이 PC에서 잰 기종 보정입니다. None이면 파일의 기종으로
    찾아 적용하고, False를 주면 보정 없이 순수 현상만 합니다(보정값을
    측정할 때 자기 자신을 되먹이지 않으려면 필요합니다).

    **RAW가 아니면(JPEG·HEIF) 디모자이크할 것이 없습니다.** 파일을 그대로
    float BGR로 올려 보정 파이프라인의 출발점으로 씁니다. 색온도·프로파일·
    기종 보정은 센서 데이터가 있어야 성립하므로 적용하지 않습니다 — 이미
    카메라가 한 번 적용해 구워 넣은 결과이기 때문입니다.
    """
    if is_editable_image(path):
        image = load_image_file(path).astype(np.float32)
        if half_size:
            image = cv2.resize(image, (0, 0), fx=0.5, fy=0.5,
                               interpolation=cv2.INTER_AREA)
        return image

    with rawpy.imread(str(path)) as raw:
        # 14비트 센서를 8비트로 바로 떨구면 계조가 뭉갭니다. 16비트로 받아
        # float 0~255로 정규화해 정밀도를 유지합니다 (파일 비트뎁스 무관).
        params = dict(no_auto_bright=True, output_bps=16, half_size=half_size)

        # LibRaw이 모르는 최신 기종은 블랙 페데스탈을 못 잡아(예: EOS R6
        # Mark III는 [0,38,113,78]로 읽힘) 페데스탈이 안 빠져 전체가 뜨고
        # 채널별 오프셋 차이로 마젠타가 낍니다. 센서 데이터에서 직접 추정해
        # 바로잡습니다. 지원 기종은 건드리지 않습니다.
        black_override = _repair_black_level(raw)
        if black_override is not None:
            params["user_black"] = black_override

        if target_kelvin and target_kelvin > 0:
            from .develop.engine import NEUTRAL_KELVIN, _kelvin_to_rgb

            daylight = np.array(raw.daylight_whitebalance[:3], dtype=np.float64)
            mult = daylight * (_kelvin_to_rgb(NEUTRAL_KELVIN) / _kelvin_to_rgb(target_kelvin))
            params["user_wb"] = [float(mult[0]), float(mult[1]), float(mult[2]), float(mult[1])]
        else:
            params["use_camera_wb"] = True
        rgb = raw.postprocess(**params)
    # postprocess는 카메라 flip을 이미 반영합니다. 16비트(0~65535)를
    # float 0~255로 옮깁니다 — 소수점까지 남아 계조가 살아 있습니다.
    image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR).astype(np.float32) / 257.0

    # 이 PC에서 잰 기종 보정이 있으면 먼저 적용합니다. 프로파일(색 연출)보다
    # 앞이어야 합니다 — 보정은 "기준을 맞추는" 것이고 프로파일은 그 위에
    # 얹는 연출이라, 순서가 바뀌면 연출까지 함께 비틀립니다.
    if calibration is not False:
        image = _apply_calibration(image, path, calibration)

    if apply_profile:
        from .develop.engine import apply_camera_profile

        image = apply_camera_profile(image)
    return image


def _apply_calibration(image, path: Path, calibration):
    """저장된 기종 보정을 적용합니다. 없으면 그대로 돌려줍니다."""
    from .develop import calibration as calib

    try:
        if calibration is None:
            metadata = read_metadata(path)
            key = calib.camera_key(metadata.camera_make, metadata.camera_model)
            calibration = calib.load(key)
        return calib.apply(image, calibration)
    except Exception:  # noqa: BLE001 - 보정 실패가 현상을 막으면 안 됩니다
        log.debug("기종 보정 적용 실패: %s", path.name, exc_info=True)
        return image


def _channel_floors(raw) -> list[float] | None:
    """베이어 위치별로 센서 바닥을 직접 잽니다.

    LibRaw이 보고하는 채널별 블랙이 진짜인지 판별하는 기준입니다. 실제
    오프셋이라면 센서에서 잰 채널별 바닥에도 같은 차이가 보여야 합니다.
    """
    try:
        colors = raw.raw_colors_visible
        image = raw.raw_image_visible
    except Exception:  # noqa: BLE001
        return None
    if image.size == 0:
        return None

    floors: list[float] = []
    for index in range(4):
        values = image[colors == index]
        if values.size < 256:
            return None
        # 가장 어두운 0.1%의 중앙값. 평균이 아니라 중앙값이라 핫픽셀에 안 흔들립니다.
        count = max(64, values.size // 1000)
        darkest = np.partition(values, count)[:count]
        floors.append(float(np.median(darkest)))
    return floors


def _repair_black_level(raw) -> int | None:
    """LibRaw이 블랙 페데스탈을 놓친 기종을 바로잡고 쓸 값을 돌려줍니다.

    지원 기종은 black_level_per_channel이 센서 바닥과 맞습니다(예: [2048]×4).
    LibRaw이 모르는 기종은 페데스탈을 통째로 놓쳐 아주 낮은 값이 나오는데
    (실측: EOS R6 Mark III는 [0,38,113,78], 실제 바닥은 ~2000) 그대로 두면
    페데스탈이 안 빠져 이미지가 뜹니다.

    그런데 user_black은 LibRaw의 **전역** 블랙만 바꿉니다. 채널별 cblack은
    그 위에 그대로 더 빠지므로, 위 예에서 파랑만 113을 더 잃습니다. 그래서
    페데스탈만 채워 넣으면 이번엔 파랑이 깎여 노란-초록으로 뜹니다
    (실측: R6 Mark III에서 카메라 JPEG 대비 B 0.750 → 0.468).

    센서에서 잰 채널별 바닥과 견줘 그 채널 차이가 허수로 판명되면, 미리
    화소에 그만큼 더해 두어 결과적으로 균일하게 빠지게 합니다. 실제 오프셋인
    기종은 건드리지 않습니다 — 정상 기종에 이 보정을 걸면 오히려 크게
    틀어집니다(실측: R6 Mark II 오차 0.109 → 0.785).
    """
    try:
        black = list(raw.black_level_per_channel)
        sample = raw.raw_image_visible[::3, ::3]
    except Exception:  # noqa: BLE001
        return None
    if not black or sample.size == 0:
        return None

    floor = float(np.percentile(sample, 0.5))
    # 보고된 블랙이 센서 바닥보다 크게 낮다 = 페데스탈을 놓쳤다.
    # 이 조건이 아니면 지원 기종이므로 아무것도 하지 않습니다.
    if max(black) >= floor * 0.5:
        return None

    reported_spread = max(black) - min(black)
    if reported_spread <= 32:
        # 채널이 균일하면 LibRaw이 제대로 읽은 것으로 봅니다. 밝은 장면은
        # 진짜 검정이 없어 센서 바닥이 높게 나오므로, 이 조건이 없으면
        # 블랙이 정말 0인 카메라의 밝은 사진을 잘못 눌러 버립니다.
        return None

    measured = _channel_floors(raw)
    if measured is None:
        return int(floor)
    measured_spread = max(measured) - min(measured)

    # 보고된 채널 차이가 실측보다 훨씬 크면 그 값은 허수입니다.
    # 여유(32)는 노이즈로 생기는 실측 편차를 넘기기 위한 것입니다.
    if reported_spread <= measured_spread + 32:
        return int(floor)  # 진짜 채널 오프셋 — 그대로 두고 페데스탈만 채웁니다

    low = min(black)
    try:
        image = raw.raw_image  # 쓰기 가능한 뷰 — postprocess가 이 값을 씁니다
        colors = raw.raw_colors
        white = int(raw.white_level)
        for index in range(4):
            extra = black[index] - low
            if extra <= 0:
                continue
            mask = colors == index
            # 포화 근처에 더하면 흰색이 넘칩니다. 화이트 레벨에서 자릅니다.
            image[mask] = np.minimum(
                image[mask].astype(np.int32) + extra, white
            ).astype(image.dtype)
    except Exception:  # noqa: BLE001 - 보정 실패가 현상을 막으면 안 됩니다
        return int(floor)

    # 화소에 (cblack[c] - low)를 더했으므로, 전역 블랙은 그만큼 낮춰야
    # 채널마다 정확히 floor가 빠집니다.
    return int(floor) - low


def to_display(image: np.ndarray) -> np.ndarray:
    """float 0~255 이미지를 표시용 8비트로 변환합니다 (마지막 단계에서만)."""
    if image.dtype == np.uint8:
        return image
    return np.clip(image, 0.0, 255.0).astype(np.uint8)


def _flip_to_orientation(flip: int) -> int:
    """LibRaw의 flip 값을 EXIF Orientation으로 변환합니다."""
    return {0: 1, 3: 3, 5: 8, 6: 6}.get(flip, 1)


# ---------------------------------------------------------------- 메타데이터


def _ratio_to_float(tag) -> float | None:
    try:
        value = tag.values[0]
        return float(value.num) / float(value.den) if value.den else None
    except Exception:  # noqa: BLE001
        return None


def _element_to_float(value) -> float | None:
    """태그 **값 하나**를 실수로. `_ratio_to_float`은 태그 객체를 받습니다.

    둘을 헷갈리면 조용히 None이 됩니다 — 실제로 GPS 파서에 `_ratio_to_float`을
    원소마다 부르는 실수를 했고, 예외를 삼키는 코드라 아무 경고 없이 위치가
    통째로 사라졌습니다. 니콘 Z9 실파일로 돌려 보고서야 알았습니다.

    EXIF의 도/분/초는 한 배열 안에 정수와 분수가 섞여 옵니다
    (예: `[44, 382467/10000, 0]`).
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
    """내장 프리뷰 JPEG에서 EXIF를 읽습니다.

    CR3처럼 TIFF 기반이 아닌 컨테이너(ISO BMFF)는 exifread가 원본을 파싱하지
    못합니다. 다행히 프리뷰 JPEG에는 EXIF가 그대로 들어 있어서 카메라·ISO·
    셔터·조리개·초점거리를 건질 수 있습니다.
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
    "EXIF LensModel",        # 표준 (Sony, Canon, Nikon 최신, Fujifilm …)
    "Image LensModel",       # 서브 IFD를 독립 TIFF로 읽었을 때 (CR3의 CMT2)
    "MakerNote LensModel",
    "MakerNote Lens",        # Pentax, Minolta 는 여기에 읽을 수 있는 이름을 씁니다
    "MakerNote LensType",    # Canon, Pentax — 숫자 ID일 때도 있어 뒤로 미룹니다
)

_LENS_PLACEHOLDERS = {"unknown", "n/a", "na", "----", "none", "manual lens"}


def _lens_from_tags(tags) -> str | None:
    """여러 제조사 표기를 훑어 렌즈 이름을 찾습니다.

    표준 EXIF LensModel만 보면 MakerNote에만 렌즈를 쓰는 기종(펜탁스, 미놀타
    등)에서 렌즈가 통째로 비어 자동 광학 보정이 동작하지 않습니다.
    """
    for key in _LENS_TAGS:
        if key not in tags:
            continue
        value = str(tags[key]).strip()
        if not value or value.lower() in _LENS_PLACEHOLDERS:
            continue
        # LensType 등은 "61182" 같은 숫자 ID로 나오기도 합니다. 이름이 아니면
        # DB 조회에 쓸 수 없으므로 건너뜁니다.
        if not any(ch.isalpha() for ch in value):
            continue
        return value
    return None


def read_metadata(path: Path) -> RawMetadata:
    """RAW의 EXIF를 읽습니다. 실패해도 예외 없이 빈 메타데이터를 돌려줍니다.

    A6700은 최대 11fps 연사이므로 서브초 단위까지 읽어야 연사 그룹을
    시간순으로 올바르게 정렬할 수 있습니다.
    """
    try:
        with path.open("rb") as fh:
            tags = exifread.process_file(fh, details=False)
    except Exception as exc:  # noqa: BLE001
        log.warning("EXIF 읽기 실패 %s: %s", path.name, exc)
        tags = {}

    if not tags:
        # CR3는 TIFF가 아니라 ISO BMFF라 exifread가 통째로 실패합니다.
        # 전용 파서로 moov/uuid 안의 CMT 박스를 읽습니다 (렌즈·촬영시각 포함).
        from .cr3 import is_cr3, read_exif_tags

        if is_cr3(path):
            tags = read_exif_tags(path)

    if not tags and path.suffix.lower() in HEIF_EXTENSIONS:
        # HEIF도 ISO BMFF라 exifread가 못 엽니다. 컨테이너 안에 EXIF가 통째로
        # (Exif\0\0 + TIFF) 들어 있으므로 그 블록만 꺼내 다시 읽힙니다.
        # 촬영시각이 없으면 장면 묶기가 화면 변화에만 의존하게 됩니다.
        tags = _tags_from_heif(path)

    if not tags:
        # 그래도 못 읽으면 프리뷰 JPEG의 빈약한 EXIF라도 씁니다
        tags = _tags_from_preview(path)
    if not tags:
        return RawMetadata(path=path)

    capture_time = None
    # 프리뷰 EXIF에는 DateTimeOriginal이 빠져 있는 경우가 있어 대안을 함께 봅니다
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

    return RawMetadata(
        path=path,
        capture_time=capture_time,
        camera_model=str(tags["Image Model"]).strip() if "Image Model" in tags else None,
        camera_make=str(tags["Image Make"]).strip() if "Image Make" in tags else None,
        lens_model=_lens_from_tags(tags),
        iso=_int_tag(iso) if iso else None,
        shutter_speed=_ratio_to_float(shutter) if shutter else None,
        aperture=_ratio_to_float(aperture) if aperture else None,
        focal_length=_ratio_to_float(focal) if focal else None,
        orientation=_int_tag(orientation) or 1 if orientation else 1,
        latitude=_gps_degrees(tags, "GPS GPSLatitude", "GPS GPSLatitudeRef"),
        longitude=_gps_degrees(tags, "GPS GPSLongitude", "GPS GPSLongitudeRef"),
    )


def _gps_degrees(tags: dict, value_key: str, ref_key: str) -> float | None:
    """EXIF의 도/분/초 3원소를 부호 있는 십진 도로.

    EXIF는 위도를 '35도 41분 12.3초' + 'N' 형태로 나눠 담습니다. 남반구·
    서반구는 ref가 S/W이고 값 자체는 양수라, ref를 안 보면 지구 반대편이
    됩니다.
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
