"""Several people on one install: who is asking, and which data is theirs.

One process serves everyone. The login in front of the full view (an OIDC
proxy) and the MCP's own OAuth say who a request is (the `sub`); this module
turns that into the person's own `ui.App`, built from a copy of the
environment with their store, their VictoriaLogs tenant and their state
folder (`env_for`). Nothing else changes per person: every view, the API,
the MCP tools and the publisher work on that App and see that person's
events only.

Isolation is the store's: each person is a VictoriaLogs tenant (AccountID),
and every request to VictoriaLogs carries it (wowstats.stores.tenant_headers),
so a query for one tenant can't read another's rows whatever its text. The
owner is tenant 0 on VL_URL, as before; friends are tenants 1..n on
FRIENDS_VL_URL, a store of their own that can delete (wowstats.tenancy.delete).
With EVENTS_STORE=sqlite:<folder> each person is a file of their own in that
folder (events-<account id>.sqlite; wowstats.eventstore), friends included:
no FRIENDS_VL_URL, and a deletion removes the file.

Who gets in (identify, the web view and the MCP alike): one of the owner's
subs, or a sub on WOW_FRIENDS_FILE ({"<sub>": "<slug>"}, {"<sub>": ""} or ["<sub>"],
rendered by the host from its identity provider's friends group). Nothing
else: no org, role or name claim decides who gets in, so a proxy that drops
a header can't lock anyone out or let anyone in (2026-11-19: the org header
arrived empty and refused everyone). Without the file, only the owner.

A listed person without a slug gets one at their first web login, derived
from their BattleTag (PROXY_BATTLETAG_HEADER, the login proxy's copy of the
claim): the name part, then a keyed hash of the whole tag (derived_slugs).
The tag is read at that moment only and never kept; the slug is. An explicit
slug in the list wins, a slug already given never changes, and no BattleTag
leaves the person in without an address until the list names them. The file is watched while the server
runs (FriendsWatch: its content checked every WATCH_S, a full pass every
FRIENDS_RECONCILE_S anyway), so a changed list applies without a restart:
a listed person gets a tenant with that slug before their first login (or
at it, if they're quicker); a person who left the list loses every access
at once (status "removed": no login, no MCP, no upload, no site) and their
data is deleted FRIENDS_PURGE_DAYS later (delete_tenant), unless the list
names them again before that: then they're back with everything (owner,
2026-11-22: a valid but wrong list would otherwise wipe people for good).
A valid list that would remove every friend at once is refused
(FRIENDS_ALLOW_EMPTY, or `users reconcile --allow-empty`, lets it through),
and a missing or broken file changes nothing. Each person: an AccountID, a state
folder, a slug, an ingest token, a zogzog key, all in
WOW_STATE_DIR/users.json (0600). Tokens and install codes are
kept as SHA-256 hashes only; zogzog keys are never stored: each is derived
from ZOGZOG_MASTER_KEY_FILE with the account id and a generation.

Environment (docs/deploy.md, "Friends"):
  WOW_STATE_DIR        the registry and each person's state (users/<account>/)
  WOW_OWNER_SUB        the owner's `sub`: tenant 0 on VL_URL. Comma-separated for
                       an owner with several logins (a second account linked to
                       Battle.net): each one is the owner, one tenant, one App.
                       A sub also on the friends list is the owner's.
  WOW_OWNER_SLUG       the owner's slug (its site name); WOW_OWNER_ALIASES: more hosts for it
  WOW_FRIENDS_FILE     who may use it besides the owner, {"<sub>": "<slug>" or ""}
                       or ["<sub>"], watched for changes; FRIENDS_RECONCILE_S (120)
                       a full pass
  FRIENDS_PURGE_DAYS   days between leaving the list and the deletion of the
                       person's data (7; 0: at once). The clock is the
                       registry's removed_at, so a restart neither resets nor skips it
  FRIENDS_ALLOW_EMPTY  1: apply a list that removes every friend at once
                       (refused otherwise, logged as empty_list_refused)
  PROXY_BATTLETAG_HEADER  the login's BattleTag claim, for a friend's first slug
                       (unset: slugs come from the list only)
  FRIENDS_VL_URL       the friends' VictoriaLogs; FRIENDS_VL_DELETE_KEY_FILE its
                       -deleteAuthKey (not used with EVENTS_STORE=sqlite:...)
  ZOGZOG_MASTER_KEY_FILE  32 random bytes, base64url: friends' zogzog keys
  PUBLIC_URL           the full view's address (install commands, the shipper's url):
                       https, or http on 127.0.0.1/localhost only
  SITE_DOMAIN          friends' sites are <slug>-wow.<SITE_DOMAIN>
  PROXY_USER_HEADER    the login's sub (X-Forwarded-User)
  INGEST_DAILY_MB      what /ingest takes from one person a day (512; 0: no limit)
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import threading
import time
import unicodedata
import urllib.parse
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

# 2 to 24 of a-z and 0-9, with at most one inner hyphen: a derived slug is
# <name>-<hash> (derived_slugs), a hand-picked one is usually one word.
SLUG_RE = re.compile(r"^(?=.{2,24}$)[a-z0-9]+(?:-[a-z0-9]+)?$")
SLUG_RULE = "2 to 24 lowercase letters and digits, with at most one hyphen inside"
# Names a slug can't take: hosts and paths of the install, and words that
# would read as the site's own.
RESERVED = frozenset({"full", "www", "api", "admin", "root", "static", "mcp", "oauth", "install", "start", "help",
                      "settings", "privacy", "wow", "zogzog", "public", "login", "logout", "owner", "test"})
CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
CODE_LEN = 10
CODE_TTL = 15 * 60
CODE_RE = re.compile(rf"^[{CODE_ALPHABET}]{{{CODE_LEN}}}$")
TOKEN_MIN = 32


# How the app names whoever runs it, to a friend. Not the owner's slug
# capitalised: that printed "Sam" (or "Wow42") on every self-hosted install
# (owner, 2026-11-19), and the app has no display-name setting.
RUNS_IT = "the person who runs this site"


class TenancyError(Exception):
    """A request the person can fix (a taken slug); the message is for them."""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


@dataclass
class User:
    sub: str
    account_id: int
    org: str = ""  # unused (admission is the friends list): old records keep it
    owner: bool = False
    slug: str = ""
    created: str = ""
    # active | disabled (`users disable`: the operator's switch) | removed (off
    # the friends list, data kept until removed_at + FRIENDS_PURGE_DAYS).
    status: str = "active"
    public_on: bool = False
    hidden_public: list = field(default_factory=list)
    hidden_zogzog: list = field(default_factory=list)
    # Older registries wrote bnet_to_model=True even at signup. Only the
    # Settings endpoint records a choice under this new field.
    bnet_to_model_choice: bool | None = None
    # The 3D view's models: "hd" (Updated) or "sd" (Classic), the page's
    # default for everyone who opens it; a visitor's own toggle wins there.
    models: str = "hd"
    ingest_sha256: str = ""
    ingest_set_at: str = ""
    zogzog_gen: int = 0
    first_event: str = ""
    # When they left the friends list (ISO, UTC), "" while they're on it:
    # the purge clock, kept in users.json so a restart doesn't reset it.
    removed_at: str = ""

    @property
    def active(self) -> bool:
        return self.status == "active"

    @property
    def bnet_to_model(self) -> bool:
        return self.bnet_to_model_choice is True

    def public(self) -> dict:
        """What the publisher and the host list may know: no hashes."""
        return {"slug": self.slug, "account_id": self.account_id, "owner": self.owner, "status": self.status,
                "public_on": self.public_on, "hidden_public": list(self.hidden_public),
                "hidden_zogzog": list(self.hidden_zogzog), "zogzog_gen": self.zogzog_gen, "models": self.models}


def _lock_fd(fd: int, on: bool) -> None:
    """flock, or its Windows twin: the app also runs outside Docker, and
    the Windows CI job runs it on Windows (tests/test_windows_e2e.py)."""
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        if not on:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            return
        while True:  # LK_LOCK gives up after 10 tries, a second apart
            try:
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                return
            except OSError:
                continue
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_EX if on else fcntl.LOCK_UN)


class _FileLock:
    """An flock on users.json.lock: the full view and `wow-stats users`
    (docker or kubectl exec into the same container) both write the registry."""

    def __init__(self, reg: Registry):
        self.reg = reg

    def __enter__(self):
        self.reg.lock.acquire()
        self.reg._depth += 1
        if self.reg._depth == 1:  # re-entrant: set_slug calls update
            self.reg.path.parent.mkdir(parents=True, exist_ok=True)
            self.reg._fd = os.open(self.reg.path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT, 0o600)
            _lock_fd(self.reg._fd, True)
            self.reg._mtime = None  # read again under the lock: another process may have written
        return self

    def __exit__(self, *exc):
        self.reg._depth -= 1
        if self.reg._depth == 0:
            _lock_fd(self.reg._fd, False)
            os.close(self.reg._fd)
        self.reg.lock.release()


class Registry:
    """WOW_STATE_DIR/users.json, read again whenever it changed on disk."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self._data: dict | None = None
        self._mtime: int | None = None
        self._depth = 0
        self._fd = -1
        self.reserved: frozenset = frozenset()

    def writing(self) -> _FileLock:
        return _FileLock(self)

    def _load(self) -> dict:
        try:
            m = self.path.stat().st_mtime_ns
        except FileNotFoundError:
            m = -1
        if self._data is None or m != self._mtime:
            try:
                self._data = json.loads(self.path.read_text())
            except FileNotFoundError:
                self._data = {"version": 1, "next_account": 1, "users": {}, "codes": {}}
            self._mtime = m
        return self._data

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self._data, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)
        self._mtime = self.path.stat().st_mtime_ns

    @staticmethod
    def _user(d: dict) -> User:
        known = User.__dataclass_fields__
        return User(**{k: v for k, v in d.items() if k in known})

    def users(self) -> list[User]:
        with self.lock:
            return sorted((self._user(u) for u in self._load()["users"].values()), key=lambda u: u.account_id)

    def get(self, sub: str) -> User | None:
        with self.lock:
            d = self._load()["users"].get(sub)
            return self._user(d) if d else None

    def by_slug(self, slug: str) -> User | None:
        return next((u for u in self.users() if u.slug and u.slug == slug), None)

    def ensure(self, sub: str, owner: bool = False, slug: str = "", public_on: bool = False) -> User:
        """The person's record, made at their first request. The owner is
        tenant 0; everyone else gets the next AccountID, never reused, so a
        deleted person's leftovers can't surface in a new one's tenant."""
        with self.writing():
            data = self._load()
            d = data["users"].get(sub)
            if d is None:
                if owner:
                    acct = 0
                else:
                    acct = int(data.get("next_account", 1))
                    data["next_account"] = acct + 1
                d = asdict(User(sub=sub, account_id=acct, owner=owner, slug=slug, created=now_iso(),
                                public_on=public_on))
                data["users"][sub] = d
                self._save()
            return self._user(d)

    def update(self, sub: str, **fields) -> User:
        with self.writing():
            d = self._load()["users"].get(sub)
            if d is None:
                raise KeyError(sub)
            d.update(fields)
            self._save()
            return self._user(d)

    def set_slug(self, sub: str, slug: str) -> User:
        slug = (slug or "").strip().lower()
        if not SLUG_RE.match(slug):
            raise TenancyError(SLUG_RULE)
        if slug in self.reserved:  # the owner's own names: someone has it
            raise TenancyError(f"'{slug}' is taken")
        if slug in RESERVED:
            raise TenancyError(f"'{slug}' is reserved")
        with self.writing():
            u = self.get(sub)
            if u is None:
                raise KeyError(sub)
            if u.slug:
                if u.slug == slug:
                    return u
                raise TenancyError("your name is already set; it can't change (it is your site's address)")
            other = self.by_slug(slug)
            if other is not None or slug in self.reserved:
                raise TenancyError(f"'{slug}' is taken")
            return self.update(sub, slug=slug)

    def remove(self, sub: str) -> None:
        with self.writing():
            data = self._load()
            data["users"].pop(sub, None)
            data["codes"] = {k: v for k, v in data["codes"].items() if v.get("sub") != sub}
            self._save()

    # ------------------------------------------------------------ install codes and ingest tokens

    def new_code(self, sub: str) -> str:
        """A one-time install code (15 minutes): the installer it fetches
        carries a fresh ingest token, so a code is a credential. Kept hashed;
        one live code per person."""
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
        with self.writing():
            data = self._load()
            t = time.time()
            data["codes"] = {k: v for k, v in data["codes"].items() if v["exp"] > t and v["sub"] != sub}
            data["codes"][sha256(code)] = {"sub": sub, "exp": int(t) + CODE_TTL}
            self._save()
        return code

    def redeem(self, code: str) -> tuple[User, str] | None:
        """The person and a new ingest token for a valid code, once; None
        otherwise. The old token stops working: one shipper per person."""
        if not CODE_RE.match(code or ""):
            return None
        with self.writing():
            data = self._load()
            hit = data["codes"].pop(sha256(code), None)
            if hit is None or hit["exp"] < time.time():
                self._save()
                return None
            u = data["users"].get(hit["sub"])
            if u is None or u.get("status") != "active" or not u.get("slug"):
                self._save()
                return None
            token = secrets.token_urlsafe(TOKEN_MIN)
            u["ingest_sha256"] = sha256(token)
            u["ingest_set_at"] = now_iso()
            self._save()
            return self._user(u), token

    def check_ingest(self, slug: str, password: str) -> User | None:
        u = self.by_slug(slug)
        if u is None or not u.active or not u.ingest_sha256 or len(password) < TOKEN_MIN:
            return None
        return u if hmac.compare_digest(sha256(password), u.ingest_sha256) else None


def zogzog_key(master: bytes, account_id: int, gen: int) -> bytes:
    """A friend's zogzog key: never stored, derived again by whoever holds
    the master (the full view shows the link, the publisher seals with it).
    A new generation is a new key (old links stop working)."""
    return hmac.new(master, f"zogzog|{int(account_id)}|{int(gen)}".encode(), hashlib.sha256).digest()


def person_key(env: dict, account_id: int, gen: int, owner: bool) -> bytes | None:
    """A person's /zogzog/ key, the same in the full view (the link) and the
    publisher (the seal). The owner's generation 0 is ZOGZOG_KEY_FILE (his
    link from before multi-user keeps working); every other generation, his
    included, is derived from the master: "Make a new link" gave the owner
    the same link back while his key came from the file whatever the
    generation (outside review, 2026-11-19). None: no key to be had."""
    if owner and int(gen) == 0:
        kf = (env.get("ZOGZOG_KEY_FILE") or "").strip()
        try:
            raw = Path(kf).read_text().strip() if kf else ""
        except OSError:
            raw = ""
        if not raw:
            return None
        key = unb64u(raw)
        if len(key) != 32:  # as publish.read_key: else the link opens nothing the publisher sealed
            raise SystemExit(f"{kf}: not a 256-bit key (43 base64url characters)")
        return key
    master = read_master(env)
    return zogzog_key(master, account_id, gen) if master else None


def read_master(env: dict) -> bytes | None:
    p = (env.get("ZOGZOG_MASTER_KEY_FILE") or "").strip()
    if not p:
        return None
    try:
        raw = Path(p).read_text().strip()
    except OSError:
        return None
    key = unb64u(raw)
    if len(key) < 32:
        raise SystemExit("ZOGZOG_MASTER_KEY_FILE: 32 random bytes or more, base64url")
    return key


def check_public_url(url: str) -> None:
    """PUBLIC_URL is https, or http on this machine only. Every install and
    update pipes this server's reply into bash or iex, and the shipper sends
    its upload password here: over plain http anyone on the path could read
    the password and swap the script (security review L4)."""
    if not url:
        return
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").lower()
    loopback = host == "localhost" or host.startswith("127.") or host == "::1"
    if not (p.scheme == "https" and host) and not (p.scheme == "http" and loopback):
        raise SystemExit(f"PUBLIC_URL={url}: use https (the installers run what this address sends); "
                         "plain http only on 127.0.0.1 or localhost")


# ---------------------------------------------------------------- slugs from a BattleTag

# Name#1234. Battle.net names are 3 to 12 characters in any script; the
# discriminator is digits.
BATTLETAG_RE = re.compile(r"^([^#\s][^#]{0,63})#(\d{3,10})$")
SLUG_NAME_MAX = 12
SLUG_HASH_LENS = (4, 6, 8)


def battletag_of(raw: str | None) -> str:
    """The BattleTag in a header value, "" when it isn't one (a display name
    from a password login, nothing at all). Python reads header bytes as
    Latin-1; the proxy writes UTF-8 (Pétrel#4821), so read them back."""
    v = (raw or "").strip()
    with contextlib.suppress(UnicodeEncodeError, UnicodeDecodeError):
        v = v.encode("latin-1").decode("utf-8")
    v = unicodedata.normalize("NFC", v)
    return v if BATTLETAG_RE.match(v) else ""


def derived_slugs(battletag: str, key: bytes) -> list[str]:
    """The slugs a BattleTag may get, first choice first: the name part in
    a-z0-9 (accents stripped, 12 at most, "player" when nothing is left),
    a hyphen, then 4, 6 or 8 hex of an HMAC of the whole tag. Never the
    #1234: with it anyone could add the person as a Battle.net friend. The
    key (slug_key) keeps the hash from being worked out from a known tag.
    The same tag and key always give the same list. [] for no BattleTag."""
    tag = battletag_of(battletag)
    if not tag:
        return []
    name = tag.partition("#")[0]
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    name = re.sub(r"[^a-z0-9]", "", name)[:SLUG_NAME_MAX] or "player"
    digest = hmac.new(key, b"wow-stats slug|" + tag.encode(), hashlib.sha256).hexdigest()
    return [f"{name}-{digest[:n]}" for n in SLUG_HASH_LENS]


def slug_key(state: Path) -> bytes:
    """WOW_STATE_DIR/slug-key: 32 random bytes made on first start (0600),
    the key of derived_slugs. Its own key, not one of the others: the share
    token goes to the publisher and the exporter, and the zogzog master is
    optional. Losing it changes only the slugs given after: a given slug is
    in users.json and never changes."""
    path = state / "slug-key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(b64u(secrets.token_bytes(32)) + "\n")
        try:
            os.link(tmp, path)  # never replaces a key another process just made
        except FileExistsError:
            pass
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()
    key = unb64u(path.read_text().strip())
    if len(key) < 32:
        raise SystemExit(f"{path}: not a key (32 random bytes, base64url); delete it to make a new one")
    return key


PURGE_DAYS = 7


def purge_seconds(v: str | None) -> float:
    """FRIENDS_PURGE_DAYS in seconds: days, fractions allowed, 0 for at once."""
    raw = (v or "").strip()
    try:
        days = float(raw) if raw else PURGE_DAYS
    except ValueError:
        days = -1
    if not (days >= 0 and days < 3650):
        raise SystemExit(f"FRIENDS_PURGE_DAYS={raw!r}: a number of days, 0 or more (0: delete at once)")
    return days * 86400


def _epoch(iso: str) -> float | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=UTC)).timestamp()


def site_host(slug: str, domain: str) -> str:
    return f"{slug}-wow.{domain}" if domain else ""


def _csv(v: str) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


Tenant = User  # the name the MCP port uses


@dataclass
class Who:
    """The answer to "who is this request": a User, or why not."""
    user: User | None
    status: int = 200
    why: str = ""


class Tenancy:
    """The registry plus everything that turns a person into their App."""

    def __init__(self, env: dict):
        self.env = dict(env)
        self.state = Path(env["WOW_STATE_DIR"])
        self.registry = Registry(self.state / "users.json")
        # Every login of the owner's (2026-11-21: the usual account and the
        # one linked to Battle.net). They all land on one record, owner_sub,
        # so one tenant, one App, one analysis timer: the record already
        # marked owner if it's listed (reordering the list moves nothing),
        # else the first.
        self.owner_subs = tuple(dict.fromkeys(_csv(env.get("WOW_OWNER_SUB", ""))))
        if not self.owner_subs:
            raise SystemExit("WOW_STATE_DIR needs WOW_OWNER_SUB (the owner's sub: tenant 0)")
        self.owner_sub = next((u.sub for u in self.registry.users() if u.owner and u.sub in self.owner_subs),
                              self.owner_subs[0])
        self.owner_slug = env.get("WOW_OWNER_SLUG", "").strip().lower()
        if self.owner_slug and not SLUG_RE.match(self.owner_slug):
            raise SystemExit(f"WOW_OWNER_SLUG={self.owner_slug!r}: {SLUG_RULE}")
        self.owner_aliases = [a.lower() for a in _csv(env.get("WOW_OWNER_ALIASES", ""))]
        # The friends' event store: FRIENDS_VL_URL, or with the SQLite
        # store the same folder as the owner's (a file per person keeps
        # them apart, and deleting one is removing the file).
        from wowstats.eventstore import events_location, is_sqlite

        events = events_location(env)
        self.friends_vl = events if is_sqlite(events) else env.get("FRIENDS_VL_URL", "").strip().rstrip("/")
        self.friends_file = env.get("WOW_FRIENDS_FILE", "").strip()
        self.list_unusable = False
        self.purge_s = purge_seconds(env.get("FRIENDS_PURGE_DAYS"))
        from wowstats.stores import truthy

        self.allow_empty = truthy(env.get("FRIENDS_ALLOW_EMPTY") or "")
        # The last pass refused a list that removes everyone (list_state): the
        # overview says so, and the log says it once, not every pass.
        self.empty_refused = False
        # sub -> the listed name that clashed, so a clash is logged once, not
        # at every login and every pass (it's the owner's list to fix).
        self.name_conflicts: dict[str, str] = {}
        self.public_url = env.get("PUBLIC_URL", "").strip().rstrip("/")
        check_public_url(self.public_url)
        self.domain = env.get("SITE_DOMAIN", "").strip().lower().strip(".")
        self.user_header = env.get("PROXY_USER_HEADER", "X-Forwarded-User")
        self.battletag_header = (env.get("PROXY_BATTLETAG_HEADER") or "").strip()
        self.slug_key = slug_key(self.state)
        from wowstats.ingest import Budget

        self.ingest_budget = Budget(env.get("INGEST_DAILY_MB"))
        self._apps: dict[str, object] = {}
        self._ctx: dict[str, object] = {}
        self._lock = threading.Lock()
        from wowstats.activity import Activity

        # When each person last came by (the owner's overview, wowstats.admin).
        self.activity = Activity(self.user_dir)
        self.registry.ensure(self.owner_sub, owner=True, slug=self.owner_slug, public_on=True)
        # The owner's slug and his aliases' first labels (wow.m -> wow) are
        # never anyone else's, even before his record has a slug.
        self.registry.reserved = frozenset({self.owner_slug} | {a.split(".")[0] for a in self.owner_aliases}) - {""}
        # An owner's login also on the friends list stays the owner's (listed()
        # drops it). Said once, by its place in WOW_OWNER_SUB, never the sub.
        raw = read_friends(self.friends_file) if self.friends_file else None
        for i, s in enumerate(self.owner_subs, 1):
            if raw and s in raw:
                log_event("owner_also_listed", position=i, why=f"WOW_OWNER_SUB's login #{i} is also on the "
                          "friends list: it is the owner's", fix="take it off the friends list")

    def is_owner(self, sub: str) -> bool:
        return sub in self.owner_subs

    def list_state(self, allow_empty: bool = False) -> tuple[dict[str, str] | None, str]:
        """The friends list (read_friends) without the owner's logins (the
        owner wins over the list, so none of his is provisioned, named or
        removed as a friend), and why it is None: "unusable" (missing or
        broken) or "empty" (a valid list naming nobody while friends are
        on: refused unless FRIENDS_ALLOW_EMPTY or `allow_empty`). Either way
        nothing changes and the registry answers, as for a broken file."""
        raw = read_friends(self.friends_file) if self.friends_file else None
        if raw is None:
            return None, "unusable"
        out = {k: v for k, v in raw.items() if k not in self.owner_subs}
        if not out and not (allow_empty or self.allow_empty) and self.current_friends():
            return None, "empty"
        return out, ""

    def listed(self) -> dict[str, str] | None:
        return self.list_state()[0]

    def current_friends(self) -> list[User]:
        """The friends who are in: not the owner, not already removed (a
        disabled one counts: the operator's switch, still on the list)."""
        return [u for u in self.registry.users()
                if not (u.owner or u.account_id == 0 or self.is_owner(u.sub) or u.removed_at)]

    def purge_at(self, u: User) -> float | None:
        """When a removed person's data goes (epoch seconds), None when they're in."""
        at = _epoch(u.removed_at)
        return None if at is None else at + self.purge_s

    def pending_purges(self) -> list[dict]:
        """Who is removed and waiting for the deletion, soonest first: for
        `users list`, the overview and the start log. No sub, no key."""
        out = []
        for u in self.registry.users():
            at = self.purge_at(u)
            if at is not None and not u.owner:
                out.append({"slug": u.slug or None, "account_id": u.account_id, "removed_at": u.removed_at,
                            "purge_at": datetime.fromtimestamp(at, UTC).isoformat(timespec="seconds")})
        return sorted(out, key=lambda p: p["purge_at"])

    # ------------------------------------------------------------ who

    def identify(self, sub: str, battletag: str = "") -> Who:
        """The person behind a login (module docstring: the owner's sub, or a
        listed one). A listed person reconcile() hasn't reached yet is
        provisioned here, and one without a slug gets theirs: the list's, or
        one derived from `battletag` (read now, never kept). Ids decide who
        gets in: a name, email or BattleTag never does."""
        sub = (sub or "").strip()
        if not sub or len(sub) > 200:
            return Who(None, 401, "not logged in")
        if self.is_owner(sub):  # before the list and the BattleTag: no slug, no tenant of his own
            return Who(self.registry.get(self.owner_sub))
        listed = self.listed()
        u = self.registry.get(sub)
        # A broken list changes nothing (reconcile): the registry, which
        # mirrors the last good one, answers until it's fixed.
        if (sub not in listed) if listed is not None else (u is None):
            # Also the page of a friend whose first sign-in made their account
            # and who now waits for the owner: what happened, what to do,
            # what comes next (owner, 2026-11-20).
            return Who(None, 403, f"You're signed in, but {RUNS_IT} hasn't let you in yet. Ask them to add "
                                  "you, then reload this page.")
        if not self.friends_vl:
            return Who(None, 403, f"This install doesn't take friends yet. Ask {RUNS_IT}.")
        if u is not None and u.removed_at and listed is not None:  # listed again before the purge
            u = restore(self, u)
        if u is None or (not u.slug and u.active):
            u = provision_or_flag(self, sub, (listed or {}).get(sub, ""), battletag) or self.registry.get(sub)
        if not u.active:
            return Who(None, 403, f"This account is switched off. Ask {RUNS_IT} to turn it back on.")
        return Who(u)


    def resolve_tenant(self, sub: str) -> User | None:
        """The MCP's one entry point (its auth middleware calls this with the
        upstream token's sub, at login and at each refresh): the same rule
        as the web view (identify)."""
        return self.identify(sub).user

    def from_headers(self, headers) -> Who:
        """The login proxy's header (the server checked the request came
        from the proxy: wowstats.guard). Sent twice is no one."""
        from wowstats import guard

        sub, problem = guard.identity(headers, self.user_header)
        if problem:
            return Who(None, 400, problem)
        tag = ""
        if self.battletag_header:
            raw, problem = guard.identity(headers, self.battletag_header)
            tag = "" if problem else raw  # sent twice: no name from it, the login still works
        return self.identify(sub, tag)

    def mcp_user(self, sub: str) -> User | None:
        """Every /mcp call and refresh: the person still exists and is on."""
        u = self.registry.get(self.owner_sub if self.is_owner(sub) else sub)
        if u is None or not u.active:
            return None
        self.activity.seen(u, "mcp")
        return u

    # ------------------------------------------------------------ their settings

    def user_dir(self, u: User) -> Path:
        return self.state / "users" / str(u.account_id)

    def env_for(self, u: User) -> dict:
        """The person's environment: the process's, with their store, their
        tenant and their state. Friends never get the owner's archive, their
        default character, their weights or a share port."""
        e = dict(self.env)
        for k in ("MCP_PUBLIC_URL", "MCP_AUTH", "INGEST_PASSWORD", "INGEST_PASSWORD_FILE", "ANALYSIS_SHARE_PORT"):
            e[k] = ""
        e["VL_ACCOUNT_ID"] = str(u.account_id)
        # The auction scans everyone here shares (wowstats.ahshared), in the
        # friends' store, owner included: one ruleset and faction's house is
        # the same house whoever scanned it (owner, 2026-11-24).
        from wowstats import ahshared

        e[ahshared.ENV] = self.friends_vl
        # The owner pays the server's own analysis runs: their nudge says
        # "your own credit", a friend's names the owner (wowstats.analysis.auto).
        from wowstats.analysis.auto import OWNER_MARK

        e[OWNER_MARK] = "1" if u.owner else ""
        # The 3D view's game files the person's shipper sends: in their own
        # folder, so delete_tenant takes them too.
        e["MODEL3D_ASSETS_DIR"] = str(self.user_dir(u) / "assets")
        # Their item facts (wowstats.itemfacts): theirs, gone with them too.
        e["ITEM_FACTS_DIR"] = str(self.user_dir(u) / "items")
        if u.owner:
            e.setdefault("ANALYSIS_STATE_DIR", str(self.user_dir(u) / "analysis"))
            if not e["ANALYSIS_STATE_DIR"]:
                e["ANALYSIS_STATE_DIR"] = str(self.user_dir(u) / "analysis")
            return e
        e.update({"VL_URL": self.friends_vl, "VL_ARCHIVE_URL": "", "DEFAULT_CHAR": "", "WEIGHTS": "",
                  "ANALYSIS_STATE_DIR": str(self.user_dir(u) / "analysis")})
        return e

    def app_for(self, u: User):
        """The person's App, made once. A changed setting that the App reads
        at start (none so far) would need forget()."""
        from wowstats import ui

        with self._lock:
            app = self._apps.get(u.sub)
            if app is None:
                env = self.env_for(u)
                Path(env["ANALYSIS_STATE_DIR"]).mkdir(parents=True, exist_ok=True)
                app = self._apps[u.sub] = ui.App(env)
                app.render_site = lambda sub=u.sub: self.render_site(sub)
                # Read live: a change in Settings needs no restart.
                app.models_default = lambda sub=u.sub: getattr(self.registry.get(sub), "models", None)
            return app

    def render_site(self, sub: str) -> tuple[str, bytes | None]:
        """Where the full view reads the person's armory render: their site's
        host on the static server, and their /zogzog/ key for the sealed copy
        (wowstats.armory.from_site)."""
        u = self.registry.get(sub)
        if u is None or not u.slug:
            return "", None
        return self.canonical_host(u), person_key(self.env, u.account_id, u.zogzog_gen, u.owner)

    def ctx_for(self, u: User):
        """The MCP tools' context for the person: their store, their env,
        their Battle.net setting (read live: a toggle needs no restart)."""
        from wowstats.mcp import context

        with self._lock:
            ctx = self._ctx.get(u.sub)
            if ctx is None:
                ctx = self._ctx[u.sub] = context(self.env_for(u))
                app = self._apps.get(u.sub)
                if app is not None:
                    ctx.title = app.title
        live = self.registry.get(u.sub)
        # Read live too: a first login gives the slug after the context was made.
        slug = (live.slug if live else "") or u.slug
        return replace(ctx, bnet=live.bnet_to_model if live else False,
                       who=slug or f"an account with no name yet (tenant {u.account_id})")

    def forget(self, sub: str) -> None:
        with self._lock:
            self._apps.pop(sub, None)
            self._ctx.pop(sub, None)
        u = self.registry.get(sub)
        if u is not None:
            self.activity.forget(u)

    # ------------------------------------------------------------ hosts and keys

    def hosts_of(self, u: User) -> list[str]:
        out = [site_host(u.slug, self.domain)] if u.slug and self.domain else []
        return out + (self.owner_aliases if u.owner else [])

    def canonical_host(self, u: User) -> str:
        """Where the person's site lives: the owner's first alias (wow.m),
        everyone else's <slug>-wow host."""
        if u.owner and self.owner_aliases:
            return self.owner_aliases[0]
        return site_host(u.slug, self.domain)

    def zogzog_link(self, u: User) -> str | None:
        """The person's /zogzog/ link, key in the fragment (person_key)."""
        host = self.canonical_host(u)
        if not host or not u.slug:
            return None
        key = person_key(self.env, u.account_id, u.zogzog_gen, u.owner)
        return f"https://{host}/zogzog/#{b64u(key)}" if key else None

    def host_list(self) -> list[dict]:
        """For the host's DNS automation: each person's site host, whether they want it
        public now, whether they're active. The owner's aliases included."""
        out = []
        for u in self.registry.users():
            if not u.slug:
                continue
            for h in self.hosts_of(u):
                out.append({"slug": u.slug, "host": h, "wants_public": u.public_on, "active": u.active,
                            "owner": u.owner})
        return out

    def users_json(self) -> dict:
        """For the publisher (share port): who has a site, which store and
        tenant, what is on. No hashes, no keys."""
        from wowstats import gearmode

        out = []
        for u in self.registry.users():
            if not u.slug:
                continue
            env = self.env_for(u)
            out.append({**u.public(), "store": "owner" if u.owner else "friends",
                        "gear_settings": gearmode.for_publish(env), "weights": env.get("WEIGHTS", ""),
                        "hosts": self.hosts_of(u), "canonical": self.canonical_host(u)})
        return {"users": out}

    def profile_characters(self) -> list[tuple[int, str]]:
        """(account_id, character) for the exporter (one Battle.net profile
        call a day each; its series carry the account_id, which every reader
        asks for: wowstats.stores.profile_sel). Each person's characters in
        their order, newest first; the exporter caps a friend's count.

        A name is one character on Battle.net, so it belongs to one person.
        Anyone can upload an event naming any character, so a name two
        people have goes to the owner when the owner has it, else to nobody:
        a friend can't read another person's Battle.net data by naming
        their character (security review 0.6.0 S1). Logged as a count."""
        claims: dict[str, list[User]] = {}
        order: list[tuple[User, str]] = []
        for u in self.registry.users():
            if not u.active:
                continue
            try:
                app = self.app_for(u)
                names = [c["char"] for c in app.views.chars() if not c.get("demo")]
            except Exception:  # noqa: BLE001 - one store down doesn't empty everyone's list
                continue
            for n in dict.fromkeys(names):
                claims.setdefault(n, []).append(u)
                order.append((u, n))
        out, contested = [], 0
        for u, n in order:
            who = claims[n]
            if len(who) > 1:
                if u.account_id == who[0].account_id:
                    contested += 1
                if not u.owner:
                    continue
            out.append((u.account_id, n))
        if contested:
            log_event("profile_names_contested", count=contested)
        return out


# ---------------------------------------------------------------- deletion


def delete_tenant(t: Tenancy, u: User, dry_run: bool = False) -> dict:
    """Everything of one friend: their events (a VictoriaLogs delete task
    for their tenant, on the friends' store only, or their SQLite file),
    their state folder and
    their registry entry with its ingest token and install codes. The
    bundles go at the publisher's next pass (it drops slugs that left
    users.json, on every static pod). Their MCP logins are refused from then
    on (no registry entry) and expire. Battle.net data in vm-wow is not
    touched: the exporter stops reading their characters at its next daily
    pass and the store's 23-day retention drops the rest. Never the owner."""
    if u.owner or u.account_id == 0:
        raise TenancyError("the owner's tenant is never deleted this way")
    if not t.friends_vl:
        raise TenancyError("FRIENDS_VL_URL is unset")
    from wowstats import eventstore
    from wowstats.stores import SourceError

    key_file = (t.env.get("FRIENDS_VL_DELETE_KEY_FILE") or "").strip()
    key = Path(key_file).read_text().strip() if key_file and not eventstore.is_sqlite(t.friends_vl) else ""
    out = {"slug": u.slug, "account_id": u.account_id, "dry_run": dry_run}
    if dry_run:
        return out
    try:
        out["delete_task"] = eventstore.get(t.friends_vl).delete(u.account_id, auth_key=key)
    except SourceError as e:
        raise TenancyError(str(e)) from None
    shutil.rmtree(t.user_dir(u), ignore_errors=True)
    t.registry.remove(u.sub)
    t.forget(u.sub)
    return out


# ---------------------------------------------------------------- the friends list


def log_event(action: str, **fields) -> None:
    """One JSON line per step, for the host's log pipeline."""
    print(json.dumps({"event": "friends", "action": action, **fields}, sort_keys=True), flush=True)


def read_friends(path: str) -> dict[str, str] | None:
    """WOW_FRIENDS_FILE as {sub: slug}, the slug "" when the list leaves it
    to the login (derived_slugs). The file is {"<sub>": "<slug>"}, the same
    with "" or null for some, or ["<sub>", ...]. A valid {} or [] is a list
    with nobody on it: everyone listed before leaves (the old rule read it as
    a failed render and kept the last friend active; outside review,
    2026-11-19). None when the file is missing, unreadable, not JSON, of
    another shape or has a bad entry, and then reconcile() changes nothing:
    a list that failed to render must never read as "everyone left"."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    if isinstance(data, list):
        if not all(isinstance(k, str) and k.strip() for k in data):
            return None
        return dict.fromkeys(data, "")
    if not isinstance(data, dict):
        return None
    data = {k: "" if v is None else v for k, v in data.items()}
    if not all(isinstance(k, str) and k.strip() and isinstance(v, str) and (v == "" or SLUG_RE.match(v))
               for k, v in data.items()):
        return None
    return data


class Unnamed(TenancyError):
    """A listed person with no slug in the list and no BattleTag to derive
    one from."""


def provision(t: Tenancy, sub: str, slug: str, battletag: str = "") -> User:
    """A listed person's tenant, with the slug the list gives them or, when
    it gives none, the first free one of derived_slugs. A slug they already
    have is kept, whatever either says."""
    u = t.registry.ensure(sub)
    if u.slug:
        return u
    if slug:
        u = t.registry.set_slug(sub, slug)
        log_event("provisioned", slug=slug, account_id=u.account_id)
        return u
    choices = derived_slugs(battletag, t.slug_key)
    if not choices:
        raise Unnamed("no slug in the friends list and no BattleTag at the login")
    for c in choices:
        try:
            u = t.registry.set_slug(sub, c)
        except TenancyError:
            now = t.registry.get(sub)
            if now is not None and now.slug:  # another request named them first
                return now
            continue  # taken or reserved: a longer hash
        log_event("provisioned", slug=c, account_id=u.account_id, source="battletag")
        return u
    raise TenancyError(f"'{choices[-1]}' is taken")


def name_conflict(t: Tenancy, u: User, listed: dict[str, str] | None) -> dict | None:
    """Why a listed person has no address: their listed name is "taken"
    (someone has it, the owner's own names included) or "reserved" (the
    app's words), or the list gives none and their login brought no
    BattleTag ("no name", slug ""). None when they have one, or the name is
    free and the next pass gives it to them."""
    if u.slug or u.owner or not listed or u.sub not in listed:
        return None
    slug = listed[u.sub]
    if not slug:
        return {"slug": "", "why": "no name"}
    if slug in t.registry.reserved or t.registry.by_slug(slug) is not None:
        return {"slug": slug, "why": "taken"}
    if slug in RESERVED:
        return {"slug": slug, "why": "reserved"}
    return None


def provision_or_flag(t: Tenancy, sub: str, slug: str, battletag: str = "") -> User | None:
    """provision(), or None when the listed name is taken or reserved, or
    there is no name at all (no slug listed, no BattleTag). The person is in
    without an address, and /start tells them the owner has a name to fix:
    friends never pick their own (the owner's rule, 2026-11-19), so it is
    his list to fix, logged once per name."""
    try:
        u = provision(t, sub, slug, battletag)
    except TenancyError as e:
        if t.name_conflicts.get(sub) != slug:
            t.name_conflicts[sub] = slug
            log_event("name_conflict", slug=slug or None, why=str(e),
                      fix="give this person a name in the friends list")
        return None
    t.name_conflicts.pop(sub, None)
    return u


def revoke(t: Tenancy, u: User, now: float) -> User:
    """A person who left the list: every access stops now (status
    "removed": identify, mcp_user, check_ingest, redeem and the publisher
    all want "active"), their data stays until purge_at. Someone the
    operator had switched off stays "disabled", so being listed again
    doesn't switch them back on."""
    at = datetime.fromtimestamp(now, UTC).isoformat(timespec="seconds")
    u = t.registry.update(u.sub, status="removed" if u.status == "active" else u.status, removed_at=at)
    t.forget(u.sub)
    log_event("removed", slug=u.slug or None, account_id=u.account_id, removed_at=at,
              purge_at=datetime.fromtimestamp(t.purge_at(u), UTC).isoformat(timespec="seconds"))
    return u


def restore(t: Tenancy, u: User) -> User:
    """Listed again before the purge: back in, with everything they had."""
    u = t.registry.update(u.sub, status="active" if u.status == "removed" else u.status, removed_at="")
    t.forget(u.sub)
    log_event("restored", slug=u.slug or None, account_id=u.account_id)
    return u


def purge(t: Tenancy, u: User, reason: str) -> dict:
    """delete_tenant, logged: the window ran out, or the operator said now."""
    out = delete_tenant(t, u)
    log_event("purged", slug=u.slug or None, account_id=u.account_id, reason=reason)
    return out


def reconcile(t: Tenancy, now: float | None = None, allow_empty: bool = False) -> dict:
    """Make the registry match the friends list: provision who is listed
    with a slug, restore who is listed again, revoke who left it and purge
    who left it FRIENDS_PURGE_DAYS ago. Someone listed without a slug waits
    for their first login (identify). The owner is never in play. A broken
    list, or one that would remove every friend at once (unless
    `allow_empty`), changes nothing: no purge either, since it can't say
    who is still out."""
    now = time.time() if now is None else now
    listed, why = t.list_state(allow_empty)
    if listed is None:
        if why == "empty":
            if not t.empty_refused:  # once per refusal, not every pass
                log_event("empty_list_refused", level="error", path=t.friends_file,
                          friends=len(t.current_friends()),
                          why="the list names nobody, which would remove every friend at once: nothing changed",
                          fix="if that is what you want, run `wow-stats users reconcile --allow-empty` "
                              "(or set FRIENDS_ALLOW_EMPTY=1)")
            t.empty_refused = True
            return {"skipped": True, "refused": "empty"}
        if not t.list_unusable:  # once, not every pass
            log_event("list_unusable", path=t.friends_file,
                      why="missing, unreadable or not {sub: slug}: nothing changed")
        t.list_unusable = True
        return {"skipped": True}
    t.list_unusable = t.empty_refused = False
    out: dict = {"skipped": False, "provisioned": [], "restored": [], "removed": [], "purged": [], "errors": []}
    for sub, slug in listed.items():
        u = t.registry.get(sub)
        if u is not None and u.removed_at:
            u = restore(t, u)
            out["restored"].append(u.slug or u.account_id)
        if u is not None and u.slug:
            if slug and u.slug != slug:
                log_event("slug_kept", slug=u.slug, listed_slug=slug, account_id=u.account_id)
            continue
        if not slug:
            continue  # named from their BattleTag at their first login (identify)
        if provision_or_flag(t, sub, slug) is None:
            out["errors"].append(slug)
        else:
            out["provisioned"].append(slug)
    for u in t.registry.users():
        # An owner's login with a friend's record from before stays as it is:
        # nothing of anyone's is deleted for being the owner's.
        if u.owner or u.account_id == 0 or t.is_owner(u.sub) or u.sub in listed:
            continue
        try:
            if t.purge_at(u) is None:  # just left (or a removed_at that doesn't read: the clock starts now)
                u = revoke(t, u, now)
                out["removed"].append(u.slug or u.account_id)
            if now >= t.purge_at(u):
                purge(t, u, "window" if t.purge_s else "at once")
                out["purged"].append(u.slug or u.account_id)
        except (TenancyError, OSError) as e:
            log_event("purge_failed", slug=u.slug or None, account_id=u.account_id, why=str(e))
            out["errors"].append(u.slug or u.account_id)
    return out


# How often the watch looks at the list's content. A friend added in the
# identity provider used to wait for a restart of the full view (the host
# rolled the pod on a changed list, 2026-11-19: a minute of "no available
# server" for everyone); a read of a small file every few seconds costs
# nothing next to that.
WATCH_S = 10


def _digest(path: str) -> str | None:
    """The list's content hash, None when it can't be read."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


class FriendsWatch:
    """The full view's eye on the friends list: reconcile() as soon as the
    content changes, and every `every` seconds whatever happens (a failed
    offboard gets its retry there). A broken file is reconciled once
    (skipped, one log line) and then left alone until it changes: the
    registry keeps the last good list. `on_change` runs after a pass that
    provisioned or offboarded someone (the full view wakes its prebuilt
    answers, so a new friend's are built now, not at the next check)."""

    def __init__(self, t: Tenancy, every: float = 120, on_change=None):
        self.t, self.every, self.on_change = t, every, on_change
        self.digest: str | None = None
        self.done_at: float | None = None

    def step(self, now: float | None = None) -> dict | None:
        """One look; the pass's result when it ran one, else None."""
        now = time.monotonic() if now is None else now
        digest = _digest(self.t.friends_file)
        if self.done_at is not None and digest == self.digest and now - self.done_at < self.every:
            return None
        if self.done_at is not None and digest != self.digest:
            log_event("list_changed", path=self.t.friends_file)
        out = reconcile(self.t)
        self.digest, self.done_at = digest, now
        if any(out.get(k) for k in ("provisioned", "restored", "removed", "purged")) and self.on_change is not None:
            self.on_change()
        return out


def start_reconcile(t: Tenancy, on_change=None) -> threading.Thread | None:
    """The full view's background watch over the friends list."""
    if not t.friends_file:
        return None
    watch = FriendsWatch(t, int(t.env.get("FRIENDS_RECONCILE_S") or "120"), on_change)
    # Who is waiting for the deletion, said at each start: the dates live in
    # users.json, so a restart is also when someone may wonder.
    log_event("purges_pending", purge_days=t.purge_s / 86400, pending=t.pending_purges())

    def loop():
        while True:
            try:
                watch.step()
            except Exception as e:  # noqa: BLE001 - one bad pass waits for the next
                log_event("reconcile_failed", why=f"{type(e).__name__}: {e}")
            time.sleep(WATCH_S)

    th = threading.Thread(target=loop, name="friends", daemon=True)
    th.start()
    return th


# ---------------------------------------------------------------- `wow-stats users`


def cli(argv: list[str]) -> int:
    """`wow-stats users list | hosts | reconcile [--allow-empty] | disable <slug> |
    enable <slug> | delete <slug> [--yes] | ah-share`, run where the full view runs (docker or kubectl exec): the
    same WOW_STATE_DIR and settings. Prints no token, hash or key. `delete`
    is also the purge-now of a removed friend: it skips the rest of the
    FRIENDS_PURGE_DAYS window."""
    import argparse
    import sys

    ap = argparse.ArgumentParser(prog="wow-stats users")
    ap.add_argument("action", choices=["list", "hosts", "reconcile", "disable", "enable", "delete",
                                           "ah-share"])
    ap.add_argument("slug", nargs="?")
    ap.add_argument("--yes", action="store_true", help="delete: really delete (default: say what would go)")
    ap.add_argument("--allow-empty", action="store_true",
                    help="reconcile: apply a friends list that removes every friend at once")
    a = ap.parse_args(argv)
    t = Tenancy(dict(os.environ))
    if a.action == "list":
        listed = t.listed()
        for u in t.registry.users():
            c = name_conflict(t, u, listed)
            clash = (c["slug"] or c["why"]) if c else None  # the name to fix, or "no name"
            due = t.purge_at(u)
            print(json.dumps({"slug": u.slug or None, "name_conflict": clash, "account_id": u.account_id,
                              "owner": u.owner, "org": u.org,
                              "status": u.status, "public_on": u.public_on, "created": u.created,
                              "shipper_set_at": u.ingest_set_at or None, "first_event": u.first_event or None,
                              "removed_at": u.removed_at or None,
                              "purge_at": datetime.fromtimestamp(due, UTC).isoformat(timespec="seconds")
                              if due is not None else None}))
        return 0
    if a.action == "hosts":
        print(json.dumps({"hosts": t.host_list()}, indent=1))
        return 0
    if a.action == "ah-share":
        # Once, after the upgrade that shares the auction scans: the scans
        # stored before it, of everyone still active, into the shared space
        # (wowstats.ahshared). Safe to run again.
        from wowstats import ahshared

        if not t.friends_vl:
            print("no friends' store (FRIENDS_VL_URL, or EVENTS_STORE=sqlite:...): nothing to share into",
                  file=sys.stderr)
            return 1
        people = [(u.slug or str(u.account_id), t.app_for(u).store) for u in t.registry.users() if u.active]
        ahshared.backfill(t.friends_vl, people)
        return 0
    if a.action == "reconcile":
        out = reconcile(t, allow_empty=a.allow_empty)
        if out.get("refused") == "empty":
            print("refused: the friends list names nobody, which would remove every friend at once. "
                  "Run again with --allow-empty if that is what you want.", file=sys.stderr)
        return 1 if out.get("skipped") else 0
    u = t.registry.by_slug(a.slug or "")
    if u is None:
        print(f"no one has the slug {a.slug!r}", file=sys.stderr)
        return 1
    if a.action in ("disable", "enable"):
        if u.owner:
            print("the owner can't be switched off", file=sys.stderr)
            return 1
        if u.removed_at:
            print(f"{u.slug} is off the friends list (data deleted after {u.removed_at} + "
                  f"{t.purge_s / 86400:g} days): put them back on the list instead", file=sys.stderr)
            return 1
        t.registry.update(u.sub, status="disabled" if a.action == "disable" else "active")
        print(f"{u.slug}: {'disabled' if a.action == 'disable' else 'active'} (the web view and the MCP refuse "
              "at the next request; the site goes at the publisher's next pass)")
        return 0
    try:
        print(json.dumps(delete_tenant(t, u, dry_run=True) if not a.yes else purge(t, u, "operator")))
    except TenancyError as e:
        print(str(e), file=sys.stderr)
        return 1
    if not a.yes:
        print("dry run: add --yes to delete the events, the state and the registry entry", file=sys.stderr)
    elif u.sub in (t.listed() or {}):
        print(f"{u.slug} is still on the friends list: take them off it too, or the next pass gives them a new, "
              "empty account", file=sys.stderr)
    return 0
