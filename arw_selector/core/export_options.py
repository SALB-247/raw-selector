"""내보내기 옵션.

같은 셀렉트 결과라도 목적에 따라 필요한 파일이 다르다 — 인쇄용 풀사이즈,
SNS용 긴 변 2048px, 클라이언트 확인용 워터마크 저용량.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from enum import Enum
from pathlib import Path

from .types import Grade, ImageRecord

ALL_GRADES: tuple[str, ...] = tuple(grade.value for grade in Grade)


class ExportFormat(str, Enum):
    """현상 결과를 저장할 형식.

    HEIF/AVIF는 **넣을 수 없습니다.** 이 OpenCV 빌드(5.0.0 pip 휠)에
    인코더가 들어 있지 않아 `cv2.imwrite('x.heic', …)`가 예외를 던집니다
    (실측). 넣으려면 pillow-heif 같은 의존성을 새로 들여야 합니다.
    RAW 옆의 .HIF 원본을 **그대로 복사**하는 것은 include_companions로
    이미 됩니다 — 그쪽은 인코더가 필요 없습니다.
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
        """색 프로파일을 **화소를 건드리지 않고** 넣을 수 있는 형식인가.

        WebP만 빠집니다 — ICC를 넣으려면 RIFF를 확장 형식(VP8X)으로 바꿔야
        하는데, 그 손이 이 형식의 쓰임에 비해 큽니다. 다시 저장하는 방법도
        있지만 재압축이라 화질이 떨어집니다(실측: JPEG을 PIL로 다시 저장하면
        14,880 → 6,895바이트, 화소 최대 14레벨 차이).
        """
        return self in (ExportFormat.JPEG, ExportFormat.PNG, ExportFormat.TIFF)

    @property
    def supports_16bit(self) -> bool:
        """16비트로 저장할 수 있는 형식인가.

        **JPEG·WebP에 uint16을 주면 조용히 8비트로 떨어집니다** — cv2가
        경고만 남기고 진행합니다(실측: "Unsupported depth ... fallbacked
        to CV_8U"). 화면에서 미리 잠가야 "16비트로 저장했는데 8비트가
        나온다"가 되지 않습니다.
        """
        return self in (ExportFormat.PNG, ExportFormat.TIFF)


class ExportColorSpace(str, Enum):
    """내보낼 파일의 색공간.

    **태그만 붙이는 것이 아니라 화소도 변환합니다** — 하나만 하면 뷰어가
    다른 공간의 숫자로 읽어 색이 틀어집니다(core/develop/icc.py).

    sRGB는 화면·SNS 전달의 기본값입니다. Adobe RGB는 초록·시안 쪽이 넓어
    인쇄 워크플로에서 쓰입니다 — 대신 색 관리를 하지 않는 뷰어에 올리면
    채도가 빠져 보이므로, 그쪽으로 보낼 파일이 아니면 sRGB가 안전합니다.
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
    """내보내기 동작 전체."""

    move: bool = False

    include_companions: bool = False
    """RAW 옆에 함께 저장된 JPG/HIF/XMP도 같이 내보낼지.

    기본을 끕니다. RAW+HEIF로 찍으면 컷마다 파일이 두 배로 늘어나는데,
    셀렉 결과로는 RAW만 필요한 경우가 대부분입니다. 필요한 사람이 켜는 편이
    모르는 사이에 용량이 두 배가 되는 것보다 낫습니다.
    """

    apply_develop: bool = True

    grades: tuple[str, ...] = ALL_GRADES
    """내보낼 등급. ("keep",)이면 keep만 나갑니다.

    셀렉 결과를 넘길 때는 보통 keep만 필요하지만, 백업은 전부 필요합니다.
    이동 모드와 조합하면 "reject만 다른 폴더로 치우기"도 됩니다.
    """

    copy_raw: bool = True
    """원본 RAW를 함께 내보낼지. 끄면 현상된 이미지만 나갑니다."""

    image_format: ExportFormat = ExportFormat.JPEG
    quality: int = 95

    bit_depth: int = 8
    """저장 비트 심도. PNG·TIFF만 16을 받습니다(supports_16bit 참고).

    파이프라인은 원래 float32로 흐르므로 16을 골라도 계산이 달라지지 않고,
    마지막 양자화만 바뀝니다. 실측으로 계조가 실제 보존되는 것을 확인했습니다
    — 디모자이크 직후 채널당 고유 레벨 544만, 각 보정 단계 통과 후에도
    477만~604만(8비트 상한은 256).
    """

    color_space: ExportColorSpace = ExportColorSpace.SRGB
    """내보낼 파일의 색공간. 화소 변환 + ICC 임베드가 함께 갑니다.

    WebP는 ICC를 넣을 수 없어(supports_icc) sRGB로 되돌립니다 — 변환만
    하고 태그를 못 붙이면 뷰어가 sRGB로 읽어 **색이 틀어진 파일**이
    나갑니다. 그럴 바에는 변환을 안 하는 것이 맞습니다.
    """

    resize_mode: ResizeMode = ResizeMode.NONE
    resize_long_edge: int = 2048
    resize_percent: int = 50

    filename_pattern: str = "{name}"
    """파일명 규칙. {name} {index} {grade} {date} {score} 를 쓸 수 있습니다."""

    subfolder_by_grade: bool = True
    """끄면 등급 폴더를 만들지 않고 한곳에 모읍니다."""

    subfolder_by_place: bool = False
    """GPS 위치가 같은 컷끼리 장소 폴더로 나눌지 (core/places.py).

    기본을 끕니다. 바디에 GPS가 없으면 좌표가 아예 안 들어가서, 켜 두면
    전부 `_위치없음` 한 폴더로 들어가 폴더만 하나 더 생깁니다
    (실측: A6700 300장 중 GPS 있는 파일 0장). 위치가 있는 배치에서만
    의미가 있습니다.

    장소가 바깥, 등급이 안쪽입니다 — 반대로 하면 같은 장소의 keep과 review가
    멀리 떨어져 "이 장소 결과"를 한눈에 볼 수 없습니다.
    """

    def __post_init__(self) -> None:
        # 문자열로 들어와도 enum으로 맞춘다 (위젯 데이터 왕복 대비)
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

        # 형식이 못 받는 심도·색공간은 여기서 되돌립니다. 대기열에 담아 둔 뒤
        # 형식만 JPEG으로 바꾸는 경로가 있어, 화면 잠금만으로는 새지 않는다고
        # 보장할 수 없습니다.
        self.bit_depth = 16 if self.bit_depth == 16 else 8
        if self.bit_depth == 16 and not self.image_format.supports_16bit:
            self.bit_depth = 8
        if not self.image_format.supports_icc:
            self.color_space = ExportColorSpace.SRGB

        # 등급 목록은 문자열 하나로 들어오거나 모르는 값이 섞일 수 있습니다.
        # 전부 걸러내 비면 '전체'로 되돌립니다 — 아무것도 안 나가는 것보다 낫습니다.
        selected = self.grades
        if isinstance(selected, str):
            selected = (selected,)
        cleaned = tuple(g for g in (selected or ()) if g in ALL_GRADES)
        self.grades = cleaned or ALL_GRADES

    def wants_grade(self, grade) -> bool:
        """이 등급을 내보낼지."""
        return getattr(grade, "value", grade) in self.grades

    def target_long_edge(self, source_long_edge: int | None = None) -> int | None:
        """현상 시 적용할 긴 변 픽셀. None이면 원본 크기."""
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


def format_filename(
    pattern: str, record: ImageRecord, index: int, suffix: str
) -> str:
    """규칙에 따라 파일명을 만듭니다.

    알 수 없는 치환자는 그대로 둔다 — 조용히 지우면 사용자가 오타를
    알아차리지 못합니다.
    """
    capture = None
    if record.metadata and record.metadata.capture_time:
        capture = record.metadata.capture_time

    values = {
        "name": record.path.stem,
        "index": f"{index:04d}",
        "grade": record.final_grade.value,
        "date": (capture or datetime.now()).strftime("%Y%m%d"),
        "time": (capture or datetime.now()).strftime("%H%M%S"),
        "score": f"{record.score:.0f}",
    }

    result = pattern
    for key, value in values.items():
        result = result.replace("{" + key + "}", str(value))

    # 앞뒤의 점과 공백을 떼어냅니다. 점으로 시작하면 이름 없는 숨김 파일이
    # 되어(패턴 "."이면 결과가 그냥 ".jpg") 탐색기에서 보이지 않고, 끝의
    # 점·공백은 Windows가 파일을 만들 때 말없이 잘라내 우리가 검사한 이름과
    # 실제 이름이 어긋납니다. 가운데 점은 그대로 둡니다 — 사용자가 쓴 것입니다.
    result = _INVALID_NAME.sub("_", result).strip(" .")
    if not result:
        result = _INVALID_NAME.sub("_", record.path.stem).strip(" .")
    return f"{result or '이름없음'}{suffix}"
