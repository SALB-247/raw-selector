"""내보내기 옵션 대화상자.

같은 셀렉트 결과라도 목적에 따라 필요한 파일이 다르다 — 인쇄용 풀사이즈,
SNS용 2048px, 클라이언트 확인용 저용량.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
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

from ..core.export_options import ExportFormat, ExportOptions, ResizeMode
from . import theme
from .i18n import tr

FORMAT_LABELS = {
    ExportFormat.JPEG: "JPEG (권장)",
    ExportFormat.PNG: "PNG (무손실, 용량 큼)",
    ExportFormat.WEBP: "WebP",
    ExportFormat.TIFF: "TIFF (무손실, 인쇄·재보정용)",
}
# HEIF/AVIF는 목록에 없습니다. 이 OpenCV 빌드에 인코더가 없어서
# 저장 자체가 실패합니다(실측). RAW 옆의 .HIF 원본을 그대로 옮기는 것은
# '함께 저장된 JPG/HIF/XMP도 내보내기'로 됩니다.

RESIZE_LABELS = {
    ResizeMode.NONE: "원본 크기",
    ResizeMode.LONG_EDGE: "긴 변 기준",
    ResizeMode.PERCENT: "비율",
}

LONG_EDGE_PRESETS = (
    (1080, "1080px · SNS 정사각/세로"),
    (1920, "1920px · FHD"),
    (2048, "2048px · 웹 게시용"),
    (2560, "2560px · QHD"),
    (3000, "3000px"),
    (3840, "3840px · 4K/UHD"),
    (4000, "4000px"),
    (6000, "6000px · 인화 대비"),
)
"""자주 쓰는 긴 변 크기.

매번 숫자를 손으로 치게 하면 2048을 2408로 잘못 넣는 일이 생깁니다.
직접 입력도 그대로 됩니다 — 프리셋은 거들 뿐입니다.
"""

NAME_TOKENS = (
    ("{name}", "원본 파일 이름"),
    ("{index}", "일련번호 (0001…)"),
    ("{grade}", "등급 (keep/review/reject)"),
    ("{date}", "촬영 날짜"),
    ("{time}", "촬영 시각"),
    ("{score}", "점수"),
)


# 콤보·토큰 라벨은 화면에 보이는 텍스트라 언어에 따라 달라집니다. 모듈 로드
# 시점에 tr()로 굳히면 언어 전환이 안 되므로, 값을 함수 안에 둡니다
# (gui/ordering_text.py와 같은 방식).


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
    """내보내기 직전에 옵션을 정합니다."""

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
        # (위치 정보가 있는 컷, 전체). 장소별 폴더가 의미가 있는지 알려 줍니다.
        self._located, self._total = located
        # 이 배치의 RAW 장수. None이면 모른다는 뜻이라 예전처럼 다 켭니다.
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

    # ------------------------------------------------------------ 구성

    def _build_files_group(self) -> QGroupBox:
        box = QGroupBox(tr("Files"))
        form = QFormLayout(box)

        # 어떤 등급을 내보낼지. 넘길 때는 keep만, 백업은 전부 — 목적마다 다릅니다.
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

        # RAW가 없어서 위 두 항목이 잠겼을 때 이유를 적는 자리입니다.
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

        resize_row = QHBoxLayout()
        self.resize_mode = QComboBox()
        for value in ResizeMode:
            self.resize_mode.addItem(_resize_label(value), value.value)
        self.resize_mode.currentIndexChanged.connect(self._on_resize_mode)
        resize_row.addWidget(self.resize_mode, 1)

        # 자주 쓰는 크기는 골라 쓰고, 그 밖의 값은 옆 칸에 직접 넣습니다.
        self.long_edge_preset = QComboBox()
        self.long_edge_preset.addItem(tr("Custom"), 0)
        for pixels, _ in LONG_EDGE_PRESETS:
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

        self._image_form = form
        self._image_fields = (self.image_format, self.quality, resize_row)
        self._image_widgets = (
            self.image_format,
            self.quality,
            self.resize_mode,
            self.long_edge_preset,
            self.resize_long_edge,
            self.resize_percent,
        )
        # 요약 갱신은 아직 못 합니다 — 파일 이름 위젯이 뒤에 만들어집니다
        self._sync_image_controls()
        return box

    def _build_naming_group(self) -> QGroupBox:
        box = QGroupBox(tr("Filename"))
        form = QFormLayout(box)

        self.pattern = QLineEdit()
        self.pattern.textChanged.connect(self._refresh_summary)
        form.addRow(tr("Pattern"), self.pattern)

        # 예전에는 쓸 수 있는 항목을 글자로만 적어 두었습니다. 중괄호까지
        # 정확히 옮겨 적어야 해서 오타가 나기 쉬웠습니다. 눌러서 넣습니다.
        tokens = QHBoxLayout()
        tokens.setSpacing(4)
        self.token_buttons = []
        for token, _ in NAME_TOKENS:
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
        """커서 자리에 항목을 넣습니다. 넣고 나서도 계속 칠 수 있어야 합니다."""
        self.pattern.setFocus()
        self.pattern.insert(token)
        self._refresh_summary()

    # ------------------------------------------------------------ 동작

    def _on_apply_develop(self) -> None:
        self._sync_image_controls()
        self._refresh_summary()

    def _sync_image_controls(self) -> None:
        """보정본을 안 만들면 형식·품질·크기는 쓰이는 데가 없습니다.

        그대로 활성 상태로 두면 여기서 고른 JPEG 품질이 내보낼 RAW에도
        적용되는 줄 알게 됩니다. 실제로는 원본이 그대로 복사될 뿐입니다.
        """
        on = self.apply_develop.isChecked()
        for widget in self._image_widgets:
            widget.setEnabled(on)
        for field in self._image_fields:
            label = self._image_form.labelForField(field)
            if label is not None:
                label.setEnabled(on)

    def _on_resize_mode(self) -> None:
        mode = self.resize_mode.currentData()
        long_edge = mode == ResizeMode.LONG_EDGE.value
        self.long_edge_preset.setVisible(long_edge)
        self.resize_long_edge.setVisible(long_edge)
        self.resize_percent.setVisible(mode == ResizeMode.PERCENT.value)
        self._refresh_summary()

    def _on_long_edge_preset(self) -> None:
        """프리셋을 고르면 숫자 칸에 그대로 넣습니다."""
        pixels = self.long_edge_preset.currentData()
        if pixels:
            self.resize_long_edge.setValue(int(pixels))
        self._refresh_summary()

    def _on_long_edge_value(self) -> None:
        """직접 친 값이 프리셋과 다르면 '직접 입력'으로 되돌립니다.

        3000 프리셋을 고른 뒤 2999로 고쳤는데 콤보가 계속 3000을 가리키면
        어느 쪽이 실제 값인지 알 수 없습니다.
        """
        index = self.long_edge_preset.findData(self.resize_long_edge.value())
        self.long_edge_preset.blockSignals(True)
        self.long_edge_preset.setCurrentIndex(index if index >= 0 else 0)
        self.long_edge_preset.blockSignals(False)
        self._refresh_summary()

    def _sync_subfolder(self) -> None:
        """등급을 하나만 내보내면 등급별로 나눌 것이 없습니다.

        _keep 폴더 하나만 만들어 놓고 그 안에 전부 넣는 꼴이라, 폴더가 한 겹
        늘 뿐 아무것도 나뉘지 않습니다.
        """
        chosen = [v for v, c in self.grade_checks.items() if c.isChecked()]
        # 아무것도 안 고르면 전체(3등급)가 나갑니다 — 그때는 나눌 이유가 있습니다
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
        """RAW가 한 장도 없으면 RAW 전용 옵션을 잠급니다.

        JPEG·HIF만 있는 배치에서는 '원본 RAW도 함께'와 '짝 파일도'가 할 일이
        없습니다. 켤 수 있게 두면 켜 놓고 결과가 그대로인 것을 보며 이유를
        찾게 됩니다 — 조용히 무시하는 대신 왜 못 쓰는지 적어 둡니다.

        섞여 있으면(RAW + JPEG) 잠그지 않습니다. RAW 쪽에는 여전히 필요하고,
        JPEG 쪽은 export가 파일 단위로 알아서 건너뜁니다.
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
        """위치 정보가 있는 컷이 없으면 장소로 나눌 것이 없습니다.

        켤 수는 있게 두되 몇 장에 위치가 있는지 알려 줍니다. 바디에 GPS가
        없으면 아예 안 들어가는데(실측: A6700 300장 중 0장), 그것도 모르고
        켜면 `_위치없음` 폴더 하나만 더 생깁니다.
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
        """무엇이 나갈지 한 줄로 알려 줍니다. 옵션 조합을 헷갈리기 쉽습니다."""
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
            # RAW가 없는 배치에서 '원본 RAW 제외'는 틀린 말은 아니지만
            # 무언가를 빼고 있다는 뜻으로 읽힙니다. 뺄 것이 없습니다.
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

        index = self.resize_mode.findData(options.resize_mode.value)
        if index >= 0:
            self.resize_mode.setCurrentIndex(index)
        self.resize_long_edge.setValue(options.resize_long_edge)
        self.resize_percent.setValue(options.resize_percent)
        self._on_long_edge_value()   # 저장값이 프리셋에 있으면 그걸 가리킵니다
        self._on_resize_mode()
        # setChecked는 값이 그대로면 toggled를 안 쏘므로 직접 맞춥니다
        self._sync_image_controls()

        self.pattern.setText(options.filename_pattern)

    def result_options(self) -> ExportOptions:
        return ExportOptions(
            grades=tuple(v for v, c in self.grade_checks.items() if c.isChecked()),
            move=self.move_files.isChecked(),
            include_companions=self.include_companions.isChecked(),
            apply_develop=self.apply_develop.isChecked(),
            copy_raw=self.copy_raw.isChecked(),
            image_format=ExportFormat(self.image_format.currentData()),
            quality=self.quality.value(),
            resize_mode=ResizeMode(self.resize_mode.currentData()),
            resize_long_edge=self.resize_long_edge.value(),
            resize_percent=self.resize_percent.value(),
            filename_pattern=self.pattern.text() or "{name}",
            subfolder_by_grade=self.subfolder.isChecked(),
            subfolder_by_place=self.subfolder_place.isChecked(),
        )
