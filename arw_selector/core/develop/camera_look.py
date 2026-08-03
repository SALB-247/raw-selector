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
    from .engine import apply_exposure_with_shoulder, to_light

    render_s, target_s = _pair(render, target)
    luma_r, luma_t = _luma(render_s), _luma(target_s)

    # ① 노출: 중앙값 로그비 (극단 클리핑에 둔감).
    #
    # 비를 **엔진이 곱하는 그 공간에서** 잡습니다. 우리가 찾는 것은
    # "engine.apply_exposure가 render의 표시값을 target의 표시값으로
    # 옮기려면 광량에 얼마를 곱해야 하는가"이므로, 두 값 모두 엔진의
    # 전달함수(to_light)로 되돌려야 합니다. 예전에는 sRGB로 되돌렸는데,
    # render는 postprocess·기종보정·프로파일 곡선을 지난 값이라 sRGB가
    # 아닙니다 — 슬라이더에 찍히는 값이 0.24~0.81 EV 어긋났습니다(실측).
    #
    # target을 그 출신(카메라 JPEG = 진짜 sRGB)대로 sRGB로 되돌리는 것은
    # **틀립니다.** 같은 그림을 자기 자신에 맞추면 노출이 0이어야 하는데
    # 두 공간이 갈려 0.59가 나옵니다. 목표는 그저 도달할 표시값입니다.
    #
    # 노출이 커지면 노출 단계에서 255에 붙는 화소가 0.76%에서 2.25%로
    # 늘어나 하이라이트를 잃는 것처럼 보입니다. 그런데 **최종 계조를 세어
    # 보면 반대**입니다 — 목표에서 밝은 10% 구간에 남는 고유 레벨이
    # 121.6에서 125.4로 늘어납니다. 255에 붙는 그 화소들은 어차피 흰색에
    # 가깝던 쪽이고, 노출을 제대로 올리면 나머지가 커브의 촘촘한 구간에
    # 얹히기 때문입니다. 중간 단계의 클립 비율로 판단하면 안 됩니다.
    lin_r = float(to_light(np.median(luma_r)))
    lin_t = float(to_light(np.median(luma_t)))
    exposure = float(np.log2((lin_t + 1e-4) / (lin_r + 1e-4)))
    # 렌더 경로가 노출 뒤에 하이라이트 어깨를 겁니다(_tone_lut). 여기서
    # 순수한 곱만 쓰면 아래 분위수 커브가 어깨를 모르는 밝기 위에서 맞춰져,
    # 이 파일이 내세운 "피팅과 렌더가 같은 연산을 써야 한다"가 다시 깨집니다.
    luma_r2 = np.clip(apply_exposure_with_shoulder(luma_r, exposure), 0, 255)

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
    from .engine import apply_exposure

    ycc = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb).astype(np.float32)
    luma = np.clip(apply_exposure(ycc[..., 0], look["exposure"]), 0, 255)
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
    from .engine import apply_exposure

    luma = np.clip(apply_exposure(_luma(render_s), exposure), 0, 255)
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


def _wb_log_ratios(mean_bgr: np.ndarray) -> tuple[float, float]:
    """표시값 채널 평균 → 선형 → (log R/G, log B/G)."""
    from .engine import srgb_to_linear

    lin = srgb_to_linear(np.maximum(np.asarray(mean_bgr, np.float64), 1.0)
                         / 255.0)
    return float(np.log(lin[2] / lin[1])), float(np.log(lin[0] / lin[1]))


def _wb_means(render: np.ndarray, target: np.ndarray):
    """비교에 쓸 (target, render) 채널 평균.

    무채색 후보가 있으면 그것을(조명 색에 안 휘둘림), 없으면 전체 평균을
    씁니다 — 색조명 장면(이자카야 LED 등)은 무채색이 아예 없는데, 하필
    그런 컷이 이 피팅을 가장 필요로 합니다.
    """
    from .calibration import _neutral_means

    pair = _neutral_means(target, np.clip(render, 0, 255).astype(np.uint8))
    if pair is not None:
        return pair
    return (target.reshape(-1, 3).mean(axis=0),
            np.clip(render, 0, 255).reshape(-1, 3).mean(axis=0))


def _wb_gap(render: np.ndarray, target: np.ndarray) -> float:
    """두 그림의 색 균형 차이 — log(R/G)·log(B/G) 절대합."""
    t_mean, r_mean = _wb_means(render, target)
    want, got = _wb_log_ratios(t_mean), _wb_log_ratios(r_mean)
    return abs(got[0] - want[0]) + abs(got[1] - want[1])


CHANNEL_POINTS = 8
"""채널별 잔차 곡선의 분위수 표본 수. 잔차용이라 성글게 잡습니다."""

CHANNEL_MAX_SHIFT = 12.0
"""채널 곡선이 움직일 수 있는 최대 레벨. 분위수 대응이 폭주하는 것만
막습니다 — 색이 나빠지는 쪽은 스코어 판정(match_settings)이 잡습니다."""

CHANNEL_MIN_GAIN = 0.05
"""채널 곡선 채택에 요구하는 최소 상대 개선. 여기 스코어는 작은 근사
렌더(WB 근사·광학 보정 없음)로 재는데, 실제 정착 경로(재디모자이크·광학
포함)로 넘어가면 아슬아슬한 이득은 뒤집힐 수 있습니다 — 실측: 내부 +2~3%
이득이던 P1032946이 정착 렌더에서 -2% 손해로 반전. 진짜 수혜 컷은 내부
-21~-30%라 5% 문턱과는 한 자릿수 차이로 떨어져 있습니다."""


def _fit_channel_curve(render_ch: np.ndarray, target_ch: np.ndarray) -> tuple:
    """한 채널의 잔차를 분위수 대응으로 — 편집기 좌표 점들. 없으면 ()."""
    quantiles = np.linspace(0.03, 0.97, CHANNEL_POINTS)
    src = np.quantile(render_ch, quantiles)
    dst = np.clip(np.quantile(target_ch, quantiles),
                  src - CHANNEL_MAX_SHIFT, src + CHANNEL_MAX_SHIFT)
    xs = np.clip(np.round(src), 1, 254)
    ys = np.clip(np.round(dst), 0, 255)
    points, seen = [], set()
    for x, y in zip(xs, ys):
        if int(x) in seen:
            continue
        seen.add(int(x))
        points.append((int(x), int(y)))
    if not points or max(abs(y - x) for x, y in points) < 1.5:
        return ()                      # 잔차가 반올림 수준 — 항등으로 둡니다
    return ((0, 0), *points, (255, 255))


def _apply_matched_wb(render: np.ndarray, kelvin: int, tint: int,
                      wb) -> np.ndarray:
    """(색온도, 색조)가 **정착 후** 실제로 만드는 그림을 예측합니다.

    슬라이더가 놓이면 색온도는 재디모자이크로 **선형에서** 걸리고
    (raw_io.load_demosaiced의 앵커 배수), 색조는 표시값 G 곱으로
    남습니다(engine._apply_white_balance). 피팅의 검증·사전 적용이 이
    조합과 다른 공간을 쓰면 — 처음에 드래그용 근사(전부 감마 곱)를 썼다가
    왕복 테스트가 잡았습니다: 켈빈 3000을 건 목표에서 2200·틴트 -87이
    나왔습니다. 감마에 곱한 게인을 선형 가정으로 읽으면 2.4승으로
    부풀기 때문입니다.
    """
    from ..raw_io import _estimate_as_shot_kelvin
    from .engine import _kelvin_to_rgb, linear_to_srgb, srgb_to_linear

    camera = np.array(wb[0][:3], dtype=np.float64)
    daylight = np.array(wb[1][:3], dtype=np.float64)
    est = _estimate_as_shot_kelvin(tuple(camera), tuple(daylight))
    gain = _kelvin_to_rgb(float(est)) / _kelvin_to_rgb(float(kelvin))
    gain = gain / gain[1]

    linear = srgb_to_linear(np.clip(render, 0, 255).astype(np.float64) / 255.0)
    linear[..., 2] *= gain[0]                 # R
    linear[..., 0] *= gain[2]                 # B
    out = linear_to_srgb(np.clip(linear, 0.0, 1.0)) * 255.0
    if tint:
        out[..., 1] *= 1.0 - tint / 100.0 * 0.18
    return np.clip(out, 0, 255).astype(np.float32)


def _kelvin_working(working: np.ndarray, kelvin: int, wb) -> np.ndarray:
    """작업 공간 float에 앵커 켈빈 게인을 미리 겁니다 (정착의 근사).

    정착은 센서 선형(색 행렬 앞)에 배수를 걸지만, 여기서는 행렬 뒤의 작업
    선형에 같은 배수를 겁니다 — 앵커(추정 켈빈) 근처의 작은 게인에서는
    차이가 작고, 채택은 어차피 실제 렌더 스코어로 판정하므로 근사가 나쁘면
    그대로 기각됩니다. 작업 공간 전달함수는 sRGB 곡선입니다(Melissa).
    """
    from ..raw_io import _estimate_as_shot_kelvin
    from .engine import _kelvin_to_rgb, linear_to_srgb, srgb_to_linear

    camera = np.array(wb[0][:3], dtype=np.float64)
    daylight = np.array(wb[1][:3], dtype=np.float64)
    est = _estimate_as_shot_kelvin(tuple(camera), tuple(daylight))
    gain = _kelvin_to_rgb(float(est)) / _kelvin_to_rgb(float(kelvin))
    gain = gain / gain[1]

    linear = srgb_to_linear(np.clip(working, 0, 255).astype(np.float64)
                            / 255.0)
    linear[..., 2] *= gain[0]                 # R
    linear[..., 0] *= gain[2]                 # B
    return (linear_to_srgb(np.clip(linear, 0.0, 1.0))
            * 255.0).astype(np.float32)


def fit_white_balance(render: np.ndarray, target: np.ndarray,
                      wb) -> tuple[int, int] | None:
    """render의 색 균형을 target에 맞추는 (색온도, 색조). 못 맞추면 None.

    채널 비(R/G, B/G)를 목표로 앵커 모델(camera × K(추정)/K(t))의 켈빈과,
    켈빈 축에 없는 초록-마젠타 성분을 색조로 역산합니다. 앵커 공식이라
    "temperature=t"가 렌더에 주는 선형 게인이 정확히 K(추정)/K(t)이고,
    그 예측 위에서 2축을 2변수로 풉니다.

    **개선될 때만 답을 냅니다.** 색 차이에는 켈빈·색조 축 밖의 성분(제조사
    색 렌더)도 섞여 있어서, 억지로 맞추면 한 축을 줄이며 다른 축을
    키웁니다 — 실측에서 파나소닉 컷의 R/G가 2.0%에서 4.9%로 나빠졌습니다.
    맞춘 결과의 색 균형 차이가 10% 이상 줄지 않으면 None을 돌려주고,
    호출자는 기존 값을 둡니다.
    """
    from ..raw_io import _estimate_as_shot_kelvin
    from .engine import _kelvin_to_rgb, linear_to_srgb

    if wb is None:
        return None
    camera = np.array(wb[0][:3], dtype=np.float64)
    daylight = np.array(wb[1][:3], dtype=np.float64)
    if camera[1] <= 0 or daylight[1] <= 0:
        return None

    t_mean, r_mean = _wb_means(render, target)
    want = _wb_log_ratios(t_mean)
    got = _wb_log_ratios(r_mean)
    want_rg, want_bg = want[0] - got[0], want[1] - got[1]

    est = _estimate_as_shot_kelvin(tuple(camera), tuple(daylight))
    anchor = _kelvin_to_rgb(float(est))

    best = None
    for kelvin in range(2000, 12001, 25):
        gain = anchor / _kelvin_to_rgb(float(kelvin))
        model_rg = float(np.log(gain[0] / gain[1]))
        model_bg = float(np.log(gain[2] / gain[1]))
        # 색조(G만 곱함)는 (log R/G, log B/G) 공간에서 (+d, +d) 방향입니다
        delta = ((want_rg - model_rg) + (want_bg - model_bg)) / 2.0
        residual = ((want_rg - model_rg - delta) ** 2
                    + (want_bg - model_bg - delta) ** 2)
        if best is None or residual < best[0]:
            best = (residual, kelvin, delta)

    _, kelvin, delta = best
    # delta = 선형 G 공통 성분. tint의 정의는 **표시값** G 게인
    # (1 - 0.18·tint/100)이므로 중간 회색에서 정확히 환산합니다.
    grey = 0.18
    disp_gain = float(linear_to_srgb(np.float64(grey * np.exp(-delta)))
                      / linear_to_srgb(np.float64(grey)))
    tint = int(np.clip(round((1.0 - disp_gain) * 100.0 / 0.18), -100, 100))

    adjusted = _apply_matched_wb(render, int(kelvin), tint, wb)
    if _wb_gap(adjusted, target) >= _wb_gap(render, target) * 0.9:
        return None
    return int(kelvin), tint


def match_settings(
    render: np.ndarray,
    target: np.ndarray,
    base: DevelopSettings | None = None,
    wb=None,
    working: np.ndarray | None = None,
) -> DevelopSettings:
    """중립 현상 render를 내장 JPEG target에 근접시키는 DevelopSettings.

    render는 보정창 베이스(디모자이크+프로파일)의 8비트 BGR, target은
    load_preview 결과입니다. base를 주면 그 설정에서 **색온도·색조·노출·
    채도·톤 곡선만** 바꾼 사본을 돌려줍니다 — 디테일·마스크·크롭 등 다른
    편집은 그대로 둡니다(원클릭 버튼이 기존 편집을 지우면 안 됩니다).

    working은 같은 컷의 **작업 공간 float**(보정창의 self._source)입니다.
    주면 피팅·검증 렌더를 전부 실제 화면 경로(작업 공간에 적용 →
    display=True로 sRGB 변환)로 돌립니다. 표시값 위에서 피팅·검증하면
    커브·채도가 실제로는 더 넓은 작업 공간에 걸리는 것과 어긋납니다 —
    실측으로 같은 설정이 두 공간에서 R/G 12%까지 다른 색을 만듭니다
    (채도 높은 컷일수록 큼). 없으면 예전처럼 표시값 위에서 피팅합니다.

    wb는 (camera_whitebalance, daylight_whitebalance)입니다. 주면 색 균형을
    먼저 맞춥니다(fit_white_balance) — 노출·커브·채도는 밝기와 크로마
    크기만 다루므로, 색 균형이 어긋난 상태로는 "맞추기를 눌러도 색이
    다르다"가 됩니다(실측: 이자카야 LED에서 R/G 8.1%·B/G 11.2% 어긋남이
    피팅으로 0.3%·1.2%가 됩니다). 색이 개선되지 않는 컷(제조사 색 렌더가
    지배)은 자동으로 건너뜁니다.

    채도는 연구처럼 YCrCb 근사가 아니라 **실제 엔진 렌더**(apply_settings)
    위에서 잽니다. 엔진은 커브를 채널별로 적용해 크로마가 함께 움직이므로,
    같은 채도값이라도 YCrCb 근사와 결과가 다릅니다 — 화면에 나올 그
    경로에서 재야 화면=결과가 맞습니다.
    """
    from ..raw_io import to_display
    from .engine import apply_settings

    base = base or DevelopSettings()
    if working is not None:
        # 실제 프레임: 렌더 비교 기준도 작업 이미지에서 파생시킵니다.
        # (전달된 render와 사실상 같지만, 한 원본에서 나와야 어긋날 수
        # 없습니다.)
        working_s = _small(np.clip(working, 0.0, 255.0).astype(np.float32))
        render_s = to_display(working_s)
        _, target_s = _pair(render_s, target)
    else:
        working_s = None
        render_s, target_s = _pair(render, target)

    def fit_tone(source: np.ndarray, source_working, tone_tint: int):
        """색 균형이 정해진 소스에서 노출·커브·채도를 피팅합니다.

        source_working이 있으면 렌더는 실제 화면 경로(작업 공간 적용 후
        sRGB 변환)를 씁니다. tone_tint는 그 렌더에 함께 태우는 색조 —
        정착 화면도 색조를 엔진에서 겁니다.
        """
        def real(applied: DevelopSettings) -> np.ndarray:
            if source_working is None:
                return apply_settings(source, applied)
            return apply_settings(source_working, applied, display=True)

        fitted = fit_look(source, target_s)
        exposure = float(np.clip(round(fitted["exposure"], EXPOSURE_DECIMALS),
                                 -5.0, 5.0))
        weights = _weights(source, exposure)
        curve = curve_for_lut(fitted["lut"], weights, base.curve)

        # 채도는 톤을 확정한 뒤 실제 엔진 응답에서 잽니다. 톤·채도만 넣은
        # 벌거벗은 설정을 쓰는 이유: base의 크롭·마스크·정보 띠가 끼면 작은
        # 비교 이미지가 잘리거나 덧그려져 측정 자체가 깨집니다.
        tone_only = DevelopSettings(
            basic=BasicSettings(exposure=exposure, tint=tone_tint),
            curve=CurveSettings(
                highlights=curve.highlights, lights=curve.lights,
                darks=curve.darks, shadows=curve.shadows,
                points_rgb=curve.points_rgb,
            ),
        )
        toned = real(tone_only)
        ratio = (_chroma(target_s) + 1e-6) / (_chroma(toned) + 1e-6)
        saturation = int(np.clip(round((ratio - 1.0) * 100.0), -100, 100))
        rendered = real(replace(tone_only,
                                basic=replace(tone_only.basic,
                                              saturation=saturation)))
        luma_err, chroma_err = score(rendered, target_s)
        return exposure, curve, saturation, luma_err + chroma_err, rendered

    # 색 균형을 먼저 맞추고, 노출·커브·채도는 그 위에서 잽니다. 실제
    # 화면도 같은 순서입니다(화이트밸런스 → 톤). 사전 적용은 정착 후
    # 실제와 같은 공간을 씁니다 — 실제 프레임에서는 켈빈 게인을 작업
    # 선형에 걸고(_kelvin_working) 색조는 엔진 렌더에 태우며, 표시값
    # 폴백에서는 _apply_matched_wb를 씁니다.
    #
    # **채택은 최종 그림으로 판정합니다.** 색 균형 지표만 보면 무채색은
    # 좋아지는데 커브·채도까지 얹은 결과가 나빠지는 컷이 있습니다(실측
    # 파나소닉: 균형 지표는 개선인데 최종 B/G가 0.0% → 4.6%). 그래서 두
    # 후보(피팅 WB / 지금 WB)를 끝까지 피팅해 실제 엔진 렌더가 목표에 더
    # 가까운 쪽을 씁니다 — 채도를 엔진 응답에서 재는 것과 같은 원칙입니다.
    temperature = base.basic.temperature
    tint = base.basic.tint
    source, source_working = render_s, working_s
    cur_tint = tint if working_s is not None else 0
    exposure, curve, saturation, err, rendered = fit_tone(
        render_s, working_s, cur_tint)

    fitted_wb = fit_white_balance(render_s, target_s, wb)
    if fitted_wb is not None:
        if working_s is not None:
            cand_working = _kelvin_working(working_s, fitted_wb[0], wb)
            cand_display = to_display(cand_working)
            wb_fit = fit_tone(cand_display, cand_working, fitted_wb[1])
        else:
            cand_working = None
            cand_display = np.clip(
                _apply_matched_wb(render_s, fitted_wb[0], fitted_wb[1], wb),
                0, 255).astype(np.uint8)
            wb_fit = fit_tone(cand_display, None, 0)
        if wb_fit[3] < err:
            temperature, tint = fitted_wb
            exposure, curve, saturation, err, rendered = wb_fit
            source, source_working = cand_display, cand_working
            cur_tint = tint if working_s is not None else 0

    # 남은 색 잔차를 **채널별 곡선**으로 한 번 더 좁힙니다 — 승자 렌더와
    # 목표의 채널별 분위수 대응입니다(SIZE 렌더라 왕복이 ms 단위). 연구는
    # 채널별 커브를 기각했지만(채도 악화) 그때는 WB 선행도 스코어 판정도
    # 없었습니다. 지금은 실제 엔진 렌더의 스코어가 좋아질 때만 채택하므로
    # 그 우려를 판정이 직접 잡습니다 — 실측: 이자카야 LED 컷 합 8.87→7.02,
    # 형광 혼합 컷 12.05→9.52, 개선 없는 컷은 자동 기각.
    #
    # 두 번 반복은 무익했습니다(곡선을 누적이 아니라 대체하므로 늘 악화).
    #
    # base에 사용자가 넣어 둔 채널 곡선이 있으면 시도하지 않습니다 —
    # 채널 곡선은 매칭 소유가 아니라는 계약(curve_for_lut)이 우선입니다.
    if not (base.curve.points_red or base.curve.points_green
            or base.curve.points_blue):
        rendered_s, tgt_s = _pair(rendered, target_s)
        channelled = replace(
            curve,
            points_red=_fit_channel_curve(rendered_s[..., 2].ravel(),
                                          tgt_s[..., 2].ravel()),
            points_green=_fit_channel_curve(rendered_s[..., 1].ravel(),
                                            tgt_s[..., 1].ravel()),
            points_blue=_fit_channel_curve(rendered_s[..., 0].ravel(),
                                           tgt_s[..., 0].ravel()),
        )
        if channelled != curve:
            trial_settings = DevelopSettings(
                basic=BasicSettings(exposure=exposure, saturation=saturation,
                                    tint=cur_tint),
                curve=channelled)
            if source_working is None:
                trial = apply_settings(source, trial_settings)
            else:
                trial = apply_settings(source_working, trial_settings,
                                       display=True)
            if sum(score(trial, target_s)) < err * (1.0 - CHANNEL_MIN_GAIN):
                curve = channelled

    return replace(
        base,
        basic=replace(base.basic, exposure=exposure, saturation=saturation,
                      temperature=temperature, tint=tint),
        curve=curve,
    )
