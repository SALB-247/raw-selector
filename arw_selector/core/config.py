"""Settings. The right value differs per shooting style, so nothing here is
hardcoded."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from . import focus

log = logging.getLogger(__name__)

#: The range face_bonus_full_area may take (face area relative to frame).
#:
#: The 0.1% lower bound is roughly 160x160 pixels at 26MP. Stage shooting
#: from a distance with a telephoto has the main subject's face clustered
#: at 0.1~0.6% (measured on 2845 A6700 frames), and a setting that cannot
#: point into that band is meaningless.
#: The 50% upper bound is a close-up with the face covering half the
#: screen; above that there is no point.
#:
#: The GUI spinbox and sanitized_config() have to see the same values, so
#: they live here.
FACE_BONUS_AREA_RANGE = (0.001, 0.50)


@dataclass
class AnalyzeConfig:
    """Focus analysis parameters. Change these and the cache is invalid."""

    detect_long_edge: int = focus.DETECT_LONG_EDGE
    laplacian_k: float = focus.LAPLACIAN_K
    tenengrad_k: float = focus.TENENGRAD_K

    noise_compensation: bool = True
    """Subtract the noise contribution from sharpness (focus reading v4).

    Turned off, this is the same measurement as v3. Measured over 2846
    frames, turning it on demoted noisy soft bursts while the keep count
    stayed the same - turning it off is for comparison and verification.
    Exposed as the "Precision" item of the analysis dialog.
    """

    center_priority: bool = False
    """Single-subject composition first - pick the main subject mainly by
    centrality.

    For genres that put the protagonist dead centre, such as portraits and
    fan-site shooting. Validated at 95.4% on 47,990 frames of implicit
    ground truth (the current pick: 77.9%). On 110 frames labelled as the
    stage/group genre the current one wins instead (75.5% vs 68.2%), so the
    default is off - only the user knows the genre. It goes into the cache
    key automatically, so the two options split their caches.
    """

    af_roi_hint: bool = False
    """Use the camera's AF position as the scoring region on frames where no
    face was found.

    Read from Sony 0x2027 and Nikon AFInfo2 (maker_meta). It never steps in
    when a face or eye ROI exists - the position zone AF records is not the
    eye (measured on 47 frames, RESEARCH_METADATA.md), so it cannot beat
    face detection. Off by default: turning it on changes the score of
    frames that used to be scored as TILE, so it is optional.
    """

    demosaic_small_preview: bool = False
    """Demosaic and analyse RAWs whose embedded preview is far smaller than
    the sensor.

    Panasonic RW2 is this case - measured (DC-S5M2X): the sensor is
    6008x4008 but the embedded preview is only 1920px (32% of the long
    edge, 10% by pixel count). Sony ARW and Canon CR2/CR3 are at 98~99%, so
    they do not apply.

    Scoring's premise that sharpness is measured at full resolution is
    broken on files like these. Turned on, analysis runs on a half
    demosaic (50% of the long edge), which brings the premise back. In
    exchange it is slow - measured 15.9 -> 79.9ms per frame (12 workers).

    Since the preview/sensor ratio is measured per file to decide
    (raw_io.load_preview), a body that is not in the extension list but is
    in the same situation gets rescued along with it.
    """

    def cache_key(self) -> str:
        """A fingerprint of everything that affects the analysis result.

        Not just the settings but the algorithm version has to go in. Even
        with the settings unchanged, old results are invalid once the
        measurement method changes, and with the version left out the cache
        hands back the old scores so the fix never takes effect.
        """
        payload = dict(asdict(self))

        # **Off gives the same result as before, so the fingerprint has to
        # be the same too.** Just dropping the new field in would make even
        # people who never use this option re-analyse whole folders. It
        # rides in the fingerprint only when on, splitting the cache per
        # option.
        if not payload.get("demosaic_small_preview", False):
            payload.pop("demosaic_small_preview", None)

        payload["_algorithm"] = focus.ALGORITHM_VERSION
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


@dataclass
class GroupConfig:
    """Similar-frame grouping parameters."""

    time_gap_seconds: float = 3.0
    """Past this gap it is a different group. The main signal for grouping.

    Measured (A6700, 2845 frames), the median gap within a burst was 0.16s
    and p90 was 1.4s, while real scene changes were tens to hundreds of
    seconds. Time separates the two situations cleanly.
    """

    scene_change_distance: int = 40
    """dHash Hamming distance threshold (out of 64 bits). A secondary
    signal, so it is set loose.

    Measured on the same batch, the visual signal alone could not separate
    bursts from scene changes.
      - Distance within one burst (<0.5s): median 11, p90 25, p99 37
      - Distance across a scene change (>60s): median 29, p10 23
    The distributions overlap, so no threshold separates the two. Switching
    to histogram correlation made no difference (burst p10 0.651 vs change
    p90 0.732).

    It is because shooting a moving subject with a telephoto changes the
    frame a great deal even within 0.16s. So this value is left to catch
    only the blatant case, "a burst, and yet the frame flipped over
    completely" (40 and up, past the burst p99 of 37). For still-life or
    portrait-led shooting it works well set lower.
    """

    no_time_hash_distance: int = 16
    """The threshold used only when the EXIF capture time is missing.

    In that case the visual signal is the only evidence there is, so left
    loose everything ends up in one group.
    """

    max_group_size: int = 40
    """Runaway guard. A group that grows past this is forcibly cut."""


@dataclass
class ScoreConfig:
    """Parameters for combining scores and assigning grades."""

    # --------------------------------------------------------- score weights
    #
    # score = (ROI sharpness x trust + frame sharpness x (1 - trust)) x 0.5
    #         + bonuses - penalties
    #
    # For why sharpness is multiplied by 0.5, see scoring.SHARPNESS_SCALE -
    # if sharpness alone used all of 0~100, turning on even a little bonus
    # pins everything to 100 and the ranking disappears.
    # **The absolute score thresholds below all assume that scale
    # (sharpness 0~50).**
    #
    # Trust means how far this ROI is to be believed. If the eyes were
    # caught, the sharpness inside them is the basis for scoring outright;
    # but a tile estimate may not be the main subject, so part of the weight
    # is handed over to the whole frame.

    trust_eye: float = 0.75
    """Weight of the ROI sharpness when the eye region was caught."""

    trust_face: float = 0.60
    """When a face was caught but the eye ROI is too small."""

    trust_tile: float = 0.55
    """When there is no face and a grid tile was used to estimate."""

    trust_frame: float = 0.40
    """When no ROI could be caught and the whole frame is used."""

    face_priority: bool = True
    """Face-priority mode. The default for portrait-led shooting (the
    A6700's primary use here).

    With it on, frames where a face was caught trust the face/eye ROI more,
    and a frame where the face is soft but the background is sharper (focus
    fell behind) is penalised by penalty_face_defocus. For a landscape-led
    batch it can be turned off to score on whole-frame sharpness alone.
    """

    penalty_face_defocus: float = 15.0
    """The maximum penalty in face-priority mode when the background is
    sharper than the face.

    It screens out frames "focused on the background, not the face". The
    size of the penalty is proportional to how much sharper the background
    is than the face, and it is weighted by the face detection confidence
    (an uncertain detection is penalised less). It does not apply when
    there is no face or the mode is off.
    """

    bonus_focus_on_face: float = 5.0
    """Points added in face-priority mode when the focus ROI really is a
    face/eye.

    A face being in the frame and that face being in focus are two
    different things. This bonus attaches only to the latter - because that
    is what portrait culling wants. bonus_face attaches as soon as 'there
    is a face at all', so it is a different animal.
    """

    penalty_no_face: float = 10.0
    """The penalty in face-priority mode when there is no face at all.

    Measured (A6700, 2845 frames): the median score of frames with no face
    was 59.0, actually higher than the 47.6 of frames focused on a face.
    Frames with a face are measured on the (usually softer) face ROI and
    take the background-focus penalty on top, while frames with no face use
    the frame sharpness as it is, with no penalty. The result was that in
    face-priority mode, frames with no face were auto-kept 4 times more
    often.

    This penalty is the correction that puts the two populations back on
    comparable terms. For landscape-led work, turn face_priority off - then
    it does not apply.
    """

    bonus_face: float = 20.0
    """Points added when a face is detected. Raised for portrait-led work.

    It attaches multiplied by the size weighting of face_bonus_full_area -
    a small face does not receive all of this value.
    """

    bonus_eye: float = 15.0
    """Points added on top when the eyes were caught as well."""

    penalty_eyes_closed: float = 20.0
    """The penalty when the main subject appears to have their eyes closed.

    A frame with closed eyes is unusable however good the focus is, and
    sharpness does not screen it out at all - closed eyes are in focus too.

    It is the largest of the penalties. The user set the value themselves,
    meaning frames with closed eyes are to be taken out of the auto-keep
    candidates altogether. But the call is not perfect (see
    eyes_closed_below), so a wrong call costs exactly that much.

    It pairs with bonus_eyes_open. The real score gap between an open frame
    and a closed one is the sum of the two.
    """

    bonus_eyes_open: float = 10.0
    """The bonus when the main subject appears to have their eyes open.

    With only a penalty, "the eyes are open" and "the eyes could not be
    measured" come out identical in the score. A frame that could not be
    measured because it is a profile was treated the same as a frame with
    the eyes open to camera, which meant the most important signal in
    portrait culling was only half used.

    **It is not given to frames that could not be measured (-1).** The
    principle that what is unknown is treated as neither good nor bad still
    stands - do otherwise and a distant profile receives the same bonus as
    a frontal portrait.

    It does not take the face size weighting. It is left symmetric with
    penalty_eyes_closed so that "open, +; closed, -" reads at a glance.
    """

    eyes_closed_below: float = 0.25
    """An eye aspect ratio (EAR) below this value is taken as closed.

    Measured on 107 real photos the user labelled (28 closed / 79 open):

        thresh   caught  false pen.   accuracy
        0.20       8/28        0/79        81%
        0.22      14/28        2/79        85%
        0.25      17/28        7/79        83%
        0.28      24/28       16/79        81%
        0.30      25/28       19/79        79%   <- default
        0.32      25/28       26/79        73%
        0.35      26/28       40/79        61%

    **There is no reason to go above 0.30.** 0.32 catches the same 25 as
    0.30 while cutting 7 more open eyes. It is because the EAR of the
    closed labels is mostly clustered at 0.28 and below (25 of 28), while
    open eyes start from 0.20, so the two distributions overlap above that.

    If you dislike a false penalty more than a miss, drop it to 0.22 - only
    2 open eyes get cut and the accuracy is the highest in this sample.

    **Default 0.25 (moved 0.30 -> 0.22 -> 0.25).** Evidence below: second
    labelling round, 400 frames, 2026-07-26.
    From a 20,000-frame corpus, 400 frames stratified to lie near the
    threshold only (0.22~0.38) were labelled on a "worth keeping alive"
    basis - in this band the median EAR of the keeps is 0.316 vs 0.273 for
    the penalty-worthy, the distributions almost entirely overlap, and
    **no threshold holds up at all** (at 0.30, 38.6% of the frames worth
    keeping are falsely penalised; even down at 0.24 it is 9.4% false
    penalty for 18.6% caught). It is because one eye closed, blur and
    darkness are mixed in independently of EAR, and the sharpness and
    exposure axes catch those separately. So the penalty avoids the
    ambiguous band entirely and works only on the **definitely-closed tail
    (<0.22, ~5% of the corpus)** - 0 false penalties on this labelling, and
    on the first 107-frame labelling it was 2/79, the minimum there too.

    **Final 0.25 (the user's decision).** Measured on 152 clean labels
    (72 closed / 80 open):

        thresh    false pen.   recall   accuracy
        0.22            8.0%    57.5%      76.2%
        0.25           10.3%    67.1%      79.4%   <- default

    We use 0.25, on the judgement that 0.22 is excessively conservative and
    misses too many closed eyes - the trade gives up another 2.3%p of false
    penalty to gain 9.6%p of recall, and the accuracy is higher this way
    too.

    A frame where the eyes could not be measured (-1) is not penalised at
    any value.
    """

    bonus_face_size: float = 0.0
    """Points added the larger the face is (proportional to area relative
    to the frame).

    Used when you want to favour a main subject caught large over a distant
    passer-by.
    """

    face_bonus_full_area: float = 0.03
    """The face area (relative to the frame) at which the face bonus starts
    being received **in full**.

    A face smaller than this has its bonus reduced in proportion to size.
    Without it, a face caught in the audience receives exactly the same
    bonus as the main subject's face - the detector finds faces only a few
    dozen pixels across as well.

    It applies to both bonus_face and bonus_eye. Leave it off the eye bonus
    and small faces just detour through that one.

    The default 3% is roughly 880x880 pixels at 6240x4168 (26MP). It is the
    value the user chose, the line at which a waist-up portrait is credited
    in full.

    **Shooting from a distance with a telephoto, it has to go far lower.**
    Measured on 2845 A6700 frames (stage shooting at 300mm), the main
    subject's face had a median of 0.34% and a maximum of 2.99%. In a batch
    like that, somewhere near 0.3% is right.

    The range is FACE_BONUS_AREA_RANGE (0.1%~50%).
    """

    penalty_highlight_clip: float = 1.0
    """Maximum penalty when highlights blow out past the threshold."""

    penalty_shadow_clip: float = 2.5
    """Maximum penalty when the shadows are crushed.

    Kept small. Stage and night shooting deliberately has large black
    areas, so set large it cuts perfectly good frames by the pile. It only
    applies past max_clipped_shadows (0.5 by default).
    """

    penalty_extreme_luma: float = 15.0
    """Penalty when the frame is nearly black or nearly white (lens cap,
    misfired shutter)."""

    max_clipped_shadows: float = 0.5
    """Shadows crushed past this ratio become penalty material."""

    keep_per_group: int = 1
    """How many top frames per group to raise to keep.

    Left at 0 this turns the scene guarantee off. Scoring then goes by the
    absolute score (keep_above) or the target ratio alone, so scenes appear
    out of which no keep comes at all.
    """

    min_keep_score: float = 0.0
    """The minimum score to be raised to keep. The only condition that
    disables the scene guarantee.

    At 0, at least one frame always comes out of every scene (the default
    behaviour). Above 0, when a whole scene falls short of this score
    nothing is taken from that scene.

    Forcing one frame out of even a scene where everything is shaken drops
    the trust in the keep folder. But raising this value can make a whole
    scene disappear, so raise it and you must check review to match.
    """

    reject_below: float = 15.0
    """Below this score it is a reject regardless of group rank (an
    absolute threshold)."""

    reject_percentile: float = 15.0
    """What bottom percentage of the batch to treat as reject candidates
    (a relative threshold).

    Lighting conditions differ from batch to batch, so an absolute
    threshold alone is unstable. The absolute value and the percentile have
    to be read together.
    """

    keep_above: float = 65.0
    """The absolute score that raises a frame to keep regardless of group
    rank.

    Since the sharpness term is 0~50 (scoring.SHARPNESS_SCALE), only frames
    that took a substantial bonus clear this value. It is the value the
    user tuned against their own real shoots.

    If target_keep_ratio is on, this value is ignored.
    """

    target_keep_ratio: float | None = None
    """The target keep ratio (0~1). None uses keep_above as it is.

    An absolute score means something different in every batch. Change the
    lighting and the lens and the whole score distribution shifts, so a
    value that yielded 10% on one shoot yields 30% on another. Give it a
    target ratio and the threshold is derived back out of the batch's own
    score distribution, so the result ratio holds even when the shoot
    changes.

    The achievable lower bound is 'number of scenes / total frames',
    because at least one frame is always left per scene. A target lower
    than that just gives the lower bound as the result.
    """

    reject_below_group_best: float = 10.0
    """Reject if it is this far below the best of the same group.

    This is the judgement a person actually makes on a burst. Even if it
    sits mid-pack on the global score, if there is a better frame of the
    same moment then it is a duplicate with no reason to look at it.

    Measured (2845 frames, 226 groups), the gap against the group best had
    a median of 14.7 points and a p75 of 24 points - **on the old scale,
    where sharpness used all of 0~100**. The sharpness term is now halved
    (SHARPNESS_SCALE) so the gaps are roughly halved too, which makes the
    value corresponding to that 20 points 10 points.

    The group's number one is fixed as keep ahead of this rule, so a scene
    never disappears wholesale.
    """

    max_clipped_highlights: float = 0.25
    """Highlights blown out past this ratio take a penalty."""


@dataclass
class Config:
    analyze: AnalyzeConfig = field(default_factory=AnalyzeConfig)
    group: GroupConfig = field(default_factory=GroupConfig)
    score: ScoreConfig = field(default_factory=ScoreConfig)
    workers: int | None = None
    """None means cpu_count - 1."""

    recursive: bool = True

    @classmethod
    def load(cls, path: Path | None) -> "Config":
        """Read the settings from YAML.

        If the path is missing or the file is damaged, it falls through to
        the defaults. One settings file is never allowed to become the
        reason the program will not run.
        """
        if path is None or not Path(path).exists():
            return cls()

        try:
            raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            log.warning("설정 파일을 읽지 못했습니다 (%s): %s", path, exc)
            return cls()

        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, data: Any) -> "Config":
        """Unknown keys are filtered out.

        Even if a hand-edited file has a section come in as something other
        than a dict, only that section falls back to defaults and the rest
        survives.
        """
        if not isinstance(data, dict):
            return cls()

        sections = {"analyze": AnalyzeConfig, "group": GroupConfig, "score": ScoreConfig}
        kwargs: dict[str, Any] = {}
        for name, section_cls in sections.items():
            values = data.get(name)
            if not isinstance(values, dict):
                kwargs[name] = section_cls()
                continue
            valid = {f.name for f in fields(section_cls)}
            try:
                kwargs[name] = section_cls(
                    **{k: v for k, v in values.items() if k in valid}
                )
            except (TypeError, ValueError) as exc:
                log.warning("%s 설정을 기본값으로 되돌립니다: %s", name, exc)
                kwargs[name] = section_cls()

        if "workers" in data:
            try:
                kwargs["workers"] = int(data["workers"]) if data["workers"] else None
            except (TypeError, ValueError):
                pass
        if "recursive" in data:
            kwargs["recursive"] = bool(data["recursive"])
        return cls(**kwargs)

    def to_yaml(self) -> str:
        return yaml.safe_dump(asdict(self), sort_keys=False, allow_unicode=True)
