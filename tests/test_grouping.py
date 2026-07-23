"""grouping.py 단위 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from arw_selector.core import grouping
from arw_selector.core.config import GroupConfig
from arw_selector.core.raw_io import RawMetadata
from arw_selector.core.types import ImageRecord

BASE_TIME = datetime(2026, 7, 12, 16, 0, 0)


def make_record(index: int, offset_seconds: float, scene_hash: int | None = 0) -> ImageRecord:
    path = Path(f"DSC{index:05d}.ARW")
    return ImageRecord(
        path=path,
        metadata=RawMetadata(
            path=path, capture_time=BASE_TIME + timedelta(seconds=offset_seconds)
        ),
        dhash=scene_hash,
    )


class TestDhash:
    def test_identical_images_hash_identically(self):
        rng = np.random.default_rng(5)
        image = rng.integers(0, 256, size=(400, 600, 3), dtype=np.uint8)
        assert grouping.dhash(image) == grouping.dhash(image.copy())

    def test_survives_exposure_shift(self):
        """연사 중 노출이 흔들려도 같은 장면으로 묶여야 합니다."""
        rng = np.random.default_rng(6)
        image = rng.integers(20, 200, size=(400, 600, 3), dtype=np.uint8)
        brighter = np.clip(image.astype(np.int16) + 30, 0, 255).astype(np.uint8)
        assert grouping.hamming_distance(
            grouping.dhash(image), grouping.dhash(brighter)
        ) <= 2

    def test_different_scenes_differ(self):
        rng = np.random.default_rng(7)
        a = rng.integers(0, 256, size=(400, 600, 3), dtype=np.uint8)
        b = rng.integers(0, 256, size=(400, 600, 3), dtype=np.uint8)
        assert grouping.hamming_distance(grouping.dhash(a), grouping.dhash(b)) > 12

    def test_hash_fits_in_64_bits(self):
        rng = np.random.default_rng(8)
        image = rng.integers(0, 256, size=(100, 100, 3), dtype=np.uint8)
        assert 0 <= grouping.dhash(image) < 2**64

    def test_accepts_grayscale(self):
        gray = np.zeros((100, 100), np.uint8)
        assert isinstance(grouping.dhash(gray), int)


class TestHammingDistance:
    def test_identical(self):
        assert grouping.hamming_distance(0b1011, 0b1011) == 0

    def test_counts_differing_bits(self):
        assert grouping.hamming_distance(0b0000, 0b1011) == 3


class TestAssignGroups:
    def test_burst_becomes_one_group(self):
        """A6700 연사는 0.1초대 간격입니다."""
        records = [make_record(i, i * 0.15) for i in range(8)]
        grouping.assign_groups(records)
        assert {r.group_id for r in records} == {0}

    def test_time_gap_splits_groups(self):
        records = [make_record(0, 0.0), make_record(1, 0.2), make_record(2, 60.0)]
        grouping.assign_groups(records, GroupConfig(time_gap_seconds=3.0))
        assert [r.group_id for r in records] == [0, 0, 1]

    def test_obvious_scene_change_splits_despite_close_timing(self):
        """시간이 붙어 있어도 화면이 완전히 뒤집히면 다른 장면입니다."""
        records = [
            make_record(0, 0.0, scene_hash=0b0000),
            make_record(1, 0.2, scene_hash=0b0001),
            make_record(2, 0.4, scene_hash=(1 << 45) - 1),  # 45비트 차이
        ]
        grouping.assign_groups(records, GroupConfig(scene_change_distance=40))
        assert records[0].group_id == records[1].group_id
        assert records[2].group_id != records[0].group_id

    def test_moderate_visual_change_does_not_split_a_burst(self):
        """망원 연사는 0.16초 사이에도 화면이 꽤 바뀝니다.

        실측에서 같은 연사의 해시 거리 p90이 25였습니다. 이 정도 변화로
        연사를 쪼개면 그룹이 파편화되어 셀렉터가 무의미해집니다.
        """
        records = [
            make_record(0, 0.0, scene_hash=0),
            make_record(1, 0.16, scene_hash=(1 << 25) - 1),  # 25비트 차이
        ]
        grouping.assign_groups(records, GroupConfig())
        assert records[0].group_id == records[1].group_id

    def test_visual_signal_is_primary_when_time_is_missing(self):
        """EXIF 시각이 없으면 화면 변화가 유일한 근거이므로 임계를 조입니다."""
        records = [
            ImageRecord(path=Path("a.ARW"), dhash=0),
            ImageRecord(path=Path("b.ARW"), dhash=(1 << 25) - 1),
        ]
        grouping.assign_groups(records, GroupConfig(no_time_hash_distance=16))
        assert records[0].group_id != records[1].group_id

    def test_max_group_size_caps_runaway_groups(self):
        records = [make_record(i, i * 0.1) for i in range(25)]
        grouping.assign_groups(records, GroupConfig(max_group_size=10))
        counts = grouping.group_counts(records)
        assert max(counts.values()) <= 10
        assert len(counts) == 3

    def test_anchor_comparison_prevents_drift_chaining(self):
        """조금씩 변하는 팬 촬영이 하나의 거대 그룹으로 이어지면 안 됩니다.

        직전 장과만 비교하면 매 장 1비트씩 달라지는 시퀀스가 무한히 이어집니다.
        앵커(그룹 첫 장)와 비교하면 누적 변화가 임계를 넘는 순간 끊깁니다.
        """
        records = [
            make_record(i, i * 0.1, scene_hash=(1 << (i * 3)) - 1) for i in range(25)
        ]
        grouping.assign_groups(
            records, GroupConfig(scene_change_distance=40, max_group_size=999)
        )
        assert len(grouping.group_counts(records)) > 1

    def test_preserves_input_order(self):
        """정렬은 내부 사정이고, 호출자가 준 순서는 유지해야 합니다."""
        records = [make_record(2, 20.0), make_record(0, 0.0), make_record(1, 10.0)]
        names = [r.path.name for r in records]
        grouping.assign_groups(records)
        assert [r.path.name for r in records] == names

    def test_groups_follow_capture_time_not_list_order(self):
        records = [make_record(2, 20.0), make_record(0, 0.0), make_record(1, 0.2)]
        grouping.assign_groups(records, GroupConfig(time_gap_seconds=3.0))
        by_name = {r.path.name: r.group_id for r in records}
        assert by_name["DSC00000.ARW"] == by_name["DSC00001.ARW"]
        assert by_name["DSC00002.ARW"] != by_name["DSC00000.ARW"]

    def test_missing_capture_time_falls_back_to_filename(self):
        """EXIF가 없는 파일도 그룹 배정에서 빠지면 안 됩니다."""
        records = [
            ImageRecord(path=Path("DSC00001.ARW"), dhash=0),
            ImageRecord(path=Path("DSC00002.ARW"), dhash=0),
        ]
        grouping.assign_groups(records)
        assert all(r.group_id is not None for r in records)

    def test_missing_dhash_still_groups_by_time(self):
        records = [make_record(0, 0.0, None), make_record(1, 0.2, None), make_record(2, 99.0, None)]
        grouping.assign_groups(records)
        assert [r.group_id for r in records] == [0, 0, 1]

    def test_empty_input(self):
        assert grouping.assign_groups([]) == []

    def test_single_record(self):
        records = [make_record(0, 0.0)]
        grouping.assign_groups(records)
        assert records[0].group_id == 0
