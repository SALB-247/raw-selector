"""캐시 상태 조회와 삭제 테스트."""

from __future__ import annotations

import numpy as np
import pytest

from arw_selector.core.cache import (
    AnalysisCache,
    CACHE_DIR_NAME,
    cache_stats,
    clear_cache,
    default_cache_path,
)
from arw_selector.core.thumbs import thumbnail_path, write_thumbnail
from tests.test_cache import make_record


@pytest.fixture
def shoot(tmp_path):
    """분석 캐시와 썸네일이 들어 있는 촬영 폴더를 흉내냅니다."""
    arw = tmp_path / "DSC001.ARW"
    arw.write_bytes(b"fake raw")

    with AnalysisCache(default_cache_path(tmp_path), "params") as cache:
        cache.put_many([make_record(arw)])

    cache_dir = tmp_path / CACHE_DIR_NAME
    image = np.full((60, 90, 3), 120, np.uint8)
    write_thumbnail(image, thumbnail_path(cache_dir, arw))
    return tmp_path


class TestCacheStats:
    def test_missing_cache_reports_nothing(self, tmp_path):
        stats = cache_stats(tmp_path)
        assert stats.exists is False
        assert stats.total_bytes == 0
        assert stats.summary() == "캐시 없음"

    def test_counts_entries_and_thumbnails(self, shoot):
        stats = cache_stats(shoot)
        assert stats.exists is True
        assert stats.analysis_entries == 1
        assert stats.thumbnail_count == 1
        assert stats.analysis_bytes > 0
        assert stats.thumbnail_bytes > 0

    def test_summary_is_readable(self, shoot):
        summary = cache_stats(shoot).summary()
        assert "분석 1건" in summary and "썸네일 1개" in summary and "MB" in summary

    def test_size_parts_add_up_to_total(self, shoot):
        """부분과 합계에서 단위가 갈리면 사용자가 보기에 숫자가 안 맞습니다."""
        stats = cache_stats(shoot)
        assert stats.analysis_mb + stats.thumbnail_mb == pytest.approx(
            stats.total_mb, rel=1e-9
        )

    def test_counts_export_logs(self, shoot):
        (shoot / CACHE_DIR_NAME / "export_20260101_000000.json").write_text("{}", encoding="utf-8")
        assert cache_stats(shoot).log_count == 1

    def test_corrupt_db_does_not_raise(self, tmp_path):
        """손상된 캐시라도 개수만 모를 뿐, 조회 자체는 성공해야 삭제할 수 있습니다."""
        cache_dir = tmp_path / CACHE_DIR_NAME
        cache_dir.mkdir()
        (cache_dir / "analysis.sqlite").write_bytes(b"this is not a database")

        stats = cache_stats(tmp_path)
        assert stats.exists is True
        assert stats.analysis_entries == 0


class TestClearCache:
    def test_removes_db_and_thumbnails(self, shoot):
        clear_cache(shoot)
        assert cache_stats(shoot).exists is False

    def test_returns_stats_from_before_deletion(self, shoot):
        """무엇을 지웠는지 사용자에게 보여줘야 합니다."""
        before = clear_cache(shoot)
        assert before.analysis_entries == 1
        assert before.thumbnail_count == 1

    def test_originals_are_never_touched(self, shoot):
        clear_cache(shoot)
        assert (shoot / "DSC001.ARW").exists()

    def test_keeps_export_logs_by_default(self, shoot):
        """'캐시 삭제'가 되돌리기를 없앨 거라고 예상하는 사용자는 없습니다."""
        log = shoot / CACHE_DIR_NAME / "export_20260101_000000.json"
        log.write_text("{}", encoding="utf-8")

        clear_cache(shoot)
        assert log.exists()

    def test_can_drop_logs_explicitly(self, shoot):
        log = shoot / CACHE_DIR_NAME / "export_20260101_000000.json"
        log.write_text("{}", encoding="utf-8")

        clear_cache(shoot, keep_logs=False)
        assert not log.exists()

    def test_missing_cache_is_not_an_error(self, tmp_path):
        assert clear_cache(tmp_path).exists is False

    def test_reanalysis_works_after_clear(self, shoot):
        """지운 뒤에도 캐시가 정상 재생성되어야 합니다."""
        clear_cache(shoot)
        arw = shoot / "DSC001.ARW"
        with AnalysisCache(default_cache_path(shoot), "params") as cache:
            cache.put_many([make_record(arw)])
        assert cache_stats(shoot).analysis_entries == 1
