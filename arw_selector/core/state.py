"""Per-machine state.

Unlike content such as presets, "the folder last opened" means something
only on that PC. Put in the app folder and carried on a USB stick it would
point at a path that does not even exist, so this alone lives in the user
folder (%APPDATA% and the like).

The app has to keep running even if both reading and writing fail. A
convenience feature must not block the app.
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
    """Reads the whole state. An empty dict if missing or broken."""
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
    """Writes the state wholesale. Failures are passed over quietly."""
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        log.debug("상태를 저장하지 못했습니다: %s", exc)


def update_state(**values) -> None:
    """Saves with only some of the keys changed."""
    current = load_state()
    current.update(values)
    save_state(current)


def last_folder() -> Path | None:
    """The folder last opened. Returned only if it still exists.

    If it points at something deleted, or at an external drive that has
    been unplugged, it counts as absent.
    """
    value = load_state().get("last_folder")
    if not value:
        return None
    path = Path(value)
    return path if path.is_dir() else None


def remember_folder(folder: Path) -> None:
    update_state(last_folder=str(Path(folder)))


def language() -> str | None:
    """The chosen interface language. None follows the system setting.

    Kept in the per-machine state - handing the same preset back and forth
    with someone using another language has to leave each person's screen
    language as it was.
    """
    value = load_state().get("language")
    return value if isinstance(value, str) and value else None


def set_language(code: str | None) -> None:
    update_state(language=code or "")


def camera_match_on_open() -> bool:
    """Whether to apply camera look matching automatically when the
    adjustment window opens. **Off by default.**

    Even when it is on, a frame that already has an adjustment (that is not
    neutral) is never touched - an automatic feature must not overwrite the
    user's edit. The decision itself is made by the adjustment window (core
    knows nothing about the screen's situation).
    """
    return bool(load_state().get("camera_match_on_open", False))


def set_camera_match_on_open(enabled: bool) -> None:
    update_state(camera_match_on_open=bool(enabled))


ANALYZE_OPTION_KEYS = (
    "noise_compensation",
    "af_roi_hint",
    "center_priority",
    "demosaic_small_preview",
)
"""The analysis options the start dialog offers.

These are exactly the ones that go into the cache fingerprint, which is why
they have to survive a restart. While they did not, turning any of them on
and analysing a folder meant that on the next launch the options fell back
to their defaults, the fingerprint no longer matched, and a full cache
counted as zero - the folder asked to be analysed again from scratch.
"""


def analyze_options() -> dict:
    """The saved analysis options. Empty while nothing has been saved, so
    the config defaults stand."""
    saved = load_state().get("analyze_options")
    if not isinstance(saved, dict):
        return {}
    return {key: bool(saved[key]) for key in ANALYZE_OPTION_KEYS if key in saved}


def set_analyze_options(**options) -> None:
    """Remembers the options. Unknown keys are dropped rather than stored -
    a stray key would go on to be handed to the config as a keyword."""
    keep = {key: bool(value) for key, value in options.items()
            if key in ANALYZE_OPTION_KEYS}
    if keep:
        update_state(analyze_options={**analyze_options(), **keep})


def update_check_enabled() -> bool:
    """Whether the update check has been turned on. **Off by default.**

    The check sends a request to an outside server. A photo editing tool
    must not go out onto the network without asking - turning it on is the
    user's decision.
    """
    return bool(load_state().get("update_check", False))


def set_update_check(enabled: bool) -> None:
    update_state(update_check=bool(enabled))
