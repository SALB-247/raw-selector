"""테마의 모든 스타일시트가 Qt에서 실제로 파싱되는지 확인합니다.

이 테스트가 있는 이유: Qt는 스타일시트 파싱에 실패하면 경고를 **한 줄만**
내고 그 시트를 통째로 버립니다. 예외도, 반환값도 없습니다. 화면은 그냥
기본 스타일로 나오므로 눈으로 봐도 "왜 색이 안 먹지"로만 보입니다.

실제로 겪은 일: 이어지는 줄에 f 접두사를 빼먹어서 `}}`가 이스케이프가 아니라
닫는 중괄호 두 개가 됐고, 창 스타일시트 전체가 조용히 버려졌습니다.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QtMsgType, qInstallMessageHandler  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from arw_selector.gui import theme  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def parse_errors(app):
    """Qt가 낸 스타일시트 경고를 잡아 둡니다."""
    captured: list[str] = []

    def handler(mode, context, message):
        if mode in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg):
            captured.append(message)

    previous = qInstallMessageHandler(handler)
    yield captured
    qInstallMessageHandler(previous)


def apply_sheet(app, captured, sheet: str) -> list[str]:
    captured.clear()
    probe = QWidget()
    probe.setStyleSheet(sheet)
    app.processEvents()
    probe.deleteLater()
    app.processEvents()
    return [m for m in captured if "stylesheet" in m.lower()]


def stylesheet_constants() -> list[tuple[str, str]]:
    found = []
    for name in sorted(dir(theme)):
        if name.startswith("_"):
            continue
        value = getattr(theme, name)
        if isinstance(value, str) and "{" in value:
            found.append((name, value))
    return found


@pytest.mark.parametrize("name,sheet", stylesheet_constants(),
                         ids=lambda v: v if isinstance(v, str) and "{" not in v
                         else "")
def test_constant_stylesheets_parse(app, parse_errors, name, sheet):
    problems = apply_sheet(app, parse_errors, sheet)
    assert not problems, f"{name} 파싱 실패: {problems}"


def test_window_style_parses(app, parse_errors):
    """창 전체 스타일 — 여기가 깨지면 앱이 통째로 기본 스타일이 됩니다."""
    problems = apply_sheet(app, parse_errors, theme.window_style())
    assert not problems, f"window_style() 파싱 실패: {problems}"


def test_hint_label_parses(app, parse_errors):
    problems = apply_sheet(app, parse_errors, theme.hint_label())
    assert not problems, f"hint_label() 파싱 실패: {problems}"


def test_braces_are_balanced():
    """짝이 안 맞는 중괄호는 파싱 실패의 가장 흔한 원인입니다."""
    sheets = dict(stylesheet_constants())
    sheets["window_style()"] = theme.window_style()
    sheets["hint_label()"] = theme.hint_label()

    for name, sheet in sheets.items():
        assert sheet.count("{") == sheet.count("}"), (
            f"{name}: 여는 중괄호 {sheet.count('{')}개, "
            f"닫는 중괄호 {sheet.count('}')}개"
        )


def test_no_leftover_escaped_braces():
    """`{{` / `}}` 가 남아 있으면 f 접두사를 빠뜨린 것입니다."""
    sheets = dict(stylesheet_constants())
    sheets["window_style()"] = theme.window_style()

    for name, sheet in sheets.items():
        assert "{{" not in sheet and "}}" not in sheet, (
            f"{name}: 이스케이프되지 않은 중괄호가 남아 있습니다 — "
            f"이어지는 줄에 f 접두사를 빠뜨렸는지 확인하세요"
        )


def test_main_window_applies_its_stylesheet(app, parse_errors):
    """실제 창을 만들었을 때도 경고가 없어야 합니다."""
    from arw_selector.gui.main_window import MainWindow

    parse_errors.clear()
    theme.apply_app_theme(app)
    window = MainWindow()
    window.show()
    app.processEvents()

    problems = [m for m in parse_errors if "stylesheet" in m.lower()]
    window.close()
    app.processEvents()

    assert not problems, f"MainWindow 스타일시트 경고: {problems}"
