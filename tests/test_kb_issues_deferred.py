"""The refresh job's `run` holds no write token: the issue calls its tools
make (sweep, size budget) are written to KB_ISSUES_OUT, and `land` replays
them with the App's token (.github/workflows/kb-refresh.yaml)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "kb"))

import web  # noqa: E402


class FakeGitHub:
    def __init__(self, open_issues: dict[str, int] | None = None):
        self.open = open_issues or {}
        self.calls: list[tuple] = []

    def find_issue(self, marker, label):
        return next((n for m, n in self.open.items() if m in marker), None)

    def create_issue(self, title, body, labels):
        self.calls.append(("create", title, body, labels))

    def edit_issue(self, number, title, body):
        self.calls.append(("edit", number, title, body))

    def close_issue(self, number, comment):
        self.calls.append(("close", number, comment))


def test_run_defers_and_land_replays(tmp_path, monkeypatch):
    out = tmp_path / "issues.jsonl"
    monkeypatch.setenv("KB_ISSUES_OUT", str(out))
    untouched = FakeGitHub()
    assert web.keep_issue(untouched, "sweep", "kb-watch", "KB sweep", "two sources moved") == "deferred"
    assert web.keep_issue(untouched, "budget", "kb-watch", "KB over budget", None) == "deferred"
    assert untouched.calls == []  # nothing reached GitHub from `run`

    monkeypatch.delenv("KB_ISSUES_OUT")
    gh = FakeGitHub({"kb-watch:budget": 7})
    assert web.replay_issues(str(out), gh) == ["sweep: opened", "budget: closed"]
    assert gh.calls[0][0] == "create" and "<!-- kb-watch:sweep -->" in gh.calls[0][2]
    assert gh.calls[1][:2] == ("close", 7)
