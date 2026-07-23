"""미리보기 이미지 뷰.

슬라이더로 크롭 네 변을 맞추는 건 사실상 불가능합니다. 이미지 위에서 직접
끌어야 합니다. 이 위젯이 그 역할을 합니다. 방사형·선형 마스크도 같은
이유로 여기서 끕니다 — 숫자 네 개로 원의 자리를 맞출 수는 없습니다.

크롭 좌표는 항상 0~1 정규화 값으로 주고받습니다. 화면 크기나 원본 해상도가
달라도 같은 의미를 유지해야 하기 때문입니다. 마스크 도형도 마찬가지입니다.
"""

from __future__ import annotations

import math
from enum import Enum, auto

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QWidget

from .i18n import tr

HANDLE_SIZE = 9
"""핸들 반경 (픽셀). 너무 작으면 잡기 어렵습니다."""

MIN_CROP_FRACTION = 0.05
"""크롭이 이보다 작아지지 않게 막습니다."""

SHAPE_HANDLE_RADIUS = 5.0
"""도형 조작점을 그리는 반지름 (픽셀). 사진을 가리지 않을 만큼만."""

SHAPE_GRAB = 12
"""도형 조작점을 '잡았다'고 볼 거리 (픽셀).

크롭 핸들보다 넉넉합니다. 크롭 핸들은 변을 따라 길게 뻗어 있어 대충 눌러도
잡히지만, 도형 조작점은 점 하나뿐이라 같은 여유로는 자꾸 헛나갑니다.
"""

MIN_SHAPE_RADIUS = 0.01
"""방사형 반경의 하한 (정규화).

0까지 내려가면 조작점 넷이 중심 한 점에 겹쳐, 다시 키울 방법이 사라집니다.
"""

ROTATE_HANDLE_GAP = 22
"""회전 조작점을 타원 밖으로 띄우는 거리 (픽셀). 반경 조작점과 안 겹칠 만큼."""


def _unit(value: float) -> float:
    """0~1 밖으로 나가지 않게 자릅니다.

    조작점이 이미지 밖으로 나가면 화면에서 사라져 되돌릴 방법이 없습니다.
    """
    return min(max(float(value), 0.0), 1.0)


class Handle(Enum):
    NONE = auto()
    MOVE = auto()
    LEFT = auto()
    RIGHT = auto()
    TOP = auto()
    BOTTOM = auto()
    TOP_LEFT = auto()
    TOP_RIGHT = auto()
    BOTTOM_LEFT = auto()
    BOTTOM_RIGHT = auto()


class ShapeHandle(Enum):
    """방사형·선형 마스크에서 지금 잡고 있는 지점."""

    NONE = auto()
    CENTER = auto()    # 방사형 중심 이동 / 선형은 두 끝을 함께 이동
    RADIUS_X = auto()  # 방사형 가로 반경 (회전했으면 그 축)
    RADIUS_Y = auto()
    ROTATE = auto()
    START = auto()     # 선형 0% 쪽
    END = auto()       # 선형 100% 쪽


SHAPE_KINDS = ("radial", "linear")
"""이미지 위에서 직접 끌 수 있는 마스크 종류 (MaskType 값과 같은 문자열)."""


_CURSORS = {
    Handle.MOVE: Qt.SizeAllCursor,
    Handle.LEFT: Qt.SizeHorCursor,
    Handle.RIGHT: Qt.SizeHorCursor,
    Handle.TOP: Qt.SizeVerCursor,
    Handle.BOTTOM: Qt.SizeVerCursor,
    Handle.TOP_LEFT: Qt.SizeFDiagCursor,
    Handle.BOTTOM_RIGHT: Qt.SizeFDiagCursor,
    Handle.TOP_RIGHT: Qt.SizeBDiagCursor,
    Handle.BOTTOM_LEFT: Qt.SizeBDiagCursor,
}


class ImageView(QWidget):
    """이미지를 비율 유지해 그리고, 크롭 모드에서는 직접 조작하게 합니다."""

    crop_changed = Signal(float, float, float, float)  # left, top, right, bottom
    crop_finished = Signal()
    zoom_changed = Signal(float)
    pan_finished = Signal()
    """화면을 끌어 옮기고 손을 뗐을 때.

    확대 상태에서는 보이는 곳만 고화질로 만들기 때문에, 옮기면 새로 드러난
    부분을 다시 만들어야 합니다."""
    color_picked = Signal(float, float)  # 이미지 내 상대 좌표 (0~1)
    brush_painted = Signal(float, float)  # 브러시로 칠한 지점 (0~1 상대 좌표)
    clicked = Signal(float, float)
    """끌지 않고 그냥 누른 지점 (0~1 상대 좌표).

    확대 상태에서는 누르는 순간 팬이 시작되므로, **움직이지 않고 뗐을 때만**
    보냅니다. 그래야 화면을 옮기려다 실수로 무언가를 고르는 일이 없습니다.
    """
    shape_changed = Signal(dict)
    """방사형·선형 마스크를 끄는 중 — 바뀐 정규화 파라미터 전체.

    끄는 동안에는 이것만 나갑니다. 값을 받아 두기만 하고 다시 그리지는
    마십시오 — 윤곽선은 이 위젯이 스스로 그립니다.
    """
    shape_finished = Signal()
    """도형에서 손을 뗐을 때. 무거운 재렌더는 여기서 돌립니다."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setMouseTracking(True)
        self.setStyleSheet("background: #141416;")

        self._pixmap: QPixmap | None = None
        self._crop = (0.0, 0.0, 1.0, 1.0)
        self._crop_mode = False
        self._ratio: float | None = None
        self._active = Handle.NONE
        self._drag_origin: QPoint | None = None
        self._drag_start_crop = (0.0, 0.0, 1.0, 1.0)
        self._busy = False
        self._message = ""

        # 줌/팬. zoom 1.0이 "창에 맞춤"이고, offset은 그 상태 기준 픽셀 이동.
        self._zoom = 1.0
        self._offset = QPoint(0, 0)
        self._panning = False
        self._press_pos: QPoint | None = None
        self._pan_origin: QPoint | None = None
        self._pan_start_offset = QPoint(0, 0)

        # 방사형·선형 마스크 조작. kind가 None이면 그리지도 받지도 않습니다.
        self._shape_kind: str | None = None
        self._shape_params: dict = {}
        self._shape_size = 1.0
        self._shape_active = ShapeHandle.NONE
        self._shape_origin: QPoint | None = None
        self._shape_start: dict = {}
        """드래그를 시작할 때의 파라미터. 이동량을 **시작값**에 더해야
        커서와 도형이 어긋나지 않습니다(직전 값에 누적하면 밀립니다)."""

    # ------------------------------------------------------------ 상태

    def set_pixmap(self, pixmap: QPixmap | None) -> None:
        self._pixmap = pixmap
        self.update()

    def set_message(self, message: str) -> None:
        self._message = message
        self.update()

    def set_busy(self, busy: bool) -> None:
        """보정 중 표시. 렌더가 200ms 정도 걸려서 알려주지 않으면 멈춘 줄 압니다."""
        if self._busy != busy:
            self._busy = busy
            self.update()

    def set_crop(self, left: float, top: float, right: float, bottom: float) -> None:
        self._crop = (left, top, right, bottom)
        self.update()

    def crop(self) -> tuple[float, float, float, float]:
        return self._crop

    def set_crop_mode(self, enabled: bool) -> None:
        self._crop_mode = enabled
        # 크롭과 도형은 같은 자리에서 서로 다른 것을 잡습니다. 크롭이
        # 이기고, 도형은 그리지도 받지도 않습니다.
        self._cancel_shape_drag()
        self.setCursor(Qt.CrossCursor if enabled else Qt.ArrowCursor)
        self.update()

    def set_ratio(self, ratio: float | None) -> None:
        """가로/세로 비 고정. None이면 자유.

        비율을 새로 고르면 이미지 중앙에 최대 크기로 잡아 줍니다. 사용자가
        비율을 바꾸는 시점에는 보통 "이 비율로 전체를 다시 잡고 싶다"는
        뜻이지, 지금 크롭의 한 귀퉁이를 유지하고 싶은 게 아닙니다.
        """
        self._ratio = ratio
        if ratio:
            self._center_ratio_crop(ratio)
            self._emit_crop()
        self.update()

    def _center_ratio_crop(self, ratio: float) -> None:
        """주어진 비율로 이미지 중앙에 들어가는 최대 크롭을 만듭니다."""
        base = self._image_rect()
        if base.isEmpty():
            return

        image_ratio = base.width() / base.height()
        # 정규화 공간에서의 목표 비 = 원하는 비 / 이미지 비
        target = ratio / image_ratio

        if target >= 1.0:
            width, height = 1.0, 1.0 / target
        else:
            width, height = target, 1.0

        left = (1.0 - width) / 2.0
        top = (1.0 - height) / 2.0
        self._crop = (left, top, left + width, top + height)

    # ------------------------------------------------------------ 좌표 변환

    def _image_rect(self) -> QRect:
        """위젯 안에서 이미지가 실제로 그려지는 사각형 (줌·팬 반영)."""
        if self._pixmap is None or self._pixmap.isNull():
            return QRect()

        available = self.rect()
        scaled = self._pixmap.size().scaled(available.size(), Qt.KeepAspectRatio)
        width = max(1, int(scaled.width() * self._zoom))
        height = max(1, int(scaled.height() * self._zoom))

        x = available.left() + (available.width() - width) // 2 + self._offset.x()
        y = available.top() + (available.height() - height) // 2 + self._offset.y()
        return QRect(x, y, width, height)

    # ------------------------------------------------------------ 줌 / 팬

    def zoom(self) -> float:
        return self._zoom

    def visible_region(self, pad: float = 0.06) -> tuple[float, float, float, float]:
        """지금 화면에 보이는 부분을 이미지 대비 비율(0~1)로 돌려줍니다.

        확대 상태에서 고화질을 다시 만들 때, 안 보이는 데까지 만들 이유가
        없습니다. 실측(R6M3 27MP): 4배 확대면 보이는 면적이 전체의 6%라
        보정이 3.4초에서 0.22초로 줄어듭니다.

        pad는 여유분입니다. 조금 밀어 봐도 곧바로 빈 곳이 드러나지 않게
        가장자리를 넉넉히 잡습니다.
        """
        rect = self._image_rect()
        if rect.isEmpty():
            return (0.0, 0.0, 1.0, 1.0)

        available = self.rect()
        left = (available.left() - rect.left()) / rect.width()
        top = (available.top() - rect.top()) / rect.height()
        right = (available.right() - rect.left()) / rect.width()
        bottom = (available.bottom() - rect.top()) / rect.height()

        left, top = left - pad, top - pad
        right, bottom = right + pad, bottom + pad

        left = min(max(0.0, left), 1.0)
        top = min(max(0.0, top), 1.0)
        right = min(max(0.0, right), 1.0)
        bottom = min(max(0.0, bottom), 1.0)
        if right - left < 0.01 or bottom - top < 0.01:
            return (0.0, 0.0, 1.0, 1.0)
        return (left, top, right, bottom)

    def set_zoom(self, zoom: float, focus: QPoint | None = None) -> None:
        """줌 배율을 바꿉니다.

        focus를 주면 그 지점이 화면에서 제자리에 머물도록 오프셋을 맞춥니다.
        안 그러면 확대할 때마다 보던 곳이 화면 밖으로 밀려납니다.
        """
        new_zoom = float(np.clip(zoom, 1.0, 16.0))
        if abs(new_zoom - self._zoom) < 1e-6:
            return

        anchor = focus or self.rect().center()
        before = self._image_rect()
        if not before.isEmpty():
            # 앵커가 이미지 안에서 차지하는 비율
            rx = (anchor.x() - before.left()) / before.width()
            ry = (anchor.y() - before.top()) / before.height()

        self._zoom = new_zoom
        if new_zoom <= 1.0:
            self._offset = QPoint(0, 0)
        elif not before.isEmpty():
            after = self._image_rect()
            # 앵커가 같은 화면 위치에 오도록 밉니다
            self._offset += QPoint(
                int(anchor.x() - (after.left() + rx * after.width())),
                int(anchor.y() - (after.top() + ry * after.height())),
            )
            self._clamp_offset()

        self.update()

    def reset_view(self) -> None:
        self._zoom = 1.0
        self._offset = QPoint(0, 0)
        self.update()

    def zoom_to_roi(self, roi: tuple[int, int, int, int], scale: float) -> None:
        """초점 판정에 쓴 영역을 화면 가득 채웁니다.

        roi는 원본 프리뷰 좌표계, scale은 표시 중인 이미지와의 배율입니다.
        눈이 실제로 맞았는지는 확대해서 봐야 알 수 있습니다.
        """
        if self._pixmap is None or self._pixmap.isNull():
            return

        x, y, w, h = roi
        if w <= 0 or h <= 0:
            return

        # 표시 이미지 안에서 ROI가 차지하는 비율
        pixmap_width = self._pixmap.width()
        pixmap_height = self._pixmap.height()
        roi_width = max(1.0, w * scale)
        roi_height = max(1.0, h * scale)

        # ROI가 화면의 60%를 차지하도록 (여유를 둬야 주변 맥락이 보인다)
        target = min(
            pixmap_width / roi_width, pixmap_height / roi_height, 16.0
        ) * 0.6
        self._zoom = float(np.clip(target, 1.0, 16.0))
        self._offset = QPoint(0, 0)

        rect = self._image_rect()
        if rect.isEmpty():
            return

        # ROI 중심이 화면 중앙에 오도록
        center_ratio_x = (x + w / 2.0) * scale / pixmap_width
        center_ratio_y = (y + h / 2.0) * scale / pixmap_height
        self._offset = QPoint(
            int(self.rect().center().x() - (rect.left() + center_ratio_x * rect.width())),
            int(self.rect().center().y() - (rect.top() + center_ratio_y * rect.height())),
        )
        self._clamp_offset()
        self.update()

    def _clamp_offset(self) -> None:
        """이미지가 화면 밖으로 완전히 나가지 않게 막습니다."""
        rect = self._image_rect()
        if rect.isEmpty():
            return

        available = self.rect()
        margin_x = max(0, (rect.width() - available.width()) // 2)
        margin_y = max(0, (rect.height() - available.height()) // 2)
        self._offset = QPoint(
            int(np.clip(self._offset.x(), -margin_x, margin_x)),
            int(np.clip(self._offset.y(), -margin_y, margin_y)),
        )

    def wheelEvent(self, event) -> None:
        """휠로 확대/축소. 커서 위치를 기준으로 합니다."""
        if self._pixmap is None or self._pixmap.isNull():
            return
        step = 1.25 if event.angleDelta().y() > 0 else 1 / 1.25
        self.set_zoom(self._zoom * step, event.position().toPoint())
        self.zoom_changed.emit(self._zoom)
        event.accept()

    def _crop_rect(self) -> QRect:
        """정규화 크롭을 화면 좌표로."""
        base = self._image_rect()
        if base.isEmpty():
            return QRect()
        left, top, right, bottom = self._crop
        return QRect(
            base.left() + int(left * base.width()),
            base.top() + int(top * base.height()),
            max(1, int((right - left) * base.width())),
            max(1, int((bottom - top) * base.height())),
        )

    def _to_fraction(self, point: QPoint) -> tuple[float, float]:
        base = self._image_rect()
        if base.isEmpty():
            return 0.0, 0.0
        x = (point.x() - base.left()) / base.width()
        y = (point.y() - base.top()) / base.height()
        return min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)

    # -------------------------------------------------- 도형 마스크 (방사형/선형)

    def set_shape(self, kind: str | None, params: dict | None = None,
                  size: float = 1.0) -> None:
        """이미지 위에서 끌 마스크 도형을 정합니다. kind가 None이면 해제.

        size는 '범위 %' 배율입니다. 실제 알파가 이 배율만큼 커지고 작아지므로
        (masks._radial_alpha) 윤곽선도 같이 반영해야 화면과 결과가 맞습니다.
        """
        self._shape_kind = kind if kind in SHAPE_KINDS else None
        self._shape_params = dict(params or {})
        self._shape_size = float(size)
        self._cancel_shape_drag()
        self.update()

    def shape_params(self) -> dict:
        """지금 그리고 있는 도형의 정규화 파라미터 사본."""
        return dict(self._shape_params)

    def _cancel_shape_drag(self) -> None:
        self._shape_active = ShapeHandle.NONE
        self._shape_origin = None

    def _shape_scale(self) -> float:
        """반경에 곱해 둘 범위 배율. 0으로는 절대 내려가지 않습니다.

        범위 슬라이더를 0%까지 내리면 배율이 0이 되어, 반경을 되돌려 계산할
        때 0으로 나누게 됩니다. 조작 쪽만 하한을 둡니다 — 윤곽선은 실제
        배율 그대로 그려야 화면과 결과가 어긋나지 않습니다.
        """
        return max(0.05, self._shape_size)

    def _radial_frame(self, base: QRect, params: dict | None = None):
        """방사형 타원의 (중심, 가로축 벡터, 세로축 벡터). 전부 위젯 픽셀.

        `_image_rect()`는 비율을 유지한 채 줌·팬만 반영하므로 이미지 픽셀 →
        위젯은 가로세로가 **같은 배율**입니다. 덕분에 회전각을 따로 보정할
        필요가 없습니다 — 이 전제가 깨지면(비율을 무시하고 늘려 그리면)
        타원이 각도부터 어긋납니다.
        """
        params = self._shape_params if params is None else params
        size = self._shape_size
        cx = float(params.get("cx", 0.5))
        cy = float(params.get("cy", 0.5))
        rx = max(MIN_SHAPE_RADIUS, float(params.get("rx", 0.3))) * size
        ry = max(MIN_SHAPE_RADIUS, float(params.get("ry", 0.3))) * size
        angle = math.radians(float(params.get("rotation", 0.0)))
        ca, sa = math.cos(angle), math.sin(angle)

        centre = (base.left() + cx * base.width(),
                  base.top() + cy * base.height())
        # 가로 반경은 폭, 세로 반경은 높이 기준입니다(_radial_alpha와 동일).
        u = (rx * base.width() * ca, rx * base.width() * sa)
        v = (-ry * base.height() * sa, ry * base.height() * ca)
        return centre, u, v

    def _linear_points(self, base: QRect, params: dict | None = None):
        """선형 그라디언트의 (시작점, 끝점). 위젯 픽셀."""
        params = self._shape_params if params is None else params
        x0 = float(params.get("x0", 0.5))
        y0 = float(params.get("y0", 0.0))
        x1 = float(params.get("x1", 0.5))
        y1 = float(params.get("y1", 0.4))
        return (
            (base.left() + x0 * base.width(), base.top() + y0 * base.height()),
            (base.left() + x1 * base.width(), base.top() + y1 * base.height()),
        )

    def _shape_handles(self) -> list[tuple[ShapeHandle, tuple[float, float]]]:
        """(핸들, 위젯 좌표) 목록.

        방사형의 반경 조작점은 **축마다 양쪽에 하나씩** 둡니다. 한쪽만 두면
        타원이 화면 밖으로 반쯤 나갔을 때 잡을 방법이 없습니다.

        중심을 목록 맨 뒤에 둡니다. 반경이 아주 작아 조작점이 전부 한 점에
        겹치면 가장 가까운 것을 고르는 규칙이 동점이 되는데, 그때 중심이
        이기면 타원을 다시 키울 수 없게 됩니다.
        """
        base = self._image_rect()
        if base.isEmpty() or not self._shape_kind:
            return []

        if self._shape_kind == "linear":
            start, end = self._linear_points(base)
            middle = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
            return [(ShapeHandle.START, start), (ShapeHandle.END, end),
                    (ShapeHandle.CENTER, middle)]

        centre, u, v = self._radial_frame(base)
        length = math.hypot(*u) or 1.0
        reach = 1.0 + ROTATE_HANDLE_GAP / length
        return [
            (ShapeHandle.ROTATE, (centre[0] + u[0] * reach,
                                  centre[1] + u[1] * reach)),
            (ShapeHandle.RADIUS_X, (centre[0] + u[0], centre[1] + u[1])),
            (ShapeHandle.RADIUS_X, (centre[0] - u[0], centre[1] - u[1])),
            (ShapeHandle.RADIUS_Y, (centre[0] + v[0], centre[1] + v[1])),
            (ShapeHandle.RADIUS_Y, (centre[0] - v[0], centre[1] - v[1])),
            (ShapeHandle.CENTER, centre),
        ]

    def _shape_handle_at(self, point: QPoint) -> ShapeHandle:
        """가장 가까운 조작점. 동점이면 목록 앞쪽이 이깁니다."""
        best, best_distance = ShapeHandle.NONE, float(SHAPE_GRAB)
        for handle, (x, y) in self._shape_handles():
            distance = math.hypot(point.x() - x, point.y() - y)
            if distance < best_distance:
                best, best_distance = handle, distance
        return best

    def _drag_shape(self, point: QPoint) -> None:
        """끌고 있는 조작점을 따라 정규화 파라미터를 다시 냅니다."""
        base = self._image_rect()
        if base.isEmpty() or self._shape_origin is None:
            return

        start = self._shape_start
        params = dict(start)
        dx = (point.x() - self._shape_origin.x()) / base.width()
        dy = (point.y() - self._shape_origin.y()) / base.height()

        if self._shape_kind == "linear":
            self._drag_linear(params, start, dx, dy)
        else:
            self._drag_radial(params, start, base, point, dx, dy)

        self._shape_params = params
        self.shape_changed.emit(dict(params))
        self.update()

    _LINEAR_DEFAULTS = {"x0": 0.5, "y0": 0.0, "x1": 0.5, "y1": 0.4}

    def _drag_linear(self, params: dict, start: dict,
                     dx: float, dy: float) -> None:
        values = {key: float(start.get(key, default))
                  for key, default in self._LINEAR_DEFAULTS.items()}

        if self._shape_active is ShapeHandle.START:
            params["x0"] = _unit(values["x0"] + dx)
            params["y0"] = _unit(values["y0"] + dy)
            return
        if self._shape_active is ShapeHandle.END:
            params["x1"] = _unit(values["x1"] + dx)
            params["y1"] = _unit(values["y1"] + dy)
            return

        # 두 끝을 함께 밉니다. 각각 따로 자르면 한쪽이 가장자리에 닿는 순간
        # 나머지만 계속 움직여, 옮기기만 했는데 그라디언트 방향이 변합니다.
        xs = (values["x0"], values["x1"])
        ys = (values["y0"], values["y1"])
        dx = min(max(dx, -min(xs)), 1.0 - max(xs))
        dy = min(max(dy, -min(ys)), 1.0 - max(ys))
        params["x0"], params["x1"] = xs[0] + dx, xs[1] + dx
        params["y0"], params["y1"] = ys[0] + dy, ys[1] + dy

    def _drag_radial(self, params: dict, start: dict, base: QRect,
                     point: QPoint, dx: float, dy: float) -> None:
        if self._shape_active is ShapeHandle.CENTER:
            params["cx"] = _unit(float(start.get("cx", 0.5)) + dx)
            params["cy"] = _unit(float(start.get("cy", 0.5)) + dy)
            return

        # 반경·회전은 이동량이 아니라 **중심에서 커서까지**로 셉니다. 잡은
        # 자리가 조작점과 몇 픽셀 어긋나 있어도 그 오차가 누적되지 않습니다.
        angle = math.radians(float(start.get("rotation", 0.0)))
        ca, sa = math.cos(angle), math.sin(angle)
        ox = point.x() - (base.left() + float(start.get("cx", 0.5)) * base.width())
        oy = point.y() - (base.top() + float(start.get("cy", 0.5)) * base.height())

        if self._shape_active is ShapeHandle.ROTATE:
            params["rotation"] = math.degrees(math.atan2(oy, ox)) % 360.0
        elif self._shape_active is ShapeHandle.RADIUS_X:
            length = abs(ox * ca + oy * sa) / base.width()
            params["rx"] = max(MIN_SHAPE_RADIUS, length / self._shape_scale())
        elif self._shape_active is ShapeHandle.RADIUS_Y:
            length = abs(-ox * sa + oy * ca) / base.height()
            params["ry"] = max(MIN_SHAPE_RADIUS, length / self._shape_scale())

    # ------------------------------------------------------------ 마우스

    def _handle_at(self, point: QPoint) -> Handle:
        rect = self._crop_rect()
        if rect.isEmpty():
            return Handle.NONE

        near_left = abs(point.x() - rect.left()) <= HANDLE_SIZE
        near_right = abs(point.x() - rect.right()) <= HANDLE_SIZE
        near_top = abs(point.y() - rect.top()) <= HANDLE_SIZE
        near_bottom = abs(point.y() - rect.bottom()) <= HANDLE_SIZE
        inside_x = rect.left() - HANDLE_SIZE <= point.x() <= rect.right() + HANDLE_SIZE
        inside_y = rect.top() - HANDLE_SIZE <= point.y() <= rect.bottom() + HANDLE_SIZE

        if near_left and near_top:
            return Handle.TOP_LEFT
        if near_right and near_top:
            return Handle.TOP_RIGHT
        if near_left and near_bottom:
            return Handle.BOTTOM_LEFT
        if near_right and near_bottom:
            return Handle.BOTTOM_RIGHT
        if near_left and inside_y:
            return Handle.LEFT
        if near_right and inside_y:
            return Handle.RIGHT
        if near_top and inside_x:
            return Handle.TOP
        if near_bottom and inside_x:
            return Handle.BOTTOM
        if rect.contains(point):
            return Handle.MOVE
        return Handle.NONE

    def set_pick_mode(self, enabled: bool) -> None:
        """스포이드 모드. 클릭하면 그 지점의 색을 알려 줍니다."""
        self._pick_mode = enabled
        self.setCursor(Qt.CrossCursor if enabled else Qt.ArrowCursor)

    def set_brush_mode(self, enabled: bool) -> None:
        """브러시 모드. 드래그하는 동안 지나간 지점을 계속 알려 줍니다."""
        self._brush_mode = enabled
        self._brushing = False
        self._brush_pos = None
        self._cancel_shape_drag()
        # 붓 크기를 원으로 보여 주므로 커서 자체는 숨깁니다
        self.setCursor(Qt.BlankCursor if enabled else Qt.ArrowCursor)
        self.update()

    def set_brush_radius(self, ratio: float) -> None:
        """붓 반지름 (이미지 짧은 변 대비 비율). 미리보기 원 크기입니다."""
        self._brush_radius_ratio = max(0.002, float(ratio))
        self.update()

    def set_brush_erasing(self, erasing: bool) -> None:
        """지우개 모드면 미리보기 원 색을 다르게 그립니다."""
        self._brush_erasing = bool(erasing)
        self.update()

    def _emit_brush(self, point) -> bool:
        """포인터가 이미지 안이면 상대 좌표로 알립니다."""
        base = self._image_rect()
        if base.isEmpty():
            return False
        x = (point.x() - base.left()) / base.width()
        y = (point.y() - base.top()) / base.height()
        if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
            self.brush_painted.emit(x, y)
            return True
        return False

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return

        if getattr(self, "_brush_mode", False):
            self._brushing = True
            self._brush_pos = event.position().toPoint()
            self._emit_brush(self._brush_pos)
            self.update()
            return

        if getattr(self, "_pick_mode", False):
            base = self._image_rect()
            if not base.isEmpty():
                x = (event.position().toPoint().x() - base.left()) / base.width()
                y = (event.position().toPoint().y() - base.top()) / base.height()
                if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
                    self.color_picked.emit(x, y)
            return

        # 도형 마스크 조작은 **크롭보다 뒤, 팬보다 앞**입니다. 크롭 모드에서는
        # 두 조작이 같은 자리에서 겹치므로 크롭이 이기고, 조작점을 잡았을
        # 때는 화면이 따라 움직이면 안 되므로 팬보다 먼저 봅니다.
        if self._shape_kind and not self._crop_mode:
            handle = self._shape_handle_at(event.position().toPoint())
            if handle is not ShapeHandle.NONE:
                self._shape_active = handle
                self._shape_origin = event.position().toPoint()
                self._shape_start = dict(self._shape_params)
                self.update()
                return

        # 크롭 모드가 아니면 드래그는 팬입니다
        if not self._crop_mode:
            self._press_pos = event.position().toPoint()
            if self._zoom > 1.0:
                self._panning = True
                self._pan_origin = event.position().toPoint()
                self._pan_start_offset = QPoint(self._offset)
                self.setCursor(Qt.ClosedHandCursor)
            return

        self._active = self._handle_at(event.position().toPoint())
        self._drag_origin = event.position().toPoint()
        self._drag_start_crop = self._crop

    def mouseMoveEvent(self, event) -> None:
        if getattr(self, "_brush_mode", False):
            # 누르지 않고 움직여도 붓 크기를 보여 줘야 어디에 얼마나 칠할지 압니다
            self._brush_pos = event.position().toPoint()
            if getattr(self, "_brushing", False):
                self._emit_brush(self._brush_pos)
            self.update()
            return

        if self._shape_active is not ShapeHandle.NONE:
            self._drag_shape(event.position().toPoint())
            return

        if (self._shape_kind and not self._crop_mode and not self._panning
                and self._shape_handle_at(event.position().toPoint())
                is not ShapeHandle.NONE):
            # 잡을 수 있는 점이라는 걸 알려 줍니다. 안 그러면 손 모양 커서만
            # 보여서 화면을 옮기는 자리인 줄 압니다.
            self.setCursor(Qt.SizeAllCursor)
            return

        if not self._crop_mode:
            if self._panning and self._pan_origin is not None:
                self._offset = self._pan_start_offset + (event.position().toPoint() - self._pan_origin)
                self._clamp_offset()
                self.update()
            else:
                self.setCursor(
                    Qt.OpenHandCursor if self._zoom > 1.0 else Qt.ArrowCursor
                )
            return

        if self._active is Handle.NONE or self._drag_origin is None:
            self.setCursor(QCursor(_CURSORS.get(self._handle_at(event.position().toPoint()), Qt.CrossCursor)))
            return

        left, top, right, bottom = self._drag_start_crop
        base = self._image_rect()
        if base.isEmpty():
            return

        dx = (event.position().toPoint().x() - self._drag_origin.x()) / base.width()
        dy = (event.position().toPoint().y() - self._drag_origin.y()) / base.height()

        if self._active is Handle.MOVE:
            # 이동은 크기를 유지한 채 경계 안에서만
            width, height = right - left, bottom - top
            left = min(max(left + dx, 0.0), 1.0 - width)
            top = min(max(top + dy, 0.0), 1.0 - height)
            right, bottom = left + width, top + height
        else:
            if self._active in (Handle.LEFT, Handle.TOP_LEFT, Handle.BOTTOM_LEFT):
                left = min(max(left + dx, 0.0), right - MIN_CROP_FRACTION)
            if self._active in (Handle.RIGHT, Handle.TOP_RIGHT, Handle.BOTTOM_RIGHT):
                right = max(min(right + dx, 1.0), left + MIN_CROP_FRACTION)
            if self._active in (Handle.TOP, Handle.TOP_LEFT, Handle.TOP_RIGHT):
                top = min(max(top + dy, 0.0), bottom - MIN_CROP_FRACTION)
            if self._active in (Handle.BOTTOM, Handle.BOTTOM_LEFT, Handle.BOTTOM_RIGHT):
                bottom = max(min(bottom + dy, 1.0), top + MIN_CROP_FRACTION)

        self._crop = (left, top, right, bottom)
        if self._ratio:
            self._apply_ratio(anchor=self._active)
        self._emit_crop()
        self.update()

    _CLICK_SLOP = 3
    """이만큼 안 움직였으면 '끌었다'가 아니라 '눌렀다'로 봅니다(픽셀)."""

    def _maybe_emit_click(self, event) -> None:
        origin = getattr(self, "_press_pos", None)
        self._press_pos = None
        if origin is None:
            return
        moved = event.position().toPoint() - origin
        if abs(moved.x()) > self._CLICK_SLOP or abs(moved.y()) > self._CLICK_SLOP:
            return
        base = self._image_rect()
        if base.isEmpty():
            return
        x = (origin.x() - base.left()) / base.width()
        y = (origin.y() - base.top()) / base.height()
        if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
            self.clicked.emit(x, y)

    def mouseReleaseEvent(self, event) -> None:
        if getattr(self, "_brushing", False):
            self._brushing = False
            return
        if self._shape_active is not ShapeHandle.NONE:
            self._cancel_shape_drag()
            self.update()
            # 손을 뗀 뒤에만 알립니다. 끄는 동안 알리면 무거운 재렌더가
            # 예약과 취소를 반복합니다(브러시·팬과 같은 이유).
            self.shape_finished.emit()
            return
        if self._panning:
            self._panning = False
            self._pan_origin = None
            self.setCursor(Qt.OpenHandCursor if self._zoom > 1.0 else Qt.ArrowCursor)
            # 손을 뗀 뒤에 알립니다. 끄는 동안 매 픽셀마다 알리면 무거운
            # 재생성이 계속 예약됐다 취소되기를 반복합니다.
            self.pan_finished.emit()
            self._maybe_emit_click(event)
            return
        if not self._crop_mode:
            self._maybe_emit_click(event)
        if self._active is not Handle.NONE:
            self._active = Handle.NONE
            self._drag_origin = None
            self.crop_finished.emit()

    def mouseDoubleClickEvent(self, event) -> None:
        """크롭 모드면 크롭 초기화, 아니면 줌 초기화."""
        if not self._crop_mode:
            self.reset_view()
            self.zoom_changed.emit(self._zoom)
            return
        self._crop = (0.0, 0.0, 1.0, 1.0)
        self._emit_crop()
        self.crop_finished.emit()
        self.update()

    def _apply_ratio(self, anchor: Handle) -> None:
        """고정 비율에 맞춰 크롭을 조정합니다.

        이미지의 실제 종횡비를 고려해야 합니다 — 정규화 좌표에서 1:1은
        원본이 3:2면 화면상 1:1이 아닙니다.
        """
        base = self._image_rect()
        if base.isEmpty() or not self._ratio:
            return

        left, top, right, bottom = self._crop
        image_ratio = base.width() / base.height()
        # 정규화 공간에서의 목표 비 = 원하는 비 / 이미지 비
        target = self._ratio / image_ratio

        width = right - left
        height = bottom - top
        if height <= 0:
            return

        if width / height > target:
            width = height * target
        else:
            height = width / target

        # 잡고 있는 쪽을 고정점으로 삼습니다
        if anchor in (Handle.TOP_LEFT, Handle.LEFT, Handle.TOP):
            left, top = right - width, bottom - height
        elif anchor in (Handle.TOP_RIGHT, Handle.RIGHT):
            right, top = left + width, bottom - height
        elif anchor in (Handle.BOTTOM_LEFT,):
            left, bottom = right - width, top + height
        else:
            right, bottom = left + width, top + height

        # 경계를 벗어나면 안쪽으로 밉니다
        if left < 0:
            right, left = right - left, 0.0
        if top < 0:
            bottom, top = bottom - top, 0.0
        if right > 1:
            left, right = left - (right - 1), 1.0
        if bottom > 1:
            top, bottom = top - (bottom - 1), 1.0

        self._crop = (
            max(0.0, left), max(0.0, top), min(1.0, right), min(1.0, bottom)
        )

    def _emit_crop(self) -> None:
        self.crop_changed.emit(*self._crop)

    # ------------------------------------------------------------ 그리기

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(20, 20, 22))

        if self._pixmap is None or self._pixmap.isNull():
            painter.setPen(QColor(150, 150, 155))
            painter.drawText(self.rect(), Qt.AlignCenter, self._message or "…")
            return

        target = self._image_rect()
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawPixmap(target, self._pixmap)

        if self._crop_mode:
            self._paint_crop(painter, target)
        elif self._shape_kind:
            self._paint_shape(painter, target)

        if getattr(self, "_brush_mode", False):
            self._paint_brush_cursor(painter, target)

        if self._busy:
            self._paint_busy(painter)

    def _paint_brush_cursor(self, painter: QPainter, target: QRect) -> None:
        """붓 크기·위치를 원으로 보여 줍니다.

        커서만 있으면 얼마나 굵게 칠할지 알 수 없어, 칠해 보고 되돌리기를
        반복하게 됩니다. 실제 칠해질 반경을 그대로 그립니다.
        """
        position = getattr(self, "_brush_pos", None)
        if position is None or target.isEmpty():
            return

        ratio = getattr(self, "_brush_radius_ratio", 0.05)
        radius = max(2, int(round(ratio * min(target.width(), target.height()))))
        erasing = getattr(self, "_brush_erasing", False)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(Qt.NoBrush)
        # 밝은 배경에서도 보이도록 어두운 테두리를 먼저 깔고 그 위에 밝은 선
        painter.setPen(QPen(QColor(0, 0, 0, 160), 3))
        painter.drawEllipse(position, radius, radius)
        painter.setPen(QPen(QColor(255, 140, 140) if erasing else QColor(120, 210, 255), 1.5))
        painter.drawEllipse(position, radius, radius)
        # 중심점 — 어디를 찍는지 정확히 보이게
        painter.setPen(QPen(QColor(255, 255, 255, 200), 1))
        painter.drawLine(position.x() - 4, position.y(), position.x() + 4, position.y())
        painter.drawLine(position.x(), position.y() - 4, position.x(), position.y() + 4)
        painter.restore()

    _SHAPE_DARK = QColor(0, 0, 0, 170)
    _SHAPE_LINE = QColor(120, 210, 255)
    """어두운 밑선을 깔고 그 위에 밝은 선. 밝은 하늘 위에 밝은 선만 그리면
    아무것도 안 보입니다(브러시 원과 같은 이유)."""

    def _paint_shape(self, painter: QPainter, base: QRect) -> None:
        """방사형 윤곽 / 선형 방향과 조작점을 그립니다."""
        if base.isEmpty():
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(Qt.NoBrush)

        if self._shape_kind == "radial":
            self._paint_radial(painter, base)
        else:
            self._paint_linear(painter, base)

        for handle, (x, y) in self._shape_handles():
            centre = QPointF(x, y)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(self._SHAPE_DARK, 3))
            painter.drawEllipse(centre, SHAPE_HANDLE_RADIUS, SHAPE_HANDLE_RADIUS)
            painter.setPen(QPen(QColor(255, 255, 255, 235), 1.4))
            painter.setBrush(
                self._SHAPE_LINE if handle is self._shape_active
                else QColor(28, 28, 32, 210)
            )
            painter.drawEllipse(centre, SHAPE_HANDLE_RADIUS, SHAPE_HANDLE_RADIUS)
        painter.restore()

    _ELLIPSE_STEPS = 72
    """타원을 몇 조각으로 쪼개 그릴지. 6000px 폭에서도 각이 보이지 않는 수준."""

    def _paint_radial(self, painter: QPainter, base: QRect) -> None:
        centre, u, v = self._radial_frame(base)
        outline = QPolygonF([
            QPointF(centre[0] + u[0] * math.cos(t) + v[0] * math.sin(t),
                    centre[1] + u[1] * math.cos(t) + v[1] * math.sin(t))
            for t in (step * math.tau / self._ELLIPSE_STEPS
                      for step in range(self._ELLIPSE_STEPS + 1))
        ])
        # 회전 조작점까지 잇는 짧은 막대. 안 그리면 점 하나가 왜 타원 밖에
        # 떠 있는지 알 수 없습니다.
        length = math.hypot(*u) or 1.0
        reach = 1.0 + ROTATE_HANDLE_GAP / length
        stem = (QPointF(centre[0] + u[0], centre[1] + u[1]),
                QPointF(centre[0] + u[0] * reach, centre[1] + u[1] * reach))

        for pen in (QPen(self._SHAPE_DARK, 3), QPen(self._SHAPE_LINE, 1.5)):
            painter.setPen(pen)
            painter.drawPolyline(outline)
            painter.drawLine(stem[0], stem[1])

    def _paint_linear(self, painter: QPainter, base: QRect) -> None:
        start, end = self._linear_points(base)
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy) or 1.0
        # 진행 방향에 수직인 띠. 그라디언트는 이 선과 나란히 번지므로,
        # 띠를 그려 줘야 어느 쪽이 0%이고 어느 쪽이 100%인지 보입니다.
        nx, ny = -dy / length, dx / length
        bar = max(24.0, min(base.width(), base.height()) * 0.22)

        for pen in (QPen(self._SHAPE_DARK, 3), QPen(self._SHAPE_LINE, 1.5)):
            painter.setPen(pen)
            painter.drawLine(QPointF(*start), QPointF(*end))
            for point in (start, end):
                painter.drawLine(
                    QPointF(point[0] - nx * bar, point[1] - ny * bar),
                    QPointF(point[0] + nx * bar, point[1] + ny * bar),
                )

    def leaveEvent(self, event) -> None:
        """커서가 나가면 미리보기 원도 지웁니다."""
        if getattr(self, "_brush_pos", None) is not None:
            self._brush_pos = None
            self.update()
        super().leaveEvent(event)

    def _paint_crop(self, painter: QPainter, base: QRect) -> None:
        crop = self._crop_rect()

        # 잘려 나갈 영역을 어둡게 — 결과가 어떻게 될지 바로 보입니다
        shade = QColor(0, 0, 0, 130)
        painter.setPen(Qt.NoPen)
        painter.setBrush(shade)
        painter.drawRect(QRect(base.left(), base.top(), base.width(), crop.top() - base.top()))
        painter.drawRect(QRect(base.left(), crop.bottom(), base.width(), base.bottom() - crop.bottom()))
        painter.drawRect(QRect(base.left(), crop.top(), crop.left() - base.left(), crop.height()))
        painter.drawRect(QRect(crop.right(), crop.top(), base.right() - crop.right(), crop.height()))

        # 3분할 가이드
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 70), 1))
        for i in (1, 2):
            x = crop.left() + crop.width() * i // 3
            y = crop.top() + crop.height() * i // 3
            painter.drawLine(x, crop.top(), x, crop.bottom())
            painter.drawLine(crop.left(), y, crop.right(), y)

        painter.setPen(QPen(QColor(255, 255, 255, 220), 2))
        painter.drawRect(crop)

        # 모서리 핸들
        painter.setBrush(QColor(255, 255, 255, 230))
        painter.setPen(Qt.NoPen)
        for x, y in (
            (crop.left(), crop.top()), (crop.right(), crop.top()),
            (crop.left(), crop.bottom()), (crop.right(), crop.bottom()),
        ):
            painter.drawRect(QRect(x - 4, y - 4, 8, 8))

    def _paint_busy(self, painter: QPainter) -> None:
        """'보정 중' 배지. 화면을 가리지 않게 구석에 작게."""
        text = tr("Applying edit…")
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 22
        box = QRect(self.rect().left() + 10, self.rect().top() + 10, width, 26)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 190))
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(QColor(255, 200, 90))
        painter.drawText(box, Qt.AlignCenter, text)
