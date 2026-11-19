import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from wowstats import ui

PW = "a-long-enough-password"


def test_view_defaults_to_full_on_loopback_only():
    assert ui.App({"BIND": "127.0.0.1"}).view == "full"
    with pytest.raises(ui.ConfigError, match="VIEW is unset"):
        ui.App({"BIND": "0.0.0.0", "AUTH": "open"})
    with pytest.raises(ui.ConfigError):
        ui.App({"BIND": "0.0.0.0", "VIEW": "secret", "AUTH": "open"})


def test_auth_rules():
    with pytest.raises(ui.ConfigError, match="AUTH is unset"):
        ui.App({"BIND": "0.0.0.0", "VIEW": "full"})
    with pytest.raises(ui.ConfigError, match="AUTH=none"):
        ui.App({"BIND": "0.0.0.0", "VIEW": "full", "AUTH": "none"})
    with pytest.raises(ui.ConfigError, match="12"):
        ui.App({"BIND": "0.0.0.0", "VIEW": "full", "AUTH": "basic", "BASIC_AUTH_USER": "me",
                "BASIC_AUTH_PASSWORD": "short"})
    # A public view and a full view behind a proxy (tests/test_deploy_shapes.py
    # has every way these refuse).
    assert ui.App({"BIND": "0.0.0.0", "VIEW": "public", "AUTH": "open", "ALLOW_OPEN": "1"}).auth_mode == "open"
    assert ui.App({"BIND": "0.0.0.0", "VIEW": "full", "AUTH": "proxy",
                   "PROXY_TRUSTED_CIDRS": "198.51.100.0/24"}).auth_mode == "proxy"


def test_basic_auth_and_healthz(run_app):
    app = ui.App({"BIND": "127.0.0.1", "AUTH": "basic", "BASIC_AUTH_USER": "me", "BASIC_AUTH_PASSWORD": PW})
    srv = run_app(app)
    assert srv.get("/healthz")[0] == 200  # probes carry no login
    assert srv.get("/api/config")[0] == 401
    assert srv.get("/")[0] == 401
    tok = ui._basic("me", PW).decode()
    code, _, body = srv.get("/api/config", headers={"Authorization": tok})
    assert code == 200 and json.loads(body)["view"] == "full"


class FakeVL(BaseHTTPRequestHandler):
    got: list = []

    def do_POST(self):
        n = int(self.headers["Content-Length"])
        FakeVL.got.append((self.path, self.headers.get("Content-Type"), self.rfile.read(n)))
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_vl():
    FakeVL.got = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeVL)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_ingest_forwards_to_victorialogs(run_app, fake_vl):
    app = ui.App({"BIND": "127.0.0.1", "VL_URL": fake_vl, "INGEST_PASSWORD": PW})
    srv = run_app(app)
    tok = {"Authorization": ui._basic("wow", PW).decode(), "Content-Type": "application/x-ndjson"}
    lines = [{"_time": "2026-11-17T15:14:03.120Z", "feed": "wow-level", "char": "A-B", "_msg": "x", "level": 3},
             {"_time": "2026-11-17T15:15:03.120Z", "feed": "wow-prices", "char": "A-B", "_msg": "y"}]
    body = "\n".join(json.dumps(x) for x in lines).encode() + b"\n\n"
    code, _, resp = srv.get("/ingest", headers=tok, method="POST", data=body)
    assert code == 200 and json.loads(resp) == {"accepted": 2}
    path, ctype, sent = FakeVL.got[0]
    assert path.startswith("/insert/jsonline?")
    # component=wow as a stream field, as the lab's receiver stores it: the
    # lab's LOGS_FILTER {component="wow"} reads both paths alike.
    assert "_stream_fields=component%2Cfeed" in path and "_time_field=_time" in path and "_msg_field=_msg" in path
    assert ctype == "application/stream+json"  # anything else: VictoriaLogs answers 200 and stores nothing
    assert [json.loads(x) for x in sent.splitlines()] == [{**x, "component": "wow"} for x in lines]


def test_ingest_refuses(run_app, fake_vl):
    srv = run_app(ui.App({"BIND": "127.0.0.1", "VL_URL": fake_vl, "INGEST_PASSWORD": PW}))
    good = {"Authorization": ui._basic("wow", PW).decode()}
    assert srv.get("/ingest", method="POST", data=b"{}")[0] == 401
    assert srv.get("/ingest", headers={"Authorization": ui._basic("wow", "wrong-password-x").decode()},
                   method="POST", data=b"{}")[0] == 401
    assert srv.get("/ingest", headers=good, method="POST", data=b"not json\n")[0] == 400
    assert srv.get("/ingest", headers=good, method="POST", data=b'{"_time":"x","feed":"other"}\n')[0] == 400
    assert FakeVL.got == []
    off = run_app(ui.App({"BIND": "127.0.0.1", "VL_URL": fake_vl}))
    assert off.get("/ingest", method="POST", data=b"{}")[0] == 404


def test_an_empty_roster_is_not_kept(monkeypatch):
    """The demo: the page opened before the seed must not show no one for
    CACHE_TTL once the events are in (2026-11-19, the first GET / cached [])."""
    app = ui.App({"BIND": "127.0.0.1", "AUTH": "none", "VIEW": "full"})
    answers = [[], [{"char": "Testchar-Demo"}]]
    monkeypatch.setattr(app.views, "chars", lambda: answers.pop(0) if len(answers) > 1 else answers[0])
    assert app.chars()["chars"] == []
    assert [c["char"] for c in app.chars()["chars"]] == ["Testchar-Demo"]
    assert app.known("Testchar-Demo")


@pytest.mark.parametrize("ctype", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data", None])
def test_plan_post_needs_json(run_app, monkeypatch, ctype):
    """Review L5: a cross-site <form enctype="text/plain"> carries the
    browser's cached basic credentials and a JSON-shaped body. Only
    application/json starts a plan, and a page can't send that cross-site
    without a preflight this server never grants."""
    from wowstats.plan import http as plan_http

    ran = []
    monkeypatch.setattr(plan_http, "plan", lambda *a, **k: ran.append(a) or {"markdown": "", "writer": "",
                                                                             "estimate": {}})
    app = ui.App({"BIND": "127.0.0.1", "AUTH": "basic", "BASIC_AUTH_USER": "me", "BASIC_AUTH_PASSWORD": PW})
    srv = run_app(app)
    auth = {"Authorization": "Basic " + base64.b64encode(f"me:{PW}".encode()).decode()}
    body = b'{"hours": 3, "x": "="}'
    st, _, _ = srv.get("/api/plan", headers={**auth, **({"Content-Type": ctype} if ctype else {})},
                       method="POST", data=body)
    assert st == 415 and ran == []
    st, _, _ = srv.get("/api/plan", headers={**auth, "Content-Type": "application/json"}, method="POST", data=body)
    assert st == 200 and len(ran) == 1
