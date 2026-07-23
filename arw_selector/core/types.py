"""모듈 간에 오가는 공용 타입.

여기 있는 것들은 전부 picklable해야 합니다 — macOS의 ProcessPoolExecutor는
spawn 방식이라 워커와 주고받는 모든 값이 pickle을 통과합니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from .raw_io import RawMetadata

if TYPE_CHECKING:
    from .develop import DevelopSettings


class Grade(str, Enum):
    """셀렉트 등급. str 상속이라 JSON 직렬화가 그대로 됩니다."""

    KEEP = "keep"
    REVIEW = "review"
    REJECT = "reject"


OUTPUT_DIR_NAMES: frozenset[str] = frozenset({"_keep", "_review", "_reject"})
"""export가 만드는 폴더 이름. 재스캔 시 제외 대상."""


class FocusSource(str, Enum):
    """초점 판정에 쓴 ROI가 어디서 나왔는지."""

    EYE = "eye"          # 얼굴 랜드마크의 눈 영역 — 가장 신뢰도 높음
    FACE = "face"        # 얼굴은 찾았지만 눈 ROI가 너무 작음
    TILE = "tile"        # 얼굴 없음, 격자 타일 중 최고 선명 영역
    FRAME = "frame"      # 폴백: 프레임 전체


@dataclass(frozen=True)
class FocusResult:
    """한 장의 초점 측정 결과."""

    sharpness: float          # 0~100으로 정규화된 최종 선명도 (ROI 기준)
    laplacian: float          # 정규화된 Laplacian variance
    tenengrad: float          # 정규화된 Tenengrad
    source: FocusSource
    frame_sharpness: float = 0.0
    """ROI와 무관하게 고정 스케일 전체 프레임에서 잰 선명도.

    ROI 선정은 프레임마다 달라질 수 있고(얼굴 검출 성공/실패), ROI가 바뀌면
    sharpness끼리는 비교할 수 없습니다. frame_sharpness는 항상 같은 방식으로
    재므로 컷 간 비교의 안정적인 기준선이 됩니다.
    """
    roi: tuple[int, int, int, int] | None = None  # 프리뷰 좌표계의 (x, y, w, h)
    face_count: int = 0
    face_confidence: float = 0.0
    face_area_ratio: float = 0.0   # 프레임 대비 얼굴 면적 — 작은 얼굴은 판정 신뢰도가 낮습니다
    background_sharpness: float = 0.0
    """얼굴 ROI를 쓸 때, 얼굴 박스 바깥에서 가장 선명한 영역의 선명도.

    "초점이 얼굴이 아니라 배경에 맞은" 컷을 가려내기 위한 신호입니다. 얼굴은
    흐린데 배경이 쨍하면 이 값이 얼굴 ROI 선명도보다 높습니다. 얼굴 우선
    모드의 감점 판단에 씁니다. 얼굴이 없으면 0(해당 없음)이고, 예전 캐시
    레코드도 0이라 그 경우 frame_sharpness로 폴백합니다.
    """
    clipped_highlights: float = 0.0  # 0~1
    clipped_shadows: float = 0.0     # 0~1
    mean_luma: float = 0.0

    faces: tuple[tuple[int, int, int, int], ...] = ()
    """검출된 얼굴 전체의 (x, y, w, h). 프리뷰 좌표계입니다.

    주 피사체 하나만 들고 있으면 화면에서 "왜 저 얼굴이 뽑혔는지"를 볼 수
    없습니다. 전부 그려 주고 주 피사체만 색으로 구분합니다. 예전 캐시에는
    없어서 빈 튜플이며, 그때는 ROI만 표시합니다.
    """

    main_face: int = -1
    """faces 안에서 주 피사체의 인덱스. 얼굴이 없거나 예전 캐시면 -1."""

    face_scores: tuple[float, ...] = ()
    """faces와 같은 순서의 검출 확신도.

    화면에 그릴지, 주 피사체가 될 수 있는지를 정하는 데 씁니다. 실촬영
    표본을 눈으로 확인한 결과 0.60~0.75 구간에는 스피커 콘·장갑·어두운
    얼룩 같은 오검출이 몰려 있었습니다. 다만 같은 구간에 진짜 얼굴(측면,
    모션블러, 무대 조명)도 함께 있어서 **검출 자체를 끊으면 실제 얼굴을
    16% 잃습니다**(실측). 그래서 세는 것은 그대로 두고, 보여 주는 것과
    주 피사체로 삼는 것만 확신도로 거릅니다.
    """

    eyes_open: float = -1.0
    """주 피사체의 눈 종횡비(EAR). 잴 수 없었으면 -1.

    작을수록 감은 쪽입니다. 양쪽 눈 중 **더 떠 있는 쪽**을 씁니다 — 옆얼굴에서
    먼 쪽 눈은 거의 안 보여 항상 '감음'으로 나오는데, 그걸로 감점하면 측면
    컷이 전부 떨어집니다.

    사용자가 라벨한 107장 실측:

        임계    정확    거짓감점    잡아냄
        0.20    81%     0/79       8/28
        0.22    85%     2/79      14/28
        0.30    79%    19/79      25/28   ← 기본값 (config.eyes_closed_below)

    전체 표와 0.30을 고른 이유는 `config.ScoreConfig.eyes_closed_below`에
    있습니다. 여기 숫자가 그쪽과 어긋나면 그쪽이 맞습니다.

    -1(못 잼)은 감점하지 않습니다. 얼굴이 없거나, 너무 작거나, 화면 밖으로
    걸쳐 랜드마크를 못 얻은 경우입니다.
    """

    af_face: int = -1
    """카메라 AF가 가리킨 얼굴 번호 (faces 안의 인덱스). 없으면 -1.

    **주 피사체를 이걸로 덮어쓰지 않습니다.** 실측(117장): 둘이 갈렸을 때
    AF를 따르면 27장 개선·36장 악화로 손해입니다.

    대신 **신뢰도 신호**로 씁니다 — AF가 우리와 같은 사람을 가리키면 우리
    선정이 88% 맞고, 다르면 52%(사실상 동전던지기)입니다.
    """

    source_width: int = 0
    source_height: int = 0
    """roi와 faces 좌표가 어느 크기의 이미지를 기준으로 하는지.

    좌표만 있고 기준 크기가 없으면 그 좌표는 해석할 수 없습니다. 예전에는
    화면에서 "내장 프리뷰의 가로 = 센서 가로"라고 어림잡았는데, 파나소닉
    S1R처럼 4700만 화소 바디가 1920px짜리 프리뷰만 넣어 두는 경우가 있어
    박스가 4.37배 어긋난 자리에 그려졌습니다(실측). 추측하지 말고 잽니다.

    예전 캐시에는 없어서 0이며, 그때는 화면 쪽에서 프리뷰를 직접 재 봅니다.
    """


@dataclass
class ImageRecord:
    """파이프라인이 한 장에 대해 축적하는 모든 정보."""

    path: Path
    metadata: RawMetadata | None = None
    focus: FocusResult | None = None
    error: str | None = None

    dhash: int | None = None
    """장면 지문. 그룹핑에 씁니다.

    분석 단계에서 프리뷰를 이미 메모리에 올린 김에 같이 계산합니다. 나중에
    다시 구하려면 4000장을 전부 재디코딩해야 하므로 캐시에도 함께 넣습니다.
    """

    place_id: int | None = None
    """같은 장소끼리 묶은 번호 (core/places.py). GPS가 없으면 None.

    장면(group_id)과는 축이 다릅니다. 장면은 3초 안의 연사 묶음이고, 장소는
    하루에 몇 개뿐입니다. 내보낼 때 장소별 폴더로 나누는 데 씁니다.
    """

    # grouping/scoring 단계에서 채워집니다
    group_id: int | None = None
    group_rank: int | None = None
    score: float = 0.0
    grade: Grade = Grade.REVIEW
    reasons: list = field(default_factory=list)
    """판정 근거 (scoring.Reason). 문장이 아니라 키와 수치입니다.

    core는 Qt를 import하지 않으므로 화면 문장을 만들 수 없습니다. 문구는
    core/reason_text.py(영어, CLI용)와 gui/reason_text.py(번역됨)에 있습니다.
    타입을 scoring.Reason으로 못 박지 않은 것은 순환 import 때문입니다.
    """
    manual_grade: Grade | None = None  # GUI에서 사용자가 덮어쓴 등급

    manual_main_face: int | None = None
    """사용자가 화면에서 직접 고른 주 피사체 얼굴 번호.

    자동 선정이 틀렸을 때(앞줄 행인, 관객석 얼룩) 쓰는 탈출구입니다. 이
    값이 있으면 focus는 이미 그 얼굴 기준으로 다시 계산된 상태입니다 —
    표시만 바꾸고 점수를 그대로 두면 '고쳤는데 등급이 안 바뀌는' 상태가
    되어 더 헷갈립니다.
    """

    develop: "DevelopSettings | None" = None
    """이 컷에 지정된 보정. 내보낼 때 적용됩니다.

    분석 결과가 아니라 사용자의 편집이므로 캐시에 넣지 않는다 — 캐시는
    파일 내용이 바뀌면 버려지는데, 편집은 그것과 무관하게 유지돼야 합니다.
    """

    @property
    def final_grade(self) -> Grade:
        """사용자 수동 판정이 항상 자동 판정을 이깁니다."""
        return self.manual_grade or self.grade

    @property
    def ok(self) -> bool:
        return self.error is None and self.focus is not None

    @property
    def main_face_norm(self) -> tuple[float, float, float, float] | None:
        """주 피사체 얼굴의 정규화 좌표 (x, y, w, h), 0~1. 없으면 None.

        보정 쪽에 넘겨 얼굴 마스크의 '주 피사체'를 화면의 빨간 상자와 같은
        얼굴로 묶습니다. 해상도가 아니라 비율로 넘기는 이유는, 마스크가
        미리보기·Full Render·내보내기에서 각각 다른 크기의 이미지 위에서
        돌기 때문입니다.
        """
        focus = self.focus
        if focus is None or not focus.faces:
            return None
        if not 0 <= focus.main_face < len(focus.faces):
            return None
        width = focus.source_width or 0
        height = focus.source_height or 0
        if width <= 0 or height <= 0:
            return None
        x, y, w, h = focus.faces[focus.main_face]
        return (x / width, y / height, w / width, h / height)
