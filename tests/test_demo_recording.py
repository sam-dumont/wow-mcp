"""Recorder lifecycle with fake VHS, installer, wizard and lab. No browser or game."""

import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parents[1] / "demo"
pytestmark = pytest.mark.skipif(os.name != "posix", reason="the Mac recording scripts use bash and POSIX signals")


def executable(path, body):
    path.write_text(body)
    path.chmod(0o755)


@pytest.fixture
def demo(tmp_path):
    root = tmp_path / "demo"
    root.mkdir()
    for name in ("lab", "record.sh"):
        shutil.copy(DEMO / name, root)
    shutil.copytree(DEMO / "stack", root / "stack")
    shutil.copytree(DEMO / "tapes", root / "tapes")
    (root / "browser").mkdir()
    (root / "stack/up.sh").write_text('source "$here/stack/env.sh"\nsleep 30 &\necho $! > "$state/pids"\n')
    fake = tmp_path / "bin"
    fake.mkdir()
    python = shlex.quote(sys.executable)
    env = {**os.environ, "PATH": f"{fake}:{os.defpath}", "HOME": str(tmp_path / "operator"),
           "KEEP": "0", "MODES": "dark", "LAB_STATE": str(tmp_path / "lab")}
    for var in ("WOW_STATS_CONFIG", "WOW_STATS_WOW_DIR", "GIT_DIR", "XDG_DATA_HOME", "UV_CACHE_DIR"):
        env[var] = str(tmp_path / "sentinel")
    (tmp_path / "sentinel").write_text("operator data")
    executable(fake / "launchctl", "#!/bin/sh\nexit 92\n")
    # Only allow the exact uv invocations used before the first tape and by lab friend.
    executable(fake / "uv", f"""#!/bin/bash
set -eu
case "$*" in
  "python find >=3.11") echo {python};;
  "run --frozen python demo/stack/fake_wow.py "*) mkdir -p "$5/WTF";;
  "run --frozen python wizard.py")
    echo http://fixture/install > "$DEMO_STATE/install-url"
    exec {python} {shlex.quote(str(tmp_path / "waiter.py"))} wizard;;
  "run --no-project python "*"seed_friend.py "*) test -s "${{@: -2:1}}";;
  "run --no-project python -"*) shift 3; exec {python} "$@";;
  *) exit 91;;
esac
""")
    # This stand-in would overwrite the sentinel if WOW_STATS_CONFIG leaked.
    installer = """set -eu
test "$WOW_STATS_CONFIG" = "$HOME/.config/wow-stats/config.toml"
test -d "$WOW_STATS_WOW_DIR/WTF"
test "$NO_REAL_WOW" = 1
test -z "${GIT_DIR-}${XDG_DATA_HOME-}${UV_CACHE_DIR-}"
test "$(command -v launchctl)" = "${DEMO_FRIEND_SHELL%/*}/shims/launchctl"
mkdir -p "$(dirname "$WOW_STATS_CONFIG")"
echo fixture > "$WOW_STATS_CONFIG"
"""
    (tmp_path / "installer").write_text(installer)
    executable(fake / "curl", f"""#!/bin/bash
if [[ $* == *install-code ]]; then echo '{{"mac": "curl -fsSL http://fixture/install | bash"}}'
else cat {shlex.quote(str(tmp_path / "installer"))}; fi
""")
    (tmp_path / "waiter.py").write_text("""import os, signal, subprocess, sys, time
from pathlib import Path
base = Path(__file__).parent
name = sys.argv[1]
signal.alarm(30)
if name.endswith("-child"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
else:
    subprocess.Popen([sys.executable, __file__, name + "-child"], start_new_session=True)
(base / name).write_text(str(os.getpid()))
time.sleep(30)
""")
    return root, fake, env


def test_friend_installs_under_fake_home(demo, tmp_path):
    root, _, env = demo
    subprocess.run([root / "lab", "friend", "dark"], env=env, check=True, timeout=10)
    assert (tmp_path / "lab/home-dark/.config/wow-stats/config.toml").read_text() == "fixture\n"
    assert (tmp_path / "sentinel").read_text() == "operator data"


def test_friend_rejects_a_path_as_mode(demo, tmp_path):
    root, _, env = demo
    result = subprocess.run([root / "lab", "friend", "dark/../../escape"], env=env, timeout=5)
    assert result.returncode != 0
    assert not (tmp_path / "lab").exists()


@pytest.mark.parametrize("cancel", [True, False], ids=["term", "failed-tape"])
def test_record_reaps_terminal_and_wizard(demo, tmp_path, cancel):
    root, fake, env = demo
    executable(fake / "vhs", f"""#!/bin/bash
set -eu
# Simulate a terminal startup file moving a different launchctl ahead of our shim.
export PATH={shlex.quote(str(fake))}:"$PATH"
source "$DEMO_FRIEND_SHELL" || exit 1
source {shlex.quote(str(tmp_path / "installer"))}
echo "$TMPDIR" > {shlex.quote(str(tmp_path / "state"))}
{"exec " + shlex.quote(sys.executable) + " " + shlex.quote(str(tmp_path / "waiter.py")) + " terminal" if cancel else "exit 42"}
""")
    with subprocess.Popen([root / "record.sh"], env=env) as recorder:
        marks = ["wizard", "wizard-child"]
        if cancel:
            marks += ["terminal", "terminal-child"]
        try:
            if cancel:
                deadline = time.monotonic() + 5
                while not all((tmp_path / mark).exists() for mark in marks):
                    assert recorder.poll() is None and time.monotonic() < deadline
                    time.sleep(0.05)
                recorder.terminate()
            assert recorder.wait(timeout=8) == (143 if cancel else 42)
            assert not Path((tmp_path / "state").read_text().strip()).exists()
            assert (tmp_path / "sentinel").read_text() == "operator data"
            for mark in marks:
                # A container's PID 1 may defer reaping a dead grandchild.
                pid = (tmp_path / mark).read_text()
                proc = subprocess.run(["/bin/ps", "-p", pid, "-o", "stat="], capture_output=True, text=True)
                assert proc.stdout.strip().startswith("Z") or (proc.returncode == 1 and not proc.stdout.strip())
        finally:
            if recorder.poll() is None:
                recorder.terminate()
                recorder.wait(timeout=8)
