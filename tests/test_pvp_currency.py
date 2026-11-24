"""The PvP rank from currency 3473 "Renown - PvP Rank" in the addons friends
already run (owner, 2026-11-24: ForeverDB stays optional).

tests/data/pvp-currency/: made-up saves in the shape the addons' code writes
them. Syndicator.lua as Tracking/CurrencyCache.lua ScanAllCurrencies saves it
on Forever (currencies[<id>] = quantity, currencyByHeader); DataStore.lua and
DataStore_Currencies.lua as DataStore_Currencies.lua ScanCurrencies_Retail
does (Currencies packed category | catalog index << 8 | count << 18; Totals[id]
= quantity | earned this week << 20; lastUpdate). Testpriest holds rank 3
(Totals 3 + 1 << 20: one earned this week); Testhunter never met the currency,
only the made-up 9999, which must never leave the shipper.
"""

import contextlib
import io
import json
import os
import shutil
from pathlib import Path

from wowstats import ingest

DATA = Path(__file__).parent / "data"
SYND_AT = 1795700000


def account(tmp_path, *files) -> Path:
    acct = tmp_path / "WTF" / "Account" / "ACCT"
    (acct / "SavedVariables").mkdir(parents=True)
    for f in files:
        src = DATA / ("foreverdb" if f == "ForeverDB.lua" else "pvp-currency") / f
        shutil.copy(src, acct / "SavedVariables" / f)
    if "Syndicator.lua" in files:
        os.utime(acct / "SavedVariables" / "Syndicator.lua", (SYND_AT, SYND_AT))
    return acct


def roster(seen=None):
    return {"Testpriest-Fixture": {"seen": seen or 0.0}, "Testhunter-Fixture": {"seen": 0.0}}


def run(shipper, acct, state, chars=None) -> list[dict]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        shipper.extract_foreverdb(acct, chars or roster(), state, shipper.Out(dry=True))
    return [json.loads(x) for x in buf.getvalue().splitlines()]


def test_syndicator_saves_the_rank_by_currency_id(shipper, tmp_path):
    acct = account(tmp_path, "Syndicator.lua")
    assert shipper.syndicator_renown(acct, roster()) == {
        "Testpriest-Fixture": {"renown": 3, "at": SYND_AT, "at_basis": None}}
    # The character's own folder dates it when older than the file.
    assert shipper.syndicator_renown(acct, roster(seen=1795650000))["Testpriest-Fixture"]["at"] == 1795650000


def test_altoholic_saves_the_rank_in_totals(shipper, tmp_path):
    acct = account(tmp_path, "DataStore.lua", "DataStore_Currencies.lua")
    # Bits 0-19 only: the one earned this week (bit 20) is not the rank.
    assert shipper.altoholic_renown(acct) == {"Testpriest-Fixture": {"renown": 3, "at": 1795600000, "at_basis": None}}


def test_one_event_from_syndicator_only_the_rank_leaves(shipper, tmp_path):
    acct, state = account(tmp_path, "Syndicator.lua"), {}
    sent = run(shipper, acct, state)
    assert [e["char"] for e in sent] == ["Testpriest-Fixture"]  # no currency: nothing, not rank 0
    ev = sent[0]
    assert ev["feed"] == "wow-foreverdb" and ev["kind"] == "pvp" and ev["source"] == "syndicator"
    assert ev["pvp_rank"] == 3 and ev["rank_from"] == "renown" and ev["renown"] == 3
    assert ev["_msg"] == "Testpriest-Fixture: PvP rank 3 (renown, Syndicator)"
    assert set(ev) <= shipper.EVENT_BASE | shipper.FDB_KEYS
    text = json.dumps(sent)
    for never in ("9999", "42", "Made Up", "Player vs. Player", "123456"):
        assert never not in text
    assert run(shipper, acct, state) == []  # unchanged: nothing again
    stored = ingest.screen(ev)
    assert stored["pvp_rank"] == 3 and stored["source"] == "syndicator"


def test_one_event_from_altoholic(shipper, tmp_path):
    sent = run(shipper, account(tmp_path, "DataStore.lua", "DataStore_Currencies.lua"), {})
    assert [(e["char"], e["source"], e["pvp_rank"], e["_time"]) for e in sent] == [
        ("Testpriest-Fixture", "altoholic", 3, "2026-11-25T09:46:40.000Z")]
    assert "Made Up" not in json.dumps(sent) and "1048579" not in json.dumps(sent)


def test_newest_snapshot_wins_across_sources(shipper, tmp_path):
    acct = account(tmp_path, "ForeverDB.lua", "DataStore.lua", "DataStore_Currencies.lua")
    got = shipper.pvp_ranks(acct, roster())
    # ForeverDB's priest snapshot (renown 0) is older than Altoholic's scan;
    # its own counts ride along. The hunter's rank is ForeverDB's alone.
    assert got["Testpriest-Fixture"]["source"] == "altoholic" and got["Testpriest-Fixture"]["pvp_rank"] == 3
    assert got["Testpriest-Fixture"]["honorable_kills"] == 0
    assert got["Testhunter-Fixture"]["source"] == "foreverdb" and got["Testhunter-Fixture"]["pvp_rank"] == 3
    # Syndicator's later save beats both for the priest.
    shutil.copy(DATA / "pvp-currency" / "Syndicator.lua", acct / "SavedVariables" / "Syndicator.lua")
    os.utime(acct / "SavedVariables" / "Syndicator.lua", (SYND_AT, SYND_AT))
    assert shipper.pvp_ranks(acct, roster())["Testpriest-Fixture"]["source"] == "syndicator"


def test_the_same_rank_from_another_addon_is_no_news(shipper, tmp_path):
    acct, state = account(tmp_path, "DataStore.lua", "DataStore_Currencies.lua"), {}
    assert len(run(shipper, acct, state)) == 1
    shutil.copy(DATA / "pvp-currency" / "Syndicator.lua", acct / "SavedVariables" / "Syndicator.lua")
    os.utime(acct / "SavedVariables" / "Syndicator.lua", (SYND_AT, SYND_AT))
    assert run(shipper, acct, state) == []


def test_currencies_are_archived(shipper):
    assert shipper.ARCHIVED_SOURCES.match("Account/ACCT/SavedVariables/DataStore_Currencies.lua")
    assert shipper.ARCHIVED_SOURCES.match("Account/ACCT/SavedVariables/Syndicator.lua")
