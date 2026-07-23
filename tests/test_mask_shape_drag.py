"""방사형·선형 마스크를 이미지 위에서 직접 끄는 조작.

이 두 종류만 오랫동안 기본 위치에 박혀 있었습니다. 파라미터는 전부 정규화
좌표인데 그 값을 만질 UI가 없어서, 스포트라이트는 언제나 화면 정중앙이었습니다.

여기서 잠그는 것은 두 가지입니다.

1. **줌·팬 상태에서의 좌표 변환.** 확대하면 `_image_rect()`가 위젯보다 커지고
   왼쪽 위가 음수가 됩니다. 위젯 폭으로 나누는 실수를 하면 등배에서는 멀쩡한데
   확대하는 순간 핸들이 커서에서 떨어져 나갑니다.
2. **정규화 좌표 왕복.** 화면에 찍은 조작점을 다시 0~1로 되돌렸을 때 원래
   값이 나와야 합니다. 해상도 독립이 이 코드베이스의 전제입니다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from arw_selector.core.develop import DevelopSettings  # noqa: E402
from arw_selector.core.develop.settings import (  # noqa: E402
    LocalAdjustments,
    Mask,
    MaskType,
)
from arw_selector.gui.image_view import ImageView, ShapeHandle  # noqa: E402
from conftest import destroy_all_widgets  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from arw_selector.gui import theme

    instance = QApplication.instance() or QApplication([])
    theme.apply_app_theme(instance)
    yield instance
    destroy_all_widgets(instance)
    instance.processEvents()

    from arw_selector.gui.loupe import wait_for_detached_renders

    wait_for_detached_renders()


class _Event:
    """ImageView가 실제로 쓰는 것(position/button)만 흉내 냅니다."""

    def __init__(self, x: int, y: int):
        self._point = QPoint(int(x), int(y))

    def position(self):
        return self

    def toPoint(self) -> QPoint:
        return self._point

    def button(self):
        return Qt.LeftButton


@pytest.fixture
def view(app):
    widget = ImageView()
    widget.resize(400, 300)
    widget.set_pixmap(QPixmap(400, 300))
    yield widget
    widget.close()


def _drag(view: ImageView, start: tuple[float, float],
          delta: tuple[float, float]) -> None:
    """조작점을 잡아 끌고 놓습니다."""
    view.mousePressEvent(_Event(*start))
    view.mouseMoveEvent(_Event(start[0] + delta[0], start[1] + delta[1]))
    view.mouseReleaseEvent(_Event(start[0] + delta[0], start[1] + delta[1]))


def _widget_point(view: ImageView, nx: float, ny: float) -> tuple[float, float]:
    """정규화 좌표를 위젯 좌표로 — 테스트 쪽에서 독립적으로 다시 계산합니다."""
    base = view._image_rect()
    return (base.left() + nx * base.width(), base.top() + ny * base.height())


def _handle_point(view: ImageView, handle: ShapeHandle) -> tuple[float, float]:
    for kind, point in view._shape_handles():
        if kind is handle:
            return point
    raise AssertionError(f"{handle} 조작점이 없습니다")


# ------------------------------------------------------------ 방사형


class TestRadialDrag:
    def test_center_drag_moves_normalized_center(self, view):
        view.set_shape("radial", {"cx": 0.5, "cy": 0.5, "rx": 0.25, "ry": 0.25})
        base = view._image_rect()

        _drag(view, _widget_point(view, 0.5, 0.5), (40, -30))

        params = view.shape_params()
        assert params["cx"] == pytest.approx(0.5 + 40 / base.width())
        assert params["cy"] == pytest.approx(0.5 - 30 / base.height())

    def test_center_stays_inside_the_frame(self, view):
        """조작점이 이미지 밖으로 나가면 다시 잡을 방법이 없습니다."""
        view.set_shape("radial", {"cx": 0.5, "cy": 0.5, "rx": 0.2, "ry": 0.2})

        _drag(view, _widget_point(view, 0.5, 0.5), (5000, 5000))

        params = view.shape_params()
        assert params["cx"] == pytest.approx(1.0)
        assert params["cy"] == pytest.approx(1.0)

    def test_radius_handle_changes_only_its_own_axis(self, view):
        view.set_shape("radial", {"cx": 0.5, "cy": 0.5, "rx": 0.2, "ry": 0.3})
        base = view._image_rect()

        _drag(view, _widget_point(view, 0.7, 0.5), (40, 0))

        params = view.shape_params()
        assert params["rx"] == pytest.approx((0.2 * base.width() + 40) / base.width())
        assert params["ry"] == pytest.approx(0.3), "세로 반경까지 따라 움직였습니다"

    def test_radius_never_collapses_to_zero(self, view):
        """0이 되면 조작점 넷이 중심에 겹쳐 다시 키울 수 없습니다."""
        view.set_shape("radial", {"cx": 0.5, "cy": 0.5, "rx": 0.2, "ry": 0.2})

        centre = _widget_point(view, 0.5, 0.5)
        handle = _handle_point(view, ShapeHandle.RADIUS_X)
        _drag(view, handle, (centre[0] - handle[0], 0))  # 중심까지 끌어당깁니다

        assert view.shape_params()["rx"] > 0.0

    def test_rotate_handle_sets_degrees(self, view):
        view.set_shape("radial", {"cx": 0.5, "cy": 0.5, "rx": 0.2, "ry": 0.1})
        rotate = _handle_point(view, ShapeHandle.ROTATE)
        centre = _widget_point(view, 0.5, 0.5)

        # 중심 바로 아래로 끌면 90도 (화면 y축은 아래로 자랍니다)
        _drag(view, rotate, (centre[0] - rotate[0], centre[1] + 100 - rotate[1]))

        assert view.shape_params()["rotation"] == pytest.approx(90.0)

    def test_rotated_axis_is_measured_along_the_shape(self, view):
        """회전한 뒤에는 가로 반경도 회전한 축을 따라 재야 합니다.

        화면 x축으로 재면 90도 돌린 타원에서 가로 반경 조작점이 위아래에
        있는데도 좌우로 끌어야 커지는, 손이 도저히 안 맞는 상태가 됩니다.
        """
        view.set_shape("radial",
                       {"cx": 0.5, "cy": 0.5, "rx": 0.2, "ry": 0.3, "rotation": 90.0})
        base = view._image_rect()

        # 90도에서 가로축은 화면 아래를 향합니다
        handle = _handle_point(view, ShapeHandle.RADIUS_X)
        centre = _widget_point(view, 0.5, 0.5)
        assert handle[1] > centre[1] and handle[0] == pytest.approx(centre[0], abs=1e-6)

        _drag(view, handle, (0, 40))
        assert view.shape_params()["rx"] == pytest.approx(
            (0.2 * base.width() + 40) / base.width()
        )

    def test_size_scale_is_folded_in_both_directions(self, view):
        """범위 %가 걸린 상태에서도 화면의 타원과 저장값이 맞아야 합니다.

        실제 알파는 rx × 범위로 만들어집니다(masks._radial_alpha). 윤곽선이
        범위를 무시하면 화면의 타원과 빨간 영역 표시가 서로 다른 크기로
        그려집니다.
        """
        view.set_shape("radial", {"cx": 0.5, "cy": 0.5, "rx": 0.2, "ry": 0.2},
                       size=0.5)
        base = view._image_rect()

        handle = _handle_point(view, ShapeHandle.RADIUS_X)
        centre = _widget_point(view, 0.5, 0.5)
        assert handle[0] - centre[0] == pytest.approx(0.2 * 0.5 * base.width())

        # 중심에서 120px 되는 자리로 끌면 저장값은 (120/폭) / 0.5
        _drag(view, handle, (centre[0] + 120 - handle[0], 0))
        assert view.shape_params()["rx"] == pytest.approx(
            120 / base.width() / 0.5
        )


# ------------------------------------------------------------ 선형


class TestLinearDrag:
    def test_endpoints_move_independently(self, view):
        view.set_shape("linear", {"x0": 0.5, "y0": 0.0, "x1": 0.5, "y1": 0.4})
        base = view._image_rect()

        _drag(view, _widget_point(view, 0.5, 0.0), (40, 30))

        params = view.shape_params()
        assert params["x0"] == pytest.approx(0.5 + 40 / base.width())
        assert params["y0"] == pytest.approx(30 / base.height())
        assert params["x1"] == pytest.approx(0.5), "끝점까지 끌려왔습니다"
        assert params["y1"] == pytest.approx(0.4)

    def test_end_handle_moves_the_other_side(self, view):
        view.set_shape("linear", {"x0": 0.5, "y0": 0.0, "x1": 0.5, "y1": 0.4})
        base = view._image_rect()

        _drag(view, _widget_point(view, 0.5, 0.4), (0, 60))

        params = view.shape_params()
        assert params["y1"] == pytest.approx(0.4 + 60 / base.height())
        assert params["y0"] == pytest.approx(0.0)

    def test_middle_handle_shifts_both_ends(self, view):
        view.set_shape("linear", {"x0": 0.5, "y0": 0.0, "x1": 0.5, "y1": 0.4})
        base = view._image_rect()

        _drag(view, _widget_point(view, 0.5, 0.2), (40, 30))

        params = view.shape_params()
        shift_x, shift_y = 40 / base.width(), 30 / base.height()
        assert params["x0"] == pytest.approx(0.5 + shift_x)
        assert params["x1"] == pytest.approx(0.5 + shift_x)
        assert params["y0"] == pytest.approx(shift_y)
        assert params["y1"] == pytest.approx(0.4 + shift_y)

    def test_shifting_into_the_edge_keeps_the_direction(self, view):
        """가장자리에서 두 끝을 따로 자르면 옮기기만 했는데 방향이 변합니다."""
        view.set_shape("linear", {"x0": 0.5, "y0": 0.1, "x1": 0.5, "y1": 0.5})

        _drag(view, _widget_point(view, 0.5, 0.3), (0, -5000))

        params = view.shape_params()
        assert params["y0"] == pytest.approx(0.0)
        assert params["y1"] == pytest.approx(0.4), "그라디언트 길이가 변했습니다"


# ------------------------------------------------------------ 줌 / 팬


class TestZoomedCoordinates:
    def test_handles_follow_the_zoomed_image_rect(self, view):
        view.set_shape("radial", {"cx": 0.3, "cy": 0.7, "rx": 0.2, "ry": 0.2})
        view.set_zoom(4.0)
        base = view._image_rect()

        assert base.width() > view.width(), "확대되지 않았습니다"

        centre = _handle_point(view, ShapeHandle.CENTER)
        assert centre[0] == pytest.approx(base.left() + 0.3 * base.width())
        assert centre[1] == pytest.approx(base.top() + 0.7 * base.height())

    def test_same_pixel_drag_moves_less_when_zoomed(self, view):
        """확대하면 같은 픽셀을 끌어도 이미지 안에서는 덜 움직여야 합니다."""
        view.set_shape("radial", {"cx": 0.5, "cy": 0.5, "rx": 0.2, "ry": 0.2})
        _drag(view, _widget_point(view, 0.5, 0.5), (40, 0))
        plain = view.shape_params()["cx"]

        view.set_shape("radial", {"cx": 0.5, "cy": 0.5, "rx": 0.2, "ry": 0.2})
        view.set_zoom(4.0)
        base = view._image_rect()
        _drag(view, _handle_point(view, ShapeHandle.CENTER), (40, 0))
        zoomed = view.shape_params()["cx"]

        assert plain == pytest.approx(0.5 + 40 / 400)
        assert zoomed == pytest.approx(0.5 + 40 / base.width())
        assert zoomed < plain

    def test_normalized_round_trip_survives_zoom_and_pan(self, view):
        """정규화 → 화면 → 정규화가 제자리로 돌아와야 합니다."""
        original = {"cx": 0.32, "cy": 0.71, "rx": 0.18, "ry": 0.27,
                    "rotation": 0.0}
        view.set_shape("radial", dict(original))
        view.set_zoom(3.0)
        view._offset += QPoint(37, -21)  # 팬까지 섞어 봅니다
        base = view._image_rect()

        centre = _handle_point(view, ShapeHandle.CENTER)
        radius_x = _handle_point(view, ShapeHandle.RADIUS_X)
        radius_y = _handle_point(view, ShapeHandle.RADIUS_Y)

        assert (centre[0] - base.left()) / base.width() == pytest.approx(original["cx"])
        assert (centre[1] - base.top()) / base.height() == pytest.approx(original["cy"])
        assert (radius_x[0] - centre[0]) / base.width() == pytest.approx(original["rx"])
        assert (radius_y[1] - centre[1]) / base.height() == pytest.approx(original["ry"])

    def test_linear_round_trip_survives_zoom(self, view):
        original = {"x0": 0.21, "y0": 0.12, "x1": 0.83, "y1": 0.64}
        view.set_shape("linear", dict(original))
        view.set_zoom(2.5)
        base = view._image_rect()

        start = _handle_point(view, ShapeHandle.START)
        end = _handle_point(view, ShapeHandle.END)

        assert (start[0] - base.left()) / base.width() == pytest.approx(original["x0"])
        assert (start[1] - base.top()) / base.height() == pytest.approx(original["y0"])
        assert (end[0] - base.left()) / base.width() == pytest.approx(original["x1"])
        assert (end[1] - base.top()) / base.height() == pytest.approx(original["y1"])


# ------------------------------------------------------------ 다른 모드와의 충돌


class TestModePrecedence:
    def test_crop_mode_wins(self, view):
        """크롭과 도형은 같은 자리를 놓고 다툽니다. 크롭이 이깁니다."""
        view.set_shape("radial", {"cx": 0.5, "cy": 0.5, "rx": 0.25, "ry": 0.25})
        view.set_crop_mode(True)

        before = view.shape_params()
        _drag(view, _widget_point(view, 0.5, 0.5), (40, 40))

        assert view._shape_active is ShapeHandle.NONE
        assert view.shape_params() == before

    def test_brush_mode_wins(self, view):
        view.set_shape("radial", {"cx": 0.5, "cy": 0.5, "rx": 0.25, "ry": 0.25})
        view.set_brush_mode(True)

        painted: list[tuple[float, float]] = []
        view.brush_painted.connect(lambda x, y: painted.append((x, y)))
        before = view.shape_params()
        _drag(view, _widget_point(view, 0.5, 0.5), (40, 40))

        assert painted, "브러시가 칠해지지 않았습니다"
        assert view.shape_params() == before

    def test_grabbing_a_handle_does_not_pan(self, view):
        """확대 상태에서 조작점을 잡았는데 화면이 따라가면 못 씁니다."""
        view.set_shape("radial", {"cx": 0.5, "cy": 0.5, "rx": 0.2, "ry": 0.2})
        view.set_zoom(4.0)
        offset = QPoint(view._offset)

        _drag(view, _handle_point(view, ShapeHandle.CENTER), (40, 40))

        assert view._offset == offset, "화면이 같이 밀렸습니다"
        assert not view._panning

    def test_empty_space_still_pans_when_zoomed(self, view):
        """조작점에서 먼 곳을 누르면 평소처럼 화면이 움직여야 합니다."""
        view.set_shape("radial", {"cx": 0.5, "cy": 0.5, "rx": 0.05, "ry": 0.05})
        view.set_zoom(4.0)

        view.mousePressEvent(_Event(10, 10))
        assert view._panning
        assert view._shape_active is ShapeHandle.NONE
        view.mouseReleaseEvent(_Event(20, 20))

    def test_no_shape_means_no_handles(self, view):
        view.set_shape(None)
        assert view._shape_handles() == []
        view.mousePressEvent(_Event(200, 150))
        assert view._shape_active is ShapeHandle.NONE

    def test_unknown_kind_is_ignored(self, view):
        """얼굴·브러시 마스크를 골라도 조작점이 뜨면 안 됩니다."""
        view.set_shape("face", {"index": 0})
        assert view._shape_handles() == []


# ------------------------------------------------------------ 신호 시점


class TestSignalTiming:
    def test_heavy_signal_waits_for_release(self, view):
        """끄는 동안 재렌더를 부르면 조작이 따라오지 못합니다."""
        view.set_shape("radial", {"cx": 0.5, "cy": 0.5, "rx": 0.2, "ry": 0.2})
        moves: list[dict] = []
        finished: list[int] = []
        view.shape_changed.connect(moves.append)
        view.shape_finished.connect(lambda: finished.append(1))

        centre = _widget_point(view, 0.5, 0.5)
        view.mousePressEvent(_Event(*centre))
        view.mouseMoveEvent(_Event(centre[0] + 10, centre[1]))
        view.mouseMoveEvent(_Event(centre[0] + 20, centre[1]))
        assert len(moves) == 2
        assert finished == []

        view.mouseReleaseEvent(_Event(centre[0] + 20, centre[1]))
        assert finished == [1]

    def test_drag_offset_is_measured_from_the_press(self, view):
        """이동량을 직전 값에 누적하면 도형이 커서보다 두 배씩 밀립니다."""
        view.set_shape("radial", {"cx": 0.5, "cy": 0.5, "rx": 0.2, "ry": 0.2})
        base = view._image_rect()

        centre = _widget_point(view, 0.5, 0.5)
        view.mousePressEvent(_Event(*centre))
        for step in (10, 20, 30):
            view.mouseMoveEvent(_Event(centre[0] + step, centre[1]))
        view.mouseReleaseEvent(_Event(centre[0] + 30, centre[1]))

        assert view.shape_params()["cx"] == pytest.approx(0.5 + 30 / base.width())


# ------------------------------------------------------------ 윤곽선 = 실제 알파


class TestOutlineMatchesTheEngine:
    """화면에 그린 윤곽선이 실제로 만들어지는 알파의 경계와 같아야 합니다.

    같은 정규화 파라미터를 뷰와 엔진이 각자 해석합니다. 한쪽이 반경을 폭
    기준으로, 다른 쪽이 짧은 변 기준으로 잡는 식의 어긋남은 정사각형
    이미지에서는 드러나지 않습니다. 400×300으로 재서 잡아 둡니다.
    """

    _PARAMS = {"cx": 0.4, "cy": 0.6, "rx": 0.2, "ry": 0.15, "rotation": 35.0}
    _SIZE = 0.8

    def _alpha_at(self, alpha, nx: float, ny: float) -> float:
        height, width = alpha.shape[:2]
        x = min(max(int(round(nx * width)), 0), width - 1)
        y = min(max(int(round(ny * height)), 0), height - 1)
        return float(alpha[y, x])

    def test_radius_handle_sits_on_the_alpha_boundary(self, view):
        from arw_selector.core.develop.masks import _radial_alpha

        view.set_shape("radial", dict(self._PARAMS), size=self._SIZE)
        base = view._image_rect()
        alpha = _radial_alpha(self._PARAMS, base.height(), base.width(), self._SIZE)

        handle = _handle_point(view, ShapeHandle.RADIUS_X)
        nx = (handle[0] - base.left()) / base.width()
        ny = (handle[1] - base.top()) / base.height()

        assert self._alpha_at(alpha, self._PARAMS["cx"], self._PARAMS["cy"]) == \
            pytest.approx(1.0), "중심이 꽉 차 있지 않습니다"
        assert self._alpha_at(alpha, nx, ny) < 0.05, (
            "반경 조작점이 알파 경계 위에 있지 않습니다"
        )
        # 중심과 경계의 중간은 절반쯤 — 축이 어긋나면 여기서 크게 벗어납니다
        half_x = self._PARAMS["cx"] + (nx - self._PARAMS["cx"]) / 2.0
        half_y = self._PARAMS["cy"] + (ny - self._PARAMS["cy"]) / 2.0
        assert self._alpha_at(alpha, half_x, half_y) == pytest.approx(0.5, abs=0.05)

    def test_linear_handles_sit_on_0_and_100_percent(self, view):
        from arw_selector.core.develop.masks import _linear_alpha

        params = {"x0": 0.2, "y0": 0.15, "x1": 0.75, "y1": 0.7}
        view.set_shape("linear", dict(params))
        base = view._image_rect()
        alpha = _linear_alpha(params, base.height(), base.width())

        start = _handle_point(view, ShapeHandle.START)
        end = _handle_point(view, ShapeHandle.END)
        middle = _handle_point(view, ShapeHandle.CENTER)

        def normalized(point):
            return ((point[0] - base.left()) / base.width(),
                    (point[1] - base.top()) / base.height())

        assert self._alpha_at(alpha, *normalized(start)) == pytest.approx(0.0, abs=0.02)
        assert self._alpha_at(alpha, *normalized(end)) == pytest.approx(1.0, abs=0.02)
        assert self._alpha_at(alpha, *normalized(middle)) == pytest.approx(0.5, abs=0.03)


# ------------------------------------------------------------ 실제로 그려지는가


def _colours(widget) -> set[str]:
    image = widget.grab().toImage()
    return {
        image.pixelColor(x, y).name()
        for y in range(0, image.height(), 2)
        for x in range(0, image.width(), 2)
    }


class TestShapeIsVisible:
    """좌표가 맞아도 화면에 안 보이면 없는 기능입니다.

    이 코드베이스에서 곡선 편집기가 그랬습니다 — 계산은 전부 정상인데
    남은 브러시가 플롯 전체를 덮어 아무것도 보이지 않았습니다.
    """

    def test_radial_outline_is_painted(self, view):
        plain = _colours(view)
        view.set_shape("radial", {"cx": 0.5, "cy": 0.5, "rx": 0.3, "ry": 0.25})

        assert _colours(view) - plain, "타원이 그려지지 않았습니다"

    def test_linear_guide_is_painted(self, view):
        plain = _colours(view)
        view.set_shape("linear", {"x0": 0.5, "y0": 0.1, "x1": 0.5, "y1": 0.6})

        assert _colours(view) - plain, "선형 안내선이 그려지지 않았습니다"

    def test_crop_mode_hides_the_shape(self, view):
        """크롭 중에 타원까지 겹쳐 나오면 어느 선을 잡아야 할지 모릅니다."""
        view.set_shape("radial", {"cx": 0.5, "cy": 0.5, "rx": 0.3, "ry": 0.25})
        with_shape = _colours(view)

        view.set_crop_mode(True)
        view.set_crop(0.0, 0.0, 1.0, 1.0)

        assert _colours(view) != with_shape


# ------------------------------------------------------------ 패널 연동


def _radial(**params) -> Mask:
    base = {"cx": 0.5, "cy": 0.5, "rx": 0.25, "ry": 0.25}
    base.update(params)
    return Mask(kind=MaskType.RADIAL, adjust=LocalAdjustments(exposure=0.5),
                params=base, label="원형")


class TestPanelBinding:
    @pytest.fixture
    def panel(self, app):
        from arw_selector.gui.develop_panel import DevelopPanel

        widget = DevelopPanel()
        yield widget
        widget.close()

    def test_shape_mask_only_for_draggable_kinds(self, panel):
        panel.set_settings(DevelopSettings(masks=(_radial(),)))
        assert panel.shape_mask() is not None

        panel.set_settings(DevelopSettings(masks=(
            Mask(kind=MaskType.BRUSH, adjust=LocalAdjustments(exposure=0.3)),
        )))
        assert panel.shape_mask() is None, "붓 마스크에 조작점이 붙었습니다"

    def test_no_selection_means_no_shape(self, panel):
        panel.set_settings(DevelopSettings())
        assert panel.shape_mask() is None

    def test_silent_update_does_not_trigger_render(self, panel):
        """끄는 동안 settings_changed가 나가면 무거운 재렌더가 반복됩니다."""
        panel.set_settings(DevelopSettings(masks=(_radial(),)))
        emitted: list[int] = []
        panel.settings_changed.connect(lambda: emitted.append(1))

        panel.set_mask_params({"cx": 0.8, "cy": 0.2, "rx": 0.1, "ry": 0.1},
                              silent=True)

        assert emitted == []
        assert panel._masks[0].params["cx"] == pytest.approx(0.8)

    def test_loud_update_reaches_settings(self, panel):
        panel.set_settings(DevelopSettings(masks=(_radial(),)))
        emitted: list[int] = []
        panel.settings_changed.connect(lambda: emitted.append(1))

        panel.set_mask_params({"cx": 0.8, "cy": 0.2, "rx": 0.1, "ry": 0.1})

        assert emitted == [1]
        assert panel.settings().masks[0].params["cx"] == pytest.approx(0.8)

    def test_selection_announces_the_shape(self, panel):
        """마스크를 고르면 화면 쪽에 알려야 조작점이 따라 옮겨집니다."""
        panel.set_settings(DevelopSettings(masks=(_radial(), _radial(cx=0.1))))
        seen: list[int] = []
        panel.mask_shape_changed.connect(lambda: seen.append(1))

        panel.mask_list.setCurrentRow(1)

        assert seen, "선택이 바뀌었는데 알림이 없습니다"
        assert panel.shape_mask().params["cx"] == pytest.approx(0.1)

    def test_size_change_announces_the_shape(self, panel):
        """범위 %는 방사형 윤곽선의 크기 그 자체입니다."""
        panel.set_settings(DevelopSettings(masks=(_radial(),)))
        seen: list[int] = []
        panel.mask_shape_changed.connect(lambda: seen.append(1))

        panel.mask_size.set_value(60.0)

        assert seen


# ------------------------------------------------------------ 루페 연동


class TestLoupeWiring:
    @pytest.fixture
    def dialog(self, app):
        from arw_selector.core.types import ImageRecord
        from arw_selector.gui.loupe import LoupeDialog

        widget = LoupeDialog(ImageRecord(path=Path("558A8911.CR3")), fast=True)
        widget.preview.resize(400, 300)
        widget.preview.set_pixmap(QPixmap(400, 300))
        yield widget
        widget.close()

    def test_selecting_a_radial_mask_shows_handles(self, dialog):
        dialog.panel.set_settings(DevelopSettings(masks=(_radial(cx=0.3),)))

        assert dialog.preview._shape_kind == "radial"
        assert dialog.preview.shape_params()["cx"] == pytest.approx(0.3)

    def test_size_percent_reaches_the_outline(self, dialog):
        """범위 60%면 화면의 타원도 60%여야 합니다."""
        mask = Mask(kind=MaskType.RADIAL, adjust=LocalAdjustments(exposure=0.5),
                    size=60, params={"cx": 0.5, "cy": 0.5, "rx": 0.2, "ry": 0.2})
        dialog.panel.set_settings(DevelopSettings(masks=(mask,)))

        assert dialog.preview._shape_size == pytest.approx(0.6)

    def test_linear_size_is_ignored(self, dialog):
        """선형에는 줄일 도형이 없습니다. 범위를 곱하면 결과와 어긋납니다."""
        mask = Mask(kind=MaskType.LINEAR, adjust=LocalAdjustments(exposure=0.5),
                    size=40, params={"x0": 0.5, "y0": 0.0, "x1": 0.5, "y1": 0.4})
        dialog.panel.set_settings(DevelopSettings(masks=(mask,)))

        assert dialog.preview._shape_size == pytest.approx(1.0)

    def test_dragging_writes_back_to_the_mask(self, dialog):
        dialog.panel.set_settings(DevelopSettings(masks=(_radial(),)))
        base = dialog.preview._image_rect()

        _drag(dialog.preview, _widget_point(dialog.preview, 0.5, 0.5), (40, 0))

        params = dialog.panel._masks[0].params
        assert params["cx"] == pytest.approx(0.5 + 40 / base.width())
        assert dialog._dirty, "저장 대상으로 표시되지 않았습니다"

    def test_brush_mask_clears_the_handles(self, dialog):
        dialog.panel.set_settings(DevelopSettings(masks=(_radial(),)))
        assert dialog.preview._shape_kind == "radial"

        dialog.panel.set_settings(DevelopSettings(masks=(
            Mask(kind=MaskType.BRUSH, adjust=LocalAdjustments(exposure=0.3)),
        )))
        assert dialog.preview._shape_kind is None
