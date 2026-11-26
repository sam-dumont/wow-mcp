"""The install's shared auction scans (wowstats.ahshared), on the SQLite
store (one file per person, the shared space its own file): a scan serves
every character of its ruleset and faction, whoever scanned it, and carries
nothing about who that was. Made-up characters and prices only."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from wowstats import ahshared, ahsource, eventstore
from wowstats.stores import Store

DAY = (datetime.now(UTC) - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
T = DAY.isoformat().replace("+00:00", "Z")
SCAN_DAY = DAY.strftime("%Y-%m-%d")
# Who scans: Ada (Alliance, Normal), Bram (Horde, Normal), Cleo (Alliance, Hardcore).
ADA, BRAM, CLEO, DORA, EZRA = "Ada-Normal", "Bram-Normal", "Cleo-Hardcore", "Dora-Normal", "Ezra-Normal"


def character(char, ruleset, faction=None, race=None) -> dict:
    return {"_time": T, "feed": "wow-character", "char": char, "realm": ruleset, "faction": faction, "race": race,
            "source_id": f"character:{char}:1"}


def price(char, ruleset, item, copper, faction=None) -> dict:
    """A wow-prices event as the shipper sends it; `faction` only when
    Auctionator's key names the house."""
    return {"_time": T, "feed": "wow-prices", "char": char, "source_id": f"price:{ruleset}:{item}:1", "rev": 1,
            "_msg": f"item {item}: lowest {copper} copper", "item_id": item, "item_key": str(item),
            "item_name": f"Item {item}", "price_copper": copper, "available": 4, "scan_day": SCAN_DAY,
            "realm": ruleset, "faction": faction}


def lines(*evs) -> list[bytes]:
    return [json.dumps({k: v for k, v in e.items() if v is not None}).encode() for e in evs]


@pytest.fixture
def lab(tmp_path):
    """Five people on one SQLite folder (tenants 0-4) and the shared space."""
    loc = f"sqlite:{tmp_path}"
    people = {n: Store(loc, account_id=i) for i, n in enumerate((ADA, BRAM, CLEO, DORA, EZRA))}

    def upload(who, *evs):
        ls = lines(*evs)
        eventstore.get(loc).append(people[who].account_id, ls)
        return ahshared.share(loc, ls)

    return loc, people, upload


def shared_rows(loc) -> list[dict]:
    p = eventstore.get(loc).path(ahshared.ACCOUNT)
    with sqlite3.connect(p) as con:
        return [json.loads(r) for (r,) in con.execute("SELECT row FROM events")]


def prices_of(loc, own) -> dict[str, str]:
    rows = ahshared.scans(own, loc, "min(price_copper) as lo, max(_time) as t", {"lo": "min", "t": "maxtime"})
    return {r["item_key"]: (r["ruleset"], r.get("faction"), r["lo"]) for r in rows}


def test_a_scan_serves_its_ruleset_and_faction_and_says_nothing_of_who(lab):
    loc, people, upload = lab
    assert upload(ADA, character(ADA, "Normal", "Alliance"), price(ADA, "Normal", 101, 500, "Alliance")) == 1
    assert upload(BRAM, character(BRAM, "Normal", "Horde"), price(BRAM, "Normal", 202, 700, faction="Horde")) == 1
    assert upload(CLEO, character(CLEO, "Hardcore", "Alliance"), price(CLEO, "Hardcore", 303, 900, "Alliance")) == 1
    upload(DORA, character(DORA, "Normal", "Alliance"))  # never scans
    upload(EZRA, character(EZRA, "Normal", race="Troll"))  # Horde by race, never scans

    stored = shared_rows(loc)
    assert {r["faction"] for r in stored} == {"Alliance", "Horde"}
    for r in stored:  # price fields, the house, nothing about the scanner
        assert set(r) <= {"_time", "feed", "component", "ruleset", "faction", "house_from", "_msg", *ahshared.SHARED_FIELDS}
        assert not any(n.split("-")[0] in json.dumps(r) for n in (ADA, BRAM, CLEO))

    # Dora (Alliance, Normal) gets Ada's scan: not Bram's Horde house, not Cleo's other ruleset.
    assert prices_of(loc, people[DORA]) == {"101": ("Normal", "Alliance", "500")}
    # Ezra (Horde by race, Normal) gets Bram's only.
    assert prices_of(loc, people[EZRA]) == {"202": ("Normal", "Horde", "700")}
    # Cleo sees her ruleset's only.
    assert prices_of(loc, people[CLEO]) == {"303": ("Hardcore", "Alliance", "900")}


def test_a_character_record_names_the_realm_the_long_way_and_still_gets_its_ruleset(lab):
    """Forever's client realm name is "<cluster> Realm <n> <ruleset>"; Auctionator's key is the
    ruleset alone (wowstats.ahsource.ruleset_key). Dora never scans and gets Ada's scan."""
    loc, people, upload = lab
    upload(ADA, character(ADA, "Fixture Realm 1 Normal", "Alliance"), price(ADA, "Normal", 505, 400, "Alliance"))
    upload(DORA, character(DORA, "Fixture Realm 1 Normal", "Alliance"))
    upload(CLEO, character(CLEO, "Fixture Realm 1 Hardcore", "Alliance"))
    assert prices_of(loc, people[DORA]) == {"505": ("Normal", "Alliance", "400")}
    assert prices_of(loc, people[CLEO]) == {}


def test_the_neutral_house_serves_both_sides_of_its_ruleset(lab):
    loc, people, upload = lab
    upload(ADA, character(ADA, "Normal", "Alliance"), price(ADA, "Normal", 404, 300, faction="Neutral"))
    upload(EZRA, character(EZRA, "Normal", "Horde"))
    upload(CLEO, character(CLEO, "Hardcore", "Alliance"))
    assert prices_of(loc, people[EZRA]) == {"404": ("Normal", "Neutral", "300")}
    assert prices_of(loc, people[CLEO]) == {}


@pytest.mark.parametrize("shared", [False, True])
def test_market_trends_and_planned_loot_stay_in_one_house(lab, shared):
    from types import SimpleNamespace

    from wowstats.mcp import tools
    from wowstats.plan import facts

    loc, people, upload = lab
    own = people[ADA]
    upload(ADA, character(ADA, "Normal", "Alliance"), character(CLEO, "Hardcore", "Horde"),
           price(ADA, "Normal", 505, 10000, "Alliance"), price(CLEO, "Hardcore", 505, 99000, "Horde"))
    loc = loc if shared else ""
    ctx = tools.Ctx(store=SimpleNamespace(events=own), default_char=ADA, env={"AH_SHARED_STORE": loc})

    def trend(**kwargs):
        return tools.t_prices(ctx, {"item": "505", **kwargs})

    answer = trend()
    assert answer["house"] == {"ruleset": "Normal", "faction": "Alliance"}
    assert [(r["latest_copper"], r["scan_days"], r["change_vs_first"]) for r in answer["items"]] == [(10000, 1, None)]
    assert trend(ruleset="Hardcore", faction="Horde")["items"][0]["latest_copper"] == 99000
    assert trend(ruleset="Normal", faction="Neutral")["items"] == []
    scans = facts.prices(own, shared=loc, char=ADA)
    assert facts.run_loot({"loot_inside": [(505, 2)]}, None, scans)["ah_copper"] == 20000
    assert facts.prices(own, shared=loc, char=CLEO)[505]["copper"] == 99000
    assert facts.prices(own, shared=loc, char="Unknown-Fixture") == {}


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("alias", [False, True], ids=["same-spelling", "spaced-and-dashed"])
@pytest.mark.parametrize("side_fields", [{"race": "Gnome"}, {"faction": "Alliance"}], ids=["race", "faction"])
@pytest.mark.parametrize("scan_faction", [None, "Alliance"], ids=["ruleset-key", "faction-key"])
def test_scanning_another_house_cannot_change_the_characters_side(lab, shared, alias, side_fields, scan_faction):
    from types import SimpleNamespace

    from wowstats.mcp import tools
    from wowstats.plan import facts

    loc, people, upload = lab
    name = ADA.replace("-", " ") if alias else ADA
    upload(ADA, character(ADA, "Fixture Realm 1 Normal"),
           {"_time": (DAY + timedelta(minutes=30)).isoformat(), "char": ADA, "feed": "wow-player", "race": "Orc"},
           {"_time": T, "char": name, "feed": "wow-recorder", "kind": "session", **side_fields},
           price(ADA, "Hardcore", 505, 99000, "Horde") | {"_time": (DAY + timedelta(hours=1)).isoformat()})
    if shared:
        scanner = DORA.replace("-", " ") if alias else DORA
        upload(DORA, {"_time": T, "char": DORA, "feed": "wow-player", "race": "Orc"},
               character(scanner, "Normal", **side_fields), price(DORA, "Normal", 505, 10000, scan_faction))
        assert ahshared.homes(people[ADA]) == {("Normal", "Alliance"), ("Hardcore", "Horde")}
    else:
        upload(ADA, price(ADA, "Normal", 505, 10000, scan_faction))
    own = people[ADA]
    loc = loc if shared else ""
    ctx = tools.Ctx(store=SimpleNamespace(events=own), default_char=ADA, env={"AH_SHARED_STORE": loc})
    result = tools.t_prices(ctx, {"item": "505", "char": name})
    if shared and not scan_faction:
        assert result["house"] is None and result["items"] == []
        assert facts.prices(own, shared=loc, char=name) == {}
    else:
        assert result["house"] == {"ruleset": "Normal", "faction": scan_faction}
        assert result["items"][0]["latest_copper"] == facts.prices(own, shared=loc, char=name)[505]["copper"] == 10000
    hardcore = tools.t_prices(ctx, {"item": "505", "ruleset": "Hardcore", "faction": "Horde"})
    assert hardcore["items"][0]["latest_copper"] == 99000


def test_neutral_market_is_a_fallback_and_never_merged_with_a_side():
    rows = [{"ruleset": "Normal", "faction": s} for s in ("Alliance", "Neutral")]
    assert ahshared.one_house(rows, ("Normal", "Alliance"))[1] == rows[:1]
    assert ahshared.one_house(rows, ("Normal", "Horde"))[1] == rows[1:]
    assert ahshared.one_house(rows, None)[1] == []


@pytest.mark.parametrize("shared", [False, True])
def test_an_unknown_side_still_has_a_known_ruleset(lab, shared):
    from wowstats.plan import facts

    loc, people, upload = lab
    own = people[ADA]
    loc = loc if shared else ""
    upload(ADA, character(ADA, "Fixture Realm 1 Normal", race="Skyborne"),
           price(ADA, "Hardcore", 505, 99000))
    assert facts.prices(own, shared=loc, char=ADA) == {}
    scanner = DORA if shared else ADA
    upload(scanner, price(scanner, "Normal", 505, 3000, faction="Neutral"))
    assert facts.prices(own, shared=loc, char=ADA)[505]["copper"] == 3000

    legacy = [{"ruleset": "Normal"}]
    assert ahshared.one_house(legacy, ("Normal", None))[1] == legacy
    assert ahshared.one_house(legacy, None)[1] == legacy


def test_realm_only_prices_stay_private_even_with_a_known_race(lab):
    loc, people, upload = lab
    upload(BRAM, character(BRAM, "Normal", race="Orc"))
    assert upload(BRAM, price(BRAM, "Normal", 505, 50)) == 0
    upload(EZRA, character(EZRA, "Normal", "Horde"))
    assert prices_of(loc, people[BRAM]) == {"505": ("Normal", None, "50")}
    assert prices_of(loc, people[EZRA]) == {}


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("label", ["Horde", None])
def test_only_one_of_the_selected_characters_own_keys_sets_the_default(lab, shared, label):
    from types import SimpleNamespace

    from wowstats import analysis
    from wowstats.mcp import tools
    from wowstats.plan import facts

    loc, people, upload = lab
    own = people[BRAM]
    upload(BRAM, price(BRAM.replace("-", " "), "Normal", 505, 55, label),
           price(CLEO, "Hardcore", 505, 777, "Alliance"))
    loc = loc if shared else ""
    env = {"AH_SHARED_STORE": loc}
    ctx = tools.Ctx(store=SimpleNamespace(events=own), default_char=BRAM, env=env)
    trend = tools.t_prices(ctx, {"item": "505"})
    assert trend["house"] == {"ruleset": "Normal", "faction": label} and trend["items"][0]["latest_copper"] == 55
    assert facts.prices(own, shared=loc, char=BRAM)[505]["copper"] == 55
    rows = analysis.prices_for(own, env, BRAM)
    assert ahsource.market({"items": rows}, faction=None, money=None)["items"][505]["price_copper"] == 55
    assert facts.prices(own, shared=loc, char="No-Data") == {}

    upload(BRAM, price(BRAM, "Normal", 505, 75, "Neutral"))
    assert tools.t_prices(ctx, {"item": "505"})["items"] == []
    assert analysis.prices_for(own, env, BRAM) == [] and facts.prices(own, shared=loc, char=BRAM) == {}
    def explicit(faction):
        return tools.t_prices(ctx, {"item": "505", "ruleset": "Normal", "faction": faction})
    assert explicit("Neutral")["items"][0]["latest_copper"] == 75
    selected = explicit(label or "Unknown")
    assert selected["items"][0]["latest_copper"] == 55 and selected["house"]["faction"] == label


def test_backfill_copies_older_scans_once(lab):
    loc, people, _ = lab
    # Scans stored before the shared space existed: in Ada's own store only.
    eventstore.get(loc).append(0, lines(character(ADA, "Normal", "Alliance"), price(ADA, "Normal", 707, 70, "Alliance"),
                                        {**price(ADA, "Normal", 707, 90, "Alliance"), "rev": 2, "price_copper": 65},
                                        price(ADA, "Normal", 606, 60)))
    eventstore.get(loc).append(3, lines(character(DORA, "Normal", "Alliance")))
    # Earlier shared rows discarded the scanner and whether the faction was inferred.
    legacy = ahshared.shared_event(price(ADA, "Normal", 606, 60, "Alliance"))
    legacy.pop("house_from")
    eventstore.get(loc).append(ahshared.ACCOUNT, lines(legacy))
    assert prices_of(loc, people[DORA]) == {}
    out = ahshared.backfill(loc, [("ada", people[ADA])], out=open("/dev/null", "w"))  # noqa: SIM115
    assert out["ada"] == {"scans": 2, "shared": 1}
    assert prices_of(loc, people[DORA]) == {"707": ("Normal", "Alliance", "65")}
    ahshared.backfill(loc, [("ada", people[ADA])], out=open("/dev/null", "w"))  # noqa: SIM115
    assert len(shared_rows(loc)) == 2  # the unverified row and one confirmed row
    assert "Ada" not in json.dumps(shared_rows(loc))

    # Only Alliance and Horde could be inferred. Legacy Neutral was explicit.
    eventstore.get(loc).append(ahshared.ACCOUNT, lines({**legacy, "faction": "Neutral"}))
    assert prices_of(loc, people[DORA])["606"] == ("Normal", "Neutral", "60")


def test_one_person_reads_their_own_scans_as_rulesets():
    """No shared space (one person): the own store's `realm` is the ruleset."""
    class Own:
        stream = "feed:wow-*"

        def vl_direct(self, q, keys, ops, limit, start, end):
            assert "realm" in q and "ruleset" not in q
            return [{"item_key": "1", "scan_day": SCAN_DAY, "realm": "Normal", "faction": "Horde", "lo": "5"}]

    assert ahshared.scans(Own(), "", "min(price_copper) as lo", {"lo": "min"}) == [
        {"item_key": "1", "scan_day": SCAN_DAY, "ruleset": "Normal", "faction": "Horde", "lo": "5"}]


def test_gear_keeps_to_the_characters_ruleset():
    """Two rulesets in the rows (one person with characters on both): the
    gear advice reads the character's own ruleset, the newest scanned
    elsewhere never shows."""
    now = datetime.now(UTC)
    t = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")
    d = now.strftime("%Y-%m-%d")
    rows = [{"ruleset": rs, "faction": "Alliance", "item_key": str(i), "scan_day": d, "lo": "10", "av": "1", "t": t}
            for rs, base in (("Normal", 1000), ("Hardcore", 5000)) for i in range(base, base + 60)]
    got = ahsource.market({"items": rows, "money": [{"_time": t, "level": "20", "money_copper": "5", "realm": "Normal"}]}, faction="Alliance",
                          money=None, now=now.timestamp())
    assert got["scan"]["ruleset"] == "Normal" and all(1000 <= i < 1060 for i in got["items"])


def test_prune_drops_scans_older_than_price_days_on_sqlite(lab):
    loc, _, upload = lab
    old = DAY - timedelta(days=ahshared.PRICE_DAYS + 5)
    upload(ADA, character(ADA, "Normal", "Alliance"),
           price(ADA, "Normal", 2589, 15, "Alliance") | {"_time": old.isoformat().replace("+00:00", "Z")},
           price(ADA, "Normal", 2592, 40, "Alliance"))
    assert len(shared_rows(loc)) == 2
    assert eventstore.get(loc).delete(ahshared.ACCOUNT, before=datetime.now(UTC) - timedelta(days=90)) == {
        "deleted_rows": 1}
    (left,) = shared_rows(loc)
    assert left["item_key"] == "2592"
    assert eventstore.get(loc).delete(999, before=datetime.now(UTC)) == {"deleted_rows": 0}


def test_prune_daily_runs_once_a_day_and_a_friends_deletion_leaves_it(lab, monkeypatch):
    loc, people, upload = lab
    upload(ADA, character(ADA, "Normal", "Alliance"), price(ADA, "Normal", 2592, 40, "Alliance"))
    monkeypatch.setattr(ahshared, "_last_prune", 0.0)
    assert ahshared.prune_daily(loc) is True
    assert ahshared.prune_daily(loc) is False  # the same day
    eventstore.get(loc).delete(people[ADA].account_id)  # a friend's deletion
    assert len(shared_rows(loc)) == 1


def test_prune_without_a_delete_api_logs_once_and_skips(monkeypatch):
    from wowstats.stores import SourceError

    class Refusing:
        def delete(self, *a, **k):
            raise SourceError("VictoriaLogs refused the delete: 400")

    monkeypatch.setattr(eventstore, "get", lambda loc: Refusing())
    monkeypatch.setattr(ahshared, "_warned", False)
    said = []
    for _ in range(2):
        monkeypatch.setattr(ahshared, "_last_prune", 0.0)
        assert ahshared.prune_daily("http://vl:9428", log=said.append) is False
    assert len(said) == 1 and "not pruned" in said[0]
