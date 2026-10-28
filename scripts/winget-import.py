#!/usr/bin/env python3
"""WinGet to TXA Package Converter & Auto-Creator.

Imports packages from Windows Package Manager (WinGet) into TXA manifest repository,
or feeds them directly into `txacreate new`.
Supports:
  1. Arbitrary app names from `txapm list --all` (e.g. "GitHub CLI", "Unity Hub 3.22.2") or WinGet IDs.
  2. `--from-installed` (`-i`): automatically scan installed programs on the PC not yet in TXA.
  3. `--txacreate`: run `txacreate new` automatically (full binary download, SHA-256 & PE inspection).
  4. Fast metadata conversion into `manifests/` with 14-step validation compliance.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import textwrap
from pathlib import Path
from typing import Any

# Ensure tools/src is in sys.path for txa package resolution
REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_SRC = REPO_ROOT / "tools" / "src"
if str(TOOLS_SRC) not in sys.path:
    sys.path.insert(0, str(TOOLS_SRC))

# Auto re-exec in workspace venv if txa or yaml is missing
try:
    import yaml
    from jsonschema import Draft202012Validator
    import txa
except ImportError:
    venv_py = REPO_ROOT / "tools" / ".venv" / "Scripts" / "python.exe"
    if venv_py.is_file() and sys.executable.lower() != str(venv_py).lower():
        os.execv(str(venv_py), [str(venv_py), *sys.argv])
    else:
        sys.exit("Error: Required dependencies (yaml, jsonschema, txa) not found. Run in tools/.venv.")

from txa.core import console, licenses
from txa.core.manifest import BUILD_FILE, CORE_FILE, about_file, schema_errors
from txa.create import winget as wg
from txa.create.draft import Draft, TargetDraft, pascal


def sanitize_alias(moniker: str | None) -> str | None:
    if not moniker:
        return None
    alias = re.sub(r"[^a-z0-9-]", "", moniker.strip().lower()).strip("-")
    if 2 <= len(alias) <= 40 and re.match(r"^[a-z0-9][a-z0-9-]{1,39}$", alias):
        return alias
    return None


def convert_winget_to_draft(data: dict[str, Any], namespace: str = "TXAVL", override_id: str | None = None) -> Draft:
    """Convert WinGet metadata dictionary into a TXA Draft object."""
    pkg_id_raw = data.get("PackageIdentifier") or data.get("Id") or "App.Tool"
    raw_name = data.get("PackageName") or data.get("Name") or pkg_id_raw.split(".")[-1]
    raw_version = str(data.get("Version") or data.get("PackageVersion") or "1.0.0")

    version = wg.normalize_version(raw_version)

    if override_id:
        pkg_id = override_id
    else:
        pascal_app = wg.derive_txa_app_name(pkg_id_raw, raw_name)
        pkg_id = f"{namespace}.{pascal_app}"

    name = (raw_name.strip() or "Tool")[:64]
    if len(name) < 2:
        name = f"{name} App"

    pub = str(data.get("Publisher") or data.get("Author") or namespace).strip()[:128]
    if len(pub) < 2:
        pub = f"{pub} Team"
    author = str(data.get("Author") or pub).strip()[:128]
    if len(author) < 2:
        author = pub

    summary = str(data.get("ShortDescription") or data.get("Description") or f"{name} package for Windows.").strip()
    summary = re.sub(r"\s+", " ", summary)
    if len(summary) < 10:
        summary = f"{name} official installer for Windows."
    if len(summary) > 160:
        cutoff = summary[:157].rfind(" ")
        summary = summary[:cutoff if cutoff > 50 else 157] + "..."

    description = str(data.get("Description") or "").strip()[:4000] or None
    if description and summary and (description.lower() == summary.lower() or len(description) <= len(summary)):
        description = None
    elif description:
        wrapped_lines = []
        for paragraph in description.split("\n"):
            p = paragraph.strip()
            if p:
                wrapped_lines.append(textwrap.fill(p, width=120))
        description = "\n".join(wrapped_lines) if wrapped_lines else None

    homepage = str(data.get("Homepage") or data.get("Publisher Url") or "").strip()
    if not homepage.startswith("https://"):
        homepage = None

    raw_lic = str(data.get("License") or "").strip()
    norm_lic = licenses.normalize(raw_lic) if raw_lic else None
    if norm_lic and (norm_lic in licenses.SPDX or norm_lic in licenses.NEEDS_URL):
        lic_name = norm_lic
    elif "mit" in raw_lic.lower():
        lic_name = "MIT"
    elif "apache" in raw_lic.lower():
        lic_name = "Apache-2.0"
    elif "gpl" in raw_lic.lower():
        lic_name = "GPL-3.0-or-later"
    elif "bsd" in raw_lic.lower():
        lic_name = "BSD-3-Clause"
    else:
        lic_name = "Proprietary"

    lic_url = str(data.get("License Url") or "").strip()
    if not lic_url.startswith("https://"):
        lic_url = homepage if (homepage and lic_name in licenses.NEEDS_URL) else (
            "https://github.com/TXAVL/txa-repo" if lic_name in licenses.NEEDS_URL else None
        )

    moniker = data.get("Moniker")
    alias = sanitize_alias(moniker)
    if not alias:
        short = sanitize_alias(pkg_id.split(".")[-1])
        if short and short != pkg_id.lower():
            alias = short

    raw_tags = data.get("Tags") or []
    if isinstance(raw_tags, str):
        raw_tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
    cleaned_tags = []
    for t in raw_tags:
        ct = wg.clean_tag(t)
        if ct and ct not in cleaned_tags:
            cleaned_tags.append(ct)
        if len(cleaned_tags) >= 16:
            break

    targets_data = data.get("_targets") or {}
    target_drafts = []

    for arch, tinfo in targets_data.items():
        url = tinfo["url"]
        if not url.startswith("https://"):
            continue
        sha = tinfo["sha256"]
        engine = tinfo.get("engine") or wg.ENGINE_MAP.get(tinfo.get("type", "exe").lower(), "exe")

        switches = dict(wg.DEFAULT_SWITCHES.get(engine, {}))
        custom_sw = tinfo.get("switches", {})
        if isinstance(custom_sw, dict):
            if custom_sw.get("Silent"):
                switches["silent"] = str(custom_sw["Silent"]).strip()
            if custom_sw.get("SilentWithProgress"):
                switches["progress"] = str(custom_sw["SilentWithProgress"]).strip()
            if custom_sw.get("Custom"):
                switches["custom"] = str(custom_sw["Custom"]).strip()

        portable_dict = {}
        if engine == "portable":
            portable_dict = {"path": ["."]}

        target_drafts.append(TargetDraft(
            url=url,
            arch=arch,
            engine=engine,
            sha256=sha,
            size=1,
            payload=Path(""),
            switches=switches,
            portable=portable_dict,
        ))

    return Draft(
        package_id=pkg_id,
        version=version,
        name=name,
        publisher=pub,
        author=author,
        summary=summary,
        description=description,
        homepage=homepage,
        license_name=lic_name,
        license_url=lic_url,
        alias=alias,
        tags=cleaned_tags,
        targets=target_drafts,
    )


def validate_draft(draft: Draft) -> list[str]:
    """Validate Draft against TXA JSON schemas."""
    cards = draft.cards()
    errors = []
    for kind, card in [("core", cards[CORE_FILE]), ("build", cards[BUILD_FILE]), ("about", cards[about_file(draft.lang)])]:
        errs = schema_errors(kind, card)
        for e in errs:
            errors.append(f"{kind}: {e}")
    return errors


def save_draft_manifests(draft: Draft, root_dir: Path, dry_run: bool = False) -> Path:
    """Save rendered manifests to <root_dir>/manifests/<shard>/<Namespace>/<AppName>/<Version>/ with LF endings."""
    rel_path = draft.directory
    target_dir = root_dir / rel_path
    if dry_run:
        return target_dir

    target_dir.mkdir(parents=True, exist_ok=True)
    rendered = draft.render()
    for filename, content in rendered.items():
        content_lf = content.replace("\r\n", "\n")
        (target_dir / filename).write_text(content_lf, encoding="utf-8", newline="\n")
    return target_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Import WinGet packages or installed apps into TXA repository.")
    parser.add_argument("packages", nargs="*", help="App names (from `txapm list --all`) or WinGet Package IDs")
    parser.add_argument("-i", "--from-installed", action="store_true",
                        help="Scan installed apps on this PC (like `txapm list --all`) and import missing ones")
    parser.add_argument("--txacreate", action="store_true",
                        help="Run full `txacreate new` pipeline (downloads binary, verifies SHA-256 & PE)")
    parser.add_argument("-s", "--search", help="Search WinGet for matching package names/monikers")
    parser.add_argument("-n", "--namespace", default="TXAVL", help="Namespace for TXA packages (default: TXAVL)")
    parser.add_argument("--id", help="Override TXA Package ID (e.g. TXAVL.MyTool)")
    parser.add_argument("-o", "--out", default="manifests", help="Output directory root (default: ./manifests or repo root)")
    parser.add_argument("--build-api", action="store_true", help="Build static txa.v1 REST API after import")
    parser.add_argument("--api-out", default="dist/api", help="Destination directory for built API (default: dist/api)")
    parser.add_argument("--dry-run", action="store_true", help="Preview generated manifests without writing to disk")
    parser.add_argument("-y", "--yes", action="store_true", help="Automatically import all without prompting")
    args = parser.parse_args()

    repo_root = REPO_ROOT

    # If --txacreate is requested, delegate directly to txacreate CLI
    if args.txacreate:
        from txa.create.cli import main as txacreate_main
        cli_args = ["new", "--out", str(repo_root), "-y"]
        if args.dry_run:
            cli_args.append("--dry-run")
        if args.from_installed:
            cli_args.append("--from-installed")
        if args.id:
            cli_args += ["--id", args.id]
        cli_args += list(args.packages)
        sys.exit(txacreate_main(cli_args))

    # Search mode
    if args.search:
        print(f"\nSearching WinGet for '{console.paint(args.search, 'cyan')}'...")
        results = wg.search_winget(args.search)
        if not results:
            print(console.paint("No packages found.", "yellow"))
            return
        print(f"\nFound {len(results)} package(s):")
        print(f"  {'Id':<42} {'Version':<16} {'Name'}")
        print("  " + "-" * 78)
        for r in results:
            print(f"  {r['id']:<42} {r['version']:<16} {r['name']}")
        print(f"\nTo import a package, run:\n  .\\scripts\\winget-import.ps1 {results[0]['id']}")
        return

    queries = list(args.packages)
    if args.from_installed:
        print("\nScanning installed programs on this PC not yet in TXA...")
        untracked = wg.scan_installed_winget_packages()
        if not untracked:
            print(console.paint("[+] All detected WinGet programs on this machine are already in TXA!", "green"))
            return
        print(f"\nFound {len(untracked)} installed program(s) available on WinGet:")
        print(f"{'#':<4} {'Installed Name':<38} {'WinGet Id':<36} {'Version'}")
        print("-" * 92)
        for idx, item in enumerate(untracked, 1):
            print(f"{idx:<4} {item['name'][:37]:<38} {console.paint(item['id'], 'cyan'):<45} {item['version']}")

        if args.yes or not sys.stdin.isatty():
            queries.extend(item["id"] for item in untracked)
        else:
            choice = console.ask("\nEnter numbers to import (e.g. 1,3,5 or 'all')", "all").strip().lower()
            if choice == "all":
                queries.extend(item["id"] for item in untracked)
            else:
                idxs = [int(x.strip()) for x in choice.split(",") if x.strip().isdigit()]
                queries.extend(untracked[i - 1]["id"] for i in idxs if 1 <= i <= len(untracked))

    if not queries:
        parser.print_help()
        print("\nExamples:")
        print("  .\\scripts\\winget-import.ps1 --from-installed -y")
        print("  .\\scripts\\winget-import.ps1 \"GitHub CLI\" \"GitHub Desktop\" \"Steam\"")
        print("  txacreate new \"GitHub CLI\" --dry-run")
        print("  txacreate new --from-installed --dry-run")
        return

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = (repo_root / args.out).resolve()
    manifest_root = out_dir.parent if out_dir.name == "manifests" else out_dir

    success_count = 0
    for query in queries:
        print(f"\nResolving '{console.paint(query, 'cyan')}'...")
        wg_id = wg.resolve_to_winget_id(query)
        if not wg_id:
            print(console.paint(f"[-] Could not resolve '{query}' on WinGet.", "red"))
            continue
        data = wg.query_winget_package(wg_id)
        if not data:
            print(console.paint(f"[-] No supported HTTPS installer found for '{wg_id}'.", "red"))
            continue

        draft = convert_winget_to_draft(data, namespace=args.namespace, override_id=args.id)
        errors = validate_draft(draft)
        if errors:
            print(console.paint(f"[-] Validation failed for {draft.package_id}:", "red"))
            for err in errors:
                print(f"    - {err}")
            continue

        dest_dir = save_draft_manifests(draft, manifest_root, dry_run=args.dry_run)
        if args.dry_run:
            print(console.paint(f"[DRY-RUN] {wg_id} -> {draft.ref} ({dest_dir})", "yellow"))
        else:
            print(console.paint(f"[+] {wg_id} -> {draft.ref}", "green"))
            print(f"    Location: {dest_dir}")
            print(f"    Targets:  {', '.join(t.arch + ' (' + t.engine + ')' for t in draft.targets)}")
            if draft.alias:
                print(f"    Alias:    txapm install {draft.alias}")
            success_count += 1

    if args.build_api and success_count > 0 and not args.dry_run:
        print(f"\nBuilding txa.v1 REST API into '{args.api_out}'...")
        from txa.ci.api import build_api
        api_target = Path(args.api_out)
        if not api_target.is_absolute():
            api_target = (repo_root / args.api_out).resolve()
        api_target.mkdir(parents=True, exist_ok=True)
        stats = build_api(manifest_root, api_target)
        print(console.paint(f"[+] API build complete: {stats['packages']} packages indexed.", "green"))


if __name__ == "__main__":
    main()
