"""The semantic layer (wowstats.analysis.semantic) and its DuckDB engine
(wowstats.analysis.duck), on the local demo (dev/seed.py: the docs site's
made-up character, Nibbet-Gearholt).

The reference answer: his level 23 took 14,229 s played (Polestar's split),
his slowest is 32, and his Blackfathom Deeps run, entered at 23, lasted
4,980 s (generate.BFD_SECONDS)."""

from __future__ import annotations

import itertools
import json
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

from wowstats.analysis import TABLES, drive, duck, pages, prep, semantic
from wowstats.analysis.sql import QueryError
from wowstats.mcp import route, tools

ROOT = Path(__file__).resolve().parents[2]
CHAR = "Nibbet-Gearholt"
END = "2026-11-18"
warnings.filterwarnings("ignore", module="boring_semantic_layer")


@pytest.fixture(scope="module")
def seed(tmp_path_factory):
    out = tmp_path_factory.mktemp("seed") / "seed.ndjson"
    subprocess.run([sys.executable, str(ROOT / "dev" / "seed.py"), "--out", str(out), "--end-day", END], check=True,
                   capture_output=True)
    return [json.loads(x) for x in out.read_text().splitlines() if x.strip()]


@pytest.fixture(scope="module")
def demo(seed):
    rows = [r for r in seed if r.get("char") == CHAR]
    db = prep.build(rows, CHAR, route.reference())
    con = duck.mirror(db, TABLES)
    semantic.prepare(con)
    return {"db": db, "con": con, "layer": semantic.Layer(con), "ro": duck.ReadOnlyDuck(con),
            "play": duck.ReadOnlyDuck(con, tables=TABLES)}


@pytest.fixture(scope="module")
def testchar():
    """Testchar (tests/testchar.py): a story written by hand, for the checks
    that count on it (each quest accepted as the one two before is handed
    in, the gold moved by flights alone)."""
    from tests import testchar as tc

    char = "Testchar-Demo"
    db = prep.build([r for r in tc.rows() if r.get("char") == char], char, route.reference())
    con = duck.mirror(db, TABLES)
    semantic.prepare(con)
    return {"db": db, "con": con, "layer": semantic.Layer(con), "ro": duck.ReadOnlyDuck(con)}


def ask(demo, **q):
    return semantic.run(demo["layer"], demo["ro"], q)


def table(got) -> list[dict]:
    return [dict(zip(got["columns"], r, strict=True)) for r in got["rows"]]


# ---------------------------------------------------------------- the model file


def test_every_metric_and_dimension_resolves_and_runs(demo):
    """Each metric alone, and each dimension of its model with it: compiles
    and runs on the demo. No dimension without a metric to group."""
    cat = semantic.catalogue()
    assert len(cat["metrics"]) >= 30 and len({m["name"] for m in cat["metrics"]}) == len(cat["metrics"])
    for m in cat["metrics"]:
        assert m["kind"] in ("simple", "ratio", "derived"), m["name"]
        assert m["unit"] and m["description"] and m["caveats"], m["name"]
        ask(demo, metrics=[m["name"]])
    seen = set()
    for d in cat["dimensions"]:
        assert d["description"], d["name"]
        for model in d["models"]:
            m = next(x for x in cat["metrics"] if x["model"] == model)
            got = ask(demo, metrics=[m["name"]], group_by=[d["name"]], limit=3)
            assert got["columns"][-1] == m["name"]
            seen.add(d["name"])
    assert seen == {d["name"] for d in cat["dimensions"]}
    # Every model reads one of the layer's views, and every view is read.
    assert {c["table"] for c in semantic.spec().values()} == set(semantic.VIEWS)


def test_the_catalogue_page_is_generated_from_the_model():
    text = (ROOT / "docs" / "reference" / "semantic-layer.md").read_text(encoding="utf-8")
    for m in semantic.catalogue()["metrics"]:
        assert f"`{m['name']}`" in text, m["name"]


# ---------------------------------------------------------------- the compiler


def test_the_reference_answer(demo):
    rows = {r["level"]: r for r in table(ask(demo, metrics=["played_s", "online_s", "active_s", "calendar_s"],
                                             group_by=["level"]))}
    assert rows[23]["played_s"] == 14229
    assert max(rows, key=lambda lv: rows[lv]["played_s"] or 0) == 32
    bfd = table(ask(demo, metrics=["run_s"], group_by=["dungeon"],
                    where=[{"dimension": "dungeon", "op": "=", "value": "Blackfathom Deeps"}]))
    assert bfd == [{"dungeon": "Blackfathom Deeps", "run_s": 4980}]


def test_metrics_of_two_models_aggregate_apart_then_join(demo):
    """online_s (timeline) and played_s (levels) by level: the level's split
    is not summed once per timeline row (no fan-out)."""
    got = table(ask(demo, metrics=["online_s", "played_s", "kills"], group_by=["level"]))
    db = demo["db"]
    online = dict(db.execute("SELECT level, SUM(dur_s) FROM timeline GROUP BY level"))
    played = dict(db.execute("SELECT level, played_s FROM levels"))
    kills = dict(db.execute("SELECT level, COUNT(*) FROM records WHERE kind = 'kill' GROUP BY level"))
    assert got and {r["level"] for r in got} == set(online) | set(played)
    for r in got:
        assert r["online_s"] == online.get(r["level"]) and r["played_s"] == played.get(r["level"])
        assert (r["kills"] or 0) == kills.get(r["level"], 0)
    sql = ask(demo, metrics=["online_s", "played_s"], group_by=["level"])["sql"]
    assert "FULL OUTER JOIN" in sql and "sem_timeline" in sql and "sem_levels" in sql


def test_the_order_of_the_models_never_changes_the_rows(demo):
    """Three models with a sparse one (dungeon runs: a few levels only):
    every order of the metrics gives the same rows, one per level. Joining
    each model on the first one's keys split a level in two whenever the
    first model had no row for it (review 2026-11-21, A)."""
    names = ["run_s", "played_s", "online_s"]
    want = None
    for order in itertools.permutations(names):
        got = table(ask(demo, metrics=list(order), group_by=["level"]))
        levels = [r["level"] for r in got]
        assert len(levels) == len(set(levels)), (order, levels)
        rows = sorted(tuple(r[c] for c in ["level", *names]) for r in got)
        want = want or rows
        assert rows == want, order
    runs = {r[0] for r in want if r[1] is not None}
    assert 0 < len(runs) < len(want)  # the first model really is sparse


def test_a_week_is_the_sum_of_its_days(demo):
    days = table(ask(demo, metrics=["online_s"], grain="day", limit=500))
    weeks = table(ask(demo, metrics=["online_s"], grain="week", limit=500))
    assert len(days) > len(weeks) >= 1 and "day" in days[0] and "week" in weeks[0]
    assert sum(r["online_s"] for r in days) == pytest.approx(sum(r["online_s"] for r in weeks))
    assert sum(r["online_s"] for r in weeks) == pytest.approx(demo["db"].execute(
        "SELECT SUM(dur_s) FROM timeline").fetchone()[0])


def test_since_and_until_cut_on_local_days(demo):
    days = table(ask(demo, metrics=["online_s"], grain="day", limit=500))
    first = days[0]["day"][:10]
    one = table(ask(demo, metrics=["online_s"], since=first, until=first))
    assert one == [{"online_s": days[0]["online_s"]}]


def test_filter_values_are_bound_never_written(demo):
    evil = "Westfall'); DROP TABLE levels; --"
    got = ask(demo, metrics=["online_s"], group_by=["zone"],
              where=[{"dimension": "zone", "op": "in", "values": [evil, "Duskwood"]},
                     {"dimension": "level", "op": ">=", "value": 30}])
    assert evil not in got["sql"] and "Duskwood" not in got["sql"] and "$p0" in got["sql"]
    assert got["params"] == {"p0": evil, "p1": "Duskwood", "p2": 30}
    assert [r["zone"] for r in table(got)] == ["Duskwood"]
    assert demo["db"].execute("SELECT COUNT(*) FROM levels").fetchone()[0] > 0
    assert demo["ro"].first_row("SELECT COUNT(*) AS n FROM levels")["n"] > 0


@pytest.mark.parametrize("q, why", [
    ({"metrics": ["played_s; DROP TABLE levels"]}, "unknown"),
    ({"metrics": ["played_s"], "group_by": ["level) UNION SELECT 1 --"]}, "unknown"),
    ({"metrics": ["played_s"], "where": [{"dimension": "level", "op": "OR 1=1 --", "value": 1}]}, "op is one of"),
    ({"metrics": ["played_s"], "where": [{"dimension": "level", "op": "=", "value": {"$gt": 1}}]}, "number or a text"),
    ({"metrics": ["played_s"], "where": [{"dimension": "level", "op": "=", "value": True}]}, "number or a text"),
    ({"metrics": ["played_s"], "where": [{"dimension": "level", "op": "=", "value": float("nan")}]}, "finite"),
    ({"metrics": ["played_s"], "where": [{"dimension": "level", "op": "=", "value": "x" * 500}]}, "longer"),
    ({"metrics": ["played_s"], "where": [{"dimension": "time", "op": ">", "value": "2026"}]}, "since and until"),
    ({"metrics": ["played_s"], "since": "2026-11-01' OR 1=1"}, "a date"),
    ({"metrics": ["played_s"], "grain": "year"}, "grain"),
    ({"metrics": ["played_s"], "order_by": ["level; DROP TABLE x"]}, "order_by"),
    ({"metrics": ["played_s"], "limit": 10 ** 9}, "limit"),
    ({"metrics": ["played_s"], "group_by": ["zone"]}, "can't be grouped"),
    ({"metrics": []}, "at least one"),
])
def test_names_and_values_outside_the_model_are_refused(demo, q, why):
    with pytest.raises(QueryError, match=why):
        demo["layer"].compile(q)


# ---------------------------------------------------------------- the engine


@pytest.mark.parametrize("sql", [
    "SELECT * FROM read_csv('/etc/passwd')",
    "SELECT * FROM read_parquet('https://example.com/x.parquet')",
    "SELECT * FROM read_text('/etc/hosts')",
    "SELECT * FROM 'https://example.com/data.csv'",
    "SELECT * FROM glob('/*')",
    "SELECT * FROM query('SELECT 1')",
    "ATTACH '/tmp/x.duckdb' AS x",
    "INSTALL httpfs",
    "LOAD httpfs",
    "SET enable_external_access = true",
    "COPY levels TO '/tmp/levels.csv'",
    "CREATE TABLE x AS SELECT 1",
    "SELECT 1; DROP TABLE levels",
    "SELECT repeat('x', 1000000000)",
    "SELECT lpad('x', 500000000, 'y')",
    "SELECT len(range(300000000))",
    "SELECT * FROM sem_levels",
    "SELECT * FROM duckdb_settings()",
])
def test_play_sql_reads_the_tables_and_nothing_else(demo, sql):
    with pytest.raises(QueryError):
        demo["play"].run(sql)
    assert demo["play"].first_row("SELECT COUNT(*) AS n FROM levels")["n"] > 0


def test_play_sql_answers_like_before(demo):
    """The numbers the SQLite engine gave, in DuckDB's dialect."""
    cols, rows, cut = demo["play"].run("SELECT level, played_s FROM levels WHERE level = 23")
    assert (cols, rows, cut) == (["level", "played_s"], [(23, 14229.0)], False)
    row = demo["play"].first_row("SELECT COUNT(*) FILTER (WHERE kind = 'kill') AS kills, 7/2 AS d, 7//2 AS i, "
                                 "1.5 AS dec FROM records")
    assert row == {"kills": demo["db"].execute("SELECT COUNT(*) FROM records WHERE kind='kill'").fetchone()[0],
                   "d": 3.5, "i": 3, "dec": 1.5}
    stop = duck.ReadOnlyDuck(demo["con"], seconds=0.3)
    with pytest.raises(QueryError, match="stopped after"):
        stop.run("SELECT COUNT(*) FROM range(100000000000)")


def test_the_mirror_has_every_table_row_and_column(demo):
    db, con = demo["db"], demo["con"]
    for t in TABLES:
        cols = [c[1] for c in db.execute(f'PRAGMA table_info("{t}")')]
        assert [r[0] for r in con.execute(f'DESCRIBE "{t}"').fetchall()] == cols, t
        assert con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] == \
            db.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0], t


# ---------------------------------------------------------------- over MCP


class SeedStore:
    """The store a tenant's tools read: the demo seed for tenant 0, nothing
    for anyone else (as VictoriaLogs answers per AccountID)."""

    vl_url, vl_archive_url, logs_filter, hot_days, stream = "http://unused", "", "feed:wow-*", 89, "feed:wow-*"

    def __init__(self, rows, account_id=0):
        self.rows, self.account_id = rows, account_id

    def mine(self, char):
        return [dict(r) for r in self.rows if r.get("char") == char] if self.account_id == 0 else []

    def logs(self, query, start=None, end=None, *a, **k):
        char = query.split('char:="')[1].split('"')[0] if 'char:="' in query else CHAR
        if pages.ROWS_QUERY in query:  # the layer's narrow prep (the deaths and dungeons tools)
            return [r for r in self.mine(char) if r.get("feed") in ("wow-level", "wow-dungeon")
                    or (r.get("feed") == "wow-recorder" and r.get("kind") in ("level", "session", "zone", "death"))
                    or (r.get("feed") in ("wow-character", "wow-gear") and r.get("class"))]
        feed = next((f for f in ("wow-level", "wow-dungeon") if f'feed:="{f}"' in query), None)
        return [r for r in self.mine(char) if feed and r.get("feed") == feed]


@pytest.fixture()
def lab(seed, tmp_path, monkeypatch):
    stores = {0: SeedStore(seed, 0), 1: SeedStore(seed, 1)}

    def query(store, stream, char):
        return stores[getattr(store, "account_id", 0)].mine(char)

    monkeypatch.setattr(prep, "query", query)
    monkeypatch.setenv("ANALYSIS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("KB_GAME_TABLES", "off")
    drive._WS.clear()
    return stores


def call(store, name, args):
    return tools.call(tools.Ctx(store=store, default_char=CHAR), name, args)


def test_dungeon_time_per_zone_per_week_over_mcp(lab):
    out = call(lab[0], "query_metric", {"char": CHAR, "metrics": ["dungeon_s"], "group_by": ["zone"],
                                        "grain": "week", "where": [{"dimension": "dungeon", "op": "is not null"}],
                                        "order_by": ["-dungeon_s"]})
    lines = out["result"].splitlines()
    assert lines[0] == "zone\tweek\tdungeon_s" and out["rows"] >= 3
    db = prep.build(lab[0].mine(CHAR), CHAR, route.reference())
    want = db.execute("SELECT SUM(dur_s) FROM timeline WHERE instance IS NOT NULL").fetchone()[0]
    assert sum(float(x.split("\t")[2]) for x in lines[1:]) == pytest.approx(want)
    assert out["definitions"]["dungeon_s"]["kind"] == "simple" and "WITH m0 AS" in out["sql"]
    assert "propose_finding" in out["evidence"]


def test_xp_per_hour_by_level_over_mcp(lab):
    out = call(lab[0], "query_metric", {"char": CHAR, "metrics": ["xp_per_hour", "level_xp", "played_s"],
                                        "group_by": ["level"], "where": [{"dimension": "level", "op": "=",
                                                                          "value": 23}]})
    head, row = out["result"].splitlines()[:2]
    assert head == "level\txp_per_hour\tlevel_xp\tplayed_s"
    level, xph, xp, played = row.split("\t")
    assert (level, xp, played) == ("23", "29400", "14229")
    assert float(xph) == pytest.approx(29400 / (14229 / 3600), rel=1e-3)
    assert out["params"] == {"p0": 23} and out["definitions"]["xp_per_hour"]["kind"] == "ratio"
    cat = call(lab[0], "list_metrics", {})
    assert {"played_s", "online_s", "active_s", "calendar_s"} <= set(cat["models"]["levels"]) | set(
        cat["models"]["timeline"])


def test_a_money_change_says_why_it_is_empty_over_mcp(lab):
    """query_metric: gold_delta_copper per day carries its evidence count, and
    the answer counts the days measured, unknown (one snapshot) and
    unavailable (none), so an empty cell never reads as a zero."""
    out = call(lab[0], "query_metric", {"char": CHAR, "metrics": ["gold_delta_copper", "online_s"], "grain": "day",
                                        "limit": 500})
    head = out["result"].splitlines()[0].split("\t")
    assert head == ["day", "gold_delta_copper", "gold_delta_copper_evidence", "online_s"]
    ev = out["min_evidence"]["gold_delta_copper"]
    assert ev["at_least"] == 2 and ev["measured"] + ev["unknown"] + ev["unavailable"] == out["rows"]
    for row in json_rows(out):
        assert (row[1] == "") == (row[2] < 2), row
    cat = {m["name"]: m for m in call(lab[0], "list_metrics", {"names": ["gold_delta_copper", "vs_route"]})[
        "metrics"]}
    assert cat["gold_delta_copper"]["min_evidence"]["at_least"] == 2
    assert cat["vs_route"]["eligibility"]["where"]


def test_levels_and_dungeons_tools_agree_with_the_layer(lab):
    """The existing tools read the store; the layer reads the prep: same
    numbers for every level and every run on the demo."""
    levels = call(lab[0], "levels", {"char": CHAR})["levels"]
    layer = {r[0]: r for r in json_rows(call(lab[0], "query_metric", {
        "char": CHAR, "metrics": ["played_s", "level_xp"], "group_by": ["level"], "limit": 500}))}
    assert len(levels) >= 10
    for lv in levels:
        got = layer[lv["level"]]
        assert (round(got[1]), got[2]) == (lv["seconds"], lv["xp"]), lv["level"]
    assert next(lv for lv in levels if lv["level"] == 23)["seconds"] == 14229
    runs = call(lab[0], "dungeons", {"char": CHAR})["runs"]
    by_name = {r[0]: r for r in json_rows(call(lab[0], "query_metric", {
        "char": CHAR, "metrics": ["run_s", "run_xp", "run_xp_per_hour"], "group_by": ["dungeon"]}))}
    assert len(runs) == len(by_name) == 4
    for r in runs:
        got = by_name[r["name"]]
        assert (round(got[1]), got[2]) == (r["seconds"], r["xp"]), r["name"]
        assert round(got[3]) == r["xp_per_hour"], r["name"]
    assert by_name["Blackfathom Deeps"][1] == 4980


def json_rows(out):
    def num(x):
        try:
            return float(x) if "." in x else int(x)
        except ValueError:
            return x
    return [[num(x) for x in line.split("\t")] for line in out["result"].splitlines()[1:]]


def test_a_friend_cant_query_the_owners_character(lab):
    """The owner's workspace is built first (and cached); the friend, in
    another tenant, naming the owner's character gets nothing of it."""
    mine = call(lab[0], "query_metric", {"char": CHAR, "metrics": ["played_s"], "group_by": ["level"]})
    assert "9749" in mine["result"]
    for name, args in (("query_metric", {"char": CHAR, "metrics": ["played_s"], "group_by": ["level"]}),
                       ("play_sql", {"char": CHAR, "query": "SELECT level, played_s FROM levels"}),
                       ("propose_finding", {"char": CHAR, "finding": {
                           "key": "x", "title": "{played_s}", "claim": "{played_s} s", "expect": {"played_s": 9749},
                           "metric_query": {"metrics": ["played_s"], "where": [
                               {"dimension": "level", "op": "=", "value": 30}]}}})):
        out = call(lab[1], name, args)
        body = json.dumps(out)
        assert "9749" not in body and "Blackfathom" not in body, (name, body[:300])
        assert out.get("answer", "x") is None or "error" in out or out.get("stored") is False, (name, out)


def test_propose_finding_takes_a_query_metric_call(lab):
    mq = {"metrics": ["played_s", "dungeon_s"], "where": [{"dimension": "level", "op": "=", "value": 30}]}
    good = {"key": "level-30-dungeon", "title": "Level 30: {dungeon_s} s in dungeons",
            "claim": "Level 30 took {played_s} s, {dungeon_s} s of it inside a dungeon.",
            "expect": {"played_s": 9749}, "relevance": 2, "action": None, "metric_query": mq,
            "constants": [{"value": 30, "label": "level"}]}
    ok = call(lab[0], "propose_finding", {"char": CHAR, "finding": good})
    assert ok["stored"], ok
    # The level is declared, not measured: without the declaration it is refused.
    bare = call(lab[0], "propose_finding", {"char": CHAR, "finding": {**good, "key": "level-30-bare",
                                                                     "constants": []}})
    assert not bare["stored"] and "30 in the title is neither a placeholder" in bare["why"]
    from wowstats.analysis import load_state

    kept = load_state({"ANALYSIS_STATE_DIR": str(Path(__import__("os").environ["ANALYSIS_STATE_DIR"]))},
                      CHAR)["findings"]["level-30-dungeon"]
    assert kept["engine"] == "duckdb" and kept["sql_params"] == {"p0": 30} and kept["metric_query"]["metrics"] == [
        "played_s", "dungeon_s"]
    wrong = call(lab[0], "propose_finding", {"char": CHAR, "finding": {**good, "key": "level-30-wrong",
                                                                      "expect": {"played_s": 9000}}})
    assert not wrong["stored"] and "re-run" in wrong["why"]
    both = call(lab[0], "propose_finding", {"char": CHAR, "finding": {**good, "sql": "SELECT 1 AS played_s"}})
    assert not both["stored"] and "not both" in both["why"]
    # The nightly recheck runs it again on DuckDB; a finding kept before 0.7 stays on SQLite.
    from wowstats.analysis import recheck

    db = prep.build(lab[0].mine(CHAR), CHAR, route.reference())
    old = {"key": "old", "sql": "SELECT SUM(kind='kill') AS k FROM records", "title_template": "{k}",
           "claim_template": "{k} kills", "holds": "k > 0"}
    kept_all = {"level-30-dungeon": dict(kept), "old": old}
    info = {"confirmed": 0, "faded": 0, "dropped": []}
    from wowstats.analysis import _duck_runner
    from wowstats.analysis.sql import ReadOnly

    recheck(kept_all, ReadOnly(db), "2026-11-20T00:00:00+00:00", info, duck=_duck_runner(db))
    assert info["confirmed"] == 2 and info["faded"] == 0, info


@pytest.fixture()
def testchar_lab(lab):
    """Testchar (tests/testchar.py), whose kills carry no XP, in tenant 0."""
    from tests import testchar

    lab[0].rows = testchar.rows()
    return lab


def test_the_slice_metrics_over_mcp_keep_the_findings_contract(testchar_lab):
    """The 0.7.0 slice's metrics reach query_metric, play_sql and
    propose_finding: Testchar's kills carry no XP, so kill XP is unknown
    and the finding says it with placeholders; a number typed in is refused."""
    lab, CHAR = testchar_lab, "Testchar-Demo"  # noqa: N806
    names = {n for ms in call(lab[0], "list_metrics", {})["models"].values() for n in ms}
    assert {"kills_without_xp", "kill_xp_per_online_hour", "gold_earned_copper", "gear_changes"} <= names
    out = call(lab[0], "query_metric", {"char": CHAR, "metrics": ["kills", "kills_without_xp", "kill_xp"]})
    head, row = out["result"].splitlines()[:2]
    kills, without, xp = row.split("\t")
    assert head == "kills\tkills_without_xp\tkill_xp" and kills == without and int(kills) > 0
    weeks = call(lab[0], "play_sql", {"char": CHAR, "query": "SELECT week, kills, deaths FROM per_week ORDER BY week"})
    assert weeks["rows"] >= 1 and weeks["result"].splitlines()[0] == "week\tkills\tdeaths"
    mq = {"metrics": ["kills", "kills_without_xp"]}
    good = {"key": "kills-without-xp", "title": "{kills_without_xp} kills don't say their XP",
            "claim": "{kills_without_xp} of {kills} kills carry no XP: kill XP is unknown.",
            "expect": {"kills_without_xp": int(without)}, "relevance": 1, "action": None, "metric_query": mq}
    ok = call(lab[0], "propose_finding", {"char": CHAR, "finding": good})
    assert ok["stored"], ok
    typed = call(lab[0], "propose_finding", {"char": CHAR, "finding": {
        **good, "key": "kills-typed", "claim": f"{int(kills) + 1} kills carry no XP."}})
    assert not typed["stored"] and "placeholder" in typed["why"]


def test_queries_from_several_threads_share_one_database(demo):
    """The MCP answers several calls at once, and the nightly run builds its own
    copy meanwhile: each call takes its own cursor, so none sees another's rows."""
    from concurrent.futures import ThreadPoolExecutor

    def one(i):
        if i % 2:
            return demo["play"].first_row(f"SELECT played_s + {i} AS v FROM levels WHERE level = 30")["v"] - i
        return table(ask(demo, metrics=["played_s"], where=[{"dimension": "level", "op": "=", "value": 30}]))[0][
            "played_s"]

    with ThreadPoolExecutor(4) as pool:
        got = list(pool.map(one, range(16)))
    other = duck.mirror(demo["db"], TABLES)
    assert got == [9749] * 16
    assert other.execute("SELECT played_s FROM levels WHERE level = 30").fetchone()[0] == 9749


# ---------------------------------------------------------------- the four former gaps


def test_quests_pair_accept_and_hand_in(testchar):
    demo = testchar
    """Testchar picks each quest up when the one two before it is handed in:
    every hand-in after level 22's first two has its accept; one abandoned,
    one still open."""
    db = demo["db"]
    status = dict(db.execute("SELECT status, COUNT(*) FROM quests GROUP BY 1"))
    turnins = db.execute("SELECT COUNT(*) FROM records WHERE kind = 'turnin'").fetchone()[0]
    assert status == {"done": turnins - 2, "untimed": 2, "abandoned": 1, "open": 1}
    got = {r["status"]: r for r in table(ask(demo, metrics=["quest_count", "quest_reward_xp", "quest_s"],
                                             group_by=["status"]))}
    assert got["untimed"]["quest_s"] is None and got["abandoned"]["quest_reward_xp"] is None
    # Every hand-in's XP is in a quest row, once.
    assert sum(r["quest_reward_xp"] or 0 for r in got.values()) == table(ask(demo, metrics=["quest_xp"]))[0][
        "quest_xp"]


def test_quest_time_is_shared_so_it_adds_up(demo):
    """quest_s splits each online second among the quests open then: the
    quests' times sum to the online time with at least one quest open, and
    each is at most its own open time and its wall time."""
    db = demo["db"]
    spans = db.execute("SELECT accepted_t, COALESCE(closed_t, 1e12) FROM quests WHERE accepted_t IS NOT NULL"
                       ).fetchall()
    covered = sum(dur for t0, dur in db.execute("SELECT t0, dur_s FROM timeline")
                  if any(a <= t0 < c for a, c in spans))
    total = table(ask(demo, metrics=["quest_s", "quest_open_s", "quests_open_avg"]))[0]
    assert total["quest_s"] == pytest.approx(covered) and covered > 0
    assert total["quest_open_s"] > total["quest_s"] and total["quests_open_avg"] > 1
    for q_s, open_s, wall in db.execute("SELECT quest_s, open_online_s, wall_s FROM quests WHERE status = 'done'"):
        assert q_s <= open_s + 1e-6 and (wall is None or open_s <= wall + 1e-6)


def test_quest_xp_per_minute(demo):
    """The showcase's former gap: which quest gave the most XP per minute."""
    rows = table(ask(demo, metrics=["quest_xp_per_min", "done_quest_xp", "done_quest_s"], group_by=["quest"],
                     order_by=["-quest_xp_per_min"], limit=500))
    top = rows[0]
    assert top["quest_xp_per_min"] == pytest.approx(top["done_quest_xp"] / (top["done_quest_s"] / 60))
    # Open, abandoned and untimed quests have no rate (NULL, last), never NaN.
    rates = [r["quest_xp_per_min"] for r in rows]
    assert all(x is None or x == x for x in rates) and rates[-1] is None
    assert all(x is not None for x in rates[:5])


def test_guide_and_step_with_the_routes_titles(demo):
    db = demo["db"]
    rows = table(ask(demo, metrics=["online_s"], group_by=["guide", "step", "step_title"], limit=500))
    want = {(g, s): d for g, s, d in db.execute("SELECT guide, step, SUM(dur_s) FROM timeline GROUP BY 1, 2")}
    assert {(r["guide"], r["step"]): r["online_s"] for r in rows} == pytest.approx(want)
    titled = {(g, s): t for g, s, t in db.execute("SELECT guide, step, title FROM route_steps")}
    assert any(r["step_title"] for r in rows)
    for r in rows:
        assert r["step_title"] == titled.get((r["guide"], r["step"]))
    guides = table(ask(demo, metrics=["online_s"], group_by=["guide_title"]))
    assert any(r["guide_title"] for r in guides)


def test_unexplained_gold_per_level(testchar):
    demo = testchar
    """The purse's moves between snapshots, less the quest money the records
    saw: on Testchar, the rest is the flights (80-260 copper each)."""
    db = demo["db"]
    rows = table(ask(demo, metrics=["gold_moved_copper", "gold_quest_copper", "unexplained_gold_delta"],
                     group_by=["level"], where=[{"dimension": "dings", "op": "=", "value": 0}]))
    assert rows
    for r in rows:
        assert r["unexplained_gold_delta"] == r["gold_moved_copper"] - r["gold_quest_copper"]
    flights = {lv for (lv,) in db.execute("SELECT DISTINCT level FROM records WHERE kind = 'taxi'")}
    for r in rows:
        if r["level"] in flights:
            assert -260 * 2 <= r["unexplained_gold_delta"] < 0, r
    snaps = [m for (m,) in db.execute("SELECT money_copper FROM snapshots ORDER BY t")]
    assert table(ask(demo, metrics=["gold_moved_copper"]))[0]["gold_moved_copper"] == snaps[-1] - snaps[0]
    # Additive, unlike gold_delta_copper: the weeks sum to the whole.
    weeks = table(ask(demo, metrics=["gold_moved_copper"], grain="week"))
    assert sum(r["gold_moved_copper"] for r in weeks) == snaps[-1] - snaps[0]


def test_a_ratio_across_two_models(demo):
    """kills (records) over combat_s (timeline), by level and by week: each
    model aggregates apart, the ratio runs on the joined row."""
    db = demo["db"]
    kills = dict(db.execute("SELECT level, COUNT(*) FROM records WHERE kind = 'kill' GROUP BY 1"))
    combat = dict(db.execute("SELECT level, SUM(dur_s) FROM timeline WHERE combat = 1 GROUP BY 1"))
    got = table(ask(demo, metrics=["kills_per_combat_hour"], group_by=["level"]))
    assert [r for r in got if r["kills_per_combat_hour"] is not None]
    for r in got:
        k, c = kills.get(r["level"], 0), combat.get(r["level"])
        want = k / (c / 3600) if c else None
        assert r["kills_per_combat_hour"] == (pytest.approx(want) if want is not None else None), r
    # Only the asked metric comes out; its parts stay inside.
    assert ask(demo, metrics=["kills_per_combat_hour"], group_by=["level"])["columns"] == [
        "level", "kills_per_combat_hour"]
    week = table(ask(demo, metrics=["kills_per_combat_hour", "kills", "combat_s"], grain="week"))
    for r in week:
        assert r["kills_per_combat_hour"] == pytest.approx(r["kills"] / (r["combat_s"] / 3600))
    d = ask(demo, metrics=["kills_per_combat_hour"])["definitions"]["kills_per_combat_hour"]
    assert d["models"] == ["records", "timeline"] and d["of"] == ["kills", "combat_s"]


def test_a_cross_model_metric_keeps_to_shared_dimensions(demo):
    with pytest.raises(QueryError, match="shared by"):
        ask(demo, metrics=["quest_xp_per_played_hour"], group_by=["zone"])
    with pytest.raises(QueryError, match="shared by"):
        ask(demo, metrics=["kills_per_combat_hour"], group_by=["kind"])  # records only


@pytest.mark.parametrize("expr", ["kills.__class__", "kills ** 2", "__import__('os')", "kills / combat_s if 1 else 0",
                                  "kills + 'x'", "kills / nope", "kills / deaths"])
def test_cross_model_expressions_are_arithmetic_on_metrics(expr):
    owner = {m["name"]: m["model"] for m in semantic.catalogue()["metrics"] if m["model"] != "cross"}
    with pytest.raises(ValueError):
        semantic._cross_terms("x", expr, owner)


# ---------------------------------------------------------------- the query process (wowstats.analysis.duckproc)


def test_the_query_process_answers_like_in_process(demo):
    """play_sql, the judge and the metrics through the child give the same
    rows, cut and refusals as the in-process runner; a slow query stops."""
    from wowstats.analysis import duckproc

    w = duckproc.Worker(demo["db"], TABLES, seconds=0.5)
    try:
        play = duck.ReadOnlyDuck(demo["con"], tables=TABLES, worker=w)
        q = "SELECT level, played_s, zone FROM levels l LEFT JOIN (SELECT 1 AS zone) z ON true ORDER BY level"
        assert play.run(q, 5) == demo["play"].run(q, 5)
        assert play.first_row("SELECT 7/2 AS d, NULL AS n, 'é' AS t") == {"d": 3.5, "n": None, "t": "é"}
        with pytest.raises(QueryError, match="not allowed"):
            play.run("SELECT * FROM read_csv('/etc/passwd')")
        metrics = duck.ReadOnlyDuck(demo["con"], worker=w)
        got = semantic.run(demo["layer"], metrics, {"metrics": ["played_s"], "group_by": ["level"]})
        assert got == ask(demo, metrics=["played_s"], group_by=["level"])
        with pytest.raises(QueryError, match="stopped after"):
            play.run("SELECT COUNT(*) FROM range(100000000000)")
        assert play.run("SELECT 1 AS one")[1] == [(1,)]  # the next query still answers
    finally:
        w.close()


def test_a_compiled_shape_serves_any_values_and_any_layer_at_its_signature(demo):
    """A question's parts are compiled once per shape (semantic._compiled):
    the next question of that shape, with other values or on another Layer
    of the same columns, gets the same SQL with its own parameters and the
    same answer as a cold compile. Another type of value is another shape;
    a reloaded model is compiled again."""
    q = {"metrics": ["online_s", "kills"], "group_by": ["level"],
         "where": [{"dimension": "zone", "op": "in", "values": ["a", "b"]}], "since": "2026-01-01"}
    semantic._COMPILED.clear()
    cold = semantic.Layer(demo["con"]).compile(q)
    other = {**q, "where": [{"dimension": "zone", "op": "in", "values": ["Elwynn Forest", "x'; --"]}]}
    hot = semantic.Layer(demo["con"]).compile(other)
    assert hot["sql"] == cold["sql"] and hot["params"] == {"p0": "Elwynn Forest", "p1": "x'; --"}
    assert hot["columns"] == cold["columns"] and hot["definitions"] == cold["definitions"]
    for ask_ in (other, {**other, "where": [], "since": None}):
        warm = semantic.run(semantic.Layer(demo["con"]), demo["ro"], ask_)
        semantic._COMPILED.clear()
        assert semantic.run(semantic.Layer(demo["con"]), demo["ro"], ask_) == warm and warm["rows"]
    n = semantic.Layer(demo["con"]).compile({**q, "where": [{"dimension": "level", "op": ">=", "value": 20}]})
    assert n["sql"] != cold["sql"] and n["params"] == {"p0": 20}
    before = semantic.Layer(demo["con"]).models
    semantic.spec.cache_clear()  # semantic.yaml read again: a new entry, never the old parts
    try:
        assert semantic.Layer(demo["con"]).models is not before
    finally:
        semantic.spec.cache_clear()


def test_the_query_process_takes_its_parents_views(demo):
    """A Worker given the parent's views per grain (semantic.prepare's
    answer) creates them as they are: the same rows as one that compiles."""
    from wowstats.analysis import duckproc

    views = semantic.prepare(duck.mirror(demo["db"], TABLES, rows=False))
    assert views == semantic.view_sql(demo["con"])
    q = "SELECT * FROM per_level ORDER BY level"
    got = []
    for v in (None, views):
        w = duckproc.Worker(demo["db"], TABLES, views=v)
        try:
            got.append(duck.ReadOnlyDuck(demo["con"], tables={*TABLES, *views}, worker=w).run(q, 500))
        finally:
            w.close()
    assert got[0] == got[1] and got[0][1]


# ---------------------------------------------------------------- deaths and dungeon runs (wave 2)


@pytest.fixture(scope="module")
def fights():
    """The degraded-mode demo: deaths with Polestar_Recorder's likely
    killer and its level, dungeon runs with kills inside."""
    from tests import degraded as D

    db = prep.build(D.base_rows(), D.CHAR, route.reference())
    con = duck.mirror(db, TABLES)
    semantic.prepare(con)
    return {"db": db, "con": con, "layer": semantic.Layer(con), "ro": duck.ReadOnlyDuck(con)}


def test_death_level_gaps_count_the_records(fights):
    """deaths_above, level_gap_max and come_back_level against the death
    records themselves, per zone: a gap is npc_level minus the record's
    level, 3+ is above, the come-back level is the highest killer above."""
    want = {z: (n, a or 0, g, b) for z, n, a, g, b in fights["db"].execute(
        "SELECT zone, COUNT(*), SUM(npc_level - level >= 3), MAX(npc_level - level),"
        " MAX(CASE WHEN npc_level - level >= 3 THEN npc_level END) FROM records WHERE kind = 'death' GROUP BY zone")}
    got = table(ask(fights, metrics=["deaths", "deaths_above", "level_gap_max", "come_back_level"], group_by=["zone"],
                    where=[{"dimension": "kind", "op": "=", "value": "death"}]))
    assert {r["zone"]: (r["deaths"], r["deaths_above"], r["level_gap_max"], r["come_back_level"]) for r in got} == want
    assert any(a for _, a, _, _ in want.values()), "the demo has a death to a creature 3+ levels above"


def test_records_fall_in_their_dungeon_run(fights):
    """`run` on a record is the run whose span holds it (start to leaving),
    the run's own name in dungeon_runs: the kills of a run, by creature level."""
    db = fights["db"]
    want = dict(db.execute(
        "SELECT d.start_t, COUNT(*) FROM records r JOIN dungeon_runs d ON r.t BETWEEN d.start_t AND d.left_t"
        " WHERE r.kind = 'kill' GROUP BY d.start_t").fetchall())
    assert want, "the demo has kills inside a run"
    got = table(ask(fights, metrics=["kills"], group_by=["run"], where=[
        {"dimension": "kind", "op": "=", "value": "kill"}, {"dimension": "run", "op": "is not null"}]))
    assert {r["run"]: r["kills"] for r in got} == want
    runs = {r["run"] for r in table(ask(fights, metrics=["run_count"], group_by=["run"]))}
    assert set(want) <= runs
