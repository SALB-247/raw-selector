"""루페 / 미리보기 창.

더블클릭으로 열리고, 여기서 바로 보정까지 합니다. 판정 근거(ROI)를 확인하고
등급을 바꾸고 다음 컷으로 넘어가는 것까지 한 창에서 끝나야, 수백 장을
검토하는 흐름이 끊기지 않습니다.
"""

from __future__ import annotations

import logging
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
from ..core import face_mesh
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
"""미리보기 렌더 해상도. 더 키우면 슬라이더 반응이 눈에 띄게 둔해집니다."""

FINAL_LONG_EDGE = 2200
"""최종 미리보기 표시 해상도. 디모자이크는 원본으로 하되 표시는 이 크기로."""


from .workers import silent_disconnect as _silent_disconnect  # noqa: E402

log = logging.getLogger(__name__)

#: 창이 닫힌 뒤에도 아직 도는 렌더 스레드를 붙잡아 두는 곳.
#:
#: Qt는 **실행 중인 QThread가 파괴될 때** qFatal로 프로세스를 죽입니다.
#: cancel()은 플래그만 세우는데, 워커가 rawpy 디모자이크(수 초짜리 단일 C
#: 호출) 안에 있으면 그 플래그를 볼 지점이 없습니다. 그래서 "취소하고
#: 잠깐 기다린 뒤 닫기"는 기다림이 모자라는 순간 그대로 크래시가 됩니다
#: (실측: 렌더 도중 창을 12번 여닫으니 재현).
#:
#: 기다리는 대신 참조를 여기로 옮깁니다. 창은 즉시 닫히고, 스레드는 제
#: 속도로 끝난 뒤 스스로 빠집니다. 파괴되는 시점에는 이미 멈춰 있습니다.
_RUNNING_RENDERS: set = set()

#: 지금 이 프로세스에서 돌고 있는 Full Render 스레드.
#:
#: **동시에 하나만** 돌아야 합니다. 27MP RAW 한 장을 풀 해상도로 디모자이크
#: 하는 데 실측 2.8GB가 듭니다(R6M3). 두 개가 겹치면 5.5GB — 8GB PC에서는
#: OS와 앱 몫까지 더해 한계를 넘고, 사용자에게는 "크래시"로 보입니다.
#:
#: 겹치는 경로는 평범합니다: 버튼을 껐다 켜면 `_abandon_render`가 돌던
#: 워커를 놓아주지만 **멈추지는 못합니다**(rawpy 디모자이크는 중간에 끊을
#: 지점이 없습니다). 그 상태에서 새 워커를 띄우면 곧바로 두 개가 됩니다.
#: 그래서 시작 전에 여기를 보고, 비어 있을 때만 출발합니다.
_FULL_RENDER_SLOT: set = set()

FULL_RENDER_LOCKOUT_MS = 3000
"""Full Render를 켠 뒤 버튼을 다시 누를 수 있게 되기까지의 최소 시간.

껐다 켜기를 연타하면 무거운 렌더가 겹칩니다. 실제로 그걸로 크래시
리포트가 올라왔습니다. 잠깐 잠가서 연타 자체를 막습니다.
"""


_WORKER_SIGNALS = ("source_ready", "done", "failed", "finished")
"""렌더 워커가 창으로 보내는 신호 전부. 정리할 때 하나도 빠뜨리면 안 됩니다.

source_ready가 빠져 있으면 은퇴시킨 워커가 나중에 디모자이크 원본을 창에
밀어 넣어, 다음 렌더가 낡은 화소를 재사용합니다(_keep_demosaic 참고).
"""


def _disconnect_worker(worker) -> None:
    """워커의 신호를 전부 끊습니다. 없는 신호는 건너뜁니다.

    이름으로 찾는 이유는 정리 경로가 **어떤 경우에도 예외를 내면 안 되기**
    때문입니다. 속성으로 바로 쓰면 신호 하나가 없는 객체에서 AttributeError가
    나고, 그 순간 뒤따르는 정리(취소·참조 보관)가 통째로 건너뛰어집니다 —
    도는 스레드를 놓치는 바로 그 상황입니다.
    """
    for name in _WORKER_SIGNALS:
        signal = getattr(worker, name, None)
        if signal is not None:
            _silent_disconnect(signal)


def _detach_until_finished(worker) -> None:
    """창과 분리해 스레드가 끝날 때까지 살려 둡니다."""
    _RUNNING_RENDERS.add(worker)
    worker.finished.connect(lambda: _RUNNING_RENDERS.discard(worker))


def full_render_in_flight() -> bool:
    """지금 어느 창에서든 Full Render가 돌고 있는가."""
    for worker in list(_FULL_RENDER_SLOT):
        try:
            if worker.isRunning():
                return True
        except RuntimeError:
            pass
        _FULL_RENDER_SLOT.discard(worker)
    return False


def wait_for_detached_renders(timeout_ms: int = 30000) -> None:
    """앱을 끄기 전에 남은 렌더를 기다립니다.

    여기서는 정말로 기다려야 합니다 — 인터프리터가 끝나면 객체가 사라지고,
    그때 도는 중이면 같은 크래시가 납니다.
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
    """정규화 상자를 잘라낸 영역 기준 좌표로. 영역 밖이면 None."""
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


class FinalRenderWorker(QThread):
    """RAW를 실제로 디모자이크해 최종 화질 미리보기를 만듭니다.

    24MP 현상은 몇 초 걸릴 수 있어 메인 스레드를 막지 않도록 분리합니다.

    실측(R6M3 27MP): 디모자이크 5.1초, 보정 3.4초. **시간의 60%가
    디모자이크**이고 rawpy는 이미지 전체 단위라 쪼갤 수 없습니다. 그래서
    확대할 때마다 처음부터 다시 하면 매번 8.5초가 듭니다.

    두 가지로 줄입니다.

    1. 디모자이크 결과를 창이 들고 있다가 넘겨줍니다(`source`). 같은 컷을
       확대·이동하는 동안에는 5.1초를 다시 쓰지 않습니다.
    2. 확대한 상태면 **화면에 보이는 영역만** 보정합니다. 4배 확대에서
       보정이 3.4초 → 0.22초가 됩니다(실측).
    """

    done = Signal(object)     # 완성된 BGR 이미지
    failed = Signal(str)
    source_ready = Signal(object)  # 디모자이크 원본 (다음 렌더에서 재사용)

    def __init__(self, path: Path, settings: DevelopSettings, wb,
                 target_long_edge: int = FINAL_LONG_EDGE, generation: int = 0,
                 source: "np.ndarray | None" = None,
                 region: tuple[float, float, float, float] | None = None,
                 main_face_box: tuple[float, float, float, float] | None = None,
                 metadata=None):
        super().__init__()
        self._main_face_box = main_face_box
        # 렌즈 자동 보정은 기종·렌즈 이름으로 프로필을 찾습니다. 안 넘기면
        # 조용히 원본이 나와서, 화면에는 걸린 보정이 Full Render에서만
        # 사라집니다.
        self._metadata = metadata
        self._path = path
        self._settings = settings
        self._wb = wb  # (camera, daylight) 또는 None
        self._target = max(1, int(target_long_edge))
        self.generation = generation
        self._source = source
        """이미 디모자이크해 둔 원본. 있으면 그 단계를 건너뜁니다."""
        self.region = region
        """보정할 영역 (left, top, right, bottom, 0~1). None이면 전체."""
        self._cancelled = False

    def cancel(self) -> None:
        """결과를 버리게 표시합니다. 스레드를 강제로 죽이지는 않습니다."""
        self._cancelled = True

    def run(self) -> None:
        try:
            import numpy as _np

            from ..core.raw_io import load_demosaiced, resize_long_edge

            face_box = self._main_face_box
            image = self._source
            if image is None:
                # 라이브 프리뷰(half)와 같은 방식이되 풀 해상도로 디모자이크합니다.
                image = load_demosaiced(self._path)
                if self._cancelled:
                    return
                # 창이 들고 있다가 다음 확대·이동 때 넘겨줍니다
                self.source_ready.emit(image)

            if self._cancelled:
                return

            if self.region is not None:
                # 보이는 영역만. 자른 뒤 자르기 설정을 그대로 두면 두 번
                # 잘리므로, 여기서는 기하 보정을 중립으로 두고 보냅니다.
                height, width = image.shape[:2]
                left, top, right, bottom = self.region
                x0 = max(0, min(width - 1, int(left * width)))
                y0 = max(0, min(height - 1, int(top * height)))
                x1 = max(x0 + 1, min(width, int(right * width)))
                y1 = max(y0 + 1, min(height, int(bottom * height)))
                image = _np.ascontiguousarray(image[y0:y1, x0:x1])
                # 주 피사체 좌표도 잘라낸 조각 기준으로 다시 잡습니다. 안 그러면
                # 확대할 때만 마스크가 엉뚱한 얼굴로 옮겨 갑니다.
                face_box = _remap_box(face_box, (left, top, right, bottom))

            # 화면에 실제로 보이는 해상도까지만 줄입니다. resize_long_edge는
            # 확대하지 않으므로, target이 원본보다 크면 원본 그대로 갑니다.
            image = resize_long_edge(image, self._target)
            if self._cancelled:
                return
            result = engine.apply_settings(image, self._settings, self._path,
                                           self._metadata,
                                           wb=self._wb, main_face_box=face_box)
            if self._cancelled:
                return
            self.done.emit(result)
        except Exception as exc:  # noqa: BLE001
            if not self._cancelled:
                self.failed.emit(str(exc))


CLIP_BLINK_MS = 550
"""클리핑 표시 점멸 주기. 가만히 칠해 두면 사진 원래 색과 구분이 안 됩니다."""

CLIP_HIGHLIGHT_LEVEL = 250
CLIP_SHADOW_LEVEL = 5
"""클리핑으로 볼 화소값.

254/2로 잡으면 8비트로 내린 뒤 정확히 그 값에 닿은 화소만 잡혀서, 눈으로는
분명히 날아간 영역인데 표시가 거의 안 뜹니다. 실제로 "켜도 안 보인다"는
리포트가 여기서 나왔습니다. 조금 안쪽으로 잡아야 경고 구실을 합니다.
"""


def clip_overlay(
    image_bgr: np.ndarray, show_shadow: bool, show_highlight: bool
) -> np.ndarray:
    """클리핑된 화소를 색으로 덮어 표시합니다 (Lightroom과 같은 방식).

    하이라이트가 날아간 곳(어느 채널이든 상한 이상)은 빨강, 섀도우가 뭉개진
    곳(모든 채널 하한 이하)은 파랑으로 칠합니다. 원본은 건드리지 않습니다.
    """
    result = image_bgr.copy()
    if show_highlight:
        blown = image_bgr.max(axis=2) >= CLIP_HIGHLIGHT_LEVEL
        result[blown] = (0, 0, 255)  # BGR 빨강
    if show_shadow:
        crushed = image_bgr.max(axis=2) <= CLIP_SHADOW_LEVEL
        result[crushed] = (255, 0, 0)  # BGR 파랑
    return result


def clip_counts(image_bgr: np.ndarray) -> tuple[int, int]:
    """(뭉개진 화소 수, 날아간 화소 수). 표시가 왜 안 뜨는지 알려면 필요합니다."""
    crushed = int(np.count_nonzero(image_bgr.max(axis=2) <= CLIP_SHADOW_LEVEL))
    blown = int(np.count_nonzero(image_bgr.max(axis=2) >= CLIP_HIGHLIGHT_LEVEL))
    return crushed, blown


def bgr_to_pixmap(image: np.ndarray) -> QPixmap:
    """OpenCV BGR ndarray를 QPixmap으로. 복사본을 만들어야 버퍼가 살아 있습니다.

    QImage는 바이트열을 uint8 3채널로 해석합니다(bytesPerLine=3*width). float
    배열을 그대로 넘기면 4바이트 값을 화소로 잘못 읽어 화면 전체가 컬러
    노이즈가 됩니다. 파이프라인 중간값은 float이므로 여기서 반드시 8비트로
    맞춥니다 — 호출부가 빠뜨려도 안전해야 합니다.
    """
    if image.dtype != np.uint8:
        image = np.clip(image, 0.0, 255.0).astype(np.uint8)
    rgb = np.ascontiguousarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    height, width, _ = rgb.shape
    qimage = QImage(rgb.data, width, height, 3 * width, QImage.Format_RGB888)
    return QPixmap.fromImage(qimage.copy())


class LoupeDialog(QDialog):
    """미리보기 + 보정 + 컷 이동.

    records를 함께 넘기면 같은 목록 안에서 앞뒤로 이동할 수 있습니다.
    """

    records_changed = Signal()
    queue_requested = Signal(list)
    export_requested = Signal(list)
    record_switched = Signal(object, object)  # (이전 경로, 새 경로)
    main_face_changed = Signal(object)  # 주 피사체를 바꾼 레코드

    def __init__(
        self,
        record: ImageRecord,
        records: list[ImageRecord] | None = None,
        parent=None,
        fast: bool = False,
    ):
        super().__init__(parent)
        # fast=True면 내장 JPEG으로 즉시 엽니다(빠른 미리보기용). False면 RAW를
        # 디모자이크해 정확한 색·계조로 엽니다(보정용, 여는 데 조금 더 걸림).
        self._fast = fast
        self.records = records or [record]
        self.index = self.records.index(record) if record in self.records else 0
        self.record = self.records[self.index]

        self._source: np.ndarray | None = None
        self._wb = None  # raw_io.WhiteBalance — 절대 색온도 변환용
        self._final_worker = None  # 최종 미리보기 렌더 스레드
        # 취소했지만 아직 도는 워커들. 참조를 놓으면 Qt가 프로세스를 죽입니다.
        self._retired_workers: list[FinalRenderWorker] = []
        self._waiting_for_slot = False
        """앞 렌더가 끝나기를 기다리는 중인지. 겹쳐 돌리지 않기 위한 표시."""

        self._clip_base: np.ndarray | None = None
        """클리핑 칠하기 직전의 이미지. 점멸할 때 여기서만 다시 칠합니다."""
        self._clip_blink_on = True

        self._roi_reference_width = 0
        """roi·faces 좌표의 기준 폭. 그릴 때 이미지 폭과 나눠 배율을 냅니다."""

        self._eye_contours: list[np.ndarray] | None = None
        """이 컷의 눈 윤곽(분석 좌표계). 컷을 바꾸거나 주 피사체를 바꾸면 비웁니다."""

        self._demosaic_cache = None
        self._demosaic_path: Path | None = None
        """직전 Full Render의 디모자이크 결과. 확대·이동 때 재사용합니다."""

        self._final_region: tuple[float, float, float, float] | None = None
        """마지막 Full Render가 만든 영역. 전체가 아니면 화면 맞춤이 달라집니다."""
        self._degraded = False  # 디모자이크 실패로 JPEG 폴백 중인지
        self._degraded_reason = ""
        """왜 폴백했는지. 알 수 있으면 화면에 그대로 보여 줍니다."""
        self._dirty = False

        # 비모달로 띄웁니다. 모달이면 보정하는 동안 메인 창이 멈춰서
        # 격자를 보거나 다른 컷을 열 수 없습니다.
        # 최소화/최대화 버튼을 명시해야 합니다. QDialog 기본은 닫기 버튼만
        # 달려 있어(WS_MAXIMIZEBOX 없음) 윈도우 스냅(에어로 스냅)이 걸리지
        # 않습니다 — 화면 가장자리로 끌어도 반쪽 배치가 되지 않습니다.
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowSystemMenuHint
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )
        self.setModal(False)
        self.setAttribute(Qt.WA_DeleteOnClose)

        # 화면보다 큰 창으로 열면 창 관리자가 줄여 놓는데, 그때 스플리터가
        # 패널 최소 폭을 못 맞춰 오른쪽이 잘린 채로 뜹니다. 처음부터 화면
        # 안에 들어가는 크기로 엽니다 (FHD 100%에서 실제로 겪은 문제).
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

        # 패널 폭은 스플리터가 정하지만, 창 자체가 패널 최소보다 좁아지면
        # 오른쪽(값 박스·리셋 버튼)이 잘립니다. 레이아웃이 요구하는 최소를
        # 창 최소로 삼아 그 아래로는 못 줄이게 막습니다. 폰트 메트릭에 따라
        # 값이 달라지므로 하드코딩하지 않고 레이아웃에서 가져옵니다.
        # 세로는 패널이 스크롤되므로 적당히 잡습니다.
        self.layout().activate()
        self.setMinimumSize(self.layout().minimumSize().width(), 640)

        # 창을 열어 둔 채 프로그램을 끄면 closeEvent가 안 불립니다. 그러면
        # 도는 워커가 파괴되어 Qt가 프로세스를 죽입니다(0xc0000409).
        # 종료 직전에 한 번 더 정리할 기회를 잡아 둡니다.
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown_workers)

        # 슬라이더를 끌 때마다 렌더링하면 버벅입니다. 잠깐 멈추면 그립니다.
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(120)
        self._render_timer.timeout.connect(self._render)

        # Full Render는 조작이 멈춘 뒤에만 돌립니다. 매 조작마다 풀 해상도로
        # 현상하면 슬라이더를 못 움직입니다.
        self._final_generation = 0
        self._full_render_timer = QTimer(self)
        self._full_render_timer.setSingleShot(True)
        self._full_render_timer.setInterval(800)
        self._full_render_timer.timeout.connect(self._show_final_preview)

        # 켠 직후 버튼을 잠그는 타이머. 연타하면 무거운 렌더가 겹쳐서
        # 메모리가 두 배가 되고, 작은 PC는 그 지점에서 죽습니다.
        self._full_render_lock = QTimer(self)
        self._full_render_lock.setSingleShot(True)
        self._full_render_lock.timeout.connect(self._release_full_render_button)

        # 앞 렌더가 끝나기를 기다렸다 출발하기 위한 재시도 타이머
        self._slot_timer = QTimer(self)
        self._slot_timer.setSingleShot(True)
        self._slot_timer.timeout.connect(self._retry_when_slot_free)

        # 클리핑 점멸. 가만히 칠해 두면 원래 그런 색인지 경고인지 모릅니다.
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

        # 잠긴 이유를 말해 주지 않으면 사용자는 프로그램이 멈춘 줄 압니다
        self.lock_notice = QLabel()
        self.lock_notice.setWordWrap(True)
        self.lock_notice.setStyleSheet(
            f"background: {theme.SURFACE}; color: {theme.WARNING};"
            f" border: 1px solid {theme.WARNING}; border-radius: 4px;"
            " padding: 5px 8px; font-weight: bold;"
        )
        self.lock_notice.setVisible(False)
        layout.addWidget(self.lock_notice)

        # 이미지와 보정 패널 사이를 사용자가 끌어 조절할 수 있게 합니다.
        # 폭을 고정해 두면 내용이 조금만 늘어도 오른쪽이 말없이 잘립니다
        # (실제로 세 번 겪었습니다). 스플리터는 최소치만 지키고 나머지는
        # 창 크기와 사용자 조작에 맡깁니다.
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
        self.preview.crop_finished.connect(self._on_settings_changed)
        self.preview.zoom_changed.connect(self._on_zoom)
        self.preview.pan_finished.connect(self._schedule_full_render)
        viewer.addWidget(self.preview, 1)
        viewer.addLayout(self._build_viewer_controls())
        self.body_splitter.addWidget(viewer_box)

        right = QVBoxLayout()
        right.setSpacing(4)

        self.histogram = HistogramWidget()
        right.addWidget(self.histogram)

        # 예전에는 히스토그램 좌·우 상단 모서리의 9px짜리 삼각형이 이 토글
        # 이었습니다. 어두운 회색이라 있는 줄도 몰랐고, 22px 코너를 정확히
        # 눌러야 해서 눌러도 안 눌렸습니다. 글자가 있는 버튼으로 꺼냅니다.
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

        # 순환 import를 피하려고 여기서 가져옵니다
        from .develop_panel import DevelopPanel

        self.panel = DevelopPanel()
        self.panel.settings_changed.connect(self._on_settings_changed)
        self.panel.crop_mode_changed.connect(self._on_crop_mode)
        self.panel.pick_mode_changed.connect(self._on_pick_mode)
        self.panel.mask_overlay_changed.connect(self._render)
        self.panel.mask_shape_changed.connect(self._sync_mask_shape)
        self.panel.brush_mode_changed.connect(self._on_brush_mode)
        self.panel.brush_changed.connect(self._sync_brush_cursor)
        self.preview.color_picked.connect(self._on_color_picked)
        self.preview.brush_painted.connect(self._on_brush_paint)
        self.preview.clicked.connect(self._on_preview_clicked)
        self.preview.shape_changed.connect(self._on_shape_dragged)
        self.preview.shape_finished.connect(self._on_settings_changed)
        right.addWidget(self.panel, 1)

        container = QWidget()
        container.setLayout(right)
        # 고정이 아니라 최소만 정합니다. 모자라면 사용자가 스플리터를 끌어
        # 넓히면 되고, 창이 커지면 이미지 쪽이 늘어납니다.
        container.setMinimumWidth(self.panel.minimumWidth())
        self.body_splitter.addWidget(container)
        self.body_splitter.setStretchFactor(0, 1)   # 이미지가 남는 공간을 가져갑니다
        self.body_splitter.setStretchFactor(1, 0)
        # 미리보기 모드에서는 보정 패널을 숨겨 이미지를 크게 봅니다.
        # (색은 보정 모드와 똑같이 디모자이크로 정확합니다.)
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

        # 단축키는 라벨이 아니라 툴팁에 답니다. 표시 항목이 셋으로 늘면서
        # "(B)" 같은 꼬리표까지 넣으면 이 줄만으로 창 최소 폭이 1004px가 되어,
        # 900px 화면에서 우측 패널이 잘립니다(실측).
        self.before_after = QCheckBox(tr("Original"))
        self.before_after.setToolTip(tr("Shows the image before develop (B)"))
        self.before_after.toggled.connect(self._render)
        row.addWidget(self.before_after)

        # 원본 위에 그리는 것이 늘어나면서 한 스위치로 묶어 두기 어려워졌습니다.
        # 초점 영역만 보고 싶은데 얼굴 상자가 같이 나오면 정작 초점을 못 봅니다.
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

    # ------------------------------------------------------------ 줌

    def zoom_to_focus(self) -> None:
        """판정에 쓴 ROI를 화면 가득 채웁니다."""
        if not (self.record.focus and self.record.focus.roi):
            return
        if not self.panel.settings().geometry.is_neutral():
            # 크롭이 걸리면 좌표계가 달라져 ROI 위치를 신뢰할 수 없습니다
            return
        self.preview.zoom_to_roi(self.record.focus.roi, self._roi_scale)
        self._on_zoom(self.preview.zoom())

    def _on_zoom(self, zoom: float) -> None:
        self.zoom_label.setText(f"{zoom * 100:.0f}%")
        # 확대하면 더 정밀한 해상도가 필요합니다. 확대를 멈춘 뒤 다시 그립니다.
        self._schedule_full_render()

    # ------------------------------------------------------------ 대기열 / 내보내기

    def add_to_queue(self) -> None:
        """현재 컷을 대기열에 담습니다. 부모 창이 대기열을 들고 있습니다."""
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
            ("Z", self.zoom_to_focus),
            ("Q", self.add_to_queue),
        ):
            action = QAction(self)
            action.setShortcut(QKeySequence(keys))
            action.triggered.connect(handler)
            self.addAction(action)

    def _toggle_before_after(self) -> None:
        self.before_after.setChecked(not self.before_after.isChecked())

    # ------------------------------------------------------------ 컷 이동

    def step(self, delta: int) -> None:
        """앞뒤 컷으로 이동합니다. 목록 끝에서는 멈춥니다."""
        target = self.index + delta
        if not (0 <= target < len(self.records)):
            return
        self._commit_settings()
        previous_path = self.record.path
        self.index = target
        self.record = self.records[target]
        # 컷을 옮기면 들고 있던 디모자이크 원본(약 390MB)은 쓸모가 없습니다
        self._drop_demosaic()
        self._load_current()
        self.record_switched.emit(previous_path, self.record.path)

    def _commit_settings(self) -> None:
        """지금 화면의 보정값을 현재 레코드에 저장합니다.

        컷을 옮기기 전에 반드시 불러야 합니다. 안 그러면 방금 맞춘 값이
        조용히 사라집니다.
        """
        if not self._dirty:
            return
        settings = self.panel.settings()
        self.record.develop = None if settings.is_neutral() else settings
        self._dirty = False
        self.records_changed.emit()

    def _load_current(self) -> None:
        self.panel.set_settings(self.record.develop or DevelopSettings())
        self._dirty = False

        try:
            # 보정 화면은 RAW를 실제로 디모자이크한 중립 이미지를 씁니다.
            # 내장 JPEG은 카메라 픽처스타일(대비·채도·톤)이 이미 구워져 있어
            # 보정을 끈 상태에서도 실제 RAW와 크게 달라집니다. 셀렉 그리드는
            # 속도 때문에 JPEG을 쓰지만, 여기서는 정확도가 우선입니다.
            # 미리보기든 보정이든 오직 RAW 디모자이크만 씁니다 — 내장 JPEG은
            # 색·계조가 카메라 렌더라 여기서는 절대 쓰지 않습니다. 반응 속도를
            # 위해 half-size로 합니다(최종 미리보기 버튼은 풀 해상도).
            full = load_demosaiced(self.record.path, half_size=True)
            self._source = resize_long_edge(full, PREVIEW_LONG_EDGE)
            self._roi_scale = self._resolve_roi_scale(full.shape[1] * 2)
            self._degraded = False
            self._degraded_reason = ""
        except Exception as demosaic_exc:  # noqa: BLE001
            # 디모자이크가 아예 실패하는 파일(손상, 완전 미지원)은 아무것도
            # 못 보여주는 것보다 내장 JPEG으로라도 보여 주는 편이 낫습니다.
            # 색·계조는 정확하지 않으므로 표시로 알립니다.
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

        self._maybe_warn_stale_roi()

        # 절대 색온도 변환에 쓸 화이트밸런스를 읽고, 슬라이더 기본값을
        # 이 컷의 as-shot 색온도로 맞춥니다.
        self._wb = read_white_balance(self.record.path)
        if self._wb is not None:
            self.panel.set_as_shot_kelvin(self._wb.as_shot_kelvin)

        # 렌즈 프로필이 잡히는지 미리 알려 줍니다. DB에 없는 렌즈가 흔해서
        # (실측: 탐론 A069 미등록) 자동 보정을 켜기 전에 알아야 합니다.
        from ..core.develop.optics import available_lenses, find_lens

        match = find_lens(self.record.metadata)
        self.panel.set_lens_info(match.summary, match.found)

        # 색 보정 표시는 이 사진의 기종 것만 보여야 합니다
        meta = self.record.metadata
        self.panel.set_camera(
            getattr(meta, "camera_make", "") or "",
            getattr(meta, "camera_model", "") or "",
        )

        # JPEG·HEIF는 카메라가 프로파일·기종 색·렌즈 보정을 이미 적용한
        # 결과입니다. 센서 기반 항목을 잠급니다 (set_raw_source 참고).
        from ..core.raw_io import is_editable_image

        self.panel.set_raw_source(not is_editable_image(self.record.path))

        # 얼굴 마스크에서 번호를 고르려면 몇 개가 잡혔는지 알아야 합니다
        focus = self.record.focus
        self.panel.set_face_count(len(focus.faces) if focus else 0)

        # 자동 조회가 실패했을 때 직접 고를 수 있도록 후보를 채웁니다.
        if not self.panel.lens_override.count():
            maker = None
            if self.record.metadata and self.record.metadata.camera_model:
                model = self.record.metadata.camera_model
                maker = "Sony" if model.startswith("ILCE") else None
            self.panel.lens_override.addItems(["", *available_lenses(maker=maker)])

        # 초점 정보는 캐시에서 옵니다. 없으면 표시 자체를 끕니다.
        has_focus = self.record.focus is not None and self.record.focus.roi is not None
        has_faces = bool(self.record.focus and self.record.focus.faces)
        self.show_roi.setEnabled(has_focus)
        self.show_faces.setEnabled(has_faces)
        self.show_eyes.setEnabled(has_faces)
        self.focus_zoom_button.setEnabled(has_focus)
        self.preview.reset_view()
        self._on_zoom(1.0)
        self.show_roi.setToolTip(
            tr("The region used for grading — green box (F)") if has_focus
            else tr("This shot has no analysis data")
        )
        self._eye_contours = None  # 컷이 바뀌면 다시 잽니다

        self._refresh_header()
        self._render()

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
            # 사유를 알 수 있으면 알려 줍니다. "실패"라고만 하면 파일이
            # 깨진 줄 알지만, 실제로는 멀쩡한 RAW인데 제조사 독점 압축이라
            # 못 푸는 경우가 있습니다(니콘 고효율 등).
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

    # ------------------------------------------------------------ 렌더링

    def _on_settings_changed(self) -> None:
        self._dirty = True
        # 렌더가 200ms쯤 걸립니다. 알려주지 않으면 멈춘 줄 압니다.
        self.preview.set_busy(True)
        self._render_timer.start()

        # 값이 바뀌면 진행 중인 Full Render 결과는 이미 낡았습니다. 곧바로
        # 멈추고 빠른 미리보기로 돌아갑니다. 예전에는 취소만 하고 다시
        # 예약해서, 슬라이더를 계속 움직이는 동안 무거운 렌더가 뜨고
        # 지기를 반복하며 조작이 무거워졌습니다.
        self._stop_full_render_for_edit()
        self._schedule_full_render()

    def set_locked(self, locked: bool, reason: str = "") -> None:
        """편집을 잠급니다. 내보내기가 도는 동안 씁니다.

        내보내기 워커는 이 레코드들의 `develop`과 등급을 한 장씩 읽어 갑니다.
        그 사이에 값을 바꾸면 앞 장은 옛 설정으로, 뒷 장은 새 설정으로 나가
        같은 배치에서 색이 갈립니다. 되돌리기 로그도 실제와 어긋납니다.
        막을 수 없는 일이 아니라 막아야 하는 일입니다.
        """
        self._locked = locked
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
        """편집이 들어오면 Full Render를 즉시 접고 프리뷰 상태로 되돌립니다.

        모드 자체는 켜 둡니다 — 손을 떼면 _full_render_timer가 다시
        고화질로 그려 줍니다. 여기서 끄는 것은 '지금 돌고 있는 작업'뿐입니다.
        """
        if (self._final_worker is None and not self._waiting_for_slot
                and not self._full_render_timer.isActive()):
            return
        self._abandon_render()
        self._set_full_render_state(busy=False)
        self.preview.set_busy(False)

    def _on_crop_mode(self, enabled: bool) -> None:
        settings = self.panel.settings().geometry
        self.preview.set_crop(
            settings.crop_left, settings.crop_top,
            settings.crop_right, settings.crop_bottom,
        )
        self.preview.set_ratio(self._ratio_value(settings.ratio))
        self.preview.set_crop_mode(enabled)
        self._render()

    def _ratio_value(self, ratio) -> float | None:
        """비율 설정을 실제 숫자로.

        ORIGINAL은 원본 종횡비라 이미지를 봐야 정해집니다. 고정 표에 없어서
        예전에는 조용히 '자유'처럼 동작했습니다.
        """
        from ..core.develop import CropRatio

        if ratio is CropRatio.ORIGINAL and self._source is not None:
            height, width = self._source.shape[:2]
            return width / height if height else None
        return ratio.value_ratio if ratio else None

    def _on_crop_dragged(self, left: float, top: float, right: float, bottom: float) -> None:
        """이미지 위에서 끈 결과를 슬라이더에 반영합니다.

        렌더는 여기서 하지 않는다 — 드래그 중 매 픽셀마다 다시 그리면
        따라오지 못합니다. 놓는 순간(crop_finished)에 한 번만 그립니다.
        """
        for key, value in (
            ("geo.crop_left", left * 100.0), ("geo.crop_top", top * 100.0),
            ("geo.crop_right", right * 100.0), ("geo.crop_bottom", bottom * 100.0),
        ):
            self.panel.rows[key].set_value(value, silent=True)
        self._dirty = True

    def _on_pick_mode(self, key: str) -> None:
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
        """미리보기에서 찍은 지점의 색조를 패널에 전달합니다."""
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
        """경고 문구와 실제 화소 비율.

        예전에는 클리핑 여부가 **바뀔 때만** 문구를 갱신했습니다. 그래서
        표시를 켠 순간에는 아무 안내도 없었고, 화면에 색이 안 보이면
        고장인지 정말 클리핑이 없는 건지 구분할 방법이 없었습니다.
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

    def _render(self) -> None:
        if self._source is None:
            return

        settings = self.panel.settings()

        if self.before_after.isChecked():
            image = self._source
        else:
            # 크롭 모드에서는 크롭을 적용하지 않고 보여 줍니다. 잘라낸 결과를
            # 그리면 그 위에서 크롭 범위를 다시 잡을 수가 없습니다.
            if self.preview._crop_mode:
                settings = replace(
                    settings, geometry=replace(settings.geometry, **_FULL_CROP)
                )
            # 워터마크와 정보 띠는 빼고 보정만 적용합니다. 정보 띠의 검은 바나
            # 워터마크 글자가 섞이면 히스토그램·클리핑 경고가 사진의 계조를
            # 반영하지 못해, 보정값이 바뀐 것처럼 보입니다. 표기는 아래에서
            # 따로 얹습니다.
            image = engine.apply_settings(
                self._source,
                replace(settings, watermark=WatermarkSettings(),
                        exif_strip=ExifStripSettings()),
                self.record.path, self.record.metadata,
                wb=self._wb.engine_wb if self._wb else None,
                main_face_box=self.record.main_face_norm,
            )

        # 소스와 중간 연산은 float(정밀도 유지)이므로 표시 직전에만 8비트로.
        image = to_display(image)
        self.histogram.set_image(image)
        # 곡선 편집기 배경에도 같은 히스토그램을 깔아 줍니다.
        # 어느 계조를 만지고 있는지 보여야 곡선을 정확히 끌 수 있습니다.
        self.panel.set_curve_histogram(self.histogram.luminance())

        # 클리핑 오버레이는 히스토그램 계산 뒤에 얹습니다 (히스토그램은
        # 실제 이미지를 반영해야 하고, 색칠은 화면 표시용입니다).
        # 점멸시켜야 하므로 칠하기 직전 상태를 따로 들고 있습니다 — 매번
        # 보정 파이프라인을 다시 돌리면 초당 두 번씩 몇백 ms를 태웁니다.
        self._clip_base = image
        show_shadow, show_highlight = self._clip_overlay_state()
        if (show_shadow or show_highlight) and self._clip_blink_on:
            image = clip_overlay(image, show_shadow, show_highlight)
        self._refresh_clip_label()

        # 계조 판단이 끝난 뒤에 결과물 표기(워터마크·정보 띠)를 얹습니다.
        if not self.before_after.isChecked():
            image = engine.apply_overlays(
                image, settings, self.record.path, self.record.metadata
            )

        if self._any_overlay_on() and self.record.focus:
            image = self._draw_roi(image)

        overlay_mask = self.panel.overlay_mask()
        if overlay_mask is not None:
            image = self._draw_mask_overlay(image, overlay_mask)

        self.preview.set_pixmap(bgr_to_pixmap(image))
        self.preview.set_busy(False)

    def _full_render_target(self) -> int:
        """지금 화면에 실제로 필요한 긴 변 픽셀 수.

        고정 2200px로 뽑으면 창이 크거나 확대했을 때는 모자라고, 창이 작을
        때는 낭비입니다. 뷰포트 크기 × 배율(× 화면 배율)만큼만 만듭니다.
        원본보다 커지면 resize_long_edge가 원본에서 멈춥니다.
        """
        viewport = max(self.preview.width(), self.preview.height())
        try:
            ratio = float(self.preview.devicePixelRatioF())
        except AttributeError:
            ratio = 1.0
        needed = int(viewport * max(1.0, self.preview.zoom()) * max(1.0, ratio))
        return max(PREVIEW_LONG_EDGE, needed)

    def _set_full_render_state(self, busy: bool) -> None:
        """Full Render 버튼의 글자·색·활성 여부를 한곳에서 정합니다.

        네 상태입니다.

        - 꺼짐(회색): 누르면 시작
        - 켜짐(파랑): 결과가 화면에 있음
        - 생성 중(주황, 잠금): 지금 돌고 있음. 잠금은 연타로 렌더가 겹치는
          것을 막습니다 — 겹치면 메모리가 두 배(실측 2.8GB → 5.5GB)가 되어
          작은 PC에서 그대로 죽습니다.
        - 대기 중(주황, 잠금): 앞 렌더가 아직 안 끝나 출발을 못 하는 상태
        """
        if busy:
            self.final_button.setText(
                tr("Waiting…") if self._final_worker is None else tr("Rendering…"))
            self.final_button.setStyleSheet(theme.BUSY_BUTTON)
        else:
            self.final_button.setText("Full Render")
            self.final_button.setStyleSheet(theme.TOGGLE_BUTTON)

        # 활성 여부는 오직 잠금 타이머가 정합니다. 렌더가 도는 동안에도
        # '끄기'는 허용해야 합니다 — 결과를 버리는 일이라 위험하지 않고,
        # 5~8초짜리 렌더에 갇히면 그것대로 멈춘 것처럼 보입니다.
        # 겹쳐 도는 것은 버튼이 아니라 `_FULL_RENDER_SLOT`이 막습니다.
        self.final_button.setEnabled(not self._full_render_lock.isActive())

    def _lock_full_render_button(self) -> None:
        """버튼을 잠시 잠급니다. 연타로 무거운 렌더가 겹치는 것을 막습니다."""
        self.final_button.setEnabled(False)
        self._full_render_lock.start(FULL_RENDER_LOCKOUT_MS)

    def _release_full_render_button(self) -> None:
        """잠금 해제. 글자와 색은 지금 상태를 그대로 둡니다."""
        self.final_button.setEnabled(True)

    def _on_full_render_toggled(self, enabled: bool) -> None:
        """Full Render 모드 on/off."""
        self._lock_full_render_button()
        if enabled:
            self._set_full_render_state(busy=False)
            self._schedule_full_render()
        else:
            self._abandon_render()
            # 끄면 들고 있던 디모자이크 원본(약 390MB)도 놓습니다. 다시 켤
            # 때 5초를 더 쓰지만, 안 쓰는 동안 그만한 메모리를 붙들고 있는
            # 편이 더 나쁩니다 — 8GB PC에서는 그 자체로 부담입니다.
            self._drop_demosaic()
            self._set_full_render_state(busy=False)
            self._render()  # 빠른 미리보기로 되돌립니다

    def _schedule_full_render(self) -> None:
        """조작이 멈춘 뒤에 고화질로 다시 그립니다.

        슬라이더를 움직이는 동안 매번 풀 해상도로 현상하면 조작이 불가능해서,
        손을 뗀 뒤 잠깐 조용할 때만 돌립니다.
        """
        if not self.final_button.isChecked():
            return
        self._abandon_render()
        self._full_render_timer.start()

    def _abandon_render(self) -> None:
        """예약을 접고, 돌던 렌더의 결과를 버립니다.

        **멈추지는 못합니다.** cancel()은 플래그만 세우고, rawpy 디모자이크는
        그 플래그를 볼 지점이 없는 단일 C 호출입니다. 그래서 스레드는 몇 초 더
        메모리를 쥔 채 계속 돕니다. 다음 렌더가 그 위에 겹치지 않도록
        `_FULL_RENDER_SLOT`이 끝까지 물고 있다가 스스로 빠집니다.
        """
        self._full_render_timer.stop()
        self._waiting_for_slot = False
        worker = self._final_worker
        self._final_worker = None
        if worker is not None:
            worker.cancel()
            self._retire_worker(worker)

    def _retire_worker(self, worker: "FinalRenderWorker") -> None:
        """취소한 워커를 스레드가 끝날 때까지 붙잡아 둡니다.

        cancel()은 플래그만 세웁니다 — 스레드는 그 플래그를 확인할 때까지
        계속 돕니다. 그 상태에서 마지막 참조를 놓으면 파이썬이 QThread를
        파괴하고, Qt는 "실행 중인 스레드가 파괴됨"을 치명적 오류로 보고
        qFatal()로 프로세스를 즉사시킵니다(Qt6Core, 0xc0000409).

        실제로 이 경로에서 크래시했습니다. Full Render를 켠 채 슬라이더를
        움직이면 _schedule_full_render가 매번 여기를 지나갑니다.

        source_ready까지 끊습니다. 결과(done)만 끊으면 은퇴한 워커가 나중에
        디모자이크 원본을 창에 밀어 넣어, 다음 렌더가 낡은 화소를 재사용합니다.
        """
        _disconnect_worker(worker)
        if not worker.isRunning():
            return
        self._retired_workers.append(worker)
        worker.finished.connect(self._reap_workers)

    def _shutdown_workers(self) -> None:
        """이 창이 들고 있던 렌더를 놓습니다. 닫기와 종료 양쪽에서 씁니다.

        기다리지 않습니다. 아직 도는 스레드는 모듈 수준으로 넘겨(참조를
        살려 둔 채) 제 속도로 끝나게 둡니다. 기다리면 rawpy 디모자이크가
        길어질 때 창이 굳고, 기다림이 모자라면 크래시가 납니다.
        """
        current = self._final_worker
        self._final_worker = None
        retired = self._retired_workers
        self._retired_workers = []

        for worker in ([current] if current is not None else []) + retired:
            try:
                worker.cancel()
                # 결과는 더 이상 쓰지 않습니다. finished만 남겨 두었다가
                # 스스로 목록에서 빠지게 합니다.
                _disconnect_worker(worker)
                if worker.isRunning():
                    _detach_until_finished(worker)
            except RuntimeError:
                pass  # 이미 정리된 객체

    def _reap_workers(self) -> None:
        """끝난 워커를 목록에서 치웁니다."""
        self._retired_workers = [
            worker for worker in self._retired_workers if worker.isRunning()
        ]

    def _show_final_preview(self) -> None:
        """RAW를 디모자이크한 최종 화질 결과를 만들어 보여 줍니다."""
        if self._source is None or not self.final_button.isChecked():
            self._waiting_for_slot = False
            return
        if self._final_worker is not None:
            return  # 이미 만드는 중

        # 앞 렌더가 아직 메모리를 쥐고 있으면 출발하지 않습니다. 겹치면
        # 27MP 기준 2.8GB가 5.5GB가 되고, 작은 PC는 여기서 죽습니다.
        # 끝나는 대로 다시 시도하도록 예약만 걸어 둡니다.
        if full_render_in_flight():
            self._waiting_for_slot = True
            self._set_full_render_state(busy=True)
            self.preview.set_busy(True)
            self._slot_timer.start(200)
            return

        self._waiting_for_slot = False
        settings = self.panel.settings()

        # 확대 중이면 보이는 데만 만듭니다. 등배에서는 전체가 보이므로
        # 잘라 봐야 이득이 없고, 잘린 결과를 화면에 맞추기만 번거롭습니다.
        region = None
        if self.preview.zoom() > 1.01 and settings.geometry.is_neutral():
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
        )
        worker.source_ready.connect(self._keep_demosaic)
        worker.done.connect(self._on_final_ready)
        worker.failed.connect(self._on_final_failed)
        worker.finished.connect(self._clear_final_worker)
        self._final_worker = worker

        # 자리를 먼저 잡고 출발합니다. 스레드가 끝나면 스스로 비웁니다 —
        # 결과를 버렸든(cancel) 아니든 메모리는 그때까지 물려 있으므로,
        # 반납 시점은 '취소'가 아니라 '실제 종료'여야 합니다.
        _FULL_RENDER_SLOT.add(worker)
        worker.finished.connect(lambda w=worker: _FULL_RENDER_SLOT.discard(w))

        self._set_full_render_state(busy=True)
        self.preview.set_busy(True)
        worker.start()

    def _compose_region(self, patch: np.ndarray,
                        region: tuple[float, float, float, float]) -> np.ndarray:
        """보이는 영역만 만든 결과를 전체 프레임 자리에 끼워 넣습니다.

        화면은 이미지 하나를 놓고 줌·팬을 겁니다. 잘린 조각을 그대로 올리면
        줌 계산과 초점 영역 좌표가 전부 어긋납니다. 그래서 프레임 크기의
        판을 만들고(안 보이는 곳은 기존 미리보기를 늘려서 채웁니다) 그 위
        제자리에 선명한 조각을 얹습니다. 결과는 지금까지와 같은 '한 장'이라
        아래쪽 코드는 아무것도 달라지지 않습니다.
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
        """이 컷의 디모자이크 결과가 이미 있으면 돌려줍니다.

        확대·이동할 때마다 5.1초짜리 디모자이크를 다시 하지 않기 위한
        것입니다(실측 R6M3 27MP). 들고 있는 값은 27MP 기준 약 390MB이므로
        **한 컷치만** 둡니다 — 컷을 넘기면 곧바로 놓습니다.
        """
        if self._demosaic_path == path:
            return self._demosaic_cache
        return None

    def _keep_demosaic(self, image) -> None:
        """워커가 막 만든 디모자이크 원본을 받아 둡니다.

        **이름표는 보낸 워커의 경로로 답니다.** 렌더가 도는 사이에 컷을
        넘기면 self.record는 이미 다음 컷인데 이 신호는 앞 컷의 것입니다.
        지금 레코드 기준으로 이름을 붙이면 다음 Full Render가 `_demosaic_for`
        에서 그것을 히트시켜 **앞 컷의 화소를 다음 컷의 최종 화질 결과로**
        보여 줍니다. 예외도 표시도 없이 판단 근거만 바뀌므로, 사용자는
        B를 보고 있다고 믿으면서 A를 보고 셀렉하게 됩니다.

        지금 컷의 것이 아니면 그냥 버립니다. 27MP 기준 약 390MB라 "혹시
        돌아올지 모르니" 붙들고 있을 값이 아닙니다.
        """
        worker = self.sender()
        path = getattr(worker, "_path", None) or self.record.path
        if path != self.record.path:
            return
        self._demosaic_cache = image
        self._demosaic_path = path

    def _drop_demosaic(self) -> None:
        """들고 있던 원본을 놓습니다. 390MB짜리라 오래 쥐고 있으면 안 됩니다."""
        self._demosaic_cache = None
        self._demosaic_path = None

    def _retry_when_slot_free(self) -> None:
        """앞 렌더가 끝나기를 기다렸다가 출발합니다."""
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
        """완성된 결과를 화면에 올립니다.

        **보낸 워커를 직접 확인합니다.** 큐에 이미 실린 신호는 disconnect
        해도 배달됩니다. self._final_worker로 검사하면 이미 새 워커로 바뀐
        뒤라 검사를 통과해 버리고, 낡은 그림이 새 화면을 덮어씁니다.
        """
        worker = self.sender()
        if worker is None or worker is not self._final_worker:
            return
        if worker._path != self.record.path:
            return
        if worker.generation != self._final_generation:
            return
        # 보정이 중립이면 apply_settings가 입력(float)을 그대로 돌려주므로
        # _render와 똑같이 표시 직전에 8비트로 변환합니다.
        self.preview.set_busy(False)
        image = to_display(image)
        if self._final_region is not None:
            image = self._compose_region(image, self._final_region)

        # 표기·ROI·클리핑은 Full Render에서도 그대로 보여야 합니다. 예전에는
        # 여기서 곧장 화면에 올려서, Full Render를 켜는 순간 클리핑 표시와
        # 초점 영역이 조용히 사라졌습니다.
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
            return  # 은퇴시킨 워커가 뒤늦게 끝난 것 — 현재 상태를 건드리면 안 됩니다
        self._final_worker = None
        self._set_full_render_state(busy=False)

    # -------------------------------------------------------- 클리핑 표시

    def _clip_overlay_state(self) -> tuple[bool, bool]:
        return (self.shadow_clip_button.isChecked(),
                self.highlight_clip_button.isChecked())

    def _on_clip_overlay_toggled(self, _checked: bool = False) -> None:
        """클리핑 표시를 켜고 끕니다."""
        show_shadow, show_highlight = self._clip_overlay_state()
        # 히스토그램 위젯도 같은 상태를 들고 있어야 삼각형 표시가 맞습니다
        self.histogram.set_overlay_state(show_shadow, show_highlight)

        if show_shadow or show_highlight:
            self._clip_blink_on = True
            self._clip_blink_timer.start(CLIP_BLINK_MS)
        else:
            self._clip_blink_timer.stop()
            self._clip_blink_on = True
        self._render()

    def _sync_clip_buttons(self, show_shadow: bool, show_highlight: bool) -> None:
        """히스토그램 쪽에서 토글된 경우 버튼을 맞춥니다."""
        for button, value in ((self.shadow_clip_button, show_shadow),
                              (self.highlight_clip_button, show_highlight)):
            if button.isChecked() != value:
                button.blockSignals(True)
                button.setChecked(value)
                button.blockSignals(False)
        self._on_clip_overlay_toggled()

    def _blink_clip_overlay(self) -> None:
        """점멸 한 틱. 보정을 다시 계산하지 않고 칠하기만 뒤집습니다."""
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
        """점멸용 빠른 경로 — 표기·ROI만 다시 얹고 화면에 올립니다."""
        settings = self.panel.settings()
        if not self.before_after.isChecked():
            image = engine.apply_overlays(
                image, settings, self.record.path, self.record.metadata
            )
        if self._any_overlay_on() and self.record.focus:
            image = self._draw_roi(image)
        self.preview.set_pixmap(bgr_to_pixmap(to_display(image)))

    def _resolve_roi_scale(self, sensor_width: int) -> float:
        """화면에 그릴 때 ROI 좌표에 곱할 배율.

        ROI와 얼굴 박스는 **분석에 쓴 내장 프리뷰** 좌표계입니다. 예전에는
        그 프리뷰가 센서와 같은 가로라고 어림잡았는데, 파나소닉 S1R은 4700만
        화소(8392px)에 1920px짜리 프리뷰만 넣습니다. 그래서 박스가 4.37배
        어긋난 자리에 1/4 크기로 그려졌습니다(실측). 캐논은 풀 해상도
        프리뷰라 우연히 맞아서, 캐논만 보면 멀쩡해 보였습니다.

        이제는 분석이 기준 크기를 함께 남깁니다. 예전 캐시에는 없으므로
        그때만 프리뷰를 직접 읽어 재고, 그것도 실패하면 옛 어림을 씁니다.
        """
        display_width = self._source.shape[1] if self._source is not None else 1
        focus = self.record.focus

        reference = getattr(focus, "source_width", 0) if focus else 0
        if not reference and focus is not None and focus.roi:
            # v4 이전 캐시 — 좌표만 있고 기준이 없습니다. 한 번 읽어 잽니다.
            try:
                from ..core.raw_io import load_preview as _load_preview

                reference = _load_preview(self.record.path).shape[1]
            except Exception:  # noqa: BLE001
                log.debug("프리뷰 크기 확인 실패, 옛 어림값 사용", exc_info=True)

        if not reference:
            reference = max(1, sensor_width)

        # 그릴 때는 이 기준 폭에서 배율을 다시 냅니다(_draw_roi 참고).
        self._roi_reference_width = reference
        return display_width / reference

    def _explain_degraded(self) -> str:
        """왜 RAW를 못 풀었는지 한 줄로. 모르면 빈 문자열."""
        try:
            from ..core.nef_meta import unsupported_reason

            return unsupported_reason(self.record.path) or ""
        except Exception:  # noqa: BLE001 - 사유를 못 찾아도 표시는 계속합니다
            log.debug("미지원 사유 확인 실패", exc_info=True)
            return ""

    def _maybe_warn_stale_roi(self) -> None:
        """예전 캐시라 초점 영역 좌표를 믿을 수 없으면 알려 줍니다."""
        focus = self.record.focus
        if focus is None or not focus.roi:
            return
        if getattr(focus, "source_width", 0):
            return
        log.debug("%s: 예전 캐시의 초점 좌표 — 기준 크기를 직접 재서 씁니다",
                  self.record.path.name)

    # ------------------------------------------------- 주 피사체 수동 전환

    def _on_preview_clicked(self, rx: float, ry: float) -> None:
        """얼굴 위를 클릭하면 그 얼굴을 주 피사체로 삼습니다.

        얼굴 상자가 보이지 않는 상태에서 클릭이 무언가를 바꾸면 놀랍기만
        하므로, 표시를 켜 둔 동안에만 받습니다.
        """
        if not self.show_faces.isChecked():
            return
        focus = self.record.focus
        if focus is None or not focus.faces:
            return
        if not self.panel.settings().geometry.is_neutral():
            return  # 크롭·회전이 걸리면 좌표계가 달라 클릭 위치를 못 믿습니다

        reference = self._roi_reference_width or 1
        source_h = getattr(focus, "source_height", 0) or reference
        px, py = rx * reference, ry * source_h

        # 겹친 얼굴에서는 작은 쪽을 고릅니다 — 큰 얼굴 안의 작은 얼굴은
        # 클릭으로 고를 방법이 그것뿐입니다.
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
        """주 피사체를 바꾸고 **판정을 그 얼굴 기준으로 다시** 냅니다.

        표시만 옮기면 점수와 등급은 엉뚱한 얼굴 그대로 남습니다. 분석 때와
        같은 함수(analyze_focus)를 얼굴만 지정해 다시 태워, ROI·선명도·배경
        선명도가 모두 새 얼굴 기준이 되게 합니다.
        """
        from ..core.focus import analyze_focus
        from ..core.raw_io import load_preview

        try:
            preview = load_preview(self.record.path)
            focus = analyze_focus(preview, force_main_face=index)
        except Exception:  # noqa: BLE001 - 못 바꿔도 보정 작업은 계속돼야 합니다
            log.warning("%s: 주 피사체 재판정 실패", self.record.path.name,
                        exc_info=True)
            return

        self.record.focus = focus
        self.record.manual_main_face = index
        self._eye_contours = None
        self.main_face_changed.emit(self.record)
        self._refresh_header()
        self._render()

    def _any_overlay_on(self) -> bool:
        """표시 항목이 하나라도 켜져 있는가. 전부 꺼져 있으면 복사조차 안 합니다."""
        return (self.show_roi.isChecked() or self.show_faces.isChecked()
                or self.show_eyes.isChecked())

    def visible_face_indices(self) -> list[int]:
        """화면에 그릴 얼굴 번호. 주 피사체 전환 대상도 이 목록입니다.

        확신이 낮은 검출은 뺍니다. 실촬영 표본을 눈으로 보면 0.60~0.75
        구간은 스피커 콘·흰 장갑·어두운 얼룩이 대부분이라, 그려 봐야
        "저게 왜 얼굴이냐"는 의문만 남깁니다(실제 리포트).
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
        """화면에 그릴 얼굴들의 눈 윤곽 — **분석 프리뷰 좌표계**입니다.

        얼굴 상자와 같은 좌표계로 맞춰 두면 그릴 때 배율 하나만 곱하면
        됩니다. 컷당 한 번만 재고 캐시합니다(얼굴당 1.3ms).
        """
        if self._eye_contours is not None:
            return self._eye_contours

        self._eye_contours = []
        focus = self.record.focus
        source = self._source
        if focus is None or source is None or not focus.faces:
            return self._eye_contours

        reference = self._roi_reference_width or 1
        to_source = source.shape[1] / reference  # 분석 좌표 → 지금 이미지
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
        """켜 둔 표시 항목을 이미지 위에 얹습니다.

        좌표는 전부 원본 프리뷰 기준이라 축소 배율을 곱해야 맞습니다.
        크롭이나 회전이 걸려 있으면 좌표계가 전부 달라지므로 그리지 않습니다.
        """
        if not self.panel.settings().geometry.is_neutral():
            return image

        # 배율은 저장해 둔 값이 아니라 **지금 그리는 이미지**에서 냅니다.
        # Full Render 결과는 미리보기보다 크기가 달라서, 저장값을 쓰면
        # 고화질로 바꾸는 순간 박스가 어긋납니다.
        reference = self._roi_reference_width or 1
        scale = image.shape[1] / reference
        focus = self.record.focus
        marked = image.copy()
        thin = max(1, int(image.shape[1] / 1200))  # 기존의 1/3 굵기

        def draw(box, colour, width) -> None:
            x, y, w, h = box
            cv2.rectangle(
                marked,
                (int(x * scale), int(y * scale)),
                (int((x + w) * scale), int((y + h) * scale)),
                colour, width,
            )

        # 검출된 얼굴을 옅게 그립니다. 주 피사체만 보여 주면 "왜 저 얼굴이
        # 뽑혔는지" 알 수 없고, 다른 얼굴을 놓친 건지도 모릅니다.
        if self.show_faces.isChecked():
            for index in self.visible_face_indices():
                if index != focus.main_face:
                    draw(focus.faces[index], (170, 170, 170), thin)

        if self.show_eyes.isChecked():
            for contour in self._eye_rings():
                cv2.polylines(marked, [np.round(contour * scale).astype(np.int32)],
                              True, (240, 200, 60), thin)

        # 초점 ROI (눈/얼굴/타일) — 실제로 선명도를 잰 자리
        if self.show_roi.isChecked() and focus.roi:
            draw(focus.roi, (80, 220, 80), thin)

        # 주 피사체는 빨간 사각형. 초점 기준으로 고른 결과입니다.
        # 굵기는 다른 얼굴과 같게 두고 **색으로만** 구분합니다. 두 배로
        # 그렸더니 확대했을 때 선이 얼굴을 덮어 정작 초점을 못 봤습니다.
        if self.show_faces.isChecked() and 0 <= focus.main_face < len(focus.faces):
            draw(focus.faces[focus.main_face], (60, 60, 235), thin)
        return marked

    # -------------------------------------------------- 방사형·선형 마스크 조작

    def _sync_mask_shape(self) -> None:
        """선택한 마스크가 방사형·선형이면 이미지 위에 조작점을 띄웁니다.

        예전에는 이 두 종류만 기본 위치에 박혀 있었습니다. 파라미터는 전부
        정규화 좌표인데 그 값을 만질 UI가 없어서, 스포트라이트는 언제나
        화면 정중앙이었습니다.
        """
        from ..core.develop.masks import SIZE_KINDS, _size_factor

        mask = self.panel.shape_mask()
        if mask is None:
            self.preview.set_shape(None)
            return
        # 범위(size)는 방사형에만 걸립니다. 선형에 곱하면 있지도 않은
        # 반경을 줄이는 셈이 되어 화면과 결과가 어긋납니다.
        size = _size_factor(mask) if mask.kind in SIZE_KINDS else 1.0
        self.preview.set_shape(mask.kind.value, mask.params, size=size)

    def _on_shape_dragged(self, params: dict) -> None:
        """이미지 위에서 끈 도형 좌표를 마스크에 담아 둡니다.

        여기서 렌더하지 않습니다 — 끄는 동안 매번 다시 그리면 따라오지
        못합니다(크롭 드래그와 같은 방식). 윤곽선은 ImageView가 스스로
        그리고, 실제 재렌더는 shape_finished에서 한 번만 돕니다.
        """
        self.panel.set_mask_params(params, silent=True)
        self._dirty = True

    # ------------------------------------------------------------ 브러시

    _BRUSH_CANVAS = 512
    """브러시 알파를 담는 해상도. 프리셋 파일에 실려 다니므로 크게 잡지 않습니다."""

    def _on_brush_mode(self, enabled: bool) -> None:
        """칠하기 모드에서는 크롭·스포이드를 끄고 브러시만 받습니다."""
        self.preview.set_brush_mode(enabled)
        self._sync_brush_cursor()
        if enabled:
            # 칠하는 동안은 영역이 보여야 어디를 칠했는지 압니다
            self.panel.mask_overlay_check.setChecked(True)

    def _sync_brush_cursor(self) -> None:
        """붓 크기·지우개 상태를 미리보기 원에 반영합니다."""
        self.preview.set_brush_radius(self.panel.brush_radius_ratio())
        self.preview.set_brush_erasing(self.panel.is_erasing())

    def _brush_canvas(self, mask) -> np.ndarray:
        """선택 마스크의 알파를 편집용 캔버스로. 없으면 빈 캔버스를 만듭니다.

        이미지 비율에 맞춰야 칠한 모양이 찌그러지지 않습니다.
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
        """이미지 위에서 칠한 한 점을 마스크 알파에 반영합니다."""
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
        # 지우개는 같은 붓으로 0을 칠합니다
        value = 0.0 if self.panel.is_erasing() else 1.0
        cv2.circle(canvas, center, radius, value, -1, lineType=cv2.LINE_AA)

        self.panel.set_brush_bitmap(encode_brush(canvas))

    def _draw_mask_overlay(self, image: np.ndarray, mask) -> np.ndarray:
        """선택한 마스크가 덮는 영역을 빨갛게 표시합니다 (반투명).

        세기(opacity)와 무관하게 영역의 모양을 보여줘야 하므로 고정 강도로
        칠합니다. 얼굴이 없어 마스크를 못 만들면 그대로 둡니다.
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

    # ------------------------------------------------------------ 등급 / 적용

    def set_grade(self, grade: Grade) -> None:
        self.record.manual_grade = grade
        self._refresh_header()
        self.records_changed.emit()

    def apply_to_all(self) -> None:
        """이 창에서 맞춘 보정을 목록 전체에 적용합니다.

        크롭·기울이기·회전은 제외합니다. 구도는 컷마다 달라서 한 장에서 잡은
        크롭을 다른 장에 씌우면 피사체가 잘려 나갑니다. 현재 컷의 크롭은
        그대로 두고, 나머지 컷에는 색보정만 나눠 줍니다.
        """
        settings = self.panel.settings()
        self._commit_settings()

        shared = settings.without_geometry()
        value = None if shared.is_neutral() else shared

        for record in self.records:
            if record is self.record:
                continue  # 현재 컷은 크롭까지 포함해 이미 저장했습니다
            if value is None:
                record.develop = None
            elif record.develop is not None:
                # 다른 컷이 이미 잡아 둔 크롭은 지키고 나머지만 덮어씁니다
                record.develop = replace(
                    shared, geometry=record.develop.geometry
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
        """예약된 렌더와 백그라운드 스레드를 모두 끊고 닫습니다.

        이 창은 WA_DeleteOnClose라 닫히는 즉시 파이썬 객체가 사라집니다.
        그 뒤에 신호가 하나라도 도착하면 이미 없어진 C++ 객체를 건드려
        네이티브 크래시(Qt6Core fail-fast)가 납니다. 그래서 타이머를 멈추고
        **finished까지** 끊습니다 — 예전에는 done/failed만 끊어서, 워커가
        끝나며 보내는 finished가 삭제된 창의 슬롯을 호출할 수 있었습니다.
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
