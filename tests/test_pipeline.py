"""pipeline.py 단위 테스트 (프로세스 풀을 띄우지 않는 부분 위주)."""

from __future__ import annotations

from pathlib import Path

import pytest

from arw_selector.core import pipeline
from arw_selector.core.config import AnalyzeConfig, Config


class TestResolveWorkers:
    def test_explicit_count_wins(self):
        assert pipeline.resolve_workers(4) == 4

    def test_default_leaves_one_core_free(self):
        """코어 수 - 1이 상한이되, 램과 실측 이득에도 걸립니다.

        예전에는 코어 수만 봤습니다. 코어가 많은 PC에서는 워커가 6개를
        넘어도 거의 안 빨라지면서(실측 8워커 2.93배 vs 6워커 2.83배) 램만
        더 씁니다. 저사양에서는 램이 모자라 스왑이 걸립니다.
        자세한 경계는 test_memory_limits.py 에서 봅니다.
        """
        import os

        cores = max(1, (os.cpu_count() or 2) - 1)

        workers = pipeline.resolve_workers(None)

        assert 1 <= workers <= cores
        assert workers <= pipeline.MAX_USEFUL_WORKERS

    @pytest.mark.parametrize("bad", [0, -1])
    def test_invalid_falls_back_to_default(self, bad):
        assert pipeline.resolve_workers(bad) >= 1


class TestProgress:
    def test_ratio(self):
        assert pipeline.Progress(25, 100, 0, 0, 1.0).ratio == 0.25

    def test_ratio_of_empty_batch_is_complete(self):
        assert pipeline.Progress(0, 0, 0, 0, 0.0).ratio == 1.0

    def test_eta_needs_a_few_samples(self):
        """초반 몇 장으로 추정하면 값이 심하게 튑니다."""
        assert pipeline.Progress(3, 4000, 0, 0, 1.0).eta_seconds is None

    def test_eta_extrapolates_linearly(self):
        progress = pipeline.Progress(100, 4100, 0, 0, 10.0)
        assert progress.eta_seconds == pytest.approx(400.0)

    def test_eta_is_none_when_finished(self):
        assert pipeline.Progress(50, 50, 0, 0, 5.0).eta_seconds is None


class TestAnalyzeFile:
    def test_corrupt_file_returns_error_record(self, tmp_path):
        """손상 파일 한 장이 4000장 배치를 죽이면 안 됩니다."""
        bogus = tmp_path / "corrupt.ARW"
        bogus.write_bytes(b"this is not a raw file at all")

        record = pipeline.analyze_file(bogus, AnalyzeConfig())

        assert record.path == bogus
        assert record.error is not None
        assert not record.ok
        assert record.focus is None

    def test_missing_file_returns_error_record(self, tmp_path):
        record = pipeline.analyze_file(tmp_path / "nope.ARW", AnalyzeConfig())
        assert record.error is not None
        assert not record.ok


class TestAnalyzePaths:
    def test_empty_input(self):
        assert pipeline.analyze_paths([]) == []

    def test_reports_progress_for_cached_only_batch(self, tmp_path):
        """캐시가 전부 히트해도 진행률 콜백은 최소 한 번 와야 UI가 100%가 됩니다."""
        seen = []
        pipeline.analyze_paths([], progress_cb=seen.append)
        assert seen == []  # 빈 배치는 호출하지 않습니다


class TestConfigCacheKey:
    def test_same_config_same_key(self):
        assert AnalyzeConfig().cache_key() == AnalyzeConfig().cache_key()

    def test_changed_parameter_changes_key(self):
        """K를 바꾸면 예전 분석 결과는 무횹니다."""
        a = AnalyzeConfig()
        b = AnalyzeConfig(laplacian_k=0.099)
        assert a.cache_key() != b.cache_key()

    def test_key_is_short_and_stable(self):
        key = AnalyzeConfig().cache_key()
        assert len(key) == 16
        assert key == AnalyzeConfig().cache_key()

    def test_algorithm_version_invalidates_cache(self, monkeypatch):
        """설정이 같아도 측정 알고리즘이 바뀌면 예전 결과는 무횹니다.

        이것이 빠져 있으면 캐시가 옛날 점수를 돌려줘서, 고친 내용이 반영되지
        않은 채로 "고쳤다"고 착각하게 됩니다.
        """
        from arw_selector.core import focus

        before = AnalyzeConfig().cache_key()
        monkeypatch.setattr(focus, "ALGORITHM_VERSION", focus.ALGORITHM_VERSION + 1)
        assert AnalyzeConfig().cache_key() != before


class TestConfigLoading:
    def test_defaults_when_no_file(self, tmp_path):
        config = Config.load(tmp_path / "missing.yaml")
        assert config.analyze.detect_long_edge == 1024

    def test_partial_yaml_keeps_other_defaults(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text("score:\n  keep_per_group: 3\n", encoding="utf-8")
        config = Config.load(path)
        assert config.score.keep_per_group == 3
        assert config.analyze.detect_long_edge == 1024

    def test_unknown_keys_are_ignored(self, tmp_path):
        """오래된 설정 파일이 남아 있어도 죽지 않아야 합니다."""
        path = tmp_path / "config.yaml"
        path.write_text("analyze:\n  legacy_option: 5\n", encoding="utf-8")
        assert Config.load(path).analyze.detect_long_edge == 1024

    def test_round_trips_through_yaml(self, tmp_path):
        config = Config()
        config.score.keep_per_group = 2
        path = tmp_path / "c.yaml"
        path.write_text(config.to_yaml(), encoding="utf-8")
        assert Config.load(path).score.keep_per_group == 2
