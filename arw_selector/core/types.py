"""Common types passed between modules.

Everything here has to be picklable - macOS's ProcessPoolExecutor uses the
spawn method, so every value exchanged with a worker goes through pickle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from .raw_io import RawMetadata

if TYPE_CHECKING:
    from .develop import DevelopSettings


class Grade(str, Enum):
    """Culling grade. It inherits str, so it serialises to JSON as is."""

    KEEP = "keep"
    REVIEW = "review"
    REJECT = "reject"


OUTPUT_DIR_NAMES: frozenset[str] = frozenset({"_keep", "_review", "_reject"})
"""Folder names export creates. Excluded when rescanning."""


class FocusSource(str, Enum):
    """Where the ROI used for focus scoring came from."""

    EYE = "eye"          # eye region from face landmarks - most trustworthy
    FACE = "face"        # a face was found but the eye ROI is too small
    AF = "af"            # no face, the AF position the camera recorded
    TILE = "tile"        # no face, the sharpest of the grid tiles
    FRAME = "frame"      # fallback: the whole frame


@dataclass(frozen=True)
class FocusResult:
    """The focus measurement for one frame."""

    sharpness: float          # final sharpness normalised 0~100 (ROI)
    laplacian: float          # normalised Laplacian variance
    tenengrad: float          # normalised Tenengrad
    source: FocusSource
    frame_sharpness: float = 0.0
    """Sharpness measured over the whole frame at a fixed scale, regardless
    of the ROI.

    The ROI choice can change from frame to frame (face detection
    succeeding or failing), and once the ROI changes, sharpness values are
    not comparable with each other. frame_sharpness is always measured the
    same way, so it is a stable baseline for comparing frames.
    """
    roi: tuple[int, int, int, int] | None = None  # preview coords (x, y, w, h)
    face_count: int = 0
    face_confidence: float = 0.0
    face_area_ratio: float = 0.0   # face area vs frame - small faces are less reliable
    background_sharpness: float = 0.0
    """When a face ROI is used, the sharpness of the sharpest region outside
    the face box.

    It is the signal for screening out frames "focused on the background,
    not the face". If the face is soft while the background is crisp, this
    value is higher than the face ROI sharpness. Used for the penalty
    decision in face-priority mode. It is 0 (not applicable) when there is
    no face, and old cache records are 0 too, in which case it falls back
    to frame_sharpness.
    """
    clipped_highlights: float = 0.0  # 0~1
    clipped_shadows: float = 0.0     # 0~1
    mean_luma: float = 0.0

    faces: tuple[tuple[int, int, int, int], ...] = ()
    """(x, y, w, h) of every detected face. In preview coordinates.

    Holding only the one main subject means you cannot see on screen "why
    that face was picked". We draw them all and mark only the main subject
    by colour. Old caches do not have it so it is an empty tuple, and in
    that case only the ROI is shown.
    """

    main_face: int = -1
    """Index of the main subject within faces. -1 if there is no face or the
    cache is old."""

    face_scores: tuple[float, ...] = ()
    """Detection confidences, in the same order as faces.

    Used to decide whether to draw it on screen and whether it can become
    the main subject. Checking real shooting samples by eye, the 0.60~0.75
    band was crowded with false detections - speaker cones, gloves, dark
    smudges. But real faces (profiles, motion blur, stage lighting) sit in
    that same band as well, so **cutting the detection itself loses 16% of
    the real faces** (measured). So counting is left as it is, and only
    what gets shown and what may become the main subject are filtered by
    confidence.
    """

    eyes_open: float = -1.0
    """Eye aspect ratio (EAR) of the main subject. -1 if it could not be
    measured.

    The smaller, the more closed. Of the two eyes it uses the **more open
    one** - on a profile the far eye is barely visible and always comes out
    as 'closed', and penalising on that drops every side-on frame.

    Measured on 152 frames the user labelled (causes broken out):

        thresh  accuracy  false pen.   caught
        0.22       76%        8.0%      57.5%
        0.25       79%       10.3%      67.1%   <- default
                                                   (config.eyes_closed_below)
        0.28       82%       16.2%      79.2%

    The full table and the reason 0.25 was chosen are in
    `config.ScoreConfig.eyes_closed_below`. If the numbers here disagree
    with those, those are the right ones.

    -1 (could not measure) is not penalised. That is the case where there
    is no face, it is too small, or it straddles the edge of the frame so
    no landmarks could be obtained.
    """

    af_face: int = -1
    """The face number the camera's AF pointed at (an index into faces). -1
    if there is none.

    **The main subject is not overwritten with this.** Measured (117
    frames): when the two disagreed, following AF improved 27 frames and
    made 36 worse - a net loss.

    Instead it is used as a **confidence signal** - when AF points at the
    same person we do, our pick is right 88% of the time; when it differs,
    52% (a coin toss in practice).
    """

    source_width: int = 0
    source_height: int = 0
    """What size of image the roi and faces coordinates are relative to.

    Coordinates with no reference size cannot be interpreted. The screen
    used to guess that "the embedded preview's width = the sensor width",
    but there are cases like the Panasonic S1R, a 47-megapixel body, that
    embed only a 1920px preview, and the boxes were drawn 4.37x off
    (measured). We measure instead of guessing.

    Old caches do not have it so it is 0, and in that case the screen side
    measures the preview directly.
    """


@dataclass
class ImageRecord:
    """Everything the pipeline accumulates for one frame."""

    path: Path
    metadata: RawMetadata | None = None
    focus: FocusResult | None = None
    error: str | None = None

    dhash: int | None = None
    """Scene fingerprint. Used for grouping.

    Computed while the analysis stage already has the preview in memory
    anyway. Getting it again later would mean re-decoding all 4000 frames,
    so it goes into the cache as well.
    """

    place_id: int | None = None
    """The number that groups frames from the same place (core/places.py).
    None if there is no GPS.

    It is a different axis from the scene (group_id). A scene is a burst
    within 3 seconds; there are only a few places in a day. Used to split
    into per-place folders on export.
    """

    # filled in by the grouping/scoring stages
    group_id: int | None = None
    group_rank: int | None = None
    score: float = 0.0
    grade: Grade = Grade.REVIEW
    reasons: list = field(default_factory=list)
    """The grounds for the scoring (scoring.Reason). Keys and figures, not
    sentences.

    core does not import Qt, so it cannot build screen sentences. The
    wording lives in core/reason_text.py (English, for the CLI) and
    gui/reason_text.py (translated). The type is not pinned to
    scoring.Reason because of a circular import.
    """
    manual_grade: Grade | None = None  # the grade the user overrode in the GUI

    manual_main_face: int | None = None
    """The main subject face number the user picked directly on screen.

    It is the escape hatch for when the automatic pick is wrong (a
    passer-by in the front row, a smudge in the audience). When this value
    is set, focus has already been recomputed against that face - changing
    only the display and leaving the score alone gives you the 'I fixed it
    but the grade did not change' state, which is more confusing.
    """

    develop: "DevelopSettings | None" = None
    """The adjustment assigned to this frame. Applied on export.

    It is the user's edit, not an analysis result, so it does not go into
    the cache - the cache is thrown away when the file content changes,
    whereas an edit has to survive regardless of that.
    """

    @property
    def final_grade(self) -> Grade:
        """The user's manual scoring always beats the automatic one."""
        return self.manual_grade or self.grade

    @property
    def ok(self) -> bool:
        return self.error is None and self.focus is not None

    @property
    def main_face_norm(self) -> tuple[float, float, float, float] | None:
        """Normalised (x, y, w, h) of the main subject's face, 0~1. None if
        there is none.

        Handed to the adjustment side to tie the face mask's 'main subject'
        to the same face as the red box on screen. The reason it is passed
        as a ratio rather than a resolution is that the mask runs on top of
        images of different sizes in the preview, in Full Render and on
        export.
        """
        focus = self.focus
        if focus is None or not focus.faces:
            return None
        if not 0 <= focus.main_face < len(focus.faces):
            return None
        width = focus.source_width or 0
        height = focus.source_height or 0
        if width <= 0 or height <= 0:
            return None
        x, y, w, h = focus.faces[focus.main_face]
        return (x / width, y / height, w / width, h / height)
