"""점수 통합과 등급 판정.

가장 비싼 오류는 false reject다. 쓸 만한 컷을 버리면 사용자는 그 사실을
영영 모르지만, 애매한 컷이 review로 남는 건 육안으로 한 번 보면 끝납니다.
그래서 판정은 reject 쪽으로 보수적으로 기웁니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import FACE_BONUS_AREA_RANGE, ScoreConfig
from .types import FocusSource, Grade, ImageRecord

def roi_trust(source: FocusSource, config: ScoreConfig) -> float:
    """ROI 소스별로 roi_sharpness를 얼마나 신뢰할지.

    눈/얼굴을 잡았으면 그 안의 선명도가 곧 판정 근겁니다. 타일 추정은 주
    피사체가 아닐 수 있으므로 전체 프레임 쪽에 무게를 나눠 줍니다.
    """
    return {
        FocusSource.EYE: config.trust_eye,
        FocusSource.FACE: config.trust_face,
        FocusSource.TILE: config.trust_tile,
        FocusSource.FRAME: config.trust_frame,
    }.get(source, 0.5)


SHARPNESS_SCALE = 0.5
"""**얼굴 우선 모드에서** 선명도 항이 점수에서 차지하는 배율.

선명도만으로 0~100을 다 쓰면 보너스를 조금만 켜도 곧바로 100에 붙어
버립니다. 그러면 잘 찍은 컷들이 전부 같은 점수가 되어 순위가 사라집니다 —
셀렉터로서 가장 중요한 '이 중에 무엇이 나은가'를 못 하게 됩니다.

절반으로 눌러 선명도가 0~50을 쓰게 하고, 나머지 50을 얼굴·눈 신호가 쓸
자리로 남깁니다.

**이 값을 바꾸면 절대 점수 임계값(keep_above, reject_below 등)도 같이
움직여야 합니다.** 안 그러면 전부 reject가 됩니다.
"""

SHARPNESS_SCALE_NO_FACE = 1.0
"""얼굴 우선 모드를 **껐을 때**의 배율.

모드를 끄면 얼굴·눈 보너스와 감점이 전부 빠집니다. 그때도 0.5를 쓰면
선명도만으로 최대 50점이라 점수의 상단 절반이 통째로 빕니다 — 실측
(A6700 2845장)에서 최대 45.1점이라 keep 기준 65에 닿는 컷이 없습니다.

얼굴 신호가 쓰던 자리를 선명도가 도로 가져갑니다. 실측에서 최대 90.2점,
100점에 붙는 컷 0장으로 순위가 살아 있습니다.
"""

# 얼굴과 배경의 선명도 격차가 이 값(0~100 척도)에 이르면 감점/판정이 포화합니다.
_FACE_DEFOCUS_SCALE = 40.0

#: 얼굴 크기 가중이 실질적으로 0이 되는 지점 (기준 면적 대비 비율).
#:
#: 제곱근 곡선은 0에 닿기만 할 뿐 도중에 끊기지 않아서, 기준의 1/1000짜리
#: 얼굴도 3%쯤은 받아 갑니다. 수십 화소짜리 얼굴에 보너스를 조금이라도
#: 주는 것은 "받을 가치가 없다"는 판단과 어긋나므로 여기서 끊습니다.
#: 기준 5%면 0.05%, 즉 26MP에서 114×114 화소입니다.
_FACE_WEIGHT_CUTOFF = 0.01


def sanitized_config(config: ScoreConfig | None) -> ScoreConfig:
    """쓸 수 없는 설정값을 되돌린 사본. 판정 전에 반드시 한 번 지납니다.

    설정값이 GUI 스핀박스로만 오는 것이 아니라서 필요합니다. 판정 기준
    프리셋은 YAML이고 사용자가 직접 열어 고치거나 남에게 받아 씁니다.
    YAML은 `.nan`과 `.inf`를 그대로 float으로 읽어 오고, 위젯 범위 밖의
    숫자도 아무 저항 없이 통과합니다.

    걸러 두지 않으면 **분석이 다 끝난 뒤 등급을 매기는 순간** 무너집니다:

      - reject_percentile이 범위 밖이면 np.percentile이 ValueError를 던져
        4000장 분석 결과가 통째로 사라집니다.
      - NaN 가중치는 예외 없이 점수만 NaN으로 만듭니다. NaN은 어떤 비교에도
        False라 등급이 '아무 조건에도 안 걸린 것'으로 떨어집니다 — 예외보다
        찾기 어렵습니다.

    되돌리는 방향은 항목마다 다릅니다. 백분위는 가장 가까운 끝으로 자릅니다
    (0은 '상대 임계 없음', 100은 '전부 하위'라는 뜻이 그대로 살아 있습니다).
    나머지 수치는 기본값으로 물러섭니다. 목표 비율만 기본값이 아니라
    None으로 보냅니다 — 읽을 수 없는 값을 0.10으로 바꾸면 사용자가 요구한
    적 없는 비율이 조용히 걸리기 때문입니다.
    """
    from dataclasses import fields, replace

    config = config or ScoreConfig()
    # 전체 설정(Config)을 잘못 넘기면 아래 루프가 다섯 프레임 안쪽에서
    # "'Config' object has no attribute 'trust_eye'"로 죽습니다. 진짜 문제가
    # 무엇인지 이름조차 나오지 않아서, 실제로 채점표를 붙일 때 한 번 당했습니다.
    if not isinstance(config, ScoreConfig):
        raise TypeError(
            f"ScoreConfig가 필요합니다 ({type(config).__name__}을 받았습니다). "
            "전체 설정을 넘겼다면 .score를 넘기십시오."
        )
    fixes: dict[str, object] = {}

    for field_info in fields(ScoreConfig):
        value = getattr(config, field_info.name)
        if not isinstance(value, float) or np.isfinite(value):
            continue
        if field_info.name == "target_keep_ratio":
            fixes[field_info.name] = None
        else:
            fixes[field_info.name] = field_info.default

    percentile = fixes.get("reject_percentile", config.reject_percentile)
    if not 0.0 <= percentile <= 100.0:
        fixes["reject_percentile"] = float(np.clip(percentile, 0.0, 100.0))

    # 얼굴 기준 면적도 같은 이유로 자릅니다. 0이나 음수면 모든 얼굴이 온전한
    # 보너스를 받아 크기 가중이 통째로 사라지고, 1을 넘으면 어떤 얼굴도
    # 기준에 못 미쳐 인물 보너스가 전부 죽습니다. 둘 다 조용히 일어납니다.
    low, high = FACE_BONUS_AREA_RANGE
    area = fixes.get("face_bonus_full_area", config.face_bonus_full_area)
    if not low <= area <= high:
        fixes["face_bonus_full_area"] = float(np.clip(area, low, high))

    return replace(config, **fixes) if fixes else config


def _effective_trust(focus, config: ScoreConfig) -> float:
    """실제로 적용할 ROI 신뢰도 — **설정값을 그대로 씁니다.**

    예전에는 얼굴 우선 모드에서 눈 0.85 / 얼굴 0.75의 하한을 걸어 설정값을
    끌어올렸습니다. 얼굴 영역이 판정을 주도하게 하려는 의도였지만, 그
    하한보다 낮은 값은 **아무 반응이 없었습니다** — 눈 기준을 0.75로,
    얼굴 기준을 0.60으로 내려도 점수가 한 자리도 안 움직였습니다.

    설정을 조용히 무시하는 것이 하한이 막으려던 문제보다 나쁩니다. 얼굴
    우선 모드는 이미 bonus_focus_on_face / penalty_no_face /
    penalty_face_defocus로 제 역할을 합니다 — 신뢰도까지 덮을 이유가 없습니다.
    """
    return float(np.clip(roi_trust(focus.source, config), 0.0, 1.0))


def _face_bonus_weight(focus, config: ScoreConfig) -> float:
    """얼굴 크기에 따른 보너스 배율 (0~1).

    얼굴 검출은 수십 화소짜리 얼굴도 찾습니다. 크기를 안 보면 객석에 잡힌
    얼굴이 주 피사체와 같은 보너스를 받아, 무대 뒤 관객이 주 피사체로 뽑힌
    컷이 인물을 크게 잡은 컷과 나란히 올라옵니다(실사용 리포트).

    기준 면적(config.face_bonus_full_area) 이상이면 온전히 받고, 그 아래는
    면적에 **제곱근**을 씌워 비례시킵니다. 면적을 그대로 쓰면 얼굴이 절반
    크기일 때 보너스가 1/4로 떨어져 평범한 전신 인물컷까지 깎입니다.

    아주 작은 얼굴은 아예 0으로 끊습니다 — _FACE_WEIGHT_CUTOFF 참고.
    """
    full_area = float(config.face_bonus_full_area)
    if full_area <= 0.0:
        return 1.0  # 크기를 안 보겠다는 뜻

    ratio = max(0.0, float(getattr(focus, "face_area_ratio", 0.0)))
    if ratio >= full_area:
        return 1.0
    if ratio < full_area * _FACE_WEIGHT_CUTOFF:
        return 0.0
    return float(np.sqrt(ratio / full_area))


def _face_defocus_penalty(focus, config: ScoreConfig) -> float:
    """"초점이 얼굴이 아니라 배경에 맞은" 컷에 매길 감점(0 이상).

    배경 선명도가 얼굴 ROI 선명도보다 높을수록 커지고, 얼굴 검출 신뢰도로
    가중합니다. background_sharpness가 없는(예전 캐시) 레코드는 frame_sharpness로
    폴백합니다.
    """
    if not (config.face_priority and config.penalty_face_defocus and focus.face_count):
        return 0.0
    if focus.source not in (FocusSource.EYE, FocusSource.FACE):
        return 0.0

    # background_sharpness는 얼굴 밖 최고 선명 영역의 실측치다. 0은 "배경이
    # 매끈함"(초점 얕은 좋은 인물)이라는 유효한 측정이지 결측이 아니므로,
    # frame_sharpness(얼굴 포함 전체)로 대체하면 안 됩니다 — 좋은 인물을
    # 잘못 감점하게 됩니다. 예전(v2) 캐시는 0이라 감점이 안 되지만, v3
    # 재분석 뒤에는 제대로 채워지고 재분석은 어차피 필요합니다.
    deficit = focus.background_sharpness - focus.sharpness
    if deficit <= 0:
        return 0.0

    confidence = float(np.clip(focus.face_confidence, 0.0, 1.0))
    magnitude = min(1.0, deficit / _FACE_DEFOCUS_SCALE)
    return config.penalty_face_defocus * magnitude * max(0.5, confidence)


def _eyes_closed(focus, config: ScoreConfig) -> bool:
    """주 피사체가 눈을 감았다고 볼지.

    **못 잰 경우(-1)는 감점하지 않습니다.** 얼굴이 없거나, 눈이 너무 작거나,
    화면 밖으로 걸쳐 랜드마크를 못 얻은 컷입니다. 모르는 것을 나쁜 것으로
    취급하면 멀쩡한 원경 컷이 통째로 밀려납니다.
    """
    if not config.penalty_eyes_closed:
        return False
    value = getattr(focus, "eyes_open", -1.0)
    return 0.0 <= value < config.eyes_closed_below


# 채점표 항목 키. 화면 문구가 아니라 **식별자**입니다 — 번역과 테스트가
# 이 값을 기준으로 붙습니다. 리터럴을 흩어 두면 오타가 조용히 지나가고,
# 이름을 바꿀 때 한 군데를 빠뜨립니다.
LINE_FAILED = "failed"
LINE_SHARPNESS = "sharpness"
LINE_FACE_DEFOCUS = "face_defocus"
LINE_FOCUS_ON_FACE = "focus_on_face"
LINE_NO_FACE = "no_face"
LINE_FACE_DETECTED = "face_detected"
LINE_FACE_SIZE = "face_size"
LINE_EYE_DETECTED = "eye_detected"
LINE_EYES_CLOSED = "eyes_closed"
LINE_EYES_OPEN = "eyes_open"
LINE_EYES_UNKNOWN = "eyes_unknown"
LINE_HIGHLIGHT_CLIP = "highlight_clip"
LINE_SHADOW_CLIP = "shadow_clip"
LINE_EXTREME_LUMA = "extreme_luma"
LINE_CLAMPED = "clamped"

#: 눈 상태 줄들. 세 가지 중 정확히 하나가 항상 나옵니다(얼굴 우선 모드일 때).
EYE_STATE_KEYS = (LINE_EYES_CLOSED, LINE_EYES_OPEN, LINE_EYES_UNKNOWN)

# 판정 근거 키. 채점표 키와 별개입니다 — 근거는 "왜 이 등급인가"를 말하고,
# 채점표는 "몇 점이 어디서 왔는가"를 말합니다. 겹치는 항목도 문구가 다릅니다.
REASON_ERROR = "error"
REASON_ROI_SHARPNESS = "roi_sharpness"
REASON_FACE_COUNT = "face_count"
REASON_FACE_DEFOCUS = "face_defocus"
REASON_HIGHLIGHT_CLIP = "highlight_clip"
REASON_SHADOW_CLIP = "shadow_clip"
REASON_EYES_UNKNOWN = "eyes_unknown"
REASON_EYES_CLOSED = "eyes_closed"
REASON_EYES_OPEN = "eyes_open"
REASON_FRAME_BLACK = "frame_black"
REASON_FRAME_WHITE = "frame_white"
REASON_BATCH_BOTTOM = "batch_bottom"
REASON_BETTER_IN_GROUP = "better_in_group"
REASON_NOT_RAW = "not_raw"


@dataclass(frozen=True)
class Reason:
    """등급 판정 근거 한 줄.

    ScoreLine과 같은 이유로 키와 수치만 듭니다 — core는 Qt를 import하지
    않으므로 화면 문장을 만들 수 없습니다. 영어 문구는
    core/reason_text.py(번역 없음, CLI용)와 gui/reason_text.py(번역됨)에
    있고, 둘이 어긋나지 않는지는 테스트가 지킵니다.
    """

    key: str
    params: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoreLine:
    """채점표 한 줄.

    key는 **번역 키**이지 화면에 그대로 쓰는 문자열이 아닙니다. params는
    근거 문구에 채워 넣을 수치입니다.

    core가 완성된 문장을 만들면 번역할 방법이 없습니다. 이 패키지는 Qt를
    import하지 않습니다 — 분석이 ProcessPoolExecutor 워커에서 도는데,
    워커마다 Qt를 올리는 것은 순전히 낭비입니다. 그래서 키와 숫자만
    넘기고 문장은 GUI가 만듭니다 (gui/score_card.py).
    """

    key: str
    value: float
    params: dict[str, object] = field(default_factory=dict)


def sharpness_scale(config: ScoreConfig) -> float:
    """이 설정에서 선명도 항에 곱할 배수.

    얼굴 우선 모드는 얼굴·눈 신호에 점수의 절반을 내주므로 선명도를 0~50으로
    누릅니다. 모드를 끄면 그 신호가 전부 사라지는데, 배수를 그대로 두면
    선명도만으로 최대 50점이라 상단 절반이 통째로 빕니다 — 실측(A6700
    2845장)에서 최대 45.1점이라 keep 기준 65에 닿는 컷이 하나도 없습니다.

    반대로 배수만 올리고 얼굴 보너스를 남겨 두면 42장이 100점에 붙어
    순위가 사라집니다. 배수와 보너스는 **같이** 움직여야 합니다.
    """
    return SHARPNESS_SCALE if config.face_priority else SHARPNESS_SCALE_NO_FACE


def score_breakdown(
    record: ImageRecord, config: ScoreConfig | None = None
) -> tuple[list[ScoreLine], float]:
    """점수를 항목별로 쪼갠 것과 최종 점수.

    **compute_score가 이 함수를 씁니다.** 채점표를 따로 만들면 반드시
    어긋납니다 — 점수 규칙을 고칠 때 한쪽만 고치기 때문입니다. 화면에 뜬
    항목의 합이 실제 점수와 다르면 설명이 아니라 거짓말이 됩니다.
    """
    config = sanitized_config(config)
    if not record.ok:
        return [ScoreLine(LINE_FAILED, 0.0, {"error": record.error or ""})], 0.0

    focus = record.focus
    lines: list[ScoreLine] = []

    trust = _effective_trust(focus, config)
    scale = sharpness_scale(config)
    base = scale * (
        trust * focus.sharpness + (1.0 - trust) * focus.frame_sharpness
    )
    lines.append(ScoreLine(LINE_SHARPNESS, base, {
        "source": focus.source.value,
        "roi": focus.sharpness,
        "trust": trust,
        "frame": focus.frame_sharpness,
        "frame_weight": 1.0 - trust,
        "scale": scale,
    }))

    # 얼굴 우선 모드: 얼굴은 흐린데 배경이 더 선명하면 초점이 빗나간 컷으로 보고 감점
    defocus = _face_defocus_penalty(focus, config)
    if defocus:
        lines.append(ScoreLine(LINE_FACE_DEFOCUS, -defocus, {
            "background": focus.background_sharpness,
            "face": focus.sharpness,
        }))

    # 얼굴·눈 신호는 **전부** 얼굴 우선 모드 안에서만 작동합니다.
    #
    # 예전에는 초점 관련 셋만 이 모드에 묶여 있고 얼굴/눈 보너스는 모드와
    # 무관하게 붙었습니다. 그래서 "풍경 위주면 꺼서 전체 프레임 선명도로만
    # 판정합니다"라는 설명과 실제 동작이 달랐습니다. 배수를 모드에 따라
    # 바꾸면서 이 어긋남이 치명적이 됩니다 — 배수 1.0에 얼굴 보너스가
    # 그대로 붙으면 실측에서 42장이 100점에 붙어 순위가 사라집니다.
    if config.face_priority:
        # 초점이 얼굴에 맞았으면 우대하고, 얼굴이 아예 없으면 이 모드가
        # 보려던 근거가 없는 컷이므로 낮춥니다. 이걸 빼면 얼굴 없는 컷이
        # 프레임 선명도만으로 얼굴 컷을 앞지릅니다.
        if focus.face_count and focus.source in (FocusSource.EYE, FocusSource.FACE):
            if config.bonus_focus_on_face:
                lines.append(ScoreLine(
                    LINE_FOCUS_ON_FACE, config.bonus_focus_on_face))
        elif not focus.face_count:
            if config.penalty_no_face:
                lines.append(ScoreLine(LINE_NO_FACE, -config.penalty_no_face))

        # **얼굴 크기로 가중합니다** — 객석에 잡힌 작은 얼굴이 주 피사체와
        # 같은 가산을 받으면 안 됩니다 (_face_bonus_weight 참고).
        face_weight = _face_bonus_weight(focus, config)
        weight_params = {
            "area": focus.face_area_ratio * 100.0,
            "threshold": config.face_bonus_full_area * 100.0,
            "weight": face_weight,
        }
        if focus.face_count:
            if config.bonus_face:
                lines.append(ScoreLine(
                    LINE_FACE_DETECTED, config.bonus_face * face_weight,
                    dict(weight_params)))
            if config.bonus_face_size:
                # 프레임 대비 얼굴 면적. 실측에서 망원 인물은 0.2% 수준이라
                # 그대로 곱하면 티가 안 납니다. 10%를 만점으로 잡아 정규화합니다.
                lines.append(ScoreLine(
                    LINE_FACE_SIZE,
                    config.bonus_face_size * min(1.0, focus.face_area_ratio / 0.10)))
        if focus.source is FocusSource.EYE and config.bonus_eye:
            # 눈 보너스도 같은 이유로 가중합니다. 객석 얼굴에서도 '눈 영역'은
            # 잡히기 때문에, 안 걸면 크기 가중을 우회하는 통로가 됩니다.
            lines.append(ScoreLine(
                LINE_EYE_DETECTED, config.bonus_eye * face_weight,
                dict(weight_params)))

        # 눈 상태 — 뜨면 +, 감으면 −. 초점으로는 전혀 걸러지지 않는 항목이라
        # (감은 눈도 초점은 맞아 있습니다) 여기서 양방향으로 벌립니다.
        # 못 잰 컷은 어느 쪽도 아닙니다 — 모르는 것을 좋게도 나쁘게도 보지
        # 않습니다. 그러지 않으면 옆얼굴 원경이 정면 인물과 같은 가산을 받습니다.
        eyes_open = getattr(focus, "eyes_open", -1.0)
        eye_params = {"ear": eyes_open, "threshold": config.eyes_closed_below}
        if _eyes_closed(focus, config):
            lines.append(ScoreLine(
                LINE_EYES_CLOSED, -config.penalty_eyes_closed, eye_params))
        elif eyes_open >= 0.0:
            lines.append(ScoreLine(
                LINE_EYES_OPEN, config.bonus_eyes_open, eye_params))
        else:
            lines.append(ScoreLine(LINE_EYES_UNKNOWN, 0.0))

    # 하이라이트가 크게 날아갔으면 초점과 무관하게 쓰기 어렵습니다.
    if (
        config.penalty_highlight_clip
        and focus.clipped_highlights > config.max_clipped_highlights
    ):
        excess = focus.clipped_highlights - config.max_clipped_highlights
        lines.append(ScoreLine(
            LINE_HIGHLIGHT_CLIP,
            -min(config.penalty_highlight_clip, excess * 100.0),
            {"clipped": focus.clipped_highlights * 100.0,
             "allowed": config.max_clipped_highlights * 100.0}))

    if (
        config.penalty_shadow_clip
        and focus.clipped_shadows > config.max_clipped_shadows
    ):
        excess = focus.clipped_shadows - config.max_clipped_shadows
        lines.append(ScoreLine(
            LINE_SHADOW_CLIP,
            -min(config.penalty_shadow_clip, excess * 100.0),
            {"clipped": focus.clipped_shadows * 100.0,
             "allowed": config.max_clipped_shadows * 100.0}))

    # 완전히 어둡거나 완전히 밝은 프레임 — 렌즈캡, 하늘 향한 오발 셔터 등
    if focus.mean_luma < 8.0 or focus.mean_luma > 247.0:
        lines.append(ScoreLine(
            LINE_EXTREME_LUMA, -config.penalty_extreme_luma,
            {"luma": focus.mean_luma}))

    total = sum(line.value for line in lines)
    clipped = float(np.clip(total, 0.0, 100.0))
    if clipped != total:
        lines.append(ScoreLine(
            LINE_CLAMPED, clipped - total, {"total": total}))
    return lines, clipped


def compute_score(record: ImageRecord, config: ScoreConfig | None = None) -> float:
    """한 장의 0~100 점수. 그룹이나 배치 정보 없이 계산됩니다.

    가중치는 전부 ScoreConfig에서 옵니다. 촬영 스타일마다 무엇을 중히 볼지가
    달라서(인물이면 얼굴, 풍경이면 전체 선명도) 하드코딩하면 안 됩니다.
    """
    return score_breakdown(record, config)[1]


def _reasons(
    record: ImageRecord,
    config: ScoreConfig,
    threshold: float,
    group_best: float | None = None,
) -> list[Reason]:
    """등급 판정 근거. GUI에서 사용자가 납득할 수 있어야 합니다.

    문장이 아니라 키와 수치를 돌려줍니다 — Reason 참고.
    """
    reasons: list[Reason] = []
    if not record.ok:
        return [Reason(REASON_ERROR, {"error": record.error or ""})]

    focus = record.focus
    reasons.append(Reason(REASON_ROI_SHARPNESS, {
        "source": focus.source.value, "sharpness": focus.sharpness}))

    # 원본이 RAW가 아니면 보정 여유가 다릅니다. 말해 주지 않으면 RAW처럼
    # 밀어붙이다 하이라이트가 안 살아나는 이유를 못 찾습니다.
    from .raw_io import is_editable_image  # 순환 import 회피

    if is_editable_image(record.path):
        reasons.append(Reason(REASON_NOT_RAW,
                              {"format": record.path.suffix.lstrip(".").upper()}))

    if focus.face_count:
        reasons.append(Reason(REASON_FACE_COUNT, {"count": focus.face_count}))
    if _face_defocus_penalty(focus, config) > 0:
        reasons.append(Reason(REASON_FACE_DEFOCUS, {
            "deficit": focus.background_sharpness - focus.sharpness}))
    if (
        config.penalty_highlight_clip
        and focus.clipped_highlights > config.max_clipped_highlights
    ):
        reasons.append(Reason(REASON_HIGHLIGHT_CLIP, {
            "percent": focus.clipped_highlights * 100.0}))
    if (
        config.penalty_shadow_clip
        and focus.clipped_shadows > config.max_clipped_shadows
    ):
        reasons.append(Reason(REASON_SHADOW_CLIP, {
            "percent": focus.clipped_shadows * 100.0}))
    # 눈 상태는 **항상** 적습니다. 감점될 때만 적으면 "눈을 떴다", "감았지만
    # 임계 위라 안 깎였다", "아예 못 쟀다"가 화면에서 전부 똑같이 침묵으로
    # 보입니다. 사용자가 눈 감긴 컷을 직접 찾아야 하는 상황이 됩니다.
    # 눈 상태는 얼굴 우선 모드에서만 점수에 반영됩니다. 모드를 껐는데
    # 근거에만 뜨면, 안 쓰이는 값을 보고 임계를 맞추게 됩니다.
    eyes_open = getattr(focus, "eyes_open", -1.0)
    if config.face_priority:
        if eyes_open < 0.0:
            reasons.append(Reason(REASON_EYES_UNKNOWN))
        elif _eyes_closed(focus, config):
            reasons.append(Reason(REASON_EYES_CLOSED, {
                "ear": eyes_open, "threshold": config.eyes_closed_below}))
        else:
            reasons.append(Reason(REASON_EYES_OPEN, {
                "ear": eyes_open, "bonus": config.bonus_eyes_open}))
    if focus.mean_luma < 8.0:
        reasons.append(Reason(REASON_FRAME_BLACK))
    elif focus.mean_luma > 247.0:
        reasons.append(Reason(REASON_FRAME_WHITE))
    if record.score < threshold:
        reasons.append(Reason(REASON_BATCH_BOTTOM, {"threshold": threshold}))

    if group_best is not None:
        deficit = group_best - record.score
        if deficit >= config.reject_below_group_best:
            reasons.append(Reason(REASON_BETTER_IN_GROUP, {"deficit": deficit}))

    return reasons


def achievable_keep_floor(
    records: list[ImageRecord], config: ScoreConfig | None = None
) -> float:
    """목표 비율로 내려갈 수 있는 하한.

    장면마다 최소 1장을 남기므로 '장면 수 / 전체 장수'보다 낮아질 수 없습니다.
    단 min_keep_score에 걸리는 장면은 keep이 안 나오므로 하한에서 빠집니다.
    GUI가 이 값을 보여줘야 사용자가 목표를 잘못 잡지 않습니다.
    """
    valid = [r for r in records if r.ok]
    if not valid:
        return 0.0

    config = sanitized_config(config) if config else None
    if config and config.keep_per_group <= 0:
        return 0.0  # 장면 보장을 껐으면 하한이 없습니다

    minimum = config.min_keep_score if config else 0.0
    if minimum <= 0.0:
        return len({r.group_id for r in valid}) / len(valid)

    qualifying = {
        r.group_id for r in valid if r.group_rank == 0 and r.score >= minimum
    }
    return len(qualifying) / len(valid)


def groups_without_keep(records: list[ImageRecord]) -> set[int | None]:
    """keep이 하나도 없는 장면들.

    장면 보장을 끄거나 품질 하한을 올리면 전체가 빠지는 장면이 생깁니다.
    사용자가 그게 어떤 장면인지 볼 수 있어야 놓친 컷을 확인할 수 있습니다.
    """
    groups: dict[int | None, bool] = {}
    for record in records:
        has_keep = groups.get(record.group_id, False)
        groups[record.group_id] = has_keep or record.final_grade is Grade.KEEP
    return {group for group, has_keep in groups.items() if not has_keep}


def records_in_groups_without_keep(records: list[ImageRecord]) -> list[ImageRecord]:
    """keep이 없는 장면에 속한 컷들. 격자에서 따로 보여주기 위한 것."""
    targets = groups_without_keep(records)
    return [r for r in records if r.group_id in targets]


def dropped_groups(
    records: list[ImageRecord], config: ScoreConfig | None = None
) -> int:
    """keep이 하나도 나오지 않는 장면 수.

    품질 하한과 장면 보장 해제(keep_per_group=0) 양쪽 모두를 반영합니다.
    """
    config = sanitized_config(config)
    if config.min_keep_score <= 0.0 and config.keep_per_group > 0:
        return 0
    return len(groups_without_keep(records))


def _may_auto_keep(record: ImageRecord, config: ScoreConfig) -> bool:
    """점수만으로 곧장 keep을 줘도 되는 컷인지.

    절대 임계(keep_above)는 "볼 것도 없이 건진 컷"이라는 뜻입니다. 얼굴
    우선 모드에서 얼굴이 하나도 없으면 그 판단의 근거가 없습니다 — 잘 찍힌
    풍경일 수도, 인물을 놓친 헛컷일 수도 있어서 사람이 봐야 합니다.

    그래도 장면 1등 자격은 그대로라, 그 장면에서 제일 나은 컷이면 keep으로
    올라갑니다. 장면이 통째로 사라지지는 않습니다.
    """
    if not config.face_priority:
        return True
    return bool(record.focus.face_count)


def _effective_keep_above(records: list[ImageRecord], config: ScoreConfig) -> float:
    """목표 비율이 설정돼 있으면 배치 점수 분포에서 임계값을 역산합니다.

    keep은 '그룹 1등' + '임계값 이상' 의 합집합입니다. 그룹 1등은 이미 확정이니,
    목표 장수에서 그룹 수를 뺀 만큼만 나머지 중 상위에서 채우면 됩니다.
    """
    if config.target_keep_ratio is None:
        return config.keep_above

    valid = [r for r in records if r.ok]
    if not valid:
        return config.keep_above

    target_count = round(len(valid) * config.target_keep_ratio)

    # 품질 하한에 걸려 keep이 안 나오는 장면은 보장 장수에서 빼야 합니다.
    # 안 그러면 목표 비율이 실제보다 적게 잡힙니다.
    guaranteed = (
        {
            r.group_id for r in valid
            if r.group_rank == 0 and r.score >= config.min_keep_score
        }
        if config.keep_per_group > 0
        else set()
    )
    extra_needed = target_count - len(guaranteed)

    if extra_needed <= 0:
        # 목표가 하한보다 낮다 — 그룹 1등만 남기는 것이 최선입니다
        return float("inf")

    others = sorted(
        (r.score for r in valid if r.group_rank != 0), reverse=True
    )
    if extra_needed >= len(others):
        return 0.0
    return others[extra_needed - 1]


def grade_records(
    records: list[ImageRecord], config: ScoreConfig | None = None
) -> list[ImageRecord]:
    """점수 계산 → 그룹 내 순위 → 등급 판정. 제자리 수정 후 그대로 반환합니다.

    grouping.assign_groups()가 먼저 실행돼 group_id가 채워져 있어야 합니다.
    """
    config = sanitized_config(config)
    if not records:
        return records

    for record in records:
        record.score = compute_score(record, config)

    # 그룹 안에서 점수 내림차순 순위를 매깁니다. 동점이면 파일명 순으로 안정화합니다.
    by_group: dict[int | None, list[ImageRecord]] = {}
    for record in records:
        by_group.setdefault(record.group_id, []).append(record)

    group_best: dict[int | None, float] = {}
    for group_id, members in by_group.items():
        members.sort(key=lambda r: (-r.score, r.path.name))
        for rank, record in enumerate(members):
            record.group_rank = rank
        group_best[group_id] = members[0].score

    keep_above = _effective_keep_above(records, config)

    # 절대 임계와 배치 상대 임계를 함께 봅니다. 조명 조건이 배치마다 달라서
    # 절대값만으로는 불안정하고, 백분위만 쓰면 전부 잘 나온 배치에서도
    # 기계적으로 하위 15%를 버리게 됩니다.
    valid_scores = [r.score for r in records if r.ok]
    if valid_scores:
        relative = float(np.percentile(valid_scores, config.reject_percentile))
        threshold = max(config.reject_below, relative)
    else:
        threshold = config.reject_below

    for record in records:
        if not record.ok:
            record.grade = Grade.REJECT
        elif record.score >= keep_above and _may_auto_keep(record, config):
            record.grade = Grade.KEEP
        elif (
            record.group_rank is not None
            and record.group_rank < config.keep_per_group
            and record.score >= config.min_keep_score
        ):
            # 그룹의 상위 컷은 기본적으로 지킵니다. 장면 전체가 사라지는 것은
            # 되돌리기 어려운 실패이기 때문입니다. 다만 min_keep_score를 올리면
            # 장면 전체가 기준 미달일 때 아무것도 뽑지 않습니다.
            record.grade = Grade.KEEP
        elif record.score < threshold:
            record.grade = Grade.REJECT
        elif (
            group_best.get(record.group_id, 0.0) - record.score
            >= config.reject_below_group_best
        ):
            # 같은 순간을 찍은 더 나은 컷이 있습니다. 볼 이유가 없는 중복입니다.
            record.grade = Grade.REJECT
        else:
            record.grade = Grade.REVIEW

        record.reasons = _reasons(record, config, threshold, group_best.get(record.group_id))

    return records


def summarize(records: list[ImageRecord]) -> dict[str, int]:
    """등급별 장수. 사용자 수동 판정이 반영된 최종 등급 기준."""
    counts = {grade.value: 0 for grade in Grade}
    for record in records:
        counts[record.final_grade.value] += 1
    return counts
