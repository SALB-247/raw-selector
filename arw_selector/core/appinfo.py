"""제품 정체성과 저장 위치 한 곳 모음.

이름이 저장 경로에 박혀 있어서, 여기저기 문자열로 흩어져 있으면 제품명을
바꿀 때 프리셋과 되돌리기 로그를 잃습니다. 실제로 ARW Selector -> RAW_selector
로 바꾸면서 겪은 문제라, 이후 이름 변경은 이 파일만 고치면 되도록 모았습니다.

저장 위치는 두 갈래입니다:

  data_dir()       실행 파일 옆 `data/`. 프리셋·렌즈 프로필·로그처럼 **앱과
                   함께 다녀야 하는 것**. USB에 통째로 복사하면 그대로 따라갑니다.
  user_state_dir() %APPDATA% 등. 마지막으로 연 폴더처럼 **그 PC에서만 의미 있는
                   상태**만 둡니다.

예전 이름/위치는 지우지 않습니다:
  - 예전 설정 폴더는 시작할 때 새 위치로 복사합니다 (원본 보존).
  - 사진 폴더 옆 캐시/로그는 폴더마다 흩어져 있어 옮길 수 없으므로,
    새 이름이 없으면 예전 이름을 찾아 씁니다. 그래야 예전 내보내기를
    계속 되돌릴 수 있습니다.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

APP_NAME = "RAW_selector"
"""사용자에게 보이는 제품명 (창 제목, EXIF Software, 실행 파일 이름)."""

APP_DIR_NAME = "raw_selector"
"""설정 폴더 이름 (%APPDATA%/... 또는 ~/Library/Application Support/...)."""

DATA_DIR_NAME = "data"
"""실행 파일 옆에 두는 데이터 폴더 이름."""

CACHE_DIR_NAME = ".raw_selector_cache"
"""사진 폴더 옆에 두는 분석 캐시 · 내보내기 되돌리기 로그 폴더."""

LOG_FILE_NAME = "raw_selector.log"

STATE_FILE_NAME = "state.json"
"""기기별 상태 파일 (마지막으로 연 폴더 등)."""

LEGACY_APP_DIR_NAMES: tuple[str, ...] = ("arw_selector",)
"""예전 설정 폴더 이름들. 시작 시 새 폴더로 복사해 옵니다."""

LEGACY_CACHE_DIR_NAMES: tuple[str, ...] = (".arw_selector_cache",)
"""예전 캐시/로그 폴더 이름들. 새 이름이 없을 때 대신 읽습니다."""


def app_root() -> Path:
    """실행 파일이 있는 폴더 (소스로 돌릴 때는 저장소 최상위).

    Nuitka standalone은 컴파일된 모듈에 __compiled__ 를 넣어 줍니다.
    PyInstaller는 sys.frozen 을 씁니다. 둘 다 아니면 소스 트리입니다.
    """
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _config_root() -> Path:
    """플랫폼별 사용자 설정 폴더의 부모."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return Path(base) if base else Path.home() / "AppData" / "Roaming"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    base = os.environ.get("XDG_CONFIG_HOME")
    return Path(base) if base else Path.home() / ".config"


def user_state_dir() -> Path:
    """그 PC에서만 의미 있는 상태를 두는 곳 (마지막으로 연 폴더 등).

    프리셋 같은 콘텐츠는 여기 두지 않습니다 — 앱을 다른 PC로 옮기면
    따라가지 않기 때문입니다.
    """
    return _config_root() / APP_DIR_NAME


def _is_writable(path: Path) -> bool:
    """실제로 파일을 만들어 보고 판단합니다.

    Program Files처럼 권한이 막힌 곳에 설치했을 수 있어서, 존재 여부만으로는
    알 수 없습니다.
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


#: 쓰기가 막힌 곳에서 실행할 때 사용자 폴더로 복사해 와야 하는 것들.
#: logs는 실행 중에 생기는 것이라 넣지 않습니다.
_SEEDED_DIRS = ("select_presets", "develop_presets", "lensfun", "calibration")


def _seed_from_bundle(target: Path) -> None:
    """같이 담겨 온 기본 데이터를 사용자 폴더로 옮겨 놓습니다.

    쓰기가 막힌 곳(맥이면 .app 안, 윈도우면 Program Files)에서 실행하면
    data/가 사용자 폴더로 물러나는데, 그때 번들에 함께 담긴 **판정 프리셋과
    렌즈 프로필 XML이 통째로 안 보였습니다**. 보정 프리셋만 코드에서 다시
    만들어 넣고 있어서(`presets.install_default_profiles`) 그쪽만 멀쩡해
    더 알아채기 어려웠습니다.

    실측(0.15.1, DMG에서 바로 실행): 판정 프리셋 0개로 자체 점검이 실패하고,
    렌즈 DB가 바디 1052/렌즈 1609에서 948/1304로 줄었습니다.

    이미 있는 파일은 건드리지 않습니다 — 사용자가 고쳐 둔 것이 우선입니다.
    """
    import shutil

    source_root = app_root() / DATA_DIR_NAME
    if not source_root.is_dir() or source_root.resolve() == target.resolve():
        return
    for name in _SEEDED_DIRS:
        source = source_root / name
        if not source.is_dir():
            continue
        try:
            for item in source.rglob("*"):
                if not item.is_file():
                    continue
                destination = target / name / item.relative_to(source)
                if destination.exists():
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, destination)
        except OSError:
            # 복사에 실패해도 앱은 떠야 합니다. 빠진 것은 자체 점검이 셉니다.
            pass


@lru_cache(maxsize=1)
def data_dir() -> Path:
    """프리셋·렌즈 프로필·로그를 두는 곳. 기본은 실행 파일 옆입니다.

    앱 폴더를 통째로 복사하면 프리셋도 같이 따라가는 게 자연스럽습니다.
    다만 쓰기가 막힌 위치(Program Files, .app 번들 안 등)에 설치했다면
    저장이 아예 안 되므로, 그때는 사용자 폴더로 물러납니다.
    """
    candidate = app_root() / DATA_DIR_NAME
    if _is_writable(candidate):
        return candidate
    fallback = user_state_dir() / DATA_DIR_NAME
    fallback.mkdir(parents=True, exist_ok=True)
    # 물러났으면 번들의 기본 데이터를 옮겨 옵니다. lru_cache라 한 번만 돕니다.
    _seed_from_bundle(fallback)
    return fallback


def is_portable() -> bool:
    """데이터가 실행 파일 옆에 있는지 (쓰기 불가로 폴백했으면 False)."""
    return data_dir() == app_root() / DATA_DIR_NAME
