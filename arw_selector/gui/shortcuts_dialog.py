"""The keyboard shortcut sheet - what F1 opens.

Every key was documented in the HOWTO appendix and nowhere in the app. A
culling tool lives on its keys, and a sheet you can open with one of them
is the difference between learning them and never finding out.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from .i18n import tr


def grid_shortcuts() -> list[tuple[str, str]]:
    return [
        ("1 / 2 / 3", tr("Grade the selection keep / review / reject")),
        ("0", tr("Clear the manual grade (back to automatic)")),
        ("Space", tr("Open the selected photo in the loupe")),
        ("D", tr("Open the Develop window for the selection")),
        ("Q", tr("Add the selection to the export queue")),
        ("C", tr("Compare two to four selected photos side by side")),
        ("Ctrl+Shift+C / V", tr("Copy the selected photo's develop settings / paste onto the selection (crop and masks kept)")),
        ("[ / ]", tr("Previous / next scene")),
        ("Esc", tr("Stop the running task (analysis or export)")),
        ("F1", tr("This sheet")),
        (tr("Double-click"), tr("Open the photo (Preview or Develop, per the toolbar mode)")),
    ]


def loupe_shortcuts() -> list[tuple[str, str]]:
    return [
        ("← / →", tr("Previous / next photo")),
        ("[ / ]", tr("Previous / next scene")),
        ("1 / 2 / 3", tr("Grade keep / review / reject")),
        ("B", tr("Original — before/after toggle")),
        ("F", tr("Focus overlay (grading region)")),
        ("A", tr("Faces overlay")),
        ("E", tr("Eyes overlay")),
        ("P", tr("AF point overlay")),
        ("Z", tr("Zoom to focus")),
        ("Q", tr("Add to queue")),
        ("Enter / Esc", tr("Apply / cancel the crop while its handles are up")),
        ("F1", tr("This sheet")),
        (tr("Mouse wheel"), tr("Zoom")),
        (tr("Drag"), tr("Pan")),
        (tr("Double-click"), tr("Reset the view")),
    ]


def compare_shortcuts() -> list[tuple[str, str]]:
    return [
        ("1 / 2 / 3 / 0", tr("Grade the active tile (bright border) / clear it")),
        ("← / →", tr("Move the active tile")),
        (tr("Wheel / drag"), tr("Zoom and pan every tile together")),
        ("Z", tr("Every tile on its own focus region")),
        ("R", tr("Reset the views")),
        ("Esc", tr("Close")),
    ]


def _table(title: str, rows: list[tuple[str, str]]) -> str:
    body = "".join(
        f"<tr><td style='padding:2px 14px 2px 0'><b>{keys}</b></td><td>{what}</td></tr>"
        for keys, what in rows
    )
    return f"<h3 style='margin-bottom:4px'>{title}</h3><table>{body}</table>"


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Keyboard shortcuts"))
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        layout = QVBoxLayout(self)
        text = QLabel(_table(tr("Grid (main window)"), grid_shortcuts())
                      + _table(tr("Loupe (Develop window)"), loupe_shortcuts())
                      + _table(tr("Compare window"), compare_shortcuts()))
        text.setTextFormat(Qt.RichText)
        text.setWordWrap(True)
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)
        layout.addWidget(buttons)


def show_shortcuts(owner) -> ShortcutsDialog:
    """Opens the sheet once per window and brings it forward after that."""
    dialog = getattr(owner, "_shortcuts_dialog", None)
    if dialog is None:
        dialog = ShortcutsDialog(owner)
        owner._shortcuts_dialog = dialog
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
