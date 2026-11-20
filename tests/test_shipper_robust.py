"""The shipper against files it doesn't expect, and a server that redirects
(client review 2026-11-20: M1, L1, L2, L3). The degraded-mode rule: one odd
addon file costs that file's events for the run, never the shipping or the
update check, so a fix can still arrive. Crafted files under tmp_path only."""

from __future__ import annotations

import gzip
import hashlib
import http.server
import re
import shutil
import struct
import subprocess
import threading
import tomllib
import zlib

import pytest

from tests.test_shipper_stats_only import CHAR
from wowstats import account

SPOOLED = '{"_time":"2026-01-01T00:00:00.000Z","feed":"wow-x","_msg":"queued from an earlier run"}\n'


@pytest.mark.parametrize("label,src", [
    ("deep nesting", b"X = " + b"{" * 5000 + b"}" * 5000),
    ("table as key", b"X = { [{}] = 1 }"),
    ("huge \\u escape", b'X = "\\u{FFFFFFFFFFFFFFFFFFFF}"'),
])
def test_an_odd_savedvariables_file_is_skipped(shipper, tmp_path, label, src, monkeypatch):
    monkeypatch.setattr(shipper, "LOG", tmp_path / "log")
    p = tmp_path / "x.lua"
    p.write_bytes(src)
    assert shipper.read_sv(p) is None, label
    assert "cannot parse x.lua" in (tmp_path / "log").read_text()


def test_auctionator_nested_too_deep_is_a_cbor_error(shipper):
    with pytest.raises(shipper.CborError):
        shipper.cbor_decode(b"\x81" * 5000 + b"\x00")


def test_a_corrupt_state_starts_over_and_is_kept(shipper, tmp_path, monkeypatch):
    monkeypatch.setattr(shipper, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(shipper, "LOG", tmp_path / "log")
    (tmp_path / "state.json").write_text('{"char_ids": {"aa"')
    assert shipper.load_state() == {}
    assert (tmp_path / "state.json.corrupt").read_text() == '{"char_ids": {"aa"'
    (tmp_path / "state.json").write_text("[1, 2]")
    assert shipper.load_state() == {}


class Server:
    """Records each request; POSTs answer `post` (and, with a Location,
    redirect), GETs 404."""

    def __init__(self, post=200, location=None):
        self.hits = []
        me = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                me.hits.append(("POST", self.path, self.headers.get("Authorization")))
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(post)
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                me.hits.append(("GET", self.path, self.headers.get("Authorization")))
                self.send_response(200 if location is None and post == 200 and self.path == "/elsewhere" else 404)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def setup(shipper, tmp_path, monkeypatch):
    """An install whose account has a Syndicator file holding a string where
    a table goes (PoC 2), an event spooled by an earlier run, and a server."""
    srv = Server()
    wow = tmp_path / "wow"
    sv = wow / "WTF" / "Account" / "ACC" / "SavedVariables"
    sv.mkdir(parents=True)
    (sv / "Polestar.lua").write_text(CHAR)
    (sv / "Syndicator.lua").write_text('SYNDICATOR_DATA = "oops"\n')
    arch = tmp_path / "arch"
    (arch / "spool").mkdir(parents=True)
    (arch / "spool" / "20260101T000000000000Z.ndjson").write_text(SPOOLED)
    (tmp_path / "config.toml").write_text(
        f"wow_dir = '{wow}'\narchive_dir = '{arch}'\nwtf_archive = false\nlog_file = '{tmp_path / 'log'}'\n"
        f"[ship]\nurl = '{srv.base}/ingest'\nuser = 'u'\npassword = 'password-long-enough'\n")
    monkeypatch.setenv("WOW_STATS_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr(shipper, "QUIET_S", 0)
    monkeypatch.setattr(shipper.sys, "argv", ["wow_stats.py"])
    # The update check runs from the install folder only: make this one it.
    monkeypatch.setattr(shipper, "install_dir", lambda: shipper.SELF.parent)
    yield srv, arch, tmp_path / "log"
    srv.close()


def test_a_bad_addon_file_still_ships_and_checks_for_updates(shipper, setup):
    srv, arch, log = setup
    assert shipper.main() == 0
    lines = log.read_text()
    assert re.search(r"extract: \w+ skipped this run \(AttributeError", lines), lines
    posts = [h for h in srv.hits if h[0] == "POST"]
    assert posts, "nothing shipped"
    assert not list((arch / "spool").glob("*.ndjson")), "the spool stayed"
    # The earlier run's event and Polestar's, from the same account, went out.
    sent = b"".join(gzip.decompress(p.read_bytes()) for p in (arch / "sent").glob("*.gz"))
    assert b"queued from an earlier run" in sent and b"Demo-Player" in sent
    assert ("GET", "/install/update/shipper", posts[0][2]) in srv.hits, "no update check"


def test_an_extraction_that_fails_whole_still_ships(shipper, setup, monkeypatch):
    srv, arch, log = setup

    def boom(*a, **k):
        raise RuntimeError("a bug in the extraction")

    monkeypatch.setattr(shipper, "extract_tree", boom)
    assert shipper.main() == 1
    assert "extract failed; sending what is spooled" in log.read_text()
    assert any(h[0] == "POST" for h in srv.hits) and not list((arch / "spool").glob("*.ndjson"))
    assert any(h[1] == "/install/update/shipper" for h in srv.hits)


def test_ship_never_follows_a_redirect(shipper, tmp_path, monkeypatch):
    """PoC 3: a 302 from /ingest to another host carried the upload password
    there, and its 200 moved the spool to sent/ unstored."""
    other = Server()
    first = Server(post=302, location=f"{other.base}/elsewhere")
    try:
        for name in ("SPOOL", "SENT", "REJECTED", "TOO_OLD"):
            monkeypatch.setattr(shipper, name, tmp_path / name.lower())
        monkeypatch.setattr(shipper, "LOG", tmp_path / "log")
        shipper.SPOOL.mkdir()
        (shipper.SPOOL / "a.ndjson").write_text(SPOOLED)
        monkeypatch.setattr(shipper, "CONFIG", {"ship": {"url": f"{first.base}/ingest", "user": "u",
                                                         "password": "password-long-enough"}})
        shipper.ship()
        assert other.hits == []
        assert (shipper.SPOOL / "a.ndjson").read_text() == SPOOLED
        assert "HTTP 302, kept for the next run" in (tmp_path / "log").read_text()
        ok, line, _hard = shipper.server_check()
        assert not ok and "302" in line and other.hits == []
    finally:
        first.close()
        other.close()


@pytest.mark.skipif(not shutil.which("bash"), reason="no bash")
@pytest.mark.parametrize("folder", [r"/Volumes/Jeux d'été/World of Warcraft/_forever_",
                                    '/Users/a "quoted" name/_forever_', "/plain/World of Warcraft/_forever_"])
def test_the_installers_write_any_wow_folder_as_valid_toml(folder):
    """L2: a folder with an apostrophe broke the Windows config, and the Mac
    installer left the line out."""
    out = subprocess.run(["bash", "-c", account.MAC_WOW_DIR_LINE], env={"wow": folder, "PATH": "/usr/bin:/bin"},
                         capture_output=True, text=True, check=True).stdout
    assert tomllib.loads(out)["wow_dir"] == folder
    # PowerShell's String.Replace is a plain replace: the same line, built the same way.
    win = "D:\\Jeux d'été\\World of Warcraft\\_forever_"
    line = 'wow_dir = "' + win.replace("\\", "\\\\").replace('"', '\\"') + '"\n'
    assert tomllib.loads(line)["wow_dir"] == win
    assert ".Replace('\\', '\\\\').Replace('\"', '\\\"')" in account.WIN_WOW_DIR_LINE


def test_the_install_line_is_https_only():
    assert account.curl("https://wow.example/install/abc") == \
        "curl --proto '=https' --proto-redir '=https' -fsSL https://wow.example/install/abc | bash"
    assert account.curl("http://127.0.0.1:8080/install/abc") == "curl -fsSL http://127.0.0.1:8080/install/abc | bash"


def blte(block: bytes, declared: int) -> bytes:
    return (b"BLTE" + struct.pack(">I", 12 + 24) + b"\x0f" + (1).to_bytes(3, "big")
            + struct.pack(">II", len(block), declared) + hashlib.md5(block).digest() + block)


def test_blte_holds_each_block_to_its_declared_size(shipper):
    """L3: 81 KB on disk declared 16 bytes and decoded to 80 MB first."""
    bomb = b"Z" + zlib.compress(b"\0" * (80 * 1024 * 1024), 9)
    with pytest.raises(shipper.CascMissing, match="too large"):
        shipper.Blte(blte(bomb, 16)).block(0)
    with pytest.raises(shipper.CascMissing, match="too large"):  # declared over the file cap: refused unread
        shipper.Blte(blte(bomb, 80 * 1024 * 1024), limit=shipper.CASC_MAX_FILE)
    short = b"Z" + zlib.compress(b"abc")
    with pytest.raises(shipper.CascMissing, match="declared size"):
        shipper.Blte(blte(short, 10)).block(0)
    assert shipper.Blte(blte(short, 3)).read() == b"abc"
    # A block that is itself BLTE nests a little, never without end.
    inner = b"F" + blte(b"N" + b"xyz", 3)
    assert shipper.Blte(blte(inner, 3)).read() == b"xyz"
    deep = b"N" + b"xyz"
    for _ in range(4):
        deep = b"F" + blte(deep, 3)
    with pytest.raises(shipper.CascMissing, match="nested"):
        shipper.Blte(blte(deep, 3)).read()
