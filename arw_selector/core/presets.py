"""Preset store.

The scoring criteria and the adjustment settings both use the same
structure. They are saved as YAML in a settings folder under the user's
home, so they can be loaded on another shoot or in another session as they
are, and the file can be opened and edited by hand or handed to someone
else.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

from .appinfo import (  # noqa: F401 (re-export)
    APP_DIR_NAME,
    LEGACY_APP_DIR_NAMES,
    _config_root,
    data_dir,
)

SELECT_PRESET_DIR = "select_presets"
DEVELOP_PRESET_DIR = "develop_presets"
WATERMARK_PRESET_DIR = "watermark_presets"
EXPORT_PRESET_DIR = "export_presets"

_SAFE_NAME = re.compile(r"[^\w가-힣 _-]+")


def user_config_dir() -> Path:
    """The per-platform settings folder.

    Presets and lens profiles live in data/ next to the executable, because
    it is natural for them to come along when the whole app folder is
    moved. Only when it is installed somewhere that cannot be written to
    does it fall back to the user folder (see appinfo.data_dir).
    """
    return data_dir()


def migrate_legacy_config() -> Path | None:
    """Copies presets saved at an old location into the data folder in use
    now.

    It absorbs both moves:
      1) %APPDATA%/arw_selector   (the old product name)
      2) %APPDATA%/raw_selector   (same product name, back when it lived
                                   in AppData)
    The default now is data/ next to the executable, so whichever of the
    two is found first is brought over. Leave this out and the presets the
    user made look as though they have vanished wholesale.

    It **copies** rather than moves. That fits this tool's basic principle
    (do nothing that cannot be undone) and leaves an older version working
    as before if it is run again. If the target already has presets, it is
    not touched.

    The return value is the path of the old folder actually copied from
    (None if there was not one).
    """
    import shutil

    target = user_config_dir()
    # if even one preset is already there it is the user's current work,
    # so we do not overwrite it
    for subdir in (SELECT_PRESET_DIR, DEVELOP_PRESET_DIR):
        existing = target / subdir
        if existing.is_dir() and any(existing.glob("*.yaml")):
            return None

    root = _config_root()
    candidates = [root / APP_DIR_NAME, *(root / name for name in LEGACY_APP_DIR_NAMES)]
    for legacy in candidates:
        if legacy.resolve() == target.resolve() or not legacy.is_dir():
            continue
        if not any((legacy / sub).is_dir() for sub in (SELECT_PRESET_DIR, DEVELOP_PRESET_DIR)):
            continue
        try:
            target.mkdir(parents=True, exist_ok=True)
            for subdir in (SELECT_PRESET_DIR, DEVELOP_PRESET_DIR, "lensfun"):
                source = legacy / subdir
                if source.is_dir():
                    shutil.copytree(source, target / subdir, dirs_exist_ok=True)
        except OSError as exc:
            log.warning("예전 프리셋을 옮기지 못했습니다 (%s): %s", legacy, exc)
            return None
        log.info("예전 프리셋을 옮겨 왔습니다: %s -> %s", legacy, target)
        return legacy
    return None


def safe_filename(name: str) -> str:
    """Cleans a preset name up so it can be used as a filename.

    Even if the user puts a slash or '..' in the name, it must not escape
    the folder it was given.
    """
    cleaned = _SAFE_NAME.sub("", name).strip()
    cleaned = cleaned.replace("..", "").strip(". ")
    return cleaned[:80] or "이름없음"


@dataclass(frozen=True)
class PresetInfo:
    name: str
    path: Path
    modified: datetime

    @property
    def display(self) -> str:
        return self.name


class PresetStore:
    """A store that handles one kind of preset."""

    def __init__(self, subdirectory: str, root: Path | None = None):
        self.directory = (root or user_config_dir()) / subdirectory

    def ensure_dir(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[PresetInfo]:
        """Returns them in name order.

        Even if stat fails for a moment (antivirus briefly locking a file
        it has just scanned after saving), the preset is not dropped from
        the list. Otherwise a preset that has just been saved looks as
        though it has disappeared from the screen.
        """
        if not self.directory.exists():
            return []
        items = []
        for path in self.directory.glob("*.yaml"):
            try:
                modified = datetime.fromtimestamp(path.stat().st_mtime)
            except OSError:
                modified = datetime.min
            items.append(PresetInfo(name=path.stem, path=path, modified=modified))
        return sorted(items, key=lambda p: p.name.lower())

    def path_for(self, name: str) -> Path:
        return self.directory / f"{safe_filename(name)}.yaml"

    def exists(self, name: str) -> bool:
        return self.path_for(name).exists()

    def save(self, name: str, data: dict[str, Any]) -> Path:
        self.ensure_dir()
        path = self.path_for(name)
        payload = {
            "name": name,
            "saved": datetime.now().isoformat(timespec="seconds"),
            "data": data,
        }
        path.write_text(
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return path

    def load(self, name_or_path: str | Path) -> dict[str, Any]:
        """Returns the contents of the preset.

        A damaged file is raised as **either OSError or ValueError**. The
        screen side catches only those two and shows a warning dialog
        (gui/preset_bar.py and others), but the `yaml.YAMLError` the YAML
        parser raises is not a ValueError, so it slips through that net. It
        then escapes past the Qt slot and, instead of a warning dialog, the
        user sees the app disappear. We convert it here.
        """
        path = (
            Path(name_or_path)
            if isinstance(name_or_path, Path) or str(name_or_path).endswith(".yaml")
            else self.path_for(str(name_or_path))
        )
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"프리셋을 읽지 못했습니다 ({path.name}): {exc}") from exc
        if not isinstance(payload, dict) or "data" not in payload:
            raise ValueError(f"프리셋 형식이 아닙니다: {path.name}")
        data = payload["data"]
        if not isinstance(data, dict):
            raise ValueError(f"프리셋 내용이 비어 있습니다: {path.name}")
        return data

    def delete(self, name: str) -> bool:
        path = self.path_for(name)
        try:
            path.unlink()
            return True
        except OSError:
            return False


def select_presets(root: Path | None = None) -> PresetStore:
    """Scoring criteria presets."""
    return PresetStore(SELECT_PRESET_DIR, root)


def develop_presets(root: Path | None = None) -> PresetStore:
    """Adjustment presets."""
    return PresetStore(DEVELOP_PRESET_DIR, root)


def export_presets(root: Path | None = None) -> PresetStore:
    """Export option sets - format, size, naming, folders - by name, so
    "web 2048 JPEG" or "print TIFF" is one pick instead of eight fields."""
    return PresetStore(EXPORT_PRESET_DIR, root)


def watermark_presets(root: Path | None = None) -> PresetStore:
    """Watermark presets.

    Why they are kept apart from the adjustments: putting the same
    watermark on several looks, or a different watermark on the same look,
    is common. Bundled into one lump you would have to make a preset for
    every combination (see DevelopSettings.for_preset).
    """
    return PresetStore(WATERMARK_PRESET_DIR, root)


def default_develop_profiles() -> dict[str, dict]:
    """The camera profile presets shipped by default.

    They are 'looks' laid on top of the demosaic baseline (the standard
    profile). Colour temperature has to differ per frame so it is not
    included, which means they can be applied to any photo as they are.
    """
    from .develop import (
        BasicSettings,
        ColorGradeSettings,
        ColorGradeZone,
        DevelopSettings,
        HSLBand,
        HSLSettings,
    )

    def hsl(**bands: HSLBand) -> HSLSettings:
        return HSLSettings(bands=dict(bands))

    profiles = {
        # standard = the baseline as it is (for resetting the look)
        "표준": DevelopSettings(),
        "인물": DevelopSettings(
            basic=BasicSettings(contrast=-8, clarity=-12, vibrance=10, saturation=-3),
            hsl=hsl(orange=HSLBand(luminance=8, saturation=-5),
                    red=HSLBand(saturation=-4)),
        ),
        "풍경": DevelopSettings(
            basic=BasicSettings(contrast=14, clarity=8, vibrance=18, saturation=4),
            hsl=hsl(blue=HSLBand(saturation=10, luminance=-6),
                    green=HSLBand(saturation=8)),
        ),
        "선명": DevelopSettings(
            basic=BasicSettings(contrast=20, clarity=12, vibrance=12, saturation=20),
        ),
        "필름": DevelopSettings(
            basic=BasicSettings(contrast=-14, blacks=18, whites=-8, saturation=-10),
            color_grade=ColorGradeSettings(
                shadows=ColorGradeZone(hue=200, saturation=12),
                highlights=ColorGradeZone(hue=45, saturation=10),
                blending=50,
            ),
        ),
        "중립": DevelopSettings(
            basic=BasicSettings(contrast=-18, saturation=-8, clarity=-4),
        ),
    }
    return {name: settings.to_dict() for name, settings in profiles.items()}


def install_default_profiles(root: Path | None = None) -> int:
    """Installs the default profile presets once. Returns how many were
    installed.

    If they were installed already (the marker exists) it is skipped. The
    marker makes it a one-time write so that presets the user deleted are
    not brought back every time. If the user is already using the same
    name, it is not overwritten.
    """
    store = develop_presets(root)
    store.ensure_dir()
    marker = store.directory / ".profiles_installed"
    if marker.exists():
        return 0
    installed = 0
    for name, data in default_develop_profiles().items():
        if not store.exists(name):
            store.save(name, data)
            installed += 1
    marker.write_text("1", encoding="utf-8")
    return installed


def default_select_presets() -> dict[str, dict]:
    """The scoring (culling) presets shipped by default.

    They are the code defaults (ScoreConfig, GroupConfig) as they are. So
    that presets carrying a personal shooting context do not end up in the
    build, only these generic values are generated from code and put in -
    the same approach as the adjustment presets.
    """
    from dataclasses import asdict

    from .config import GroupConfig, ScoreConfig

    return {
        "기본": {
            "score": asdict(ScoreConfig()),
            "group": asdict(GroupConfig()),
        }
    }


def install_default_select_presets(root: Path | None = None) -> int:
    """Installs the default scoring presets once. Returns how many were
    installed.

    Same rule as install_default_profiles (a marker makes it one-time, the
    user's own are not overwritten). There used to be no defaults for the
    scoring presets, so once personal presets were kept out of the build,
    the build had no scoring presets at all.
    """
    store = select_presets(root)
    store.ensure_dir()
    marker = store.directory / ".select_installed"
    if marker.exists():
        return 0
    installed = 0
    for name, data in default_select_presets().items():
        if not store.exists(name):
            store.save(name, data)
            installed += 1
    marker.write_text("1", encoding="utf-8")
    return installed
