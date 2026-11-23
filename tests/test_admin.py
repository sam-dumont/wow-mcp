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
    A_CHAR,
    A_MARKERS,
    B_CHAR,
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
from wowstats import tenancy as T

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
    # Taken off the list: still a row until the purge, "no".
    with open(t.friends_file, "w") as f:
        json.dump({"friend-3": "pip"}, f)
    m = {p["slug"]: p for p in overview(server)["people"]}["mira"]
    assert m["admitted"] is False and m["purge_at"] is None
    # Once the pass removed them: when their data goes, on the row and in a tile.
    T.reconcile(t)
    o = overview(server)
    m = {p["slug"]: p for p in o["people"]}["mira"]
    assert m["status"] == "removed" and m["purge_at"] == o["friends"]["pending"][0]
    assert o["friends"]["purge_days"] == 7 and o["friends"]["refused_empty"] is False
    admin._cache.clear()
    page = server.get("/admin", headers=as_user(OWNER))[2].decode()
    assert "removed, data deleted" in page and "(in 7 days)" in page and "Pending deletion" in page


def test_a_refused_friends_list_shows(server):  # noqa: F811
    friend_ready(server)
    t = server.t
    T.reconcile(t)
    with open(t.friends_file, "w") as f:
        f.write("{}")
    assert T.reconcile(t)["refused"] == "empty"
    assert overview(server)["friends"]["refused_empty"] is True
    admin._cache.clear()
    assert "--allow-empty" in server.get("/admin", headers=as_user(OWNER))[2].decode()


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


# ------------------------------------------------- the AI analysis timers

def recorder(store, tenant: str, char: str, t0: str, minutes: int) -> None:
    """`minutes` of Recorder play from t0 in the fake store, where the
    timer's two reads (wowstats.analysis.auto: played_chars, played_since)
    find it: the characters' latest record straight from VictoriaLogs, the
    one character's sessions through the event cache, from its raw events."""
    from datetime import timedelta

    from wowstats.stores import iso, parse_t

    start = parse_t(t0)
    rows = [{"_time": iso(start + timedelta(minutes=m, seconds=s)), "feed": "wow-recorder", "kind": "tick"}
            for m in range(minutes) for s in (0, 30, 59)]
    store.rules[tenant] = [*fakes.raw_rules(rows, char), ("max(_time) as last", [{"char": char, "last": rows[-1]["_time"]}]),
                           *store.rules[tenant]]


@pytest.fixture
def analysis_on(env, tmp_path, monkeypatch):  # noqa: F811
    """The server's own analysis configured (OpenAI, a key file, 15 h, like
    the lab), the process' service made from it, with no provider: nothing
    here reaches one. The timer's reads are the real ones, on the real
    App and Tenancy: no fake store, no injected played/chars."""
    from wowstats.analysis import auto

    key = tmp_path / "analysis.key"
    key.write_text("not-a-real-key\n")
    monkeypatch.setattr(auto, "sdk_installed", lambda module: True)
    e = {**env, "ANALYSIS_PROVIDER": "openai", "ANALYSIS_MODEL": "gpt-test", "ANALYSIS_API_KEY_FILE": str(key),
         "ANALYSIS_STALE_PLAYED_HOURS": "15", "ANALYSIS_MONTHLY_BUDGET": "100"}
    svc = auto.Service(e, client=object())
    assert svc.cfg.on, svc.cfg.why_off()
    monkeypatch.setattr(auto, "_SERVICE", svc)
    return svc


def test_the_analysis_cell_reads_each_persons_timer_on_the_real_store(server, vl, analysis_on):  # noqa: F811
    """0.7.0 to 0.8.0: the cell handed the timer the page's store, which has
    no `logs`, and showed "store: AttributeError" for every person. On the
    real App and Tenancy both the owner's and a friend's timers read."""
    friend_ready(server)
    recorder(vl, "0", A_CHAR, "2026-11-20T18:00:00Z", 90)
    recorder(vl, "1", B_CHAR, "2026-11-21T18:00:00Z", 30)
    people = {p["slug"]: p for p in overview(server)["people"]}
    for slug, char, played in (("sam", A_CHAR, "1 h 29"), ("mira", B_CHAR, "0 h 29")):
        a = people[slug]["analysis"]
        assert "error" not in a, (slug, a.get("error"))
        assert a["chars"] == [{"char": char, "played_since": played, "due": False, "refreshed": False}], slug
        assert a["refresh_after"] == "15 h 00" and a["due"] is False
    page = server.get("/admin", headers=as_user(OWNER))[2]
    assert b"store: AttributeError" not in page


def test_the_schedulers_due_check_reads_the_same_timer(server, vl, analysis_on):  # noqa: F811
    """The worker's due check (Service.run_job) on a person's real
    environment: it reads the play time, it doesn't skip on a read error."""
    recorder(vl, "0", A_CHAR, "2026-11-20T18:00:00Z", 90)
    owner = server.t.registry.get(OWNER)
    r = analysis_on.run_job(server.t.env_for(owner))
    assert r["started"] is False
    assert r["skipped"].startswith("not due: ") and f"{A_CHAR}: 1 h 29 of play" in r["skipped"], r


def test_a_broken_timer_shows_its_message_escaped_and_cut(server, analysis_on, monkeypatch):  # noqa: F811
    def boom(e, store=None):
        raise RuntimeError("<b>bad</b> " + "x" * 500)

    monkeypatch.setattr(analysis_on, "timer", boom)
    a = {p["slug"]: p for p in overview(server)["people"]}["sam"]["analysis"]
    assert a["error"].startswith("RuntimeError: <b>bad</b> x") and len(a["error"]) == admin.ERROR_CHARS
    page = server.get("/admin", headers=as_user(OWNER))[2]
    assert b"store: RuntimeError: &lt;b&gt;bad&lt;/b&gt; x" in page and b"<b>bad</b>" not in page
