"""Concurrent uploads are bounded (load estimate for the lab, 2026-11-22: a
logout wave of 13 shippers read every body whole at once). An upload past
the gate waits briefly, then gets a 503 the shipper keeps its spool for."""

from __future__ import annotations

import gzip
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from wowstats import ingest, ui

PW = "a-long-enough-password"
LINE = {"_time": "2026-11-17T15:14:03.120Z", "feed": "wow-level", "char": "A-B", "_msg": "x", "level": 3}


class SlowVL:
    """A VictoriaLogs that holds each insert until `go` is set, and counts
    how many it holds at once."""

    def __init__(self):
        self.go = threading.Event()
        self.lock = threading.Lock()
        self.inflight = self.peak = 0
        self.got: list[bytes] = []
        me = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                if self.path.startswith("/internal/force_flush"):  # the flush after a store: not an upload
                    self.send_response(200)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                with me.lock:
                    me.inflight += 1
                    me.peak = max(me.peak, me.inflight)
                me.go.wait(20)
                with me.lock:
                    me.inflight -= 1
                    me.got.append(body)
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def wait_inflight(self, n: int) -> None:
        end = time.monotonic() + 10
        while self.inflight < n and time.monotonic() < end:
            time.sleep(0.01)
        assert self.inflight >= n

    def close(self):
        self.go.set()
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture()
def vl():
    v = SlowVL()
    yield v
    v.close()


def upload(srv, n: int = 1):
    tok = {"Authorization": ui._basic("wow", PW).decode(), "Content-Type": "application/x-ndjson"}
    body = b"".join(json.dumps({**LINE, "level": i}).encode() + b"\n" for i in range(n))
    return srv.get("/ingest", headers=tok, method="POST", data=body)


def app(vl_url: str, **env) -> ui.App:
    return ui.App({"BIND": "127.0.0.1", "VL_URL": vl_url, "INGEST_PASSWORD": PW, **env})


def test_the_gate_bounds_concurrent_uploads(run_app, vl):
    a = app(vl.url, INGEST_CONCURRENCY="2", INGEST_WAIT_S="30")
    srv = run_app(a)
    results: list = []
    threads = [threading.Thread(target=lambda i=i: results.append(upload(srv, i + 1))) for i in range(6)]
    for t in threads:
        t.start()
    vl.wait_inflight(2)
    time.sleep(0.3)  # the other four have arrived and wait for a slot
    h = json.loads(srv.get("/healthz")[2])
    assert h["uploads_running"] == 2 and h["uploads_waiting"] == 4 and h["uploads_max"] == 2
    vl.go.set()
    for t in threads:
        t.join(20)
    assert sorted(r[0] for r in results) == [200] * 6  # each waited its turn
    assert vl.peak == 2
    assert sum(len(b.splitlines()) for b in vl.got) == sum(range(1, 7))
    h = json.loads(srv.get("/healthz")[2])
    assert h["uploads_running"] == 0 and h["uploads_waiting"] == 0


def test_an_upload_past_the_gate_gets_a_retryable_503(run_app, vl):
    a = app(vl.url, INGEST_CONCURRENCY="1", INGEST_WAIT_S="0.2")
    srv = run_app(a)
    first: list = []
    t = threading.Thread(target=lambda: first.append(upload(srv)))
    t.start()
    vl.wait_inflight(1)
    started = time.monotonic()
    code, headers, body = upload(srv, 3)
    assert code == 503 and headers["Retry-After"] == "60" and "nothing is lost" in json.loads(body)["error"]
    assert time.monotonic() - started >= 0.2  # it waited before giving up
    # A body as big as the shipper sends (4 MiB): read and dropped, so the
    # sender gets the 503, not a reset connection.
    assert upload(srv, 45000)[0] == 503
    vl.go.set()
    t.join(20)
    assert first[0][0] == 200 and len(vl.got) == 1  # only the first one was stored
    assert upload(srv, 3)[0] == 200  # a slot again: the retry goes through


def test_the_gate_settings_are_checked_at_start():
    assert ingest.Gate().limit == 4 and ingest.Gate().wait_s == 10
    with pytest.raises(ui.ConfigError, match="INGEST_CONCURRENCY"):
        app("http://unused", INGEST_CONCURRENCY="0")
    with pytest.raises(ui.ConfigError, match="INGEST_WAIT_S"):
        app("http://unused", INGEST_WAIT_S="soon")
    with pytest.raises(ui.ConfigError, match="ANALYSIS_WORKSPACES_MAX"):
        app("http://unused", ANALYSIS_WORKSPACES_MAX="many")


SPOOLED = "".join(json.dumps({**LINE, "level": i}, separators=(",", ":")) + "\n" for i in range(1, 41))


def test_the_shipper_keeps_its_spool_on_a_503_and_sends_it_later(shipper, run_app, vl, tmp_path, monkeypatch):
    """The real shipper against the real /ingest with the gate full: nothing
    moves to sent/, the spool file stays whole, the next run delivers it."""
    a = app(vl.url, INGEST_CONCURRENCY="1", INGEST_WAIT_S="0.1")
    srv = run_app(a)
    for name in ("SPOOL", "SENT", "REJECTED", "TOO_OLD"):
        monkeypatch.setattr(shipper, name, tmp_path / name.lower())
    monkeypatch.setattr(shipper, "LOG", tmp_path / "log")
    shipper.SPOOL.mkdir()
    spooled = shipper.SPOOL / "20261122T200000000000Z.ndjson"
    spooled.write_text(SPOOLED)
    monkeypatch.setattr(shipper, "CONFIG", {"ship": {"url": f"{srv.base}/ingest", "user": "wow", "password": PW}})
    vl.go.set()

    with a.uploads.slot() as ok:  # another shipper holds the only slot
        assert ok
        shipper.ship()
    assert spooled.read_text() == SPOOLED
    assert not shipper.SENT.exists() or not list(shipper.SENT.iterdir())
    assert "HTTP 503, kept for the next run" in (tmp_path / "log").read_text()
    assert vl.got == []

    shipper.ship()  # the next run, five minutes later
    assert not spooled.exists()
    stored = [json.loads(x) for b in vl.got for x in b.splitlines()]
    assert sorted(e["level"] for e in stored) == list(range(1, 41))
    sent = b"".join(gzip.decompress(p.read_bytes()) for p in shipper.SENT.glob("*.gz"))
    assert sent.decode() == SPOOLED
