"""txapm - install and manage packages from the TXA repository.

    txapm install <id|alias> [--version X.Y.Z] [--arch x64] [--progress|--interactive]
    txapm upgrade [<id>] [--all]       txapm uninstall <id>
    txapm list [--upgrade-available]   txapm show <id>      txapm search <text>
    txapm export <file>                txapm import <file>
    txapm pin add|remove|list [<id>]   txapm download <id> [-d dir]
    txapm list --all                   txapm source list|add|remove|enable|disable|update|reset
    txapm --info                       txapm hash <file|url>     txapm validate <folder>
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from txa import __version__
from txa.core import config, console, net, semver, winos
from txa.pm import installer, uninstall
from txa.pm.installer import InstallError
from txa.pm.source import NotFound, Source, SourceError, Sources, check_url


def _die(message: str) -> int:
    print(f"txapm: {message}", file=sys.stderr)
    return 1


def _about(cards: dict) -> dict:
    return cards["about"].get(cards["core"]["locale"]["default"]) or next(iter(cards["about"].values()))


def _pick(args, query: str) -> tuple[Source, str]:
    """(source, package id) honouring --source; refuses ids that exist in several sources."""
    return Sources(getattr(args, "source", None)).resolve(query)


def _source_of(record: dict | None) -> Source:
    name = (record or {}).get("source") or config.OFFICIAL_SOURCE
    try:
        return Sources(name).items[0]
    except SourceError:
        return Source()


def _install(src: Source, package_id: str, version: str | None, args) -> int:
    version, cards = src.release(package_id, version)
    build, about = cards["build"], _about(cards)
    target = installer.pick_target(build, getattr(args, "arch", None))
    origin = "" if src.name == config.OFFICIAL_SOURCE else f"  [source: {src.name}]"
    print(f"→ {about['name']} {version} ({package_id}) · {target['arch']} · {target['engine']}{origin}"
          + ("  [GitHub fallback]" if src.used_fallback else ""))
    download_only = getattr(args, "download_only", False)
    if not download_only and not getattr(args, "skip_os_check", False):
        installer.check_os(build)
    previous = installer.installed().get(package_id)
    if previous and previous["version"] == version and not getattr(args, "force", False) and not download_only:
        print(f"✓ {package_id} {version} is already installed (use --force to reinstall)")
        return 0

    payload = installer.fetch(package_id, version, target, console.progress("download"), source=src)
    console.done_line()
    print(f"✓ downloaded {payload} - SHA-256 verified")
    if download_only:
        return 0

    mode = "interactive" if getattr(args, "interactive", False) else "progress" if getattr(args, "progress", False) else "silent"
    log = config.txa_home() / "logs" / f"{package_id}-{version}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    executable, params = installer.build_command(target, payload, mode, log)
    scope = build.get("scope", "machine")
    elevate = scope == "machine"
    print(f"→ running {mode} install{' (elevation required)' if elevate and not installer.is_admin() else ''}")
    code = installer.run(executable, params, elevate)
    meaning = installer.EXIT_CODES.get(code, "see the installer log")
    if code not in set(target.get("success_codes") or []) | installer.SUCCESS:
        return _die(f"installer exited with {code}: {meaning} (log: {log})")
    installer.record(package_id, version, target, code, about["name"], scope, src.name)
    print(f"✓ {package_id} {version} installed ({meaning})")
    return 0


def _available(records: dict) -> dict[str, str]:
    """package id -> newer version published in the source each package was installed from."""
    latest: dict[str, dict[str, str]] = {}
    out = {}
    for package_id, info in records.items():
        name = info.get("source") or config.OFFICIAL_SOURCE
        if name not in latest:
            try:
                latest[name] = {p["id"]: p["latest"] for p in _source_of(info).index().get("packages", [])}
            except Exception:  # source offline: no upgrade info for its packages
                latest[name] = {}
        newest = latest[name].get(package_id)
        if newest and semver.is_valid(info["version"]) and semver.parse(newest) > semver.parse(info["version"]):
            out[package_id] = newest
    return out


# --------------------------------------------------------------------------- commands


def cmd_install(args) -> int:
    src, package_id = _pick(args, args.package)
    return _install(src, package_id, args.version, args)


def cmd_upgrade(args) -> int:
    records = installer.installed()
    if args.package:
        package_id = next((k for k in records if k.lower() == args.package.lower()), None) \
            or _pick(args, args.package)[1]
        if package_id not in records:
            return _die(f"{package_id} was not installed with txapm - use `txapm install`")
        if package_id in installer.pins() and not args.include_pinned:
            return _die(f"{package_id} is pinned - use --include-pinned or `txapm pin remove {package_id}`")
        todo = {package_id: _available({package_id: records[package_id]}).get(package_id)}
        if not todo[package_id]:
            print(f"✓ {package_id} {records[package_id]['version']} is up to date")
            return 0
    else:
        todo = _available(records)
        pinned = installer.pins()
        for package_id in sorted(k for k in list(todo) if k in pinned and not args.include_pinned):
            todo.pop(package_id)
            print(f"  {package_id:<40} pinned at {pinned[package_id]} - skipped (txapm pin remove to release)")
        if not todo:
            print("✓ everything is up to date")
            return 0
        for package_id, newest in sorted(todo.items()):
            print(f"  {package_id:<40} {records[package_id]['version']:<10} → {newest}")
        if not args.all:
            print(f"{len(todo)} upgrade(s) available - run `txapm upgrade --all` to apply")
            return 0
    failures = 0
    for package_id, newest in sorted(todo.items()):
        args.arch = args.arch or records[package_id].get("arch")
        failures += _install(_source_of(records[package_id]), package_id, newest, args) != 0
        args.arch = None
    return 1 if failures else 0


def cmd_uninstall(args) -> int:
    records = installer.installed()
    package_id = next((k for k in records if k.lower() == args.package.lower()), None)
    src = _source_of(records.get(package_id)) if package_id else None
    if not package_id:
        src, package_id = _pick(args, args.package)
    record = records.get(package_id)
    name = (record or {}).get("name")
    engine = (record or {}).get("engine", "exe")
    scope = (record or {}).get("scope", "machine")
    if not name:
        _, cards = src.release(package_id)
        name, engine = _about(cards)["name"], installer.pick_target(cards["build"])["engine"]
        scope = cards["build"].get("scope", "machine")
    matches = uninstall.find(name, uninstall.installed_programs())
    if not matches:
        return _die(f"'{name}' is not in the list of installed programs")
    if len(matches) > 1 and not args.all_matches:
        listing = ", ".join(f"{m.name} {m.version or ''}".strip() for m in matches)
        return _die(f"several programs match '{name}': {listing} (use --all-matches)")
    for program in matches:
        executable, params = uninstall.split_command(uninstall.command(program, engine, args.interactive))
        print(f"→ removing {program.name} {program.version or ''}".rstrip())
        code = installer.run(executable, params, scope == "machine")
        if code not in installer.SUCCESS:
            return _die(f"uninstaller exited with {code}: {installer.EXIT_CODES.get(code, 'unknown error')}")
    installer.forget(package_id)
    print(f"✓ {package_id} uninstalled")
    return 0


def cmd_list(args) -> int:
    if args.all:
        return _list_machine(args)
    records = installer.installed()
    if not records:
        print("nothing installed through txapm yet")
        return 0
    try:
        available = _available(records)
    except Exception:  # offline: still list what is installed
        available = {}
    rows = sorted(records.items())
    if args.upgrade_available:
        rows = [r for r in rows if r[0] in available]
    pinned = installer.pins()
    print(f"{'Id':<40} {'Version':<10} {'Available':<10} {'Arch':<6} {'Source':<10} Pin")
    for package_id, info in rows:
        print(f"{package_id:<40} {info['version']:<10} {available.get(package_id, ''):<10} {info['arch']:<6} "
              f"{info.get('source', config.OFFICIAL_SOURCE):<10} {pinned.get(package_id, '')}")
    return 0


def _list_machine(args) -> int:
    """Every program Windows knows about; those also in TXA get an Id, Available and Source."""
    index: dict[str, tuple[str, dict]] = {}
    for source in Sources().items:
        try:
            for entry in source.index().get("packages", []):
                index.setdefault(entry["name"].lower(), (source.name, entry))
        except Exception:  # offline source: still list the machine
            continue
    rows = []
    for program in sorted(uninstall.installed_programs(), key=lambda p: p.name.lower()):
        if not program.name:
            continue
        lowered = program.name.lower()
        match = index.get(lowered) or next((v for n, v in index.items() if lowered.startswith(n + " ")), None)
        origin, entry = match if match else ("", None)
        version = program.version or ""
        newest = ""
        if entry:
            current = semver.coerce(version) or version
            if semver.is_valid(current) and semver.parse(entry["latest"]) > semver.parse(current):
                newest = entry["latest"]
        if args.upgrade_available and not newest:
            continue
        rows.append((program.name[:38], entry["id"] if entry else "", version[:14], newest, origin))
    print(f"{'Name':<38} {'Id':<32} {'Version':<14} {'Available':<10} Source")
    for name, package_id, version, newest, source in rows:
        print(f"{name:<38} {package_id:<32} {version:<14} {newest:<10} {source}")
    print(f"{len(rows)} program(s)")
    return 0


def cmd_pin(args) -> int:
    pinned = installer.pins()
    if args.action == "list":
        if not pinned:
            print("no pins")
        for package_id, version in sorted(pinned.items()):
            print(f"{package_id:<40} {version}")
        return 0
    if not args.package:
        return _die(f"pin {args.action} needs a package id")
    package_id = _pick(args, args.package)[1]
    if args.action == "remove":
        installer.set_pin(package_id, None)
        print(f"✓ {package_id} unpinned")
        return 0
    version = args.version or (installer.installed().get(package_id) or {}).get("version") or "*"
    installer.set_pin(package_id, version)
    print(f"✓ {package_id} pinned at {version} - `txapm upgrade --all` will skip it")
    return 0


def cmd_download(args) -> int:
    src, package_id = _pick(args, args.package)
    version, cards = src.release(package_id, args.version)
    target = installer.pick_target(cards["build"], args.arch)
    payload = installer.fetch(package_id, version, target, console.progress("download"), source=src)
    console.done_line()
    out = Path(args.dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    dest = out / payload.name
    shutil.copy2(payload, dest)
    manifest = out / f"{package_id}-{version}.txa.json"
    manifest.write_text(json.dumps({"version": version, **cards}, indent=2, ensure_ascii=False), "utf-8")
    print(f"✓ {dest} (SHA-256 verified)")
    print(f"✓ {manifest}")
    return 0


def cmd_source(args) -> int:
    items = config.sources()
    official_enabled = items[0]["enabled"]
    custom = items[1:]
    by_name = {i["name"].lower(): i for i in items}
    action, name = args.action, args.name
    if action in ("add", "remove", "enable", "disable") and not name:
        return _die(f"source {action} needs a name")
    if action == "add":
        if not args.url:
            return _die("usage: txapm source add <name> <url|folder> [--token-env VAR] [--allow-lan]")
        if name.lower() in by_name:
            return _die(f"a source named '{name}' already exists")
        if not name.replace("-", "").replace("_", "").isalnum():
            return _die("source names may only contain letters, digits, - and _")
        url = check_url(args.url)
        probe = Source(url, name=name, token_env=args.token_env, allow_lan=args.allow_lan, fallback=False)
        index = probe._get_json("index.json")
        if not index or index.get("api") != "txa.v1":
            return _die(f"{url} does not serve a txa.v1 index (expected {url}/index.json)")
        entry = {"name": name, "url": url}
        if args.token_env:
            entry["token_env"] = args.token_env
        if args.allow_lan:
            entry["allow_lan"] = True
        config.save_custom_sources([*custom, entry], official_enabled)
        print(f"✓ source '{name}' added - {index.get('count', 0)} package(s)")
    elif action == "remove":
        if name.lower() == config.OFFICIAL_SOURCE:
            return _die("the official source cannot be removed - use `txapm source disable txa`")
        if name.lower() not in by_name:
            return _die(f"no source named '{name}'")
        config.save_custom_sources([c for c in custom if c["name"].lower() != name.lower()], official_enabled)
        print(f"✓ source '{name}' removed")
    elif action in ("enable", "disable"):
        if name.lower() not in by_name:
            return _die(f"no source named '{name}'")
        flag = action == "enable"
        if name.lower() == config.OFFICIAL_SOURCE:
            config.save_custom_sources(custom, flag)
        else:
            config.save_custom_sources([{**c, "enabled": flag} if c["name"].lower() == name.lower() else c
                                        for c in custom], official_enabled)
        print(f"✓ source '{name}' {action}d")
    elif action == "reset":
        config.save_custom_sources([], True)
        cfg = config.load_config()
        cfg.pop("api", None)
        config.save_config(cfg)
        print("✓ sources reset to the official txa source only")
    elif action == "update":
        for source in Sources(name).items:
            try:
                print(f"✓ {source.name}: {source.index().get('count', 0)} package(s)")
            except Exception as exc:
                print(f"✗ {source.name}: {exc}")
        return 0
    elif action == "export":
        print(json.dumps([{k: v for k, v in i.items() if k != "official"} for i in custom], indent=2))
        return 0
    print(f"{'Name':<12} {'Enabled':<8} {'Argument':<58} Options")
    for item in config.sources():
        options = ", ".join(filter(None, [
            "official" if item.get("official") else "",
            f"token from %{item['token_env']}%" if item.get("token_env") else "",
            "LAN allowed" if item.get("allow_lan") else "",
        ]))
        print(f"{item['name']:<12} {'yes' if item.get('enabled', True) else 'no':<8} {item['url']:<58} {options}")
    return 0


def cmd_info() -> int:
    from txa.core import credentials

    record = credentials.stored() or {}
    current = winos.current()
    email = f" <{record['email']}>" if record.get("email") else ""
    print(f"txapm {__version__}")
    print(f"windows     {winos.fmt(current) if current else '-'} · {winos.native_arch()}")
    enabled = [i["name"] for i in config.sources() if i.get("enabled", True)]
    print(f"sources     {', '.join(enabled) or '(none enabled)'}")
    print(f"repository  {config.get('upstream')}")
    print(f"home        {config.txa_home()}")
    print(f"downloads   {config.temp_root()}")
    print(f"logs        {config.txa_home() / 'logs'}")
    print(f"installed   {len(installer.installed())} package(s), {len(installer.pins())} pin(s)")
    print(f"github      {record.get('login', '-')}{email}")
    return 0


def cmd_hash(args) -> int:
    from txa.create.cli import main as create_main

    return create_main(["hash", args.target])


def cmd_validate(args) -> int:
    from txa.ci.cli import main as txaci_main

    return txaci_main(["validate", *args.paths, *(["--offline"] if args.offline else [])])


def cmd_show(args) -> int:
    src, package_id = _pick(args, args.package)
    version, cards = src.release(package_id, args.version)
    build, about = cards["build"], _about(cards)
    if args.versions:
        print("\n".join(src.versions(package_id)))
        return 0
    minimum = winos.parse_build(build["platform"]["min_build"])
    print(f"{about['name']} {version}  ({package_id})  [source: {src.name}]")
    print(f"  {about['summary']}")
    print(f"  publisher  {about['publisher']}   author {about['author']}")
    print(f"  license    {about['license']['name']}" + (f"  {about['license'].get('url')}" if about['license'].get('url') else ""))
    if about.get("homepage"):
        print(f"  homepage   {about['homepage']}")
    print(f"  windows    {build['platform']['min_build']}+ ({winos.describe(minimum)}) · scope {build.get('scope', 'machine')}")
    for target in build["targets"]:
        print(f"  [{target['arch']}] {target['engine']:<5} {target['source']['url']}")
        print(f"         sha256 {target['source']['sha256']}")
    return 0


def cmd_search(args) -> int:
    hits = Sources(args.source).search(args.text)
    if not hits:
        print("no packages found")
        return 1
    width = max(len(h["id"]) for _, h in hits)
    print(f"{'Id':<{width}}  {'Version':<10} {'Source':<10} Summary")
    for source, hit in hits:
        print(f"{hit['id']:<{width}}  {hit['latest']:<10} {source.name:<10} {hit['summary']}")
    return 0


def cmd_export(args) -> int:
    records = installer.installed()
    document = {
        "txa": 1, "kind": "export", "exported": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "packages": [{"id": k, "version": v["version"], "arch": v["arch"], "source": v.get("source", config.OFFICIAL_SOURCE)}
                     for k, v in sorted(records.items())],
    }
    Path(args.file).write_text(json.dumps(document, indent=2) + "\n", "utf-8")
    print(f"✓ exported {len(records)} package(s) to {args.file}")
    return 0


def cmd_import(args) -> int:
    try:
        document = json.loads(Path(args.file).read_text("utf-8"))
    except (OSError, ValueError) as exc:
        return _die(f"cannot read {args.file}: {exc}")
    if document.get("kind") != "export":
        return _die(f"{args.file} is not a txapm export file")
    failures = 0
    for item in document.get("packages", []):
        args.arch = item.get("arch")
        try:
            src = _source_of(item)
            failures += _install(src, item["id"], item["version"] if args.exact_versions else None, args) != 0
        except (InstallError, NotFound, SourceError, net.NetError) as exc:
            failures += 1
            print(f"txapm: {item['id']}: {exc}", file=sys.stderr)
    print(f"{'✓' if not failures else '!'} import finished, {failures} failure(s)")
    return 1 if failures else 0


# --------------------------------------------------------------------------- entry


def _install_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--arch", choices=["x64", "x86", "arm64"])
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--progress", action="store_true", help="unattended, but show the installer's progress UI")
    mode.add_argument("--interactive", action="store_true", help="run the normal installer UI")
    p.add_argument("--force", action="store_true", help="reinstall even if this version is recorded")
    p.add_argument("--skip-os-check", action="store_true")


def main(argv: list[str] | None = None) -> int:
    console.setup()
    parser = argparse.ArgumentParser(prog="txapm", description="TXA package manager",
        epilog='examples:\n  txapm search notepad\n  txapm install TXAVL.HelloTool\n  txapm upgrade --all\n  txapm uninstall TXAVL.HelloTool', formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"txapm {__version__}")
    parser.add_argument("--info", action="store_true", help="show version, paths, source and account")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p = sub.add_parser("install", aliases=["add"], help="download, verify and silently install a package")
    p.add_argument("-s", "--source", help="only look in this source (see `txapm source list`)")
    p.add_argument("package", help="package id (TXAVL.HelloTool) or alias")
    p.add_argument("--version", dest="version", help="install this X.Y.Z instead of the newest")
    p.add_argument("--download-only", action="store_true", help="stop after downloading and verifying")
    _install_options(p)
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("upgrade", aliases=["update"], help="upgrade one package, or list/apply all upgrades")
    p.add_argument("-s", "--source", help="only look in this source (see `txapm source list`)")
    p.add_argument("package", nargs="?")
    p.add_argument("--all", action="store_true", help="upgrade every package with a newer version")
    p.add_argument("--include-pinned", action="store_true", help="also upgrade pinned packages")
    _install_options(p)
    p.set_defaults(func=cmd_upgrade)

    p = sub.add_parser("uninstall", aliases=["remove", "rm"], help="silently uninstall a package")
    p.add_argument("-s", "--source", help="only look in this source (see `txapm source list`)")
    p.add_argument("package")
    p.add_argument("--interactive", action="store_true", help="run the normal uninstaller UI")
    p.add_argument("--all-matches", action="store_true", help="remove every program matching the name")
    p.set_defaults(func=cmd_uninstall)

    p = sub.add_parser("list", aliases=["ls"], help="packages installed through txapm (--all: every program)")
    p.add_argument("--upgrade-available", action="store_true", help="only packages with a newer version")
    p.add_argument("--all", action="store_true", help="every installed program on this PC, matched against TXA")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", aliases=["view"], help="show package details")
    p.add_argument("-s", "--source", help="only look in this source (see `txapm source list`)")
    p.add_argument("package")
    p.add_argument("--version", dest="version")
    p.add_argument("--versions", action="store_true", help="list every published version")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("search", aliases=["find"], help="search the package index")
    p.add_argument("-s", "--source", help="only look in this source (see `txapm source list`)")
    p.add_argument("text")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("export", help="save the list of installed packages to a JSON file")
    p.add_argument("file")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("import", help="install every package from an export file")
    p.add_argument("file")
    p.add_argument("--exact-versions", action="store_true", help="install the exported versions, not the newest")
    _install_options(p)
    p.set_defaults(func=cmd_import, download_only=False)

    p = sub.add_parser("pin", help="hold a package at its version so upgrade --all skips it")
    p.add_argument("-s", "--source", help="only look in this source (see `txapm source list`)")
    p.add_argument("action", choices=["add", "remove", "list"])
    p.add_argument("package", nargs="?")
    p.add_argument("--version", dest="version", help="pin to this version (default: installed version)")
    p.set_defaults(func=cmd_pin)

    p = sub.add_parser("download", help="download and verify an installer plus its manifest into a folder")
    p.add_argument("-s", "--source", help="only look in this source (see `txapm source list`)")
    p.add_argument("package")
    p.add_argument("--version", dest="version")
    p.add_argument("--arch", choices=["x64", "x86", "arm64"])
    p.add_argument("-d", "--dir", default=str(Path.home() / "Downloads"), help="target folder (default Downloads)")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("source", help="manage package sources: list, add, remove, enable, disable, update, reset")
    p.add_argument("action", nargs="?", default="list",
                   choices=["list", "add", "remove", "enable", "disable", "update", "reset", "export"])
    p.add_argument("name", nargs="?", help="source name")
    p.add_argument("url", nargs="?", help="https:// URL serving txa.v1, or a local folder with manifests/")
    p.add_argument("--token-env", help="environment variable holding a bearer token for a private source")
    p.add_argument("--allow-lan", action="store_true", help="allow installers on private/LAN addresses")
    p.set_defaults(func=cmd_source)

    p = sub.add_parser("hash", help="SHA-256, engine and architecture of an installer file or URL")
    p.add_argument("target")
    p.set_defaults(func=cmd_hash)

    p = sub.add_parser("validate", help="run the repository bot checks on a local manifest folder")
    p.add_argument("paths", nargs="+")
    p.add_argument("--offline", action="store_true")
    p.set_defaults(func=cmd_validate)

    args = parser.parse_args(argv)
    if args.info:
        return cmd_info()
    if args.command is None:
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except (InstallError, NotFound, SourceError, net.NetError) as exc:
        return _die(str(exc))
    except KeyboardInterrupt:
        return _die("cancelled")


if __name__ == "__main__":
    sys.exit(main())
