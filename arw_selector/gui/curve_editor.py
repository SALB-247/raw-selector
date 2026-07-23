"""톤 곡선 편집기.

파라메트릭 슬라이더만으로는 특정 밝기 구간을 정확히 집어 올리거나 내릴 수
없습니다. 곡선을 직접 끄는 편이 빠르고 정확합니다.

배경에 히스토그램을 깔아 두면 어느 계조를 만지고 있는지 바로 보입니다.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .i18n import tr

HANDLE_RADIUS = 5
HIT_RADIUS = 10
"""잡기 판정 반경. 화면 반경보다 넉넉해야 집기 편합니다."""

CHANNEL_COLORS = {
    "rgb": QColor(230, 230, 235),
    "red": QColor(235, 90, 90),
    "green": QColor(110, 210, 120),
    "blue": QColor(105, 150, 245),
}


class CurveEditor(QWidget):
    """드래그 가능한 톤 곡선.

    끝점 두 개는 항상 있고 지울 수 없습니다. 중간 점은 클릭으로 추가하고
    오른쪽 클릭이나 더블클릭으로 지웁니다.
    """

    points_changed = Signal(tuple)  # ((입력, 출력), ...) — 끝점 제외

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
        # 끝점을 포함한 전체 제어점. 항상 x 오름차순을 유지합니다.
        self._points: list[list[float]] = [[0.0, 0.0], [255.0, 255.0]]
        self._dragging: int | None = None
        self._histogram: np.ndarray | None = None
        # 파라메트릭 4구간 (어두운영역/어두움/밝음/밝은영역). rgb 채널 곡선에만
        # 반영합니다 — 채널별 포인트 곡선과는 별개 개념입니다.
        self._parametric = (0, 0, 0, 0)
        self._show_clip = True  # 곡선 클리핑 표시 여부

    # ------------------------------------------------------------ 상태

    def set_channel(self, channel: str) -> None:
        self._channel = channel
        self.update()

    def channel(self) -> str:
        return self._channel

    def set_histogram(self, values: np.ndarray | None) -> None:
        self._histogram = values
        self.update()

    def set_parametric(self, shadows: int, darks: int, lights: int, highlights: int) -> None:
        """파라메트릭 구간 값을 받아 곡선 그래프에 함께 표시합니다."""
        values = (int(shadows), int(darks), int(lights), int(highlights))
        if values != self._parametric:
            self._parametric = values
            self.update()

    def set_clip_markers(self, show: bool) -> None:
        """곡선의 클리핑 표시를 켜고 끕니다."""
        self._show_clip = bool(show)
        self.update()

    def _parametric_lut(self) -> np.ndarray | None:
        """파라메트릭 곡선 256칸 LUT. 값이 없으면 None (그리지 않음)."""
        if self._channel != "rgb" or not any(self._parametric):
            return None
        from ..core.develop.engine import parametric_tone_lut

        shadows, darks, lights, highlights = self._parametric
        return parametric_tone_lut(shadows, darks, lights, highlights)

    def points(self) -> tuple[tuple[int, int], ...]:
        """제어점을 반환합니다. 옮기지 않은 끝점만 생략합니다.

        끝점은 블랙/화이트 포인트라 세로로 끌 수 있습니다(mouseMoveEvent).
        예전에는 무조건 잘라내서, 끌어 놓으면 편집기의 곡선만 바뀌고 사진에는
        아무 일도 일어나지 않았습니다 — 엔진이 끝점을 (0,0)/(255,255)로 다시
        붙였기 때문입니다. 항등 위치의 끝점만 생략해 엔진이 채우게 두면,
        기존 프리셋(중간 점만 저장)과도 그대로 호환됩니다.
        """
        result = [(int(round(x)), int(round(y))) for x, y in self._points]
        if result[0] == (0, 0):
            result = result[1:]
        if result and result[-1] == (255, 255):
            result = result[:-1]
        return tuple(result)

    def set_points(self, points: tuple[tuple[int, int], ...]) -> None:
        # x=0/255에 온 점은 중간 점이 아니라 끝점입니다. 그대로 가운데에
        # 끼우면 끝점이 하나 더 생겨 곡선이 세로로 꺾입니다.
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

    # ------------------------------------------------------------ 좌표 변환

    _AXIS_MARGIN = 15  # 0~100 눈금 라벨을 넣을 아래/왼쪽 여백

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

    # ------------------------------------------------------------ 마우스

    def mousePressEvent(self, event) -> None:
        index = self._hit_test(event.position().toPoint())

        if event.button() == Qt.RightButton:
            # 끝점은 지울 수 없습니다 — 지우면 곡선이 정의되지 않습니다
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

        # 빈 곳을 누르면 점을 추가하고 바로 끌 수 있게 합니다
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
            # 왼쪽 끝점은 x=0에 고정, 출력만 조정 (블랙 포인트)
            self._points[0][1] = y
        elif index == len(self._points) - 1:
            self._points[-1][1] = y
        else:
            # 이웃을 넘어가지 못하게 막습니다. 넘으면 곡선이 뒤집힙니다.
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

    # ------------------------------------------------------------ 그리기

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self._plot_rect()

        painter.fillRect(self.rect(), QColor(24, 24, 27))
        self._draw_histogram(painter, rect)

        # 격자 + 대각선 (항등선)
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

        # 브러시를 반드시 비우고 테두리를 그립니다. 핸들에서 설정한 브러시가
        # 남아 있으면 drawRect가 테두리가 아니라 사각형 전체를 칠해서
        # 곡선과 격자가 전부 덮입니다.
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

        # 히스토그램 위젯과 **같은 함수**를 씁니다. 두 그래프의 세로 스케일이
        # 다르면 같은 사진인데 모양이 달라 보여서, 곡선을 어디에 걸어야
        # 하는지 판단이 어긋납니다.
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
        """입출력 축을 0~100(%)로 표시합니다. 8비트 값이 아니라 표준 비율."""
        painter.setPen(QColor(120, 120, 128))
        font = painter.font()
        font.setPointSize(7)
        painter.setFont(font)
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            label = str(int(frac * 100))
            # 아래쪽 = 입력축
            x = rect.left() + rect.width() * frac
            painter.drawText(
                QRectF(x - 12, rect.bottom() + 2, 24, self._AXIS_MARGIN),
                Qt.AlignHCenter | Qt.AlignTop, label,
            )
            # 왼쪽 = 출력축
            y = rect.bottom() - rect.height() * frac
            painter.drawText(
                QRectF(0, y - 7, self._AXIS_MARGIN - 2, 14),
                Qt.AlignRight | Qt.AlignVCenter, label,
            )

    def _draw_parametric(self, painter: QPainter, rect: QRect) -> None:
        """파라메트릭 구간 조정을 흐린 선으로 함께 보여 줍니다.

        포인트 곡선과 별개로, 슬라이더로 바꾼 구간 톤이 곡선 위에 어떻게
        나타나는지 눈으로 확인할 수 있게 합니다.
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
        """곡선이 계조를 잘라내는 곳을 표시합니다.

        입력이 남아 있는데 출력이 0(뭉갬)이나 255(날아감)에 붙으면 그 구간은
        디테일이 사라집니다. 하이라이트는 빨강, 섀도우는 파랑으로 알립니다.
        """
        lut = self._composite_lut()
        crush = np.where(lut <= 0.5)[0]
        blow = np.where(lut >= 254.5)[0]
        painter.setPen(Qt.NoPen)
        # 섀도우 클리핑: 입력 0보다 큰데도 출력 0이면 그 구간
        if crush.size and crush.max() > 0:
            painter.setBrush(QColor(90, 150, 245))
            x0 = rect.left()
            x1 = rect.left() + rect.width() * crush.max() / 255.0
            painter.drawRect(QRectF(x0, rect.bottom() - 3, x1 - x0, 3))
        # 하이라이트 클리핑: 입력 255보다 작은데도 출력 255면 그 구간
        if blow.size and blow.min() < 255:
            painter.setBrush(QColor(235, 90, 90))
            x0 = rect.left() + rect.width() * blow.min() / 255.0
            x1 = rect.right()
            painter.drawRect(QRectF(x0, rect.top(), x1 - x0, 3))

    def _composite_lut(self) -> np.ndarray:
        """파라메트릭 다음에 포인트 곡선을 적용한 최종 응답 (클리핑 판정용)."""
        base = self._parametric_lut()
        base = base if base is not None else np.arange(256, dtype=np.float32)
        point = self._curve_lut()
        return np.clip(np.interp(base, np.arange(256), point), 0, 255)

    def _curve_lut(self) -> np.ndarray:
        """현재 제어점으로 만든 256칸 곡선. 엔진과 같은 부드러운 보간을 씁니다."""
        from ..core.develop.engine import smooth_curve_lut

        return smooth_curve_lut([(p[0], p[1]) for p in self._points])

    def _draw_curve(self, painter: QPainter, rect: QRect) -> None:
        lut = self._curve_lut()
        color = CHANNEL_COLORS.get(self._channel, CHANNEL_COLORS["rgb"])

        path = QPainterPath()
        path.moveTo(self._to_screen(0, lut[0]))
        for value in range(1, 256):
            path.lineTo(self._to_screen(value, lut[value]))

        # 곡선이 부드러워졌으니 선을 조금 굵혀 가시성을 높입니다.
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
            # QRectF로 명시합니다. (QPointF, 반지름, 반지름) 형태는 PySide6에서
            # 오버로드가 엉뚱하게 잡혀 위젯 전체가 칠해졌습니다.
            painter.drawEllipse(
                QRectF(
                    center.x() - HANDLE_RADIUS, center.y() - HANDLE_RADIUS,
                    HANDLE_RADIUS * 2, HANDLE_RADIUS * 2,
                )
            )
