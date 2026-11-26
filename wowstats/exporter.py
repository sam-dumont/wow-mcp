"""Battle.net API exporter for WoW: Forever (and the Retail WoW Token).

Stdlib only, one process, three polling loops on their own cadence:
  token     /data/wow/token/index per game namespace, every 20 min
  realm     /data/wow/ruleset/index and each ruleset, every 5 min (Forever is
            realmless: a ruleset's status is the closest thing to realm status)
  profile   each character in PROFILE_CHARACTERS, every PROFILE_INTERVAL (2 h)
            on the clock (00:00, 02:00... UTC, not from the pod's start), and
            a character listed for the first time within 5 minutes (poll_new):
            its profile,
            appearance (and sex), guild, equipment, talents (/specializations) and
            completed achievements (the Legacy points). Forever has no realms: a character is
            `{name}-{surname}` lowercased (testpriest-fixture) in the
            profile-forever-{region} namespace (checked live 2026-11-18). A
            character the API doesn't know yet (404, too low or never seen)
            reads wow_character_profile_found 0 and nothing else.
            Every series carries the `account_id` of the person whose
            character it is (0: the owner, and PROFILE_CHARACTERS); the
            readers ask for their own account only (wowstats.stores).

No auction house: the project reads no Auction House API (owner's rule,
2026-11-20). Prices come from the player's own Auctionator scans
(wowstats/ahsource.py), which the shipper uploads like any other event.

Every request sends If-Modified-Since (the API answers 304, checked live
2026-11-18) and Accept-Encoding: gzip, so an unchanged snapshot costs a few
hundred bytes. A 429 pauses every loop (Retry-After, else exponential).

Where the numbers go, and why two places: Blizzard's Developer API Terms cap
the retention of API data at 30 days, so the game data is never scraped into
a longer-lived store. It is pushed every VM_PUSH_INTERVAL to a VictoriaMetrics
of its own with a short retention (/api/v1/import/prometheus), which this
process also force-merges daily when VM_MERGE_KEY_FILE is set, so expired
blocks are really deleted. /metrics and /healthz on LISTEN_PORT (9464) only
serve the exporter's own health (wow_api_up, wow_exporter_*), safe to scrape
into any store.

Logs carry status codes and counts, never prices: container logs often end
up in a store kept longer than 30 days.

Environment: BNET_CLIENT_ID_FILE, BNET_CLIENT_SECRET_FILE (required: your own
client, docs/self-host/battlenet.md), BNET_REGION (eu), BNET_LOCALE (en_GB), PROFILE_CHARACTERS
(comma list of Name-Surname, as the WTF folders name them),
BNET_PROFILE_NAMESPACE (profile-forever-{region}), PROFILE_INTERVAL (7200),
VM_PUSH_URL (may carry its own query args), VM_PUSH_EXTRA_LABELS (comma list of name=value), VM_PUSH_INTERVAL (60),
VM_MERGE_KEY_FILE, VM_MERGE_URL (default: VM_PUSH_URL's host,
/internal/force_merge), LISTEN_PORT (9464).
"""

import base64
import gzip
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# One line each: a chained `a = b = ""` ties a key file's name to the
# values the log prints, as far as CodeQL's taint tracking can tell.
REGION = ""
LOCALE = ""
API = ""
OAUTH = ""
FOREVER_NS = ""
PROFILE_NS = ""
PUSH_URL = ""
MERGE_URL = ""
MERGE_KEY_FILE = ""
CLIENT_ID_FILE = ""
CLIENT_SECRET_FILE = ""
TOKEN_NAMESPACES: dict = {}
PROFILE_CHARS: list = []
# Several people (wowstats.tenancy): the full view's list of every person's
# characters, one "<account_id>\t<name>" per line, read at each profile pass
# and added to PROFILE_CHARACTERS (account 0).
PROFILE_CHARS_URL = ""
# Characters read per person a day from that list, newest first (security
# review 0.6.0 S1: a friend's invented names can't make this client fetch
# without end). The owner's account (0) and PROFILE_CHARACTERS have no cap.
PER_ACCOUNT = 12
# "new": how often the character list is checked for a character never read (poll_new).
# "profile": every character, on the clock (owner, 2026-11-20: every 2 hours).
# Budget: 5 calls a character a pass (profile, appearance, equipment,
# specializations, achievements), one at a time, so 2.5 an hour per
# character against Blizzard's 36,000 an hour and 100 a second per client:
# the owner plus 10 friends at PER_ACCOUNT is ~130 characters, ~325 calls an
# hour, about 1% of the quota, and most answers are 304s.
INTERVALS = {"token": 1200, "realm": 300, "profile": 7200, "new": 300}
# Tasks that run on the clock (multiples of their interval since the epoch,
# UTC) instead of from the process' start.
ON_THE_CLOCK = ("profile",)
# Blizzard's Developer API Terms: API data is kept 30 days at most.
KEEP_S = 30 * 86400
PUSH_LABELS: list = []
PUSH_INTERVAL = 60
MERGE_INTERVAL = 86400
LISTEN = 9464
TIMEOUT = 60


def merge_url_for(push_url: str) -> str:
    """The force-merge endpoint of the VictoriaMetrics that VM_PUSH_URL writes to."""
    u = urllib.parse.urlsplit(push_url)
    return urllib.parse.urlunsplit((u.scheme, u.netloc, "/internal/force_merge", "", ""))


def push_target(push_url: str, extra_labels: list) -> str:
    """VM_PUSH_URL may already carry query args (extra_label=...): add ours
    with '&', never replace them."""
    if not extra_labels:
        return push_url
    q = urllib.parse.urlencode([("extra_label", x) for x in extra_labels])
    return push_url + ("&" if urllib.parse.urlsplit(push_url).query else "?") + q


def configure(env=None):
    global REGION, LOCALE, API, OAUTH, FOREVER_NS, PROFILE_NS, PROFILE_CHARS, PROFILE_CHARS_URL
    global PUSH_URL, MERGE_URL, MERGE_KEY_FILE
    global CLIENT_ID_FILE, CLIENT_SECRET_FILE, TOKEN_NAMESPACES
    global INTERVALS, PUSH_LABELS, PUSH_INTERVAL, MERGE_INTERVAL, LISTEN, TIMEOUT
    env = os.environ if env is None else env
    REGION = env.get("BNET_REGION", "eu")
    LOCALE = env.get("BNET_LOCALE", "en_GB")
    API = env.get("BNET_API_URL", f"https://{REGION}.api.blizzard.com")
    OAUTH = env.get("BNET_OAUTH_URL", "https://oauth.battle.net/token")
    CLIENT_ID_FILE = env.get("BNET_CLIENT_ID_FILE", "")
    CLIENT_SECRET_FILE = env.get("BNET_CLIENT_SECRET_FILE", "")
    if not CLIENT_ID_FILE or not CLIENT_SECRET_FILE:
        sys.exit("set BNET_CLIENT_ID_FILE and BNET_CLIENT_SECRET_FILE: files holding YOUR Battle.net API "
                 "client's id and secret (docs/self-host/battlenet.md)")
    # {"forever": "dynamic-forever-eu", "retail": "dynamic-eu"}
    TOKEN_NAMESPACES = json.loads(env.get("BNET_TOKEN_NAMESPACES") or json.dumps(
        {"forever": f"dynamic-forever-{REGION}", "retail": f"dynamic-{REGION}"}))
    FOREVER_NS = env.get("BNET_FOREVER_NAMESPACE", f"dynamic-forever-{REGION}")
    PROFILE_NS = env.get("BNET_PROFILE_NAMESPACE", f"profile-forever-{REGION}")
    PROFILE_CHARS = [c.strip() for c in env.get("PROFILE_CHARACTERS", "").split(",") if c.strip()]
    PROFILE_CHARS_URL = env.get("PROFILE_CHARACTERS_URL", "").strip()
    INTERVALS = {
        "token": int(env.get("TOKEN_INTERVAL", "1200")),
        "realm": int(env.get("REALM_INTERVAL", "300")),
        "profile": int(env.get("PROFILE_INTERVAL", "7200")),
        "new": 300,
    }
    PUSH_URL = env.get("VM_PUSH_URL", "")
    PUSH_LABELS = [x for x in env.get("VM_PUSH_EXTRA_LABELS", "").split(",") if x]
    PUSH_INTERVAL = int(env.get("VM_PUSH_INTERVAL", "60"))
    MERGE_KEY_FILE = env.get("VM_MERGE_KEY_FILE", "")
    MERGE_URL = env.get("VM_MERGE_URL") or (merge_url_for(PUSH_URL) if PUSH_URL and MERGE_KEY_FILE else "")
    MERGE_INTERVAL = int(env.get("VM_MERGE_INTERVAL", "86400"))
    LISTEN = int(env.get("LISTEN_PORT", "9464"))
    TIMEOUT = int(env.get("HTTP_TIMEOUT", "60"))


lock = threading.Lock()
# name -> {labels tuple -> (value, updated_at, task)}; game data, push only
data = {}
health = {
    "api_up": 0.0,
    "last_success": {},  # task -> unix time
    "requests": {},  # (endpoint, code) -> count
    "push_errors": 0,
    "merge_errors": 0,
    "last_push": 0.0,
}
cache = {}  # url -> (last_modified, parsed body)
pause_until = 0.0
backoff = 0
_token = {"value": "", "exp": 0.0}


def log(msg):
    print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), msg, flush=True)


class ApiError(Exception):
    def __init__(self, code, endpoint):
        super().__init__(f"{endpoint}: HTTP {code}")
        self.code = code


def read_file(path):
    with open(path) as f:
        return f.read().strip()


def oauth_token(force=False):
    """Client-credentials token, cached until 5 min before it expires (24 h)."""
    if not force and _token["value"] and time.time() < _token["exp"]:
        return _token["value"]
    cid = read_file(CLIENT_ID_FILE)
    csec = read_file(CLIENT_SECRET_FILE)
    req = urllib.request.Request(
        OAUTH,
        data=b"grant_type=client_credentials",
        headers={
            "Authorization": "Basic " + base64.b64encode(f"{cid}:{csec}".encode()).decode(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = json.load(r)
    except urllib.error.HTTPError as e:
        count("oauth", e.code)
        raise ApiError(e.code, "oauth") from None
    count("oauth", 200)
    _token["value"] = body["access_token"]
    _token["exp"] = time.time() + int(body.get("expires_in", 3600)) - 300
    return _token["value"]


def count(endpoint, code):
    with lock:
        k = (endpoint, str(code))
        health["requests"][k] = health["requests"].get(k, 0) + 1


def api_get(path, namespace, endpoint):
    """GET with the cached token; returns the parsed body (cached on 304) or
    None on 404. Raises ApiError for anything else that isn't 2xx."""
    global pause_until, backoff
    url = f"{API}{path}?{urllib.parse.urlencode({'locale': LOCALE})}"
    key = f"{namespace} {url}"
    for attempt in (0, 1):
        headers = {
            "Authorization": f"Bearer {oauth_token(force=attempt == 1)}",
            "Battlenet-Namespace": namespace,
            "Accept-Encoding": "gzip",
        }
        if key in cache:
            headers["If-Modified-Since"] = cache[key][0]
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                body = json.loads(raw)
                del raw
                cache[key] = (r.headers.get("Last-Modified") or formatdate(usegmt=True), body)
                count(endpoint, r.status)
                backoff = 0
                return body
        except urllib.error.HTTPError as e:
            count(endpoint, e.code)
            if e.code == 304 and key in cache:
                backoff = 0
                return cache[key][1]
            if e.code == 404:
                # The API answered: the endpoint just doesn't exist for this
                # game (Forever has no token or realms, 2026-11-18), or the
                # character isn't on the profile API yet.
                cache.pop(key, None)
                return None
            if e.code == 401 and attempt == 0:
                continue  # token revoked or expired early: fetch a new one
            if e.code == 429:
                ra = e.headers.get("Retry-After")
                backoff = min(backoff + 1, 6)
                wait = int(ra) if ra and ra.isdigit() else 60 * 2 ** (backoff - 1)
                pause_until = time.time() + min(wait, 3600)
                log(f"429 on {endpoint}: pausing {min(wait, 3600)}s")
            raise ApiError(e.code, endpoint) from None
    raise ApiError(401, endpoint)


def put(name, labels, value, task):
    with lock:
        data.setdefault(name, {})[tuple(sorted(labels.items()))] = (float(value), time.time(), task)


def drop(name, task, keep):
    """Forget series of `name` written by `task` that the last run didn't see
    (a ruleset that disappeared, a slot now empty)."""
    with lock:
        series = data.get(name, {})
        for k in [k for k, v in series.items() if v[2] == task and k not in keep]:
            del series[k]


def poll_token():
    seen = set()
    for game, ns in TOKEN_NAMESPACES.items():
        body = api_get("/data/wow/token/index", ns, "token")
        if body is None or "price" not in body:
            continue
        labels = {"region": REGION, "game": game}
        # price is in copper: 1 gold = 100 silver = 10,000 copper.
        put("wow_token_price_gold", labels, body["price"] / 10000, "token")
        put("wow_token_last_updated_timestamp_seconds", labels, body["last_updated_timestamp"] / 1000, "token")
        seen.add(tuple(sorted(labels.items())))
    drop("wow_token_price_gold", "token", seen)
    drop("wow_token_last_updated_timestamp_seconds", "token", seen)
    return f"{len(seen)} of {len(TOKEN_NAMESPACES)} games"


def poll_realm():
    index = api_get("/data/wow/ruleset/index", FOREVER_NS, "ruleset")
    if index is None:
        raise ApiError(404, "ruleset")
    up, pop = set(), set()
    for ref in index.get("rulesets", []):
        rs = api_get(f"/data/wow/ruleset/{ref['id']}", FOREVER_NS, "ruleset")
        if rs is None:
            continue
        name = rs.get("name") or str(ref["id"])
        labels = {"realm": name, "region": REGION, "ruleset_id": str(rs.get("id", ref["id"]))}
        put("wow_realm_up", labels, 1 if rs.get("status", {}).get("type") == "UP" else 0, "realm")
        up.add(tuple(sorted(labels.items())))
        # Retail connected realms carry population.type (LOW..FULL); Forever's
        # rulesets have no population field (checked live 2026-11-18). Kept
        # so the series appears by itself if Blizzard adds it.
        ptype = (rs.get("population") or {}).get("type")
        if ptype:
            pl = {"realm": name, "region": REGION, "population": ptype}
            put("wow_realm_population", pl, 1, "realm")
            pop.add(tuple(sorted(pl.items())))
    drop("wow_realm_up", "realm", up)
    drop("wow_realm_population", "realm", pop)
    return f"{len(up)} rulesets"


def char_slug(name: str) -> str:
    """Forever's profile key: `{name}-{surname}` lowercased, URL-encoded (no
    realm: Forever has none)."""
    return urllib.parse.quote(name.lower(), safe="-")


PROFILE_SERIES = ("wow_character_profile", "wow_character_customization", "wow_character_equipped",
                  "wow_character_profile_refreshed_timestamp_seconds", "wow_character_item_level",
                  # Each worn item's stats, as the armory shows them: a random
                  # suffix's ("of the Owl") are in no item table (gear advice).
                  "wow_character_equipped_stat",
                  # Talents (/specializations): the active group's points per tree and ranks.
                  "wow_character_talent_points", "wow_character_talent_rank",
                  # Completed achievements (/achievements): the Legacy points'
                  # challenges are achievements (wowstats.talents.legacy).
                  "wow_character_achievement",
                  # How many it completed, written only when /achievements
                  # answered: without it the Legacy count is unknown, not 0.
                  "wow_character_achievements_read",
                  # When each part (PARTS) last answered (review 3: a 503 on
                  # /equipment once left the whole character marked fresh).
                  "wow_character_part_refreshed_timestamp_seconds")

# What each Battle.net call writes. A part replaces its own series only when
# its call answered; a failed call keeps what the part had, with its own
# time, and the character's refreshed series is the oldest part's time.
PARTS = {"profile": ("wow_character_profile", "wow_character_item_level"),
         "appearance": ("wow_character_customization",),
         "equipment": ("wow_character_equipped", "wow_character_equipped_stat"),
         "specializations": ("wow_character_talent_points", "wow_character_talent_rank"),
         "achievements": ("wow_character_achievement", "wow_character_achievements_read")}
PART_TS = "wow_character_part_refreshed_timestamp_seconds"
REFRESHED = "wow_character_profile_refreshed_timestamp_seconds"
# Which of a character's series the exporter holds now, as one label on each
# (`snapshot`, a hash of the series' names and labels) and one marker series
# per character (SNAPSHOT, its value when that set became current). The
# readers keep the newest marker's set only: a talent dropped in a respec or
# an emptied slot is gone at once, not after their 2-day window (review 3).
SNAPSHOT = "wow_character_snapshot"
snapshots: dict[tuple, tuple[str, float]] = {}  # (account_id, character) -> (snapshot, since)


def talent_readings(body) -> tuple[list[dict], dict[int, tuple[int, str]]]:
    """The active talent group of a /specializations answer: its trees
    [{tree, points}] in the order given and {spell id: (rank, talent name)}.
    Forever's shape (read live 2026-11-18, a level 32 priest): no
    active_specialization; specialization_groups[] {is_active,
    specializations[] {specialization_name, spent_points, talents[]
    {talent {name, id}, spell_tooltip {spell {name, id}}, talent_rank}}}."""
    trees: list[dict] = []
    ranks: dict[int, tuple[int, str]] = {}
    groups = body.get("specialization_groups") if isinstance(body, dict) else None
    group = next((g for g in groups or [] if isinstance(g, dict) and g.get("is_active")), None)
    for sp in (group or {}).get("specializations") or []:
        if not isinstance(sp, dict) or not isinstance(sp.get("specialization_name"), str):
            continue
        pts = sp.get("spent_points")
        trees.append({"tree": sp["specialization_name"], "points": pts if isinstance(pts, int) else 0})
        for t in sp.get("talents") or []:
            spell = ((t.get("spell_tooltip") or {}).get("spell") or {}) if isinstance(t, dict) else {}
            rank = t.get("talent_rank") if isinstance(t, dict) else None
            if isinstance(spell.get("id"), int) and isinstance(rank, int) and rank > 0:
                name = ((t.get("talent") or {}).get("name") or spell.get("name") or "")
                ranks[spell["id"]] = (rank, str(name))
    return trees, ranks


def completed_achievements(body) -> dict[int, float]:
    """{achievement id: completed at (epoch seconds)} from an /achievements
    answer: achievements[] {id, achievement {id}, completed_timestamp (ms)}.
    Without a timestamp an entry counts only when its criteria say
    is_completed (then "now": when is unknown)."""
    out: dict[int, float] = {}
    for a in (body.get("achievements") if isinstance(body, dict) else None) or []:
        if not isinstance(a, dict):
            continue
        aid = a.get("id") if isinstance(a.get("id"), int) else (a.get("achievement") or {}).get("id")
        if not isinstance(aid, int):
            continue
        ts = a.get("completed_timestamp")
        if isinstance(ts, (int, float)) and ts > 0:
            out[aid] = ts / 1000 if ts > 1e11 else float(ts)
        elif (a.get("criteria") or {}).get("is_completed") is True:
            out[aid] = time.time()
    return out


def forget_old(now=None):
    """Drop every profile series not refreshed for 30 days (Blizzard's cap),
    whatever happened to the character since."""
    now = now or time.time()
    with lock:
        for name in (*PROFILE_SERIES, "wow_character_profile_found"):
            series = data.get(name, {})
            for k in [k for k, v in series.items() if now - v[1] > KEEP_S]:
                del series[k]


def whose(key: tuple) -> tuple:
    """(account_id, character) of a series' labels tuple."""
    lab = dict(key)
    return lab.get("account_id"), lab.get("character")


def commit(who: dict, part: str, rows: list[tuple[str, dict, float]]) -> None:
    """One part's answer: its series become `rows` (the ones it no longer
    has go), and the part's time is now."""
    me = (who["account_id"], who["character"])
    seen = {(n, tuple(sorted(lab.items()))) for n, lab, _v in rows}
    for n, lab, v in rows:
        put(n, lab, v, "profile")
    put(PART_TS, {**who, "part": part}, time.time(), "profile")
    with lock:
        for n in PARTS[part]:
            series = data.get(n, {})
            for k in [k for k in series if whose(k) == me and (n, k) not in seen]:
                del series[k]


def as_of(who: dict) -> None:
    """The character's refreshed series: the oldest part's last answer, once
    every part has answered at least once (since the exporter started)."""
    me = (who["account_id"], who["character"])
    with lock:
        at = {dict(k).get("part"): v[0] for k, v in data.get(PART_TS, {}).items() if whose(k) == me}
    if set(at) >= set(PARTS):
        put(REFRESHED, who, min(at[p] for p in PARTS), "profile")


def forget_character(me: tuple, keep_found: bool = True) -> None:
    with lock:
        for n in PROFILE_SERIES if keep_found else (*PROFILE_SERIES, "wow_character_profile_found"):
            series = data.get(n, {})
            for k in [k for k in series if whose(k) == me]:
                del series[k]


NAME_RE = re.compile(r"^[^\x00-\x1f\"\\/]{1,64}$")


def profile_chars() -> list[tuple[str, str]]:
    """(account_id, name) pairs: PROFILE_CHARACTERS as account 0, plus the
    full view's list (PROFILE_CHARACTERS_URL), "<account_id>\t<name>" per
    line, at most PER_ACCOUNT per friend. A name is read for one account
    only, the first that lists it (the full view gives a name claimed by
    two people to the owner or to nobody: wowstats.tenancy). The list down:
    the last answer is not kept, the env list alone goes this pass (a
    character missed today is read tomorrow)."""
    out = [("0", n) for n in dict.fromkeys(PROFILE_CHARS)]
    taken = {n for _, n in out}
    if PROFILE_CHARS_URL:
        per: dict[str, int] = {}
        try:
            from wowstats import guard

            with guard.share_open(PROFILE_CHARS_URL) as r:
                for line in r.read(1_000_000).decode("utf-8", "replace").splitlines():
                    acct, _, n = line.strip().partition("\t")
                    if not acct.isdigit() or not NAME_RE.match(n) or n in taken:
                        continue
                    acct = str(int(acct))
                    if acct != "0" and per.get(acct, 0) >= PER_ACCOUNT:
                        continue
                    per[acct] = per.get(acct, 0) + 1
                    taken.add(n)
                    out.append((acct, n))
        except OSError as e:
            log(f"profile characters from {urllib.parse.urlsplit(PROFILE_CHARS_URL).hostname}: {e}")
    return out


def read_character(acct: str, name: str) -> bool:
    """One character's profile, appearance, equipment, talents and
    achievements into `data`, one part at a time (commit): an ApiError
    leaves the parts not read yet as they were, with their own time. The
    look is option/choice ids, the ones the 3D view's recipe reads
    (character3d). False: Battle.net doesn't know it (yet)."""
    who = {"character": name, "account_id": acct}  # on every series: whose character it is
    path = f"/profile/wow/character/{char_slug(name)}"
    prof = api_get(path, PROFILE_NS, "profile")
    put("wow_character_profile_found", who, 1 if prof else 0, "profile")
    if not prof:
        forget_character((acct, name))  # not on Battle.net (yet): only "not found" stays
        return False
    look = api_get(f"{path}/appearance", PROFILE_NS, "profile_appearance") or {}
    race = (prof.get("race") or look.get("playable_race") or {}).get("id")
    gender = ((prof.get("gender") or look.get("gender") or {}).get("type") or "").lower()
    # The guild's name, from the profile (owner, 2026-11-20: Battle.net,
    # not an addon); none when the character has no guild.
    guild = (prof.get("guild") or {}).get("name") if isinstance(prof.get("guild"), dict) else None
    in_guild = isinstance(guild, str) and bool(guild)
    # in_guild says the profile was read for it: a series without it
    # (an older exporter) leaves the guild unknown, not "none".
    # Class and faction too: the page fills them from here only when the
    # addons sent none (a friend with level splits only).
    cls = (prof.get("character_class") or {}).get("id") if isinstance(prof.get("character_class"), dict) else None
    side = ((prof.get("faction") or {}).get("type") or "").lower() if isinstance(prof.get("faction"), dict) else ""
    labels = {**who, "race_id": str(race or ""), "gender": gender, "in_guild": str(in_guild).lower(),
              "class_id": str(cls or ""), "faction": side, **({"guild": str(guild)} if in_guild else {})}
    rows = [("wow_character_profile", labels, prof.get("level") or 0)]
    # The armory's item level: Blizzard's own count (every slot, an
    # empty one 0), which the page shows next to its own average.
    for kind in ("equipped", "average"):
        v = prof.get(f"{kind}_item_level")
        if isinstance(v, (int, float)):
            rows.append(("wow_character_item_level", {**who, "kind": kind}, v))
    commit(who, "profile", rows)
    rows = []
    for c in look.get("customizations") or []:
        opt, choice = (c.get("option") or {}).get("id"), (c.get("choice") or {}).get("id")
        if opt and choice:
            labels = {**who, "option_id": str(opt)}
            rows.append(("wow_character_customization", labels, choice))
    commit(who, "appearance", rows)
    # No /character-media: Blizzard serves its renders on paths shared by
    # character id across the Classic games, and one showed a stranger in
    # the owner's place (2026-11-21). The page's picture is our own render
    # of the 3D model (wowstats.armory).
    gear = api_get(f"{path}/equipment", PROFILE_NS, "profile_equipment") or {}
    rows = []
    for it in gear.get("equipped_items") or []:
        slot, item = (it.get("slot") or {}).get("type"), (it.get("item") or {}).get("id")
        if slot and item:
            labels = {**who, "slot": slot.lower()}
            rows.append(("wow_character_equipped", labels, item))
            for st in it.get("stats") or []:
                kind, v = ((st or {}).get("type") or {}).get("type"), (st or {}).get("value")
                if kind and isinstance(v, (int, float)) and not st.get("is_negated"):
                    sl = {**labels, "stat": str(kind)}
                    rows.append(("wow_character_equipped_stat", sl, v))
    commit(who, "equipment", rows)
    # Talents for the card, the spec and the meta score: the only source
    # (the game saves none; Altoholic keeps only the class name).
    trees, ranks = talent_readings(api_get(f"{path}/specializations", PROFILE_NS, "profile_specializations"))
    rows = []
    for i, t in enumerate(trees):
        labels = {**who, "tree": t["tree"], "order": str(i)}
        rows.append(("wow_character_talent_points", labels, t["points"]))
    for sp, (rk, tname) in ranks.items():
        labels = {**who, "spell_id": str(sp), "talent": tname}
        rows.append(("wow_character_talent_rank", labels, rk))
    commit(who, "specializations", rows)
    # Completed achievements: the Legacy points (owner, 2026-11-20).
    body = api_get(f"{path}/achievements", PROFILE_NS, "profile_achievements")
    got = completed_achievements(body)
    rows = [("wow_character_achievements_read", who, len(got))] if isinstance(body, dict) else []
    for aid, at in got.items():
        labels = {**who, "achievement_id": str(aid)}
        rows.append(("wow_character_achievement", labels, at))
    commit(who, "achievements", rows)
    # When Blizzard's answers were read: the page says how old the look is,
    # and the series is pushed every minute whatever its age.
    as_of(who)
    return True


def read_profiles(chars, label):
    """A failure on one character still lets the others refresh. Keep the
    scheduler's failure signal, including the global backoff on a 429."""
    found = failed = 0
    for acct, name in chars:
        try:
            found += read_character(acct, name)
        except (ApiError, OSError, ValueError, KeyError) as e:
            if isinstance(e, ApiError) and e.code == 429:
                raise
            failed += 1
            log(f"profile read failed: {e}")
    summary = f"{found} of {len(chars)} {label}"
    if failed:
        raise ProfileReadError(f"{summary}, {failed} failed")
    return summary


class ProfileReadError(Exception):
    """One or more character reads failed in this pass."""


def poll_profile():
    """Every listed character, every PROFILE_INTERVAL on the clock; what the
    run didn't see goes, and anything not refreshed for 30 days."""
    chars = profile_chars()
    # A character no longer listed goes whole.
    with lock:
        gone = {whose(k) for n in (*PROFILE_SERIES, "wow_character_profile_found")
                for k in data.get(n, {})} - set(chars)
    for me in gone:
        forget_character(me, keep_found=False)
    forget_old()
    return read_profiles(chars, "characters")


def never_read(acct: str, name: str) -> bool:
    """No profile read for this character since the exporter started (or
    since the daily pass forgot it)."""
    with lock:
        key = (("account_id", acct), ("character", name))
        found = data.get("wow_character_profile_found", {}).get(key)
        return found is None or (bool(found[0]) and key not in data.get(REFRESHED, {}))


def poll_new():
    """A character the list names for the first time is read now, not at
    the next pass: a friend who installs at 09:30 has their Battle.net gear,
    look and talents a few minutes later. One read each (a 404 counts: it
    waits for the next pass), one call at a time like the passes; the
    passes and the 30-day rule are unchanged."""
    chars = [(a, n) for a, n in profile_chars() if never_read(a, n)]
    return read_profiles(chars, "new characters")


TASKS = {"token": poll_token, "realm": poll_realm, "profile": poll_profile, "new": poll_new}


def esc(v):
    return str(v).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def fmt(name, labels, value):
    lbl = ",".join(f'{k}="{esc(v)}"' for k, v in labels)
    return f"{name}{{{lbl}}} {value!r}\n" if lbl else f"{name} {value!r}\n"


def render_health():
    with lock:
        out = [fmt("wow_api_up", (), health["api_up"])]
        for task, ts in sorted(health["last_success"].items()):
            out.append(fmt("wow_exporter_last_success_timestamp_seconds", (("task", task),), ts))
        for (ep, code), c in sorted(health["requests"].items()):
            out.append(fmt("wow_api_requests_total", (("code", code), ("endpoint", ep)), float(c)))
        out.append(fmt("wow_exporter_push_errors_total", (), float(health["push_errors"])))
        out.append(fmt("wow_exporter_merge_errors_total", (), float(health["merge_errors"])))
        out.append(fmt("wow_exporter_last_push_timestamp_seconds", (), health["last_push"]))
        out.append(fmt("wow_api_paused", (), 1.0 if time.time() < pause_until else 0.0))
    return "".join(out)


def snapshot_ids() -> dict[tuple, str]:
    """{(account_id, character): snapshot} for every character with profile
    series: a hash of the names and labels it holds now (not the values, so
    a refresh that changes nothing but numbers keeps the label and vm-wow's
    series). A character whose set changed gets a new `since`. Under lock."""
    sets: dict[tuple, list] = {}
    for n in PROFILE_SERIES:
        for k in data.get(n, {}):
            sets.setdefault(whose(k), []).append((n, k))
    out = {}
    for me, keys in sets.items():
        sid = hashlib.sha256(json.dumps(sorted(keys)).encode()).hexdigest()[:12]
        if snapshots.get(me, ("", 0.0))[0] != sid:
            snapshots[me] = (sid, time.time())
        out[me] = sid
    for me in [m for m in snapshots if m not in sets]:
        del snapshots[me]
    return out


def render_data(now=None):
    """Game data still fresh: a value is pushed for up to 3 polling intervals
    after its last successful read, then dropped, so a dead API shows as a
    gap instead of a flat line. A character's profile series carry their
    `snapshot`, and its SNAPSHOT marker is pushed whatever their age."""
    now = now or time.time()
    out = []
    profile = set(PROFILE_SERIES)
    with lock:
        sids = snapshot_ids()
        for name in sorted(data):
            for labels, (value, ts, task) in sorted(data[name].items()):
                if now - ts <= 3 * INTERVALS[task]:
                    if name in profile and whose(labels) in sids:
                        labels = tuple(sorted((*labels, ("snapshot", sids[whose(labels)]))))
                    out.append(fmt(name, labels, value))
        for (acct, char), sid in sorted(sids.items()):
            out.append(fmt(SNAPSHOT, (("account_id", acct), ("character", char), ("snapshot", sid)),
                           snapshots[(acct, char)][1]))
    return "".join(out)


def push():
    body = (render_data() + render_health()).encode()
    req = urllib.request.Request(push_target(PUSH_URL, PUSH_LABELS), data=body, method="POST",
                                 headers={"Content-Type": "text/plain"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
        with lock:
            health["last_push"] = time.time()
    except (urllib.error.URLError, OSError) as e:
        with lock:
            health["push_errors"] += 1
        log(f"push failed: {e}")


def force_merge():
    """Rewrite this month's and last month's partitions: a merge drops the
    blocks that are past the retention (otherwise they stay on disk until
    their whole part is, which can be weeks)."""
    key = read_file(MERGE_KEY_FILE)
    t = time.gmtime()
    prev = time.gmtime(time.mktime((t.tm_year, t.tm_mon, 1, 0, 0, 0, 0, 0, 0)) - 86400)
    for y, m in ((prev.tm_year, prev.tm_mon), (t.tm_year, t.tm_mon)):
        # authKey in the form body, not the URL: URLs end up in logs.
        form = urllib.parse.urlencode({"authKey": key, "partition_prefix": f"{y:04d}_{m:02d}"}).encode()
        req = urllib.request.Request(MERGE_URL, data=form, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                r.read()
        except (urllib.error.URLError, OSError) as e:
            with lock:
                health["merge_errors"] += 1
            log(f"force_merge {y:04d}_{m:02d} failed: {e}")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            body, ctype = render_health().encode(), "text/plain; version=0.0.4"
        elif self.path == "/healthz":
            body, ctype = b"ok\n", "text/plain"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def next_slot(name, now):
    """When `name` runs next: on the clock (the next multiple of its interval
    since the epoch, so 2 h is 00:00, 02:00... UTC) for ON_THE_CLOCK, else
    one interval from now."""
    every = INTERVALS[name]
    return (now // every + 1) * every if name in ON_THE_CLOCK else now + every


def run_task(name):
    try:
        summary = TASKS[name]()
        with lock:
            health["last_success"][name] = time.time()
            health["api_up"] = 1.0
        log(f"{name}: ok ({summary})")
        return True
    except ApiError as e:
        with lock:
            health["api_up"] = 0.0
        log(f"{name}: {e}")
    except (OSError, ValueError, KeyError, ProfileReadError) as e:
        with lock:
            health["api_up"] = 0.0
        log(f"{name}: {type(e).__name__}: {e}")
    return False


def main():
    configure()
    if not PUSH_URL:
        log("VM_PUSH_URL is empty: the game data stays in memory, nothing is stored")
    if not PROFILE_CHARS and not PROFILE_CHARS_URL:
        log("PROFILE_CHARACTERS is empty: no appearance from Battle.net")
    server = ThreadingHTTPServer(("", LISTEN), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log(f"listening on :{LISTEN}, region {REGION}")
    # The clock tasks wait for their slot: poll_new reads every character
    # within its first run anyway (nothing is read yet after a start).
    due = {name: next_slot(name, time.time()) if name in ON_THE_CLOCK else 0.0 for name in TASKS}
    fails = {name: 0 for name in TASKS}
    next_push = time.time() + 10
    next_merge = time.time() + 600
    while True:
        now = time.time()
        if now >= pause_until:
            for name in TASKS:
                if now >= due[name]:
                    if run_task(name):
                        fails[name] = 0
                        due[name] = next_slot(name, time.time())
                    else:
                        # Retry sooner than the full interval, but back off:
                        # 1, 2, 4... min, never past the next slot.
                        fails[name] += 1
                        due[name] = min(next_slot(name, time.time()),
                                        time.time() + min(INTERVALS[name], 60 * 2 ** (fails[name] - 1)))
                    if time.time() < pause_until:
                        break
        if PUSH_URL and time.time() >= next_push:
            push()
            next_push = time.time() + PUSH_INTERVAL
        if MERGE_URL and MERGE_KEY_FILE and time.time() >= next_merge:
            force_merge()
            next_merge = time.time() + MERGE_INTERVAL
        time.sleep(1)
