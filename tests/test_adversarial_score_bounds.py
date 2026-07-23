"""판정 설정이 범위를 벗어나도 배치가 멈추지 않는지.

ScoreConfig는 GUI 스핀박스로만 오는 값이 아닙니다. 판정 기준 프리셋은
YAML로 저장되고(core/presets.py), 사용자가 직접 열어 고치거나 남에게
받아서 씁니다. 거기 적힌 값은 위젯의 범위를 지키지 않습니다.

여기서 걸리는 것들은 전부 "4000장 분석이 끝난 뒤 등급을 매기는 순간"에
터집니다. 분석에 몇 분을 쓰고 나서 예외 하나로 결과가 통째로 사라지는
것이 이 파일이 막으려는 상황입니다.
"""

from __future__ import annotations

import math

import pytest

from arw_selector.core import scoring
from arw_selector.core.config import ScoreConfig
from arw_selector.core.types import FocusResult, FocusSource, Grade, ImageRecord


def make_records(count: int = 9, groups: int = 3):
    from pathlib import Path

    records = []
    for index in range(count):
        focus = FocusResult(
            sharpness=30.0 + index * 5,
            laplacian=30.0,
            tenengrad=30.0,
            source=FocusSource.TILE,
            frame_sharpness=30.0 + index * 5,
            mean_luma=120.0,
        )
        record = ImageRecord(path=Path(f"{index:03d}.ARW"), focus=focus)
        record.group_id = index % groups
        records.append(record)
    return records


# 프리셋 파일에서 실제로 나올 수 있는 값들. YAML은 `.nan` / `.inf` 를
# 그대로 float으로 읽어 옵니다.
OUT_OF_RANGE = [
    ("reject_percentile", -50.0),
    ("reject_percentile", 150.0),
    ("reject_percentile", float("nan")),
    ("reject_percentile", float("inf")),
    ("reject_percentile", float("-inf")),
    ("target_keep_ratio", float("nan")),
    ("target_keep_ratio", float("inf")),
    ("target_keep_ratio", float("-inf")),
    ("target_keep_ratio", -3.0),
    ("target_keep_ratio", 99.0),
    ("keep_above", float("nan")),
    ("keep_above", float("inf")),
    ("min_keep_score", float("nan")),
    ("min_keep_score", -1000.0),
    ("reject_below", float("nan")),
    ("reject_below_group_best", float("nan")),
    ("reject_below_group_best", -50.0),
    ("keep_per_group", -5),
    ("keep_per_group", 10**6),
    ("bonus_face", float("nan")),
    ("penalty_no_face", float("inf")),
    ("eyes_closed_below", float("nan")),
    ("max_clipped_highlights", float("nan")),
    ("trust_eye", float("nan")),
]


class TestExtremeScoreConfig:
    @pytest.mark.parametrize("name,value", OUT_OF_RANGE)
    def test_grading_never_raises(self, name, value):
        records = make_records()
        config = ScoreConfig(**{name: value})
        scoring.grade_records(records, config)

    @pytest.mark.parametrize("name,value", OUT_OF_RANGE)
    def test_scores_stay_in_range_and_finite(self, name, value):
        """점수가 NaN이 되면 정렬과 임계 비교가 전부 조용히 무너집니다.

        NaN은 어떤 비교에도 False라 등급이 '아무 조건에도 안 걸린 것'으로
        떨어집니다 — 예외가 아니라 잘못된 결과라서 알아채기 어렵습니다.
        """
        records = make_records()
        scoring.grade_records(records, ScoreConfig(**{name: value}))
        for record in records:
            assert math.isfinite(record.score), f"{name}={value} 에서 점수가 {record.score}"
            assert 0.0 <= record.score <= 100.0

    @pytest.mark.parametrize("name,value", OUT_OF_RANGE)
    def test_every_record_gets_a_grade(self, name, value):
        records = make_records()
        scoring.grade_records(records, ScoreConfig(**{name: value}))
        assert all(isinstance(r.grade, Grade) for r in records)

    def test_percentile_clamps_to_the_edges(self):
        """범위 밖 백분위는 가장 가까운 끝으로 봅니다.

        0%는 '상대 임계 없음', 100%는 '전부 하위'입니다. 그 사이 어딘가로
        임의로 되돌리면 사용자가 적은 의도와 멀어집니다.
        """
        low = make_records()
        scoring.grade_records(low, ScoreConfig(reject_percentile=-50.0))

        zero = make_records()
        scoring.grade_records(zero, ScoreConfig(reject_percentile=0.0))

        assert [r.grade for r in low] == [r.grade for r in zero]

    def test_nan_ratio_falls_back_to_absolute_threshold(self):
        """비율을 못 읽으면 절대 임계(keep_above)로 갑니다.

        target_keep_ratio는 None이 '비율 안 씀'이라는 뜻이라, 읽을 수 없는
        값도 같은 자리로 보내는 것이 가장 덜 놀랍습니다.
        """
        nan_records = make_records()
        scoring.grade_records(
            nan_records, ScoreConfig(target_keep_ratio=float("nan"), keep_above=35.0))

        none_records = make_records()
        scoring.grade_records(
            none_records, ScoreConfig(target_keep_ratio=None, keep_above=35.0))

        assert [r.grade for r in nan_records] == [r.grade for r in none_records]


class TestExtremeConfigWithDegenerateBatches:
    @pytest.mark.parametrize("count", [0, 1, 2])
    def test_tiny_batches(self, count):
        """0장·1장 폴더에서도 같은 설정이 통과해야 합니다."""
        for name, value in OUT_OF_RANGE:
            records = make_records(count, groups=max(1, count))
            scoring.grade_records(records, ScoreConfig(**{name: value}))
            assert len(records) == count

    def test_all_records_failed(self):
        from pathlib import Path

        for name, value in OUT_OF_RANGE:
            records = [ImageRecord(path=Path(f"{i}.ARW"), error="손상") for i in range(4)]
            scoring.grade_records(records, ScoreConfig(**{name: value}))
            assert all(r.grade is Grade.REJECT for r in records)

    def test_helpers_survive_extreme_config(self):
        for name, value in OUT_OF_RANGE:
            records = make_records()
            config = ScoreConfig(**{name: value})
            scoring.grade_records(records, config)
            floor = scoring.achievable_keep_floor(records, config)
            assert math.isfinite(floor) and 0.0 <= floor <= 1.0
            assert scoring.dropped_groups(records, config) >= 0
            assert isinstance(scoring.summarize(records), dict)


class TestReasonsStayReadable:
    def test_reasons_never_contain_nan(self):
        """판정 근거는 사용자가 읽는 문장입니다. 'nan점 더 선명'은 설명이 아닙니다.

        근거는 이제 키와 수치로 오므로, 화면에 나가는 **문장으로 렌더한 뒤**
        확인합니다. 수치만 보면 포맷 과정에서 생기는 nan을 놓칩니다.
        """
        from arw_selector.core.reason_text import render

        for name, value in OUT_OF_RANGE:
            records = make_records()
            scoring.grade_records(records, ScoreConfig(**{name: value}))
            for record in records:
                for reason in record.reasons:
                    text = render(reason).lower()
                    assert "nan" not in text, f"{name}={value}: {text}"
                    assert "inf" not in text, f"{name}={value}: {text}"
