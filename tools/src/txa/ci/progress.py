"""Live progress of a pull request's validation - the ``TXA Progress`` workflow.

``TXA Validate`` runs on ``pull_request`` with a read-only token (it executes nothing from the
pull request but must stay unprivileged), so it cannot talk to the PR while it works. This watcher
runs on ``pull_request_target`` - trusted default-branch code with a write token - and *only reads
the Actions API*: it never checks out or runs anything from the pull request. In order, it

1. posts the checklist comment and the ``txa:validating`` label, plus the pipeline health badges;
2. creates the sixteen numbered checks as *queued* (the list in the merge box);
3. ticks the checks and the comment off as the Validate job reaches each step.

When the run completes the ``TXA Gate`` workflow writes the exact result of every check and
replaces the comment with the full report.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Callable

from txa.ci import checks as check_runs
from txa.ci.gate import set_labels, upsert_bot_comment
from txa.ci.report import badges
from txa.ci.steps import TITLES
from txa.constants import LABELS, REPORT_MARKER
from txa.core.github import GitHub, GitHubError

VALIDATE_WORKFLOW = "txa-validate.yml"
STEP_RE = re.compile(r"^STEP (\d{2}) · ")
VALIDATION_STEPS = 14
_IN_FLIGHT = ("verified", "review", "approved", "fix", "published", "confirm")  # stale after a new push
_RELEASE_RE = re.compile(r"^manifests/([a-z0-9])/([A-Za-z0-9]+)/([A-Za-z0-9-]+)/(\d+\.\d+\.\d+)/")
_STEP_STATUS = {"success": "PASS", "failure": "FAIL", "skipped": "SKIP", "cancelled": "SKIP"}


def _parse(stamp: str | None) -> datetime | None:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else None


def _seconds(start: str | None, end: str | None) -> float | None:
    a, b = _parse(start), _parse(end)
    return (b - a).total_seconds() if a and b else None


def human(seconds: float | None) -> str:
    if seconds is None:
        return ""
    seconds = int(seconds + 0.5)
    return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m{seconds % 60:02d}s"


# --------------------------------------------------------------------------- reading the Actions API


def find_validate_run(gh: GitHub, repo: str, head_sha: str) -> dict | None:
    data = gh.get(f"repos/{repo}/actions/workflows/{VALIDATE_WORKFLOW}/runs",
                  params={"head_sha": head_sha, "event": "pull_request", "per_page": 5})
    runs = [r for r in data.get("workflow_runs", []) if r.get("head_sha") == head_sha]
    return max(runs, key=lambda r: r["created_at"]) if runs else None


def read_steps(gh: GitHub, repo: str, run_id: int) -> tuple[dict[int, dict], list[dict]]:
    """({step number: API step}, setup steps) of the validation job."""
    jobs = gh.get(f"repos/{repo}/actions/runs/{run_id}/jobs").get("jobs", [])
    found: dict[int, dict] = {}
    setup: list[dict] = []
    if jobs:
        for step in jobs[0].get("steps", []):
            match = STEP_RE.match(step["name"])
            if match:
                found[int(match[1])] = step
            elif not step["name"].startswith("Post "):
                setup.append(step)
    return found, setup


def step_timings(gh: GitHub, repo: str, run_id: int) -> dict[int, float]:
    """Seconds each validation step took (for the final report)."""
    found, _ = read_steps(gh, repo, run_id)
    out = {}
    for number, step in found.items():
        took = _seconds(step.get("started_at"), step.get("completed_at"))
        if took is not None:
            out[number] = took
    return out


def pipeline_health(gh: GitHub, repo: str) -> dict[str, bool]:
    """Is the bot itself working? Looks at the latest finished runs of the gate and of the index publisher."""
    def healthy(workflow: str) -> bool:
        try:
            runs = gh.get(f"repos/{repo}/actions/workflows/{workflow}/runs",
                          params={"status": "completed", "per_page": 5}).get("workflow_runs", [])
        except GitHubError:
            return True
        decided = [r for r in runs if r.get("conclusion") in ("success", "failure", "timed_out")]
        return not decided or decided[0]["conclusion"] == "success"

    return {"Validation Pipeline": healthy("txa-gate.yml"), "Publish Pipeline": healthy("txa-publish-api.yml")}


def classify(gh: GitHub, repo: str, number: int, base: str) -> set[str]:
    """Labels that are known the moment the PR opens: new package / new version / edit / infra change."""
    files = [f["filename"] for f in gh.paginate(f"repos/{repo}/pulls/{number}/files")]
    labels: set[str] = set()
    if any(not f.startswith("manifests/") for f in files):
        labels.add(LABELS["infra"][0])
    for shard, namespace, app, version in sorted({m.groups() for f in files if (m := _RELEASE_RE.match(f))}):
        package = f"manifests/{shard}/{namespace}/{app}"
        if gh.request("GET", f"repos/{repo}/contents/{package}", ok=(404,), params={"ref": base}).status_code != 200:
            labels.add(LABELS["new_app"][0])
        elif gh.request("GET", f"repos/{repo}/contents/{package}/{version}", ok=(404,), params={"ref": base}).status_code == 200:
            labels.add(LABELS["update"][0])
        else:
            labels.add(LABELS["new_version"][0])
    return labels


# --------------------------------------------------------------------------- the checklist comment


def _icon(step: dict | None) -> tuple[str, str]:
    if step is None:
        return "⏳", "waiting"
    status, conclusion = step.get("status"), step.get("conclusion")
    if status == "completed":
        return {"success": ("✅", "done"), "failure": ("❌", "failed"), "skipped": ("⏭️", "skipped"),
                "cancelled": ("🚫", "cancelled")}.get(conclusion, ("✅", "done"))
    if status == "in_progress":
        return "🔄", "running…"
    return "⏳", "waiting"


def render_progress(run: dict | None, found: dict[int, dict], setup: list[dict], waited: float, head_sha: str,
                    health: dict[str, bool] | None = None) -> str:
    done = sum(1 for s in found.values() if s.get("status") == "completed")
    running = next((n for n, s in sorted(found.items()) if s.get("status") == "in_progress"), None)
    bar = "█" * done + "░" * (VALIDATION_STEPS - done)
    if run is None:
        state = f"⏳ Waiting for GitHub to start the validation run… ({human(waited)})"
    elif run.get("status") == "queued":
        state = f"⏳ Queued, waiting for a Windows runner… ({human(waited)})"
    elif run.get("status") == "completed":
        state = "📝 Validation finished - the final report is being written…"
    elif running is not None:
        state = f"🔄 Running **step {running:02d}** · {TITLES[running]}"
    elif not found or all(s.get("status") != "completed" for s in found.values()):
        prep = next((s for s in setup if s.get("status") == "in_progress"), None)
        detail = " · " + prep["name"] if prep else ""
        state = f"🛠️ Preparing the runner{detail}…"
    else:
        state = "🔄 Running…"
    lines = [REPORT_MARKER, badges(health), "", "## 🤖 TXA Bot · Validating…", "",
             f"`{bar}`  **{done}/{VALIDATION_STEPS}** checks done", "", state, "",
             "| # | Check | Status | Time |", "|---|---|---|---|"]
    for number, title in TITLES.items():
        if number > VALIDATION_STEPS:
            icon, label, took = "⏳", "after validation", ""
        else:
            step = found.get(number)
            icon, label = _icon(step)
            took = human(_seconds(step.get("started_at"), step.get("completed_at"))) if step else ""
        lines.append(f"| {number:02d} | {title} | {icon} {label} | {took} |")
    link = f"[Workflow run]({run['html_url']}) · " if run else ""
    lines += ["", f"<sub>{link}this comment and the checks below update live · head `{head_sha[:12]}`</sub>"]
    return "\n".join(line for line in lines if line is not None).replace("\n\n\n", "\n\n") + "\n"


# --------------------------------------------------------------------------- the watcher


def _sync_checks(gh: GitHub, repo: str, ids: dict[int, int], found: dict[int, dict], seen: dict[int, str],
                 run: dict | None) -> None:
    """Move the numbered checks to match the Validate job's steps; update only what changed."""
    for number, step in found.items():
        check_id = ids.get(number)
        if not check_id:
            continue
        if step.get("status") == "in_progress" and seen.get(number) != "running":
            check_runs.start(gh, repo, check_id)
            seen[number] = "running"
        elif step.get("status") == "completed" and seen.get(number) != "done":
            status = _STEP_STATUS.get(step.get("conclusion"), "PASS")
            took = human(_seconds(step.get("started_at"), step.get("completed_at")))
            check_runs.finish(gh, repo, check_id, status,
                              f"Finished in {took or 'a moment'} - the full findings appear when the whole validation completes.",
                              details_url=run.get("html_url") if run else None)
            seen[number] = "done"
    if run and run.get("status") == "completed" and ids.get(15) and seen.get(15) != "running":
        check_runs.start(gh, repo, ids[15], "Deciding labels and the merge gate…")
        seen[15] = "running"


def watch(gh: GitHub, repo: str, number: int, head_sha: str, *, timeout: float = 40 * 60,
          sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> int:
    """Post the checklist now, then keep it current until the validation run completes."""
    started = clock()
    pr = gh.get(f"repos/{repo}/pulls/{number}")
    from txa.ci import agreement

    if not agreement.is_signed(gh, repo, pr):
        print(f"::notice::PR #{number}: waiting for the Contributor Agreement - the checklist starts once it is accepted")
        return 0
    current = {label["name"] for label in pr.get("labels", [])}
    health = pipeline_health(gh, repo)
    try:
        early = classify(gh, repo, number, pr["base"]["ref"])
    except GitHubError as exc:
        print(f"::warning::could not classify the pull request ({exc})")
        early = set()
    set_labels(gh, repo, number, current, add={LABELS["validating"][0]} | early,
               remove={LABELS[key][0] for key in _IN_FLIGHT})
    last = render_progress(None, {}, [], 0, head_sha, health)
    upsert_bot_comment(gh, repo, number, last)          # visible within seconds of opening the PR
    ids = check_runs.create_all(gh, repo, head_sha)
    seen: dict[int, str] = {}
    polls = 0
    while clock() - started < timeout:
        if polls and polls % 6 == 0:
            pr = gh.get(f"repos/{repo}/pulls/{number}")
            if pr["state"] != "open" or pr["head"]["sha"] != head_sha:
                print("::notice::the pull request was closed or received a newer commit - stopping")
                return 0
        polls += 1
        try:
            run = find_validate_run(gh, repo, head_sha)
            found, setup = read_steps(gh, repo, run["id"]) if run else ({}, [])
            _sync_checks(gh, repo, ids, found, seen, run)
        except GitHubError as exc:
            print(f"::warning::could not read or update the validation run ({exc}) - retrying")
            sleep(10)
            continue
        body = render_progress(run, found, setup, clock() - started, head_sha, health)
        if body != last:
            upsert_bot_comment(gh, repo, number, body)
            last = body
        if run and run.get("status") == "completed":
            return 0
        # queued runs change rarely; a running one finishes a step every second or two
        sleep(3 if run and run.get("status") == "in_progress" else 10)
    print("::warning::gave up waiting for the validation run")
    return 0
