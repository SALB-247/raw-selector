"""Grade filter buttons.

A dropdown hides both the current filter and how many photos each grade
holds. Over a 4000-photo pass that is a constant irritation, so the
filters stay on screen with their counts.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QWidget

from ..core.types import Grade
from .i18n import tr

NO_KEEP = "no_keep"
"""Special filter: only scenes that produced no keep at all."""

#: (value, colour). The label is *not* stored here — a module-level list is
#: built at import time, which would freeze the wording in whatever language
#: happened to be active then. `_label_for` resolves it per call instead.
#:
#: "All" used to be grey (#8a8a92), which made the active button look like
#: the inactive ones. Which filter you are looking at is the single most
#: confusing thing on this screen, so the checked state is always a solid
#: colour.
FILTERS: list[tuple[object, str]] = [
    (None, "#7fb3ff"),
    (Grade.KEEP, "#4caf50"),
    (Grade.REVIEW, "#ffa726"),
    (Grade.REJECT, "#e55757"),
    (NO_KEEP, "#c9a06a"),
]


def _label_for(value: object) -> str:
    """Button caption before the counts are filled in."""
    if value is None:
        return tr("All")
    if value is NO_KEEP:
        return tr("Scenes with no keep")
    return str(value.value)  # keep / review / reject stay as they are


class FilterBar(QWidget):
    filter_changed = Signal(object)  # Grade | None

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[Grade | None, QPushButton] = {}

        for value, color in FILTERS:
            button = QPushButton(_label_for(value))
            button.setCheckable(True)
            button.setStyleSheet(self._style(color))
            button.clicked.connect(lambda _=False, v=value: self.filter_changed.emit(v))
            button.setMinimumWidth(130)
            self.group.addButton(button)
            layout.addWidget(button)
            self.buttons[value] = button

        # Without this the buttons stretch to the window width, which looks wrong
        layout.addStretch(1)
        self.buttons[None].setChecked(True)

    @staticmethod
    def _style(color: str) -> str:
        """Outline when off, filled when on.

        Unchecked buttons used to be filled grey, so all five read as one
        block. An outline lets each one be read separately and leaves the
        checked one standing out on its own.
        """
        return f"""
            QPushButton {{
                background: transparent; color: {color};
                border: 1px solid {color}; padding: 6px 16px;
                border-radius: 13px;
            }}
            QPushButton:hover {{ background: #2f2f35; }}
            QPushButton:checked {{
                background: {color}; color: #16161a; font-weight: bold;
                border-color: {color};
            }}
        """

    def update_counts(
        self, summary: dict[str, int], total: int, no_keep: int = 0
    ) -> None:
        """Write the per-grade counts into the buttons."""
        self.buttons[None].setText(
            tr("All {total}").format(total=total))
        for value, _ in FILTERS:
            if value is None:
                continue
            if value is NO_KEEP:
                button = self.buttons[NO_KEEP]
                button.setText(
                    tr("Scenes with no keep {count}").format(count=no_keep))
                button.setEnabled(no_keep > 0)
                button.setToolTip(tr(
                    "Scenes that produced no keep at all — either scene\n"
                    "guarantees are off, or every shot fell below the\n"
                    "quality floor. Check here for anything missed."
                ))
                continue
            count = summary.get(value.value, 0)
            ratio = count / total * 100 if total else 0.0
            self.buttons[value].setText(
                f"{value.value} {count} ({ratio:.0f}%)")

    def current_grade(self):
        """Selected filter: a Grade, NO_KEEP, or None for everything."""
        for value, button in self.buttons.items():
            if button.isChecked():
                return value
        return None
