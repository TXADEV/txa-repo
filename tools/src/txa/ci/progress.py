"""Live progress of a pull request's validation - the ``TXA Progress`` workflow.

``TXA Validate`` runs on ``pull_request`` with a read-only token (it executes nothing from the
pull request but must stay unprivileged), so it cannot talk to the PR while it works. This watcher
runs on ``pull_request_target`` - trusted default-branch code with a write token - and *only reads
the Actions API*: it never checks out or runs anything from the pull request. It posts the
checklist the moment the PR is opened and ticks the steps off as the Validate job reaches them.
When the run completes the ``TXA Gate`` workflow replaces the comment with the full report.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Callable

from txa.ci.gate import set_labels, upsert_bot_comment
from txa.ci.steps import TITLES
from txa.constants import LABELS, REPORT_MARKER
from txa.core.github import GitHub, GitHubError

VALIDATE_WORKFLOW = "txa-validate.yml"
STEP_RE = re.compile(r"^STEP (\d{2}) · ")
VALIDATION_STEPS = 14
_IN_FLIGHT = ("verified", "review", "approved", "fix", "published")  # stale after a new push


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


def find_validate_run(gh: GitHub, repo: str, head_sha: str) -> dict | None:
    data = gh.get(f"repos/{repo}/actions/workflows/{VALIDATE_WORKFLOW}/runs",
                  params={"head_sha": head_sha, "event": "pull_request", "per_page": 5})
    runs = [r for r in data.get("workflow_runs", []) if r.get("head_sha") == head_sha]
    return max(runs, key=lambda r: r["created_at"]) if runs else None


def read_steps(gh: GitHub, repo: str, run_id: int) -> tuple[dict[int, dict], list[dict]]:
    """({step number: API step}, setup steps) of the validation job."""
    jobs = gh.get(f"repos/{repo}/actions/runs/{run_id}/jobs").get("jobs", [])
    checks: dict[int, dict] = {}
    setup: list[dict] = []
    if jobs:
        for step in jobs[0].get("steps", []):
            match = STEP_RE.match(step["name"])
            if match:
                checks[int(match[1])] = step
            elif not step["name"].startswith("Post "):
                setup.append(step)
    return checks, setup


def step_timings(gh: GitHub, repo: str, run_id: int) -> dict[int, float]:
    """Seconds each validation step took (for the final report)."""
    checks, _ = read_steps(gh, repo, run_id)
    out = {}
    for number, step in checks.items():
        took = _seconds(step.get("started_at"), step.get("completed_at"))
        if took is not None:
            out[number] = took
    return out


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


def render_progress(run: dict | None, checks: dict[int, dict], setup: list[dict], waited: float,
                    head_sha: str) -> str:
    done = sum(1 for s in checks.values() if s.get("status") == "completed")
    running = next((n for n, s in sorted(checks.items()) if s.get("status") == "in_progress"), None)
    bar = "█" * done + "░" * (VALIDATION_STEPS - done)
    if run is None:
        state = f"⏳ Waiting for GitHub to start the validation run… ({human(waited)})"
    elif run.get("status") == "queued":
        state = f"⏳ Queued, waiting for a Windows runner… ({human(waited)})"
    elif run.get("status") == "completed":
        state = "📝 Validation finished - the final report is being written…"
    elif running is not None:
        state = f"🔄 Running **step {running:02d}** · {TITLES[running]}"
    elif not checks or all(s.get("status") != "completed" for s in checks.values()):
        prep = next((s for s in setup if s.get("status") == "in_progress"), None)
        detail = " · " + prep["name"] if prep else ""
        state = f"🛠️ Preparing the runner{detail}…"
    else:
        state = "🔄 Running…"
    lines = [REPORT_MARKER, "## 🤖 TXA Bot · Validating…", "",
             f"`{bar}`  **{done}/{VALIDATION_STEPS}** checks done", "", state, "",
             "| # | Check | Status | Time |", "|---|---|---|---|"]
    for number, title in TITLES.items():
        if number > VALIDATION_STEPS:
            icon, label, took = "⏳", "after validation", ""
        else:
            step = checks.get(number)
            icon, label = _icon(step)
            took = human(_seconds(step.get("started_at"), step.get("completed_at"))) if step else ""
        lines.append(f"| {number:02d} | {title} | {icon} {label} | {took} |")
    link = f"[Workflow run]({run['html_url']}) · " if run else ""
    lines += ["", f"<sub>{link}this comment updates live · head `{head_sha[:12]}`</sub>"]
    return "\n".join(lines) + "\n"


def watch(gh: GitHub, repo: str, number: int, head_sha: str, *, timeout: float = 40 * 60,
          sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> int:
    """Post the checklist now, then keep it current until the validation run completes."""
    started = clock()
    pr = gh.get(f"repos/{repo}/pulls/{number}")
    current = {label["name"] for label in pr.get("labels", [])}
    set_labels(gh, repo, number, current, add={LABELS["validating"][0]},
               remove={LABELS[key][0] for key in _IN_FLIGHT})
    last = None
    polls = 0
    while clock() - started < timeout:
        if polls % 6 == 0 and polls:
            pr = gh.get(f"repos/{repo}/pulls/{number}")
            if pr["state"] != "open" or pr["head"]["sha"] != head_sha:
                print("::notice::the pull request was closed or received a newer commit - stopping")
                return 0
        polls += 1
        try:
            run = find_validate_run(gh, repo, head_sha)
            checks, setup = read_steps(gh, repo, run["id"]) if run else ({}, [])
        except GitHubError as exc:
            print(f"::warning::could not read the validation run ({exc}) - retrying")
            sleep(10)
            continue
        body = render_progress(run, checks, setup, clock() - started, head_sha)
        if body != last:
            upsert_bot_comment(gh, repo, number, body)
            last = body
        if run and run.get("status") == "completed":
            return 0
        # queued runs change rarely; a running one finishes a step every second or two
        sleep(3 if run and run.get("status") == "in_progress" else 10)
    print("::warning::gave up waiting for the validation run")
    return 0
