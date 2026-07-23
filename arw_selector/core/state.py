"""기기별 상태.

프리셋 같은 콘텐츠와 달리, "마지막으로 연 폴더"는 그 PC에서만 의미가
있습니다. 앱 폴더에 같이 넣어 USB로 옮기면 존재하지도 않는 경로를
가리키게 되므로, 이것만 사용자 폴더(%APPDATA% 등)에 둡니다.

읽기·쓰기 모두 실패해도 앱은 그냥 돌아가야 합니다. 편의 기능이 앱을
막아서는 안 됩니다.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .appinfo import STATE_FILE_NAME, user_state_dir

log = logging.getLogger(__name__)


def state_path() -> Path:
    return user_state_dir() / STATE_FILE_NAME


def load_state() -> dict:
    """상태 전체를 읽습니다. 없거나 깨졌으면 빈 dict."""
    try:
        raw = state_path().read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.debug("상태 파일이 깨져 있어 무시합니다: %s", state_path())
        return {}
    return data if isinstance(data, dict) else {}


def save_state(values: dict) -> None:
    """상태를 통째로 씁니다. 실패는 조용히 넘깁니다."""
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        log.debug("상태를 저장하지 못했습니다: %s", exc)


def update_state(**values) -> None:
    """일부 키만 바꿔 저장합니다."""
    current = load_state()
    current.update(values)
    save_state(current)


def last_folder() -> Path | None:
    """마지막으로 연 폴더. 지금도 존재할 때만 돌려줍니다.

    지워졌거나 뽑아 둔 외장 드라이브를 가리키면 없는 것으로 칩니다.
    """
    value = load_state().get("last_folder")
    if not value:
        return None
    path = Path(value)
    return path if path.is_dir() else None


def remember_folder(folder: Path) -> None:
    update_state(last_folder=str(Path(folder)))


def language() -> str | None:
    """고른 인터페이스 언어. None이면 시스템 설정을 따릅니다.

    기기별 상태에 둡니다 — 같은 프리셋을 다른 언어를 쓰는 사람과 주고받아도
    각자의 화면 언어는 그대로여야 합니다.
    """
    value = load_state().get("language")
    return value if isinstance(value, str) and value else None


def set_language(code: str | None) -> None:
    update_state(language=code or "")


def update_check_enabled() -> bool:
    """업데이트 확인을 켜 두었는지. **기본은 꺼짐입니다.**

    확인은 외부 서버에 요청을 보냅니다. 사진 편집 도구가 묻지도 않고
    네트워크로 나가면 안 됩니다 — 켜는 것은 사용자가 정합니다.
    """
    return bool(load_state().get("update_check", False))


def set_update_check(enabled: bool) -> None:
    update_state(update_check=bool(enabled))
