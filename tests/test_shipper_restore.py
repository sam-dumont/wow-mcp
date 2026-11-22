"""Restoring a lost server from the player's PC (review 4, A).

The backup guide said `--backfill` refills an empty server from the WTF
archive. It doesn't: it reads the archive with the normal checkpoint
(state.json), which already holds every record, so it finds nothing. The
whole story here: a few play days shipped to a server, the server emptied
(local files and state kept), the documented procedure, and the server
holds the same events again (same `source_id`, same fields). Made-up
characters and files under tmp_path only."""

from __future__ import annotations

import gzip
import http.server
import json
import os
import shutil
import threading
from datetime import UTC, datetime

import pytest

from tests.test_shipper_client import CHUNKS, MAPS, lua_str

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="no git")

POLESTAR = (
    'PolestarDB = {\n["alts"] = {\n["chars"] = {\n["Hero-Alpha"] = {\n["name"] = "Hero Alpha",\n'
    '["seen"] = 1794900000,\n["level"] = 12,\n["xp"] = 10,\n["xpMax"] = 100,\n["class"] = "PRIEST",\n'
    '["race"] = "Gnome",\n["faction"] = "Alliance",\n},\n},\n},\n}\n'
)


def recorder(keys) -> str:
    body = ",\n".join(f"[{i}] = {lua_str(CHUNKS[k])}" for i, k in enumerate(keys, 1))
    maps = ",\n".join(f"[{k}] = {lua_str(v)}" for k, v in MAPS.items())
    return f'PolestarRecorderDB = {{\n["chunks"] = {{\n{body}\n}},\n["maps"] = {{\n{maps}\n}},\n}}\n'


def questie(ids: list[int]) -> str:
    done = "".join(f"[{q}] = true,\n" for q in ids)
    return f'QuestieConfig = {{\n["char"] = {{\n["Hero Alpha - Realm"] = {{\n["complete"] = {{\n{done}}},\n}},\n}},\n}}\n'


def syndicator(items: list[int]) -> str:
    bag = "".join(f'{{\n["itemID"] = {i},\n["itemCount"] = 1,\n}},\n' for i in items)
    return ('SYNDICATOR_DATA = {\n["Characters"] = {\n["Hero Alpha-Realm"] = {\n'
            '["details"] = {\n["character"] = "Hero Alpha",\n["className"] = "PRIEST",\n["race"] = "Gnome",\n'
            f'["faction"] = "Alliance",\n}},\n["bags"] = {{\n{{\n{bag}}},\n}},\n}},\n}},\n}}\n')


# Three play days: when the game wrote its files, the Recorder chunks still in
# the file (it deletes those its uploader acked), quests done, bag items.
DAYS = [("2026-11-15T19:02:11.250", [("aaaa", 1), ("bbbb", 1)], [7], [2589]),
        ("2026-11-17T07:20:00.500", [("aaaa", 2), ("aaaa", 3)], [7, 8], [2589, 2592]),
        ("2026-11-18T21:45:30.125", [("bbbb", 2)], [7, 8, 9], [2592])]


class Store:
    """The receiver: keeps every line a POST carries, answers 200."""

    def __init__(self):
        self.lines: list[dict] = []
        me = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                me.lines += [json.loads(ln) for ln in body.splitlines() if ln.strip()]
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/ingest"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def canonical(self) -> dict[str, dict]:
        """What the UI reads: the newest `_time` per source_id."""
        out: dict[str, dict] = {}
        for ev in self.lines:
            if ev["source_id"] not in out or ev["_time"] >= out[ev["source_id"]]["_time"]:
                out[ev["source_id"]] = ev
        return out


@pytest.fixture
def played(shipper, tmp_path, monkeypatch):
    """Three play days shipped to a Store; yields (store, archive folder, run)."""
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

    for when, keys, quests, items in DAYS:
        (sv / "Polestar_Recorder.lua").write_text(recorder(keys))
        (sv / "Questie.lua").write_text(questie(quests))
        (sv / "Syndicator.lua").write_text(syndicator(items))
        t = datetime.fromisoformat(when).replace(tzinfo=UTC).timestamp()
        for f in [*sv.iterdir(), *acct.glob("Realm/*/SavedVariables/*")]:
            os.utime(f, (t, t))
        assert run() == 0
    assert not list((arch / "spool").glob("*.ndjson")), "a day didn't ship"
    yield store, arch, run
    store.srv.shutdown()
    store.srv.server_close()


def test_the_history_covers_every_source(played):
    store, _, _ = played
    feeds = {e["feed"] for e in store.lines}
    assert {"wow-recorder", "wow-quests", "wow-bags"} <= feeds
    # The Recorder's first chunks are gone from the game's file by day 2:
    # only the archive and sent/ still hold them.
    assert any(e["source_id"].startswith("rec:aaaa:1:") for e in store.lines)


def test_backfill_keeps_finding_only_unseen_records(played):
    """The old procedure, on a server that lost everything: the normal
    checkpoint has every record, so --backfill sends nothing. It keeps that
    meaning (events a past run missed); the restore is --replay."""
    store, _, run = played
    before = store.canonical()
    assert sum(e["feed"] == "wow-recorder" for e in before.values()) == 21
    store.lines.clear()
    assert run("--backfill") == 0
    # Not one Recorder record (snapshots only come back because the
    # checkpoint keeps the newest one per character).
    assert not [e for e in store.lines if e["feed"] in ("wow-recorder", "wow-character")]
    assert {k for k in before if k.startswith("rec:")}.isdisjoint(store.canonical())


def test_replay_restores_what_was_delivered(played, shipper):
    store, arch, run = played
    before = store.canonical()
    state = (arch / "state.json").read_bytes()
    store.lines.clear()  # the server is lost; the PC keeps everything
    assert run("--replay") == 0
    assert store.canonical() == before
    assert (arch / "state.json").read_bytes() == state
    # sent/ doesn't grow from a replay: run it twice, same file sizes.
    sizes = {p.name: p.stat().st_size for p in (arch / "sent").glob("*.gz")}
    assert run("--replay") == 0
    assert {p.name: p.stat().st_size for p in (arch / "sent").glob("*.gz")} == sizes


def test_replay_needs_only_sent(played, shipper, tmp_path):
    """--replay reads sent/ and nothing else: a PC where the game isn't
    installed (yet), or moved, still refills the server. It used to stop
    at "no WTF folder" and exit 0 having sent nothing (recovery drill,
    2026-11-22)."""
    store, arch, run = played
    before = store.canonical()
    shutil.rmtree(tmp_path / "wow")
    store.lines.clear()
    assert run("--replay") == 0
    assert store.canonical() == before


def test_reextract_restores_from_the_archive_alone(played, shipper):
    """sent/ lost too: --reextract reads every archived version again with
    a checkpoint of its own, and never touches the normal one."""
    store, arch, run = played
    before = store.canonical()
    state = (arch / "state.json").read_bytes()
    shutil.rmtree(arch / "sent")
    store.lines.clear()
    assert run("--reextract") == 0
    # wow-setup is this run's own report (the install as it is now), not history.
    assert {k: v for k, v in store.canonical().items() if v["feed"] != "wow-setup"} == \
        {k: v for k, v in before.items() if v["feed"] != "wow-setup"}
    assert (arch / "state.json").read_bytes() == state  # the normal checkpoint, untouched
    # The normal run after it finds nothing new.
    store.lines.clear()
    assert run() == 0
    assert store.lines == []


def test_reextract_leaves_the_normal_checkpoint_alone(played, shipper, tmp_path):
    """Play nobody has shipped yet, then --reextract: the recovery reads and
    sends with its own checkpoint only (#77). state.json stays byte for
    byte as it was, so the next background run still extracts that play
    with the normal checkpoint. It used to end with a normal run that
    advanced state.json (recovery drill, 2026-11-22)."""
    store, arch, run = played
    sv = tmp_path / "wow" / "WTF" / "Account" / "1#1" / "SavedVariables"
    (sv / "Questie.lua").write_text(questie([7, 8, 9, 10]))
    (sv / "Syndicator.lua").write_text(syndicator([2592, 2593]))
    t = datetime.fromisoformat("2026-11-19T20:00:00.750").replace(tzinfo=UTC).timestamp()
    for f in sv.iterdir():
        os.utime(f, (t, t))
    state = (arch / "state.json").read_bytes()
    shutil.rmtree(arch / "sent")
    store.lines.clear()
    assert run("--reextract") == 0
    assert (arch / "state.json").read_bytes() == state
    new = {e["source_id"] for e in store.lines
           if e["feed"] in ("wow-quests", "wow-bags") and e["_time"].startswith("2026-11-19T20:00")}
    assert new, "the recovery sent the unshipped play"
    # The background run after it extracts that play itself: same ids, nothing lost.
    store.lines.clear()
    assert run() == 0
    assert new <= {e["source_id"] for e in store.lines}


def test_replay_stops_on_a_refusal_and_says_so(played, shipper, monkeypatch):
    store, arch, run = played
    monkeypatch.setattr(shipper, "post", lambda url, headers, body: 503)
    assert run("--replay") == 1
    assert "replay:" in (arch.parent / "log").read_text()


def test_replay_reads_a_sent_file_cut_short(played, shipper):
    """A crash while appending leaves a truncated gzip member: what reads
    before it still goes."""
    store, arch, run = played
    f = sorted((arch / "sent").glob("*.gz"))[0]
    whole = gzip.decompress(f.read_bytes()).splitlines()
    f.write_bytes(f.read_bytes() + gzip.compress(b'{"_time":"2026-11-18T00:00:00.000Z"')[:20])
    store.lines.clear()
    assert run("--replay") == 0
    assert len(store.lines) >= len(whole)
