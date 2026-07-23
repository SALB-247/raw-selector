"""macOS 앱 번들(.app)과 배포 이미지(.dmg)를 만듭니다.

    python build_macos.py --clean
    python build_macos.py --dmg

포함할 데이터 목록은 `build_windows.py`의 것을 그대로 가져다 씁니다. 모델이나
프리셋 폴더를 추가할 때 고칠 자리가 둘로 갈라지면 한쪽은 반드시 빠지는데,
그 실수는 빌드도 실행도 성공한 채로 기능만 사라지는 방식으로 드러납니다
(판정 프리셋이 통째로 빠진 배포본이 실제로 점검을 통과한 적이 있습니다).

Windows 쪽과 다른 점만 여기에 둡니다.

- 콘솔 모드가 없습니다. macOS는 .app을 더블클릭하면 터미널이 붙지 않고,
  터미널에서 안쪽 실행 파일을 직접 부르면 그 터미널에 그대로 출력합니다.
- 아이콘은 .ico가 아니라 .icns입니다 (`python make_icon.py`가 만듭니다).
- 백신이 버전 리소스를 막는 문제가 없어 재시도 경로도 없습니다.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from build_windows import (
    DATA_DIRS,
    DATA_FILES,
    HIDDEN_IMPORTS,
    OPTIONAL_IMPORTS,
    ROOT,
    app_version,
)

APP_NAME = "RAW_selector"
ENTRY = ROOT / "launcher.py"
OUTPUT = ROOT / "build"
ICNS = ROOT / "assets" / "icon.icns"
DMG_BACKGROUND = ROOT / "assets" / "dmg_background.png"

#: 설치 창의 크기(포인트)와 아이콘 배치. 배경 그림과 dmgbuild가 같은 좌표를
#: 써야 화살표가 두 아이콘 사이에 정확히 놓입니다.
DMG_WINDOW = (600, 400)
DMG_ICON_SIZE = 100
DMG_APP_XY = (150, 180)
DMG_APPLICATIONS_XY = (450, 180)

#: lensfunpy 휠이 macosx_14_0_arm64라서 그 아래로는 설치 자체가 안 됩니다.
#: 번들에 적어 두면 낮은 OS에서 조용히 깨지는 대신 명확히 거절당합니다.
MIN_OS = "14.0"

#: 역방향 도메인 형식이어야 합니다. macOS는 이 값으로 앱을 구분해서
#: 환경설정·권한(사진 폴더 접근 등)을 따로 기억하고, 공증에도 필요합니다.
#: Nuitka 기본값은 그냥 "RAW_selector"라 다른 앱과 부딪힐 수 있습니다.
BUNDLE_ID = "com.salb247.rawselector"


def stamp_bundle_info(app: Path) -> None:
    """Info.plist에 최소 OS와 번들 식별자를 적습니다.

    Nuitka에는 최소 OS를 지정하는 옵션이 없습니다. 실행 파일의
    LC_BUILD_VERSION은 빌드에 쓴 파이썬을 따라가는데, 이 venv의 파이썬은
    11.0으로 만들어져 있어 번들이 "macOS 11부터"라고 주장하게 됩니다.
    그런데 같이 담기는 liblensfun은 14.0이라, 11~13에서는 앱이 일단 뜬 뒤
    렌즈 보정을 부르는 순간 죽습니다. LSMinimumSystemVersion을 적어 두면
    macOS가 실행 전에 "이 앱에는 더 최신 버전이 필요합니다"로 막습니다.
    """
    plist = app / "Contents" / "Info.plist"
    for key, kind, value in (
        ("LSMinimumSystemVersion", "string", MIN_OS),
        ("CFBundleIdentifier", "string", BUNDLE_ID),
    ):
        # -replace는 없는 키에 실패하므로 지우고 넣습니다
        subprocess.run(["plutil", "-remove", key, str(plist)],
                       capture_output=True, text=True)
        result = subprocess.run(
            ["plutil", "-insert", key, f"-{kind}", value, str(plist)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"  Info.plist에 {key}를 넣지 못했습니다: {result.stderr.strip()}")


def check_prerequisites() -> list[str]:
    problems = []
    if sys.platform != "darwin":
        problems.append("맥에서만 돌릴 수 있습니다.")
    try:
        import nuitka  # noqa: F401
    except ImportError:
        problems.append("Nuitka가 없습니다. pip install nuitka")
    try:
        import dmgbuild  # noqa: F401
    except ImportError:
        problems.append("dmgbuild가 없습니다. pip install dmgbuild (DMG 배경·바로가기용)")
    if shutil.which("clang") is None:
        problems.append("clang이 없습니다. xcode-select --install")
    if not ICNS.is_file():
        problems.append(f"아이콘이 없습니다: {ICNS}  (python make_icon.py)")
    for source, _ in DATA_FILES:
        if not source.is_file():
            problems.append(f"데이터 파일 없음: {source}")
    return problems


def build_command(output: Path) -> list[str]:
    command = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--macos-create-app-bundle",
        "--assume-yes-for-downloads",
        "--enable-plugin=pyside6",
        f"--output-dir={output}",
        f"--output-filename={APP_NAME}",
        f"--macos-app-name={APP_NAME}",
        f"--macos-app-version={app_version()}",
        f"--macos-app-icon={ICNS}",
        # 창을 띄우고 Dock에 뜨는 보통의 앱입니다(Nuitka 기본값이기도 합니다).
        # 분석 워커가 Dock 아이콘을 하나씩 더 만들지 않는 것은 이 옵션과
        # 무관합니다 — 자식은 QApplication을 만들지 않고, launcher.py의
        # freeze_support()가 GUI 재시작을 막습니다.
        "--macos-app-mode=gui",
        "--noinclude-pytest-mode=nofollow",
        "--noinclude-setuptools-mode=nofollow",
        # GUI가 지연 임포트를 많이 써서 자동 추적만으로는 일부가 빠집니다
        "--include-package=arw_selector",
    ]

    for module in HIDDEN_IMPORTS:
        command.append(f"--include-module={module}")

    for module in OPTIONAL_IMPORTS:
        try:
            __import__(module)
        except ImportError:
            print(f"  {module} 없음 — 해당 기능 없이 빌드합니다")
            continue
        command.append(f"--include-module={module}")
        if module == "lensfunpy":
            import lensfunpy

            db_dir = Path(lensfunpy.__file__).parent / "db_files"
            if db_dir.is_dir():
                command.append(f"--include-data-dir={db_dir}=lensfunpy/db_files")

    for source, target in DATA_FILES:
        command.append(f"--include-data-files={source}={target}")
    for source, target in DATA_DIRS:
        if source.is_dir():
            command.append(f"--include-data-dir={source}={target}")

    command.append(str(ENTRY))
    return command


def bundle_path() -> Path:
    """Nuitka가 만든 .app. 진입점 이름을 따라가므로 launcher.app일 수 있습니다."""
    for name in (f"{APP_NAME}.app", f"{ENTRY.stem}.app"):
        candidate = OUTPUT / name
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(f"{OUTPUT} 안에서 .app을 찾지 못했습니다")


def selftest(app: Path) -> bool:
    """번들 안의 실행 파일을 직접 불러 열 항목을 확인합니다.

    .app을 `open`으로 띄우면 종료 코드가 돌아오지 않습니다. 안쪽 바이너리를
    그대로 부르면 그 터미널에 출력하고 코드도 돌려줍니다.
    """
    binary = app / "Contents" / "MacOS" / APP_NAME
    if not binary.is_file():
        found = list((app / "Contents" / "MacOS").glob("*"))
        print(f"  실행 파일을 찾지 못했습니다. 있는 것: {found}")
        return False
    result = subprocess.run([str(binary), "--selftest"], text=True)
    return result.returncode == 0


def build_dmg_background() -> Path:
    """설치 창 배경(제목·화살표·안내)을 그려 저장합니다.

    Finder가 그리는 아이콘 라벨이 검은색이라, 배경은 밝게 둬야 "RAW_selector"·
    "Applications" 글자가 읽힙니다. 화살표는 두 아이콘 사이 여백(gap)에
    정확히 놓이도록 DMG_APP_XY·DMG_APPLICATIONS_XY에서 좌표를 계산합니다.
    """
    from PIL import Image, ImageDraw, ImageFont

    scale = 2  # 곡선·글자를 크게 그린 뒤 줄여 계단을 없앱니다
    width, height = DMG_WINDOW
    ko_font = "/System/Library/Fonts/AppleSDGothicNeo.ttc"

    def font(size: int):
        try:
            return ImageFont.truetype(ko_font, size * scale)
        except OSError:
            return ImageFont.load_default()

    green = (76, 175, 80)
    ink = (43, 43, 48)
    dim = (122, 122, 130)

    image = Image.new("RGB", (width * scale, height * scale), (255, 255, 255))
    draw = ImageDraw.Draw(image)

    # 위에서 아래로 옅은 회색 그라디언트
    top, bottom = (247, 248, 250), (233, 235, 239)
    for y in range(height * scale):
        t = y / (height * scale)
        draw.line(
            [(0, y), (width * scale, y)],
            fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)),
        )

    cx = width * scale / 2
    draw.text((cx, 60 * scale), APP_NAME, font=font(30), fill=ink,
              anchor="mm", stroke_width=1, stroke_fill=ink)
    draw.text((cx, 92 * scale), app_version(), font=font(15), fill=dim, anchor="mm")

    # 화살표 — 아이콘 중심선 높이에, 두 아이콘 사이 여백에 놓습니다
    line_y = DMG_APP_XY[1] * scale
    start = (DMG_APP_XY[0] + DMG_ICON_SIZE // 2 + 12) * scale
    end = (DMG_APPLICATIONS_XY[0] - DMG_ICON_SIZE // 2 - 14) * scale
    shaft = 12 * scale
    head = 30 * scale
    draw.rounded_rectangle(
        [start, line_y - shaft // 2, end - head + 4 * scale, line_y + shaft // 2],
        radius=shaft // 2, fill=green,
    )
    draw.polygon(
        [(end, line_y), (end - head, line_y - head * 0.7),
         (end - head, line_y + head * 0.7)], fill=green,
    )

    draw.text((cx, 300 * scale),
              "왼쪽 아이콘을 Applications 폴더로 드래그하세요",
              font=font(16), fill=dim, anchor="mm")

    DMG_BACKGROUND.parent.mkdir(parents=True, exist_ok=True)
    image.resize((width, height), Image.LANCZOS).save(DMG_BACKGROUND)
    return DMG_BACKGROUND


def make_dmg(app: Path) -> Path:
    """드래그해서 설치하는 배포용 DMG를 만듭니다.

    Applications 폴더 바로가기와 화살표 배경을 넣어, 받는 사람이 창을 열면
    바로 끌어다 놓을 수 있게 합니다. 창 배치는 dmgbuild가 `.DS_Store`를 직접
    써서 지정합니다 — Finder를 스크립트로 조종하지 않으므로(그쪽은 자동화
    권한을 물어 헤드리스에서 멈춥니다) 어디서 돌려도 같은 결과가 나옵니다.
    """
    import dmgbuild

    target = OUTPUT / f"{APP_NAME}_{app_version()}_macos_arm64.dmg"
    target.unlink(missing_ok=True)
    background = build_dmg_background()

    settings = {
        "format": "UDZO",
        "files": [str(app)],
        "symlinks": {"Applications": "/Applications"},
        "icon": str(ICNS),  # 마운트한 디스크 아이콘
        "background": str(background),
        "window_rect": ((200, 150), DMG_WINDOW),
        "default_view": "icon-view",
        "icon_size": DMG_ICON_SIZE,
        "text_size": 13,
        "show_status_bar": False,
        "show_toolbar": False,
        "show_pathbar": False,
        "show_sidebar": False,
        "icon_locations": {
            app.name: DMG_APP_XY,
            "Applications": DMG_APPLICATIONS_XY,
        },
    }
    dmgbuild.build_dmg(str(target), APP_NAME, settings=settings)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="macOS 빌드")
    parser.add_argument("--clean", action="store_true", help="이전 빌드를 지우고 시작")
    parser.add_argument("--dmg", action="store_true", help="빌드 후 .dmg까지 만든다")
    parser.add_argument("--dmg-only", action="store_true",
                        help="이미 빌드된 .app을 dmg로만 묶는다")
    args = parser.parse_args()

    problems = check_prerequisites()
    if problems:
        for problem in problems:
            print(f"  - {problem}")
        return 1

    if args.dmg_only:
        image = make_dmg(bundle_path())
        print(f"만들었습니다: {image}  ({image.stat().st_size / 1024 / 1024:.1f}MB)")
        return 0

    if args.clean and OUTPUT.is_dir():
        shutil.rmtree(OUTPUT)

    command = build_command(OUTPUT)
    print(f"{APP_NAME} {app_version()} — 빌드를 시작합니다 (수 분 걸립니다)")
    if subprocess.run(command).returncode != 0:
        print("빌드에 실패했습니다.")
        return 1

    app = bundle_path()
    if app.name != f"{APP_NAME}.app":
        renamed = OUTPUT / f"{APP_NAME}.app"
        if renamed.is_dir():
            shutil.rmtree(renamed)
        app = app.rename(renamed)

    stamp_bundle_info(app)

    print(f"\n번들: {app}")
    if not selftest(app):
        print("자체 점검에서 실패한 항목이 있습니다.")
        return 1

    if args.dmg:
        image = make_dmg(app)
        print(f"배포 이미지: {image}  ({image.stat().st_size / 1024 / 1024:.1f}MB)")

    print(
        "\n서명하지 않은 번들이라 Gatekeeper가 막습니다. 본인 기계에서 쓰려면:\n"
        f"    xattr -dr com.apple.quarantine {app}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
