"""What the pages show, built from the stores. Everything here is the FULL
view; wowstats.redact makes the public one out of it by allow-listing.

The MCP server (wowstats.mcp, optional) gets a
Views instance too: docs/architecture.md describes the methods they can use.
"""

from __future__ import annotations

import concurrent.futures
import contextvars
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from wowstats import (
    ahshared,
    ahsource,
    classguide,
    classmeta,
    coverage,
    eventcache,
    forever,
    gearmode,
    insights,
    journal,
    profcaps,
    quests,
    riding,
    talents,
)
from wowstats import gear as gearmod
from wowstats.analysis import pages, prep
from wowstats.stores import (
    FLIGHT_RECORDS,
    HISTORY_START,
    SESSION_BUCKET_OPS,
    SESSION_BUCKETS,
    ZONE_RECORDS,
    SourceError,
    Store,
    dedupe,
    iso,
    jlist,
    lq,
    newest,
    num,
    opt_num,
    parse_t,
    pick_sheet,
    pick_splits,
    public_error,
    sheet_rows,
    split_sessions,
    taxi_time,
    truthy,
    zone_time,
    zone_totals,
)

GROUPED = {"grouped", "dungeon"}


@dataclass
class Settings:
    """What the UI server was started with (wowstats.ui reads the environment)."""

    view: str = "full"  # "full" or "public"
    default_char: str = ""
    price_days: int = 90
    weights: dict = field(default_factory=dict)  # WEIGHTS: character -> spec the gear is scored for
    ah_shared: str = ""  # AH_SHARED_STORE: the install's shared auction scans (wowstats.ahshared)
    # Where the person's per-character choices are kept (ANALYSIS_STATE_DIR,
    # WOW_STATE_DIR: wowstats.gearmode), for the PvP rewards toggle every
    # gear answer reads (gear_inputs).
    state: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)


class ConfigError(SystemExit):
    """A setting the server can't start with (wowstats.ui stops on it)."""


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


def settings_from_env(env: dict, view: str = "full") -> Settings:
    """The one place Settings are read from the environment: the page
    (wowstats.ui) and the MCP (wowstats.mcp.tools._views) both call it, so
    both score the gear for the same WEIGHTS. Review-3: the MCP built a bare
    Settings, its `weights` stayed empty, and its gear_options scored the
    add-on's spec while the page scored the configured one."""
    return Settings(view=view, default_char=env.get("DEFAULT_CHAR", ""),
                    price_days=int(env.get("PRICE_DAYS", str(ahshared.PRICE_DAYS))),
                    weights=parse_weights(env.get("WEIGHTS", "")),
                    ah_shared=ahshared.location(env),
                    state={k: env[k] for k in ("ANALYSIS_STATE_DIR", "WOW_STATE_DIR") if env.get(k)})


def norm_char(c: str) -> str:
    """The character-folder form: 'Testpriest Fixture' and 'Testpriest-Fixture' are one character."""
    return c.strip().replace(" ", "-")


def card_title(cls, spec: dict | None) -> str | None:
    """The character's one title line (owner, 2026-11-18), the same in the
    hero, the window title, the og tags and the MCP summary:
    "Priest · Discipline 16/0/7 (levelling Shadow)". The class; then the tree
    with the most points and the split in the game's tab order, every tree
    shown (talents.in_tab_order); then what the gear is scored for, only when that
    isn't the same tree. No talents known: the class alone."""
    if not isinstance(cls, str) or not cls.strip():
        return None
    cls = cls.strip().title()
    spec = spec or {}
    name, split = spec.get("name"), spec.get("split")
    if not name:
        return cls
    out = f"{cls} · {name}" + (f" {split}" if split else "")
    w = spec.get("scored_for") or {}
    if w.get("key") and w.get("label"):
        ref = gearmod.reference()
        token = gearmod.class_token(cls)
        # Battle.net names some trees longer than the specs ("Shadow Magic",
        # "Feral Combat"): a spec key the tree's name starts with is that tree.
        letters = "".join(c for c in name.lower() if c.isalpha())
        key = gearmod.spec_key(ref, token, name) or next(
            (s["key"] for s in gearmod.specs_of(ref, token) if letters.startswith(s["key"])), None)
        if key != w["key"]:
            out += f" (levelling {str(w['label']).split(' (')[0]})"
    return out


class ContextPool(concurrent.futures.ThreadPoolExecutor):
    """A pool whose tasks run in the submitter's context: the reads they make
    count for the answer that asked (wowstats.eventcache.reading, `as_of`)."""

    def submit(self, fn, /, *args, **kwargs):
        return super().submit(contextvars.copy_context().run, fn, *args, **kwargs)


class Views:
    def __init__(self, store: Store, settings: Settings | None = None):
        self.store = store
        self.settings = settings or Settings()
        self.pool = ContextPool(max_workers=8)
        self._aliases: dict[str, list[str]] = {}
        # The gear replay per character (wowstats.history): one per minute at
        # most, as every level's snapshot reads it.
        self._recon: dict[str, tuple[float, object]] = {}
        self._snap_in: dict[str, tuple[float, tuple]] = {}
        # The quests each character's history says are done (quests_history,
        # never Battle.net's): worked out once a minute at most.
        self._done: dict[str, tuple[float, dict[int, str]]] = {}
        # The account's Auctionator scans: /api/prices and the advice's
        # Auction House source read them once (price_rows).
        self._prices: tuple[float, list] | None = None
        self._prices_lock = threading.Lock()

    def expire(self) -> None:
        """Forget what the views kept a minute (the replay, the snapshots'
        inputs, the prices) and make the event cache ask again:
        the next answers are worked out from the stores as they are now."""
        self._recon.clear()
        self._snap_in.clear()
        self._done.clear()
        with self._prices_lock:
            self._prices = None
        if getattr(self.store, "use_cache", False):
            self.store.cache.expire()

    @staticmethod
    def _minute(cache: dict, char: str, fn):
        """fn() kept a minute in `cache` per character, with the event
        snapshots it read: a hit says the data it was built from (`as_of`,
        wowstats.eventcache.computed), not the data of the moment."""
        hit = cache.get(char)
        if hit and time.time() - hit[0] < 60:
            eventcache.replay(hit[2])
            return hit[1]
        out, seen = eventcache.computed(fn)
        cache[char] = (time.time(), out, seen)
        return out

    @property
    def stream(self) -> str:
        return self.store.stream

    def vl(self, query: str, keys: tuple = (), ops: dict | None = None, **kw):
        return self.store.vl(query, keys, ops, **kw)

    # ------------------------------------------------------------ characters

    def chars(self) -> list[dict]:
        """Every character with events, newest first (DEFAULT_CHAR on top)."""
        st = self.stream
        seen = self.pool.submit(self.vl, f"{st} char:* | stats by (char) count() as events, max(_time) as last_seen",
                                ("char",), {"events": "sum", "last_seen": "maxtime"})
        snap = self.pool.submit(self.vl, f'{st} feed:="wow-character" -kind:=played | stats by (char) '
                                         f"max(_time) as seen, row_max(_time, level, zone) as latest",
                                ("char",), {"seen": "maxtime"})
        demo = self.pool.submit(self.vl, f"{st} demo:=true | stats by (char) count() as n", ("char",), {"n": "sum"})
        snaps: dict[str, dict] = {}
        for r in snap.result():
            c = norm_char(r.get("char") or "")
            if c and (c not in snaps or parse_t(r.get("seen")) >= parse_t(snaps[c].get("seen"))):
                snaps[c] = r
        demo_chars = {norm_char(r.get("char") or "") for r in demo.result()}
        merged: dict[str, dict] = {}
        aliases: dict[str, list[str]] = {}
        for r in seen.result():
            raw = r.get("char")
            if not raw:
                continue
            c = norm_char(raw)
            aliases.setdefault(c, []).append(raw)
            m = merged.setdefault(c, {"char": c, "events": 0, "last_seen": None})
            m["events"] += int(num(r.get("events")))
            if parse_t(r.get("last_seen")) > parse_t(m["last_seen"]):
                m["last_seen"] = r.get("last_seen")
        out = []
        for c, m in merged.items():
            latest = {}
            if c in snaps:
                try:
                    latest = json.loads(snaps[c].get("latest") or "{}")
                except ValueError:
                    latest = {}
            out.append({**m, "level": opt_num(latest.get("level")), "zone": latest.get("zone"),
                        "demo": c in demo_chars})
        self._aliases = aliases
        dc = self.settings.default_char
        out.sort(key=lambda r: (r["char"] != dc, -parse_t(r["last_seen"]).timestamp()))
        return out

    def price_rows(self) -> list[dict]:
        """The Auctionator scans (wow-prices), one row per item, auction
        house (ruleset, faction) and scan day over price_days, at least the
        Auction House source's window: the price list and the gear advice
        share this one read per eventcache.FRESH_S (prices are the
        account's, not one character's, so not in the event cache). With
        several people on the install, the shared scans of this person's
        own houses (wowstats.ahshared): anyone's newest scan counts."""
        from wowstats.eventcache import FRESH_S

        with self._prices_lock:
            if self._prices and time.monotonic() - self._prices[0] < FRESH_S:
                return self._prices[1]
            days = max(self.settings.price_days, ahsource.WINDOW_DAYS)
            rows = ahshared.price_rows(self.store, self.settings.ah_shared,
                                       start=datetime.now(UTC) - timedelta(days=days))
            self._prices = (time.monotonic(), rows)
            return rows

    def char_filter(self, char: str) -> str:
        names = sorted(set(self._aliases.get(char) or [char]) | {char})
        if len(names) == 1:
            return f"char:={lq(char)}"
        return "char:in(" + ", ".join(lq(n) for n in names) + ")"

    def char(self, char: str) -> dict:
        cf = self.char_filter(char)
        st = self.stream
        rec = f'{st} feed:="wow-recorder" {cf}'
        sub = self.pool.submit
        vl, own = self.vl, self.q
        jobs = {
            "levels": sub(vl, f'{st} feed:="wow-level" {cf} | fields _time, level, seconds, xp, quests, '
                              f"kills, partial, source_id"),
            "snap": sub(sheet_rows, own, cf),
            "xp": sub(vl, f"{rec} kind:in(xp, level) | fields _time, kind, level, xp, xp_max, rested_xp, zone "
                          f"| sort by (_time desc) | limit 1"),
            "dings": sub(vl, f"{rec} kind:=level | fields _time, level, source_id"),
            # Altoholic's /played (optional): the shipper's kind=played events.
            "played": sub(vl, f'{st} feed:="wow-character" {cf} kind:=played | fields _time, played_total_s, '
                              f"played_level_s, level | sort by (_time desc) | limit 50"),
            "gear": sub(gearmod.sheet_rows, own, cf, "_time, at_basis, source_id, items, item_level, professions, "
                                                   "wanted, spec, spec_from, class, race, faction"),
            # The Recorder's skill records: each profession's cap (wowstats.profcaps).
            "skills": sub(vl, f"{rec} kind:=skill | fields _time, skill_id, rank, max_rank"),
            # Time per zone from the records' own times (stores.zone_time).
            "zones": sub(vl, f"{rec} {ZONE_RECORDS}"),
            "minutes": sub(vl, f"{rec} {SESSION_BUCKETS}", ("_time",), SESSION_BUCKET_OPS),
            "steps": sub(vl, f"{rec} kind:in(step, skip) | fields _time, kind, guide, step, reason, duration_s, "
                             f"zone, source_id"),
            "dungeons": sub(vl, f'{st} feed:="wow-dungeon" {cf} | fields _time, name, dungeon_id, level, '
                                f"seconds, xp, source_id"),
            "deaths": sub(vl, f"{rec} kind:=death | fields _time, zone, level, source_id"),
            "flights": sub(vl, f"{rec} {FLIGHT_RECORDS}"),
            "player_days": sub(vl, f'{st} feed:="wow-player" {cf} player:* | stats by (player, context, '
                                   f"_time:1d) count_uniq(source_id) as n",
                               ("player", "context", "_time"), {"n": "sum"}),
            "players": sub(vl, f'{st} feed:="wow-player" {cf} player:* | stats by (player) max(_time) as '
                               f"last_seen, max(level) as level, uniq_values(class) limit 3 as classes, "
                               f"uniq_values(race) limit 3 as races, uniq_values(zone) limit 12 as zones",
                           ("player",), {"last_seen": "maxtime", "level": "max", "classes": "union",
                                         "races": "union", "zones": "union"}),
            # The character's analytical copy, the semantic layer's (wowstats.analysis.copies).
            "layer": sub(self.layer_copy, char),
        }
        res = {k: f.result() for k, f in jobs.items()}
        levels = build_levels(res["levels"])
        dungeons = build_dungeons(dedupe(res["dungeons"]))
        death_rows = dedupe(res["deaths"])
        deaths = build_deaths(death_rows)  # sorts death_rows newest first
        # The numbers come from the semantic layer, one definition with the
        # MCP's query_metric; the rows above keep the facts around them. Its
        # failure is this answer's failure (SemanticError: the last good
        # answer stays, marked stale), never numbers counted here instead.
        layer = pages.numbers(char, res["layer"])
        pages.apply_rows(levels, dungeons, layer)
        pages.apply_deaths(deaths, layer, death_rows)
        if not death_rows and not res["minutes"]:
            # Nothing of the Recorder, which is what counts deaths: unknown,
            # never 0 (the MCP's deaths says the same).
            deaths["total"] = None
        gear = build_gear(gearmod.pick_sheet(res["gear"]))
        if not (gear and gear["items"]):
            # Syndicator's items win; without them (no gear event, or one
            # with the professions only), Battle.net's whole set.
            gear = with_battlenet_items(gear, gearmod.armory_worn(self.store, char),
                                        bool(getattr(self.store, "profile_vm_url", "")))
        if gear:
            gear["professions"] = profcaps.fill(gear["professions"], profcaps.newest_skills(res["skills"]))
            try:  # vm-wow down or absent: the item level computed, the silhouette
                arm = self.store.character_armory(char)
            except Exception:  # noqa: BLE001
                arm = {}
            add_armory(gear, arm)
        climb = build_climb(levels, res["dings"])
        # "Per level" over the levels the Recorder saw whole, the same ones
        # on both sides (review 6). The same gaps as the coverage note.
        spans = split_sessions(res["minutes"])
        deaths["per_level"] = (None if deaths["total"] is None else
                               deaths_per_level(deaths["by_level"], climb, spans,
                                                coverage.recorder_gaps(spans, res["levels"])))
        header = build_header(char, levels, res, gear, pages.header(layer))
        header.update(played_total(res["played"], levels, header))
        fill_from_battlenet(header, self.bnet_profile(char))
        cat = talents.shared().get()
        bnet = self.bnet_talents(char)
        cls = header.get("class") or (gear or {}).get("class")
        tal, spec = talents.from_battlenet(cat, cls, bnet)
        spec["scored_for"] = self.scored_for(char, gearmod.pick_sheet(res["gear"]), header, cat, tal)
        if spec.get("name"):
            header["spec"] = spec["name"]  # the hero line says what the talents are
        header["title"] = card_title(header.get("class") or (gear or {}).get("class"), spec)
        ref = self.route_ref()
        for split in levels:
            if split.get("route_s") is None and ref and ref.available:
                plan = ref.level(split["level"], header.get("faction"), header.get("class"))
                split["route_s"] = plan.get("expected_s") if plan else None
        return {
            "char": char,
            "header": header,
            "levels": levels,
            "route_source": ref.source if ref and ref.available else None,
            "climb": climb,
            "zones": zone_totals(zone_time(res["zones"], split_sessions(res["minutes"]))),
            "steps": build_steps(dedupe(res["steps"]), ref),
            "dungeons": dungeons,
            "deaths": deaths,
            "players": build_players(char, res["players"], res["player_days"]),
            "flights": build_flights(dedupe(res["flights"])),
            "gear": gear,
            "spec": spec,
            "talents": tal,
            "legacy": talents.legacy(cat, self.bnet_achievements()),
            # "Your class at level N" (wowstats.classguide): from the class,
            # race, side, level and talents only, never where the character is.
            "class_guide": classguide.block(header, tal),
            # "State of the meta" for the class and spec (wowstats.classmeta):
            # the knowledge base's words, from the class, spec and level only.
            "meta": classmeta.paragraph(header.get("class") or (gear or {}).get("class"),
                                        spec.get("name") or header.get("spec"), header.get("level")),
        }

    # ------------------------------------------------------------ gear advice

    def scored_for(self, char: str, row: dict, header: dict, cat, tal: dict) -> dict | None:
        """What the gear advice scores this character for (the player's
        choice: WEIGHTS, else the add-ons' settings), and, when the talents
        are known and level 40 is near, whether that tree's 31-point talent
        needs a respec (sanity_check; the full view only, redact drops it)."""
        if not row:
            return None
        ref = gearmod.reference()
        sheet = gearmod.sheet(row, header)
        sheet["talents"] = (tal.get("points") or None) if tal.get("available") else None
        w = gearmod.scored_for(ref, sheet.get("class"), sheet, self.settings.weights.get(char))
        if not w:
            return None
        pts = {t["tree"]: t["points"] for t in tal.get("points") or [] if t.get("tree")} if tal.get("available") \
            else {}
        cap = talents.capstone(cat, sheet.get("class"))
        tree = next((t for t in pts if gearmod.spec_key(ref, gearmod.class_token(sheet.get("class")), t) == w["key"]),
                    None)
        w["sanity_check"] = gearmod.capstone_check(ref, sheet.get("class"), w["key"], header.get("level"), pts,
                                                   cap.get(tree) if tree else None)
        return w

    def bnet_talents(self, char: str) -> dict | None:
        """Battle.net's talents (stores.character_talents, from vm-wow), the
        only source of talents since 2026-11-20. None when vm-wow is absent
        or the API doesn't know the character; {"error": True} when vm-wow
        didn't answer, so the card says so rather than "not read yet" (#92)."""
        try:
            return self.store.character_talents(char)
        except Exception:  # noqa: BLE001
            return {"error": True}

    def addon_talents(self, char: str, cls) -> list[dict]:
        """The builds WoWSims Exporter saved (the shipper's wow-talents
        events: the player's own client data, kept like any other event),
        oldest first, as talents.history() reads them; one per change, an
        unreadable string left out (wowstats.wowsims)."""
        from wowstats import wowsims

        rows = self.q(f'feed:="wow-talents" {self.char_filter(char)} | fields _time, talents, level, class '
                      f"| sort by (_time) | limit 5000")
        cat = talents.shared().get()
        out: list[dict] = []
        for r in sorted(rows, key=lambda r: parse_t(r.get("_time"))):
            got = wowsims.decode(cat, cls or r.get("class"), r.get("talents"))  # the addon saves the class too
            if got is None or (out and {k: out[-1][k] for k in ("trees", "ranks")} == got):
                continue
            out.append({"t": r.get("_time"), **got})
        return out

    def talent_history(self, char: str, cls) -> dict:
        """How the talents changed (owner, 2026-11-20). From WoWSims
        Exporter's saves when the player has it (every change, kept like any
        event: "source": "addon", days None), else Battle.net's daily reads
        of the last 3 weeks, read live from vm-wow on every call and kept
        nowhere else (Blizzard's 30-day cap: never a log store, a file, a
        bundle or a cache). {"available", "why", "source", "days", "since",
        "changes": talents.history(), "not_recorded"}; why is "no_client"
        (no vm-wow on this site), "no_battlenet" (no reading in the window)
        or "unreachable" (vm-wow didn't answer). not_recorded: talents
        Battle.net shows learned that the addon can't record (the sim
        doesn't model them), so a gap is said, never hidden."""
        from wowstats import wowsims

        days = getattr(self.store, "TALENT_HISTORY_DAYS", 21)
        out = {"char": char, "days": days, "available": False, "why": "no_battlenet", "source": None, "since": None,
               "changes": [], "not_recorded": []}
        cat = talents.shared().get()
        try:
            mine = self.addon_talents(char, cls)
        except SourceError as e:
            # The logs store down: Battle.net's, as without the addon, but
            # said so. It read as "install the addon" to a player who has it
            # (invariants, property 7).
            mine = []
            out["addon_unread"] = public_error(e, "the add-on's talent saves")
        from wowstats import storemodels

        if mine:
            gaps = wowsims.missing_from(cat, cls, self.bnet_talents(char))
            return {**out, "available": True, "why": None, "source": "addon", "days": None,
                    "since": str(mine[0]["t"])[:10],
                    "changes": storemodels.talent_changes(talents.history(cat, cls, mine)), "not_recorded": gaps}
        # The one step to the full history (degraded mode): the addon, as steps.json names it.
        from wowstats import missing

        a = missing.addons().get("wowsims") or {}
        out["addon"] = {"name": a.get("name"), "url": a.get("url")} if a else None
        if not getattr(self.store, "profile_vm_url", ""):
            return {**out, "why": "no_client"}
        try:
            readings = self.store.talent_readings(char, days)
        except Exception:  # noqa: BLE001
            return {**out, "why": "unreachable"}
        if not readings:
            return out
        # Battle.net's readings go through the model for this call only (semantic.read keeps nothing).
        return {**out, "available": True, "why": None, "source": "battlenet", "since": str(readings[0]["t"])[:10],
                "changes": storemodels.talent_changes(talents.history(cat, cls, readings))}

    def bnet_profile(self, char: str) -> dict:
        """Battle.net's profile (stores.character_profile), {} when vm-wow
        is down, absent, or holds nothing read in the last 30 days."""
        try:
            return self.store.character_profile(char) or {}
        except Exception:  # noqa: BLE001
            return {}

    def bnet_achievements(self) -> dict | None:
        """The account's completed achievements on Battle.net (the Legacy
        points); None when vm-wow is absent, {"error": True} when it didn't
        answer (talents.legacy says so, #92)."""
        try:
            return self.store.account_achievements()
        except Exception:  # noqa: BLE001
            return {"error": True}

    def talent_points(self, char: str, cls) -> list[dict] | None:
        """Points per tree under the frame's own names, in the game's tab
        order (Battle.net says "Shadow Magic" and lists it second), for the
        gear advice's spec detection. None without Battle.net's talents."""
        tal, _ = talents.from_battlenet(talents.shared().get(), cls, self.bnet_talents(char))
        return (tal.get("points") or None) if tal.get("available") else None

    def quests_done(self, char: str, worn: dict | None = None) -> dict[int, str]:
        """Every quest the character has done -> what says so: the history
        (quests_history), plus what Battle.net says it wears now
        (wowstats.quests.with_battlenet; `worn`: gear.armory_worn's answer
        when the caller has it, else read here, live). The one answer Gear,
        the Overview and the MCP read."""
        if worn is None:
            worn = gearmod.armory_worn(self.store, char)
        return quests.with_battlenet(self.quests_history(char), quests.worn_ids(worn))

    def quests_history(self, char: str) -> dict[int, str]:
        """wowstats.quests.history: the Recorder, the quest addons and every
        gear and bags sheet it ever sent, so a reward sold or replaced since
        still counts. No Battle.net data, so it may be cached a minute."""

        st = self.stream
        cf = self.char_filter(char)
        return self._minute(self._done, char, lambda: quests.history(lambda q: self.vl(f"{st} {q}"),
                                                                     f'feed:="wow-recorder" {cf}', cf))

    def gear(self, char: str, spec: str | None = None, mode: str | None = None, pvp: bool | None = None) -> dict:
        """Gear advice (wowstats.gear): per slot what is worn, the best item
        at this level and the next one coming, and the upgrades in order.
        spec: the page's choice, else Polestar's setting or the player's pick.
        mode: the weights' mode (Leveling, PvP, PvE), Leveling by default.
        pvp: whether PvP rewards count, else the character's saved toggle."""
        gi = self.gear_inputs(char)
        if not gi["available"]:
            return gi
        if pvp is not None:
            gi = {**gi, "pvp": pvp}
        try:
            advice = gearmod.advise(gearmod.reference(), gi["sheet"], spec=spec, mode=mode, **gear_args(gi))
        except gearmod.GearError as e:
            return {**gi["base"], "available": False, "why": str(e)}
        return {**gi["base"], "available": True, "at": gi["at"], "talents": gi["sheet"].get("talents"), **advice,
                **({} if gi["worn_from"] == "sheet" else gearmod.unknown_worn(gi["worn_from"]))}

    def gear_inputs(self, char: str) -> dict:
        """What the doll's advice is scored from: the newest sheet with
        Battle.net's talents and worn stats, the level, the quests done, the
        bags and the auction house. The planner scores the Dungeons panel and
        the Overview's dungeon moves from these same inputs (wowstats.plan
        inputs `gear_inputs`), so every item line's BiS share starts from the
        doll's badge (owner, 2026-11-21: one number per slot). {"available":
        False, "why"} without a sheet to score."""
        cf = self.char_filter(char)
        st = self.stream
        rec = f'{st} feed:="wow-recorder" {cf}'
        sub = self.pool.submit
        vl, own = self.vl, self.q
        jobs = {
            "gear": sub(gearmod.sheet_rows, own, cf),
            "snap": sub(sheet_rows, own, cf),
            "xp": sub(vl, f"{rec} kind:in(xp, level, zone) | fields _time, kind, level, zone "
                          f"| sort by (_time desc) | limit 1"),
            # Quests already done (wowstats.quests): their rewards are gone.
            "done": sub(self.quests_history, char),
            # What the bags and the bank hold: those upgrades are an equip away.
            "held": sub(gearmod.held_rows, own, cf),
            # The quest log and the guide step: a quest source's work
            # (wowstats.questwork) reads both.
            "log": sub(vl, f"{rec} {quests.Q_LOG}"),
            "step": sub(vl, f"{rec} {quests.Q_STEP}"),
        }
        # Reputations and the PvP rank: a quartermaster's item is an upgrade
        # once the standing is held (wowstats.gear rep_check). Optional, like
        # the prices: a read that fails leaves them unknown, never the advice.
        standings = sub(gearmod.standing_rows, own, cf)
        # What is on sale on the auction house (wowstats.ahsource): the
        # scans are the price list's own read.
        ah = {k: sub(vl, f"{st} {q}", keys, ops)
              for k, (q, keys, ops) in ahsource.queries(cf, f'feed:="wow-recorder" {cf}').items()}
        ah["items"] = sub(self.price_rows)
        res = {k: f.result() for k, f in jobs.items()}
        row = gearmod.pick_sheet(res["gear"])
        header = build_header(char, [], res)
        fill_from_battlenet(header, self.bnet_profile(char))
        base = {"char": char, "name": header["name"], "polestar": header["polestar"]}
        worn = gearmod.armory_worn(self.store, char)
        worn_from = "sheet"
        if not gearmod.sheet_has_items(row):
            # No items from Syndicator (no gear event, or one with the
            # professions only): Battle.net's equipment, else every slot
            # open (the best items for the level), said to be an estimate.
            if not (header.get("class") or (row or {}).get("class")):
                return {**base, "available": False, "why": "no character sheet yet"}
            row, worn_from = gearmod.no_sheet(header, worn, row)
        sheet = gearmod.sheet(row, header)
        sheet["talents"] = self.talent_points(char, sheet.get("class"))  # spec detection: Battle.net only
        sheet["worn_stats"] = worn
        try:
            sheet["reps"], sheet["pvp_rank"] = gearmod.standings_of(standings.result())
        except SourceError:
            sheet["reps"], sheet["pvp_rank"] = None, None
        done = set(quests.with_battlenet(res["done"], quests.worn_ids(worn)))
        try:
            market = ahsource.market({k: f.result() for k, f in ah.items()}, faction=sheet.get("faction"),
                                     money=header.get("money_copper"))
        except SourceError:
            market = None  # no prices is no reason to lose the advice
        return {"available": True, "base": base, "at": row.get("_time"), "worn_from": worn_from, "sheet": sheet,
                "level": header.get("level"), "zone": header.get("zone"), "done": done,
                "log": quests.in_log(res["log"], done), "route": quests.position(res["step"]),
                "weights": self.settings.weights.get(char), "held": gearmod.newest_bags(res["held"]),
                "market": market,
                # The PvP rewards toggle (wowstats.gearmode): off unless saved.
                "pvp": gearmode.pvp(self.settings.state, char)}

    def meta(self, char: str, data: dict | None = None, advice: dict | None = None, cite: bool = False) -> dict:
        """The class's meta for this character and how close it is to it
        (wowstats.classmeta): the character page's answer and the gear
        advice, as the page has them."""
        data = data if data is not None else self.char(char)
        if advice is None:
            try:
                advice = self.gear(char)
            except Exception:  # noqa: BLE001 - no gear advice: the other parts still count
                advice = None
        return classmeta.answer(data, advice, cite=cite)

    # ------------------------------------------------------------ insights

    def q(self, query: str) -> list[dict]:
        """A LogsQL query after the stream filter (insights.Q)."""
        return self.vl(f"{self.stream} {query}")

    @staticmethod
    def route_ref():
        try:
            from wowstats.mcp import route
        except ImportError:
            return None
        return route.reference()

    @staticmethod
    def who(data: dict) -> dict:
        hd = data.get("header") or {}
        return {"faction": hd.get("faction"), "race": hd.get("race"), "class": hd.get("class")}

    def insights(self, char: str, data: dict | None = None, advice: dict | None = None) -> dict:
        """Everything the Overview, next_moves and sanity_check read, in one
        pass: the character page's data, gear advice, deaths with their
        likely killer, dungeon runs, sessions, skills and guide steps."""
        cf = self.char_filter(char)
        rec = insights.rec(cf)
        sub = self.pool.submit
        jobs = {
            "data": sub(self.char, char) if data is None else None,
            "advice": sub(self.gear, char) if advice is None else None,
            "steps": sub(self.q, f"{rec} kind:=step | fields _time, guide, step, reason, source_id"),
            "sessions": sub(insights.sessions, self.q, cf),
            "skills": sub(self.q, f"{rec} kind:=skill | fields _time, skill_id, rank, max_rank, source_id"),
            "runs": sub(insights.dungeon_runs, self.q, cf),
            **{k: sub(self.q, q) for k, q in riding.queries(cf, rec).items()},
        }
        res = {k: (f.result() if f else None) for k, f in jobs.items()}
        data = data if data is not None else res["data"]
        advice = advice if advice is not None else res["advice"]
        ds = insights.deaths(self.q, cf, data.get("levels") or [])
        steps_rows = sorted(dedupe(res["steps"]), key=lambda r: parse_t(r.get("_time")))
        skills = {int(opt_num(r["skill_id"])): {"rank": int(num(r.get("rank"))), "max_rank": int(num(r.get(
            "max_rank"))), "t": r.get("_time")} for r in sorted(dedupe(res["skills"]), key=lambda r: parse_t(
                r.get("_time"))) if opt_num(r.get("skill_id")) is not None}
        # Riding from the spellbook too: the Recorder misses a training it didn't see.
        skills = riding.merge(skills, res["riding_book"], res["riding_learn"])
        return {"data": data, "advice": advice if (advice or {}).get("available") else None, "deaths": ds,
                "runs": res["runs"], "sessions": res["sessions"], "steps_rows": steps_rows, "skills": skills,
                "skill_rows": sorted(dedupe(res["skills"]), key=lambda r: parse_t(r.get("_time")))}

    def layer_copy(self, char: str):
        """The character's analytical copy (wowstats.analysis.copies), the
        one the MCP's tools and the planner read too; None: no events."""
        from wowstats.analysis import copies

        # The view's own store: the same key as the MCP's (copies.key_of reads
        # the stores' addresses and tenant, not the object).
        return copies.of(self.store, char)

    def layer_ask(self, char: str):
        """The semantic layer on the character's copy (the one the character
        page's numbers read: wowstats.analysis.pages.asker), for the
        Overview's pace and death counts. A failure raises SemanticError:
        the Overview fails like any answer, never shows its lines without
        the layer's counts."""
        return pages.asker(char, self.layer_copy(char))

    def overview(self, char: str) -> dict:
        """The home page of a character: on track or not, what's happening
        now, how the guide is followed, the top upgrades, deaths, the two
        questions (next moves, sanity check) and the Overview's one list of
        moves made from both. Every line links its section."""
        x = self.insights(char)
        data, advice, ref = x["data"], x["advice"], self.route_ref()
        who = self.who(data)
        hd = data.get("header") or {}
        gear = data.get("gear") or {}
        ask = self.layer_ask(char)
        ov = insights.overview(data, advice, x["deaths"], ref, who, x["steps_rows"], x["sessions"], ask)
        profs = gear.get("professions") or []
        talents = (advice or {}).get("talents") if advice else None
        ov["next_moves"] = insights.next_moves(hd.get("level"), advice, hd, ov["on_track"], ov["guide"], profs,
                                               x["skills"])
        ov["sanity_check"] = insights.sanity_check(hd.get("level"), x["deaths"], ov["guide"], ov["on_track"],
                                                   advice, profs, x["skills"], talents, gear.get("items"), hd,
                                                   ask=ask)
        ov["moves"] = insights.overview_moves(ov["next_moves"], ov["sanity_check"])
        ov["deaths_detail"] = x["deaths"][::-1][:15]
        ov["clusters"] = insights.death_clusters(ask, x["deaths"])
        ov["runs"] = x["runs"]
        ov["roster_note"] = insights.ROSTER_NOTE
        ov["char"] = char
        ov["header"] = hd
        ov["route_source"] = ref.source if ref and ref.available else None
        return ov

    def gear_history(self, char: str) -> list[dict]:
        rows = self.q(f'feed:="wow-gear" {self.char_filter(char)} | fields _time, items, item_level, professions, '
                      f"spec, class, race, faction, source_id")
        out = []
        for r in sorted(dedupe(rows), key=lambda r: parse_t(r.get("_time"))):
            g = build_gear(r) or {}
            out.append({**g, "_time": r.get("_time")})
        return out

    def timeline(self, char: str) -> dict:
        x = self.insights(char, advice={})
        gh = self.gear_history(char)
        ev = insights.timeline(x["data"], x["deaths"], x["runs"], x["sessions"], gh, x["skill_rows"],
                               x["steps_rows"], self.route_ref())
        levels = sorted(insights.windows(x["data"]))
        return {"char": char, "events": ev, "kinds": list(insights.TIMELINE_KINDS), "levels": levels,
                "gear_history_since": gh[0]["_time"] if gh else None}

    def skill_records(self, char: str) -> list[dict]:
        """The Recorder's skill records (rank and cap, each time the skill
        list changed): the professions' caps (wowstats.profcaps)."""
        rec = f'{self.stream} feed:="wow-recorder" {self.char_filter(char)}'
        return self.vl(f"{rec} kind:=skill | fields _time, skill_id, rank, max_rank")

    def snapshot_inputs(self, char: str) -> tuple[dict, dict, list[dict], list[dict], list[dict]]:
        """What every level's snapshot reads and no level changes: the
        character's data, its level windows, its deaths (each with a fight
        lookup) and its gear history; cached a minute, like reconstruction.
        The publisher asks for every level in a row: rebuilt per level, these
        made a long history's build quadratic in its deaths."""

        def build():
            data = self.char(char)
            ds = list(insights.deaths(self.q, self.char_filter(char), data.get("levels") or []))
            return data, insights.windows(data), ds, self.gear_history(char), self.skill_records(char)

        return self._minute(self._snap_in, char, build)

    def snapshot(self, char: str, level: int) -> dict:
        """The character as it was at `level` (the level slider)."""
        data, wins, ds, gh, skills = self.snapshot_inputs(char)
        if level not in wins:
            return {"char": char, "level": level, "available": False,
                    "why": "no record of that level (the recording started later)"}
        cf = self.char_filter(char)
        snap = insights.snapshot(level, data, wins[level], gh, self.q, cf, self.route_ref(), self.who(data), ds)
        g = snap.pop("gear")
        snap.update(char=char, available=True, levels=sorted(wins))
        if g:
            snap["gear"] = {k: g.get(k) for k in ("items", "item_level", "professions")}
            # The caps as they were at the end of that level: the Recorder's
            # records up to then, else the trainer tier (the same rule as
            # the page's current caps).
            end = wins[level][1] or datetime.now(UTC)
            snap["gear"]["professions"] = profcaps.fill(snap["gear"]["professions"] or [],
                                                        profcaps.newest_skills(skills, until=end.timestamp()))
            snap["gear"]["items"] = [{**i, "certainty": "exact"} for i in snap["gear"]["items"] or []]
            snap["bis"] = self._bis_at(char, level, g, data)
        else:
            # No sheet from then: the replay of what the Recorder saw it get
            # (wowstats.history), every slot "likely", never a fact; slots it
            # has nothing for stay out (unknown), and so does any level that
            # ended before the first record.
            recon = self.reconstruction(char, data, gh)
            t1 = wins[level][1] or datetime.now(UTC)
            if recon is not None and t1 >= recon[0].start:
                from wowstats import history

                items = history.items_at(gearmod.reference(), recon[0], t1, recon[2])
                if items:
                    snap["gear"] = {"items": items, "item_level": None}
                    snap["gear_reconstructed"] = True
                    snap["gear_records_from"] = iso(recon[0].start)
                    snap["gear_check"] = recon[1]
        if not g:
            from wowstats import gear as gm
            ref = gm.reference()
            looted = []
            for iid in snap.pop("looted_item_ids", []):
                info = ref.item(iid)
                if info and info.get("inv") and (info.get("quality") or 0) >= 2:
                    looted.append({"item_id": iid, "name": (ref.data.get("names") or {}).get(str(iid))
                                   or forever.item_name(iid),
                                   "inv": info.get("inv"), "item_level": info.get("ilvl")})
            snap["looted_gear"] = looted[:20]
        return snap

    def reconstruction(self, char: str, data: dict, gh: list[dict]):
        """(the anchored replay, its agreement with the first sheet, the
        shipped item names) or None, cached a minute."""
        return self._minute(self._recon, char, lambda: self._reconstruct(char, data, gh))

    def _reconstruct(self, char: str, data: dict, gh: list[dict]):
        from wowstats import history
        from wowstats import items as itemsmod

        base = insights.rec(self.char_filter(char))
        turnins, loots, crafts, lvls = self.pool.map(lambda q: dedupe(self.q(f"{base} {q}")), (
            history.Q_TURNIN, history.Q_LOOT, history.Q_CRAFT, "kind:=level | fields _time, level"))
        dings = insights.dings_from(data.get("levels") or [], lvls)
        hd = data.get("header") or {}
        last = gh[-1] if gh else {}
        sheet = {"class": (hd.get("class") or last.get("class") or "").upper(),
                 "race": hd.get("race") or last.get("race"), "faction": hd.get("faction"), "spec": last.get("spec")}
        first = gh[0] if gh else None
        try:
            out = history.build(gearmod.reference(), sheet, turnins, loots, crafts, dings,
                                {"_time": first.get("_time") or first.get("at"), "items": first.get("items") or []}
                                if first else None)
        except (gearmod.GearError, ValueError, TypeError, KeyError):
            out = None
        if out is not None:
            names = itemsmod.shared()
            for sp in (x for spans in out[0].slots.values() for x in spans):
                key = str(sp.item_id)
                if sp.item_id and key not in out[2]:
                    nm = names.get(sp.item_id)[0]
                    if nm:
                        out[2][key] = nm
        return out

    def _bis_at(self, char: str, level: int, g: dict, data: dict) -> dict | None:
        """The gear advice of that time: the sheet then, at that level."""
        hd = data.get("header") or {}
        row = {"items": json.dumps(g.get("items") or []), "class": (hd.get("class") or "").upper(),
               "race": hd.get("race"), "faction": hd.get("faction"), "spec": g.get("spec"),
               "professions": json.dumps(g.get("professions") or [])}
        try:
            sheet = gearmod.sheet(row, hd)
            a = gearmod.advise(gearmod.reference(), sheet, level=level, weights=self.settings.weights.get(char))
        except (gearmod.GearError, ValueError, TypeError):
            return None
        return {"gear_score": a.get("gear_score"), "upgrades": a.get("upgrades", [])[:5], "spec": a.get("spec")}

    def loot(self, char: str) -> dict:
        """What the character looted (Polestar_Recorder's loot records), one
        group per dungeon run and one per level for the rest, newest first.
        Green and better items are the notable ones; the rest are summed per
        item. No creature names, no positions: item, quality, when, where,
        at what level."""
        from wowstats import items as itemsmod

        cf = self.char_filter(char)
        rows = dedupe(self.q(f'{insights.rec(cf)} kind:=loot items:* | fields _time, items, item_names, zone, '
                             "source_id"))
        runs = sorted(dedupe(self.q(f'feed:="wow-dungeon" {cf} | fields _time, left, seconds, name, level, '
                                    "source_id")), key=lambda r: parse_t(r.get("_time")))
        data = self.char(char)
        dings = insights.dings_from(data.get("levels") or [], [])
        cur = (data.get("header") or {}).get("level")
        names = itemsmod.shared()
        wins = []
        for r in runs:
            t0 = parse_t(r.get("_time"))
            t1 = parse_t(r["left"]) if r.get("left") else t0 + timedelta(seconds=num(r.get("seconds")))
            wins.append((t0, t1, r))
        groups: dict[str, dict] = {}
        total = 0
        for r in sorted(rows, key=lambda r: parse_t(r.get("_time"))):
            t = parse_t(r.get("_time"))
            run = next((w[2] for w in wins if w[0] <= t <= w[1]), None)
            lvl = insights.level_at(dings, t, cur)
            if run:
                key = "run:" + (run.get("source_id") or run.get("_time") or "")
                g = groups.setdefault(key, {"kind": "dungeon", "name": run.get("name"),
                                            "level": opt_num(run.get("level")), "t": run.get("_time"), "items": {}})
            else:
                key = f"level:{lvl}"
                g = groups.setdefault(key, {"kind": "level", "name": None, "level": lvl, "t": r.get("_time"),
                                            "items": {}})
            try:
                shipped = json.loads(r.get("item_names") or "{}")  # the shipper's names (LibForeverData)
            except ValueError:
                shipped = {}
            for part in str(r.get("items") or "").split(";"):
                iid, _, n = part.partition(":")
                if not iid.isdigit():
                    continue
                total += 1
                it = g["items"].get(iid)
                if it is None:
                    name, quality = names.get(int(iid))
                    name = name or (shipped.get(iid) if isinstance(shipped, dict) else None)
                    it = g["items"][iid] = {"item_id": int(iid), "name": name, "quality": quality, "count": 0,
                                            "t": r.get("_time"), "zone": r.get("zone")}
                it["count"] += int(n) if n.isdigit() else 1
        out = []
        for g in groups.values():
            its = sorted(g["items"].values(), key=lambda i: (-(i["quality"] or 0), i["t"] or ""))
            notable = [i for i in its if (i["quality"] or 0) >= 2]
            out.append({**g, "items": its, "notable": len(notable)})
        out.sort(key=lambda g: parse_t(g["t"]), reverse=True)
        return {"char": char, "groups": out, "total": total,
                "notable": sum(g["notable"] for g in out), "quality_known": names.table != {}}

    def people(self, char: str) -> dict:
        """FULL view only: other players seen (Polestar's sightings) and the
        dungeon runs with what the addons saved about each group."""
        data = self.char(char)
        runs = insights.dungeon_runs(self.q, self.char_filter(char))
        players = data.get("players") or {}
        return {"char": char, "players": players, "runs": runs, "roster_note": insights.ROSTER_NOTE,
                "sources": insights.GROUP_SOURCES}

    # ------------------------------------------------------------ journal

    def journal(self, char: str) -> dict:
        cf = self.char_filter(char)
        st = self.stream
        rec = f'{st} feed:="wow-recorder" {cf}'
        sub = self.pool.submit
        vl = self.vl
        jobs = {
            # Activity per minute: the session boundaries (stores.split_sessions).
            "minutes": sub(vl, f"{rec} {SESSION_BUCKETS}", ("_time",), SESSION_BUCKET_OPS),
            # The bar's readings, not the shipper's xp_gained: a chunk recovered
            # late left that short (prep.xp_gains, the analysis' own rule).
            "xp": sub(vl, f"{rec} kind:=xp | fields _time, xp, xp_max, source_id, rev"),
            "events": sub(vl, f"{rec} kind:in(session, level, zone, turnin, death, taxi, land) "
                              f"| fields _time, kind, login, level, zone, quest_id, quest_name, seconds, "
                              f"source_id"),
            "dungeons": sub(vl, f'{st} feed:="wow-dungeon" {cf} | fields _time, name, level, seconds, xp, '
                                f"source_id"),
            "players": sub(vl, f'{st} feed:="wow-player" {cf} player:* | fields _time, player, context, '
                               f"source_id"),
            "snaps": sub(vl, f'{st} feed:="wow-character" {cf} -kind:=played | fields _time, money_copper, level, '
                             f"xp, source_id"),
        }
        res = {k: f.result() for k, f in jobs.items()}
        xp = [{"_time": r.get("_time"), "xp": gained} for r, gained in prep.xp_gains(res["xp"])]
        sessions = journal.build(
            minutes=res["minutes"], xp=xp, events=dedupe(res["events"]),
            dungeons=dedupe(res["dungeons"]), players=dedupe(res["players"]), snaps=dedupe(res["snaps"]),
            own=char)
        # Before the Recorder's first record (or without it): one entry per
        # logout sheet, the time played from the level splits (estimated).
        levels = self.vl(f'{st} feed:="wow-level" {cf} | fields _time, level, seconds, xp, partial')
        sessions = journal.with_history(sessions, dedupe(res["snaps"]), levels, dedupe(res["dungeons"]))
        # Play the Recorder missed (wowstats.coverage): the session it cuts
        # into says so, or its 0 deaths reads as none.
        spans = split_sessions(res["minutes"])
        gaps = coverage.recorder_gaps(spans, levels)
        journal.mark_gaps(sessions, gaps, spans)
        quests = [q for s in sessions for q in s["quests"]]
        missing = {q["quest_id"] for q in quests if not q.get("name") and not forever.quest(q.get("quest_id"))}
        forever.name_quests(quests, self.quest_titles(cf, missing))
        return {"char": char, "sessions": sessions, "gap_s": journal.GAP_S, "recorder_gaps": gaps}

    def quest_titles(self, cf: str, ids) -> dict[int, str]:
        """The Recorder's own quest titles (`text` records: the quest's page
        as the game showed it), for quests forever-db doesn't have."""
        ids = sorted({int(i) for i in ids if isinstance(i, int)})
        if not ids:
            return {}
        rows = self.vl(f'{self.stream} feed:="wow-recorder" {cf} kind:=text quest_id:in({",".join(map(str, ids))}) '
                       f"title:* | stats by (quest_id) max(title) as title", ("quest_id",), {"title": "first"})
        return {n: r["title"] for r in rows if (n := opt_num(r.get("quest_id"))) is not None and r.get("title")}

    # ------------------------------------------------------------ prices

    def prices(self) -> dict:
        """The player's own Auctionator scans (wow-prices): the latest price per
        item and its daily lows over the last price_days."""
        rows = self.price_rows()
        if self.settings.price_days < ahsource.WINDOW_DAYS:  # the shared read went further back
            since = (datetime.now(UTC) - timedelta(days=self.settings.price_days)).timestamp()
            rows = [r for r in rows if parse_t(r.get("t")).timestamp() >= since]
        return build_prices(rows)

    # ------------------------------------------------------------ market (Battle.net API)

    def market(self) -> dict:
        """WoW Forever has no token and no realms (Battle.net API): the token
        is Retail's (game="retail") and wow_realm_up is one series per
        ruleset. No auction house: the project reads no Auction House API
        (prices are the player's own Auctionator scans, prices())."""
        if not self.store.vm_url:
            return {"token": None, "realms": [], "configured": False, "reason": "no-exporter"}
        now = datetime.now(UTC)
        sub = self.pool.submit
        s = self.store
        jobs = {
            "token": sub(s.vm_range, 'wow_token_price_gold{game="retail"}', now - timedelta(days=7), now, "1h"),
            "realm": sub(s.vm_query, "last_over_time(wow_realm_up[15m])"),
            "realm_day": sub(s.vm_query, "min_over_time(wow_realm_up[24h])"),
        }
        res = {k: f.result() for k, f in jobs.items()}
        return build_market(res)

    # ------------------------------------------------------------ debug

    def logsql(self, query: str, limit: int = 200) -> list[dict]:
        """A LogsQL query after the events filter, for the full view's debug box.
        Not a boundary: `or *` widens it to the whole store it reads. The
        tenant (AccountID, stores.tenant_headers) is what keeps a person to
        their own events (security review info 6)."""
        q = query.strip()
        if not q or len(q) > 2000:
            raise ValueError("a LogsQL query of at most 2000 characters")
        from wowstats.eventstore import Unanswerable

        try:
            return self.store.vl_raw(self.store.vl_url, f"{self.stream} {q} | limit {int(limit)}",
                                     HISTORY_START, datetime.now(UTC), limit)
        except Unanswerable as e:  # the SQLite store: say what it can't answer, it's the asker's query
            raise ValueError(str(e)) from None


# ---------------------------------------------------------------- builders


def build_levels(rows: list[dict]) -> list[dict]:
    """The level splits as the MCP's level tools list them (stores.pick_splits
    picks one per level): the facts (which level, partial, when). Its
    numbers are the semantic layer's alone (wowstats.analysis.pages.apply_rows)."""
    return [{"level": lvl, "seconds": None, "xp": None, "quests": None, "kills": None,
             "partial": truthy(r.get("partial")), "at": r.get("_time"), "xp_per_hour": None}
            for lvl, r in sorted(pick_splits(rows).items())]


def gear_args(gi: dict) -> dict:
    """advise()'s keyword arguments from Views.gear_inputs (the sheet apart)."""
    return ({k: gi[k] for k in ("level", "zone", "done", "weights", "held", "market")}
            | {"pvp": bool(gi.get("pvp")), "log": gi.get("log"), "route": gi.get("route")})


def build_header(char: str, levels: list[dict], res: dict, gear: dict | None = None,
                 layer: dict | None = None) -> dict:
    """The hero line. `layer`: the semantic layer's numbers for it
    (wowstats.analysis.pages.header), used as they are: the only way the
    pace, the averages and the counts are computed."""
    snap, xp = pick_sheet(res["snap"]), newest(res["xp"])
    # The newest of the account snapshot and the recorder wins for each value.
    top = snap if parse_t(snap.get("_time")) >= parse_t(xp.get("_time")) else xp
    # The recorder's xp records name no level: without a snapshot, the
    # level is the newest ding's, else one past the newest finished split.
    ding = newest(res.get("dings") or [])
    done_split = max((lv["level"] for lv in levels if not lv["partial"]), default=None)
    level = (opt_num(top.get("level")) or opt_num(snap.get("level")) or opt_num(xp.get("level"))
             or opt_num(ding.get("level")) or (done_split + 1 if done_split else None))
    gear = gear or {}
    # Rested XP is only in the Recorder's xp records: shown while that record
    # is for the current level (a ding or a newer snapshot makes it stale).
    rested = opt_num(xp.get("rested_xp")) if xp.get("kind") == "xp" and opt_num(xp.get("level")) in (
        None, level) else None
    if top is xp and xp.get("kind") == "level":
        cur_xp, xp_max = 0, None
    else:
        cur_xp, xp_max = opt_num(top.get("xp")), opt_num(top.get("xp_max"))
    if xp_max is None:
        xp_max = opt_num(snap.get("xp_max")) if opt_num(snap.get("level")) == level else None
    layer = layer or {}
    xph = layer.get("xp_per_hour")
    avg_s, avg_n = layer.get("avg_level_s"), layer.get("avg_levels") or 0
    splits_s = layer.get("splits_played_s")
    # The level in play has no split yet: its time so far is the layer's
    # in_level_s (its Recorder sessions since the ding), the MCP's own.
    current_s = layer.get("in_level_s")
    # Polestar writes a split when a level ends, so the level being played has
    # none yet; `partial` means the recording started partway into that level.
    current = next((lv for lv in levels if lv["level"] == level), None)
    eta = int((xp_max - cur_xp) / xph * 3600) if (xph and xp_max and cur_xp is not None and xp_max > cur_xp) else None
    cls = snap.get("class") or gear.get("class")
    return {
        "char": char,
        # Forever names are "First Surname": the folder form is First-Surname.
        "name": char.split("-", 1)[0],
        "full_name": char.replace("-", " "),
        "race": snap.get("race") or gear.get("race"),
        "class": cls.title() if isinstance(cls, str) else None,
        "faction": snap.get("faction") or gear.get("faction"),
        # Battle.net's (Views.bnet_profile sets them): unknown until then.
        "guild": None,
        "guild_known": False,
        "spec": gear.get("spec"),
        # Full view only (redact.HEADER leaves it out); for the 3D model.
        "sex": None,
        # Which addons know this character: the page says what fills the
        # sections that stay empty (a character Polestar doesn't track).
        "sources": [x for x in str(snap.get("source") or "").split(",") if x],
        "polestar": bool(levels) or (bool(snap) and not snap.get("source")),
        "rested_xp": rested,
        "resting": truthy(snap.get("resting")) if top is snap and snap.get("resting") is not None else None,
        "level": level,
        "xp": cur_xp,
        "xp_max": xp_max,
        # Gold comes from the account snapshot (PolestarDB.alts.chars, saved at
        # logout): the Recorder's money fields are quest rewards, not the purse.
        "money_copper": opt_num(snap.get("money_copper")),
        # The money's own time: from an older sheet when the newest has none
        # (stores.newest_with), never the newer sheet's (review 6).
        "money_at": (snap.get("part_at") or {}).get("money_copper"),
        "zone": top.get("zone") or snap.get("zone"),
        "seen": max(snap.get("_time") or "", xp.get("_time") or "", key=lambda s: parse_t(s)) or None,
        "time_in_level_s": current["seconds"] if current else current_s,
        # Played since tracking began: the splits, plus the level in play.
        "played_s": ((splits_s or 0) + (0 if current else current_s or 0)) if levels else None,
        # The first tracked level started before tracking did: its split (and
        # so played_s) misses the part before.
        "first_partial": bool(levels and levels[0]["partial"]),
        "first_level": levels[0]["level"] if levels else None,
        "avg_level_s": avg_s,
        "avg_levels": avg_n,
        "xp_per_hour": xph,
        "eta_s": eta,
        # The kills and quests, each with its population (pages.climb): the
        # completed levels (Polestar's splits) and the current level so far,
        # never one bare total a reader could take for the other (external
        # review, 2026-11-22).
        "counts": layer.get("counts"),
    }


# A first /played snapshot has no earlier one to bound its session: assume
# the session started at most this long before the save.
PLAYED_SESSION_S = 12 * 3600


def played_total(snaps: list[dict], levels: list[dict], header: dict) -> dict:
    """Total played since level 1, from Altoholic's /played (DataStore saves
    `played` and `playedThisLevel` as the game answered at the last login or
    /reload). played - playedThisLevel is /played when that level began, a
    fixed point: add the splits of that level and the ones after, plus the
    level in play, and the total is current. The level is the one saved
    with it, which is the level of the answer only if no ding came between
    the login and the save: a snapshot with a ding since the one before it
    is skipped. No usable snapshot: {} (the page keeps "played since
    tracking began")."""
    rows = sorted((r for r in snaps if opt_num(r.get("played_total_s"))), key=lambda r: parse_t(r.get("_time")),
                  reverse=True)
    if not rows:
        return {}
    level = header.get("level")
    # A total below what the Recorder and Polestar already prove (the level
    # splits plus the level in play) is a fresh or broken save: never shown,
    # so it can't pull the climb back (owner, 2026-11-18).
    proven = sum(lv["seconds"] or 0 for lv in levels) + (header.get("time_in_level_s") or 0)
    if not levels:  # no Polestar splits to carry it forward: the answer as saved, if it is this level's
        for r in rows:
            lvl, total = opt_num(r.get("level")), opt_num(r.get("played_total_s"))
            if level is not None and lvl is not None and lvl != level:
                continue
            if total < proven:
                continue
            return {"played_total_s": int(total), "played_total_from": "altoholic",
                    "played_total_at": r.get("_time")}
        return {}
    dings = [parse_t(lv["at"]).timestamp() for lv in levels if lv.get("at")]
    by_level = {lv["level"]: lv for lv in levels}
    first = levels[0]["level"] if levels else None
    for i, r in enumerate(rows):
        lvl, total, in_level = opt_num(r.get("level")), opt_num(r.get("played_total_s")), opt_num(
            r.get("played_level_s"))
        if lvl is None or in_level is None or level is None or lvl > level:
            continue
        t = parse_t(r.get("_time")).timestamp()
        start = parse_t(rows[i + 1].get("_time")).timestamp() if i + 1 < len(rows) else t - PLAYED_SESSION_S
        if any(start < d <= t for d in dings):
            continue  # a ding in that session: the answer may be from the level before
        # Every level from lvl on must be tracked in full, or the sum misses time.
        if lvl != level and (first is None or lvl < first or (lvl in by_level and by_level[lvl]["partial"])):
            continue
        after = sum(lv["seconds"] for lv in levels if lv["level"] >= lvl)
        if level not in by_level:
            after += header.get("time_in_level_s") or 0
        if total - in_level + after < proven:
            continue
        return {"played_total_s": int(total - in_level + after), "played_total_from": "altoholic",
                "played_total_at": r.get("_time")}
    return {}


def build_gear(row: dict) -> dict | None:
    """The newest wow-gear event (the shipper's character sheet). Its lists
    arrive as JSON strings (VictoriaLogs stores arrays that way)."""
    if not row:
        return None

    def clean(r: dict, nums: tuple, texts: tuple) -> dict:
        return {**{k: opt_num(r.get(k)) for k in nums}, **{k: r.get(k) for k in texts}}

    items = [clean(i, ("slot", "item_id", "quality", "item_level", "req_level", "enchant_id"), ("slot_name", "name"))
             for i in jlist(row.get("items")) if isinstance(i, dict)]
    # The enchant from the item's link (shipper 0.2.2 on): 0 none, a number
    # the enchant, absent (an older shipper) unknown. Named from Forever's
    # SpellItemEnchantment (wowstats.itemcard).
    from wowstats import itemcard

    for i in items:
        i["enchant"] = itemcard.shared().enchant(i["enchant_id"]) if i.get("enchant_id") else None
    # The rank, and the cap DataStore_Crafts read in game (`cap`, `cap_at`);
    # an old sheet's `max_rank` came from CharacterExport-Forever (dropped
    # 2026-11-20) and isn't read. Views.char sets the cap (wowstats.profcaps).
    profs = [{**clean(p, ("skill_id", "rank"), ("name",)),
              **{k: opt_num(p[k]) for k in ("cap", "cap_at") if p.get(k) is not None}}
             for p in jlist(row.get("professions")) if isinstance(p, dict)]
    wanted = [clean(w, ("item_id", "item_level", "req_level", "level"), ("slot_name", "name"))
              for w in jlist(row.get("wanted")) if isinstance(w, dict)]
    return {"at": row.get("_time"), "items": sorted(items, key=lambda i: i["slot"] or 0),
            "item_level": opt_num(row.get("item_level")), "professions": profs, "wanted": wanted,
            "spec": row.get("spec") or None,
            "class": row.get("class") or None, "race": row.get("race") or None,
            "faction": row.get("faction") or None}


# What the page takes from Battle.net only when the addons sent nothing for
# it; the addon's value wins (owner, 2026-11-20). Gear is with_battlenet_items.
BNET_FILLS = ("class", "race", "faction", "level")


def fill_from_battlenet(header: dict, prof: dict) -> None:
    """Battle.net's profile into the hero line: sex and guild always (no
    addon has them), class, race, faction and level only where the addons
    sent none (`from_battlenet` lists which). `prof` is {} without one read
    in the last 30 days: those stay unknown."""
    header.update(sex=prof.get("sex"), guild=prof.get("guild"), guild_known=bool(prof.get("guild_known")))
    used = []
    for k in BNET_FILLS:
        if header.get(k) in (None, "") and prof.get(k):
            header[k] = prof[k].title() if k == "class" else prof[k]
            used.append(k)
    if used:  # absent when the addons sent everything
        header["from_battlenet"] = used
        header["battlenet_read"] = prof.get("refreshed")


def with_battlenet_items(gear: dict | None, worn: dict, exporter_on: bool) -> dict | None:
    """The page's gear when the shipper sent no equipped item: Battle.net's
    (items_from "battle.net"), else what fills it and when (items_note),
    never an empty paperdoll that says nothing."""
    items = gearmod.battlenet_items(worn)
    if not items and gear is None and not exporter_on:
        return None  # the page's own empty panel says which addon fills it
    gear = gear or {"at": None, "items": [], "item_level": None, "professions": [], "wanted": [], "spec": None,
                    "class": None, "race": None, "faction": None}
    if items:
        return {**gear, "items": items, "items_from": "battle.net",
                "items_note": "Gear from Battle.net, read every 2 hours. Syndicator shows it right away: turn it "
                              "on in AddOns, log in, then /reload."}
    when = ("Battle.net's copy comes within about 10 minutes of a character's first upload, and within 2 "
            "hours after that. " if exporter_on else "")
    return {**gear,
            "items_note": f"No equipped items saved yet. {when}Syndicator shows them right away: turn it on in "
                          "AddOns, log in, then /reload."}


# The armory's item level: every slot but the shirt and tabard, an empty
# one counting 0, a two-hander counted again for the off hand (Blizzard's
# own rule; Battle.net's figure wins when the exporter has it). The ranged
# slot counts: Classic's sheet has it.
ARMORY_SLOTS = (1, 2, 3, 15, 5, 9, 10, 6, 7, 8, 11, 12, 13, 14, 16, 17, 18)


def armory_item_level(items: list[dict]) -> float | None:
    by = {i.get("slot"): i for i in items or [] if i.get("item_id")}
    if not by:
        return None
    from wowstats import itemcard

    main = by.get(16)
    if main and 17 not in by and (itemcard.shared().card(main["item_id"]) or {}).get("s") == "Two-Hand":
        by[17] = main
    return sum(int(by[s].get("item_level") or 0) for s in ARMORY_SLOTS if s in by) / len(ARMORY_SLOTS)


def add_armory(gear: dict, armory: dict) -> None:
    """The armory's item level. gear.render (the picture: our own still of
    the 3D model, wowstats.armory) is filled where it exists: by the
    publisher in a bundle, by the live view when it reads the static site."""
    bnet = armory.get("item_level")
    mine = armory_item_level(gear.get("items") or [])
    gear["armory_item_level"] = int(bnet) if bnet is not None else (int(mine) if mine is not None else None)
    gear["armory_item_level_source"] = "battle.net" if bnet is not None else "computed" if mine is not None else None
    gear["render"] = None


def build_climb(levels: list[dict], dings: list[dict]) -> list[dict]:
    """Level over calendar time: one point per ding (the split's own time,
    or the recorder's level record when the split is missing)."""
    pts: dict[int, str] = {}
    for lv in levels:
        # A partial split still ends on a real ding: only its played time is short.
        if lv["at"]:
            pts[lv["level"] + 1] = lv["at"]
    for d in dedupe(dings):
        lvl = opt_num(d.get("level"))
        if lvl is not None and lvl not in pts:
            pts[lvl] = d.get("_time")
    return [{"level": k, "t": pts[k]} for k in sorted(pts)]


def build_steps(rows: list[dict], ref=None) -> dict:
    """Guide steps from Polestar_Recorder. A `step` record is written when
    the guide moves to a step; its duration_s is the time since the last
    one. `reason` says why: auto (a step completed), next/back (clicked),
    done, or pick: the player picked or switched guide, which isn't a step
    done (its step index is often empty) and is listed apart."""
    rows.sort(key=lambda r: parse_t(r.get("_time")))
    agg: dict[tuple, dict] = {}
    skipped, picks = [], []
    last: dict[str, tuple] = {}
    total = 0
    for r in rows:
        guide = r.get("guide") or "guide"
        step = r.get("step")
        s = opt_num(step)
        if r.get("kind") == "skip":
            if isinstance(s, int):
                skipped.append({"guide": guide, "steps": [s], "t": r.get("_time"), "zone": r.get("zone"),
                                "how": "skipped"})
            continue
        if r.get("reason") == "pick":
            picks.append({"guide": guide, "title": insights.guide_title(guide, ref), "step": s, "t": r.get("_time")})
            if isinstance(s, int):
                last[guide] = (s, r.get("_time"))  # jumps count from where the pick landed
            continue
        if not isinstance(s, int):
            continue  # a guide finished or emptied: no step to time
        total += 1
        k = (guide, s)
        a = agg.setdefault(k, {"guide": guide, "step": s, "secs": 0, "n": 0, "zone": r.get("zone"),
                               "title": ref.step_title(guide, s) if ref is not None and getattr(
                                   ref, "available", False) else None,
                               "guide_title": insights.guide_title(guide, ref)})
        a["secs"] += int(num(r.get("duration_s")))
        a["n"] += 1
        prev = last.get(guide)
        # A jump forward by more than one step: the ones in between were
        # never reached (clicked past, or done out of order).
        if prev and prev[0] + 1 < s <= prev[0] + 25:
            skipped.append({"guide": guide, "steps": list(range(prev[0] + 1, s)), "t": r.get("_time"),
                            "zone": r.get("zone"), "how": "jumped"})
        last[guide] = (s, r.get("_time"))
    for sk in skipped:
        sk["guide_title"] = insights.guide_title(sk["guide"], ref)
    slow = sorted(agg.values(), key=lambda a: -a["secs"])[:12]
    secs = sorted(a["secs"] for a in agg.values() if a["secs"] > 0)
    median = secs[len(secs) // 2] if secs else None
    return {"slowest": slow, "skipped": skipped[-30:][::-1], "skipped_total": sum(len(s["steps"]) for s in skipped),
            "total": total, "median_s": median, "picks": picks[-12:][::-1], "picks_total": len(picks),
            "guides": sorted({a["guide"] for a in agg.values()})}


def build_dungeons(rows: list[dict]) -> list[dict]:
    """The dungeon runs' facts (name, level, when), newest first; each run's
    time, XP and XP an hour are the semantic layer's (pages.apply_rows)."""
    out = [{"name": r.get("name") or f"Dungeon {r.get('dungeon_id', '?')}", "level": opt_num(r.get("level")),
            "seconds": None, "xp": None, "t": r.get("_time"), "xp_per_hour": None} for r in rows]
    out.sort(key=lambda d: parse_t(d["t"]), reverse=True)
    return out


def build_deaths(rows: list[dict]) -> dict:
    """The newest deaths (facts: when, where, at what level); the counts are
    the semantic layer's (pages.apply_deaths). Sorts `rows` newest first."""
    rows.sort(key=lambda r: parse_t(r.get("_time")), reverse=True)
    return {"total": None,
            "recent": [{"t": r.get("_time"), "zone": r.get("zone"), "level": opt_num(r.get("level"))}
                       for r in rows[:15]],
            "by_zone": [], "by_level": {}}


def deaths_per_level(by_level: dict, climb: list[dict], spans: list[tuple], gaps: list[dict]) -> dict | None:
    """The Deaths section's "Per level": deaths over levels, both read over
    the same levels, the ones the Recorder saw whole. A level counts when
    the ding into it and the ding out of it are both known (`climb`: the
    splits' and the Recorder's dings), the Recorder's first record (`spans`,
    unix seconds) is at or before the ding into it, and the Recorder missed
    none of it (`gaps`, coverage.recorder_gaps: Polestar's split longer than
    what the Recorder saw). The level in play, the levels before the Recorder
    and a level with a gap leave both sides. {value, deaths, n, levels} or
    None when no level qualifies (never 0).

    Review 6: the page divided every Recorder death by Polestar's level
    count, so a Recorder switched on late read 3 deaths / 12 levels = 0.25
    for a character that died once in the one level it saw whole."""
    if not spans:
        return None
    first = spans[0][0]
    ding = {int(c["level"]): parse_t(c["t"]).timestamp() for c in climb if c.get("t") and c.get("level") is not None}
    missed = {int(g["level"]) for g in gaps or [] if g.get("level") is not None}
    levels = [lv for lv in sorted(ding) if lv + 1 in ding and ding[lv] >= first and lv not in missed]
    if not levels:
        return None
    n = sum(int((by_level or {}).get(str(lv), 0)) for lv in levels)
    return {"value": round(n / len(levels), 2), "deaths": n, "n": len(levels), "levels": levels}


def build_players(char: str, summary: list[dict], days: list[dict]) -> dict:
    """The People tab's players: the `players` store model's metrics per
    player (wowstats.storemodels.players), the regulars first."""
    from wowstats import storemodels

    per = storemodels.players(days, summary, lambda p: norm_char(p) != char)
    out = []
    for p, d in per.items():
        out.append({"player": p, "days": d["days"], "day_list": d["day_list"], "group_days": d["group_days"],
                    "grouped": d["grouped"], "sightings": d["sightings"], "contexts": d["contexts"],
                    "last_seen": d["last_seen"], "level": opt_num(d["level"]),
                    "class": (jlist(d["classes"]) or [None])[0], "race": (jlist(d["races"]) or [None])[0],
                    "zones": jlist(d["zones"])})
    out.sort(key=lambda r: (-r["days"], -r["grouped"], -r["sightings"]))
    recurring = [r for r in out if r["days"] >= 2 or r["grouped"] >= 2]
    last_day = max((day for d in per.values() for day in d["day_list"] if day), default=None)
    return {"recurring": recurring[:30], "total": len(out), "once": len(out) - len(recurring), "last_day": last_day}


def build_flights(rows: list[dict]) -> dict:
    """The flights from the stored taxi and land records (stores.taxi_time):
    a flight still in the air, or whose land record isn't stored, has no time."""
    flights = [{"t": f.get("_time"), "from_zone": f.get("zone"), "to_zone": (f["land"] or {}).get("zone"),
                "secs": None if f["secs"] is None else int(f["secs"])} for f in taxi_time(rows)]
    total = sum(f["secs"] or 0 for f in flights)
    return {"count": len(flights), "total_s": total, "recent": flights[::-1][:15],
            "longest": max(flights, key=lambda f: f["secs"] or 0) if flights else None}


def build_prices(rows: list[dict]) -> dict:
    items: dict[tuple, dict] = {}
    days: set[str] = set()
    for r in rows:
        key, day = r.get("item_key"), r.get("scan_day")
        lo = opt_num(r.get("lo"))
        if not key or not day or lo is None:
            continue
        days.add(day)
        it = items.setdefault((r.get("ruleset") or "", key), {
            "item_key": key, "item_id": opt_num(r.get("item_id")), "name": r.get("name") or None,
            "ruleset": r.get("ruleset") or None, "series": []})
        if r.get("name") and not it["name"]:
            it["name"] = r["name"]
        # One point a day: a ruleset's houses (its faction's and the
        # neutral one) are one ruleset here, the lowest of them.
        pt = next((p for p in it["series"] if p["day"] == day), None)
        if pt is None:
            it["series"].append({"day": day, "lo": lo, "hi": opt_num(r.get("hi")), "available": opt_num(r.get("av"))})
        else:
            pt["lo"] = min(pt["lo"], lo)
            pt["hi"] = max((v for v in (pt["hi"], opt_num(r.get("hi"))) if v is not None), default=None)
            pt["available"] = max((v for v in (pt["available"], opt_num(r.get("av"))) if v is not None), default=None)
    out = []
    for it in items.values():
        it["series"].sort(key=lambda p: p["day"])
        last = it["series"][-1]
        prev = it["series"][-2] if len(it["series"]) > 1 else None
        lows = [p["lo"] for p in it["series"]]
        out.append({**it, "price": last["lo"], "day": last["day"], "available": last["available"],
                    "change": (last["lo"] - prev["lo"]) if prev else None, "min": min(lows), "max": max(lows),
                    "scans": len(it["series"])})
    out.sort(key=lambda i: ((i["name"] or "~").lower(), i["item_key"]))
    return {"items": out[:3000], "total": len(out), "scan_days": sorted(days),
            "last_scan": max(days) if days else None}


def build_market(res: dict) -> dict:
    def key(metric: dict) -> str:
        return json.dumps({k: v for k, v in metric.items() if k != "__name__"}, sort_keys=True)

    token = None
    if res["token"]:
        s = res["token"][0]
        series = [[int(t), float(v)] for t, v in s.get("values", [])]
        if series:
            token = {"label": "Retail token", "gold": series[-1][1], "series": series,
                     "change_7d": series[-1][1] - series[0][1], "at": series[-1][0]}
    day_min = {key(r["metric"]): float(r["value"][1]) for r in res["realm_day"]}
    realms = [{"name": r["metric"].get("realm") or f"ruleset {r['metric'].get('ruleset_id', '?')}",
               "up": float(r["value"][1]) >= 1, "dipped_24h": day_min.get(key(r["metric"]), 1) < 1}
              for r in res["realm"]]
    configured = bool(token or realms)
    return {"token": token, "realms": sorted(realms, key=lambda r: r["name"]), "configured": configured,
            "reason": None if configured else "no-data"}


__all__ = ["SourceError", "Settings", "Views", "norm_char"]
