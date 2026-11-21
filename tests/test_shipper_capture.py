"""Re-extracting an archived version keeps the times the live run read
(review 4, B). Git keeps no file times, so a rebuilt file used to read as
written at rebuild time, and a Questie snapshot observed on 2026-11-17 came
back dated the day of the restore: the time the quest evidence reads as
"first seen". Each commit now carries a capture manifest; an archive made
before it dates its files by the commit, labelled `at_basis: "commit"`.
Made-up characters and files under tmp_path only."""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="no git")

POLESTAR = (
    'PolestarDB = {\n["alts"] = {\n["chars"] = {\n["Demo-Player"] = {\n["name"] = "Demo Player",\n'
    '["seen"] = 1794900000,\n["level"] = 12,\n["xp"] = 10,\n["xpMax"] = 100,\n["class"] = "PRIEST",\n'
    '["race"] = "Gnome",\n["faction"] = "Alliance",\n},\n},\n},\n}\n'
)


def questie(ids: list[int]) -> str:
    done = "".join(f"[{q}] = true,\n" for q in ids)
    return f'QuestieConfig = {{\n["char"] = {{\n["Demo Player - Realm"] = {{\n["complete"] = {{\n{done}}},\n}},\n}},\n}}\n'


def syndicator(items: list[int]) -> str:
    bag = "".join(f'{{\n["itemID"] = {i},\n["itemCount"] = 1,\n}},\n' for i in items)
    return ('SYNDICATOR_DATA = {\n["Characters"] = {\n["Demo Player-Realm"] = {\n'
            '["details"] = {\n["character"] = "Demo Player",\n["className"] = "PRIEST",\n["race"] = "Gnome",\n'
            f'["faction"] = "Alliance",\n}},\n["bags"] = {{\n{{\n{bag}}},\n}},\n}},\n}},\n}}\n')


def epoch(s: str) -> float:
    return datetime.fromisoformat(s).replace(tzinfo=UTC).timestamp()


# Three play days: (when the game wrote the files, quests done, bag items).
DAYS = [("2026-11-15T19:02:11.250", [7], [2589]),
        ("2026-11-17T07:20:00.500", [7, 8], [2589, 2592]),
        ("2026-11-18T21:45:30.125", [7, 8, 9], [2592])]


def play(shipper, tmp_path, monkeypatch, commit_times: bool = False) -> list[dict]:
    """Runs the shipper (archive + extract, no network) once per play day;
    returns every event the live runs spooled."""
    wow = tmp_path / "wow"
    sv = wow / "WTF" / "Account" / "DEMO" / "SavedVariables"
    sv.mkdir(parents=True)
    (sv / "Polestar.lua").write_text(POLESTAR)
    arch = tmp_path / "arch"
    (tmp_path / "config.toml").write_text(f"wow_dir = '{wow}'\narchive_dir = '{arch}'\n"
                                          f"log_file = '{tmp_path / 'log'}'\n")
    monkeypatch.setenv("WOW_STATS_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr(shipper, "QUIET_S", 0)
    monkeypatch.setattr(shipper.sys, "argv", ["wow_stats.py", "--no-ship"])
    for i, (when, quests, items) in enumerate(DAYS):
        t = epoch(when)
        (sv / "Questie.lua").write_text(questie(quests))
        (sv / "Syndicator.lua").write_text(syndicator(items))
        for f in sv.iterdir():
            os.utime(f, (t, t))
        if commit_times:  # the archive's commit an hour after the logout
            monkeypatch.setenv("GIT_COMMITTER_DATE", f"{int(t) + 3600 + i} +0000")
        assert shipper.main() == 0
    return [json.loads(ln) for f in sorted((arch / "spool").glob("*.ndjson")) for ln in f.read_text().splitlines()]


def reextract(shipper) -> list[dict]:
    got: list[dict] = []
    out = shipper.Out(dry=True)
    out.emit = lambda ev: got.append({k: v for k, v in ev.items() if v is not None and v != ""})
    shipper.backfill({}, out)  # an empty checkpoint: everything the archive holds
    return got


def dated(events: list[dict]) -> dict[str, tuple]:
    return {e["source_id"]: (e["_time"], e.get("at_basis")) for e in events if e["feed"] in ("wow-quests", "wow-bags")}


def test_reextracting_the_archive_keeps_the_observation_times(shipper, tmp_path, monkeypatch):
    live = play(shipper, tmp_path, monkeypatch)
    want = dated(live)
    assert len(want) == 6  # a quest snapshot and a bags snapshot per day
    assert sorted(t for t, _ in want.values())[2:4] == ["2026-11-17T07:20:00.500Z"] * 2
    assert all(basis is None for _, basis in want.values())  # the live run reads exact times
    assert dated(reextract(shipper)) == want
    assert dated(reextract(shipper)) == want  # and again: same answer each time


def test_an_archive_without_manifest_dates_by_the_commit_and_says_so(shipper, tmp_path, monkeypatch):
    # The archive as a shipper before the manifest made it.
    monkeypatch.setattr(shipper, "write_capture", lambda wanted: None)
    live = play(shipper, tmp_path, monkeypatch, commit_times=True)
    assert not (tmp_path / "arch" / "wtf" / shipper.CAPTURE_FILE).exists()
    again = dated(reextract(shipper))
    assert again.keys() == dated(live).keys()
    for i, (when, _, _) in enumerate(DAYS):
        at = shipper.iso(int(epoch(when)) + 3600 + i)  # the commit's time, to the second
        assert sorted(sid for sid, v in again.items() if v == (at, "commit")) == sorted(
            sid for sid, (t, _) in dated(live).items() if t == shipper.iso(epoch(when))), when


def test_a_time_only_rewrite_makes_no_commit(shipper, tmp_path, monkeypatch):
    """The game rewrites its files at every logout; one with the same content
    moves only the manifest, which waits for the next real change."""
    play(shipper, tmp_path, monkeypatch)
    repo = tmp_path / "arch" / "wtf"
    n = len(shipper.git("rev-list", "HEAD").stdout.split())
    t = epoch("2026-11-19T10:00:00")
    for f in (tmp_path / "wow" / "WTF" / "Account" / "DEMO" / "SavedVariables").iterdir():
        os.utime(f, (t, t))
    assert shipper.main() == 0
    assert len(shipper.git("rev-list", "HEAD").stdout.split()) == n
    man = json.loads((repo / shipper.CAPTURE_FILE).read_text())["mtime_ns"]
    assert man["Account/DEMO/SavedVariables/Questie.lua"] == int(t) * 10**9
