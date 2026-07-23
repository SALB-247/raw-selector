"""공개 저장소로 내보낼 파일만 골라 복사합니다.

손으로 고르면 반드시 빠뜨립니다. 공개는 되돌릴 수 없으므로 — 올린 뒤에
지워도 클론·포크·캐시에 남습니다 — 규칙을 코드로 적어 두고 매번 같은
결과가 나오게 합니다.

기본은 **거부**입니다. 목록에 없는 것은 안 나갑니다. 새 파일을 만들면
여기에 추가해야 공개본에 들어갑니다. 반대로 두면(기본 허용) 개인 자료를
새로 만들 때마다 빠뜨릴 위험이 생깁니다.

    python tools/export_public.py --to D:\\ARW_SELECTOR_public
    python tools/export_public.py --to ... --dry-run
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 공개할 폴더. 그 아래는 아래 EXCLUDE에 걸리지 않는 한 전부 나갑니다.
INCLUDE_DIRS = (
    "arw_selector",
    "tests",
    "tools",
    "assets",
    "data/lensfun",
    "data/develop_presets",
    "data/calibration",
    "data/translations",
)

#: 공개할 최상위 파일.
INCLUDE_FILES = (
    ".gitattributes",
    "BUILD.md",
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "THIRD_PARTY.md",
    "build_windows.py",
    "build_macos.py",
    "launcher.py",
    "make_icon.py",
    "pyproject.toml",
    "stamp_version.py",
    "verify_dist.py",
)

#: 절대 나가면 안 되는 것 (경로 조각으로 판단).
#:
#: 사진과 라벨은 사용자 개인 자료입니다. 라벨에는 사진 라이브러리의 전체
#: 경로가 들어 있고, 사진에는 공개에 동의한 적 없는 인물이 찍혀 있습니다.
#: 판정 프리셋은 파일 이름부터 촬영 맥락을 드러냅니다.
EXCLUDE_PARTS = (
    "labels",
    "data/select_presets",
    "data/logs",
    "data/lensfun/.v1cache",
    "thumbs",
    "build",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".git",
)

#: 확장자로 막는 것. RAW 원본이 실수로 섞이는 것을 막습니다.
EXCLUDE_SUFFIXES = (
    ".arw", ".cr2", ".cr3", ".nef", ".raf", ".orf", ".rw2", ".dng",
    ".hif", ".heic", ".jpg", ".jpeg",
    ".sqlite", ".sqlite-journal", ".log", ".pyc",
)

#: 공개본에 넣을 .gitignore.
#:
#: 원본 것을 그대로 복사하지 않습니다. 거기에는 사용자의 사진 폴더 이름이
#: 적혀 있어서 그 자체로 개인 맥락을 드러냅니다.
PUBLIC_GITIGNORE = """\
__pycache__/
*.py[cod]
*.egg-info/
.pytest_cache/
.venv/
venv/

build/
dist/

# Photos and anything derived from them stay out of the repository.
*.ARW
*.CR2
*.CR3
*.NEF
*.RAF
*.ORF
*.RW2
*.DNG
*.HIF
*.HEIC

# Regenerable: analysis cache, thumbnails, logs.
*.sqlite
*.sqlite-journal
.raw_selector_cache/
.arw_selector_cache/
thumbs/
data/logs/
data/lensfun/.v1cache/

# Personal: judgement presets and hand-made ground-truth labels.
data/select_presets/
labels/

.DS_Store
Thumbs.db
"""

#: 이름으로 막는 것. 에이전트 지시문과 내부 메모는 공개 대상이 아닙니다.
EXCLUDE_NAMES = (
    "claude.md",
    "agents.md",
    "masking_plan.md",
    "nuitka-crash-report.xml",
    "배포_읽어보세요.txt",
    "자체점검.bat",
)


def blocked(relative: Path) -> str | None:
    """내보내면 안 되는 이유. 나가도 되면 None."""
    posix = relative.as_posix()
    for part in EXCLUDE_PARTS:
        if posix == part or posix.startswith(part + "/"):
            return f"제외 폴더 {part}"
    if relative.suffix.lower() in EXCLUDE_SUFFIXES:
        return f"제외 확장자 {relative.suffix}"
    if relative.name.lower() in EXCLUDE_NAMES:
        return f"제외 파일명 {relative.name}"
    return None


def collect() -> tuple[list[Path], list[tuple[Path, str]]]:
    keep: list[Path] = []
    dropped: list[tuple[Path, str]] = []

    candidates: list[Path] = []
    for name in INCLUDE_FILES:
        path = ROOT / name
        if path.is_file():
            candidates.append(path)
    for name in INCLUDE_DIRS:
        base = ROOT / name
        if base.is_dir():
            candidates.extend(p for p in base.rglob("*") if p.is_file())

    for path in sorted(candidates):
        relative = path.relative_to(ROOT)
        reason = blocked(relative)
        if reason:
            dropped.append((relative, reason))
        else:
            keep.append(relative)
    return keep, dropped


def audit_leftovers(keep: list[Path]) -> list[Path]:
    """저장소가 추적 중인데 공개 목록에 없는 것들.

    빠뜨린 것이 있는지 사람이 눈으로 확인할 수 있게 보여 줍니다. 그냥
    넘어가면 새로 만든 소스 파일이 조용히 빠질 수 있습니다.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True,
            text=True, encoding="utf-8", check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    tracked = {Path(line) for line in out.stdout.splitlines() if line}
    # .gitignore는 공개용으로 새로 쓰므로 빠진 것이 아닙니다.
    return sorted(tracked - set(keep) - {Path(".gitignore")})


def main() -> int:
    parser = argparse.ArgumentParser(description="공개 저장소용 파일만 내보냅니다")
    parser.add_argument("--to", required=True, type=Path, help="내보낼 폴더")
    parser.add_argument("--dry-run", action="store_true", help="복사하지 않고 목록만")
    args = parser.parse_args()

    keep, dropped = collect()
    print(f"내보낼 파일 {len(keep)}개 / 걸러낸 파일 {len(dropped)}개\n")

    if dropped:
        print("걸러낸 것")
        for relative, reason in dropped[:20]:
            print(f"  {relative}  ← {reason}")
        if len(dropped) > 20:
            print(f"  … 외 {len(dropped) - 20}개")

    leftovers = audit_leftovers(keep)
    if leftovers:
        print(f"\n**저장소에 있는데 공개 목록에 없는 파일 {len(leftovers)}개** "
              "— 의도한 것인지 확인하십시오")
        for relative in leftovers:
            print(f"  {relative}")

    if args.dry_run:
        print("\n(--dry-run 이라 복사하지 않았습니다)")
        return 0

    target = args.to.resolve()
    if target == ROOT:
        print("\n원본 폴더로는 내보낼 수 없습니다")
        return 1

    print(f"\n복사 위치: {target}")
    for relative in keep:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    # .gitignore는 원본을 복사하지 않고 공개용으로 새로 씁니다.
    (target / ".gitignore").write_text(PUBLIC_GITIGNORE, encoding="utf-8")
    print(f"완료 — {len(keep)}개 복사 + .gitignore 생성")

    # 내보낸 결과에 개인 자료가 섞이지 않았는지 마지막으로 훑습니다.
    strays = [
        p.relative_to(target) for p in target.rglob("*")
        if p.is_file() and blocked(p.relative_to(target))
    ]
    if strays:
        print(f"\n경고: 내보낸 폴더에 걸러야 할 파일이 {len(strays)}개 있습니다")
        for relative in strays[:10]:
            print(f"  {relative}")
        return 1
    print("내보낸 폴더 재검사: 개인 자료 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
