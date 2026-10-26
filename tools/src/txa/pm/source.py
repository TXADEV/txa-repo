"""Package lookups.

Primary: the static txa.v1 REST API on GitHub Pages (``config.api_base()``).
Fallback: the GitHub REST contents API of txa-repo, assembled with the same code
the API publisher uses - so both paths return identical data.
"""
from __future__ import annotations

import re

import requests

from txa.core import config, net, semver
from txa.core.github import GitHub, GitHubError
from txa.core.manifest import Bundle, assemble, package_dir, split_id


class NotFound(LookupError):
    pass


class Source:
    def __init__(self, api: str | None = None, upstream: str | None = None, token: str | None = None):
        self.api = (api or config.api_base()).rstrip("/")
        self.upstream = upstream or config.get("upstream")
        self.http = net.session()
        self._token = token
        self._index: dict | None = None
        self.used_fallback = False

    # ------------------------------------------------------------------ API
    def _get_json(self, path: str) -> dict | None:
        try:
            response = self.http.get(f"{self.api}/{path}", timeout=30)
        except requests.RequestException:
            return None
        if response.status_code != 200:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    def index(self) -> dict:
        if self._index is None:
            self._index = self._get_json("index.json") or {"packages": []}
        return self._index

    def resolve(self, query: str) -> str:
        """Exact id (case-insensitive) or alias -> canonical id."""
        lowered = query.lower()
        for entry in self.index().get("packages", []):
            if entry["id"].lower() == lowered or (entry.get("alias") or "").lower() == lowered:
                return entry["id"]
        if "." not in query:
            namespace = config.get("namespace")
            for entry in self.index().get("packages", []):
                if entry["id"].lower() == f"{namespace}.{query}".lower():
                    return entry["id"]
            return f"{namespace}.{query}"
        return query

    def search(self, text: str) -> list[dict]:
        needle = text.lower()
        hits = []
        for entry in self.index().get("packages", []):
            haystack = " ".join([entry["id"], entry.get("alias") or "", entry.get("name", ""),
                                 entry.get("summary", ""), " ".join(entry.get("tags", []))]).lower()
            if needle in haystack:
                hits.append(entry)
        return hits

    def release(self, package_id: str, version: str | None = None) -> tuple[str, dict]:
        """Return (version, {core, build, about}) for the requested or newest version."""
        document = self._get_json(f"packages/{package_id}.json")
        if document:
            versions = document.get("versions", {})
            chosen = version or document.get("latest") or semver.latest(versions)
            if chosen not in versions:
                raise NotFound(f"{package_id} has no version {chosen} (available: {', '.join(versions)})")
            return chosen, versions[chosen]
        return self._github_release(package_id, version)

    def versions(self, package_id: str) -> list[str]:
        document = self._get_json(f"packages/{package_id}.json")
        if document:
            return list(document.get("versions", {}))
        return sorted(self._github_versions(package_id), key=semver.parse, reverse=True)

    # ------------------------------------------------------------------ GitHub fallback
    def _gh(self) -> GitHub:
        if self._token is None:
            from txa.core.credentials import load_token

            self._token = load_token()[0] or ""
        return GitHub(self._token or None)

    def _github_versions(self, package_id: str) -> list[str]:
        split_id(package_id)
        try:
            entries = self._gh().get(f"repos/{self.upstream}/contents/{package_dir(package_id)}")
        except GitHubError as exc:
            if exc.status == 404:
                raise NotFound(f"package {package_id} not found in {self.upstream}") from None
            raise
        return [e["name"] for e in entries if e["type"] == "dir" and semver.is_valid(e["name"])]

    def _github_release(self, package_id: str, version: str | None) -> tuple[str, dict]:
        self.used_fallback = True
        versions = self._github_versions(package_id)
        chosen = version or semver.latest(versions)
        if not chosen or chosen not in versions:
            raise NotFound(f"{package_id} has no version {version or '(none published)'}")
        path = f"{package_dir(package_id)}/{chosen}"
        gh = self._gh()
        texts = {}
        for entry in gh.get(f"repos/{self.upstream}/contents/{path}"):
            if entry["type"] == "file" and re.search(r"\.txa\.yaml$", entry["name"]):
                raw = self.http.get(entry["download_url"], timeout=30)
                raw.raise_for_status()
                texts[entry["name"]] = raw.content.decode("utf-8")
        bundle = Bundle.from_texts(path, texts)
        if bundle.problems or not bundle.core or not bundle.build:
            raise NotFound(f"{package_id} {chosen} has unreadable cards in {self.upstream}")
        return chosen, assemble(bundle)
