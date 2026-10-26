"""txaci - command line entry for the TXA validation pipeline.

CI usage (one GitHub Actions step per command)::

    txaci init --base <sha> --head <sha> --pr <n>
    txaci step 1 … txaci step 14
    txaci verdict
    txaci gate triage --report report.json --head-sha <sha>     # STEP 15
    txaci gate merge  --report report.json                       # STEP 16

Local usage::

    txaci validate manifests/t/TXAVL/HelloTool/1.4.2 [--offline]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from txa import __version__
from txa.ci import steps
from txa.ci.context import Context, init_ci, init_local
from txa.ci.report import build_report, render_console, render_markdown, run_live
from txa.core import console

LAST_VALIDATION_STEP = 14


def _escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _emit(number: int, results) -> None:
    in_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    for target, outcome in results:
        print(f"{console.paint(target['path'], 'bold', 'cyan')} → {console.status(outcome.status)}")
        for message in outcome.messages:
            level, text = message["level"], message["text"]
            shade = {"fail": ("red", "bold"), "warn": ("yellow",), "ok": ("green",), "skip": ("gray",)}.get(level, ("cyan",))
            print(f"  {console.paint('[' + level + ']', *shade)} {text}")
            if in_actions and level in ("fail", "warn"):
                kind = "error" if level == "fail" else "warning"
                print(f"::{kind} title=STEP {number:02d} · {steps.TITLES[number]}::{_escape(target['path'] + ': ' + text)}")


def cmd_init(args) -> int:
    pr = int(args.pr) if args.pr else None
    ctx = init_ci(Path(args.repo_root), args.base, args.head, pr, Path(args.state))
    print(f"txaci {__version__}: {len(ctx.targets)} release(s), {len(ctx.outside)} file(s) outside manifests/")
    for target in ctx.targets:
        print(f"  {target['mode']:<12} {target['path']}")
    for path in ctx.outside:
        print(f"  outside      {path}")
    return 0


def cmd_step(args) -> int:
    number = int(args.number)
    if number not in steps.REGISTRY:
        raise SystemExit(f"txaci: step {number} is not a validation step (1-{LAST_VALIDATION_STEP})")
    ctx = Context.load(Path(args.state))
    print(console.paint(f"STEP {number:02d} · {steps.TITLES[number]}", "bold"))
    if not ctx.targets:
        print("  no manifest releases in this change set - skipped")
        return 0
    results = steps.run_step(ctx, number)
    ctx.save()
    _emit(number, results)
    return 1 if any(o.status == "FAIL" for _, o in results) else 0


def cmd_verdict(args) -> int:
    ctx = Context.load(Path(args.state))
    report = build_report(ctx)
    path = Path(args.report) if args.report else Path(args.state) / "report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), "utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(render_markdown(report))
    print(render_console(report))
    print(f"report written to {path}")
    return 1 if report["verdict"] == "fail" else 0


def cmd_validate(args) -> int:
    ctx = init_local([Path(p) for p in args.paths], offline=args.offline)
    numbers = [int(n) for n in args.only.split(",")] if args.only else None
    report = run_live(ctx, numbers)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2), "utf-8")
    return 1 if report["verdict"] == "fail" else 0


def cmd_gate(args) -> int:
    from txa.ci import gate
    from txa.core.github import GitHub

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("txaci gate: GH_TOKEN is required")
    repo = args.repo or os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        raise SystemExit("txaci gate: --repo or GITHUB_REPOSITORY is required")
    state = Path(args.state) / "gate.json"
    report = Path(args.report) if args.report else None
    gh = GitHub(token)
    if args.action == "triage":
        if not args.head_sha:
            raise SystemExit("txaci gate triage: --head-sha is required")
        return gate.triage(gh, repo, report, args.head_sha, state, args.run_url)
    merger = GitHub(os.environ.get("TXA_MERGE_TOKEN") or token)
    return gate.merge_gate(gh, merger, repo, state, report, args.run_url)


def cmd_build_api(args) -> int:
    from txa.ci.api import build_api

    result = build_api(Path(args.root), Path(args.out))
    print(f"published {result['packages']} package(s) to {result['out']}")
    for item in result["skipped"]:
        print(f"::warning title=Skipped release::{item['path']}: {'; '.join(item['problems'])}")
    return 0


def cmd_schema(args) -> int:
    from txa.core.manifest import schema

    print(json.dumps(schema(args.kind), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    console.setup()
    parser = argparse.ArgumentParser(prog="txaci", description="TXA 16-step manifest validator",
        epilog='examples:\n  txaci validate manifests/t/TXAVL/HelloTool/1.4.2\n  txaci validate <folder> --offline\n  txaci schema build\n\ninit/step/verdict/gate are used by the GitHub bot.', formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"txaci {__version__}")
    parser.add_argument("--state", default=str(Context.default_state_dir()), help="pipeline state directory")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p = sub.add_parser("init", help="collect the change set of a pull request")
    p.add_argument("--base", required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--pr")
    p.add_argument("--repo-root", default=".")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("step", help="run one validation step (1-14)")
    p.add_argument("number")
    p.set_defaults(func=cmd_step)

    p = sub.add_parser("verdict", help="write report.json and fail when any step failed")
    p.add_argument("--report")
    p.set_defaults(func=cmd_verdict)

    p = sub.add_parser("validate", help="run steps 01-14 against release directories on disk")
    p.add_argument("paths", nargs="+")
    p.add_argument("--offline", action="store_true", help="skip network steps 06-09")
    p.add_argument("--only", help="comma separated step numbers, e.g. 1,2,3")
    p.add_argument("--json", help="also write the report to this file")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("gate", help="STEP 15 triage / STEP 16 merge gate (trusted workflow only)")
    p.add_argument("action", choices=["triage", "merge"])
    p.add_argument("--report")
    p.add_argument("--head-sha")
    p.add_argument("--repo")
    p.add_argument("--run-url")
    p.set_defaults(func=cmd_gate)

    p = sub.add_parser("build-api", help="generate the static txa.v1 JSON API")
    p.add_argument("--root", default=".")
    p.add_argument("--out", default="site")
    p.set_defaults(func=cmd_build_api)

    p = sub.add_parser("schema", help="print the JSON schema of a card kind")
    p.add_argument("kind", choices=["core", "build", "about"])
    p.set_defaults(func=cmd_schema)

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
