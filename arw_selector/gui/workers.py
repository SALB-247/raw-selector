"""백그라운드 작업 스레드.

무거운 일은 전부 여기서 합니다. 4000장 분석 중에 UI가 멈추면 사용자는
프로그램이 죽은 줄 압니다.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThread, Signal

from ..core import export as export_module
from ..core.config import Config
from ..core.pipeline import Progress
from ..core.session import SelectionSession
from ..core.thumbs import thumbnail_path


def silent_disconnect(signal) -> None:
    """연결이 없어도 조용히 넘어가는 disconnect.

    정리 코드에서는 이미 끊긴 경우가 정상인데, libpyside는 그때마다
    RuntimeWarning을 냅니다.
    """
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        try:
            signal.disconnect()
        except (TypeError, RuntimeError):
            pass


_UNSTOPPED_WORKERS: set = set()
"""제때 멈추지 않은 워커를 붙잡아 두는 곳.

`stop_worker`가 False를 돌려준 뒤 호출부가 참조를 놓으면, 파이썬이 아직
도는 QThread를 파괴하고 Qt는 그것을 치명적 오류로 보고 프로세스를
즉사시킵니다(0xc0000409). 15초를 기다린 보람이 참조를 버리는 그 한 줄에서
사라집니다.

기다리는 대신 여기로 옮깁니다 — 창은 즉시 닫히고, 스레드는 제 속도로 끝난
뒤 스스로 빠집니다. 파괴되는 시점에는 이미 멈춰 있습니다. loupe의
`_detach_until_finished`와 같은 방식입니다.
"""


def keep_until_finished(worker) -> None:
    """멈추지 않은 워커의 참조를 프로세스 수준으로 옮깁니다."""
    if worker is None:
        return
    _UNSTOPPED_WORKERS.add(worker)
    try:
        worker.finished.connect(lambda: _UNSTOPPED_WORKERS.discard(worker))
    except (AttributeError, RuntimeError):
        # finished를 못 걸면 스스로 빠지지 못합니다. 그래도 붙잡아 두는
        # 편이 낫습니다 — 새는 것은 객체 하나지만, 놓치면 프로세스입니다.
        pass


def stop_worker(worker, timeout_ms: int = 15000) -> bool:
    """워커를 안전하게 세웁니다. 실제로 멈췄으면 True.

    QThread가 **도는 채로 파괴되면 Qt가 qFatal로 프로세스를 즉사시킵니다**
    (Windows fail-fast, 0xc0000409). 그래서 순서가 중요합니다:

      1. 취소 신호 — run()이 다음 검사 지점에서 빠져나오게
      2. 시그널 해제 — 늦게 도착한 신호가 이미 지워진 창을 건드리지 않게
      3. 대기 — 진짜로 끝날 때까지

    타임아웃은 넉넉해야 합니다. 짧게 잡고 그냥 진행하면 바로 그 크래시가
    납니다. 내보내기는 한 장 현상에 수백 ms가 걸리기도 합니다.
    """
    if worker is None:
        return True
    try:
        if hasattr(worker, "cancel"):
            worker.cancel()
        for name in ("done", "failed", "finished_ok", "progressed", "finished"):
            signal = getattr(worker, name, None)
            if signal is not None:
                silent_disconnect(signal)
        if not worker.isRunning():
            return True
        return bool(worker.wait(timeout_ms))
    except RuntimeError:
        return True  # 이미 정리된 객체


class AnalysisWorker(QThread):
    """폴더 분석을 백그라운드에서 돌립니다."""

    progressed = Signal(object)   # Progress
    finished_ok = Signal(object)  # SelectionSession
    failed = Signal(str)

    def __init__(self, folder: Path, config: Config, use_cache: bool = True,
                 paths: list[Path] | None = None, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.config = config
        self.use_cache = use_cache
        self.paths = paths
        """None이면 폴더 전체 스캔, 목록이면 그 파일들만."""
        self._cancelled = False

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
            self.finished_ok.emit(session)
        except Exception as exc:  # noqa: BLE001 - 스레드에서 새어나가면 앱이 죽습니다
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class ExportWorker(QThread):
    """내보내기를 백그라운드에서 돌립니다.

    현상까지 하면 장당 수백 ms가 걸려서 수백 장이면 몇 분입니다. UI가 멈추면
    안 되고, 중간에 그만둘 수 있어야 합니다.
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
    loaded = Signal(str, object)  # path, QImage (QPixmap은 GUI 스레드에서만 만든다)


class ThumbnailTask(QRunnable):
    """썸네일 한 장을 디스크에서 읽어 옵니다.

    분석 때 만들어 둔 512px JPEG를 읽습니다. 없으면 (예: 예전 캐시) RAW에서
    직접 뽑는데, 이건 느리므로 스레드 풀에서 처리합니다.
    """

    def __init__(self, source: Path, cache_dir: Path, signals: ThumbnailSignals):
        super().__init__()
        self.source = source
        self.cache_dir = cache_dir
        self.signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        # QPixmap은 GUI 스레드 밖에서 만들면 안 됩니다(간헐적 크래시). 워커에서는
        # QImage로 읽어 넘기고, 메인 스레드 슬롯에서 QPixmap으로 변환합니다.
        from PySide6.QtGui import QImage

        image = QImage()
        thumb = thumbnail_path(self.cache_dir, self.source)

        if thumb.exists():
            image.load(str(thumb))

        if image.isNull():
            # 썸네일이 없으면 원본에서 만들어 두고 다음부터 재사용합니다
            try:
                from ..core.raw_io import load_preview
                from ..core.thumbs import write_thumbnail

                preview = load_preview(self.source, max_long_edge=512)
                write_thumbnail(preview, thumb)
                image.load(str(thumb))
            except Exception:  # noqa: BLE001 - 썸네일 실패는 치명적이지 않습니다
                pass

        self.signals.loaded.emit(str(self.source), image)
