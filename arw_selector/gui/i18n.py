"""Translation helper for user-visible text.

Source strings are English. Korean ships as a translation, not the other
way round, because contributors read the source before they read any
translation file.

Two rules keep this from rotting:

1. **Only the GUI layer produces display text.** `arw_selector.core` must
   not import Qt — analysis runs in `ProcessPoolExecutor` workers, and
   pulling Qt into every worker would cost startup time for nothing. Core
   returns stable keys plus numbers; this layer turns them into sentences.

2. **No f-strings around translatable text.** `tr("Kept {count}")` can be
   translated; `tr(f"Kept {count}")` cannot, because the extractor sees a
   different string every run. Format *after* translating.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QLocale, QTranslator

#: Qt looks translations up by (context, source text).
#:
#: This is empty on purpose, and it has to stay that way. `pyside6-lupdate`
#: can only infer a context from a class, so a module-level `tr()` helper
#: like this one gets `<name></name>` in the generated `.ts`. Look the
#: string up under any other name and every lookup misses.
#:
#: A mismatch here does not raise — `translate()` just hands back the
#: English source, so the app quietly stays English. That is why
#: `tests/test_i18n_roundtrip.py` walks the whole chain instead of only
#: checking that `tr()` was called.
CONTEXT = ""

_translator: QTranslator | None = None


def tr(text: str, disambiguation: str | None = None) -> str:
    """Translate one user-visible string.

    Falls back to the English source when no translation is loaded, which
    is what happens in tests and on a fresh install.
    """
    return QCoreApplication.translate(CONTEXT, text, disambiguation)


def install(app: QCoreApplication, language: str | None = None) -> str:
    """Load a translation and return the language actually used.

    `language` is a two-letter code ("ko", "en"). None means: use the saved
    preference, and fall back to the system locale if there is none.
    Returns "en" when nothing was loaded — English is the source, so that
    is a complete result, not a failure.
    """
    global _translator

    from ..core import state
    from .appinfo_bridge import translations_dir

    # Take down whatever is installed first. Without this a second call
    # leaves the first translator attached to the application while only the
    # second one is tracked here, so it can never be removed again — the app
    # stays in the old language with nothing pointing at why.
    uninstall(app)

    code = language or state.language() or QLocale.system().name().split("_")[0]
    if code == "en":
        return "en"

    path = translations_dir() / f"raw_selector_{code}.qm"
    if not path.is_file():
        return "en"

    translator = QTranslator()
    if not translator.load(str(path)):
        return "en"

    app.installTranslator(translator)
    # Qt drops a translator that only Python references — keep it alive.
    _translator = translator
    return code


def uninstall(app: QCoreApplication) -> None:
    """Detach the translation, putting the interface back to English source.

    Mostly for tests: a translator left installed on the singleton
    QApplication follows into every later test, and the failure surfaces
    somewhere else entirely as "this label is Korean and should be English".
    """
    global _translator

    if _translator is not None:
        app.removeTranslator(_translator)
        _translator = None


def current_language() -> str:
    return "en" if _translator is None else "ko"
