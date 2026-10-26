"""Pipeline state shared between steps.

In CI every step runs as its own process (one GitHub Actions step each), so the
state lives in ``$RUNNER_TEMP/txa-ci/state.json``. Locally everything stays in memory.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import yaml

from txa import __version__
from txa.core import semver
from txa.core.manifest import Bundle, find_repo_root

POLICY_FILE = "policy.txa.yaml"

DEFAULT_POLICY: dict = {
    "namespaces": ["TXAVL"],
    "limits": {
        "max_payload_mb": 2048,
        "max_redirects": 8,
        "http_timeout_s": 30,
        "max_line_length": 400,
    },
    "network": {
        "min_regions_ok": 3,
        # EDNS Client Subnet hints: the resolver answers as if the client sat in that network.
        "regions": {
            "vn-hanoi": "113.160.0.0/24",
            "sg-singapore": "203.116.0.0/24",
            "jp-tokyo": "126.0.0.0/24",
            "au-sydney": "1.120.0.0/24",
            "de-frankfurt": "79.192.0.0/24",
            "us-east": "73.0.0.0/24",
        },
    },
    "signature": {"require": False},
    "os": {"floor": "10.0.17763"},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def parse_policy(text: str | None) -> dict:
    if not text:
        return copy.deepcopy(DEFAULT_POLICY)
    data = yaml.safe_load(text) or {}
    data.pop("txa", None)
    data.pop("kind", None)
    return _merge(DEFAULT_POLICY, data)


# --------------------------------------------------------------------------- git


def git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8")
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout if proc.returncode == 0 else ""


def changed_files(repo: Path, base: str, head: str) -> list[tuple[str, str]]:
    out = git(repo, "diff", "--name-status", "--no-renames", "-z", f"{base}...{head}")
    fields = [f for f in out.split("\0") if f]
    return [(fields[i][0], fields[i + 1]) for i in range(0, len(fields) - 1, 2)]


def tree_dirs(repo: Path, rev: str, path: str) -> list[str]:
    out = git(repo, "ls-tree", "-d", "--name-only", f"{rev}:{path}", check=False)
    return [line for line in out.splitlines() if line]


def show(repo: Path, rev: str, path: str) -> str | None:
    proc = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=repo, capture_output=True, text=True, encoding="utf-8")
    return proc.stdout if proc.returncode == 0 else None


# --------------------------------------------------------------------------- context


class Context:
    def __init__(self, data: dict, state_dir: Path | None = None):
        self.data = data
        self.state_dir = state_dir
        self.payloads: dict[str, dict[str, Path]] = {}
        self._bundles: dict[str, Bundle] = {}

    # ---- persistence
    @staticmethod
    def default_state_dir() -> Path:
        base = os.environ.get("TXA_STATE_DIR") or (
            str(Path(os.environ["RUNNER_TEMP"]) / "txa-ci") if os.environ.get("RUNNER_TEMP") else ".txa-ci"
        )
        return Path(base)

    @classmethod
    def load(cls, state_dir: Path) -> "Context":
        path = state_dir / "state.json"
        if not path.exists():
            raise SystemExit(f"txaci: no pipeline state at {path} - run `txaci init` first")
        return cls(json.loads(path.read_text("utf-8")), state_dir)

    def save(self) -> None:
        if self.state_dir is None:
            return
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "state.json").write_text(json.dumps(self.data, indent=2), "utf-8")

    # ---- accessors
    @property
    def mode(self) -> str:
        return self.data["mode"]

    @property
    def repo_root(self) -> Path:
        return Path(self.data["repo_root"])

    @property
    def policy(self) -> dict:
        return self.data["policy"]

    @property
    def offline(self) -> bool:
        return bool(self.data.get("offline"))

    @property
    def targets(self) -> list[dict]:
        return self.data["targets"]

    @property
    def outside(self) -> list[str]:
        return self.data.get("outside", [])

    def bundle(self, target: dict) -> Bundle:
        key = target["path"]
        if key not in self._bundles:
            self._bundles[key] = Bundle.read(self.repo_root / key)
        return self._bundles[key]

    def artifact(self, target: dict, arch: str) -> dict:
        return self.data.setdefault("artifacts", {}).setdefault(f"{target['path']}#{arch}", {})

    def payload_dir(self, target: dict, arch: str) -> Path:
        import hashlib

        tag = hashlib.sha1(f"{target['path']}#{arch}".encode()).hexdigest()[:12]
        root = self.state_dir or Path(".txa-ci")
        return root / "payloads" / tag

    def record(self, step: int, target: dict, status: str, messages: list[dict]) -> None:
        results = [r for r in self.data.setdefault("results", []) if not (r["step"] == step and r["target"] == target["path"])]
        results.append({"step": step, "target": target["path"], "status": status, "messages": messages})
        self.data["results"] = sorted(results, key=lambda r: (r["target"], r["step"]))

    def results(self) -> list[dict]:
        return self.data.get("results", [])


def _base_state(mode: str, repo_root: Path, policy: dict) -> dict:
    return {
        "tool": f"txaci {__version__}",
        "mode": mode,
        "repo_root": str(repo_root.resolve()),
        "policy": policy,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "targets": [],
        "outside": [],
        "results": [],
        "artifacts": {},
    }


def init_ci(repo_root: Path, base: str, head: str, pr: int | None, state_dir: Path) -> Context:
    """Build the change set of a pull request. The policy always comes from the base commit."""
    data = _base_state("ci", repo_root, parse_policy(show(repo_root, base, POLICY_FILE)))
    data.update({"base": base, "head": head, "pr": pr})
    changes = changed_files(repo_root, base, head)
    data["changes"] = [{"status": s, "path": p} for s, p in changes]
    releases: dict[str, dict] = {}
    for status, path in changes:
        parts = PurePosixPath(path).parts
        if not parts or parts[0] != "manifests":
            data["outside"].append(path)
            continue
        release = "/".join(parts[:5]) if len(parts) >= 6 else str(PurePosixPath(path).parent)
        entry = releases.setdefault(release, {"path": release, "changed": [], "deleted": []})
        entry["changed"].append(path)
        if status == "D":
            entry["deleted"].append(path)
    for release, entry in sorted(releases.items()):
        package = str(PurePosixPath(release).parent)
        version = PurePosixPath(release).name
        base_versions = tree_dirs(repo_root, base, package)
        existed = version in base_versions
        still_there = (repo_root / release).is_dir() and any((repo_root / release).iterdir())
        if not still_there:
            mode = "removal"
        elif existed:
            mode = "update"
        elif base_versions:
            mode = "new-version"
        else:
            mode = "new-app"
        data["targets"].append({
            "path": release,
            "mode": mode,
            "existing_versions": [v for v in base_versions if v != version],
            "changed": entry["changed"],
        })
    ctx = Context(data, state_dir)
    ctx.save()
    return ctx


def init_local(release_dirs: list[Path], offline: bool = False,
               payloads: dict[str, dict[str, Path]] | None = None) -> Context:
    """Validate release directories on disk (used by `txaci validate` and txacreate)."""
    first = Path(release_dirs[0]).resolve()
    repo_root = find_repo_root(first) or first.parent
    policy_file = repo_root / POLICY_FILE
    policy = parse_policy(policy_file.read_text("utf-8") if policy_file.exists() else None)
    data = _base_state("local", repo_root, policy)
    data["offline"] = offline
    ctx = Context(data)
    for directory in release_dirs:
        directory = Path(directory).resolve()
        try:
            rel = directory.relative_to(repo_root).as_posix()
        except ValueError:
            rel = directory.as_posix()
        siblings = [p.name for p in directory.parent.iterdir() if p.is_dir() and p.name != directory.name] \
            if directory.parent.is_dir() else []
        siblings = [s for s in siblings if semver.is_valid(s)]
        data["targets"].append({
            "path": rel,
            "mode": "new-version" if siblings else "new-app",
            "existing_versions": siblings,
            "changed": [],
        })
        if payloads and rel in payloads:
            ctx.payloads[rel] = payloads[rel]
        elif payloads and str(directory) in payloads:
            ctx.payloads[rel] = payloads[str(directory)]
    return ctx
