"""Start-analysis dialog - shows what and how many, and picks the options.

It only comes up when the analyse button is pressed. The automatic analysis
on opening a folder still starts straight away, as it does now - so as not
to lay one more click on the most common flow. This window is the place for
"analyse again", which is why the option to ignore the cache lives here.

It was built so that when options grow it is this window, not the toolbar,
that takes them (the precision group). The values are written back to the
session's AnalyzeConfig and carry over to the next analysis.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QVBoxLayout,
)

from ..core.config import AnalyzeConfig
from ..core.pipeline import estimate_analysis_seconds
from .i18n import format_duration, tr


@dataclass(frozen=True)
class AnalysisOptions:
    """The choices the dialog hands back."""

    use_cache: bool
    noise_compensation: bool
    af_roi_hint: bool
    center_priority: bool
    demosaic_small_preview: bool = False


class AnalysisStartDialog(QDialog):
    """Shows the photo count and cache state first, then lets you pick the
    cache and precision options."""

    def __init__(self, photo_count: int, cached_count: int,
                 analyze: AnalyzeConfig, parent=None,
                 small_preview_count: int = 0, cache_note: str = ""):
        super().__init__(parent)
        self.setWindowTitle(tr("Start analysis"))
        self._photo_count = photo_count
        self._cached_count = cached_count
        self._small_preview_count = small_preview_count

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self.summary = QLabel(
            tr("{count} photos").format(count=photo_count)
        )
        self.summary.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(self.summary)

        if cached_count > 0:
            cache_text = tr("Cache: {count} photos can be reused").format(
                count=cached_count)
        else:
            cache_text = tr("Cache: none — everything will be analysed fresh")
        self.cache_label = QLabel(cache_text)
        layout.addWidget(self.cache_label)
        # Where the cache is going, when that is not the usual place: a
        # locked card or a read-only drive used to leave the user guessing
        # why nothing was ever remembered.
        self.cache_note = None
        if cache_note:
            self.cache_note = QLabel(cache_note)
            self.cache_note.setWordWrap(True)
            self.cache_note.setStyleSheet("color: palette(mid);")
            layout.addWidget(self.cache_note)

        # ---------------- Cache
        self.use_cache = QCheckBox(tr("Use cached results"))
        self.use_cache.setChecked(cached_count > 0)
        self.use_cache.setEnabled(cached_count > 0)
        if cached_count > 0:
            self.use_cache.setToolTip(
                tr("Unchecked: ignore the cache and re-analyse every photo."))
        else:
            # Even when disabled it has to show "why it cannot be pressed" -
            # a locked control with no reason reads to the user as a bug
            # (learnt from the JPEG lock).
            self.use_cache.setToolTip(
                tr("No usable cache for these photos and settings."))
        layout.addWidget(self.use_cache)

        # ---------------- Precise analysis
        precise = QGroupBox(tr("Precision"))
        box = QVBoxLayout(precise)

        self.noise_comp = QCheckBox(tr("Noise-robust sharpness"))
        self.noise_comp.setChecked(analyze.noise_compensation)
        self.noise_comp.setToolTip(tr(
            "Subtracts the noise contribution before scoring sharpness, so\n"
            "noisy soft shots stop scoring as sharp (high-ISO bursts).\n"
            "Measured on 2,846 photos: keeps unchanged, noisy soft frames\n"
            "demoted. Turn off only to compare with the old measurement."))
        box.addWidget(self.noise_comp)

        self.center_priority = QCheckBox(tr("Single-subject framing (portrait)"))
        self.center_priority.setChecked(analyze.center_priority)
        self.center_priority.setToolTip(tr(
            "Pick the main face by centrality first.\n"
            "For portrait-style shoots that keep one subject near the middle.\n"
            "Leave off for group or stage photos."))
        box.addWidget(self.center_priority)

        self.af_hint = QCheckBox(tr("Use camera AF point when no face is found"))
        self.af_hint.setChecked(analyze.af_roi_hint)
        self.af_hint.setToolTip(tr(
            "Reads the autofocus position the camera recorded (Sony, Canon\n"
            "CR3, Nikon) and judges that area instead of guessing the\n"
            "sharpest tile — only for photos where no face was detected.\n"
            "Faces and eyes always take priority."))
        box.addWidget(self.af_hint)

        # Only appears when small previews (Panasonic RW2) are mixed in. It
        # does not add a choice to a folder that has none of those files.
        self.demosaic_small = QCheckBox(
            tr("Develop small-preview RAW for analysis ({count} photos)")
            .format(count=small_preview_count))
        self.demosaic_small.setChecked(
            analyze.demosaic_small_preview and small_preview_count > 0)
        self.demosaic_small.setVisible(small_preview_count > 0)
        self.demosaic_small.setToolTip(tr(
            "Some RAW files carry a preview far smaller than the sensor —\n"
            "Panasonic RW2 embeds 32% of the long edge, where Sony and Canon\n"
            "embed 98-99%. Sharpness is meant to be measured at full detail,\n"
            "so those shots are currently judged on a different scale.\n"
            "This develops them instead. See the times below."))
        self.demosaic_small.toggled.connect(self._refresh_estimate)
        box.addWidget(self.demosaic_small)

        layout.addWidget(precise)

        self.estimate = QLabel("")
        self.estimate.setStyleSheet("color: #9a9aa2;")
        layout.addWidget(self.estimate)

        buttons = QDialogButtonBox()
        self.start_button = buttons.addButton(
            tr("Start analysis"), QDialogButtonBox.AcceptRole)
        # The standard Cancel button only turns Korean with Qt's built-in
        # translation, and we do not ship that catalogue. The label is
        # attached directly with our own tr().
        buttons.addButton(tr("Cancel"), QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.use_cache.toggled.connect(self._refresh_estimate)
        self._refresh_estimate()
        self.start_button.setDefault(True)

    # ------------------------------------------------------------------

    def _refresh_estimate(self) -> None:
        """Refreshes how many get analysed fresh, and how long it takes.

        In a folder that has the small-preview option, it shows **both times
        side by side**. "5x slower" is not something you can decide on -
        1 minute becoming 5 minutes and 20 seconds becoming 100 seconds are
        different decisions.
        """
        reused = self._cached_count if self.use_cache.isChecked() else 0
        pending = max(0, self._photo_count - reused)
        if pending == 0:
            self.estimate.setText(
                tr("Everything is cached — results will appear instantly."))
            return

        # There is no telling which side the ones reused from cache came
        # from. Assume the ratio is the same and split the remaining count
        # in proportion.
        share = (self._small_preview_count / self._photo_count
                 if self._photo_count else 0.0)
        heavy = round(pending * share)

        chosen = self.demosaic_small.isChecked() and self._small_preview_count > 0
        seconds = estimate_analysis_seconds(
            pending, demosaic_count=heavy if chosen else 0)
        text = tr("{pending} photos to analyse — {duration}").format(
            pending=pending, duration=format_duration(seconds))
        if reused:
            text += tr(" ({reused} reused from cache)").format(reused=reused)

        if self._small_preview_count > 0:
            fast = format_duration(estimate_analysis_seconds(pending))
            slow = format_duration(
                estimate_analysis_seconds(pending, demosaic_count=heavy))
            text += "\n" + tr("Embedded preview: {fast}   ·   Developed: {slow}") \
                .format(fast=fast, slow=slow)
        self.estimate.setText(text)

    def options(self) -> AnalysisOptions:
        return AnalysisOptions(
            use_cache=self.use_cache.isChecked() and self.use_cache.isEnabled(),
            noise_compensation=self.noise_comp.isChecked(),
            af_roi_hint=self.af_hint.isChecked(),
            center_priority=self.center_priority.isChecked(),
            demosaic_small_preview=(self.demosaic_small.isChecked()
                                    and self._small_preview_count > 0),
        )

    @staticmethod
    def ask(photo_count: int, cached_count: int, analyze: AnalyzeConfig,
            parent=None, small_preview_count: int = 0,
            cache_note: str = "") -> AnalysisOptions | None:
        """Puts the dialog up; None on cancel."""
        dialog = AnalysisStartDialog(photo_count, cached_count, analyze, parent,
                                     small_preview_count, cache_note)
        dialog.setModal(True)
        accepted = dialog.exec() == QDialog.Accepted
        options = dialog.options() if accepted else None
        dialog.deleteLater()
        return options
