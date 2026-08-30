"""Shared widgets used by the adjustment panel.

With dozens of sliders, building them one line at a time by hand is
unmanageable. The label, slider, number entry and reset are bundled into one
piece and reused.
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .i18n import tr


def disable_wheel(widget: QWidget) -> QWidget:
    """Stops the wheel changing the value and passes the scroll on to the
    parent (the panel).

    Running the wheel down a long panel drags the cursor over combo boxes and
    spin boxes. If the value changes on its own there, the adjustment goes
    wrong without you even knowing what changed. The sliders already had the
    same handling; the dropdowns had been left out.

    The focus policy is lowered along with it. With StrongFocus you can only
    work it from the keyboard after clicking to give it focus, so there is no
    accident from brushing past.
    """
    widget.setFocusPolicy(Qt.StrongFocus)
    widget.wheelEvent = lambda event: event.ignore()
    return widget


def disable_wheel_in(parent: QWidget) -> None:
    """Applies the wheel block to every combo box and spin box in the panel.

    It is easy to miss one each time a widget is added, so everything is swept
    in a single pass once it is all built.
    """
    # PySide6's findChildren does not take a tuple of types, so each kind is
    # swept separately. QAbstractSpinBox alone catches both QSpinBox and
    # QDoubleSpinBox.
    from PySide6.QtWidgets import QAbstractSpinBox, QComboBox

    for widget_type in (QComboBox, QAbstractSpinBox):
        for child in parent.findChildren(widget_type):
            disable_wheel(child)


# Slider track gradients. The colour shows at once which value is being
# adjusted. The same direction as Lightroom is used (negative on the left).
GRADIENTS = {
    "temperature": ("#4a7fd4", "#e8c14a"),   # cool <-> warm
    "tint": ("#4ac46a", "#d24ac4"),          # green <-> magenta
    "exposure": ("#101013", "#f5f5f5"),
    "contrast": ("#6a6a70", "#e8e8ea"),
    "highlights": ("#5a5a60", "#ffffff"),
    "shadows": ("#101013", "#9a9aa0"),
    "whites": ("#7a7a80", "#ffffff"),
    "blacks": ("#000000", "#8a8a90"),
    # Saturation/vibrance mean "the colour gets deeper", not that it heads for
    # one particular colour. They used to end at a single red/orange each, so
    # raising them looked like it turned things red. Changed towards several
    # colours coming alive out of grey.
    "saturation": ("#8a8a8a", "#7a9ad0", "#7ac07a", "#d0c060", "#d06a6a"),
    "vibrance": ("#8a8a8a", "#93a8c8", "#9dbd93", "#c8bd8a", "#c88a8a"),
    "hue": (
        "#ff0000", "#ffff00", "#00ff00", "#00ffff", "#0000ff", "#ff00ff", "#ff0000"
    ),
    "mono": ("#3a3a40", "#8a8ab0"),
}


def hsl_band_colors(center_hue: int, channel: str) -> tuple[str, ...]:
    """Track colours for the HSL band sliders.

    Painting every band with the same rainbow leaves you unable to tell what
    you are working on right now. Like Lightroom, every band uses the gradient
    belonging to its own hue range.

    - Hue: narrow, only as far as the neighbouring hues (for red,
      magenta <-> red <-> orange)
    - Saturation: grey -> that colour
    - Luminance: that colour dark -> that colour bright

    center_hue is on OpenCV's scale (0~179), so it is doubled for QColor.
    """
    from PySide6.QtGui import QColor

    hue = (center_hue * 2) % 360

    def css(h: int, s: int, v: int) -> str:
        return QColor.fromHsv(h % 360, s, v).name()

    if channel == "hue":
        # Only 30° either side - it shows where dragging the slider really goes
        return (css(hue - 30, 230, 235), css(hue, 230, 235), css(hue + 30, 230, 235))
    if channel == "saturation":
        # Saturation only. It used to raise the value 150->235 along with it,
        # so raising saturation looked like it brightened as well.
        return (css(hue, 20, 215), css(hue, 245, 215))
    # Luminance - value only. Lowering saturation 200->120 along with it makes
    # raising luminance look like the colour is draining away.
    return (css(hue, 170, 55), css(hue, 170, 250))


def temperature_track_colors(
    as_shot: int, low: int = 2000, high: int = 12000
) -> tuple[tuple[float, str], ...]:
    """The colour temperature track. Grey sits at the neutral (as-shot) point.

    Colour temperature is absolute Kelvin, so "no change" is not the middle of
    the track. Shot at 5500K it is the 35% point of the 2000~12000 span. But
    laying blue->orange out evenly leaves something bluish under the handle in
    its untouched state, so it reads as a cool adjustment when nothing was
    done. Marking the neutral point at its real position shows at once which
    way to go to get warmer.
    """
    span = max(high - low, 1)
    pivot = min(max((as_shot - low) / span, 0.05), 0.95)
    return ((0.0, "#4a7fd4"), (pivot, "#c8c8cc"), (1.0, "#e8c14a"))


def _track_style(colors) -> str:
    """The stylesheet for a slider with a gradient track.

    Given plain colour strings the stops come out evenly spaced; given
    (position, colour) pairs they are placed at those positions. Needed for
    sliders whose neutral is not the centre, like colour temperature.
    """
    colors = tuple(colors)
    if colors and isinstance(colors[0], (tuple, list)):
        stops = ", ".join(f"stop:{float(pos):.3f} {color}" for pos, color in colors)
    elif len(colors) == 2:
        stops = f"stop:0 {colors[0]}, stop:1 {colors[1]}"
    else:
        stops = ", ".join(
            f"stop:{i / (len(colors) - 1):.3f} {c}" for i, c in enumerate(colors)
        )
    return f"""
        QSlider::groove:horizontal {{
            height: 6px; border-radius: 3px;
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, {stops});
        }}
        QSlider::handle:horizontal {{
            width: 11px; margin: -4px 0; border-radius: 6px;
            background: #f0f0f2; border: 1px solid #16161a;
        }}
        QSlider::handle:horizontal:hover {{ background: #ffffff; }}
    """


#: Roughly how many arrow-key presses it takes to cross a slider end to end.
#: Too fine and nothing visibly moves; too coarse and you cannot stop on the
#: value you want.
_ARROW_DIVISIONS = 200


def _arrow_step(span: float, quantum: float) -> float:
    """Sets how far one arrow-key press moves, in proportion to the range.

    Qt's default arrow-key step is **1 internal integer unit** of the slider.
    How much that one unit comes to on screen differs per range, so the same
    arrow key moved 1% on brightness (0~100) but 0.01% on colour temperature
    (2000~12000K). On colour temperature you had to press 100 times to get a
    visible change, so it looked as if it did not move at all.

    The range is divided into a fixed number of steps so every slider feels
    the same. The value is rounded onto a multiple of 1, 2 or 5 so the numbers
    printed on screen do not turn messy (colour temperature 50K, exposure
    0.05EV, tilt 0.5°).
    """
    if span <= 0:
        return quantum
    rough = span / _ARROW_DIVISIONS
    if rough <= quantum:
        return quantum  # coarse enough already - most integer sliders here
    magnitude = 10.0 ** math.floor(math.log10(rough))
    for factor in (1.0, 2.0, 5.0):
        candidate = factor * magnitude
        if rough <= candidate:
            break
    else:
        candidate = 10.0 * magnitude
    # The slider is integer inside, so it has to be a multiple of the
    # quantum (1/scale) to stay in step
    return max(quantum, round(candidate / quantum) * quantum)


class SliderRow(QWidget):
    """Label + slider + number. Double-click puts it back to the default."""

    value_changed = Signal(float)

    def __init__(
        self,
        label: str,
        minimum: float,
        maximum: float,
        default: float = 0.0,
        decimals: int = 0,
        suffix: str = "",
        tooltip: str = "",
        gradient: str | None = None,
        step: float | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.default = default
        self.decimals = decimals
        self._scale = 10 ** decimals
        self._syncing = False
        # Arrow-key and PageUp step. With none given it is set in proportion
        # to the range.
        self.step = (
            step if step and step > 0
            else _arrow_step(maximum - minimum, 1.0 / self._scale)
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 1, 0, 1)
        layout.setSpacing(1)

        header = QHBoxLayout()
        header.setSpacing(4)
        self.label = QLabel(label)
        self.label.setStyleSheet("color: #ccc;")
        header.addWidget(self.label)
        header.addStretch(1)

        self.spin = QDoubleSpinBox()
        self.spin.setRange(minimum, maximum)
        self.spin.setDecimals(decimals)
        self.spin.setSingleStep(self.step)
        self.spin.setValue(default)
        self.spin.setSuffix(suffix)
        self.spin.setFixedWidth(78)
        self.spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.spin.setStyleSheet(
            "QDoubleSpinBox { background: #303035; color: #eee; border: 1px solid #444;"
            " border-radius: 3px; padding: 1px 4px; }"
        )
        self.spin.valueChanged.connect(self._on_spin)
        header.addWidget(self.spin)

        # Left as faint text on a transparent background, nobody knows it is
        # there. It is always shown, but at the default value pressing it does
        # nothing, so it is disabled and dimmed.
        self.reset_button = QPushButton("↺")
        self.reset_button.setFixedSize(24, 22)
        self.reset_button.setCursor(Qt.PointingHandCursor)
        self.reset_button.setToolTip(
            tr("Reset to default ({value})").format(value=self._format(default)))
        self.reset_button.clicked.connect(self.reset)
        header.addWidget(self.reset_button)
        layout.addLayout(header)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(int(minimum * self._scale), int(maximum * self._scale))
        self.slider.setValue(int(default * self._scale))
        raw_step = max(1, int(round(self.step * self._scale)))
        self.slider.setSingleStep(raw_step)
        self.slider.setPageStep(raw_step * 10)
        self.slider.setFocusPolicy(Qt.StrongFocus)  # arrow keys, not the wheel
        self.slider.wheelEvent = lambda event: event.ignore()
        self.spin.wheelEvent = lambda event: event.ignore()
        self.slider.valueChanged.connect(self._on_slider)
        self.set_gradient(gradient)
        layout.addWidget(self.slider)

        if tooltip:
            self.setToolTip(tooltip)

        self._highlight_if_changed()  # sets the reset button's initial state

    def set_gradient(self, gradient) -> None:
        """Changes the track colour. Takes a name (a GRADIENTS key) or a tuple
        of colours.

        There are cases like the HSL tab where the same slider has to take a
        different colour per channel, so it has to stay changeable later too.
        """
        if not gradient:
            self.slider.setStyleSheet("")
            return
        colors = GRADIENTS.get(gradient) if isinstance(gradient, str) else tuple(gradient)
        if colors:
            self.slider.setStyleSheet(_track_style(colors))

    def wheelEvent(self, event) -> None:
        """Ignores the wheel and passes it to the parent (the scroll area).

        Letting the wheel adjust the value means that while scrolling the
        panel, every slider the mouse passed over changes on its own. There is
        less to gain than there is to lose.
        """
        event.ignore()

    def _on_slider(self, raw: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        self.spin.setValue(raw / self._scale)
        self._syncing = False
        self._emit()

    def _on_spin(self, value: float) -> None:
        if self._syncing:
            return
        self._syncing = True
        self.slider.setValue(int(round(value * self._scale)))
        self._syncing = False
        self._emit()

    def _emit(self) -> None:
        self._highlight_if_changed()
        self.value_changed.emit(self.value())

    def _format(self, value: float) -> str:
        return f"{value:.{self.decimals}f}" if self.decimals else f"{value:.0f}"

    def _highlight_if_changed(self) -> None:
        """Brings the label and the reset button to life when the value is not
        the default.

        What you have touched has to show at a glance, and the means to put it
        back has to be right there too.
        """
        changed = abs(self.value() - self.default) > 1e-9
        self.label.setStyleSheet(
            "color: #7fb3ff; font-weight: bold;" if changed else "color: #ccc;"
        )
        self.reset_button.setEnabled(changed)
        self.reset_button.setStyleSheet(theme.reset_button(changed))

    def value(self) -> float:
        return self.spin.value()

    def set_value(self, value: float, silent: bool = False) -> None:
        self._syncing = True
        self.spin.setValue(value)
        self.slider.setValue(int(round(value * self._scale)))
        self._syncing = False
        self._highlight_if_changed()
        if not silent:
            self.value_changed.emit(self.value())

    def reset(self) -> None:
        self.set_value(self.default)

    def mouseDoubleClickEvent(self, event) -> None:
        self.reset()


class CollapsibleSection(QWidget):
    """A collapsible section. Like the Lightroom panel, only what you need is
    opened out.

    The eye button on the right turns that section's adjustments off and on as
    a whole. It is for taking them out for a moment without erasing the
    values, so switching it back on brings the original values straight back.
    """

    toggled_open = Signal(bool)
    visibility_changed = Signal(bool)

    def __init__(self, title: str, expanded: bool = False, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(0)

        self.header = QPushButton(f"  {title}")
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.setStyleSheet(theme.SECTION_HEADER)
        self.header.toggled.connect(self._on_toggled)
        header_row.addWidget(self.header, 1)

        self.eye = QPushButton("◉")
        self.eye.setCheckable(True)
        self.eye.setChecked(True)
        self.eye.setFixedWidth(30)
        self.eye.setCursor(Qt.PointingHandCursor)
        self.eye.setToolTip(tr("Toggle this section's edits on and off (values kept)"))
        self.eye.setStyleSheet(theme.EYE_BUTTON)
        self.eye.toggled.connect(self._on_visibility)
        header_row.addWidget(self.eye)

        layout.addLayout(header_row)

        self.body = QFrame()
        self.body.setStyleSheet("QFrame { background: #232326; }")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(8, 6, 8, 8)
        self.body_layout.setSpacing(2)
        self.body.setVisible(expanded)
        layout.addWidget(self.body)

        self._title = title
        self._update_arrow()

    def _on_toggled(self, checked: bool) -> None:
        self.body.setVisible(checked)
        self._update_arrow()
        self.toggled_open.emit(checked)

    def _update_arrow(self) -> None:
        self._refresh_title()

    def add_widget(self, widget: QWidget) -> None:
        self.body_layout.addWidget(widget)

    def add_layout(self, layout) -> None:
        self.body_layout.addLayout(layout)

    def set_expanded(self, expanded: bool) -> None:
        self.header.setChecked(expanded)

    def mark_active(self, active: bool) -> None:
        """Marks the title when this section has values that were touched."""
        self._active = active
        self._refresh_title()

    def _refresh_title(self) -> None:
        arrow = "▾" if self.header.isChecked() else "▸"
        suffix = "  ●" if getattr(self, "_active", False) else ""
        if not self.eye.isChecked():
            suffix += tr("  (off)")
        self.header.setText(f"  {arrow}  {self._title}{suffix}")

    def _on_visibility(self, visible: bool) -> None:
        self.eye.setText("◉" if visible else "○")
        self._refresh_title()
        self.visibility_changed.emit(visible)

    def is_visible_section(self) -> bool:
        return self.eye.isChecked()

    def set_section_visible(self, visible: bool) -> None:
        self.eye.setChecked(visible)
