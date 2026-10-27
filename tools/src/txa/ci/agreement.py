"""Contributor Agreement - the first thing a contributor does after opening a pull request.

The bot asks the author to reply with one comment ("@txa-repo agree", optionally
``agree company="Name"``). It then deletes that comment, records the agreement inside its own comment
and removes ``txa:needs-agreement``; until then ``txa/agreement`` and ``txa/verified`` stay pending,
so nothing can merge. Like a real CLA bot it signs **once per person**: maintainers are exempt and
anyone who already has a merged pull request is recognised without asking again.
Comments are only ever read as data.
"""
from __future__ import annotations

import re

from txa.constants import LABELS, STATUS_CONTEXT
from txa.core.github import GitHub, GitHubError

MARK = "<!-- txa-bot:agreement"
PHRASE = "@txa-repo agree"
_PHRASE_RE = re.compile(r'^\s*@?txa-repo\s+agree(?:\s+company\s*=\s*"([^"\r\n]{1,80})")?\s*[.!]?\s*$', re.I)
LABEL = LABELS["agree"][0]
CONTEXT = "txa/agreement"
EXEMPT = {"OWNER", "MEMBER", "COLLABORATOR"}


def _text(login: str) -> str:
    return (f"{MARK} -->\nHello @{login},\n\n"
            "This PR cannot be merged until you accept the **TXA Contributor Agreement** (EULA): you confirm that you "
            "have the right to submit this manifest, that the installer is the vendor's own official download, and that "
            "the package follows the repository policy.\n\n"
            f"To accept, reply to this pull request with a single comment:\n\n```\n{PHRASE}\n```\n\n"
            f"Contributing for a company? Add it: `{PHRASE} company=\"Example Ltd\"`. "
            "You only have to do this once - later pull requests are recognised automatically.\n\n"
            "The bot removes your comment and marks this step as done. Nothing else starts until you do.\n\n"
            "<sub>Template: txabot/needsAgreement</sub>")


def _signed_text(login: str, how: str = "", company: str = "") -> str:
    extra = f" ({how})" if how else ""
    firm = f" on behalf of **{company}**" if company else ""
    return (f"{MARK} signed={login} -->\n✅ **Contributor Agreement accepted** by @{login}{firm}{extra}. "
            "Thank you - the automatic checks go on.\n\n<sub>Template: txabot/agreementSigned</sub>")


def find(gh: GitHub, repo: str, number: int) -> dict | None:
    for comment in gh.paginate(f"repos/{repo}/issues/{number}/comments"):
        if comment["body"].startswith(MARK) and comment["user"]["type"] == "Bot":
            return comment
    return None


def _is_signed_comment(comment: dict | None, login: str) -> bool:
    return bool(comment) and f"signed={login} -->" in comment["body"].split("\n", 1)[0]


def is_signed(gh: GitHub, repo: str, pr: dict) -> bool:
    return _is_signed_comment(find(gh, repo, pr["number"]), pr["user"]["login"])


def _returning(gh: GitHub, repo: str, login: str) -> bool:
    """Someone whose pull request was already merged has accepted the agreement before."""
    try:
        data = gh.get("search/issues", params={"q": f"repo:{repo} is:pr is:merged author:{login}", "per_page": 1})
    except GitHubError:
        return False
    return data.get("total_count", 0) > 0


def _set_status(gh: GitHub, repo: str, pr: dict, signed: bool) -> None:
    """``txa/agreement`` shows the step in the merge box; while it is open the required status is held too."""
    sha = pr["head"]["sha"]
    gh.post(f"repos/{repo}/statuses/{sha}", json={
        "state": "success" if signed else "pending", "context": CONTEXT,
        "description": "Contributor Agreement accepted" if signed else f"Comment {PHRASE} to accept"})
    if not signed:
        gh.post(f"repos/{repo}/statuses/{sha}", json={
            "state": "pending", "context": STATUS_CONTEXT, "description": "Waiting for the contributor to accept the agreement"})


def process(gh: GitHub, repo: str, number: int, comment_id: int | None = None) -> int:
    """Ask for the agreement (PR opened) or accept it (the author commented)."""
    pr = gh.get(f"repos/{repo}/pulls/{number}")
    if pr["state"] != "open":
        return 0
    author = pr["user"]["login"]
    current = {label["name"] for label in pr.get("labels", [])}
    bot = find(gh, repo, number)
    signed = _is_signed_comment(bot, author)
    how, company = "", ""
    if not signed and pr.get("author_association") in EXEMPT:
        signed, how = True, "maintainer, exempt"
    if not signed and _returning(gh, repo, author):
        signed, how = True, "agreed on an earlier pull request"
    if not signed and comment_id:
        try:
            comment = gh.get(f"repos/{repo}/issues/comments/{comment_id}")
        except GitHubError:
            comment = None
        match = _PHRASE_RE.match(comment["body"]) if comment and comment["user"]["login"] == author else None
        if match:
            gh.delete(f"repos/{repo}/issues/comments/{comment_id}", ok=(404,))
            signed, company = True, " ".join((match.group(1) or "").split())
    from txa.ci.gate import set_labels  # late: gate imports this module

    if signed:
        if bot and "signed=" not in bot["body"].split("\n", 1)[0]:
            gh.patch(f"repos/{repo}/issues/comments/{bot['id']}", json={"body": _signed_text(author, how, company)})
        elif not bot:
            gh.post(f"repos/{repo}/issues/{number}/comments", json={"body": _signed_text(author, how, company)})
        set_labels(gh, repo, number, current, add=set(), remove={LABEL})
        _set_status(gh, repo, pr, True)
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
