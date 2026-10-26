import hashlib
import shutil
import types
import zipfile
from pathlib import Path

import pytest

from pebuilder import build_pe
from txa.ci import steps
from txa.ci.context import init_local
from txa.ci.report import build_report
from txa.core import engines, manifest, portable, userpath
from txa.create.draft import Draft, TargetDraft
from txa.pm import installer
from txa.pm.source import Source

DATA = Path(__file__).parent / "data"


def make_zip(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return path


def app_zip(tmp_path: Path, machine: int = 0x8664, folder: str = "tool-1.0/bin") -> Path:
    exe = build_pe(machine, plus=machine == 0x8664)
    return make_zip(tmp_path / "tool.zip", {
        f"{folder}/tool.exe": exe, f"{folder}/helper.exe": exe, "tool-1.0/README.txt": b"hello",
    })


# ------------------------------------------------------------------ safe archive handling

@pytest.mark.parametrize("name", ["../evil.txt", "a/../../evil.txt", "/abs/evil.txt", "C:/evil.txt",
                                  "dir/nul.txt", "dir/bad:name.txt", "dir/trailing."])
def test_unsafe_entry_names_are_rejected(tmp_path, name):
    archive = make_zip(tmp_path / "bad.zip", {name: b"x"})
    with zipfile.ZipFile(archive) as zf, pytest.raises(portable.ZipProblem):
        portable.entries(zf)


def test_zip_bomb_limits(tmp_path):
    archive = make_zip(tmp_path / "big.zip", {"a/b.bin": b"0" * 4096})
    with zipfile.ZipFile(archive) as zf, pytest.raises(portable.ZipProblem, match="unpacks to"):
        portable.entries(zf, max_unpacked=1024)


def test_analyse_detects_strip_and_path(tmp_path):
    layout = portable.analyse(app_zip(tmp_path))
    assert layout.strip == 1 and layout.suggested_path == ["bin"] and layout.files == 3
    flat = make_zip(tmp_path / "flat.zip", {"tool.exe": build_pe(0x14C), "readme.txt": b"x"})
    layout = portable.analyse(flat)
    assert layout.strip == 0 and layout.suggested_path == ["."]


def test_extract_strips_and_reports_progress(tmp_path):
    seen = []
    dest = tmp_path / "out"
    count = portable.extract(app_zip(tmp_path), dest, 1, lambda done, total: seen.append((done, total)))
    assert count == 3 and (dest / "bin" / "tool.exe").is_file() and (dest / "README.txt").read_bytes() == b"hello"
    assert seen and seen[-1][0] == seen[-1][1]


def test_extract_refuses_to_escape(tmp_path):
    archive = make_zip(tmp_path / "evil.zip", {"x/../../evil.txt": b"x"})
    with pytest.raises(portable.ZipProblem):
        portable.extract(archive, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()


def test_inspect_zip_reads_inner_program(tmp_path):
    found = engines.inspect(app_zip(tmp_path))
    assert (found.format, found.engine, found.payload_arch) == ("zip", "portable", "x64")
    assert found.portable == {"strip": 1, "path": ["bin"]}
    assert engines.inspect(make_zip(tmp_path / "docs.zip", {"a/readme.txt": b"x"})).notes


# ------------------------------------------------------------------ validator

def portable_release(tmp_path: Path, archive: Path, arch="x64", strip=1, path=("bin",)) -> tuple[Path, dict]:
    digest = hashlib.sha256(archive.read_bytes()).hexdigest().upper()
    draft = Draft("TXAVL.Tool", "1.0.0", name="Tool", publisher="TXA VL", author="TXA VL",
                  summary="A portable command line tool for testing.", license_name="MIT", alias="tool", aliases=["tool2"],
                  targets=[TargetDraft("https://example.com/tool.zip", arch, "portable", digest, archive.stat().st_size,
                                       archive, {}, [], {"strip": strip, "path": list(path)})])
    release = tmp_path / "repo" / draft.directory
    release.mkdir(parents=True)
    for name, text in draft.render().items():
        (release / name).write_text(text, "utf-8", newline="\n")
    return release, {draft.directory: {arch: archive}}


def run(release: Path, payloads, numbers):
    ctx = init_local([release], offline=True, payloads=payloads)
    for number in numbers:
        steps.run_step(ctx, number)
    return {r["step"]: r for r in build_report(ctx)["results"]}


def test_portable_release_passes(tmp_path):
    release, payloads = portable_release(tmp_path, app_zip(tmp_path))
    results = run(release, payloads, [1, 2, 3, 9, 10, 11, 12, 13, 14])
    assert results[2]["status"] == "PASS", results[2]
    for number in (9, 11, 12, 14):
        assert results[number]["status"] == "PASS", (number, results[number])
    assert results[10]["status"] == "SKIP"
    assert "tool.exe" in " ".join(m["text"] for m in results[12]["messages"])


def test_wrong_path_or_strip_fails_step_12(tmp_path):
    release, payloads = portable_release(tmp_path, app_zip(tmp_path), path=("tools",))
    assert run(release, payloads, [9, 12])[12]["status"] == "FAIL"
    release, payloads = portable_release(tmp_path / "b", app_zip(tmp_path), strip=0)
    assert run(release, payloads, [9, 12])[12]["status"] == "FAIL"


def test_cpu_mismatch_and_32bit(tmp_path):
    release, payloads = portable_release(tmp_path, app_zip(tmp_path, machine=0x14C))
    assert run(release, payloads, [9, 12])[12]["status"] == "WARN"  # 32-bit programs on an x64 package
    release, payloads = portable_release(tmp_path / "b", app_zip(tmp_path, machine=0xAA64))
    assert run(release, payloads, [9, 12])[12]["status"] == "FAIL"


def test_unsafe_archive_fails_step_12(tmp_path):
    archive = make_zip(tmp_path / "tool.zip", {"../evil.exe": build_pe(0x8664, plus=True)})
    release, payloads = portable_release(tmp_path, archive, strip=0, path=("bin",))
    # the unsafe member is caught while analysing the payload, before any path check
    assert run(release, payloads, [9, 12])[12]["status"] == "FAIL"


def test_schema_rejects_inconsistent_portable_cards():
    base = {"txa": 1, "kind": "build", "ref": "TXAVL.App@1.0.0", "platform": {"os": "windows", "min_build": "10.0.17763"}}
    source = {"url": "https://e.com/a.zip", "sha256": "A" * 64}
    ok = {**base, "targets": [{"arch": "x64", "engine": "portable", "source": source, "portable": {"path": ["bin"]}}]}
    assert not manifest.schema_errors("build", ok)
    for target in (
        {"arch": "x64", "engine": "portable", "source": source},                                         # no portable block
        {"arch": "x64", "engine": "portable", "source": source, "portable": {"path": ["bin"]}, "switches": {"silent": "/S"}},
        {"arch": "x64", "engine": "inno", "source": source, "portable": {"path": ["bin"]}},               # portable on an installer
        {"arch": "x64", "engine": "portable", "source": source, "portable": {"path": ["../x"]}},
        {"arch": "x64", "engine": "portable", "source": source, "portable": {"path": ["C:/x"]}},
    ):
        assert manifest.schema_errors("build", {**base, "targets": [target]}), target


# ------------------------------------------------------------------ aliases

def test_aliases_resolve_and_collide(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(DATA / "manifests", repo / "manifests")
    core = repo / "manifests/t/TXAVL/HelloTool/1.4.2/core.txa.yaml"
    core.write_text(core.read_text("utf-8").replace("  alias: hellotool\n", "  alias: hellotool\n  aliases:\n    - hi\n    - hello\n"),
                    "utf-8", newline="\n")
    source = Source(str(repo), name="t", fallback=False)
    assert source.find("hi") == source.find("hellotool") == source.find("HELLO") == "TXAVL.HelloTool"
    assert "hello" in source.index()["packages"][0]["aliases"]

    other, payloads = portable_release(tmp_path / "other", app_zip(tmp_path))
    target = repo / "manifests/t/TXAVL/Tool/1.0.0"
    shutil.copytree(other, target)
    card = target / "core.txa.yaml"
    card.write_text(card.read_text("utf-8").replace("- tool2", "- hi"), "utf-8", newline="\n")  # steal an alias
    ctx = init_local([target], offline=True)
    steps.run_step(ctx, 3)
    messages = " ".join(m["text"] for r in ctx.results() for m in r["messages"])
    assert "short name 'hi' is already used by TXAVL.HelloTool" in messages


# ------------------------------------------------------------------ PATH + install

def test_user_path_helpers():
    value = r"C:\Windows;%USERPROFILE%\bin;C:\Tools\a"
    assert userpath.merge(value, [r"c:\tools\A", r"C:\New"]) == value + r";C:\New"
    assert userpath.remove(value, [r"C:\TOOLS\a\ "]) == r"C:\Windows;%USERPROFILE%\bin"
    inside = r"C:\apps\ffmpeg\9\bin;C:\apps\ffmpeg2\bin;C:\x"
    assert userpath.remove_under(inside, r"C:\apps\ffmpeg") == r"C:\apps\ffmpeg2\bin;C:\x"


def test_install_and_uninstall_portable(tmp_path, monkeypatch):
    monkeypatch.setenv("TXA_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(installer, "sys", types.SimpleNamespace(platform="win32"))
    calls = []
    monkeypatch.setattr(installer.userpath, "update", lambda **kw: calls.append(kw) or True)
    target = {"arch": "x64", "engine": "portable", "portable": {"strip": 1, "path": ["bin"]}}
    old = installer.install_portable("TXAVL.Tool", "1.0.0", target, app_zip(tmp_path))
    assert (Path(old["dir"]) / "bin" / "tool.exe").is_file()
    assert old["path_dirs"] == [str(Path(old["dir"]) / "bin")]
    assert calls[-1]["add"] == old["path_dirs"] and calls[-1]["drop_under"] == installer.apps_root() / "TXAVL.Tool"

    installer.record("TXAVL.Tool", "1.0.0", target, 0, "Tool", "user", extra=old)
    new = installer.install_portable("TXAVL.Tool", "1.1.0", target, app_zip(tmp_path))
    assert not Path(old["dir"]).exists() and Path(new["dir"]).is_dir()  # the old version is cleaned up

    assert installer.uninstall_portable("TXAVL.Tool") == installer.apps_root() / "TXAVL.Tool"
    assert not (installer.apps_root() / "TXAVL.Tool").exists() and calls[-1] == {"drop_under": installer.apps_root() / "TXAVL.Tool"}


def test_install_portable_rejects_wrong_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("TXA_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(installer, "sys", types.SimpleNamespace(platform="win32"))
    monkeypatch.setattr(installer.userpath, "update", lambda **kw: True)
    target = {"arch": "x64", "engine": "portable", "portable": {"strip": 1, "path": ["nope"]}}
    with pytest.raises(installer.InstallError, match="holds no .exe"):
        installer.install_portable("TXAVL.Tool", "1.0.0", target, app_zip(tmp_path))
    assert not (installer.apps_root() / "TXAVL.Tool" / "1.0.0").exists()
