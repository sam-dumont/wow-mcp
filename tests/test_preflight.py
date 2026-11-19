"""The installers' closing checklist (wow_stats.py --preflight) and the setup
report the shipper sends with its data (feed wow-setup).

A fake WoW folder per case, a local receiver standing in for /ingest, and
on macOS the whole stock installer under a throwaway HOME with a fake
launchctl in front of the real one (the owner's own agent is never
touched: the test checks the fake is the one on PATH first).
"""

from __future__ import annotations

import base64
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tests import test_tenancy as tt
from wowstats import account

# The app with a friend on it (tests/test_tenancy.py), for the one-line installer.
vl, env, server, friend_ready = tt.vl, tt.env, tt.server, tt.friend_ready

ROOT = Path(__file__).resolve().parent.parent
OK, NO = "✓", "✗"


# ---------------------------------------------------------------- the one list


def test_the_shippers_list_is_the_steps_list(shipper):
    """steps.json is the source; the shipper (which runs on the PC, without
    the package) carries a copy that must say the same."""
    steps = account.steps()
    want = [{k: a[k] for k in ("id", "name", "dir", "required", "url", "what")} for a in steps["addons"]]
    assert want == shipper.ADDONS
    req = {a["id"]: a["required"] for a in steps["addons"]}
    either = [{"id": k["id"], "addons": k["addons"], "what": k["what"]} for k in steps["data"]
              if len(k["addons"]) > 1 and not any(req[i] for i in k["addons"])]
    assert either == shipper.EITHER


# ---------------------------------------------------------------- a fake PC


class Receiver:
    """/ingest: 200 for the right basic auth, 401 otherwise; keeps what it got."""

    def __init__(self, user="mira", password="s3cret-pass"):
        want = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        self.got: list[bytes] = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                ok = self.headers.get("Authorization") == want
                if ok:
                    outer.got.append(body)
                self.send_response(200 if ok else 401)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/ingest"

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def receiver():
    r = Receiver()
    yield r
    r.close()


def make_wow(root: Path, addons=("Polestar", "Polestar_Recorder", "Questie", "Syndicator", "Auctionator",
                                  "CharacterExport-Forever", "Narcissus", "VulcanSimBench"),
             recorder: bool | None = True, played: bool = True) -> Path:
    wow = root / "World of Warcraft" / "_forever_"
    acct = wow / "WTF" / "Account" / "123456789#1"
    (acct / "SavedVariables").mkdir(parents=True)
    for d in addons:
        (wow / "Interface" / "AddOns" / d).mkdir(parents=True)
        (wow / "Interface" / "AddOns" / d / f"{d}.toc").write_text(f"## Title: {d}\n## Version: 1.0.{len(d)}\n")
    if recorder is not None:
        (acct / "SavedVariables" / "Polestar_Recorder.lua").write_text(
            f'PolestarRecorderDB = {{\n\t["enabled"] = {str(recorder).lower()},\n\t["chunks"] = {{\n\t}},\n}}\n')
    if played:
        cdir = acct / "Living Flame" / "Hero" / "SavedVariables"
        cdir.mkdir(parents=True)
        (cdir / "Polestar.lua").write_text("PolestarCharDB = {\n}\n")
    return wow


def config(path: Path, wow: Path, url: str | None, password="s3cret-pass") -> Path:
    text = f"wow_dir = '{wow}'\narchive_dir = '{path.parent / 'archive'}'\n"
    if url:
        text += f'[ship]\nurl = "{url}"\nuser = "mira"\npassword = "{password}"\n'
    path.write_text(text)
    return path


def fake_launchctl(bin_dir: Path, registered: bool) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    f = bin_dir / "launchctl"
    f.write_text(f'#!/bin/sh\necho "$@" >> "{bin_dir}/calls"\n'
                 f'[ "$1" = print ] && exit {0 if registered else 113}\nexit 0\n')
    f.chmod(0o755)
    return f


def preflight(tmp_path: Path, conf: Path, registered=True, extra_env=None) -> tuple[int, str]:
    penv = {k: v for k, v in os.environ.items() if not k.startswith("WOW_STATS")}
    fake = fake_launchctl(tmp_path / "bin", registered)
    penv.update(WOW_STATS_CONFIG=str(conf), PATH=f"{fake.parent}{os.pathsep}{penv['PATH']}", PYTHONIOENCODING="utf-8",
               **(extra_env or {}))
    r = subprocess.run([sys.executable, str(ROOT / "shipper" / "wow_stats.py"), "--preflight"], env=penv,
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    return r.returncode, r.stdout


def line(out: str, text: str) -> str:
    hits = [ln.strip() for ln in out.splitlines() if text in ln]
    assert len(hits) == 1, (text, out)
    return hits[0]


AGENT = sys.platform == "darwin"  # the background-program line: launchd here (Task Scheduler on Windows CI)


# ---------------------------------------------------------------- each line, each way


def test_everything_in_place_is_all_ticks(tmp_path, receiver):
    wow = make_wow(tmp_path)
    rc, out = preflight(tmp_path, config(tmp_path / "c.toml", wow, receiver.url))
    assert rc == 0, out
    assert out.splitlines()[0] == "Checking your setup:"
    body = [ln.strip() for ln in out.splitlines()[1:]]
    assert all(ln.startswith(OK) for ln in body), out
    assert line(out, "WoW Forever folder") == f"{OK} WoW Forever folder: {wow}"
    assert line(out, "Polestar 1.0") == f"{OK} Polestar 1.0.8"
    assert line(out, "Recorder is on") == f"{OK} Recorder is on"
    assert line(out, "Questie") == f"{OK} Questie: quests you did before installing"
    assert line(out, "Saved game data") == f"{OK} Saved game data for 1 character"
    assert line(out, "Server") == f"{OK} Server: connected as mira"
    assert receiver.got == [b""]  # the ping: an empty, authenticated send
    # one line per addon (Questie and Altoholic share one), the folder, the
    # switch, saved data, server, and the background program on macOS
    assert len(body) == 1 + 2 + 1 + 1 + 5 + 1 + 1 + int(AGENT)


def test_two_optional_addons_missing_are_crosses_not_a_failure(tmp_path, receiver):
    wow = make_wow(tmp_path, addons=("Polestar", "Polestar_Recorder", "Syndicator", "Auctionator",
                                     "CharacterExport-Forever", "Narcissus", "VulcanSimBench"))
    rc, out = preflight(tmp_path, config(tmp_path / "c.toml", wow, receiver.url))
    assert rc == 0, out
    assert line(out, "Questie or Altoholic") == (
        f"{NO} Questie or Altoholic (optional): without one, quests you did before installing stay unknown. "
        "Get Questie: https://www.curseforge.com/wow/addons/questie")
    shutil.rmtree(wow / "Interface" / "AddOns" / "Auctionator")
    rc, out = preflight(tmp_path, config(tmp_path / "c.toml", wow, receiver.url))
    assert rc == 0
    assert line(out, "Auctionator") == (f"{NO} Auctionator (optional) adds prices from your own auction house scans. "
                                        "Get it: https://www.curseforge.com/wow/addons/auctionator")


def test_altoholic_alone_covers_the_old_quests(tmp_path, receiver):
    wow = make_wow(tmp_path, addons=("Polestar", "Polestar_Recorder", "Altoholic"))
    _, out = preflight(tmp_path, config(tmp_path / "c.toml", wow, receiver.url))
    assert line(out, "Altoholic") == f"{OK} Altoholic: quests you did before installing"


def test_required_addons_missing_say_where_to_get_them(tmp_path, receiver):
    wow = make_wow(tmp_path, addons=("Questie",), recorder=None)
    rc, out = preflight(tmp_path, config(tmp_path / "c.toml", wow, receiver.url))
    assert rc == 0, out  # the program works once they're in: not a failed install
    assert line(out, "Polestar is missing") == (
        f"{NO} Polestar is missing. Get it: https://www.curseforge.com/wow/addons/polestar")
    assert line(out, "Polestar Recorder is missing").startswith(f"{NO} Polestar Recorder is missing. Get it: https://")
    assert "Recorder is o" not in out and "can't tell" not in out  # no switch line without the Recorder


@pytest.mark.parametrize(("recorder", "want"), [
    (True, f"{OK} Recorder is on"),
    (False, f"{NO} Recorder is off. Type /psrec on once in game."),
    (None, f"{NO} Recorder: can't tell yet. Type /psrec on once in game."),
])
def test_the_recorder_switch(tmp_path, receiver, recorder, want):
    wow = make_wow(tmp_path, recorder=recorder)
    rc, out = preflight(tmp_path, config(tmp_path / "c.toml", wow, receiver.url))
    assert rc == 0
    assert line(out, "Recorder:" if recorder is None else "Recorder is") == want


def test_not_played_yet(tmp_path, receiver):
    wow = make_wow(tmp_path, played=False)
    rc, out = preflight(tmp_path, config(tmp_path / "c.toml", wow, receiver.url))
    assert rc == 0
    assert line(out, "saved game data") == f"{NO} No saved game data yet. Play a bit, then log out."


def test_no_wow_folder_is_a_hard_failure(tmp_path, receiver):
    rc, out = preflight(tmp_path, config(tmp_path / "c.toml", tmp_path / "nowhere", receiver.url))
    assert rc == 2
    assert line(out, "WoW Forever folder").startswith(f"{NO} WoW Forever folder not found. Log in to WoW once")


def test_a_refused_password_is_a_hard_failure(tmp_path, receiver):
    wow = make_wow(tmp_path)
    rc, out = preflight(tmp_path, config(tmp_path / "c.toml", wow, receiver.url, password="stale-password"))
    assert rc == 2
    assert line(out, "upload password") == (f"{NO} The server refused your upload password. Get a new install "
                                            "command on your start page.")
    assert "stale-password" not in out


def test_no_network_is_a_cross_not_a_failure(tmp_path):
    wow = make_wow(tmp_path)
    rc, out = preflight(tmp_path, config(tmp_path / "c.toml", wow, "http://127.0.0.1:9/ingest"))
    assert rc == 0
    assert line(out, "Can't reach") == (f"{NO} Can't reach 127.0.0.1 right now. Nothing is lost: it sends once "
                                        "you're back online.")


def test_no_server_set(tmp_path):
    wow = make_wow(tmp_path)
    conf = config(tmp_path / "c.toml", wow, None)
    rc, out = preflight(tmp_path, conf)
    assert rc == 0
    assert line(out, "No server set") == f"{NO} No server set yet: fill in [ship] in {conf}."


@pytest.mark.skipif(not AGENT, reason="launchd: macOS only (Task Scheduler: the Windows end-to-end)")
def test_the_background_program(tmp_path, receiver):
    wow = make_wow(tmp_path)
    conf = config(tmp_path / "c.toml", wow, receiver.url)
    rc, out = preflight(tmp_path, conf, registered=True)
    assert rc == 0 and line(out, "Background") == f"{OK} Background program: sends your data every 5 minutes"
    assert (tmp_path / "bin" / "calls").read_text() == f"print gui/{os.getuid()}/local.wow-stats\n"
    rc, out = preflight(tmp_path, conf, registered=False)
    assert rc == 2 and line(out, "Background") == (f"{NO} Background program is not registered. Run the install "
                                                   "command again.")
    rc, out = preflight(tmp_path, conf, extra_env={"WOW_STATS_NO_LOAD": "1"})
    assert rc == 0 and line(out, "Background") == f"{NO} Background program: not started (WOW_STATS_NO_LOAD=1)."


def test_ascii_marks_where_the_console_cant_show_them(tmp_path, receiver):
    wow = make_wow(tmp_path, recorder=False)
    penv = {k: v for k, v in os.environ.items() if not k.startswith("WOW_STATS")}
    penv.update(WOW_STATS_CONFIG=str(config(tmp_path / "c.toml", wow, receiver.url)), PYTHONIOENCODING="cp1252")
    r = subprocess.run([sys.executable, str(ROOT / "shipper" / "wow_stats.py"), "--preflight"], env=penv,
                       capture_output=True, timeout=60)
    out = r.stdout.decode("cp1252")
    assert r.returncode == 0 and "  [ok] Polestar 1.0.8" in out and "  [x] Recorder is off." in out, out


# ---------------------------------------------------------------- the setup report


def test_the_setup_report_goes_with_a_send_and_holds_nothing_personal(shipper, tmp_path, monkeypatch):
    wow = make_wow(tmp_path, addons=("Polestar", "Polestar_Recorder", "Syndicator"), recorder=False)
    monkeypatch.setattr(shipper, "WOW", wow)
    out = shipper.Out(dry=True)
    state: dict = {}
    lines: list[str] = []
    monkeypatch.setattr("builtins.print", lambda s, *a, **k: lines.append(s))
    shipper.setup_event(state, out, force=False)
    ev = json.loads(lines[0])
    assert ev["feed"] == "wow-setup" and "char" not in ev
    assert ev["addons"] == {"polestar": "1.0.8", "recorder": "1.0.17", "syndicator": "1.0.10"}
    assert ev["recorder_on"] is False and ev["saved_chars"] == 1
    assert set(ev) == {"_time", "feed", "_msg", "source_id", "addons", "recorder_on", "saved_chars"}
    assert str(tmp_path) not in lines[0] and "Hero" not in lines[0]
    # Unchanged and nothing else to send: not again. Something to send, or a change: again.
    shipper.setup_event(state, out, force=False)
    assert len(lines) == 1
    shipper.setup_event(state, out, force=True)
    assert len(lines) == 2
    (wow / "Interface" / "AddOns" / "Questie").mkdir()
    (wow / "Interface" / "AddOns" / "Questie" / "Questie.toc").write_text("## Version: 9\n")
    shipper.setup_event(state, out, force=False)
    assert len(lines) == 3 and json.loads(lines[2])["addons"]["questie"] == "9"


def test_the_receiver_takes_the_setup_report():
    from wowstats import ingest

    stored, bad = ingest.parse(json.dumps({"_time": "2026-11-19T10:00:00.000Z", "feed": "wow-setup", "_msg": "setup",
                                           "addons": {"polestar": "0.4.1.1"}, "recorder_on": True,
                                           "saved_chars": 2}).encode())
    assert bad == 0
    ev = json.loads(stored[0])
    assert ev["addons"] == '{"polestar":"0.4.1.1"}' and ev["recorder_on"] is True


# ---------------------------------------------------------------- the stock installer, end to end (macOS)


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac installer: macOS only")
def test_the_mac_installer_ends_with_the_checklist(tmp_path, receiver):
    """install-mac.sh as the one-line installer runs it (WOW_STATS_QUIET=1):
    its only output is the checklist; the agent is written and "loaded" by a
    fake launchctl, the Recorder comes from a local fake release."""
    home = tmp_path / "home"
    home.mkdir()
    wow = make_wow(tmp_path, addons=("Polestar", "Polestar_Recorder", "Syndicator", "Auctionator",
                                     "CharacterExport-Forever", "VulcanSimBench"))
    (home / ".config" / "wow-stats").mkdir(parents=True)
    conf = config(home / ".config" / "wow-stats" / "config.toml", wow, receiver.url)
    fake = fake_launchctl(tmp_path / "bin", True)
    penv = {k: v for k, v in os.environ.items() if not k.startswith("WOW_STATS")}
    penv.update(HOME=str(home), PATH=f"{fake.parent}{os.pathsep}{penv['PATH']}", WOW_STATS_QUIET="1",
               WOW_STATS_CONFIG=str(conf), WOW_STATS_RECORDER_URL="http://127.0.0.1:9/none.zip",
               WOW_STATS_RECORDER_SHA256="0" * 64)
    for k in ("UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR"):  # the real uv's, not a fresh HOME's
        penv.setdefault(k, subprocess.run(["uv", "cache", "dir"] if k == "UV_CACHE_DIR" else ["uv", "python", "dir"],
                                         capture_output=True, text=True).stdout.strip())
    # Never the real launchctl: the agent's label is the owner's own.
    assert shutil.which("launchctl", path=penv["PATH"]) == str(fake)
    r = subprocess.run(["bash", str(ROOT / "shipper" / "install-mac.sh")], env=penv, capture_output=True, text=True,
                       timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("Checking your setup:\n"), r.stdout  # nothing before it
    assert line(r.stdout, "Questie or Altoholic").startswith(NO)
    assert line(r.stdout, "Narcissus").startswith(NO)
    assert line(r.stdout, "Background").startswith(OK)
    assert sum(ln.strip().startswith(NO) for ln in r.stdout.splitlines()) == 2, r.stdout
    calls = (tmp_path / "bin" / "calls").read_text()
    assert "bootstrap" in calls and "kickstart" in calls
    assert (home / "Library" / "LaunchAgents" / "local.wow-stats.plist").is_file()


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac installer: macOS only")
def test_the_one_line_mac_install_as_a_friend_sees_it(tmp_path, server):
    """The line /start hands out, pasted in Terminal: the install, then the
    checklist, then one closing line; the ping reaches the app's own /ingest
    with the password the installer wrote."""
    server.t.public_url = server.base
    h = friend_ready(server)
    st, _, body = server.get("/api/me/install-code", headers={**h, "Content-Type": "application/json"},
                             method="POST", data=b"{}")
    assert st == 200, body
    cmd = json.loads(body)["mac"]
    home = tmp_path / "home"
    home.mkdir()
    wow = make_wow(tmp_path, addons=("Polestar", "Polestar_Recorder", "Syndicator", "Auctionator",
                                     "CharacterExport-Forever", "VulcanSimBench"))
    fake = fake_launchctl(tmp_path / "bin", True)
    penv = {k: v for k, v in os.environ.items() if not k.startswith("WOW_STATS")}
    for k, c in (("UV_CACHE_DIR", ["uv", "cache", "dir"]), ("UV_PYTHON_INSTALL_DIR", ["uv", "python", "dir"])):
        penv.setdefault(k, subprocess.run(c, capture_output=True, text=True).stdout.strip())
    penv.update(HOME=str(home), PATH=f"{fake.parent}{os.pathsep}{penv['PATH']}", WOW_STATS_WOW_DIR=str(wow),
               WOW_STATS_RECORDER_URL="http://127.0.0.1:9/none.zip", WOW_STATS_RECORDER_SHA256="0" * 64)
    assert shutil.which("launchctl", path=penv["PATH"]) == str(fake)  # never the owner's own agent
    r = subprocess.run(["bash", "-c", cmd], env=penv, capture_output=True, text=True, timeout=300, cwd=home)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert out.startswith("Installing wow-stats for mira...\nChecking your setup:\n"), out
    assert out.rstrip().endswith("your page fills in within 5 minutes."), out
    assert line(out, "Server") == f"{OK} Server: connected as mira"
    assert [ln.strip() for ln in out.splitlines() if ln.strip().startswith(NO)] == [
        f"{NO} Questie or Altoholic (optional): without one, quests you did before installing stay unknown. "
        "Get Questie: https://www.curseforge.com/wow/addons/questie",
        f"{NO} Narcissus (optional) adds your character's look in 3D before Battle.net knows it. "
        "Get it: https://www.curseforge.com/wow/addons/narcissus"]
    assert "password" not in out.lower()
    sample = os.environ.get("PREFLIGHT_SAMPLE")
    if sample:  # the PR's sample: the terminal, as a friend sees it
        Path(sample).write_text(f"$ {cmd}\n{out}")
