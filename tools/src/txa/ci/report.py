"""Machine-readable report (report.json) and its Markdown rendering."""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

from txa import __version__
from txa.ci.context import Context
from txa.ci.steps import TITLES
from txa.constants import LABELS, REPORT_MARKER
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


def _short(text: str, limit: int = 150) -> str:
    text = _cell(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _took(timings: dict | None, number: int) -> str:
    seconds = (timings or {}).get(str(number))
    if seconds is None:
        return ""
    return f"{seconds:.1f}s" if seconds < 60 else f"{int(seconds) // 60}m{int(seconds) % 60:02d}s"


_ENGINE_LABELS = {
    "inno": "Inno Setup", "nsis": "NSIS (Nullsoft)", "msi": "Windows Installer (.msi)", "wix": "WiX Burn bundle",
    "exe": "generic .exe", "portable": "portable ZIP",
}


def _installer_rows(report: dict, target: dict) -> list[str]:
    """One table row per architecture: what the file is, how big, its hash and signature."""
    from urllib.parse import urlsplit

    rows = []
    for key, art in sorted((report.get("artifacts") or {}).items()):
        path, _, arch = key.partition("#")
        if path != target["path"] or not art.get("sha256"):
            continue
        inspection = art.get("inspection") or {}
        signature = art.get("signature") or {}
        if inspection.get("engine") == "portable":
            sign = "n/a (archive)"
        elif signature.get("status") == "valid":
            sign = f"✅ {_cell(str(signature.get('signer') or 'signed'))}"
        elif signature.get("status") == "unsigned":
            sign = "⚠️ unsigned"
        else:
            sign = signature.get("status", "n/a")
        engine = inspection.get("engine") or "?"
        size = f"{art['bytes'] / 1048576:.2f} MiB" if art.get("bytes") else "?"
        host = urlsplit(art.get("final_url") or "").hostname or ""
        rows.append(f"| `{arch}` | {_ENGINE_LABELS.get(engine, engine)} (`{engine}`) | {size} | {sign} | "
                    f"`{art['sha256'][:16]}…` | {host} |")
    return rows


def badges(health: dict[str, bool] | None) -> str:
    """Pipeline health badges, shown at the top of the bot's comment."""
    if not health:
        return ""
    out = []
    for label, ok in health.items():
        value, color = ("Healthy", "brightgreen") if ok else ("Degraded", "orange")
        out.append(f"![{label}](https://img.shields.io/badge/{quote(label)}-{value}-{color}?labelColor=555555)")
    return " ".join(out)


def confirm_note(open_boxes: list[str]) -> str:
    """The block asking the contributor to tick the checklist (the bot rewrites it when they do)."""
    if open_boxes:
        listing = "\n".join(f"> - [ ] {text}" for text in open_boxes[:10])
        inner = (f"> ☑️ **Please tick every box of the checklist in the pull request description.** "
                 f"Nothing can be merged until you do ({len(open_boxes)} still open):\n>\n{listing}")
    else:
        inner = "> ✅ The contributor confirmed the checklist."
    return f"<!-- txa-bot:confirm -->\n{inner}\n<!-- /txa-bot:confirm -->"


WAIT_FOR_MODERATOR = ("> ℹ️ **The check-in policy requires a moderator (code owner) to approve pull requests from the "
                      "community.** TXA moderators are volunteers - please be patient and allow them enough time to "
                      "review your submission. Nothing is published before they approve.")


def render_markdown(report: dict, gate: dict | None = None, run_url: str | None = None) -> str:
    timings = (gate or {}).get("timings")
    verdict = report.get("verdict")
    headline = {
        "pass": "✅ All automated checks passed",
        "fail": "❌ Fix required",
        "infra": "🛠️ Infrastructure change",
        "empty": "ℹ️ Nothing to validate",
    }.get(verdict, str(verdict))
    lines = [REPORT_MARKER]
    if (gate or {}).get("health"):
        lines += [badges(gate["health"]), ""]
    lines += ["## 🤖 TXA Bot · Validation Report", "", f"**{headline}**", ""]
    if verdict == "infra":
        lines += ["This pull request changes no manifests. It touches:", ""]
        lines += [f"- `{p}`" for p in report.get("outside", [])[:30]]
        lines += ["", "> Infrastructure changes skip the automated checks and need an **admin** code-owner review.", ""]
    for target in report.get("targets", []):
        rows = [r for r in report.get("results", []) if r["target"] == target["path"]]
        by_step = {r["step"]: r for r in rows}
        lines += [f"### 📦 `{target['path']}`  ·  {target['mode']}", ""]
        installers = _installer_rows(report, target)
        if installers:
            lines += ["| CPU | Installer type | Size | Signature | SHA-256 | Downloaded from |", "|---|---|---|---|---|---|", *installers, ""]
        lines += ["| # | Check | Result | Time | Summary |", "|---|---|---|---|---|"]
        for number, title in TITLES.items():
            row = by_step.get(number)
            if number >= 15:
                status = (gate or {}).get(f"step{number}", {}).get("status", "PENDING")
                summary = _short((gate or {}).get(f"step{number}", {}).get("detail", "runs in the trusted TXA Gate workflow"), 220)
            elif row is None:
                status, summary = "SKIP", "not run"
            else:
                status = row["status"]
                messages = sorted(row["messages"], key=lambda m: _ORDER.get(m["level"], 9))
                summary = _short(messages[0]["text"]) if messages else ""
                if len(messages) > 1:
                    summary += f" _(+{len(messages) - 1} more below)_"
            lines.append(f"| {number:02d} | {title} | {ICONS.get(status, status)} {status} | {_took(timings, number)} | {summary} |")
        lines.append("")
        lines += ["<details><summary>📋 Full output of every check</summary>", ""]
        for number in range(1, 15):
            row = by_step.get(number)
            if row is None:
                continue
            took = _took(timings, number)
            lines.append(f"**STEP {number:02d} · {TITLES[number]}** — {ICONS.get(row['status'], '')} {row['status']}"
                         f"{' · ' + took if took else ''}")
            ordered = sorted(row["messages"], key=lambda m: _ORDER.get(m["level"], 9))
            lines += [f"- `{m['level']}` {m['text']}" for m in ordered] or ["- _nothing to report_"]
            lines.append("")
        lines += ["</details>", ""]
    if report.get("targets") and report.get("outside"):
        lines += ["> ❌ Manifest changes are mixed with files outside `manifests/` - split them into separate PRs.", ""]
    if verdict == "fail":
        lines += ["Fix the failing checks, push a new commit and the bot starts over automatically.", ""]
    labels = set((gate or {}).get("labels", []))
    if gate and gate.get("confirm"):
        lines += [confirm_note(gate["confirm"]), ""]
    if verdict in ("pass", "infra") and LABELS["approved"][0] in labels:
        lines += ["> ✅ **A code owner approved this pull request** - it merges as soon as every check is green.", ""]
    if gate and gate.get("labels"):
        lines.append("Labels: " + " ".join(f"`{label}`" for label in gate["labels"]))
    if run_url:
        lines += ["", f"<sub>[Workflow run]({run_url}) · {report.get('tool')} · head `{(report.get('head') or '')[:12]}`</sub>"]
    return "\n".join(lines) + "\n"


QUICK_STEPS = (1, 2, 3, 4, 5, 14)  # everything that only needs the YAML cards, no network


def quick_check(ctx: Context, quiet: bool = False) -> dict:
    """Fast offline sanity check of the cards. Silent apart from one line (or the problems found).

    The repository bot runs the full 14 steps on the pull request, so this only catches the
    mistakes that would make that run fail for a trivial reason (bad YAML, unknown license, ...).
    """
    from txa.ci import steps

    for number in QUICK_STEPS:
        steps.run_step(ctx, number)
    report = build_report(ctx)
    problems = [(r["step"], m["text"]) for r in report["results"] if r["status"] == "FAIL"
                for m in r["messages"] if m["level"] == "fail"]
    if not quiet:
        for number, text in problems:
            console.say(f"✗ STEP {number:02d} {TITLES[number]}: {text}")
        if not problems:
            console.say("✓ cards look valid (encoding, YAML, naming, version, license, switches)")
    return report


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
