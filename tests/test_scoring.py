"""scoring.py 단위 테스트.

핵심 보장: 어떤 임계값 조합에서도 그룹의 최고 컷은 reject되지 않습니다.
장면 하나가 전체가 사라지는 것이 이 툴의 가장 치명적인 실팹니다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arw_selector.core import scoring
from arw_selector.core.config import ScoreConfig
from arw_selector.core.types import FocusResult, FocusSource, Grade, ImageRecord


def make_record(
    name: str,
    sharpness: float,
    frame_sharpness: float | None = None,
    source: FocusSource = FocusSource.EYE,
    group_id: int = 0,
    **focus_kwargs,
) -> ImageRecord:
    """테스트용 레코드. focus_kwargs로 FocusResult 필드를 덮어쓸 수 있습니다.

    눈/얼굴 ROI인데 얼굴이 0개인 조합은 실제로는 나올 수 없습니다. 기본값을
    맞춰 두지 않으면 얼굴 우선 모드의 '얼굴 없음' 감점이 엉뚱하게 붙어,
    관계 없는 테스트가 그 정책에 끌려다닙니다.

    같은 이유로 얼굴 면적도 채웁니다. 얼굴이 있는데 면적이 0인 조합 역시
    나올 수 없고, 그대로 두면 얼굴 크기 가중이 보너스를 0으로 눌러
    보너스와 무관한 테스트까지 끌려갑니다.
    """
    if "face_count" not in focus_kwargs and source in (
        FocusSource.EYE, FocusSource.FACE
    ):
        focus_kwargs["face_count"] = 1
    if focus_kwargs.get("face_count") and "face_area_ratio" not in focus_kwargs:
        focus_kwargs["face_area_ratio"] = 0.05  # 화면을 채운 인물컷 수준
    return ImageRecord(
        path=Path(name),
        focus=FocusResult(
            sharpness=sharpness,
            laplacian=sharpness,
            tenengrad=sharpness,
            source=source,
            frame_sharpness=sharpness if frame_sharpness is None else frame_sharpness,
            mean_luma=focus_kwargs.pop("mean_luma", 120.0),
            **focus_kwargs,
        ),
        group_id=group_id,
    )


class TestComputeScore:
    def test_failed_record_scores_zero(self):
        record = ImageRecord(path=Path("x.ARW"), error="PreviewError")
        assert scoring.compute_score(record) == 0.0

    def test_score_is_bounded(self):
        assert scoring.compute_score(make_record("a.ARW", 100.0)) <= 100.0
        assert scoring.compute_score(make_record("b.ARW", 0.0)) >= 0.0

    def test_sharper_scores_higher(self):
        low = scoring.compute_score(make_record("a.ARW", 30.0))
        high = scoring.compute_score(make_record("b.ARW", 80.0))
        assert high > low

    def test_eye_source_trusts_roi_more_than_tile_source(self):
        """눈을 잡았으면 그 안의 선명도가 곧 판정 근겁니다.

        ROI 선명도는 높고 전체 프레임은 낮은 경우 — 배경 흐린 인물 사진이
        정확히 이 모양입니다. 눈 기준이면 높게, 타일 추정이면 덜 신뢰합니다.
        """
        eye = make_record("a.ARW", 90.0, frame_sharpness=30.0, source=FocusSource.EYE)
        tile = make_record("b.ARW", 90.0, frame_sharpness=30.0, source=FocusSource.TILE)
        assert scoring.compute_score(eye) > scoring.compute_score(tile)

    def test_clipped_highlights_penalized(self):
        clean = make_record("a.ARW", 70.0, clipped_highlights=0.0)
        blown = make_record("b.ARW", 70.0, clipped_highlights=0.9)
        assert scoring.compute_score(blown) < scoring.compute_score(clean)

    def test_black_frame_penalized(self):
        """렌즈캡 컷, 오발 셔터."""
        normal = make_record("a.ARW", 60.0, mean_luma=120.0)
        black = make_record("b.ARW", 60.0, mean_luma=2.0)
        assert scoring.compute_score(black) < scoring.compute_score(normal)


class TestGradeRecords:
    def test_group_best_is_never_rejected(self):
        """전 그룹이 흐릿해도 최고 컷은 남긴다 — 장면 유실 방지."""
        records = [make_record(f"{i}.ARW", 5.0, group_id=0) for i in range(5)]
        scoring.grade_records(records)
        best = min(records, key=lambda r: r.group_rank)
        assert best.grade == Grade.KEEP

    def test_every_group_yields_a_keep(self):
        """장면마다 최소 한 장은 살아남아야 합니다."""
        records = []
        for group in range(6):
            for i in range(4):
                records.append(make_record(f"g{group}_{i}.ARW", 10.0 + i, group_id=group))
        scoring.grade_records(records)

        for group in range(6):
            members = [r for r in records if r.group_id == group]
            assert any(r.grade == Grade.KEEP for r in members), f"그룹 {group} 전멸"

    def test_keep_per_group_respected(self):
        records = [make_record(f"{i}.ARW", 90.0 - i * 20, group_id=0) for i in range(4)]
        scoring.grade_records(records, ScoreConfig(keep_per_group=2, keep_above=999.0))
        keeps = [r for r in records if r.grade == Grade.KEEP]
        assert len(keeps) == 2
        assert {r.path.name for r in keeps} == {"0.ARW", "1.ARW"}

    def test_high_absolute_score_keeps_regardless_of_rank(self):
        """그룹 전체가 잘 나왔으면 전부 살린다 (절대 임계 모드)."""
        records = [make_record(f"{i}.ARW", 95.0, group_id=0) for i in range(5)]
        scoring.grade_records(
            records,
            ScoreConfig(keep_above=35.0, keep_per_group=1, target_keep_ratio=None),
        )
        assert all(r.grade == Grade.KEEP for r in records)

    def test_failed_records_are_rejected(self):
        records = [
            make_record("good.ARW", 80.0, group_id=0),
            ImageRecord(path=Path("bad.ARW"), error="PreviewError: 손상", group_id=1),
        ]
        scoring.grade_records(records)
        assert records[1].grade == Grade.REJECT
        # 근거는 키와 수치로 옵니다. 사용자가 보는 것은 렌더된 문장입니다.
        from arw_selector.core.reason_text import render

        assert "손상" in render(records[1].reasons[0])

    def test_low_ranked_blurry_frames_are_rejected(self):
        records = [make_record("sharp.ARW", 95.0, group_id=0)] + [
            make_record(f"blur{i}.ARW", 3.0, group_id=0) for i in range(4)
        ]
        scoring.grade_records(records, ScoreConfig(keep_per_group=1, reject_below=30.0))
        assert records[0].grade == Grade.KEEP
        assert all(r.grade == Grade.REJECT for r in records[1:])

    def test_uniformly_good_batch_is_not_forced_to_reject(self):
        """백분위만 쓰면 전부 잘 나온 배치에서도 기계적으로 하위를 버립니다.

        절대 임계와 함께 봐야 이런 배치를 지킬 수 있습니다.
        """
        records = [make_record(f"{i}.ARW", 88.0 + i * 0.1, group_id=i) for i in range(20)]
        scoring.grade_records(records, ScoreConfig(reject_below=30.0, reject_percentile=15.0))
        assert not any(r.grade == Grade.REJECT for r in records)

    def test_worse_duplicate_in_same_burst_is_rejected(self):
        """연사에서 더 나은 컷이 있으면 중복은 볼 이유가 없습니다.

        전역 점수로는 중간이어도 같은 순간의 더 나은 컷이 있으면 버립니다.
        이것이 review 더미를 실질적으로 줄여 줍니다.
        """
        records = [
            make_record("best.ARW", 80.0, group_id=0),
            make_record("dup.ARW", 55.0, group_id=0),  # 선명도 25점 열세
        ]
        # 선명도 항이 절반이라 실제 점수 격차는 12.5점입니다
        scoring.grade_records(
            records,
            ScoreConfig(keep_above=999.0, reject_below=0.0, reject_percentile=0.0,
                        reject_below_group_best=10.0),
        )
        assert records[0].grade == Grade.KEEP
        assert records[1].grade == Grade.REJECT
        assert any(r.key == scoring.REASON_BETTER_IN_GROUP for r in records[1].reasons)

    def test_close_second_stays_in_review(self):
        """근소한 차이는 사람이 봐야 합니다 — 표정이나 구도는 점수에 안 잡힙니다."""
        records = [
            make_record("best.ARW", 80.0, group_id=0),
            make_record("close.ARW", 75.0, group_id=0),  # 5점 열세
        ]
        scoring.grade_records(
            records,
            ScoreConfig(keep_above=999.0, reject_below=0.0, reject_percentile=0.0,
                        reject_below_group_best=20.0),
        )
        assert records[1].grade == Grade.REVIEW

    def test_group_relative_rule_never_rejects_the_best(self):
        """그룹 1등은 이 규칙보다 먼저 keep으로 확정됩니다."""
        records = [make_record(f"{i}.ARW", 100.0 - i * 30, group_id=0) for i in range(4)]
        scoring.grade_records(records, ScoreConfig(reject_below_group_best=5.0))
        best = min(records, key=lambda r: r.group_rank)
        assert best.grade == Grade.KEEP

    def test_ranks_are_assigned_within_group(self):
        records = [
            make_record("a.ARW", 10.0, group_id=0),
            make_record("b.ARW", 90.0, group_id=0),
            make_record("c.ARW", 50.0, group_id=0),
        ]
        scoring.grade_records(records)
        by_name = {r.path.name: r.group_rank for r in records}
        assert by_name == {"b.ARW": 0, "c.ARW": 1, "a.ARW": 2}

    def test_ties_break_deterministically(self):
        """같은 폴더를 두 번 돌렸을 때 결과가 달라지면 신뢰를 잃습니다."""
        first = [make_record(f"{i}.ARW", 50.0, group_id=0) for i in range(5)]
        second = [make_record(f"{i}.ARW", 50.0, group_id=0) for i in reversed(range(5))]
        scoring.grade_records(first)
        scoring.grade_records(second)
        assert {r.path.name: r.group_rank for r in first} == {
            r.path.name: r.group_rank for r in second
        }

    def test_reasons_are_populated(self):
        """근거에 ROI 종류와 얼굴 수가 실제로 담겨야 합니다.

        키만 확인하면 수치를 안 넣어도 통과합니다. 화면에 나가는 문장까지
        봅니다 — 사용자가 읽는 것이 그쪽입니다.
        """
        from arw_selector.core.reason_text import render

        records = [make_record("a.ARW", 80.0, source=FocusSource.EYE, face_count=2)]
        scoring.grade_records(records)
        reasons = records[0].reasons

        roi = next(r for r in reasons if r.key == scoring.REASON_ROI_SHARPNESS)
        assert roi.params["source"] == FocusSource.EYE.value
        assert "eye area" in render(roi)

        faces = next(r for r in reasons if r.key == scoring.REASON_FACE_COUNT)
        assert faces.params["count"] == 2
        assert "2" in render(faces)

    def test_empty_input(self):
        assert scoring.grade_records([]) == []


class TestScoreWeights:
    """가중치가 실제로 점수를 바꾸는지.

    이 테스트가 없어서 weight_roi_sharpness 같은 설정이 정의만 되고
    아무도 읽지 않는 상태로 오래 남아 있었습니다. YAML에서 값을 바꿔도
    아무 일이 없었고, 사용자는 그걸 알 방법이 없었습니다.
    """

    def _record(self, roi: float, frame: float, source=FocusSource.EYE, **kwargs):
        return make_record("a.ARW", roi, frame_sharpness=frame, source=source, **kwargs)

    #: 얼굴 항목을 전부 0으로 만든 설정. 가중치만 떼어 보고 싶을 때 씁니다.
    #:
    #: 예전에는 face_priority=False로 껐습니다. 지금은 모드를 끄면 선명도
    #: 배수까지 1.0으로 바뀌므로(scoring.sharpness_scale), 그 방법을 쓰면
    #: 보려던 것과 상관없이 기대값이 두 배가 됩니다. 모드는 켠 채로 값만
    #: 0으로 둡니다.
    _NO_FACE_TERMS = dict(
        face_priority=True, bonus_face=0.0, bonus_face_size=0.0,
        bonus_eye=0.0, bonus_focus_on_face=0.0, penalty_no_face=0.0,
        penalty_face_defocus=0.0, bonus_eyes_open=0.0, penalty_eyes_closed=0.0,
    )

    def test_trust_shifts_weight_between_roi_and_frame(self):
        # 얼굴 정책은 여기서 볼 것이 아닙니다. 가중치 계산만 떼어 봅니다.
        record = self._record(roi=90.0, frame=10.0)

        roi_heavy = scoring.compute_score(
            record, ScoreConfig(trust_eye=1.0, **self._NO_FACE_TERMS))
        frame_heavy = scoring.compute_score(
            record, ScoreConfig(trust_eye=0.0, **self._NO_FACE_TERMS))

        scale = scoring.SHARPNESS_SCALE
        assert roi_heavy == pytest.approx(90.0 * scale, abs=0.5)
        assert frame_heavy == pytest.approx(10.0 * scale, abs=0.5)

    def test_each_source_uses_its_own_trust(self):
        """소스별로 다른 신뢰도가 적용돼야 합니다."""
        config = ScoreConfig(
            trust_eye=1.0, trust_face=0.0, trust_tile=1.0, trust_frame=0.0,
            **self._NO_FACE_TERMS,
        )
        scale = scoring.SHARPNESS_SCALE
        for source, expected in (
            (FocusSource.EYE, 90.0 * scale),
            (FocusSource.FACE, 10.0 * scale),
            (FocusSource.TILE, 90.0 * scale),
            (FocusSource.FRAME, 10.0 * scale),
        ):
            record = self._record(roi=90.0, frame=10.0, source=source)
            assert scoring.compute_score(record, config) == pytest.approx(
                expected, abs=0.5
            ), f"{source} 신뢰도가 반영되지 않았습니다"

    def test_roi_trust_helper_matches_config(self):
        config = ScoreConfig(trust_eye=0.9, trust_tile=0.2)
        assert scoring.roi_trust(FocusSource.EYE, config) == 0.9
        assert scoring.roi_trust(FocusSource.TILE, config) == 0.2

    def test_face_bonus_applies_only_with_faces(self):
        with_face = self._record(roi=50.0, frame=50.0, face_count=2)
        without = self._record(roi=50.0, frame=50.0, face_count=0)
        # bonus_face만 떼어 봅니다. 눈 보너스도 꺼야 합니다 — 얼굴 0개면
        # 크기 가중이 0이라 눈 보너스가 한쪽에만 붙고, 그 차이가 얼굴 보너스
        # 차이에 섞입니다. 얼굴 우선 모드의 가감점도 마저 꺼야 합니다:
        # 얼굴 유무로 penalty_no_face / bonus_focus_on_face가 갈립니다.
        config = ScoreConfig(**{**self._NO_FACE_TERMS, "bonus_face": 10.0})

        assert scoring.compute_score(with_face, config) - scoring.compute_score(
            without, config
        ) == pytest.approx(10.0, abs=0.5)

    def test_eye_bonus_only_for_eye_source(self):
        # bonus_eye만 떼어 봅니다 — 나머지 얼굴 항목은 전부 0입니다
        config = ScoreConfig(**{**self._NO_FACE_TERMS, "bonus_eye": 8.0})
        eye = self._record(roi=50.0, frame=50.0, source=FocusSource.EYE)
        tile = self._record(roi=50.0, frame=50.0, source=FocusSource.TILE)
        # 신뢰도 차이를 없애 보너스만 비교합니다
        config.trust_eye = config.trust_tile = 0.5

        assert scoring.compute_score(eye, config) - scoring.compute_score(
            tile, config
        ) == pytest.approx(8.0, abs=0.5)

    def test_face_size_bonus_scales_with_area(self):
        config = ScoreConfig(bonus_face_size=10.0)
        small = self._record(roi=50.0, frame=50.0, face_count=1, face_area_ratio=0.01)
        large = self._record(roi=50.0, frame=50.0, face_count=1, face_area_ratio=0.10)

        assert scoring.compute_score(large, config) > scoring.compute_score(small, config)

    def test_highlight_penalty_is_configurable(self):
        record = self._record(roi=80.0, frame=80.0, clipped_highlights=0.9)

        strong = scoring.compute_score(
            record, ScoreConfig(penalty_highlight_clip=40.0, max_clipped_highlights=0.1)
        )
        none = scoring.compute_score(record, ScoreConfig(penalty_highlight_clip=0.0))

        assert none > strong

    def test_shadow_penalty_stays_small(self):
        """섀도우 감점은 작아야 합니다.

        무대·야간 촬영은 의도적으로 검은 부분이 많습니다. 크게 잡으면
        멀쩡한 컷이 무더기로 깎입니다 — 하이라이트 날아감과 달리
        '검은 부분이 많다'는 것 자체는 결함이 아닙니다.
        """
        crushed = self._record(roi=70.0, frame=70.0, clipped_shadows=0.9)
        clean = self._record(roi=70.0, frame=70.0, clipped_shadows=0.0)
        gap = (scoring.compute_score(clean, ScoreConfig())
               - scoring.compute_score(crushed, ScoreConfig()))
        assert 0.0 <= gap <= 5.0, f"섀도우 감점이 {gap:.1f}점으로 과합니다"

    def test_shadow_penalty_applies_when_enabled(self):
        record = self._record(roi=70.0, frame=70.0, clipped_shadows=0.9)
        config = ScoreConfig(penalty_shadow_clip=30.0, max_clipped_shadows=0.2)
        assert scoring.compute_score(record, config) < scoring.compute_score(
            record, ScoreConfig()
        )

    def test_extreme_luma_penalty_is_configurable(self):
        record = self._record(roi=70.0, frame=70.0, mean_luma=2.0)

        default = scoring.compute_score(record, ScoreConfig())
        disabled = scoring.compute_score(record, ScoreConfig(penalty_extreme_luma=0.0))

        assert disabled > default

    def test_score_stays_bounded_with_extreme_weights(self):
        record = self._record(roi=100.0, frame=100.0, face_count=5, face_area_ratio=0.5)
        config = ScoreConfig(bonus_face=30.0, bonus_eye=30.0, bonus_face_size=30.0)
        assert 0.0 <= scoring.compute_score(record, config) <= 100.0

    def test_weights_survive_config_round_trip(self, tmp_path):
        """파일로 저장하고 다시 읽어도 가중치가 유지돼야 합니다."""
        from arw_selector.core.config import Config

        config = Config()
        config.score.trust_eye = 0.9
        config.score.bonus_face = 12.5
        config.score.penalty_shadow_clip = 7.0

        path = tmp_path / "기준.yaml"
        path.write_text(config.to_yaml(), encoding="utf-8")
        restored = Config.load(path)

        assert restored.score.trust_eye == 0.9
        assert restored.score.bonus_face == 12.5
        assert restored.score.penalty_shadow_clip == 7.0


class TestKeepGuaranteeOff:
    """장면당 keep 보장을 끄면 keep 없는 장면이 생깁니다."""

    def _batch(self, groups: int = 4, per_group: int = 4):
        records = []
        for g in range(groups):
            for i in range(per_group):
                records.append(
                    make_record(f"g{g}_{i}.ARW", 70.0 - g * 15 - i, group_id=g)
                )
        return records

    def test_zero_disables_guarantee(self):
        records = self._batch()
        scoring.grade_records(
            records,
            ScoreConfig(keep_per_group=0, target_keep_ratio=None, keep_above=65.0),
        )
        empty = scoring.groups_without_keep(records)
        assert empty, "보장을 껐는데도 모든 장면에 keep이 생겼다"

    def test_one_still_guarantees(self):
        records = self._batch()
        scoring.grade_records(records, ScoreConfig(keep_per_group=1))
        assert scoring.groups_without_keep(records) == set()

    def test_groups_without_keep_lists_them(self):
        records = self._batch(groups=3, per_group=2)
        scoring.grade_records(
            records,
            ScoreConfig(keep_per_group=0, target_keep_ratio=None, keep_above=65.0),
        )
        empty = scoring.groups_without_keep(records)
        for group in empty:
            members = [r for r in records if r.group_id == group]
            assert all(r.final_grade is not Grade.KEEP for r in members)

    def test_records_in_groups_without_keep(self):
        records = self._batch(groups=3, per_group=2)
        scoring.grade_records(
            records,
            ScoreConfig(keep_per_group=0, target_keep_ratio=None, keep_above=65.0),
        )
        listed = scoring.records_in_groups_without_keep(records)
        empty = scoring.groups_without_keep(records)
        assert {r.group_id for r in listed} == empty

    def test_floor_is_zero_when_guarantee_off(self):
        records = self._batch()
        config = ScoreConfig(keep_per_group=0)
        scoring.grade_records(records, config)
        assert scoring.achievable_keep_floor(records, config) == 0.0

    def test_manual_grade_counts_as_keep(self):
        """사용자가 직접 keep으로 올린 장면은 비어 있지 않습니다."""
        records = self._batch(groups=2, per_group=2)
        scoring.grade_records(
            records,
            ScoreConfig(keep_per_group=0, target_keep_ratio=None, keep_above=999.0),
        )
        assert len(scoring.groups_without_keep(records)) == 2

        records[0].manual_grade = Grade.KEEP
        assert len(scoring.groups_without_keep(records)) == 1


class TestMinKeepScore:
    """품질 하한 — 장면 전체가 기준 미달이면 아무것도 뽑지 않습니다."""

    def test_zero_preserves_every_scene(self):
        """기본값 0에서는 기존 보장이 그대로 유지됩니다."""
        records = [make_record(f"{i}.ARW", 5.0, group_id=0) for i in range(4)]
        scoring.grade_records(records, ScoreConfig(min_keep_score=0.0))
        assert any(r.grade == Grade.KEEP for r in records)

    def test_bad_scene_yields_nothing(self):
        """전부 흔들린 장면까지 무리하게 건져 올리지 않습니다.

        하한을 숫자로 박아 두면 보너스 기본값을 올릴 때마다 깨집니다.
        보려는 것은 "이 장면 최고점보다 하한이 높으면 아무것도 안 나온다"이지
        50이라는 숫자가 아닙니다.
        """
        records = [make_record(f"{i}.ARW", 20.0, group_id=0) for i in range(4)]
        best = max(scoring.compute_score(r) for r in records)
        scoring.grade_records(records, ScoreConfig(min_keep_score=best + 1.0))
        assert not any(r.grade == Grade.KEEP for r in records)

    def test_good_scene_still_kept(self):
        records = [make_record(f"{i}.ARW", 80.0 - i * 5, group_id=0) for i in range(4)]
        scoring.grade_records(records, ScoreConfig(min_keep_score=50.0))
        assert sum(1 for r in records if r.grade == Grade.KEEP) >= 1

    def test_only_qualifying_scenes_survive(self):
        records = []
        for i in range(5):
            records.append(make_record(f"good{i}.ARW", 75.0, group_id=i))
        for i in range(5):
            records.append(make_record(f"bad{i}.ARW", 15.0, group_id=10 + i))

        scoring.grade_records(records, ScoreConfig(min_keep_score=50.0))
        kept_groups = {r.group_id for r in records if r.grade == Grade.KEEP}
        assert kept_groups == {0, 1, 2, 3, 4}

    def test_dropped_groups_counted(self):
        records = [make_record(f"g{i}.ARW", 75.0 if i < 3 else 15.0, group_id=i)
                   for i in range(6)]
        config = ScoreConfig(min_keep_score=50.0)
        scoring.grade_records(records, config)
        assert scoring.dropped_groups(records, config) == 3

    def test_floor_accounts_for_dropped_groups(self):
        records = [make_record(f"g{i}.ARW", 75.0 if i < 2 else 15.0, group_id=i)
                   for i in range(10)]
        config = ScoreConfig(min_keep_score=50.0)
        scoring.grade_records(records, config)
        assert scoring.achievable_keep_floor(records, config) == pytest.approx(0.2)


class TestTargetKeepRatio:
    """목표 비율을 주면 배치 점수 분포에서 임계값을 역산합니다."""

    def _spread_batch(self, n_groups: int = 20, per_group: int = 5):
        records = []
        for g in range(n_groups):
            for i in range(per_group):
                records.append(
                    make_record(f"g{g:02d}_{i}.ARW", 95.0 - g * 2 - i * 3, group_id=g)
                )
        return records

    def test_hits_target_ratio(self):
        records = self._spread_batch()  # 100장, 20그룹 -> 하한 20%
        scoring.grade_records(records, ScoreConfig(target_keep_ratio=0.30))
        keeps = sum(1 for r in records if r.grade == Grade.KEEP)
        assert keeps == pytest.approx(30, abs=1)

    def test_different_score_scales_give_same_ratio(self):
        """절대 점수는 배치마다 의미가 다릅니다. 비율은 유지돼야 합니다.

        조명이나 렌즈가 바뀌면 점수 분포가 전체가 이동하는데, 그때도
        결과 비율이 같아야 목표 지정이 의미가 있습니다.
        """
        bright = self._spread_batch()
        dim = [
            make_record(r.path.name, r.focus.sharpness * 0.5, group_id=r.group_id)
            for r in bright
        ]
        config = ScoreConfig(target_keep_ratio=0.30)
        scoring.grade_records(bright, config)
        scoring.grade_records(dim, config)

        assert sum(1 for r in bright if r.grade == Grade.KEEP) == pytest.approx(
            sum(1 for r in dim if r.grade == Grade.KEEP), abs=1
        )

    def test_cannot_go_below_group_floor(self):
        """목표가 하한보다 낮으면 하한이 결과가 됩니다 — 장면은 못 버립니다."""
        records = self._spread_batch(n_groups=20, per_group=5)  # 하한 20%
        scoring.grade_records(records, ScoreConfig(target_keep_ratio=0.05))
        keeps = sum(1 for r in records if r.grade == Grade.KEEP)
        assert keeps == 20
        assert scoring.achievable_keep_floor(records) == pytest.approx(0.20)

    def test_every_group_still_survives_at_tight_target(self):
        """비율을 조여도 장면 유실은 없어야 합니다."""
        records = self._spread_batch(n_groups=20, per_group=5)
        scoring.grade_records(records, ScoreConfig(target_keep_ratio=0.05))
        for group in range(20):
            members = [r for r in records if r.group_id == group]
            assert any(r.grade == Grade.KEEP for r in members), f"그룹 {group} 전멸"

    def test_none_falls_back_to_absolute_threshold(self):
        records = self._spread_batch()
        scoring.grade_records(
            records, ScoreConfig(target_keep_ratio=None, keep_above=70.0)
        )
        for record in records:
            if record.score >= 70.0:
                assert record.grade == Grade.KEEP

    def test_floor_of_empty_batch(self):
        assert scoring.achievable_keep_floor([]) == 0.0


class TestFacePriority:
    """얼굴 우선 모드 — 초점이 얼굴이 아니라 배경에 맞은 컷을 가려냅니다."""

    def _defocused(self, confidence: float = 0.9, background: float = 90.0):
        # 얼굴 ROI는 흐리고(40) 배경은 쨍한(90) 컷. frame=roi로 두어 신뢰도
        # 변화가 기본 점수를 흔들지 않게 하고, 감점만 분리해 봅니다.
        return make_record(
            "a.ARW", 40.0, frame_sharpness=40.0, source=FocusSource.EYE,
            face_count=1, face_confidence=confidence, background_sharpness=background,
        )

    def test_background_focus_is_penalized(self):
        """감점 자체를 켜고 꺼서 비교합니다.

        모드를 껐다 켜서 비교하면 안 됩니다 — 모드는 선명도 배수까지 함께
        바꾸므로(scoring.sharpness_scale) 두 점수가 다른 척도가 됩니다.
        """
        record = self._defocused()
        penalised = scoring.compute_score(record, ScoreConfig(face_priority=True))
        clean = scoring.compute_score(
            record, ScoreConfig(face_priority=True, penalty_face_defocus=0.0))
        assert penalised < clean

    def test_no_face_terms_when_mode_disabled(self):
        """모드를 끄면 얼굴 항목이 전부 빠지고 선명도만 남습니다."""
        record = self._defocused()
        off = scoring.compute_score(record, ScoreConfig(face_priority=False))
        # roi=frame=40이라 신뢰도와 무관하게 40, 배수는 모드 꺼짐 쪽입니다.
        assert off == pytest.approx(
            40.0 * scoring.SHARPNESS_SCALE_NO_FACE, abs=0.5)

    def test_penalty_scales_with_deficit(self):
        small_gap = scoring.compute_score(
            self._defocused(background=50.0), ScoreConfig(face_priority=True)
        )
        big_gap = scoring.compute_score(
            self._defocused(background=95.0), ScoreConfig(face_priority=True)
        )
        assert big_gap < small_gap

    def test_penalty_weighted_by_confidence(self):
        unsure = scoring.compute_score(
            self._defocused(confidence=0.6), ScoreConfig(face_priority=True)
        )
        sure = scoring.compute_score(
            self._defocused(confidence=1.0), ScoreConfig(face_priority=True)
        )
        assert sure < unsure  # 확신할수록 더 깎는다

    def test_sharp_face_is_not_penalized(self):
        """얼굴이 배경보다 선명한 정상 인물 컷은 감점되지 않습니다."""
        record = make_record(
            "a.ARW", 90.0, frame_sharpness=90.0, source=FocusSource.EYE,
            face_count=1, face_confidence=0.95, background_sharpness=30.0,
        )
        config = ScoreConfig(face_priority=True)
        assert scoring._face_defocus_penalty(record.focus, config) == 0.0
        # 초점이 얼굴에 맞았으므로 오히려 가산이 붙어야 합니다.
        keys = {line.key for line in scoring.score_breakdown(record, config)[0]}
        assert scoring.LINE_FACE_DEFOCUS not in keys
        assert scoring.LINE_FOCUS_ON_FACE in keys

    def test_smooth_background_is_never_penalized(self):
        """background_sharpness=0은 '배경이 매끈함'(좋은 인물)이라는 유효한
        측정이므로, frame_sharpness가 아무리 높아도 감점하면 안 됩니다.

        예전 캐시(v2)도 0이라 감점되지 않고, v3 재분석 뒤 실측으로 판단합니다.
        """
        record = make_record(
            "a.ARW", 40.0, frame_sharpness=90.0, source=FocusSource.EYE,
            face_count=1, face_confidence=1.0,  # background_sharpness 기본 0
        )
        # 핵심: 매끈한 배경(0)에는 초점 빗나감 감점이 절대 붙지 않는다.
        # (전체 점수는 신뢰도 하한이 별도로 움직이므로 여기서 비교하지 않는다.)
        assert scoring._face_defocus_penalty(record.focus, ScoreConfig(face_priority=True)) == 0.0

    def test_no_face_is_lowered_by_the_mode(self):
        """예전에는 '얼굴이 없으면 모드가 영향을 주지 않는다'가 규칙이었습니다.

        그게 결함이었습니다. 얼굴 컷은 (부드러운) 얼굴 ROI로 재고 배경초점
        감점까지 받는데 얼굴 없는 컷은 프레임 선명도를 그대로 쓰니, 얼굴
        우선 모드에서 얼굴 없는 컷이 오히려 유리했습니다.
        """
        record = make_record(
            "a.ARW", 40.0, frame_sharpness=90.0, source=FocusSource.TILE,
            face_count=0, background_sharpness=90.0,
        )
        # 감점만 켜고 꺼서 봅니다. 모드를 껐다 켜면 선명도 배수까지 바뀌어
        # 두 점수가 다른 척도가 됩니다.
        penalised = scoring.compute_score(record, ScoreConfig(face_priority=True))
        clean = scoring.compute_score(
            record, ScoreConfig(face_priority=True, penalty_no_face=0.0))

        assert penalised < clean, "얼굴 우선 모드인데 얼굴 없는 컷이 그대로입니다"
        assert clean - penalised == pytest.approx(
            ScoreConfig().penalty_no_face, abs=0.5)

    def test_face_priority_does_not_override_trust(self):
        """얼굴 우선 모드가 신뢰도 설정을 덮어쓰면 안 됩니다.

        예전에는 이 모드가 눈 0.85 / 얼굴 0.75로 하한을 걸었습니다. 그래서
        패널에서 신뢰도를 0까지 내려도 점수가 한 자리도 움직이지 않았고,
        사용자는 슬라이더가 죽은 것인지 값이 원래 그런 것인지 알 수 없었습니다.
        얼굴 우선 정책은 가감점으로만 표현하고, 가중치는 설정에 맡깁니다.
        """
        focus_eye = self._defocused().focus
        for priority in (True, False):
            config = ScoreConfig(trust_eye=0.4, face_priority=priority)
            assert scoring._effective_trust(focus_eye, config) == pytest.approx(0.4)

    def test_defocus_reason_is_reported(self):
        records = [self._defocused()]
        scoring.grade_records(records, ScoreConfig(face_priority=True))
        assert any(reason.key == scoring.REASON_FACE_DEFOCUS
                   for reason in records[0].reasons)

    def test_face_priority_survives_config_round_trip(self, tmp_path):
        from arw_selector.core.config import Config

        config = Config()
        config.score.face_priority = False
        config.score.penalty_face_defocus = 33.0
        path = tmp_path / "기준.yaml"
        path.write_text(config.to_yaml(), encoding="utf-8")
        restored = Config.load(path)
        assert restored.score.face_priority is False
        assert restored.score.penalty_face_defocus == 33.0


class TestFacePriorityNeedsAFace:
    """얼굴 우선 모드인데 얼굴 없는 컷이 유리하면 모드가 거꾸로 도는 겁니다.

    실측 배경 (A6700 2845장): 얼굴 없는 컷의 점수 중앙값이 59.0으로 초점이
    얼굴에 맞은 컷의 47.6보다 높았고, 절대 keep 임계를 넘는 비율이 29.7%
    대 6.9%로 4배였습니다. 얼굴 컷은 (대개 더 부드러운) 얼굴 ROI로 재고
    배경초점 감점까지 받는데, 얼굴 없는 컷은 프레임 선명도를 그대로 쓰기
    때문입니다. DSC03099.ARW가 얼굴 0개로 88점 keep이 된 것이 그 사례입니다.
    """

    def _config(self, **overrides):
        return ScoreConfig(face_priority=True, **overrides)

    def test_no_face_scores_below_focused_face(self):
        """같은 선명도라면 초점이 얼굴에 맞은 쪽이 높아야 합니다."""
        on_face = make_record(
            "face.ARW", 70.0, frame_sharpness=70.0,
            source=FocusSource.FACE, face_count=1,
        )
        no_face = make_record(
            "none.ARW", 70.0, frame_sharpness=70.0,
            source=FocusSource.TILE, face_count=0,
        )
        config = self._config()

        assert scoring.compute_score(on_face, config) > scoring.compute_score(no_face, config)

    def test_no_face_cannot_auto_keep(self):
        """절대 임계는 '볼 것도 없이 건진 컷'이라는 뜻입니다.

        얼굴이 없으면 그 판단의 근거가 없으므로 사람이 봐야 합니다.
        """
        records = [
            make_record("sharp_noface.ARW", 99.0, frame_sharpness=99.0,
                        source=FocusSource.TILE, face_count=0, group_id=0),
            make_record("best.ARW", 99.5, frame_sharpness=99.5,
                        source=FocusSource.EYE, face_count=1, group_id=0),
        ]
        config = self._config(keep_above=70.0, keep_per_group=1,
                              target_keep_ratio=None)
        for record in records:
            record.score = scoring.compute_score(record, config)
        scoring.grade_records(records, config)

        assert records[0].grade is not Grade.KEEP, "얼굴 없는 컷이 자동 keep 됐습니다"
        assert records[1].grade is Grade.KEEP

    def test_no_face_still_wins_its_scene(self):
        """장면에 얼굴 컷이 하나도 없으면 그 장면도 한 장은 나와야 합니다.

        장면이 통째로 사라지는 것은 되돌리기 어려운 실패입니다.
        """
        records = [
            make_record("a.ARW", 60.0, source=FocusSource.TILE,
                        face_count=0, group_id=7),
            make_record("b.ARW", 40.0, source=FocusSource.TILE,
                        face_count=0, group_id=7),
        ]
        config = self._config(keep_above=70.0, keep_per_group=1,
                              target_keep_ratio=None)
        for record in records:
            record.score = scoring.compute_score(record, config)
        scoring.grade_records(records, config)

        assert records[0].grade is Grade.KEEP

    def test_landscape_mode_is_unaffected(self):
        """얼굴 우선을 끄면 얼굴 유무로 손해 보지 않아야 합니다."""
        no_face = make_record("none.ARW", 90.0, frame_sharpness=90.0,
                              source=FocusSource.TILE, face_count=0)
        config = ScoreConfig(face_priority=False, keep_above=35.0,
                             target_keep_ratio=None)
        no_face.score = scoring.compute_score(no_face, config)
        scoring.grade_records([no_face], config)

        assert no_face.score == pytest.approx(
            90.0 * scoring.SHARPNESS_SCALE_NO_FACE, abs=1.0)
        assert no_face.grade is Grade.KEEP

    def test_face_present_but_focus_elsewhere_gets_no_bonus(self):
        """얼굴이 있는 것과 그 얼굴에 초점이 맞은 것은 다릅니다."""
        focused = make_record("focused.ARW", 60.0, frame_sharpness=60.0,
                              source=FocusSource.FACE, face_count=1)
        elsewhere = make_record("elsewhere.ARW", 60.0, frame_sharpness=60.0,
                                source=FocusSource.TILE, face_count=1)
        config = self._config()

        assert scoring.compute_score(focused, config) > scoring.compute_score(elsewhere, config)


class TestSummarize:
    def test_counts_by_grade(self):
        records = [make_record(f"{i}.ARW", 95.0, group_id=i) for i in range(3)]
        scoring.grade_records(records)
        summary = scoring.summarize(records)
        assert sum(summary.values()) == 3
        assert summary["keep"] == 3

    def test_manual_grade_overrides_automatic(self):
        """사용자가 직접 내린 판정이 항상 이깁니다."""
        records = [make_record("a.ARW", 95.0, group_id=0)]
        scoring.grade_records(records)
        assert records[0].grade == Grade.KEEP

        records[0].manual_grade = Grade.REJECT
        assert scoring.summarize(records)["reject"] == 1
        assert scoring.summarize(records)["keep"] == 0
