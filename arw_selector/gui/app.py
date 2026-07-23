"""GUI 진입점."""

from __future__ import annotations

import logging
import multiprocessing
import sys


def _show_crash_dialog(exc_type, exc_value, exc_traceback, report_path) -> None:
    """처리되지 않은 예외를 사용자에게 알립니다.

    GUI는 콘솔이 없어 두면 아무 설명 없이 멈춘 것처럼 보입니다.
    로그 위치를 알려 주어야 문제를 전달할 수 있습니다.
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
    except Exception:  # noqa: BLE001 - 알림 실패가 또 다른 크래시가 되면 안 됩니다
        pass


def _install_qt_message_handler() -> None:
    """Qt가 내는 경고와 치명적 오류를 로그에 남깁니다.

    Qt는 잘못된 사용을 만나면 qFatal()로 프로세스를 즉사시킵니다. 그건
    Windows의 fail-fast(0xc0000409)라서 시그널을 거치지 않고, faulthandler도
    파이썬 예외 훅도 아무것도 남기지 못합니다 — 실제로 Full Render 중
    "실행 중인 QThread 파괴"로 죽었을 때 로그가 통째로 비어 있었고 이벤트
    뷰어에만 흔적이 있었습니다.

    메시지 핸들러는 죽기 직전에 불리므로, 여기서 flush까지 해 두면 원인이
    한 줄로 남습니다.
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
            # 다음 순간 프로세스가 사라집니다. 버퍼를 비워 둡니다.
            for h in _logging.getLogger().handlers:
                try:
                    h.flush()
                except Exception:  # noqa: BLE001
                    pass

    qInstallMessageHandler(handler)


def main() -> int:
    # 분석은 ProcessPoolExecutor를 사용합니다. PyInstaller/Nuitka로 묶었을 때
    # 자식 프로세스가 GUI를 다시 띄우는 것을 막습니다.
    multiprocessing.freeze_support()

    from ..core.logging_setup import install_excepthook, setup_logging

    log_path = setup_logging()
    install_excepthook(_show_crash_dialog)
    try:
        _install_qt_message_handler()
    except Exception:  # noqa: BLE001 - 계측 실패가 앱을 막으면 안 됩니다
        logging.getLogger(__name__).warning("Qt 메시지 핸들러 설치 실패", exc_info=True)
    logging.getLogger(__name__).info("로그 파일: %s", log_path)

    # 제품명이 바뀌기 전(ARW Selector)에 저장해 둔 프리셋을 새 설정 폴더로
    # 옮겨 옵니다. 이걸 빠뜨리면 사용자가 만든 프리셋이 통째로 사라진 것처럼
    # 보입니다. 원본은 지우지 않습니다.
    try:
        from ..core.presets import migrate_legacy_config

        moved = migrate_legacy_config()
        if moved:
            logging.getLogger(__name__).info("예전 설정을 옮겨 왔습니다: %s", moved)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).warning("설정 마이그레이션 실패", exc_info=True)

    # 기본 카메라 프로파일 프리셋을 한 번 설치합니다 (실패해도 앱은 뜹니다).
    try:
        from ..core.presets import install_default_profiles

        install_default_profiles()
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).warning("기본 프로파일 설치 실패", exc_info=True)

    from .main_window import main as run

    return run()


if __name__ == "__main__":
    sys.exit(main())
