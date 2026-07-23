"""루페의 표시 항목 체크박스와 얼굴 클릭 전환 — 실제 위젯으로 확인합니다.

체크박스가 '있다'가 아니라 **끄면 실제로 안 그려지는가**를 봅니다. 예전에
초점 영역 하나로 전부 묶여 있어서, 초점만 보려 해도 얼굴 상자가 같이 나와
정작 초점을 볼 수 없었습니다.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from arw_selector.core.focus import FocusSource  # noqa: E402
from arw_selector.core.types import FocusResult, ImageRecord  # noqa: E402
from arw_selector.gui.image_view import ImageView  # noqa: E402
from arw_selector.gui.loupe import LoupeDialog  # noqa: E402
from conftest import destroy_all_widgets  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from arw_selector.gui import theme

    instance = QApplication.instance() or QApplication([])
    theme.apply_app_theme(instance)
    yield instance
    destroy_all_widgets(instance)


def _record() -> ImageRecord:
    return ImageRecord(
        path=Path("558A8911.CR3"),
        focus=FocusResult(
            sharpness=60.0, laplacian=60.0, tenengrad=60.0,
            source=FocusSource.FACE,
            roi=(300, 300, 120, 120),
            faces=((100, 100, 200, 220), (600, 150, 90, 100)),
            face_scores=(0.99, 0.95),
            main_face=0,
            source_width=1000,
            source_height=800,
        ),
    )


@pytest.fixture
def dialog(app):
    widget = LoupeDialog(_record(), fast=True)
    widget._roi_reference_width = 1000
    yield widget
    widget.close()


def _draw(dialog) -> np.ndarray:
    """1000×800 캔버스에 지금 켜진 표시 항목을 그립니다."""
    return dialog._draw_roi(np.zeros((800, 1000, 3), np.uint8))


def _painted(image: np.ndarray) -> int:
    return int(np.count_nonzero(image.any(axis=2)))


# ------------------------------------------------------- 항목별로 꺼지는가


def test_each_overlay_can_be_turned_off_alone(dialog):
    dialog.show_roi.setChecked(True)
    dialog.show_faces.setChecked(True)
    dialog.show_eyes.setChecked(False)
    both = _painted(_draw(dialog))

    dialog.show_faces.setChecked(False)
    roi_only = _painted(_draw(dialog))

    dialog.show_roi.setChecked(False)
    dialog.show_faces.setChecked(True)
    faces_only = _painted(_draw(dialog))

    assert roi_only > 0 and faces_only > 0
    assert both > roi_only and both > faces_only


def test_nothing_is_drawn_when_all_are_off(dialog):
    dialog.show_roi.setChecked(False)
    dialog.show_faces.setChecked(False)
    dialog.show_eyes.setChecked(False)

    assert not dialog._any_overlay_on()
    assert _painted(_draw(dialog)) == 0


def test_shortcuts_exist_for_each_item(dialog):
    """F·A·E가 각각 붙어 있어야 합니다 — 마우스로만 끄면 손이 멉니다."""
    keys = {action.shortcut().toString() for action in dialog.actions()}
    assert {"F", "A", "E"} <= keys


# ------------------------------------------------------- 얼굴 클릭 전환


def test_clicking_a_face_switches_the_main_subject(dialog, monkeypatch):
    """작은 쪽 얼굴을 누르면 그 얼굴이 주 피사체가 되어야 합니다."""
    called = {}

    def fake_set(index: int) -> None:
        called["index"] = index

    monkeypatch.setattr(dialog, "set_main_face", fake_set)
    dialog.show_faces.setChecked(True)

    # 1번 얼굴 (600,150,90,100)의 중심 → 정규화
    dialog._on_preview_clicked((600 + 45) / 1000, (150 + 50) / 800)
    assert called.get("index") == 1


def test_clicking_the_current_main_face_does_nothing(dialog, monkeypatch):
    called = {}
    monkeypatch.setattr(dialog, "set_main_face",
                        lambda index: called.setdefault("index", index))
    dialog.show_faces.setChecked(True)

    dialog._on_preview_clicked((100 + 100) / 1000, (100 + 110) / 800)
    assert "index" not in called


def test_clicking_empty_space_does_nothing(dialog, monkeypatch):
    called = {}
    monkeypatch.setattr(dialog, "set_main_face",
                        lambda index: called.setdefault("index", index))
    dialog.show_faces.setChecked(True)

    dialog._on_preview_clicked(0.95, 0.95)
    assert "index" not in called


def test_click_is_ignored_when_faces_are_hidden(dialog, monkeypatch):
    """상자가 안 보이는데 클릭이 무언가를 바꾸면 놀랍기만 합니다."""
    called = {}
    monkeypatch.setattr(dialog, "set_main_face",
                        lambda index: called.setdefault("index", index))
    dialog.show_faces.setChecked(False)

    dialog._on_preview_clicked((600 + 45) / 1000, (150 + 50) / 800)
    assert "index" not in called


# ------------------------------------------------------- 클릭 vs 드래그


class _Event:
    def __init__(self, point: QPoint):
        self._point = point

    def position(self):
        return self

    def toPoint(self) -> QPoint:
        return self._point

    def button(self):
        return Qt.LeftButton


def test_drag_does_not_count_as_a_click(app):
    """화면을 옮기려다 주 피사체가 바뀌면 안 됩니다."""
    from PySide6.QtGui import QPixmap

    view = ImageView()
    view.resize(400, 300)
    view.set_pixmap(QPixmap(400, 300))
    seen = []
    view.clicked.connect(lambda x, y: seen.append((x, y)))

    view.mousePressEvent(_Event(QPoint(200, 150)))
    view.mouseReleaseEvent(_Event(QPoint(260, 190)))
    assert seen == []

    view.mousePressEvent(_Event(QPoint(200, 150)))
    view.mouseReleaseEvent(_Event(QPoint(201, 151)))
    assert len(seen) == 1
    view.close()
