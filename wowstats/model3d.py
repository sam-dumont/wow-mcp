"""The 3D character (full view only): Wowhead's model viewer, fed from here.

The page draws the character with Wowhead's ZamModelViewer, the WebGL viewer
behind wowhead.com's dressing room and the fan sites built on it
(github.com/Miorey/wow-model-viewer). Two things it can't do from the
browser, so this module does them:

- **Wowhead's files.** wow.zamimg.com refuses cross-origin requests from
  other sites (403 to any foreign Origin, no CORS headers), so the viewer
  can't fetch its models and textures from a page on another host. The
  server fetches them instead, from a fixed list of paths, and keeps them on
  disk: `/model3d/zam/<path>` is `https://wow.zamimg.com/modelviewer/classic/<path>`
  (the live data when classic has no such file). The browser only ever
  talks to this server, so the page's CSP keeps `'self'` everywhere (plus
  `blob:` images: the viewer decodes textures into blob URLs).
- **The look.** Forever's customization ids (Battle.net's appearance,
  Narcissus) are the modern ones: Wowhead's classic character metas use
  other ids, and a look matched against them fell back to every option's
  first choice (a Gnome priest, bald and beardless). The character metas come
  from the live data first (LIVE_FIRST), and a choice the live meta doesn't
  have under the same id (Forever's Facial Hair is Beard plus Mustache
  there) is matched by the geosets it shows, from Forever's
  ChrCustomizationElement and ChrCustomizationGeoset tables.
- **Display ids.** The viewer dresses a model by item display id, and the
  shipper sends item ids. Forever's own client tables (Item,
  ItemModifiedAppearance, ItemAppearance, published by wago.tools for the
  `wowf` product, build 1.60.1) give both the display id and the inventory
  type the viewer wants as the slot. Fetched once a day at most, kept in
  memory and on disk.
- **Forever's own weapons.** Forever added items with display ids no
  Wowhead data has (Spine of the Devourer, display 752138, 2026-11-19: the
  model was drawn with no weapon). Its ItemDisplayInfo still names a model
  Wowhead knows, with a texture (TextureFileData) Wowhead serves: the relay
  answers such a display's meta/item/ with the meta of another display of
  the same model, its texture swapped for Forever's (Model3D.item_meta).

Nothing is fetched until someone opens the 3D view: the module only answers
`/model3d/*` and `/api/model3d`, behind the page login.

Environment:
  MODEL3D            on (default) | off
  MODEL3D_CACHE_DIR  where the display-id table and Wowhead's files are kept
                     (default <tmp>/wow-stats-model3d; any writable folder)
  MODEL3D_CACHE_MB   disk budget for Wowhead's files (512); oldest go first
  MODEL3D_ZAM_URL, MODEL3D_WAGO_URL, MODEL3D_PRODUCT: the upstreams (tests)
"""

from __future__ import annotations

import base64
import concurrent.futures
import contextlib
import csv
import hashlib
import hmac
import io
import json
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from wowstats import __version__

# /model3d/view: the page around Wowhead's viewer, a third party's script.
# It runs in a frame with `sandbox="allow-scripts"` and no
# allow-same-origin, so its origin is opaque: it can't read the page around
# it, its cookies or storage, the /zogzog/ key, or call /api/* with the
# visitor's login (pre-publication review M2: the frame was same-origin and
# a hostile viewer build could take over the account or read every sealed
# file). It has no network either (connect-src 'none'): it asks the page
# around it for each file over postMessage, and that page fetches only the
# relay's allow-listed paths (web/model3d/model3d.js). Its own CSS and script
# are inlined (view_page), allowed by hash: a sandboxed frame's requests
# carry no cookies in Chromium, so behind a cookie login they'd hit the
# login page. The viewer compiles WebAssembly and builds functions at run
# time ('wasm-unsafe-eval', 'unsafe-eval'), only here; jQuery and the viewer
# arrive as text and are evaluated. The page also carries this policy in a
# <meta>, so a bundle on a host that sends no headers keeps it.
VIEW_POLICY = ("default-src 'none'; script-src {script} 'unsafe-eval' 'wasm-unsafe-eval'; style-src {style}; "
               "img-src data: blob:; connect-src {connect}; base-uri 'none'; form-action 'none'")
# A bundle built before the sandbox (no <meta> policy in its view.html) is
# served as before until the publisher rebuilds it: a new version always
# rebuilds (publish.page_id), so this lasts one publisher pass.
CSP_VIEW_LEGACY = ("default-src 'none'; script-src 'self' 'unsafe-eval' 'wasm-unsafe-eval'; style-src 'self'; "
                   "img-src 'self' data: blob:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
                   "frame-ancestors 'self'")
_META = re.compile(rb'<meta http-equiv="Content-Security-Policy" content="([^"]+)">')

# The viewer script is Wowhead's current one ("live"): the "classic" copy on
# the CDN still asks for .mo3 models that are gone. The classic models,
# textures and metadata are read with it, as Wowhead's classic pages do.
ZAM_URL = "https://wow.zamimg.com/modelviewer/"
VIEWER = "live/viewer/viewer.min.js"
# The viewer is fetched from Wowhead's CDN and runs in every visitor's
# browser, so only this exact build is relayed (pre-publication review M2):
# a changed script is refused, the last good copy on disk is served, and
# the log says so until someone reads the new one and sets
# MODEL3D_VIEWER_SHA256 (then bumps this pin). Wowhead publishes no licence
# for it, so it isn't vendored. Reviewed 2026-11-19, 796,450 bytes, the
# same build every cache held since 2026-11-18.
VIEWER_SHA256 = "ce4ceffb220830b806a9ec9483118e84471a4a4b47edfbb3c672914e96cdd241"
DATA = "classic/"
# A file Wowhead's classic data lacks (a race or an item newer than Classic
# Era) is looked for in its current data next: the ids are the game's own
# file and display ids, the same in both.
FALLBACK = "live/"
# Forever draws the modern character models, with the modern customization
# ids: Battle.net's and Narcissus's looks (Skin 1174, Hair Style 1210...)
# are the ids of Wowhead's current data, not of its classic data (17734...,
# another set for the old models). A look read against the classic data
# matched nothing, so every option fell back to its first choice: a bald
# character, no beard (2026-11-18). The character's own model and its
# customization come from the current data first; items stay classic first.
LIVE_FIRST = re.compile(r"^meta/(character|charactercustomization)/")
WAGO_URL = "https://wago.tools"
PRODUCT = "wowf"  # WoW Forever on wago.tools
USER_AGENT = f"wow-stats/{__version__}"
JQUERY = "jquery-3.7.1.min.js"
# Our own files under web/model3d/, served by this module only.
OWN = {"model3d.js": "text/javascript; charset=utf-8", "model3d.css": "text/css; charset=utf-8",
       "view.js": "text/javascript; charset=utf-8", "view.css": "text/css; charset=utf-8",
       JQUERY: "text/javascript; charset=utf-8", "LICENSE-jquery.txt": "text/plain; charset=utf-8"}
# The only Wowhead paths the relay fetches: the viewer and what it loads.
RELAY = re.compile(r"^(viewer/viewer\.min\.js"
                   r"|meta/(character|charactercustomization|item|itemvisual|armor/\d{1,2})/\d{1,10}\.json"
                   r"|textures/\d{1,10}\.(webp|png)"
                   r"|(?P<k>m2|skin|anim|skel|phys|bone)/\d{1,10}(?:-\d{1,6}){0,2}\.(?P=k))$")
ITEM_META = re.compile(r"^meta/item/(\d{1,10})\.json$")
TYPES = {".js": "text/javascript; charset=utf-8", ".json": "application/json", ".webp": "image/webp",
         ".png": "image/png"}
# Where the browser reads the relay's files: /model3d/zam/<ZAM_REV>/<path>
# (view.js, the static bundles). Bump it whenever what a path answers
# changes. v0.2.1 moved the character metas from Wowhead's classic data to
# its live data (LIVE_FIRST) under the same URLs, served with a day of
# browser cache: a browser that had opened the 3D view on v0.2.0 kept the
# classic meta/character/13.json, asked for its model (m2/119159.m2, in no
# v0.2.1 bundle: the only /model3d/ 404 in the static server's log) and drew
# a broken head, or nothing (2026-11-18). A new revision is a new URL, so no
# old entry can match.
ZAM_REV = "r2"
# What a browser may keep for a day: files named by the game's FileDataID
# (models, skins, textures...), whose content never changes. The metas and
# the viewer script are revalidated on every load: what they answer depends
# on this module's choices (classic or live data), not only on the id.
STABLE = re.compile(r"^(textures|m2|skin|anim|skel|phys|bone)/")


def cache_control(path: str, scope: str = "public") -> str:
    """Cache-Control for a relay path (without zam/<rev>/)."""
    return f"{scope}, max-age=86400" if STABLE.match(path) else "no-cache"

MAX_FILE = 48 * 1024 * 1024
DAY = 86400

# Forever's playable races -> their character models (male, female), from
# Forever's own ChrRaceXChrModel table (build 1.60.1). Names as the game
# saves them (UnitRace, English client) and as its files spell them.
CHR_MODELS = {"human": (1, 2), "orc": (3, 4), "dwarf": (5, 6), "night elf": (7, 8), "undead": (9, 10),
              "tauren": (11, 12), "gnome": (13, 14), "troll": (15, 16), "skyborne": (218, 219)}
RACE_ALIASES = {"nightelf": "night elf", "scourge": "undead", "high order skyborne": "skyborne",
                "windshaper skyborne": "skyborne"}
# Skyborne is Forever's own race: Wowhead has no model for it. It shares its
# skeleton with the Blood Elf (ChrModel 19, 20), so it stands in, and the
# page says so.
STAND_INS = {218: (19, "Blood Elf"), 219: (20, "Blood Elf")}
GENDERS = {"male": 0, "female": 1}


def race_key(race) -> str | None:
    r = " ".join(str(race or "").replace("_", " ").split()).lower()
    r = RACE_ALIASES.get(r, r)
    return r if r in CHR_MODELS else None


def chr_model(race, sex) -> int | None:
    """The viewer's character model for a race and sex (male when unknown)."""
    key = race_key(race)
    return CHR_MODELS[key][GENDERS.get(sex, 0)] if key else None
# Inventory types the viewer shows. Its meta/armor/<type>/ holds 1 head,
# 3 shoulder, 4 shirt, 5 chest, 6 waist, 7 legs, 8 feet, 9 wrist, 10 hands,
# 16 back, 19 tabard, 20 robe; weapons, shields and held items are meta/item/.
ARMOR = {1, 3, 4, 5, 6, 7, 8, 9, 10, 16, 19, 20}
HELD = {13, 14, 15, 17, 21, 22, 23, 25, 26}
# Worn but never drawn: neck, fingers, trinkets. Not failures.
UNSEEN_SLOTS = {2, 11, 12, 13, 14}
# Where the viewer puts each inventory type (its own table): two-handers,
# one-handers and wands all go in the main hand (21), shields, held items
# and bows in the off hand (22).
HAND = {13: 21, 17: 21, 21: 21, 25: 21, 26: 21, 14: 22, 15: 22, 22: 22, 23: 22}
# Weapon subclasses (item class 2) of the ranged slot: bows, guns and
# crossbows are drawn; a wand isn't (the game doesn't draw one either).
BOW_GUN = {2, 3, 18}
WAND = 19
RANGED_SLOT = 18


class Upstream(Exception):
    pass


def fetch(url: str, timeout: int = 30) -> bytes | None:
    """GET a URL: the body, None on a 404, Upstream on anything else."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(MAX_FILE + 1)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise Upstream(f"{urllib.parse.urlsplit(url).hostname} answered {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise Upstream(f"{urllib.parse.urlsplit(url).hostname} unreachable: {e}") from None
    if len(body) > MAX_FILE:
        raise Upstream(f"{urllib.parse.urlsplit(url).hostname}: file too large")
    return body


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".part-")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


class DisplayIds:
    """item id -> (inventory type, display id), from Forever's client tables
    on wago.tools. Kept in memory and on disk; checked for a newer build once
    a day; a failed check keeps the table it has."""

    def __init__(self, wago_url: str, product: str, cache: Path | None):
        self.wago = wago_url.rstrip("/")
        self.product = product
        self.file = cache / "display-ids.json" if cache else None
        self.table: dict[int, tuple[int, int, int, int]] = {}
        # Forever's customization choice -> the geosets it turns on, [(type, id)]
        # (ChrCustomizationElement, ChrCustomizationGeoset): how a choice
        # Wowhead's data doesn't have is found among the ones it does.
        self.geosets: dict[int, list[tuple[int, int]]] = {}
        # display id -> (model resource, material resource), for the displays
        # that have a model (ItemDisplayInfo), and material -> its texture
        # (TextureFileData): how a display Wowhead lacks is drawn (item_meta).
        self.looks: dict[int, tuple[int, int]] = {}
        self.textures: dict[int, int] = {}
        self._by_model: dict[int, list[int]] | None = None
        self.build = ""
        self.checked = 0.0
        self.lock = threading.Lock()

    def get(self, item_id: int) -> tuple[int, int, int, int] | None:
        """(inventory type, display id, item class, subclass), or None."""
        self._load()
        return self.table.get(item_id)

    def choice_geosets(self, choice_id: int) -> list[tuple[int, int]]:
        self._load()
        return self.geosets.get(choice_id, [])

    def look(self, display: int) -> tuple[int, int] | None:
        """(model resource, material resource) of a display, or None."""
        self._load()
        return self.looks.get(display)

    def texture(self, material: int) -> int | None:
        self._load()
        return self.textures.get(material)

    def same_model(self, display: int) -> list[int]:
        """The other displays of the same model, oldest first (the ones
        Wowhead's data most likely has)."""
        look = self.look(display)
        if not look:
            return []
        if self._by_model is None:
            by: dict[int, list[int]] = {}
            for d, (m, _) in sorted(self.looks.items()):
                by.setdefault(m, []).append(d)
            self._by_model = by
        return [d for d in self._by_model.get(look[0], []) if d != display]

    def _load(self) -> None:
        if self.table and time.time() - self.checked < DAY:
            return
        with self.lock:
            if self.table and time.time() - self.checked < DAY:
                return
            if not self.table and self.file and self.file.is_file():
                try:
                    d = json.loads(self.file.read_text(encoding="utf-8"))
                    if d.get("v") != 4:
                        raise ValueError("an older cache")
                    self.table = {int(k): tuple(v[:4]) for k, v in d["items"].items()}
                    self.geosets = {int(k): [tuple(g) for g in v] for k, v in d.get("geosets", {}).items()}
                    self.looks = {int(k): (int(v[0]), int(v[1])) for k, v in d.get("looks", {}).items()}
                    self.textures = {int(k): int(v) for k, v in d.get("textures", {}).items()}
                    self._by_model = None
                    self.build, self.checked = d["build"], float(d["checked"])
                except (OSError, ValueError, KeyError, TypeError, IndexError):
                    self.table = {}
                if self.table and time.time() - self.checked < DAY:
                    return
            try:
                self._refresh()
            except Upstream:
                if not self.table:
                    raise
                self.checked = time.time()  # keep what we have; try again tomorrow

    def _refresh(self) -> None:
        latest = fetch(f"{self.wago}/api/builds/{urllib.parse.quote(self.product)}/latest")
        try:
            build = str(json.loads(latest or b"{}")["version"])
        except (ValueError, KeyError, TypeError):
            raise Upstream(f"wago.tools knows no build of {self.product}") from None
        if build != self.build or not self.table:
            tables = {}
            for name in ("Item", "ItemModifiedAppearance", "ItemAppearance"):
                body = fetch(f"{self.wago}/db2/{name}/csv?build={urllib.parse.quote(build)}", timeout=120)
                if body is None:
                    raise Upstream(f"wago.tools has no {name} table for {build}")
                tables[name] = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
            self.table = build_table(**tables)
            cust = {}
            for name in ("ChrCustomizationElement", "ChrCustomizationGeoset"):
                # Optional: without them a look Wowhead's ids don't cover
                # falls back to the default choice, as before.
                with contextlib.suppress(Upstream):
                    body = fetch(f"{self.wago}/db2/{name}/csv?build={urllib.parse.quote(build)}", timeout=120)
                    if body:
                        cust[name] = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
            self.geosets = choice_geosets(cust.get("ChrCustomizationElement") or [],
                                          cust.get("ChrCustomizationGeoset") or [])
            for name in ("ItemDisplayInfo", "TextureFileData"):
                # Optional too: without them a display Wowhead lacks stays
                # undrawn, as before.
                with contextlib.suppress(Upstream):
                    body = fetch(f"{self.wago}/db2/{name}/csv?build={urllib.parse.quote(build)}", timeout=120)
                    if body:
                        cust[name] = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
            self.looks, self.textures = display_looks(cust.get("ItemDisplayInfo") or [],
                                                      cust.get("TextureFileData") or [])
            self._by_model = None
            self.build = build
        self.checked = time.time()
        if self.file:
            with contextlib.suppress(OSError):  # no writable cache: memory only
                _write(self.file, json.dumps({"v": 4, "build": self.build, "checked": self.checked,
                                              "items": {str(k): list(v) for k, v in self.table.items()},
                                              "geosets": {str(k): [list(g) for g in v]
                                                          for k, v in self.geosets.items()},
                                              "looks": {str(k): list(v) for k, v in self.looks.items()},
                                              "textures": {str(k): v for k, v in self.textures.items()}},
                                             separators=(",", ":")).encode())


def build_table(Item: list[dict], ItemModifiedAppearance: list[dict],  # noqa: N803 - the tables' own names
                ItemAppearance: list[dict]) -> dict[int, tuple[int, int, int, int]]:
    """Join the three client tables: item -> inventory type, class and
    subclass, item -> its appearance (modifier 0, the base look first),
    appearance -> display id."""

    def i(v) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    kind = {i(r.get("ID")): (i(r.get("InventoryType")), i(r.get("ClassID")), i(r.get("SubclassID"))) for r in Item}
    display = {i(r.get("ID")): i(r.get("ItemDisplayInfoID")) for r in ItemAppearance}
    out: dict[int, tuple[int, int, int, int]] = {}
    for r in sorted(ItemModifiedAppearance, key=lambda r: (i(r.get("ItemAppearanceModifierID")),
                                                            i(r.get("OrderIndex")))):
        item = i(r.get("ItemID"))
        d = display.get(i(r.get("ItemAppearanceID")), 0)
        if item and d and item not in out:
            inv, cls, sub = kind.get(item, (0, 0, 0))
            out[item] = (inv, d, cls, sub)
    return out


def display_looks(displays: list[dict], texture_files: list[dict]) -> tuple[dict[int, tuple[int, int]],
                                                                              dict[int, int]]:
    """display -> (model resource, material resource) for every display with
    one model (ItemDisplayInfo slot 0), and material -> its texture file
    (TextureFileData, the first of each material)."""

    def i(v) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    looks = {i(r.get("ID")): (i(r.get("ModelResourcesID_0")), i(r.get("ModelMaterialResourcesID_0")))
             for r in displays if i(r.get("ID")) and i(r.get("ModelResourcesID_0"))}
    wanted = {m for _, m in looks.values() if m}
    textures: dict[int, int] = {}
    for r in sorted(texture_files, key=lambda r: i(r.get("FileDataID"))):
        m = i(r.get("MaterialResourcesID"))
        if m in wanted and i(r.get("UsageType")) == 0 and m not in textures:
            textures[m] = i(r.get("FileDataID"))
    return looks, textures


def choice_geosets(elements: list[dict], geosets: list[dict]) -> dict[int, list[tuple[int, int]]]:
    """Customization choice -> the (geoset type, geoset id) pairs it shows."""

    def i(v) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    geo = {i(r.get("ID")): (i(r.get("GeosetType")), i(r.get("GeosetID"))) for r in geosets}
    out: dict[int, list[tuple[int, int]]] = {}
    for r in elements:
        g = geo.get(i(r.get("ChrCustomizationGeosetID")))
        if g and i(r.get("ChrCustomizationChoiceID")):
            out.setdefault(i(r.get("ChrCustomizationChoiceID")), []).append(g)
    return out


class Relay:
    """Wowhead's viewer files, fetched on demand from a fixed list of paths
    and kept on disk (a missing file is remembered for an hour)."""

    # Bounded (review, info 2): a lock per path and a miss per path grew with
    # every id anyone asked for. Locks are striped; misses are pruned.
    LOCKS = 64
    MAX_MISSING = 4096

    def __init__(self, zam_url: str, cache: Path | None, budget_mb: int, viewer_sha256: str = VIEWER_SHA256):
        self.zam = zam_url.rstrip("/") + "/"
        self.dir = cache / "zam" if cache else None
        self.budget = budget_mb * 1024 * 1024
        self.viewer_sha256 = (viewer_sha256 or VIEWER_SHA256).strip().lower()
        self.missing: dict[str, float] = {}
        self.locks = [threading.Lock() for _ in range(self.LOCKS)]
        self.guard = threading.Lock()
        self.used: int | None = None
        # What answers a meta/item/ Wowhead lacks (Model3D.item_meta).
        self.stand_in = None

    def get(self, path: str, stand_in: bool = True) -> bytes | None:
        body = self._get(path)
        if body is None and stand_in and self.stand_in is not None and ITEM_META.match(path):
            body = self.stand_in(path)
        return body

    def _get(self, path: str) -> bytes | None:
        if not RELAY.match(path):
            return None
        live_first = bool(LIVE_FIRST.match(path))
        # The live-first files sit apart on disk: a cache from before kept
        # the classic copy under the plain path.
        local = (self.dir / "live-first" / path if live_first else self.dir / path) if self.dir else None
        viewer = path == "viewer/viewer.min.js"
        max_age = DAY if viewer else 30 * DAY
        if local and local.is_file() and time.time() - local.stat().st_mtime < max_age:
            body = local.read_bytes()
            if not viewer or self._pinned(body):
                return body
        if time.time() - self.missing.get(path, 0) < 3600:
            return None
        with self.locks[hash(path) % self.LOCKS]:
            if local and local.is_file() and time.time() - local.stat().st_mtime < max_age:
                body = local.read_bytes()
                if not viewer or self._pinned(body):
                    return body
            try:
                if viewer:
                    body = fetch(self.zam + VIEWER)
                    if body is not None and not self._pinned(body):
                        got = hashlib.sha256(body).hexdigest()
                        print(f"model3d: Wowhead's viewer changed (sha256 {got}, pinned {self.viewer_sha256}): "
                              "refused. Read the new build, then set MODEL3D_VIEWER_SHA256.", flush=True)
                        if local and local.is_file() and self._pinned(local.read_bytes()):
                            return local.read_bytes()  # the last good copy, kept as it is
                        raise Upstream("Wowhead's 3D viewer changed and is waiting for a review")
                else:
                    first, then = (FALLBACK, DATA) if live_first else (DATA, FALLBACK)
                    body = fetch(self.zam + first + path)
                    if body is None:
                        body = fetch(self.zam + then + path)
            except Upstream:
                # Stale beats nothing, but never an unreviewed viewer.
                if local and local.is_file() and (not viewer or self._pinned(local.read_bytes())):
                    return local.read_bytes()
                raise
            if body is None:
                self._miss(path)
                return None
            if local:
                try:
                    _write(local, body)
                    self._account(len(body))
                except OSError:
                    pass
            return body

    def _pinned(self, body: bytes) -> bool:
        return hmac.compare_digest(hashlib.sha256(body).hexdigest(), self.viewer_sha256)

    def _miss(self, path: str) -> None:
        with self.guard:
            now = time.time()
            if len(self.missing) >= self.MAX_MISSING:
                self.missing = {k: t for k, t in self.missing.items() if now - t < 3600}
                if len(self.missing) >= self.MAX_MISSING:  # still full: the oldest half goes
                    keep = sorted(self.missing.items(), key=lambda kv: kv[1])[self.MAX_MISSING // 2:]
                    self.missing = dict(keep)
            self.missing[path] = now

    def _account(self, n: int) -> None:
        """Keep the cache under its budget: oldest files go first."""
        with self.guard:
            if self.used is None:
                self.used = sum(f.stat().st_size for f in self.dir.rglob("*") if f.is_file())
            else:
                self.used += n
            if self.used <= self.budget:
                return
            files = sorted((f for f in self.dir.rglob("*") if f.is_file()), key=lambda f: f.stat().st_mtime)
            for f in files:
                if self.used <= self.budget * 0.8:
                    break
                try:
                    size = f.stat().st_size
                    f.unlink()
                    self.used -= size
                except OSError:
                    pass


def _sha(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


def view_page(static: Path, render: bool = False) -> bytes:
    """/model3d/view with its CSS and script inlined and its policy in a
    <meta> (VIEW_POLICY). `render`: the renderer's copy, loaded on its own
    in a headless browser that holds nothing (wowstats.renderer): it reads
    its files itself (connect-src 'self') instead of asking a page around it."""
    html = (static / "view.html").read_text(encoding="utf-8")
    css = (static / "view.css").read_text(encoding="utf-8")
    js = (static / "view.js").read_text(encoding="utf-8")
    for name, text in (("view.css", css), ("view.js", js)):
        if re.search(r"</(script|style)|<!--", text, re.I):
            raise ValueError(f"{name} can't be inlined: it holds a closing tag or a comment opener")
    policy = VIEW_POLICY.format(script=_sha(js), style=_sha(css), connect="'self'" if render else "'none'")
    html = (html.replace("<!--inline:view.css-->", f"<style>{css}</style>")
                .replace("<!--inline:view.js-->", f"<script>{js}</script>")
                .replace("<!--policy-->", f'<meta http-equiv="Content-Security-Policy" content="{policy}">'))
    return html.encode()


def view_csp(body: bytes, render: bool = False) -> str:
    """The header policy for a view page: its own <meta> policy, plus what a
    <meta> can't say: framed by this origin only, and sandboxed even when
    opened on its own (the renderer's copy is the top page, unsandboxed)."""
    m = _META.search(body)
    if m is None:
        return CSP_VIEW_LEGACY
    extra = "; frame-ancestors 'self'" if not render else ""
    return m.group(1).decode() + extra + ("; sandbox allow-scripts" if not render else "")


def meta_path(inv_type: int, display: int) -> str:
    return f"meta/armor/{inv_type}/{display}.json" if inv_type in ARMOR else f"meta/item/{display}.json"


class Model3D:
    def __init__(self, env: dict, static: Path):
        cache = env.get("MODEL3D_CACHE_DIR") or str(Path(tempfile.gettempdir()) / "wow-stats-model3d")
        self.cache = Path(cache)
        try:
            self.cache.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.cache = None
        self.static = static / "model3d"
        self.ids = DisplayIds(env.get("MODEL3D_WAGO_URL", WAGO_URL), env.get("MODEL3D_PRODUCT", PRODUCT), self.cache)
        self.relay = Relay(env.get("MODEL3D_ZAM_URL", ZAM_URL), self.cache, int(env.get("MODEL3D_CACHE_MB", "512")),
                           env.get("MODEL3D_VIEWER_SHA256", ""))
        self.relay.stand_in = self.item_meta
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="model3d")

    # How many displays of the same model are asked of Wowhead before giving
    # up: each miss is one request (then remembered for an hour).
    STAND_IN_TRIES = 6

    def item_meta(self, path: str) -> bytes | None:
        """A held item's meta Wowhead lacks (a display Forever added): the meta
        of another display of the same model, its texture swapped for this
        display's own (Forever's ItemDisplayInfo and TextureFileData). None
        when the model or the texture can't be matched: better no weapon
        than the wrong one."""
        m = ITEM_META.match(path)
        try:
            display = int(m.group(1)) if m else 0
            look = self.ids.look(display) if display else None
        except Upstream:
            return None
        if not look:
            return None
        texture = self.ids.texture(look[1]) if look[1] else None
        if look[1] and not texture:
            return None
        for other in self.ids.same_model(display)[:self.STAND_IN_TRIES]:
            body = self.relay.get(f"meta/item/{other}.json", stand_in=False)
            try:
                meta = json.loads(body) if body else None
            except ValueError:
                meta = None
            if not isinstance(meta, dict):
                continue
            theirs = self.ids.look(other)
            old = self.ids.texture(theirs[1]) if theirs and theirs[1] else None
            tex = meta.get("Textures") if isinstance(meta.get("Textures"), dict) else {}
            if texture and texture not in tex.values():
                hits = [k for k, v in tex.items() if v == old] if old else []
                if not hits and len(tex) == 1:
                    hits = list(tex)  # one texture: the material's
                if not hits:
                    continue
                meta["Textures"] = {**tex, **{k: texture for k in hits}}
            return json.dumps(meta, separators=(",", ":")).encode()
        return None

    def stand_ins(self, answer: dict | None) -> dict[int, dict]:
        """The metas item_meta made for an answer's held items (display ->
        meta), for a renderer that can't make them (wowstats.pictures)."""
        out = {}
        for it in (answer or {}).get("items") or []:
            inv, display = it.get("inventory_type"), it.get("display_id")
            if inv in ARMOR or not display:
                continue
            path = meta_path(inv, display)
            if self.relay.get(path, stand_in=False) is None:
                body = self.item_meta(path)
                if body:
                    out[int(display)] = json.loads(body)
        return out

    def owns(self, path: str) -> bool:
        return path == "/api/model3d" or path.startswith("/model3d/")

    def handle(self, handler, app, path: str, qs: dict) -> None:
        if handler.command not in ("GET", "HEAD"):
            return handler.not_found()
        if path == "/api/model3d":
            name = qs.get("name", [""])[0]
            if not app.valid_char(name):
                return handler.json(400, {"error": "bad character name"})
            if not app.known(name):
                return handler.json(404, {"error": f"no events for {name}"})
            level = qs.get("level", [""])[0]
            if level and not (level.isdigit() and 1 <= int(level) <= 60):
                return handler.json(400, {"error": "level is 1 to 60"})
            try:
                return handler.json(200, self.for_char(app, name, int(level) if level else None,
                                                       qs.get("sex", [""])[0]))
            except Upstream as e:
                return handler.json(502, {"error": str(e)})
        rest = path[len("/model3d/"):]
        if rest == "view":
            body = view_page(self.static)
            return handler.send(200, body, "text/html; charset=utf-8", "no-cache", csp=view_csp(body))
        if rest in OWN:
            return handler.send(200, (self.static / rest).read_bytes(), OWN[rest], "no-cache")
        zam = f"zam/{ZAM_REV}/"
        if rest.startswith(zam):
            rel = rest[len(zam):]
            try:
                body = self.relay.get(rel)
            except Upstream as e:
                return handler.json(502, {"error": str(e)})
            if body is None:
                return handler.not_found()
            ctype = TYPES.get(os.path.splitext(rest)[1], "application/octet-stream")
            return handler.send(200, body, ctype, cache_control(rel, "private"))
        return handler.not_found()

    def for_char(self, app, name: str, level: int | None = None, sex: str = "") -> dict:
        """The viewer's answer for a character now, or at a past level (the
        gear of its snapshot; none when that level has no character sheet)."""
        d = app.char(name)
        gear = d.get("gear") or {}
        if level is not None:
            snap = app.views.snapshot(name, level)
            if not snap.get("gear"):
                return {"model": None, "items": [], "missing": [], "level": level,
                        "why": f"no gear known at level {level}"}
            # A reconstructed level (wowstats.history) dresses the model in
            # its likely items only; the slots it doesn't know stay empty.
            gear = {**gear, **snap["gear"]}
        return {**self.character(gear, d.get("header"), sex, find_look(app, name)), "level": level}

    def character(self, gear: dict, header: dict | None = None, sex_override: str = "",
                  look: dict | None = None) -> dict:
        """What the viewer needs for one character: the model, its look (the
        saved one from `find_look`, else the default), and each equipped
        item's slot and display id. Items it can't draw are listed with the
        reason; neck, rings, trinkets, ammo, quivers and relics are left out
        without one (the game doesn't draw them either)."""
        header = header or {}
        race = gear.get("race") or header.get("race")
        cls = str(gear.get("class") or header.get("class") or "").upper()
        sex = gear.get("sex") or header.get("sex")
        if sex not in GENDERS and look and look.get("gender") in GENDERS:
            sex = look["gender"]  # Battle.net knows it when no addon saved it
        sex_known = sex in GENDERS
        if not sex_known and sex_override in GENDERS:
            sex = sex_override
        out: dict = {"race": race, "sex": sex, "sex_known": sex_known, "class": cls or None, "model": None,
                     "stand_in": None, "items": [], "missing": [], "appearance": None, "source": None}
        model = chr_model(race, sex)
        if model is None:
            out["why"] = (f"no model for the race {race}" if race else
                          "the race isn't saved yet (Syndicator or Polestar saves it at logout)")
            return out
        if model in STAND_INS:
            model, out["stand_in"] = STAND_INS[model]
        custom = self.relay.get(f"meta/charactercustomization/{model}.json")
        if not self.relay.get(f"meta/character/{model}.json") or not custom:
            out["why"] = f"Wowhead's viewer has no {race} model"
            return out
        try:
            options = json.loads(custom).get("Options") or []
        except ValueError:
            options = []
        out["model"] = {"chr_model": model, "gender": GENDERS.get(sex, 0),
                        "options": apply_look(options, translate_look(options, (look or {}).get("options"),
                                                                      self.ids.choice_geosets))}
        model_ids = {o.get("Id") for o in options if isinstance(o, dict)}
        if look and any(o.get("optionId") in model_ids for o in look.get("options") or [] if isinstance(o, dict)):
            out["appearance"] = {"source": look.get("source"), "refreshed": look.get("refreshed")}

        def one(it: dict) -> dict:
            row = {k: it.get(k) for k in ("slot", "slot_name", "item_id", "name")}
            if it.get("slot") in UNSEEN_SLOTS:
                return {**row, "unseen": True}
            try:
                hit = self.ids.get(int(it.get("item_id") or 0))
            except (TypeError, ValueError):
                hit = None
            if hit is None:
                return {**row, "why": "not in Forever's item tables"}
            inv, display, item_class, sub = hit
            row.update(inventory_type=inv, display_id=display)
            if inv not in ARMOR and inv not in HELD:
                return {**row, "unseen": True}  # ammo, quivers, relics, bags
            if it.get("slot") == RANGED_SLOT and item_class == 2 and sub == WAND:
                return {**row, "unseen": True}
            row["ranged"] = it.get("slot") == RANGED_SLOT
            if self.relay.get(meta_path(inv, display)) is None:
                return {**row, "why": "Wowhead's viewer has no model for it"}
            return row

        rows = [r for r in self.pool.map(one, [i for i in gear.get("items") or [] if isinstance(i, dict)])
                if not r.pop("unseen", False)]
        # Two weapons for one hand (a gun and a sword both go in the main
        # hand): a hunter holds the ranged weapon, everyone else the melee one.
        hunter = cls == "HUNTER"
        rows.sort(key=lambda r: (r.get("ranged") != hunter, r.get("slot") or 0))
        held: dict[int, dict] = {}
        for row in rows:
            ranged = row.pop("ranged", False)
            hand = HAND.get(row.get("inventory_type"))
            if hand and "why" not in row:
                if hand in held:
                    if not ranged:  # a ranged weapon that loses its hand stays out quietly
                        other = held[hand].get("name") or "another weapon"
                        out["missing"].append({**row, "why": f"{other} is in that hand"})
                    continue
                held[hand] = row
            (out["missing"] if "why" in row else out["items"]).append(row)
        out["items"].sort(key=lambda r: r.get("slot") or 0)
        out["source"] = f"wago.tools {self.ids.product} {self.ids.build}" if self.ids.build else None
        return out


# --- the character's look -----------------------------------------------------

def translate_look(options: list, saved: list | None, geosets) -> list[dict]:
    """A saved look in the ids of the customization data the viewer reads.
    A choice the data has keeps its ids. One it lacks is matched by the
    geosets Forever's own tables say it shows (`geosets`: choice id ->
    [(type, id)]): Forever's one "Facial Hair" option (9486, "Styled" is
    beard 6 and mustache 5) is two options in Wowhead's data (Beard and
    Mustache), each choice showing one geoset."""
    have = {o.get("Id"): {c.get("Id") for c in o.get("Choices") or []} for o in options if isinstance(o, dict)}
    by_geoset: dict[tuple[int, int], dict] = {}
    for o in options:
        if not isinstance(o, dict):
            continue
        for c in o.get("Choices") or []:
            gs = {(e["Geoset"].get("GeosetType"), e["Geoset"].get("GeosetID"))
                  for e in c.get("Elements") or [] if isinstance(e, dict) and isinstance(e.get("Geoset"), dict)}
            if len(gs) == 1:
                by_geoset.setdefault(gs.pop(), {"optionId": o.get("Id"), "choiceId": c.get("Id")})
    direct, matched = [], []
    for s in saved or []:
        try:
            opt, choice = int(s["optionId"]), int(s["choiceId"])
        except (KeyError, TypeError, ValueError):
            continue
        if choice in have.get(opt, ()):
            direct.append({"optionId": opt, "choiceId": choice})
            continue
        for g in geosets(choice) or []:
            hit = by_geoset.get(tuple(g))
            if hit:
                matched.append(hit)
    # A choice the data has wins over one found through geosets.
    return matched + direct


def apply_look(options: list, saved: list | None) -> list[dict]:
    """The viewer's options: the saved choice for every option the model has
    (Battle.net's and Narcissus's ids are Wowhead's), the first choice for
    the rest. A choice the model doesn't offer (another race's look after a
    race change) falls back to the default too."""
    chosen = {}
    for o in saved or []:
        with contextlib.suppress(KeyError, TypeError, ValueError):
            chosen[int(o["optionId"])] = int(o["choiceId"])
    out = []
    for o in options:
        if not isinstance(o, dict) or not o.get("Choices"):
            continue
        ids = [c.get("Id") for c in o["Choices"]]
        pick = chosen.get(o.get("Id"))
        out.append({"optionId": o["Id"], "choiceId": pick if pick in ids else ids[0]})
    return out


def narcissus_look(rows: list[dict]) -> dict | None:
    """The newest `looks` the shipper sent from Narcissus's barber shop."""
    for r in rows:
        try:
            looks = json.loads(r.get("looks") or "[]")
        except ValueError:
            continue
        if looks:
            return {"source": "narcissus", "options": looks, "gender": None, "refreshed": r.get("_time")}
    return None


def find_look(app, name: str) -> dict | None:
    """Battle.net's look first (every character the API knows, no addon),
    then the last one saved in Narcissus's barber shop, else None (the
    default look). Never raises: a store that is down means the default."""
    with contextlib.suppress(Exception):
        look = app.store.character_look(name)
        if look:
            return look
    with contextlib.suppress(Exception):
        v = app.views
        return narcissus_look(app.store.vl(
            f'{v.stream} feed:="wow-character" {v.char_filter(name)} looks:* '
            f"| fields _time, looks | sort by (_time desc) | limit 1"))
    return None


# --- what the viewer will load, for the static bundle ------------------------

M2_CHUNKS = {b"SFID": "skin", b"TXID": "textures", b"AFID": "anim", b"BFID": "bone", b"SKID": "skel",
             b"PFID": "phys"}


def m2_refs(body: bytes) -> dict[str, set[int]]:
    """The file ids an M2 model names in its chunks (SFID skins, TXID
    textures, AFID animations, BFID bones, SKID skeleton, PFID physics): the
    files the viewer asks for after the model itself."""
    import struct

    out: dict[str, set[int]] = {}
    i = 0
    while i + 8 <= len(body):
        tag, n = body[i:i + 4], struct.unpack_from("<I", body, i + 4)[0]
        kind = M2_CHUNKS.get(tag)
        if kind and n:
            data = body[i + 8:i + 8 + n]
            if tag == b"AFID":  # (anim id, sub id, file id) records of 8 bytes
                ids = {struct.unpack_from("<I", data, k + 4)[0] for k in range(0, len(data) - 7, 8)}
            else:
                ids = set(struct.unpack_from(f"<{len(data) // 4}I", data))
            out.setdefault(kind, set()).update(x for x in ids if x)
        i += 8 + n
    return out


def _file_ids(meta: dict, key: str, gender: int | None = None) -> set[int]:
    """FileDataIds of a meta's TextureFiles or ModelFiles (every entry of the
    gender or for both: 2 and 3 mean either)."""
    out = set()
    for entries in (meta.get(key) or {}).values():
        for e in entries or []:
            if isinstance(e, dict) and e.get("FileDataId") and (
                    gender is None or e.get("Gender") in (gender, 2, 3)):
                out.add(int(e["FileDataId"]))
    return out


def viewer_files(relay: Relay, answer: dict) -> set[str]:
    """Every relay path the viewer loads to draw `answer` (Model3D.character):
    the viewer, the character's model and customization, each item's meta,
    and every model, skin and texture they name, fetched as it goes. A
    superset of what the browser asks for (both genders' item textures, every
    skin of a model), so a static copy draws without the relay."""
    want: set[str] = {"viewer/viewer.min.js"}
    model = (answer or {}).get("model")
    if not model:
        return want
    gender = model.get("gender", 0)
    models: set[int] = set()
    textures: set[int] = set()

    def meta(path: str) -> dict:
        want.add(path)
        body = relay.get(path)
        try:
            return json.loads(body) if body else {}
        except ValueError:
            return {}

    ch = meta(f"meta/character/{model['chr_model']}.json")
    if ch.get("Model"):
        models.add(int(ch["Model"]))
    custom = meta(f"meta/charactercustomization/{model['chr_model']}.json")
    chosen = {(o["optionId"], o["choiceId"]) for o in model.get("options") or []}
    mats = set()
    for opt in custom.get("Options") or []:
        for choice in opt.get("Choices") or []:
            if (opt.get("Id"), choice.get("Id")) not in chosen:
                continue
            for el in choice.get("Elements") or []:
                m = (el.get("Material") or {}).get("MaterialResourcesID")
                if m:
                    mats.add(str(m))
                if el.get("CondModelFileDataId"):
                    models.add(int(el["CondModelFileDataId"]))
    for m, entries in (custom.get("TextureFiles") or {}).items():
        if m in mats:
            textures.update(int(e["FileDataId"]) for e in entries or [] if e.get("Gender") in (gender, 2, 3))
    for it in answer.get("items") or []:
        if not it.get("display_id"):
            continue
        im = meta(meta_path(it["inventory_type"], it["display_id"]))
        if im.get("Model"):
            models.add(int(im["Model"]))
        textures.update(int(v) for v in (im.get("Textures") or {}).values() if v)
        textures.update(int(v) for v in (im.get("Textures2") or {}).values() if v)
        textures |= _file_ids(im, "TextureFiles", gender)
        models |= _file_ids(im, "ModelFiles")
    for mid in sorted(models):
        path = f"m2/{mid}.m2"
        want.add(path)
        body = relay.get(path)
        if not body:
            continue
        for kind, ids in m2_refs(body).items():
            if kind == "textures":
                textures |= ids
            else:
                want.update(f"{kind}/{x}.{kind}" for x in ids)
                if kind != "skel":
                    continue
                # A modern model's bones are named by its skeleton (SKL1, its
                # own BFID chunk), not the model: without them the static
                # viewer 404s on a bone file. The skeleton's animations stay
                # out: the standing pose doesn't load them.
                for sid in ids:
                    skel = relay.get(f"skel/{sid}.skel")
                    if skel:
                        want.update(f"bone/{b}.bone" for b in m2_refs(skel).get("bone", ()))
    want.update(f"textures/{t}.webp" for t in textures)
    return want
