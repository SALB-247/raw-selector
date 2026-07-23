"""판정 근거 문구가 두 렌더러에서 같은지, 모든 키를 덮는지.

같은 영어 문장이 두 곳에 있습니다.

  - core/reason_text.py — Qt 없음. CLI가 씁니다 (PySide6는 optional extra라
    raw-select는 Qt 없이 돌아야 합니다)
  - gui/reason_text.py — tr()로 감쌈. pyside6-lupdate가 추출하려면 리터럴이
    필요해서, tr(변수)로는 아무것도 안 나옵니다

**중복은 사실이고, 이 파일이 그 대가입니다.** 한쪽만 고치면 여기서
깨집니다. 안 그러면 화면과 CSV의 문구가 조용히 갈라집니다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from arw_selector.core import reason_text as plain  # noqa: E402
from arw_selector.core import scoring  # noqa: E402
from arw_selector.core.config import ScoreConfig  # noqa: E402
from arw_selector.core.scoring import Reason  # noqa: E402
from arw_selector.core.types import (  # noqa: E402
    FocusResult, FocusSource, ImageRecord,
)


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def translated(app):
    from arw_selector.gui import reason_text

    return reason_text


#: 키마다 실제로 붙는 수치. scoring._reasons가 넣는 것과 같은 모양입니다.
SAMPLES = {
    scoring.REASON_ERROR: {"error": "PreviewError"},
    scoring.REASON_ROI_SHARPNESS: {"source": "eye", "sharpness": 44.0},
    scoring.REASON_FACE_COUNT: {"count": 2},
    scoring.REASON_FACE_DEFOCUS: {"deficit": 12.0},
    scoring.REASON_HIGHLIGHT_CLIP: {"percent": 30.0},
    scoring.REASON_SHADOW_CLIP: {"percent": 55.0},
    scoring.REASON_EYES_UNKNOWN: {},
    scoring.REASON_EYES_CLOSED: {"ear": 0.18, "threshold": 0.30},
    scoring.REASON_EYES_OPEN: {"ear": 0.42, "bonus": 10.0},
    scoring.REASON_FRAME_BLACK: {},
    scoring.REASON_FRAME_WHITE: {},
    scoring.REASON_BATCH_BOTTOM: {"threshold": 15.0},
    scoring.REASON_BETTER_IN_GROUP: {"deficit": 13.0},
    scoring.REASON_NOT_RAW: {"format": "JPG"},
}


def _all_keys() -> list[str]:
    return [
        value for name, value in vars(scoring).items()
        if name.startswith("REASON_") and isinstance(value, str)
    ]


# ------------------------------------------------------- 덮개


def test_samples_cover_every_key():
    """키를 추가하고 이 표를 안 늘리면 아래 검사들이 그 키를 건너뜁니다."""
    assert set(_all_keys()) == set(SAMPLES)


def test_plain_renderer_covers_every_key():
    for key in _all_keys():
        assert key in plain.TEMPLATES, f"{key}에 영어 문구가 없습니다"


def test_translated_renderer_covers_every_key(translated):
    for key in _all_keys():
        assert translated._template(key) is not None, f"{key}에 번역 문구가 없습니다"


# ------------------------------------------------------- 어긋남


def test_both_renderers_agree_without_translation(translated):
    """번역을 안 걸면 두 렌더러의 결과가 글자 하나까지 같아야 합니다."""
    for key, params in SAMPLES.items():
        reason = Reason(key, params)
        assert translated.render(reason) == plain.render(reason), (
            f"{key}의 문구가 두 곳에서 다릅니다")


def test_no_placeholder_survives_rendering():
    """수치를 안 채우면 화면에 '{deficit:.0f}'가 그대로 뜹니다."""
    for key, params in SAMPLES.items():
        text = plain.render(Reason(key, params))
        assert "{" not in text and "}" not in text, f"{key}: {text}"


def test_rendering_is_english():
    """공개본의 원본 문자열은 영어입니다.

    ASCII만 허용하면 안 됩니다 — em dash(—)나 화살표(→)는 정당한 영문
    문장부호이고 이 프로젝트가 실제로 씁니다. 보려는 것은 한글이 원본
    문자열에 남아 있지 않은가입니다.
    """
    import re

    hangul = re.compile(r"[가-힣]")
    for key, params in SAMPLES.items():
        text = plain.render(Reason(key, params))
        assert not hangul.search(text), f"{key}: {text}"


# ------------------------------------------------------- 실제 판정에서


def _record(**focus_kwargs) -> ImageRecord:
    fields = dict(
        sharpness=44.0, laplacian=44.0, tenengrad=44.0, frame_sharpness=60.0,
        source=FocusSource.EYE, face_count=1, face_area_ratio=0.05,
        mean_luma=120.0, eyes_open=0.42,
    )
    fields.update(focus_kwargs)
    return ImageRecord(path=Path("x.ARW"), focus=FocusResult(**fields))


@pytest.mark.parametrize("kwargs", [
    {},
    {"eyes_open": 0.10},
    {"eyes_open": -1.0},
    {"face_count": 0, "source": FocusSource.FRAME},
    {"mean_luma": 2.0},
    {"mean_luma": 253.0},
    {"clipped_highlights": 0.9, "clipped_shadows": 0.9},
    {"background_sharpness": 95.0},
])
def test_real_reasons_all_render(kwargs):
    """판정이 실제로 만드는 조합이 전부 문장이 되는지."""
    record = _record(**kwargs)
    record.score = scoring.compute_score(record)
    reasons = scoring._reasons(record, ScoreConfig(), threshold=15.0,
                               group_best=90.0)
    assert reasons
    for reason in reasons:
        text = plain.render(reason)
        assert text and text != reason.key, f"{reason.key}에 문구가 없습니다"


def test_failed_record_shows_the_error():
    record = ImageRecord(path=Path("x.ARW"), error="PreviewError")
    reasons = scoring._reasons(record, ScoreConfig(), threshold=0.0)
    assert plain.render(reasons[0]) == "PreviewError"


def test_unknown_key_does_not_crash():
    """문구가 없는 키가 들어와도 툴팁이 통째로 죽으면 안 됩니다."""
    assert plain.render(Reason("brand_new_key", {})) == "brand_new_key"
