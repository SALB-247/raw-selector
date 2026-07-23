"""채점표가 실제 점수와 맞는지, 눈 상태를 말해 주는지.

58점이 어디서 온 숫자인지 화면에서 알 수 없다는 리포트에서 나왔습니다.
특히 눈을 감았는지 아닌지는 감점될 때만 표시돼서, "떴다 / 감았지만 임계
위다 / 아예 못 쟀다"가 전부 침묵으로 보였습니다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arw_selector.core import scoring
from arw_selector.core.config import ScoreConfig
from arw_selector.core.types import FocusResult, FocusSource, Grade, ImageRecord


def _record(**focus_kwargs) -> ImageRecord:
    fields = dict(
        sharpness=44.0, laplacian=44.0, tenengrad=44.0, frame_sharpness=60.0,
        source=FocusSource.EYE, face_count=1, face_area_ratio=0.05,
        face_confidence=0.99, mean_luma=120.0,
    )
    fields.update(focus_kwargs)
    return ImageRecord(path=Path("x.ARW"), focus=FocusResult(**fields))


# ------------------------------------------------------- 합계가 맞는가


@pytest.mark.parametrize("kwargs", [
    {},
    {"face_count": 0, "source": FocusSource.FRAME},
    {"eyes_open": 0.10},
    {"eyes_open": 0.40},
    {"face_area_ratio": 0.0001},
    {"clipped_highlights": 0.9, "clipped_shadows": 0.9},
    {"mean_luma": 2.0},
    {"mean_luma": 253.0},
    {"source": FocusSource.TILE},
    {"background_sharpness": 95.0},
])
def test_lines_add_up_to_the_score(kwargs):
    """항목의 합이 실제 점수와 달라지면 설명이 아니라 거짓말이 됩니다."""
    record = _record(**kwargs)
    lines, total = scoring.score_breakdown(record)
    assert sum(line.value for line in lines) == pytest.approx(total, abs=1e-6)


def test_keys_are_identifiers_not_display_text():
    """core는 화면 문구를 만들지 않습니다 — 그러면 번역할 수가 없습니다.

    키에 한글이나 공백이 들어오면 누군가 문장을 되돌려 넣은 것입니다.
    """
    for kwargs in ({}, {"eyes_open": 0.10}, {"eyes_open": -1.0},
                   {"face_count": 0, "source": FocusSource.FRAME},
                   {"mean_luma": 2.0}):
        lines, _ = scoring.score_breakdown(_record(**kwargs))
        for line in lines:
            assert line.key.isascii(), f"{line.key!r}에 비ASCII 문자"
            assert " " not in line.key, f"{line.key!r}는 문장처럼 보입니다"


@pytest.mark.parametrize("kwargs", [
    {},
    {"face_count": 0, "source": FocusSource.FRAME},
    {"eyes_open": 0.10},
    {"face_area_ratio": 0.0001},
    {"mean_luma": 2.0},
])
def test_breakdown_matches_compute_score(kwargs):
    """compute_score가 이 함수를 쓰므로 어긋날 수 없어야 합니다."""
    record = _record(**kwargs)
    assert scoring.score_breakdown(record)[1] == pytest.approx(
        scoring.compute_score(record))


def test_clipping_is_shown_as_its_own_line():
    """0~100으로 자른 것을 안 적으면 항목 합과 점수가 안 맞습니다."""
    record = _record(sharpness=0.0, frame_sharpness=0.0, face_count=0,
                     source=FocusSource.FRAME, mean_luma=2.0)
    lines, total = scoring.score_breakdown(record)
    assert total == 0.0
    assert any(line.key == scoring.LINE_CLAMPED for line in lines)
    assert sum(line.value for line in lines) == pytest.approx(0.0)


def test_failed_record_does_not_crash():
    record = ImageRecord(path=Path("x.ARW"), error="PreviewError")
    lines, total = scoring.score_breakdown(record)
    assert total == 0.0
    assert lines


def test_face_weight_is_explained():
    """보너스가 왜 깎였는지 화면이 설명할 수 있도록 수치를 넘겨야 합니다."""
    record = _record(face_area_ratio=0.003)
    lines, _ = scoring.score_breakdown(record, ScoreConfig(face_bonus_full_area=0.03))
    face = next(line for line in lines if line.key == scoring.LINE_FACE_DETECTED)
    assert face.params["area"] == pytest.approx(0.30)
    assert face.params["threshold"] == pytest.approx(3.0)
    assert face.params["weight"] == pytest.approx(0.316, abs=0.01)


def test_trust_is_explained():
    lines, _ = scoring.score_breakdown(_record(), ScoreConfig(trust_eye=0.75))
    base = next(line for line in lines if line.key == scoring.LINE_SHARPNESS)
    assert base.params["trust"] == pytest.approx(0.75)
    assert base.params["source"] == FocusSource.EYE.value


# ------------------------------------------------------- 눈 상태


def _eye_state_line(lines):
    """눈 상태 줄. 눈 '검출' 보너스와 헷갈리지 않게 키로 고릅니다."""
    return next(line for line in lines if line.key in scoring.EYE_STATE_KEYS)


def test_eye_state_is_always_present():
    """어느 경우에도 한 줄은 나와야 합니다 — 침묵이면 알 수가 없습니다."""
    for eyes_open in (-1.0, 0.10, 0.25, 0.40):
        lines, _ = scoring.score_breakdown(_record(eyes_open=eyes_open))
        assert _eye_state_line(lines) is not None


def test_open_eyes_are_rewarded():
    config = ScoreConfig(bonus_eyes_open=9.0)
    line = _eye_state_line(
        scoring.score_breakdown(_record(eyes_open=0.40), config)[0])
    assert line.key == scoring.LINE_EYES_OPEN
    assert line.params["ear"] == pytest.approx(0.40)
    assert line.value == pytest.approx(9.0)


def test_unmeasured_eyes_get_nothing():
    config = ScoreConfig(bonus_eyes_open=9.0, penalty_eyes_closed=20.0)
    line = _eye_state_line(
        scoring.score_breakdown(_record(eyes_open=-1.0), config)[0])
    assert line.value == 0.0


def test_closed_eyes_are_reported_with_the_threshold():
    config = ScoreConfig(eyes_closed_below=0.25, penalty_eyes_closed=20.0)
    line = _eye_state_line(
        scoring.score_breakdown(_record(eyes_open=0.10), config)[0])
    assert line.key == scoring.LINE_EYES_CLOSED
    assert line.value == pytest.approx(-20.0)
    assert line.params["ear"] == pytest.approx(0.10)
    assert line.params["threshold"] == pytest.approx(0.25)


def test_unmeasured_eyes_are_reported_as_unmeasured():
    """못 잰 것과 뜬 것은 다릅니다. 같게 보이면 사용자가 오해합니다."""
    line = _eye_state_line(scoring.score_breakdown(_record(eyes_open=-1.0))[0])
    assert line.key == scoring.LINE_EYES_UNKNOWN
    assert line.value == 0.0


# ------------------------------------------------------- 판정 근거 문구


def _reasons_for(**kwargs) -> list[str]:
    record = _record(**kwargs)
    record.score = scoring.compute_score(record)
    return scoring._reasons(record, ScoreConfig(), threshold=0.0)


def _has(reasons, key) -> bool:
    return any(r.key == key for r in reasons)


def test_reasons_always_mention_the_eyes():
    """감점될 때만 적으면 눈을 감았는지 화면에서 알 수가 없습니다."""
    assert _has(_reasons_for(eyes_open=0.40), scoring.REASON_EYES_OPEN)
    assert _has(_reasons_for(eyes_open=0.10), scoring.REASON_EYES_CLOSED)
    assert _has(_reasons_for(eyes_open=-1.0), scoring.REASON_EYES_UNKNOWN)


def test_reasons_include_the_ear_value():
    """임계를 만질 때 지금 값이 얼마인지 보여야 맞출 수 있습니다.

    화면에 뜨는 문장까지 확인합니다 — 수치를 들고만 있고 문구에 안 넣으면
    사용자에게는 없는 것과 같습니다.
    """
    from arw_selector.core.reason_text import render

    for eyes_open in (0.40, 0.10):
        reasons = _reasons_for(eyes_open=eyes_open)
        rendered = " ".join(render(r) for r in reasons)
        assert f"{eyes_open:.2f}" in rendered
