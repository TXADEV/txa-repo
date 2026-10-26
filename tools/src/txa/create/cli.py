"""txacreate - publish installers to txa-repo through a pull request.

    txacreate login                        GitHub Device Code Flow (browser + one-time code)
    txacreate login --with-token < pat.txt Personal Access Token from stdin
    txacreate new <url> [<url> ...]        analyse, write cards, pre-flight, fork, branch, commit, PR
    txacreate new <url> --dry-run          only write the cards to ./txa-out
    txacreate update <id> <url> [...]      new version of an existing package, metadata carried over
    txacreate hash <file|url>              SHA-256 + engine + architecture of an installer
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import webbrowser
from pathlib import Path

from txa import __version__
from txa.ci import steps
from txa.ci.context import init_local
from txa.ci.report import build_report, render_console
from txa.core import config, console, credentials, engines, net, semver, winos
from txa.core.github import GitHub, GitHubError, device_flow
from txa.core.manifest import ManifestError, split_id
from txa.create.draft import Draft, TargetDraft, guess_arch, name_from_filename, pascal

MAX_DOWNLOAD = 4 * 1024 * 1024 * 1024


def _die(message: str) -> int:
    print(f"txacreate: {message}", file=sys.stderr)
    return 1


# --------------------------------------------------------------------------- auth


def cmd_login(args) -> int:
    if args.token or args.with_token:
        token = (args.token or sys.stdin.read()).strip()
        source = "personal access token"
    else:
        def show(code: dict) -> None:
            print(f"\n  1. Open   {code['verification_uri']}")
            print(f"  2. Enter  {code['user_code']}\n")
            if not args.no_browser:
                webbrowser.open(code["verification_uri"])
            print("  Waiting for you to authorize TXA in the browser…")
        token = device_flow(config.get("client_id"), args.scope, show)
        source = "device flow"
    response = GitHub(token).request("GET", "user")
    login = response.json()["login"]
    scopes = response.headers.get("X-OAuth-Scopes")
    if scopes is not None and not {"repo", "public_repo"} & {s.strip() for s in scopes.split(",")}:
        print("  ! token has no repo/public_repo scope - forking and opening PRs will fail")
    path = credentials.save_token(token, login, source)
    print(f"✓ logged in as {login} via {source}; credential saved to {path}")
    return 0


def cmd_logout(_args) -> int:
    print("✓ credential removed" if credentials.clear_token() else "no stored credential")
    return 0


def cmd_whoami(_args) -> int:
    token, source = credentials.load_token()
    if not token:
        return _die("not logged in - run `txacreate login`")
    print(f"{GitHub(token).get('user')['login']} (token from {source}) → upstream {config.get('upstream')}")
    return 0


def cmd_config(args) -> int:
    cfg = config.load_config()
    if args.action == "list":
        for key in config.keys():
            print(f"{key:<10} {config.get(key) or '-'}")
        print(f"{'api_base':<10} {config.api_base()}  (derived)")
    elif args.action == "get":
        print(config.get(args.key))
    elif args.action == "set":
        config.get(args.key)  # validates the key name
        cfg[args.key] = args.value
        config.save_config(cfg)
        print(f"✓ {args.key} = {args.value}")
    elif args.action == "unset":
        cfg.pop(args.key, None)
        config.save_config(cfg)
        print(f"✓ {args.key} reset to default")
    return 0


# --------------------------------------------------------------------------- new


def _ask_until(prompt: str, default: str | None, check=None) -> str:
    while True:
        value = console.ask(prompt, default)
        problem = check(value) if check else (None if value else "required")
        if not problem:
            return value
        print(f"    ✗ {problem}")


def _per_url(value: str | None, count: int) -> list[str | None]:
    if not value:
        return [None] * count
    items = [v.strip() or None for v in value.split(",")]
    if len(items) != count:
        raise SystemExit(f"txacreate: expected {count} comma-separated value(s), got '{value}'")
    return items


def cmd_new(args) -> int:
    upstream = config.get("upstream")
    namespace = config.get("namespace")
    interactive = sys.stdin.isatty() and not args.yes
    gh = login = None
    token = None
    if not args.dry_run:
        token, source = credentials.load_token()
        if not token:
            return _die("not logged in - run `txacreate login` (or set TXA_TOKEN)")
        gh = GitHub(token)
        login = gh.get("user")["login"]
        print(f"→ authenticated as {login} ({source}), upstream {upstream}")

    # 1. download + analyse every installer
    work = Path(tempfile.mkdtemp(prefix="create-", dir=config.temp_root()))
    session = net.session()
    arches, engine_overrides = _per_url(args.arch, len(args.urls)), _per_url(args.engine, len(args.urls))
    analysed = []
    for index, url in enumerate(args.urls):
        print(f"→ downloading {url}")
        try:
            dl = net.download(session, url, work / f"payload{index}", MAX_DOWNLOAD,
                              on_progress=console.progress("download"))
        except net.NetError as exc:
            console.done_line()
            return _die(str(exc))
        console.done_line()
        found = engines.inspect(dl.path)
        arch = arches[index] or guess_arch(dl.path.name, found)
        engine = engine_overrides[index] or found.engine
        if not engine:
            return _die(f"{dl.path.name}: unsupported payload ({found.label}); pass --engine")
        print(f"  {dl.path.name}: {dl.bytes:,} bytes · SHA-256 {dl.sha256}")
        print(f"  {found.label} · installer CPU {found.machine or '-'} → arch {arch}, engine {engine}")
        analysed.append((url, dl, found, arch, engine))
    seen = [a[3] for a in analysed]
    if len(set(seen)) != len(seen):
        return _die(f"two installers resolve to the same architecture ({', '.join(seen)}) - use --arch x64,x86,…")
    _, first_dl, first, _, _ = analysed[0]

    # 2. identity + carry-over from the newest published release
    raw_name = args.name or first.product_name or name_from_filename(first_dl.path.name)
    package_id = args.id or f"{namespace}.{pascal(raw_name)}"
    try:
        split_id(package_id)
    except ManifestError as exc:
        return _die(str(exc))
    previous: dict = {}
    published: list[str] = []
    try:
        from txa.pm.source import Source

        src = Source(token=token or "")
        latest, previous = src.release(package_id)
        published = src.versions(package_id)
        print(f"→ {package_id} already published (latest {latest}) - reusing its metadata")
    except Exception:
        if getattr(args, "update", False):
            return _die(f"{package_id} is not published yet - use `txacreate new`")
        print(f"→ {package_id} is a new package")
    prev_core = previous.get("core") or {}
    prev_build = previous.get("build") or {}
    prev_lang = (prev_core.get("locale") or {}).get("default")
    prev_about = (previous.get("about") or {}).get(prev_lang or "", {})
    prev_license = prev_about.get("license") or {}

    version = args.version or semver.coerce(first.product_version) or semver.coerce(first_dl.path.name) \
        or semver.coerce(args.urls[0])
    draft = Draft(
        package_id=package_id,
        version=version or "",
        lang=args.lang or prev_lang or "en-US",
        channel=args.channel or (prev_core.get("package") or {}).get("channel", "stable"),
        alias=args.alias or (prev_core.get("package") or {}).get("alias"),
        tags=[t.strip() for t in args.tags.split(",")] if args.tags else prev_core.get("tags", []),
        min_build=args.min_build or (prev_build.get("platform") or {}).get("min_build") or "10.0.17763",
        scope=args.scope or prev_build.get("scope") or "machine",
        name=args.name or prev_about.get("name") or (first.product_name or raw_name),
        publisher=args.publisher or prev_about.get("publisher") or (first.company or ""),
        author=args.author or prev_about.get("author") or "",
        summary=args.summary or prev_about.get("summary") or "",
        description=args.description or prev_about.get("description"),
        homepage=args.homepage or prev_about.get("homepage"),
        license_name=args.license or prev_license.get("name") or "",
        license_url=args.license_url or prev_license.get("url"),
        extra_abouts={k: v for k, v in (previous.get("about") or {}).items() if k != prev_lang},
    )
    draft.author = draft.author or draft.publisher

    # 3. fill gaps
    required = {
        "version": ("Version (X.Y.Z)", lambda v: None if semver.is_valid(v) else "use strict X.Y.Z"),
        "name": ("Display name", None),
        "publisher": ("Publisher", None),
        "author": ("Author", None),
        "summary": ("Short description (10-160 chars)", lambda v: None if 10 <= len(v) <= 160 else "10-160 characters"),
        "license_name": ("License (SPDX id, or Proprietary/Freeware)", None),
    }
    missing = [key for key in required if not getattr(draft, key)]
    if draft.version and not semver.is_valid(draft.version):
        missing.insert(0, "version")
    if missing and not interactive:
        return _die("missing " + ", ".join(f"--{k.replace('_name', '').replace('_', '-')}" for k in missing))
    for key in missing:
        prompt, check = required[key]
        setattr(draft, key, _ask_until(prompt, getattr(draft, key) or None, check))
    if published and draft.version in published:
        return _die(f"{package_id} {draft.version} is already published - bump the version")

    for url, dl, found, arch, engine in analysed:
        switches = dict(engines.DEFAULT_SWITCHES.get(engine, {}))
        if args.silent:
            switches["silent"] = args.silent
        if args.progress:
            switches["progress"] = args.progress
        if args.custom:
            switches["custom"] = args.custom
        if engine == "exe" and not switches.get("silent"):
            if not interactive:
                return _die("engine 'exe' needs --silent (and ideally --progress)")
            switches["silent"] = _ask_until("Silent switches for this .exe", None)
            switches["progress"] = console.ask("Switches for install with progress UI", switches["silent"])
        draft.targets.append(TargetDraft(url, arch, engine, dl.sha256, dl.bytes, dl.path, switches,
                                         list(args.mirror or []) if len(analysed) == 1 else []))

    # 4. write the cards
    out_root = Path(args.out) if args.out else (Path.cwd() / "txa-out" if args.dry_run else work / "tree")
    release = out_root / draft.directory
    release.mkdir(parents=True, exist_ok=True)
    rendered = draft.render()
    for filename, text in rendered.items():
        (release / filename).write_text(text, "utf-8", newline="\n")
    print(f"→ wrote {len(rendered)} card(s) to {release}")

    # 5. local pre-flight with the very same 14 checks the bot runs
    report = None
    if not args.no_validate:
        rel = release.relative_to(out_root).as_posix()
        ctx = init_local([release], offline=args.offline,
                         payloads={rel: {t.arch: t.payload for t in draft.targets}})
        if published:
            ctx.targets[0].update({"mode": "new-version", "existing_versions": published})
        print("→ running TXA pre-flight (steps 01-14)…")
        for number in sorted(steps.REGISTRY):
            steps.run_step(ctx, number)
        report = build_report(ctx)
        print(render_console(report))
        if report["verdict"] == "fail" and not args.force:
            return _die("pre-flight failed - fix the issues above or pass --force to open the PR anyway")

    if args.dry_run:
        print("✓ dry run complete - nothing was sent to GitHub")
        return 0

    # 6. fork → branch → commit → PR
    existing_pr = None if args.force else _safe(lambda: _find_pr(gh, upstream, draft))
    if existing_pr:
        return _die(f"an open pull request already covers {package_id} {draft.version}: {existing_pr}")
    kind = "New version" if published else "New package"
    title = f"{kind}: {package_id} {draft.version}"
    files = {f"{draft.directory}/{name}": text for name, text in rendered.items()}
    try:
        from txa.create.submit import submit

        result = submit(gh, upstream, login, package_id, draft.version, files, title,
                        _pr_body(kind, draft, analysed, report), direct=args.direct)
    except GitHubError as exc:
        return _die(str(exc))
    print(f"\n✓ pull request #{result.number} opened: {result.url}")
    print(f"  branch {result.head_repo}@{result.branch} · the TXA Bot will now run its 16 steps")
    return 0


def _safe(fn):
    try:
        return fn()
    except GitHubError:
        return None


def _find_pr(gh: GitHub, upstream: str, draft: Draft) -> str | None:
    from txa.create.submit import find_open_pr

    return find_open_pr(gh, upstream, draft.package_id, draft.version)


def _pr_body(kind: str, draft: Draft, analysed, report: dict | None) -> str:
    build = winos.parse_build(draft.min_build)
    lines = [
        f"## 📦 {kind}: `{draft.package_id}` {draft.version}", "",
        "| Field | Value |", "|---|---|",
        f"| Name | {draft.name} |",
        f"| Publisher | {draft.publisher} |",
        f"| License | {draft.license_name} |",
        f"| Minimum Windows | {draft.min_build} ({winos.describe(build)}) |",
        f"| Scope | {draft.scope} |", "",
        "### Installers", "",
        "| Arch | Engine | Size | SHA-256 |", "|---|---|---|---|",
    ]
    for url, dl, found, arch, engine in analysed:
        lines.append(f"| {arch} | {found.label} (`{engine}`) | {dl.bytes / 1048576:.2f} MiB | `{dl.sha256}` |")
    if report:
        counts: dict[str, int] = {}
        for row in report["results"]:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        summary = " · ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        lines += ["", f"**Local pre-flight (txaci):** {summary}"]
    lines += ["", "---", f"<sub>Submitted with txacreate {__version__} · cards in `{draft.directory}/`</sub>"]
    return "\n".join(lines)


def cmd_update(args) -> int:
    """Like `new`, but for a package that already exists: every field is carried over."""
    args.id, args.update = args.package, True
    return cmd_new(args)


def cmd_hash(args) -> int:
    source = args.target
    if source.lower().startswith("https://"):
        work = Path(tempfile.mkdtemp(prefix="hash-", dir=config.temp_root()))
        path = net.download(net.session(), source, work, MAX_DOWNLOAD, on_progress=console.progress("download")).path
        console.done_line()
    else:
        path = Path(source)
        if not path.is_file():
            return _die(f"{source}: no such file")
    found = engines.inspect(path)
    print(f"sha256   {net.sha256_file(path)}")
    print(f"size     {path.stat().st_size}")
    print(f"engine   {found.engine or '-'} ({found.label})")
    print(f"arch     {guess_arch(path.name, found)} (installer CPU {found.machine or '-'})")
    if found.product_name or found.product_version:
        print(f"product  {found.product_name or '-'} {found.product_version or ''}".rstrip())
    return 0


def cmd_validate(args) -> int:
    from txa.ci.cli import main as txaci_main

    return txaci_main(["validate", *args.paths, *(["--offline"] if args.offline else [])])


# --------------------------------------------------------------------------- entry


def _release_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--version", help="X.Y.Z (default: read from the installer)")
    p.add_argument("--name")
    p.add_argument("--publisher")
    p.add_argument("--author")
    p.add_argument("--summary")
    p.add_argument("--description")
    p.add_argument("--homepage")
    p.add_argument("--license")
    p.add_argument("--license-url")
    p.add_argument("--lang", help="default locale (default en-US)")
    p.add_argument("--alias")
    p.add_argument("--tags", help="comma separated")
    p.add_argument("--channel", choices=["stable", "beta"])
    p.add_argument("--arch", help="override per URL, comma separated: x64,x86,arm64")
    p.add_argument("--engine", help="override per URL, comma separated: inno,nsis,msi,wix,exe")
    p.add_argument("--scope", choices=["user", "machine"])
    p.add_argument("--min-build", help="minimum Windows build, e.g. 10.0.17763")
    p.add_argument("--silent", help="silent switches (required for engine exe)")
    p.add_argument("--progress", help="switches for install with a progress UI")
    p.add_argument("--custom", help="extra switches always appended")
    p.add_argument("--mirror", action="append", help="additional download mirror (repeatable)")
    p.add_argument("--out", help="write cards under this folder (default ./txa-out for --dry-run)")
    p.add_argument("--dry-run", action="store_true", help="write cards and pre-flight only, no GitHub calls")
    p.add_argument("--offline", action="store_true", help="skip network checks in the pre-flight")
    p.add_argument("--no-validate", action="store_true", help="skip the local pre-flight")
    p.add_argument("--direct", action="store_true", help="branch in upstream instead of a fork (needs push rights)")
    p.add_argument("--force", action="store_true", help="open the PR even if pre-flight fails or a PR exists")
    p.add_argument("-y", "--yes", action="store_true", help="never prompt; fail on missing fields")


def main(argv: list[str] | None = None) -> int:
    console.setup()
    parser = argparse.ArgumentParser(prog="txacreate", description="Publish installers to the TXA repository")
    parser.add_argument("--version", action="version", version=f"txacreate {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("login", help="authenticate with GitHub (Device Code Flow or PAT)")
    p.add_argument("--token", help="personal access token (prefer --with-token: flags end up in shell history)")
    p.add_argument("--with-token", action="store_true", help="read a personal access token from stdin")
    p.add_argument("--scope", default="public_repo", help="OAuth scope for the device flow (default public_repo)")
    p.add_argument("--no-browser", action="store_true", help="do not open the verification page automatically")
    p.set_defaults(func=cmd_login)

    sub.add_parser("logout", help="delete the stored credential").set_defaults(func=cmd_logout)
    sub.add_parser("whoami", help="show the authenticated GitHub user").set_defaults(func=cmd_whoami)

    p = sub.add_parser("config", help="read or change settings (upstream, client_id, namespace, api)")
    p.add_argument("action", choices=["list", "get", "set", "unset"])
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("new", help="create a release from installer URL(s) and open a pull request")
    p.add_argument("urls", nargs="+", metavar="URL", help="HTTPS URL of an .exe/.msi (one per architecture)")
    p.add_argument("--id", help="Namespace.AppName (default: <namespace>.<ProductName>)")
    _release_options(p)
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("update", help="new version of an existing package (metadata carried over)")
    p.add_argument("package", help="existing package id, e.g. TXAVL.HelloTool")
    p.add_argument("urls", nargs="+", metavar="URL")
    _release_options(p)
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("hash", help="SHA-256, engine and architecture of a local file or HTTPS URL")
    p.add_argument("target")
    p.set_defaults(func=cmd_hash)

    p = sub.add_parser("validate", help="run the 14 bot checks against local release folders")
    p.add_argument("paths", nargs="+")
    p.add_argument("--offline", action="store_true")
    p.set_defaults(func=cmd_validate)

    args = parser.parse_args(argv)
    if args.command == "config" and args.action in ("get", "set", "unset") and not args.key:
        parser.error("config get/set/unset needs a key")
    if args.command == "config" and args.action == "set" and args.value is None:
        parser.error("config set needs a value")
    try:
        return args.func(args)
    except GitHubError as exc:
        return _die(str(exc))
    except KeyboardInterrupt:
        return _die("cancelled")


if __name__ == "__main__":
    sys.exit(main())
