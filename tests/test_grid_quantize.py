"""격자 칸 크기 양자화.

예전에는 슬라이더 값을 칸 폭으로 그대로 썼습니다. 뷰포트 폭이 칸 폭의
배수가 아니면 오른쪽에 한 칸이 안 되는 자투리가 그냥 버려집니다 — 폭
1200px에 칸 190px이면 6칸 1140px에 60px이 낭비됩니다.

우측에 판정 기준·대기열 패널이 나타나면 격자 폭이 달라지므로, **그때도
다시 맞춰지는지**가 핵심입니다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from arw_selector.core.types import Grade, ImageRecord  # noqa: E402
from arw_selector.gui.grid_view import ThumbnailGrid  # noqa: E402
from conftest import destroy_all_widgets  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from arw_selector.gui import theme

    instance = QApplication.instance() or QApplication([])
    theme.apply_app_theme(instance)
    yield instance
    destroy_all_widgets(instance)


@pytest.fixture
def grid(app, tmp_path):
    widget = ThumbnailGrid(tmp_path)
    records = []
    for i in range(60):
        record = ImageRecord(path=Path(f"DSC{i:05d}.ARW"))
        record.grade = Grade.KEEP
        records.append(record)
    widget.set_records(records, tmp_path)
    widget.resize(1200, 800)
    # **show()가 필요합니다.** 띄우지 않은 위젯은 resize()를 불러도 뷰포트
    # 크기가 갱신되지 않아, 폭을 바꿔 가며 재는 테스트가 전부 같은 폭을
    # 재게 됩니다(그래서 '폭을 넓혔는데 열이 안 늘었다'는 거짓 실패가 났습니다).
    widget.show()
    QApplication.processEvents()
    yield widget
    widget.close()


def _resize(grid, width: int, height: int = 800) -> None:
    """크기를 바꾸고 **레이아웃이 실제로 반영될 때까지** 기다립니다.

    `resize()`만 부르면 뷰포트 폭이 즉시 갱신되지 않습니다. 그 상태로 재면
    바뀌기 전 폭으로 계산하게 되어, 기능이 멀쩡한데도 테스트가 실패합니다
    (실제로 그렇게 두 번 헛짚었습니다).
    """
    grid.resize(width, height)
    QApplication.processEvents()
    grid._apply_thumb_size()


def _resize_like_the_app(grid, width: int, height: int = 800) -> None:
    """크기만 바꾸고 **격자가 스스로 맞추도록** 둡니다.

    `_resize`는 `_apply_thumb_size()`를 손으로 한 번 더 부릅니다. 그게
    실제 버그를 가렸습니다 — resizeEvent 안에서 읽는 viewport().width()가
    아직 예전 값이라, 앱에서는 우측 패널을 열면 격자가 좁아졌는데도 열 수가
    그대로 남아 오른쪽이 텅 비었습니다. 손으로 다시 부르면 그때는 폭이
    갱신되어 있어 테스트만 통과했습니다.

    여기서는 앱과 똑같이 resize만 하고 이벤트 루프를 돌립니다.
    """
    grid.resize(width, height)
    for _ in range(4):
        QApplication.processEvents()


def _leftover(grid) -> int:
    """오른쪽에 남는 자투리 픽셀."""
    cell = grid.gridSize().width()
    return grid.viewport().width() - grid.columns() * cell


# ------------------------------------------------------- 자투리


@pytest.mark.parametrize("width", [800, 1000, 1200, 1367, 1440, 1920])
@pytest.mark.parametrize("desired", [90, 140, 180, 250, 360])
def test_no_wasted_margin(grid, width, desired):
    """어떤 폭·어떤 희망 크기에서도 자투리가 한 칸보다 훨씬 작아야 합니다."""
    _resize(grid, width)
    grid.set_thumb_size(desired)

    leftover = _leftover(grid)
    assert 0 <= leftover < grid.columns() + 2, (
        f"폭 {width}, 희망 {desired}: 자투리 {leftover}px "
        f"({grid.columns()}칸 × {grid.gridSize().width()}px)"
    )


def test_cells_fill_the_row(grid):
    """칸 폭 × 칸 수가 뷰포트 폭과 거의 같아야 합니다."""
    _resize(grid, 1200)
    grid.set_thumb_size(190)
    used = grid.columns() * grid.gridSize().width()
    assert used >= grid.viewport().width() - grid.columns()


# ------------------------------------------------------- 희망 크기 존중


def test_bigger_request_gives_fewer_columns(grid):
    _resize(grid, 1200)
    grid.set_thumb_size(100)
    many = grid.columns()
    grid.set_thumb_size(300)
    few = grid.columns()
    assert few < many


def test_actual_size_stays_near_the_request(grid):
    """양자화가 크기를 크게 바꾸면 슬라이더가 제 역할을 못 합니다."""
    _resize(grid, 1200)
    for desired in (120, 180, 240, 300):
        grid.set_thumb_size(desired)
        actual = grid.delegate.thumb_size
        # 한 칸 늘리거나 줄이는 폭 안에서만 움직여야 합니다
        assert abs(actual - desired) <= desired / max(1, grid.columns()) + 4, (
            f"희망 {desired} → 실제 {actual} ({grid.columns()}칸)")


# ------------------------------------------------------- 패널이 생겼을 때


def test_relayouts_when_the_view_narrows(grid):
    """우측 패널이 열리면 격자가 좁아집니다 — 그때 다시 맞춰야 합니다."""
    _resize(grid, 1400)
    grid.set_thumb_size(180)
    wide_cell = grid.gridSize().width()
    assert _leftover(grid) < grid.columns() + 2

    # 판정 기준 패널이 열려 격자가 320px 좁아진 상황
    _resize(grid, 1080)
    assert _leftover(grid) < grid.columns() + 2, "패널이 열린 뒤 자투리가 남습니다"
    assert grid.gridSize().width() != wide_cell or grid.columns() >= 1


def test_relayouts_when_the_view_widens(grid):
    _resize(grid, 900)
    grid.set_thumb_size(180)
    narrow = grid.columns()
    _resize(grid, 1600)
    assert grid.columns() > narrow
    assert _leftover(grid) < grid.columns() + 2


def test_extremely_narrow_view_keeps_one_column(grid):
    """패널을 다 열어 격자가 아주 좁아져도 한 칸은 남아야 합니다."""
    _resize(grid, 140)
    grid.set_thumb_size(360)
    assert grid.columns() >= 1
    assert grid.delegate.thumb_size >= 40


def test_zero_width_does_not_crash(grid):
    """레이아웃 도중 폭이 0인 순간이 있습니다."""
    _resize(grid, 0)
    grid.set_thumb_size(180)  # 예외 없이 넘어가면 됩니다


# ------------------------------------------------------- 안정성


def test_side_panels_opening_leaves_no_margin(grid):
    """판정 기준 + 대기열을 **둘 다** 열면 격자가 절반 이하로 좁아집니다.

    실제 리포트: 두 패널을 다 연 상태에서 크기를 최대로 두면 한 열만 남고
    오른쪽에 그 열만큼의 빈 공간이 생겼습니다.
    """
    _resize_like_the_app(grid, 1660, 900)   # 패널 둘 다 닫힘
    grid.set_thumb_size(360)                # 최대 크기
    assert _leftover(grid) < grid.columns() + 2

    # 판정 기준(약 380px) + 대기열(약 520px)이 함께 열린 상태
    _resize_like_the_app(grid, 1660 - 380 - 520, 900)

    leftover = _leftover(grid)
    assert leftover < grid.columns() + 2, (
        f"패널을 열자 오른쪽에 {leftover}px이 남습니다 "
        f"({grid.columns()}열 × {grid.gridSize().width()}px, "
        f"뷰포트 {grid.viewport().width()}px)")


def test_thumb_and_cell_stay_consistent(grid):
    """칸 폭과 썸네일 크기가 따로 놀면 안 됩니다.

    둘이 어긋나면 칸은 넓은데 그림만 작게 그려져, 화면에는 여백으로 보입니다.
    """
    for width in (1660, 760, 1200, 700):
        _resize_like_the_app(grid, width, 900)
        grid.set_thumb_size(360)
        expected = grid.gridSize().width() - grid.delegate.PADDING - grid.CELL_GAP * 2
        assert grid.delegate.thumb_size == max(40, expected), (
            f"폭 {width}: 칸 {grid.gridSize().width()}px 인데 "
            f"썸네일 {grid.delegate.thumb_size}px")


def test_scrollbar_cannot_toggle_the_layout(grid):
    """세로 스크롤바가 켜졌다 꺼졌다 하면 열 수가 진동합니다.

    실제로 겪은 고리: 스크롤바가 사라짐 → 뷰포트가 넓어짐 → 열이 하나 늘어남
    → 칸이 작아져 전체 높이가 줄어듦 → 스크롤바가 또 필요 없어짐… 최대
    크기에서 2열↔3열로 계속 떨렸습니다.

    스크롤바를 항상 켜 두면 뷰포트 폭이 내용과 무관해져 고리가 끊깁니다.
    """
    from PySide6.QtCore import Qt as _Qt

    assert grid.verticalScrollBarPolicy() == _Qt.ScrollBarAlwaysOn


def test_column_count_is_stable_across_relayouts(grid):
    """장수를 바꿔 가며 레이아웃을 반복해도 열 수가 흔들리면 안 됩니다.

    **주의: 이 테스트는 스크롤바 진동 버그를 재현하지 못합니다.** 변이
    테스트로 확인했습니다 — 스크롤바 정책을 AsNeeded로 되돌려도 이 테스트는
    통과합니다. 오프스크린 플랫폼에서는 스크롤바가 실제로 나타났다 사라지며
    뷰포트 폭을 바꾸는 동작이 재현되지 않습니다.

    그 버그를 잠그는 것은 위의 `test_scrollbar_cannot_toggle_the_layout`
    (정책 자체를 단언)입니다. 이 테스트는 그와 별개로 '같은 폭에서 반복
    호출이 수렴하는가'만 봅니다.
    """
    from arw_selector.core.types import Grade, ImageRecord

    _resize(grid, 1374, 900)  # 리포트에 찍힌 실제 창 폭
    grid.set_thumb_size(360)  # 최대 크기 — 여기서 났습니다

    for count in (2, 3, 4, 6, 9, 12):
        records = []
        for i in range(count):
            record = ImageRecord(path=Path(f"DSC{i:05d}.NEF"))
            record.grade = Grade.KEEP
            records.append(record)
        grid.set_records(records)
        QApplication.processEvents()

        seen = set()
        for _ in range(6):
            grid._apply_thumb_size()
            QApplication.processEvents()
            seen.add(grid.columns())
        assert len(seen) == 1, f"{count}장에서 열 수가 진동합니다: {sorted(seen)}"


def test_repeated_resize_settles(grid):
    """같은 폭으로 여러 번 불러도 값이 흔들리면 안 됩니다.

    스크롤바가 생겼다 사라졌다 하며 진동하면 화면이 떨립니다.
    """
    _resize(grid, 1200)
    grid.set_thumb_size(180)
    first = (grid.gridSize().width(), grid.delegate.thumb_size)
    for _ in range(5):
        grid._apply_thumb_size()
    assert (grid.gridSize().width(), grid.delegate.thumb_size) == first
