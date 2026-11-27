"""Own one recording session, including the terminal and browser process trees."""

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def processes():
    # POSIX ps works on macOS and Linux. Only request process metadata.
    result = subprocess.check_output(
        ["/bin/ps", "-A", "-o", "pid=,ppid=,pgid=,stat=,lstart="], text=True, timeout=3,
        env={"PATH": os.defpath, "LC_ALL": "C"},
    )
    rows = {}
    for line in result.splitlines():
        pid, parent, group, status, born = line.split(None, 4)
        if not status.startswith("Z"):
            rows[int(pid)] = (int(parent), int(group), born)
    return rows


def descendants(rows, roots):
    found = set(roots)
    while more := {pid for pid, (parent, _, _) in rows.items() if parent in found} - found:
        found.update(more)
    return found & rows.keys()


def send(pids, sig):
    for pid in pids:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, sig)


def stop(session, keep):
    # The worker has a private process group. ttyd's terminal and Chromium
    # can start new sessions. Freeze the parents before signalling anything
    # that could exit and leave its children parented to init.
    tracked = {}

    def collect():
        rows = processes()
        roots = {pid for pid, (_, group, _) in rows.items() if group == session}
        roots.update(pid for pid, born in tracked.items() if pid in rows and rows[pid][2] == born)
        return {pid: rows[pid][2] for pid in descendants(rows, roots) - descendants(rows, keep)}

    for _ in range(8):
        live = collect()
        if live == tracked:
            break
        send(live, signal.SIGSTOP)
        tracked.update(live)
    send(live, signal.SIGTERM)
    send(live, signal.SIGCONT)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        deadline = time.monotonic() + 1
        while live and time.monotonic() < deadline:
            time.sleep(0.05)
            live = collect()
            tracked.update(live)
            if sig == signal.SIGKILL:
                send(live, sig)
    if live:
        raise RuntimeError("recording processes still running; keeping the lab directory")


def main(script):
    cancelled = 0

    def cancel(signum, _frame):
        nonlocal cancelled
        cancelled = signum

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, cancel)
    processes()  # fail before starting the lab if process inspection is unavailable
    state = Path(tempfile.mkdtemp(prefix="wow.", dir="/tmp"))
    code = 1
    keep = set()
    try:
        worker = subprocess.Popen(
            ["/bin/bash", script, "--session", str(state)], start_new_session=True,
            stdin=subprocess.PIPE, env={**os.environ, "TMPDIR": str(state)},
        )
        try:
            status = state / "record.exit"
            while not cancelled and worker.poll() is None:
                if status.exists() and (result := status.read_text().strip()):
                    code = int(result)
                    break
                time.sleep(0.1)
            if cancelled:
                code = 128 + cancelled
            if code == 0 and os.environ.get("KEEP") == "1":
                keep = {int(pid) for pid in (state / "pids").read_text().splitlines()}
        finally:
            try:
                stop(worker.pid, keep)
            except BaseException:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(worker.pid, signal.SIGKILL)
                raise
            finally:
                worker.stdin.close()
                worker.wait(timeout=2)
    except BaseException:
        print(f"Recording stopped; inspect {state}", file=sys.stderr)
        raise
    if keep:
        print(f"KEEP=1: lab left running, state in {state}")
    else:
        shutil.rmtree(state)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
