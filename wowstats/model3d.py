"""The 3D character (full view, and the static bundles): our own viewer
(web/model3d/m3d, a port of wow.export's renderer) drawing a recipe worked
out here from the player's own game files.

Everything comes from the person's own store (wowstats.assets): the client
tables their shipper read from their WoW install, and the models and
textures it sends once this server asks for them. Nothing is fetched from
any other site, and one person's files never serve another's pages: the
store is the App's own, and every App is one person's (wowstats.tenancy).

- `/api/model3d?name=&level=&sex=`: the answer for one character
  (Model3D.for_char), behind the page's login:
  {race, sex, sex_known, class, level, models, why, appearance,
   recipe, recipe_sd, no_classic, items, missing}.
  `recipe` is the updated model's recipe (wowstats.character3d, format v1),
  `recipe_sd` the original ("Classic") model's or null, `no_classic` the
  options whose chosen look the original model lacks. `recipe` is null with
  a `why` when the person's shipper hasn't sent the tables yet, the race is
  unknown, or the model's own file isn't on the server yet.
- `/model3d/file/<file id>`: one file of the person's store, same login,
  cached privately (a file id's content never changes).
- `/model3d/view`: the frame the viewer runs in (view_page), sandboxed.

The shipper asks what to send (`Assets.wanted`): every file the person's
characters' recipes name, now, at each past level and in every outfit of
their gear history (history_outfits), both models, both sexes when the sex
isn't known. Worked out at most every WANTED_TTL seconds.

Environment:
  MODEL3D            on (default) | off
  MODEL3D_CACHE_DIR  a writable folder (default <tmp>/wow-stats-model3d): the
                     default home of the files (MODEL3D_ASSETS_DIR, wowstats.assets)
  MODEL3D_MODELS     hd (default) | sd: the models the 3D view opens on
                     (Updated or Classic); a visitor's own pick wins
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import re
import tempfile
import threading
import time
from pathlib import Path

from wowstats import character3d

# /model3d/view: the frame the viewer runs in. `sandbox="allow-scripts"`
# with no allow-same-origin makes its origin opaque: it can't read the page
# around it, its cookies or storage, the /zogzog/ key, or call /api/* with
# the visitor's login (pre-publication review M2). It has no network
# (connect-src 'none'): it asks the page around it for each file over
# postMessage, and that page fetches only model3d/file/<id>
# (web/model3d/model3d.js). Its CSS and scripts are inlined (view_page),
# allowed by hash: a sandboxed frame's requests carry no cookies in
# Chromium, so behind a cookie login they'd hit the login page. No eval of
# any kind: the viewer is plain script, WebGL needs none. The page also
# carries this policy in a <meta>, so a bundle on a host that sends no
# headers keeps it.
VIEW_POLICY = ("default-src 'none'; script-src {script}; style-src {style}; connect-src {connect}; "
               "base-uri 'none'; form-action 'none'")
_META = re.compile(rb'<meta http-equiv="Content-Security-Policy" content="([^"]+)">')
# Our own files under web/model3d/ the page loads (the frame's are inlined).
OWN = {"model3d.js": "text/javascript; charset=utf-8", "model3d.css": "text/css; charset=utf-8"}
FILE = re.compile(r"^file/(\d{1,10})$")
MODELS = ("hd", "sd")
GENDERS = {"male": 0, "female": 1}
WANTED_TTL = 600
# What a browser may keep: a file named by its id never changes.
FILE_CACHE = "max-age=86400"


def _sha(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


def viewer_script(static: Path) -> str:
    """The viewer (web/model3d/m3d, in ORDER.txt's order) and view.js, as
    one script."""
    m3d = static / "m3d"
    names = [n.strip() for n in (m3d / "ORDER.txt").read_text(encoding="utf-8").splitlines()
             if n.strip() and not n.lstrip().startswith("#")]
    parts = [(m3d / n).read_text(encoding="utf-8") for n in names] + [(static / "view.js").read_text(encoding="utf-8")]
    return "\n;\n".join(parts)


def view_page(static: Path, render: bool = False) -> bytes:
    """/model3d/view with its CSS and scripts inlined and its policy in a
    <meta> (VIEW_POLICY). `render`: the renderer's copy, loaded on its own
    in a headless browser that holds nothing (wowstats.renderer): it reads
    its files itself (connect-src 'self') instead of asking a page around it."""
    html = (static / "view.html").read_text(encoding="utf-8")
    css = (static / "view.css").read_text(encoding="utf-8")
    js = viewer_script(static)
    for name, text in (("view.css", css), ("the viewer's scripts", js)):
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
    opened on its own (the renderer's copy is the top page, unsandboxed). A
    page without one gets nothing but the sandbox."""
    m = _META.search(body)
    own = m.group(1).decode() if m else "default-src 'none'"
    return own if render else own + "; frame-ancestors 'self'; sandbox allow-scripts"


def find_look(app, name: str) -> dict | None:
    """Battle.net's look (every character the API knows), else None: the
    race's default look. The API only since 2026-11-20 (Narcissus dropped;
    its old `looks` events stay stored, unread). Never raises: a store that
    is down means the default."""
    with contextlib.suppress(Exception):
        return app.store.character_look(name) or None
    return None



def _outfit(gear: dict) -> tuple:
    """What a gear sheet draws: race, class, sex and each item with its
    enchant; two sheets with the same outfit need the same files."""
    items = tuple(sorted((str(i.get("slot")), str(i.get("item_id")), str(i.get("enchant_id") or 0))
                         for i in gear.get("items") or [] if isinstance(i, dict)))
    return (gear.get("race"), gear.get("class"), gear.get("sex"), items)


def history_outfits(app, name: str) -> list[list[dict]]:
    """Every outfit a character's history knows: each gear sheet the shipper
    sent (wow-gear), and the reconstructed slots (wowstats.history, the
    Recorder's loots, turn-ins and crafts) at each point the replay put an
    item on. Nothing when the history can't be read."""
    views = getattr(app, "views", None)
    if views is None:
        return []
    out: list[list[dict]] = []
    try:
        data, _wins, _ds, gh = views.snapshot_inputs(name)
        out += [g["items"] for g in gh or [] if g.get("items")]
        recon = views.reconstruction(name, data, gh)
    except Exception as e:  # noqa: BLE001 - the history's store down: the levels' outfits still count
        print(f"model3d: {name}'s gear history: {e!r}", flush=True)
        return out
    if recon is not None:
        from wowstats import gear as gearmod
        from wowstats import history

        rp, ref = recon[0], gearmod.reference()
        for t in sorted({sp.t for spans in rp.slots.values() for sp in spans}):
            items = history.items_at(ref, rp, t, recon[2])
            if items:
                out.append(items)
    return out

class Model3D:
    def __init__(self, env: dict, static: Path, assets=None):
        cache = env.get("MODEL3D_CACHE_DIR") or str(Path(tempfile.gettempdir()) / "wow-stats-model3d")
        self.cache: Path | None = Path(cache)
        try:
            self.cache.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.cache = None
        self.static = static / "model3d"
        self.assets = assets
        models = (env.get("MODEL3D_MODELS") or "hd").strip().lower()
        self.models = models if models in MODELS else "hd"
        # The client tables, parsed (character3d.load_tables), kept while
        # the store's tables don't change.
        self.load_tables = character3d.load_tables
        self._tables: tuple[tuple, object] | None = None
        self._wanted: tuple[float, set[int]] | None = None
        self.lock = threading.Lock()

    # ------------------------------------------------------------ the store

    @property
    def store(self):
        return getattr(self.assets, "store", None)

    def tables(self):
        """The person's client tables, or None when none came yet."""
        store = self.store
        if store is None:
            return None
        stamp = ("tables", store.build()) + tuple(getattr(store, "stamp", lambda: ())()[:3])
        with self.lock:
            if self._tables and self._tables[0] == stamp:
                return self._tables[1]
        if not store.build():
            return None
        t = self.load_tables(store)
        if not t.get("ChrModel"):
            t = None
        with self.lock:
            self._tables = (stamp, t)
        return t

    def stamp(self) -> str:
        """What the answers depend on besides the events: the store (new
        files make a recipe drawable). Part of the answers' cache key."""
        store = self.store
        st = store.stamp() if store is not None and hasattr(store, "stamp") else ()
        return hashlib.sha256(repr(st).encode()).hexdigest()[:10]

    # ------------------------------------------------------------ routes

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
            sex = qs.get("sex", [""])[0]
            if sex and sex not in GENDERS:  # part of the cache key: never a free-form string
                return handler.json(400, {"error": "sex is male or female"})
            # Through the App: a plain open's answer is prebuilt (wowstats.prebuilt).
            ans = app.model3d_answer(name, int(level) if level else None, sex)
            # The default models at the time of asking, not of the
            # prebuilt answer: a change in Settings shows at once.
            return handler.json(200, {**ans, "models": self.default_models(app)})
        rest = path[len("/model3d/"):]
        if rest == "view":
            body = view_page(self.static)
            # A frame in the page: the page counts the visit (ui send()).
            return handler.send(200, body, "text/html; charset=utf-8", "no-cache", csp=view_csp(body), stats=False)
        if rest in OWN:
            return handler.send(200, (self.static / rest).read_bytes(), OWN[rest], "no-cache")
        m = FILE.match(rest)
        if m:
            store = self.store
            got = store.file(int(m.group(1))) if store is not None else None
            if got is None:
                return handler.not_found()
            return handler.send(200, got[0], "application/octet-stream", f"private, {FILE_CACHE}")
        return handler.not_found()

    # ------------------------------------------------------------ answers

    def default_models(self, app) -> str:
        """The models the page opens on (the visitor's own pick wins there):
        the person's setting when the app has one (wowstats.tenancy), else
        MODEL3D_MODELS, else Updated."""
        pref = getattr(app, "models_default", None)
        with contextlib.suppress(Exception):
            got = pref() if callable(pref) else None
            if got in MODELS:
                return got
        return self.models

    def gear_at(self, app, name: str, level: int | None) -> tuple[dict, dict | None, str | None]:
        """(gear sheet, header, why not) for a character now or at a past level
        (the gear of its snapshot; a reconstructed level dresses the model in
        its likely items only)."""
        d = app.char(name)
        gear = d.get("gear") or {}
        if level is not None:
            snap = app.views.snapshot(name, level)
            if not snap.get("gear"):
                return gear, d.get("header"), f"no gear known at level {level}"
            gear = {**gear, **snap["gear"]}
        return gear, d.get("header"), None

    def for_char(self, app, name: str, level: int | None = None, sex: str = "") -> dict:
        """The viewer's answer for a character now, or at a past level."""
        gear, header, why = self.gear_at(app, name, level)
        if why:
            return {**self.empty(why), "level": level, "models": self.default_models(app)}
        return {**self.character(gear, header, sex, find_look(app, name)), "level": level,
                "models": self.default_models(app)}

    @staticmethod
    def empty(why: str) -> dict:
        return {"race": None, "sex": None, "sex_known": False, "class": None, "recipe": None, "recipe_sd": None,
                "no_classic": [], "items": [], "missing": [], "appearance": None, "why": why}

    def inputs(self, gear: dict, header: dict | None, sex_override: str, look: dict | None) -> dict:
        """What assemble takes for a character, and what the page says of it."""
        header = header or {}
        race = gear.get("race") or header.get("race")
        cls = str(gear.get("class") or header.get("class") or "").upper() or None
        # Battle.net's only (owner, 2026-11-20): the header's (its profile)
        # or the look's; unknown without it, and the viewer offers both. A
        # caller that already knows it (a test) passes it on the sheet.
        sex = gear.get("sex") or header.get("sex")
        if sex not in GENDERS and look and look.get("gender") in GENDERS:
            sex = look["gender"]
        sex_known = sex in GENDERS
        if not sex_known:
            sex = sex_override if sex_override in GENDERS else "male"
        items = [{"slot": it.get("slot"), "item_id": it.get("item_id"), "name": it.get("name"),
                  "slot_name": it.get("slot_name"),
                  # The enchant from the item's link (shipper 0.2.2 on): its glow.
                  "enchant": it.get("enchant_id") or None}
                 for it in gear.get("items") or [] if isinstance(it, dict)]
        return {"race": race, "class": cls, "sex": sex, "sex_known": sex_known, "items": items,
                "choices": (look or {}).get("options") or []}

    def character(self, gear: dict, header: dict | None = None, sex_override: str = "",
                  look: dict | None = None) -> dict:
        """The answer for one gear sheet (the module's docstring)."""
        x = self.inputs(gear, header, sex_override, look)
        out = {**self.empty(""), "race": x["race"], "sex": x["sex"], "sex_known": x["sex_known"],
               "class": x["class"]}
        out.pop("why")
        tables = self.tables()
        if tables is None:
            out["why"] = "the shipper sends the game files from your computer on its next run"
            return out
        rid = character3d.race_id(tables, x["race"])
        if rid is None:
            out["why"] = (f"no model for the race {x['race']}" if x["race"] else
                          "the race isn't known yet (Battle.net's profile gives it)")
            return out
        sex, cid = GENDERS[x["sex"]], character3d.class_id(x["class"])
        hd = character3d.assemble(tables, rid, sex, cid, x["choices"], x["items"], "hd")
        sd = character3d.assemble(tables, rid, sex, cid, x["choices"], x["items"], "sd")
        have = self.store.have() if hasattr(self.store, "have") else None
        if hd["recipe"] is None:
            out["why"] = hd.get("why") or "no model"
            return out
        if have is not None and hd["recipe"]["model"] not in have:
            out["why"] = "the shipper sends this model's files from your computer on its next run"
            return out
        out["recipe"] = hd["recipe"]
        if sd["classic"] and sd["recipe"] and (have is None or sd["recipe"]["model"] in have):
            out["recipe_sd"] = sd["recipe"]
            out["no_classic"] = character3d.classic_gaps(tables, rid, sex, x["choices"])
        ix = character3d.index(tables)
        options = {o for o, _r in ix.options.get(ix.race_models.get((rid, sex)), [])}
        if look and any(isinstance(o, dict) and o.get("optionId") in options for o in look.get("options") or []):
            out["appearance"] = {"source": look.get("source"), "refreshed": look.get("refreshed")}
        by_slot = {it["slot"]: it for it in x["items"]}

        def row(slot, item_id, why=None) -> dict:
            it = by_slot.get(slot) or {}
            r = {"slot": slot, "slot_name": it.get("slot_name"), "item_id": item_id, "name": it.get("name")}
            return {**r, "why": why} if why else r

        drawn, _ = character3d.worn(tables, x["items"], cid)
        # An item whose model hasn't come yet is drawn without it: listed, with why.
        placed = {a["slot"]: a["model"] for a in hd["recipe"]["attachments"] + hd["recipe"]["collections"]}
        late = {s for s, m in placed.items() if have is not None and m not in have}
        out["items"] = [row(d["slot"], d["item_id"]) for d in drawn if d["slot"] not in late]
        out["missing"] = sorted([row(m["slot"], m["item_id"], m["why"]) for m in hd["missing"]]
                                + [row(d["slot"], d["item_id"], "its files haven't come from your computer yet")
                                   for d in drawn if d["slot"] in late], key=lambda r: r["slot"] or 0)
        return out

    # ------------------------------------------------------------ what the shipper sends

    def needs(self, gear: dict, header: dict | None, look: dict | None) -> set[int]:
        """Every file id the recipes of one gear sheet name: both models,
        and both sexes when the sex isn't known (the page offers both)."""
        tables = self.tables()
        if tables is None:
            return set()
        out: set[int] = set()
        for sex in ("male", "female"):
            x = self.inputs(gear, header, sex, look)
            if x["sex_known"] and x["sex"] != sex:
                continue
            rid = character3d.race_id(tables, x["race"])
            if rid is None:
                return out
            for models in MODELS:
                got = character3d.assemble(tables, rid, GENDERS[x["sex"]], character3d.class_id(x["class"]),
                                           x["choices"], x["items"], models)
                out.update(got["needs"])
        return out

    def wanted(self, app) -> set[int]:
        """What the person's shipper should send: the union of every
        character's needs, now, at each past level and in every gear sheet
        of its history, each outfit once. Kept WANTED_TTL seconds; a
        failure keeps the last set."""
        with self.lock:
            if self._wanted and time.time() - self._wanted[0] < WANTED_TTL:
                return set(self._wanted[1])
        out: set[int] = set()
        try:
            for c in app.chars().get("chars") or []:
                name = c.get("char")
                if not isinstance(name, str) or not app.valid_char(name):
                    continue
                look = find_look(app, name)
                levels = [None] + [lv for lv in (app.timeline(name) or {}).get("levels") or []
                                   if isinstance(lv, int) and 1 <= lv <= 60]
                sheets = []
                for lv in levels:
                    gear, header, why = self.gear_at(app, name, lv)
                    if not why:
                        sheets.append((gear, header))
                # Every outfit of the history, not just the ones in force at
                # a level's end (owner, 2026-11-20: the timeline's model):
                # each gear sheet the shipper sent, and each point where the
                # Recorder's replay changed a slot.
                if sheets:
                    base, header = sheets[0]
                    sheets += [({**base, "items": items}, header) for items in history_outfits(app, name)]
                seen: set = set()
                for gear, header in sheets:
                    key = _outfit(gear)
                    if key not in seen:
                        seen.add(key)
                        out |= self.needs(gear, header, look)
        except Exception as e:  # noqa: BLE001 - the store down: ask for what was asked before
            print(f"model3d: what to ask the shipper for: {e!r}", flush=True)
            with self.lock:
                return set(self._wanted[1]) if self._wanted else set()
        with self.lock:
            self._wanted = (time.time(), out)
        return set(out)
