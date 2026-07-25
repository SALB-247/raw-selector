"""분석 시작 다이얼로그 — 무엇을 몇 장 분석하는지 보여 주고 옵션을 고릅니다.

분석 버튼을 눌렀을 때만 뜹니다. 폴더를 열 때의 자동 분석은 지금처럼 바로
시작합니다 — 가장 흔한 흐름에 클릭을 하나 얹지 않기 위해서입니다. 이 창은
"다시 분석"의 자리이므로 캐시를 무시하는 선택지가 여기에 있습니다.

옵션이 늘 때 툴바가 아니라 이 창이 받도록 만들었습니다(정밀 분석 묶음).
설정값은 세션의 AnalyzeConfig에 반영되어 다음 분석에도 유지됩니다.
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
from ..core.pipeline import estimate_analysis_seconds, format_duration
from .i18n import tr


@dataclass(frozen=True)
class AnalysisOptions:
    """다이얼로그가 돌려주는 선택."""

    use_cache: bool
    noise_compensation: bool
    af_roi_hint: bool


class AnalysisStartDialog(QDialog):
    """사진 수·캐시 상태를 먼저 보여 주고, 캐시·정밀 옵션을 고르게 합니다."""

    def __init__(self, photo_count: int, cached_count: int,
                 analyze: AnalyzeConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Start analysis"))
        self._photo_count = photo_count
        self._cached_count = cached_count

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

        # ---------------- 캐시
        self.use_cache = QCheckBox(tr("Use cached results"))
        self.use_cache.setChecked(cached_count > 0)
        self.use_cache.setEnabled(cached_count > 0)
        if cached_count > 0:
            self.use_cache.setToolTip(
                tr("Unchecked: ignore the cache and re-analyse every photo."))
        else:
            # 비활성이어도 "왜 못 누르는지"는 보여야 합니다 — 잠긴 컨트롤에
            # 이유가 없으면 사용자가 버그로 읽습니다 (JPEG 잠금에서 배운 것).
            self.use_cache.setToolTip(
                tr("No usable cache for these photos and settings."))
        layout.addWidget(self.use_cache)

        # ---------------- 정밀 분석
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

        self.af_hint = QCheckBox(tr("Use camera AF point when no face is found"))
        self.af_hint.setChecked(analyze.af_roi_hint)
        self.af_hint.setToolTip(tr(
            "Reads the autofocus position the camera recorded (Sony, Canon\n"
            "CR3, Nikon) and judges that area instead of guessing the\n"
            "sharpest tile — only for photos where no face was detected.\n"
            "Faces and eyes always take priority."))
        box.addWidget(self.af_hint)

        layout.addWidget(precise)

        self.estimate = QLabel("")
        self.estimate.setStyleSheet("color: #9a9aa2;")
        layout.addWidget(self.estimate)

        buttons = QDialogButtonBox()
        self.start_button = buttons.addButton(
            tr("Start analysis"), QDialogButtonBox.AcceptRole)
        # 표준 Cancel 버튼은 Qt 내장 번역이 있어야 한국어가 되는데, 그 카탈로그를
        # 안 싣습니다. 우리 tr()로 라벨을 직접 답니다.
        buttons.addButton(tr("Cancel"), QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.use_cache.toggled.connect(self._refresh_estimate)
        self._refresh_estimate()
        self.start_button.setDefault(True)

    # ------------------------------------------------------------------

    def _refresh_estimate(self) -> None:
        """옵션에 따라 몇 장을 새로 분석하고 얼마나 걸릴지 갱신합니다."""
        reused = self._cached_count if self.use_cache.isChecked() else 0
        pending = max(0, self._photo_count - reused)
        seconds = estimate_analysis_seconds(pending)
        if pending == 0:
            text = tr("Everything is cached — results will appear instantly.")
        else:
            text = tr("{pending} photos to analyse — {duration}").format(
                pending=pending, duration=format_duration(seconds))
            if reused:
                text += tr(" ({reused} reused from cache)").format(reused=reused)
        self.estimate.setText(text)

    def options(self) -> AnalysisOptions:
        return AnalysisOptions(
            use_cache=self.use_cache.isChecked() and self.use_cache.isEnabled(),
            noise_compensation=self.noise_comp.isChecked(),
            af_roi_hint=self.af_hint.isChecked(),
        )

    @staticmethod
    def ask(photo_count: int, cached_count: int, analyze: AnalyzeConfig,
            parent=None) -> AnalysisOptions | None:
        """다이얼로그를 띄우고, 취소면 None."""
        dialog = AnalysisStartDialog(photo_count, cached_count, analyze, parent)
        dialog.setModal(True)
        accepted = dialog.exec() == QDialog.Accepted
        options = dialog.options() if accepted else None
        dialog.deleteLater()
        return options
