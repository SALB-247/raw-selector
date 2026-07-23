"""점멸할 때 버튼 크기가 흔들리지 않는지.

점멸 상태가 여백을 12px로, 글자를 굵게 잡고 있어서 깜빡일 때마다 버튼이
넓어졌다 좁아졌다 했습니다. 옆 버튼들까지 밀려서 툴바 전체가 들썩입니다.

크기는 눈으로 확인할 수 없습니다 — 재야 압니다.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QHBoxLayout, QPushButton, QWidget  # noqa: E402

from arw_selector.gui import theme  # noqa: E402
from arw_selector.gui.attention import ButtonPulse  # noqa: E402
from conftest import destroy_all_widgets, destroy_widget  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    theme.apply_app_theme(instance)
    yield instance
    destroy_all_widgets(instance)


@pytest.fixture
def button(app):
    widget = QPushButton("폴더 열기")
    widget.setStyleSheet(theme.BUTTON)
    widget.show()
    app.processEvents()
    yield widget
    destroy_widget(widget, app)


def _size(widget, app):
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.adjustSize()
    app.processEvents()
    return widget.sizeHint()


def test_attention_style_has_the_same_box_as_a_normal_button(button, app):
    """색만 달라야 합니다. 여백이나 글자 굵기가 다르면 폭이 바뀝니다."""
    normal = _size(button, app)

    button.setStyleSheet(theme.ATTENTION_BUTTON)
    highlighted = _size(button, app)

    assert highlighted == normal, (
        f"점멸 상태에서 크기가 바뀝니다: {normal} → {highlighted}")


def test_pulsing_never_changes_the_width(button, app):
    """켜짐/꺼짐을 오가는 동안 폭이 한 번도 변하지 않아야 합니다."""
    pulse = ButtonPulse(button)
    widths = {button.sizeHint().width()}

    pulse.start(count=4)
    for _ in range(9):
        widths.add(_size(button, app).width())
        pulse._tick()
    widths.add(_size(button, app).width())

    assert len(widths) == 1, f"점멸 중 폭이 여러 값입니다: {sorted(widths)}"


def test_neighbours_do_not_shift(app):
    """옆 버튼이 밀리면 툴바 전체가 들썩입니다."""
    host = QWidget()
    row = QHBoxLayout(host)
    first = QPushButton("폴더 열기")
    second = QPushButton("분석")
    for widget in (first, second):
        widget.setStyleSheet(theme.BUTTON)
        row.addWidget(widget)
    row.addStretch(1)
    host.resize(600, 60)
    host.show()
    app.processEvents()

    try:
        before = second.pos().x()
        first.setStyleSheet(theme.ATTENTION_BUTTON)
        host.layout().activate()
        app.processEvents()
        assert second.pos().x() == before, "점멸 때문에 옆 버튼이 밀렸습니다"
    finally:
        destroy_widget(host, app)


def test_padding_is_shared_between_the_two_styles():
    """두 스타일이 같은 상수를 쓰는지 — 한쪽만 고치는 것을 막습니다."""
    assert theme.BUTTON_PADDING in theme.BUTTON
    assert theme.BUTTON_PADDING in theme.ATTENTION_BUTTON


def test_attention_style_does_not_embolden():
    """굵은 글씨는 같은 여백에서도 글자 폭을 늘립니다."""
    assert "font-weight" not in theme.ATTENTION_BUTTON


def test_toggle_button_keeps_its_size_when_checked(app):
    """판정 기준·대기열 버튼도 켤 때 흔들리면 안 됩니다."""
    widget = QPushButton("판정 기준 ▸")
    widget.setCheckable(True)
    widget.setStyleSheet(theme.TOGGLE_BUTTON)
    widget.show()
    app.processEvents()
    try:
        off = _size(widget, app)
        widget.setChecked(True)
        on = _size(widget, app)
        assert on == off, f"켤 때 크기가 바뀝니다: {off} → {on}"
    finally:
        destroy_widget(widget, app)


def test_horizontal_padding_stays_within_the_width_budget():
    """가로 여백은 보정 창 최소 폭에 직접 얹힙니다.

    실측: 가로 1px당 보정 창이 약 8px 넓어집니다.
        11px → 900px   14px → 922px   16px → 938px   18px → 954px

    맞춰야 하는 화면은 13" MacBook Air (M1)의 기본 Retina, 1440x900
    points입니다. 여기서는 상한만 지킵니다 — 실제로 창이 화면에 들어가는지는
    test_toolbar_fits_small_screens.py 와 test_gui_rendering.py 가 봅니다.
    """
    horizontal = int(theme.BUTTON_PADDING.split()[1].replace("px", ""))
    assert horizontal <= 20, (
        f"좌우 여백 {horizontal}px — 보정 창이 1440px 화면에서 빠듯해집니다")


def test_vertical_padding_is_roomy():
    """세로는 창 폭에 영향이 없습니다. 여기서 여유를 냅니다."""
    vertical = int(theme.BUTTON_PADDING.split()[0].replace("px", ""))
    assert vertical >= 7, f"세로 여백이 {vertical}px로 빠듯합니다"
