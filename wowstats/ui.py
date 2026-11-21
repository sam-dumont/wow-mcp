"""`wow-stats ui`: the web UI, its JSON API, and the optional ingest endpoint.

Stdlib only. It reads the stores (GETs), answers GETs, and when INGEST is
set up, forwards the shipper's NDJSON to VictoriaLogs. The answers a plain
open asks for are built once per new data in the background and served as
they are (wowstats.prebuilt); the rest, and all of them before the first
build, are worked out when asked and kept in memory for CACHE_TTL seconds.

Environment (defaults suit deploy/compose):
  VIEW                   full | public. Unset: full on a loopback BIND, else
                         the server refuses to start. One process serves one
                         view, so two deployments of the same image can't
                         leak into each other through a routing mistake.
  VL_URL                 VictoriaLogs with the events (http://victorialogs:9428)
  VL_ARCHIVE_URL         optional second VictoriaLogs holding older days:
                         ranges older than VL_ARCHIVE_AFTER_DAYS (89) are read there
  VM_URL                 VictoriaMetrics with the Battle.net exporter's gauges
                         (empty: no exporter; full view only)
  LOGS_FILTER            LogsQL filter that selects the events (feed:wow-*)
  VL_CONCURRENCY         VictoriaLogs requests at once, all callers together (2)
  DEFAULT_CHAR           character opened first (empty: the latest seen)
  BIND, PORT             127.0.0.1, 8080
  AUTH                   none | basic | proxy | open (docs/deploy.md, "Logins")
  BASIC_AUTH_USER, BASIC_AUTH_PASSWORD or BASIC_AUTH_PASSWORD_FILE
  INGEST_PASSWORD or INGEST_PASSWORD_FILE, INGEST_USER (wow): turns on
                         POST /ingest for the shipper, with its own login
  PRICE_DAYS             days of Auctionator scans in the prices panel (90)
  MCP_PUBLIC_URL, MCP_*: the MCP endpoint (wowstats.mcp, docs/mcp.md), FastMCP
                         on its own port (MCP_PORT, 8090) in this process; off
                         until MCP_PUBLIC_URL is set (or MCP_AUTH=none on a
                         loopback BIND)
  MODEL3D, MODEL3D_*     the 3D character (wowstats.model3d), full view only;
                         on unless MODEL3D=off
  USERS_URL, ANALYSIS_SHARE_TOKEN_FILE: public view only, with several
                         people: the full view's /users.json, so the live
                         page follows Settings (public page off, hidden
                         characters; wowstats.sitesettings)
"""

from __future__ import annotations

import base64
import hmac
import importlib
import ipaddress
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from wowstats import bar, guard, httpserve, insights, itemcard, itemfacts, model3d, prebuilt, redact
from wowstats.stores import SourceError, Store, env_account, iso, parse_t, public_error, tenant_headers
from wowstats.views import Settings, Views

# media-src: the manual's clips, from /start/media/ and, on the help page, as
# blob: URLs of the clips it decrypts (wowstats.mcp.helppage).
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; "
       "connect-src 'self'; font-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")
# The full page with the 3D character only adds a frame of its own origin:
# /model3d/view, sandboxed under its own policy (wowstats.model3d).
CSP_3D = CSP.replace("frame-ancestors 'none'", "frame-src 'self'; frame-ancestors 'none'")
CHAR_RE = re.compile(r"^[^\x00-\x1f\"\\]{1,64}$")
SPEC_RE = re.compile(r"^[a-z]{1,20}$")
FULL_BLOCK = re.compile(r"<!--full-->.*?<!--/full-->", re.S)
MODEL3D_BLOCK = re.compile(r"<!--model3d-->.*?<!--/model3d-->", re.S)
INGEST_MAX_BYTES = 8 * 1024 * 1024
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/base.css": ("base.css", "text/css; charset=utf-8"),
    "/theme.js": ("theme.js", "text/javascript; charset=utf-8"),
    "/items.js": ("items.js", "text/javascript; charset=utf-8"),
}
# The game's item icons (wowstats.itemcard), full view only: the pod that
# fetches them is the one with a route to Wowhead's icon host.
ICON_PATH = re.compile(r"^/icons/(large|medium)/([a-z0-9_\-]{1,96})\.jpg$")
# The character's picture (wowstats.armory): our own still of its 3D model,
# read from the static site, which the publisher fills.
RENDER_PATH = re.compile(r"^/render/([^/]{1,80})\.webp$")
# /fonts/<name>.woff2: the pages' own fonts (bar.FONTS; OFL, vendored in
# web/fonts): no font CDN, CSP font-src 'self'.
FONTS = {"/" + rel: rel for rel in bar.FONTS}


def static_dir() -> Path:
    if os.environ.get("STATIC_DIR"):
        return Path(os.environ["STATIC_DIR"])
    here = Path(__file__).resolve().parent
    packaged = here / "web"  # the wheel carries web/ inside the package
    return packaged if packaged.is_dir() else here.parent / "web"


def optional(name: str):
    """wowstats.mcp is optional: absent, its routes 404.
    A module that exists but fails to import is a bug, not an absence."""
    try:
        return importlib.import_module(f"wowstats.{name}")
    except ModuleNotFoundError as e:
        if e.name == f"wowstats.{name}":
            return None
        raise


class ConfigError(SystemExit):
    pass


class FullOnly(Exception):
    """An answer the public view doesn't have: the route answers 404."""


def _loopback(bind: str) -> bool:
    try:
        return ipaddress.ip_address(bind).is_loopback
    except ValueError:
        return bind == "localhost"


def _secret(env: dict, name: str) -> str:
    if env.get(f"{name}_FILE"):
        return Path(env[f"{name}_FILE"]).read_text(encoding="utf-8").strip()
    return env.get(name, "")


def _basic(user: str, pw: str) -> bytes:
    return b"Basic " + base64.b64encode(f"{user}:{pw}".encode())


def plan_status(char: str, store, env: dict | None = None, battlenet: list[int] | None = None) -> dict:
    from wowstats import plan

    return plan.status(dict(os.environ if env is None else env), char or None, store, battlenet=battlenet)


def parse_weights(raw: str) -> dict[str, str]:
    """WEIGHTS=Testpriest-Fixture:shadow,Other-Name:holy -> {character: spec}:
    the spec each character's gear is scored for, over what its add-ons say
    (owner, 2026-11-18: the weights are the player's choice, the talents are
    shown as they are)."""
    out = {}
    for pair in (p.strip() for p in raw.split(",")):
        if not pair:
            continue
        char, sep, spec = pair.partition(":")
        if not sep or not char.strip() or not spec.strip():
            raise ConfigError(f"WEIGHTS: {pair!r} is not Character-Name:spec")
        out[char.strip().replace(" ", "-")] = spec.strip().lower()
    return out


class App:
    """Everything one server process needs, read from the environment once."""

    def __init__(self, env: dict | None = None, store: Store | None = None):
        env = dict(os.environ if env is None else env)
        self.env = env
        self.bind = env.get("BIND", "127.0.0.1")
        self.port = int(env.get("PORT", "8080"))
        # Where the character's picture comes from: the static site, whose
        # bundles hold the publisher's still of the 3D model (wowstats.armory).
        # Empty: no picture, the page shows the 3D toggle.
        self.render_from = env.get("RENDER_FROM", "").strip()
        from wowstats import armory

        armory.forget_blizzard_cache(env)  # 0.7.0-rc.4 and older: Blizzard's renders, never shown again
        # With several people: () -> (the person's site host, their /zogzog/
        # key), set by wowstats.tenancy and read at each request (a new link
        # changes the key).
        self.render_site = None
        self.zogzog_path = env.get("ZOGZOG_PATH", "/zogzog/").strip()
        loopback = _loopback(self.bind)

        view = env.get("VIEW", "").strip().lower()
        if not view:
            if not loopback:
                raise ConfigError(f"BIND={self.bind} is not a loopback address and VIEW is unset: set VIEW=public "
                                  "(anonymous visitors) or VIEW=full (behind a login).")
            view = "full"
        if view not in ("full", "public"):
            raise ConfigError(f"VIEW={view}: use full or public.")
        self.view = view

        mode = env.get("AUTH", "").strip().lower() or ("none" if loopback else "")
        if mode == "":
            raise ConfigError(f"BIND={self.bind} is not a loopback address and AUTH is unset: set AUTH=basic or "
                              "AUTH=proxy (or AUTH=none with BIND=127.0.0.1, AUTH=open when the port is "
                              "published on 127.0.0.1 only or the view is public).")
        if mode == "none" and not loopback:
            raise ConfigError(f"AUTH=none on BIND={self.bind}: anyone who reaches this port would see the stats. "
                              "Bind to 127.0.0.1, or set AUTH=basic, AUTH=proxy, or AUTH=open on purpose.")
        self.auth_expected: bytes | None = None
        if mode == "basic":
            user, pw = env.get("BASIC_AUTH_USER", ""), _secret(env, "BASIC_AUTH_PASSWORD")
            if not user or len(pw) < 12:
                raise ConfigError("AUTH=basic needs BASIC_AUTH_USER and a BASIC_AUTH_PASSWORD (or _FILE) "
                                  "of 12+ characters.")
            self.auth_expected = _basic(user, pw)
        elif mode not in ("none", "proxy", "open"):
            raise ConfigError(f"AUTH={mode}: use none, basic, proxy or open.")
        self.auth_mode = mode
        # Fail closed whatever is in front (wowstats.guard): no open door off
        # loopback by accident, a proxy header only from the proxy, and only
        # the hosts this server is reached by.
        try:
            if mode == "open":
                guard.check_open(env, self.bind)
                if self.view == "full" and (env.get("ALLOW_OPEN") or "").strip().lower() not in guard.TRUE:
                    guard.check_open_hosts(env)
            self.proxy = guard.ProxyTrust(env) if mode == "proxy" else None
        except guard.GuardError as e:
            raise ConfigError(str(e.code)) from None
        self.hosts = guard.Hosts(env)
        self.open_on_purpose = (env.get("ALLOW_OPEN") or "").strip().lower() in guard.TRUE

        ingest_pw = _secret(env, "INGEST_PASSWORD")
        self.ingest_expected: bytes | None = None
        if ingest_pw:
            if len(ingest_pw) < 12:
                raise ConfigError("INGEST_PASSWORD (or _FILE) needs 12+ characters.")
            self.ingest_expected = _basic(env.get("INGEST_USER", "wow"), ingest_pw)

        self.store = store or Store(
            env.get("VL_URL", "http://victorialogs:9428"), env.get("VL_ARCHIVE_URL", ""),
            env.get("VM_URL", "") if view == "full" else "",  # the live public view: no market, no 3D
            env.get("LOGS_FILTER", "feed:wow-*"), int(env.get("VL_ARCHIVE_AFTER_DAYS", "89")),
            profile_vm_url=env.get("VM_URL", ""),  # the profile: allowed in public (stores.py)
            account_id=env_account(env))
        self.settings = Settings(
            view=view, default_char=env.get("DEFAULT_CHAR", ""),
            price_days=int(env.get("PRICE_DAYS", "90")), weights=parse_weights(env.get("WEIGHTS", "")))
        self.views = Views(self.store, self.settings)
        # The person's Settings (public page on or off, hidden characters) for
        # the live public view, from the full view's share port; None: one
        # person, no Settings, everything shown (wowstats.sitesettings).
        self.page_settings = None
        if view == "public":
            from wowstats import sitesettings

            self.page_settings = sitesettings.live(env, env_account(env))
        self.cache_ttl = int(env.get("CACHE_TTL", "45"))
        door = env.get("FULL_DOOR_PATH", "/full/").strip().strip("/")
        self.door = f"/{door}/" if door else ""
        self.static = static_dir()
        full = view == "full"
        self.mcp = self._mcp(env) if full else None
        # The play analysis (wowstats.analysis): the full view reads its kept
        # state; the nightly recheck starts from main().
        self.analysis_env = {k: v for k, v in env.items() if k.startswith("ANALYSIS_")} if full else {}
        # Never in the public view: its routes don't exist there, its markup
        # is stripped and the page's CSP stays the strict one.
        on = env.get("MODEL3D", "on").strip().lower() not in ("off", "0", "false", "no")
        self.model3d = model3d.Model3D(env, self.static) if full and on else None
        # The game files the person's shipper sends for the 3D view
        # (wowstats.assets): theirs only, with the 3D view only. The shipper
        # is asked for what this person's characters' recipes name.
        from wowstats import assets

        self.assets = assets.Assets(env, self.model3d.cache) if self.model3d is not None else None
        if self.model3d is not None:
            self.model3d.assets = self.assets
            self.assets.wanted = lambda: self.model3d.wanted(self)
        # The facts of the items the reference lacks, from the person's own
        # game client (wowstats.itemfacts): read for their answers only.
        self.item_facts = itemfacts.for_env(env)
        # Plausible on every page of the full view too (owner, 2026-11-20):
        # the same PLAUSIBLE_DOMAIN as the static sites, so every wow host
        # counts into one Plausible site, told apart by hostname. Checked
        # now, so a bad value stops the server at start.
        self.stats = None
        if full:
            from wowstats import publish

            self.stats = publish.analytics(env)
        self.env = env
        self._cache: dict[str, tuple[float, dict]] = {}
        # key -> [lock, users]: only keys being built (review 0.6.0, S5).
        self._locks: dict[str, list] = {}
        self._guard = threading.Lock()
        # The answers built once per new data (wowstats.prebuilt); the thread
        # building them records into its own dict (page_answers).
        self.prebuilt = prebuilt.Prebuilt(self)
        self._building = threading.local()

    def _mcp(self, env: dict):
        """The MCP endpoint's settings, checked now so a bad one stops the
        server at start rather than later; main() starts it on its own port.
        Off until MCP_PUBLIC_URL says where clients reach it."""
        mod = optional("mcp")
        if mod is None:
            return None
        try:
            ep = mod.endpoint(env, self.bind)
        except SystemExit as e:
            raise ConfigError(str(e.code)) from None
        if ep is not None:
            ep.ctx.title = self.title  # character_summary's title: the page's own
        return ep

    # ------------------------------------------------------------ cache

    def cached(self, key: str, fn):
        """The prebuilt answer when there is one (wowstats.prebuilt). Else
        fresh for cache_ttl seconds; one refresh at a time per key; a failed
        refresh serves the last good answer, marked stale."""
        # Worked out with this person's item facts (wowstats.itemfacts), never another's.
        fn = self._with_facts(fn)
        building = getattr(self._building, "answers", None)
        if building is not None:  # the builder's own thread: worked out now, once per build
            if key not in building:
                building[key] = {**fn(), "generated_at": iso(datetime.now(UTC))}
            return building[key]
        hit = self.prebuilt.get(key)
        if hit is not None:
            return hit
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < self.cache_ttl:
            return hit[1]
        # One lock per key in flight, dropped with its last user: a fixed
        # array of locks would deadlock a build that asks for another key.
        with self._guard:
            entry = self._locks.setdefault(key, [threading.Lock(), 0])
            entry[1] += 1
        try:
            return self._build(key, fn, entry[0])
        finally:
            with self._guard:
                entry[1] -= 1
                if entry[1] == 0:
                    self._locks.pop(key, None)

    def _with_facts(self, fn):
        def run():
            with itemfacts.using(self.item_facts.facts()):
                return fn()

        return run

    def item_cards(self, ids) -> dict[str, dict]:
        """The tooltip cards of `ids` (wowstats.itemcard), and for an item the
        catalog has no card for, or a card without stats, this person's item
        facts (wowstats.itemfacts): the catalog's card wins field by field."""
        cat = itemcard.shared()
        cards = cat.many(ids)
        facts = self.item_facts.facts()
        if not facts:
            return cards
        for i in ids:
            if str(i) in cards and (cards[str(i)].get("w") or cards[str(i)].get("g")):
                continue
            got = facts.card(i, getattr(cat, "icon_files", None), getattr(cat, "subclasses", None))
            if got:
                cards[str(i)] = {**got, **cards.get(str(i), {})}
        return cards

    def _build(self, key: str, fn, lock: threading.Lock):
        with lock:
            hit = self._cache.get(key)
            if hit and time.time() - hit[0] < self.cache_ttl:
                return hit[1]
            try:
                value = fn()
            except SourceError as e:
                if hit:
                    # Nothing from the key in the log: it can hold the 3D
                    # view's sex (CodeQL py/clear-text-logging, PR #8).
                    return {**hit[1], "stale": True, "error": public_error(e, "a cached answer")}
                raise
            value = {**value, "generated_at": iso(datetime.now(UTC))}
            self._cache[key] = (time.time(), value)
            if len(self._cache) > 64:  # a few characters at most; never grows unbounded
                oldest = min(self._cache, key=lambda k: self._cache[k][0])
                self._cache.pop(oldest, None)
            return value

    # ------------------------------------------------------------ data, already shaped for this view

    def features(self) -> dict:
        full = self.view == "full"
        feats = {"view": self.view, "players": full, "gold": full, "market": full, "prices": full,
                 "debug": full, "mcp": bool(self.mcp), "journal": full, "people": full,
                 "loot": full, "analysis": bool(self.analysis_env.get("ANALYSIS_STATE_DIR")),
                 "plan": full}
        return feats if full else redact.public("config", feats)

    def public_off(self) -> bool:
        """The person turned their public page off in Settings (public view only)."""
        return self.page_settings is not None and not self.page_settings.get().public

    def hidden(self) -> frozenset:
        """The characters this view never shows: the public view's hidden list."""
        return self.page_settings.get().hide_public if self.page_settings is not None else frozenset()

    def chars(self) -> dict:
        rows = self.cached("chars", lambda: {"chars": self.views.chars()})["chars"]
        if not rows:
            # A store with nothing yet (a fresh install, the demo before its
            # seed): ask again next time instead of showing no one for CACHE_TTL.
            self._cache.pop("chars", None)
        if self.view == "full":
            return {"chars": rows}
        hide = self.hidden()
        return redact.public("chars", {"chars": [c for c in rows if c["char"] not in hide]})

    @staticmethod
    def valid_char(name: str) -> bool:
        return bool(CHAR_RE.match(name))

    def known(self, name: str) -> bool:
        if name in self.hidden():
            return False
        return name in {c["char"] for c in self.cached("chars", lambda: {"chars": self.views.chars()})["chars"]}

    def render(self, name: str) -> bytes | None:
        """The picture of a known character (/render/<char>.webp): the
        publisher's still of its 3D model, from the static site."""
        from wowstats import armory

        if not self.render_from or not self.known(name):
            return None
        host, key = self.render_site() if self.render_site else ("", None)
        return armory.from_site(self.render_from, name, host=host, key=key, zogzog=self.zogzog_path)

    # The public view answers char, timeline and snapshot only (redact.PUBLIC);
    # journal, overview, gear advice and people are full-view answers.

    def char(self, name: str) -> dict:
        d = self.cached(f"char:{name}", lambda: self.views.char(name))
        if self.render_from and isinstance(d.get("gear"), dict):
            # The live view can't know whether the publisher has drawn the
            # still yet: the page asks /render/<char>.webp and keeps the
            # silhouette and the 3D toggle when there is none.
            d = {**d, "gear": {**d["gear"], "render": {"ext": "webp"}}}
        if self.view == "full":
            return d
        return redact.char(d, *self.views.route_levels(d))

    def missing(self, name: str) -> list[dict]:
        """What name's pages can't show yet and the fix (wowstats.missing);
        full view only: the static sites leave it out (no tier lists it)."""
        from wowstats import missing

        snap = self.cached("missing", lambda: missing.snapshot(lambda q: self.views.vl(f"{self.views.stream} {q}")))
        return missing.for_char(snap, name)

    def journal(self, name: str) -> dict:
        self._full_only()
        return self.cached(f"journal:{name}", lambda: self.views.journal(name))

    def overview(self, name: str) -> dict:
        self._full_only()

        def build() -> dict:
            ov = self.views.overview(name)
            if any(m.get("kind") == "dungeon" for m in ov.get("moves") or []):
                # A dungeon move carries the Dungeons panel's estimate of a
                # run (XP, time, pace, quests): one estimate, both places.
                from wowstats.plan import dungeonview

                ov["moves"] = dungeonview.with_gain(ov["moves"], self.dungeons(name))
            return ov

        return self.cached(f"overview:{name}", build)

    def timeline(self, name: str) -> dict:
        if self.view == "full":
            return self.cached(f"timeline:{name}", lambda: self.views.timeline(name))
        # The slider's stops only: the levels with a span of time, from the
        # character page's own answer (no events read at all).
        d = self.cached(f"char:{name}", lambda: self.views.char(name))
        return redact.public("timeline", {"char": name, "levels": sorted(insights.windows(d)),
                                          "generated_at": d.get("generated_at")})

    def snapshot(self, name: str, level: int) -> dict:
        d = self.cached(f"snapshot:{name}:{level}", lambda: self.views.snapshot(name, level))
        return d if self.view == "full" else redact.public("snapshot", d)

    def loot(self, name: str) -> dict:
        self._full_only()
        return self.cached(f"loot:{name}", lambda: self.views.loot(name))

    def bnet_worn(self, name: str) -> list[int]:
        """What Battle.net says the character wears now (item ids), read live
        for the planner's quest answer (wowstats.quests.with_battlenet)."""
        from wowstats import gear, quests

        return quests.worn_ids(gear.armory_worn(self.store, name))

    def dungeons(self, name: str) -> dict:
        """The Dungeons panel (wowstats.plan.dungeonview), from the planner's
        inputs: what each dungeon holds for the character."""
        self._full_only()

        def build() -> dict:
            from wowstats import plan
            from wowstats.plan import dungeonview

            try:
                inp = plan.inputs(self.env, name, self.store, battlenet=self.bnet_worn(name))
            except LookupError as e:
                return {"char": name, "available": False, "why": str(e)}
            return {**dungeonview.build(inp), "available": True}

        return self.cached(f"dungeons:{name}", build)

    def gear(self, name: str, spec: str | None) -> dict:
        self._full_only()
        return self.cached(f"gear:{name}:{spec or ''}", lambda: self.views.gear(name, spec))

    def meta(self, name: str) -> dict:
        """The class's meta and the character's adherence (wowstats.classmeta),
        from the page's own char and gear answers; full view only."""
        self._full_only()
        return self.cached(f"meta:{name}", lambda: self.views.meta(name, self.char(name), self.gear(name, None)))

    def talent_history(self, name: str) -> dict:
        """The talent changes of the last 3 weeks (wowstats.views), full view
        only and never through the answer cache: Battle.net's readings are
        read from vm-wow per request and kept nowhere else. No tier lists
        it (redact), so no bundle carries it."""
        self._full_only()
        hd = (self.char(name) or {}).get("header") or {}
        return self.views.talent_history(name, hd.get("class"))

    def news(self, name: str | None, week: str | None = None) -> dict:
        """This week on Forever (wowstats.news): the weekly digest, what
        concerns the viewer's characters first (the open one by class,
        level and role; the others by level), and what is coming. Full view
        only; the earlier weeks with `week`."""
        self._full_only()
        from wowstats import news

        def build() -> dict:
            mine = []
            if name:
                hd = (self.char(name) or {}).get("header") or {}
                mine.append(hd)
            mine += [{"level": c.get("level")} for c in self.chars()["chars"] if c.get("char") != name]
            return news.digest(chars=mine, week=week)

        return self.cached(f"news:{name or ''}:{week or ''}", build)

    def api_chars(self) -> dict:
        """/api/chars as the route sends it."""
        if self.view != "full":
            return self.chars()
        return self.cached("api:chars", lambda: {**self.chars(), "sources": self.store.sources()})

    def api_char(self, name: str) -> dict:
        """/api/char as the route sends it: the full view adds the sources and
        what the character's pages can't show yet."""
        if self.view != "full":
            return self.char(name)
        return self.cached(f"api:char:{name}", lambda: {**self.char(name), "sources": self.store.sources(),
                                                        "missing": self.missing(name)})

    def api_journal(self, name: str) -> dict:
        self._full_only()
        return self.cached(f"api:journal:{name}", lambda: {**self.journal(name), "sources": self.store.sources()})

    def people(self, name: str) -> dict:
        self._full_only()
        return self.cached(f"people:{name}", lambda: self.views.people(name))

    def plan(self, name: str) -> dict:
        """The mount goal and the last plan against what happened."""
        self._full_only()

        def build() -> dict:
            try:
                return plan_status(name, self.store, self.env, battlenet=self.bnet_worn(name))
            except LookupError as e:
                return {"available": False, "why": str(e)}

        return self.cached(f"plan:{name}", build)

    def market(self) -> dict:
        self._full_only()
        return self.cached("market:api", self.views.market)

    def prices(self) -> dict:
        self._full_only()
        return self.cached("prices", self.views.prices)

    def model3d_answer(self, name: str, level: int | None = None, sex: str = "") -> dict:
        """/api/model3d: the 3D viewer's answer (wowstats.model3d)."""
        # The store's stamp in the key: files that arrive make a recipe
        # drawable without a new event (a prebuilt answer would wait for one).
        return self.cached(f"model3d:{name}:{level or ''}:{sex}:{self.model3d.stamp()}",
                           lambda: self.model3d.for_char(self, name, level, sex))

    # ------------------------------------------------------------ prebuilt

    def data_mark(self, before: tuple | None = None) -> tuple:
        """What says the answers may have changed (wowstats.prebuilt): the
        newest event and the number of events (an old session sent late
        moves only the count), vm-wow's Battle.net fingerprint (when vm-wow
        doesn't answer, the last one seen, as the publisher does), and the
        person's item facts: their shipper sends those after its events, so
        a build the events started missed them (rc.2 on the lab: the Drowned
        City kept its bare names for a day)."""
        rows = self.views.vl(f"{self.views.stream} | stats max(_time) as t, count() as n", (),
                             {"t": "maxtime", "n": "sum"})
        r = rows[0] if rows else {}
        try:
            fp = self.store.profile_fingerprint()
        except Exception:  # noqa: BLE001 - vm-wow down: judge on the events alone
            fp = before[2] if before else ""
        f = self.item_facts.facts()
        facts = f"{len(f.items)}:{len(f.budget)}:{sum(int(v.get('h') or 0) for v in f.items.values())}"
        return str(r.get("t") or ""), str(r.get("n") or "0"), fp, facts

    def page_answers(self, only: str = "", keep: dict | None = None) -> dict:
        """Every answer a plain open of the page asks for (web/app.js loadChar
        and start), and the Timeline's, worked out now through the routes'
        own methods. `only`: rebuild those keys alone, over `keep`.
        A store down raises SourceError (the last set stays); any other
        failure leaves that answer out, worked out when asked."""
        out = {k: v for k, v in (keep or {}).items() if not (only and k.startswith(only))}
        self._building.answers = out

        def ask(what, *a):
            try:
                return what(*a)
            except (FullOnly, LookupError):
                return None
            except SourceError:
                if what in (self.market, self.prices):  # vm-wow down: that section only, as the publisher
                    return None
                raise
            except Exception as e:  # noqa: BLE001 - one answer's bug: that route works it out when asked
                prebuilt.log(f"{getattr(what, '__name__', what)}{a}: {e!r}")
                return None

        try:
            full = self.view == "full"
            if full:
                ask(self.market)
            if only:
                return out
            self.views.expire()  # nothing read before this build is reused in it
            ask(self.api_chars)
            if full:
                ask(self.prices)
            for c in self.cached("chars", lambda: {"chars": self.views.chars()})["chars"]:
                name = c.get("char")
                if not isinstance(name, str) or not self.valid_char(name) or name in self.hidden():
                    continue
                ask(self.api_char, name)
                tl = ask(self.timeline, name) or {}
                if full:
                    for fn in (self.api_journal, self.overview, self.dungeons, self.loot, self.people, self.plan,
                               self.meta):
                        ask(fn, name)
                    g = ask(self.gear, name, None) or {}
                    for spec in ((g.get("spec") or {}).get("choices") or []):
                        if isinstance(spec, dict) and SPEC_RE.match(str(spec.get("key") or "")):
                            ask(self.gear, name, spec["key"])
                    if self.model3d is not None:
                        ask(self.model3d_answer, name)
                for lv in tl.get("levels") or []:
                    if isinstance(lv, int) and 1 <= lv <= 60:
                        ask(self.snapshot, name, lv)
                        if full and self.model3d is not None:
                            ask(self.model3d_answer, name, lv)
            return out
        finally:
            self._building.answers = None

    def _full_only(self) -> None:
        if self.view != "full":
            raise FullOnly

    def index_html(self, nav: str = "") -> bytes:
        """The character pages; `nav`: the reader's pages in the bar (bar.links)."""
        html = (self.static / "index.html").read_text(encoding="utf-8")
        if self.view == "public":
            html = FULL_BLOCK.sub("", html)
        if self.model3d is None:
            html = MODEL3D_BLOCK.sub("", html)
        # The tab icon from where no login stands in the way (bar.ICON).
        html = bar.fill(html, "./", nav).replace('rel="icon" href="favicon.svg"', f'rel="icon" href="{bar.ICON}"', 1)
        return og_tags(html, self.default_header()).replace("@VIEW@", self.view).encode()

    def default_header(self) -> dict | None:
        """The page's own character's header (as this view may show it), for
        the og tags; None when the store can't answer."""
        try:
            name = self.settings.default_char if self.settings.default_char not in self.hidden() else ""
            name = name or next(iter(c["char"] for c in self.chars()["chars"]), "")
            return self.char(name)["header"] if name else None
        except Exception:  # noqa: BLE001 - a link preview is never worth a failed page
            return None

    def title(self, name: str) -> str | None:
        """The character's title line (views.card_title), as the page shows it."""
        try:
            return (self.char(name).get("header") or {}).get("title")
        except Exception:  # noqa: BLE001
            return None


def og_tags(html: str, header: dict | None) -> str:
    """The link preview: og:title "<name> · <title line>", the same string
    the page puts in its window title (views.card_title)."""
    if not header or not header.get("name"):
        return html
    t = header.get("title")
    title = f"{header['name']} · {t}" if t else str(header["name"])
    tags = (f'<meta property="og:type" content="profile">\n'
            f'<meta property="og:title" content="{escape(title)}">\n')
    if t:
        tags += f'<meta property="og:description" content="{escape(t)}">\n'
    return html.replace("</head>", tags + "</head>", 1)


INSTALL_CODE_PATH = re.compile(r"/install/(?!update\b)[^/]+")
# The shipper's auto-update: its version and files (wowstats.account.handle_shipper).
SHIPPER_UPDATE = "/install/update/shipper"
ASSETS = "/install/assets/"
# The item facts the shipper reads from the game's hotfix cache (wowstats.itemfacts).
ITEMS = "/install/items/"
# web/stats.js on the full view: under /privacy/ with the privacy note's
# other files, the only paths the login proxy lets through before a login
# (with /install/), so the privacy note counts its visits too.
STATS_JS = "/privacy/stats.js"
# The tab icon (web/favicon.svg), any view, before any login (bar.ICON).
ICON_PATHS = ("/favicon.ico", "/favicon.svg", bar.ICON)


def stats_tags(stats: dict, src: str, path: str) -> str:
    """The two script tags of a Plausible pageview (wowstats.publish
    analytics()): stats.js first, which queues the pageview with `path` as
    its address, then Plausible's manual script, which sends it. `path` is
    the page's path alone, percent-encoded: never the query, never the
    #fragment, and an install code (a bearer for 15 minutes) never."""
    path = INSTALL_CODE_PATH.sub("/install/code", path)[:200]
    path = urllib.parse.quote(path, safe="/-._~") or "/"
    return (f'<script src="{src}" data-path="{path}" defer></script>\n'
            f'<script src="{stats["script"]}" data-domain="{stats["domain"]}" data-api="{stats["api"]}" defer>'
            "</script>\n")


def og_generic(html: str) -> str:
    """The link preview of a sealed page: the page's own generic title and
    nothing about whose it is."""
    tags = ('<meta property="og:type" content="website">\n'
            '<meta property="og:title" content="Leveling log">\n')
    return html.replace("</head>", tags + "</head>", 1)


def make_handler(root: App, tenancy=None):
    """One App (one person), or a tenancy front (wowstats.tenancy): then the
    login says who is asking and each request runs on that person's App."""
    class Handler(BaseHTTPRequestHandler):
        server_version = "wow-stats"
        sys_version = ""

        def log_message(self, fmt, *args):  # one line per request, no query strings
            # An install code is a bearer for 15 minutes: never in the log.
            path = INSTALL_CODE_PATH.sub("/install/<code>", urllib.parse.urlsplit(self.path).path)
            sys.stdout.write(f"{self.command} {path} {args[1] if len(args) > 1 else ''}\n")
            sys.stdout.flush()

        # Helpers the optional MCP module may use too.
        def send(self, code: int, body: bytes, ctype: str, cache: str = "no-store", headers: dict | None = None,
                 csp: str = CSP, stats: bool = True):
            """`stats`: every HTML page of the full view gets the Plausible
            pageview when PLAUSIBLE_DOMAIN is set (App.stats); False for a
            page inside another one (the 3D viewer's frame), which would
            count the visit twice."""
            if stats and root.stats is not None and ctype.startswith("text/html") and b"</head>" in body:
                tags = stats_tags(root.stats, STATS_JS, urllib.parse.urlsplit(self.path).path)
                body = body.replace(b"</head>", tags.encode() + b"</head>", 1)
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("Content-Security-Policy", csp)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Robots-Tag", "noindex, nofollow")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def json(self, code: int, obj) -> None:
            self.send(code, json.dumps(obj, separators=(",", ":")).encode(), "application/json")

        def not_found(self) -> None:
            """404. A person's browser on the full view gets a page with the
            way back (their page, Start, Help); scripts, the API and the
            MCP clients keep the short plain answer."""
            path = urllib.parse.urlsplit(self.path).path
            if (tenancy is not None and root.view == "full" and self.command in ("GET", "HEAD")
                    and "text/html" in (self.headers.get("Accept") or "") and not path.startswith("/api/")):
                from wowstats import account

                return self.send(404, account.not_found_page(), "text/html; charset=utf-8")
            self.send(404, b"not found\n", "text/plain")

        def read_body(self, limit: int) -> bytes | None:
            try:
                n = int(self.headers.get("Content-Length") or "-1")
            except ValueError:
                n = -1
            if n < 0 or n > limit:
                return None
            return self.rfile.read(n)

        def _authorized(self, expected: bytes | None) -> bool:
            if expected is None:
                return True
            got = (self.headers.get("Authorization") or "").encode()
            if hmac.compare_digest(got, expected):
                return True
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="wow-stats", charset="UTF-8"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            self._route()

        def do_POST(self):
            self._route()

        def do_DELETE(self):
            self._route()

        def do_OPTIONS(self):  # CORS preflight on the MCP and OAuth paths; anything else 404s
            self._route()

        def _route(self):
            url = urllib.parse.urlsplit(self.path)
            path = url.path
            qs = urllib.parse.parse_qs(url.query)
            app = root
            full = app.view == "full"
            try:
                if path == "/healthz" and self.command in ("GET", "HEAD"):
                    return self.send(200, b"ok\n", "text/plain")
                if not full and app.public_off():  # what the static server says for a site built with it off
                    return self.not_found()
                # /ingest and the shipper's update carry their own credentials,
                # which a rebinding page doesn't have; the shipper may know the
                # server by any name.
                shipper = (path == SHIPPER_UPDATE or path.startswith(SHIPPER_UPDATE + "/")
                           or path.startswith(ASSETS) or path.startswith(ITEMS))
                if path != "/ingest" and not shipper and not root.hosts.ok(self.headers.get("Host")):
                    return self.send(421, root.hosts.refusal(self.headers.get("Host")), "text/plain; charset=utf-8")
                if (path != "/ingest" and not shipper and app.auth_mode == "open" and full
                        and not app.open_on_purpose and guard.proxied(self.headers)):
                    return self.send(403, guard.PROXIED, "text/plain; charset=utf-8")
                if path == "/ingest":
                    if self.command != "POST":
                        return self.not_found()
                    if tenancy is not None:
                        return self._ingest_tenant()
                    if app.ingest_expected is None:
                        return self.not_found()
                    if not self._authorized(app.ingest_expected):
                        return None
                    return self._ingest(app.store.vl_url, app.store.account_id)
                if path.startswith(ITEMS) and full:  # item facts: the upload password and budget of /ingest
                    allows = spend = None
                    target = app
                    if tenancy is not None:
                        u = self._ingest_user()
                        if u is None:
                            return None
                        target = tenancy.app_for(u)
                        allows = lambda n, s=u.slug: tenancy.ingest_budget.allows(s, n)  # noqa: E731
                        spend = lambda n, s=u.slug: tenancy.ingest_budget.spend(s, n)  # noqa: E731
                    elif app.ingest_expected is None:
                        return self.not_found()
                    elif not self._authorized(app.ingest_expected):
                        return None
                    return target.item_facts.handle(self, path[len(ITEMS):], qs, allows, spend)
                if path.startswith(ASSETS):  # the 3D view's files: the upload password and budget of /ingest
                    allows = spend = None
                    target = app
                    if tenancy is not None:
                        u = self._ingest_user()
                        if u is None:
                            return None
                        target = tenancy.app_for(u)
                        allows = lambda n, s=u.slug: tenancy.ingest_budget.allows(s, n)  # noqa: E731
                        spend = lambda n, s=u.slug: tenancy.ingest_budget.spend(s, n)  # noqa: E731
                    elif app.ingest_expected is None:
                        return self.not_found()
                    elif not self._authorized(app.ingest_expected):
                        return None
                    if target.assets is None:
                        return self.not_found()
                    return target.assets.handle(self, path[len(ASSETS):], qs, allows, spend)
                if shipper:  # the upload password, like /ingest: the same installs, no page login
                    if self.command not in ("GET", "HEAD"):
                        return self.not_found()
                    if tenancy is not None:
                        if self._ingest_user() is None:
                            return None
                    elif app.ingest_expected is None:
                        return self.not_found()
                    elif not self._authorized(app.ingest_expected):
                        return None
                    from wowstats import account

                    return account.handle_shipper(self, path[len(SHIPPER_UPDATE):])
                if tenancy is not None and path.startswith("/install/") and self.command in ("GET", "HEAD"):
                    from wowstats import account

                    return account.handle_install(self, tenancy, path[len("/install/"):])
                if path in ICON_PATHS and self.command in ("GET", "HEAD"):
                    # Before any login: browsers ask for the icon without being
                    # told to, and a 401 there is noise on every page.
                    return self.send(200, (root.static / "favicon.svg").read_bytes(), "image/svg+xml",
                                     "public, max-age=86400")
                if path == STATS_JS and root.stats is not None and self.command in ("GET", "HEAD"):
                    # Before any login: the privacy note loads it too.
                    return self.send(200, (root.static / "stats.js").read_bytes(), "text/javascript; charset=utf-8",
                                     "public, max-age=3600")
                if tenancy is not None and path == "/privacy" and self.command in ("GET", "HEAD"):
                    from wowstats import account

                    page = account.privacy_page(self._via_proxy() and bool(self.headers.get(tenancy.user_header)),
                                                tenancy.env)
                    return self.send(200, page, "text/html; charset=utf-8", "no-cache")
                if tenancy is not None and path.startswith("/privacy/") and self.command in ("GET", "HEAD"):
                    from wowstats import account

                    hit = account.privacy_asset(app.static, path[len("/privacy/"):])
                    if hit is None:
                        return self.not_found()
                    return self.send(200, hit[0], hit[1], "public, max-age=3600")
                if not self._authorized(app.auth_expected):
                    return None
                if root.proxy is not None and not self._via_proxy():
                    return self.send(403, b"This server takes requests through its login proxy only.\n",
                                     "text/plain; charset=utf-8")
                if tenancy is not None:
                    who = tenancy.from_headers(self.headers)
                    if who.user is None:
                        return self._refused(who.status, who.why, path)
                    me, app = who.user, tenancy.app_for(who.user)
                    tenancy.activity.seen(me, "full")
                    from wowstats import account

                    if account.route(self, tenancy, me, app, path):
                        return None
                    if path == "/" and self.command in ("GET", "HEAD") and not me.slug:
                        return self.send(302, b"", "text/plain", headers={"Location": "/start"})
                if full and path == "/api/plan" and self.command == "POST":
                    from wowstats.plan import http as plan_http

                    plan_http.handle(self, app.store, app.env)
                    # A new plan changes the plan panel's review: built again now.
                    return app.prebuilt.changed("plan:")
                if app.model3d is not None and app.model3d.owns(path):
                    return app.model3d.handle(self, app, path, qs)
                if self.command not in ("GET", "HEAD"):
                    return self.not_found()
                if full and app.door and path in (app.door, app.door.rstrip("/")):
                    # The login door (FULL_DOOR_PATH): whatever logged the
                    # visitor in sent them here; the full home page is at /.
                    return self.send(302, b"", "text/plain", headers={"Location": "/"})
                if path in STATIC:
                    name, ctype = STATIC[path]
                    if name != "index.html":
                        return self.send(200, (app.static / name).read_bytes(), ctype, "no-cache")
                    # The person's own pages (wowstats.account), with several people.
                    html = app.index_html(bar.links("/") if tenancy is not None else "")
                    return self.send(200, html, ctype, "no-cache", csp=CSP_3D if app.model3d is not None else CSP)
                if path in FONTS:
                    return self.send(200, (app.static / FONTS[path]).read_bytes(), "font/woff2",
                                     "public, max-age=604800")
                if path == "/api/config":
                    return self.json(200, app.features())
                if path == "/api/items":
                    from wowstats import itemcard

                    ids = [int(x) for x in qs.get("ids", [""])[0].split(",")[:400] if x.isdigit() and len(x) < 9]
                    return self.json(200, {"items": [{"id": int(k), **v}
                                                     for k, v in app.item_cards(ids).items()]})
                m = ICON_PATH.match(path) if full else None
                if m:
                    from wowstats import itemcard

                    body = itemcard.shared().icon(m.group(2), m.group(1))
                    if body is None:
                        return self.not_found()
                    return self.send(200, body, "image/jpeg", "public, max-age=604800")
                m = RENDER_PATH.match(path)
                if m:
                    name = urllib.parse.unquote(m.group(1))
                    body = app.render(name) if CHAR_RE.match(name) else None
                    if body is None:
                        return self.not_found()
                    # A new still (new gear) shows within 5 minutes.
                    return self.send(200, body, "image/webp", "private, max-age=300")
                if path == "/api/chars":
                    return self.json(200, app.api_chars())
                if path == "/api/news" and full:
                    name = qs.get("name", [""])[0]
                    week = qs.get("week", [""])[0]
                    if (name and not CHAR_RE.match(name)) or (week and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", week)):
                        return self.json(400, {"error": "bad character name or week"})
                    return self.json(200, app.news(name if name and app.known(name) else None, week or None))
                if path == "/api/gear" and full:
                    name = qs.get("name", [""])[0]
                    spec = qs.get("spec", [""])[0].strip().lower() or None
                    if not CHAR_RE.match(name):
                        return self.json(400, {"error": "bad character name"})
                    if spec is not None and not SPEC_RE.match(spec):
                        return self.json(400, {"error": "bad spec"})
                    if not app.known(name):
                        return self.json(404, {"error": f"no events for {name}"})
                    return self.json(200, app.gear(name, spec))
                if path in ("/api/overview", "/api/timeline", "/api/snapshot", "/api/people", "/api/loot",
                            "/api/dungeons", "/api/meta", "/api/talents"):
                    name = qs.get("name", [""])[0]
                    if not CHAR_RE.match(name):
                        return self.json(400, {"error": "bad character name"})
                    if path in ("/api/people", "/api/overview", "/api/loot", "/api/dungeons", "/api/meta",
                                "/api/talents") and not full:
                        return self.not_found()  # full view only (redact.PUBLIC)
                    if not app.known(name):
                        return self.json(404, {"error": f"no events for {name}"})
                    if path == "/api/overview":
                        return self.json(200, app.overview(name))
                    if path == "/api/timeline":
                        return self.json(200, app.timeline(name))
                    if path == "/api/loot":
                        return self.json(200, app.loot(name))
                    if path == "/api/dungeons":
                        return self.json(200, app.dungeons(name))
                    if path == "/api/meta":
                        return self.json(200, app.meta(name))
                    if path == "/api/talents":
                        return self.json(200, app.talent_history(name))
                    if path == "/api/people":
                        return self.json(200, app.people(name))
                    lv = qs.get("level", [""])[0]
                    if not lv.isdigit() or not 1 <= int(lv) <= 60:
                        return self.json(400, {"error": "level is 1 to 60"})
                    return self.json(200, app.snapshot(name, int(lv)))
                if path == "/api/char" or (path == "/api/journal" and full):
                    name = qs.get("name", [""])[0]
                    if not CHAR_RE.match(name):
                        return self.json(400, {"error": "bad character name"})
                    if not app.known(name):
                        return self.json(404, {"error": f"no events for {name}"})
                    if path == "/api/journal":
                        return self.json(200, app.api_journal(name))
                    return self.json(200, app.api_char(name))
                if not full:
                    return self.not_found()
                if path == "/api/analysis":
                    from wowstats import analysis

                    name = (qs.get("name", [""])[0] or "").replace(" ", "-")
                    if not CHAR_RE.match(name):
                        return self.json(400, {"error": "bad character name"})
                    return self.json(200, analysis.answer(app.analysis_env, name, "full"))
                if path == "/api/plan":  # the mount goal and the last plan against what happened
                    name = (qs.get("char", [""])[0] or app.settings.default_char or "").replace(" ", "-")
                    if not CHAR_RE.match(name):
                        return self.json(400, {"error": "bad character name"})
                    if not app.known(name):
                        return self.json(404, {"error": f"no events for {name}"})
                    return self.json(200, app.plan(name))
                if path == "/api/market":
                    return self.json(200, app.market())
                if path == "/api/prices":
                    return self.json(200, app.prices())
                if path == "/api/logsql":
                    try:
                        rows = app.views.logsql(qs.get("q", [""])[0])
                    except ValueError as e:
                        return self.json(400, {"error": str(e)})
                    return self.json(200, {"rows": rows})
                return self.not_found()
            except FullOnly:
                return self.not_found()
            except SourceError as e:
                return self.json(502, {"error": public_error(e, path)})
            except Exception as e:  # noqa: BLE001 - the page shows it; the log keeps it
                sys.stdout.write(f"error on {path}: {e!r}\n")
                return self.json(500, {"error": "the server hit a bug; see its log"})

        def _via_proxy(self) -> bool:
            """From the login proxy (AUTH=proxy: wowstats.guard.ProxyTrust)."""
            return root.proxy is None or root.proxy.trusted(self.client_address[0], self.headers)

        def _refused(self, status: int, why: str, path: str):
            """A login the app doesn't take: no data, a short page saying why."""
            if path.startswith("/api/"):
                return self.json(status, {"error": why})
            from wowstats import account

            return self.send(status, account.refused_page(why), "text/html; charset=utf-8", "no-store")

        def _ingest_user(self):
            """The person whose upload password (basic auth: slug and ingest
            token, wowstats.tenancy) the request carries; None after a 401."""
            raw = (self.headers.get("Authorization") or "")
            user = pw = ""
            if raw.lower().startswith("basic "):
                try:
                    user, _, pw = base64.b64decode(raw[6:].strip()).decode("utf-8").partition(":")
                except (ValueError, UnicodeDecodeError):
                    user = pw = ""
            u = tenancy.registry.check_ingest(user, pw) if user else None
            if u is None:
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="wow-stats", charset="UTF-8"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            return u

        def _ingest_tenant(self):
            """POST /ingest with several people: the person's events go to
            their own tenant."""
            u = self._ingest_user()
            if u is None:
                return None
            from wowstats import ingest

            n = ingest.length(self.headers)
            if not tenancy.ingest_budget.allows(u.slug, n):  # review L7: one person can't fill the store
                return self.json(429, {"error": "today's upload budget is used up; the rest goes tomorrow"})
            app = tenancy.app_for(u)

            def stored(lines: int) -> None:
                tenancy.ingest_budget.spend(u.slug, n)
                tenancy.activity.uploaded(u, lines)
                if not u.first_event:
                    tenancy.registry.update(u.sub, first_event=datetime.now(UTC).isoformat(timespec="seconds"))

            return self._ingest(app.store.vl_url, app.store.account_id, stored)

        def _ingest(self, vl_url: str, account_id: int, stored=None):
            """The shipper's NDJSON, screened line by line (wowstats.ingest,
            a shared receiver's rules), then handed to VictoriaLogs' jsonline
            API (which answers 200 and stores nothing unless the content type
            is application/stream+json), in the sender's tenant. `stored(n)`
            runs once VictoriaLogs has taken the n lines, before the 200: a
            refused write leaves no upload, budget or first event behind
            (review 2026-11-21, D)."""
            from wowstats import ingest

            body = self.read_body(INGEST_MAX_BYTES)
            if body is None:
                return self.json(413, {"error": f"send a Content-Length of at most {INGEST_MAX_BYTES} bytes"})
            lines, bad = ingest.parse(body)
            if bad:  # all or nothing: the shipper keeps the file and moves bad lines aside
                return self.json(400, {"error": f"{bad} lines are not wow-stats events"})
            if lines:
                q = urllib.parse.urlencode({"_stream_fields": ingest.STREAM_FIELDS, "_time_field": "_time",
                                            "_msg_field": "_msg"})
                req = urllib.request.Request(f"{vl_url}/insert/jsonline?{q}", data=b"\n".join(lines) + b"\n",
                                             method="POST", headers={"Content-Type": "application/stream+json",
                                                                     **tenant_headers(account_id)})
                vl = urllib.parse.urlsplit(vl_url)  # host and port only: a user:password@ stays out of the log
                at = f"VictoriaLogs at {vl.hostname}{f':{vl.port}' if vl.port else ''}"  # the log's, never the reply's
                try:
                    with urllib.request.urlopen(req, timeout=60) as r:
                        r.read()
                except urllib.error.HTTPError as e:
                    return self.json(502, {"error": public_error(f"{at} answered {e.code}", "/ingest")})
                except (urllib.error.URLError, OSError) as e:
                    return self.json(502, {"error": public_error(f"{at} unreachable: {e}", "/ingest")})
                if stored is not None:
                    stored(len(lines))
                prebuilt.soon()  # the page catches up with the start page's "It works"
            return self.json(200, {"accepted": len(lines)})

    return Handler


class Server(httpserve.Server):
    """Timeouts and a connection cap (wowstats.httpserve, review L8)."""


def serve(app: App, tenancy=None) -> ThreadingHTTPServer:
    srv = Server((app.bind, app.port), make_handler(app, tenancy))
    srv.daemon_threads = True
    return srv


def _newest(app: App) -> str | None:
    """The newest event's time, as the analysis records it (meta.last_record)."""
    rows = app.views.vl(f"{app.views.stream} | stats max(_time) as t")
    t = max((r.get("t") or "" for r in rows), default="")
    return parse_t(t).astimezone(UTC).isoformat(timespec="seconds") if t else None


def tenancy_of(env: dict, app: App):
    """WOW_STATE_DIR turns on several people (wowstats.tenancy): full view
    behind a login proxy only, and the MCP built with the registry's checks."""
    if not (env.get("WOW_STATE_DIR") or "").strip():
        return None
    if app.view != "full" or app.auth_mode != "proxy":
        raise ConfigError("WOW_STATE_DIR (several people) needs VIEW=full and AUTH=proxy: the login proxy says who "
                          "is asking.")
    from wowstats import tenancy as T

    if app.mcp is not None and (env.get("MCP_AUTH") or "oauth").strip().lower() == "none":
        raise ConfigError("WOW_STATE_DIR (several people) with MCP_AUTH=none: the MCP couldn't tell who is asking. "
                          "Use MCP_AUTH=oauth.")
    t = T.Tenancy(env)
    if app.mcp is not None:
        app.mcp.attach_tenancy(t)
    return t


def prebuilt_apps(app: App, tenancy=None) -> list[App]:
    """Whose answers the builder keeps (wowstats.prebuilt): this process'
    App, or with several people every active person's, the owner first."""
    if tenancy is None:
        return [app]
    users = sorted((u for u in tenancy.registry.users() if u.active), key=lambda u: not u.owner)
    return [tenancy.app_for(u) for u in users]


def main() -> int:
    env = dict(os.environ)
    try:
        app = App(env)
        tenancy = tenancy_of(env, app)
    except ConfigError as e:
        print(e.code, file=sys.stderr)
        return 2
    srv = serve(app, tenancy)
    if tenancy is not None:
        from wowstats import tenancy as T

        if T.start_reconcile(tenancy, on_change=prebuilt.WAKE.set) is not None:
            print(f"friends: the list in {tenancy.friends_file}, watched every {T.WATCH_S} s (a full pass every "
                  f"{env.get('FRIENDS_RECONCILE_S') or '120'} s)", flush=True)
    # The answers a plain open asks for, built in the background once per new
    # data (wowstats.prebuilt): each person's, the owner first.
    prebuilt.start(lambda: prebuilt_apps(app, tenancy))
    if app.mcp is not None:
        # FastMCP on its own port (wowstats.mcp): a bad OIDC issuer stops the
        # start here, like a bad setting.
        try:
            run = app.mcp.start()
        except SystemExit as e:
            print(e.code, file=sys.stderr)
            return 2
        print(f"mcp: on {app.bind}:{run.port}, path {app.mcp.path}", flush=True)
    if app.view == "full":
        from wowstats import analysis

        # The nightly recheck (no model): with several people, on the owner's
        # App and environment only, so one process never rereads every
        # person's history at once (vl-wow, 1 GiB). A friend's kept findings
        # are checked again when their own client runs the analysis.
        own = tenancy.app_for(tenancy.registry.get(tenancy.owner_sub)) if tenancy else app
        own_env = own.env if tenancy else env
        if analysis.start_schedule(own_env, lambda: _newest(own)) is not None:
            print("analysis: nightly recheck on, state in " + str(analysis.state_dir(own_env)), flush=True)
        from wowstats.analysis import auto

        # The server's own analysis (a model the host configured): each
        # person's rebuild checks their played-time timer, the run goes on
        # in the background (wowstats.analysis.auto).
        cfg = auto.service().cfg
        if cfg.on:
            if cfg.auto:
                prebuilt.ON_BUILD.append(
                    lambda a: a.analysis_env.get("ANALYSIS_STATE_DIR") and auto.service().on_rebuild(a.env))
            print(f"analysis: the server's own runs on {cfg.provider}:{cfg.model} ({cfg.auth}), "
                  + (f"after {auto.hours(cfg.stale_s)} of play" if cfg.auto else "off (ANALYSIS_AUTO)")
                  + (f", budget ${cfg.budget:g} a month" if cfg.priced else ", cost unknown (no prices)")
                  + (f", {cfg.tokens_cap:,} tokens a month" if cfg.tokens_cap else ""), flush=True)
        try:
            share = analysis.share_server(env, tenancy)
        except guard.GuardError as e:
            print(e.code, file=sys.stderr)
            return 2
        if share is not None:
            print(f"analysis: the share port on {share.server_address[0]}:{share.server_address[1]}, token in "
                  f"{guard.share_token_path(env)}", flush=True)
    extras = [n for n, m in (("mcp", app.mcp), ("3d", app.model3d)) if m is not None]
    print(f"wow-stats ui on {app.bind}:{app.port}: view {app.view}, auth {app.auth_mode}, "
          f"hosts {', '.join(sorted(app.hosts.names)) or 'loopback and IP addresses only'}, "
          f"ingest {'on' if app.ingest_expected else 'off'}, logs {app.store.vl_url} "
          f"(archive {app.store.vl_archive_url or 'off'}), metrics {app.store.vm_url or 'off'}"
          + (f", with {', '.join(extras)}" if extras else ""), flush=True)
    srv.serve_forever()
    return 0
