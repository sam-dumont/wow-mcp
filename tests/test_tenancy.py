"""Several people on one install (wowstats.tenancy): person B never gets one
byte of person A's data, through any page, API route, MCP tool, plan,
analysis call or bundle.

The store is a fake VictoriaLogs that answers by tenant (the AccountID
header, as the real one does): tenant 0 holds the owner's canned events
(tests/mcp/fakes: Testpriest-Fixture, Thrall, Jaina, Twig), tenant 1 the same
events renamed (Testfriend-Otherhall, Varian, Tyrande, Pip). Any request to it
without the tenant headers fails the test. B's answers must hold none of
A's names, whatever B asks for, including A's character by name.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests import sealed
from tests.mcp import fakes
from wowstats import account, ingest, publish, tenancy, ui
from wowstats import mcp as mcpmod
from wowstats.mcp import tools
from wowstats.sitesettings import PageSettings

CHAR_FILTER = re.compile(r'char:=?"?([A-Z][A-Za-z]+-[A-Z][A-Za-z]+)')
RENAME = {"Testpriest": "Testfriend", "Fixture": "Otherhall", "Thrall": "Varian", "Jaina": "Tyrande", "Twig": "Pip"}
A_MARKERS = [k.encode() for k in RENAME] + [k.lower().encode() for k in RENAME]
A_CHAR, B_CHAR = "Testpriest-Fixture", "Testfriend-Otherhall"
OWNER, FRIEND, FRIENDS_ORG = "owner-sub-1", "friend-sub-2", "org-friends"

# vm-wow is ONE VictoriaMetrics for everyone (not split by tenant like
# VictoriaLogs): the exporter puts the person's account_id on every series,
# and every reader must ask for its own. The owner (0) and the friend (1)
# each have Battle.net data for their character; the owner's account also
# holds a series under the friend's character's name (a forged or reused
# name), which the friend must never read. Owner's markers carry A's names.
SERIES = re.compile(r"([a-z_]+)\{([^}]*)\}")
LABEL = re.compile(r'([a-z_]+)=("(?:[^"\\]|\\.)*")')


# Read an hour before the test runs: a fixed date stops counting as a read
# 30 days later (Blizzard's cap, views.fill_from_battlenet) and the guild
# goes blank (a fixed 2026-11-18 would have gone red on 2026-12-18).
REFRESHED = time.time() - 3600


def _profile(acct: str, char: str, guild: str, trees: list[tuple[str, int]], ach: int, spell: int) -> list:
    who = {"character": char, "account_id": acct}
    return [("wow_character_profile", {**who, "race_id": "7", "gender": "female", "guild": guild}, 30.0),
            ("wow_character_profile_found", who, 1.0),
            ("wow_character_profile_refreshed_timestamp_seconds", who, REFRESHED),
            ("wow_character_achievement", {**who, "achievement_id": str(ach)}, 1795000000.0),
            ("wow_character_achievements_read", who, 1.0),
            ("wow_character_talent_rank", {**who, "spell_id": str(spell), "talent": ""}, 2.0),
            *[("wow_character_talent_points", {**who, "tree": t, "order": str(i)}, float(n))
              for i, (t, n) in enumerate(trees)]]


PROFILE = (_profile("0", A_CHAR, "Thrall Company", [("Discipline", 16), ("Holy", 0), ("Shadow", 7)], 61499,
                      14521)
           + _profile("0", B_CHAR, "Jaina Company", [("Discipline", 0), ("Holy", 21), ("Shadow", 0)], 61500,
                        14524)
           + _profile("1", B_CHAR, "Varian Company", [("Discipline", 0), ("Holy", 0), ("Shadow", 21)], 61502,
                        15338))


def vm_answer(query: str) -> list[dict] | None:
    """VictoriaMetrics' answer for a profile series query: every label
    matcher must hold, as the real store does. None for other queries."""
    m = SERIES.search(query)
    if not m or not m.group(1).startswith("wow_character_"):
        return None
    want = {k: json.loads(v) for k, v in LABEL.findall(m.group(2))}
    return [{"metric": {"__name__": n, **lab}, "value": [1795000000, str(v)]}
            for n, lab, v in PROFILE if n == m.group(1) and all(lab.get(k) == x for k, x in want.items())]


def vm_range_answer(query: str, start: int, end: int) -> list[dict]:
    """vm-wow over time (the talent history): every profile series as it is
    now at `end`, and the talent trees at 0 a day after `start` (a fresh
    character then), so each person's history is one change, to their own
    build. The same label matching as vm_answer."""
    rows = vm_answer(query) or []
    out = []
    for r in rows:
        values = [[end, r["value"][1]]]
        if r["metric"]["__name__"] == "wow_character_talent_points":
            values.insert(0, [start + 86400, "0"])
        out.append({"metric": r["metric"], "values": values})
    return out


def renamed(v):
    if isinstance(v, str):
        for a, b in RENAME.items():
            v = v.replace(a, b)
        return v
    if isinstance(v, list | tuple):
        return type(v)(renamed(x) for x in v)
    if isinstance(v, dict):
        return {renamed(k): renamed(x) for k, x in v.items()}
    return v


class TenantVL:
    """VictoriaLogs and VictoriaMetrics on one port, logs per tenant."""

    def __init__(self):
        # The web view's character list asks with counts (the MCP's fakes don't).
        ui_rules = [("stats by (char) count() as events", [{**c, "events": "120"} for c in fakes.CHARS])]
        self.rules = {"0": ui_rules + fakes.LOGS, "1": renamed(ui_rules + fakes.LOGS)}
        # The owner has WoWSims Exporter (his own wow-talents events, in his
        # tenant); the friend hasn't: his history is Battle.net's.
        # One character's reads go through the event cache: its hourly count, then the hours' rows.
        saves = [{"_time": "2026-10-01T20:00:00Z", "feed": "wow-talents", "char": A_CHAR, "talents": "0050",
                  "level": 12, "class": "priest"},
                 {"_time": "2026-11-18T20:00:00Z", "feed": "wow-talents", "char": A_CHAR,
                  "talents": "02500420103--00502", "level": 33, "class": "priest"}]
        self.rules["0"] = [("-feed:wow-combatlog | stats by (_time:1h) count() as n",
                            [{"_time": r["_time"], "n": "1"} for r in saves]),
                           ('feed:="wow-talents"', saves), ("-feed:wow-combatlog", saves), *self.rules["0"]]
        assert "Testpriest" not in json.dumps(self.rules["1"])
        self.chars = {"0": {c["char"] for c in fakes.CHARS}, "1": {renamed(c["char"]) for c in fakes.CHARS}}
        self.untagged: list[str] = []
        self.inserted: dict[str, list[dict]] = {}
        self.insert_fails = False  # the store answers 500 to every write
        self.deleted: list[tuple[str, str]] = []
        self.vm_queries: list[str] = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _tenant(self) -> str | None:
                acct, proj = self.headers.get("AccountID"), self.headers.get("ProjectID")
                if acct is None or proj != "0":
                    fake.untagged.append(self.path)
                    return None
                return acct

            def _send(self, code, body, ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                u = urllib.parse.urlsplit(self.path)
                q = dict(urllib.parse.parse_qsl(u.query))
                if u.path == "/select/logsql/query":
                    t = self._tenant()
                    if t is None:
                        return self._send(400, b"no tenant")
                    query = q.get("query", "")
                    rows = next((r for s, r in fake.rules.get(t, []) if s in query), [])
                    # A character this tenant doesn't have: no rows, as the real store answers.
                    mine = fake.chars.get(t, set())
                    if any(n not in mine for n in CHAR_FILTER.findall(query)):
                        rows = []
                    return self._send(200, "".join(json.dumps(r) + "\n" for r in rows).encode(),
                                      "application/stream+json")
                if u.path == "/api/v1/query":
                    fake.vm_queries.append(q.get("query", ""))
                    res = vm_answer(q.get("query", ""))
                    if res is None:
                        res = next((r for s, r in fakes.METRICS if s in q.get("query", "")), [])
                    return self._send(200, json.dumps({"status": "success", "data": {"result": res}}).encode())
                if u.path == "/api/v1/query_range":
                    fake.vm_queries.append(q.get("query", ""))
                    res = vm_range_answer(q.get("query", ""), int(q.get("start") or 0), int(q.get("end") or 0))
                    return self._send(200, json.dumps({"status": "success", "data": {"result": res}}).encode())
                self._send(404, b"")

            def do_POST(self):
                u = urllib.parse.urlsplit(self.path)
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n)
                t = self._tenant()
                if t is None:
                    return self._send(400, b"no tenant")
                if u.path == "/insert/jsonline":
                    if fake.insert_fails:
                        return self._send(500, b"disk full")
                    fake.inserted.setdefault(t, []).extend(json.loads(x) for x in body.splitlines() if x.strip())
                    return self._send(200, b"")
                if u.path == "/delete/run_task":
                    fake.deleted.append((t, body.decode()))
                    return self._send(200, b'{"task_id":"1"}')
                self._send(404, b"")

        srv = type("S", (ThreadingHTTPServer,), {"request_queue_size": 64, "daemon_threads": True})
        self.httpd = srv(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def vl():
    v = TenantVL()
    yield v
    v.close()
    # The one rule every test checks: no VictoriaLogs request without its tenant.
    assert v.untagged == [], f"VictoriaLogs requests without AccountID/ProjectID: {v.untagged[:5]}"


@pytest.fixture
def env(vl, tmp_path):
    master = tmp_path / "master.key"
    master.write_text(tenancy.b64u(b"m" * 32))
    friends = tmp_path / "friends.json"  # who gets in besides the owner: FRIEND as mira, friend-3 as pip
    friends.write_text(json.dumps({FRIEND: "mira", "friend-3": "pip"}))
    return {"BIND": "127.0.0.1", "VIEW": "full", "AUTH": "proxy", "PROXY_TRUSTED_CIDRS": "127.0.0.1/32", "VL_URL": vl.url, "VM_URL": vl.url,
            "FRIENDS_VL_URL": vl.url, "WOW_STATE_DIR": str(tmp_path / "state"), "WOW_OWNER_SUB": OWNER,
            "WOW_OWNER_SLUG": "sam", "WOW_OWNER_ALIASES": "wow.m.example", "WOW_FRIENDS_FILE": str(friends),
            "SITE_DOMAIN": "m.example",
            "PUBLIC_URL": "https://wow-full.m.example", "ZOGZOG_MASTER_KEY_FILE": str(master),
            "ZOGZOG_KEY_FILE": str(tmp_path / "owner-zogzog.key"), "MODEL3D": "off",
            "MODEL3D_CACHE_DIR": str(tmp_path / "cache"), "ANALYSIS_STATE_DIR": str(tmp_path / "owner-analysis")}


@pytest.fixture
def server(env, run_app):
    app = ui.App(env)
    t = ui.tenancy_of(env, app)
    r = run_app(app)
    r.srv.RequestHandlerClass = ui.make_handler(app, t)
    r.t, r.app = t, app
    yield r


def as_user(sub):
    """What the login proxy sends: the sub, and nothing the app relies on
    besides (2026-11-19: X-Forwarded-Org arrived empty)."""
    return {"X-Forwarded-User": sub}


def friend_ready(server, sub=FRIEND, slug="mira"):
    """A listed friend's first visit: in, with the list's slug."""
    h = as_user(sub)
    code, _, body = server.get("/api/me", headers=h)
    assert code == 200 and json.loads(body)["slug"] == slug, body
    return h


def leaks(body: bytes, path: str = "") -> list[bytes]:
    """A's names in an answer, the request's own words aside (a 404 that
    names the character asked for tells nothing)."""
    for v in urllib.parse.parse_qs(urllib.parse.urlsplit(path).query).values():
        body = body.replace(v[0].encode(), b"")
    body = body.replace(urllib.parse.unquote(path).encode(), b"")
    return [m for m in A_MARKERS if m in body]


ROUTES = ["/", "/start", "/start/print", "/settings", "/api/config", "/api/chars", "/api/me", "/api/me/status",
          "/api/analysis", "/api/market", "/api/prices", "/api/logsql?q=*", "/api/logsql?q=" +
          urllib.parse.quote("char:Testpriest* or *"), "/api/admin/hosts"]
PER_CHAR = ["/api/char?name={c}", "/api/journal?name={c}", "/api/overview?name={c}", "/api/timeline?name={c}",
            "/api/snapshot?name={c}&level=12", "/api/people?name={c}", "/api/loot?name={c}", "/api/gear?name={c}",
            "/api/plan?char={c}", "/api/nudge?name={c}", "/render/{c}.webp"]


def test_friend_sees_only_their_own_data(server, vl):
    h = friend_ready(server)
    seen = 0
    for path in ROUTES + [p.format(c=c) for p in PER_CHAR for c in (B_CHAR, A_CHAR)]:
        code, _, body = server.get(path, headers=h)
        assert not leaks(body, path), (path, leaks(body, path), body[:300])
        seen += code == 200
    # B's own data is there (the test isn't passing on empty answers)
    code, _, body = server.get(f"/api/char?name={B_CHAR}", headers=h)
    assert code == 200 and b"Testfriend" in body
    code, _, body = server.get(f"/api/char?name={A_CHAR}", headers=h)
    assert code == 404
    code, _, body = server.get("/api/admin/hosts", headers=h)
    assert code == 404  # the owner's only
    assert seen > 15
    # No server-side model for a friend (multi-user design section 8), nor
    # for anyone since 0.4.0: the Coach's route is gone.
    assert server.get("/api/coach", method="POST", data=b"{}", headers=h)[0] == 404


def test_owner_sees_his_own_and_never_the_friends(server):
    friend_ready(server)
    h = as_user(OWNER)
    code, _, body = server.get("/api/chars", headers=h)
    assert code == 200 and b"Testpriest" in body and b"Testfriend" not in body
    code, _, body = server.get(f"/api/char?name={B_CHAR}", headers=h)
    assert code == 404
    code, _, body = server.get("/api/admin/hosts", headers=h)
    hosts = {x["host"] for x in json.loads(body)["hosts"]}
    assert hosts == {"sam-wow.m.example", "wow.m.example", "mira-wow.m.example"}


def test_the_owner_gets_in_by_his_sub_alone(server):
    code, _, body = server.get("/api/me", headers=as_user(OWNER))
    assert code == 200 and json.loads(body)["owner"] is True


def test_a_listed_friend_gets_in_without_any_org(server, vl):
    """Listed, never reconciled, no org header: provisioned at the first
    request, with the list's slug, in their own tenant."""
    assert server.t.registry.get(FRIEND) is None
    friend_ready(server)
    u = server.t.registry.get(FRIEND)
    assert u.slug == "mira" and u.account_id >= 1 and not u.owner
    code, _, body = server.get(f"/api/char?name={B_CHAR}", headers=as_user(FRIEND))
    assert code == 200 and b"Testfriend" in body and not leaks(body)


def test_a_friend_named_like_the_owner_is_a_friend(server, capsys, monkeypatch):
    # Same display name, listed with the owner's slug: still another person
    # (identity is the sub), in without a slug. Friends never pick a name
    # (the owner sets it in the list, 2026-11-19): no route to choose one,
    # the start page says whose fix it is, and the clash is logged once.
    Path(server.t.friends_file).write_text(json.dumps({FRIEND: "mira", "Sam": "sam"}))
    h = as_user("Sam")
    for _ in range(3):
        code, _, body = server.get("/api/me", headers=h)
        me = json.loads(body)
        assert code == 200 and me["owner"] is False and me["slug"] == ""
    tenancy.reconcile(server.t)
    clashes = [json.loads(x) for x in capsys.readouterr().out.splitlines() if '"name_conflict"' in x]
    assert len(clashes) == 1 and clashes[0]["slug"] == "sam" and "taken" in clashes[0]["why"], clashes
    for slug in ("sam", "wow", "pip"):
        code, _, _ = server.get("/api/me/slug", headers={**h, "Content-Type": "application/json"},
                                method="POST", data=json.dumps({"slug": slug}).encode())
        assert code == 404
    assert server.t.registry.get("Sam").slug == ""
    code, _, body = server.get("/api/me/install-code", headers={**h, "Content-Type": "application/json"},
                               method="POST", data=b"{}")
    assert code == 400 and b"needs a fix from the person who runs this site" in body, body
    code, _, body = server.get("/api/chars", headers=h)
    assert not leaks(body)
    # `wow-stats users list` shows the owner which name to fix.
    for k, v in server.t.env.items():
        monkeypatch.setenv(k, v)
    assert tenancy.cli(["list"]) == 0
    rows = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith("{")]
    assert [r["name_conflict"] for r in rows if r["slug"] is None] == ["sam"]
    # The owner fixes the list: the next pass gives the name, nothing logged twice.
    Path(server.t.friends_file).write_text(json.dumps({FRIEND: "mira", "Sam": "samfriend"}))
    assert tenancy.reconcile(server.t)["provisioned"] == ["samfriend"]
    assert json.loads(server.get("/api/me", headers=h)[2])["slug"] == "samfriend"
    assert '"name_conflict"' not in capsys.readouterr().out


def test_unlisted_or_no_login_is_refused(server):
    for h in (as_user("someone"), {"X-Forwarded-User": "someone", "X-Forwarded-Org": FRIENDS_ORG}, {}):
        code, _, body = server.get("/api/chars", headers=h)
        assert code in (401, 403) and not leaks(body)
    assert server.t.registry.get("someone") is None
    # The page a refused friend sees: the app's style (from the login-free
    # /privacy/ assets, the only ones they can load), what to do, a way out.
    code, headers, body = server.get("/", headers=as_user("someone"))
    assert code == 403 and headers["Content-Type"].startswith("text/html")
    assert (b"You&#x27;re signed in, but the person who runs this site hasn&#x27;t let you in yet. Ask them to "
            b"add you, then reload this page.") in body
    assert b'href="/oauth2/logout"' in body and b'href="/privacy/account.css"' in body
    assert b"organisation" not in body and not leaks(body)
    # Its only script is the shared theme switch, from the same login-free place.
    assert re.findall(rb'<script src="([^"]+)"', body) == [b"/privacy/theme.js"]
    for asset in ("/privacy/base.css", "/privacy/account.css", "/privacy/theme.js"):
        assert server.get(asset, headers=as_user("someone"))[0] == 200, asset


def test_no_list_means_the_owner_only(env, run_app):
    """Without WOW_FRIENDS_FILE nobody provisions themselves: nothing else
    says who may (the org gate is gone)."""
    e = {k: v for k, v in env.items() if k != "WOW_FRIENDS_FILE"}
    t = tenancy.Tenancy(e)
    assert t.identify(OWNER).user.owner
    assert t.identify(FRIEND).status == 403 and t.registry.get(FRIEND) is None


def test_every_mcp_tool_as_the_friend(server):
    friend_ready(server)
    u = server.t.registry.get(FRIEND)
    server.t.app_for(u)
    ctx = server.t.ctx_for(u)
    assert ctx.store.account_id == u.account_id and ctx.bnet is True
    assert ctx.who == u.slug == "mira"  # said when the account has no data (tools.resolve_char)
    ran = 0
    for tool in tools.mcp_tools(ctx):
        props = tool.input_schema["properties"]
        for args in ({}, {"char": A_CHAR}, {"char": B_CHAR}):
            if any(k not in props for k in args):
                continue
            try:
                out = tools.call(ctx, tool.name, dict(args), tools.mcp_tools(ctx))
            except tools.ToolError as e:
                out = {"error": str(e)}
            except LookupError as e:
                out = {"error": str(e)}
            # The fake store ignores the char filter; the real one finds no
            # rows for A's name in B's tenant. Only the asked name may echo.
            body = json.dumps(out).encode().replace(A_CHAR.encode(), b"")
            assert not leaks(body), (tool.name, args, leaks(body), body[:300])
            ran += 1
    assert ran > 30


@pytest.fixture
def talent_catalog(monkeypatch):
    """Forever's talent tables (the test slice), so Battle.net's points make a frame."""
    import time

    from tests.test_talents import TABLES
    from wowstats import talents

    c = talents.Catalog("http://wago.invalid", "wowf", None)
    c.data, c.checked = talents.catalog_from(lambda name: iter(TABLES.get(name, []))), time.time()
    monkeypatch.setattr(talents, "_shared", c)


def test_battlenet_data_is_each_persons_own(server, vl, talent_catalog):
    """The owner asked (2026-11-20): "we only see OUR data, right?". vm-wow
    isn't split per tenant, so every read is filtered by the account_id the
    exporter puts on each series, never by the character name alone. Every
    MCP tool, as the owner and as the friend, with no char, their own and
    the other's: each vm-wow query names the caller's account only, and the
    talents, guild and Legacy points they get are their own. The dashboard
    reads through the same store methods: checked too."""
    from wowstats.mcp import classtools

    friend_ready(server)
    people = {"0": server.t.registry.get(OWNER) or server.t.identify(OWNER).user,
              "1": server.t.registry.get(FRIEND)}
    own = {"0": (A_CHAR, "Discipline", "Thrall Company"), "1": (B_CHAR, "Shadow", "Varian Company")}
    for acct, u in people.items():
        server.t.app_for(u)
        ctx = server.t.ctx_for(u)
        assert str(ctx.store.account_id) == acct and ctx.bnet is True
        mine, spec, guild = own[acct]
        other = own["1" if acct == "0" else "0"][0]
        vl.vm_queries.clear()
        ran = 0
        for tool in tools.mcp_tools(ctx):
            props = tool.input_schema["properties"]
            for args in ({}, {"char": mine}, {"char": other}):
                if any(k not in props for k in args):
                    continue
                try:
                    out = tools.call(ctx, tool.name, dict(args), tools.mcp_tools(ctx))
                except (tools.ToolError, LookupError) as e:
                    out = {"error": str(e)}
                body = json.dumps(out)
                for _, (_, _, g) in ((k, v) for k, v in own.items() if k != acct):
                    assert g not in body, (acct, tool.name, args)
                ran += 1
        assert ran > 30
        profile_qs = [q for q in vl.vm_queries if "wow_character_" in q]
        assert profile_qs, "no tool read Battle.net's talents"
        assert all(f'account_id="{acct}"' in q for q in profile_qs), [q for q in profile_qs
                                                                   if f'account_id="{acct}"' not in q][:3]
        # Their own talents, and none under the other's name (the friend's
        # name exists in the owner's account too: still nothing for the friend).
        got = tools.call(ctx, "character_summary", {"char": mine})
        assert got["talents"]["spec"] == spec, (acct, got["talents"])
        assert classtools._who(ctx, mine)["talents"]["spec"] == spec
        if acct == "1":
            assert tools.call(ctx, "character_summary", {"char": A_CHAR})["talents"] is None
            assert classtools._who(ctx, A_CHAR)["talents"] is None
        # The talent history (owner, 2026-11-20): their own build's change
        # only, through the MCP and the page's own route alike; the other
        # person's character (its name in the owner's account too) has none.
        hist = tools.call(ctx, "talent_changes", {"char": mine})
        assert hist["changes"], (acct, hist)
        assert hist["source"] == {"0": "WoWSims Exporter", "1": "Battle.net"}[acct], (acct, hist["source"])
        top = max(hist["changes"][0]["after"], key=lambda t: t["points"])
        assert top["tree"] == spec, (acct, hist["changes"])
        if acct == "1":
            assert tools.call(ctx, "talent_changes", {"char": A_CHAR})["changes"] is None
        code, _, body = server.get(f"/api/talents?name={mine}", headers=as_user(u.sub))
        page = json.loads(body)
        def same(cs):  # the build moved; read seconds apart, maybe a name from the catalog
            return [(c["before"], c["after"], [g["spell"] for g in c["gained"]]) for c in cs]

        assert code == 200 and same(page["changes"]) == same(hist["changes"]), (acct, page)
        # The dashboard, through the same store: its talent read (the card's
        # Views.bnet_talents), then guild and Legacy on the page itself.
        views = server.t.app_for(u).views
        top = max(views.bnet_talents(mine)["trees"], key=lambda t: t["points"])
        assert top["tree"] == spec, (acct, top)
        if acct == "1":
            assert views.bnet_talents(A_CHAR) is None
        h = as_user(u.sub)
        code, _, body = server.get(f"/api/char?name={mine}", headers=h)
        d = json.loads(body)
        assert code == 200 and d["header"]["guild"] == guild, d["header"]
        # Account-wide: the owner's two characters' challenges, the friend's one.
        assert d["legacy"]["earned_points"] == {"0": 2, "1": 1}[acct]
        if acct == "1":
            for c in (A_CHAR, B_CHAR):
                _, _, body = server.get(f"/api/char?name={c}", headers=h)
                assert b"Thrall Company" not in body and b"Jaina Company" not in body and b"Discipline 16" not in body


def test_mcp_over_http_scopes_and_revokes(server, env, tmp_path):
    """The FastMCP endpoint with the registry: the friend logs in through the
    provider (oidc_standin), is provisioned, sees only their tenant; an
    unlisted sub never gets a token, whatever its org or roles; a listed one
    needs neither; a switch-off ends the calls and the refreshes."""
    from tests.mcp import oauth_client, oidc_standin

    friend_ready(server)
    with oidc_standin.Standin(user=FRIEND, org=FRIENDS_ORG) as idp:
        (tmp_path / "oidc").write_text(oidc_standin.CLIENT_SECRET)
        (tmp_path / "sign").write_text("s" * 40)
        port = oauth_client.free_port()
        ep = mcpmod.endpoint({**env, "MCP_AUTH": "oauth", "MCP_PUBLIC_URL": f"http://127.0.0.1:{port}/mcp",
                              "MCP_PORT": str(port), "OIDC_ISSUER": idp.issuer,
                              "OIDC_CLIENT_ID": oidc_standin.CLIENT_ID,
                              "OIDC_CLIENT_SECRET_FILE": str(tmp_path / "oidc"),
                              "MCP_SIGNING_KEY_FILE": str(tmp_path / "sign"),
                              "MCP_STATE_DIR": str(tmp_path / "mcp-state")}, "127.0.0.1")
        ep.attach_tenancy(server.t)
        run = ep.start()
        try:
            lg = oauth_client.Login(f"http://127.0.0.1:{port}", run.url)
            lg.login()

            def call(name, args):
                r = lg.rpc("tools/call", {"name": name, "arguments": args})
                return r.status_code, json.dumps(oauth_client.result(r)).encode()

            code, body = call("character_summary", {"char": A_CHAR})
            assert code == 200 and not leaks(body.replace(A_CHAR.encode(), b"")), body[:300]
            code, body = call("character_summary", {})
            assert code == 200 and b"Testfriend" in body and not leaks(body), body[:300]
            # Unlisted, with or without the org and the role: each login
            # stops at the code exchange.
            for user, org, roles in (("friend-x", FRIENDS_ORG, ["user"]), ("friend-y", "", [])):
                idp.user = user
                idp.users[user] = {"org": org, "roles": roles, "active": True}
                other = oauth_client.Login(f"http://127.0.0.1:{port}", run.url)
                other.register()
                r = other.exchange(other.code_from(other.authorize()))
                assert r.status_code in (400, 401) and r.json()["error"] == "invalid_grant", (user, r.text)
                assert server.t.registry.get("friend-x") is None and server.t.registry.get("friend-y") is None
            # Listed, no org claim and no role: in, provisioned with the list's slug.
            idp.user = "friend-3"
            idp.users["friend-3"] = {"org": "", "roles": [], "active": True}
            pip = oauth_client.Login(f"http://127.0.0.1:{port}", run.url)
            pip.register()
            r = pip.exchange(pip.code_from(pip.authorize()))
            assert r.status_code == 200, r.text
            assert server.t.registry.get("friend-3").slug == "pip"
            # Switched off: the next call is refused (live revocation, no
            # restart), and so is the next refresh.
            server.t.registry.update(FRIEND, status="disabled")
            r = lg.rpc("tools/call", {"name": "character_summary", "arguments": {}})
            out = oauth_client.result(r)
            assert "error" in out or out["result"].get("isError"), out
            assert b"Testfriend" not in r.content
            assert lg.refresh().status_code in (400, 401)
        finally:
            run.stop()


def test_ingest_lands_in_the_senders_tenant(server, vl):
    friend_ready(server)
    u = server.t.registry.get(FRIEND)
    t = server.t
    code = t.registry.new_code(u.sub)
    got = t.registry.redeem(code)
    assert got is not None and t.registry.redeem(code) is None  # once
    _, token = got
    auth = "Basic " + base64.b64encode(f"mira:{token}".encode()).decode()
    lines = [{"_time": "2026-11-17T15:14:03Z", "feed": "wow-level", "char": B_CHAR, "level": 3,
              "password": "x", "nested": {"a": 1}, "_stream": "evil"},
             {"_time": "not a time", "feed": "wow-recorder", "char": B_CHAR}]
    data = "\n".join(json.dumps(x) for x in lines).encode()
    code, _, body = server.get("/ingest", headers={"Authorization": auth}, method="POST", data=data)
    assert code == 200, body
    assert set(vl.inserted) == {str(u.account_id)}
    a, b = vl.inserted[str(u.account_id)]
    assert "password" not in a and "_stream" not in a and a["nested"] == '{"a":1}'
    assert b["feed"] == "wow-invalid" and b["feed_sent"] == "wow-recorder"
    bad = "Basic " + base64.b64encode(f"sam:{token}".encode()).decode()  # the right token, someone else's name
    code, _, _ = server.get("/ingest", headers={"Authorization": bad}, method="POST", data=data)
    assert code == 401


def test_ingest_has_a_daily_budget_per_person(env, run_app, vl):
    """Review L7: a friend's token (or a stolen one) could POST 8 MB batches
    without end into the shared store, kept for years. Each person gets
    INGEST_DAILY_MB a day; past it, 429, and the shipper keeps its spool
    for the next day. Another person's budget is their own."""
    line = json.dumps({"_time": "2026-11-17T15:14:03Z", "feed": "wow-level", "char": B_CHAR, "level": 3,
                       "note": "x" * 300}).encode()
    e = {**env, "INGEST_DAILY_MB": str(3.5 * len(line) / 1024 / 1024)}  # three of those lines a day
    app = ui.App(e)
    t = ui.tenancy_of(e, app)
    r = run_app(app)
    r.srv.RequestHandlerClass = ui.make_handler(app, t)
    r.get("/api/me", headers=as_user(FRIEND))
    tokens = {}
    for sub, slug in ((FRIEND, "mira"), (OWNER, "sam")):
        _, tokens[slug] = t.registry.redeem(t.registry.new_code(sub))

    def send(slug):
        auth = "Basic " + base64.b64encode(f"{slug}:{tokens[slug]}".encode()).decode()
        return r.get("/ingest", headers={"Authorization": auth}, method="POST", data=line)[0]

    assert [send("mira") for _ in range(3)] == [200, 200, 200]
    assert send("mira") == 429
    assert send("sam") == 200


def test_an_upload_counts_only_once_the_store_has_it(server, vl):
    """Review 2026-11-21, D: with the store refusing the write, /ingest
    answers 502 and nothing says the person uploaded: no first event, no
    upload in the activity, no bytes off the day's budget. On the same
    connection, the next upload that lands sets all three."""
    friend_ready(server)
    t = server.t
    u = t.registry.get(FRIEND)
    _, token = t.registry.redeem(t.registry.new_code(u.sub))
    auth = "Basic " + base64.b64encode(f"mira:{token}".encode()).decode()
    data = json.dumps({"_time": "2026-11-17T15:14:03Z", "feed": "wow-level", "char": B_CHAR, "level": 3}).encode()
    vl.insert_fails = True
    code, _, _ = server.get("/ingest", headers={"Authorization": auth}, method="POST", data=data)
    assert code == 502
    assert not t.registry.get(FRIEND).first_event
    assert not t.activity.get(u).get("upload")
    assert t.ingest_budget._today("mira") == 0
    vl.insert_fails = False
    code, _, _ = server.get("/ingest", headers={"Authorization": auth}, method="POST", data=data)
    assert code == 200
    assert t.registry.get(FRIEND).first_event
    assert t.activity.get(u)["upload"]["events"] == 1
    assert t.ingest_budget._today("mira") == len(data)


def test_installers_carry_the_token_and_never_print_it(server):
    friend_ready(server)
    t = server.t
    u = t.registry.get(FRIEND)
    code = t.registry.new_code(u.sub)
    st, _, sh = server.get(f"/install/{code}", headers={"User-Agent": "curl/8.7.1"})
    assert st == 200 and sh.startswith(b"#!/bin/bash")
    assert b"shasum -a 256 -c" in sh and b"astral.sh/uv/install" not in sh  # uv pinned, checked
    token = re.search(rb'^password = "([A-Za-z0-9_-]{32,})"$', sh, re.M).group(1).decode()
    assert t.registry.check_ingest("mira", token) is not None
    assert b"echo" not in sh.split(token.encode())[0].rsplit(b"\n", 1)[-1]
    st, _, again = server.get(f"/install/{code}", headers={"User-Agent": "curl/8.7.1"})
    assert st == 404  # once
    code = t.registry.new_code(u.sub)
    st, _, ps = server.get(f"/install/{code}", headers={"User-Agent": "Mozilla/5.0 WindowsPowerShell/5.1"})
    assert st == 200 and b"Get-FileHash $uvZip" in ps and b"astral.sh/uv/install" not in ps
    assert t.registry.check_ingest("mira", token) is None  # a new code, a new token


@pytest.mark.parametrize("method,ua", [
    ("HEAD", "curl/8.7.1"),  # a link checker
    ("GET", "Slackbot-LinkExpanding 1.0 (+api.slack.com/robots)"),  # their real agents carry a URL
    ("GET", "Mozilla/5.0 (compatible; Discordbot/2.0; +discordapp.com)"),
    ("GET", "facebookexternalhit/1.1;line-poker/1.0"),  # iMessage's preview
    ("GET", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15"),
])
def test_a_link_preview_never_burns_the_install_code(server, method, ua):
    """Review L3: a code pasted in a chat is fetched by the chat's unfurler
    (or a HEAD from a link checker). That must not redeem it: the friend's
    own curl or irm right after still gets the installer."""
    friend_ready(server)
    t = server.t
    code = t.registry.new_code(FRIEND)
    st, _, body = server.get(f"/install/{code}", headers={"User-Agent": ua}, method=method)
    assert st == 200 and b"password = " not in body
    st, _, sh = server.get(f"/install/{code}", headers={"User-Agent": "curl/8.7.1"})
    assert st == 200 and re.search(rb'^password = "[A-Za-z0-9_-]{32,}"$', sh, re.M), sh[:200]


def test_bundles_are_per_person(server, env, tmp_path):
    pytest.importorskip("cryptography")
    friend_ready(server)
    t = server.t
    t.registry.update(FRIEND, public_on=True)
    users = t.users_json()["users"]
    fu = next(u for u in users if u["slug"] == "mira")
    e = publish.user_env({**env, "PUBLISH_DIR": str(tmp_path / "site")}, fu)
    key = tenancy.zogzog_key(tenancy.read_master(env), fu["account_id"], 0)
    res = publish.publish(e, force=True, zz_key=key, public=True, slug="mira",
                          canonical="https://mira-wow.m.example")
    assert res["built"]
    root = tmp_path / "site" / "mira" / "current"
    n = 0
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        body = p.read_bytes()
        if p.suffix == ".enc":
            body = publish.unseal(key, sealed.path_of(p.relative_to(root / "zogzog").as_posix()), body)
        assert not leaks(body), (p, leaks(body))
        n += 1
    assert n > 10
    assert b"Testfriend" in (root / "public" / "data" / "chars.json").read_bytes()
    # The wizard's link opens this bundle: same key.
    assert t.zogzog_link(t.registry.get(FRIEND)).endswith("#" + tenancy.b64u(key))


def test_static_server_maps_hosts_to_slugs(tmp_path):
    from wowstats import static

    for slug in ("sam", "mira"):
        d = tmp_path / slug / "b1" / "public"
        d.mkdir(parents=True)
        (d / "index.html").write_text(slug)
        (tmp_path / slug / "current").symlink_to("b1")
    s = static.Site({"SITE_DIR": str(tmp_path), "SITE_DOMAIN": "m.example", "SITE_ALIASES": "wow.m.example=sam"})
    assert s.resolve("/", "wow.m.example")[0].read_text() == "sam"
    assert s.resolve("/", "sam-wow.m.example:443")[0].read_text() == "sam"
    assert s.resolve("/", "mira-wow.m.example")[0].read_text() == "mira"
    assert s.resolve("/", "other-wow.m.example") is None
    assert s.resolve("/", "evil.example") is None
    assert s.resolve("/../sam/current/public/index.html", "mira-wow.m.example") is None


def test_delete_removes_the_tenant(server, vl):
    friend_ready(server)
    t = server.t
    u = t.registry.get(FRIEND)
    with pytest.raises(tenancy.TenancyError):
        tenancy.delete_tenant(t, t.registry.get(OWNER))
    out = tenancy.delete_tenant(t, u)
    assert vl.deleted == [(str(u.account_id), "filter=%2A")]
    assert t.registry.get(FRIEND) is None and out["slug"] == "mira"
    # A new friend never gets the deleted one's AccountID back.
    h = as_user("friend-3")
    server.get("/api/me", headers=h)
    assert t.registry.get("friend-3").account_id > u.account_id


def test_screen_matches_the_vrl():
    ev = ingest.screen({"_time": "2026-11-17T15:14:03Z", "feed": "wow-level", "Bad Key": 1, "host": "x",
                        "battletag": "x", "email_x": "y", "ok_field": [1, 2], "_msg": "m" * 5000})
    assert ev == {"_time": "2026-11-17T15:14:03Z", "feed": "wow-level", "ok_field": "[1,2]", "component": "wow",
                  "_msg": "m" * 4096}
    # The payload never picks it (the VRL sets it after the filter).
    assert ingest.screen({"_time": "2026-11-17T15:14:03Z", "feed": "wow-level", "component": "x"})["component"] == "wow"
    assert ingest.screen({"_time": "2026-11-17T15:14:03Z", "feed": "WOW-x"}) is None
    old = ingest.screen({"_time": "1999-01-01T00:00:00Z", "feed": "wow-level"})
    assert old["feed"] == "wow-invalid" and old["ingest_error"] == "_time out of range"


def test_slug_rules(tmp_path):
    r = tenancy.Registry(tmp_path / "u.json")
    r.ensure("a")
    for bad in ("a", "full", "x" * 25, "-ab", "ab-", "a--b", "a-b-c", "a b", "été"):
        with pytest.raises(tenancy.TenancyError):
            r.set_slug("a", bad)
    assert r.set_slug("a", "frost42").slug == "frost42"
    r.ensure("c")
    assert r.set_slug("c", "frost-3f9a").slug == "frost-3f9a"  # a derived slug's shape
    r.ensure("b")
    with pytest.raises(tenancy.TenancyError, match="taken"):
        r.set_slug("b", "frost42")
    with pytest.raises(tenancy.TenancyError, match="can't change"):
        r.set_slug("a", "other")


def test_account_pages_render():
    live = account.start_page(base="https://wow-full.m.example")
    assert b'data-widget="install"' in live
    for state in (b"new", b"wait", b"none", b"ok"):  # one hero each; account.js shows one
        assert b'class="hero" data-in="' + state + b'"' in live
    assert b"/install/update | bash" in live and b'href="/start/print"' in live
    p = account.start_page(printable=True)
    assert b"data-widget" not in p and b"Polestar Recorder" in p
    # The owner sets each friend's name in the friends list: no step asks for one.
    assert b"Pick your name" not in p and b"address is already set" in p


def test_resolve_tenant_for_the_mcp_port(env):
    """The MCP admits by the web view's rule: the sub, the owner or listed."""
    t = tenancy.Tenancy(env)
    u = t.resolve_tenant(FRIEND)  # first login: provisioned
    assert u is not None and u.account_id >= 1 and not u.owner and u.slug == "mira"
    assert t.resolve_tenant(FRIEND).account_id == u.account_id  # same tenant again
    owner = t.resolve_tenant(OWNER)
    assert owner.owner and owner.account_id == 0
    assert t.resolve_tenant("new-one") is None
    assert t.registry.get("new-one") is None  # unlisted: nothing provisioned
    t.registry.update(FRIEND, status="disabled")
    assert t.resolve_tenant(FRIEND) is None
    assert t.resolve_tenant("") is None


def test_install_steps_are_complete_and_actionable():
    """docs/videos/steps.json feeds /start and /help/: stable ids, and every
    step says how to check it and what to do when it didn't work."""
    s = account.steps()
    assert [st["id"] for st in s["steps"]] == ["login", "addons", "install", "play", "see", "ai"]
    for st in s["steps"]:
        assert st["check"] and st["if_not"] and st["audience"] in ("start", "both"), st["id"]
    assert {t["id"] for t in s["troubleshooting"]} >= {"no-data", "no-folder", "antivirus", "client-not-registered"}
    assert all(t["fix"] for t in s["troubleshooting"])
    ids = {a["id"] for a in s["addons"]}
    assert {"polestar", "recorder"} <= {a["id"] for a in s["addons"] if a["required"]}
    assert all(a["dir"] and isinstance(a["feeds"], list) for a in s["addons"])
    for k in s["data"]:  # the missing-data kinds name known addons and say what to do
        assert set(k["addons"]) <= ids and k["signals"] and k["what"] and k["fix"] and k["sections"], k
    for a in s["addons"]:
        assert a["url"] is None or a["url"].startswith("https://"), a
    inst = next(st for st in s["steps"] if st["id"] == "install")
    assert "{code}" in inst["os"]["mac"]["command"] and "{code}" in inst["os"]["windows"]["command"]
    # Friends don't choose their page's name: the owner's friends list gives it.
    assert not re.search(r"(?i)\bpick (your|a) (name|handle)", json.dumps(s)), "a step asks the friend for a name"


def test_update_keeps_the_password_and_reset_revokes_it(server):
    h = friend_ready(server)
    t = server.t
    u = t.registry.get(FRIEND)
    _, token = t.registry.redeem(t.registry.new_code(u.sub))
    t.registry.update(u.sub, ingest_set_at="2026-11-18T00:00:00+00:00")
    curl = {"User-Agent": "curl/8.7.1"}
    st, _, boot = server.get("/install/update", headers=curl)
    assert st == 200 and b"--config -" in boot and token.encode() not in boot  # public, the same for everyone
    st, _, ps = server.get("/install/update", headers={"User-Agent": "WindowsPowerShell/5.1"})
    assert st == 200 and b"/install/update/payload" in ps
    good = "Basic " + base64.b64encode(f"mira:{token}".encode()).decode()
    st, _, sh = server.get("/install/update/payload", headers={**curl, "Authorization": good})
    assert st == 200 and b"wow-stats is updated" in sh
    assert token.encode() not in sh and not re.search(rb'^password = "[^"]+"', sh, re.M)
    assert b'> "$conf"' not in sh  # the config is never rewritten
    st, _, _ = server.get("/install/update/payload", headers={**curl, "Authorization": "Basic " +
                                                             base64.b64encode(b"mira:wrong").decode()})
    assert st == 401
    assert t.registry.check_ingest("mira", token) is not None  # an update changes nothing
    j = {**h, "Content-Type": "application/json"}
    st, _, body = server.get("/api/me/install-code", headers=j, method="POST", data=b"{}")
    assert st == 400 and b"update" in body  # installed: no code without a reset
    st, _, body = server.get("/api/me/install-code", headers=j, method="POST", data=b'{"reset": true}')
    assert st == 200 and b"/install/" in body
    assert t.registry.check_ingest("mira", token) is None  # the reset revoked it at once
    _, _, me = server.get("/api/me", headers=h)
    assert json.loads(me)["update"]["mac"].endswith("/install/update | bash")


LOGIN_FREE = ["/privacy", "/install/update", "/install/abcdefghij", "/healthz"]


def test_login_free_pages_load_nothing_behind_the_login(server):
    """The lab routes /privacy and /install/ around the login: whatever those
    pages load must be reachable there too (an anonymous reader gets 401/403
    from everything else)."""
    friend_ready(server)
    seen, todo = set(), list(LOGIN_FREE)
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        for ua in ("Mozilla/5.0", "curl/8.7.1", "WindowsPowerShell/5.1"):
            st, hdr, body = server.get(path, headers={"User-Agent": ua})
            if path == "/install/abcdefghij":
                # a bad code: a script-friendly refusal for curl and irm, the
                # "run it in a terminal" note for anything else; nothing to load
                assert st == (200 if ua == "Mozilla/5.0" else 404), (ua, st)
                continue
            assert st == 200, (path, ua, st)
            text = body.decode("utf-8", "replace")
            refs = re.findall(r'(?:src|href)="(/[^"]*)"', text) + re.findall(r'url\("?(/[^")]+)"?\)', text)
            if hdr["Content-Type"].startswith("text/css"):  # base.css names its fonts relative to itself
                refs += [urllib.parse.urljoin(path, u) for u in re.findall(r'url\("?([a-z][^":)]+)"?\)', text)]
            refs += [u.split("/install/", 1)[1] and "/install/" + u.split("/install/", 1)[1]
                     for u in re.findall(r"\$base(/install/[\w/]+)", text)]
            for ref in refs:
                ref = ref.split("#")[0].split("?")[0]
                if ref and ref.startswith("/") and ref not in ("/", "/start", "/settings", "/privacy"):
                    assert ref.startswith(("/privacy/", "/install/")), (path, "loads", ref)
                    if ref != "/install/update/payload":  # basic auth: the install's own password
                        todo.append(ref)
    assert {"/privacy/base.css", "/privacy/account.css"} <= seen and any(s.startswith("/privacy/fonts/") for s in seen)
    # the payload answers to the install's own password, not to a login
    st, _, _ = server.get("/install/update/payload", headers={"User-Agent": "curl/8.7.1"})
    assert st == 401


def test_the_wizard_works_with_the_store_down(env, run_app):
    env = {**env, "FRIENDS_VL_URL": "http://127.0.0.1:9", "VL_URL": "http://127.0.0.1:9", "VM_URL": ""}
    app = ui.App(env)
    t = ui.tenancy_of(env, app)
    r = run_app(app)
    r.srv.RequestHandlerClass = ui.make_handler(app, t)
    h = as_user(FRIEND)
    st, _, body = r.get("/api/me", headers=h)
    me = json.loads(body)
    assert st == 200 and me["chars"] == [] and me["update"]["mac"].endswith("/install/update | bash")
    st, _, _ = r.get("/start", headers=h)
    assert st == 200


# ---------------------------------------------------------------- the friends list (WOW_FRIENDS_FILE)


@pytest.fixture
def listed(env, run_app, tmp_path):
    """The full view with a friends list: FRIEND as mira, friend-3 as pip."""
    f = tmp_path / "friends.json"
    f.write_text(json.dumps({FRIEND: "mira", "friend-3": "pip"}))
    e = {**env, "WOW_FRIENDS_FILE": str(f)}
    app = ui.App(e)
    t = ui.tenancy_of(e, app)
    r = run_app(app)
    r.srv.RequestHandlerClass = ui.make_handler(app, t)
    r.t, r.app, r.file = t, app, f
    yield r


def ingest_as(server, slug, token):
    auth = "Basic " + base64.b64encode(f"{slug}:{token}".encode()).decode()
    line = json.dumps({"_time": "2026-11-17T15:14:03Z", "feed": "wow-level", "char": B_CHAR, "level": 3})
    return server.get("/ingest", headers={"Authorization": auth}, method="POST", data=line.encode())[0]


def test_listed_friends_get_a_tenant_and_nobody_else(listed, vl):
    t = listed.t
    out = tenancy.reconcile(t)
    assert sorted(out["provisioned"]) == ["mira", "pip"] and not out["removed"]
    mira, pip = t.registry.get(FRIEND), t.registry.get("friend-3")
    assert (mira.slug, pip.slug) == ("mira", "pip") and 0 < mira.account_id != pip.account_id
    code, _, body = listed.get("/api/me", headers=as_user(FRIEND))
    assert code == 200 and json.loads(body)["slug"] == "mira"  # ready before the first login
    # Same org, valid login, not on the list: no tenant, nothing written.
    code, _, _ = listed.get("/api/me", headers=as_user("stranger"))
    assert code == 403 and t.registry.get("stranger") is None
    assert t.resolve_tenant("stranger") is None and t.registry.get("stranger") is None
    assert tenancy.reconcile(t)["provisioned"] == []  # a second pass changes nothing
    assert vl.deleted == []


DAY = 86400


def removed_mira(listed, vl):
    """mira with a shipper, events and state, then taken off the list at
    t0: (t0, mira, her token, pip's token)."""
    t = listed.t
    tenancy.reconcile(t)
    mira = t.registry.get(FRIEND)
    _, token = t.registry.redeem(t.registry.new_code(FRIEND))
    _, pip_token = t.registry.redeem(t.registry.new_code("friend-3"))
    assert ingest_as(listed, "mira", token) == 200
    (t.user_dir(mira) / "analysis").mkdir(parents=True, exist_ok=True)
    listed.file.write_text(json.dumps({"friend-3": "pip"}))
    t0 = time.time()
    out = tenancy.reconcile(t, now=t0)
    assert out["removed"] == ["mira"] and out["purged"] == [] and not out["errors"]
    return t0, mira, token, pip_token


def test_a_friend_who_leaves_the_list_loses_every_access_at_once(listed, vl):
    """Owner, 2026-11-22: removal revokes now (web, MCP, uploads, install
    codes, pages, sealed links) and deletes nothing yet."""
    t = listed.t
    tenancy.reconcile(t)
    code = t.registry.new_code(FRIEND)  # a live install code, taken before the removal
    t0, mira, token, pip_token = removed_mira(listed, vl)
    assert ingest_as(listed, "mira", token) == 401
    assert listed.get("/api/me", headers=as_user(FRIEND))[0] == 403
    assert listed.get("/", headers=as_user(FRIEND))[0] == 403
    assert t.mcp_user(FRIEND) is None and t.resolve_tenant(FRIEND) is None
    assert t.registry.redeem(code) is None
    assert listed.get(f"/install/{t.registry.new_code(FRIEND)}", headers={"User-Agent": "curl/8.7.1"})[0] == 404
    # The publisher and the live public view drop the site and the /zogzog/ bundle.
    line = next(u for u in t.users_json()["users"] if u["slug"] == "mira")
    assert line["status"] == "removed" and not PageSettings.of({**line, "public_on": True}).public
    assert [h["active"] for h in t.host_list() if h["slug"] == "mira"] == [False]
    assert t.app_for(mira) not in ui.prebuilt_apps(listed.app, t)
    # Nothing deleted: the events, the state folder and the record stay.
    assert vl.deleted == [] and t.user_dir(mira).exists()
    assert t.registry.get(FRIEND).removed_at and t.registry.get(FRIEND).account_id == mira.account_id
    # The other friend and the owner are untouched.
    assert t.registry.get("friend-3").slug == "pip" and ingest_as(listed, "pip", pip_token) == 200
    assert t.registry.get(OWNER).owner and listed.get("/api/me", headers=as_user(OWNER))[0] == 200


def test_the_site_goes_between_two_builds(tmp_path):
    """A removed friend's page and /zogzog/ link don't wait for the next
    build (PUBLISH_INTERVAL): drop_inactive takes them down within REVOKE_S."""
    from wowstats import static

    for slug in ("sam", "mira", "pip"):
        (tmp_path / "site" / slug / "b1" / "public").mkdir(parents=True)
        (tmp_path / "site" / slug / "current").symlink_to("b1")
        (tmp_path / "out" / slug).mkdir(parents=True)
    (tmp_path / "token").write_text("t" * 32)
    site = static.Site({"SITE_DIR": str(tmp_path / "site"), "SITE_DOMAIN": "m.example", "UPLOAD_TOKEN_FILE":
                        str(tmp_path / "token")})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), static.make_upload_handler(site))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    users = [{"slug": "sam", "status": "active"}, {"slug": "mira", "status": "removed"},
             {"slug": "pip", "status": "active"}]
    env = {"PUBLISH_PUSH_URL": f"http://127.0.0.1:{srv.server_address[1]}",
           "PUBLISH_PUSH_TOKEN_FILE": str(tmp_path / "token"), "PUBLISH_DIR": str(tmp_path / "out")}
    slept = []
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(publish, "fetch_users", lambda e: users)
            publish.wait(env, 150, sleep=slept.append)
            assert slept == [60, 60, 30]
            # The share port down: nobody's page goes for it.
            mp.setattr(publish, "fetch_users", lambda e: (_ for _ in ()).throw(OSError("down")))
            publish.drop_inactive(env)
    finally:
        srv.shutdown()
        srv.server_close()
    assert sorted(d.name for d in (tmp_path / "site").iterdir()) == ["pip", "sam"]
    assert sorted(d.name for d in (tmp_path / "out").iterdir()) == ["pip", "sam"]


def test_data_stays_through_day_6_and_coming_back_restores_it(listed, vl):
    t = listed.t
    t0, mira, token, _ = removed_mira(listed, vl)
    removed_at = t.registry.get(FRIEND).removed_at
    for day in (1, 3, 6.9):
        out = tenancy.reconcile(t, now=t0 + day * DAY)
        assert out["removed"] == [] and out["purged"] == []
    assert vl.deleted == [] and t.user_dir(mira).exists()
    assert t.registry.get(FRIEND).removed_at == removed_at  # passes never move the clock
    # Listed again on day 6: back with the same tenant, slug and upload password.
    listed.file.write_text(json.dumps({FRIEND: "mira", "friend-3": "pip"}))
    out = tenancy.reconcile(t, now=t0 + 6 * DAY)
    assert out["restored"] == ["mira"] and out["provisioned"] == []
    back = t.registry.get(FRIEND)
    assert (back.status, back.removed_at, back.account_id, back.slug) == ("active", "", mira.account_id, "mira")
    assert listed.get("/api/me", headers=as_user(FRIEND))[0] == 200
    assert ingest_as(listed, "mira", token) == 200 and t.mcp_user(FRIEND) is not None
    assert t.user_dir(mira).exists() and vl.deleted == []
    # Long after the window: nothing purges someone who is back.
    assert tenancy.reconcile(t, now=t0 + 30 * DAY)["purged"] == []


def test_coming_back_at_the_login_restores_too(listed, vl):
    """Listed again and logging in before the next pass: identify restores."""
    t = listed.t
    removed_mira(listed, vl)
    listed.file.write_text(json.dumps({FRIEND: "mira", "friend-3": "pip"}))
    code, _, body = listed.get("/api/me", headers=as_user(FRIEND))
    assert code == 200 and json.loads(body)["slug"] == "mira"
    assert t.registry.get(FRIEND).removed_at == "" and vl.deleted == []


def test_a_switched_off_friend_stays_off_when_listed_again(listed, vl):
    t = listed.t
    tenancy.reconcile(t)
    t.registry.update(FRIEND, status="disabled")  # the operator's switch
    listed.file.write_text(json.dumps({"friend-3": "pip"}))
    tenancy.reconcile(t)
    assert t.registry.get(FRIEND).status == "disabled" and t.registry.get(FRIEND).removed_at
    listed.file.write_text(json.dumps({FRIEND: "mira", "friend-3": "pip"}))
    tenancy.reconcile(t)
    assert t.registry.get(FRIEND).status == "disabled" and t.registry.get(FRIEND).removed_at == ""


def test_the_purge_runs_after_the_window_across_a_restart_once(listed, env, vl, capsys):
    t = listed.t
    t0, mira, token, _ = removed_mira(listed, vl)
    # A restart: a new process reads the clock from users.json.
    e = {**env, "WOW_FRIENDS_FILE": str(listed.file)}
    t2 = tenancy.Tenancy(e)
    assert tenancy.reconcile(t2, now=t0 + 6 * DAY)["purged"] == [] and vl.deleted == []
    # VictoriaLogs refuses on day 7: nothing else goes, the next pass retries.
    t2.friends_vl = "http://127.0.0.1:9"
    out = tenancy.reconcile(t2, now=t0 + 7 * DAY)
    assert out["errors"] == ["mira"] and t2.registry.get(FRIEND) is not None and t2.user_dir(mira).exists()
    t3 = tenancy.Tenancy(e)
    out = tenancy.reconcile(t3, now=t0 + 7 * DAY + 60)
    assert out["purged"] == ["mira"] and not out["errors"]
    assert vl.deleted == [(str(mira.account_id), "filter=%2A")]  # their tenant, and only theirs
    assert t3.registry.get(FRIEND) is None and not t3.user_dir(mira).exists()
    # Idempotent: later passes (and the first process's) find nothing to do.
    assert tenancy.reconcile(t3, now=t0 + 8 * DAY)["purged"] == []
    assert tenancy.reconcile(t, now=t0 + 9 * DAY)["purged"] == []
    assert len(vl.deleted) == 1 and ingest_as(listed, "mira", token) == 401
    logs = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith('{"')]
    assert [x["reason"] for x in logs if x.get("action") == "purged"] == ["window"]
    # Listed again after the purge: a new, empty account, never the old tenant.
    listed.file.write_text(json.dumps({FRIEND: "mira", "friend-3": "pip"}))
    tenancy.reconcile(t3, now=t0 + 10 * DAY)
    assert t3.registry.get(FRIEND).account_id > mira.account_id


def test_purge_days_0_deletes_at_once(listed, env, vl):
    e = {**env, "WOW_FRIENDS_FILE": str(listed.file), "FRIENDS_PURGE_DAYS": "0"}
    t = tenancy.Tenancy(e)
    tenancy.reconcile(t)
    mira = t.registry.get(FRIEND)
    listed.file.write_text(json.dumps({"friend-3": "pip"}))
    out = tenancy.reconcile(t)
    assert out["removed"] == ["mira"] and out["purged"] == ["mira"]
    assert vl.deleted == [(str(mira.account_id), "filter=%2A")] and t.registry.get(FRIEND) is None


@pytest.mark.parametrize("v", ["-1", "seven", "nan"])
def test_purge_days_must_be_a_number(env, v):
    with pytest.raises(SystemExit, match="FRIENDS_PURGE_DAYS"):
        tenancy.Tenancy({**env, "FRIENDS_PURGE_DAYS": v})


def test_purge_now_skips_the_window(listed, vl, monkeypatch, capsys):
    """A friend who asks for their data gone now: `users delete <slug> --yes`."""
    t = listed.t
    t0, mira, _, _ = removed_mira(listed, vl)
    for k, v in t.env.items():
        monkeypatch.setenv(k, v)
    assert tenancy.cli(["list"]) == 0
    rows = {r["slug"]: r for r in (json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith("{"))}
    assert rows["mira"]["status"] == "removed" and rows["mira"]["purge_at"] > rows["mira"]["removed_at"]
    assert rows["pip"]["purge_at"] is None
    assert tenancy.cli(["delete", "mira"]) == 0 and vl.deleted == []  # the dry run
    assert tenancy.cli(["delete", "mira", "--yes"]) == 0
    assert vl.deleted == [(str(mira.account_id), "filter=%2A")] and t.registry.get(FRIEND) is None
    assert '"reason": "operator"' in capsys.readouterr().out
    assert tenancy.reconcile(t, now=t0 + 8 * DAY)["purged"] == []  # nothing left for the window


@pytest.mark.parametrize("content", ["{}", "{}\n", "[]", json.dumps({OWNER: "sam"}), json.dumps([OWNER])])
def test_a_list_that_removes_everyone_is_refused(listed, vl, capsys, content):
    """A valid list naming nobody (or only the owner) while friends are in
    would remove all of them at once: refused, logged once, nothing changes,
    everyone keeps their access. The old rule offboarded them all."""
    t = listed.t
    watch = tenancy.FriendsWatch(t, every=120)
    watch.step(now=0)
    listed.file.write_text(content)
    assert watch.step(now=1) == {"skipped": True, "refused": "empty"}
    assert watch.step(now=300) == {"skipped": True, "refused": "empty"}
    logs = [json.loads(x) for x in capsys.readouterr().out.splitlines() if '"empty_list_refused"' in x]
    assert len(logs) == 1 and logs[0]["level"] == "error" and logs[0]["friends"] == 2
    assert all(not u.removed_at for u in t.registry.users()) and vl.deleted == []
    assert listed.get("/api/me", headers=as_user(FRIEND))[0] == 200
    assert listed.get("/api/me", headers=as_user("friend-3"))[0] == 200
    owner = t.registry.get(OWNER)
    assert owner.owner and owner.active


def test_the_flag_lets_an_empty_list_through(listed, env, vl, monkeypatch, capsys):
    t = listed.t
    tenancy.reconcile(t)
    listed.file.write_text("{}")
    assert tenancy.reconcile(t)["refused"] == "empty"
    # The CLI's one-off flag.
    for k, v in t.env.items():
        monkeypatch.setenv(k, v)
    assert tenancy.cli(["reconcile"]) == 1 and "--allow-empty" in capsys.readouterr().err
    assert tenancy.cli(["reconcile", "--allow-empty"]) == 0
    assert sorted(u.status for u in t.registry.users() if not u.owner) == ["removed", "removed"]
    assert vl.deleted == []  # still the window: removed, not deleted
    # Everyone already out: an empty list is no longer "removing everyone".
    assert tenancy.reconcile(t)["skipped"] is False
    assert listed.get("/api/me", headers=as_user(FRIEND))[0] == 403


def test_the_env_flag_lets_an_empty_list_through(listed, env, vl):
    e = {**env, "WOW_FRIENDS_FILE": str(listed.file), "FRIENDS_ALLOW_EMPTY": "1"}
    t = tenancy.Tenancy(e)
    tenancy.reconcile(t)
    listed.file.write_text("{}")
    assert sorted(tenancy.reconcile(t)["removed"]) == ["mira", "pip"]


def test_removing_some_but_not_all_is_not_refused(listed, vl):
    """The guard is for "everyone at once" only: the window covers the rest."""
    t = listed.t
    tenancy.reconcile(t)
    listed.file.write_text(json.dumps({"friend-9": "nox"}))  # both old friends out, a new one in
    out = tenancy.reconcile(t)
    assert sorted(out["removed"]) == ["mira", "pip"] and out["provisioned"] == ["nox"]


@pytest.mark.parametrize("content", [None, "", "not json", "null", '{"x": "Bad Slug"}', '{"x": 1}', "[1]",
                                     '[""]', '"x"'])
def test_an_unusable_list_changes_nothing(listed, vl, content):
    t = listed.t
    tenancy.reconcile(t)
    before = [u.sub for u in t.registry.users()]
    if content is None:
        listed.file.unlink()
    else:
        listed.file.write_text(content)
    assert tenancy.reconcile(t) == {"skipped": True}
    assert [u.sub for u in t.registry.users()] == before and vl.deleted == []
    assert all(u.active for u in t.registry.users())


def test_the_owner_is_never_in_play(listed, vl):
    t = listed.t
    assert OWNER not in json.loads(listed.file.read_text())  # absent from the list
    tenancy.reconcile(t)
    owner = t.registry.get(OWNER)
    assert owner.owner and owner.account_id == 0 and owner.active
    listed.file.write_text(json.dumps({OWNER: "other", "friend-3": "pip"}))  # listed by mistake: ignored
    tenancy.reconcile(t)
    owner = t.registry.get(OWNER)
    assert owner.owner and owner.slug == "sam" and owner.active
    assert ("0", "filter=%2A") not in vl.deleted


def test_a_changed_list_applies_without_a_restart(listed, vl, capsys):
    """The watch reconciles as soon as the content changes, not at the next
    full pass, and the prebuilt answers list the new friend at once: no
    restart of the full view (2026-11-19: the host rolled the pod for a new
    friend, and the site was down for a minute)."""
    t, woken = listed.t, []
    watch = tenancy.FriendsWatch(t, every=120, on_change=lambda: woken.append(1))
    assert sorted(watch.step(now=0)["provisioned"]) == ["mira", "pip"] and woken == [1]
    assert watch.step(now=5) is None  # same content, inside the full pass: nothing read twice
    listed.file.write_text(json.dumps({FRIEND: "mira", "friend-3": "pip", "friend-4": "nox"}))
    out = watch.step(now=6)
    assert out["provisioned"] == ["nox"] and woken == [1, 1]
    nox = t.registry.get("friend-4")
    assert nox.slug == "nox" and nox.active
    assert listed.get("/api/me", headers=as_user("friend-4"))[0] == 200
    assert t.app_for(nox) in ui.prebuilt_apps(listed.app, t)
    assert '"action": "list_changed"' in capsys.readouterr().out
    # Someone leaves: removed at the next look, and gone from the builder.
    listed.file.write_text(json.dumps({FRIEND: "mira", "friend-3": "pip"}))
    assert watch.step(now=7)["removed"] == ["nox"] and woken == [1, 1, 1]
    assert t.registry.get("friend-4").status == "removed"
    assert len(ui.prebuilt_apps(listed.app, t)) == 3  # the owner, mira, pip
    # A full pass comes anyway (a failed purge's retry), changing nothing.
    assert watch.step(now=200) == {"skipped": False, "provisioned": [], "restored": [], "removed": [],
                                   "purged": [], "errors": []}
    assert woken == [1, 1, 1]


@pytest.mark.parametrize("content", [None, "", "{not json", '{"x": "Bad Slug"}'])
def test_a_broken_list_keeps_the_last_good_one(listed, vl, capsys, content):
    """A render that fails mid-way must never read as "everyone left": the
    friends stay, one log line says why, and the next good list applies."""
    t = listed.t
    watch = tenancy.FriendsWatch(t, every=120)
    watch.step(now=0)
    before = sorted(u.sub for u in t.registry.users())
    capsys.readouterr()
    if content is None:
        listed.file.unlink()
    else:
        listed.file.write_text(content)
    assert watch.step(now=1) == {"skipped": True}
    assert watch.step(now=2) is None and watch.step(now=300) == {"skipped": True}
    assert capsys.readouterr().out.count('"action": "list_unusable"') == 1
    assert sorted(u.sub for u in t.registry.users()) == before and vl.deleted == []
    assert listed.get("/api/me", headers=as_user(FRIEND))[0] == 200
    listed.file.write_text(json.dumps({FRIEND: "mira"}))
    assert watch.step(now=301)["removed"] == ["pip"]


def test_the_publisher_clears_every_static_pod(tmp_path):
    from wowstats import static

    for slug in ("sam", "mira"):
        (tmp_path / "site" / slug / "b1" / "public").mkdir(parents=True)
        (tmp_path / "site" / slug / "current").symlink_to("b1")
    (tmp_path / "token").write_text("t" * 32)
    site = static.Site({"SITE_DIR": str(tmp_path / "site"), "SITE_DOMAIN": "m.example", "UPLOAD_TOKEN_FILE":
                        str(tmp_path / "token")})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), static.make_upload_handler(site))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        env = {"PUBLISH_PUSH_URL": f"http://127.0.0.1:{srv.server_address[1]}",
               "PUBLISH_PUSH_TOKEN_FILE": str(tmp_path / "token")}
        publish.unpush_others(env, {"sam"})
    finally:
        srv.shutdown()
        srv.server_close()
    assert sorted(d.name for d in (tmp_path / "site").iterdir()) == ["sam"]


# ---------------------------------------------------------------- the owner's /zogzog/ link


def test_a_new_link_rotates_the_owners_key_too(server, env, tmp_path):
    """"Make a new link" gave the owner the same link back: his key came from
    ZOGZOG_KEY_FILE whatever the generation (outside review, 2026-11-19).
    The file only seeds generation 0; from the first rotation his key is
    derived like a friend's, so the full view and the publisher agree on it
    without sharing a file."""
    (tmp_path / "owner-zogzog.key").write_text(tenancy.b64u(b"o" * 32) + "\n")
    t = server.t
    h = {**as_user(OWNER), "Content-Type": "application/json"}
    assert server.get("/api/me", headers=as_user(OWNER))[0] == 200
    first = t.zogzog_link(t.registry.get(OWNER))
    assert first.endswith("#" + tenancy.b64u(b"o" * 32))
    code, _, body = server.get("/api/me/settings", headers=h, method="POST",
                               data=json.dumps({"new_zogzog_link": True}).encode())
    assert code == 200, body
    owner = t.registry.get(OWNER)
    second = t.zogzog_link(owner)
    assert second and second != first and owner.zogzog_gen == 1
    # The publisher seals the owner's bundle under the key the link carries.
    u = next(x for x in t.users_json()["users"] if x["owner"])
    key = publish.user_key(env, u)
    assert second.endswith("#" + tenancy.b64u(key))
    # And a friend's key is never the owner's.
    friend_ready(server)
    fu = next(x for x in t.users_json()["users"] if x["slug"] == "mira")
    assert publish.user_key(env, fu) != key


def test_no_link_or_key_reaches_a_log(tmp_path, capsys, monkeypatch):
    """The first key and a rotated one are written to their file; the
    publisher's output names the file and never the key or the link."""
    kf = tmp_path / "zogzog.key"
    monkeypatch.setattr(publish, "publish", lambda env, force=False, **k: {"built": True})
    publish.rotate({"PUBLISH_DIR": str(tmp_path / "site"), "ZOGZOG_KEY_FILE": str(kf),
                    "ZOGZOG_LINK_BASE": "https://wow.m.example"})
    key = kf.read_text().strip()
    out = capsys.readouterr()
    assert key not in out.out + out.err and "#" not in out.out + out.err
    assert str(kf) in out.out


@pytest.mark.parametrize(("rng", "code", "body", "crange"), [
    (None, 200, b"0123456789", None),
    ("bytes=0-1", 206, b"01", "bytes 0-1/10"),
    ("bytes=7-", 206, b"789", "bytes 7-9/10"),
    ("bytes=-2", 206, b"89", "bytes 8-9/10"),
    ("bytes=4-99", 206, b"456789", "bytes 4-9/10"),
    ("bytes=12-", 416, b"", "bytes */10"),
    ("bytes=0-1,4-5", 200, b"0123456789", None),  # several ranges: the whole file
])
def test_a_clip_answers_byte_ranges(rng, code, body, crange):
    """Safari plays a <video> only from a server that answers Range with 206."""
    sent = {}

    class H:
        headers = {"Range": rng} if rng else {}

        def send(self, c, b, ctype, cache, headers=None):
            sent.update(code=c, body=b, headers=headers or {})

    account.send_media(H(), b"0123456789", "video/mp4")
    assert (sent["code"], sent["body"]) == (code, body)
    assert sent["headers"].get("Content-Range") == crange
    assert sent["headers"]["Accept-Ranges"] == "bytes"


def test_full_view_reads_each_persons_render_from_their_own_site(env, run_app, tmp_path):
    """The full view has no renderer: /render/<char>.webp (the publisher's
    still of the 3D model) comes from the static server (RENDER_FROM),
    which answers by Host. The owner's comes
    from his public bundle (wow.m), a friend whose page isn't public from
    their sealed /zogzog/ copy. Before 2026-11-19 the request named no host:
    a 404 for everyone, and the paperdoll kept its silhouette."""
    pytest.importorskip("cryptography")
    from wowstats import static

    png = b"RIFF\x00\x00\x00\x00WEBPVP8L" + b"x" * 64  # our still: a WebP
    site_dir = tmp_path / "site"
    own = site_dir / "sam" / "b1" / "public" / "data" / "render"
    own.mkdir(parents=True)
    (own / f"{A_CHAR}.webp").write_bytes(png)
    (site_dir / "sam" / "current").symlink_to("b1")
    s = static.Site({"SITE_DIR": str(site_dir), "ZOGZOG_PATH": "/zogzog/", "SITE_DOMAIN": "m.example",
                     "SITE_ALIASES": "wow.m.example=sam"})
    srv = static.Server(("127.0.0.1", 0), static.make_handler(s))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        e = {**env, "RENDER_FROM": f"http://127.0.0.1:{srv.server_address[1]}"}
        app = ui.App(e)
        t = ui.tenancy_of(e, app)
        r = run_app(app)
        r.srv.RequestHandlerClass = ui.make_handler(app, t)
        r.t = t
        h = friend_ready(r)
        # The friend's bundle: /zogzog/ only (their page is off), sealed with their key.
        fu = t.registry.get(FRIEND)
        key = tenancy.person_key(e, fu.account_id, fu.zogzog_gen, False)
        rel = f"data/render/{B_CHAR}.webp"
        zz = site_dir / "mira" / "b1" / "zogzog"
        (zz / "data").mkdir(parents=True)
        (zz / publish.sealed_name(key, rel)).write_bytes(publish.seal(key, rel, png))
        (site_dir / "mira" / "current").symlink_to("b1")

        code, hdrs, body = r.get(f"/render/{A_CHAR}.webp", headers=as_user(OWNER))
        assert (code, body) == (200, png)
        assert hdrs.get("Content-Type") == "image/webp"
        # The live answer points the page at it (the page keeps the 3D
        # toggle when the publisher hasn't drawn it yet).
        g = json.loads(r.get(f"/api/char?name={A_CHAR}", headers=as_user(OWNER))[2]).get("gear")
        assert g["render"] == {"ext": "webp"}
        code, _, body = r.get(f"/render/{B_CHAR}.webp", headers=h)
        assert (code, body) == (200, png)
        # Nobody reads another person's picture, by name or by site.
        assert r.get(f"/render/{A_CHAR}.webp", headers=h)[0] == 404
        assert r.get(f"/render/{B_CHAR}.webp", headers=as_user(OWNER))[0] == 404
        # A wrong key opens nothing.
        (zz / publish.sealed_name(key, rel)).write_bytes(publish.seal(b"k" * 32, rel, png))
        assert r.get(f"/render/{B_CHAR}.webp", headers=h)[0] == 404
        # Only our still is served: a PNG left in a bundle (Blizzard's render,
        # 0.7.0-rc.4 and older) or under a .webp name is not ours.
        (zz / publish.sealed_name(key, rel)).write_bytes(publish.seal(key, rel, b"\x89PNG\r\n\x1a\n" + b"x" * 64))
        assert r.get(f"/render/{B_CHAR}.webp", headers=h)[0] == 404
        (own / f"{A_CHAR}.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
        assert r.get(f"/render/{A_CHAR}.png", headers=as_user(OWNER))[0] == 404
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.parametrize("url,ok", [
    ("https://wow-full.m.example", True), ("http://127.0.0.1:8080", True), ("http://localhost:8080", True),
    ("http://[::1]:8080", True), ("", True),
    ("http://wow-full.m.example", False), ("http://192.0.2.10:8080", False), ("ftp://wow.example", False),
])
def test_public_url_is_https_off_loopback(env, url, ok):
    """Review L4: every friend's install and update pipes this server's reply
    into bash or iex, and the shipper sends its password to it. Over plain
    http an on-path attacker swaps the script: refused unless it's this
    machine (a laptop install, the tests)."""
    e = {**env, "PUBLIC_URL": url}
    if ok:
        tenancy.Tenancy(e)
    else:
        with pytest.raises(SystemExit, match="PUBLIC_URL"):
            tenancy.Tenancy(e)


def test_the_privacy_page_says_what_this_install_does():
    """Review L12: the page stated this lab's own operations (Plausible
    counts, backups that expire 30 days later) on every install. Those
    sentences now come from the install's settings; without them the page
    says to ask the owner rather than state something untrue."""
    from wowstats import account

    bare = account.privacy_page(False, {}).decode()
    assert "Plausible" not in bare and "30\ndays later" not in bare and "expire 30" not in bare
    assert "ask the owner" in bare.lower()
    lab = account.privacy_page(False, {"PLAUSIBLE_DOMAIN": "wow.m.example", "PRIVACY_BACKUP_DAYS": "30",
                                       "PRIVACY_LOG_DAYS": "30"}).decode()
    assert "Visit counts (Plausible, on this server's own address)." in lab
    assert "No cookies, nothing about your game." in lab
    assert "Backups of your events expire 30 days after that deletion." in lab
    assert "ask the owner how long" not in lab.lower()


def test_the_privacy_page_says_how_long_logs_keep_a_request():
    """Audit 2026-11-19: the page said the logs kept only the time, the page
    and the login id, and the offboarding promise left the logs out. The
    page now lists the access log's fields and how long they stay, from
    PRIVACY_LOG_DAYS; unset, it says to ask the owner."""
    from wowstats import account

    bare = account.privacy_page(False, {}).decode()
    assert "Ask the owner how long they keep these lines." in bare
    assert "Log lines about you stay until the logs expire: ask the owner how long." in bare
    assert "days after each visit" not in bare
    kept = account.privacy_page(False, {"PRIVACY_LOG_DAYS": "14", "PRIVACY_BACKUP_DAYS": "30"}).decode()
    assert "These lines are kept 14 days." in kept
    assert "Log lines about you expire 14 days after each visit." in kept
    assert "Backups of your events expire 30 days after that deletion." in kept
    assert "ask the owner how long" not in kept.lower()
    assert "@LOGS" not in kept and "@BACKUPS@" not in kept and "@VISITS@" not in kept
    junk = account.privacy_page(False, {"PRIVACY_LOG_DAYS": "a month"}).decode()
    assert "Ask the owner how long they keep these lines." in junk


def test_the_privacy_page_says_when_a_removed_friends_data_goes():
    """Owner, 2026-11-22: removal stops access at once, the data goes
    FRIENDS_PURGE_DAYS later (7 by default), or at once on request. The
    page reads the same setting as the deletion, so it can't promise
    another number."""
    from wowstats import account

    def how_long(env):
        page = account.privacy_page(False, env).decode()
        text = re.sub(r"\s+", " ", page.split("<h2>How long</h2>")[1].split("</p>")[0])
        assert "@PURGE@" not in text
        return text

    default = how_long({})
    assert "your access stops at once" in default and "deleted 7 days later" in default
    assert "within those 7 days, you get everything back" in default and "right away instead" in default
    assert "deleted 14 days later" in how_long({"FRIENDS_PURGE_DAYS": "14"})
    assert "deleted 1 day later" in how_long({"FRIENDS_PURGE_DAYS": "1"})
    at_once = how_long({"FRIENDS_PURGE_DAYS": "0"})
    assert "deleted within minutes" in at_once and "days later" not in at_once
    assert "within minutes" not in default


def test_the_privacy_page_lists_what_the_owner_can_see():
    """The audit's list: game data with other players' names, the free text
    people and their AI store, the web server's log fields, the login
    service, and what the MCP server receives (never the conversation)."""
    from wowstats import account

    page = account.privacy_page(False, {}).decode()
    for section in ("What is stored", "What the owner can see", "Your AI assistant", "How long"):
        assert f"<h2>{section}</h2>" in page
    assert "combat logs" in page
    assert "goals you gave the play analysis" in page and "findings" in page and "300 characters" in page
    assert "your IP address, your browser" in page and "without the part after <code>?</code>" in page
    assert "login service" in page and "your email address" in page.lower()
    assert "never gets your" in page and "conversation" in page
    assert "Nobody deletes backups or logs by hand" in page


def test_the_owners_first_key_is_checked_like_the_publishers(tmp_path):
    """Review info 11: the owner's generation-0 key file was used whatever
    its length; publish.read_key refuses anything but 256 bits. The full
    view would show a link that opens nothing the publisher sealed."""
    kf = tmp_path / "zogzog.key"
    kf.write_text(tenancy.b64u(b"k" * 16))
    with pytest.raises(SystemExit, match="256-bit"):
        tenancy.person_key({"ZOGZOG_KEY_FILE": str(kf)}, 0, 0, owner=True)
    kf.write_text(tenancy.b64u(b"k" * 32))
    assert tenancy.person_key({"ZOGZOG_KEY_FILE": str(kf)}, 0, 0, owner=True) == b"k" * 32


def test_the_privacy_page_names_what_people_trust():
    """Review info 9 and 12: the AI assistant sees other players' names (so
    their names reach the person's AI provider), and installing or updating
    runs what this server sends. Both are said on the page."""
    from wowstats import account

    page = account.privacy_page(False, {}).decode()
    assert "names of other players" in page and "AI provider" in page
    assert "runs what this server sends" in page


# ---------------------------------------------------------------- slugs from a BattleTag (synthetic tags only)

TAG, TAG_HEADER = "Pétrel#4821", "X-Forwarded-BattleTag"


def as_bnet(sub, tag=TAG):
    """The login proxy with the BattleTag claim forwarded: UTF-8 bytes on
    the wire, as Traefik writes them (urllib sends str headers as Latin-1)."""
    return {**as_user(sub), TAG_HEADER: tag.encode().decode("latin-1")}


def test_a_derived_slug_is_the_name_and_a_keyed_hash():
    key, other = b"k" * 32, b"o" * 32
    first, six, eight = tenancy.derived_slugs(TAG, key)
    assert re.fullmatch(r"petrel-[0-9a-f]{4}", first), first
    assert six.startswith(first) and len(six) == len(first) + 2 and eight.startswith(six)
    assert tenancy.derived_slugs(TAG, key) == [first, six, eight]  # stable
    assert tenancy.derived_slugs(TAG, other)[0] != first  # the key is in it: not worked out from the tag
    assert tenancy.derived_slugs("Pétrel#4822", key)[0] != first  # the whole tag, discriminator included
    for tag in (TAG, "Zogzog#123456", "Ünïcödé#9999", "Тестов#4321", "Averyveryverylongname#11111"):
        for s in tenancy.derived_slugs(tag, key):
            assert tenancy.SLUG_RE.match(s) and tag.partition("#")[2] not in s, (tag, s)
    assert tenancy.derived_slugs("Тестов#4321", key)[0].startswith("player-")
    assert tenancy.derived_slugs("Averyveryverylongname#11111", key)[0].startswith("averyveryver-")
    for not_a_tag in ("", "Sam Dumont", "sam@example.org", "Name#", "#1234", "Name#12ab", "Na#me#1234"):
        assert tenancy.derived_slugs(not_a_tag, key) == [], not_a_tag


def test_the_slug_key_is_made_once_and_kept(tmp_path):
    k = tenancy.slug_key(tmp_path)
    assert len(k) == 32 and tenancy.slug_key(tmp_path) == k
    if os.name != "nt":
        assert (tmp_path / "slug-key").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("content", [[FRIEND], {FRIEND: ""}, {FRIEND: None}])
def test_a_listed_friend_without_a_slug_is_named_from_their_battletag(server, vl, content):
    t = server.t
    t.battletag_header = TAG_HEADER
    Path(t.friends_file).write_text(json.dumps(content))
    assert tenancy.reconcile(t)["provisioned"] == [] and t.registry.get(FRIEND) is None  # waits for the login
    code, _, body = server.get("/api/me", headers=as_bnet(FRIEND))
    want = tenancy.derived_slugs(TAG, t.slug_key)[0]
    assert code == 200 and json.loads(body)["slug"] == want, body
    assert "4821" not in want
    # Stored: the slug. Never the BattleTag, in any form.
    state = b"".join(p.read_bytes() for p in Path(t.state).rglob("*") if p.is_file())
    assert b"4821" not in state and "Pétrel".encode() not in state and b"Petrel" not in state
    # Another tag later (a renamed account) changes nothing: the address stays.
    assert json.loads(server.get("/api/me", headers=as_bnet(FRIEND, "Other#5555"))[2])["slug"] == want
    assert tenancy.reconcile(t)["provisioned"] == [] and t.registry.get(FRIEND).slug == want
    assert f"{want}-wow.m.example" in {h["host"] for h in t.host_list()}
    from wowstats import static

    assert static.Site({"SITE_DIR": str(t.state / "site"), "SITE_DOMAIN": "m.example"}).slug_of(
        f"{want}-wow.m.example") == want


def test_a_taken_or_reserved_derived_slug_gets_a_longer_hash(server, vl):
    t = server.t
    t.battletag_header = TAG_HEADER
    first, six, eight = tenancy.derived_slugs(TAG, t.slug_key)
    Path(t.friends_file).write_text(json.dumps({"friend-3": first, FRIEND: ""}))
    tenancy.reconcile(t)
    assert t.registry.get("friend-3").slug == first
    t.registry.reserved = t.registry.reserved | {six}  # as the owner's alias would
    assert json.loads(server.get("/api/me", headers=as_bnet(FRIEND))[2])["slug"] == eight


def test_an_explicit_slug_in_the_list_wins(server, vl):
    t = server.t
    t.battletag_header = TAG_HEADER
    assert json.loads(server.get("/api/me", headers=as_bnet(FRIEND))[2])["slug"] == "mira"


def test_an_existing_slug_is_kept(server, vl, capsys):
    t = server.t
    tenancy.reconcile(t)
    assert t.registry.get(FRIEND).slug == "mira"
    t.battletag_header = TAG_HEADER
    Path(t.friends_file).write_text(json.dumps([FRIEND, "friend-3"]))  # the host now leaves names to the login
    out = tenancy.reconcile(t)
    assert out["provisioned"] == [] and out["removed"] == []
    assert json.loads(server.get("/api/me", headers=as_bnet(FRIEND))[2])["slug"] == "mira"
    assert t.registry.get("friend-3").slug == "pip" and '"slug_kept"' not in capsys.readouterr().out


@pytest.mark.parametrize("header,sent", [("", TAG), (TAG_HEADER, None), (TAG_HEADER, "Mira Lastname")])
def test_no_battletag_keeps_todays_behaviour(server, vl, capsys, header, sent):
    """No header configured (a self-hoster without Battle.net), none sent,
    or a display name that isn't a BattleTag: in, without an address, until
    the list names them."""
    t = server.t
    t.battletag_header = header
    Path(t.friends_file).write_text(json.dumps({FRIEND: ""}))
    h = as_user(FRIEND) if sent is None else as_bnet(FRIEND, sent)
    for _ in range(2):
        code, _, body = server.get("/api/me", headers=h)
        assert code == 200 and json.loads(body)["slug"] == ""
    assert tenancy.name_conflict(t, t.registry.get(FRIEND), {FRIEND: ""}) == {"slug": "", "why": "no name"}
    assert capsys.readouterr().out.count('"action": "name_conflict"') == 1
    code, _, body = server.get("/api/me/install-code", headers={**h, "Content-Type": "application/json"},
                               method="POST", data=b"{}")
    assert code == 400 and b"needs a fix" in body
    Path(t.friends_file).write_text(json.dumps({FRIEND: "mira"}))
    assert tenancy.reconcile(t)["provisioned"] == ["mira"]
    assert json.loads(server.get("/api/me", headers=h)[2])["slug"] == "mira"


def test_the_friends_file_shapes(tmp_path):
    f = tmp_path / "f.json"
    for content, want in (([FRIEND, "b"], {FRIEND: "", "b": ""}), ({FRIEND: "mira", "b": ""}, {FRIEND: "mira", "b": ""}),
                          ({FRIEND: None}, {FRIEND: ""}), ({FRIEND: "frost-3f9a"}, {FRIEND: "frost-3f9a"})):
        f.write_text(json.dumps(content))
        assert tenancy.read_friends(str(f)) == want, content


# ---------------------------------------------------------------- the owner's other logins (synthetic subs only)

ALIAS = "owner-alias-9"


@pytest.fixture
def two_logins(env, run_app):
    """The owner with two logins: WOW_OWNER_SUB as a comma-separated list,
    the alias second (the record stays on the first)."""
    e = {**env, "WOW_OWNER_SUB": f"{OWNER}, {ALIAS}"}
    app = ui.App(e)
    t = ui.tenancy_of(e, app)
    r = run_app(app)
    r.srv.RequestHandlerClass = ui.make_handler(app, t)
    r.t, r.app, r.env = t, app, e
    yield r


def test_one_owner_sub_still_works(env):
    t = tenancy.Tenancy(env)
    assert t.owner_subs == (OWNER,) and t.owner_sub == OWNER
    assert t.identify(OWNER).user.account_id == 0
    assert t.identify(ALIAS).status == 403


def test_every_owner_login_is_tenant_0_on_the_web(two_logins):
    t = two_logins
    for sub in (OWNER, ALIAS):
        code, _, body = t.get("/api/me", headers=as_user(sub))
        me = json.loads(body)
        assert code == 200 and me["owner"] is True and me["slug"] == "sam", (sub, body)
        code, _, body = t.get("/api/chars", headers=as_user(sub))
        assert code == 200 and b"Testpriest" in body, sub
        assert t.get("/api/admin/hosts", headers=as_user(sub))[0] == 200, sub
    # One record, one App, one analysis state: the alias is the owner, not a second tenant.
    assert t.t.registry.get(ALIAS) is None
    assert [u.account_id for u in t.t.registry.users()] == [0]
    assert list(t.t._apps) == [OWNER]
    assert t.get("/api/me", headers=as_user("someone-else"))[0] == 403


def test_every_owner_login_is_tenant_0_over_mcp(two_logins, tmp_path):
    from tests.mcp import oauth_client, oidc_standin

    t = two_logins.t
    for sub in (OWNER, ALIAS):
        u = t.resolve_tenant(sub)
        assert u.owner and u.account_id == 0 and u.sub == OWNER, sub
        assert t.mcp_user(sub).sub == OWNER
    assert t.resolve_tenant("someone-else") is None and t.mcp_user("someone-else") is None
    # The real login, as the alias: the code exchange admits it, the tools
    # run on the owner's data and context (one dashboard_refresh timer).
    with oidc_standin.Standin(user=ALIAS) as idp:
        (tmp_path / "oidc").write_text(oidc_standin.CLIENT_SECRET)
        (tmp_path / "sign").write_text("s" * 40)
        port = oauth_client.free_port()
        ep = mcpmod.endpoint({**two_logins.env, "MCP_AUTH": "oauth", "MCP_PUBLIC_URL": f"http://127.0.0.1:{port}/mcp",
                              "MCP_PORT": str(port), "OIDC_ISSUER": idp.issuer,
                              "OIDC_CLIENT_ID": oidc_standin.CLIENT_ID,
                              "OIDC_CLIENT_SECRET_FILE": str(tmp_path / "oidc"),
                              "MCP_SIGNING_KEY_FILE": str(tmp_path / "sign"),
                              "MCP_STATE_DIR": str(tmp_path / "mcp-state")}, "127.0.0.1")
        ep.attach_tenancy(t)
        run = ep.start()
        try:
            for sub in (ALIAS, OWNER):
                idp.user = sub
                idp.users[sub] = {"org": "", "roles": [], "active": True}
                lg = oauth_client.Login(f"http://127.0.0.1:{port}", run.url)
                lg.login()
                r = lg.rpc("tools/call", {"name": "character_summary", "arguments": {"char": A_CHAR}})
                assert r.status_code == 200 and b"Testpriest" in r.content, (sub, r.text[:300])
            idp.user = "someone-else"
            idp.users["someone-else"] = {"org": "", "roles": [], "active": True}
            other = oauth_client.Login(f"http://127.0.0.1:{port}", run.url)
            other.register()
            r = other.exchange(other.code_from(other.authorize()))
            assert r.status_code in (400, 401) and r.json()["error"] == "invalid_grant", r.text
        finally:
            run.stop()
    assert list(t._ctx) == [OWNER] and t.registry.get(ALIAS) is None


def test_an_owner_login_on_the_friends_list_is_the_owner(env, capsys):
    friends = Path(env["WOW_FRIENDS_FILE"])
    friends.write_text(json.dumps({FRIEND: "mira", ALIAS: "alias"}))
    # A friend's record from before the alias joined the owner's list: kept,
    # never deleted, never used.
    t0 = tenancy.Tenancy(env)
    old = t0.identify(ALIAS).user
    assert old.account_id >= 1 and not old.owner
    capsys.readouterr()
    t = tenancy.Tenancy({**env, "WOW_OWNER_SUB": f"{OWNER},{ALIAS}"})
    out = capsys.readouterr().out
    logged = [json.loads(x) for x in out.splitlines() if "owner_also_listed" in x]
    assert [x["position"] for x in logged] == [2] and ALIAS not in out
    u = t.identify(ALIAS).user
    assert u.owner and u.account_id == 0 and u.sub == OWNER
    assert t.mcp_user(ALIAS).account_id == 0
    done = tenancy.reconcile(t)
    assert done["removed"] == [] and "alias" not in done["provisioned"]
    assert t.registry.get(ALIAS).account_id == old.account_id  # still there, untouched


def test_a_battletag_never_names_or_provisions_an_owner_login(two_logins):
    t = two_logins.t
    t.battletag_header = TAG_HEADER
    Path(t.friends_file).write_text(json.dumps([FRIEND, ALIAS]))
    before = json.loads(Path(t.registry.path).read_text())["next_account"]
    code, _, body = two_logins.get("/api/me", headers=as_bnet(ALIAS))
    assert code == 200 and json.loads(body)["owner"] is True and json.loads(body)["slug"] == "sam", body
    tenancy.reconcile(t)
    assert t.registry.get(ALIAS) is None
    data = json.loads(Path(t.registry.path).read_text())
    assert data["next_account"] == before and [u.account_id for u in t.registry.users()] == [0]
