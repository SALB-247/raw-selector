"""Nuitka standalone 빌드 스크립트 (Windows).

사용법:
    python build_windows.py            # standalone 폴더 빌드
    python build_windows.py --onefile  # 단일 exe (실행 시 임시 폴더에 압축 해제)

standalone을 기본으로 하는 이유:
    onefile은 실행할 때마다 임시 폴더에 전체를 풀기 때문에 시작이 느리고,
    ProcessPoolExecutor가 자식 프로세스를 띄울 때 문제가 생길 수 있습니다.
    4000장 배치를 병렬로 돌리는 프로그램이라 standalone이 안전합니다.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent

# gui/app.py를 직접 진입점으로 쓰면 그 모듈이 __main__이 되어 패키지 맥락을
# 잃고 상대 임포트가 실패합니다. 절대 임포트만 쓰는 launcher.py를 씁니다.
ENTRY = ROOT / "launcher.py"
OUTPUT = ROOT / "build"
APP_NAME = "RAW_selector"
EXE_NAME = "RAW_selector.exe"
ICON = ROOT / "assets" / "icon.ico"

# 데이터 파일: ONNX 모델은 반드시 포함해야 합니다.
#
# --include-package=arw_selector 는 파이썬 모듈만 가져갑니다. .onnx는 데이터라
# 여기에 적지 않으면 조용히 빠지고, 두 모델 모두 없을 때 예외를 던지지 않고
# 물러서도록 만들어져 있어서 **빌드도 실행도 성공한 채로 기능만 사라집니다.**
DATA_FILES = [
    (
        ROOT / "arw_selector" / "core" / "models" / "face_detection_yunet_2023mar.onnx",
        "arw_selector/core/models/face_detection_yunet_2023mar.onnx",
    ),
    # 468점 얼굴 윤곽. 빠지면 얼굴·눈 마스크가 타원 근사로 떨어지고
    # 눈 감김 감점이 아예 측정되지 않습니다(EAR을 못 재면 감점 없음).
    (
        ROOT / "arw_selector" / "core" / "models" / "face_mesh_192x192.onnx",
        "arw_selector/core/models/face_mesh_192x192.onnx",
    ),
]

# exe 옆에 함께 놓을 data 폴더. appinfo.app_root()가 exe 위치를 앱 루트로
# 보므로, 여기에 넣으면 다른 PC에서도 렌즈 DB와 기본 프리셋이 그대로 붙습니다.
# logs는 실행 중에 생기는 것이라 넣지 않습니다.
DATA_DIRS = [
    (ROOT / "data" / "lensfun", "data/lensfun"),
    (ROOT / "data" / "develop_presets", "data/develop_presets"),
    # 판정 기준 프리셋. 예전에는 목록에서 빠져 있어서 배포본에 판정
    # 프리셋이 하나도 안 들어갔습니다 — 빌드도 실행도 성공하고 자체 점검도
    # 통과하는데(그때는 보정 프리셋만 셌습니다) 프리셋 목록만 비어 있었습니다.
    (ROOT / "data" / "select_presets", "data/select_presets"),
    # 번역(.qm). 빠지면 화면이 통째로 영어로 뜹니다 — 예외는 나지 않고,
    # Qt가 조용히 원문을 돌려주기 때문에 버그로 보이지도 않습니다.
    (ROOT / "data" / "translations", "data/translations"),
    # 자주 쓰이는 신형 바디의 색 보정을 미리 담아 둡니다. 없으면 받는 사람이
    # 처음 그 기종 폴더를 열 때마다 직접 계산해야 합니다.
    (ROOT / "data" / "calibration", "data/calibration"),
    # exe 리소스와 별개로 실행 중에 QIcon으로도 읽습니다 (창·작업표시줄)
    (ROOT / "assets", "assets"),
]

# Nuitka가 자동으로 찾지 못하는 모듈들
HIDDEN_IMPORTS = [
    # HEIF 디코더. 없으면 .HIF/.HEIC가 통째로 안 열립니다 — 다른 디코더가
    # 아예 없어서 폴백도 없습니다.
    #
    # 실제로 디코드하는 것은 파이썬 패키지가 아니라 site-packages 최상위에
    # 따로 놓인 _pillow_heif.pyd(52KB)와 그것이 부르는 libheif DLL(2.1MB)
    # 입니다. 패키지만 적으면 이 둘이 안 따라올 수 있어 함께 적습니다.
    "pillow_heif",
    "_pillow_heif",
    "rawpy",
    "cv2",
    "piexif",
    "exifread",
    "yaml",
    "PIL",
]

OPTIONAL_IMPORTS = ["lensfunpy"]


def check_prerequisites() -> list[str]:
    """빌드 전에 필요한 것들을 확인합니다."""
    problems = []

    try:
        import nuitka  # noqa: F401
    except ImportError:
        problems.append(
            "Nuitka가 설치되어 있지 않습니다. pip install nuitka"
        )

    for source, _ in DATA_FILES:
        if not source.exists():
            problems.append(f"데이터 파일이 없습니다: {source}")

    # 렌즈 DB가 빠지면 다른 PC에서 광학 보정이 통째로 죽습니다. 빌드가 끝난
    # 뒤에야 알게 되면 늦으므로 여기서 막습니다.
    for source, _ in DATA_DIRS:
        if not source.is_dir():
            problems.append(f"데이터 폴더가 없습니다: {source}")
        elif not any(source.iterdir()):
            problems.append(f"데이터 폴더가 비어 있습니다: {source}")

    if not ICON.exists():
        problems.append(f"아이콘이 없습니다: {ICON} (python make_icon.py 로 만듭니다)")

    if not ENTRY.exists():
        problems.append(f"진입점이 없습니다: {ENTRY}")

    return problems


def build_command(onefile: bool, output: Path, version_info: bool = True) -> list[str]:
    command = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",
        "--enable-plugin=pyside6",
        # attach: 콘솔에서 실행하면 그 콘솔에 붙어 출력하고, 탐색기에서
        # 더블클릭하면 콘솔 창을 만들지 않습니다.
        # disable로 두면 콘솔이 아예 없어서 `--selftest`를 콘솔에서 돌려도
        # 아무 출력 없이 끝납니다 — 실제로 그렇게 나갔던 적이 있습니다.
        "--windows-console-mode=attach",
        f"--output-dir={output}",
        f"--output-filename={EXE_NAME}",
        # numpy/cv2는 이미 컴파일된 확장이라 다시 컴파일할 필요가 없습니다
        "--noinclude-pytest-mode=nofollow",
        "--noinclude-setuptools-mode=nofollow",
    ]

    # 버전 정보는 링크가 끝난 뒤 exe를 다시 열어 써 넣습니다. 백신이 갓 만들어진
    # 26MB 파일을 검사하느라 그 순간 파일을 잡고 있으면 이 단계만 실패하고
    # 빌드 전체가 무산됩니다. 실패 시 이 정보 없이 재시도할 수 있어야 합니다.
    if version_info:
        command += [
            f"--product-name={APP_NAME}",
            f"--company-name={APP_NAME}",
            "--file-description=RAW 초점 셀렉트 및 보정 도구",
            f"--product-version={app_version()}",
        ]

    if onefile:
        command.append("--onefile")

    # 패키지 전체를 포함합니다. GUI가 지연 임포트를 많이 써서 자동 추적만으로는
    # 일부 모듈이 빠집니다.
    command.append("--include-package=arw_selector")

    for module in HIDDEN_IMPORTS:
        command.append(f"--include-module={module}")

    for module in OPTIONAL_IMPORTS:
        try:
            __import__(module)
            command.append(f"--include-module={module}")
            # lensfun은 데이터베이스 XML을 함께 넣어야 동작합니다
            if module == "lensfunpy":
                import lensfunpy

                db_dir = Path(lensfunpy.__file__).parent / "db_files"
                if db_dir.exists():
                    command.append(f"--include-data-dir={db_dir}=lensfunpy/db_files")
        except ImportError:
            print(f"  {module} 없음 — 해당 기능 없이 빌드합니다")

    for source, target in DATA_FILES:
        command.append(f"--include-data-files={source}={target}")

    for source, target in DATA_DIRS:
        if source.is_dir():
            command.append(f"--include-data-dir={source}={target}")

    if ICON.exists():
        command.append(f"--windows-icon-from-ico={ICON}")

    command.append(str(ENTRY))
    return command


def app_version() -> str:
    """패키지에서 버전을 읽습니다. 파일 이름에 손으로 적으면 어긋납니다."""
    sys.path.insert(0, str(ROOT))
    try:
        from arw_selector import __version__

        return __version__
    except ImportError:
        return "0.0.0"


#: zip에 넣지 않을 것들 (배포 폴더 기준 상대 경로의 첫 조각).
#:
#: 빌드가 끝나면 `--selftest`로 확인하라고 BUILD.md에 적어 두었는데, 그
#: 실행이 배포 폴더 안에 data/logs를 만듭니다. 그대로 묶으면 만든 사람의
#: 로그와 크래시 파일이 받는 사람에게 갑니다. 실제로 그렇게 될 뻔했습니다
#: (286개 -> 290개, 늘어난 넷 중 셋이 제 로그였습니다).
ZIP_EXCLUDE_DIRS = ("data/logs",)


def _shippable(path: Path, dist: Path) -> bool:
    relative = path.relative_to(dist).as_posix()
    return not any(
        relative == prefix or relative.startswith(prefix + "/")
        for prefix in ZIP_EXCLUDE_DIRS
    )


def make_zip(dist: Path) -> Path | None:
    """배포 폴더를 zip으로 묶습니다.

    폴더째 전달하면 받는 쪽에서 파일 하나만 빠뜨려도 원인 찾기가 어렵습니다.
    압축 파일 하나로 주는 편이 안전합니다.

    최상위에 폴더 하나를 두고 그 안에 넣습니다 — 압축을 푼 자리에 281개
    파일이 쏟아지지 않도록.

    **실행 중에 생긴 것은 빼고 묶습니다** (_shippable 참고).
    """
    import zipfile

    if not dist.is_dir():
        print(f"배포 폴더가 없습니다: {dist}")
        return None


    name = f"{APP_NAME}_{app_version()}_win64"
    archive = OUTPUT / f"{name}.zip"
    files = sorted(p for p in dist.rglob("*") if p.is_file() and _shippable(p, dist))
    total = sum(p.stat().st_size for p in files)

    print()
    print(f"압축 중: {len(files)}개 파일 ({total / 1024 / 1024:.0f}MB)")
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(
        archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6
    ) as bundle:
        for index, path in enumerate(files, start=1):
            bundle.write(path, Path(name) / path.relative_to(dist))
            if index % 50 == 0 or index == len(files):
                print(f"  {index}/{len(files)}", end="\r")

    size = archive.stat().st_size
    print(f"  {len(files)}/{len(files)}      ")
    print(f"완성: {archive}")
    print(f"      {size / 1024 / 1024:.0f}MB (원본 대비 {100 * size / total:.0f}%)")
    return archive


def check_zip(archive: Path, dist: Path) -> bool:
    """만든 zip이 실제로 멀쩡한지 확인합니다.

    깨진 압축 파일을 보내면 상대방이 받아서야 압니다. 여기서 봅니다.
    """
    import zipfile

    with zipfile.ZipFile(archive) as bundle:
        broken = bundle.testzip()
        names = bundle.namelist()

    # 세는 기준을 make_zip과 맞춰야 합니다. 안 그러면 로그가 하나 생길
    # 때마다 "파일 수 불일치"로 멀쩡한 zip이 FAIL로 뜹니다.
    expected = sum(1 for p in dist.rglob("*") if p.is_file() and _shippable(p, dist))
    ok = broken is None and len(names) == expected

    print()
    print("압축 파일 확인:")
    print(f"  [{'OK  ' if broken is None else 'FAIL'}] 무결성"
          f"{'' if broken is None else f' — 깨진 항목: {broken}'}")
    print(f"  [{'OK  ' if len(names) == expected else 'FAIL'}] "
          f"파일 수 {len(names)}개 (배포 폴더 {expected}개)")

    needed = (f"/{EXE_NAME}", "/data/lensfun/", "/data/develop_presets/",
              "/data/select_presets/", "/assets/icon.ico")
    for fragment in needed:
        present = any(fragment.strip("/") in n for n in names)
        print(f"  [{'OK  ' if present else 'FAIL'}] {fragment}")
        ok = ok and present
    return ok


#: 실행 파일과 함께 넣는 것들. 받는 PC에는 Python이 없으므로 점검도
#: 더블클릭으로 되어야 합니다.
#:
#: CHANGELOG는 "올려 쓰기 전에 프리셋을 다시 맞추라" 같은 **행동이 필요한**
#: 안내가 들어 있어 함께 나가야 합니다. 배포_읽어보세요.txt가 이 파일을
#: 가리키고 있어서, 빠지면 안내가 끊깁니다.
EXTRA_FILES = ("배포_읽어보세요.txt", "자체점검.bat", "CHANGELOG.md")


def copy_readme(dist: Path) -> None:
    """배포 폴더에 사용 안내와 점검 도구를 함께 넣습니다.

    받은 사람이 처음 열었을 때 무엇부터 해야 하는지 알 수 있어야 합니다.
    """
    for name in EXTRA_FILES:
        source = ROOT / name
        if source.is_file():
            try:
                shutil.copy2(source, dist / name)
            except OSError as exc:
                print(f"  {name} 을(를) 넣지 못했습니다: {exc}")
        else:
            print(f"  {name} 이(가) 없습니다")


def main() -> int:
    parser = argparse.ArgumentParser(description="Windows standalone 빌드")
    parser.add_argument("--onefile", action="store_true", help="단일 exe로 묶기")
    parser.add_argument("--clean", action="store_true", help="이전 빌드 삭제")
    parser.add_argument(
        "--no-version-info", action="store_true",
        help="exe에 제품명·설명을 넣지 않습니다 (백신이 이 단계를 막을 때)",
    )
    parser.add_argument(
        "--no-zip", action="store_true", help="zip 묶기를 건너뜁니다",
    )
    parser.add_argument(
        "--zip-only", action="store_true",
        help="이미 빌드된 폴더를 zip으로만 묶습니다 (다시 빌드하지 않음)",
    )
    args = parser.parse_args()

    if args.zip_only:
        dist = OUTPUT / "launcher.dist"
        copy_readme(dist)
        archive = make_zip(dist)
        if archive is None:
            return 1
        return 0 if check_zip(archive, dist) else 1

    problems = check_prerequisites()
    if problems:
        print("빌드를 시작할 수 없습니다:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    if args.clean and OUTPUT.exists():
        print(f"이전 빌드 삭제: {OUTPUT}")
        shutil.rmtree(OUTPUT, ignore_errors=True)

    OUTPUT.mkdir(parents=True, exist_ok=True)
    command = build_command(args.onefile, OUTPUT, version_info=not args.no_version_info)

    print("빌드 명령:")
    print("  " + " \\\n    ".join(command))
    print()

    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    sys.stdout.write(result.stdout or "")
    sys.stderr.write(result.stderr or "")

    if result.returncode != 0:
        output_text = (result.stdout or "") + (result.stderr or "")
        if "Failed to add resources" in output_text and not args.no_version_info:
            # 컴파일과 링크는 끝났고 버전 정보를 써 넣는 단계에서만 막힌
            # 상황입니다. 그 정보는 표시용이므로 빼고 다시 만듭니다.
            print()
            print("버전 정보를 써 넣지 못했습니다. 백신이 exe를 검사하는 중일 수")
            print("있습니다. 버전 정보 없이 다시 빌드합니다.")
            print()
            retry = build_command(args.onefile, OUTPUT, version_info=False)
            result = subprocess.run(retry, cwd=ROOT)
            if result.returncode == 0:
                print()
                print("버전 정보(제품명·설명) 없이 빌드했습니다. 실행에는 지장이")
                print("없습니다. 이 정보가 필요하면 백신 실시간 검사에서 빌드")
                print("폴더를 제외한 뒤 다시 빌드하십시오.")

        if result.returncode != 0:
            print("빌드에 실패했습니다.")
            return result.returncode

    target = OUTPUT / ("launcher.dist" if not args.onefile else "")
    print()
    print("빌드 완료")
    print(f"  결과물: {target}")
    if not args.onefile:
        print(f"  실행 파일: {target / EXE_NAME}")
        print(f"  배포 시 {target.name} 폴더 전체를 함께 전달하십시오.")
        copy_readme(target)
        verify_dist(target)
        if not args.no_zip:
            archive = make_zip(target)
            if archive is not None:
                check_zip(archive, target)
    return 0


def verify_dist(dist: Path) -> bool:
    """배포 폴더에 있어야 할 것이 다 들어갔는지 확인합니다.

    다른 PC에 옮긴 뒤에 "렌즈 프로필이 하나도 없다"를 발견하면 늦습니다.
    빌드 직후에 여기서 세어 봅니다.
    """
    checks: list[tuple[str, bool, str]] = []

    exe = dist / EXE_NAME
    checks.append(("실행 파일", exe.is_file(), str(exe)))

    lens_dir = dist / "data" / "lensfun"
    xmls = list(lens_dir.glob("*.xml")) if lens_dir.is_dir() else []
    checks.append((f"렌즈 프로필 XML {len(xmls)}개", len(xmls) >= 50, str(lens_dir)))

    presets = dist / "data" / "develop_presets"
    yamls = list(presets.glob("*.yaml")) if presets.is_dir() else []
    checks.append((f"보정 프리셋 {len(yamls)}개", len(yamls) >= 5, str(presets)))

    # 판정 프리셋은 따로 셉니다. 예전에는 보정 프리셋만 세고 "기본 프리셋
    # 8개 [OK]"로 표시해서, 판정 프리셋이 통째로 빠진 배포본이 점검을
    # 통과했습니다.
    select = dist / "data" / "select_presets"
    select_yamls = list(select.glob("*.yaml")) if select.is_dir() else []
    checks.append(
        (f"판정 프리셋 {len(select_yamls)}개", len(select_yamls) >= 1, str(select))
    )

    # 번역이 빠지면 예외 없이 화면만 영어가 됩니다. 눈으로는 버그로 보이지
    # 않으므로 여기서 셉니다.
    translations = dist / "data" / "translations"
    qm = list(translations.glob("*.qm")) if translations.is_dir() else []
    checks.append((f"번역 {len(qm)}개", len(qm) >= 1, str(translations)))

    models = dist / "arw_selector" / "core" / "models"
    detector = models / "face_detection_yunet_2023mar.onnx"
    checks.append(("얼굴 검출 모델", detector.is_file(), str(detector)))

    mesh = models / "face_mesh_192x192.onnx"
    checks.append(("얼굴 윤곽 모델 (468점)", mesh.is_file(), str(mesh)))

    # HEIF 디코더. 빠지면 .HIF/.HEIC만 안 열립니다 — 나머지는 멀쩡히 돌아서
    # 배포본을 받은 쪽이 "이 파일만 이상하다"로 겪습니다. 실제로 푸는 것은
    # libheif DLL이라 그것까지 확인합니다.
    heif_pyd = list(dist.glob("_pillow_heif*.pyd"))
    checks.append(("HEIF 디코더 모듈", bool(heif_pyd), str(dist / "_pillow_heif*.pyd")))
    libheif = list(dist.glob("libheif*.dll"))
    checks.append(("libheif DLL", bool(libheif), str(dist / "libheif*.dll")))

    print()
    print("배포 내용 확인:")
    ok = True
    for label, passed, where in checks:
        print(f"  [{'OK' if passed else '없음'}] {label}")
        if not passed:
            ok = False
            print(f"         찾은 위치: {where}")
    if not ok:
        print()
        print("  빠진 항목이 있습니다. 이대로 배포하면 해당 기능이 동작하지 않습니다.")
    return ok


if __name__ == "__main__":
    sys.exit(main())
