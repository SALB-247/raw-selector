"""램 사용을 묶어 두는 장치들의 테스트.

저사양 PC(4코어/8GB)에서 문제가 된 두 곳입니다:
  - 썸네일 캐시가 무제한이라 스크롤할수록 램이 쌓였습니다
  - 워커 수를 코어 수만 보고 정해 램이 모자라면 스왑이 걸렸습니다
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from arw_selector.core import pipeline


class TestResolveWorkers:
    """코어·램·실측 이득, 셋 중 가장 빡빡한 것을 따릅니다."""

    @pytest.fixture
    def fake_env(self, monkeypatch):
        def setup(cores: int, available_mb: int | None):
            monkeypatch.setattr(os, "cpu_count", lambda: cores)
            monkeypatch.setattr(
                pipeline, "_available_memory_mb", lambda: available_mb
            )
        return setup

    def test_explicit_request_wins(self, fake_env):
        """사용자가 직접 정했으면 그대로 씁니다."""
        fake_env(cores=4, available_mb=500)
        assert pipeline.resolve_workers(9) == 9

    def test_low_memory_reduces_workers(self, fake_env):
        """램이 모자라면 코어가 남아도 줄입니다. 스왑이 걸리면 더 느립니다."""
        fake_env(cores=8, available_mb=800)
        low = pipeline.resolve_workers(None)

        fake_env(cores=8, available_mb=11000)
        high = pipeline.resolve_workers(None)

        assert low < high
        assert low >= 1, "0개가 되면 아무것도 못 합니다"

    def test_capped_at_useful_limit(self, fake_env):
        """실측상 6개 이상은 거의 안 빨라집니다. 램만 더 씁니다."""
        fake_env(cores=64, available_mb=100000)

        assert pipeline.resolve_workers(None) == pipeline.MAX_USEFUL_WORKERS

    def test_leaves_a_core_for_the_ui(self, fake_env):
        fake_env(cores=4, available_mb=100000)

        assert pipeline.resolve_workers(None) == 3

    def test_unknown_memory_falls_back_to_cores(self, fake_env):
        """램을 못 읽는 환경에서도 동작해야 합니다."""
        fake_env(cores=4, available_mb=None)

        assert pipeline.resolve_workers(None) == 3

    def test_never_returns_zero(self, fake_env):
        fake_env(cores=1, available_mb=1)

        assert pipeline.resolve_workers(None) >= 1


pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from arw_selector.gui import grid_view  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class TestThumbnailCacheBound:
    """스크롤하며 지나간 썸네일이 영원히 남으면 안 됩니다."""

    @pytest.fixture
    def model(self, app):
        return grid_view.RecordListModel(Path("."))

    def _fill(self, model, count: int, size: int = 320) -> None:
        image = QImage(size, int(size * 2 / 3), QImage.Format_RGB32)
        image.fill(0x202020)
        for index in range(count):
            model._on_thumbnail(f"C:/photos/DSC{index:05d}.ARW", image)

    def test_cache_stays_under_limit(self, model):
        self._fill(model, 3000)

        assert model._pixmap_bytes <= grid_view.THUMBNAIL_CACHE_BYTES

    def test_keeps_at_least_a_screenful(self, model):
        self._fill(model, 3000)

        assert len(model._pixmaps) >= grid_view.MIN_CACHED_THUMBNAILS

    def test_evicted_entries_can_be_requested_again(self, model):
        """버린 항목을 _requested에 남겨 두면 영영 다시 안 그려집니다."""
        self._fill(model, 3000)
        first = "C:/photos/DSC00000.ARW"

        assert first not in model._pixmaps, "가장 오래된 것이 남아 있습니다"
        assert first not in model._requested

    def test_recently_used_survives(self, model):
        """최근에 본 것은 남아 있어야 스크롤이 끊기지 않습니다."""
        self._fill(model, 3000)
        newest = "C:/photos/DSC02999.ARW"

        assert newest in model._pixmaps

    def test_switching_folder_clears_cache(self, model):
        self._fill(model, 200)
        assert model._pixmaps

        model.set_records([], cache_dir=Path("다른폴더"))

        assert not model._pixmaps
        assert model._pixmap_bytes == 0

    def test_same_folder_keeps_cache(self, model):
        """같은 폴더를 다시 그릴 때까지 버리면 스크롤이 매번 깜빡입니다."""
        self._fill(model, 50)
        before = len(model._pixmaps)

        model.set_records([], cache_dir=Path("."))

        assert len(model._pixmaps) == before

    def test_replacing_one_entry_does_not_double_count(self, model):
        """같은 파일을 다시 받으면 용량을 두 번 더하면 안 됩니다."""
        self._fill(model, 1)
        once = model._pixmap_bytes

        self._fill(model, 1)

        assert model._pixmap_bytes == once
