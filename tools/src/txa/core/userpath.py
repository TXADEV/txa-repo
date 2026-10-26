"""The per-user PATH (HKCU\\Environment\\Path) used by portable packages.

Reads and writes the *raw* registry value so entries such as ``%USERPROFILE%\\bin`` keep their
variables, and writes it back as REG_EXPAND_SZ. The merge/remove helpers are pure functions so
they can be tested on any system.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.expandvars(path.strip().strip('"')))).rstrip("\\/")


def split(value: str) -> list[str]:
    return [part for part in value.split(";") if part.strip()]


def merge(value: str, add: list[str]) -> str:
    """Append directories that are not already present (compared after expanding variables)."""
    parts = split(value)
    present = {_key(p) for p in parts}
    for directory in add:
        if _key(directory) not in present:
            parts.append(directory)
            present.add(_key(directory))
    return ";".join(parts)


def remove(value: str, drop: list[str]) -> str:
    wanted = {_key(d) for d in drop}
    return ";".join(p for p in split(value) if _key(p) not in wanted)


def remove_under(value: str, root: str | Path) -> str:
    """Drop every entry that lives inside ``root`` (used when a package is uninstalled)."""
    base = _key(str(root))
    kept = []
    for part in split(value):
        k = _key(part)
        if k == base or k.startswith(base + os.sep):
            continue
        kept.append(part)
    return ";".join(kept)


def _registry():
    if sys.platform != "win32":
        raise OSError("the user PATH can only be edited on Windows")
    import winreg

    return winreg


def read_raw() -> str:
    winreg = _registry()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as key:
        try:
            return winreg.QueryValueEx(key, "Path")[0]
        except FileNotFoundError:
            return ""


def write_raw(value: str) -> None:
    winreg = _registry()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, value)
    _broadcast()


def _broadcast() -> None:
    """Tell running programs (Explorer, new terminals) that the environment changed."""
    try:
        import ctypes

        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, ctypes.byref(result))
    except (AttributeError, OSError):
        pass


def update(add: list[str] | None = None, drop: list[str] | None = None, drop_under: str | Path | None = None) -> bool:
    """Apply changes to the user PATH. Returns True when the stored value changed."""
    current = read_raw()
    new = current
    if drop_under:
        new = remove_under(new, drop_under)
    if drop:
        new = remove(new, drop)
    if add:
        new = merge(new, add)
    if new == current:
        return False
    write_raw(new)
    return True
