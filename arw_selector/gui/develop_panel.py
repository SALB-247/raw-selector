"""Adjustment panel.

It follows the panel layout of Lightroom / Camera Raw - using the names and
the arrangement the user already knows is easier to learn. Sections are
collapsible, so only the ones you need stay open.

When a value changes it emits settings_changed and the preview is redrawn.
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
    MaskCombine,
    MaskType,
    MetadataSettings,
    NoiseAlgorithm,
    OpticsSettings,
    WatermarkPosition,
    WatermarkSettings,
)
from ..core.develop.mask_presets import MASK_PRESETS, build_mask
from ..core.presets import develop_presets, watermark_presets
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

# Default displayed value of the temperature slider. A fallback used only
# when the as-shot value is unknown.
DEFAULT_KELVIN = 5500
KELVIN_MIN, KELVIN_MAX = 2000, 12000

PANEL_MIN_CHARS = 46
"""Minimum width of the adjustment panel (in characters).

Nailed down in pixels it gets clipped whenever the font size or the DPI
changes. It is taken as the number of characters a Hangul label + the value
box + the reset button need to fit on one line, and the real width is
computed from the font metrics.

The width itself is **not fixed**. Only the minimum is held and the splitter
decides the rest, so when a widget is added to a section and the content
grows wider, the user can widen it instead of it being clipped.
"""

# Shared style for the buttons above the curve (clipping / reset)
_CURVE_BUTTON_STYLE = (
    "QPushButton { background: #2f2f35; color: #ccc; border: 1px solid #444;"
    " border-radius: 3px; font-size: 11px; }"
    "QPushButton:hover { background: #3a3a42; }"
    "QPushButton:checked { background: #4a5a75; color: #fff; border-color: #5a7bb0; }"
)


def _curve_channel_style(color: str) -> str:
    """Channel buttons (luminance/R/G/B) - filled with that channel's colour
    once selected.

    Why the padding is spelled out: without it the global BUTTON's 8px 16px
    applies as-is, and at the fixed width of 34px the content width becomes 0
    and the label is clipped entirely.
    """
    return (
        "QPushButton { background: #2f2f35; color: #aaa; border: 1px solid #444;"
        " border-radius: 3px; font-size: 11px; font-weight: bold;"
        " padding: 1px 2px; }"
        "QPushButton:hover { background: #3a3a42; }"
        f"QPushButton:checked {{ background: {color}; color: #16161a;"
        f" border-color: {color}; }}"
    )

# Which settings field each slider row corresponds to.
#
# Temperature (basic.temperature) is absolute Kelvin and has to distinguish
# "untouched" as 0, so it is handled separately rather than living in this
# table.
#
# Listing the read side (settings) and the write side (set_settings) by hand
# invites the mistake of adding a field to only one of them. That really
# happened, and values were not saved. Generating both directions from one
# table removes the possibility.
#
# Format: row key -> (settings section, field name, cast, scale)
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
    "detail.noise_passes": ("detail", "noise_passes", int, 1),
    "detail.noise_detail": ("detail", "noise_detail", int, 1),
    "detail.color_noise_reduction": ("detail", "color_noise_reduction", int, 1),
    "detail.color_noise_radius": ("detail", "color_noise_radius", int, 1),
    "detail.color_noise_shadow": ("detail", "color_noise_shadow", int, 1),
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

    # Crop is % in the UI and a 0~1 normalised value in the settings
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


# The labels below are text shown on screen, so they change with the
# language. Freezing them with tr() at module load time would break language
# switching (the same reason as gui/ordering_text.py), so the values live
# inside functions and are translated on every call. The tables in core
# (NOISE, HSL, EXIF, STRIP, mask presets) know nothing about Qt, so they are
# transcribed here.


def _ratio_label(ratio: CropRatio) -> str:
    return {
        CropRatio.FREE: tr("Free"),
        CropRatio.ORIGINAL: tr("Original ratio"),
    }.get(ratio, RATIO_LABELS[ratio])  # numeric ratios (1:1, 4:3 …) stay as-is


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


class _AdvancedGroup:
    """A titled fold holding the fine-tuning controls of one section.

    Exposes add_widget/add_layout so it can stand in for a section wherever
    a control is added - the caller does not need to know it is folded.
    """

    def __init__(self, title: str, tooltip: str = "") -> None:
        self._title = title
        self.toggle = QPushButton(f"{title}  ▸")
        self.toggle.setCheckable(True)
        self.toggle.setCursor(Qt.PointingHandCursor)
        self.toggle.setStyleSheet(
            "QPushButton { text-align: left; border: none; color: #aaa;"
            " padding: 4px 0; } QPushButton:checked { color: #ddd; }"
        )
        if tooltip:
            self.toggle.setToolTip(tooltip)
        self.box = QWidget()
        self.box.setVisible(False)
        self._layout = QVBoxLayout(self.box)
        self._layout.setContentsMargins(0, 0, 0, 4)
        self._layout.setSpacing(2)
        self.toggle.toggled.connect(self._on_toggled)

    def _on_toggled(self, shown: bool) -> None:
        self.box.setVisible(shown)
        self.toggle.setText(f"{self._title}  " + ("▾" if shown else "▸"))

    def add_widget(self, widget) -> None:
        self._layout.addWidget(widget)

    def add_layout(self, layout) -> None:
        self._layout.addLayout(layout)


class DevelopPanel(QWidget):
    """Exposes every adjustment parameter as collapsible sections."""

    settings_changed = Signal()
    crop_mode_changed = Signal(bool)
    pick_mode_changed = Signal(str)  # "purple" / "green" / "" (cleared)
    mask_overlay_changed = Signal()  # region display toggle / selection change
    mask_shape_changed = Signal()
    """The shape to draw on the image (radial / linear) has changed.

    Separate from the region display (mask_overlay_changed). The handles have
    to be visible without turning the red overlay on, and conversely toggling
    the overlay alone does not change the shape.
    """
    brush_mode_changed = Signal(bool)  # painting by brush on/off
    brush_changed = Signal()           # brush size / eraser (redraw circle)

    camera_match_requested = Signal()
    """The 'Match camera JPEG' button. Fitting needs both the original and the
    embedded JPEG, and the loupe holds both, so the panel only raises the
    request."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self._waking = False
        self._raw_state = None
        self.rows: dict[str, SliderRow] = {}
        self.sections: dict[str, CollapsibleSection] = {}
        self._section_labels: dict[str, tuple[str, str]] = {}
        self.defringe_pickers: dict[str, QPushButton] = {}
        self._defringe_hues = {"purple": 145, "green": 65}
        # Temperature is absolute Kelvin. Untouched it stays at as-shot
        # (= no change, stored as 0); once the user moves it, that absolute
        # value is stored.
        self._as_shot_kelvin = DEFAULT_KELVIN
        self._temperature_touched = False
        # HSL is 8 bands x 3 channels but there are only 8 sliders. The values
        # of the channels that are not shown are held here and swapped in when
        # the tab changes.
        self._hsl_state: dict[str, HSLBand] = {band: HSLBand() for band in HSL_BANDS}
        # Local adjustment masks. Per-frame editing state, so they are left out
        # of preset sharing and batch apply.
        self._masks: list[Mask] = []
        # Only the **minimum** width is set and the splitter is left to decide
        # the rest. Fixed, the right-hand side is silently clipped as soon as
        # the content grows a little (this happened three times). The minimum
        # is derived from the character width rather than pixels too, so it
        # holds the same proportion when the font or the DPI changes.
        self.setMinimumWidth(self._minimum_panel_width())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        # A preset does not carry the **values that differ per frame** -
        # straightening, watermark, masks (DevelopSettings.for_preset). On load
        # those keep their current values too (with_preset); otherwise every
        # time a preset is picked the straightening you set comes undone and
        # the watermark disappears.
        self.preset_bar = PresetBar(
            develop_presets(),
            collect=lambda: self.settings().for_preset().to_dict(),
            apply=lambda data: self.set_settings(
                self.settings().with_preset(DevelopSettings.from_dict(data))),
        )
        self.preset_bar.applied.connect(self.settings_changed.emit)
        preset_wrapper = QWidget()
        wrapper_layout = QVBoxLayout(preset_wrapper)
        wrapper_layout.setContentsMargins(8, 8, 8, 0)
        wrapper_layout.addWidget(self.preset_bar)

        # Camera look matching - a one-click that spans exposure, curve and
        # saturation, so it sits beside the preset row rather than in any one
        # section. Hidden inside a collapsed section it would never be found.
        self.match_camera_button = QPushButton(tr("Match camera JPEG"))
        self.match_camera_button.clicked.connect(self.camera_match_requested.emit)
        wrapper_layout.addWidget(self.match_camera_button)
        self._sync_match_camera_button(True)
        outer.addWidget(preset_wrapper)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet(f"QScrollArea {{ background: {theme.BACKGROUND}; }}")
        # The last safety net. When the screen is narrow or the font is large
        # and the content does not fit, it can be scrolled horizontally instead
        # of being clipped. Until now the right-hand side simply vanished in
        # that situation and could not be reached.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll = scroll

        # With twelve sections the scroll is long. Standing tabs along the edge
        # like a notebook index lets you go straight to the section you want.
        # The tabs sit on the left edge. On the right they would butt against
        # the value boxes and the scrollbar, splitting the eye, and they would
        # be the first thing pushed out and clipped as the panel narrows.
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
        # Everything above adjusts the photograph. The three below put
        # something onto the file that leaves - they were sitting in the same
        # run as the sliders with nothing to say they are a different kind of
        # thing.
        self._add_group_heading(tr("On the exported file"))
        self._build_exif_strip()
        self._build_watermark()
        self._build_metadata()
        self.body.addStretch(1)
        self._build_section_tabs()
        # Stop the value changing when the wheel passes over a dropdown or spin
        # box
        disable_wheel_in(self)

        # If a combo insists on being as wide as its 'longest item', the panel
        # is pushed out and the right-hand side is clipped. The lens list
        # (1218 entries) and the font list (620-odd) have very long names mixed
        # in and that really happened. The list popup opens wide, but the combo
        # itself is made to fit its slot.
        for combo in self.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(8)

        # Re-lay the scroll area now that the left tab strip exists. Leave this
        # out and the scroll area stays occupying the whole panel width, so the
        # content is pushed right by the tab strip width (28px) and clipped.
        self.layout().activate()

        reset = QPushButton(tr("Reset all"))
        reset.clicked.connect(self.reset)
        footer = QWidget()
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(8, 0, 8, 8)
        footer_layout.addWidget(reset)
        outer.addWidget(footer)

    # ------------------------------------------------------ section building

    def _add_group_heading(self, text: str) -> None:
        """A divider naming the run of sections that follows it."""
        heading = QLabel(text)
        heading.setStyleSheet(
            "QLabel { color: #7f7f8a; font-size: 11px; letter-spacing: 1px;"
            " padding: 10px 8px 2px 8px; border-top: 1px solid #3a3a42; }"
        )
        self.body.addWidget(heading)

    def _section(
        self, key: str, title: str, icon: str = "", expanded: bool = False,
        tooltip: str = "",
    ) -> CollapsibleSection:
        section = CollapsibleSection(f"{icon} {title}" if icon else title, expanded)
        if tooltip:
            section.header.setToolTip(tooltip)
        section.visibility_changed.connect(self._emit)
        self.sections[key] = section
        # Remember the icon and name to use on the side tab
        self._section_labels[key] = (icon or "•", title)
        self.body.addWidget(section)
        return section

    # The tabs are on the left edge, so the highlight line is drawn on the
    # inner side (the right).
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
        """Computes the minimum width from the character width (so it grows
        along with the font and the DPI).

        It is held down to no more than a set fraction of the screen width
        though. Once the minimum width exceeds the screen, the splitter cannot
        honour the demand and the panel comes up with its right-hand side
        clipped until the user drags it by hand (this really happened at FHD
        100%). Content that overflows can now be reached by horizontal
        scrolling, so lowering the minimum width does not cost access.
        """
        char = max(7, self.fontMetrics().averageCharWidth())
        scrollbar = self.style().pixelMetric(QStyle.PM_ScrollBarExtent)
        wanted = char * PANEL_MIN_CHARS + self._TAB_STRIP_WIDTH + scrollbar + 8

        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry().width()
            if available > 0:
                # Taking more than half leaves no room to see the photo
                wanted = min(wanted, int(available * 0.38))
        return max(320, wanted)

    def content_width(self) -> int:
        """The horizontal width the content inside the scroll can really use.

        The panel width minus the left tab strip and the vertical scrollbar.
        Tests compare this against the content's preferred width to catch
        clipping.
        """
        scrollbar = self.style().pixelMetric(QStyle.PM_ScrollBarExtent)
        width = self.width() or self.minimumWidth()
        return width - self._TAB_STRIP_WIDTH - scrollbar - 8

    def required_content_width(self) -> int:
        """The horizontal width the content needs to be shown properly.

        minimumSizeHint cannot be used - a button reports that it can shrink
        even if that mangles its text, so it comes back as 'it fits' while on
        screen it is clipped. The maximum of the preferred widths (sizeHint) is
        what has to be looked at.
        """
        needed = self.body.sizeHint().width()
        for child in self._content.findChildren(QWidget):
            needed = max(needed, child.sizeHint().width())
        return needed

    def _build_section_tabs(self) -> None:
        """Vertical tabs down the left edge. Pressing one expands that
        section and scrolls to it. (They used to sit on the right - see the
        comment at the body_row layout for why they moved.)"""
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
            # It sits on the left, so the highlight line is drawn on the right
            tab.setStyleSheet(self._TAB_STYLE)
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
        # Expanding re-lays the layout, so scroll only once the positions have
        # been computed
        QTimer.singleShot(0, lambda: self._scroll_section_to_top(section))

    def _scroll_section_to_top(self, section) -> None:
        """Scrolls so the section title lands at the **very top** of the
        visible area.

        ensureWidgetVisible was used before. That is the minimum scroll needed
        to 'merely be visible', so when a section is longer than the viewport
        the title runs off the top and roughly the middle is what you see. You
        cannot tell which section you opened. Setting the scroll position
        directly always shows the title first.
        """
        from PySide6.QtCore import QPoint

        content = self._scroll.widget()
        if content is None:
            return
        bar = self._scroll.verticalScrollBar()
        top = section.mapTo(content, QPoint(0, 0)).y()
        bar.setValue(min(top, bar.maximum()))

    def _add_row(self, section, key: str, *args, **kwargs) -> SliderRow:
        """Adds a slider row.

        `section` is anything that takes a widget - a CollapsibleSection or
        one of the advanced groups below, which is how a helper control goes
        under the fold without a second code path.
        """
        row = SliderRow(*args, **kwargs)
        row.value_changed.connect(self._emit)
        self.rows[key] = row
        section.add_widget(row)
        return row

    def _advanced_group(self, section: CollapsibleSection, title: str,
                        tooltip: str = "") -> "_AdvancedGroup":
        """A fold inside a section for the controls that fine-tune the ones
        above them.

        The Detail section had nine controls in a row, of which people reach
        for three. Hiding the rest keeps them one click away instead of
        making the section a wall.
        """
        group = _AdvancedGroup(title, tooltip)
        section.add_widget(group.toggle)
        section.add_widget(group.box)
        return group

    def _build_basic(self) -> None:
        section = self._section("basic", tr("Basic"), "◐", expanded=True)
        # Temperature is absolute Kelvin. Lower is cooler (blue), higher is
        # warmer (orange). The default (as-shot) is filled in when the image is
        # opened. An upper bound of 12000K covers every real shooting light
        # source from candlelight (~1800) to heavy overcast (~10000) while
        # keeping the slider steps fine enough for small adjustments.
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
                      tooltip=tr("Multiplies the whole image, the way a stop of "
                                 "light does. Raising it blows the highlights "
                                 "first - to lift only the middle, use "
                                 "Brightness."))
        self._add_row(section, "basic.brightness", tr("Brightness"), -100, 100,
                      gradient="exposure",
                      tooltip=tr("Bends the middle, holding white and black "
                                 "where they are. This is the one for lifting "
                                 "a backlit face without blowing the sky."))
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

        # A decode-stage option, so it is a checkbox rather than a slider. Why
        # it sits below the tone sliders: when the highlights are blown, this
        # area (Highlights, Whites) is where the user reaches, and that is when
        # it has to catch the eye.
        self.highlight_recovery = QCheckBox(tr("Highlight recovery (RAW)"))
        self.highlight_recovery.setToolTip(tr(
            "Rebuilds blown highlights from the sensor channels that did not\n"
            "clip — stage LEDs and spotlights keep structure instead of going white.\n"
            "The whole image comes out 1-1.5 stops darker (headroom is reserved);\n"
            "raise Exposure to taste — highlights now roll off instead of clipping.\n"
            "RAW only. Toggling re-develops the preview (a second or two)."
        ))
        self.highlight_recovery.toggled.connect(self._emit)
        section.add_widget(self.highlight_recovery)

    def _build_curve(self) -> None:
        section = self._section("curve", tr("Curve"), "∿")

        self.curve_editor = CurveEditor()
        self.curve_editor.points_changed.connect(self._on_curve_points)

        # The channel state is held in a combo (for compatibility with existing
        # code and tests), but the visible control is the buttons above the
        # curve.
        self.curve_channel = QComboBox()
        self.curve_channel.setVisible(False)
        for label, key in (
            ("RGB", "rgb"), (tr("Red"), "red"), (tr("Green"), "green"),
            (tr("Blue"), "blue")
        ):
            self.curve_channel.addItem(label, key)
        self.curve_channel.currentIndexChanged.connect(self._on_curve_channel)

        # Button row above the curve: clipping toggle + channel (luminance/
        # R/G/B) + reset
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
        # Unlike the clipping button this one has a fixed width, so the global
        # padding has to come off for the ↺ to be visible
        reset_curve.setStyleSheet(_CURVE_BUTTON_STYLE + theme.COMPACT_BUTTON)
        button_row.addWidget(reset_curve)
        section.add_layout(button_row)

        section.add_widget(self.curve_editor)

        hint = QLabel(tr("Click to add · drag to move · right-click/double-click to delete"))
        hint.setStyleSheet(theme.hint_label())
        # In English one line is wider than the panel. Wrapping it keeps it all
        # visible at narrow widths too (Korean is short enough to fit on one
        # line as it is).
        hint.setWordWrap(True)
        section.add_widget(hint)

        # The points are held per channel. The editor only shows one channel.
        self._curve_points: dict[str, tuple] = {
            "rgb": (), "red": (), "green": (), "blue": ()
        }

        self._add_row(section, "curve.highlights", tr("Highlights"), -100, 100,
                      gradient="highlights")
        self._add_row(section, "curve.lights", tr("Lights"), -100, 100, gradient="whites")
        self._add_row(section, "curve.darks", tr("Darks"), -100, 100, gradient="shadows")
        self._add_row(section, "curve.shadows", tr("Shadows"), -100, 100,
                      gradient="blacks")
        # Reflect the parametric bands on the curve graph.
        for key in ("curve.highlights", "curve.lights", "curve.darks", "curve.shadows"):
            self.rows[key].value_changed.connect(self._update_curve_parametric)

    def _update_curve_parametric(self, *_) -> None:
        """Hands the four parametric band values to the curve editor."""
        self.curve_editor.set_parametric(
            int(self.rows["curve.shadows"].value()),
            int(self.rows["curve.darks"].value()),
            int(self.rows["curve.lights"].value()),
            int(self.rows["curve.highlights"].value()),
        )

    def _on_temperature_touched(self, _value: float) -> None:
        """Once the user moves the temperature, start storing it as an absolute
        value."""
        if not self._loading:
            self._temperature_touched = True

    def set_as_shot_kelvin(self, kelvin: int) -> None:
        """Fills the as-shot temperature into the slider's default and current
        value when an image is opened.

        While it is still untouched the slider is put at as-shot, so that
        position means 'no change'.
        """
        self._as_shot_kelvin = int(kelvin)
        row = self.rows["basic.temperature"]
        row.default = float(kelvin)
        # Move the neutral point of the track to this frame's as-shot too. Only
        # then does the colour show that where the handle sits means "no
        # change".
        row.set_gradient(
            temperature_track_colors(self._as_shot_kelvin, KELVIN_MIN, KELVIN_MAX)
        )
        if not self._temperature_touched:
            row.set_value(float(kelvin), silent=True)

    def _on_curve_channel(self) -> None:
        """Channel tab switch - fills the editor with that channel's points."""
        index = self.curve_channel.currentIndex()
        channel = self.curve_channel.currentData()
        # Whether driven from a button or from code, keep the channel button
        # state in sync.
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
        """The loupe hands over the current image's histogram."""
        self.curve_editor.set_histogram(values)

    def _build_detail(self) -> None:
        section = self._section("detail", tr("Detail"), "◈")
        self._add_row(section, "detail.sharpen_amount", tr("Sharpening"), 0, 150,
                      gradient="mono")

        # Everything below fine-tunes the three sliders above it, so it goes
        # under a fold. Nine controls in a row made the section a wall and
        # buried Sharpening / Noise reduction / Color noise reduction in it.
        tuning = self._advanced_group(
            section, tr("Fine tuning"),
            tr("Sets the character of the sharpening and noise reduction "
               "above. The defaults are measured ones - worth a look when a "
               "particular photo needs it, not every time."))

        self._add_row(tuning, "detail.sharpen_radius", tr("Sharpen radius"),
                      0.5, 3.0, 1.0, decimals=1, gradient="mono",
                      tooltip=tr("How wide an edge the sharpening works on."))

        # Noise reduction differs greatly per method in the detail it leaves
        # and the time it takes. Putting the method above the slider makes it
        # visible what is being adjusted.
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
        tuning.add_layout(algorithm_row)

        self._add_row(section, "detail.noise_reduction", tr("Noise reduction"), 0, 100,
                      gradient="mono",
                      tooltip=tr("Luminance (brightness) noise. The strength adapts\n"
                                 "automatically to the photo's real noise, so the same\n"
                                 "value gives a similar result across different ISOs"))
        passes_row = self._add_row(
            tuning, "detail.noise_passes", tr("Passes"), 1, 4, 2,
            tooltip=tr(
                "Runs the noise reduction several times, weaker each pass.\n"
                "For the same amount of noise removed, several gentle passes\n"
                "hurt detail far less than one strong pass.\n\n"
                "Measured (A6700 ISO3200, strong-edge detail retained at\n"
                "equal noise removal):\n"
                "  70% removed:  1 pass 74% / 2 passes 97% / 3 passes 99%\n"
                "  80% removed:  1 pass cannot reach / 4 passes 85%\n\n"
                "The heavier the reduction (concert shots at ISO 2000+),\n"
                "the more passes matter. Time scales with the pass count.\n"
                "Applies to the non-local-means methods only."))
        passes_row.slider.setSingleStep(1)
        self._noise_passes_row = passes_row
        self.noise_algorithm.currentIndexChanged.connect(self._sync_noise_passes)
        self._sync_noise_passes()
        # Applying a strong reduction in a single pass collapses detail
        # (measured: at 70% removed, edge retention is 74% with 1 pass vs 97%
        # with 2, and 80% cannot be reached with 1 pass at all).
        # When the user raises the strength into that territory, the pass count
        # is raised to 2 **visibly** - changing it silently would break what
        # you see is what you get, and it can be put back with the slider.
        self.rows["detail.noise_reduction"].value_changed.connect(self._suggest_passes)
        self._add_row(tuning, "detail.noise_detail", tr("Detail preservation"), 0, 100, 50,
                      gradient="mono",
                      tooltip=tr("Restores the original where there is fine texture like\n"
                                 "hair or foliage. Flat sky or skin is left unaffected"))
        self._add_row(section, "detail.color_noise_reduction", tr("Color noise reduction"),
                      0, 100, gradient="mono",
                      tooltip=tr("Removes only colour mottling. It does not touch\n"
                                 "luminance, so there is no loss of detail"))
        self._add_row(tuning, "detail.color_noise_radius", tr("Color noise radius"),
                      0, 100, 50, gradient="mono",
                      tooltip=tr("How large a colour blob to catch. Blobs grow larger at\n"
                                 "higher ISO. Raising it also bleeds true colour edges"))
        self._add_row(tuning, "detail.color_noise_shadow", tr("Shadow color noise"),
                      0, 100, 100, gradient="mono",
                      tooltip=tr(
                          "Extra colour-noise suppression in dark areas only.\n"
                          "Colour blotches are worst in shadows (they get amplified\n"
                          "with the exposure), but matching the overall blur to the\n"
                          "shadows would bleed true colour edges in bright areas.\n\n"
                          "Measured on the shadows of five high-ISO files -\n"
                          "colour noise left in the shadow:\n"
                          "  slider        25    50    75   100\n"
                          "  this at 0     68%   46%   38%   25%\n"
                          "  this at 100   25%   15%   12%    9%\n"
                          "Bright areas keep their colour throughout. The top\n"
                          "of the slider spends colour in the dark instead\n"
                          "(85% at the middle, 65% at the top).\n\n"
                          "Works together with colour noise reduction — it does\n"
                          "nothing while that is 0"))
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
        self._add_row(tuning, "detail.face_priority", tr("Face priority"), 0, 100, 85,
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

    #: Past this strength, even two passes give up edges (measured:
    #: strength 80 goes 73~98% at two passes, 80~99% at three), so the
    #: slider is nudged one step further. Below it the default of two
    #: already holds the edges at 92~100%.
    PASS_PROMOTE_ABOVE = 70

    def _suggest_passes(self, *_args) -> None:
        """Raises the pass count to 3 when the reduction is pushed high
        (reversible). Two is already the default."""
        if self._loading:
            return
        passes_row = self.rows.get("detail.noise_passes")
        if passes_row is None or not self._noise_passes_row.isEnabled():
            return
        strength = self.rows["detail.noise_reduction"].value()
        if strength > self.PASS_PROMOTE_ABOVE and passes_row.value() <= 2:
            passes_row.set_value(3)

    def _sync_noise_passes(self, *_args) -> None:
        """The pass count applies to the non-local-means family only - it is
        locked for the other methods.

        Left touchable, the user assumes it applies, sets a value, and then
        goes hunting for why nothing changed (already lived through once with
        the JPEG locking).
        """
        algorithm = self.noise_algorithm.currentData()
        supported = algorithm in (NoiseAlgorithm.NLMEANS, NoiseAlgorithm.NLMEANS_HQ)
        self._noise_passes_row.setEnabled(supported)

    # ---------------------------------------------------------------- masks

    # Local adjustment sliders attached to the selected mask
    # (field, label, minimum, maximum, decimals, suffix)
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
        # The brush is painted by the user rather than detected, so it is kept
        # apart
        menu.addSection(tr("Manual"))
        brush_action = menu.addAction(tr("Brush (paint by hand)"))
        brush_action.setToolTip(tr("Drag over the image to paint just the area you want"))
        brush_action.triggered.connect(self._add_brush_mask)

        add_button.setMenu(menu)
        self._mask_menu = menu  # hold a reference or it gets garbage collected
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
        # The guidance goes in a tooltip only. Putting another label line here
        # would push the panel's minimum width up by that text width, and on a
        # narrow screen the right-hand side gets clipped.
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

        # Brush-only controls. They come on only when a BRUSH mask is selected.
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

        # Chooses who the face and eye masks apply to. It used to be fixed to
        # the single 'largest-area face', so when a passer-by in the front row
        # came out bigger than the main subject the wrong person was
        # brightened, and there was no way to treat several people at once.
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

        # Refining the area - subtract from this mask, add to it, or keep only
        # where they overlap. Only the parent's adjustments are used, so this
        # is about the area alone.
        self._build_mask_refine(controls)

        self._mask_adjust_rows: dict[str, SliderRow] = {}
        for field, label, minimum, maximum, decimals, suffix in self._mask_adjust_specs():
            row = SliderRow(label, minimum, maximum, default=0.0,
                            decimals=decimals, suffix=suffix)
            row.value_changed.connect(self._on_mask_adjust_changed)
            self._mask_adjust_rows[field] = row
            controls.addWidget(row)

        # The per-mask curve stays **collapsed**. It is an advanced feature for
        # gradation the sliders cannot reach, and leaving a curve editor
        # expanded for every mask buries the panel in curves and pushes the
        # commonly used sliders off screen.
        self._build_mask_curve(controls)

        section.add_widget(self.mask_controls)
        self.mask_controls.setEnabled(False)

    def _build_mask_refine(self, controls) -> None:
        """The list of refine pieces for the area, plus add/remove."""
        self.refine_box = QWidget()
        layout = QVBoxLayout(self.refine_box)
        layout.setContentsMargins(0, 4, 0, 2)
        layout.setSpacing(2)

        header = QHBoxLayout()
        title = QLabel(tr("Refine area"))
        title.setStyleSheet("color: #aaa;")
        header.addWidget(title)
        header.addStretch(1)

        add_refine = QPushButton(tr("Add"))
        add_refine.setToolTip(tr(
            "Build this mask's area from several pieces:\n"
            "Add — widen it · Subtract — take a part away ·\n"
            "Intersect — keep only where both overlap"
        ))
        menu = QMenu(add_refine)
        for mode, mode_label in ((MaskCombine.SUBTRACT, tr("Subtract")),
                                 (MaskCombine.INTERSECT, tr("Intersect")),
                                 (MaskCombine.ADD, tr("Add"))):
            section_menu = menu.addMenu(mode_label)
            for kind, kind_label in ((MaskType.RADIAL, tr("Radial")),
                                     (MaskType.LINEAR, tr("Linear")),
                                     (MaskType.BRUSH, tr("Brush")),
                                     (MaskType.FACE, tr("Face")),
                                     (MaskType.EYE, tr("Eye")),
                                     (MaskType.BACKGROUND, tr("Background")),
                                     (MaskType.SUBJECT, tr("Subject"))):
                action = section_menu.addAction(kind_label)
                action.triggered.connect(
                    lambda _=False, k=kind, m=mode: self._add_refine(k, m))
        add_refine.setMenu(menu)
        self._refine_menu = menu          # hold a reference or it gets GC'd
        header.addWidget(add_refine)

        self.refine_delete = QPushButton(tr("Remove"))
        self.refine_delete.clicked.connect(self._delete_refine)
        header.addWidget(self.refine_delete)
        layout.addLayout(header)

        self.refine_list = QListWidget()
        self.refine_list.setMaximumHeight(72)
        self.refine_list.setStyleSheet(
            "QListWidget { background: #232327; color: #ddd;"
            " border: 1px solid #3a3a40; border-radius: 3px; }"
        )
        self.refine_list.setToolTip(tr(
            "Pieces that shape this mask's area. The adjustments below apply\n"
            "to the finished area, not to each piece."
        ))
        self.refine_list.itemChanged.connect(self._on_refine_item_changed)
        self.refine_list.currentRowChanged.connect(
            lambda _row: self._load_selected_refine())
        layout.addWidget(self.refine_list)

        self.refine_feather = SliderRow(tr("Piece feather"), 0, 100,
                                        default=50, suffix=" %")
        self.refine_size = SliderRow(tr("Piece range"), 0, 200, default=100,
                                     suffix=" %")
        self.refine_invert = QCheckBox(tr("Invert piece"))
        for widget in (self.refine_feather, self.refine_size):
            widget.value_changed.connect(self._on_refine_changed)
            layout.addWidget(widget)
        self.refine_invert.toggled.connect(self._on_refine_changed)
        layout.addWidget(self.refine_invert)
        controls.addWidget(self.refine_box)

    def _build_mask_curve(self, controls) -> None:
        """The per-mask curve - collapsed by default."""
        from .curve_editor import CurveEditor

        self.mask_curve_toggle = QPushButton(tr("Tone curve  ▸"))
        self.mask_curve_toggle.setCheckable(True)
        self.mask_curve_toggle.setStyleSheet(
            "QPushButton { text-align: left; border: none; color: #aaa;"
            " padding: 4px 0; } QPushButton:checked { color: #ddd; }"
        )
        self.mask_curve_toggle.setToolTip(tr(
            "A tone curve for this mask's area only — for gradation the\n"
            "sliders cannot reach. Same editor as the global curve."
        ))
        self.mask_curve_toggle.toggled.connect(self._on_mask_curve_toggled)
        controls.addWidget(self.mask_curve_toggle)

        self.mask_curve_box = QWidget()
        curve_layout = QVBoxLayout(self.mask_curve_box)
        curve_layout.setContentsMargins(0, 0, 0, 4)
        curve_layout.setSpacing(2)

        # The channels are the same set as the global curve - combinations like
        # "cool down the background only" are the main use of a mask curve, so
        # luminance alone would be handing over only half of it.
        self.mask_curve_channel = QComboBox()
        self.mask_curve_channel.setVisible(False)
        for label, key in (("RGB", "rgb"), (tr("Red"), "red"),
                           (tr("Green"), "green"), (tr("Blue"), "blue")):
            self.mask_curve_channel.addItem(label, key)
        self.mask_curve_channel.currentIndexChanged.connect(
            self._on_mask_curve_channel)

        channel_row = QHBoxLayout()
        channel_row.setSpacing(3)
        channel_row.addStretch(1)
        self.mask_curve_buttons = QButtonGroup(self)
        self.mask_curve_buttons.setExclusive(True)
        for index, (label, color) in enumerate(
            ((tr("RGB"), "#dddddd"), ("R", "#e06060"),
             ("G", "#5cc264"), ("B", "#5c8cf0"))
        ):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setFixedSize(34, 22)
            button.setStyleSheet(_curve_channel_style(color))
            button.clicked.connect(
                lambda _=False, i=index: self.mask_curve_channel.setCurrentIndex(i))
            self.mask_curve_buttons.addButton(button, index)
            channel_row.addWidget(button)
        self.mask_curve_buttons.button(0).setChecked(True)
        curve_layout.addLayout(channel_row)

        self.mask_curve = CurveEditor()
        self.mask_curve.setMinimumHeight(150)
        self.mask_curve.points_changed.connect(self._on_mask_curve_points)
        curve_layout.addWidget(self.mask_curve)
        controls.addWidget(self.mask_curve_box)
        self.mask_curve_box.setVisible(False)

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
        """Adds an empty brush mask and goes straight into painting mode."""
        mask = Mask(
            kind=MaskType.BRUSH,
            adjust=LocalAdjustments(exposure=0.3),  # so a stroke shows at once
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
        """Brush radius (as a ratio of the image's short edge)."""
        return max(0.005, self.brush_size.value() / 100.0 / 2.0)

    def is_erasing(self) -> bool:
        return self.brush_erase.isChecked()

    def set_brush_bitmap(self, bitmap: str) -> None:
        """Applies what the loupe painted to the selected mask."""
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
                self.brush_paint.setChecked(False)  # painting mode off too
            self.mask_shape_changed.emit()
            return
        mask = self._masks[index]

        # The brush controls are visible only on a brush mask. Moving to
        # another mask has to turn painting mode off, or you paint into the
        # wrong mask.
        is_brush = mask.kind is MaskType.BRUSH
        self.brush_box.setVisible(is_brush)
        if not is_brush and self.brush_paint.isChecked():
            self.brush_paint.setChecked(False)
        previous = self._loading
        self._loading = True
        self.mask_size.set_value(float(mask.size), silent=True)
        # Range only means something for masks built from a shape. Linear,
        # background and brush have no shape to shrink, so it is made
        # untouchable to avoid confusion.
        self.mask_size.setEnabled(mask.kind in (MaskType.FACE, MaskType.EYE, MaskType.RADIAL))
        self.mask_opacity.set_value(float(mask.opacity), silent=True)
        self.mask_feather.set_value(float(mask.feather), silent=True)
        self.mask_invert.setChecked(mask.invert)

        # Choosing the face target only means something for face and eye masks
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
        self.mask_curve.set_points(
            self._mask_curve_points(self.mask_curve_channel.currentData()))
        # If this mask has a curve on it, do not leave it collapsed - something
        # changing the picture from a place you cannot see makes the cause
        # impossible to find.
        if not mask.adjust.curve.is_neutral() \
                and not self.mask_curve_toggle.isChecked():
            self.mask_curve_toggle.setChecked(True)
        self._loading = previous
        self._rebuild_refine_list(select=0 if mask.refine else None)
        # Emitted regardless of _loading. This is not a signal that a value
        # changed but a statement of "this is the mask now on screen", so
        # loading a frame is precisely when it must go out.
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
        # Range (size) is the size of the radial outline itself. Without
        # emitting, only the slider moves and the ellipse on screen stays put.
        self.mask_shape_changed.emit()
        if self.mask_overlay_check.isChecked():
            self.mask_overlay_changed.emit()

    def _on_mask_face_target_changed(self, *_) -> None:
        """Changes who the face mask applies to."""
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
        # Numbered from 1 for the human, from 0 internally
        params["index"] = max(0, self.mask_face_index.value() - 1)
        self._masks[index] = replace(mask, params=params)
        self._emit()
        if self.mask_overlay_check.isChecked():
            self.mask_overlay_changed.emit()

    def set_face_count(self, count: int) -> None:
        """How many faces were detected in this frame. To pick a number you
        have to know how many there are."""
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

    # -------------------------------------------------------- refining areas

    _REFINE_LABELS = {
        MaskCombine.ADD: "＋", MaskCombine.SUBTRACT: "－",
        MaskCombine.INTERSECT: "∩",
    }

    def _selected_refine_index(self) -> int:
        return self.refine_list.currentRow()

    def _add_refine(self, kind: MaskType, mode: MaskCombine) -> None:
        index = self._selected_mask_index()
        if not (0 <= index < len(self._masks)):
            return
        from dataclasses import replace

        mask = self._masks[index]
        piece = Mask(kind=kind, combine=mode, feather=50)
        if kind is MaskType.RADIAL:
            piece = replace(piece, params={"cx": 0.5, "cy": 0.5,
                                           "rx": 0.25, "ry": 0.25})
        elif kind is MaskType.LINEAR:
            piece = replace(piece, params={"x0": 0.5, "y0": 0.0,
                                           "x1": 0.5, "y1": 0.5})
        self._masks[index] = replace(mask, refine=(*mask.refine, piece))
        self._rebuild_refine_list(select=len(mask.refine))
        self._emit()
        if self.mask_overlay_check.isChecked():
            self.mask_overlay_changed.emit()

    def _delete_refine(self) -> None:
        mask_index = self._selected_mask_index()
        piece_index = self._selected_refine_index()
        if not (0 <= mask_index < len(self._masks)):
            return
        from dataclasses import replace

        mask = self._masks[mask_index]
        if not (0 <= piece_index < len(mask.refine)):
            return
        pieces = list(mask.refine)
        del pieces[piece_index]
        self._masks[mask_index] = replace(mask, refine=tuple(pieces))
        self._rebuild_refine_list(
            select=min(piece_index, len(pieces) - 1) if pieces else None)
        self._emit()
        if self.mask_overlay_check.isChecked():
            self.mask_overlay_changed.emit()

    def _rebuild_refine_list(self, select: int | None = None) -> None:
        mask_index = self._selected_mask_index()
        pieces = (self._masks[mask_index].refine
                  if 0 <= mask_index < len(self._masks) else ())
        previous = self._loading
        self._loading = True
        self.refine_list.clear()
        for piece in pieces:
            mark = self._REFINE_LABELS.get(piece.combine, "＋")
            item = QListWidgetItem(f"{mark}  {piece.kind.value}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if piece.enabled else Qt.Unchecked)
            self.refine_list.addItem(item)
        self._loading = previous
        if select is not None and 0 <= select < len(pieces):
            self.refine_list.setCurrentRow(select)
        self._load_selected_refine()

    def _load_selected_refine(self) -> None:
        mask_index = self._selected_mask_index()
        piece_index = self._selected_refine_index()
        pieces = (self._masks[mask_index].refine
                  if 0 <= mask_index < len(self._masks) else ())
        active = 0 <= piece_index < len(pieces)
        for widget in (self.refine_feather, self.refine_size,
                       self.refine_invert, self.refine_delete):
            widget.setEnabled(active)
        if not active:
            return
        piece = pieces[piece_index]
        previous = self._loading
        self._loading = True
        self.refine_feather.set_value(float(piece.feather), silent=True)
        self.refine_size.set_value(float(piece.size), silent=True)
        self.refine_size.setEnabled(
            piece.kind in (MaskType.FACE, MaskType.EYE, MaskType.RADIAL))
        self.refine_invert.setChecked(piece.invert)
        self._loading = previous

    def _on_refine_item_changed(self, item: QListWidgetItem) -> None:
        if self._loading:
            return
        mask_index = self._selected_mask_index()
        if not (0 <= mask_index < len(self._masks)):
            return
        from dataclasses import replace

        mask = self._masks[mask_index]
        piece_index = self.refine_list.row(item)
        if not (0 <= piece_index < len(mask.refine)):
            return
        pieces = list(mask.refine)
        pieces[piece_index] = replace(
            pieces[piece_index], enabled=item.checkState() == Qt.Checked)
        self._masks[mask_index] = replace(mask, refine=tuple(pieces))
        self._emit()
        if self.mask_overlay_check.isChecked():
            self.mask_overlay_changed.emit()

    def _on_refine_changed(self, *_) -> None:
        if self._loading:
            return
        mask_index = self._selected_mask_index()
        piece_index = self._selected_refine_index()
        if not (0 <= mask_index < len(self._masks)):
            return
        from dataclasses import replace

        mask = self._masks[mask_index]
        if not (0 <= piece_index < len(mask.refine)):
            return
        pieces = list(mask.refine)
        pieces[piece_index] = replace(
            pieces[piece_index],
            feather=int(self.refine_feather.value()),
            size=int(self.refine_size.value()),
            invert=self.refine_invert.isChecked(),
        )
        self._masks[mask_index] = replace(mask, refine=tuple(pieces))
        self._emit()
        if self.mask_overlay_check.isChecked():
            self.mask_overlay_changed.emit()

    # ---------------------------------------------------------- mask curves

    def _on_mask_curve_toggled(self, shown: bool) -> None:
        self.mask_curve_box.setVisible(shown)
        self.mask_curve_toggle.setText(
            tr("Tone curve  ▾") if shown else tr("Tone curve  ▸"))

    def _on_mask_curve_channel(self, _index: int) -> None:
        channel = self.mask_curve_channel.currentData()
        self.mask_curve.set_channel(channel)
        button = self.mask_curve_buttons.button(
            self.mask_curve_channel.currentIndex())
        if button is not None:
            button.setChecked(True)
        self.mask_curve.set_points(self._mask_curve_points(channel))

    def _mask_curve_points(self, channel: str) -> tuple:
        index = self._selected_mask_index()
        if not (0 <= index < len(self._masks)):
            return ()
        curve = self._masks[index].adjust.curve
        return {
            "rgb": curve.points_rgb, "red": curve.points_red,
            "green": curve.points_green, "blue": curve.points_blue,
        }.get(channel, ())

    def _on_mask_curve_points(self, points: tuple) -> None:
        if self._loading:
            return
        index = self._selected_mask_index()
        if not (0 <= index < len(self._masks)):
            return
        from dataclasses import replace

        field = {"rgb": "points_rgb", "red": "points_red",
                 "green": "points_green",
                 "blue": "points_blue"}[self.mask_curve_channel.currentData()]
        mask = self._masks[index]
        curve = replace(mask.adjust.curve, **{field: points})
        self._masks[index] = replace(
            mask, adjust=replace(mask.adjust, curve=curve))
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
        """The overlay mask for the loupe to draw. None when the display is off
        or nothing is selected."""
        if not self.mask_overlay_check.isChecked():
            return None
        index = self._selected_mask_index()
        return self._masks[index] if 0 <= index < len(self._masks) else None

    SHAPE_KINDS = (MaskType.RADIAL, MaskType.LINEAR)
    """Masks that can be dragged directly on the image. For the rest,
    detection or the brush decides the position."""

    def shape_mask(self) -> "Mask | None":
        """The mask whose handles go on the image. None if there is none.

        Independent of the region display checkbox - the handles themselves are
        visible so you can tell what you are grabbing, and having to turn the
        red overlay on before you could move it would be the stranger thing.
        """
        index = self._selected_mask_index()
        if not (0 <= index < len(self._masks)):
            return None
        mask = self._masks[index]
        return mask if mask.kind in self.SHAPE_KINDS else None

    def set_mask_params(self, params: dict, *, silent: bool = False) -> None:
        """Swaps in the normalised parameters of the selected mask.

        Called with silent=True while dragging. Emitting settings_changed on
        every pixel makes the heavy re-render schedule and cancel over and
        over, and the control cannot keep up (the same reason as crop
        dragging). One notification after the drag ends is enough.
        """
        index = self._selected_mask_index()
        if not (0 <= index < len(self._masks)):
            return
        from dataclasses import replace

        self._masks[index] = replace(self._masks[index], params=dict(params))
        if not silent:
            self._emit()

    def _build_hsl(self) -> None:
        section = self._section(
            "hsl", tr("Color mixer"), "◎",
            tooltip=tr("Adjusts one colour band at a time - the reds, the blues. "
                       "For tinting the shadows and highlights instead, use "
                       "Color grading."))

        self.hsl_channel = QComboBox()
        self.hsl_channel.addItems([tr("Hue"), tr("Saturation"), tr("Luminance")])
        self.hsl_channel.currentIndexChanged.connect(self._on_hsl_channel)
        section.add_widget(self.hsl_channel)

        for band in HSL_BANDS:
            self._add_row(section, f"hsl.{band}", _hsl_band_label(band), -100, 100)
        self._refresh_hsl_gradients()

    def _refresh_hsl_gradients(self) -> None:
        """Gives each band the track colour of its own hue range.

        Painted all with the same rainbow, you cannot tell what you are
        touching. The meaning also differs by channel (hue/saturation/
        luminance), so it is repainted each time.
        """
        channel = self._hsl_channel_key()
        for band in HSL_BANDS:
            self.rows[f"hsl.{band}"].set_gradient(
                hsl_band_colors(HSL_BAND_CENTERS[band], channel)
            )

    def _build_color_grade(self) -> None:
        section = self._section(
            "color_grade", tr("Color grading"), "◑",
            tooltip=tr("Tints the shadows, midtones and highlights separately. "
                       "For changing one colour wherever it appears, use "
                       "Color mixer."))

        # Splitting hue and saturation into separate sliders makes "which
        # direction, and how far" hard to grasp. Dragging a single point on a
        # wheel is far quicker.
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

        # New or third-party lenses missing from the bundled DB can be covered
        # by the user dropping in a profile. That is unusable if you do not
        # know where the folder is, so it is opened from here.
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

        # For a new body the library has no colour information, so the
        # developed colour differs from the camera's. The calibration values
        # measured on this PC can be inspected and deleted here.
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

        # Says here why the items above are locked when the source is not RAW.
        self.source_note = QLabel()
        self.source_note.setStyleSheet(theme.hint_label())
        self.source_note.setWordWrap(True)
        self.source_note.setVisible(False)
        section.add_widget(self.source_note)

        self._camera = ("", "")   # filled in by set_camera
        self._is_raw = True
        self._refresh_calibration_label()
        self._refresh_lens_db_label()

        divider = QLabel(tr("Manual correction"))
        divider.setStyleSheet("color: #9a9aa2; margin-top: 6px;")
        section.add_widget(divider)

        self._add_row(section, "optics.distortion", tr("Distortion"), -100, 100, gradient="mono",
                      tooltip=tr("Negative corrects barrel (convex), positive corrects pincushion (concave)"))
        # Named apart from the Effects vignette on purpose. The same word
        # sat in both sections meaning opposite things - this one takes the
        # lens's corner falloff out, that one puts darkening in.
        self._add_row(section, "optics.vignetting", tr("Correct vignetting"),
                      -100, 100, gradient="exposure",
                      tooltip=tr("Evens out the corner darkening the lens itself "
                                 "leaves. Positive brightens the corners: 100 "
                                 "raises them 2.2 stops, 50 exactly half - the "
                                 "scale is stops, not display values. To darken "
                                 "the corners on purpose, use Vignette under "
                                 "Effects."))
        self._add_row(section, "optics.defringe_purple", tr("Remove purple fringing"),
                      0, 100, gradient="mono")
        self._add_row(section, "optics.defringe_green", tr("Remove green fringing"),
                      0, 100, gradient="mono")

        # The fringe colour differs per lens and per scene, so a fixed value
        # rarely fits. Sampling the real fringing gives the reference hue.
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
        """Opens the lens profile folder in the file browser (creating it if it
        is missing)."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from ..core.develop.optics import ensure_user_lens_db_dir

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(ensure_user_lens_db_dir())))

    def _reload_lens_db(self) -> None:
        """Re-reads after XML has been dropped in. Refills the list too."""
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
        """The body of the photo currently shown. Narrows the calibration
        display to this model."""
        self._camera = (make or "", model or "")
        self._refresh_calibration_label()

    def set_raw_source(self, is_raw: bool) -> None:
        """Locks the sensor-based items depending on whether the source is RAW.

        With JPEG and HEIF the camera has already applied and baked in the
        profile, the model colour and the lens correction. Applying them once
        more is a double correction. So for a non-RAW source these items are
        **not applied at all** (see raw_io.load_demosaiced).

        They are turned off on screen too. Left touchable, the user assumes
        they apply, sets values, and then goes hunting for why nothing changed
        - already lived through once with the ROI trust.

        Temperature is not locked. Absolute Kelvin conversion is impossible,
        but relative warmer/cooler still works and there really are occasions
        to reach for it.
        """
        self._is_raw = bool(is_raw)

        self.optics_auto.setEnabled(is_raw)
        if not is_raw:
            self.optics_auto.setChecked(False)
        # Highlight recovery only holds up with sensor data - JPEG and HEIF are
        # already the camera's clipped, baked result, so there is no channel
        # left to rebuild from.
        self.highlight_recovery.setEnabled(is_raw)
        if not is_raw:
            self.highlight_recovery.setChecked(False)
        self.calibration_button.setEnabled(is_raw)
        # Camera look matching is sensor-based too - with JPEG and HEIF the
        # file itself is already the camera's rendering, so there is nothing
        # separate to match against.
        self._sync_match_camera_button(is_raw)

        self.source_note.setText(
            "" if is_raw else tr(
                "Non-RAW source: the camera already applied its profile, "
                "colour calibration and lens correction, so those are off."
            )
        )
        self.source_note.setVisible(not is_raw)
        self._refresh_calibration_label()

    def _sync_match_camera_button(self, is_raw: bool) -> None:
        """Whether the match button is enabled, and its tooltip. When locking
        it, the tooltip states the reason.

        Greyed out with nothing said, it reads as broken - already lived
        through with the automatic lens correction, so the same rule is
        followed here.
        """
        self.match_camera_button.setEnabled(is_raw)
        self.match_camera_button.setToolTip(
            tr(
                "Fits exposure, tone curve and saturation so the develop\n"
                "starts close to this shot's embedded camera JPEG.\n"
                "The fit lands on the sliders as ordinary values, so\n"
                "everything stays editable. The camera's local tone mapping\n"
                "cannot be copied by global controls, so small differences remain."
            ) if is_raw else tr(
                "JPEG and HEIF are already the camera's own rendering —\n"
                "there is nothing to match against."
            )
        )

    def _refresh_calibration_label(self) -> None:
        """Shows only the calibration for **the model of the current photo**.

        It used to list every stored model. Looking at a photo shot on a Sony,
        'Canon EOS R6 Mark III: R 1.025 …' would appear, and it read as if that
        value applied to this photo. In fact it has nothing to do with it.
        """
        from ..core.develop import calibration as calib

        # Without RAW the calibration is not applied even when one exists.
        # Showing the value would read as it being in effect right now.
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
        """Shows the stored calibrations and deletes one on request.

        A delete option has to exist - once the library is updated and supports
        the model properly, this calibration gets in the way instead.
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
        """Switches the eyedropper mode. Only one is on at a time."""
        if checked:
            for other, button in self.defringe_pickers.items():
                if other != key:
                    button.setChecked(False)
        self.pick_mode_changed.emit(key if checked else "")

    def set_sampled_hue(self, key: str, hue: int) -> None:
        """Applies the hue sampled from the preview."""
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
        """Shows the lens matching result the loupe looked up."""
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
        self._add_row(section, "effects.vignette_amount", tr("Vignette"), -100, 100,
                      gradient="exposure",
                      tooltip=tr("Darkens the corners on purpose, to hold the eye "
                                 "in the middle. To take out the darkening the "
                                 "lens leaves, use Correct vignetting under "
                                 "Optics."))
        self._add_row(section, "effects.vignette_midpoint", tr("Vignette midpoint"),
                      0, 100, 50, gradient="mono",
                      tooltip=tr("How far out from the centre the darkening starts."))

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

        # The watermark uses its own presets. Mixed into the colour adjustment
        # presets, putting the same watermark on several looks would mean
        # creating a new preset every time.
        self.watermark_preset_bar = PresetBar(
            watermark_presets(),
            collect=lambda: {"watermark": self.settings().to_dict()["watermark"]},
            apply=self._apply_watermark_preset,
        )
        self.watermark_preset_bar.applied.connect(self.settings_changed.emit)
        section.add_widget(self.watermark_preset_bar)

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
        self.watermark_position.setCurrentIndex(3)  # bottom-right
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
        # The internal representation is BGR, to match OpenCV
        self._watermark_color = (chosen.blue(), chosen.green(), chosen.red())
        self._refresh_color_button()
        self._emit()

    def _apply_watermark_preset(self, data) -> None:
        """Lays a watermark preset over the current adjustments - only the
        watermark changes.

        The preset file holds just the one piece, {"watermark": {...}}. Read
        wholesale as DevelopSettings, every other item is overwritten with the
        defaults.
        """
        from dataclasses import replace as _replace

        incoming = DevelopSettings.from_dict(data if isinstance(data, dict)
                                             else {})
        self.set_settings(_replace(self.settings(),
                                   watermark=incoming.watermark))

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

    # --------------------------------------------------------- interaction

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
        """Hue/saturation/luminance tab switch - refills the slider values from
        the current channel.

        Resetting _loading to False unconditionally makes the remaining widget
        assignments fire signals when this is called in the middle of
        set_settings. The previous value has to be restored.
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

    # ----------------------------------------------- reading/writing values

    def _slider_values(self, section: str) -> dict:
        """Collects the slider values under that section's field names."""
        values = {}
        for key, (target, field, cast, scale) in SLIDER_BINDINGS.items():
            if target == section:
                values[field] = cast(self.rows[key].value() * scale)
        return values

    def settings(self, *, gated: bool = True) -> DevelopSettings:
        """The panel's values.

        With gated=False the section eyes are ignored and the widget values
        come back as they stand. That view is what decides when an eye has
        to wake up - asking the gated view would never work, because a
        section that is off reports its defaults and so could never be seen
        to change.
        """
        # Temperature is stored as an absolute value only once touched,
        # otherwise as 0 (no change).
        temperature = (
            int(self.rows["basic.temperature"].value())
            if self._temperature_touched
            else 0
        )
        basic = BasicSettings(
            temperature=temperature,
            highlight_recovery=self.highlight_recovery.isChecked(),
            **self._slider_values("basic"),
        )

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

        # Push the currently shown channel's values into the state first
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

        # A section whose eye button is off goes out as the default values. The
        # widget values are left alone, so turning it back on brings the
        # original values back.
        def masked(key: str, value, default):
            if not gated:
                return value
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

        # The same table settings() uses, run in the opposite direction
        for key, (section, field, _cast, scale) in SLIDER_BINDINGS.items():
            value = getattr(getattr(settings, section), field)
            self.rows[key].set_value(float(value) / scale, silent=True)

        # Temperature: 0 (no change) goes to the as-shot position, an absolute
        # value goes to that value.
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

        # Not restored when the source is not RAW - the same reason as
        # optics_auto.
        self.highlight_recovery.setChecked(
            settings.basic.highlight_recovery and self._is_raw)

        optics = settings.optics
        # Automatic lens correction is not restored when the source is not RAW.
        # If a preset had been saved with it on, the locked checkbox came back
        # on, and even locked isChecked() is True, so settings() handed back
        # auto_enabled=True - a double correction applied in a state you cannot
        # even turn off. (core blocks it too, but the screen has to agree or
        # the user cannot tell what is applied.)
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

        # The eyes come from the values themselves. A photo with nothing on
        # it opens with every section switched off, which is what makes the
        # "(off)" marks mean anything; a photo that has been edited opens
        # with exactly the sections that hold values switched on.
        #
        # Switching them all off regardless would be silently destructive -
        # an off section reports its defaults, so opening an edited photo
        # would throw the edit away.
        for key, section in self.sections.items():
            section.set_section_visible(
                self._section_has_values(key, settings, self._masks))

        self._loading = False
        self._raw_state = self.settings(gated=False)
        self._update_section_markers()

    # ------------------------------------------------------- notification

    _SECTION_FIELDS = {
        "basic": "basic", "curve": "curve", "detail": "detail",
        "hsl": "hsl", "color_grade": "color_grade", "effects": "effects",
        "optics": "optics", "geometry": "geometry",
        "watermark": "watermark", "metadata": "metadata",
        "exif_strip": "exif_strip",
    }

    @staticmethod
    def _section_has_values(key: str, settings: DevelopSettings, masks) -> bool:
        """Whether this section holds anything other than its defaults."""
        if key == "masks":
            return any(not mask.is_neutral() for mask in masks)
        value = getattr(settings, DevelopPanel._SECTION_FIELDS[key])
        if key == "metadata":
            return bool(value.enabled)
        if key in ("exif_strip", "watermark"):
            return bool(value.is_active())
        if hasattr(value, "is_neutral"):
            return not value.is_neutral()
        return value != type(value)()

    def _wake_edited_sections(self) -> None:
        """Turns a section's eye on as soon as one of its values moves.

        A section that is off hands back its defaults, so an edit made
        inside it would be dropped on the way out. Waking it is what makes
        "start off, switch on when you touch a dial" safe.
        """
        raw = self.settings(gated=False)
        previous, self._raw_state = self._raw_state, raw
        if previous is None:
            return
        for key in self.sections:
            section = self.sections[key]
            if section.is_visible_section():
                continue
            field = self._SECTION_FIELDS.get(key)
            moved = (raw.masks != previous.masks if key == "masks"
                     else getattr(raw, field) != getattr(previous, field))
            if moved:
                section.set_section_visible(True)

    def _emit(self, *_) -> None:
        if self._loading or self._waking:
            return
        self._waking = True
        try:
            self._wake_edited_sections()
        finally:
            self._waking = False
        self._update_section_markers()
        self.preset_bar.mark_modified()
        self.settings_changed.emit()

    def _update_section_markers(self) -> None:
        """Leaves a marker on the sections that were touched, so what was
        changed is visible even while they are collapsed."""
        # The ungated view, so a section that is switched off but holds
        # values still shows its dot - "* (off)" is a real state.
        settings = self.settings(gated=False)
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

        # Touched sections are marked on the tabs too, so where you changed
        # something is visible even while they are collapsed.
        for key, tab in getattr(self, "section_tabs", {}).items():
            section = self.sections.get(key)
            active = bool(section and getattr(section, "_active", False))
            tab.setStyleSheet(self._TAB_STYLE_ACTIVE if active else self._TAB_STYLE)

    def reset(self) -> None:
        self.set_settings(DevelopSettings())
        self.preset_bar.refresh()
        self.settings_changed.emit()
