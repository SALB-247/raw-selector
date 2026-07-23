"""Where the GUI looks for its own resources.

Kept apart from `core.appinfo` so the translation lookup does not drag the
core package into a Qt-shaped dependency.
"""

from __future__ import annotations

from pathlib import Path

from ..core.appinfo import app_root


def translations_dir() -> Path:
    """Compiled `.qm` files, next to the executable in a build."""
    return app_root() / "data" / "translations"
