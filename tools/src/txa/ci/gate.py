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
from pathlib import Path

from txa.ci.report import render_markdown
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
           run_url: str | None = None) -> int:
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

    if verdict == "pass" and not mixed:
        wanted.add(_label("review"))
        if not removal_only:
            wanted.add(_label("verified"))
        state, description = "success", (
            "Removal - waiting for moderator" if removal_only else "All automated TXA checks passed")
    elif verdict == "infra":
        wanted.add(_label("review"))
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

    current = {label["name"] for label in pr.get("labels", [])}
    for stale in sorted((current & MANAGED) - wanted):
        gh.delete(f"repos/{repo}/issues/{number}/labels/{stale}", ok=(404,))
    if wanted - current:
        gh.post(f"repos/{repo}/issues/{number}/labels", json={"labels": sorted(wanted - current)})

    gh.post(f"repos/{repo}/statuses/{head_sha}", json={
        "state": state, "context": STATUS_CONTEXT, "description": description[:140],
        **({"target_url": run_url} if run_url else {}),
    })

    eligible = state == "success" and verdict == "pass" and not outside and not removal_only and not pr.get("draft")
    gate = {
        "pr": number, "head": head_sha, "verdict": verdict, "eligible": eligible, "labels": sorted(wanted),
        "why": description,
        "step15": {"status": "PASS" if state == "success" else "FAIL",
                   "detail": f"labels: {', '.join(sorted(wanted)) or '-'} · status {STATUS_CONTEXT}={state}"},
        "step16": {"status": "PENDING", "detail": "evaluating merge gate"},
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(gate, indent=2), "utf-8")
    _upsert_comment(gh, repo, number, report, gate, run_url, reason)
    print(f"STEP 15: PR #{number} verdict={verdict} status={state} labels={sorted(wanted)}")
    return 0


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
                raise
        if decision == "APPROVED":
            detail = "auto-merge (squash) armed · code-owner approval present - merging"
        else:
            detail = "auto-merge (squash) armed · 🔒 waiting for ≥1 moderator (code owner) approval"
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
    _upsert_comment(gh, repo, gate["pr"], load_report(report_path), gate, run_url, "")
    return 0


def _upsert_comment(gh: GitHub, repo: str, number: int, report: dict | None, gate: dict, run_url: str | None,
                    reason: str) -> None:
    if report is None:
        body = (f"{REPORT_MARKER}\n## 🤖 TXA Bot · Validation Report\n\n❌ The validation run did not produce a usable "
                f"report ({reason}). Push a new commit or ask a moderator to re-run the workflow.\n")
    else:
        body = render_markdown(report, gate, run_url)
    for comment in gh.paginate(f"repos/{repo}/issues/{number}/comments"):
        if comment["body"].startswith(REPORT_MARKER) and comment["user"]["type"] == "Bot":
            gh.patch(f"repos/{repo}/issues/comments/{comment['id']}", json={"body": body})
            return
    gh.post(f"repos/{repo}/issues/{number}/comments", json={"body": body})
