"""One BiS share per worn slot, the paper doll's (owner, 2026-11-21): every
item line that says "59% → 100% on your wrist" starts from the number the
doll's badge shows for that slot, on the Overview, its dungeon moves and the
Dungeons panel alike.

rc.6 on a live character said "12% → 18% on your chest" in a dungeon card
while the doll said 39%: the planner (wowstats.plan.facts.gather) scored the
gear at the level of the newest Recorder event that has a `level` field,
and a kill's `level` is the creature's (a guard seen at 55 put the advice at
level 55, with BiS from far above the character). It also scored without
Battle.net's talents and worn stats and without the auction house, which the
doll has. The demo character here, with Battle.net's talents and a last
kill of a level-55 creature, on the VictoriaLogs and vm-wow stand-ins of
tests/degraded.py."""

from __future__ import annotations

import time

import pytest

from tests import degraded as D
from wowstats import gear as gearmod
from wowstats import plan as planmod
from wowstats import ui
from wowstats.stores import parse_t


def _talents() -> dict:
    who = {"character": D.CHAR, "account_id": "0"}

    def v(m: dict, x: float) -> dict:
        return {"metric": m, "value": [time.time(), str(x)]}

    trees = [("Discipline", 16), ("Shadow Magic", 7), ("Holy", 0)]
    return {"wow_character_talent_points": [v({**who, "tree": t, "order": str(i)}, p)
                                            for i, (t, p) in enumerate(trees)],
            "wow_character_talent_rank": [v({**who, "spell_id": "15338", "talent": "Spirit Tap"}, 5)],
            "wow_character_profile_refreshed_timestamp_seconds": [v(who, time.time())]}


def _with_a_high_kill(rows: list[dict]) -> list[dict]:
    """The demo, plus one last kill of a level-55 creature (a city guard)."""
    last = max(rows, key=lambda r: parse_t(r["_time"]))
    t = parse_t(last["_time"]).timestamp() + 60
    kill = {"_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)), "char": D.CHAR, "component": "wow",
            "feed": "wow-recorder", "kind": "kill", "layer": "app", "npc_id": "68", "level": "55",
            "classification": "elite", "source_id": "kill-guard"}
    return [*rows, kill]


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("WOW_ITEMS", "off")
    vl = D.VL(_with_a_high_kill(D.rows_for("full")), metrics=_talents())
    tmp = tmp_path_factory.mktemp("bis")
    env = {"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": vl.url, "VM_URL": vl.url,
           "MODEL3D": "off", "DEFAULT_CHAR": D.CHAR, "HOME": str(tmp), "ANALYSIS_STATE_DIR": str(tmp),
           "WOW_ITEMS": "off"}
    planmod._CACHE.clear()
    yield ui.App(env)
    vl.close()
    planmod._CACHE.clear()
    mp.undo()


def _item_lines(x, out: list) -> list[dict]:
    """Every row of an answer that carries a BiS badge."""
    if isinstance(x, dict):
        if x.get("bis_pct") is not None and x.get("slot_name"):
            out.append(x)
        for v in x.values():
            _item_lines(v, out)
    elif isinstance(x, list):
        for v in x:
            _item_lines(v, out)
    return out


def _doll(app) -> dict[str, set]:
    """The doll's badge per slot name: {slot name: {pct, ...}} (two rings,
    two trinkets; both hands are one setup)."""
    g = app.gear(D.CHAR, None)
    out: dict[str, set] = {}
    for s in g["slots"]:
        st = s.get("standing")
        out.setdefault(s["slot_name"], set()).add(None if s.get("current") is None else (st or {}).get("pct"))
    return out


def test_every_item_line_starts_from_the_dolls_badge(app):
    doll = _doll(app)
    rows = _item_lines(app.dungeons(D.CHAR), []) + _item_lines(app.overview(D.CHAR), [])
    assert len(rows) > 20, "the demo's dungeons and moves should carry badged items"
    bad = []
    for r in rows:
        name = "Main hand" if r["slot_name"] in ("Main hand", "Off hand") else r["slot_name"]
        want = doll.get(name, set())
        if r.get("bis_pct_worn") not in want:
            bad.append((r.get("name"), r["slot_name"], r.get("bis_pct_worn"), sorted(want, key=str)))
    assert not bad, f"item lines whose 'from' isn't the doll's badge: {bad[:8]}"


def test_newest_sheet_takes_the_players_level_only():
    """A kill's `level` is the creature's, an accept's or turn-in's the
    quest's: only a ding, a session record or Polestar's sheet say the
    character's own (wowstats.stores.PLAYER_LEVEL_KINDS)."""
    rows = [
        {"_time": "2026-11-20T10:00:00Z", "feed": "wow-gear", "char": D.CHAR, "class": "PRIEST",
         "items": "[]"},
        {"_time": "2026-11-20T10:00:00Z", "feed": "wow-character", "char": D.CHAR, "level": 33},
        {"_time": "2026-11-20T10:05:00Z", "feed": "wow-recorder", "char": D.CHAR, "kind": "session", "level": 33},
        {"_time": "2026-11-20T10:10:00Z", "feed": "wow-recorder", "char": D.CHAR, "kind": "accept", "level": 38},
        {"_time": "2026-11-20T10:20:00Z", "feed": "wow-recorder", "char": D.CHAR, "kind": "kill", "level": 55},
        {"_time": "2026-11-20T10:25:00Z", "feed": "wow-recorder", "char": D.CHAR, "kind": "unit", "level": 60},
    ]
    assert gearmod.newest_sheet(rows)[1] == 33
    ding = {"_time": "2026-11-20T10:30:00Z", "feed": "wow-recorder", "char": D.CHAR, "kind": "level", "level": 34}
    assert gearmod.newest_sheet([*rows, ding])[1] == 34


@pytest.fixture(scope="module")
def app_ah(tmp_path_factory):
    """The demo with a fresh auction house scan listing Highlander's Cloth
    Girdle (20099), on sale only: a better belt than any the character can
    get otherwise, so the doll's waist reads a lower share of BiS."""
    from wowstats import ahsource

    mp = pytest.MonkeyPatch()
    mp.setenv("WOW_ITEMS", "off")
    real = ahsource.market

    def market(res, **kw):
        m = real(res, **kw)
        on = {"price_copper": 5000, "seen_day": time.strftime("%Y-%m-%d"), "seen_days_ago": 0, "listed": 1}
        return {**m, "scan": m.get("scan") or {"day": on["seen_day"], "days_ago": 0},
                "items": {**(m.get("items") or {}), 20099: on}}

    mp.setattr(ahsource, "market", market)
    vl = D.VL(D.rows_for("full"), metrics=_talents())
    tmp = tmp_path_factory.mktemp("bis-ah")
    env = {"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": vl.url, "VM_URL": vl.url,
           "MODEL3D": "off", "DEFAULT_CHAR": D.CHAR, "HOME": str(tmp), "ANALYSIS_STATE_DIR": str(tmp),
           "WOW_ITEMS": "off"}
    planmod._CACHE.clear()
    yield ui.App(env)
    vl.close()
    planmod._CACHE.clear()
    mp.undo()


def test_an_auction_house_bis_moves_every_line_with_the_doll(app_ah):
    """The doll counts what is on sale (wowstats.ahsource); the Dungeons
    panel once scored from the stored events alone, so its waist read 60%
    where the doll said 57%. Now both come from Views.gear_inputs."""
    doll = _doll(app_ah)
    waist = [r for r in _item_lines(app_ah.dungeons(D.CHAR), []) if r["slot_name"] == "Waist"]
    assert waist and doll["Waist"] and None not in doll["Waist"]
    assert {r["bis_pct_worn"] for r in waist} == doll["Waist"]
