"""The shipper's auto-update (shipper/wow_stats.py check_update) against a
server that answers like wowstats' /install/update/shipper, and that route
on a real one-person server. Synthetic: a local HTTP server, a throwaway
install folder, never the real install."""

import base64
import hashlib
import json
import re
import threading
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import wowstats
from wowstats import account, missing, ui

ROOT = Path(__file__).resolve().parent.parent
REAL = (ROOT / "shipper" / "wow_stats.py").read_bytes()
PW = "a-long-enough-password"
AUTH = "Basic " + base64.b64encode(b"u:password-long-enough").decode()


def with_version(src: bytes, v: str) -> bytes:
    return re.sub(rb'(?m)^VERSION = "[^"]+"$', f'VERSION = "{v}"'.encode(), src, count=1)


class Fake:
    """path -> (status, body); records each request's path and Authorization."""

    def __init__(self):
        self.routes: dict[str, tuple[int, bytes]] = {}
        self.seen: list[tuple[str, str | None]] = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                fake.seen.append((self.path, self.headers.get("Authorization")))
                code, body = fake.routes.get(self.path, (404, b"not found\n"))
                self.send_response(code)
                if 300 <= code < 400:
                    self.send_header("Location", "http://127.0.0.1:1/elsewhere")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def offer(self, version: str, body: bytes, sha: str | None = None, extra: dict | None = None):
        files = {"wow_stats.py": {"sha256": sha or hashlib.sha256(body).hexdigest(), "size": len(body)}}
        for name, b in (extra or {}).items():
            files[name] = {"sha256": hashlib.sha256(b).hexdigest(), "size": len(b)}
            self.routes[f"/install/update/shipper/{name}"] = (200, b)
        self.routes["/install/update/shipper"] = (200, json.dumps({"version": version, "files": files}).encode())
        self.routes["/install/update/shipper/wow_stats.py"] = (200, body)

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def fake():
    f = Fake()
    yield f
    f.close()


@pytest.fixture
def installed(shipper, tmp_path, monkeypatch):
    """An installed copy at 0.5.1 in a throwaway install folder, shipping to `url`."""
    d = (tmp_path / "share" / "wow-stats").resolve()
    d.mkdir(parents=True)
    me = d / "wow_stats.py"
    me.write_bytes(with_version(REAL, "0.5.1"))
    monkeypatch.setattr(shipper, "SELF", me)
    monkeypatch.setattr(shipper, "VERSION", "0.5.1")
    monkeypatch.setattr(shipper, "install_dir", lambda: d)
    monkeypatch.setattr(shipper, "LOG", tmp_path / "wow-stats.log")
    monkeypatch.setattr(shipper, "DRY", False)
    started = []
    monkeypatch.setattr(shipper, "rerun_installer", started.append)

    def ship_to(url, **more):
        monkeypatch.setattr(shipper, "CONFIG", {**more, "ship": {"url": url, "user": "u",
                                                                  "password": "password-long-enough"}})

    class Install:
        path, log, installers = me, tmp_path / "wow-stats.log", started
        ship = staticmethod(ship_to)

        @staticmethod
        def lines():
            return [ln.split(" ", 2)[2] for ln in Install.log.read_text().splitlines()] if Install.log.exists() else []

    return Install


def test_versions_move_together(shipper):
    """One number per release: pyproject, the package and the shipper (the
    server announces the shipper's, every install climbs to it)."""
    v = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert shipper.VERSION == wowstats.__version__ == v
    assert json.loads(account.shipper_manifest())["version"] == v


def test_newer_version_replaces_the_script(shipper, installed, fake):
    new = with_version(REAL, "0.6.0")
    fake.offer("0.6.0", new)
    installed.ship(fake.base + "/ingest")
    old = installed.path.read_bytes()
    state: dict = {}
    assert shipper.check_update(state, now=1_000_000) == "updated"
    assert installed.path.read_bytes() == new
    assert installed.path.with_name("wow_stats.py.prev").read_bytes() == old
    assert not list(installed.path.parent.glob(".wow_stats.py.new"))
    assert installed.lines() == ["auto-update: 0.5.1 -> 0.6.0, from the next run on"]
    # Same server, same password, nothing else asked.
    assert fake.seen == [("/install/update/shipper", AUTH), ("/install/update/shipper/wow_stats.py", AUTH)]
    assert installed.installers == []  # no agent copies to compare: the agent is left alone
    # Once every 2 hours at most.
    assert shipper.check_update(state, now=1_000_000 + 3600) is None
    assert len(fake.seen) == 2


def test_hash_mismatch_keeps_the_running_version(shipper, installed, fake):
    fake.offer("0.6.0", with_version(REAL, "0.6.0"), sha="0" * 64)
    installed.ship(fake.base + "/ingest")
    before = installed.path.read_bytes()
    assert shipper.check_update({}, now=1) == "kept"
    assert installed.path.read_bytes() == before and not installed.path.with_name("wow_stats.py.prev").exists()
    assert installed.lines() == ["auto-update: wow_stats.py: sha256 mismatch; keeping 0.5.1"]


def test_broken_file_keeps_the_running_version(shipper, installed, fake):
    broken = with_version(REAL, "0.6.0") + b"\ndef (:\n"
    fake.offer("0.6.0", broken)
    installed.ship(fake.base + "/ingest")
    before = installed.path.read_bytes()
    assert shipper.check_update({}, now=1) == "kept"
    assert installed.path.read_bytes() == before
    assert installed.lines()[0].startswith("auto-update: the new version doesn't compile")
    # A file that says another version than the one announced: refused too.
    fake.offer("0.6.0", with_version(REAL, "0.7.0"))
    assert shipper.check_update({}, now=1) == "kept"
    assert installed.path.read_bytes() == before


def test_never_downgrades(shipper, installed, fake):
    installed.ship(fake.base + "/ingest")
    before = installed.path.read_bytes()
    for v in ("0.5.0", "0.5.1", "0.4.3"):
        fake.offer(v, with_version(REAL, v))
        assert shipper.check_update({}, now=1) == "current", v
    assert installed.path.read_bytes() == before
    assert all(p == "/install/update/shipper" for p, _ in fake.seen)  # the file never fetched
    assert installed.lines() == []


def test_release_candidates_sort_below_their_final(shipper, installed, fake):
    """A release candidate is an update over the last final and below its own
    final (tools/release/release.py names them X.Y.Z-rc.N)."""
    vt = shipper.version_tuple
    assert vt("0.6.1") < vt("0.7.0-rc.1") < vt("0.7.0-rc.2") < vt("0.7.0") < vt("0.7.1-rc.1")
    assert vt("0.7.0-rc") is None and vt("0.7.0rc1") is None
    installed.ship(fake.base + "/ingest")
    fake.offer("0.7.0-rc.1", with_version(REAL, "0.7.0-rc.1"))
    assert shipper.check_update({}, now=1) == "updated"
    assert installed.lines() == ["auto-update: 0.5.1 -> 0.7.0-rc.1, from the next run on"]


def test_server_without_the_endpoint(shipper, installed, fake):
    """A server older than the auto-update answers 404: one line, no error."""
    installed.ship(fake.base + "/ingest")
    before = installed.path.read_bytes()
    assert shipper.check_update({}, now=1) == "kept"
    assert installed.path.read_bytes() == before
    assert installed.lines() == ["auto-update: the server has no update endpoint; keeping 0.5.1"]
    # Unreachable, not JSON, a redirect (it would carry the password elsewhere): the same.
    installed.ship("http://127.0.0.1:1/ingest")
    assert shipper.check_update({}, now=1) == "kept"
    installed.ship(fake.base + "/ingest")
    fake.routes["/install/update/shipper"] = (200, b"<html>login</html>")
    assert shipper.check_update({}, now=1) == "kept"
    fake.routes["/install/update/shipper"] = (302, b"")
    assert shipper.check_update({}, now=1) == "kept"
    assert ("/elsewhere", AUTH) not in fake.seen
    assert installed.path.read_bytes() == before and len(installed.lines()) == 4


def test_what_turns_it_off(shipper, installed, fake, tmp_path, monkeypatch):
    fake.offer("0.6.0", with_version(REAL, "0.6.0"))
    installed.ship(fake.base + "/ingest", auto_update=False)  # pinned
    assert shipper.check_update({}, now=1) is None
    installed.ship(fake.base + "/ingest", auto_update="off")
    assert shipper.check_update({}, now=1) is None
    installed.ship("https://elsewhere.example/receiver")  # not a wow-stats /ingest
    assert shipper.check_update({}, now=1) is None
    installed.ship("http://192.0.2.7:8080/ingest")  # plain http off this computer
    assert shipper.check_update({}, now=1) == "kept"
    assert installed.lines()[-1] == "auto-update: only over https (or from this computer); keeping 0.5.1"
    monkeypatch.setattr(shipper, "CONFIG", {})  # no [ship]
    assert shipper.check_update({}, now=1) is None
    # A copy run from somewhere else (a git checkout) never rewrites itself.
    installed.ship(fake.base + "/ingest")
    monkeypatch.setattr(shipper, "install_dir", lambda: tmp_path / "elsewhere")
    assert shipper.check_update({}, now=1) is None
    assert fake.seen == []


def test_changed_agent_runs_the_new_installer(shipper, installed, fake, monkeypatch):
    monkeypatch.setattr(shipper, "WINDOWS", False)
    monkeypatch.setattr(shipper.sys, "platform", "darwin")
    d = installed.path.parent
    (d / "install-mac.sh").write_bytes(b"#!/bin/bash\n# old\n")
    (d / "wow-stats.plist").write_bytes(b"<plist/>\n")
    new = with_version(REAL, "0.6.0")
    script, plist = b"#!/bin/bash\n# new\n", b"<plist/>\n"
    fake.offer("0.6.0", new, extra={"install-mac.sh": script, "wow-stats.plist": plist})
    installed.ship(fake.base + "/ingest")
    assert shipper.check_update({}, now=1) == "updated"
    stage = d / ".update"
    assert installed.installers == [stage]
    assert {p.name: p.read_bytes() for p in stage.iterdir()} == {"install-mac.sh": script, "wow-stats.plist": plist,
                                                                  "wow_stats.py": new}
    # The same definition: only the script moves.
    (d / "install-mac.sh").write_bytes(script)
    fake.offer("0.7.0", with_version(REAL, "0.7.0"), extra={"install-mac.sh": script, "wow-stats.plist": plist})
    monkeypatch.setattr(shipper, "VERSION", "0.6.0")
    assert shipper.check_update({}, now=1) == "updated"
    assert installed.installers == [stage]


def test_route_on_a_one_person_server(run_app, shipper, installed, monkeypatch):
    """The real route: the shipper at an older version takes the server's
    copy, which is this repository's own wow_stats.py."""
    app = ui.App({"BIND": "127.0.0.1", "VL_URL": "http://vl.invalid", "INGEST_PASSWORD": PW})
    srv = run_app(app)
    good = {"Authorization": ui._basic("wow", PW).decode()}
    assert srv.get("/install/update/shipper")[0] == 401
    code, _, body = srv.get("/install/update/shipper", headers={**good, "Host": "any-name.example"})
    man = json.loads(body)
    assert code == 200 and man["version"] == wowstats.__version__
    assert man["files"]["wow_stats.py"]["sha256"] == hashlib.sha256(REAL).hexdigest()
    assert set(man["files"]) == set(account.SHIPPER_FILES)
    assert srv.get("/install/update/shipper/wow_stats.py", headers=good)[2] == REAL
    assert srv.get("/install/update/shipper/..%2Fsecrets", headers=good)[0] == 404
    assert srv.get("/install/update/shipper/state.json", headers=good)[0] == 404
    assert srv.get("/install/update/shipper", headers=good, method="POST", data=b"")[0] == 404
    off = run_app(ui.App({"BIND": "127.0.0.1", "VL_URL": "http://vl.invalid"}))
    assert off.get("/install/update/shipper", headers=good)[0] == 404  # no INGEST_PASSWORD: no route

    monkeypatch.setattr(shipper, "VERSION", "0.0.1")
    installed.path.write_bytes(with_version(REAL, "0.0.1"))
    monkeypatch.setattr(shipper, "CONFIG", {"ship": {"url": srv.base + "/ingest", "user": "wow", "password": PW}})
    assert shipper.check_update({}, now=1) == "updated"
    assert installed.path.read_bytes() == REAL


def test_setup_report_names_the_version(shipper, tmp_path):
    rep = shipper.setup_report(tmp_path / "_forever_")
    assert rep["shipper"] == shipper.VERSION
    rows = [{"_time": "2026-11-19T10:00:00Z", "addons": "{}", "shipper": "0.5.1"}]
    assert missing.setup(lambda q: rows)["shipper"] == "0.5.1"
    assert missing.setup(lambda q: [{"_time": "2026-11-19T10:00:00Z", "addons": "{}"}])["shipper"] is None
