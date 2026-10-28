"""WinGet bridge for ``txacreate`` and ``winget-import``.

Resolves package names or WinGet IDs (including installed programs from ``txapm list --all``)
into official HTTPS installer URLs and metadata so ``txacreate new`` can run automatically
without manual URL hunting.
"""
from __future__ import annotations

import re
import subprocess
from typing import Any

import yaml

from txa.core import config, licenses

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

DEFAULT_SWITCHES = {
    "inno": {"silent": "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-", "progress": "/SILENT /SUPPRESSMSGBOXES /NORESTART /SP-"},
    "nsis": {"silent": "/S", "progress": "/S"},
    "msi": {"silent": "/qn /norestart", "progress": "/qb! /norestart"},
    "wix": {"silent": "/quiet /norestart", "progress": "/passive /norestart"},
    "exe": {"silent": "/S", "progress": "/S"},
}

_TOP_KEY_RE = re.compile(r"^([A-Za-z0-9 ]+):\s*(.*)$")
_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")

# Skip system runtimes / store extensions / Steam game entries when scanning installed programs
_SKIP_ID_PREFIXES = (
    "ARP\\",
    "MSIX\\",
    "Microsoft.VCRedist.",
    "Microsoft.VCLibs.",
    "Microsoft.UI.Xaml.",
    "Microsoft.WindowsAppRuntime.",
    "Microsoft.DotNet.Native.",
    "Microsoft.CLRTypesSQLServer.",
    "Microsoft.AppInstaller",
    "Python.Launcher",
    "Python.PythonInstallManager",
)

_GENERIC_APP_PARTS = {
    "cli", "launcher", "connect", "community", "professional", "enterprise",
    "desktop", "client", "runtime", "sdk", "server", "agent", "installer",
    "manager", "hub", "studio", "code", "app", "player", "viewer",
}


def derive_txa_app_name(pkg_id_raw: str, raw_name: str) -> str:
    """Derive a clean, non-colliding PascalCase AppName from WinGet ID and display name."""
    from txa.create.draft import pascal

    parts = [p for p in pkg_id_raw.split(".") if p]
    app_part = parts[-1] if parts else "App"
    if len(parts) >= 3 or len(app_part) <= 3 or app_part.isdigit() or app_part.lower() in _GENERIC_APP_PARTS:
        if len(parts) == 2 and parts[0].lower() != parts[1].lower():
            candidate = f"{parts[0]} {parts[1]}"
        else:
            candidate = clean_query_name(raw_name)
    else:
        candidate = app_part
    pascal_app = pascal(candidate)
    digits = {"0": "Zero", "1": "One", "2": "Two", "3": "Three", "4": "Four", "5": "Five", "6": "Six", "7": "Seven", "8": "Eight", "9": "Nine"}
    if pascal_app and pascal_app[0] in digits:
        pascal_app = digits[pascal_app[0]] + pascal_app[1:]
    return re.sub(r"^[^A-Za-z0-9]+", "", pascal_app)[:48] or "App"


def clean_query_name(name: str) -> str:
    """Strip version suffixes and parentheticals from installed program display names."""
    s = name.strip()
    s = re.sub(r"\s*\((?:User|Machine|x64|x86|64-bit|32-bit|remove only)\)\s*", " ", s, flags=re.I)
    s = re.sub(r"\s+(?:version\s+)?v?\d+(?:\.\d+)+[^\s]*.*$", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip()


def normalize_version(v: str) -> str:
    """Coerce any version string into strict X.Y.Z semver."""
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


def clean_tag(tag: str) -> str | None:
    t = re.sub(r"[^a-z0-9-]", "", str(tag).strip().lower()).strip("-")
    if 2 <= len(t) <= 32 and _TAG_RE.match(t):
        return t
    return None


def parse_winget_show(text: str) -> dict[str, Any] | None:
    """Parse output of ``winget show`` robustly."""
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

    for k in ["Version", "Publisher", "Publisher Url", "Publisher Support Url", "Author", "Moniker",
              "Homepage", "License", "License Url", "Copyright"]:
        if k in sections and sections[k]:
            data[k] = sections[k][0]

    if "Description" in sections:
        desc_lines = [l.strip() for l in sections["Description"] if l.strip()]
        data["Description"] = " ".join(desc_lines)

    if "Tags" in sections:
        tags = [l.strip().lstrip("-").strip() for l in sections["Tags"] if l.strip()]
        data["Tags"] = tags

    if "Installer" in sections:
        inst_block = "\n".join(sections["Installer"])
        try:
            inst_dict = yaml.safe_load(inst_block)
            if isinstance(inst_dict, dict):
                data["Installer"] = inst_dict
        except Exception:
            pass

    return data


def query_winget_package(package_id: str, arches: tuple[str, ...] = ("x64", "arm64", "x86")) -> dict[str, Any] | None:
    """Query WinGet CLI across CPU architectures and combine installer targets."""
    combined: dict[str, Any] = {}
    targets_by_arch: dict[str, dict[str, Any]] = {}

    def _fetch_one(arch: str, inst_type_flag: str | None = None) -> dict[str, Any] | None:
        cmd = ["winget", "show", "--id", package_id, "-e", "-a", arch, "-s", "winget", "--disable-interactivity", "--nowarn"]
        if inst_type_flag:
            cmd += ["--installer-type", inst_type_flag]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=20)
        except Exception:
            return None
        if res.returncode != 0 or not res.stdout:
            return None
        return parse_winget_show(res.stdout)

    for arch in arches:
        parsed = _fetch_one(arch)
        if not parsed:
            continue
        if not combined:
            combined = parsed

        inst = parsed.get("Installer")
        if isinstance(inst, dict):
            inst_type = str(inst.get("Installer Type", "exe")).lower()
            # If default installer is msix/appx, try traditional Win32 installer types
            if inst_type in ("msix", "appx", "msstore"):
                for alt_type in ("nullsoft", "inno", "wix", "msi", "exe", "burn", "zip", "portable"):
                    alt_parsed = _fetch_one(arch, alt_type)
                    if alt_parsed and isinstance(alt_parsed.get("Installer"), dict):
                        inst = alt_parsed["Installer"]
                        inst_type = str(inst.get("Installer Type", alt_type)).lower()
                        break

            url = str(inst.get("Installer Url") or "").strip()
            sha = str(inst.get("Installer SHA256") or "").strip().upper()
            if url.startswith("https://") and len(sha) == 64 and inst_type not in ("msix", "appx", "msstore"):
                targets_by_arch[arch] = {
                    "arch": arch,
                    "url": url,
                    "sha256": sha,
                    "type": inst_type,
                    "engine": ENGINE_MAP.get(inst_type, "exe"),
                    "switches": inst.get("Installer Switches") or {},
                }

    if not combined or not targets_by_arch:
        return None

    combined["_targets"] = targets_by_arch
    return combined


def _parse_winget_table(stdout: str) -> list[dict[str, str]]:
    """Parse fixed-width table output from ``winget search`` or ``winget list`` using header column offsets."""
    lines = stdout.splitlines()
    header_idx = -1
    for i, line in enumerate(lines):
        if "Name" in line and "Id" in line and "Version" in line:
            header_idx = i
            break
    if header_idx == -1 or header_idx + 2 >= len(lines):
        return []

    header = lines[header_idx]
    id_col = header.find("Id")
    ver_col = header.find("Version")
    avail_col = header.find("Available")
    src_col = header.find("Source")

    rows: list[dict[str, str]] = []
    for line in lines[header_idx + 2:]:
        if not line.strip() or line.startswith("---"):
            continue
        name = line[:id_col].strip()
        pkg_id = line[id_col:ver_col].strip() if ver_col > id_col else line[id_col:].strip()
        end_ver = avail_col if avail_col > ver_col else (src_col if src_col > ver_col else len(line))
        version = line[ver_col:end_ver].strip() if ver_col != -1 else ""
        source = line[src_col:].strip() if src_col != -1 and len(line) > src_col else ""
        if name and pkg_id:
            rows.append({"name": name, "id": pkg_id, "version": version, "source": source})
    return rows


def resolve_to_winget_id(query: str) -> str | None:
    """Resolve an arbitrary app name or WinGet ID to an exact WinGet PackageIdentifier."""
    q = query.strip()
    if not q:
        return None

    # 1. If it looks like a Publisher.App ID, try exact ID match first
    if "." in q and " " not in q:
        cmd = ["winget", "show", "--id", q, "-e", "-s", "winget", "--disable-interactivity", "--nowarn"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=15)
            if res.returncode == 0 and "Found " in res.stdout:
                m = re.search(r"Found\s+.+?\s+\[([^\]]+)\]", res.stdout.splitlines()[0])
                return m.group(1).strip() if m else q
        except Exception:
            pass

    # 2. Search winget by query (and cleaned query if it has version numbers)
    candidates = [q]
    cleaned = clean_query_name(q)
    if cleaned and cleaned.lower() != q.lower():
        candidates.append(cleaned)

    for candidate in candidates:
        cmd = ["winget", "search", candidate, "-s", "winget", "--disable-interactivity", "--nowarn"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=20)
        except Exception:
            continue
        rows = _parse_winget_table(res.stdout)
        if not rows:
            continue

        cand_lower = candidate.lower()
        for r in rows:
            if r["name"].lower() == cand_lower or r["id"].lower() == cand_lower:
                return r["id"]
        for r in rows:
            if r["name"].lower().startswith(cand_lower) or r["id"].lower().endswith("." + cand_lower):
                return r["id"]
        return rows[0]["id"]

    return None


def scan_installed_winget_packages(include_existing: bool = False, repo_root=None) -> list[dict[str, str]]:
    """Scan installed programs via ``winget list`` and filter out ones already in TXA."""
    existing_ids: set[str] = set()
    existing_names: set[str] = set()
    if not include_existing:
        try:
            from txa.pm.source import Sources
            for source in Sources().items:
                try:
                    for entry in source.index().get("packages", []):
                        existing_ids.add(entry["id"].lower())
                        existing_names.add(entry["name"].lower())
                        if entry.get("alias"):
                            existing_names.add(entry["alias"].lower())
                except Exception:
                    continue
        except Exception:
            pass
        if repo_root is not None:
            from pathlib import Path
            ns_dir = Path(repo_root) / "manifests" / config.get("namespace")[0].lower() / config.get("namespace")
            if ns_dir.is_dir():
                for child in ns_dir.iterdir():
                    if child.is_dir():
                        existing_ids.add(f"{config.get('namespace')}.{child.name}".lower())

    cmd = ["winget", "list", "--disable-interactivity", "--nowarn"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=30)
    except Exception:
        return []

    rows = _parse_winget_table(res.stdout)
    found: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    for r in rows:
        if r["source"] != "winget":
            continue
        name, pkg_id, version = r["name"], r["id"], r["version"]
        if any(pkg_id.startswith(prefix) for prefix in _SKIP_ID_PREFIXES):
            continue
        if pkg_id.lower() in seen_ids:
            continue

        app_slug = pkg_id.split(".")[-1].lower()
        derived_app = derive_txa_app_name(pkg_id, name).lower()
        name_clean = clean_query_name(name).lower()
        if not include_existing:
            if name.lower() in existing_names or name_clean in existing_names or app_slug in existing_names:
                continue
            if f"{config.get('namespace')}.{app_slug}".lower() in existing_ids:
                continue
            if f"{config.get('namespace')}.{derived_app}".lower() in existing_ids:
                continue

        seen_ids.add(pkg_id.lower())
        found.append({"name": name, "id": pkg_id, "version": version})

    return found


def populate_args_from_winget(args, data: dict[str, Any], namespace: str) -> list[str]:
    """Populate ``txacreate new`` argparse Namespace from WinGet metadata and return unique URLs."""
    pkg_id_raw = data.get("PackageIdentifier") or "App.Tool"
    raw_name = data.get("PackageName") or pkg_id_raw.split(".")[-1]
    raw_version = str(data.get("Version") or "1.0.0")

    if not getattr(args, "id", None):
        pascal_app = derive_txa_app_name(pkg_id_raw, raw_name)
        args.id = f"{namespace}.{pascal_app}"

    if not getattr(args, "version", None):
        args.version = normalize_version(raw_version)

    if not getattr(args, "name", None):
        name = clean_query_name(raw_name)[:64] or raw_name[:64]
        args.name = name if len(name) >= 2 else f"{name} App"

    pub = str(data.get("Publisher") or data.get("Author") or namespace).strip()[:128]
    if len(pub) < 2:
        pub = f"{pub} Team"
    if not getattr(args, "publisher", None):
        args.publisher = pub
    if not getattr(args, "author", None):
        author = str(data.get("Author") or pub).strip()[:128]
        args.author = author if len(author) >= 2 else pub

    if not getattr(args, "summary", None):
        summary = str(data.get("ShortDescription") or data.get("Description") or f"{args.name} installer for Windows.").strip()
        summary = re.sub(r"\s+", " ", summary)
        if len(summary) < 10:
            summary = f"{args.name} official installer for Windows."
        if len(summary) > 160:
            cutoff = summary[:157].rfind(" ")
            summary = summary[:cutoff if cutoff > 50 else 157] + "..."
        args.summary = summary

    if not getattr(args, "homepage", None):
        hp = str(data.get("Homepage") or data.get("Publisher Url") or "").strip()
        if hp.startswith("https://"):
            args.homepage = hp

    if not getattr(args, "license", None):
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
        args.license = lic_name

    if not getattr(args, "license_url", None):
        lic_url = str(data.get("License Url") or "").strip()
        if lic_url.startswith("https://"):
            args.license_url = lic_url
        elif args.license in licenses.NEEDS_URL:
            args.license_url = getattr(args, "homepage", None) or f"https://github.com/{config.get('upstream')}"

    if not getattr(args, "alias", None):
        moniker = str(data.get("Moniker") or "").strip().lower()
        alias = re.sub(r"[^a-z0-9-]", "", moniker).strip("-")
        if not (2 <= len(alias) <= 40):
            alias = re.sub(r"[^a-z0-9-]", "", args.id.split(".")[-1].lower()).strip("-")
        if 2 <= len(alias) <= 40:
            args.alias = alias

    if not getattr(args, "tags", None):
        raw_tags = data.get("Tags") or []
        cleaned = []
        for t in raw_tags:
            ct = clean_tag(t)
            if ct and ct not in cleaned:
                cleaned.append(ct)
            if len(cleaned) >= 16:
                break
        if cleaned:
            args.tags = ",".join(cleaned)

    targets = data.get("_targets") or {}
    # Deduplicate URLs when the same universal installer is served for multiple arches
    unique_targets: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for arch in ("x64", "arm64", "x86"):
        t = targets.get(arch)
        if t and t["url"] not in seen_urls:
            seen_urls.add(t["url"])
            unique_targets.append(t)

    if unique_targets:
        if not getattr(args, "arch", None):
            args.arch = ",".join(t["arch"] for t in unique_targets)
        if not getattr(args, "engine", None):
            args.engine = ",".join(t["engine"] for t in unique_targets)
        first_sw = unique_targets[0].get("switches") or {}
        def_sw = DEFAULT_SWITCHES.get(unique_targets[0]["engine"], {})
        if not getattr(args, "silent", None):
            args.silent = str(first_sw.get("Silent") or def_sw.get("silent") or "/S").strip()
        if not getattr(args, "progress", None):
            args.progress = str(first_sw.get("SilentWithProgress") or def_sw.get("progress") or args.silent).strip()

    return [t["url"] for t in unique_targets]
