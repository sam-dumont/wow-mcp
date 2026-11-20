"""The semantic layer (wowstats.analysis.semantic) and its DuckDB engine
(wowstats.analysis.duck), on the demo seed (dev/seed.py: made-up characters).

The reference answer: Testchar-Demo's slowest level is 30, 11,240 s played
(Polestar's split), 2,650 s of it in Scarlet Monastery: Library."""

from __future__ import annotations

import json
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

from wowstats.analysis import TABLES, drive, duck, prep, semantic
from wowstats.analysis.sql import QueryError
from wowstats.mcp import route, tools

ROOT = Path(__file__).resolve().parents[2]
CHAR = "Testchar-Demo"
warnings.filterwarnings("ignore", module="boring_semantic_layer")


@pytest.fixture(scope="module")
def seed(tmp_path_factory):
    out = tmp_path_factory.mktemp("seed") / "seed.ndjson"
    subprocess.run([sys.executable, str(ROOT / "dev" / "seed.py"), "--out", str(out)], check=True,
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
    assert rows[30]["played_s"] == 11240
    assert max(rows, key=lambda lv: rows[lv]["played_s"] or 0) == 30
    lib = table(ask(demo, metrics=["run_s"], group_by=["dungeon"],
                    where=[{"dimension": "dungeon", "op": "=", "value": "Scarlet Monastery: Library"}]))
    assert lib == [{"dungeon": "Scarlet Monastery: Library", "run_s": 2650}]


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
              where=[{"dimension": "zone", "op": "in", "values": [evil, "Stranglethorn Vale"]},
                     {"dimension": "level", "op": ">=", "value": 30}])
    assert evil not in got["sql"] and "Stranglethorn" not in got["sql"] and "$p0" in got["sql"]
    assert got["params"] == {"p0": evil, "p1": "Stranglethorn Vale", "p2": 30}
    assert [r["zone"] for r in table(got)] == ["Stranglethorn Vale"]
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
    cols, rows, cut = demo["play"].run("SELECT level, played_s FROM levels WHERE level = 30")
    assert (cols, rows, cut) == (["level", "played_s"], [(30, 11240.0)], False)
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
        feed = next((f for f in ("wow-level", "wow-dungeon") if f'feed:="{f}"' in query), None)
        char = query.split('char:="')[1].split('"')[0] if 'char:="' in query else CHAR
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
                                                                          "value": 30}]})
    head, row = out["result"].splitlines()[:2]
    assert head == "level\txp_per_hour\tlevel_xp\tplayed_s"
    level, xph, xp, played = row.split("\t")
    assert (level, xp, played) == ("30", "55000", "11240")
    assert float(xph) == pytest.approx(55000 / (11240 / 3600), rel=1e-3)
    assert out["params"] == {"p0": 30} and out["definitions"]["xp_per_hour"]["kind"] == "ratio"
    cat = call(lab[0], "list_metrics", {})
    assert {"played_s", "online_s", "active_s", "calendar_s"} <= {m["name"] for m in cat["metrics"]}


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
    assert next(lv for lv in levels if lv["level"] == 30)["seconds"] == 11240
    runs = call(lab[0], "dungeons", {"char": CHAR})["runs"]
    by_name = {r[0]: r for r in json_rows(call(lab[0], "query_metric", {
        "char": CHAR, "metrics": ["run_s", "run_xp", "run_xp_per_hour"], "group_by": ["dungeon"]}))}
    assert len(runs) == len(by_name) == 4
    for r in runs:
        got = by_name[r["name"]]
        assert (round(got[1]), got[2]) == (r["seconds"], r["xp"]), r["name"]
        assert round(got[3]) == r["xp_per_hour"], r["name"]
    assert by_name["Scarlet Monastery: Library"][1] == 2650


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
    assert "11240" in mine["result"]
    for name, args in (("query_metric", {"char": CHAR, "metrics": ["played_s"], "group_by": ["level"]}),
                       ("play_sql", {"char": CHAR, "query": "SELECT level, played_s FROM levels"}),
                       ("propose_finding", {"char": CHAR, "finding": {
                           "key": "x", "title": "{played_s}", "claim": "{played_s} s", "expect": {"played_s": 11240},
                           "metric_query": {"metrics": ["played_s"], "where": [
                               {"dimension": "level", "op": "=", "value": 30}]}}})):
        out = call(lab[1], name, args)
        body = json.dumps(out)
        assert "11240" not in body and "Library" not in body, (name, body[:300])
        assert out.get("answer", "x") is None or "error" in out or out.get("stored") is False, (name, out)


def test_propose_finding_takes_a_query_metric_call(lab):
    mq = {"metrics": ["played_s", "dungeon_s"], "where": [{"dimension": "level", "op": "=", "value": 30}]}
    good = {"key": "level-30-dungeon", "title": "Level 30: {dungeon_s} s in dungeons",
            "claim": "Level 30 took {played_s} s, {dungeon_s} s of it inside a dungeon.",
            "expect": {"played_s": 11240}, "relevance": 2, "action": None, "metric_query": mq}
    ok = call(lab[0], "propose_finding", {"char": CHAR, "finding": good})
    assert ok["stored"], ok
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
    assert got == [11240] * 16
    assert other.execute("SELECT played_s FROM levels WHERE level = 30").fetchone()[0] == 11240


# ---------------------------------------------------------------- the four former gaps


def test_quests_pair_accept_and_hand_in(demo):
    """The demo picks each quest up when the one two before it is handed in:
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


def test_unexplained_gold_per_level(demo):
    """The purse's moves between snapshots, less the quest money the records
    saw: on the demo, the rest is the flights (80-260 copper each)."""
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
