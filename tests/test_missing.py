"""The "Missing:" lines (wowstats.missing): what a character's pages can't
show because a source is missing, and the one fix, from steps.json's list
and what arrived. Synthetic stores: a function answering its two queries.
The browser side (web/app.js markMissing) is in tests/test_missing_browser.py."""

from __future__ import annotations

import json

import pytest

from tests.mcp import fakes
from wowstats import missing
from wowstats.mcp import tools
from wowstats.mcp.store import Store

HERO, ALT = "Hero-Realm", "Alt-Realm"
EVERYTHING = ["wow-level", "wow-dungeon", "wow-recorder", "wow-bags", "wow-gear", "wow-character", "wow-prices"]


def store(feeds: dict[str, list[str]], quests: dict[str, list[str]] | None = None, setup: dict | None = None,
          account: list[str] = ()):
    """q(query) -> rows, as VictoriaLogs answers each of missing's queries."""
    def q(query: str) -> list[dict]:
        if query == missing.ARRIVED_Q:
            return ([{"char": c, "feed": f, "n": "3"} for c, fs in feeds.items() for f in fs]
                    + [{"char": c, "feed": "wow-quests", "source": s, "kind": "completed", "n": "1"}
                       for c, ss in (quests or {}).items() for s in ss]
                    + [{"feed": f, "n": "1"} for f in account]
                    + ([{"feed": missing.SETUP_FEED, "n": "1"}] if setup else []))
        if query == missing.SETUP_Q:
            return [{"_time": "2026-11-19T10:00:00Z", "addons": json.dumps(setup["addons"]),
                     "recorder_on": json.dumps(setup.get("recorder_on")), "saved_chars": "1",
                     **({"off": json.dumps(setup["off"])} if "off" in setup else {})}] if setup else []
        raise AssertionError(query)
    return q


def lines(q, char=HERO) -> dict[str, str]:
    return {m["id"]: m["line"] for m in missing.for_char(missing.snapshot(q), char)}


ALL_ADDONS = {"polestar": "0.4.1.1", "recorder": "0.4.1.1", "syndicator": "291", "auctionator": "349",
              "questie": "1", "altoholic": "1.60"}


def test_complete_data_says_nothing():
    q = store({HERO: EVERYTHING}, {HERO: ["questie"]}, {"addons": ALL_ADDONS, "recorder_on": True})
    assert lines(q) == {}


def test_each_missing_source_one_line_with_its_fix():
    # Questie and Syndicator not installed, Auctionator installed but never scanned, the Recorder off.
    rep = {"addons": {"polestar": "1", "recorder": "1", "auctionator": "349"}, "recorder_on": False}
    q = store({HERO: ["wow-level", "wow-dungeon", "wow-gear", "wow-character"]}, setup=rep)
    assert lines(q) == {
        "play": "Missing: play sessions, zones, deaths and loot. Type /psrec on once in game, then /reload.",
        "bags": "Missing: your gear and bags. Add Syndicator.",
        "quests": "Missing: quests you did before installing. Add Questie or Altoholic.",
        "prices": "Missing: auction prices. Scan the auction house with Auctionator, then log out.",
    }


def test_a_recorder_never_switched_on_gets_the_same_fix_as_off():
    # Installed by the installer, never run in game: the report says "unknown"
    # (a shipper before 0.7.0 left recorder_on out). Not "log in and wait".
    for on in ("unknown", None):
        rep = {"addons": {"polestar": "1", "recorder": "1"}, "recorder_on": on}
        q = store({HERO: ["wow-level", "wow-dungeon", "wow-gear", "wow-character"]}, setup=rep)
        assert lines(q)["play"] == "Missing: play sessions, zones, deaths and loot. Type /psrec on once in game, then /reload."


def test_no_polestar_no_level_times_or_gear_advice():
    rep = {"addons": {"recorder": "1", "syndicator": "1"}, "recorder_on": True}
    q = store({HERO: ["wow-recorder", "wow-bags", "wow-gear"]}, {HERO: ["altoholic"]}, rep, account=["wow-prices"])
    assert lines(q) == {"levels": "Missing: level times, dungeon runs and gear advice. Add Polestar."}


def test_installed_and_ticked_but_nothing_yet_says_what_brings_it():
    rep = {"addons": {**ALL_ADDONS}, "recorder_on": True, "off": {}}
    q = store({HERO: ["wow-level", "wow-recorder", "wow-bags"]}, {}, rep, account=["wow-prices"])
    assert lines(q) == {"quests": "Missing: quests you did before installing. Finish a quest with this character, "
                                  "then log out or type /reload once."}


def test_a_fresh_alt_with_polestar_ticked_plays_to_its_next_level():
    """The owner's case (2026-11-19): his main has everything; a level 1 alt
    played 28 seconds has no level split and no quest yet. Questie is
    installed but unticked on both, Altoholic ticked."""
    rep = {"addons": {**ALL_ADDONS}, "recorder_on": True, "off": {HERO: ["questie"], ALT: ["questie"]}}
    q = store({HERO: EVERYTHING, ALT: ["wow-recorder", "wow-bags", "wow-gear", "wow-character"]},
              {HERO: ["altoholic"]}, rep)
    assert lines(q, HERO) == {}
    assert lines(q, ALT) == {
        "levels": "Missing: level times, dungeon runs and gear advice. Play to your next level, then log out.",
        "quests": "Missing: quests you did before installing. Finish a quest with this character, "
                  "then log out or type /reload once.",
    }
    # Never "add" or "tick" an addon the report shows installed and loaded.
    assert not any("Add " in v or "Tick" in v for v in lines(q, ALT).values())


def test_installed_but_not_ticked_for_this_character_says_so():
    rep = {"addons": {**ALL_ADDONS}, "recorder_on": True,
           "off": {ALT: ["polestar", "questie", "altoholic", "syndicator"]}}
    q = store({HERO: EVERYTHING, ALT: ["wow-recorder", "wow-gear"]}, {HERO: ["questie"]}, rep)
    assert lines(q, HERO) == {}
    assert lines(q, ALT) == {
        "levels": "Missing: level times, dungeon runs and gear advice. Polestar is installed but not ticked for "
                  "this character: tick it in AddOns at character select, then log in.",
        "bags": "Missing: your gear and bags. Syndicator is installed but not ticked for this character: "
                "tick it in AddOns at character select, then log in.",
        "quests": "Missing: quests you did before installing. Questie and Altoholic are installed but not ticked "
                  "for this character: tick one in AddOns at character select, then log in.",
    }
    # One of the two ticked is enough: then it's the wait, not the tick.
    rep["off"][ALT] = ["questie"]
    assert lines(store({ALT: ["wow-level", "wow-recorder", "wow-bags"]}, {}, rep, account=["wow-prices"]), ALT) == {
        "quests": "Missing: quests you did before installing. Finish a quest with this character, "
                  "then log out or type /reload once."}


def test_not_installed_keeps_the_install_advice():
    rep = {"addons": {"polestar": "1", "recorder": "1"}, "recorder_on": True, "off": {}}
    q = store({HERO: ["wow-level", "wow-recorder"]}, {}, rep)
    got = lines(q)
    assert got["bags"] == "Missing: your gear and bags. Add Syndicator."
    assert got["quests"] == "Missing: quests you did before installing. Add Questie or Altoholic."


def test_prices_are_the_accounts_not_each_characters():
    rep = {"addons": ALL_ADDONS, "recorder_on": True}
    q = store({HERO: EVERYTHING, ALT: ["wow-level", "wow-recorder", "wow-bags"]},
              {HERO: ["questie"], ALT: ["questie"]}, rep)
    assert lines(q, ALT) == {}


def test_an_older_shipper_without_a_setup_report():
    """No report: a gap alone can't tell "not installed" from "not yet", so
    the wait first, then what to check; never a bare "Add X"."""
    q = store({HERO: ["wow-level", "wow-dungeon"]})
    got = lines(q)
    assert got["quests"] == ("Missing: quests you did before installing. Finish a quest with this character, then "
                             "log out or type /reload once. Still nothing after that? Check that Questie or Altoholic "
                             "is installed and ticked in AddOns at character select.")
    assert got["play"].endswith("Check that Polestar Recorder is installed and ticked in AddOns at character select.")
    assert not any(" Add " in v for v in got.values())


def test_nothing_yet():
    q = store({ALT: EVERYTHING})
    assert lines(q, HERO) == {"nothing": "Nothing arrived yet. Play a bit, then log out."}
    # An account without a character (a test account, a friend before the
    # first log out): one line, no addon named, whatever the report says.
    rep = {"addons": {}, "recorder_on": None, "off": {}}
    for q in (store({}), store({}, setup=rep)):
        assert [m["line"] for m in missing.for_account(missing.snapshot(q))["missing"]] == [
            "Nothing arrived yet. Play a bit, then log out."]


def test_spellings_are_one_character():
    q = store({"Hero Realm": EVERYTHING}, {"Hero Realm": ["questie"]}, {"addons": ALL_ADDONS})
    assert lines(q, "Hero-Realm") == {}


def test_the_lines_carry_their_sections_and_addon_links():
    q = store({HERO: ["wow-level", "wow-recorder", "wow-bags"]}, setup={"addons": {"polestar": "1"}},
              account=["wow-prices"])
    m = {x["id"]: x for x in missing.for_char(missing.snapshot(q), HERO)}["quests"]
    assert m["sections"] == ["dungeons", "moves"] and m["partial"] is True
    assert [a["name"] for a in m["addons"]] == ["Questie", "Altoholic"]
    assert all(a["url"].startswith("https://www.curseforge.com/") for a in m["addons"])


def test_the_start_page_gets_the_setup_and_each_gap_once():
    rep = {"addons": {"polestar": "0.4.1.1", "recorder": "0.4.1.1"}, "recorder_on": True}
    q = store({HERO: ["wow-level", "wow-recorder"], ALT: ["wow-level", "wow-recorder", "wow-bags"]}, setup=rep)
    out = missing.for_account(missing.snapshot(q))
    assert out["setup"] == {"at": "2026-11-19T10:00:00Z", "addons": rep["addons"], "recorder_on": True,
                            "saved_chars": 1, "off": None, "shipper": None}
    by = {m["id"]: m for m in out["missing"]}
    assert by["bags"]["chars"] == [HERO] and by["quests"]["chars"] == [ALT, HERO]
    # The account page names whose gap it is, except the account's own.
    assert by["bags"]["line"] == f"Missing for {HERO}: your gear and bags. Add Syndicator."
    assert by["quests"]["who"] == f"{ALT} and {HERO}"
    assert by["prices"]["line"] == "Missing: auction prices. Add Auctionator." and by["prices"]["who"] == ""


def test_the_start_page_splits_a_kind_by_fix():
    rep = {"addons": {**ALL_ADDONS}, "recorder_on": True, "off": {ALT: ["syndicator"]}}
    q = store({HERO: ["wow-level", "wow-recorder"], ALT: ["wow-level", "wow-recorder"]},
              {HERO: ["questie"], ALT: ["questie"]}, rep, account=["wow-prices"])
    got = sorted(m["line"] for m in missing.for_account(missing.snapshot(q))["missing"])
    assert got == [
        f"Missing for {ALT}: your gear and bags. Syndicator is installed but not ticked for this character: "
        "tick it in AddOns at character select, then log in.",
        f"Missing for {HERO}: your gear and bags. Log in with this character, then log out or type /reload once.",
    ]


# ---------------------------------------------------------------- the MCP's `missing`


@pytest.fixture
def ctx():
    gap = [("feed:=\"wow-setup\"", [{"_time": "2026-11-19T10:00:00Z", "addons": '{"polestar":"1","recorder":"1"}',
                                     "recorder_on": "true", "saved_chars": "1"}]),
           ("stats by (char, feed, source, kind) count() as n", [{"char": fakes.CHAR, "feed": f, "n": "5"}
                                                   for f in ("wow-level", "wow-recorder", "wow-dungeon")])]
    s = fakes.FakeStores(logs=gap + fakes.LOGS)
    tools._MISSING.clear()
    yield tools.Ctx(store=Store(vl_url=s.url, logs_filter='{component="wow"}'))
    s.close()


def test_mcp_answers_say_what_they_cant_see(ctx):
    out = tools.call(ctx, "levels", {"char": fakes.CHAR})
    assert [m["id"] for m in out["missing"]] == ["bags", "quests", "prices"]
    assert out["missing"][0] == {"id": "bags", "what": "your gear and bags", "fix": "Add Syndicator.",
                                 "line": "Missing: your gear and bags. Add Syndicator."}
    # An account-wide tool: the account's gaps, each with the characters it concerns.
    out = tools.call(ctx, "prices", {})
    assert {m["id"] for m in out["missing"]} >= {"prices"} and all("chars" in m for m in out["missing"])


def test_mcp_answers_stand_when_the_store_cant_say(ctx):
    ctx.store.vl_url = "http://127.0.0.1:9"  # down: no list, the answer's own error as before
    with pytest.raises(tools.ToolError):
        tools.call(ctx, "levels", {"char": fakes.CHAR})
