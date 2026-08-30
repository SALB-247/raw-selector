"""Preview image view.

Matching the four crop edges with sliders is effectively impossible. It has
to be dragged directly on the image, and this widget is what does that.
Radial and linear masks are dragged here for the same reason - you cannot
place a circle with four numbers.

Crop coordinates are always passed around as 0~1 normalised values, because
the meaning has to hold whatever the screen size or the source resolution.
The same goes for the mask shapes.
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
"""Handle radius (pixels). Too small and it is hard to grab."""

MIN_CROP_FRACTION = 0.05
"""Stops the crop shrinking below this."""

SHAPE_HANDLE_RADIUS = 5.0
"""Radius the shape handles are drawn at (pixels). Only as big as it can be
without hiding the photo."""

SHAPE_GRAB = 12
"""The distance at which a shape handle counts as 'grabbed' (pixels).

More generous than the crop handles. A crop handle runs the length of an
edge so a rough press catches it, but a shape handle is a single point and
the same margin keeps missing.
"""

MIN_SHAPE_RADIUS = 0.01
"""Lower bound of the radial radius (normalised).

Down at 0 the four handles pile up on the centre point and there is no way
left to grow it again.
"""

ROTATE_HANDLE_GAP = 22
"""How far outside the ellipse the rotate handle floats (pixels). Just enough
not to overlap the radius handles."""


def _unit(value: float) -> float:
    """Clips so it does not go outside 0~1.

    A handle that leaves the image disappears from the screen and there is no
    way to bring it back.
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
    """The point currently grabbed on a radial or linear mask."""

    NONE = auto()
    CENTER = auto()    # radial: move the centre / linear: move both ends
    RADIUS_X = auto()  # radial horizontal radius (that axis, once rotated)
    RADIUS_Y = auto()
    ROTATE = auto()
    START = auto()     # the linear 0% end
    END = auto()       # the linear 100% end


SHAPE_KINDS = ("radial", "linear")
"""The mask kinds that can be dragged directly on the image (the same strings
as the MaskType values)."""


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
    """Draws the image keeping its aspect ratio, and in crop mode lets it be
    manipulated directly."""

    crop_changed = Signal(float, float, float, float)  # left, top, right, bottom
    crop_finished = Signal()
    zoom_changed = Signal(float)
    pan_finished = Signal()
    """Emitted when the screen has been dragged and released.

    While zoomed in, only what is visible is rendered at high quality, so
    after a move the newly exposed part has to be rendered again."""
    color_picked = Signal(float, float)  # relative coordinates in image (0~1)
    brush_painted = Signal(float, float)  # painted point (0~1 relative coords)
    clicked = Signal(float, float)
    """A point pressed without dragging (0~1 relative coordinates).

    While zoomed in, pressing starts a pan straight away, so this is sent
    **only when released without moving**. That way trying to move the screen
    never picks something by accident.
    """
    shape_changed = Signal(dict)
    """Mid-drag on a radial or linear mask - the full set of changed
    normalised parameters.

    This is the only thing emitted during the drag. Just take the values; do
    not redraw - the outline is drawn by this widget itself.
    """
    shape_finished = Signal()
    """Emitted when the shape is released. The heavy re-render runs here."""

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

        # Zoom / pan. zoom 1.0 is "fit to window", and offset is the pixel
        # shift relative to that state.
        self._zoom = 1.0
        self._offset = QPoint(0, 0)
        self._panning = False
        self._press_pos: QPoint | None = None
        self._pan_origin: QPoint | None = None
        self._pan_start_offset = QPoint(0, 0)

        # Radial and linear mask manipulation. With kind None it neither draws
        # nor accepts anything.
        self._shape_kind: str | None = None
        self._shape_params: dict = {}
        self._shape_size = 1.0
        self._shape_active = ShapeHandle.NONE
        self._shape_origin: QPoint | None = None
        self._shape_start: dict = {}
        """The parameters as of the start of the drag. The movement has to be
        added to the **start value** or the cursor and the shape drift apart
        (accumulating onto the previous value makes it slide)."""

    # ---------------------------------------------------------------- state

    def set_pixmap(self, pixmap: QPixmap | None) -> None:
        self._pixmap = pixmap
        self.update()

    def set_message(self, message: str) -> None:
        self._message = message
        self.update()

    def set_busy(self, busy: bool) -> None:
        """The 'adjusting' indicator. The render takes around 200ms, so without
        saying so it reads as frozen."""
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
        # Crop and shapes grab different things in the same place. Crop wins,
        # and the shape neither draws nor accepts anything.
        self._cancel_shape_drag()
        self.setCursor(Qt.CrossCursor if enabled else Qt.ArrowCursor)
        self.update()

    def set_ratio(self, ratio: float | None) -> None:
        """Locks the width/height ratio. None means free.

        Picking a new ratio lays out the largest crop that fits centred on the
        image. When the user changes the ratio they usually mean "lay the whole
        thing out again at this ratio", not "keep one corner of the current
        crop".
        """
        self._ratio = ratio
        if ratio:
            self._center_ratio_crop(ratio)
            self._emit_crop()
        self.update()

    def _center_ratio_crop(self, ratio: float) -> None:
        """Builds the largest crop at the given ratio that fits centred on the
        image."""
        base = self._image_rect()
        if base.isEmpty():
            return

        image_ratio = base.width() / base.height()
        # Target ratio in normalised space = wanted ratio / image ratio
        target = ratio / image_ratio

        if target >= 1.0:
            width, height = 1.0, 1.0 / target
        else:
            width, height = target, 1.0

        left = (1.0 - width) / 2.0
        top = (1.0 - height) / 2.0
        self._crop = (left, top, left + width, top + height)

    # -------------------------------------------------- coordinate conversion

    def _image_rect(self) -> QRect:
        """The rectangle the image is actually drawn in inside the widget
        (zoom and pan applied)."""
        if self._pixmap is None or self._pixmap.isNull():
            return QRect()

        available = self.rect()
        scaled = self._pixmap.size().scaled(available.size(), Qt.KeepAspectRatio)
        width = max(1, int(scaled.width() * self._zoom))
        height = max(1, int(scaled.height() * self._zoom))

        x = available.left() + (available.width() - width) // 2 + self._offset.x()
        y = available.top() + (available.height() - height) // 2 + self._offset.y()
        return QRect(x, y, width, height)

    # ----------------------------------------------------------- zoom / pan

    def zoom(self) -> float:
        return self._zoom

    def visible_region(self, pad: float = 0.06) -> tuple[float, float, float, float]:
        """Returns the part currently visible as a ratio of the image (0~1).

        When rebuilding at high quality while zoomed in, there is no reason to
        build what is not visible. Measured (R6M3 27MP): at 4x zoom the visible
        area is 6% of the whole, and the adjustment drops from 3.4s to 0.22s.

        pad is the slack. The edges are taken generously so a small nudge does
        not immediately expose empty space.
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
        """Changes the zoom factor.

        Given focus, the offset is adjusted so that point stays put on screen.
        Otherwise what you were looking at is pushed off screen with every
        zoom step.
        """
        new_zoom = float(np.clip(zoom, 1.0, 16.0))
        if abs(new_zoom - self._zoom) < 1e-6:
            return

        anchor = focus or self.rect().center()
        before = self._image_rect()
        if not before.isEmpty():
            # Where the anchor sits within the image, as a ratio
            rx = (anchor.x() - before.left()) / before.width()
            ry = (anchor.y() - before.top()) / before.height()

        self._zoom = new_zoom
        if new_zoom <= 1.0:
            self._offset = QPoint(0, 0)
        elif not before.isEmpty():
            after = self._image_rect()
            # Shift so the anchor lands at the same screen position
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
        """Fills the screen with the region used for focus scoring.

        roi is in the source preview coordinate system, scale is the factor
        against the image being displayed. Whether the eyes are really in focus
        can only be told by zooming in.
        """
        if self._pixmap is None or self._pixmap.isNull():
            return

        x, y, w, h = roi
        if w <= 0 or h <= 0:
            return

        # The share of the displayed image the ROI takes up
        pixmap_width = self._pixmap.width()
        pixmap_height = self._pixmap.height()
        roi_width = max(1.0, w * scale)
        roi_height = max(1.0, h * scale)

        # So the ROI takes 60% of the screen (slack is needed to see the
        # surrounding context)
        target = min(
            pixmap_width / roi_width, pixmap_height / roi_height, 16.0
        ) * 0.6
        self._zoom = float(np.clip(target, 1.0, 16.0))
        self._offset = QPoint(0, 0)

        rect = self._image_rect()
        if rect.isEmpty():
            return

        # So the ROI centre lands at the centre of the screen
        center_ratio_x = (x + w / 2.0) * scale / pixmap_width
        center_ratio_y = (y + h / 2.0) * scale / pixmap_height
        self._offset = QPoint(
            int(self.rect().center().x() - (rect.left() + center_ratio_x * rect.width())),
            int(self.rect().center().y() - (rect.top() + center_ratio_y * rect.height())),
        )
        self._clamp_offset()
        self.update()

    def _clamp_offset(self) -> None:
        """Stops the image being dragged fully off screen."""
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
        """Zoom in/out with the wheel, anchored on the cursor position."""
        if self._pixmap is None or self._pixmap.isNull():
            return
        step = 1.25 if event.angleDelta().y() > 0 else 1 / 1.25
        self.set_zoom(self._zoom * step, event.position().toPoint())
        self.zoom_changed.emit(self._zoom)
        event.accept()

    def _crop_rect(self) -> QRect:
        """The normalised crop in screen coordinates."""
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

    # ------------------------------------- shape masks (radial / linear)

    def set_shape(self, kind: str | None, params: dict | None = None,
                  size: float = 1.0) -> None:
        """Sets the mask shape to drag on the image. kind None clears it.

        size is the 'Range %' factor. The real alpha grows and shrinks by this
        factor (masks._radial_alpha), so the outline has to follow it for what
        you see to be what you get.
        """
        self._shape_kind = kind if kind in SHAPE_KINDS else None
        self._shape_params = dict(params or {})
        self._shape_size = float(size)
        self._cancel_shape_drag()
        self.update()

    def shape_params(self) -> dict:
        """A copy of the normalised parameters of the shape being drawn."""
        return dict(self._shape_params)

    def _cancel_shape_drag(self) -> None:
        self._shape_active = ShapeHandle.NONE
        self._shape_origin = None

    def _shape_scale(self) -> float:
        """The range factor to multiply the radius by. Never goes down to 0.

        Dragging the range slider to 0% makes the factor 0, and computing the
        radius back divides by zero. The lower bound applies to the
        manipulation side only - the outline has to be drawn at the real factor
        or what you see and what you get diverge.
        """
        return max(0.05, self._shape_size)

    def _radial_frame(self, base: QRect, params: dict | None = None):
        """The radial ellipse as (centre, horizontal axis vector, vertical axis
        vector). All in widget pixels.

        `_image_rect()` keeps the aspect ratio and only applies zoom and pan,
        so image pixel -> widget is the **same factor** horizontally and
        vertically. That is why the rotation angle needs no separate
        correction - break that premise (draw stretched, ignoring the ratio)
        and the ellipse goes wrong starting from its angle.
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
        # The horizontal radius is against the width and the vertical radius
        # against the height (the same as _radial_alpha).
        u = (rx * base.width() * ca, rx * base.width() * sa)
        v = (-ry * base.height() * sa, ry * base.height() * ca)
        return centre, u, v

    def _linear_points(self, base: QRect, params: dict | None = None):
        """The linear gradient as (start point, end point). Widget pixels."""
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
        """A list of (handle, widget coordinates).

        The radial radius handles are placed **one on each side of each axis**.
        With only one side there is no way to grab it once the ellipse is half
        off screen.

        The centre goes last in the list. When the radius is very small and all
        the handles pile up on one point, the nearest-one rule ties, and if the
        centre won there the ellipse could never be grown again.
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
        """The nearest handle. On a tie the earlier one in the list wins."""
        best, best_distance = ShapeHandle.NONE, float(SHAPE_GRAB)
        for handle, (x, y) in self._shape_handles():
            distance = math.hypot(point.x() - x, point.y() - y)
            if distance < best_distance:
                best, best_distance = handle, distance
        return best

    def _drag_shape(self, point: QPoint) -> None:
        """Recomputes the normalised parameters to follow the handle being
        dragged."""
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

        # Push both ends together. Clipping them separately means that the
        # moment one touches the edge only the other keeps moving, so a plain
        # move changes the gradient direction.
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

        # Radius and rotation are measured **from the centre to the cursor**
        # rather than by the movement. Even if the grab point is a few pixels
        # off the handle, that error does not accumulate.
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

    # ---------------------------------------------------------------- mouse

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
        """Eyedropper mode. A click reports the colour at that point."""
        self._pick_mode = enabled
        self.setCursor(Qt.CrossCursor if enabled else Qt.ArrowCursor)

    def set_brush_mode(self, enabled: bool) -> None:
        """Brush mode. Keeps reporting the points passed over while
        dragging."""
        self._brush_mode = enabled
        self._brushing = False
        self._brush_pos = None
        self._cancel_shape_drag()
        # The brush size is shown as a circle, so the cursor itself is hidden
        self.setCursor(Qt.BlankCursor if enabled else Qt.ArrowCursor)
        self.update()

    def set_brush_radius(self, ratio: float) -> None:
        """Brush radius (as a ratio of the image's short edge). This is the
        size of the preview circle."""
        self._brush_radius_ratio = max(0.002, float(ratio))
        self.update()

    def set_brush_erasing(self, erasing: bool) -> None:
        """In eraser mode the preview circle is drawn in a different colour."""
        self._brush_erasing = bool(erasing)
        self.update()

    def _emit_brush(self, point) -> bool:
        """If the pointer is inside the image, reports it in relative
        coordinates."""
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

        # Shape mask manipulation comes **after crop and before pan**. In crop
        # mode the two overlap in the same place so crop wins, and once a
        # handle is grabbed the screen must not move along with it, so it is
        # checked before pan.
        if self._shape_kind and not self._crop_mode:
            handle = self._shape_handle_at(event.position().toPoint())
            if handle is not ShapeHandle.NONE:
                self._shape_active = handle
                self._shape_origin = event.position().toPoint()
                self._shape_start = dict(self._shape_params)
                self.update()
                return

        # Outside crop mode a drag is a pan
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
            # The brush size has to show while moving without pressing too, or
            # you cannot tell where and how much you are about to paint
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
            # Signals that this is a grabbable point. Otherwise only the hand
            # cursor shows and it reads as a place to move the screen.
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
            # Moving keeps the size and stays within the bounds
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
    """Moving less than this counts as 'pressed' rather than 'dragged'
    (pixels)."""

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
            # Only reported after release. Reporting during the drag makes the
            # heavy re-render schedule and cancel over and over (the same
            # reason as the brush and the pan).
            self.shape_finished.emit()
            return
        if self._panning:
            self._panning = False
            self._pan_origin = None
            self.setCursor(Qt.OpenHandCursor if self._zoom > 1.0 else Qt.ArrowCursor)
            # Reported after release. Reporting on every pixel during the drag
            # makes the heavy rebuild get scheduled and cancelled over and
            # over.
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
        """In crop mode this resets the crop, otherwise it resets the zoom."""
        if not self._crop_mode:
            self.reset_view()
            self.zoom_changed.emit(self._zoom)
            return
        self._crop = (0.0, 0.0, 1.0, 1.0)
        self._emit_crop()
        self.crop_finished.emit()
        self.update()

    def _apply_ratio(self, anchor: Handle) -> None:
        """Adjusts the crop to the locked ratio.

        The image's real aspect ratio has to be taken into account - 1:1 in
        normalised coordinates is not 1:1 on screen when the source is 3:2.
        """
        base = self._image_rect()
        if base.isEmpty() or not self._ratio:
            return

        left, top, right, bottom = self._crop
        image_ratio = base.width() / base.height()
        # Target ratio in normalised space = wanted ratio / image ratio
        target = self._ratio / image_ratio

        width = right - left
        height = bottom - top
        if height <= 0:
            return

        if width / height > target:
            width = height * target
        else:
            height = width / target

        # The side being held becomes the anchor point
        if anchor in (Handle.TOP_LEFT, Handle.LEFT, Handle.TOP):
            left, top = right - width, bottom - height
        elif anchor in (Handle.TOP_RIGHT, Handle.RIGHT):
            right, top = left + width, bottom - height
        elif anchor in (Handle.BOTTOM_LEFT,):
            left, bottom = right - width, top + height
        else:
            right, bottom = left + width, top + height

        # If it leaves the bounds, push it back inside
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

    # -------------------------------------------------------------- drawing

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
        """Shows the brush size and position as a circle.

        With only a cursor you cannot tell how thickly it will paint, so you
        end up painting and undoing over and over. The radius that will really
        be painted is drawn as-is.
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
        # A dark outline first and a bright line over it, so it stays visible
        # on a bright background
        painter.setPen(QPen(QColor(0, 0, 0, 160), 3))
        painter.drawEllipse(position, radius, radius)
        painter.setPen(QPen(QColor(255, 140, 140) if erasing else QColor(120, 210, 255), 1.5))
        painter.drawEllipse(position, radius, radius)
        # The centre point - so exactly where you are pointing is visible
        painter.setPen(QPen(QColor(255, 255, 255, 200), 1))
        painter.drawLine(position.x() - 4, position.y(), position.x() + 4, position.y())
        painter.drawLine(position.x(), position.y() - 4, position.x(), position.y() + 4)
        painter.restore()

    _SHAPE_DARK = QColor(0, 0, 0, 170)
    _SHAPE_LINE = QColor(120, 210, 255)
    """A dark line underneath and a bright line over it. A bright line alone
    over a bright sky shows nothing at all (the same reason as the brush
    circle)."""

    def _paint_shape(self, painter: QPainter, base: QRect) -> None:
        """Draws the radial outline / linear direction and the handles."""
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
    """How many segments the ellipse is drawn in. Enough that no corners show
    even at a width of 6000px."""

    def _paint_radial(self, painter: QPainter, base: QRect) -> None:
        centre, u, v = self._radial_frame(base)
        outline = QPolygonF([
            QPointF(centre[0] + u[0] * math.cos(t) + v[0] * math.sin(t),
                    centre[1] + u[1] * math.cos(t) + v[1] * math.sin(t))
            for t in (step * math.tau / self._ELLIPSE_STEPS
                      for step in range(self._ELLIPSE_STEPS + 1))
        ])
        # A short stem out to the rotate handle. Without it there is no telling
        # why a lone point is floating outside the ellipse.
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
        # Bars perpendicular to the direction of travel. The gradient spreads
        # parallel to this line, so the bars are what make it visible which end
        # is 0% and which is 100%.
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
        """When the cursor leaves, the preview circle is cleared too."""
        if getattr(self, "_brush_pos", None) is not None:
            self._brush_pos = None
            self.update()
        super().leaveEvent(event)

    def _paint_crop(self, painter: QPainter, base: QRect) -> None:
        crop = self._crop_rect()

        # Darken the area that will be cut away - the result is visible at once
        shade = QColor(0, 0, 0, 130)
        painter.setPen(Qt.NoPen)
        painter.setBrush(shade)
        painter.drawRect(QRect(base.left(), base.top(), base.width(), crop.top() - base.top()))
        painter.drawRect(QRect(base.left(), crop.bottom(), base.width(), base.bottom() - crop.bottom()))
        painter.drawRect(QRect(base.left(), crop.top(), crop.left() - base.left(), crop.height()))
        painter.drawRect(QRect(crop.right(), crop.top(), base.right() - crop.right(), crop.height()))

        # Rule-of-thirds guides
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 70), 1))
        for i in (1, 2):
            x = crop.left() + crop.width() * i // 3
            y = crop.top() + crop.height() * i // 3
            painter.drawLine(x, crop.top(), x, crop.bottom())
            painter.drawLine(crop.left(), y, crop.right(), y)

        painter.setPen(QPen(QColor(255, 255, 255, 220), 2))
        painter.drawRect(crop)

        # Corner handles
        painter.setBrush(QColor(255, 255, 255, 230))
        painter.setPen(Qt.NoPen)
        for x, y in (
            (crop.left(), crop.top()), (crop.right(), crop.top()),
            (crop.left(), crop.bottom()), (crop.right(), crop.bottom()),
        ):
            painter.drawRect(QRect(x - 4, y - 4, 8, 8))

    def _paint_busy(self, painter: QPainter) -> None:
        """The 'adjusting' badge. Small, in a corner, so it does not cover the
        screen."""
        text = tr("Applying edit…")
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 22
        box = QRect(self.rect().left() + 10, self.rect().top() + 10, width, 26)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 190))
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(QColor(255, 200, 90))
        painter.drawText(box, Qt.AlignCenter, text)
