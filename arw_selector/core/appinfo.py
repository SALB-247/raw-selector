"""Product identity and storage locations, gathered in one place.

The name is embedded in the storage paths, so with it scattered around as
strings everywhere, changing the product name loses the presets and the
undo logs. That is a problem we actually hit while renaming ARW Selector ->
RAW_selector, so it was gathered here to make any later rename a matter of
editing this one file.

There are two branches of storage location:

  data_dir()       `data/` next to the executable. Things that **have to
                   travel with the app**, such as presets, lens profiles
                   and logs. Copy the lot to a USB stick and they come
                   along.
  user_state_dir() %APPDATA% and the like. Only **state that means
                   something on that PC alone**, such as the folder last
                   opened.

Old names and locations are not deleted:
  - the old settings folder is copied to the new location at startup (the
    original is preserved).
  - the cache/log next to a photo folder is scattered across every folder
    so it cannot be moved; if the new name is not there, the old name is
    found and used. That is what keeps old exports undoable.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

APP_NAME = "RAW_selector"
"""The product name the user sees (window title, EXIF Software, executable
name)."""

APP_DIR_NAME = "raw_selector"
"""Settings folder name (%APPDATA%/... or ~/Library/Application
Support/...)."""

DATA_DIR_NAME = "data"
"""Name of the data folder kept next to the executable."""

CACHE_DIR_NAME = ".raw_selector_cache"
"""The folder kept next to the photo folder for the analysis cache and the
export undo logs."""

LOG_FILE_NAME = "raw_selector.log"

STATE_FILE_NAME = "state.json"
"""Per-machine state file (the folder last opened and so on)."""

LEGACY_APP_DIR_NAMES: tuple[str, ...] = ("arw_selector",)
"""Old settings folder names. Copied into the new folder at startup."""

LEGACY_CACHE_DIR_NAMES: tuple[str, ...] = (".arw_selector_cache",)
"""Old cache/log folder names. Read instead when the new name is absent."""


def app_root() -> Path:
    """The folder the executable is in (the repository root when running
    from source).

    Nuitka standalone puts __compiled__ into the compiled module.
    PyInstaller uses sys.frozen. Neither one means it is the source tree.
    """
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _config_root() -> Path:
    """The parent of the per-platform user settings folder."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return Path(base) if base else Path.home() / "AppData" / "Roaming"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    base = os.environ.get("XDG_CONFIG_HOME")
    return Path(base) if base else Path.home() / ".config"


def user_state_dir() -> Path:
    """Where state that means something on that PC alone lives (the folder
    last opened and so on).

    Content such as presets is not kept here - it would not come along if
    the app were moved to another PC.
    """
    return _config_root() / APP_DIR_NAME


def _is_writable(path: Path) -> bool:
    """Decides by actually trying to create a file.

    It may have been installed somewhere permission-blocked such as Program
    Files, so existence alone does not tell you.
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


#: What has to be copied into the user folder when running from a
#: write-blocked location. logs is left out because it is created at run
#: time.
_SEEDED_DIRS = ("select_presets", "develop_presets", "watermark_presets",
                "lensfun", "calibration")


def _seed_from_bundle(target: Path) -> None:
    """Puts the default data shipped alongside into the user folder.

    Running from a write-blocked location (inside the .app on macOS,
    Program Files on Windows) makes data/ fall back to the user folder, and
    at that point the **scoring presets and the lens profile XML bundled
    with it were invisible wholesale**. Only the adjustment presets are
    re-generated from code (`presets.install_default_profiles`), so that
    side alone was fine, which made it harder to notice.

    Measured (0.15.1, run straight from the DMG): the self-check failed
    with 0 scoring presets, and the lens DB shrank from 1052 bodies / 1609
    lenses to 948 / 1304.

    Files that are already there are not touched - what the user has
    changed takes priority.
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
            # The app has to come up even if the copy fails. The
            # self-check counts what is missing.
            pass


@lru_cache(maxsize=1)
def data_dir() -> Path:
    """Where presets, lens profiles and logs live. Next to the executable
    by default.

    It is natural for the presets to come along when the whole app folder
    is copied. But if it was installed somewhere write-blocked (Program
    Files, inside the .app bundle, and so on), saving would not work at
    all, so in that case it falls back to the user folder.
    """
    candidate = app_root() / DATA_DIR_NAME
    if _is_writable(candidate):
        return candidate
    fallback = user_state_dir() / DATA_DIR_NAME
    fallback.mkdir(parents=True, exist_ok=True)
    # Having fallen back, bring over the bundle's default data. lru_cache
    # means this runs only once.
    _seed_from_bundle(fallback)
    return fallback


def is_portable() -> bool:
    """Whether the data is next to the executable (False if it fell back
    because it was not writable)."""
    return data_dir() == app_root() / DATA_DIR_NAME
