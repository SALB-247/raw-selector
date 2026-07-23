"""내보내기 대기열 패널.

여러 폴더를 돌며 "이 컷들은 이 프리셋으로" 를 쌓아두고 마지막에 한 번에
내보냅니다. 현상까지 하면 장당 수백 ms라, 작업할 때마다 기다리는 대신
모아서 돌리는 편이 낫습니다.

목록이 아니라 표로 보여 줍니다. 파일 / 보정 / 크롭 세 가지를 한눈에 대조해야
"이 컷에 뭐가 걸려 있더라"를 확인할 수 있습니다.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.develop import DevelopSettings
from ..core.export_queue import ExportQueue
from ..core.presets import develop_presets
from . import theme
from .i18n import tr

# These double as combo captions *and* as the values compared against
# `currentText()`. Module-level constants would be built once at import and
# then stop matching in any language but the one active at that moment, so
# they are functions.
def CUSTOM() -> str:
    return tr("(per-photo edit)")


def NONE_LABEL() -> str:
    return tr("(no edit)")


def COLUMNS() -> tuple[str, ...]:
    return (tr("File"), tr("Develop preset"), tr("Crop"), tr("Grade"))


class QueuePanel(QWidget):
    """대기열 표 + 제거/비우기/저장/불러오기."""

    export_requested = Signal()
    edit_requested = Signal(object)  # Path — 그 컷을 보정 화면에서 엽니다
    changed = Signal()

    def __init__(self, queue: ExportQueue, parent=None):
        super().__init__(parent)
        self.queue = queue
        self.store = develop_presets()
        self._loading = False
        self.setFixedWidth(430)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        self.title = QLabel(tr("Queue"))
        self.title.setStyleSheet("font-weight: bold; color: #ddd;")
        layout.addWidget(self.title)

        self.table = QTableWidget(0, len(COLUMNS()))
        self.table.setHorizontalHeaderLabels(COLUMNS())
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setStyleSheet(theme.TABLE)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        self.table.setToolTip(tr("Double-click to edit in the develop window"))

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        layout.addWidget(self.table, 1)

        layout.addLayout(self._build_bulk_row())

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("color: #9a9aa2; font-size: 11px;")
        layout.addWidget(self.summary)

        row = QHBoxLayout()
        self.remove_button = QPushButton(tr("Remove selected"))
        self.remove_button.clicked.connect(self.remove_selected)
        row.addWidget(self.remove_button)

        self.clear_button = QPushButton(tr("Clear"))
        self.clear_button.clicked.connect(self.clear)
        row.addWidget(self.clear_button)
        layout.addLayout(row)

        file_row = QHBoxLayout()
        save = QPushButton(tr("Save"))
        save.setToolTip(tr("Save the queue to a file and pick it up next session"))
        save.clicked.connect(self.save_to_file)
        file_row.addWidget(save)

        load = QPushButton(tr("Load"))
        load.clicked.connect(self.load_from_file)
        file_row.addWidget(load)
        layout.addLayout(file_row)

        self.export_button = QPushButton(tr("Export queue"))
        self.export_button.setStyleSheet(theme.PRIMARY_BUTTON)
        self.export_button.clicked.connect(self.export_requested.emit)
        layout.addWidget(self.export_button)

        self.refresh()

    def _build_bulk_row(self) -> QHBoxLayout:
        """Change the preset on every selected row at once."""
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Selected rows:")))

        self.bulk_preset = QComboBox()
        self.bulk_preset.setMinimumWidth(140)
        row.addWidget(self.bulk_preset, 1)

        apply_button = QPushButton(tr("Apply"))
        apply_button.setToolTip(tr("Set the develop preset on the selected rows"))
        apply_button.clicked.connect(self.apply_preset_to_selection)
        row.addWidget(apply_button)

        return row

    # ------------------------------------------------------------ 갱신

    def _preset_names(self) -> list[str]:
        return [info.name for info in self.store.list()]

    def refresh(self) -> None:
        self._loading = True
        names = self._preset_names()

        self.bulk_preset.clear()
        self.bulk_preset.addItem(NONE_LABEL())
        self.bulk_preset.addItems(names)

        self.table.setRowCount(len(self.queue))
        for row, entry in enumerate(self.queue):
            missing = not entry.source.exists()

            name_item = QTableWidgetItem(entry.source.name)
            name_item.setData(Qt.UserRole, str(entry.source))
            tooltip = str(entry.source)
            if missing:
                name_item.setForeground(Qt.red)
                tooltip += tr("\n⚠ source is missing — it will be skipped on export")
            name_item.setToolTip(tooltip)
            self.table.setItem(row, 0, name_item)

            # Preset picker — either a per-photo edit or a saved preset
            combo = QComboBox()
            combo.addItems([NONE_LABEL(), CUSTOM(), *names])
            current = self._current_label(entry)
            index = combo.findText(current)
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.currentIndexChanged.connect(
                lambda _=0, r=row: self._on_preset_changed(r)
            )
            self.table.setCellWidget(row, 1, combo)

            crop = "○" if self._has_crop(entry) else "—"
            crop_item = QTableWidgetItem(crop)
            crop_item.setTextAlignment(Qt.AlignCenter)
            crop_item.setToolTip(
                tr("This photo has a crop or straighten applied") if crop == "○"
                else tr("No crop")
            )
            self.table.setItem(row, 2, crop_item)

            grade_item = QTableWidgetItem(entry.grade.value)
            grade_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 3, grade_item)

        count = len(self.queue)
        self.title.setText(tr("Queue ({count})").format(count=count))

        missing_count = len(self.queue.missing_sources())
        cropped = sum(1 for e in self.queue if self._has_crop(e))
        text = tr("{count} photos · {developed} edited · {cropped} cropped").format(
            count=count, developed=self.queue.develop_count, cropped=cropped)
        if missing_count:
            text += tr("\n⚠ {count} with a missing source will be skipped").format(
                count=missing_count)
        self.summary.setText(text)

        has_items = count > 0
        self.export_button.setEnabled(has_items)
        self.clear_button.setEnabled(has_items)
        self.remove_button.setEnabled(has_items)
        self._loading = False

    @staticmethod
    def _has_crop(entry) -> bool:
        return bool(
            entry.develop and not entry.develop.geometry.is_neutral()
        )

    @staticmethod
    def _current_label(entry) -> str:
        if not entry.has_develop:
            return NONE_LABEL()
        return entry.preset_name or CUSTOM()

    def _on_double_click(self, row: int, column: int) -> None:
        """행을 더블클릭하면 그 컷을 보정 화면에서 엽니다.

        프리셋 콤보 칸은 제외합니다 — 거기서는 더블클릭이 콤보 조작입니다.
        """
        if column == 1 or row >= len(self.queue.entries):
            return

        entry = self.queue.entries[row]
        if not entry.source.exists():
            QMessageBox.warning(
                self, tr("Queue"),
                tr("Source is missing:\n{path}").format(path=entry.source)
            )
            return
        self.edit_requested.emit(entry.source)

    # ------------------------------------------------------------ 프리셋

    def _on_preset_changed(self, row: int) -> None:
        if self._loading or row >= len(self.queue.entries):
            return

        combo = self.table.cellWidget(row, 1)
        if combo is None:
            return
        self._assign_preset(row, combo.currentText())
        self.refresh()
        self.changed.emit()

    def _assign_preset(self, row: int, label: str) -> None:
        """행에 프리셋을 적용합니다.

        크롭은 컷마다 구도가 달라서 프리셋으로 덮어쓰지 않습니다. 원래 잡아
        둔 크롭은 그대로 두고 색보정만 바꿉니다.
        """
        from dataclasses import replace

        entry = self.queue.entries[row]

        if label == NONE_LABEL():
            entry.develop = None
            entry.preset_name = None
            return
        if label == CUSTOM():
            entry.preset_name = None
            return  # leave the per-photo edit alone

        try:
            settings = DevelopSettings.from_dict(self.store.load(label))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(
                self, tr("Preset"), tr("Could not load:\n{error}").format(error=exc))
            return

        # 프리셋에는 그 프리셋을 만든 컷의 크롭과 마스크가 같이 저장돼
        # 있습니다. 다른 컷에 그대로 씌우면 엉뚱한 데가 잘리고 엉뚱한 데가
        # 보정됩니다. 예전에는 대기열 항목에 보정이 아직 없을 때
        # (entry.develop is None) 프리셋의 크롭이 딸려 들어와, 갓 넣은
        # 사진들이 남의 구도로 잘렸습니다.
        settings = settings.without_geometry()
        if entry.develop is not None:
            settings = replace(
                settings,
                geometry=entry.develop.geometry,
                masks=entry.develop.masks,
            )
        entry.develop = settings
        entry.preset_name = label

    def apply_preset_to_selection(self) -> None:
        rows = {index.row() for index in self.table.selectedIndexes()}
        if not rows:
            QMessageBox.information(
                self, tr("Queue"), tr("Select some rows first"))
            return

        label = self.bulk_preset.currentText()
        for row in sorted(rows):
            if row < len(self.queue.entries):
                self._assign_preset(row, label)

        self.refresh()
        self.changed.emit()

    # ------------------------------------------------------------ 동작

    def remove_selected(self) -> None:
        rows = {index.row() for index in self.table.selectedIndexes()}
        sources = [
            Path(self.table.item(row, 0).data(Qt.UserRole))
            for row in rows
            if self.table.item(row, 0)
        ]
        if not sources:
            return
        self.queue.remove(sources)
        self.refresh()
        self.changed.emit()

    def clear(self) -> None:
        if not len(self.queue):
            return
        answer = QMessageBox.question(
            self, tr("Queue"),
            tr("Clear all {count} entries?").format(count=len(self.queue)),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.queue.clear()
        self.refresh()
        self.changed.emit()

    def save_to_file(self) -> None:
        if not len(self.queue):
            QMessageBox.information(self, tr("Queue"), tr("Nothing to save"))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Save queue"), "queue.json", "JSON (*.json)"
        )
        if not path:
            return
        try:
            self.queue.save(Path(path))
        except OSError as exc:
            QMessageBox.warning(
                self, tr("Queue"), tr("Could not save:\n{error}").format(error=exc))

    def load_from_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Load queue"), "", "JSON (*.json)"
        )
        if not path:
            return
        try:
            loaded = ExportQueue.load(Path(path))
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.warning(
                self, tr("Queue"), tr("Could not load:\n{error}").format(error=exc))
            return

        # Merge into the existing queue — discarding what is already stacked
        # up just because a file was opened would lose real work
        added = updated = 0
        for entry in loaded:
            if self.queue.add(
                entry.source, entry.develop, entry.grade, entry.preset_name
            ):
                added += 1
            else:
                updated += 1

        self.refresh()
        self.changed.emit()
        QMessageBox.information(
            self, tr("Queue"),
            tr("{added} added, {updated} updated").format(
                added=added, updated=updated))
