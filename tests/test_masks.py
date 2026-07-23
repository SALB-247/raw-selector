"""마스크(국소 보정) 엔진과 프리셋 테스트.

얼굴 검출이 필요한 경로(FACE/EYE/BACKGROUND)는 합성 이미지로 재현하기
어려우므로, 얼굴이 없을 때 안전하게 None으로 떨어지는지와 파라메트릭
마스크(방사형/선형/브러시)의 성질, 국소 합성의 국소성을 고정합니다.
"""

from __future__ import annotations

import numpy as np

from arw_selector.core.develop import engine
from arw_selector.core.develop import masks as masks_mod
from arw_selector.core.develop.mask_presets import MASK_PRESETS, build_mask
from arw_selector.core.develop.settings import (
    DevelopSettings,
    GeometrySettings,
    LocalAdjustments,
    Mask,
    MaskType,
)


class TestLocalAdjustments:
    def test_neutral_by_default(self):
        assert LocalAdjustments().is_neutral()

    def test_any_field_makes_it_active(self):
        assert not LocalAdjustments(exposure=0.5).is_neutral()
        assert not LocalAdjustments(smoothing=10).is_neutral()


class TestMaskSerialization:
    def test_round_trip_parametric(self):
        mask = Mask(
            kind=MaskType.RADIAL,
            adjust=LocalAdjustments(exposure=0.4, smoothing=20),
            feather=70, opacity=80, invert=True,
            params={"cx": 0.5, "cy": 0.4, "rx": 0.3, "ry": 0.2},
            label="테스트",
        )
        restored = Mask.from_dict(mask.to_dict())
        assert restored == mask

    def test_round_trip_brush_bitmap(self):
        small = np.zeros((32, 32), np.uint8)
        small[8:24, 8:24] = 255
        encoded = masks_mod.encode_brush(small)
        mask = Mask(kind=MaskType.BRUSH, bitmap=encoded,
                    adjust=LocalAdjustments(exposure=0.2))
        restored = Mask.from_dict(mask.to_dict())
        assert restored.bitmap == encoded
        assert restored.kind is MaskType.BRUSH

    def test_from_dict_rejects_garbage(self):
        assert Mask.from_dict(None) is None
        assert Mask.from_dict({"kind": "nonsense"}) is None
        assert Mask.from_dict({}) is None

    def test_develop_settings_round_trip_with_masks(self):
        settings = DevelopSettings(masks=(
            Mask(kind=MaskType.RADIAL, adjust=LocalAdjustments(exposure=0.3)),
            Mask(kind=MaskType.EYE, adjust=LocalAdjustments(smoothing=40),
                 params={"region": "under_eye", "index": 0}),
        ))
        restored = DevelopSettings.from_dict(settings.to_dict())
        assert len(restored.masks) == 2
        assert restored.masks[0].kind is MaskType.RADIAL
        assert restored.masks[1].adjust.smoothing == 40

    def test_masks_make_settings_non_neutral(self):
        assert DevelopSettings().is_neutral()
        active = DevelopSettings(masks=(
            Mask(kind=MaskType.RADIAL, adjust=LocalAdjustments(exposure=0.3)),
        ))
        assert not active.is_neutral()

    def test_neutral_mask_keeps_settings_neutral(self):
        """조정이 비어 있는 마스크는 없는 것과 같습니다."""
        settings = DevelopSettings(masks=(Mask(kind=MaskType.RADIAL),))
        assert settings.is_neutral()

    def test_without_geometry_drops_masks(self):
        """마스크는 컷별이라 일괄 적용에서 빠져야 합니다."""
        settings = DevelopSettings(
            geometry=GeometrySettings(straighten=5.0),
            masks=(Mask(kind=MaskType.RADIAL, adjust=LocalAdjustments(exposure=0.3)),),
        )
        shared = settings.without_geometry()
        assert shared.masks == ()
        assert shared.geometry.straighten == 0.0


class TestAlphaGeneration:
    def _shape(self):
        return (200, 300)

    def _dummy_bgr(self):
        return np.zeros((200, 300, 3), np.uint8)

    def test_radial_center_stronger_than_edge(self):
        mask = Mask(kind=MaskType.RADIAL,
                    params={"cx": 0.5, "cy": 0.5, "rx": 0.3, "ry": 0.3})
        alpha = masks_mod.build_mask_alpha(mask, self._shape(), self._dummy_bgr(), None)
        assert alpha is not None
        assert alpha[100, 150] > 0.9
        assert alpha[0, 0] < 0.1

    def test_linear_gradient_increases_downward(self):
        mask = Mask(kind=MaskType.LINEAR,
                    params={"x0": 0.5, "y0": 0.0, "x1": 0.5, "y1": 0.5})
        alpha = masks_mod.build_mask_alpha(mask, self._shape(), self._dummy_bgr(), None)
        assert alpha is not None
        assert alpha[-1, 0] > alpha[0, 0]
        assert alpha[0, 0] < 0.05

    def test_brush_alpha_from_bitmap(self):
        small = np.zeros((40, 40), np.uint8)
        small[10:30, 10:30] = 255
        mask = Mask(kind=MaskType.BRUSH, bitmap=masks_mod.encode_brush(small), feather=10)
        alpha = masks_mod.build_mask_alpha(mask, self._shape(), self._dummy_bgr(), None)
        assert alpha is not None
        assert alpha[100, 150] > alpha[0, 0]

    def test_size_shrinks_radial_region(self):
        """범위(size)를 줄이면 덮는 영역이 실제로 작아져야 합니다."""
        params = {"cx": 0.5, "cy": 0.5, "rx": 0.3, "ry": 0.3}
        full = masks_mod.build_mask_alpha(
            Mask(kind=MaskType.RADIAL, params=params, size=100),
            self._shape(), self._dummy_bgr(), None,
        )
        small = masks_mod.build_mask_alpha(
            Mask(kind=MaskType.RADIAL, params=params, size=50),
            self._shape(), self._dummy_bgr(), None,
        )
        assert (small > 0.05).sum() < (full > 0.05).sum()
        assert small[100, 150] > 0.9  # 중심은 그대로 꽉 찬다

    def test_size_defaults_to_full(self):
        assert Mask(kind=MaskType.RADIAL).size == 100

    def test_size_round_trips(self):
        mask = Mask(kind=MaskType.EYE, size=60, params={"region": "under_eye"})
        assert Mask.from_dict(mask.to_dict()).size == 60

    def test_face_kinds_return_none_without_faces(self):
        for kind, params in (
            (MaskType.FACE, {"region": "skin"}),
            (MaskType.EYE, {"region": "under_eye"}),
        ):
            mask = Mask(kind=kind, params=params)
            assert masks_mod.build_mask_alpha(mask, self._shape(), self._dummy_bgr(), None) is None


class TestApplyLocal:
    def test_exposure_brightens(self):
        image = np.full((20, 20, 3), 100.0, np.float32)
        out = masks_mod.apply_local(image, LocalAdjustments(exposure=1.0))
        assert out.mean() > image.mean() + 40

    def test_smoothing_reduces_variance(self):
        rng = np.random.default_rng(0)
        image = rng.uniform(60, 180, (60, 60, 3)).astype(np.float32)
        out = masks_mod.apply_local(image, LocalAdjustments(smoothing=90))
        assert out.var() < image.var()

    def test_neutral_local_is_noop(self):
        image = np.full((10, 10, 3), 120.0, np.float32)
        out = masks_mod.apply_local(image, LocalAdjustments())
        assert np.allclose(out, image)


class TestApplyMasks:
    def _uniform(self):
        return np.full((200, 300, 3), 100.0, np.float32)

    def test_radial_exposure_brightens_center_only(self):
        mask = Mask(kind=MaskType.RADIAL,
                    adjust=LocalAdjustments(exposure=1.0),
                    params={"cx": 0.5, "cy": 0.5, "rx": 0.25, "ry": 0.25})
        out = masks_mod.apply_masks(self._uniform(), (mask,))
        assert out[100, 150].mean() > 150       # 중앙은 밝아지고
        assert out[0, 0].mean() == np.float32(100.0)  # 구석은 그대로

    def test_invert_flips_region(self):
        mask = Mask(kind=MaskType.RADIAL,
                    adjust=LocalAdjustments(exposure=1.0), invert=True,
                    params={"cx": 0.5, "cy": 0.5, "rx": 0.25, "ry": 0.25})
        out = masks_mod.apply_masks(self._uniform(), (mask,))
        assert out[0, 0].mean() > 150            # 이제 바깥이 밝아지고
        assert out[100, 150].mean() == np.float32(100.0)

    def test_opacity_scales_effect(self):
        base = LocalAdjustments(exposure=1.0)
        params = {"cx": 0.5, "cy": 0.5, "rx": 0.25, "ry": 0.25}
        full = masks_mod.apply_masks(
            self._uniform(), (Mask(kind=MaskType.RADIAL, adjust=base, opacity=100, params=params),)
        )
        half = masks_mod.apply_masks(
            self._uniform(), (Mask(kind=MaskType.RADIAL, adjust=base, opacity=50, params=params),)
        )
        assert full[100, 150].mean() > half[100, 150].mean() > 100

    def test_neutral_masks_are_noop(self):
        image = self._uniform()
        out = masks_mod.apply_masks(image, (Mask(kind=MaskType.RADIAL),))
        assert np.array_equal(out, image)

    def test_face_mask_without_face_is_noop(self):
        image = self._uniform()
        mask = Mask(kind=MaskType.FACE, adjust=LocalAdjustments(smoothing=50),
                    params={"region": "skin"})
        out = masks_mod.apply_masks(image, (mask,))
        assert np.array_equal(out, image)


class TestEngineIntegration:
    def test_mask_changes_pipeline_output(self):
        image = np.full((120, 160, 3), 90, np.uint8)
        settings = DevelopSettings(masks=(
            Mask(kind=MaskType.RADIAL, adjust=LocalAdjustments(exposure=1.2),
                 params={"cx": 0.5, "cy": 0.5, "rx": 0.3, "ry": 0.3}),
        ))
        out = engine.apply_settings(image, settings)
        center = out[60, 80].mean()
        corner = out[0, 0].mean()
        assert center > corner + 20

    def test_neutral_settings_untouched_by_empty_masks(self):
        image = np.full((60, 80, 3), 130, np.uint8)
        out = engine.apply_settings(image, DevelopSettings(masks=()))
        assert np.array_equal(out, image)


class TestMaskPresets:
    def test_presets_exist_and_are_active(self):
        assert len(MASK_PRESETS) >= 8
        for preset in MASK_PRESETS:
            mask = preset.build()
            assert not mask.adjust.is_neutral(), f"{preset.key} 조정이 비어 있다"
            assert mask.label == preset.label

    def test_build_by_key(self):
        mask = build_mask("under_eye")
        assert mask is not None
        assert mask.kind is MaskType.EYE
        assert mask.params.get("region") == "under_eye"

    def test_unknown_key_returns_none(self):
        assert build_mask("does_not_exist") is None

    def test_build_gives_independent_params(self):
        """같은 프리셋을 두 번 만들어도 params dict가 공유되면 안 됩니다."""
        a = build_mask("dodge")
        b = build_mask("dodge")
        a.params["cx"] = 0.1
        assert b.params["cx"] != 0.1
