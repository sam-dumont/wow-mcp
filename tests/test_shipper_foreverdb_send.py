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


def echo(body: dict) -> dict:
    """What the site answers when it took everything: the counts of what was sent (a test-side count)."""
    out: dict = {"found": {}}
    if "data" in body:
        counts = {}
        for k, v in body["data"].items():
            counts[k] = (sum(len(x) for x in v.get("c", {}).values()) if k == "trails"
                         else sum(len(o["b"].split("^")) for o in v) if k == "obs" else len(v))
        out["shared"] = {"counts": counts}
    if body.get("armory"):
        out["armory"] = {"stored": True, "characters": [{"name": c["name"], "stored": True} for c in body["armory"]]}
    if "share" in body:
        sh = body["share"]
        runs = sh.get("runs", [])
        out["helper"] = {"counts": {"runs": len(runs), "kills": sum(len(r.get("kills", [])) for r in runs),
                                    "finds": len(sh.get("finds", [])), "wish": len(sh.get("wish", {}))}}
    return out


class Net:
    """The HTTP layer: records every call, answers what the test says."""

    def __init__(self, fs, words=("wolves", "bring"), status=200):
        self.calls, self.words, self.status, self.me_status, self.answer = [], list(words), status, 200, None

    def __call__(self, url, data=None, hdrs=None):
        self.calls.append((url, data, hdrs))
        if url.endswith("/api/gamewords"):
            return (200, {"words": self.words}) if self.words is not None else (503, None)
        status = self.me_status if url.endswith("/api/me/addon-file") else self.status
        return status, (self.answer(json.loads(gzip.decompress(data))) if self.answer else echo(json.loads(gzip.decompress(data))))

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
    assert sent and all(", HTTP 200" in ln for ln in sent)
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

SECRET = "fdb_s=THE-SECRET-VALUE; cf_clearance=NOT-FOR-US; other=thing"
SENT = "fdb_s=THE-SECRET-VALUE"
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
    assert comp[2]["Cookie"] == SENT and "cf_clearance" not in json.dumps(comp[2]) and comp[2]["X-FDB"] == "1" and "X-FDB-Install" not in comp[2]
    body = json.loads(gzip.decompress(comp[1]))
    assert list(body) == ["kind", "v", "addonVersion", "data", "armory"]
    assert body["armory"] == []  # no character is ticked unless the owner lists it
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
    assert "THE-SECRET" not in "".join(lines) and "fdb_s=" not in "".join(lines)
    assert "THE-SECRET" not in (arch / "foreverdb-send.json").read_text()


@pytest.mark.parametrize(("mode", "text"), [(0o644, SECRET), (0o600, "a=b\nc=d"), (0o600, "   "), (0o600, "other=1; cf_clearance=2")])
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
    p.write_text("fdb_s=NEW\n")
    sess_run(fs, env, now=9000.0, force=True)
    assert net.posts[-1][2]["Cookie"] == "fdb_s=NEW"


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


def test_only_the_fdb_s_value_is_taken_from_whatever_was_pasted(fs, tmp_path):
    p = tmp_path / "s"
    for text, want in [("abc.DEF-1_2~3%2B+/:=\n", "fdb_s=abc.DEF-1_2~3%2B+/:="), ("fdb_s=V1\n", "fdb_s=V1"), ("ab=cd\n", "fdb_s=ab=cd"),
                       ("Cookie: cf_clearance=X; fdb_s=V2; a=b", "fdb_s=V2")]:
        p.write_text(text)
        p.chmod(0o600)
        assert fs.load_session(p) == (want, None)
    assert fs.load_session(tmp_path / "missing") == (None, None)


def test_armory_is_opt_in_by_character_name(fs, env):
    wow, arch, net, _lines, _run = env
    write_acct(wow, "ACCT1")
    session(arch)
    assert sess_run(fs, env, now=1000.0, armory=["Zorbo"]) == 1
    body = json.loads(gzip.decompress(net.posts[0][1]))
    assert [c["name"] for c in body["armory"]] == ["Zorbo"] and "secret" not in body["armory"][0]
    assert fs.armory_names({"foreverdb_armory": ["A", 5]}) == ["A"] and fs.armory_names({}) == []


def test_a_cloudflare_challenge_stops_everything(fs, env, monkeypatch):
    wow, arch, net, lines, _run = env
    write_acct(wow, "ACCT1")
    session(arch)

    def challenged(url, data=None, hdrs=None):
        net.calls.append((url, data, hdrs))
        if data is not None:
            raise fs.Challenge
        return net(url, data, hdrs)

    monkeypatch.setattr(fs, "http", challenged)
    assert sess_run(fs, env, now=1000.0) == 0
    assert len([c for c in net.calls if c[1] is not None]) == 1  # no retry, no anonymous fallback
    assert "Cloudflare challenge" in "".join(lines) and "THE-SECRET" not in "".join(lines)


# ---- the answer is the proof ----------------------------------------------------------------------------------


def test_the_answer_is_checked_against_what_was_sent(fs):
    sent = {"quests": 2, "loot": 1}
    ok, line = fs.check_response({"shared": {"counts": {"quests": 2, "loot": 1, "fish": 0}}}, sent)
    assert ok and line == "quests 2/2, loot 1/1"
    ok, line = fs.check_response({"shared": {"counts": {"quests": 1, "loot": 1}}}, sent)  # partial: a warning
    assert ok and "quests MISMATCH 1/2" in line and "loot 1/1" in line
    assert not fs.check_response({"shared": {"counts": {}}}, sent)[0]  # empty
    assert not fs.check_response({}, sent)[0] and not fs.check_response(None, sent)[0]
    assert not fs.check_response({"shared": {"error": "no", "counts": {"quests": 2, "loot": 1}}}, sent)[0]
    # explained by the answer: skipped (anonymous can't have it) or already known (found)
    assert fs.check_response({"shared": {"counts": {"quests": 2}, "skipped": ["loot"]}}, sent)[0]
    ok, line = fs.check_response({"found": {"loot": 1}, "shared": {"counts": {"quests": 2}}}, sent)
    assert ok and "loot 0/1 (known already)" in line


def test_the_armory_and_helper_answers_are_checked_too(fs):
    good = {"armory": {"characters": [{"name": "A", "stored": False}]}}
    assert fs.check_response(good, {}, armory=1) == (True, "armory 1/1")
    ok, line = fs.check_response({"armory": {"characters": [], "rejected": [{"reason": "x"}]}}, {}, armory=1)
    assert ok and "armory MISMATCH" in line  # a warning
    assert fs.check_response({"armory": {"error": "no", "characters": [{"stored": True}]}}, {}, armory=1)[0]
    hs = {"runs": 1, "kills": 1, "finds": 0, "wish": 1, "tips": 4}
    assert fs.check_response({"helper": {"counts": {"runs": 1, "kills": 1, "wish": 1}}}, {}, helper=hs)[0]
    ok, line = fs.check_response({"helper": {"counts": {"runs": 1, "kills": 0, "wish": 1}}}, {}, helper=hs)
    assert ok and "kills MISMATCH 0/1" in line and not fs.check_response({}, {}, helper=hs)[0]


def test_a_section_mismatch_is_a_warning_and_the_file_is_recorded(fs, env):
    wow, arch, net, lines, _run = env
    write_acct(wow, "ACCT1")
    net.answer = lambda body: {"shared": {"counts": {"quests": 1, "loot": 1, "trails": 2, "obs": 4}}}
    assert fs.run(wow, arch, lines.append, now=1000.0) == 1
    assert any("anon, HTTP 200" in ln and "quests MISMATCH 1/2" in ln and "warnings only" in ln for ln in lines)
    assert fs.run(wow, arch, lines.append, now=1000.0 + 901) == 0 and len(net.posts) == 1  # never resent


def test_an_answer_without_counts_is_retried(fs, env):
    wow, arch, net, lines, _run = env
    write_acct(wow, "ACCT1")
    net.answer = lambda body: {"shared": {"counts": {}}}
    assert fs.run(wow, arch, lines.append, now=1000.0) == 0
    assert any("no shared counts" in ln and "will retry" in ln for ln in lines)
    st = json.loads((arch / "foreverdb-send.json").read_text())
    assert st.get("files", {}).get("ACCT1", {}).get("hash") is None
    net.answer = None  # the site answers in full: the retry sends again and is recorded
    assert fs.run(wow, arch, lines.append, now=1000.0 + 901) == 1
    assert any("quests 2/2" in ln and "loot 1/1" in ln for ln in lines)
    assert len(net.posts) == 2


def test_logged_in_answer_covers_data_armory_and_helper(fs, env):
    wow, arch, net, lines, _run = env
    write_acct(wow, "ACCT1")
    write_acct(wow, "ACCT1", HELPER, file="ForeverDB_Helper.lua")
    session(arch)
    assert sess_run(fs, env, now=1000.0, armory=["Zorbo"]) == 2
    me = [ln for ln in lines if "(logged in)" in ln]
    assert any("me, HTTP 200" in ln and "quests 2/2" in ln and "armory 1/1" in ln for ln in me)
    assert any(ln.startswith("foreverdb: helper:") and "runs 1/1" in ln and "kills 1/1" in ln for ln in me)
    assert "Zorbo" not in "".join(lines) and "THE-SECRET" not in "".join(lines)
    # an answer without the armory: the data is recorded for what matched, the file is retried for the armory
    net.answer = lambda body: {"shared": {"counts": {"quests": 2, "loot": 1, "trails": 2, "obs": 4}}}
    write_acct(wow, "ACCT1", LUA.replace('["scale"] = 1', '["scale"] = 1, ["fish"] = { [1] = { ["n"] = 1 } }'))
    assert sess_run(fs, env, now=5000.0, armory=["Zorbo"]) == 1  # warnings only: fish and the armory
    assert any("armory MISMATCH" in ln and "fish MISMATCH" in ln for ln in lines)
    net.answer = lambda body: {}
    write_acct(wow, "ACCT1", HELPER.replace('["dur"] = 90', '["dur"] = 99'), file="ForeverDB_Helper.lua")
    sess_run(fs, env, now=9000.0, armory=["Zorbo"])
    assert any("no helper counts in the answer" in ln for ln in lines)


def test_a_refused_send_logs_the_servers_message_without_secrets(fs, env):
    wow, arch, net, lines, _run = env
    write_acct(wow, "ACCT1")
    session(arch)
    net.me_status = 500
    net.answer = lambda body: {"error": "boom: bad row", "debug": "fdb_s=THE-SECRET-VALUE", "id": "A" * 40}
    assert sess_run(fs, env, now=1000.0) == 0
    line = next(ln for ln in lines if "HTTP 500" in ln)
    assert "error: " in line and "boom: bad row" in line
    assert "THE-SECRET" not in "".join(lines) and "A" * 32 not in line


def test_error_text_is_short_and_blanked(fs):
    assert len(fs.error_text({"_text": "x " * 500})) == 300
    assert fs.error_text(None) == "(no body)"
    t = fs.error_text({"_text": "Set-Cookie: fdb_s=abc123; Token: zzz tail"}, "fdb_s=abc123")
    assert "abc123" not in t and "zzz" not in t and "tail" in t


def test_dry_run_prints_section_sizes_for_both_paths(fs, env):
    wow, arch, _net, lines, run = env
    write_acct(wow, "ACCT1")
    run(dry=True)
    session(arch)
    sess_run(fs, env, dry=True)
    assert any("section bytes (anon) quests=" in ln for ln in lines)
    assert any("section bytes (logged in) quests=" in ln and "armory bytes 2" in ln for ln in lines)
