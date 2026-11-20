"""tools/ab: the A/B regression check, on made-up data only.

The proxy records and replays, refuses writes and other tenants; the clock
freezes what the app reads; the report names what changed per feature and
flags regressions; recordings never land in a git tree or outlive 30 days;
and one record-then-replay round trip through the real driver gives an empty
diff (the determinism the check rests on)."""

from __future__ import annotations

import gzip
import importlib.util
import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.ab_fake_store import CHAR, FakeStore

ROOT = Path(__file__).resolve().parents[1]
AB = ROOT / "tools" / "ab"


def _load(name: str):
    if str(AB) not in sys.path:
        sys.path.insert(0, str(AB))
    spec = importlib.util.spec_from_file_location(f"ab_{name}", AB / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


proxy = _load("proxy")
diff = _load("diff")
ab = _load("ab")


@pytest.fixture
def store():
    s = FakeStore()
    yield s
    s.close()


def _get(url: str, headers: dict | None = None, data: bytes | None = None, method: str | None = None):
    req = urllib.request.Request(url, headers=headers or {}, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# ------------------------------------------------------------------ proxy


def test_record_then_replay_gives_the_same_answers_and_names_a_miss(store, tmp_path):
    cas = proxy.Cassette()
    rec = proxy.Proxy("record", cas, {"vl": store.url, "vm": store.url})
    vl = rec.start("vl")
    q = "/select/logsql/query?query=%7Bcomponent%3D%22wow%22%7D&start=2026-01-01T00:00:00Z&limit=10"
    live = _get(vl + q)
    rec.stop()
    assert live[0] == 200 and live[1]
    cas.save(tmp_path / "c.jsonl.gz")
    assert len(store.requests) == 1

    rep = proxy.Proxy("replay", proxy.Cassette.load(tmp_path / "c.jsonl.gz"))
    vl = rep.start("vl")
    assert _get(vl + q) == live
    code, body = _get(vl + "/select/logsql/query?query=other")
    rep.stop()
    assert code == 502 and b"not in the recording" in body
    assert rep.stats["exact"] == 1 and rep.stats["miss"] == 1
    assert rep.misses[0]["query"] == [["query", "other"]]
    assert len(store.requests) == 1, "a replay never reaches a store"


def test_writes_and_other_tenants_never_reach_the_store(store):
    rec = proxy.Proxy("record", proxy.Cassette(), {"vl": store.url, "vm": store.url})
    vl, vm = rec.start("vl"), rec.start("vm")
    refused = [
        _get(vl + "/insert/jsonline", data=b"{}\n", method="POST"),
        _get(vl + "/delete/run_task", data=b"x", method="POST"),
        _get(vl + "/select/logsql/query?query=x", headers={"AccountID": "3"}),
        _get(vl + "/select/logsql/query?query=x", headers={"AccountID": "0", "ProjectID": "1"}),
        _get(vm + "/api/v1/import/prometheus", data=b"x 1", method="POST"),
        _get(vm + "/api/v1/admin/tsdb/delete_series?match[]=x", method="POST"),
    ]
    ok = _get(vl + "/select/logsql/query?query=x", headers={"AccountID": "0", "ProjectID": "0"})
    rec.stop()
    assert [c for c, _ in refused] == [403] * len(refused)
    assert ok[0] == 200
    assert [(m, p) for m, p, _ in store.requests] == [("GET", "/select/logsql/query")]
    assert rec.stats["refused"] == len(refused)


def test_a_moved_clock_matches_nearby_but_never_another_range():
    e = {"store": "vl", "method": "GET", "path": "/select/logsql/query",
         "query": [["query", "x"], ["start", "2004-11-23T00:00:00Z"], ["end", "2026-01-01T10:00:00Z"]],
         "body": None, "tenant": {}, "status": 200, "ctype": "application/json", "resp": "A"}
    cas = proxy.Cassette([e])
    near = {**e, "query": [["query", "x"], ["start", "2004-11-23T00:00:00Z"], ["end", "2026-01-01T10:00:02Z"]]}
    far = {**e, "query": [["query", "x"], ["start", "2025-12-11T10:00:00Z"], ["end", "2026-01-01T10:00:00Z"]]}
    # A range that moved by minutes is another range (a session's bounds), not a ticking clock.
    moved = {**e, "query": [["query", "x"], ["start", "2004-11-23T00:00:00Z"], ["end", "2026-01-01T10:04:41Z"]]}
    assert cas.find(near)[1] == "fuzzy"
    assert cas.find(far) == (None, "miss")
    assert cas.find(moved) == (None, "miss")


def test_extend_answers_from_the_recording_and_records_only_what_is_new(store):
    cas = proxy.Cassette()
    rec = proxy.Proxy("record", cas, {"vl": store.url, "vm": store.url})
    vl = rec.start("vl")
    _get(vl + "/select/logsql/query?query=a")
    rec.stop()
    ext = proxy.Proxy("extend", cas, {"vl": store.url, "vm": store.url}, tag="newer")
    vl = ext.start("vl")
    _get(vl + "/select/logsql/query?query=a")
    _get(vl + "/select/logsql/query?query=b")
    ext.stop()
    assert len(store.requests) == 2
    assert [e.get("added_by") for e in cas.entries] == [None, "newer"]


# ------------------------------------------------------------------ clock


def test_the_clock_is_frozen_for_the_app_and_randomness_is_seeded(tmp_path):
    code = (
        f"import sys; sys.path.insert(0, {str(AB)!r})\n"
        "import abclock; abclock.install('2026-03-04T05:06:07Z')\n"
        "import time, secrets\n"
        "from datetime import datetime, date, UTC\n"
        "from wowstats import stores\n"
        "t = stores.datetime.now(UTC)\n"
        "print(t.isoformat(), datetime.utcnow().isoformat(), date.today().isoformat(), int(time.time()),\n"
        "      isinstance(datetime.fromtimestamp(0, UTC), stores.datetime), secrets.token_hex(4))\n"
    )
    out = [subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
           for _ in range(2)]
    assert out[0] == out[1]
    now, utc, today, ts, inst, tok = out[0].split()
    assert now == "2026-03-04T05:06:07+00:00" and utc == "2026-03-04T05:06:07"
    assert today == "2026-03-04" and ts == str(int(datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC).timestamp()))
    assert inst == "True" and len(tok) == 8


# ------------------------------------------------------------------ diff


def _run(tmp: Path, name: str, files: dict, run: dict | None = None, proxy_rep: dict | None = None) -> Path:
    d = tmp / name
    for rel, v in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(v if isinstance(v, str) else json.dumps(v))
    (d / "_run.json").write_text(json.dumps(run or {"version": "1", "steps": {"web": "ok"}}))
    if proxy_rep is not None:
        (d / "_proxy.json").write_text(json.dumps(proxy_rep))
    return d


def test_the_report_says_what_changed_per_feature_and_flags_regressions(tmp_path):
    slots = lambda chest, gain: [{"slot": "Chest", "best": {"name": chest}, "gain": gain},  # noqa: E731
                                 {"slot": "Feet", "best": {"name": "Boots"}, "gain": 2}]
    a = _run(tmp_path, "a", {
        "api/gear.json": {"status": 200, "body": {"slots": slots("Robe", 4.5)}},
        "plan/compute_2h.json": {"end": {"level": 34.2}},
        "mcp/levels.json": {"is_error": False, "structured": {"levels": [1, 2]}},
        "mcp/deaths.json": {"is_error": False, "structured": {"n": 3}},
        "api/journal.json": {"status": 200, "body": {"days": [{"day": "2026-01-01", "xp": 10}]}},
        "api/overview.json": {"status": 200, "body": {"rate": 1.5}},
        "public/index.html": "<html><body><h1>Lvl 34</h1><script>x()</script></body></html>",
    })
    b = _run(tmp_path, "b", {
        "api/gear.json": {"status": 200, "body": {"slots": slots("Tunic", 6.0), "reason": "more stamina"}},
        "plan/compute_2h.json": {"end": {"level": 34.6}},
        "mcp/levels.json": {"is_error": True, "content": ["boom"]},
        "mcp/deaths.json": {"is_error": False, "structured": {}},
        "api/journal.json": {"status": 200, "body": {"days": [{"day": "2026-01-01", "xp": "NaN"}]}},
        "public/index.html": "<html><body><h1>Lvl 35</h1><script>y()</script></body></html>",
        "api/talents.json": {"status": 200, "body": {"changes": []}},
    }, proxy_rep={"exact": 5, "miss": 1, "misses": [{"store": "vl", "path": "/select/logsql/query",
                                                     "query": [["query", "new"]]}]})
    res = diff.diff_runs(a, b)
    text = diff.report(res, "0.6.1", "rc")
    assert "## Gear and upgrades" in text
    assert '`slots[slot=Chest].best.name`: "Robe" → "Tunic"' in text, text
    assert "`slots[slot=Chest].gain`: 4.5 → 6.0 (+1.5)" in text
    assert "`reason` new" in text
    assert "`end.level`: 34.2 → 34.6 (+0.4)" in text
    assert "Feet" not in text, "an unchanged slot is not reported"
    assert "Lvl 34" in text and "Lvl 35" in text and "x()" not in text, "pages compare as text, scripts aside"
    regs = "\n".join(f"{r['file']}: {r['what']}" for r in res["regressions"])
    assert "mcp/levels.json: fails now: MCP error" in regs
    assert "mcp/deaths.json: empty now" in regs
    assert "api/journal.json: bad value" in regs and "NaN" in regs
    assert "api/overview.json: answer gone" in regs
    assert "recording miss" in regs
    assert "api/talents.json (new in B)" in text


def test_a_route_a_version_lacks_is_not_a_regression(tmp_path):
    a = _run(tmp_path, "a", {"api/meta.json": {"status": 404, "body": {"error": "not found"}}})
    b = _run(tmp_path, "b", {"api/meta.json": {"status": 200, "body": {"score": 70}}})
    res = diff.diff_runs(a, b)
    assert not res["regressions"]
    assert diff.diff_runs(b, a)["regressions"], "the other way round, B lost a route"


def test_run_leftovers_are_normalised_away(tmp_path):
    a = _run(tmp_path, "a", {"mcp/x.json": {"p": "/tmp/ab-work-abc123/site", "u": "http://127.0.0.1:51234/x",
                                            "s": "https://github.com/example-org/wow-stats", "b": "b1763456789"}})
    b = _run(tmp_path, "b", {"mcp/x.json": {"p": "/tmp/ab-work-zz9/site", "u": "http://127.0.0.1:40000/x",
                                            "s": "https://github.com/example-user/wow-mcp", "b": "b1763999999"}})
    assert diff.diff_runs(a, b)["files"] == {}


# ------------------------------------------------------------------ places and dates


def test_outputs_never_go_inside_a_git_tree(tmp_path, monkeypatch):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    with pytest.raises(ab.AbError, match="git work tree"):
        ab.home(str(tmp_path / "repo" / "wow-ab"))
    monkeypatch.delenv("WOW_AB_HOME", raising=False)
    assert not ab.in_git_tree(ab.REPO.parent / "wow-ab") or not (ab.REPO.parent / ".git").exists(), \
        "the default home sits next to the checkout, not in it"
    h = ab.home(str(tmp_path / "out"))
    assert sorted(p.name for p in h.iterdir()) == ["cache", "recordings", "reports", "runs"]


def test_a_recording_older_than_30_days_is_refused(tmp_path):
    t = datetime(2026, 1, 1, tzinfo=UTC)
    meta = {"recorded_at": "2026-01-01T00:00:00Z", "delete_by": "2026-01-31"}
    ab.check_age(meta, t + timedelta(days=29))
    with pytest.raises(ab.AbError, match="older than 30 days.*2026-01-31"):
        ab.check_age(meta, t + timedelta(days=31))


# ------------------------------------------------------------------ the whole loop


def test_record_then_replay_through_the_driver_is_deterministic(store, tmp_path):
    """The real driver, this checkout, a made-up store: the replay's answers
    equal the recorded run's, and nothing left the machine on replay."""
    pytest.importorskip("fastmcp")
    h = tmp_path / "home"
    args = ["--home", str(h)]
    assert ab.main([*args, "record", "--vl", store.url, "--vm", store.url, "--here", "--char", CHAR,
                    "--name", "r", "--env", f"DEFAULT_CHAR={CHAR}"]) == 0
    seen = len(store.requests)
    assert seen > 0
    assert ab.main([*args, "replay", "r", "--here", "--label", "again"]) == 0
    assert len(store.requests) == seen
    rep = json.loads((h / "runs" / "again" / "_proxy.json").read_text())
    assert rep["miss"] == 0 and rep["refused"] == 0
    assert ab.main([*args, "diff", "record-r", "again"]) == 0
    meta = json.loads((h / "recordings" / "r" / "meta.json").read_text())
    assert meta["delete_by"] == (datetime.fromisoformat(meta["recorded_at"].replace("Z", "+00:00"))
                                 + timedelta(days=30)).date().isoformat()
    with gzip.open(h / "recordings" / "r" / "cassette.jsonl.gz", "rt") as f:
        assert all(json.loads(line)["tenant"].get("AccountID", "0") == "0" for line in f)
    for part in ("api/chars.json", "plan/compute_2h.json", "mcp/_tools.json"):
        assert (h / "runs" / "again" / part).exists(), part
