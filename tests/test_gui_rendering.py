"""위젯이 실제로 그려지는지 확인하는 렌더링 테스트.

로직 테스트로는 이런 버그를 못 잡습니다. 곡선 편집기에서 실제로 겪은 일:
좌표 계산도 LUT도 paintEvent 호출도 전부 정상이었는데, 마지막 테두리
drawRect가 핸들에서 남은 브러시로 플롯 영역 전체를 칠해서 화면에는
아무것도 안 보였습니다. 그려진 픽셀을 직접 세는 수밖에 없습니다.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

# 화면 없이 렌더링합니다. QApplication 생성 전에 정해야 합니다.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from arw_selector.core.develop import DevelopSettings  # noqa: E402
from arw_selector.gui.curve_editor import CurveEditor  # noqa: E402
from arw_selector.gui.histogram import HistogramWidget  # noqa: E402


@pytest.fixture(scope="module")
def app():
    """실제 앱과 같은 스타일로 만듭니다.

    테마를 안 걸면 테스트는 OS 기본 스타일(windows11)로 돌고, 실제 앱은
    Fusion으로 돕니다. 위젯 크기가 스타일마다 달라서, 그대로 두면 잘림
    회귀 테스트가 정작 사용자가 보는 화면을 검사하지 않게 됩니다.
    """
    from arw_selector.gui import theme

    instance = QApplication.instance() or QApplication([])
    theme.apply_app_theme(instance)
    yield instance

    # 남아 있는 창을 정리하고 나갑니다. 열린 채로 인터프리터가 끝나면
    # 아직 도는 렌더 스레드가 파괴되어 Qt가 프로세스를 죽입니다
    # (0xc0000409). 테스트는 전부 통과하는데 종료 코드만 실패로 나옵니다.
    for widget in list(instance.topLevelWidgets()):
        try:
            widget.close()
        except RuntimeError:
            pass  # 이미 삭제된 위젯
    instance.processEvents()

    # 창을 닫으면 도는 렌더는 모듈 수준으로 분리됩니다(기다리면 크래시가
    # 나므로). 인터프리터가 끝나기 전에는 실제로 끝나 있어야 합니다 —
    # 안 그러면 도는 QThread가 파괴되어 종료 코드가 0xc0000409가 됩니다.
    from arw_selector.gui.loupe import wait_for_detached_renders

    wait_for_detached_renders()


def color_census(widget) -> dict[tuple[int, int, int], int]:
    """그려진 픽셀의 색 분포. 단색이면 아무것도 안 그려진 것입니다."""
    image = widget.grab().toImage()
    counts: dict[tuple[int, int, int], int] = {}
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            color = image.pixelColor(x, y)
            key = (color.red(), color.green(), color.blue())
            counts[key] = counts.get(key, 0) + 1
    return counts


def bright_pixels(counts: dict[tuple[int, int, int], int], threshold: int = 170) -> int:
    return sum(
        n for (r, g, b), n in counts.items()
        if r > threshold and g > threshold and b > threshold
    )


class TestCurveEditorRendering:
    @pytest.fixture
    def editor(self, app):
        widget = CurveEditor()
        widget.resize(400, 280)
        rng = np.random.default_rng(3)
        x = np.arange(256)
        widget.set_histogram(
            (np.exp(-((x - 100) ** 2) / 1200) * 6000).astype(np.float32)
        )
        return widget

    def test_identity_curve_is_visible(self, editor):
        counts = color_census(editor)
        assert len(counts) > 5, f"단색으로 그려졌다: {counts}"
        assert bright_pixels(counts) > 20, "곡선이 보이지 않습니다"

    def test_curve_with_points_is_visible(self, editor):
        """실제로 겪은 버그: 브러시가 남아 곡선이 전체가 덮였습니다."""
        editor.set_points(((64, 40), (128, 170), (200, 230)))
        counts = color_census(editor)

        assert len(counts) > 50, f"거의 그려지지 않았다 (색 {len(counts)}종)"
        assert bright_pixels(counts) > 50, "곡선과 핸들이 보이지 않습니다"

    def test_background_is_not_covered(self, editor):
        """배경색이 살아 있어야 합니다 — 덮였다면 무언가 전체를 칠한 것입니다."""
        editor.set_points(((128, 200),))
        counts = color_census(editor)
        background = counts.get((24, 24, 27), 0)
        assert background > 200, f"배경이 덮였다 (남은 픽셀 {background})"

    def test_channel_color_changes(self, editor):
        editor.set_points(((128, 200),))
        editor.set_channel("red")
        counts = color_census(editor)
        reddish = sum(
            n for (r, g, b), n in counts.items() if r > 150 and g < 140 and b < 140
        )
        assert reddish > 20, "빨강 채널 곡선이 빨갛게 그려지지 않았습니다"


class TestCurveEditorStandard:
    """곡선 편집기의 표준화 — 축 0~100, 파라메트릭 반영, 클리핑 표시."""

    @pytest.fixture
    def editor(self, app):
        widget = CurveEditor()
        widget.resize(380, 300)
        return widget

    def test_parametric_only_on_rgb(self, editor):
        editor.set_channel("rgb")
        editor.set_parametric(0, 0, 40, 0)
        assert editor._parametric_lut() is not None
        editor.set_channel("red")
        assert editor._parametric_lut() is None, "채널 곡선엔 파라메트릭 없음"

    def test_parametric_lifts_lights(self, editor):
        """밝음(+)은 중상단 구간을 위로 밀어 올립니다."""
        editor.set_channel("rgb")
        editor.set_parametric(0, 0, 60, 0)
        lut = editor._parametric_lut()
        # 62.5% 지점(≈160)이 항등선보다 위에 있어야 합니다
        assert lut[160] > 160

    def test_parametric_matches_engine(self, editor):
        """편집기와 엔진이 같은 파라메트릭 LUT를 써야 표시와 결과가 일치합니다."""
        from arw_selector.core.develop.engine import parametric_tone_lut

        editor.set_channel("rgb")
        editor.set_parametric(-30, 0, 40, 0)
        assert np.allclose(
            editor._parametric_lut(), parametric_tone_lut(-30, 0, 40, 0)
        )

    def test_composite_detects_clipping(self, editor):
        """양끝을 잘라내는 곡선은 0과 255를 만들어 냅니다."""
        editor.set_points(((40, 0), (215, 255)))
        lut = editor._composite_lut()
        assert lut.min() <= 0.5, "섀도우 클리핑이 감지되지 않았습니다"
        assert lut.max() >= 254.5, "하이라이트 클리핑이 감지되지 않았습니다"

    def test_axis_labels_rendered(self, editor):
        """0~100 눈금 라벨이 여백에 실제로 그려집니다."""
        counts = color_census(editor)
        # 라벨/파라메트릭/곡선으로 색이 여러 종류 나와야 합니다
        assert len(counts) > 8

    def test_smooth_not_linear(self, editor):
        """부드러운 곡선은 제어점 사이에서 선형 보간과 달라야 합니다."""
        import numpy as np

        from arw_selector.core.develop.engine import smooth_curve_lut

        points = [(0, 0), (64, 40), (192, 220), (255, 255)]
        smooth = smooth_curve_lut(points)
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        linear = np.interp(np.arange(256), xs, ys)
        # 중간 구간에서 확연히 달라야(부드럽게 휘어야) 합니다
        assert np.abs(smooth - linear).max() > 3
        # 제어점은 지나야 합니다
        for x, y in points:
            assert abs(smooth[x] - y) < 1.0

    def test_smooth_is_monotone(self, editor):
        """단조 증가 제어점이면 곡선도 뒤집히지 않아야 합니다 (오버슈트 없음)."""
        import numpy as np

        from arw_selector.core.develop.engine import smooth_curve_lut

        lut = smooth_curve_lut([(0, 0), (100, 90), (150, 95), (255, 255)])
        assert np.all(np.diff(lut) >= -0.01), "곡선이 역전되었습니다"

    def test_identity_endpoints_are_omitted(self, editor):
        """옮기지 않은 끝점은 내보내지 않습니다 (기존 프리셋과 같은 형태)."""
        assert editor.points() == ()
        editor.set_points(((128, 150),))
        assert editor.points() == ((128, 150),)

    def test_moved_black_point_reaches_settings(self, editor):
        """블랙 포인트를 끌면 그 값이 실제로 나가야 합니다.

        예전에는 끝점을 무조건 잘라내서, 편집기의 곡선만 올라가고 사진은
        그대로였습니다 — 화면과 결과가 어긋나는 상태였습니다.
        """
        editor._points[0][1] = 60.0
        assert (0, 60) in editor.points()

    def test_endpoint_round_trip(self, editor):
        """내보낸 끝점을 다시 넣어도 끝점 자리에 들어가야 합니다."""
        editor.set_points(((0, 60), (128, 150), (255, 200)))
        assert editor._points[0] == [0.0, 60.0]
        assert editor._points[-1] == [255.0, 200.0]
        assert len(editor._points) == 3
        assert editor.points() == ((0, 60), (128, 150), (255, 200))

    def test_editor_curve_matches_engine(self, editor):
        """편집기가 그리는 곡선과 엔진 LUT가 같아야 합니다."""
        import numpy as np

        from arw_selector.core.develop.engine import (
            _spline_lut, curve_control_points,
        )

        editor.set_points(((0, 60), (128, 150)))
        engine_lut = _spline_lut(curve_control_points(editor.points()))
        assert np.allclose(editor._curve_lut(), engine_lut)


class TestCurvePanelButtons:
    """곡선 위 채널/클리핑 버튼."""

    @pytest.fixture
    def panel(self, app):
        from arw_selector.gui.develop_panel import DevelopPanel

        return DevelopPanel()

    def test_channel_button_switches_channel(self, panel):
        panel.curve_channel_buttons.button(1).click()  # R
        assert panel.curve_channel.currentData() == "red"
        panel.curve_channel_buttons.button(3).click()  # B
        assert panel.curve_channel.currentData() == "blue"

    def test_channel_button_syncs_when_combo_changes(self, panel):
        panel.curve_channel.setCurrentIndex(2)  # green
        assert panel.curve_channel_buttons.button(2).isChecked()

    def test_clip_button_toggles_markers(self, panel):
        assert panel.curve_editor._show_clip is True
        panel.curve_clip_button.setChecked(False)
        assert panel.curve_editor._show_clip is False


class TestCurveEditorInteraction:
    """곡선 편집기를 실제 마우스 조작으로 확인합니다.

    set_points()는 불러오기용이라 신호를 내지 않습니다. 따라서 그 함수만
    호출하는 테스트는 사용자가 실제로 밟는 경로를 지나가지 않습니다.
    채널별 저장이 제대로 되는지는 클릭과 드래그로만 확인할 수 있습니다.
    """

    @pytest.fixture
    def panel(self, app):
        from arw_selector.gui.develop_panel import DevelopPanel

        widget = DevelopPanel()
        widget.resize(400, 900)
        widget.curve_editor.resize(400, 280)
        return widget

    @staticmethod
    def _click(app, widget, x, y, button=None):
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        button = button or Qt.LeftButton
        point = QPoint(x, y)
        for kind in (QMouseEvent.Type.MouseButtonPress,
                     QMouseEvent.Type.MouseButtonRelease):
            app.sendEvent(widget, QMouseEvent(
                kind, QPointF(point), QPointF(widget.mapToGlobal(point)),
                button, button, Qt.NoModifier))
        app.processEvents()

    def test_click_adds_point(self, app, panel):
        self._click(app, panel.curve_editor, 100, 100)
        assert len(panel.settings().curve.points_rgb) == 1

    def test_channels_keep_separate_points(self, app, panel):
        """채널을 오가도 각자의 점이 남아 있어야 합니다."""
        self._click(app, panel.curve_editor, 100, 100)

        panel.curve_channel.setCurrentIndex(1)  # 빨강
        app.processEvents()
        assert panel.curve_editor.points() == (), "채널 전환 시 편집기가 비어야 합니다"

        self._click(app, panel.curve_editor, 200, 180)
        settings = panel.settings()
        assert len(settings.curve.points_red) == 1
        assert len(settings.curve.points_rgb) == 1, "RGB 점이 사라졌습니다"

        panel.curve_channel.setCurrentIndex(0)
        app.processEvents()
        assert len(panel.curve_editor.points()) == 1, "RGB 점이 복원되지 않았습니다"

    def test_right_click_removes_point(self, app, panel):
        from PySide6.QtCore import Qt

        self._click(app, panel.curve_editor, 100, 100)
        assert len(panel.settings().curve.points_rgb) == 1

        self._click(app, panel.curve_editor, 100, 100, Qt.RightButton)
        assert panel.settings().curve.points_rgb == ()

    def test_channel_reset_leaves_other_channels(self, app, panel):
        self._click(app, panel.curve_editor, 100, 100)
        panel.curve_channel.setCurrentIndex(1)
        app.processEvents()
        self._click(app, panel.curve_editor, 200, 180)

        panel._reset_curve_channel()
        app.processEvents()

        settings = panel.settings()
        assert settings.curve.points_red == ()
        assert len(settings.curve.points_rgb) == 1, "다른 채널까지 초기화되었습니다"

    def test_loading_settings_shows_current_channel(self, app, panel):
        """설정을 불러오면 선택된 채널의 곡선이 보여야 합니다."""
        from arw_selector.core.develop import CurveSettings

        panel.set_settings(DevelopSettings(curve=CurveSettings(
            points_rgb=((60, 40), (190, 220)),
            points_red=((128, 150),),
        )))
        app.processEvents()
        assert len(panel.curve_editor.points()) == 2

        panel.curve_channel.setCurrentIndex(1)
        app.processEvents()
        assert panel.curve_editor.points() == ((128, 150),)

    def test_curve_changes_pixels(self, app):
        """편집기 값이 실제 렌더에 도달하는지 확인합니다."""
        from arw_selector.core.develop import CurveSettings
        from arw_selector.core.develop.engine import apply_settings

        ramp = np.dstack([np.tile(
            np.linspace(0, 255, 256, dtype=np.uint8), (64, 1))] * 3)
        flat = apply_settings(ramp, DevelopSettings())
        curved = apply_settings(ramp, DevelopSettings(
            curve=CurveSettings(points_rgb=((64, 20), (192, 235)))))
        assert not np.array_equal(flat, curved)

    def test_red_curve_touches_only_red(self, app):
        from arw_selector.core.develop import CurveSettings
        from arw_selector.core.develop.engine import apply_settings

        ramp = np.dstack([np.tile(
            np.linspace(0, 255, 256, dtype=np.uint8), (64, 1))] * 3)
        flat = apply_settings(ramp, DevelopSettings())
        red = apply_settings(ramp, DevelopSettings(
            curve=CurveSettings(points_red=((128, 200),))))

        # BGR 순서입니다
        assert np.abs(red[..., 2].astype(int) - flat[..., 2].astype(int)).max() > 10
        assert np.array_equal(red[..., 0], flat[..., 0]), "파랑 채널이 바뀌었습니다"


class TestResetButtons:
    """되돌릴 수단이 조작 위젯 바로 옆에 있어야 합니다.

    처음엔 18x18에 투명 배경 흐린 글자라 있는 줄도 몰랐습니다.
    """

    def test_slider_reset_exists_and_works(self, app):
        from arw_selector.gui.widgets import SliderRow

        row = SliderRow("노출", -5, 5, 0.0, decimals=2)
        assert row.reset_button is not None
        assert row.reset_button.width() >= 20

        row.set_value(2.5)
        assert row.value() == 2.5
        row.reset_button.click()
        assert row.value() == 0.0

    def test_slider_reset_disabled_at_default(self, app):
        """기본값이면 눌러도 소용없으므로 비활성이어야 합니다."""
        from arw_selector.gui.widgets import SliderRow

        row = SliderRow("대비", -100, 100, 0)
        assert row.reset_button.isEnabled() is False

        row.set_value(30)
        assert row.reset_button.isEnabled() is True

        row.reset()
        assert row.reset_button.isEnabled() is False

    def test_slider_reset_respects_nonzero_default(self, app):
        """기본값이 0이 아닌 항목도 그 값으로 돌아가야 합니다."""
        from arw_selector.gui.widgets import SliderRow

        row = SliderRow("혼합", 0, 100, 50)
        row.set_value(80)
        row.reset_button.click()
        assert row.value() == 50

    def test_color_wheel_reset(self, app):
        from arw_selector.gui.color_wheel import ColorGradeZoneWidget

        zone = ColorGradeZoneWidget("어두운 영역")
        assert zone.reset_button.isEnabled() is False

        zone.set_values(210, 60, -30)
        assert zone.values() == (210, 60, -30)
        # 리셋 여부와 무관하게 값 텍스트가 갱신되어야 합니다
        assert "210" in zone.wheel_value.text() and "60" in zone.wheel_value.text()
        assert zone.lum_value.text() == "-30"
        assert zone.reset_button.isEnabled() is True

        zone.reset_button.click()
        assert zone.values() == (0, 0, 0)

    def test_color_wheel_shows_values(self, app):
        """휠에 숫자 값이 보여야 같은 설정을 다시 맞출 수 있습니다."""
        from arw_selector.gui.color_wheel import ColorGradeZoneWidget

        zone = ColorGradeZoneWidget("중간 영역")
        # 채도 0이면 색조는 의미 없으므로 '중립'
        assert zone.wheel_value.text() == "Neutral"
        assert zone.lum_value.text() == "+0"

        zone.set_values(235, 25, 12)
        assert "235" in zone.wheel_value.text()
        assert "25" in zone.wheel_value.text()
        assert zone.lum_value.text() == "+12"

    def test_reset_button_visible_in_render(self, app):
        """실제로 그려지는지 픽셀로 확인합니다."""
        from arw_selector.gui.widgets import SliderRow

        row = SliderRow("노출", -5, 5, 0.0, decimals=2)
        row.resize(300, 60)
        row.set_value(2.0)

        counts = color_census(row)
        assert len(counts) > 5, "리셋 버튼이 그려지지 않았습니다"


class TestLoupeMinimumSize:
    """루페 창은 내용이 잘릴 만큼 좁게 줄어들 수 없어야 합니다.

    보정 패널이 348px 고정폭이라, 창에 최소 크기가 없으면 그보다 좁게
    줄였을 때 패널이 줄어드는 대신 오른쪽(값 박스·리셋 버튼·슬라이더
    우측)이 창 밖으로 잘립니다. 실제로 그 상태를 겪었습니다.
    """

    @pytest.fixture
    def dialog(self, app):
        from pathlib import Path

        from arw_selector.core.types import ImageRecord
        from arw_selector.gui.loupe import LoupeDialog

        record = ImageRecord(path=Path("DSC0001.ARW"))
        dlg = LoupeDialog(record, [record])
        yield dlg
        dlg.close()

    def test_minimum_covers_layout_minimum(self, dialog):
        """창 최소 폭이 레이아웃이 요구하는 최소 폭 이상이어야 합니다."""
        layout_min = dialog.layout().minimumSize().width()
        assert dialog.minimumWidth() >= layout_min, (
            f"창 최소폭 {dialog.minimumWidth()} < 레이아웃 최소폭 {layout_min}"
        )

    def test_cannot_shrink_below_minimum(self, dialog):
        """최소보다 좁게 요청해도 그 아래로 줄어들지 않아야 합니다."""
        dialog.show()
        dialog.resize(500, 400)
        dialog.layout().activate()
        assert dialog.width() >= dialog.minimumWidth()
        assert dialog.height() >= dialog.minimumHeight()

    def test_panel_content_is_not_clipped(self, dialog):
        """보정 패널 내용이 가로로 잘리지 않아야 합니다.

        섹션에 위젯을 추가하다 폭이 모자라면 Qt는 스크롤바를 띄우는 대신
        버튼 글자를 뭉개 잘라 버립니다. 조용히 사라지므로 눈으로 보기 전에는
        모릅니다 — 실제로 광학 섹션에 버튼을 넣으면서 두 번 겪었습니다.

        minimumSizeHint가 아니라 선호 폭(sizeHint)으로 봐야 합니다.
        """
        panel = dialog.panel
        needed = panel.required_content_width()
        available = panel.content_width()
        assert needed <= available, (
            f"보정 패널 내용이 {needed}px를 요구하는데 {available}px만 있습니다. "
            f"develop_panel.PANEL_WIDTH 를 최소 "
            f"{needed - available + panel.width()}px 로 올리세요."
        )

    def test_reset_button_inside_window_at_minimum(self, dialog):
        """콘텐츠를 담는 폭에서 슬라이더 리셋 버튼이 창 안에 있어야 합니다.

        패널에 가로 스크롤이 생긴 뒤로 '최소 폭'은 스크롤이 걸린 상태일 수
        있습니다(영어는 라벨이 길어 특히 그렇습니다). 그때 오른쪽 위젯은
        가로 스크롤로 닿으며, 그 접근성은 test_overflow_is_scrollable_not_clipped
        가 따로 확인합니다. 여기서는 콘텐츠가 다 들어가는 폭에서 리셋 버튼이
        창 안에 있는지 봅니다.
        """
        dialog.show()
        panel = dialog.panel
        extra = max(0, panel.required_content_width() - panel.content_width())
        dialog.resize(dialog.minimumWidth() + extra + 40, dialog.minimumHeight())
        dialog.layout().activate()

        row = panel.rows["basic.exposure"]
        reset = row.reset_button
        right_edge = reset.mapTo(dialog, reset.rect().topRight()).x()
        assert right_edge <= dialog.width(), (
            f"리셋 버튼 오른쪽 끝 {right_edge} > 창 폭 {dialog.width()}"
        )

    def test_fast_mode_flag_stored(self, app):
        """빠른 미리보기 모드 플래그가 창에 전달됩니다."""
        from pathlib import Path

        from arw_selector.core.types import ImageRecord
        from arw_selector.gui.loupe import LoupeDialog

        record = ImageRecord(path=Path("DSC0001.ARW"))
        fast = LoupeDialog(record, [record], fast=True)
        develop = LoupeDialog(record, [record], fast=False)
        try:
            assert fast._fast is True
            assert develop._fast is False
        finally:
            fast.close()
            develop.close()


class TestDevelopPanelBinding:
    """패널 ↔ 설정 왕복이 손실 없이 이루어져야 합니다.

    읽기와 쓰기를 각각 손으로 나열하던 시절에는 한쪽에만 필드를 추가해
    값이 저장되지 않는 문제가 있었습니다. 선언적 표로 통합한 뒤에도
    그 성질이 유지되는지 확인합니다.
    """

    @pytest.fixture
    def panel(self, app):
        from arw_selector.gui.develop_panel import DevelopPanel

        return DevelopPanel()

    def test_every_binding_points_to_a_real_field(self, panel):
        from dataclasses import fields

        from arw_selector.gui.develop_panel import SLIDER_BINDINGS

        settings = DevelopSettings()
        for key, (section, field, _cast, _scale) in SLIDER_BINDINGS.items():
            assert hasattr(settings, section), f"{key}: 섹션 {section} 없음"
            names = {f.name for f in fields(getattr(settings, section))}
            assert field in names, f"{key}: {section}.{field} 없음"

    def test_every_binding_has_a_row(self, panel):
        from arw_selector.gui.develop_panel import SLIDER_BINDINGS

        for key in SLIDER_BINDINGS:
            assert key in panel.rows, f"{key}에 대응하는 슬라이더가 없습니다"

    def test_round_trip_preserves_all_slider_values(self, panel):
        """설정을 넣고 다시 읽으면 같은 값이 나와야 합니다."""
        from arw_selector.core.develop import (
            BasicSettings,
            DetailSettings,
            EffectSettings,
            GeometrySettings,
            OpticsSettings,
        )
        from arw_selector.gui.develop_panel import SLIDER_BINDINGS

        original = DevelopSettings(
            basic=BasicSettings(
                tint=-20, exposure=1.25, contrast=15,
                highlights=-40, shadows=35, whites=10, blacks=-5,
                texture=20, clarity=25, dehaze=12, vibrance=18, saturation=-8,
            ),
            detail=DetailSettings(
                sharpen_amount=60, sharpen_radius=2.5,
                noise_reduction=30, color_noise_reduction=45,
                noise_detail=20, color_noise_radius=85,
            ),
            effects=EffectSettings(
                grain_amount=25, grain_size=40,
                vignette_amount=-35, vignette_midpoint=60,
            ),
            optics=OpticsSettings(
                distortion=-15, manual_vignetting=20,
                defringe_purple=40, defringe_green=25,
            ),
            geometry=GeometrySettings(
                crop_left=0.1, crop_top=0.15, crop_right=0.9, crop_bottom=0.85,
                straighten=2.5,
            ),
        )

        panel.set_settings(original)
        restored = panel.settings()

        for key, (section, field, _cast, _scale) in SLIDER_BINDINGS.items():
            expected = getattr(getattr(original, section), field)
            actual = getattr(getattr(restored, section), field)
            assert actual == pytest.approx(expected, abs=0.02), (
                f"{key} ({section}.{field}): {expected} -> {actual}"
            )

    def test_crop_scale_conversion(self, panel):
        """크롭은 UI에서 %, 설정에서는 0~1입니다."""
        from arw_selector.core.develop import GeometrySettings

        panel.set_settings(
            DevelopSettings(geometry=GeometrySettings(crop_left=0.25))
        )
        assert panel.rows["geo.crop_left"].value() == pytest.approx(25.0)
        assert panel.settings().geometry.crop_left == pytest.approx(0.25)

    def test_neutral_round_trip(self, panel):
        panel.set_settings(DevelopSettings())
        assert panel.settings().is_neutral()

    def test_noise_algorithm_round_trip(self, panel):
        """노이즈 방식은 슬라이더가 아니라 콤보라 표에서 빠져 있습니다."""
        from arw_selector.core.develop import DetailSettings, NoiseAlgorithm

        for algorithm in NoiseAlgorithm:
            panel.set_settings(DevelopSettings(
                detail=DetailSettings(noise_reduction=40, noise_algorithm=algorithm)
            ))
            assert panel.settings().detail.noise_algorithm is algorithm

    def test_untouched_panel_uses_default_algorithm(self, panel):
        """패널을 열기만 해도 방식이 바뀌면 안 됩니다.

        콤보의 첫 항목과 DetailSettings의 기본값이 어긋나 있으면, 사진을
        열었다는 것만으로 노이즈 감소 결과가 달라집니다.
        """
        from arw_selector.core.develop import DetailSettings

        assert (panel.settings().detail.noise_algorithm
                is DetailSettings().noise_algorithm)

    def test_temperature_untouched_stays_zero(self, panel):
        """색온도를 안 건드리면 as-shot 위치에 있어도 0(변화 없음)으로 저장."""
        from arw_selector.core.develop import BasicSettings

        panel.set_as_shot_kelvin(3200)
        panel.set_settings(DevelopSettings())  # temperature=0
        assert panel.rows["basic.temperature"].value() == 3200  # 표시는 as-shot
        assert panel.settings().basic.temperature == 0          # 저장은 0

    def test_temperature_absolute_round_trip(self, panel):
        """색온도를 지정하면 절대 Kelvin이 그대로 왕복합니다."""
        from arw_selector.core.develop import BasicSettings

        panel.set_settings(
            DevelopSettings(basic=BasicSettings(temperature=2500))
        )
        assert panel.rows["basic.temperature"].value() == 2500
        assert panel.settings().basic.temperature == 2500

    def test_as_shot_does_not_override_absolute(self, panel):
        """이미 절대값이 지정돼 있으면 as-shot이 덮어쓰지 않습니다."""
        from arw_selector.core.develop import BasicSettings

        panel.set_settings(
            DevelopSettings(basic=BasicSettings(temperature=2500))
        )
        panel.set_as_shot_kelvin(4000)
        assert panel.settings().basic.temperature == 2500


class TestPresetImportExport:
    """프리셋 파일 가져오기/내보내기 — 저장 폴더가 환경마다 달라도 파일로 주고받기."""

    def _bar(self, app, tmp_path):
        from arw_selector.core.presets import PresetStore
        from arw_selector.gui.preset_bar import PresetBar

        store = PresetStore("develop_presets", tmp_path)
        applied = []
        return PresetBar(store, collect=lambda: {"x": 1},
                         apply=lambda d: applied.append(d)), store, applied

    def test_import_adds_and_applies(self, app, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QFileDialog

        from arw_selector.core.develop import BasicSettings, DevelopSettings
        from arw_selector.core.presets import PresetStore

        # 외부 프리셋 파일을 하나 만들어 둡니다
        external = PresetStore("develop_presets", tmp_path / "other")
        settings = DevelopSettings(basic=BasicSettings(exposure=1.5, temperature=2500))
        src = external.save("무대", settings.to_dict())

        bar, store, applied = self._bar(app, tmp_path)
        monkeypatch.setattr(QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, **k: (str(src), "")))
        bar.import_preset()

        assert store.exists("무대"), "가져온 프리셋이 저장되지 않았습니다"
        loaded = DevelopSettings.from_dict(store.load("무대"))
        assert loaded.basic.exposure == 1.5
        assert applied and applied[-1] == settings.to_dict()  # 즉시 적용됨
        assert "무대" in [bar.combo.itemText(i) for i in range(bar.combo.count())]

    def test_import_rejects_non_preset(self, app, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QFileDialog, QMessageBox

        bad = tmp_path / "bad.yaml"
        bad.write_text("이건 프리셋이 아닙니다", encoding="utf-8")

        bar, store, _ = self._bar(app, tmp_path)
        monkeypatch.setattr(QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, **k: (str(bad), "")))
        warned = []
        monkeypatch.setattr(QMessageBox, "warning",
                            staticmethod(lambda *a, **k: warned.append(a)))
        bar.import_preset()
        assert warned, "잘못된 파일에 경고가 없습니다"
        assert store.list() == []

    def test_export_writes_file(self, app, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QFileDialog

        from arw_selector.core.develop import BasicSettings, DevelopSettings

        bar, store, _ = self._bar(app, tmp_path)
        store.save("내룩", DevelopSettings(basic=BasicSettings(contrast=20)).to_dict())
        bar.refresh(select="내룩")

        out = tmp_path / "exported.yaml"
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: (str(out), "")))
        bar.export_current()
        assert out.exists()
        assert "contrast: 20" in out.read_text(encoding="utf-8")


class TestClippingOverlay:
    """클리핑 경고 토글과 이미지 오버레이."""

    def test_overlay_paints_highlights_red(self, app):
        from arw_selector.gui.loupe import clip_overlay

        image = np.full((10, 10, 3), 100, np.uint8)
        image[0, 0] = (200, 255, 200)   # 초록 채널 날아감
        out = clip_overlay(image, show_shadow=False, show_highlight=True)
        assert tuple(out[0, 0]) == (0, 0, 255)   # BGR 빨강
        assert tuple(out[5, 5]) == (100, 100, 100)  # 나머지는 그대로

    def test_overlay_paints_shadows_blue(self, app):
        from arw_selector.gui.loupe import clip_overlay

        image = np.full((10, 10, 3), 100, np.uint8)
        image[0, 0] = (1, 0, 2)   # 전부 어두움
        out = clip_overlay(image, show_shadow=True, show_highlight=False)
        assert tuple(out[0, 0]) == (255, 0, 0)   # BGR 파랑

    def test_overlay_off_is_noop(self, app):
        from arw_selector.gui.loupe import clip_overlay

        image = np.full((10, 10, 3), 255, np.uint8)
        out = clip_overlay(image, show_shadow=False, show_highlight=False)
        assert np.array_equal(out, image)

    def test_corner_click_toggles_overlay(self, app):
        """히스토그램 좌/우 상단 코너 클릭이 오버레이를 켠다."""
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        from arw_selector.gui.histogram import HistogramWidget

        widget = HistogramWidget()
        widget.resize(320, 140)

        events = []
        widget.overlay_toggled.connect(lambda s, h: events.append((s, h)))

        def click(x, y):
            for kind in (QMouseEvent.Type.MouseButtonPress,):
                widget.mousePressEvent(QMouseEvent(
                    kind, QPointF(x, y), QPointF(x, y),
                    Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))

        click(5, 5)                       # 좌상단 → 섀도우 토글
        assert widget.overlay_state() == (True, False)
        click(widget.width() - 5, 5)      # 우상단 → 하이라이트 토글
        assert widget.overlay_state() == (True, True)
        click(160, 70)                    # 가운데 → 채널만 전환, 오버레이 유지
        assert widget.overlay_state() == (True, True)
        assert events[-1] == (True, True)


class TestHistogramRendering:
    @pytest.fixture
    def widget(self, app):
        histogram = HistogramWidget()
        histogram.resize(320, 140)
        return histogram

    def test_empty_histogram_shows_message(self, widget):
        counts = color_census(widget)
        assert len(counts) > 1, "빈 히스토그램에 안내 문구가 없습니다"

    def test_histogram_draws_channels(self, widget):
        rng = np.random.default_rng(7)
        image = rng.integers(20, 235, (200, 300, 3), dtype=np.uint8)
        widget.set_image(image)

        counts = color_census(widget)
        assert len(counts) > 20, f"히스토그램이 그려지지 않았다 (색 {len(counts)}종)"

    def test_all_three_modes_render(self, widget):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        widget.resize(320, 140)
        rng = np.random.default_rng(9)
        widget.set_image(rng.integers(20, 235, (200, 300, 3), dtype=np.uint8))

        def center_click():
            # 코너가 아닌 가운데를 눌러야 채널만 전환됩니다.
            point = QPointF(widget.width() / 2, widget.height() / 2)
            widget.mousePressEvent(QMouseEvent(
                QMouseEvent.Type.MouseButtonPress, point, point,
                Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))

        for expected_mode in range(3):
            assert widget._mode == expected_mode
            counts = color_census(widget)
            assert len(counts) > 10, f"모드 {expected_mode}에서 렌더링 실패"
            center_click()

        assert widget._mode == 0, "세 번 누르면 처음 모드로 돌아와야 합니다"

    def test_clipping_detected_on_blown_image(self, widget):
        blown = np.full((100, 100, 3), 255, np.uint8)
        widget.set_image(blown)
        assert widget._highlight_clip is True

    def test_no_clipping_on_midtone_image(self, widget):
        flat = np.full((100, 100, 3), 128, np.uint8)
        widget.set_image(flat)
        assert widget._highlight_clip is False
        assert widget._shadow_clip is False


class TestFullRenderButtonVisibility:
    """켜 놓고도 켠 줄 모르면 안 됩니다.

    어두운 테마에서 눌린 상태를 Qt 기본 음영으로만 표시하면 꺼진 버튼과
    거의 구분되지 않습니다. 꺼짐/켜짐/생성 중을 색으로 갈라 놓습니다.
    """

    @pytest.fixture
    def dialog(self, app):
        from pathlib import Path

        from arw_selector.core.types import ImageRecord
        from arw_selector.gui.loupe import LoupeDialog

        widget = LoupeDialog(ImageRecord(path=Path("558A8911.CR3")), fast=True)
        widget.show()
        app.processEvents()
        yield widget
        widget.close()

    def _dominant(self, button, app):
        from collections import Counter

        from PySide6.QtGui import QImage

        app.processEvents()
        image = QImage(button.size(), QImage.Format_RGB32)
        button.render(image)
        counts = Counter()
        for x in range(0, image.width(), 2):
            for y in range(0, image.height(), 2):
                counts[image.pixelColor(x, y).name()] += 1
        return counts.most_common(1)[0][0]

    def test_three_states_are_distinct(self, dialog, app):
        button = dialog.final_button

        button.setChecked(False)
        dialog._set_full_render_state(busy=False)
        off = self._dominant(button, app)

        button.setChecked(True)
        dialog._set_full_render_state(busy=False)
        on = self._dominant(button, app)

        dialog._set_full_render_state(busy=True)
        busy = self._dominant(button, app)

        assert len({off, on, busy}) == 3, f"색이 겹칩니다: {off} / {on} / {busy}"

    def test_returns_to_on_colour_after_render(self, dialog, app):
        """생성이 끝나도 모드는 켜져 있으므로 켜짐 색으로 돌아와야 합니다."""
        button = dialog.final_button
        button.setChecked(True)

        dialog._set_full_render_state(busy=False)
        on = self._dominant(button, app)
        dialog._set_full_render_state(busy=True)
        dialog._set_full_render_state(busy=False)

        assert self._dominant(button, app) == on

    def test_label_matches_state(self, dialog):
        """작업 중 글자는 '생성'과 '대기'를 구분합니다.

        둘은 다른 상황입니다. 생성 중은 내 렌더가 돌고 있는 것이고, 대기
        중은 다른 렌더가 메모리를 쥐고 있어 아직 출발도 못 한 것입니다.
        같은 글자로 뭉뚱그리면 왜 안 끝나는지 알 수 없습니다.
        """
        dialog._final_worker = object()   # 내 렌더가 도는 중
        dialog._set_full_render_state(busy=True)
        assert "Rendering" in dialog.final_button.text()

        dialog._final_worker = None       # 앞 렌더를 기다리는 중
        dialog._set_full_render_state(busy=True)
        assert "Waiting" in dialog.final_button.text()

        dialog._set_full_render_state(busy=False)
        assert dialog.final_button.text() == "Full Render"

    def test_button_locks_after_toggle(self, dialog):
        """켠 직후에는 못 누릅니다 — 연타하면 무거운 렌더가 겹칩니다."""
        dialog._lock_full_render_button()
        assert not dialog.final_button.isEnabled()

        # 잠금 중에는 상태를 갱신해도 계속 잠겨 있어야 합니다
        dialog._set_full_render_state(busy=False)
        assert not dialog.final_button.isEnabled()

        dialog._full_render_lock.stop()
        dialog._set_full_render_state(busy=False)
        assert dialog.final_button.isEnabled()


class _FakeSignal:
    """connect/disconnect만 받아 주는 자리표시자."""

    def connect(self, *_args):
        pass

    def disconnect(self, *_args):
        pass


class TestFullRenderWorkerLifetime:
    """도는 QThread의 참조를 놓으면 Qt가 프로세스를 죽입니다.

    실측: Full Render를 켠 채 조작하면 _schedule_full_render가 매번
    _abandon_render를 부르는데, 거기서 아직 도는 워커의 마지막 참조를
    버렸습니다. Qt는 "QThread: Destroyed while thread is still running"을
    qFatal로 처리해 프로세스를 즉사시킵니다 — Windows fail-fast(0xc0000409)라
    faulthandler도 예외 훅도 아무것도 남기지 못했습니다.
    """

    @pytest.fixture
    def dialog(self, app):
        from pathlib import Path

        from arw_selector.core.types import ImageRecord
        from arw_selector.gui.loupe import LoupeDialog

        widget = LoupeDialog(
            ImageRecord(path=Path("558A8911.CR3")), fast=True
        )
        yield widget
        widget.close()

    def test_cancelled_worker_is_kept_alive(self, dialog, app):
        """취소한 워커가 아직 돌고 있으면 붙잡고 있어야 합니다.

        렌더가 언제 끝나는지에 기대지 않도록, 실행 중이라고 답하는 가짜
        워커를 넣어 취소 경로만 떼어 봅니다.
        """
        class StillRunning:
            def __init__(self):
                self.cancelled = False
                self.done = _FakeSignal()
                self.failed = _FakeSignal()
                self.finished = _FakeSignal()

            def cancel(self):
                self.cancelled = True

            def isRunning(self):
                return True

        worker = StillRunning()
        dialog._final_worker = worker

        dialog._abandon_render()

        assert worker.cancelled, "취소 신호를 안 보냈습니다"
        assert dialog._final_worker is None
        assert worker in dialog._retired_workers, (
            "도는 워커의 참조를 버렸습니다 — Qt가 프로세스를 죽입니다"
        )
        dialog._retired_workers = []  # 정리 (가짜라 wait()이 없습니다)

    def test_finished_worker_is_not_retained(self, dialog, app):
        """이미 끝난 워커까지 붙들고 있으면 메모리만 씁니다."""
        from arw_selector.gui.loupe import FinalRenderWorker

        from arw_selector.core.develop import DevelopSettings

        worker = FinalRenderWorker(
            dialog.record.path, DevelopSettings(), None,
            target_long_edge=64, generation=1,
        )
        worker.start()
        worker.wait(30000)

        dialog._retire_worker(worker)

        assert worker not in dialog._retired_workers

    def test_edit_stops_running_full_render(self, dialog, app):
        """편집이 들어오면 진행 중인 Full Render를 즉시 접어야 합니다.

        예전에는 취소만 하고 곧바로 다시 예약해서, 슬라이더를 계속 움직이는
        동안 무거운 렌더가 뜨고 지기를 반복하며 조작이 무거워졌습니다.
        모드 자체는 켜 둔 채로 지금 도는 작업만 멈춥니다.
        """
        class StillRunning:
            def __init__(self):
                self.done = _FakeSignal()
                self.failed = _FakeSignal()
                self.finished = _FakeSignal()

            def cancel(self):
                pass

            def isRunning(self):
                return True

        dialog.final_button.setChecked(True)
        dialog._final_worker = StillRunning()

        dialog._on_settings_changed()

        assert dialog._final_worker is None, "진행 중인 렌더가 그대로입니다"
        assert dialog.final_button.isChecked(), "모드까지 꺼 버렸습니다"
        assert dialog.final_button.text() == "Full Render"
        dialog._retired_workers = []

    def test_stop_is_a_noop_when_nothing_running(self, dialog):
        """돌고 있지 않으면 아무것도 건드리지 않습니다."""
        dialog._final_worker = None
        dialog._full_render_timer.stop()

        dialog._stop_full_render_for_edit()

        assert dialog._final_worker is None
        assert dialog._retired_workers == []

    def test_closing_detaches_instead_of_waiting(self, dialog, app):
        """닫을 때 기다리면 안 됩니다 — 기다림이 모자라면 크래시입니다.

        cancel()은 플래그만 세우는데, 워커가 rawpy 디모자이크(수 초짜리 단일
        C 호출) 안에 있으면 그 플래그를 볼 지점이 없습니다. 실측: 렌더 도중
        창을 12번 여닫으니 'QThread: Destroyed while thread is still running'
        으로 프로세스가 죽었습니다. 기다리는 대신 참조를 모듈로 옮겨
        스레드가 제 속도로 끝나게 둡니다.
        """
        from arw_selector.gui import loupe as loupe_module

        class StillRunning:
            def __init__(self):
                self.cancelled = False
                self.done = _FakeSignal()
                self.failed = _FakeSignal()
                self.finished = _FakeSignal()

            def cancel(self):
                self.cancelled = True

            def isRunning(self):
                return True

        worker = StillRunning()
        dialog._final_worker = worker
        before = set(loupe_module._RUNNING_RENDERS)

        dialog._shutdown_workers()

        try:
            assert worker.cancelled
            assert dialog._final_worker is None
            assert worker in loupe_module._RUNNING_RENDERS, (
                "도는 워커를 놓아 버렸습니다 — Qt가 프로세스를 죽입니다"
            )
        finally:
            loupe_module._RUNNING_RENDERS.clear()
            loupe_module._RUNNING_RENDERS.update(before)

    def test_finished_worker_is_not_detached(self, dialog):
        """이미 끝난 워커까지 붙들면 목록만 자랍니다."""
        from arw_selector.gui import loupe as loupe_module

        class Done:
            def __init__(self):
                self.done = _FakeSignal()
                self.failed = _FakeSignal()
                self.finished = _FakeSignal()

            def cancel(self):
                pass

            def isRunning(self):
                return False

        worker = Done()
        dialog._final_worker = worker
        before = len(loupe_module._RUNNING_RENDERS)

        dialog._shutdown_workers()

        assert len(loupe_module._RUNNING_RENDERS) == before

    def test_reap_drops_completed_workers(self, dialog):
        """끝난 워커는 목록에서 치워져야 합니다."""
        from arw_selector.core.develop import DevelopSettings
        from arw_selector.gui.loupe import FinalRenderWorker

        worker = FinalRenderWorker(
            dialog.record.path, DevelopSettings(), None,
            target_long_edge=64, generation=1,
        )
        dialog._retired_workers = [worker]  # 시작하지 않았으므로 isRunning False

        dialog._reap_workers()

        assert dialog._retired_workers == []


class TestPanelSurvivesNarrowScreens:
    """좁은 화면에서 오른쪽이 잘려 손이 닿지 않으면 안 됩니다.

    실측 배경: FHD 100%에서 보정 패널 오른쪽이 잘린 채 떴고, 사용자가
    스플리터를 직접 끌어야만 했습니다. 최소 폭이 화면보다 커지면
    스플리터가 그 요구를 들어줄 수 없기 때문입니다.
    """

    def test_minimum_width_stays_within_screen(self, app):
        from arw_selector.gui.develop_panel import DevelopPanel

        panel = DevelopPanel()
        available = app.primaryScreen().availableGeometry().width()

        assert panel.minimumWidth() <= available * 0.5, (
            f"최소 폭 {panel.minimumWidth()}이 화면 {available}의 절반을 넘습니다"
        )

    def test_overflow_is_scrollable_not_clipped(self, app):
        """최소 폭을 낮춘 대신, 넘치는 내용은 가로 스크롤로 닿아야 합니다."""
        from PySide6.QtCore import Qt

        from arw_selector.gui.develop_panel import DevelopPanel

        panel = DevelopPanel()

        assert (
            panel._scroll.horizontalScrollBarPolicy() == Qt.ScrollBarAsNeeded
        ), "가로로 넘치면 잘려 나갑니다"

    def test_dialog_opens_inside_the_screen(self, app):
        """화면보다 큰 창으로 열면 창 관리자가 줄이면서 패널이 잘립니다."""
        from pathlib import Path

        from arw_selector.core.types import ImageRecord
        from arw_selector.gui.loupe import LoupeDialog

        available = app.primaryScreen().availableGeometry()
        dialog = LoupeDialog(
            ImageRecord(path=Path("558A8911.CR3")), fast=True
        )
        try:
            # 13" MacBook Air (M1)의 기본 Retina 1440x900 points에서 메뉴
            # 막대와 Dock을 뺀 크기. 애플 실리콘 맥북 중 가장 낮습니다.
            assert dialog.width() <= max(1440, available.width())
            assert dialog.height() <= max(806, available.height())
        finally:
            # 닫지 않고 두면 GC 시점에 워커가 도는 채로 파괴되어 Qt가
            # 프로세스를 죽입니다. 실제로 테스트 종료 시 0xc0000409가 났습니다.
            dialog.close()


class TestGridIsScannable:
    """3000장을 훑는 화면입니다. 등급이 한눈에 안 들어오면 도구가 아닙니다.

    실측 배경: 예전 화면은 색 66종에 두 회색이 81%를 차지했고, 등급은
    2px 테두리로만 표시돼 구분이 안 됐습니다.
    """

    def _cell(self, app, grade, score=72.0, selected=False):
        from pathlib import Path

        from PySide6.QtCore import QRect
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtWidgets import QStyleOptionViewItem

        from arw_selector.core.types import Grade, ImageRecord
        from arw_selector.gui.grid_view import RECORD_ROLE, ThumbnailDelegate

        record = ImageRecord(path=Path("DSC00001.ARW"))
        record.manual_grade = grade
        record.score = score

        class _Index:
            def isValid(self):
                return True

            def data(self, role):
                return record if role == RECORD_ROLE else None

        delegate = ThumbnailDelegate()
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 196, 214)
        if selected:
            from PySide6.QtWidgets import QStyle

            option.state |= QStyle.State_Selected

        image = QImage(196, 214, QImage.Format_RGB32)
        image.fill(0x161618)
        painter = QPainter(image)
        delegate.paint(painter, option, _Index())
        painter.end()
        return image

    def _colours(self, image):
        from collections import Counter

        counts = Counter()
        for x in range(0, image.width(), 2):
            for y in range(0, image.height(), 2):
                counts[image.pixelColor(x, y).name()] += 1
        return counts

    def test_each_grade_paints_its_own_colour(self, app):
        from arw_selector.core.types import Grade
        from arw_selector.gui.grid_view import GRADE_COLORS

        for grade in (Grade.KEEP, Grade.REVIEW, Grade.REJECT):
            counts = self._colours(self._cell(app, grade))
            expected = GRADE_COLORS[grade].name()

            assert counts.get(expected, 0) > 60, (
                f"{grade.value} 색이 거의 안 칠해졌습니다"
            )

    def test_grades_are_visually_distinct(self, app):
        from arw_selector.core.types import Grade

        dominant = {}
        for grade in (Grade.KEEP, Grade.REVIEW, Grade.REJECT):
            counts = self._colours(self._cell(app, grade))
            # 배경을 뺀 뒤 가장 많이 쓰인 색
            counts.pop("#161618", None)
            counts.pop("#18181a", None)
            dominant[grade] = counts.most_common(1)[0][0]

        assert len(set(dominant.values())) == 3, f"등급끼리 겹칩니다: {dominant}"

    def test_selection_is_distinct_from_grade(self, app):
        """선택 표시가 등급 색에 묻히면 무엇을 고른 건지 모릅니다."""
        from arw_selector.core.types import Grade

        plain = self._colours(self._cell(app, Grade.KEEP))
        chosen = self._colours(self._cell(app, Grade.KEEP, selected=True))

        assert set(chosen) - set(plain), "선택했는데 화면이 그대롭니다"

    def test_filter_button_shows_which_is_active(self, app):
        """'전체'가 켜졌을 때 회색이면 꺼진 것과 구분이 안 됩니다.

        표시 이름이 아니라 필터 **값**으로 찾습니다 — 이름은 번역되므로
        로케일이 바뀌면 조회가 실패합니다.
        """
        from arw_selector.gui.filter_bar import FILTERS

        colours = {value: colour for value, colour in FILTERS}

        assert colours[None].lower() != "#8a8a92", (
            "'전체' 활성 색이 회색으로 되돌아갔습니다"
        )


class TestSliderTracksTellTheTruth:
    """트랙 색이 실제 동작과 다른 말을 하면 안 됩니다."""

    def test_temperature_neutral_sits_at_as_shot(self):
        """색온도는 절대 Kelvin이라 '변화 없음'이 트랙 한가운데가 아닙니다.

        5500K 촬영이면 2000~12000 구간의 35% 지점입니다. 파랑→주황을
        균등하게 깔면 손대지 않은 상태의 핸들 밑이 푸르스름해서, 아무것도
        안 했는데 차갑게 보정된 것처럼 읽힙니다.
        """
        from arw_selector.gui.widgets import temperature_track_colors

        stops = temperature_track_colors(5500, 2000, 12000)
        positions = [pos for pos, _ in stops]

        assert positions[0] == 0.0 and positions[-1] == 1.0
        assert positions[1] == pytest.approx(0.35, abs=0.01)

    def test_temperature_pivot_follows_camera(self):
        """텅스텐 조명(3200K)으로 찍었으면 중립점도 왼쪽으로 가야 합니다."""
        from arw_selector.gui.widgets import temperature_track_colors

        warm_shot = temperature_track_colors(3200, 2000, 12000)[1][0]
        cool_shot = temperature_track_colors(9000, 2000, 12000)[1][0]

        assert warm_shot < cool_shot

    def test_pivot_stays_inside_track(self):
        """as-shot이 범위 밖이어도 무채색 지점이 트랙 밖으로 나가면 안 됩니다."""
        from arw_selector.gui.widgets import temperature_track_colors

        for kelvin in (0, 500, 50000):
            pivot = temperature_track_colors(kelvin, 2000, 12000)[1][0]
            assert 0.0 < pivot < 1.0, f"{kelvin}K에서 중립점이 {pivot}"

    def test_hsl_saturation_track_changes_only_saturation(self):
        """채도 트랙이 밝기까지 바꾸면 채도를 올릴 때 밝아지는 줄 압니다."""
        from PySide6.QtGui import QColor

        from arw_selector.gui.widgets import hsl_band_colors

        left, right = (QColor(c) for c in hsl_band_colors(0, "saturation"))

        assert left.value() == right.value(), "명도가 함께 변합니다"
        assert right.saturation() > left.saturation()

    def test_hsl_luminance_track_changes_only_lightness(self):
        from PySide6.QtGui import QColor

        from arw_selector.gui.widgets import hsl_band_colors

        left, right = (QColor(c) for c in hsl_band_colors(0, "luminance"))

        # HSV -> RGB -> HSV 왕복에서 몇 단위는 어긋납니다. 의도적으로 채도를
        # 함께 움직였는지만 보면 되므로 여유를 둡니다.
        assert abs(left.saturation() - right.saturation()) <= 5, "채도가 함께 변합니다"
        assert right.value() > left.value()

    def test_saturation_track_is_not_a_single_hue(self):
        """채도는 '색이 진해진다'는 뜻이지 빨개진다는 뜻이 아닙니다."""
        from PySide6.QtGui import QColor

        from arw_selector.gui.widgets import GRADIENTS

        hues = {QColor(c).hue() for c in GRADIENTS["saturation"][1:]}

        assert len(hues) > 1, "한 색으로만 끝나면 그 색으로 물드는 것처럼 보입니다"


class TestQueuePresetKeepsFraming:
    """프리셋에 딸려 온 크롭·마스크가 다른 컷을 덮어쓰면 안 됩니다.

    실측 배경: 사용자 프리셋 '2'에 크롭(0.34~0.75)과 기울이기 -4.3도가
    저장돼 있었습니다. 대기열에 갓 넣은 사진은 보정이 없어서(develop=None)
    이 크롭이 그대로 적용됐고, 프리셋 한 번으로 대기열 전체가 남의 구도로
    잘렸습니다.
    """

    @pytest.fixture
    def panel(self, app, tmp_path):
        from arw_selector.core.develop import DevelopSettings
        from arw_selector.core.develop.settings import (
            BasicSettings,
            GeometrySettings,
        )
        from arw_selector.core.export_queue import ExportQueue
        from arw_selector.core.presets import PresetStore
        from arw_selector.gui.queue_panel import QueuePanel

        store = PresetStore("develop", root=tmp_path)
        store.ensure_dir()
        store.save(
            "크롭포함",
            DevelopSettings(
                basic=BasicSettings(exposure=0.6),
                geometry=GeometrySettings(
                    crop_left=0.34, crop_top=0.28,
                    crop_right=0.75, crop_bottom=0.74,
                    straighten=-4.3,
                ),
            ).to_dict(),
        )

        panel = QueuePanel(ExportQueue())
        panel.store = store
        return panel

    def _add(self, panel, name="DSC0001.ARW", develop=None):
        from pathlib import Path

        from arw_selector.core.export_queue import QueueEntry
        from arw_selector.core.types import Grade

        panel.queue.entries.append(
            QueueEntry(source=Path(name), develop=develop, grade=Grade.KEEP)
        )

    def test_fresh_entry_does_not_inherit_preset_crop(self, panel):
        """보정이 없던 항목 — 예전에 여기서 크롭이 딸려 왔습니다."""
        self._add(panel)

        panel._assign_preset(0, "크롭포함")

        entry = panel.queue.entries[0]
        assert entry.develop.geometry.is_neutral(), "프리셋의 크롭이 적용됐습니다"
        assert entry.develop.basic.exposure == pytest.approx(0.6), "색보정은 와야 합니다"

    def test_existing_framing_is_preserved(self, panel):
        """이미 잡아 둔 크롭은 프리셋이 덮어쓰면 안 됩니다."""
        from arw_selector.core.develop import DevelopSettings
        from arw_selector.core.develop.settings import GeometrySettings

        mine = GeometrySettings(crop_left=0.1, crop_right=0.9, straighten=2.0)
        self._add(panel, develop=DevelopSettings(geometry=mine))

        panel._assign_preset(0, "크롭포함")

        assert panel.queue.entries[0].develop.geometry == mine


class TestDarkThemeIsLocked:
    """OS가 라이트 모드여도 앱은 다크로 고정되어야 합니다.

    실측 배경: 팔레트를 안 잠갔을 때 라이트 모드 PC에서 앱 글자색 #ddd 가
    OS 바탕 #f3f3f3 위에 그려져 글씨가 보이지 않았습니다.
    """

    @pytest.fixture
    def light_app(self, app):
        from PySide6.QtCore import Qt

        from arw_selector.gui import theme

        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
        theme.apply_app_theme(app)
        yield app
        app.styleHints().setColorScheme(Qt.ColorScheme.Unknown)
        theme.apply_app_theme(app)

    def test_palette_stays_dark_under_light_os(self, light_app):
        from arw_selector.gui import theme

        palette = light_app.palette()

        assert palette.window().color().name() == theme.BACKGROUND
        assert palette.text().color().name() == "#dddddd"

    def test_text_contrasts_with_window(self, light_app):
        """글자와 배경이 붙어 있으면 안 읽힙니다."""
        palette = light_app.palette()
        text = palette.text().color()
        window = palette.window().color()

        assert abs(text.lightness() - window.lightness()) > 80

    def test_progress_bar_chunk_is_green(self, light_app):
        """회색 청크는 어두운 바탕에서 진행 중인지 안 보입니다."""
        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QProgressBar

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(100)
        bar.resize(200, 20)
        bar.show()
        light_app.processEvents()

        image = QImage(bar.size(), QImage.Format_RGB32)
        bar.render(image)
        bar.hide()

        greens = sum(
            1
            for x in range(0, image.width(), 3)
            if (c := image.pixelColor(x, image.height() // 2)).green()
            > max(c.red(), c.blue()) + 30
        )
        assert greens > image.width() // 6, "초록 청크가 그려지지 않았습니다"


class TestExportDialogDevelopToggle:
    """보정본을 안 만들 때 형식·품질·크기가 살아 있으면 안 됩니다.

    켜져 있으면 거기서 고른 JPEG 품질이 내보낼 RAW에도 적용되는 줄 압니다.
    실제로는 원본이 그대로 복사될 뿐입니다.
    """

    @pytest.fixture
    def dialog(self, app):
        from pathlib import Path

        from arw_selector.gui.export_dialog import ExportDialog

        return ExportDialog(Path("out"), {"keep": 1}, develop_count=0)

    def test_image_controls_disabled_when_develop_off(self, dialog):
        dialog.apply_develop.setChecked(False)

        assert not dialog.image_format.isEnabled()
        assert not dialog.quality.isEnabled()
        assert not dialog.resize_mode.isEnabled()

    def test_image_controls_enabled_when_develop_on(self, dialog):
        dialog.apply_develop.setChecked(True)

        assert dialog.image_format.isEnabled()
        assert dialog.quality.isEnabled()
        assert dialog.resize_mode.isEnabled()

    def test_saved_options_sync_enabled_state(self, app):
        """저장된 옵션으로 창이 열릴 때도 맞아야 합니다.

        setChecked는 값이 이미 같으면 toggled를 안 쏘므로, 불러오기 경로에서
        직접 맞춰 주지 않으면 꺼진 채로 컨트롤만 살아 있게 됩니다.
        """
        from pathlib import Path

        from arw_selector.core.export_options import ExportOptions
        from arw_selector.gui.export_dialog import ExportDialog

        dialog = ExportDialog(
            Path("out"),
            {"keep": 1},
            develop_count=0,
            options=ExportOptions(apply_develop=False),
        )

        assert not dialog.quality.isEnabled()
        assert not dialog.image_format.isEnabled()
