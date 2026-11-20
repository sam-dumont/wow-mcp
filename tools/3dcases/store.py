"""The 3D cases' data: Battle.net's answers and renders on the local disk
(never in git), the anonymous case specs in git, and the coverage cells.

Blizzard's Developer API Terms keep API data 30 days at most, so every
fetch lives in <store>/fetch/<case>/<YYYY-MM-DD>/ and nothing older than
MAX_AGE_DAYS is used: the run refuses it and says to refresh. The store also
maps each case id to its character (sources.json): names stay on this disk.

The repo's tools/3dcases/cases.json holds what the assembly reads and
nothing that names anyone: race, body type, class, the customisation
option/choice ids, the worn items and their enchants, and the coverage
cells the case fills.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPECS = HERE / "cases.json"
DEFAULT_STORE = Path(os.environ.get("WOW_3D_CASES") or Path.home() / "wow-3d-cases")
MAX_AGE_DAYS = 30
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Battle.net's slot types -> inventory slots (wowstats.gear.BNET_SLOTS plus
# the two it doesn't score: the shirt and the tabard show on the model).
SLOTS = {"head": 1, "neck": 2, "shoulder": 3, "shirt": 4, "chest": 5, "waist": 6, "legs": 7, "feet": 8,
         "wrist": 9, "hands": 10, "finger_1": 11, "finger_2": 12, "trinket_1": 13, "trinket_2": 14,
         "back": 15, "main_hand": 16, "off_hand": 17, "ranged": 18, "tabard": 19}
# Forever's playable races: the eight of 2004 and the Skyborne (95 Alliance, 96 Horde: one model).
RACES = {1: "human", 2: "orc", 3: "dwarf", 4: "nightelf", 5: "undead", 6: "tauren", 7: "gnome", 8: "troll",
         95: "skyborne", 96: "skyborne"}
SEXES = ("male", "female")
ARMOUR = {1: "cloth", 2: "leather", 3: "mail", 4: "plate"}
# Customisation options that make a look unusual, by the words of their
# name (ChrCustomizationOption.Name_lang, enUS).
UNUSUAL = {"facial-hair": ("facial hair", "beard", "mustache", "moustache", "sideburn", "goatee"),
           "piercings": ("piercing", "earring"), "tattoos": ("tattoo", "marking", "paint")}


# ------------------------------------------------------------------ the store

def today() -> str:
    return datetime.now(UTC).date().isoformat()


def age_days(day: str, now: str | None = None) -> int:
    return (date.fromisoformat(now or today()) - date.fromisoformat(day)).days


class Store:
    """<root>/sources.json (case id -> {slug, region}), <root>/fetch/<case>/<day>/."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def sources(self) -> dict:
        try:
            return json.loads((self.root / "sources.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save_sources(self, src: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        (self.root / "sources.json").write_text(json.dumps(src, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    def days(self, case: str) -> list[str]:
        d = self.root / "fetch" / case
        return sorted(p.name for p in d.iterdir() if p.is_dir() and DAY.match(p.name)) if d.is_dir() else []

    def latest(self, case: str, now: str | None = None) -> tuple[Path | None, str]:
        """The newest fetch of a case still usable, else (None, why)."""
        days = self.days(case)
        if not days:
            return None, "never fetched: run `refresh`"
        day = days[-1]
        old = age_days(day, now)
        if old > MAX_AGE_DAYS:
            return None, (f"fetched {day}, {old} days ago: Blizzard's data is kept {MAX_AGE_DAYS} days at most. "
                          f"Run `refresh` (and `prune` drops the old one)")
        return self.root / "fetch" / case / day, ""

    def prune(self, now: str | None = None) -> list[Path]:
        """Drop every fetch and run older than MAX_AGE_DAYS."""
        gone = []
        for case_dir in (self.root / "fetch").glob("*") if (self.root / "fetch").is_dir() else []:
            for p in case_dir.iterdir():
                if p.is_dir() and DAY.match(p.name) and age_days(p.name, now) > MAX_AGE_DAYS:
                    shutil.rmtree(p)
                    gone.append(p)
        for p in (self.root / "runs").glob("*") if (self.root / "runs").is_dir() else []:
            if p.is_dir() and DAY.match(p.name[:10]) and age_days(p.name[:10], now) > MAX_AGE_DAYS:
                shutil.rmtree(p)
                gone.append(p)
        return gone


# ------------------------------------------------------------------ Battle.net

class Api:
    """A polite Battle.net client: one request at a time, at most `rate`
    a second, If-Modified-Since never (each fetch is a dated snapshot), a 429
    waits Retry-After. Credentials: BNET_CLIENT_ID_FILE and
    BNET_CLIENT_SECRET_FILE (the exporter's), or BNET_CREDS_CMD, a command
    that prints {"client_id", "client_secret"} as JSON (e.g. `sops -d ...`);
    read into memory, never printed or written."""

    def __init__(self, region: str = "eu", rate: float = 4.0, budget: int = 600):
        self.region = region
        self.base = f"https://{region}.api.blizzard.com"
        self.gap = 1.0 / rate
        self.last = 0.0
        self.budget = budget
        self.calls = 0
        self._token = None

    @staticmethod
    def creds() -> tuple[str, str]:
        idf, sef = os.environ.get("BNET_CLIENT_ID_FILE"), os.environ.get("BNET_CLIENT_SECRET_FILE")
        if idf and sef:
            return Path(idf).read_text().strip(), Path(sef).read_text().strip()
        cmd = os.environ.get("BNET_CREDS_CMD")
        if cmd:
            out = subprocess.run(shlex.split(cmd), capture_output=True, check=True).stdout
            d = json.loads(out)
            return str(d["client_id"]).strip(), str(d["client_secret"]).strip()
        raise SystemExit("set BNET_CLIENT_ID_FILE and BNET_CLIENT_SECRET_FILE, or BNET_CREDS_CMD "
                         "(tools/3dcases/README.md)")

    def token(self) -> str:
        if self._token is None:
            cid, sec = self.creds()
            req = urllib.request.Request(
                "https://oauth.battle.net/token", data=b"grant_type=client_credentials",
                headers={"Authorization": "Basic " + base64.b64encode(f"{cid}:{sec}".encode()).decode(),
                         "Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req, timeout=30) as r:
                self._token = json.loads(r.read())["access_token"]
        return self._token

    def _wait(self) -> None:
        if self.calls >= self.budget:
            raise SystemExit(f"stopped at {self.calls} requests (--budget)")
        delay = self.last + self.gap - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self.last = time.monotonic()
        self.calls += 1

    def get(self, path: str, namespace: str, locale: str = "en_GB") -> dict | None:
        url = f"{self.base}{path}?" + urllib.parse.urlencode({"namespace": namespace, "locale": locale})
        for attempt in range(4):
            self._wait()
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token()}",
                                                       "User-Agent": "wow-stats-3dcases"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                if e.code == 429 or e.code >= 500:
                    time.sleep(float(e.headers.get("Retry-After") or 2 ** (attempt + 1)))
                    continue
                raise
        return None

    def image(self, url: str) -> tuple[bytes, str] | None:
        """A render and its Last-Modified, only from Blizzard's render host."""
        u = urllib.parse.urlsplit(url)
        if u.scheme != "https" or u.hostname != "render.worldofwarcraft.com":
            return None
        self._wait()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "wow-stats-3dcases"}),
                                        timeout=60) as r:
                return r.read(), r.headers.get("Last-Modified") or ""
        except urllib.error.HTTPError:
            return None

    def profile_ns(self) -> str:
        return f"profile-forever-{self.region}"


RENDERS = {"main-raw": "hd", "main-sd-raw": "sd"}


def fetch_character(api: Api, slug: str, into: Path) -> dict:
    """One character's profile, appearance, equipment and renders, into a
    dated folder. Returns what was saved ({"missing": [...]} for the rest)."""
    ns = api.profile_ns()
    path = f"/profile/wow/character/{urllib.parse.quote(slug.lower(), safe='-')}"
    into.mkdir(parents=True, exist_ok=True)
    saved, missing = [], []
    for name, sub in (("profile", ""), ("appearance", "/appearance"), ("equipment", "/equipment"),
                      ("media", "/character-media")):
        body = api.get(path + sub, ns)
        if body is None:
            missing.append(name)
            continue
        (into / f"{name}.json").write_text(json.dumps(body, indent=1, sort_keys=True), encoding="utf-8")
        saved.append(name)
    media = _json(into / "media.json") or {}
    stamps = {}
    for a in media.get("assets") or []:
        if a.get("key") in RENDERS and isinstance(a.get("value"), str):
            got = api.image(a["value"])
            if got and got[0][:8] == b"\x89PNG\r\n\x1a\n":
                (into / f"{RENDERS[a['key']]}.png").write_bytes(got[0])
                stamps[RENDERS[a["key"]]] = got[1]
                saved.append(RENDERS[a["key"]])
            else:
                missing.append(RENDERS[a["key"]])
    (into / "renders.json").write_text(json.dumps(stamps, indent=1, sort_keys=True), encoding="utf-8")
    return {"saved": saved, "missing": missing}


# Blizzard renders a character when it logs in (Last-Modified within a
# second or two of last_login_timestamp, 2026-11-20). A render written long
# after the last login is another character's: Forever and the other
# Classic games share the render paths by character id, and three of the
# first 21 cases showed someone else entirely.
FOREIGN_AFTER_S = 15 * 60


def foreign_render(fetched: Path, model: str) -> str:
    """Why this reference isn't the character's own render, or ''."""
    from email.utils import parsedate_to_datetime

    prof = _json(fetched / "profile.json") or {}
    stamp = (_json(fetched / "renders.json") or {}).get(model) or ""
    login = prof.get("last_login_timestamp")
    if not stamp or not isinstance(login, (int, float)):
        return ""
    try:
        written = parsedate_to_datetime(stamp).timestamp()
    except (TypeError, ValueError):
        return ""
    late = written - login / 1000
    if late > FOREIGN_AFTER_S:
        return (f"Blizzard's render was written {late / 3600:.1f} h after this character's last login: "
                f"another character's render on the same path, not this look")
    return ""


def _json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ specs and cells

def spec_from(fetched: Path) -> dict | None:
    """The anonymous spec of one fetch: what the assembly reads, no name."""
    prof, look, gear = (_json(fetched / f"{n}.json") for n in ("profile", "appearance", "equipment"))
    if not prof or not look:
        return None
    race = (prof.get("race") or look.get("playable_race") or {}).get("id")
    cls = (prof.get("character_class") or look.get("playable_class") or {}).get("id")
    sex = 1 if ((prof.get("gender") or look.get("gender") or {}).get("type") or "").upper() == "FEMALE" else 0
    choices = sorted(({"optionId": int(c["option"]["id"]), "choiceId": int(c["choice"]["id"])}
                      for c in look.get("customizations") or []
                      if (c.get("option") or {}).get("id") and (c.get("choice") or {}).get("id")),
                     key=lambda c: c["optionId"])
    items = items_of(look, gear)
    return {"race": race, "sex": sex, "class": cls, "level": prof.get("level"), "choices": choices,
            "items": items, "gear": gear_facts(gear)}


def items_of(look: dict | None, gear: dict | None) -> list[dict]:
    """What the model wears: the appearance's items (what shows, with the
    enchant whose glow shows), else the equipment's."""
    out = {}
    for it in (look or {}).get("items") or []:
        slot = SLOTS.get(((it.get("slot") or {}).get("type") or "").lower())
        if slot and it.get("id"):
            out[slot] = {"slot": slot, "item_id": int(it["id"]), "enchant": int(it.get("enchant") or 0)}
    if not out:
        for it in (gear or {}).get("equipped_items") or []:
            slot = SLOTS.get(((it.get("slot") or {}).get("type") or "").lower())
            iid = (it.get("item") or {}).get("id")
            if slot and iid:
                ench = next((e.get("enchantment_id") for e in it.get("enchantments") or []
                             if (e.get("enchantment_slot") or {}).get("type") == "PERMANENT"), 0)
                out[slot] = {"slot": slot, "item_id": int(iid), "enchant": int(ench or 0)}
    return [out[s] for s in sorted(out)]


def gear_facts(gear: dict | None) -> dict:
    """slot -> (item class, subclass, inventory type) from the equipment
    answer: what the cells read (armour type, shield, two-hander...)."""
    out = {}
    for it in (gear or {}).get("equipped_items") or []:
        slot = SLOTS.get(((it.get("slot") or {}).get("type") or "").lower())
        if slot:
            out[str(slot)] = [(it.get("item_class") or {}).get("id"), (it.get("item_subclass") or {}).get("id"),
                              ((it.get("inventory_type") or {}).get("type") or "")]
    return out


def cells(spec: dict, option_names: dict[int, str] | None = None,
          default_choice: dict[int, int] | None = None) -> list[str]:
    """The coverage cells a spec fills. option_names (option id -> name) and
    default_choice (option id -> its first choice) come from the client
    tables; without them the customisation cells are left out."""
    out = [f"race:{RACES.get(spec['race'], spec['race'])}/{SEXES[spec['sex']]}"]
    g = {int(k): v for k, v in (spec.get("gear") or {}).items()}
    chest = g.get(5) or g.get(7)
    if chest and chest[0] == 4 and chest[1] in ARMOUR:
        out.append(f"armour:{ARMOUR[chest[1]]}")
    slots = {it["slot"] for it in spec["items"]}
    if 15 in slots:
        out.append("gear:cloak")
    if 19 in slots:
        out.append("gear:tabard")
    if 1 in slots:
        out.append("gear:helm")
    off, main = g.get(17), g.get(16)
    if off and off[0] == 4 and off[1] == 6:
        out.append("gear:shield")
    if off and off[0] == 2:
        out.append("gear:dual-wield")
    if main and main[2] in ("TWOHWEAPON",):
        out.append("gear:two-hand")
    ranged = g.get(18)
    if ranged and ranged[0] == 2 and ranged[1] in (2, 3, 18, 16):  # bow, gun, crossbow, thrown
        out.append("gear:ranged")
    if any(it.get("enchant") for it in spec["items"] if it["slot"] in (16, 17)):
        out.append("gear:weapon-glow")
    if option_names:
        for c in spec["choices"]:
            name = (option_names.get(c["optionId"]) or "").lower()
            if default_choice and default_choice.get(c["optionId"]) == c["choiceId"]:
                continue
            for cell, words in UNUSUAL.items():
                if any(w in name for w in words):
                    out.append(f"look:{cell}")
    return sorted(set(out))


ALL_CELLS = ([f"race:{r}/{s}" for r in dict.fromkeys(RACES.values()) for s in SEXES]
             + [f"armour:{a}" for a in ARMOUR.values()]
             + ["gear:cloak", "gear:tabard", "gear:helm", "gear:shield", "gear:dual-wield", "gear:two-hand",
                "gear:ranged", "gear:weapon-glow", "look:facial-hair", "look:piercings", "look:tattoos",
                "model:updated", "model:classic"])


def load_specs(path: Path = SPECS) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("cases") or []
    except (OSError, ValueError):
        return []


def save_specs(cases: list[dict], path: Path = SPECS) -> None:
    doc = {"_": "Anonymous 3D test cases (tools/3dcases/README.md): what the assembly reads, never a name. "
                "Picked from public Battle.net profiles; the references are fetched at test time.",
           "cases": cases}
    path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")


ANON_KEYS = {"id", "cells", "race", "sex", "class", "level", "choices", "items", "gear", "picked"}


def anonymous(spec: dict, case_id: str, cell_list: list[str], picked: str) -> dict:
    """Only ANON_KEYS: the spec as git keeps it."""
    out = {k: v for k, v in spec.items() if k in ANON_KEYS}
    return {**out, "id": case_id, "cells": cell_list, "picked": picked}
