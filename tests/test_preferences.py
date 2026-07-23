"""설정 창 — 언어·업데이트 확인·라이선스.

업데이트 확인은 **외부 서버에 요청을 보냅니다.** 사진 도구가 묻지도 않고
네트워크로 나가면 안 되므로, 기본이 꺼짐인지와 자동으로 도는 경로가
없는지를 여기서 지킵니다.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from arw_selector.core import state, updates  # noqa: E402
from conftest import destroy_all_widgets, destroy_widget  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from arw_selector.gui import theme

    instance = QApplication.instance() or QApplication([])
    theme.apply_app_theme(instance)
    yield instance
    destroy_all_widgets(instance)


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """상태 파일을 임시 폴더로 돌립니다. 사용자 설정을 건드리면 안 됩니다."""
    monkeypatch.setattr(state, "state_path", lambda: tmp_path / "state.json")
    return tmp_path


@pytest.fixture
def dialog(app, isolated_state):
    from arw_selector.gui.preferences_dialog import PreferencesDialog

    widget = PreferencesDialog()
    yield widget
    destroy_widget(widget, app)


# ------------------------------------------------------- 네트워크


def test_update_check_is_off_by_default(dialog):
    """묻지 않고 외부로 나가지 않습니다."""
    assert not dialog.update_check.isChecked()


def test_nothing_checks_on_startup(monkeypatch, app, isolated_state):
    """창을 여는 것만으로 요청이 나가면 안 됩니다."""
    from arw_selector.gui.preferences_dialog import PreferencesDialog

    calls = []
    monkeypatch.setattr(updates, "check", lambda *a, **k: calls.append(a))

    widget = PreferencesDialog()
    try:
        assert calls == [], "설정 창을 여는 것만으로 업데이트를 확인했습니다"
    finally:
        destroy_widget(widget, app)


def test_check_runs_only_on_the_button(dialog, monkeypatch):
    calls = []

    def fake(version, **kwargs):
        calls.append(version)
        return updates.UpdateResult(error="not_configured")

    monkeypatch.setattr(updates, "check", fake)
    dialog.check_now.click()
    assert len(calls) == 1


def test_unconfigured_source_says_so(dialog):
    """저장소가 아직 공개되지 않았으면 그렇다고 말합니다 — 지어내지 않습니다."""
    dialog._check_for_updates()
    assert dialog.update_status.text().strip()
    assert "configured" in dialog.update_status.text().lower() or \
        "설정" in dialog.update_status.text()


# ------------------------------------------------------- 버전 비교


def test_version_compare_is_numeric():
    """문자열로 비교하면 0.9 > 0.14 가 됩니다 — 열 번째 릴리스에서 터집니다."""
    assert updates.is_newer("0.14.0", "0.9.0")
    assert not updates.is_newer("0.9.0", "0.14.0")


def test_version_tags_with_a_prefix_parse():
    assert updates.parse_version("v1.2.3") == (1, 2, 3)
    assert updates.parse_version("0.14.0") == (0, 14, 0)


def test_same_version_is_not_newer():
    assert not updates.is_newer("0.14.0", "0.14.0")


def test_unparseable_version_does_not_crash():
    assert updates.parse_version("") == (0,)
    assert updates.parse_version("nightly") == (0,)


def test_check_without_repository_makes_no_request(monkeypatch):
    """설정이 없으면 urlopen 까지 가지 않아야 합니다."""
    import urllib.request

    def explode(*args, **kwargs):
        raise AssertionError("요청이 나갔습니다")

    monkeypatch.setattr(urllib.request, "urlopen", explode)
    result = updates.check("0.14.0", repository="")
    assert result.error == "not_configured"


# ------------------------------------------------------- 언어


def test_language_defaults_to_system(dialog):
    assert dialog.language_combo.currentData() == ""


def test_language_names_stay_in_their_own_language(dialog):
    """한국어를 찾는 사람은 '한국어'를 찾습니다."""
    captions = [dialog.language_combo.itemText(i)
                for i in range(dialog.language_combo.count())]
    assert "English" in captions
    assert "한국어" in captions


def test_language_choice_is_saved(dialog, isolated_state):
    index = dialog.language_combo.findData("ko")
    dialog.language_combo.setCurrentIndex(index)
    dialog.accept()
    assert state.language() == "ko"


def test_language_change_is_reported(dialog):
    """바꿨는지 알아야 '다시 시작하십시오'를 한 번만 띄웁니다."""
    dialog.language_combo.setCurrentIndex(dialog.language_combo.findData("ko"))
    assert dialog.language_changed_from(None)
    assert not dialog.language_changed_from("ko")


def test_installed_language_follows_the_saved_choice(app, isolated_state):
    """설정을 저장해도 install()이 안 읽으면 아무 소용이 없습니다."""
    from arw_selector.gui import i18n

    state.set_language("ko")
    assert i18n.install(app) == "ko"

    state.set_language("en")
    assert i18n.install(app) == "en"


# ------------------------------------------------------- 라이선스


def test_licence_text_is_shown(dialog):
    """오픈소스 배포에는 라이선스 고지가 따라가야 합니다."""
    from arw_selector.gui.preferences_dialog import _read_licence_text

    text = _read_licence_text()
    assert "MIT License" in text
    assert "PySide6" in text
    assert "LGPL" in text


def test_licence_text_comes_from_the_shipped_files():
    """소스에 박아 두면 파일과 어긋날 수 있습니다."""
    from arw_selector.core.appinfo import app_root

    assert (app_root() / "LICENSE").is_file()
    assert (app_root() / "THIRD_PARTY.md").is_file()
