#!/usr/bin/env python3
"""Send the ForeverDB addon's data to foreverdb.net, as the site's own page does.

https://foreverdb.net/send reads each account's
WTF/Account/<account>/SavedVariables/ForeverDB.lua in the browser and posts the
shareable part of it, anonymously, to /api/anon/addon-file. There is no Mac
build of the site's uploader, so this does the same from the shipper's run.
What the page does is ported step for step (the Lua reader, the section
filter, the name scrub, the change hash, the request); the findings are in
docs/shipper.md ("Sending to ForeverDB"). Nothing else is sent: no character
snapshot (the anonymous page sends "armory": []), no name, no
ForeverDB_Helper.lua (the page needs a login for it).

Off unless `foreverdb_send = true` is in config.toml (or WOW_STATS_FOREVERDB_SEND=1).
Run by wow_stats.py after its own work; by hand:

    python3 foreverdb_send.py --dry-run   # size and shape of each request, nothing sent
    python3 foreverdb_send.py --force     # send now, whatever the last send

Python standard library only (3.11+).
"""

from __future__ import annotations

import argparse
import base64
import copy
import gzip
import hashlib
import json
import math
import os
import re
import secrets
import stat
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

SITE = "https://foreverdb.net"
SEND_URL = SITE + "/api/anon/addon-file"
ME_URL = SITE + "/api/me/addon-file"
WORDS_URL = SITE + "/api/gamewords"
MAX_FILE = 48 * 1024 * 1024  # the page refuses a bigger saved file
MIN_INTERVAL_S = 15 * 60  # between two attempts
STATE_NAME = "foreverdb-send.json"
INSTALL_RE = re.compile(r"[A-Za-z0-9_-]{16,64}")

# --------------------------------------------------------------------------
# JavaScript semantics the page relies on


def is_index(k: str) -> bool:
    """A JS array index: such keys come first, ascending, in an object."""
    return k.isascii() and k.isdigit() and (k == "0" or k[0] != "0") and int(k) < 2**32 - 1


def js_order(d: dict) -> dict:
    ints = sorted((k for k in d if is_index(k)), key=int)
    return {k: d[k] for k in [*ints, *(k for k in d if not is_index(k))]}


def js_num(v: float | int) -> str:
    """Number.prototype.toString."""
    if isinstance(v, int):
        v = float(v)
    if math.isnan(v) or math.isinf(v):
        return "null"
    if v == 0:
        return "0"
    sign = "-" if v < 0 else ""
    m, _, e = repr(abs(v)).partition("e")
    ip, _, fp = m.partition(".")
    digits = (ip + fp).lstrip("0")
    # decimal point position relative to the first significant digit
    n = len(ip.lstrip("0")) if ip.strip("0") else -(len(fp) - len(fp.lstrip("0")))
    n += int(e or 0)
    digits = digits.rstrip("0") or "0"
    k = len(digits)
    if k <= n <= 21:
        return sign + digits + "0" * (n - k)
    if 0 < n <= 21:
        return sign + digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * (-n) + digits
    ex = n - 1
    es = ("+" if ex >= 0 else "-") + str(abs(ex))
    return sign + (digits[0] if k == 1 else digits[0] + "." + digits[1:]) + "e" + es


def stringify(v) -> str:
    """JSON.stringify, no spacing."""
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        return js_num(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ",".join(stringify(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + stringify(x) for k, x in v.items()) + "}"
    raise TypeError(type(v))


def is_obj(v) -> bool:
    return isinstance(v, (dict, list))


def values(v) -> list:
    return list(v.values()) if isinstance(v, dict) else list(v) if isinstance(v, list) else []


def nkeys(v) -> int:
    return len(v) if is_obj(v) else 0


def is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# --------------------------------------------------------------------------
# The page's Lua reader (X)

_WS = re.compile(r"\s*")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\s*=")
_VAL = re.compile(r"(true|false|nil|-?(?:0x[0-9a-fA-F]+|[0-9]+(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?))")


class LuaError(ValueError):
    pass


def parse_lua(e: str) -> dict:
    n = len(e)
    pos = 0

    def skip() -> None:
        nonlocal pos
        while True:
            pos = _WS.match(e, pos).end()
            if e.startswith("--", pos):
                if e.startswith("--[[", pos):
                    u = e.find("]]", pos + 4)
                    pos = n if u < 0 else u + 2
                else:
                    u = e.find("\n", pos)
                    pos = n if u < 0 else u + 1
            else:
                return

    def string() -> str:
        nonlocal pos
        q = e[pos]
        pos += 1
        out: list[str] = []
        while pos < n and e[pos] != q:
            if e[pos] == "\\":
                pos += 1
                c = e[pos] if pos < n else ""
                if c == "n":
                    out.append("\n")
                elif c == "t":
                    out.append("\t")
                elif c == "r":
                    out.append("\r")
                elif c.isascii() and c.isdigit():
                    f = ""
                    while pos < n and e[pos].isascii() and e[pos].isdigit() and len(f) < 3:
                        f += e[pos]
                        pos += 1
                    out.append(chr(int(f)))
                    continue
                else:
                    out.append(c)
                pos += 1
            else:
                out.append(e[pos])
                pos += 1
        pos += 1
        return "".join(out)

    def value():
        nonlocal pos
        skip()
        c = e[pos] if pos < n else ""
        if c == "{":
            return table()
        if c in ('"', "'"):
            return string()
        if e.startswith("[[", pos):
            u = e.find("]]", pos)
            if u < 0:
                raise LuaError("unterminated long string")
            s = e[pos + 2 : u]
            pos = u + 2
            return s
        m = _VAL.match(e[pos : pos + 40])
        if not m:
            raise LuaError(f"unexpected {e[pos : pos + 20]!r} at {pos}")
        pos += len(m[0])
        t = m[0]
        if t == "true":
            return True
        if t == "false":
            return False
        if t == "nil":
            return None
        if t.startswith("-0x"):
            return None  # Number("-0x10") is NaN: null in JSON
        if t.startswith("0x"):
            return float(int(t, 16))
        return float(t)

    def table():
        nonlocal pos
        pos += 1
        u: dict = {}
        d = 1
        positional = True
        while True:
            skip()
            if pos < n and e[pos] == "}":
                pos += 1
                break
            if pos >= n:
                raise LuaError("unterminated table")
            if e[pos] == "[":
                pos += 1
                k = value()
                skip()
                pos += 1
                skip()
                pos += 1
                positional = False
                f = k if isinstance(k, str) else js_num(k) if is_num(k) else str(k)
            else:
                m = _KEY.match(e[pos : pos + 80])
                if m:
                    f = re.sub(r"\s*=$", "", m[0])
                    pos += len(m[0])
                    positional = False
                else:
                    f = str(d)
                    d += 1
            u[f] = value()
            skip()
            if pos < n and e[pos] in ",;":
                pos += 1
        if positional and u:
            return [u[k] for k in sorted(u, key=int)]
        return js_order(u)

    out: dict = {}
    while True:
        skip()
        if pos >= n:
            break
        m = _IDENT.match(e[pos : pos + 80])
        if not m:
            break
        pos += len(m[0])
        skip()
        if pos >= n or e[pos] != "=":
            break
        pos += 1
        out[m[0]] = value()
    return out


# --------------------------------------------------------------------------
# The page's section filter (ht) and what it reads

SECTIONS = ["quests", "loot", "beasts", "trainers", "vendors", "nodes", "fish", "skins", "talents", "encounters",
            "botCounts", "auctions", "rares", "flights", "gearReports", "petPinReports", "trails", "routeReports",
            "obs", "feedback"]
TRAIL_KEYS = ["v", "cell", "c", "n", "cap"]
_OBS_ID = re.compile(r"[A-Za-z0-9]{8,32}")
_OBS_B = re.compile(r"[A-Za-z0-9:.,_-]+(?:\^[A-Za-z0-9:.,_-]+){0,39}")
OBS_OWN_MAX, OBS_RELAY_MAX = 400, 6000


def entries(v) -> list[tuple[str, object]]:
    if isinstance(v, list):
        return [(str(i + 1), x) for i, x in enumerate(v)]
    if isinstance(v, dict):
        return list(v.items())
    return []


def obs_index(s) -> int | None:
    return int(s) if re.fullmatch(r"[0-9]{1,9}", s) and int(s) >= 1 else None


def obs_ok(b) -> bool:
    return isinstance(b, str) and len(b) <= 200 and _OBS_B.fullmatch(b) is not None


def obs_list(e: dict) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    s, r = e.get("obs"), e.get("relay")
    if isinstance(s, dict) and is_num(s.get("v")) and s["v"] == 1 and isinstance(s.get("id"), str) \
            and _OBS_ID.fullmatch(s["id"]):
        own = [(obs_index(c), b) for c, b in entries(s.get("out"))]
        own = sorted(((c, b) for c, b in own if c and obs_ok(b)), key=lambda x: x[0])[:OBS_OWN_MAX]
        for c, b in own:
            seen.add(f"{s['id']}:{c}")
            out.append({"i": s["id"], "s": c, "b": b, "own": True})
    if isinstance(r, dict) and is_num(r.get("v")) and r["v"] == 1:
        a = 0
        for c, i in entries(r.get("got")):
            if not _OBS_ID.fullmatch(c):
                continue
            for u, d in entries(i):
                ln = obs_index(u)
                if not ln or not obs_ok(d) or f"{c}:{ln}" in seen or a >= OBS_RELAY_MAX:
                    continue
                seen.add(f"{c}:{ln}")
                out.append({"i": c, "s": ln, "b": d, "own": False})
                a += 1
    return out


def characters(w: dict) -> dict[str, dict]:
    """The page's $e: armory entries by guid, the newest of each."""
    chars: dict[str, dict] = {}
    for k, s in entries(w.get("armory")) if isinstance(w.get("armory"), dict) else []:
        if not isinstance(s, dict):
            continue
        g = s["guid"] if isinstance(s.get("guid"), str) else k
        if not g.startswith("Player-"):
            continue
        if g in chars and (chars[g].get("time") or 0) >= (s.get("time") or 0):
            continue
        chars[g] = s
    return chars


SNAPSHOT_KEYS = ["guid", "name", "surname", "realm", "realmNorm", "region", "regionName", "level", "race", "raceId",
                 "class", "classId", "faction", "sex", "guild", "guildRoster", "gear", "ilvl", "stats", "talents",
                 "professions", "skills", "reps", "legacy", "mounts", "pets", "consumables", "resist", "time",
                 "addon", "build"]


def is_letter(c: str) -> bool:
    return unicodedata.category(c)[0] == "L"


def scrub_names(sections: dict, names: list, words: set[str] | None) -> None:
    """Character names in the quests' text become $N (the page's ft)."""
    ok = [n for n in dict.fromkeys(x for x in names if isinstance(x, str))
          if 2 <= len(n) <= 12 and all(is_letter(c) for c in n) and not (words and n.lower() in words)]
    ok.sort(key=len, reverse=True)
    quests = sections.get("quests")
    if not ok or not is_obj(quests):
        return

    def sub(s):
        if not isinstance(s, str):
            return s
        out: list[str] = []
        i = last = 0
        while i < len(s):
            hit = None
            if i == 0 or not is_letter(s[i - 1]):
                for nm in ok:
                    j = i + len(nm)
                    if s.startswith(nm, i) and (j >= len(s) or not is_letter(s[j])):
                        hit = nm
                        break
            if hit:
                out += [s[last:i], "$N"]
                i = last = i + len(hit)
            else:
                i += 1
        out.append(s[last:])
        return "".join(out)

    for q in values(quests):
        if not isinstance(q, dict):
            continue
        for k in ("t", "obj"):
            if k in q:
                q[k] = sub(q[k])
        if isinstance(q.get("objs"), (dict, list)):
            for o in values(q["objs"]):
                if isinstance(o, dict) and isinstance(o.get("t"), str):
                    o["t"] = sub(o["t"])


def build_sections(w: dict, names: list, words: set[str] | None) -> dict:
    s: dict = {}
    for k in SECTIONS:
        if k != "obs" and isinstance(w.get(k), (dict, list)):
            s[k] = copy.deepcopy(w[k])
    if "trails" in s:
        t = s["trails"]
        s["trails"] = {k: t[k] for k in TRAIL_KEYS if isinstance(t, dict) and k in t}
    ob = obs_list(w)
    if ob:
        s["obs"] = ob
    if words is not None:
        scrub_names(s, names, words)
    return s


def counts_of(w: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for k in SECTIONS:
        v = w.get(k)
        if k == "encounters":
            c = sum(nkeys(a.get("fights")) if isinstance(a, dict) else 0 for a in values(w.get("encounters")))
        elif k == "auctions":
            c = sum(len([x for x in (a["i"] if isinstance(a.get("i"), dict) else {}) if ":" not in x])
                    if isinstance(a, dict) else 0 for a in values(w.get("auctions")))
        elif k == "trails":
            c = sum(nkeys(x) for x in values(v.get("c"))) if isinstance(v, dict) and isinstance(v.get("c"), dict) else 0
        elif k == "obs":
            c = sum(len(o["b"].split("^")) for o in obs_list(w))
        else:
            c = nkeys(v)
        if c:
            out[k] = c
    return out


# --------------------------------------------------------------------------
# Files, state, request


def account_files(wow: Path, name: str = "foreverdb.lua") -> list[tuple[str, Path]]:
    """(account folder name, file) for each account, oldest file first. `name`: lowercase file name."""
    acc = wow / "WTF" / "Account"
    found: list[tuple[float, str, Path]] = []
    try:
        dirs = sorted(d for d in acc.iterdir() if d.is_dir() and d.name.lower() != "savedvariables")
    except OSError:
        return []
    for d in dirs:
        sv = next((x for x in d.iterdir() if x.is_dir() and x.name.lower() == "savedvariables"), None)
        if sv is None:
            continue
        for f in sv.iterdir():
            if f.is_file() and f.name.lower() == name:
                found.append((f.stat().st_mtime, d.name, f))
    return [(a, f) for _, a, f in sorted(found, key=lambda x: x[0])]


def new_install_id() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(24)).decode().rstrip("=")


class Skip(Exception):
    """Nothing to send from this file."""


class Retry(Exception):
    """Not now: the next run tries again."""


def read_vars(path: Path) -> dict:
    """The file's top-level variables, read like the page (48 MiB cap)."""
    if path.stat().st_size > MAX_FILE:
        raise Skip("file too large")
    return parse_lua(path.read_text(encoding="utf-8", errors="replace"))


def plan_file(lua_vars: dict, words_get) -> dict:
    """One ForeverDB.lua like the page: sections, counts, hash, anonymous payload."""
    db = lua_vars.get("ForeverDB_DB")
    if not isinstance(db, dict):
        raise Skip("nothing saved yet")
    chars = characters(db)
    ordered = sorted(chars.items(), key=lambda kv: -(kv[1].get("time") or 0))
    names = [c.get("name") or "?" for _, c in ordered]
    words = words_get() if names else None  # the page fetches the list only when it has names
    if names and words is None:
        # The page then sends the quests' text unscrubbed; this waits for the list instead.
        raise Retry("the game word list is not available")
    sections = build_sections(db, names, words)
    snaps = {g: {k: s[k] for k in SNAPSHOT_KEYS if k in s} for g, s in chars.items()}
    version = db["addonVersion"] if isinstance(db.get("addonVersion"), str) else ""
    digest = hashlib.sha256(stringify({"sections": sections, "snapshots": snaps,
                                       "addonVersion": version}).encode()).hexdigest()
    counts = counts_of(db)
    payload = {"kind": "fdb-upload", "v": 1, "addonVersion": version}
    if counts:
        payload["data"] = sections
    payload["armory"] = []
    raw = stringify(payload).encode()
    return {"hash": digest, "counts": counts, "payload": payload, "raw": raw, "body": gzip.compress(raw, mtime=0),
            "sections": sections, "snaps": snaps, "guids": [g for g, _ in ordered], "version": version}


def logged_in_body(plan: dict, data: bool, guids: list[str]) -> tuple[bytes, bytes]:
    """The logged-in page's ut(): data only when shared, the ticked characters' snapshots as the armory."""
    payload = {"kind": "fdb-upload", "v": 1, "addonVersion": plan["version"]}
    if data:
        payload["data"] = plan["sections"]
    payload["armory"] = [plan["snaps"][g] for g in guids if g in plan["snaps"]]
    raw = stringify(payload).encode()
    return raw, gzip.compress(raw, mtime=0)


# --------------------------------------------------------------------------
# The Helper's anonymous share section (the page's ye, bt, be, gt)

HC = {
    "top": ["schema", "addon", "build", "locale"],
    "run": ["id", "map", "diff", "start", "stop", "dur", "done", "open", "size", "helpers", "lv", "cls"],
    "kill": ["npc", "enc", "boss", "key", "t", "wipes", "at"],
    "loot": ["item", "n", "how"],
    "wipe": ["npc", "enc", "t", "hp", "at"],
    "wish": ["added", "got"],
    "goal": ["id", "kind", "done"],
    "task": ["k", "d"],
    "tip": ["id", "map", "npc", "enc", "boss", "quest", "text", "at"],
    "find": ["item", "how", "m", "x", "y", "npc", "obj", "vendor", "p", "quest", "n", "at"],
}


def is_plain(v) -> bool:
    return isinstance(v, dict)


def pick(e, keys):
    """The page's Z: the listed primitive fields of an object, else None."""
    if not is_plain(e):
        return None
    return {k: e[k] for k in keys if k in e and e[k] is not None and not isinstance(e[k], (dict, list))}


def as_list(e) -> list:
    """The page's Xe: an array, or an object's digit keys in order."""
    if isinstance(e, list):
        return e
    if is_plain(e):
        return [e[k] for k in sorted((k for k in e if re.fullmatch(r"[0-9]+", k)), key=int)]
    return []


def pick_list(e, keys, extra=None) -> list:
    out = []
    for x in as_list(e):
        r = pick(x, keys)
        if r is not None and extra:
            r = extra(x, r)
        if r is not None:
            out.append(r)
    return out


def _kill(k, a):
    return {**a, "loot": pick_list(k.get("loot"), HC["loot"])}


def _run(n, r):
    if not is_plain(n):
        return r
    return {**r, "kills": pick_list(n.get("kills"), HC["kill"], _kill), "wipes": pick_list(n.get("wipes"), HC["wipe"])}


def _goal(n, r):
    return {**r, "tasks": pick_list(n.get("tasks"), HC["task"])}


def share_filter(e) -> dict:
    """The page's ye: only the whitelisted primitive fields of the Helper's share section."""
    t = pick(e, HC["top"]) or {}
    get = e.get if is_plain(e) else (lambda k: None)
    t["runs"] = pick_list(get("runs"), HC["run"], _run)
    if is_plain(get("wish")):
        wish = {n: pick(v, HC["wish"]) or {} for n, v in e["wish"].items() if re.fullmatch(r"[0-9]{1,9}", n)}
        t["wish"] = js_order(wish)
    if is_plain(e) and "goals" in e:
        t["goals"] = pick_list(e["goals"], HC["goal"], _goal)
    tips = get("tips")
    tips = tips if isinstance(tips, list) else list(tips.values()) if is_plain(tips) else []
    t["tips"] = [r for r in (pick(x, HC["tip"]) for x in tips) if r is not None]
    t["finds"] = pick_list(get("finds"), HC["find"])
    return t


def merge_shares(shares: list) -> tuple[dict, float]:
    runs: dict = {}
    wish: dict = {}
    goals: list = []
    tips: list = []
    finds: list = []
    top: dict = {}
    has_wish = has_goals = False
    updated = 0.0
    for f in shares:
        if not is_plain(f) and not isinstance(f, list):
            continue
        u = f.get("updated") if is_plain(f) else None
        updated = max(updated, float(u) if is_num(u) else 0.0)
        w = share_filter(f)
        for k in HC["top"]:
            if k in w:
                top[k] = w[k]
        for r in w["runs"]:
            runs[r.get("id") if not isinstance(r.get("id"), (dict, list)) else None] = r
        if "wish" in w:
            has_wish = True
            wish.update(w["wish"])
        if "goals" in w:
            has_goals = True
            goals += w["goals"]
        tips += w["tips"]
        finds += w["finds"]
    out = {**top, "runs": list(runs.values()), "tips": tips, "finds": finds}
    if has_wish:
        out["wish"] = js_order(wish)
    if has_goals:
        out["goals"] = goals
    return out, updated


def share_counts(e: dict) -> dict[str, int]:
    runs = values(e.get("runs"))
    return {"runs": len(runs),
            "kills": sum(len(values(r.get("kills"))) for r in runs if is_plain(r)),
            "wipes": sum(len(values(r.get("wipes"))) for r in runs if is_plain(r)),
            "finds": len(values(e.get("finds"))), "tips": len(values(e.get("tips"))),
            "wish": len(e["wish"]) if is_plain(e.get("wish")) else 0, "goals": len(values(e.get("goals")))}


def _fin(v) -> float:
    return float(v) if is_num(v) and math.isfinite(v) else 0.0


def helper_rank(g: dict) -> list:
    db = g["db"]
    gen = db.get("gen")
    share = db.get("share")
    return [float(gen) if is_num(gen) and math.isfinite(gen) else -1.0, _fin(db.get("savedAt")),
            _fin(share.get("updated")) if is_plain(share) else 0.0, g["mtime"], 1 if g["companion"] else 0]


def plan_helper(entries: list[tuple[str, Path, bool, dict]]) -> dict | None:
    """The merged share section of every account's Helper data (the page's plan.helper):
    entries are (account, file, is_companion, parsed variables), any order."""
    best: dict[str, dict] = {}
    for acct, path, comp, v in sorted(entries, key=lambda x: x[1].stat().st_mtime):
        db = v.get("ForeverDBHelperDB")
        if not is_plain(db) or not db:
            continue
        g = {"db": db, "companion": comp, "mtime": path.stat().st_mtime * 1000}
        if acct not in best or helper_rank(g) > helper_rank(best[acct]):
            best[acct] = g
    shares = [g["db"]["share"] for g in sorted(best.values(), key=lambda g: g["mtime"])
              if is_plain(g["db"].get("share"))]
    if not shares:
        return None
    share, saved = merge_shares(shares)
    counts = share_counts(share)
    raw = stringify({"share": share}).encode()
    return {"share": share, "saved": saved, "counts": counts, "empty": not any(counts.values()),
            "hash": hashlib.sha256(stringify(share).encode()).hexdigest(), "raw": raw,
            "body": gzip.compress(raw, mtime=0)}


# --------------------------------------------------------------------------
# The owner's own foreverdb.net session (the logged-in page's credential: a cookie)

def load_session(path: Path) -> tuple[str | None, str | None]:
    """(Cookie header value, why it was refused). A missing file is no session and no complaint."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None, None
    except OSError:
        return None, "cannot be read"
    if not stat.S_ISREG(st.st_mode):
        return None, "is not a plain file"
    if os.name != "nt":
        if st.st_mode & 0o777 != 0o600:
            return None, f"must have mode 600 (it has {st.st_mode & 0o777:o})"
        if st.st_uid != os.getuid():
            return None, "must be owned by you"
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None, "cannot be read as text"
    text = re.sub(r"^\s*cookie:\s*", "", text.strip(), flags=re.I)
    if not text or not text.isascii() or any(c in text for c in "\r\n\x00"):
        return None, "must hold one line: the fdb_s cookie's value"
    # The bare value, `fdb_s=value`, or a whole Cookie header of which only fdb_s is taken.
    mine = next((x.strip() for x in text.split(";") if x.strip().lower().startswith("fdb_s=")), None)
    value = mine[6:] if mine else text
    if not re.fullmatch(r"[A-Za-z0-9._~%+/:=-]+", value):
        return None, "holds no fdb_s cookie value"
    return "fdb_s=" + value, None


def session_headers(cookie: str) -> dict[str, str]:
    """What the logged-in page's fetch carries: the gzip type, X-FDB, the browser's cookie."""
    return {"Content-Type": "application/gzip", "X-FDB": "1", "Origin": SITE, "Referer": SITE + "/send",
            "Accept": "*/*", "User-Agent": "wow-stats-shipper (foreverdb_send)", "Cookie": cookie}


def headers(install: str) -> dict[str, str]:
    return {"Content-Type": "application/gzip", "X-FDB": "1", "X-FDB-Install": install,
            "Origin": SITE, "Referer": SITE + "/send", "Accept": "*/*",
            "User-Agent": "wow-stats-shipper (foreverdb_send)"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Challenge(Exception):
    """Cloudflare answered with a bot challenge instead of the API: never worked around."""


def http(url: str, data: bytes | None = None, hdrs: dict | None = None) -> tuple[int, dict | None]:
    """(status, JSON body or None). Network errors raise OSError. A Cookie goes to foreverdb.net only."""
    if any(k.lower() == "cookie" for k in (hdrs or {})) and not url.startswith(SITE + "/"):
        raise OSError("refusing to send a session anywhere but foreverdb.net")
    req = urllib.request.Request(url, data=data, headers=hdrs or {"User-Agent": "wow-stats-shipper (foreverdb_send)"},
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=60) as r:
            if r.headers.get("cf-mitigated"):
                raise Challenge
            status, text = r.status, r.read(4 * 1024 * 1024)
    except urllib.error.HTTPError as e:
        if e.headers.get("cf-mitigated"):
            raise Challenge from None
        status, text = e.code, e.read(65536)
    except urllib.error.URLError as e:
        raise OSError(str(getattr(e, "reason", e))) from None
    try:
        return status, json.loads(text)
    except ValueError:
        return status, None


def fetch_words() -> set[str] | None:
    try:
        status, body = http(WORDS_URL)
    except OSError:
        return None
    if status == 200 and isinstance(body, dict) and isinstance(body.get("words"), list):
        return set(body["words"])
    return None


def state_path(archive: Path) -> Path:
    return archive / STATE_NAME


def load_state(p: Path) -> dict:
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(st, dict):
            return st
    except (OSError, ValueError):
        pass
    return {}


def save_state(p: Path, st: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(st), encoding="utf-8")
    os.replace(tmp, p)


def enabled(config: dict) -> bool:
    v = os.environ.get("WOW_STATS_FOREVERDB_SEND", config.get("foreverdb_send", False))
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


HELPER_KEYS = ("runs", "kills", "finds", "wish")


def check_response(resp, sent: dict[str, int], *, armory: int = 0,
                   helper: dict[str, int] | None = None) -> tuple[bool, str]:
    """Was it taken? The site's answer must show what we sent: the shared counts per section (the page reads
    `shared.counts`), the characters stored (`armory.characters`), the Helper's counts (`helper.counts`).
    Returns (all there, one log phrase: counts and numbers only)."""
    if not isinstance(resp, dict):
        return False, "no JSON answer"
    parts: list[str] = []
    ok = True
    found = resp.get("found")
    if sent:
        sh = resp.get("shared") if isinstance(resp.get("shared"), dict) else {}
        counts = sh.get("counts") if isinstance(sh.get("counts"), dict) else {}
        skipped = sh.get("skipped") if isinstance(sh.get("skipped"), list) else []
        if sh.get("error"):
            ok = False
            parts.append("shared: the site reports an error")
        if not counts:
            ok = False
            parts.append("no shared counts in the answer")
        for k, v in sent.items():
            got = counts.get(k)
            if got == v:
                parts.append(f"{k} {got}/{v}")
            elif k in skipped:
                parts.append(f"{k} skipped by the site")
            elif counts and isinstance(found, (dict, list)) and k in found:
                parts.append(f"{k} {got if isinstance(got, int) else 0}/{v} (known already)")
            elif counts:
                ok = False
                parts.append(f"{k} MISMATCH {got if isinstance(got, int) else 0}/{v}")
    if armory:
        a = resp.get("armory") if isinstance(resp.get("armory"), dict) else {}
        chars = a.get("characters") if isinstance(a.get("characters"), list) else []
        took = sum(1 for c in chars if isinstance(c, dict) and (c.get("stored") or c.get("stored") is False))
        rejected = len(a["rejected"]) if isinstance(a.get("rejected"), list) else 0
        parts.append(f"armory {took}/{armory}")
        if a.get("error") or took < armory or rejected:
            ok = False
            parts.append("armory MISMATCH")
    if helper:
        h = resp.get("helper") if isinstance(resp.get("helper"), dict) else {}
        hc = h.get("counts") if isinstance(h.get("counts"), dict) else {}
        if not hc:
            ok = False
            parts.append("no helper counts in the answer")
        for k in HELPER_KEYS:
            if helper.get(k):
                got = hc.get(k)
                parts.append(f"{k} {got if isinstance(got, int) else 0}/{helper[k]}")
                if got != helper[k]:
                    ok = False
                    parts[-1] = parts[-1].replace(k, f"{k} MISMATCH", 1)
    return ok, ", ".join(parts) if parts else "nothing to compare"


def verdict(ok: bool, phrase: str) -> str:
    return phrase if ok else phrase + "; not recorded as sent, will retry"


def _digest(cookie: str) -> str:
    return hashlib.sha256(cookie.encode()).hexdigest()


def run(wow: Path, archive: Path, log, *, dry: bool = False, force: bool = False, now: float | None = None,
        words_get=None, session_file: Path | None = None, armory: list[str] | None = None) -> int:
    """Send what changed. Returns how many requests were sent (dry: planned).
    With a session file: as the logged-in page (ForeverDB.lua with the characters, and the Helper's share);
    without, or when the session has expired: anonymously."""
    now = time.time() if now is None else now
    words_get = words_get or fetch_words
    sp = state_path(archive)
    st = load_state(sp)
    files = account_files(wow)
    hfiles = account_files(wow, "foreverdb_helper.lua")
    if not files and not hfiles:
        return 0
    sess = st.setdefault("session", {})
    cookie = None
    if session_file is not None:
        cookie, why = load_session(Path(session_file))
        if why and sess.get("refused") != why:
            log(f"foreverdb: not using {session_file}: it {why}")
        if why:
            sess["refused"] = why
        else:
            sess.pop("refused", None)
        if cookie and sess.get("expired") == _digest(cookie):
            cookie = None  # the same expired session: said once already
    stamps = {a: [f.stat().st_mtime_ns, f.stat().st_size] for a, f in files}
    if cookie:
        stamps.update({a + "/h": [f.stat().st_mtime_ns, f.stat().st_size] for a, f in hfiles})
        known_stamps = sess.get("stamps", {})
    else:
        known_stamps = {a: v.get("stamp") for a, v in st.get("files", {}).items()}
    if not dry and not force:
        if all(known_stamps.get(a) == s for a, s in stamps.items()):
            return 0
        if now - float(st.get("tried", 0)) < MIN_INTERVAL_S:
            return 0
    if not dry:
        st["tried"] = now
    sent = 0
    try:
        sent = _passes(files, hfiles, stamps, cookie, st, sess, log, dry, force, now, words_get,
                       session_file, armory or [])
    except Challenge:
        log("foreverdb.net answered with a Cloudflare challenge: not sending (nothing is done about it)")
        sess["challenged"] = now
    if not dry:
        save_state(sp, st)
    return sent


def _passes(files, hfiles, stamps, cookie, st, sess, log, dry, force, now, words_get, session_file, armory) -> int:
    sent = 0
    if cookie:
        result = _session_pass(files, hfiles, stamps, cookie, st, sess, log, dry, force, now, words_get, armory)
        if result == "expired":
            log(f"foreverdb.net session expired: paste a new one into {session_file}")
            sess["expired"] = _digest(cookie)
            cookie = None
        else:
            sent = result
    if not cookie:
        stamps = {a: [f.stat().st_mtime_ns, f.stat().st_size] for a, f in files}
        sent += _anon_pass(files, stamps, st, log, dry, force, now, words_get)
    return sent


def _anon_pass(files, stamps, st, log, dry, force, now, words_get) -> int:
    known = st.get("files", {})
    install = st.get("install") if isinstance(st.get("install"), str) and INSTALL_RE.fullmatch(st["install"]) else None
    sent = 0
    new_files = dict(known)
    for idx, (acct, path) in enumerate(files, 1):
        label = f"account {idx}"
        try:
            plan = plan_file(read_vars(path), words_get)
        except Skip as e:
            log(f"foreverdb: {label}: {e}")
            new_files[acct] = {"stamp": stamps[acct], "hash": None}
            continue
        except (Retry, LuaError, OSError, RecursionError) as e:
            log(f"foreverdb: {label}: not read ({e if isinstance(e, Retry) else type(e).__name__})")
            continue
        shape = ", ".join(f"{k}={v}" for k, v in plan["counts"].items()) or "nothing to share"
        if dry:
            top = ["kind", "v", "addonVersion", *(["data"] if "data" in plan["payload"] else []), "armory"]
            log(f"foreverdb: {label}: would send {len(plan['raw'])} bytes JSON, {len(plan['body'])} gzipped; "
                f"top-level {top}; data sections {sorted(plan['payload'].get('data', {}))}; counts {shape}")
            sent += 1 if plan["counts"] else 0
            continue
        old = known.get(acct, {}).get("hash")
        if not plan["counts"] or (old == plan["hash"] and not force):
            new_files[acct] = {"stamp": stamps[acct], "hash": old}
            continue
        install = install or new_install_id()
        try:
            status, resp = http(SEND_URL, plan["body"], headers(install))
        except OSError as e:
            log(f"foreverdb: {label}: send failed ({e})")
            continue
        good, phrase = check_response(resp, plan["counts"]) if 200 <= status < 300 else (False, "")
        log(f"foreverdb: {label}: sent {len(plan['body'])} bytes, anon, HTTP {status}"
            + (f", {verdict(good, phrase)}" if 200 <= status < 300 else ""))
        if good:
            new_files[acct] = {"stamp": stamps[acct], "hash": plan["hash"]}
            st["at"] = now
            sent += 1
    if not dry:
        st["install"] = install or st.get("install")
        st["files"] = new_files
        if st["install"] is None:
            st.pop("install")
    return sent


def _session_pass(files, hfiles, stamps, cookie, st, sess, log, dry, force, now, words_get, armory):
    """The logged-in page's send. Returns the number of requests sent, or "expired" on a 401/403."""
    mem = sess.setdefault("files", {})
    hdrs = session_headers(cookie)
    parsed: dict[Path, dict] = {}
    sent = 0
    clean = True
    entries = []
    for idx, (acct, path) in enumerate(files, 1):
        label = f"account {idx}"
        try:
            parsed[path] = read_vars(path)
            entries.append((acct, path, True, parsed[path]))
            plan = plan_file(parsed[path], words_get)
        except Skip as e:
            log(f"foreverdb: {label}: {e}")
            continue
        except (Retry, LuaError, OSError, RecursionError) as e:
            log(f"foreverdb: {label}: not read ({e if isinstance(e, Retry) else type(e).__name__})")
            clean = False
            continue
        entry = mem.get(acct, {})
        picked = [g for g in plan["guids"] if plan["snaps"].get(g, {}).get("name") in armory]
        ticked = ",".join(picked) or "none"  # the page's wt(): the ticked characters
        changed = entry.get("hash") != plan["hash"]
        share = bool(plan["counts"]) and (force or changed)
        chars = ticked != "none" and (force or changed or entry.get("armory") != ticked)
        if not share and not chars:
            continue
        raw, body = logged_in_body(plan, share, picked if chars else [])
        if dry:
            log(f"foreverdb: {label}: would send (logged in) {len(raw)} bytes JSON, {len(body)} gzipped; "
                f"data {share}, characters {len(picked) if chars else 0}")
            sent += 1
            continue
        try:
            status, resp = http(ME_URL, body, hdrs)
        except OSError as e:
            log(f"foreverdb: {label}: send failed ({e})")
            clean = False
            continue
        if status in (401, 403):
            return "expired"
        good, phrase = (check_response(resp, plan["counts"] if share else {}, armory=len(picked) if chars else 0)
                        if 200 <= status < 300 else (False, ""))
        log(f"foreverdb: {label}: sent (logged in) {len(body)} bytes, me, HTTP {status}"
            + (f", {verdict(good, phrase)}" if 200 <= status < 300 else ""))
        if good:
            mem[acct] = {"hash": plan["hash"], "armory": ticked}
            st["at"] = now
            sent += 1
        else:
            clean = False
    for acct, path in hfiles:
        try:
            entries.append((acct, path, False, read_vars(path)))
        except (Skip, LuaError, OSError, RecursionError):
            clean = False
    helper = plan_helper(entries)
    if helper is not None and not helper["empty"] and (force or sess.get("helper", {}).get("hash") != helper["hash"]):
        counts = ", ".join(f"{k}={v}" for k, v in helper["counts"].items() if v)
        if dry:
            log(f"foreverdb: helper: would send (logged in) {len(helper['raw'])} bytes JSON, "
                f"{len(helper['body'])} gzipped; top-level ['share']; counts {counts}")
            sent += 1
        else:
            try:
                status, resp = http(ME_URL, helper["body"], hdrs)
            except OSError as e:
                log(f"foreverdb: helper: send failed ({e})")
                status, resp = 0, None
                clean = False
            if status in (401, 403):
                return "expired"
            if status:
                good, phrase = check_response(resp, {}, helper=helper["counts"]) if 200 <= status < 300 else (False, "")
                log(f"foreverdb: helper: sent (logged in) {len(helper['body'])} bytes, me, HTTP {status}"
                    + (f", {verdict(good, phrase)}" if 200 <= status < 300 else f", {counts}"))
                if good or status == 400:  # the page remembers a refused 400 too
                    sess["helper"] = {"hash": helper["hash"], "saved": helper["saved"]}
                if good:
                    st["at"] = now
                    sent += 1
                else:
                    clean = False
    if not dry and clean:
        sess["stamps"] = stamps
    return sent


def session_path(config: dict, archive: Path) -> Path | None:
    """Where the owner's foreverdb.net Cookie header value is kept; `foreverdb_session_file = ""` turns it off."""
    v = config.get("foreverdb_session_file")
    if v is None:
        return archive / "foreverdb-session"
    return Path(os.path.expandvars(str(v))).expanduser() if v else None


def armory_names(config: dict) -> list[str]:
    """The characters to put in your armory (logged-in sends only); none by default."""
    v = config.get("foreverdb_armory", [])
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def maybe_send(shipper) -> int:
    """The hook in wow_stats.main(): `shipper` is the wow_stats module."""
    if not enabled(shipper.CONFIG):
        return 0
    return run(shipper.WOW, shipper.ARCHIVE, shipper.log, session_file=session_path(shipper.CONFIG, shipper.ARCHIVE),
               armory=armory_names(shipper.CONFIG))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print the size and shape of each request; send nothing")
    ap.add_argument("--force", action="store_true", help="send every file now, whatever the last send")
    a = ap.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import wow_stats  # noqa: PLC0415 - the shipper's own WoW folder discovery and config

    wow_stats.DRY = a.dry_run
    wow_stats.configure()
    if not a.dry_run and not enabled(wow_stats.CONFIG):
        print("foreverdb_send is off: set foreverdb_send = true in config.toml", file=sys.stderr)
        return 2
    n = run(wow_stats.WOW, wow_stats.ARCHIVE, wow_stats.log, dry=a.dry_run, force=a.force,
            session_file=session_path(wow_stats.CONFIG, wow_stats.ARCHIVE), armory=armory_names(wow_stats.CONFIG))
    print(f"foreverdb: {n} file(s) {'planned' if a.dry_run else 'sent'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
