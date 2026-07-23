"""정답 라벨을 만드는 도구.

왜 필요한가
-----------
두 기능이 **정답이 없어서** 막혀 있습니다.

1. **눈 감김 감점** — 판정기를 네 가지 만들었는데 전부 62~72%였습니다.
   원인을 파 보니 방법이 아니라 제가 붙인 라벨이었습니다. 32장 중 최소
   6장이 명백히 틀렸고 2장은 사람도 판정 불가였습니다. 40% 가까이 오염된
   정답 위에서는 어떤 방법을 써도 70%가 천장입니다.

2. **소니 AF 포인트** — 태그는 100% 들어 있고 대상의 몸통을 가리킵니다.
   그런데 "AF가 가리키는 사람"과 "우리가 고른 주 피사체"가 얼굴 2개 이상인
   컷에서 40%만 일치합니다. **어느 쪽이 맞는지는 알 수 없습니다.**

둘 다 촬영자만 답할 수 있는 질문입니다. 이 도구는 그 답을 기계가 읽을 수
있는 형태로 받습니다.

쓰는 법
-------
    python tools/label_tool.py eyes  <RAW 폴더>
    python tools/label_tool.py focus <RAW 폴더>

키보드만으로 넘어갑니다. 중간에 닫아도 그때까지가 저장되고, 다시 열면
이어서 합니다.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from arw_selector.core import face_mesh  # noqa: E402
from arw_selector.core import focus as fm  # noqa: E402
from arw_selector.core.raw_io import load_preview  # noqa: E402
from arw_selector.gui import theme  # noqa: E402
from arw_selector.gui.loupe import bgr_to_pixmap  # noqa: E402

LABEL_DIR = Path(__file__).resolve().parent.parent / "labels"
"""답을 저장할 곳.

**사진 폴더에 쓰지 않습니다.** 원본 폴더는 읽기 전용으로 다루는 것이
원칙이고, 카드를 그대로 꽂아 놓고 라벨링하는 경우도 있습니다.
"""

MAX_SHOTS = 120
"""한 번에 물어볼 최대 장수. 더 많으면 끝까지 하기 어렵습니다."""

MIN_EYE_PX = 30
"""눈이 이보다 작으면 사람도 판정하기 어려우니 아예 묻지 않습니다."""


# ---------------------------------------------------------------- 표본 수집


def _main_face(preview: np.ndarray):
    """(주 얼굴 상자, 전체 얼굴 상자들, focus 결과) 또는 None."""
    height, width = preview.shape[:2]
    scale = min(1.0, fm.DETECT_LONG_EDGE / max(height, width))
    small = cv2.resize(preview, (round(width * scale), round(height * scale)),
                       interpolation=cv2.INTER_AREA)
    faces = fm.detect_faces(small)
    if faces is None:
        return None
    result = fm.analyze_focus(preview)
    if not result.faces or not 0 <= result.main_face < len(result.faces):
        return None
    return result.faces[result.main_face], result.faces, result


def collect_eye_samples(folder: Path) -> list[dict]:
    """눈이 충분히 큰 컷만 골라 눈 부분 crop 정보를 모읍니다."""
    from arw_selector.core.raw_io import iter_raw_files

    files = iter_raw_files(folder, recursive=True)
    if not files:
        return []
    step = max(1, len(files) // (MAX_SHOTS * 3))
    samples = []
    for path in files[::step]:
        if len(samples) >= MAX_SHOTS:
            break
        try:
            preview = load_preview(path)
        except Exception:  # noqa: BLE001
            continue
        picked = _main_face(preview)
        if picked is None:
            continue
        box = picked[0]
        points = face_mesh.landmarks(preview, box)
        if points is None:
            continue
        eye_width = max(
            float(np.ptp([points[i][0] for i in face_mesh.LEFT_EYE])),
            float(np.ptp([points[i][0] for i in face_mesh.RIGHT_EYE])),
        )
        if eye_width < MIN_EYE_PX:
            continue
        ring = list(face_mesh.LEFT_EYE) + list(face_mesh.RIGHT_EYE)
        xs = [points[i][0] for i in ring]
        ys = [points[i][1] for i in ring]
        samples.append({
            "path": str(path),
            "eye_px": round(eye_width, 1),
            "cx": float(np.mean(xs)),
            "cy": float(np.mean(ys)),
            "half": max(40.0, float(max(xs) - min(xs)) * 0.85),
        })
    return samples


def collect_focus_samples(folder: Path) -> list[dict]:
    """얼굴이 2개 이상이라 '누가 주인공인가'가 실제로 갈리는 컷만 모읍니다."""
    import exifread

    from arw_selector.core.raw_io import iter_raw_files

    files = iter_raw_files(folder, recursive=True)
    step = max(1, len(files) // (MAX_SHOTS * 3))
    samples = []
    for path in files[::step]:
        if len(samples) >= MAX_SHOTS:
            break
        try:
            preview = load_preview(path)
        except Exception:  # noqa: BLE001
            continue
        picked = _main_face(preview)
        if picked is None:
            continue
        _box, faces, result = picked
        if len(faces) < 2:
            continue  # 얼굴이 하나면 물어볼 것이 없습니다

        af = None
        try:
            with path.open("rb") as fh:
                tags = exifread.process_file(fh, details=True)
            tag = tags.get("MakerNote Tag 0x2027")
            values = list(getattr(tag, "values", []) or []) if tag else []
            if len(values) >= 4:
                w, h, x, y = (int(v) for v in values[:4])
                if w > 0 and h > 0:
                    af = [x / w, y / h]
        except Exception:  # noqa: BLE001
            af = None

        samples.append({
            "path": str(path),
            "faces": [list(map(int, b)) for b in faces],
            "ours": int(result.main_face),
            "af": af,
        })
    return samples


# ---------------------------------------------------------------- 창


class LabelWindow(QWidget):
    """한 장씩 보여 주고 답을 받습니다. 답은 즉시 파일에 씁니다."""

    def __init__(self, mode: str, samples: list[dict], answers_path: Path):
        super().__init__()
        self.mode = mode
        self.samples = samples
        self.answers_path = answers_path
        self.answers: dict[str, object] = {}
        if answers_path.is_file():
            try:
                self.answers = json.loads(answers_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.answers = {}

        self.setWindowTitle(
            "눈 감김 라벨링" if mode == "eyes" else "주 피사체 라벨링")
        self.setStyleSheet(theme.dialog_style())
        self.resize(1000, 760)

        layout = QVBoxLayout(self)
        self.question = QLabel()
        self.question.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(self.question)

        self.view = QLabel()
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setMinimumHeight(520)
        layout.addWidget(self.view, 1)

        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #9a9aa2;")
        layout.addWidget(self.hint)

        self.progress = QProgressBar()
        self.progress.setMaximum(len(samples))
        layout.addWidget(self.progress)

        buttons = QHBoxLayout()
        self.buttons: list[QPushButton] = []
        for label, key in self._choices():
            button = QPushButton(label)
            button.setStyleSheet(theme.BUTTON)
            button.clicked.connect(lambda _=False, k=key: self.answer(k))
            buttons.addWidget(button)
            self.buttons.append(button)
        back = QPushButton("◀ 이전")
        back.setStyleSheet(theme.BUTTON)
        back.clicked.connect(self.go_back)
        buttons.addWidget(back)
        layout.addLayout(buttons)

        # 답한 곳 다음부터 시작합니다
        self.index = 0
        while (self.index < len(samples)
               and samples[self.index]["path"] in self.answers):
            self.index += 1
        self.show_current()

    def _choices(self):
        if self.mode == "eyes":
            return [("감음 (1)", "closed"), ("떴음 (2)", "open"),
                    ("모르겠음 (3)", "unclear")]
        return [("우리 선택이 맞음 (1)", "ours"),
                ("AF 쪽이 맞음 (2)", "af"),
                ("둘 다 아님 (3)", "neither"),
                ("모르겠음 (4)", "unclear")]

    def keyPressEvent(self, event) -> None:
        keys = {Qt.Key_1: 0, Qt.Key_2: 1, Qt.Key_3: 2, Qt.Key_4: 3}
        slot = keys.get(event.key())
        if slot is not None and slot < len(self.buttons):
            self.buttons[slot].click()
        elif event.key() in (Qt.Key_Backspace, Qt.Key_Left):
            self.go_back()

    # ------------------------------------------------------------ 그리기

    def show_current(self) -> None:
        if self.index >= len(self.samples):
            self.finish()
            return
        sample = self.samples[self.index]
        self.progress.setValue(self.index)
        path = Path(sample["path"])

        try:
            preview = load_preview(path)
        except Exception as exc:  # noqa: BLE001
            self.hint.setText(f"{path.name}: 열지 못했습니다 ({exc})")
            self.answer("unclear")
            return

        if self.mode == "eyes":
            image = self._draw_eyes(preview, sample)
            self.question.setText(
                f"[{self.index + 1}/{len(self.samples)}] {path.name} — "
                "이 사람 눈이 감겨 있습니까?")
            self.hint.setText(
                "1 = 감음 · 2 = 떴음 · 3 = 모르겠음 (흐리거나 어두워 판단 불가)\n"
                "한쪽만 감았으면 '감음'입니다. 애매하면 망설이지 말고 3을 "
                "누르세요 — 억지 라벨이 가장 해롭습니다."
            )
        else:
            image = self._draw_focus(preview, sample)
            self.question.setText(
                f"[{self.index + 1}/{len(self.samples)}] {path.name} — "
                "누가 주 피사체입니까?")
            self.hint.setText(
                "빨강 = 우리가 고른 얼굴 · 노랑 = 카메라 AF가 가리키는 사람 · "
                "회색 = 나머지 얼굴\n"
                "1 = 우리 선택이 맞음 · 2 = AF 쪽이 맞음 · "
                "3 = 둘 다 아님 · 4 = 모르겠음"
            )

        long_edge = 900
        h, w = image.shape[:2]
        scale = long_edge / max(h, w)
        shown = cv2.resize(image, (int(w * scale), int(h * scale)))
        self.view.setPixmap(bgr_to_pixmap(shown))

    def _draw_eyes(self, preview: np.ndarray, sample: dict) -> np.ndarray:
        h, w = preview.shape[:2]
        cx, cy, half = sample["cx"], sample["cy"], sample["half"]
        x0, y0 = max(0, int(cx - half)), max(0, int(cy - half * 0.5))
        x1, y1 = min(w, int(cx + half)), min(h, int(cy + half * 0.5))
        crop = preview[y0:y1, x0:x1]
        if crop.size == 0:
            return preview
        return crop

    def _draw_focus(self, preview: np.ndarray, sample: dict) -> np.ndarray:
        h, w = preview.shape[:2]
        marked = preview.copy()
        thin = max(2, w // 700)
        for index, box in enumerate(sample["faces"]):
            x, y, fw, fh = box
            colour = ((60, 60, 245) if index == sample["ours"]
                      else (160, 160, 160))
            cv2.rectangle(marked, (x, y), (x + fw, y + fh), colour,
                          thin * 2 if index == sample["ours"] else thin)
        if sample.get("af"):
            px, py = int(sample["af"][0] * w), int(sample["af"][1] * h)
            cv2.circle(marked, (px, py), w // 45, (60, 210, 245), thin * 2)
            cv2.line(marked, (px - w // 60, py), (px + w // 60, py),
                     (60, 210, 245), thin)
            cv2.line(marked, (px, py - w // 60), (px, py + w // 60),
                     (60, 210, 245), thin)
        return marked

    # ------------------------------------------------------------ 답 저장

    def answer(self, value: str) -> None:
        sample = self.samples[self.index]
        record = {"answer": value}
        if self.mode == "focus":
            record["ours"] = sample["ours"]
            record["has_af"] = sample.get("af") is not None
        else:
            record["eye_px"] = sample["eye_px"]
        self.answers[sample["path"]] = record
        self._save()
        self.index += 1
        self.show_current()

    def go_back(self) -> None:
        if self.index > 0:
            self.index -= 1
            self.answers.pop(self.samples[self.index]["path"], None)
            self._save()
            self.show_current()

    def _save(self) -> None:
        self.answers_path.parent.mkdir(parents=True, exist_ok=True)
        self.answers_path.write_text(
            json.dumps(self.answers, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )

    def finish(self) -> None:
        counts: dict[str, int] = {}
        for record in self.answers.values():
            counts[record["answer"]] = counts.get(record["answer"], 0) + 1
        summary = " · ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        QMessageBox.information(
            self, "완료",
            f"{len(self.answers)}장 라벨링 완료\n{summary}\n\n"
            f"저장 위치:\n{self.answers_path}",
        )
        self.close()


# ---------------------------------------------------------------- 진입점


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] not in ("eyes", "focus"):
        print(__doc__)
        return 2
    mode, folder = argv[1], Path(argv[2])
    if not folder.is_dir():
        print(f"폴더가 없습니다: {folder}")
        return 1

    app = QApplication.instance() or QApplication([])
    theme.apply_app_theme(app)

    print(f"{folder}에서 표본을 고르는 중… (수십 초 걸릴 수 있습니다)")
    samples = (collect_eye_samples(folder) if mode == "eyes"
               else collect_focus_samples(folder))
    if not samples:
        print("물어볼 컷이 없습니다.")
        if mode == "eyes":
            print(f"  눈이 {MIN_EYE_PX}px 이상인 얼굴이 있어야 합니다.")
        else:
            print("  얼굴이 2개 이상인 컷이 있어야 합니다.")
        return 1
    print(f"{len(samples)}장 준비됨")

    # 폴더 이름을 파일명에 넣어, 여러 폴더를 라벨링해도 안 섞이게 합니다
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in folder.name)
    answers_path = LABEL_DIR / f"{mode}_{safe}.json"
    window = LabelWindow(mode, samples, answers_path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    sys.exit(main(sys.argv))
