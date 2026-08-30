"""The display order of the grid.

There are times you want everything lined up on score alone, regardless of
the scene (group) - sweeping quickly through the best and the most shaken
frames of the whole batch. The sort is a pure function, so it is verified
without a UI. Ties are always stabilised on the filename, so sorting the
same batch twice never shifts the order.
"""

from __future__ import annotations

from enum import Enum

from .types import ImageRecord


class SortMode(str, Enum):
    FILE = "file"              # filename (~capture order); scenes cluster
    SCORE_DESC = "score_desc"  # highest score first
    SCORE_ASC = "score_asc"    # lowest score first


# The display wording does not live here. core does not import Qt, so there
# is no way to translate it, and as a module constant the language would be
# frozen at import time. See gui/ordering_text.py.


def sort_records(records: list[ImageRecord], mode) -> list[ImageRecord]:
    """Returns a new list (the original order is not touched).

    Sorting by score ignores groups entirely and lines the whole batch up
    in one row.

    mode takes a SortMode or its value string. PySide6 turns an Enum that
    inherits str into a plain str when it stores it as combo box data (the
    same trap as GeometrySettings.ratio), so comparing with `is` alone
    misses every time and it quietly falls back to file order.
    """
    try:
        mode = SortMode(mode)
    except ValueError:
        mode = SortMode.FILE

    if mode is SortMode.SCORE_DESC:
        return sorted(records, key=lambda r: (-r.score, r.path.name))
    if mode is SortMode.SCORE_ASC:
        return sorted(records, key=lambda r: (r.score, r.path.name))
    return sorted(records, key=lambda r: r.path.name)  # FILE
