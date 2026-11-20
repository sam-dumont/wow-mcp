"""The owner's overview (/admin, wowstats.admin): the owner gets it, a friend
gets the same 404 as any unknown address, nobody without a login gets
anything; what it shows is times, counts and versions, never a character's
name; the publisher reports its pass over the share port.

Same fake store and identities as tests/test_tenancy.py: tenant 0 holds the
owner's canned characters, tenant 1 the friend's (renamed)."""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.mcp import fakes
from tests.test_tenancy import (  # noqa: F401 - fixtures
    A_MARKERS,
    FRIEND,
    OWNER,
    RENAME,
    as_user,
    env,
    friend_ready,
    server,
    vl,
)
from wowstats import __version__, activity, admin, analysis, guard, publish

PATHS = ("/admin", "/api/admin/overview")
# Every character name, both tenants', as the page could print it.
NAMES = [c["char"].split("-")[0].encode() for c in fakes.CHARS] + [v.encode() for v in RENAME.values()] + A_MARKERS


@pytest.fixture(autouse=True)
def fresh_cache():
    admin._cache.clear()
    yield
    admin._cache.clear()


def overview(server) -> dict:  # noqa: F811
    admin._cache.clear()
    code, _, body = server.get("/api/admin/overview", headers=as_user(OWNER))
    assert code == 200, body
    return json.loads(body)


def test_the_owner_gets_the_page(server):  # noqa: F811
    for path in PATHS:
        code, headers, body = server.get(path, headers=as_user(OWNER))
        assert code == 200, (path, body[:200])
    code, headers, body = server.get("/admin", headers=as_user(OWNER))
    assert headers["Content-Type"].startswith("text/html")
    assert b'href="/account.css"' in body and b'href="/admin">Reload</a>' in body
    # One script, the shared theme switch: nothing reloads the page by itself.
    assert re.findall(rb'<script src="([^"]+)"', body) == [b"/theme.js"] and b"http-equiv" not in body
    assert headers["Cache-Control"] == "no-store"


def test_a_friend_gets_a_404_like_any_unknown_address(server):  # noqa: F811
    h = friend_ready(server)
    unknown = server.get("/nothing-here", headers=h)
    for path in PATHS + ("/admin/",):
        code, headers, body = server.get(path, headers=h)
        assert code == 404, path
        assert b"Admin" not in body and b"admin" not in body.replace(b"/admin", b"")
    # The same page as any address that leads nowhere: nothing says it exists.
    assert server.get("/admin", headers={**h, "Accept": "text/html"})[2] == \
        server.get("/nothing-here", headers={**h, "Accept": "text/html"})[2]
    assert unknown[0] == 404


def test_no_login_or_an_unlisted_login_gets_nothing(server):  # noqa: F811
    for h in ({}, as_user("someone")):
        for path in PATHS:
            code, _, body = server.get(path, headers=h)
            assert code in (401, 403), (path, code)
            assert b"people" not in body and b"checks" not in body


def ingest(server, slug: str, n: int = 2) -> None:  # noqa: F811
    t = server.t
    u = t.registry.by_slug(slug)
    _, token = t.registry.redeem(t.registry.new_code(u.sub))
    auth = "Basic " + base64.b64encode(f"{slug}:{token}".encode()).decode()
    lines = "\n".join(json.dumps({"_time": "2026-11-17T15:14:03Z", "feed": "wow-level", "char": "Testfriend-Otherhall",
                                  "level": 3 + i}) for i in range(n))
    code, _, body = server.get("/ingest", headers={"Authorization": auth}, method="POST", data=lines.encode())
    assert code == 200, body


def test_the_rows_say_who_and_when(server):  # noqa: F811
    h = friend_ready(server)
    server.get("/api/chars", headers=h)  # a visit
    ingest(server, "mira", 3)
    code = server.t.registry.new_code(server.t.registry.by_slug("mira").sub)
    assert server.get(f"/install/{code}", headers={"User-Agent": "Mozilla/5.0 WindowsPowerShell/5.1"})[0] == 200
    assert server.t.mcp_user(FRIEND) is not None  # an MCP call
    o = overview(server)
    assert o["version"] == __version__
    people = {p["slug"]: p for p in o["people"]}
    assert set(people) >= {"sam", "mira"}
    assert o["people"][0]["slug"] == "sam" and people["sam"]["owner"] and people["sam"]["admitted"]
    m = people["mira"]
    assert m["admitted"] is True and not m["owner"] and m["public_on"] is False
    assert m["full"] and m["mcp"] and m["upload"]["at"] and m["upload"]["events"] == 3
    assert m["os"] == "Windows" and m["version"] == __version__
    assert m["chars"] == len(fakes.CHARS) and people["sam"]["chars"] == len(fakes.CHARS)
    assert m["addons"] == {"state": "none", "text": "no report"}
    assert m["build"] is None  # no publisher report yet
    assert m["stale"] == []
    # The status strip: the store's check and the state folder.
    names = {c["name"] for c in o["checks"]}
    assert "Store (VictoriaLogs)" in names and "vm-wow" in names
    assert o["disk"]["bytes"] > 0 and o["publisher"] is None


def test_a_person_off_the_list_and_stale_flags(server, tmp_path):  # noqa: F811
    friend_ready(server)
    t = server.t
    u = t.registry.by_slug("mira")
    # An old shipper, an upload long ago.
    t.activity._update(u, install={"at": "2026-01-01T00:00:00+00:00", "os": "macOS", "version": "0.1.0"},
                       upload={"at": "2026-01-01T00:00:00+00:00", "events": 5})
    m = {p["slug"]: p for p in overview(server)["people"]}["mira"]
    assert any(f.startswith("no upload for") for f in m["stale"])
    assert "shipper older than the server" in m["stale"]
    # Taken off the list: still a row until the offboarding, "no".
    with open(t.friends_file, "w") as f:
        json.dump({"friend-3": "pip"}, f)
    m = {p["slug"]: p for p in overview(server)["people"]}["mira"]
    assert m["admitted"] is False


def test_the_setup_report_is_one_badge():
    assert admin.addons_badge(None)["text"] == "no report"
    assert admin.addons_badge({"addons": '{"polestar": "1", "recorder": "2"}', "recorder_on": "true"}) == \
        {"state": "ok", "text": "addons OK"}
    assert admin.addons_badge({"addons": '{"questie": "1"}'})["text"] == "2 missing"
    assert admin.addons_badge({"addons": {"polestar": "1", "recorder": "2"}, "recorder_on": "false"})["text"] == \
        "Recorder off"
    # Never switched on: the shipper says "unknown" (before 0.7.0 it left the field out).
    for rep in ({"addons": {"polestar": "1", "recorder": "2"}, "recorder_on": "unknown"},
                {"addons": {"polestar": "1", "recorder": "2"}}):
        assert admin.addons_badge(rep) == {"state": "bad", "text": "Recorder not switched on yet"}


def test_no_character_name_no_secret_on_the_page(server):  # noqa: F811
    friend_ready(server)
    ingest(server, "mira")
    t = server.t
    u = t.registry.by_slug("mira")
    for path in PATHS[0::2]:
        admin._cache.clear()
        code, _, body = server.get(path, headers=as_user(OWNER))
        assert code == 200
        found = [n for n in NAMES if n in body]
        assert not found, (path, found)
        for secret in (OWNER, FRIEND, t.registry.get(u.sub).ingest_sha256, "account_id", "ingest"):
            assert secret.encode() not in body, (path, secret)
        assert b"zogzog" not in body and b"#" + b"key" not in body


def test_seen_is_one_timestamp_written_at_most_every_5_minutes(tmp_path):
    class U:
        account_id = 7

    a = activity.Activity(lambda u: tmp_path / str(u.account_id))
    a.seen(U, "full", now=1000.0)
    first = a.get(U)["full"]
    (tmp_path / "7" / activity.FILE).write_text(json.dumps({"full": "kept"}))
    a.seen(U, "full", now=1000.0 + activity.SEEN_EVERY_S - 1)
    assert a.get(U) == {"full": "kept"}  # not written again within the window
    a.seen(U, "full", now=1000.0 + activity.SEEN_EVERY_S)
    assert a.get(U)["full"] not in ("kept", None) and first
    a.uploaded(U, 0)  # an empty check isn't an upload
    assert "upload" not in a.get(U)
    with pytest.raises(ValueError):
        a.seen(U, "zones")


def share(env, tenancy):  # noqa: F811
    e = {**env, "ANALYSIS_SHARE_PORT": "0", "ANALYSIS_SHARE_TOKEN_FILE": str(tenancy.state / "share-token")}
    srv = analysis.share_server(e, tenancy)
    return srv, e, f"http://127.0.0.1:{srv.server_address[1]}"


def test_the_publisher_reports_its_pass(server, env, tmp_path):  # noqa: F811
    friend_ready(server)
    srv, e, base = share(env, server.t)
    try:
        renderer_down = "http://127.0.0.1:9"  # nothing listens there
        pe = {**e, "USERS_URL": base + "/users.json", "RENDER_URL": renderer_down}
        assert publish.report_url(pe) == base + "/publisher.json"
        res = [{"slug": "sam", "built": True, "built_at": 1_700_000_000.0},
               {"slug": "mira", "built": False, "why": "failed", "error": "PublishError: no users list",
                "built_at": None},
               {"slug": "../evil", "built": True}]
        publish.send_report(pe, res, 0)
        rep = admin.read_report(server.t)
        assert [s["slug"] for s in rep["sites"]] == ["sam", "mira"]  # a bad slug is dropped
        assert rep["renderer"]["ok"] is False
        o = overview(server)
        people = {p["slug"]: p for p in o["people"]}
        assert people["sam"]["build"]["built_at"] == 1_700_000_000.0 and not people["sam"]["build"]["error"]
        assert people["mira"]["build"]["error"] == "PublishError: no users list"
        assert o["publisher"]["failed"] == 1
        assert {"name": "Renderer", "via": "publisher"}.items() <= next(
            c for c in o["checks"] if c["name"] == "Renderer").items()
        page = server.get("/admin", headers=as_user(OWNER))[2]
        assert b"PublishError: no users list" in page
        # Without the token, or not JSON: refused, nothing kept.
        req = urllib.request.Request(base + "/publisher.json", data=b"{}", method="POST")
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req, timeout=5)
        assert err.value.code == 401
        with pytest.raises(OSError, match="400"):
            guard.share_post(base + "/publisher.json", b"not json", e)
        assert admin.read_report(server.t)["sites"][0]["slug"] == "sam"
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_privacy_page_says_what_the_owner_sees():
    from wowstats import account

    page = account.privacy_page(False, {}).decode()
    assert "overview page" in page and "how many characters you have (a number, no names)" in page
    assert "Never a character's name, a zone, your gold" in page


def test_a_listed_name_that_clashes_shows_as_a_name_conflict(server):  # noqa: F811
    # The owner's own name and an app word: both his list's mistake, both
    # named on the page with why, never "(no name yet)".
    Path(server.t.friends_file).write_text(json.dumps({FRIEND: "mira", "clash-1": "sam", "clash-2": "admin"}))
    for sub in ("clash-1", "clash-2"):
        assert server.get("/api/me", headers=as_user(sub))[0] == 200
    rows = {p["name_conflict"]["slug"]: p["name_conflict"]["why"] for p in overview(server)["people"]
            if p["name_conflict"]}
    assert rows == {"sam": "taken", "admin": "reserved"}
    admin._cache.clear()
    body = server.get("/admin", headers=as_user(OWNER))[2]
    assert b"name conflict: <code>sam</code> (taken)" in body
    assert b"name conflict: <code>admin</code> (reserved)" in body
    assert b"(no name yet)" not in body


def test_versions_order_release_candidates():
    """A server on a release candidate still flags older shippers, and never
    compares a version it can't read (tools/release/release.py's X.Y.Z-rc.N)."""
    v = admin.vtuple
    assert v("0.6.1") < v("0.7.0-rc.1") < v("0.7.0-rc.2") < v("0.7.0") < v("v0.7.1")
    assert v("0.7.0rc1") is None and v("0.7") is None and v(None) is None
