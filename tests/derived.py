"""The derived test fixtures (tools/fixtures/generate.py DERIVED: the
planner's inputs for the synthetic priest and hunter, the priest's gear
answer), built at test time and cached by what they are made from.

They are the product's own code run on the generator's events, so every
planner, gear or data change moved them: committed, they changed 20 to 44
times each from 2026-11-20 to 11-25 and conflicted on every PR. Now the
first test that needs them runs `generate.py --derived-out` (about 20 s on a
laptop) into a cache folder named by a hash of everything they are made from
(KEYED: the generator, wowstats/, data/, the committed test data, uv.lock,
and the Python version), and every later run with the same inputs reads it.

    load("plan-inputs.json.gz")   # the parsed JSON (tests: the `derived` fixture)

With pytest-xdist the first worker to ask builds and the others wait for it:
a lock directory (mkdir is atomic, as tests/test_eval_mcp.py's #258) names
its owner's pid, so a lock left by a run that was killed is taken over. The
cache lives in .cache/test-fixtures/ (git-ignored; WOW_FIXTURE_CACHE moves
it; CI keeps it between runs with actions/cache) and holds the newest KEEP
builds.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("plan-inputs.json.gz", "plan-inputs-level1.json.gz", "gear-priest.json.gz")
# What the derived fixtures are made from: a change to any byte of these is a new build.
KEYED = ("tools/fixtures", "wowstats", "data", "tests/data", "uv.lock")
# The longest a worker waits for another's build: pytest-timeout (CI's
# --timeout 300) stops it first; this bounds a local run without it.
WAIT_S = 900
KEEP = 4
# The build's environment: the machine's basics only, never what a test set
# (a monkeypatched WOW_ITEMS=off would build other fixtures than CI's).
ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "UV_CACHE_DIR")


def cache_root() -> Path:
    return Path(os.environ.get("WOW_FIXTURE_CACHE") or ROOT / ".cache" / "test-fixtures")


def key() -> str:
    """A hash of every file in KEYED (path and content) and the Python version."""
    h = hashlib.sha256(f"python {sys.version_info[0]}.{sys.version_info[1]}\n".encode())
    files = []
    for rel in KEYED:
        p = ROOT / rel
        if p.is_file():
            files.append(p)
            continue
        for dirpath, dirnames, filenames in os.walk(p):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            files += [Path(dirpath) / f for f in filenames if not f.endswith(".pyc") and f != ".DS_Store"]
    for f in sorted(files):
        rel = f.relative_to(ROOT).as_posix()
        if rel.startswith("tests/data/") and f.name in NAMES:
            continue  # a stale copy left from before they were built here
        h.update(rel.encode() + b"\0")
        h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()[:24]


def _alive(owner: str) -> bool:
    """Whether the lock's owner ("host pid") still runs; another host's: assumed so."""
    host, _, pid = owner.partition(" ")
    if host != socket.gethostname() or not pid.isdigit():
        return True
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _build(root: Path, k: str) -> None:
    tmp = root / f"{k}.tmp-{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "fixtures" / "generate.py"), "--derived-out", str(tmp)],
                       cwd=ROOT, capture_output=True, text=True,
                       env={k: v for k, v in os.environ.items() if k in ENV_KEEP})
    if r.returncode or not all((tmp / n).is_file() for n in NAMES):
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError("tools/fixtures/generate.py --derived-out failed:\n" + (r.stdout + r.stderr)[-4000:])
    tmp.rename(root / k)  # atomic: the folder exists whole or not at all
    builds = sorted((d for d in root.iterdir() if d.is_dir() and "." not in d.name), key=lambda d: d.stat().st_mtime)
    for old in builds[:-KEEP]:
        shutil.rmtree(old, ignore_errors=True)


def ensure() -> Path:
    """The folder holding NAMES for this tree's inputs, built when missing."""
    root = cache_root()
    root.mkdir(parents=True, exist_ok=True)
    k = key()
    done, lock, err = root / k, root / f"{k}.lock", root / f"{k}.error"
    deadline, start = time.monotonic() + WAIT_S, time.time()
    while not done.is_dir():
        try:
            lock.mkdir()
        except FileExistsError:
            owner = (lock / "owner").read_text() if (lock / "owner").exists() else ""
            if owner and not _alive(owner):
                shutil.rmtree(lock, ignore_errors=True)  # its builder died: take it over
                continue
            # This run's builder failed (an older run's error is not ours: its next builder retries).
            if err.exists() and err.stat().st_mtime >= start - 1 and not done.is_dir():
                raise RuntimeError(err.read_text()) from None
            if time.monotonic() > deadline:
                raise RuntimeError(f"no derived fixtures after {WAIT_S} s from the worker building them ({lock})") from None
            time.sleep(0.5)
            continue
        try:
            (lock / "owner").write_text(f"{socket.gethostname()} {os.getpid()}")
            err.unlink(missing_ok=True)
            if not done.is_dir():
                _build(root, k)
        except BaseException as e:  # pytest-timeout's stop too: the others must not wait for nothing
            err.write_text(f"{type(e).__name__}: {e}"[:6000])
            raise
        finally:
            shutil.rmtree(lock, ignore_errors=True)
    return done


def load(name: str) -> dict:
    """One derived fixture, parsed (`name`: one of NAMES)."""
    assert name in NAMES, f"{name} isn't a derived fixture: {NAMES}"
    return json.loads(gzip.decompress((ensure() / name).read_bytes()))
