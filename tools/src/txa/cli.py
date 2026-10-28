"""txacli - fast unified CLI for WinGet import, repo management, API building, and PR submission.

Replaces PowerShell wrapper scripts with a native global command (`txacli`):
    txacli -s <query>                                  search WinGet packages
    txacli search <query>                              search WinGet packages
    txacli <pkg_or_name> [<pkg2> ...]                  convert WinGet package(s) into TXA manifests
    txacli import <pkg_or_name> [-o C:\\my-txa-repo]    import package(s) into a repo folder
    txacli -i [-y]                                     scan PC for untracked installed apps & import
    txacli scan                                        list installed apps on PC available on WinGet
    txacli submit <folder_or_repo_or_api>              open PR(s) for release folder, repo root, or API dir
    txacli build-api [-r <repo>] [-o <api_out>]        build static txa.v1 REST API
    txacli validate <folder_or_repo> [--offline]       run 14-step validation
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from txa import __version__
from txa.core import config, console
from txa.core.manifest import find_repo_root
from txa.create import winget as wg


def _resolve_repo_root(out_arg: str | None) -> Path:
    if out_arg:
        p = Path(out_arg).expanduser().resolve()
        return p.parent if p.name == "manifests" else p
    found = find_repo_root(Path.cwd())
    return found if found else Path.cwd()


def do_search(query: str) -> int:
    from txa.core import i18n

    print(f"\n{i18n.tr('Searching WinGet for', 'Đang tìm trên WinGet cho')} '{console.paint(query, 'cyan')}'...")
    results = wg.search_winget(query)
    if not results:
        print(console.paint(i18n.tr("No packages found.", "Không tìm thấy gói nào."), "yellow"))
        return 1
    print(f"\n{i18n.tr(f'Found {len(results)} package(s):', f'Tìm thấy {len(results)} gói:')}")
    print(f"  {'Id':<42} {'Version':<16} {'Name'}")
    print("  " + "-" * 78)
    for r in results:
        print(f"  {r['id']:<42} {r['version']:<16} {r['name']}")
    print(f"\n{i18n.tr('To import a package, run:', 'Để import một gói vào kho, hãy chạy:')}\n  txacli {results[0]['id']}")
    return 0


def do_scan(repo_root: Path | None = None) -> list[dict[str, str]]:
    from txa.core import i18n

    print(f"\n{i18n.tr('Scanning installed programs on this PC not yet in TXA...', 'Đang quét các phần mềm trên máy chưa có trong TXA...')}")
    untracked = wg.scan_installed_winget_packages(repo_root=repo_root)
    if not untracked:
        print(console.paint(i18n.tr("[+] All detected WinGet programs on this machine are already in TXA!",
                                    "[+] Tất cả phần mềm WinGet trên máy đều đã có trong kho TXA!"), "green"))
        return []
    print(f"\n{i18n.tr(f'Found {len(untracked)} installed program(s) available on WinGet:', f'Tìm thấy {len(untracked)} phần mềm trên máy có sẵn trên WinGet:')}")
    print(f"{'#':<4} {'Installed Name':<38} {'WinGet Id':<36} {'Version'}")
    print("-" * 92)
    for idx, item in enumerate(untracked, 1):
        print(f"{idx:<4} {item['name'][:37]:<38} {console.paint(item['id'], 'cyan'):<45} {item['version']}")
    return untracked


def do_build_api(repo_root: Path, api_out: str) -> int:
    from txa.ci.api import build_api
    from txa.core import i18n

    api_target = Path(api_out).expanduser()
    if not api_target.is_absolute():
        api_target = (repo_root / api_target).resolve()
    print(f"\n{i18n.tr('Building txa.v1 REST API into', 'Đang tạo REST API txa.v1 vào')} '{api_target}'...")
    summary = build_api(repo_root, api_target)
    print(console.paint(i18n.tr(
        f"[+] API build complete: {summary['packages']} package(s) indexed.",
        f"[+] Đã tạo xong API: {summary['packages']} gói được lập chỉ mục.",
    ), "green"))
    if summary["skipped"]:
        print(console.paint(i18n.tr(
            f"[!] Skipped {len(summary['skipped'])} invalid release(s).",
            f"[!] Đã bỏ qua {len(summary['skipped'])} bản phát hành không hợp lệ.",
        ), "yellow"))
    return 0


def do_import(args) -> int:
    manifest_root = _resolve_repo_root(getattr(args, "out", None))

    if getattr(args, "txacreate", False):
        from txa.create.cli import main as txacreate_main

        cli_args = ["new", "--out", str(manifest_root), "-y"]
        if getattr(args, "dry_run", False):
            cli_args.append("--dry-run")
        if getattr(args, "from_installed", False):
            cli_args.append("--from-installed")
        if getattr(args, "id", None):
            cli_args += ["--id", args.id]
        cli_args += list(args.packages)
        return txacreate_main(cli_args)

    queries = list(args.packages)
    if getattr(args, "from_installed", False):
        untracked = do_scan(manifest_root)
        if not untracked:
            return 0
        if getattr(args, "yes", False) or not sys.stdin.isatty():
            queries.extend(item["id"] for item in untracked)
        else:
            from txa.core import i18n

            options = [console.Option("all", i18n.tr("All programs", "Tất cả phần mềm"),
                                      i18n.tr(f"import all {len(untracked)} program(s)", f"import toàn bộ {len(untracked)} phần mềm"))]
            for item in untracked:
                options.append(console.Option(item["id"], item["name"][:24], f"{item['id']} ({item['version']})"))
            picked = console.choose(
                i18n.tr("\nSelect program to import (use ↑/↓ or type number, Enter to confirm)",
                        "\nChọn phần mềm cần import (dùng ↑/↓ hoặc nhập số, Enter để xác nhận)"),
                "import", options, default=0,
            )
            if picked.value == "all":
                queries.extend(item["id"] for item in untracked)
            else:
                queries.append(str(picked.value))

    if not queries:
        return 1

    from txa.core import i18n

    namespace = getattr(args, "namespace", None) or config.get("namespace") or "TXAVL"
    created_dirs: list[Path] = []
    for query in queries:
        print(f"\n{i18n.tr('Resolving', 'Đang phân giải')} '{console.paint(query, 'cyan')}'...")
        wg_id = wg.resolve_to_winget_id(query)
        if not wg_id:
            print(console.paint(i18n.tr(f"[-] Could not resolve '{query}' on WinGet.", f"[-] Không tìm thấy '{query}' trên WinGet."), "red"))
            continue
        data = wg.query_winget_package(wg_id)
        if not data:
            print(console.paint(i18n.tr(f"[-] No supported HTTPS installer found for '{wg_id}'.", f"[-] Không tìm thấy bộ cài HTTPS hợp lệ cho '{wg_id}'."), "red"))
            continue

        draft = wg.convert_winget_to_draft(data, namespace=namespace, override_id=getattr(args, "id", None))
        errors = wg.validate_draft(draft)
        if errors:
            print(console.paint(i18n.tr(f"[-] Validation failed for {draft.package_id}:", f"[-] Kiểm tra thất bại cho {draft.package_id}:"), "red"))
            for err in errors:
                print(f"    - {err}")
            continue

        dest_dir = wg.save_draft_manifests(draft, manifest_root, dry_run=getattr(args, "dry_run", False))
        if getattr(args, "dry_run", False):
            print(console.paint(f"[DRY-RUN] {wg_id} -> {draft.ref} ({dest_dir})", "yellow"))
        else:
            print(console.paint(f"[+] {wg_id} -> {draft.ref}", "green"))
            print(f"    {i18n.tr('Location:', 'Thư mục: ')} {dest_dir}")
            print(f"    {i18n.tr('Targets: ', 'Kiến trúc:')} {', '.join(t.arch + ' (' + t.engine + ')' for t in draft.targets)}")
            if draft.alias:
                print(f"    {i18n.tr('Alias:   ', 'Lệnh cài:')} txapm install {draft.alias}")
            created_dirs.append(dest_dir)

    if getattr(args, "build_api", False) and created_dirs and not getattr(args, "dry_run", False):
        do_build_api(manifest_root, getattr(args, "api_out", "dist/api"))

    if getattr(args, "add_source", None) and created_dirs and not getattr(args, "dry_run", False):
        from txa.pm.cli import main as txapm_main

        src_name = args.add_source
        txapm_main(["source", "add", src_name, str(manifest_root)])

    if getattr(args, "submit", False) and created_dirs and not getattr(args, "dry_run", False):
        from txa.create.cli import main as txacreate_main

        for d in created_dirs:
            txacreate_main(["submit", str(d), *(["--direct"] if getattr(args, "direct", False) else [])])

    return 0 if created_dirs or getattr(args, "dry_run", False) else 1


def main(argv: list[str] | None = None) -> int:
    console.setup()
    args_list = list(sys.argv[1:] if argv is None else argv)

    # Support explicit subcommands cleanly while keeping flag-based invocation 100% compatible
    if args_list:
        first = args_list[0].lower()
        if first in ("search", "find", "s") and len(args_list) >= 2 and not args_list[1].startswith("-"):
            return do_search(" ".join(args_list[1:]))
        if first in ("scan", "installed", "ls") and len(args_list) == 1:
            do_scan(_resolve_repo_root(None))
            return 0
        if first in ("submit", "pr", "push"):
            from txa.create.cli import main as txacreate_main

            return txacreate_main(["submit", *args_list[1:]])
        if first in ("validate", "check"):
            from txa.ci.cli import main as txaci_main

            return txaci_main(["validate", *args_list[1:]])
        if first in ("build-api", "api"):
            ap = argparse.ArgumentParser(prog="txacli build-api", description="Build static txa.v1 REST API")
            ap.add_argument("-r", "--repo", default=None, help="Repository root containing manifests/ (default: current repo)")
            ap.add_argument("-o", "--api-out", default="dist/api", help="Output folder for built API (default: dist/api)")
            parsed = ap.parse_args(args_list[1:])
            return do_build_api(_resolve_repo_root(parsed.repo), parsed.api_out)
        if first in ("import", "add", "in"):
            args_list = args_list[1:]

    parser = console.SmartArgumentParser(
        prog="txacli",
        description="TXA Fast CLI: WinGet importer, scanner, API builder, and PR submitter.",
        epilog=(
            "examples:\n"
            "  txacli -s PowerToys\n"
            "  txacli search \"GitHub CLI\"\n"
            "  txacli Microsoft.PowerToys GitHub.cli\n"
            "  txacli -i -y\n"
            "  txacli scan\n"
            "  txacli Postman.Postman -o C:\\my-txa-repo --build-api --api-out C:\\my-txa-api\n"
            "  txacli submit C:\\my-txa-repo\n"
            "  txacli validate C:\\my-txa-repo --offline"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"txacli {__version__}")
    parser.add_argument("packages", nargs="*", help="App names (from `txapm list --all`) or WinGet Package IDs")
    parser.add_argument("-i", "--from-installed", action="store_true",
                        help="Scan installed apps on this PC (like `txapm list --all`) and import missing ones")
    parser.add_argument("--txacreate", action="store_true",
                        help="Run full `txacreate new` pipeline (downloads binary, verifies SHA-256 & PE)")
    parser.add_argument("-s", "--search", help="Search WinGet for matching package names/monikers")
    parser.add_argument("-n", "--namespace", default="TXAVL", help="Namespace for TXA packages (default: TXAVL)")
    parser.add_argument("--id", help="Override TXA Package ID (e.g. TXAVL.MyTool)")
    parser.add_argument("-o", "--out", default=None, help="Output repo directory root (default: current repo or ./manifests)")
    parser.add_argument("--build-api", action="store_true", help="Build static txa.v1 REST API after import")
    parser.add_argument("--api-out", default="dist/api", help="Destination directory for built API (default: dist/api)")
    parser.add_argument("--add-source", metavar="NAME", help="Automatically add/register output repo in `txapm source`")
    parser.add_argument("--submit", action="store_true", help="Open a pull request with `txacreate submit` after importing")
    parser.add_argument("--direct", action="store_true", help="With --submit: push branch directly to upstream")
    parser.add_argument("--dry-run", action="store_true", help="Preview generated manifests without writing to disk")
    parser.add_argument("-y", "--yes", action="store_true", help="Automatically import all without prompting")
    args = parser.parse_args(args_list)

    if args.search:
        return do_search(args.search)

    if not args.packages and not args.from_installed:
        parser.print_help()
        return 0

    return do_import(args)


if __name__ == "__main__":
    sys.exit(main())

