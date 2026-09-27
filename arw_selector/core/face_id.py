"""Face identity embeddings (SFace, OpenCV zoo) - so the batch can tell its
subject from a bystander, a mascot head or a poster face.

SFace (Zhong et al. 2021; the OpenCV zoo's Apache-2.0 file) turns the
112x112 crop aligned on YuNet's five landmarks into a 128-d embedding; by
the zoo's own threshold a cosine similarity of 0.363 or more is the same
person. The alignment reads the detection row the analysis already has,
so no second detector runs.

Measured on the A1 shoot of 2026-08-30 (734 faces on the 500 labelled
frames, crops cut from the half-size colour plane): the subject's frontal
faces cluster at 295 frames and her profiles at 106 (SFace is not
pose-invariant enough to join the two at 0.363), the largest bystander at
11; a face covered by a palm, a mascot head and a poster join nothing.
That is what subject.py needs - a *major* identity is the subject in
whatever pose, a minor one is not.

**Never raises.** It is called for every face in the middle of a batch;
without the model every embedding is None and the subject pass stays off.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).parent / "models" / "face_recognition_sface_2021dec.onnx"
EMBEDDING_SIZE = 128

SAME_PERSON = 0.363
"""Cosine similarity at or above which two faces are one person - OpenCV's
threshold for SFace. Loosening it to 0.30 on the A1 shoot merged the
subject's frontal and profile clusters, but pulled a bystander with a
camera in with them; subject.py handles the pose split instead."""

MIN_FACE_PX = 20.0
"""Faces narrower than this, in the image the crop is cut from, get no
embedding: the aligned 112px crop would be mostly interpolation."""

_local = threading.local()


def available() -> bool:
    """Whether the model is present and loads."""
    return _recognizer() is not None


@contextlib.contextmanager
def _quiet_opencv():
    """Block OpenCV's C++ warnings while the recogniser is built - the same
    "setPreferableTarget ... not supported by the new graph engine" line
    focus._quiet_opencv silences for the detector (the copy avoids a
    circular import; focus imports this module)."""
    logging_api = getattr(getattr(cv2, "utils", None), "logging", None)
    if logging_api is None:
        yield
        return
    previous = logging_api.getLogLevel()
    logging_api.setLogLevel(logging_api.LOG_LEVEL_ERROR)
    try:
        yield
    finally:
        logging_api.setLogLevel(previous)


def _recognizer():
    """One recogniser per thread, reused - the 39MB model is loaded once.
    The full-precision file: the zoo's int8 file is a quarter of the size
    but eight times slower on the CPU path here (33ms a face against 4),
    and OpenCV 5 does not load a half-precision conversion."""
    rec = getattr(_local, "rec", None)
    if rec is not None:
        return rec or None
    if not MODEL_PATH.is_file():
        _local.rec = False
        return None
    try:
        with _quiet_opencv():
            _local.rec = cv2.FaceRecognizerSF_create(str(MODEL_PATH), "")
    except cv2.error as exc:
        log.warning("얼굴 식별 모델을 읽지 못했습니다: %s", exc)
        _local.rec = False
        return None
    return _local.rec


def embeddings(image_bgr: np.ndarray, rows: np.ndarray, scale: float = 1.0
               ) -> list[np.ndarray | None]:
    """L2-normalised embeddings for YuNet rows (x, y, w, h, five landmarks,
    score), one per row; None where a face was skipped or failed.

    `scale` maps the row coordinates onto image_bgr - the rows come from
    the 1024px detection copy while the crops are cut from the larger
    colour plane, where a 0.4%-of-frame face is 190px across instead of
    45.
    """
    count = 0 if rows is None else len(rows)
    rec = _recognizer()
    if rec is None or image_bgr is None or count == 0:
        return [None] * count
    out: list[np.ndarray | None] = []
    for row in rows:
        try:
            box = np.asarray(row[:15], dtype=np.float32).copy()
            box[:14] *= scale
            if box[2] < MIN_FACE_PX or box[3] < MIN_FACE_PX:
                out.append(None)
                continue
            crop = rec.alignCrop(image_bgr, box)
            feature = np.asarray(rec.feature(crop), dtype=np.float32).reshape(-1)
            norm = float(np.linalg.norm(feature))
            if feature.size != EMBEDDING_SIZE or not np.isfinite(norm) or norm <= 0.0:
                out.append(None)
                continue
            out.append(feature / norm)
        except cv2.error as exc:  # a face that cannot be cut must not stop the frame
            log.debug("얼굴 식별 실패: %s", exc)
            out.append(None)
    return out


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity of two normalised embeddings."""
    return float(np.dot(a, b))
