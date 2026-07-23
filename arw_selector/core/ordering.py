"""격자 표시 순서.

장면(그룹)과 무관하게 점수만으로 줄 세우고 싶을 때가 있습니다 — 배치 전체에서
제일 잘 나온/제일 흔들린 컷을 빠르게 훑을 때. 정렬은 순수 함수라 UI 없이도
검증합니다. 동점은 항상 파일명으로 안정화해, 같은 배치를 두 번 정렬해도
순서가 흔들리지 않습니다.
"""

from __future__ import annotations

from enum import Enum

from .types import ImageRecord


class SortMode(str, Enum):
    FILE = "file"              # 파일명(≈촬영 순서) — 장면이 뭉쳐 보이는 기본값
    SCORE_DESC = "score_desc"  # 점수 높은순
    SCORE_ASC = "score_asc"    # 점수 낮은순


# 표시 문구는 여기 두지 않습니다. core는 Qt를 import하지 않으므로 번역할
# 방법이 없고, 모듈 상수로 두면 import 시점에 언어가 굳습니다.
# gui/ordering_text.py 를 보십시오.


def sort_records(records: list[ImageRecord], mode) -> list[ImageRecord]:
    """새 리스트를 돌려줍니다 (원본 순서는 건드리지 않습니다).

    점수순 정렬은 그룹을 완전히 무시하고 배치 전체를 한 줄로 세웁니다.

    mode는 SortMode 또는 그 값 문자열을 받습니다. PySide6는 str을 상속한 Enum을
    콤보박스 데이터로 저장할 때 평범한 str로 바꿔 버려서(GeometrySettings.ratio와
    같은 함정), `is` 비교만 하면 전부 빗나가 조용히 파일순으로 떨어집니다.
    """
    try:
        mode = SortMode(mode)
    except ValueError:
        mode = SortMode.FILE

    if mode is SortMode.SCORE_DESC:
        return sorted(records, key=lambda r: (-r.score, r.path.name))
    if mode is SortMode.SCORE_ASC:
        return sorted(records, key=lambda r: (r.score, r.path.name))
    return sorted(records, key=lambda r: r.path.name)  # FILE
