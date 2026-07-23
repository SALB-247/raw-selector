"""얼굴 윤곽 랜드마크 (MediaPipe Face Mesh, ONNX).

YuNet은 점 5개(눈 2·코 1·입꼬리 2)만 줍니다. 그걸로는 마스크를 타원으로
어림잡을 수밖에 없고, 실제로 그렇게 만든 마스크는 이렇게 어긋났습니다
(같은 얼굴 실측):

  눈 마스크    화면의 4.09% — 눈 중심점 둘로 사각형을 그려 이마·볼까지 덮음
  피부 마스크  33.75% — '이목구비 제외'라고 해 놓고 실제로는 눈·눈썹·입이
               그대로 포함 (일반 얼굴 마스크와 수치가 같았습니다)

468점을 쓰면 각각 0.41% / 23.97%가 됩니다. 눈은 눈꺼풀 윤곽만, 피부는
이목구비를 실제로 빼고 남은 부분만 잡힙니다.

**분석(셀렉)에는 쓰지 않습니다.** 4000장을 훑는 경로에 장당 비용을 더할
이유가 없고, 거기서는 YuNet 5점으로 충분합니다. 보정 창의 마스크처럼
정밀도가 결과를 좌우하는 곳에서만 씁니다.
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
"""모델이 요구하는 입력 한 변."""

FACE_PAD = 0.25
"""얼굴 상자를 이만큼 넓혀서 잘라 넣습니다.

Face Mesh는 턱·이마까지 들어온 그림에서 잘 맞습니다. 상자에 딱 맞춰
자르면 윤곽점이 가장자리에 눌립니다.
"""

# ---------------------------------------------------------------- 윤곽 인덱스
#
# MediaPipe Face Mesh의 468점 표준 인덱스입니다. 숫자 자체에 의미는 없고
# 모델이 정한 순서입니다.

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
"""입술 안쪽 — 입을 벌린 컷에서 **치아**가 있는 자리입니다.

치아 화이트닝을 바깥 입술로 걸면 입술까지 채도가 빠져 창백해집니다.
입을 다문 컷에서는 이 윤곽이 실처럼 얇아져 효과가 거의 없는데, 보이지도
않는 치아를 만지지 않는 것이 맞는 동작입니다.
"""

LEFT_IRIS_RING = (159, 145, 33, 133)    # 위·아래·좌·우 — 홍채 크기 어림용
RIGHT_IRIS_RING = (386, 374, 362, 263)

# 눈 종횡비(EAR)용 6점 — 감김 판정에 씁니다
LEFT_EAR_POINTS = (33, 160, 158, 133, 153, 144)
RIGHT_EAR_POINTS = (362, 385, 387, 263, 373, 380)


_local = threading.local()


def available() -> bool:
    """모델 파일이 있는지. 없으면 부르는 쪽이 예전 방식으로 물러섭니다."""
    return MODEL_PATH.is_file()


def _net():
    """스레드마다 하나씩 재사용합니다. ONNX 로딩은 반복하기엔 비쌉니다."""
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
    """얼굴 하나의 468점을 **입력 이미지 좌표**로 돌려줍니다. 실패하면 None.

    face_box는 (x, y, w, h), image_bgr과 같은 좌표계입니다.
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

    # 형변환은 **자른 뒤에** 합니다. 보정 엔진은 6000×4000 float 배열을
    # 넘기는데, 그걸 통째로 uint8로 바꾸면 얼굴 하나 재는 데 수십 ms가 듭니다.
    crop = image_bgr[y0:y1, x0:x1]
    if crop.dtype != np.float32:
        crop = crop.astype(np.float32)
    crop = np.clip(crop, 0.0, 255.0)
    resized = cv2.resize(crop, (INPUT_SIZE, INPUT_SIZE))
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB) / 255.0
    # **NCHW입니다.** NHWC로 넣으면 유한값은 나오는데 좌표가 전부 범위 밖인
    # "돌긴 하는데 틀린" 상태가 되어 알아채기 어렵습니다.
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

    # 모델 좌표(0~192) → 잘라낸 조각 → 원본
    scale_x = (x1 - x0) / float(INPUT_SIZE)
    scale_y = (y1 - y0) / float(INPUT_SIZE)
    mapped = points.copy()
    mapped[:, 0] = points[:, 0] * scale_x + x0
    mapped[:, 1] = points[:, 1] * scale_y + y0
    return mapped


def polygon(points: np.ndarray, indices) -> np.ndarray:
    """윤곽 인덱스를 cv2.fillPoly가 받는 정수 좌표 배열로."""
    return np.array([[int(round(points[i][0])), int(round(points[i][1]))]
                     for i in indices], np.int32)


def eye_aspect_ratio(points: np.ndarray) -> float:
    """양 눈 종횡비의 평균. 작을수록 감은 쪽입니다.

    실측(무대 사진 32장, 손으로 라벨): 임계값 0.20에서 72% 정확. 완벽하진
    않지만 거짓감점이 2건뿐이라 감점을 작게 주면 쓸 만합니다.
    """
    def ratio(indices) -> float:
        p = [points[i][:2] for i in indices]
        vertical = (float(np.linalg.norm(p[1] - p[5]))
                    + float(np.linalg.norm(p[2] - p[4])))
        horizontal = float(np.linalg.norm(p[0] - p[3]))
        return vertical / (2.0 * horizontal + 1e-6)

    return (ratio(LEFT_EAR_POINTS) + ratio(RIGHT_EAR_POINTS)) / 2.0
