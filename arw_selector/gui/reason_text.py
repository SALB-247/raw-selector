"""Translated wording for grading reasons.

Mirrors `core/reason_text.py` string for string. The core copy exists
because the CLI must work without Qt; this copy exists because `tr()`
needs literal arguments — `pyside6-lupdate` extracts what it can see, and
`tr(SOME_VARIABLE)` gives it nothing to extract.

`tests/test_reason_text.py` renders every reason through both modules and
fails when the untranslated output differs, so the duplication cannot
drift quietly.
"""

from __future__ import annotations

from ..core import scoring
from ..core.reason_text import render as render_plain
from ..core.scoring import Reason
from ..core.types import FocusSource
from .i18n import tr


def _roi_name(source: str) -> str:
    return {
        FocusSource.EYE.value: tr("eye area"),
        FocusSource.FACE.value: tr("face area"),
        FocusSource.TILE.value: tr("estimated subject"),
        FocusSource.FRAME.value: tr("whole frame"),
    }.get(source, source)


def _template(key: str) -> str | None:
    """Translated template for a reason key.

    Rebuilt per call so switching language takes effect without a restart.
    """
    return {
        scoring.REASON_ERROR: "{error}",
        scoring.REASON_ROI_SHARPNESS: tr("sharpness {sharpness:.0f} on the {roi_name}"),
        scoring.REASON_FACE_COUNT: tr("{count} face(s)"),
        scoring.REASON_FACE_DEFOCUS: tr(
            "focus missed the face (background sharper by {deficit:.0f})"),
        scoring.REASON_HIGHLIGHT_CLIP: tr("highlights {percent:.0f}% clipped"),
        scoring.REASON_SHADOW_CLIP: tr("shadows {percent:.0f}% crushed"),
        scoring.REASON_EYES_UNKNOWN: tr(
            "eyes not measured (profile, occluded or too small)"),
        scoring.REASON_EYES_CLOSED: tr(
            "eyes look closed (EAR {ear:.2f} < {threshold:.2f})"),
        scoring.REASON_EYES_OPEN: tr("eyes open (EAR {ear:.2f})"),
        scoring.REASON_FRAME_BLACK: tr("frame is almost black"),
        scoring.REASON_FRAME_WHITE: tr("frame is almost white"),
        scoring.REASON_BATCH_BOTTOM: tr("bottom of the batch (below {threshold:.0f})"),
        scoring.REASON_BETTER_IN_GROUP: tr(
            "a shot {deficit:.0f} points better exists in this scene"),
        scoring.REASON_NOT_RAW: tr("{format} source — less latitude than RAW"),
    }.get(key)


def render(reason: Reason) -> str:
    template = _template(reason.key)
    if template is None:
        return render_plain(reason)  # unknown key — at least show something
    params = dict(reason.params)
    if "source" in params:
        params["roi_name"] = _roi_name(str(params["source"]))
    try:
        return template.format(**params)
    except (KeyError, ValueError, IndexError):
        # A translation with a broken placeholder must not take the panel
        # down. Fall back to English rather than showing nothing.
        return render_plain(reason)


def render_all(reasons) -> list[str]:
    return [render(reason) for reason in reasons]
