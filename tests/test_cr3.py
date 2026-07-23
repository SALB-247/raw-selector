"""CR3(ISO BMFF) 메타데이터 파서.

exifread는 TIFF 기반 RAW만 읽어서 CR3에서는 렌즈 정보가 통째로 비었고,
그 탓에 자동 렌즈 보정이 동작하지 않았습니다. 여기서는 사용자 파일 없이도
검증되도록 합성 BMFF를 만들어 씁니다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arw_selector.core import cr3, raw_io

SAMPLE = Path(__file__).resolve().parents[1] / "558A8911.CR3"


def _tiff(tag: int = 0x0110, value: bytes = b"TESTCAM\x00") -> bytes:
    """ASCII 태그 하나짜리 최소 리틀엔디안 TIFF 스트림."""
    data_offset = 8 + 2 + 12 + 4
    return b"".join([
        b"II*\x00", (8).to_bytes(4, "little"),      # 헤더 + IFD0 오프셋
        (1).to_bytes(2, "little"),                   # 엔트리 1개
        tag.to_bytes(2, "little"),
        (2).to_bytes(2, "little"),                   # ASCII
        len(value).to_bytes(4, "little"),
        data_offset.to_bytes(4, "little"),
        (0).to_bytes(4, "little"),                   # 다음 IFD 없음
        value,
    ])


def _box(box_type: bytes, payload: bytes) -> bytes:
    return (len(payload) + 8).to_bytes(4, "big") + box_type + payload


def _fake_cr3(cmt1: bytes) -> bytes:
    """ftyp('crx ') + moov > uuid(캐논) > CMT1 구조를 만듭니다."""
    ftyp = _box(b"ftyp", b"crx " + (0).to_bytes(4, "big") + b"crx isom")
    uuid_payload = cr3.CANON_UUID + _box(b"CMT1", cmt1)
    return ftyp + _box(b"moov", _box(b"uuid", uuid_payload))


class TestBrandDetection:
    def test_detects_crx_brand(self, tmp_path):
        path = tmp_path / "a.CR3"
        path.write_bytes(_fake_cr3(_tiff()))
        assert cr3.is_cr3(path)

    def test_rejects_non_cr3(self, tmp_path):
        path = tmp_path / "b.CR3"
        path.write_bytes(b"II*\x00" + b"\x00" * 40)  # 확장자만 CR3인 TIFF
        assert not cr3.is_cr3(path)

    def test_rejects_tiny_file(self, tmp_path):
        path = tmp_path / "c.CR3"
        path.write_bytes(b"xx")
        assert not cr3.is_cr3(path)


class TestBoxParsing:
    def test_reads_tag_from_cmt_box(self, tmp_path):
        path = tmp_path / "a.CR3"
        path.write_bytes(_fake_cr3(_tiff(value=b"MYCAMERA\x00")))
        tags = cr3.read_exif_tags(path)
        assert "Image Model" in tags
        assert "MYCAMERA" in str(tags["Image Model"])

    def test_lens_model_gets_standard_alias(self, tmp_path):
        """CMT 박스는 각자 IFD0이라 exifread가 'Image ...'로 이름 붙입니다.

        호출부가 'EXIF LensModel'로 찾을 수 있어야 합니다.
        """
        path = tmp_path / "a.CR3"
        path.write_bytes(_fake_cr3(_tiff(tag=0xA434, value=b"RF50mm F1.8\x00")))
        tags = cr3.read_exif_tags(path)
        assert "EXIF LensModel" in tags
        assert "RF50mm" in str(tags["EXIF LensModel"])

    def test_garbage_returns_empty(self, tmp_path):
        path = tmp_path / "a.CR3"
        path.write_bytes(b"\x00" * 200)
        assert cr3.read_exif_tags(path) == {}

    def test_missing_file_returns_empty(self, tmp_path):
        assert cr3.read_exif_tags(tmp_path / "없음.CR3") == {}

    def test_absurd_box_size_does_not_hang(self, tmp_path):
        """손상 파일이 터무니없는 크기를 주장해도 멈추지 않아야 합니다."""
        path = tmp_path / "a.CR3"
        ftyp = _box(b"ftyp", b"crx " + b"\x00" * 8)
        bogus = (0xFFFFFFF0).to_bytes(4, "big") + b"moov"
        path.write_bytes(ftyp + bogus)
        assert cr3.read_exif_tags(path) == {}


@pytest.mark.skipif(not SAMPLE.exists(), reason="샘플 CR3 없음")
class TestRealFile:
    """실제 EOS R6 Mark II 파일. 프리뷰 EXIF로는 못 얻던 것들입니다."""

    def test_lens_model_is_recovered(self):
        tags = cr3.read_exif_tags(SAMPLE)
        assert "EXIF LensModel" in tags, "CR3에서 렌즈를 못 읽었다"

    def test_read_metadata_now_has_lens(self):
        """raw_io가 CR3 파서를 실제로 타는지 (연결 확인)."""
        meta = raw_io.read_metadata(SAMPLE)
        assert meta.lens_model, "read_metadata에 렌즈가 안 들어왔다"
        assert meta.camera_model
        assert meta.capture_time is not None

    def test_richer_than_preview_exif(self):
        """프리뷰 JPEG EXIF(16개)보다 훨씬 많은 정보를 얻어야 의미가 있습니다."""
        assert len(cr3.read_exif_tags(SAMPLE)) > len(raw_io._tags_from_preview(SAMPLE))
