"""Crash points on the ingest path (properties 11 and 12).

An event goes shipper outbox (the spool) -> network -> the server's store ->
the server's accept (the 200, #51) -> the shipper's checkpoint (the spool
file rewritten or removed, sent/). wcl-mcp commits its data and checkpoint
in one transaction; here they are separate steps, so each boundary must be
safe on its own. A few play days (tests/invariants/test_restore.py's) go
through the real shipper to the real /ingest (wowstats.ui over a
VictoriaLogs stand-in), and the process is killed, or the I/O fails, at one
boundary on one day. After a restart and the rest of the days, the store
holds every logical event of a run without the crash, every copy of one
`source_id` is the same line, and the prepared facts are the same.

A kill is a BaseException the shipper's own `except Exception` can't catch,
like a kill -9: nothing after it runs.
"""

from __future__ import annotations

import gc
import http.client
import json
import os
import shutil
import threading
import urllib.parse
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.invariants import histories as H
from tests.invariants.test_restore import CHARS, days
from tests.test_shipper_restore import POLESTAR, questie, recorder, syndicator
from wowstats import ui

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="no git")

PW = "crash-points-password"
SEED = 1


class Killed(BaseException):
    """kill -9: no handler in the shipper runs."""


class Server:
    """The real /ingest over a VictoriaLogs that keeps every line it stores."""

    def __init__(self, run_app):
        self.lines: list[dict] = []
        me = self

        class VL(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if self.path.startswith("/insert/jsonline"):
                    me.lines += [json.loads(ln) for ln in body.splitlines() if ln.strip()]
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):  # the page's builder may ask; nothing to say
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass

        self.vl = ThreadingHTTPServer(("127.0.0.1", 0), VL)
        self.vl.daemon_threads = True
        threading.Thread(target=self.vl.serve_forever, daemon=True).start()
        app = ui.App({"BIND": "127.0.0.1", "VL_URL": f"http://127.0.0.1:{self.vl.server_address[1]}",
                      "INGEST_PASSWORD": PW})
        self.url = run_app(app).base + "/ingest"

    def close(self) -> None:
        self.vl.shutdown()
        self.vl.server_close()

    def history(self) -> list[dict]:
        """Every stored line but the install's own report (about the run, not the play)."""
        return [ln for ln in self.lines if ln["feed"] != "wow-setup"]

    def canonical(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for ev in self.history():
            if ev["source_id"] not in out or ev["_time"] >= out[ev["source_id"]]["_time"]:
                out[ev["source_id"]] = ev
        return out


def play(shipper, tmp_path, monkeypatch, run_app, fault=None, on_day: int = -1) -> Server:
    """The seed's play days through the shipper to a fresh server; `fault`
    arms its crash on day `on_day`, and the next run is the restart."""
    srv = Server(run_app)
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
        f"[ship]\nurl = '{srv.url}'\nuser = 'wow'\npassword = '{PW}'\n")
    monkeypatch.setenv("WOW_STATS_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr(shipper, "QUIET_S", 0)
    monkeypatch.setattr(shipper, "NAMES", {})
    monkeypatch.setattr(shipper.sys, "argv", ["wow_stats.py"])

    def run() -> str:
        try:
            shipper.main()
        except Killed:
            gc.collect()  # the dead run's lock file closes, as the OS would
            return "killed"
        return "ran"

    schedule = days(SEED)
    for i, (when, keys, quests, items) in enumerate(schedule):
        (sv / "Polestar_Recorder.lua").write_text(recorder(keys))
        (sv / "Questie.lua").write_text(questie(quests))
        (sv / "Syndicator.lua").write_text(syndicator(items))
        t = datetime.fromisoformat(when).replace(tzinfo=UTC).timestamp()
        for f in [*sv.iterdir(), *acct.glob("Realm/*/SavedVariables/*")]:
            os.utime(f, (t, t))
        if fault is not None and i == on_day % len(schedule):
            with fault(shipper, srv, arch, monkeypatch) as hit:
                run()
            assert hit, f"{fault.__name__}: the crash point was never reached"
            # The restart: the next scheduled run, before the game writes its files again.
        assert run() == "ran"
    assert not list((arch / "spool").glob("*.ndjson")), "an event waits in the spool"
    return srv


# ---------------------------------------------------------------- the crash points


class _Armed:
    """One shot: `hit` is true once the crash point was reached."""

    def __init__(self, undo):
        self.hit, self.undo = False, undo

    def __enter__(self):
        return self

    def __bool__(self):
        return self.hit

    def __exit__(self, *exc):
        self.undo()
        return False


def after_outbox_write(shipper, srv, arch, monkeypatch):
    """The spool file is in place (Out.close renamed it); state.json is not."""
    orig = shipper.Out.close

    def close(self):
        dest = orig(self)
        if dest is not None and not armed.hit:
            armed.hit = True
            raise Killed
        return dest

    monkeypatch.setattr(shipper.Out, "close", close)
    armed = _Armed(lambda: monkeypatch.setattr(shipper.Out, "close", orig))
    return armed


def mid_upload(shipper, srv, arch, monkeypatch):
    """Half the body is on the wire, cut on a line boundary, when the
    shipper dies: the server sees the connection close short of the
    Content-Length."""
    orig = shipper.post

    def post(url, headers, body):
        if armed.hit:
            return orig(url, headers, body)
        armed.hit = True
        lines = body.splitlines(keepends=True)
        half = b"".join(lines[:max(1, len(lines) // 2)])
        u = urllib.parse.urlsplit(url)
        conn = http.client.HTTPConnection(u.hostname, u.port, timeout=10)
        conn.putrequest("POST", u.path)
        for k, v in {**headers, "Content-Length": str(len(body))}.items():
            conn.putheader(k, v)
        conn.endheaders()
        conn.send(half)
        armed.stored_before = len(srv.lines)
        conn.close()
        raise Killed

    monkeypatch.setattr(shipper, "post", post)
    armed = _Armed(lambda: monkeypatch.setattr(shipper, "post", orig))
    return armed


def stored_not_accepted(shipper, srv, arch, monkeypatch):
    """The server's store took the lines, then the server died before its
    200: the shipper sees the connection drop."""
    orig = ui.prebuilt.soon  # runs after the store's write, before the 200

    def soon(*a, **k):
        if not armed.hit:
            armed.hit = True
            raise RuntimeError("the server dies here")
        return orig(*a, **k)

    monkeypatch.setattr(ui.prebuilt, "soon", soon)
    armed = _Armed(lambda: monkeypatch.setattr(ui.prebuilt, "soon", orig))
    return armed


def accepted_not_checkpointed(shipper, srv, arch, monkeypatch):
    """The 200 came back; the shipper dies before it writes sent/ or
    rewrites the spool file."""
    orig = shipper.post

    def post(url, headers, body):
        code = orig(url, headers, body)
        if not armed.hit and code is not None and 200 <= code < 300:
            armed.hit = True
            raise Killed
        return code

    monkeypatch.setattr(shipper, "post", post)
    armed = _Armed(lambda: monkeypatch.setattr(shipper, "post", orig))
    return armed


def state_write_cut(shipper, srv, arch, monkeypatch):
    """The shipper dies half way through writing its new state."""
    orig = shipper.save_state

    def save_state(state):
        if armed.hit:
            return orig(state)
        armed.hit = True
        text = json.dumps(state, indent=1, sort_keys=True)
        shipper.STATE.with_name(".state.json.tmp").write_text(text[:len(text) // 2])
        raise Killed

    monkeypatch.setattr(shipper, "save_state", save_state)
    armed = _Armed(lambda: monkeypatch.setattr(shipper, "save_state", orig))
    return armed


def state_file_cut(shipper, srv, arch, monkeypatch):
    """The run finishes, then the machine loses power before state.json's
    data reached the disk: it comes back cut short (the rename kept, the
    data lost)."""

    def cut():
        text = shipper.STATE.read_text()
        shipper.STATE.write_text(text[:len(text) // 2])
        armed.hit = True

    armed = _Armed(cut)
    return armed


FAULTS = [after_outbox_write, mid_upload, stored_not_accepted, accepted_not_checkpointed, state_write_cut,
          state_file_cut]


@pytest.fixture(scope="module")
def reference(shipper, tmp_path_factory):
    """The same days with no crash: every event once, and the prepared facts."""
    from tests.conftest import Running

    started: list = []

    def run_app(app):
        started.append(Running(app))
        return started[-1]

    with pytest.MonkeyPatch.context() as mp:
        srv = play(shipper, tmp_path_factory.mktemp("ref"), mp, run_app)
    want = srv.canonical()
    facts = {c: H.facts(H.build([dict(r) for r in want.values()], c)) for c in CHARS}
    yield want, facts
    srv.close()
    for r in started:
        r.close()


def _changed(got: dict, want: dict) -> dict:
    return {k: {f: (got[k].get(f), want[k].get(f)) for f in set(got[k]) | set(want[k])
                if got[k].get(f) != want[k].get(f)} for k in got.keys() & want.keys() if got[k] != want[k]}


# The first run (nothing to fall back on) and the last day (every kind of
# state there is); a middle day found nothing the last one didn't.
@pytest.mark.parametrize("on_day", [0, -1], ids=["first run", "last day"])
@pytest.mark.parametrize("fault", FAULTS, ids=lambda f: f.__name__)
def test_a_crash_loses_and_doubles_nothing(shipper, tmp_path, monkeypatch, run_app, reference, fault, on_day):
    want, facts = reference
    srv = play(shipper, tmp_path, monkeypatch, run_app, fault, on_day)
    try:
        got = srv.canonical()
        assert set(got) == set(want), "an event lost, or one the play never had"
        assert got == want, f"events changed (now, without the crash): {_changed(got, want)}"
        # A copy sent twice is the same line, so no dedupe rule can pick another one.
        differ = [ev["source_id"] for ev in srv.history() if ev != got[ev["source_id"]]]
        assert not differ, f"copies of one event differ: {differ[:5]}"
        for c in CHARS:
            assert H.facts(H.build([dict(r) for r in got.values()], c)) == facts[c]
    finally:
        srv.close()


def test_a_cut_upload_stores_nothing(run_app):
    """Mid-upload, on the server's side: the connection ends short of the
    Content-Length, on a line boundary. The lines that did arrive are
    whole JSON, but the upload isn't: nothing is stored and the answer is
    not a 2xx, so the shipper's resend is the only copy."""
    import base64
    import socket

    srv = Server(run_app)
    try:
        ev = {"_time": "2026-11-17T15:14:03.120Z", "feed": "wow-level", "char": "Hero-Alpha", "_msg": "x",
              "level": 3, "source_id": "level:Hero-Alpha:3"}
        body = b"".join(json.dumps({**ev, "level": n, "source_id": f"level:Hero-Alpha:{n}"}).encode() + b"\n"
                        for n in range(3, 7))
        half = body[:body.index(b"\n", len(body) // 2) + 1]
        u = urllib.parse.urlsplit(srv.url)
        auth = base64.b64encode(f"wow:{PW}".encode()).decode()
        with socket.create_connection((u.hostname, u.port), timeout=10) as s:
            s.sendall(f"POST {u.path} HTTP/1.1\r\nHost: x\r\nAuthorization: Basic {auth}\r\n"
                      f"Content-Type: application/x-ndjson\r\nContent-Length: {len(body)}\r\n\r\n".encode() + half)
            s.shutdown(socket.SHUT_WR)
            reply = b""
            while chunk := s.recv(4096):
                reply += chunk
        status = int(reply.split(b" ", 2)[1]) if reply else None
        assert status is None or not 200 <= status < 300, reply[:200]
        assert srv.lines == []
    finally:
        srv.close()


def test_the_spool_and_the_state_reach_the_disk_before_they_count(shipper, tmp_path, monkeypatch):
    """A power cut keeps a rename and can lose the data written before it
    (the file comes back empty or short). Each file is flushed to the disk
    before the rename that publishes it, and the spool before the state
    that says its events are taken care of."""
    monkeypatch.setattr(shipper, "SPOOL", tmp_path / "spool")
    monkeypatch.setattr(shipper, "STATE", tmp_path / "state.json")
    calls: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(shipper.os, "fsync", lambda fd: (calls.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(shipper.os, "replace",
                        lambda a, b: (calls.append(f"replace {os.path.basename(a)}"), real_replace(a, b))[1])
    out = shipper.Out(dry=False)
    out.emit({"_time": "2026-11-17T15:14:03.120Z", "feed": "wow-level", "char": "Hero-Alpha", "source_id": "x"})
    spool_tmp = out.tmp.name
    assert out.close() is not None
    shipper.save_state({"rec": {"aaaa:1": 3}})
    shipper.save_state({"rec": {"aaaa:1": 4}})
    assert json.loads((tmp_path / "state.json").read_text()) == {"rec": {"aaaa:1": 4}}
    spool, state = calls.index(f"replace {spool_tmp}"), calls.index("replace .state.json.tmp")
    assert "fsync" in calls[:spool], f"the spool renamed before it was flushed: {calls}"
    assert "fsync" in calls[spool + 1:state], f"the state renamed before it was flushed: {calls}"
