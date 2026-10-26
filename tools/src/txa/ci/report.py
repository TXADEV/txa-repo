"""Machine-readable report (report.json) and its Markdown rendering."""
from __future__ import annotations

from datetime import datetime, timezone

from txa import __version__
from txa.ci.context import Context
from txa.ci.steps import TITLES
from txa.constants import REPORT_MARKER
from txa.core import console

ICONS = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "SKIP": "⏭️", "PENDING": "⏳"}
_ORDER = {"fail": 0, "warn": 1, "ok": 2, "info": 3, "skip": 4}


def build_report(ctx: Context) -> dict:
    results = ctx.results()
    statuses = {r["status"] for r in results}
    if not ctx.targets:
        verdict = "infra" if ctx.outside else "empty"
    elif "FAIL" in statuses:
        verdict = "fail"
    else:
        verdict = "pass"
    artifacts = {}
    for key, art in (ctx.data.get("artifacts") or {}).items():
        probe = art.get("probe") or {}
        artifacts[key] = {
            "sha256": art.get("sha256"),
            "bytes": art.get("bytes"),
            "final_url": probe.get("final_url"),
            "signature": art.get("signature"),
            "inspection": {k: v for k, v in (art.get("inspection") or {}).items() if k in ("label", "machine", "engine", "payload_arch")},
        }
    return {
        "schema": "txa.report.v1",
        "tool": f"txaci {__version__}",
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pr": ctx.data.get("pr"),
        "base": ctx.data.get("base"),
        "head": ctx.data.get("head"),
        "outside": ctx.outside,
        "targets": ctx.targets,
        "results": results,
        "artifacts": artifacts,
        "verdict": verdict,
    }


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict, gate: dict | None = None, run_url: str | None = None) -> str:
    lines = [REPORT_MARKER, "## 🤖 TXA Bot · Validation Report", ""]
    verdict = report.get("verdict")
    if verdict == "infra":
        lines += ["This pull request changes no manifests. It touches:", ""]
        lines += [f"- `{p}`" for p in report.get("outside", [])[:30]]
        lines += ["", "> Infrastructure changes skip the automated checks and need an **admin** code-owner review."]
    for target in report.get("targets", []):
        rows = [r for r in report.get("results", []) if r["target"] == target["path"]]
        by_step = {r["step"]: r for r in rows}
        lines += [f"### 📦 `{target['path']}`", "", f"Mode: **{target['mode']}**", "",
                  "| # | Check | Result | Details |", "|---|---|---|---|"]
        for number, title in TITLES.items():
            row = by_step.get(number)
            if number >= 15:
                status = (gate or {}).get(f"step{number}", {}).get("status", "PENDING")
                detail = (gate or {}).get(f"step{number}", {}).get("detail", "runs in the trusted TXA Gate workflow")
            elif row is None:
                status, detail = "SKIP", "not run"
            else:
                status = row["status"]
                messages = sorted(row["messages"], key=lambda m: _ORDER.get(m["level"], 9))
                detail = "<br>".join(_cell(m["text"]) for m in messages[:3])
                if len(messages) > 3:
                    detail += f"<br>… +{len(messages) - 3} more"
            lines.append(f"| {number:02d} | {title} | {ICONS.get(status, status)} {status} | {detail} |")
        lines.append("")
        verbose = [r for r in rows if len(r["messages"]) > 3]
        if verbose:
            lines += ["<details><summary>Full findings</summary>", ""]
            for row in verbose:
                lines.append(f"**STEP {row['step']:02d} · {TITLES[row['step']]}**")
                lines += [f"- `{m['level']}` {m['text']}" for m in row["messages"]]
                lines.append("")
            lines += ["</details>", ""]
    if report.get("targets") and report.get("outside"):
        lines += ["> ❌ Manifest changes are mixed with files outside `manifests/` - split them into separate PRs.", ""]
    summary = {
        "pass": "✅ **All automated checks passed.**",
        "fail": "❌ **Fix required** - see the failing steps above, push a new commit and the bot re-runs.",
        "infra": "🛠️ **Infrastructure change** - admin review required.",
        "empty": "ℹ️ Nothing to validate.",
    }.get(verdict, verdict)
    lines.append(summary)
    if gate and gate.get("labels"):
        lines.append("")
        lines.append("Labels: " + " ".join(f"`{label}`" for label in gate["labels"]))
    if run_url:
        lines += ["", f"<sub>[Workflow run]({run_url}) · {report.get('tool')} · head `{(report.get('head') or '')[:12]}`</sub>"]
    return "\n".join(lines) + "\n"


def run_live(ctx: Context, numbers: list[int] | None = None) -> dict:
    """Run the validation steps, showing each one as it starts and finishes.

    On a terminal the running step shows a spinner with its elapsed time; the finished step then
    prints its status and findings. Returns the report (same as ``build_report``).
    """
    from txa.ci import steps

    paint = console.paint
    numbers = numbers or sorted(steps.REGISTRY)
    count = len(steps.REGISTRY)
    level_style = {"fail": ("red", "bold"), "warn": ("yellow",), "ok": ("green",), "info": ("cyan",), "skip": ("gray",)}
    for target in ctx.targets:
        print(f"\n{paint(target['path'], 'bold', 'cyan')}  {paint('[' + target['mode'] + ']', 'magenta')}")
    for number in numbers:
        tag = f"  {paint('STEP', 'gray')} {paint(f'{number:02d}', 'bold')}{paint(f'/{count}', 'gray')} "
        with console.working(TITLES[number], indent=f"  STEP {number:02d}/{count} ") as busy:
            results = steps.run_step(ctx, number)
        for target, outcome in results:
            took = paint(f"{busy.elapsed:.1f}s", "gray") if busy.elapsed >= 0.1 else ""
            print(f"{tag}{console.status(outcome.status)}  {TITLES[number]}  {took}".rstrip())
            for message in sorted(outcome.messages, key=lambda m: _ORDER.get(m["level"], 9)):
                level, text = message["level"], message["text"]
                if level in ("fail", "warn") or outcome.status == "PASS":
                    label = paint(level.ljust(4), *level_style.get(level, ()))
                    print(f"           {label}  {paint(text, 'red') if level == 'fail' else text}")
    report = build_report(ctx)
    verdict = report["verdict"]
    shade = {"pass": ("green", "bold"), "fail": ("red", "bold")}.get(verdict, ("yellow", "bold"))
    print(f"\nverdict: {paint(verdict.upper(), *shade)}")
    return report


def render_console(report: dict) -> str:
    level_style = {"fail": ("red", "bold"), "warn": ("yellow",), "ok": ("green",), "info": ("cyan",), "skip": ("gray",)}
    paint = console.paint
    out = []
    for target in report.get("targets", []):
        out.append(f"\n{paint(target['path'], 'bold', 'cyan')}  {paint('[' + target['mode'] + ']', 'magenta')}")
        for row in (r for r in report["results"] if r["target"] == target["path"]):
            number = paint(f"{row['step']:02d}", "bold")
            out.append(f"  {paint('STEP', 'gray')} {number} {console.status(row['status'])}  {TITLES[row['step']]}")
            for message in sorted(row["messages"], key=lambda m: _ORDER.get(m["level"], 9)):
                if message["level"] in ("fail", "warn") or row["status"] == "PASS":
                    level, text = message["level"], message["text"]
                    tag = paint(level.ljust(4), *level_style.get(level, ()))
                    out.append(f"           {tag}  {paint(text, 'red') if level == 'fail' else text}")
    verdict = report["verdict"]
    shade = {"pass": ("green", "bold"), "fail": ("red", "bold")}.get(verdict, ("yellow", "bold"))
    out.append(f"\nverdict: {paint(verdict.upper(), *shade)}")
    return "\n".join(out)
