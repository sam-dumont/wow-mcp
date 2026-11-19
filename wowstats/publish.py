"""`wow-stats publish`: the sites friends see, as static files.

Two bundles from one pass over the stores, written side by side:

  public/   the public view (wowstats.redact), served at /
  zogzog/   the full content (other players, gold, prices, people, the
            timeline, 3D), served at ZOGZOG_PATH (default /zogzog/), with
            every data file sealed: AES-256-GCM under ZOGZOG_KEY_FILE, a
            random 96-bit nonce per file and the file's path as associated
            data. The page takes the key from the link's fragment
            (/zogzog/#<key>), which browsers never send to a server.

No Ask links, MCP, LogsQL box or write endpoint in either: those live only on
the dynamic full view behind its login. The 3D files (Wowhead's viewer,
models and textures, public game assets) are fetched once through the
relay's allow-list and cache (wowstats.model3d) and copied in, in clear.

Rebuilt when new events arrived (the newest event's time moved), when the
Battle.net data in vm-wow changed (the exporter refreshes each profile,
look and equipment once a day, with or without a game session: a hash of
them, Store.profile_fingerprint), or when the bundle is older than
MAX_AGE_S whatever happened: Blizzard's API terms keep Battle.net data (the
look from the profile API, the auction house) at most 30 days, and a daily
rebuild keeps every copy in the bundle far inside that.

Each build is written in <out>/.build-<n>/, renamed to <out>/b<n>/ when
complete, and <out>/current is swapped to it in one rename; older builds
are removed. A failed build removes its own folder; one a killed run left
behind goes at the next run, once nothing in it changed for STALE_BUILD_S
(sweep_builds). The static server (wowstats.static) reads
<out>/current. Environment: as `wow-stats ui` (VL_URL, VL_ARCHIVE_URL,
VM_URL, MODEL3D_CACHE_DIR...), plus PUBLISH_DIR, PUBLISH_INTERVAL (600),
ZOGZOG_PATH (/zogzog/, empty: no zogzog bundle), ZOGZOG_KEY_FILE,
ZOGZOG_LINK_BASE (the site's address, for `zogzog-key link`),
PUBLISH_PUSH_URL and PUBLISH_PUSH_TOKEN_FILE (push each build to a
static server's upload port instead of sharing a volume: wowstats.static;
every address the name resolves to gets it, so with a headless Service each
pod does), PLAUSIBLE_DOMAIN (off when empty), PLAUSIBLE_SCRIPT and
PLAUSIBLE_API (see analytics()), ANALYSIS_SHARE_URL (the full view's
/zogzog/ analysis lines, wowstats.analysis.share_server; empty: none),
HELP_KEY_FILE and HELP_MCP_URL (the MCP help page, /help/#<key>, sealed under
its own key: wowstats.mcp.helppage; unset or no such file: no help page),
RENDER_URL, RENDER_BUDGET_S and RENDER_TIMEOUT_S (each past level's picture,
from the renderer: wowstats.pictures; empty: past levels show the 3D model).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import socket
import sys
import tarfile
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from wowstats import __version__, armory, forever, itemcard, names, pictures, redact, talents, ui
from wowstats import model3d as m3d

MAX_AGE_S = 86400
KEEP = 2  # builds kept besides current, for a request in flight during the swap
# A .build-* folder nothing wrote to for this long belongs to a run that died
# (killed, out of memory, the container stopped): a live build writes files
# every few seconds, and its slowest step (a level's picture) times out in
# minutes. Another publisher's build on the same folder is younger and stays.
STALE_BUILD_S = 3600
ENC = ".enc"
FULL_BLOCK = ui.FULL_BLOCK
ASK = re.compile(r'\s*<section id="askbox".*?</section>', re.S)
DEBUG = re.compile(r'\s*<details id="debug".*?</details>', re.S)


class PublishError(RuntimeError):
    """A bundle that would show something broken: nothing is published."""


def log(*a) -> None:
    print(datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "publish:", *a, flush=True)


# ------------------------------------------------------------------ the key


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def read_key(path: str) -> bytes | None:
    try:
        raw = Path(path).read_text().strip()
    except OSError:
        return None
    key = unb64u(raw)
    if len(key) != 32:
        raise SystemExit(f"{path}: not a 256-bit key (43 base64url characters)")
    return key


def new_key(path: str) -> str:
    """A fresh 256-bit key, written 0600; returns it as the link's fragment."""
    k = b64u(secrets.token_bytes(32))
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(k + "\n")
    os.replace(tmp, p)
    return k


def seal(key: bytes, path: str, data: bytes) -> bytes:
    """nonce (12 bytes) + AES-256-GCM ciphertext and tag; `path` (the URL
    path inside the bundle, as the page asks for it) is the associated data,
    so a file can't be swapped for another."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(key).encrypt(nonce, data, path.encode())


def unseal(key: bytes, path: str, blob: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return AESGCM(key).decrypt(blob[:12], blob[12:], path.encode())


# ------------------------------------------------------------------ bundles


def url_part(s) -> str:
    """As the page's encodeURIComponent writes it."""
    return urllib.parse.quote(str(s), safe="-_.!~*'()")


def data_path(what: str, *parts) -> str:
    return "data/" + "/".join([what, *(url_part(p) for p in parts)]) + ".json"


class Bundle:
    def __init__(self, root: Path, key: bytes | None = None):
        self.root, self.key, self.files = root, key, 0
        self.item_ids: set[int] = set()
        self.unnamed: dict[str, set[int]] = {"item": set(), "quest": set()}

    def put(self, rel: str, body: bytes) -> None:
        """rel: the URL path inside the bundle; on disk, its decoded form,
        which must stay inside the bundle (names.inside: every bundle file
        is written here)."""
        try:
            p = names.inside(self.root, rel)
        except names.Escape as e:
            raise PublishError(str(e)) from None
        p.parent.mkdir(parents=True, exist_ok=True)
        if self.key is not None and rel.startswith("data/"):
            p = p.with_name(p.name + ENC)
            body = seal(self.key, rel, body)
        p.write_bytes(body)
        self.files += 1

    def json(self, rel: str, obj) -> None:
        self.item_ids |= itemcard.item_ids(obj)
        for kind, iid in unnamed(obj):
            self.unnamed[kind].add(iid)
        self.put(rel, json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode())


def unnamed(obj):
    """(kind, id) of every item or quest an answer shows by id alone: a
    dict with an "item_id" or a "quest_id" and no "name"."""
    if isinstance(obj, dict):
        for kind in ("item", "quest"):
            iid = obj.get(f"{kind}_id")
            if isinstance(iid, int) and iid > 0 and not obj.get("name"):
                yield kind, iid
        for v in obj.values():
            yield from unnamed(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from unnamed(v)


# ------------------------------------------------------------------ analytics

DOMAIN = re.compile(r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
# A path on the site's own origin: one leading slash, never two (//host is
# another origin), no scheme, nothing to escape.
OWN_PATH = re.compile(r"^/(?!/)[A-Za-z0-9._~/-]*$")


def analytics(env: dict) -> dict | None:
    """Plausible on the two static sites, off unless PLAUSIBLE_DOMAIN is set
    (a standalone install tracks nobody by default). Pageviews only, through
    Plausible's `manual` script: no hash routing, no outbound-link or file
    download events, and the page sends its address itself, as the bundle's
    path alone (stats.js): never the #fragment, where /zogzog/'s key lives,
    nor the query. PLAUSIBLE_SCRIPT and PLAUSIBLE_API are paths on the site's
    own origin, which your reverse proxy sends to Plausible (its
    /js/script.manual.js and /api/event): the page's CSP stays 'self' and the
    browser never calls a third party. Never in the live full view."""
    domain = env.get("PLAUSIBLE_DOMAIN", "").strip().lower()
    if not domain:
        return None
    script = env.get("PLAUSIBLE_SCRIPT", "/js/script.js").strip()
    api = env.get("PLAUSIBLE_API", "/api/event").strip()
    if not DOMAIN.match(domain):
        raise SystemExit(f"PLAUSIBLE_DOMAIN={domain!r}: a host name, as the site is named in Plausible")
    for name, val in (("PLAUSIBLE_SCRIPT", script), ("PLAUSIBLE_API", api)):
        if not OWN_PATH.match(val):
            raise SystemExit(f"{name}={val!r}: a path on this site (/js/script.js), never another host")
    return {"domain": domain, "script": script, "api": api}


def page(app: ui.App, sealed: bool, stats: dict | None = None, path: str = "/") -> bytes:
    """index.html for a static bundle: the 3D kept in both, the full-only
    sections kept in zogzog minus the Ask links and the query box. `path`: where
    the bundle is served, the only address the analytics ever send."""
    html = (app.static / "index.html").read_text(encoding="utf-8")
    if app.view == "public":
        html = FULL_BLOCK.sub("", html)
    else:
        html = DEBUG.sub("", ASK.sub("", html))
    if stats is not None:
        # stats.js first: it queues the pageview the deferred script sends.
        tags = (f'<script src="stats.js" data-path="{path}" defer></script>\n'
                f'<script src="{stats["script"]}" data-domain="{stats["domain"]}" data-api="{stats["api"]}" defer>'
                "</script>\n")
        html = html.replace("</head>", tags + "</head>", 1)
    attrs = 'data-static="1"' + (' data-sealed="1"' if sealed else "")
    return html.replace("@VIEW@", app.view).replace("<html ", f"<html {attrs} ", 1).encode()


def copy_static(app: ui.App, b: Bundle, stats: dict | None = None) -> None:
    web = app.static
    for name in ("app.js", "app.css", "items.js") + (("stats.js",) if stats is not None else ()):
        b.put(name, (web / name).read_bytes())
    for rel in ui.FONTS.values():
        b.put(rel, (web / rel).read_bytes())
    for name in m3d.OWN:
        b.put(f"model3d/{name}", (web / "model3d" / name).read_bytes())
    b.put("model3d/view.html", m3d.view_page(web / "model3d"))


# The files each bundle holds, by answer, and each answer cut down to its
# shape in wowstats.redact (PUBLIC, ZOGZOG): a new answer, or a new field in
# one, reaches a static site only once it is listed in both places. No
# "people" in either: other players stay in the live full view (/full/).
FILES = {
    "public": ("config", "chars", "char", "timeline", "snapshot", "model3d", "items"),
    "zogzog": ("config", "chars", "market", "prices", "char", "journal", "gear", "overview", "timeline", "loot",
               "dungeons", "snapshot", "model3d", "items", "analysis"),
}


def fetch_analysis(env: dict) -> dict:
    """The play analysis' shareable nudges per character from the full view
    (its share port): actions, each with where, what blocks it and the next
    step. Screened again here: a nudge with a time, a date, he or she or a
    number typed by hand doesn't reach the bundle, whatever the other side
    sent; pattern lines (descriptions) never do."""
    url = (env.get("ANALYSIS_SHARE_URL") or "").strip()
    if not url:
        return {"available": False}
    from wowstats.analysis import verify

    try:
        from wowstats import guard

        with guard.share_open(url, env) as r:
            a = json.loads(r.read(2_000_000))
    except (OSError, ValueError) as e:
        log(f"analysis: {e}")
        return {"available": False}
    if not isinstance(a, dict) or not a.get("available") or not isinstance(a.get("chars"), dict):
        return {"available": False}
    keep = lambda xs: [{k: n.get(k) for k in verify.SHARED_NUDGE} for n in xs or []  # noqa: E731
                       if isinstance(n, dict) and verify.shareable_action(n, set())[0]][:4]
    chars = {c: {**{k: v for k, v in x.items() if k != "patterns"}, "nudges": keep(x.get("nudges"))}
             for c, x in a["chars"].items() if isinstance(x, dict) and x.get("available") and x.get("char") == c}
    return {"available": bool(chars), "chars": chars}


def put_items(b: Bundle, tier: str) -> int:
    """The tooltips of every item the bundle's answers name, and their icons
    (both sizes) under data/icon/, sealed in /zogzog/ like every data file:
    which icons a bundle holds would list its loot. Icons come from the
    catalog's disk cache, fetched once by name. Returns the icons written.

    Every item gets an icon: its own, or the game's question mark for an
    item the client tables have no icon for (or don't have at all). An icon
    that can't be fetched fails the publish with the list: the page would
    show an empty frame (0.2.3: Forever's new items, whose icon is only on
    their appearance, came out blank and nothing said so)."""
    cat = itemcard.shared()
    ids = sorted(b.item_ids)
    cards = cat.many(ids)
    # Written even empty (no catalog, no items): the page always asks for it,
    # and a 404 in a bundle is a bug the browser test looks for.
    b.json("data/items.json", redact.TIERS[tier]("items", {"items": [{"id": int(k), **v} for k, v in cards.items()]}))
    if not getattr(cat, "on", True):
        return 0  # WOW_ITEMS=off: no cards, no icons, the page draws its own marks
    if ids and not cards and not getattr(cat, "cards", True):
        # No catalog at all (wago.tools down and nothing cached): every item
        # would come out as the question mark. Publish nothing, so the last
        # bundle, icons and all, stays up until the next try.
        raise PublishError(f"{tier}: the item catalog couldn't be read (wago.tools, no cached copy); the last "
                           f"bundle stays up")
    icon_of = {i: (cards.get(str(i)) or {}).get("i") or itemcard.QUESTION for i in ids}
    n, failed = 0, set()
    for name in sorted(set(icon_of.values()) | {itemcard.QUESTION}):
        for size in itemcard.SIZES:
            body = cat.icon(name, size)
            if body is None:
                failed.add(name)
                continue
            b.put(f"data/icon/{size}/{name}.jpg", body)
            n += 1
    no_card = [i for i in ids if str(i) not in cards]
    if no_card:
        log(f"{tier}: {len(no_card)} item(s) the client tables don't have, shown with the question mark: "
            + ", ".join(map(str, no_card)))
    if failed:
        bad = [f"{i} ({icon_of[i]})" for i in ids if icon_of[i] in failed]
        raise PublishError(f"{tier}: no icon for {len(bad)} item(s), the page would show empty frames: "
                           + ", ".join(bad or sorted(failed)))
    return n


def check_names(b: Bundle, tier: str) -> None:
    """Every item and quest the bundle shows has a name. One that a source
    names but the answer doesn't fails the publish (a view that skipped the
    lookup: 0.2.3's journal printed "quest 631"); one no source names is
    logged, and the page says "Unknown quest (631)" (an item: its slot,
    with its Wowhead link)."""
    cards = itemcard.shared().many(sorted(b.unnamed["item"])) if b.unnamed["item"] else {}
    items = [i for i in sorted(b.unnamed["item"]) if not (cards.get(str(i)) or {}).get("n")]
    quests = sorted(b.unnamed["quest"])
    missed = [q for q in quests if (forever.quest(q) or {}).get("name")]
    if missed:
        raise PublishError(f"{tier}: {len(missed)} quest(s) shown by id that forever-db names: "
                           + ", ".join(map(str, missed)))
    if quests:
        log(f"{tier}: {len(quests)} quest(s) no source names, shown as Unknown quest: " + ", ".join(map(str, quests)))
    if items:
        log(f"{tier}: {len(items)} item(s) no source names, shown by slot with a Wowhead link: "
            + ", ".join(map(str, items)))


def build_view(app: ui.App, full_app: ui.App, b: Bundle, chars: list[dict], m3d_files: set[str],
               stats: dict | None = None, path: str = "/", shared: dict | None = None,
               canonical: str = "", pics: pictures.Pictures | None = None) -> None:
    """Every answer the page asks for, saved where api() looks for it."""
    sealed = b.key is not None
    tier = "public" if app.view == "public" else "zogzog"
    files = FILES[tier]
    full = app.view == "full"
    # Every answer goes through its tier's shape here, the public ones a
    # second time (the App already cut them down): whatever is added between
    # the store and the file stays out.
    out = redact.TIERS[tier]

    def put(what: str, rel: str, fn) -> None:
        if what not in files:
            return
        try:
            b.json(rel, out(what, fn()))
        except Exception as e:  # noqa: BLE001 - a store that is down drops one section
            if what not in ("market", "prices"):
                raise
            log(f"{what}: {e}")

    head = None
    # The og tags: the bundle's own first character, through this tier's
    # shape. Never on a sealed page: its HTML is in clear, and a link preview
    # of /zogzog/ named the character with the public page off (outside
    # review, 2026-11-19). It gets the generic preview; the page sets its
    # window title once the data is open.
    if chars and not sealed:
        try:
            dc = app.settings.default_char
            name = dc if dc in {c["char"] for c in chars} else chars[0]["char"]
            head = out("char", app.char(name)).get("header")
        except Exception:  # noqa: BLE001
            head = None
    html = ui.og_tags(page(app, sealed, stats, path).decode(), head) if not sealed else \
        ui.og_generic(page(app, sealed, stats, path).decode())
    if canonical:  # the site's one address, fixed at build time (an alias host serves the same bundle)
        url = canonical.rstrip("/") + path
        html = html.replace("</head>", f'<link rel="canonical" href="{url}">\n'
                                       f'<meta property="og:url" content="{url}">\n</head>', 1)
    b.put("index.html", html.encode())
    copy_static(app, b, stats)
    feats = {**app.features(), "mcp": False, "debug": False, "static": True, "people": False,
             "players": False, "loot": full}
    put("config", data_path("config"), lambda: feats)
    # Only the bundle's own characters: one hidden by its player stays out of
    # the list too, not just out of the files.
    own = {c["char"] for c in chars}
    put("chars", data_path("chars"), lambda: {**{k: (v if k != "chars" else [c for c in v if c.get("char") in own])
                                                 for k, v in app.chars().items()}, "sources": app.store.sources()})
    if full and feats.get("market"):
        put("market", data_path("market"), lambda: app.views.market(app.settings.ah_faction))
    if full and feats.get("prices"):
        put("prices", data_path("prices"), app.views.prices)
    for c in chars:
        name = c["char"]
        put("char", data_path("char", name), lambda: {**app.char(name), "sources": app.store.sources()})
        if "char" in files:
            put_render(b, app, name)
        if "char" in files:
            copy_icons(b, talents.icon_names(app.char(name)))
        put("journal", data_path("journal", name), lambda: app.journal(name))
        put("gear", data_path("gear", name), lambda: app.gear(name, None))
        put("overview", data_path("overview", name), lambda: app.overview(name))
        tl = app.timeline(name)
        put("timeline", data_path("timeline", name), lambda: tl)
        put("loot", data_path("loot", name), lambda: app.loot(name))
        put("dungeons", data_path("dungeons", name), lambda: app.dungeons(name))
        # Every character gets the file (the page asks for it); those with a
        # kept analysis get their lines.
        sh = ((shared or {}).get("chars") or {}).get(name) or {"available": False}
        put("analysis", data_path("analysis", name), lambda: sh)
        if full_app.model3d is not None:
            ans = model_answer(full_app, name, None)
            put("model3d", data_path("model3d", name), lambda: ans)
            m3d_files |= m3d.viewer_files(full_app.model3d.relay, ans)
        for lv in tl.get("levels") or []:
            sn = app.snapshot(name, lv)
            if full_app.model3d is not None and sn.get("gear"):
                ans = model_answer(full_app, name, lv)
                put("model3d", data_path("model3d", name, lv), lambda: ans)
                m3d_files |= m3d.viewer_files(full_app.model3d.relay, ans)
                sn = {**sn, **put_picture(b, pics, name, lv, ans, full_app.model3d.stand_ins(ans))}
            put("snapshot", data_path("snapshot", name, lv), lambda: sn)
    if "items" in files:
        put_items(b, tier)
    check_names(b, tier)


def put_picture(b: Bundle, pics: pictures.Pictures | None, name: str, level: int, ans: dict,
                metas: dict | None = None) -> dict:
    """The level's picture (wowstats.pictures) at data/picture/<char>/<level>.webp,
    sealed in /zogzog/, and what the snapshot's answer says of it: drawn,
    pending, or nothing (no renderer, or nothing to draw: the page keeps the
    3D model)."""
    got = pics.get(ans, metas) if pics is not None else None
    if got is None:
        return {}
    if got == "pending":
        return {"picture": {"pending": True}}
    b.put(f"data/picture/{url_part(name)}/{int(level)}.{pictures.EXT}", got)
    return {"picture": {"ext": pictures.EXT}}


def put_render(b: Bundle, app: ui.App, name: str) -> bool:
    """The armory's full-body render (wowstats.armory) at
    data/render/<char>.<ext>, sealed in /zogzog/. Battle.net data: gone
    from vm-wow 30 days after its last refresh, so from the next build too."""
    r = app.store.character_armory(name).get("render")
    if not r:
        return False
    body = armory.fetch(r["url"])
    if not body or armory.kind(body) != armory.ext(r["url"]):
        log(f"render for {name}: not downloaded")
        return False
    b.put(f"data/render/{url_part(name)}.{armory.ext(r['url'])}", body)
    return True


def copy_icons(b: Bundle, names: set[str], size: str = "medium") -> int:
    """The talent frame's icons, fetched once here (wow.zamimg.com, like the
    3D files) so a visitor's browser never calls an outside site."""
    n = 0
    for name in sorted(names):
        try:
            body = talents.icon_bytes(name, size)
        except m3d.Upstream as e:
            log(f"icon {name}: {e}")
            continue
        if body:
            b.put(talents.icon_path(name, size), body)
            n += 1
    return n


def model_answer(full_app: ui.App, name: str, level: int | None) -> dict:
    try:
        return full_app.model3d.for_char(full_app, name, level)
    except m3d.Upstream as e:
        return {"model": None, "items": [], "missing": [], "why": f"the 3D files didn't come: {e}"}


def copy_3d(full_app: ui.App, b: Bundle, files: set[str]) -> int:
    n = 0
    for rel in sorted(files):
        if not m3d.RELAY.match(rel):
            continue
        try:
            body = full_app.model3d.relay.get(rel)
        except m3d.Upstream:
            body = None
        if body is not None:
            b.put(f"model3d/zam/{m3d.ZAM_REV}/{rel}", body)
            n += 1
    return n


# ------------------------------------------------------------------ push


def _push_req(base: str, token: str, method: str, build: str = "", body: bytes | None = None,
              slug: str = "") -> dict:
    q = urllib.parse.urlencode({k: v for k, v in (("slug", slug), ("build", build)) if v})
    url = base + "/bundle" + (f"?{q}" if q else "")
    req = urllib.request.Request(url, data=body, method=method, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def push_targets(url: str) -> list[str]:
    """One base URL per address PUBLISH_PUSH_URL's name resolves to. Behind a
    load-balanced Service the check and the push could each land on any pod,
    and a new pod stayed unready (404, no bundle) until chance sent it one
    (the 0.2.1 rollout); a headless Service lists every pod, ready or not,
    and each gets the build. https keeps the name: its certificate needs it."""
    u = urllib.parse.urlsplit(url.rstrip("/"))
    if u.scheme != "http" or not u.hostname:
        return [url.rstrip("/")]
    out: list[str] = []
    for *_, addr in socket.getaddrinfo(u.hostname, u.port or 80, type=socket.SOCK_STREAM):
        host = f"[{addr[0]}]" if ":" in addr[0] else addr[0]
        base = f"http://{host}:{addr[1]}{u.path}"
        if base not in out:
            out.append(base)
    return out


def push(env: dict, build_dir: Path, slug: str = "") -> None:
    """The build as a tar.gz to every static server that doesn't have it yet
    (each forgets everything on restart: its files live in an emptyDir). One
    that fails doesn't keep the others waiting; the first error is raised
    after all were tried, and the next pass tries again."""
    if not env.get("PUBLISH_PUSH_URL"):
        return
    token = Path(env["PUBLISH_PUSH_TOKEN_FILE"]).read_text().strip()
    tarball: bytes | None = None
    errors: list[Exception] = []
    for base in push_targets(env["PUBLISH_PUSH_URL"]):
        try:
            if _push_req(base, token, "GET", slug=slug).get("build") == build_dir.name:
                continue
            if tarball is None:
                buf = io.BytesIO()
                with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tf:
                    for p in sorted(build_dir.iterdir()):
                        tf.add(p, arcname=p.name)
                tarball = buf.getvalue()
            _push_req(base, token, "PUT", build_dir.name, tarball, slug=slug)
            log(f"pushed {build_dir.name} to {base} ({len(tarball) // 1024} KiB)")
        except (OSError, ValueError) as e:
            log(f"push to {base}: {e}")
            errors.append(e)
    if errors:
        raise errors[0]


# ------------------------------------------------------------------ one run


def profile_fingerprint(app: ui.App, before: str | None) -> str | None:
    """vm-wow's fingerprint; when it doesn't answer, the last one seen, so an
    outage neither forces a rebuild nor hides a change (the next pass that
    reaches it compares against the last build)."""
    try:
        return app.store.profile_fingerprint()
    except Exception as e:  # noqa: BLE001 - vm-wow down: judge on the events and the age alone
        log(f"vm-wow: {e}")
        return before


def newest_event(app: ui.App) -> str:
    rows = app.views.vl(f"{app.views.stream} | stats max(_time) as t")
    return max((r.get("t") or "" for r in rows), default="")


def safe_chars(chars: list[dict]) -> list[dict]:
    """The characters whose name can be a file name (names.valid_char).
    /ingest and the shipper refuse any other; one stored before
    that is skipped here and logged, never written."""
    out = []
    for c in chars:
        if names.valid_char(c.get("char")):
            out.append(c)
        else:
            log(f"skipped a character whose name can't be a file name: {str(c.get('char'))[:80]!r}")
    return out


def publish(env: dict | None = None, force: bool = False, *, zz_key: bytes | None = None, public: bool = True,
            hide_public: frozenset = frozenset(), hide_zogzog: frozenset = frozenset(), slug: str = "",
            canonical: str = "") -> dict:
    """One site. With several people (publish_all) each person's: their key
    (zz_key), whether the public page is on, the characters they hid from
    each bundle, their slug for the push and their site's address."""
    env = dict(os.environ if env is None else env)
    out = Path(env.get("PUBLISH_DIR", "/data/site"))
    out.mkdir(parents=True, exist_ok=True)
    sweep_builds(out)
    zz_path = env.get("ZOGZOG_PATH", "/zogzog/").strip()
    base = {**env, "BIND": "127.0.0.1", "AUTH": "none", "MCP_PUBLIC_URL": "", "MCP_AUTH": "",
            "INGEST_PASSWORD": "", "INGEST_PASSWORD_FILE": ""}
    full_app = ui.App({**base, "VIEW": "full"})
    pub_app = ui.App({**base, "VIEW": "public"})
    stats = analytics(env)
    pics = pictures.Pictures(env, out, log)
    # The page itself changes with a new version or analytics setting: rebuild
    # then too, not only on new data.
    page_id = hashlib.sha256(json.dumps([__version__, stats, zz_path, public, sorted(hide_public),
                                         sorted(hide_zogzog), canonical, pics.on],
                                        sort_keys=True).encode()).hexdigest()[:12]

    state_f = out / "state.json"
    try:
        state = json.loads(state_f.read_text())
    except (OSError, ValueError):
        state = {}
    newest = newest_event(full_app)
    profiles = profile_fingerprint(full_app, state.get("profiles"))
    shared = fetch_analysis(env) if zz_path else {"available": False}
    shared_id = hashlib.sha256(json.dumps(shared, sort_keys=True).encode()).hexdigest()[:12]
    age = time.time() - float(state.get("built_at", 0))
    if zz_path and zz_key is None:
        kf = env.get("ZOGZOG_KEY_FILE", str(out.parent / "zogzog.key"))
        zz_key = read_key(kf)
        if zz_key is None:
            zz_key = unb64u(new_key(kf))
            # Never the key or the link in a log (outside review, 2026-11-19:
            # the first key's whole link sat in the publisher's log).
            log(f"made a new key for the full-content link in {kf}; `wow-stats zogzog-key link` prints the link")
            force = True
    help_key = help_key_of(env)
    key_id = hashlib.sha256((zz_key or b"") + b"|" + (help_key or b"")).hexdigest()[:12] if zz_key or help_key else ""
    why = ("forced" if force else "no build yet" if not (out / "current").exists()
           else "new events" if state.get("newest") != newest
           else "Battle.net data changed" if state.get("profiles", "") != (profiles or "")
           else f"older than {MAX_AGE_S // 3600} h" if age >= MAX_AGE_S
           else "new key" if state.get("key_id") != key_id
           else "new version or page settings" if state.get("page_id") != page_id
           # A missing answer (the full view restarting) keeps the last build's lines.
           else "analysis changed" if shared.get("available") and state.get("analysis_id") != shared_id
           else "level pictures pending" if pics.on and state.get("pictures_pending")
           and time.time() >= float(state.get("pictures_retry_at") or 0) else None)
    if why is None:
        push(env, (out / "current").resolve(), slug)
        return {"built": False, "why": "no new events, no Battle.net change", "newest": newest}

    t0 = time.time()
    # Builds are named by the second; two in the same second (a forced one
    # right after another, or another publisher's build in progress) take the
    # next free number.
    n = int(t0)
    while (out / f"b{n}").exists() or (out / f".build-{n}").exists():
        n += 1
    build = out / f".build-{n}"
    try:
        chars = safe_chars(full_app.views.chars())
        files: set[str] = set()
        # The public page off: no public/ in the build (the static server answers
        # 404 there), the 3D files gathered in a scratch folder for zogzog.
        pub = Bundle(build / ("public" if public else ".scratch"))
        if public:
            build_view(pub_app, full_app, pub, [c for c in chars if c["char"] not in hide_public], files, stats, "/",
                       canonical=canonical, pics=pics)
        if help_key is not None and public:
            from wowstats.mcp import helppage

            helppage.build(pub, help_key, env.get("HELP_MCP_URL", "").strip() or env.get("MCP_PUBLIC_URL", "").strip())
        zz = None
        if zz_key is not None:
            zz = Bundle(build / "zogzog", zz_key)
            build_view(full_app, full_app, zz, [c for c in chars if c["char"] not in hide_zogzog], files, stats,
                       "/" + zz_path.strip("/") + "/", shared, canonical=canonical, pics=pics)
        n3d = 0
        if full_app.model3d is not None:
            n3d = copy_3d(full_app, pub, files)
            zam = pub.root / "model3d" / "zam"
            # No 3D file yet (an empty store on a fresh install, or no gear seen):
            # nothing to copy, and the build goes on.
            if zz is not None and zam.is_dir():
                shutil.copytree(zam, build / "zogzog" / "model3d" / "zam", dirs_exist_ok=True)
        shutil.rmtree(build / ".scratch", ignore_errors=True)
        (build / "BUILT").write_text(json.dumps({"at": datetime.now(UTC).isoformat(), "newest": newest}))
        final = out / f"b{n}"
        os.replace(build, final)
    except BaseException:
        # A failed build never takes the site down (current still points at
        # the last good one), and it gives its disk back now.
        shutil.rmtree(build, ignore_errors=True)
        raise
    tmp = out / ".current.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(final.name)
    os.replace(tmp, out / "current")
    old = sorted((p for p in out.glob("b*") if p.is_dir() and p != final), key=lambda p: p.name)
    for p in old[:-KEEP] if KEEP else old:
        shutil.rmtree(p, ignore_errors=True)
    pics.prune()
    state = {"newest": newest, "profiles": profiles or "", "built_at": t0, "key_id": key_id, "page_id": page_id,
             "build": final.name, "analysis_id": shared_id, **pics.state()}
    state_f.write_text(json.dumps(state))
    push(env, final, slug)
    res = {"built": True, "why": why, "build": final.name, "chars": len(chars), "public_files": pub.files,
           "zogzog_files": zz.files if zz else 0, "files_3d": n3d, "seconds": round(time.time() - t0, 1)}
    if pics.on:
        res["pictures"] = {"drawn": pics.drawn, "pending": len(pics.pending), "render_s": round(pics.spent, 1)}
    log(json.dumps(res))
    return res


def _last_write(p: Path) -> float:
    newest = p.lstat().st_mtime
    for q in p.rglob("*"):
        try:
            newest = max(newest, q.lstat().st_mtime)
        except OSError:  # removed under us: that build is alive
            return time.time()
    return newest


def sweep_builds(out: Path, now: float | None = None) -> list[str]:
    """Remove the .build-* folders a dead run left in `out` (nothing in them
    changed for STALE_BUILD_S). Never current's target or a b* bundle: only
    unfinished builds are named .build-*. Returns the names removed."""
    now = time.time() if now is None else now
    live = (out / "current").resolve() if (out / "current").exists() else None
    gone = []
    for p in sorted(out.glob(".build-*")):
        if p.is_symlink() or not p.is_dir() or p.resolve() == live:
            continue
        try:
            if now - _last_write(p) < STALE_BUILD_S:
                continue
        except OSError:
            continue
        shutil.rmtree(p, ignore_errors=True)
        gone.append(p.name)
    if gone:
        log(f"removed {len(gone)} abandoned build folder(s): {', '.join(gone)}")
    return gone


def help_key_of(env: dict) -> bytes | None:
    """The help page's own key (never zogzog's: one link opens one page).
    HELP_KEY_FILE unset, or no file there yet: no help page, and no failure."""
    kf = env.get("HELP_KEY_FILE", "").strip()
    if not kf:
        return None
    key = read_key(kf)
    if key is None:
        log(f"HELP_KEY_FILE {kf}: no such file, no help page (a key: 32 random bytes, base64url, 43 characters)")
    return key


def rotate(env: dict | None = None) -> int:
    """A new zogzog key: every old link stops working at the next publish,
    which this run does now."""
    env = dict(os.environ if env is None else env)
    out = Path(env.get("PUBLISH_DIR", "/data/site"))
    kf = env.get("ZOGZOG_KEY_FILE", str(out.parent / "zogzog.key"))
    new_key(kf)
    print(f"New key in {kf}: every old link stops working. `wow-stats zogzog-key link` prints the new one.",
          flush=True)
    publish(env, force=True)
    return 0


def link(env: dict | None = None) -> int:
    """The full-content link, on the terminal of whoever asks for it (never
    in a log): `wow-stats zogzog-key link`."""
    env = dict(os.environ if env is None else env)
    out = Path(env.get("PUBLISH_DIR", "/data/site"))
    kf = env.get("ZOGZOG_KEY_FILE", str(out.parent / "zogzog.key"))
    key = read_key(kf)
    if key is None:
        print(f"no key in {kf} yet: the publisher makes one on its first run", file=sys.stderr)
        return 1
    zz_path = env.get("ZOGZOG_PATH", "/zogzog/").strip() or "/zogzog/"
    sys.stdout.write(env.get("ZOGZOG_LINK_BASE", "").rstrip("/") + "/" + zz_path.strip("/") + "/#" + b64u(key) + "\n")
    return 0


def fetch_users(env: dict) -> list[dict]:
    """The people with a site, from the full view's share port (USERS_URL):
    wowstats.tenancy.Tenancy.users_json. No hashes or keys come over it."""
    from wowstats import guard

    with guard.share_open(env["USERS_URL"], env) as r:
        data = json.loads(r.read(4_000_000))
    users = data.get("users") if isinstance(data, dict) else None
    if not isinstance(users, list):
        raise PublishError("USERS_URL: no users list")
    return users


def user_env(env: dict, u: dict) -> dict:
    """One person's settings for a publish run: their tenant and store, their
    own folder, their analysis lines."""
    from wowstats.tenancy import SLUG_RE

    slug = str(u.get("slug") or "")
    if not SLUG_RE.match(slug):
        raise PublishError(f"bad slug {slug!r}")
    out = Path(env.get("PUBLISH_DIR", "/data/site"))
    e = {**env, "PUBLISH_DIR": str(out / slug), "VL_ACCOUNT_ID": str(int(u["account_id"]))}
    share = (env.get("ANALYSIS_SHARE_URL") or "").strip()
    if share:
        e["ANALYSIS_SHARE_URL"] = share + ("&" if "?" in share else "?") + urllib.parse.urlencode({"slug": slug})
    if u.get("store") != "owner":
        if not (env.get("FRIENDS_VL_URL") or "").strip():
            raise PublishError("FRIENDS_VL_URL is unset")
        e.update({"VL_URL": env["FRIENDS_VL_URL"], "VL_ARCHIVE_URL": "", "DEFAULT_CHAR": "", "WEIGHTS": "",
                  "HELP_KEY_FILE": "", "ZOGZOG_KEY_FILE": ""})
    return e


def user_key(env: dict, u: dict) -> bytes:
    """A person's /zogzog/ key from the registry's line (tenancy.person_key:
    the same one the full view puts in their link)."""
    from wowstats import tenancy

    key = tenancy.person_key(env, int(u["account_id"]), int(u.get("zogzog_gen") or 0), u.get("store") == "owner")
    if key is None:
        raise PublishError("ZOGZOG_MASTER_KEY_FILE is unset or unreadable")
    return key


def publish_all(env: dict | None = None, force: bool = False) -> list[dict]:
    """Every person's site (USERS_URL set): PUBLISH_DIR/<slug>/, sealed with
    their own key (user_key: the owner's ZOGZOG_KEY_FILE until his first new
    link, then like friends', derived from ZOGZOG_MASTER_KEY_FILE), public only when they turned it on, pushed with
    their slug. A slug that left the list loses its folder and its pushed
    copies (the person was deleted or switched off)."""
    from wowstats import tenancy

    env = dict(os.environ if env is None else env)
    out = Path(env.get("PUBLISH_DIR", "/data/site"))
    out.mkdir(parents=True, exist_ok=True)
    res, keep = [], set()
    for u in fetch_users(env):
        if u.get("status") != "active":
            continue
        slug = str(u.get("slug") or "")
        try:
            e = user_env(env, u)
            # The owner's first generation: None, so publish() reads (or
            # makes) ZOGZOG_KEY_FILE as before.
            key = None if u.get("store") == "owner" and not int(u.get("zogzog_gen") or 0) else user_key(env, u)
            keep.add(slug)
            r = publish(e, force, zz_key=key, public=bool(u.get("public_on")),
                        hide_public=frozenset(u.get("hidden_public") or ()),
                        hide_zogzog=frozenset(u.get("hidden_zogzog") or ()), slug=slug,
                        canonical=f"https://{u['canonical']}" if u.get("canonical") else "")
            res.append({"slug": slug, **r})
        except Exception as e:  # noqa: BLE001 - one person's store down doesn't stop the others
            log(f"{slug}: {e!r}")
            keep.add(slug)  # never drop a site on an error
            res.append({"slug": slug, "built": False, "why": f"failed: {e!r}"})
    for d in out.iterdir():
        if d.is_dir() and tenancy.SLUG_RE.match(d.name) and d.name not in keep:
            shutil.rmtree(d, ignore_errors=True)
            log(f"{d.name}: no longer in the list, site removed")
    unpush_others(env, keep)
    return res


def unpush_others(env: dict, keep: set[str]) -> None:
    """Every static pod drops the sites of people who left, asked for its
    own list: this pod's folders are an emptyDir, so after a restart it
    no longer knows who it pushed."""
    if not env.get("PUBLISH_PUSH_URL"):
        return
    token = Path(env["PUBLISH_PUSH_TOKEN_FILE"]).read_text().strip()
    for base in push_targets(env["PUBLISH_PUSH_URL"]):
        try:
            req = urllib.request.Request(base + "/bundles", headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=30) as r:
                held = json.loads(r.read()).get("slugs") or []
            for slug in sorted(set(held) - keep):
                _push_req(base, token, "DELETE", slug=slug)
                log(f"{slug}: removed from {base}")
        except (OSError, ValueError) as e:
            log(f"sites on {base}: {e}")


def main(argv: list[str]) -> int:
    loop = "--loop" in argv
    force = "--force" in argv
    interval = int(os.environ.get("PUBLISH_INTERVAL", "600"))
    while True:
        try:
            if os.environ.get("USERS_URL", "").strip():
                for r in publish_all(force=force):
                    if not r["built"]:
                        log(f"{r['slug']}: {r['why']}")
                if not loop:
                    return 0
                force = False
                time.sleep(interval)
                continue
            r = publish(force=force)
            if not r["built"]:
                log(r["why"])
        except Exception as e:  # noqa: BLE001 - one bad run waits for the next
            if not loop:
                raise
            log(f"failed: {e!r}")
        if not loop:
            return 0
        force = False
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
