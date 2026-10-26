"""One GitHub *check run* per bot step - the numbered list in the pull request's merge box.

    01. Character Encoding & BOM Sanity       Queued - Waiting to run this check...
    02. Strict YAML Structure & Indentation   in progress
    ...
    16. Auto-Merge Gate & Code Owner Review   Queued

``TXA Progress`` creates all sixteen as *queued* the moment the PR opens and moves them along as
the Validate job reaches each step; ``TXA Gate`` writes the exact outcome of every step (with the
findings as the check's text) when the report is in. Needs a token with ``checks: write``.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from txa.ci.steps import TITLES
from txa.core.github import GitHub, GitHubError

_NAME = re.compile(r"^(\d{2})\. ")
_ORDER = {"fail": 0, "warn": 1, "ok": 2, "info": 3, "skip": 4}
_RANK = {"FAIL": 3, "WARN": 2, "PASS": 1, "SKIP": 0}
_TITLE = {"PASS": "Passed", "WARN": "Passed with warnings", "FAIL": "Failed", "SKIP": "Skipped"}


def name_of(number: int) -> str:
    return f"{number:02d}. {TITLES[number]}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def conclusion_for(status: str) -> str:
    """PASS/WARN keep the merge box green (warnings are explained in the check text)."""
    return {"PASS": "success", "WARN": "success", "FAIL": "failure", "SKIP": "skipped"}.get(status, "neutral")


def create_all(gh: GitHub, repo: str, head_sha: str, details_url: str | None = None) -> dict[int, int]:
    """Create the sixteen checks as 'queued' and return {step number: check run id}."""
    ids: dict[int, int] = {}
    for number in TITLES:
        payload = {
            "name": name_of(number), "head_sha": head_sha, "status": "queued",
            "output": {"title": "Waiting to run", "summary": "Queued by the TXA bot - this step has not started yet."},
        }
        if details_url:
            payload["details_url"] = details_url
        try:
            ids[number] = gh.post(f"repos/{repo}/check-runs", json=payload)["id"]
        except GitHubError as exc:
            print(f"::warning::cannot create check '{name_of(number)}': {exc}")
            return ids
    return ids


def existing(gh: GitHub, repo: str, head_sha: str) -> dict[int, int]:
    """Latest check run id of every TXA step already on this commit."""
    found: dict[int, int] = {}
    try:
        data = gh.get(f"repos/{repo}/commits/{head_sha}/check-runs", params={"filter": "latest", "per_page": 100})
    except GitHubError:
        return found
    for run in data.get("check_runs", []):
        match = _NAME.match(run["name"])
        if match and int(match[1]) in TITLES and run["name"] == name_of(int(match[1])):
            found[int(match[1])] = run["id"]
    return found


def start(gh: GitHub, repo: str, check_id: int, summary: str = "Running now…") -> None:
    gh.patch(f"repos/{repo}/check-runs/{check_id}", json={
        "status": "in_progress", "started_at": _now(), "output": {"title": "Running", "summary": summary}})


def finish(gh: GitHub, repo: str, check_id: int, status: str, summary: str, text: str | None = None,
           details_url: str | None = None) -> None:
    output = {"title": _TITLE.get(status, status), "summary": summary[:60000]}
    if text:
        output["text"] = text[:60000]
    payload = {"status": "completed", "conclusion": conclusion_for(status), "completed_at": _now(), "output": output}
    if details_url:
        payload["details_url"] = details_url
    gh.patch(f"repos/{repo}/check-runs/{check_id}", json=payload)


def _worst(rows: list[dict]) -> str:
    return max((r["status"] for r in rows), key=lambda s: _RANK.get(s, 0)) if rows else "SKIP"


def _describe(rows: list[dict]) -> tuple[str, str]:
    """(summary, text) from the findings of one step across every release in the PR."""
    messages = sorted((m for r in rows for m in r["messages"]), key=lambda m: _ORDER.get(m["level"], 9))
    summary = "\n".join(f"- **{m['level']}** {m['text']}" for m in messages[:12]) or "Nothing to report."
    if len(messages) > 12:
        summary += f"\n- … and {len(messages) - 12} more (see the check text)"
    text = "\n".join(f"- `{m['level']}` {m['text']}" for m in messages)
    return summary, text


def finalize(gh: GitHub, repo: str, head_sha: str, report: dict, gate: dict | None,
             details_url: str | None = None) -> None:
    """Write the exact result of every step. Creates a check that was never queued (e.g. Progress did not run)."""
    ids = existing(gh, repo, head_sha)
    results = report.get("results", [])
    for number in TITLES:
        if number <= 14:
            rows = [r for r in results if r["step"] == number]
            if not rows:
                continue
            status = _worst(rows)
            summary, text = _describe(rows)
        else:
            entry = (gate or {}).get(f"step{number}") or {}
            status = entry.get("status", "PENDING")
            if status == "PENDING":
                if number in ids:
                    start(gh, repo, ids[number], entry.get("detail", "Running now…"))
                continue
            summary, text = entry.get("detail", ""), None
        if number not in ids:
            created = gh.post(f"repos/{repo}/check-runs", json={
                "name": name_of(number), "head_sha": head_sha, "status": "in_progress", "started_at": _now()})
            if not created.get("id"):
                continue  # GitHub did not hand back a check run: skip it, the report comment still tells the story
            ids[number] = created["id"]
        finish(gh, repo, ids[number], status, summary, text, details_url)
