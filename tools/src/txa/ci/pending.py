"""Packages whose pull request is still open: listed in the index as *waiting for review*, never installable.

The published ``pending.json`` lets ``txapm`` say "this exists but a moderator has not approved it yet"
instead of "not found". Everything comes from the GitHub API about *open pull requests* and is
treated as untrusted text: only a few fields are kept, control characters are stripped and the
lengths are capped. Nothing here is ever executed or used as a download location.
"""
from __future__ import annotations

import base64
import re
import unicodedata

from txa.constants import LABELS
from txa.core.github import GitHub, GitHubError
from txa.core.manifest import ManifestError, parse_card

_RELEASE_RE = re.compile(r"^(manifests/[a-z0-9]/([A-Z][A-Za-z0-9]{1,39})/([A-Za-z0-9][A-Za-z0-9-]{0,63})/(\d+\.\d+\.\d+))/([^/]+)$")
_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
_PRIORITY = [  # first match wins: what the contributor has to deal with first
    ("fix", "fix-required"), ("confirm", "needs-confirmation"), ("validating", "validating"),
    ("approved", "approved"), ("review", "awaiting-review"),
]
MAX_PRS = 100


def clean(text: object, limit: int) -> str:
    """Plain, single-line, printable text - safe to show in any terminal."""
    kept = "".join(" " if c.isspace() else c for c in str(text or "") if not unicodedata.category(c).startswith("C") or c.isspace())
    return " ".join(kept.split())[:limit]


def _read(gh: GitHub, repo: str, path: str, sha: str) -> dict | None:
    try:
        info = gh.get(f"repos/{repo}/contents/{path}", params={"ref": sha})
        if info.get("size", 0) > 65536 or info.get("encoding") != "base64":
            return None
        return parse_card(base64.b64decode(info["content"]).decode("utf-8"))
    except (GitHubError, ManifestError, ValueError, KeyError):
        return None


def collect(gh: GitHub, repo: str) -> list[dict]:
    """One entry per release in an open, non-draft pull request that adds or changes a manifest."""
    entries: list[dict] = []
    for count, pr in enumerate(gh.paginate(f"repos/{repo}/pulls", {"state": "open"})):
        if count >= MAX_PRS:
            break
        if pr.get("draft"):
            continue
        labels = {label["name"] for label in pr.get("labels", [])}
        status = next((name for key, name in _PRIORITY if LABELS[key][0] in labels), "validating")
        head_repo = (pr.get("head", {}).get("repo") or {}).get("full_name")
        if not head_repo:
            continue
        releases: dict[str, dict] = {}
        for item in gh.paginate(f"repos/{repo}/pulls/{pr['number']}/files"):
            match = _RELEASE_RE.match(item["filename"])
            if match and item.get("status") != "removed":
                folder, namespace, app, version, name = match.groups()
                release = releases.setdefault(folder, {"id": f"{namespace}.{app}", "version": version, "files": {}})
                release["files"][name] = item["filename"]
        for release in list(releases.values())[:3]:
            core = _read(gh, head_repo, release["files"]["core.txa.yaml"], pr["head"]["sha"]) if "core.txa.yaml" in release["files"] else None
            abouts = sorted(n for n in release["files"] if n.startswith("about."))
            about = _read(gh, head_repo, release["files"][abouts[0]], pr["head"]["sha"]) if abouts else None
            package = (core or {}).get("package") or {}
            entries.append({
                "id": release["id"],
                "version": release["version"],
                "name": clean((about or {}).get("name") or release["id"], 64),
                "summary": clean((about or {}).get("summary"), 160),
                "publisher": clean((about or {}).get("publisher"), 128),
                "alias": package.get("alias") if _ALIAS_RE.match(str(package.get("alias") or "")) else None,
                "aliases": [a for a in (package.get("aliases") or []) if isinstance(a, str) and _ALIAS_RE.match(a)][:8],
                "status": status,
                "pr": pr["number"],
                "url": f"https://github.com/{repo}/pull/{pr['number']}",
                "by": clean(pr["user"]["login"], 40),
                "updated": pr.get("updated_at"),
            })
    return entries
