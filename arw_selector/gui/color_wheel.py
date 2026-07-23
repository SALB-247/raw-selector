"""색상휠 위젯.

색조와 채도를 슬라이더 두 개로 나눠 조작하면 "어느 방향으로 얼마나"가
직관적으로 안 잡힙니다. 휠 위의 한 점을 끄는 편이 훨씬 빠릅니다.

각도가 색조, 중심에서의 거리가 채돕니다.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QConicalGradient,
    QPainter,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .i18n import tr


class ColorWheel(QWidget):
    """색조/채도를 한 번에 고르는 원형 위젯."""

    changed = Signal(int, int)  # (색조 0~359, 채도 0~100)

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self._title = title
        self._hue = 0
        self._saturation = 0
        self._dragging = False
        self.setMinimumSize(96, 96)
        self.setToolTip(tr(
            "Drag to choose hue and saturation\n"
            "Closer to the center is paler, further out is more saturated\n"
            "Double-click to reset"
        ))

    # ------------------------------------------------------------ 상태

    def values(self) -> tuple[int, int]:
        return self._hue, self._saturation

    def set_values(self, hue: int, saturation: int, silent: bool = False) -> None:
        self._hue = int(hue) % 360
        self._saturation = max(0, min(100, int(saturation)))
        self.update()
        if not silent:
            self.changed.emit(self._hue, self._saturation)

    def reset(self) -> None:
        self.set_values(0, 0)

    # ------------------------------------------------------------ 좌표

    def _wheel_rect(self) -> QRectF:
        size = min(self.width(), self.height()) - 4
        return QRectF(
            (self.width() - size) / 2, (self.height() - size) / 2, size, size
        )

    def _marker_pos(self) -> QPointF:
        rect = self._wheel_rect()
        radius = rect.width() / 2 * (self._saturation / 100.0)
        angle = math.radians(self._hue)
        return QPointF(
            rect.center().x() + radius * math.cos(angle),
            rect.center().y() - radius * math.sin(angle),
        )

    def _from_pos(self, point: QPoint) -> tuple[int, int]:
        rect = self._wheel_rect()
        dx = point.x() - rect.center().x()
        dy = rect.center().y() - point.y()

        distance = math.hypot(dx, dy)
        radius = rect.width() / 2
        saturation = int(round(min(1.0, distance / radius) * 100)) if radius else 0
        hue = int(round(math.degrees(math.atan2(dy, dx)))) % 360
        return hue, saturation

    # ------------------------------------------------------------ 마우스

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        self._dragging = True
        self.set_values(*self._from_pos(event.position().toPoint()))

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self.set_values(*self._from_pos(event.position().toPoint()))

    def mouseReleaseEvent(self, event) -> None:
        self._dragging = False

    def mouseDoubleClickEvent(self, event) -> None:
        self.reset()

    def wheelEvent(self, event) -> None:
        """휠은 부모 스크롤로 넘긴다 — 실수로 색이 바뀌면 문제가 됩니다."""
        event.ignore()

    # ------------------------------------------------------------ 그리기

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self._wheel_rect()

        # 색조 원 (각도 방향)
        conical = QConicalGradient(rect.center(), 0)
        for step in range(13):
            position = step / 12.0
            conical.setColorAt(position, QColor.fromHsv(int(position * 359), 255, 235))
        painter.setPen(Qt.NoPen)
        painter.setBrush(conical)
        painter.drawEllipse(rect)

        # 중심으로 갈수록 무채색 (채도 방향)
        radial = QRadialGradient(rect.center(), rect.width() / 2)
        radial.setColorAt(0.0, QColor(105, 105, 112, 255))
        radial.setColorAt(1.0, QColor(105, 105, 112, 0))
        painter.setBrush(radial)
        painter.drawEllipse(rect)

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(90, 90, 96), 1))
        painter.drawEllipse(rect)

        # 선택 마커
        marker = self._marker_pos()
        size = 6 if self._saturation else 4
        painter.setBrush(
            QColor.fromHsv(self._hue, int(self._saturation * 2.55), 235)
            if self._saturation
            else QColor(140, 140, 146)
        )
        painter.setPen(QPen(QColor(250, 250, 252), 2))
        painter.drawEllipse(
            QRectF(marker.x() - size, marker.y() - size, size * 2, size * 2)
        )


class ColorGradeZoneWidget(QWidget):
    """색상휠 + 광도 슬라이더 한 세트 (그림자/중간/하이라이트용)."""

    changed = Signal()

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(3)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(2)

        self.label = QLabel(title)
        self.label.setStyleSheet("color: #b0b0b8; font-size: 11px;")
        header.addWidget(self.label, 1, Qt.AlignCenter)

        self.reset_button = QPushButton("↺")
        self.reset_button.setFixedSize(22, 18)
        self.reset_button.setCursor(Qt.PointingHandCursor)
        self.reset_button.setToolTip(tr("Reset this zone to defaults"))
        self.reset_button.clicked.connect(self.reset)
        header.addWidget(self.reset_button)
        layout.addLayout(header)

        self.wheel = ColorWheel(title)
        self.wheel.changed.connect(self._on_changed)
        layout.addWidget(self.wheel, 1)

        # 휠만 있으면 지금 값이 정확히 얼마인지 알 수 없습니다. 숫자를 함께
        # 보여 주어야 같은 설정을 다시 맞추거나 남에게 전할 수 있습니다.
        self.wheel_value = QLabel()
        self.wheel_value.setAlignment(Qt.AlignCenter)
        self.wheel_value.setStyleSheet("color: #9a9aa2; font-size: 10px;")
        layout.addWidget(self.wheel_value)

        lum_row = QHBoxLayout()
        lum_row.setContentsMargins(0, 0, 0, 0)
        lum_row.setSpacing(4)
        lum_caption = QLabel(tr("Luminance"))
        lum_caption.setStyleSheet("color: #9a9aa2; font-size: 10px;")
        lum_row.addWidget(lum_caption)

        self.luminance = QSlider(Qt.Horizontal)
        self.luminance.setRange(-100, 100)
        self.luminance.setToolTip(tr("Luminance"))
        self.luminance.wheelEvent = lambda event: event.ignore()
        self.luminance.valueChanged.connect(self._on_changed)
        self.luminance.setStyleSheet(
            "QSlider::groove:horizontal { height: 4px; border-radius: 2px;"
            " background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            " stop:0 #101013, stop:1 #f0f0f2); }"
            "QSlider::handle:horizontal { width: 9px; margin: -3px 0;"
            " border-radius: 5px; background: #f0f0f2; border: 1px solid #16161a; }"
        )
        lum_row.addWidget(self.luminance, 1)

        self.lum_value = QLabel()
        self.lum_value.setFixedWidth(28)
        self.lum_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lum_value.setStyleSheet("color: #ccc; font-size: 10px;")
        lum_row.addWidget(self.lum_value)
        layout.addLayout(lum_row)

        self._refresh_reset()

    def _on_changed(self, *_) -> None:
        self._refresh_reset()
        self.changed.emit()

    def values(self) -> tuple[int, int, int]:
        hue, saturation = self.wheel.values()
        return hue, saturation, self.luminance.value()

    def set_values(self, hue: int, saturation: int, luminance: int) -> None:
        self.wheel.set_values(hue, saturation, silent=True)
        blocked = self.luminance.blockSignals(True)
        self.luminance.setValue(luminance)
        self.luminance.blockSignals(blocked)
        self._refresh_reset()

    def reset(self) -> None:
        """색상휠과 광도를 한꺼번에 되돌립니다."""
        self.set_values(0, 0, 0)
        self.changed.emit()

    def _refresh_reset(self) -> None:
        hue, saturation, luminance = self.values()
        changed = bool(saturation or luminance)
        self.reset_button.setEnabled(changed)
        self.reset_button.setStyleSheet(theme.reset_button(changed, size=11))
        self.label.setStyleSheet(
            "color: #7fb3ff; font-size: 11px; font-weight: bold;"
            if changed
            else "color: #b0b0b8; font-size: 11px;"
        )
        # 채도가 0이면 색조는 의미가 없으므로 숨깁니다
        self.wheel_value.setText(
            tr("Hue {hue}°   Saturation {saturation}").format(
                hue=hue, saturation=saturation
            )
            if saturation
            else tr("Neutral")
        )
        self.lum_value.setText(f"{luminance:+d}")
