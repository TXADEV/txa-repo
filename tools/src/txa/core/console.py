"""Terminal helpers shared by the three CLIs."""
from __future__ import annotations

import sys


def setup() -> None:
    """Force UTF-8 output so arrows and check marks never crash a cp1252 console."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def progress(label: str):
    """Return an on_progress(done, total) callback that draws a single-line bar."""
    def draw(done: int, total: int | None) -> None:
        if not sys.stderr.isatty():
            return
        if total:
            width = 30
            filled = int(width * done / total)
            sys.stderr.write(f"\r  {label} [{'#' * filled}{'.' * (width - filled)}] {done * 100 // total:3d}% ")
        else:
            sys.stderr.write(f"\r  {label} {done / 1048576:.1f} MiB ")
        sys.stderr.flush()
    return draw


def done_line() -> None:
    if sys.stderr.isatty():
        sys.stderr.write("\n")


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"  {prompt}{suffix}: ").strip()
    except EOFError:
        answer = ""
    return answer or (default or "")
