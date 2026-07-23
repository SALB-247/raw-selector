"""국소 보정 원클릭 프리셋.

마스크 영역 정의 + 그 영역 조정을 한 벌로 묶은 레시피입니다. 사용자가 버튼
하나로 마스크를 추가하고, 이후 세기·위치를 다듬을 수 있습니다. 인물 리터치가
1순위지만(눈가주름) 배경·조명 계열도 함께 제공합니다.

얼굴/눈/배경 프리셋은 적용 시점의 이미지에서 영역을 재검출하므로, 얼굴이
없으면 마스크가 만들어지지 않습니다(호출부에서 안내).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .settings import LocalAdjustments, Mask, MaskType


@dataclass(frozen=True)
class MaskPreset:
    key: str
    label: str
    group: str          # 목록 묶음: 인물 / 배경 / 조명·하늘
    description: str
    template: Mask

    def build(self) -> Mask:
        """새 Mask 인스턴스. params dict는 매번 복사해 공유를 피합니다."""
        return replace(self.template, params=dict(self.template.params), label=self.label)


def _mask(kind, adjust, *, feather=50, params=None, invert=False, opacity=100) -> Mask:
    return Mask(
        kind=kind, adjust=adjust, feather=feather,
        params=params or {}, invert=invert, opacity=opacity,
    )


# 인물 ---------------------------------------------------------------------
_PORTRAIT = [
    MaskPreset(
        "under_eye", "언더아이 리터치", "인물",
        "눈밑 주름·다크서클을 은은하게 펴고 아주 살짝 밝힙니다.",
        _mask(MaskType.EYE,
              LocalAdjustments(smoothing=22, texture=-16, shadows=10, exposure=0.05),
              feather=60, params={"region": "under_eye", "index": 0}),
    ),
    MaskPreset(
        "skin_smooth", "피부 매끄럽게", "인물",
        "얼굴 전체 피부를 부드럽게. 질감은 살짝 낮춥니다.",
        _mask(MaskType.FACE,
              LocalAdjustments(smoothing=38, texture=-22, clarity=-8),
              feather=55, params={"region": "skin", "index": 0}),
    ),
    MaskPreset(
        "eye_pop", "눈동자 또렷하게", "인물",
        "눈동자에 명료도·샤픈을 더해 시선을 살립니다.",
        _mask(MaskType.EYE,
              LocalAdjustments(clarity=32, sharpen=28, exposure=0.15, saturation=10),
              feather=45, params={"region": "iris", "index": 0}),
    ),
    MaskPreset(
        "teeth_white", "치아 화이트닝", "인물",
        "치아의 노란기를 빼고 살짝 밝힙니다. 입을 벌린 컷에만 효과가 있습니다.",
        _mask(MaskType.FACE,
              LocalAdjustments(temperature=-30, saturation=-35, exposure=0.15),
              feather=45, params={"region": "teeth", "index": 0}),
    ),
    MaskPreset(
        "face_brighten", "얼굴 밝히기", "인물",
        "역광·그늘로 어두운 얼굴을 끌어올립니다.",
        _mask(MaskType.FACE,
              LocalAdjustments(exposure=0.35, shadows=18),
              feather=60, params={"region": "skin", "index": 0}),
    ),
]

# 배경 ---------------------------------------------------------------------
_BACKGROUND = [
    MaskPreset(
        "subject_pop", "인물 강조 (배경 어둡게)", "배경",
        "배경을 어둡게·덜 진하게 눌러 인물을 도드라지게.",
        _mask(MaskType.BACKGROUND,
              LocalAdjustments(exposure=-0.55, saturation=-18, contrast=-6),
              feather=50),
    ),
    MaskPreset(
        "bg_blur", "배경 흐리게 (아웃포커스)", "배경",
        "배경만 부드럽게 흐려 얕은 심도 느낌을 냅니다.",
        _mask(MaskType.BACKGROUND,
              LocalAdjustments(smoothing=78, clarity=-20),
              feather=45),
    ),
]

# 조명·하늘 ----------------------------------------------------------------
_LIGHT = [
    MaskPreset(
        "sky_boost", "하늘 파랗게", "조명·하늘",
        "위쪽 선형 마스크로 하늘을 더 파랗고 진하게.",
        _mask(MaskType.LINEAR,
              LocalAdjustments(temperature=-28, saturation=22, clarity=12),
              params={"x0": 0.5, "y0": 0.0, "x1": 0.5, "y1": 0.45}),
    ),
    MaskPreset(
        "spotlight", "스포트라이트 (주변 어둡게)", "조명·하늘",
        "가운데 원형 밖을 어둡게 눌러 시선을 모읍니다.",
        _mask(MaskType.RADIAL,
              LocalAdjustments(exposure=-0.55),
              invert=True, params={"cx": 0.5, "cy": 0.45, "rx": 0.33, "ry": 0.4}),
    ),
    MaskPreset(
        "dodge", "부분 밝게 (원형)", "조명·하늘",
        "원형 마스크로 원하는 곳만 밝힙니다. 위치·크기는 이후 조정.",
        _mask(MaskType.RADIAL,
              LocalAdjustments(exposure=0.5),
              params={"cx": 0.5, "cy": 0.5, "rx": 0.25, "ry": 0.25}),
    ),
    MaskPreset(
        "burn", "부분 어둡게 (원형)", "조명·하늘",
        "원형 마스크로 원하는 곳만 어둡게.",
        _mask(MaskType.RADIAL,
              LocalAdjustments(exposure=-0.5),
              params={"cx": 0.5, "cy": 0.5, "rx": 0.25, "ry": 0.25}),
    ),
]

MASK_PRESETS: list[MaskPreset] = _PORTRAIT + _BACKGROUND + _LIGHT
_BY_KEY = {preset.key: preset for preset in MASK_PRESETS}


def build_mask(key: str) -> Mask | None:
    preset = _BY_KEY.get(key)
    return preset.build() if preset else None
