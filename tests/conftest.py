"""테스트 공용 도구.

Qt 위젯 정리를 여기 한곳에 둡니다. 파일마다 각자 정리하다가 실제로 힙이
깨졌습니다 — 아래 `destroy_widget` 설명 참고.

기기 상태(state.json)도 여기서 격리합니다. 아래 픽스처 설명 참고.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_user_state(tmp_path, monkeypatch):
    """모든 테스트의 상태 파일을 임시 폴더로 돌립니다.

    **테스트가 사용자의 실제 설정을 고쳤습니다.** 실제로 이렇게 됐습니다:

        last_folder: C:\\...\\pytest-503\\test_starting_analysis_stops_t0\\촬영3
        language: en

    `test_attention_pulse.py`가 `choose_folder()`를 부르는데 그 안에서
    `state.remember_folder()`가 돌아, 존재하지도 않는 pytest 임시 폴더가
    "마지막으로 연 폴더"로 저장됐습니다. 언어도 영어로 바뀌어서 앱을
    켜면 영어로 떴습니다.

    테스트 하나하나에서 막을 문제가 아닙니다 — 상태를 건드리는 코드는
    앞으로도 늘어납니다. autouse로 전부 막습니다.
    """
    from arw_selector.core import state

    monkeypatch.setattr(state, "state_path", lambda: tmp_path / "state.json")
    yield


@pytest.fixture(autouse=True)
def _english_interface():
    """테스트가 끝나면 번역기를 반드시 뗍니다.

    QApplication은 싱글턴이라 한 테스트가 설치한 번역기가 뒤따르는 **모든**
    테스트로 넘어갑니다. 그러면 실패가 엉뚱한 곳에서 터집니다 — 실제로
    test_score_card 가 "라벨이 영어여야 하는데 '분석 실패'가 나온다"로
    깨졌고, 원인은 세 파일 앞의 i18n 테스트였습니다.

    원본 문자열이 영어이므로 번역기가 없는 상태가 테스트의 기준입니다.
    """
    yield

    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return

    app = QApplication.instance()
    if app is None:
        return
    from arw_selector.gui import i18n

    i18n.uninstall(app)


def destroy_widget(widget, app=None) -> None:
    """위젯을 **파괴까지** 시킵니다. close()만으로는 부족합니다.

    `close()`는 숨기기입니다. C++ 객체는 그대로 남고, 부모 없는 위젯은
    QApplication이 끝날 때까지 살아 있습니다. 테스트마다 하나씩 쌓이면
    모듈 스코프 app 픽스처 teardown에서 수십 개가 한꺼번에 정리되는데,
    그때 힙이 손상되어 파이썬이 죽었습니다.

    실제 증상 (tests/test_settings_panel_coverage.py 단독 실행):
      - 29개 전부 통과 표시가 나온 **뒤**
      - pytest가 "29 passed" 요약을 찍기 **전에**
      - 종료 코드 0xC0000374 (STATUS_HEAP_CORRUPTION)

    통과한 뒤에 죽기 때문에 더 나쁩니다. 화면에는 전부 초록으로 보이는데
    종료 코드만 비정상이라, 스위트를 통째로 돌릴 때는 알아채기 어렵습니다.
    이 프로젝트는 예전에도 같은 계열의 네이티브 크래시(0xC0000409)를
    겪었습니다 — tests/test_adversarial_worker_lifetime.py 참고.

    `deleteLater()`는 이벤트 루프가 한 번 돌아야 실제로 지웁니다. 그래서
    `processEvents()`가 반드시 뒤따라야 합니다. 이것만 빠뜨리면 예약만 해
    두고 똑같이 쌓입니다.
    """
    if widget is None:
        return
    try:
        widget.close()
        widget.setParent(None)
        widget.deleteLater()
    except RuntimeError:
        # 이미 C++ 쪽이 파괴된 경우 — 정리 목적은 이미 달성됐습니다
        return

    if app is None:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
    if app is not None:
        app.processEvents()


def destroy_all_widgets(app) -> None:
    """남아 있는 최상위 위젯을 전부 파괴합니다. app 픽스처 teardown용."""
    if app is None:
        return
    for widget in list(app.topLevelWidgets()):
        destroy_widget(widget, app)
