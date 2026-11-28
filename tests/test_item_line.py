"""One item line everywhere (owner, 2026-11-21): every place an item shows
says the worn slot's doll % and the % with this item, "70% → 91% on your
chest"; the doll, under the slot, "70% → 91% with the best at hand".

The 0.7.0 tour showed three forms: "17% → 82% on your head" on the moves,
"▲ 17% → 82% at hand" on the doll, "HEAD 17% → 100%" on the Upgrades list
and "(0% → 100%)" in Back in time. One helper a side now: web/app.js upText
(its doll form included) and wowstats/gear.py gain_words, which the MCP's
item rows carry as `line`. This file checks nobody builds one by hand, the
pair rule behind the doll's "at hand" for rings and trinkets, and the MCP's
lines on the demo. The page's words are run under node in
test_bis_words_node, the pages in test_item_line_browser."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

from tests import degraded as D
from tests.test_gear import WORN, sheet
from wowstats import gear, ui
from wowstats import plan as planmod
from wowstats.mcp import tools as T
from wowstats.mcp.store import Store as MStore

ROOT = Path(__file__).resolve().parent.parent
LINE = re.compile(r"^(empty|\d+%) → \d+% on your [a-z ]+$|^\+\d+\.\d points$")


# --- nobody builds a line by hand ----------------------------------------------

def _code(text: str, comment: str) -> list[tuple[int, str]]:
    return [(n, ln) for n, ln in enumerate(text.splitlines(), 1) if not ln.lstrip().startswith(comment)]


def _helper(src: str, name: str) -> range:
    m = re.search(rf"^function {name}\(.*?^}}\n", src, re.S | re.M)
    assert m, f"app.js lost {name}"
    first = src[:m.start()].count("\n") + 1
    return range(first, first + m.group(0).count("\n"))


def test_the_page_builds_item_lines_in_one_place():
    src = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    inside = set(_helper(src, "upShort")) | set(_helper(src, "upText"))
    # upShort is upText's half: no place shows it bare (the Upgrades list's
    # "HEAD 17% → 100%", Back in time's "(0% → 100%)").
    calls = [n for n, ln in _code(src, "//") if "upShort(" in ln and not ln.lstrip().startswith("function ")]
    assert calls and all(n in inside for n in calls), f"upShort called outside upText at lines {calls}"
    # No other "→" next to a "%" in the page code (talent ranks say "3→5", no "%").
    for f in sorted((ROOT / "web").glob("*.js")):
        text = f.read_text(encoding="utf-8")
        own = inside if f.name == "app.js" else set()
        bad = [(n, ln.strip()) for n, ln in _code(text, "//")
               if re.search(r"→|\\u2192|&rarr;", ln) and "%" in ln and n not in own]
        assert not bad, f"{f.name}: an item line built by hand: {bad}"


def test_the_doll_says_it_with_the_helper():
    src = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    iup = [ln for ln in src.splitlines() if "class: 'iup'" in ln]
    assert iup and all("upText(u, 'doll')" in ln for ln in iup), iup
    # rc.7's "▲ 17% → 82% at hand" and "▲ something to wear" are gone.
    code = "\n".join(ln for _n, ln in _code(src, "//"))
    assert "} at hand`" not in code and "something to wear" not in code


def test_the_server_builds_item_lines_in_one_place():
    found = []
    for f in sorted((ROOT / "wowstats").rglob("*.py")):
        for n, ln in _code(f.read_text(encoding="utf-8"), "#"):
            if re.search(r"f[\"'].*→", ln) and "%" in ln:
                found.append((str(f.relative_to(ROOT)), n))
    assert [p for p, _ in found] == ["wowstats/gear.py"], found


# --- the doll's "at hand" for a pair (#69's rule) ------------------------------

@pytest.fixture(scope="module")
def ref():
    r = gear.reference()
    assert r.available, r.why
    return r


def _pairs(a: dict) -> dict:
    by = {s["slot"]: s for s in a["slots"]}
    return {name: (by[x], by[y]) for name, (x, y) in (("finger", (11, 12)), ("trinket", (13, 14)))}


def _weak_first(x: dict, y: dict) -> tuple[dict, dict]:
    """An empty slot first, else the fewer points (gear.compare)."""
    if x["current"] is None:
        return x, y
    if y["current"] is None:
        return y, x
    return (y, x) if y["current"]["score"] < x["current"]["score"] else (x, y)


@pytest.mark.parametrize("level", [20, 32, 45])
def test_pair_at_hand_weaker_takes_the_best_stronger_the_next(ref, level):
    a = gear.advise(ref, sheet(), level=level)
    for name, (x, y) in _pairs(a).items():
        weak, strong = _weak_first(x, y)
        worn = {s["current"]["item_id"] for s in (x, y) if s["current"]}
        bw, bs = weak["best"], strong["best"]
        for b in (bw, bs):
            # Never the one the other slot already wears (compare() calls it
            # equipped; the doll offered the worn trinket again at level 20).
            assert b is None or b["item_id"] not in worn, (name, level, b)
        if bw and bs:
            assert bw["item_id"] != bs["item_id"], (name, level)
            # Below 60 the effort weighs (gear.effort_penalty): a harder ring
            # scoring a bit more goes to the stronger slot.
            def worth(b):
                return b["score"] * (1 - gear.effort_penalty(gear.EFFORT.index(b["effort"]), level))
            assert (worth(bw) >= worth(bs) or bw["fit"]["factor"] > bs["fit"]["factor"]), (name, level)
        if bs and not bw:
            raise AssertionError(f"{name} at {level}: the stronger slot has a pick, the weaker none")


def only_rings(ref, sources: dict, rings: set[str]) -> dict:
    """The gear data with these rings alone: the others leave the sources and
    the items too (an item with no source still counts in the BiS since
    2026-11-24, gear.unsourced), the worn ones stay."""
    worn = {str(i) for i in WORN.values()}
    items = {k: v for k, v in ref.data["items"].items() if v.get("inv") != 11 or k in rings | worn}
    return {**ref.data, "sources": sources, "items": items}


def test_pair_one_good_ring_reaches_100_once(ref):
    """One ring better than both worn: the weaker slot reads 100% with it,
    the stronger has nothing at hand (it is BiS beside that ring)."""
    src = ref.data["sources"]
    keep = {k: v for k, v in src.items() if (ref.item(int(k)) or {}).get("inv") != 11 or k == "281635"}
    one = dataclasses.replace(ref, data=only_rings(ref, keep, {"281635"}), peers=None, nowhere=None)
    a = gear.advise(one, sheet(), level=32)
    weak, strong = _weak_first(*_pairs(a)["finger"])
    assert weak["best"]["item_id"] == 281635
    assert (weak["best"]["bis_pct_worn"], weak["best"]["bis_pct"]) == (13, 100)
    assert strong["best"] is None and strong["standing"]["rank"] == 1


def test_pair_two_good_rings_reach_100_each(ref):
    """Two different rings at hand, both above the worn: the best two are the
    pair's BiS, so the weaker reads 100% with the best and the stronger 100%
    with the next."""
    src = ref.data["sources"]
    keep = {k: v for k, v in src.items()
            if (ref.item(int(k)) or {}).get("inv") != 11 or k in ("281635", "2933")}
    two = dataclasses.replace(ref, data=only_rings(ref, keep, {"281635", "2933"}), peers=None, nowhere=None)
    a = gear.advise(two, sheet(), level=32)
    weak, strong = _weak_first(*_pairs(a)["finger"])
    assert weak["best"]["item_id"] == 281635 and strong["best"]["item_id"] == 2933
    assert weak["best"]["bis_pct"] == 100 and strong["best"]["bis_pct"] == 100
    assert weak["best"]["bis_pct_worn"] == weak["standing"]["pct"]
    assert strong["best"]["bis_pct_worn"] == strong["standing"]["pct"]


# --- the MCP: every item row says the page's line -------------------------------


@pytest.fixture(scope="module")
def ctx(tmp_path_factory, demo_clock):
    mp = pytest.MonkeyPatch()
    mp.setenv("WOW_ITEMS", "off")
    vl = D.VL(D.rows_for("full"))
    tmp = tmp_path_factory.mktemp("line")
    env = {"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": vl.url, "VM_URL": vl.url,
           "MODEL3D": "off", "DEFAULT_CHAR": D.CHAR, "HOME": str(tmp), "ANALYSIS_STATE_DIR": str(tmp),
           "WOW_ITEMS": "off"}
    planmod._CACHE.clear()
    yield T.Ctx(store=MStore(vl_url=vl.url, vm_url=vl.url), default_char=D.CHAR, env=env), ui.App(env)
    vl.close()
    planmod._CACHE.clear()
    mp.undo()


def _rows(x, out: list) -> list[dict]:
    if isinstance(x, dict):
        if "bis_pct" in x and "bis_pct_worn" in x:
            out.append(x)
        for v in x.values():
            _rows(v, out)
    elif isinstance(x, list):
        for v in x:
            _rows(v, out)
    return out


@pytest.mark.parametrize("tool, more", [("gear", {}), ("gear", {"options": True}), ("dungeons", {"options": True})])
def test_mcp_item_rows_carry_the_line(ctx, tool, more):
    c, _app = ctx
    rows = _rows(T.call(c, tool, {"char": D.CHAR, **more}), [])
    assert rows, f"{tool}: no item rows on the demo"
    for r in rows:
        assert LINE.match(r.get("line") or ""), (tool, r.get("item") or r.get("name"), r.get("line"))


def test_mcp_lines_are_the_pages(ctx):
    """The MCP's `gear` line for an item is gain_words of the advice row the
    page draws, so the model and the page say the same thing."""
    c, app = ctx
    page = {u["item_id"]: gear.gain_words(u) for u in app.gear(D.CHAR, None)["upgrades"]}
    out = T.call(c, "gear", {"char": D.CHAR, "limit": 40})
    # The upgrades: the doll's per-slot picks (slots[].best_now) measure a
    # pair's stronger slot against the next item, the lists against the weaker.
    rows = [r for r in _rows(out["next_upgrades"], []) if r.get("item_id") in page]
    assert rows
    for r in rows:
        assert r["line"] == page[r["item_id"]], (r["item"], r["line"], page[r["item_id"]])


def test_nibbet_gear_options_keep_both_bad_mojo_mask_bosses(ctx):
    c, app = ctx
    page = next(u for u in app.gear(D.CHAR, None)["upgrades"] if u["item_id"] == 9470)
    out = T.call(c, "gear", {"char": D.CHAR, "options": True, "limit": 20})
    mask = next(o for slot in out["slots"] for o in slot["options"] if o["item_id"] == 9470 and o["kind"] == "dungeon")
    assert mask["source"] == "Nekrum Gutchewer in Zul'Farrak" and mask["chance"] == 25 and mask["cost_s"] > 0
    bosses = {r["source"]: r["chance"] for r in [mask, *mask["alternatives"]]}
    assert bosses == {f"{s['boss']} in {s['zone']}": s["chance_pct"] for s in page["sources"] if s["kind"] == "drop"}
    assert bosses["Shadowpriest Sezz'ziz in Zul'Farrak"] is None


def test_nibbet_dungeon_handins_and_unfinished_armory_agree_across_tools(ctx):
    from wowstats import redact

    c, app = ctx
    options = T.call(c, "dungeons", {"char": D.CHAR, "options": True})["dungeons"]
    blocks = app.dungeons(D.CHAR)["dungeons"]
    overview = app.overview(D.CHAR)["next_moves"]["moves"]
    moves = T.call(c, "next_moves", {"char": D.CHAR})["answer"]
    shown = 0
    for name, count, pre, pre_xp, xp in (("Zul'Farrak", 12, 5, 11200, 91031),
                                         ("Razorfen Downs", 14, 9, 24020, 47160)):
        b = next(b for b in blocks if b["name"] == name)
        option = next(d for d in options if d["name"] == name)
        # Only the dungeons worth running now have a move: with the pace of his own
        # levels as the yardstick Zul'Farrak (from level 47) has none at his level.
        move = next((m for m in moves if m["point"].startswith(f"If you like dungeons: {name}")), None)
        card = next((m for m in overview if m.get("kind") == "dungeon" and m["title"] == name), None)
        assert (move is None) == (card is None)
        handins = b["gain"]["handins"]
        saved = redact.zogzog("dungeons", {"dungeons": [b]})["dungeons"][0]["gain"]["handins"]
        assert saved == handins == option["gain"]["handins"]
        if card:
            assert handins == card["gain"]["handins"] == move["handins"]
            assert b["gain"]["xp_quests"] == move["xp_quests"]
        assert len(handins) == len({q["quest_id"] for q in handins}) == count
        assert sum(q["prerequisite"] for q in handins) == pre
        assert sum(q["xp"] for q in handins) == b["gain"]["xp_quests"] == xp
        assert all(q["name"] and q["url"] for q in handins)
        assert b["trip"]["quest_xp_left"] == xp
        why = f"{count} hand-ins ({xp:,} XP), including {pre} prerequisites worth {pre_xp:,} XP"
        if card:
            assert why in b["why"] == option["why"] and why in card["detail"] and why in move["point"]
        shown += bool(card)
    assert shown, "no pinned dungeon has a move"
    for rows in (blocks, options):
        armory = next(b for b in rows if b["name"] == "Scarlet Monastery: Armory")
        assert (armory["verdict"], armory["why"]) == (
            "part of a multi-wing quest", "finish Down the Scarlet Path first, then kill Herod for "
            "In the Name of the Light. The hand-in also needs Library and Cathedral")
        assert armory["gain"]["handins"] == armory["quest_rewards"] == []
        assert armory["gain"]["upgrades"] == armory["gain"]["drop_upgrades"] == armory["gain"]["xp_quests"] == 0
        assert armory["deferred_quest_rewards"][0]["name"] == "Orb of Lorica"


def test_one_current_zone_and_one_line_across_the_gear_tools(ctx):
    """eval-mcp 2026-11-22 (zone_quest_upgrade): gear_options had no current
    zone and no zone mark, so the model read the zone off sessions and
    picked one of three Arathi quest upgrades. Now character_summary, gear
    and gear_options say the same zone, both lists mark what is in it by the
    same rule, and an item in both says the same line."""
    c, _app = ctx
    summary = T.call(c, "character_summary", {"char": D.CHAR})
    g = T.call(c, "gear", {"char": D.CHAR, "limit": 40})
    o = T.call(c, "gear", {"char": D.CHAR, "options": True, "limit": 20})
    zone = summary["zone"]
    assert zone and g["zone"] == zone and o["zone"] == zone
    opts = [x for s in o["slots"] for x in s["options"]]
    assert all(isinstance(x["in_current_zone"], bool) for x in opts)
    quests = [x for x in opts if x["kind"] == "quest"]
    assert any(x["in_current_zone"] for x in quests) and not all(x["in_current_zone"] for x in quests)
    for x in quests:
        assert x["in_current_zone"] == (x["zone"] == zone), (x["item"], x["zone"])
    lines = {x["item_id"]: x for x in opts}
    both = [u for u in g["next_upgrades"] if u["item_id"] in lines]
    assert both
    for u in both:
        assert u["line"] == lines[u["item_id"]]["line"], (u["item"], u["line"], lines[u["item_id"]]["line"])
        if lines[u["item_id"]]["in_current_zone"]:
            assert u["in_current_zone"], u["item"]


def test_plan_facts_name_items_by_their_line(ctx):
    c, _app = ctx
    planmod._CACHE.clear()
    out = T.call(c, "plan_session", {"char": D.CHAR, "hours": 2})
    facts = out.get("facts") or {}
    items = {k: v for k, v in facts.items() if re.match(r"^(gear\d+|.+\.item\d+)$", k)}
    assert items, sorted(facts)[:40]
    for k, v in items.items():
        assert re.search(r"\((empty|\d+%) → \d+% on your [a-z ]+[,)]|\(\+\d+\.\d points[,)]", v), (k, v)
        assert not re.search(r", \+\d+(\.\d)?[,)]", v), (k, v)
