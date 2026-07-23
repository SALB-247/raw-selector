"""JPEG·HEIF에서 센서 기반 항목이 실제로 꺼지는지.

카메라가 이미 프로파일·기종 색·렌즈 보정을 적용해 구워 넣은 결과라,
한 번 더 걸면 이중 보정이 됩니다. 그래서 적용도 안 하고 화면에서도
잠급니다 — 만질 수 있게 두면 적용되는 줄 알고 값을 맞추다가 왜 아무
변화가 없는지 찾게 됩니다.

**색온도는 잠그지 않습니다.** 절대 Kelvin 변환은 못 해도 상대적인
따뜻/차갑게는 그대로 되고, 실제로 손댈 일이 있습니다.
"""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from arw_selector.core import raw_io  # noqa: E402
from conftest import destroy_all_widgets, destroy_widget  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from arw_selector.gui import theme

    instance = QApplication.instance() or QApplication([])
    theme.apply_app_theme(instance)
    yield instance
    destroy_all_widgets(instance)


@pytest.fixture
def panel(app):
    from arw_selector.gui.develop_panel import DevelopPanel

    widget = DevelopPanel()
    yield widget
    destroy_widget(widget, app)


def _jpeg(path: Path, value=140) -> Path:
    image = np.full((60, 80, 3), value, np.uint8)
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    path.write_bytes(buffer.tobytes())
    return path


# ------------------------------------------------------- 화면


def test_sensor_only_controls_are_locked_for_jpeg(panel):
    panel.set_raw_source(False)
    assert not panel.optics_auto.isEnabled()
    assert not panel.optics_auto.isChecked()
    assert not panel.calibration_button.isEnabled()


def test_they_come_back_for_raw(panel):
    panel.set_raw_source(False)
    panel.set_raw_source(True)
    assert panel.optics_auto.isEnabled()
    assert panel.calibration_button.isEnabled()


def test_a_note_explains_why(panel):
    """잠긴 이유가 없으면 고장으로 보입니다.

    isVisible()은 부모가 화면에 없으면 False라 여기서는 못 씁니다.
    숨김 여부만 봅니다.
    """
    panel.set_raw_source(False)
    assert not panel.source_note.isHidden()
    assert panel.source_note.text().strip()

    panel.set_raw_source(True)
    assert panel.source_note.isHidden()


def test_calibration_values_are_not_shown_for_jpeg(panel):
    """값이 보이면 지금 걸려 있다고 읽힙니다."""
    panel.set_camera("SONY", "ILCE-6700")
    panel.set_raw_source(False)
    assert panel.calibration_label.text() == ""


def test_temperature_stays_available(panel):
    """색온도는 JPEG에서도 만질 수 있어야 합니다."""
    panel.set_raw_source(False)
    row = panel.rows["basic.temperature"]
    assert row.slider.isEnabled()


# ------------------------------------------------------- 실제 적용


def test_jpeg_base_skips_profile_and_calibration(tmp_path, monkeypatch):
    """화면만 잠그고 실제로는 걸리면 아무 소용이 없습니다."""
    from arw_selector.core.develop import calibration as calib

    path = _jpeg(tmp_path / "a.jpg")

    called = []
    monkeypatch.setattr(calib, "load", lambda *a, **k: called.append(a))
    monkeypatch.setattr(
        "arw_selector.core.develop.engine.apply_camera_profile",
        lambda image: called.append("profile") or image,
    )

    raw_io.load_demosaiced(path)
    assert called == [], "JPEG에 프로파일·보정이 적용됐습니다"


def test_temperature_still_shifts_a_jpeg(tmp_path):
    """상대적인 따뜻/차갑게는 센서 데이터 없이도 됩니다."""
    from dataclasses import replace

    from arw_selector.core.develop import DevelopSettings
    from arw_selector.core.develop.engine import apply_settings

    base = raw_io.load_demosaiced(_jpeg(tmp_path / "a.jpg", value=140))

    warm = apply_settings(base.copy(), replace(
        DevelopSettings(), basic=replace(DevelopSettings().basic, temperature=8000)))
    cool = apply_settings(base.copy(), replace(
        DevelopSettings(), basic=replace(DevelopSettings().basic, temperature=3000)))

    # 따뜻하게 하면 빨강이 파랑보다 올라갑니다 (BGR 배열)
    assert float(warm[..., 2].mean()) > float(cool[..., 2].mean())
    assert float(warm[..., 0].mean()) < float(cool[..., 0].mean())


def test_untouched_temperature_leaves_the_jpeg_alone(tmp_path):
    """기본은 '손대지 않음'이어야 합니다 — 열자마자 색이 변하면 안 됩니다."""
    from arw_selector.core.develop import DevelopSettings
    from arw_selector.core.develop.engine import apply_settings

    base = raw_io.load_demosaiced(_jpeg(tmp_path / "a.jpg", value=140))
    assert DevelopSettings().basic.temperature <= 0

    out = apply_settings(base.copy(), DevelopSettings())
    assert np.allclose(out, base, atol=1.0)
