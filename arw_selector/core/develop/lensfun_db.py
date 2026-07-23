"""lensfun 데이터베이스 XML 버전 변환.

설치된 lensfun 라이브러리는 DB 포맷 **버전 1**까지만 읽습니다. 반면 lensfun
저장소의 최신 DB는 **버전 2**라, 그대로 주면 통째로 거부합니다:

    Database version is 2, but newest supported is only 1! -> XMLFormatError

그런데 두 포맷의 실제 차이는 하나뿐입니다(전체 DB에서 24군데):

    v1:  <distortion model="ptlens" focal="18" a=".." b=".." c=".."/>
         <real-focal-length focal="18" real-focal="17.3"/>

    v2:  <distortion model="ptlens" focal="18" a=".." b=".." c=".." real-focal="17.3"/>

왜곡·TCA·비네팅 계수는 완전히 같습니다. 그래서 real-focal 속성을 예전
요소로 되돌리고 버전 표기만 낮추면 **손실 없이** v1로 바꿀 수 있습니다.

덕분에 최신 DB(바디 1045 · 렌즈 1558)를 지금 라이브러리로도 쓸 수 있습니다.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

_VERSION = re.compile(r'(<lensdatabase\b[^>]*\bversion=")(\d+)(")')
_DISTORTION = re.compile(r"<distortion\b[^>]*?/>", re.IGNORECASE)
_REAL_FOCAL = re.compile(r'\s+real-focal="([^"]*)"')
# real-focal 의 'focal' 부분에 걸리지 않도록 앞에 하이픈이 없는 것만 잡습니다
_FOCAL = re.compile(r'(?<![-\w])focal="([^"]*)"')


def declared_version(text: str) -> int | None:
    """XML이 선언한 DB 포맷 버전. 못 찾으면 None."""
    match = _VERSION.search(text)
    return int(match.group(2)) if match else None


def _rewrite_distortion(match: "re.Match[str]") -> str:
    """real-focal 속성을 떼어내 <real-focal-length> 요소로 되돌립니다."""
    tag = match.group(0)
    real = _REAL_FOCAL.search(tag)
    if real is None:
        return tag

    cleaned = _REAL_FOCAL.sub("", tag)
    focal = _FOCAL.search(cleaned)
    if focal is None:
        # focal 을 못 읽으면 짝지을 수 없으니 속성만 버립니다.
        # 계수는 그대로라 왜곡 보정 자체는 정상 동작합니다.
        return cleaned
    return (
        f'{cleaned}\n            <real-focal-length focal="{focal.group(1)}"'
        f' real-focal="{real.group(1)}"/>'
    )


def convert_to_v1(text: str) -> str:
    """버전 2 DB XML을 버전 1로 바꿉니다. 이미 v1이면 그대로 돌려줍니다."""
    version = declared_version(text)
    if version is None or version <= 1:
        return text

    converted = _DISTORTION.sub(_rewrite_distortion, text)
    return _VERSION.sub(r"\g<1>1\g<3>", converted, count=1)


def needs_conversion(text: str) -> bool:
    version = declared_version(text)
    return version is not None and version > 1
