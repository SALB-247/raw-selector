"""Captions for the sort modes.

Kept out of `core.ordering` for the usual two reasons: core does not
import Qt, and a module-level dict of captions freezes the language at
import time.
"""

from __future__ import annotations

from ..core.ordering import SortMode
from .i18n import tr


def sort_label(mode: SortMode) -> str:
    return {
        SortMode.FILE: tr("By filename"),
        SortMode.SCORE_DESC: tr("Highest score first"),
        SortMode.SCORE_ASC: tr("Lowest score first"),
    }.get(mode, str(mode))
