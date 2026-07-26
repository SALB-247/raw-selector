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
        # 채점표 오른쪽 여백에 앉습니다 — 폭을 스스로 최소로 유지해야
        # 근거 문구 열(stretch 1)이 남는 공간을 가져갑니다.
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
        # 소니는 Model에 제조사가 없고("ILCE-6700"), 캐논은 이미 들어
        # 있습니다("Canon EOS R5") — 중복으로 붙이지 않습니다.
        if body and make and not body.upper().startswith(make.split()[0].upper()):
            body = f"{make} {body}"
        if body:
            rows.append((tr("Camera"), body))

        if metadata.lens_model:
            rows.append((tr("Lens"), metadata.lens_model))

        if metadata.focal_length:
            text = f"{metadata.focal_length:g} mm"
            # 풀프레임은 환산이 곧 실초점이라 표기가 소음입니다. 캐논은
            # 역산이라 ±0.3% 오차(400→399)가 나므로, 2% 넘게 다를 때만
            # 환산을 보여 줍니다 — 크롭 바디(1.5×)는 확실히 걸립니다.
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
            # 소수 3자리(≈110m) — 장소 폴더 이름(4자리)보다도 덜 정밀합니다.
            # 위치는 화면 표시까지만이고 내보내는 파일에는 절대 안 씁니다.
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
