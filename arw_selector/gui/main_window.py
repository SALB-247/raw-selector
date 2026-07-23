"""Main window.

The flow: pick a folder → analyse → check and adjust in the grid → export.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSlider,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ..core import export as export_module
from ..core import state
from ..core.appinfo import APP_NAME
from ..core.cache import cache_stats, clear_cache, default_cache_path
from ..core.config import Config
from ..core.export_options import ExportOptions
from ..core.ordering import SortMode, sort_records
from ..core.pipeline import estimate_analysis_seconds, format_duration
from ..core.raw_io import RAW_FILE_FILTER
from ..core.export_queue import ExportQueue
from ..core.session import SelectionSession
from ..core.types import Grade, ImageRecord
from ..core.scoring import (
    achievable_keep_floor,
    groups_without_keep,
    records_in_groups_without_keep,
)
from .queue_panel import QueuePanel
from .export_dialog import ExportDialog
from .filter_bar import NO_KEEP, FilterBar
from .flow_layout import FlowLayout
from .grid_view import ThumbnailGrid
from . import i18n
from .attention import ButtonPulse
from .i18n import tr
from .loupe import LoupeDialog
from .ordering_text import sort_label
from .score_card import ScoreCard
from .settings_panel import SettingsPanel
from .workers import AnalysisWorker, ExportWorker
from . import theme

log = logging.getLogger(__name__)


def _raw_count(records) -> int:
    """How many of these are actually RAW.

    The export dialog locks its RAW-only options when this is zero. A
    JPEG-only batch has no original to preserve and no companion files, and
    leaving those switches live means setting them and then hunting for why
    nothing changed.
    """
    from ..core.raw_io import is_raw

    return sum(1 for record in records if is_raw(record.path))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1400, 900)

        self.config = Config()
        self.session: SelectionSession | None = None
        self.folder: Path | None = None
        self.analysis_worker: AnalysisWorker | None = None
        self.export_worker: ExportWorker | None = None
        self.queue = ExportQueue()
        self.export_options = ExportOptions()
        self._export_is_queue = False
        self._editing_locked = False
        """Whether grade and develop edits are locked while an export runs."""

        self._loupes: dict[Path, object] = {}
        """Open develop windows, tracked so one photo cannot open twice."""

        self._explicit_paths: list[Path] | None = None
        """Files chosen via Open files. None means scan the whole folder."""

        self._build_ui()
        self._build_shortcuts()

        # Start once the window is actually up. Firing during construction
        # means the first blink happens before anything is on screen.
        QTimer.singleShot(400, self._pulse_first_step)

    # ------------------------------------------------------------ layout

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)

        # The grid comes first: the toolbar connects to its slots
        self.grid = ThumbnailGrid(Path.cwd())
        self.grid.record_activated.connect(self.open_loupe)
        self.grid.selectionModel().selectionChanged.connect(self._on_selection_changed)

        # The controls sit on their own panel to separate them from the photo
        # grid. A layout alone cannot carry a background colour, hence the
        # wrapping widget.
        toolbar_panel = QWidget()
        toolbar_panel.setObjectName("toolbar")
        toolbar_panel.setLayout(self._build_toolbar())
        # The toolbar wraps, so its height depends on how wide it ends up.
        # Without this the parent layout budgets for one row and clips the
        # second one.
        policy = toolbar_panel.sizePolicy()
        policy.setHeightForWidth(True)
        toolbar_panel.setSizePolicy(policy)
        layout.addWidget(toolbar_panel)

        self.filter_bar = FilterBar()
        self.filter_bar.filter_changed.connect(self.apply_filter)

        # Sorting sits at the right of the filter row — going through a batch
        # by score, ignoring scenes, has to be possible.
        filter_panel = QWidget()
        filter_panel.setObjectName("filterbar")
        filter_row = QHBoxLayout(filter_panel)
        filter_row.setContentsMargins(8, 5, 8, 5)
        filter_row.addWidget(self.filter_bar, 1)
        filter_row.addWidget(QLabel(tr("Sort")))
        self.sort_combo = QComboBox()
        for mode in (SortMode.FILE, SortMode.SCORE_DESC, SortMode.SCORE_ASC):
            self.sort_combo.addItem(sort_label(mode), mode)
        self.sort_combo.setToolTip(
            tr("Sorting by score ignores scenes and lines the whole batch up"))
        self.sort_combo.currentIndexChanged.connect(lambda _=0: self.apply_filter())
        filter_row.addWidget(self.sort_combo)
        layout.addWidget(filter_panel)

        # Grid on the left, panels on the right
        body = QHBoxLayout()
        body.setSpacing(8)
        body.addWidget(self.grid, 1)

        self.settings_panel = SettingsPanel(self.config)
        self.settings_panel.changed.connect(self.on_settings_changed)
        self.settings_panel.setVisible(False)
        body.addWidget(self.settings_panel)

        self.queue_panel = QueuePanel(self.queue)
        self.queue_panel.export_requested.connect(self.export_queue)
        self.queue_panel.edit_requested.connect(self.edit_queue_entry)
        self.queue_panel.setVisible(False)
        body.addWidget(self.queue_panel)

        layout.addLayout(body, 1)

        # The score card runs along the bottom, under the grid. Putting it in
        # the right-hand panel would make it compete with the criteria and
        # queue panels, and with all three open the grid drops to two columns.
        self.score_card = ScoreCard()
        layout.addWidget(self.score_card)

        # Progress and cancel belong together. The bar used to sit at the top
        # with the stop button off at the end of the toolbar, so during a long
        # run the eye had to move between two places, and it was never obvious
        # where cancelling lived.
        self.setStatusBar(QStatusBar())
        self.statusBar().setSizeGripEnabled(False)
        self._build_status_bar()
        self.setStyleSheet(theme.window_style())

    def _build_status_bar(self) -> None:
        """Bottom bar — what is happening right now, and how to stop it."""
        bar = self.statusBar()
        # At the default margins the stop button hugs the window corner and
        # is awkward to hit
        bar.setContentsMargins(4, 2, 8, 2)

        self.status_label = QLabel(tr("Open a folder to begin"))
        self.status_label.setStyleSheet(f"color: {theme.TEXT}; padding-left: 6px;")
        bar.addWidget(self.status_label, 1)

        # The progress bar only appears during work; otherwise it is a strip
        # of wasted space.
        self.status_progress = QProgressBar()
        self.status_progress.setFixedWidth(240)
        self.status_progress.setTextVisible(True)
        self.status_progress.setVisible(False)
        bar.addPermanentWidget(self.status_progress)

        self.status_eta = QLabel()
        self.status_eta.setStyleSheet(theme.hint_label())
        self.status_eta.setVisible(False)
        bar.addPermanentWidget(self.status_eta)

        self.stop_button = QPushButton(tr("Stop"))
        self.stop_button.setStyleSheet(theme.DANGER_BUTTON)
        self.stop_button.setToolTip(tr("Stop the running task (Esc)"))
        self.stop_button.clicked.connect(self.cancel_running)
        self.stop_button.setVisible(False)
        bar.addPermanentWidget(self.stop_button)

        stop_action = QAction(self)
        stop_action.setShortcut(QKeySequence(Qt.Key_Escape))
        stop_action.triggered.connect(self.cancel_running)
        self.addAction(stop_action)

    def set_status(self, message: str, *, busy: bool = False) -> None:
        """Bottom-bar message. Busy states are colour-coded as well."""
        self.status_label.setText(message)
        self.status_label.setStyleSheet(
            f"color: {theme.ACCENT if busy else theme.TEXT};"
            " padding-left: 6px; font-size: 12px;"
            + (" font-weight: bold;" if busy else "")
        )

    def _begin_task(self, message: str) -> None:
        """Start of a long task. Progress bar and stop button come up together."""
        self.status_progress.setRange(0, 0)  # indeterminate until a total is known
        self.status_progress.setVisible(True)
        self.stop_button.setVisible(True)
        self.stop_button.setEnabled(True)
        self.stop_button.setText(tr("Stop"))
        self.set_status(message, busy=True)

    def _end_task(self) -> None:
        """Task over. Success, failure and cancellation all pass through here."""
        self.status_progress.setVisible(False)
        self.status_progress.setRange(0, 100)
        self.status_progress.setValue(0)
        self.status_eta.setVisible(False)
        self.stop_button.setVisible(False)
        self.stop_button.setEnabled(True)
        self.stop_button.setText(tr("Stop"))

    def _set_eta(self, seconds: float | None) -> None:
        """Time left. Raw seconds are hard to read on a large batch."""
        if not seconds or seconds <= 0:
            self.status_eta.setVisible(False)
            return
        if seconds < 60:
            text = tr("about {value:.0f}s left").format(value=seconds)
        elif seconds < 3600:
            text = tr("about {value:.0f} min left").format(value=seconds / 60)
        else:
            text = tr("about {value:.1f} h left").format(value=seconds / 3600)
        self.status_eta.setText(text)
        self.status_eta.setVisible(True)

    def _build_toolbar(self) -> FlowLayout:
        """A dozen controls in one row — which wraps when the window is narrow.

        As a plain QHBoxLayout this row set the window's minimum width, and
        Qt will not shrink a window below that. Measured with the real
        interface font: 1366px in Korean, 1547px in English. The lowest
        default Retina resolution on an Apple Silicon MacBook is 1440x900
        points, so the English toolbar did not fit at all.
        """
        bar = FlowLayout(spacing=6)

        self.open_button = QPushButton(tr("Open folder"))
        self.open_button.clicked.connect(self.choose_folder)
        bar.addWidget(self.open_button)

        self.open_files_button = QPushButton(tr("Open files"))
        self.open_files_button.setToolTip(tr(
            "Open one file or a handful, rather than a whole folder.\n"
            "Reads every RAW format: ARW, CR3, NEF, RAF, ORF, RW2, DNG."
        ))
        self.open_files_button.clicked.connect(self.choose_files)
        bar.addWidget(self.open_files_button)

        self.analyze_button = QPushButton(tr("Analyse"))
        self.analyze_button.clicked.connect(self.start_analysis)
        self.analyze_button.setEnabled(False)
        bar.addWidget(self.analyze_button)

        # Blink whatever should be pressed next. There are a dozen buttons
        # here and no way for a first-time user to tell where to start.
        self.open_pulse = ButtonPulse(self.open_button)
        self.analyze_pulse = ButtonPulse(self.analyze_button)

        self.recursive_check = QCheckBox(tr("Include subfolders"))
        self.recursive_check.setChecked(True)
        bar.addWidget(self.recursive_check)

        bar.addSpacing(16)
        self.settings_button = QPushButton(tr("Criteria ▸"))
        self.settings_button.setCheckable(True)
        self.settings_button.toggled.connect(self.toggle_settings)
        bar.addWidget(self.settings_button)

        self.develop_button = QPushButton(tr("Develop"))
        self.develop_button.setToolTip(tr(
            "Adjust the selected photo in the preview (D)\n"
            "Select several to apply the same edit to all of them"
        ))
        self.develop_button.clicked.connect(self.open_develop)
        self.develop_button.setEnabled(False)
        bar.addWidget(self.develop_button)

        self.queue_add_button = QPushButton(tr("Add to queue"))
        self.queue_add_button.setToolTip(tr(
            "Stack the selected photos, with their current edit, on the "
            "queue (Q)\nGather across folders and export in one go"
        ))
        self.queue_add_button.clicked.connect(self.add_to_queue)
        self.queue_add_button.setEnabled(False)
        bar.addWidget(self.queue_add_button)

        self.queue_button = QPushButton(tr("Queue ▸"))
        self.queue_button.setCheckable(True)
        self.queue_button.toggled.connect(self.toggle_queue)
        bar.addWidget(self.queue_button)

        # What a double-click does: quick preview (embedded JPEG) or develop
        # (demosaiced RAW)
        bar.addSpacing(16)
        bar.addWidget(QLabel(tr("Double-click")))
        self.dblclick_group = QButtonGroup(self)
        self.dblclick_preview = QRadioButton(tr("Preview"))
        self.dblclick_preview.setToolTip(tr(
            "Double-click opens the embedded JPEG straight away — fast, with "
            "the camera's own colour"
        ))
        self.dblclick_develop = QRadioButton(tr("Develop"))
        self.dblclick_develop.setToolTip(tr(
            "Double-click demosaics the RAW for accurate colour and tone"
        ))
        self.dblclick_develop.setChecked(True)
        self.dblclick_group.addButton(self.dblclick_preview)
        self.dblclick_group.addButton(self.dblclick_develop)
        bar.addWidget(self.dblclick_preview)
        bar.addWidget(self.dblclick_develop)

        bar.addSpacing(16)
        bar.addWidget(QLabel(tr("Size")))
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(90, 360)
        self.size_slider.setValue(180)
        self.size_slider.setFixedWidth(110)
        self.size_slider.valueChanged.connect(self.grid.set_thumb_size)
        bar.addWidget(self.size_slider)

        bar.addSpacing(16)

        # Export is where this screen leads. Giving it the same weight as the
        # utility buttons leaves no clue about what to press.
        self.export_button = QPushButton(tr("Export"))
        self.export_button.setStyleSheet(theme.PRIMARY_BUTTON)
        self.export_button.clicked.connect(self.start_export)
        self.export_button.setEnabled(False)
        bar.addWidget(self.export_button)

        self.undo_button = QPushButton(tr("Undo"))
        self.undo_button.clicked.connect(self.undo_export)
        self.undo_button.setEnabled(False)
        bar.addWidget(self.undo_button)

        self.cache_button = QPushButton(tr("Cache"))
        self.cache_button.setToolTip(tr("Inspect and clear the cache"))
        self.cache_button.clicked.connect(self.manage_cache)
        self.cache_button.setEnabled(False)
        bar.addWidget(self.cache_button)

        # Even for a camera the library knows, the values measured on this
        # machine are sometimes preferable. The automatic prompt only appears
        # for unsupported bodies, so there has to be a way to ask for it.
        self.calibrate_button = QPushButton(tr("Colour calibration"))
        self.calibrate_button.setToolTip(tr(
            "Compare this folder's photos against the camera's own JPEGs to\n"
            "work out colour corrections. The result takes precedence over\n"
            "the library's defaults."
        ))
        self.calibrate_button.clicked.connect(self.calibrate_current_folder)
        self.calibrate_button.setEnabled(False)
        bar.addWidget(self.calibrate_button)

        # Preferences is always available — it holds the language, and someone
        # who cannot read the current language needs to reach it.
        self.preferences_button = QPushButton(tr("Preferences"))
        self.preferences_button.setToolTip(
            tr("Interface language, updates and licences"))
        self.preferences_button.clicked.connect(self.open_preferences)
        bar.addWidget(self.preferences_button)

        return bar

    def open_preferences(self) -> None:
        """Preferences. A language change needs a restart — say so once."""
        from .preferences_dialog import PreferencesDialog

        before = state.language()
        dialog = PreferencesDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        if dialog.language_changed_from(before):
            QMessageBox.information(
                self, tr("Preferences"),
                tr("The interface language changes the next time the app starts."),
            )

    def _build_shortcuts(self) -> None:
        """Grade from the keyboard. Mouse-only does not survive 4000 photos."""
        for key, grade in [("1", Grade.KEEP), ("2", Grade.REVIEW), ("3", Grade.REJECT)]:
            action = QAction(self)
            action.setShortcut(QKeySequence(key))
            action.triggered.connect(lambda _=False, g=grade: self.set_manual_grade(g))
            self.addAction(action)

        reset = QAction(self)
        reset.setShortcut(QKeySequence("0"))
        reset.triggered.connect(lambda: self.set_manual_grade(None))
        self.addAction(reset)

        loupe = QAction(self)
        loupe.setShortcut(QKeySequence(Qt.Key_Space))
        loupe.triggered.connect(self.open_selected_loupe)
        self.addAction(loupe)

        develop = QAction(self)
        develop.setShortcut(QKeySequence("D"))
        develop.triggered.connect(self.open_develop)
        self.addAction(develop)

        enqueue = QAction(self)
        enqueue.setShortcut(QKeySequence("Q"))
        enqueue.triggered.connect(self.add_to_queue)
        self.addAction(enqueue)

    # ------------------------------------------------------------ analysis

    def _pulse_first_step(self) -> None:
        """Point at Open folder while nothing has been opened yet."""
        if self.folder is None and not self._explicit_paths:
            self.open_pulse.start()

    def choose_folder(self) -> None:
        # Start from wherever the last folder was. Shoots usually pile up in
        # the same place, so navigating from scratch every time is wasted work.
        start = state.last_folder()
        folder = QFileDialog.getExistingDirectory(
            self, tr("Choose a RAW folder"), str(start) if start else ""
        )
        if not folder:
            return

        chosen = Path(folder)
        # **Picking the same folder again does not blink.** Blinking "press
        # analyse" at someone already looking at analysed results reads as an
        # instruction to press it, and 4000 photos get re-analysed.
        changed = chosen != self.folder

        self.folder = chosen
        state.remember_folder(self.folder)
        self._explicit_paths = None  # picking a folder means scan all of it
        self.analyze_button.setEnabled(True)
        self.undo_button.setEnabled(bool(export_module.find_logs(self.folder)))
        self._refresh_cache_button()
        self.set_status(
            tr("{folder} — press Analyse to start").format(folder=self.folder))
        if changed:
            self.analyze_pulse.start()

    def choose_files(self) -> None:
        """Open individual files, when a whole folder is not needed."""
        paths, _ = QFileDialog.getOpenFileNames(
            self, tr("Choose RAW files"), "",
            f"{RAW_FILE_FILTER};;" + tr("All files (*)")
        )
        if not paths:
            return

        self._explicit_paths = [Path(p) for p in paths]
        self.folder = self._explicit_paths[0].parent
        self.analyze_button.setEnabled(True)
        self._refresh_cache_button()
        self.undo_button.setEnabled(bool(export_module.find_logs(self.folder)))
        self.set_status(
            tr("{count} files selected — press Analyse to start").format(
                count=len(paths))
        )
        self.start_analysis()

    def start_analysis(self) -> None:
        if self.folder is None or (self.analysis_worker and self.analysis_worker.isRunning()):
            return

        self.config.recursive = self.recursive_check.isChecked()
        self._set_busy(True)
        self._begin_task(tr("Preparing to analyse…"))

        self.analysis_worker = AnalysisWorker(
            self.folder, self.config, paths=self._explicit_paths
        )
        self.analysis_worker.progressed.connect(self.on_progress)
        self.analysis_worker.finished_ok.connect(self.on_analysis_done)
        self.analysis_worker.failed.connect(self.on_worker_failed)
        self.analysis_worker.start()

    def on_progress(self, progress) -> None:
        self.status_progress.setMaximum(progress.total)
        self.status_progress.setValue(progress.done)
        self._set_eta(progress.eta_seconds)
        self.set_status(
            tr("Analysing {done}/{total} (cached {cached}, failed {failed})").format(
                done=progress.done, total=progress.total,
                cached=progress.cached, failed=progress.failed),
            busy=True,
        )

    def on_analysis_done(self, session: SelectionSession) -> None:
        # Cancelling also lands here, with the results only partly filled in.
        # Not saying so leaves a 300-of-4000 summary looking like a finished one.
        cancelled = bool(self.analysis_worker and self.analysis_worker.is_cancelled())

        self.session = session
        self._set_busy(False)
        self._end_task()
        self.export_button.setEnabled(bool(session.records))
        self.calibrate_button.setEnabled(bool(session.records))
        self.apply_filter()  # this also hands over the thumbnail cache folder
        self._show_summary(cancelled=cancelled)
        self._refresh_cache_button()
        if not cancelled:
            self._offer_calibration(session)

    def calibrate_current_folder(self) -> None:
        """Colour calibration the user asked for directly.

        Runs even for a camera the library knows, and even when a calibration
        already exists — for when the values measured here should win over the
        library's defaults.
        """
        from PySide6.QtWidgets import QMessageBox

        from ..core.develop import calibration as calib
        from .calibration_dialog import run_calibration

        if self.session is None or not self.session.records:
            QMessageBox.information(
                self, tr("Colour calibration"),
                tr("Open and analyse a photo folder first.")
            )
            return

        paths = [record.path for record in self.session.records if record.ok]
        need = calib.find_uncalibrated(paths, force=True)
        if need is None:
            QMessageBox.information(
                self, tr("Colour calibration"),
                tr("No usable samples were found in this folder.\n\n"
                   "It needs at least {count} photos from the same camera,\n"
                   "each carrying the camera's embedded preview.").format(
                       count=calib.MIN_SAMPLES),
            )
            return

        if run_calibration(self, need, manual=True):
            self.grid.refresh()

    def _offer_calibration(self, session: SelectionSession) -> None:
        """Offer calibration for a camera the library does not know.

        Asked after analysis, not before: up front the user does not yet know
        what these photos are, and running the measurement alongside analysis
        slows both down.
        """
        try:
            from ..core.develop import calibration as calib
            from .calibration_dialog import run_calibration

            paths = [record.path for record in session.records if record.ok]
            need = calib.find_uncalibrated(paths)
            if need is None:
                return
            if run_calibration(self, need):
                # A new calibration leaves existing thumbnails and previews
                # carrying the old colour
                self.grid.refresh()
        except Exception:  # noqa: BLE001 - a failed offer must not block selection
            log.warning("colour calibration check failed", exc_info=True)

    def on_worker_failed(self, message: str) -> None:
        self._set_busy(False)
        self._set_editing_locked(False)
        self._end_task()
        self.set_status(tr("The task failed"))
        QMessageBox.critical(self, tr("Failed"), message)

    def _show_summary(self, *, cancelled: bool = False) -> None:
        if not self.session:
            return
        summary = self.session.summary
        total = len(self.session.records)
        failed = len(self.session.failed)

        score_config = self.config.score
        empty_groups = groups_without_keep(self.session.records)
        no_keep_records = records_in_groups_without_keep(self.session.records)

        self.filter_bar.update_counts(summary, total, len(no_keep_records))
        self.settings_panel.show_floor(
            achievable_keep_floor(self.session.records, score_config),
            self.session.group_count,
            total,
            len(empty_groups),
        )
        # Ratio mode derives the threshold from this distribution. Without
        # showing it there is no answering "I set 10%, why did this drop out".
        self.settings_panel.set_score_stats(
            [r.score for r in self.session.records if r.ok]
        )

        text = tr("{total} photos · {scenes} scenes · "
                  "keep {keep} / review {review} / reject {reject}").format(
                      total=total, scenes=self.session.group_count,
                      keep=summary["keep"], review=summary["review"],
                      reject=summary["reject"])
        if failed:
            text += tr(" · {count} failed to analyse").format(count=failed)
        if cancelled:
            text = tr("Cancelled — results so far: ") + text
        self.set_status(text)

    def _set_busy(self, busy: bool) -> None:
        self.open_button.setEnabled(not busy)
        self.open_files_button.setEnabled(not busy)
        self.analyze_button.setEnabled(not busy and self.folder is not None)
        self.export_button.setEnabled(not busy and bool(self.session and self.session.records))
        self.calibrate_button.setEnabled(
            not busy and bool(self.session and self.session.records))
        self.cache_button.setEnabled(not busy and self.folder is not None)

    def _set_editing_locked(self, locked: bool, reason: str = "") -> None:
        """Lock grade and develop edits. Used while an export runs.

        The export worker reads each record's develop settings and grade as it
        goes. Changing them mid-run sends earlier and later photos out with
        different settings, and leaves the undo log describing something that
        never happened.
        """
        self._editing_locked = locked
        for dialog in list(self._loupes.values()):
            try:
                dialog.set_locked(locked, reason)
            except RuntimeError:
                pass  # window already closed

    # ------------------------------------------------------------ filter / grade

    def apply_filter(self, selection=None) -> None:
        """Show only what the current filter selects."""
        if not self.session:
            return
        if selection is None:
            selection = self.filter_bar.current_grade()

        if selection is None:
            records = self.session.records
        elif selection is NO_KEEP:
            records = records_in_groups_without_keep(self.session.records)
        else:
            records = [r for r in self.session.records if r.final_grade == selection]

        records = sort_records(records, self.sort_combo.currentData())
        # The thumbnail cache folder has to be passed **here** for the grid to
        # notice the folder changed. Assigning the model's cache_dir directly
        # means set_records sees the two already equal, and the branch that
        # clears the previous folder's thumbnails never runs.
        self.grid.set_records(
            records, default_cache_path(self.session.folder).parent)

    def _on_selection_changed(self, *_) -> None:
        has_selection = bool(self.grid.selectedIndexes())
        self.develop_button.setEnabled(has_selection)
        self.queue_add_button.setEnabled(has_selection)
        self._refresh_score_card()

    def _refresh_score_card(self) -> None:
        """Score card for the selection. With several, the last one picked."""
        records = self.grid.selected_records()
        self.score_card.show_record(
            records[-1] if records else None, self.config.score
        )

    def toggle_settings(self, shown: bool) -> None:
        self.settings_panel.setVisible(shown)
        self.settings_button.setText(
            tr("Criteria ◂") if shown else tr("Criteria ▸"))

    def on_settings_changed(self) -> None:
        """Re-grade at once when criteria change. No re-analysis.

        Re-analysing 4000 photos takes minutes; re-grading them takes 0.3s.
        Seeing the result immediately is what makes the values adjustable.
        """
        if not self.session:
            return
        self.session.config = self.config
        self.session.regrade()
        self.apply_filter()
        self._show_summary()
        # A score card still holding the old score while the criteria move
        # turns the one panel opened to watch the change into a source of
        # confusion.
        self._refresh_score_card()

    def set_manual_grade(self, grade: Grade | None) -> None:
        # Changing a grade mid-export splits the batch: photos already written
        # were sorted by one rule and the rest by another. It is a keyboard
        # shortcut, so it is easy to hit by accident — blocked here too.
        if getattr(self, "_editing_locked", False):
            self.set_status(tr("Grades cannot be changed during an export"))
            return

        records = self.grid.selected_records()
        if not records:
            return
        for record in records:
            record.manual_grade = grade
        self.grid.refresh()
        self.apply_filter()
        self._show_summary()

    def open_selected_loupe(self) -> None:
        records = self.grid.selected_records()
        if records:
            self.open_loupe(records[0])

    def open_loupe(self, record) -> None:
        """The double-click / Space path.

        Hands over everything currently visible in the grid so ←/→ inside the
        loupe keeps moving. Filtered to keep only, it moves between keeps.
        """
        self._open_loupe_with(record, self._visible_records())

    def _visible_records(self) -> list:
        model = self.grid.model_
        return [model.record_at(row) for row in range(model.rowCount())]

    def _open_loupe_with(self, record, records: list) -> None:
        """Open a develop window.

        Modeless, so the main window keeps working and several can be open at
        once. Re-opening the same photo raises the existing window instead of
        making another.
        """
        if record not in records:
            records = [record, *records]

        existing = self._loupes.get(record.path)
        if existing is not None:
            # Develop windows carry WA_DeleteOnClose, so the C++ object is gone
            # the moment one closes. If any path ever misses the `finished`
            # signal, the reference left here is a husk that crashes on touch.
            # Drop it and open fresh.
            try:
                existing.raise_()
                existing.activateWindow()
                return
            except RuntimeError:
                self._loupes.pop(record.path, None)

        # No parent, deliberately. On Windows a parented window is an "owned
        # window" and always sits **above** its parent, which would make it
        # impossible to bring the main window forward while a develop window is
        # open — no way to see the grid. `_loupes` holds the reference, and the
        # windows close with the main window.
        dialog = LoupeDialog(
            record, records, None, fast=self.dblclick_preview.isChecked()
        )
        dialog.records_changed.connect(self._on_loupe_changed)
        dialog.queue_requested.connect(self._queue_from_loupe)
        dialog.export_requested.connect(self._export_from_loupe)
        dialog.record_switched.connect(
            lambda old, new, d=dialog: self._retrack_loupe(d, old, new)
        )
        dialog.main_face_changed.connect(self._on_main_face_changed)
        dialog.finished.connect(lambda _=0, d=dialog: self._on_loupe_closed(d))

        self._loupes[record.path] = dialog
        # A window opened during an export has to be locked as well, otherwise
        # there is a way around the lock standing wide open.
        if self._editing_locked:
            dialog.set_locked(True, tr(
                "Export in progress — develop and grading are locked until "
                "it finishes."))
        dialog.show()
        dialog.raise_()

    def _retrack_loupe(self, dialog, old_path, new_path) -> None:
        """Moving between photos inside the window moves the tracking key."""
        if self._loupes.get(old_path) is dialog:
            del self._loupes[old_path]
        self._loupes[new_path] = dialog

    def _on_loupe_closed(self, dialog) -> None:
        for path, opened in list(self._loupes.items()):
            if opened is dialog:
                del self._loupes[path]
        self.grid.refresh()
        self.apply_filter()
        self._show_summary()

    def _queue_from_loupe(self, records: list) -> None:
        """Queue from the loupe. Takes that photo, not the grid selection."""
        added, updated = self.queue.add_records(records)
        self.queue_panel.refresh()
        self._update_queue_button()
        self.set_status(
            tr("Queued {added} / updated {updated} · {total} in the queue").format(
                added=added, updated=updated, total=len(self.queue))
        )

    def _export_from_loupe(self, records: list) -> None:
        """Export straight from the loupe."""
        destination = QFileDialog.getExistingDirectory(
            self, tr("Export to"), str(self.folder or Path.home())
        )
        if not destination:
            return

        developed = sum(1 for r in records if r.develop is not None)

        # 등급을 세어 그대로 보여 줍니다. 예전에는 전부 keep으로 적어서,
        # review 사진 한 장을 내보내면서도 "keep 1 · review 0"이 떴습니다.
        counts = Counter(r.final_grade.value for r in records)
        summary = {grade: counts.get(grade, 0)
                   for grade in ("keep", "review", "reject")}

        # 이 사진들은 사용자가 직접 열어서 고른 것입니다. 툴바 내보내기에서
        # 등급 필터를 좁혀 둔 적이 있으면 그 값이 세션 내내 남는데, 여기까지
        # 걸리면 고른 사진이 통째로 걸러져 한 장도 안 나가고 "0장 복사"로
        # 끝났습니다 — 이유는 어디에도 안 나옵니다. 고른 등급은 켜 둡니다.
        options = replace(self.export_options, grades=tuple(sorted(counts)))

        dialog = ExportDialog(
            Path(destination), summary,
            developed, options, self,
            raw_count=_raw_count(records),
        )
        if dialog.exec() != QDialog.Accepted:
            return

        self.export_options = dialog.result_options()
        self._export_is_queue = False
        self._run_export(list(records), Path(destination))

    def _on_main_face_changed(self, record: ImageRecord) -> None:
        """Re-score and re-grade a photo whose main subject was changed.

        Grades are relative within the batch, so one photo cannot be fixed on
        its own. This re-grades rather than re-analyses, so it finishes at once
        even at 4000 photos.
        """
        if record not in self.session.records:
            return
        self.session.regrade()
        self._on_loupe_changed()
        self.set_status(
            tr("{name} — re-graded with the new main subject "
               "(score {score:.1f}, {grade})").format(
                   name=record.path.name, score=record.score,
                   grade=record.final_grade.value)
        )

    def _on_loupe_changed(self) -> None:
        """Bring the grid and summary in line after a loupe edit."""
        self.grid.refresh()
        developed = sum(1 for r in self.session.records if r.develop is not None)
        summary = self.session.summary
        self.set_status(
            tr("keep {keep} / review {review} / reject {reject} · "
               "{developed} edited").format(
                   keep=summary["keep"], review=summary["review"],
                   reject=summary["reject"], developed=developed)
        )

    # ------------------------------------------------------------ develop

    def open_develop(self) -> None:
        """Open the selection in the loupe, where edits and grading happen."""
        records = self.grid.selected_records()
        if not records:
            QMessageBox.information(
                self, tr("Develop"), tr("Select some photos first"))
            return
        self._open_loupe_with(records[0], records)

    # ------------------------------------------------------------ queue

    def toggle_queue(self, shown: bool) -> None:
        self.queue_panel.setVisible(shown)
        self.queue_button.setText(tr("Queue ◂") if shown else tr("Queue ▸"))

    def add_to_queue(self) -> None:
        """Stack the selection on the queue with its current develop settings."""
        records = self.grid.selected_records()
        if not records:
            return

        added, updated = self.queue.add_records(records)
        self.queue_panel.refresh()
        self._update_queue_button()

        message = tr("{count} added to the queue").format(count=added)
        if updated:
            message += tr(", {count} updated").format(count=updated)
        message += tr(" · {total} in the queue").format(total=len(self.queue))
        self.set_status(message)

        if not self.queue_button.isChecked():
            self.queue_button.setChecked(True)

    def _update_queue_button(self) -> None:
        count = len(self.queue)
        arrow = "◂" if self.queue_button.isChecked() else "▸"
        self.queue_button.setText(
            tr("Queue {count} {arrow}").format(count=count, arrow=arrow)
            if count else tr("Queue {arrow}").format(arrow=arrow))

    def edit_queue_entry(self, path) -> None:
        """Edit a queue entry in the develop window.

        The queue only holds paths, with no analysis attached. If the current
        session has the same file, that record is used (so the focus region and
        score show up); otherwise a temporary record is built from the path.
        """
        path = Path(path)
        index = self.queue.index_of(path)
        if index is None:
            return
        entry = self.queue.entries[index]

        record = next(
            (r for r in (self.session.records if self.session else []) if r.path == path),
            None,
        )
        if record is None:
            record = ImageRecord(path=path)
            record.grade = entry.grade
        record.develop = entry.develop

        dialog = LoupeDialog(record, [record], None)   # no parent — see above
        dialog.queue_requested.connect(self._queue_from_loupe)
        dialog.export_requested.connect(self._export_from_loupe)

        def sync_back(_=0) -> None:
            # Push the edit back into the queue. Even if the values came from a
            # preset, once they are edited by hand they are no longer it.
            if record.develop != entry.develop:
                entry.preset_name = None
            entry.develop = record.develop
            entry.grade = record.final_grade

            self.queue_panel.refresh()
            self.grid.refresh()
            self._show_summary()

        dialog.finished.connect(sync_back)
        self._loupes[record.path] = dialog
        dialog.finished.connect(lambda _=0, d=dialog: self._on_loupe_closed(d))
        dialog.show()
        dialog.raise_()

    def export_queue(self) -> None:
        """Export everything stacked on the queue in one run."""
        if not len(self.queue):
            return

        missing = self.queue.missing_sources()
        destination = QFileDialog.getExistingDirectory(
            self, tr("Export the queue to"), str(self.folder or Path.home())
        )
        if not destination:
            return

        if missing:
            QMessageBox.information(
                self, tr("Queue"),
                tr("{count} with a missing source will be skipped.").format(
                    count=len(missing)),
            )

        summary = {"keep": len(self.queue), "review": 0, "reject": 0}
        dialog = ExportDialog(
            Path(destination), summary, self.queue.develop_count,
            self.export_options, self,
            raw_count=_raw_count(self.queue.to_records()),
        )
        if dialog.exec() != QDialog.Accepted:
            return

        self.export_options = dialog.result_options()
        records = [r for r in self.queue.to_records() if r.path.exists()]
        self._export_is_queue = True
        self._run_export(records, Path(destination))

    # ------------------------------------------------------------ cache

    def manage_cache(self) -> None:
        """Show what the cache holds and offer to clear it."""
        target = self.folder or (self.session.folder if self.session else None)
        if target is None:
            return

        stats = cache_stats(target)
        if not stats.exists:
            QMessageBox.information(
                self, tr("Cache"),
                tr("{path}\n\nNo cache here.").format(path=target))
            return

        # How long it takes to rebuild is the only figure that matters to the
        # decision. This used to read "about 1m 30s for 2845 photos" — someone
        # else's folder — which said nothing about this one.
        rebuild = format_duration(
            estimate_analysis_seconds(stats.analysis_entries))
        answer = QMessageBox.question(
            self,
            tr("Clear cache"),
            tr("{path}\n\n"
               "Analysis results: {entries} ({analysis_mb:.1f}MB)\n"
               "Thumbnails: {thumbs} ({thumb_mb:.1f}MB)\n"
               "Total {total_mb:.1f}MB\n\n"
               "Clearing means the next analysis rebuilds it "
               "({entries} photos / {rebuild}).\n"
               "Undo records are kept.\n\n"
               "Clear the cache?").format(
                   path=target, entries=stats.analysis_entries,
                   analysis_mb=stats.analysis_mb, thumbs=stats.thumbnail_count,
                   thumb_mb=stats.thumbnail_mb, total_mb=stats.total_mb,
                   rebuild=rebuild),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        removed = clear_cache(target)
        self.set_status(
            tr("Cache cleared: {entries} results, {thumbs} thumbnails, "
               "{mb:.1f}MB freed").format(
                   entries=removed.analysis_entries,
                   thumbs=removed.thumbnail_count, mb=removed.total_mb)
        )
        self._refresh_cache_button()

    def _refresh_cache_button(self) -> None:
        target = self.folder or (self.session.folder if self.session else None)
        if target is None:
            self.cache_button.setEnabled(False)
            self.cache_button.setText(tr("Cache"))
            return

        stats = cache_stats(target)
        self.cache_button.setEnabled(True)
        self.cache_button.setText(
            tr("Cache {mb:.0f}MB").format(mb=stats.total_mb)
            if stats.exists else tr("No cache")
        )
        self.cache_button.setToolTip(f"{target}\n{stats.summary()}")

    # ------------------------------------------------------------ cancelling

    def cancel_running(self) -> None:
        """Request a stop. The photo already underway finishes first.

        While that happens, pressing again feels like nothing is working. The
        button locks in its pressed state and the message says it was heard.
        """
        stopping = False
        if self.analysis_worker and self.analysis_worker.isRunning():
            self.analysis_worker.cancel()
            self.set_status(
                tr("Stopping analysis — finishing the photo in progress…"),
                busy=True)
            stopping = True
        if self.export_worker and self.export_worker.isRunning():
            self.export_worker.cancel()
            self.set_status(
                tr("Stopping export — finishing the photo in progress…"),
                busy=True)
            stopping = True

        if stopping:
            self.stop_button.setEnabled(False)
            self.stop_button.setText(tr("Stopping…"))
            self.status_eta.setVisible(False)

    # ------------------------------------------------------------ export

    def start_export(self) -> None:
        if not self.session or not self.session.records:
            return

        destination = QFileDialog.getExistingDirectory(
            self, tr("Export to (choosing the source folder creates it inside)"),
            str(self.session.folder)
        )
        if not destination:
            return

        developed = sum(1 for r in self.session.records if r.develop is not None)
        located = sum(
            1 for r in self.session.records
            if r.metadata is not None and getattr(r.metadata, "has_location", False)
        )
        dialog = ExportDialog(
            Path(destination), self.session.summary, developed,
            self.export_options, self,
            located=(located, len(self.session.records)),
            raw_count=_raw_count(self.session.records),
        )
        if dialog.exec() != QDialog.Accepted:
            return

        self.export_options = dialog.result_options()
        self._export_is_queue = False
        self._run_export(self.session.records, Path(destination))

    def _run_export(self, records, destination: Path) -> None:
        """Exports always run in the background.

        With develop applied it measures 7 seconds per photo — 500 photos is an
        hour. A frozen UI for that long reads as a crash, and without a time
        estimate there is no way to decide between waiting and giving up.

        **Never starts a second run.** `_set_busy` only locks the toolbar's
        export button; the queue panel and the loupe both keep their own
        Export, so a second one can arrive mid-run. Overwriting
        `self.export_worker` then drops the last reference to a running
        QThread, which Qt treats as fatal and kills the process outright
        (0xc0000409). Even surviving that, two runs would write into the same
        folder and split the undo log between them.
        """
        if self.export_worker is not None and self.export_worker.isRunning():
            QMessageBox.information(
                self, tr("Export"),
                tr("An export is already running.\n\n"
                   "Start again once it finishes or is cancelled."),
            )
            return

        self._set_busy(True)
        self._set_editing_locked(True, tr(
            "Export in progress — develop and grading are locked until "
            "it finishes."))
        self._export_started = time.monotonic()
        self._begin_task(tr("Preparing to export…"))

        self.export_worker = ExportWorker(records, destination, self.export_options)
        self.export_worker.progressed.connect(self.on_export_progress)
        self.export_worker.finished_ok.connect(self.on_export_done)
        self.export_worker.failed.connect(self.on_worker_failed)
        self.export_worker.start()

    def on_export_progress(self, done: int, total: int) -> None:
        self.status_progress.setMaximum(total)
        self.status_progress.setValue(done)
        self.set_status(
            tr("Exporting {done}/{total}").format(done=done, total=total),
            busy=True)

        # The estimate comes only from the rate measured so far. Per photo this
        # ranges between 0.01s and 7s depending on whether develop runs, so any
        # constant picked ahead of time is wrong.
        started = getattr(self, "_export_started", None)
        if started is not None and done > 0 and total > done:
            elapsed = time.monotonic() - started
            self._set_eta(elapsed / done * (total - done))

    def on_export_done(self, result) -> None:
        self._set_busy(False)
        self._set_editing_locked(False)
        self._end_task()
        self.undo_button.setEnabled(True)

        title = tr("Export cancelled") if result.cancelled else tr("Export finished")
        message = tr("{count} copied").format(count=result.moved)
        if result.rendered:
            message += tr(" · {count} developed").format(count=result.rendered)
        if result.failed:
            message += tr(" · {count} failed").format(count=len(result.failed))
        if result.cancelled:
            message += tr("\n\nUndo can clear up whatever was written before "
                          "you stopped.")

        # Tidy the state first. QMessageBox is modal and spins its own event
        # loop, so anything after it leaves the state inconsistent until the
        # user clicks OK.
        if self._export_is_queue and not result.cancelled and not result.failed:
            # Clear the queue once everything is out. Anything that failed
            # stays, so it can be retried.
            self.queue.clear()
            self.queue_panel.refresh()
            self._update_queue_button()
        self._export_is_queue = False
        self._refresh_cache_button()
        self.set_status(message.replace("\n\n", " · "))

        QMessageBox.information(self, title, message)

    def undo_export(self) -> None:
        target = self.folder or (self.session.folder if self.session else None)
        if target is None:
            return
        logs = export_module.find_logs(target)
        if not logs:
            QMessageBox.information(
                self, tr("Undo"), tr("Nothing to undo"))
            return

        answer = QMessageBox.question(
            self, tr("Undo"),
            tr("{name}\n\nUndo this export?").format(name=logs[0].name),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return

        result = export_module.undo_export(logs[0])
        QMessageBox.information(
            self, tr("Undo finished"),
            tr("{count} cleaned up").format(count=result.moved))
        self.undo_button.setEnabled(bool(export_module.find_logs(target)))

    # ------------------------------------------------------------ shutdown

    def _shutdown_workers(self) -> None:
        """Stop everything running. Used by both close and quit.

        This used to wait three seconds and carry on regardless. Anything still
        running then gets its QThread destroyed, and Qt kills the process
        (0xc0000409). A single photo can take hundreds of milliseconds to
        develop, so three seconds is not long enough.
        """
        from .workers import keep_until_finished, stop_worker

        # Develop windows are top-level with no parent (so the main window can
        # be brought forward). That means they do not close along with it —
        # closing them here is what lets the app actually exit.
        for dialog in list(self._loupes.values()):
            try:
                dialog.close()
            except RuntimeError:
                pass  # window already closed
        self._loupes.clear()

        for name in ("analysis_worker", "export_worker"):
            worker = getattr(self, name, None)
            if worker is None:
                continue
            if not stop_worker(worker):
                # Dropping the reference destroys a running QThread and kills
                # the process right here. Let it go, but keep hold of it.
                log.warning("%s did not stop in time — holding it until it does",
                            name)
                keep_until_finished(worker)
            setattr(self, name, None)

        # Stop the thumbnail pool too: drop whatever is queued, wait only for
        # what is already running.
        try:
            self.grid.model_.shutdown()
        except Exception:  # noqa: BLE001
            log.debug("could not shut down the thumbnail pool", exc_info=True)

        # Closing a develop window does not stop its render thread; that
        # finishes at its own pace. If the interpreter exits first, a running
        # thread is destroyed and the process dies.
        try:
            from .loupe import wait_for_detached_renders

            wait_for_detached_renders()
        except Exception:  # noqa: BLE001
            log.debug("could not wait for outstanding renders", exc_info=True)

    def closeEvent(self, event) -> None:
        """Closing during analysis tidies the workers up on the way out."""
        self._shutdown_workers()
        super().closeEvent(event)


def configure_application(app: QApplication) -> str:
    """Everything that must happen before the first widget exists.

    Separate from `main` so it can be tested. `main` itself ends in
    `app.exec()`, which blocks on the event loop, so a test cannot call it —
    and checking the *source text* of `main` instead (which is what this
    replaced) breaks as soon as line numbers move.

    Returns the interface language actually in use.
    """
    app.setApplicationName(APP_NAME)

    # Load the translation **before** any widget exists. Captions are read as
    # each widget is constructed, so installing a translator afterwards leaves
    # everything already built showing the source English.
    language = i18n.install(app)
    log.info("interface language: %s", language)

    theme.apply_app_theme(app)
    return language


def main() -> int:
    import sys

    app = QApplication(sys.argv)
    configure_application(app)
    window = MainWindow()
    window.show()
    return app.exec()
