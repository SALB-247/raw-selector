"""One-click presets for local adjustments.

A recipe that ties the mask region definition and the adjustment for that
region into one set. The user adds a mask with one button and can then
refine the strength and the position. Portrait retouching is the first
priority (eye wrinkles), but background and lighting families are provided
alongside.

The face/eye/background presets re-detect the region on the image as it is
at the moment they are applied, so with no face no mask is made (the caller
explains this).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .settings import LocalAdjustments, Mask, MaskType


@dataclass(frozen=True)
class MaskPreset:
    key: str
    label: str
    group: str          # list group: portrait / background / light and sky
    description: str
    template: Mask

    def build(self) -> Mask:
        """A new Mask instance. The params dict is copied every time to
        avoid sharing."""
        return replace(self.template, params=dict(self.template.params), label=self.label)


def _mask(kind, adjust, *, feather=50, params=None, invert=False, opacity=100) -> Mask:
    return Mask(
        kind=kind, adjust=adjust, feather=feather,
        params=params or {}, invert=invert, opacity=opacity,
    )


# portrait -----------------------------------------------------------------
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

# background ---------------------------------------------------------------
_BACKGROUND = [
    MaskPreset(
        "subject_pop", "인물 강조 (배경 어둡게)", "배경",
        "배경을 어둡게·덜 진하게 눌러 인물을 도드라지게.",
        _mask(MaskType.BACKGROUND,
              LocalAdjustments(exposure=-0.55, saturation=-18, contrast=-6),
              feather=50),
    ),
    MaskPreset(
        "subject_lift", "주 피사체 살리기", "배경",
        "인식한 피사체만 밝히고 또렷하게. 경계가 고와 머리카락까지 살립니다.",
        _mask(MaskType.SUBJECT,
              LocalAdjustments(exposure=0.3, clarity=14, saturation=6),
              feather=25),
    ),
    MaskPreset(
        "subject_pop_ai", "인물 강조 (정밀)", "배경",
        "위 '인물 강조'와 같은 효과를 인식 모델로 — 경계가 더 정확합니다.",
        _mask(MaskType.SUBJECT,
              LocalAdjustments(exposure=-0.55, saturation=-18, contrast=-6),
              invert=True, feather=25),
    ),
    MaskPreset(
        "bg_blur", "배경 흐리게 (아웃포커스)", "배경",
        "배경만 부드럽게 흐려 얕은 심도 느낌을 냅니다.",
        _mask(MaskType.BACKGROUND,
              LocalAdjustments(smoothing=78, clarity=-20),
              feather=45),
    ),
]

# light and sky -------------------------------------------------------------
_LIGHT = [
    MaskPreset(
        "sky_boost", "하늘 파랗게", "조명·하늘",
        "위쪽 선형 마스크로 하늘을 더 파랗고 진하게.",
        # The linear alpha is 0 at (x0,y0) and 1 at (x1,y1). The sky has to
        # be 1 at the **top**, so the start point is at the bottom (0.45)
        # and the end point is right at the top (0.0). It used to be
        # written the other way round, so the sky_boost preset turned the
        # bottom 55% blue (measured: alpha 0.00 at the top, 1.00 at the
        # bottom).
        _mask(MaskType.LINEAR,
              LocalAdjustments(temperature=-28, saturation=22, clarity=12),
              params={"x0": 0.5, "y0": 0.45, "x1": 0.5, "y1": 0.0}),
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
