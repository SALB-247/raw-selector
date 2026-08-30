"""Points out the next button to press by flashing it.

A user opening it for the first time has more than ten buttons in the window
and no way to tell which of them is the starting point. After opening a
folder it is the same - nothing on screen says "now Analyse has to be
pressed".

**A flash that does not stop is worse than no flash at all.** Blinking on and
on keeps stealing the eye, and it stops being distinguishable from the notice
that really is urgent. So it stops the moment any one of three things
happens.

  - the set number of flashes is reached
  - the user presses that button
  - the button turns disabled (there is no reason to be pointing at
    something that cannot be pressed)
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QAbstractButton

from . import theme

PULSE_INTERVAL_MS = 550
"""The interval of one blink. Faster looks anxious, slower does not catch the
eye."""

PULSE_COUNT = 6
"""How many times it blinks. About 3.3 seconds - long enough to register, and
over before it grates."""


class ButtonPulse(QObject):
    """Flashes one button, and only the set number of times.

    Applied to the same button again it cancels the previous flashing and
    starts afresh. Overlapped, the two timers overwrite each other's style
    and it never gets back to its original look.
    """

    def __init__(self, button: QAbstractButton, parent: QObject | None = None) -> None:
        super().__init__(parent or button)
        self._button = button
        self._original = button.styleSheet()
        self._remaining = 0
        self._on = False

        self._timer = QTimer(self)
        self._timer.setInterval(PULSE_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

        # A press means the point has been made, so there is no reason to go
        # on blinking.
        button.clicked.connect(self.stop)

    # ------------------------------------------------------------ Controls

    def start(self, count: int = PULSE_COUNT) -> None:
        if not self._button.isEnabled():
            return  # pointing at a button that cannot be pressed only confuses
        self._remaining = max(1, count) * 2  # an on/off pair
        self._on = False
        self._tick()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._remaining = 0
        self._on = False
        self._restore()

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    # ------------------------------------------------------------ Internals

    def _tick(self) -> None:
        # If it turns disabled part way through (e.g. the analysis has
        # already started) it stops at once.
        if not self._button.isEnabled() or self._remaining <= 0:
            self.stop()
            return

        self._remaining -= 1
        self._on = not self._on
        if self._on:
            self._button.setStyleSheet(theme.ATTENTION_BUTTON)
        else:
            self._restore()

    def _restore(self) -> None:
        try:
            self._button.setStyleSheet(self._original)
        except RuntimeError:
            pass  # the widget is already destroyed - nothing to restore
