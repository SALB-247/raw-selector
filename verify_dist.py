"""빌드된 배포 폴더가 다른 PC에서 제대로 돌지 확인합니다.

빌드 직후 이 PC에서 돌려 보면 "여기선 되는데 저기선 안 되는" 원인을 미리
걸러 냅니다. 대부분은 개발 폴더에만 있고 배포본에는 안 들어간 파일입니다.

    python verify_dist.py build\\launcher.dist
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

EXE_NAME = "RAW_selector.exe"


def check(dist: Path) -> int:
    if not dist.is_dir():
        print(f"폴더가 없습니다: {dist}")
        return 1

    # 절대 경로로 굳혀 둡니다. 아래 --selftest 는 cwd를 배포 폴더로 바꿔서
    # 돌리는데, 상대 경로로 받으면 그 안에서 다시 상대로 해석되어
    # "파일 이름, 디렉터리 이름 또는 볼륨 레이블 구문이 잘못되었습니다"로
    # 죽습니다. BUILD.md가 안내하는 `verify_dist.py build\launcher.dist`가
    # 정확히 그 경우입니다.
    dist = dist.resolve()

    failures = 0

    def report(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        print(f"  [{'OK  ' if ok else 'FAIL'}] {label}")
        if detail:
            print(f"          {detail}")
        if not ok:
            failures += 1

    print(f"배포 폴더: {dist}")
    print()
    print("파일 구성")

    exe = dist / EXE_NAME
    report("실행 파일", exe.is_file(), str(exe))

    lens = dist / "data" / "lensfun"
    xmls = sorted(lens.glob("*.xml")) if lens.is_dir() else []
    report(f"렌즈 프로필 XML ({len(xmls)}개)", len(xmls) >= 50,
           "" if xmls else f"{lens} 가 비었습니다 — 광학 자동보정이 동작하지 않습니다")

    presets = dist / "data" / "develop_presets"
    yamls = sorted(presets.glob("*.yaml")) if presets.is_dir() else []
    report(f"보정 프리셋 ({len(yamls)}개)", len(yamls) >= 5,
           ", ".join(p.stem for p in yamls))

    # 판정 프리셋을 따로 셉니다. 예전에는 보정 프리셋만 세면서 "기본 프리셋"
    # 이라고 불러서, 판정 프리셋이 통째로 빠진 배포본이 점검을 통과했습니다.
    select = dist / "data" / "select_presets"
    select_yamls = sorted(select.glob("*.yaml")) if select.is_dir() else []
    report(f"판정 프리셋 ({len(select_yamls)}개)", len(select_yamls) >= 1,
           ", ".join(p.stem for p in select_yamls))

    icon = dist / "assets" / "icon.ico"
    report("아이콘", icon.is_file(), str(icon))

    # 받는 PC에는 Python이 없습니다. 점검이 더블클릭으로 되어야 합니다.
    for name in ("배포_읽어보세요.txt", "자체점검.bat", "CHANGELOG.md"):
        report(name, (dist / name).is_file())

    models = dist / "arw_selector" / "core" / "models"
    detector = models / "face_detection_yunet_2023mar.onnx"
    report("얼굴 검출 모델", detector.is_file(), str(detector))

    # 없어도 예외가 나지 않고 타원 근사로 물러서므로, 여기서 안 세면
    # 마스크 정밀도와 눈 감김 감점이 통째로 빠진 것을 아무도 모릅니다.
    mesh = models / "face_mesh_192x192.onnx"
    report("얼굴 윤곽 모델 (468점)", mesh.is_file(), str(mesh))

    # 개발 폴더의 흔적이 섞여 나가면 안 됩니다
    logs = dist / "data" / "logs"
    report("로그 폴더가 섞이지 않음", not logs.exists(),
           f"{logs} 가 배포본에 들어갔습니다" if logs.exists() else "")

    # lensfunpy 자체 DB (번들 기본값)
    bundled = dist / "lensfunpy" / "db_files"
    report("lensfunpy 번들 DB", bundled.is_dir(), str(bundled))

    print()
    print("실행 확인 (--selftest)")
    if exe.is_file():
        # 반드시 진짜 콘솔(cmd)을 통해 돌립니다. capture_output으로 파이프를
        # 만들어 실행하면 콘솔이 없는 exe도 출력이 잡혀서, "콘솔에서 돌리면
        # 아무것도 안 나온다"는 문제를 못 잡습니다. 실제로 그렇게 놓쳤습니다.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            captured = Path(tmp) / "console.txt"
            # **리스트가 아니라 문자열로 넘깁니다.** 리스트로 주면 파이썬이
            # list2cmdline으로 조립하면서 안쪽 따옴표를 `\"` 로 이스케이프하는데,
            # 그건 C 런타임 규칙이라 cmd.exe가 알아듣지 못합니다. 실제로
            # "파일 이름, 디렉터리 이름 또는 볼륨 레이블 구문이 잘못되었습니다"
            # 만 내고 exe는 아예 실행되지 않았습니다 — 즉 이 점검이 배포본을
            # 한 번도 실제로 돌려 본 적이 없었습니다.
            #
            # 바깥 따옴표를 한 겹 더 두르는 `cmd /c ""exe" args"` 는 경로에
            # 공백이 있어도 되는 표준 형태입니다.
            command = f'cmd.exe /c ""{exe}" --selftest > "{captured}" 2>&1"'
            try:
                result = subprocess.run(command, timeout=300, cwd=str(dist))
                console_text = (
                    captured.read_text(encoding="utf-8", errors="replace")
                    if captured.is_file() else ""
                )
            except subprocess.TimeoutExpired:
                report("자체 점검 통과", False, "300초 안에 끝나지 않았습니다")
                console_text, result = "", None
            except OSError as exc:
                report("자체 점검 통과", False, str(exc))
                console_text, result = "", None

            for line in console_text.strip().splitlines():
                print(f"    {line}")

            if result is not None:
                report("자체 점검 통과", result.returncode == 0,
                       f"종료 코드 {result.returncode}")
                report("콘솔에 출력이 보임", bool(console_text.strip()),
                       "콘솔 모드가 disable이면 아무것도 안 나옵니다")

        # 콘솔 출력이 막혀도 결과를 확인할 수 있어야 합니다
        record = dist / "data" / "logs" / "selftest.txt"
        report("결과가 파일로도 남음", record.is_file(), str(record))
    else:
        report("자체 점검 통과", False, "실행 파일이 없어 건너뜀")

    print()
    if failures:
        print(f"{failures}건 실패 — 이대로는 다른 PC에서 문제가 납니다.")
    else:
        print("전부 통과. 이 폴더를 통째로 옮기면 됩니다.")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="배포 폴더 검증")
    parser.add_argument(
        "dist", nargs="?", default="build/launcher.dist",
        help="배포 폴더 경로 (기본: build/launcher.dist)",
    )
    args = parser.parse_args()
    return check(Path(args.dist))


if __name__ == "__main__":
    raise SystemExit(main())
