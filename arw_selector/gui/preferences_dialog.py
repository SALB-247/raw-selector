"""Preferences: interface language, update checking, licences.

Language changes apply on the next start. Qt can swap a translator at
runtime, but every caption already built keeps the text it was created
with — so a live switch would leave half the window in each language
unless every widget handled `LanguageChange`. Saying "restart" is honest;
swapping half the window is not.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..core import state, updates
from ..core.appinfo import APP_NAME, app_root
from . import theme
from .i18n import tr

#: Selectable language codes. "" means follow the system, which is what
#: `i18n.install` already understands.
#:
#: Only the codes live here. A module-level list of captions would be built
#: at import time and then stop following the language — the same trap this
#: whole exercise keeps running into.
LANGUAGE_CODES: tuple[str, ...] = ("", "en", "ko")


def language_caption(code: str) -> str:
    """Menu caption for a language code.

    Language names stay in their own language — someone looking for Korean
    is looking for "한국어", not for whatever the current interface calls
    it. Only "follow the system" is translated.
    """
    if code == "":
        return tr("System default")
    return {"en": "English", "ko": "한국어"}.get(code, code)


def _read_licence_text() -> str:
    """LICENSE and THIRD_PARTY.md as one block of text.

    Read from disk rather than embedded in the source: the two must not be
    able to disagree, and the files are what ships.
    """
    parts = []
    for name in ("LICENSE", "THIRD_PARTY.md"):
        path = app_root() / name
        try:
            parts.append(f"===== {name} =====\n\n" + path.read_text(encoding="utf-8"))
        except OSError:
            parts.append(f"===== {name} =====\n\n" + tr("(file not found: {path})")
                         .format(path=path))
    return "\n\n\n".join(parts)


class PreferencesDialog(QDialog):
    """Settings that are not about grading or developing."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Preferences"))
        self.resize(620, 520)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._build_general_tab(), tr("General"))
        tabs.addTab(self._build_about_tab(), tr("About"))
        layout.addWidget(tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------ general

    def _build_general_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        language_box = QGroupBox(tr("Language"))
        form = QFormLayout(language_box)

        self.language_combo = QComboBox()
        current = state.language() or ""
        for code in LANGUAGE_CODES:
            self.language_combo.addItem(language_caption(code), code)
        index = self.language_combo.findData(current)
        self.language_combo.setCurrentIndex(max(0, index))
        form.addRow(tr("Interface language"), self.language_combo)

        note = QLabel(tr("Takes effect the next time the app starts."))
        note.setStyleSheet(theme.hint_label())
        form.addRow(note)
        outer.addWidget(language_box)

        update_box = QGroupBox(tr("Updates"))
        update_layout = QVBoxLayout(update_box)

        self.update_check = QCheckBox(tr("Check for updates"))
        self.update_check.setChecked(state.update_check_enabled())
        self.update_check.setToolTip(tr(
            "Off by default. Checking contacts a server and tells it which\n"
            "version is running here. Nothing is sent unless you ask."
        ))
        update_layout.addWidget(self.update_check)

        row = QHBoxLayout()
        self.check_now = QPushButton(tr("Check now"))
        self.check_now.clicked.connect(self._check_for_updates)
        row.addWidget(self.check_now)
        row.addStretch(1)
        update_layout.addLayout(row)

        self.update_status = QLabel()
        self.update_status.setWordWrap(True)
        self.update_status.setStyleSheet(theme.hint_label())
        update_layout.addWidget(self.update_status)

        outer.addWidget(update_box)
        outer.addStretch(1)
        return page

    def _check_for_updates(self) -> None:
        """Runs only on this button. See core/updates.py."""
        self.check_now.setEnabled(False)
        self.update_status.setText(tr("Checking…"))
        # Repaint before the blocking call, or the label stays blank for the
        # whole timeout and the button looks dead.
        self.update_status.repaint()

        result = updates.check(__version__)
        self.check_now.setEnabled(True)

        if result.error == "not_configured":
            self.update_status.setText(
                tr("No update source is configured for this build."))
        elif result.error == "unreachable":
            self.update_status.setText(
                tr("Could not reach the update server."))
        elif result.error:
            self.update_status.setText(
                tr("The update server replied with something unreadable."))
        elif result.latest:
            self.update_status.setText(
                tr("Version {latest} is available (this is {current}).").format(
                    latest=result.latest, current=__version__))
            if result.url:
                QDesktopServices.openUrl(result.url)
        else:
            self.update_status.setText(
                tr("This is the latest version ({current}).").format(
                    current=__version__))

    # ------------------------------------------------------------ about

    def _build_about_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        title = QLabel(f"{APP_NAME} {__version__}")
        title.setStyleSheet(f"color: {theme.TEXT}; font-weight: 600; font-size: 14px;")
        outer.addWidget(title)

        summary = QLabel(tr("RAW focus selection and develop tool."))
        summary.setStyleSheet(theme.hint_label())
        outer.addWidget(summary)

        licences = QPlainTextEdit()
        licences.setReadOnly(True)
        licences.setPlainText(_read_licence_text())
        licences.setLineWrapMode(QPlainTextEdit.NoWrap)
        outer.addWidget(licences, 1)

        note = QLabel(tr(
            "This project's own code is MIT licensed. Bundled data and the\n"
            "libraries used by the packaged build keep their own terms — "
            "PySide6 in particular is LGPL-3.0."
        ))
        note.setWordWrap(True)
        note.setStyleSheet(theme.hint_label())
        outer.addWidget(note)
        return page

    # ------------------------------------------------------------ result

    def accept(self) -> None:
        state.set_language(self.language_combo.currentData() or None)
        state.set_update_check(self.update_check.isChecked())
        super().accept()

    def language_changed_from(self, previous: str | None) -> bool:
        """Whether the choice differs from what was active — for the restart note."""
        return (self.language_combo.currentData() or None) != (previous or None)
