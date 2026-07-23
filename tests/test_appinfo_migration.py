"""제품명 변경(ARW Selector -> RAW_selector) 마이그레이션.

이름이 저장 경로에 박혀 있어서, 마이그레이션이 없으면 사용자가 만든 프리셋과
'되돌리기' 로그가 통째로 사라진 것처럼 보입니다. 여기서 그 유실을 막습니다.
"""

from __future__ import annotations

import json

import pytest

from arw_selector.core import appinfo, cache, export, presets


class TestIdentity:
    def test_names_are_migrated(self):
        assert appinfo.APP_NAME == "RAW_selector"
        assert appinfo.APP_DIR_NAME == "raw_selector"
        assert appinfo.CACHE_DIR_NAME == ".raw_selector_cache"

    def test_legacy_names_are_still_known(self):
        """예전 이름을 잊으면 예전 데이터를 찾을 방법이 없습니다."""
        assert "arw_selector" in appinfo.LEGACY_APP_DIR_NAMES
        assert ".arw_selector_cache" in appinfo.LEGACY_CACHE_DIR_NAMES

    def test_modules_share_one_source_of_truth(self):
        assert cache.CACHE_DIR_NAME == appinfo.CACHE_DIR_NAME
        assert export.LOG_DIR_NAME == appinfo.CACHE_DIR_NAME
        assert presets.APP_DIR_NAME == appinfo.APP_DIR_NAME

    def test_exif_software_tag_uses_new_name(self):
        from arw_selector.core.develop import metadata

        assert metadata.SOFTWARE_NAME == appinfo.APP_NAME

    def test_version_matches_pyproject(self):
        """버전을 두 곳에 적어 두면 한쪽만 올리게 됩니다.

        예전에는 여기에 버전 문자열을 박아 둬서 올릴 때마다 테스트가
        깨졌습니다. 그건 위험이 아니라 소음입니다. 진짜 위험은 패키지와
        pyproject.toml이 서로 다른 버전을 말하는 것입니다.
        """
        import re
        from pathlib import Path

        from arw_selector import __version__

        pyproject = (
            Path(__file__).resolve().parents[1] / "pyproject.toml"
        ).read_text(encoding="utf-8")
        declared = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)

        assert declared is not None, "pyproject.toml에 version이 없습니다"
        assert __version__ == declared.group(1)


# 프리셋 이사(AppData -> 앱 폴더 data/) 검증은 test_portable_data.py 의
# TestPresetMigration 에 있습니다. 그쪽은 대상 폴더까지 격리해 두어, 테스트가
# 실제 data/ 폴더를 건드리지 않습니다.


class TestCacheDirFallback:
    """사진 폴더 옆 캐시는 폴더마다 흩어져 있어 옮길 수 없습니다."""

    def test_prefers_new_name(self, tmp_path):
        (tmp_path / appinfo.CACHE_DIR_NAME).mkdir()
        assert cache.resolve_cache_dir(tmp_path).name == appinfo.CACHE_DIR_NAME

    def test_falls_back_to_legacy(self, tmp_path):
        """예전 이름으로 분석해 둔 폴더를 다시 분석하게 만들면 안 됩니다."""
        (tmp_path / ".arw_selector_cache").mkdir()
        assert cache.resolve_cache_dir(tmp_path).name == ".arw_selector_cache"

    def test_new_name_wins_when_both_exist(self, tmp_path):
        (tmp_path / ".arw_selector_cache").mkdir()
        (tmp_path / appinfo.CACHE_DIR_NAME).mkdir()
        assert cache.resolve_cache_dir(tmp_path).name == appinfo.CACHE_DIR_NAME

    def test_uses_new_name_for_fresh_folder(self, tmp_path):
        assert cache.resolve_cache_dir(tmp_path).name == appinfo.CACHE_DIR_NAME

    def test_analysis_cache_path_follows_fallback(self, tmp_path):
        (tmp_path / ".arw_selector_cache").mkdir()
        assert cache.default_cache_path(tmp_path).parent.name == ".arw_selector_cache"


class TestUndoLogFallback:
    """예전 내보내기를 계속 되돌릴 수 있어야 합니다."""

    def test_finds_logs_written_under_old_name(self, tmp_path):
        legacy = tmp_path / ".arw_selector_cache"
        legacy.mkdir()
        (legacy / "export_20260101_120000.json").write_text(
            json.dumps({"version": 1, "entries": []}), encoding="utf-8"
        )
        found = export.find_logs(tmp_path)
        assert len(found) == 1, "예전 되돌리기 로그를 찾지 못했다"

    def test_finds_logs_under_new_name(self, tmp_path):
        current = tmp_path / appinfo.CACHE_DIR_NAME
        current.mkdir()
        (current / "export_20260101_120000.json").write_text("{}", encoding="utf-8")
        assert len(export.find_logs(tmp_path)) == 1

    def test_missing_dir_returns_empty(self, tmp_path):
        assert export.find_logs(tmp_path) == []
