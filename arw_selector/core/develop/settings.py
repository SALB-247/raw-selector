"""보정 파라미터 데이터 모델.

Lightroom / Camera Raw의 패널 구성을 따라갑니다. 사용자가 이미 익숙한
이름과 범위를 쓰는 편이 배우기 쉽고, 나중에 XMP로 내보낼 때도 대응이
단순해집니다.

값 범위는 대부분 -100~+100이고, 노출만 EV 단윕니다.
모든 dataclass는 picklable하고 dict 왕복이 가능해야 합니다 — 프리셋 파일과
내보내기 워커가 둘 다 필요로 합니다.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from typing import Any

# HSL / 색상 혼합에서 다루는 8개 색상대. Lightroom과 같은 구성입니다.
HSL_BANDS = ("red", "orange", "yellow", "green", "aqua", "blue", "purple", "magenta")
HSL_BAND_LABELS = {
    "red": "빨강", "orange": "주황", "yellow": "노랑", "green": "녹색",
    "aqua": "아쿠아", "blue": "파랑", "purple": "자주", "magenta": "마젠타",
}
# 각 색상대의 중심 색조 (OpenCV HSV 기준 0~179)
HSL_BAND_CENTERS = {
    "red": 0, "orange": 15, "yellow": 30, "green": 60,
    "aqua": 90, "blue": 120, "purple": 140, "magenta": 160,
}


def _as_dict(values: Any) -> dict[str, Any]:
    """섹션 값을 dict로. dict가 아니면 빈 dict.

    프리셋은 사용자가 직접 열어 고칠 수 있는 YAML입니다. 섹션 하나를
    실수로 문자열이나 리스트로 만들어 두면 `dict(values)`는 "dictionary
    update sequence element..."로, `values.get(...)`은 AttributeError로
    터집니다. 그 예외는 보정 패널 전체를 열지 못하게 만듭니다 — 값 하나가
    이상한 것과 파일을 못 여는 것은 사용자에게 전혀 다른 사건입니다.
    """
    return dict(values) if isinstance(values, dict) else {}


def _as_int(value: Any, default: int) -> int:
    """숫자로 못 읽는 값은 기본값으로.

    bool은 int의 하위형이라 그냥 통과하는데, 그러면 `opacity: true`가 1이
    됩니다. 손 편집에서 나올 법한 실수라 명시적으로 걸러 기본값을 씁니다.
    """
    if isinstance(value, bool) or value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def _as_key_tuple(value: Any, allowed) -> tuple[str, ...]:
    """항목 목록을 정리합니다. 아는 키만 남깁니다.

    문자열 하나로 들어오면 (예: `include: camera`) 그대로 순회하면 글자
    단위로 쪼개져 조용히 빈 목록이 됩니다. 한 항목으로 봅니다.
    """
    if isinstance(value, str):
        value = (value,)
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    return tuple(k for k in value if k in allowed)


def _coerce_scalar(value: Any, default: Any) -> Any:
    """기본값의 타입에 맞춰 값을 맞춥니다. 못 맞추면 기본값.

    **dataclass는 타입을 검사하지 않습니다.** `sharpen_amount: "강하게"`는
    생성자를 그대로 통과해서, 불러오기는 조용히 성공하고 몇십 분 뒤
    내보내기에서 장마다 TypeError로 죽습니다. 원인을 찾기 가장 어려운
    모양이라 들어오는 자리에서 막습니다.

    NaN·inf도 기본값으로 되돌립니다. 그대로 두면 예외 없이 화소만
    쓰레기가 되어(clip → uint8 캐스팅에서 임의값) 결과물에 남습니다.
    """
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        return bool(value) if isinstance(value, (int, float)) else default
    if isinstance(default, int):
        return _as_int(value, default)
    if isinstance(default, float):
        if isinstance(value, bool) or value is None:
            return default
        try:
            result = float(value)
        except (TypeError, ValueError):
            return default
        return result if math.isfinite(result) else default
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    return value


def _merge_known(cls, values: Any, base: dict[str, Any]) -> Any:
    """알 수 없는 키는 버리고, 남은 값은 기본값의 타입에 맞춰 반영합니다.

    예전 버전이 저장한 프리셋에 지금은 없는 필드가 들어 있어도 열려야 합니다.
    손으로 편집한 파일에서 섹션이 dict가 아닌 값(문자열, 리스트 등)으로
    들어오는 경우도 있으므로 기본값으로 넘어갑니다.

    타입을 여기서 맞추는 이유는 _coerce_scalar 참고 — dataclass 생성자는
    타입을 검사하지 않아서, 걸러 두지 않으면 이상한 값이 그대로 살아남아
    한참 뒤 렌더에서 터집니다.
    """
    if not isinstance(values, dict):
        return cls(**base)

    valid = {f.name for f in fields(cls)}
    merged = dict(base)
    merged.update({
        k: _coerce_scalar(v, base[k])
        for k, v in values.items()
        if k in valid and k in base
    })
    try:
        return cls(**merged)
    except (TypeError, ValueError):
        # _coerce_scalar가 못 거른 모양(중첩 구조 등)이 남아 있으면 통째로 기본값
        return cls(**base)


@dataclass(frozen=True)
class BasicSettings:
    """기본 패널 — 화이트밸런스와 톤."""

    temperature: int = 0      # 절대 색온도(Kelvin). 0은 "손대지 않음"(as-shot 유지)
    tint: int = 0             # -100(초록) ~ +100(마젠타)
    exposure: float = 0.0     # EV, -5 ~ +5

    brightness: int = 0
    """중간톤 밝기 (-100 ~ +100). 노출과 다릅니다.

    노출은 전체에 2^EV를 곱해 하이라이트부터 날아갑니다. 밝기는 감마라
    흰색과 검정을 고정한 채 중간톤만 밀어 올립니다 — 역광 인물의 얼굴만
    살리고 싶을 때 이쪽이 맞습니다.
    """

    contrast: int = 0
    highlights: int = 0
    shadows: int = 0
    whites: int = 0
    blacks: int = 0
    texture: int = 0          # 중간 주파수 디테일
    clarity: int = 0          # 국소 대비
    dehaze: int = 0
    vibrance: int = 0
    saturation: int = 0


@dataclass(frozen=True)
class CurveSettings:
    """곡선 패널 — 파라메트릭 곡선과 채널별 포인트 곡선."""

    highlights: int = 0
    lights: int = 0
    darks: int = 0
    shadows: int = 0

    # (입력, 출력) 점들. 비어 있으면 항등.
    points_rgb: tuple[tuple[int, int], ...] = ()
    points_red: tuple[tuple[int, int], ...] = ()
    points_green: tuple[tuple[int, int], ...] = ()
    points_blue: tuple[tuple[int, int], ...] = ()

    def is_neutral(self) -> bool:
        return (
            self.highlights == 0 and self.lights == 0
            and self.darks == 0 and self.shadows == 0
            and not (self.points_rgb or self.points_red
                     or self.points_green or self.points_blue)
        )


class NoiseAlgorithm(str, Enum):
    """휘도 노이즈를 지우는 방식.

    같은 "노이즈 감소 50"이라도 방식마다 남는 디테일과 걸리는 시간이 크게
    다릅니다. 아래 값은 R6 Mark III ISO 6400 실파일(2048² 크롭)에서 잰
    것으로, 평탄 영역 노이즈를 원본의 50%까지 줄였을 때 남은 엣지
    그래디언트 비율과 32MP 환산 처리 시간입니다.
    """

    LEGACY = "legacy"
    """예전 방식. 디테일 보존 78.7%, 0.29초.

    지웠던 사진을 예전과 똑같이 재현해야 할 때만 씁니다. 슬라이더가
    실질적으로 동작하지 않습니다(60 이상은 100과 차이가 0.05).
    """

    BILATERAL = "bilateral"
    """양방향 필터. 디테일 보존 79.9%, 0.34초.

    가장 빠릅니다. 노이즈가 적은 저감도 사진에서 살짝만 다듬을 때.
    """

    NLMEANS = "nlmeans"
    """비국소 평균(표준). 디테일 보존 99.4%, 0.95초.

    떨어진 곳의 비슷한 무늬끼리 평균 내므로 엣지를 거의 잃지 않습니다.
    고감도 사진의 기본값입니다.
    """

    NLMEANS_HQ = "nlmeans_hq"
    """비국소 평균(고품질). 디테일 보존 99.9%, 2.6초.

    탐색 창이 넓어 표준보다 2.7배 느립니다. 크게 인화할 한 장에.
    """


NOISE_ALGORITHM_LABELS = {
    NoiseAlgorithm.NLMEANS: "표준 (비국소 평균)",
    NoiseAlgorithm.NLMEANS_HQ: "고품질 (비국소 평균, 느림)",
    NoiseAlgorithm.BILATERAL: "빠름 (양방향 필터)",
    NoiseAlgorithm.LEGACY: "기존 방식 (구버전 재현용)",
}
"""콤보박스 표시 순서 겸 이름. 권장하는 것부터 놓습니다."""


@dataclass(frozen=True)
class DetailSettings:
    """세부 패널 — 샤프닝과 노이즈 감소."""

    sharpen_amount: int = 0       # 0~150
    sharpen_radius: float = 1.0   # 0.5~3.0
    noise_reduction: int = 0      # 0~100 (휘도)
    color_noise_reduction: int = 0  # 0~100

    noise_algorithm: NoiseAlgorithm = NoiseAlgorithm.NLMEANS
    """휘도 노이즈를 지우는 방식. 각 값의 실측치는 NoiseAlgorithm 참고."""

    noise_detail: int = 50
    """디테일 보존 (0~100). 무늬가 있는 곳에 원본을 얼마나 되살릴지.

    노이즈 감소는 평탄한 곳에는 이롭고 머리카락·나뭇잎처럼 잔무늬가 있는
    곳에는 해롭습니다. 국소 대비가 노이즈보다 확실히 큰 자리에만 원본을
    섞어 되돌립니다. 0이면 전면 적용, 100이면 무늬 있는 곳을 거의 그대로.
    """

    color_noise_radius: int = 50
    """색 노이즈 반경 (0~100). 얼마나 큰 색 얼룩까지 볼지.

    고감도 색 노이즈는 화소 단위가 아니라 수십 화소짜리 얼룩입니다
    (실측: R6M3 ISO6400에서 색 노이즈의 54%가 4화소보다 큰 스케일).
    올리면 큰 얼룩까지 잡지만 진짜 색 경계도 함께 번집니다.
    """

    destripe: int = 0
    """LED월 가로 줄무늬 제거 (0~100).

    LED 패널의 PWM 점멸과 롤링셔터 판독이 어긋나면 가로 밴드가 남습니다.
    실측한 두 컷(DSC02751 ISO2500 1/800, DSC03868 ISO3200 1/1000) 모두
    주기가 **103px로 같았습니다** — ISO도 셔터도 다른데 같다는 것은
    피사체가 아니라 판독 주기에서 온다는 뜻입니다.

    기본 0(꺼짐)입니다. 줄무늬는 특정 촬영장에서만 나오는데, 늘 켜 두면
    수평선이 있는 풍경에서 하늘의 미묘한 그라데이션을 건드릴 수 있습니다.

    주기가 검출되지 않으면(16~400px 밖) 값을 올려도 아무 일도 하지 않습니다.
    """

    face_priority: int = 85
    """얼굴 우선 (0~100). 얼굴 밖에서 휘도 노이즈 감소를 얼마나 뺄지.

    고감도에서 눈에 거슬리는 것은 대개 **피부의 알갱이**입니다. 피부는
    원래 매끄러워서 세게 지워도 잃을 것이 없지만, 같은 강도를 화면 전체에
    걸면 옷의 짜임·머리카락·객석 조명이 함께 뭉갭니다.

    0이면 화면 전체에 같은 강도(예전 동작), 100이면 얼굴 밖은 아예 건드리지
    않습니다.

    실측 (DSC03360, A6700 ISO3200, RAW 디모자이크 6240×4168, 노이즈 감소 70):

        얼굴 우선    피부 노이즈    배경 디테일    시간
             0        -39%         -20%      1.37초
            50        -36%         -13%      1.33초
            85        -33%          -6%      1.32초
           100        -34%          -2%      0.75초

    기본값 85는 '주로 얼굴'이라는 뜻 그대로입니다. 100이 배경 디테일에는
    더 좋고 두 배 빠르지만(얼굴 상자 밖을 아예 계산하지 않으므로), 배경에
    노이즈 감소가 **0**이 되어 풍경 한구석에 우연히 얼굴이 하나 잡힌 사진에서
    화면 전체의 노이즈 감소가 사라집니다. 85면 그런 경우에도 배경이 강도의
    15%는 받습니다.

    얼굴이 검출되지 않으면 이 값은 통째로 무시하고 화면 전체에 같은 강도를
    겁니다 — 얼굴을 못 찾았다고 기능이 사라지면 안 됩니다.

    색 노이즈에는 걸지 않습니다. 색 얼룩 제거는 디테일을 거의 해치지 않아
    얼굴만 할 이유가 없고, 배경에만 색 얼룩이 남으면 그게 더 눈에 띕니다.
    """

    def __post_init__(self) -> None:
        """방식이 문자열로 들어와도 enum으로 맞춥니다.

        프리셋 파일은 문자열로 저장되고, 모르는 값이 적힌 파일도 열려야
        합니다 (GeometrySettings.ratio와 같은 이유).
        """
        if not isinstance(self.noise_algorithm, NoiseAlgorithm):
            try:
                object.__setattr__(
                    self, "noise_algorithm", NoiseAlgorithm(self.noise_algorithm)
                )
            except ValueError:
                object.__setattr__(self, "noise_algorithm", NoiseAlgorithm.NLMEANS)

    def is_neutral(self) -> bool:
        """화소를 실제로 건드리는 값이 하나도 없는지.

        방식과 보조 파라미터(디테일 보존·색 반경)는 조정량이 0이면 아무
        일도 하지 않습니다. 이것들만 바뀐 상태가 '보정 있음'으로 표시되면
        패널의 ● 표시와 프리셋 비교가 거짓말을 하게 됩니다.
        """
        return (
            self.sharpen_amount == 0
            and self.noise_reduction == 0
            and self.color_noise_reduction == 0
            and self.destripe == 0
        )


@dataclass(frozen=True)
class HSLBand:
    hue: int = 0
    saturation: int = 0
    luminance: int = 0

    def is_neutral(self) -> bool:
        return self.hue == 0 and self.saturation == 0 and self.luminance == 0


@dataclass(frozen=True)
class HSLSettings:
    """색상 혼합 패널 — 8개 색상대별 색조/채도/광도."""

    bands: dict[str, HSLBand] = field(
        default_factory=lambda: {name: HSLBand() for name in HSL_BANDS}
    )

    def __post_init__(self) -> None:
        """항상 8개 밴드를 다 채워 둡니다.

        일부만 지정해서 만들 수 있게 두면 같은 의미의 설정이 서로 다른
        객체가 되어 비교와 프리셋 왕복이 어긋납니다.
        """
        normalized = {name: HSLBand() for name in HSL_BANDS}
        normalized.update(
            {k: v for k, v in (self.bands or {}).items() if k in HSL_BANDS}
        )
        object.__setattr__(self, "bands", normalized)

    def is_neutral(self) -> bool:
        return all(band.is_neutral() for band in self.bands.values())

    def to_dict(self) -> dict:
        return {name: asdict(band) for name, band in self.bands.items()}

    @classmethod
    def from_dict(cls, data: dict) -> "HSLSettings":
        bands = {name: HSLBand() for name in HSL_BANDS}
        for name, values in _as_dict(data).items():
            if name in bands and isinstance(values, dict):
                bands[name] = _merge_known(HSLBand, values, asdict(HSLBand()))
        return cls(bands=bands)


@dataclass(frozen=True)
class ColorGradeZone:
    """색 보정의 한 구간 (어두운/중간/밝은 영역)."""

    hue: int = 0          # 0~359
    saturation: int = 0   # 0~100
    luminance: int = 0    # -100~100

    def is_neutral(self) -> bool:
        return self.saturation == 0 and self.luminance == 0


@dataclass(frozen=True)
class ColorGradeSettings:
    """색 보정 패널 — 구간별 컬러 그레이딩."""

    shadows: ColorGradeZone = field(default_factory=ColorGradeZone)
    midtones: ColorGradeZone = field(default_factory=ColorGradeZone)
    highlights: ColorGradeZone = field(default_factory=ColorGradeZone)
    blending: int = 50
    balance: int = 0

    def is_neutral(self) -> bool:
        return (
            self.shadows.is_neutral()
            and self.midtones.is_neutral()
            and self.highlights.is_neutral()
        )


@dataclass(frozen=True)
class EffectSettings:
    """효과 패널 — 그레인과 비네팅."""

    grain_amount: int = 0    # 0~100
    grain_size: int = 25     # 1~100
    vignette_amount: int = 0  # -100(어둡게) ~ +100(밝게)
    vignette_midpoint: int = 50


class CropRatio(str, Enum):
    FREE = "free"
    ORIGINAL = "original"
    SQUARE = "1:1"
    FOUR_THREE = "4:3"
    THREE_TWO = "3:2"
    SIXTEEN_NINE = "16:9"

    @property
    def value_ratio(self) -> float | None:
        """가로/세로 비. FREE와 ORIGINAL은 계산 시점에 정해집니다."""
        return {
            CropRatio.SQUARE: 1.0,
            CropRatio.FOUR_THREE: 4 / 3,
            CropRatio.THREE_TWO: 3 / 2,
            CropRatio.SIXTEEN_NINE: 16 / 9,
        }.get(self)


@dataclass(frozen=True)
class GeometrySettings:
    """도형 패널 — 크롭, 수평 보정, 회전.

    크롭은 0~1 정규화 좌표로 저장합니다. 미리보기(축소본)에서 지정한 값이
    원본 해상도에서도 그대로 통해야 하기 때문입니다.
    """

    crop_left: float = 0.0
    crop_top: float = 0.0
    crop_right: float = 1.0
    crop_bottom: float = 1.0
    straighten: float = 0.0    # 도 단위, -45 ~ +45
    rotate_quarters: int = 0   # 90도 단위 회전 (0~3)
    flip_horizontal: bool = False
    flip_vertical: bool = False
    ratio: CropRatio = CropRatio.FREE

    def __post_init__(self) -> None:
        """ratio가 문자열로 들어와도 enum으로 맞춥니다.

        PySide6는 str을 상속한 Enum을 콤보박스 데이터로 저장할 때 평범한
        str로 바꿔 버립니다. 여기서 흡수하지 않으면 .value 접근이 터집니다.
        """
        if not isinstance(self.ratio, CropRatio):
            try:
                object.__setattr__(self, "ratio", CropRatio(self.ratio))
            except ValueError:
                object.__setattr__(self, "ratio", CropRatio.FREE)

    def has_crop(self) -> bool:
        return (
            self.crop_left > 0.0 or self.crop_top > 0.0
            or self.crop_right < 1.0 or self.crop_bottom < 1.0
        )

    def is_neutral(self) -> bool:
        return (
            not self.has_crop()
            and self.straighten == 0.0
            and self.rotate_quarters == 0
            and not self.flip_horizontal
            and not self.flip_vertical
        )


class WatermarkPosition(str, Enum):
    """3×3 정렬 위치. 세밀한 배치는 offset으로 합니다."""

    TOP_LEFT = "top_left"
    TOP_CENTER = "top_center"
    TOP_RIGHT = "top_right"
    MIDDLE_LEFT = "middle_left"
    CENTER = "center"
    MIDDLE_RIGHT = "middle_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_CENTER = "bottom_center"
    BOTTOM_RIGHT = "bottom_right"

    @property
    def anchor(self) -> tuple[float, float]:
        """(가로, 세로) 정렬 비율. 0=왼쪽/위, 0.5=가운데, 1=오른쪽/아래."""
        horizontal = {"left": 0.0, "center": 0.5, "right": 1.0}
        vertical = {"top": 0.0, "middle": 0.5, "bottom": 1.0}
        if self is WatermarkPosition.CENTER:
            return 0.5, 0.5
        parts = self.value.split("_")
        return horizontal[parts[1]], vertical[parts[0]]


@dataclass(frozen=True)
class WatermarkSettings:
    """워터마크 — 텍스트 또는 이미지."""

    enabled: bool = False
    text: str = ""
    image_path: str = ""
    position: WatermarkPosition = WatermarkPosition.BOTTOM_RIGHT
    opacity: int = 70          # 0~100
    scale: int = 5             # 이미지 긴 변 대비 %
    margin: int = 3            # 여백 %

    offset_x: float = 0.0
    """가로 미세조정 (이미지 폭 대비 %). 양수는 오른쪽."""

    offset_y: float = 0.0
    """세로 미세조정 (이미지 높이 대비 %). 양수는 아래쪽."""

    rotation: int = 0
    """워터마크 회전 (도). 대각선 배치용."""

    font_path: str = ""
    """워터마크 글꼴 파일 경로. 비우면 기본 글꼴을 씁니다.

    글꼴 '이름'이 아니라 파일 경로를 저장합니다. 렌더는 PIL이 하는데 PIL은
    파일을 직접 열어야 하고, 이름→파일 매핑은 OS마다 달라 깨지기 쉽습니다.
    """

    color: tuple[int, int, int] = (255, 255, 255)
    shadow: bool = True        # 밝은 배경에서도 읽히게

    def __post_init__(self) -> None:
        """position이 문자열로 들어와도 enum으로 맞춘다 (GeometrySettings와 같은 이유)."""
        if not isinstance(self.position, WatermarkPosition):
            try:
                object.__setattr__(self, "position", WatermarkPosition(self.position))
            except ValueError:
                object.__setattr__(self, "position", WatermarkPosition.BOTTOM_RIGHT)

    def is_active(self) -> bool:
        return self.enabled and bool(self.text or self.image_path)


# 내보낼 때 넣을 수 있는 EXIF 항목. 키는 내부 이름, 값은 표시명.
EXIF_FIELDS = {
    "camera": "카메라 (제조사/모델)",
    "lens": "렌즈",
    "exposure": "노출 (셔터/조리개/ISO)",
    "focal_length": "초점거리",
    "datetime": "촬영 일시",
    "artist": "작가",
    "copyright": "저작권",
    "software": "소프트웨어",
}


@dataclass(frozen=True)
class MetadataSettings:
    """EXIF 삽입 — 선택한 항목만 나갑니다.

    기본은 전부 끔. 사진을 밖으로 내보낼 때 촬영 장비나 시각이 딸려
    나가는 것을 원치 않는 경우가 많으므로, 넣는 쪽을 명시적 선택으로 둡니다.
    """

    enabled: bool = False
    include: tuple[str, ...] = ()
    artist: str = ""
    copyright: str = ""

    def wants(self, key: str) -> bool:
        return self.enabled and key in self.include


@dataclass(frozen=True)
class OpticsSettings:
    """광학 패널 — 렌즈 왜곡, 비네팅, 색수차.

    자동은 lensfun DB 프로필, 수동은 직접 조정입니다. DB에 없는 렌즈가
    흔하므로(실측: 탐론 A069 미등록) 둘 다 필요합니다.
    """

    auto_enabled: bool = False
    auto_distortion: bool = True
    auto_vignetting: bool = True
    auto_chromatic: bool = True

    lens_override: str = ""
    """사용자가 직접 고른 렌즈 이름입니다.

    EXIF 렌즈명이 비어 있거나(어댑터 사용) 데이터베이스 이름과 다를 때
    자동 조회가 실패합니다. 그때 직접 지정할 수 있어야 합니다.
    """

    distortion: int = 0
    manual_vignetting: int = 0
    defringe_purple: int = 0
    defringe_green: int = 0

    defringe_purple_hue: int = 145
    """보라 언저리로 볼 색조 중심입니다. 스포이드로 지정합니다."""

    defringe_green_hue: int = 65
    """녹색 언저리로 볼 색조 중심입니다."""

    def is_neutral(self) -> bool:
        return (
            not self.auto_enabled
            and self.distortion == 0
            and self.manual_vignetting == 0
            and self.defringe_purple == 0
            and self.defringe_green == 0
        )


@dataclass(frozen=True)
class ExifStripSettings:
    """이미지 하단 정보 띠.

    EXIF는 SNS에 올리면 대부분 지워집니다. 화면에 보이는 글자로 박아 두면
    어디로 가든 남습니다.
    """

    enabled: bool = False
    dark_background: bool = True
    include: tuple[str, ...] = (
        "camera", "lens", "focal_length", "aperture", "shutter", "iso",
    )
    height_percent: float = 6.0
    custom_text: str = ""

    def is_active(self) -> bool:
        return self.enabled and bool(self.include or self.custom_text)


# 띠에 넣을 수 있는 항목
STRIP_FIELDS = {
    "filename": "파일명",
    "camera": "카메라",
    "lens": "렌즈",
    "focal_length": "초점거리",
    "aperture": "조리개",
    "shutter": "셔터",
    "iso": "ISO",
    "datetime": "촬영 일시",
}


# ---------------------------------------------------------------- 마스크(국소 보정)


class MaskType(str, Enum):
    """마스크 종류. 얼굴/눈/배경은 이미지에서 매번 재생성됩니다."""

    BRUSH = "brush"          # 손으로 칠한 알파 비트맵
    RADIAL = "radial"        # 타원 그라디언트
    LINEAR = "linear"        # 선형 그라디언트
    FACE = "face"            # 얼굴 인식 (피부/입)
    EYE = "eye"              # 얼굴 랜드마크 기반 (눈밑/눈동자)
    BACKGROUND = "background"  # 인물 제외 배경 (GrabCut)


@dataclass(frozen=True)
class LocalAdjustments:
    """마스크 영역에만 적용하는 조정. BasicSettings의 부분집합 + 국소 전용.

    색온도는 절대 Kelvin이 아니라 상대 이동(-100~+100, 양수는 따뜻하게)입니다.
    국소 보정은 '배경 대비 얼마나 밀지'가 자연스러워 전역과 다르게 다룹니다.
    """

    exposure: float = 0.0      # EV
    contrast: int = 0
    highlights: int = 0
    shadows: int = 0
    whites: int = 0
    blacks: int = 0
    temperature: int = 0       # 상대 이동 (-100 차갑게 ~ +100 따뜻하게)
    tint: int = 0
    texture: int = 0
    clarity: int = 0
    saturation: int = 0
    sharpen: int = 0           # 0~150
    smoothing: int = 0         # 피부 부드럽게 (0~100), surface blur

    def is_neutral(self) -> bool:
        return all(getattr(self, f.name) == 0 for f in fields(self))


@dataclass(frozen=True)
class Mask:
    """마스크 하나 = 영역 정의 + 그 영역에 적용할 국소 조정.

    얼굴/눈/배경/방사형/선형은 params(정규화 좌표)만 저장하고 렌더 시 다시
    만든다 — 해상도가 달라도 같은 위치가 나옵니다. 브러시만 bitmap(축소된
    알파 PNG를 base64로)을 직접 들고 다닙니다.
    """

    kind: MaskType
    adjust: LocalAdjustments = field(default_factory=LocalAdjustments)
    enabled: bool = True
    invert: bool = False
    opacity: int = 100         # 0~100
    feather: int = 50          # 경계 부드러움 0~100
    size: int = 100
    """인식 영역 크기 (%, 0~200). 100이 기본입니다.

    얼굴/눈/방사형처럼 도형으로 만드는 영역에만 적용됩니다. 눈밑처럼 좁게
    잡아야 하는 부위는 사람마다 적정 범위가 달라서 조절이 필요합니다.
    """
    params: dict[str, Any] = field(default_factory=dict)
    """종류별 정규화 파라미터.
      RADIAL:  cx, cy, rx, ry, rotation
      LINEAR:  x0, y0, x1, y1
      FACE:    index(몇 번째 얼굴), region("skin"|"mouth")
      EYE:     index, region("under_eye"|"iris")
    """
    bitmap: str = ""           # BRUSH 전용, base64 PNG(단일 채널, ≈512px)
    label: str = ""            # 사용자에게 보일 이름

    def __post_init__(self) -> None:
        if not isinstance(self.kind, MaskType):
            try:
                object.__setattr__(self, "kind", MaskType(self.kind))
            except ValueError:
                object.__setattr__(self, "kind", MaskType.RADIAL)

    def is_neutral(self) -> bool:
        return not self.enabled or self.opacity <= 0 or self.adjust.is_neutral()

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "adjust": asdict(self.adjust),
            "enabled": self.enabled,
            "invert": self.invert,
            "opacity": self.opacity,
            "feather": self.feather,
            "size": self.size,
            "params": dict(self.params),
            "bitmap": self.bitmap,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Mask | None":
        if not isinstance(data, dict) or "kind" not in data:
            return None
        try:
            kind = MaskType(data["kind"])
        except ValueError:
            return None
        adjust = _merge_known(LocalAdjustments, data.get("adjust"), asdict(LocalAdjustments()))
        params = data.get("params")
        # 수치는 전부 관대하게 읽습니다. 여기서 int()가 터지면 이 마스크만이
        # 아니라 프리셋(그리고 대기열) 전체가 열리지 않습니다.
        return cls(
            kind=kind,
            adjust=adjust,
            enabled=bool(data.get("enabled", True)),
            invert=bool(data.get("invert", False)),
            opacity=_as_int(data.get("opacity"), 100),
            feather=_as_int(data.get("feather"), 50),
            size=_as_int(data.get("size"), 100),
            params=dict(params) if isinstance(params, dict) else {},
            bitmap=str(data.get("bitmap", "")),
            label=str(data.get("label", "")),
        )


@dataclass(frozen=True)
class DevelopSettings:
    """보정 설정 전체."""

    basic: BasicSettings = field(default_factory=BasicSettings)
    curve: CurveSettings = field(default_factory=CurveSettings)
    detail: DetailSettings = field(default_factory=DetailSettings)
    hsl: HSLSettings = field(default_factory=HSLSettings)
    color_grade: ColorGradeSettings = field(default_factory=ColorGradeSettings)
    effects: EffectSettings = field(default_factory=EffectSettings)
    optics: OpticsSettings = field(default_factory=OpticsSettings)
    geometry: GeometrySettings = field(default_factory=GeometrySettings)
    watermark: WatermarkSettings = field(default_factory=WatermarkSettings)
    metadata: MetadataSettings = field(default_factory=MetadataSettings)
    exif_strip: ExifStripSettings = field(default_factory=ExifStripSettings)
    masks: tuple[Mask, ...] = ()
    """국소 보정 마스크들. 컷마다 다르므로(크롭과 같은 성격) 일괄 적용에서 제외됩니다."""

    def is_neutral(self) -> bool:
        """아무것도 바꾸지 않은 상태인지.

        워터마크·메타데이터·정보 띠는 픽셀 연산이 아니어도 출력에 영향을
        주므로 함께 봅니다.
        """
        return (
            self.basic == BasicSettings()
            and self.curve.is_neutral()
            and self.detail.is_neutral()
            and self.hsl.is_neutral()
            and self.color_grade.is_neutral()
            and self.effects == EffectSettings()
            and self.optics.is_neutral()
            and self.geometry.is_neutral()
            and not self.watermark.is_active()
            and not self.metadata.enabled
            and not self.exif_strip.is_active()
            and all(m.is_neutral() for m in self.masks)
        )

    # ------------------------------------------------------------ 직렬화

    def to_dict(self) -> dict:
        return {
            "basic": asdict(self.basic),
            "curve": _curve_to_dict(self.curve),
            # 프리셋은 YAML로 저장됩니다. safe_dump는 Enum을 표현하지 못하므로
            # (CropRatio·WatermarkPosition과 같은 이유) 문자열로 풀어 둡니다.
            "detail": {
                **asdict(self.detail),
                "noise_algorithm": self.detail.noise_algorithm.value,
            },
            "hsl": self.hsl.to_dict(),
            "color_grade": {
                "shadows": asdict(self.color_grade.shadows),
                "midtones": asdict(self.color_grade.midtones),
                "highlights": asdict(self.color_grade.highlights),
                "blending": self.color_grade.blending,
                "balance": self.color_grade.balance,
            },
            "effects": asdict(self.effects),
            "optics": asdict(self.optics),
            "geometry": {**asdict(self.geometry), "ratio": self.geometry.ratio.value},
            "watermark": {
                **asdict(self.watermark),
                "position": self.watermark.position.value,
                "color": list(self.watermark.color),
            },
            "metadata": {
                **asdict(self.metadata),
                "include": list(self.metadata.include),
            },
            "exif_strip": {
                **asdict(self.exif_strip),
                "include": list(self.exif_strip.include),
            },
            "masks": [m.to_dict() for m in self.masks],
        }

    def without_geometry(self) -> "DevelopSettings":
        """도형(크롭·기울이기·회전)과 마스크를 뺀 사본. 일괄 적용에 씁니다.

        크롭은 컷마다 구도가 달라서 일괄 적용하면 안 됩니다. 한 장에서 잡은
        크롭을 다른 장에 그대로 씌우면 피사체가 잘려 나갑니다. 마스크도
        마찬가지로 그 컷의 얼굴·구도에 맞춰 만든 것이라 공유하면 안 됩니다.
        색보정처럼 전체에 공유해도 되는 것과는 성격이 다릅니다.
        """
        from dataclasses import replace

        return replace(self, geometry=GeometrySettings(), masks=())

    @classmethod
    def from_dict(cls, data: Any) -> "DevelopSettings":
        """모르는 키는 무시합니다. 예전 프리셋도 열려야 합니다.

        손상된 파일에서 dict가 아닌 값이 들어와도 기본값으로 넘어갑니다.
        """
        if not isinstance(data, dict):
            data = {}
        return cls(
            basic=_merge_known(BasicSettings, data.get("basic"), asdict(BasicSettings())),
            curve=_curve_from_dict(data.get("curve")),
            detail=_detail_from_dict(data.get("detail")),
            hsl=HSLSettings.from_dict(data.get("hsl")),
            color_grade=_color_grade_from_dict(data.get("color_grade")),
            effects=_merge_known(EffectSettings, data.get("effects"), asdict(EffectSettings())),
            optics=_merge_known(OpticsSettings, data.get("optics"), asdict(OpticsSettings())),
            geometry=_geometry_from_dict(data.get("geometry")),
            watermark=_watermark_from_dict(data.get("watermark")),
            metadata=_metadata_from_dict(data.get("metadata")),
            exif_strip=_exif_strip_from_dict(data.get("exif_strip")),
            masks=_masks_from_dict(data.get("masks")),
        )


def _curve_to_dict(curve: CurveSettings) -> dict:
    return {
        "highlights": curve.highlights,
        "lights": curve.lights,
        "darks": curve.darks,
        "shadows": curve.shadows,
        "points_rgb": [list(p) for p in curve.points_rgb],
        "points_red": [list(p) for p in curve.points_red],
        "points_green": [list(p) for p in curve.points_green],
        "points_blue": [list(p) for p in curve.points_blue],
    }


def _curve_from_dict(data: dict | None) -> CurveSettings:
    data = _as_dict(data)

    def points(key: str) -> tuple[tuple[int, int], ...]:
        raw = data.get(key) or []
        try:
            return tuple((int(a), int(b)) for a, b in raw)
        except (TypeError, ValueError):
            return ()

    return CurveSettings(
        highlights=_as_int(data.get("highlights"), 0),
        lights=_as_int(data.get("lights"), 0),
        darks=_as_int(data.get("darks"), 0),
        shadows=_as_int(data.get("shadows"), 0),
        points_rgb=points("points_rgb"),
        points_red=points("points_red"),
        points_green=points("points_green"),
        points_blue=points("points_blue"),
    )


def _detail_from_dict(data: dict | None) -> DetailSettings:
    """세부 설정을 복원합니다.

    노이즈 감소 방식이 없는 예전 프리셋은 기본값(비국소 평균)으로 열립니다.
    예전 결과를 그대로 재현해야 하면 방식을 '기존 방식'으로 바꾸면 됩니다.
    """
    data = _as_dict(data)
    algorithm = data.pop("noise_algorithm", NoiseAlgorithm.NLMEANS)
    merged = _merge_known(DetailSettings, data, asdict(DetailSettings()))
    try:
        resolved = NoiseAlgorithm(algorithm)
    except ValueError:
        resolved = NoiseAlgorithm.NLMEANS
    return DetailSettings(**{**asdict(merged), "noise_algorithm": resolved})


def _color_grade_from_dict(data: dict | None) -> ColorGradeSettings:
    data = _as_dict(data)
    base = asdict(ColorGradeZone())
    return ColorGradeSettings(
        shadows=_merge_known(ColorGradeZone, data.get("shadows"), base),
        midtones=_merge_known(ColorGradeZone, data.get("midtones"), base),
        highlights=_merge_known(ColorGradeZone, data.get("highlights"), base),
        blending=_as_int(data.get("blending"), 50),
        balance=_as_int(data.get("balance"), 0),
    )


def _geometry_from_dict(data: dict | None) -> GeometrySettings:
    data = _as_dict(data)
    ratio = data.pop("ratio", CropRatio.FREE.value)
    merged = _merge_known(GeometrySettings, data, asdict(GeometrySettings()))
    try:
        return GeometrySettings(**{**asdict(merged), "ratio": CropRatio(ratio)})
    except ValueError:
        return merged


_DEFAULT_WATERMARK_COLOR = (255, 255, 255)


def _as_color(value: Any) -> tuple[int, int, int]:
    """워터마크 색을 항상 세 채널로 맞춥니다.

    cv2.putText는 채널 수가 맞지 않으면 **그리는 시점에** 터집니다. 불러올
    때 조용히 넘어가면 사용자는 몇십 분 걸린 배치가 끝날 때쯤 실패를 봅니다.
    """
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return _DEFAULT_WATERMARK_COLOR
    channels = []
    for component in value[:3]:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            return _DEFAULT_WATERMARK_COLOR
        channels.append(int(min(255, max(0, component))))
    return (channels[0], channels[1], channels[2])


def _watermark_from_dict(data: dict | None) -> WatermarkSettings:
    data = _as_dict(data)
    position = data.pop("position", WatermarkPosition.BOTTOM_RIGHT.value)
    color = data.pop("color", _DEFAULT_WATERMARK_COLOR)
    merged = _merge_known(WatermarkSettings, data, asdict(WatermarkSettings()))
    try:
        resolved = WatermarkPosition(position)
    except (ValueError, TypeError):
        resolved = WatermarkPosition.BOTTOM_RIGHT
    return WatermarkSettings(
        **{
            **asdict(merged),
            "position": resolved,
            "color": _as_color(color),
        }
    )


def _exif_strip_from_dict(data: dict | None) -> ExifStripSettings:
    data = _as_dict(data)
    include = data.pop("include", None)
    merged = _merge_known(ExifStripSettings, data, asdict(ExifStripSettings()))
    if include is None:
        return merged
    return ExifStripSettings(
        **{**asdict(merged), "include": _as_key_tuple(include, STRIP_FIELDS)}
    )


def _masks_from_dict(data: Any) -> tuple[Mask, ...]:
    """마스크 리스트를 복원합니다. 깨진 항목은 조용히 건너뜁니다."""
    if not isinstance(data, (list, tuple)):
        return ()
    masks = []
    for item in data:
        mask = Mask.from_dict(item)
        if mask is not None:
            masks.append(mask)
    return tuple(masks)


def _metadata_from_dict(data: dict | None) -> MetadataSettings:
    data = _as_dict(data)
    include = data.pop("include", ())
    merged = _merge_known(MetadataSettings, data, asdict(MetadataSettings()))
    return MetadataSettings(
        **{**asdict(merged), "include": _as_key_tuple(include, EXIF_FIELDS)}
    )
