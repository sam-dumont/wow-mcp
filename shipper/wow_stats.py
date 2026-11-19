#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# Part of wow-stats, GPL-3.0-only: the installers put the license (LICENSE)
# next to this file, and their header names the source of this version.
"""wow-stats: archive WoW Forever's WTF folder, extract events, ship them.

Runs every few minutes (launchd on macOS, Task Scheduler on Windows; the
installers next to this file), and on macOS also each time the client saves
its SavedVariables (logout, /reload). Four steps, each safe to rerun:

1. archive: mirror `_forever_/WTF/` into a git repo (<archive_dir>/wtf) and
   commit. Polestar Recorder deletes the chunks its uploader acked at the next
   login, so only the last few hours survive in the file: the git history is
   the only place every version is kept. Skipped when git isn't installed,
   or with `wtf_archive = false` in the config (stats only: the events are
   still extracted, spooled, sent and kept in sent/; --backfill has nothing
   to replay).
2. extract: turn what is new in the SavedVariables (Polestar, Polestar
   Recorder, Auctionator, Syndicator and friends for the gear, Questie and
   Altoholic for the quests done before Polestar) and Logs/WoWCombatLog*.txt into NDJSON events in
   <archive_dir>/spool/. docs/contract.md lists every field.
3. ship: POST each spool file to the receiver in [ship] (basic auth): the
   wow-stats ingest endpoint of the compose kit, or any receiver that takes
   the same NDJSON. Sent files go to sent/ gzipped. No [ship] url or no
   network: log it and stop, the spool waits.
4. update: at most every 6 hours, ask the same server for a newer version
   of this file and replace it (check_update; `auto_update = false` pins it).

Python standard library only (3.11+). Settings: config.toml, see
config.example.toml (~/.config/wow-stats/ on macOS and Linux,
%APPDATA%\\wow-stats\\ on Windows, or WOW_STATS_CONFIG).

Flags: --dry-run (extract to stdout, write nothing), --no-ship, --backfill
(re-extract every archived version, for events a past run missed),
--install-recorder (the installers: put Polestar Recorder in the AddOns
folder, see install_recorder).
"""

from __future__ import annotations

import argparse
import base64
import csv
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import tomllib
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

# The release this file belongs to: pyproject's version, moved with it at each
# release (tests/test_shipper_update.py keeps them equal). The server reads
# it from the file it serves, auto_update compares it, and the setup report
# carries it (/start asks an install without it to update once by hand).
VERSION = "0.6.0"

WINDOWS = os.name == "nt"
HOME = Path.home()
WOW_CANDIDATES = [
    Path("/Applications/World of Warcraft/_forever_"),
    Path(r"C:\Program Files (x86)\World of Warcraft\_forever_"),
    Path(r"C:\Program Files\World of Warcraft\_forever_"),
]


def config_path() -> Path:
    if os.environ.get("WOW_STATS_CONFIG"):
        return Path(os.environ["WOW_STATS_CONFIG"]).expanduser()
    if WINDOWS:
        return Path(os.environ.get("APPDATA", HOME / "AppData" / "Roaming")) / "wow-stats" / "config.toml"
    return HOME / ".config" / "wow-stats" / "config.toml"


def default_log() -> Path:
    if WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA", HOME / "AppData" / "Local")) / "wow-stats" / "wow-stats.log"
    if sys.platform == "darwin":
        return HOME / "Library" / "Logs" / "wow-stats.log"
    return HOME / ".local" / "state" / "wow-stats" / "wow-stats.log"


# Set by configure() from config.toml; these are the defaults.
CONFIG: dict = {}
WOW = WOW_CANDIDATES[0]
ARCHIVE = HOME / "WoWArchive"
REPO = ARCHIVE / "wtf"
STATE = ARCHIVE / "state.json"
SPOOL = ARCHIVE / "spool"
SENT = ARCHIVE / "sent"
REJECTED = ARCHIVE / "rejected"
TOO_OLD = ARCHIVE / "too-old"
LOG = default_log()

QUIET_S = 5  # the client writes its files one after another: wait for 5 s of quiet
QUIET_MAX_WAIT_S = 120
POST_MAX_BYTES = 4 * 1024 * 1024
ADDONS_FILE = "_addons.tsv"  # in the archive repo, next to the WTF mirror
# No console window for git when Task Scheduler runs us with pythonw.
NO_WINDOW = {"creationflags": 0x08000000} if WINDOWS else {}


def configure() -> None:
    global CONFIG, WOW, ARCHIVE, REPO, STATE, SPOOL, SENT, REJECTED, TOO_OLD, LOG
    p = config_path()
    try:
        CONFIG = tomllib.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        CONFIG = {}
    path = lambda v: Path(os.path.expandvars(str(v))).expanduser()
    if CONFIG.get("wow_dir"):
        WOW = path(CONFIG["wow_dir"])
    else:
        WOW = next((c for c in WOW_CANDIDATES if (c / "WTF").is_dir()), WOW_CANDIDATES[0])
    ARCHIVE = path(CONFIG.get("archive_dir", HOME / "WoWArchive"))
    REPO, STATE = ARCHIVE / "wtf", ARCHIVE / "state.json"
    SPOOL, SENT, REJECTED = ARCHIVE / "spool", ARCHIVE / "sent", ARCHIVE / "rejected"
    TOO_OLD = ARCHIVE / "too-old"
    LOG = path(CONFIG.get("log_file", default_log()))


DRY = False  # --dry-run: the log goes to stderr only


def log(*parts: object) -> None:
    line = " ".join([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), *map(str, parts)])
    if sys.stderr is not None and (DRY or sys.stderr.isatty()):
        print(line, file=sys.stderr, flush=True)
    if DRY:
        return
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# SavedVariables: a Lua table reader (the subset the client writes)
# ---------------------------------------------------------------------------

_NUM = re.compile(rb"-?(?:0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")
_NAME = re.compile(rb"[A-Za-z_][A-Za-z0-9_]*")
_PLAIN = re.compile(rb'[^"\'\\]+')
_ESC = {ord("n"): b"\n", ord("t"): b"\t", ord("r"): b"\r", ord("a"): b"\a", ord("b"): b"\b",
        ord("f"): b"\f", ord("v"): b"\v", ord("\\"): b"\\", ord('"'): b'"', ord("'"): b"'",
        ord("\n"): b"\n"}


class LuaError(ValueError):
    pass


class LuaReader:
    """raw=True keeps strings as bytes: Auctionator stores binary CBOR in them."""

    def __init__(self, data: bytes, raw: bool = False):
        self.s, self.i, self.raw = data, 0, raw

    def _ws(self) -> None:
        s, n = self.s, len(self.s)
        while self.i < n:
            c = s[self.i]
            if c in b" \t\r\n":
                self.i += 1
            elif s.startswith(b"--", self.i):
                if s.startswith(b"--[[", self.i):
                    j = s.find(b"]]", self.i)
                    self.i = n if j < 0 else j + 2
                else:
                    j = s.find(b"\n", self.i)
                    self.i = n if j < 0 else j + 1
            else:
                return

    def _expect(self, ch: bytes) -> None:
        self._ws()
        if not self.s.startswith(ch, self.i):
            raise LuaError(f"expected {ch!r} at byte {self.i}")
        self.i += len(ch)

    def document(self) -> dict:
        out = {}
        while True:
            self._ws()
            if self.i >= len(self.s):
                return out
            m = _NAME.match(self.s, self.i)
            if not m:
                raise LuaError(f"expected a name at byte {self.i}")
            self.i = m.end()
            self._expect(b"=")
            out[m.group().decode()] = self.value()
            self._ws()
            if self.s.startswith(b";", self.i):
                self.i += 1

    def value(self):
        self._ws()
        s, i = self.s, self.i
        if i >= len(s):
            raise LuaError("unexpected end")
        c = s[i]
        if c == ord("{"):
            return self._table()
        if c in (ord('"'), ord("'")):
            return self._string()
        for word, val in ((b"true", True), (b"false", False), (b"nil", None)):
            if s.startswith(word, i) and not s[i + len(word):i + len(word) + 1].isalnum():
                self.i = i + len(word)
                return val
        for word, val in ((b"-inf", float("-inf")), (b"inf", float("inf")), (b"-nan", float("nan")),
                          (b"nan", float("nan"))):
            if s.startswith(word, i):
                self.i = i + len(word)
                return val
        m = _NUM.match(s, i)
        if not m:
            raise LuaError(f"unexpected {s[i:i + 20]!r} at byte {i}")
        self.i = m.end()
        t = m.group()
        if t.lower().startswith((b"0x", b"-0x")):
            return int(t, 16)
        if re.fullmatch(rb"-?\d+", t):
            return int(t)
        return float(t)

    def _string(self) -> str:
        s = self.s
        q = s[self.i]
        self.i += 1
        buf = bytearray()
        while True:
            m = _PLAIN.match(s, self.i)
            if m:
                buf += m.group()
                self.i = m.end()
            if self.i >= len(s):
                raise LuaError("unterminated string")
            c = s[self.i]
            if c == q:
                self.i += 1
                return bytes(buf) if self.raw else buf.decode("utf-8", errors="replace")
            if c != ord("\\"):  # the other quote character
                buf.append(c)
                self.i += 1
                continue
            e = s[self.i + 1]
            if e in _ESC:
                buf += _ESC[e]
                self.i += 2
            elif 48 <= e <= 57:
                m = re.match(rb"\d{1,3}", s[self.i + 1:self.i + 4])
                buf.append(int(m.group()) & 0xFF)
                self.i += 1 + len(m.group())
            elif e == ord("x"):
                buf.append(int(s[self.i + 2:self.i + 4], 16))
                self.i += 4
            elif e == ord("z"):
                self.i += 2
                while self.i < len(s) and s[self.i] in b" \t\r\n":
                    self.i += 1
            elif e == ord("u"):
                j = s.index(b"}", self.i)
                buf += chr(int(s[self.i + 3:j], 16)).encode()
                self.i = j + 1
            else:
                buf.append(e)
                self.i += 2

    def _table(self) -> dict:
        self.i += 1  # {
        out: dict = {}
        n = 0
        while True:
            self._ws()
            s = self.s
            if s.startswith(b"}", self.i):
                self.i += 1
                return out
            if s.startswith(b"[", self.i):
                self.i += 1
                key = self.value()
                self._expect(b"]")
                self._expect(b"=")
                out[key] = self.value()
            else:
                m = _NAME.match(s, self.i)
                j = m.end() if m else -1
                k = j
                while 0 <= k < len(s) and s[k] in b" \t\r\n":
                    k += 1
                if m and s.startswith(b"=", k) and not s.startswith(b"==", k):
                    self.i = k + 1
                    out[m.group().decode()] = self.value()
                else:
                    n += 1
                    out[n] = self.value()
            self._ws()
            if self.s[self.i:self.i + 1] in (b",", b";"):
                self.i += 1


def read_sv(path: Path, raw: bool = False) -> dict | None:
    """The globals of one SavedVariables file, None when absent or unreadable."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        return LuaReader(data, raw).document()
    except (LuaError, IndexError, ValueError) as e:
        log(f"cannot parse {path.name}: {e}")
        return None


def read_sv_versions(path: Path, raw: bool = False) -> list[dict]:
    """The file and the client's previous copy (.bak), newest first."""
    out = []
    for p in (path, path.with_name(path.name + ".bak")):
        d = read_sv(p, raw)
        if d is not None:
            out.append(d)
    return out


# ---------------------------------------------------------------------------
# CBOR (RFC 8949): Auctionator serializes its price database with
# C_EncodingUtil.SerializeCBOR. Lua strings come out as byte strings.
# ---------------------------------------------------------------------------

class CborError(ValueError):
    pass


def cbor_decode(data: bytes):
    def item(i: int):
        if i >= len(data):
            raise CborError("truncated")
        ib = data[i]
        mt, ai = ib >> 5, ib & 31
        i += 1
        if mt == 7:
            if ai == 20:
                return False, i
            if ai == 21:
                return True, i
            if ai in (22, 23):
                return None, i
            if ai == 25:
                return struct.unpack(">e", data[i:i + 2])[0], i + 2
            if ai == 26:
                return struct.unpack(">f", data[i:i + 4])[0], i + 4
            if ai == 27:
                return struct.unpack(">d", data[i:i + 8])[0], i + 8
            raise CborError(f"simple value {ai} at byte {i - 1}")
        if ai < 24:
            n = ai
        elif ai in (24, 25, 26, 27):
            size = 1 << (ai - 24)
            n = int.from_bytes(data[i:i + size], "big")
            i += size
        elif ai == 31:
            n = None  # indefinite length, up to a 0xff break
        else:
            raise CborError(f"bad length {ai} at byte {i - 1}")
        if mt == 0:
            return n, i
        if mt == 1:
            return -1 - n, i
        if mt in (2, 3):
            if n is None:
                parts = []
                while data[i] != 0xFF:
                    part, i = item(i)
                    parts.append(part)
                return "".join(parts), i + 1
            return data[i:i + n].decode("utf-8", errors="replace"), i + n
        if mt == 4:
            out = []
            while (n is None and data[i] != 0xFF) or (n is not None and len(out) < n):
                v, i = item(i)
                out.append(v)
            return out, (i + 1 if n is None else i)
        if mt == 5:
            m = {}
            count = 0
            while (n is None and data[i] != 0xFF) or (n is not None and count < n):
                k, i = item(i)
                v, i = item(i)
                m[k] = v
                count += 1
            return m, (i + 1 if n is None else i)
        if mt == 6:
            return item(i)  # a tag: keep the tagged value
        raise CborError(f"major type {mt}")

    try:
        return item(0)[0]
    except (IndexError, struct.error) as e:
        raise CborError(str(e)) from None


# ---------------------------------------------------------------------------
# Quest titles and item names from the data addons Polestar installs
# (Interface/AddOns, read only). Records are "<id>\t<name>;<more fields>",
# the name %XX-escaped (and quoted with ' in Polestar_QuestDB).
# ---------------------------------------------------------------------------

_DB_NAME = re.compile(r"^(\d+)\t'?([^;\n]*)", re.M)
NAME_FILES = {  # first one that names an id wins
    "Quests": ["LibForeverData/Data/Quests.lua", "Polestar_QuestDB/Quests.lua"],
    "Items": ["LibForeverData/Data/Items.lua", "Polestar_QuestDB/Items.lua"],
    # Creature names for the deaths' likely killer and the kills.
    "Npcs": ["LibForeverData/Data/Npcs.lua"],
}
NAMES: dict[str, dict[int, str]] = {}


def names(kind: str) -> dict[int, str]:
    """id -> name for kind "Quests", "Items" or "Npcs"; empty when no data addon is
    installed (quest names then come from the Recorder's own `text` records)."""
    if kind not in NAMES:
        found: dict[int, str] = {}
        for rel in NAME_FILES[kind]:
            try:
                text = (WOW / "Interface" / "AddOns" / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in _DB_NAME.finditer(text):
                if m.group(2):
                    found.setdefault(int(m.group(1)), urllib.parse.unquote(m.group(2)))
        NAMES[kind] = found
    return NAMES[kind]


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def iso(epoch: float) -> str:
    dt = datetime.fromtimestamp(epoch, tz=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def char_of(display: str) -> str:
    """'Testpriest Fixture' -> 'Testpriest-Fixture', the form of the client's character folders."""
    return display.strip().replace(" ", "-")


def player_of(raw: str) -> tuple[str, str | None]:
    """A player as a source names them -> (player, realm). Forever names are
    'First Surname'; a cross-realm name carries '-Realm'. The player field
    uses the character-folder form, so it compares with `char`."""
    name, _, realm = raw.partition("-")
    return char_of(name), (realm or None)


def valid_char(name) -> bool:
    """A character name the app accepts (wowstats/names.py, the same rule:
    tests/test_names.py keeps them equal). The publisher writes each
    character's files under its name, so /ingest refuses any other."""
    if not isinstance(name, str) or not 0 < len(name) <= 64:
        return False
    if not unicodedata.category(name[0]).startswith("L"):
        return False
    return all(c in " -'0123456789" or unicodedata.category(c).startswith("L") for c in name)


class Out:
    """Collects events: to stdout (dry run) or a spool file."""

    def __init__(self, dry: bool):
        self.dry = dry
        self.counts: dict[str, int] = {}
        self.skipped = 0
        self.chars: dict[str, int] = {}
        self.fh = None
        self.tmp: Path | None = None

    def emit(self, ev: dict) -> None:
        ev = {k: v for k, v in ev.items() if v is not None and v != ""}
        if "char" in ev and not valid_char(ev["char"]):
            self.skipped += 1  # the app would refuse the whole batch
            return
        key = ev["feed"] + (":" + ev["kind"] if "kind" in ev else "")
        self.counts[key] = self.counts.get(key, 0) + 1
        self.chars[ev.get("char") or "?"] = self.chars.get(ev.get("char") or "?", 0) + 1
        line = json.dumps(ev, ensure_ascii=False, separators=(",", ":"))
        if self.dry:
            print(line)
            return
        if self.fh is None:
            SPOOL.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=SPOOL, prefix="partial-", suffix=".partial")
            self.fh, self.tmp = os.fdopen(fd, "w", encoding="utf-8"), Path(name)
        self.fh.write(line + "\n")

    def close(self) -> Path | None:
        if self.fh is None:
            return None
        self.fh.close()
        dest = SPOOL / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".ndjson")
        os.replace(self.tmp, dest)
        return dest

    def summary(self) -> str:
        """Events per feed and per character, for the log."""
        feeds: dict[str, int] = {}
        for key, n in self.counts.items():
            feeds[key.split(":")[0]] = feeds.get(key.split(":")[0], 0) + n
        return (f"{sum(feeds.values())} events; per feed {json.dumps(dict(sorted(feeds.items())))}; "
                f"per character {json.dumps(dict(sorted(self.chars.items())), ensure_ascii=False)}"
                + (f"; {self.skipped} skipped (a character name the app refuses)" if self.skipped else ""))

    def discard(self) -> None:
        if self.fh is not None:
            self.fh.close()
            self.tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Polestar Recorder records (Polestar_Recorder/Recorder.lua, schema 2)
# ---------------------------------------------------------------------------

# Field names per kind, in the order the addon's ns.Rec calls write them.
# "P" is the position: map, x, y (hundredths of a percent) or three empty fields.
KINDS = {
    "session": ["char_id", "race", "class", "faction", "sex", "level", "locale", "build", "interface",
                "region", "ruleset", "recorder_version", "polestar_version", "packs"],
    "dropped": ["chunks_dropped"],
    "map": ["map", "map_type", "parent_map_id", "continent", "top", "left", "bottom", "right"],
    "mark": ["P", "text"],
    "where": ["where_kind", "where_id", "subzone", "indoors"],
    "poi": ["quest_id", "done", "P"],
    "imap": ["world_map_id", "inst_map_id", "group", "floor"],
    "open": ["spell_id", "P", "npc_kind", "npc_id"],
    "rep": ["faction_id", "reaction", "standing", "rep_gained"],
    "skill": ["skill_id", "rank", "max_rank"],
    "explore": ["area_id", "xp_reward", "P"],
    "craft": ["spell_id", "item_id", "count"],
    "learn": ["spell_id"],
    "achieve": ["achievement_id"],
    "step": ["guide", "step", "reason", "P"],
    "combat": ["on", "P"],
    "afk": ["on"],
    "revive": ["P", "how"],
    "objectives": ["quest_id", "objectives"],
    "escort": ["quest_id", "on", "P"],
    "progress": ["quest_id", "objective", "have", "need", "P"],
    "complete": ["quest_id"],
    "fail": ["quest_id", "P"],
    "offer": ["npc_kind", "npc_id", "P", "quests_available", "quests_active", "quests_log", "quests_done",
              "quests_not_offered"],
    "text": ["quest_id", "title", "objective", "text"],
    "detail": ["quest_id", "npc_kind", "npc_id", "P", "item_id"],
    "accept": ["quest_id", "npc_kind", "npc_id", "P", "level", "xp_reward", "money_copper", "items", "choices"],
    "turnin": ["quest_id", "npc_kind", "npc_id", "P", "level", "xp_reward", "money_copper", "item"],
    "abandon": ["quest_id"],
    "gossip": ["npc_kind", "npc_id", "P", "gossip_id", "text"],
    "unit": ["npc_id", "level", "classification", "reaction", "P", "seen_by"],
    "unitx": ["npc_id", "spawn_uid", "P"],
    "rare": ["npc_id", "level", "classification", "P", "seen_by"],
    "treasure": ["object_id", "P"],
    "kill": ["npc_id", "level", "classification", "P", "xp_reward", "rested_bonus", "tapped", "quests"],
    "spawn": ["npc_id", "spawn_uid", "P"],
    "engage": ["encounter_id", "difficulty", "size"],
    "encounter": ["encounter_id", "success", "difficulty", "size"],
    "gather": ["object_id", "P", "how", "skill_line", "skill_rank"],
    "loot": ["npc_kind", "npc_id", "P", "gather", "items", "wanted"],
    "enter": ["instance_id", "instance_kind", "difficulty", "P"],
    "pos": ["P", "flags", "speed"],
    "ride": ["on", "P"],
    "zone": ["P", "zone", "subzone"],
    "xp": ["xp", "xp_max", "rested_xp"],
    "level": ["level", "P", "played_s"],
    "death": ["P", "npc_id", "npc_level"],
    "flightmaster": ["npc_id", "P", "taxi_node", "known_nodes"],
    "taxi": ["from_node", "to_node"],
    "land": ["to_node", "seconds", "P"],
    "bind": ["npc_id", "P"],
    "trainer": ["npc_id", "P", "services"],
    "vendor": ["npc_id", "P", "items"],
    "cast": ["spell_id", "item_id", "P", "npc_kind", "npc_id"],
    "rest": ["rest_kind", "seconds"],
    "group": ["size", "raid"],
    "refuse": ["P", "npc_kind", "npc_id", "error"],
}
TEXT_FIELDS = {
    "char_id", "race", "class", "faction", "locale", "ruleset", "recorder_version", "polestar_version", "packs",
    "text", "where_kind", "subzone", "guide", "reason", "how", "objectives", "npc_kind", "quests_available",
    "quests_active", "quests_log", "quests_done", "quests_not_offered", "title", "objective", "items", "choices",
    "item", "classification", "reaction", "seen_by", "spawn_uid", "quests", "gather", "wanted", "instance_kind",
    "zone", "known_nodes", "services", "rest_kind", "error", "skill_line",
}
BOOL_FIELDS = {"indoors", "done", "on", "tapped", "success", "raid", "wanted"}
QUEST_NAME_KINDS = {"accept", "turnin", "complete", "abandon", "fail"}
LOGIN_GAP_S = 120  # more than a /reload: the first record after it is a login


def _scalar(name: str, v: str):
    if name in BOOL_FIELDS and v in ("0", "1"):
        return v == "1"
    if name in TEXT_FIELDS:
        return v
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


def parse_record(kind: str, fields: list[str]) -> dict:
    spec = KINDS.get(kind)
    out: dict = {}
    if spec is None:
        if fields:
            out["args"] = "\t".join(fields)
        return out
    i = 0
    for name in spec:
        if name == "P":
            m, x, y = (fields[i:i + 3] + ["", "", ""])[:3]
            i += 3
            if m:
                out["map_id"] = int(m)
            if x and y:
                out["x"], out["y"] = int(x) / 100, int(y) / 100
            continue
        if i < len(fields) and fields[i] != "":
            out[name] = _scalar(name, fields[i])
        i += 1
    if i < len(fields) and any(fields[i:]):
        out["args"] = "\t".join(fields[i:])
    if kind == "pos" and isinstance(out.get("speed"), int):
        out["speed"] = out["speed"] / 10  # tenths of a yard per second
    if kind == "map" and "map" in out:  # a map's projection, not a position
        out["map_id"] = out.pop("map")
    return out


def load_chunks(sv_dirs: list[Path]) -> tuple[dict, dict[int, int]]:
    """(charId, seq) -> (epoch, lines), from every Polestar_Recorder.lua(.bak)
    found, and map id -> map type (Enum.UIMapType: 2 continent, 3 zone, 4 dungeon)."""
    chunks: dict = {}
    map_types: dict[int, int] = {}
    for d in sv_dirs:
        for db in read_sv_versions(d / "Polestar_Recorder.lua"):
            rdb = db.get("PolestarRecorderDB")
            if not isinstance(rdb, dict) or not isinstance(rdb.get("chunks"), dict):
                continue
            for text in (rdb.get("maps") or {}).values():
                f = str(text).split("\t")
                if len(f) > 1 and f[0].isdigit() and f[1].isdigit():
                    map_types[int(f[0])] = int(f[1])
            for text in rdb["chunks"].values():
                add_chunk(chunks, text)
    return chunks, map_types


def add_chunk(chunks: dict, text) -> None:
    """One chunk's text into chunks[(charId, seq)] = (epoch, lines), whatever
    its source (the SavedVariables or Polestar Client's sent.jsonl): the same
    text gives the same records, so the same events. The longest version wins:
    the chunk being written grows until it rolls."""
    if not isinstance(text, str):
        return
    lines = text.split("\n")
    head = lines[0].split("\t")
    if head[0] != "chunk" or len(head) < 5:
        return
    try:
        key, epoch = (head[4], int(head[2])), int(head[3])
    except ValueError:
        return
    if key not in chunks or len(lines) > len(chunks[key][1]):
        chunks[key] = (epoch, lines)


# Polestar Client (the uploader) keeps every chunk its server accepted in the
# last 8 days in sent.jsonl, one {"at", "install", "seq", "text"} per line,
# where `text` is the chunk exactly as in PolestarRecorderDB.chunks. The
# Recorder deletes acked chunks from its SavedVariables at the next login, so
# a chunk the WTF archive never saw (a run that didn't happen, the history
# before the shipper was installed) is still there for a week. Read only.
CLIENT_ID = "gg.polestar.client"
CLIENT: dict = {}  # its chunks, loaded by main() for the current tree's extraction


def client_dir() -> Path:
    """Tauri's app_data_dir() for the client (apps/client/src-tauri/src/lib.rs in
    github.com/fitzforge/polestar): <data dir>/gg.polestar.client."""
    if CONFIG.get("polestar_client_dir"):
        return Path(os.path.expandvars(str(CONFIG["polestar_client_dir"]))).expanduser()
    if WINDOWS:
        base = Path(os.environ.get("APPDATA") or HOME / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = HOME / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local" / "share")
    return base / CLIENT_ID


def client_chunks(d: Path | None = None) -> dict:
    """(charId, seq) -> (epoch, lines) from the client's sent.jsonl. No client
    (no file) is the usual case for players who don't run it: empty."""
    chunks: dict = {}
    p = (d or client_dir()) / "sent.jsonl"
    try:
        data = p.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return chunks
    except OSError as e:
        log(f"polestar client: can't read {p}: {e}")
        return chunks
    for line in data.splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            add_chunk(chunks, rec.get("text"))
    return chunks


def client_chunks_of(acct: Path) -> dict:
    """CLIENT's chunks of the characters whose folders are in acct (by the
    Recorder's per-character id, as extract_tree maps them); a character no
    account folder knows goes with the first account."""
    if not CLIENT:
        return {}
    accts = accounts(acct.parent.parent)
    owner: dict[str, Path] = {}
    for a in accts:
        for d in char_sv_dirs(a):
            for db in read_sv_versions(d / "Polestar_Recorder.lua"):
                cid = (db.get("PolestarRecorderCharDB") or {}).get("id")
                if isinstance(cid, str):
                    owner.setdefault(cid, a)
    return {k: v for k, v in CLIENT.items() if owner.get(k[0], accts[0]) == acct}


def merge_chunks(chunks: dict, more: dict) -> None:
    for key, (epoch, lines) in more.items():
        if key not in chunks or len(lines) > len(chunks[key][1]):
            chunks[key] = (epoch, lines)


def sessions_of(chunks: dict) -> list[dict]:
    """Records grouped into play sessions per character. A chunk continues
    the previous one when it opened as that one rolled (5 min age or 16 KiB)
    or right after a /reload; a gap means the player logged out."""
    by_char: dict[str, list] = {}
    for (cid, seq), (epoch, lines) in chunks.items():
        recs = []
        for idx, line in enumerate(lines):
            f = line.split("\t")
            if len(f) < 2 or idx == 0:
                continue
            try:
                dt = int(f[1])
            except ValueError:
                continue
            recs.append({"idx": idx, "kind": f[0], "t": epoch + dt / 10, "line": line, "fields": f[2:]})
        by_char.setdefault(cid, []).append({"seq": seq, "epoch": epoch, "recs": recs})
    sessions = []
    for cid, lst in by_char.items():
        lst.sort(key=lambda c: c["seq"])
        cur = None
        for c in lst:
            last_t = cur["chunks"][-1]["recs"][-1]["t"] if cur and cur["chunks"][-1]["recs"] else None
            prev_epoch = cur["chunks"][-1]["epoch"] if cur else None
            cont = cur is not None and (c["epoch"] - prev_epoch <= 330 or (last_t is not None and c["epoch"] - last_t <= 120))
            if not cont:
                cur = {"char_id": cid, "chunks": []}
                sessions.append(cur)
            cur["chunks"].append(c)
    for s in sessions:
        s["recs"] = [(c["seq"], r) for c in s["chunks"] for r in c["recs"]]
        ts = [r["t"] for _, r in s["recs"]] or [s["chunks"][0]["epoch"]]
        s["start"], s["end"] = s["chunks"][0]["epoch"], max(ts)
    sessions.sort(key=lambda s: s["start"])
    return sessions


# Records whose creature gets its name (LibForeverData's NPC table): a death's
# likely killer, the units around it and the kills.
NPC_NAMED = {"death", "unit", "kill", "rare", "loot"}


def extract_recorder(sessions: list[dict], map_types: dict, char_ids: dict, state: dict, out: Out) -> None:
    done = state.setdefault("rec", {})
    map_names = state.setdefault("map_names", {})
    last_xp = state.setdefault("last_xp", {})
    for s in sessions:  # map names first: a zone record names the map it was written on,
        for _, r in s["recs"]:  # but at login that can still be the continent's (type 2)
            if r["kind"] == "zone":
                p = parse_record("zone", r["fields"])
                if "map_id" in p and p.get("zone") and map_types.get(p["map_id"], 0) >= 3:
                    map_names[str(p["map_id"])] = p["zone"]
    first_xp_t: dict[str, float] = {}
    for s in sessions:
        for _, r in s["recs"]:
            if r["kind"] == "xp":
                first_xp_t.setdefault(s["char_id"], r["t"])
    prev_xp = {cid: v for cid, v in last_xp.items() if cid in first_xp_t and v[0] < first_xp_t[cid]}
    # Quest titles: Polestar_QuestDB when installed, else the ones the
    # Recorder saw itself (`text` records carry the title).
    quest_titles = state.setdefault("quest_titles", {})
    for s in sessions:
        for _, r in s["recs"]:
            if r["kind"] == "text":
                p = parse_record("text", r["fields"])
                if isinstance(p.get("quest_id"), int) and p.get("title"):
                    quest_titles[str(p["quest_id"])] = p["title"]
    qdb = names("Quests")
    # The end of each character's previous session, to tell a login from a
    # chunk that only continues one (every chunk opens with a `session` record).
    last_end = state.setdefault("last_rec_t", {})
    run_end: dict[str, float] = {}

    for s in sessions:
        cid = s["char_id"]
        char = char_ids.get(cid, f"charid-{cid}")
        recs = s["recs"]
        zone_now = None
        prev_step_t = s["start"]
        before = run_end.get(cid)
        if before is None and cid in last_end and last_end[cid] < s["start"]:
            before = last_end[cid]
        login_t = recs[0][1]["t"] if recs else None
        if before is not None and login_t is not None and login_t - before <= LOGIN_GAP_S:
            login_t = None  # a /reload or a chunk roll, not a login
        run_end[cid] = max(run_end.get(cid, 0), s["end"])
        for i, (seq, r) in enumerate(recs):
            kind = r["kind"]
            ev = parse_record(kind, r["fields"])
            if i == 0 and login_t is not None:
                ev["login"] = True
            if kind in QUEST_NAME_KINDS and isinstance(ev.get("quest_id"), int):
                ev["quest_name"] = qdb.get(ev["quest_id"]) or quest_titles.get(str(ev["quest_id"]))
            if kind == "zone":
                zone_now = ev.get("zone") or zone_now
                nxt = next((r2["t"] for _, r2 in recs[i + 1:] if r2["kind"] == "zone"), s["end"])
                ev["duration_s"] = round(nxt - r["t"], 1)
            elif kind == "step":
                ev["duration_s"] = round(r["t"] - prev_step_t, 1)
                prev_step_t = r["t"]
            elif kind == "taxi":
                land = next((r2 for _, r2 in recs[i + 1:] if r2["kind"] == "land"), None)
                if land:
                    secs = parse_record("land", land["fields"]).get("seconds")
                    ev["duration_s"] = secs if isinstance(secs, (int, float)) else round(land["t"] - r["t"], 1)
            elif kind == "xp" and isinstance(ev.get("xp"), int) and isinstance(ev.get("xp_max"), int):
                p = prev_xp.get(cid)
                if p:
                    _, pxp, pmax = p
                    if ev["xp_max"] == pmax and ev["xp"] >= pxp:
                        ev["xp_gained"] = ev["xp"] - pxp
                    else:  # a level went by: the rest of the old bar plus the new one
                        ev["xp_gained"] = max(0, pmax - pxp) + ev["xp"]
                prev_xp[cid] = [r["t"], ev["xp"], ev["xp_max"]]
            if isinstance(ev.get("npc_id"), int) and kind in NPC_NAMED:
                nm = names("Npcs").get(ev["npc_id"])
                if nm:
                    ev["npc_name"] = nm
            if kind == "loot" and isinstance(ev.get("items"), str):
                # The loot history's item names (LibForeverData's item table):
                # the server's own tables don't name every Forever item.
                inames = {i: names("Items").get(int(i)) for i, _, _ in
                          (part.partition(":") for part in ev["items"].split(";")) if i.isdigit()}
                inames = {i: n for i, n in inames.items() if n}
                if inames:
                    ev["item_names"] = json.dumps(inames, ensure_ascii=False, separators=(",", ":"))
            if kind != "map":
                if "map_id" in ev and str(ev["map_id"]) in map_names:
                    ev.setdefault("zone", map_names[str(ev["map_id"])])
                elif zone_now:
                    ev.setdefault("zone", zone_now)
            key = f"{cid}:{seq}"
            if r["idx"] <= done.get(key, 0):
                continue
            out.emit({"_time": iso(r["t"]), "feed": "wow-recorder", "char": char, "kind": kind,
                      "_msg": r["line"], "source_id": f"rec:{cid}:{seq}:{r['idx']}", **ev})
        for c in s["chunks"]:
            key = f"{cid}:{c['seq']}"
            last_idx = c["recs"][-1]["idx"] if c["recs"] else 0
            done[key] = max(done.get(key, 0), last_idx)
    for cid, v in prev_xp.items():
        if cid not in last_xp or v[0] >= last_xp[cid][0]:
            last_xp[cid] = v
    for cid, t in run_end.items():
        last_end[cid] = max(last_end.get(cid, 0), t)


def where_at(sessions: list[dict], char_ids: dict, map_names: dict, t: float):
    """(char, zone, map_id) of the session playing at epoch t, from the Recorder."""
    for s in sessions:
        if s["start"] - 60 <= t <= s["end"] + 60:
            zone = map_id = None
            for _, r in s["recs"]:
                if r["t"] > t:
                    break
                if r["kind"] in ("zone", "pos"):
                    p = parse_record(r["kind"], r["fields"])
                    map_id = p.get("map_id", map_id)
                    if r["kind"] == "zone" and p.get("zone"):
                        zone = p["zone"]
            if map_id is not None and str(map_id) in map_names:
                zone = map_names[str(map_id)]
            return char_ids.get(s["char_id"]), zone, map_id
    return None, None, None


# ---------------------------------------------------------------------------
# Polestar (account and character SavedVariables)
# ---------------------------------------------------------------------------

def extract_polestar(acct: Path, char_dirs: list[Path], sessions, char_ids, state, out: Out,
                     looks: dict | None = None) -> str | None:
    looks = looks or {}
    seen_keys = set(state.setdefault("emitted", []))
    new_keys: list[str] = []

    def once(key: str) -> bool:
        if key in seen_keys:
            return False
        seen_keys.add(key)
        new_keys.append(key)
        return True

    map_names = state.get("map_names", {})
    alts_latest = None
    for db in read_sv_versions(acct / "SavedVariables" / "Polestar.lua"):
        pdb = db.get("PolestarDB") or {}
        chars = ((pdb.get("alts") or {}).get("chars")) or {}
        for key, c in chars.items():
            if not isinstance(c, dict) or not isinstance(c.get("seen"), (int, float)):
                continue
            char = char_of(c.get("name") or str(key).split("-")[0])
            if alts_latest is None or c["seen"] > alts_latest[1]:
                alts_latest = (char, c["seen"])
            sid = f"character:{char}:{int(c['seen'])}"
            if not once(sid):
                continue
            out.emit({"_time": iso(c["seen"]), "feed": "wow-character", "char": char, "source_id": sid,
                      "_msg": f"{char} level {c.get('level')} {c.get('xp')}/{c.get('xpMax')} xp, "
                              f"{(c.get('money') or 0) // 10000}g in {c.get('zone')}",
                      "level": c.get("level"), "xp": c.get("xp"), "xp_max": c.get("xpMax"),
                      "money_copper": c.get("money"), "zone": c.get("zone"), "class": c.get("class"),
                      "race": c.get("race"), "faction": c.get("faction"), "realm": c.get("realm"),
                      "bind": c.get("bind"), "resting": c.get("resting"),
                      **({"looks": json.dumps(looks[char], separators=(",", ":"))} if char in looks else {})})
        # Players Polestar's version probe asked: same faction, targeted or moused
        # over out of combat, outside groups, guilds, instances and rest areas;
        # one per player a week, at most 10 a session (Polestar/Core/Update.lua).
        probed = ((pdb.get("update") or {}).get("probed")) or {}
        for raw, at in probed.items():
            if not isinstance(at, (int, float)) or not isinstance(raw, str):
                continue
            player, realm = player_of(raw)
            sid = f"player:probed:{player}:{int(at)}"
            if not once(sid):
                continue
            char, zone, map_id = where_at(sessions, char_ids, map_names, at)
            char = char or (alts_latest[0] if alts_latest else None)
            out.emit({"_time": iso(at), "feed": "wow-player", "char": char, "source_id": sid,
                      "_msg": f"seen {player}" + (f" in {zone}" if zone else ""),
                      "player": player, "player_realm": realm, "context": "seen", "source": "polestar-probe",
                      "zone": zone, "map_id": map_id})

    for cdir in char_dirs:
        char = cdir.parent.name
        for db in read_sv_versions(cdir / "Polestar.lua"):
            cdb = db.get("PolestarCharDB") or {}
            splits = (((cdb.get("qol") or {}).get("xp") or {}).get("splits")) or {}
            for level, sp in splits.items():
                if not isinstance(sp, dict) or not isinstance(sp.get("at"), (int, float)):
                    continue
                sid = f"level:{char}:{level}"
                if not once(sid):
                    continue
                secs = sp.get("seconds")
                out.emit({"_time": iso(sp["at"]), "feed": "wow-level", "char": char, "source_id": sid,
                          "_msg": f"{char} finished level {level} in "
                                  f"{round(secs / 3600, 2) if isinstance(secs, (int, float)) else '?'} h played, "
                                  f"{sp.get('quests')} quests, {sp.get('kills')} kills",
                          "level": level, "seconds": secs, "xp": sp.get("xp"), "quests": sp.get("quests"),
                          "kills": sp.get("kills"), "partial": bool(sp.get("partial", False))})
            runs = cdb.get("instanceRuns") or {}
            for run in runs.values():
                if not isinstance(run, dict) or not isinstance(run.get("entered"), (int, float)):
                    continue
                if not isinstance(run.get("left"), (int, float)):
                    continue  # still inside: emitted once it is over
                sid = f"dungeon:{char}:{int(run['entered'])}"
                if not once(sid):
                    continue
                out.emit({"_time": iso(run["entered"]), "feed": "wow-dungeon", "char": char, "source_id": sid,
                          "_msg": f"{char} ran {run.get('name')} at level {run.get('level')}: "
                                  f"{round((run.get('time') or 0) / 60)} min, {run.get('xp')} xp",
                          "dungeon_id": run.get("id"), "name": run.get("name"), "dungeon_kind": run.get("kind"),
                          "level": run.get("level"), "seconds": run.get("time"), "xp": run.get("xp"),
                          "left": iso(run["left"]), "resumed": iso(run["resumed"]) if run.get("resumed") else None})
    state["emitted"] = sorted(seen_keys)
    return alts_latest[0] if alts_latest else None


# ---------------------------------------------------------------------------
# Gear and professions: Syndicator (equipped items), CharacterExport-Forever
# and Polestar (professions), GearQuestForever (tracked upgrades)
# ---------------------------------------------------------------------------

# Syndicator's `equipped` is a Lua array from inventory slot 0 (ammo): its
# index is the slot + 1. Slots 20 and up are bags, not gear.
SLOTS = {1: "Head", 2: "Neck", 3: "Shoulder", 4: "Shirt", 5: "Chest", 6: "Waist", 7: "Legs", 8: "Feet",
         9: "Wrist", 10: "Hands", 11: "Finger", 12: "Finger", 13: "Trinket", 14: "Trinket", 15: "Back",
         16: "Main hand", 17: "Off hand", 18: "Ranged", 19: "Tabard"}
NO_ITEM_LEVEL = {4, 19}  # shirt and tabard: cosmetic, left out of the average
SEXES = {2: "male", 3: "female"}  # UnitSex(), as Syndicator saves it; 1 is "unknown"
PROFESSIONS = {129: "First Aid", 164: "Blacksmithing", 165: "Leatherworking", 171: "Alchemy", 182: "Herbalism",
               185: "Cooking", 186: "Mining", 197: "Tailoring", 202: "Engineering", 333: "Enchanting",
               356: "Fishing", 393: "Skinning"}
_ITEMDB_STATS = re.compile(r'^\[(\d+)\]="(\d+) (\d+) (\d+) (\d+) ', re.M)
# Names: not a stats line, a quest line ("44 40 1 ...") or a source ("q1031").
_ITEMDB_NAME = re.compile(r'^\[(\d+)\]="((?!\d+ \d+ |[qdc]\d)[^"]*)"', re.M)
_LINK_NAME = re.compile(r"\|h\[([^\]]*)\]\|h")
# item:<id>:<enchantId>:<gem1>...: the enchant is the link's second field.
_LINK_ITEM = re.compile(r"(?:\|H|(?<![A-Za-z]))item:(\d+):(-?\d*)")


def link_enchant(link) -> int | None:
    """The enchant id in an item link: 0 when the field is empty or 0 (no
    enchant), None when there's no item link at all (unknown)."""
    m = _LINK_ITEM.search(str(link or ""))
    if not m:
        return None
    return int(m.group(2)) if m.group(2).lstrip("-").isdigit() and int(m.group(2)) > 0 else 0
ITEMDB: dict[str, dict] = {}


def item_db() -> dict[str, dict]:
    """item id -> (item level, required level) and item id -> name, from
    Polestar_ItemDB: Forever.lua (Forever's own stats) over Items.lua (the
    Classic Era client tables). Empty without the addon."""
    if not ITEMDB:
        stats: dict[int, tuple[int, int]] = {}
        nm: dict[int, str] = {}
        for rel in ("Polestar_ItemDB/Forever.lua", "Polestar_ItemDB/Items.lua"):
            try:
                text = (WOW / "Interface" / "AddOns" / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # Only DB.Register's table holds item stats; RegisterLeveling's
            # quest table has lines of the same shape keyed by quest id.
            start = text.find("DB.Register(")
            end = text.find("\n})", start)
            block = text[start:end] if start >= 0 else ""
            for m in _ITEMDB_STATS.finditer(block):
                stats.setdefault(int(m.group(1)), (int(m.group(3)), int(m.group(4))))
            for m in _ITEMDB_NAME.finditer(text):
                nm.setdefault(int(m.group(1)), m.group(2))
        ITEMDB.update(stats=stats, names=nm)
    return ITEMDB


def item_info(item_id: int, link_name: str | None = None) -> dict:
    db = item_db()
    ilvl, req = (db.get("stats") or {}).get(item_id, (None, None))
    name = link_name or names("Items").get(item_id) or (db.get("names") or {}).get(item_id)
    return {"item_id": item_id, "name": name, "item_level": ilvl, "req_level": req or None}


def _first(*dbs: dict | None):
    return next((d for d in dbs if isinstance(d, dict)), None)


def _adler32(b: bytes) -> str:
    return f"{zlib.adler32(b):08x}"


def simbench_export(cdir: Path) -> dict[str, str] | None:
    """The fields of Vulcan SimBench's last export (VulcanSimBenchCharDB.last,
    written by /simx: "FSX1:k=v;...#<adler32>", its Format.lua), or None when
    there is none or its checksum doesn't match (a cut or damaged string)."""
    sb = _first(*read_sv_versions(cdir / "VulcanSimBench.lua")) or {}
    last = (sb.get("VulcanSimBenchCharDB") or {}).get("last")
    if not isinstance(last, str):
        return None
    m = re.search(r"(FSX\d+:[^#]*)#([0-9a-fA-F]{8})", last)
    if not m or _adler32(m.group(1).encode()) != m.group(2).lower():
        return None
    return dict(kv.split("=", 1) for kv in m.group(1).split(":", 1)[1].split(";") if "=" in kv)


def simbench_talents(cdir: Path) -> list[dict] | None:
    """Talent points per tree from SimBench's last export (`tal`, `tn`). The
    game saves no talents anywhere else; Polestar's own Automatic picks the
    tree with the most points (Gear.lua DetectSpec), and so does the server
    with these. [{"tree": "Shadow", "points": 21}, ...] in tree order. Forever's
    client can't give them (its talents are trait nodes: simbench_ranks)."""
    fields = simbench_export(cdir) or {}
    points = [int(p) if p.isdigit() else None for p in fields.get("tal", "").split("-")] if fields.get("tal") else []
    names = fields.get("tn", "").replace("+", " ").split(",") if fields.get("tn") else []
    if not points:
        return None
    return [{"tree": names[i] if i < len(names) and names[i] else None, "points": pt} for i, pt in enumerate(points)]


def simbench_ranks(cdir: Path) -> dict | None:
    """Forever's talents from SimBench's last export: each learned talent as
    its spell ID and rank (`ts`, "spellID:rank,..."), how many talent nodes
    the add-on read (`tsn`; with no `ts`, none spent), and the level and local
    date of the export (`lvl`, `d`). The server lays them out on the class's
    trait tree (wowstats.talents). None when the export has neither."""
    f = simbench_export(cdir) or {}
    if "ts" not in f and "tsn" not in f:
        return None
    ranks = []
    for part in f.get("ts", "").split(","):
        sp, _, rk = part.partition(":")
        if sp.isdigit() and rk.isdigit() and int(rk) > 0:
            ranks.append({"spell": int(sp), "rank": int(rk)})
    nodes = f.get("tsn", "")
    lvl = f.get("lvl", "")
    day = f.get("d", "")
    return {"ranks": ranks, "nodes": int(nodes) if nodes.isdigit() else None,
            "level": int(lvl) if lvl.isdigit() else None,
            "date": day if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else None}


def gear_of(acct: Path, cdir: Path, char: str) -> dict | None:
    """The character sheet for one character, from the account's and the
    character's SavedVariables. None when no addon knows its gear or skills."""
    first = char.split("-", 1)[0]
    synd = next(iter(read_sv_versions(acct / "SavedVariables" / "Syndicator.lua")), None) or {}
    sheet = None
    for c in (((synd.get("SYNDICATOR_DATA") or {}).get("Characters")) or {}).values():
        det = (c or {}).get("details") or {}
        if isinstance(det, dict) and char_of(str(det.get("character") or "")) == char:
            sheet = c
            break
    items = []
    det = (sheet or {}).get("details") or {}
    for idx, it in ((sheet or {}).get("equipped") or {}).items():
        try:
            slot = int(idx) - 1
        except (TypeError, ValueError):
            continue
        if slot not in SLOTS or not isinstance(it, dict) or not isinstance(it.get("itemID"), int):
            continue
        m = _LINK_NAME.search(str(it.get("itemLink") or ""))
        info = item_info(it["itemID"], m.group(1) if m else None)
        items.append({"slot": slot, "slot_name": SLOTS[slot], **info,
                      "quality": it.get("quality") if isinstance(it.get("quality"), int) else None,
                      # Since 0.2.2; the server reads a missing key as "unknown".
                      "enchant_id": link_enchant(it.get("itemLink"))})
    items.sort(key=lambda i: i["slot"])

    profs: dict[int, dict] = {}
    pole = next(iter(read_sv_versions(acct / "SavedVariables" / "Polestar.lua")), None) or {}
    for key, c in ((((pole.get("PolestarDB") or {}).get("alts") or {}).get("chars")) or {}).items():
        if isinstance(c, dict) and char_of(c.get("name") or str(key).split("-")[0]) == char:
            for sid, rank in (c.get("profs") or {}).items():
                if isinstance(sid, int) and isinstance(rank, (int, float)):
                    profs[sid] = {"skill_id": sid, "name": PROFESSIONS.get(sid), "rank": int(rank), "max_rank": None}
    export = next(iter(read_sv_versions(cdir / "CharacterExport-Forever.lua")), None) or {}
    for realm in ((export.get("CharacterExportForeverDBChar") or {}).get("professionCache") or {}).values():
        for who, cache in (realm or {}).items():
            if who != first or not isinstance(cache, dict):
                continue
            for p in cache.values():
                sid = p.get("professionID") if isinstance(p, dict) else None
                if not isinstance(sid, int):
                    continue
                cur = profs.setdefault(sid, {"skill_id": sid, "name": None, "rank": None, "max_rank": None})
                cur["name"] = cur["name"] or p.get("name")
                if isinstance(p.get("rank"), int):  # skills only go up: the higher reading is the newer
                    cur["rank"] = max(cur["rank"] or 0, p["rank"])
                if isinstance(p.get("maxRank"), int):
                    cur["max_rank"] = p["maxRank"]
                    # The export reads the cap only while the profession
                    # window is open (TRADE_SKILL_*): a rank trained since
                    # shows up only after the window is opened again. The
                    # server hedges a "train the next rank" move on this.
                    if isinstance(p.get("lastUpdated"), (int, float)) and p["lastUpdated"] > 0:
                        cur["max_rank_at"] = iso(p["lastUpdated"])
    professions = sorted((p for p in profs.values() if p["name"]), key=lambda p: (-(p["rank"] or 0), p["name"]))

    cls = det.get("className")
    spec = spec_from = None
    bis = next(iter(read_sv_versions(acct / "SavedVariables" / "ForeverLevelingBiS.lua")), None) or {}
    for key, c in (((bis.get("ForeverLevelingBiSDB") or {}).get("chars")) or {}).items():
        if char_of(str(key).split("-")[0]) == char and isinstance(c, dict) and isinstance(c.get("spec"), str):
            spec, spec_from = c["spec"], "ForeverLevelingBiS"
    gq = _first(*read_sv_versions(cdir / "GearQuestForever.lua")) or {}
    gq = gq.get("GearQuestForeverCharDB") or {}
    if not spec and isinstance(cls, str):
        spec = ((gq.get("specPick") or {}).get(cls))
        spec_from = "GearQuestForever" if spec else None
    talents = simbench_talents(cdir)
    ranks = simbench_ranks(cdir) or {}
    # Upgrades the player chose to hunt in GearQuestForever:
    # "gen:<class>:<item id>:<slot>:<level>:<spec>:<faction>" -> status.
    wanted = []
    for key, h in (gq.get("hunts") or {}).items():
        parts = str(key).split(":")
        if len(parts) < 5 or not isinstance(h, dict) or h.get("status") != "tracked" or not parts[2].isdigit():
            continue
        info = item_info(int(parts[2]))
        wanted.append({**info, "slot_name": parts[3], "level": int(parts[4]) if parts[4].isdigit() else None})
    wanted.sort(key=lambda w: (w["level"] or 0, w["slot_name"]))

    # Polestar's own gear settings (PolestarDB.settings.gear: Stat weights
    # per class, "auto" or a spec key; the least gain that counts) and the
    # player's weight edits (PolestarDB.gearAdjust[CLASS], Adjust.lua), for
    # the server's gear advice (docs/gear-reference.md).
    db = pole.get("PolestarDB") or {}
    token = str(cls or "").replace(" ", "").upper() or None
    gsets = ((db.get("settings") or {}).get("gear")) or {}
    pspec = (gsets.get("spec") or {}).get(token) if token and isinstance(gsets.get("spec"), dict) else None
    adjust = ((db.get("gearAdjust") or {}).get(token)) if token and isinstance(db.get("gearAdjust"), dict) else None
    # A list, not a nested object: VictoriaLogs would flatten an object into
    # dotted fields; it keeps a list as one JSON string.
    adjust = sorted(({"spec": sp, "stat": st, **{k: v for k, v in e.items()
                                                if k in ("x", "v") and isinstance(v, (int, float))}}
                     for sp, edits in (adjust or {}).items() if isinstance(edits, dict)
                     for st, e in edits.items() if isinstance(e, dict)),
                    key=lambda a: (str(a["spec"]), str(a["stat"])))
    min_upgrade = gsets.get("minUpgrade") if isinstance(gsets.get("minUpgrade"), (int, float)) else None

    if not items and not professions:
        return None
    recipes = recipes_of(acct, char)
    # Polestar's last class trainer visit: the level it was at (Engine.lua
    # classTrainVisit, set when a class trainer's window opens).
    pcdb = next(iter(read_sv_versions(cdir / "Polestar.lua")), None) or {}
    visit = (pcdb.get("PolestarCharDB") or {}).get("classTrainVisit")
    guild = det.get("guild")
    rated = [i["item_level"] for i in items if i["slot"] not in NO_ITEM_LEVEL and i["item_level"]]
    return {"items": items, "professions": professions, "wanted": wanted,
            "item_level": round(sum(rated) / len(rated), 1) if rated else None,
            "guild": str(guild).rsplit("-", 1)[0] if guild else None,
            "spec": spec.title() if isinstance(spec, str) else None,
            "spec_from": spec_from if isinstance(spec, str) else None,
            "polestar_spec": pspec if isinstance(pspec, str) else None, "gear_adjust": adjust,
            "talents": talents,
            # Forever's talents (spell ID and rank), the export's level and date.
            "talent_ranks": ranks.get("ranks"), "talent_nodes": ranks.get("nodes"),
            "talents_level": ranks.get("level"), "talents_date": ranks.get("date"),
            "min_upgrade": min_upgrade,
            "class": cls, "race": det.get("race"), "faction": det.get("faction"),
            "sex": SEXES.get(det.get("sex")),
            **({"recipes": recipes} if recipes else {}),
            **({"class_train_level": int(visit)} if isinstance(visit, (int, float)) and visit > 0 else {})}


def recipes_of(acct: Path, char: str) -> list[dict]:
    """The recipes the character knows, per profession, from DataStore_Crafts
    (Altoholic's data store): [{profession, known: [spell IDs], seen}].
    DataStore saves every recipe of a profession window the character opened,
    each as one number: bit 2 learned, bits 7 and up the recipe's spell ID
    (DataStore_Crafts_Retail.lua _GetRecipeInfo). A profession whose window
    was never opened lists none and is left out: unknown, not "unlearned".
    Its characters are keyed "Default.<realm>.<Name>" (DataStore.lua)."""
    sv = acct / "SavedVariables"
    ids = next(iter(read_sv_versions(sv / "DataStore.lua")), None) or {}
    crafts = next(iter(read_sv_versions(sv / "DataStore_Crafts.lua")), None) or {}
    keys = ((ids.get("DataStore_CharacterIDs") or {}).get("Set")) or {}
    cid = next((v for k, v in keys.items() if isinstance(k, str) and char_of(k.rsplit(".", 1)[-1]) == char), None)
    c = (crafts.get("DataStore_Crafts_Characters") or {}).get(cid) if cid is not None else None
    profs = (c or {}).get("Professions") or {}
    out = []
    for p in (profs.values() if isinstance(profs, dict) else profs):
        if not isinstance(p, dict) or not isinstance(p.get("Name"), str):
            continue
        known, seen = set(), 0
        for cat in (p.get("Crafts") or {}).values():
            for v in (cat.values() if isinstance(cat, dict) else cat if isinstance(cat, list) else []):
                if isinstance(v, int) and v > 0:
                    seen += 1
                    if v >> 2 & 1:
                        known.add(v >> 7)
        if seen:
            out.append({"profession": p["Name"], "known": sorted(known), "seen": seen})
    return sorted(out, key=lambda r: r["profession"])


# ---------------------------------------------------------------------------
# The roster: every character any local file knows, not only Polestar's
# ---------------------------------------------------------------------------

def roster(acct: Path) -> dict[str, dict]:
    """Every character of one account folder: its WTF folder
    (<realm>/<char>/, written at each login), Polestar's alts list and
    Syndicator's characters. Auctionator keeps nothing per character.
    char -> what is known: realm, race, class, sex, faction, guild, money,
    level, last seen, and which sources know it."""
    chars: dict[str, dict] = {}

    def put(char: str, source: str, **kv) -> None:
        c = chars.setdefault(char, {"sources": set(), "seen": 0.0})
        c["sources"].add(source)
        for k, v in kv.items():
            if k == "seen":
                c["seen"] = max(c["seen"], v or 0.0)
            elif v not in (None, "") and c.get(k) in (None, ""):
                c[k] = v

    for d in sorted(acct.glob("*/*")):
        if not d.is_dir() or d.parent.name == "SavedVariables":
            continue
        times = [f.stat().st_mtime for f in d.rglob("*") if f.is_file()]
        put(char_of(d.name), "folder", folder=d, seen=max(times, default=0.0), realm=d.parent.name)
    for db in read_sv_versions(acct / "SavedVariables" / "Polestar.lua"):
        for key, c in ((((db.get("PolestarDB") or {}).get("alts") or {}).get("chars")) or {}).items():
            if isinstance(c, dict):
                put(char_of(c.get("name") or str(key).split("-")[0]), "polestar",
                    seen=c.get("seen") if isinstance(c.get("seen"), (int, float)) else 0.0,
                    level=c.get("level"), race=c.get("race"), **{"class": c.get("class")},
                    faction=c.get("faction"))
    synd = next(iter(read_sv_versions(acct / "SavedVariables" / "Syndicator.lua")), None) or {}
    for c in (((synd.get("SYNDICATOR_DATA") or {}).get("Characters")) or {}).values():
        det = (c or {}).get("details") if isinstance(c, dict) else None
        if not isinstance(det, dict) or not det.get("character"):
            continue
        guild = det.get("guild")
        put(char_of(str(det["character"])), "syndicator", race=det.get("race"),
            **{"class": det.get("className")}, sex=SEXES.get(det.get("sex")), faction=det.get("faction"),
            guild=str(guild).rsplit("-", 1)[0] if guild else None,
            money_copper=c.get("money") if isinstance(c.get("money"), int) else None)
    return chars


# ---------------------------------------------------------------------------
# Narcissus's barber shop (account SavedVariables/Narcissus_Barbershop.lua)
# ---------------------------------------------------------------------------

# The character's own GUID: its profession links carry it (`|Htrade:Player-
# <realm id>-<player id>:...`), and CharacterExport-Forever saves them in the
# character's own SavedVariables. Narcissus keys its saves by that player id.
OWN_GUID = re.compile(rb"\|Htrade:Player-\d+-([0-9A-Fa-f]+):")


def own_player_id(cdir: Path) -> str | None:
    try:
        m = OWN_GUID.search((cdir / "CharacterExport-Forever.lua").read_bytes())
    except OSError:
        return None
    return m.group(1).decode().upper() if m else None


def _seq(v) -> list:
    """A Lua array as the reader gives it ({"1": a, "2": b}) as a list."""
    if isinstance(v, list):
        return v
    if not isinstance(v, dict):
        return []
    return [v[k] for k in sorted(v, key=lambda k: int(k) if str(k).isdigit() else 1 << 30)]


def narcissus_looks(acct: Path, chars: dict[str, dict]) -> dict[str, list[dict]]:
    """char -> the newest look saved in Narcissus's barber shop for it, as
    [{optionId, choiceId}] (the game's ids, Wowhead's too). Saves are
    NarciBarberShopDB.PlayerData[<player id>].SavedLooks[<race id>][male|
    female] = newest first {name, data = {{optionID, choiceID}...},
    timeCreated} (Narcissus_Barbershop/Code/Barbershop.lua); a character set
    to the shared profile uses SharedSavedLooks. The player id comes from
    the character's GUID (own_player_id); without it, the name Narcissus
    saved, when only one of our characters has it."""
    db = next(iter(read_sv_versions(acct / "SavedVariables" / "Narcissus_Barbershop.lua")), None) or {}
    narci = db.get("NarciBarberShopDB") or {}
    players = narci.get("PlayerData") or {}
    if not isinstance(players, dict) or not players:
        return {}
    by_first: dict[str, list[str]] = {}
    for char in chars:
        by_first.setdefault(char.split("-")[0].lower(), []).append(char)
    out: dict[str, list[dict]] = {}
    for char, c in chars.items():
        folder = c.get("folder")
        pid = own_player_id(folder / "SavedVariables") if folder else None
        pdata = players.get(pid) if pid else None
        if pdata is None:
            hits = [v for v in players.values() if isinstance(v, dict)
                    and str(v.get("playerName") or "").lower() == char.split("-")[0].lower()]
            if len(hits) == 1 and len(by_first.get(char.split("-")[0].lower(), [])) == 1:
                pdata = hits[0]
        if not isinstance(pdata, dict):
            continue
        saved = narci.get("SharedSavedLooks") if pdata.get("usePublicProfile") else pdata.get("SavedLooks")
        best = None
        for race_key, by_sex in (saved or {}).items():
            if race_key == "alternateForm" or not isinstance(by_sex, dict):
                continue  # a druid form or the worgen's other shape, not the character
            for sex, looks in by_sex.items():
                if c.get("sex") and sex != c["sex"]:
                    continue
                for look in _seq(looks):
                    if isinstance(look, dict) and isinstance(look.get("timeCreated"), (int, float)) and (
                            best is None or look["timeCreated"] > best["timeCreated"]):
                        best = look
        if best:
            pairs = [_seq(x) for x in _seq(best.get("data"))]
            looks = [{"optionId": int(x[0]), "choiceId": int(x[1])} for x in pairs
                     if len(x) >= 2 and all(isinstance(n, (int, float)) for n in x[:2])]
            if looks:
                out[char] = looks
    return out


def extract_roster(chars: dict[str, dict], state: dict, out: Out) -> None:
    """A wow-character snapshot for each character Polestar doesn't track
    (Polestar's own logout snapshots cover the others), each time what is
    known about it changes. Its time is the newest file in its WTF folder:
    the client writes them at login and logout."""
    sent = state.setdefault("roster", {})
    for char, c in sorted(chars.items()):
        if "polestar" in c["sources"]:
            continue
        ev = {"realm": c.get("realm"), "race": c.get("race"), "class": c.get("class"), "sex": c.get("sex"),
              "faction": c.get("faction"), "guild": c.get("guild"), "money_copper": c.get("money_copper"),
              "level": c.get("level"), "source": ",".join(sorted(c["sources"]))}
        if c.get("looks"):
            ev["looks"] = json.dumps(c["looks"], separators=(",", ":"))
        digest = hashlib.sha1(json.dumps(ev, sort_keys=True).encode()).hexdigest()[:12]
        if sent.get(char) == digest:
            continue
        sent[char] = digest
        what = " ".join(str(x).title() for x in (c.get("race"), c.get("class")) if x)
        out.emit({"_time": iso(c["seen"] or time.time()), "feed": "wow-character", "char": char,
                  "source_id": f"character:{char}:{digest}",
                  "_msg": f"{char}: {what or 'only its WTF folder'} ({ev['source']})", **ev})


# ---------------------------------------------------------------------------
# Altoholic's /played (optional): DataStore_Characters, account SavedVariables
# ---------------------------------------------------------------------------

# Read from Thaoky's source (github.com/Thaoky, 2026-10-14 releases, the ones
# Altoholic_Forever depends on):
# - DataStore/DataStore.lua: each character's key is "Default.<realm>.<name>"
#   (ThisCharKey), stored with StoreToSetAndList in DataStore_CharacterIDs =
#   {Set = {[key] = id}, List = {key, ...}, Count = n} (API/Utility.lua).
#   Only Set maps a key to its id. The realm is GetRealmName(), unverified on
#   Forever, so only the name part is used.
# - DataStore_Characters/DataStore_Characters.lua: DataStore_Characters_Info
#   [id] (API/Core.lua GetCharacterDB) holds `name` (AddonFactory's
#   GetPlayerName: "First Last" on Forever), `played` and `playedThisLevel`
#   (seconds, from TIME_PLAYED_MSG, which it requests at every login and
#   /reload: option RequestPlayTime, on by default), `BaseInfo` (bits 0-6:
#   the level, updated at each ding), `lastUpdate` and `lastLogoutTimestamp`.
# `lastUpdate` does NOT mark when `played` was captured: OnPlayerAlive sets
# it at login, then OnPlayerLogout sets it again, to the same time() as
# lastLogoutTimestamp. So played + (lastLogoutTimestamp - lastUpdate) adds
# nothing, and the moment `played` is valid for (the last login or /reload)
# isn't saved anywhere. The server anchors it to a level start instead:
# played - playedThisLevel is /played when that level began, a fixed point,
# and Polestar's splits plus the Recorder's time carry it to now
# (wowstats.views.played_total). It holds whenever no ding came between
# that login and the save.
DS_LOGGED_IN = 5_000_000_000


def _by_id(v) -> dict[int, object]:
    """A Lua array as the reader gives it (a list, or {"1": a, ...}) by index."""
    if isinstance(v, list):
        return {i + 1: x for i, x in enumerate(v)}
    if isinstance(v, dict):
        return {int(k): x for k, x in v.items() if str(k).isdigit()}
    return {}


def altoholic_played(acct: Path, only: set[str] | None = None) -> list[dict]:
    """One /played snapshot per character and saved version of
    DataStore_Characters.lua: {char, played_total_s, played_level_s, level,
    at, logged_out}. Empty without Altoholic (DataStore) installed. only:
    the account's own roster; any other character is never sent."""
    sv = acct / "SavedVariables"
    keys: dict[int, str] = {}
    for db in read_sv_versions(sv / "DataStore.lua"):
        ids = db.get("DataStore_CharacterIDs") or {}
        if not isinstance(ids, dict):
            continue
        # Set only: List indexes shift when a character is deleted.
        for key, i in (ids.get("Set") or {}).items() if isinstance(ids.get("Set"), dict) else ():
            if isinstance(i, int):
                keys.setdefault(i, str(key))
    out = []
    for db in read_sv_versions(sv / "DataStore_Characters.lua"):
        for i, c in _by_id(db.get("DataStore_Characters_Info")).items():
            if not isinstance(c, dict):
                continue
            played, at = c.get("played"), c.get("lastUpdate")
            if not isinstance(played, (int, float)) or played <= 0 or not isinstance(at, (int, float)):
                continue
            name = c.get("name") or keys.get(i, "").rsplit(".", 1)[-1]
            if not name or (only is not None and char_of(str(name)) not in only):
                continue
            lvl = c.get("BaseInfo")
            plv = c.get("playedThisLevel")
            out_ts = c.get("lastLogoutTimestamp")
            out.append({"char": char_of(str(name)), "played_total_s": int(played),
                        "played_level_s": int(plv) if isinstance(plv, (int, float)) else None,
                        "level": int(lvl) & 0x7F if isinstance(lvl, (int, float)) else None,
                        "at": float(at),
                        "logged_out": isinstance(out_ts, (int, float)) and out_ts < DS_LOGGED_IN})
    return out


# ---------------------------------------------------------------------------
# Completed quests (optional): Questie and Altoholic's DataStore_Quests
# ---------------------------------------------------------------------------

# The Recorder only sees the quests handed in while it runs, so a quest done
# before Polestar was installed read as not done (owner, 2026-11-19: Bazil
# Thredd "blocked" by The Unsent Letter, handed in long before). The game
# keeps the whole set (C_QuestLog.GetAllCompletedQuestIDs); two addons the
# kit already lists save it at every login. Polestar saves nothing of it:
# its completed set lives in memory only (Quests/QuestDB.lua
# client.CompletedSet, Polestar v0.4.1). Syndicator, Auctionator,
# CharacterExport-Forever (only into its clipboard export), Narcissus and
# VulcanSimBench save no quests either. wowstats.quests joins these sets
# with the Recorder's hand-ins and what the quest chains imply.
#
# Questie (12.11.0 as installed): QuestieQuest:Initialize and every quest
# log reset set Questie.db.char.complete = GetQuestsCompleted(), saved by
# AceDB in the account's Questie.lua as QuestieConfig.char[<key>].complete
# = {[questID] = true}. The key is "First Surname" on Forever (AceDB-3.0.lua
# :275, Questie's own copy), "Name - Realm" elsewhere. Exact, but only as
# fresh as the last login with Questie on.
#
# DataStore_Quests (2026.10.14; Forever loads DataStore_Quests.toc,
# "## Interface: 120105, 16001"): API/History.lua refreshes
# DataStore_Quests_History[id].Quests from C_QuestLog.GetAllCompletedQuestIDs
# 5 s after each login (option AutoUpdateHistory, on by default; wiped and
# rebuilt) and sets a bit at each hand-in. 64 quests per Lua number:
# Quests[ceil(q / 64)] bit q % 64 (bit 0 of slot k is quest 64k), set by
# adding 2^bit to a double (LibBit64), and the client writes the number
# with 16 significant digits. A double holds 53 bits, so in a slot whose
# high bits are set the low ones are rounded away, and the 16 digits blur
# them further: decoded naively, the owner's file (2026-11-19) gave 366
# quests for the 323 the game counted. Only the bits that no rounding can
# have touched are read (_ds_bits); the others are left unknown, never
# guessed. Size is the game's own count. With AutoUpdateHistory off the
# number is reloaded and rewritten at every login, its error grows without
# bound, and the file isn't read.


def questie_completed(acct: Path) -> dict[str, list[int]]:
    """char -> the quests Questie's last login with it found completed."""
    out: dict[str, list[int]] = {}
    db = next(iter(read_sv_versions(acct / "SavedVariables" / "Questie.lua")), None) or {}
    for key, c in (((db.get("QuestieConfig") or {}).get("char")) or {}).items():
        done = c.get("complete") if isinstance(c, dict) else None
        if not isinstance(done, dict) or not done:
            continue
        ids = sorted(q for q, v in done.items() if isinstance(q, int) and q > 0 and v is True)
        if ids:
            out[char_of(str(key).split(" - ")[0])] = ids
    return out


def _ds_bits(v) -> tuple[set[int], set[int]]:
    """(the set bits of one DataStore_Quests_History number that no
    rounding can have changed, the bits it can't tell). The number read back differs
    from the exact bit field by the 16-digit print (half a unit in the
    16th digit), the read itself (half a ulp) and the additions that
    built it (half a ulp each, 64 at most); a bit is known when every
    integer within that distance agrees on it."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v < 0 or v >= 2.0 ** 64:
        return set(), set(range(64))
    x = int(v)
    err = 0
    if x >= 2 ** 53:
        ulp = 1 << (x.bit_length() - 53)
        err += ulp // 2 + 32 * ulp
    if x >= 10 ** 16:
        err += 5 * 10 ** (len(str(x)) - 17)
    err += 1 if err else 0
    lo, hi = max(0, x - err), x + err
    known = {b for b in range(64) if lo >> b == hi >> b}
    return {b for b in known if lo >> b & 1}, set(range(64)) - known


def datastore_completed(acct: Path) -> dict[str, dict]:
    """char -> {quest_ids, size, unknown, at} from DataStore_Quests_History:
    the quests its last refresh found completed, as far as the numbers can
    be trusted (_ds_bits), the game's own count, and when."""
    sv = acct / "SavedVariables"
    db = next(iter(read_sv_versions(sv / "DataStore_Quests.lua")), None) or {}
    opts = db.get("DataStore_Quests_Options")
    if isinstance(opts, dict) and opts.get("AutoUpdateHistory") is False:
        return {}
    names: dict[int, str] = {}
    for d in read_sv_versions(sv / "DataStore.lua"):
        ids = (d.get("DataStore_CharacterIDs") or {}).get("Set")
        for key, i in (ids.items() if isinstance(ids, dict) else ()):
            if isinstance(i, int):
                names.setdefault(i, str(key).rsplit(".", 1)[-1])
    for d in read_sv_versions(sv / "DataStore_Characters.lua"):
        for i, c in _by_id(d.get("DataStore_Characters_Info")).items():
            if isinstance(c, dict) and c.get("name"):
                names[i] = str(c["name"])
    out: dict[str, dict] = {}
    for i, h in _by_id(db.get("DataStore_Quests_History")).items():
        if not isinstance(h, dict) or not names.get(i):
            continue
        done, unknown = set(), 0
        for slot, v in _by_id(h.get("Quests")).items():
            bits, unk = _ds_bits(v)
            unknown += len(unk)
            done |= {64 * (slot - 1) + b if b else 64 * slot for b in bits}
        if done:
            out[char_of(names[i])] = {
                "quest_ids": sorted(done), "unknown": unknown,
                "size": h.get("Size") if isinstance(h.get("Size"), int) else None,
                "at": float(h["LastUpdate"]) if isinstance(h.get("LastUpdate"), (int, float)) else None}
    return out


def extract_quests(acct: Path, chars: dict[str, dict], state: dict, out: Out) -> None:
    """One wow-quests event (kind=completed) per character and source each
    time the set of quests that source says are completed changes."""
    seen = state.setdefault("quests", {})
    snaps: list[tuple[str, str, dict]] = []
    qf = acct / "SavedVariables" / "Questie.lua"
    for char, ids in questie_completed(acct).items():
        snaps.append((char, "questie", {"quest_ids": ids, "at": qf.stat().st_mtime if qf.exists() else None}))
    df = acct / "SavedVariables" / "DataStore_Quests.lua"
    for char, d in datastore_completed(acct).items():
        snaps.append((char, "altoholic", {**d, "at": d["at"] or (df.stat().st_mtime if df.exists() else None)}))
    for char, source, d in snaps:
        if char not in chars:
            continue
        ids = d["quest_ids"]
        digest = hashlib.sha1(json.dumps(ids).encode()).hexdigest()[:12]
        key = f"{char}:{source}"
        if seen.get(key) == digest:
            continue
        seen[key] = digest
        extra = {k: d[k] for k in ("size", "unknown") if d.get(k) is not None}
        out.emit({"_time": iso(d["at"] or time.time()), "feed": "wow-quests", "kind": "completed", "char": char,
                  "source": source, "source_id": f"quests:{key}:{digest}",
                  "_msg": f"{char}: {len(ids)} quests completed ({source})", "quest_ids": ids, "n": len(ids),
                  **extra})


# Altoholic's DataStore_Talents (bundled in Altoholic_Forever, Thaoky,
# version 2026.10.14 as installed), read from its source:
# - Forever (interface 16001) loads DataStore_Talents.toc ("## Interface:
#   120105, 16001"), whose files are Enum, DataStore_Talents.lua and
#   API/Specialization, Covenant, StatPriorities, Glyphs: not
#   API/TalentTrees.lua (only the Vanilla and TBC tocs list it).
#   AddonFactory/Core/Tools.lua:146-148 sets isForever for 16000-19999.
# - DataStore_Talents.lua:355-415 registers no scan at all on any client
#   (its ScanTalents_* functions and their events are commented out), so
#   DataStore_Talents_Characters holds no trees or ranks; on Forever it
#   only gets Glyphs (API/Glyphs.lua).
# - API/Specialization.lua:4 keeps the file on Forever; :194-202 scans at
#   PLAYER_ENTERING_WORLD, PLAYER_ALIVE and CHARACTER_POINTS_CHANGED;
#   :30-42 stores DataStore_Talents_Specializations[character id] = spec
#   index (bits 0-2) + role id << 3 + name id << 5, the names and roles in
#   DataStore_Talents_SpecializationInfos.Names/Roles = {List, Set, Count}
#   (decoded as :83-88 does). The id is DataStore_CharacterIDs.Set[key]
#   (characterIdTables; DataStore.lua:71-75 assigns the id), as for Characters_Info.
# On Forever 1.60.1 the spec it reads is the class itself ("Priest",
# "Hunter" in the account's own file, 2026-11-18): C_SpecializationInfo
# knows one spec named after the class with no points (VulcanSimBench
# Collect.lua:696-698). So Altoholic gives no talents and no real spec here,
# and the shipper doesn't read DataStore_Talents: the card's talents come
# from Battle.net (the exporter) and /simx.


def extract_played(acct: Path, chars: dict[str, dict], state: dict, out: Out) -> None:
    """A wow-character event (kind=played) per new /played snapshot of a
    character in the roster."""
    sent = set(state.setdefault("played", []))
    for p in altoholic_played(acct, set(chars)):
        sid = f"played:{p['char']}:{p['played_total_s']}"
        if sid in sent:
            continue
        sent.add(sid)
        state["played"].append(sid)
        out.emit({"_time": iso(p["at"]), "feed": "wow-character", "kind": "played", "char": p["char"],
                  "source_id": sid, "source": "altoholic",
                  "_msg": f"{p['char']}: {p['played_total_s'] // 3600}h {p['played_total_s'] % 3600 // 60}m "
                          f"played (Altoholic), level {p['level']}",
                  "played_total_s": p["played_total_s"], "played_level_s": p["played_level_s"],
                  "level": p["level"], "logged_out": p["logged_out"]})


def extract_gear(acct: Path, chars: dict[str, dict], state: dict, out: Out) -> None:
    """One wow-gear event per character each time its sheet changes (gear,
    skills, guild, tracked upgrades), for every character of the roster.
    The time is the character's logout snapshot in Polestar, when the game
    wrote these files, else the newest of the files read."""
    seen = state.setdefault("gear", {})
    logout: dict[str, float] = {}
    for db in read_sv_versions(acct / "SavedVariables" / "Polestar.lua"):
        for key, c in ((((db.get("PolestarDB") or {}).get("alts") or {}).get("chars")) or {}).items():
            if isinstance(c, dict) and isinstance(c.get("seen"), (int, float)):
                ch = char_of(c.get("name") or str(key).split("-")[0])
                logout[ch] = max(logout.get(ch, 0), c["seen"])
    for char, c in sorted(chars.items()):
        # The character's own SavedVariables, when it has a folder.
        cdir = (c.get("folder") or acct / "-" / "-" / char) / "SavedVariables"
        g = gear_of(acct, cdir, char)
        if g is None:
            continue
        digest = hashlib.sha1(json.dumps(g, sort_keys=True).encode()).hexdigest()[:12]
        if seen.get(char) == digest:
            continue
        seen[char] = digest
        at = logout.get(char)
        if at is None:
            files = [acct / "SavedVariables" / "Syndicator.lua", cdir / "CharacterExport-Forever.lua"]
            at = max((f.stat().st_mtime for f in files if f.exists()), default=time.time())
        profs = ", ".join(f"{p['name']} {p['rank']}" for p in g["professions"])
        out.emit({"_time": iso(at), "feed": "wow-gear", "char": char, "source_id": f"gear:{char}:{digest}",
                  "_msg": f"{char}: {len(g['items'])} items equipped"
                          + (f", item level {g['item_level']}" if g["item_level"] else "")
                          + (f"; {profs}" if profs else ""),
                  **g})


def _item_ids(containers) -> list[int]:
    """The item IDs in Syndicator's bags or bank: a Lua array of containers,
    each a Lua array of slots ({} when empty, else itemID, itemCount,
    itemLink...; Tracking/BagCache.lua)."""
    seen: set[int] = set()
    for bag in (containers.values() if isinstance(containers, dict) else containers or []):
        for it in (bag.values() if isinstance(bag, dict) else bag or []):
            if isinstance(it, dict) and isinstance(it.get("itemID"), int) and it["itemID"] > 0:
                seen.add(it["itemID"])
    return sorted(seen)


def inventory_of(acct: Path, char: str) -> dict | None:
    """Which items the character holds in its bags and its bank, by ID only,
    from Syndicator (the bank as it was the last time the character opened
    it). None when Syndicator doesn't know the character or hasn't scanned
    its bags yet (no containers); empty lists when it holds nothing."""
    synd = next(iter(read_sv_versions(acct / "SavedVariables" / "Syndicator.lua")), None) or {}
    for c in (((synd.get("SYNDICATOR_DATA") or {}).get("Characters")) or {}).values():
        det = (c or {}).get("details") or {}
        if isinstance(det, dict) and char_of(str(det.get("character") or "")) == char:
            return {"bags": _item_ids(c.get("bags")), "bank": _item_ids(c.get("bank"))} if c.get("bags") else None
    return None


def extract_inventory(acct: Path, chars: dict[str, dict], state: dict, out: Out) -> None:
    """One wow-bags event per character each time the set of item IDs in
    its bags or bank changes: what it has, for "obtained" next to a
    dungeon's drops. IDs only, no counts or links."""
    seen = state.setdefault("bags", {})
    for char in sorted(chars):
        inv = inventory_of(acct, char)
        if inv is None:
            continue
        digest = hashlib.sha1(json.dumps(inv, sort_keys=True).encode()).hexdigest()[:12]
        if seen.get(char) == digest:
            continue
        seen[char] = digest
        f = acct / "SavedVariables" / "Syndicator.lua"
        at = f.stat().st_mtime if f.exists() else time.time()
        out.emit({"_time": iso(at), "feed": "wow-bags", "char": char, "source_id": f"bags:{char}:{digest}",
                  "_msg": f"{char}: {len(inv['bags'])} items in the bags, {len(inv['bank'])} in the bank", **inv})


# ---------------------------------------------------------------------------
# Auctionator (account SavedVariables/Auctionator.lua): your own price scans
# ---------------------------------------------------------------------------

# Auctionator counts days from time({year=2020, month=1, day=1, hour=0}): local
# midnight on the player's machine, then whole 86400 s days (Constants/Main.lua).
SCAN_DAY_0 = time.mktime((2020, 1, 1, 0, 0, 0, 0, 0, -1))
FACTIONS = ("Alliance", "Horde", "Neutral")


def _text(v) -> str:
    return v.decode("utf-8", errors="replace") if isinstance(v, bytes) else str(v)


def _plain(v):
    """A table read in raw mode, with bytes keys and strings made text."""
    if isinstance(v, dict):
        return {_text(k) if isinstance(k, bytes) else k: _plain(x) for k, x in v.items()}
    return _text(v) if isinstance(v, bytes) else v


def auctionator_db(acct: Path) -> dict[str, dict]:
    """realm key -> {item key -> {"l": {day: copper}, "h": ..., "a": ..., "m": copper}},
    merged over Auctionator.lua and its .bak. The realm the player was on is
    left as a table when the client didn't get to serialize it; the others are
    CBOR strings."""
    merged: dict[str, dict] = {}
    for db in reversed(read_sv_versions(acct / "SavedVariables" / "Auctionator.lua", raw=True)):
        prices = db.get("AUCTIONATOR_PRICE_DATABASE")
        if not isinstance(prices, dict):
            continue
        for key, blob in prices.items():
            realm = _text(key)
            if realm == "__dbversion":
                continue
            if isinstance(blob, bytes):
                try:
                    data = cbor_decode(blob)
                except CborError as e:
                    log(f"auctionator: cannot decode {realm}: {e}")
                    continue
            else:
                data = _plain(blob)
            if not isinstance(data, dict):
                continue
            items = merged.setdefault(realm, {})
            for key, rec in data.items():
                if not isinstance(rec, dict):
                    continue
                # A day's numbers only widen while it lasts: lowest low,
                # highest high, most listed, whichever file saw them.
                cur = items.setdefault(str(key), {"l": {}, "h": {}, "a": {}})
                hi = rec.get("h") if isinstance(rec.get("h"), dict) else {}
                lo = rec.get("l") if isinstance(rec.get("l"), dict) else {}
                # Auctionator leaves l out when it equals h: the day's low is h then.
                lows = {**hi, **lo}
                for f, days, pick in (("l", lows, min), ("h", hi, max), ("a", rec.get("a"), max)):
                    for day, v in (days if isinstance(days, dict) else {}).items():
                        v = _num(v)
                        if v is None:
                            continue
                        old = cur[f].get(str(day))
                        cur[f][str(day)] = v if old is None else pick(old, v)
    return merged


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _max(a, b):
    return b if a is None else a if b is None else max(a, b)


def _price_twin(seen: dict, realm: str, realm_name: str, faction: str | None, rest: str, lo, hi, av) -> bool:
    """True when the same item and day was already sent under the realm's
    other Auctionator key (and the numbers are no wider): Auctionator keys
    a realm "Realm Faction" on a faction auction house and "Realm" on a
    shared one, and moves an account's prices from one key to the other
    with the same numbers. On 2026-11-18/19 that sent one account's 1,347
    prices twice, once per key. The twin's record is copied under this key,
    so a later change of the day still counts from it."""
    twins = ([f"price:{realm_name}:{rest}"] if faction else
             [f"price:{realm} {f}:{rest}" for f in FACTIONS])
    for t in twins:
        prev = seen.get(t)
        if prev and [min(lo, prev[0]), _max(hi, prev[1]), _max(av, prev[2])] == prev[:3]:
            seen[f"price:{realm}:{rest}"] = list(prev)
            return True
    return False


def extract_auctionator(acct: Path, char: str | None, state: dict, out: Out) -> None:
    """One wow-prices event per item and scan day. A day's numbers can still
    move while it lasts (Auctionator keeps the day's lowest and highest
    minimum buyout and the most it saw listed), so a change sends the day
    again with the next `rev`; readers keep the highest rev per source_id."""
    seen = state.setdefault("prices", {})
    item_names = names("Items")
    for realm, items in auctionator_db(acct).items():
        realm_name, faction = realm, None
        head, _, tail = realm.rpartition(" ")
        if tail in FACTIONS and head:  # the legacy auction house keys "Realm Faction"
            realm_name, faction = head, tail
        for key, rec in items.items():
            if not isinstance(rec, dict):
                continue  # "version" and other bookkeeping
            key = str(key)
            m = re.match(r"^(?:g:)?(\d+)", key)
            item_id = int(m.group(1)) if m else None
            hi_d, lo_d, av_d = (rec.get(f) if isinstance(rec.get(f), dict) else {} for f in ("h", "l", "a"))
            for day in sorted(set(hi_d) | set(lo_d), key=lambda d: int(d) if str(d).isdigit() else 0):
                if not str(day).isdigit():
                    continue
                hi, lo, av = _num(hi_d.get(day)), _num(lo_d.get(day)), _num(av_d.get(day))
                lo = lo if lo is not None else hi
                if lo is None:
                    continue
                sid = f"price:{realm}:{key}:{day}"
                prev = seen.get(sid)
                if prev is None and _price_twin(seen, realm, realm_name, faction, f"{key}:{day}", lo, hi, av):
                    continue
                if prev:  # within a day the numbers only widen: merge, never go back
                    lo, hi, av = min(lo, prev[0]), _max(hi, prev[1]), _max(av, prev[2])
                    if [lo, hi, av] == prev[:3]:
                        continue
                rev = prev[3] + 1 if prev else 1
                seen[sid] = [lo, hi, av, rev]
                t = SCAN_DAY_0 + int(day) * 86400
                name = item_names.get(item_id) if item_id is not None else None
                out.emit({"_time": iso(t), "feed": "wow-prices", "char": char, "source_id": sid, "rev": rev,
                          "_msg": f"{name or key}: lowest {round(lo)} copper on {datetime.fromtimestamp(t):%Y-%m-%d}",
                          "item_id": item_id, "item_key": key, "item_name": name,
                          "price_copper": round(lo), "price_high_copper": round(hi) if hi is not None else None,
                          "available": int(av) if av is not None else None,
                          "scan_day": datetime.fromtimestamp(t).strftime("%Y-%m-%d"),
                          "realm": realm_name, "faction": faction})


# ---------------------------------------------------------------------------
# Combat logs
# ---------------------------------------------------------------------------

# "11/17/2026 15:14:03.1200-5  SPELL_DAMAGE,..." (the year and the UTC offset
# are in newer clients only).
CL_LINE = re.compile(r"^(\d{1,2})/(\d{1,2})(?:/(\d{4}))? (\d{1,2}):(\d{2}):(\d{2})(?:\.(\d+))?([+-]\d{1,4})?\s+([A-Z0-9_]+)(?:,(.*))?$")
CL_GUID = re.compile(r"^(?:[A-Za-z]+-\d|0000000000000000$)")
AFFIL_MINE, AFFIL_GROUP = 0x1, 0x2 | 0x4
TYPE_PLAYER = 0x400
PLAYER_BUCKET_S = 600


def cl_time(m: re.Match) -> float:
    mon, day, year, hh, mi, ss, frac, tz = m.group(1, 2, 3, 4, 5, 6, 7, 8)
    now = datetime.now()
    y = int(year) if year else now.year
    secs = int(ss) + (float("0." + frac) if frac else 0.0)
    naive = datetime(y, int(mon), int(day), int(hh), int(mi), 0)
    if not year and naive > now + timedelta(days=1):
        naive = naive.replace(year=y - 1)  # a December log read in January
    if tz:
        sign = -1 if tz[0] == "-" else 1
        digits = tz[1:]
        off_min = int(digits) * 60 if len(digits) <= 2 else int(digits[:-2]) * 60 + int(digits[-2:])
        return naive.replace(tzinfo=timezone(timedelta(minutes=sign * off_min))).timestamp() + secs
    return naive.astimezone().timestamp() + secs  # the Mac's local time, DST included


def extract_combatlogs(our_chars: set[str], fallback_char: str | None, state: dict, out: Out) -> None:
    logs = sorted((WOW / "Logs").glob("WoWCombatLog*.txt"))
    st = state.setdefault("cl", {})
    seen_players = state.setdefault("cl_players", {})
    for path in logs:
        fs = st.setdefault(path.name, {"offset": 0})
        size = path.stat().st_size
        if size < fs["offset"]:
            fs.clear()
            fs["offset"] = 0  # rewritten from the start
        if size == fs["offset"]:
            continue
        with path.open("rb") as fh:
            fh.seek(fs["offset"])
            data = fh.read()
        end = data.rfind(b"\n")
        if end < 0:
            continue  # no complete line yet
        data = data[:end + 1]
        if not fs.get("char"):  # whose log: the player flagged as "mine"
            for m in re.finditer(rb',Player-[^,]+,"([^"]+)",(0x[0-9a-fA-F]+)', data):
                if int(m.group(2), 16) & AFFIL_MINE and int(m.group(2), 16) & TYPE_PLAYER:
                    fs["char"] = player_of(m.group(1).decode("utf-8", "replace"))[0]
                    break
        char = fs.get("char") or fallback_char
        pos = fs["offset"]
        for raw in data.splitlines(keepends=True):
            offset = pos
            pos += len(raw)
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            m = CL_LINE.match(line)
            if not m:
                continue
            t = cl_time(m)
            event, rest = m.group(9), m.group(10) or ""
            cols = next(csv.reader([rest]), []) if rest else []
            ev = {"_time": iso(t), "feed": "wow-combatlog", "char": char, "event": event, "_msg": line,
                  "source_id": f"cl:{path.name}:{offset}", "zone": fs.get("zone")}
            if event == "ZONE_CHANGE" and len(cols) >= 2:
                fs["zone"] = ev["zone"] = cols[1]
            if len(cols) >= 7 and CL_GUID.match(cols[0]):  # the standard source/dest prefix
                ev["source_name"], ev["dest_name"] = cols[1] if cols[1] != "nil" else None, cols[5] if cols[5] != "nil" else None
                for guid, name, flags in ((cols[0], cols[1], cols[2]), (cols[4], cols[5], cols[6])):
                    if not guid.startswith("Player-"):
                        continue
                    try:
                        fl = int(flags, 16)
                    except ValueError:
                        continue
                    player, realm = player_of(name)
                    if fl & AFFIL_MINE or player in our_chars:
                        continue
                    bucket = int(t // PLAYER_BUCKET_S * PLAYER_BUCKET_S)
                    sid = f"player:combatlog:{player}:{bucket}"
                    if sid in seen_players:
                        continue
                    seen_players[sid] = bucket
                    ctx = "grouped" if fl & AFFIL_GROUP else "combat"
                    out.emit({"_time": iso(t), "feed": "wow-player", "char": char, "source_id": sid,
                              "_msg": f"{ctx} {player}" + (f" in {fs['zone']}" if fs.get("zone") else ""),
                              "player": player, "player_realm": realm, "context": ctx, "source": "combatlog",
                              "zone": fs.get("zone")})
            out.emit(ev)
        fs["offset"] = pos
    cutoff = time.time() - 2 * 86400
    for k in [k for k, b in seen_players.items() if b < cutoff]:
        del seen_players[k]


# ---------------------------------------------------------------------------
# Extract: one WTF tree (the archive copy, the live folder, or an old commit)
# ---------------------------------------------------------------------------

def accounts(wtf: Path) -> list[Path]:
    base = wtf / "Account"
    return sorted(p for p in base.iterdir() if p.is_dir() and p.name != "SavedVariables") if base.is_dir() else []


def char_sv_dirs(acct: Path) -> list[Path]:
    return sorted(p for p in acct.glob("*/*/SavedVariables") if p.is_dir())


def extract_tree(wtf: Path, state: dict, out: Out, live: bool = True) -> list[dict]:
    """live=False for an old archived version (--backfill): its file times
    are the extraction's, so characters dated by them (the ones Polestar
    doesn't track) are left to the current tree."""
    char_ids = state.setdefault("char_ids", {})
    all_sessions = []
    for acct in accounts(wtf):
        cdirs = char_sv_dirs(acct)
        for d in cdirs:
            for db in read_sv_versions(d / "Polestar_Recorder.lua"):
                cid = (db.get("PolestarRecorderCharDB") or {}).get("id")
                if isinstance(cid, str):
                    char_ids[cid] = d.parent.name
        chunks, map_types = load_chunks([acct / "SavedVariables"])
        merge_chunks(chunks, client_chunks_of(acct))
        sessions = sessions_of(chunks)
        extract_recorder(sessions, map_types, char_ids, state, out)
        chars = roster(acct)
        looks = narcissus_looks(acct, chars)
        for char, look in looks.items():
            chars[char]["looks"] = look
        latest = extract_polestar(acct, cdirs, sessions, char_ids, state, out, looks)
        if not live:
            chars = {k: c for k, c in chars.items() if "polestar" in c["sources"]}
        extract_roster(chars, state, out)
        extract_gear(acct, chars, state, out)
        extract_inventory(acct, chars, state, out)
        extract_played(acct, chars, state, out)
        extract_quests(acct, chars, state, out)
        extract_auctionator(acct, latest or (cdirs[-1].parent.name if cdirs else None), state, out)
        all_sessions += sessions
    return all_sessions


def our_characters(wtf: Path) -> tuple[set[str], str | None]:
    chars, latest = set(), (None, 0.0)
    for acct in accounts(wtf):
        for d in char_sv_dirs(acct):
            chars.add(d.parent.name)
            t = d.stat().st_mtime
            if t > latest[1]:
                latest = (d.parent.name, t)
    return chars, latest[0]


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------

def wait_quiet(wtf: Path) -> bool:
    """True once no file under WTF changed for QUIET_S seconds."""
    deadline = time.time() + QUIET_MAX_WAIT_S
    while True:
        newest = max((p.stat().st_mtime for p in wtf.rglob("*") if p.is_file()), default=0)
        if time.time() - newest >= QUIET_S:
            return True
        if time.time() > deadline:
            return False
        time.sleep(QUIET_S)


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", **NO_WINDOW)
    if check and r.returncode:  # git's own words in the log, not just "exit status 128"
        raise RuntimeError(f"git {args[0]} in {REPO}: {r.stderr.strip() or r.returncode}")
    return r


def addon_meta(d: Path) -> dict | None:
    """An AddOns folder's .toc fields (its own-named .toc first), None when
    it has no .toc (not an addon)."""
    tocs = sorted(d.glob("*.toc"), key=lambda p: (p.stem != d.name, p.name)) if d.is_dir() else []
    if not tocs:
        return None
    meta: dict[str, str] = {}
    for line in tocs[0].read_text(encoding="utf-8-sig", errors="replace").splitlines():
        m = re.match(r"^##\s*([A-Za-z-]+)\s*:\s*(.*)$", line)
        if m and m.group(1) not in meta:
            meta[m.group(1)] = m.group(2).strip()
    return meta


def addon_list() -> str:
    rows = []
    base = WOW / "Interface" / "AddOns"
    for d in sorted(base.iterdir() if base.is_dir() else []):
        meta = addon_meta(d)
        if meta is None:
            continue
        title = re.sub(r"\|c[0-9a-fA-F]{8}|\|r", "", meta.get("Title", d.name))
        rows.append(f"{d.name}\t{meta.get('Version', '')}\t{meta.get('Interface', '')}\t{title}")
    return "addon\tversion\tinterface\ttitle\n" + "\n".join(rows) + "\n"


def archive_on() -> bool:
    """The WTF archive (step 1): on unless the config says `wtf_archive = false`."""
    v = CONFIG.get("wtf_archive", True)
    if isinstance(v, str):
        return v.strip().lower() not in ("false", "off", "no", "0")
    return bool(v)


def archive() -> bool:
    src = WOW / "WTF"
    if not REPO.joinpath(".git").is_dir():
        REPO.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(REPO)], check=True, capture_output=True, **NO_WINDOW)
        git("config", "user.name", "wow-stats")
        git("config", "user.email", "wow-stats@localhost")
        git("config", "commit.gpgsign", "false")  # runs unattended: no signing prompt
    wanted = set()
    for p in src.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(src)
        wanted.add(rel)
        dst = REPO / rel
        st = p.stat()
        if dst.exists():
            dt = dst.stat()
            if dt.st_size == st.st_size and int(dt.st_mtime) == int(st.st_mtime):
                continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
    for p in REPO.rglob("*"):
        rel = p.relative_to(REPO)
        if rel.parts[0] in (".git", CLIENT_KEEP) or str(rel) == ADDONS_FILE or not p.is_file():
            continue
        if rel not in wanted:
            p.unlink()
    (REPO / ADDONS_FILE).write_text(addon_list(), encoding="utf-8")
    git("add", "-A")
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        return False
    names = git("diff", "--cached", "--name-only").stdout.split()
    git("commit", "-q", "-m", f"WTF {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: {len(names)} files")
    log(f"archive: committed {len(names)} changed files")
    return True


def backfill(state: dict, out: Out) -> None:
    """Re-extract every archived version: the Recorder chunks of each one."""
    if not REPO.joinpath(".git").is_dir():
        log("backfill: no archive yet" if archive_on() else "backfill: no archive (wtf_archive = false)")
        return
    keep = re.compile(r"^Account/[^/]+/(SavedVariables/(Polestar|Polestar_Recorder|Auctionator|Syndicator"
                      r"|ForeverLevelingBiS|Narcissus_Barbershop|DataStore|DataStore_Characters|DataStore_Crafts|DataStore_Quests|Questie)\.lua(\.bak)?"
                      r"|[^/]+/[^/]+/SavedVariables/(Polestar|Polestar_Recorder|CharacterExport-Forever"
                      r"|GearQuestForever|VulcanSimBench)\.lua(\.bak)?)$")
    commits = git("rev-list", "--reverse", "HEAD").stdout.split()
    for c in commits:
        with tempfile.TemporaryDirectory() as tmp:
            for path in git("ls-tree", "-r", "--name-only", c).stdout.splitlines():
                if keep.match(path):
                    dst = Path(tmp) / path
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_bytes(subprocess.run(["git", "-C", str(REPO), "show", f"{c}:{path}"],
                                                   check=True, capture_output=True, **NO_WINDOW).stdout)
            extract_tree(Path(tmp), state, out, live=False)


# ---------------------------------------------------------------------------
# Ship
# ---------------------------------------------------------------------------

def ship_settings() -> tuple[str, dict] | None:
    """The receiver's URL and headers from [ship], None when not set up."""
    sh = CONFIG.get("ship") or {}
    url = sh.get("url")
    if not url:
        return None
    headers = {"Content-Type": "application/x-ndjson"}
    pw = sh.get("password")
    if sh.get("password_file"):
        pw = Path(os.path.expandvars(sh["password_file"])).expanduser().read_text(encoding="utf-8").strip()
    if sh.get("user"):
        token = base64.b64encode(f"{sh['user']}:{pw or ''}".encode()).decode()
        headers["Authorization"] = "Basic " + token
    return url, headers


def post(url: str, headers: dict, body: bytes) -> int | None:
    """HTTP status of one POST, None when the receiver can't be reached. The
    receiver answers 2xx only once its store has the lines (and 400 after
    30-65 s when that store is down): hence the long timeout. A timeout after
    the write sends the lines twice; queries dedupe on source_id."""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log(f"ship: {url}: {getattr(e, 'reason', e)}")
        return None


TIME_OK = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def valid(line: bytes) -> bool:
    """The receiver refuses a whole POST (400) for one line that isn't JSON."""
    try:
        ev = json.loads(line)
    except ValueError:
        return False
    return isinstance(ev, dict) and isinstance(ev.get("_time"), str) and bool(TIME_OK.match(ev["_time"]))


def retention_cutoff() -> str | None:
    """`_time` below which the receiver's store drops events on arrival, from
    [ship] retention_days (unset: no limit known, everything goes)."""
    days = (CONFIG.get("ship") or {}).get("retention_days")
    if isinstance(days, bool) or not isinstance(days, (int, float)) or days <= 0:
        return None
    return iso(time.time() - days * 86400)


def rewrite(f: Path, lines: list[bytes]) -> None:
    tmp = f.with_name(f.name + ".rest")
    tmp.write_bytes(b"".join(lines))
    os.replace(tmp, f)


def ship() -> None:
    files = sorted(SPOOL.glob("*.ndjson")) if SPOOL.is_dir() else []
    if not files:
        return
    settings = ship_settings()
    if settings is None:
        log(f"ship: no [ship] url in {config_path()}; {len(files)} spool files wait")
        return
    url, headers = settings
    # urllib has one timeout for connect and reply, and the reply can take
    # minutes: find out quickly when the receiver isn't there at all.
    u = urllib.parse.urlsplit(url)
    try:
        socket.create_connection((u.hostname, u.port or (443 if u.scheme == "https" else 80)), timeout=5).close()
    except OSError as e:
        log(f"ship: {u.hostname}:{u.port} unreachable ({e}); {len(files)} spool files wait")
        return
    SENT.mkdir(parents=True, exist_ok=True)
    cutoff = retention_cutoff()
    for f in files:
        lines, bad, old = [], [], []
        for ln in f.read_bytes().splitlines(keepends=True):
            if ln.strip():
                ln = ln if ln.endswith(b"\n") else ln + b"\n"
                if not valid(ln):
                    bad.append(ln)
                elif cutoff and json.loads(ln)["_time"] < cutoff:
                    old.append(ln)
                else:
                    lines.append(ln)
        if bad:
            REJECTED.mkdir(parents=True, exist_ok=True)
            with (REJECTED / f.name).open("ab") as fh:
                fh.write(b"".join(bad))
            log(f"ship: {f.name}: {len(bad)} invalid lines moved to {REJECTED}")
        if old:  # VictoriaLogs would answer 2xx and drop them: keep them here instead
            TOO_OLD.mkdir(parents=True, exist_ok=True)
            with (TOO_OLD / f.name).open("ab") as fh:
                fh.write(b"".join(old))
            log(f"ship: {f.name}: {len(old)} events older than the receiver's retention "
                f"({CONFIG['ship']['retention_days']} days) kept in {TOO_OLD}, not sent")
        if bad or old:
            rewrite(f, lines)
        sent = 0
        while sent < len(lines):
            part, size = [], 0
            for ln in lines[sent:]:
                if part and size + len(ln) > POST_MAX_BYTES:
                    break
                part.append(ln)
                size += len(ln)
            code = post(url, headers, b"".join(part))
            if code is None or not 200 <= code < 300:
                if code is not None:
                    log(f"ship: {f.name}: HTTP {code}, kept for the next run")
                rewrite(f, lines[sent:])  # keep only what is left
                return
            with gzip.open(SENT / (f.name + ".gz"), "ab") as dst:  # one gzip member per POST
                dst.write(b"".join(part))
            sent += len(part)
            if sent < len(lines):  # a big first run: progress, and a kill resends nothing already stored
                rewrite(f, lines[sent:])
                log(f"ship: {f.name}: {sent}/{len(lines)} events sent")
        f.unlink()
        log(f"ship: {f.name} sent ({len(lines)} events)")


# ---------------------------------------------------------------------------

def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except FileNotFoundError:
        return {}


def save_state(state: dict) -> None:
    tmp = STATE.with_name(".state.json.tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    os.replace(tmp, STATE)


def rotate_log() -> None:
    try:
        if LOG.stat().st_size > 5 * 1024 * 1024:
            os.replace(LOG, LOG.with_suffix(".log.1"))
    except FileNotFoundError:
        pass


def take_lock(path: Path):
    """An exclusive lock on path, None when another run holds it."""
    fh = open(path, "a+")
    try:
        if WINDOWS:
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


CLIENT_KEEP = "_polestar-client"  # in the archive repo, next to the WTF mirror


def keep_dir() -> Path:
    return REPO / CLIENT_KEEP


def keep_client() -> int:
    """Append the client's chunks not kept yet (by install, charId, seq) to
    our own copy of sent.jsonl: the client prunes its file to 8 days, ours
    only grows, and the archive step commits it. Returns how many."""
    try:
        data = (client_dir() / "sent.jsonl").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    dst = keep_dir() / "sent.jsonl"

    def key(line: str):
        try:
            rec = json.loads(line)
        except ValueError:
            return None
        if not isinstance(rec, dict) or not isinstance(rec.get("text"), str):
            return None
        one: dict = {}
        add_chunk(one, rec["text"])
        return (str(rec.get("install")), *next(iter(one))) if one else None

    try:
        have = {key(ln) for ln in dst.read_text(encoding="utf-8", errors="replace").splitlines()}
    except FileNotFoundError:
        have = set()
    new = []
    for line in data.splitlines():
        k = key(line)
        if k is not None and k not in have:
            have.add(k)
            new.append(line + "\n")
    if new:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with dst.open("a", encoding="utf-8") as fh:
            fh.writelines(new)
        log(f"polestar client: kept {len(new)} new chunks in {dst}")
    return len(new)


def _last_rec(lines: list[str]) -> int:
    """The index extract_recorder marks done for a chunk: its last record's."""
    for idx in range(len(lines) - 1, 0, -1):
        f = lines[idx].split("\t")
        if len(f) >= 2 and f[1].lstrip("-").isdigit():
            return idx
    return 0


def load_client(state: dict) -> None:
    """CLIENT = the client's live file, plus the kept chunks a run still has
    to send (a fresh state: all of them) and a day before those, so the
    sessions around them come out as they did the first time. Kept chunks
    already sent and older than the live file stay on disk: re-reading every
    one of them each run would grow without end."""
    live = client_chunks()
    kept = client_chunks(keep_dir())
    done = state.get("rec", {})
    todo = [e for k, (e, lines) in kept.items()
            if k not in live and done.get(f"{k[0]}:{k[1]}", 0) < _last_rec(lines)]
    since = min(todo) - 86400 if todo else min((e for e, _ in live.values()), default=float("inf"))
    CLIENT.clear()
    CLIENT.update({k: v for k, v in kept.items() if v[0] >= since})
    merge_chunks(CLIENT, live)
    if CLIENT:
        log(f"polestar client: {len(CLIENT)} chunks ({len(live)} in {client_dir() / 'sent.jsonl'}, "
            f"{len(kept)} kept)")


# ---------------------------------------------------------------- Polestar Recorder

# The installers (install and update, Mac and Windows) put Polestar Recorder
# in the AddOns folder: it isn't on CurseForge, and a friend unzipping it by
# hand got the folder depth wrong. The official release zip from its GitHub
# repo, at an exact version, checked against the SHA-256 the release's own
# SHA256SUMS lists. Bumped by hand, not by Dependabot (the checksum moves
# with the version): set both from https://github.com/fitzforge/polestar/releases
# (the Polestar_Recorder-<v>.zip line of SHA256SUMS), then run
# tests/test_recorder_install.py.
RECORDER_VERSION = "0.4.1.1"
RECORDER_SHA256 = "90bc192be92ea245aa55eb4aea5b339f10c5e170d0ea82598f8f811ef0a87fec"
RECORDER_URL = ("https://github.com/fitzforge/polestar/releases/download/"
                f"v{RECORDER_VERSION}/Polestar_Recorder-{RECORDER_VERSION}.zip")
RECORDER_DIR = "Polestar_Recorder"


class RecorderError(Exception):
    pass


def _version(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def recorder_version(addons: Path) -> str | None:
    """The version in an installed Recorder's .toc; None when there is none."""
    toc = addons / RECORDER_DIR / f"{RECORDER_DIR}.toc"
    try:
        text = toc.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    m = re.search(r"^## Version:\s*(\S+)", text, re.M)
    return m.group(1) if m else ""


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "wow-stats installer"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read(20 * 1024 * 1024)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RecorderError(f"download failed: {getattr(e, 'reason', e)}") from e


def install_recorder(wow: Path, fetch=_download, url: str | None = None, sha256: str | None = None) -> str:
    """Puts the pinned Polestar Recorder in <wow>/Interface/AddOns, unless
    the same or a newer one is there already. The zip's files land in
    AddOns/Polestar_Recorder/ (its .toc directly inside), replacing the old
    folder in one rename. The game's saved data lives under WTF/, which this
    never touches."""
    if not wow.is_dir():
        raise RecorderError(f"no WoW folder at {wow}")
    addons = wow / "Interface" / "AddOns"
    have = recorder_version(addons)
    if have and _version(have) >= _version(RECORDER_VERSION):
        return f"Polestar Recorder {have} is already there: kept"
    data = fetch(url or RECORDER_URL)
    if hashlib.sha256(data).hexdigest() != (sha256 or RECORDER_SHA256):
        raise RecorderError("the download doesn't match its checksum: nothing changed")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise RecorderError(f"not a zip: {e}") from e
    names = z.namelist()
    for n in names:
        parts = n.replace("\\", "/").split("/")
        if n.startswith(("/", "\\")) or ".." in parts or parts[0] != RECORDER_DIR:
            raise RecorderError(f"unexpected path in the zip: {n}")
    if f"{RECORDER_DIR}/{RECORDER_DIR}.toc" not in names:
        raise RecorderError("no Polestar_Recorder.toc in the zip")
    addons.mkdir(parents=True, exist_ok=True)
    # Unpacked next to its place (one filesystem, so the swap is a rename),
    # under a dot name WoW doesn't list as an addon.
    tmp = Path(tempfile.mkdtemp(prefix=".wow-stats-recorder-", dir=addons))
    try:
        z.extractall(tmp)
        dest, old = addons / RECORDER_DIR, tmp / "old"
        if dest.exists():
            dest.rename(old)
        (tmp / RECORDER_DIR).rename(dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return (f"Polestar Recorder {RECORDER_VERSION} installed in {addons}"
            + (f" (was {have})" if have else ""))


# ---------------------------------------------------------------- the preflight

# The addons and what each gives, as docs/videos/steps.json lists them
# ("addons" and "data"): the start page, the help and the pages' "Missing:"
# lines read that file; this copy is for the PC, and tests/test_preflight.py
# keeps it equal. EITHER: data that one of several optional addons gives.
ADDONS = [
    {"id": "polestar", "name": "Polestar", "dir": "Polestar", "required": True,
     "url": "https://www.curseforge.com/wow/addons/polestar",
     "what": "level splits, quests, deaths, dungeons, the gear advice"},
    {"id": "recorder", "name": "Polestar Recorder", "dir": "Polestar_Recorder", "required": True,
     "url": "https://github.com/fitzforge/polestar/releases/latest", "what": "your play sessions, zone by zone"},
    {"id": "syndicator", "name": "Syndicator", "dir": "Syndicator", "required": False,
     "url": "https://www.curseforge.com/wow/addons/syndicator", "what": "gear and gold on every character"},
    {"id": "auctionator", "name": "Auctionator", "dir": "Auctionator", "required": False,
     "url": "https://www.curseforge.com/wow/addons/auctionator", "what": "prices from your own auction house scans"},
    {"id": "altoholic", "name": "Altoholic", "dir": "Altoholic", "required": False,
     "url": "https://www.curseforge.com/wow/addons/altoholic",
     "what": "your full /played from level 1, the quests you finished before Polestar, and the recipes you know"},
    {"id": "questie", "name": "Questie", "dir": "Questie", "required": False,
     "url": "https://www.curseforge.com/wow/addons/questie",
     "what": "the quests you finished before Polestar (Altoholic does it too: one of the two is enough)"},
    {"id": "characterexport", "name": "CharacterExport-Forever", "dir": "CharacterExport-Forever", "required": False,
     "url": "https://www.curseforge.com/wow/addons/characterexport", "what": "profession caps"},
    {"id": "narcissus", "name": "Narcissus", "dir": "Narcissus", "required": False,
     "url": "https://www.curseforge.com/wow/addons/narcissus",
     "what": "your character's look in 3D before Battle.net knows it"},
    {"id": "simbench", "name": "Vulcan SimBench", "dir": "VulcanSimBench", "required": False,
     "url": "https://www.curseforge.com/wow/addons/vulcan-simbench", "what": "a history of your talents"},
]
EITHER = [{"id": "quests", "addons": ["questie", "altoholic"], "what": "quests you did before installing"}]
SETUP_FEED = "wow-setup"
HARD = 2  # the preflight's exit code when the install can't work as it is


def installed_addons(wow: Path) -> dict[str, str]:
    """ADDONS ids found in the AddOns folder, with their .toc version."""
    base = wow / "Interface" / "AddOns"
    out = {}
    for a in ADDONS:
        meta = addon_meta(base / a["dir"])
        if meta is not None:
            out[a["id"]] = meta.get("Version", "")
    return out


def recorder_on(wow: Path) -> bool | None:
    """Polestar Recorder's switch (PolestarRecorderDB.enabled, saved per WoW
    account; /psrec on sets it): True when it is on for any account, None
    when no account saved it yet (the game writes it at logout)."""
    seen = None
    for acct in accounts(wow / "WTF"):
        db = next(iter(read_sv_versions(acct / "SavedVariables" / "Polestar_Recorder.lua")), None) or {}
        rdb = db.get("PolestarRecorderDB")
        if isinstance(rdb, dict) and "enabled" in rdb:
            if rdb["enabled"] is True:
                return True
            seen = False
    return seen


def saved_chars(wow: Path) -> int:
    """Characters with Polestar's saved data: played and logged out since
    the addons went in."""
    return sum(1 for acct in accounts(wow / "WTF") for d in char_sv_dirs(acct)
               if (d / "Polestar.lua").is_file() or (d / "Polestar_Recorder.lua").is_file())


def addons_off(wow: Path, installed: dict[str, str] | None = None) -> dict[str, list[str]]:
    """Per character, the installed ADDONS ids it doesn't load: unticked in
    AddOns at character select (its AddOns.txt says "disabled"), or not
    listed there and off by default (the .toc's DefaultState). Characters
    with everything ticked are left out. The pages need it to tell "add
    Questie" from "Questie is there, tick it for this character"."""
    installed = installed_addons(wow) if installed is None else installed
    base = wow / "Interface" / "AddOns"
    dirs = {a["dir"]: a["id"] for a in ADDONS if a["id"] in installed}
    default_off = {i for d, i in dirs.items()
                   if str((addon_meta(base / d) or {}).get("DefaultState", "")).strip().lower() == "disabled"}
    out: dict[str, set[str]] = {}
    for acct in accounts(wow / "WTF"):
        for d in sorted(acct.glob("*/*")):
            if not d.is_dir() or d.parent.name == "SavedVariables":
                continue
            state: dict[str, bool] = {}
            f = d / "AddOns.txt"
            for line in (f.read_text(encoding="utf-8-sig", errors="replace").splitlines() if f.is_file() else ()):
                name, _, val = line.partition(":")
                if name.strip() in dirs and val.strip().lower() in ("enabled", "disabled"):
                    state[dirs[name.strip()]] = val.strip().lower() == "enabled"
            off = {i for i in dirs.values() if not state.get(i, i not in default_off)}
            if off:
                out.setdefault(char_of(d.name), set()).update(off)
    return {c: sorted(ids) for c, ids in sorted(out.items())}


def setup_report(wow: Path) -> dict:
    """What the preflight saw, as the server gets it (feed wow-setup): the
    addons and their versions, the Recorder's switch, how many characters
    saved data, and per character the installed addons it doesn't load. No
    folder or path; the character names are the ones every event carries."""
    have = installed_addons(wow)
    return {"addons": have, "recorder_on": recorder_on(wow), "saved_chars": saved_chars(wow),
            "off": addons_off(wow, have), "shipper": VERSION}


def setup_event(state: dict, out: Out, force: bool) -> None:
    """The setup report with a send (force: other events go), when it
    changed, and once a day: /start and the pages read the newest one."""
    rep = setup_report(WOW)
    digest = hashlib.sha256(json.dumps(rep, sort_keys=True).encode()).hexdigest()[:16]
    last = state.get("setup") or {}
    now = time.time()
    if not (force or last.get("hash") != digest or now - float(last.get("at") or 0) > 86400):
        return
    out.emit({"_time": iso(now), "feed": SETUP_FEED, "_msg": "setup", "source_id": f"setup:{digest}:{int(now)}",
              **rep})
    state["setup"] = {"hash": digest, "at": now}


def server_check() -> tuple[bool, str, bool]:
    """(ok, line, hard): an empty authenticated POST to [ship] url, which
    the receiver answers like any send (2xx: the password works)."""
    settings = ship_settings()
    if settings is None:
        return False, f"No server set yet: fill in [ship] in {config_path()}.", False
    url, headers = settings
    try:
        req = urllib.request.Request(url, data=b"", headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except (urllib.error.URLError, TimeoutError, OSError):
        host = urllib.parse.urlsplit(url).hostname
        return False, f"Can't reach {host} right now. Nothing is lost: it sends once you're back online.", False
    who = (CONFIG.get("ship") or {}).get("user")
    if 200 <= code < 300:
        return True, "Server: connected" + (f" as {who}" if who else ""), False
    if code in (401, 403):
        return False, "The server refused your upload password. Get a new install command on your start page.", True
    return False, f"The server answered HTTP {code}. Nothing is lost: it tries again every 5 minutes.", False


def agent_check() -> tuple[bool, str, bool] | None:
    """(ok, line, hard) for the background program (launchd on macOS, Task
    Scheduler on Windows); None elsewhere (no installer there)."""
    if os.environ.get("WOW_STATS_NO_LOAD") == "1":
        return False, "Background program: not started (WOW_STATS_NO_LOAD=1).", False
    if sys.platform == "darwin":
        tool = shutil.which("launchctl")
        cmd = [tool, "print", f"gui/{os.getuid()}/local.wow-stats"] if tool else None
    elif WINDOWS:
        tool = shutil.which("schtasks")
        cmd = [tool, "/Query", "/TN", "wow-stats"] if tool else None
    else:
        return None
    try:
        ok = bool(cmd) and subprocess.run(cmd, capture_output=True, timeout=30, **NO_WINDOW).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    if ok:
        return True, "Background program: sends your data every 5 minutes", False
    return False, "Background program is not registered. Run the install command again.", True


def _marks() -> tuple[str, str]:
    """A check and a cross, or plain ASCII where the console can't show them
    (a pipe on Windows uses the ANSI code page)."""
    try:
        "\u2713\u2717".encode(sys.stdout.encoding or "ascii")
        return "\u2713", "\u2717"
    except (UnicodeEncodeError, LookupError):
        return "[ok]", "[x]"


def preflight() -> int:
    """The installers' last step: one line per check, a check mark or a
    cross with the one thing that fixes it. Exits HARD only when the
    install can't work as it is (no WoW folder, the password refused, no
    background program); a missing addon is a cross, never a failure."""
    good, bad = _marks()
    lines: list[tuple[bool, str]] = []
    hard = False
    if not (WOW / "WTF").is_dir():
        lines.append((False, f"WoW Forever folder not found. Log in to WoW once, or set wow_dir in {config_path()}."))
        hard = True
        have: dict[str, str] = {}
    else:
        lines.append((True, f"WoW Forever folder: {WOW}"))
        have = installed_addons(WOW)
    by_id = {a["id"]: a for a in ADDONS}
    for a in (a for a in ADDONS if a["required"]):
        if a["id"] in have:
            lines.append((True, f"{a['name']} {have[a['id']]}".rstrip()))
        else:
            lines.append((False, f"{a['name']} is missing. Get it: {a['url']}"))
        if a["id"] == "recorder" and a["id"] in have:
            on = recorder_on(WOW)
            lines.append((True, "Recorder is on") if on else
                         (False, "Recorder is off. Type /psrec on once in game.") if on is False else
                         (False, "Recorder: can't tell yet. Type /psrec on once in game."))
    grouped = {i for e in EITHER for i in e["addons"]}
    for e in EITHER:
        got = [by_id[i]["name"] for i in e["addons"] if i in have]
        names = " or ".join(by_id[i]["name"] for i in e["addons"])
        first = by_id[e["addons"][0]]
        lines.append((True, f"{' and '.join(got)}: {e['what']}") if got else
                     (False, f"{names} (optional): without one, {e['what']} stay unknown. "
                             f"Get {first['name']}: {first['url']}"))
    for a in ADDONS:
        if a["required"] or a["id"] in grouped:
            continue
        lines.append((True, f"{a['name']}: {a['what']}") if a["id"] in have else
                     (False, f"{a['name']} (optional) adds {a['what']}. Get it: {a['url']}"))
    if (WOW / "WTF").is_dir():
        n = saved_chars(WOW)
        lines.append((True, f"Saved game data for {n} character{'' if n == 1 else 's'}") if n else
                     (False, "No saved game data yet. Play a bit, then log out."))
    ok, text, h = server_check()
    lines.append((ok, text))
    hard |= h
    ag = agent_check()
    if ag is not None:
        lines.append((ag[0], ag[1]))
        hard |= ag[2]
    print("Checking your setup:")
    for ok, text in lines:
        print(f"  {good if ok else bad} {text}")
    sys.stdout.flush()
    return HARD if hard else 0


# ---------------------------------------------------------------- auto-update

# Once every 6 hours at most, after the run's own work: the shipper asks the
# server it ships to for the current version (GET <base>/install/update/shipper,
# with the upload password) and, when it is newer, takes the file from the same
# server, checks its sha256 and that it compiles, then replaces itself (the old
# copy stays as wow_stats.py.prev). The new version runs from the next run on.
# Same trust as the update command, no wider: the same server, the same
# password, HTTPS (or this computer). Never a downgrade; any failure keeps the
# running version and logs one line. `auto_update = false` in the config pins it.
UPDATE_EVERY_S = 6 * 3600
UPDATE_PATH = "/install/update/shipper"
UPDATE_MAX_BYTES = 8 * 1024 * 1024
# The files that define the background program, per platform: when the
# server's differ from the copies the installer left next to the script, the
# installer runs again (detached) to rewrite the agent or the task.
AGENT_FILES = {"darwin": ("install-mac.sh", "wow-stats.plist"), "win32": ("install-windows.ps1",)}
SELF = Path(__file__).resolve()


class UpdateError(Exception):
    pass


def version_tuple(v) -> tuple[int, ...] | None:
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", str(v or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def auto_update_on() -> bool:
    """On unless the config says `auto_update = false`."""
    v = CONFIG.get("auto_update", True)
    if isinstance(v, str):
        return v.strip().lower() not in ("false", "off", "no", "0")
    return bool(v)


def install_dir() -> Path:
    """Where the installers put the script: only that copy updates itself
    (a git checkout run by hand is never rewritten)."""
    if WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA", HOME / "AppData" / "Local")) / "wow-stats"
    return HOME / ".local" / "share" / "wow-stats"


def update_source() -> tuple[str, dict] | None:
    """(the server's base URL, the Authorization header) from [ship], None
    when the receiver isn't a wow-stats /ingest. Raises UpdateError for a
    plain-HTTP server other than this computer."""
    settings = ship_settings()
    if settings is None:
        return None
    url, headers = settings
    u = urllib.parse.urlsplit(url)
    path = u.path.rstrip("/")
    if not path.endswith("/ingest") or u.scheme not in ("https", "http"):
        return None
    if u.scheme == "http" and u.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise UpdateError("only over https (or from this computer)")
    base = urllib.parse.urlunsplit((u.scheme, u.netloc, path[: -len("/ingest")], "", ""))
    return base, {k: v for k, v in headers.items() if k == "Authorization"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the upload password elsewhere: refused."""

    def redirect_request(self, *a, **k):
        return None


def _fetch(url: str, headers: dict, limit: int) -> bytes:
    req = urllib.request.Request(url, headers={**headers, "User-Agent": f"wow-stats-shipper/{VERSION}"})
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=60) as r:
            data = r.read(limit + 1)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("the server has no update endpoint") from None
        raise UpdateError(f"the server answered HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpdateError(f"server unreachable ({getattr(e, 'reason', e)})") from None
    if len(data) > limit:
        raise UpdateError(f"{url.rsplit('/', 1)[-1]} is over {limit} bytes")
    return data


def _verified(base: str, headers: dict, files: dict, name: str) -> bytes:
    spec = files.get(name) if isinstance(files, dict) else None
    want = spec.get("sha256") if isinstance(spec, dict) else None
    if not isinstance(want, str) or not re.fullmatch(r"[0-9a-f]{64}", want):
        raise UpdateError(f"no sha256 for {name}")
    data = _fetch(f"{base}{UPDATE_PATH}/{name}", headers, UPDATE_MAX_BYTES)
    if hashlib.sha256(data).hexdigest() != want:
        raise UpdateError(f"{name}: sha256 mismatch")
    return data


def _file_sha(p: Path) -> str | None:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def agent_changed(files: dict) -> tuple[str, ...]:
    """The platform's agent files when the server's differ from the copies
    next to the script; () when they match, or when there are no copies to
    compare (an install made before they were kept: left alone)."""
    names = AGENT_FILES.get("win32" if WINDOWS else sys.platform, ())
    here = [(n, _file_sha(SELF.parent / n)) for n in names]
    if not names or any(h is None for _, h in here):
        return ()
    want = {n: (files.get(n) or {}).get("sha256") if isinstance(files, dict) else None for n in names}
    return names if any(h != want[n] for n, h in here) else ()


def rerun_installer(stage: Path) -> None:
    """The platform's installer from `stage`, detached, after this run ends:
    it rewrites the agent (launchd) or the task, and reloads it. Its own
    session, so launchd's end-of-job cleanup doesn't take it along."""
    logf = open(LOG, "ab") if LOG.parent.is_dir() else subprocess.DEVNULL
    env = {**os.environ, "WOW_STATS_QUIET": "1", "WOW_STATS_CONFIG": str(config_path())}
    if WINDOWS:
        # Re-registering a running task is fine (-Force); MultipleInstances
        # IgnoreNew skips its start while this run is still on.
        ps = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
              "-File", str(stage / "install-windows.ps1")]
        flags = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        try:  # out of the task's job, when Task Scheduler allows it
            subprocess.Popen(ps, env=env, stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
                             creationflags=flags | 0x01000000)
        except OSError:
            subprocess.Popen(ps, env=env, stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
                             creationflags=flags)
    else:
        subprocess.Popen(["/bin/sh", "-c", 'sleep 5; exec /bin/bash "$1"', "sh", str(stage / "install-mac.sh")],
                         env=env, stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
                         start_new_session=True)


def apply_update(new: bytes) -> None:
    """Writes the new script next to this one, keeps this one as .prev, and
    swaps them in one rename."""
    tmp = SELF.with_name(".wow_stats.py.new")
    tmp.write_bytes(new)
    if not WINDOWS:
        tmp.chmod(0o755)
    shutil.copy2(SELF, SELF.with_name(SELF.name + ".prev"))
    os.replace(tmp, SELF)


def check_update(state: dict, now: float | None = None) -> str | None:
    """The update check (see UPDATE_EVERY_S): None when skipped, else
    "current", "updated" or "kept" (a failure, logged)."""
    if not auto_update_on() or SELF.parent != install_dir().resolve():
        return None
    up = state.setdefault("update", {})
    now = time.time() if now is None else now
    try:
        last = float(up["checked"]) if "checked" in up else None
    except (TypeError, ValueError):
        last = None
    if last is not None and 0 <= now - last < UPDATE_EVERY_S:
        return None
    up["checked"] = now
    try:
        src = update_source()
        if src is None:
            return None
        base, headers = src
        try:
            man = json.loads(_fetch(base + UPDATE_PATH, headers, 64 * 1024))
        except ValueError:
            raise UpdateError("the server's answer isn't JSON") from None
        if not isinstance(man, dict):
            raise UpdateError("the server's answer isn't a manifest")
        new_v, cur_v = version_tuple(man.get("version")), version_tuple(VERSION)
        if new_v is None:
            raise UpdateError("the server names no version")
        if cur_v is not None and new_v <= cur_v:  # never a downgrade
            return "current"
        files = man.get("files")
        new = _verified(base, headers, files, "wow_stats.py")
        try:
            compile(new, str(SELF), "exec")
        except (SyntaxError, ValueError) as e:
            raise UpdateError(f"the new version doesn't compile ({e})") from None
        m = re.search(rb'(?m)^VERSION = "([^"]+)"$', new)
        if not m or version_tuple(m.group(1).decode()) != new_v:
            raise UpdateError("the new file's version isn't the one announced")
        agent = agent_changed(files)
        stage = SELF.parent / ".update"
        if agent:
            shutil.rmtree(stage, ignore_errors=True)
            stage.mkdir()
            for n in agent:
                (stage / n).write_bytes(_verified(base, headers, files, n))
            (stage / "wow_stats.py").write_bytes(new)
        apply_update(new)
    except (UpdateError, OSError) as e:
        log(f"auto-update: {e}; keeping {VERSION}")
        return "kept"
    log(f"auto-update: {VERSION} -> {man['version']}, from the next run on"
        + ("; the background program is set up again" if agent else ""))
    if agent:
        rerun_installer(stage)
    return "updated"


def main() -> int:
    global DRY
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="extract from the live files to stdout, write nothing")
    ap.add_argument("--no-ship", action="store_true", help="archive and extract only")
    ap.add_argument("--backfill", action="store_true", help="re-extract every archived version too")
    ap.add_argument("--print-wow-dir", action="store_true", help="print the WoW folder in use (for the installers)")
    ap.add_argument("--install-recorder", action="store_true",
                    help="put the pinned Polestar Recorder in the AddOns folder (for the installers)")
    ap.add_argument("--preflight", action="store_true",
                    help="check the setup and print a checklist (the installers' last step)")
    a = ap.parse_args()
    DRY = a.dry_run or a.print_wow_dir or a.preflight
    configure()
    if a.print_wow_dir:
        print(WOW)
        return 0
    if a.preflight:
        return preflight()
    if a.install_recorder:
        try:
            # WOW_STATS_RECORDER_URL + WOW_STATS_RECORDER_SHA256: tests only (the Windows
            # end-to-end serves a fake release from localhost); both or neither, never set in production.
            url, sha = os.environ.get("WOW_STATS_RECORDER_URL"), os.environ.get("WOW_STATS_RECORDER_SHA256")
            said = install_recorder(WOW, url=url, sha256=sha) if url and sha else install_recorder(WOW)
        except RecorderError as e:  # the log line shows in the installer's window too
            log(f"Polestar Recorder was not installed ({e}). Install it by hand: the help page, "
                "Everything else, The addons, step by step.")
            return 1
        log(said)
        return 0
    if not (WOW / "WTF").is_dir():
        log(f"no WTF folder in {WOW}: set wow_dir in {config_path()}")
        return 0

    if a.dry_run:
        state: dict = {}
        out = Out(dry=True)
        load_client(state)
        extract_tree(WOW / "WTF", state, out)
        ours, latest = our_characters(WOW / "WTF")
        extract_combatlogs(ours, latest, state, out)
        setup_event(state, out, force=True)
        log("dry run:", json.dumps(dict(sorted(out.counts.items()))))
        log("dry run:", out.summary())
        return 0

    rotate_log()
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    lock = take_lock(ARCHIVE / ".lock")
    if lock is None:
        log("another run holds the lock")
        return 0

    if not wait_quiet(WOW / "WTF"):
        log("the client is still writing its files; next run")
        return 0
    keep_client()  # before the archive step, which commits our copy
    if not archive_on():  # stats only: the spool and sent/ still keep every event
        tree = WOW / "WTF"
    elif shutil.which("git"):
        archive()
        tree = REPO
    else:  # no history then: the spool and sent/ still keep every event
        log("git not found: no WTF archive this run")
        tree = WOW / "WTF"

    state = load_state()
    out = Out(dry=False)
    try:
        if a.backfill:
            backfill(state, out)
        load_client(state)  # after the backfill: the current tree's pass takes them, once
        extract_tree(tree, state, out)
        ours, latest = our_characters(tree)
        extract_combatlogs(ours, latest, state, out)
        setup_event(state, out, force=bool(out.counts))
    except Exception:
        out.discard()
        raise
    spooled = out.close()  # the spool first: a crash before the state only repeats events
    save_state(state)
    if spooled:
        log(f"extract: {spooled.name}:", json.dumps(dict(sorted(out.counts.items()))))
        log(f"extract: {spooled.name}:", out.summary())
    if not a.no_ship:
        ship()
        try:
            check_update(state)
        except Exception as e:  # noqa: BLE001 - an update bug never stops the shipping
            log(f"auto-update: {type(e).__name__}: {e}; keeping {VERSION}")
        save_state(state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # pythonw has no console: the log is the only place to see it
        log("failed:\n" + traceback.format_exc())
        sys.exit(1)
