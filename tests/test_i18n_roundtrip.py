"""번역이 실제로 화면까지 도달하는지.

문자열을 tr()로 감싸는 것만으로는 아무것도 증명되지 않습니다. 추출 →
번역 → 컴파일 → 로드 중 한 군데만 끊겨도 화면은 조용히 영어로 남습니다.
조용히 실패하는 것이 이 작업에서 가장 위험합니다 — 800개를 다 옮긴 뒤에야
알게 되기 때문입니다.

여기서는 이미 옮긴 채점표 문자열로 그 사슬 전체를 확인합니다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QTranslator  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from arw_selector.core import scoring  # noqa: E402
from arw_selector.gui import i18n  # noqa: E402
from conftest import destroy_all_widgets  # noqa: E402

QM = Path(__file__).resolve().parents[1] / "data" / "translations" / "raw_selector_ko.qm"


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance
    destroy_all_widgets(instance)


@pytest.fixture
def korean(app):
    """한국어 번역을 걸었다가 반드시 되돌립니다.

    되돌리지 않으면 뒤에 도는 테스트가 한국어 화면을 보게 되어, 원인을
    엉뚱한 곳에서 찾게 됩니다.
    """
    if not QM.is_file():
        pytest.skip("컴파일된 한국어 번역이 없습니다 (tools/build_translations.py)")
    translator = QTranslator()
    assert translator.load(str(QM)), f"{QM} 를 못 읽었습니다"
    app.installTranslator(translator)
    yield
    app.removeTranslator(translator)


def test_english_is_the_source(app):
    """번역을 안 걸면 영어가 그대로 나와야 합니다."""
    assert i18n.tr("Sharpness") == "Sharpness"
    assert i18n.tr("Eyes closed") == "Eyes closed"


def test_korean_translation_reaches_tr(korean):
    assert i18n.tr("Sharpness") == "선명도"
    assert i18n.tr("Eyes closed") == "눈 감김"
    assert i18n.tr("Total") == "합계"


def test_placeholders_survive_translation(korean):
    """자리표시자가 번역에서 깨지면 화면에서 KeyError가 납니다."""
    text = i18n.tr("EAR {ear:.2f} < threshold {threshold:.2f}").format(
        ear=0.18, threshold=0.30)
    assert "0.18" in text and "0.30" in text
    assert "{" not in text


def test_score_card_labels_follow_the_language(app, korean):
    """tr()만 되고 화면이 안 따라오면 아무 소용이 없습니다."""
    from arw_selector.gui.score_card import _label_for

    assert _label_for(scoring.LINE_EYES_CLOSED) == "눈 감김"
    assert _label_for(scoring.LINE_FACE_DETECTED) == "얼굴 검출"


def test_labels_are_english_again_after_removal(app):
    """korean 픽스처가 정리를 제대로 하는지 — 이게 새면 다른 테스트가 깨집니다."""
    from arw_selector.gui.score_card import _label_for

    assert _label_for(scoring.LINE_EYES_CLOSED) == "Eyes closed"


def test_the_app_actually_installs_the_translation(app, monkeypatch):
    """만들어 두고 아무도 부르지 않으면 화면은 계속 영어입니다.

    실제로 그랬습니다 — i18n.install()을 쓴 곳이 한 군데도 없어서, 번역이
    전부 완성돼 있는데도 앱은 영어로만 떴습니다. Qt는 조용히 원문을
    돌려주므로 예외도 로그도 없습니다.

    소스 텍스트를 검사하지 않습니다. 그렇게 했더니 파일이 바뀌는 순간
    inspect가 줄 번호를 잘못 짚어 엉뚱한 함수 본문을 읽었습니다. 동작으로
    확인합니다 — 실제로 앱 설정을 돌리고 번역이 걸렸는지 봅니다.
    """
    from arw_selector.core import state
    from arw_selector.gui import main_window

    if not QM.is_file():
        pytest.skip("컴파일된 한국어 번역이 없습니다")

    monkeypatch.setattr(state, "language", lambda: "ko")
    try:
        assert main_window.configure_application(app) == "ko"
        assert i18n.tr("Sharpness") == "선명도"
    finally:
        # 번역기를 남기면 뒤에 도는 테스트가 한국어 화면을 봅니다.
        translator = i18n._translator
        if translator is not None:
            app.removeTranslator(translator)
            i18n._translator = None


def test_translation_lives_where_the_app_looks(app):
    """빌드에서 폴더가 빠지면 조용히 영어가 됩니다."""
    from arw_selector.gui.appinfo_bridge import translations_dir

    folder = translations_dir()
    assert folder.is_dir(), f"{folder} 가 없습니다"
    assert list(folder.glob("*.qm")), f"{folder} 에 컴파일된 번역이 없습니다"


def test_no_string_is_left_untranslated():
    """빠진 번역이 하나라도 있으면 그 자리만 영어로 남습니다.

    `.ts`에서 `<source>`와 `<translation>` 사이에는 `<extracomment>`가
    낄 수 있습니다(소스의 `#:` 주석에서 옵니다). 붙어 있다고 가정한 검사는
    그런 항목을 건너뛰면서 "빠진 것 없음"이라고 보고했습니다 — 실제로 그렇게
    두 개를 놓쳤습니다. 거짓말하는 검사기는 없는 것만 못합니다.
    """
    import re

    ts = QM.with_suffix(".ts")
    if not ts.is_file():
        pytest.skip("추출된 .ts가 없습니다")

    text = ts.read_text(encoding="utf-8")
    # `<message>` 블록 단위로 봅니다. source에서 translation까지 한 번에
    # 매칭하면, 이미 번역된 항목들을 지나 미번역을 만날 때까지 되짚어
    # 올라가서 그 구간 전체를 문자열 하나로 보고합니다 — 개수는 맞는데
    # 이름이 엉뚱하게 나옵니다.
    missing = []
    for block in re.findall(r"<message>(.*?)</message>", text, re.S):
        if 'type="unfinished"' not in block:
            continue
        found = re.search(r"<source>(.*?)</source>", block, re.S)
        missing.append(found.group(1) if found else "(출처 불명)")
    assert not missing, f"번역 안 된 문자열 {len(missing)}개: {missing[:5]}"


def test_reason_wording_is_translated(korean):
    """근거 문구도 화면까지 번역이 도달해야 합니다."""
    from arw_selector.gui.reason_text import render

    text = render(scoring.Reason(scoring.REASON_EYES_CLOSED,
                                 {"ear": 0.18, "threshold": 0.30}))
    assert "눈 감김" in text
    assert "0.18" in text and "0.30" in text


def test_every_reason_key_is_translated(korean):
    """근거 키를 추가하고 번역을 빠뜨리면 그 줄만 영어로 남습니다."""
    from arw_selector.gui import reason_text

    keys = [
        value for name, value in vars(scoring).items()
        if name.startswith("REASON_") and isinstance(value, str)
    ]
    # 오류 문구는 예외 메시지를 그대로 싣는 자리라 번역 대상이 아닙니다.
    keys = [k for k in keys if k != scoring.REASON_ERROR]
    untranslated = [k for k in keys if reason_text._template(k).isascii()]
    assert not untranslated, f"번역 안 된 근거: {untranslated}"


def test_every_line_key_is_translated(korean):
    """키를 추가하고 번역을 빠뜨리면 그 줄만 영어로 남습니다.

    한 줄만 영어인 화면은 버그로 보이지도 않아서 오래 남습니다.
    """
    from arw_selector.gui.score_card import _label_for

    keys = [
        value for name, value in vars(scoring).items()
        if name.startswith("LINE_") and isinstance(value, str)
    ]
    untranslated = [k for k in keys if _label_for(k).isascii()]
    assert not untranslated, f"번역 안 된 항목: {untranslated}"
