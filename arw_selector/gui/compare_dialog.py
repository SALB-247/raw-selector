"""Two to four shots side by side, zoomed and panned together.

Picking the one frame out of a burst is what culling mostly is, and
stepping through the loupe one frame at a time leaves the comparison to
memory. Here the frames sit next to each other, a wheel on one zooms all,
a drag on one pans all, and Z puts every tile on its own focus region so
the eyes of four frames can be judged at once. 1, 2, 3 grade the active
tile (the one with the bright border - click a tile or use the arrows).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QThread, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QFrame, QGridLayout, QLabel, QVBoxLayout

from ..core.types import Grade, ImageRecord
from .grid_view import GRADE_COLORS, GRADE_LABELS
from .i18n import tr
from .image_view import ImageView
from .workers import silent_disconnect

MAX_TILES = 4

_RUNNING_LOADERS: set = set()
"""Preview threads let go of by a window that closed before they were done.

A window closed while its previews were still being read used to wait
five seconds for the thread - twice, once in closeEvent and once on
finished - and then be deleted with the thread still running, which Qt
treats as fatal (0xC0000409 on Windows). Four 50MP previews off a slow
card reader or a disk waking up take longer than that, and closing the
window that "does not show anything" is the natural reaction. Now the
thread belongs to no window, is told to stop after the photo it is on,
and is kept here until it ends; the window closes at once. The same
arrangement as the loupe's retired renders.
"""


def _detach_until_finished(thread) -> None:
    _RUNNING_LOADERS.add(thread)
    thread.finished.connect(lambda: _RUNNING_LOADERS.discard(thread))


def wait_for_detached_loaders(timeout_ms: int = 30000) -> None:
    """Before the app exits. A thread still running when the interpreter
    ends is the same crash, so here we do wait (main_window._shutdown_workers)."""
    for thread in list(_RUNNING_LOADERS):
        try:
            if thread.isRunning():
                thread.wait(timeout_ms)
        except RuntimeError:
            pass
    _RUNNING_LOADERS.clear()


def default_loader(path):
    """The embedded preview at 2048px - what the loupe's fast mode shows."""
    from ..core.raw_io import load_preview

    return load_preview(path, max_long_edge=2048)


class CompareTile(QFrame):
    activated = Signal(object)

    def __init__(self, record: ImageRecord, parent=None) -> None:
        super().__init__(parent)
        self.record = record
        self._active = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        self.header = QLabel()
        layout.addWidget(self.header)
        self.view = ImageView()
        self.view.installEventFilter(self)
        layout.addWidget(self.view, 1)
        self.set_active(False)
        self.refresh_header()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.view and event.type() == QEvent.MouseButtonPress:
            self.activated.emit(self)
        return False

    def refresh_header(self) -> None:
        record = self.record
        grade = record.final_grade
        label = GRADE_LABELS.get(grade, "")
        if record.manual_grade is not None:
            label += " ✋"
        self.header.setText(f"{record.path.name}  ·  {record.score:.0f}  ·  {label}")
        colour = GRADE_COLORS.get(grade)
        self.header.setStyleSheet(
            f"padding: 2px 6px; font-weight: bold; color: #141418; "
            f"background: {colour.name() if colour else '#666'}; border-radius: 4px;")

    def set_active(self, active: bool) -> None:
        self._active = active
        self.setStyleSheet(
            "CompareTile { border: 2px solid #8cb4ff; border-radius: 6px; }" if active
            else "CompareTile { border: 1px solid #3a3a42; border-radius: 6px; }")

    def is_active(self) -> bool:
        return self._active


class _PreviewLoader(QThread):
    """Reads the previews after the window is up. Four 50MP embedded JPEGs
    take a couple of seconds, which is too long for the window to sit
    unopened."""
    loaded = Signal(int, object)

    def __init__(self, paths, loader, parent=None) -> None:
        super().__init__(parent)
        self._paths = list(paths)
        self._loader = loader

    def run(self) -> None:
        for position, path in enumerate(self._paths):
            if self.isInterruptionRequested():
                return  # the window is gone; at most the photo under way is read
            try:
                image = self._loader(path)
            except Exception:  # noqa: BLE001 - one unreadable frame must not sink the window
                image = None
            self.loaded.emit(position, image)


class CompareDialog(QDialog):
    records_changed = Signal()

    def __init__(self, records: list[ImageRecord], parent=None, loader=None) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.Window, True)
        self.setWindowTitle(tr("Compare"))
        self.records = list(records)[:MAX_TILES]
        loader = loader or default_loader
        grid = QGridLayout(self)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(6)
        columns = 2 if len(self.records) >= 3 else max(1, len(self.records))
        self.tiles: list[CompareTile] = []
        self._syncing = False
        self._active = 0
        for position, record in enumerate(self.records):
            tile = CompareTile(record)
            tile.activated.connect(self._on_tile_activated)
            tile.view.set_message(tr("Loading…"))
            tile.view.view_changed.connect(lambda t=tile: self._sync_from(t))
            grid.addWidget(tile, position // columns, position % columns)
            self.tiles.append(tile)
        if self.tiles:
            self.tiles[0].set_active(True)
        # No parent: the thread has to outlive the window when the window
        # closes first (see _RUNNING_LOADERS).
        self._loader = _PreviewLoader([r.path for r in self.records], loader)
        self._loader.loaded.connect(self._on_loaded)
        # Esc goes through reject/done, not closeEvent: the thread is let go
        # on that road too. _release_loader does nothing the second time.
        self.finished.connect(lambda _=0: self._release_loader())
        self._loader.start()
        for keys, handler in (
            ("1", lambda: self.grade_active(Grade.KEEP)),
            ("2", lambda: self.grade_active(Grade.REVIEW)),
            ("3", lambda: self.grade_active(Grade.REJECT)),
            ("0", lambda: self.grade_active(None)),
            ("Left", lambda: self.step_active(-1)),
            ("Right", lambda: self.step_active(1)),
            ("Z", self.zoom_to_focus),
            ("R", self.reset_views),
            ("F1", self.show_shortcuts),
        ):
            QShortcut(QKeySequence(keys), self, handler)
        self.resize(1400, 900)

    def _on_loaded(self, position: int, image) -> None:
        if not 0 <= position < len(self.tiles):
            return
        view = self.tiles[position].view
        if image is None:
            view.set_message(tr("Could not load this photo"))
            return
        from .loupe import bgr_to_pixmap

        view.set_pixmap(bgr_to_pixmap(image))

    def wait_loaded(self, timeout_ms: int = 5000) -> bool:
        """Blocks until every preview is in (tests, and callers that need
        the pixmaps before zooming)."""
        from PySide6.QtWidgets import QApplication

        loader = self._loader
        finished = loader.wait(timeout_ms) if loader is not None else True
        QApplication.processEvents()
        return finished

    def closeEvent(self, event) -> None:
        self._release_loader()
        super().closeEvent(event)

    def _release_loader(self) -> None:
        """Lets the preview thread go without waiting for it.

        Its signal is disconnected first, so a photo arriving late cannot
        land on a closed window. If it is still reading, it is asked to
        stop after the current photo and kept alive at module level until
        it does. Nothing here blocks: the window closes at once however
        slow the disk is.
        """
        loader, self._loader = self._loader, None
        if loader is None:
            return
        silent_disconnect(loader.loaded)
        try:
            loader.requestInterruption()
            if loader.isRunning():
                _detach_until_finished(loader)
        except RuntimeError:
            pass  # the thread object is already gone

    # ---------------------------------------------------------------- tiles

    def _on_tile_activated(self, tile: CompareTile) -> None:
        self.set_active(self.tiles.index(tile))

    def set_active(self, index: int) -> None:
        if not self.tiles:
            return
        self._active = max(0, min(index, len(self.tiles) - 1))
        for position, tile in enumerate(self.tiles):
            tile.set_active(position == self._active)

    def active_record(self) -> ImageRecord | None:
        return self.tiles[self._active].record if self.tiles else None

    def step_active(self, delta: int) -> None:
        if self.tiles:
            self.set_active((self._active + delta) % len(self.tiles))

    def grade_active(self, grade: Grade | None) -> None:
        if not self.tiles:
            return
        tile = self.tiles[self._active]
        tile.record.manual_grade = grade
        tile.refresh_header()
        self.records_changed.emit()

    # ---------------------------------------------------------------- views

    def _sync_from(self, tile: CompareTile) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            zoom, offset = tile.view.view_state()
            for other in self.tiles:
                if other is not tile:
                    other.view.set_view_state(zoom, offset)
        finally:
            self._syncing = False

    def zoom_to_focus(self) -> None:
        """Every tile on its own focus region: the eyes of each frame."""
        for tile in self.tiles:
            focus = tile.record.focus
            pixmap = tile.view.pixmap()
            if focus is None or pixmap is None or pixmap.isNull() or not focus.source_width:
                continue
            tile.view.zoom_to_roi(focus.roi, pixmap.width() / float(focus.source_width))

    def reset_views(self) -> None:
        for tile in self.tiles:
            tile.view.reset_view()

    def show_shortcuts(self) -> None:
        from .shortcuts_dialog import show_shortcuts

        show_shortcuts(self)
