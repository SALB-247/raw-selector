"""새 기종의 색을 카메라 내장 JPEG에 맞춰 로컬에서 보정합니다.

배경
----
LibRaw은 기종별 색 정보를 내장하고 있는데, 갓 나온 바디는 그 표에 없습니다.
그러면 디모자이크 결과가 카메라가 만든 그림과 다르게 나옵니다 — 실측에서
EOS R6 Mark III는 블랙 페데스탈이 어긋나 노란-초록으로 떴습니다.

라이브러리가 갱신되기를 기다리는 대신, 카메라가 스스로 만든 JPEG을 정답지로
삼아 이 PC에서 직접 보정값을 구합니다. 같은 장면을 카메라와 우리가 각각
현상한 결과이므로, 둘의 채널 균형 차이가 곧 우리가 놓친 양입니다.

무엇을 구하는가
--------------
채널별 이득(gain) 세 개뿐입니다. 색을 "예쁘게" 만드는 것이 아니라 기준점을
맞추는 것이라, 자유도가 높은 행렬을 추정하면 장면에 과적합됩니다. 여러 장의
중앙값을 써서 한 장면의 색조에 끌려가지 않게 합니다.

어디에 저장되는가
----------------
이 PC의 data/calibration/ 뿐입니다. 측정값은 개체·펌웨어·촬영 조건을 타므로
남에게 옮길 만한 성질이 아닙니다.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Sequence

import cv2
import numpy as np

log = logging.getLogger(__name__)

#: 보정을 신뢰하려면 최소 이만큼은 재야 합니다. 한두 장은 그 장면의 색조를
#: 카메라 특성으로 착각합니다.
MIN_SAMPLES = 4

#: 이 이상은 정확도가 거의 안 오르고 시간만 듭니다.
MAX_SAMPLES = 12

#: 이득이 이 범위를 벗어나면 측정이 잘못된 것으로 봅니다. 정상적인 기종 차이는
#: 몇 %~수십 % 수준이고, 2배가 넘게 벌어지면 계산이나 표본이 잘못된 것입니다.
GAIN_LIMIT = (0.5, 2.0)

#: 채널비가 이보다 덜 어긋나 있으면 보정할 게 없습니다. 굳이 값을 만들어
#: 두면 다음 라이브러리 갱신 때 오히려 방해가 됩니다.
NEGLIGIBLE = 0.02

_UNSAFE = re.compile(r'[<>:"/\\|?*\s]+')


def camera_key(make: str | None, model: str | None) -> str:
    """저장 키. 제조사와 모델을 합쳐 파일명으로 쓸 수 있게 다듬습니다."""
    parts = [p.strip() for p in (make or "", model or "") if p and p.strip()]
    if not parts:
        return ""
    # 캐논은 모델에 제조사를 이미 넣어 둡니다 ("Canon EOS R6 Mark III")
    if len(parts) == 2 and parts[1].lower().startswith(parts[0].lower()):
        parts = [parts[1]]
    return _UNSAFE.sub("_", " ".join(parts)).strip("_")


@dataclass(frozen=True)
class CameraCalibration:
    """한 기종의 채널 이득."""

    camera: str
    gain: tuple[float, float, float]  # B, G, R 순서 (OpenCV 채널 순서)
    samples: int = 0
    created: str = ""
    app_version: str = ""
    note: str = ""
    #: 저장 파일 이름이 되는 키. `camera_key(make, model)`로 만든 값입니다.
    #:
    #: 이름(`camera`)에서 그때그때 다시 만들어 쓰면 안 됩니다. 읽을 때는
    #: 제조사+모델로 키를 만드는데 쓸 때는 모델만 썼던 적이 있고, 그 둘이
    #: 같아지는 것은 모델에 제조사가 이미 들어간 캐논뿐이었습니다. 소니·니콘·
    #: 파나소닉·후지는 저장은 되지만 다시 읽히지 않아, 보정이 조용히 적용되지
    #: 않고 폴더를 열 때마다 계산을 다시 권했습니다.
    key: str = ""

    def storage_key(self) -> str:
        """저장·삭제에 쓸 키. 예전 파일에는 key가 없어 이름으로 물러섭니다."""
        return self.key or _UNSAFE.sub("_", self.camera).strip("_")

    def is_neutral(self) -> bool:
        return all(abs(g - 1.0) < 1e-3 for g in self.gain)

    def to_dict(self) -> dict:
        return {
            "camera": self.camera,
            "gain": list(self.gain),
            "samples": self.samples,
            "created": self.created,
            "app_version": self.app_version,
            "note": self.note,
            "key": self.key,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CameraCalibration | None":
        try:
            gain = tuple(float(v) for v in data["gain"])
            if len(gain) != 3:
                return None
            low, high = GAIN_LIMIT
            if not all(low <= g <= high for g in gain):
                log.warning("보정값이 허용 범위를 벗어나 무시합니다: %s", gain)
                return None
            return cls(
                camera=str(data.get("camera", "")),
                gain=gain,  # type: ignore[arg-type]
                samples=int(data.get("samples", 0)),
                created=str(data.get("created", "")),
                app_version=str(data.get("app_version", "")),
                note=str(data.get("note", "")),
                key=str(data.get("key", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None


def calibration_dir() -> Path:
    """보정값 저장 폴더. 이 PC 안에만 둡니다."""
    from ..appinfo import data_dir

    return data_dir() / "calibration"


def _path_for(key: str) -> Path:
    return calibration_dir() / f"{key}.json"


def load(key: str) -> CameraCalibration | None:
    """저장된 보정값. 없거나 깨졌으면 None."""
    if not key:
        return None
    path = _path_for(key)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # JSONDecodeError만 잡으면 UTF-8이 아닌 파일에서 UnicodeDecodeError가
        # 새어나갑니다. 둘 다 ValueError 아래라 한 번에 받습니다. 이 함수를
        # 그대로 부르는 곳(gui/calibration_dialog.py)은 예외를 감싸지 않습니다.
        return None
    return CameraCalibration.from_dict(data)


def save(calibration: CameraCalibration) -> Path | None:
    """보정값을 저장하고 경로를 돌려줍니다.

    키는 `calibration.key`를 씁니다 — 읽을 때(`load`)와 같은 값이어야 합니다.
    """
    key = calibration.storage_key()
    if not key:
        return None
    path = _path_for(key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(calibration.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("보정값을 저장하지 못했습니다: %s", exc)
        return None
    return path


def remove(key: str) -> bool:
    """보정값을 지웁니다. 라이브러리가 갱신되면 필요 없어집니다."""
    try:
        _path_for(key).unlink()
        return True
    except OSError:
        return False


def stored_cameras() -> list[CameraCalibration]:
    """저장된 보정값 전체."""
    folder = calibration_dir()
    if not folder.is_dir():
        return []
    result = []
    for path in sorted(folder.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # 깨진 파일 하나가 목록 전체를 막으면 안 됩니다
        item = CameraCalibration.from_dict(data)
        if item is not None:
            result.append(item)
    return result


# ---------------------------------------------------------------- 감지


def looks_unsupported(path: Path) -> bool:
    """LibRaw이 이 기종을 온전히 모르는 것으로 보이는지.

    블랙 페데스탈을 놓쳤다는 것은 기종 표에 없다는 뜻이고, 그러면 색 정보도
    같이 없을 가능성이 큽니다. 이 신호가 뜬 기종만 보정을 권합니다 —
    잘 지원되는 기종까지 매번 물으면 성가시기만 합니다.
    """
    import rawpy

    from ..raw_io import _repair_black_level

    try:
        with rawpy.imread(str(path)) as raw:
            return _repair_black_level(raw) is not None
    except Exception:  # noqa: BLE001
        return False


@dataclass
class CalibrationNeed:
    """보정이 필요해 보이는 기종과 그 표본."""

    camera: str
    key: str
    samples: list[Path] = field(default_factory=list)


def find_uncalibrated(
    paths: Iterable[Path], limit: int = MAX_SAMPLES, force: bool = False
) -> CalibrationNeed | None:
    """폴더에서 보정이 필요한 기종을 찾습니다.

    자동 권유(force=False)는 조건이 셋입니다: 저장된 보정이 없고, LibRaw
    지원이 불완전해 보이고, 표본이 충분할 것. 하나라도 아니면 아무 말도
    하지 않습니다 — 잘 지원되는 기종까지 매번 물으면 성가시기만 합니다.

    force=True는 사용자가 직접 요청한 경우입니다. 라이브러리가 아는 기종도,
    이미 보정값이 있는 기종도 다시 잽니다. 라이브러리의 기본 색이 마음에
    들지 않아 이 PC의 측정값을 우선하고 싶을 때 쓰라고 열어 둔 길입니다.
    """
    from ..raw_io import read_metadata

    by_camera: dict[str, CalibrationNeed] = {}
    checked_support: dict[str, bool] = {}

    for path in paths:
        try:
            metadata = read_metadata(path)
        except Exception:  # noqa: BLE001
            continue
        key = camera_key(metadata.camera_make, metadata.camera_model)
        if not key:
            continue
        if not force:
            if load(key) is not None:
                continue
            if key not in checked_support:
                checked_support[key] = looks_unsupported(path)
            if not checked_support[key]:
                continue

        need = by_camera.setdefault(
            key, CalibrationNeed(camera=metadata.camera_model or key, key=key)
        )
        if len(need.samples) >= limit:
            continue
        # 내장 미리보기가 없는 파일은 정답지가 없어 잴 수 없습니다. 여기서
        # 걸러 두지 않으면 계산을 시작한 뒤에야 "표본 부족"으로 실패합니다.
        if has_embedded_preview(path):
            need.samples.append(path)

    for need in by_camera.values():
        if len(need.samples) >= MIN_SAMPLES:
            return need
    return None


# ---------------------------------------------------------------- 측정


NEUTRAL_SATURATION = 0.18
"""이 채도 미만이면 '원래 무채색'으로 봅니다."""

MIN_NEUTRAL_PIXELS = 200
"""무채색 화소가 이보다 적으면 못 믿습니다 — 전체 평균으로 물러섭니다."""


def _neutral_means(
    camera_bgr: np.ndarray, ours_bgr: np.ndarray
) -> tuple[np.ndarray, np.ndarray] | None:
    """무채색에 가까운 화소만 골라 양쪽 채널 평균을 냅니다.

    이미지 전체 평균으로 재면 피사체 색이 그대로 섞입니다. 붉은 옷이 화면을
    채우면 "이 바디는 붉다"고 배우고, 다음 장면에서는 반대로 배웁니다. 그래서
    장마다 값이 흔들리고, 그 흔들림이 곧 "장마다 색감이 다르다"가 됩니다.

    회색 벽·흰 셔츠·콘크리트처럼 **원래 색이 없어야 할 곳**만 보면, 남는
    차이는 피사체가 아니라 바디와 현상의 차이입니다.

    실측(R6M3 10장):
    - 장별 흔들림 0.0122 → 0.0071 (41.6%↓, R 47%↓ B 61%↓, G는 비슷)
    - **학습에 안 쓴 사진으로 검증**(5장으로 구해 나머지 5장에 적용,
      60회 분할): 남은 색 차이 0.0428 → 0.0355 (17.1%↓)

    두 번째가 중요합니다. 같은 사진으로 구하고 같은 사진으로 재면 전체
    평균 방식이 항상 이깁니다 — 그 지표에 맞춰 구한 값이니까요. 실제
    쓰임새는 '모르는 사진에 적용'이므로 그쪽으로 재야 합니다.

    기준은 카메라 JPEG에서 잡습니다 — 정답 쪽에서 골라야 우리 결과의
    치우침이 선택에 끼어들지 않습니다.
    """
    hsv = cv2.cvtColor(camera_bgr, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1].astype(np.float32) / 255.0
    value = hsv[:, :, 2].astype(np.float32) / 255.0
    # 너무 어둡거나 날아간 곳은 채널 비율을 믿을 수 없습니다
    mask = (saturation < NEUTRAL_SATURATION) & (value > 0.15) & (value < 0.92)
    if int(np.count_nonzero(mask)) < MIN_NEUTRAL_PIXELS:
        return None

    camera_mean = camera_bgr[mask].astype(np.float64).mean(axis=0)
    ours_mean = ours_bgr[mask].astype(np.float64).mean(axis=0)
    return camera_mean, ours_mean


def _channel_means(image_bgr: np.ndarray) -> np.ndarray:
    """채널 평균. 극단 화소는 빼고 잽니다.

    포화된 하이라이트와 뭉갠 섀도우는 채널이 함께 잘려 있어 균형 정보가
    없습니다. 그대로 넣으면 밝은 장면일수록 이득이 1에 가까워집니다.
    """
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    usable = (gray > 20) & (gray < 235)
    if np.count_nonzero(usable) < gray.size // 20:
        usable = np.ones_like(gray, dtype=bool)
    return np.array(
        [float(image_bgr[:, :, c][usable].mean()) for c in range(3)], dtype=np.float64
    )


def embedded_preview(path: Path) -> np.ndarray | None:
    """카메라가 만든 내장 미리보기(BGR). 이것이 정답지입니다.

    형식이 두 가지입니다:
      - JPEG   : 대부분의 기종. 바이트열로 옵니다.
      - BITMAP : 비압축 RGB 배열. 일부 기종·변환기가 이렇게 넣습니다.

    아예 없는 파일도 있습니다(변환기가 미리보기를 떼어 낸 DNG 등).
    그때는 이 파일로는 보정할 수 없으므로 None을 돌려주고, 부르는 쪽이
    다른 파일로 넘어갑니다.
    """
    import rawpy

    try:
        with rawpy.imread(str(path)) as raw:
            thumb = raw.extract_thumb()
            if thumb.format == rawpy.ThumbFormat.JPEG:
                return cv2.imdecode(
                    np.frombuffer(thumb.data, np.uint8), cv2.IMREAD_COLOR
                )
            # BITMAP은 이미 디코드된 RGB 배열입니다
            array = np.asarray(thumb.data)
            if array.ndim != 3 or array.shape[2] < 3:
                return None
            return cv2.cvtColor(array[:, :, :3], cv2.COLOR_RGB2BGR)
    except Exception:  # noqa: BLE001 - 없거나 깨진 파일은 그냥 건너뜁니다
        return None


def has_embedded_preview(path: Path) -> bool:
    """이 파일로 보정을 잴 수 있는지 (미리보기 유무)."""
    return embedded_preview(path) is not None


def sample_gain(path: Path) -> np.ndarray | None:
    """한 장에서 채널 이득을 구합니다. 실패하면 None.

    카메라 JPEG과 우리 현상 결과를 같은 크기로 줄여 채널 평균을 비교합니다.
    밝기 자체는 카메라의 톤 커브가 섞여 있어 맞출 수 없으므로, 전체 밝기로
    나눠 **균형만** 봅니다.
    """
    from ..raw_io import load_demosaiced

    camera = embedded_preview(path)
    if camera is None:
        return None

    try:
        # 프로파일은 의도적인 색 연출이라 빼고, 순수 현상만 견줍니다.
        # calibration=False가 중요합니다 — 이미 저장된 보정을 먹인 결과로
        # 다시 보정값을 구하면 자기 자신을 되먹여 값이 계속 밀려납니다.
        ours = load_demosaiced(
            path, half_size=True, apply_profile=False, calibration=False
        )
    except Exception:  # noqa: BLE001
        return None
    ours = np.clip(ours, 0, 255).astype(np.uint8)

    size = (320, 213)
    camera_small = cv2.resize(camera, size, interpolation=cv2.INTER_AREA)
    ours_small = cv2.resize(ours, size, interpolation=cv2.INTER_AREA)

    pair = _neutral_means(camera_small, ours_small)
    if pair is None:
        # 무채색이 거의 없는 장면(단색 조명, 꽉 찬 원색)은 전체 평균으로
        # 물러섭니다. 정확도는 떨어지지만 아무 값도 못 내는 것보다 낫습니다.
        camera_mean = _channel_means(camera_small)
        ours_mean = _channel_means(ours_small)
    else:
        camera_mean, ours_mean = pair
    if np.any(ours_mean <= 1.0) or np.any(camera_mean <= 1.0):
        return None

    # 밝기를 뺀 균형만 비교합니다
    camera_ratio = camera_mean / camera_mean.mean()
    ours_ratio = ours_mean / ours_mean.mean()
    gain = camera_ratio / ours_ratio

    low, high = GAIN_LIMIT
    if not np.all((gain >= low) & (gain <= high)):
        return None
    return gain


def measure(
    paths: Sequence[Path],
    camera: str,
    progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    app_version: str = "",
    key: str = "",
) -> CameraCalibration | None:
    """여러 장에서 이 기종의 채널 이득을 구합니다.

    장마다 이득을 재고 **중앙값**을 씁니다. 평균은 한 장이 이상해도 끌려가는데,
    중앙값은 절반이 멀쩡하면 버팁니다 — 역광이나 단색 장면이 섞여도 됩니다.

    시간이 걸립니다(장당 1~2초). 부르는 쪽이 진행률을 보여 주고 취소를
    받을 수 있게 콜백을 둡니다.
    """
    selected = list(paths)[:MAX_SAMPLES]
    total = len(selected)
    gains: list[np.ndarray] = []

    for index, path in enumerate(selected, start=1):
        if should_cancel and should_cancel():
            log.info("보정 측정 취소 (%d/%d)", index - 1, total)
            return None
        gain = sample_gain(path)
        if gain is not None:
            gains.append(gain)
        if progress:
            progress(index, total)

    if len(gains) < MIN_SAMPLES:
        log.info("보정에 쓸 표본이 부족합니다 (%d/%d)", len(gains), MIN_SAMPLES)
        return None

    median = np.median(np.stack(gains), axis=0)
    # 이득의 곱이 1이 되게 정규화합니다 — 밝기는 건드리지 않고 균형만 바꿉니다.
    median = median / float(np.exp(np.mean(np.log(median))))

    drift = float(np.max(np.abs(median - 1.0)))
    if drift < NEGLIGIBLE:
        log.info("이 기종은 보정이 필요 없습니다 (최대 편차 %.3f)", drift)
        return CameraCalibration(
            camera=camera, gain=(1.0, 1.0, 1.0), samples=len(gains),
            created=datetime.now().isoformat(timespec="seconds"),
            app_version=app_version,
            note="편차가 작아 보정하지 않습니다",
            key=key,
        )

    return CameraCalibration(
        camera=camera,
        gain=(float(median[0]), float(median[1]), float(median[2])),
        samples=len(gains),
        created=datetime.now().isoformat(timespec="seconds"),
        app_version=app_version,
        note=f"내장 JPEG {len(gains)}장 기준",
        key=key,
    )


def apply(image_bgr: np.ndarray, calibration: CameraCalibration | None) -> np.ndarray:
    """보정 이득을 곱합니다. 없거나 중립이면 그대로 돌려줍니다."""
    if calibration is None or calibration.is_neutral():
        return image_bgr
    gain = np.array(calibration.gain, dtype=np.float32)
    return image_bgr.astype(np.float32) * gain
