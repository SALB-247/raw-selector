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


def scene_step(records: list[ImageRecord], current: int, direction: int) -> int | None:
    """The row of the first photo of the next (direction > 0) or previous
    scene, walking the list in its display order. None at the ends.

    A scene is a run of the same group_id. In file order the scenes are
    contiguous, so this steps burst by burst - 300 scenes is how a
    4000-frame shoot is actually worked. In a score-sorted list the runs
    are broken up and it steps to the next photo of a different scene,
    which is still the honest reading of "next scene" there.
    """
    if not records or not 0 <= current < len(records):
        return None
    group = records[current].group_id
    if direction > 0:
        row = current + 1
        while row < len(records) and records[row].group_id == group:
            row += 1
        return row if row < len(records) else None
    row = current
    while row > 0 and records[row - 1].group_id == group:
        row -= 1
    if row == 0:
        return None
    previous = records[row - 1].group_id
    while row > 0 and records[row - 1].group_id == previous:
        row -= 1
    return row


def scene_position(records: list[ImageRecord], current: int) -> tuple[int, int, int, int]:
    """(scene number, scene count, photo number within the scene, photos in
    the scene) for the status line, 1-based. Scenes are numbered by first
    appearance in the display order."""
    if not records or not 0 <= current < len(records):
        return 0, 0, 0, 0
    order: list = []
    for record in records:
        if record.group_id not in order:
            order.append(record.group_id)
    group = records[current].group_id
    members = [i for i, r in enumerate(records) if r.group_id == group]
    return order.index(group) + 1, len(order), members.index(current) + 1, len(members)
