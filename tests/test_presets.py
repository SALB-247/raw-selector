"""프리셋 저장소 테스트.

보정 엔진 자체는 test_develop_engine.py에서 다룹니다. 여기서는 프리셋이
디스크에 안전하게 오가는지만 봅니다.
"""

from __future__ import annotations

import pytest

from arw_selector.core.develop import BasicSettings, DevelopSettings
from arw_selector.core.presets import (
    PresetStore,
    default_develop_profiles,
    install_default_profiles,
    safe_filename,
)


class TestDefaultProfiles:
    def test_profiles_are_valid(self):
        profiles = default_develop_profiles()
        assert set(profiles) == {"표준", "인물", "풍경", "선명", "필름", "중립"}
        for name, data in profiles.items():
            # dict 왕복이 되어야 프리셋으로 저장·로드됩니다
            restored = DevelopSettings.from_dict(data)
            assert restored == DevelopSettings.from_dict(restored.to_dict())
        # 표준은 중립(베이스라인 그대로), 나머지는 무언가 바꿉니다
        assert DevelopSettings.from_dict(profiles["표준"]).is_neutral()
        assert not DevelopSettings.from_dict(profiles["인물"]).is_neutral()

    def test_install_once(self, tmp_path):
        first = install_default_profiles(tmp_path)
        assert first == 6
        # 두 번째는 마커 때문에 아무것도 안 합니다
        assert install_default_profiles(tmp_path) == 0

    def test_install_skips_user_preset(self, tmp_path):
        store = PresetStore("develop_presets", tmp_path)
        store.save("인물", DevelopSettings(basic=BasicSettings(exposure=1.0)).to_dict())
        install_default_profiles(tmp_path)
        # 사용자의 인물 프리셋은 덮어쓰지 않습니다
        loaded = DevelopSettings.from_dict(store.load("인물"))
        assert loaded.basic.exposure == 1.0


class TestPresetStore:
    def test_save_and_load(self, tmp_path):
        store = PresetStore("develop_presets", tmp_path)
        settings = DevelopSettings(basic=BasicSettings(exposure=0.8, contrast=15))
        store.save("무대 조명", settings.to_dict())

        assert DevelopSettings.from_dict(store.load("무대 조명")) == settings

    def test_list_returns_saved_presets(self, tmp_path):
        store = PresetStore("select_presets", tmp_path)
        store.save("타이트", {"a": 1})
        store.save("느슨", {"a": 2})
        assert {p.name for p in store.list()} == {"타이트", "느슨"}

    def test_list_of_missing_dir_is_empty(self, tmp_path):
        assert PresetStore("nope", tmp_path).list() == []

    def test_overwrite(self, tmp_path):
        store = PresetStore("p", tmp_path)
        store.save("이름", {"value": 1})
        store.save("이름", {"value": 2})
        assert store.load("이름")["value"] == 2
        assert len(store.list()) == 1

    def test_delete(self, tmp_path):
        store = PresetStore("p", tmp_path)
        store.save("지울것", {"a": 1})
        assert store.delete("지울것")
        assert store.list() == []

    def test_delete_missing_is_false(self, tmp_path):
        assert PresetStore("p", tmp_path).delete("없음") is False

    def test_corrupt_preset_raises(self, tmp_path):
        store = PresetStore("p", tmp_path)
        store.ensure_dir()
        (store.directory / "깨진것.yaml").write_text("not a preset", encoding="utf-8")
        with pytest.raises(ValueError):
            store.load("깨진것")

    def test_korean_names_preserved(self, tmp_path):
        store = PresetStore("p", tmp_path)
        store.save("공연 야간 망원", {"a": 1})
        assert store.list()[0].name == "공연 야간 망원"

    def test_full_develop_settings_survive(self, tmp_path):
        """확장된 보정 설정 전체가 프리셋으로 오가야 합니다."""
        from arw_selector.core.develop import (
            ColorGradeSettings,
            ColorGradeZone,
            CurveSettings,
            DetailSettings,
            EffectSettings,
            HSLBand,
            HSLSettings,
            WatermarkSettings,
        )

        settings = DevelopSettings(
            basic=BasicSettings(exposure=0.4, clarity=25, dehaze=10),
            curve=CurveSettings(shadows=15, points_rgb=((90, 110),)),
            detail=DetailSettings(sharpen_amount=45, color_noise_reduction=30,
                                  color_noise_radius=70),
            hsl=HSLSettings(bands={"orange": HSLBand(saturation=-15, luminance=10)}),
            color_grade=ColorGradeSettings(
                highlights=ColorGradeZone(hue=45, saturation=20)
            ),
            effects=EffectSettings(vignette_amount=-25),
            watermark=WatermarkSettings(enabled=True, text="© 2026"),
        )

        store = PresetStore("develop_presets", tmp_path)
        store.save("전체", settings.to_dict())
        assert DevelopSettings.from_dict(store.load("전체")) == settings

    def test_noise_algorithm_survives_yaml(self, tmp_path):
        """프리셋은 YAML입니다 — Enum이 남아 있으면 저장이 통째로 실패합니다."""
        from arw_selector.core.develop import DetailSettings, NoiseAlgorithm

        settings = DevelopSettings(
            detail=DetailSettings(noise_reduction=55,
                                  noise_algorithm=NoiseAlgorithm.NLMEANS_HQ)
        )
        store = PresetStore("develop_presets", tmp_path)
        store.save("고감도", settings.to_dict())
        restored = DevelopSettings.from_dict(store.load("고감도"))
        assert restored.detail.noise_algorithm is NoiseAlgorithm.NLMEANS_HQ


class TestSafeFilename:
    def test_strips_path_separators(self):
        """이름으로 지정 폴더를 벗어날 수 없어야 합니다."""
        result = safe_filename("../../etc/passwd")
        assert "/" not in result and "\\" not in result and ".." not in result

    def test_keeps_korean_and_spaces(self):
        assert safe_filename("무대 조명 A") == "무대 조명 A"

    def test_empty_gets_fallback(self):
        assert safe_filename("///") == "이름없음"

    def test_length_capped(self):
        assert len(safe_filename("가" * 200)) <= 80
