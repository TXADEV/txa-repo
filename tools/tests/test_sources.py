import shutil
from pathlib import Path

import pytest

from txa.core import config
from txa.pm.source import Source, SourceError, Sources, check_url

DATA = Path(__file__).parent / "data"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TXA_HOME", str(tmp_path / "home"))
    return tmp_path


def make_repo(root: Path) -> Path:
    shutil.copytree(DATA / "manifests", root / "manifests")
    return root


def add(name: str, url: str, **extra):
    custom = config.sources()[1:]
    config.save_custom_sources([*custom, {"name": name, "url": url, **extra}])


def test_check_url_rules(home):
    assert check_url("https://pkgs.example.com/api/txa/v1/") == "https://pkgs.example.com/api/txa/v1"
    assert check_url("http://localhost:8080/api") == "http://localhost:8080/api"
    with pytest.raises(SourceError, match="https"):
        check_url("http://pkgs.example.com")
    with pytest.raises(SourceError, match="manifests"):
        check_url(str(home))
    repo = make_repo(home / "repo")
    assert check_url(str(repo)) == str(repo.resolve())


def test_local_folder_source_serves_txa_v1(home):
    repo = make_repo(home / "repo")
    source = Source(str(repo), name="corp", fallback=False)
    index = source.index()
    assert index["api"] == "txa.v1" and index["packages"][0]["id"] == "TXAVL.HelloTool"
    assert source.find("hellotool") == "TXAVL.HelloTool"  # alias
    version, cards = source.release("TXAVL.HelloTool")
    assert version == "1.4.2" and cards["build"]["targets"][0]["engine"] == "inno"


def test_official_first_and_disable(home):
    add("corp", str(make_repo(home / "repo")))
    assert [s["name"] for s in config.sources()] == ["txa", "corp"]
    config.save_custom_sources(config.sources()[1:], official_enabled=False)
    assert [s.name for s in Sources().items] == ["corp"]


def test_same_id_in_two_sources_needs_explicit_choice(home):
    config.save_custom_sources([], official_enabled=False)
    add("corp", str(make_repo(home / "a")))
    add("lab", str(make_repo(home / "b")))
    with pytest.raises(SourceError, match="several sources"):
        Sources().resolve("TXAVL.HelloTool")
    source, package_id = Sources("lab").resolve("TXAVL.HelloTool")
    assert source.name == "lab" and package_id == "TXAVL.HelloTool"


def test_unknown_source_name(home):
    with pytest.raises(SourceError, match="no source named"):
        Sources("nope")


def test_credentials_only_go_to_the_source_host(home, monkeypatch):
    monkeypatch.setenv("CORP_TOKEN", "s3cret")
    source = Source("https://pkgs.corp.example/api/txa/v1", name="corp", token_env="CORP_TOKEN", fallback=False)
    assert source.download_headers("https://pkgs.corp.example/files/a.exe") == {"Authorization": "Bearer s3cret"}
    assert source.download_headers("https://cdn.vendor.example/a.exe") == {}
