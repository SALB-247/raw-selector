"""히스토그램 위젯.

보정하면서 계조가 어디로 몰리는지 육안으로 봐야 합니다. 특히 하이라이트가
날아가거나 섀도우가 뭉개지는 건 이미지만 봐서는 놓치기 쉽습니다.

양 끝의 클리핑 경고 삼각형은 Lightroom과 같은 역할이다 — 켜지면 그쪽
계조가 잘려 나가고 있다는 뜻입니다.
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
"""전체 픽셀의 이 비율 이상이 양 끝에 붙으면 클리핑으로 봅니다."""

LOG_KNEE = 30.0
"""세로 로그 스케일의 완만함. 클수록 바닥을 더 들어 올립니다.

예전에는 `log1p(v) / log1p(peak)`였습니다. 이건 peak가 클수록 압축이
세져서, peak의 1%짜리 구간도 높이 60%를 넘습니다. 결과는 화면 전체를
채운 회색 덩어리 — 어느 계조가 많은지 전혀 읽을 수 없었습니다.

여기서는 먼저 peak로 정규화한 뒤 로그를 겁니다. 압축 정도가 사진의
화소 수와 무관해지고, knee 하나로 조절됩니다. 30은 실사진으로 비교해
고른 값입니다(세제곱근과 비슷하면서 어두운 쪽 꼬리가 더 잘 보입니다).
"""


def histogram_heights(values: np.ndarray, peak: float) -> np.ndarray:
    """빈도를 0~1 높이로. 히스토그램과 곡선 배경이 같은 모양이어야 합니다."""
    if peak <= 0:
        return np.zeros_like(values, dtype=np.float64)
    scaled = np.clip(np.asarray(values, dtype=np.float64) / peak, 0.0, 1.0)
    return np.log1p(scaled * LOG_KNEE) / np.log1p(LOG_KNEE)


class HistogramWidget(QWidget):
    """RGB + 휘도 히스토그램. 클릭하면 채널 표시를 바꿉니다."""

    clipping_changed = Signal(bool, bool)  # (섀도우, 하이라이트)
    overlay_toggled = Signal(bool, bool)   # (섀도우 오버레이, 하이라이트 오버레이)

    _CORNER = 22  # 코너 클릭 영역(px)

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
        self._show_shadow = False      # 섀도우 클리핑 이미지 오버레이 켜짐
        self._show_highlight = False   # 하이라이트 클리핑 이미지 오버레이 켜짐
        self._mode = 0  # 0: RGB, 1: 휘도, 2: 둘 다

    def set_image(self, image_bgr: np.ndarray | None) -> None:
        """이미지에서 히스토그램을 다시 계산합니다."""
        if image_bgr is None or image_bgr.size == 0:
            self._histograms = {}
            self.update()
            return

        # 전체 픽셀을 다 세면 느립니다. 계조 분포는 표본으로도 충분히 정확합니다.
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
        """휘도 히스토그램. 곡선 편집기 배경으로 재사용합니다."""
        return self._histograms.get("l")

    def overlay_state(self) -> tuple[bool, bool]:
        return self._show_shadow, self._show_highlight

    def set_overlay_state(self, show_shadow: bool, show_highlight: bool) -> None:
        """바깥(버튼)에서 켜고 끈 것을 반영합니다.

        표시 상태가 두 군데(버튼과 이 위젯)에 있으면 반드시 어긋납니다.
        버튼을 주인으로 두고 여기는 따라가게 합니다.
        """
        if (show_shadow, show_highlight) == (self._show_shadow, self._show_highlight):
            return
        self._show_shadow = show_shadow
        self._show_highlight = show_highlight
        self.update()

    def mousePressEvent(self, event) -> None:
        point = event.position().toPoint()
        rect = self.rect()
        # 좌/우 상단 코너는 클리핑 오버레이 토글, 그 밖은 채널 전환입니다.
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

        # 눈금 — 1/4 지점마다 세로선
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

        # 로그 스케일 — 선형으로 그리면 큰 봉우리 하나가 나머지를 다 눌러 버립니다
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
        # 선을 조금 굵혀 가시성을 높입니다 (너무 굵지 않게).
        painter.setPen(QPen(color, 1.6))
        painter.drawPath(path)

    def _draw_clip_markers(self, painter, rect) -> None:
        """양 끝 클리핑 경고 삼각형 (클릭하면 이미지 오버레이 토글).

        - 오버레이 켜짐: 흰 테두리로 강조 (지금 화면에 표시 중)
        - 클리핑 감지: 빨강 (계조가 잘리고 있음)
        - 평상시: 흐린 회색
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
