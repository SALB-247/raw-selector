"""카메라 룩 매칭 — 내장 JPEG을 정답지로 중립 현상을 근접시키는 시작점.

보정창의 베이스는 중립 디모자이크(+표준 프로파일)라, 카메라가 구워 낸
내장 JPEG(픽처스타일·톤매핑)과 상당히 다릅니다. 여기서는 그 차이를
①노출 ②루마 분위수 커브 ③채도 스칼라로 피팅해(연구
tools/research/research_camera_look.py 의 수학 그대로), 결과를 **앱의
실제 보정값**으로 기록합니다:

  - 노출     → BasicSettings.exposure (슬라이더 정밀도 0.01EV로 양자화)
  - 커브     → CurveSettings 파라메트릭 4값(하이라이트/라이트/다크/섀도),
               표현력이 모자라면 포인트 곡선(points_rgb)으로 폴백
  - 채도     → BasicSettings.saturation

LUT를 몰래 끼워 넣지 않고 설정값으로 적는 이유는 화면=결과 보장입니다 —
슬라이더에 그대로 보이고, 프리셋 저장·일괄 적용·내보내기 모두 같은 값을
읽습니다.

실측 근거 (RESEARCH_METADATA.md 9절·9-1절, 총 1,035장):
  - 루마 MAE 21.5→8.9 (다양한 장면 31장) / 15.2→10.6 (콘서트 위주 1,004장)
  - 제어점 4개 양자화가 256단 LUT와 사실상 동급 (10.9 vs 10.6)
  - 채널별 RGB 커브는 루마 커브보다 낫지 않음 (8.9 vs 8.9, 채도는 악화)
  - 피팅 비용 7ms/장 — 보정창을 열 때마다 즉석 피팅해도 됩니다
  - 하이라이트 클리핑↔잔여 상관 r=-0.02 — 천장 경고는 불요

잔여 오차의 근원은 카메라의 국소 톤매핑(DRO류)으로, 전역 커브의 본질적
한계입니다. 이 기능은 '가장 비슷한 시작점'이지 완전 재현이 아닙니다.
"""

from __future__ import annotations

from dataclasses import replace

import cv2
import numpy as np

from .settings import BasicSettings, CurveSettings, DevelopSettings

SIZE = 256
"""피팅·평가 해상도(긴변). 연구와 같은 값 — 룩은 저주파 현상이라 이보다
키워도 결과가 달라지지 않고, 이 크기라야 열 때마다 피팅해도 공짜입니다."""

POINTS = 16
"""분위수 커브 제어점 수(연구와 동일). 최종적으로 앱 값에 양자화되므로
여기서의 표본 수는 피팅 안정성만 좌우합니다."""

EXPOSURE_DECIMALS = 2
"""노출 기록 자릿수. 슬라이더(QDoubleSpinBox decimals=2)가 이 정밀도라,
더 곱게 계산해 봐야 화면에 올리는 순간 반올림됩니다 — 화면=결과를
지키려면 계산 단계에서 먼저 양자화해야 합니다."""

PARAMETRIC_MAX_ERR = 2.5
"""파라메트릭 4값 근사를 받아들이는 상한 (가중 평균 오차, 8비트 레벨).

이보다 나쁘면 포인트 곡선으로 폴백합니다. 연구 실측에서 4점 양자화의
전체 잔여가 256단 대비 +0.3에 그쳤으므로, LUT 근사 오차가 이 수준이면
이미지 잔여에는 사실상 차이가 없습니다."""

_CURVE_SAMPLE_XS = (0, 4, 10, 22, 40, 64, 96, 136, 192, 255)
"""포인트 곡선 폴백에서 LUT를 표본화할 입력 위치.

섀도 쪽이 촘촘한 이유: 카메라 룩의 급한 굴곡은 토(toe) 리프트에 몰려
있고, 균등 간격(32씩)은 그 구간에서 스플라인이 평균 2.4레벨을 빗나갔
습니다(감마 0.45 실측). 이 격자는 같은 곡선을 평균 0.4레벨로 통과합니다.
더 촘촘히 찍으면 곡선 편집기에 점이 바글거려 사용자가 손대기 어렵습니다."""


# ------------------------------------------------------------------ 피팅 원형
# (연구 스크립트 fit_look의 수학을 그대로 옮긴 것. 여기가 기준 구현이고
#  연구 쪽은 재현용 사본으로 남습니다.)


def _luma(bgr: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)[..., 0].astype(np.float32)


def _chroma(bgr: np.ndarray) -> float:
    ycc = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb).astype(np.float32)
    return float(np.abs(ycc[..., 1:] - 128.0).mean())


def _small(bgr: np.ndarray) -> np.ndarray:
    height, width = bgr.shape[:2]
    scale = SIZE / max(height, width)
    if scale >= 1.0:
        return bgr
    return cv2.resize(bgr, (max(8, int(width * scale)),
                            max(8, int(height * scale))),
                      interpolation=cv2.INTER_AREA)


def _pair(render: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """둘 다 평가 해상도로 줄이고 모양을 맞춥니다.

    내장 JPEG은 센서와 종횡비가 미세하게 다를 수 있습니다(여백 크롭 등).
    화소 단위 정합이 아니라 분위수·평균 통계만 쓰므로 강제 리사이즈로
    충분합니다 — 연구도 같은 방식으로 쟀습니다.
    """
    render_s, target_s = _small(render), _small(target)
    if render_s.shape != target_s.shape:
        target_s = cv2.resize(target_s, (render_s.shape[1], render_s.shape[0]))
    return render_s, target_s


def fit_look(render: np.ndarray, target: np.ndarray) -> dict:
    """중립 현상(render)을 target(내장 JPEG)에 근접시키는 원 파라미터.

    반환: {"exposure": EV, "lut": float32[256], "saturation": 배율}.
    lut는 노출 적용 **후**의 루마에 대한 매핑입니다.
    """
    render_s, target_s = _pair(render, target)
    luma_r, luma_t = _luma(render_s), _luma(target_s)

    # ① 노출: 중앙값 로그비 (극단 클리핑에 둔감)
    exposure = float(np.log2((np.median(luma_t) + 1) / (np.median(luma_r) + 1)))
    luma_r2 = np.clip(luma_r * (2.0 ** exposure), 0, 255)

    # ② 루마 분위수 커브: 같은 분위수끼리 짝지어 단조 LUT
    quantiles = np.linspace(0.02, 0.98, POINTS)
    src = np.quantile(luma_r2, quantiles)
    dst = np.quantile(luma_t, quantiles)
    src = np.maximum.accumulate(np.concatenate([[0.0], src, [255.0]]))
    dst = np.maximum.accumulate(np.concatenate([[0.0], dst, [255.0]]))
    lut = np.interp(np.arange(256), src, dst).astype(np.float32)

    # ③ 채도: 커브 적용 후 크로마 비
    matched = apply_look(render_s, {"exposure": exposure, "lut": lut,
                                    "saturation": 1.0})
    chroma_ratio = (_chroma(target_s) + 1e-6) / (_chroma(matched) + 1e-6)
    return {"exposure": exposure, "lut": lut,
            "saturation": float(np.clip(chroma_ratio, 0.4, 2.5))}


def apply_look(bgr: np.ndarray, look: dict) -> np.ndarray:
    """연구용 룩 적용(YCrCb 공간). 합성 검증과 채도 피팅에만 씁니다.

    제품 렌더는 이걸 쓰지 않습니다 — 설정값으로 기록해 engine.apply_settings
    가 그리는 것이 최종이고, 여기와의 잔차는 match_settings가 채도 단계에서
    실제 엔진 응답으로 흡수합니다.
    """
    ycc = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb).astype(np.float32)
    luma = np.clip(ycc[..., 0] * (2.0 ** look["exposure"]), 0, 255)
    ycc[..., 0] = np.interp(luma, np.arange(256), look["lut"])
    ycc[..., 1:] = np.clip(
        (ycc[..., 1:] - 128.0) * look["saturation"] + 128.0, 0, 255)
    return cv2.cvtColor(ycc.astype(np.uint8), cv2.COLOR_YCrCb2BGR)


def score(render: np.ndarray, target: np.ndarray) -> tuple[float, float]:
    """(루마 MAE, 채도 MAE) — 낮을수록 비슷. 연구와 같은 자입니다."""
    render_s, target_s = _pair(render, target)
    luma = float(np.abs(_luma(render_s) - _luma(target_s)).mean())
    ycc_r = cv2.cvtColor(render_s, cv2.COLOR_BGR2YCrCb).astype(np.float32)
    ycc_t = cv2.cvtColor(target_s, cv2.COLOR_BGR2YCrCb).astype(np.float32)
    chroma = float(np.abs(ycc_r[..., 1:] - ycc_t[..., 1:]).mean())
    return luma, chroma


# ------------------------------------------------------------------ LUT → 앱 값


def _weights(render_s: np.ndarray, exposure: float) -> np.ndarray:
    """LUT 근사 오차의 가중치 — 노출 적용 후 루마 히스토그램.

    LUT 256칸을 똑같이 취급하면 화소가 하나도 없는 계조 구간의 오차가
    피팅을 끌고 갑니다. 이미지 잔여(MAE)를 줄이는 것이 목적이므로 화소가
    실제로 놓인 곳을 세게 봅니다. 바닥값을 깔아 빈 구간도 완전히 버리지는
    않습니다 — 같은 커브가 노출이 조금 다른 옆 컷에도 이식되기 때문입니다.
    """
    luma = np.clip(_luma(render_s) * (2.0 ** exposure), 0, 255)
    hist = np.bincount(luma.astype(np.int64).ravel(), minlength=256).astype(np.float64)
    hist = hist / max(hist.sum(), 1.0)
    hist += 1.0 / 1024.0
    return (hist / hist.sum()).astype(np.float64)


def _parametric_error(amounts: tuple[int, int, int, int], lut: np.ndarray,
                      weights: np.ndarray) -> float:
    """파라메트릭 4값이 만든 곡선과 목표 LUT의 가중 평균 오차(레벨)."""
    from .engine import parametric_tone_lut

    shadows, darks, lights, highlights = amounts
    approx = parametric_tone_lut(shadows, darks, lights, highlights)
    return float((np.abs(approx.astype(np.float64) - lut) * weights).sum())


def fit_parametric(lut: np.ndarray,
                   weights: np.ndarray | None = None) -> tuple[int, int, int, int]:
    """목표 LUT에 가장 가까운 앱 파라메트릭 4값 (섀도/다크/라이트/하이라이트).

    엔진의 실제 응답(engine.parametric_tone_lut)에 맞춥니다. 응답은 구간별
    가우시안을 **순차** 적용하므로 엄밀히는 비선형이지만, 항등 기준으로
    선형화한 최소자승이 좋은 출발점이고, 그 위에서 정수 좌표 하강(구간별
    삼분 탐색)으로 실제 응답 기준의 최적을 찾습니다. 반환값은 슬라이더
    범위(-100~100)의 정수라 그대로 화면에 올라갑니다.
    """
    from .engine import (
        _PARAMETRIC_STRENGTH,
        _PARAMETRIC_WIDTH,
        PARAMETRIC_REGIONS,
    )

    lut = np.asarray(lut, dtype=np.float64)
    if weights is None:
        weights = np.full(256, 1.0 / 256.0)

    # ── 선형화 초기값: identity + Σ (amount/100)·강도·가우시안 ≈ lut
    x = np.arange(256, dtype=np.float64) / 255.0
    basis = np.stack([
        _PARAMETRIC_STRENGTH * np.exp(-((x - center) ** 2)
                                      / (2 * _PARAMETRIC_WIDTH ** 2)) * 255.0
        for _name, center in PARAMETRIC_REGIONS
    ], axis=1)                                    # (256, 4) — 섀도·다크·라이트·하이라이트 순
    delta = lut - np.arange(256, dtype=np.float64)
    w_col = np.sqrt(weights)[:, None]
    solution, *_ = np.linalg.lstsq(basis * w_col, delta * w_col[:, 0], rcond=None)
    amounts = [int(np.clip(round(v * 100.0), -100, 100)) for v in solution]

    # ── 정수 좌표 하강: 실제 응답 기준. 한 좌표의 오차 곡선은 실측상
    #    단봉이라 삼분 탐색이 통하고, 혹시 모를 평평한 바닥은 마지막
    #    국소 스캔(±2)이 정리합니다. 삼분 탐색과 국소 스캔이 같은 값을
    #    거듭 두드리므로 평가를 캐시합니다 — 보정창을 열 때마다 도는
    #    코드라 낭비가 그대로 대기 시간이 됩니다.
    cache: dict[tuple[int, int, int, int], float] = {}

    def err_at(index: int, value: int) -> float:
        candidate = list(amounts)
        candidate[index] = value
        key = tuple(candidate)
        found = cache.get(key)
        if found is None:
            found = cache[key] = _parametric_error(key, lut, weights)
        return found

    for _round in range(3):
        changed = False
        for index in range(4):
            low, high = -100, 100
            while high - low > 2:
                third = (high - low) // 3
                mid1, mid2 = low + third, high - third
                if err_at(index, mid1) <= err_at(index, mid2):
                    high = mid2
                else:
                    low = mid1
            best_value = amounts[index]
            best_err = err_at(index, best_value)
            for value in range(low - 2, high + 3):
                value = int(np.clip(value, -100, 100))
                candidate_err = err_at(index, value)
                if candidate_err < best_err - 1e-9:
                    best_err, best_value = candidate_err, value
            if best_value != amounts[index]:
                amounts[index] = best_value
                changed = True
        if not changed:
            break
    return tuple(amounts)  # type: ignore[return-value]


def lut_to_curve_points(lut: np.ndarray) -> tuple[tuple[int, int], ...]:
    """LUT를 곡선 편집기 포인트로 표본화합니다 (파라메트릭 폴백용).

    입력 x는 고정 격자를 씁니다 — 분위수 자리를 쓰면 컷마다 점 위치가
    널뛰어 곡선 편집기에서 비교가 안 됩니다. 단조 LUT + 단조 스플라인
    조합이라 이 간격이면 레벨 미만으로 통과합니다.
    """
    lut = np.asarray(lut, dtype=np.float64)
    points = []
    last_x = None
    for x in _CURVE_SAMPLE_XS:
        if x == last_x:
            continue
        y = int(np.clip(round(float(np.interp(x, np.arange(256), lut))), 0, 255))
        points.append((int(x), y))
        last_x = x
    return tuple(points)


# ------------------------------------------------------------------ 제품 진입점


def curve_for_lut(lut: np.ndarray, weights: np.ndarray,
                  base_curve: CurveSettings) -> CurveSettings:
    """피팅 LUT를 앱 커브 설정으로 — 파라메트릭 우선, 모자라면 포인트.

    파라메트릭 4값이 기본인 이유: 슬라이더에 그대로 보여서 사용자가 이어서
    만지기 쉽고, 연구 실측에서 256단 LUT와 사실상 동급(10.9 vs 10.6)이었기
    때문입니다. 다만 파라메트릭 응답은 구간당 진폭이 ±0.22로 묶여 있어
    급한 굴곡은 못 담습니다 — 그때 조용히 눌러 담으면 '매칭했는데 안
    비슷한' 상태가 되므로 포인트 곡선으로 넘어갑니다. 어느 쪽이든 곡선
    편집기가 그대로 보여 주는 값입니다.

    base_curve의 채널별 곡선(R/G/B 포인트)은 매칭 소유가 아니라 그대로
    지나갑니다.
    """
    parametric = fit_parametric(lut, weights)
    if _parametric_error(parametric, lut, weights) <= PARAMETRIC_MAX_ERR:
        return replace(base_curve,
                       shadows=parametric[0], darks=parametric[1],
                       lights=parametric[2], highlights=parametric[3],
                       points_rgb=())
    return replace(base_curve,
                   shadows=0, darks=0, lights=0, highlights=0,
                   points_rgb=lut_to_curve_points(lut))


def match_settings(
    render: np.ndarray,
    target: np.ndarray,
    base: DevelopSettings | None = None,
) -> DevelopSettings:
    """중립 현상 render를 내장 JPEG target에 근접시키는 DevelopSettings.

    render는 보정창 베이스(디모자이크+프로파일)의 8비트 BGR, target은
    load_preview 결과입니다. base를 주면 그 설정에서 **노출·채도·톤 곡선만**
    바꾼 사본을 돌려줍니다 — 디테일·마스크·크롭 등 다른 편집은 그대로
    둡니다(원클릭 버튼이 기존 편집을 지우면 안 됩니다).

    채도는 연구처럼 YCrCb 근사가 아니라 **실제 엔진 렌더**(apply_settings)
    위에서 잽니다. 엔진은 커브를 채널별로 적용해 크로마가 함께 움직이므로,
    같은 채도값이라도 YCrCb 근사와 결과가 다릅니다 — 화면에 나올 그
    경로에서 재야 화면=결과가 맞습니다.
    """
    from .engine import apply_settings

    base = base or DevelopSettings()
    render_s, target_s = _pair(render, target)

    fitted = fit_look(render_s, target_s)
    exposure = float(np.clip(round(fitted["exposure"], EXPOSURE_DECIMALS),
                             -5.0, 5.0))

    weights = _weights(render_s, exposure)
    curve = curve_for_lut(fitted["lut"], weights, base.curve)

    # 채도는 톤을 확정한 뒤 실제 엔진 응답에서 잽니다. 톤·채도만 넣은
    # 벌거벗은 설정을 쓰는 이유: base의 크롭·마스크·정보 띠가 끼면 작은
    # 비교 이미지가 잘리거나 덧그려져 측정 자체가 깨집니다.
    tone_only = DevelopSettings(
        basic=BasicSettings(exposure=exposure),
        curve=CurveSettings(
            highlights=curve.highlights, lights=curve.lights,
            darks=curve.darks, shadows=curve.shadows,
            points_rgb=curve.points_rgb,
        ),
    )
    toned = apply_settings(render_s, tone_only)
    ratio = (_chroma(target_s) + 1e-6) / (_chroma(toned) + 1e-6)
    saturation = int(np.clip(round((ratio - 1.0) * 100.0), -100, 100))

    return replace(
        base,
        basic=replace(base.basic, exposure=exposure, saturation=saturation),
        curve=curve,
    )
