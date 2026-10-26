"""txacreate - publish installers to txa-repo through a pull request.

    txacreate new <url> [<url> ...]        analyse, write cards, pre-flight, then fork + branch + commit + PR
                                           (GitHub access is requested only at the fork step)
    txacreate new <url> --dry-run          only write the cards to ./txa-out
    txacreate update <id> <url> [...]      new version of an existing package, metadata carried over
    txacreate hash <file|url>              SHA-256 + engine + architecture of an installer
    txacreate submit <folder>              PR for a hand-written release folder
    txacreate show <id>                    print the published cards of a package
    txacreate token [--store [-t PAT]] [--clear]   optional: pre-authorize, or forget, GitHub access
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
    console.error("txacreate", message)
    return 1


# --------------------------------------------------------------------------- auth


SCOPES = "public_repo user:email"


def _verified(token: str) -> tuple[GitHub, dict, str | None]:
    """Check a token against GitHub -> (client, user, email). Warns when it cannot open PRs."""
    gh = GitHub(token)
    response = gh.request("GET", "user")
    user = response.json()
    scopes = response.headers.get("X-OAuth-Scopes")
    if scopes is not None and not {"repo", "public_repo"} & {x.strip() for x in scopes.split(",")}:
        console.say("  ! this token has no repo/public_repo scope - forking and opening PRs will fail")
    return gh, user, _primary_email(gh, user)


def _device_login(reason: str) -> tuple[str, str]:
    """GitHub Device Code Flow: show a one-time code, open the browser, wait for approval."""
    def show(code: dict) -> None:
        print(f"\n{console.paint(reason, 'bold')}")
        print(f"  1. Open   {console.paint(code['verification_uri'], 'cyan')}")
        print(f"  2. Enter  {console.paint(code['user_code'], 'yellow', 'bold')}\n")
        webbrowser.open(code["verification_uri"])
        print(console.paint("  Waiting for you to authorize TXA in the browser…", "gray"))

    return device_flow(config.get("client_id"), SCOPES, show), "device flow"


def _sign_in(token_arg: str | None = None) -> tuple[GitHub, str]:
    """Credentials for the fork step. Order: --token, TXA_TOKEN, stored login, GH_TOKEN, then the browser.

    Only a device-flow login is stored; a token passed explicitly is used once and never saved.
    """
    token, source = (token_arg, "--token") if token_arg else credentials.load_token()
    saved = False
    if not token:
        token, source = _device_login("GitHub access is needed to fork the repository and open the pull request:")
        saved = True
    try:
        gh, user, email = _verified(token)
    except GitHubError as exc:
        if exc.status != 401 or saved or not source.startswith("stored credential"):
            raise
        console.say("  ! the stored GitHub login has expired - signing in again")
        credentials.clear_token()
        token, source = _device_login("Authorize TXA again:")
        saved = True
        gh, user, email = _verified(token)
    if saved:
        path = credentials.save_token(token, user["login"], "device flow", email)
        console.say(f"✓ signed in as {user['login']} <{email or 'email hidden'}> (kept encrypted in {path})")
    else:
        console.say(f"→ using GitHub account {user['login']} ({source})")
    return gh, user["login"]


def _primary_email(gh: GitHub, user: dict) -> str | None:
    """Public profile email, else the primary verified one (needs the user:email scope)."""
    if user.get("email"):
        return user["email"]
    response = gh.request("GET", "user/emails", ok=(403, 404))
    if response.status_code != 200:
        return None
    emails = response.json()
    primary = next((e for e in emails if e.get("primary") and e.get("verified")), None)
    return (primary or next(iter(emails), {})).get("email")


def cmd_token(args) -> int:
    """Optional helper: authorize ahead of time, show the stored login, or forget it."""
    if args.clear:
        console.say("✓ stored GitHub login removed" if credentials.clear_token() else "no stored GitHub login")
        return 0
    if args.store:
        if args.token:
            token, source = args.token.strip(), "personal access token"
        else:
            token, source = _device_login("Authorize TXA on GitHub:")
        _, user, email = _verified(token)
        path = credentials.save_token(token, user["login"], source, email)
        console.say(f"✓ stored login for {user['login']} <{email or 'email hidden'}> via {source}")
        print(console.paint(f"  {path} (encrypted for this Windows account)", "gray"))
        return 0
    token, source = credentials.load_token()
    if not token:
        console.say("! not signed in - this is fine: txacreate asks for GitHub access only when it opens a pull request")
        return 0
    _, user, email = _verified(token)
    for key, value in (("user", console.paint(user["login"], "bold", "cyan")), ("email", email or "(hidden)"),
                       ("token", source), ("upstream", config.get("upstream"))):
        print(f"{console.label(key, 10)}{value}")
    return 0


def cmd_config(args) -> int:
    cfg = config.load_config()
    if args.action == "list":
        for key in config.keys():
            print(f"{console.label(key, 10)}{config.get(key) or '-'}")
        print(f"{console.label('api_base', 10)}{config.api_base()}  {console.paint('(derived)', 'gray')}")
    elif args.action == "get":
        print(config.get(args.key))
    elif args.action == "set":
        config.get(args.key)  # validates the key name
        cfg[args.key] = args.value
        config.save_config(cfg)
        console.say(f"✓ {args.key} = {args.value}")
    elif args.action == "unset":
        cfg.pop(args.key, None)
        config.save_config(cfg)
        console.say(f"✓ {args.key} reset to default")
    return 0


# --------------------------------------------------------------------------- new


def _ask_until(prompt: str, default: str | None, check=None) -> str:
    while True:
        value = console.ask(prompt, default)
        problem = check(value) if check else (None if value else "required")
        if not problem:
            return value
        console.say(f"    ✗ {problem}")


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
    if not args.dry_run:
        console.say(f"→ target repository {upstream}")

    # 1. download + analyse every installer
    work = Path(tempfile.mkdtemp(prefix="create-", dir=config.temp_root()))
    session = net.session()
    arches, engine_overrides = _per_url(args.arch, len(args.urls)), _per_url(args.engine, len(args.urls))
    analysed = []
    for index, url in enumerate(args.urls):
        console.say(f"→ downloading {url}")
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
        print(f"  {console.paint(dl.path.name, 'bold')}: {dl.bytes:,} bytes · SHA-256 {console.paint(dl.sha256, 'gray')}")
        print(f"  {found.label} · installer CPU {found.machine or '-'} → arch {console.paint(arch, 'yellow', 'bold')}, "
              f"engine {console.paint(engine, 'magenta', 'bold')}")
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

        src = Source()
        latest, previous = src.release(package_id)
        published = src.versions(package_id)
        console.say(f"→ {package_id} already published (latest {latest}) - reusing its metadata")
    except Exception:
        if getattr(args, "update", False):
            return _die(f"{package_id} is not published yet - use `txacreate new`")
        console.say(f"→ {package_id} is a new package")
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
    console.say(f"→ wrote {len(rendered)} card(s) to {release}")

    # 5. local pre-flight with the very same 14 checks the bot runs
    report = None
    if not args.no_validate:
        rel = release.relative_to(out_root).as_posix()
        ctx = init_local([release], offline=args.offline,
                         payloads={rel: {t.arch: t.payload for t in draft.targets}})
        if published:
            ctx.targets[0].update({"mode": "new-version", "existing_versions": published})
        console.say("→ running TXA pre-flight (steps 01-14)…")
        for number in sorted(steps.REGISTRY):
            steps.run_step(ctx, number)
        report = build_report(ctx)
        print(render_console(report))
        if report["verdict"] == "fail" and not args.force:
            return _die("pre-flight failed - fix the issues above or pass --force to open the PR anyway")

    if args.dry_run:
        console.say("✓ dry run complete - nothing was sent to GitHub")
        return 0

    # 6. fork → branch → commit → PR  (the first step that needs GitHub access)
    try:
        gh, login = _sign_in(args.token)
    except GitHubError as exc:
        return _die(f"{exc}\n  (or pass --token <PAT> / set TXA_TOKEN)")
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
    console.say(f"\n✓ pull request #{result.number} opened: {console.paint(result.url, 'cyan', 'bold')}")
    print(console.paint(f"  branch {result.head_repo}@{result.branch} · the TXA Bot will now run its 16 steps", "gray"))
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
    print(f"{console.label('sha256', 9)}{console.paint(net.sha256_file(path), 'green')}")
    print(f"{console.label('size', 9)}{path.stat().st_size:,} bytes")
    print(f"{console.label('engine', 9)}{console.paint(found.engine or '-', 'magenta', 'bold')} ({found.label})")
    print(f"{console.label('arch', 9)}{console.paint(guess_arch(path.name, found), 'yellow', 'bold')} (installer CPU {found.machine or '-'})")
    if found.product_name or found.product_version:
        print(f"{console.label('product', 9)}{found.product_name or '-'} {found.product_version or ''}".rstrip())
    return 0


def cmd_submit(args) -> int:
    """Open a PR for a hand-written release folder (core + build + about cards)."""
    from txa.core.manifest import Bundle, kind_of, release_dir
    from txa.create.submit import find_open_pr, submit

    folder = Path(args.folder)
    bundle = Bundle.read(folder)
    if not bundle.package_id or not bundle.version:
        return _die(f"{folder}: no readable core.txa.yaml")
    package_id, version = bundle.package_id, bundle.version
    if not args.no_validate:
        ctx = init_local([folder], offline=args.offline)
        console.say(f"→ validating {package_id} {version} (steps 01-14)…")
        for number in sorted(steps.REGISTRY):
            steps.run_step(ctx, number)
        report = build_report(ctx)
        print(render_console(report))
        if report["verdict"] == "fail" and not args.force:
            return _die("validation failed - fix the issues above or pass --force")
    try:
        gh, login = _sign_in(args.token)
    except GitHubError as exc:
        return _die(f"{exc}\n  (or pass --token <PAT> / set TXA_TOKEN)")
    upstream = config.get("upstream")
    if not args.force:
        existing = find_open_pr(gh, upstream, package_id, version)
        if existing:
            return _die(f"an open pull request already covers {package_id} {version}: {existing}")
    try:
        from txa.pm.source import Source

        published = Source().versions(package_id)
    except Exception:
        published = []
    kind = "New version" if published else "New package"
    target_dir = release_dir(package_id, version)
    files = {f"{target_dir}/{name}": raw.decode("utf-8") for name, raw in bundle.files.items() if kind_of(name)}
    body = (f"## 📦 {kind}: `{package_id}` {version}\n\nSubmitted from a local folder with "
            f"`txacreate submit` ({len(files)} card(s) in `{target_dir}/`).\n\n<sub>txacreate {__version__}</sub>")
    result = submit(gh, upstream, login, package_id, version, files, f"{kind}: {package_id} {version}", body,
                    direct=args.direct)
    console.say(f"\n✓ pull request #{result.number} opened: {console.paint(result.url, 'cyan', 'bold')}")
    return 0


def cmd_show(args) -> int:
    from txa.core.manifest import dump_card
    from txa.pm.source import Source

    src = Source()
    package_id = src.resolve(args.package)
    version, cards = src.release(package_id, args.version)
    documents = [("core.txa.yaml", cards["core"]), ("build.txa.yaml", cards["build"])]
    documents += [(f"about.{lang}.txa.yaml", card) for lang, card in cards["about"].items()]
    for name, card in documents:
        print(console.paint(f"# ---- {package_id} {version} · {name}", "bold", "cyan"))
        print(dump_card(card))
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
    p.add_argument("-t", "--token", help="GitHub personal access token for this run (default: browser sign-in when needed)")


def main(argv: list[str] | None = None) -> int:
    console.setup()
    parser = argparse.ArgumentParser(prog="txacreate", description="Publish installers to the TXA repository",
        epilog='examples:\n  txacreate new https://vendor.example/app-1.2.3-x64.exe\n  txacreate update TXAVL.HelloTool https://vendor.example/app-1.2.4-x64.exe\n  txacreate hash setup.exe', formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"txacreate {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p = sub.add_parser("token", help="optional: sign in to GitHub ahead of time, show or forget the stored login")
    p.add_argument("--store", action="store_true", help="authorize now (browser) and keep the login")
    p.add_argument("--clear", action="store_true", help="forget the stored login")
    p.add_argument("-t", "--token", help="with --store: keep this personal access token instead of using the browser")
    p.set_defaults(func=cmd_token)

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

    p = sub.add_parser("submit", help="open a pull request for a hand-written release folder")
    p.add_argument("folder", help="folder containing core.txa.yaml, build.txa.yaml, about.<lang>.txa.yaml")
    p.add_argument("--offline", action="store_true", help="skip network checks in the validation")
    p.add_argument("--no-validate", action="store_true")
    p.add_argument("--direct", action="store_true", help="branch in upstream instead of a fork (needs push rights)")
    p.add_argument("--force", action="store_true", help="submit even if validation fails or a PR exists")
    p.add_argument("-t", "--token", help="GitHub personal access token for this run")
    p.set_defaults(func=cmd_submit)

    p = sub.add_parser("show", help="print the published cards of a package")
    p.add_argument("package")
    p.add_argument("--version", dest="version")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("hash", help="SHA-256, engine and architecture of a local file or HTTPS URL")
    p.add_argument("target")
    p.set_defaults(func=cmd_hash)

    p = sub.add_parser("validate", help="run the 14 bot checks against local release folders")
    p.add_argument("paths", nargs="+")
    p.add_argument("--offline", action="store_true")
    p.set_defaults(func=cmd_validate)

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "config" and args.action in ("get", "set", "unset") and not args.key:
        parser.error("config get/set/unset needs a key")
    if args.command == "config" and args.action == "set" and args.value is None:
        parser.error("config set needs a value")
    try:
        return args.func(args)
    except (GitHubError, net.NetError, LookupError) as exc:  # LookupError covers source.NotFound
        return _die(str(exc))
    except KeyboardInterrupt:
        return _die("cancelled")


if __name__ == "__main__":
    sys.exit(main())
