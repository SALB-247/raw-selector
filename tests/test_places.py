"""GPS 장소 묶기와 장소별 폴더 내보내기.

실사진에는 GPS가 없어서(A6700 300장 중 0장) 합성 좌표로 검증합니다.
좌표 계산 자체는 값만 맞으면 되는 순수 함수라 합성으로 충분합니다.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from arw_selector.core import places as places_mod
from arw_selector.core.export import NO_PLACE_FOLDER, build_plan
from arw_selector.core.export_options import ExportFormat, ExportOptions
from arw_selector.core.raw_io import RawMetadata
from arw_selector.core.types import Grade, ImageRecord

# 서울시청 / 광화문(약 900m) / 부산시청
SEOUL = (37.5665, 126.9780)
GWANGHWAMUN = (37.5759, 126.9769)
BUSAN = (35.1798, 129.0750)


def _record(name: str, coords=None, minutes: int = 0) -> ImageRecord:
    meta = RawMetadata(
        path=Path(name),
        capture_time=datetime(2026, 7, 22, 10, 0) + timedelta(minutes=minutes),
        latitude=coords[0] if coords else None,
        longitude=coords[1] if coords else None,
    )
    record = ImageRecord(path=Path(name), metadata=meta)
    record.grade = Grade.KEEP
    return record


# ------------------------------------------------------- 거리


def test_haversine_matches_known_distance():
    """서울시청↔부산시청은 약 325km입니다."""
    distance = places_mod.haversine_m(*SEOUL, *BUSAN)
    assert 320_000 < distance < 330_000


def test_haversine_is_symmetric_and_zero_at_same_point():
    assert places_mod.haversine_m(*SEOUL, *SEOUL) == pytest.approx(0.0, abs=1e-6)
    assert places_mod.haversine_m(*SEOUL, *BUSAN) == pytest.approx(
        places_mod.haversine_m(*BUSAN, *SEOUL), rel=1e-9)


def test_longitude_shrinks_with_latitude():
    """평면 근사를 쓰면 이 차이가 사라집니다 — 고위도에서 오차가 2배입니다."""
    at_equator = places_mod.haversine_m(0.0, 0.0, 0.0, 1.0)
    at_sixty = places_mod.haversine_m(60.0, 0.0, 60.0, 1.0)
    assert at_sixty == pytest.approx(at_equator * 0.5, rel=0.01)


# ------------------------------------------------------- 묶기


def test_nearby_shots_become_one_place():
    records = [_record(f"a{i}.ARW", SEOUL, i) for i in range(4)]
    result = places_mod.assign_places(records)
    assert len(result) == 1
    assert {r.place_id for r in records} == {1}


def test_far_shots_become_separate_places():
    records = ([_record(f"a{i}.ARW", SEOUL, i) for i in range(3)]
               + [_record(f"b{i}.ARW", BUSAN, 100 + i) for i in range(3)])
    result = places_mod.assign_places(records)
    assert len(result) == 2
    assert records[0].place_id != records[-1].place_id


def test_nine_hundred_metres_is_a_different_place():
    """기본 반경 250m — 같은 도심의 다른 장소가 합쳐지면 안 됩니다."""
    records = ([_record(f"a{i}.ARW", SEOUL, i) for i in range(3)]
               + [_record(f"b{i}.ARW", GWANGHWAMUN, 30 + i) for i in range(3)])
    assert len(places_mod.assign_places(records)) == 2


def test_records_without_gps_get_no_place():
    """위치를 모르는 것과 그 장소에서 찍은 것은 다릅니다."""
    records = [_record("a.ARW", SEOUL, 0), _record("b.ARW", SEOUL, 1),
               _record("c.ARW", None, 2)]
    places_mod.assign_places(records)
    assert records[2].place_id is None


def test_single_shot_in_transit_is_not_a_place():
    """이동 중 한 장까지 폴더를 만들면 폴더가 수십 개가 됩니다."""
    records = ([_record(f"a{i}.ARW", SEOUL, i) for i in range(3)]
               + [_record("mid.ARW", BUSAN, 50)]
               + [_record(f"c{i}.ARW", SEOUL, 100 + i) for i in range(3)])
    result = places_mod.assign_places(records)
    assert all(len(p.records) >= places_mod.MIN_CLUSTER for p in result)
    mid = next(r for r in records if r.path.name == "mid.ARW")
    assert mid.place_id is None


def test_walking_across_a_venue_stays_one_place():
    """중심을 첫 장에 고정하면 행사장을 가로지를 때 갈라집니다."""
    records = [
        _record(f"a{i}.ARW", (SEOUL[0] + i * 0.0004, SEOUL[1]), i)
        for i in range(8)
    ]
    assert len(places_mod.assign_places(records)) == 1


def test_no_gps_at_all_returns_no_places():
    records = [_record(f"a{i}.ARW", None, i) for i in range(5)]
    assert places_mod.assign_places(records) == []


def test_label_encodes_hemisphere():
    place = places_mod.Place(index=3, latitude=-33.86, longitude=151.20)
    assert place.label.startswith("03_")
    assert "S" in place.label and "E" in place.label


# ------------------------------------------------------- 내보내기 폴더


def _plan(records, **kwargs):
    options = ExportOptions(subfolder_by_place=True, copy_raw=True,
                            apply_develop=False, **kwargs)
    return build_plan(records, Path("/out"), options=options)


def test_export_splits_by_place(tmp_path):
    records = []
    for i in range(3):
        path = tmp_path / f"seoul{i}.ARW"
        path.write_bytes(b"x")
        records.append(_record(str(path), SEOUL, i))
    for i in range(3):
        path = tmp_path / f"busan{i}.ARW"
        path.write_bytes(b"x")
        records.append(_record(str(path), BUSAN, 100 + i))
    places_mod.assign_places(records)

    plan = _plan(records)
    folders = {op.destination.parent.parent.name for op in plan.operations}
    assert len(folders) == 2, f"장소별로 안 나뉘었습니다: {folders}"


def test_export_puts_gps_less_shots_in_their_own_folder(tmp_path):
    records = []
    for i in range(2):
        path = tmp_path / f"here{i}.ARW"
        path.write_bytes(b"x")
        records.append(_record(str(path), SEOUL, i))
    path = tmp_path / "nowhere.ARW"
    path.write_bytes(b"x")
    records.append(_record(str(path), None, 5))
    places_mod.assign_places(records)

    plan = _plan(records)
    parents = {op.source.name: op.destination.parent.parent.name
               for op in plan.operations}
    assert parents["nowhere.ARW"] == NO_PLACE_FOLDER
    assert parents["here0.ARW"] != NO_PLACE_FOLDER


def test_place_is_outside_grade(tmp_path):
    """장소가 바깥, 등급이 안쪽 — 반대면 한 장소 결과를 한눈에 못 봅니다."""
    records = []
    for i in range(2):
        path = tmp_path / f"a{i}.ARW"
        path.write_bytes(b"x")
        records.append(_record(str(path), SEOUL, i))
    records[1].grade = Grade.REVIEW
    places_mod.assign_places(records)

    plan = _plan(records)
    for op in plan.operations:
        assert op.destination.parent.name.startswith("_")       # 등급
        assert not op.destination.parent.parent.name.startswith("_")  # 장소


def test_place_folders_are_off_by_default(tmp_path):
    path = tmp_path / "a.ARW"
    path.write_bytes(b"x")
    records = [_record(str(path), SEOUL, 0)]
    plan = build_plan(records, Path("/out"), options=ExportOptions())
    assert plan.operations
    assert NO_PLACE_FOLDER not in str(plan.operations[0].destination)


# ------------------------------------------------------- 포맷


# ------------------------------------------------------- 실파일 파싱

# GPS가 든 니콘 실파일. 환경변수로 폴더를 받고, 없으면 이 테스트만 건너뜁니다.
_NIKON = Path(os.environ.get("ARW_NIKON_SAMPLES", "_no_nikon_samples_")) / "Nikon-Z9-raw-00010.nef"


@pytest.mark.skipif(not _NIKON.is_file(), reason="GPS가 든 실파일이 없습니다")
def test_real_file_gps_is_parsed():
    """실물로만 잡히는 버그가 있습니다.

    EXIF의 도/분/초는 한 배열에 정수와 분수가 섞여 옵니다
    (`[44, 382467/10000, 0]`). 처음에는 태그 객체용 변환 함수를 원소마다
    불렀고, 예외를 삼키는 코드라 **아무 경고 없이** 위치가 통째로 사라졌습니다.
    합성 좌표 테스트는 전부 통과하고 있었습니다.
    """
    from arw_selector.core.raw_io import read_metadata

    meta = read_metadata(_NIKON)
    assert meta.has_location
    # 옐로스톤. W(서경)이라 경도가 음수여야 합니다 — ref를 안 보면 지구 반대편입니다.
    assert meta.latitude == pytest.approx(44.6374, abs=0.001)
    assert meta.longitude == pytest.approx(-110.4519, abs=0.001)


def test_tiff_is_an_export_format():
    assert ExportFormat("tiff").suffix == ".tif"


def test_heif_is_not_offered():
    """이 OpenCV 빌드에 인코더가 없어 저장이 실패합니다 — 목록에 없어야 합니다."""
    values = {f.value for f in ExportFormat}
    assert not values & {"heif", "heic", "avif"}
