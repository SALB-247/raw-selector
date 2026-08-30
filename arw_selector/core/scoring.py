"""Combining scores and assigning grades.

The most expensive error is a false reject. Throw away a usable frame and
the user never finds out, whereas an ambiguous frame left in review is
settled by one look with the eye. That is why scoring leans conservative
on the reject side.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import FACE_BONUS_AREA_RANGE, ScoreConfig
from .types import FocusSource, Grade, ImageRecord

def roi_trust(source: FocusSource, config: ScoreConfig) -> float:
    """How far to trust roi_sharpness, per ROI source.

    If the eyes/face were caught, the sharpness inside them is the basis
    for scoring outright. A tile estimate may not be the main subject, so
    part of the weight is handed over to the whole frame.
    """
    return {
        FocusSource.EYE: config.trust_eye,
        FocusSource.FACE: config.trust_face,
        # The AF position is "where the camera put the focus", so it is
        # trusted at the same level as a tile estimate. No extra setting is
        # added - zone AF points at the subject (the torso) but is not as
        # accurate as the eyes (RESEARCH_METADATA.md).
        FocusSource.AF: config.trust_tile,
        FocusSource.TILE: config.trust_tile,
        FocusSource.FRAME: config.trust_frame,
    }.get(source, 0.5)


SHARPNESS_SCALE = 0.5
"""The scale the sharpness term takes in the score **in face-priority mode**.

If sharpness alone used all of 0~100, turning on even a little bonus pins
everything to 100 straight away. Then every well-shot frame ends up with
the same score and the ranking disappears - it stops doing the most
important thing a culler does, 'which of these is better'.

Pressed down to half, sharpness uses 0~50 and the remaining 50 is left as
room for the face/eye signals.

**Change this value and the absolute score thresholds (keep_above,
reject_below and so on) have to move with it.** Otherwise everything
becomes a reject.
"""

SHARPNESS_SCALE_NO_FACE = 1.0
"""The scale when face-priority mode is **off**.

With the mode off, the face/eye bonuses and penalties all drop out. Use
0.5 even then and sharpness alone tops out at 50 points, so the upper half
of the score is empty wholesale - measured (A6700, 2845 frames) the
maximum was 45.1 points, so no frame reaches the keep threshold of 65.

Sharpness takes back the room the face signals used to hold. Measured, the
maximum is 90.2 points with 0 frames pinned to 100, so the ranking is
alive.
"""

# Once the sharpness gap between face and background reaches this value
# (on the 0~100 scale), the penalty/scoring saturates.
_FACE_DEFOCUS_SCALE = 40.0

#: The point at which the face size weighting effectively becomes 0 (as a
#: ratio of the reference area).
#:
#: The square-root curve only touches 0, it never breaks off part way, so
#: a face 1/1000 of the reference still takes about 3%. Giving even a
#: little bonus to a face a few dozen pixels across contradicts the
#: judgement that it "is not worth receiving one", so it is cut off here.
#: At a 5% reference that is 0.05%, i.e. 114x114 pixels at 26MP.
_FACE_WEIGHT_CUTOFF = 0.01


def sanitized_config(config: ScoreConfig | None) -> ScoreConfig:
    """A copy with unusable settings put back. Always passed once before
    scoring.

    It is needed because settings do not come only from the GUI spinboxes.
    The scoring preset is YAML, and the user opens and edits it directly or
    takes one from someone else. YAML reads `.nan` and `.inf` straight in
    as floats, and numbers outside the widget range pass through without
    any resistance at all.

    Left unfiltered, it collapses **at the moment grades are assigned,
    after the analysis has all finished**:

      - If reject_percentile is out of range, np.percentile throws a
        ValueError and the analysis result for 4000 frames disappears
        wholesale.
      - A NaN weight raises no exception and only turns the score into
        NaN. NaN is False against any comparison, so the grade falls
        through to 'caught by no condition at all' - harder to find than
        an exception.

    Which way each item is put back differs per item. The percentile is cut
    to the nearest end (0 keeps its meaning of 'no relative threshold' and
    100 of 'everything is bottom'). The remaining figures fall back to
    their defaults. Only the target ratio is sent as None rather than the
    default - because changing an unreadable value to 0.10 would quietly
    impose a ratio the user never asked for.
    """
    from dataclasses import fields, replace

    config = config or ScoreConfig()
    # Pass the whole Config in by mistake and the loop below dies within
    # five frames with "'Config' object has no attribute 'trust_eye'". Not
    # even the name of the real problem shows up, and we were actually
    # caught by it once while wiring up the score card.
    if not isinstance(config, ScoreConfig):
        raise TypeError(
            f"ScoreConfig가 필요합니다 ({type(config).__name__}을 받았습니다). "
            "전체 설정을 넘겼다면 .score를 넘기십시오."
        )
    fixes: dict[str, object] = {}

    for field_info in fields(ScoreConfig):
        value = getattr(config, field_info.name)
        if not isinstance(value, float) or np.isfinite(value):
            continue
        if field_info.name == "target_keep_ratio":
            fixes[field_info.name] = None
        else:
            fixes[field_info.name] = field_info.default

    percentile = fixes.get("reject_percentile", config.reject_percentile)
    if not 0.0 <= percentile <= 100.0:
        fixes["reject_percentile"] = float(np.clip(percentile, 0.0, 100.0))

    # The face reference area is cut for the same reason. At 0 or negative
    # every face receives the full bonus and the size weighting disappears
    # wholesale; above 1 no face reaches the reference and the portrait
    # bonuses all die. Both happen quietly.
    low, high = FACE_BONUS_AREA_RANGE
    area = fixes.get("face_bonus_full_area", config.face_bonus_full_area)
    if not low <= area <= high:
        fixes["face_bonus_full_area"] = float(np.clip(area, low, high))

    return replace(config, **fixes) if fixes else config


def _effective_trust(focus, config: ScoreConfig) -> float:
    """The ROI trust actually applied - **the setting is used as it is.**

    It used to pull the setting up in face-priority mode with a floor of
    0.85 for eyes / 0.75 for face. The intent was to let the face region
    lead the scoring, but any value below that floor got **no response at
    all** - lowering the eye setting to 0.75 and the face setting to 0.60
    did not move the score by a single digit.

    Quietly ignoring a setting is worse than the problem the floor was
    meant to block. Face-priority mode already does its job through
    bonus_focus_on_face / penalty_no_face / penalty_face_defocus - there is
    no reason for it to cover the trust as well.
    """
    return float(np.clip(roi_trust(focus.source, config), 0.0, 1.0))


def _face_bonus_weight(focus, config: ScoreConfig) -> float:
    """The bonus scale by face size (0~1).

    Face detection finds faces only a few dozen pixels across as well.
    Without looking at size, a face caught in the audience receives the
    same bonus as the main subject, and a frame where a spectator behind
    the stage was picked as the main subject comes up alongside a frame
    with the person caught large (reported from real use).

    At or above the reference area (config.face_bonus_full_area) it is
    received in full; below that it is made proportional by taking the
    **square root** of the area. Use the area as it is and a face at half
    size has its bonus drop to 1/4, which cuts even an ordinary full-body
    portrait.

    A very small face is cut off at 0 outright - see _FACE_WEIGHT_CUTOFF.
    """
    full_area = float(config.face_bonus_full_area)
    if full_area <= 0.0:
        return 1.0  # means "do not look at size"

    ratio = max(0.0, float(getattr(focus, "face_area_ratio", 0.0)))
    if ratio >= full_area:
        return 1.0
    if ratio < full_area * _FACE_WEIGHT_CUTOFF:
        return 0.0
    return float(np.sqrt(ratio / full_area))


def _face_defocus_penalty(focus, config: ScoreConfig) -> float:
    """The penalty (0 or more) for a frame "focused on the background, not
    the face".

    It grows the more the background sharpness exceeds the face ROI
    sharpness, and is weighted by the face detection confidence. A record
    from an old (v2) cache carries 0 there, which reads as "the background
    is smooth" and simply draws no penalty - see the comment below for why
    it must not fall back to frame_sharpness instead.
    """
    if not (config.face_priority and config.penalty_face_defocus and focus.face_count):
        return 0.0
    if focus.source not in (FocusSource.EYE, FocusSource.FACE):
        return 0.0

    # background_sharpness is the measured value of the sharpest region
    # outside the face. 0 is a valid measurement meaning "the background is
    # smooth" (a good portrait with shallow focus), not a missing value, so
    # it must not be replaced with frame_sharpness (the whole frame,
    # including the face) - doing so falsely penalises good portraits. An
    # old (v2) cache holds 0 so no penalty applies, but after a v3
    # re-analysis it is filled in properly, and the re-analysis is needed
    # anyway.
    deficit = focus.background_sharpness - focus.sharpness
    if deficit <= 0:
        return 0.0

    confidence = float(np.clip(focus.face_confidence, 0.0, 1.0))
    magnitude = min(1.0, deficit / _FACE_DEFOCUS_SCALE)
    return config.penalty_face_defocus * magnitude * max(0.5, confidence)


def _eyes_closed(focus, config: ScoreConfig) -> bool:
    """Whether to take the main subject as having their eyes closed.

    **A frame that could not be measured (-1) is not penalised.** Those are
    frames with no face, or eyes too small, or landmarks that could not be
    obtained because the subject runs off the edge of the frame. Treat what
    is unknown as bad and perfectly good distant frames get pushed out
    wholesale.
    """
    if not config.penalty_eyes_closed:
        return False
    value = getattr(focus, "eyes_open", -1.0)
    return 0.0 <= value < config.eyes_closed_below


# Score card item keys. Not screen text but **identifiers** - translation
# and tests attach against these values. Scatter the literals around and a
# typo passes quietly, and renaming misses one place.
LINE_FAILED = "failed"
LINE_SHARPNESS = "sharpness"
LINE_FACE_DEFOCUS = "face_defocus"
LINE_FOCUS_ON_FACE = "focus_on_face"
LINE_NO_FACE = "no_face"
LINE_FACE_DETECTED = "face_detected"
LINE_FACE_SIZE = "face_size"
LINE_EYE_DETECTED = "eye_detected"
LINE_EYES_CLOSED = "eyes_closed"
LINE_EYES_OPEN = "eyes_open"
LINE_EYES_UNKNOWN = "eyes_unknown"
LINE_HIGHLIGHT_CLIP = "highlight_clip"
LINE_SHADOW_CLIP = "shadow_clip"
LINE_EXTREME_LUMA = "extreme_luma"
LINE_CLAMPED = "clamped"

#: The eye state lines. Exactly one of the three always appears (when in
#: face-priority mode).
EYE_STATE_KEYS = (LINE_EYES_CLOSED, LINE_EYES_OPEN, LINE_EYES_UNKNOWN)

# Scoring reason keys. Separate from the score card keys - a reason says
# "why this grade", the score card says "where the points came from". Even
# the items that overlap are worded differently.
REASON_ERROR = "error"
REASON_ROI_SHARPNESS = "roi_sharpness"
REASON_FACE_COUNT = "face_count"
REASON_FACE_DEFOCUS = "face_defocus"
REASON_HIGHLIGHT_CLIP = "highlight_clip"
REASON_SHADOW_CLIP = "shadow_clip"
REASON_EYES_UNKNOWN = "eyes_unknown"
REASON_EYES_CLOSED = "eyes_closed"
REASON_EYES_OPEN = "eyes_open"
REASON_FRAME_BLACK = "frame_black"
REASON_FRAME_WHITE = "frame_white"
REASON_BATCH_BOTTOM = "batch_bottom"
REASON_BETTER_IN_GROUP = "better_in_group"
REASON_NOT_RAW = "not_raw"
REASON_AF_MISMATCH = "af_mismatch"


@dataclass(frozen=True)
class Reason:
    """One line of grade scoring reason.

    For the same reason as ScoreLine it carries only a key and figures -
    core does not import Qt, so it cannot build screen sentences. The
    English text lives in core/reason_text.py (untranslated, for the CLI)
    and gui/reason_text.py (translated), and a test keeps the two from
    drifting apart.
    """

    key: str
    params: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoreLine:
    """One line of the score card.

    key is a **translation key**, not a string used on screen as it is.
    params are the figures to fill into the reason text.

    If core built finished sentences there would be no way to translate
    them. This package does not import Qt - the analysis runs in
    ProcessPoolExecutor workers, and loading Qt into every worker is purely
    a waste. So only keys and numbers are handed over and the GUI builds
    the sentences (gui/score_card.py).
    """

    key: str
    value: float
    params: dict[str, object] = field(default_factory=dict)


def sharpness_scale(config: ScoreConfig) -> float:
    """The multiplier to apply to the sharpness term under these settings.

    Face-priority mode gives half the score over to the face/eye signals,
    so sharpness is pressed down to 0~50. Turn the mode off and those
    signals all disappear; leave the multiplier as it was and sharpness
    alone tops out at 50 points, so the upper half is empty wholesale -
    measured (A6700, 2845 frames) the maximum was 45.1 points, so not one
    frame reaches the keep threshold of 65.

    Conversely, raise only the multiplier and leave the face bonuses in
    place and 42 frames pin to 100 and the ranking disappears. The
    multiplier and the bonuses have to move **together**.
    """
    return SHARPNESS_SCALE if config.face_priority else SHARPNESS_SCALE_NO_FACE


def score_breakdown(
    record: ImageRecord, config: ScoreConfig | None = None
) -> tuple[list[ScoreLine], float]:
    """The score split up per item, and the final score.

    **compute_score uses this function.** Build the score card separately
    and it is certain to drift - because when the scoring rules are changed
    only one side gets changed. If the sum of the items shown on screen
    differs from the real score, it is not an explanation but a lie.
    """
    config = sanitized_config(config)
    if not record.ok:
        return [ScoreLine(LINE_FAILED, 0.0, {"error": record.error or ""})], 0.0

    focus = record.focus
    lines: list[ScoreLine] = []

    trust = _effective_trust(focus, config)
    scale = sharpness_scale(config)
    base = scale * (
        trust * focus.sharpness + (1.0 - trust) * focus.frame_sharpness
    )
    lines.append(ScoreLine(LINE_SHARPNESS, base, {
        "source": focus.source.value,
        "roi": focus.sharpness,
        "trust": trust,
        "frame": focus.frame_sharpness,
        "frame_weight": 1.0 - trust,
        "scale": scale,
    }))

    # Face-priority mode: if the face is soft but the background is
    # sharper, treat it as a frame where the focus missed and penalise it
    defocus = _face_defocus_penalty(focus, config)
    if defocus:
        lines.append(ScoreLine(LINE_FACE_DEFOCUS, -defocus, {
            "background": focus.background_sharpness,
            "face": focus.sharpness,
        }))

    # The face/eye signals **all** work only inside face-priority mode.
    #
    # It used to be that only the three focus-related ones were tied to
    # this mode and the face/eye bonuses attached regardless of it. So the
    # description "for landscape-led work turn it off and score on whole
    # frame sharpness alone" did not match the real behaviour. Making the
    # multiplier depend on the mode turns that mismatch fatal - with the
    # face bonuses still attached at a multiplier of 1.0, measured, 42
    # frames pin to 100 and the ranking disappears.
    if config.face_priority:
        # Favour a frame where the focus landed on the face, and lower one
        # with no face at all because it has none of the evidence this mode
        # was meant to look at. Take this out and frames with no face
        # overtake frames with a face on frame sharpness alone.
        if focus.face_count and focus.source in (FocusSource.EYE, FocusSource.FACE):
            if config.bonus_focus_on_face:
                lines.append(ScoreLine(
                    LINE_FOCUS_ON_FACE, config.bonus_focus_on_face))
        elif not focus.face_count:
            if config.penalty_no_face:
                lines.append(ScoreLine(LINE_NO_FACE, -config.penalty_no_face))

        # **Weighted by face size** - a small face caught in the audience
        # must not receive the same bonus as the main subject
        # (see _face_bonus_weight).
        face_weight = _face_bonus_weight(focus, config)
        weight_params = {
            "area": focus.face_area_ratio * 100.0,
            "threshold": config.face_bonus_full_area * 100.0,
            "weight": face_weight,
        }
        if focus.face_count:
            if config.bonus_face:
                lines.append(ScoreLine(
                    LINE_FACE_DETECTED, config.bonus_face * face_weight,
                    dict(weight_params)))
            if config.bonus_face_size:
                # Face area relative to the frame. Measured, a telephoto
                # portrait is around 0.2%, so multiplying it straight in
                # makes no visible difference. Normalised with 10% as full
                # marks.
                lines.append(ScoreLine(
                    LINE_FACE_SIZE,
                    config.bonus_face_size * min(1.0, focus.face_area_ratio / 0.10)))
        if focus.source is FocusSource.EYE and config.bonus_eye:
            # The eye bonus is weighted for the same reason. An 'eye
            # region' is caught on audience faces too, so leaving it off
            # makes it a detour around the size weighting.
            lines.append(ScoreLine(
                LINE_EYE_DETECTED, config.bonus_eye * face_weight,
                dict(weight_params)))

        # Eye state - open, +; closed, -. It is an item focus does not
        # screen out at all (closed eyes are in focus too), so the two are
        # pulled apart in both directions here. A frame that could not be
        # measured is neither - what is unknown is treated as neither good
        # nor bad. Do otherwise and a distant profile receives the same
        # bonus as a frontal portrait.
        eyes_open = getattr(focus, "eyes_open", -1.0)
        eye_params = {"ear": eyes_open, "threshold": config.eyes_closed_below}
        if _eyes_closed(focus, config):
            lines.append(ScoreLine(
                LINE_EYES_CLOSED, -config.penalty_eyes_closed, eye_params))
        elif eyes_open >= 0.0:
            lines.append(ScoreLine(
                LINE_EYES_OPEN, config.bonus_eyes_open, eye_params))
        else:
            lines.append(ScoreLine(LINE_EYES_UNKNOWN, 0.0))

    # If the highlights blew out badly it is hard to use regardless of
    # focus.
    if (
        config.penalty_highlight_clip
        and focus.clipped_highlights > config.max_clipped_highlights
    ):
        excess = focus.clipped_highlights - config.max_clipped_highlights
        lines.append(ScoreLine(
            LINE_HIGHLIGHT_CLIP,
            -min(config.penalty_highlight_clip, excess * 100.0),
            {"clipped": focus.clipped_highlights * 100.0,
             "allowed": config.max_clipped_highlights * 100.0}))

    if (
        config.penalty_shadow_clip
        and focus.clipped_shadows > config.max_clipped_shadows
    ):
        excess = focus.clipped_shadows - config.max_clipped_shadows
        lines.append(ScoreLine(
            LINE_SHADOW_CLIP,
            -min(config.penalty_shadow_clip, excess * 100.0),
            {"clipped": focus.clipped_shadows * 100.0,
             "allowed": config.max_clipped_shadows * 100.0}))

    # A completely dark or completely bright frame - lens cap, a misfired
    # shutter pointed at the sky, and so on
    if focus.mean_luma < 8.0 or focus.mean_luma > 247.0:
        lines.append(ScoreLine(
            LINE_EXTREME_LUMA, -config.penalty_extreme_luma,
            {"luma": focus.mean_luma}))

    total = sum(line.value for line in lines)
    clipped = float(np.clip(total, 0.0, 100.0))
    if clipped != total:
        lines.append(ScoreLine(
            LINE_CLAMPED, clipped - total, {"total": total}))
    return lines, clipped


def compute_score(record: ImageRecord, config: ScoreConfig | None = None) -> float:
    """The 0~100 score for one frame. Computed with no group or batch info.

    The weights all come from ScoreConfig. What matters differs per
    shooting style (face for portraits, whole-frame sharpness for
    landscapes), so nothing may be hardcoded.
    """
    return score_breakdown(record, config)[1]


def _reasons(
    record: ImageRecord,
    config: ScoreConfig,
    threshold: float,
    group_best: float | None = None,
) -> list[Reason]:
    """The grade scoring reasons. The user has to be able to accept them in
    the GUI.

    Returns keys and figures rather than sentences - see Reason.
    """
    reasons: list[Reason] = []
    if not record.ok:
        return [Reason(REASON_ERROR, {"error": record.error or ""})]

    focus = record.focus
    reasons.append(Reason(REASON_ROI_SHARPNESS, {
        "source": focus.source.value, "sharpness": focus.sharpness}))

    # If the original is not RAW the editing headroom is different. Without
    # saying so, the user pushes it like a RAW and cannot find why the
    # highlights will not come back.
    from .raw_io import is_editable_image  # avoid a circular import

    if is_editable_image(record.path):
        reasons.append(Reason(REASON_NOT_RAW,
                              {"format": record.path.suffix.lstrip(".").upper()}))

    if focus.face_count:
        reasons.append(Reason(REASON_FACE_COUNT, {"count": focus.face_count}))
    # If the camera's AF pointed at a different person from ours, attach a
    # "main subject uncertain" signal. It does not change the score -
    # following the AF is a loss (117 labels). Instead, when the two split,
    # our accuracy drops from 87% to 49%, so the user is made to look once
    # more at that point. Only when af_face and main_face are both valid
    # faces and differ from each other.
    if (getattr(focus, "af_face", -1) >= 0 and focus.main_face >= 0
            and focus.af_face != focus.main_face):
        reasons.append(Reason(REASON_AF_MISMATCH))
    if _face_defocus_penalty(focus, config) > 0:
        reasons.append(Reason(REASON_FACE_DEFOCUS, {
            "deficit": focus.background_sharpness - focus.sharpness}))
    if (
        config.penalty_highlight_clip
        and focus.clipped_highlights > config.max_clipped_highlights
    ):
        reasons.append(Reason(REASON_HIGHLIGHT_CLIP, {
            "percent": focus.clipped_highlights * 100.0}))
    if (
        config.penalty_shadow_clip
        and focus.clipped_shadows > config.max_clipped_shadows
    ):
        reasons.append(Reason(REASON_SHADOW_CLIP, {
            "percent": focus.clipped_shadows * 100.0}))
    # The eye state is **always** written. Write it only when it is
    # penalised and "the eyes are open", "closed but above the threshold so
    # nothing was cut" and "could not be measured at all" all look
    # identically like silence on screen. It becomes a situation where the
    # user has to find the closed-eye frames themselves.
    # The eye state feeds into the score only in face-priority mode. If it
    # showed in the reasons with the mode off, the user would tune the
    # threshold against a value that is not being used.
    eyes_open = getattr(focus, "eyes_open", -1.0)
    if config.face_priority:
        if eyes_open < 0.0:
            reasons.append(Reason(REASON_EYES_UNKNOWN))
        elif _eyes_closed(focus, config):
            reasons.append(Reason(REASON_EYES_CLOSED, {
                "ear": eyes_open, "threshold": config.eyes_closed_below}))
        else:
            reasons.append(Reason(REASON_EYES_OPEN, {
                "ear": eyes_open, "bonus": config.bonus_eyes_open}))
    if focus.mean_luma < 8.0:
        reasons.append(Reason(REASON_FRAME_BLACK))
    elif focus.mean_luma > 247.0:
        reasons.append(Reason(REASON_FRAME_WHITE))
    if record.score < threshold:
        reasons.append(Reason(REASON_BATCH_BOTTOM, {"threshold": threshold}))

    if group_best is not None:
        deficit = group_best - record.score
        if deficit >= config.reject_below_group_best:
            reasons.append(Reason(REASON_BETTER_IN_GROUP, {"deficit": deficit}))

    return reasons


def achievable_keep_floor(
    records: list[ImageRecord], config: ScoreConfig | None = None
) -> float:
    """The lower bound the target ratio can go down to.

    At least 1 frame is left per scene, so it cannot go below 'number of
    scenes / total frames'. Scenes caught by min_keep_score yield no keep,
    though, so they drop out of the lower bound. The GUI has to show this
    value or the user sets the target wrongly.
    """
    valid = [r for r in records if r.ok]
    if not valid:
        return 0.0

    config = sanitized_config(config) if config else None
    if config and config.keep_per_group <= 0:
        return 0.0  # with the scene guarantee off there is no lower bound

    minimum = config.min_keep_score if config else 0.0
    if minimum <= 0.0:
        return len({r.group_id for r in valid}) / len(valid)

    qualifying = {
        r.group_id for r in valid if r.group_rank == 0 and r.score >= minimum
    }
    return len(qualifying) / len(valid)


def groups_without_keep(records: list[ImageRecord]) -> set[int | None]:
    """The scenes with no keep at all.

    Turn the scene guarantee off or raise the quality floor and scenes
    appear out of which everything drops. The user has to be able to see
    which scenes those are in order to check the frames that were missed.
    """
    groups: dict[int | None, bool] = {}
    for record in records:
        has_keep = groups.get(record.group_id, False)
        groups[record.group_id] = has_keep or record.final_grade is Grade.KEEP
    return {group for group, has_keep in groups.items() if not has_keep}


def records_in_groups_without_keep(records: list[ImageRecord]) -> list[ImageRecord]:
    """The frames belonging to scenes with no keep. For showing separately
    in the grid."""
    targets = groups_without_keep(records)
    return [r for r in records if r.group_id in targets]


def dropped_groups(
    records: list[ImageRecord], config: ScoreConfig | None = None
) -> int:
    """The number of scenes out of which no keep comes at all.

    Reflects both the quality floor and the scene guarantee being released
    (keep_per_group=0).
    """
    config = sanitized_config(config)
    if config.min_keep_score <= 0.0 and config.keep_per_group > 0:
        return 0
    return len(groups_without_keep(records))


def _may_auto_keep(record: ImageRecord, config: ScoreConfig) -> bool:
    """Whether this frame may be given keep outright on score alone.

    The absolute threshold (keep_above) means "a frame saved without
    needing to look". In face-priority mode, with no face at all there is
    no evidence for that judgement - it could be a well-shot landscape or a
    wasted frame that missed the person, so a human has to look.

    The scene number-one qualification still stands, so if it is the best
    frame in that scene it is raised to keep. A scene never disappears
    wholesale.
    """
    if not config.face_priority:
        return True
    return bool(record.focus.face_count)


def _effective_keep_above(records: list[ImageRecord], config: ScoreConfig) -> float:
    """If a target ratio is set, derive the threshold back out of the batch
    score distribution.

    keep is the union of 'group number one' and 'at or above the
    threshold'. The group number ones are already fixed, so only the target
    count minus the number of groups has to be filled from the top of the
    rest.
    """
    if config.target_keep_ratio is None:
        return config.keep_above

    valid = [r for r in records if r.ok]
    if not valid:
        return config.keep_above

    target_count = round(len(valid) * config.target_keep_ratio)

    # Scenes caught by the quality floor that yield no keep have to be
    # taken out of the guaranteed count. Otherwise the target ratio comes
    # out lower than it really is.
    guaranteed = (
        {
            r.group_id for r in valid
            if r.group_rank == 0 and r.score >= config.min_keep_score
        }
        if config.keep_per_group > 0
        else set()
    )
    extra_needed = target_count - len(guaranteed)

    if extra_needed <= 0:
        # The target is below the lower bound - leaving only the group
        # number ones is the best that can be done
        return float("inf")

    others = sorted(
        (r.score for r in valid if r.group_rank != 0), reverse=True
    )
    if extra_needed >= len(others):
        return 0.0
    return others[extra_needed - 1]


def grade_records(
    records: list[ImageRecord], config: ScoreConfig | None = None
) -> list[ImageRecord]:
    """Compute scores -> rank within group -> assign grades. Modified in
    place and returned as they are.

    grouping.assign_groups() has to have run first so group_id is filled
    in.
    """
    config = sanitized_config(config)
    if not records:
        return records

    for record in records:
        record.score = compute_score(record, config)

    # Rank within the group by descending score. Ties are stabilised by
    # file name.
    by_group: dict[int | None, list[ImageRecord]] = {}
    for record in records:
        by_group.setdefault(record.group_id, []).append(record)

    group_best: dict[int | None, float] = {}
    for group_id, members in by_group.items():
        members.sort(key=lambda r: (-r.score, r.path.name))
        for rank, record in enumerate(members):
            record.group_rank = rank
        group_best[group_id] = members[0].score

    keep_above = _effective_keep_above(records, config)

    # The absolute threshold and the batch-relative threshold are read
    # together. Lighting conditions differ from batch to batch, so an
    # absolute value alone is unstable, and using the percentile alone
    # mechanically throws away the bottom 15% even in a batch where
    # everything came out well.
    valid_scores = [r.score for r in records if r.ok]
    if valid_scores:
        relative = float(np.percentile(valid_scores, config.reject_percentile))
        threshold = max(config.reject_below, relative)
    else:
        threshold = config.reject_below

    for record in records:
        if not record.ok:
            record.grade = Grade.REJECT
        elif record.score >= keep_above and _may_auto_keep(record, config):
            record.grade = Grade.KEEP
        elif (
            record.group_rank is not None
            and record.group_rank < config.keep_per_group
            and record.score >= config.min_keep_score
        ):
            # The top frames of a group are protected by default, because
            # a whole scene disappearing is a failure that is hard to
            # undo. Raise min_keep_score, though, and nothing is taken
            # when a whole scene falls short of the threshold.
            record.grade = Grade.KEEP
        elif record.score < threshold:
            record.grade = Grade.REJECT
        elif (
            group_best.get(record.group_id, 0.0) - record.score
            >= config.reject_below_group_best
        ):
            # There is a better frame of the same moment. A duplicate with
            # no reason to look at it.
            record.grade = Grade.REJECT
        else:
            record.grade = Grade.REVIEW

        record.reasons = _reasons(record, config, threshold, group_best.get(record.group_id))

    return records


def summarize(records: list[ImageRecord]) -> dict[str, int]:
    """Frame counts per grade. Based on the final grade, which reflects the
    user's manual scoring."""
    counts = {grade.value: 0 for grade in Grade}
    for record in records:
        counts[record.final_grade.value] += 1
    return counts
