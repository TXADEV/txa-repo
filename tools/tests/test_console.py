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


def test_force_color_zero_means_off(monkeypatch):
    for value in ("0", "", "false"):
        monkeypatch.setenv("FORCE_COLOR", value)
        assert console.paint("x", "red", stream=io.StringIO()) == "x"


def test_download_progress_shows_speed_and_eta(monkeypatch):
    out = FakeTTY()
    monkeypatch.setattr(console.sys, "stderr", out)
    draw = console.progress("dl")
    import time as _time

    draw(1_000_000, 10_000_000)
    _time.sleep(0.7)
    draw(5_000_000, 10_000_000)
    draw(10_000_000, 10_000_000)
    text = out.getvalue()
    assert "50%" in text or "100%" in text
    assert "MiB/s" in text and "ETA" in text and "9.5 MiB" in text


def test_progress_is_silent_when_piped(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(console.sys, "stderr", out)
    console.progress("dl")(1, 2)
    with console.working("x") as busy:
        pass
    assert out.getvalue() == "" and busy.elapsed >= 0


def test_stage_and_human_formats(capsys):
    console.stage(2, 5, "Downloading")
    assert capsys.readouterr().out == "[2/5] Downloading\n"
    assert (console.human_time(5), console.human_time(65)) == ("5s", "1m05s")
    assert (console.human_size(500), console.human_size(2048), console.human_size(5 * 1048576)) == ("500 B", "2 KiB", "5.0 MiB")
