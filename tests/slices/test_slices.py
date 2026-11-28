"""Integration slices: a few golden characters (tests/slices/characters.py)
sent the way a player's shipper sends them (POST /ingest), stored by a
VictoriaLogs stand-in and, again, by the SQLite event store, then read by the page's HTTP API and by the MCP's
tools, each through its own path. The assertions are contracts between the
surfaces, never one module's internals:

- one number means one thing everywhere: the level, the time in the level,
  the time played, the pace, the ETA, every dungeon trip line (the
  Dungeons panel, the Overview's move, the MCP's dungeon options) and each
  slot's best upgrade (the gear page, the MCP's gear);
- coverage agrees with what arrived: no source reads `unavailable` while
  one of its parts holds data, and no `missing` line or fix tells the player
  to add an addon whose own feed is there;
- every number in a move, an upgrade line or a next-moves point says where
  it comes from;
- the plan, the moves, the raid prep and the class effects keep every key
  of the committed snapshot (tests/slices/golden.py): a knowledge-base or
  data change that drops one fails here, as #189 dropped every level 60's
  Onyxia attunement and only the release's browser suite saw it;
- `not_possible_yet` never names something a registered MCP tool computes.

Fast and deterministic: no browser, one fixed clock (the demo's), one server
per character. The whole file runs in well under a minute.
"""

from __future__ import annotations

import json
import re

import pytest

from tests import degraded as D
from wowstats import missing as M
from wowstats.mcp import tools as T

from . import characters as C
from . import golden
from . import harness as H

pytestmark = [pytest.mark.slice, pytest.mark.usefixtures("demo_clock")]


@pytest.fixture(scope="module", params=[(g, b) for b in D.BACKENDS for g in C.GOLDEN],
                ids=lambda p: f"{p[0]}-{p[1]}")
def served(request, tmp_path_factory, monkeypatch_module):
    monkeypatch_module.setenv("WOW_ITEMS", "off")
    name, backend = request.param
    char, build = C.GOLDEN[name]
    rows = build()
    s = H.serve(char, rows, tmp_path_factory.mktemp(name), backend)
    s.name, s.rows = name, rows
    yield s
    s.close()


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


# ---------------------------------------------------------------- one number, one meaning


def test_the_hero_numbers_are_the_same_on_the_page_and_in_the_mcp(served):
    hd = served.page("char")["header"]
    cs = served.mcp("character_summary")
    page = {"level": hd["level"], "time_in_level": T.dur(hd.get("time_in_level_s")),
            "played": T.dur(hd.get("played_s")), "xp_per_hour": hd.get("xp_per_hour"),
            "eta": T.dur(hd.get("eta_s"))}
    mcp = {"level": cs["level"], "time_in_level": cs.get("time_in_level"), "played": cs.get("played_tracked"),
           "xp_per_hour": cs.get("xp_per_hour_last_3_levels"), "eta": cs.get("eta_next_level")}
    assert page == mcp, served.name


def test_the_level_rows_are_the_same_on_the_page_and_in_the_mcp(served):
    page = [(lv["level"], lv["seconds"], lv["xp"], bool(lv["partial"])) for lv in served.page("char")["levels"]]
    mcp = [(lv["level"], lv["seconds"], lv["xp"], bool(lv["partial"])) for lv in served.mcp("levels")["levels"]]
    assert page == mcp, served.name


TRIP = ("basis", "run_s", "xp", "xp_kills", "xp_quests", "xp_per_hour", "level_pct", "vs_questing", "questing_s")


def trip(g: dict | None) -> tuple | None:
    if not g:
        return None
    return (*(g.get(k) for k in TRIP), tuple(sorted(q["quest_id"] for q in g.get("quests") or [])))


def test_a_dungeon_trip_reads_the_same_in_the_panel_the_overview_and_the_mcp(served):
    panel = served.page("dungeons")
    if not panel.get("available"):
        pytest.skip(f"{served.name}: no Dungeons panel ({panel.get('why')})")
    lines = {d["name"]: trip(d.get("gain")) for d in panel["dungeons"] if d.get("gain")}
    options = served.mcp("dungeons", options=True)["dungeons"]
    opts = {d["name"]: trip(d.get("gain")) for d in options if d.get("gain")}
    worthwhile = [d["name"] for d in panel["dungeons"] if d["fits_now"] and d["value"] == "high"]
    assert [d["name"] for d in options if d["fits_now"] and d["priority"] == "high"] == worthwhile
    moves = {m["zone"]["name"]: trip(m.get("gain")) for m in served.page("overview").get("moves") or []
             if m.get("kind") == "dungeon"}
    assert list(moves) == worthwhile[:2]
    points = [m for m in served.mcp("next_moves")["answer"] if m["page_section"] == "#dungeons"]
    assert len(points) == len(moves)
    for point, name in zip(points, moves, strict=True):
        assert point["point"].startswith(f"If you like dungeons: {name}.")
    for name, line in opts.items():
        assert lines.get(name) == line, f"{served.name}: {name}: panel {lines.get(name)} vs MCP {line}"
    for name, line in moves.items():
        assert lines.get(name) == line, f"{served.name}: {name}: panel {lines.get(name)} vs Overview {line}"


def test_each_slots_best_upgrade_is_the_same_on_the_gear_page_and_in_the_mcp(served):
    page = served.page("gear")
    if not page.get("available"):
        pytest.skip(f"{served.name}: no gear advice")
    mine = {s["slot_name"]: (s.get("best") or {}).get("item_id") for s in page.get("slots") or []}
    theirs = {s["slot"]: (s.get("best_now") or {}).get("item_id") for s in served.mcp("gear").get("slots") or []}
    assert {k: v for k, v in mine.items() if k in theirs} == theirs, served.name
    # The Overview's upgrade moves are the gear page's upgrades, never others.
    ups = {u["item_id"] for u in page.get("upgrades") or []} | {v for v in mine.values() if v}
    for m in served.page("overview").get("moves") or []:
        if m.get("kind") == "upgrade":
            assert m["item_id"] in ups, f"{served.name}: move {m['title']} isn't one of the gear page's upgrades"


@pytest.mark.parametrize("mode", ["lvl", "pvp", "pve"])
def test_each_mode_reads_the_same_on_the_gear_page_and_in_the_mcp(served, mode):
    """Leveling, PvP and PvE: the same mode, the same weights' credit and
    each slot's same best item and worn standing on both surfaces."""
    page = served.page("gear", mode=mode)
    if not page.get("available"):
        pytest.skip(f"{served.name}: no gear advice")
    mcp = served.mcp("gear", mode=mode)
    assert page["mode"]["key"] == mode and mcp["mode"] == page["mode"]["label"], served.name
    assert page["source"]["url"] == mcp["source"]["url"] == "https://foreverdb.net"
    mine = {s["slot_name"]: ((s.get("best") or {}).get("item_id"), (s.get("standing") or {}).get("label"))
            for s in page.get("slots") or []}
    theirs = {s["slot"]: ((s.get("best_now") or {}).get("item_id"), s.get("worn_standing"))
              for s in mcp.get("slots") or []}
    assert {k: v for k, v in mine.items() if k in theirs} == theirs, (served.name, mode)


def test_the_mode_picked_on_the_page_is_kept_for_the_mcp(served):
    """The gear page's mode picker writes the mode on the server
    (wowstats.gearmode): the page and the MCP's `gear` read it without a
    `mode`, the MCP's `mode` overrides it for one call, Leveling clears it."""
    import urllib.error
    import urllib.request

    def pick(mode: str) -> dict:
        req = urllib.request.Request(served.base + "/api/gear-mode", method="POST",
                                     data=json.dumps({"name": served.char, "mode": mode}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())

    if not served.page("gear").get("available"):
        pytest.skip(f"{served.name}: no gear advice")
    assert served.page("gear")["mode"]["key"] == "lvl"
    # JSON only: a cross-site form can't send it without a preflight.
    form = urllib.request.Request(served.base + "/api/gear-mode", method="POST", data=b"name=x&mode=pvp",
                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(form, timeout=60)
    assert e.value.code == 415
    try:
        assert pick("pvp")["mode"]["key"] == "pvp"
        assert served.page("gear")["mode"]["key"] == "pvp"
        served.calls.clear()
        mcp = served.mcp("gear")
        assert mcp["mode"] == "PvP" and mcp["mode_from"] == "the player's pick on the gear page"
        asked = served.mcp("gear", mode="lvl")
        assert asked["mode"] == "Leveling" and asked["mode_from"] == "asked"
    finally:
        pick("lvl")
        served.calls.clear()
    assert served.page("gear")["mode"]["key"] == "lvl" and served.mcp("gear")["mode_from"] == "the default"


def test_the_pvp_toggle_reaches_the_page_and_the_mcp(served):
    """The gear page's PvP rewards toggle (wowstats.gearmode), off by
    default: kept on the server, read by the page and the MCP's `gear`, which
    `pvp` overrides for one call; the PvP mode counts them whatever it says."""
    import urllib.request

    def toggle(on) -> dict:
        req = urllib.request.Request(served.base + "/api/gear-mode", method="POST",
                                     data=json.dumps({"name": served.char, "pvp": on}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())

    if not served.page("gear").get("available"):
        pytest.skip(f"{served.name}: no gear advice")
    assert served.page("gear")["pvp_rewards"] == {"on": False, "forced": False}
    assert served.mcp("gear")["pvp_rewards"] == {"counted": False, "from": "the default (off)"}
    try:
        assert toggle(True) == {"ok": True, "pvp": True}
        assert served.page("gear")["pvp_rewards"]["on"] is True
        served.calls.clear()
        assert served.mcp("gear")["pvp_rewards"] == {"counted": True, "from": "the player's toggle on the gear page"}
        assert served.mcp("gear", pvp=False)["pvp_rewards"] == {"counted": False, "from": "asked"}
    finally:
        toggle(False)
        served.calls.clear()
    assert served.mcp("gear", mode="pvp")["pvp_rewards"] == {"counted": True, "from": "the PvP mode"}
    assert served.page("gear")["pvp_rewards"]["on"] is False


# ---------------------------------------------------------------- coverage and missing


def coverages(served) -> dict[str, dict]:
    out = {"page": served.page("char")["coverage"]}
    for tool, args in (("character_summary", {}), ("gear", {}), ("dungeons", {}), ("next_moves", {})):
        cov = served.mcp(tool, **args).get("coverage")
        if cov:
            out[tool] = cov
    return out


def test_no_source_is_unavailable_while_one_of_its_parts_holds_data(served):
    for where, cov in coverages(served).items():
        for src, e in cov.items():
            if e.get("parts"):
                assert e["state"] != "unavailable", f"{served.name}: {where}: {src} {e}"


def present_addons(rows: list[dict]) -> set[str]:
    """The addons whose own data arrived (the setup report aside)."""
    have = set()
    for r in rows:
        f = r.get("feed")
        if f == "wow-recorder":
            have.add("recorder")
        elif f in ("wow-level", "wow-character", "wow-dungeon"):
            have.add("polestar")
        elif f == "wow-bags" or f == "wow-gear" and r.get("items") not in (None, "", "[]", []):
            have.add("syndicator")
        elif f == "wow-prices":
            have.add("auctionator")
        elif f == "wow-quests" and r.get("source"):
            have.add(str(r["source"]))
    return have


def fixes(served) -> list[str]:
    out = [m["line"] for m in served.page("char").get("missing") or []]
    for where, cov in coverages(served).items():
        out += [f"{where} {src}: {e['fix']}" for src, e in cov.items() if e.get("fix")]
    for tool in ("character_summary", "gear", "dungeons"):
        out += [f"{tool}: {json.dumps(m)}" for m in served.mcp(tool).get("missing") or []]
    return out


def test_nothing_says_add_an_addon_whose_data_is_already_there(served):
    names = {a: v["name"] for a, v in M.addons().items()}
    have = present_addons(served.rows)
    for line in fixes(served):
        for said in re.findall(r"\bAdd ([^.]+)\.", line):
            named = {n.strip() for n in re.split(r",| or | and ", said)}
            for a in have:
                assert names.get(a, a) not in named, f"{served.name}: {line!r} while {a}'s data arrived"


# ---------------------------------------------------------------- prose


def test_every_number_in_a_move_or_point_says_where_it_comes_from(served):
    ov = served.page("overview")
    for m in (ov.get("moves") or []) + (ov.get("upgrades") or []):
        if re.search(r"\d", m.get("text") or ""):
            assert (m.get("cite") or "").strip(), f"{served.name}: no source for {m['text']!r}"
    for p in served.mcp("next_moves").get("answer") or []:
        if re.search(r"\d", p.get("point") or ""):
            assert (p.get("from") or "").strip(), f"{served.name}: no source for {p['point']!r}"


# ---------------------------------------------------------------- behaviour snapshot


def test_no_move_prep_or_effect_of_the_snapshot_went_away(served):
    want = golden.stored().get(served.name)
    assert want is not None, f"{served.name} has no snapshot: uv run python -m tests.slices.golden --write"
    gone, _new = golden.diff({served.name: want}, {served.name: golden.keys(served)})
    assert not gone, "went away (update tests/slices/golden.json in the same change if on purpose):\n" + \
        "\n".join(gone)


def test_the_snapshot_holds_raid_prep_for_the_level_60():
    # What #189 took away: the Onyxia attunement for every level 60.
    prep = golden.stored()["gorvak"]["prep"]
    assert "raids-open-2026-12|quest Dragonkin Menace" in prep, prep


# ---------------------------------------------------------------- what the tools can't do yet


# Words a not_possible_yet line would use for something a tool now computes.
CAPABILITIES = {"auction": "prices", "price": "prices", "talent": "talent_changes", "dungeon": "dungeons",
                "gear": "gear", "upgrade": "gear", "death": "deaths", "session": "sessions", "news": "news",
                "mount": "mount_goal", "riding": "mount_goal", "route": "route_reference"}


def test_not_possible_yet_never_names_what_a_tool_computes(served):
    registered = {t.name for t in T.TOOLS}
    for tool in ("next_moves", "sanity_check"):
        for line in served.mcp(tool).get("not_possible_yet") or []:
            hits = [f"{w} -> {t}" for w, t in CAPABILITIES.items() if t in registered and w in line.lower()]
            # The one standing gap names the combat log, which no tool reads: say so explicitly.
            if "combat log" in line.lower():
                continue
            assert not hits, f"{served.name}: {tool} says it can't {line!r}, but {hits}"


def test_the_golden_characters_are_what_they_say():
    # A guard on the fixtures themselves: each one has the shape it is named for.
    feeds = {n: {r["feed"] for r in b()} for n, (_c, b) in C.GOLDEN.items()}
    assert {"wow-gear", "wow-bags", "wow-recorder"} <= feeds["nibbet"]
    assert "wow-bags" not in feeds["nibbet-gear-no-bags"] and "wow-gear" in feeds["nibbet-gear-no-bags"]
    assert "wow-level" not in feeds["tilly"] and "wow-recorder" in feeds["tilly"]
    assert not {"wow-recorder", "wow-gear", "wow-bags"} & feeds["nibbet-bare"]
    assert C.GOLDEN["nibbet"][0] == D.CHAR
