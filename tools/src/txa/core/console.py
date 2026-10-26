"""Terminal helpers shared by the three CLIs: UTF-8 output, colours, progress bar, prompts.

Colours are ANSI. They switch on only for a real terminal, never when output is piped or
redirected, and respect NO_COLOR (off) / FORCE_COLOR (on) / TERM=dumb (off).
"""
from __future__ import annotations

import os
import sys

_CODES = {
    "bold": "1", "dim": "2",
    "red": "31", "green": "32", "yellow": "33", "blue": "34", "magenta": "35", "cyan": "36", "gray": "90",
}
_GLYPHS = {"✓": ("green", "bold"), "→": ("cyan",), "✗": ("red", "bold"), "!": ("yellow", "bold")}


def _enable_vt() -> None:
    """Windows 10+: switch the console into ANSI (virtual terminal) mode."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        for std in (-11, -12):  # stdout, stderr
            handle = kernel32.GetStdHandle(std)
            mode = wintypes.DWORD()
            if handle and kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except (AttributeError, OSError):
        pass


def setup() -> None:
    """Force UTF-8 output so arrows and check marks never crash a cp1252 console; enable colours."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    _enable_vt()


def use_color(stream=None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("TERM") == "dumb":
        return False
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


def paint(text: str, *styles: str, stream=None) -> str:
    if not styles or not use_color(stream):
        return text
    codes = ";".join(_CODES[s] for s in styles)
    return f"\033[{codes}m{text}\033[0m"


def say(text: str = "", stream=None, end: str = "\n") -> None:
    """print() that colours a leading glyph: ✓ green, → cyan, ✗ red, ! yellow."""
    stream = stream or sys.stdout
    body = text.lstrip(" ")
    if body[:1] in _GLYPHS:
        indent = text[: len(text) - len(body)]
        text = indent + paint(body[0], *_GLYPHS[body[0]], stream=stream) + body[1:]
    print(text, file=stream, end=end)


def error(prefix: str, message: str) -> None:
    print(f"{paint(prefix + ':', 'red', 'bold', stream=sys.stderr)} {message}", file=sys.stderr)


def cell(text: str, width: int, *styles: str) -> str:
    """Pad first, colour second, so ANSI codes never break column alignment."""
    return paint(f"{text:<{width}}", *styles)


def head(text: str) -> str:
    return paint(text, "bold", "cyan")


def label(text: str, width: int = 11) -> str:
    return paint(f"{text:<{width}}", "gray")


def status(name: str) -> str:
    """PASS / WARN / FAIL / SKIP, coloured."""
    return paint(f"{name:<4}", {"PASS": "green", "WARN": "yellow", "FAIL": "red", "SKIP": "gray"}.get(name, "dim"), "bold")


def progress(label_text: str):
    """Return an on_progress(done, total) callback that draws a single-line bar."""
    def draw(done: int, total: int | None) -> None:
        if not sys.stderr.isatty():
            return
        if total:
            width = 30
            filled = int(width * done / total)
            bar = paint("#" * filled, "green", stream=sys.stderr) + paint("." * (width - filled), "gray", stream=sys.stderr)
            sys.stderr.write(f"\r  {label_text} [{bar}] {done * 100 // total:3d}% ")
        else:
            sys.stderr.write(f"\r  {label_text} {done / 1048576:.1f} MiB ")
        sys.stderr.flush()
    return draw


def done_line() -> None:
    if sys.stderr.isatty():
        sys.stderr.write("\n")


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"  {paint(prompt, 'bold')}{suffix}: ").strip()
    except EOFError:
        answer = ""
    return answer or (default or "")
