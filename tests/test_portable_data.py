"""포터블 데이터 구조 — 콘텐츠는 앱 폴더, 기기별 상태만 사용자 폴더.

프리셋 위치가 이번까지 세 번 바뀌었습니다(arw_selector -> raw_selector ->
앱 폴더 data/). 매번 사용자가 만든 프리셋을 잃지 않는 것이 핵심입니다.
"""

from __future__ import annotations

import pytest

from arw_selector.core import appinfo, presets, state


class TestLayout:
    def test_data_dir_sits_next_to_app(self):
        """기본은 실행 파일 옆 data/ 입니다."""
        assert appinfo.data_dir() == appinfo.app_root() / appinfo.DATA_DIR_NAME
        assert appinfo.is_portable()

    def test_presets_live_in_data_dir(self):
        assert presets.user_config_dir() == appinfo.data_dir()

    def test_state_stays_in_user_folder(self):
        """마지막으로 연 폴더는 그 PC에서만 의미가 있어 함께 다니면 안 됩니다."""
        assert appinfo.user_state_dir() != appinfo.data_dir()
        assert appinfo.APP_DIR_NAME in str(appinfo.user_state_dir())

    def test_falls_back_when_app_dir_is_read_only(self, tmp_path, monkeypatch):
        """Program Files처럼 쓰기가 막힌 곳에 설치하면 저장이 아예 안 됩니다."""
        monkeypatch.setattr(appinfo, "app_root", lambda: tmp_path / "app")
        monkeypatch.setattr(appinfo, "_is_writable", lambda path: False)
        monkeypatch.setattr(appinfo, "user_state_dir", lambda: tmp_path / "user")
        appinfo.data_dir.cache_clear()
        try:
            assert appinfo.data_dir() == tmp_path / "user" / appinfo.DATA_DIR_NAME
        finally:
            appinfo.data_dir.cache_clear()


class TestPresetMigration:
    """AppData에 있던 프리셋을 앱 폴더로 가져옵니다."""

    @pytest.fixture
    def dirs(self, tmp_path, monkeypatch):
        target = tmp_path / "app" / "data"
        config_root = tmp_path / "roaming"
        monkeypatch.setattr(presets, "user_config_dir", lambda: target)
        monkeypatch.setattr(presets, "_config_root", lambda: config_root)
        return target, config_root

    def _seed(self, root, app_dir_name, name="내 프리셋"):
        folder = root / app_dir_name / presets.DEVELOP_PRESET_DIR
        folder.mkdir(parents=True)
        (folder / f"{name}.yaml").write_text(f"name: {name}\n", encoding="utf-8")
        return root / app_dir_name

    def test_migrates_from_current_appdata(self, dirs):
        target, config_root = dirs
        self._seed(config_root, "raw_selector")

        assert presets.migrate_legacy_config() is not None
        moved = target / presets.DEVELOP_PRESET_DIR / "내 프리셋.yaml"
        assert moved.exists(), "AppData 프리셋이 앱 폴더로 오지 않았다"

    def test_migrates_from_original_name(self, dirs):
        """제품명 변경 이전(arw_selector)에 만든 프리셋도 살려야 합니다."""
        target, config_root = dirs
        self._seed(config_root, "arw_selector", name="옛날프리셋")

        assert presets.migrate_legacy_config() is not None
        assert (target / presets.DEVELOP_PRESET_DIR / "옛날프리셋.yaml").exists()

    def test_original_is_preserved(self, dirs):
        target, config_root = dirs
        legacy = self._seed(config_root, "raw_selector")
        presets.migrate_legacy_config()
        assert (legacy / presets.DEVELOP_PRESET_DIR / "내 프리셋.yaml").exists()

    def test_does_not_overwrite_existing_work(self, dirs):
        """앱 폴더에 이미 작업물이 있으면 예전 것으로 덮으면 안 됩니다."""
        target, config_root = dirs
        self._seed(config_root, "raw_selector")
        current = target / presets.DEVELOP_PRESET_DIR
        current.mkdir(parents=True)
        (current / "지금것.yaml").write_text("name: 지금것\n", encoding="utf-8")

        assert presets.migrate_legacy_config() is None
        assert (current / "지금것.yaml").exists()
        assert not (current / "내 프리셋.yaml").exists()

    def test_noop_without_legacy(self, dirs):
        assert presets.migrate_legacy_config() is None

    def test_runs_only_once(self, dirs):
        _, config_root = dirs
        self._seed(config_root, "raw_selector")
        assert presets.migrate_legacy_config() is not None
        assert presets.migrate_legacy_config() is None


class TestSessionState:
    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        monkeypatch.setattr(appinfo, "user_state_dir", lambda: tmp_path)
        monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)

    def test_remembers_last_folder(self, tmp_path):
        folder = tmp_path / "촬영_2026"
        folder.mkdir()
        state.remember_folder(folder)
        assert state.last_folder() == folder

    def test_forgets_folder_that_disappeared(self, tmp_path):
        """외장 드라이브를 뽑았거나 지워졌으면 없는 것으로 칩니다."""
        state.update_state(last_folder=str(tmp_path / "없는폴더"))
        assert state.last_folder() is None

    def test_missing_state_is_empty(self):
        assert state.load_state() == {}
        assert state.last_folder() is None

    def test_corrupt_state_does_not_crash(self, tmp_path):
        (tmp_path / appinfo.STATE_FILE_NAME).write_text("{깨진 json", encoding="utf-8")
        assert state.load_state() == {}

    def test_update_keeps_other_keys(self):
        state.update_state(alpha=1)
        state.update_state(beta=2)
        loaded = state.load_state()
        assert loaded["alpha"] == 1 and loaded["beta"] == 2
