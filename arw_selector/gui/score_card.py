"""Score card for the selected photo — where every point came from.

The tooltip summarises *why* a shot was rejected but never explained the
number itself. There was no way to see where 58 points came from, whether
the eyes were closed, or whether a small face had cut the bonus down. With
that hidden, turning the knobs told you nothing.

Every figure comes from `scoring.score_breakdown()`. Nothing is
recalculated for display: a second calculation drifts the moment somebody
edits one of them, and a score card that disagrees with the score is worse
than no score card.

`score_breakdown` returns **keys and numbers**, not sentences — the core
package stays free of Qt. Turning those into text is this module's job.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core import scoring
from ..core.config import ScoreConfig
from ..core.scoring import ScoreLine, score_breakdown
from ..core.types import FocusSource, Grade, ImageRecord
from . import theme
from .i18n import tr
from .reason_text import render_all

_GRADE_LABELS = {
    Grade.KEEP: "keep",
    Grade.REVIEW: "review",
    Grade.REJECT: "reject",
}

_PLUS = theme.GRADE_COLORS["keep"]
_MINUS = theme.GRADE_COLORS["reject"]


def _label_for(key: str) -> str:
    """Row name for a breakdown key.

    Built on each call rather than stored in a module-level dict: a dict
    would capture the translation once, at import time, and the language
    would then be frozen for the life of the process.
    """
    return {
        scoring.LINE_FAILED: tr("Analysis failed"),
        scoring.LINE_SHARPNESS: tr("Sharpness"),
        scoring.LINE_FACE_DEFOCUS: tr("Focus missed the face"),
        scoring.LINE_FOCUS_ON_FACE: tr("Focus on the face"),
        scoring.LINE_NO_FACE: tr("No face"),
        scoring.LINE_FACE_DETECTED: tr("Face detected"),
        scoring.LINE_FACE_SIZE: tr("Face size"),
        scoring.LINE_EYE_DETECTED: tr("Eyes detected"),
        scoring.LINE_EYES_CLOSED: tr("Eyes closed"),
        scoring.LINE_EYES_OPEN: tr("Eyes open"),
        scoring.LINE_EYES_UNKNOWN: tr("Eyes not measured"),
        scoring.LINE_HIGHLIGHT_CLIP: tr("Blown highlights"),
        scoring.LINE_SHADOW_CLIP: tr("Crushed shadows"),
        scoring.LINE_EXTREME_LUMA: tr("Lens cap / stray shutter"),
        scoring.LINE_CLAMPED: tr("Clamped to range"),
    }.get(key, key)


def _roi_name(source: str) -> str:
    return {
        FocusSource.EYE.value: tr("eye area"),
        FocusSource.FACE.value: tr("face area"),
        FocusSource.TILE.value: tr("estimated subject"),
        FocusSource.FRAME.value: tr("whole frame"),
    }.get(source, source)


def _detail_for(line: ScoreLine) -> str:
    """Evidence text for a row. Empty when the row speaks for itself.

    Translate first, format second. `tr(f"...")` cannot be translated at
    all — the extractor would see a different string on every run.
    """
    p = line.params
    if not p:
        return ""

    if line.key == scoring.LINE_SHARPNESS:
        return tr("{roi_name} {roi:.0f} × trust {trust:.2f} + frame {frame:.0f} "
                  "× {frame_weight:.2f}, ×{scale:g}").format(
            roi_name=_roi_name(str(p["source"])), **{
                k: v for k, v in p.items() if k != "source"})
    if line.key == scoring.LINE_FACE_DEFOCUS:
        return tr("background {background:.0f} > face {face:.0f}").format(**p)
    if line.key in (scoring.LINE_FACE_DETECTED, scoring.LINE_EYE_DETECTED):
        return tr("face {area:.2f}% of {threshold:.1f}% "
                  "→ ×{weight:.2f}").format(**p)
    if line.key == scoring.LINE_EYES_CLOSED:
        return tr("EAR {ear:.2f} < threshold {threshold:.2f}").format(**p)
    if line.key == scoring.LINE_EYES_OPEN:
        return tr("EAR {ear:.2f} ≥ threshold {threshold:.2f}").format(**p)
    if line.key in (scoring.LINE_HIGHLIGHT_CLIP, scoring.LINE_SHADOW_CLIP):
        return tr("{clipped:.1f}% (allowed {allowed:.1f}%)").format(**p)
    if line.key == scoring.LINE_EXTREME_LUMA:
        return tr("mean brightness {luma:.0f}").format(**p)
    if line.key == scoring.LINE_CLAMPED:
        return tr("{total:.1f} clamped into 0–100").format(**p)
    if line.key == scoring.LINE_FAILED:
        return str(p.get("error", ""))
    return ""


class ScoreCard(QWidget):
    """Point-by-point breakdown for one photo. Hides itself with no selection."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("scorecard")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 6, 10, 8)
        outer.setSpacing(4)

        self.title = QLabel()
        self.title.setStyleSheet(f"color: {theme.TEXT}; font-weight: 600;")
        outer.addWidget(self.title)

        # Rebuilt on every record: the number of rows changes, and reusing
        # widgets means eventually forgetting to clear a leftover row, which
        # shows the previous photo's numbers under this photo's name.
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(14)
        self._grid.setVerticalSpacing(2)
        outer.addWidget(self._grid_host)

        self.reasons = QLabel()
        self.reasons.setStyleSheet(theme.hint_label())
        self.reasons.setWordWrap(True)
        outer.addWidget(self.reasons)

        self.setVisible(False)

    # ------------------------------------------------------------ display

    def show_record(
        self, record: ImageRecord | None, config: ScoreConfig | None = None
    ) -> None:
        """Draw the breakdown for one photo. None hides the card."""
        if record is None:
            self.setVisible(False)
            return

        self._clear()

        grade = _GRADE_LABELS.get(record.grade, "")
        colour = theme.GRADE_COLORS.get(grade, theme.TEXT)
        self.title.setText(
            f"{record.path.name} · <span style='color:{colour}'>{grade}</span> · "
            + tr("score {score:.1f}").format(score=record.score)
        )
        self.title.setTextFormat(Qt.RichText)

        lines, total = score_breakdown(record, config)
        for row, line in enumerate(lines):
            self._add_row(row, _label_for(line.key), line.value, _detail_for(line))

        # The total gets its own row. Without it you have to add the column
        # up yourself to know whether it matches the score in the heading.
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet(f"color: {theme.BORDER};")
        self._grid.addWidget(divider, len(lines), 0, 1, 3)
        self._add_row(len(lines) + 1, tr("Total"), total, "", bold=True)

        self.reasons.setText(
            " · ".join(render_all(record.reasons)) if record.reasons else "")
        self.reasons.setVisible(bool(record.reasons))
        self.setVisible(True)

    # ------------------------------------------------------------ internals

    def _clear(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _add_row(
        self, row: int, label: str, value: float, detail: str, *, bold: bool = False
    ) -> None:
        weight = "600" if bold else "400"

        name = QLabel(label)
        name.setStyleSheet(f"color: {theme.TEXT}; font-weight: {weight};")
        self._grid.addWidget(name, row, 0)

        # Rows worth zero show a dash instead of the number. A column of
        # "0.0" hides which entries actually moved the score.
        if value == 0.0 and not bold:
            text, colour = "—", theme.TEXT_FAINT
        else:
            text = f"{value:+.1f}"
            colour = theme.TEXT if bold else (_PLUS if value > 0 else _MINUS)
        amount = QLabel(text)
        amount.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        amount.setStyleSheet(f"color: {colour}; font-weight: {weight};")
        amount.setMinimumWidth(52)
        self._grid.addWidget(amount, row, 1)

        note = QLabel(detail)
        note.setStyleSheet(theme.hint_label())
        self._grid.addWidget(note, row, 2)
        self._grid.setColumnStretch(2, 1)
