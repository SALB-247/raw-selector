"""극단적인 폴더·파일·이미지로 배치를 흔듭니다.

4000장 배치는 사용자가 넘긴 카드 그대로입니다. 우리가 고를 수 없습니다 —
0바이트로 끝난 파일, 이름만 RAW인 텍스트, 한글·이모지·아주 긴 파일명,
괄호와 공백이 든 경로가 섞여 들어옵니다. 그중 하나가 배치를 세우면
사용자는 몇 분을 기다린 뒤 아무것도 못 얻습니다.

프로세스 풀은 일부러 태우지 않습니다. 여기서 볼 것은 병렬 처리가 아니라
**한 장을 어떻게 다루는가**이고, spawn 방식으로 워커를 띄우면 테스트가
느려질 뿐 검사 내용은 같습니다.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from arw_selector.core import cache as cache_mod
from arw_selector.core import focus, grouping, scoring
from arw_selector.core.appinfo import CACHE_DIR_NAME, LEGACY_CACHE_DIR_NAMES
from arw_selector.core.config import AnalyzeConfig, GroupConfig, ScoreConfig
from arw_selector.core.pipeline import analyze_file
from arw_selector.core.raw_io import iter_raw_files
from arw_selector.core.types import Grade


# 실제 카드에서 나올 법한 이름들. 윈도우 탐색기에서 전부 만들 수 있습니다.
AWKWARD_NAMES = [
    "사진 001.ARW",
    "여행(2026) 봄.ARW",
    "이모지🎉컷.ARW",
    "  앞뒤 공백  .ARW",
    "점.이.많은.이름.ARW",
    "긴" * 90 + ".ARW",
    "MiXeD.aRw",
    "대문자.ARW",
]


class TestFolderScan:
    def test_empty_folder(self, tmp_path):
        assert iter_raw_files(tmp_path) == []

    def test_folder_that_does_not_exist(self, tmp_path):
        """분석 도중 사용자가 카드를 뽑으면 실제로 이 상태가 됩니다."""
        assert iter_raw_files(tmp_path / "없는폴더") == []

    def test_path_that_is_a_file(self, tmp_path):
        target = tmp_path / "파일.txt"
        target.write_text("x", encoding="utf-8")
        assert iter_raw_files(target) == []

    def test_awkward_names_are_all_found(self, tmp_path):
        folder = tmp_path / "카드 (1) 백업"
        folder.mkdir()
        made = []
        for name in AWKWARD_NAMES:
            path = folder / name
            try:
                path.write_bytes(b"x")
            except OSError:
                continue  # 파일시스템이 못 만드는 이름은 우리 문제가 아닙니다
            made.append(path.name)

        found = {p.name for p in iter_raw_files(folder)}
        assert found == set(made)

    def test_single_photo_folder(self, tmp_path):
        (tmp_path / "하나.ARW").write_bytes(b"x")
        assert len(iter_raw_files(tmp_path)) == 1

    def test_output_folders_are_skipped(self, tmp_path):
        """내보낸 결과를 다시 스캔하면 원본을 두 번 처리합니다."""
        (tmp_path / "원본.ARW").write_bytes(b"x")
        for name in ("_keep", "_review", "_reject"):
            sub = tmp_path / name
            sub.mkdir()
            (sub / "사본.ARW").write_bytes(b"x")
        assert [p.name for p in iter_raw_files(tmp_path)] == ["원본.ARW"]

    def test_cache_folder_is_skipped(self, tmp_path):
        """캐시 썸네일이 사진으로 잡히면 두 번째 스캔부터 장수가 부풉니다.

        0.15에서 JPEG도 판정 대상이 되면서 `.raw_selector_cache/thumbs/*.jpg`
        가 사진으로 잡혔습니다. 한 번 분석한 폴더를 다시 열면 장수와 장면
        수가 썸네일 개수만큼 늘어납니다.
        """
        (tmp_path / "원본.ARW").write_bytes(b"x")
        for cache_name in (CACHE_DIR_NAME, *LEGACY_CACHE_DIR_NAMES):
            thumbs = tmp_path / cache_name / "thumbs"
            thumbs.mkdir(parents=True)
            (thumbs / "8e54ac4d604422caf9b7.jpg").write_bytes(b"x")
        assert [p.name for p in iter_raw_files(tmp_path)] == ["원본.ARW"]

    def test_deeply_nested(self, tmp_path):
        deep = tmp_path
        for index in range(12):
            deep = deep / f"{index}단계"
        deep.mkdir(parents=True)
        (deep / "깊은.ARW").write_bytes(b"x")
        assert len(iter_raw_files(tmp_path)) == 1
        assert iter_raw_files(tmp_path, recursive=False) == []

    def test_result_is_sorted(self, tmp_path):
        """순서가 흔들리면 {index} 파일명과 화면 순서가 실행마다 달라집니다."""
        for name in ("c.ARW", "a.ARW", "b.ARW"):
            (tmp_path / name).write_bytes(b"x")
        assert [p.name for p in iter_raw_files(tmp_path)] == ["a.ARW", "b.ARW", "c.ARW"]


class TestSingleFileAnalysis:
    """어떤 파일도 예외를 밖으로 내보내면 안 됩니다 — error에 담아 돌려줍니다."""

    @pytest.mark.parametrize("name", AWKWARD_NAMES)
    def test_awkward_name_reports_an_error_not_a_crash(self, tmp_path, name):
        path = tmp_path / name
        try:
            path.write_text("RAW가 아닌 내용", encoding="utf-8")
        except OSError:
            pytest.skip("파일시스템이 만들 수 없는 이름")

        record = analyze_file(path, AnalyzeConfig())
        assert record.path == path
        assert record.error is not None and not record.ok

    def test_zero_byte_file(self, tmp_path):
        path = tmp_path / "빈.ARW"
        path.write_bytes(b"")
        record = analyze_file(path, AnalyzeConfig())
        assert record.error is not None

    def test_text_file_wearing_a_raw_extension(self, tmp_path):
        path = tmp_path / "가짜.ARW"
        path.write_text("이건 그냥 메모입니다\n" * 100, encoding="utf-8")
        assert analyze_file(path, AnalyzeConfig()).error is not None

    def test_directory_named_like_a_raw(self, tmp_path):
        directory = tmp_path / "폴더인데.ARW"
        directory.mkdir()
        assert analyze_file(directory, AnalyzeConfig()).error is not None

    def test_unwritable_thumbnail_dir_is_reported_not_raised(self, tmp_path):
        """썸네일을 못 쓰는 위치를 줘도 예외가 새어나오면 안 됩니다.

        캐시 폴더 자리에 파일이 있거나 권한이 없으면 실제로 이렇게 됩니다.
        결과는 error에 담겨 돌아오고, 배치는 다음 장으로 넘어가야 합니다.
        """
        path = tmp_path / "a.ARW"
        path.write_bytes(b"x")
        blocked = tmp_path / "막힌캐시"
        blocked.write_text("디렉터리가 아님", encoding="utf-8")

        record = analyze_file(path, AnalyzeConfig(), cache_dir=blocked)

        assert record.path == path
        assert record.error is not None and not record.ok


class TestDegenerateBatches:
    """0장·1장 배치에서 그룹핑과 판정이 끝까지 도는지."""

    def _records(self, tmp_path, count):
        from arw_selector.core.types import FocusResult, FocusSource, ImageRecord

        records = []
        for index in range(count):
            records.append(ImageRecord(
                path=tmp_path / f"{index}.ARW",
                focus=FocusResult(50.0, 50.0, 50.0, FocusSource.TILE,
                                  frame_sharpness=50.0, mean_luma=120.0),
                dhash=index,
            ))
        return records

    @pytest.mark.parametrize("count", [0, 1, 2])
    def test_group_then_grade(self, tmp_path, count):
        records = self._records(tmp_path, count)
        grouping.assign_groups(records, GroupConfig())
        scoring.grade_records(records, ScoreConfig())

        assert all(r.group_id is not None for r in records)
        assert all(isinstance(r.grade, Grade) for r in records)
        assert sum(scoring.summarize(records).values()) == count

    def test_single_photo_is_never_rejected_by_relative_rules(self, tmp_path):
        """한 장짜리 배치에서 그 한 장이 '배치 하위'로 떨어지면 결과가 빕니다."""
        records = self._records(tmp_path, 1)
        grouping.assign_groups(records, GroupConfig())
        scoring.grade_records(records, ScoreConfig())
        assert records[0].final_grade is Grade.KEEP


class TestExtremeImages:
    @pytest.mark.parametrize(
        "image",
        [
            np.zeros((1, 1, 3), np.uint8),
            np.full((1, 1, 3), 255, np.uint8),
            np.zeros((1, 4000, 3), np.uint8),
            np.zeros((4000, 1, 3), np.uint8),
            np.zeros((3, 3, 4), np.uint8),          # 알파 채널이 붙은 프리뷰
            np.zeros((40, 40, 3), np.float32),      # 아직 8비트로 안 내린 배열
        ],
    )
    def test_analyze_focus_stays_in_range(self, image):
        result = focus.analyze_focus(image)
        assert 0.0 <= result.sharpness <= 100.0
        assert np.isfinite(result.sharpness) and np.isfinite(result.frame_sharpness)

    def test_large_image_uses_a_tile(self):
        """큰 이미지에서 프레임 전체로 물러서면 초점 판정이 무의미해집니다."""
        rng = np.random.default_rng(7)
        image = rng.integers(0, 256, (1800, 2700, 3), dtype=np.uint8)
        result = focus.analyze_focus(image)
        assert result.roi is not None

    @pytest.mark.parametrize("value", [0, 128, 255])
    def test_uniform_frames_are_flagged_as_extreme(self, value):
        result = focus.analyze_focus(np.full((300, 400, 3), value, np.uint8))
        assert result.sharpness == pytest.approx(0.0, abs=1.0)


class TestCacheUnderStress:
    def test_schema_bump_drops_old_entries(self, tmp_path, monkeypatch):
        """분석 로직이 바뀌면 예전 점수가 남아 있으면 안 됩니다."""
        db = tmp_path / "analysis.sqlite"
        conn = sqlite3.connect(db)
        conn.executescript(cache_mod._SCHEMA)
        conn.execute(
            "INSERT INTO analysis VALUES ('a', 1.0, 1, 'k', '{}')")
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', '1')")
        conn.commit()
        conn.close()

        with cache_mod.AnalysisCache(db, "k") as cache:
            assert cache._conn.execute(
                "SELECT COUNT(*) FROM analysis").fetchone()[0] == 0

    @pytest.mark.parametrize("stored", ["이상한값", "", "999"])
    def test_unreadable_schema_version(self, tmp_path, stored):
        db = tmp_path / "analysis.sqlite"
        conn = sqlite3.connect(db)
        conn.executescript(cache_mod._SCHEMA)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (stored,))
        conn.commit()
        conn.close()

        with cache_mod.AnalysisCache(db, "k") as cache:
            assert cache.get_many([tmp_path / "a.ARW"]) == {}

    def test_corrupt_database_does_not_stop_the_batch(self, tmp_path):
        """캐시가 깨져도 분석은 그냥 다시 하면 됩니다.

        pipeline.analyze_paths가 이 예외를 삼키고 캐시 없이 진행합니다.
        여기서는 예외 종류가 그 쪽 기대와 맞는지만 붙잡아 둡니다.
        """
        db = tmp_path / "analysis.sqlite"
        db.write_text("이건 sqlite가 아닙니다" * 40, encoding="utf-8")
        with pytest.raises(sqlite3.Error):
            cache_mod.AnalysisCache(db, "k").open()

    def test_two_readers_on_the_same_database(self, tmp_path):
        """같은 폴더를 두 번 열어도 서로를 막지 않아야 합니다."""
        db = tmp_path / "analysis.sqlite"
        with cache_mod.AnalysisCache(db, "k") as first:
            with cache_mod.AnalysisCache(db, "k") as second:
                assert first.get_many([tmp_path / "a.ARW"]) == {}
                assert second.get_many([tmp_path / "a.ARW"]) == {}

    def test_cache_stats_on_a_corrupt_database(self, tmp_path):
        """용량을 못 세더라도 '삭제' 버튼은 살아 있어야 합니다."""
        cache_dir = tmp_path / cache_mod.CACHE_DIR_NAME
        cache_dir.mkdir()
        (cache_dir / cache_mod.CACHE_FILE_NAME).write_text("깨짐", encoding="utf-8")

        stats = cache_mod.cache_stats(tmp_path)
        assert stats.exists and stats.analysis_entries == 0

        cache_mod.clear_cache(tmp_path)
        assert not (cache_dir / cache_mod.CACHE_FILE_NAME).exists()

    def test_clear_keeps_export_logs(self, tmp_path):
        """되돌리기 로그까지 지우면 4000장을 되돌릴 방법이 사라집니다."""
        cache_dir = tmp_path / cache_mod.CACHE_DIR_NAME
        cache_dir.mkdir()
        log = cache_dir / "export_20260101_000000.json"
        log.write_text("{}", encoding="utf-8")

        cache_mod.clear_cache(tmp_path)
        assert log.exists()
