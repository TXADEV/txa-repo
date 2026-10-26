import sys
from pathlib import Path

import pytest

from pebuilder import build_pe
from txa.core import engines, pe, semver, winos
from txa.core.manifest import ManifestError, parse_card, schema_errors


# ------------------------------------------------------------------ strict YAML

def test_duplicate_keys_are_rejected():
    with pytest.raises(ManifestError, match="duplicate key 'kind'"):
        parse_card("txa: 1\nkind: core\nkind: build\n")


@pytest.mark.parametrize("text, word", [
    ("a: &x 1\nb: *x\n", "anchor"),
    ("a: !!str 1\n", "explicit tag"),
])
def test_anchors_and_tags_are_rejected(text, word):
    with pytest.raises(ManifestError, match=word):
        parse_card(text)


def test_multiple_documents_are_rejected():
    with pytest.raises(ManifestError, match="exactly one"):
        parse_card("a: 1\n---\nb: 2\n")


def test_numeric_sha_gets_a_quoting_hint():
    card = parse_card(
        "txa: 1\nkind: build\nref: TXAVL.App@1.0.0\nplatform:\n  os: windows\n  min_build: 10.0.17763\n"
        "targets:\n  - arch: x64\n    engine: nsis\n    source:\n      url: https://e.com/a.exe\n      sha256: 0\n"
    )
    assert any("wrap the value in quotes" in e for e in schema_errors("build", card))


# ------------------------------------------------------------------ versions

@pytest.mark.parametrize("text, expected", [
    ("1.4.2.0", "1.4.2"), ("setup-2.10.3-x64.exe", "2.10.3"), ("1.2.3.4", None), ("v10", None),
])
def test_coerce(text, expected):
    assert semver.coerce(text) == expected


def test_latest_uses_numeric_order():
    assert semver.latest(["1.9.0", "1.10.0", "1.2.11"]) == "1.10.0"


def test_windows_build_table():
    assert winos.describe((10, 0, 17763)) == "Windows 10 1809"
    assert winos.arch_preference("arm64") == ["arm64", "x64", "x86"]


# ------------------------------------------------------------------ PE + engines

def test_pe_header_and_version_info(tmp_path: Path):
    path = tmp_path / "app.exe"
    path.write_bytes(build_pe(0x8664, plus=True, os_version=(10, 0), subsystem_version=(6, 1),
                              strings={"ProductName": "Hello Tool", "CompanyName": "TXA VL", "ProductVersion": "1.4.2"}))
    info = pe.read_pe(path)
    assert info.machine == "x64" and info.pe32_plus
    assert info.os_version == (10, 0) and info.subsystem_version == (6, 1)
    assert info.version_info["ProductName"] == "Hello Tool"
    assert info.version_info["CompanyName"] == "TXA VL"
    assert info.fixed_product_version == "1.4.2.0"


@pytest.mark.parametrize("kwargs, engine", [
    ({"overlay": b"Inno Setup Setup Data (6.2.2)"}, "inno"),
    ({"overlay": b"\xef\xbe\xad\xdeNullsoftInst"}, "nsis"),
    ({"sections": (".text", ".wixburn")}, "wix"),
    ({}, "exe"),
])
def test_engine_detection(tmp_path: Path, kwargs, engine):
    path = tmp_path / "setup.exe"
    path.write_bytes(build_pe(**kwargs))
    found = engines.inspect(path)
    assert found.format == "pe" and found.engine == engine and found.machine == "x86"


def test_garbage_is_unknown(tmp_path: Path):
    path = tmp_path / "page.exe"
    path.write_bytes(b"<!DOCTYPE html><html></html>")
    assert engines.inspect(path).format == "unknown"


@pytest.mark.skipif(sys.platform != "win32", reason="reads a real Windows binary")
def test_real_python_executable():
    info = pe.read_pe(sys.executable)
    assert info.machine in ("x64", "arm64", "x86")
    assert "Python" in info.version_info.get("ProductName", "")


# ------------------------------------------------------------------ switches

@pytest.mark.parametrize("engine, silent, progress, error", [
    ("inno", "/VERYSILENT /NORESTART", "/SILENT", None),
    ("inno", "/S", "/SILENT", "needs one of /VERYSILENT"),
    ("nsis", "/S", "/S", None),
    ("nsis", "/s", "/S", "case-sensitive"),
    ("nsis", "/D=C:\\App /S", "/S", "/D= to be the last"),
    ("msi", "/qn /norestart", "/qb! /norestart", None),
    ("msi", "/VERYSILENT", "/qb", "belongs to another installer engine"),
    ("wix", "/quiet /norestart", "/passive", None),
    ("exe", "--silent && calc", "", "metacharacters"),
])
def test_switch_rules(engine, silent, progress, error):
    errors, _ = engines.check_switches(engine, {"silent": silent, "progress": progress, "custom": ""})
    if error:
        assert any(error in e for e in errors), errors
    else:
        assert not errors, errors


# ------------------------------------------------------------------ uninstall

from txa.pm.uninstall import Program, command, find, split_command  # noqa: E402


def test_uninstall_prefers_msi_then_quiet_then_engine_flags():
    msi = Program("Hello Tool", "1.4.2", "MsiExec.exe /I{12345678-1234-1234-1234-123456789ABC}", None)
    assert command(msi, "msi") == "msiexec.exe /x {12345678-1234-1234-1234-123456789ABC} /qn /norestart"
    quiet = Program("Git", "2.5", r'"C:\Git\unins000.exe"', r'"C:\Git\unins000.exe" /SILENT')
    assert command(quiet, "inno").endswith("/SILENT")
    inno = Program("Hello", "1", r'"C:\Program Files\Hello\unins000.exe"', None)
    assert command(inno, "inno").endswith("/VERYSILENT /SUPPRESSMSGBOXES /NORESTART")
    with pytest.raises(Exception, match="--interactive"):
        command(Program("X", "1", r"C:\x\uninst.exe", None), "exe")


def test_uninstall_match_and_split():
    programs = [Program("Hello Tool 1.4.2 (x64)", "1.4.2", "u.exe", None), Program("Hello Toolbox", "2", "v.exe", None)]
    assert [p.name for p in find("Hello Tool", programs)] == ["Hello Tool 1.4.2 (x64)"]
    assert split_command(r'"C:\Program Files\A\unins.exe" /S') == (r"C:\Program Files\A\unins.exe", "/S")
    assert split_command(r"C:\A\uninstall.exe /allusers /S") == (r"C:\A\uninstall.exe", "/allusers /S")
