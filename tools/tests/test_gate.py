import json
from pathlib import Path

import pytest

from txa.ci import gate

HEAD = "a" * 40
REPO = "TXAVL/txa-repo"


class FakeGitHub:
    """Records calls; answers just enough of the REST/GraphQL surface used by the gate."""

    def __init__(self, files, labels=(), comments=(), review="REVIEW_REQUIRED", armed=False, head=HEAD):
        self.files, self.labels, self.comments = list(files), list(labels), list(comments)
        self.review, self.armed, self.head = review, armed, head
        self.calls: list[tuple] = []

    def get(self, path, **_):
        self.calls.append(("GET", path))
        return {"number": 7, "head": {"sha": self.head}, "labels": [{"name": n} for n in self.labels], "draft": False}

    def paginate(self, path, params=None):
        if path.endswith("/files"):
            return [{"filename": f} for f in self.files]
        if path.endswith("/comments"):
            return list(self.comments)
        return [{"number": 7, "head": {"sha": self.head}, "labels": []}]

    def post(self, path, json=None, **_):
        self.calls.append(("POST", path, json))
        return {}

    def patch(self, path, json=None):
        self.calls.append(("PATCH", path, json))
        return {}

    def put(self, path, json=None, **_):
        self.calls.append(("PUT", path, json))
        return {}

    def delete(self, path, **_):
        self.calls.append(("DELETE", path))

    def graphql(self, query, variables=None):
        self.calls.append(("GRAPHQL", query.strip()))
        if "branchProtectionRules" in query:
            return {"repository": {"defaultBranchRef": {"name": "main"}, "branchProtectionRules": {"nodes": [
                {"pattern": "main", "requiresCodeOwnerReviews": True, "requiredApprovingReviewCount": 1,
                 "requiresApprovingReviews": True}]}}}
        if "pullRequest(number" in query:
            return {"repository": {"pullRequest": {
                "id": "PR_1", "isDraft": False, "reviewDecision": self.review, "headRefOid": self.head,
                "autoMergeRequest": {"enabledAt": "x"} if self.armed else None}}}
        return {}

    # helpers for assertions
    def status(self):
        return next(c[2] for c in self.calls if c[0] == "POST" and "/statuses/" in c[1])

    def added_labels(self):
        return {label for c in self.calls if c[0] == "POST" and c[1].endswith("/labels") for label in c[2]["labels"]}

    def mutations(self):
        return [c[1] for c in self.calls if c[0] == "GRAPHQL" and c[1].startswith("mutation")]


def write_report(tmp_path: Path, verdict="pass", mode="new-version", head=HEAD, targets=True) -> Path:
    path = tmp_path / "report.json"
    target = {"path": "manifests/t/TXAVL/HelloTool/1.4.2", "mode": mode}
    results = [{"step": n, "target": target["path"], "status": "FAIL" if verdict == "fail" and n == 4 else "PASS",
                "messages": []} for n in range(1, 15)] if targets else []
    path.write_text(json.dumps({
        "schema": "txa.report.v1", "pr": 7, "head": head, "verdict": verdict, "outside": [],
        "targets": [target] if targets else [], "results": results, "artifacts": {},
    }), "utf-8")
    return path


MANIFEST_FILES = ["manifests/t/TXAVL/HelloTool/1.4.2/core.txa.yaml"]


def test_verified_pr_gets_labels_status_and_auto_merge(tmp_path):
    gh = FakeGitHub(MANIFEST_FILES, labels=["txa:fix-required"])
    state = tmp_path / "gate.json"
    gate.triage(gh, REPO, write_report(tmp_path), HEAD, state)
    assert gh.added_labels() == {"txa:new-version", "txa:verified-build", "txa:awaiting-review"}
    assert ("DELETE", f"repos/{REPO}/issues/7/labels/txa:fix-required") in gh.calls
    assert gh.status()["state"] == "success" and gh.status()["context"] == "txa/verified"
    gate.merge_gate(gh, gh, REPO, state, tmp_path / "report.json")
    assert any("enablePullRequestAutoMerge" in m for m in gh.mutations())
    assert "waiting for" in json.loads(state.read_text())["step16"]["detail"]


def test_mixed_infra_change_is_never_auto_merged(tmp_path):
    gh = FakeGitHub(MANIFEST_FILES + [".github/workflows/txa-validate.yml"], armed=True)
    state = tmp_path / "gate.json"
    gate.triage(gh, REPO, write_report(tmp_path), HEAD, state)
    assert gh.status()["state"] == "failure"
    assert {"txa:fix-required", "txa:infra-change"} <= gh.added_labels()
    assert "txa:verified-build" not in gh.added_labels()
    gate.merge_gate(gh, gh, REPO, state, tmp_path / "report.json")
    assert any("disablePullRequestAutoMerge" in m for m in gh.mutations())


def test_report_for_another_commit_is_not_trusted(tmp_path):
    gh = FakeGitHub(MANIFEST_FILES)
    gate.triage(gh, REPO, write_report(tmp_path, head="b" * 40), HEAD, tmp_path / "gate.json")
    assert gh.status()["state"] == "error"
    assert "txa:verified-build" not in gh.added_labels()


def test_missing_report_fails_closed(tmp_path):
    gh = FakeGitHub(MANIFEST_FILES)
    gate.triage(gh, REPO, tmp_path / "nope.json", HEAD, tmp_path / "gate.json")
    assert gh.status()["state"] == "error"
    assert "txa:fix-required" in gh.added_labels()


def test_failed_steps_are_listed_in_status(tmp_path):
    gh = FakeGitHub(MANIFEST_FILES)
    gate.triage(gh, REPO, write_report(tmp_path, verdict="fail"), HEAD, tmp_path / "gate.json")
    assert gh.status()["state"] == "failure" and "04" in gh.status()["description"]


def test_infra_only_pr_needs_admin_and_skips_merge(tmp_path):
    gh = FakeGitHub([".github/CODEOWNERS"])
    state = tmp_path / "gate.json"
    gate.triage(gh, REPO, write_report(tmp_path, verdict="infra", targets=False), HEAD, state)
    assert gh.status()["state"] == "success"
    assert gh.added_labels() == {"txa:infra-change", "txa:awaiting-review"}
    gate.merge_gate(gh, gh, REPO, state, tmp_path / "report.json")
    assert not gh.mutations()
    assert json.loads(state.read_text())["step16"]["status"] == "SKIP"


def test_existing_bot_comment_is_updated_not_duplicated(tmp_path):
    comment = {"id": 99, "body": "<!-- txa-bot:report -->\nold", "user": {"type": "Bot"}}
    spoof = {"id": 5, "body": "<!-- txa-bot:report -->\nfake", "user": {"type": "User"}}
    gh = FakeGitHub(MANIFEST_FILES, comments=[spoof, comment])
    gate.triage(gh, REPO, write_report(tmp_path), HEAD, tmp_path / "gate.json")
    assert any(c[0] == "PATCH" and c[1].endswith("/comments/99") for c in gh.calls)
    assert not any(c[0] == "PATCH" and c[1].endswith("/comments/5") for c in gh.calls)
