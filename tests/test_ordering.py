"""ordering.py — 장면 무관 점수 정렬."""

from __future__ import annotations

from pathlib import Path

from arw_selector.core.ordering import SortMode, sort_records
from arw_selector.core.types import ImageRecord


def _rec(name: str, score: float, group_id: int = 0) -> ImageRecord:
    return ImageRecord(path=Path(name), score=score, group_id=group_id)


def test_score_desc_orders_high_to_low():
    records = [_rec("a.ARW", 30), _rec("b.ARW", 90), _rec("c.ARW", 60)]
    ordered = sort_records(records, SortMode.SCORE_DESC)
    assert [r.path.name for r in ordered] == ["b.ARW", "c.ARW", "a.ARW"]


def test_score_asc_orders_low_to_high():
    records = [_rec("a.ARW", 30), _rec("b.ARW", 90), _rec("c.ARW", 60)]
    ordered = sort_records(records, SortMode.SCORE_ASC)
    assert [r.path.name for r in ordered] == ["a.ARW", "c.ARW", "b.ARW"]


def test_file_mode_orders_by_name():
    records = [_rec("c.ARW", 30), _rec("a.ARW", 90), _rec("b.ARW", 60)]
    ordered = sort_records(records, SortMode.FILE)
    assert [r.path.name for r in ordered] == ["a.ARW", "b.ARW", "c.ARW"]


def test_score_sort_ignores_grouping():
    """점수순은 그룹 경계를 완전히 무시하고 배치 전체를 한 줄로 세웁니다."""
    records = [
        _rec("g0_lo.ARW", 10, group_id=0),
        _rec("g1_hi.ARW", 95, group_id=1),
        _rec("g0_hi.ARW", 80, group_id=0),
        _rec("g1_lo.ARW", 20, group_id=1),
    ]
    ordered = sort_records(records, SortMode.SCORE_DESC)
    assert [r.path.name for r in ordered] == [
        "g1_hi.ARW", "g0_hi.ARW", "g1_lo.ARW", "g0_lo.ARW"
    ]


def test_ties_break_by_name_deterministically():
    a = [_rec(f"{i}.ARW", 50) for i in range(5)]
    b = [_rec(f"{i}.ARW", 50) for i in reversed(range(5))]
    assert [r.path.name for r in sort_records(a, SortMode.SCORE_DESC)] == [
        r.path.name for r in sort_records(b, SortMode.SCORE_DESC)
    ]


class TestModeCoercion:
    """PySide6가 콤보 데이터로 넣은 Enum을 평범한 str로 돌려주는 함정.

    `is` 비교만 하면 전부 빗나가 조용히 파일순이 됩니다 — 화면에는 '점수
    높은순'이라고 떠 있는데 실제로는 정렬이 안 되는 상태였습니다.
    """

    def _batch(self):
        return [_rec("a.ARW", 30), _rec("b.ARW", 90), _rec("c.ARW", 60)]

    def test_plain_string_score_desc(self):
        ordered = sort_records(self._batch(), "score_desc")
        assert [r.path.name for r in ordered] == ["b.ARW", "c.ARW", "a.ARW"]

    def test_plain_string_score_asc(self):
        ordered = sort_records(self._batch(), "score_asc")
        assert [r.path.name for r in ordered] == ["a.ARW", "c.ARW", "b.ARW"]

    def test_enum_and_string_agree(self):
        for mode in SortMode:
            assert [r.path.name for r in sort_records(self._batch(), mode)] == [
                r.path.name for r in sort_records(self._batch(), mode.value)
            ]

    def test_unknown_value_falls_back_to_file_order(self):
        ordered = sort_records(self._batch(), "nonsense")
        assert [r.path.name for r in ordered] == ["a.ARW", "b.ARW", "c.ARW"]

    def test_none_falls_back_to_file_order(self):
        ordered = sort_records(self._batch(), None)
        assert [r.path.name for r in ordered] == ["a.ARW", "b.ARW", "c.ARW"]


def test_does_not_mutate_input():
    records = [_rec("b.ARW", 10), _rec("a.ARW", 90)]
    original = list(records)
    sort_records(records, SortMode.SCORE_DESC)
    assert records == original
