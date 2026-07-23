"""lensfun DB 포맷 버전 2 -> 1 변환.

설치된 lensfun 라이브러리는 버전 1까지만 읽는데, 배포되는 최신 DB는
버전 2입니다. 그대로 주면 DB 전체가 거부되어 렌즈 인식이 통째로 죽습니다.
두 포맷의 차이는 real focal length 표기 하나뿐이라 무손실로 바꿀 수 있습니다.
"""

from __future__ import annotations

from arw_selector.core.develop import lensfun_db

V2 = """<lensdatabase version="2">
    <lens>
        <maker>Canon</maker>
        <model>Canon EF-S 18-55mm f/3.5-5.6</model>
        <mount>Canon EF-S</mount>
        <cropfactor>1.6</cropfactor>
        <calibration>
            <distortion model="ptlens" focal="18" a="0.01" b="-0.02" c="0.03" real-focal="17.3"/>
            <distortion model="ptlens" focal="55" a="0.00" b="-0.01" c="0.01"/>
            <tca model="poly3" focal="18" vr="1.0001" vb="1.0002"/>
        </calibration>
    </lens>
</lensdatabase>
"""


class TestVersionDetection:
    def test_reads_declared_version(self):
        assert lensfun_db.declared_version(V2) == 2
        assert lensfun_db.declared_version(V2.replace('version="2"', 'version="1"')) == 1

    def test_missing_version_is_none(self):
        assert lensfun_db.declared_version("<lensdatabase>") is None

    def test_needs_conversion_only_for_v2(self):
        assert lensfun_db.needs_conversion(V2)
        assert not lensfun_db.needs_conversion(V2.replace('version="2"', 'version="1"'))
        assert not lensfun_db.needs_conversion("<lensdatabase>")


class TestConversion:
    def test_version_is_lowered(self):
        assert 'version="1"' in lensfun_db.convert_to_v1(V2)
        assert 'version="2"' not in lensfun_db.convert_to_v1(V2)

    def test_real_focal_becomes_its_own_element(self):
        out = lensfun_db.convert_to_v1(V2)
        assert '<real-focal-length focal="18" real-focal="17.3"/>' in out

    def test_attribute_is_removed_from_distortion(self):
        """속성이 남아 있으면 v1 파서가 알 수 없는 속성으로 보고 거부합니다."""
        out = lensfun_db.convert_to_v1(V2)
        for line in out.splitlines():
            if "<distortion" in line:
                assert "real-focal=" not in line, line

    def test_coefficients_are_untouched(self):
        """왜곡 계수가 바뀌면 사진이 실제로 잘못 보정됩니다."""
        out = lensfun_db.convert_to_v1(V2)
        assert 'a="0.01" b="-0.02" c="0.03"' in out
        assert 'a="0.00" b="-0.01" c="0.01"' in out
        assert 'vr="1.0001" vb="1.0002"' in out

    def test_distortion_without_real_focal_is_unchanged(self):
        out = lensfun_db.convert_to_v1(V2)
        assert '<distortion model="ptlens" focal="55" a="0.00" b="-0.01" c="0.01"/>' in out

    def test_v1_passes_through_unchanged(self):
        v1 = V2.replace('version="2"', 'version="1"')
        assert lensfun_db.convert_to_v1(v1) == v1

    def test_conversion_is_idempotent(self):
        once = lensfun_db.convert_to_v1(V2)
        assert lensfun_db.convert_to_v1(once) == once

    def test_no_version_left_alone(self):
        text = "<lensdatabase><lens/></lensdatabase>"
        assert lensfun_db.convert_to_v1(text) == text


class TestLoadsIntoLibrary:
    """변환본이 실제로 lensfun에 들어가야 의미가 있습니다."""

    def test_converted_xml_is_accepted(self, tmp_path):
        from arw_selector.core.develop import optics

        if not optics.LENSFUN_AVAILABLE:
            import pytest

            pytest.skip("lensfunpy 미설치")

        import lensfunpy

        raw = tmp_path / "v2.xml"
        raw.write_text(V2, encoding="utf-8")

        # 변환 전에는 거부당합니다
        import pytest

        with pytest.raises(Exception):
            lensfunpy.Database(paths=[str(raw)])

        converted = tmp_path / "v1.xml"
        converted.write_text(lensfun_db.convert_to_v1(V2), encoding="utf-8")
        db = lensfunpy.Database(paths=[str(converted)])
        assert any("18-55" in (lens.model or "") for lens in db.lenses)

    def test_loader_converts_v2_automatically(self, tmp_path, monkeypatch):
        """사용자가 받은 v2 파일을 그대로 넣어도 동작해야 합니다."""
        from arw_selector.core.develop import optics

        (tmp_path / "custom.xml").write_text(V2, encoding="utf-8")
        prepared = optics._prepare_user_xmls(tmp_path)

        assert len(prepared) == 1
        assert optics.V1_CACHE_DIR in prepared[0], "변환본이 아니라 원본을 넘겼다"
        assert 'version="1"' in Path(prepared[0]).read_text(encoding="utf-8")
        # 원본은 그대로 둡니다
        assert 'version="2"' in (tmp_path / "custom.xml").read_text(encoding="utf-8")


from pathlib import Path  # noqa: E402  (테스트 말미에서만 씁니다)
