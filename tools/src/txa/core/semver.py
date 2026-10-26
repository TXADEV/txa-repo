"""Strict X.Y.Z versions used by TXA manifests."""
from __future__ import annotations

import re

VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_LOOSE_RE = re.compile(r"(?<![\d.])(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?(?![\d])")


def parse(version: str) -> tuple[int, int, int]:
    match = VERSION_RE.match(version or "")
    if not match:
        raise ValueError(f"'{version}' is not a strict X.Y.Z version")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def is_valid(version: str) -> bool:
    return bool(VERSION_RE.match(version or ""))


def latest(versions) -> str | None:
    valid = [v for v in versions if is_valid(v)]
    return max(valid, key=parse) if valid else None


def coerce(text: str | None) -> str | None:
    """Best-effort X.Y.Z from free text such as '1.4.2.0' or 'setup-1.4.2-x64.exe'.

    A fourth component is only dropped when it is zero, so '1.2.3.4' stays ambiguous
    and returns None - the caller must ask for an explicit version.
    """
    if not text:
        return None
    match = _LOOSE_RE.search(text)
    if not match:
        return None
    major, minor, patch, build = match.groups()
    if build not in (None, "0"):
        return None
    return f"{int(major)}.{int(minor)}.{int(patch)}"
