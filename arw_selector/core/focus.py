"""초점 판정.

기본 전략: 얼굴을 찾고, 얼굴 랜드마크의 눈 위치에서 ROI를 잘라 그 안의
선명도만 측정합니다. 배경이 아무리 선명해도 눈이 나가면 버리는 컷이고,
그 반대도 마찬가지이기 때문입니다. 얼굴이 없으면 격자 타일 중 가장 선명한
영역을 주 피사체로 간주합니다.

선명도는 콘트라스트로 정규화합니다. Laplacian variance는 콘트라스트의
제곱에 비례해서 커지므로, 정규화 없이 쓰면 저조도/저대비 장면이
초점과 무관하게 전부 낮은 점수를 받습니다. 이것이 오판의 가장 큰 원인입니다.

이 모듈의 함수는 전부 순수 함수입니다 — ndarray를 받아 dataclass를 돌려줍니다.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from pathlib import Path

import cv2
import numpy as np

from .raw_io import resize_long_edge
from .types import FocusResult, FocusSource

log = logging.getLogger(__name__)

ALGORITHM_VERSION = 3
"""측정 알고리즘 버전. 캐시 키에 들어갑니다.

설정값이 그대로여도 알고리즘이 바뀌면 예전 결과는 무횹니다. 이 값을 올리지
않으면 캐시가 옛날 점수를 그대로 돌려주고, 고친 내용이 반영되지 않은 채
"고쳤다"고 착각하게 됩니다. 실제로 발생한 사례입니다.

v2: 저분산 영역 게이트(MIN_VARIANCE) 추가, 타일 선정을 원시 그래디언트
    에너지 기준으로 변경 — 어두운 배경 노이즈가 피사체로 뽑히던 문제
v3: 주 피사체 얼굴을 면적×신뢰도로 고르도록 변경(큰 오검출이 ROI를
    가로채던 문제), 얼굴 ROI일 때 배경 선명도(background_sharpness)를
    따로 측정 — "초점이 얼굴이 아니라 배경에 맞은" 컷을 가리기 위함
"""

MODEL_PATH = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"

DETECT_LONG_EDGE = 1024
"""얼굴 검출용 축소 해상도. YuNet은 이 정도면 충분히 정확하고 훨씬 빠릅니다."""

MIN_ROI_PX = 24
"""이보다 작은 ROI는 선명도 측정이 무의미합니다."""

FACE_DISPLAY_MIN_SCORE = 0.80
"""화면에 얼굴 박스를 그릴 최소 확신도.

검출 임계값(0.6)보다 높게 잡습니다. 실촬영 표본을 눈으로 확인해 보면
0.60~0.75 구간에는 스피커 콘, 흰 장갑, 어두운 얼룩 같은 오검출이 몰려
있습니다. 화면에 그려 봐야 "왜 저게 얼굴이지"만 남습니다.

검출 자체를 이 값으로 끊지는 않습니다 — 같은 구간에 측면·모션블러·무대
조명 속 **진짜 얼굴**도 많아서, 끊으면 실측 16%를 잃습니다.
"""

FACE_MAIN_MIN_SCORE = 0.75
"""주 피사체(눈 ROI 기준)가 될 수 있는 최소 확신도.

이보다 낮은 검출만 있으면 어쩔 수 없이 그중에서 고르지만, 더 확신 있는
얼굴이 하나라도 있으면 그쪽을 씁니다. 초점 판정의 기준점이 되는 자리라
오검출이 앉으면 그 컷의 점수가 통째로 틀립니다.
"""

_EPS = 1e-6

MIN_VARIANCE = 25.0
"""판정 가능한 최소 분산 (표준편차 5에 해당).

정규화는 콘트라스트 불변성을 위한 것이지만, 신호가 없는 영역에서는 비율
자체가 의미를 잃습니다. 어두운 무대의 빈 배경(분산 0.9)은 원시 그래디언트가
노이즈 수준인데도 분산으로 나누는 순간 실제 피사체(분산 2000)보다 높은
점수를 받았습니다.

분모에 하한만 두는 것으로는 부족했습니다. 백색 노이즈는 Laplacian 응답 자체가
하한과 비슷한 크기라 여전히 통과합니다. 그래서 하한이 아니라 게이트로 쓴다 —
표준편차 5 미만은 센서 노이즈 영역이고 초점을 판정할 근거가 없으므로 0입니다.

실측 기준: 문제가 된 노이즈 타일은 분산 0.9, 실제 피사체 타일은 227~2000.
"""

FRAME_LONG_EDGE = 1024
"""frame_sharpness 측정용 고정 해상도.

전체 프레임 선명도는 반드시 항상 같은 스케일에서 재야 합니다. Laplacian
계열 지표는 해상도에 민감해서, 스케일이 다르면 값 자체가 비교 불가능해집니다.
"""

# 정규화된 지표를 0~100으로 눌러 담는 포화 상수 — 해당 값에서 50점이 됩니다.
# A6700 실배치(ILCE-6700, 망원/표준 혼합 85장)의 중앙값으로 잡았습니다.
# 촬영 스타일에 따라 분포가 달라지므로 config로 덮어쓸 수 있어야 합니다.
LAPLACIAN_K = 0.053
TENENGRAD_K = 1.63
FRAME_LAPLACIAN_K = 0.053
FRAME_TENENGRAD_K = 1.63


# ---------------------------------------------------------------- 얼굴 검출

_detector_local = threading.local()


@contextlib.contextmanager
def _quiet_opencv():
    """OpenCV의 C++ 경고를 이 블록 동안만 막습니다.

    YuNet을 만들 때마다 OpenCV 5.0이 이 줄을 찍습니다:

        setPreferableTarget Targets are not supported by the new graph engine

    실행 대상 힌트가 무시된다는 뜻일 뿐이고 검출은 정상입니다 — 실측으로
    확인했습니다(8장, 얼굴 개수 동일, 주 얼굴 상자 0화소 차이). 그런데 주
    피사체를 바꿀 때마다 검출기를 새로 만들어서 콘솔에 계속 쌓이고, 그러면
    정작 봐야 할 경고가 묻힙니다.

    범위를 이 블록으로 좁힙니다. 전역으로 낮추면 진짜 오류까지 사라집니다.
    """
    logging_api = getattr(getattr(cv2, "utils", None), "logging", None)
    if logging_api is None:  # 빌드에 따라 없을 수 있습니다
        yield
        return
    previous = logging_api.getLogLevel()
    logging_api.setLogLevel(logging_api.LOG_LEVEL_ERROR)
    try:
        yield
    finally:
        logging_api.setLogLevel(previous)


def _get_detector(size: tuple[int, int]) -> "cv2.FaceDetectorYN | None":
    """YuNet 검출기를 프로세스/스레드마다 하나씩 재사용합니다.

    ONNX 로딩은 장당 반복하기엔 비쌉니다. 4000장이면 그 비용이 전붑니다.
    """
    if not MODEL_PATH.exists():
        return None

    detector = getattr(_detector_local, "detector", None)
    if detector is None:
        try:
            with _quiet_opencv():
                detector = cv2.FaceDetectorYN.create(
                    str(MODEL_PATH), "", size, 0.6, 0.3, 5000
                )
        except cv2.error as exc:
            log.warning("YuNet 초기화 실패, 타일 기반으로 폴백: %s", exc)
            _detector_local.detector = False
            return None
        _detector_local.detector = detector
    elif detector is False:
        return None

    detector.setInputSize(size)
    return detector


def detect_faces(image_bgr: np.ndarray) -> np.ndarray | None:
    """축소된 BGR 이미지에서 얼굴을 검출합니다.

    반환: (N, 15) 배열 — x, y, w, h, 우안xy, 좌안xy, 코xy, 입 좌우xy, score.
    좌표는 입력 이미지 좌표곕니다.
    """
    h, w = image_bgr.shape[:2]
    detector = _get_detector((w, h))
    if detector is None:
        return None
    try:
        _, faces = detector.detect(image_bgr)
    except cv2.error as exc:
        log.debug("얼굴 검출 실패: %s", exc)
        return None
    return faces if faces is not None and len(faces) else None


# ---------------------------------------------------------------- 선명도 측정


def measure_patch(gray_patch: np.ndarray) -> tuple[float, float]:
    """그레이스케일 패치의 (정규화 Laplacian, 정규화 Tenengrad)를 반환합니다.

    둘 다 패치의 분산으로 나눠 콘트라스트 불변으로 만듭니다. Tenengrad는
    Laplacian보다 방향성 모션블러에 민감해서 손떨림 컷을 더 잘 잡아냅니다.
    """
    if gray_patch.size == 0:
        return 0.0, 0.0

    patch = gray_patch.astype(np.float32)
    variance = float(patch.var())

    # 신호가 없는 영역은 판정 불가로 처리한다 (MIN_VARIANCE 주석 참고).
    # 여기서 걸러내지 않으면 어두운 배경 노이즈가 피사체를 이깁니다.
    if variance < MIN_VARIANCE:
        return 0.0, 0.0

    laplacian = float(cv2.Laplacian(patch, cv2.CV_32F).var()) / variance

    gx = cv2.Sobel(patch, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(patch, cv2.CV_32F, 0, 1, ksize=3)
    tenengrad = float(np.mean(gx * gx + gy * gy)) / variance

    return laplacian, tenengrad


def gradient_energy(gray_patch: np.ndarray) -> float:
    """정규화하지 않은 그래디언트 에너지.

    한 이미지 안에서 영역끼리 비교할 때 씁니다. 같은 사진의 타일들은 노출이
    동일하므로 정규화가 필요 없고, 오히려 정규화하면 어두운 노이즈 영역이
    실제 피사체를 이깁니다.
    """
    if gray_patch.size == 0:
        return 0.0
    patch = gray_patch.astype(np.float32)
    gx = cv2.Sobel(patch, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(patch, cv2.CV_32F, 0, 1, ksize=3)
    return float(np.mean(gx * gx + gy * gy))


def _saturate(value: float, k: float) -> float:
    """0~inf 값을 0~100으로 단조 매핑합니다. k에서 50점."""
    return 100.0 * value / (value + k) if value > 0 else 0.0


# ---------------------------------------------------------------- ROI 선정


def _eye_roi(face: np.ndarray, scale: float, shape: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """랜드마크의 양 눈을 감싸는 ROI를 원본 좌표계로 역투영합니다."""
    right_eye = np.array([face[4], face[5]], dtype=np.float32)
    left_eye = np.array([face[6], face[7]], dtype=np.float32)
    eye_distance = float(np.linalg.norm(left_eye - right_eye))
    if eye_distance < 2.0:
        return None

    center = (right_eye + left_eye) / 2.0 / scale
    half_w = (eye_distance * 0.9) / scale
    half_h = (eye_distance * 0.45) / scale
    return _clip_box(center[0] - half_w, center[1] - half_h, half_w * 2, half_h * 2, shape)


#: 주 피사체 후보로 선명도까지 재 볼 얼굴 수. 단체 사진에서 수십 명을
#: 전부 원본 해상도로 재면 느려지므로, 면적 상위 몇 개만 봅니다.
MAX_FOCUS_CANDIDATES = 8

#: 최고 선명도의 이 비율 이상이면 "초점이 맞은 얼굴"로 봅니다.
IN_FOCUS_RATIO = 0.85


FACE_MAIN_MIN_CONTRAST = 8.0
"""주 피사체가 되려면 이만큼은 명암이 있어야 합니다(8비트 표준편차).

진짜 얼굴은 눈·코·입 그림자 때문에 반드시 무늬가 있습니다. 관객석의 어두운
얼룩은 평탄합니다 — 실측에서 진짜 얼굴은 34~72, 오검출 얼룩은 5.7~6.6
이었습니다.

**얼룩을 걸러내는 최소한으로만 잡습니다.** 처음에 12.0으로 뒀더니 어둡게
찍힌 진짜 얼굴까지 후보에서 빠질 여지가 컸습니다. 8.0이면 얼룩(5.7~6.6)은
그대로 걸리면서 여유가 생깁니다.
"""


def _patch_contrast(face: np.ndarray, gray_full: np.ndarray,
                    scale: float, shape: tuple[int, int]) -> float:
    """얼굴 상자 안의 명암 정도. 못 재면 0."""
    box = _clip_box(face[0] / scale, face[1] / scale,
                    face[2] / scale, face[3] / scale, shape)
    x, y, w, h = box
    if w <= 0 or h <= 0:
        return 0.0
    patch = gray_full[y:y + h, x:x + w]
    return float(patch.std()) if patch.size else 0.0


def _pick_main_face(
    faces: np.ndarray,
    gray_full: np.ndarray,
    scale: float,
    shape: tuple[int, int],
    laplacian_k: float,
    tenengrad_k: float,
) -> int:
    """여러 얼굴 중 주 피사체의 인덱스를 고릅니다.

    예전에는 면적×신뢰도만 봤습니다. 그러면 앞쪽에 크게 잡힌 행인이 뒤에서
    초점이 맞은 인물을 이기고, ROI가 흐린 얼굴로 가서 잘 찍힌 컷이 낮은
    점수를 받습니다.

    사진에서 "주 피사체"는 촬영자가 초점을 맞춘 대상입니다. 그래서 먼저
    얼굴마다 실제 선명도를 재고, 초점이 맞은 축에 드는 얼굴만 남긴 뒤 그
    안에서 면적×신뢰도로 고릅니다. 모두 같은 거리인 단체 사진에서는 전부
    초점이 맞은 축이라 예전과 같은 결과가 나옵니다 — 달라지는 것은 피사계
    심도가 얕아 누구는 맞고 누구는 나간 경우뿐입니다.
    """
    area = faces[:, 2] * faces[:, 3]
    confidence = np.clip(faces[:, 14], 0.0, None)
    size_rank = area * confidence

    if len(faces) == 1:
        return 0

    # 확신이 낮은 검출은 주 피사체 후보에서 뺍니다. 스피커 콘이나 어두운
    # 얼룩이 이 자리에 앉으면 그 컷의 초점 판정이 통째로 틀립니다. 다만
    # 낮은 것밖에 없으면 어쩔 수 없이 그중에서 고릅니다 — 아무도 못 고르는
    # 것보다는 낫습니다.
    trusted = [i for i in range(len(faces))
               if confidence[i] >= FACE_MAIN_MIN_SCORE]
    pool = trusted if trusted else list(range(len(faces)))

    # 무늬가 거의 없는 조각도 뺍니다. 진짜 얼굴은 눈·코·입 그림자 때문에
    # 반드시 명암이 있습니다. 실측(DSC03360): 진짜 얼굴 6개의 표준편차가
    # 43~72인데 관객석 어두운 얼룩 둘은 5.7과 6.6이었고, 그중 하나가
    # 신뢰도 0.80으로 임계값을 통과해 주 피사체로 뽑혔습니다.
    detailed = [i for i in pool
                if _patch_contrast(faces[i], gray_full, scale, shape)
                >= FACE_MAIN_MIN_CONTRAST]
    if detailed:
        pool = detailed

    if len(pool) == 1:
        return int(pool[0])

    # 면적 상위 후보만 원본 해상도에서 재 봅니다
    ordered = sorted(pool, key=lambda i: size_rank[i], reverse=True)
    candidates = ordered[:MAX_FOCUS_CANDIDATES]

    sharpness: dict[int, float] = {}
    for index in candidates:
        box = _clip_box(
            faces[index][0] / scale, faces[index][1] / scale,
            faces[index][2] / scale, faces[index][3] / scale, shape,
        )
        if min(box[2], box[3]) < MIN_ROI_PX:
            continue
        x, y, w, h = box
        # **정규화하지 않은** 그래디언트 에너지로 비교합니다.
        #
        # 예전에는 _measure_sharpness(패치 분산으로 나눈 값)를 썼습니다.
        # 어두운 영역은 분산이 작아서 노이즈만으로도 값이 치솟습니다. 실측
        # (DSC04240): 무대 위 주인공 얼굴이 밝기 140·그래디언트 4886인데
        # 정규화 선명도는 51, 뒤쪽 어두운 관객 얼굴은 밝기 19·그래디언트
        # 233인데 정규화 선명도가 83이었습니다. 그래서 매번 관객이 주
        # 피사체로 뽑혔습니다(사용자 리포트 3장 전부 같은 양상).
        #
        # 같은 사진 안의 얼굴끼리는 노출이 같으므로 정규화가 필요 없습니다.
        # 타일 선정은 이미 같은 이유로 gradient_energy를 씁니다.
        #
        # **제곱근을 씌웁니다.** 그래디언트 에너지는 대비의 제곱에 비례해서,
        # 날것으로 쓰면 밝고 복잡한 얼굴이 압도적으로 유리합니다. 그러면
        # 조명을 세게 받은 사람만 계속 주 피사체가 됩니다. 제곱근을 씌우면
        # 대비에 비례하는 정도로 눌러져, 아래 IN_FOCUS_RATIO 문턱을 여러
        # 얼굴이 함께 통과하고 최종 판단이 면적·신뢰도로 넘어갑니다.
        energy = gradient_energy(gray_full[y:y + h, x:x + w])
        sharpness[int(index)] = float(np.sqrt(max(0.0, energy)))

    if not sharpness:
        return int(max(pool, key=lambda i: size_rank[i]))

    best = max(sharpness.values())
    if best <= 0:
        return int(max(pool, key=lambda i: size_rank[i]))

    # 초점이 맞은 축에 드는 얼굴들 — 그 안에서는 크고 확실한 쪽이 주 피사체
    in_focus = [i for i, value in sharpness.items() if value >= best * IN_FOCUS_RATIO]
    return max(in_focus, key=lambda i: size_rank[i])


def _clip_box(x: float, y: float, w: float, h: float, shape: tuple[int, int]) -> tuple[int, int, int, int]:
    """박스를 이미지 경계 안으로 자릅니다."""
    height, width = shape
    x0 = max(0, int(round(x)))
    y0 = max(0, int(round(y)))
    x1 = min(width, int(round(x + w)))
    y1 = min(height, int(round(y + h)))
    return x0, y0, max(0, x1 - x0), max(0, y1 - y0)


def _boxes_overlap(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> bool:
    """두 (x, y, w, h) 박스가 겹치는지. 배경 타일에서 얼굴 영역을 빼는 데 씁니다."""
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    return not (ax0 + aw <= bx0 or bx0 + bw <= ax0 or ay0 + ah <= by0 or by0 + bh <= ay0)


def _best_tile(gray_small: np.ndarray, scale: float, shape: tuple[int, int],
               grid: tuple[int, int] = (6, 4),
               exclude: tuple[float, float, float, float] | None = None
               ) -> tuple[int, int, int, int] | None:
    """격자 타일 중 실제 디테일이 가장 많은 곳을 피사체(또는 배경)로 봅니다.

    정규화된 값이 아니라 원시 그래디언트 에너지로 고릅니다. 같은 사진 안의
    타일들은 노출이 같아서 정규화가 불필요하고, 정규화하면 어두운 배경의
    노이즈가 피사체를 이겨 버린다 (실측: 노이즈 타일 ten_raw 13 vs
    피사체 7808인데 정규화 후에는 14.4 vs 3.9로 역전).

    exclude(축소본 좌표계의 얼굴 박스)를 주면 그와 겹치는 타일은 건너뜁니다.
    얼굴 밖에서 가장 선명한 배경 영역을 찾을 때 씁니다. 이 모드에서는 쓸
    타일이 없거나 격자를 만들 수 없으면 None을 돌려줍니다.
    """
    cols, rows = grid
    h, w = gray_small.shape[:2]
    tile_h, tile_w = h // rows, w // cols
    if tile_h < 8 or tile_w < 8:
        return None if exclude is not None else (0, 0, shape[1], shape[0])

    best_value, best_rc = -1.0, None
    for r in range(rows):
        for c in range(cols):
            if exclude is not None and _boxes_overlap(
                (c * tile_w, r * tile_h, tile_w, tile_h), exclude
            ):
                continue
            tile = gray_small[r * tile_h:(r + 1) * tile_h, c * tile_w:(c + 1) * tile_w]
            energy = gradient_energy(tile)
            if energy > best_value:
                best_value, best_rc = energy, (r, c)

    if best_rc is None:
        return None

    r, c = best_rc
    # 인접 타일까지 살짝 넓게 잡아 피사체가 타일 경계에 걸린 경우를 흡수합니다
    x = (c * tile_w - tile_w * 0.25) / scale
    y = (r * tile_h - tile_h * 0.25) / scale
    return _clip_box(x, y, (tile_w * 1.5) / scale, (tile_h * 1.5) / scale, shape)


def _measure_sharpness(
    gray_full: np.ndarray, box: tuple[int, int, int, int],
    laplacian_k: float, tenengrad_k: float,
) -> float:
    """박스 영역의 최종 선명도(0~100)를 ROI와 같은 방식으로 잽니다."""
    x, y, w, h = box
    lap_raw, ten_raw = measure_patch(gray_full[y:y + h, x:x + w])
    return 0.4 * _saturate(lap_raw, laplacian_k) + 0.6 * _saturate(ten_raw, tenengrad_k)


MIN_EYE_PX = 12.0
"""눈 가로 폭이 이보다 작으면 개폐를 재지 않습니다.

멀리 있는 얼굴은 눈이 몇 화소뿐이라 사람이 봐도 못 맞힙니다. 억지로 값을
내면 그 값으로 감점하게 되므로 아예 '못 쟀음'으로 둡니다.
"""


def _measure_eye_opening(image_bgr: np.ndarray, box) -> float:
    """주 피사체의 눈 종횡비(EAR). 못 재면 -1.

    **양쪽 중 더 떠 있는 쪽**을 씁니다. 옆얼굴에서 먼 쪽 눈은 거의 안 보여
    항상 '감음'으로 나오는데, 그걸로 감점하면 측면 컷이 전부 떨어집니다.

    비용은 얼굴당 1.3ms입니다. 주 피사체 하나만 재므로 4000장에 5초 남짓이라
    분석 시간에 영향이 없습니다.
    """
    try:
        from . import face_mesh

        if not face_mesh.available():
            return -1.0
        points = face_mesh.landmarks(
            image_bgr, (float(box[0]), float(box[1]),
                        float(box[2]), float(box[3])))
        if points is None:
            return -1.0

        best = -1.0
        for ring, ear_points in (
            (face_mesh.LEFT_EYE, face_mesh.LEFT_EAR_POINTS),
            (face_mesh.RIGHT_EYE, face_mesh.RIGHT_EAR_POINTS),
        ):
            xs = [points[i][0] for i in ring]
            if float(max(xs) - min(xs)) < MIN_EYE_PX:
                continue
            p = [points[i][:2] for i in ear_points]
            vertical = (float(np.linalg.norm(p[1] - p[5]))
                        + float(np.linalg.norm(p[2] - p[4])))
            horizontal = float(np.linalg.norm(p[0] - p[3]))
            best = max(best, vertical / (2.0 * horizontal + 1e-6))
        return best
    except Exception:  # noqa: BLE001 - 눈을 못 재도 분석 전체가 멈추면 안 됩니다
        log.debug("눈 개폐 측정 실패", exc_info=True)
        return -1.0


# ---------------------------------------------------------------- 진입점


def analyze_focus(
    image_bgr: np.ndarray,
    detect_long_edge: int = DETECT_LONG_EDGE,
    laplacian_k: float = LAPLACIAN_K,
    tenengrad_k: float = TENENGRAD_K,
    force_main_face: int | None = None,
) -> FocusResult:
    """프리뷰 이미지 한 장의 초점 상태를 측정합니다.

    검출은 축소본에서, ROI 선명도 측정은 원본 해상도에서 합니다.
    선명도는 원본 픽셀에서 재야 의미가 있기 때문입니다.

    주의: detect_long_edge를 바꾸면 얼굴 검출 결과가 달라져 ROI가 바뀔 수
    있습니다. frame_sharpness는 이 값과 무관하게 FRAME_LONG_EDGE에서 재므로
    설정을 바꿔도 컷 간 비교가 유지됩니다.

    force_main_face를 주면 자동 선정을 건너뛰고 그 얼굴을 주 피사체로 삼습니다.
    화면에서 사용자가 다른 얼굴을 고른 경우입니다. ROI·선명도·배경 선명도가
    **전부 그 얼굴 기준으로 다시** 계산되어야 판정이 실제로 따라옵니다 —
    표시만 바꾸면 점수는 엉뚱한 얼굴 그대로입니다. 그래서 별도 경로를 두지
    않고 이 함수를 다시 태웁니다.
    """
    full_h, full_w = image_bgr.shape[:2]
    shape = (full_h, full_w)

    long_edge = max(full_h, full_w)
    scale = min(1.0, detect_long_edge / long_edge) if long_edge else 1.0
    if scale < 1.0:
        small = cv2.resize(
            image_bgr,
            (max(1, round(full_w * scale)), max(1, round(full_h * scale))),
            interpolation=cv2.INTER_AREA,
        )
    else:
        small = image_bgr

    gray_small = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray_full = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)

    # 노출 상태 — 축소본으로 재도 충분합니다
    mean_luma = float(gray_small.mean())
    total = gray_small.size
    clipped_highlights = float(np.count_nonzero(gray_small >= 250)) / total
    clipped_shadows = float(np.count_nonzero(gray_small <= 5)) / total

    faces = detect_faces(small)
    roi: tuple[int, int, int, int] | None = None
    source = FocusSource.FRAME
    face_count = 0
    face_confidence = 0.0
    face_area_ratio = 0.0
    background_sharpness = 0.0
    face_box_small: tuple[float, float, float, float] | None = None
    face_boxes: tuple[tuple[int, int, int, int], ...] = ()
    face_scores: tuple[float, ...] = ()
    main_face = -1

    if faces is not None:
        face_count = len(faces)
        if force_main_face is not None and 0 <= force_main_face < len(faces):
            index = int(force_main_face)
        else:
            index = _pick_main_face(faces, gray_full, scale, shape,
                                    laplacian_k, tenengrad_k)
        face = faces[index]
        main_face = index
        face_boxes = tuple(
            _clip_box(f[0] / scale, f[1] / scale, f[2] / scale, f[3] / scale, shape)
            for f in faces
        )
        face_scores = tuple(float(f[14]) for f in faces)
        face_confidence = float(face[14])
        face_area_ratio = float(face[2] * face[3]) / float(small.shape[0] * small.shape[1])
        face_box_small = (float(face[0]), float(face[1]), float(face[2]), float(face[3]))

        candidate = _eye_roi(face, scale, shape)
        if candidate and min(candidate[2], candidate[3]) >= MIN_ROI_PX:
            roi, source = candidate, FocusSource.EYE
        else:
            candidate = _clip_box(
                face[0] / scale, face[1] / scale, face[2] / scale, face[3] / scale, shape
            )
            if min(candidate[2], candidate[3]) >= MIN_ROI_PX:
                roi, source = candidate, FocusSource.FACE

    if roi is None:
        candidate = _best_tile(gray_small, scale, shape)
        if candidate and min(candidate[2], candidate[3]) >= MIN_ROI_PX:
            roi, source = candidate, FocusSource.TILE

    if roi is None:
        roi, source = (0, 0, full_w, full_h), FocusSource.FRAME

    x, y, w, h = roi
    laplacian_raw, tenengrad_raw = measure_patch(gray_full[y:y + h, x:x + w])

    laplacian = _saturate(laplacian_raw, laplacian_k)
    tenengrad = _saturate(tenengrad_raw, tenengrad_k)
    # Tenengrad에 더 무게를 준다 — 모션블러 판별력이 좋습니다
    sharpness = 0.4 * laplacian + 0.6 * tenengrad

    # 얼굴을 ROI로 썼으면, 얼굴 밖에서 가장 선명한 배경도 재 둡니다. 얼굴은
    # 흐린데 배경이 쨍하면(초점이 뒤로 빠진 컷) 얼굴 우선 모드가 감점합니다.
    if source in (FocusSource.EYE, FocusSource.FACE) and face_box_small is not None:
        bg_box = _best_tile(gray_small, scale, shape, exclude=face_box_small)
        if bg_box and min(bg_box[2], bg_box[3]) >= MIN_ROI_PX:
            background_sharpness = _measure_sharpness(
                gray_full, bg_box, laplacian_k, tenengrad_k
            )

    # ROI와 무관한 기준선. 반드시 FRAME_LONG_EDGE 스케일에서 재야 합니다.
    # detect_long_edge가 마침 같으면 이미 만들어 둔 gray_small을 재사용합니다.
    if max(gray_small.shape[:2]) == FRAME_LONG_EDGE:
        frame_gray = gray_small
    else:
        frame_gray = resize_long_edge(gray_full, FRAME_LONG_EDGE)
    frame_lap_raw, frame_ten_raw = measure_patch(frame_gray)
    frame_sharpness = 0.4 * _saturate(frame_lap_raw, FRAME_LAPLACIAN_K) + 0.6 * _saturate(
        frame_ten_raw, FRAME_TENENGRAD_K
    )

    eyes_open = -1.0
    if 0 <= main_face < len(face_boxes):
        eyes_open = _measure_eye_opening(image_bgr, face_boxes[main_face])

    return FocusResult(
        eyes_open=eyes_open,
        sharpness=sharpness,
        laplacian=laplacian,
        tenengrad=tenengrad,
        source=source,
        frame_sharpness=frame_sharpness,
        roi=roi,
        face_count=face_count,
        face_confidence=face_confidence,
        face_area_ratio=face_area_ratio,
        background_sharpness=background_sharpness,
        faces=face_boxes,
        face_scores=face_scores,
        main_face=main_face,
        clipped_highlights=clipped_highlights,
        clipped_shadows=clipped_shadows,
        mean_luma=mean_luma,
        # roi·faces 가 어느 크기를 기준으로 한 좌표인지 함께 남깁니다.
        # 이게 없으면 화면 쪽에서 추측할 수밖에 없고, 실제로 그 추측이
        # 틀려서 박스가 엉뚱한 자리에 그려졌습니다.
        source_width=full_w,
        source_height=full_h,
    )
