"""Background worker threads.

Everything heavy happens here. If the UI freezes while analysing 4000
shots, the user takes it for a dead program.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThread, Signal

from ..core import export as export_module
from ..core.config import Config
from ..core.pipeline import Progress
from ..core.session import SelectionSession
from ..core.thumbs import thumbnail_path

log = logging.getLogger(__name__)


def silent_disconnect(signal) -> None:
    """A disconnect that passes over quietly even with nothing connected.

    In cleanup code, already being disconnected is the normal case, but
    libpyside raises a RuntimeWarning every single time.
    """
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        try:
            signal.disconnect()
        except (TypeError, RuntimeError):
            pass


_UNSTOPPED_WORKERS: set = set()
"""Where a worker that did not stop in time is held on to.

If the caller lets go of the reference after `stop_worker` returns False,
Python destroys a QThread that is still running, Qt takes that as a fatal
error and kills the process outright (0xc0000409). Everything gained by
waiting 15 seconds is lost on that one line that throws the reference away.

Instead of waiting, it is moved here - the window closes immediately, and
the thread finishes at its own pace and then drops itself out. By the time
it is destroyed it has already stopped. The same approach as loupe's
`_detach_until_finished`.
"""


def keep_until_finished(worker) -> None:
    """Moves an unstopped worker's reference up to process level."""
    if worker is None:
        return
    _UNSTOPPED_WORKERS.add(worker)
    try:
        worker.finished.connect(lambda: _UNSTOPPED_WORKERS.discard(worker))
    except (AttributeError, RuntimeError):
        # If finished cannot be hooked up it can never drop itself out.
        # Even so, holding on is better - what leaks is one object, but
        # what is let go is the process.
        pass


def stop_worker(worker, timeout_ms: int = 15000) -> bool:
    """Stops a worker safely. True if it really did stop.

    If a QThread is **destroyed while still running, Qt kills the process
    outright with qFatal** (Windows fail-fast, 0xc0000409). That is why the
    order matters:

      1. cancel signal - so run() drops out at the next check point
      2. disconnect signals - so a late signal cannot touch a deleted window
      3. wait - until it truly ends

    The timeout has to be generous. Set it short and carry on regardless and
    you get exactly that crash. Export can take several hundred ms to
    develop a single shot.
    """
    if worker is None:
        return True
    try:
        if hasattr(worker, "cancel"):
            worker.cancel()
        for name in ("done", "failed", "finished_ok", "progressed", "restoring", "finished"):
            signal = getattr(worker, name, None)
            if signal is not None:
                silent_disconnect(signal)
        if not worker.isRunning():
            return True
        return bool(worker.wait(timeout_ms))
    except RuntimeError:
        return True  # already a cleaned-up object


class AnalysisWorker(QThread):
    """Runs the folder analysis in the background."""

    progressed = Signal(object)   # Progress
    restoring = Signal(int, int)  # saved main-subject picks put back: done, total
    finished_ok = Signal(object)  # SelectionSession
    failed = Signal(str)

    def __init__(self, folder: Path, config: Config, use_cache: bool = True,
                 paths: list[Path] | None = None, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.config = config
        self.use_cache = use_cache
        self.paths = paths
        """None scans the whole folder; a list means only those files."""
        self._cancelled = False
        self.restored_faces = 0
        """How many saved main-subject picks the run put back (_restore_main_faces)."""

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled

    def run(self) -> None:
        try:
            session = SelectionSession(folder=self.folder, config=self.config)
            session.run(
                use_cache=self.use_cache,
                progress_cb=self.progressed.emit,
                should_cancel=self.is_cancelled,
                paths=self.paths,
            )
            self.restored_faces = self._restore_main_faces(session)
            self.finished_ok.emit(session)
        except Exception as exc:  # noqa: BLE001 - a thread leak kills the app
            self.failed.emit(f"{type(exc).__name__}: {exc}")

    def _restore_main_faces(self, session: SelectionSession) -> int:
        """Puts the saved main-subject picks back before the session is
        handed over.

        Each pick is a preview read and a detector pass, so fifty of them
        on the GUI thread froze the window for up to a minute at the very
        moment "analysis complete" appeared, with no progress shown. Here
        the progress bar is still up (restoring) and the stop button still
        works. Grades and develop edits are instant and stay with the
        window (main_window.on_analysis_done). The batch is re-graded when
        a pick changed a score. A failure here must not fail the analysis.
        """
        from ..core import edits as edits_store

        try:
            faces = edits_store.restore_main_faces(
                self.folder, session.records, self.config.analyze,
                progress_cb=self.restoring.emit, should_cancel=self.is_cancelled)
            if faces:
                session.regrade()
            return faces
        except Exception:  # noqa: BLE001 - the measurements stand on their own
            log.warning("저장된 주 피사체 선택을 복원하지 못했다", exc_info=True)
            return 0


class ExportWorker(QThread):
    """Runs the export in the background.

    With develop included it takes several hundred ms per shot, so a few
    hundred shots is minutes. The UI must not freeze, and you have to be
    able to give up part way through.
    """

    progressed = Signal(int, int)
    finished_ok = Signal(object)  # ExportResult
    failed = Signal(str)

    def __init__(self, records, destination: Path, options=None, parent=None):
        super().__init__(parent)
        from ..core.export_options import ExportOptions

        self.records = records
        self.destination = destination
        self.options = options or ExportOptions()
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled

    def run(self) -> None:
        try:
            result = export_module.export_records(
                self.records,
                self.destination,
                move=self.options.move,
                include_companions=self.options.include_companions,
                apply_develop=self.options.apply_develop,
                options=self.options,
                progress_cb=lambda done, total: self.progressed.emit(done, total),
                should_cancel=self.is_cancelled,
            )
            self.finished_ok.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class ThumbnailSignals(QObject):
    loaded = Signal(str, object)  # path, QImage (QPixmap: GUI thread only)


class ThumbnailTask(QRunnable):
    """Reads one thumbnail off the disk.

    Reads the 512px JPEG made during analysis. If it is missing (e.g. an old
    cache) it is pulled straight out of the RAW, and since that is slow it
    is handled on the thread pool.
    """

    def __init__(self, source: Path, cache_dir: Path, signals: ThumbnailSignals):
        super().__init__()
        self.source = source
        self.cache_dir = cache_dir
        self.signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        # QPixmap must not be built outside the GUI thread (intermittent
        # crashes). The worker reads it as a QImage and hands that over, and
        # the main-thread slot converts it to a QPixmap.
        from PySide6.QtGui import QImage

        image = QImage()
        thumb = thumbnail_path(self.cache_dir, self.source)

        if thumb.exists():
            image.load(str(thumb))

        if image.isNull():
            # With no thumbnail, build one from the original and reuse it
            # from then on
            try:
                from ..core.raw_io import load_preview
                from ..core.thumbs import write_thumbnail

                preview = load_preview(self.source, max_long_edge=512)
                write_thumbnail(preview, thumb)
                image.load(str(thumb))
            except Exception:  # noqa: BLE001 - thumbnail failure is not fatal
                pass

        self.signals.loaded.emit(str(self.source), image)
