"""Checking whether a newer release exists.

**Nothing here runs on its own.** The check reaches out to a server, which
means telling that server this machine is running this app at this version.
A photo tool has no business doing that unasked, so the only caller is a
button the user presses.

The endpoint is GitHub's public releases API. It needs no token and no
account; if the repository is not published yet, the call simply reports
that rather than pretending.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

log = logging.getLogger(__name__)

#: The repository releases are published from. Empty until the project is
#: public — the check reports "not configured" rather than guessing a URL.
REPOSITORY = ""

TIMEOUT_SECONDS = 6.0
"""Short on purpose. This runs while someone waits at a dialog."""


@dataclass(frozen=True)
class UpdateResult:
    """Outcome of one check. `error` and `latest` are mutually exclusive."""

    latest: str | None = None
    url: str = ""
    error: str = ""

    @property
    def configured(self) -> bool:
        return bool(REPOSITORY)


def parse_version(text: str) -> tuple[int, ...]:
    """"v0.14.1" -> (0, 14, 1). Unparseable parts count as 0.

    Comparing version strings directly gets "0.9" > "0.14" wrong, which is
    exactly the case that matters as a project passes its tenth release.
    """
    numbers = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in numbers[:4]) or (0,)


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


def check(current_version: str, *, repository: str = "") -> UpdateResult:
    """Ask once whether a newer release exists.

    Every failure — offline, timeout, rate limit, malformed reply — comes
    back as `error`. Raising here would put a traceback in front of someone
    who only wanted to know if there was an update.
    """
    repo = repository or REPOSITORY
    if not repo:
        return UpdateResult(error="not_configured")

    url = f"https://api.github.com/repos/{repo}/releases/latest"
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log.debug("update check failed: %s", exc)
        return UpdateResult(error="unreachable")
    except (ValueError, UnicodeDecodeError) as exc:
        log.debug("update check returned something unreadable: %s", exc)
        return UpdateResult(error="bad_reply")

    tag = payload.get("tag_name") or payload.get("name") or ""
    if not tag:
        return UpdateResult(error="bad_reply")

    page = payload.get("html_url") or f"https://github.com/{repo}/releases"
    if is_newer(tag, current_version):
        return UpdateResult(latest=tag, url=page)
    return UpdateResult(latest=None, url=page)
