"""shipper/foreverdb_send.py: ForeverDB.lua to foreverdb.net, as the site's /send page does.

Synthetic files only; the HTTP layer is replaced, nothing leaves the test."""

import gzip
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def fs():
    spec = importlib.util.spec_from_file_location("foreverdb_send_under_test", ROOT / "shipper" / "foreverdb_send.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


LUA = """-- saved
ForeverDB_DB = {
    ["addonVersion"] = "2.5.0",
    ["armory"] = {
        ["Player-1-AAAA"] = { ["guid"] = "Player-1-AAAA", ["name"] = "Zorbo", ["level"] = 12, ["time"] = 5,
            ["secret"] = "never sent" },
    },
    ["quests"] = {
        [101] = { ["t"] = "Zorbo, bring Zorbo's sword to Zorboland", ["obj"] = "Kill 3 wolves near Zorbo",
            ["objs"] = { { ["t"] = "Tell Zorbo", }, }, ["lvl"] = 4, },
        [7] = { ["t"] = "Plain", },
    },
    ["loot"] = { [1234] = { ["n"] = 2.5 }, },
    ["trails"] = { ["v"] = 1, ["c"] = { ["a"] = { ["1"] = 1, ["2"] = 2 } }, ["junk"] = 5, ["n"] = 3 },
    ["obs"] = { ["v"] = 1, ["id"] = "ABCDEFGH12", ["out"] = { [2] = "a:1^b:2", [1] = "c:3", [3] = "bad space" } },
    ["relay"] = { ["v"] = 1, ["got"] = { ["ZZZZZZZZ99"] = { [5] = "x:9" }, ["short"] = { [1] = "q:1" } } },
    ["notes"] = { "private note" },
    ["scale"] = 1,
}
"""


def write_acct(wow: Path, name: str, text: str = LUA, file: str = "ForeverDB.lua") -> Path:
    sv = wow / "WTF" / "Account" / name / "SavedVariables"
    sv.mkdir(parents=True, exist_ok=True)
    p = sv / file
    p.write_text(text, encoding="utf-8")
    return p


class Net:
    """The HTTP layer: records every call, answers what the test says."""

    def __init__(self, fs, words=("wolves", "bring"), status=200):
        self.calls, self.words, self.status, self.me_status = [], list(words), status, 200

    def __call__(self, url, data=None, hdrs=None):
        self.calls.append((url, data, hdrs))
        if url.endswith("/api/gamewords"):
            return (200, {"words": self.words}) if self.words is not None else (503, None)
        if url.endswith("/api/me/addon-file"):
            return self.me_status, {"shared": {"counts": {"quests": 2, "loot": 1}}}
        return self.status, {"shared": {"counts": {"quests": 2, "loot": 1}}}

    @property
    def posts(self):
        return [c for c in self.calls if c[1] is not None]


@pytest.fixture
def env(fs, tmp_path, monkeypatch):
    wow, arch = tmp_path / "wow", tmp_path / "arch"
    net = Net(fs)
    monkeypatch.setattr(fs, "http", net)
    lines: list[str] = []
    return wow, arch, net, lines, lambda *a, **k: fs.run(wow, arch, lines.append, **k)


# ---- the Lua reader and JS semantics ---------------------------------------------------------------------------


def test_lua_reader_follows_the_pages_rules(fs):
    d = fs.parse_lua('A = { 1, 2, "x", }\nB = { [2] = "b", [1] = "a", name = true, [10] = nil }\n'
                     'C = "t\\tq\\"\\65\\\\" D = [[raw]] E = -0x1F F = 0x1F G = 1e2 -- c\n--[[ block ]] H = {}\n'
                     "I = { ['k'] = { 1.5 } ; z = 1 }")
    assert d["A"] == [1.0, 2.0, "x"]
    assert list(d["B"]) == ["1", "2", "10", "name"] and d["B"]["10"] is None
    assert d["C"] == 't\tq"A\\' and d["D"] == "raw"
    assert d["E"] is None and d["F"] == 31 and d["G"] == 100
    assert d["H"] == {} and d["I"] == {"k": [1.5], "z": 1}


def test_lua_reader_stops_where_the_page_stops(fs):
    assert fs.parse_lua("A = 1\nnot an assignment\nB = 2") == {"A": 1}
    with pytest.raises(fs.LuaError):
        fs.parse_lua("A = ?")


@pytest.mark.parametrize(("v", "js"), [(1, "1"), (100.0, "100"), (1.5, "1.5"), (1e21, "1e+21"), (1e-7, "1e-7"),
                                       (0.000001, "0.000001"), (123456789012345680000.0, "123456789012345680000"),
                                       (-2.5, "-2.5"), (float("nan"), "null")])
def test_numbers_print_like_javascript(fs, v, js):
    assert fs.js_num(v) == js


def test_stringify_is_json_stringify(fs):
    assert fs.stringify({"b": [1.0, None, True, "é\n\x01"], "a": {}}) == '{"b":[1,null,true,"é\\n\\u0001"],"a":{}}'


# ---- discovery -------------------------------------------------------------------------------------------------


def test_finds_each_accounts_file_oldest_first(fs, tmp_path):
    wow = tmp_path / "wow"
    a = write_acct(wow, "ACCT2")
    b = write_acct(wow, "ACCT1")
    write_acct(wow, "ACCT3", file="ForeverDB_Helper.lua")  # the page's anonymous path never sends it
    write_acct(wow, "ACCT4", file="ForeverDB.lua.bak")
    (wow / "WTF" / "Account" / "SavedVariables").mkdir()
    os.utime(a, (100, 100))
    os.utime(b, (50, 50))
    assert [f for _, f in fs.account_files(wow)] == [b, a]
    assert fs.account_files(tmp_path / "nowhere") == []


# ---- the request -----------------------------------------------------------------------------------------------


def test_request_is_what_the_page_builds(fs, env):
    wow, _arch, net, _lines, run = env
    write_acct(wow, "ACCT1")
    assert run() == 1
    (url, body, hdrs), = net.posts
    assert url == "https://foreverdb.net/api/anon/addon-file"
    assert hdrs["Content-Type"] == "application/gzip" and hdrs["X-FDB"] == "1"
    assert fs.INSTALL_RE.fullmatch(hdrs["X-FDB-Install"]) and "Authorization" not in hdrs
    p = json.loads(gzip.decompress(body))
    assert list(p) == ["kind", "v", "addonVersion", "data", "armory"]
    assert p["kind"] == "fdb-upload" and p["v"] == 1 and p["addonVersion"] == "2.5.0" and p["armory"] == []
    data = p["data"]
    assert sorted(data) == ["loot", "obs", "quests", "trails"]  # notes, armory, settings, relay stay home
    assert data["trails"] == {"v": 1, "c": {"a": {"1": 1, "2": 2}}, "n": 3}
    assert list(data["quests"]) == ["7", "101"]  # JS object order: integer keys ascending
    assert "private" not in json.dumps(p) and "never sent" not in json.dumps(p)
    assert data["quests"]["101"]["t"] == "$N, bring $N's sword to Zorboland"  # whole words only
    assert data["quests"]["101"]["obj"] == "Kill 3 wolves near $N" and data["quests"]["101"]["objs"] == [{"t": "Tell $N"}]


def test_name_scrub_skips_game_words_and_short_names(fs):
    s = {"quests": {"1": {"t": "Wolves hunt B and Wolves", "obj": "Wolves", "objs": {"1": {"t": "Wolves!"}}}}}
    fs.scrub_names(s, ["Wolves", "B", "wolves2", None, "Wolves"], {"wolves"})  # a game word, too short, not letters
    assert s["quests"]["1"]["t"] == "Wolves hunt B and Wolves"
    s = {"quests": {"1": {"t": "Ann and Annabel Ann", "objs": [{"t": "Annabel"}]}}}
    fs.scrub_names(s, ["Ann", "Annabel"], set())
    assert s["quests"]["1"]["t"] == "$N and $N $N" and s["quests"]["1"]["objs"][0]["t"] == "$N"


def test_observations_are_filtered_like_the_page(fs):
    out = fs.obs_list({"obs": {"v": 1, "id": "ABCDEFGH12", "out": {"2": "a:1^b:2", "1": "c:3", "3": "bad space",
                                                                  "0": "z:1", "x": "q:1"}},
                       "relay": {"v": 1, "got": {"ABCDEFGH12": {"1": "dup:1", "9": "r:1"}, "short": {"1": "q:1"}}}})
    assert [(o["i"], o["s"], o["b"], o["own"]) for o in out] == [
        ("ABCDEFGH12", 1, "c:3", True), ("ABCDEFGH12", 2, "a:1^b:2", True), ("ABCDEFGH12", 9, "r:1", False)]
    assert fs.obs_list({"obs": {"v": 2, "id": "ABCDEFGH12", "out": {"1": "a"}}}) == []


def test_counts_follow_the_pages_rules(fs):
    c = fs.counts_of({"quests": {"1": {}, "2": {}}, "encounters": {"a": {"fights": {"x": 1, "y": 2}}},
                      "auctions": {"r": {"i": {"1": 1, "2:3": 2}}}, "trails": {"c": {"a": {"1": 1}, "b": {"1": 1}}},
                      "loot": 5, "fish": {}})
    assert c == {"quests": 2, "encounters": 2, "auctions": 1, "trails": 2}


def test_nothing_to_share_sends_nothing(fs, env):
    wow, _a, net, _l, run = env
    write_acct(wow, "ACCT1", 'ForeverDB_DB = { ["scale"] = 1, ["quests"] = {} }')
    write_acct(wow, "ACCT2", "Other = 1")
    assert run() == 0 and net.posts == []


# ---- change detection, state, rate limit -----------------------------------------------------------------------


def test_sends_once_then_only_what_changed(fs, env):
    wow, arch, net, lines, run = env
    p = write_acct(wow, "ACCT1")
    assert run(now=1000.0) == 1
    st = json.loads((arch / "foreverdb-send.json").read_text())
    assert fs.INSTALL_RE.fullmatch(st["install"]) and st["at"] == 1000.0 and st["files"]["ACCT1"]["hash"]
    assert run(now=5000.0) == 0 and len(net.posts) == 1  # same stamp: not even read
    os.utime(p, (1, 1))  # touched, same content: read, same hash, not sent
    assert run(now=6000.0) == 0 and len(net.posts) == 1
    p.write_text(LUA.replace('["scale"] = 1', '["scale"] = 1, ["fish"] = { [1] = { ["n"] = 1 } }'))
    assert run(now=7000.0) == 1 and len(net.posts) == 2
    assert net.posts[0][2]["X-FDB-Install"] == net.posts[1][2]["X-FDB-Install"]  # the same install code
    assert run(now=8000.0, force=True) == 1 and len(net.posts) == 3  # --force sends whatever
    sent = [ln for ln in lines if "sent " in ln]
    assert sent and all("bytes, HTTP 200" in ln for ln in sent)
    assert "Zorbo" not in "".join(lines) and "ACCT1" not in "".join(lines)  # no names in the log


def test_at_most_one_attempt_every_fifteen_minutes(fs, env):
    wow, _a, net, _l, run = env
    p = write_acct(wow, "ACCT1")
    assert run(now=1000.0) == 1
    p.write_text(LUA.replace('["scale"] = 1', '["scale"] = 2, ["fish"] = { [1] = { ["n"] = 1 } }'))
    assert run(now=1000.0 + 600) == 0 and len(net.posts) == 1  # too soon
    assert run(now=1000.0 + 901) == 1 and len(net.posts) == 2


def test_a_failed_send_is_not_remembered(fs, env):
    wow, arch, net, lines, run = env
    write_acct(wow, "ACCT1")
    net.status = 503
    assert run(now=1000.0) == 0
    assert "HTTP 503" in "".join(lines)
    st = json.loads((arch / "foreverdb-send.json").read_text())
    assert "ACCT1" not in st.get("files", {})
    net.status = 200
    assert run(now=1000.0 + 901) == 1


def test_without_the_word_list_nothing_is_sent(fs, env):
    wow, _a, net, lines, run = env
    write_acct(wow, "ACCT1")
    net.words = None  # the page would send the quests' text unscrubbed; this waits
    assert run() == 0 and net.posts == []
    assert "word list" in "".join(lines)


def test_unreadable_file_is_logged_and_skipped(fs, env):
    wow, _a, net, lines, run = env
    write_acct(wow, "ACCT1", "ForeverDB_DB = { [1] = ? }")
    assert run() == 0 and net.posts == []
    assert "not read" in "".join(lines)


# ---- dry run and the switch ------------------------------------------------------------------------------------


def test_dry_run_reports_size_and_shape_and_sends_nothing(fs, env):
    wow, arch, net, lines, run = env
    write_acct(wow, "ACCT1")
    assert run(dry=True) == 1
    assert net.posts == [] and not arch.exists()
    line = next(ln for ln in lines if "would send" in ln)
    assert "bytes JSON" in line and "gzipped" in line and "'data'" in line and "quests=2" in line
    assert "Zorbo" not in line


def test_disabled_means_nothing_is_sent(fs, env, monkeypatch):
    wow, arch, net, _l, _run = env
    write_acct(wow, "ACCT1")
    monkeypatch.delenv("WOW_STATS_FOREVERDB_SEND", raising=False)

    class Shipper:
        CONFIG: dict = {}
        WOW, ARCHIVE = wow, arch
        log = staticmethod(lambda *a: None)

    assert fs.maybe_send(Shipper) == 0
    Shipper.CONFIG = {"foreverdb_send": False}
    assert fs.maybe_send(Shipper) == 0
    assert net.calls == []
    Shipper.CONFIG = {"foreverdb_send": True}
    assert fs.maybe_send(Shipper) == 1 and len(net.posts) == 1


@pytest.mark.parametrize(("conf", "env_v", "want"), [({}, None, False), ({"foreverdb_send": True}, None, True),
                                                      ({"foreverdb_send": "off"}, None, False),
                                                      ({}, "1", True), ({"foreverdb_send": True}, "0", False)])
def test_switch(fs, monkeypatch, conf, env_v, want):
    monkeypatch.delenv("WOW_STATS_FOREVERDB_SEND", raising=False)
    if env_v is not None:
        monkeypatch.setenv("WOW_STATS_FOREVERDB_SEND", env_v)
    assert fs.enabled(conf) is want


def test_shipper_hook_survives_a_missing_module(shipper):
    """wow_stats.py runs without the file next to it (an older install)."""
    src = (ROOT / "shipper" / "wow_stats.py").read_text()
    assert "import foreverdb_send" in src and "except ImportError" in src


def test_enabled_but_module_missing_says_so():
    """The hook logs one clear line when the setting is on and the file isn't beside the script."""
    src = (ROOT / "shipper" / "wow_stats.py").read_text()
    assert "foreverdb_send.py is not next to this script" in src


# ---- the owner's own session: as the logged-in page --------------------------------------------------------------

SECRET = "sid=THE-SECRET-VALUE; other=thing"
HELPER = """ForeverDBHelperDB = {
    ["gen"] = 1,
    ["share"] = { ["updated"] = 5, ["schema"] = 2,
        ["runs"] = { { ["id"] = "r1", ["map"] = 33, ["dur"] = 90, ["note"] = "private note",
            ["kills"] = { { ["npc"] = 7, ["boss"] = "B", ["secret"] = "x",
                ["loot"] = { { ["item"] = 5, ["n"] = 1, ["who"] = "Someone" } } } },
            ["wipes"] = { { ["npc"] = 7, ["hp"] = 5 } } } },
        ["tips"] = { { ["id"] = 1, ["text"] = "a tip", ["owner"] = "Zorbo" } },
        ["wish"] = { ["123"] = { ["added"] = 1, ["name"] = "Item" }, ["bad"] = { ["added"] = 1 } },
        ["goals"] = { { ["id"] = 1, ["kind"] = "k", ["title"] = "my private goal" } },
    },
}
"""


def session(arch: Path, mode: int = 0o600, text: str = SECRET + "\n") -> Path:
    arch.mkdir(parents=True, exist_ok=True)
    p = arch / "foreverdb-session"
    p.write_text(text)
    p.chmod(mode)
    return p


def sess_run(fs, env, **kw):
    wow, arch, _net, lines, _run = env
    return fs.run(wow, arch, lines.append, session_file=arch / "foreverdb-session", **kw)


def test_session_sends_like_the_logged_in_page(fs, env):
    wow, arch, net, lines, _run = env
    write_acct(wow, "ACCT1")
    write_acct(wow, "ACCT1", HELPER, file="ForeverDB_Helper.lua")
    session(arch)
    assert sess_run(fs, env, now=1000.0) == 2
    comp, helper = net.posts
    assert comp[0] == helper[0] == "https://foreverdb.net/api/me/addon-file"
    assert comp[2]["Cookie"] == SECRET and comp[2]["X-FDB"] == "1" and "X-FDB-Install" not in comp[2]
    body = json.loads(gzip.decompress(comp[1]))
    assert list(body) == ["kind", "v", "addonVersion", "data", "armory"]
    assert [c["name"] for c in body["armory"]] == ["Zorbo"] and "secret" not in body["armory"][0]
    assert body["data"]["quests"]["101"]["t"].startswith("$N,")
    share = json.loads(gzip.decompress(helper[1]))
    assert list(share) == ["share"]
    s = share["share"]
    assert s["runs"] == [{"id": "r1", "map": 33, "dur": 90,
                          "kills": [{"npc": 7, "boss": "B", "loot": [{"item": 5, "n": 1}]}],
                          "wipes": [{"npc": 7, "hp": 5}]}]
    assert s["tips"] == [{"id": 1, "text": "a tip"}] and s["wish"] == {"123": {"added": 1}}
    assert s["goals"] == [{"id": 1, "kind": "k", "tasks": []}] and s["schema"] == 2
    assert "private" not in json.dumps(s) and "Someone" not in json.dumps(s)
    # nothing new: nothing sent; new Helper data: only the Helper goes
    assert sess_run(fs, env, now=5000.0) == 0 and len(net.posts) == 2
    write_acct(wow, "ACCT1", HELPER.replace('["dur"] = 90', '["dur"] = 91'), file="ForeverDB_Helper.lua")
    assert sess_run(fs, env, now=7000.0) == 1 and len(net.posts) == 3
    assert json.loads(gzip.decompress(net.posts[2][1]))["share"]["runs"][0]["dur"] == 91
    # logged-in memory is its own: the anonymous send was never made
    assert all("anon" not in c[0] for c in net.posts)


def test_nothing_secret_in_the_log_or_the_state(fs, env):
    wow, arch, _net, lines, _run = env
    write_acct(wow, "ACCT1")
    session(arch)
    sess_run(fs, env, now=1000.0)
    assert "THE-SECRET" not in "".join(lines) and "sid=" not in "".join(lines)
    assert "THE-SECRET" not in (arch / "foreverdb-send.json").read_text()


@pytest.mark.parametrize(("mode", "text"), [(0o644, SECRET), (0o600, "a=b\nc=d"), (0o600, "   ")])
def test_a_loose_or_odd_session_file_is_refused_once_and_anonymous_is_used(fs, env, mode, text):
    wow, arch, net, lines, _run = env
    write_acct(wow, "ACCT1")
    p = session(arch, mode, text)
    assert sess_run(fs, env, now=1000.0) == 1
    assert net.posts[0][0].endswith("/api/anon/addon-file") and "Cookie" not in net.posts[0][2]
    refusals = [ln for ln in lines if "not using" in ln]
    assert len(refusals) == 1 and str(p) in refusals[0] and "THE-SECRET" not in refusals[0]
    p.write_text(text + " ")  # touched: still refused, not said again
    assert sess_run(fs, env, now=1000.0 + 901, force=True) == 1
    assert len([ln for ln in lines if "not using" in ln]) == 1


def test_a_session_file_owned_by_someone_else_is_refused(fs, env, monkeypatch):
    wow, arch, net, lines, _run = env
    write_acct(wow, "ACCT1")
    session(arch)
    real_uid = os.getuid()
    monkeypatch.setattr(fs.os, "getuid", lambda: real_uid + 1)
    sess_run(fs, env, now=1000.0)
    assert "must be owned by you" in "".join(lines) and "Cookie" not in net.posts[0][2]


def test_an_expired_session_says_so_and_falls_back_to_anonymous(fs, env):
    wow, arch, net, lines, _run = env
    write_acct(wow, "ACCT1")
    write_acct(wow, "ACCT1", HELPER, file="ForeverDB_Helper.lua")
    p = session(arch)
    net.me_status = 401
    assert sess_run(fs, env, now=1000.0) == 1
    urls = [c[0] for c in net.posts]
    assert urls == ["https://foreverdb.net/api/me/addon-file", "https://foreverdb.net/api/anon/addon-file"]
    assert f"foreverdb.net session expired: paste a new one into {p}" in lines
    assert "Cookie" not in net.posts[1][2]  # the anonymous send carries no session, and no Helper
    assert "THE-SECRET" not in "".join(lines)
    # the same expired cookie is not tried (or complained about) again; a new one is
    write_acct(wow, "ACCT1", LUA.replace('["scale"] = 1', '["scale"] = 1, ["fish"] = { [1] = { ["n"] = 1 } }'))
    sess_run(fs, env, now=3000.0)
    assert len(net.posts) == 3 and net.posts[2][0].endswith("/anon/addon-file")
    assert len([ln for ln in lines if "session expired" in ln]) == 1
    net.me_status = 200
    p.write_text("sid=NEW\n")
    sess_run(fs, env, now=9000.0, force=True)
    assert net.posts[-1][2]["Cookie"] == "sid=NEW"


def test_the_session_goes_to_foreverdb_only(fs):
    with pytest.raises(OSError, match="anywhere but foreverdb.net"):
        fs.http("https://example.com/api/me/addon-file", b"x", {"Cookie": SECRET})
    with pytest.raises(OSError, match="anywhere but foreverdb.net"):
        fs.http("https://foreverdb.net.evil.test/api/me/addon-file", b"x", {"cookie": SECRET})
    assert fs.ME_URL.startswith(fs.SITE + "/") and fs.SEND_URL.startswith(fs.SITE + "/")


def test_session_path_setting(fs, tmp_path):
    assert fs.session_path({}, tmp_path) == tmp_path / "foreverdb-session"
    assert fs.session_path({"foreverdb_session_file": ""}, tmp_path) is None
    assert fs.session_path({"foreverdb_session_file": str(tmp_path / "x")}, tmp_path) == tmp_path / "x"
