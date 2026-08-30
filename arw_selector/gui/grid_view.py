"""The thumbnail grid.

Pushing 4000 photos into a QListWidget as items puts the memory and the
start-up time out of reach. It is built as a model/delegate instead, and only
the thumbnails visible on screen are read in, asynchronously.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QAbstractListModel,
    QEvent,
    QModelIndex,
    QRect,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from collections import OrderedDict

from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QListView, QStyle, QStyledItemDelegate

from ..core.types import Grade, ImageRecord
from .i18n import tr
from .reason_text import render_all
from .workers import ThumbnailSignals, ThumbnailTask

GRADE_COLORS = {
    Grade.KEEP: QColor(76, 175, 80),
    Grade.REVIEW: QColor(255, 167, 38),
    Grade.REJECT: QColor(229, 87, 87),
}

GRADE_LABELS = {
    Grade.KEEP: "KEEP",
    Grade.REVIEW: "REVIEW",
    Grade.REJECT: "REJECT",
}
"""The grade is written out in words too. Colour alone cannot be told apart
by someone with a colour vision deficiency."""

RECORD_ROLE = Qt.UserRole + 1


THUMBNAIL_CACHE_BYTES = 128 * 1024 * 1024
"""The most memory the thumbnails put on screen may hold on to.

There used to be no ceiling, so every thumbnail scrolled past stayed in RAM.
On a 3000-photo folder that comes to several hundred MB, and on an 8GB PC
everything else gets that much tighter. Throwing one away costs little, as it
is read straight back from the disk thumbnail cache (a JPEG of a few tens of
KB), so scrolling feels much the same.
"""

MIN_CACHED_THUMBNAILS = 60
"""The fewest kept regardless of size. One screenful has to stay behind."""


class RecordListModel(QAbstractListModel):
    def __init__(self, cache_dir: Path, parent=None):
        super().__init__(parent)
        self._records: list[ImageRecord] = []
        # An LRU where the most recently used goes to the back. On overflow
        # they are thrown away from the front.
        self._pixmaps: "OrderedDict[str, QPixmap]" = OrderedDict()
        self._pixmap_bytes = 0
        self._requested: set[str] = set()
        self.cache_dir = cache_dir

        self._pool = QThreadPool()
        # Eating every core on thumbnail reads makes the UI stutter
        self._pool.setMaxThreadCount(max(2, QThreadPool.globalInstance().maxThreadCount() // 2))
        self._signals = ThumbnailSignals()
        self._signals.loaded.connect(self._on_thumbnail)

    def set_records(self, records: list[ImageRecord], cache_dir: Path | None = None) -> None:
        self.beginResetModel()
        self._records = records
        if cache_dir is not None and cache_dir != self.cache_dir:
            # Once you have switched to another folder there is no reason to
            # look at the old thumbnails again. This used not to be cleared,
            # so RAM piled up the more folders you moved through.
            self._pixmaps.clear()
            self._pixmap_bytes = 0
        if cache_dir is not None:
            self.cache_dir = cache_dir
        self._requested.clear()
        self.endResetModel()

    def shutdown(self) -> None:
        """Stops the thumbnail work.

        A QRunnable fires a signal as it finishes, and if the model has
        already been destroyed by then it calls a slot on an object that is
        not there. What is queued up is thrown away, only what is running is
        waited for, and then the signal line is cut.
        """
        from .workers import silent_disconnect

        try:
            self._pool.clear()
            self._pool.waitForDone(10000)
        except RuntimeError:
            pass
        silent_disconnect(self._signals.loaded)

    def _pixmap_size(self, pixmap: QPixmap) -> int:
        return max(0, pixmap.width() * pixmap.height() * max(1, pixmap.depth()) // 8)

    def _trim_cache(self) -> None:
        """Throws away the oldest first once the size limit is passed.

        A thrown-away entry has to come out of _requested as well. Otherwise,
        even when it comes back on screen it is filtered out as "already
        requested" and never gets drawn again.
        """
        while (
            self._pixmap_bytes > THUMBNAIL_CACHE_BYTES
            and len(self._pixmaps) > MIN_CACHED_THUMBNAILS
        ):
            key, pixmap = self._pixmaps.popitem(last=False)
            self._pixmap_bytes -= self._pixmap_size(pixmap)
            self._requested.discard(key)

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._records)

    def record_at(self, row: int) -> ImageRecord | None:
        return self._records[row] if 0 <= row < len(self._records) else None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        record = self._records[index.row()]

        if role == RECORD_ROLE:
            return record
        if role == Qt.DisplayRole:
            return record.path.name
        if role == Qt.DecorationRole:
            key = str(record.path)
            pixmap = self._pixmaps.get(key)
            if pixmap is not None:
                self._pixmaps.move_to_end(key)  # mark as most recently used
                return pixmap
            self._request_thumbnail(record.path)
            return None
        if role == Qt.ToolTipRole:
            return self._tooltip(record)
        return None

    def _tooltip(self, record: ImageRecord) -> str:
        lines = [record.path.name,
                 tr("Score {score:.1f} · {grade}").format(
                     score=record.score, grade=record.final_grade.value)]
        if record.metadata:
            meta = record.metadata
            parts = [p for p in [
                meta.lens_model,
                f"ISO {meta.iso}" if meta.iso else None,
                meta.shutter_display,
                f"f/{meta.aperture:g}" if meta.aperture else None,
            ] if p]
            if parts:
                lines.append(" · ".join(parts))
        lines.extend(render_all(record.reasons))
        return "\n".join(lines)

    def _request_thumbnail(self, path: Path) -> None:
        key = str(path)
        if key in self._requested:
            return
        self._requested.add(key)
        self._pool.start(ThumbnailTask(path, self.cache_dir, self._signals))

    def _on_thumbnail(self, path_str: str, image) -> None:
        # The worker hands over a QImage. The conversion to QPixmap is done
        # here, on the GUI thread.
        pixmap = QPixmap.fromImage(image) if image is not None and not image.isNull() else QPixmap()
        previous = self._pixmaps.pop(path_str, None)
        if previous is not None:
            self._pixmap_bytes -= self._pixmap_size(previous)
        self._pixmaps[path_str] = pixmap
        self._pixmap_bytes += self._pixmap_size(pixmap)
        self._trim_cache()
        for row, record in enumerate(self._records):
            if str(record.path) == path_str:
                index = self.index(row, 0)
                self.dataChanged.emit(index, index, [Qt.DecorationRole])
                break


class ThumbnailDelegate(QStyledItemDelegate):
    """Draws the thumbnail + the grade colour border + the score badge."""

    PADDING = 16
    """The margin left and right of the thumbnail. Working out the grid cell
    width leans on this value."""

    LABEL_HEIGHT = 34
    """The space below, for writing the file name and the score."""

    def __init__(self, thumb_size: int = 180, parent=None):
        super().__init__(parent)
        self.thumb_size = thumb_size

    def sizeHint(self, option, index) -> QSize:
        return QSize(self.thumb_size + self.PADDING,
                     self.thumb_size + self.LABEL_HEIGHT)

    def paint(self, painter: QPainter, option, index) -> None:
        record: ImageRecord = index.data(RECORD_ROLE)
        if record is None:
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        rect = option.rect.adjusted(4, 4, -4, -4)
        grade_color = GRADE_COLORS.get(record.final_grade, QColor(120, 120, 120))
        selected = bool(option.state & QStyle.State_Selected)

        # Selection is a different signal from the grade. It used to be a
        # translucent blue rectangle, which mixed with the grade border and
        # left you unsure what was selected. The whole card is laid on a
        # bright panel and ringed with a thick border to tell them apart for
        # certain.
        if selected:
            painter.setBrush(QColor(58, 74, 100))
            painter.setPen(QPen(QColor(140, 180, 255), 2))
            painter.drawRoundedRect(option.rect.adjusted(1, 1, -1, -1), 6, 6)

        image_rect = QRect(rect.x(), rect.y(), rect.width(), rect.height() - 22)
        painter.setBrush(QColor(24, 24, 26))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(image_rect, 4, 4)

        pixmap: QPixmap = index.data(Qt.DecorationRole)
        if pixmap and not pixmap.isNull():
            scaled = pixmap.scaled(
                image_rect.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            target = QRect(0, 0, scaled.width(), scaled.height())
            target.moveCenter(image_rect.center())
            painter.drawPixmap(target, scaled)
        else:
            painter.setPen(QColor(90, 90, 98))
            painter.drawText(image_rect, Qt.AlignCenter, "…")

        # Grade - a thin border alone does not register while sweeping
        # through 3000 photos. A solid colour band is laid across the top
        # with the text on it, so it reads even with colour blindness.
        band = QRect(image_rect.left(), image_rect.top(), image_rect.width(), 18)
        painter.setBrush(grade_color)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(band, 4, 4)
        painter.drawRect(band.adjusted(0, 8, 0, 0))  # bottom corners square

        font = QFont(painter.font())
        font.setPointSize(8)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(20, 20, 24))
        label = GRADE_LABELS.get(record.final_grade, "")
        if record.manual_grade is not None:
            label += " ✋"   # a grade a person changed by hand
        painter.drawText(band.adjusted(6, 0, -6, 0), Qt.AlignVCenter | Qt.AlignLeft,
                         label)

        # The score is what the ordering is based on, so it has to show large
        painter.drawText(band.adjusted(6, 0, -6, 0),
                         Qt.AlignVCenter | Qt.AlignRight, f"{record.score:.0f}")

        painter.setPen(QPen(grade_color, 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(image_rect.adjusted(0, 0, -1, -1), 4, 4)

        text_rect = QRect(rect.x(), rect.bottom() - 18, rect.width(), 18)
        font.setBold(selected)
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(QColor(235, 235, 240) if selected else QColor(150, 150, 158))
        painter.drawText(text_rect, Qt.AlignCenter, record.path.name)

        painter.restore()


class ThumbnailGrid(QListView):
    record_activated = Signal(object)

    def __init__(self, cache_dir: Path, parent=None):
        super().__init__(parent)
        self.model_ = RecordListModel(cache_dir, self)
        self.setModel(self.model_)
        self.delegate = ThumbnailDelegate(parent=self)
        self.setItemDelegate(self.delegate)

        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(True)  # far less layout maths for 4000 photos
        self.setSelectionMode(QListView.ExtendedSelection)
        # The cells are set directly with gridSize instead of spacing. To fill
        # a row with N of them and no remainder, we have to decide the cell
        # width ourselves (_apply_thumb_size).
        self.setSpacing(0)
        # **The vertical scrollbar is kept on at all times.** Letting it come
        # and go produces an endless oscillation: the scrollbar disappears ->
        # the viewport widens -> one more column fits -> the cells shrink and
        # the total height drops -> the scrollbar is not needed... round that
        # loop goes, with the grid juddering between 2 and 3 columns (an
        # actual report, reproduced at the maximum size). Kept on, the
        # viewport width stops depending on the content and the loop itself
        # is broken.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self._desired_thumb = self.delegate.thumb_size
        self._resize_pending = False
        self.viewport().installEventFilter(self)
        self.setStyleSheet("QListView { background: #1b1b1d; border: none; }")

        self.doubleClicked.connect(self._on_double_click)

    def _on_double_click(self, index) -> None:
        record = index.data(RECORD_ROLE)
        if record is not None:
            self.record_activated.emit(record)

    def set_records(self, records, cache_dir: Path | None = None) -> None:
        self.model_.set_records(records, cache_dir)

    def selected_records(self) -> list[ImageRecord]:
        return [i.data(RECORD_ROLE) for i in self.selectedIndexes()]

    def set_thumb_size(self, size: int) -> None:
        """The **desired** size the user picked. The real size is fitted here
        so that a row comes out exact."""
        self._desired_thumb = max(40, int(size))
        self._apply_thumb_size()

    CELL_GAP = 3
    """The gap between cells (pixels). The grid is set directly with gridSize,
    so this is used instead of spacing."""

    def _apply_thumb_size(self) -> None:
        """To the size closest to the desired one that **leaves no margin on
        the right**.

        The slider value used to be used as-is. That leaves a remainder of
        less than one cell on the right whenever the viewport width is not a
        multiple of the cell width - at a width of 1200px and a cell of 200px
        it comes out exact, but at a cell of 190px, 6 cells make 1140px and
        60px is simply thrown away.

        Deciding the column count first and dividing the width by that count
        brings the remainder below the column count (a few pixels at most).

        **This is called again every time the viewport width changes.** When
        the scoring criteria or the queue panel appears on the right the grid
        width changes, and without re-fitting then, a remainder appears every
        time a panel is opened.
        """
        width = self.viewport().width()
        if width <= 0:
            return

        desired_cell = self._desired_thumb + self.delegate.PADDING + self.CELL_GAP * 2
        # **Rounding, not flooring.** Deciding the column count by flooring
        # pours all the leftover width into the cell size, so you ask for
        # 300px and get 378px - the slider spins for nothing. Rounding puts
        # the real size closest to what was asked for.
        columns = max(1, round(width / max(1, desired_cell)))
        cell = max(1, width // columns)
        thumb = max(40, cell - self.delegate.PADDING - self.CELL_GAP * 2)

        if thumb == self.delegate.thumb_size and self.gridSize().width() == cell:
            return
        self.delegate.thumb_size = thumb
        self.setGridSize(QSize(cell, thumb + self.delegate.LABEL_HEIGHT
                               + self.CELL_GAP * 2))
        self.model_.layoutChanged.emit()

    def columns(self) -> int:
        """How many cells fit in a row right now. For tests and diagnostics."""
        cell = self.gridSize().width()
        if cell <= 0:
            return 0
        return max(1, self.viewport().width() // cell)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # **The calculation must not happen right here.** At resizeEvent time
        # viewport().width() is still the old value. Used as-is, the column
        # count from when it was wide stays put, so opening the scoring
        # criteria or the queue panel on the right leaves only one column
        # even though the grid has narrowed, and the right side is empty (an
        # actual report).
        #
        # Measured after one turn of the event loop, the updated width comes
        # out.
        self._schedule_thumb_size()

    def eventFilter(self, watched, event):
        """Listens directly for the **viewport's** size changes.

        At the widget's resizeEvent time viewport().width() is still the old
        value. The cell width is calculated against the viewport, so listening
        only on the widget side leaves the column count from when it was wide
        in place - open the scoring criteria and the queue on the right
        together and the grid narrows to less than half, yet the columns do
        not reduce and the right side is left empty.

        The viewport resize arrives after the width has settled.
        """
        if watched is self.viewport() and event.type() == QEvent.Resize:
            self._schedule_thumb_size()
        return super().eventFilter(watched, event)

    def _schedule_thumb_size(self) -> None:
        """Re-fits just once, on the next turn of the event loop.

        While resizes come one after another (dragging the window) the
        bookings are folded into one, so the grid is not rebuilt for every
        pixel.
        """
        if self._resize_pending:
            return
        self._resize_pending = True

        def run() -> None:
            self._resize_pending = False
            self._apply_thumb_size()

        QTimer.singleShot(0, run)

    def refresh(self) -> None:
        """Redraws when a grade has changed."""
        self.viewport().update()
