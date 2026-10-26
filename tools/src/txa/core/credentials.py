"""GitHub credential storage.

Windows: the token is encrypted with DPAPI for the current user before it touches disk.
Other systems: plain JSON with 0600 permissions (same model as most git credential stores).

Lookup order: ``TXA_TOKEN`` env > stored credential > ``GH_TOKEN`` / ``GITHUB_TOKEN`` env.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from txa.core.config import txa_home


def _path() -> Path:
    return txa_home() / "credentials.bin"


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32
    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(_Blob),
    ]
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(_Blob),
    ]
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _UI_FORBIDDEN = 0x1

    def _call(fn, data: bytes, *extra) -> bytes:
        buffer = ctypes.create_string_buffer(data, len(data))
        blob_in = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        blob_out = _Blob()
        if not fn(ctypes.byref(blob_in), *extra, None, None, None, _UI_FORBIDDEN, ctypes.byref(blob_out)):
            raise ctypes.WinError()
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            _kernel32.LocalFree(blob_out.pbData)

    def _protect(data: bytes) -> bytes:
        return _call(_crypt32.CryptProtectData, data, "txa")

    def _unprotect(data: bytes) -> bytes:
        return _call(_crypt32.CryptUnprotectData, data, None)
else:
    def _protect(data: bytes) -> bytes:
        return data

    def _unprotect(data: bytes) -> bytes:
        return data


def save_token(token: str, login: str, source: str) -> Path:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({
        "token": token, "login": login, "source": source,
        "saved": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }).encode("utf-8")
    path.write_bytes(_protect(payload))
    if sys.platform != "win32":
        os.chmod(path, 0o600)
    return path


def stored() -> dict | None:
    try:
        return json.loads(_unprotect(_path().read_bytes()).decode("utf-8"))
    except (OSError, ValueError):
        return None


def load_token() -> tuple[str | None, str]:
    if os.environ.get("TXA_TOKEN"):
        return os.environ["TXA_TOKEN"], "TXA_TOKEN env"
    record = stored()
    if record and record.get("token"):
        return record["token"], f"stored credential ({record.get('source')})"
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(name):
            return os.environ[name], f"{name} env"
    return None, "none"


def clear_token() -> bool:
    try:
        _path().unlink()
        return True
    except FileNotFoundError:
        return False
