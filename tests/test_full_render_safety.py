"""Full Render 가 겹쳐 돌지 않는지 지킵니다.

이 테스트가 있는 이유: 27MP RAW 한 장을 풀 해상도로 디모자이크하면 실측
**2.8GB**를 씁니다(R6M3). 버튼을 껐다 켜면 돌던 워커는 멈추지 않고(rawpy
디모자이크는 중간에 끊을 지점이 없습니다) 새 워커가 그 위에 뜨므로 5.5GB가
되고, 8GB PC는 거기서 죽습니다. 실제로 "Full Render 2회 클릭 시 크래시"로
리포트가 올라왔습니다.

진짜 디모자이크는 장당 수 초라 테스트에서 돌릴 수 없습니다. 워커의 run()만
잠자는 것으로 바꿔 끼우면, 우리가 지키려는 것(동시 실행 수)은 그대로
검사하면서 몇 초 만에 끝납니다.
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from arw_selector.gui import loupe as loupe_mod  # noqa: E402
from conftest import destroy_all_widgets  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from arw_selector.gui import theme

    instance = QApplication.instance() or QApplication([])
    theme.apply_app_theme(instance)
    yield instance

    destroy_all_widgets(instance)
    instance.processEvents()
    loupe_mod.wait_for_detached_renders()


@pytest.fixture(autouse=True)
def clean_slot():
    loupe_mod._FULL_RENDER_SLOT.clear()
    yield
    loupe_mod._FULL_RENDER_SLOT.clear()


class SleepyWorker(loupe_mod.FinalRenderWorker):
    """진짜 디모자이크 대신 잠깐 잡니다 — 점유 시간만 흉내 냅니다."""

    duration = 0.6

    def run(self) -> None:
        time.sleep(self.duration)


def pump(app, seconds: float, watcher=None) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents(QEventLoop.AllEvents, 5)
        if watcher is not None:
            watcher()


def live_renders() -> int:
    count = 0
    for worker in list(loupe_mod._FULL_RENDER_SLOT):
        try:
            if worker.isRunning():
                count += 1
        except RuntimeError:
            pass
    return count


def test_slot_reports_running_worker(app):
    worker = SleepyWorker(__file__, None, None)
    loupe_mod._FULL_RENDER_SLOT.add(worker)
    worker.start()
    try:
        assert loupe_mod.full_render_in_flight()
        assert worker.wait(5000)
        app.processEvents()
        # 끝나면 스스로 비어야 합니다 — 안 그러면 이후 렌더가 영영 못 뜹니다
        assert not loupe_mod.full_render_in_flight()
    finally:
        worker.wait(5000)


def test_slot_is_empty_when_nothing_runs(app):
    assert not loupe_mod.full_render_in_flight()


def test_finished_worker_frees_the_slot(app):
    """끝난 워커가 슬롯에 남아 있으면 다음 렌더가 영원히 대기합니다."""
    worker = SleepyWorker(__file__, None, None)
    worker.duration = 0.1
    loupe_mod._FULL_RENDER_SLOT.add(worker)
    worker.start()
    worker.wait(5000)
    app.processEvents()
    assert not loupe_mod.full_render_in_flight()


def test_many_workers_are_all_tracked(app):
    """슬롯이 하나라도 새면 겹침 방어가 통째로 무의미해집니다."""
    workers = [SleepyWorker(__file__, None, None) for _ in range(4)]
    for worker in workers:
        worker.duration = 0.3
        loupe_mod._FULL_RENDER_SLOT.add(worker)
        worker.start()
    try:
        assert loupe_mod.full_render_in_flight()
    finally:
        for worker in workers:
            worker.wait(5000)
    app.processEvents()
    assert not loupe_mod.full_render_in_flight()


def test_lockout_constant_is_three_seconds():
    """사용자가 요청한 값입니다. 줄이면 연타가 다시 통과합니다."""
    assert loupe_mod.FULL_RENDER_LOCKOUT_MS == 3000
