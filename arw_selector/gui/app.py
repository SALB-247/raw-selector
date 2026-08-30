"""GUI entry point."""

from __future__ import annotations

import logging
import multiprocessing
import sys


def _show_crash_dialog(exc_type, exc_value, exc_traceback, report_path) -> None:
    """Tells the user about an unhandled exception.

    A GUI has no console, so left alone it looks like it stopped with no
    explanation at all. Only by saying where the log is can the problem be
    passed on.
    """
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        from .i18n import tr

        if QApplication.instance() is None:
            return

        message = f"{exc_type.__name__}: {exc_value}"
        detail = (
            tr("\n\nError report: {path}").format(path=report_path) if report_path
            else tr("\n\nFailed to write the log.")
        )
        box = QMessageBox()
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle(tr("Error"))
        box.setText(tr("An unhandled error occurred."))
        box.setInformativeText(message + detail)
        box.setDetailedText("".join(__import__("traceback").format_exception(
            exc_type, exc_value, exc_traceback
        )))
        box.exec()
    except Exception:  # noqa: BLE001 - a failed notice must not crash again
        pass


def _install_qt_message_handler() -> None:
    """Puts the warnings and fatal errors Qt raises into the log.

    When Qt meets a misuse it kills the process outright with qFatal(). That
    is a Windows fail-fast (0xc0000409), so it never goes through a signal
    and neither faulthandler nor the Python exception hook leaves anything
    behind - when it really did die during Full Render on "destroying a
    running QThread", the log was empty from end to end and the only trace
    was in the Event Viewer.

    The message handler is called right before it dies, so flushing here too
    leaves the cause behind as one line.
    """
    import logging as _logging

    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    log = _logging.getLogger("qt")
    levels = {
        QtMsgType.QtDebugMsg: _logging.DEBUG,
        QtMsgType.QtInfoMsg: _logging.INFO,
        QtMsgType.QtWarningMsg: _logging.WARNING,
        QtMsgType.QtCriticalMsg: _logging.ERROR,
        QtMsgType.QtFatalMsg: _logging.CRITICAL,
    }

    def handler(mode, context, message) -> None:
        level = levels.get(mode, _logging.INFO)
        where = ""
        if context is not None and context.file:
            where = f" ({context.file}:{context.line})"
        log.log(level, "%s%s", message, where)
        if mode == QtMsgType.QtFatalMsg:
            # The process vanishes the next moment. Empty the buffers.
            for h in _logging.getLogger().handlers:
                try:
                    h.flush()
                except Exception:  # noqa: BLE001
                    pass

    qInstallMessageHandler(handler)


def main() -> int:
    # Analysis uses ProcessPoolExecutor. This stops the child processes from
    # bringing the GUI up again when bundled with PyInstaller/Nuitka.
    multiprocessing.freeze_support()

    from ..core.logging_setup import install_excepthook, setup_logging

    log_path = setup_logging()
    install_excepthook(_show_crash_dialog)
    try:
        _install_qt_message_handler()
    except Exception:  # noqa: BLE001 - instrumentation must not block the app
        logging.getLogger(__name__).warning("Qt 메시지 핸들러 설치 실패", exc_info=True)
    logging.getLogger(__name__).info("로그 파일: %s", log_path)

    # Brings presets saved before the product was renamed (ARW Selector)
    # over to the new settings folder. Leave this out and the presets the
    # user made look as though they vanished wholesale. The originals are
    # not deleted.
    try:
        from ..core.presets import migrate_legacy_config

        moved = migrate_legacy_config()
        if moved:
            logging.getLogger(__name__).info("예전 설정을 옮겨 왔습니다: %s", moved)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).warning("설정 마이그레이션 실패", exc_info=True)

    # Installs the default presets (develop/scoring) once (the app still
    # comes up if it fails).
    try:
        from ..core.presets import (
            install_default_profiles,
            install_default_select_presets,
        )

        install_default_profiles()
        install_default_select_presets()
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).warning("기본 프리셋 설치 실패", exc_info=True)

    from .main_window import main as run

    return run()


if __name__ == "__main__":
    sys.exit(main())
