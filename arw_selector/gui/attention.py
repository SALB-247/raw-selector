"""다음에 누를 버튼을 점멸로 알려 줍니다.

처음 연 사용자는 창에 버튼이 열 개 넘게 있는데 그중 무엇이 시작점인지
알 수 없습니다. 폴더를 연 뒤에도 마찬가지로 "이제 분석을 눌러야 한다"는
것이 화면에 드러나 있지 않습니다.

**멈추지 않는 점멸은 안 하느니만 못합니다.** 계속 깜빡이면 시선을 계속
빼앗기고, 정작 급한 알림과 구분이 안 됩니다. 그래서 셋 중 하나라도
일어나면 즉시 멈춥니다.

  - 정해진 횟수를 채움
  - 사용자가 그 버튼을 누름
  - 버튼이 비활성으로 바뀜 (누를 수 없는 것을 가리키고 있을 이유가 없습니다)
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QAbstractButton

from . import theme

PULSE_INTERVAL_MS = 550
"""한 번 깜빡이는 간격. 더 빠르면 초조해 보이고, 느리면 눈에 안 띕니다."""

PULSE_COUNT = 6
"""깜빡일 횟수. 약 3.3초입니다 — 눈에 들어오되 거슬리기 전에 끝납니다."""


class ButtonPulse(QObject):
    """버튼 하나를 정해진 횟수만 점멸시킵니다.

    같은 버튼에 다시 걸면 이전 점멸을 취소하고 새로 시작합니다. 겹치면
    타이머 둘이 서로 스타일을 덮어써서 원래 모양으로 못 돌아갑니다.
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

        # 누르면 목적을 달성한 것이므로 더 깜빡일 이유가 없습니다.
        button.clicked.connect(self.stop)

    # ------------------------------------------------------------ 조작

    def start(self, count: int = PULSE_COUNT) -> None:
        if not self._button.isEnabled():
            return  # 누를 수 없는 버튼을 가리키면 사용자만 헷갈립니다
        self._remaining = max(1, count) * 2  # 켜기/끄기 한 쌍
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

    # ------------------------------------------------------------ 내부

    def _tick(self) -> None:
        # 도중에 비활성이 되면(예: 분석이 이미 시작됨) 즉시 멈춥니다.
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
            pass  # 위젯이 이미 파괴됨 — 되돌릴 대상이 없습니다
