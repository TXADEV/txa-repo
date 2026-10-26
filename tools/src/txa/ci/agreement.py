"""Contributor Agreement - the first thing a contributor does after opening a pull request.

The bot asks the author to reply with one comment ("@txa-repo agree"). It then deletes that comment,
records the agreement inside its own comment and removes ``txa:needs-agreement``; until then the
``txa/verified`` status stays pending, so nothing can merge. Comments are only ever read as data.
"""
from __future__ import annotations

import re

from txa.constants import LABELS, STATUS_CONTEXT
from txa.core.github import GitHub, GitHubError

MARK = "<!-- txa-bot:agreement"
PHRASE = "@txa-repo agree"
_PHRASE_RE = re.compile(r"^\s*@?txa-repo\s+agree\s*[.!]?\s*$", re.I)
LABEL = LABELS["agree"][0]


def _text(login: str) -> str:
    return (f"{MARK} -->\nHello @{login},\n\n"
            "This PR cannot be merged until you accept the **TXA Contributor Agreement** (EULA): you confirm that you "
            "have the right to submit this manifest, that the installer is the vendor's own official download, and that "
            "the package follows the repository policy.\n\n"
            f"To accept, reply to this pull request with a single comment:\n\n```\n{PHRASE}\n```\n\n"
            "The bot removes your comment and marks this step as done. Nothing else starts until you do.")


def _signed_text(login: str) -> str:
    return (f"{MARK} signed={login} -->\n✅ **Contributor Agreement accepted** by @{login}. "
            "Thank you - the automatic checks go on.")


def find(gh: GitHub, repo: str, number: int) -> dict | None:
    for comment in gh.paginate(f"repos/{repo}/issues/{number}/comments"):
        if comment["body"].startswith(MARK) and comment["user"]["type"] == "Bot":
            return comment
    return None


def is_signed(gh: GitHub, repo: str, pr: dict) -> bool:
    comment = find(gh, repo, pr["number"])
    return bool(comment) and f"signed={pr['user']['login']} -->" in comment["body"].split("\n", 1)[0]


def _set_status(gh: GitHub, repo: str, pr: dict, signed: bool) -> None:
    """Hold the required status while the agreement is open; the gate sets the real result afterwards."""
    if not signed:
        gh.post(f"repos/{repo}/statuses/{pr['head']['sha']}", json={
            "state": "pending", "context": STATUS_CONTEXT, "description": "Waiting for the contributor to accept the agreement"})


def process(gh: GitHub, repo: str, number: int, comment_id: int | None = None) -> int:
    """Ask for the agreement (PR opened) or accept it (the author commented)."""
    pr = gh.get(f"repos/{repo}/pulls/{number}")
    if pr["state"] != "open":
        return 0
    author = pr["user"]["login"]
    current = {label["name"] for label in pr.get("labels", [])}
    bot = find(gh, repo, number)
    signed = bool(bot) and f"signed={author} -->" in bot["body"].split("\n", 1)[0]
    if not signed and comment_id:
        try:
            comment = gh.get(f"repos/{repo}/issues/comments/{comment_id}")
        except GitHubError:
            comment = None
        if comment and comment["user"]["login"] == author and _PHRASE_RE.match(comment["body"]):
            gh.delete(f"repos/{repo}/issues/comments/{comment_id}", ok=(404,))
            signed = True
    from txa.ci.gate import set_labels  # late: gate imports this module

    if signed:
        if bot and "signed=" not in bot["body"].split("\n", 1)[0]:
            gh.patch(f"repos/{repo}/issues/comments/{bot['id']}", json={"body": _signed_text(author)})
        elif not bot:
            gh.post(f"repos/{repo}/issues/{number}/comments", json={"body": _signed_text(author)})
        set_labels(gh, repo, number, current, add=set(), remove={LABEL})
        _rerun_gate(gh, repo, pr)
        print(f"agreement: PR #{number} accepted by {author}")
        return 0
    if not bot:
        gh.post(f"repos/{repo}/issues/{number}/comments", json={"body": _text(author)})
    set_labels(gh, repo, number, current, add={LABEL}, remove=set())
    _set_status(gh, repo, pr, False)
    print(f"agreement: PR #{number} waiting for {author}")
    return 0


def _rerun_gate(gh: GitHub, repo: str, pr: dict) -> None:
    """If the checks already finished while the agreement was open, run them again so the gate re-decides."""
    from txa.ci.progress import find_validate_run

    try:
        run = find_validate_run(gh, repo, pr["head"]["sha"])
        if run and run.get("status") == "completed":
            gh.post(f"repos/{repo}/actions/runs/{run['id']}/rerun")
    except GitHubError as exc:
        print(f"::warning::could not re-run the checks ({exc})")
