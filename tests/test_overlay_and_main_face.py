"""표시 항목 체크박스(#121)와 주 피사체 수동 전환(#117).

수동 전환에서 중요한 것은 "빨간 상자가 옮겨졌다"가 아니라 **판정이 따라
왔는가**입니다. 표시만 바꾸고 점수를 그대로 두면 고쳤는데 등급이 안 바뀌는
상태가 되어 오히려 더 헷갈립니다.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from arw_selector.core import focus as focus_mod
from arw_selector.core.develop import masks as masks_mod
from arw_selector.core.develop.settings import Mask, MaskType
from arw_selector.core.types import FocusResult, ImageRecord


# ------------------------------------------------------- 강제 주 피사체


def _two_faces() -> np.ndarray:
    """YuNet 결과를 흉내 낸 두 줄. 0번이 크고, 1번이 작습니다."""
    def row(x, y, w, h, score):
        return [x, y, w, h,
                x + w * 0.32, y + h * 0.36,
                x + w * 0.68, y + h * 0.36,
                x + w * 0.50, y + h * 0.56,
                x + w * 0.38, y + h * 0.74,
                x + w * 0.62, y + h * 0.74,
                score]

    return np.array([row(40, 40, 160, 180, 0.99),
                     row(400, 60, 60, 70, 0.95)], np.float32)


@pytest.fixture
def patched_detector(monkeypatch):
    faces = _two_faces()
    monkeypatch.setattr(focus_mod, "detect_faces", lambda image: faces)
    return faces


def _noisy_image() -> np.ndarray:
    rng = np.random.default_rng(7)
    return rng.integers(0, 255, (400, 640, 3), dtype=np.uint8)


def test_force_main_face_overrides_automatic_pick(patched_detector):
    automatic = focus_mod.analyze_focus(_noisy_image())
    forced = focus_mod.analyze_focus(_noisy_image(), force_main_face=1)

    assert forced.main_face == 1
    assert automatic.main_face != 1 or automatic.roi != forced.roi


def test_forced_face_moves_the_roi(patched_detector):
    """ROI가 새 얼굴 안으로 들어가야 판정이 그 얼굴을 본 것입니다."""
    forced = focus_mod.analyze_focus(_noisy_image(), force_main_face=1)
    face = forced.faces[1]
    x, y, w, h = forced.roi

    cx, cy = x + w / 2, y + h / 2
    assert face[0] - w <= cx <= face[0] + face[2] + w
    assert face[1] - h <= cy <= face[1] + face[3] + h


def test_forced_face_recomputes_sharpness(patched_detector):
    """선명도가 새 ROI에서 다시 나와야 점수가 따라옵니다."""
    first = focus_mod.analyze_focus(_noisy_image(), force_main_face=0)
    second = focus_mod.analyze_focus(_noisy_image(), force_main_face=1)
    assert first.sharpness != second.sharpness


def test_out_of_range_force_falls_back(patched_detector):
    """없는 번호를 주면 자동 선정으로 돌아갑니다 — 예외로 죽으면 안 됩니다."""
    forced = focus_mod.analyze_focus(_noisy_image(), force_main_face=99)
    automatic = focus_mod.analyze_focus(_noisy_image())
    assert forced.main_face == automatic.main_face


# ------------------------------------------------------- 정규화 좌표


def _focus(**kwargs) -> FocusResult:
    base = dict(sharpness=50.0, laplacian=50.0, tenengrad=50.0,
                source=focus_mod.FocusSource.FACE)
    base.update(kwargs)
    return FocusResult(**base)


def test_main_face_norm_is_a_ratio():
    record = ImageRecord(
        path=Path("x.ARW"),
        focus=_focus(
            roi=(0, 0, 10, 10),
            faces=((100, 50, 200, 250),),
            main_face=0,
            source_width=1000,
            source_height=500,
        ),
    )
    assert record.main_face_norm == pytest.approx((0.1, 0.1, 0.2, 0.5))


def test_main_face_norm_needs_a_reference_size():
    """기준 크기가 없는 예전 캐시에서는 좌표를 해석할 수 없습니다."""
    record = ImageRecord(
        path=Path("x.ARW"),
        focus=_focus(faces=((10, 10, 20, 20),), main_face=0),
    )
    assert record.main_face_norm is None


def test_main_face_norm_is_none_without_faces():
    assert ImageRecord(path=Path("x.ARW")).main_face_norm is None


# ------------------------------------------------------- 마스크가 따라오는가


def test_mask_follows_the_given_main_face():
    """힌트를 주면 마스크가 그 얼굴에 걸려야 합니다.

    힌트가 없으면 마스크는 스스로 다시 고르는데, 그러면 사용자가 화면에서
    주 피사체를 바꿔도 마스크만 옛 얼굴에 남습니다.
    """
    faces = _two_faces().astype(np.float64)
    detect = np.zeros((400, 640, 3), np.uint8)
    mask = Mask(kind=MaskType.FACE, params={"target": "main"},
                size=100, opacity=100, feather=40)

    # 작은 쪽(1번) 얼굴 자리를 정규화해 힌트로 넘깁니다
    hint = (400 / 640, 60 / 400, 60 / 640, 70 / 400)
    chosen = masks_mod.select_faces(faces, mask, detect, hint)

    assert len(chosen) == 1
    assert chosen[0][0] == pytest.approx(400, abs=1)


def test_nearest_face_ignores_index_order():
    """힌트는 위치로 맞춥니다 — 검출 순서가 달라도 같은 얼굴을 찾아야 합니다."""
    faces = _two_faces().astype(np.float64)
    reversed_faces = faces[::-1].copy()
    hint = (40 / 640, 40 / 400, 160 / 640, 180 / 400)

    a = masks_mod._nearest_face(faces, hint, 400, 640)
    b = masks_mod._nearest_face(reversed_faces, hint, 400, 640)
    assert a[0] == b[0] and a[1] == b[1]


# ------------------------------------------------------- 잘린 영역 좌표 변환


def test_remap_box_into_a_crop():
    box = (0.5, 0.5, 0.1, 0.1)
    region = (0.4, 0.4, 0.8, 0.8)  # 가운데 40% 구간

    from arw_selector.gui.loupe import _remap_box

    result = _remap_box(box, region)
    assert result == pytest.approx((0.25, 0.25, 0.25, 0.25))


def test_remap_box_drops_boxes_outside_the_crop():
    """확대해서 얼굴이 화면 밖으로 나가면 힌트를 버립니다.

    그대로 두면 마스크가 잘린 조각 안의 엉뚱한 자리를 주 피사체로 봅니다.
    """
    from arw_selector.gui.loupe import _remap_box

    assert _remap_box((0.9, 0.9, 0.05, 0.05), (0.0, 0.0, 0.4, 0.4)) is None
    assert _remap_box(None, (0.0, 0.0, 1.0, 1.0)) is None


def test_remap_box_survives_a_degenerate_region():
    from arw_selector.gui.loupe import _remap_box

    assert _remap_box((0.1, 0.1, 0.1, 0.1), (0.5, 0.5, 0.5, 0.5)) is None
