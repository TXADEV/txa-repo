"""TXA manifest format v1.

Every release lives in ``manifests/<shard>/<Namespace>/<AppName>/<X.Y.Z>/`` and is
described by three kinds of cards:

* ``core.txa.yaml``          identity (id, version, default locale, alias, tags)
* ``build.txa.yaml``         binaries per architecture (engine, source, switches)
* ``about.<lang>.txa.yaml``  human text per locale (name, publisher, summary, license)

``build`` and ``about`` cards point back at the core card through ``ref: <id>@<version>``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

FORMAT = 1
CORE_FILE = "core.txa.yaml"
BUILD_FILE = "build.txa.yaml"
ABOUT_RE = re.compile(r"^about\.(?P<lang>[A-Za-z0-9-]+)\.txa\.yaml$")
ID_RE = re.compile(r"^(?P<namespace>[A-Z][A-Za-z0-9]{1,39})\.(?P<app>[A-Za-z0-9][A-Za-z0-9-]{0,63})$")
LANG_RE = re.compile(r"^[a-z]{2,3}(-[A-Z][a-z]{3})?(-[A-Z]{2}|-\d{3})?$")
ARCHES = ("x64", "x86", "arm64")
ENGINES = ("inno", "nsis", "msi", "wix", "exe", "portable")
SCHEMA_URL = "https://raw.githubusercontent.com/{repo}/main/tools/src/txa/schemas/{kind}.schema.json"


class ManifestError(ValueError):
    pass


def about_file(lang: str) -> str:
    return f"about.{lang}.txa.yaml"


def ref_of(package_id: str, version: str) -> str:
    return f"{package_id}@{version}"


def split_id(package_id: str) -> tuple[str, str]:
    match = ID_RE.match(package_id or "")
    if not match:
        raise ManifestError(f"'{package_id}' is not a valid Namespace.AppName identifier")
    return match["namespace"], match["app"]


def package_dir(package_id: str) -> str:
    namespace, app = split_id(package_id)
    return f"manifests/{namespace[0].lower()}/{namespace}/{app}"


def release_dir(package_id: str, version: str) -> str:
    return f"{package_dir(package_id)}/{version}"


def kind_of(filename: str) -> str | None:
    if filename == CORE_FILE:
        return "core"
    if filename == BUILD_FILE:
        return "build"
    if ABOUT_RE.match(filename):
        return "about"
    return None


# --------------------------------------------------------------------------- parsing


class StrictLoader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate keys instead of silently keeping the last one."""


def _strict_mapping(loader: StrictLoader, node: yaml.MappingNode) -> dict:
    seen: set = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key '{key}'", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node, deep=True)


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _strict_mapping)


def forbidden_tokens(text: str) -> list[str]:
    """Anchors, aliases and explicit tags make cards harder to review - TXA bans them."""
    problems = []
    names = {yaml.AnchorToken: "anchor (&)", yaml.AliasToken: "alias (*)", yaml.TagToken: "explicit tag (!)"}
    for token in yaml.scan(text, Loader=yaml.SafeLoader):
        for cls, label in names.items():
            if isinstance(token, cls):
                problems.append(f"line {token.start_mark.line + 1}: {label} is not allowed")
    return problems


def parse_card(text: str) -> dict:
    """Parse one card strictly. Raises ManifestError with a readable message."""
    try:
        problems = forbidden_tokens(text)
        if problems:
            raise ManifestError("; ".join(problems))
        documents = list(yaml.compose_all(text, Loader=yaml.SafeLoader))
        if len(documents) != 1:
            raise ManifestError(f"expected exactly one YAML document, found {len(documents)}")
        data = yaml.load(text, Loader=StrictLoader)
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark
        where = f"line {mark.line + 1}, column {mark.column + 1}: " if mark else ""
        raise ManifestError(f"{where}{exc.problem or exc}") from None
    except yaml.YAMLError as exc:
        raise ManifestError(str(exc)) from None
    if not isinstance(data, dict):
        raise ManifestError("the root of a card must be a mapping")
    return data


@lru_cache(maxsize=None)
def _validator(kind: str) -> Draft202012Validator:
    text = resources.files("txa").joinpath("schemas", f"{kind}.schema.json").read_text("utf-8")
    return Draft202012Validator(json.loads(text))


def schema(kind: str) -> dict:
    return _validator(kind).schema


def schema_errors(kind: str, card: dict) -> list[str]:
    errors = sorted(_validator(kind).iter_errors(card), key=lambda e: [str(p) for p in e.absolute_path])
    out = []
    for error in errors:
        path = ".".join(str(p) for p in error.absolute_path) or "<root>"
        hint = ""
        if error.validator == "type" and error.validator_value == "string" and not isinstance(error.instance, str):
            hint = " - YAML read it as a number/boolean/date, wrap the value in quotes"
        out.append(f"{path}: {error.message}{hint}")
    return out


# --------------------------------------------------------------------------- writing


class _Dumper(yaml.SafeDumper):
    def increase_indent(self, flow: bool = False, indentless: bool = False):
        # Indent sequences under their key so every level is exactly two spaces.
        return super().increase_indent(flow, False)


def _str_representer(dumper: yaml.SafeDumper, value: str):
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str_representer)


def dump_card(card: dict, header: list[str] | None = None) -> str:
    body = yaml.dump(
        card, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=4096, default_flow_style=False
    )
    lines = [f"# {line}".rstrip() for line in header or []]
    return "\n".join(lines) + ("\n" if lines else "") + body


# --------------------------------------------------------------------------- bundles


@dataclass
class Bundle:
    """All files of one release directory, parsed leniently so every step can report."""

    path: Path
    files: dict[str, bytes] = field(default_factory=dict)
    cards: dict[str, dict] = field(default_factory=dict)
    problems: dict[str, list[str]] = field(default_factory=dict)
    subdirs: list[str] = field(default_factory=list)

    @classmethod
    def read(cls, path: Path) -> "Bundle":
        bundle = cls(Path(path))
        if bundle.path.is_dir():
            for entry in sorted(bundle.path.iterdir()):
                if entry.is_dir():
                    bundle.subdirs.append(entry.name)
                else:
                    bundle.files[entry.name] = entry.read_bytes()
        bundle._parse()
        return bundle

    @classmethod
    def from_texts(cls, path: Path, texts: dict[str, str]) -> "Bundle":
        bundle = cls(Path(path), files={k: v.encode("utf-8") for k, v in texts.items()})
        bundle._parse()
        return bundle

    def _parse(self) -> None:
        for name, raw in self.files.items():
            if kind_of(name) is None:
                continue
            try:
                text = raw.decode("utf-8").lstrip("﻿")
                self.cards[name] = parse_card(text)
            except UnicodeDecodeError as exc:
                self.problems.setdefault(name, []).append(f"not valid UTF-8 ({exc.reason} at byte {exc.start})")
            except ManifestError as exc:
                self.problems.setdefault(name, []).append(str(exc))

    @property
    def core(self) -> dict | None:
        return self.cards.get(CORE_FILE)

    @property
    def build(self) -> dict | None:
        return self.cards.get(BUILD_FILE)

    @property
    def abouts(self) -> dict[str, dict]:
        out = {}
        for name, card in self.cards.items():
            match = ABOUT_RE.match(name)
            if match:
                out[match["lang"]] = card
        return out

    @property
    def package_id(self) -> str | None:
        return ((self.core or {}).get("package") or {}).get("id")

    @property
    def version(self) -> str | None:
        return ((self.core or {}).get("package") or {}).get("version")

    @property
    def default_lang(self) -> str | None:
        return ((self.core or {}).get("locale") or {}).get("default")

    def default_about(self) -> dict | None:
        abouts = self.abouts
        return abouts.get(self.default_lang or "") or next(iter(abouts.values()), None)

    def targets(self) -> list[dict]:
        return list((self.build or {}).get("targets") or [])


def assemble(bundle: Bundle) -> dict[str, Any]:
    """Shape served by the static API for one release."""
    return {"core": bundle.core, "build": bundle.build, "about": bundle.abouts}


def find_repo_root(start: Path) -> Path | None:
    for candidate in [start, *start.parents]:
        if (candidate / "manifests").is_dir():
            return candidate
    return None
