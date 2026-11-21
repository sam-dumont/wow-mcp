"""Restore completeness and replay fidelity (properties 1 and 2).

A few play days, drawn from a seed (which Recorder chunks the game file
still holds each day, the quests done, the bags, the second the game wrote
its files), go to a server through the real shipper. Then the server is
lost and the player's PC kept: the documented procedure (docs/self-host/
backup.md, `--replay`, #77) brings back the same events and the same
prepared facts; with sent/ lost too, `--reextract` of the archive does,
with every snapshot dated as the live run read it (#75: no `at_basis`,
so not approximate).

Builds on tests/test_shipper_restore.py (one fixed set of days) and
tests/test_shipper_capture.py.
"""

from __future__ import annotations

import os
import random
import shutil
from datetime import UTC, datetime

import pytest

from tests.invariants import histories as H
from tests.test_shipper_restore import POLESTAR, Store, questie, recorder, syndicator

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="no git")

ORDER = [("aaaa", 1), ("bbbb", 1), ("aaaa", 2), ("aaaa", 3), ("bbbb", 2)]
CHARS = ("Hero-Alpha", "Hero-Beta")


def days(seed: int) -> list[tuple[str, list, list[int], list[int]]]:
    """Three or four play days: the chunks the file holds (the ones the last
    run's ack hasn't removed yet, then the new ones), quests done so far,
    bag items, and when the game wrote its files (to the millisecond)."""
    rnd = random.Random(seed)
    n = rnd.choice((3, 4))
    cuts = sorted(rnd.sample(range(1, len(ORDER)), n - 1)) + [len(ORDER)]
    out, start, quests, t = [], 0, [7], datetime(2026, 11, 15, 18, tzinfo=UTC).timestamp()
    for end in cuts:
        keep = rnd.randint(0, start)  # older chunks still in the file
        t += rnd.uniform(0.5, 2.0) * 86400 + rnd.randint(0, 999) / 1000
        quests = sorted(set(quests) | set(rnd.sample(range(8, 30), rnd.randint(0, 3))))
        items = rnd.sample([2589, 2592, 4306, 4338], rnd.randint(1, 3))
        when = datetime.fromtimestamp(t, UTC).isoformat(timespec="milliseconds").replace("+00:00", "")
        out.append((when, ORDER[keep:end], quests, items))
        start = end
    return out


def play(shipper, tmp_path, monkeypatch, schedule) -> tuple[Store, object, object]:
    store = Store()
    wow = tmp_path / "wow"
    acct = wow / "WTF" / "Account" / "1#1"
    sv = acct / "SavedVariables"
    sv.mkdir(parents=True)
    for name, cid in (("Hero-Alpha", "aaaa"), ("Hero-Beta", "bbbb")):
        d = acct / "Realm" / name / "SavedVariables"
        d.mkdir(parents=True)
        (d / "Polestar_Recorder.lua").write_text(f'PolestarRecorderCharDB = {{\n["id"] = "{cid}",\n}}\n')
    (sv / "Polestar.lua").write_text(POLESTAR)
    arch = tmp_path / "arch"
    (tmp_path / "config.toml").write_text(
        f"wow_dir = '{wow}'\narchive_dir = '{arch}'\nlog_file = '{tmp_path / 'log'}'\n"
        "auto_update = false\nmodel3d_files = false\nitem_facts = false\n"
        f"polestar_client_dir = '{tmp_path / 'no-client'}'\n"
        f"[ship]\nurl = '{store.url}'\nuser = 'u'\npassword = 'password-long-enough'\n")
    monkeypatch.setenv("WOW_STATS_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr(shipper, "QUIET_S", 0)
    monkeypatch.setattr(shipper, "NAMES", {})

    def run(*flags: str) -> int:
        monkeypatch.setattr(shipper.sys, "argv", ["wow_stats.py", *flags])
        return shipper.main()

    for when, keys, quests, items in schedule:
        (sv / "Polestar_Recorder.lua").write_text(recorder(keys))
        (sv / "Questie.lua").write_text(questie(quests))
        (sv / "Syndicator.lua").write_text(syndicator(items))
        t = datetime.fromisoformat(when).replace(tzinfo=UTC).timestamp()
        for f in [*sv.iterdir(), *acct.glob("Realm/*/SavedVariables/*")]:
            os.utime(f, (t, t))
        assert run() == 0
    assert not list((arch / "spool").glob("*.ndjson")), "a day didn't ship"
    return store, arch, run


def prepared(canon: dict[str, dict]) -> dict:
    """The prepared facts of every character the server holds."""
    rows = [dict(r) for r in canon.values()]
    return {c: H.facts(H.build(rows, c)) for c in CHARS}


def history(canon: dict[str, dict]) -> dict[str, dict]:
    """The events as history: the install's own report (wow-setup) is about
    the run that sent it, not the play."""
    return {k: v for k, v in canon.items() if v["feed"] != "wow-setup"}


@pytest.fixture(params=range(4), ids=lambda s: f"seed{s}")
def played(request, shipper, tmp_path, monkeypatch):
    store, arch, run = play(shipper, tmp_path, monkeypatch, days(request.param))
    yield store, arch, run
    store.srv.shutdown()
    store.srv.server_close()


def test_replay_restores_the_same_events_and_facts(played):
    store, arch, run = played
    before = store.canonical()
    facts = prepared(before)
    assert any(r["feed"] == "wow-recorder" for r in before.values())
    store.lines.clear()  # the server is lost; the PC keeps its files and state.json
    assert run("--replay") == 0
    after = store.canonical()
    assert after == before
    assert prepared(after) == facts


def test_reextract_keeps_every_observation_time(played):
    """sent/ lost too: the archive alone, read again, gives the same events
    at the same times, and no snapshot is dated by the commit."""
    store, arch, run = played
    before = history(store.canonical())
    facts = prepared(before)
    shutil.rmtree(arch / "sent")
    store.lines.clear()
    assert run("--reextract") == 0
    after = history(store.canonical())
    assert {k: v["_time"] for k, v in after.items()} == {k: v["_time"] for k, v in before.items()}
    assert not [k for k, v in after.items() if v.get("at_basis")], "an approximate time on a captured snapshot"
    assert after == before
    assert prepared(after) == facts
