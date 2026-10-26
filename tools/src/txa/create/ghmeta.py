"""Facts about a GitHub project, read from its release link, to pre-fill the manifest questions."""
from __future__ import annotations

import re

from txa.core import licenses, semver
from txa.core.github import GitHub, GitHubError

RELEASE_RE = re.compile(r"^https://github\.com/([^/\s]+)/([^/\s]+)/releases/download/([^/\s]+)/")
_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")


def humanize(repo: str) -> str:
    """'SteamRouteFixer' / 'steam-route-fixer' -> 'Steam Route Fixer'."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", repo.replace("-", " ").replace("_", " "))
    return " ".join(word if word.isupper() else word[:1].upper() + word[1:] for word in spaced.split())


def from_release_url(url: str, gh: GitHub | None = None) -> dict | None:
    """Metadata of the project behind a ``github.com/<owner>/<repo>/releases/download/...`` link.

    Returns None for any other link or when GitHub cannot be reached - callers simply ask the user.
    """
    match = RELEASE_RE.match(url)
    if not match:
        return None
    owner, repo, tag = match.groups()
    gh = gh or GitHub()
    try:
        project = gh.get(f"repos/{owner}/{repo}")
        user = gh.get(f"users/{owner}")
    except GitHubError:
        return None
    info: dict = {
        "owner": owner, "repo": repo, "tag": tag, "html_url": project.get("html_url"),
        "version": semver.coerce(tag),
        "publisher": user.get("name") or user.get("login") or owner,
        "name": humanize(repo),
        "summary": (project.get("description") or "").strip() or None,
        "homepage": project.get("homepage") or project.get("html_url"),
        "tags": [t for t in project.get("topics", []) if _TAG_RE.match(t)][:8],
        "release_url": f"https://github.com/{owner}/{repo}/releases/tag/{tag}",
    }
    spdx = (project.get("license") or {}).get("spdx_id")
    if spdx and spdx in licenses.SPDX:
        info["license"] = spdx
        try:
            info["license_url"] = gh.get(f"repos/{owner}/{repo}/license").get("html_url")
        except GitHubError:
            info["license_url"] = None
    return info
