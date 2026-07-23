"""손상된 프리셋·대기열 파일을 열 때 죽지 않는지.

프리셋과 대기열은 사용자가 직접 열어 고칠 수 있는 YAML/JSON입니다
(core/presets.py 참고). 손으로 고치다 섹션 하나를 문자열로 만들거나,
예전 버전이 다른 모양으로 저장해 뒀거나, 저장 중에 전원이 나가면
그 파일은 우리가 예상한 모양이 아닙니다.

`DevelopSettings.from_dict`는 "dict가 아닌 값이 들어와도 기본값으로
넘어간다"고 약속합니다. 그 약속이 실제로 지켜지는지 확인합니다 —
지켜지지 않으면 프리셋 하나 때문에 보정 패널이 열리지 않고, 대기열
항목 하나 때문에 쌓아 둔 수백 장이 통째로 사라집니다.
"""

from __future__ import annotations

import json

import pytest
import yaml

from arw_selector.core.develop import DevelopSettings, Mask, MaskType
from arw_selector.core.export_queue import ExportQueue
from arw_selector.core.presets import PresetStore


# 섹션 자리에 들어올 수 있는 "dict가 아닌 것"들. 손 편집과 YAML 오타에서
# 실제로 나오는 모양입니다.
NOT_A_DICT = ("문자열", ["리스트"], 12345, 1.5, True)

SECTIONS = (
    "basic", "curve", "detail", "hsl", "color_grade",
    "effects", "optics", "geometry", "watermark", "metadata", "exif_strip",
)


class TestCorruptDevelopPreset:
    @pytest.mark.parametrize("section", SECTIONS)
    @pytest.mark.parametrize("value", NOT_A_DICT)
    def test_section_is_not_a_dict(self, section, value):
        """섹션 하나가 망가져도 나머지는 살아야 합니다."""
        data = {section: value, "basic": {"contrast": 30}}
        settings = DevelopSettings.from_dict(data)
        assert isinstance(settings, DevelopSettings)
        if section != "basic":
            # 멀쩡한 섹션까지 같이 버리면 사용자가 만든 값이 사라집니다
            assert settings.basic.contrast == 30

    @pytest.mark.parametrize(
        "data",
        [
            {"curve": {"highlights": "높음"}},
            {"curve": {"shadows": None}},
            {"curve": {"points_rgb": "곡선"}},
            {"color_grade": {"blending": "중간"}},
            {"color_grade": {"balance": None}},
            {"color_grade": {"shadows": "어둡게"}},
            {"watermark": {"color": "빨강"}},
            {"watermark": {"color": [1]}},
            {"watermark": {"color": None}},
            {"watermark": {"opacity": "반투명"}},
            {"metadata": {"include": 5}},
            {"metadata": {"include": None}},
            {"exif_strip": {"include": 5}},
            {"exif_strip": {"include": "camera"}},
            {"exif_strip": {"height_percent": "높게"}},
            {"geometry": {"crop_left": "왼쪽"}},
            {"geometry": {"rotate_quarters": None}},
            {"detail": {"sharpen_amount": "강하게"}},
        ],
    )
    def test_wrong_typed_values(self, data):
        """숫자 자리에 글자가 들어와도 기본값으로 물러섭니다."""
        settings = DevelopSettings.from_dict(data)
        assert isinstance(settings, DevelopSettings)
        # 복원한 것을 다시 저장하고 읽어도 같은 모양이어야 합니다
        assert DevelopSettings.from_dict(settings.to_dict()) == settings

    def test_watermark_color_always_has_three_channels(self):
        """색이 세 값이 아니면 cv2.putText가 저장 시점에 터집니다.

        불러올 때는 조용히 넘어가고 내보낼 때 죽으면, 사용자는 몇십 분
        걸린 배치가 끝날 때쯤에야 알게 됩니다.
        """
        for broken in ("빨강", [1], [1, 2], None, 5, [1, 2, 3, 4]):
            settings = DevelopSettings.from_dict({"watermark": {"color": broken}})
            assert len(settings.watermark.color) == 3
            assert all(isinstance(c, int) for c in settings.watermark.color)

    @pytest.mark.parametrize(
        "payload",
        ["", "문자열", "- 리스트", "123", "null", "{}", "[]"],
    )
    def test_yaml_shapes_that_are_not_our_format(self, payload):
        settings = DevelopSettings.from_dict(yaml.safe_load(payload))
        assert settings == DevelopSettings()


class TestCorruptMask:
    @pytest.mark.parametrize("field", ["opacity", "feather", "size"])
    @pytest.mark.parametrize("value", ["높음", None, [], {}, "", "12.5"])
    def test_numeric_fields_tolerate_garbage(self, field, value):
        """마스크 수치가 깨져도 마스크 하나만 기본값이 되면 됩니다."""
        mask = Mask.from_dict({"kind": "radial", field: value})
        assert mask is not None
        assert isinstance(getattr(mask, field), int)

    def test_broken_mask_does_not_take_down_the_others(self):
        data = {
            "masks": [
                {"kind": "radial", "opacity": "높음"},
                {"kind": "face", "params": {"region": "skin"}},
                {"kind": "없는종류"},
                "문자열",
                None,
            ]
        }
        settings = DevelopSettings.from_dict(data)
        kinds = [m.kind for m in settings.masks]
        assert kinds == [MaskType.RADIAL, MaskType.FACE]

    def test_params_that_are_not_a_dict(self):
        mask = Mask.from_dict({"kind": "radial", "params": [1, 2, 3]})
        assert mask is not None and mask.params == {}


class TestPresetValuesReachTheEngineSafely:
    """불러오기가 통과한 값은 렌더까지 무사히 가야 합니다.

    여기서 막지 못하면 실패 지점이 '프리셋을 열 때'가 아니라 '몇십 분 걸린
    내보내기 도중'이 됩니다 — 사용자가 원인을 찾을 수 없는 자리입니다.
    """

    @pytest.fixture
    def image(self):
        import numpy as np

        rng = np.random.default_rng(5)
        return rng.integers(0, 256, (40, 60, 3), dtype=np.uint8)

    @pytest.mark.parametrize(
        "exposure", [1e9, -1e9, 1024.0, -1024.0, 200.0, 60.0, 20.0, -20.0]
    )
    def test_absurd_exposure_still_renders(self, image, exposure):
        import numpy as np

        from arw_selector.core.develop import BasicSettings
        from arw_selector.core.develop import engine

        result = engine.apply_settings(
            image, DevelopSettings(basic=BasicSettings(exposure=exposure)))
        assert result.dtype == np.uint8
        assert np.all(np.isfinite(result.astype(np.float32)))

    @pytest.mark.parametrize("exposure", [20.0, 60.0, 200.0, 1e9])
    def test_beyond_saturation_changes_nothing(self, image, exposure):
        """8비트 LUT는 ±8 EV면 포화합니다. 그 위는 전부 같은 그림이어야 합니다."""
        import numpy as np

        from arw_selector.core.develop import BasicSettings
        from arw_selector.core.develop import engine

        saturated = engine.apply_settings(
            image, DevelopSettings(basic=BasicSettings(exposure=10.0)))
        extreme = engine.apply_settings(
            image, DevelopSettings(basic=BasicSettings(exposure=exposure)))
        assert np.array_equal(saturated, extreme)

    def test_preset_with_absurd_exposure_round_trips(self, image):
        """YAML에 적힌 값을 우리가 조용히 바꿔 저장하면 안 됩니다."""
        settings = DevelopSettings.from_dict({"basic": {"exposure": 1e9}})
        assert settings.basic.exposure == 1e9

    @pytest.mark.parametrize(
        "params",
        [
            {"rotation": float("nan")},
            {"cx": float("nan")},
            {"rx": float("nan"), "ry": float("nan")},
            {"rx": float("inf")},
            {"cy": float("-inf")},
            {"cx": "가운데"},
            {"rx": None},
            {"rotation": [1, 2]},
        ],
    )
    def test_radial_mask_params_never_produce_nan_alpha(self, image, params):
        """마스크 알파의 NaN은 예외 없이 저장본에 쓰레기 화소로 남습니다.

        params는 종류마다 다른 자유 형식 dict라 dataclass의 타입 정리를
        거치지 않습니다. YAML의 `.nan`이 그대로 계산에 실립니다.
        """
        import numpy as np

        from arw_selector.core.develop import LocalAdjustments
        from arw_selector.core.develop.masks import build_mask_alpha

        mask = Mask(kind=MaskType.RADIAL, adjust=LocalAdjustments(exposure=1.0),
                    params=params)
        alpha = build_mask_alpha(mask, image.shape, image, None)
        assert alpha is not None
        assert np.all(np.isfinite(alpha)), "마스크 알파에 NaN/inf가 있습니다"

    @pytest.mark.parametrize(
        "params",
        [
            {"x0": float("nan")},
            {"y1": float("inf")},
            {"x0": 0.0, "y0": 0.0, "x1": "끝", "y1": 1.0},
        ],
    )
    def test_linear_mask_params_never_produce_nan_alpha(self, image, params):
        import numpy as np

        from arw_selector.core.develop import LocalAdjustments
        from arw_selector.core.develop.masks import build_mask_alpha

        mask = Mask(kind=MaskType.LINEAR, adjust=LocalAdjustments(exposure=1.0),
                    params=params)
        alpha = build_mask_alpha(mask, image.shape, image, None)
        assert alpha is not None
        assert np.all(np.isfinite(alpha))

    def test_nan_params_do_not_leak_into_the_rendered_image(self, image):
        import numpy as np

        from arw_selector.core.develop import LocalAdjustments
        from arw_selector.core.develop import engine

        mask = Mask(kind=MaskType.RADIAL, adjust=LocalAdjustments(exposure=2.0),
                    params={"cx": 0.5, "cy": 0.5, "rx": 0.3, "ry": 0.3,
                            "rotation": float("nan")})
        result = engine.apply_settings(image, DevelopSettings(masks=(mask,)))
        assert np.all(np.isfinite(result.astype(np.float32)))


class TestPresetStoreRoundTrip:
    def test_saved_preset_reloads_identically(self, tmp_path):
        """저장한 프리셋이 그대로 돌아오지 않으면 '적용'이 거짓말이 됩니다."""
        from arw_selector.core.develop import (
            BasicSettings, CurveSettings, LocalAdjustments,
        )

        store = PresetStore("develop_presets", tmp_path)
        original = DevelopSettings(
            basic=BasicSettings(temperature=5500, exposure=1.25),
            curve=CurveSettings(points_rgb=((10, 20), (200, 210))),
            masks=(Mask(kind=MaskType.RADIAL,
                        adjust=LocalAdjustments(exposure=0.5),
                        params={"cx": 0.5, "rx": 0.2}, label="눈밑"),),
        )
        store.save("한글 이름 (괄호)", original.to_dict())
        assert DevelopSettings.from_dict(store.load("한글 이름 (괄호)")) == original

    def test_preset_file_truncated_midway(self, tmp_path):
        """저장 중에 전원이 나가면 반쪽짜리 YAML이 남습니다."""
        store = PresetStore("develop_presets", tmp_path)
        path = store.save("반쪽", DevelopSettings().to_dict())
        text = path.read_text(encoding="utf-8")
        path.write_text(text[: len(text) // 2], encoding="utf-8")

        # 읽기는 실패해도 됩니다. 다만 목록이 무너지면 안 됩니다.
        try:
            data = store.load("반쪽")
        except ValueError:
            data = None
        if data is not None:
            assert isinstance(DevelopSettings.from_dict(data), DevelopSettings)
        assert [info.name for info in store.list()] == ["반쪽"]


class TestPresetLoadFailureIsCatchable:
    """불러오기 실패는 호출부가 잡을 수 있는 종류여야 합니다.

    프리셋 화면(gui/preset_bar.py, gui/queue_panel.py)은 전부
    `except (OSError, ValueError)`로 받아 경고창을 띄웁니다. 그 그물에
    안 걸리는 예외가 나오면 Qt 슬롯 밖으로 새어나갑니다 — 사용자에게는
    경고창 대신 앱이 사라지는 것으로 보입니다.

    YAML 파서는 `yaml.YAMLError`를 던지는데 이것은 ValueError가 **아닙니다**.
    같은 이유로 core/config.py는 이미 YAMLError를 따로 잡고 있습니다.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "data: [\n  - 닫히지 않은",      # 괄호가 안 닫힘
            "data:\n\tname: 탭",            # YAML은 탭을 금지합니다
            "data: {a: 1\n",                 # 중괄호가 안 닫힘
            "*없는앵커",                      # 정의되지 않은 앵커
            "data: !!python/object:os.system {}",  # safe_load가 거부하는 태그
            "a: 1\n a: 2\n",                 # 들여쓰기 오류
        ],
    )
    def test_malformed_yaml_raises_value_error(self, tmp_path, text):
        store = PresetStore("develop_presets", tmp_path)
        store.ensure_dir()
        (store.directory / "깨짐.yaml").write_text(text, encoding="utf-8")

        with pytest.raises(ValueError):
            store.load("깨짐")

    def test_non_utf8_file_raises_value_error(self, tmp_path):
        store = PresetStore("develop_presets", tmp_path)
        store.ensure_dir()
        (store.directory / "깨짐.yaml").write_bytes(b"\xff\xfe\x00\x01")

        with pytest.raises(ValueError):
            store.load("깨짐")

    def test_missing_file_raises_os_error(self, tmp_path):
        store = PresetStore("develop_presets", tmp_path)
        with pytest.raises(OSError):
            store.load("없는프리셋")

    def test_broken_preset_does_not_disappear_from_the_list(self, tmp_path):
        """목록에서 사라지면 사용자가 지울 수도 고칠 수도 없습니다."""
        store = PresetStore("develop_presets", tmp_path)
        store.ensure_dir()
        (store.directory / "깨짐.yaml").write_text("data: [", encoding="utf-8")
        store.save("멀쩡", DevelopSettings().to_dict())

        assert sorted(i.name for i in store.list()) == ["깨짐", "멀쩡"]


class TestCorruptCalibrationFile:
    """기종 보정값은 '없거나 깨졌으면 None'을 약속합니다.

    부르는 쪽(gui/calibration_dialog.py)은 예외를 감싸지 않으므로, 그
    약속이 깨지면 보정 안내 창을 여는 순간 앱이 사라집니다.
    """

    @pytest.fixture
    def calibration_dir(self, tmp_path, monkeypatch):
        from arw_selector.core.develop import calibration as calib

        folder = tmp_path / "calibration"
        folder.mkdir()
        monkeypatch.setattr(calib, "calibration_dir", lambda: folder)
        return folder

    @pytest.mark.parametrize(
        "content",
        [b"", b"{ \xea\xb9\xa8\xec\xa7\x90", b"\xff\xfe\x00\x01", b"null", b"[]"],
    )
    def test_broken_file_returns_none(self, calibration_dir, content):
        from arw_selector.core.develop import calibration as calib

        (calibration_dir / "TESTCAM.json").write_bytes(content)
        assert calib.load("TESTCAM") is None

    def test_broken_file_does_not_hide_the_good_ones(self, calibration_dir):
        from arw_selector.core.develop import calibration as calib

        (calibration_dir / "깨짐.json").write_bytes(b"\xff\xfe\x00\x01")
        (calibration_dir / "빈것.json").write_text("{}", encoding="utf-8")
        assert isinstance(calib.stored_cameras(), list)

    def test_empty_key_is_none(self):
        from arw_selector.core.develop import calibration as calib

        assert calib.load("") is None


class TestCorruptExportQueue:
    def _write(self, tmp_path, entries):
        path = tmp_path / "queue.json"
        path.write_text(
            json.dumps({"version": 1, "entries": entries}, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_entry_that_is_not_a_dict_is_skipped(self, tmp_path):
        """항목 하나가 깨졌다고 쌓아 둔 대기열 전체를 잃으면 안 됩니다."""
        path = self._write(tmp_path, [
            "문자열",
            None,
            123,
            {"source": str(tmp_path / "a.ARW")},
        ])
        queue = ExportQueue.load(path)
        assert len(queue) == 1

    def test_entry_with_broken_develop_is_kept_with_defaults(self, tmp_path):
        """보정이 깨졌다고 그 사진까지 대기열에서 빼면 조용히 사라집니다."""
        path = self._write(tmp_path, [
            {"source": str(tmp_path / "a.ARW"), "develop": {"curve": "문자열"}},
            {"source": str(tmp_path / "b.ARW"), "develop": {"geometry": ["x"]}},
        ])
        queue = ExportQueue.load(path)
        assert len(queue) == 2

    def test_unknown_grade_is_skipped_not_fatal(self, tmp_path):
        path = self._write(tmp_path, [
            {"source": str(tmp_path / "a.ARW"), "grade": "없는등급"},
            {"source": str(tmp_path / "b.ARW"), "grade": "keep"},
        ])
        assert len(ExportQueue.load(path)) == 1

    def test_entries_key_is_not_a_list(self, tmp_path):
        path = tmp_path / "queue.json"
        path.write_text(json.dumps({"entries": {"a": 1}}), encoding="utf-8")
        assert len(ExportQueue.load(path)) == 0

    def test_round_trip_with_full_develop(self, tmp_path):
        from arw_selector.core.develop import BasicSettings

        queue = ExportQueue()
        queue.add(tmp_path / "가 나(다).ARW",
                  DevelopSettings(basic=BasicSettings(exposure=1.5)))
        path = queue.save(tmp_path / "q.json")
        back = ExportQueue.load(path)
        assert len(back) == 1
        assert back.entries[0].develop == queue.entries[0].develop
