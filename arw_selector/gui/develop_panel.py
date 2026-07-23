"""보정 패널.

Lightroom / Camera Raw의 패널 구성을 따른다 — 사용자가 이미 익숙한 이름과
배치를 쓰는 편이 배우기 쉽습니다. 섹션은 접이식이라 필요한 것만 펴 놓고 씁니다.

값이 바뀌면 settings_changed를 쏘고, 미리보기가 다시 그려집니다.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QButtonGroup,
    QComboBox,
    QSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.develop import (
    EXIF_FIELDS,
    HSL_BAND_CENTERS,
    HSL_BAND_LABELS,
    HSL_BANDS,
    NOISE_ALGORITHM_LABELS,
    STRIP_FIELDS,
    BasicSettings,
    ColorGradeSettings,
    ColorGradeZone,
    CropRatio,
    CurveSettings,
    DetailSettings,
    DevelopSettings,
    EffectSettings,
    ExifStripSettings,
    GeometrySettings,
    HSLBand,
    HSLSettings,
    LocalAdjustments,
    Mask,
    MaskType,
    MetadataSettings,
    NoiseAlgorithm,
    OpticsSettings,
    WatermarkPosition,
    WatermarkSettings,
)
from ..core.develop.mask_presets import MASK_PRESETS, build_mask
from ..core.presets import develop_presets
from .color_wheel import ColorGradeZoneWidget
from .curve_editor import CurveEditor
from .preset_bar import PresetBar
from .widgets import (
    CollapsibleSection,
    SliderRow,
    disable_wheel_in,
    hsl_band_colors,
    temperature_track_colors,
)
from . import theme
from .i18n import tr

RATIO_LABELS = {
    CropRatio.FREE: "자유",
    CropRatio.ORIGINAL: "원본 비율",
    CropRatio.SQUARE: "1:1",
    CropRatio.FOUR_THREE: "4:3",
    CropRatio.THREE_TWO: "3:2",
    CropRatio.SIXTEEN_NINE: "16:9",
}

# 색온도 슬라이더의 기본 표시값. as-shot을 모를 때만 쓰는 폴백입니다.
DEFAULT_KELVIN = 5500
KELVIN_MIN, KELVIN_MAX = 2000, 12000

PANEL_MIN_CHARS = 46
"""보정 패널 최소 폭 (글자 수 기준).

픽셀로 박아 두면 폰트 크기나 DPI가 달라질 때 잘립니다. 한글 라벨 + 값 입력칸
+ 리셋 버튼이 한 줄에 들어가는 데 필요한 글자 수로 잡고, 실제 폭은 폰트
메트릭에서 계산합니다.

폭 자체는 **고정하지 않습니다**. 최소만 지키고 나머지는 스플리터가 정하므로,
섹션에 위젯을 추가해 내용이 넓어져도 잘리는 대신 사용자가 넓힐 수 있습니다.
"""

# 곡선 위 버튼(클리핑/초기화) 공통 스타일
_CURVE_BUTTON_STYLE = (
    "QPushButton { background: #2f2f35; color: #ccc; border: 1px solid #444;"
    " border-radius: 3px; font-size: 11px; }"
    "QPushButton:hover { background: #3a3a42; }"
    "QPushButton:checked { background: #4a5a75; color: #fff; border-color: #5a7bb0; }"
)


def _curve_channel_style(color: str) -> str:
    """채널 버튼(밝기/R/G/B) — 선택되면 그 채널 색으로 채웁니다."""
    return (
        "QPushButton { background: #2f2f35; color: #aaa; border: 1px solid #444;"
        " border-radius: 3px; font-size: 11px; font-weight: bold; }"
        "QPushButton:hover { background: #3a3a42; }"
        f"QPushButton:checked {{ background: {color}; color: #16161a;"
        f" border-color: {color}; }}"
    )

# 슬라이더 한 줄이 어느 설정 필드에 대응하는지.
#
# 색온도(basic.temperature)는 절대 Kelvin이고 "손대지 않음"을 0으로 구분해야
# 해서 이 표에 넣지 않고 따로 처리합니다.
#
# 읽기(settings)와 쓰기(set_settings)를 각각 손으로 나열하면 한쪽에만 필드를
# 추가하는 실수가 납니다. 실제로 그렇게 값이 저장되지 않는 문제가 있었습니다.
# 한 표에서 양방향을 모두 생성해 그 가능성을 없앱니다.
#
# 형식: 행 키 -> (설정 섹션, 필드명, 형변환, 배율)
SLIDER_BINDINGS: dict[str, tuple[str, str, type, float]] = {
    "basic.tint": ("basic", "tint", int, 1),
    "basic.exposure": ("basic", "exposure", float, 1),
    "basic.contrast": ("basic", "contrast", int, 1),
    "basic.highlights": ("basic", "highlights", int, 1),
    "basic.shadows": ("basic", "shadows", int, 1),
    "basic.whites": ("basic", "whites", int, 1),
    "basic.blacks": ("basic", "blacks", int, 1),
    "basic.texture": ("basic", "texture", int, 1),
    "basic.clarity": ("basic", "clarity", int, 1),
    "basic.dehaze": ("basic", "dehaze", int, 1),
    "basic.vibrance": ("basic", "vibrance", int, 1),
    "basic.saturation": ("basic", "saturation", int, 1),

    "curve.highlights": ("curve", "highlights", int, 1),
    "curve.lights": ("curve", "lights", int, 1),
    "curve.darks": ("curve", "darks", int, 1),
    "curve.shadows": ("curve", "shadows", int, 1),

    "detail.sharpen_amount": ("detail", "sharpen_amount", int, 1),
    "detail.sharpen_radius": ("detail", "sharpen_radius", float, 1),
    "detail.noise_reduction": ("detail", "noise_reduction", int, 1),
    "detail.noise_detail": ("detail", "noise_detail", int, 1),
    "detail.color_noise_reduction": ("detail", "color_noise_reduction", int, 1),
    "detail.color_noise_radius": ("detail", "color_noise_radius", int, 1),
    "detail.face_priority": ("detail", "face_priority", int, 1),
    "detail.destripe": ("detail", "destripe", int, 1),

    "grade.blending": ("color_grade", "blending", int, 1),
    "grade.balance": ("color_grade", "balance", int, 1),

    "optics.distortion": ("optics", "distortion", int, 1),
    "optics.vignetting": ("optics", "manual_vignetting", int, 1),
    "optics.defringe_purple": ("optics", "defringe_purple", int, 1),
    "optics.defringe_green": ("optics", "defringe_green", int, 1),

    "effects.grain_amount": ("effects", "grain_amount", int, 1),
    "effects.grain_size": ("effects", "grain_size", int, 1),
    "effects.vignette_amount": ("effects", "vignette_amount", int, 1),
    "effects.vignette_midpoint": ("effects", "vignette_midpoint", int, 1),

    # 크롭은 UI에서 %, 설정에서는 0~1 정규화 값입니다
    "geo.crop_left": ("geometry", "crop_left", float, 0.01),
    "geo.crop_top": ("geometry", "crop_top", float, 0.01),
    "geo.crop_right": ("geometry", "crop_right", float, 0.01),
    "geo.crop_bottom": ("geometry", "crop_bottom", float, 0.01),
    "geo.straighten": ("geometry", "straighten", float, 1),

    "wm.opacity": ("watermark", "opacity", int, 1),
    "wm.scale": ("watermark", "scale", int, 1),
    "wm.margin": ("watermark", "margin", int, 1),
    "wm.offset_x": ("watermark", "offset_x", float, 1),
    "wm.offset_y": ("watermark", "offset_y", float, 1),
    "wm.rotation": ("watermark", "rotation", int, 1),

    "strip.height": ("exif_strip", "height_percent", float, 1),
}

POSITION_LABELS = {
    WatermarkPosition.TOP_LEFT: "↖ 좌상단",
    WatermarkPosition.TOP_CENTER: "↑ 상단 가운데",
    WatermarkPosition.TOP_RIGHT: "↗ 우상단",
    WatermarkPosition.MIDDLE_LEFT: "← 좌측 가운데",
    WatermarkPosition.CENTER: "· 정가운데",
    WatermarkPosition.MIDDLE_RIGHT: "→ 우측 가운데",
    WatermarkPosition.BOTTOM_LEFT: "↙ 좌하단",
    WatermarkPosition.BOTTOM_CENTER: "↓ 하단 가운데",
    WatermarkPosition.BOTTOM_RIGHT: "↘ 우하단",
}


# 아래 라벨들은 화면에 보이는 텍스트라 언어에 따라 달라집니다. 모듈 로드
# 시점에 tr()로 굳히면 언어 전환이 안 되므로(gui/ordering_text.py와 같은
# 이유), 값을 함수 안에 두어 호출할 때마다 번역되게 합니다. core에 있는
# 표(NOISE·HSL·EXIF·STRIP·마스크 프리셋)는 Qt를 모르므로 여기서 옮겨 씁니다.


def _ratio_label(ratio: CropRatio) -> str:
    return {
        CropRatio.FREE: tr("Free"),
        CropRatio.ORIGINAL: tr("Original ratio"),
    }.get(ratio, RATIO_LABELS[ratio])  # 1:1·4:3 등 숫자 비율은 그대로 둡니다


def _position_label(position: WatermarkPosition) -> str:
    return {
        WatermarkPosition.TOP_LEFT: tr("↖ Top-left"),
        WatermarkPosition.TOP_CENTER: tr("↑ Top-center"),
        WatermarkPosition.TOP_RIGHT: tr("↗ Top-right"),
        WatermarkPosition.MIDDLE_LEFT: tr("← Middle-left"),
        WatermarkPosition.CENTER: tr("· Center"),
        WatermarkPosition.MIDDLE_RIGHT: tr("→ Middle-right"),
        WatermarkPosition.BOTTOM_LEFT: tr("↙ Bottom-left"),
        WatermarkPosition.BOTTOM_CENTER: tr("↓ Bottom-center"),
        WatermarkPosition.BOTTOM_RIGHT: tr("↘ Bottom-right"),
    }.get(position, str(position))


def _noise_algorithm_label(algorithm: NoiseAlgorithm) -> str:
    return {
        NoiseAlgorithm.NLMEANS: tr("Standard (non-local means)"),
        NoiseAlgorithm.NLMEANS_HQ: tr("High quality (non-local means, slow)"),
        NoiseAlgorithm.BILATERAL: tr("Fast (bilateral filter)"),
        NoiseAlgorithm.LEGACY: tr("Legacy (reproduces old versions)"),
    }.get(algorithm, str(algorithm))


def _hsl_band_label(band: str) -> str:
    return {
        "red": tr("Red"), "orange": tr("Orange"), "yellow": tr("Yellow"),
        "green": tr("Green"), "aqua": tr("Aqua"), "blue": tr("Blue"),
        "purple": tr("Purple"), "magenta": tr("Magenta"),
    }.get(band, band)


def _exif_field_label(key: str) -> str:
    return {
        "camera": tr("Camera (make/model)"),
        "lens": tr("Lens"),
        "exposure": tr("Exposure (shutter/aperture/ISO)"),
        "focal_length": tr("Focal length"),
        "datetime": tr("Date taken"),
        "artist": tr("Artist"),
        "copyright": tr("Copyright"),
        "software": tr("Software"),
    }.get(key, key)


def _strip_field_label(key: str) -> str:
    return {
        "filename": tr("Filename"),
        "camera": tr("Camera"),
        "lens": tr("Lens"),
        "focal_length": tr("Focal length"),
        "aperture": tr("Aperture"),
        "shutter": tr("Shutter"),
        "iso": tr("ISO"),
        "datetime": tr("Date taken"),
    }.get(key, key)


def _mask_preset_group(group: str) -> str:
    return {
        "인물": tr("Portrait"),
        "배경": tr("Background"),
        "조명·하늘": tr("Light & sky"),
    }.get(group, group)


def _mask_preset_label(key: str) -> str:
    return {
        "under_eye": tr("Under-eye retouch"),
        "skin_smooth": tr("Smooth skin"),
        "eye_pop": tr("Sharpen irises"),
        "teeth_white": tr("Whiten teeth"),
        "face_brighten": tr("Brighten face"),
        "subject_pop": tr("Emphasize subject (darken background)"),
        "bg_blur": tr("Blur background (bokeh)"),
        "sky_boost": tr("Bluer sky"),
        "spotlight": tr("Spotlight (darken surroundings)"),
        "dodge": tr("Brighten area (radial)"),
        "burn": tr("Darken area (radial)"),
    }.get(key, key)


def _mask_preset_description(key: str) -> str:
    return {
        "under_eye": tr(
            "Softens under-eye lines and dark circles, and lifts brightness a touch."),
        "skin_smooth": tr(
            "Smooths skin across the whole face; texture eased slightly."),
        "eye_pop": tr(
            "Adds clarity and sharpening to the irises to bring out the gaze."),
        "teeth_white": tr(
            "Removes the yellow cast from teeth and brightens slightly. "
            "Only affects shots with the mouth open."),
        "face_brighten": tr("Lifts a face darkened by backlight or shade."),
        "subject_pop": tr(
            "Darkens and desaturates the background to make the subject stand out."),
        "bg_blur": tr(
            "Softly blurs only the background for a shallow depth-of-field look."),
        "sky_boost": tr("A top linear mask makes the sky bluer and deeper."),
        "spotlight": tr("Darkens outside a central oval to draw the eye in."),
        "dodge": tr(
            "A radial mask brightens just where you want; "
            "adjust position and size afterward."),
        "burn": tr("A radial mask darkens just where you want."),
    }.get(key, "")


class DevelopPanel(QWidget):
    """전체 보정 파라미터를 접이식 섹션으로 노출합니다."""

    settings_changed = Signal()
    crop_mode_changed = Signal(bool)
    pick_mode_changed = Signal(str)  # "purple" / "green" / "" (해제)
    mask_overlay_changed = Signal()  # 선택 마스크 영역 표시 토글/선택 변경
    mask_shape_changed = Signal()
    """이미지 위에 그릴 도형(방사형·선형)이 달라졌음.

    영역 표시(mask_overlay_changed)와는 별개입니다. 조작점은 빨간 오버레이를
    켜지 않아도 보여야 하고, 반대로 오버레이만 껐다 켜는 것으로 도형이
    바뀌지는 않습니다.
    """
    brush_mode_changed = Signal(bool)  # 브러시로 직접 칠하기 on/off
    brush_changed = Signal()           # 붓 크기·지우개 변경 (미리보기 원 갱신)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self.rows: dict[str, SliderRow] = {}
        self.sections: dict[str, CollapsibleSection] = {}
        self._section_labels: dict[str, tuple[str, str]] = {}
        self.defringe_pickers: dict[str, QPushButton] = {}
        self._defringe_hues = {"purple": 145, "green": 65}
        # 색온도는 절대 Kelvin. 손대지 않으면 as-shot(=변화 없음, 0으로 저장),
        # 사용자가 움직이면 그 절대값을 저장합니다.
        self._as_shot_kelvin = DEFAULT_KELVIN
        self._temperature_touched = False
        # HSL은 8개 밴드 × 3채널인데 슬라이더는 8개뿐입니다. 보이지 않는
        # 채널 값을 여기 들고 있다가 탭을 바꿀 때 바꿔 끼웁니다.
        self._hsl_state: dict[str, HSLBand] = {band: HSLBand() for band in HSL_BANDS}
        # 국소 보정 마스크. 컷별 편집 상태라 프리셋 공유·일괄 적용에서 빠집니다.
        self._masks: list[Mask] = []
        # 폭은 **최소치만** 정하고 나머지는 스플리터에 맡깁니다. 고정하면
        # 내용이 조금만 늘어도 오른쪽이 말없이 잘립니다(세 번 겪었습니다).
        # 최소치도 픽셀이 아니라 글자 폭에서 뽑아, 폰트·DPI가 달라져도
        # 같은 비율로 맞습니다.
        self.setMinimumWidth(self._minimum_panel_width())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        self.preset_bar = PresetBar(
            develop_presets(),
            collect=lambda: self.settings().to_dict(),
            apply=lambda data: self.set_settings(DevelopSettings.from_dict(data)),
        )
        self.preset_bar.applied.connect(self.settings_changed.emit)
        preset_wrapper = QWidget()
        wrapper_layout = QVBoxLayout(preset_wrapper)
        wrapper_layout.setContentsMargins(8, 8, 8, 0)
        wrapper_layout.addWidget(self.preset_bar)
        outer.addWidget(preset_wrapper)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet(f"QScrollArea {{ background: {theme.BACKGROUND}; }}")
        # 최후의 안전장치. 화면이 좁거나 폰트가 커서 내용이 안 들어가면
        # 잘라 버리는 대신 가로로 밀어 볼 수 있게 합니다. 지금까지 이
        # 상황에서 오른쪽이 그냥 사라져 손이 닿지 않았습니다.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll = scroll

        # 섹션이 열두 개라 스크롤이 깁니다. 오른쪽 가장자리에 노트 인덱스처럼
        # 탭을 세워 두면 원하는 섹션으로 바로 갈 수 있습니다.
        # 탭은 왼쪽 가장자리에 둡니다. 오른쪽에 두면 값 입력칸·스크롤바와 붙어
        # 시선이 분산되고, 패널이 좁아질 때 제일 먼저 밀려 잘립니다.
        body_row = QHBoxLayout()
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(0)
        self._tab_strip_holder = QVBoxLayout()
        self._tab_strip_holder.setContentsMargins(0, 0, 0, 0)
        body_row.addLayout(self._tab_strip_holder)
        body_row.addWidget(scroll, 1)
        outer.addLayout(body_row, 1)

        container = QWidget()
        self._content = container
        self.body = QVBoxLayout(container)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(1)
        scroll.setWidget(container)

        self._build_basic()
        self._build_curve()
        self._build_detail()
        self._build_masks()
        self._build_hsl()
        self._build_color_grade()
        self._build_effects()
        self._build_optics()
        self._build_geometry()
        self._build_exif_strip()
        self._build_watermark()
        self._build_metadata()
        self.body.addStretch(1)
        self._build_section_tabs()
        # 드롭다운·스핀박스 위를 휠로 지나갈 때 값이 바뀌지 않게 합니다
        disable_wheel_in(self)

        # 콤보가 '가장 긴 항목'만큼 넓어지려 들면 패널이 밀려 오른쪽이 잘립니다.
        # 렌즈 목록(1218개)과 글꼴 목록(620여 개)에는 아주 긴 이름이 섞여 있어
        # 실제로 그렇게 됐습니다. 목록 팝업은 넓게 뜨되, 콤보 자체는 칸에
        # 맞추게 합니다.
        for combo in self.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(8)

        # 왼쪽 탭 띠가 생긴 뒤 스크롤 영역의 자리를 다시 잡습니다. 이걸
        # 빠뜨리면 스크롤 영역이 패널 전체 폭을 차지한 채 남아, 내용이 탭 띠
        # 폭(28px)만큼 오른쪽으로 밀려 잘립니다.
        self.layout().activate()

        reset = QPushButton(tr("Reset all"))
        reset.clicked.connect(self.reset)
        footer = QWidget()
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(8, 0, 8, 8)
        footer_layout.addWidget(reset)
        outer.addWidget(footer)

    # ------------------------------------------------------------ 섹션 구성

    def _section(
        self, key: str, title: str, icon: str = "", expanded: bool = False
    ) -> CollapsibleSection:
        section = CollapsibleSection(f"{icon} {title}" if icon else title, expanded)
        section.visibility_changed.connect(self._emit)
        self.sections[key] = section
        # 우측 탭에 쓸 아이콘·이름을 기억해 둡니다
        self._section_labels[key] = (icon or "•", title)
        self.body.addWidget(section)
        return section

    # 탭이 왼쪽 가장자리라 강조선은 안쪽(오른쪽)에 그립니다.
    _TAB_STYLE = (
        "QToolButton { background: #2b2b30; color: #b8b8c0; border: none;"
        " border-right: 2px solid transparent; font-size: 13px; }"
        "QToolButton:hover { background: #3a3a42; color: #fff; }"
    )
    _TAB_STYLE_ACTIVE = (
        "QToolButton { background: #33404f; color: #cfe0ff; border: none;"
        " border-right: 2px solid #5a7bb0; font-size: 13px; }"
        "QToolButton:hover { background: #3f5169; color: #fff; }"
    )

    _TAB_STRIP_WIDTH = 28

    def _minimum_panel_width(self) -> int:
        """최소 폭을 글자 폭에서 계산합니다 (폰트·DPI에 따라 함께 커집니다).

        다만 화면 폭의 일정 비율을 넘지 않게 눌러 둡니다. 최소 폭이 화면보다
        커지면 스플리터가 그 요구를 들어줄 수 없어, 사용자가 손으로 끌기
        전에는 오른쪽이 잘린 채로 뜹니다(FHD 100%에서 실제로 발생).
        넘치는 내용은 이제 가로 스크롤로 닿을 수 있으므로, 최소 폭을
        낮춰도 접근성이 사라지지 않습니다.
        """
        char = max(7, self.fontMetrics().averageCharWidth())
        scrollbar = self.style().pixelMetric(QStyle.PM_ScrollBarExtent)
        wanted = char * PANEL_MIN_CHARS + self._TAB_STRIP_WIDTH + scrollbar + 8

        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry().width()
            if available > 0:
                # 절반을 넘게 차지하면 정작 사진이 안 보입니다
                wanted = min(wanted, int(available * 0.38))
        return max(320, wanted)

    def content_width(self) -> int:
        """스크롤 안 내용이 실제로 쓸 수 있는 가로 폭.

        패널 폭에서 왼쪽 탭 띠와 세로 스크롤바를 뺀 값입니다. 테스트가 이 값과
        내용 선호 폭을 비교해 잘림을 잡아냅니다.
        """
        scrollbar = self.style().pixelMetric(QStyle.PM_ScrollBarExtent)
        width = self.width() or self.minimumWidth()
        return width - self._TAB_STRIP_WIDTH - scrollbar - 8

    def required_content_width(self) -> int:
        """내용이 제대로 보이려면 필요한 가로 폭.

        minimumSizeHint는 못 씁니다 — 버튼은 글자를 뭉개서라도 줄어들 수 있다고
        보고해서 '맞는다'고 나오지만 화면에서는 잘립니다. 선호 폭(sizeHint)의
        최댓값을 봐야 합니다.
        """
        needed = self.body.sizeHint().width()
        for child in self._content.findChildren(QWidget):
            needed = max(needed, child.sizeHint().width())
        return needed

    def _build_section_tabs(self) -> None:
        """오른쪽 가장자리 세로 탭. 누르면 그 섹션을 펴고 그리로 스크롤합니다."""
        strip = QWidget()
        strip.setFixedWidth(28)
        strip.setStyleSheet(f"background: {theme.BACKGROUND};")
        layout = QVBoxLayout(strip)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(1)

        self.section_tabs: dict[str, QToolButton] = {}
        for key, (icon, title) in self._section_labels.items():
            tab = QToolButton()
            tab.setText(icon)
            tab.setToolTip(title)
            tab.setFixedSize(28, 30)
            tab.setCursor(Qt.PointingHandCursor)
            tab.setStyleSheet(self._TAB_STYLE)
        # 왼쪽에 붙으므로 강조선도 왼쪽이 아니라 오른쪽에 그립니다
            tab.clicked.connect(lambda _=False, k=key: self._jump_to_section(k))
            layout.addWidget(tab)
            self.section_tabs[key] = tab

        layout.addStretch(1)
        self._tab_strip_holder.addWidget(strip)

    def _jump_to_section(self, key: str) -> None:
        section = self.sections.get(key)
        if section is None:
            return
        section.set_expanded(True)
        # 펼치면서 레이아웃이 다시 잡히므로, 자리 계산이 끝난 뒤에 스크롤합니다
        QTimer.singleShot(0, lambda: self._scroll_section_to_top(section))

    def _scroll_section_to_top(self, section) -> None:
        """섹션 제목이 보이는 영역 **맨 위**에 오도록 스크롤합니다.

        예전에는 ensureWidgetVisible을 썼습니다. 그건 '보이기만 하면 되는'
        최소 스크롤이라, 섹션이 뷰포트보다 길면 제목이 위로 지나가 버리고
        가운데쯤이 보입니다. 어느 섹션을 연 것인지 알 수 없습니다.
        스크롤 위치를 직접 지정하면 항상 제목부터 보입니다.
        """
        from PySide6.QtCore import QPoint

        content = self._scroll.widget()
        if content is None:
            return
        bar = self._scroll.verticalScrollBar()
        top = section.mapTo(content, QPoint(0, 0)).y()
        bar.setValue(min(top, bar.maximum()))

    def _add_row(self, section: CollapsibleSection, key: str, *args, **kwargs) -> SliderRow:
        row = SliderRow(*args, **kwargs)
        row.value_changed.connect(self._emit)
        self.rows[key] = row
        section.add_widget(row)
        return row

    def _build_basic(self) -> None:
        section = self._section("basic", tr("Basic"), "◐", expanded=True)
        # 색온도는 절대 Kelvin입니다. 낮을수록 차갑고(파랑), 높을수록
        # 따뜻합니다(주황). 기본값(as-shot)은 이미지를 열 때 채워 넣습니다.
        # 상한 12000K면 촛불(~1800)부터 짙은 흐림(~10000)까지 실제 촬영 광원을
        # 모두 덮으면서도 슬라이더 눈금이 촘촘해 미세 조정이 쉽습니다.
        temp_row = self._add_row(
            section, "basic.temperature", tr("Temperature"), KELVIN_MIN, KELVIN_MAX,
            default=DEFAULT_KELVIN, suffix=" K",
            gradient=temperature_track_colors(DEFAULT_KELVIN, KELVIN_MIN, KELVIN_MAX),
            tooltip=tr("Absolute value based on the capture colour temperature. "
                       "Lower it for cooler, raise it for warmer"),
        )
        temp_row.slider.setSingleStep(50)
        temp_row.value_changed.connect(self._on_temperature_touched)
        self._add_row(section, "basic.tint", tr("Tint"), -100, 100, gradient="tint",
                      tooltip=tr("Positive is magenta, negative is green"))
        self._add_row(section, "basic.exposure", tr("Exposure"), -5, 5, decimals=2,
                      suffix=" EV", gradient="exposure",
                      tooltip=tr("Multiplies the whole image to brighten it. "
                                 "Raising it blows the highlights first"))
        self._add_row(section, "basic.brightness", tr("Brightness"), -100, 100,
                      gradient="exposure",
                      tooltip=tr("Adjusts only the midtones, leaving whites and blacks alone.\n"
                                 "Better than exposure for lifting just the face of a backlit subject"))
        self._add_row(section, "basic.contrast", tr("Contrast"), -100, 100, gradient="contrast")
        self._add_row(section, "basic.highlights", tr("Highlights"), -100, 100,
                      gradient="highlights")
        self._add_row(section, "basic.shadows", tr("Shadows"), -100, 100, gradient="shadows")
        self._add_row(section, "basic.whites", tr("Whites"), -100, 100, gradient="whites")
        self._add_row(section, "basic.blacks", tr("Blacks"), -100, 100, gradient="blacks")
        self._add_row(section, "basic.texture", tr("Texture"), -100, 100, gradient="mono",
                      tooltip=tr("Mid-frequency detail"))
        self._add_row(section, "basic.clarity", tr("Clarity"), -100, 100, gradient="mono",
                      tooltip=tr("Local contrast — the large radius makes it the slowest to render"))
        self._add_row(section, "basic.dehaze", tr("Dehaze"), -100, 100, gradient="mono")
        self._add_row(section, "basic.vibrance", tr("Vibrance"), -100, 100, gradient="vibrance",
                      tooltip=tr("Touches already-saturated colours less (protects skin tones)"))
        self._add_row(section, "basic.saturation", tr("Saturation"), -100, 100,
                      gradient="saturation")

    def _build_curve(self) -> None:
        section = self._section("curve", tr("Curve"), "∿")

        self.curve_editor = CurveEditor()
        self.curve_editor.points_changed.connect(self._on_curve_points)

        # 채널은 콤보로 상태를 들고 있되(기존 코드·테스트 호환), 눈에 보이는
        # 조작은 곡선 위의 버튼으로 합니다.
        self.curve_channel = QComboBox()
        self.curve_channel.setVisible(False)
        for label, key in (
            ("RGB", "rgb"), (tr("Red"), "red"), (tr("Green"), "green"),
            (tr("Blue"), "blue")
        ):
            self.curve_channel.addItem(label, key)
        self.curve_channel.currentIndexChanged.connect(self._on_curve_channel)

        # 곡선 위 버튼 행: 클리핑 토글 + 채널(밝기/R/G/B) + 초기화
        button_row = QHBoxLayout()
        button_row.setSpacing(3)

        self.curve_clip_button = QPushButton(tr("Clipping"))
        self.curve_clip_button.setCheckable(True)
        self.curve_clip_button.setChecked(True)
        self.curve_clip_button.setFixedHeight(24)
        self.curve_clip_button.setToolTip(tr("Show where the curve clips tonal values"))
        self.curve_clip_button.toggled.connect(self.curve_editor.set_clip_markers)
        self.curve_clip_button.setStyleSheet(_CURVE_BUTTON_STYLE)
        button_row.addWidget(self.curve_clip_button)
        button_row.addStretch(1)

        self.curve_channel_buttons = QButtonGroup(self)
        self.curve_channel_buttons.setExclusive(True)
        for index, (label, color) in enumerate(
            ((tr("RGB"), "#dddddd"), ("R", "#e06060"),
             ("G", "#5cc264"), ("B", "#5c8cf0"))
        ):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setFixedSize(34, 24)
            button.setToolTip(tr("{label} channel curve").format(label=label))
            button.setStyleSheet(_curve_channel_style(color))
            button.clicked.connect(
                lambda _=False, i=index: self.curve_channel.setCurrentIndex(i)
            )
            self.curve_channel_buttons.addButton(button, index)
            button_row.addWidget(button)
        self.curve_channel_buttons.button(0).setChecked(True)

        reset_curve = QPushButton("↺")
        reset_curve.setFixedSize(28, 24)
        reset_curve.setToolTip(tr("Reset this channel's curve"))
        reset_curve.clicked.connect(self._reset_curve_channel)
        reset_curve.setStyleSheet(_CURVE_BUTTON_STYLE)
        button_row.addWidget(reset_curve)
        section.add_layout(button_row)

        section.add_widget(self.curve_editor)

        hint = QLabel(tr("Click to add · drag to move · right-click/double-click to delete"))
        hint.setStyleSheet(theme.hint_label())
        # 영어로는 한 줄이 패널보다 넓습니다. 접어서 좁은 폭에서도 다 보이게
        # 합니다(한국어는 짧아 원래 한 줄에 들어갑니다).
        hint.setWordWrap(True)
        section.add_widget(hint)

        # 채널별 점을 따로 들고 있습니다. 편집기는 한 채널만 보여 줍니다.
        self._curve_points: dict[str, tuple] = {
            "rgb": (), "red": (), "green": (), "blue": ()
        }

        self._add_row(section, "curve.highlights", tr("Highlights"), -100, 100,
                      gradient="highlights")
        self._add_row(section, "curve.lights", tr("Lights"), -100, 100, gradient="whites")
        self._add_row(section, "curve.darks", tr("Darks"), -100, 100, gradient="shadows")
        self._add_row(section, "curve.shadows", tr("Shadows"), -100, 100,
                      gradient="blacks")
        # 파라메트릭 구간을 곡선 그래프에 반영합니다.
        for key in ("curve.highlights", "curve.lights", "curve.darks", "curve.shadows"):
            self.rows[key].value_changed.connect(self._update_curve_parametric)

    def _update_curve_parametric(self, *_) -> None:
        """파라메트릭 4구간 값을 곡선 편집기에 전달합니다."""
        self.curve_editor.set_parametric(
            int(self.rows["curve.shadows"].value()),
            int(self.rows["curve.darks"].value()),
            int(self.rows["curve.lights"].value()),
            int(self.rows["curve.highlights"].value()),
        )

    def _on_temperature_touched(self, _value: float) -> None:
        """사용자가 색온도를 움직이면 절대값으로 저장하기 시작합니다."""
        if not self._loading:
            self._temperature_touched = True

    def set_as_shot_kelvin(self, kelvin: int) -> None:
        """이미지를 열 때 as-shot 색온도를 슬라이더 기본/현재값으로 채웁니다.

        아직 손대지 않은 상태면 슬라이더를 as-shot에 맞춰, 그 위치가
        '변화 없음'이 되게 합니다.
        """
        self._as_shot_kelvin = int(kelvin)
        row = self.rows["basic.temperature"]
        row.default = float(kelvin)
        # 트랙의 무채색 지점도 이 컷의 as-shot으로 옮겨 줍니다. 그래야
        # 핸들이 놓인 자리가 "변화 없음"이라는 게 색으로 보입니다.
        row.set_gradient(
            temperature_track_colors(self._as_shot_kelvin, KELVIN_MIN, KELVIN_MAX)
        )
        if not self._temperature_touched:
            row.set_value(float(kelvin), silent=True)

    def _on_curve_channel(self) -> None:
        """채널 탭 전환 — 편집기에 그 채널의 점을 채웁니다."""
        index = self.curve_channel.currentIndex()
        channel = self.curve_channel.currentData()
        # 버튼으로 조작하든 코드로 바꾸든 채널 버튼 상태를 맞춰 줍니다.
        button = self.curve_channel_buttons.button(index)
        if button is not None and not button.isChecked():
            button.setChecked(True)
        self.curve_editor.set_channel(channel)
        previous = self._loading
        self._loading = True
        self.curve_editor.set_points(self._curve_points[channel])
        self._loading = previous

    def _on_curve_points(self, points: tuple) -> None:
        self._curve_points[self.curve_channel.currentData()] = points
        self._emit()

    def _reset_curve_channel(self) -> None:
        self._curve_points[self.curve_channel.currentData()] = ()
        self.curve_editor.reset()

    def set_curve_histogram(self, values) -> None:
        """루페가 현재 이미지의 히스토그램을 넘겨줍니다."""
        self.curve_editor.set_histogram(values)

    def _build_detail(self) -> None:
        section = self._section("detail", tr("Detail"), "◈")
        self._add_row(section, "detail.sharpen_amount", tr("Sharpening"), 0, 150,
                      gradient="mono")
        self._add_row(section, "detail.sharpen_radius", tr("Radius"), 0.5, 3.0, 1.0,
                      decimals=1, gradient="mono")

        # 노이즈 감소는 방식마다 남는 디테일과 걸리는 시간이 크게 다릅니다.
        # 슬라이더 위에 방식을 먼저 두어, 무엇을 조절하고 있는지가 보이게
        # 합니다.
        algorithm_row = QHBoxLayout()
        algorithm_row.addWidget(QLabel(tr("Noise method")))
        self.noise_algorithm = QComboBox()
        for algorithm in NOISE_ALGORITHM_LABELS:
            self.noise_algorithm.addItem(_noise_algorithm_label(algorithm), algorithm)
        self.noise_algorithm.setToolTip(tr(
            "The method used to remove luminance noise.\n"
            "The values in parentheses are measured on real R6 Mark III ISO 6400 files:\n"
            "the detail retained and the 32MP processing time when noise is halved.\n\n"
            "Standard: detail 99.4% / 0.95s — default for high ISO\n"
            "High quality: detail 99.9% / 2.6s — for a single large print\n"
            "Fast: detail 79.9% / 0.34s — a light touch at low ISO\n"
            "Legacy: detail 78.7% — only to reproduce older results exactly"
        ))
        self.noise_algorithm.currentIndexChanged.connect(self._emit)
        algorithm_row.addWidget(self.noise_algorithm, 1)
        section.add_layout(algorithm_row)

        self._add_row(section, "detail.noise_reduction", tr("Noise reduction"), 0, 100,
                      gradient="mono",
                      tooltip=tr("Luminance (brightness) noise. The strength adapts\n"
                                 "automatically to the photo's real noise, so the same\n"
                                 "value gives a similar result across different ISOs"))
        self._add_row(section, "detail.noise_detail", tr("Detail preservation"), 0, 100, 50,
                      gradient="mono",
                      tooltip=tr("Restores the original where there is fine texture like\n"
                                 "hair or foliage. Flat sky or skin is left unaffected"))
        self._add_row(section, "detail.color_noise_reduction", tr("Color noise reduction"),
                      0, 100, gradient="mono",
                      tooltip=tr("Removes only colour mottling. It does not touch\n"
                                 "luminance, so there is no loss of detail"))
        self._add_row(section, "detail.color_noise_radius", tr("Color noise radius"),
                      0, 100, 50, gradient="mono",
                      tooltip=tr("How large a colour blob to catch. Blobs grow larger at\n"
                                 "higher ISO. Raising it also bleeds true colour edges"))
        self._add_row(section, "detail.destripe", tr("Destripe"), 0, 100, 0,
                      gradient="mono",
                      tooltip=tr("Removes the horizontal banding that appears when an LED\n"
                                 "wall's PWM flicker beats against the rolling shutter.\n\n"
                                 "Measured (DSC02751 ISO2500 1/800,\n"
                                 "     DSC03868 ISO3200 1/1000):\n"
                                 "  both frames period 103px — the same across ISO and shutter\n"
                                 "  banding cut 71~78%, horizontal detail 99.6% preserved\n"
                                 "  frames without banding are not detected and left alone\n\n"
                                 "Because it subtracts the same value from every row,\n"
                                 "horizontal detail is not damaged in principle"))
        self._add_row(section, "detail.face_priority", tr("Face priority"), 0, 100, 85,
                      gradient="mono",
                      tooltip=tr("How much to hold back luminance noise reduction outside\n"
                                 "faces. At high ISO the grain that bothers you is usually\n"
                                 "on skin, and the same strength across the whole frame\n"
                                 "smears fabric weave and hair as well.\n\n"
                                 "Measured (A6700 ISO3200, noise reduction 70):\n"
                                 "  0 — skin -39% / background detail -20%\n"
                                 " 85 — skin -33% / background detail -6% (default)\n"
                                 "100 — skin -34% / background detail -2%, twice as fast\n\n"
                                 "Ignored on photos with no face"))

    # ------------------------------------------------------------ 마스크

    # 선택한 마스크에 붙는 국소 조정 슬라이더 (필드, 라벨, 최소, 최대, 소수, 접미)
    @staticmethod
    def _mask_adjust_specs():
        return [
            ("exposure", tr("Exposure"), -3.0, 3.0, 2, " EV"),
            ("contrast", tr("Contrast"), -100, 100, 0, ""),
            ("highlights", tr("Highlights"), -100, 100, 0, ""),
            ("shadows", tr("Shadows"), -100, 100, 0, ""),
            ("temperature", tr("Temperature"), -100, 100, 0, ""),
            ("saturation", tr("Saturation"), -100, 100, 0, ""),
            ("texture", tr("Texture"), -100, 100, 0, ""),
            ("clarity", tr("Clarity"), -100, 100, 0, ""),
            ("smoothing", tr("Skin smoothing"), 0, 100, 0, ""),
            ("sharpen", tr("Sharpening"), 0, 150, 0, ""),
        ]

    def _build_masks(self) -> None:
        section = self._section("masks", tr("Local adjustments (masks)"), "❉")

        add_button = QToolButton()
        add_button.setText(tr("＋ Add mask"))
        add_button.setPopupMode(QToolButton.InstantPopup)
        add_button.setStyleSheet(
            "QToolButton { background: #34506a; color: #eaf2ff; border: none;"
            " border-radius: 4px; padding: 6px 10px; } QToolButton::menu-indicator { image: none; }"
            "QToolButton:hover { background: #3f6187; }"
        )
        menu = QMenu(add_button)
        groups: dict[str, list] = {}
        for preset in MASK_PRESETS:
            groups.setdefault(preset.group, []).append(preset)
        for group, presets in groups.items():
            menu.addSection(_mask_preset_group(group))
            for preset in presets:
                action = menu.addAction(_mask_preset_label(preset.key))
                action.setToolTip(_mask_preset_description(preset.key))
                action.triggered.connect(
                    lambda _=False, key=preset.key: self._add_mask_preset(key)
                )
        # 브러시는 인식이 아니라 사용자가 직접 칠하는 것이라 따로 둡니다
        menu.addSection(tr("Manual"))
        brush_action = menu.addAction(tr("Brush (paint by hand)"))
        brush_action.setToolTip(tr("Drag over the image to paint just the area you want"))
        brush_action.triggered.connect(self._add_brush_mask)

        add_button.setMenu(menu)
        self._mask_menu = menu  # 참조를 잡아 둬야 GC되지 않습니다
        section.add_widget(add_button)

        hint = QLabel(tr("The face, eye and background presets are detected automatically on this frame"))
        hint.setStyleSheet(theme.hint_label("#7a9a7a"))
        hint.setWordWrap(True)
        section.add_widget(hint)

        self.mask_list = QListWidget()
        self.mask_list.setMaximumHeight(110)
        self.mask_list.setStyleSheet(
            "QListWidget { background: #232327; color: #ddd; border: 1px solid #3a3a40;"
            " border-radius: 3px; }"
        )
        # 안내는 툴팁으로만 답니다. 여기에 라벨을 한 줄 더 넣으면 그 글자
        # 폭이 패널 최소 폭을 밀어 올려, 좁은 화면에서 오른쪽이 잘립니다.
        self.mask_list.setToolTip(tr(
            "Selecting a radial or linear mask shows handles on the image.\n"
            "Drag the centre to move, an edge point to resize, an outer point to rotate."
        ))
        self.mask_list.itemChanged.connect(self._on_mask_item_changed)
        self.mask_list.currentRowChanged.connect(self._on_mask_selected)
        section.add_widget(self.mask_list)

        controls_row = QHBoxLayout()
        self.mask_overlay_check = QCheckBox(tr("Show region"))
        self.mask_overlay_check.setToolTip(tr("Shows the area the selected mask covers in red"))
        self.mask_overlay_check.toggled.connect(lambda _=False: self.mask_overlay_changed.emit())
        controls_row.addWidget(self.mask_overlay_check)
        controls_row.addStretch(1)
        self.mask_delete_button = QPushButton(tr("Delete"))
        self.mask_delete_button.clicked.connect(self._delete_selected_mask)
        controls_row.addWidget(self.mask_delete_button)
        section.add_layout(controls_row)

        # 브러시 전용 조작. BRUSH 마스크를 골랐을 때만 켜집니다.
        self.brush_box = QWidget()
        brush_layout = QVBoxLayout(self.brush_box)
        brush_layout.setContentsMargins(0, 2, 0, 2)
        brush_layout.setSpacing(2)

        brush_row = QHBoxLayout()
        self.brush_paint = QCheckBox(tr("Paint"))
        self.brush_paint.setToolTip(tr("When on, drag over the image to paint an area"))
        self.brush_paint.toggled.connect(self.brush_mode_changed.emit)
        brush_row.addWidget(self.brush_paint)
        self.brush_erase = QCheckBox(tr("Eraser"))
        self.brush_erase.setToolTip(tr("Erases what you have painted"))
        self.brush_erase.toggled.connect(lambda _=False: self.brush_changed.emit())
        brush_row.addWidget(self.brush_erase)
        brush_row.addStretch(1)
        clear = QPushButton(tr("Clear all"))
        clear.clicked.connect(self._clear_brush)
        brush_row.addWidget(clear)
        brush_layout.addLayout(brush_row)

        self.brush_size = SliderRow(tr("Brush size"), 1, 40, default=10, suffix=" %",
                                    tooltip=tr("Brush diameter relative to the image's short edge"))
        self.brush_size.value_changed.connect(lambda _=0.0: self.brush_changed.emit())
        brush_layout.addWidget(self.brush_size)
        section.add_widget(self.brush_box)
        self.brush_box.setVisible(False)

        self.mask_controls = QWidget()
        controls = QVBoxLayout(self.mask_controls)
        controls.setContentsMargins(0, 2, 0, 0)
        controls.setSpacing(1)

        # 얼굴·눈 마스크가 누구에게 걸리는지 고릅니다. 예전에는 '면적이 가장
        # 큰 얼굴' 하나로 고정이라, 앞줄 행인이 주인공보다 크게 잡히면 엉뚱한
        # 사람이 밝아졌고 여러 명을 한꺼번에 손볼 방법도 없었습니다.
        self.face_target_box = QWidget()
        target_layout = QVBoxLayout(self.face_target_box)
        target_layout.setContentsMargins(0, 0, 0, 2)
        target_layout.setSpacing(2)

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel(tr("Apply to")))
        self.mask_face_target = QComboBox()
        self.mask_face_target.addItem(tr("Main subject"), "main")
        self.mask_face_target.addItem(tr("All faces"), "all")
        self.mask_face_target.addItem(tr("By number"), "index")
        self.mask_face_target.setToolTip(tr(
            "Main subject — the face chosen by focus scoring (the red box on screen)\n"
            "All faces — applied to every detected face\n"
            "By number — largest face first: 1, 2, 3…"
        ))
        self.mask_face_target.currentIndexChanged.connect(
            self._on_mask_face_target_changed)
        target_row.addWidget(self.mask_face_target, 1)

        self.mask_face_index = QSpinBox()
        self.mask_face_index.setRange(1, 50)
        self.mask_face_index.setPrefix("#")
        self.mask_face_index.setToolTip(tr("Numbered from the largest face"))
        self.mask_face_index.valueChanged.connect(
            self._on_mask_face_target_changed)
        target_row.addWidget(self.mask_face_index)
        target_layout.addLayout(target_row)

        self.face_count_label = QLabel()
        self.face_count_label.setStyleSheet(theme.hint_label())
        target_layout.addWidget(self.face_count_label)
        controls.addWidget(self.face_target_box)

        self.mask_size = SliderRow(
            tr("Range"), 0, 200, default=100, suffix=" %",
            tooltip=tr("The size of the detected region. 100 is the default; 0~200% shrinks or grows it.\n"
                       "Applies only to face, eye and radial masks."),
        )
        self.mask_opacity = SliderRow(tr("Strength"), 0, 100, default=100, suffix=" %",
                                      tooltip=tr("Overall strength of the mask effect"))
        self.mask_feather = SliderRow(tr("Feather"), 0, 100, default=50, suffix=" %")
        self.mask_invert = QCheckBox(tr("Invert region"))
        for widget in (self.mask_size, self.mask_opacity, self.mask_feather):
            widget.value_changed.connect(self._on_mask_geometry_changed)
            controls.addWidget(widget)
        self.mask_invert.toggled.connect(self._on_mask_geometry_changed)
        controls.addWidget(self.mask_invert)

        self._mask_adjust_rows: dict[str, SliderRow] = {}
        for field, label, minimum, maximum, decimals, suffix in self._mask_adjust_specs():
            row = SliderRow(label, minimum, maximum, default=0.0,
                            decimals=decimals, suffix=suffix)
            row.value_changed.connect(self._on_mask_adjust_changed)
            self._mask_adjust_rows[field] = row
            controls.addWidget(row)

        section.add_widget(self.mask_controls)
        self.mask_controls.setEnabled(False)

    def _selected_mask_index(self) -> int:
        return self.mask_list.currentRow()

    def _add_mask_preset(self, key: str) -> None:
        mask = build_mask(key)
        if mask is None:
            return
        self._masks.append(mask)
        self._rebuild_mask_list(select=len(self._masks) - 1)
        self._emit()

    def _add_brush_mask(self) -> None:
        """빈 브러시 마스크를 추가하고 바로 칠하기 모드로 들어갑니다."""
        mask = Mask(
            kind=MaskType.BRUSH,
            adjust=LocalAdjustments(exposure=0.3),  # 칠하면 바로 보이도록 기본값
            feather=40,
            label=tr("Brush"),
        )
        self._masks.append(mask)
        self._rebuild_mask_list(select=len(self._masks) - 1)
        self.brush_paint.setChecked(True)
        self._emit()

    def _clear_brush(self) -> None:
        index = self._selected_mask_index()
        if not (0 <= index < len(self._masks)):
            return
        from dataclasses import replace

        self._masks[index] = replace(self._masks[index], bitmap="")
        self._emit()
        if self.mask_overlay_check.isChecked():
            self.mask_overlay_changed.emit()

    def brush_radius_ratio(self) -> float:
        """붓 반지름 (이미지 짧은 변 대비 비율)."""
        return max(0.005, self.brush_size.value() / 100.0 / 2.0)

    def is_erasing(self) -> bool:
        return self.brush_erase.isChecked()

    def set_brush_bitmap(self, bitmap: str) -> None:
        """루페가 칠한 결과를 선택된 마스크에 반영합니다."""
        index = self._selected_mask_index()
        if not (0 <= index < len(self._masks)):
            return
        from dataclasses import replace

        self._masks[index] = replace(self._masks[index], bitmap=bitmap)
        self._emit()

    def _rebuild_mask_list(self, select: int | None = None) -> None:
        previous = self._loading
        self._loading = True
        self.mask_list.clear()
        for mask in self._masks:
            item = QListWidgetItem(mask.label or mask.kind.value)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if mask.enabled else Qt.Unchecked)
            self.mask_list.addItem(item)
        self._loading = previous
        if select is not None and 0 <= select < len(self._masks):
            self.mask_list.setCurrentRow(select)
        self._load_selected_mask()

    def _load_selected_mask(self) -> None:
        index = self._selected_mask_index()
        active = 0 <= index < len(self._masks)
        self.mask_controls.setEnabled(active)
        self.mask_delete_button.setEnabled(active)
        if not active:
            self.brush_box.setVisible(False)
            if self.brush_paint.isChecked():
                self.brush_paint.setChecked(False)  # 칠하기 모드도 함께 끕니다
            self.mask_shape_changed.emit()
            return
        mask = self._masks[index]

        # 브러시 조작은 브러시 마스크에서만 보입니다. 다른 마스크로 넘어가면
        # 칠하기 모드를 꺼야 엉뚱한 마스크에 칠하지 않습니다.
        is_brush = mask.kind is MaskType.BRUSH
        self.brush_box.setVisible(is_brush)
        if not is_brush and self.brush_paint.isChecked():
            self.brush_paint.setChecked(False)
        previous = self._loading
        self._loading = True
        self.mask_size.set_value(float(mask.size), silent=True)
        # 범위는 도형으로 만드는 마스크에만 의미가 있습니다. 선형·배경·브러시는
        # 줄일 도형이 없으므로 아예 못 만지게 해 헷갈리지 않도록 합니다.
        self.mask_size.setEnabled(mask.kind in (MaskType.FACE, MaskType.EYE, MaskType.RADIAL))
        self.mask_opacity.set_value(float(mask.opacity), silent=True)
        self.mask_feather.set_value(float(mask.feather), silent=True)
        self.mask_invert.setChecked(mask.invert)

        # 얼굴 대상 선택은 얼굴·눈 마스크에서만 의미가 있습니다
        is_face = mask.kind in (MaskType.FACE, MaskType.EYE)
        self.face_target_box.setVisible(is_face)
        if is_face:
            target = str(mask.params.get("target", "main"))
            slot = self.mask_face_target.findData(target)
            self.mask_face_target.setCurrentIndex(slot if slot >= 0 else 0)
            self.mask_face_index.setValue(
                int(mask.params.get("index", 0)) + 1)
            self.mask_face_index.setVisible(target == "index")

        for field, row in self._mask_adjust_rows.items():
            row.set_value(float(getattr(mask.adjust, field)), silent=True)
        self._loading = previous
        # _loading과 무관하게 알립니다. 이건 값이 바뀌었다는 신호가 아니라
        # "지금 화면에 뜬 마스크는 이것"이라는 표시라, 컷을 불러올 때야말로
        # 반드시 나가야 합니다.
        self.mask_shape_changed.emit()

    def _on_mask_selected(self, _row: int) -> None:
        if self._loading:
            return
        self._load_selected_mask()
        if self.mask_overlay_check.isChecked():
            self.mask_overlay_changed.emit()

    def _on_mask_item_changed(self, item: QListWidgetItem) -> None:
        if self._loading:
            return
        index = self.mask_list.row(item)
        if not (0 <= index < len(self._masks)):
            return
        from dataclasses import replace

        self._masks[index] = replace(
            self._masks[index], enabled=item.checkState() == Qt.Checked
        )
        self._emit()

    def _on_mask_geometry_changed(self, *_) -> None:
        if self._loading:
            return
        index = self._selected_mask_index()
        if not (0 <= index < len(self._masks)):
            return
        from dataclasses import replace

        self._masks[index] = replace(
            self._masks[index],
            size=int(self.mask_size.value()),
            opacity=int(self.mask_opacity.value()),
            feather=int(self.mask_feather.value()),
            invert=self.mask_invert.isChecked(),
        )
        self._emit()
        # 범위(size)는 방사형 윤곽선의 크기 그 자체입니다. 안 알리면
        # 슬라이더만 움직이고 화면의 타원은 그대로 남습니다.
        self.mask_shape_changed.emit()
        if self.mask_overlay_check.isChecked():
            self.mask_overlay_changed.emit()

    def _on_mask_face_target_changed(self, *_) -> None:
        """얼굴 마스크가 누구에게 걸릴지 바꿉니다."""
        if self._loading:
            return
        index = self._selected_mask_index()
        if not (0 <= index < len(self._masks)):
            return
        from dataclasses import replace

        target = self.mask_face_target.currentData()
        self.mask_face_index.setVisible(target == "index")

        mask = self._masks[index]
        params = dict(mask.params)
        params["target"] = target
        # 번호는 사람이 보기 편하게 1부터, 안에서는 0부터
        params["index"] = max(0, self.mask_face_index.value() - 1)
        self._masks[index] = replace(mask, params=params)
        self._emit()
        if self.mask_overlay_check.isChecked():
            self.mask_overlay_changed.emit()

    def set_face_count(self, count: int) -> None:
        """이 컷에서 검출된 얼굴 수. 번호를 고르려면 몇 개인지 알아야 합니다."""
        self.mask_face_index.setMaximum(max(1, count))
        if count <= 0:
            self.face_count_label.setText(tr("No faces detected"))
        else:
            self.face_count_label.setText(tr("{count} faces detected").format(count=count))

    def _on_mask_adjust_changed(self, *_) -> None:
        if self._loading:
            return
        index = self._selected_mask_index()
        if not (0 <= index < len(self._masks)):
            return
        from dataclasses import replace

        values = {}
        for field, row in self._mask_adjust_rows.items():
            values[field] = row.value() if field == "exposure" else int(row.value())
        adjust = replace(self._masks[index].adjust, **values)
        self._masks[index] = replace(self._masks[index], adjust=adjust)
        self._emit()

    def _delete_selected_mask(self) -> None:
        index = self._selected_mask_index()
        if not (0 <= index < len(self._masks)):
            return
        del self._masks[index]
        self._rebuild_mask_list(
            select=min(index, len(self._masks) - 1) if self._masks else None
        )
        self._emit()
        if self.mask_overlay_check.isChecked():
            self.mask_overlay_changed.emit()

    def overlay_mask(self) -> "Mask | None":
        """루페가 그릴 오버레이 마스크. 표시가 꺼져 있거나 선택이 없으면 None."""
        if not self.mask_overlay_check.isChecked():
            return None
        index = self._selected_mask_index()
        return self._masks[index] if 0 <= index < len(self._masks) else None

    SHAPE_KINDS = (MaskType.RADIAL, MaskType.LINEAR)
    """이미지 위에서 직접 끌 수 있는 마스크. 나머지는 인식이나 붓이 자리를 정합니다."""

    def shape_mask(self) -> "Mask | None":
        """이미지 위에 조작점을 띄울 마스크. 없으면 None.

        영역 표시 체크와 무관합니다 — 조작점 자체가 보이니 무엇을 만지는지는
        알 수 있고, 빨간 오버레이를 켜야만 움직일 수 있다면 그게 더 이상합니다.
        """
        index = self._selected_mask_index()
        if not (0 <= index < len(self._masks)):
            return None
        mask = self._masks[index]
        return mask if mask.kind in self.SHAPE_KINDS else None

    def set_mask_params(self, params: dict, *, silent: bool = False) -> None:
        """선택 마스크의 정규화 파라미터를 갈아 끼웁니다.

        끄는 동안에는 silent=True로 부릅니다. 매 픽셀마다 settings_changed를
        내면 무거운 재렌더가 예약과 취소를 반복해 조작이 따라오지 못합니다
        (크롭 드래그와 같은 이유). 손을 뗀 뒤 한 번만 알리면 됩니다.
        """
        index = self._selected_mask_index()
        if not (0 <= index < len(self._masks)):
            return
        from dataclasses import replace

        self._masks[index] = replace(self._masks[index], params=dict(params))
        if not silent:
            self._emit()

    def _build_hsl(self) -> None:
        section = self._section("hsl", tr("Color mixer"), "◎")

        self.hsl_channel = QComboBox()
        self.hsl_channel.addItems([tr("Hue"), tr("Saturation"), tr("Luminance")])
        self.hsl_channel.currentIndexChanged.connect(self._on_hsl_channel)
        section.add_widget(self.hsl_channel)

        for band in HSL_BANDS:
            self._add_row(section, f"hsl.{band}", _hsl_band_label(band), -100, 100)
        self._refresh_hsl_gradients()

    def _refresh_hsl_gradients(self) -> None:
        """밴드마다 그 색상대 고유의 트랙 색을 입힙니다.

        전부 같은 무지개로 칠하면 지금 무엇을 만지는지 알 수 없습니다.
        채널(색조/채도/광도)에 따라서도 의미가 달라 매번 다시 칠합니다.
        """
        channel = self._hsl_channel_key()
        for band in HSL_BANDS:
            self.rows[f"hsl.{band}"].set_gradient(
                hsl_band_colors(HSL_BAND_CENTERS[band], channel)
            )

    def _build_color_grade(self) -> None:
        section = self._section("color_grade", tr("Color grading"), "◑")

        # 색조와 채도를 슬라이더로 나눠 놓으면 "어느 방향으로 얼마나"가
        # 안 잡힙니다. 휠 위의 한 점을 끄는 편이 훨씬 빠릅니다.
        self.grade_zones: dict[str, ColorGradeZoneWidget] = {}

        middle = ColorGradeZoneWidget(tr("Midtones"))
        middle.changed.connect(self._emit)
        self.grade_zones["midtones"] = middle
        section.add_widget(middle)

        pair = QHBoxLayout()
        for key, label in (("shadows", tr("Shadows")), ("highlights", tr("Highlights"))):
            zone = ColorGradeZoneWidget(label)
            zone.changed.connect(self._emit)
            self.grade_zones[key] = zone
            pair.addWidget(zone)
        section.add_layout(pair)

        self._add_row(section, "grade.blending", tr("Blending"), 0, 100, 50, gradient="mono")
        self._add_row(section, "grade.balance", tr("Balance"), -100, 100, gradient="mono")

    def _build_optics(self) -> None:
        section = self._section("optics", tr("Optics"), "⊙")

        self.optics_auto = QCheckBox(tr("Auto lens profile"))
        self.optics_auto.setToolTip(tr(
            "Looks up the camera and lens in the lensfun database and corrects them.\n"
            "For lenses not in the DB, use the manual correction below."
        ))
        self.optics_auto.toggled.connect(self._emit)
        section.add_widget(self.optics_auto)

        self.lens_label = QLabel()
        self.lens_label.setWordWrap(True)
        self.lens_label.setStyleSheet(theme.hint_label(theme.TEXT_DIM))
        section.add_widget(self.lens_label)

        auto_row = QHBoxLayout()
        self.optics_auto_distortion = QCheckBox(tr("Distortion"))
        self.optics_auto_vignetting = QCheckBox(tr("Vignetting"))
        self.optics_auto_chromatic = QCheckBox(tr("Chromatic aberration"))
        self.optics_auto_chromatic.setToolTip(tr(
            "Lateral chromatic aberration — the colour fringing from slight per-channel magnification differences"
        ))
        for check in (
            self.optics_auto_distortion,
            self.optics_auto_vignetting,
            self.optics_auto_chromatic,
        ):
            check.setChecked(True)
            check.toggled.connect(self._emit)
            auto_row.addWidget(check)
        section.add_layout(auto_row)

        lens_row = QHBoxLayout()
        lens_row.addWidget(QLabel(tr("Lens override")))
        self.lens_override = QComboBox()
        self.lens_override.setEditable(True)
        self.lens_override.setToolTip(tr(
            "Choose one directly when the EXIF lens name is missing or differs from the database name.\n"
            "Common with adapters or third-party lenses."
        ))
        self.lens_override.currentTextChanged.connect(self._emit)
        lens_row.addWidget(self.lens_override, 1)
        section.add_layout(lens_row)

        # 번들 DB에 없는 신형·서드파티 렌즈는 사용자가 프로필을 넣어 넓힐 수
        # 있습니다. 폴더가 어디인지 모르면 쓸 수 없으니 여기서 열어 줍니다.
        db_row = QHBoxLayout()
        open_db = QPushButton(tr("Lens profile folder"))
        open_db.setToolTip(tr("Drop lensfun XML here to widen the list of recognised gear"))
        open_db.clicked.connect(self._open_lens_db_folder)
        db_row.addWidget(open_db)
        reload_db = QPushButton(tr("Reload lens DB"))
        reload_db.setToolTip(tr("Press this if you added XML while the app was running"))
        reload_db.clicked.connect(self._reload_lens_db)
        db_row.addWidget(reload_db)
        section.add_layout(db_row)

        self.lens_db_label = QLabel()
        self.lens_db_label.setStyleSheet(theme.hint_label())
        self.lens_db_label.setWordWrap(True)
        section.add_widget(self.lens_db_label)

        # 새 기종은 라이브러리에 색 정보가 없어 현상 색이 카메라와 다릅니다.
        # 이 PC에서 잰 보정값을 여기서 확인하고 지울 수 있게 합니다.
        self.calibration_button = QPushButton(tr("Manage camera color calibration"))
        self.calibration_button.setToolTip(tr(
            "View or delete this PC's calibration values, derived by comparing against the camera's built-in JPEG"
        ))
        self.calibration_button.clicked.connect(self._manage_calibration)
        section.add_widget(self.calibration_button)

        self.calibration_label = QLabel()
        self.calibration_label.setStyleSheet(theme.hint_label())
        self.calibration_label.setWordWrap(True)
        section.add_widget(self.calibration_label)

        # RAW가 아닐 때 왜 위 항목들이 잠겼는지 여기에 적습니다.
        self.source_note = QLabel()
        self.source_note.setStyleSheet(theme.hint_label())
        self.source_note.setWordWrap(True)
        self.source_note.setVisible(False)
        section.add_widget(self.source_note)

        self._camera = ("", "")   # set_camera로 채워집니다
        self._is_raw = True
        self._refresh_calibration_label()
        self._refresh_lens_db_label()

        divider = QLabel(tr("Manual correction"))
        divider.setStyleSheet("color: #9a9aa2; margin-top: 6px;")
        section.add_widget(divider)

        self._add_row(section, "optics.distortion", tr("Distortion"), -100, 100, gradient="mono",
                      tooltip=tr("Negative corrects barrel (convex), positive corrects pincushion (concave)"))
        self._add_row(section, "optics.vignetting", tr("Vignetting"), -100, 100,
                      gradient="exposure", tooltip=tr("Positive brightens the corners"))
        self._add_row(section, "optics.defringe_purple", tr("Remove purple fringing"),
                      0, 100, gradient="mono")
        self._add_row(section, "optics.defringe_green", tr("Remove green fringing"),
                      0, 100, gradient="mono")

        # 언저리 색은 렌즈와 장면마다 달라서 고정값으로는 잘 맞지 않습니다.
        # 실제 언저리를 찍어 그 색조를 기준으로 삼습니다.
        pick_row = QHBoxLayout()
        pick_row.addWidget(QLabel(tr("Sample colour")))
        for key, label in (("purple", tr("Purple")), ("green", tr("Green"))):
            button = QPushButton(f"💧 {label}")
            button.setCheckable(True)
            button.setToolTip(
                tr("Click the {label} fringing in the preview to set its hue").format(label=label)
            )
            button.clicked.connect(
                lambda checked, k=key: self._on_pick_toggled(k, checked)
            )
            self.defringe_pickers[key] = button
            pick_row.addWidget(button)
        section.add_layout(pick_row)

        self.defringe_hue_label = QLabel()
        self.defringe_hue_label.setStyleSheet(theme.hint_label(theme.TEXT_DIM))
        section.add_widget(self.defringe_hue_label)
        self._refresh_hue_label()

    def _refresh_lens_db_label(self) -> None:
        from ..core.develop.optics import database_coverage

        cameras, lenses = database_coverage()
        self.lens_db_label.setText(
            tr("Recognised: {cameras} bodies · {lenses} lenses").format(
                cameras=cameras, lenses=lenses))

    def _open_lens_db_folder(self) -> None:
        """탐색기로 렌즈 프로필 폴더를 엽니다 (없으면 만들어서)."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from ..core.develop.optics import ensure_user_lens_db_dir

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(ensure_user_lens_db_dir())))

    def _reload_lens_db(self) -> None:
        """XML을 넣은 뒤 다시 읽습니다. 목록도 새로 채웁니다."""
        from ..core.develop.optics import available_lenses, reload_database

        cameras, lenses = reload_database()
        current = self.lens_override.currentText()
        self.lens_override.clear()
        self.lens_override.addItems(["", *available_lenses()])
        self.lens_override.setCurrentText(current)
        self.lens_db_label.setText(
            tr("Reloaded — {cameras} bodies · {lenses} lenses").format(
                cameras=cameras, lenses=lenses)
        )

    def set_camera(self, make: str, model: str) -> None:
        """지금 보고 있는 사진의 바디. 보정 표시를 이 기종으로 좁힙니다."""
        self._camera = (make or "", model or "")
        self._refresh_calibration_label()

    def set_raw_source(self, is_raw: bool) -> None:
        """원본이 RAW인지에 따라 센서 기반 항목을 잠급니다.

        JPEG·HEIF에는 카메라가 이미 프로파일·기종 색·렌즈 보정을 적용해
        구워 넣었습니다. 한 번 더 걸면 이중 보정이 됩니다. 그래서 이 항목들은
        RAW가 아닐 때 **적용 자체가 안 됩니다**(raw_io.load_demosaiced 참고).

        화면에서도 꺼 둡니다. 만질 수 있게 두면 적용되는 줄 알고 값을
        맞추다가, 왜 아무 변화가 없는지 찾게 됩니다 — ROI 신뢰도에서 이미
        한 번 겪은 일입니다.

        색온도는 잠그지 않습니다. 절대 Kelvin 변환은 못 하지만 상대적인
        따뜻/차갑게는 그대로 되고, 실제로 손댈 일이 있습니다.
        """
        self._is_raw = bool(is_raw)

        self.optics_auto.setEnabled(is_raw)
        if not is_raw:
            self.optics_auto.setChecked(False)
        self.calibration_button.setEnabled(is_raw)

        self.source_note.setText(
            "" if is_raw else tr(
                "Non-RAW source: the camera already applied its profile, "
                "colour calibration and lens correction, so those are off."
            )
        )
        self.source_note.setVisible(not is_raw)
        self._refresh_calibration_label()

    def _refresh_calibration_label(self) -> None:
        """**지금 사진의 기종** 보정만 보여 줍니다.

        예전에는 저장된 기종을 전부 나열했습니다. 소니로 찍은 사진을 보는데
        'Canon EOS R6 Mark III: R 1.025 …'가 떠서, 이 사진에 그 값이 적용되는
        것처럼 읽혔습니다. 실제로는 아무 상관이 없습니다.
        """
        from ..core.develop import calibration as calib

        # RAW가 아니면 보정값이 있어도 적용되지 않습니다. 값을 보여 주면
        # 지금 걸려 있다고 읽힙니다.
        if not getattr(self, "_is_raw", True):
            self.calibration_label.setText("")
            return

        make, model = getattr(self, "_camera", ("", ""))
        if not model:
            self.calibration_label.setText("")
            return

        try:
            stored = calib.load(calib.camera_key(make, model))
        except Exception:  # noqa: BLE001
            stored = None

        if stored is None:
            self.calibration_label.setText(
                tr("{model}: no saved colour calibration").format(model=model))
            return
        if stored.is_neutral():
            self.calibration_label.setText(
                tr("{model}: no calibration needed").format(model=model))
            return
        b, g, r = stored.gain
        self.calibration_label.setText(
            tr("{model}: R {r:.3f} · G {g:.3f} · B {b:.3f} ({samples} frames)").format(
                model=model, r=r, g=g, b=b, samples=stored.samples)
        )

    def _manage_calibration(self) -> None:
        """저장된 보정을 보여 주고, 원하면 지웁니다.

        지우는 선택지가 있어야 합니다 — 라이브러리가 갱신되어 기종을 제대로
        지원하게 되면 이 보정이 오히려 방해가 됩니다.
        """
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        from ..core.develop import calibration as calib

        stored = calib.stored_cameras()
        if not stored:
            QMessageBox.information(
                self, tr("Camera color calibration"),
                tr("There is no saved calibration.\n\n"
                   "Opening a folder of photos from a camera the library doesn't know "
                   "offers to compute one.\n"
                   "Saved in: {path}").format(path=calib.calibration_dir()),
            )
            return

        labels = []
        for item in stored:
            b, g, r = item.gain
            state = tr("no calibration needed") if item.is_neutral() else \
                f"R {r:.3f} · G {g:.3f} · B {b:.3f}"
            labels.append(tr("{camera}  —  {state}  ({samples} frames)").format(
                camera=item.camera, state=state, samples=item.samples))

        choice, ok = QInputDialog.getItem(
            self, tr("Camera color calibration"),
            tr("Saved in: {path}\n\nChoose an item to delete:").format(
                path=calib.calibration_dir()),
            labels, 0, False,
        )
        if not ok:
            return

        target = stored[labels.index(choice)]
        confirm = QMessageBox.question(
            self, tr("Camera color calibration"),
            tr("Deletes the calibration for {camera}.\n"
               "Next time you open a folder from this camera, it will offer to recompute.").format(
                   camera=target.camera),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        if calib.remove(target.storage_key()):
            self._refresh_calibration_label()
            QMessageBox.information(self, tr("Camera color calibration"), tr("Deleted."))
        else:
            QMessageBox.warning(self, tr("Camera color calibration"), tr("Could not delete."))

    def _on_pick_toggled(self, key: str, checked: bool) -> None:
        """스포이드 모드 전환. 한 번에 하나만 켭니다."""
        if checked:
            for other, button in self.defringe_pickers.items():
                if other != key:
                    button.setChecked(False)
        self.pick_mode_changed.emit(key if checked else "")

    def set_sampled_hue(self, key: str, hue: int) -> None:
        """미리보기에서 찍은 색조를 반영합니다."""
        if key == "purple":
            self._defringe_hues["purple"] = hue
        elif key == "green":
            self._defringe_hues["green"] = hue
        for button in self.defringe_pickers.values():
            button.setChecked(False)
        self._refresh_hue_label()
        self._emit()

    def _refresh_hue_label(self) -> None:
        purple = self._defringe_hues["purple"]
        green = self._defringe_hues["green"]
        self.defringe_hue_label.setText(
            tr("Reference hue — purple {purple}° · green {green}°").format(
                purple=purple * 2, green=green * 2)
        )

    def set_lens_info(self, summary: str, found: bool) -> None:
        """루페가 조회한 렌즈 매칭 결과를 보여 줍니다."""
        self.lens_label.setText(
            f"{'✓' if found else '✗'} {summary}"
        )
        self.lens_label.setStyleSheet(
            theme.hint_label("#7a9a7a" if found else "#c9a06a")
        )

    def _build_effects(self) -> None:
        section = self._section("effects", tr("Effects"), "✦")
        self._add_row(section, "effects.grain_amount", tr("Grain"), 0, 100, gradient="mono")
        self._add_row(section, "effects.grain_size", tr("Grain size"), 1, 100, 25,
                      gradient="mono")
        self._add_row(section, "effects.vignette_amount", tr("Vignetting"), -100, 100,
                      gradient="exposure")
        self._add_row(section, "effects.vignette_midpoint", tr("Vignette midpoint"), 0, 100, 50,
                      gradient="mono")

    def _build_geometry(self) -> None:
        section = self._section("geometry", tr("Crop / straighten"), "⬚")

        self.crop_mode_button = QPushButton(tr("✂  Crop directly on the image"))
        self.crop_mode_button.setCheckable(True)
        self.crop_mode_button.setToolTip(tr(
            "When on, drag on the preview to set the crop.\n"
            "Drag a corner to resize, drag inside to move,\n"
            "double-click to reset to the whole frame."
        ))
        self.crop_mode_button.toggled.connect(self.crop_mode_changed.emit)
        section.add_widget(self.crop_mode_button)

        ratio_row = QHBoxLayout()
        ratio_row.addWidget(QLabel(tr("Ratio")))
        self.ratio_combo = QComboBox()
        for ratio in CropRatio:
            self.ratio_combo.addItem(_ratio_label(ratio), ratio)
        self.ratio_combo.currentIndexChanged.connect(self._emit)
        ratio_row.addWidget(self.ratio_combo, 1)
        section.add_layout(ratio_row)

        self._add_row(section, "geo.straighten", tr("Straighten"), -45, 45, decimals=1, suffix="°")
        self._add_row(section, "geo.crop_left", tr("Left"), 0, 100)
        self._add_row(section, "geo.crop_right", tr("Right"), 0, 100, 100)
        self._add_row(section, "geo.crop_top", tr("Top"), 0, 100)
        self._add_row(section, "geo.crop_bottom", tr("Bottom"), 0, 100, 100)

        buttons = QHBoxLayout()
        for label, tooltip, handler in (
            ("⟲ 90°", tr("Rotate 90° left"), lambda: self._rotate(-1)),
            ("⟳ 90°", tr("Rotate 90° right"), lambda: self._rotate(1)),
        ):
            button = QPushButton(label)
            button.setToolTip(tooltip)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        section.add_layout(buttons)

        flips = QHBoxLayout()
        self.flip_h = QCheckBox(tr("Flip horizontal"))
        self.flip_h.toggled.connect(self._emit)
        flips.addWidget(self.flip_h)
        self.flip_v = QCheckBox(tr("Flip vertical"))
        self.flip_v.toggled.connect(self._emit)
        flips.addWidget(self.flip_v)
        section.add_layout(flips)

        self._rotate_quarters = 0
        self.rotate_label = QLabel(tr("Rotation 0°"))
        self.rotate_label.setStyleSheet(theme.hint_label(theme.TEXT_DIM))
        section.add_widget(self.rotate_label)

    def _build_exif_strip(self) -> None:
        section = self._section("exif_strip", tr("Capture info strip"), "▤")

        self.strip_enabled = QCheckBox(tr("Add an info strip below the image"))
        self.strip_enabled.setToolTip(tr(
            "EXIF is usually stripped when you post to social media.\n"
            "Burned in as visible text, it survives wherever the photo goes."
        ))
        self.strip_enabled.toggled.connect(self._emit)
        section.add_widget(self.strip_enabled)

        background_row = QHBoxLayout()
        background_row.addWidget(QLabel(tr("Background")))
        self.strip_background = QComboBox()
        self.strip_background.addItem(tr("Black background / white text"), True)
        self.strip_background.addItem(tr("White background / black text"), False)
        self.strip_background.currentIndexChanged.connect(self._emit)
        background_row.addWidget(self.strip_background, 1)
        section.add_layout(background_row)

        self.strip_checks: dict[str, QCheckBox] = {}
        for key in STRIP_FIELDS:
            check = QCheckBox(_strip_field_label(key))
            check.toggled.connect(self._emit)
            self.strip_checks[key] = check
            section.add_widget(check)

        self._add_row(section, "strip.height", tr("Strip height"), 2, 20, 6, decimals=1,
                      suffix=" %", gradient="mono")

        self.strip_text = QLineEdit()
        self.strip_text.setPlaceholderText(tr("Text for the right side (artist name, etc.)"))
        self.strip_text.textChanged.connect(self._emit)
        section.add_widget(self.strip_text)

    def _build_watermark(self) -> None:
        section = self._section("watermark", tr("Watermark"), "◇")

        self.watermark_enabled = QCheckBox(tr("Add watermark"))
        self.watermark_enabled.toggled.connect(self._emit)
        section.add_widget(self.watermark_enabled)

        self.watermark_text = QLineEdit()
        self.watermark_text.setPlaceholderText(tr("Text (e.g. © 2026 Jane Doe)"))
        self.watermark_text.textChanged.connect(self._emit)
        section.add_widget(self.watermark_text)

        font_row = QHBoxLayout()
        font_row.addWidget(QLabel(tr("Font")))
        self.watermark_font = QComboBox()
        self.watermark_font.setMaxVisibleItems(24)
        self.watermark_font.addItem(tr("Default"), "")
        from ..core.develop.watermark import available_fonts

        for name, path in available_fonts():
            self.watermark_font.addItem(name, path)
        self.watermark_font.currentIndexChanged.connect(self._emit)
        font_row.addWidget(self.watermark_font, 1)
        section.add_layout(font_row)

        image_row = QHBoxLayout()
        self.watermark_image = QLineEdit()
        self.watermark_image.setPlaceholderText(tr("Or a PNG image"))
        self.watermark_image.textChanged.connect(self._emit)
        image_row.addWidget(self.watermark_image, 1)
        browse = QPushButton(tr("Browse"))
        browse.clicked.connect(self._browse_watermark)
        image_row.addWidget(browse)
        section.add_layout(image_row)

        position_row = QHBoxLayout()
        position_row.addWidget(QLabel(tr("Position")))
        self.watermark_position = QComboBox()
        for position in WatermarkPosition:
            self.watermark_position.addItem(_position_label(position), position)
        self.watermark_position.setCurrentIndex(3)  # 우하단
        self.watermark_position.currentIndexChanged.connect(self._emit)
        position_row.addWidget(self.watermark_position, 1)
        section.add_layout(position_row)

        self._add_row(section, "wm.opacity", tr("Opacity"), 0, 100, 70, gradient="mono")
        self._add_row(section, "wm.scale", tr("Size"), 1, 40, 5, suffix=" %", gradient="mono")
        self._add_row(section, "wm.margin", tr("Margin"), 0, 20, 3, suffix=" %", gradient="mono")
        self._add_row(section, "wm.offset_x", tr("Horizontal offset"), -50, 50, 0,
                      decimals=1, suffix=" %", gradient="mono",
                      tooltip=tr("Nudges left or right from the nine-grid position"))
        self._add_row(section, "wm.offset_y", tr("Vertical offset"), -50, 50, 0,
                      decimals=1, suffix=" %", gradient="mono")
        self._add_row(section, "wm.rotation", tr("Rotation"), -180, 180, 0, suffix="°",
                      gradient="mono")

        color_row = QHBoxLayout()
        color_row.addWidget(QLabel(tr("Color")))
        self._watermark_color = (255, 255, 255)
        self.watermark_color_button = QPushButton()
        self.watermark_color_button.setFixedHeight(22)
        self.watermark_color_button.setToolTip(tr("Text watermark colour"))
        self.watermark_color_button.clicked.connect(self._pick_watermark_color)
        color_row.addWidget(self.watermark_color_button, 1)
        section.add_layout(color_row)
        self._refresh_color_button()

        self.watermark_shadow = QCheckBox(tr("Shadow (legibility on light backgrounds)"))
        self.watermark_shadow.setChecked(True)
        self.watermark_shadow.toggled.connect(self._emit)
        section.add_widget(self.watermark_shadow)

    def _pick_watermark_color(self) -> None:
        from PySide6.QtWidgets import QColorDialog

        blue, green, red = self._watermark_color
        chosen = QColorDialog.getColor(
            QColor(red, green, blue), self, tr("Watermark colour")
        )
        if not chosen.isValid():
            return
        # 내부 표현은 OpenCV와 맞춰 BGR로 들고 있습니다
        self._watermark_color = (chosen.blue(), chosen.green(), chosen.red())
        self._refresh_color_button()
        self._emit()

    def _refresh_color_button(self) -> None:
        blue, green, red = self._watermark_color
        text_color = "#000" if (red + green + blue) > 380 else "#fff"
        self.watermark_color_button.setStyleSheet(
            f"QPushButton {{ background: rgb({red},{green},{blue});"
            f" color: {text_color}; border: 1px solid #555; border-radius: 3px; }}"
        )
        self.watermark_color_button.setText(f"#{red:02X}{green:02X}{blue:02X}")

    def _build_metadata(self) -> None:
        section = self._section("metadata", tr("EXIF metadata"), "⚙")

        self.metadata_enabled = QCheckBox(tr("Include EXIF on export"))
        self.metadata_enabled.setToolTip(tr(
            "Off by default. When you send a photo out, you often don't want\n"
            "your gear or the capture time going with it.\n"
            "Location data (GPS) is never written under any circumstances."
        ))
        self.metadata_enabled.toggled.connect(self._emit)
        section.add_widget(self.metadata_enabled)

        self.metadata_checks: dict[str, QCheckBox] = {}
        grid = QGridLayout()
        for index, key in enumerate(EXIF_FIELDS):
            check = QCheckBox(_exif_field_label(key))
            check.toggled.connect(self._emit)
            self.metadata_checks[key] = check
            grid.addWidget(check, index // 1, index % 1)
        section.add_layout(grid)

        self.metadata_artist = QLineEdit()
        self.metadata_artist.setPlaceholderText(tr("Artist name"))
        self.metadata_artist.textChanged.connect(self._emit)
        section.add_widget(self.metadata_artist)

        self.metadata_copyright = QLineEdit()
        self.metadata_copyright.setPlaceholderText(tr("Copyright notice"))
        self.metadata_copyright.textChanged.connect(self._emit)
        section.add_widget(self.metadata_copyright)

        note = QLabel(tr("GPS location data is never recorded"))
        note.setWordWrap(True)
        note.setStyleSheet(theme.hint_label("#7a9a7a"))
        section.add_widget(note)

    # ------------------------------------------------------------ 상호작용

    def _browse_watermark(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Watermark image"), "", tr("Images (*.png *.jpg *.jpeg)")
        )
        if path:
            self.watermark_image.setText(path)

    def _rotate(self, direction: int) -> None:
        self._rotate_quarters = (self._rotate_quarters + direction) % 4
        self.rotate_label.setText(tr("Rotation {deg}°").format(deg=self._rotate_quarters * 90))
        self._emit()

    def _on_hsl_channel(self) -> None:
        """색조/채도/광도 탭 전환 — 슬라이더 값을 현재 채널로 다시 채웁니다.

        _loading을 무조건 False로 되돌리면 set_settings 도중에 호출됐을 때
        남은 위젯 설정이 신호를 쏘게 됩니다. 이전 값을 복원해야 합니다.
        """
        previous = self._loading
        self._loading = True
        channel = self._hsl_channel_key()
        for band in HSL_BANDS:
            self.rows[f"hsl.{band}"].set_value(
                getattr(self._hsl_state[band], channel), silent=True
            )
        self._refresh_hsl_gradients()
        self._loading = previous

    def _hsl_channel_key(self) -> str:
        return ["hue", "saturation", "luminance"][self.hsl_channel.currentIndex()]

    # ------------------------------------------------------------ 값 읽기/쓰기

    def _slider_values(self, section: str) -> dict:
        """슬라이더 값을 해당 섹션의 필드 이름으로 모읍니다."""
        values = {}
        for key, (target, field, cast, scale) in SLIDER_BINDINGS.items():
            if target == section:
                values[field] = cast(self.rows[key].value() * scale)
        return values

    def settings(self) -> DevelopSettings:
        # 색온도는 손댔을 때만 절대값으로, 아니면 0(변화 없음)으로 저장합니다.
        temperature = (
            int(self.rows["basic.temperature"].value())
            if self._temperature_touched
            else 0
        )
        basic = BasicSettings(temperature=temperature, **self._slider_values("basic"))

        curve = CurveSettings(
            **self._slider_values("curve"),
            points_rgb=self._curve_points["rgb"],
            points_red=self._curve_points["red"],
            points_green=self._curve_points["green"],
            points_blue=self._curve_points["blue"],
        )

        detail = DetailSettings(
            **self._slider_values("detail"),
            noise_algorithm=self.noise_algorithm.currentData() or NoiseAlgorithm.NLMEANS,
        )

        # 현재 보고 있는 채널 값을 상태에 먼저 반영합니다
        self._sync_hsl_state()
        hsl = HSLSettings(bands=dict(self._hsl_state))

        grade = ColorGradeSettings(
            **self._slider_values("color_grade"),
            shadows=ColorGradeZone(*self.grade_zones["shadows"].values()),
            midtones=ColorGradeZone(*self.grade_zones["midtones"].values()),
            highlights=ColorGradeZone(*self.grade_zones["highlights"].values()),
        )

        optics = OpticsSettings(
            **self._slider_values("optics"),
            auto_enabled=self.optics_auto.isChecked(),
            auto_distortion=self.optics_auto_distortion.isChecked(),
            auto_vignetting=self.optics_auto_vignetting.isChecked(),
            auto_chromatic=self.optics_auto_chromatic.isChecked(),
            defringe_purple_hue=self._defringe_hues["purple"],
            defringe_green_hue=self._defringe_hues["green"],
            lens_override=self.lens_override.currentText().strip(),
        )

        effects = EffectSettings(**self._slider_values("effects"))

        geometry = GeometrySettings(
            **self._slider_values("geometry"),
            rotate_quarters=self._rotate_quarters,
            flip_horizontal=self.flip_h.isChecked(),
            flip_vertical=self.flip_v.isChecked(),
            ratio=self.ratio_combo.currentData() or CropRatio.FREE,
        )

        watermark = WatermarkSettings(
            **self._slider_values("watermark"),
            enabled=self.watermark_enabled.isChecked(),
            text=self.watermark_text.text(),
            image_path=self.watermark_image.text(),
            position=self.watermark_position.currentData() or WatermarkPosition.BOTTOM_RIGHT,
            color=self._watermark_color,
            shadow=self.watermark_shadow.isChecked(),
            font_path=self.watermark_font.currentData() or "",
        )

        metadata = MetadataSettings(
            enabled=self.metadata_enabled.isChecked(),
            include=tuple(k for k, c in self.metadata_checks.items() if c.isChecked()),
            artist=self.metadata_artist.text(),
            copyright=self.metadata_copyright.text(),
        )

        exif_strip = ExifStripSettings(
            **self._slider_values("exif_strip"),
            enabled=self.strip_enabled.isChecked(),
            dark_background=bool(self.strip_background.currentData()),
            include=tuple(k for k, c in self.strip_checks.items() if c.isChecked()),
            custom_text=self.strip_text.text(),
        )

        # 눈 버튼이 꺼진 섹션은 기본값으로 바꿔 내보냅니다. 위젯 값은 그대로
        # 두므로 다시 켜면 원래 값이 돌아옵니다.
        def masked(key: str, value, default):
            return value if self.sections[key].is_visible_section() else default

        return DevelopSettings(
            basic=masked("basic", basic, BasicSettings()),
            curve=masked("curve", curve, CurveSettings()),
            detail=masked("detail", detail, DetailSettings()),
            hsl=masked("hsl", hsl, HSLSettings()),
            color_grade=masked("color_grade", grade, ColorGradeSettings()),
            effects=masked("effects", effects, EffectSettings()),
            optics=masked("optics", optics, OpticsSettings()),
            geometry=masked("geometry", geometry, GeometrySettings()),
            watermark=masked("watermark", watermark, WatermarkSettings()),
            metadata=masked("metadata", metadata, MetadataSettings()),
            exif_strip=masked("exif_strip", exif_strip, ExifStripSettings()),
            masks=masked("masks", tuple(self._masks), ()),
        )

    def _sync_hsl_state(self) -> None:
        channel = self._hsl_channel_key()
        for band in HSL_BANDS:
            value = int(self.rows[f"hsl.{band}"].value())
            current = self._hsl_state[band]
            self._hsl_state[band] = HSLBand(
                **{**{"hue": current.hue, "saturation": current.saturation,
                      "luminance": current.luminance}, channel: value}
            )

    def set_settings(self, settings: DevelopSettings) -> None:
        self._loading = True
        grade, geometry = settings.color_grade, settings.geometry

        # settings()와 같은 표를 반대 방향으로 사용합니다
        for key, (section, field, _cast, scale) in SLIDER_BINDINGS.items():
            value = getattr(getattr(settings, section), field)
            self.rows[key].set_value(float(value) / scale, silent=True)

        # 색온도: 0(변화 없음)이면 as-shot 위치로, 절대값이면 그 값으로.
        kelvin = settings.basic.temperature
        self._temperature_touched = kelvin > 0
        self.rows["basic.temperature"].set_value(
            float(kelvin if kelvin > 0 else self._as_shot_kelvin), silent=True
        )

        for key, zone in (
            ("shadows", grade.shadows),
            ("midtones", grade.midtones),
            ("highlights", grade.highlights),
        ):
            self.grade_zones[key].set_values(zone.hue, zone.saturation, zone.luminance)

        algorithm_index = self.noise_algorithm.findData(settings.detail.noise_algorithm)
        if algorithm_index >= 0:
            self.noise_algorithm.setCurrentIndex(algorithm_index)

        optics = settings.optics
        # RAW가 아니면 자동 렌즈 보정은 되살리지 않습니다. 프리셋에 켜진 채로
        # 저장돼 있으면 잠긴 체크박스가 다시 켜졌고, 잠겨 있어도 isChecked()는
        # True라 settings()가 auto_enabled=True를 돌려줬습니다 — 끌 수도 없는
        # 상태로 이중 보정이 걸립니다. (core에서도 막지만 화면 표시도 맞아야
        # 사용자가 무엇이 걸렸는지 알 수 있습니다.)
        self.optics_auto.setChecked(optics.auto_enabled and self._is_raw)
        self.optics_auto_distortion.setChecked(optics.auto_distortion)
        self.optics_auto_vignetting.setChecked(optics.auto_vignetting)
        self.optics_auto_chromatic.setChecked(optics.auto_chromatic)
        self._defringe_hues = {
            "purple": optics.defringe_purple_hue,
            "green": optics.defringe_green_hue,
        }
        self._refresh_hue_label()
        self.lens_override.setCurrentText(optics.lens_override)

        self._hsl_state = dict(settings.hsl.bands)
        self._on_hsl_channel()

        self._curve_points = {
            "rgb": settings.curve.points_rgb,
            "red": settings.curve.points_red,
            "green": settings.curve.points_green,
            "blue": settings.curve.points_blue,
        }
        self._on_curve_channel()
        self._update_curve_parametric()

        self._rotate_quarters = geometry.rotate_quarters
        self.rotate_label.setText(tr("Rotation {deg}°").format(deg=self._rotate_quarters * 90))
        self.flip_h.setChecked(geometry.flip_horizontal)
        self.flip_v.setChecked(geometry.flip_vertical)
        index = self.ratio_combo.findData(geometry.ratio)
        if index >= 0:
            self.ratio_combo.setCurrentIndex(index)

        watermark = settings.watermark
        self.watermark_enabled.setChecked(watermark.enabled)
        self.watermark_text.setText(watermark.text)
        self.watermark_image.setText(watermark.image_path)
        position_index = self.watermark_position.findData(watermark.position)
        if position_index >= 0:
            self.watermark_position.setCurrentIndex(position_index)
        self.watermark_shadow.setChecked(watermark.shadow)
        font_index = self.watermark_font.findData(watermark.font_path)
        self.watermark_font.setCurrentIndex(font_index if font_index >= 0 else 0)
        self._watermark_color = tuple(watermark.color)
        self._refresh_color_button()

        metadata = settings.metadata
        self.metadata_enabled.setChecked(metadata.enabled)
        for key, check in self.metadata_checks.items():
            check.setChecked(key in metadata.include)
        self.metadata_artist.setText(metadata.artist)
        self.metadata_copyright.setText(metadata.copyright)

        strip = settings.exif_strip
        self.strip_enabled.setChecked(strip.enabled)
        self.strip_background.setCurrentIndex(0 if strip.dark_background else 1)
        for key, check in self.strip_checks.items():
            check.setChecked(key in strip.include)
        self.strip_text.setText(strip.custom_text)

        self._masks = list(settings.masks)
        self._rebuild_mask_list(select=0 if self._masks else None)

        self._loading = False
        self._update_section_markers()

    # ------------------------------------------------------------ 알림

    def _emit(self, *_) -> None:
        if self._loading:
            return
        self._update_section_markers()
        self.preset_bar.mark_modified()
        self.settings_changed.emit()

    def _update_section_markers(self) -> None:
        """손댄 섹션에 표시를 남깁니다. 접혀 있어도 뭘 만졌는지 보이게."""
        settings = self.settings()
        self.sections["basic"].mark_active(settings.basic != BasicSettings())
        self.sections["curve"].mark_active(not settings.curve.is_neutral())
        self.sections["detail"].mark_active(not settings.detail.is_neutral())
        self.sections["masks"].mark_active(any(not m.is_neutral() for m in self._masks))
        self.sections["hsl"].mark_active(not settings.hsl.is_neutral())
        self.sections["color_grade"].mark_active(not settings.color_grade.is_neutral())
        self.sections["effects"].mark_active(settings.effects != EffectSettings())
        self.sections["optics"].mark_active(not settings.optics.is_neutral())
        self.sections["geometry"].mark_active(not settings.geometry.is_neutral())
        self.sections["exif_strip"].mark_active(settings.exif_strip.is_active())
        self.sections["watermark"].mark_active(settings.watermark.is_active())
        self.sections["metadata"].mark_active(settings.metadata.enabled)

        # 손댄 섹션은 탭에도 표시합니다. 접혀 있어도 어디를 만졌는지 보입니다.
        for key, tab in getattr(self, "section_tabs", {}).items():
            section = self.sections.get(key)
            active = bool(section and getattr(section, "_active", False))
            tab.setStyleSheet(self._TAB_STYLE_ACTIVE if active else self._TAB_STYLE)

    def reset(self) -> None:
        self.set_settings(DevelopSettings())
        self.preset_bar.refresh()
        self.settings_changed.emit()
