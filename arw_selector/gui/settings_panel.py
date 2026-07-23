"""Grading criteria panel.

Everything here takes effect immediately, without re-analysing. Re-running
analysis over 4000 photos takes minutes; re-grading them takes 0.3s — so
the values have to be adjustable while watching the result.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import yaml

from ..core.config import (
    FACE_BONUS_AREA_RANGE,
    Config,
    GroupConfig,
    ScoreConfig,
)
from ..core.presets import select_presets
from ..core.scoring import SHARPNESS_SCALE, SHARPNESS_SCALE_NO_FACE
from .i18n import tr
from .preset_bar import PresetBar

#: Below this value (%) the spin box moves in finer steps.
_FACE_AREA_FINE_BELOW = 0.5
_FACE_AREA_FINE_STEP = 0.1
_FACE_AREA_COARSE_STEP = 0.5


class _FaceAreaSpinBox(QDoubleSpinBox):
    """Spin box for the face-size threshold. Finer steps at small values.

    The useful range sits at one end. On telephoto work the main subject's
    face lands between 0.1% and 0.6%, and a fixed 0.5% step jumps straight
    over that. Close-up work needs to reach 20%, which is 200 presses at a
    0.1% step.
    """

    def stepBy(self, steps: int) -> None:  # noqa: N802 (Qt's name)
        # Stepping down from the boundary has to use the fine step, or 0.5
        # drops straight to 0.0 and lands on the floor.
        value = self.value()
        fine = value < _FACE_AREA_FINE_BELOW or (
            steps < 0 and value <= _FACE_AREA_FINE_BELOW
        )
        self.setSingleStep(
            _FACE_AREA_FINE_STEP if fine else _FACE_AREA_COARSE_STEP
        )
        super().stepBy(steps)


class SettingsPanel(QWidget):
    """Panel for the grading parameters. Emits `changed` on every edit."""

    changed = Signal()

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self._loading = False
        # The width is measured from the finished contents further down. The
        # panel lives inside a scroll area because on a short screen (FHD)
        # it grows taller than the window, and without scrolling Qt squeezes
        # the form rows until the text overlaps.

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        # Horizontal stays AsNeeded too. With AlwaysOff, wide content is
        # simply cut instead of scrolled, and values and buttons vanish off
        # the right edge.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        self._scroll, self._content = scroll, content

        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        self.preset_bar = PresetBar(
            select_presets(),
            collect=self._collect_preset,
            apply=self._apply_preset,
        )
        self.preset_bar.applied.connect(self.changed.emit)
        layout.addWidget(self.preset_bar)

        layout.addLayout(self._build_file_row())
        layout.addWidget(self._build_keep_group())
        layout.addWidget(self._build_reject_group())
        layout.addWidget(self._build_weight_group())
        layout.addWidget(self._build_scene_group())

        self.floor_label = QLabel()
        self.floor_label.setWordWrap(True)
        self.floor_label.setStyleSheet("color: #9a9aa2; font-size: 11px;")
        layout.addWidget(self.floor_label)

        reset = QPushButton(tr("Restore defaults"))
        reset.clicked.connect(self.reset_to_defaults)
        layout.addWidget(reset)

        layout.addStretch(1)
        self.load_from_config()

        # This panel is mostly spin boxes as well, and a stray wheel scroll
        # over one changes a criterion — the grading then shifts with no
        # visible cause.
        from .widgets import disable_wheel_in

        disable_wheel_in(self)

        # Width comes from what the contents actually ask for. Font size and
        # DPI change that, so a hard-coded number gets truncated somewhere.
        scrollbar = self.style().pixelMetric(QStyle.PM_ScrollBarExtent)
        self.setFixedWidth(content.minimumSizeHint().width() + scrollbar + 8)

    # ------------------------------------------------------------ layout

    def _build_keep_group(self) -> QGroupBox:
        box = QGroupBox(tr("Keep criteria"))
        form = QFormLayout(box)

        self.use_ratio = QCheckBox(tr("Aim for a target ratio"))
        self.use_ratio.setToolTip(tr(
            "An absolute score means something different in every batch.\n"
            "Given a ratio, the threshold is derived from that batch's own\n"
            "score distribution, so the result holds across shoots."
        ))
        self.use_ratio.toggled.connect(self._on_ratio_toggled)
        form.addRow(self.use_ratio)

        self.target_ratio = QDoubleSpinBox()
        self.target_ratio.setRange(1.0, 100.0)
        self.target_ratio.setSuffix(" %")
        self.target_ratio.setSingleStep(1.0)
        self.target_ratio.setDecimals(1)
        self.target_ratio.valueChanged.connect(self._refresh_score_stats)
        self.target_ratio.valueChanged.connect(self._emit)
        form.addRow(tr("Target keep ratio"), self.target_ratio)

        # In ratio mode the threshold is derived from the batch's score
        # distribution. Without showing that distribution there is no way to
        # tell what score the cut currently falls at.
        self.score_stats = QLabel()
        self.score_stats.setStyleSheet("color: #9a9aa2; font-size: 11px;")
        self.score_stats.setWordWrap(True)
        form.addRow(self.score_stats)

        self.keep_above = QDoubleSpinBox()
        self.keep_above.setRange(0.0, 100.0)
        self.keep_above.setSuffix(tr(" pts"))
        self.keep_above.setToolTip(
            tr("At or above this score, keep regardless of rank"))
        self.keep_above.valueChanged.connect(self._emit)
        form.addRow(tr("Absolute keep score"), self.keep_above)

        self.keep_per_group = QSpinBox()
        self.keep_per_group.setRange(0, 20)
        self.keep_per_group.setSuffix(tr(" photos"))
        self.keep_per_group.setSpecialValueText(tr("no guarantee"))
        self.keep_per_group.setToolTip(tr(
            "How many top shots to keep per scene.\n"
            "0 turns the scene guarantee off — grading is then purely by\n"
            "score, so some scenes may end up with no keep at all."
        ))
        self.keep_per_group.valueChanged.connect(self._emit)
        form.addRow(tr("Keeps per scene"), self.keep_per_group)

        self.min_keep_score = QDoubleSpinBox()
        self.min_keep_score.setRange(0.0, 100.0)
        self.min_keep_score.setSuffix(tr(" pts"))
        self.min_keep_score.setToolTip(tr(
            "Minimum score a shot needs before it can be promoted to keep.\n"
            "At 0 every scene yields at least one photo.\n"
            "Above 0, a scene where nothing reaches this score yields\n"
            "nothing at all."
        ))
        self.min_keep_score.valueChanged.connect(self._emit)
        form.addRow(tr("Keep quality floor"), self.min_keep_score)

        self.dropped_label = QLabel()
        self.dropped_label.setWordWrap(True)
        self.dropped_label.setStyleSheet("color: #c9a06a; font-size: 11px;")
        form.addRow(self.dropped_label)

        return box

    def _build_file_row(self) -> QHBoxLayout:
        """Save and load to an arbitrary path, separate from the preset store.

        Presets live in the app's settings folder, but handing one to a
        colleague or keeping it beside the shoot needs a real file path.
        """
        row = QHBoxLayout()
        row.setSpacing(4)

        export_button = QPushButton(tr("Save to file"))
        export_button.setToolTip(
            tr("Write the current grading criteria to a YAML file"))
        export_button.clicked.connect(self.export_to_file)
        row.addWidget(export_button)

        import_button = QPushButton(tr("Load from file"))
        import_button.clicked.connect(self.import_from_file)
        row.addWidget(import_button)

        return row

    def _build_weight_group(self) -> QGroupBox:
        """How the score is put together — the individual weights.

        What matters depends on what is being shot: faces for portraits,
        overall sharpness for landscape. So all of it is adjustable.
        """
        box = QGroupBox(tr("Score weights"))
        form = QFormLayout(box)

        # The multiplier is read from `scoring`. Writing the number here
        # instead leaves the display showing an old formula after the scale
        # changes — which is exactly what had happened.
        self.formula = QLabel()
        self.formula.setStyleSheet("color: #7a7a82; font-size: 11px;")
        self.formula.setToolTip(
            tr("In face-priority mode sharpness is multiplied by {scale:g}.\n"
               "Letting sharpness alone use the full 0–100 means any bonus at\n"
               "all pins the score to 100, every good shot ends up with the\n"
               "same number, and the ranking disappears.\n\n"
               "So sharpness uses 0–{half:g} and the face and eye signals use\n"
               "the rest.\n\n"
               "Turning the mode off removes those signals, so the multiplier\n"
               "becomes {full:g} — otherwise the top half of the range sits\n"
               "empty and nothing reaches the keep threshold (measured: 45.1\n"
               "max across 2845 A6700 frames).\n\n"
               "The absolute thresholds below (keep score, reject floor)\n"
               "assume this scale.").format(
                   scale=SHARPNESS_SCALE, half=100 * SHARPNESS_SCALE,
                   full=SHARPNESS_SCALE_NO_FACE)
        )
        form.addRow(self.formula)

        # Face-priority mode — the default for people. Trusts the face and
        # eye regions more, and penalises shots where focus fell behind.
        self.face_priority = QCheckBox(tr("Face-priority mode"))
        self.face_priority.setToolTip(tr(
            "Trusts the face and eye regions on shots where a face was\n"
            "found, and penalises shots where the face is soft but the\n"
            "background is sharper (focus fell behind the subject).\n"
            "Turn it off for landscape work to grade on whole-frame\n"
            "sharpness alone."
        ))
        self.face_priority.toggled.connect(self._on_face_priority_toggled)
        form.addRow(self.face_priority)

        self.penalty_face_defocus = QDoubleSpinBox()
        self.penalty_face_defocus.setRange(0.0, 60.0)
        self.penalty_face_defocus.setSuffix(tr(" pts"))
        self.penalty_face_defocus.setToolTip(tr(
            "Largest penalty when the background is sharper than the face.\n"
            "Scales with the gap and with the face detector's confidence."
        ))
        self.penalty_face_defocus.valueChanged.connect(self._emit)
        form.addRow(tr("  Focus missed the face"), self.penalty_face_defocus)

        # The other two that only apply in face-priority mode live here too.
        # Without them on screen there is no way to answer "I turned face
        # priority on, so why this score".
        self.bonus_focus_on_face = QDoubleSpinBox()
        self.bonus_focus_on_face.setRange(0.0, 40.0)
        self.bonus_focus_on_face.setSuffix(tr(" pts"))
        self.bonus_focus_on_face.setToolTip(tr(
            "Added when the focus ROI really is a face or a pair of eyes.\n"
            "'A face is in the frame' and 'the focus landed on that face'\n"
            "are different things — this bonus is only for the second."
        ))
        self.bonus_focus_on_face.valueChanged.connect(self._emit)
        form.addRow(tr("  Focus on the face"), self.bonus_focus_on_face)

        self.penalty_no_face = QDoubleSpinBox()
        self.penalty_no_face.setRange(0.0, 40.0)
        self.penalty_no_face.setSuffix(tr(" pts"))
        self.penalty_no_face.setToolTip(tr(
            "Penalty for a shot with no face at all, in face-priority mode.\n\n"
            "Measured across 2845 A6700 frames: faceless shots had a median\n"
            "score of 59.0 against 47.6 for shots focused on a face. Face\n"
            "shots are measured on the softer face ROI and can pick up the\n"
            "background-focus penalty, while faceless shots use frame\n"
            "sharpness with nothing deducted. This levels the two groups so\n"
            "they can be compared."
        ))
        self.penalty_no_face.valueChanged.connect(self._emit)
        form.addRow(tr("  No face"), self.penalty_no_face)

        eye_note = QLabel(tr("Eye state — sharpness cannot catch this"))
        eye_note.setStyleSheet("color: #9a9aa2; margin-top: 4px;")
        eye_note.setToolTip(tr(
            "Open eyes add, closed eyes subtract. The real gap between an\n"
            "open-eyed and a closed-eyed shot is the sum of the two.\n\n"
            "Shots where the eyes could not be measured (profile, occluded)\n"
            "get neither — an unknown is treated as neither good nor bad."
        ))
        form.addRow(eye_note)

        self.bonus_eyes_open = QDoubleSpinBox()
        self.bonus_eyes_open.setRange(0.0, 40.0)
        self.bonus_eyes_open.setSuffix(tr(" pts"))
        self.bonus_eyes_open.setToolTip(tr(
            "Added when the main subject's eyes look open.\n\n"
            "With only a penalty, 'eyes open' and 'eyes not measured' score\n"
            "identically. A profile shot nobody could measure would then be\n"
            "treated like a subject looking straight at the camera, and the\n"
            "single most useful signal in portrait selection is half wasted."
        ))
        self.bonus_eyes_open.valueChanged.connect(self._emit)
        form.addRow(tr("  Eyes open"), self.bonus_eyes_open)

        self.penalty_eyes_closed = QDoubleSpinBox()
        self.penalty_eyes_closed.setRange(0.0, 40.0)
        self.penalty_eyes_closed.setSuffix(tr(" pts"))
        self.penalty_eyes_closed.setToolTip(tr(
            "Penalty when the main subject's eyes look closed.\n"
            "Closed eyes are still in focus, so sharpness never catches them.\n\n"
            "Size it to push the shot out of automatic keep, rather than all\n"
            "the way down into reject."
        ))
        self.penalty_eyes_closed.valueChanged.connect(self._emit)
        form.addRow(tr("  Eyes closed"), self.penalty_eyes_closed)

        self.eyes_closed_below = QDoubleSpinBox()
        self.eyes_closed_below.setRange(0.05, 0.45)
        self.eyes_closed_below.setSingleStep(0.01)
        self.eyes_closed_below.setDecimals(2)
        self.eyes_closed_below.setToolTip(tr(
            "Eyes count as closed below this eye aspect ratio (EAR).\n\n"
            "Measured on 107 hand-labelled photos (28 closed / 79 open) —\n"
            "caught / falsely penalised:\n"
            "  0.22 —  14/28  ·   2/79   (85% correct)\n"
            "  0.25 —  17/28  ·   7/79   (83% correct)\n"
            "  0.28 —  24/28  ·  16/79   (81% correct)\n"
            "  0.30 —  25/28  ·  19/79   (79% correct)  (default)\n"
            "  0.32 —  25/28  ·  26/79   (73% correct)\n"
            "  0.35 —  26/28  ·  40/79   (61% correct)\n\n"
            "There is no reason to go above 0.30 — 0.32 catches the same\n"
            "number while penalising seven more good shots. Closed eyes sit\n"
            "mostly below 0.28, open eyes start at 0.20, and above that the\n"
            "two distributions only overlap.\n\n"
            "Shots where the eyes could not be measured are never penalised,\n"
            "at any value."
        ))
        self.eyes_closed_below.valueChanged.connect(self._emit)
        form.addRow(tr("  Eyes-closed threshold (EAR)"), self.eyes_closed_below)

        trust_note = QLabel(tr("ROI trust — how much to believe the region"))
        trust_note.setToolTip(tr(
            "This is the 'trust' term in the formula above.\n"
            "Near 1 grades on the ROI's sharpness alone; near 0 grades on\n"
            "the whole frame.\n\n"
            "Face-priority mode does not touch these — it works purely\n"
            "through bonuses and penalties."
        ))
        trust_note.setStyleSheet("color: #9a9aa2; margin-top: 4px;")
        form.addRow(trust_note)

        self.trust_spins: dict[str, QDoubleSpinBox] = {}
        for key, label, tip in (
            ("trust_eye", tr("Eye"),
             tr("With the eyes found, the sharpness inside them is the answer")),
            ("trust_face", tr("Face"),
             tr("A face was found but the eye ROI was too small")),
            ("trust_tile", tr("Estimated subject"),
             tr("No face, so the subject was guessed from tiles — trust less")),
            ("trust_frame", tr("Whole frame"), tr("No ROI could be found")),
        ):
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 1.0)
            spin.setSingleStep(0.05)
            spin.setDecimals(2)
            spin.setToolTip(tip)
            spin.valueChanged.connect(self._emit)
            self.trust_spins[key] = spin
            form.addRow(f"  {label}", spin)

        bonus_note = QLabel(tr("Bonuses"))
        bonus_note.setStyleSheet("color: #9a9aa2; margin-top: 4px;")
        form.addRow(bonus_note)

        self.bonus_spins: dict[str, QDoubleSpinBox] = {}
        for key, label, tip in (
            ("bonus_face", tr("Face detected"),
             tr("Favour shots with a face. Raise it for portrait work.\n\n"
                "Small faces do not receive all of it — the detector finds\n"
                "audience faces a few dozen pixels across. 'Face size for\n"
                "full bonus' below sets where the full amount starts.")),
            ("bonus_eye", tr("Eyes detected"),
             tr("Added on top when the eyes were found as well.\n"
                "Scaled by face size the same way as the face bonus.")),
            ("bonus_face_size", tr("Face size"),
             tr("Favour larger faces, i.e. the actual subject.\n"
                "Separate from the size scaling on the two bonuses above; "
                "this pushes big faces further up.")),
        ):
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 30.0)
            spin.setSuffix(tr(" pts"))
            spin.setToolTip(tip)
            spin.valueChanged.connect(self._emit)
            self.bonus_spins[key] = spin
            form.addRow(f"  {label}", spin)

        # Stored as a ratio (0–1); shown as a percentage, which is what a
        # person can actually read.
        low, high = FACE_BONUS_AREA_RANGE
        self.face_bonus_full_area = _FaceAreaSpinBox()
        self.face_bonus_full_area.setRange(low * 100.0, high * 100.0)
        self.face_bonus_full_area.setSuffix(" %")
        self.face_bonus_full_area.setDecimals(1)
        self.face_bonus_full_area.setToolTip(tr(
            "Face size at which the face bonus is paid in full, as a share\n"
            "of the frame area. Smaller faces receive proportionally less,\n"
            "and very small ones receive nothing.\n\n"
            "Roughly, on 26MP (6240×4168):\n"
            "  0.1% — 160×160 px (someone standing far off)\n"
            "  3%   — 880×880 px, head-and-shoulders portrait  (default)\n"
            "  20%  — a close-up filling much of the frame\n\n"
            "Raise it to only credit large faces, which helps on stage work\n"
            "where the audience keeps getting detected. Lower it to credit\n"
            "distant subjects too.\n\n"
            "Across 2845 frames of 300mm stage work the main subject's face\n"
            "had a median of 0.34% and a maximum of 2.99%. For that kind of\n"
            "shoot, drop this to around 0.3%."
        ))
        self.face_bonus_full_area.valueChanged.connect(self._emit)
        form.addRow(tr("  Face size for full bonus"), self.face_bonus_full_area)

        penalty_note = QLabel(tr("Penalties"))
        penalty_note.setStyleSheet("color: #9a9aa2; margin-top: 4px;")
        form.addRow(penalty_note)

        self.penalty_spins: dict[str, QDoubleSpinBox] = {}
        for key, label, tip in (
            ("penalty_highlight_clip", tr("Blown highlights"),
             tr("Largest penalty once clipping passes the tolerance")),
            ("penalty_shadow_clip", tr("Crushed shadows"),
             tr("0 by default — deliberately low-key work is common enough "
                "that penalising it does more harm than good")),
            ("penalty_extreme_luma", tr("Lens cap / stray shutter"),
             tr("The frame is almost entirely black or white")),
        ):
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 60.0)
            spin.setSuffix(tr(" pts"))
            spin.setToolTip(tip)
            spin.valueChanged.connect(self._emit)
            self.penalty_spins[key] = spin
            form.addRow(f"  {label}", spin)

        self.max_highlight = QDoubleSpinBox()
        self.max_highlight.setRange(0.0, 1.0)
        self.max_highlight.setSingleStep(0.05)
        self.max_highlight.setDecimals(2)
        self.max_highlight.setToolTip(
            tr("Penalties start once more than this fraction is blown"))
        self.max_highlight.valueChanged.connect(self._emit)
        form.addRow(tr("  Highlight tolerance"), self.max_highlight)

        self.max_shadow = QDoubleSpinBox()
        self.max_shadow.setRange(0.0, 1.0)
        self.max_shadow.setSingleStep(0.05)
        self.max_shadow.setDecimals(2)
        self.max_shadow.valueChanged.connect(self._emit)
        form.addRow(tr("  Shadow tolerance"), self.max_shadow)

        return box

    def _build_reject_group(self) -> QGroupBox:
        box = QGroupBox(tr("Reject criteria"))
        form = QFormLayout(box)

        self.reject_group_delta = QDoubleSpinBox()
        self.reject_group_delta.setRange(0.0, 100.0)
        self.reject_group_delta.setSuffix(tr(" pts"))
        self.reject_group_delta.setToolTip(tr(
            "Falling this far below the best shot in the same scene counts\n"
            "as a duplicate and is dropped. Raising it leaves more in\n"
            "review; lowering it rejects more."
        ))
        self.reject_group_delta.valueChanged.connect(self._emit)
        form.addRow(tr("Gap to the scene's best"), self.reject_group_delta)

        self.reject_below = QDoubleSpinBox()
        self.reject_below.setRange(0.0, 100.0)
        self.reject_below.setSuffix(tr(" pts"))
        self.reject_below.setToolTip(
            tr("Below this score, always reject (absolute floor)"))
        self.reject_below.valueChanged.connect(self._emit)
        form.addRow(tr("Absolute floor"), self.reject_below)

        self.reject_percentile = QDoubleSpinBox()
        self.reject_percentile.setRange(0.0, 50.0)
        self.reject_percentile.setSuffix(" %")
        self.reject_percentile.setToolTip(
            tr("What share of the batch's bottom end to treat as reject "
               "candidates"))
        self.reject_percentile.valueChanged.connect(self._emit)
        form.addRow(tr("Batch bottom percentile"), self.reject_percentile)

        note = QLabel(tr("The best shot in a scene is never rejected, "
                         "whatever these say"))
        note.setWordWrap(True)
        note.setStyleSheet("color: #7a9a7a; font-size: 11px;")
        form.addRow(note)

        return box

    def _build_scene_group(self) -> QGroupBox:
        box = QGroupBox(tr("Scene splitting"))
        form = QFormLayout(box)

        self.time_gap = QDoubleSpinBox()
        self.time_gap.setRange(0.1, 300.0)
        self.time_gap.setSuffix(tr(" s"))
        self.time_gap.setToolTip(
            tr("A longer gap than this starts a new scene (primary signal)"))
        self.time_gap.valueChanged.connect(self._emit)
        form.addRow(tr("Scene gap"), self.time_gap)

        self.scene_distance = QSpinBox()
        self.scene_distance.setRange(1, 64)
        self.scene_distance.setToolTip(tr(
            "How much the picture must change to split a scene (of 64 bits).\n"
            "Higher (40) for telephoto and moving subjects; lower (16–24)\n"
            "for still life and portraits."
        ))
        self.scene_distance.valueChanged.connect(self._emit)
        form.addRow(tr("Scene change distance"), self.scene_distance)

        self.no_time_distance = QSpinBox()
        self.no_time_distance.setRange(1, 64)
        self.no_time_distance.setToolTip(tr(
            "Picture-change threshold used only when EXIF has no capture\n"
            "time. With no clock to go on the picture is the only evidence,\n"
            "so it has to be stricter."
        ))
        self.no_time_distance.valueChanged.connect(self._emit)
        form.addRow(tr("Distance without a time"), self.no_time_distance)

        self.max_group = QSpinBox()
        self.max_group.setRange(2, 500)
        self.max_group.setSuffix(tr(" photos"))
        self.max_group.setToolTip(
            tr("Force a split once a scene grows past this"))
        self.max_group.valueChanged.connect(self._emit)
        form.addRow(tr("Largest scene"), self.max_group)

        return box

    # ------------------------------------------------------------ sync

    def load_from_config(self) -> None:
        """config -> widgets. No `changed` is emitted while this runs."""
        self._loading = True
        score = self.config.score
        group = self.config.group

        has_ratio = score.target_keep_ratio is not None
        self.use_ratio.setChecked(has_ratio)
        self.target_ratio.setValue((score.target_keep_ratio or 0.10) * 100.0)
        self.keep_above.setValue(score.keep_above)
        self.keep_per_group.setValue(score.keep_per_group)
        self.min_keep_score.setValue(score.min_keep_score)

        self.reject_group_delta.setValue(score.reject_below_group_best)
        self.reject_below.setValue(score.reject_below)
        self.reject_percentile.setValue(score.reject_percentile)

        self.face_priority.setChecked(score.face_priority)
        self.penalty_face_defocus.setValue(score.penalty_face_defocus)
        self.bonus_focus_on_face.setValue(score.bonus_focus_on_face)
        self.penalty_no_face.setValue(score.penalty_no_face)
        self.bonus_eyes_open.setValue(score.bonus_eyes_open)
        self.penalty_eyes_closed.setValue(score.penalty_eyes_closed)
        self.eyes_closed_below.setValue(score.eyes_closed_below)

        for key, spin in self.trust_spins.items():
            spin.setValue(getattr(score, key))
        for key, spin in self.bonus_spins.items():
            spin.setValue(getattr(score, key))
        for key, spin in self.penalty_spins.items():
            spin.setValue(getattr(score, key))
        self.face_bonus_full_area.setValue(score.face_bonus_full_area * 100.0)
        self.max_highlight.setValue(score.max_clipped_highlights)
        self.max_shadow.setValue(score.max_clipped_shadows)

        self.time_gap.setValue(group.time_gap_seconds)
        self.scene_distance.setValue(group.scene_change_distance)
        self.no_time_distance.setValue(group.no_time_hash_distance)
        self.max_group.setValue(group.max_group_size)

        self._apply_ratio_enabled(has_ratio)
        # Enabled state is decided in one place. Listing setEnabled calls
        # here as well means adding a widget, fixing the toggle path, and
        # forgetting this one.
        self._apply_face_priority_enabled(score.face_priority)
        self._loading = False

    def apply_to_config(self) -> None:
        """widgets -> config."""
        score = self.config.score
        group = self.config.group

        score.target_keep_ratio = (
            self.target_ratio.value() / 100.0 if self.use_ratio.isChecked() else None
        )
        score.keep_above = self.keep_above.value()
        score.keep_per_group = self.keep_per_group.value()
        score.min_keep_score = self.min_keep_score.value()

        score.reject_below_group_best = self.reject_group_delta.value()
        score.reject_below = self.reject_below.value()
        score.reject_percentile = self.reject_percentile.value()

        score.face_priority = self.face_priority.isChecked()
        score.penalty_face_defocus = self.penalty_face_defocus.value()
        score.bonus_focus_on_face = self.bonus_focus_on_face.value()
        score.penalty_no_face = self.penalty_no_face.value()
        score.bonus_eyes_open = self.bonus_eyes_open.value()
        score.penalty_eyes_closed = self.penalty_eyes_closed.value()
        score.eyes_closed_below = self.eyes_closed_below.value()

        for key, spin in self.trust_spins.items():
            setattr(score, key, spin.value())
        for key, spin in self.bonus_spins.items():
            setattr(score, key, spin.value())
        for key, spin in self.penalty_spins.items():
            setattr(score, key, spin.value())
        score.face_bonus_full_area = self.face_bonus_full_area.value() / 100.0
        score.max_clipped_highlights = self.max_highlight.value()
        score.max_clipped_shadows = self.max_shadow.value()

        group.time_gap_seconds = self.time_gap.value()
        group.scene_change_distance = self.scene_distance.value()
        group.no_time_hash_distance = self.no_time_distance.value()
        group.max_group_size = self.max_group.value()

    def set_score_stats(self, scores) -> None:
        """Show this batch's score distribution under the ratio setting.

        In ratio mode the threshold is derived from the batch. Without the
        distribution there is no answering "I set 10%, so why did this shot
        drop out".
        """
        self._scores = [float(s) for s in (scores or [])]
        self._refresh_score_stats()

    def _refresh_score_stats(self) -> None:
        scores = getattr(self, "_scores", None)
        if not self.use_ratio.isChecked():
            self.score_stats.setText("")
            return
        if not scores:
            self.score_stats.setText(
                tr("Not analysed yet — the distribution appears after analysis"))
            return

        ordered = sorted(scores)
        ratio = max(0.0, min(1.0, self.target_ratio.value() / 100.0))
        # This must count the way `grade_records` counts: it takes
        # round(count × ratio) as the target and uses that shot's score as
        # the threshold. Working it out as a percentile instead gave 30 on
        # screen where the real cut was 40 (4 photos at 25%). A readout that
        # disagrees with the result is worse than none.
        keep_count = max(1, min(len(ordered), round(len(ordered) * ratio)))
        cutoff = ordered[len(ordered) - keep_count]
        mean = sum(ordered) / len(ordered)
        self.score_stats.setText(
            tr("{count} photos · min {low:.1f} / mean {mean:.1f} / max {high:.1f}\n"
               "target {ratio:.0f}% → cuts at about {cutoff:.1f}").format(
                   count=len(ordered), low=ordered[0], mean=mean,
                   high=ordered[-1], ratio=ratio * 100, cutoff=cutoff)
        )

    def _apply_ratio_enabled(self, enabled: bool) -> None:
        """The two modes are mutually exclusive; grey out the unused one."""
        self.target_ratio.setEnabled(enabled)
        self.keep_above.setEnabled(not enabled)
        self._refresh_score_stats()

    def _on_ratio_toggled(self, checked: bool) -> None:
        self._apply_ratio_enabled(checked)
        self._emit()

    def _face_only_widgets(self) -> list[QWidget]:
        """Inputs that only reach the score in face-priority mode.

        Leaving them editable with the mode off produces "I changed it and
        nothing happened". A live widget is a promise that its value is
        being used.
        """
        return [
            self.penalty_face_defocus,
            self.bonus_focus_on_face,
            self.penalty_no_face,
            self.bonus_eyes_open,
            self.penalty_eyes_closed,
            self.eyes_closed_below,
            self.face_bonus_full_area,
            *self.bonus_spins.values(),
        ]

    def _apply_face_priority_enabled(self, checked: bool) -> None:
        for widget in self._face_only_widgets():
            widget.setEnabled(checked)
        self._refresh_formula()

    def _on_face_priority_toggled(self, checked: bool) -> None:
        self._apply_face_priority_enabled(checked)
        self._emit()

    def _refresh_formula(self) -> None:
        """Match the displayed formula to the multiplier actually in use.

        The multiplier depends on the mode, so hard-coding either value
        leaves the formula on screen disagreeing with the real calculation
        in the other mode.
        """
        on = self.face_priority.isChecked()
        scale = SHARPNESS_SCALE if on else SHARPNESS_SCALE_NO_FACE
        if on:
            tail = tr("     × {scale:g} + bonuses − penalties").format(scale=scale)
        else:
            tail = tr("     × {scale:g}  (face priority off — no face or eye "
                      "terms)").format(scale=scale)
        self.formula.setText(
            tr("score = (ROI sharpness × trust\n"
               "     + frame sharpness × (1 − trust))\n") + tail
        )

    def _emit(self) -> None:
        if not self._loading:
            self.apply_to_config()
            self.preset_bar.mark_modified()
            self.changed.emit()

    def reset_to_defaults(self) -> None:
        defaults = Config()
        self.config.score = defaults.score
        self.config.group = defaults.group
        self.load_from_config()
        self.preset_bar.refresh()
        self.changed.emit()

    # ------------------------------------------------------------ presets

    def _collect_preset(self) -> dict:
        self.apply_to_config()
        return {"score": asdict(self.config.score), "group": asdict(self.config.group)}

    def _apply_preset(self, data: dict) -> None:
        """Apply a preset. Unknown keys are ignored so old files still open."""
        for section, cls, target in (
            ("score", ScoreConfig, "score"),
            ("group", GroupConfig, "group"),
        ):
            values = data.get(section) or {}
            valid = {f.name for f in __import__("dataclasses").fields(cls)}
            merged = asdict(getattr(self.config, target))
            merged.update({k: v for k, v in values.items() if k in valid})
            setattr(self.config, target, cls(**merged))
        self.load_from_config()

    # ------------------------------------------------------------ files

    def export_to_file(self) -> None:
        """Write the whole set of grading criteria to a YAML file."""
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Save grading criteria"), tr("criteria.yaml"),
            "YAML (*.yaml *.yml)"
        )
        if not path:
            return

        payload = {
            "name": Path(path).stem,
            "saved": datetime.now().isoformat(timespec="seconds"),
            "data": self._collect_preset(),
        }
        try:
            Path(path).write_text(
                yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
        except OSError as exc:
            QMessageBox.warning(self, tr("Save failed"), str(exc))
            return
        QMessageBox.information(
            self, tr("Grading criteria"), tr("Saved to:\n{path}").format(path=path))

    def import_from_file(self) -> None:
        """Load grading criteria from a file.

        Same format the preset store writes, so the two interchange freely.
        """
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Load grading criteria"), "", "YAML (*.yaml *.yml)"
        )
        if not path:
            return

        try:
            payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            QMessageBox.warning(self, tr("Load failed"), str(exc))
            return

        # Accept both the preset shape ({"data": {...}}) and a bare body
        data = payload.get("data") if isinstance(payload, dict) else None
        if data is None:
            data = payload
        if not isinstance(data, dict) or not ({"score", "group"} & set(data)):
            QMessageBox.warning(
                self, tr("Load failed"), tr("Not a grading criteria file.")
            )
            return

        self._apply_preset(data)
        self.preset_bar.refresh()
        self.changed.emit()
        QMessageBox.information(
            self, tr("Grading criteria"), tr("Loaded from:\n{path}").format(path=path))

    # ------------------------------------------------------------ guidance

    def show_floor(
        self, floor_ratio: float, group_count: int, total: int, dropped: int = 0
    ) -> None:
        """Report the reachable floor, so the target is not set below it."""
        if total == 0:
            self.floor_label.setText("")
            self.dropped_label.setText("")
            return

        text = tr("{scenes} scenes / {total} photos\n"
                  "With these settings the keep floor is {floor:.1f}%.").format(
                      scenes=group_count, total=total, floor=floor_ratio * 100)
        if self.use_ratio.isChecked() and self.target_ratio.value() / 100.0 < floor_ratio:
            text += tr("\n⚠ The target is below the floor, so the floor applies.")
        self.floor_label.setText(text)

        if dropped:
            self.dropped_label.setText(
                tr("The quality floor leaves {count} scenes with no keep. "
                   "Go through review for those.").format(count=dropped)
            )
        else:
            self.dropped_label.setText("")
