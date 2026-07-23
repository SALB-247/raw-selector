"""광학 커버리지 — 렌즈명 표기 변형 매칭과 사용자 DB 확장."""

from __future__ import annotations

import pytest

from arw_selector.core.develop import optics


class TestLensNameVariants:
    """EXIF 표기와 lensfun 표기가 달라 못 찾던 경우를 넓혀 줍니다."""

    def test_original_comes_first(self):
        variants = optics._lens_name_variants("E 18-135mm F3.5-5.6 OSS")
        assert variants[0] == "E 18-135mm F3.5-5.6 OSS"

    def test_aperture_slash_form_is_tried(self):
        """lensfun은 f/3.5 표기를 씁니다."""
        variants = optics._lens_name_variants("E 18-135mm F3.5-5.6 OSS")
        assert any("f/3.5" in v for v in variants)

    def test_maker_model_code_is_stripped(self):
        """탐론 A069 같은 모델 코드가 붙으면 이름이 안 맞습니다."""
        variants = optics._lens_name_variants("E 50-300mm F4.5-6.3 A069")
        assert any("A069" not in v for v in variants)

    def test_mount_prefix_is_stripped(self):
        variants = optics._lens_name_variants("FE 70-200mm F2.8 GM OSS II")
        assert any(v.startswith("70-200mm") for v in variants)

    def test_variants_are_unique(self):
        variants = optics._lens_name_variants("50mm f/1.8")
        assert len(variants) == len({v.lower() for v in variants})

    def test_plain_name_is_safe(self):
        assert optics._lens_name_variants("50mm") == ["50mm"]


class TestOtherManufacturers:
    """제조사마다 EXIF 표기 방식이 달라서, 한 방식만 가정하면 통째로 못 찾습니다."""

    @pytest.mark.parametrize(
        "exif_name,expected_fragment",
        [
            ("FE 70-200mm F2.8 GM OSS II", "70-200mm"),      # Sony
            ("RF100-500mm F4.5-7.1 L IS USM", "100-500mm"),  # Canon
            ("NIKKOR Z 24-70mm f/2.8 S", "24-70mm"),         # Nikon
            ("XF18-55mmF2.8-4 R LM OIS", "18-55mm"),         # Fujifilm
            ("OLYMPUS M.12-40mm F2.8", "12-40mm"),           # Olympus
            ("LUMIX G VARIO 12-60/F3.5-5.6", "12-60"),       # Panasonic
            ("smc PENTAX-DA 18-55mm F3.5-5.6", "18-55mm"),   # Pentax
            ("E 50-300mm F4.5-6.3 A069", "50-300mm"),        # 탐론 모델코드
        ],
    )
    def test_maker_prefix_is_stripped(self, exif_name, expected_fragment):
        variants = optics._lens_name_variants(exif_name)
        assert any(v.lstrip().startswith(expected_fragment) for v in variants), (
            f"{exif_name} 에서 제조사 접두사를 못 떼어냈다: {variants}"
        )

    @pytest.mark.parametrize(
        "model,make,expected",
        [
            ("EOS R6 Mark II", "Canon", "Canon EOS R6m2"),
            ("EOS R6 Mark II", None, "Canon EOS R6m2"),   # Make 없어도 추정
            ("ILCE-6700", "Sony", "Sony ILCE-6700"),
            ("X-T5", "FUJIFILM", "Fujifilm X-T5"),
            ("E-M1MarkIII", "OLYMPUS", "Olympus E-M1m3"),
        ],
    )
    def test_camera_name_gets_maker_and_mark_form(self, model, make, expected):
        variants = optics._camera_name_variants(model, make)
        assert expected in variants, f"{model}({make}) -> {variants}"


class TestLensTagFallback:
    """표준 EXIF LensModel을 안 쓰는 기종 대응."""

    def test_prefers_standard_tag(self):
        from arw_selector.core import raw_io

        tags = {"EXIF LensModel": "FE 24mm F1.4 GM", "MakerNote Lens": "무시됨"}
        assert raw_io._lens_from_tags(tags) == "FE 24mm F1.4 GM"

    def test_falls_back_to_makernote(self):
        from arw_selector.core import raw_io

        tags = {"MakerNote Lens": "smc PENTAX-DA 35mm F2.4"}
        assert raw_io._lens_from_tags(tags) == "smc PENTAX-DA 35mm F2.4"

    def test_ignores_numeric_lens_id(self):
        """LensType은 '61182' 같은 숫자 ID로 나올 때가 있어 이름으로 못 씁니다."""
        from arw_selector.core import raw_io

        assert raw_io._lens_from_tags({"MakerNote LensType": "61182"}) is None

    def test_ignores_placeholders(self):
        from arw_selector.core import raw_io

        assert raw_io._lens_from_tags({"EXIF LensModel": "Unknown"}) is None

    def test_no_tags_is_none(self):
        from arw_selector.core import raw_io

        assert raw_io._lens_from_tags({}) is None


class TestUserLensDatabase:
    def test_user_dir_travels_with_the_app(self):
        """렌즈 프로필도 앱 폴더 안에 둡니다 — 앱을 옮기면 함께 가야 합니다."""
        from arw_selector.core import appinfo

        path = optics.user_lens_db_dir()
        assert path.name == "lensfun"
        assert path.parent == appinfo.data_dir()

    @pytest.mark.skipif(not optics.LENSFUN_AVAILABLE, reason="lensfunpy 미설치")
    def test_dropped_in_xml_is_actually_loaded(self, tmp_path, monkeypatch):
        """사용자가 XML을 넣으면 실제로 DB에 들어와야 합니다.

        lensfunpy의 paths는 폴더가 아니라 **XML 파일 목록**을 받습니다.
        폴더를 넘기면 Permission denied로 조용히 실패해, 기능이 있는 척만
        하게 됩니다(실제로 그랬습니다).
        """
        monkeypatch.setattr(optics, "user_lens_db_dir", lambda: tmp_path)
        (tmp_path / "custom.xml").write_text(
            '<lensdatabase version="1"><lens>'
            "<maker>PROBEMAKER</maker><model>PROBELENS 999-1000mm F9.9</model>"
            "<mount>Sony E</mount><cropfactor>1.534</cropfactor><calibration>"
            '<distortion model="ptlens" focal="999" a="0" b="0" c="0"/>'
            "</calibration></lens></lensdatabase>",
            encoding="utf-8",
        )
        optics._database.cache_clear()
        try:
            db = optics._database()
            assert db is not None
            models = [lens.model or "" for lens in db.lenses]
            assert any("PROBELENS" in m for m in models), "사용자 XML이 로드되지 않았다"
            # 번들 DB도 함께 살아 있어야 합니다
            assert len(db.lenses) > 100
        finally:
            optics._database.cache_clear()

    def test_reload_reflects_changes(self, tmp_path, monkeypatch):
        """앱을 켜 둔 채 XML을 넣어도 다시 읽으면 반영돼야 합니다."""
        monkeypatch.setattr(optics, "user_lens_db_dir", lambda: tmp_path)
        optics._database.cache_clear()
        try:
            before = optics.database_coverage()
            (tmp_path / "extra.xml").write_text(
                '<lensdatabase version="1"><lens>'
                "<maker>PROBEMAKER2</maker><model>PROBELENS2 500mm F9</model>"
                "<mount>Sony E</mount><cropfactor>1.0</cropfactor><calibration>"
                '<distortion model="ptlens" focal="500" a="0" b="0" c="0"/>'
                "</calibration></lens></lensdatabase>",
                encoding="utf-8",
            )
            after = optics.reload_database()
            if optics.LENSFUN_AVAILABLE:
                assert after[1] == before[1] + 1, f"{before} -> {after}"
        finally:
            optics._database.cache_clear()

    def test_missing_user_dir_does_not_break_db(self):
        """사용자 폴더가 없어도 번들 DB로 계속 동작해야 합니다."""
        cameras, lenses = optics.database_coverage()
        if not optics.LENSFUN_AVAILABLE:
            pytest.skip("lensfunpy 미설치")
        assert cameras > 0 and lenses > 0


@pytest.mark.skipif(not optics.LENSFUN_AVAILABLE, reason="lensfunpy 미설치")
class TestRealMatching:
    """실제 번들 DB로 매칭되는지 확인합니다."""

    def _match(self, camera_model: str, lens: str):
        return optics.find_lens_by_name(camera_model, lens)

    @pytest.mark.parametrize(
        "camera,lens",
        [
            ("ILCE-6700", "E 18-135mm F3.5-5.6 OSS"),
            ("Canon EOS R6 Mark II", "RF24-105mm F4 L IS USM"),
        ],
    )
    def test_common_lenses_are_found(self, camera, lens):
        assert self._match(camera, lens).found, f"{lens} 를 찾지 못했다"

    def test_wrong_focal_length_is_rejected(self):
        """lensfun의 loose_search는 아무 이름에나 렌즈를 하나 물려 줍니다.

        실측: "존재하지않는렌즈 999mm" -> "E 24mm F2.8". 그대로 쓰면 999mm
        렌즈에 24mm 왜곡 프로필이 적용됩니다 — 보정을 안 하느니만 못합니다.
        """
        match = optics.find_lens_by_name("ILCE-6700", "존재하지않는렌즈 999mm")
        assert not match.found, f"엉뚱한 렌즈가 매칭됐다: {match.lens}"

    def test_actual_focal_length_rejects_wrong_lens(self):
        """363mm로 찍은 사진에 24-105mm 프로필이 붙으면 왜곡이 엉뚱해집니다."""
        db = optics._database()
        cameras = optics._find_cameras_loose(db, "Canon EOS R6m2")
        assert cameras, "테스트용 바디를 못 찾았다"

        wide = optics._find_lenses_loose(db, cameras[0], "RF24-105mm F4 L IS USM", 363.0)
        assert not wide, "촬영 초점거리와 안 맞는 렌즈가 통과했다"

        tele = optics._find_lenses_loose(
            db, cameras[0], "RF100-500mm F4.5-7.1 L IS USM", 363.0
        )
        assert tele, "실제로 그 초점거리를 찍을 수 있는 렌즈는 통과해야 한다"

    def test_unregistered_modern_lens_is_not_faked(self):
        """번들 DB에 없는 신형 렌즈(탐론 A069)를 억지로 맞추면 안 됩니다."""
        match = optics.find_lens_by_name("ILCE-6700", "E 50-300mm F4.5-6.3 A069")
        if match.found:
            # 찾았다면 초점거리가 실제로 겹쳐야 합니다
            assert "50" in match.lens or "300" in match.lens
