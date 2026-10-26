"""Authenticode verification.

Windows: ``Get-AuthenticodeSignature`` (full chain + revocation against the OS trust store).
Elsewhere: ``osslsigncode verify`` when installed, else only detect an embedded blob.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from txa.core import pe

_PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$sig = Get-AuthenticodeSignature -LiteralPath $env:TXA_SIG_TARGET
$cert = $sig.SignerCertificate
$kind = $null
try { $kind = [string]$sig.SignatureType } catch { }
[pscustomobject]@{
  status      = [string]$sig.Status
  message     = [string]$sig.StatusMessage
  signer      = if ($cert) { $cert.Subject } else { $null }
  issuer      = if ($cert) { $cert.Issuer } else { $null }
  thumbprint  = if ($cert) { $cert.Thumbprint } else { $null }
  not_after   = if ($cert) { $cert.NotAfter.ToUniversalTime().ToString('o') } else { $null }
  timestamped = [bool]$sig.TimeStamperCertificate
  kind        = $kind
} | ConvertTo-Json -Compress
"""


@dataclass
class Signature:
    status: str  # valid | unsigned | invalid | untrusted | unverified | error
    detail: str
    signer: str | None = None
    issuer: str | None = None
    thumbprint: str | None = None
    timestamped: bool = False
    engine: str = "none"

    @property
    def signer_cn(self) -> str | None:
        match = re.search(r"CN=(\"[^\"]+\"|[^,]+)", self.signer or "")
        return match.group(1).strip('"') if match else self.signer


_PS_STATUS = {
    "Valid": "valid",
    "NotSigned": "unsigned",
    "HashMismatch": "invalid",
    "NotTrusted": "untrusted",
    "UnknownError": "error",
    "NotSupportedFileFormat": "error",
    "Incompatible": "invalid",
}


def verify(path: Path | str) -> Signature:
    path = Path(path)
    if sys.platform == "win32":
        return _verify_windows(path)
    if shutil.which("osslsigncode"):
        return _verify_osslsigncode(path)
    return _verify_presence(path)


def _verify_windows(path: Path) -> Signature:
    shell = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
    env = dict(os.environ, TXA_SIG_TARGET=str(path))  # path never touches the command line
    try:
        proc = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-Command", _PS_SCRIPT],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, env=env,
        )
        data = json.loads(proc.stdout.strip() or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return Signature("error", f"PowerShell verification failed: {exc}", engine="powershell")
    status = _PS_STATUS.get(data.get("status", ""), "error")
    detail = data.get("message") or data.get("status") or proc.stderr.strip()
    if status == "valid" and data.get("kind") == "Catalog":
        detail += " (catalog signature - not portable to other machines)"
    return Signature(
        status, detail, data.get("signer"), data.get("issuer"), data.get("thumbprint"),
        bool(data.get("timestamped")), "powershell",
    )


def _verify_osslsigncode(path: Path) -> Signature:
    try:
        proc = subprocess.run(["osslsigncode", "verify", "-in", str(path)], capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Signature("error", f"osslsigncode failed: {exc}", engine="osslsigncode")
    output = proc.stdout + proc.stderr
    signer = re.search(r"Signer's certificate:.*?Subject: ([^\n]+)", output, re.S)
    issuer = re.search(r"Signer's certificate:.*?Issuer\s*: ([^\n]+)", output, re.S)
    if "No signature found" in output:
        return Signature("unsigned", "no Authenticode signature", engine="osslsigncode")
    status = "valid" if proc.returncode == 0 and "Signature verification: ok" in output else "invalid"
    return Signature(
        status, output.strip().splitlines()[-1] if output.strip() else "",
        signer.group(1).strip() if signer else None, issuer.group(1).strip() if issuer else None,
        timestamped="Timestamp" in output, engine="osslsigncode",
    )


def _verify_presence(path: Path) -> Signature:
    try:
        info = pe.read_pe(path, with_version=False)
    except pe.PEError:
        return Signature("unverified", "not a PE file and no verifier available on this OS")
    if info.has_embedded_signature:
        return Signature("unverified", "embedded signature present but the chain was not verified on this OS")
    return Signature("unsigned", "no certificate table in the PE header")
