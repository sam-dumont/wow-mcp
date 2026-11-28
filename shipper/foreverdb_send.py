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
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

SITE = "https://foreverdb.net"
SEND_URL = SITE + "/api/anon/addon-file"
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


def account_files(wow: Path) -> list[tuple[str, Path]]:
    """(account folder name, ForeverDB.lua) for each account, oldest file first."""
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
            if f.is_file() and f.name.lower() == "foreverdb.lua":
                found.append((f.stat().st_mtime, d.name, f))
    return [(a, f) for _, a, f in sorted(found, key=lambda x: x[0])]


def new_install_id() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(24)).decode().rstrip("=")


class Skip(Exception):
    """Nothing to send from this file."""


class Retry(Exception):
    """Not now: the next run tries again."""


def plan_file(path: Path, words_get) -> dict:
    """Read one ForeverDB.lua like the page: sections, counts, hash, payload."""
    if path.stat().st_size > MAX_FILE:
        raise Skip("file too large")
    db = parse_lua(path.read_text(encoding="utf-8", errors="replace")).get("ForeverDB_DB")
    if not isinstance(db, dict):
        raise Skip("nothing saved yet")
    chars = characters(db)
    names = [c.get("name") or "?" for c in sorted(chars.values(), key=lambda s: -(s.get("time") or 0))]
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
    return {"hash": digest, "counts": counts, "payload": payload, "raw": raw, "body": gzip.compress(raw, mtime=0)}


def headers(install: str) -> dict[str, str]:
    return {"Content-Type": "application/gzip", "X-FDB": "1", "X-FDB-Install": install,
            "Origin": SITE, "Referer": SITE + "/send", "Accept": "*/*",
            "User-Agent": "wow-stats-shipper (foreverdb_send)"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def http(url: str, data: bytes | None = None, hdrs: dict | None = None) -> tuple[int, dict | None]:
    """(status, JSON body or None). Network errors raise OSError."""
    req = urllib.request.Request(url, data=data, headers=hdrs or {"User-Agent": "wow-stats-shipper (foreverdb_send)"},
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=60) as r:
            status, text = r.status, r.read(4 * 1024 * 1024)
    except urllib.error.HTTPError as e:
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


def summary(resp) -> str:
    """Counts only, from the answer's shared section."""
    sh = resp.get("shared") if isinstance(resp, dict) else None
    if isinstance(sh, dict) and isinstance(sh.get("counts"), dict):
        return ", ".join(f"{k}={v}" for k, v in sh["counts"].items() if isinstance(v, int) and v)
    return "no counts in the answer"


def run(wow: Path, archive: Path, log, *, dry: bool = False, force: bool = False, now: float | None = None,
        words_get=None) -> int:
    """Send what changed. Returns how many files were sent (dry: planned)."""
    now = time.time() if now is None else now
    words_get = words_get or fetch_words
    sp = state_path(archive)
    st = load_state(sp)
    files = account_files(wow)
    if not files:
        return 0
    stamps = {a: [f.stat().st_mtime_ns, f.stat().st_size] for a, f in files}
    known = st.get("files", {})
    if not dry and not force:
        if all(known.get(a, {}).get("stamp") == s for a, s in stamps.items()):
            return 0
        if now - float(st.get("tried", 0)) < MIN_INTERVAL_S:
            return 0
    install = st.get("install") if isinstance(st.get("install"), str) and INSTALL_RE.fullmatch(st["install"]) else None
    sent = 0
    new_files = dict(known)
    if not dry:
        st["tried"] = now
    for idx, (acct, path) in enumerate(files, 1):
        label = f"account {idx}"
        try:
            plan = plan_file(path, words_get)
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
        log(f"foreverdb: {label}: sent {len(plan['body'])} bytes, HTTP {status}"
            + (f", {summary(resp)}" if 200 <= status < 300 else ""))
        if 200 <= status < 300:
            new_files[acct] = {"stamp": stamps[acct], "hash": plan["hash"]}
            st["at"] = now
            sent += 1
    if not dry:
        st["install"] = install or st.get("install")
        st["files"] = new_files
        if st["install"] is None:
            st.pop("install")
        save_state(sp, st)
    return sent


def maybe_send(shipper) -> int:
    """The hook in wow_stats.main(): `shipper` is the wow_stats module."""
    if not enabled(shipper.CONFIG):
        return 0
    return run(shipper.WOW, shipper.ARCHIVE, shipper.log)


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
    n = run(wow_stats.WOW, wow_stats.ARCHIVE, wow_stats.log, dry=a.dry_run, force=a.force)
    print(f"foreverdb: {n} file(s) {'planned' if a.dry_run else 'sent'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
