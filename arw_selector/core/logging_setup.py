"""Logging and crash tracking.

A GUI application has no console, so when an exception occurs there is no
way to see the cause. Only by recording to a file can the cause be found
without reproducing it, once the user sends the log.

- ordinary log: a rotating file, the most recent 5 kept
- crash: the full stack and the environment recorded in a separate file
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import platform
import sys
import traceback
from datetime import datetime
from pathlib import Path

from .appinfo import LOG_FILE_NAME  # noqa: F401

LOG_DIR_NAME = "logs"
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 5

_configured = False


def log_directory() -> Path:
    """The log folder. Kept under the settings folder."""
    from .presets import user_config_dir

    return user_config_dir() / LOG_DIR_NAME


NATIVE_CRASH_FILE = "native_crash.txt"

_crash_dump_handle = None


def _enable_native_crash_dump(directory: Path) -> None:
    """Makes even a native crash leave behind where it died.

    When it dies on the C++ side, in Qt or OpenCV, the Python exception
    hook does not run and nothing at all is left in the log. It really did
    die with 0xc0000409 (fail-fast) inside Qt6Core.dll, and the only clue
    was the Event Viewer. faulthandler dumps the Python stack at the signal
    handler level, so you can tell which code called in before it died.

    The file handle has to stay open until the process ends, so it is held
    globally.
    """
    global _crash_dump_handle
    if _crash_dump_handle is not None:
        return
    try:
        import faulthandler

        _crash_dump_handle = (directory / NATIVE_CRASH_FILE).open("a", encoding="utf-8")
        faulthandler.enable(file=_crash_dump_handle, all_threads=True)
    except Exception:  # noqa: BLE001 - a diagnostic must not block the app
        _crash_dump_handle = None


def setup_logging(level: int = logging.INFO, console: bool = True) -> Path:
    """Sets logging up and returns the log file path.

    Calling it several times does not install duplicate handlers.
    """
    global _configured

    directory = log_directory()
    log_path = directory / LOG_FILE_NAME
    if _configured:
        return log_path

    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        # the program has to work even if the log folder cannot be made
        logging.basicConfig(level=level)
        _configured = True
        return log_path

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    _enable_native_crash_dump(directory)

    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if console and sys.stderr is not None:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        stream.setLevel(max(level, logging.WARNING))
        root.addHandler(stream)

    # exifread spits out "File format not recognized." as a warning every
    # time it meets a non-TIFF container (CR3, HEIF). Those are read
    # separately by a dedicated parser, so it is an expected path, and the
    # message does not even carry the filename, which makes it useless.
    # Left alone, a folder of 2800 HIFs fills the log with nothing but this
    # line. Real failures are recorded separately, with the filename, by
    # raw_io.read_metadata.
    logging.getLogger("exifread").setLevel(logging.ERROR)

    _configured = True
    logging.getLogger(__name__).info("=" * 60)
    logging.getLogger(__name__).info("시작: %s", environment_summary())
    return log_path


def environment_summary() -> str:
    """Sums up the environment needed to reproduce a problem in one line."""
    from .. import __version__
    from .appinfo import APP_NAME

    parts = [
        f"{APP_NAME} {__version__}",
        f"Python {platform.python_version()}",
        platform.platform(),
    ]
    try:
        import cv2

        parts.append(f"OpenCV {cv2.__version__}")
    except Exception:  # noqa: BLE001
        pass
    try:
        import rawpy

        parts.append(f"rawpy {rawpy.__version__}")
    except Exception:  # noqa: BLE001
        pass
    return " | ".join(parts)


def write_crash_report(
    exc_type, exc_value, exc_traceback, context: str = ""
) -> Path | None:
    """Records the crash in a separate file and returns the path.

    The reason it is split from the ordinary log is so that rotation does
    not erase it.
    """
    try:
        directory = log_directory()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"crash_{datetime.now():%Y%m%d_%H%M%S}.log"

        lines = [
            f"발생 시각: {datetime.now():%Y-%m-%d %H:%M:%S}",
            f"환경: {environment_summary()}",
            f"작업 디렉터리: {os.getcwd()}",
        ]
        if context:
            lines.append(f"상황: {context}")
        lines.append("")
        lines.extend(
            traceback.format_exception(exc_type, exc_value, exc_traceback)
        )

        path.write_text("\n".join(lines), encoding="utf-8")
        return path
    except Exception:  # noqa: BLE001 - recording a crash must not crash again
        return None


def install_excepthook(on_crash=None) -> None:
    """Records unhandled exceptions.

    Given an on_crash, it hands over a chance to tell the user. The GUI
    uses it to raise a dialog.
    """
    previous = sys.excepthook

    def handler(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            previous(exc_type, exc_value, exc_traceback)
            return

        logging.getLogger("crash").critical(
            "처리되지 않은 예외", exc_info=(exc_type, exc_value, exc_traceback)
        )
        path = write_crash_report(exc_type, exc_value, exc_traceback)

        if on_crash is not None:
            try:
                on_crash(exc_type, exc_value, exc_traceback, path)
            except Exception:  # noqa: BLE001
                pass
        else:
            previous(exc_type, exc_value, exc_traceback)

    sys.excepthook = handler


def recent_logs(limit: int = 5) -> list[Path]:
    """The list of recent log and crash files."""
    directory = log_directory()
    if not directory.exists():
        return []
    files = sorted(
        directory.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return files[:limit]
