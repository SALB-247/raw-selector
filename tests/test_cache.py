"""cache.py 단위 테스트.

캐시가 조용히 틀린 값을 돌려주는 것이 가장 위험합니다. 캐시 미스는 그냥
느려질 뿐이지만, 잘못된 히트는 오판으로 이어집니다.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from arw_selector.core.cache import AnalysisCache, default_cache_path
from arw_selector.core.raw_io import RawMetadata
from arw_selector.core.types import FocusResult, FocusSource, ImageRecord


def make_record(path, sharpness: float = 62.5) -> ImageRecord:
    return ImageRecord(
        path=path,
        metadata=RawMetadata(
            path=path,
            capture_time=datetime(2026, 7, 12, 16, 1, 48, 517000),
            camera_model="ILCE-6700",
            lens_model="E 50-300mm F4.5-6.3 A069",
            iso=3200,
            shutter_speed=1 / 200,
            aperture=7.1,
            focal_length=290.0,
            orientation=1,
        ),
        focus=FocusResult(
            sharpness=sharpness,
            laplacian=41.2,
            tenengrad=77.9,
            source=FocusSource.EYE,
            frame_sharpness=65.4,
            roi=(2152, 1588, 177, 88),
            face_count=6,
            face_confidence=0.86,
            face_area_ratio=0.0025,
            clipped_highlights=0.001,
            clipped_shadows=0.0,
            mean_luma=109.0,
        ),
    )


@pytest.fixture
def arw(tmp_path):
    path = tmp_path / "DSC02290.ARW"
    path.write_bytes(b"fake raw payload")
    return path


@pytest.fixture
def db(tmp_path):
    return tmp_path / "cache" / "analysis.sqlite"


class TestRoundTrip:
    def test_preserves_values_and_types(self, db, arw):
        with AnalysisCache(db, "params-a") as cache:
            cache.put_many([make_record(arw)])

        with AnalysisCache(db, "params-a") as cache:
            hit = cache.get_many([arw])[arw]

        assert hit.focus.sharpness == 62.5
        assert hit.focus.frame_sharpness == 65.4
        # enum과 tuple이 문자열/리스트로 새어나오면 하위 로직이 조용히 깨집니다
        assert hit.focus.source is FocusSource.EYE
        assert isinstance(hit.focus.roi, tuple)
        assert hit.focus.roi == (2152, 1588, 177, 88)
        assert hit.metadata.capture_time == datetime(2026, 7, 12, 16, 1, 48, 517000)
        assert hit.metadata.lens_model == "E 50-300mm F4.5-6.3 A069"
        assert hit.metadata.path == arw
        assert hit.path == arw

    def test_miss_for_unknown_path(self, db, arw):
        with AnalysisCache(db, "params-a") as cache:
            assert cache.get_many([arw]) == {}

    def test_error_records_round_trip(self, db, arw):
        """실패한 장도 캐시해야 매번 재시도하지 않습니다."""
        with AnalysisCache(db, "p") as cache:
            cache.put_many([ImageRecord(path=arw, error="PreviewError: 손상")])
        with AnalysisCache(db, "p") as cache:
            hit = cache.get_many([arw])[arw]
        assert hit.error == "PreviewError: 손상"
        assert not hit.ok


class TestInvalidation:
    def test_content_change_invalidates(self, db, arw):
        with AnalysisCache(db, "p") as cache:
            cache.put_many([make_record(arw)])

        arw.write_bytes(b"different content of another length")

        with AnalysisCache(db, "p") as cache:
            assert cache.get_many([arw]) == {}

    def test_mtime_change_invalidates(self, db, arw):
        import os

        with AnalysisCache(db, "p") as cache:
            cache.put_many([make_record(arw)])

        stat = arw.stat()
        os.utime(arw, (stat.st_atime, stat.st_mtime + 120))

        with AnalysisCache(db, "p") as cache:
            assert cache.get_many([arw]) == {}

    def test_params_key_change_invalidates(self, db, arw):
        """분석 파라미터가 바뀌면 예전 결과는 의미가 없습니다."""
        with AnalysisCache(db, "params-a") as cache:
            cache.put_many([make_record(arw)])

        with AnalysisCache(db, "params-b") as cache:
            assert cache.get_many([arw]) == {}

    def test_missing_file_is_a_miss(self, db, arw):
        with AnalysisCache(db, "p") as cache:
            cache.put_many([make_record(arw)])
        arw.unlink()
        with AnalysisCache(db, "p") as cache:
            assert cache.get_many([arw]) == {}


class TestRobustness:
    def test_corrupt_payload_is_a_miss_not_a_crash(self, db, arw):
        with AnalysisCache(db, "p") as cache:
            cache.put_many([make_record(arw)])
            cache._conn.execute("UPDATE analysis SET payload = '{not json'")
            cache._conn.commit()

        with AnalysisCache(db, "p") as cache:
            assert cache.get_many([arw]) == {}

    def test_unknown_field_in_payload_is_a_miss(self, db, arw):
        """예전 버전이 남긴 필드 구성이면 조용히 다시 분석합니다."""
        with AnalysisCache(db, "p") as cache:
            cache.put_many([make_record(arw)])
            cache._conn.execute(
                """UPDATE analysis SET payload =
                   '{"metadata": null, "focus": {"sharpness": 1, "gone": 2}, "error": null}'"""
            )
            cache._conn.commit()

        with AnalysisCache(db, "p") as cache:
            assert cache.get_many([arw]) == {}

    def test_handles_more_paths_than_sqlite_variable_limit(self, tmp_path, db):
        """SQLite 기본 변수 한도(999)를 넘는 배치에서도 동작해야 합니다.

        4000장 배치가 정확히 이 경계를 넘습니다.
        """
        paths = []
        for i in range(1200):
            p = tmp_path / f"DSC{i:05d}.ARW"
            p.write_bytes(b"x" * (i % 7 + 1))
            paths.append(p)

        with AnalysisCache(db, "p") as cache:
            cache.put_many([make_record(p, sharpness=float(i)) for i, p in enumerate(paths)])

        with AnalysisCache(db, "p") as cache:
            hits = cache.get_many(paths)

        assert len(hits) == 1200
        assert hits[paths[999]].focus.sharpness == 999.0

    def test_clear_empties_cache(self, db, arw):
        with AnalysisCache(db, "p") as cache:
            cache.put_many([make_record(arw)])
            cache.clear()
            assert cache.get_many([arw]) == {}

    def test_creates_parent_directory(self, tmp_path):
        db = tmp_path / "deep" / "nested" / "analysis.sqlite"
        with AnalysisCache(db, "p"):
            pass
        assert db.exists()


def test_default_cache_path_sits_beside_the_shoot(tmp_path):
    path = default_cache_path(tmp_path)
    assert path.parent.parent == tmp_path
    assert path.suffix == ".sqlite"
