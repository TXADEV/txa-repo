"""Installer engine detection and silent-switch rules.

Engines understood by TXA:

``inno``  Inno Setup .exe          ``nsis``  Nullsoft .exe
``msi``   Windows Installer .msi   ``wix``   WiX Burn bundle .exe
``exe``   any other .exe - switches must be declared explicitly
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from txa.core import pe

CFB_MAGIC = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"
_HEAD_SCAN = 32 * 1024 * 1024
_OVERLAY_SCAN = 8 * 1024 * 1024
_CHUNK = 1024 * 1024


def _both(text: str) -> list[bytes]:
    return [text.encode("ascii"), text.encode("utf-16-le")]


# (engine, label, markers) - first match wins, so put the most specific first.
_EXE_MARKERS: list[tuple[str, str, list[bytes]]] = [
    ("inno", "Inno Setup", [b"Inno Setup Setup Data", b"InnoSetupLdrWindow", *_both("built with Inno Setup")]),
    ("nsis", "Nullsoft Scriptable Install System", [b"NullsoftInst", b"Nullsoft.NSIS.exehead"]),
    ("exe", "InstallShield", _both("InstallShield")),
    ("exe", "Advanced Installer", _both("Advanced Installer")),
    ("exe", "7-Zip SFX", [b"7z\xBC\xAF\x27\x1C"]),
]

DEFAULT_SWITCHES: dict[str, dict[str, str]] = {
    "inno": {"silent": "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-", "progress": "/SILENT /SUPPRESSMSGBOXES /NORESTART /SP-"},
    "nsis": {"silent": "/S", "progress": "/S"},
    "msi": {"silent": "/qn /norestart", "progress": "/qb! /norestart"},
    "wix": {"silent": "/quiet /norestart", "progress": "/passive /norestart"},
}

_MSI_PLATFORMS = {"intel": "x86", "x64": "x64", "amd64": "x64", "arm64": "arm64", "": "x86"}


@dataclass
class Inspection:
    format: str  # pe | msi | unknown
    engine: str | None = None  # inno | nsis | wix | msi | exe | None
    label: str = "unknown"
    machine: str | None = None  # CPU of the installer binary itself
    payload_arch: str | None = None  # arch the package installs, when the format says so (MSI)
    pe: pe.PEInfo | None = None
    msi: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def product_name(self) -> str | None:
        if self.pe:
            return self.pe.version_info.get("ProductName") or self.pe.version_info.get("FileDescription")
        return self.msi.get("subject") or self.msi.get("title")

    @property
    def company(self) -> str | None:
        if self.pe:
            return self.pe.version_info.get("CompanyName")
        return self.msi.get("author")

    @property
    def product_version(self) -> str | None:
        if self.pe:
            return self.pe.version_info.get("ProductVersion") or self.pe.fixed_product_version
        return None

    @property
    def copyright(self) -> str | None:
        return self.pe.version_info.get("LegalCopyright") if self.pe else None


def _scan(path: Path, start: int, length: int, markers: list[bytes]) -> set[bytes]:
    found: set[bytes] = set()
    keep = max(len(m) for m in markers) - 1
    with path.open("rb") as fh:
        fh.seek(start)
        tail = b""
        remaining = length
        while remaining > 0 and len(found) < len(markers):
            chunk = fh.read(min(_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            window = tail + chunk
            for marker in markers:
                if marker not in found and marker in window:
                    found.add(marker)
            tail = window[-keep:] if keep else b""
    return found


def inspect(path: Path | str) -> Inspection:
    path = Path(path)
    with path.open("rb") as fh:
        head = fh.read(8)
    if head == CFB_MAGIC:
        return _inspect_msi(path)
    if head[:2] == b"MZ":
        return _inspect_exe(path)
    if head[:4] == b"PK\x03\x04":
        return Inspection(format="unknown", label="ZIP archive", notes=["ZIP/MSIX packages are not supported by TXA v1"])
    return Inspection(format="unknown")


def _inspect_exe(path: Path) -> Inspection:
    try:
        info = pe.read_pe(path)
    except pe.PEError as exc:
        return Inspection(format="unknown", label="broken PE", notes=[str(exc)])
    result = Inspection(format="pe", machine=info.machine, pe=info)
    if ".wixburn" in info.section_names():
        result.engine, result.label = "wix", "WiX Burn bundle"
        return result
    all_markers = [m for _, _, markers in _EXE_MARKERS for m in markers]
    found = _scan(path, 0, _HEAD_SCAN, all_markers)
    if info.overlay_offset < info.file_size and info.overlay_offset > _HEAD_SCAN:
        found |= _scan(path, info.overlay_offset, _OVERLAY_SCAN, all_markers)
    for engine, label, markers in _EXE_MARKERS:
        if found.intersection(markers):
            result.engine, result.label = engine, label
            return result
    result.engine, result.label = "exe", "generic executable"
    return result


def _inspect_msi(path: Path) -> Inspection:
    import olefile

    result = Inspection(format="msi", engine="msi", label="Windows Installer package")
    try:
        with olefile.OleFileIO(str(path)) as ole:
            meta = ole.get_metadata()
    except (OSError, ValueError) as exc:  # olefile raises plain OSError on corrupt files
        result.notes.append(f"cannot read MSI summary information: {exc}")
        return result

    def text(value) -> str:
        if isinstance(value, bytes):
            return value.decode("latin-1", errors="replace").strip("\0 ").strip()
        return str(value or "").strip()

    result.msi = {
        "title": text(meta.title),
        "subject": text(meta.subject),
        "author": text(meta.author),
        "template": text(meta.template),
        "creator": text(meta.creating_application),
    }
    platform = result.msi["template"].split(";", 1)[0].strip().lower()
    result.payload_arch = _MSI_PLATFORMS.get(platform)
    if result.payload_arch is None:
        result.notes.append(f"unknown MSI platform '{platform}'")
    creator = result.msi["creator"].lower()
    if "windows installer xml" in creator or "wix" in creator:
        result.label = "Windows Installer package (authored with WiX)"
    return result


# --------------------------------------------------------------------------- switches

_META = re.compile(r"[&|;<>^`$\r\n]")


@dataclass
class SwitchRule:
    silent: set[str]  # at least one of these tokens must be in `silent`
    progress: set[str]  # at least one of these tokens must be in `progress`
    foreign: set[str]  # tokens that belong to other engines
    case_sensitive: bool = False


RULES: dict[str, SwitchRule] = {
    "inno": SwitchRule({"/VERYSILENT"}, {"/SILENT", "/VERYSILENT"}, {"/S", "/QN", "/QUIET", "/PASSIVE", "/QB"}),
    "nsis": SwitchRule({"/S"}, {"/S"}, {"/VERYSILENT", "/SILENT", "/qn", "/quiet", "/passive", "/qb"}, True),
    "msi": SwitchRule({"/QN", "/QUIET", "/Q", "/QN+", "/QN-"}, {"/QB", "/QB!", "/QB-", "/QB+", "/PASSIVE", "/QR"}, {"/S", "/VERYSILENT", "/SILENT"}),
    "wix": SwitchRule({"/QUIET", "/Q", "-QUIET", "-Q"}, {"/PASSIVE", "-PASSIVE"}, {"/S", "/VERYSILENT", "/SILENT", "/QN"}),
}


def tokens(switches: str) -> list[str]:
    try:
        return shlex.split(switches, posix=False)
    except ValueError:
        return switches.split()


def effective_switches(target: dict) -> dict[str, str]:
    declared = target.get("switches") or {}
    defaults = DEFAULT_SWITCHES.get(target.get("engine", ""), {})
    return {
        "silent": declared.get("silent") or defaults.get("silent", ""),
        "progress": declared.get("progress") or defaults.get("progress", ""),
        "custom": declared.get("custom", ""),
    }


def check_switches(engine: str, switches: dict[str, str]) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for the effective switches of one target."""
    errors: list[str] = []
    warnings: list[str] = []
    for mode, value in switches.items():
        if value and _META.search(value):
            errors.append(f"{mode}: shell metacharacters are not allowed ({value!r})")
    if engine == "exe":
        if not switches.get("silent"):
            errors.append("engine 'exe' has no known silent mode - declare switches.silent explicitly")
        else:
            warnings.append("engine 'exe': switches cannot be cross-checked automatically, a moderator must confirm them")
        return errors, warnings
    rule = RULES[engine]

    def norm(token: str) -> str:
        head = token.split("=", 1)[0]
        return head if rule.case_sensitive else head.upper()

    for mode, required in (("silent", rule.silent), ("progress", rule.progress)):
        found = {norm(t) for t in tokens(switches.get(mode, ""))}
        wanted = required if rule.case_sensitive else {r.upper() for r in required}
        if not found & wanted:
            errors.append(f"{mode}: '{engine}' needs one of {', '.join(sorted(required))}")
        foreign = rule.foreign if rule.case_sensitive else {f.upper() for f in rule.foreign}
        for token in sorted(found & foreign):
            errors.append(f"{mode}: '{token}' belongs to another installer engine, not '{engine}'")
    if engine == "nsis":
        for mode in ("silent", "progress", "custom"):
            parts = tokens(switches.get(mode, ""))
            if any(t.upper().startswith("/D=") for t in parts[:-1]):
                errors.append(f"{mode}: NSIS requires /D= to be the last switch")
            if any(t == "/s" for t in parts):
                errors.append(f"{mode}: NSIS switches are case-sensitive - use /S, not /s")
    if engine in ("msi", "wix") and "/NORESTART" not in {t.upper() for t in tokens(switches.get("silent", ""))}:
        warnings.append("silent: add /norestart so unattended installs never reboot the machine")
    return errors, warnings
