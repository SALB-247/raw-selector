"""로깅 및 크래시 추적.

GUI 애플리케이션은 예외가 발생해도 콘솔이 없어 원인을 알 수 없습니다.
파일에 기록해 두어야 사용자가 로그를 보내면 재현 없이도 원인을 찾을 수
있습니다.

- 일반 로그: 회전식 파일, 최근 5개 유지
- 크래시: 별도 파일에 전체 스택과 환경 정보 기록
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
    """로그 폴더. 설정 폴더 하위에 둡니다."""
    from .presets import user_config_dir

    return user_config_dir() / LOG_DIR_NAME


NATIVE_CRASH_FILE = "native_crash.txt"

_crash_dump_handle = None


def _enable_native_crash_dump(directory: Path) -> None:
    """네이티브 크래시가 나도 어디서 죽었는지 남기게 합니다.

    Qt/OpenCV 같은 C++ 쪽에서 죽으면 파이썬 예외 훅이 돌지 않아 로그에 아무
    것도 안 남습니다. 실제로 Qt6Core.dll에서 0xc0000409(fail-fast)로 죽었는데
    단서가 이벤트 뷰어밖에 없었습니다. faulthandler는 시그널 핸들러 수준에서
    파이썬 스택을 찍어 주므로, 어느 코드가 호출한 뒤에 죽었는지 알 수 있습니다.

    파일 핸들은 프로세스가 끝날 때까지 열려 있어야 해서 전역으로 붙잡아 둡니다.
    """
    global _crash_dump_handle
    if _crash_dump_handle is not None:
        return
    try:
        import faulthandler

        _crash_dump_handle = (directory / NATIVE_CRASH_FILE).open("a", encoding="utf-8")
        faulthandler.enable(file=_crash_dump_handle, all_threads=True)
    except Exception:  # noqa: BLE001 - 진단 기능이 앱을 막으면 안 됩니다
        _crash_dump_handle = None


def setup_logging(level: int = logging.INFO, console: bool = True) -> Path:
    """로깅을 설정하고 로그 파일 경로를 반환합니다.

    여러 번 호출해도 핸들러가 중복 설치되지 않습니다.
    """
    global _configured

    directory = log_directory()
    log_path = directory / LOG_FILE_NAME
    if _configured:
        return log_path

    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        # 로그 폴더를 만들지 못해도 프로그램은 동작해야 합니다
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

    # exifread는 TIFF가 아닌 컨테이너(CR3·HEIF)를 만날 때마다 "File format
    # not recognized."를 warning으로 뱉습니다. 그쪽은 전용 파서로 따로
    # 읽으므로 예상된 경로이고, 메시지에 파일명도 없어 쓸모가 없습니다.
    # 그냥 두면 HIF 2800장 폴더에서 로그가 이 줄로만 찹니다. 진짜 실패는
    # raw_io.read_metadata가 파일명과 함께 따로 남깁니다.
    logging.getLogger("exifread").setLevel(logging.ERROR)

    _configured = True
    logging.getLogger(__name__).info("=" * 60)
    logging.getLogger(__name__).info("시작: %s", environment_summary())
    return log_path


def environment_summary() -> str:
    """문제 재현에 필요한 환경 정보를 한 줄로 정리합니다."""
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
    """크래시 내용을 별도 파일에 기록하고 경로를 반환합니다.

    일반 로그와 분리하는 이유는 회전으로 지워지지 않게 하기 위함입니다.
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
    except Exception:  # noqa: BLE001 - 크래시 기록 실패가 또 다른 크래시가 되면 안 됩니다
        return None


def install_excepthook(on_crash=None) -> None:
    """처리되지 않은 예외를 기록합니다.

    on_crash를 주면 사용자에게 알릴 기회를 줍니다. GUI에서는 대화상자를
    띄우는 데 씁니다.
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
    """최근 로그와 크래시 파일 목록입니다."""
    directory = log_directory()
    if not directory.exists():
        return []
    files = sorted(
        directory.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return files[:limit]
