"""폴더를 바꾸면 이전 폴더의 썸네일이 실제로 풀리는지.

grid_view에는 "다른 폴더로 갈아탔으면 이전 썸네일은 다시 볼 일이 없습니다.
예전에는 이걸 안 비워서 폴더를 옮길수록 램이 쌓였습니다"라는 주석과 함께
비우는 코드가 있었습니다. **그런데 배선이 안 되어 있었습니다.**

메인 창이 모델의 cache_dir에 직접 대입한 뒤 set_records를 호출해서,
set_records가 비교할 때는 이미 두 값이 같았습니다. 비우는 분기는 한 번도
실행되지 않았습니다. 주석이 있다고 동작하는 것은 아닙니다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from arw_selector.core.types import (  # noqa: E402
    FocusResult, FocusSource, Grade, ImageRecord,
)
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


@pytest.fixture
def window(app):
    from arw_selector.gui.main_window import MainWindow

    win = MainWindow()
    win.show()
    app.processEvents()
    yield win
    win.close()
    app.processEvents()


def _records(folder: Path, count: int = 3) -> list[ImageRecord]:
    out = []
    for index in range(count):
        record = ImageRecord(
            path=folder / f"{index:04d}.ARW",
            focus=FocusResult(
                sharpness=50.0, laplacian=50.0, tenengrad=50.0,
                frame_sharpness=50.0, source=FocusSource.EYE, face_count=1,
                face_area_ratio=0.05, mean_luma=120.0,
            ),
        )
        record.score = 50.0
        record.grade = Grade.REVIEW
        out.append(record)
    return out


def _fill_cache(model, records) -> None:
    """썸네일이 들어찬 상태를 만듭니다."""
    for record in records:
        pixmap = QPixmap(64, 64)
        pixmap.fill()
        key = str(record.path)
        model._pixmaps[key] = pixmap
        model._pixmap_bytes += model._pixmap_size(pixmap)
        model._requested.add(key)


class _Session:
    """분석 세션 대역. 폴더와 레코드만 있으면 됩니다."""

    def __init__(self, folder: Path, records):
        self.folder = folder
        self.records = records


# ------------------------------------------------------- 모델 단위


def test_model_clears_on_a_new_folder(window, tmp_path):
    model = window.grid.model_
    first = _records(tmp_path / "가")
    _fill_cache(model, first)
    assert model._pixmaps

    model.set_records(_records(tmp_path / "나"), tmp_path / "나_캐시")
    assert not model._pixmaps
    assert model._pixmap_bytes == 0


def test_model_keeps_the_cache_for_the_same_folder(window, tmp_path):
    """같은 폴더에서 필터만 바꿀 때 비우면 썸네일을 매번 다시 읽습니다."""
    model = window.grid.model_
    records = _records(tmp_path / "가")
    cache = tmp_path / "가_캐시"
    model.set_records(records, cache)
    _fill_cache(model, records)

    model.set_records(records[:2], cache)
    assert model._pixmaps


# ------------------------------------------------------- 실제 배선


def test_switching_folders_through_the_window_releases_thumbnails(window, tmp_path):
    """메인 창을 통해 폴더를 바꿔도 비워져야 합니다.

    모델만 시험하면 못 잡습니다 — 실제로 못 잡았습니다.
    """
    model = window.grid.model_

    first = tmp_path / "촬영1"
    first.mkdir()
    window.session = _Session(first, _records(first))
    window.apply_filter()
    _fill_cache(model, window.session.records)
    assert model._pixmaps, "먼저 캐시가 차 있어야 의미가 있습니다"

    second = tmp_path / "촬영2"
    second.mkdir()
    window.session = _Session(second, _records(second))
    window.apply_filter()

    assert not model._pixmaps, "폴더를 바꿨는데 이전 썸네일이 남아 있습니다"
    assert model._pixmap_bytes == 0


def test_filtering_within_a_folder_keeps_thumbnails(window, tmp_path):
    """등급 필터를 누를 때마다 썸네일을 다시 읽으면 안 됩니다."""
    model = window.grid.model_
    folder = tmp_path / "촬영3"
    folder.mkdir()
    window.session = _Session(folder, _records(folder, 5))
    window.apply_filter()
    _fill_cache(model, window.session.records)

    window.apply_filter()
    assert model._pixmaps


def test_records_are_not_retained_after_switching(window, tmp_path):
    """이전 폴더의 레코드를 격자가 계속 붙들고 있으면 안 됩니다."""
    folder = tmp_path / "촬영4"
    folder.mkdir()
    old = _records(folder, 4)
    window.session = _Session(folder, old)
    window.apply_filter()

    other = tmp_path / "촬영5"
    other.mkdir()
    window.session = _Session(other, _records(other, 2))
    window.apply_filter()

    held = window.grid.model_._records
    assert len(held) == 2
    assert all(r.path.parent == other for r in held)
