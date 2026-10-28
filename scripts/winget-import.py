#!/usr/bin/env python3
"""WinGet to TXA Package Converter.

Imports packages from Windows Package Manager (WinGet) into TXA manifest repository.
Supports:
  1. WinGet CLI lookup: fetch package metadata and installer targets (x64, arm64, x86).
  2. Local WinGet manifest directory parsing (winget-pkgs YAML files).
  3. Strict TXA schema validation and version normalization.
  4. Automatic static API generation via `txaci build-api`.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
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

from txa.core import console, engines, licenses, semver
from txa.core.manifest import (
    BUILD_FILE, CORE_FILE, SCHEMA_URL, Bundle, about_file, dump_card, ref_of, release_dir, schema_errors, split_id
)
from txa.create.draft import Draft, TargetDraft, pascal

DEFAULT_SWITCHES = {
    "inno": {"silent": "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-", "progress": "/SILENT /SUPPRESSMSGBOXES /NORESTART /SP-"},
    "nsis": {"silent": "/S", "progress": "/S"},
    "msi": {"silent": "/qn /norestart", "progress": "/qb! /norestart"},
    "wix": {"silent": "/quiet /norestart", "progress": "/passive /norestart"},
    "exe": {"silent": "/S", "progress": "/S"},
}

ENGINE_MAP = {
    "inno": "inno",
    "nullsoft": "nsis",
    "nsis": "nsis",
    "msi": "msi",
    "wix": "wix",
    "burn": "wix",
    "zip": "portable",
    "portable": "portable",
    "exe": "exe",
}

_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")
_TOP_KEY_RE = re.compile(r"^([A-Za-z0-9 ]+):\s*(.*)$")


def clean_tag(tag: str) -> str | None:
    t = re.sub(r"[^a-z0-9-]", "", str(tag).strip().lower())
    t = t.strip("-")
    if 2 <= len(t) <= 32 and _TAG_RE.match(t):
        return t
    return None


def normalize_version(v: str) -> str:
    """Coerce any version into strict X.Y.Z semver."""
    if not v:
        return "1.0.0"
    v_clean = re.sub(r"^[vV]", "", str(v).strip())
    parts = re.findall(r"\d+", v_clean)
    if not parts:
        return "1.0.0"
    if len(parts) == 1:
        return f"{int(parts[0])}.0.0"
    if len(parts) == 2:
        return f"{int(parts[0])}.{int(parts[1])}.0"
    return f"{int(parts[0])}.{int(parts[1])}.{int(parts[2])}"


def sanitize_alias(moniker: str | None) -> str | None:
    if not moniker:
        return None
    alias = re.sub(r"[^a-z0-9-]", "", moniker.strip().lower()).strip("-")
    if 2 <= len(alias) <= 40 and re.match(r"^[a-z0-9][a-z0-9-]{1,39}$", alias):
        return alias
    return None


def parse_winget_cli_output(text: str) -> dict | None:
    """Parse output from `winget show` robustly even with colons in description text."""
    lines = text.strip().splitlines()
    if not lines:
        return None

    pkg_name = None
    pkg_id = None
    first = lines[0].strip()
    match = re.search(r"Found\s+(.+?)\s+\[([^\]]+)\]", first)
    if match:
        pkg_name = match.group(1).strip()
        pkg_id = match.group(2).strip()
        body_lines = lines[1:]
    else:
        body_lines = lines

    # Parse top-level sections
    sections: dict[str, list[str]] = {}
    current_key: str | None = None
    current_lines: list[str] = []

    for line in body_lines:
        m = _TOP_KEY_RE.match(line)
        if m and not line.startswith(" "):
            if current_key:
                sections[current_key] = current_lines
            current_key = m.group(1).strip()
            rest = m.group(2).strip()
            current_lines = [rest] if rest else []
        else:
            if current_key is not None:
                current_lines.append(line)

    if current_key:
        sections[current_key] = current_lines

    data: dict[str, Any] = {}
    if pkg_name:
        data["PackageName"] = pkg_name
    if pkg_id:
        data["PackageIdentifier"] = pkg_id

    # Scalar fields
    for k in ["Version", "Publisher", "Publisher Url", "Publisher Support Url", "Author", "Moniker",
              "Homepage", "License", "License Url", "Copyright"]:
        if k in sections and sections[k]:
            data[k] = sections[k][0]

    # Description (join multi-line text)
    if "Description" in sections:
        desc_lines = [l.strip() for l in sections["Description"] if l.strip()]
        data["Description"] = " ".join(desc_lines)

    # Tags (list)
    if "Tags" in sections:
        tags = [l.strip().lstrip("-").strip() for l in sections["Tags"] if l.strip()]
        data["Tags"] = tags

    # Installer section (parsed with YAML since it is strict key-value)
    if "Installer" in sections:
        inst_block = "\n".join(sections["Installer"])
        try:
            inst_dict = yaml.safe_load(inst_block)
            if isinstance(inst_dict, dict):
                data["Installer"] = inst_dict
        except Exception:
            pass

    return data


def query_winget_cli(package_id: str, arches: list[str] = ("x64", "arm64", "x86")) -> dict | None:
    """Query winget CLI across requested CPU architectures and combine targets."""
    combined: dict[str, Any] = {}
    targets_by_arch: dict[str, dict] = {}

    for arch in arches:
        cmd = ["winget", "show", "--id", package_id, "-e", "-a", arch, "-s", "winget", "--disable-interactivity", "--nowarn"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=20)
        except Exception:
            continue
        if res.returncode != 0 or not res.stdout:
            continue

        parsed = parse_winget_cli_output(res.stdout)
        if not parsed:
            continue

        if not combined:
            combined = parsed

        inst = parsed.get("Installer")
        if isinstance(inst, dict):
            url = inst.get("Installer Url")
            sha = inst.get("Installer SHA256")
            if url and sha:
                inst_type = str(inst.get("Installer Type", "exe")).lower()
                targets_by_arch[arch] = {
                    "arch": arch,
                    "url": str(url).strip(),
                    "sha256": str(sha).strip().upper(),
                    "type": inst_type,
                    "switches": inst.get("Installer Switches") or {},
                }

    if not combined or not targets_by_arch:
        return None

    combined["_targets"] = targets_by_arch
    return combined


def search_winget(query: str, limit: int = 15) -> list[dict]:
    """Search winget packages matching query."""
    cmd = ["winget", "search", query, "-s", "winget", "--disable-interactivity", "--nowarn"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=25)
    except Exception as e:
        print(f"Error searching winget: {e}")
        return []

    results = []
    lines = res.stdout.strip().splitlines()
    header_idx = -1
    for i, line in enumerate(lines):
        if "Name" in line and "Id" in line:
            header_idx = i
            break
    if header_idx != -1 and header_idx + 2 < len(lines):
        for line in lines[header_idx + 2:]:
            parts = re.split(r"\s{2,}", line.strip())
            if len(parts) >= 2:
                name, pkg_id = parts[0], parts[1]
                version = parts[2] if len(parts) > 2 else ""
                results.append({"name": name, "id": pkg_id, "version": version})
                if len(results) >= limit:
                    break
    return results


def convert_winget_to_draft(data: dict, namespace: str = "TXAVL", override_id: str | None = None) -> Draft:
    """Convert winget metadata dictionary into a TXA Draft object."""
    pkg_id_raw = data.get("PackageIdentifier") or data.get("Id") or "App.Tool"
    raw_name = data.get("PackageName") or data.get("Name") or pkg_id_raw.split(".")[-1]
    raw_version = str(data.get("Version") or data.get("PackageVersion") or "1.0.0")

    version = normalize_version(raw_version)

    if override_id:
        pkg_id = override_id
    else:
        app_part = pkg_id_raw.split(".")[-1]
        pascal_app = pascal(app_part)
        digits = {"0": "Zero", "1": "One", "2": "Two", "3": "Three", "4": "Four", "5": "Five", "6": "Six", "7": "Seven", "8": "Eight", "9": "Nine"}
        if pascal_app and pascal_app[0] in digits:
            pascal_app = digits[pascal_app[0]] + pascal_app[1:]
        pascal_app = re.sub(r"^[^A-Za-z0-9]+", "", pascal_app) or "App"
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
        import textwrap
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
        lic_url = homepage if (homepage and lic_name in licenses.NEEDS_URL) else None

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
        ct = clean_tag(t)
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
        winget_type = tinfo.get("type", "exe").lower()
        engine = ENGINE_MAP.get(winget_type, "exe")

        switches = dict(DEFAULT_SWITCHES.get(engine, {}))
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

    draft = Draft(
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
    return draft


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
    parser = argparse.ArgumentParser(description="Import WinGet packages into TXA repository.")
    parser.add_argument("packages", nargs="*", help="WinGet Package IDs (e.g. 7zip.7zip, Git.Git)")
    parser.add_argument("-s", "--search", help="Search WinGet for matching package names/monikers")
    parser.add_argument("-n", "--namespace", default="TXAVL", help="Namespace for TXA packages (default: TXAVL)")
    parser.add_argument("--id", help="Override TXA Package ID (e.g. TXAVL.MyTool)")
    parser.add_argument("-o", "--out", default="manifests", help="Output directory root (default: ./manifests or repo root)")
    parser.add_argument("--build-api", action="store_true", help="Build static txa.v1 REST API after import")
    parser.add_argument("--api-out", default="dist/api", help="Destination directory for built API (default: dist/api)")
    parser.add_argument("--dry-run", action="store_true", help="Preview generated manifests without writing to disk")
    args = parser.parse_args()

    repo_root = REPO_ROOT

    # Search mode
    if args.search:
        print(f"\nSearching WinGet for '{console.paint(args.search, 'cyan')}'...")
        results = search_winget(args.search)
        if not results:
            print("No packages found.")
            return
        print(f"\nFound {len(results)} package(s):")
        print(f"{'Id':<40} {'Version':<15} {'Name'}")
        print("-" * 75)
        for r in results:
            print(f"{console.paint(r['id'], 'green'):<49} {r['version']:<15} {r['name']}")
        print(f"\nTo import a package, run:\n  python scripts/winget-import.py {results[0]['id']}\n")
        return

    if not args.packages:
        parser.print_help()
        print("\nTip: Run with --search <keyword> to discover WinGet package IDs.")
        return

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = (repo_root / args.out).resolve()

    if out_dir.name == "manifests":
        manifest_root = out_dir.parent
    else:
        manifest_root = out_dir

    success_count = 0
    imported_ids = []

    for pkg_id in args.packages:
        print(f"\nFetching WinGet package '{console.paint(pkg_id, 'cyan')}'...")
        data = query_winget_cli(pkg_id)
        if not data:
            print(console.paint(f"[-] Could not find or retrieve installer info for '{pkg_id}'.", "red"))
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
            print(console.paint(f"[DRY-RUN] Validated successfully -> {dest_dir}", "yellow"))
            for fn, content in draft.render().items():
                print(f"\n--- {fn} ---")
                print(content[:300] + "...")
        else:
            print(console.paint(f"[+] Successfully converted -> {draft.ref}", "green"))
            print(f"    Location: {dest_dir}")
            print(f"    Targets:  {', '.join(t.arch + ' (' + t.engine + ')' for t in draft.targets)}")
            if draft.alias:
                print(f"    Alias:    txapm install {draft.alias}")
            imported_ids.append(draft.package_id)
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
        print(f"\nTo use this repository in txapm, run:")
        print(f"  txapm source add myrepo {manifest_root}")
        print(f"  txapm search -s myrepo")


if __name__ == "__main__":
    main()
