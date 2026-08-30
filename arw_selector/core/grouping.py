"""Grouping of similar frames.

4000 frames is really "3~10 similar frames x several hundred groups". Line
them all up and take the top N and one scene that came out well hogs the
whole cull while other scenes drop out wholesale. Only by making groups and
taking the best of each group is it any use as a culling tool.

The boundary decision uses the capture time as its main signal. Measured,
time separated bursts from scene changes cleanly (0.16s within a burst vs
tens of seconds across a change), but visual similarity did not - a moving
subject shot with a telephoto changes the frame a great deal even within
0.16s, so the hash distance distribution within one burst overlaps the
distribution across a scene change. The detailed figures are in the
GroupConfig.scene_change_distance comment.

So the perceptual hash is left as a secondary signal that catches only the
"blatant change", and the threshold is tightened only when the visual
information is the sole evidence there is (no EXIF time).
"""

from __future__ import annotations

import cv2
import numpy as np

from .config import GroupConfig
from .types import ImageRecord

DHASH_SIZE = 8
"""An 8x8 comparison -> a 64-bit hash."""


def dhash(image_bgr: np.ndarray, size: int = DHASH_SIZE) -> int:
    """difference hash. Only the brighter/darker relation between adjacent
    pixels is kept.

    The value holds almost unchanged for the same scene even when exposure
    and white balance wobble, which suits grouping bursts.
    """
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY) if image_bgr.ndim == 3 else image_bgr
    resized = cv2.resize(gray, (size + 1, size), interpolation=cv2.INTER_AREA)
    bits = resized[:, 1:] > resized[:, :-1]

    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bit)
    return value


def hamming_distance(a: int, b: int) -> int:
    """How many bits differ between two hashes. 0 is an identical frame."""
    return bin(a ^ b).count("1")


def _sort_key(record: ImageRecord):
    """Capture time first, filename if there is none. A burst needs the
    sub-second part for the order to come out right."""
    capture = record.metadata.capture_time if record.metadata else None
    return (0, capture, record.path.name) if capture else (1, None, record.path.name)


def _seconds_between(a: ImageRecord, b: ImageRecord) -> float | None:
    if not (a.metadata and b.metadata):
        return None
    if not (a.metadata.capture_time and b.metadata.capture_time):
        return None
    return abs((b.metadata.capture_time - a.metadata.capture_time).total_seconds())


def assign_groups(
    records: list[ImageRecord], config: GroupConfig | None = None
) -> list[ImageRecord]:
    """Fills in record.group_id and returns the list as it is (modified in
    place).

    The order of the input list is not changed - the caller has an order of
    its own that it expects.
    """
    config = config or GroupConfig()
    if not records:
        return records

    ordered = sorted(records, key=_sort_key)

    group_id = 0
    anchor = ordered[0]
    group_size = 0

    for index, record in enumerate(ordered):
        if index == 0:
            record.group_id = group_id
            group_size = 1
            continue

        previous = ordered[index - 1]
        gap = _seconds_between(previous, record)

        # The frame comparison is against the anchor (the group's first
        # frame). Comparing only with the immediately preceding frame joins
        # a slowly changing pan shot into one enormous group.
        distance = None
        if record.dhash is not None and anchor.dhash is not None:
            distance = hamming_distance(record.dhash, anchor.dhash)

        if gap is not None:
            # Trust the time. The frame change steps in only on a blatant
            # transition.
            visual_split = (
                distance is not None and distance > config.scene_change_distance
            )
            starts_new_group = gap > config.time_gap_seconds or visual_split
        else:
            # With no EXIF time the frame change is the only evidence
            # there is - so the threshold is tightened.
            starts_new_group = (
                distance is not None and distance > config.no_time_hash_distance
            )

        starts_new_group = starts_new_group or group_size >= config.max_group_size

        if starts_new_group:
            group_id += 1
            anchor = record
            group_size = 0

        record.group_id = group_id
        group_size += 1

    return records


def group_counts(records: list[ImageRecord]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for record in records:
        if record.group_id is not None:
            counts[record.group_id] = counts.get(record.group_id, 0) + 1
    return counts
