"""STEP 15 + STEP 16 - run only from the trusted ``TXA Gate`` workflow.

``TXA Validate`` runs on ``pull_request`` with a read-only token, so it cannot label
or merge. It uploads ``report.json``; the gate (``workflow_run``, always executed
from the default branch) treats that report as untrusted input:

* the PR number from the report is re-checked against the PR's real head SHA;
* the changed-file list is fetched again from the API, never taken from the report;
* any PR touching files outside ``manifests/`` is never auto-merged.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from txa.ci.report import confirm_note, render_markdown
from txa.constants import LABELS, REPORT_MARKER, STATUS_CONTEXT
from txa.core.github import GitHub, GitHubError

MANAGED = {name for name, _, _ in LABELS.values()}


def _label(key: str) -> str:
    return LABELS[key][0]


def load_report(path: Path | None) -> dict | None:
    if not path or not Path(path).is_file():
        return None
    try:
        data = json.loads(Path(path).read_text("utf-8"))
    except ValueError:
        return None
    if data.get("schema") != "txa.report.v1" or not isinstance(data.get("results"), list):
        return None
    return data


def _find_pr(gh: GitHub, repo: str, head_sha: str) -> dict | None:
    for pr in gh.paginate(f"repos/{repo}/pulls", {"state": "open"}):
        if pr["head"]["sha"] == head_sha:
            return pr
    return None


def triage(gh: GitHub, repo: str, report_path: Path | None, head_sha: str, state_path: Path,
           run_url: str | None = None, run_id: int | None = None) -> int:
    """STEP 15 - decide labels, publish the report comment and the txa/verified status."""
    report = load_report(report_path)
    pr = None
    if report and isinstance(report.get("pr"), int):
        pr = gh.get(f"repos/{repo}/pulls/{report['pr']}")
        if pr["head"]["sha"] != head_sha:
            pr = None  # report claims a PR it does not belong to - fall back to a lookup
    pr = pr or _find_pr(gh, repo, head_sha)
    if pr is None:
        print(f"::notice::no open pull request has head {head_sha[:12]} (superseded by a newer push?) - nothing to do")
        return 0
    number = pr["number"]
    files = [f["filename"] for f in gh.paginate(f"repos/{repo}/pulls/{number}/files")]
    outside = sorted(f for f in files if not f.startswith("manifests/"))
    touches_manifests = any(f.startswith("manifests/") for f in files)
    mixed = bool(outside) and touches_manifests

    if report is None:
        verdict, reason = "broken", "validation report missing or malformed"
    elif report.get("head") != head_sha:
        verdict, reason = "broken", "report was produced for a different commit"
    else:
        verdict, reason = report["verdict"], ""
    if verdict in ("pass", "fail") and not touches_manifests:
        verdict, reason = "broken", "report lists releases but the PR changes no manifests"

    modes = {t["mode"] for t in (report or {}).get("targets", [])}
    failed = sorted({r["step"] for r in (report or {}).get("results", []) if r["status"] == "FAIL"})
    wanted: set[str] = set()
    for mode, key in (("new-app", "new_app"), ("new-version", "new_version"), ("update", "update"), ("removal", "removal")):
        if mode in modes:
            wanted.add(_label(key))
    if outside:
        wanted.add(_label("infra"))
    removal_only = modes == {"removal"}

    # a moderator may have approved while the checks were still running
    review_label = _label("approved") if review_decision(gh, repo, number) == "APPROVED" else _label("review")
    if verdict == "pass" and not mixed:
        wanted.add(review_label)
        if not removal_only:
            wanted.add(_label("verified"))
        state, description = "success", (
            "Removal - waiting for moderator" if removal_only else "All automated TXA checks passed")
    elif verdict == "infra":
        wanted.add(review_label)
        state, description = "success", "No manifest changes - admin code-owner review required"
    elif mixed:
        wanted.add(_label("fix"))
        state, description = "failure", "Manifests mixed with infrastructure files - split the PR"
    elif verdict == "fail":
        wanted.add(_label("fix"))
        state, description = "failure", f"Failed step(s): {', '.join(f'{s:02d}' for s in failed)}"
    elif verdict == "empty":
        state, description = "success", "Nothing to validate"
    else:
        wanted.add(_label("fix"))
        state, description = "error", reason or "validation did not complete"

    # first of all: the Contributor Agreement - until the author accepts it nothing may merge
    from txa.ci import agreement

    agreed = agreement.is_signed(gh, repo, pr)
    if not agreed and state == "success":
        wanted.add(_label("agree"))
        state, description = "pending", "Waiting for the contributor to accept the agreement"

    # the contributor must tick the checklist in the description; until then nothing may merge
    open_boxes = checklist(pr.get("body"))
    if open_boxes and state == "success":
        wanted.add(_label("confirm"))
        state, description = "pending", f"Waiting for the contributor to tick {len(open_boxes)} checklist box(es)"

    current = {label["name"] for label in pr.get("labels", [])}
    set_labels(gh, repo, number, current, add=wanted, remove=MANAGED)

    from txa.ci.progress import pipeline_health, step_timings  # imported late: progress depends on this module

    timings: dict[str, float] = {}
    if run_id:
        try:
            timings = {str(n): round(t, 1) for n, t in step_timings(gh, repo, run_id).items()}
        except GitHubError as exc:
            print(f"::warning::could not read step timings ({exc})")

    gh.post(f"repos/{repo}/statuses/{head_sha}", json={
        "state": state, "context": STATUS_CONTEXT, "description": description[:140],
        **({"target_url": run_url} if run_url else {}),
    })

    eligible = (state in ("success", "pending") and verdict == "pass" and not outside and not removal_only
                and not pr.get("draft"))
    gate = {
        "pr": number, "head": head_sha, "verdict": verdict, "eligible": eligible, "labels": sorted(wanted),
        "why": description, "timings": timings, "confirm": open_boxes, "health": pipeline_health(gh, repo),
        "step15": {"status": "PASS" if state in ("success", "pending") else "FAIL",
                   "detail": f"labels: {', '.join(sorted(wanted)) or '-'} · status {STATUS_CONTEXT}={state}"},
        "step16": {"status": "PENDING", "detail": "evaluating merge gate"},
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(gate, indent=2), "utf-8")
    _upsert_comment(gh, repo, number, report, gate, run_url, reason)
    _finalize_checks(gh, repo, head_sha, report, gate, run_url)
    print(f"STEP 15: PR #{number} verdict={verdict} status={state} labels={sorted(wanted)}")
    return 0


def _finalize_checks(gh: GitHub, repo: str, head_sha: str, report: dict | None, gate: dict, run_url: str | None) -> None:
    """Write each numbered check's exact outcome; never let a check-run hiccup fail the gate."""
    if report is None:
        return
    from txa.ci import checks

    try:
        checks.finalize(gh, repo, head_sha, report, gate, run_url)
    except GitHubError as exc:
        print(f"::warning::could not update the numbered checks ({exc})")


_BOX = re.compile(r"^[ \t]*[-*][ \t]+\[([ xX])\][ \t]+(.+?)[ \t]*$", re.M)


def checklist(body: str | None) -> list[str]:
    """Text of the required checklist boxes that are still unticked ('(if applicable)' ones are optional)."""
    return [text for mark, text in _BOX.findall(body or "") if mark == " " and "(if applicable)" not in text]


_PR_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      id isDraft reviewDecision headRefOid
      autoMergeRequest { enabledAt mergeMethod }
    }
  }
}"""

_LOCK_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef { name }
    branchProtectionRules(first: 20) {
      nodes { pattern requiresCodeOwnerReviews requiredApprovingReviewCount requiresApprovingReviews }
    }
  }
}"""

_ENABLE_AUTO_MERGE = "mutation($id: ID!) { enablePullRequestAutoMerge(input: {pullRequestId: $id, mergeMethod: SQUASH}) { clientMutationId } }"
_DISABLE_AUTO_MERGE = "mutation($id: ID!) { disablePullRequestAutoMerge(input: {pullRequestId: $id}) { clientMutationId } }"


def merge_gate(gh: GitHub, merger: GitHub, repo: str, state_path: Path, report_path: Path | None,
               run_url: str | None = None) -> int:
    """STEP 16 - arm squash auto-merge for verified PRs; the merge still waits for a code owner.

    ``gh`` (workflow token) edits the bot comment; ``merger`` (TXA_BOT_TOKEN when set) arms
    the merge, so the resulting push to main still triggers the API publish workflow.
    """
    if not state_path.is_file():
        print("::warning::no triage state - STEP 15 did not complete, merge gate stays closed")
        return 0
    gate = json.loads(state_path.read_text("utf-8"))
    owner, name = repo.split("/", 1)
    pr = merger.graphql(_PR_QUERY, {"owner": owner, "name": name, "number": gate["pr"]})["repository"]["pullRequest"]
    if pr["headRefOid"] != gate["head"]:
        print("::notice::a newer commit arrived - its own gate run decides")
        return 0

    lock = _lock_status(merger, owner, name)
    decision = pr["reviewDecision"]
    armed = pr["autoMergeRequest"] is not None
    if gate["eligible"]:
        if not armed:
            try:
                merger.graphql(_ENABLE_AUTO_MERGE, {"id": pr["id"]})
                armed = True
            except GitHubError as exc:
                if "clean status" in str(exc).lower() and decision == "APPROVED":
                    merger.put(f"repos/{repo}/pulls/{gate['pr']}/merge", json={"merge_method": "squash", "sha": gate["head"]})
                    print(f"STEP 16: PR #{gate['pr']} was already approved and green - squash-merged")
                    gate["step16"] = {"status": "PASS", "detail": "approved by a code owner - squash-merged"}
                    return _finish(gh, repo, gate, state_path, report_path, run_url)
                # report it in the comment instead of leaving "evaluating merge gate" on the PR forever
                reason = str(exc).replace("GitHub API 200: ", "")[:160]
                print(f"::warning::could not arm auto-merge: {reason}")
                gate["step16"] = {"status": "FAIL", "detail": f"could not arm auto-merge ({reason}) - "
                                  f"a moderator can still review and merge by hand · {lock}"}
                return _finish(gh, repo, gate, state_path, report_path, run_url)
        if decision == "APPROVED":
            detail = "auto-merge (squash) armed · code-owner approval present - merging"
        else:
            detail = "auto-merge (squash) armed · 🔒 waiting for ≥1 moderator (code owner) approval"
        if gate.get("confirm"):
            detail += " · ☑️ and for the contributor to tick the checklist"
        gate["step16"] = {"status": "PASS", "detail": f"{detail} · {lock}"}
    else:
        if armed:
            merger.graphql(_DISABLE_AUTO_MERGE, {"id": pr["id"]})
        why = "PR is a draft" if pr["isDraft"] else gate.get("why") or f"verdict '{gate['verdict']}'"
        if gate["verdict"] == "infra":
            why = "infrastructure change - admins merge manually"
        gate["step16"] = {"status": "SKIP" if gate["verdict"] in ("infra", "empty") else "FAIL",
                          "detail": f"auto-merge disabled ({why}) · {lock}"}
    print(f"STEP 16: PR #{gate['pr']} {gate['step16']['detail']}")
    return _finish(gh, repo, gate, state_path, report_path, run_url)


def _lock_status(gh: GitHub, owner: str, name: str) -> str:
    try:
        repo = gh.graphql(_LOCK_QUERY, {"owner": owner, "name": name})["repository"]
    except GitHubError:
        return "lock not readable with this token (admin token needed to audit it)"
    branch = (repo.get("defaultBranchRef") or {}).get("name", "main")
    for rule in repo["branchProtectionRules"]["nodes"]:
        if rule["pattern"] == branch:
            if rule["requiresCodeOwnerReviews"] and rule["requiredApprovingReviewCount"] >= 1:
                return f"lock OK ({rule['requiredApprovingReviewCount']} approval(s), code owners required)"
            return "::warning:: branch protection does not require code-owner review!"
    return "::warning:: default branch is not protected!"


def _finish(gh: GitHub, repo: str, gate: dict, state_path: Path, report_path: Path | None, run_url: str | None) -> int:
    state_path.write_text(json.dumps(gate, indent=2), "utf-8")
    report = load_report(report_path)
    _upsert_comment(gh, repo, gate["pr"], report, gate, run_url, "")
    _finalize_checks(gh, repo, gate["head"], report, gate, run_url)
    return 0


def _upsert_comment(gh: GitHub, repo: str, number: int, report: dict | None, gate: dict, run_url: str | None,
                    reason: str) -> None:
    if report is None:
        body = (f"{REPORT_MARKER}\n## 🤖 TXA Bot · Validation Report\n\n❌ The validation run did not produce a usable "
                f"report ({reason}). Push a new commit or ask a moderator to re-run the workflow.\n")
    else:
        body = render_markdown(report, gate, run_url)
    upsert_bot_comment(gh, repo, number, body)


# --------------------------------------------------------------------------- shared helpers


def upsert_bot_comment(gh: GitHub, repo: str, number: int, body: str) -> None:
    """Create the bot's single comment on a PR, or edit it in place (only comments written by a bot count)."""
    for comment in gh.paginate(f"repos/{repo}/issues/{number}/comments"):
        if comment["body"].startswith(REPORT_MARKER) and comment["user"]["type"] == "Bot":
            if comment["body"] != body:
                gh.patch(f"repos/{repo}/issues/comments/{comment['id']}", json={"body": body})
            return
    gh.post(f"repos/{repo}/issues/{number}/comments", json={"body": body})


def set_labels(gh: GitHub, repo: str, number: int, current: set[str], add: set[str], remove: set[str]) -> None:
    """Bring a PR's labels to ``current - remove + add`` with the fewest API calls."""
    for stale in sorted((current & remove) - add):
        gh.delete(f"repos/{repo}/issues/{number}/labels/{stale}", ok=(404,))
    missing = add - current
    if missing:
        gh.post(f"repos/{repo}/issues/{number}/labels", json={"labels": sorted(missing)})


def review_decision(gh: GitHub, repo: str, number: int) -> str | None:
    owner, name = repo.split("/", 1)
    try:
        pr = gh.graphql(_PR_QUERY, {"owner": owner, "name": name, "number": number})["repository"]["pullRequest"]
    except GitHubError:
        return None
    return pr["reviewDecision"]


# --------------------------------------------------------------------------- lifecycle (after the checks)

_MERGED_MARK = "<!-- txa-bot:merged -->"
_RELEASE_RE = re.compile(r"^manifests/[a-z0-9]/([A-Za-z0-9]+)/([A-Za-z0-9-]+)/(\d+\.\d+\.\d+)/")


def lifecycle(gh: GitHub, repo: str, *, pr_number: int | None = None, head_sha: str | None = None,
              closed: bool = False, merged: bool = False) -> int:
    """Keep the labels honest after validation: approved -> published, never 'awaiting review' forever.

    open PR     a code owner approved           ->  txa:awaiting-review becomes txa:approved
                changes were requested          ->  txa:fix-required
                approval withdrawn              ->  back to txa:awaiting-review
    merged PR   all in-flight labels go, txa:published is added, the bot comment says how to install
    closed PR   in-flight labels go
    """
    if pr_number is not None:
        prs = [gh.get(f"repos/{repo}/pulls/{pr_number}")]
    else:
        found = _find_pr(gh, repo, head_sha) if head_sha else None
        # a review event does not always carry the PR head, so fall back to refreshing every open PR
        prs = [found] if found else list(gh.paginate(f"repos/{repo}/pulls", {"state": "open"}))
    for pr in prs:
        _refresh(gh, repo, pr, closed, merged)
    return 0


def backfill(gh: GitHub, repo: str) -> int:
    """Fix PRs that were closed before the lifecycle existed (e.g. merged but still 'awaiting review').

    Equivalent of the search  is:pr state:closed label:txa:awaiting-review  - run it from the
    ``TXA Lifecycle`` workflow (manual dispatch) or locally with ``txaci gate lifecycle --backfill``.
    """
    stale = {_label(k) for k in ("validating", "review", "approved", "fix")}
    fixed = 0
    for pr in gh.paginate(f"repos/{repo}/pulls", {"state": "closed"}):
        labels = {label["name"] for label in pr.get("labels", [])}
        merged = bool(pr.get("merged_at"))
        needs_published = merged and _label("published") not in labels
        if labels & stale or needs_published:
            _refresh(gh, repo, pr, closed=True, merged=merged)
            fixed += 1
    print(f"lifecycle: backfilled {fixed} closed pull request(s)")
    return 0


def _refresh(gh: GitHub, repo: str, pr: dict, closed: bool, merged: bool) -> None:
    number = pr["number"]
    current = {label["name"] for label in pr.get("labels", [])}
    names = {key: _label(key) for key in ("validating", "verified", "fix", "review", "approved", "published", "confirm", "infra", "agree")}
    in_flight = {names[k] for k in ("validating", "fix", "review", "approved", "confirm", "agree")}
    if closed:
        add = {names["published"]} if merged else set()
        set_labels(gh, repo, number, current, add=add, remove=in_flight)
        _footer(gh, repo, pr, merged)
        print(f"lifecycle: PR #{number} {'merged' if merged else 'closed'}; labels {sorted((current - in_flight) | add)}")
        return
    if names["validating"] in current or not current & {names["verified"], names["review"], names["approved"]}:
        return  # still being validated (or never reached the gate): the gate owns the labels
    current = _sync_confirmation(gh, repo, pr, current, names)
    decision = review_decision(gh, repo, number)
    if decision == "APPROVED":
        add, remove = {names["approved"]}, {names["review"], names["fix"]}
    elif decision == "CHANGES_REQUESTED":
        add, remove = {names["fix"]}, {names["review"], names["approved"]}
    else:
        add, remove = {names["review"]}, {names["approved"]}
    set_labels(gh, repo, number, current, add=add, remove=remove)
    print(f"lifecycle: PR #{number} decision={decision} -> {sorted(add)}")


_CONFIRM_BLOCK = re.compile(r"<!-- txa-bot:confirm -->.*?<!-- /txa-bot:confirm -->", re.S)


def _sync_confirmation(gh: GitHub, repo: str, pr: dict, current: set[str], names: dict[str, str]) -> set[str]:
    """The contributor edited the description: move the label, the txa/verified status and the comment note."""
    number, sha = pr["number"], pr["head"]["sha"]
    open_boxes = checklist(pr.get("body"))
    has_label = names["confirm"] in current
    if bool(open_boxes) == has_label:
        return current  # nothing changed
    ok_text = ("No manifest changes - admin code-owner review required" if names["infra"] in current
               else "All automated TXA checks passed")
    if open_boxes:
        set_labels(gh, repo, number, current, add={names["confirm"]}, remove=set())
        state, description = "pending", f"Waiting for the contributor to tick {len(open_boxes)} checklist box(es)"
        current = current | {names["confirm"]}
    else:
        set_labels(gh, repo, number, current, add=set(), remove={names["confirm"]})
        state, description = "success", ok_text
        if names["agree"] in current:
            state, description = "pending", "Waiting for the contributor to accept the agreement"
        current = current - {names["confirm"]}
    gh.post(f"repos/{repo}/statuses/{sha}", json={"state": state, "context": STATUS_CONTEXT, "description": description})
    for comment in gh.paginate(f"repos/{repo}/issues/{number}/comments"):
        if comment["body"].startswith(REPORT_MARKER) and comment["user"]["type"] == "Bot":
            if _CONFIRM_BLOCK.search(comment["body"]):
                gh.patch(f"repos/{repo}/issues/comments/{comment['id']}",
                         json={"body": _CONFIRM_BLOCK.sub(lambda _: confirm_note(open_boxes), comment["body"])})
            break
    print(f"lifecycle: PR #{number} checklist {'incomplete' if open_boxes else 'confirmed'} -> status {state}")
    return current


def _footer(gh: GitHub, repo: str, pr: dict, merged: bool) -> None:
    """Tell the contributor what happened, in the bot's own comment."""
    number = pr["number"]
    releases = sorted({m.groups() for f in gh.paginate(f"repos/{repo}/pulls/{number}/files")
                       if (m := _RELEASE_RE.match(f["filename"]))})
    for comment in gh.paginate(f"repos/{repo}/issues/{number}/comments"):
        if comment["body"].startswith(REPORT_MARKER) and comment["user"]["type"] == "Bot":
            body = comment["body"].split("\n---\n" + _MERGED_MARK)[0].rstrip("\n")
            if merged and releases:
                lines = [f"`{ns}.{app} {ver}`" for ns, app, ver in releases]
                install = "\n".join(f"txapm install {ns}.{app}" for ns, app, _ in releases)
                note = (f"🎉 **Merged.** {', '.join(lines)} is in the repository. The package index refreshes within "
                        f"about two minutes; then anyone can run:\n\n```powershell\n{install}\n```")
            elif merged:
                note = "🎉 **Merged.**"
            else:
                note = "🚪 **Closed without merging.** Open a new pull request when the issues are fixed."
            gh.patch(f"repos/{repo}/issues/comments/{comment['id']}",
                     json={"body": f"{body}\n\n---\n{_MERGED_MARK}\n{note}\n"})
            return
