"""실행 파일 진입점.

Nuitka나 PyInstaller로 묶을 때는 이 파일을 진입점으로 지정합니다.

`arw_selector/gui/app.py`를 직접 진입점으로 쓰면 그 모듈이 `__main__`이
되면서 패키지 맥락을 잃어 상대 임포트가 실패합니다. 절대 임포트만 쓰는
얇은 진입점을 따로 두어 그 문제를 피합니다.
"""

from __future__ import annotations

import multiprocessing
import sys


def selftest() -> int:
    """묶인 실행 파일이 제 발로 설 수 있는지 확인합니다.

    다른 PC에 옮긴 뒤 창만 뜨고 아무것도 안 되는 상황을 미리 잡습니다.
    무거운 의존성(rawpy/OpenCV/lensfun)은 실제로 불러 봐야 빠졌는지
    알 수 있고, 렌즈 DB는 파일이 있어도 읽히는지가 따로입니다.
    """
    failures = 0
    lines: list[str] = []

    # 콘솔 인코딩이 UTF-8이 아니면 한글 줄이 통째로 사라집니다.
    #
    # 묶은 실행 파일은 로케일이 비어 있는 터미널에서 stdout을 ASCII로 잡을 때가
    # 있습니다(소스로 돌릴 때는 파이썬이 UTF-8 모드로 넘어가 티가 안 납니다).
    # 그러면 print가 UnicodeEncodeError를 내는데, 그것이 ValueError의 자식이라
    # 아래 except에 걸려 조용히 버려졌습니다 — [OK]도 [FAIL]도 안 나오고
    # 구분선만 두 줄 찍혀서, 무엇이 왜 실패했는지 볼 방법이 없었습니다.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass

    def say(text: str) -> None:
        """화면과 기록에 함께 남깁니다.

        콘솔이 없는 환경(탐색기에서 더블클릭, 콘솔 붙이기 실패)에서는
        print가 아무 데도 가지 않습니다. 그래도 결과는 남아야 합니다.
        """
        lines.append(text)
        try:
            print(text)
        except (OSError, ValueError):
            # 인코딩 때문이라면 바이트로라도 내보냅니다. 진단이 목적인
            # 출력이라 한 줄이라도 사라지면 안 됩니다.
            try:
                buffer = getattr(sys.stdout, "buffer", None)
                if buffer is not None:
                    buffer.write(text.encode("utf-8", "replace") + b"\n")
                    buffer.flush()
            except (OSError, ValueError):
                pass

    def step(label: str, fn) -> None:
        nonlocal failures
        try:
            detail = fn()
            say(f"[OK  ] {label}  {detail if detail else ''}".rstrip())
        except Exception as exc:  # noqa: BLE001
            failures += 1
            say(f"[FAIL] {label}  {type(exc).__name__}: {exc}")

    def check_version():
        from arw_selector import __version__
        from arw_selector.core.appinfo import APP_NAME, app_root, data_dir

        return f"{APP_NAME} {__version__} | 앱 {app_root()} | data {data_dir()}"

    def check_imaging():
        import cv2
        import rawpy

        return f"OpenCV {cv2.__version__} / rawpy {rawpy.__version__}"

    def check_face_model():
        from arw_selector.core.focus import MODEL_PATH, _get_detector

        if not MODEL_PATH.is_file():
            raise FileNotFoundError(f"얼굴 검출 모델 없음: {MODEL_PATH}")
        # 파일이 있어도 ONNX를 실제로 못 여는 경우가 있어(런타임 DLL 누락)
        # 검출기를 만들어 봐야 확실합니다.
        if _get_detector((320, 320)) is None:
            raise RuntimeError("모델 파일은 있으나 검출기를 만들지 못했습니다")
        return MODEL_PATH.name

    def check_face_mesh():
        """468점 윤곽 모델.

        빠져도 예외 없이 타원 근사로 물러서기 때문에, 여기서 확인하지 않으면
        얼굴·눈 마스크가 뭉툭해지고 눈 감김 감점이 아예 걸리지 않는 상태로
        나갑니다.
        """
        from arw_selector.core import face_mesh

        if not face_mesh.available():
            raise FileNotFoundError(f"얼굴 윤곽 모델 없음: {face_mesh.MODEL_PATH}")
        if face_mesh._net() is None:
            raise RuntimeError("모델 파일은 있으나 읽지 못했습니다")
        return face_mesh.MODEL_PATH.name

    def check_lens_db():
        from arw_selector.core.develop.optics import database_coverage

        cameras, lenses = database_coverage()
        if lenses < 100:
            raise RuntimeError(f"렌즈 프로필이 너무 적습니다 (바디 {cameras}, 렌즈 {lenses})")
        return f"바디 {cameras}, 렌즈 {lenses}"

    def check_presets():
        from arw_selector.core.presets import develop_presets

        names = [info.name for info in develop_presets().list()]
        if len(names) < 5:
            raise RuntimeError(f"보정 프리셋이 부족합니다: {names}")
        return f"{len(names)}개 ({', '.join(names[:4])} …)"

    def check_heif():
        """HEIF 디코더. 없으면 .HIF/.HEIC가 통째로 안 열립니다.

        폴백이 없습니다 — OpenCV·Pillow·LibRaw 전부 이 형식을 거부합니다.

        임포트만 보고 넘어가면 안 됩니다. 실제로 푸는 것은 파이썬 패키지가
        아니라 그 옆의 libheif DLL이고, 배포본에서 그것만 빠질 수 있습니다.
        그래서 16x16짜리를 만들어 인코드했다가 다시 디코드해 봅니다 —
        외부 파일 없이 왕복이 되는지가 유일하게 확실한 증거입니다.

        PIL 쪽 등록(register_heif_opener)은 쓰지 않습니다. 점검 한 번 하려고
        전역 이미지 열기 규칙을 바꿀 이유가 없습니다.
        """
        import io

        import numpy as np
        import pillow_heif

        buffer = io.BytesIO()
        source = np.full((16, 16, 3), 120, np.uint8)
        pillow_heif.from_bytes(
            mode="RGB", size=(16, 16), data=source.tobytes()
        ).save(buffer, quality=50)

        decoded = np.asarray(
            pillow_heif.read_heif(io.BytesIO(buffer.getvalue()))
            .to_pillow().convert("RGB")
        )
        if decoded.shape != (16, 16, 3):
            raise RuntimeError(f"디코드 결과가 이상합니다: {decoded.shape}")
        if abs(float(decoded.mean()) - 120) > 3:
            raise RuntimeError(f"디코드 값이 어긋납니다: {decoded.mean():.1f}")

        return (f"libheif {pillow_heif.libheif_version()}"
                f" / pillow-heif {pillow_heif.__version__} · 왕복 확인")

    def check_translations():
        """번역 파일이 실제로 읽히는지.

        빠져도 예외가 나지 않습니다 — Qt는 못 찾으면 영어 원문을 그대로
        돌려주므로, 화면이 전부 영어인 것 말고는 아무 증상이 없습니다.
        """
        from arw_selector.gui.appinfo_bridge import translations_dir

        folder = translations_dir()
        files = sorted(folder.glob("*.qm")) if folder.is_dir() else []
        if not files:
            raise RuntimeError(f"번역 파일이 없습니다: {folder}")
        return f"{len(files)}개 ({', '.join(p.stem for p in files)})"

    def check_select_presets():
        """판정 프리셋을 따로 셉니다.

        예전에는 보정 프리셋만 세면서 '기본 프리셋 [OK]'로 표시했습니다.
        그래서 판정 프리셋이 통째로 빠진 배포본이 점검을 통과했습니다.
        """
        from arw_selector.core.presets import select_presets

        names = [info.name for info in select_presets().list()]
        if not names:
            raise RuntimeError("판정 프리셋이 없습니다")
        return f"{len(names)}개 ({', '.join(names[:4])})"

    def check_gui():
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from arw_selector.gui import theme
        from arw_selector.gui.main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        theme.apply_app_theme(app)
        window = MainWindow()
        window.show()
        app.processEvents()
        icon = "있음" if not app.windowIcon().isNull() else "없음"
        window.close()
        return f"메인 창 생성됨, 아이콘 {icon}"

    from datetime import datetime

    say(f"RAW_selector 자체 점검  {datetime.now():%Y-%m-%d %H:%M:%S}")
    step("버전·경로", check_version)
    step("이미지 라이브러리", check_imaging)
    step("얼굴 검출 모델", check_face_model)
    step("얼굴 윤곽 모델", check_face_mesh)
    step("렌즈 프로필 DB", check_lens_db)
    step("보정 프리셋", check_presets)
    step("판정 프리셋", check_select_presets)
    step("HEIF 디코더", check_heif)
    step("번역", check_translations)
    step("GUI 생성", check_gui)

    say("")
    say("-" * 58)
    if failures:
        say(f"{failures}건 실패 — 위의 [FAIL] 항목을 확인하십시오.")
        say("폴더에서 파일이 빠졌을 가능성이 큽니다. 압축을 다시 푸십시오.")
    else:
        # 맥에서는 .exe가 없습니다. 배포본을 받은 사람이 그대로 읽는 문장이라
        # 없는 파일 이름을 대면 어디를 눌러야 하는지 알 수 없습니다.
        target = "RAW_selector.app 을" if sys.platform == "darwin" else "RAW_selector.exe 를"
        say(f"이상 없습니다. {target} 실행하시면 됩니다.")
    say("-" * 58)

    # 콘솔 출력이 안 보이는 경우를 대비해 파일로도 남깁니다
    try:
        from arw_selector.core.logging_setup import log_directory

        directory = log_directory()
        directory.mkdir(parents=True, exist_ok=True)
        report = directory / "selftest.txt"
        report.write_text("\n".join(lines) + "\n", encoding="utf-8")
        say(f"기록: {report}")
    except OSError as exc:
        say(f"기록 실패: {exc}")

    return 1 if failures else 0


def main() -> int:
    # 병렬 분석이 자식 프로세스를 띄웁니다. 묶인 실행 파일에서 자식이
    # GUI를 다시 시작하는 것을 막습니다.
    multiprocessing.freeze_support()

    if "--selftest" in sys.argv:
        return selftest()

    from arw_selector.gui.app import main as run

    return run()


if __name__ == "__main__":
    sys.exit(main())
