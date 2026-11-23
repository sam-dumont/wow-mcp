"""One analytical copy per character, whoever asks (wowstats.analysis.copies).

On 0.8.0 each reader built its own DuckDB copy of the same character: the
MCP's tools, the pages' narrow prep, the planner and every gear answer. On
the demo each build left the process about 350 MB bigger, and three
characters read by the page and the MCP took `ui` to 2.4 GB against the
compose kit's 1 GiB. These checks count copies (DuckDB connections made
from a prep), never resident memory: the same answer on every machine.
"""

from __future__ import annotations

import threading
import time
import warnings

import pytest

from tests.test_layer_pages import CHAR, SeedVL, seed_rows
from wowstats import eventcache, plan, stores, ui
from wowstats.analysis import budget, copies, duck, pages
from wowstats.mcp import tools as T
from wowstats.mcp.store import Store as MStore

warnings.filterwarnings("ignore", module="boring_semantic_layer")


@pytest.fixture
def seeded(monkeypatch, tmp_path):
    rows = seed_rows(tmp_path)
    fake = SeedVL(rows)
    monkeypatch.setattr(stores, "http_get", fake)
    monkeypatch.setattr(MStore, "_get", lambda self, url, params, extra=None: fake(url, params))
    monkeypatch.setattr(eventcache, "_SHARED", {})
    monkeypatch.setattr(pages, "_memo", type(pages._memo)())
    monkeypatch.setattr(plan, "_CACHE", {})
    for k, v in {"ANALYSIS_STATE_DIR": str(tmp_path / "state"), "KB_GAME_TABLES": "off"}.items():
        monkeypatch.setenv(k, v)
    copies.clear()
    made = []
    real = duck.mirror

    def counted(db, tables=None, rows=True):
        # A character's prep, rows and all (TABLES). Not semantic.read's
        # one-relation copies of a store model, made and closed per answer.
        con = real(db, tables, rows)
        if rows and tables is not None:
            made.append(con)
        return con

    monkeypatch.setattr(duck, "mirror", counted)
    yield {"rows": rows, "made": made, "fake": fake}
    copies.clear()


def app(home) -> ui.App:
    return ui.App({"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": "http://vl.invalid",
                   "MODEL3D": "off", "WOW_ITEMS": "off", "DEFAULT_CHAR": CHAR, "HOME": str(home / "full")})


def mstore():
    """The stores as the MCP and the pages hand them to copies.of."""
    from wowstats.plan.facts import store_for

    return store_for({}, MStore(vl_url="http://vl.invalid"))


def ctx() -> T.Ctx:
    return T.Ctx(store=MStore(vl_url="http://vl.invalid"), default_char=CHAR)


def test_one_copy_serves_the_tools_the_pages_the_plan_and_the_gear(seeded, tmp_path):
    """Every reader of the layer, one after the other, on one character:
    one DuckDB copy with rows is made in the whole process, and it stays."""
    a = app(tmp_path)
    c = ctx()
    for name, args in (("character_summary", {}), ("levels", {}), ("deaths", {}), ("dungeons", {}),
                       ("sessions", {}), ("gear", {}), ("plan_session", {"hours": 2}), ("next_step", {})):
        T.call(c, name, {**args, "char": CHAR})
    a.api_char(CHAR)
    a.overview(CHAR)
    plan.inputs(a.env, CHAR, a.store)
    assert len(seeded["made"]) == 1, f"{len(seeded['made'])} copies built for one character"
    live = copies.live()
    assert len(live) == 1 and live[0].key[-1] == CHAR and copies.STATS["builds"] >= 1


def test_the_gear_advice_is_scored_in_the_copy_and_taken_back_out(seeded):
    cp = copies.of(mstore(), CHAR)
    before = cp.con.execute("SELECT COUNT(*) FROM upgrade_candidates").fetchone()[0]
    advice = {"class": "PRIEST", "slots": [{"slot": 1, "slot_name": "Head",
                                            "current": {"item_id": 4321, "name": "Test cap", "quality": 2}}],
              "upgrades_all": []}
    with cp.scored(advice, CHAR, worn="advice"):
        assert cp.con.execute("SELECT item_id FROM gear").fetchall() == [(4321,)]
    assert cp.con.execute("SELECT COUNT(*) FROM upgrade_candidates").fetchone()[0] == before
    assert (4321,) not in cp.con.execute("SELECT item_id FROM gear").fetchall()
    assert len(seeded["made"]) == 1


def test_new_events_swap_the_copy_and_close_the_old_one(seeded, monkeypatch):
    st = mstore()
    old = copies.of(st, CHAR)
    assert copies.of(st, CHAR) is old  # same events: the same copy
    late = {**seeded["rows"][-1], "source_id": "late:1", "kind": "zone"}
    seeded["fake"].rows.append(late)
    for cache in eventcache._SHARED.values():
        cache.expire()
    new = copies.of(st, CHAR)
    assert new is not old and old.closed and not new.closed
    assert [cp.key for cp in copies.live()] == [new.key]


def test_past_the_cap_the_least_recently_used_closes_unless_a_request_holds_it(seeded, monkeypatch):
    rows = seeded["rows"]
    others = [{**r, "char": "Copyone-Gearholt"} for r in rows]
    seeded["fake"].rows.extend(others)
    monkeypatch.setenv("ANALYSIS_COPIES_MAX", "1")
    st = mstore()
    first = copies.of(st, CHAR)
    second = copies.of(st, "Copyone-Gearholt")
    assert first.closed and not second.closed and len(copies.live()) == 1
    with pytest.raises(copies.Closed):
        first.run({"metrics": ["deaths"]})
    # Held by a request: evicted from the map, open until the request ends.
    with copies.holding():
        mine = copies.of(st, CHAR)
        copies.of(st, "Copyone-Gearholt")
        assert not mine.closed and len(copies.live()) == 2
        mine.run({"metrics": ["deaths"]})
    assert mine.closed and len(copies.live()) == 1


def test_one_build_at_a_time(seeded, monkeypatch):
    """Two characters asked at once: their builds never overlap (a build's
    transient is the biggest thing the process allocates)."""
    seeded["fake"].rows.extend({**r, "char": "Copyone-Gearholt"} for r in seeded["rows"])
    inside, most = [0], [0]
    from wowstats.analysis import prep

    real = prep.build

    def slow(*a, **k):
        inside[0] += 1
        most[0] = max(most[0], inside[0])
        time.sleep(0.2)
        try:
            return real(*a, **k)
        finally:
            inside[0] -= 1

    monkeypatch.setattr(prep, "build", slow)
    st = mstore()
    ts = [threading.Thread(target=copies.of, args=(st, c)) for c in (CHAR, "Copyone-Gearholt")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert most[0] == 1 and len(copies.live()) == 2


# ------------------------------------------------------------- the budget


def test_the_cgroup_limit(tmp_path):
    f = tmp_path / "memory.max"
    f.write_text("max\n")
    assert budget.cgroup_limit([str(f)]) is None
    f.write_text(str(1 << 30))
    assert budget.cgroup_limit([str(tmp_path / "missing"), str(f)]) == 1 << 30
    f.write_text("9223372036854771712\n")  # cgroup v1's "no limit"
    assert budget.cgroup_limit([str(f)]) is None
    assert budget.cgroup_limit([str(tmp_path / "missing")]) is None


@pytest.mark.parametrize("gib", [1, 1.5, 2, 4, 8])
def test_the_split_fits_the_limit(gib):
    """What the budget allows, counted at its worst (every copy at its
    memory_limit's quarter, every workspace with a query at its cap), stays
    within the limit, from the 1 GiB floor (docs/install.md, Memory) up."""
    limit = int(gib * (1 << 30))
    b = budget.split(limit)
    worst = (budget.PROCESS + b["copies"] * (b["copy_limit"] // 4 + budget.COPY_OVERHEAD)
             + b["workspaces"] * (budget.WORKSPACE_BASE + b["query"]))
    assert worst <= limit and b["copies"] >= budget.COPIES_MIN and b["workspaces"] >= 1


def test_no_limit_keeps_the_old_defaults():
    b = budget.split(None)
    assert (b["copies"], b["workspaces"], b["query"], b["copy_limit"]) == (4, 6, 512 << 20, 256 << 20)
