"""Find a program in the OS list of installed software and remove it silently."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

from txa.pm.installer import InstallError

# Windows PowerShell 5.1 ships the "Programs" package provider on every Windows 10/11 box.
_LIST_SCRIPT = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Get-Package -ProviderName Programs -IncludeWindowsInstaller -ErrorAction SilentlyContinue | ForEach-Object {
  [pscustomobject]@{
    name      = $_.Name
    version   = $_.Version
    uninstall = $_.Meta.Attributes['UninstallString']
    quiet     = $_.Meta.Attributes['QuietUninstallString']
  }
} | ConvertTo-Json -Compress
"""

_QUIET_FLAGS = {
    "inno": "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART",
    "nsis": "/S",
    "wix": "/uninstall /quiet /norestart",
}
_MSI_GUID = re.compile(r"msiexec(?:\.exe)?\s+/[ix]\s*(\{[0-9A-F-]{36}\})", re.I)


@dataclass
class Program:
    name: str
    version: str | None
    uninstall: str | None
    quiet: str | None


def installed_programs() -> list[Program]:
    if sys.platform != "win32":
        raise InstallError("uninstalling requires Windows")
    shell = shutil.which("powershell") or "powershell.exe"
    proc = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", _LIST_SCRIPT],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    try:
        data = json.loads(proc.stdout or "[]")
    except ValueError:
        raise InstallError(f"could not read the installed-programs list: {proc.stderr.strip()}") from None
    rows = data if isinstance(data, list) else [data]
    return [Program(r.get("name") or "", r.get("version"), r.get("uninstall"), r.get("quiet")) for r in rows]


def find(name: str, programs: list[Program]) -> list[Program]:
    """Exact display name first, then 'name …' prefixes such as 'Hello Tool 1.4.2 (x64)'."""
    wanted = name.lower().strip()
    exact = [p for p in programs if p.name.lower() == wanted]
    if exact:
        return exact
    return [p for p in programs if p.name.lower().startswith(wanted + " ") and (p.uninstall or p.quiet)]


def command(program: Program, engine: str, interactive: bool = False) -> str:
    if not program.uninstall and not program.quiet:
        raise InstallError(f"'{program.name}' registers no uninstall command")
    if interactive:
        return program.uninstall or program.quiet  # type: ignore[return-value]
    guid = _MSI_GUID.search(program.uninstall or "")
    if guid:
        return f"msiexec.exe /x {guid.group(1)} /qn /norestart"
    if program.quiet:
        return program.quiet
    flags = _QUIET_FLAGS.get(engine)
    if not flags:
        raise InstallError(f"'{program.name}' has no quiet uninstall command - rerun with --interactive")
    return f"{program.uninstall} {flags}"


def split_command(cmdline: str) -> tuple[str, str]:
    """'"C:\\x y\\unins.exe" /S' -> ('C:\\x y\\unins.exe', '/S') for ShellExecute."""
    cmdline = cmdline.strip()
    if cmdline.startswith('"'):
        end = cmdline.index('"', 1)
        return cmdline[1:end], cmdline[end + 1:].strip()
    match = re.match(r"(.+?\.exe)(\s+.*)?$", cmdline, re.I)
    if match:
        return match.group(1), (match.group(2) or "").strip()
    head, _, tail = cmdline.partition(" ")
    return head, tail
