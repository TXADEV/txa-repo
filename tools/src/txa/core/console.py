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
    if os.environ.get("FORCE_COLOR", "0") not in ("", "0", "false"):
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


def human_time(seconds: float) -> str:
    seconds = int(seconds + 0.5)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}m{seconds:02d}s"


def human_size(count: float) -> str:
    if count >= 1048576:
        return f"{count / 1048576:.1f} MiB"
    return f"{count / 1024:.0f} KiB" if count >= 1024 else f"{count:.0f} B"


def stage(index: int, total: int, text: str) -> None:
    """A numbered phase of a long command: ``[2/5] Downloading …``."""
    print(f"{paint(f'[{index}/{total}]', 'cyan', 'bold')} {text}")


def progress(label_text: str):
    """Return an on_progress(done, total) callback drawing one live line.

    ``Downloading  [##########............]  42%  11.3/27.0 MiB  5.2 MiB/s  ETA 3s``
    Only drawn on a real terminal; call done_line() when finished.
    """
    started: list[float] = []
    last = [0.0]

    def draw(done: int, total: int | None) -> None:
        if not sys.stderr.isatty():
            return
        import time

        now = time.monotonic()
        if not started:
            started.append(now)
        if now - last[0] < 0.1 and not (total and done >= total):
            return
        last[0] = now
        elapsed = max(now - started[0], 1e-6)
        speed = done / elapsed
        if total:
            width = 28
            filled = int(width * done / total)
            bar = paint("#" * filled, "green", stream=sys.stderr) + paint("." * (width - filled), "gray", stream=sys.stderr)
            eta = (total - done) / speed if speed > 0 and elapsed > 0.5 else 0
            tail = f"{human_size(done)}/{human_size(total)}  {human_size(speed)}/s"
            if done < total and elapsed > 0.5:
                tail += f"  ETA {human_time(eta)}"
            line = f"  {label_text} [{bar}] {done * 100 // total:3d}%  {tail}"
        else:
            line = f"  {label_text} {human_size(done)}  {human_size(speed)}/s"
        sys.stderr.write("\r" + line + "   ")
        sys.stderr.flush()
    return draw


def done_line() -> None:
    if sys.stderr.isatty():
        sys.stderr.write("\n")


class working:
    """Spinner with elapsed time for a call that blocks (an installer, a network step).

    ``with console.working("Installing silently") as w: ...``; prints nothing unless stderr is a terminal.
    ``w.elapsed`` is available afterwards.
    """

    _FRAMES = "-\\|/"

    def __init__(self, text: str, indent: str = "  "):
        self.text = text
        self.indent = indent
        self.elapsed = 0.0
        self._stop = None
        self._thread = None
        self._started = 0.0

    def update(self, text: str) -> None:
        self.text = text

    def __enter__(self) -> "working":
        import threading
        import time

        self._started = time.monotonic()
        self._stop = threading.Event()
        if sys.stderr.isatty():
            def spin() -> None:
                index = 0
                while not self._stop.wait(0.12):
                    spent = human_time(time.monotonic() - self._started)
                    frame = paint(self._FRAMES[index % len(self._FRAMES)], "cyan", "bold", stream=sys.stderr)
                    sys.stderr.write(f"\r{self.indent}{frame} {self.text} {paint(spent, 'gray', stream=sys.stderr)}   ")
                    sys.stderr.flush()
                    index += 1

            self._thread = threading.Thread(target=spin, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        import time

        self.elapsed = time.monotonic() - self._started
        self._stop.set()
        if self._thread:
            self._thread.join()
            sys.stderr.write("\r" + " " * (len(self.indent) + len(self.text) + 16) + "\r")
            sys.stderr.flush()


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"  {paint(prompt, 'bold')}{suffix}: ").strip()
    except EOFError:
        answer = ""
    return answer or (default or "")
