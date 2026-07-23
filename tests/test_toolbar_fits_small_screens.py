"""툴바가 작은 화면에서 잘리지 않아야 합니다.

실측 배경: 툴바가 한 줄 QHBoxLayout이라 그 줄의 폭이 창의 하한이 되었고,
Qt는 레이아웃 최소값 아래로 창을 줄이지 않습니다. 실제 인터페이스
폰트(맑은 고딕 9pt)로 재니 한국어 1366px, **영어 1547px**이었습니다.

애플 실리콘 맥북에서 가장 낮은 기본 Retina 해상도가 1440x900 points
(13" MacBook Air, M1)입니다. 영어 화면이 여기 안 들어갑니다. 맥의 기본
UI 폰트는 13pt라 더 나쁩니다.

그래서 목표는 **1440x806**(메뉴 막대와 Dock을 뺀 실사용)입니다.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QRect, QSize, Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractButton,
    QComboBox,
    QLabel,
    QPushButton,
    QSlider,
    QWidget,
)

from conftest import destroy_all_widgets, destroy_widget  # noqa: E402

#: 맞춰야 하는 화면 — 13" MacBook Air (M1) 기본 Retina에서 메뉴 막대와
#: Dock을 뺀 크기입니다.
SMALLEST_SCREEN = QSize(1440, 806)


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    from arw_selector.gui import theme

    instance = QApplication.instance() or QApplication([])
    theme.apply_app_theme(instance)
    yield instance
    destroy_all_widgets(instance)


@pytest.fixture
def window(app):
    from arw_selector.gui.main_window import MainWindow

    widget = MainWindow()
    yield widget
    destroy_widget(widget, app)


def _escapees(root: QWidget) -> list[str]:
    """창 밖으로 나가 손이 닿지 않는 조작 위젯."""
    lost = []
    for kind in (QAbstractButton, QComboBox, QSlider):
        for child in root.findChildren(kind):
            if not child.isVisibleTo(root):
                continue
            corner = child.mapTo(root, child.rect().topLeft())
            if corner.x() + child.width() > root.width() + 1:
                text = child.text() if hasattr(child, "text") else ""
                lost.append(f"{type(child).__name__}({text!r})")
    return lost


# ------------------------------------------------------- 창 자체


def test_the_window_can_shrink_to_a_macbook(window, app):
    """창이 요청한 크기로 실제로 줄어들어야 합니다.

    레이아웃 최소값이 화면보다 크면 Qt가 요청을 무시합니다. 그러면
    창 관리자가 강제로 줄이면서 오른쪽이 잘려 나갑니다.
    """
    window.resize(SMALLEST_SCREEN)
    window.show()
    app.processEvents()

    assert window.width() <= SMALLEST_SCREEN.width(), (
        f"1440을 요청했는데 {window.width()}로 벌어졌습니다 — "
        f"레이아웃 최소 폭 {window.minimumSizeHint().width()}"
    )


def test_nothing_is_pushed_off_the_right_edge(window, app):
    window.resize(SMALLEST_SCREEN)
    window.show()
    app.processEvents()

    lost = _escapees(window)
    assert not lost, f"창 밖으로 나간 조작 위젯: {lost}"


def test_the_floor_is_below_any_screen_in_use(window):
    """1024px보다 낮은 화면을 쓰는 곳은 이제 없습니다 — 거기까지만.

    폭 자체를 하한으로 못 박지 않는 것이 요점입니다. 툴바가 접히면
    최소 폭은 '가장 넓은 버튼 하나'가 됩니다.
    """
    assert window.minimumSizeHint().width() <= 1024


# ------------------------------------------------------- 접히는 방식


def test_the_toolbar_wraps_instead_of_widening(window, app):
    """좁아지면 두 줄이 되어야 합니다. 한 줄로 버티면 창이 안 줄어듭니다.

    절대 px로 기준을 잡으면 폰트에 따라 결과가 달라집니다(테스트가 쓰는
    offscreen 폰트는 실제보다 훨씬 넓습니다). 툴바가 스스로 말하는
    '한 줄 폭'을 기준으로 삼습니다.
    """
    bar = window.findChild(QWidget, "toolbar")
    assert bar is not None
    one_row = bar.sizeHint().width()

    window.show()
    window.resize(one_row + 100, 806)
    app.processEvents()
    wide = bar.height()

    window.resize(one_row // 2, 806)
    app.processEvents()
    narrow = bar.height()

    assert narrow > wide, (
        f"폭을 절반({one_row // 2}px)으로 줄여도 툴바가 {narrow}px 그대로입니다"
    )


def test_a_wide_window_keeps_one_row(window, app):
    """넓을 때까지 두 줄이면 자리만 낭비합니다."""
    bar = window.findChild(QWidget, "toolbar")

    window.show()
    window.resize(bar.sizeHint().width() + 100, 806)
    app.processEvents()

    tallest = max(
        (child.height() for child in bar.findChildren(QPushButton)), default=0
    )
    assert bar.height() <= tallest * 2, (
        f"넓은 창에서도 툴바가 {bar.height()}px로 여러 줄입니다"
    )


# ------------------------------------------------------- FlowLayout 자체


def _flow(items: list[QWidget], width: int):
    from arw_selector.gui.flow_layout import FlowLayout

    host = QWidget()
    layout = FlowLayout(host, spacing=6)
    for item in items:
        layout.addWidget(item)
    host.resize(width, layout.heightForWidth(width))
    layout.setGeometry(QRect(QPoint(0, 0), host.size()))
    return host, layout


def test_flow_layout_wraps(app):
    boxes = []
    for _ in range(4):
        box = QLabel("x")
        box.setFixedSize(100, 20)
        boxes.append(box)

    host, _ = _flow(boxes, 250)
    rows = {box.y() for box in boxes}
    assert len(rows) == 2, f"250px에 100px짜리 4개가 {len(rows)}줄로 놓였습니다"
    host.deleteLater()


def test_flow_layout_centres_items_in_their_row(app):
    """라벨과 버튼이 섞여 있어 위로 붙으면 어긋나 보입니다."""
    tall = QLabel("tall")
    tall.setFixedSize(50, 40)
    short = QLabel("short")
    short.setFixedSize(50, 20)

    host, _ = _flow([tall, short], 400)
    assert short.y() > tall.y(), "낮은 위젯이 줄 안에서 가운데로 오지 않았습니다"
    assert short.y() + short.height() < tall.y() + tall.height()
    host.deleteLater()


def test_a_group_gap_does_not_start_a_row(app):
    """줄이 넘어간 자리에서 시작하는 간격은 이상한 들여쓰기로 보입니다."""
    from arw_selector.gui.flow_layout import FlowLayout

    host = QWidget()
    layout = FlowLayout(host, spacing=6)
    first = QLabel("a")
    first.setFixedSize(100, 20)
    layout.addWidget(first)
    layout.addSpacing(40)
    second = QLabel("b")
    second.setFixedSize(100, 20)
    layout.addWidget(second)

    host.resize(150, layout.heightForWidth(150))
    layout.setGeometry(QRect(QPoint(0, 0), host.size()))

    assert second.y() > first.y(), "두 번째 위젯이 줄을 넘기지 않았습니다"
    assert second.x() == first.x(), (
        f"간격이 줄 앞에 남아 {second.x() - first.x()}px 밀렸습니다"
    )
    host.deleteLater()


def test_stretch_is_accepted_and_ignored(app):
    """QHBoxLayout에서 옮겨 오는 코드가 그대로 돌아야 합니다."""
    from arw_selector.gui.flow_layout import FlowLayout

    host = QWidget()
    layout = FlowLayout(host)
    layout.addStretch(1)
    assert layout.count() == 0
    host.deleteLater()
