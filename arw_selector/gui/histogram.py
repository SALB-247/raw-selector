"""The histogram widget.

While adjusting, you have to see with your own eyes where the tones are
piling up. Blown highlights and crushed shadows in particular are easy to
miss from the image alone.

The clipping warning triangles at either end play the same role as
Lightroom's - lit, they mean the tones on that side are being cut off.
"""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QWidget

from .i18n import tr

CHANNEL_COLORS = {
    "r": QColor(235, 90, 90),
    "g": QColor(110, 210, 120),
    "b": QColor(105, 150, 245),
    "l": QColor(190, 190, 195),
}

CLIP_THRESHOLD = 0.005
"""Once this proportion or more of all the pixels sticks to either end, it is
taken as clipping."""

LOG_KNEE = 30.0
"""How gentle the vertical log scale is. The larger it is, the further the
floor is lifted.

It used to be `log1p(v) / log1p(peak)`. That compresses harder the larger
peak is, so even a band at 1% of peak passes 60% of the height. The result
was a grey lump filling the whole widget - you could not read which tones
there were a lot of at all.

Here it is normalised by peak first and the log taken after. The degree of
compression stops depending on the photo's pixel count, and is controlled by
the one knee. 30 was picked by comparing real photographs (close to a cube
root, while showing the tail at the dark end better).
"""


def histogram_heights(values: np.ndarray, peak: float) -> np.ndarray:
    """Counts to a 0~1 height. The histogram and the curve background have to
    come out the same shape."""
    if peak <= 0:
        return np.zeros_like(values, dtype=np.float64)
    scaled = np.clip(np.asarray(values, dtype=np.float64) / peak, 0.0, 1.0)
    return np.log1p(scaled * LOG_KNEE) / np.log1p(LOG_KNEE)


class HistogramWidget(QWidget):
    """RGB + luminance histogram. Clicking switches the channel display."""

    clipping_changed = Signal(bool, bool)  # (shadow, highlight)
    overlay_toggled = Signal(bool, bool)   # (shadow, highlight overlays)

    _CORNER = 22  # corner click area (px)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(110)
        self.setMaximumHeight(150)
        self.setToolTip(tr(
            "Click the center to switch the channel display\n"
            "Top-left: shadow-clipping warning · top-right: highlight-clipping warning"
        ))

        self._histograms: dict[str, np.ndarray] = {}
        self._shadow_clip = False
        self._highlight_clip = False
        self._show_shadow = False      # shadow clipping image overlay on
        self._show_highlight = False   # highlight clipping image overlay on
        self._mode = 0  # 0: RGB, 1: luminance, 2: both

    def set_image(self, image_bgr: np.ndarray | None) -> None:
        """Recalculates the histogram from the image."""
        if image_bgr is None or image_bgr.size == 0:
            self._histograms = {}
            self.update()
            return

        # Counting every pixel is slow. A sample is accurate enough for the
        # tone distribution.
        sample = image_bgr
        if sample.shape[0] * sample.shape[1] > 400_000:
            step = int(np.sqrt(sample.shape[0] * sample.shape[1] / 400_000)) + 1
            sample = sample[::step, ::step]

        histograms = {}
        for index, key in enumerate(("b", "g", "r")):
            values = cv2.calcHist([sample], [index], None, [256], [0, 256]).flatten()
            histograms[key] = values

        gray = cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY)
        histograms["l"] = cv2.calcHist([gray], [0], None, [256], [0, 256]).flatten()

        self._histograms = histograms

        total = float(gray.size)
        shadow = float(histograms["l"][:2].sum()) / total > CLIP_THRESHOLD
        highlight = float(histograms["l"][254:].sum()) / total > CLIP_THRESHOLD
        if (shadow, highlight) != (self._shadow_clip, self._highlight_clip):
            self._shadow_clip, self._highlight_clip = shadow, highlight
            self.clipping_changed.emit(shadow, highlight)

        self.update()

    def luminance(self) -> np.ndarray | None:
        """The luminance histogram. Reused as the curve editor's background."""
        return self._histograms.get("l")

    def overlay_state(self) -> tuple[bool, bool]:
        return self._show_shadow, self._show_highlight

    def set_overlay_state(self, show_shadow: bool, show_highlight: bool) -> None:
        """Carries in what was switched on and off outside (the buttons).

        With the display state living in two places (the buttons and this
        widget) they are bound to drift apart. The buttons are made the owner
        and this side follows them.
        """
        if (show_shadow, show_highlight) == (self._show_shadow, self._show_highlight):
            return
        self._show_shadow = show_shadow
        self._show_highlight = show_highlight
        self.update()

    def mousePressEvent(self, event) -> None:
        point = event.position().toPoint()
        rect = self.rect()
        # The top-left/top-right corners toggle the clipping overlay;
        # anywhere else switches the channel.
        if point.y() <= self._CORNER and point.x() <= self._CORNER:
            self._show_shadow = not self._show_shadow
            self.overlay_toggled.emit(self._show_shadow, self._show_highlight)
        elif point.y() <= self._CORNER and point.x() >= rect.width() - self._CORNER:
            self._show_highlight = not self._show_highlight
            self.overlay_toggled.emit(self._show_shadow, self._show_highlight)
        else:
            self._mode = (self._mode + 1) % 3
        self.update()

    def _channels(self) -> tuple[str, ...]:
        return {0: ("r", "g", "b"), 1: ("l",), 2: ("l", "r", "g", "b")}[self._mode]

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.fillRect(rect, QColor(22, 22, 24))

        if not self._histograms:
            painter.setPen(QColor(110, 110, 115))
            painter.drawText(rect, Qt.AlignCenter, tr("No histogram"))
            return

        # Scale - a vertical line at every quarter point
        painter.setPen(QPen(QColor(52, 52, 58), 1))
        for fraction in (0.25, 0.5, 0.75):
            x = rect.left() + rect.width() * fraction
            painter.drawLine(int(x), rect.top(), int(x), rect.bottom())

        channels = self._channels()
        peak = max(
            (float(self._histograms[key].max()) for key in channels), default=1.0
        )
        if peak <= 0:
            peak = 1.0

        painter.setCompositionMode(QPainter.CompositionMode_Plus)
        for key in channels:
            self._draw_channel(painter, rect, self._histograms[key], peak, key)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

        self._draw_clip_markers(painter, rect)

    def _draw_channel(self, painter, rect, values, peak, key) -> None:
        color = CHANNEL_COLORS[key]
        path = QPainterPath()
        path.moveTo(rect.left(), rect.bottom())

        # Log scale - drawn linearly, one big peak flattens all the rest
        heights = histogram_heights(values, peak)
        for index in range(256):
            x = rect.left() + rect.width() * index / 255.0
            y = rect.bottom() - heights[index] * rect.height()
            path.lineTo(x, y)

        path.lineTo(rect.right(), rect.bottom())
        path.closeSubpath()

        fill = QColor(color)
        fill.setAlpha(90)
        painter.fillPath(path, fill)
        # The line is thickened a little to be easier to see (not too thick).
        painter.setPen(QPen(color, 1.6))
        painter.drawPath(path)

    def _draw_clip_markers(self, painter, rect) -> None:
        """The clipping warning triangles at either end (a click toggles the
        image overlay).

        - Overlay on: picked out with a white border (showing on screen now)
        - Clipping detected: red (tones are being cut off)
        - Otherwise: faint grey
        """
        size = 9
        for clipped, shown, x, direction, overlay_color in (
            (self._shadow_clip, self._show_shadow, rect.left() + 3, 1, QColor(90, 150, 245)),
            (self._highlight_clip, self._show_highlight, rect.right() - 3, -1, QColor(255, 90, 90)),
        ):
            if shown:
                fill = overlay_color
            elif clipped:
                fill = QColor(255, 90, 90)
            else:
                fill = QColor(70, 70, 76)
            triangle = QPolygonF([
                QPointF(x, rect.top() + 3),
                QPointF(x + direction * size, rect.top() + 3),
                QPointF(x, rect.top() + 3 + size),
            ])
            painter.setPen(QPen(QColor(240, 240, 245), 1.5) if shown else Qt.NoPen)
            painter.setBrush(fill)
            painter.drawPolygon(triangle)
