"""보정 패널에서 쓰는 공용 위젯.

슬라이더가 수십 개라 한 줄씩 손으로 만들면 관리가 안 됩니다. 라벨·슬라이더·
숫자입력·초기화를 한 덩어리로 묶어 두고 재사용합니다.
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
    """휠로 값이 바뀌지 않게 하고, 스크롤을 부모(패널)로 넘깁니다.

    긴 패널을 휠로 훑다 보면 커서가 콤보박스·스핀박스 위를 지나갑니다. 그때
    값이 멋대로 바뀌면 무엇이 바뀌었는지도 모른 채 보정이 틀어집니다.
    슬라이더에는 이미 같은 처리가 되어 있었는데, 드롭다운에는 빠져 있었습니다.

    포커스 정책도 함께 낮춥니다. StrongFocus면 클릭해 포커스를 준 뒤에만
    키보드로 조작할 수 있어, 지나가다 건드리는 사고가 나지 않습니다.
    """
    widget.setFocusPolicy(Qt.StrongFocus)
    widget.wheelEvent = lambda event: event.ignore()
    return widget


def disable_wheel_in(parent: QWidget) -> None:
    """패널 안의 모든 콤보박스·스핀박스에 휠 차단을 적용합니다.

    위젯을 새로 추가할 때마다 빠뜨리기 쉬워서, 다 만든 뒤 한 번에 훑습니다.
    """
    # PySide6의 findChildren은 타입 튜플을 받지 않아 종류별로 훑습니다.
    # QAbstractSpinBox 하나로 QSpinBox와 QDoubleSpinBox가 모두 걸립니다.
    from PySide6.QtWidgets import QAbstractSpinBox, QComboBox

    for widget_type in (QComboBox, QAbstractSpinBox):
        for child in parent.findChildren(widget_type):
            disable_wheel(child)


# 슬라이더 트랙 그라디언트. 무엇을 조정하는 값인지 색으로 바로 보이게 합니다.
# Lightroom과 같은 방향(왼쪽이 음수)을 씁니다.
GRADIENTS = {
    "temperature": ("#4a7fd4", "#e8c14a"),   # 차갑게 ↔ 따뜻하게
    "tint": ("#4ac46a", "#d24ac4"),          # 초록 ↔ 마젠타
    "exposure": ("#101013", "#f5f5f5"),
    "contrast": ("#6a6a70", "#e8e8ea"),
    "highlights": ("#5a5a60", "#ffffff"),
    "shadows": ("#101013", "#9a9aa0"),
    "whites": ("#7a7a80", "#ffffff"),
    "blacks": ("#000000", "#8a8a90"),
    # 채도/생동감은 "색이 진해진다"는 뜻이지 특정 색으로 간다는 뜻이 아닙니다.
    # 예전에는 각각 빨강/주황 한 색으로 끝나서, 올리면 붉어지는 것처럼
    # 보였습니다. 무채색에서 여러 색이 살아나는 쪽으로 바꿉니다.
    "saturation": ("#8a8a8a", "#7a9ad0", "#7ac07a", "#d0c060", "#d06a6a"),
    "vibrance": ("#8a8a8a", "#93a8c8", "#9dbd93", "#c8bd8a", "#c88a8a"),
    "hue": (
        "#ff0000", "#ffff00", "#00ff00", "#00ffff", "#0000ff", "#ff00ff", "#ff0000"
    ),
    "mono": ("#3a3a40", "#8a8ab0"),
}


def hsl_band_colors(center_hue: int, channel: str) -> tuple[str, ...]:
    """HSL 밴드 슬라이더의 트랙 색.

    전 밴드를 같은 무지개로 칠하면 지금 무엇을 만지는지 알 수 없습니다.
    Lightroom처럼 밴드마다 그 색상대 고유의 그라디언트를 씁니다.

    - 색조: 이웃 색상대까지만 좁게 (빨강이면 마젠타↔빨강↔주황)
    - 채도: 무채색 → 그 색
    - 광도: 어두운 그 색 → 밝은 그 색

    center_hue는 OpenCV 기준(0~179)이라 QColor용으로 2배 합니다.
    """
    from PySide6.QtGui import QColor

    hue = (center_hue * 2) % 360

    def css(h: int, s: int, v: int) -> str:
        return QColor.fromHsv(h % 360, s, v).name()

    if channel == "hue":
        # 좌우 30도씩만 — 슬라이더를 끌었을 때 실제로 갈 방향을 보여줍니다
        return (css(hue - 30, 230, 235), css(hue, 230, 235), css(hue + 30, 230, 235))
    if channel == "saturation":
        # 채도만 움직입니다. 예전에는 명도까지 150→235로 같이 올려서,
        # 채도를 올리면 밝아지기도 하는 것처럼 보였습니다.
        return (css(hue, 20, 215), css(hue, 245, 215))
    # 광도 — 명도만 움직입니다. 채도를 200→120으로 같이 낮추면 광도를
    # 올릴 때 색이 빠지는 것처럼 보입니다.
    return (css(hue, 170, 55), css(hue, 170, 250))


def temperature_track_colors(
    as_shot: int, low: int = 2000, high: int = 12000
) -> tuple[tuple[float, str], ...]:
    """색온도 트랙. 중립(as-shot) 지점에 무채색을 놓습니다.

    색온도는 절대 Kelvin이라 "변화 없음"이 트랙 한가운데가 아닙니다.
    5500K 촬영이면 2000~12000 구간의 35% 지점입니다. 그런데 파랑→주황을
    균등하게 깔면 손대지 않은 상태의 핸들 밑이 푸르스름해서, 아무것도
    안 했는데 차갑게 보정된 것처럼 읽힙니다. 중립 지점을 실제 위치에
    찍어 두면 좌우 어느 쪽으로 가야 따뜻해지는지 바로 보입니다.
    """
    span = max(high - low, 1)
    pivot = min(max((as_shot - low) / span, 0.05), 0.95)
    return ((0.0, "#4a7fd4"), (pivot, "#c8c8cc"), (1.0, "#e8c14a"))


def _track_style(colors) -> str:
    """그라디언트 트랙을 가진 슬라이더 스타일시트.

    색 문자열만 주면 균등 간격으로, (위치, 색) 쌍을 주면 그 위치에
    찍습니다. 색온도처럼 중립이 가운데가 아닌 슬라이더에 필요합니다.
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


#: 방향키로 슬라이더 끝에서 끝까지 가는 데 걸리는 대략적인 횟수.
#: 촘촘하면 눈에 안 보이고, 성기면 원하는 값에 못 세웁니다.
_ARROW_DIVISIONS = 200


def _arrow_step(span: float, quantum: float) -> float:
    """방향키 한 번에 움직일 양을 범위에 비례해 정합니다.

    Qt의 기본 방향키 폭은 슬라이더 **내부 정수 1칸**입니다. 그 1칸이 화면에서
    얼마가 되는지는 범위마다 달라서, 같은 방향키가 밝기(0~100)에서는 1%를
    움직이는데 색온도(2000~12000K)에서는 0.01%를 움직였습니다. 색온도는 눈에
    보이는 변화까지 100번을 눌러야 해서 사실상 안 움직이는 것처럼 보입니다.

    범위를 일정한 칸수로 나눠 어느 슬라이더든 손맛이 같게 만듭니다. 값은
    1·2·5 배수로 떨어뜨려 화면에 찍히는 숫자가 지저분해지지 않게 합니다
    (색온도 50K, 노출 0.05EV, 기울이기 0.5°).
    """
    if span <= 0:
        return quantum
    rough = span / _ARROW_DIVISIONS
    if rough <= quantum:
        return quantum  # 이미 충분히 성깁니다 — 정수 슬라이더 대부분이 여기입니다
    magnitude = 10.0 ** math.floor(math.log10(rough))
    for factor in (1.0, 2.0, 5.0):
        candidate = factor * magnitude
        if rough <= candidate:
            break
    else:
        candidate = 10.0 * magnitude
    # 슬라이더 내부는 정수라 눈금(1/scale)의 배수여야 어긋나지 않습니다
    return max(quantum, round(candidate / quantum) * quantum)


class SliderRow(QWidget):
    """라벨 + 슬라이더 + 숫자. 더블클릭하면 기본값으로 돌아갑니다."""

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
        # 방향키·PageUp 폭. 지정이 없으면 범위에 비례해 정합니다.
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

        # 투명 배경에 흐린 글자로 두면 있는 줄도 모릅니다. 항상 보이게 하되
        # 기본값일 때는 눌러도 소용없으므로 비활성으로 흐려 둡니다.
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
        self.slider.setFocusPolicy(Qt.StrongFocus)  # 휠 대신 클릭 후 방향키
        self.slider.wheelEvent = lambda event: event.ignore()
        self.spin.wheelEvent = lambda event: event.ignore()
        self.slider.valueChanged.connect(self._on_slider)
        self.set_gradient(gradient)
        layout.addWidget(self.slider)

        if tooltip:
            self.setToolTip(tooltip)

        self._highlight_if_changed()  # 리셋 버튼 초기 상태를 잡아 줍니다

    def set_gradient(self, gradient) -> None:
        """트랙 색을 바꿉니다. 이름(GRADIENTS 키) 또는 색 튜플을 받습니다.

        HSL 탭처럼 같은 슬라이더가 채널에 따라 다른 색이 되어야 하는 경우가
        있어서 나중에도 바꿀 수 있어야 합니다.
        """
        if not gradient:
            self.slider.setStyleSheet("")
            return
        colors = GRADIENTS.get(gradient) if isinstance(gradient, str) else tuple(gradient)
        if colors:
            self.slider.setStyleSheet(_track_style(colors))

    def wheelEvent(self, event) -> None:
        """휠은 무시하고 부모(스크롤 영역)로 넘깁니다.

        휠로 값을 조정하게 두면 패널을 스크롤하다가 마우스가 지나간
        슬라이더들이 멋대로 바뀝니다. 잃는 것보다 얻는 게 적습니다.
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
        """기본값이 아니면 라벨과 리셋 버튼을 살립니다.

        무엇을 만졌는지 한눈에 보여야 하고, 되돌릴 수단도 그 자리에 있어야 합니다.
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
    """접이식 섹션. Lightroom 패널처럼 필요한 것만 펴 놓고 씁니다.

    오른쪽 눈 버튼으로 그 섹션의 보정을 전체가 껐다 켤 수 있습니다. 값을
    지우지 않고 잠시 빼 보는 용도라, 껐다 켜면 원래 값이 그대로 돌아옵니다.
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
        """이 섹션에 손댄 값이 있으면 제목에 표시합니다."""
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
