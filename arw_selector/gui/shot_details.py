"""Shot details — facts about the photo that the score did not use.

The score card explains *why the number is what it is*; this panel answers
the photographer's next question — *what was this shot?* Body, lens, focal
length, exposure, AF area mode, and (rarely present) location. Every value
comes straight from ``record.metadata`` which is already in the analysis
cache, so showing it costs no file I/O.

Empty fields are simply omitted rather than shown as dashes: 9% of camera
JPEGs in the field have body/ISO stripped by re-saving tools, and a column
of "—" reads as breakage. AF mode strings stay in English on purpose —
they are camera terms, like lens names, and translating them would only
make manuals harder to cross-reference.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QLabel, QSizePolicy, QWidget

from ..core.types import ImageRecord
from . import theme
from .i18n import tr


class ShotDetails(QWidget):
    """Two-column fact sheet for one photo. Hides itself when empty."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # It sits in the margin to the right of the score card - it has to
        # keep its own width minimal so the reason-text column (stretch 1)
        # takes the space that is left.
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(2)
        self.setVisible(False)

    # ------------------------------------------------------------ display

    def show_record(self, record: ImageRecord | None) -> None:
        self._clear()
        metadata = record.metadata if record is not None else None
        if metadata is None:
            self.setVisible(False)
            return

        rows: list[tuple[str, str]] = []

        if metadata.capture_time is not None:
            rows.append((tr("Captured"),
                         metadata.capture_time.strftime("%Y-%m-%d %H:%M:%S")))

        body = (metadata.camera_model or "").strip()
        make = (metadata.camera_make or "").strip()
        # Sony has no maker in Model ("ILCE-6700"), Canon already has it in
        # there ("Canon EOS R5") - do not attach it twice.
        if body and make and not body.upper().startswith(make.split()[0].upper()):
            body = f"{make} {body}"
        if body:
            rows.append((tr("Camera"), body))

        if metadata.lens_model:
            rows.append((tr("Lens"), metadata.lens_model))

        if metadata.focal_length:
            text = f"{metadata.focal_length:g} mm"
            # On full frame the equivalent is the real focal length, so
            # printing it is noise. Canon works it out backwards, giving a
            # ±0.3% error (400 -> 399), so the equivalent is only shown when
            # they differ by more than 2% - a crop body (1.5x) is caught
            # for certain.
            equiv = metadata.focal_length_35mm
            if equiv and abs(equiv - metadata.focal_length) > 0.02 * equiv:
                text += " · " + tr("{eq:g} mm equiv.").format(eq=equiv)
            rows.append((tr("Focal length"), text))

        exposure: list[str] = []
        if metadata.aperture:
            exposure.append(f"f/{metadata.aperture:g}")
        if metadata.shutter_speed:
            exposure.append(metadata.shutter_display)
        if metadata.iso:
            exposure.append(f"ISO {metadata.iso}")
        if exposure:
            rows.append((tr("Exposure"), "  ".join(exposure)))

        if metadata.af_area_mode:
            rows.append((tr("AF area"), metadata.af_area_mode))

        if metadata.has_location:
            ns = "N" if metadata.latitude >= 0 else "S"
            ew = "E" if metadata.longitude >= 0 else "W"
            # 3 decimal places (~110m) - less precise even than the place
            # folder name (4 places). Location goes no further than the
            # screen and is never written into an exported file.
            rows.append((tr("Location"),
                         f"{abs(metadata.latitude):.3f}{ns} "
                         f"{abs(metadata.longitude):.3f}{ew}"))

        if not rows:
            self.setVisible(False)
            return

        for row, (label, value) in enumerate(rows):
            name = QLabel(label)
            name.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            name.setStyleSheet(theme.hint_label())
            self._grid.addWidget(name, row, 0)

            text = QLabel(value)
            text.setStyleSheet(f"color: {theme.TEXT};")
            text.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self._grid.addWidget(text, row, 1)

        self.setVisible(True)

    # ------------------------------------------------------------ internals

    def _clear(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
