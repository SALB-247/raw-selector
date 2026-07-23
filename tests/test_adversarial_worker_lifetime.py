"""도는 QThread의 참조를 놓지 않는지.

Qt는 **실행 중인 QThread가 파괴되면** qFatal로 프로세스를 즉사시킵니다
(Windows fail-fast, 0xc0000409). 파이썬에서 그 파괴는 조용히 일어납니다 —
`self.export_worker = 새워커` 한 줄로 앞 워커의 마지막 참조가 사라지면
그걸로 끝입니다. 예외도, 로그도, 스택도 남지 않습니다.

이 프로젝트는 실제로 그 크래시를 겪었고 loupe 쪽은 고쳤습니다
(`_retire_worker`, `_detach_until_finished`). 여기서는 메인 창의 분석·
내보내기 워커에 같은 규칙이 서 있는지 확인합니다.

**진짜 QThread를 버려 보는 테스트는 쓰지 않습니다** — 규칙이 깨져 있으면
테스트가 실패하는 대신 프로세스가 죽어서, 무엇이 잘못됐는지 아무것도
남지 않기 때문입니다. QThread의 겉모습만 흉내 낸 평범한 파이썬 객체를
씁니다. 검사하려는 것은 스레드 동작이 아니라 **참조를 어떻게 다루는가**입니다.
"""

from __future__ import annotations

import gc
import os
import weakref

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402
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
    win.export_worker = None
    win.analysis_worker = None
    win.close()
    app.processEvents()


class _Signal:
    """connect/disconnect만 받아 주는 자리표시자."""

    def connect(self, *_args):
        pass

    def disconnect(self, *_args):
        pass


class StubbornWorker:
    """취소해도 멈추지 않는 워커. QThread가 C 호출 안에 있을 때의 모습입니다.

    rawpy 디모자이크나 cv2 연산은 단일 C 호출이라 취소 플래그를 볼 지점이
    없습니다. wait()가 시간 안에 못 끝내는 상황이 실제로 나옵니다.
    """

    def __init__(self):
        self.cancelled = False
        self.waited = False
        for name in ("done", "failed", "finished_ok", "progressed", "finished"):
            setattr(self, name, _Signal())

    def cancel(self):
        self.cancelled = True

    def isRunning(self):
        return True

    def wait(self, _timeout_ms=0):
        self.waited = True
        return False  # 시간 안에 못 멈췄습니다


class FakeExportWorker(StubbornWorker):
    """스레드를 실제로 띄우지 않는 ExportWorker 대역.

    진짜를 띄우면 끝나자마자 on_export_done이 모달 대화상자를 열어 테스트가
    영영 멈춥니다. 여기서 볼 것은 내보내기 동작이 아니라 '앞 워커의 참조를
    어떻게 하는가'뿐입니다.
    """

    def __init__(self, records, destination, options=None, parent=None):
        super().__init__()
        self.records = records
        self.destination = destination
        self.started = False

    def start(self):
        self.started = True


@pytest.fixture
def no_real_export(monkeypatch):
    """실제 스레드와 모달 대화상자를 둘 다 치웁니다.

    QMessageBox는 offscreen에서도 제 이벤트 루프를 돌며 입력을 기다립니다 —
    누를 사람이 없으니 테스트가 영영 멈춥니다. 무엇을 알렸는지만 받아 둡니다.
    """
    from PySide6.QtWidgets import QMessageBox

    from arw_selector.gui import main_window as module

    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(module, "ExportWorker", FakeExportWorker)
    monkeypatch.setattr(
        QMessageBox, "information",
        staticmethod(lambda parent, title, text, *a, **kw: shown.append((title, text))),
    )
    return shown


class TestExportDoesNotOverlap:
    """내보내기 두 개가 겹치면 워커 참조가 덮이고, 되돌리기 로그도 갈립니다."""

    def test_second_export_does_not_replace_a_running_worker(
        self, window, no_real_export, tmp_path
    ):
        """대기열 버튼과 루페의 '내보내기'는 _set_busy가 잠그지 않습니다.

        토글이 잠기지 않으니 진행 중에 한 번 더 누를 수 있고, 그때
        _run_export가 self.export_worker를 덮으면 도는 스레드가 파괴됩니다.
        같은 대상 폴더에 두 벌이 동시에 쓰이면 되돌리기 로그도 갈립니다.
        """
        running = StubbornWorker()
        alive = weakref.ref(running)
        window.export_worker = running
        del running

        window._run_export([], tmp_path / "출력")
        gc.collect()

        assert alive() is not None, (
            "도는 내보내기 워커의 참조를 버렸습니다 — Qt가 프로세스를 죽입니다"
        )
        assert window.export_worker is alive()
        # 조용히 무시하면 사용자는 버튼이 안 먹는다고 느낍니다
        assert no_real_export, "겹친 요청을 알리지 않았습니다"

    def test_export_starts_when_nothing_is_running(
        self, window, no_real_export, tmp_path
    ):
        """겹침 방어가 정상 경로까지 막으면 내보내기가 아예 안 됩니다."""
        window.export_worker = None
        window._run_export([], tmp_path / "출력")
        assert isinstance(window.export_worker, FakeExportWorker)
        assert window.export_worker.started
        assert not no_real_export

    def test_finished_worker_may_be_replaced(self, window, no_real_export, tmp_path):
        """끝난 워커까지 붙들면 두 번째 내보내기가 영영 시작되지 않습니다."""
        class Finished(StubbornWorker):
            def isRunning(self):
                return False

        window.export_worker = Finished()
        window._run_export([], tmp_path / "출력")
        assert isinstance(window.export_worker, FakeExportWorker)


class TestAnalysisDoesNotOverlap:
    def test_second_analysis_does_not_replace_a_running_worker(self, window, tmp_path):
        running = StubbornWorker()
        alive = weakref.ref(running)
        window.folder = tmp_path
        window.analysis_worker = running
        del running

        window.start_analysis()
        gc.collect()

        assert alive() is not None
        assert window.analysis_worker is alive()

    def test_cancel_then_restart_keeps_the_old_worker_alive(self, window, tmp_path):
        """취소 직후 다시 누르는 것은 사용자가 실제로 하는 조작입니다."""
        running = StubbornWorker()
        alive = weakref.ref(running)
        window.folder = tmp_path
        window.analysis_worker = running
        del running

        window.cancel_running()
        window.start_analysis()
        gc.collect()

        assert alive() is not None and alive().cancelled
        assert window.analysis_worker is alive()


class TestShutdownKeepsUnstoppableWorkers:
    def test_worker_that_did_not_stop_is_not_dropped(self, window):
        """제때 안 멈춘 워커의 참조까지 버리면, 바로 그때 크래시가 납니다.

        예전 코드는 stop_worker가 False를 돌려줘도 경고만 찍고
        `setattr(self, name, None)`으로 참조를 버렸습니다. 15초를 기다린
        보람이 그 한 줄에서 사라집니다.
        """
        running = StubbornWorker()
        alive = weakref.ref(running)
        window.analysis_worker = running
        del running

        window._shutdown_workers()
        gc.collect()

        assert alive() is not None, (
            "안 멈춘 워커의 참조를 버렸습니다 — 도는 QThread가 파괴됩니다"
        )
        assert alive().cancelled and alive().waited

    def test_stopped_worker_is_released(self, window):
        """멈춘 워커까지 붙들고 있으면 프로세스가 안 끝납니다."""
        class Stopped(StubbornWorker):
            def isRunning(self):
                return False

        stopped = Stopped()
        alive = weakref.ref(stopped)
        window.analysis_worker = stopped
        del stopped

        window._shutdown_workers()
        gc.collect()

        assert window.analysis_worker is None
        assert alive() is None, "끝난 워커가 계속 붙잡혀 있습니다"

    def test_shutdown_is_idempotent(self, window):
        """닫기와 종료 양쪽에서 불립니다. 두 번 불려도 같아야 합니다."""
        window._shutdown_workers()
        window._shutdown_workers()
        assert window.analysis_worker is None
        assert window.export_worker is None


class TestStopWorkerEdges:
    def test_none_is_treated_as_stopped(self):
        from arw_selector.gui.workers import stop_worker

        assert stop_worker(None) is True

    def test_object_whose_c_side_is_gone(self):
        """이미 파괴된 Qt 객체를 만지면 RuntimeError가 납니다.

        정리 코드에서는 흔한 일이라 조용히 '멈췄다'로 봐야 합니다.
        """
        from arw_selector.gui.workers import stop_worker

        class Deleted:
            def cancel(self):
                raise RuntimeError("Internal C++ object already deleted.")

        assert stop_worker(Deleted()) is True

    def test_worker_without_cancel_still_waits(self):
        from arw_selector.gui.workers import stop_worker

        class NoCancel:
            def __init__(self):
                self.waited = False

            def isRunning(self):
                return True

            def wait(self, _timeout_ms=0):
                self.waited = True
                return True

        worker = NoCancel()
        assert stop_worker(worker) is True
        assert worker.waited


class TestFullRenderAcrossCutChange:
    """Full Render가 도는 동안 컷을 넘기는 것은 평범한 조작입니다.

    디모자이크 결과(27MP 기준 약 390MB)는 한 컷치만 들고 다니면서 경로로
    이름표를 붙입니다. 그 이름표가 어긋나면 **다른 사진의 화소가 최종
    화질 결과로 나옵니다** — 예외도, 표시도 없습니다. 사용자는 B 컷을
    보고 있다고 믿으면서 A 컷을 보고 셀렉하게 됩니다.
    """

    @pytest.fixture
    def dialog(self, app):
        from pathlib import Path

        from arw_selector.core.types import ImageRecord
        from arw_selector.gui.loupe import LoupeDialog

        records = [ImageRecord(path=Path(f"컷{i}.ARW")) for i in range(3)]
        widget = LoupeDialog(records[0], records, fast=True)
        yield widget
        widget.close()
        app.processEvents()

    def _worker_for(self, dialog, path):
        from arw_selector.core.develop import DevelopSettings
        from arw_selector.gui.loupe import FinalRenderWorker

        return FinalRenderWorker(path, DevelopSettings(), None, target_long_edge=64)

    def test_late_source_from_the_previous_cut_is_not_kept(self, dialog):
        """앞 컷의 디모자이크가 지금 컷 이름으로 저장되면 안 됩니다."""
        import numpy as np

        first, second = dialog.records[0], dialog.records[1]
        worker = self._worker_for(dialog, first.path)
        worker.source_ready.connect(dialog._keep_demosaic)

        # 렌더가 도는 사이에 컷을 넘겼습니다
        dialog.record = second
        dialog._drop_demosaic()

        worker.source_ready.emit(np.zeros((4, 4, 3), np.uint8))

        assert dialog._demosaic_for(second.path) is None, (
            "앞 컷의 화소가 지금 컷의 원본으로 저장됐습니다"
        )

    def test_source_for_the_current_cut_is_kept(self, dialog):
        """정상 경로까지 막으면 확대·이동마다 디모자이크를 다시 합니다."""
        import numpy as np

        current = dialog.record
        worker = self._worker_for(dialog, current.path)
        worker.source_ready.connect(dialog._keep_demosaic)

        image = np.zeros((4, 4, 3), np.uint8)
        worker.source_ready.emit(image)

        assert dialog._demosaic_for(current.path) is image

    def test_retired_worker_stops_feeding_the_window(self, dialog):
        """은퇴시킨 워커의 source_ready까지 끊어야 뒤늦은 원본이 안 들어옵니다."""
        import numpy as np

        current = dialog.record
        worker = self._worker_for(dialog, current.path)
        worker.source_ready.connect(dialog._keep_demosaic)

        dialog._retire_worker(worker)
        worker.source_ready.emit(np.zeros((4, 4, 3), np.uint8))

        assert dialog._demosaic_for(current.path) is None

    def test_cleanup_survives_a_worker_missing_a_signal(self, dialog):
        """정리 경로는 어떤 객체를 받아도 예외를 내면 안 됩니다.

        중간에 AttributeError가 나면 뒤따르는 취소와 참조 보관이 통째로
        건너뛰어집니다 — 도는 스레드를 놓치는 바로 그 상황입니다.
        """
        class Partial:
            """신호를 일부만 가진 워커. 예전 버전이나 대역에서 나옵니다."""

            def __init__(self):
                self.cancelled = False
                self.done = _Signal()

            def cancel(self):
                self.cancelled = True

            def isRunning(self):
                return False

        worker = Partial()
        dialog._retire_worker(worker)

        dialog._final_worker = worker
        dialog._shutdown_workers()
        assert worker.cancelled, "정리가 중간에 끊겨 취소도 못 했습니다"


class TestManyLoupesOpenAndClose:
    """보정 창을 여러 개 열고 닫아도 목록과 워커가 새지 않아야 합니다."""

    def test_repeated_open_close_leaves_no_dangling_dialog(self, window, app, tmp_path):
        from pathlib import Path

        from arw_selector.core.types import ImageRecord
        from arw_selector.gui.loupe import LoupeDialog

        opened = []
        for index in range(5):
            dialog = LoupeDialog(
                ImageRecord(path=Path(f"{index}.ARW")), fast=True)
            window._loupes[Path(f"{index}.ARW")] = dialog
            opened.append(dialog)
        app.processEvents()

        for dialog in opened:
            dialog.close()
        app.processEvents()

        window._shutdown_workers()
        assert window._loupes == {}

    def test_shutdown_closes_dialogs_that_are_already_gone(self, window, app):
        """루페는 부모가 없는 독립 창이라 이미 닫혀 있을 수 있습니다."""
        from pathlib import Path

        class ClosedDialog:
            def close(self):
                raise RuntimeError("Internal C++ object already deleted.")

        window._loupes[Path("x.ARW")] = ClosedDialog()
        window._shutdown_workers()
        assert window._loupes == {}
