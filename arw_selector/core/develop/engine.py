"""보정 적용 엔진.

미리보기와 최종 내보내기가 **같은 함수**를 씁니다. 둘이 다르면 사용자가
미리보기에서 맞춘 결과와 실제 파일이 달라지는데, 그것은 이 기능의 신뢰를
전체가 무너뜨립니다. 해상도만 다르고 연산은 동일합니다.

연산 순서는 Lightroom 파이프라인을 따릅니다. 순서가 결과를 좌우하기 때문에
임의로 바꾸면 안 됩니다:
  도형 → 화이트밸런스 → 노출 → 톤 → 곡선 → 국소대비 → HSL →
  컬러그레이딩 → 채도 → 세부(샤픈/노이즈) → 효과 → 워터마크
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from .settings import (
    HSL_BAND_CENTERS,
    BasicSettings,
    ColorGradeSettings,
    CurveSettings,
    DetailSettings,
    DevelopSettings,
    EffectSettings,
    GeometrySettings,
    HSLSettings,
    NoiseAlgorithm,
)

log = logging.getLogger(__name__)

_IDENTITY = np.arange(256, dtype=np.float32)


# ---------------------------------------------------------------- 도형


def apply_geometry(image: np.ndarray, geometry: GeometrySettings) -> np.ndarray:
    """회전 → 반전 → 수평보정 → 크롭.

    크롭을 마지막에 해야 회전으로 생긴 빈 모서리를 잘라낼 수 있습니다.
    """
    if geometry.is_neutral():
        return image

    result = image
    for _ in range(geometry.rotate_quarters % 4):
        result = cv2.rotate(result, cv2.ROTATE_90_CLOCKWISE)

    if geometry.flip_horizontal:
        result = cv2.flip(result, 1)
    if geometry.flip_vertical:
        result = cv2.flip(result, 0)

    if geometry.straighten:
        height, width = result.shape[:2]
        matrix = cv2.getRotationMatrix2D(
            (width / 2, height / 2), geometry.straighten, 1.0
        )
        result = cv2.warpAffine(
            result, matrix, (width, height),
            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE,
        )

    if geometry.has_crop():
        height, width = result.shape[:2]
        x0 = int(round(np.clip(geometry.crop_left, 0.0, 1.0) * width))
        x1 = int(round(np.clip(geometry.crop_right, 0.0, 1.0) * width))
        y0 = int(round(np.clip(geometry.crop_top, 0.0, 1.0) * height))
        y1 = int(round(np.clip(geometry.crop_bottom, 0.0, 1.0) * height))
        # 크롭이 뒤집히거나 0이 되는 값이 들어와도 죽지 않아야 합니다
        if x1 - x0 >= 8 and y1 - y0 >= 8:
            result = result[y0:y1, x0:x1]

    return result


# ---------------------------------------------------------------- 톤 (LUT)


def smooth_curve_lut(points: list[tuple[float, float]]) -> np.ndarray:
    """제어점을 지나는 부드러운 곡선을 256칸 LUT로 만듭니다.

    선형 보간은 제어점마다 꺾여 톤 전환이 딱딱합니다. Fritsch-Carlson
    단조 3차 스플라인을 쓰면 부드럽게 이어지면서도 오버슈트(곡선이
    뒤집혀 계조가 역전되는 것)가 없습니다. 편집기와 엔진이 이 함수를
    공유해 화면과 결과가 일치합니다.
    """
    # 입력 x가 겹치면 h=diff(xs)에 0이 생겨 delta=diff(ys)/h가 inf/nan이 되고
    # LUT 전체가 깨집니다(손편집·레거시 프리셋에서 발생 가능). 같은 x는 첫
    # 값만 남깁니다.
    pts = []
    last_x = None
    for x, y in sorted(points):
        if x != last_x:
            pts.append((x, y))
            last_x = x

    xs = np.array([p[0] for p in pts], dtype=np.float64)
    ys = np.array([p[1] for p in pts], dtype=np.float64)
    n = len(xs)
    if n < 2:
        value = ys[0] if n else 0.0
        return np.clip(np.full(256, value), 0, 255).astype(np.float32)

    h = np.diff(xs)
    delta = np.diff(ys) / h

    # 각 제어점의 접선 (기울기). 양쪽 구간 기울기의 평균에서 출발합니다.
    m = np.empty(n)
    m[0], m[-1] = delta[0], delta[-1]
    for i in range(1, n - 1):
        m[i] = 0.0 if delta[i - 1] * delta[i] <= 0 else (delta[i - 1] + delta[i]) / 2

    # 단조성 보정 — 접선이 너무 크면 곡선이 봉긋 솟아 계조가 역전됩니다.
    for i in range(n - 1):
        if delta[i] == 0:
            m[i] = m[i + 1] = 0.0
            continue
        a, b = m[i] / delta[i], m[i + 1] / delta[i]
        s = a * a + b * b
        if s > 9.0:
            t = 3.0 / np.sqrt(s)
            m[i], m[i + 1] = t * a * delta[i], t * b * delta[i]

    # 각 입력값이 속한 구간에서 Hermite 3차로 평가합니다 (벡터화).
    x = _IDENTITY.astype(np.float64)
    idx = np.clip(np.searchsorted(xs, x, side="right") - 1, 0, n - 2)
    t = (x - xs[idx]) / h[idx]
    t2, t3 = t * t, t * t * t
    result = (
        (2 * t3 - 3 * t2 + 1) * ys[idx]
        + (t3 - 2 * t2 + t) * h[idx] * m[idx]
        + (-2 * t3 + 3 * t2) * ys[idx + 1]
        + (t3 - t2) * h[idx] * m[idx + 1]
    )
    return np.clip(result, 0, 255).astype(np.float32)


# 예전 이름 호환
_spline_lut = smooth_curve_lut


EXPOSURE_LIMIT_EV = 20.0
"""LUT 계산에 실제로 넣는 노출의 상한(EV). 슬라이더 범위(±5)와는 별개입니다.

프리셋은 사용자가 직접 고치는 YAML이라 위젯 범위 밖의 숫자가 들어옵니다.
그대로 넣으면 두 가지로 깨집니다:

  - `2.0 ** 1024`부터 파이썬이 OverflowError를 냅니다. 내보내기에서는 장마다
    예외가 나므로 배치 전체가 결과 없이 끝납니다.
  - 그 아래(예: +200 EV)에서는 예외가 없는 대신 LUT가 float32 inf가 되고,
    `0 * inf`가 **NaN**이 되어 표 첫 칸에 남습니다. 검은 화소가 쓰레기값으로
    나가는데 아무 경고도 없습니다.

자르는 위치는 넉넉합니다 — 8비트 LUT는 **±8 EV면 이미 전부 포화**합니다
(실측: +8과 +200의 결과가 완전히 같고, -8 이하는 전부 0). ±20에서 자르는
것은 결과를 하나도 바꾸지 않습니다.
"""


def _tone_lut(basic: BasicSettings) -> np.ndarray:
    """노출·대비·하이라이트/섀도우/화이트/블랙을 하나의 LUT로 합칩니다.

    픽셀마다 따로 계산하는 대신 256칸 표를 한 번 만들어 적용하면
    6000x4000에서도 비용이 거의 들지 않습니다.
    """
    lut = _IDENTITY.copy()

    if basic.exposure:
        exposure = float(np.clip(basic.exposure, -EXPOSURE_LIMIT_EV, EXPOSURE_LIMIT_EV))
        lut = lut * (2.0 ** exposure)

    normalized = np.clip(lut / 255.0, 0.0, 1.0)

    # 각 구간은 가우시안 가중치로 밀어 경계가 띠로 드러나지 않게 합니다
    if basic.shadows:
        weight = np.exp(-((normalized - 0.25) ** 2) / (2 * 0.25 ** 2))
        normalized = normalized + (basic.shadows / 100.0) * 0.28 * weight
    if basic.highlights:
        weight = np.exp(-((normalized - 0.75) ** 2) / (2 * 0.25 ** 2))
        normalized = normalized + (basic.highlights / 100.0) * 0.28 * weight
    if basic.blacks:
        weight = np.exp(-((normalized - 0.05) ** 2) / (2 * 0.15 ** 2))
        normalized = normalized + (basic.blacks / 100.0) * 0.20 * weight
    if basic.whites:
        weight = np.exp(-((normalized - 0.95) ** 2) / (2 * 0.15 ** 2))
        normalized = normalized + (basic.whites / 100.0) * 0.20 * weight

    # 밝기는 감마입니다 — 흰색과 검정은 그대로 두고 중간톤만 밀어 올립니다.
    # 노출(전체에 2^EV를 곱함)과는 성격이 다릅니다. 노출을 올리면 하이라이트가
    # 먼저 날아가지만, 밝기는 날아간 곳을 더 밝게 만들지 않습니다. 역광 인물의
    # 얼굴만 살리고 싶을 때 쓰는 쪽이 이겁니다.
    if basic.brightness:
        # +100 -> 감마 0.5(밝게), -100 -> 감마 2.0(어둡게)
        gamma = 2.0 ** (-basic.brightness / 100.0)
        normalized = np.power(np.clip(normalized, 0.0, 1.0), gamma)

    if basic.contrast:
        factor = 1.0 + basic.contrast / 100.0
        normalized = (normalized - 0.5) * factor + 0.5

    return np.clip(normalized * 255.0, 0, 255).astype(np.float32)


# 카메라 기본 프로파일(표준). 중립 디모자이크는 평탄해서 Lightroom의
# "Adobe 색상" 기본보다 밋밋합니다. 부드러운 S커브 + 약한 채도를 얹어
# 자연스러운 출발점을 만듭니다. 육안으로 맞춘 값입니다.
_STANDARD_PROFILE_CURVE = ((0, 0), (26, 54), (96, 132), (190, 216), (255, 255))
_STANDARD_PROFILE_SATURATION = 12


def apply_camera_profile(image_bgr: np.ndarray) -> np.ndarray:
    """중립 디모자이크에 기본 카메라 프로파일(표준)을 적용합니다.

    이 결과가 '보정 끔' 상태의 출발점이 됩니다. Lightroom의 기본 프로파일에
    대응하는 자리로, 여기 위에 사용자의 보정과 프로파일 프리셋이 얹힙니다.
    """
    lut = smooth_curve_lut(list(_STANDARD_PROFILE_CURVE))
    result = _apply_lut(image_bgr.astype(np.float32), lut)
    if _STANDARD_PROFILE_SATURATION:
        result = _apply_saturation_gain(result, 1.0 + _STANDARD_PROFILE_SATURATION / 100.0)
    return result.astype(np.float32)


def _apply_saturation_gain(image: np.ndarray, gain: float) -> np.ndarray:
    """float 이미지의 채도를 곱합니다 (HSV 왕복은 8비트라 float에서 직접).

    휘도를 유지하며 각 화소를 회색축에서 밀거나 당깁니다.
    """
    gray = image[:, :, 0] * 0.114 + image[:, :, 1] * 0.587 + image[:, :, 2] * 0.299
    gray = gray[:, :, None]
    return np.clip(gray + (image - gray) * gain, 0.0, 255.0).astype(np.float32)


# 파라메트릭 곡선의 표준 구간 — Lightroom과 같은 사분위 중심입니다.
# 어두운 영역(0~25%) / 어두움(25~50%) / 밝음(50~75%) / 밝은 영역(75~100%).
PARAMETRIC_REGIONS = (
    ("shadows", 0.125),
    ("darks", 0.375),
    ("lights", 0.625),
    ("highlights", 0.875),
)
_PARAMETRIC_WIDTH = 0.15
_PARAMETRIC_STRENGTH = 0.22


def parametric_tone_lut(
    shadows: float, darks: float, lights: float, highlights: float
) -> np.ndarray:
    """파라메트릭 4구간을 256칸 LUT로. 곡선 편집기와 엔진이 공유합니다.

    각 구간은 사분위 중심에 놓인 가우시안 가중치로 밀어, 경계가 띠로
    드러나지 않게 부드럽게 이어집니다.
    """
    normalized = _IDENTITY / 255.0
    amounts = {"shadows": shadows, "darks": darks, "lights": lights, "highlights": highlights}
    for name, center in PARAMETRIC_REGIONS:
        amount = amounts[name]
        if amount:
            weight = np.exp(-((normalized - center) ** 2) / (2 * _PARAMETRIC_WIDTH ** 2))
            normalized = normalized + (amount / 100.0) * _PARAMETRIC_STRENGTH * weight
    return np.clip(normalized * 255.0, 0, 255).astype(np.float32)


def curve_control_points(points) -> list[tuple[float, float]]:
    """제어점에 끝점을 채웁니다. 사용자가 x=0/255에 둔 점이 있으면 그것을 씁니다.

    예전에는 (0,0)과 (255,255)를 무조건 앞뒤에 붙였습니다. smooth_curve_lut은
    x가 겹치면 앞의 값만 남기므로, 왼쪽 끝은 붙인 (0,0)이 사용자 점을 덮고
    오른쪽 끝은 사용자 점이 (255,255)를 덮는 비대칭이 생겼습니다. 반전
    곡선((0,255),(255,0))을 주면 0→0, 255→0이 되어 사진이 통째로 검게
    나왔습니다. 블랙/화이트 포인트는 사용자가 정하는 값이므로 그대로 둡니다.
    """
    resolved = [(float(a), float(b)) for a, b in points]
    xs = {x for x, _ in resolved}
    if 0.0 not in xs:
        resolved.insert(0, (0.0, 0.0))
    if 255.0 not in xs:
        resolved.append((255.0, 255.0))
    return resolved


def _curve_lut(curve: CurveSettings) -> np.ndarray:
    """파라메트릭 곡선 + RGB 포인트 곡선을 합친 LUT."""
    lut = _IDENTITY.copy()

    if curve.shadows or curve.darks or curve.lights or curve.highlights:
        lut = parametric_tone_lut(
            curve.shadows, curve.darks, curve.lights, curve.highlights
        )

    if curve.points_rgb:
        lut = np.interp(
            lut, _IDENTITY, _spline_lut(curve_control_points(curve.points_rgb))
        )

    return np.clip(lut, 0, 255).astype(np.float32)


def _apply_lut(image: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """0~255 float 이미지에 곡선 LUT를 적용합니다.

    예전엔 8비트로 양자화한 뒤 cv2.LUT를 썼지만, 그러면 중간 단계마다
    256단계로 뭉개져 부드러운 계조에 띠(밴딩)가 생깁니다. 14비트 RAW의
    정밀도를 살리려면 float 상태에서 곡선을 보간해 적용해야 합니다.
    """
    clipped = np.clip(image, 0.0, 255.0)
    return np.interp(clipped, _IDENTITY, np.clip(lut, 0.0, 255.0)).astype(np.float32)


# ---------------------------------------------------------------- 화이트밸런스


# 색온도는 절대 Kelvin으로 다룹니다. 0은 "손대지 않음"(as-shot 유지) 신호입니다.
NEUTRAL_KELVIN = 5500


def _kelvin_to_rgb(kelvin: float) -> np.ndarray:
    """색온도(Kelvin)의 흑체 복사 색을 0~255 RGB로 근사합니다 (Tanner Helland)."""
    t = float(np.clip(kelvin, 1000.0, 40000.0)) / 100.0
    if t <= 66:
        r = 255.0
        g = 99.4708025861 * np.log(t) - 161.1195681661
    else:
        r = 329.698727446 * ((t - 60) ** -0.1332047592)
        g = 288.1221695283 * ((t - 60) ** -0.0755148492)
    if t >= 66:
        b = 255.0
    elif t <= 19:
        b = 0.0
    else:
        b = 138.5177312231 * np.log(t - 10) - 305.0447927307
    return np.clip(np.array([r, g, b], dtype=np.float64), 1e-3, 255.0)


def _wb_gain(target_kelvin: float, wb: "tuple | None") -> np.ndarray:
    """as-shot 프리뷰를 목표 색온도로 옮기는 R/G/B 게인을 구합니다.

    프리뷰 JPEG은 카메라 화이트밸런스(camera_wb)가 이미 적용된 상태입니다.
    목표 색온도의 카메라 배수는 그 카메라의 daylight 보정을 기준으로
    scale = rgb(5500)/rgb(target) 만큼 옮겨 얻습니다. 프리뷰에 걸 게인은
    (목표 배수 / as-shot 배수)입니다. RAW 정보(wb)가 없으면 5500K를
    기준으로 한 일반 근사를 씁니다 — 테스트나 비 RAW 입력용입니다.
    """
    if wb is not None:
        camera_wb, daylight_wb = wb
        daylight = np.array(daylight_wb[:3], dtype=np.float64)
        camera = np.array(camera_wb[:3], dtype=np.float64)
        target_mult = daylight * (_kelvin_to_rgb(NEUTRAL_KELVIN) / _kelvin_to_rgb(target_kelvin))
        gain = target_mult / np.maximum(camera, 1e-6)
    else:
        gain = _kelvin_to_rgb(NEUTRAL_KELVIN) / _kelvin_to_rgb(target_kelvin)
    return gain / gain[1]  # G=1로 정규화해 밝기를 유지합니다


def _apply_white_balance(
    image: np.ndarray, basic: BasicSettings, wb: "tuple | None" = None
) -> np.ndarray:
    """색온도(절대 Kelvin) + 색조를 채널 게인으로 적용합니다.

    temperature가 0 이하이면 "손대지 않음"으로 보고 as-shot을 유지합니다.
    프리뷰가 이미 현상된 상태라 채널 게인이 현실적인 근삽니다.
    """
    if basic.temperature <= 0 and not basic.tint:
        return image

    result = image.copy()
    if basic.temperature > 0:
        gain = _wb_gain(basic.temperature, wb)
        result[:, :, 2] *= float(gain[0])  # R
        result[:, :, 1] *= float(gain[1])  # G
        result[:, :, 0] *= float(gain[2])  # B
    if basic.tint:
        result[:, :, 1] *= 1.0 - basic.tint / 100.0 * 0.18  # G
    return result


# ---------------------------------------------------------------- 국소 대비


def _local_contrast(image: np.ndarray, amount: int, radius: float) -> np.ndarray:
    """언샤프 마스크. 명료도(큰 반경)와 텍스처(작은 반경)에 공용."""
    if not amount:
        return image
    blurred = cv2.GaussianBlur(image, (0, 0), radius)
    return image + (image - blurred) * (amount / 100.0)


def _apply_dehaze(image: np.ndarray, amount: int) -> np.ndarray:
    """디헤이즈 근사 — 어두운 쪽을 끌어내리고 채도를 올립니다.

    제대로 하려면 dark channel prior로 투과율 맵을 추정해야 하지만,
    프리뷰 보정 용도로는 전역 근사로 충분합니다.
    """
    if not amount:
        return image

    strength = amount / 100.0
    normalized = np.clip(image / 255.0, 0.0, 1.0)
    # 검은 점을 끌어올리거나 내려서 대비를 만듭니다
    black_point = 0.12 * strength
    normalized = np.clip((normalized - black_point) / max(1e-3, 1.0 - black_point), 0.0, 1.0)

    luma = normalized.mean(axis=2, keepdims=True)
    normalized = luma + (normalized - luma) * (1.0 + 0.4 * strength)
    return np.clip(normalized, 0.0, 1.0) * 255.0


# ---------------------------------------------------------------- HSL


def _band_weight(hue: np.ndarray, center: int, width: float = 22.0) -> np.ndarray:
    """색조 원형 거리에 따른 가중치. 빨강이 0/179를 넘나드는 것을 처리합니다."""
    distance = np.abs(hue - center)
    distance = np.minimum(distance, 180.0 - distance)
    return np.exp(-(distance ** 2) / (2 * width * width))


def _apply_hsl(image: np.ndarray, hsl: HSLSettings) -> np.ndarray:
    """8개 색상대별 색조/채도/광도 조정."""
    if hsl.is_neutral():
        return image

    hsv = cv2.cvtColor(
        np.clip(image, 0, 255).astype(np.uint8), cv2.COLOR_BGR2HSV
    ).astype(np.float32)
    hue, saturation, value = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    hue_shift = np.zeros_like(hue)
    saturation_scale = np.ones_like(saturation)
    value_scale = np.ones_like(value)

    for name, band in hsl.bands.items():
        if band.is_neutral():
            continue
        weight = _band_weight(hue, HSL_BAND_CENTERS[name])
        if band.hue:
            hue_shift += weight * (band.hue / 100.0 * 15.0)
        if band.saturation:
            saturation_scale += weight * (band.saturation / 100.0)
        if band.luminance:
            value_scale += weight * (band.luminance / 100.0 * 0.5)

    hsv[:, :, 0] = np.mod(hue + hue_shift, 180.0)
    hsv[:, :, 1] = np.clip(saturation * saturation_scale, 0, 255)
    hsv[:, :, 2] = np.clip(value * value_scale, 0, 255)

    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR).astype(np.float32)


# ---------------------------------------------------------------- 컬러 그레이딩


def _zone_color(hue_degrees: int, saturation: int) -> np.ndarray:
    """색상휠 값을 BGR 방향 벡터로."""
    hsv = np.uint8([[[int(hue_degrees / 2) % 180, 255, 255]]])
    bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0].astype(np.float32) / 255.0
    return (bgr - bgr.mean()) * (saturation / 100.0)


def _apply_color_grade(image: np.ndarray, grade: ColorGradeSettings) -> np.ndarray:
    """어두운/중간/밝은 영역에 각각 다른 색을 얹습니다."""
    if grade.is_neutral():
        return image

    normalized = np.clip(image / 255.0, 0.0, 1.0)
    luma = normalized.mean(axis=2, keepdims=True)

    # balance는 구간 경계를 밀어 어느 쪽을 넓게 볼지 정합니다
    balance = grade.balance / 100.0 * 0.25
    shadow_mask = np.clip(1.0 - (luma - balance) * 2.5, 0.0, 1.0)
    highlight_mask = np.clip((luma - balance - 0.6) * 2.5, 0.0, 1.0)
    midtone_mask = np.clip(1.0 - shadow_mask - highlight_mask, 0.0, 1.0)

    strength = grade.blending / 100.0 * 0.5
    for zone, mask in (
        (grade.shadows, shadow_mask),
        (grade.midtones, midtone_mask),
        (grade.highlights, highlight_mask),
    ):
        if zone.is_neutral():
            continue
        if zone.saturation:
            normalized = normalized + _zone_color(zone.hue, zone.saturation) * mask * strength
        if zone.luminance:
            normalized = normalized + (zone.luminance / 100.0) * 0.3 * mask

    return np.clip(normalized, 0.0, 1.0) * 255.0


# ---------------------------------------------------------------- 채도


def _apply_saturation(image: np.ndarray, basic: BasicSettings) -> np.ndarray:
    """채도와 바이브런스.

    바이브런스는 이미 진한 색을 덜 건드린다 — 인물 사진에서 피부색이
    타는 것을 막기 위해섭니다.
    """
    if not basic.saturation and not basic.vibrance:
        return image

    luma = image.mean(axis=2, keepdims=True)
    delta = image - luma

    factor = 1.0 + basic.saturation / 100.0
    if basic.vibrance:
        current = np.abs(delta).max(axis=2, keepdims=True) / 255.0
        factor = factor + (basic.vibrance / 100.0) * (1.0 - np.clip(current * 2.0, 0.0, 1.0))

    return luma + delta * factor


# ---------------------------------------------------------------- 세부


# 노이즈 σ 추정용 3×3 마스크(Immerkær). i.i.d. 노이즈 σ에 대한 응답의
# 표준편차가 정확히 6σ라, 응답을 6으로 나누면 σ가 나옵니다.
_NOISE_MASK = np.array(
    [[1.0, -2.0, 1.0], [-2.0, 4.0, -2.0], [1.0, -2.0, 1.0]], dtype=np.float32
)

_MIN_NOISE_SIGMA = 0.4
"""추정 σ의 하한.

인공 이미지나 이미 뭉갠 축소본은 σ가 0에 가깝게 나옵니다. 그대로 쓰면
필터 강도가 0이 되어 슬라이더를 올려도 아무 일도 일어나지 않습니다.
"""


def estimate_noise_sigma(luma: np.ndarray) -> float:
    """평탄 영역의 노이즈 표준편차를 추정합니다.

    같은 슬라이더 값이 ISO 100에서도 ISO 6400에서도 "적당히"가 되려면
    필터 강도가 그 사진의 실제 노이즈에 비례해야 합니다. 고정 강도로
    두면 저감도에서는 과하게 뭉개고 고감도에서는 손도 못 댑니다.

    평균이 아니라 중앙절대편차를 쓰므로 피사체의 엣지·텍스처에 끌려가지
    않습니다. 7화소 간격으로만 훑어 32MP에서도 0.05초입니다.

    실측(디모자이크 직후): R6M3 ISO6400 = 2.47, R6m2 ISO800 = 0.99,
    a1 ISO800 = 0.99, S1R = 5.44.
    """
    response = cv2.filter2D(luma.astype(np.float32), cv2.CV_32F, _NOISE_MASK)
    sample = np.abs(response[::7, ::7])
    if sample.size == 0:
        return _MIN_NOISE_SIGMA
    return max(_MIN_NOISE_SIGMA, float(1.4826 * np.median(sample) / 6.0))


def _reduce_color_noise(ycc: np.ndarray, amount: int, radius: int) -> None:
    """색 노이즈를 지웁니다 (제자리 수정).

    색 노이즈는 화소 단위 알갱이가 아니라 수십 화소에 걸친 얼룩입니다.
    실측(R6M3 ISO6400): 1/4로 줄여도 색 노이즈가 원본의 54%가 남습니다
    (휘도는 32%). 그래서 3×3 메디안 같은 작은 커널로는 손도 못 댑니다.

    줄여 놓고 지운 뒤 다시 키우면 큰 얼룩을 1/16 비용으로 잡습니다.
    휘도 채널은 건드리지 않으므로 디테일 손실이 원리적으로 0입니다
    (실측: 엣지 그래디언트 97.720 → 97.724, 측정 오차 범위).
    """
    height, width = ycc.shape[:2]
    # 원본 해상도 기준 흐림 반경. 조정량과 반경을 곱해 하나의 연속값으로
    # 만듭니다. 예전 코드는 커널 크기(3 또는 5)를 직접 골라서 슬라이더가
    # 사실상 2단 스위치였습니다 — 1~53이 전부 같은 결과였습니다.
    blur = (0.5 + 2.5 * (amount / 100.0)) * (0.4 + 1.6 * (radius / 100.0))
    # 큰 반경은 줄여 놓고 처리하는 편이 훨씬 쌉니다. 축소해도 반경 자체는
    # blur가 정하므로 슬라이더는 연속으로 움직입니다.
    scale = max(1, min(4, int(blur / 2.0)))
    small_sigma = max(0.3, blur / scale)
    small_size = (max(1, width // scale), max(1, height // scale))

    for channel in (1, 2):
        plane = ycc[:, :, channel]
        if scale > 1:
            small = cv2.resize(plane, small_size, interpolation=cv2.INTER_AREA)
        else:
            small = plane
        small = cv2.GaussianBlur(small, (0, 0), small_sigma)
        if scale > 1:
            small = cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR)
        ycc[:, :, channel] = small


def _denoise_luma_plane(luma: np.ndarray, algorithm, strength: float, sigma: float) -> np.ndarray:
    """휘도 한 채널을 방식에 따라 지웁니다. 입출력 모두 float 0~255.

    OpenCV의 비국소 평균은 8비트만 받습니다. 그런데 이 시점 값은 14비트
    RAW에서 온 소수점까지 살아 있는 float라, 8비트로 바꿔 돌려주면 계조가
    한 단계 뭉갭니다. 결과가 아니라 **변화량**만 8비트에서 가져와 float
    원본에 더해 정밀도를 지킵니다.
    """
    as_uint8 = np.clip(luma, 0.0, 255.0).astype(np.uint8)

    if algorithm is NoiseAlgorithm.BILATERAL:
        # 지름을 0으로 주면 OpenCV가 sigmaSpace에서 계산합니다. 예전 코드는
        # 지름을 5로 고정해 놓고 sigma만 키웠는데, 지름이 작으면 sigma를
        # 아무리 올려도 5×5 평균 이상은 못 갑니다 — 그래서 슬라이더 60
        # 이상이 100과 사실상 같았습니다(실측 차이 0.05).
        space_sigma = 1.0 + 2.5 * strength
        color_sigma = float(sigma * (1.0 + 4.0 * strength))
        denoised = cv2.bilateralFilter(as_uint8, 0, color_sigma, space_sigma)
    else:
        # h는 "이만큼의 차이는 노이즈로 본다"는 뜻이라 사진의 실제 σ에
        # 비례해야 합니다. 실측(R6M3 ISO6400, σ=2.41)한 h/σ 대 노이즈 감소·
        # 디테일 보존: 0.8σ에서 11%·100%, 1.2σ에서 57%·99%, 1.6σ에서
        # 74%·91%, 1.8σ에서 77%·84%, 2.0σ부터 디테일이 무너집니다(75%).
        # 슬라이더 전 구간이 쓸모 있도록 0.7σ~1.8σ에 펼칩니다.
        h = float(sigma * (0.7 + 1.1 * strength))
        template, search = (
            (7, 21) if algorithm is NoiseAlgorithm.NLMEANS_HQ else (5, 11)
        )
        denoised = cv2.fastNlMeansDenoising(as_uint8, None, h, template, search)

    return luma + (denoised.astype(np.float32) - as_uint8.astype(np.float32))


def _protect_detail(original: np.ndarray, denoised: np.ndarray,
                    amount: int, sigma: float) -> np.ndarray:
    """무늬가 있는 곳에 원본을 되살립니다.

    노이즈 감소는 평탄한 하늘·피부에는 이롭지만 머리카락·나뭇잎처럼
    잔무늬가 있는 곳에서는 무늬 자체를 지웁니다. 국소 대비를 **지운
    결과**에서 재는 것이 핵심입니다 — 원본에서 재면 노이즈가 큰 곳이
    무늬 있는 곳으로 잘못 잡혀 노이즈를 되살립니다.
    """
    # 가중치 맵은 어차피 완만하므로 절반으로 줄여 계산합니다. 32MP에서
    # 박스 필터를 원본 해상도로 돌리면 그것만으로 0.4초가 듭니다. 4분의 1로
    # 더 줄이면 머리카락 같은 잔무늬가 평균에 묻혀 보호 대상에서 빠집니다.
    height, width = denoised.shape[:2]
    small = cv2.resize(denoised, (max(1, width // 2), max(1, height // 2)),
                       interpolation=cv2.INTER_AREA)
    kernel = (5, 5)
    mean = cv2.boxFilter(small, cv2.CV_32F, kernel)
    mean_square = cv2.boxFilter(small * small, cv2.CV_32F, kernel)
    local = np.sqrt(np.maximum(mean_square - mean * mean, 0.0))
    # 노이즈 σ의 0.5배를 넘는 국소 대비부터 '무늬'로 보고, 2배에서 완전히
    # 되살립니다. 실측(R6M3 ISO6400, 양방향 NR=75)으로 고른 값입니다 —
    # 이 구간에서 엣지가 52.95 → 71.27로 돌아오는 동안 평탄 영역 노이즈는
    # 1.217 → 1.222로 그대로였습니다. 임계를 더 내리면(0.3σ) 엣지는 79까지
    # 가지만 평탄 노이즈가 1.36으로 되살아나 노이즈 감소가 무의미해집니다.
    weight = np.clip((local - sigma * 0.5) / max(sigma * 1.5, 1e-3), 0.0, 1.0)
    weight *= amount / 100.0
    weight = cv2.resize(weight, (width, height), interpolation=cv2.INTER_LINEAR)
    return denoised + (original - denoised) * weight


FACE_NR_MARGIN = 0.35
"""얼굴 상자를 이만큼 넓혀 노이즈 감소 대상으로 잡습니다.

목·귀·이마 경계까지 들어와야 합니다. 상자에 딱 맞추면 얼굴만 매끄럽고
목은 거친, 오려 붙인 듯한 결과가 됩니다.
"""

_FACE_WEIGHT_LONG_EDGE = 512
"""가중치 맵을 만들 해상도. 어차피 완만한 맵이라 원본 크기로 만들 이유가 없습니다."""

FACE_PRIORITY_MAX_CUT = 0.5
"""얼굴 우선을 100까지 올려도 얼굴 밖에서 덜어낼 수 있는 최대 비율.

예전에는 100이 곧 "얼굴 밖 0"이었습니다. 그런데 노이즈가 가장 심한 곳은
얼굴이 아니라 어두운 배경입니다 — 실측(A6700 ISO3200, DSC02434)에서 얼굴
안 σ 1.52 대 얼굴 밖 σ 3.05. 시끄러운 쪽을 통째로 손대지 않으니 노이즈
감소를 100으로 올려도 화면이 그대로였습니다.

이 상한을 두면 얼굴 우선 100에서도 얼굴 밖이 절반 세기를 받습니다.
얼굴 안 감소율과 세부 보존은 그대로입니다(둘 다 실측에서 변화 없음).
"""


def _face_weight_map(
    height: int, width: int, faces: np.ndarray | None, priority: int
) -> np.ndarray | None:
    """얼굴 안은 1.0, 밖은 (1 - 우선도)인 가중치 맵. 얼굴이 없으면 None.

    None을 돌려주면 부르는 쪽은 화면 전체에 같은 강도를 겁니다. 얼굴을 못
    찾았다고 노이즈 감소를 꺼 버리면 풍경 사진에서 기능이 사라집니다.
    """
    if faces is None or len(faces) == 0 or priority <= 0:
        return None

    floor = 1.0 - FACE_PRIORITY_MAX_CUT * (min(100, max(0, priority)) / 100.0)
    scale = min(1.0, _FACE_WEIGHT_LONG_EDGE / max(height, width))
    sh = max(8, int(round(height * scale)))
    sw = max(8, int(round(width * scale)))
    sx, sy = sw / width, sh / height

    small = np.full((sh, sw), floor, np.float32)
    for face in faces:
        x, y, fw, fh = (float(v) for v in face[:4])
        cx = (x + fw / 2.0) * sx
        cy = (y + fh / 2.0) * sy
        ax = max(2, int(fw * (0.5 + FACE_NR_MARGIN) * sx))
        ay = max(2, int(fh * (0.5 + FACE_NR_MARGIN) * sy))
        cv2.ellipse(small, (int(cx), int(cy)), (ax, ay), 0, 0, 360, 1.0, -1)

    # 경계를 부드럽게. 딱 끊으면 얼굴 테두리에 노이즈 알갱이가 확 달라지는
    # 선이 생겨, 노이즈 자체보다 더 눈에 띕니다.
    sigma = max(1.5, min(sh, sw) * 0.03)
    small = cv2.GaussianBlur(small, (0, 0), sigma)
    np.clip(small, floor, 1.0, out=small)
    return cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR)


def _active_bounds(weight: np.ndarray, threshold: float = 0.01):
    """가중치가 살아 있는 영역의 (y0, y1, x0, x1). 전부 0이면 None.

    얼굴 우선 100%에서는 얼굴 밖 가중치가 0이라, 그 바깥까지 비싼 필터를
    돌릴 이유가 없습니다. 32MP 전면 NLM은 몇 초가 걸립니다.
    """
    rows = np.where(weight.max(axis=1) > threshold)[0]
    cols = np.where(weight.max(axis=0) > threshold)[0]
    if rows.size == 0 or cols.size == 0:
        return None
    return int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1


def apply_noise_reduction(
    image: np.ndarray, detail: DetailSettings, faces: np.ndarray | None = None
) -> np.ndarray:
    """휘도 노이즈와 색 노이즈를 분리해 지웁니다. float 0~255 BGR 입출력.

    한 필터로 둘을 함께 지우려 하면 성격이 달라 둘 다 실패합니다. 휘도
    노이즈는 화소 단위 알갱이라 디테일과 주파수가 겹치고, 색 노이즈는
    수십 화소짜리 얼룩이라 작은 커널이 닿지 않습니다. 예전 방식(BGR에
    5×5 양방향 + a/b 3×3 메디안)이 정확히 그랬습니다 — 실측에서 R6M3
    ISO6400 사진의 디테일을 50% 잃고도 남은 노이즈는 ISO800 무보정
    사진보다 많았습니다.

    faces는 이 이미지 좌표계의 얼굴 상자입니다. detail.face_priority와 함께
    쓰여 얼굴 밖 강도를 낮춥니다.
    """
    if not detail.noise_reduction and not detail.color_noise_reduction:
        return image

    if detail.noise_algorithm is NoiseAlgorithm.LEGACY:
        return _legacy_noise_reduction(image, detail)

    ycc = cv2.cvtColor(
        np.clip(image, 0.0, 255.0).astype(np.float32), cv2.COLOR_BGR2YCrCb
    )

    # 색을 먼저 합니다. 휘도를 먼저 지우면 색 얼룩이 그대로 남은 채
    # 디테일 보존 판정을 하게 되어, 얼룩을 무늬로 착각합니다.
    if detail.color_noise_reduction:
        _reduce_color_noise(ycc, detail.color_noise_reduction, detail.color_noise_radius)

    if detail.noise_reduction:
        luma = ycc[:, :, 0]
        height, width = luma.shape[:2]
        weight = _face_weight_map(
            height, width, faces, getattr(detail, "face_priority", 0)
        )

        bounds = (0, height, 0, width)
        if weight is not None:
            active = _active_bounds(weight)
            if active is None:
                return cv2.cvtColor(ycc, cv2.COLOR_YCrCb2BGR)
            bounds = active

        y0, y1, x0, x1 = bounds
        patch = np.ascontiguousarray(luma[y0:y1, x0:x1])
        # σ는 **화면 전체**에서 잽니다. 잘라낸 조각에서 재면 얼굴 우선을
        # 켰을 때와 껐을 때 같은 슬라이더가 다른 세기가 됩니다 — 얼굴 안은
        # 대개 조용해서(실측 σ 1.52) 전면(3.21)의 절반이고, 그만큼 h가
        # 작아져 덜 지웁니다. 사용자에게는 "얼굴 우선을 켰더니 노이즈 감소가
        # 약해졌다"로 보입니다.
        sigma = estimate_noise_sigma(luma)
        denoised = _denoise_luma_plane(
            patch, detail.noise_algorithm, detail.noise_reduction / 100.0, sigma
        )
        if detail.noise_detail:
            denoised = _protect_detail(patch, denoised, detail.noise_detail, sigma)
        if weight is not None:
            denoised = patch + (denoised - patch) * weight[y0:y1, x0:x1]
        ycc[y0:y1, x0:x1, 0] = denoised

    return cv2.cvtColor(ycc, cv2.COLOR_YCrCb2BGR)


def _legacy_noise_reduction(image: np.ndarray, detail: DetailSettings) -> np.ndarray:
    """예전 방식 그대로. 지웠던 사진을 재현해야 할 때만 씁니다.

    고치지 않고 남겨 둡니다 — 여기서 한 글자라도 바꾸면 '구버전 재현'이라는
    이 함수의 존재 이유가 사라집니다.
    """
    as_uint8 = np.clip(image, 0, 255).astype(np.uint8)
    if detail.color_noise_reduction:
        lab = cv2.cvtColor(as_uint8, cv2.COLOR_BGR2Lab)
        strength = detail.color_noise_reduction / 100.0 * 15
        lab[:, :, 1] = cv2.medianBlur(lab[:, :, 1], 3 if strength < 8 else 5)
        lab[:, :, 2] = cv2.medianBlur(lab[:, :, 2], 3 if strength < 8 else 5)
        as_uint8 = cv2.cvtColor(lab, cv2.COLOR_Lab2BGR)
    if detail.noise_reduction:
        strength = detail.noise_reduction / 100.0
        as_uint8 = cv2.bilateralFilter(
            as_uint8, 5, int(10 + 60 * strength), int(10 + 60 * strength)
        )
    return as_uint8.astype(np.float32)


STRIPE_MIN_PERIOD = 16
STRIPE_MAX_PERIOD = 400
"""줄무늬로 볼 주기 범위(화소).

실측: LED월 줄무늬 컷의 주기는 **103px**이었고, 줄무늬가 없는 컷은 4~5px
(그냥 노이즈 자기상관)이었습니다. 아래쪽을 16으로 막아 노이즈를 줄무늬로
착각하지 않게 하고, 위쪽은 프레임 높이의 일부만 덮는 완만한 밝기 변화를
빼기 위해 400에서 끊습니다.
"""

STRIPE_BASELINE_SIGMA = 13.5
"""기준선을 만들 가우시안 시그마.

**줄무늬 주기(실측 103px)보다 확실히 작아야 합니다.** 크게 잡으면 줄무늬
자체가 기준선에 흡수되어 잔차에서 사라집니다 — 133으로 뒀다가 신호를
통째로 잃고 정상 컷까지 보정하는 상태를 만들었습니다.
"""

STRIPE_MIN_STRENGTH = 0.35
"""자기상관 최고값이 이보다 낮으면 줄무늬가 없다고 봅니다.

실측: 줄무늬 컷 0.717 / 0.669, 정상 컷 0.618 / 0.447. 정상 컷도 값이
꽤 높게 나오므로 **이 값만으로는 못 가릅니다** — 주기(STRIPE_MIN_PERIOD)와
반드시 함께 봐야 합니다.
"""


def measure_stripe(gray: np.ndarray) -> tuple[np.ndarray, int, float]:
    """가로줄 밝기의 주기적 성분을 찾습니다.

    반환: (행별 보정량, 주기, 주기성 세기). 주기가 0이면 줄무늬 없음.

    행 **평균**을 쓰고 기준선 시그마를 줄무늬 주기보다 **작게** 잡습니다.
    처음에 중앙값 + 큰 시그마(133)로 썼다가 신호를 통째로 잃었습니다 —
    시그마가 주기보다 크면 103px 진동이 기준선에 흡수되어 잔차에서 사라지고,
    주기가 전부 하한(16)으로 나오면서 **정상 컷까지 67% 보정**됐습니다.
    """
    profile = gray.mean(axis=1).astype(np.float32)
    if profile.size < STRIPE_MIN_PERIOD * 4:
        return np.zeros_like(profile), 0, 0.0

    # 완만한 밝기 변화(그라데이션·비네팅)는 사진의 일부라 빼야 하지만,
    # 시그마는 반드시 줄무늬 주기보다 작아야 합니다.
    baseline = cv2.GaussianBlur(profile.reshape(-1, 1), (0, 0),
                                STRIPE_BASELINE_SIGMA).ravel()
    residual = profile - baseline

    centred = residual - residual.mean()
    spread = float(centred.std())
    if spread < 1e-3:
        return np.zeros_like(profile), 0, 0.0

    norm = centred / spread
    auto = np.correlate(norm, norm, mode="full")[len(norm):] / len(norm)
    window = auto[STRIPE_MIN_PERIOD:min(STRIPE_MAX_PERIOD, len(auto))]
    if window.size == 0:
        return np.zeros_like(profile), 0, 0.0

    strength = float(window.max())
    period = int(np.argmax(window)) + STRIPE_MIN_PERIOD
    if strength < STRIPE_MIN_STRENGTH:
        return np.zeros_like(profile), 0, strength
    return residual, period, strength


def apply_destripe(image: np.ndarray, amount: int) -> np.ndarray:
    """LED월 등에서 생기는 가로 줄무늬를 지웁니다. float 0~255 BGR.

    LED 패널의 PWM 점멸과 롤링셔터 판독이 어긋나면 화면에 가로 밴드가
    남습니다. 실측한 두 컷 모두 주기가 **103px로 같았습니다** — ISO도
    셔터도 달랐는데 같다는 것은 피사체가 아니라 판독 주기에서 온다는 뜻입니다.

    보정은 행마다 같은 값을 빼는 것뿐이라 가로 방향 디테일은 손대지 않습니다.
    """
    if amount <= 0:
        return image

    gray = cv2.cvtColor(np.clip(image, 0.0, 255.0).astype(np.float32),
                        cv2.COLOR_BGR2GRAY)
    residual, period, _strength = measure_stripe(gray)
    if period <= 0:
        return image  # 줄무늬가 안 잡히면 아무것도 하지 않습니다

    correction = residual * (min(100, amount) / 100.0)
    return image - correction[:, None, None]


def _apply_detail(image: np.ndarray, detail: DetailSettings) -> np.ndarray:
    """샤프닝과 노이즈 감소.

    노이즈 감소를 먼저 합니다. 순서를 바꾸면 샤프닝이 키워 놓은 노이즈를
    다시 지우느라 디테일까지 뭉갭니다.
    """
    if detail.is_neutral():
        return image

    faces = None
    if getattr(detail, "face_priority", 0) and detail.noise_reduction:
        # 마스크와 별개로 여기서 한 번 더 찾습니다. 마스크는 없을 수도 있고
        # 있어도 이 시점보다 뒤에서 도는데, 노이즈 감소는 샤프닝보다 먼저
        # 끝나야 하기 때문입니다. 축소본 검출이라 50ms 수준입니다.
        from .masks import _detect_faces_full  # noqa: PLC0415 - 순환 임포트 회피

        try:
            faces = _detect_faces_full(np.clip(image, 0, 255).astype(np.uint8))
        except cv2.error:
            log.debug("얼굴 우선 노이즈 감소용 검출 실패", exc_info=True)

    # 줄무늬를 **노이즈 감소보다 먼저** 지웁니다. 순서를 바꾸면 노이즈
    # 감소가 줄무늬를 평탄한 무늬로 착각해 일부 뭉개 놓고, 남은 절반만
    # 여기서 빼게 되어 얼룩덜룩해집니다.
    result = apply_destripe(image, getattr(detail, "destripe", 0))
    result = apply_noise_reduction(result, detail, faces)

    if detail.sharpen_amount:
        blurred = cv2.GaussianBlur(result, (0, 0), max(0.3, detail.sharpen_radius))
        result = result + (result - blurred) * (detail.sharpen_amount / 100.0)

    return result


# ---------------------------------------------------------------- 효과


def _apply_effects(image: np.ndarray, effects: EffectSettings) -> np.ndarray:
    """그레인과 비네팅."""
    if effects == EffectSettings():
        return image

    result = image
    height, width = result.shape[:2]

    if effects.vignette_amount:
        y, x = np.ogrid[:height, :width]
        center_y, center_x = height / 2.0, width / 2.0
        distance = np.sqrt(
            ((x - center_x) / center_x) ** 2 + ((y - center_y) / center_y) ** 2
        )
        midpoint = max(0.1, effects.vignette_midpoint / 100.0 * 1.5)
        mask = np.clip((distance - midpoint) / max(1e-3, 1.5 - midpoint), 0.0, 1.0)
        result = result * (1.0 + (effects.vignette_amount / 100.0) * mask[:, :, None])

    if effects.grain_amount:
        # 그레인 크기는 저해상도 노이즈를 확대해서 만듭니다
        size = max(1, int(effects.grain_size / 100.0 * 4) + 1)
        rng = np.random.default_rng(12345)  # 재현 가능해야 미리보기가 깜빡이지 않습니다
        small = rng.normal(0, 1, (max(1, height // size), max(1, width // size), 1))
        noise = cv2.resize(
            small.astype(np.float32), (width, height), interpolation=cv2.INTER_LINEAR
        )
        result = result + noise[:, :, None] * (effects.grain_amount / 100.0 * 18.0)

    return result


# ---------------------------------------------------------------- 진입점


def apply_settings(
    image_bgr: np.ndarray,
    settings: DevelopSettings,
    source: "Path | None" = None,
    metadata=None,
    wb: "tuple | None" = None,
    main_face_box: "tuple[float, float, float, float] | None" = None,
) -> np.ndarray:
    """BGR uint8 이미지에 보정 전체를 적용합니다.

    source/metadata는 하단 정보 띠를 그릴 때만 씁니다. 없으면 띠는 건너뜁니다.
    wb는 (camera_whitebalance, daylight_whitebalance) 튜플로, 절대 색온도
    변환에 씁니다. 없으면 5500K 기준 일반 근사를 씁니다.

    main_face_box는 분석이 고른(또는 사용자가 화면에서 바꾼) 주 피사체 얼굴의
    정규화 좌표입니다. 얼굴 마스크의 '주 피사체' 대상이 이 얼굴을 따라갑니다.
    안 주면 마스크가 이 시점 이미지에서 스스로 고르는데, 그러면 화면의 빨간
    상자와 다른 얼굴에 걸릴 수 있습니다.
    """
    # 폭이나 높이가 0인 배열은 거의 모든 OpenCV 연산이 예외를 던집니다. 잘못된
    # 크롭 계산이나 손상 파일에서 한 장이 그렇게 나와도 미리보기 스레드나 배치
    # 내보내기 전체가 멈추면 안 되므로, 반환 계약만 지켜 그대로 돌려줍니다.
    if image_bgr.size == 0:
        from ..raw_io import to_display

        return to_display(image_bgr)

    if settings.is_neutral():
        # 보정이 없어도 반환 계약(8비트 BGR)은 지켜야 합니다. 디모자이크 입력은
        # float 0~255라, 그대로 돌려주면 미리보기는 컬러 노이즈가 되고 저장은
        # 인코더에서 깨집니다. 실제로 '최종 미리보기'에서 발생한 버그입니다.
        from ..raw_io import to_display

        return to_display(image_bgr)

    # 광학 보정을 가장 먼저 합니다. 렌즈 왜곡을 편 다음에 자르고 톤을 만져야
    # 순서가 맞다 — 반대로 하면 크롭한 조각에 왜곡 모델을 적용하게 됩니다.
    working = image_bgr
    if not settings.optics.is_neutral():
        from ..raw_io import is_editable_image
        from .optics import apply_optics

        optics = settings.optics
        # JPEG·HEIF는 카메라가 이미 렌즈 보정까지 걸어 구워 낸 결과입니다.
        # 한 번 더 걸면 이중 보정이라 가장자리가 반대로 휩니다. 화면에서는
        # 체크박스를 잠가 두지만 그것만으로는 부족합니다 — 잠긴 체크박스도
        # isChecked()는 True를 돌려주고, 프리셋을 누르면 값이 되살아납니다.
        # 여기서 막아야 CLI·배치·프리셋 어느 경로로 와도 같습니다.
        # (수동 보정은 그대로 둡니다. 사용자가 눈으로 보고 넣는 값입니다.)
        if optics.auto_enabled and source is not None and is_editable_image(source):
            optics = replace(optics, auto_enabled=False)

        working = apply_optics(working, optics, metadata)

    result = apply_geometry(working, settings.geometry).astype(np.float32)

    basic = settings.basic
    result = _apply_white_balance(result, basic, wb)

    # 톤과 곡선은 둘 다 RGB LUT라, 256칸 표 위에서 미리 합성해 이미지에는
    # 한 번만 적용합니다. float 보간이 화소당 비용이라 패스 수를 줄입니다.
    tone = _tone_lut(basic)
    curve = _curve_lut(settings.curve)
    combined = np.interp(tone, _IDENTITY, curve).astype(np.float32)
    if not np.array_equal(combined, _IDENTITY):
        result = _apply_lut(result, combined)

    # 채널별 곡선은 LUT를 채널마다 따로 적용합니다
    for channel, points in (
        (2, settings.curve.points_red),
        (1, settings.curve.points_green),
        (0, settings.curve.points_blue),
    ):
        if points:
            lut = _spline_lut(curve_control_points(points))
            result[:, :, channel] = _apply_lut(
                result[:, :, channel][:, :, None], lut
            )[:, :, 0]

    if basic.dehaze:
        result = _apply_dehaze(result, basic.dehaze)
    if basic.clarity:
        result = _local_contrast(result, basic.clarity, radius=max(3.0, min(result.shape[:2]) / 120))
    if basic.texture:
        result = _local_contrast(result, basic.texture, radius=1.2)

    result = _apply_hsl(result, settings.hsl)
    result = _apply_color_grade(result, settings.color_grade)
    result = _apply_saturation(result, basic)
    result = _apply_detail(result, settings.detail)
    result = _apply_effects(result, settings.effects)

    # 국소 보정(마스크)은 전역 보정이 다 끝난 위에 얹습니다. 얼굴/눈/배경은
    # 이 시점 이미지에서 다시 검출하므로 마스크가 화면과 정확히 맞습니다.
    if settings.masks:
        from .masks import apply_masks

        result = apply_masks(result, settings.masks,
                             main_face_box=main_face_box)

    output = np.clip(result, 0.0, 255.0).astype(np.uint8)
    return apply_overlays(output, settings, source, metadata)


def apply_overlays(
    image_bgr: np.ndarray,
    settings: DevelopSettings,
    source: "Path | None" = None,
    metadata=None,
) -> np.ndarray:
    """보정이 끝난 8비트 이미지에 워터마크와 정보 띠를 얹습니다.

    이 둘은 계조를 다루는 보정이 아니라 결과물에 덧붙이는 표기입니다. 따로
    떼어 둬야 미리보기에서 히스토그램·클리핑 경고를 '사진 자체'의 계조로
    계산할 수 있습니다. 정보 띠의 검은 바가 히스토그램에 섞이면 보정값이
    바뀐 것처럼 보입니다.
    """
    output = image_bgr
    if settings.watermark.is_active():
        from .watermark import apply_watermark

        output = apply_watermark(output, settings.watermark)

    # 정보 띠는 사진 아래에 덧붙이는 것이라 워터마크 뒤에 옵니다.
    # 먼저 붙이면 워터마크가 띠 위에 얹힐 수 있습니다.
    if settings.exif_strip.is_active() and source is not None:
        from .exif_strip import apply_exif_strip

        output = apply_exif_strip(output, source, metadata, settings.exif_strip)

    return output


def render_preview(
    image_bgr: np.ndarray,
    settings: DevelopSettings,
    long_edge: int = 1400,
    source: "Path | None" = None,
    metadata=None,
    wb: "tuple | None" = None,
    main_face_box: "tuple[float, float, float, float] | None" = None,
) -> np.ndarray:
    """미리보기용. 최종 내보내기와 같은 연산을 축소본에 적용합니다."""
    from ..raw_io import resize_long_edge

    return apply_settings(
        resize_long_edge(image_bgr, long_edge), settings, source, metadata, wb,
        main_face_box,
    )


def export_image(
    source: Path,
    destination: Path,
    settings: DevelopSettings,
    quality: int = 95,
    long_edge: int | None = None,
    main_face_box: "tuple[float, float, float, float] | None" = None,
) -> Path:
    """RAW를 읽어 보정을 적용하고 저장합니다.

    프리뷰 기반이라 빠르지만 RAW의 관용도를 다 쓰지는 못합니다. 큰 폭의
    노출 복구가 필요하면 Lightroom 쪽이 맞습니다.

    main_face_box를 넘기면 얼굴 마스크의 '주 피사체'가 화면에서 본 그 얼굴에
    걸립니다. 안 넘기면 저장본에서만 다른 얼굴로 갈 수 있습니다.
    """
    from ..raw_io import (
        load_demosaiced,
        load_preview,
        read_metadata,
        read_white_balance,
        resize_long_edge,
    )

    # 보정 화면과 같은 베이스라인(디모자이크)을 써야 화면과 결과가 일치합니다.
    # 내장 JPEG은 카메라 픽처스타일이 구워져 있어 보정값의 의미가 달라집니다.
    try:
        image = load_demosaiced(source)
    except Exception as exc:  # noqa: BLE001 - 디모자이크 실패 시 JPEG으로 폴백
        # 조용히 넘어가면 안 됩니다. 결과는 나오지만 카메라 픽처스타일이
        # 구워진 JPEG에서 나온 것이라 색·계조가 RAW 현상과 다릅니다.
        # 실측: Nikon Z9의 고효율 압축 NEF는 LibRaw 0.22가 못 풉니다.
        log.warning(
            "%s: RAW를 현상하지 못해 내장 JPEG으로 내보냅니다 "
            "(색·계조가 RAW 현상과 다릅니다) — %s",
            source.name, exc,
        )
        image = load_preview(source)
    if long_edge:
        image = resize_long_edge(image, long_edge)

    # 정보 띠·EXIF 삽입과 **렌즈 자동 보정**에 필요합니다.
    #
    # 자동 보정을 빠뜨렸던 적이 있습니다. lensfun은 기종과 렌즈 이름으로
    # 프로필을 찾으므로 메타데이터가 없으면 조용히 원본을 돌려줍니다
    # (optics.apply_auto_correction). 그래서 화면에서는 보정이 걸리는데
    # 내보낸 파일에는 안 걸리고, 정보 띠를 켜 두면 그제서야 걸렸습니다 —
    # 켜고 끄는 것과 아무 상관이 없어 보이는 조합이라 알아채기 어렵습니다.
    metadata = None
    if settings.exif_strip.is_active() or settings.optics.auto_enabled:
        try:
            metadata = read_metadata(source)
        except Exception:  # noqa: BLE001
            metadata = None

    # 절대 색온도를 쓰는 경우에만 WB를 읽습니다 (미리보기와 결과를 맞춥니다)
    wb = None
    if settings.basic.temperature > 0:
        reference = read_white_balance(source)
        wb = reference.engine_wb if reference else None

    result = apply_settings(image, settings, source, metadata, wb=wb,
                            main_face_box=main_face_box)
    destination.parent.mkdir(parents=True, exist_ok=True)

    params = []
    suffix = destination.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        params = [cv2.IMWRITE_JPEG_QUALITY, int(quality)]
    elif suffix == ".png":
        # PNG는 0~9 압축 레벨이라 품질 값을 뒤집어 매핑합니다
        params = [cv2.IMWRITE_PNG_COMPRESSION, max(0, min(9, 9 - int(quality / 11)))]
    elif suffix in (".webp",):
        params = [cv2.IMWRITE_WEBP_QUALITY, int(quality)]
    elif suffix in (".tif", ".tiff"):
        # 무손실 압축. 품질 슬라이더는 의미가 없어 무시합니다 — 압축을
        # 안 걸면 32MP 한 장이 100MB에 육박합니다.
        params = [cv2.IMWRITE_TIFF_COMPRESSION, 8]  # 8 = Adobe Deflate

    # cv2.imwrite는 한글 경로에 실패하므로 유니코드 안전 헬퍼를 씁니다.
    from ..raw_io import imwrite_unicode

    if not imwrite_unicode(destination, result, params):
        raise OSError(f"저장 실패: {destination}")

    # EXIF는 JPEG에만 넣을 수 있습니다
    if settings.metadata.enabled and suffix in (".jpg", ".jpeg"):
        from .metadata import write_metadata

        write_metadata(source, destination, settings.metadata)

    return destination
