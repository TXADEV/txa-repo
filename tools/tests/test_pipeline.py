import hashlib
import shutil
from pathlib import Path

import pytest

from pebuilder import build_pe
from txa.ci import steps
from txa.ci.context import init_local
from txa.ci.report import build_report, render_markdown
from txa.create.draft import Draft, TargetDraft, guess_arch, name_from_filename, pascal
from txa.core import engines

DATA = Path(__file__).parent / "data"
SAMPLE = "manifests/t/TXAVL/HelloTool/1.4.2"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    shutil.copytree(DATA / "manifests", tmp_path / "manifests")
    return tmp_path


def run(release: Path, numbers=None, payloads=None) -> dict:
    ctx = init_local([release], offline=True, payloads=payloads)
    for number in numbers or sorted(steps.REGISTRY):
        steps.run_step(ctx, number)
    return build_report(ctx)


def status(report: dict, number: int) -> str:
    return next(r["status"] for r in report["results"] if r["step"] == number)


def test_sample_release_passes(repo: Path):
    report = run(repo / SAMPLE)
    assert report["verdict"] == "pass", report
    markdown = render_markdown(report)
    assert markdown.startswith("<!-- txa-bot:report -->") and "| 16 | Auto-Merge Gate" in markdown


def test_bom_and_crlf_fail_step_01(repo: Path):
    core = repo / SAMPLE / "core.txa.yaml"
    core.write_bytes(b"\xef\xbb\xbf" + core.read_bytes().replace(b"\n", b"\r\n"))
    report = run(repo / SAMPLE, [1])
    messages = " ".join(m["text"] for m in report["results"][0]["messages"])
    assert status(report, 1) == "FAIL" and "BOM" in messages and "CRLF" in messages


def test_zero_width_character_fails_step_01(repo: Path):
    about = repo / SAMPLE / "about.en-US.txa.yaml"
    about.write_text(about.read_text("utf-8").replace("Hello Tool", "Hello​Tool"), "utf-8", newline="\n")
    assert status(run(repo / SAMPLE, [1]), 1) == "FAIL"


def test_odd_indentation_and_tabs_fail_step_02(repo: Path):
    core = repo / SAMPLE / "core.txa.yaml"
    core.write_text(core.read_text("utf-8").replace("  id:", "   id:"), "utf-8", newline="\n")
    report = run(repo / SAMPLE, [2])
    assert status(report, 2) == "FAIL"


def test_block_scalar_content_may_indent_freely(repo: Path):
    # the sample description contains a 4-space indented line inside a |- block
    assert status(run(repo / SAMPLE, [2]), 2) == "PASS"


def test_wrong_directory_fails_step_03(repo: Path):
    wrong = repo / "manifests/t/TXAVL/OtherName/1.4.2"
    shutil.move(str(repo / SAMPLE), wrong)
    assert status(run(wrong, [3]), 3) == "FAIL"


def test_unregistered_namespace_fails_step_03(repo: Path):
    (repo / "policy.txa.yaml").write_text("namespaces:\n  - ACME\n", "utf-8")
    assert status(run(repo / SAMPLE, [3]), 3) == "FAIL"


def test_downgrade_fails_step_04(repo: Path):
    shutil.copytree(repo / SAMPLE, repo / "manifests/t/TXAVL/HelloTool/2.0.0")
    assert status(run(repo / SAMPLE, [4]), 4) == "FAIL"


def test_missing_license_url_for_proprietary_fails_step_05(repo: Path):
    about = repo / SAMPLE / "about.en-US.txa.yaml"
    text = about.read_text("utf-8").replace("  name: MIT\n  url: https://example.com/LICENSE\n", "  name: Proprietary\n")
    about.write_text(text, "utf-8", newline="\n")
    assert status(run(repo / SAMPLE, [5]), 5) == "FAIL"


def test_future_windows_build_fails_step_13(repo: Path):
    build = repo / SAMPLE / "build.txa.yaml"
    build.write_text(build.read_text("utf-8").replace("10.0.17763", "10.0.99999"), "utf-8", newline="\n")
    assert status(run(repo / SAMPLE, [13]), 13) == "FAIL"


def test_payload_steps_with_local_installer(tmp_path: Path):
    payload = tmp_path / "hellotool-1.4.2-x64.exe"
    payload.write_bytes(build_pe(0x14C, overlay=b"Inno Setup Setup Data (6.3.3)",
                                 strings={"ProductName": "Hello Tool", "ProductVersion": "1.4.2"}))
    digest = hashlib.sha256(payload.read_bytes()).hexdigest().upper()
    found = engines.inspect(payload)
    assert guess_arch(payload.name, found) == "x64"
    draft = Draft("TXAVL.HelloTool", "1.4.2", name="Hello Tool", publisher="TXA VL", author="TXA VL",
                  summary="A tiny command line tool that greets you.", license_name="MIT",
                  targets=[TargetDraft("https://example.com/h.exe", "x64", "inno", digest,
                                       payload.stat().st_size, payload, dict(engines.DEFAULT_SWITCHES["inno"]))])
    release = tmp_path / "out" / draft.directory
    release.mkdir(parents=True)
    for name, text in draft.render().items():
        (release / name).write_text(text, "utf-8", newline="\n")
    report = run(release, payloads={draft.directory: {"x64": payload}})
    for number in (1, 2, 3, 4, 5, 9, 11, 12, 13, 14):
        assert status(report, number) == "PASS", (number, report["results"])
    assert status(report, 10) in ("WARN", "PASS")  # unsigned synthetic binary -> warning by policy

    # tamper with one byte -> STEP 11 must catch it
    payload.write_bytes(payload.read_bytes()[:-1] + b"X")
    assert status(run(release, [9, 11], payloads={draft.directory: {"x64": payload}}), 11) == "FAIL"


def test_engine_mismatch_fails_step_12(tmp_path: Path):
    payload = tmp_path / "setup.exe"
    payload.write_bytes(build_pe(0x14C, overlay=b"\xef\xbe\xad\xdeNullsoftInst"))
    digest = hashlib.sha256(payload.read_bytes()).hexdigest().upper()
    draft = Draft("TXAVL.HelloTool", "1.4.2", name="Hello Tool", publisher="TXA VL", author="TXA VL",
                  summary="A tiny command line tool that greets you.", license_name="MIT",
                  targets=[TargetDraft("https://example.com/h.exe", "x86", "inno", digest,
                                       payload.stat().st_size, payload, dict(engines.DEFAULT_SWITCHES["inno"]))])
    release = tmp_path / "out" / draft.directory
    release.mkdir(parents=True)
    for name, text in draft.render().items():
        (release / name).write_text(text, "utf-8", newline="\n")
    report = run(release, [9, 12], payloads={draft.directory: {"x86": payload}})
    assert status(report, 12) == "FAIL"


@pytest.mark.parametrize("filename, expected", [
    ("hellotool-1.4.2-x64-setup.exe", "hellotool"),
    ("npp.8.7.1.Installer.x64.exe", "npp"),
    ("SuperApp_Setup_v2.0.1.msi", "SuperApp"),
])
def test_name_from_filename(filename, expected):
    assert name_from_filename(filename) == expected


def test_pascal():
    assert pascal("Notepad++") == "NotepadPlusPlus"
    assert pascal("hello tool") == "HelloTool"
