"""Battle.net fills what the addons didn't send, per kind (owner,
2026-11-20): the addon's data wins and the API isn't used for that kind;
missing, the API's; never an API value read 30 days ago or more; nothing
from either, the page says what's coming and when.

The demo character (tests/degraded.py) with Battle.net's answers put in its
store: equipment that differs from Syndicator's, and a profile."""

from __future__ import annotations

import json
import time

import pytest

from tests import degraded as D
from wowstats import ui
from wowstats.stores import Store

# What Battle.net says the character wears: one item, not the sheet's.
BNET_WORN = {"head": {"item_id": 10504, "stats": {}}, "main_hand": {"item_id": 1317, "stats": {}}}
BNET_PROFILE = {"sex": "female", "guild": None, "guild_known": True, "class": "MAGE", "race": "Gnome",
                "faction": "Alliance", "level": 30, "refreshed": "2026-11-20T10:00:00Z"}


@pytest.fixture(autouse=True)
def no_wago(monkeypatch):
    monkeypatch.setenv("WOW_ITEMS", "off")


def app_on(rows, monkeypatch, tmp_path, worn=BNET_WORN, profile=BNET_PROFILE, exporter=True) -> tuple[ui.App, D.VL]:
    vl = D.VL(rows)
    app = ui.App({"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": vl.url, "MODEL3D": "off",
                  "DEFAULT_CHAR": D.CHAR, "HOME": str(tmp_path), "WOW_ITEMS": "off"})
    st = app.store
    asked = []
    monkeypatch.setattr(st, "profile_vm_url", "http://vm-wow.invalid" if exporter else "")
    monkeypatch.setattr(st, "character_equipment", lambda name: asked.append("equipment") or dict(worn or {}))
    monkeypatch.setattr(st, "character_profile", lambda name: asked.append("profile") or (profile and dict(profile)))
    monkeypatch.setattr(st, "character_armory", lambda name: {})
    app.asked = asked
    return app, vl


def professions_only(rows):
    """A friend without Syndicator: the shipper still sends a gear event,
    with the professions and no item."""
    out = []
    for r in D.rows_for("no-syndicator") if rows is None else rows:
        out.append(r)
    gear = [dict(r, items="[]") for r in D.base_rows() if r["feed"] == "wow-gear"]
    return out + gear


def test_the_addons_gear_wins(monkeypatch, tmp_path):
    """Syndicator's items: Battle.net's equipment isn't used for them."""
    app, vl = app_on(D.rows_for("full"), monkeypatch, tmp_path)
    try:
        g = app.char(D.CHAR)["gear"]
        assert g["items"] and g.get("items_from") is None and not g.get("items_note")
        assert {i["item_id"] for i in g["items"]} != {10504, 1317}
        assert "equipment" not in app.asked
        advice = app.gear(D.CHAR, None)
        assert advice.get("worn_from") in (None, "sheet")
        # The class is the addon's too, though Battle.net says otherwise.
        assert app.char(D.CHAR)["header"]["class"] != "Mage"
        assert "from_battlenet" not in app.char(D.CHAR)["header"]
    finally:
        vl.close()


def test_no_gear_event_takes_battlenets(monkeypatch, tmp_path):
    app, vl = app_on(D.rows_for("no-syndicator"), monkeypatch, tmp_path)
    try:
        g = app.char(D.CHAR)["gear"]
        assert g["items_from"] == "battle.net" and {i["item_id"] for i in g["items"]} == {10504, 1317}
        assert {i["slot"] for i in g["items"]} == {1, 16}
        assert g["items_note"].startswith("Gear from Battle.net,") and "Syndicator shows it" in g["items_note"]
    finally:
        vl.close()


def test_professions_only_mixes_the_two(monkeypatch, tmp_path):
    """The partial case: the professions from the shipper, the worn items
    from Battle.net (the whole set: a sheet with items lists every slot, so
    there is no slot-by-slot mix), on the page and in the advice."""
    app, vl = app_on(professions_only(None), monkeypatch, tmp_path)
    try:
        g = app.char(D.CHAR)["gear"]
        assert g["professions"], "the shipper's professions stay"
        assert g["items_from"] == "battle.net" and {i["item_id"] for i in g["items"]} == {10504, 1317}
        advice = app.gear(D.CHAR, None)
        assert advice["worn_from"] == "battle.net"
        worn = {s["slot"]: (s.get("current") or {}).get("item_id") for s in advice["slots"]}
        assert worn.get(1) == 10504 and worn.get(16) == 1317
    finally:
        vl.close()


def test_missing_class_comes_from_battlenet_the_rest_stays(monkeypatch, tmp_path):
    """Level splits only: no class from any addon, so Battle.net's; the
    level the splits give stays theirs."""
    app, vl = app_on(D.rows_for("only-level-splits"), monkeypatch, tmp_path)
    try:
        h = app.char(D.CHAR)["header"]
        assert h["class"] == "Mage" and h["faction"] == "Alliance" and h["race"] == "Gnome"
        assert set(h["from_battlenet"]) == {"class", "race", "faction"} and h["battlenet_read"]
        assert h["level"] != 30  # the splits' level wins
        assert app.gear(D.CHAR, None).get("available") is not False  # the advice has a class to score for
    finally:
        vl.close()


def test_nothing_from_either_says_whats_coming(monkeypatch, tmp_path):
    """No Syndicator, nothing from Battle.net yet: the page says when it comes."""
    app, vl = app_on(D.rows_for("no-syndicator"), monkeypatch, tmp_path, worn={}, profile=None)
    try:
        g = app.char(D.CHAR)["gear"]
        assert g["items"] == [] and "within about 10 minutes" in g["items_note"] and "2 hours" in g["items_note"]
    finally:
        vl.close()
    # Without an exporter at all: the page's own empty panel (Syndicator's line).
    app, vl = app_on(D.rows_for("no-syndicator"), monkeypatch, tmp_path, worn={}, profile=None, exporter=False)
    try:
        assert app.char(D.CHAR)["gear"] is None
    finally:
        vl.close()


class VMWow(Store):
    """vm-wow as the exporter fills it, read `age` seconds after Battle.net answered."""

    def __init__(self, age: float):
        super().__init__("http://vl.invalid", profile_vm_url="http://vm.invalid")
        self.age = age

    def profile_query(self, q):
        if "refreshed" in q:
            return [{"metric": {}, "value": [1, str(time.time() - self.age)]}]
        if "wow_character_equipped_stat" in q:
            return []
        if "wow_character_equipped" in q:
            return [{"metric": {"slot": "head"}, "value": [1, "10504"]}]
        if "wow_character_profile" in q:
            return [{"metric": {"class_id": "8", "race_id": "7", "faction": "alliance", "gender": "female",
                                "in_guild": "false"}, "value": [1, "30"]}]
        return []


def test_battlenet_data_is_never_shown_past_30_days():
    fresh, old = VMWow(3600), VMWow(30 * 86400 + 60)
    assert fresh.character_equipment("A-B") == {"head": {"item_id": 10504, "stats": {}}}
    assert fresh.character_profile("A-B")["class"] == "MAGE"
    assert old.character_equipment("A-B") == {} and old.character_profile("A-B") is None
    assert VMWow(29 * 86400).character_profile("A-B")["level"] == 30


def test_a_battlenet_read_is_never_copied_into_the_analysis_files(monkeypatch, tmp_path):
    """The page's Battle.net items live in its answers only: the analysis
    folders (kept for good) never hold an item only Battle.net named."""
    app, vl = app_on(D.rows_for("no-syndicator"), monkeypatch, tmp_path)
    try:
        app.char(D.CHAR)
        kept = [p.read_text(errors="replace") for p in tmp_path.rglob("*.json")]
        assert not any("10504" in json.dumps(k) for k in kept)
    finally:
        vl.close()
