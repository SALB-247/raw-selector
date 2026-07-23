"""하단 작업 표시줄과 중단 버튼의 회귀 테스트.

이 줄은 오래 걸리는 작업 중에 사용자가 보는 유일한 창구입니다. 켜지고
꺼지는 규칙이 한 군데라도 어긋나면, 중단 버튼이 안 보이거나 작업이 끝났는데
진행바가 남아 "멈춘 것 같은" 화면이 됩니다.

실제 분석을 돌리지 않고 워커 자리에 가짜를 끼웁니다 — 여기서 검사하는 건
파이프라인이 아니라 UI 상태 전이입니다.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from arw_selector.gui.i18n import tr  # noqa: E402
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


class FakeWorker:
    """도는 척만 하는 워커. 중단 요청이 몇 번 왔는지 셉니다."""

    def __init__(self):
        self.cancelled = 0
        self.running = True

    def isRunning(self):
        return self.running

    def cancel(self):
        self.cancelled += 1

    def is_cancelled(self):
        return self.cancelled > 0

    def wait(self, timeout_ms=0):
        """창을 닫을 때 _shutdown_workers 가 부릅니다."""
        self.running = False
        return True


def test_starts_quiet(window):
    """폴더를 열기 전에는 진행바도 중단 버튼도 없습니다."""
    assert not window.status_progress.isVisible()
    assert not window.stop_button.isVisible()
    assert not window.status_eta.isVisible()
    assert window.status_label.text()


def test_old_top_progress_bar_is_gone(window):
    """진행 표시는 한 곳에만 있어야 합니다.

    예전에는 창 위쪽에 진행바가, 툴바 끝에 중단 버튼이 따로 있었습니다.
    작업 중에 눈이 두 군데를 오갔고, 중단을 어디서 누르는지 헷갈렸습니다.
    """
    assert not hasattr(window, "progress")
    assert not hasattr(window, "cancel_button")


def test_task_shows_and_hides_controls(window, app):
    window._begin_task("작업 중")
    app.processEvents()
    assert window.status_progress.isVisible()
    assert window.stop_button.isVisible()
    assert window.stop_button.isEnabled()
    # 총량을 모르는 동안은 불확정(min == max)으로 둡니다
    assert window.status_progress.maximum() == 0

    window._end_task()
    app.processEvents()
    assert not window.status_progress.isVisible()
    assert not window.stop_button.isVisible()
    assert not window.status_eta.isVisible()
    assert window.stop_button.text() == tr("Stop")
    assert window.stop_button.isEnabled()


def test_progress_updates_bar_and_eta(window, app):
    class Progress:
        done, total, cached, failed = 40, 200, 3, 1
        eta_seconds = 130.0

    window._begin_task("분석 준비")
    window.on_progress(Progress())
    app.processEvents()

    assert window.status_progress.maximum() == 200
    assert window.status_progress.value() == 40
    assert window.status_eta.isVisible()
    assert window.status_eta.text() == tr("about {value:.0f} min left").format(
        value=130 / 60)
    assert "40/200" in window.status_label.text()
    window._end_task()


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (None, None),
        (0, None),
        (-5, None),
        # 문구는 현재 로케일에서 가져옵니다 — 한국어를 박아 두면 영어
        # 화면에서 실패하고, 영어를 박아 두면 그 반대가 됩니다.
        (12.4, lambda: tr("about {value:.0f}s left").format(value=12.4)),
        (130.0, lambda: tr("about {value:.0f} min left").format(value=130 / 60)),
        (610.0, lambda: tr("about {value:.0f} min left").format(value=610 / 60)),
        (7200.0, lambda: tr("about {value:.1f} h left").format(value=2.0)),
    ],
)
def test_eta_wording(window, seconds, expected):
    """초만 찍어 주면 큰 배치에서 못 읽습니다. 단위가 따라가야 합니다."""
    window._set_eta(seconds)
    if expected is None:
        assert not window.status_eta.isVisible()
    else:
        assert window.status_eta.isVisible()
        assert window.status_eta.text() == expected()


def test_cancel_locks_button_and_reaches_worker(window, app):
    worker = FakeWorker()
    window.analysis_worker = worker
    window._begin_task("분석 중")

    window.cancel_running()
    app.processEvents()

    assert worker.cancelled == 1
    assert not window.stop_button.isEnabled()
    assert window.stop_button.text() == tr("Stopping…")
    assert window.status_label.text() == tr(
        "Stopping analysis — finishing the photo in progress…")

    window.analysis_worker = None
    window._end_task()


def test_cancel_without_work_is_harmless(window, app):
    """도는 게 없을 때 눌러도(Esc 포함) 상태가 흐트러지면 안 됩니다."""
    window.analysis_worker = None
    window.export_worker = None
    for _ in range(20):
        window.cancel_running()
    app.processEvents()

    assert window.stop_button.text() == tr("Stop")
    assert window.stop_button.isEnabled()
    assert not window.stop_button.isVisible()


def test_repeated_cancel_keeps_forwarding(window, app):
    """연타해도 매번 워커에 전달됩니다 — 삼키면 첫 신호를 놓쳤을 때 못 멈춥니다."""
    worker = FakeWorker()
    window.analysis_worker = worker
    window._begin_task("분석 중")

    for _ in range(30):
        window.cancel_running()
    app.processEvents()

    assert worker.cancelled == 30
    window.analysis_worker = None
    window._end_task()


def test_cancelled_analysis_is_labelled_as_partial(window, app):
    """부분 결과를 완료본으로 읽게 두면 안 됩니다.

    4000장 중 300장에서 멈춘 요약이 완료된 것처럼 보이면, 사용자는 그 위에서
    셀렉을 마쳤다고 믿습니다.
    """
    from arw_selector.core.config import Config
    from arw_selector.core.session import SelectionSession

    worker = FakeWorker()
    worker.cancel()  # 중단된 상태로 끝난 워커
    window.analysis_worker = worker

    session = SelectionSession(folder=window.folder or __import__(
        "pathlib").Path.cwd(), config=Config())
    session.records = []
    window.on_analysis_done(session)
    app.processEvents()

    assert window.status_label.text().startswith(tr("Cancelled — results so far: "))
    assert not window.status_progress.isVisible()
    assert not window.stop_button.isVisible()
    window.analysis_worker = None


def test_completed_analysis_is_not_labelled_cancelled(window, app):
    from arw_selector.core.config import Config
    from arw_selector.core.session import SelectionSession

    worker = FakeWorker()  # cancel 안 함
    window.analysis_worker = worker

    session = SelectionSession(folder=window.folder or __import__(
        "pathlib").Path.cwd(), config=Config())
    session.records = []
    window.on_analysis_done(session)
    app.processEvents()

    assert not window.status_label.text().startswith(
        tr("Cancelled — results so far: "))
    window.analysis_worker = None


def test_worker_failure_restores_controls(window, app, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "critical", staticmethod(
        lambda *args, **kwargs: None))

    window._begin_task("작업 중")
    window.on_worker_failed("무언가 터졌습니다")
    app.processEvents()

    assert not window.status_progress.isVisible()
    assert not window.stop_button.isVisible()
    assert window.stop_button.isEnabled()


def test_export_progress_reports_remaining_time(window, app):
    """현상 내보내기는 실측 장당 7초입니다 — 남은 시간을 알려야 합니다."""
    import time

    window._export_started = time.monotonic() - 20.0  # 이미 20초 지난 것처럼
    window._begin_task("내보내기")
    window.on_export_progress(4, 100)
    app.processEvents()

    assert window.status_progress.maximum() == 100
    assert window.status_progress.value() == 4
    assert window.status_eta.isVisible()
    # 어떤 단위로 나오든 비어 있지만 않으면 됩니다 — 정확한 문구는
    # test_eta_wording 이 봅니다.
    assert window.status_eta.text().strip()
    window._end_task()


def test_status_bar_is_tall_enough_to_notice(window):
    """얇으면 있으나 마나입니다 — 스타일이 빠지면 여기서 걸립니다."""
    bar = window.statusBar()
    assert bar.isVisible()
    assert bar.height() >= 30
