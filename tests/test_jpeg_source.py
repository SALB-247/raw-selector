"""RAW 없이 JPEG·HEIF만으로 판정·보정하기.

RAW를 안 찍는 사람들이 있습니다. 그런 파일도 셀렉트와 보정이 되어야
합니다 — 다만 latitude가 다릅니다. 이미 현상되어 8비트로 구워진 결과라
날아간 하이라이트는 돌아오지 않습니다.

**RAW가 있으면 RAW를 씁니다.** 카메라가 RAW+JPEG으로 기록하면 같은 사진이
두 벌 있는데, 둘 다 넣으면 장수와 keep 비율이 통째로 두 배가 됩니다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

from arw_selector.core import raw_io


def _write_jpeg(path: Path, size=(60, 80), value=128) -> Path:
    image = np.full((size[0], size[1], 3), value, np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    path.write_bytes(buffer.tobytes())
    return path


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not really a raw file")
    return path


# ------------------------------------------------------- 확장자 판별


def test_raw_and_image_are_distinguished():
    assert raw_io.is_raw(Path("a.ARW"))
    assert raw_io.is_raw(Path("a.cr3"))
    assert not raw_io.is_raw(Path("a.jpg"))

    assert raw_io.is_editable_image(Path("a.JPG"))
    assert raw_io.is_editable_image(Path("a.jpeg"))
    assert raw_io.is_editable_image(Path("a.HIF"))
    assert raw_io.is_editable_image(Path("a.heic"))
    assert not raw_io.is_editable_image(Path("a.ARW"))


def test_the_two_sets_do_not_overlap():
    """겹치면 같은 파일이 두 갈래로 흘러 어느 쪽이 이길지 모호해집니다."""
    assert not (raw_io.RAW_EXTENSIONS & raw_io.EDITABLE_IMAGE_EXTENSIONS)


# ------------------------------------------------------- 파일 찾기


def test_jpeg_only_folder_is_found(tmp_path):
    _write_jpeg(tmp_path / "a.jpg")
    _write_jpeg(tmp_path / "b.JPG")
    found = raw_io.iter_raw_files(tmp_path)
    assert {p.name for p in found} == {"a.jpg", "b.JPG"}


def test_raw_wins_over_its_jpeg_twin(tmp_path):
    """RAW+JPEG 기록. 둘 다 넣으면 장수가 두 배가 됩니다."""
    _touch(tmp_path / "DSC001.ARW")
    _write_jpeg(tmp_path / "DSC001.JPG")
    _write_jpeg(tmp_path / "DSC002.JPG")

    found = raw_io.iter_raw_files(tmp_path)
    assert {p.name for p in found} == {"DSC001.ARW", "DSC002.JPG"}


def test_pairing_ignores_extension_case(tmp_path):
    """카메라마다 DSC001.ARW / dsc001.jpg 처럼 대소문자가 섞입니다."""
    _touch(tmp_path / "DSC001.ARW")
    _write_jpeg(tmp_path / "dsc001.jpg")
    assert [p.name for p in raw_io.iter_raw_files(tmp_path)] == ["DSC001.ARW"]


def test_pairing_is_per_folder(tmp_path):
    """이름만 같고 다른 폴더면 다른 촬영일 수 있습니다."""
    _touch(tmp_path / "day1" / "DSC001.ARW")
    _write_jpeg(tmp_path / "day2" / "DSC001.jpg")

    found = raw_io.iter_raw_files(tmp_path)
    assert {p.name for p in found} == {"DSC001.ARW", "DSC001.jpg"}


def test_hif_is_paired_too(tmp_path):
    """소니는 RAW+HEIF로도 기록합니다."""
    _touch(tmp_path / "DSC001.ARW")
    _touch(tmp_path / "DSC001.HIF")
    assert [p.name for p in raw_io.iter_raw_files(tmp_path)] == ["DSC001.ARW"]


def test_export_output_folders_are_skipped(tmp_path):
    """내보낸 결과를 다시 읽으면 원본이 두 번 처리됩니다."""
    _write_jpeg(tmp_path / "a.jpg")
    _write_jpeg(tmp_path / "_keep" / "a.jpg")
    assert [p.parent.name for p in raw_io.iter_raw_files(tmp_path)] == [tmp_path.name]


def test_other_files_are_ignored(tmp_path):
    _write_jpeg(tmp_path / "a.jpg")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    (tmp_path / "a.xmp").write_text("x", encoding="utf-8")
    assert [p.name for p in raw_io.iter_raw_files(tmp_path)] == ["a.jpg"]


# ------------------------------------------------------- 읽기


def test_jpeg_loads_as_preview(tmp_path):
    path = _write_jpeg(tmp_path / "a.jpg", size=(40, 60), value=200)
    image = raw_io.load_preview(path)
    assert image.shape == (40, 60, 3)
    assert image.dtype == np.uint8


def test_preview_respects_the_size_limit(tmp_path):
    path = _write_jpeg(tmp_path / "a.jpg", size=(200, 400))
    image = raw_io.load_preview(path, max_long_edge=100)
    assert max(image.shape[:2]) <= 100


def test_jpeg_loads_as_a_develop_base(tmp_path):
    """보정은 float으로 돕니다. 8비트 그대로 넘기면 중간 계산에서 뭉갭니다."""
    path = _write_jpeg(tmp_path / "a.jpg", value=100)
    base = raw_io.load_demosaiced(path)
    assert base.dtype == np.float32
    assert 0.0 <= float(base.min()) and float(base.max()) <= 255.0


def test_half_size_halves_the_base(tmp_path):
    path = _write_jpeg(tmp_path / "a.jpg", size=(100, 200))
    full = raw_io.load_demosaiced(path)
    half = raw_io.load_demosaiced(path, half_size=True)
    assert half.shape[0] == full.shape[0] // 2


def test_a_broken_file_raises_preview_error(tmp_path):
    """한 장이 깨져도 배치 전체가 멈추면 안 됩니다 — 잡을 수 있는 예외로."""
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"this is not a jpeg")
    with pytest.raises(raw_io.PreviewError):
        raw_io.load_preview(broken)


def test_heif_without_a_decoder_says_how_to_fix_it(tmp_path, monkeypatch):
    """필수 의존이지만 배포본에서 빠질 수 있습니다.

    그때 앱이 통째로 죽지 않고 그 파일만 실패해야 하고, 원인이 무엇인지
    말해야 합니다. 여기서는 임포트가 실패하는 상황을 흉내 냅니다.
    """
    import builtins

    real_import = builtins.__import__

    def without_pillow_heif(name, *args, **kwargs):
        if name == "pillow_heif":
            raise ImportError("No module named 'pillow_heif'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_pillow_heif)
    monkeypatch.delitem(sys.modules, "pillow_heif", raising=False)

    path = tmp_path / "a.hif"
    path.write_bytes(b"\x00\x00\x00\x28ftypheix" + b"\x00" * 32)
    with pytest.raises(raw_io.PreviewError) as info:
        raw_io.load_preview(path)
    assert "pillow-heif" in str(info.value)


# ------------------------------------------------------- 판정


def test_focus_analysis_runs_on_a_jpeg(tmp_path):
    """프리뷰가 되면 판정도 됩니다 — 초점 분석은 BGR 배열만 봅니다."""
    from arw_selector.core.focus import analyze_focus

    rng = np.random.default_rng(0)
    noisy = rng.integers(0, 255, (300, 400, 3), dtype=np.uint8)
    path = tmp_path / "a.jpg"
    ok, buffer = cv2.imencode(".jpg", noisy)
    assert ok
    path.write_bytes(buffer.tobytes())

    result = analyze_focus(raw_io.load_preview(path))
    assert result.sharpness > 0
    assert result.frame_sharpness > 0


# ------------------------------------------------------- 내보내기


def test_export_renders_a_jpeg_source(tmp_path):
    """복사가 아니라 다시 그려야 합니다 — 안 그러면 보정이 사라집니다.

    export_image는 RAW를 전제로 쓰던 경로라, JPEG에서도 끝까지 도는지
    한 번은 실제로 돌려 봐야 합니다.
    """
    from dataclasses import replace

    from arw_selector.core.develop import DevelopSettings
    from arw_selector.core.develop.engine import export_image

    source = tmp_path / "a.jpg"
    ok, buffer = cv2.imencode(".jpg", np.full((120, 160, 3), 110, np.uint8))
    assert ok
    source.write_bytes(buffer.tobytes())

    settings = DevelopSettings()
    settings = replace(settings, basic=replace(settings.basic, exposure=1.0))
    out = export_image(source, tmp_path / "out.jpg", settings)

    assert out.is_file()
    rendered = cv2.imread(str(out))
    assert rendered is not None
    assert rendered.shape[:2] == (120, 160)
    # 노출을 올렸으니 원본보다 밝아야 합니다. 그대로면 복사된 것입니다.
    assert float(rendered.mean()) > 115


def _record(path: Path):
    from arw_selector.core.types import Grade, ImageRecord

    record = ImageRecord(path=path)
    record.manual_grade = Grade.KEEP
    return record


def test_a_developed_jpeg_does_not_ship_its_own_twin(tmp_path):
    """보정한 JPEG은 현상본만 나가야 합니다.

    copy_raw는 '나중에 다시 현상할 수 있게 RAW를 남긴다'는 뜻입니다.
    원본이 JPEG이면 남길 RAW가 없고, 현상본과 형식·이름이 같아
    IMG_0001.jpg 옆에 IMG_0001_1.jpg가 생깁니다. 어느 쪽이 보정본인지
    알 수 없습니다. 원본은 원래 폴더에 그대로 있습니다.
    """
    from dataclasses import replace

    from arw_selector.core import export as export_module
    from arw_selector.core.develop import DevelopSettings

    source = _write_jpeg(tmp_path / "src" / "a.jpg", value=110)
    record = _record(source)
    settings = DevelopSettings()
    record.develop = replace(
        settings, basic=replace(settings.basic, exposure=1.0))

    out = tmp_path / "out"
    result = export_module.export_records([record], out)

    assert not result.failed
    produced = sorted(p.name for p in out.rglob("*.jpg"))
    assert produced == ["a.jpg"], f"원본과 현상본이 함께 나갔습니다: {produced}"
    assert result.rendered == 1

    # 나간 것이 현상본인지 확인합니다 — 원본을 복사한 것이면 안 됩니다.
    written = cv2.imread(str(next(out.rglob("a.jpg"))))
    assert float(written.mean()) > 130


def test_an_untouched_jpeg_still_goes_out(tmp_path):
    """보정을 안 걸었으면 원본이 그대로 나가야 합니다."""
    from arw_selector.core import export as export_module

    source = _write_jpeg(tmp_path / "src" / "a.jpg", value=110)
    out = tmp_path / "out"
    result = export_module.export_records([_record(source)], out)

    assert not result.failed
    assert result.moved == 1
    assert [p.name for p in out.rglob("*.jpg")] == ["a.jpg"]
