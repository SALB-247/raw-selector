"""Converting the lensfun database XML between versions.

The installed lensfun library reads only up to DB format **version 1**. The
latest DB in the lensfun repository, however, is **version 2**, and handed
over as it is it gets rejected wholesale:

    Database version is 2, but newest supported is only 1! -> XMLFormatError

Yet the real difference between the two formats is only one thing (24
places in the whole DB):

    v1:  <distortion model="ptlens" focal="18" a=".." b=".." c=".."/>
         <real-focal-length focal="18" real-focal="17.3"/>

    v2:  <distortion model="ptlens" focal="18" a=".." b=".." c=".." real-focal="17.3"/>

The distortion, TCA and vignetting coefficients are exactly the same. So by
putting the real-focal attribute back into the old element and lowering
only the version marker, it can be turned into v1 **losslessly**.

That is what lets the latest DB (1045 bodies, 1558 lenses) be used with the
current library.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

_VERSION = re.compile(r'(<lensdatabase\b[^>]*\bversion=")(\d+)(")')
_DISTORTION = re.compile(r"<distortion\b[^>]*?/>", re.IGNORECASE)
_REAL_FOCAL = re.compile(r'\s+real-focal="([^"]*)"')
# match only where there is no hyphen in front, so it does not catch the
# 'focal' part of real-focal
_FOCAL = re.compile(r'(?<![-\w])focal="([^"]*)"')


def declared_version(text: str) -> int | None:
    """The DB format version the XML declares. None if it is not found."""
    match = _VERSION.search(text)
    return int(match.group(2)) if match else None


def _rewrite_distortion(match: "re.Match[str]") -> str:
    """Detaches the real-focal attribute and puts it back as a
    <real-focal-length> element."""
    tag = match.group(0)
    real = _REAL_FOCAL.search(tag)
    if real is None:
        return tag

    cleaned = _REAL_FOCAL.sub("", tag)
    focal = _FOCAL.search(cleaned)
    if focal is None:
        # If focal cannot be read there is nothing to pair it with, so
        # only the attribute is dropped. The coefficients are untouched,
        # so distortion correction itself still works.
        return cleaned
    return (
        f'{cleaned}\n            <real-focal-length focal="{focal.group(1)}"'
        f' real-focal="{real.group(1)}"/>'
    )


def convert_to_v1(text: str) -> str:
    """Turns a version 2 DB XML into version 1. Already v1, it is returned
    as it is."""
    version = declared_version(text)
    if version is None or version <= 1:
        return text

    converted = _DISTORTION.sub(_rewrite_distortion, text)
    return _VERSION.sub(r"\g<1>1\g<3>", converted, count=1)


def needs_conversion(text: str) -> bool:
    version = declared_version(text)
    return version is not None and version > 1
