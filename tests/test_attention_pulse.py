"""다음에 누를 버튼을 점멸로 알려 주는지.

창에 버튼이 열 개 넘게 있어서 처음 여는 사람은 시작점을 못 찾습니다.
다만 **멈추지 않는 점멸은 안 하느니만 못합니다** — 그래서 멈추는 조건이
지켜지는지가 이 파일의 절반입니다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from arw_selector.gui import theme  # noqa: E402
from arw_selector.gui.attention import ButtonPulse  # noqa: E402
from conftest import destroy_all_widgets, destroy_widget  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    theme.apply_app_theme(instance)
    yield instance
    destroy_all_widgets(instance)


@pytest.fixture
def button(app):
    widget = QPushButton("테스트")
    widget.setStyleSheet("QPushButton { color: red; }")
    yield widget
    destroy_widget(widget, app)


def _advance(pulse, times):
    """타이머를 기다리지 않고 직접 돌립니다."""
    for _ in range(times):
        pulse._tick()


# ------------------------------------------------------- 점멸


def test_start_changes_the_look(button):
    pulse = ButtonPulse(button)
    before = button.styleSheet()
    pulse.start()
    assert button.styleSheet() != before
    assert pulse.running


def test_it_stops_on_its_own(button):
    """정해진 횟수를 채우면 멈춰야 합니다."""
    pulse = ButtonPulse(button)
    pulse.start(count=3)
    _advance(pulse, 20)
    assert not pulse.running


def test_the_original_look_comes_back(button):
    """원래 스타일로 못 돌아오면 버튼이 계속 강조된 채로 남습니다."""
    original = button.styleSheet()
    pulse = ButtonPulse(button)
    pulse.start(count=2)
    _advance(pulse, 20)
    assert button.styleSheet() == original


def test_clicking_stops_it(button):
    """눌렀으면 목적을 달성한 것이라 더 깜빡일 이유가 없습니다."""
    original = button.styleSheet()
    pulse = ButtonPulse(button)
    pulse.start()
    button.click()
    assert not pulse.running
    assert button.styleSheet() == original


def test_disabled_button_never_pulses(button):
    """누를 수 없는 버튼을 가리키면 사용자만 헷갈립니다."""
    button.setEnabled(False)
    original = button.styleSheet()
    pulse = ButtonPulse(button)
    pulse.start()
    assert not pulse.running
    assert button.styleSheet() == original


def test_becoming_disabled_mid_pulse_stops_it(button):
    """분석이 이미 시작되면 버튼이 잠깁니다 — 그때 멈춰야 합니다."""
    original = button.styleSheet()
    pulse = ButtonPulse(button)
    pulse.start()
    button.setEnabled(False)
    _advance(pulse, 1)
    assert not pulse.running
    assert button.styleSheet() == original


def test_restarting_does_not_lose_the_original(button):
    """겹쳐 걸면 강조된 스타일을 '원래 모양'으로 기억해 버릴 수 있습니다."""
    original = button.styleSheet()
    pulse = ButtonPulse(button)
    pulse.start()
    pulse.start()
    pulse.stop()
    assert button.styleSheet() == original


# ------------------------------------------------------- 메인 창 배선


@pytest.fixture
def window(app):
    from arw_selector.gui.main_window import MainWindow

    win = MainWindow()
    win.show()
    app.processEvents()
    yield win
    win.close()
    app.processEvents()


def test_open_button_pulses_when_nothing_is_loaded(window):
    window._pulse_first_step()
    assert window.open_pulse.running


def test_open_button_does_not_pulse_once_a_folder_is_loaded(window, tmp_path):
    window.folder = tmp_path
    window._pulse_first_step()
    assert not window.open_pulse.running


def test_choosing_a_new_folder_pulses_analyze(window, tmp_path, monkeypatch):
    from arw_selector.gui import main_window as module

    target = tmp_path / "촬영1"
    target.mkdir()
    monkeypatch.setattr(
        module.QFileDialog, "getExistingDirectory",
        staticmethod(lambda *a, **k: str(target)))

    window.choose_folder()
    assert window.analyze_pulse.running


def test_reopening_the_same_folder_does_not_pulse(window, tmp_path, monkeypatch):
    """이미 분석해 둔 폴더인데 깜빡이면 4000장을 재분석하게 됩니다."""
    from arw_selector.gui import main_window as module

    target = tmp_path / "촬영2"
    target.mkdir()
    monkeypatch.setattr(
        module.QFileDialog, "getExistingDirectory",
        staticmethod(lambda *a, **k: str(target)))

    window.choose_folder()
    window.analyze_pulse.stop()
    window.choose_folder()  # 같은 폴더를 다시
    assert not window.analyze_pulse.running


def test_a_different_folder_pulses_again(window, tmp_path, monkeypatch):
    from arw_selector.gui import main_window as module

    first, second = tmp_path / "가", tmp_path / "나"
    first.mkdir()
    second.mkdir()
    chosen = [str(first), str(second)]
    monkeypatch.setattr(
        module.QFileDialog, "getExistingDirectory",
        staticmethod(lambda *a, **k: chosen.pop(0)))

    window.choose_folder()
    window.analyze_pulse.stop()
    window.choose_folder()
    assert window.analyze_pulse.running


def test_starting_analysis_stops_the_pulse(window, tmp_path, monkeypatch):
    from arw_selector.gui import main_window as module

    target = tmp_path / "촬영3"
    target.mkdir()
    monkeypatch.setattr(
        module.QFileDialog, "getExistingDirectory",
        staticmethod(lambda *a, **k: str(target)))

    window.choose_folder()
    assert window.analyze_pulse.running
    window.analyze_button.click()
    assert not window.analyze_pulse.running
    assert window.analyze_button.styleSheet() == window.analyze_pulse._original
