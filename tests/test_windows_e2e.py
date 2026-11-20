"""A friend's whole Windows path on a real Windows: install, ship, update,
reset, uninstall. CI runs it on windows-latest (ci.yml, job windows);
anywhere else it skips. It changes the machine (a scheduled task, the
execution policy, folders under C:\\), so it runs only with
WOW_STATS_WINDOWS_E2E=1.

The app runs here, as in tests/test_tenancy.py: a friend with a name, the
fake VictoriaLogs that keeps what /ingest stores per tenant. The friend's
side runs exactly the lines the app hands out (/api/me/install-code,
/api/me's update line, the help's uninstall line from steps.json), pasted
into Windows PowerShell 5.1 or PowerShell 7 with the execution policy of a
fresh Windows (Restricted), no uv and no Python on PATH.

Two profiles:
- the runner's own user: the WoW folder where Battle.net puts it, found by
  the installer's search; Task Scheduler itself runs the task.
- "Zoë Ü": the profile folders (USERPROFILE, APPDATA, LOCALAPPDATA, TEMP)
  under C:\\Users\\Zoë Ü, the WoW folder given with WOW_STATS_WOW_DIR as the
  help says, and no Git. Task Scheduler runs tasks with the account's real
  profile, so the ship runs the task's registered command line with that
  environment.
"""

from __future__ import annotations

import hashlib
import http.server
import io
import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
import zipfile
from pathlib import Path

import pytest

from tests.test_shipper_client import CHUNKS, MAPS, lua_str
from tests.test_tenancy import FRIEND, OWNER, TenantVL, friend_ready
from wowstats import account, tenancy, ui

ON_WINDOWS_CI = pytest.mark.skipif(os.name != "nt" or os.environ.get("WOW_STATS_WINDOWS_E2E") != "1",
                                   reason="the Windows end-to-end test runs on Windows CI only (WOW_STATS_WINDOWS_E2E=1)")

ROOT = Path(__file__).resolve().parent.parent
ZOE = Path("C:/Users/Zoë Ü")
BATTLENET_WOW = Path("C:/Program Files (x86)/World of Warcraft/_forever_")
# An apostrophe too: it once made the config unreadable (client review 2026-11-20, L2).
ZOE_WOW = Path("C:/Jeux d'Zoë/World of Warcraft/_forever_")
CHARS = {"aaaa": "Hero-Alpha", "bbbb": "Zoëlle-Beta"}  # an accented character folder too
FIRST = [k for k in CHUNKS if k[0] in CHARS and k != ("bbbb", 2)]


# ---------------------------------------------------------------- the fake release

def release_zip(version: str) -> bytes:
    """A zip shaped like Polestar Recorder's release: everything under Polestar_Recorder/."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Polestar_Recorder/Polestar_Recorder.toc",
                   f"## Interface: 16001\n## Title: Polestar Recorder\n## Version: {version}\n"
                   "## SavedVariables: PolestarRecorderDB\nRecorder.lua\n")
        z.writestr("Polestar_Recorder/Recorder.lua", f"-- {version}\n")
    return buf.getvalue()


@pytest.fixture
def recorder_release():
    """The Recorder's release served from localhost (no GitHub in the test),
    through the shipper's test-only WOW_STATS_RECORDER_URL/_SHA256. Returns
    the PowerShell prefix that sets both."""
    data = release_zip("0.4.1.1")

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/Polestar_Recorder-0.4.1.1.zip"

    def prefix(sha: str | None = None) -> str:
        return (f"$env:WOW_STATS_RECORDER_URL = '{url}'; "
                f"$env:WOW_STATS_RECORDER_SHA256 = '{sha or hashlib.sha256(data).hexdigest()}'; ")

    yield prefix
    srv.shutdown()


def tree_digest(root: Path) -> dict:
    return {str(f.relative_to(root)): hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(root.rglob("*"))
            if f.is_file()}


# ---------------------------------------------------------------- the PC

def write_wtf(wow: Path, keys) -> None:
    """The account's Polestar_Recorder.lua with keys' chunks, each character's
    Polestar_Recorder.lua and Polestar.lua, the shape the addons write."""
    acct = wow / "WTF" / "Account" / "123456789#1"
    (acct / "SavedVariables").mkdir(parents=True, exist_ok=True)
    body = ",\n".join(f"[{i}] = {lua_str(CHUNKS[k])}" for i, k in enumerate(keys, 1))
    maps = ",\n".join(f"[{k}] = {lua_str(v)}" for k, v in MAPS.items())
    (acct / "SavedVariables" / "Polestar_Recorder.lua").write_text(
        f'PolestarRecorderDB = {{\n["chunks"] = {{\n{body}\n}},\n["maps"] = {{\n{maps}\n}},\n}}\n', encoding="utf-8")
    alts = "".join(f'["{n.replace("-", " ")}"] = {{\n["name"] = "{n.split("-")[0]}",\n["seen"] = 1794928683,\n'
                   f'["level"] = 12,\n}},\n' for n in CHARS.values())
    (acct / "SavedVariables" / "Polestar.lua").write_text(
        f'PolestarDB = {{\n["alts"] = {{\n["chars"] = {{\n{alts}}},\n}},\n}}\n', encoding="utf-8")
    for cid, name in CHARS.items():
        d = acct / "Living Flame" / name / "SavedVariables"
        d.mkdir(parents=True, exist_ok=True)
        (d / "Polestar_Recorder.lua").write_text(f'PolestarRecorderCharDB = {{\n["id"] = "{cid}",\n}}\n',
                                                 encoding="utf-8")


def profile_env(profile: Path | None) -> dict:
    """A fresh PC's environment: no uv, no Python on PATH; for Zoë no Git
    either (most gaming PCs have none)."""
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("UV_", "VIRTUAL_ENV", "PYTHON", "PSEXECUTIONPOLICY", "WOW_STATS"))
           # A window opened from the Start menu: its own module path, not the
           # PowerShell 7 one this test runs under (5.1 can't load 7's modules).
           and k.upper() != "PSMODULEPATH"
           # The installers find WoW where a PC keeps it (Program Files, C:\Jeux ...):
           # this test runs only on the throwaway Windows CI runner (ON_WINDOWS_CI),
           # whose fake installs it made itself, so the fixtures-only guard is off here.
           and k.upper() != "NO_REAL_WOW"}
    keep = []
    for p in env.get("PATH", "").split(os.pathsep):
        exes = ("uv.exe", "python.exe", "python3.exe") + (("git.exe",) if profile is not None else ())
        if p and not any((Path(p) / exe).is_file() for exe in exes):
            keep.append(p)
    env["PATH"] = os.pathsep.join(keep)
    if profile is not None:
        env.update(USERPROFILE=str(profile), HOMEDRIVE=profile.drive, HOMEPATH=str(profile)[len(profile.drive):],
                   APPDATA=str(profile / "AppData" / "Roaming"), LOCALAPPDATA=str(profile / "AppData" / "Local"),
                   TEMP=str(profile / "AppData" / "Local" / "Temp"), TMP=str(profile / "AppData" / "Local" / "Temp"))
        for k in ("APPDATA", "LOCALAPPDATA", "TEMP"):
            Path(env[k]).mkdir(parents=True, exist_ok=True)
    return env


def ps(shell: str, script: str, env: dict, timeout: int = 900, encoding: str = "oem") -> tuple[int, str]:
    """script pasted into a new PowerShell window: -Command, which the
    execution policy doesn't cover, as it doesn't cover what someone types;
    in the home folder, where a new window starts."""
    r = subprocess.run([shell, "-NoProfile", "-Command", script], env=env, capture_output=True, timeout=timeout,
                       cwd=env["USERPROFILE"])
    out = (r.stdout + r.stderr).decode(encoding, "replace")
    print(f"--- {shell}: {script[:120]}\n{out}\n--- exit {r.returncode}")
    return r.returncode, out


def task(env: dict) -> dict | None:
    """The scheduled task as Windows has it, None when there is none."""
    _, out = ps("powershell", "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
                "$t = Get-ScheduledTask -TaskName wow-stats -ErrorAction SilentlyContinue; "
                "if ($t) { $i = $t | Get-ScheduledTaskInfo; "
                "@{ execute = $t.Actions[0].Execute; arguments = $t.Actions[0].Arguments; "
                "wd = $t.Actions[0].WorkingDirectory; state = \"$($t.State)\"; last = $i.LastTaskResult; "
                "triggers = @($t.Triggers | ForEach-Object { $_.CimClass.CimClassName }) } "
                "| ConvertTo-Json -Compress } else { 'none' }", env, encoding="utf-8")
    line = out.strip().splitlines()[-1] if out.strip() else "none"
    return None if line == "none" else json.loads(line)


def wait_idle(env: dict, limit: float = 300) -> None:
    deadline = time.time() + limit
    while time.time() < deadline:
        t = task(env)
        if t is None or t["state"] != "Running":
            return
        time.sleep(3)
    raise AssertionError("the task is still running after 5 minutes")


def rmtree(p: Path) -> None:
    """git makes its objects read-only, which stops shutil.rmtree on Windows."""
    for f in p.rglob("*") if p.is_dir() else []:
        os.chmod(f, stat.S_IWRITE | stat.S_IREAD)
    shutil.rmtree(p, ignore_errors=True)
    assert not p.exists(), p


def clean(env: dict) -> None:
    ps("powershell", "schtasks /Delete /TN wow-stats /F 2>$null", env)
    home = Path(env["USERPROFILE"])
    for p in (Path(env["LOCALAPPDATA"]) / "wow-stats", Path(env["APPDATA"]) / "wow-stats", home / "WoWArchive",
              BATTLENET_WOW.parent, ZOE_WOW.parent.parent):
        rmtree(p)


# ---------------------------------------------------------------- the app

def test_windows_scripts_are_ascii():
    """Windows PowerShell 5.1 reads a .ps1 without a BOM as the ANSI code
    page: one UTF-8 character and the script breaks there."""
    raw = account.shipper_file("install-windows.ps1")
    assert raw.isascii(), [ln for ln in raw.splitlines() if not ln.isascii()]


@pytest.fixture(scope="module")
def fresh_policy():
    """LocalMachine Restricted for both PowerShells (Windows 11's default for
    5.1; 7's own default, RemoteSigned, is looser), CurrentUser unset. Put
    back afterwards: the runner's own steps are scripts."""
    before = {}
    env = profile_env(None)

    def run(sh, cmd):
        r = subprocess.run([sh, "-NoProfile", "-Command", cmd], capture_output=True, text=True, env=env)
        print(sh, cmd, r.stdout, r.stderr)
        return r.stdout.strip()

    for sh in ("powershell", "pwsh"):
        run(sh, "Get-ExecutionPolicy -List | Out-String")
        before[sh] = run(sh, "Get-ExecutionPolicy -Scope LocalMachine")
        run(sh, "Set-ExecutionPolicy Restricted -Scope LocalMachine -Force; "
                "Set-ExecutionPolicy Undefined -Scope CurrentUser -Force")
        assert run(sh, "Get-ExecutionPolicy") == "Restricted", sh
    yield
    for sh, was in before.items():
        run(sh, f"Set-ExecutionPolicy {was or 'Undefined'} -Scope LocalMachine -Force")


@pytest.fixture
def lab(tmp_path, run_app):
    vl = TenantVL()
    master = tmp_path / "master.key"
    master.write_text(tenancy.b64u(b"m" * 32))
    friends = tmp_path / "friends.json"
    friends.write_text(json.dumps({FRIEND: "mira"}))
    env = {"BIND": "127.0.0.1", "VIEW": "full", "AUTH": "proxy", "PROXY_TRUSTED_CIDRS": "127.0.0.1/32", "VL_URL": vl.url, "VM_URL": vl.url,
           "FRIENDS_VL_URL": vl.url, "WOW_STATE_DIR": str(tmp_path / "state"), "WOW_OWNER_SUB": OWNER,
           "WOW_OWNER_SLUG": "sam", "WOW_FRIENDS_FILE": str(friends),
           "SITE_DOMAIN": "m.example", "PUBLIC_URL": "http://127.0.0.1", "ZOGZOG_MASTER_KEY_FILE": str(master),
           "ZOGZOG_KEY_FILE": str(tmp_path / "owner-zogzog.key"), "MODEL3D": "off",
           "MODEL3D_CACHE_DIR": str(tmp_path / "cache"), "ANALYSIS_STATE_DIR": str(tmp_path / "owner-analysis")}
    app = ui.App(env)
    t = ui.tenancy_of(env, app)
    r = run_app(app)
    r.srv.RequestHandlerClass = ui.make_handler(app, t)
    t.public_url = r.base  # the address the commands carry: this one
    r.t, r.vl = t, vl
    yield r
    vl.close()


def stored(lab, u) -> list[dict]:
    return list(lab.vl.inserted.get(str(u.account_id), []))


def wait_for(lab, u, pred, what: str, env: dict, limit: float = 240) -> list[dict]:
    deadline = time.time() + limit
    while time.time() < deadline:
        evs = [e for e in stored(lab, u) if pred(e)]
        if evs:
            return evs
        time.sleep(2)
    log = Path(env["LOCALAPPDATA"]) / "wow-stats" / "wow-stats.log"
    tail = log.read_text(encoding="utf-8", errors="replace")[-3000:] if log.exists() else "(no log)"
    raise AssertionError(f"no {what} at /ingest after {limit} s; task {task(env)}; log:\n{tail}")


def install_line(lab, h: dict, reset: bool = False) -> str:
    st, _, body = lab.get("/api/me/install-code", headers={**h, "Content-Type": "application/json"}, method="POST",
                          data=json.dumps({"reset": True} if reset else {}).encode())
    assert st == 200, body
    return json.loads(body)["windows"]


def checklist(out: str) -> None:
    """The installer's closing checklist (wow_stats.py --preflight) for this
    PC: the Recorder in, Polestar not (write_wtf adds no addon), the switch
    not saved yet, two characters, the app reached, the task there. The
    marks and Zoë's accented folder are the console's business (a pipe
    here, read as the OEM code page): the ASCII text is checked."""
    assert "Checking your setup:" in out, out
    want = ["WoW Forever folder: ", "_forever_", "Polestar is missing. Get it: https://",
            "Polestar Recorder 0.4.1.1", "Recorder: can't tell yet. Type /psrec on once in game.",
            "Questie or Altoholic (optional)", "Saved game data for 2 characters", "Server: connected as mira",
            "Background program: sends your data every 5 minutes"]
    for w in want:
        assert w in out, (w, out)
    assert out.index("Checking your setup:") < out.index("wow-stats is installed.")


def config_password(conf: Path) -> str:
    return re.search(r'^password = "([^"]+)"$', conf.read_text(encoding="utf-8"), re.M).group(1)


# ---------------------------------------------------------------- the path

@ON_WINDOWS_CI
@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
@pytest.mark.parametrize("who", ["zoe", "runner"])
def test_friend_installs_ships_updates_and_uninstalls(fresh_policy, lab, recorder_release, shell, who):
    zoe = who == "zoe"
    env = profile_env(ZOE if zoe else None)
    clean(env)
    wow = ZOE_WOW if zoe else BATTLENET_WOW
    write_wtf(wow, FIRST)
    local, roaming = Path(env["LOCALAPPDATA"]) / "wow-stats", Path(env["APPDATA"]) / "wow-stats"
    script, conf = local / "wow_stats.py", roaming / "config.toml"

    def ship() -> None:
        """The task's next run: Task Scheduler's own for the runner's user;
        the registered command line in Zoë's environment for hers."""
        if not zoe:
            wait_idle(env)
            rc, _ = ps("powershell", "Start-ScheduledTask -TaskName wow-stats", env)
            assert rc == 0
            return
        t = task(env)
        r = subprocess.run(f'"{t["execute"]}" {t["arguments"]}', cwd=t["wd"], env=env, timeout=600)
        assert r.returncode == 0, local.joinpath("wow-stats.log").read_text(encoding="utf-8", errors="replace")

    h = friend_ready(lab)
    u = lab.t.registry.get(FRIEND)

    # Install: the line /start shows, pasted as is.
    line = install_line(lab, h)
    assert re.fullmatch(r"irm http://127\.0\.0\.1:\d+/install/\w+ \| iex", line), line
    if zoe:  # the help's way to name a WoW folder the search doesn't know
        line = f"$env:WOW_STATS_WOW_DIR = '{str(wow).replace(chr(39), chr(39) * 2)}'; " + line
    wtf = tree_digest(wow / "WTF")
    toc = wow / "Interface" / "AddOns" / "Polestar_Recorder" / "Polestar_Recorder.toc"
    rc, out = ps(shell, recorder_release() + line, env)
    assert rc == 0 and "wow-stats is installed." in out, out
    assert "WARNING" not in out, out
    checklist(out)
    assert toc.is_file() and "Version: 0.4.1.1" in toc.read_text(), out  # at the right depth
    assert tree_digest(wow / "WTF") == wtf  # the game's saved data, untouched  # e.g. the shipper's WoW folder garbled on its way back
    assert script.read_bytes() == account.shipper_file("wow_stats.py")
    password = config_password(conf)
    assert lab.t.registry.check_ingest(u.slug, password) is not None
    assert password not in out
    t = task(env)
    assert t is not None, "no scheduled task"
    assert Path(t["execute"]).name.lower() == "pythonw.exe" and str(script) in t["arguments"], t
    assert Path(t["execute"]).is_relative_to(Path(env["APPDATA"]) / "uv" / "python"), t  # uv's own Python
    assert sorted(t["triggers"]) == ["MSFT_TaskLogonTrigger", "MSFT_TaskTimeTrigger"], t

    # Ship: the first run sends what is on disk.
    if zoe:
        ship()  # (the runner's user: the installer started the task already)
    evs = wait_for(lab, u, lambda e: e.get("feed") == "wow-recorder", "recorder events", env)
    wait_for(lab, u, lambda e: e.get("char") == "Zoëlle-Beta", "events of the accented character", env)
    assert all(e.get("feed") != "wow-invalid" for e in stored(lab, u))
    assert not any(str(e.get("source_id", "")).startswith("rec:bbbb:2:") for e in evs)
    wait_idle(env)
    archive = Path(env["USERPROFILE"]) / "WoWArchive"
    assert not list((archive / "spool").glob("*.ndjson")) and list((archive / "sent").glob("*.gz"))
    if zoe:  # no Git: no history, the events go all the same
        assert not (archive / "wtf").exists()
        assert "git not found" in (local / "wow-stats.log").read_text(encoding="utf-8")
    else:
        assert (archive / "wtf" / ".git").is_dir()

    # Update: the code-free line; the program is replaced, the config kept.
    _, _, me = lab.get("/api/me", headers=h)
    update = json.loads(me)["update"]["windows"]
    assert update == f"irm {lab.base}/install/update | iex"
    before = conf.read_bytes()
    script.write_bytes(script.read_bytes() + b"\n# an old version\n")
    toc.write_text("## Version: 9.0.0\n")  # the friend's own, newer copy
    (toc.parent / "Mine.lua").write_text("-- mine\n")
    rc, out = ps(shell, recorder_release() + update, env)
    assert rc == 0 and "wow-stats is updated." in out, out
    assert conf.read_bytes() == before
    assert "Polestar Recorder 9.0.0" in out, out  # the update's checklist sees the friend's own copy
    assert toc.read_text() == "## Version: 9.0.0\n" and (toc.parent / "Mine.lua").is_file()
    assert script.read_bytes() == account.shipper_file("wow_stats.py")
    assert lab.t.registry.check_ingest(u.slug, password) is not None
    assert task(env) is not None
    wait_idle(env)
    write_wtf(wow, FIRST + [("bbbb", 2)])  # the friend played on: a new chunk
    ship()
    wait_for(lab, u, lambda e: str(e.get("source_id", "")).startswith("rec:bbbb:2:"), "the new chunk", env)

    # Reset: a new password, the install command again over the old install.
    line = install_line(lab, h, reset=True)
    if zoe:
        line = f"$env:WOW_STATS_WOW_DIR = '{str(wow).replace(chr(39), chr(39) * 2)}'; " + line
    assert lab.t.registry.check_ingest(u.slug, password) is None
    rc, out = ps(shell, recorder_release() + line, env)
    assert rc == 0 and "wow-stats is installed." in out, out
    new = config_password(conf)
    assert new != password and lab.t.registry.check_ingest(u.slug, new) is not None
    assert list(roaming.glob("config.toml.bak-*"))
    wait_idle(env)

    # The kit's own line (docs/install.md), run again over the install: it
    # keeps the config and finds the same WoW folder (no warning).
    stock = ROOT / "shipper" / "install-windows.ps1"
    rc, out = ps(shell, recorder_release() + f"powershell -ExecutionPolicy Bypass -File '{stock}'", env)
    assert rc == 0 and "registered and started" in out and "WARNING" not in out, out
    assert "Checking your setup:" in out and "Server: connected as mira" in out, out
    assert config_password(conf) == new
    wait_idle(env)

    # Uninstall: the help's line, pasted as is.
    off = account.steps()["uninstall"]["os"]["windows"]["command"]
    rc, out = ps(shell, off, env)
    assert rc == 0, out
    assert task(env) is None
    assert not local.exists() and not roaming.exists(), (list(local.rglob("*")), list(roaming.rglob("*")))
    assert archive.is_dir()  # the history stays, as the help says
    assert toc.is_file() and (toc.parent / "Mine.lua").is_file()  # the addons stay too
    assert tree_digest(wow / "WTF") != {} and not any(p.name.startswith(".wow-stats-recorder-")
                                                       for p in toc.parent.parent.iterdir())
    clean(env)


@ON_WINDOWS_CI
@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_a_bad_checksum_touches_no_addon(fresh_policy, recorder_release, shell):
    """The stock installer with a download that doesn't match its pin: the
    AddOns folder stays as it was, and the install goes on all the same."""
    env = profile_env(None)
    clean(env)
    wow = BATTLENET_WOW
    write_wtf(wow, FIRST)
    mine = wow / "Interface" / "AddOns" / "Polestar_Recorder"
    mine.mkdir(parents=True)
    (mine / "Polestar_Recorder.toc").write_text("## Version: 0.1\n")
    (mine / "Mine.lua").write_text("-- mine\n")
    before = tree_digest(wow)
    stock = ROOT / "shipper" / "install-windows.ps1"
    rc, out = ps(shell, recorder_release("0" * 64) + f"powershell -ExecutionPolicy Bypass -File '{stock}'", env)
    assert rc == 0 and "registered and started" in out, out
    # stderr is not a console here, so the refusal is in the log, as it is for a task run
    assert "doesn't match its checksum" in (Path(env["LOCALAPPDATA"]) / "wow-stats" / "wow-stats.log").read_text(
        encoding="utf-8"), out
    assert tree_digest(wow) == before
    assert sorted(p.name for p in mine.parent.iterdir()) == ["Polestar_Recorder"]
    assert task(env) is not None
    wait_idle(env)  # a running task holds its folder open
    clean(env)
