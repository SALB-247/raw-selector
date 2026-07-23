"""Preset save / load row.

Grading criteria and develop settings share this widget. Keeping save,
load and delete on one row is what stops "which preset am I actually
looking at" from becoming a question.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.presets import PresetStore, safe_filename
from .i18n import tr

#: Index of the "nothing selected" row. Code checks this rather than
#: comparing the caption: the caption is translated, so a text comparison
#: silently stops matching in any language but English.
UNSAVED_INDEX = 0


class PresetBar(QWidget):
    """Preset picker plus save and delete.

    `collect` returns the current settings as a dict; `apply` pushes a dict
    back into the UI.
    """

    applied = Signal()

    def __init__(
        self,
        store: PresetStore,
        collect: Callable[[], dict[str, Any]],
        apply: Callable[[dict[str, Any]], None],
        parent=None,
    ):
        super().__init__(parent)
        self.store = store
        self._collect = collect
        self._apply = apply
        self._loading = False

        # A combo plus four buttons on one row needs 480px, and the right
        # end gets cut off in a narrow panel. Two rows fit at any width.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.combo = QComboBox()
        self.combo.setMinimumWidth(120)
        # Show them all at once as the list grows, rather than scrolling
        self.combo.setMaxVisibleItems(24)
        self.combo.currentIndexChanged.connect(self._on_selected)
        layout.addWidget(self.combo)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(4)

        self.save_button = QPushButton(tr("Save"))
        self.save_button.setToolTip(tr("Save the current settings as a preset"))
        self.save_button.clicked.connect(self.save_as)
        buttons.addWidget(self.save_button)

        self.import_button = QPushButton(tr("Import"))
        self.import_button.setToolTip(tr(
            "Load a preset file (.yaml) into the list.\n"
            "Presets from another machine or folder work as they are."
        ))
        self.import_button.clicked.connect(self.import_preset)
        buttons.addWidget(self.import_button)

        self.export_button = QPushButton(tr("Export"))
        self.export_button.setToolTip(
            tr("Write the selected preset to a file, for backup or sharing"))
        self.export_button.clicked.connect(self.export_current)
        buttons.addWidget(self.export_button)

        self.delete_button = QPushButton(tr("Delete"))
        self.delete_button.clicked.connect(self.delete_current)
        buttons.addWidget(self.delete_button)

        layout.addLayout(buttons)
        self.refresh()

    # ------------------------------------------------------------ the list

    def refresh(self, select: str | None = None) -> None:
        self._loading = True
        self.combo.clear()
        self.combo.addItem(tr("(unsaved)"))
        for info in self.store.list():
            self.combo.addItem(info.name)

        if select:
            index = self.combo.findText(select)
            if index >= 0:
                self.combo.setCurrentIndex(index)
        self._loading = False
        self._update_buttons()

    def _update_buttons(self) -> None:
        has_selection = self.combo.currentIndex() > UNSAVED_INDEX
        self.delete_button.setEnabled(has_selection)
        self.export_button.setEnabled(has_selection)

    def mark_modified(self) -> None:
        """Clear the preset selection once settings are edited by hand.

        Leaving the preset name in place makes it look like the values on
        screen are that preset, when they are no longer.
        """
        if self._loading or self.combo.currentIndex() == UNSAVED_INDEX:
            return
        self._loading = True
        self.combo.setCurrentIndex(UNSAVED_INDEX)
        self._loading = False
        self._update_buttons()

    # ------------------------------------------------------------ actions

    def _on_selected(self, index: int) -> None:
        if self._loading or index <= UNSAVED_INDEX:
            self._update_buttons()
            return

        name = self.combo.currentText()
        try:
            data = self.store.load(name)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(
                self, tr("Preset"), tr("Could not load:\n{error}").format(error=exc))
            self.refresh()
            return

        self._loading = True
        self._apply(data)
        self._loading = False
        self._update_buttons()
        self.applied.emit()

    def save_as(self) -> None:
        index = self.combo.currentIndex()
        suggested = "" if index == UNSAVED_INDEX else self.combo.currentText()

        name, ok = QInputDialog.getText(
            self, tr("Save preset"), tr("Name"), text=suggested)
        if not ok or not name.strip():
            return
        name = name.strip()

        if self.store.exists(name):
            answer = QMessageBox.question(
                self, tr("Save preset"),
                tr("'{name}' already exists. Overwrite?").format(name=name),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        try:
            self.store.save(name, self._collect())
        except OSError as exc:
            QMessageBox.warning(
                self, tr("Preset"), tr("Could not save:\n{error}").format(error=exc))
            return
        self.refresh(select=name)

    def import_preset(self) -> None:
        """Pick a preset file (.yaml), add it to the list and apply it.

        The preset folder differs between machines and setups (virtualised
        paths and so on), so handing the file over directly is the reliable
        way to move one.
        """
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Import preset"), "",
            tr("Presets (*.yaml *.yml);;All files (*)")
        )
        if not path:
            return

        try:
            data = self.store.load(Path(path))  # validates the format too
        except (OSError, ValueError) as exc:
            QMessageBox.warning(
                self, tr("Import preset"),
                tr("Could not load:\n{error}").format(error=exc))
            return

        name = safe_filename(Path(path).stem)
        if self.store.exists(name):
            answer = QMessageBox.question(
                self, tr("Import preset"),
                tr("'{name}' already exists. Overwrite?\n"
                   "Choose No to save it under a different name.").format(name=name),
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.No,
            )
            if answer == QMessageBox.Cancel:
                return
            if answer == QMessageBox.No:
                new_name, ok = QInputDialog.getText(
                    self, tr("Import preset"), tr("New name"),
                    text=tr("{name} copy").format(name=name)
                )
                if not ok or not new_name.strip():
                    return
                name = new_name.strip()

        try:
            self.store.save(name, data)
        except OSError as exc:
            QMessageBox.warning(
                self, tr("Import preset"),
                tr("Could not save:\n{error}").format(error=exc))
            return

        self.refresh(select=name)
        self._loading = True
        self._apply(data)
        self._loading = False
        self.applied.emit()

    def export_current(self) -> None:
        """Write the selected preset out to a file."""
        if self.combo.currentIndex() <= UNSAVED_INDEX:
            return
        name = self.combo.currentText()
        source = self.store.path_for(name)
        if not source.exists():
            QMessageBox.warning(
                self, tr("Export preset"), tr("The source file is missing."))
            return

        target, _ = QFileDialog.getSaveFileName(
            self, tr("Export preset"), f"{name}.yaml", tr("Presets (*.yaml)")
        )
        if not target:
            return
        try:
            Path(target).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(
                self, tr("Export preset"),
                tr("Could not save:\n{error}").format(error=exc))

    def delete_current(self) -> None:
        if self.combo.currentIndex() <= UNSAVED_INDEX:
            return
        name = self.combo.currentText()
        answer = QMessageBox.question(
            self, tr("Delete preset"),
            tr("Delete '{name}'?").format(name=name),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.store.delete(name)
        self.refresh()
