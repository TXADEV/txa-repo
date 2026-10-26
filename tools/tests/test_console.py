import io

import pytest

from txa.core import console


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("NO_COLOR", "FORCE_COLOR", "TERM"):
        monkeypatch.delenv(name, raising=False)


def test_colour_only_on_a_terminal():
    assert console.paint("x", "green", stream=FakeTTY()) == "\033[32mx\033[0m"
    assert console.paint("x", "green", stream=io.StringIO()) == "x"  # piped / redirected


def test_environment_overrides(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert console.paint("x", "red", stream=io.StringIO()).startswith("\033[31m")
    monkeypatch.setenv("NO_COLOR", "1")  # NO_COLOR wins over FORCE_COLOR
    assert console.paint("x", "red", stream=FakeTTY()) == "x"


def test_dumb_terminal_has_no_colour(monkeypatch):
    monkeypatch.setenv("TERM", "dumb")
    assert console.paint("x", "red", stream=FakeTTY()) == "x"


def test_cell_pads_before_colouring(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert console.cell("ab", 5, "green") == "\033[32mab   \033[0m"
    monkeypatch.delenv("FORCE_COLOR")
    assert console.cell("ab", 5, "green") == "ab   "


def test_say_colours_leading_glyph_only(monkeypatch, capsys):
    monkeypatch.setenv("FORCE_COLOR", "1")
    console.say("  ✓ done")
    assert capsys.readouterr().out == "  \033[32;1m✓\033[0m done\n"
    monkeypatch.delenv("FORCE_COLOR")
    console.say("→ plain")
    assert capsys.readouterr().out == "→ plain\n"
