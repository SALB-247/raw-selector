"""The batch's subject - who the photographer was shooting - and every
frame's main face brought in line with it.

Each frame picks its main face on its own: the camera's tracking frame,
else size x confidence among the in-focus faces. In a burst that is
mostly right, but a mascot head, an MC, a bystander with a camera or a
poster face wins some frames - and because a burst is graded against its
best frame, one wrong winner rejects the frames around it. A shoot,
though, has a subject: the identity that is the main face far more often
than any other. From the SFace embeddings the analysis stored
(FocusResult.face_ids, face_id.py):

1. Faces are clustered greedily on cosine similarity (face_id.SAME_PERSON).
   An identity holding at least MAJOR_SHARE of the dominant identity's
   frames is *major* (a co-subject), and so is any identity whose main
   faces stand where a major identity's face stood in neighbouring frames
   of the same scene (MIN_LINK_FRAMES times, box IoU TRACK_IOU): SFace
   splits one person into a frontal identity and a profile one, and at
   30fps the face track runs straight through the split. The rest are
   *minor*.
2. A frame whose main face is minor while a major face is in the frame is
   re-scored with that face as its main face (main_face.reanalyze_with_
   main_face, the measurement the loupe's manual pick uses).
3. A frame whose main face is minor with no major face in it is "other":
   the face signals are withheld from its score (scoring.LINE_SUBJECT_
   OTHER) - the subject is not in it.
4. Before 3, a minor main face standing where a neighbouring frame's
   subject face stood (same scene, within TRACK_WINDOW frames, box IoU of
   TRACK_IOU) is the subject with her face covered or turned - SFace does
   not know a palm over the eyes - and is left as it is.

The pass is off unless one identity clearly leads (MIN_DOMINANT_FRAMES
frames and MIN_DOMINANT_SHARE of the frames with an embedded main face):
an event of many equal faces has no subject to enforce. A main face the
user pinned by hand (manual_main_face) is never touched. The states live
on the record for the session (ImageRecord.subject_state), like the
scene; the re-scored frames go back to the analysis cache.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

from . import face_id
from .config import Config
from .types import FocusResult, ImageRecord

log = logging.getLogger(__name__)

MAJOR_SHARE = 0.10
"""An identity with at least this share of the dominant identity's frames
is a subject too. The A1 shoot's subject splits into a frontal identity
(295 of the labelled frames) and a profile one (106, 36%); its largest
bystander holds 11 (4%). A two-idol shoot keeps both above the line."""

MIN_DOMINANT_FRAMES = 10
"""Fewer frames than this and no identity is a subject - a handful of
frames says nothing about a shoot."""

MIN_DOMINANT_SHARE = 0.30
"""The dominant identity must be the main face in this share of the
frames that have an embedded main face, or the pass stays off: with no
clear subject there is nothing to bring the frames in line with."""

MIN_LINK_FRAMES = 3
"""An identity becomes the subject's when this many of its main faces
stand where a subject face stood in a neighbouring frame of the same
scene. On the A1 shoot the subject's frontal identity held 1,125 frames
and her profile and turned-away identities 140, 82 and 58 - under the
10% share, but linked through the face track in every scene they share;
bystanders' faces never overlap hers three frames apart."""

TRACK_IOU = 0.3
"""Box overlap for "the same face as in the neighbouring frame" (rule 4)."""

TRACK_WINDOW = 3
"""How many frames either way, in capture order within the scene, rule 4
looks for a frame whose subject face overlaps this one's main face."""

SUBJECT = "subject"
OTHER = "other"

Rejudge = Callable[[ImageRecord, object, int], FocusResult | None]
ProgressCallback = Callable[[int, int], None]
CancelCheck = Callable[[], bool]


@dataclass
class SubjectSummary:
    """What the pass did, for the log and the tests."""

    active: bool = False
    identities: int = 0
    embedded_frames: int = 0
    dominant_frames: int = 0
    linked: int = 0
    switched: list[str] = field(default_factory=list)
    demoted: list[str] = field(default_factory=list)
    rescued: list[str] = field(default_factory=list)


def _embedding(focus, index: int) -> np.ndarray | None:
    ids = getattr(focus, "face_ids", ())
    if 0 <= index < len(ids) and ids[index]:
        vector = np.asarray(ids[index], dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        if norm > 0.0 and np.isfinite(norm):
            return vector / norm
    return None


def _assign(embedding: np.ndarray, centroids: list[np.ndarray], sizes: list[int]) -> int:
    """Greedy nearest-centroid clustering: join the closest identity when
    it is the same person, else start one. Centroids are running means,
    re-normalised."""
    if centroids:
        sims = np.asarray([float(np.dot(c, embedding)) for c in centroids])
        k = int(np.argmax(sims))
        if sims[k] >= face_id.SAME_PERSON:
            merged = centroids[k] * sizes[k] + embedding
            centroids[k] = merged / (float(np.linalg.norm(merged)) + 1e-9)
            sizes[k] += 1
            return k
    centroids.append(embedding.copy())
    sizes.append(1)
    return len(centroids) - 1


def cluster(records: list[ImageRecord]) -> tuple[dict[tuple[Path, int], int], Counter]:
    """The identity of every embedded face, keyed by (path, face index),
    and how many frames each identity is the main face of. Main faces are
    clustered first so the identities are seeded by them. The path, not
    the file name: a recursive analysis holds the same name twice (a Sony
    card starts a new folder and the numbering wraps), and keyed by name
    the later frame overwrote the earlier one's faces."""
    centroids: list[np.ndarray] = []
    sizes: list[int] = []
    cluster_of: dict[tuple[Path, int], int] = {}
    main_counts: Counter = Counter()
    for record in records:
        focus = record.focus
        if not record.ok or focus is None or not getattr(focus, "face_ids", ()):
            continue
        order = list(range(len(focus.faces)))
        if 0 <= focus.main_face < len(order):
            order.remove(focus.main_face)
            order.insert(0, focus.main_face)
        for index in order:
            embedding = _embedding(focus, index)
            if embedding is None:
                continue
            k = _assign(embedding, centroids, sizes)
            cluster_of[(record.path, index)] = k
            if index == focus.main_face:
                main_counts[k] += 1
    return cluster_of, main_counts


def _iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _capture_order(record: ImageRecord):
    capture = record.metadata.capture_time if record.metadata is not None else None
    return (capture or datetime.min, record.path.name)


def _main_box(record: ImageRecord):
    focus = record.focus
    if focus is None or not (0 <= focus.main_face < len(focus.faces)):
        return None
    return focus.faces[focus.main_face]


def _scenes(records: list[ImageRecord]) -> list[list[ImageRecord]]:
    by_group: dict[object, list[ImageRecord]] = {}
    for record in records:
        if record.ok and record.focus is not None:
            by_group.setdefault(record.group_id, []).append(record)
    scenes = list(by_group.values())
    for members in scenes:
        members.sort(key=_capture_order)
    return scenes


def _link_identities(records: list[ImageRecord], cluster_of: dict[tuple[Path, int], int],
                     major: set[int]) -> set[int]:
    """Identities whose main faces stand where a major identity's face
    stood in neighbouring frames (rule 1's face-track link), found until
    nothing more links."""
    scenes = _scenes(records)
    linked: set[int] = set()
    changed = True
    while changed:
        changed = False
        evidence: Counter = Counter()
        for members in scenes:
            keys = [cluster_of.get((r.path, r.focus.main_face)) for r in members]
            boxes = [_main_box(r) for r in members]
            for position, (k, box) in enumerate(zip(keys, boxes)):
                if k is None or k in major or k in linked or box is None:
                    continue
                lo, hi = max(0, position - TRACK_WINDOW), min(len(members), position + TRACK_WINDOW + 1)
                for other_key, other_box in zip(keys[lo:hi], boxes[lo:hi]):
                    if (other_key is not None and other_key != k and (other_key in major or other_key in linked)
                            and other_box is not None and _iou(box, other_box) >= TRACK_IOU):
                        evidence[k] += 1
                        break
        for k, n in evidence.items():
            if n >= MIN_LINK_FRAMES:
                linked.add(k)
                changed = True
    return linked


def _rescue(records: list[ImageRecord], pending: list[ImageRecord], summary: SubjectSummary) -> None:
    """Rule 4: a pending frame whose main face overlaps a subject face in a
    neighbouring frame of the same scene keeps its main face."""
    if not pending:
        return
    pending_set = {id(r) for r in pending}
    for members in _scenes(records):
        for position, record in enumerate(members):
            if id(record) not in pending_set:
                continue
            box = _main_box(record)
            if box is None:
                continue
            lo, hi = max(0, position - TRACK_WINDOW), min(len(members), position + TRACK_WINDOW + 1)
            for neighbour in members[lo:hi]:
                if neighbour is record or neighbour.subject_state != SUBJECT:
                    continue
                other = _main_box(neighbour)
                if other is not None and _iou(box, other) >= TRACK_IOU:
                    record.subject_state = SUBJECT
                    summary.rescued.append(record.path.name)
                    break


def assign_subjects(records: list[ImageRecord], config: Config,
                    rejudge: Rejudge | None = None,
                    cache_path: Path | None = None, *,
                    progress_cb: ProgressCallback | None = None,
                    should_cancel: CancelCheck | None = None) -> SubjectSummary:
    """Runs the four rules over a graded-or-not batch (grouping must have
    run for rule 4 to see scenes). Sets subject_state / subject_switched
    on every record, re-scores the switched frames and, given cache_path,
    writes them back to the analysis cache.

    The re-scores are the slow part - a preview decode and a detector pass
    each, 0.2~0.3s - so progress_cb(done, total) is called before the
    first and after each, and should_cancel is checked between them. A
    frame not reached keeps its own main face with no verdict (state
    None), like a frame the pass was off for.
    """
    summary = SubjectSummary()
    for record in records:
        record.subject_state = None
        record.subject_switched = False
    cluster_of, main_counts = cluster(records)
    summary.identities = len(set(cluster_of.values()))
    summary.embedded_frames = sum(main_counts.values())
    if not main_counts:
        return summary
    dominant, dominant_frames = main_counts.most_common(1)[0]
    summary.dominant_frames = dominant_frames
    if (dominant_frames < MIN_DOMINANT_FRAMES
            or dominant_frames < MIN_DOMINANT_SHARE * summary.embedded_frames):
        return summary
    summary.active = True
    major = {k for k, n in main_counts.items()
             if n >= max(MIN_DOMINANT_FRAMES, MAJOR_SHARE * dominant_frames)}
    linked = _link_identities(records, cluster_of, major)
    summary.linked = len(linked)
    major |= linked
    if rejudge is None:
        from .main_face import reanalyze_with_main_face as rejudge

    to_switch: list[tuple[ImageRecord, int]] = []
    pending: list[ImageRecord] = []
    for record in records:
        focus = record.focus
        if (not record.ok or focus is None or focus.main_face < 0
                or record.manual_main_face is not None):
            continue
        key = record.path
        if cluster_of.get((key, focus.main_face)) in major:
            record.subject_state = SUBJECT
            continue
        present = [(main_counts[cluster_of[(key, i)]], i) for i in range(len(focus.faces))
                   if i != focus.main_face and cluster_of.get((key, i)) in major]
        if present:
            to_switch.append((record, max(present)[1]))
            continue
        pending.append(record)

    switched: list[ImageRecord] = []
    total = len(to_switch)
    if total and progress_cb is not None:
        progress_cb(0, total)
    for done, (record, index) in enumerate(to_switch, 1):
        if should_cancel is not None and should_cancel():
            break   # the frames not reached keep their own main face, no verdict
        new_focus = rejudge(record, config.analyze, index)
        if new_focus is not None:
            record.focus = new_focus
            record.subject_switched = True
            record.subject_state = SUBJECT
            switched.append(record)
            summary.switched.append(record.path.name)
        # a frame that could not be re-scored is left as it was
        if progress_cb is not None:
            progress_cb(done, total)

    _rescue(records, pending, summary)
    for record in pending:
        if record.subject_state is None:
            record.subject_state = OTHER
            summary.demoted.append(record.path.name)

    if switched and cache_path is not None:
        # Written back so the next run reads the subject's face from the
        # cache instead of re-scoring the same frames again (measured: 3
        # re-scores on every cached run of a 592-frame folder until this
        # went through an *opened* cache - put_many on an unopened one
        # returns silently).
        try:
            from .cache import AnalysisCache

            with AnalysisCache(cache_path, config.analyze.cache_key()) as cache:
                cache.put_many(switched)
        except Exception:  # noqa: BLE001 - the cache is a convenience, the scores are done
            log.warning("주 피사체 재판정 결과를 캐시에 쓰지 못했습니다", exc_info=True)
    log.info("주 피사체: 식별 %d개, 주인공 %d/%d컷, 바꿈 %d, 구제 %d, 주인공 아님 %d",
             summary.identities, dominant_frames, summary.embedded_frames,
             len(summary.switched), len(summary.rescued), len(summary.demoted))
    return summary
