"""Export options dialog.

The same culling result needs different files depending on the purpose -
full size for print, 2048px for social, low weight for a client check.
"""

from __future__ import annotations

from pathlib import Path

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog, QMessageBox
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ..core.export_options import (
    ExportColorSpace,
    ExportFormat,
    ExportOptions,
    ResizeMode,
)
from ..core import presets as presets_module
from . import theme
from .i18n import tr


def export_presets():
    """The store, behind a module attribute so tests can point it at a
    temporary folder."""
    return presets_module.export_presets()

# HEIF/AVIF are not in the list. This OpenCV build has no encoder for them,
# so saving fails outright (measured). Moving the .HIF original that sits
# beside the RAW as-is is handled by 'also export bundled JPG/HIF/XMP'.

LONG_EDGE_PRESETS = (1080, 1920, 2048, 2560, 3000, 3840, 4000, 6000)
"""Long-edge sizes that come up often.

Making you type the number by hand every time leads to entering 2048 as
2408. Typing a value directly still works - the presets only help along.
"""

NAME_TOKENS = ("{name}", "{index}", "{grade}", "{date}", "{time}",
               "{score}")
"""Tokens the file-name template accepts. What each one means is shown by
_name_token_description(), which is translated - the descriptions used to
sit in this tuple and went stale there, unread."""


# The combo and token labels are on-screen text, so they vary by language.
# Freezing them with tr() at module load time stops language switching, so
# the values live inside functions (the same way as gui/ordering_text.py).


def _format_label(fmt: ExportFormat) -> str:
    return {
        ExportFormat.JPEG: tr("JPEG (recommended)"),
        ExportFormat.PNG: tr("PNG (lossless, large)"),
        ExportFormat.WEBP: "WebP",
        ExportFormat.TIFF: tr("TIFF (lossless, for print/re-edit)"),
    }.get(fmt, str(fmt))


def _resize_label(mode: ResizeMode) -> str:
    return {
        ResizeMode.NONE: tr("Original size"),
        ResizeMode.LONG_EDGE: tr("By long edge"),
        ResizeMode.PERCENT: tr("Percentage"),
    }.get(mode, str(mode))


def _long_edge_preset_label(pixels: int) -> str:
    return {
        1080: tr("1080px · square/portrait social"),
        1920: tr("1920px · FHD"),
        2048: tr("2048px · web"),
        2560: tr("2560px · QHD"),
        3840: tr("3840px · 4K/UHD"),
        6000: tr("6000px · for print"),
    }.get(pixels, f"{pixels}px")


def _name_token_description(token: str) -> str:
    return {
        "{name}": tr("Original filename"),
        "{index}": tr("Sequence number (0001…)"),
        "{grade}": tr("Grade (keep/review/reject)"),
        "{date}": tr("Capture date"),
        "{time}": tr("Capture time"),
        "{score}": tr("Score"),
    }.get(token, "")


class ExportDialog(QDialog):
    """Settles the options right before exporting."""

    def __init__(
        self,
        destination: Path,
        summary: dict[str, int],
        develop_count: int,
        options: ExportOptions | None = None,
        parent=None,
        located: tuple[int, int] = (0, 0),
        raw_count: int | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("Export options"))
        self.setMinimumWidth(460)
        self.options = options or ExportOptions()
        # (shots that have location info, total). Tells you whether
        # per-location folders mean anything.
        self._located, self._total = located
        # How many RAW files are in this batch. None means we do not know,
        # so everything stays on as it did before.
        self._raw_count = raw_count
        self.setStyleSheet(theme.dialog_style() + theme.GROUP_BOX)

        layout = QVBoxLayout(self)

        header = QLabel(
            f"<b>{destination}</b><br>"
            f"keep {summary.get('keep', 0)} · review {summary.get('review', 0)} · "
            f"reject {summary.get('reject', 0)}"
            + (tr("<br>{count} developed shots").format(count=develop_count)
               if develop_count else "")
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        layout.addLayout(self._build_preset_row())
        layout.addWidget(self._build_files_group())
        layout.addWidget(self._build_image_group())
        layout.addWidget(self._build_naming_group())

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet(theme.hint_label(theme.TEXT_DIM))
        layout.addWidget(self.summary_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, Qt.Horizontal
        )
        buttons.button(QDialogButtonBox.Ok).setText(tr("Export"))
        buttons.button(QDialogButtonBox.Cancel).setText(tr("Cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load()
        self._refresh_summary()

    # ------------------------------------------------------------ Presets

    def _build_preset_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Preset")))
        self._store = export_presets()
        self.preset_combo = QComboBox()
        self.preset_combo.addItem(tr("(choose)"), "")
        for info in self._store.list():
            self.preset_combo.addItem(info.name, info.name)
        self.preset_combo.setToolTip(tr(
            "Saved export settings — format, size, naming and folders.\n"
            "Choosing one fills the dialog; which grades to export stays as set here."))
        self.preset_combo.currentIndexChanged.connect(self._on_preset_chosen)
        row.addWidget(self.preset_combo, 1)
        self.preset_save = QPushButton(tr("Save…"))
        self.preset_save.setToolTip(tr("Save the settings below under a name"))
        self.preset_save.clicked.connect(self._save_preset)
        row.addWidget(self.preset_save)
        self.preset_delete = QPushButton(tr("Delete"))
        self.preset_delete.setEnabled(False)
        self.preset_delete.clicked.connect(self._delete_preset)
        row.addWidget(self.preset_delete)
        return row

    def _on_preset_chosen(self, _index: int = 0) -> None:
        name = self.preset_combo.currentData()
        self.preset_delete.setEnabled(bool(name))
        if not name:
            return
        try:
            data = self._store.load(name)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, tr("Export preset"), str(exc))
            return
        # The grades are about this batch, not the preset
        self.options = replace(ExportOptions.from_dict(data), grades=self.result_options().grades)
        self._load()
        self._refresh_summary()

    def save_preset_named(self, name: str) -> None:
        name = name.strip()
        if not name:
            return
        self._store.save(name, self.result_options().to_dict())
        if self.preset_combo.findData(name) < 0:
            self.preset_combo.addItem(name, name)
        self.preset_combo.setCurrentIndex(self.preset_combo.findData(name))

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, tr("Save export preset"), tr("Name"))
        if ok:
            self.save_preset_named(name)

    def _delete_preset(self) -> None:
        name = self.preset_combo.currentData()
        if not name:
            return
        answer = QMessageBox.question(
            self, tr("Export preset"),
            tr("Delete the export preset \"{name}\"?").format(name=name))
        if answer == QMessageBox.Yes:
            self.delete_preset_named(name)

    def delete_preset_named(self, name: str) -> None:
        self._store.delete(name)
        index = self.preset_combo.findData(name)
        if index >= 0:
            self.preset_combo.removeItem(index)
        self.preset_combo.setCurrentIndex(0)

    # ------------------------------------------------------------ Build

    def _build_files_group(self) -> QGroupBox:
        box = QGroupBox(tr("Files"))
        form = QFormLayout(box)

        # Which grades to export. keep only when handing over, all of them
        # for a backup - it differs with the purpose.
        grade_row = QHBoxLayout()
        grade_row.setSpacing(10)
        self.grade_checks: dict[str, QCheckBox] = {}
        for value, label in (("keep", "keep"), ("review", "review"), ("reject", "reject")):
            check = QCheckBox(label)
            check.toggled.connect(self._refresh_summary)
            self.grade_checks[value] = check
            grade_row.addWidget(check)
        grade_row.addStretch(1)
        form.addRow(tr("Grades to export"), grade_row)

        self.copy_raw = QCheckBox(tr("Also export the original RAW"))
        self.copy_raw.setToolTip(tr(
            "When off, only the developed images are exported. "
            "Copying the originals too doubles the size."
        ))
        self.copy_raw.toggled.connect(self._refresh_summary)
        form.addRow(self.copy_raw)

        self.include_companions = QCheckBox(tr("Also export bundled JPG/HIF/XMP"))
        self.include_companions.setToolTip(tr(
            "When shot as RAW+JPEG or RAW+HEIF, the same-named companion file\n"
            "is moved along with it.\n"
            "When off, only the RAW is exported."
        ))
        self.include_companions.toggled.connect(self._refresh_summary)
        form.addRow(self.include_companions)

        # The place where the reason goes when the two items above are
        # locked because there is no RAW.
        self.raw_note = QLabel()
        self.raw_note.setStyleSheet(theme.hint_label())
        self.raw_note.setWordWrap(True)
        self.raw_note.setVisible(False)
        form.addRow(self.raw_note)

        self.subfolder = QCheckBox(
            tr("Split into folders by grade (_keep / _review / _reject)"))
        self.subfolder.toggled.connect(self._refresh_summary)
        form.addRow(self.subfolder)

        self.subfolder_place = QCheckBox(tr("Split into folders by location (GPS)"))
        self.subfolder_place.toggled.connect(self._refresh_summary)
        form.addRow(self.subfolder_place)

        self.move_files = QCheckBox(tr("Move instead of copy"))
        self.move_files.setToolTip(tr(
            "The originals disappear from their original location. "
            "Recoverable with undo."))
        self.move_files.toggled.connect(self._refresh_summary)
        form.addRow(self.move_files)

        return box

    def _build_image_group(self) -> QGroupBox:
        box = QGroupBox(tr("Developed images"))
        form = QFormLayout(box)

        self.apply_develop = QCheckBox(tr("Render developed images"))
        self.apply_develop.toggled.connect(self._on_apply_develop)
        form.addRow(self.apply_develop)

        self.image_format = QComboBox()
        for value in ExportFormat:
            self.image_format.addItem(_format_label(value), value.value)
        self.image_format.currentIndexChanged.connect(self._refresh_summary)
        form.addRow(tr("Format"), self.image_format)

        self.quality = QSpinBox()
        self.quality.setRange(1, 100)
        self.quality.setSuffix(" %")
        form.addRow(tr("Quality"), self.quality)

        # Bit depth - only PNG and TIFF take 16. It follows along the moment
        # the format changes (if 16 is left behind while locked, a file
        # different from the one asked for goes out).
        self.bit_depth = QComboBox()
        self.bit_depth.addItem(tr("8-bit"), 8)
        self.bit_depth.addItem(tr("16-bit"), 16)
        self.bit_depth.currentIndexChanged.connect(self._refresh_summary)
        form.addRow(tr("Bit depth"), self.bit_depth)

        self.color_space = QComboBox()
        for value in ExportColorSpace:
            self.color_space.addItem(value.label, value.value)
        self.color_space.currentIndexChanged.connect(self._refresh_summary)
        form.addRow(tr("Colour space"), self.color_space)

        self.image_format.currentIndexChanged.connect(self._sync_bit_depth)
        self.image_format.currentIndexChanged.connect(self._sync_color_space)

        resize_row = QHBoxLayout()
        self.resize_mode = QComboBox()
        for value in ResizeMode:
            self.resize_mode.addItem(_resize_label(value), value.value)
        self.resize_mode.currentIndexChanged.connect(self._on_resize_mode)
        resize_row.addWidget(self.resize_mode, 1)

        # Sizes that come up often are picked from the list, any other value
        # is typed straight into the box beside it.
        self.long_edge_preset = QComboBox()
        self.long_edge_preset.addItem(tr("Custom"), 0)
        for pixels in LONG_EDGE_PRESETS:
            self.long_edge_preset.addItem(_long_edge_preset_label(pixels), pixels)
        self.long_edge_preset.currentIndexChanged.connect(self._on_long_edge_preset)
        resize_row.addWidget(self.long_edge_preset, 1)

        self.resize_long_edge = QSpinBox()
        self.resize_long_edge.setRange(64, 20000)
        self.resize_long_edge.setSuffix(" px")
        self.resize_long_edge.valueChanged.connect(self._on_long_edge_value)
        resize_row.addWidget(self.resize_long_edge)

        self.resize_percent = QSpinBox()
        self.resize_percent.setRange(5, 100)
        self.resize_percent.setSuffix(" %")
        self.resize_percent.valueChanged.connect(self._refresh_summary)
        resize_row.addWidget(self.resize_percent)
        form.addRow(tr("Size"), resize_row)

        self.render_workers = QComboBox()
        self.render_workers.addItem(tr("Auto"), 0)
        for count in (1, 2, 3, 4):
            self.render_workers.addItem(str(count), count)
        self.render_workers.setToolTip(tr(
            "How many photos are developed at once.\n"
            "Auto counts on about 4 GB of free memory per photo for a plain develop\n"
            "and up to 9 GB with a lens profile or noise reduction (50 MP), so a laptop\n"
            "usually gets 1 and a workstation 3 or 4. Pin a number only when you know\n"
            "the memory is there — two renders that do not fit swap, and end up slower\n"
            "than one."
        ))
        self.apply_develop.toggled.connect(self.render_workers.setEnabled)
        self.render_workers.setEnabled(self.apply_develop.isChecked())
        form.addRow(tr("Parallel rendering"), self.render_workers)

        self._image_form = form
        self._image_fields = (self.image_format, self.quality, resize_row)
        self._image_widgets = (
            self.image_format,
            self.quality,
            self.bit_depth,
            self.color_space,
            self.resize_mode,
            self.long_edge_preset,
            self.resize_long_edge,
            self.resize_percent,
        )
        # Cannot refresh the summary yet - the filename widgets come later
        self._sync_image_controls()
        return box

    def _build_naming_group(self) -> QGroupBox:
        box = QGroupBox(tr("Filename"))
        form = QFormLayout(box)

        self.pattern = QLineEdit()
        self.pattern.textChanged.connect(self._refresh_summary)
        form.addRow(tr("Pattern"), self.pattern)

        # It used to write out the available items as text only. You had to
        # copy them exactly, braces and all, so typos were easy. You press
        # to insert them.
        tokens = QHBoxLayout()
        tokens.setSpacing(4)
        self.token_buttons = []
        for token in NAME_TOKENS:
            button = QPushButton(token)
            button.setToolTip(
                tr("{description} — press to insert into the pattern").format(
                    description=_name_token_description(token)))
            button.setStyleSheet(theme.TOKEN_BUTTON)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, t=token: self._insert_token(t))
            self.token_buttons.append(button)
            tokens.addWidget(button)
        tokens.addStretch(1)
        form.addRow("", tokens)

        hint = QLabel(tr("Press an item to drop it into the pattern field"))
        hint.setStyleSheet(theme.hint_label())
        form.addRow("", hint)

        return box

    def _insert_token(self, token: str) -> None:
        """Inserts the item at the cursor. Typing has to carry on after it."""
        self.pattern.setFocus()
        self.pattern.insert(token)
        self._refresh_summary()

    # ------------------------------------------------------------ Behaviour

    def _on_apply_develop(self) -> None:
        self._sync_image_controls()
        self._refresh_summary()

    def _sync_image_controls(self) -> None:
        """With no developed images rendered, format/quality/size go unused.

        Leaving them enabled makes you think the JPEG quality picked here
        also applies to the RAW being exported. In fact the original is
        only copied as-is.
        """
        on = self.apply_develop.isChecked()
        for widget in self._image_widgets:
            widget.setEnabled(on)
        for field in self._image_fields:
            label = self._image_form.labelForField(field)
            if label is not None:
                label.setEnabled(on)
        # Depth and colour space hang off the format once more, so they come
        # after the blanket enable above.
        self._sync_bit_depth()
        self._sync_color_space()

    def _sync_color_space(self) -> None:
        """Locks it and falls back to sRGB for formats that cannot hold ICC.

        Converting without being able to attach the tag means the viewer
        reads it as sRGB and **a file with the colours off** goes out.
        Rather than that, it is right not to convert at all.
        """
        fmt = ExportFormat(self.image_format.currentData())
        self.color_space.setEnabled(
            fmt.supports_icc and self.apply_develop.isChecked())
        if not fmt.supports_icc:
            index = self.color_space.findData(ExportColorSpace.SRGB.value)
            if index >= 0:
                self.color_space.setCurrentIndex(index)
            self.color_space.setToolTip(tr(
                "{fmt} cannot carry a colour profile here, so it stays sRGB."
            ).format(fmt=fmt.suffix))
        else:
            self.color_space.setToolTip(tr(
                "Converts the pixels and embeds the matching profile.\n"
                "sRGB is what screens and social sites expect.\n"
                "Adobe RGB holds more greens and cyans for print — but\n"
                "viewers without colour management show it washed out."))

    def _sync_bit_depth(self) -> None:
        """Locks it and falls back to 8-bit when the format cannot take 16.

        Locking it and leaving the value alone leaves 16 behind while locked
        and a file different from the one asked for goes out - cv2 only
        leaves a warning and drops to 8-bit (a silent failure). When it
        locks, it says why in the tooltip.
        """
        fmt = ExportFormat(self.image_format.currentData())
        allowed = fmt.supports_16bit and self.apply_develop.isChecked()
        self.bit_depth.setEnabled(allowed)
        if not fmt.supports_16bit:
            index = self.bit_depth.findData(8)
            if index >= 0:
                self.bit_depth.setCurrentIndex(index)
            self.bit_depth.setToolTip(tr(
                "{fmt} files are 8-bit only. PNG and TIFF can hold 16-bit."
            ).format(fmt=fmt.suffix))
        else:
            self.bit_depth.setToolTip(tr(
                "16-bit keeps the tonal steps the develop pipeline actually\n"
                "carries — worth it when the file goes on to more editing.\n"
                "Files are roughly twice the size."))

    def _on_resize_mode(self) -> None:
        mode = self.resize_mode.currentData()
        long_edge = mode == ResizeMode.LONG_EDGE.value
        self.long_edge_preset.setVisible(long_edge)
        self.resize_long_edge.setVisible(long_edge)
        self.resize_percent.setVisible(mode == ResizeMode.PERCENT.value)
        self._refresh_summary()

    def _on_long_edge_preset(self) -> None:
        """Picking a preset drops that number straight into the box."""
        pixels = self.long_edge_preset.currentData()
        if pixels:
            self.resize_long_edge.setValue(int(pixels))
        self._refresh_summary()

    def _on_long_edge_value(self) -> None:
        """Falls back to 'Custom' when the typed value differs from a preset.

        Pick the 3000 preset, change it to 2999, and if the combo still
        points at 3000 there is no telling which of the two is the real
        value.
        """
        index = self.long_edge_preset.findData(self.resize_long_edge.value())
        self.long_edge_preset.blockSignals(True)
        self.long_edge_preset.setCurrentIndex(index if index >= 0 else 0)
        self.long_edge_preset.blockSignals(False)
        self._refresh_summary()

    def _sync_subfolder(self) -> None:
        """With only one grade exported there is nothing to split by grade.

        It amounts to making a single _keep folder and putting everything in
        it - one more layer of folder and nothing actually split.
        """
        chosen = [v for v, c in self.grade_checks.items() if c.isChecked()]
        # With nothing selected everything (all 3 grades) goes out - and then
        # there is a reason to split
        single = len(chosen) == 1
        self.subfolder.setEnabled(not single)
        if single:
            self.subfolder.setToolTip(
                tr("Only '{grade}' is being exported, so there are no grades "
                   "to split").format(grade=chosen[0]))
        else:
            self.subfolder.setToolTip(tr(
                "Creates a _keep / _review / _reject folder for each grade "
                "to split them"))

    def _sync_raw_options(self) -> None:
        """Locks the RAW-only options when there is not a single RAW.

        In a batch of JPEG/HIF only, 'also export the original RAW' and 'also
        the companion files' have nothing to do. Leaving them switchable
        means you turn them on, see the result unchanged, and go hunting for
        the reason - rather than ignoring them silently, we write down why
        they cannot be used.

        When they are mixed (RAW + JPEG) it does not lock. The RAW side still
        needs them, and on the JPEG side export skips per file on its own.
        """
        if self._raw_count is None or self._raw_count > 0:
            self.raw_note.setVisible(False)
            return

        for widget in (self.copy_raw, self.include_companions):
            widget.setChecked(False)
            widget.setEnabled(False)
        self.raw_note.setText(tr(
            "This batch has no RAW (JPEG·HIF only). With no originals to keep "
            "and no companion files, the two options above cannot be used — "
            "developed shots are exported as their rendered image, and shots "
            "that were not developed are exported as-is."
        ))
        self.raw_note.setVisible(True)

    def _sync_place_option(self) -> None:
        """With no shots carrying location, there is nothing to split by place.

        It stays switchable, but says how many shots have a location. With no
        GPS in the body nothing is recorded at all (measured: 0 of 300 shots
        on the A6700), and turning it on without knowing that only adds one
        more no-location folder (core/export.py NO_PLACE_FOLDER).
        """
        if self._located:
            self.subfolder_place.setToolTip(
                tr("Groups the {located}/{total} shots that have location info\n"
                   "by nearby coordinates and splits them into folders.\n"
                   "Shots without location go to the _위치없음 folder.").format(
                       located=self._located, total=self._total)
            )
        else:
            self.subfolder_place.setToolTip(tr(
                "This batch has no shots with location info.\n"
                "If the camera body has no GPS, you have to shoot linked to a\n"
                "phone for it to be recorded.\n"
                "Turn it on now and everything goes into the _위치없음 folder."
            ))

    def _refresh_summary(self) -> None:
        """Says in one line what goes out. Option combos confuse easily."""
        self._sync_subfolder()
        self._sync_place_option()
        self._sync_raw_options()

        parts = []
        chosen = [v for v, c in self.grade_checks.items() if c.isChecked()]
        parts.append(tr("Grades ") + (
            "+".join(chosen) if chosen else tr("none selected → all")))
        if self.apply_develop.isChecked():
            fmt = ExportFormat(self.image_format.currentData()).suffix
            parts.append(tr("developed shots rendered as {fmt}").format(fmt=fmt))
            mode = self.resize_mode.currentData()
            if mode == ResizeMode.LONG_EDGE.value:
                parts.append(
                    tr("long edge {px}px").format(px=self.resize_long_edge.value()))
            elif mode == ResizeMode.PERCENT.value:
                parts.append(
                    tr("{pct}% size").format(pct=self.resize_percent.value()))
        if self._raw_count == 0:
            # In a batch with no RAW, 'Original RAW excluded' is not wrong,
            # but it reads as though something is being left out. There is
            # nothing to leave out.
            pass
        elif self.copy_raw.isChecked() or self.move_files.isChecked():
            parts.append(tr("Original RAW included"))
        else:
            parts.append(tr("Original RAW excluded"))
        if self.include_companions.isChecked():
            parts.append(tr("Companion files (JPG/HIF/XMP) included"))
        if not self.subfolder.isChecked():
            parts.append(tr("collected in one folder"))
        if self.move_files.isChecked():
            parts.append(tr("move (originals disappear)"))

        example = self.pattern.text() or "{name}"
        for token, value in (("{name}", "DSC001"), ("{index}", "0001"),
                             ("{grade}", "keep"), ("{date}", "20260722"),
                             ("{time}", "142530"), ("{score}", "88")):
            example = example.replace(token, value)
        suffix = (ExportFormat(self.image_format.currentData()).suffix
                  if self.apply_develop.isChecked() else "")
        parts.append(
            tr("e.g. {example}{suffix}").format(example=example, suffix=suffix))
        self.summary_label.setText(" · ".join(parts))

    def _load(self) -> None:
        options = self.options
        for value, check in self.grade_checks.items():
            check.setChecked(value in options.grades)
        self.copy_raw.setChecked(options.copy_raw)
        self.include_companions.setChecked(options.include_companions)
        self.subfolder.setChecked(options.subfolder_by_grade)
        self.subfolder_place.setChecked(options.subfolder_by_place)
        self._sync_place_option()
        self._sync_raw_options()
        self.move_files.setChecked(options.move)
        self.apply_develop.setChecked(options.apply_develop)

        index = self.image_format.findData(options.image_format.value)
        if index >= 0:
            self.image_format.setCurrentIndex(index)
        self.quality.setValue(options.quality)
        index = self.bit_depth.findData(options.bit_depth)
        if index >= 0:
            self.bit_depth.setCurrentIndex(index)
        index = self.color_space.findData(options.color_space.value)
        if index >= 0:
            self.color_space.setCurrentIndex(index)

        index = self.resize_mode.findData(options.resize_mode.value)
        if index >= 0:
            self.resize_mode.setCurrentIndex(index)
        self.resize_long_edge.setValue(options.resize_long_edge)
        self.resize_percent.setValue(options.resize_percent)
        self._on_long_edge_value()   # points at the preset if the value is one
        self._on_resize_mode()
        # setChecked fires no toggled if the value is unchanged, so sync here
        self._sync_image_controls()

        self.pattern.setText(options.filename_pattern)
        index = self.render_workers.findData(int(getattr(options, "render_workers", 0) or 0))
        self.render_workers.setCurrentIndex(max(0, index))

    def result_options(self) -> ExportOptions:
        return ExportOptions(
            grades=tuple(v for v, c in self.grade_checks.items() if c.isChecked()),
            move=self.move_files.isChecked(),
            include_companions=self.include_companions.isChecked(),
            apply_develop=self.apply_develop.isChecked(),
            copy_raw=self.copy_raw.isChecked(),
            image_format=ExportFormat(self.image_format.currentData()),
            quality=self.quality.value(),
            bit_depth=int(self.bit_depth.currentData() or 8),
            color_space=ExportColorSpace(
                self.color_space.currentData() or ExportColorSpace.SRGB.value),
            resize_mode=ResizeMode(self.resize_mode.currentData()),
            resize_long_edge=self.resize_long_edge.value(),
            resize_percent=self.resize_percent.value(),
            filename_pattern=self.pattern.text() or "{name}",
            render_workers=int(self.render_workers.currentData() or 0),
            subfolder_by_grade=self.subfolder.isChecked(),
            subfolder_by_place=self.subfolder_place.isChecked(),
        )
