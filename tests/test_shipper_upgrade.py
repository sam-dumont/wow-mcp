"""A friend's 0.6.1 shipper, the real file from the v0.6.1 tag, updates itself
to 0.7.0 from a server running this tree, with no reinstall: the owner's
worry when 0.7.0 moved the repository and added signed updates.

The 0.6.1 shipper asks the server it ships to (GET <base>/install/update/
shipper), never GitHub, and reads only `version` (X.Y.Z) and `files` (the
sha256 of each). Everything runs for real: the old file as a subprocess, under
a throwaway HOME whose install folder is where the installers put it, against
the app on 127.0.0.1 with a stand-in VictoriaLogs. The server's copy is this
tree's shipper as the release commit makes it (VERSION 0.7.0, signed), pinned
to the test key here instead of the project's, so the next update's signature
check runs too.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.test_server import FakeVL, fake_vl  # noqa: F401 - the fixture
from tests.test_shipper_stats_only import CHAR
from tests.test_shipper_update import PW, REAL, ROOT, TEST_KEY, signature, with_version
from wowstats import account, ui

OLD_TAG = "v0.6.1"


def at_tag(path: str) -> bytes:
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"{OLD_TAG}:{path}"], capture_output=True)
    if r.returncode:
        pytest.skip(f"no {OLD_TAG} tag in this checkout (git fetch --tags): {r.stderr.decode().strip()}")
    return r.stdout


def release(version: str, src: bytes = REAL) -> bytes:
    """This tree's shipper as a release commit makes it: its version, and
    (test only) the test key pinned instead of the project's. LF line ends,
    as the server's image has it (a Windows checkout has CRLF)."""
    src = src.replace(b"\r\n", b"\n")
    return re.sub(rb'(?m)^UPDATE_KEY = "[0-9a-f]{64}"$', f'UPDATE_KEY = "{TEST_KEY}"'.encode(),
                  with_version(src, version), count=1)


class Served:
    """What the app's /install/update/shipper serves: account.shipper_file
    swapped for this release's files, signed (or not) like release.yml."""

    def __init__(self, monkeypatch):
        self.mp, self.real = monkeypatch, account.shipper_file
        self.manifest = account.shipper_manifest.__wrapped__  # uncached: each set() serves anew

    def set(self, version: str, *, signed: bool | str = True, changed: dict[str, bytes] | None = None,
            extra: dict | None = None) -> dict[str, bytes]:
        files = {n: self.real(n) for n in account.SHIPPER_FILES}
        files["wow_stats.py"] = release(version)
        files.update(changed or {})
        meta = {n: {"sha256": hashlib.sha256(b).hexdigest(), "size": len(b)} for n, b in files.items()}
        sig = b"{}"
        if signed:
            sig = json.dumps(signature(signed if isinstance(signed, str) else version,
                                       meta if signed is True else {})).encode()
        self.mp.setattr(account, "shipper_file", lambda n: sig if n == account.SIGNATURE_FILE else files[n])
        manifest = self.manifest
        if extra:  # a later server's manifest, with fields a 0.6.1 shipper never heard of
            self.mp.setattr(account, "shipper_manifest", lambda: json.dumps({**json.loads(manifest()), **extra}).encode())
        else:
            self.mp.setattr(account, "shipper_manifest", manifest)
        return files


class Friend:
    """A friend's Mac (or Linux) as the 0.6.1 installer left it: the script and
    its agent files in ~/.local/share/wow-stats, the config, a WoW folder."""

    def __init__(self, root: Path, base: str, conf_extra: str = ""):
        self.home = root.resolve() / "home"
        self.inst = self.home / ".local" / "share" / "wow-stats"
        self.inst.mkdir(parents=True)
        self.old = at_tag("shipper/wow_stats.py")
        assert re.search(rb'(?m)^VERSION = "0\.6\.1"$', self.old)
        for n in ("install-mac.sh", "wow-stats.plist", "install-windows.ps1", "config.example.toml"):
            (self.inst / n).write_bytes(at_tag(f"shipper/{n}"))
        self.script.write_bytes(self.old)
        self.script.chmod(0o755)
        wow = root / "wow"
        sv = wow / "WTF" / "Account" / "DEMO" / "SavedVariables"
        sv.mkdir(parents=True)
        (sv / "Polestar.lua").write_text(CHAR)
        past = time.time() - 3600  # WoW wrote it an hour ago: no quiet wait
        for p in (sv / "Polestar.lua", sv, sv.parent, wow / "WTF"):
            os.utime(p, (past, past))
        self.arch, self.log = self.home / "archive", self.home / "wow-stats.log"
        self.conf = self.home / ".config" / "wow-stats" / "config.toml"
        self.conf.parent.mkdir(parents=True)
        self.conf.write_text(f"{conf_extra}wow_dir = '{wow}'\narchive_dir = '{self.arch}'\nwtf_archive = false\n"
                             f"log_file = '{self.log}'\n[ship]\nurl = '{base}/ingest'\nuser = 'wow'\n"
                             f"password = '{PW}'\n")
        # The installer a changed agent reruns: launchd left alone, the
        # Recorder's download pointed at a closed port (no GitHub).
        self.env = {**os.environ, "HOME": str(self.home), "WOW_STATS_CONFIG": str(self.conf),
                    "WOW_STATS_NO_LOAD": "1", "WOW_STATS_RECORDER_URL": "http://127.0.0.1:9/none.zip",
                    "WOW_STATS_RECORDER_SHA256": "0" * 64}

    @property
    def script(self) -> Path:
        return self.inst / "wow_stats.py"

    def run(self, *args: str) -> subprocess.CompletedProcess:
        """One run, as launchd starts it: the installed script, its own folder."""
        r = subprocess.run([sys.executable, str(self.script), *args], env=self.env, cwd=self.inst,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
        assert r.returncode == 0, (r.stdout, r.stderr, self.logged())
        return r

    def logged(self) -> str:
        return self.log.read_text() if self.log.exists() else ""

    def version(self) -> str:
        return re.search(r'(?m)^VERSION = "([^"]+)"$', self.script.read_text()).group(1)

    def state(self) -> dict:
        return json.loads((self.arch / "state.json").read_text())

    def due(self) -> None:
        """The next update check is due (the 2 hours since the last one passed)."""
        st = self.state()
        st["update"]["checked"] = 0
        (self.arch / "state.json").write_text(json.dumps(st))


def stored() -> list[dict]:
    return [json.loads(x) for _, _, body in FakeVL.got for x in body.splitlines() if x.strip()]


@pytest.fixture
def lab(run_app, fake_vl, monkeypatch, tmp_path):  # noqa: F811
    served = Served(monkeypatch)
    srv = run_app(ui.App({"BIND": "127.0.0.1", "VL_URL": fake_vl, "INGEST_PASSWORD": PW}))
    return served, srv, tmp_path


def test_a_061_shipper_updates_itself_to_070(lab):
    served, srv, tmp = lab
    # The installer changed since 0.6.1 (it copies foreverdb_send.py); this test is about the unchanged case.
    files = served.set("0.7.0", changed={"install-mac.sh": at_tag("shipper/install-mac.sh")},
                       extra={"channel": "stable", "notes": {"any": ["thing"]}})
    friend = Friend(tmp, srv.base)

    # 0.6.1's run: it ships, then takes 0.7.0 from the server (sha256 checked,
    # the old copy kept). Its agent files are 0.7.0's too: no installer rerun.
    friend.run()
    log = friend.logged()
    assert "auto-update: 0.6.1 -> 0.7.0, from the next run on" in log, log
    assert "set up again" not in log and not (friend.inst / ".update").exists()
    assert friend.script.read_bytes() == files["wow_stats.py"]
    assert (friend.inst / "wow_stats.py.prev").read_bytes() == friend.old
    assert any(e.get("char") == "Demo-Player" for e in stored()), "0.6.1 shipped nothing"

    # 0.7.0's first run, on 0.6.1's state and spool: it ships, reports its
    # version, and leaves the update check for 2 hours.
    FakeVL.got.clear()
    friend.run()
    setup = [e for e in stored() if e.get("feed") == "wow-setup"]
    assert setup and setup[-1]["shipper"] == "0.7.0", stored()
    assert friend.logged().count("auto-update") == 1
    dry = friend.run("--dry-run")
    assert "dry run:" in dry.stdout + dry.stderr + friend.logged()

    # The next update is checked against the key 0.7.0 pins: an unsigned
    # 0.7.1 (a server built between releases) is refused and said...
    served.set("0.7.1", signed=False)
    friend.due()
    friend.run()
    assert friend.version() == "0.7.0" and "isn't signed" in friend.state()["update"]["refused"]
    assert "auto-update: the server's update isn't signed" in friend.logged()
    # ...a signature over another release too...
    served.set("0.7.1", signed="0.7.0")
    friend.due()
    friend.run()
    assert friend.version() == "0.7.0" and friend.state()["update"]["refused"]
    # ...and the signed 0.7.1 goes in.
    files = served.set("0.7.1")
    friend.due()
    friend.run()
    assert friend.script.read_bytes() == files["wow_stats.py"] and "refused" not in friend.state()["update"]
    assert (friend.inst / "wow_stats.py.prev").read_bytes() == release("0.7.0")


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac's agent: launchd and plutil")
def test_a_changed_agent_reruns_the_new_installer(lab):
    """0.6.1 reruns the server's installer when its agent files changed: here
    the server's install-mac.sh differs from the copy 0.6.1 left."""
    served, srv, tmp = lab
    mac = account.shipper_file("install-mac.sh") + b"# a release that changed the installer\n"
    files = served.set("0.7.0", changed={"install-mac.sh": mac})
    friend = Friend(tmp, srv.base)
    friend.run()
    assert "the background program is set up again" in friend.logged()
    plist = friend.home / "Library" / "LaunchAgents" / "local.wow-stats.plist"
    for _ in range(120):  # detached, after 5 s: the installer from .update
        if plist.exists() and (friend.inst / "install-mac.sh").read_bytes() == mac:
            break
        time.sleep(0.5)
    assert plist.exists(), friend.logged()
    assert str(friend.script) in plist.read_text()
    assert friend.script.read_bytes() == files["wow_stats.py"]
    # The installer leaves its own copies next to the script: the next check
    # compares against them, so it doesn't run again.
    assert (friend.inst / "install-mac.sh").read_bytes() == mac


def test_a_release_candidate_server_is_no_update_for_061(lab):
    """0.6.1 reads only X.Y.Z: a 0.7.0-rc.1 server is "no version", logged
    once per check, and the shipping goes on. It takes the final."""
    served, srv, tmp = lab
    served.set("0.7.0-rc.1")
    friend = Friend(tmp, srv.base)
    friend.run()
    assert friend.script.read_bytes() == friend.old
    assert "auto-update: the server names no version; keeping 0.6.1" in friend.logged()
    assert any(e.get("char") == "Demo-Player" for e in stored())
    served.set("0.7.0")
    friend.due()
    friend.run()
    assert friend.version() == "0.7.0"


def test_auto_update_off_stays_put_and_start_says_how(lab):
    served, srv, tmp = lab
    served.set("0.7.0")
    friend = Friend(tmp, srv.base, conf_extra="auto_update = false\n")
    friend.run()
    assert friend.script.read_bytes() == friend.old and "auto-update" not in friend.logged()
    report = [e for e in stored() if e.get("feed") == "wow-setup"][-1]
    assert report["shipper"] == "0.6.1"
    # /start, from that report: once the server has served 0.7.0 long enough
    # for an auto-update, the update command.
    setup = {"shipper": report["shipper"], "at": report["_time"]}
    assert account.update_by_hand(setup, started=time.time()) is None  # still within the window
    assert account.update_by_hand(setup, started=time.time() - account.UPDATE_GRACE_S - 60) == \
        {"have": "0.6.1", "server": "0.7.0"}
    served.set("0.7.0-rc.1")  # a candidate: nobody is asked
    assert account.update_by_hand(setup, started=0) is None
    served.set("0.6.1")
    assert account.update_by_hand(setup, started=0) is None
