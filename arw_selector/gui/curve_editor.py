"""The tone curve editor.

Parametric sliders alone cannot pick out one particular brightness band and
raise or lower it precisely. Dragging the curve directly is faster and more
accurate.

With the histogram laid in behind it, which tones you are working on shows at
once.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .i18n import tr

HANDLE_RADIUS = 5
HIT_RADIUS = 10
"""The grab hit radius. It has to be more generous than the on-screen radius
for picking a point up to be easy."""

CHANNEL_COLORS = {
    "rgb": QColor(230, 230, 235),
    "red": QColor(235, 90, 90),
    "green": QColor(110, 210, 120),
    "blue": QColor(105, 150, 245),
}


class CurveEditor(QWidget):
    """A draggable tone curve.

    The two endpoints are always there and cannot be deleted. The points in
    between are added with a click and deleted with a right-click or a
    double-click.
    """

    points_changed = Signal(tuple)  # ((input, output), ...) - no endpoints

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(200)
        self.setMouseTracking(True)
        self.setToolTip(tr(
            "Click to add a point · drag to move\n"
            "Right-click or double-click to delete\n"
            "Double-click an empty area to reset all"
        ))

        self._channel = "rgb"
        # All the control points including the endpoints. Kept in ascending
        # x at all times.
        self._points: list[list[float]] = [[0.0, 0.0], [255.0, 255.0]]
        self._dragging: int | None = None
        self._histogram: np.ndarray | None = None
        # The four parametric bands (shadows/darks/lights/highlights). They
        # are carried into the rgb channel curve only - a separate idea from
        # the per-channel point curves.
        self._parametric = (0, 0, 0, 0)
        self._show_clip = True  # whether curve clipping is marked

    # ------------------------------------------------------------ State

    def set_channel(self, channel: str) -> None:
        self._channel = channel
        self.update()

    def channel(self) -> str:
        return self._channel

    def set_histogram(self, values: np.ndarray | None) -> None:
        self._histogram = values
        self.update()

    def set_parametric(self, shadows: int, darks: int, lights: int, highlights: int) -> None:
        """Takes the parametric band values and shows them on the curve graph
        alongside."""
        values = (int(shadows), int(darks), int(lights), int(highlights))
        if values != self._parametric:
            self._parametric = values
            self.update()

    def set_clip_markers(self, show: bool) -> None:
        """Turns the curve's clipping markers on and off."""
        self._show_clip = bool(show)
        self.update()

    def _parametric_lut(self) -> np.ndarray | None:
        """The 256-entry LUT of the parametric curve. None when there is no
        value (nothing is drawn)."""
        if self._channel != "rgb" or not any(self._parametric):
            return None
        from ..core.develop.engine import parametric_tone_lut

        shadows, darks, lights, highlights = self._parametric
        return parametric_tone_lut(shadows, darks, lights, highlights)

    def points(self) -> tuple[tuple[int, int], ...]:
        """Returns the control points. Only endpoints that were not moved are
        left out.

        The endpoints are the black/white points, so they can be dragged
        vertically (mouseMoveEvent). They used to be cut off unconditionally,
        so dragging one changed only the curve in the editor and nothing at
        all happened to the photo - because the engine put the endpoints back
        at (0,0)/(255,255). Leaving out only the endpoints in their identity
        positions and letting the engine fill them in stays compatible with
        the existing presets (which store only the in-between points).
        """
        result = [(int(round(x)), int(round(y))) for x, y in self._points]
        if result[0] == (0, 0):
            result = result[1:]
        if result and result[-1] == (255, 255):
            result = result[:-1]
        return tuple(result)

    def set_points(self, points: tuple[tuple[int, int], ...]) -> None:
        # A point that lands on x=0/255 is an endpoint, not an in-between
        # point. Slotted straight into the middle it makes one endpoint too
        # many and the curve breaks vertically.
        ordered = [[float(x), float(y)] for x, y in sorted(points)]
        first = ordered.pop(0) if ordered and ordered[0][0] <= 0.0 else [0.0, 0.0]
        last = ordered.pop() if ordered and ordered[-1][0] >= 255.0 else [255.0, 255.0]
        self._points = [first, *ordered, last]
        self.update()

    def is_identity(self) -> bool:
        return len(self._points) == 2

    def reset(self) -> None:
        self._points = [[0.0, 0.0], [255.0, 255.0]]
        self.update()
        self.points_changed.emit(self.points())

    # --------------------------------------------------- Coordinate conversion

    _AXIS_MARGIN = 15  # bottom/left margin for the 0~100 scale labels

    def _plot_rect(self) -> QRect:
        return self.rect().adjusted(self._AXIS_MARGIN, 2, -2, -self._AXIS_MARGIN)

    def _to_screen(self, x: float, y: float) -> QPointF:
        rect = self._plot_rect()
        return QPointF(
            rect.left() + x / 255.0 * rect.width(),
            rect.bottom() - y / 255.0 * rect.height(),
        )

    def _to_value(self, point: QPoint) -> tuple[float, float]:
        rect = self._plot_rect()
        x = (point.x() - rect.left()) / max(1, rect.width()) * 255.0
        y = (rect.bottom() - point.y()) / max(1, rect.height()) * 255.0
        return float(np.clip(x, 0, 255)), float(np.clip(y, 0, 255))

    def _hit_test(self, point: QPoint) -> int | None:
        for index, (x, y) in enumerate(self._points):
            screen = self._to_screen(x, y)
            if (screen - QPointF(point)).manhattanLength() <= HIT_RADIUS * 1.5:
                return index
        return None

    # ------------------------------------------------------------ Mouse

    def mousePressEvent(self, event) -> None:
        index = self._hit_test(event.position().toPoint())

        if event.button() == Qt.RightButton:
            # Endpoints cannot be deleted - the curve becomes undefined
            if index is not None and 0 < index < len(self._points) - 1:
                self._points.pop(index)
                self.update()
                self.points_changed.emit(self.points())
            return

        if event.button() != Qt.LeftButton:
            return

        if index is not None:
            self._dragging = index
            return

        # Pressing an empty spot adds a point and lets you drag it right away
        x, y = self._to_value(event.position().toPoint())
        insert_at = next(
            (i for i, (px, _) in enumerate(self._points) if px > x),
            len(self._points) - 1,
        )
        self._points.insert(insert_at, [x, y])
        self._dragging = insert_at
        self.update()
        self.points_changed.emit(self.points())

    def mouseMoveEvent(self, event) -> None:
        if self._dragging is None:
            self.setCursor(
                Qt.PointingHandCursor
                if self._hit_test(event.position().toPoint()) is not None
                else Qt.CrossCursor
            )
            return

        x, y = self._to_value(event.position().toPoint())
        index = self._dragging

        if index == 0:
            # The left endpoint is pinned at x=0; only the output is
            # adjusted (the black point)
            self._points[0][1] = y
        elif index == len(self._points) - 1:
            self._points[-1][1] = y
        else:
            # Stops it crossing its neighbours. Cross one and the curve
            # flips over.
            left = self._points[index - 1][0] + 1
            right = self._points[index + 1][0] - 1
            self._points[index][0] = float(np.clip(x, left, right))
            self._points[index][1] = y

        self.update()
        self.points_changed.emit(self.points())

    def mouseReleaseEvent(self, event) -> None:
        self._dragging = None

    def mouseDoubleClickEvent(self, event) -> None:
        index = self._hit_test(event.position().toPoint())
        if index is not None and 0 < index < len(self._points) - 1:
            self._points.pop(index)
        else:
            self._points = [[0.0, 0.0], [255.0, 255.0]]
        self.update()
        self.points_changed.emit(self.points())

    # ------------------------------------------------------------ Drawing

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self._plot_rect()

        painter.fillRect(self.rect(), QColor(24, 24, 27))
        self._draw_histogram(painter, rect)

        # Grid + diagonal (the identity line)
        painter.setPen(QPen(QColor(52, 52, 58), 1))
        for i in range(1, 4):
            x = rect.left() + rect.width() * i // 4
            y = rect.top() + rect.height() * i // 4
            painter.drawLine(x, rect.top(), x, rect.bottom())
            painter.drawLine(rect.left(), y, rect.right(), y)

        painter.setPen(QPen(QColor(70, 70, 78), 1, Qt.DashLine))
        painter.drawLine(rect.bottomLeft(), rect.topRight())

        self._draw_axis_labels(painter, rect)
        self._draw_parametric(painter, rect)
        self._draw_curve(painter, rect)
        self._draw_handles(painter)
        if self._show_clip:
            self._draw_clip_markers(painter, rect)

        # The brush must be cleared before the border is drawn. If the brush
        # set for the handles is still there, drawRect fills the whole
        # rectangle rather than drawing a border, and the curve and the grid
        # are buried completely.
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(60, 60, 66), 1))
        painter.drawRect(rect)

    def _draw_histogram(self, painter: QPainter, rect: QRect) -> None:
        if self._histogram is None or self._histogram.size == 0:
            return
        peak = float(self._histogram.max())
        if peak <= 0:
            return

        from .histogram import histogram_heights

        # The **same function** as the histogram widget is used. If the two
        # graphs have different vertical scales the same photo looks a
        # different shape, and the judgement of where to hang the curve comes
        # out wrong.
        heights = histogram_heights(self._histogram, peak)
        path = QPainterPath()
        path.moveTo(rect.left(), rect.bottom())
        for index in range(256):
            x = rect.left() + rect.width() * index / 255.0
            path.lineTo(x, rect.bottom() - heights[index] * rect.height())
        path.lineTo(rect.right(), rect.bottom())
        path.closeSubpath()
        painter.fillPath(path, QColor(70, 70, 78, 120))

    def _draw_axis_labels(self, painter: QPainter, rect: QRect) -> None:
        """Shows the input/output axes as 0~100(%). The standard ratio, not
        the 8-bit value."""
        painter.setPen(QColor(120, 120, 128))
        font = painter.font()
        font.setPointSize(7)
        painter.setFont(font)
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            label = str(int(frac * 100))
            # bottom = the input axis
            x = rect.left() + rect.width() * frac
            painter.drawText(
                QRectF(x - 12, rect.bottom() + 2, 24, self._AXIS_MARGIN),
                Qt.AlignHCenter | Qt.AlignTop, label,
            )
            # left = the output axis
            y = rect.bottom() - rect.height() * frac
            painter.drawText(
                QRectF(0, y - 7, self._AXIS_MARGIN - 2, 14),
                Qt.AlignRight | Qt.AlignVCenter, label,
            )

    def _draw_parametric(self, painter: QPainter, rect: QRect) -> None:
        """Shows the parametric band adjustment alongside, as a faint line.

        Separately from the point curve, it lets you see with your own eyes
        how the band tones changed with the sliders come out on the curve.
        """
        lut = self._parametric_lut()
        if lut is None:
            return
        path = QPainterPath()
        path.moveTo(self._to_screen(0, lut[0]))
        for value in range(1, 256):
            path.lineTo(self._to_screen(value, lut[value]))
        pen = QPen(QColor(127, 179, 255, 150), 1.4, Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

    def _draw_clip_markers(self, painter: QPainter, rect: QRect) -> None:
        """Marks where the curve cuts tones off.

        Where there is still input left but the output sticks at 0 (crushed)
        or 255 (blown), the detail in that band is gone. Highlights are
        flagged in red, shadows in blue.
        """
        lut = self._composite_lut()
        crush = np.where(lut <= 0.5)[0]
        blow = np.where(lut >= 254.5)[0]
        painter.setPen(Qt.NoPen)
        # Shadow clipping: the band where the input is above 0 yet the
        # output is still 0
        if crush.size and crush.max() > 0:
            painter.setBrush(QColor(90, 150, 245))
            x0 = rect.left()
            x1 = rect.left() + rect.width() * crush.max() / 255.0
            painter.drawRect(QRectF(x0, rect.bottom() - 3, x1 - x0, 3))
        # Highlight clipping: the band where the input is below 255 yet the
        # output is still 255
        if blow.size and blow.min() < 255:
            painter.setBrush(QColor(235, 90, 90))
            x0 = rect.left() + rect.width() * blow.min() / 255.0
            x1 = rect.right()
            painter.drawRect(QRectF(x0, rect.top(), x1 - x0, 3))

    def _composite_lut(self) -> np.ndarray:
        """The final response, the point curve applied after the parametric
        one (for deciding clipping)."""
        base = self._parametric_lut()
        base = base if base is not None else np.arange(256, dtype=np.float32)
        point = self._curve_lut()
        return np.clip(np.interp(base, np.arange(256), point), 0, 255)

    def _curve_lut(self) -> np.ndarray:
        """The 256-entry curve built from the current control points. It uses
        the same smooth interpolation as the engine."""
        from ..core.develop.engine import smooth_curve_lut

        return smooth_curve_lut([(p[0], p[1]) for p in self._points])

    def _draw_curve(self, painter: QPainter, rect: QRect) -> None:
        lut = self._curve_lut()
        color = CHANNEL_COLORS.get(self._channel, CHANNEL_COLORS["rgb"])

        path = QPainterPath()
        path.moveTo(self._to_screen(0, lut[0]))
        for value in range(1, 256):
            path.lineTo(self._to_screen(value, lut[value]))

        # Now the curve is smoothed, the line is thickened a little so it is
        # easier to see.
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(color, 2.6))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

    def _draw_handles(self, painter: QPainter) -> None:
        color = CHANNEL_COLORS.get(self._channel, CHANNEL_COLORS["rgb"])
        for index, (x, y) in enumerate(self._points):
            center = self._to_screen(x, y)
            is_end = index in (0, len(self._points) - 1)
            painter.setBrush(QColor(30, 30, 34) if is_end else color)
            painter.setPen(QPen(color, 2))
            # Spelled out as a QRectF. The (QPointF, radius, radius) form
            # picked the wrong overload in PySide6 and painted the whole
            # widget.
            painter.drawEllipse(
                QRectF(
                    center.x() - HANDLE_RADIUS, center.y() - HANDLE_RADIUS,
                    HANDLE_RADIUS * 2, HANDLE_RADIUS * 2,
                )
            )
