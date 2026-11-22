"""Two answers the recovery drill (2026-11-22) tripped on, on a real history
restored twice into empty servers: `players` gave another top 20 on every
call over the same events, and `zones` failed outright on a character
whose XP rows in one zone carried no `xp_gained`. A stub store answers
the tools' queries; the rows are made up."""

from __future__ import annotations

from wowstats import stores
from wowstats.mcp import tools


class Rows:
    """A store that answers each query from the first (needle, rows) pair it contains."""

    def __init__(self, *pairs):
        self.pairs = pairs

    def logs(self, query, start=None, end=None, limit=None):
        return next((list(rows) for needle, rows in self.pairs if needle in query), [])


def ctx(store):
    return tools.Ctx(store=store)


def test_players_breaks_ties_the_same_way_whatever_order_the_store_answers():
    # Five players met once, on the same day, in the same context: a tie on
    # days, grouped and sightings. VictoriaLogs' stats rows come in no set
    # order, so the top `limit` used to change from one call to the next.
    met = [{"player": f"P{i}-Realm", "context": "seen", "_time": "2026-11-20T00:00:00Z", "n": "1"} for i in range(5)]
    info = [{"player": f"P{i}-Realm", "last_seen": f"2026-11-20T1{i}:00:00Z"} for i in range(5)]
    answers = []
    for order in (met, met[::-1], met[2:] + met[:2]):
        store = Rows(("count_uniq(source_id) as n", order), ("max(_time) as last_seen", info))
        answers.append(tools.t_players(ctx(store), {"char": "Me-Realm", "limit": 3})["players"])
    assert answers[0] == answers[1] == answers[2]
    # Among equals, the one seen last comes first.
    assert [p["player"] for p in answers[0]] == ["P4-Realm", "P3-Realm", "P2-Realm"]


def test_zones_reads_a_sum_over_no_values_as_nothing():
    # VictoriaLogs answers sum() over rows without the field as "NaN".
    act = [{"zone": "Durotar", "kind": "xp", "n": "3", "xp": "NaN"},
           {"zone": "Durotar", "kind": "kill", "n": "4", "xp": "NaN"}]
    out = tools.t_zones(ctx(Rows(("kind:in(xp, kill, turnin, death)", act))), {"char": "Me-Realm"})
    (z,) = out["zones"]
    assert (z["zone"], z["xp"], z["kills"], z["xp_per_hour"]) == ("Durotar", 0, 4, None)


def test_num_and_opt_num_never_hand_back_a_nan():
    for mod in (stores, tools):
        assert mod.num("NaN") == 0.0
        assert mod.num("Inf", default=1.0) == 1.0
        assert mod.opt_num("NaN") is None
        assert mod.opt_num("-Inf") is None
        assert mod.num("2.5") == 2.5
