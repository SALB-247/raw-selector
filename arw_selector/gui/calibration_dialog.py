"""Colour calibration for a new camera model - the notice and the progress.

The adjustment values are worked out on this PC, using the JPEGs the camera
produced as the answer key. It takes time (around 1 second per shot) and
the result affects every develop from then on, so we say what is about to
happen first and get consent.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QProgressBar,
    QVBoxLayout,
)

from ..core.develop import calibration as calib
from . import theme
from .i18n import tr


class CalibrationWorker(QThread):
    """Measuring takes a few to tens of seconds. The window must not stall."""

    progressed = Signal(int, int)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, paths: list[Path], camera: str, key: str = "", parent=None):
        super().__init__(parent)
        self._paths = paths
        self._camera = camera
        # The save key. Rebuilding it from the name puts it out of step with
        # what reading uses (see calibration.key).
        self._key = key
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:  # noqa: D102
        from .. import __version__

        try:
            result = calib.measure(
                self._paths, self._camera,
                progress=lambda done, total: self.progressed.emit(done, total),
                should_cancel=lambda: self._cancelled,
                app_version=__version__,
                key=self._key,
            )
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        if result is None:
            self.failed.emit(tr(
                "Not enough usable samples. Try again from a folder"
                " that contains more photos of this camera model."
            ))
            return
        self.finished_ok.emit(result)


def ask_to_calibrate(
    parent, need: "calib.CalibrationNeed", manual: bool = False
) -> bool:
    """Asks whether to go ahead. Explains what and why beforehand."""
    existing = calib.load(need.key)
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)

    if manual:
        box.setWindowTitle(tr("Compute color calibration on this PC"))
        box.setText(
            tr("Compute the color calibration for <b>{camera}</b> yourself.")
            .format(camera=need.camera)
        )
        head = tr(
            "Even if the library already knows this model, the values"
            " measured on this PC will take priority.\n\n"
        )
        if existing is not None:
            head += tr(
                "A calibration is already saved — recomputing overwrites it.\n\n"
            )
    else:
        box.setWindowTitle(tr("New camera model"))
        box.setText(
            tr("The library does not yet know the color of <b>{camera}</b>.")
            .format(camera=need.camera)
        )
        head = tr(
            "Left as is, the developed colors may come out different from"
            " the picture the camera produced.\n\n"
        )

    box.setInformativeText(
        head
        + tr(
            "The calibration is computed by comparing {count} photos in this"
            " folder against the camera's built-in JPEGs.\n\n"
            "· It takes anywhere from a few seconds to tens of seconds\n"
            "· The result is saved only to this PC's data folder\n"
            "· You can delete or recompute it later in the Optics section\n\n"
            "Compute now?"
        ).format(count=len(need.samples))
    )
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.setDefaultButton(QMessageBox.Yes)
    box.button(QMessageBox.Yes).setText(tr("Compute"))
    box.button(QMessageBox.No).setText(tr("Later"))
    box.setStyleSheet(theme.dialog_style())
    return box.exec() == QMessageBox.Yes


class CalibrationProgressDialog(QDialog):
    """Shows measurement progress. It can be cancelled."""

    def __init__(self, need: "calib.CalibrationNeed", parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Computing color calibration"))
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setStyleSheet(theme.dialog_style() + theme.PROGRESS_BAR)
        self.result: calib.CameraCalibration | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>{need.camera}</b>"))
        self.status = QLabel(
            tr("Comparing against the built-in JPEGs… ({done}/{total})")
            .format(done=0, total=len(need.samples))
        )
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.bar = QProgressBar()
        self.bar.setRange(0, len(need.samples))
        layout.addWidget(self.bar)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Cancel).setText(tr("Cancel"))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._worker = CalibrationWorker(need.samples, need.camera, need.key, self)
        self._worker.progressed.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, done: int, total: int) -> None:
        self.bar.setValue(done)
        self.status.setText(
            tr("Comparing against the built-in JPEGs… ({done}/{total})")
            .format(done=done, total=total)
        )

    def _on_done(self, result) -> None:
        self.result = result
        self.accept()

    def _on_failed(self, message: str) -> None:
        QMessageBox.warning(self, tr("Color calibration"), message)
        self.reject()

    def reject(self) -> None:  # noqa: D102
        self._worker.cancel()
        super().reject()

    def closeEvent(self, event) -> None:
        """If the window goes while the thread runs, Qt kills the process."""
        from .workers import stop_worker

        stop_worker(self._worker)
        super().closeEvent(event)


def run_calibration(
    parent, need: "calib.CalibrationNeed", manual: bool = False
) -> bool:
    """Ask, compute, and save. True if it was saved."""
    if not ask_to_calibrate(parent, need, manual=manual):
        return False

    dialog = CalibrationProgressDialog(need, parent)
    if dialog.exec() != QDialog.Accepted or dialog.result is None:
        return False

    result = dialog.result
    path = calib.save(result)
    if path is None:
        QMessageBox.warning(
            parent, tr("Color calibration"),
            tr("Could not save the calibration."),
        )
        return False

    if result.is_neutral():
        message = tr(
            "{camera} did not need any calibration.\n"
            "Its difference from the camera JPEGs is already small enough."
        ).format(camera=result.camera)
    else:
        b, g, r = result.gain
        message = tr(
            "Saved the calibration for {camera}.\n\n"
            "Channel gain  R {r:.3f} · G {g:.3f} · B {b:.3f}\n"
            "{samples} samples\n\n"
            "Saved to: {path}"
        ).format(
            camera=result.camera, r=r, g=g, b=b,
            samples=result.samples, path=path,
        )
    QMessageBox.information(parent, tr("Color calibration"), message)
    return True
