"""Pour `ko_wording.WORDING` into the extracted `.ts` file.

    python tools/build_translations.py     # extract first
    python tools/fill_translations.py      # then fill
    python tools/build_translations.py     # compile

Only entries still marked `unfinished` are touched, so wording edited by
hand in Qt Linguist survives.

Anything left unfilled is reported rather than passed over: an untranslated
string shows up as English in an otherwise Korean window, which reads as a
glitch and tends to sit there for months.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ko_wording import WORDING  # noqa: E402

TRANSLATIONS = Path(__file__).resolve().parents[1] / "data" / "translations"


def unescape(value: str) -> str:
    """`.ts` is XML. Quotes are escaped too, not just angle brackets —
    missing `&apos;` is what made three preset messages look absent."""
    for entity, char in (("&lt;", "<"), ("&gt;", ">"),
                         ("&apos;", "'"), ("&quot;", '"'), ("&amp;", "&")):
        value = value.replace(entity, char)
    return value


def escape(value: str) -> str:
    # `&` first, or the ampersands introduced below get escaped again.
    return (value.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def main() -> int:
    ts = TRANSLATIONS / "raw_selector_ko.ts"
    if not ts.is_file():
        print(f"{ts} 가 없습니다. 먼저 build_translations.py 를 돌리십시오.")
        return 1

    text = ts.read_text(encoding="utf-8")
    sources = re.findall(r"<source>(.*?)</source>", text, re.S)

    # `<source>` 와 `<translation>` 사이에는 `<extracomment>` 가 낄 수 있습니다
    # (소스의 `#:` 주석에서 옵니다). 붙어 있다고 가정한 정규식은 그런 항목을
    # 조용히 건너뛰었고, 그래서 채우지도 못하면서 "빠진 것 없음"이라고
    # 보고했습니다 — 도구가 거짓말을 한 셈입니다.
    between = r"(?:\s*<extracomment>.*?</extracomment>)?\s*"

    filled = 0
    missing: list[str] = []
    for raw in sources:
        plain = unescape(raw)
        korean = WORDING.get(plain)
        if korean is None:
            pattern = (r"<source>" + re.escape(raw) + r"</source>" + between
                       + r"<translation type=\"unfinished\">")
            if re.search(pattern, text, re.S):
                missing.append(plain)
            continue
        pattern = (r"(<source>" + re.escape(raw) + r"</source>" + between + r")"
                   r"<translation type=\"unfinished\"></translation>")
        text, count = re.subn(
            pattern, r"\1<translation>" + escape(korean) + "</translation>",
            text, count=1)
        filled += count

    ts.write_text(text, encoding="utf-8")
    left = len(re.findall(r'type="unfinished"', text))
    print(f"채움 {filled}개 / 남은 미번역 {left}개")

    if missing:
        print(f"\n**tools/ko_wording.py 에 없는 문자열 {len(missing)}개**")
        for source in missing:
            print(f"    {source!r}")
    unused = set(WORDING) - {unescape(s) for s in sources}
    if unused:
        print(f"\n참고: 더는 쓰이지 않는 항목 {len(unused)}개 "
              "(문구를 고쳤다면 사전도 함께 고치십시오)")
        for source in sorted(unused)[:10]:
            print(f"    {source!r}")
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
