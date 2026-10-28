"""Terminal helpers shared by the three CLIs: UTF-8 output, colours, progress bar, prompts.

Colours are ANSI. They switch on only for a real terminal, never when output is piped or
redirected, and respect NO_COLOR (off) / FORCE_COLOR (on) / TERM=dumb (off).
"""
from __future__ import annotations

import os
import re
import sys
import unicodedata

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
    from txa.core import i18n

    text = i18n.translate(text)
    stream = stream or sys.stdout
    body = text.lstrip(" ")
    if body[:1] in _GLYPHS:
        indent = text[: len(text) - len(body)]
        text = indent + paint(body[0], *_GLYPHS[body[0]], stream=stream) + body[1:]
    print(text, file=stream, end=end)


def error(prefix: str, message: str) -> None:
    from txa.core import i18n

    print(f"{paint(prefix + ':', 'red', 'bold', stream=sys.stderr)} {i18n.translate(message)}", file=sys.stderr)


def cell(text: str, width: int, *styles: str) -> str:
    """Pad first, colour second, so ANSI codes never break column alignment."""
    return paint(f"{text:<{width}}", *styles)


def head(text: str) -> str:
    from txa.core import i18n

    return paint(i18n.translate(text), "bold", "cyan")


def label(text: str, width: int = 11) -> str:
    from txa.core import i18n

    return paint(f"{i18n.translate(text):<{width}}", "gray")


def status(name: str) -> str:
    """PASS / WARN / FAIL / SKIP, coloured."""
    return paint(f"{name:<4}", {"PASS": "green", "WARN": "yellow", "FAIL": "red", "SKIP": "gray"}.get(name, "dim"), "bold")


def safe(text: object) -> str:
    """Text that came from the network, made safe to print: no escape sequences, controls or bidi overrides."""
    return "".join(" " if c in "\t\r\n" else c for c in str(text)
                   if c in "\t\r\n" or not unicodedata.category(c).startswith("C"))


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
    from txa.core import i18n

    print(f"{paint(f'[{index}/{total}]', 'cyan', 'bold')} {i18n.translate(text)}")


def progress(label_text: str):
    """Return an on_progress(done, total) callback drawing one live line.

    ``Downloading  [##########............]  42%  11.3/27.0 MiB  5.2 MiB/s  ETA 3s``
    Only drawn on a real terminal; call done_line() when finished.
    """
    started: list[float] = []
    last = [0.0]
    longest = [0]

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
        visible = len(re.sub(r"\x1b\[[0-9;]*m", "", line))
        longest[0] = max(longest[0], visible)
        sys.stderr.write("\r" + line + " " * (longest[0] - visible + 1))  # blank out leftovers of a longer earlier line
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
        from txa.core import i18n

        self.text = i18n.translate(text)
        self.indent = indent
        self.elapsed = 0.0
        self._stop = None
        self._thread = None
        self._started = 0.0

    def update(self, text: str) -> None:
        from txa.core import i18n

        self.text = i18n.translate(text)

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


# --------------------------------------------------------------------------- interactive questions
#
# field()   one value, with the suggested answer shown as a dim placeholder: Enter accepts it,
#           typing replaces it (nothing to delete first)
# choose()  pick from a list with the Up/Down arrows (or by typing the number), notes on the right
# confirm() y / n
#
# Keys are read raw on a real Windows console; anywhere else (pipes, tests, other systems) the same
# questions fall back to plain line input, so scripts never hang on a missing terminal feature.

from dataclasses import dataclass  # noqa: E402


@dataclass
class Option:
    value: object
    label: str
    note: str = ""


def _raw_available() -> bool:
    return (sys.platform == "win32" and sys.stdin.isatty() and sys.stdout.isatty()
            and not os.environ.get("TXA_PLAIN_INPUT"))


def _getkey() -> str:
    """One key press: a character, or UP DOWN LEFT RIGHT ENTER BACKSPACE ESC OTHER."""
    import msvcrt

    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):
        return {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT"}.get(msvcrt.getwch(), "OTHER")
    if ch in ("\r", "\n"):
        return "ENTER"
    if ch == "\x08":
        return "BACKSPACE"
    if ch == "\x03":
        raise KeyboardInterrupt
    if ch == "\x1b":
        return "ESC"
    return ch


def _columns() -> int:
    import shutil

    return shutil.get_terminal_size((100, 30)).columns


def _read_line(prefix: str, placeholder: str) -> str:
    """Raw single-line editor: dim placeholder, typing replaces it, Backspace edits, Enter accepts."""
    out = sys.stdout
    shown = f"({placeholder})" if placeholder else ""
    out.write(prefix + paint(shown, "gray"))
    if shown:
        out.write(f"\x1b[{len(shown)}D")  # cursor back to the start of the placeholder
    out.flush()
    buffer: list[str] = []
    fresh = bool(shown)
    while True:
        key = _getkey()
        if key == "ENTER":
            break
        if key == "BACKSPACE":
            if buffer:
                buffer.pop()
                out.write("\b \b")
                out.flush()
            continue
        if len(key) != 1 or not key.isprintable():
            continue
        if fresh:
            out.write("\x1b[K")  # first keystroke: wipe the placeholder
            fresh = False
        buffer.append(key)
        out.write(key)
        out.flush()
    return "".join(buffer)


def _question_text(question: str, example: str, key: str) -> str:
    from txa.core import i18n

    q = i18n.translate(question)
    eg = i18n.translate("e.g.")
    val_is = i18n.translate("value is:")
    example_part = f" |{eg} {example}|" if example else ""
    return f"{q}{example_part} [{key}] {val_is}"


def field(question: str, example: str, key: str, default: str = "", validate=None) -> str:
    """Ask for one value. ``validate(value)`` returns an error text or None; a blank answer takes ``default``."""
    from txa.core import i18n

    head = _question_text(question, example, key)
    val_is = i18n.translate("value is:")
    while True:
        if _raw_available():
            wrap = len(head) + len(default) + 6 > _columns()
            if wrap:
                print(head)
            prefix = f"    {val_is} " if wrap else head + " "
            answer = _read_line(prefix, default).strip() or default
            sys.stdout.write("\r\x1b[K" + (prefix if wrap else head + " ") + paint(answer, "cyan", "bold") + "\n")
            sys.stdout.flush()
        else:
            suffix = f" ({default})" if default else ""
            try:
                answer = input(f"{head}{suffix} ").strip() or default
            except EOFError:
                answer = default
        problem = validate(answer) if validate else (None if answer else "a value is required")
        if not problem:
            return answer
        print(paint(f"  ✗ {i18n.translate(problem)}", "red"))
        default = answer if answer else default


def choose(question: str, key: str, options: list[Option], default: int = 0) -> Option:
    """Pick one of ``options`` with Up/Down (or type its number), Enter to confirm."""
    from txa.core import i18n

    head = f"{i18n.translate(question)} [{key}] {i18n.translate('value is:')}"
    count = len(options)
    default = min(max(default, 0), count - 1)
    if not _raw_available() or count + 2 > shutil_lines():
        print(head)
        for number, option in enumerate(options, 1):
            print(f"  {number:>2}) {cell(i18n.translate(option.label), 16, 'bold')} {paint(i18n.translate(option.note), 'gray')}")
        while True:
            try:
                prompt_msg = i18n.translate(f"Number 1-{count}, or press Enter for {options[default].label}: ")
                answer = input(prompt_msg).strip()
            except EOFError:
                answer = ""
            if not answer:
                return options[default]
            if answer.isdigit() and 1 <= int(answer) <= count:
                return options[int(answer) - 1]
            by_name = [o for o in options if o.label.lower() == answer.lower()]
            if by_name:
                return by_name[0]
            print(paint(f"  ✗ {i18n.translate(f'type a number from 1 to {count}')}", "red"))
    index, typed = default, ""
    out = sys.stdout

    def draw(first: bool) -> None:
        if not first:
            out.write(f"\x1b[{count}A")
        for position, option in enumerate(options):
            selected = position == index
            marker = paint("›", "cyan", "bold") if selected else " "
            num = paint(f"{position + 1:>2})", "cyan", "bold") if selected else paint(f"{position + 1:>2})", "gray")
            lbl_text = i18n.translate(option.label)
            label = cell(lbl_text, 24, "bold", "cyan") if selected else cell(lbl_text, 24)
            out.write(f"\x1b[K  {marker} {num} {label} {paint(i18n.translate(option.note), 'gray')}\n")
        out.flush()

    print(head)
    draw(True)
    while True:
        key_pressed = _getkey()
        if key_pressed == "ENTER":
            break
        if key_pressed == "UP":
            index, typed = (index - 1) % count, ""
        elif key_pressed == "DOWN":
            index, typed = (index + 1) % count, ""
        elif len(key_pressed) == 1 and key_pressed.isdigit():
            typed += key_pressed
            if not 1 <= int(typed) <= count:
                typed = key_pressed
            if 1 <= int(typed) <= count:
                index = int(typed) - 1
        draw(False)
    out.write(f"\x1b[{count + 1}A\x1b[J{head} {paint(i18n.translate(options[index].label), 'cyan', 'bold')}\n")
    out.flush()
    return options[index]


def shutil_lines() -> int:
    import shutil

    return shutil.get_terminal_size((100, 30)).lines


def confirm(question: str, default: bool = False) -> bool:
    """A y/n question. Enter takes the default."""
    from txa.core import i18n

    q = i18n.translate(question)
    hint = "(Y/n)" if default else "(y/N)"
    if _raw_available():
        sys.stdout.write(f"{q}: {paint(hint, 'gray')} ")
        sys.stdout.flush()
        while True:
            key = _getkey()
            if key == "ENTER":
                answer = default
                break
            if isinstance(key, str) and key.lower() in ("y", "n"):
                answer = key.lower() == "y"
                break
        ans_label = i18n.translate("Yes" if answer else "No")
        sys.stdout.write("\r\x1b[K" + f"{q}: " + paint(ans_label, "cyan", "bold") + "\n")
        sys.stdout.flush()
        return answer
    while True:
        try:
            text = input(f"{q}: {hint} ").strip().lower()
        except EOFError:
            return default
        if not text:
            return default
        if text in ("y", "yes"):
            return True
        if text in ("n", "no"):
            return False


def ask(prompt: str, default: str | None = None) -> str:
    from txa.core import i18n

    p = i18n.translate(prompt)
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"  {paint(p, 'bold')}{suffix}: ").strip()
    except EOFError:
        answer = ""
    return answer or (default or "")


# --------------------------------------------------------------------------- smart CLI typo suggestions

import argparse  # noqa: E402
import difflib  # noqa: E402

_COMMON_COMMAND_HINTS: dict[str, str] = {
    "i": "install",
    "in": "install",
    "ins": "install",
    "instal": "install",
    "isntall": "install",
    "get": "install",
    "del": "uninstall",
    "delete": "uninstall",
    "erase": "uninstall",
    "uninstal": "uninstall",
    "unin": "uninstall",
    "up": "upgrade",
    "upgarde": "upgrade",
    "ugprade": "upgrade",
    "upd": "upgrade",
    "s": "search",
    "serach": "search",
    "seach": "search",
    "srch": "search",
    "query": "search",
    "info": "show",
    "detail": "show",
    "details": "show",
    "doc": "doctor",
    "doctr": "doctor",
    "health": "doctor",
    "diag": "doctor",
    "cls": "clean",
    "purge": "clean",
    "src": "source",
    "sources": "source",
    "repo": "source",
    "repos": "source",
    "wg": "winget",
    "win": "winget",
    "sub": "submit",
    "submt": "submit",
    "push": "submit",
    "pr": "submit",
    "val": "validate",
    "valid": "validate",
    "valdiate": "validate",
    "create": "new",
    "init": "new",
}


def suggest_words(bad: str, candidates: list[str] | set[str] | tuple[str, ...], limit: int = 3) -> list[str]:
    """Return up to ``limit`` closest matches for a mistyped command, flag, or package name."""
    b = bad.strip().lower()
    if not b or not candidates:
        return []
    cand_list = list(candidates)
    by_lower = {c.lower(): c for c in cand_list}
    out: list[str] = []

    def _add(item: str) -> None:
        if item not in out and len(out) < limit:
            out.append(item)

    if b in _COMMON_COMMAND_HINTS and _COMMON_COMMAND_HINTS[b] in by_lower:
        _add(by_lower[_COMMON_COMMAND_HINTS[b]])

    if len(b) >= 2:
        for k, orig in by_lower.items():
            if k.startswith(b) or (not b.startswith("-") and b in k and len(b) >= 3):
                _add(orig)

    for close in difflib.get_close_matches(b, list(by_lower.keys()), n=limit, cutoff=0.45):
        _add(by_lower[close])

    return out


class SmartArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that automatically suggests closest commands or flags on typos."""

    def add_subparsers(self, **kwargs):
        kwargs.setdefault("parser_class", SmartArgumentParser)
        return super().add_subparsers(**kwargs)

    def format_help(self) -> str:
        from txa.core import i18n

        if i18n.is_vi():
            if self.description:
                self.description = i18n.translate(self.description)
            for action in self._actions:
                if getattr(action, "help", None) and action.help != argparse.SUPPRESS:
                    action.help = i18n.translate(action.help)
                choices = getattr(action, "choices", None)
                if isinstance(choices, dict):
                    for subact in getattr(action, "_choices_actions", ()):
                        if getattr(subact, "help", None):
                            subact.help = i18n.translate(subact.help)
        return i18n.translate(super().format_help())

    def format_usage(self) -> str:
        from txa.core import i18n

        return i18n.translate(super().format_usage())

    def _all_flags_and_subcommands(self) -> tuple[list[str], list[str]]:
        flags: list[str] = []
        subcmds: list[str] = []
        for action in self._actions:
            for opt in getattr(action, "option_strings", ()):
                if opt not in flags:
                    flags.append(opt)
            choices = getattr(action, "choices", None)
            if isinstance(choices, dict):
                for cmd_name, subparser in choices.items():
                    if cmd_name not in subcmds:
                        subcmds.append(cmd_name)
                    for sub_act in getattr(subparser, "_actions", ()):
                        for opt in getattr(sub_act, "option_strings", ()):
                            if opt not in flags:
                                flags.append(opt)
        return flags, subcmds

    def error(self, message: str):
        from txa.core import i18n

        self.print_usage(sys.stderr)
        hint_line = ""
        lbl_cmd = i18n.translate("→ Did you mean:")
        lbl_flg = i18n.translate("→ Did you mean flag:")

        m_choice = re.search(r"invalid choice:\s*'([^']+)'\s*\(choose from ([^)]+)\)", message)
        if m_choice:
            bad = m_choice.group(1)
            raw_choices = [c.strip().strip("'\"") for c in m_choice.group(2).split(",")]
            matches = suggest_words(bad, raw_choices)
            if matches:
                styled = ", ".join(paint(f"{self.prog} {m}", "green", "bold", stream=sys.stderr) for m in matches)
                hint_line = f"  {paint(lbl_cmd, 'cyan', 'bold', stream=sys.stderr)} {styled}\n"

        if not hint_line:
            m_unrec = re.search(r"unrecognized arguments:\s*(.+)$", message)
            if m_unrec:
                bad_tok = m_unrec.group(1).strip().split()[0]
                flags, subcmds = self._all_flags_and_subcommands()
                if bad_tok.startswith("-"):
                    matches = suggest_words(bad_tok, flags)
                    if matches:
                        styled = ", ".join(paint(m, "green", "bold", stream=sys.stderr) for m in matches)
                        hint_line = f"  {paint(lbl_flg, 'cyan', 'bold', stream=sys.stderr)} {styled}\n"
                else:
                    matches = suggest_words(bad_tok, subcmds)
                    if matches:
                        styled = ", ".join(paint(f"{self.prog} {m}", "green", "bold", stream=sys.stderr) for m in matches)
                        hint_line = f"  {paint(lbl_cmd, 'cyan', 'bold', stream=sys.stderr)} {styled}\n"

        err_word = i18n.translate("error")
        err_prefix = paint(f"{self.prog}: {err_word}:", "red", "bold", stream=sys.stderr)
        self.exit(2, f"{err_prefix} {i18n.translate(message)}\n{hint_line}")

