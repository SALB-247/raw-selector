"""Loupe / preview window.

Opens on a double-click, and does the develop work right here. Checking the
grading evidence (ROI), changing the grade, and stepping to the next shot all
have to finish inside one window, or the flow of reviewing hundreds of frames
keeps breaking.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QSplitter,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.develop import (
    DevelopSettings,
    ExifStripSettings,
    GeometrySettings,
    WatermarkSettings,
    engine,
)
from ..core.raw_io import (
    load_demosaiced,
    load_preview,
    read_white_balance,
    resize_long_edge,
    to_display,
)
from ..core import face_mesh, state
from ..core.config import AnalyzeConfig
from ..core.focus import FACE_DISPLAY_MIN_SCORE
from ..core.types import Grade, ImageRecord
from .reason_text import render_all
from .histogram import HistogramWidget
from .i18n import tr
from .image_view import ImageView
from . import theme

_FULL_CROP = {
    "crop_left": 0.0, "crop_top": 0.0, "crop_right": 1.0, "crop_bottom": 1.0,
}

PREVIEW_LONG_EDGE = 1400
"""Preview render resolution. Raise it and the sliders get visibly duller."""

_AF_UNREAD = object()
"""Marker for "the AF box has not been read yet". It has to be distinct from
None (not in the file) so we do not dig through the file on every render."""

FINAL_LONG_EDGE = 2200
"""Full Render display size. Demosaic uses the original; display uses this."""


from .workers import silent_disconnect as _silent_disconnect  # noqa: E402

log = logging.getLogger(__name__)

#: Where render threads still running after the window closed are held on to.
#:
#: Qt kills the process with qFatal **when a running QThread is destroyed**.
#: cancel() only raises a flag, and when the worker is inside the rawpy
#: demosaic (a single C call lasting seconds) there is no point at which it
#: can see that flag. So "cancel, wait a moment, then close" becomes a crash
#: the moment the wait falls short (measured: reproduced by opening and
#: closing the window 12 times mid-render).
#:
#: Instead of waiting, we move the reference here. The window closes at once,
#: and the thread drops itself after finishing at its own pace. By the time
#: it is destroyed it has already stopped.
_RUNNING_RENDERS: set = set()

#: The Full Render thread running in this process right now.
#:
#: **Only one may run at a time.** Demosaicing a single 27MP RAW at full
#: resolution takes a measured 2.8GB (R6M3). Two overlapping is 5.5GB - on an
#: 8GB PC that crosses the limit once the OS and the app are added, and to
#: the user it looks like "a crash".
#:
#: The path to overlap is ordinary: toggle the button off and on and
#: `_abandon_render` releases the running worker, but it **cannot stop it**
#: (the rawpy demosaic has no point at which it can be interrupted). Start a
#: new worker in that state and there are immediately two. So we look here
#: before starting, and only set off when it is empty.
_FULL_RENDER_SLOT: set = set()

FULL_RENDER_LOCKOUT_MS = 3000
"""Minimum time before the button can be pressed again after Full Render is
switched on.

Hammering it off and on overlaps heavy renders. A crash report actually came
in from exactly that. A brief lock stops the hammering itself.
"""


def _map_scene_points(points: np.ndarray, geometry,
                      scene_hw: tuple[int, int]) -> np.ndarray:
    """Scene (pre-geometry) normalised coordinates -> display (post-geometry)
    normalised coordinates.

    ROI, face, eye, and AF coordinates are all relative to the analysis image
    (before cropping). The screen is the result with geometry applied, so
    drawing them without converting puts the boxes in the wrong place on a
    shot with a crop or a rotation - which is why they used to be hidden
    outright whenever geometry was applied.

    The **same order** as engine.apply_geometry (rotate -> flip -> straighten
    -> crop) is applied to the coordinates. Straighten uses the matrix
    cv2.getRotationMatrix2D returns exactly as it comes, in the forward
    direction - the signs have to come out of cv2 rather than be derived by
    hand, or the image and the coordinates diverge. Their agreement is
    pinned down by a marker pixel test (see the straighten branch below).
    """
    import cv2 as _cv2

    out = np.asarray(points, dtype=np.float64).reshape(-1, 2).copy()
    height, width = float(scene_hw[0]), float(scene_hw[1])

    for _ in range(int(geometry.rotate_quarters) % 4):
        # cv2.ROTATE_90_CLOCKWISE: (x, y) -> (1-y, x), frame swaps (h, w)
        out = np.stack([1.0 - out[:, 1], out[:, 0]], axis=1)
        height, width = width, height

    if geometry.flip_horizontal:
        out[:, 0] = 1.0 - out[:, 0]
    if geometry.flip_vertical:
        out[:, 1] = 1.0 - out[:, 1]

    if geometry.straighten:
        matrix = _cv2.getRotationMatrix2D(
            (width / 2, height / 2), float(geometry.straighten), 1.0)
        # warpAffine (without WARP_INVERSE_MAP) uses this matrix in the
        # **source point -> result point** direction. I first reached for the
        # inverse and the marker pixel test caught it - this direction rests
        # on that test, not on remembered documentation.
        pixels = out * np.array([width, height])
        ones = np.ones((len(pixels), 1))
        moved = np.hstack([pixels, ones]) @ matrix.T
        out = moved / np.array([width, height])

    if geometry.has_crop():
        span_x = max(1e-6, geometry.crop_right - geometry.crop_left)
        span_y = max(1e-6, geometry.crop_bottom - geometry.crop_top)
        out[:, 0] = (out[:, 0] - geometry.crop_left) / span_x
        out[:, 1] = (out[:, 1] - geometry.crop_top) / span_y

    return out


def _map_scene_box(box_px: tuple, geometry, scene_hw: tuple[int, int],
                   out_wh: tuple[int, int]) -> tuple | None:
    """Analysis-coordinate box -> display pixel box. None if off-screen.

    Straighten tilts the box, but this is for display, so the four corners
    are transformed and approximated by the axis-aligned box enclosing them.
    """
    x, y, w, h = box_px
    corners = np.array([[x, y], [x + w, y], [x, y + h], [x + w, y + h]],
                       dtype=np.float64)
    corners /= np.array([scene_hw[1], scene_hw[0]])
    mapped = _map_scene_points(corners, geometry, scene_hw)

    x0, y0 = mapped.min(axis=0)
    x1, y1 = mapped.max(axis=0)
    if x1 <= 0.0 or y1 <= 0.0 or x0 >= 1.0 or y0 >= 1.0:
        return None                      # cropped away - nothing to draw
    out_w, out_h = out_wh
    return (x0 * out_w, y0 * out_h, (x1 - x0) * out_w, (y1 - y0) * out_h)


_WORKER_SIGNALS = ("source_ready", "done", "failed", "finished")
"""Every signal the render worker sends to the window. Cleanup must not miss
a single one.

If source_ready is left out, a retired worker later pushes its demosaic
source into the window and the next render reuses stale pixels (see
_keep_demosaic).
"""


def _disconnect_worker(worker) -> None:
    """Disconnects every signal on the worker. Missing signals are skipped.

    They are looked up by name because the cleanup path **must never raise,
    under any circumstance**. Reaching for them as attributes raises
    AttributeError on an object that lacks one signal, and at that moment the
    cleanup that follows (cancel, keeping the reference) is skipped wholesale
    - which is exactly the situation where a running thread gets lost.
    """
    for name in _WORKER_SIGNALS:
        signal = getattr(worker, name, None)
        if signal is not None:
            _silent_disconnect(signal)


def _detach_until_finished(worker) -> None:
    """Detaches from the window and keeps the thread alive until it ends."""
    _RUNNING_RENDERS.add(worker)
    worker.finished.connect(lambda: _RUNNING_RENDERS.discard(worker))


def full_render_in_flight() -> bool:
    """Whether a Full Render is running right now, in any window."""
    for worker in list(_FULL_RENDER_SLOT):
        try:
            if worker.isRunning():
                return True
        except RuntimeError:
            pass
        _FULL_RENDER_SLOT.discard(worker)
    return False


def wait_for_detached_renders(timeout_ms: int = 30000) -> None:
    """Waits for the remaining renders before the app shuts down.

    Here we really do have to wait - once the interpreter ends the objects
    disappear, and if one is running at that point the same crash follows.
    """
    for worker in list(_RUNNING_RENDERS):
        try:
            if worker.isRunning():
                worker.wait(timeout_ms)
        except RuntimeError:
            pass
    _RUNNING_RENDERS.clear()


def _remap_box(
    box: tuple[float, float, float, float] | None,
    region: tuple[float, float, float, float],
) -> tuple[float, float, float, float] | None:
    """Normalised box -> coordinates inside the cut region. None if outside."""
    if box is None:
        return None
    left, top, right, bottom = region
    span_x, span_y = right - left, bottom - top
    if span_x <= 0 or span_y <= 0:
        return None
    x = (box[0] - left) / span_x
    y = (box[1] - top) / span_y
    w, h = box[2] / span_x, box[3] / span_y
    if x + w <= 0 or y + h <= 0 or x >= 1.0 or y >= 1.0:
        return None
    return (x, y, w, h)


_BASE_CACHE: dict = {}
_BASE_CACHE_LOCK = threading.Lock()
BASE_CACHE_SLOTS = 6
"""Preview bases - the half demosaic shrunk to PREVIEW_LONG_EDGE, about
16MB each at 50MP - by (file, colour temperature, highlight recovery).

Filled ahead of time for the shots next to the one on screen
(BasePrefetchWorker) and by the window's own load, so stepping to the
next shot - or back to the last - finds the base ready instead of
demosaicing on the main thread (0.6s at 50MP; 2s before the profile
curve went to a table). Six slots: the shot on screen, two ahead, one
behind, and room for the direction to turn."""


def _base_key(path: Path, kelvin: int, highlight: bool):
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    return (str(path), stat.st_mtime_ns, stat.st_size, int(kelvin), bool(highlight))


def _cached_base(key):
    with _BASE_CACHE_LOCK:
        return _BASE_CACHE.get(key)


def clear_base_cache() -> None:
    """Drops every cached base. The base bakes the body's colour
    calibration in at demosaic time and the key does not carry it, so a
    calibration run in between would hand the develop window the old
    colour while the Full Render showed the new."""
    with _BASE_CACHE_LOCK:
        _BASE_CACHE.clear()


def _store_base(key, value) -> None:
    if key is None:
        return
    with _BASE_CACHE_LOCK:
        _BASE_CACHE.pop(key, None)
        _BASE_CACHE[key] = value
        while len(_BASE_CACHE) > BASE_CACHE_SLOTS:
            _BASE_CACHE.pop(next(iter(_BASE_CACHE)))


def build_preview_base(path: Path, kelvin: int, highlight: bool):
    """The develop view's base for one shot: the half demosaic at this
    colour temperature and highlight setting, shrunk to PREVIEW_LONG_EDGE,
    with the sensor width the ROI coordinates are scaled by."""
    from ..core.raw_io import load_demosaiced, resize_long_edge

    full = load_demosaiced(path, half_size=True, target_kelvin=kelvin or None,
                           highlight_recovery=highlight)
    return resize_long_edge(full, PREVIEW_LONG_EDGE), full.shape[1] * 2


class BasePrefetchWorker(QThread):
    """Builds the preview bases of the shots next to the one on screen,
    in the order they are likely to be wanted. A base already in the
    cache is skipped; cancel() stops it between files (a demosaic under
    way runs to its end, as any rawpy call does)."""

    def __init__(self, jobs: "list[tuple[Path, int, bool]]"):
        super().__init__()
        self._jobs = jobs
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        for path, kelvin, highlight in self._jobs:
            if self._cancelled:
                return
            key = _base_key(path, kelvin, highlight)
            if key is None or _cached_base(key) is not None:
                continue
            try:
                _store_base(key, build_preview_base(path, kelvin, highlight))
            except Exception:  # noqa: BLE001 - the window loads it itself then
                log.debug("이웃 컷 베이스 준비 실패: %s", path.name, exc_info=True)


class FinalRenderWorker(QThread):
    """Actually demosaics the RAW to build the Full Render preview.

    Developing 24MP can take several seconds, so it is split off to keep the
    main thread unblocked.

    Measured (R6M3 27MP): demosaic 5.1s, adjustments 3.4s. **60% of the time
    is demosaic**, and rawpy works on whole images so it cannot be split up.
    Starting over on every zoom would therefore cost 8.5s each time.

    Two things cut that down.

    1. The window holds the demosaic result and hands it back (`source`).
       While zooming and panning the same shot, the 5.1s is not spent again.
    2. When zoomed in, only **the region visible on screen** is adjusted. At
       4x zoom the adjustments go from 3.4s -> 0.22s (measured).

    Later measurement (2026-09-14, A1 50MP): of the 7.4s "demosaic", LibRaw
    itself was 1.3s and the profile curve on 150M values 5s - the curve is
    a table now and the load is 2.3s. The optical correction the zoomed
    region needs is read for that region only (engine.apply_optics_stage
    with `region`), 0.3s instead of 13s on the whole frame per pan.
    """

    done = Signal(object)     # the finished BGR image
    failed = Signal(str)
    source_ready = Signal(object)  # demosaic source (reused by next render)

    def __init__(self, path: Path, settings: DevelopSettings, wb,
                 target_long_edge: int = FINAL_LONG_EDGE, generation: int = 0,
                 source: "np.ndarray | None" = None,
                 region: tuple[float, float, float, float] | None = None,
                 main_face_box: tuple[float, float, float, float] | None = None,
                 metadata=None, base_kelvin: int = 0):
        super().__init__()
        self._base_kelvin = base_kelvin
        """The colour temperature the handed-over source was already
        demosaiced at (0 = as-shot).

        Demosaicing directly, without a source, uses this value as well -
        that is what makes the screen and the Full Render apply the same
        gains on the same base.
        """
        self._main_face_box = main_face_box
        # Automatic lens correction finds its profile by camera and lens
        # name. Without it the source comes back silently uncorrected, so a
        # correction visible on screen disappears in the Full Render only.
        self._metadata = metadata
        self._path = path
        self._settings = settings
        self._wb = wb  # (camera, daylight) or None
        self._target = max(1, int(target_long_edge))
        self.generation = generation
        self._source = source
        """An already demosaiced source. If present, that stage is skipped."""
        self.region = region
        """The region to adjust (left, top, right, bottom, 0~1). None = all."""
        self._cancelled = False

    def cancel(self) -> None:
        """Marks the result to be discarded. Does not force-kill the thread."""
        self._cancelled = True

    def run(self) -> None:
        try:
            import numpy as _np

            from ..core.raw_io import load_demosaiced, resize_long_edge

            face_box = self._main_face_box
            image = self._source
            if image is None:
                # Same way as the live preview (half), but demosaiced at
                # full resolution.
                image = load_demosaiced(
                    self._path,
                    target_kelvin=self._base_kelvin or None,
                    highlight_recovery=self._settings.basic.highlight_recovery)
                if self._cancelled:
                    return
                # The window holds it and hands it back on the next zoom/pan
                self.source_ready.emit(image)

            if self._cancelled:
                return

            settings = self._settings
            scene_hw = None
            if self.region is not None:
                # The visible region only, corrected **as part of the whole
                # frame**. Distortion and vignetting are computed from the
                # frame centre and size, so applying them to a piece treats
                # that piece as the whole frame (measured 25.9 levels on
                # average, +52.7 at the corners). The optics stage takes the
                # whole frame and the piece's bounds, corrects only the
                # pixels the piece draws from, and hands the piece back -
                # correcting the whole 50MP frame first and cutting
                # afterwards was 13 seconds on every pan. The settings that
                # come back have optics neutralised, so apply_settings below
                # does not apply them a second time; geometry is passed on
                # neutralised too, or the crop would be applied twice.
                height, width = image.shape[:2]
                left, top, right, bottom = self.region
                x0 = max(0, min(width - 1, int(left * width)))
                y0 = max(0, min(height - 1, int(top * height)))
                x1 = max(x0 + 1, min(width, int(right * width)))
                y1 = max(y0 + 1, min(height, int(bottom * height)))
                frame_hw = (height, width)          # scene size before crop
                image, settings = engine.apply_optics_stage(
                    image, settings, self._path, self._metadata,
                    region=(x0, y0, x1, y1))
                if self._cancelled:
                    return
                # The main-subject coordinates are re-based on the cut piece
                # too. Otherwise the mask moves to the wrong face, but only
                # when zoomed in.
                face_box = _remap_box(face_box, (left, top, right, bottom))

            piece_long = max(image.shape[:2])
            # Shrink only as far as the resolution actually visible on
            # screen. resize_long_edge never enlarges, so if the target is
            # larger than the source, the source goes through unchanged.
            image = resize_long_edge(image, self._target)
            if self._cancelled:
                return
            if self.region is not None:
                # The size the piece would have had as the whole scene.
                # Without passing this, the sharpness, texture, and clarity
                # radii are computed from the piece size, so the zoomed
                # preview applies them 1/zoom weaker than the export - and it
                # goes wrong at exactly the place you zoom in to check
                # sharpness.
                shrink = max(image.shape[:2]) / max(1, piece_long)
                scene_hw = (max(1, round(frame_hw[0] * shrink)),
                            max(1, round(frame_hw[1] * shrink)))
            # **Adjustments only - no watermark, no info strip.** The side
            # that puts it on screen (_on_final_ready ->
            # _apply_display_overlays) lays those two on again, so baking
            # them here puts them in twice - measured, the height grew by
            # 132px and the same text ran on two lines. The fast preview path
            # (_render) had been doing it this way from the start; only this
            # worker was missing it, which is why the symptom appeared only
            # with Full Render on.
            # output_space="srgb": the adjustments are applied in the working
            # space, but this result goes to the screen. The engine has to
            # move it to sRGB before quantising for the viewport to be the
            # same colour as the export - to_display passes uint8 straight
            # through.
            result = engine.apply_settings(
                image,
                replace(settings, watermark=WatermarkSettings(),
                        exif_strip=ExifStripSettings()),
                self._path, self._metadata,
                wb=self._wb, main_face_box=face_box,
                base_kelvin=self._base_kelvin, scene_hw=scene_hw,
                output_space="srgb")
            if self._cancelled:
                return
            self.done.emit(result)
        except Exception as exc:  # noqa: BLE001
            if not self._cancelled:
                self.failed.emit(str(exc))


CLIP_BLINK_MS = 550
"""Clipping overlay blink period. Painted steadily it cannot be told apart
from the photo's own colour."""

CLIP_HIGHLIGHT_LEVEL = 250
CLIP_SHADOW_LEVEL = 5
"""The pixel values counted as clipped.

Set to 254/2, only pixels landing on exactly those values after the drop to 8
bits are caught, so a region plainly blown to the eye barely raises any
overlay. A report of "I turn it on and see nothing" came from exactly this.
Pulling them a little inward is what makes them work as a warning.
"""


def clip_overlay(
    image_bgr: np.ndarray, show_shadow: bool, show_highlight: bool
) -> np.ndarray:
    """Marks clipped pixels by painting over them (the way Lightroom does).

    Blown highlights (any channel at or above the upper level) are painted
    red, crushed shadows (every channel at or below the lower level) blue.
    The source is left untouched.
    """
    result = image_bgr.copy()
    if show_highlight:
        blown = image_bgr.max(axis=2) >= CLIP_HIGHLIGHT_LEVEL
        result[blown] = (0, 0, 255)  # BGR red
    if show_shadow:
        crushed = image_bgr.max(axis=2) <= CLIP_SHADOW_LEVEL
        result[crushed] = (255, 0, 0)  # BGR blue
    return result


def clip_counts(image_bgr: np.ndarray) -> tuple[int, int]:
    """(crushed pixel count, blown pixel count). Needed to explain why the
    overlay is not showing."""
    crushed = int(np.count_nonzero(image_bgr.max(axis=2) <= CLIP_SHADOW_LEVEL))
    blown = int(np.count_nonzero(image_bgr.max(axis=2) >= CLIP_HIGHLIGHT_LEVEL))
    return crushed, blown


def bgr_to_pixmap(image: np.ndarray) -> QPixmap:
    """OpenCV BGR ndarray -> QPixmap. A copy is what keeps the buffer alive.

    QImage reads the byte run as uint8 with 3 channels (bytesPerLine=3*width).
    Hand it a float array as-is and it misreads 4-byte values as pixels,
    turning the whole screen into colour noise. Intermediate pipeline values
    are float, so they are forced to 8 bits here - this has to be safe even
    when the caller forgets.
    """
    if image.dtype != np.uint8:
        image = np.clip(image, 0.0, 255.0).astype(np.uint8)
    rgb = np.ascontiguousarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    height, width, _ = rgb.shape
    qimage = QImage(rgb.data, width, height, 3 * width, QImage.Format_RGB888)
    return QPixmap.fromImage(qimage.copy())


class LoupeDialog(QDialog):
    """Preview + develop + moving between shots.

    Pass records along as well and you can step back and forth within the
    same list.
    """

    records_changed = Signal()
    queue_requested = Signal(list)
    export_requested = Signal(list)
    record_switched = Signal(object, object)  # (previous path, new path)
    main_face_changed = Signal(object)  # record whose main subject changed

    def __init__(
        self,
        record: ImageRecord,
        records: list[ImageRecord] | None = None,
        parent=None,
        fast: bool = False,
        analyze_config: AnalyzeConfig | None = None,
    ):
        super().__init__(parent)
        # Changing the main subject re-runs the scoring - it has to use the
        # same settings the batch did or the scores diverge. If none comes
        # in, we leave the defaults (which match if the batch used defaults
        # too).
        self._analyze_config = analyze_config or AnalyzeConfig()
        # fast=True is preview mode: the adjust panel is hidden so the image
        # gets the whole window. The picture itself is identical - both modes
        # demosaic the RAW (_load_base), never the embedded JPEG, so the
        # colour and gradation you judge in preview are the ones develop
        # starts from.
        self._fast = fast
        self.records = records or [record]
        self.index = self.records.index(record) if record in self.records else 0
        self.record = self.records[self.index]

        self._source: np.ndarray | None = None
        self._wb = None  # raw_io.WhiteBalance - for absolute Kelvin
        self._final_worker = None  # the Full Render thread
        self._prefetch_worker: BasePrefetchWorker | None = None
        self._ratio_seen: float | None = None
        """The crop ratio the last settings change carried, so a change of
        the combo outside crop mode can be told from any other change."""
        self._crop_before = None
        """The crop and its ratio as they stood when the handles came up -
        what Cancel puts back."""
        self._fitting_depth = 0
        """How many ratio fits are being committed inside one another. The
        commit a fit makes raises a settings change, which fits again on
        the sliders' rounded values - that converges (each pass either
        changes nothing at slider precision, or shortens a side by a whole
        percent), but only a few levels are ever needed and none may run
        away."""
        self._step_direction = 1
        """Which way the last step went, so the bases prepared ahead are
        the ones about to be shown."""
        # Workers cancelled but still running. Drop the reference and Qt
        # kills the process.
        self._retired_workers: list[FinalRenderWorker] = []
        self._waiting_for_slot = False
        """Whether we are waiting for the previous render to finish. A marker
        that keeps renders from overlapping."""

        self._clip_base: np.ndarray | None = None
        """The image just before the clipping paint. Blinking repaints only
        from here."""
        self._clip_blink_on = True

        self._roi_reference_width = 0
        """Reference width for the roi and faces coordinates. Drawing divides
        the image width by it to get the scale."""

        self._eye_contours: list[np.ndarray] | None = None
        """This shot's eye contours (analysis coordinates). Cleared when the
        shot or the main subject changes."""

        self._af_box: object = _AF_UNREAD
        """This shot's camera AF box (analysis coordinates). _AF_UNREAD = not
        read yet, None = not in the file, (x,y,w,h) = present. Cached records
        do not carry it, so it is read at display time."""

        self._demosaic_cache = None
        self._demosaic_path: Path | None = None
        """The demosaic result of the last Full Render. Reused on zoom/pan."""

        self._rendered_frame = (False, False)
        """The last render's display-frame decision - (crop editing, mask
        editing).

        Entering mask or crop editing makes the screen release geometry and
        show a different frame. A Full Render in flight at that moment was
        built **with the previous frame**, so on arrival it covers what is
        under the scene-coordinate handles with a cropped picture - the
        mismatch this redesign set out to remove comes back to life in that
        window of a few seconds. It needs the same treatment as a value edit
        folding a stale render immediately (_on_settings_changed), and
        spotting the transition means holding on to the previous decision.
        """

        self._display_geometry = GeometrySettings()
        """The geometry actually applied to the screen right now.

        The ROI and face overlays are in analysis (pre-crop) coordinates, so
        they are moved through this geometry to be drawn. Reading
        panel.settings() on the spot is wrong - during crop mode and mask
        editing the screen is drawn with geometry partly or wholly released
        (_render), so it diverges from the values used for display.
        """

        self._locked = False
        """Blocks editing while an export is running (set_locked).

        It used to be created only inside set_locked. That stayed hidden as
        long as it was written and never read, but once _settle_white_balance
        started reading it, a window that had never been locked raised
        AttributeError.
        """

        self._base_kelvin = 0
        """The target colour temperature used when demosaicing the preview
        base (0 = as-shot).

        White balance is an operation on sensor-linear values, so multiplying
        channel gains onto developed values is an approximation - the further
        from as-shot, the wider it opens up (measured: 9.2 levels on average
        at 183 mired, with 64% of pixels over 5 levels).

        So when the slider settles we demosaic again at that colour
        temperature. While it is being dragged, gains on the old base carry
        the preview - re-demosaicing is 40x slower (0.03s against 1.23s).
        """

        self._base_highlight = False
        """The highlight recovery value used when demosaicing the preview
        base.

        It is a decode-stage option, so unlike the sliders it only takes
        effect once the base itself is rebuilt. This value is how we notice
        it has diverged from the panel's."""

        self._final_region: tuple[float, float, float, float] | None = None
        """The region the last Full Render built. If it is not the whole
        frame, fitting to the screen works differently."""
        self._degraded = False  # on the JPEG fallback after demosaic failed
        self._degraded_reason = ""
        """Why we fell back. If we can tell, we show it on screen as-is."""
        self._dirty = False

        # Shown non-modal. Modal would freeze the main window while
        # developing, so you could not look at the grid or open another shot.
        # The minimise/maximise buttons have to be spelled out. A QDialog
        # carries only the close button by default (no WS_MAXIMIZEBOX), so
        # window snapping (Aero Snap) does not engage - dragging to the
        # screen edge never gives the half-screen layout.
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowSystemMenuHint
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )
        self.setModal(False)
        self.setAttribute(Qt.WA_DeleteOnClose)

        # Opening a window larger than the screen makes the window manager
        # shrink it, and at that point the splitter cannot hold the panel's
        # minimum width, so it comes up with the right side cut off. Open at
        # a size that fits inside the screen from the start (a problem
        # actually hit at FHD 100%).
        available = QApplication.primaryScreen()
        if available is not None:
            geometry = available.availableGeometry()
            self.resize(
                min(1680, max(900, geometry.width() - 80)),
                min(980, max(600, geometry.height() - 80)),
            )
        else:
            self.resize(1680, 980)
        self.setStyleSheet(theme.dialog_style("#1b1b1d"))

        self._build_ui()
        self._build_shortcuts()
        # Enter belongs to the crop handles while they are up (Apply). A
        # QDialog makes every push button autoDefault, and a focused one
        # - the 90 degree button just clicked, say - clicks itself on
        # Return before the key ever reaches this window.
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)

        # The splitter decides the panel width, but if the window itself gets
        # narrower than the panel's minimum, the right side (value boxes,
        # reset buttons) is cut off. We take the minimum the layout asks for
        # as the window minimum, so it cannot shrink below that. The value
        # varies with the font metrics, so it is read from the layout rather
        # than hardcoded. The height is set loosely, since the panel scrolls.
        self.layout().activate()
        self.setMinimumSize(self.layout().minimumSize().width(), 640)

        # Quitting the program with the window still open never calls
        # closeEvent. A running worker is then destroyed and Qt kills the
        # process (0xc0000409). We take one more chance to clean up just
        # before shutdown.
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown_workers)

        # Rendering on every slider drag stutters. We draw once it pauses.
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(120)
        self._render_timer.timeout.connect(self._render)

        # When the colour temperature settles we demosaic again at that
        # value. It has to run before the Full Render (600ms) - a Full Render
        # built on the old base is thrown away immediately.
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.setInterval(600)
        self._settle_timer.timeout.connect(self._settle_white_balance)

        # Full Render only runs once the controls stop. Developing at full
        # resolution on every input would make the sliders unusable.
        self._final_generation = 0
        self._full_render_timer = QTimer(self)
        self._full_render_timer.setSingleShot(True)
        self._full_render_timer.setInterval(800)
        self._full_render_timer.timeout.connect(self._show_final_preview)

        # Timer that locks the button right after it is switched on.
        # Hammering it overlaps heavy renders, doubling memory, and a small
        # PC dies at that point.
        self._full_render_lock = QTimer(self)
        self._full_render_lock.setSingleShot(True)
        self._full_render_lock.timeout.connect(self._release_full_render_button)

        # Retry timer for waiting out the previous render before setting off
        self._slot_timer = QTimer(self)
        self._slot_timer.setSingleShot(True)
        self._slot_timer.timeout.connect(self._retry_when_slot_free)

        # Clipping blink. Painted steadily you cannot tell the warning from
        # the colour that was already there.
        self._clip_blink_timer = QTimer(self)
        self._clip_blink_timer.timeout.connect(self._blink_clip_overlay)

        QTimer.singleShot(0, self._load_current)

    # ------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self.info = QLabel()
        self.info.setWordWrap(True)
        layout.addWidget(self.info)

        # Without saying why it is locked, the user thinks the program hung
        self.lock_notice = QLabel()
        self.lock_notice.setWordWrap(True)
        self.lock_notice.setStyleSheet(
            f"background: {theme.SURFACE}; color: {theme.WARNING};"
            f" border: 1px solid {theme.WARNING}; border-radius: 4px;"
            " padding: 5px 8px; font-weight: bold;"
        )
        self.lock_notice.setVisible(False)
        layout.addWidget(self.lock_notice)

        # Lets the user drag the divide between the image and the develop
        # panel. With a fixed width, the slightest growth in content cuts the
        # right side off silently (hit three times in practice). The splitter
        # holds only the minimum and leaves the rest to the window size and
        # what the user does.
        self.body_splitter = QSplitter(Qt.Horizontal)
        self.body_splitter.setChildrenCollapsible(False)
        self.body_splitter.setHandleWidth(6)
        layout.addWidget(self.body_splitter, 1)

        viewer_box = QWidget()
        viewer = QVBoxLayout(viewer_box)
        viewer.setContentsMargins(0, 0, 0, 0)
        self.preview = ImageView()
        self.preview.set_message(tr("Loading…"))
        self.preview.crop_changed.connect(self._on_crop_dragged)
        # Through the panel, not straight to the render: the drag pushed
        # its values in silently, and the panel is where a switched-off
        # section wakes. Straight to the render, an off geometry section
        # handed back no crop at all.
        self.preview.crop_finished.connect(self._commit_external_edit)
        self.preview.zoom_changed.connect(self._on_zoom)
        self.preview.pan_finished.connect(self._schedule_full_render)
        viewer.addWidget(self.preview, 1)
        viewer.addLayout(self._build_viewer_controls())
        self.body_splitter.addWidget(viewer_box)

        right = QVBoxLayout()
        right.setSpacing(4)

        self.histogram = HistogramWidget()
        right.addWidget(self.histogram)

        # These toggles used to be the 9px triangles in the histogram's top
        # left and right corners. Dark grey, so nobody knew they were there,
        # and you had to hit a 22px corner exactly, so pressing them often
        # did nothing. We pull them out as buttons with text on them.
        clip_row = QHBoxLayout()
        clip_row.setSpacing(4)
        clip_row.setContentsMargins(0, 0, 0, 0)

        self.shadow_clip_button = QPushButton(tr("▼ Shadows"))
        self.shadow_clip_button.setCheckable(True)
        self.shadow_clip_button.setToolTip(
            tr("Blinks the shadow pixels with crushed tone in blue"))
        self.shadow_clip_button.setStyleSheet(theme.clip_button(theme.CLIP_SHADOW))
        self.shadow_clip_button.toggled.connect(self._on_clip_overlay_toggled)
        clip_row.addWidget(self.shadow_clip_button)

        self.highlight_clip_button = QPushButton(tr("▲ Highlights"))
        self.highlight_clip_button.setCheckable(True)
        self.highlight_clip_button.setToolTip(
            tr("Blinks the highlight pixels with blown tone in red"))
        self.highlight_clip_button.setStyleSheet(
            theme.clip_button(theme.CLIP_HIGHLIGHT))
        self.highlight_clip_button.toggled.connect(self._on_clip_overlay_toggled)
        clip_row.addWidget(self.highlight_clip_button)
        right.addLayout(clip_row)

        self.clip_label = QLabel()
        self.clip_label.setStyleSheet(f"color: {theme.WARNING}; font-size: 11px;")
        right.addWidget(self.clip_label)
        self.histogram.clipping_changed.connect(self._on_clipping)
        self.histogram.overlay_toggled.connect(self._sync_clip_buttons)

        # Imported here to avoid a circular import
        from .develop_panel import DevelopPanel

        self.panel = DevelopPanel()
        self.panel.settings_changed.connect(self._on_settings_changed)
        self.panel.camera_match_requested.connect(self._match_camera_look)
        self.panel.lens_profile_requested.connect(self._measure_lens_profile)
        self.panel.crop_mode_changed.connect(self._on_crop_mode)
        self.panel.crop_cancelled.connect(self._cancel_crop)
        self.panel.pick_mode_changed.connect(self._on_pick_mode)
        self.panel.mask_overlay_changed.connect(self._render)
        self.panel.mask_shape_changed.connect(self._sync_mask_shape)
        self.panel.brush_mode_changed.connect(self._on_brush_mode)
        self.panel.brush_changed.connect(self._sync_brush_cursor)
        self.preview.color_picked.connect(self._on_color_picked)
        self.preview.brush_painted.connect(self._on_brush_paint)
        self.preview.clicked.connect(self._on_preview_clicked)
        self.preview.shape_changed.connect(self._on_shape_dragged)
        self.preview.shape_finished.connect(self._commit_external_edit)
        right.addWidget(self.panel, 1)

        container = QWidget()
        container.setLayout(right)
        # A minimum, not a fixed width. If it is too narrow the user can drag
        # the splitter wider, and when the window grows the image side grows.
        container.setMinimumWidth(self.panel.minimumWidth())
        self.body_splitter.addWidget(container)
        self.body_splitter.setStretchFactor(0, 1)   # image takes the slack
        self.body_splitter.setStretchFactor(1, 0)
        # In preview mode the develop panel is hidden so the image can be
        # seen large. (Colour is demosaic-accurate, exactly as in develop
        # mode.)
        if self._fast:
            container.setVisible(False)

        layout.addLayout(self._build_footer())

    def _build_viewer_controls(self) -> QHBoxLayout:
        row = QHBoxLayout()

        self.prev_button = QPushButton(tr("◀ Previous"))
        self.prev_button.setToolTip(tr("Previous shot (←)"))
        self.prev_button.clicked.connect(lambda: self.step(-1))
        row.addWidget(self.prev_button)

        self.next_button = QPushButton(tr("Next ▶"))
        self.next_button.setToolTip(tr("Next shot (→)"))
        self.next_button.clicked.connect(lambda: self.step(1))
        row.addWidget(self.next_button)

        self.position_label = QLabel()
        self.position_label.setStyleSheet("color: #9a9aa2;")
        row.addWidget(self.position_label)

        row.addStretch(1)

        # Shortcuts go in the tooltip, not the label. With the overlay
        # toggles grown to three, adding tails like "(B)" as well pushes the
        # window's minimum width to 1004px from this row alone, which cuts
        # off the right panel on a 900px screen (measured).
        self.before_after = QCheckBox(tr("Original"))
        self.before_after.setToolTip(tr("Shows the image before develop (B)"))
        self.before_after.toggled.connect(self._render)
        row.addWidget(self.before_after)

        # As more got drawn over the image, keeping it all under one switch
        # became untenable. If you only want the focus region but the face
        # boxes come along with it, you cannot actually see the focus.
        self.show_roi = QCheckBox(tr("Focus"))
        self.show_roi.setChecked(True)
        self.show_roi.setToolTip(tr("The region used for grading — green box (F)"))
        self.show_roi.toggled.connect(self._render)
        row.addWidget(self.show_roi)

        self.show_faces = QCheckBox(tr("Faces"))
        self.show_faces.setChecked(True)
        self.show_faces.setToolTip(tr(
            "Detected faces — grey boxes, the main subject in red (A).\n"
            "Click a face to make it the main subject and re-grade."
        ))
        self.show_faces.toggled.connect(self._render)
        row.addWidget(self.show_faces)

        self.show_eyes = QCheckBox(tr("Eyes"))
        self.show_eyes.setToolTip(tr("Eye contours — to check the eyes are really open (E)"))
        self.show_eyes.toggled.connect(self._render)
        row.addWidget(self.show_eyes)

        self.show_af = QCheckBox(tr("AF point"))
        self.show_af.setToolTip(tr(
            "Where the camera focused — orange box (P).\n"
            "Sony, Canon CR3, Nikon. Not every file records it."))
        self.show_af.toggled.connect(self._render)
        row.addWidget(self.show_af)

        self.focus_zoom_button = QPushButton(tr("Zoom to focus"))
        self.focus_zoom_button.setToolTip(tr(
            "Fills the screen with the region used for grading (Z).\n"
            "You have to zoom in to tell whether focus really landed on the eyes."
        ))
        self.focus_zoom_button.clicked.connect(self.zoom_to_focus)
        row.addWidget(self.focus_zoom_button)

        self.final_button = QPushButton("Full Render")
        self.final_button.setCheckable(True)
        self.final_button.setToolTip(tr(
            "The usual preview develops at half resolution for speed.\n"
            "With this on, it re-develops at full resolution to match the\n"
            "screen whenever you stop adjusting — for checking sharpening,\n"
            "noise, and mask retouching at real quality. Zooming in redraws\n"
            "it that much more finely."
        ))
        self.final_button.setStyleSheet(theme.TOGGLE_BUTTON)
        self.final_button.toggled.connect(self._on_full_render_toggled)
        row.addWidget(self.final_button)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setStyleSheet("color: #9a9aa2;")
        self.zoom_label.setToolTip(tr("Wheel to zoom · drag to pan · double-click to reset"))
        row.addWidget(self.zoom_label)

        return row

    def _build_footer(self) -> QHBoxLayout:
        footer = QHBoxLayout()

        footer.addWidget(QLabel(tr("Grade")))
        self.grade_buttons: dict[Grade, QPushButton] = {}
        for grade, label, color in (
            (Grade.KEEP, "keep (1)", "#4caf50"),
            (Grade.REVIEW, "review (2)", "#ffa726"),
            (Grade.REJECT, "reject (3)", "#e55757"),
        ):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setStyleSheet(
                "QPushButton { background: #3a3a3f; color: #ccc; border: none;"
                " padding: 6px 12px; border-radius: 4px; }"
                f"QPushButton:checked {{ background: {color}; color: #16161a;"
                " font-weight: bold; }"
            )
            button.clicked.connect(lambda _=False, g=grade: self.set_grade(g))
            self.grade_buttons[grade] = button
            footer.addWidget(button)

        footer.addStretch(1)

        self.apply_all_button = QPushButton(tr("Apply develop to all"))
        self.apply_all_button.setToolTip(tr(
            "Applies the develop set in this window to every shot in the list.\n"
            "Crop and straighten are excluded, since framing differs shot to shot."
        ))
        self.apply_all_button.clicked.connect(self.apply_to_all)
        footer.addWidget(self.apply_all_button)

        self.queue_button = QPushButton(tr("Add to queue (Q)"))
        self.queue_button.setToolTip(tr("Add this shot to the queue with its current develop"))
        self.queue_button.clicked.connect(self.add_to_queue)
        footer.addWidget(self.queue_button)

        self.export_button = QPushButton(tr("Export"))
        self.export_button.setToolTip(tr("Export this shot right now"))
        self.export_button.clicked.connect(self.export_current)
        footer.addWidget(self.export_button)

        close = QPushButton(tr("Close"))
        close.clicked.connect(self.accept)
        footer.addWidget(close)

        return footer

    # ------------------------------------------------------------ Zoom

    def zoom_to_focus(self) -> None:
        """Fills the screen with the ROI used for scoring."""
        if not (self.record.focus and self.record.focus.roi):
            return
        if not self.panel.settings().geometry.is_neutral():
            # A crop changes the coordinate system, so the ROI position
            # cannot be trusted
            return
        self.preview.zoom_to_roi(self.record.focus.roi, self._roi_scale)
        self._on_zoom(self.preview.zoom())

    def _on_zoom(self, zoom: float) -> None:
        self.zoom_label.setText(f"{zoom * 100:.0f}%")
        # Zooming in needs a finer resolution. We redraw once zooming stops.
        self._schedule_full_render()

    # ---------------------------------------------------------- Queue / export

    def add_to_queue(self) -> None:
        """Puts the current shot in the queue. The parent window holds it."""
        self._commit_settings()
        self.queue_requested.emit([self.record])

    def export_current(self) -> None:
        self._commit_settings()
        self.export_requested.emit([self.record])

    def _build_shortcuts(self) -> None:
        for keys, handler in (
            ("Left", lambda: self.step(-1)),
            ("Right", lambda: self.step(1)),
            ("1", lambda: self.set_grade(Grade.KEEP)),
            ("2", lambda: self.set_grade(Grade.REVIEW)),
            ("3", lambda: self.set_grade(Grade.REJECT)),
            ("B", self._toggle_before_after),
            ("F", lambda: self.show_roi.setChecked(not self.show_roi.isChecked())),
            ("A", lambda: self.show_faces.setChecked(not self.show_faces.isChecked())),
            ("E", lambda: self.show_eyes.setChecked(not self.show_eyes.isChecked())),
            ("P", lambda: self.show_af.setChecked(not self.show_af.isChecked())),
            ("Z", self.zoom_to_focus),
            ("Q", self.add_to_queue),
            ("F1", self.show_shortcuts),
            ("[", lambda: self.step_scene(-1)),
            ("]", lambda: self.step_scene(1)),
        ):
            action = QAction(self)
            action.setShortcut(QKeySequence(keys))
            action.triggered.connect(handler)
            self.addAction(action)

    def _toggle_before_after(self) -> None:
        self.before_after.setChecked(not self.before_after.isChecked())

    # --------------------------------------------------------- Shot navigation

    def step_scene(self, direction: int) -> None:
        """Moves to the first shot of the previous / next scene."""
        from ..core.ordering import scene_step

        target = scene_step(self.records, self.index, direction)
        if target is not None:
            self.step(target - self.index)

    def step(self, delta: int) -> None:
        """Moves to the previous/next shot. Stops at the ends of the list."""
        target = self.index + delta
        if not (0 <= target < len(self.records)):
            return
        self._commit_settings()
        previous_path = self.record.path
        self.index = target
        self.record = self.records[target]
        self._step_direction = 1 if delta > 0 else -1
        # Moving shots makes the held demosaic source (about 390MB) useless
        self._drop_demosaic()
        self._load_current()
        self.record_switched.emit(previous_path, self.record.path)

    def _commit_settings(self) -> None:
        """Saves the develop values now on screen into the current record.

        This must be called before moving to another shot. Otherwise the
        values just dialled in disappear silently.
        """
        if not self._dirty:
            return
        settings = self.panel.settings()
        self.record.develop = None if settings.is_neutral() else settings
        self._dirty = False
        self.records_changed.emit()

    def _load_current(self) -> None:
        from ..core.raw_io import is_editable_image

        self.panel.set_settings(self.record.develop or DevelopSettings())
        self._dirty = False
        basic = (self.record.develop or DevelopSettings()).basic
        # If a colour temperature was saved, demosaic **at that temperature
        # from the start**. Opening at as-shot and matching later means
        # demosaicing twice, and the screen in between is an approximation
        # that differs from the export - and if the slider is never touched
        # it stays that way forever.
        kelvin = int(basic.temperature) if basic.temperature > 0 else 0
        if is_editable_image(self.record.path):
            kelvin = 0      # no sensor data, so the demosaic ignores it
        self._load_base(basic.highlight_recovery, kelvin)
        self._load_context()
        self._prefetch_neighbours()
        if self.preview._crop_mode:
            # Stepped to another shot with the handles up: they still
            # framed the previous shot's rectangle, and a release would
            # have committed it into this one.
            self._on_crop_mode(True)
        if getattr(self, "_pick_target", ""):
            # The eyedropper was armed for the previous shot's fringing;
            # left armed, it swallowed the first click on this one.
            self.panel.clear_pick_mode()

    @staticmethod
    def _base_request(record) -> "tuple[Path, int, bool]":
        """What _load_current would demosaic for this record: the saved
        colour temperature (as-shot for a JPEG) and highlight setting."""
        from ..core.raw_io import is_editable_image

        basic = (record.develop or DevelopSettings()).basic
        kelvin = int(basic.temperature) if basic.temperature > 0 else 0
        if is_editable_image(record.path):
            kelvin = 0
        return record.path, kelvin, bool(basic.highlight_recovery)

    def _prefetch_neighbours(self) -> None:
        """Prepares the bases of the shots about to be stepped to: two
        ahead in the direction of travel, one behind."""
        self._cancel_prefetch()
        if full_render_in_flight():
            # A Full Render holds gigabytes; a half-size demosaic on top of
            # it is the overlap that kills a small PC. The next step
            # prefetches again.
            return
        offsets = (1, 2, -1) if self._step_direction >= 0 else (-1, -2, 1)
        jobs = [self._base_request(self.records[self.index + d])
                for d in offsets if 0 <= self.index + d < len(self.records)]
        jobs = [job for job in jobs
                if _cached_base(_base_key(*job)) is None]
        if not jobs:
            return
        worker = BasePrefetchWorker(jobs)
        self._prefetch_worker = worker
        worker.start()

    def _cancel_prefetch(self) -> None:
        """Lets go of the prefetch thread. It is not waited for: a
        demosaic under way finishes at its own pace, kept alive at module
        level like a retired render (destroying a running QThread is a
        crash)."""
        worker = self._prefetch_worker
        self._prefetch_worker = None
        if worker is None:
            return
        try:
            worker.cancel()
            if worker.isRunning():
                _detach_until_finished(worker)
        except RuntimeError:
            pass

    def _load_base(self, highlight_recovery: bool, kelvin: int = 0) -> None:
        """Builds the preview base (demosaic).

        Highlight recovery is a decode-stage option, so toggling it comes
        back through here as well - redrawing only the LUT, the way a slider
        does, does not carry it.

        kelvin is the target colour temperature for applying white balance in
        sensor linear (0 = as-shot). It only arrives after the slider settles
        (see _base_kelvin).
        """
        self._base_highlight = highlight_recovery
        # Set only on success. The fallback below is a camera-baked JPEG,
        # which has no sensor-linear WB applied; claiming that it does makes
        # the gains 1 and the white balance disappears entirely.
        self._base_kelvin = 0
        try:
            # The develop view uses a neutral image actually demosaiced from
            # the RAW. The embedded JPEG already has the camera picture style
            # (contrast, saturation, tone) baked in, so even with every
            # adjustment off it differs greatly from the real RAW. The
            # culling grid uses JPEG for speed, but here accuracy comes
            # first.
            # Preview or develop, only the RAW demosaic is used - the
            # embedded JPEG's colour and gradation are a camera render, so it
            # is never used here. Done at half-size for responsiveness (the
            # Full Render button goes to full resolution).
            # Ready already, when a neighbour was prepared ahead or the
            # shot was stepped back to (see _BASE_CACHE); built here
            # otherwise, and kept for the same reasons.
            key = _base_key(self.record.path, kelvin, highlight_recovery)
            ready = _cached_base(key) if key is not None else None
            if ready is None:
                ready = build_preview_base(self.record.path, kelvin,
                                           highlight_recovery)
                _store_base(key, ready)
            self._source, sensor_width = ready
            self._roi_scale = self._resolve_roi_scale(sensor_width)
            self._base_kelvin = kelvin
            self._degraded = False
            self._degraded_reason = ""
        except Exception as demosaic_exc:  # noqa: BLE001
            # For files where the demosaic fails outright (corrupt, wholly
            # unsupported), showing the embedded JPEG at least beats showing
            # nothing. Colour and gradation are not accurate, so we say so on
            # screen.
            try:
                full = load_preview(self.record.path)
                self._source = resize_long_edge(full, PREVIEW_LONG_EDGE)
                self._roi_scale = self._resolve_roi_scale(full.shape[1])
                self._degraded = True
                self._degraded_reason = self._explain_degraded()
            except Exception as exc:  # noqa: BLE001
                self._source = None
                self._roi_scale = 1.0
                self._degraded = False
                self.preview.set_pixmap(None)
                self.preview.set_message(
                    tr("Cannot open this file: {exc}\n(demosaic: {demosaic_exc})")
                    .format(exc=exc, demosaic_exc=demosaic_exc)
                )

    def _load_context(self) -> None:
        """The rest of switching shots - WB reference, lens lookup, overlay
        state.

        Unlike _load_base this runs **once per shot**. Rebuilding only the
        base from a highlight recovery toggle does not come back through here
        - refilling the lens candidate combo would throw away what the user
        picked.
        """
        self._maybe_warn_stale_roi()

        # Read the white balance for absolute Kelvin conversion and set the
        # slider default to this shot's as-shot colour temperature. **If it
        # cannot be read (JPEG, HEIF), fall back to the default** - without
        # that, the previous RAW's as-shot stays behind, and on the next JPEG
        # shot nudging the slider a single step applies a large colour move
        # against that reference (measured Rx0.77, Bx1.40).
        from .develop_panel import DEFAULT_KELVIN

        self._wb = read_white_balance(self.record.path)
        if self._wb is not None:
            self.panel.set_as_shot_kelvin(self._wb.as_shot_kelvin)
        else:
            self.panel.set_as_shot_kelvin(DEFAULT_KELVIN)

        # Tell up front whether a lens profile was found. Lenses missing from
        # the DB are common (measured: Tamron A069 not registered), so this
        # has to be known before automatic correction is switched on.
        from ..core.develop.optics import available_lenses, find_lens

        match = find_lens(self.record.metadata)
        self.panel.set_lens_info(match.summary, match.found)

        # The colour calibration display must show only this shot's camera
        meta = self.record.metadata
        self.panel.set_camera(
            getattr(meta, "camera_make", "") or "",
            getattr(meta, "camera_model", "") or "",
        )

        # JPEG and HEIF are the result of the camera already applying the
        # profile, the camera colour, and the lens correction. Sensor-based
        # items are locked (see set_raw_source).
        from ..core.raw_io import is_editable_image

        self.panel.set_raw_source(not is_editable_image(self.record.path))

        # Picking an index in the face mask needs to know how many were found
        focus = self.record.focus
        self.panel.set_face_count(len(focus.faces) if focus else 0)

        # Fill in the candidates so one can be picked by hand when the
        # automatic lookup fails.
        if not self.panel.lens_override.count():
            maker = None
            if self.record.metadata and self.record.metadata.camera_model:
                model = self.record.metadata.camera_model
                maker = "Sony" if model.startswith("ILCE") else None
            self.panel.lens_override.addItems(["", *available_lenses(maker=maker)])

        # Focus data comes from the cache. Without it, the toggles go off.
        has_focus = self.record.focus is not None and self.record.focus.roi is not None
        has_faces = bool(self.record.focus and self.record.focus.faces)
        self.show_roi.setEnabled(has_focus)
        self.show_faces.setEnabled(has_faces)
        self.show_eyes.setEnabled(has_faces)
        # AF only means anything when the file recorded it, but knowing that
        # means reading the file (_source is async, so it is not here yet).
        # If there is metadata we leave it enabled, and when there is nothing
        # to draw the tooltip is what explains it.
        self.show_af.setEnabled(self.record.metadata is not None)
        self.focus_zoom_button.setEnabled(has_focus)
        self.preview.reset_view()
        self._on_zoom(1.0)
        self.show_roi.setToolTip(
            tr("The region used for grading — green box (F)") if has_focus
            else tr("This shot has no analysis data")
        )
        self._eye_contours = None  # re-measured when the shot changes
        self._af_box = _AF_UNREAD  # re-read when the shot changes

        # If it is switched on in preferences, lay the camera look down as a
        # starting point, but only on shots with no develop at all. It has to
        # happen before _render() for the first frame to show matched values.
        self._maybe_auto_camera_match()

        self._refresh_header()
        self._render()

    # ---------------------------------------------------- Camera look matching

    def _fit_camera_match(self) -> DevelopSettings | None:
        """Computes the camera look match settings for the current shot.
        None if it cannot.

        Fits exposure, tone curve, and saturation against the embedded JPEG
        (the camera render) as the answer key, and returns them as **ordinary
        values that go onto the sliders** (core/develop/camera_look.py). No
        failure may block the window - a convenience that sets a starting
        point getting in the way of opening at all turns the point on its
        head.
        """
        from ..core.develop import camera_look
        from ..core.raw_io import is_editable_image

        if (self._source is None or self._degraded
                or is_editable_image(self.record.path)):
            return None
        try:
            target = load_preview(self.record.path)
            # working=self._source: runs the fit and the check through the
            # real screen path (working space applied -> sRGB conversion).
            # Fitting on display values alone leaves the curve out of step
            # with the space it is actually applied in, and colour is left
            # behind (measured R/G 12%).
            return camera_look.match_settings(
                to_display(self._source), target, base=self.panel.settings(),
                wb=self._wb.engine_wb if self._wb else None,
                working=self._source,
            )
        except Exception:  # noqa: BLE001 - preview may be absent or broken
            log.debug("카메라 룩 매칭 실패: %s", self.record.path.name,
                      exc_info=True)
            return None

    def _match_camera_look(self) -> None:
        """The 'Match camera JPEG' button - puts the fit onto the sliders.

        It changes only exposure, saturation, and the tone curve, leaving the
        rest of the edits - detail, masks, crop - alone (the contract of
        camera_look.match_settings).
        """
        matched = self._fit_camera_match()
        if matched is None:
            reason = (
                tr("RAW demosaic failed here, so the screen already shows "
                   "the embedded JPEG — there is nothing to match.")
                if self._degraded else
                tr("Could not read this shot's embedded JPEG to match against.")
            )
            self.info.setText(
                f"<b>{self.record.path.name}</b> · "
                f"<span style='color:{theme.WARNING}'>{reason}</span>"
            )
            return
        self.panel.set_settings(matched)
        # A leftover preset name makes the on-screen values read as that
        # preset
        self.panel.preset_bar.mark_modified()
        self._on_settings_changed()

    def _measure_lens_profile(self) -> None:
        """The 'Measure vignetting from this camera JPEG' button.

        Measures this shot, stores the profile in the user lens DB, reloads
        the DB and refreshes the lens line - so a lens that read "not in
        the DB" a second ago now reads as found, and automatic vignetting
        correction has something to apply.
        """
        from ..core.develop import lens_profile
        from ..core.develop.optics import find_lens
        from ..core.raw_io import is_editable_image

        def warn(text: str) -> None:
            self.panel.set_lens_profile_note(text, ok=False)
            self.info.setText(
                f"<b>{self.record.path.name}</b> · "
                f"<span style='color:{theme.WARNING}'>{text}</span>")

        if (self._source is None or self._degraded
                or is_editable_image(self.record.path)):
            warn(tr("Only a RAW with a readable embedded JPEG can be measured."))
            return
        # The distortion fit runs lensfun a few dozen times; say so. A
        # direct repaint, not processEvents - pumping the loop from inside
        # a handler runs whatever is queued (the deferred base load among
        # it), which is how a measurement once found its source gone.
        self.preview.set_busy(True)
        self.preview.repaint()
        try:
            measured = lens_profile.measure_photo(
                self.record.path, to_display(self._source), self.record.metadata)
        except Exception as exc:  # noqa: BLE001 - a helper must not take the window down
            warn(tr("Measurement failed: {error}").format(error=exc))
            return
        finally:
            self.preview.set_busy(False)
        if measured is None:
            warn(tr("This file has no lens, focal length or aperture to file "
                    "the profile under."))
            return
        if not measured.usable:
            warn(tr("Not enough mid-tones out to the corners in this frame "
                    "(reach {reach:.0%}). Pick a shot lit evenly to the "
                    "edges and try again.").format(reach=measured.reach))
            return

        path = lens_profile.store(measured, self.record.metadata)
        match = find_lens(self.record.metadata)
        self.panel.set_lens_info(match.summary, match.found)
        distortion = measured.distortion
        if distortion is not None and distortion.usable:
            geometry = tr("distortion fitted to {residual:.1f}px").format(
                residual=distortion.residual_px)
        elif distortion is not None and distortion.patches < lens_profile.MIN_PATCHES:
            geometry = tr("distortion not measured - too little texture")
        else:
            geometry = tr("distortion not measured")
        self.panel.set_lens_profile_note(tr(
            "Saved: {lens} at {focal:g}mm f/{aperture:g} - corners "
            "brightened x{gain:.2f}, {geometry}. Profile: {file}"
        ).format(lens=measured.lens, focal=measured.focal,
                 aperture=measured.aperture, gain=measured.corner_gain,
                 geometry=geometry, file=path.name))
        self._on_settings_changed()

    def _maybe_auto_camera_match(self) -> None:
        """Applies automatically if 'Start from camera look' is on in
        preferences.

        **If the shot carries even one adjustment, it is never touched.** An
        automatic feature overwriting the user's edits is the end of trust.
        Preview-only windows (fast) have no panel, so they are excluded.
        """
        from ..core import state

        if self._fast or not state.camera_match_on_open():
            return
        # While an export is running (set_locked), record.develop must not be
        # changed by any path - including _load_current running again from a
        # shot change.
        if getattr(self, "_locked", False):
            return
        current = self.record.develop
        if current is not None and not current.is_neutral():
            return
        matched = self._fit_camera_match()
        if matched is None:
            return
        self.panel.set_settings(matched)
        self.panel.preset_bar.mark_modified()
        # The starting point has to be saved too for what you see to be what
        # you get - close the window without doing anything else and the
        # values now on screen are exactly what the export uses.
        self._dirty = True

    def _refresh_header(self) -> None:
        record = self.record
        self.setWindowTitle(tr("Develop — {name}").format(name=record.path.name))

        parts = [f"<b>{record.path.name}</b>",
                 tr("Score {score:.1f}").format(score=record.score)]
        if record.focus:
            parts.append(
                tr("ROI sharpness {value:.1f}").format(value=record.focus.sharpness))
            parts.append(
                tr("Frame {value:.1f}").format(value=record.focus.frame_sharpness))
        if record.metadata:
            meta = record.metadata
            for value in (meta.lens_model, f"ISO {meta.iso}" if meta.iso else None,
                          meta.shutter_display,
                          f"f/{meta.aperture:g}" if meta.aperture else None):
                if value:
                    parts.append(value)

        text = " · ".join(parts)
        if record.reasons:
            text += ("<br><span style='color:#999'>"
                     + " / ".join(render_all(record.reasons)) + "</span>")
        if getattr(self, "_degraded", False):
            # Say why if we can tell. Just "failed" reads as a broken file,
            # but often the RAW is perfectly fine and merely uses a vendor
            # proprietary compression we cannot decode (Nikon High
            # Efficiency and the like).
            reason = self._degraded_reason or tr(
                "RAW demosaic failed — showing the embedded JPEG"
                " (colour and tone may not be accurate)"
            )
            text += f"<br><span style='color:{theme.WARNING}'>⚠ {reason}</span>"
        self.info.setText(text)

        self.position_label.setText(f"{self.index + 1} / {len(self.records)}")
        self.prev_button.setEnabled(self.index > 0)
        self.next_button.setEnabled(self.index < len(self.records) - 1)

        for grade, button in self.grade_buttons.items():
            button.setChecked(record.final_grade == grade)

    # ------------------------------------------------------------ Rendering

    def _on_settings_changed(self) -> None:
        self._dirty = True

        # The ratio combo lives in the panel, but the crop rectangle it
        # constrains lives in the preview. While crop mode is up, a change
        # has to reach the preview here - set_ratio used to run only on
        # entering crop mode, so picking a ratio with the handles already
        # showing did nothing. Guarded on a real change: set_ratio commits
        # through this very handler, and re-laying out on every pass would
        # loop.
        geometry = self.panel.settings(gated=False).geometry
        wanted = self._ratio_value(geometry.ratio)
        if self.preview._crop_mode:
            # A ratio just picked, a quarter turn or a crop slider that
            # took the crop off its ratio, a slider nudged by hand: mirror
            # and fit (a fitting crop is left alone, so the commit this
            # makes cannot loop back here).
            if wanted != self.preview._ratio:
                self.preview.set_ratio_only(wanted)
            self._fit_crop_to_ratio()
        else:
            # Whatever moved - the combo, a quarter turn that took the crop
            # off a fixed ratio, an old file's crop - the sliders are made
            # to agree with what the engine cuts. Cheap when they already
            # do (the common case), and the commit it makes when they do
            # not comes back here to find them agreeing.
            self._apply_ratio_to_settings()
        self._ratio_seen = wanted

        # Highlight recovery is a decode-stage option, so reapplying the LUT
        # does not carry it - the base (half demosaic) is rebuilt. It is the
        # same blocking call as opening a shot (0.5~2s), and a toggle is not
        # hammered the way a slider is, so it is left as it is. The Full
        # Render cache was built with the old value too, so it goes as well.
        flag = self.panel.settings().basic.highlight_recovery
        if flag != self._base_highlight and self._source is not None \
                and not self._degraded:
            self._drop_demosaic()
            self._load_base(flag, self._base_kelvin)

        # Colour temperature is a decode stage too. But it is a slider, so it
        # gets hammered, and demosaicing again right away (1.2s) would stall
        # the interaction. We wait until the hand comes off and do it once -
        # until then, gains on the old base carry the preview.
        #
        # Only applied for RAW. JPEG and HEIF have nothing to demosaic, so
        # even if the timer fires _settle_white_balance returns immediately.
        if (self._source is not None and not self._degraded
                and self._wb is not None):
            self._settle_timer.start()

        # A render takes about 200ms. Without saying so it looks hung.
        self.preview.set_busy(True)
        self._render_timer.start()

        # Once a value changes, an in-flight Full Render's result is already
        # stale. We stop it at once and fall back to the fast preview. It
        # used to only cancel and reschedule, so while the slider kept
        # moving, heavy renders rose and fell over and over and the
        # interaction got heavy.
        self._stop_full_render_for_edit()
        self._schedule_full_render()

    def _settle_white_balance(self) -> None:
        """The colour temperature settled. Demosaic again at that value, in
        sensor linear.

        Getting here turns the screen's white balance from approximate into
        exact - a measured error of 5.8~9.2 levels becomes 0.00 (see
        _wb_gain). The picture shifts slightly, and that shift is precisely
        how wrong the approximation was.

        Nothing to do if it is not RAW. Editable images have no demosaic at
        all, and their white balance is nothing but gains on the picture.
        """
        from ..core.raw_io import is_editable_image

        if self._source is None or self._degraded or self._locked:
            return
        if is_editable_image(self.record.path) or self._wb is None:
            return

        wanted = int(self.panel.settings().basic.temperature)
        if wanted <= 0:
            wanted = 0                      # back to as-shot
        if wanted == self._base_kelvin:
            return

        # This stalls for about 1.2s (half re-demosaic). It is the same
        # blocking call as opening a shot or toggling highlight recovery, and
        # it runs once, after the hand comes off. Without saying so it looks
        # hung.
        self.preview.set_busy(True)
        self._drop_demosaic()
        self._load_base(self._base_highlight, wanted)
        self._render()
        self._schedule_full_render()

    def set_locked(self, locked: bool, reason: str = "") -> None:
        """Locks editing. Used while an export is running.

        The export worker reads these records' `develop` and grades one shot
        at a time. Change a value in between and the earlier shots go out
        with the old settings and the later ones with the new, so colour
        splits within a single batch. The undo log stops matching reality
        too. This is not something that cannot be prevented; it is something
        that must be.
        """
        self._locked = locked
        if locked:
            # The picture is an editor too: a handle, the brush or the
            # eyedropper would commit past the disabled panel.
            self._leave_other_modes(keep="")
        self.preview.set_locked(locked)
        self.panel.setEnabled(not locked)
        for button in self.grade_buttons.values():
            button.setEnabled(not locked)
        for button in (self.apply_all_button, self.queue_button,
                       self.export_button):
            button.setEnabled(not locked)
        if locked:
            self._abandon_render()
            self.final_button.setEnabled(False)
        else:
            self.final_button.setEnabled(
                not self._full_render_lock.isActive())

        self.lock_notice.setText(reason)
        self.lock_notice.setVisible(bool(locked and reason))

    def _stop_full_render_for_edit(self) -> None:
        """On an edit, folds the Full Render at once and returns to preview.

        The mode itself stays on - once the hand comes off,
        _full_render_timer draws at full quality again. What is switched off
        here is only 'the job running right now'.
        """
        if (self._final_worker is None and not self._waiting_for_slot
                and not self._full_render_timer.isActive()):
            return
        self._abandon_render()
        self._set_full_render_state(busy=False)
        self.preview.set_busy(False)

    def _on_crop_mode(self, enabled: bool) -> None:
        # Leaving: nothing to push. The handles reported every drag into
        # the sliders and the release committed it; pushing the ratio in
        # again here is what used to lay the placed crop out afresh.
        if enabled:
            self._leave_other_modes(keep="crop")
        self.preview.set_crop_mode(enabled)
        # The editing frame first - the whole photo, no crop, no strip.
        # The ratio is measured against the picture on screen, and until
        # this render the screen still shows the *finished* frame: the
        # previous crop with the info strip under it. Fitted against that,
        # a 1:1 crop on a 3:2 photo came out as the full frame.
        self._render()
        if not enabled:
            return
        # Ungated on purpose. Crop mode edits the stored rectangle and its
        # ratio, and the ratio is a constraint on editing rather than an
        # operation on pixels - so a saved ratio leaves geometry "neutral",
        # the section opens switched off, and the gated view hands back
        # FREE. Laying the crop out from here commits it (crop_finished),
        # which is what switches the section on.
        settings = self.panel.settings(gated=False).geometry
        # What Cancel puts back: the rectangle and the ratio as saved, before
        # the fit below touches an old file's crop.
        self._crop_before = (
            (settings.crop_left, settings.crop_top,
             settings.crop_right, settings.crop_bottom),
            settings.ratio,
        )
        self.preview.set_crop(
            settings.crop_left, settings.crop_top,
            settings.crop_right, settings.crop_bottom,
        )
        self.preview.set_ratio_only(self._ratio_value(settings.ratio))
        self._fit_crop_to_ratio()

    def _cancel_crop(self) -> None:
        """The Cancel button (or Esc) with the handles up: the crop and its
        ratio go back to what they were when the handles came up, and the
        handles go down. Every release in between was committed, so this is
        a restore, not a discard - the sliders, the record and the picture
        all follow through one commit."""
        if self.preview._crop_mode and self._crop_before is not None:
            crop, ratio = self._crop_before
            self.panel.set_ratio_silently(ratio)
            self._on_crop_dragged(*crop)
            self.preview.set_crop(*crop)
        self.panel.crop_mode_button.setChecked(False)
        self.panel.commit_external_edit()

    def keyPressEvent(self, event) -> None:
        """Enter applies and Esc cancels while the crop handles are up.
        A dialog closes on Esc, and closing the loupe was what Esc did in
        the middle of a crop."""
        if self.preview._crop_mode:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self.panel.crop_apply_button.click()
                event.accept()
                return
            if event.key() == Qt.Key_Escape:
                self.panel.crop_cancel_button.click()
                event.accept()
                return
        super().keyPressEvent(event)

    def _fit_crop_to_ratio(self) -> None:
        """Crop mode: bring the crop onto the ratio, in the frame the crop
        applies to - the source after the quarter turns, from the settings
        - and push the result to the handles and the sliders alike.

        Not the frame on screen: the render can be a step behind (a
        quarter turn just made is drawn by a timer) and it used to be the
        finished frame, cropped and with the strip, on entering. And not
        the handles' rectangle alone: a slider nudged by hand lives in the
        panel only until it is mirrored here. The sliders round to a
        percent, so the exact rectangle wins when the two agree to that.
        """
        from ..core.develop import crop_ratio

        geometry = self.panel.settings(gated=False).geometry
        wanted = self._ratio_value(geometry.ratio)
        frame = self._frame_ratio()
        panel_crop = (geometry.crop_left, geometry.crop_top,
                      geometry.crop_right, geometry.crop_bottom)
        preview_crop = self.preview.crop()
        agree = max(abs(a - b) for a, b in zip(panel_crop, preview_crop)) <= 0.006
        crop = preview_crop if agree else panel_crop
        if not agree:
            self.preview.set_crop(*panel_crop)
        if not wanted or frame is None or crop_ratio.fits(crop, wanted, frame):
            return
        if self._fitting_depth >= self._FIT_DEPTH_LIMIT:
            return
        fitted = crop_ratio.fit(crop, wanted, frame)
        self._fitting_depth += 1
        try:
            self.preview.set_crop(*fitted)
            self._on_crop_dragged(*fitted)
            self.panel.commit_external_edit()
        finally:
            self._fitting_depth -= 1

    def _ratio_value(self, ratio) -> float | None:
        """Ratio setting -> an actual number.

        ORIGINAL is the source aspect ratio **as it stands after the quarter
        turns** - the frame the crop is applied to. It is not in the fixed
        table, so it used to behave quietly like 'free', and then it read
        the unrotated source, so a turned photo got the wrong way round.
        """
        from ..core.develop import CropRatio

        if ratio is CropRatio.ORIGINAL:
            frame = self._frame_ratio()
            return frame
        return ratio.value_ratio if ratio else None

    def _frame_ratio(self) -> float | None:
        """Width / height of the frame the crop applies to: the source
        after the quarter turns (see crop_ratio.frame_ratio)."""
        from ..core.develop import crop_ratio

        if self._source is None:
            return None
        height, width = self._source.shape[:2]
        quarters = self.panel.settings(gated=False).geometry.rotate_quarters
        return crop_ratio.frame_ratio(width, height, quarters)

    _FIT_DEPTH_LIMIT = 8

    def _apply_ratio_to_settings(self) -> None:
        """The ratio combo changed while crop mode is off: fit the stored
        crop to it in frame space and commit, so the picture follows the
        combo at once instead of waiting for the next visit to crop mode.
        """
        from ..core.develop import crop_ratio

        geometry = self.panel.settings(gated=False).geometry
        ratio = self._ratio_value(geometry.ratio)
        frame = self._frame_ratio()
        if not ratio or frame is None:
            return
        crop = (geometry.crop_left, geometry.crop_top,
                geometry.crop_right, geometry.crop_bottom)
        if crop_ratio.fits(crop, ratio, frame):
            return
        fitted = crop_ratio.fit(crop, ratio, frame)
        # The sliders hold whole percents. A small crop can sit further
        # off its ratio than the tolerance and still round back to the
        # very same percents - pushing those in and committing came
        # straight back here, and again, until the stack overflowed. The
        # engine snaps the cut exactly whatever the sliders hold, so when
        # the fit changes nothing at slider precision there is nothing to
        # commit; and a fit is never started from inside its own commit.
        if all(round(a * 100.0) == round(b * 100.0) for a, b in zip(fitted, crop)):
            return
        if self._fitting_depth >= self._FIT_DEPTH_LIMIT:
            return
        self._fitting_depth += 1
        try:
            self._on_crop_dragged(*fitted)
            self.panel.commit_external_edit()
        finally:
            self._fitting_depth -= 1

    def _commit_external_edit(self) -> None:
        """The end of a crop or shape drag. Goes through the panel so a
        switched-off section wakes - see DevelopPanel.commit_external_edit.
        (A forwarder: the preview is wired up before the panel exists.)"""
        self.panel.commit_external_edit()

    def _on_crop_dragged(self, left: float, top: float, right: float, bottom: float) -> None:
        """Reflects the result of a drag on the image onto the sliders.

        No render happens here - redrawing on every pixel of the drag cannot
        keep up. We draw once, at the moment of release (crop_finished).
        """
        for key, value in (
            ("geo.crop_left", left * 100.0), ("geo.crop_top", top * 100.0),
            ("geo.crop_right", right * 100.0), ("geo.crop_bottom", bottom * 100.0),
        ):
            self.panel.rows[key].set_value(value, silent=True)
        self._dirty = True

    def _leave_other_modes(self, keep: str) -> None:
        """One mode on the picture at a time. Each one sets its own cursor
        and grabs the press first, so two at once left the crop rectangle
        painting under the brush, or the eyedropper swallowing the click
        that was meant to set the main face. Leaving crop mode applies
        the crop (every release was committed); the others just stop."""
        if keep != "crop" and self.preview._crop_mode:
            self.panel.crop_mode_button.setChecked(False)
        if keep != "brush" and self.panel.brush_paint.isChecked():
            self.panel.brush_paint.setChecked(False)
        if keep != "pick" and getattr(self, "_pick_target", ""):
            self.panel.clear_pick_mode()

    def _on_pick_mode(self, key: str) -> None:
        if key:
            self._leave_other_modes(keep="pick")
        self._pick_target = key
        self.preview.set_pick_mode(bool(key))
        if key:
            label = tr("purple") if key == "purple" else tr("green")
            self.info.setText(
                f"<b>{self.record.path.name}</b> · "
                f"<span style='color:#7fb3ff'>"
                + tr("Click on the {label} fringing").format(label=label)
                + "</span>"
            )
        else:
            self._refresh_header()

    def _on_color_picked(self, rx: float, ry: float) -> None:
        """Passes the hue at the point picked in the preview to the panel."""
        target = getattr(self, "_pick_target", "")
        if not target or self._source is None:
            return

        from ..core.develop.optics import sample_hue

        height, width = self._source.shape[:2]
        hue = sample_hue(
            self._source, int(rx * width), int(ry * height)
        )
        self.panel.set_sampled_hue(target, hue)
        self._pick_target = ""
        self.preview.set_pick_mode(False)
        self._refresh_header()

    def _on_clipping(self, shadow: bool, highlight: bool) -> None:
        self._clip_flags = (shadow, highlight)
        self._refresh_clip_label()

    def _refresh_clip_label(self) -> None:
        """The warning text and the actual pixel percentages.

        The text used to be refreshed **only when** the clipping state
        changed. So there was no message at the moment the overlay was
        switched on, and when no colour appeared on screen there was no way
        to tell a fault from genuinely having no clipping.
        """
        shadow, highlight = getattr(self, "_clip_flags", (False, False))
        warnings = []
        if shadow:
            warnings.append(tr("Shadows crushed"))
        if highlight:
            warnings.append(tr("Highlights blown"))

        if any(self._clip_overlay_state()) and self._clip_base is not None:
            crushed, blown = clip_counts(self._clip_base)
            total = self._clip_base.shape[0] * self._clip_base.shape[1] or 1
            if not (crushed or blown):
                warnings.append(tr("No clipped pixels to show"))
            else:
                warnings.append(
                    tr("crushed {crushed:.2f}% · blown {blown:.2f}%").format(
                        crushed=crushed / total * 100,
                        blown=blown / total * 100,
                    )
                )
        self.clip_label.setText(" · ".join(warnings))

    def _mask_editing_active(self) -> bool:
        """Whether a mask is being viewed or handled - while it is, the
        screen shows the scene frame.

        True when the shape handles are up, when the brush is painting, or
        when the red region overlay is on. Mask coordinates are all relative
        to the scene (before cropping), so showing the cropped frame at this
        point puts the grabbed coordinates out of step from the start (see
        _render).
        """
        if self.preview._crop_mode:
            # Crop mode wins: the handles are hidden and grab nothing, and
            # the crop rectangle lives in the frame after the turns. With a
            # radial mask still selected, the screen used to drop the
            # turns as well, and the rectangle sat on the unturned picture.
            return False
        if getattr(self.preview, "_shape_kind", None) is not None:
            return True
        if getattr(self.preview, "_brush_mode", False):
            return True
        return self.panel.overlay_mask() is not None

    def _render(self) -> None:
        if self._source is None:
            return

        # If the display-frame decision changed (entering/leaving mask or
        # crop editing), fold the in-flight Full Render. That render was
        # built with the previous frame, so the moment it arrives it covers
        # the screen being edited. Every transition path
        # (_sync_mask_shape, the region overlay toggle, the brush, crop mode)
        # comes through here, so this one place catches it.
        frame = (self.preview._crop_mode, self._mask_editing_active())
        if frame != self._rendered_frame:
            self._rendered_frame = frame
            self._stop_full_render_for_edit()
            self._schedule_full_render()

        settings = self.panel.settings()

        if self.before_after.isChecked():
            image = self._source
            shown_geometry = GeometrySettings()
            if self.preview._crop_mode:
                # The handles live in the crop frame - the source after
                # the turns, flips and straighten. Shown raw, the rectangle
                # sat on the unturned picture.
                shown_geometry = replace(settings.geometry, **_FULL_CROP)
                image = engine.apply_geometry(image, shown_geometry)
        else:
            # In crop mode we show the image without applying the crop.
            # Drawing the cut result leaves no way to re-set the crop bounds
            # on top of it.
            if self.preview._crop_mode:
                settings = replace(
                    settings, geometry=replace(settings.geometry, **_FULL_CROP)
                )

            # **While a mask is viewed or handled, geometry is released
            # entirely.**
            #
            # Mask coordinates are relative to the scene (before cropping).
            # If the screen shows the cropped frame, the coordinates grabbed
            # by dragging and the red region overlay all become relative to
            # that frame, out of step with where the engine applies them -
            # this was why drawing a new mask on a cropped photo landed
            # somewhere else.
            #
            # Releasing only the crop is not enough. Rotation and straighten
            # change the coordinate system too, so leaving them in puts it
            # out of step by that much. So all of it is released - a photo
            # with a rotation applied appears in its original orientation
            # while the mask is being handled, which is the same kind of
            # price crop mode pays by showing the whole frame while editing.
            if self._mask_editing_active():
                settings = replace(settings, geometry=GeometrySettings())
            # Adjustments only - no watermark and no info strip. If the info
            # strip's black bar or the watermark text mixes in, the histogram
            # and the clipping warnings stop reflecting the photo's gradation
            # and it looks as if the adjustment values changed. The markings
            # are laid on separately below.
            image = engine.apply_settings(
                self._source,
                replace(settings, watermark=WatermarkSettings(),
                        exif_strip=ExifStripSettings()),
                self.record.path, self.record.metadata,
                wb=self._wb.engine_wb if self._wb else None,
                main_face_box=self.record.main_face_norm,
                base_kelvin=self._base_kelvin,
                output_space="srgb",
            )

        # Record the geometry **actually applied to the screen**, for the
        # overlay coordinate conversion. The before/after comparison is the
        # source as-is, so it has no geometry.
        # The crop as the cut really made it (snapped to the ratio), not
        # the sliders' whole percents - or the overlays land a percent off
        # inside a small crop.
        self._display_geometry = (shown_geometry
                                  if self.before_after.isChecked()
                                  else engine.exact_geometry(settings.geometry,
                                                             *self._source.shape[:2]))

        # The before/after source is working-space float, so it is moved to
        # display here; the adjusted render was already moved by the engine
        # via output_space="srgb" (uint8 passes through).
        image = to_display(image)
        self.histogram.set_image(image)
        # Lay the same histogram behind the curve editor as well. Seeing
        # which gradations you are touching is what makes it possible to drag
        # the curve accurately.
        self.panel.set_curve_histogram(self.histogram.luminance())

        # The clipping overlay goes on after the histogram is computed (the
        # histogram must reflect the real image; the painting is for
        # display). It has to blink, so the state just before painting is
        # held separately - re-running the develop pipeline every time would
        # burn hundreds of ms twice a second.
        self._clip_base = image
        show_shadow, show_highlight = self._clip_overlay_state()
        if (show_shadow or show_highlight) and self._clip_blink_on:
            image = clip_overlay(image, show_shadow, show_highlight)
        self._refresh_clip_label()

        # Screen markings (the ROI box, the red mask region) go onto the
        # photo itself, before anything that changes its size. The info
        # strip is appended below the photo; drawn after it, the ROI was
        # scaled against photo-plus-strip and sat too high.
        if self._any_overlay_on() and self.record.focus:
            image = self._draw_roi(image)

        overlay_mask = self.panel.overlay_mask()
        if overlay_mask is not None and not self.preview._crop_mode:
            # Scene coordinates on the crop frame would be off by the turns
            image = self._draw_mask_overlay(image, overlay_mask)

        # The output markings (watermark, info strip) go on last, and only
        # when the screen shows the finished frame - see
        # _shows_finished_frame.
        if self._shows_finished_frame():
            image = engine.apply_overlays(
                image, settings, self.record.path, self.record.metadata
            )

        self.preview.set_pixmap(bgr_to_pixmap(image))
        self.preview.set_busy(False)

    def _full_render_target(self) -> int:
        """The long-edge pixel count the screen actually needs right now.

        Pulling a fixed 2200px falls short when the window is large or zoomed
        in, and is waste when the window is small. We build only viewport
        size x zoom (x device pixel ratio). If that goes past the source,
        resize_long_edge stops at the source.
        """
        viewport = max(self.preview.width(), self.preview.height())
        try:
            ratio = float(self.preview.devicePixelRatioF())
        except AttributeError:
            ratio = 1.0
        needed = int(viewport * max(1.0, self.preview.zoom()) * max(1.0, ratio))
        return max(PREVIEW_LONG_EDGE, needed)

    def _set_full_render_state(self, busy: bool) -> None:
        """Decides the Full Render button's text, colour, and enabled state
        in one place.

        There are four states.

        - Off (grey): press to start
        - On (blue): the result is on screen
        - Rendering (orange, locked): running right now. The lock keeps
          hammering from overlapping renders - overlapping doubles memory
          (measured 2.8GB -> 5.5GB) and kills a small PC outright.
        - Waiting (orange, locked): the previous render has not finished, so
          this one cannot set off
        """
        if busy:
            self.final_button.setText(
                tr("Waiting…") if self._final_worker is None else tr("Rendering…"))
            self.final_button.setStyleSheet(theme.BUSY_BUTTON)
        else:
            self.final_button.setText("Full Render")
            self.final_button.setStyleSheet(theme.TOGGLE_BUTTON)

        # The lock timer alone decides the enabled state. Switching *off*
        # must stay allowed even while a render runs - it only discards the
        # result, so it is not dangerous, and being trapped in a 5~8s render
        # looks like a hang in its own right.
        # Overlapping runs are blocked by `_FULL_RENDER_SLOT`, not the button.
        self.final_button.setEnabled(not self._full_render_lock.isActive())

    def _lock_full_render_button(self) -> None:
        """Locks the button briefly. Keeps hammering from overlapping heavy
        renders."""
        self.final_button.setEnabled(False)
        self._full_render_lock.start(FULL_RENDER_LOCKOUT_MS)

    def _release_full_render_button(self) -> None:
        """Unlock. The text and colour are left in their current state."""
        self.final_button.setEnabled(True)

    def _on_full_render_toggled(self, enabled: bool) -> None:
        """Full Render mode on/off."""
        self._lock_full_render_button()
        if enabled:
            self._set_full_render_state(busy=False)
            self._schedule_full_render()
        else:
            self._abandon_render()
            # Switching off also releases the held demosaic source (about
            # 390MB). Turning it back on costs 5s more, but holding that much
            # memory while it is unused is worse - on an 8GB PC it is a
            # burden in itself.
            self._drop_demosaic()
            self._set_full_render_state(busy=False)
            self._render()  # back to the fast preview

    def _schedule_full_render(self) -> None:
        """Redraws at full quality once the controls stop.

        Developing at full resolution on every slider move makes it
        impossible to work, so it runs only in the brief quiet after the hand
        comes off.
        """
        if not self.final_button.isChecked():
            return
        self._abandon_render()
        self._full_render_timer.start()

    def _abandon_render(self) -> None:
        """Folds the schedule and discards the running render's result.

        **It cannot stop it.** cancel() only raises a flag, and the rawpy
        demosaic is a single C call with no point at which it can see that
        flag. So the thread keeps running for several more seconds, holding
        its memory. To keep the next render from overlapping on top of it,
        `_FULL_RENDER_SLOT` holds on to the end and then drops itself.
        """
        self._full_render_timer.stop()
        self._waiting_for_slot = False
        worker = self._final_worker
        self._final_worker = None
        if worker is not None:
            worker.cancel()
            self._retire_worker(worker)

    def _retire_worker(self, worker: "FinalRenderWorker") -> None:
        """Holds a cancelled worker until its thread finishes.

        cancel() only raises a flag - the thread keeps running until it
        checks that flag. Drop the last reference in that state and Python
        destroys the QThread, and Qt treats "a running thread was destroyed"
        as a fatal error and kills the process instantly with qFatal()
        (Qt6Core, 0xc0000409).

        This path actually crashed. Move a slider with Full Render on and
        _schedule_full_render comes through here every time.

        source_ready is disconnected as well. Disconnecting only the result
        (done) lets a retired worker push its demosaic source into the window
        later, and the next render reuses stale pixels.
        """
        _disconnect_worker(worker)
        if not worker.isRunning():
            return
        self._retired_workers.append(worker)
        worker.finished.connect(self._reap_workers)

    def _shutdown_workers(self) -> None:
        """Releases the renders this window held. Used by both close and quit.

        It does not wait. Threads still running are handed to module level
        (with the reference kept alive) and left to finish at their own pace.
        Waiting freezes the window when the rawpy demosaic runs long, and a
        wait that falls short crashes.
        """
        self._cancel_prefetch()
        current = self._final_worker
        self._final_worker = None
        retired = self._retired_workers
        self._retired_workers = []

        for worker in ([current] if current is not None else []) + retired:
            try:
                worker.cancel()
                # The result is no longer used. Only finished is left in
                # place, so it drops itself from the list.
                _disconnect_worker(worker)
                if worker.isRunning():
                    _detach_until_finished(worker)
            except RuntimeError:
                pass  # already cleaned-up object

    def _reap_workers(self) -> None:
        """Clears finished workers out of the list."""
        self._retired_workers = [
            worker for worker in self._retired_workers if worker.isRunning()
        ]

    def _show_final_preview(self) -> None:
        """Builds and shows the Full Render result from a RAW demosaic."""
        if self._source is None or not self.final_button.isChecked():
            self._waiting_for_slot = False
            return
        if self._final_worker is not None:
            return  # already building

        # If the previous render still holds memory we do not set off.
        # Overlapping turns 2.8GB into 5.5GB at 27MP, and a small PC dies
        # here. We only schedule a retry for as soon as it finishes.
        if full_render_in_flight():
            self._waiting_for_slot = True
            self._set_full_render_state(busy=True)
            self.preview.set_busy(True)
            self._slot_timer.start(200)
            return

        self._waiting_for_slot = False
        settings = self.panel.settings()

        # Built with **the same frame** as the screen (_render). During crop
        # editing the crop is released, and while a mask is viewed or handled
        # all geometry is released. Leave geometry in place only here and the
        # moment the result arrives the screen being edited turns into the
        # cropped frame, out of step with the coordinates the rubber band and
        # the handles assume.
        if self.preview._crop_mode:
            settings = replace(
                settings, geometry=replace(settings.geometry, **_FULL_CROP))
        if self._mask_editing_active():
            settings = replace(settings, geometry=GeometrySettings())

        # When zoomed in we build only what is visible. At 1:1 the whole
        # frame is visible, so cutting gains nothing and fitting the cut
        # result to the screen is only a nuisance.
        #
        # We cut even with lens correction on - because the worker applies
        # the optical correction **before cutting**
        # (engine.apply_optics_stage). It used to apply it after cutting, so
        # zooming in bent the picture in the Full Render only.
        #
        # **With a mask present we do not cut.** Mask coordinates are
        # relative to the scene, but cutting only the visible region here
        # makes apply_settings treat that piece as the whole scene when it
        # applies the mask - the mask moves, but only in a zoomed Full
        # Render. The face box is re-based on the piece (_remap_box), but
        # radial, linear, and brush masks carry their coordinates as
        # parameters and cannot be. Even at the cost of a whole-frame render,
        # the correct picture comes first.
        region = None
        if (self.preview.zoom() > 1.01 and settings.geometry.is_neutral()
                and not settings.masks):
            region = self.preview.visible_region()
        self._final_region = region

        self._final_generation += 1
        worker = FinalRenderWorker(
            self.record.path, settings, self._wb.engine_wb if self._wb else None,
            target_long_edge=self._full_render_target(),
            generation=self._final_generation,
            source=self._demosaic_for(self.record.path),
            region=region,
            main_face_box=self.record.main_face_norm,
            metadata=self.record.metadata,
            base_kelvin=self._base_kelvin,
        )
        worker.source_ready.connect(self._keep_demosaic)
        worker.done.connect(self._on_final_ready)
        worker.failed.connect(self._on_final_failed)
        worker.finished.connect(self._clear_final_worker)
        self._final_worker = worker

        # Take the slot first, then set off. The thread clears it itself when
        # it ends - whether the result was discarded (cancel) or not, the
        # memory is held until then, so the moment of release has to be
        # 'actually finished', not 'cancelled'.
        _FULL_RENDER_SLOT.add(worker)
        worker.finished.connect(lambda w=worker: _FULL_RENDER_SLOT.discard(w))

        self._set_full_render_state(busy=True)
        self.preview.set_busy(True)
        worker.start()

    def _compose_region(self, patch: np.ndarray,
                        region: tuple[float, float, float, float]) -> np.ndarray:
        """Fits a visible-region-only result into its place in the full frame.

        The screen puts down a single image and applies zoom and pan to it.
        Putting the cut piece up as-is throws the zoom arithmetic and the
        focus region coordinates all out of step. So we build a canvas at
        frame size (filling the invisible parts by stretching the existing
        preview) and lay the sharp piece onto it in its own place. The result
        is 'one image' just as before, so nothing downstream changes.
        """
        left, top, right, bottom = region
        span_x = max(1e-6, right - left)
        span_y = max(1e-6, bottom - top)
        patch_h, patch_w = patch.shape[:2]
        frame_w = max(patch_w, int(round(patch_w / span_x)))
        frame_h = max(patch_h, int(round(patch_h / span_y)))

        base = self._clip_base if self._clip_base is not None else self._source
        if base is None:
            return patch
        canvas = cv2.resize(to_display(base), (frame_w, frame_h),
                            interpolation=cv2.INTER_LINEAR)

        x0 = int(round(left * frame_w))
        y0 = int(round(top * frame_h))
        x1 = min(frame_w, x0 + patch_w)
        y1 = min(frame_h, y0 + patch_h)
        if x1 > x0 and y1 > y0:
            canvas[y0:y1, x0:x1] = patch[:y1 - y0, :x1 - x0]
        return canvas

    def _demosaic_for(self, path: Path):
        """Returns this shot's demosaic result if we already have it.

        It exists so that a 5.1s demosaic is not repeated on every zoom and
        pan (measured, R6M3 27MP). What it holds is about 390MB at 27MP, so
        we keep **one shot's worth only** - move to another shot and it is
        released at once.
        """
        if self._demosaic_path == path:
            return self._demosaic_cache
        return None

    def _keep_demosaic(self, image) -> None:
        """Takes in the demosaic source the worker just built.

        **The label carries the sending worker's path.** Move to another shot
        while a render is running and self.record is already the next shot
        while this signal belongs to the previous one. Labelling it by the
        current record makes the next Full Render hit it in `_demosaic_for`
        and show **the previous shot's pixels as the next shot's Full Render
        result**. With no exception and nothing on screen, only the evidence
        changes, so the user culls looking at A while believing it is B.

        If it is not the current shot's, it is simply discarded. At about
        390MB per 27MP shot it is not a value to hold on to "in case we come
        back".
        """
        worker = self.sender()
        path = getattr(worker, "_path", None) or self.record.path
        if path != self.record.path:
            return
        self._demosaic_cache = image
        self._demosaic_path = path

    def _drop_demosaic(self) -> None:
        """Releases the held source. At 390MB it must not be held for long."""
        self._demosaic_cache = None
        self._demosaic_path = None

    def _retry_when_slot_free(self) -> None:
        """Waits for the previous render to finish, then sets off."""
        if not self._waiting_for_slot:
            return
        if not self.final_button.isChecked():
            self._waiting_for_slot = False
            self._set_full_render_state(busy=False)
            return
        if full_render_in_flight():
            self._slot_timer.start(200)
            return
        self._waiting_for_slot = False
        self._show_final_preview()

    def _on_final_ready(self, image: np.ndarray) -> None:
        """Puts the finished result on screen.

        **We compare the sender against the current worker.** A signal
        already queued is delivered even after a disconnect, so merely
        knowing that a worker exists is not enough - by then it has been
        swapped for a new one, and the stale picture would overwrite the new
        screen. self.sender() tells us which worker actually emitted this,
        and the path and generation checks below cover the same race for a
        shot change and a settings change.
        """
        worker = self.sender()
        if worker is None or worker is not self._final_worker:
            return
        if worker._path != self.record.path:
            return
        if worker.generation != self._final_generation:
            return
        # The worker renders with output_space="srgb", so normally display
        # uint8 arrives. to_display is a pass-through then, and if a float
        # does come in it is moved here.
        self.preview.set_busy(False)
        # The geometry applied to this result - used by the overlay
        # coordinate conversion (_draw_roi).
        self._display_geometry = (
            engine.exact_geometry(worker._settings.geometry, *self._source.shape[:2])
            if self._source is not None else worker._settings.geometry)
        image = to_display(image)
        if self._final_region is not None:
            image = self._compose_region(image, self._final_region)

        # The markings, the ROI, and the clipping must still show in the Full
        # Render. This used to put it straight on screen, so the moment Full
        # Render was switched on the clipping overlay and the focus region
        # quietly disappeared.
        self._clip_base = image
        show_shadow, show_highlight = self._clip_overlay_state()
        if (show_shadow or show_highlight) and self._clip_blink_on:
            image = clip_overlay(image, show_shadow, show_highlight)
        self._apply_display_overlays(image)

    def _on_final_failed(self, message: str) -> None:
        if self.sender() is not self._final_worker:
            return
        self.preview.set_busy(False)
        self.preview.set_message(tr("Final preview failed: {message}").format(message=message))

    def _clear_final_worker(self) -> None:
        if self.sender() is not self._final_worker:
            return  # a retired worker finished late - do not touch state
        self._final_worker = None
        self._set_full_render_state(busy=False)

    # ------------------------------------------------------ Clipping overlay

    def _clip_overlay_state(self) -> tuple[bool, bool]:
        return (self.shadow_clip_button.isChecked(),
                self.highlight_clip_button.isChecked())

    def _on_clip_overlay_toggled(self, _checked: bool = False) -> None:
        """Switches the clipping overlay on and off."""
        show_shadow, show_highlight = self._clip_overlay_state()
        # The histogram widget has to hold the same state for its triangle
        # markers to match
        self.histogram.set_overlay_state(show_shadow, show_highlight)

        if show_shadow or show_highlight:
            self._clip_blink_on = True
            self._clip_blink_timer.start(CLIP_BLINK_MS)
        else:
            self._clip_blink_timer.stop()
            self._clip_blink_on = True
        self._render()

    def _sync_clip_buttons(self, show_shadow: bool, show_highlight: bool) -> None:
        """Matches the buttons when the toggle came from the histogram."""
        for button, value in ((self.shadow_clip_button, show_shadow),
                              (self.highlight_clip_button, show_highlight)):
            if button.isChecked() != value:
                button.blockSignals(True)
                button.setChecked(value)
                button.blockSignals(False)
        self._on_clip_overlay_toggled()

    def _blink_clip_overlay(self) -> None:
        """One blink tick. Flips only the painting, without recomputing the
        adjustments."""
        show_shadow, show_highlight = self._clip_overlay_state()
        if not (show_shadow or show_highlight) or self._clip_base is None:
            self._clip_blink_timer.stop()
            return

        self._clip_blink_on = not self._clip_blink_on
        image = self._clip_base
        if self._clip_blink_on:
            image = clip_overlay(image, show_shadow, show_highlight)
        self._apply_display_overlays(image)

    def _apply_display_overlays(self, image: np.ndarray) -> None:
        """Fast path for blinking - relays only the markings and the ROI,
        then puts it on screen. Same order as _render: the ROI on the
        photo first, the strip last."""
        settings = self.panel.settings()
        if self._any_overlay_on() and self.record.focus:
            image = self._draw_roi(image)
        if self._shows_finished_frame():
            image = engine.apply_overlays(
                image, settings, self.record.path, self.record.metadata
            )
        self.preview.set_pixmap(bgr_to_pixmap(to_display(image)))

    def _shows_finished_frame(self) -> bool:
        """Whether the screen is showing the exported composition rather
        than an editing frame.

        Only then do the watermark and the info strip go on. While the crop
        handles or a mask are up, every handle normalises its position
        against the pixmap - and a strip appended under the photo shifts
        each y by the strip's height. The crop you drew then cut the photo
        short, the ratio presets fitted the wrong aspect, and in crop mode
        the strip looked chopped off by the crop rectangle.
        """
        return (not self.before_after.isChecked()
                and not self.preview._crop_mode
                and not self._mask_editing_active())

    def _resolve_roi_scale(self, sensor_width: int) -> float:
        """The scale to multiply ROI coordinates by when drawing on screen.

        The ROI and the face boxes are in the coordinate system of **the
        embedded preview used for analysis**. It used to be assumed that this
        preview had the same width as the sensor, but the Panasonic S1R puts
        only a 1920px preview into a 47-megapixel (8392px) file. So the boxes
        were drawn at 1/4 size, 4.37x out of place (measured). Canon gives a
        full-resolution preview and happened to match, so looking at Canon
        alone everything seemed fine.

        Analysis now records the reference size along with the coordinates.
        Older caches do not have it, so only then do we read the preview and
        measure it directly, and if that fails too we use the old guess.
        """
        display_width = self._source.shape[1] if self._source is not None else 1
        focus = self.record.focus

        reference = getattr(focus, "source_width", 0) if focus else 0
        if not reference and focus is not None and focus.roi:
            # Pre-v4 cache - coordinates only, no reference. Read once and
            # measure.
            try:
                from ..core.raw_io import load_preview as _load_preview

                reference = _load_preview(self.record.path).shape[1]
            except Exception:  # noqa: BLE001
                log.debug("프리뷰 크기 확인 실패, 옛 어림값 사용", exc_info=True)

        if not reference:
            reference = max(1, sensor_width)

        # Drawing recomputes the scale from this reference width (see
        # _draw_roi).
        self._roi_reference_width = reference
        return display_width / reference

    def _explain_degraded(self) -> str:
        """One line on why the RAW could not be decoded. Empty if unknown."""
        try:
            from ..core.nef_meta import unsupported_reason

            return unsupported_reason(self.record.path) or ""
        except Exception:  # noqa: BLE001 - display goes on without a reason
            log.debug("미지원 사유 확인 실패", exc_info=True)
            return ""

    def _maybe_warn_stale_roi(self) -> None:
        """Warns when an old cache makes the focus region coordinates
        untrustworthy."""
        focus = self.record.focus
        if focus is None or not focus.roi:
            return
        if getattr(focus, "source_width", 0):
            return
        log.debug("%s: 예전 캐시의 초점 좌표 — 기준 크기를 직접 재서 씁니다",
                  self.record.path.name)

    # ---------------------------------------------- Manual main-subject switch

    def _on_preview_clicked(self, rx: float, ry: float) -> None:
        """Clicking on a face makes that face the main subject.

        A click changing something while the face boxes are not visible is
        nothing but startling, so it is only accepted while the overlay is
        on.
        """
        if not self.show_faces.isChecked():
            return
        focus = self.record.focus
        if focus is None or not focus.faces:
            return
        if not self.panel.settings().geometry.is_neutral():
            return  # crop/rotate change the coords - click untrustworthy

        reference = self._roi_reference_width or 1
        source_h = getattr(focus, "source_height", 0) or reference
        px, py = rx * reference, ry * source_h

        # Among overlapping faces we pick the smaller - a small face inside a
        # large one has no other way of being picked by click.
        hits = [
            index for index in self.visible_face_indices()
            if focus.faces[index][0] <= px <= focus.faces[index][0] + focus.faces[index][2]
            and focus.faces[index][1] <= py <= focus.faces[index][1] + focus.faces[index][3]
        ]
        if not hits:
            return
        chosen = min(hits, key=lambda i: focus.faces[i][2] * focus.faces[i][3])
        if chosen == focus.main_face:
            return
        self.set_main_face(chosen)

    def set_main_face(self, index: int) -> None:
        """Changes the main subject and **re-scores against that face**.

        Moving only the overlay leaves the score and the grade on the wrong
        face. We re-run the same function analysis used (analyze_focus) with
        just the face pinned, so the ROI, the sharpness, and the background
        sharpness all become relative to the new face.
        """
        from ..core.main_face import reanalyze_with_main_face

        # Re-run with the batch's own settings (see main_face) - the same
        # code the edits store uses to put a saved pick back.
        focus = reanalyze_with_main_face(self.record, self._analyze_config, index)
        if focus is None:
            return

        self.record.focus = focus
        self.record.manual_main_face = index
        self._eye_contours = None
        self.main_face_changed.emit(self.record)
        self._refresh_header()
        self._render()

    def _any_overlay_on(self) -> bool:
        """Whether any overlay toggle is on. With all off we do not even
        copy."""
        return (self.show_roi.isChecked() or self.show_faces.isChecked()
                or self.show_eyes.isChecked() or self.show_af.isChecked())

    def visible_face_indices(self) -> list[int]:
        """The face indices to draw. Also the candidates for switching the
        main subject.

        Low-confidence detections are dropped. Looking at real shoot samples
        by eye, the 0.60~0.75 band is mostly speaker cones, white gloves, and
        dark smudges, so drawing them leaves nothing but "why is that a
        face?" (an actual report).
        """
        focus = self.record.focus
        if focus is None or not focus.faces:
            return []
        scores = getattr(focus, "face_scores", ()) or ()
        return [
            index for index in range(len(focus.faces))
            if index == focus.main_face
            or index >= len(scores)
            or scores[index] >= FACE_DISPLAY_MIN_SCORE
        ]

    def _eye_rings(self) -> list[np.ndarray]:
        """Eye contours for the faces to be drawn - in **the analysis preview
        coordinate system**.

        Keeping them in the same coordinate system as the face boxes means
        drawing needs only one scale multiply. Measured once per shot and
        cached (1.3ms per face).
        """
        if self._eye_contours is not None:
            return self._eye_contours

        self._eye_contours = []
        focus = self.record.focus
        source = self._source
        if focus is None or source is None or not focus.faces:
            return self._eye_contours

        reference = self._roi_reference_width or 1
        to_source = source.shape[1] / reference  # analysis coords -> image
        detect = np.clip(source, 0, 255).astype(np.uint8)
        for index in self.visible_face_indices():
            x, y, w, h = focus.faces[index]
            points = face_mesh.landmarks(
                detect, (x * to_source, y * to_source,
                         w * to_source, h * to_source))
            if points is None:
                continue
            for ring in (face_mesh.LEFT_EYE, face_mesh.RIGHT_EYE):
                contour = np.array(
                    [[points[i][0] / to_source, points[i][1] / to_source]
                     for i in ring], np.float64)
                self._eye_contours.append(contour)
        return self._eye_contours

    def _draw_roi(self, image: np.ndarray) -> np.ndarray:
        """Lays the enabled overlays onto the image.

        The coordinates are all relative to the analysis preview (before
        cropping). The screen is the result with geometry applied, so the
        boxes and contours are moved through the same geometry to be drawn
        (_map_scene_points). They used to be hidden wholesale whenever
        geometry was applied, which made a single crop wipe out the focus and
        face overlays entirely and turned into a report of "crop and I cannot
        see them". Only the boxes cropped away should drop out; the rest must
        show in place.
        """
        geometry = self._display_geometry
        reference = self._roi_reference_width or 1
        focus = self.record.focus

        # All the conversion needs is the **aspect ratio** of the analysis
        # coordinate system. Leaning on the demosaic source (_source) makes
        # the overlays disappear entirely when the file could not be opened
        # (a shot with analysis results only) - the old code drew even then.
        ref_h = int(getattr(focus, "source_height", 0) or 0)
        if not ref_h:
            shape = (self._source.shape if self._source is not None
                     else image.shape)
            ref_h = max(1, round(reference * shape[0] / shape[1]))
        scene_hw = (ref_h, reference)

        out_wh = (image.shape[1], image.shape[0])
        marked = image.copy()
        thin = max(1, int(image.shape[1] / 1200))  # 1/3 of the old width

        def draw(box, colour, width) -> None:
            mapped = _map_scene_box(tuple(box), geometry, scene_hw, out_wh)
            if mapped is None:
                return                      # outside crop - nowhere to draw
            x, y, w, h = mapped
            cv2.rectangle(marked, (int(x), int(y)),
                          (int(x + w), int(y + h)), colour, width)

        # Detected faces are drawn faintly. Showing only the main subject
        # leaves no way to tell "why was that face chosen", or whether other
        # faces were missed.
        if self.show_faces.isChecked():
            for index in self.visible_face_indices():
                if index != focus.main_face:
                    draw(focus.faces[index], (170, 170, 170), thin)

        if self.show_eyes.isChecked():
            for contour in self._eye_rings():
                points = (np.asarray(contour, np.float64)
                          / np.array([reference, ref_h]))
                mapped = _map_scene_points(points, geometry, scene_hw)
                pixels = np.round(mapped * np.array(out_wh)).astype(np.int32)
                cv2.polylines(marked, [pixels], True, (240, 200, 60), thin)

        # Focus ROI (eye/face/tile) - where sharpness was actually measured
        if self.show_roi.isChecked() and focus.roi:
            draw(focus.roi, (80, 220, 80), thin)

        # The main subject is a red rectangle. It is what focus picked.
        # The width is kept the same as the other faces and they are told
        # apart **by colour alone**. Drawn at double width, the line covered
        # the face when zoomed in and the focus could not be seen at all.
        if self.show_faces.isChecked() and 0 <= focus.main_face < len(focus.faces):
            draw(focus.faces[focus.main_face], (60, 60, 235), thin)

        # Camera AF position - orange. Zone AF points at the torso (Sony), so
        # being out of step with the face box is normal. That offset is
        # itself a confidence signal (A1).
        af_box = self._af_reference_box()
        if self.show_af.isChecked() and af_box is not None:
            draw(af_box, (40, 170, 240), max(thin, 2))
        return marked

    def _af_reference_box(self) -> tuple[int, int, int, int] | None:
        """The camera AF box in the same coordinate system as the faces and
        the ROI (relative to _roi_reference_width).

        Cached records carry no AF box, so the file is read at display time
        (2MB of header, ~ms). It is read once and cached, and None (not in
        the file) is kept distinct from not-yet-read so we do not dig through
        the file on every render.
        """
        if self._af_box is not _AF_UNREAD:
            return self._af_box  # type: ignore[return-value]

        reference = self._roi_reference_width
        source = self._source
        if not reference or source is None:
            # The preview is not up yet. We do not cache (keeping
            # _AF_UNREAD) and try again on the next render - settling on None
            # would mean never reading it.
            return None
        # af_preview_box takes the preview size and returns coordinates in
        # it. It has to be given the same aspect ratio as the analysis
        # coordinate system (reference width) for the result to line up with
        # the face boxes.
        ref_h = int(round(reference * source.shape[0] / source.shape[1]))
        orientation = self.record.metadata.orientation if self.record.metadata else 1
        try:
            from ..core.maker_meta import af_preview_box

            self._af_box = af_preview_box(
                self.record.path, orientation, int(reference), ref_h)
        except Exception:  # noqa: BLE001 - display only, so fail quietly
            self._af_box = None
        return self._af_box  # type: ignore[return-value]

    # -------------------------------------------- Radial / linear mask handles

    def _sync_mask_shape(self) -> None:
        """Raises handles on the image when the selected mask is radial or
        linear.

        These two used to be nailed to their default positions. The
        parameters are all normalised coordinates, but there was no UI to
        touch those values, so the spotlight was always dead centre.
        """
        from ..core.develop.masks import SIZE_KINDS, _size_factor

        mask = self.panel.shape_mask()
        if mask is None:
            self.preview.set_shape(None)
        else:
            # The size applies to radial only. Multiplying it onto a linear
            # mask amounts to shrinking a radius that does not exist, and
            # the screen and the result go out of step.
            size = _size_factor(mask) if mask.kind in SIZE_KINDS else 1.0
            self.preview.set_shape(mask.kind.value, mask.params, size=size)
        # Handles up or down is a display-frame transition (the scene frame
        # while they are up, the crop-applied view when they go). Drawn now,
        # not by the timer: the handles are live the moment they are up,
        # and a drag begun before the timer fired measured itself against
        # the still-cropped picture, so the ellipse jumped when the render
        # landed - the same stale frame that broke the ratio crop.
        if self.panel._loading:
            # A shot being loaded: the source on screen is still the last
            # shot's, and _load_context draws the new one in a moment.
            self._render_timer.start()
        elif (self.preview._crop_mode, self._mask_editing_active()) != self._rendered_frame:
            self._render()
        else:
            self._render_timer.start()

    def _on_shape_dragged(self, params: dict) -> None:
        """Stores the shape coordinates dragged on the image into the mask.

        No render happens here - redrawing on every step of the drag cannot
        keep up (the same way as the crop drag). ImageView draws the outline
        itself, and the real re-render runs once, from shape_finished.
        """
        self.panel.set_mask_params(params, silent=True)
        self._dirty = True

    # ------------------------------------------------------------ Brush

    _BRUSH_CANVAS = 512
    """The resolution the brush alpha is held at. It rides along inside
    preset files, so it is not set large."""

    def _on_brush_mode(self, enabled: bool) -> None:
        """Paint mode switches off crop and the eyedropper, taking only the
        brush."""
        if enabled:
            self._leave_other_modes(keep="brush")
        self.preview.set_brush_mode(enabled)
        self._sync_brush_cursor()
        if enabled:
            # While painting, the region has to show to know what was painted
            self.panel.mask_overlay_check.setChecked(True)

    def _sync_brush_cursor(self) -> None:
        """Reflects the brush size and eraser state in the preview circle."""
        self.preview.set_brush_radius(self.panel.brush_radius_ratio())
        self.preview.set_brush_erasing(self.panel.is_erasing())

    def _brush_canvas(self, mask) -> np.ndarray:
        """The selected mask's alpha as an editing canvas. Builds an empty
        one if there is none.

        It has to match the image aspect ratio or the painted shape comes out
        squashed.
        """
        from ..core.develop.masks import _brush_alpha  # noqa: PLC0415

        height, width = 1, 1
        if self._source is not None:
            height, width = self._source.shape[:2]
        long_edge = max(height, width) or 1
        scale = self._BRUSH_CANVAS / long_edge
        canvas_h = max(8, int(round(height * scale)))
        canvas_w = max(8, int(round(width * scale)))

        if mask.bitmap:
            existing = _brush_alpha(replace(mask, feather=0), canvas_h, canvas_w)
            if existing is not None:
                return existing
        return np.zeros((canvas_h, canvas_w), np.float32)

    def _on_brush_paint(self, nx: float, ny: float) -> None:
        """Reflects one point painted on the image into the mask alpha."""
        from ..core.develop.masks import encode_brush
        from ..core.develop.settings import MaskType

        mask = self.panel.overlay_mask() or None
        index = self.panel._selected_mask_index()
        if not (0 <= index < len(self.panel._masks)):
            return
        mask = self.panel._masks[index]
        if mask.kind is not MaskType.BRUSH:
            return

        canvas = self._brush_canvas(mask)
        h, w = canvas.shape[:2]
        radius = max(1, int(round(self.panel.brush_radius_ratio() * min(h, w))))
        center = (int(round(nx * w)), int(round(ny * h)))
        # The eraser paints 0 with the same brush
        value = 0.0 if self.panel.is_erasing() else 1.0
        cv2.circle(canvas, center, radius, value, -1, lineType=cv2.LINE_AA)

        self.panel.set_brush_bitmap(encode_brush(canvas))

    def _draw_mask_overlay(self, image: np.ndarray, mask) -> np.ndarray:
        """Marks the area the selected mask covers in red (semi-transparent).

        The shape of the area has to show regardless of the opacity, so it is
        painted at a fixed strength. If there is no face and the mask cannot
        be built, the image is left alone.
        """
        from ..core.develop.masks import mask_overlay_alpha

        alpha = mask_overlay_alpha(mask, image, self.record.main_face_norm)
        if alpha is None:
            return image
        red = np.zeros_like(image)
        red[:, :, 2] = 255
        a = (alpha * 0.45)[:, :, None]
        return (image.astype(np.float32) * (1 - a) + red * a).astype(np.uint8)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._render()

    # ----------------------------------------------------------- Grade / apply

    def set_grade(self, grade: Grade) -> None:
        self.record.manual_grade = grade
        self._refresh_header()
        self.records_changed.emit()
        if state.advance_after_grade():
            self.step(1)

    def show_shortcuts(self) -> None:
        from .shortcuts_dialog import show_shortcuts

        show_shortcuts(self)

    def apply_to_all(self) -> None:
        """Applies the develop dialled in here to the whole list.

        Crop, straighten, and rotate are excluded. Framing differs shot to
        shot, so putting one shot's crop onto another cuts the subject away.
        The current shot's crop is left as it is, and the other shots get the
        colour adjustments only.
        """
        settings = self.panel.settings()
        self._commit_settings()

        shared = settings.without_geometry()
        value = None if shared.is_neutral() else shared

        for record in self.records:
            if record is self.record:
                continue  # current shot already saved, crop included
            if value is None:
                record.develop = None
            elif record.develop is not None:
                # Crops and masks another shot already holds are kept and
                # only the rest is overwritten. Keeping the crop but
                # overwriting the mask makes every local adjustment drawn per
                # shot vanish in a single apply-to-all - both are outside the
                # shared set for the same reason (values specific to a shot).
                record.develop = replace(
                    shared, geometry=record.develop.geometry,
                    masks=record.develop.masks,
                )
            else:
                record.develop = shared

        self.records_changed.emit()

        count = len(self.records)
        self.info.setText(
            f"<b>{self.record.path.name}</b> · "
            f"<span style='color:#7fb3ff'>"
            + tr("Develop applied to {count} photos "
                 "(crop and straighten kept per shot)").format(count=count)
            + "</span>"
        )

    def accept(self) -> None:
        self._commit_settings()
        super().accept()

    def reject(self) -> None:
        self._commit_settings()
        super().reject()

    def closeEvent(self, event) -> None:
        """Cuts every scheduled render and background thread, then closes.

        This window is WA_DeleteOnClose, so the Python object disappears the
        instant it closes. If even one signal arrives after that, it touches
        an already-gone C++ object and a native crash follows (Qt6Core
        fail-fast). So the timers are stopped and **finished is cut as
        well** - only done/failed used to be cut, so the finished a worker
        sends as it ends could call a slot on a deleted window.
        """
        self._render_timer.stop()
        self._full_render_timer.stop()
        self._full_render_lock.stop()
        self._slot_timer.stop()
        self._clip_blink_timer.stop()
        self._waiting_for_slot = False
        self._drop_demosaic()
        self._shutdown_workers()
        super().closeEvent(event)
