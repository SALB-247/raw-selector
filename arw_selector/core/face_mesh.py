"""Face contour landmarks (MediaPipe Face Mesh, ONNX).

YuNet gives only 5 points (2 eyes, 1 nose, 2 mouth corners). With those the
best you can do is approximate the mask with an ellipse, and a mask
actually built that way was off by this much (measured on the same face):

  eye mask     4.09% of the screen - a rectangle drawn from the two eye
               centres, covering the forehead and cheeks as well
  skin mask    33.75% - billed as 'facial features excluded', but the eyes,
               brows and mouth were included as they were (the figure was
               the same as the plain face mask)

With the 468 points they become 0.41% / 23.97% respectively. The eye picks
up only the eyelid contour, and the skin only what is left after the facial
features really are subtracted.

**It is not used for analysis (culling).** There is no reason to add a
per-frame cost to the path that sweeps 4000 frames, and YuNet's 5 points
are enough there. It is used only where precision decides the result, such
as the masks in the adjustment window.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).parent / "models" / "face_mesh_192x192.onnx"

INPUT_SIZE = 192
"""The side length of the input the model requires."""

FACE_PAD = 0.25
"""The face box is widened by this much before being cropped and fed in.

Face Mesh fits well on a picture that includes the chin and forehead. Crop
exactly to the box and the contour points get squashed against the edge.
"""

# -------------------------------------------------------- contour indices
#
# The standard 468-point indices of MediaPipe Face Mesh. The numbers
# themselves mean nothing; they are the order the model settled on.

FACE_OVAL = (
    10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365,
    379, 378, 400, 377, 152, 148, 176, 149, 150, 136, 172, 58, 132, 93,
    234, 127, 162, 21, 54, 103, 67, 109,
)

LEFT_EYE = (33, 246, 161, 160, 159, 158, 157, 173, 133,
            155, 154, 153, 145, 144, 163, 7)
RIGHT_EYE = (362, 398, 384, 385, 386, 387, 388, 466, 263,
             249, 390, 373, 374, 380, 381, 382)

LEFT_BROW = (70, 63, 105, 66, 107, 55, 65, 52, 53, 46)
RIGHT_BROW = (300, 293, 334, 296, 336, 285, 295, 282, 283, 276)

LIPS = (61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291,
        409, 270, 269, 267, 0, 37, 39, 40, 185)
INNER_LIPS = (78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308,
              415, 310, 311, 312, 13, 82, 81, 80, 191)
"""The inner lips - where the **teeth** are on a frame with the mouth open.

Run teeth whitening off the outer lips and the lips lose saturation too and
go pale. On a frame with the mouth closed this contour thins to a thread so
the effect is almost nil, and not touching teeth that are not even visible
is the correct behaviour.
"""

LEFT_IRIS_RING = (159, 145, 33, 133)    # top/bottom/left/right - for iris size
RIGHT_IRIS_RING = (386, 374, 362, 263)

# the 6 points for the eye aspect ratio (EAR) - used for the closed check
LEFT_EAR_POINTS = (33, 160, 158, 133, 153, 144)
RIGHT_EAR_POINTS = (362, 385, 387, 263, 373, 380)


_local = threading.local()


def available() -> bool:
    """Whether the model file is present. Callers fall back to the older
    path if not."""
    return MODEL_PATH.is_file()


def _net():
    """One net per thread, reused. Loading ONNX is too expensive to
    repeat."""
    net = getattr(_local, "net", None)
    if net is not None:
        return net or None
    if not MODEL_PATH.is_file():
        _local.net = False
        return None
    try:
        _local.net = cv2.dnn.readNetFromONNX(str(MODEL_PATH))
    except cv2.error as exc:
        log.warning("Face Mesh 모델을 읽지 못했습니다: %s", exc)
        _local.net = False
        return None
    return _local.net


def landmarks(image_bgr: np.ndarray,
              face_box: tuple[float, float, float, float]) -> np.ndarray | None:
    """Returns the 468 points of one face in **input image coordinates**.
    None on failure.

    face_box is (x, y, w, h), in the same coordinate system as image_bgr.
    """
    net = _net()
    if net is None or image_bgr is None or image_bgr.size == 0:
        return None

    height, width = image_bgr.shape[:2]
    x, y, w, h = (float(v) for v in face_box)
    if w <= 0 or h <= 0:
        return None

    x0 = max(0, int(x - w * FACE_PAD))
    y0 = max(0, int(y - h * FACE_PAD))
    x1 = min(width, int(x + w * (1.0 + FACE_PAD)))
    y1 = min(height, int(y + h * (1.0 + FACE_PAD)))
    if x1 - x0 < 16 or y1 - y0 < 16:
        return None

    # The type conversion happens **after** the crop. The adjustment engine
    # hands over a 6000x4000 float array, and converting the whole thing to
    # uint8 costs tens of ms just to measure one face.
    crop = image_bgr[y0:y1, x0:x1]
    if crop.dtype != np.float32:
        crop = crop.astype(np.float32)
    crop = np.clip(crop, 0.0, 255.0)
    resized = cv2.resize(crop, (INPUT_SIZE, INPUT_SIZE))
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB) / 255.0
    # **It is NCHW.** Feed it NHWC and you get finite values but every
    # coordinate out of range - a "it runs, but it is wrong" state that is
    # hard to notice.
    blob = np.transpose(rgb, (2, 0, 1))[None]

    try:
        net.setInput(blob)
        outputs = net.forward(net.getUnconnectedOutLayersNames())
    except cv2.error as exc:
        log.debug("Face Mesh 실행 실패: %s", exc)
        return None

    points = None
    for out in outputs:
        values = np.asarray(out).reshape(-1)
        if values.size >= 468 * 3:
            points = values[: 468 * 3].reshape(468, 3).astype(np.float64)
            break
    if points is None or not np.isfinite(points).all():
        return None

    # model coordinates (0~192) -> the cropped patch -> the original
    scale_x = (x1 - x0) / float(INPUT_SIZE)
    scale_y = (y1 - y0) / float(INPUT_SIZE)
    mapped = points.copy()
    mapped[:, 0] = points[:, 0] * scale_x + x0
    mapped[:, 1] = points[:, 1] * scale_y + y0
    return mapped


def polygon(points: np.ndarray, indices) -> np.ndarray:
    """Contour indices as the integer coordinate array cv2.fillPoly
    takes."""
    return np.array([[int(round(points[i][0])), int(round(points[i][1]))]
                     for i in indices], np.int32)


def eye_aspect_ratio(points: np.ndarray) -> float:
    """The mean aspect ratio of both eyes. The smaller, the more closed.

    Measured (32 stage photos, labelled by hand): 72% accurate at a
    threshold of 0.20. Not perfect, but with only 2 false penalties it is
    usable as long as the penalty is kept small.
    """
    def ratio(indices) -> float:
        p = [points[i][:2] for i in indices]
        vertical = (float(np.linalg.norm(p[1] - p[5]))
                    + float(np.linalg.norm(p[2] - p[4])))
        horizontal = float(np.linalg.norm(p[0] - p[3]))
        return vertical / (2.0 * horizontal + 1e-6)

    return (ratio(LEFT_EAR_POINTS) + ratio(RIGHT_EAR_POINTS)) / 2.0
