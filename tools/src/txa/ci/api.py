"""Static JSON API (namespace txa.v1) published to GitHub Pages from txa-repo.

Layout under ``<out>/api/txa/v1/``::

    index.json                 every package, newest version summary
    packages/<Id>.json         every version of one package, all three cards
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from txa.constants import API_NAMESPACE
from txa.core import semver
from txa.core.manifest import Bundle, assemble, kind_of, schema_errors


def _valid(bundle: Bundle) -> list[str]:
    problems = [f"{name}: {p}" for name, items in bundle.problems.items() for p in items]
    for name, card in bundle.cards.items():
        problems += [f"{name}: {e}" for e in schema_errors(kind_of(name), card)]
    if not bundle.core or not bundle.build or not bundle.default_about():
        problems.append("incomplete release (core, build and default about cards are required)")
    return problems


def collect(repo_root: Path) -> tuple[dict[str, dict[str, dict]], list[dict]]:
    """Read every valid release under manifests/ -> ({id: {version: cards}}, skipped)."""
    packages: dict[str, dict[str, dict]] = {}
    skipped = []
    for release in sorted((repo_root / "manifests").glob("*/*/*/*")):
        if not release.is_dir() or not semver.is_valid(release.name):
            continue
        bundle = Bundle.read(release)
        problems = _valid(bundle)
        if problems:
            skipped.append({"path": release.relative_to(repo_root).as_posix(), "problems": problems[:5]})
            continue
        packages.setdefault(bundle.package_id, {})[bundle.version] = assemble(bundle)
    return packages, skipped


def package_document(package_id: str, versions: dict[str, dict]) -> dict:
    ordered = sorted(versions, key=semver.parse, reverse=True)
    return {"api": "txa.v1", "id": package_id, "latest": ordered[0], "versions": {v: versions[v] for v in ordered}}


def index_document(packages: dict[str, dict[str, dict]]) -> dict:
    from txa.core import i18n

    index = []
    for package_id in sorted(packages, key=str.lower):
        document = package_document(package_id, packages[package_id])
        latest = document["versions"][document["latest"]]
        about = latest["about"].get(latest["core"]["locale"]["default"]) or next(iter(latest["about"].values()))
        summaries = {loc: ab["summary"] for loc, ab in latest["about"].items() if isinstance(ab, dict) and ab.get("summary")}
        vi_sum = summaries.get("vi-VN") or i18n._SUMMARIES_VI.get(package_id.split(".")[-1].lower())
        if vi_sum:
            summaries.setdefault("vi-VN", vi_sum)
        en_sum = summaries.get("en-US") or i18n._SUMMARIES_EN.get(package_id.split(".")[-1].lower()) or about["summary"]
        if en_sum:
            summaries.setdefault("en-US", en_sum)
        index.append({
            "id": package_id,
            "alias": latest["core"]["package"].get("alias"),
            "aliases": latest["core"]["package"].get("aliases", []),
            "latest": document["latest"],
            "versions": list(document["versions"]),
            "name": about["name"],
            "publisher": about["publisher"],
            "summary": about["summary"],
            "summaries": summaries,
            "tags": latest["core"].get("tags", []),
            "arches": [t["arch"] for t in latest["build"]["targets"]],
        })
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {"api": "txa.v1", "generated": generated, "count": len(index), "packages": index}


def build_api(repo_root: Path, out: Path, pending: list[dict] | None = None) -> dict:
    packages, skipped = collect(repo_root)
    api = out / API_NAMESPACE
    (api / "packages").mkdir(parents=True, exist_ok=True)
    for package_id, versions in packages.items():
        document = package_document(package_id, versions)
        (api / "packages" / f"{package_id}.json").write_text(json.dumps(document, ensure_ascii=False, indent=1), "utf-8")
    manifest = index_document(packages)
    index, generated = manifest["packages"], manifest["generated"]
    waiting = sorted(pending or [], key=lambda e: (e["id"].lower(), e["version"]))
    (api / "pending.json").write_text(json.dumps(
        {"api": "txa.v1", "generated": generated, "count": len(waiting), "packages": waiting}, ensure_ascii=False, indent=1), "utf-8")
    (api / "index.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")
    (out / ".nojekyll").write_text("", "utf-8")
    rows = "".join(
        f"<tr><td><code>{p['id']}</code></td><td>{p['latest']}</td><td>{p['summary']}</td></tr>" for p in index
    )
    (out / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>TXA package index</title>"
        "<style>body{font:15px system-ui;margin:2rem auto;max-width:60rem;padding:0 1rem}"
        "td,th{padding:.3rem .6rem;border-bottom:1px solid #8884;text-align:left}</style>"
        f"<h1>TXA package index</h1><p>{len(index)} package(s) · generated {generated} · "
        f"<a href='{API_NAMESPACE}/index.json'>index.json</a></p>"
        f"<table><tr><th>Id</th><th>Latest</th><th>Summary</th></tr>{rows}</table>",
        "utf-8",
    )
    return {"packages": len(index), "pending": len(waiting), "skipped": skipped, "out": str(api)}
