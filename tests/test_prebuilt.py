"""The page's answers built once per new data (wowstats.prebuilt): a plain
open reads no store once they are in, they are the routes' own answers, a
check without new data builds nothing, and with several people each set is
its owner's alone, behind the same login checks."""

from __future__ import annotations

import base64
import json
import urllib.parse

import pytest

from tests.test_public_view import CHAR, PoisonStore
from tests.test_tenancy import A_CHAR, B_CHAR, FRIEND, OWNER, env, friend_ready, leaks, vl  # noqa: F401
from wowstats import prebuilt, ui
from wowstats.stores import SourceError


class CountingStore(PoisonStore):
    """The poisoned store, counting every read it answers."""

    def __init__(self):
        super().__init__()
        self.reads: list[str] = []
        self.mark = ("2026-11-17T15:11:00Z", "12")
        self.down = False

    def _count(self, what, q):
        if self.down:
            raise SourceError("VictoriaLogs unreachable")
        self.reads.append(f"{what} {q}")

    def vl(self, query, keys=(), ops=None, limit=0):
        self._count("vl", query)
        if "count() as n" in query:
            return [{"t": self.mark[0], "n": self.mark[1]}]
        return super().vl(query, keys, ops, limit)

    def vl_raw(self, base, query, start, end, limit=0):
        self._count("vl_raw", query)
        return super().vl_raw(base, query, start, end, limit)

    def vm_query(self, q):
        self._count("vm", q)
        return super().vm_query(q)

    def vm_range(self, q, start, end, step):
        self._count("vm_range", q)
        return super().vm_range(q, start, end, step)

    def profile_query(self, q):
        self._count("profile", q)
        return super().profile_query(q)


@pytest.fixture
def full(monkeypatch):
    # The Dungeons panel reads the planner's inputs from a store of its own
    # (no vm-wow): here, the same counting one.
    from wowstats.plan import facts

    monkeypatch.setattr(facts, "store_for", lambda _env, like=None: like)
    store = CountingStore()
    app = ui.App({"VIEW": "full", "BIND": "127.0.0.1", "MODEL3D": "off"}, store=store)
    return app, store


def page_open(name: str, spec: str = "") -> list[str]:
    """What web/app.js asks for when the page opens on a character, plus the
    Journal and Timeline tabs."""
    q = urllib.parse.quote(name)
    return ["/", "/api/config", "/api/chars", f"/api/char?name={q}", "/api/market", "/api/prices",
            f"/api/gear?name={q}" + (f"&spec={spec}" if spec else ""), f"/api/loot?name={q}",
            f"/api/analysis?name={q}", f"/api/nudge?name={q}", f"/api/dungeons?name={q}", f"/api/plan?char={q}",
            f"/api/overview?name={q}", f"/api/people?name={q}", f"/api/journal?name={q}",
            f"/api/timeline?name={q}"]


def body_of(srv, path, headers=None) -> dict:
    code, _, body = srv.get(path, headers=headers)
    assert code == 200, (path, code, body[:200])
    return json.loads(body) if body.startswith(b"{") else {}


def without_clock(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in ("generated_at", "stale", "error")}


def test_a_plain_open_reads_no_store_once_built(full, run_app):
    app, store = full
    assert app.prebuilt.check() == "first build"
    srv = run_app(app)
    store.reads.clear()
    paths = page_open(CHAR)
    levels = body_of(srv, f"/api/timeline?name={CHAR}")["levels"]
    assert levels, "the test needs a level to open"
    paths += [f"/api/snapshot?name={CHAR}&level={lv}" for lv in levels]
    specs = [c["key"] for c in body_of(srv, f"/api/gear?name={CHAR}")["spec"]["choices"]]
    paths += [f"/api/gear?name={CHAR}&spec={s}" for s in specs]
    for path in paths:
        body_of(srv, path)
    assert store.reads == [], f"a plain open read the stores: {store.reads[:5]}"


def test_prebuilt_answers_are_the_routes_own(full, run_app, monkeypatch):
    """Same bytes as the answer worked out when asked (the clock aside)."""
    from wowstats.plan import facts

    app, _ = full
    app.prebuilt.check()
    built = run_app(app)
    live_app = ui.App({"VIEW": "full", "BIND": "127.0.0.1", "MODEL3D": "off"}, store=CountingStore())
    monkeypatch.setattr(facts, "store_for", lambda _env, like=None: like)
    live = run_app(live_app)
    assert live_app.prebuilt.answers is None
    for path in page_open(CHAR) + [f"/api/snapshot?name={CHAR}&level=12"]:
        if path == "/":
            continue
        assert without_clock(body_of(built, path)) == without_clock(body_of(live, path)), path


def test_a_check_without_new_data_builds_nothing(full):
    app, store = full
    assert app.prebuilt.check(now=1000.0) == "first build"
    first = app.prebuilt.answers
    store.reads.clear()
    assert app.prebuilt.check(now=1001.0) is None
    assert app.prebuilt.answers is first
    # One stats query and one vm-wow fingerprint: nothing a character's answers read.
    assert len(store.reads) == 1 + len(store.PROFILE_SERIES), store.reads
    store.mark = ("2026-11-17T15:11:00Z", "13")  # an old session sent late: same newest time, one more event
    assert app.prebuilt.check(now=1002.0) == "new data"
    assert app.prebuilt.answers is not first


def test_market_on_its_own_clock_and_a_daily_rebuild(full):
    app, store = full
    app.prebuilt.check(now=1000.0)
    first = app.prebuilt.answers
    assert app.prebuilt.check(now=1000.0 + prebuilt.MARKET_S) == "market"
    assert app.prebuilt.answers["market:api"] is not first["market:api"]
    assert app.prebuilt.answers[f"api:char:{CHAR}"] is first[f"api:char:{CHAR}"]
    assert app.prebuilt.check(now=1000.0 + prebuilt.MAX_AGE_S) == "daily rebuild"


def test_a_store_down_keeps_the_last_answers_marked_stale(full, run_app):
    app, store = full
    app.prebuilt.check()
    srv = run_app(app)
    store.down = True
    assert app.prebuilt.check() is None
    d = body_of(srv, f"/api/char?name={CHAR}")
    # The reader gets a request id, never the store's own text (review info 2);
    # the owner's overview keeps the detail.
    assert d["stale"] is True and "request" in d["error"] and "unreachable" not in d["error"] and d["header"]
    assert "unreachable" in app.prebuilt.error
    store.down = False
    app.prebuilt.check()
    assert "stale" not in body_of(srv, f"/api/char?name={CHAR}")


def test_before_the_first_build_answers_are_worked_out_when_asked(full, run_app):
    app, store = full
    srv = run_app(app)
    assert app.prebuilt.answers is None
    assert body_of(srv, f"/api/char?name={CHAR}")["header"]
    assert store.reads  # read now, as before


def test_a_new_plan_drops_the_plan_answer_and_rebuilds(full):
    app, _ = full
    app.prebuilt.check(now=1000.0)
    assert f"plan:{CHAR}" in app.prebuilt.answers
    app.prebuilt.changed("plan:")
    assert prebuilt.WAKE.is_set()
    assert not any(k.startswith("plan:") for k in app.prebuilt.answers)
    assert app.prebuilt.check(now=1001.0) == "changed"
    assert f"plan:{CHAR}" in app.prebuilt.answers


def test_the_public_view_builds_its_own_answers_only(run_app):
    store = CountingStore()
    app = ui.App({"VIEW": "public", "BIND": "127.0.0.1", "MODEL3D": "off"}, store=store)
    app.prebuilt.check()
    assert not any(k.startswith(("market:", "prices", "people:", "plan:", "api:journal:")) for k in app.prebuilt.answers)
    srv = run_app(app)
    store.reads.clear()
    for path in ("/api/chars", f"/api/char?name={CHAR}", f"/api/timeline?name={CHAR}"):
        body_of(srv, path)
    assert srv.get(f"/api/people?name={CHAR}")[0] == 404
    assert store.reads == []


# ---------------------------------------------------------------- several people

@pytest.fixture
def server(env, run_app, vl):  # noqa: F811
    app = ui.App(env)
    t = ui.tenancy_of(env, app)
    r = run_app(app)
    r.srv.RequestHandlerClass = ui.make_handler(app, t)
    r.t, r.app = t, app
    return r


def test_each_persons_answers_are_their_own(server, vl):  # noqa: F811
    h = friend_ready(server)
    apps = ui.prebuilt_apps(server.app, server.t)
    assert len(apps) == 2 and apps[0] is server.t.app_for(server.t.registry.get(OWNER))
    for a in apps:
        assert a.prebuilt.check() == "first build"
    owner, friend = (a.prebuilt.answers for a in apps)
    assert A_CHAR in json.dumps(owner) and B_CHAR not in json.dumps(owner)
    assert not leaks(json.dumps(friend).encode())
    # Served behind the same checks: the friend gets theirs, never the owner's.
    for path in page_open(B_CHAR) + page_open(A_CHAR):
        code, _, body = server.get(path, headers=h)
        assert not leaks(body, path), (path, body[:200])
    assert server.get(f"/api/char?name={A_CHAR}", headers=h)[0] == 404
    assert b"Testfriend" in server.get(f"/api/char?name={B_CHAR}", headers=h)[2]
    # No login, no answers: prebuilt or not.
    assert server.get("/api/chars")[0] in (401, 403)
    assert server.get(f"/api/char?name={A_CHAR}", headers={"X-Forwarded-User": "stranger"})[0] in (401, 403)


def test_the_share_port_serves_no_page_answer(server, env, tmp_path):  # noqa: F811
    """The share port (the publisher's) answers its own few files and
    nothing of the page, with or without its token."""
    import urllib.error
    import urllib.request

    from wowstats import analysis, guard

    for a in ui.prebuilt_apps(server.app, server.t):
        a.prebuilt.check()
    e = {**env, "ANALYSIS_SHARE_PORT": "0", "ANALYSIS_SHARE_TOKEN_FILE": str(tmp_path / "share-token")}
    srv = analysis.share_server(e, server.t)
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        token = guard.share_token(e)
        for path in page_open(A_CHAR) + page_open(B_CHAR):
            for auth in (None, f"Bearer {token}"):
                req = urllib.request.Request(base + path, headers={"Authorization": auth} if auth else {})
                with pytest.raises(urllib.error.HTTPError) as err:
                    urllib.request.urlopen(req, timeout=10)
                assert err.value.code in (401, 404), (path, auth, err.value.code)
    finally:
        srv.shutdown()
        srv.server_close()


def test_vm_wow_down_alone_is_not_a_stale_page(full, monkeypatch):
    """The publisher's rule: without vm-wow, judge on the events alone."""
    app, store = full
    app.prebuilt.check(now=1000.0)
    monkeypatch.setattr(store, "profile_fingerprint", lambda: (_ for _ in ()).throw(SourceError("vm-wow down")))
    assert app.prebuilt.check(now=1001.0) is None
    assert app.prebuilt.error is None and "stale" not in app.prebuilt.get(f"api:char:{CHAR}")


def test_an_upload_wakes_the_builder_once_after_the_burst(server, vl, monkeypatch):  # noqa: F811
    """/start says "It works" as soon as events land; the page's answers
    must not wait for the next minute's check (they said "No character
    yet" meanwhile). Several uploads in a row: one wake, after the last."""
    monkeypatch.setattr(prebuilt, "INGEST_WAKE_S", 0.3)
    friend_ready(server)
    t = server.t
    u = t.registry.get(FRIEND)
    _, token = t.registry.redeem(t.registry.new_code(u.sub))
    auth = "Basic " + base64.b64encode(f"mira:{token}".encode()).decode()
    line = json.dumps({"_time": "2026-11-17T15:14:03Z", "feed": "wow-level", "char": B_CHAR, "level": 3}).encode()
    prebuilt.WAKE.clear()
    for _ in range(3):
        assert server.get("/ingest", headers={"Authorization": auth}, method="POST", data=line)[0] == 200
        assert not prebuilt.WAKE.is_set()  # not per upload
    assert prebuilt.WAKE.wait(2.0)  # once the burst is over


def test_new_item_facts_rebuild_the_answers(monkeypatch, tmp_path, shipper):
    """The shipper sends its item facts after its events: a build those
    events started must not keep the Drowned City's bare names for a day."""
    from tests.test_itemfacts import facts_of
    from wowstats import itemfacts
    from wowstats.plan import facts

    monkeypatch.setattr(facts, "store_for", lambda _env, like=None: like)
    app = ui.App({"VIEW": "full", "BIND": "127.0.0.1", "MODEL3D": "off", "ITEM_FACTS_DIR": str(tmp_path)},
                 store=CountingStore())
    assert app.prebuilt.check(now=1000.0) == "first build"
    assert app.prebuilt.check(now=1001.0) is None
    app.item_facts.store.put_items(list(facts_of(shipper).values()), itemfacts.has_reference)
    assert app.prebuilt.check(now=1002.0) == "new data"
    assert app.prebuilt.check(now=1003.0) is None
