"""Re-scoring a photo against a main subject the user picked by hand.

The loupe used to hold this inline. It is needed in two places now - the
loupe, when the user clicks another face, and the edits store, when a
folder is opened again and the picks made last time have to be put back -
so it lives here, without Qt.
"""

from __future__ import annotations

import logging

from .config import AnalyzeConfig
from .focus import analyze_focus
from .raw_io import load_preview
from .types import FocusResult, ImageRecord

log = logging.getLogger(__name__)


def reanalyze_with_main_face(record: ImageRecord, config: AnalyzeConfig,
                             index: int) -> FocusResult | None:
    """Runs the batch's own focus analysis again with one face pinned.

    Moving only the overlay would leave the score and the grade on the
    wrong face; the ROI, the sharpness and the background sharpness all
    have to be measured against the chosen one. It has to run with the
    **same settings** as the batch - without them laplacian_k and the
    noise subtraction fall back to defaults and af_face dies at -1, so the
    score would differ from the batch for no reason but the main subject.
    None when the file cannot be read.
    """
    try:
        preview = load_preview(record.path)
        af_box = None
        af_tracking = False
        if record.metadata is not None:
            from .maker_meta import af_preview_box

            af_box = af_preview_box(
                record.path, record.metadata.orientation,
                preview.shape[1], preview.shape[0],
            )
            af_tracking = "tracking" in (record.metadata.af_area_mode or "").lower()
        return analyze_focus(
            preview,
            detect_long_edge=config.detect_long_edge,
            laplacian_k=config.laplacian_k,
            tenengrad_k=config.tenengrad_k,
            force_main_face=index,
            af_box=af_box,
            use_af_roi=config.af_roi_hint,
            center_priority=config.center_priority,
            noise_compensation=config.noise_compensation,
            af_tracking=af_tracking,
        )
    except Exception:  # noqa: BLE001 - a pick that cannot be re-scored is dropped, not fatal
        log.warning("%s: 주 피사체 재판정 실패", record.path.name, exc_info=True)
        return None
