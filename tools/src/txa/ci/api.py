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


def build_api(repo_root: Path, out: Path) -> dict:
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

    api = out / API_NAMESPACE
    (api / "packages").mkdir(parents=True, exist_ok=True)
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    index = []
    for package_id in sorted(packages, key=str.lower):
        versions = packages[package_id]
        ordered = sorted(versions, key=semver.parse, reverse=True)
        latest = versions[ordered[0]]
        about = latest["about"].get(latest["core"]["locale"]["default"]) or next(iter(latest["about"].values()))
        document = {"api": "txa.v1", "id": package_id, "latest": ordered[0], "versions": {v: versions[v] for v in ordered}}
        (api / "packages" / f"{package_id}.json").write_text(json.dumps(document, ensure_ascii=False, indent=1), "utf-8")
        index.append({
            "id": package_id,
            "alias": latest["core"]["package"].get("alias"),
            "latest": ordered[0],
            "versions": ordered,
            "name": about["name"],
            "publisher": about["publisher"],
            "summary": about["summary"],
            "tags": latest["core"].get("tags", []),
            "arches": [t["arch"] for t in latest["build"]["targets"]],
        })
    manifest = {"api": "txa.v1", "generated": generated, "count": len(index), "packages": index}
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
    return {"packages": len(index), "skipped": skipped, "out": str(api)}
