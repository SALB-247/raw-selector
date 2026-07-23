"""썸네일 격자.

4000장을 QListWidget에 아이템으로 밀어 넣으면 메모리와 시작 시간이 감당이
안 됩니다. 모델/델리게이트로 만들고 썸네일은 화면에 보이는 것만 비동기로
읽어 옵니다.
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
"""등급을 글자로도 적습니다. 색만으로는 색각 이상이 있으면 구분이 안 됩니다."""

RECORD_ROLE = Qt.UserRole + 1


THUMBNAIL_CACHE_BYTES = 128 * 1024 * 1024
"""화면에 올린 썸네일을 붙들고 있을 최대 용량.

예전에는 상한이 없어서, 스크롤하며 지나간 썸네일이 전부 램에 남았습니다.
3000장 폴더에서 수백 MB가 되고 8GB PC에서는 그만큼 다른 곳이 좁아집니다.
버려도 디스크 썸네일 캐시에서 곧바로 다시 읽으므로(수십 KB JPEG) 스크롤
체감은 거의 그대롭니다.
"""

MIN_CACHED_THUMBNAILS = 60
"""용량과 무관하게 최소한 유지할 장수. 한 화면분은 남아 있어야 합니다."""


class RecordListModel(QAbstractListModel):
    def __init__(self, cache_dir: Path, parent=None):
        super().__init__(parent)
        self._records: list[ImageRecord] = []
        # 최근에 쓴 것이 뒤로 가는 LRU. 넘치면 앞에서부터 버립니다.
        self._pixmaps: "OrderedDict[str, QPixmap]" = OrderedDict()
        self._pixmap_bytes = 0
        self._requested: set[str] = set()
        self.cache_dir = cache_dir

        self._pool = QThreadPool()
        # 썸네일 읽기로 코어를 다 먹으면 UI가 버벅입니다
        self._pool.setMaxThreadCount(max(2, QThreadPool.globalInstance().maxThreadCount() // 2))
        self._signals = ThumbnailSignals()
        self._signals.loaded.connect(self._on_thumbnail)

    def set_records(self, records: list[ImageRecord], cache_dir: Path | None = None) -> None:
        self.beginResetModel()
        self._records = records
        if cache_dir is not None and cache_dir != self.cache_dir:
            # 다른 폴더로 갈아탔으면 이전 썸네일은 다시 볼 일이 없습니다.
            # 예전에는 이걸 안 비워서 폴더를 옮길수록 램이 쌓였습니다.
            self._pixmaps.clear()
            self._pixmap_bytes = 0
        if cache_dir is not None:
            self.cache_dir = cache_dir
        self._requested.clear()
        self.endResetModel()

    def shutdown(self) -> None:
        """썸네일 작업을 세웁니다.

        QRunnable은 끝나면서 시그널을 쏘는데, 그 시점에 모델이 이미
        지워져 있으면 없는 객체의 슬롯을 부릅니다. 큐에 쌓인 것은 버리고
        도는 것만 기다린 뒤, 신호선을 끊어 둡니다.
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
        """용량을 넘으면 오래된 것부터 버립니다.

        버린 항목은 _requested에서도 빼야 합니다. 안 그러면 다시 화면에
        들어와도 '이미 요청함'으로 걸러져 영영 안 그려집니다.
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
                self._pixmaps.move_to_end(key)  # 최근에 쓴 것으로 표시
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
        # 워커는 QImage를 넘긴다. QPixmap 변환은 GUI 스레드인 여기서 합니다.
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
    """썸네일 + 등급 색 테두리 + 점수 배지를 그립니다."""

    PADDING = 16
    """썸네일 좌우에 두는 여백. 격자 칸 폭 계산이 이 값에 기댑니다."""

    LABEL_HEIGHT = 34
    """파일명·점수를 적을 아래 공간."""

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

        # 선택은 등급과 다른 신호입니다. 예전에는 반투명 파란 사각형이라
        # 등급 테두리와 섞여 무엇이 선택된 건지 헷갈렸습니다. 카드 전체를
        # 밝은 판으로 깔고 굵은 테두리를 둘러 확실히 구분합니다.
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

        # 등급 — 얇은 테두리만으로는 3000장을 훑을 때 눈에 안 들어옵니다.
        # 위쪽에 꽉 찬 색 띠를 깔고 글자를 얹어 색맹이어도 읽히게 합니다.
        band = QRect(image_rect.left(), image_rect.top(), image_rect.width(), 18)
        painter.setBrush(grade_color)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(band, 4, 4)
        painter.drawRect(band.adjusted(0, 8, 0, 0))  # 아래쪽 모서리는 각지게

        font = QFont(painter.font())
        font.setPointSize(8)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(20, 20, 24))
        label = GRADE_LABELS.get(record.final_grade, "")
        if record.manual_grade is not None:
            label += " ✋"   # 사람이 직접 바꾼 등급
        painter.drawText(band.adjusted(6, 0, -6, 0), Qt.AlignVCenter | Qt.AlignLeft,
                         label)

        # 점수는 정렬의 기준이라 크게 보여야 합니다
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
        self.setUniformItemSizes(True)  # 4000장 레이아웃 계산을 크게 줄입니다
        self.setSelectionMode(QListView.ExtendedSelection)
        # spacing 대신 gridSize로 칸을 직접 잡습니다. 자투리 없이 한 줄에
        # N개를 채우려면 칸 폭을 우리가 정해야 합니다 (_apply_thumb_size).
        self.setSpacing(0)
        # **세로 스크롤바를 항상 켜 둡니다.** 껐다 켰다 하게 두면 무한 진동이
        # 생깁니다: 스크롤바가 사라짐 → 뷰포트가 넓어짐 → 열이 하나 늘어남 →
        # 칸이 작아져 전체 높이가 줄어듦 → 스크롤바가 필요 없어짐… 이 고리가
        # 돌면서 격자가 2열↔3열로 계속 떨립니다(실제 리포트, 최대 크기에서 재현).
        # 항상 켜 두면 뷰포트 폭이 내용과 무관해져 고리 자체가 끊깁니다.
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
        """사용자가 고른 **희망** 크기. 실제 크기는 여기서 한 줄에 딱 맞게 맞춥니다."""
        self._desired_thumb = max(40, int(size))
        self._apply_thumb_size()

    CELL_GAP = 3
    """칸 사이 여백 (픽셀). 격자를 gridSize로 직접 잡으므로 spacing 대신 씁니다."""

    def _apply_thumb_size(self) -> None:
        """희망 크기에 가장 가까우면서 **오른쪽에 여백이 남지 않는** 크기로.

        예전에는 슬라이더 값을 그대로 썼습니다. 그러면 뷰포트 폭이 칸 폭의
        배수가 아닐 때 오른쪽에 한 칸이 안 되는 자투리가 남습니다 — 폭이
        1200px이고 칸이 200px이면 딱 맞지만, 칸이 190px이면 6칸 1140px에
        60px이 그냥 버려집니다.

        열 개수를 먼저 정하고 그 개수로 폭을 나누면 자투리가 열 개수 미만
        (최대 몇 픽셀)으로 줄어듭니다.

        **뷰포트 폭이 바뀔 때마다 다시 부릅니다.** 우측에 판정 기준이나
        대기열 패널이 나타나면 격자 폭이 달라지는데, 그때 다시 안 맞추면
        패널을 열 때마다 자투리가 생깁니다.
        """
        width = self.viewport().width()
        if width <= 0:
            return

        desired_cell = self._desired_thumb + self.delegate.PADDING + self.CELL_GAP * 2
        # **내림이 아니라 반올림**입니다. 내림으로 열 수를 정하면 남는 폭이
        # 전부 칸 크기로 들어가, 300px을 요청했는데 378px이 나오는 식으로
        # 슬라이더가 헛돕니다. 반올림하면 실제 크기가 요청에 가장 가깝습니다.
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
        """지금 한 줄에 들어가는 칸 수. 테스트와 진단용."""
        cell = self.gridSize().width()
        if cell <= 0:
            return 0
        return max(1, self.viewport().width() // cell)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # **여기서 바로 계산하면 안 됩니다.** resizeEvent 시점의
        # viewport().width()는 아직 예전 값입니다. 그대로 쓰면 넓었을 때의
        # 열 수가 그대로 남아, 우측에 판정 기준·대기열 패널을 열면 격자가
        # 좁아졌는데도 한 열만 남고 오른쪽이 텅 빕니다(실제 리포트).
        #
        # 이벤트 루프를 한 바퀴 돌린 뒤에 재면 갱신된 폭이 나옵니다.
        self._schedule_thumb_size()

    def eventFilter(self, watched, event):
        """**뷰포트**의 크기 변화를 직접 듣습니다.

        위젯의 resizeEvent 시점에는 viewport().width()가 아직 예전 값입니다.
        칸 폭은 뷰포트 기준으로 계산하므로, 위젯 쪽만 듣고 있으면 넓었을 때의
        열 수가 그대로 남습니다 — 우측에 판정 기준·대기열을 함께 열면 격자가
        절반 이하로 좁아지는데도 열이 안 줄어 오른쪽이 텅 빕니다.

        뷰포트 리사이즈는 폭이 확정된 뒤에 옵니다.
        """
        if watched is self.viewport() and event.type() == QEvent.Resize:
            self._schedule_thumb_size()
        return super().eventFilter(watched, event)

    def _schedule_thumb_size(self) -> None:
        """다음 이벤트 루프에서 한 번만 다시 맞춥니다.

        연속으로 리사이즈될 때(창을 끄는 중) 매 픽셀마다 격자를 다시 짜지
        않도록 예약을 하나로 묶습니다.
        """
        if self._resize_pending:
            return
        self._resize_pending = True

        def run() -> None:
            self._resize_pending = False
            self._apply_thumb_size()

        QTimer.singleShot(0, run)

    def refresh(self) -> None:
        """등급이 바뀌었을 때 다시 그립니다."""
        self.viewport().update()
