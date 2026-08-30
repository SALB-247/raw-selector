"""Subject extraction (U²-Netp) - answering "what is this photo about" per pixel.

This serves a different purpose from the background mask (GrabCut). GrabCut
grows a colour split out of the seed boxes we hand it, so its edges are blunt
and it knows nothing about a subject it was not seeded on. This model was
trained to pick out "what stands out" as a whole, so it resolves down to
individual strands of hair.

**In exchange, it picks exactly one protagonist.** Measured on a stage frame
(5 people, LED backdrop), only the centre performer scored alpha 0.96 while
the rest came in at 0.000-0.018 - lowering the threshold does not recover
them. That is why masks._subject_alpha fills in any detected face the model
discarded, using the older method for those people only.

Model: U²-Net (u2netp, Apache-2.0). It is the same file OpenCV 5.x registers
in samples/dnn/models.yml, so cv2.dnn reads it (verified: sha1
0a99236f0d5c1916a99a8c401b23e5ef32038606, 4.4 MB, 83 ms for a 320² pass).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).parent / "models" / "u2netp.onnx"

INPUT_SIZE = 320
"""The input size the model was trained at, matching OpenCV's models.yml entry."""

_MEAN = (123.6, 116.2, 103.5)
_SCALE = 0.019
"""Pre-processing follows models.yml as well. The scale is a single-scalar
approximation of the ImageNet standard deviation (1/(255x0.229) = 0.0174);
we keep the value OpenCV registered rather than the per-channel one."""

_local = threading.local()


def available() -> bool:
    """Whether the model file is present. Callers fall back to the older path."""
    return MODEL_PATH.is_file()


def _net():
    """One net per thread, reused. Loading ONNX is too expensive to repeat."""
    net = getattr(_local, "net", None)
    if net is not None:
        return net or None
    if not MODEL_PATH.is_file():
        _local.net = False
        return None
    try:
        _local.net = cv2.dnn.readNetFromONNX(str(MODEL_PATH))
    except cv2.error as exc:
        log.warning("could not read the subject model: %s", exc)
        _local.net = False
        return None
    return _local.net


def subject_alpha(image_bgr: np.ndarray) -> np.ndarray | None:
    """Subject probability map (float32 HxW, 0-1), or None if unavailable.

    Returned at the same size as the input.
    """
    net = _net()
    if net is None or image_bgr is None or image_bgr.size == 0:
        return None
    height, width = image_bgr.shape[:2]
    if height < 2 or width < 2:
        return None

    source = image_bgr
    if source.dtype != np.uint8:
        source = np.clip(source, 0, 255).astype(np.uint8)

    try:
        blob = cv2.dnn.blobFromImage(
            source, _SCALE, (INPUT_SIZE, INPUT_SIZE), _MEAN,
            swapRB=True, crop=False)
        net.setInput(blob)
        # There are seven outputs (d0...d6). forward() hands back the first
        # (the finest one); the rest are training-side auxiliaries we ignore.
        raw = net.forward()
    except cv2.error as exc:
        log.debug("subject inference failed: %s", exc)
        return None

    mask = np.asarray(raw, dtype=np.float32).reshape(
        raw.shape[-2], raw.shape[-1])
    # The output is a sigmoid, but its range varies per frame, so it has to be
    # normalised for a threshold to mean the same thing everywhere (the original
    # U²-Net implementation post-processes the same way).
    low, high = float(mask.min()), float(mask.max())
    if high - low < 1e-6:
        return None
    mask = (mask - low) / (high - low)
    return cv2.resize(mask, (width, height),
                      interpolation=cv2.INTER_LINEAR).astype(np.float32)
