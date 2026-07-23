"""Extract translatable strings and compile the translations.

    python tools/build_translations.py            # extract + compile
    python tools/build_translations.py --check    # report gaps, change nothing

Extraction (`.ts`) is additive: strings already translated keep their
translation, new ones arrive marked `unfinished`. Compilation (`.qm`) is
what the app actually loads.

**A missing or mismatched translation never raises.** Qt hands back the
English source, so a broken chain looks like "the app is in English"
rather than like a bug. `--check` exists so that stays visible, and
`tests/test_i18n_roundtrip.py` walks the chain end to end.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "arw_selector" / "gui"
TRANSLATIONS = ROOT / "data" / "translations"

#: Languages we ship. English is the source, so it needs no file.
LANGUAGES = ("ko",)


def sources() -> list[Path]:
    """Files that may contain `tr(...)`.

    Only the GUI package: `arw_selector.core` must not import Qt, so by
    definition it holds no translatable text.
    """
    return sorted(p for p in GUI.rglob("*.py") if p.name != "__init__.py")


def run(tool: str, *args: str) -> bool:
    try:
        result = subprocess.run(
            [tool, *args], cwd=ROOT, capture_output=True, text=True)
    except FileNotFoundError:
        print(f"  {tool} 를 찾을 수 없습니다 — PySide6 도구가 설치되어 있는지 확인하십시오")
        return False
    output = (result.stdout + result.stderr).strip()
    if output:
        for line in output.splitlines():
            print(f"    {line}")
    return result.returncode == 0


def unfinished(ts: Path) -> list[str]:
    """Sources still waiting for a translation.

    Works one `<message>` block at a time rather than matching across the
    whole file. A pattern spanning `<source>` to `<translation>` looks
    right but backtracks past every already-translated entry until it
    reaches an untranslated one, and then reports that whole run as a
    single source string. The count came out right and the names were
    nonsense.

    (`<extracomment>` also sits between the two elements — it comes from
    `#:` comments in the source — which is why matching a fixed shape
    between them fails as well.)
    """
    if not ts.is_file():
        return []
    text = ts.read_text(encoding="utf-8")
    missing = []
    for block in re.findall(r"<message>(.*?)</message>", text, re.S):
        if 'type="unfinished"' not in block:
            continue
        found = re.search(r"<source>(.*?)</source>", block, re.S)
        missing.append(found.group(1) if found else "(source unknown)")
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description="번역 추출·컴파일")
    parser.add_argument("--check", action="store_true",
                        help="빠진 번역만 보고하고 파일은 건드리지 않습니다")
    args = parser.parse_args()

    TRANSLATIONS.mkdir(parents=True, exist_ok=True)
    files = [str(p.relative_to(ROOT)) for p in sources()]
    print(f"검사 대상 {len(files)}개 파일\n")

    failed = False
    for language in LANGUAGES:
        ts = TRANSLATIONS / f"raw_selector_{language}.ts"
        qm = TRANSLATIONS / f"raw_selector_{language}.qm"

        if not args.check:
            print(f"[{language}] 문자열 추출")
            if not run("pyside6-lupdate", *files, "-ts", str(ts)):
                failed = True
                continue

        missing = unfinished(ts)
        if missing:
            print(f"[{language}] **번역이 빠진 문자열 {len(missing)}개**")
            for source in missing[:15]:
                print(f"    {source}")
            if len(missing) > 15:
                print(f"    … 외 {len(missing) - 15}개")
            failed = True
        else:
            print(f"[{language}] 빠진 번역 없음")

        if not args.check:
            print(f"[{language}] 컴파일")
            if not run("pyside6-lrelease", str(ts), "-qm", str(qm)):
                failed = True

    if failed:
        print("\n번역이 완전하지 않습니다. 빠진 문자열은 화면에 영어로 나옵니다.")
        print("Qt Linguist 로 채우십시오:  pyside6-linguist data/translations/*.ts")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
