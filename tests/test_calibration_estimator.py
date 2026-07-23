"""채널 이득 추정이 피사체 색에 휘둘리지 않는지 지킵니다.

이 테스트가 있는 이유: 이득을 이미지 **전체 평균**으로 재면 피사체 색이
그대로 섞입니다. 붉은 옷이 화면을 채우면 "이 바디는 붉다"고 배우고, 다음
장면에서는 반대로 배웁니다. 그 흔들림이 곧 "장마다 색감이 다르다"입니다.

실측(R6M3 10장, 학습에 안 쓴 사진으로 검증): 무채색 화소만 쓰면 남은 색
차이가 0.0428 → 0.0355로 17.1% 줄었습니다.
"""

from __future__ import annotations

import numpy as np
import pytest

from arw_selector.core.develop import calibration as calib


def flat(colour, size=(120, 160)):
    """단색 판. (B, G, R) 순서입니다."""
    image = np.zeros((size[0], size[1], 3), np.uint8)
    image[:, :] = colour
    return image


def scene(neutral, subject, subject_fraction=0.6):
    """무채색 배경 + 색이 강한 피사체가 섞인 장면."""
    image = flat(neutral)
    columns = int(image.shape[1] * subject_fraction)
    image[:, :columns] = subject
    return image


def test_neutral_means_ignores_the_coloured_subject():
    """피사체가 화면 대부분을 채워도 무채색 쪽만 봐야 합니다."""
    camera = scene(neutral=(128, 128, 128), subject=(40, 40, 220))
    ours = scene(neutral=(128, 128, 128), subject=(40, 40, 220))

    pair = calib._neutral_means(camera, ours)
    assert pair is not None
    camera_mean, _ = pair
    # 무채색 영역만 봤다면 세 채널이 거의 같아야 합니다
    assert np.allclose(camera_mean, camera_mean.mean(), atol=3.0), camera_mean


def test_whole_image_means_are_dragged_by_the_subject():
    """비교용 — 전체 평균은 실제로 피사체에 끌려갑니다.

    이게 사실이 아니면 무채색 방식을 쓸 이유가 없습니다.
    """
    camera = scene(neutral=(128, 128, 128), subject=(40, 40, 220))
    means = calib._channel_means(camera)
    # R(index 2)이 확연히 큽니다 — 붉은 피사체 때문입니다
    assert means[2] > means[0] * 1.3, means


def test_neutral_estimator_recovers_a_known_cast():
    """알고 있는 색 치우침을 그대로 되찾아야 합니다."""
    camera = scene(neutral=(120, 120, 120), subject=(30, 200, 30))
    # 우리 결과가 파랑이 세고 빨강이 약한 상태라고 둡니다
    ours = camera.astype(np.float64) * np.array([1.10, 1.0, 0.90])
    ours = np.clip(ours, 0, 255).astype(np.uint8)

    pair = calib._neutral_means(camera, ours)
    assert pair is not None
    camera_mean, ours_mean = pair
    gain = (camera_mean / camera_mean.mean()) / (ours_mean / ours_mean.mean())

    # 파랑은 낮추고 빨강은 올리는 방향이어야 합니다
    assert gain[0] < 0.97, gain      # B
    assert gain[2] > 1.03, gain      # R


def test_falls_back_when_there_is_no_neutral_area():
    """무채색이 거의 없는 장면(단색 조명)에서는 None을 돌려줘야 합니다.

    그래야 부르는 쪽이 전체 평균으로 물러설 수 있습니다. 억지로 몇 화소만
    가지고 재면 그 값이 더 위험합니다.
    """
    camera = flat((30, 30, 220))    # 화면 전체가 붉음
    ours = flat((30, 30, 220))
    assert calib._neutral_means(camera, ours) is None


def test_sample_gain_survives_a_fully_saturated_scene(monkeypatch, tmp_path):
    """폴백 경로가 실제로 값을 내는지 — 예외로 끝나면 안 됩니다."""
    camera = flat((30, 30, 220))
    ours = (camera.astype(np.float64) * np.array([1.05, 1.0, 0.95]))
    ours = np.clip(ours, 0, 255).astype(np.uint8)

    monkeypatch.setattr(calib, "embedded_preview", lambda _path: camera)
    monkeypatch.setattr(
        calib, "load_demosaiced", lambda *a, **k: ours.astype(np.float32),
        raising=False,
    )

    # load_demosaiced 는 함수 안에서 import 하므로 모듈 경로로 갈아 끼웁니다
    import arw_selector.core.raw_io as raw_io

    monkeypatch.setattr(raw_io, "load_demosaiced",
                        lambda *a, **k: ours.astype(np.float32))

    gain = calib.sample_gain(tmp_path / "가짜.CR3")
    assert gain is not None, "무채색이 없다고 아예 못 재면 안 됩니다"
    low, high = calib.GAIN_LIMIT
    assert np.all((gain >= low) & (gain <= high)), gain


@pytest.mark.parametrize("fraction", [0.3, 0.5, 0.7, 0.9])
def test_estimate_is_stable_as_the_subject_grows(fraction):
    """피사체가 커져도 추정값이 크게 흔들리면 안 됩니다.

    이게 흔들린다는 것은 곧 장면마다 다른 보정값이 나온다는 뜻이고,
    사용자에게는 '장마다 색감이 다르다'로 보입니다.
    """
    camera = scene(neutral=(130, 130, 130), subject=(20, 210, 40),
                   subject_fraction=fraction)
    ours = np.clip(
        camera.astype(np.float64) * np.array([1.08, 1.0, 0.94]), 0, 255
    ).astype(np.uint8)

    pair = calib._neutral_means(camera, ours)
    assert pair is not None, f"피사체 비율 {fraction}에서 무채색을 못 찾았습니다"
    camera_mean, ours_mean = pair
    gain = (camera_mean / camera_mean.mean()) / (ours_mean / ours_mean.mean())

    # 피사체 크기와 무관하게 같은 값이 나와야 합니다
    assert 0.90 < gain[0] < 0.96, (fraction, gain)
    assert 1.04 < gain[2] < 1.10, (fraction, gain)
