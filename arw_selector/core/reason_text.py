"""Plain English wording for grading reasons. No Qt.

The CLI writes reasons into JSON and CSV, and `PySide6` is an optional
extra (`pip install raw-selector[gui]`) — so `raw-select` has to produce
text without Qt anywhere in the import graph.

`gui/reason_text.py` holds the same wording wrapped in `tr()` so the
desktop app can show it translated. **Two copies of the same English is a
real risk**, so `tests/test_reason_text.py` renders every reason through
both and fails if they disagree. Edit one, and that test tells you about
the other.
"""

from __future__ import annotations

from . import scoring
from .scoring import Reason
from .types import FocusSource

#: Wording for the region the focus score was measured on.
ROI_NAMES = {
    FocusSource.EYE.value: "eye area",
    FocusSource.FACE.value: "face area",
    FocusSource.TILE.value: "estimated subject",
    FocusSource.FRAME.value: "whole frame",
}

#: One template per reason key. `str.format` fields must match the params
#: that `scoring._reasons` attaches — the tests check every key.
TEMPLATES = {
    scoring.REASON_ERROR: "{error}",
    scoring.REASON_ROI_SHARPNESS: "sharpness {sharpness:.0f} on the {roi_name}",
    scoring.REASON_FACE_COUNT: "{count} face(s)",
    scoring.REASON_FACE_DEFOCUS:
        "focus missed the face (background sharper by {deficit:.0f})",
    scoring.REASON_HIGHLIGHT_CLIP: "highlights {percent:.0f}% clipped",
    scoring.REASON_SHADOW_CLIP: "shadows {percent:.0f}% crushed",
    scoring.REASON_EYES_UNKNOWN:
        "eyes not measured (profile, occluded or too small)",
    scoring.REASON_EYES_CLOSED:
        "eyes look closed (EAR {ear:.2f} < {threshold:.2f})",
    scoring.REASON_EYES_OPEN: "eyes open (EAR {ear:.2f})",
    scoring.REASON_FRAME_BLACK: "frame is almost black",
    scoring.REASON_FRAME_WHITE: "frame is almost white",
    scoring.REASON_BATCH_BOTTOM: "bottom of the batch (below {threshold:.0f})",
    scoring.REASON_BETTER_IN_GROUP:
        "a shot {deficit:.0f} points better exists in this scene",
    scoring.REASON_NOT_RAW: "{format} source — less latitude than RAW",
}


def render(reason: Reason) -> str:
    """One reason as a sentence. Unknown keys fall back to the key itself.

    Falling back rather than raising is deliberate: a reason that has no
    wording yet should still be visible, and a crash in the tooltip would
    take the whole panel with it.
    """
    template = TEMPLATES.get(reason.key)
    if template is None:
        return reason.key
    params = dict(reason.params)
    if "source" in params:
        params["roi_name"] = ROI_NAMES.get(str(params["source"]), str(params["source"]))
    try:
        return template.format(**params)
    except (KeyError, ValueError, IndexError):
        return reason.key


def render_all(reasons) -> list[str]:
    return [render(reason) for reason in reasons]
