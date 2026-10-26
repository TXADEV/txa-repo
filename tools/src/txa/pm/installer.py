"""Download, verify and silently run an installer described by a TXA build card."""
from __future__ import annotations

import hmac
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from txa.core import config, engines, net, winos

MAX_DOWNLOAD = 4 * 1024 * 1024 * 1024
EXIT_CODES = {
    0: "success",
    3010: "success - restart required",
    1641: "success - restart initiated by the installer",
    1602: "cancelled by the user",
    1618: "another installation is already in progress",
    1603: "fatal error during installation",
    1633: "this platform is not supported by the package",
    1223: "elevation (UAC) was declined",
}
SUCCESS = {0, 3010, 1641}


class InstallError(Exception):
    pass


def pick_target(build: dict, arch: str | None = None) -> dict:
    targets = {t["arch"]: t for t in build.get("targets", [])}
    if arch:
        if arch not in targets:
            raise InstallError(f"no {arch} installer (available: {', '.join(targets)})")
        return targets[arch]
    native = winos.native_arch()
    for candidate in winos.arch_preference(native):
        if candidate in targets:
            return targets[candidate]
    raise InstallError(f"no installer runs on {native} (available: {', '.join(targets)})")


def check_os(build: dict) -> None:
    current = winos.current()
    needed = winos.parse_build(build["platform"]["min_build"])
    if current and current < needed:
        raise InstallError(f"needs Windows {winos.fmt(needed)} ({winos.describe(needed)}), "
                           f"this machine runs {winos.fmt(current)}")


def fetch(package_id: str, version: str, target: dict, on_progress=None, source=None) -> Path:
    """Download into %TEMP%\\txa\\<id>\\<version>\\<arch> and verify SHA-256 before returning.

    ``source`` (a pm.source.Source) may allow LAN hosts and lend its credentials to its own host.
    """
    dest = config.temp_root() / package_id / version / target["arch"]
    url = target["source"]["url"]
    result = net.download(net.session(), url, dest, MAX_DOWNLOAD, on_progress=on_progress,
                          allow_private=bool(source and source.allow_lan),
                          headers=source.download_headers(url) if source else None)
    expected = str(target["source"]["sha256"]).upper()
    if not hmac.compare_digest(result.sha256, expected):
        result.path.unlink(missing_ok=True)
        raise InstallError(f"SHA-256 mismatch - expected {expected}, got {result.sha256}. The file was deleted.")
    return result.path


def build_command(target: dict, payload: Path, mode: str, log: Path) -> tuple[str, str]:
    """Return (executable, parameters). mode: silent | progress | interactive."""
    engine = target["engine"]
    switches = engines.effective_switches(target)
    chosen = "" if mode == "interactive" else switches[mode]
    parts = [p for p in (chosen, switches["custom"]) if p]
    upper = " ".join(parts).upper()
    if engine == "msi":
        params = subprocess.list2cmdline(["/i", str(payload)])
        if mode != "interactive":
            params += f" {' '.join(parts)}"
        params += " " + subprocess.list2cmdline(["/l*v", str(log)])
        return "msiexec.exe", params.strip()
    if engine == "inno" and mode != "interactive" and "/LOG" not in upper:
        parts.append(subprocess.list2cmdline([f"/LOG={log}"]))
    if engine == "wix" and mode != "interactive" and "/LOG" not in upper:
        parts.append(subprocess.list2cmdline(["/log", str(log)]))
    return str(payload), " ".join(parts)


def is_admin() -> bool:
    if sys.platform != "win32":
        return False
    import ctypes

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def run(executable: str, params: str, elevate: bool) -> int:
    if sys.platform != "win32":
        raise InstallError("installing requires Windows (use --download-only elsewhere)")
    if elevate and not is_admin():
        return _run_elevated(executable, params)
    command = f"{subprocess.list2cmdline([executable])} {params}".strip()
    return subprocess.run(command).returncode  # CreateProcess directly - no shell involved


def _run_elevated(executable: str, params: str) -> int:
    import ctypes
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
        ]

    see_mask_nocloseprocess, see_mask_noasync = 0x40, 0x100
    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = see_mask_nocloseprocess | see_mask_noasync
    info.lpVerb = "runas"
    info.lpFile = executable
    info.lpParameters = params
    info.nShow = 1
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.windll.kernel32
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == 1223:
            return 1223
        raise InstallError(f"could not start elevated installer (Win32 error {error})")
    kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
    code = wintypes.DWORD()
    kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    kernel32.CloseHandle(info.hProcess)
    return code.value


# --------------------------------------------------------------------------- local records


def _records_path() -> Path:
    return config.txa_home() / "state" / "installed.json"


def installed() -> dict:
    try:
        return json.loads(_records_path().read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def record(package_id: str, version: str, target: dict, exit_code: int, name: str, scope: str,
           source: str = config.OFFICIAL_SOURCE) -> None:
    data = installed()
    data[package_id] = {
        "version": version, "arch": target["arch"], "engine": target["engine"], "exit_code": exit_code,
        "name": name, "scope": scope, "source": source, "installed": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _save(data)


def forget(package_id: str) -> None:
    data = installed()
    data.pop(package_id, None)
    _save(data)


def _pins_path() -> Path:
    return config.txa_home() / "state" / "pins.json"


def pins() -> dict[str, str]:
    """package id -> pinned version ('*' = never upgrade)."""
    try:
        return json.loads(_pins_path().read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def set_pin(package_id: str, version: str | None) -> None:
    data = pins()
    if version is None:
        data.pop(package_id, None)
    else:
        data[package_id] = version
    path = _pins_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), "utf-8")


def _save(data: dict) -> None:
    path = _records_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), "utf-8")
