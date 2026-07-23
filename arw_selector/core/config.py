"""설정. 촬영 스타일마다 적정값이 달라지므로 하드코딩하지 않습니다."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from . import focus

log = logging.getLogger(__name__)

#: face_bonus_full_area가 가질 수 있는 범위 (프레임 대비 얼굴 면적).
#:
#: 하한 0.1%는 26MP에서 대략 160×160 화소입니다. 망원으로 멀리서 찍는
#: 무대 촬영은 주 피사체 얼굴이 0.1~0.6%에 몰려 있어(A6700 2845장 실측),
#: 그 구간을 못 가리키면 설정이 무의미해집니다.
#: 상한 50%는 얼굴이 화면 절반을 덮는 클로즈업으로, 그 위는 의미가 없습니다.
#:
#: GUI 스핀박스와 sanitized_config()가 같은 값을 봐야 하므로 여기 둡니다.
FACE_BONUS_AREA_RANGE = (0.001, 0.50)


@dataclass
class AnalyzeConfig:
    """초점 분석 파라미터. 이 값이 바뀌면 캐시는 무효가 됩니다."""

    detect_long_edge: int = focus.DETECT_LONG_EDGE
    laplacian_k: float = focus.LAPLACIAN_K
    tenengrad_k: float = focus.TENENGRAD_K

    def cache_key(self) -> str:
        """분석 결과에 영향을 주는 모든 것의 지문.

        설정값뿐 아니라 알고리즘 버전도 넣어야 합니다. 설정이 그대로여도
        측정 방식이 바뀌면 예전 결과는 무효인데, 버전이 빠져 있으면 캐시가
        옛날 점수를 그대로 돌려줘서 고친 내용이 반영되지 않습니다.
        """
        payload = json.dumps(
            {**asdict(self), "_algorithm": focus.ALGORITHM_VERSION}, sort_keys=True
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclass
class GroupConfig:
    """유사 컷 그룹핑 파라미터."""

    time_gap_seconds: float = 3.0
    """이 간격을 넘으면 다른 그룹. 그룹핑의 주 신호입니다.

    실측(A6700, 2845장)에서 연사 내 간격 중앙값은 0.16초, p90은 1.4초였고
    실제 장면 전환은 수십~수백 초였습니다. 시간은 두 상황을 깨끗하게 가릅니다.
    """

    scene_change_distance: int = 40
    """dHash 해밍 거리 임계값 (64비트 중). 보조 신호이므로 느슨하게 잡습니다.

    같은 배치 실측에서 시각 신호만으로는 연사와 장면 전환을 가를 수 없었습니다.
      - 같은 연사(<0.5초) 거리: 중앙값 11, p90 25, p99 37
      - 장면 전환(>60초) 거리: 중앙값 29, p10 23
    분포가 겹쳐서 어떤 임계값도 둘을 분리하지 못합니다. 히스토그램 상관으로
    바꿔도 마찬가지였다(연사 p10 0.651 vs 전환 p90 0.732).

    망원으로 움직이는 피사체를 찍으면 0.16초 사이에도 화면이 크게 바뀌기
    때문입니다. 그래서 이 값은 "연사인데도 화면이 완전히 뒤집힌" 명백한
    경우(연사 p99=37을 넘는 40 이상)만 잡도록 두었습니다. 정물이나 인물 위주
    촬영이라면 더 낮춰도 잘 동작합니다.
    """

    no_time_hash_distance: int = 16
    """EXIF 촬영 시각이 없을 때만 쓰는 임계값.

    이 경우 시각 신호가 유일한 근거라 느슨하게 두면 전부 한 그룹이 됩니다.
    """

    max_group_size: int = 40
    """폭주 방지. 한 그룹이 이보다 커지면 강제로 끊습니다."""


@dataclass
class ScoreConfig:
    """점수 통합과 등급 판정 파라미터."""

    # ------------------------------------------------------------ 점수 가중치
    #
    # 점수 = (ROI 선명도 × 신뢰도 + 전체 선명도 × (1 - 신뢰도)) × 0.5
    #        + 보너스 - 감점
    #
    # 선명도에 0.5를 곱하는 이유는 scoring.SHARPNESS_SCALE 참고 — 선명도만으로
    # 0~100을 다 쓰면 보너스를 조금만 켜도 100에 붙어 순위가 사라집니다.
    # **아래 절대 점수 임계값들은 그 척도(선명도 0~50)를 전제로 합니다.**
    #
    # 신뢰도는 이 ROI를 얼마나 믿을지를 뜻합니다. 눈을 잡았으면 그 안의 선명도가
    # 곧 판정 근거지만, 타일 추정은 주 피사체가 아닐 수 있어 전체 프레임
    # 쪽에 무게를 나눠 줍니다.

    trust_eye: float = 0.75
    """눈 영역을 잡았을 때 ROI 선명도의 비중."""

    trust_face: float = 0.60
    """얼굴은 잡았지만 눈 ROI가 너무 작을 때."""

    trust_tile: float = 0.55
    """얼굴이 없어 격자 타일로 추정했을 때."""

    trust_frame: float = 0.40
    """ROI를 못 잡아 전체 프레임을 쓸 때."""

    face_priority: bool = True
    """얼굴 우선 모드. 인물 위주 촬영(A6700 기본 용도)의 기본값입니다.

    켜져 있으면 얼굴을 잡은 컷에서 얼굴/눈 ROI를 더 신뢰하고, 얼굴은 흐린데
    배경이 더 선명한 컷(초점이 뒤로 빠진 컷)을 penalty_face_defocus로 감점합니다.
    풍경 위주 배치라면 꺼서 전체 프레임 선명도로만 판정할 수 있습니다.
    """

    penalty_face_defocus: float = 15.0
    """얼굴 우선 모드에서, 배경이 얼굴보다 선명할 때의 최대 감점.

    "초점이 얼굴이 아니라 배경에 맞은" 컷을 가려냅니다. 감점 크기는 배경이
    얼굴보다 얼마나 더 선명한지에 비례하고, 얼굴 검출 신뢰도로 가중합니다
    (불확실한 검출은 덜 감점). 얼굴이 없거나 모드가 꺼져 있으면 적용되지
    않습니다.
    """

    bonus_focus_on_face: float = 5.0
    """얼굴 우선 모드에서 초점 ROI가 실제로 얼굴/눈일 때 더하는 점수.

    얼굴이 화면에 있다는 것과 그 얼굴에 초점이 맞았다는 것은 다릅니다.
    이 보너스는 후자에만 붙습니다 — 인물 셀렉에서 원하는 것이 그것이기
    때문입니다. bonus_face는 '얼굴이 있기만 하면' 붙으므로 성격이 다릅니다.
    """

    penalty_no_face: float = 10.0
    """얼굴 우선 모드인데 얼굴이 하나도 없을 때의 감점.

    실측(A6700 2845장): 얼굴 없는 컷의 점수 중앙값이 59.0으로, 초점이
    얼굴에 맞은 컷의 47.6보다 오히려 높았습니다. 얼굴 컷은 (대개 더
    부드러운) 얼굴 ROI로 재고 배경초점 감점까지 받는데, 얼굴 없는 컷은
    프레임 선명도를 감점 없이 그대로 쓰기 때문입니다. 그 결과 얼굴 우선
    모드인데도 얼굴 없는 컷이 4배 더 자주 자동 keep 됐습니다.

    이 감점은 두 집단을 견줄 수 있게 맞추는 보정입니다. 풍경 위주라면
    face_priority를 끄십시오 — 그러면 적용되지 않습니다.
    """

    bonus_face: float = 20.0
    """얼굴이 검출되면 더하는 점수. 인물 위주 촬영에서 올립니다.

    face_bonus_full_area의 크기 가중을 곱해서 붙습니다 — 작은 얼굴은 이
    값을 다 받지 못합니다.
    """

    bonus_eye: float = 15.0
    """눈까지 잡혔을 때 추가로 더하는 점수."""

    penalty_eyes_closed: float = 20.0
    """주 피사체가 눈을 감은 것으로 보일 때의 감점.

    눈 감은 컷은 초점이 아무리 맞아도 못 쓰는데, 선명도로는 전혀 걸러지지
    않습니다 — 감은 눈도 초점은 맞아 있기 때문입니다.

    감점 중 가장 큽니다. 사용자가 직접 정한 값으로, 눈 감은 컷은 아예
    자동 keep 후보에서 빼겠다는 뜻입니다. 다만 판정이 완벽하지 않으므로
    (eyes_closed_below 참고) 오판이 나면 그만큼 크게 손해 봅니다.

    bonus_eyes_open과 짝입니다. 뜬 컷과 감은 컷의 실제 점수 차이는
    두 값의 합입니다.
    """

    bonus_eyes_open: float = 10.0
    """주 피사체가 눈을 뜬 것으로 보일 때의 가산.

    감점만 있으면 "눈을 떴다"와 "눈을 못 쟀다"가 점수에서 똑같습니다.
    옆얼굴이라 못 잰 컷과 정면으로 눈을 뜬 컷이 같은 대우를 받는 셈이라,
    인물 셀렉트에서 가장 중요한 신호가 반만 쓰이고 있었습니다.

    **못 잰 컷(-1)에는 주지 않습니다.** 모르는 것을 좋은 것으로도 나쁜
    것으로도 취급하지 않는다는 원칙은 그대로입니다 — 그렇게 하면 옆얼굴
    원경이 정면 인물과 같은 가산을 받습니다.

    얼굴 크기 가중을 받지 않습니다. penalty_eyes_closed와 대칭으로 두어
    "떴으면 +, 감았으면 −"가 한눈에 읽히게 했습니다.
    """

    eyes_closed_below: float = 0.30
    """이 값보다 눈 종횡비(EAR)가 작으면 감았다고 봅니다.

    사용자가 라벨한 실사진 107장(감음 28 / 뜸 79) 실측:

        임계    잡아냄    거짓감점    정확
        0.20     8/28     0/79      81%
        0.22    14/28     2/79      85%
        0.25    17/28     7/79      83%
        0.28    24/28    16/79      81%
        0.30    25/28    19/79      79%   ← 기본값
        0.32    25/28    26/79      73%
        0.35    26/28    40/79      61%

    **0.30 위로는 올릴 이유가 없습니다.** 0.32는 0.30과 똑같이 25장을
    잡으면서 뜬 눈만 7장 더 깎습니다. 감음 라벨의 EAR이 대부분 0.28
    이하에 몰려 있고(28장 중 25장), 뜬 눈은 0.20부터 시작해 두 분포가
    그 위에서 겹치기 때문입니다.

    놓치는 것보다 거짓감점이 싫으면 0.22로 내리십시오 — 뜬 눈은 2장만
    깎이고 정확도는 이 표본에서 가장 높습니다.

    눈을 못 잰 컷(-1)은 어느 값에서도 감점하지 않습니다.
    """

    bonus_face_size: float = 0.0
    """얼굴이 클수록 더하는 점수 (프레임 대비 면적 비례).

    멀리 있는 행인보다 크게 잡힌 주 피사체를 우대하고 싶을 때 씁니다.
    """

    face_bonus_full_area: float = 0.03
    """얼굴 보너스를 **온전히** 받기 시작하는 얼굴 면적 (프레임 대비).

    이보다 작은 얼굴은 크기에 비례해 보너스가 줄어듭니다. 없으면 객석에
    잡힌 얼굴이 주 피사체 얼굴과 똑같은 보너스를 받습니다 — 검출기는 수십
    화소짜리 얼굴도 찾아내기 때문입니다.

    bonus_face와 bonus_eye 양쪽에 걸립니다. 눈 보너스에 안 걸면 작은 얼굴이
    그쪽으로 우회합니다.

    기본 3%는 6240×4168(26MP)에서 대략 880×880 화소입니다. 사용자가 고른
    값으로, 상반신 인물컷을 온전히 인정하는 선입니다.

    **망원으로 멀리서 찍으면 훨씬 낮춰야 합니다.** A6700 2845장(300mm 무대
    촬영) 실측에서 주 피사체 얼굴은 중앙값 0.34%, 최대 2.99%였습니다. 그런
    배치에서는 0.3% 근처가 맞습니다.

    범위는 FACE_BONUS_AREA_RANGE(0.1%~50%)입니다.
    """

    penalty_highlight_clip: float = 1.0
    """하이라이트가 임계를 넘게 날아갔을 때 최대 감점."""

    penalty_shadow_clip: float = 2.5
    """섀도우가 뭉갰을 때 최대 감점.

    작게 둡니다. 무대·야간 촬영은 의도적으로 검은 부분이 많아서, 크게 잡으면
    멀쩡한 컷이 무더기로 깎입니다. max_clipped_shadows(기본 0.5)를 넘는
    경우에만 걸립니다.
    """

    penalty_extreme_luma: float = 15.0
    """프레임이 거의 검거나 흴 때 감점 (렌즈캡, 오발 셔터)."""

    max_clipped_shadows: float = 0.5
    """섀도우가 이 비율을 넘게 뭉개지면 감점 대상."""

    keep_per_group: int = 1
    """그룹당 keep으로 올릴 상위 컷 수.

    0으로 두면 장면 보장을 끕니다. 그때는 절대 점수(keep_above)나 목표
    비율만으로 판정하므로, 전체가 keep이 하나도 안 나오는 장면이 생깁니다.
    """

    min_keep_score: float = 0.0
    """keep으로 올리기 위한 최소 점수. 장면 보장을 무력화하는 유일한 조건.

    0이면 모든 장면에서 최소 1장이 반드시 나온다(기본 동작). 0보다 크면
    장면 전체가 이 점수에 못 미칠 때 그 장면에서는 아무것도 뽑지 않습니다.

    전부 흔들린 장면까지 무리하게 한 장 건져 올리면 keep 폴더의 신뢰가
    떨어집니다. 다만 이 값을 올리면 장면 전체가 사라질 수 있으므로,
    올린 만큼 review를 꼭 확인해야 합니다.
    """

    reject_below: float = 15.0
    """이 점수 미만은 그룹 순위와 무관하게 reject (절대 임계)."""

    reject_percentile: float = 15.0
    """배치 내 하위 몇 %를 reject 후보로 볼지 (상대 임계).

    조명 조건은 배치마다 달라서 절대 임계만으로는 불안정합니다. 절대값과
    백분위를 함께 봐야 합니다.
    """

    keep_above: float = 65.0
    """그룹 순위와 무관하게 keep으로 올리는 절대 점수.

    선명도 항이 0~50이므로(scoring.SHARPNESS_SCALE) 이 값은 보너스를 상당히
    받은 컷만 넘습니다. 사용자가 실제 촬영본으로 맞춘 값입니다.

    target_keep_ratio가 켜져 있으면 이 값은 무시됩니다.
    """

    target_keep_ratio: float | None = None
    """목표 keep 비율 (0~1). None이면 keep_above를 그대로 씁니다.

    절대 점수는 배치마다 의미가 달라집니다. 조명과 렌즈가 바뀌면 점수 분포가
    전체가 이동해서, 어떤 촬영에서 10%를 내던 값이 다른 촬영에서는 30%가
    됩니다. 목표 비율을 주면 배치의 점수 분포에서 임계값을 역산하므로
    촬영이 바뀌어도 결과 비율이 유지됩니다.

    달성 가능한 하한은 '장면 수 / 전체 장수'다. 장면마다 최소 1장은 반드시
    남기기 때문입니다. 목표가 그보다 낮으면 하한이 그대로 결과가 됩니다.
    """

    reject_below_group_best: float = 10.0
    """같은 그룹 베스트보다 이만큼 낮으면 reject.

    연사에서 사람이 실제로 하는 판단입니다. 전역 점수로는 중간쯤이어도,
    같은 순간을 찍은 더 나은 컷이 있으면 그것은 볼 이유가 없는 중복입니다.

    실측(2845장, 그룹 226개)에서 그룹 베스트 대비 격차는 중앙값 14.7점,
    p75가 24점이었습니다 — **선명도가 0~100을 쓰던 예전 척도 기준**입니다.
    지금은 선명도 항이 절반(SHARPNESS_SCALE)이라 격차도 대략 절반이므로
    그 20점에 해당하는 값이 10점입니다.

    그룹 1등은 이 규칙보다 먼저 keep으로 확정되므로 장면이 통째로
    사라지는 일은 없습니다.
    """

    max_clipped_highlights: float = 0.25
    """하이라이트가 이 비율을 넘게 날아가면 감점."""


@dataclass
class Config:
    analyze: AnalyzeConfig = field(default_factory=AnalyzeConfig)
    group: GroupConfig = field(default_factory=GroupConfig)
    score: ScoreConfig = field(default_factory=ScoreConfig)
    workers: int | None = None
    """None이면 cpu_count - 1."""

    recursive: bool = True

    @classmethod
    def load(cls, path: Path | None) -> "Config":
        """YAML에서 설정을 읽습니다.

        경로가 없거나 파일이 손상되었으면 기본값으로 넘어갑니다. 설정 파일
        하나 때문에 프로그램이 실행되지 않는 상황을 만들지 않습니다.
        """
        if path is None or not Path(path).exists():
            return cls()

        try:
            raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            log.warning("설정 파일을 읽지 못했습니다 (%s): %s", path, exc)
            return cls()

        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, data: Any) -> "Config":
        """알 수 없는 키는 걸러냅니다.

        손으로 편집한 파일에서 섹션이 dict가 아닌 값으로 들어와도 그 섹션만
        기본값이 되고 나머지는 살립니다.
        """
        if not isinstance(data, dict):
            return cls()

        sections = {"analyze": AnalyzeConfig, "group": GroupConfig, "score": ScoreConfig}
        kwargs: dict[str, Any] = {}
        for name, section_cls in sections.items():
            values = data.get(name)
            if not isinstance(values, dict):
                kwargs[name] = section_cls()
                continue
            valid = {f.name for f in fields(section_cls)}
            try:
                kwargs[name] = section_cls(
                    **{k: v for k, v in values.items() if k in valid}
                )
            except (TypeError, ValueError) as exc:
                log.warning("%s 설정을 기본값으로 되돌립니다: %s", name, exc)
                kwargs[name] = section_cls()

        if "workers" in data:
            try:
                kwargs["workers"] = int(data["workers"]) if data["workers"] else None
            except (TypeError, ValueError):
                pass
        if "recursive" in data:
            kwargs["recursive"] = bool(data["recursive"])
        return cls(**kwargs)

    def to_yaml(self) -> str:
        return yaml.safe_dump(asdict(self), sort_keys=False, allow_unicode=True)
