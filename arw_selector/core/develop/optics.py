"""광학 보정 — 렌즈 왜곡, 비네팅, 색수차.

두 갈래로 동작합니다.

1. **자동**: lensfun 데이터베이스에서 카메라와 렌즈를 찾아 프로필을 적용합니다.
   정확하지만 DB에 없는 렌즈는 사용할 수 없습니다. 실측에서 소니 순정 E PZ 16-50mm는
   매칭됐지만 탐론 A069(50-300mm)는 DB에 없었습니다.
2. **수동**: 왜곡·비네팅·색수차를 직접 조정합니다. DB에 없는 렌즈나 자동 결과가
   마음에 안 들 때 씁니다.

lensfunpy가 없어도 수동 보정은 동작해야 합니다 — 선택 의존성으로 둡니다.
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
except ImportError:  # pragma: no cover - 설치 여부에 따라 갈립니다
    lensfunpy = None
    LENSFUN_AVAILABLE = False


# OpticsSettings는 settings.py 한 곳에만 둡니다. 예전에 여기에도 같은 이름의
# 사본이 있었는데, settings.py 쪽만 계속 자라면서(lens_override, defringe_green,
# 색조 지정 등) 둘이 갈라졌습니다. 실수로 이 모듈에서 import하면 필드가 빠진
# 다른 클래스를 쓰게 되어 저장/불러오기가 조용히 어긋납니다.


@dataclass(frozen=True)
class LensMatch:
    """렌즈 DB 조회 결과. UI가 무엇이 잡혔는지 보여줘야 합니다."""

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
    """사용자가 추가 렌즈 프로필(.xml)을 넣는 폴더.

    번들 DB는 lensfunpy 릴리스 시점 스냅샷이라 최신 렌즈가 빠져 있습니다
    (실측: 탐론 A069 미등록). 앱을 다시 빌드하지 않고도 커버리지를 넓힐 수
    있도록, 이 폴더의 XML을 번들 DB에 얹어 함께 읽습니다. lensfun 공식
    저장소나 직접 만든 프로필을 그대로 떨어뜨리면 됩니다.
    """
    from pathlib import Path as _Path

    from ..presets import user_config_dir

    return _Path(user_config_dir()) / "lensfun"


V1_CACHE_DIR = ".v1cache"
"""버전 2 XML을 버전 1로 변환해 두는 폴더 (사용자 폴더 하위)."""


def _prepare_user_xmls(user_dir: "Path") -> list[str]:
    """사용자 폴더의 XML을 라이브러리가 읽을 수 있는 형태로 준비합니다.

    lensfun 저장소의 최신 DB는 포맷 버전 2인데 설치된 라이브러리는 1까지만
    읽습니다. 사용자가 받은 파일을 그대로 넣어도 되도록, 버전 2면 변환본을
    만들어 그것을 넘깁니다. 원본은 건드리지 않습니다.

    lensfunpy의 paths는 폴더가 아니라 **파일 목록**을 받습니다(폴더를 주면
    Permission denied로 실패합니다).
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
    """lensfun DB는 로딩이 무거우므로 한 번만 만듭니다.

    사용자 폴더에 XML이 있으면 함께 읽습니다. 그 폴더가 깨져 있어도 번들
    DB만으로 계속 동작해야 합니다.
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
    """렌즈 DB를 다시 읽습니다. 새 (바디 수, 렌즈 수)를 돌려줍니다.

    DB는 로딩이 무거워 한 번만 읽고 캐시합니다. 그래서 앱을 켜 둔 채 프로필
    XML을 넣으면 반영되지 않습니다 — 사용자가 직접 다시 읽게 해 줍니다.
    """
    _database.cache_clear()
    return database_coverage()


def ensure_user_lens_db_dir() -> "Path":
    """사용자 렌즈 프로필 폴더를 만들어 두고 경로를 돌려줍니다.

    폴더가 없으면 어디에 넣어야 할지 알 수 없습니다. 열어 보여 주기 전에
    만들어 둡니다.
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
    """(바디 수, 렌즈 수). 사용자 폴더를 더한 최종 커버리지입니다."""
    db = _database()
    if db is None:
        return (0, 0)
    return (len(db.cameras), len(db.lenses))


_APERTURE = re.compile(r"\bF(\d)", re.IGNORECASE)
_MODEL_CODE = re.compile(r"\s+[A-Z]\d{3,4}\b")  # 탐론 A069, 시그마 C013 같은 코드

_GLUED_MOUNT = re.compile(
    r"^(RF|EF-S|EF|FE|E|Z|XF|XC|DT|DA|FA)(?=\d)", re.IGNORECASE
)
"""초점거리에 바로 붙은 마운트 표기 (RF100-500mm, XF18-55mm …)."""

_PENTAX_PREFIX = re.compile(r"^(smc|hd)\s+pentax-?[a-z*]*\s+", re.IGNORECASE)
"""smc PENTAX-DA / HD PENTAX-D FA* 같은 펜탁스 접두사."""


def _lens_name_variants(name: str) -> list[str]:
    """EXIF 렌즈명을 lensfun 표기에 맞춰 여러 후보로 풀어 줍니다.

    제조사마다 EXIF 표기가 제각각입니다:
      "E 50-300mm F4.5-6.3 A069"  (Sony/Tamron EXIF)
      "50-300mm f/4.5-6.3"        (lensfun 표기)
    한 번에 못 찾으면 조금씩 느슨하게 만들어 다시 시도합니다.
    """
    variants = [name]

    # F4.5 -> f/4.5 (lensfun은 슬래시 표기를 씁니다)
    slashed = _APERTURE.sub(r"f/\1", name)
    if slashed != name:
        variants.append(slashed)

    # 끝에 붙는 제조사 모델 코드(A069 등)를 떼어 봅니다
    for candidate in list(variants):
        stripped = _MODEL_CODE.sub("", candidate).strip()
        if stripped and stripped != candidate:
            variants.append(stripped)

    # 제조사마다 앞에 붙이는 말이 다릅니다. lensfun은 대체로 이걸 떼고 씁니다.
    #   Sony      "FE 70-200mm F2.8 GM OSS II" / "E 18-135mm …" / "DT …"
    #   Canon     "RF100-500mm …" / "EF24-70mm …"
    #   Nikon     "NIKKOR Z 24-70mm f/2.8 S" / "AF-S NIKKOR …"
    #   Fujifilm  "XF18-55mmF2.8-4 R LM OIS" / "XC …"
    #   Olympus   "OLYMPUS M.12-40mm F2.8" / "M.Zuiko Digital …"
    #   Panasonic "LUMIX G VARIO 12-60/F3.5-5.6"
    #   Pentax    "smc PENTAX-DA 18-55mm …" / "HD PENTAX-DA …"
    for candidate in list(variants):
        parts = candidate.split()
        if len(parts) > 1 and parts[0].upper() in {
            "E", "FE", "RF", "EF", "EF-S", "Z", "DT", "SEL", "XF", "XC",
            "NIKKOR", "OLYMPUS", "LUMIX", "SMC", "HD", "DA", "FA",
        }:
            variants.append(" ".join(parts[1:]))

    # 마운트 표기가 초점거리에 바로 붙는 경우 ("RF100-500mm", "XF18-55mm").
    # 공백으로 나눠서는 못 떼므로 숫자 앞에서 잘라 냅니다.
    for candidate in list(variants):
        stripped = _GLUED_MOUNT.sub("", candidate).strip()
        if stripped and stripped != candidate:
            variants.append(stripped)

    # 펜탁스는 "smc PENTAX-DA", "HD PENTAX-D FA*" 처럼 하위 구분자가 붙습니다.
    for candidate in list(variants):
        stripped = _PENTAX_PREFIX.sub("", candidate).strip()
        if stripped and stripped != candidate:
            variants.append(stripped)

    # 두 단어짜리 접두사도 떼어 봅니다 (AF-S NIKKOR, LUMIX G, M.Zuiko Digital …)
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
    """렌즈명에서 초점거리 범위를 뽑습니다. "50-300mm" -> (50, 300)."""
    match = _FOCAL.search(name)
    if not match:
        return None
    if match.group(1) and match.group(2):
        low, high = float(match.group(1)), float(match.group(2))
    else:
        low = high = float(match.group(3))
    return (min(low, high), max(low, high))


def _focal_matches(lens, wanted: tuple[float, float] | None) -> bool:
    """후보 렌즈의 초점거리 범위가 실제 렌즈와 겹치는지.

    lensfun의 loose_search는 아주 관대해서, 전혀 다른 이름에도 아무 렌즈나
    돌려줍니다(실측: "존재하지않는렌즈 999mm" -> "E 24mm F2.8"). 그대로 쓰면
    엉뚱한 왜곡·비네팅 프로필이 사진에 적용됩니다. 보정을 안 하는 것보다
    나쁩니다. 초점거리로 최소한의 검산을 합니다.
    """
    if wanted is None:
        return True
    try:
        low, high = float(lens.min_focal), float(lens.max_focal)
    except (AttributeError, TypeError, ValueError):
        return True  # 정보가 없으면 막지 않습니다
    if low <= 0 or high <= 0:
        return True
    # 범위가 '겹치기만' 하면 통과시키면 안 됩니다. 이름이 24-105인데 100-500
    # 렌즈가 100~105 구간에서 겹친다는 이유로 통과해 버립니다. 같은 렌즈라면
    # 양 끝이 비슷해야 합니다.
    #
    # 허용치는 10%입니다. 20%로 뒀더니 망원에서 너무 헐거워, DB가 커지자
    # 800mm 렌즈가 999mm 요청에 걸렸습니다(199 < 999*0.2). 실제 표기 반올림은
    # 1% 수준이라 10%면 충분합니다.
    return (
        abs(low - wanted[0]) <= max(2.0, wanted[0] * 0.1)
        and abs(high - wanted[1]) <= max(2.0, wanted[1] * 0.1)
    )


# 앞에 \b를 두면 "E-M1MarkIII"처럼 숫자에 바로 붙은 표기를 놓칩니다
# ('1'과 'M' 사이에는 단어 경계가 없습니다).
_MARK = re.compile(r"mark\s*([ivx]+)\b", re.IGNORECASE)
_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6"}


def _camera_name_variants(model: str, make: str | None = None) -> list[str]:
    """EXIF 바디명을 lensfun 표기에 맞춰 여러 후보로 풀어 줍니다.

    카메라 EXIF의 Model 필드에는 제조사가 안 들어갑니다("EOS R6 Mark II").
    반면 lensfun은 제조사를 붙여 짧게 씁니다("Canon EOS R6m2"). 예전에는
    모델명 첫 단어를 제조사로 넘겼는데, 그러면 maker="EOS"로 조회해서
    캐논 바디가 통째로 안 잡혔습니다.
    """
    variants = [model]

    # "Mark II" -> "m2" (lensfun 표기)
    def _to_m(match: "re.Match[str]") -> str:
        return "m" + _ROMAN.get(match.group(1).lower(), match.group(1))

    shortened = _MARK.sub(_to_m, model)
    shortened = re.sub(r"\s+(m\d)\b", r"\1", shortened)  # "R6 m2" -> "R6m2"
    if shortened != model:
        variants.append(shortened)

    # 제조사를 앞에 붙인 형태도 시도합니다. EXIF Make가 있으면 그걸 쓰고,
    # 없으면 모델명 생김새로 추정합니다(제조사별 접두사는 꽤 고유합니다).
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
    """바디명 표기 변형을 차례로 시도합니다. maker는 넘기지 않습니다.

    EXIF Model에는 제조사가 없어서, 첫 단어를 maker로 넘기면 오히려 검색이
    실패합니다. lensfun의 loose_search가 제조사 없이도 잘 찾습니다.
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
    """lensfun의 범용 대체 렌즈인지.

    이름이 전혀 안 맞으면 lensfun은 "Rectilinear 10-1000mm f/1.0" 같은 범용
    항목을 물려 줍니다. 실측 보정값이 없는 자리표시자라, 이걸 '찾았다'고
    보고하면 사용자는 렌즈 프로필이 적용된 줄 착각합니다.
    """
    model = (getattr(lens, "model", "") or "").lower()
    if "rectilinear" in model:
        return True
    try:
        low, high = float(lens.min_focal), float(lens.max_focal)
    except (AttributeError, TypeError, ValueError):
        return False
    # 실제 줌은 아무리 넓어도 20배 남짓입니다 (18-300mm ≈ 16배)
    return low > 0 and high / low > 25.0


def _covers_focal(lens, focal: float | None) -> bool:
    """실제로 그 초점거리로 찍을 수 있는 렌즈인지.

    EXIF의 촬영 초점거리는 이름 추정보다 확실한 근거입니다. 363mm로 찍은
    사진에 24-105mm 프로필이 붙으면 왜곡 보정이 엉뚱하게 들어갑니다.
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
    """표기 변형을 차례로 시도해 렌즈를 찾습니다. 못 찾으면 빈 리스트.

    이름에서 뽑은 초점거리 범위와, 실제 촬영 초점거리(EXIF) 둘 다로
    걸러냅니다 — 틀린 프로필을 적용하느니 수동 보정으로 넘기는 편이 낫습니다.
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
    """EXIF로 카메라와 렌즈를 조회합니다."""
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
                # 절대 경로를 문구에 박지 않습니다. PC마다 다르고, 개발
                # 기계의 경로가 그대로 보이면 남의 경로처럼 읽힙니다.
                # 폴더는 바로 아래 '렌즈 프로필 폴더' 버튼이 열어 줍니다.
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
    """데이터베이스에 등록된 렌즈 목록입니다.

    EXIF 렌즈명이 비어 있거나 DB 이름과 다를 때 사용자가 직접 고를 수
    있어야 합니다. 서드파티 렌즈나 어댑터를 쓰면 흔히 발생합니다.

    maker는 **거르는 조건이 아니라 정렬 우선순위**입니다. 예전에는 소니 바디에
    maker='Sony'로 걸러서 탐론·시그마 같은 서드파티 렌즈가 목록에서 통째로
    사라졌습니다 — 서드파티를 물리는 경우가 훨씬 흔한데도 고를 수가 없었습니다.
    limit도 기본 200이라 1304개 중 앞부분만 나왔습니다(탐론이 잘려 나갔습니다).
    기본은 전부 보여 주고, limit은 0이면 무제한입니다.
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
        # 같은 제조사를 위로 올리되, 나머지도 계속 보이게 둡니다
        same_maker = bool(maker) and label.lower().startswith(maker.lower())
        return (0 if same_maker else 1, label.lower())

    ordered = sorted(names, key=sort_key)
    return ordered[:limit] if limit and limit > 0 else ordered


def available_cameras(keyword: str | None = None, limit: int = 200) -> list[str]:
    """데이터베이스에 등록된 카메라 목록입니다."""
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
    """사용자가 직접 고른 이름으로 렌즈를 찾습니다."""
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


def apply_auto_correction(
    image: np.ndarray, metadata: RawMetadata | None, settings: OpticsSettings
) -> np.ndarray:
    """lensfun 프로필로 왜곡과 비네팅을 보정합니다.

    프로필이 없으면 원본을 그대로 돌려준다 — 실패를 조용히 넘기고 수동
    보정이 이어서 동작하게 합니다.
    """
    if not settings.auto_enabled or not LENSFUN_AVAILABLE or metadata is None:
        return image

    db = _database()
    if db is None or not metadata.camera_model:
        return image
    if not (settings.lens_override or metadata.lens_model):
        return image

    try:
        cameras = _find_cameras_loose(db, metadata.camera_model, metadata.camera_make)
        if not cameras:
            return image
        camera = cameras[0]

        # 사용자가 직접 고른 렌즈가 있으면 EXIF보다 우선합니다
        lens_name = settings.lens_override or metadata.lens_model
        lenses = _find_lenses_loose(db, camera, lens_name)
        if not lenses:
            return image

        height, width = image.shape[:2]
        modifier = lensfunpy.Modifier(
            lenses[0], camera.crop_factor, width, height
        )
        modifier.initialize(
            metadata.focal_length or 50.0,
            metadata.aperture or 5.6,
            10.0,            # 피사체 거리(m) — EXIF에 없으므로 일반적인 값
            pixel_format=np.uint8,
        )

        result = image
        if settings.auto_vignetting:
            # pixel_format을 반드시 맞춰야 합니다. 선언을 빼면 lensfun이
            # 0~255 기준 연산을 0~1 값에 적용해 결과가 폭주합니다.
            #
            # 반드시 copy()여야 합니다. lensfun은 배열을 제자리에서 고치는데,
            # ascontiguousarray는 이미 연속이면 원본을 그대로 돌려주므로
            # 호출자가 넘긴 이미지까지 파괴됩니다.
            corrected = np.ascontiguousarray(result).astype(np.uint8, copy=True)
            if modifier.apply_color_modification(corrected):
                # 프로필과 촬영 조건이 어긋나면 여전히 비정상 값이 나올 수
                # 있습니다. 그대로 쓰면 픽셀이 쓰레기가 되므로 검사하고 버립니다.
                if np.all(np.isfinite(corrected.astype(np.float32))):
                    result = corrected
                else:
                    log.warning(
                        "비네팅 프로필이 비정상 값을 냈다 — 건너뛴다 (%s)",
                        lenses[0].model,
                    )

        if settings.auto_chromatic:
            # 배율 색수차 — 채널마다 배율이 미세하게 달라 생기는 색 테두립니다.
            # 채널별 좌표를 따로 받아 각각 리매핑해야 합니다.
            coords = modifier.apply_subpixel_distortion()
            if coords is not None:
                channels = list(cv2.split(result))
                # lensfun은 (h, w, 3, 2) — 채널별 (x, y) 좌표를 줍니다
                for index in range(3):
                    channels[index] = cv2.remap(
                        channels[index],
                        np.ascontiguousarray(coords[:, :, index, :]),
                        None, cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_REPLICATE,
                    )
                result = cv2.merge(channels)

        if settings.auto_distortion:
            coords = modifier.apply_geometry_distortion()
            if coords is not None:
                result = cv2.remap(
                    result, coords, None, cv2.INTER_LANCZOS4,
                    borderMode=cv2.BORDER_REPLICATE,
                )

        return result
    except Exception as exc:  # noqa: BLE001 - 보정 실패로 현상을 막지 않습니다
        log.warning("자동 렌즈 보정 실패: %s", exc)
        return image


def apply_manual_distortion(image: np.ndarray, amount: int) -> np.ndarray:
    """수동 왜곡 보정. 방사 왜곡 모델을 단순화해서 씁니다.

    음수는 배럴 왜곡(볼록)을 펴고, 양수는 핀쿠션(오목)을 폅니다.
    """
    if not amount:
        return image

    height, width = image.shape[:2]
    k = amount / 100.0 * 0.35

    # 정규화 좌표에서 r' = r * (1 + k*r^2)
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


def apply_manual_vignetting(image: np.ndarray, amount: int) -> np.ndarray:
    """수동 비네팅 보정. 양수면 주변부를 밝혀 어두워짐을 상쇄합니다."""
    if not amount:
        return image

    height, width = image.shape[:2]
    y, x = np.indices((height, width), dtype=np.float32)
    center_x, center_y = width / 2.0, height / 2.0
    radius = np.sqrt(
        ((x - center_x) / center_x) ** 2 + ((y - center_y) / center_y) ** 2
    )
    gain = 1.0 + (amount / 100.0) * 0.6 * np.clip(radius, 0.0, 1.5) ** 2

    # 입력 dtype을 유지합니다. 광학 보정은 파이프라인의 맨 앞이라, 여기서
    # uint8로 떨구면 이후의 톤·곡선이 256단계 위에서 계산되어 부드러운 하늘
    # 같은 곳에 띠(밴딩)가 생깁니다 — 디모자이크가 float으로 넘겨 준 14비트
    # 정밀도를 비네팅 슬라이더 하나 때문에 잃게 됩니다.
    return np.clip(
        image.astype(np.float32) * gain[:, :, None], 0, 255
    ).astype(image.dtype)


def sample_hue(image: np.ndarray, x: int, y: int, radius: int = 4) -> int:
    """지정한 지점 주변의 대표 색조를 구합니다 (스포이드).

    언저리 색은 렌즈와 장면마다 달라서 고정값으로는 잘 맞지 않습니다.
    실제 언저리를 찍어 그 색조를 기준으로 삼는 편이 정확합니다.

    돌려주는 값은 apply_defringe와 같은 8비트 HSV 색조(0~179)입니다. 화면이
    넘겨 주는 미리보기는 디모자이크 결과라 float인데, float을 그대로 HSV로
    바꾸면 OpenCV가 색조를 0~359로 돌려주고 채도도 0~1이 됩니다. 그러면
    스포이드가 실제와 전혀 다른 값을 내놓아(보라 145 → 110) 언저리 제거가
    엉뚱한 색에 걸립니다. 8비트로 맞춘 뒤 계산합니다.
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

    # 채도가 낮은 픽셀은 색조가 불안정하므로 가중치를 줄입니다
    weights = saturation + 1.0
    # 색조는 원형이라 단순 평균이 아니라 벡터 평균을 써야 합니다
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
    """색수차로 생긴 보라/녹색 언저리를 제거합니다.

    고대비 경계에서 해당 색조를 띤 픽셀만 골라 채도를 낮춥니다. 실제 피사체
    색까지 건드리지 않도록 경계 근처로 범위를 좁힙니다.
    """
    if not purple and not green:
        return image

    # HSV 왕복은 8비트 기준입니다(H 0~179, S 0~255). 파이프라인 중간값은
    # float 0~255인데 그대로 넘기면 OpenCV가 float을 0~1 입력으로 보고 S를
    # 0~1로 돌려줍니다. 그 값을 다시 uint8 HSV로 해석해 되돌리면 채도가
    # 통째로 0이 되어 사진 전체가 흑백이 됩니다 — 색수차 제거를 켰을 뿐인데
    # 색이 사라지는 것으로 나타납니다. _apply_hsl과 같은 방식으로 8비트에
    # 맞춰 계산하고, 결과는 받은 dtype으로 돌려줍니다.
    source = image if image.dtype == np.uint8 else np.clip(image, 0, 255).astype(np.uint8)

    hsv = cv2.cvtColor(source, cv2.COLOR_BGR2HSV).astype(np.float32)
    hue, saturation = hsv[:, :, 0], hsv[:, :, 1]

    # 경계 마스크 — 언저리는 대비가 큰 곳에만 생깁니다
    gray = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY)
    edges = cv2.dilate(
        cv2.Laplacian(gray, cv2.CV_32F).__abs__(), np.ones((3, 3), np.uint8)
    )
    edge_mask = np.clip(edges / max(1.0, edges.max()) * 4.0, 0.0, 1.0)

    for amount, center, width in (
        (purple, purple_hue, 20), (green, green_hue, 18)
    ):
        if not amount:
            continue
        distance = np.abs(hue - center)
        distance = np.minimum(distance, 180.0 - distance)
        band = np.exp(-(distance ** 2) / (2 * width * width))
        saturation *= 1.0 - (amount / 100.0) * band * edge_mask

    hsv[:, :, 1] = np.clip(saturation, 0, 255)
    result = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    return result if image.dtype == np.uint8 else result.astype(image.dtype)


def apply_optics(
    image: np.ndarray, settings: OpticsSettings, metadata: RawMetadata | None = None
) -> np.ndarray:
    """광학 보정 전체. 자동 프로필 → 수동 조정 순으로 적용합니다."""
    if settings.is_neutral():
        return image

    result = apply_auto_correction(image, metadata, settings)
    result = apply_manual_distortion(result, settings.distortion)
    result = apply_manual_vignetting(result, settings.manual_vignetting)
    result = apply_defringe(
        result,
        settings.defringe_purple,
        settings.defringe_green,
        settings.defringe_purple_hue,
        settings.defringe_green_hue,
    )
    return result
