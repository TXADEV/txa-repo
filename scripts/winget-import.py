#!/usr/bin/env python3
"""Compatibility wrapper for `txacli` (WinGet to TXA Package Converter & Auto-Creator).

Prefer running `txacli` directly from any terminal.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_SRC = REPO_ROOT / "tools" / "src"
if str(TOOLS_SRC) not in sys.path:
    sys.path.insert(0, str(TOOLS_SRC))

try:
    import yaml  # noqa: F401
    import txa.cli as txa_cli
except ImportError:
    venv_py = REPO_ROOT / "tools" / ".venv" / "Scripts" / "python.exe"
    if venv_py.is_file() and sys.executable.lower() != str(venv_py).lower():
        os.execv(str(venv_py), [str(venv_py), *sys.argv])
    else:
        sys.exit("Error: Required dependencies not found. Run `txacli` or use tools/.venv.")

from txa.create.winget import (  # re-exported for backward compatibility
    convert_winget_to_draft,
    sanitize_alias,
    save_draft_manifests,
    validate_draft,
)

__all__ = [
    "convert_winget_to_draft",
    "sanitize_alias",
    "save_draft_manifests",
    "validate_draft",
]

if __name__ == "__main__":
    sys.exit(txa_cli.main())
