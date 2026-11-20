"""A/B regression check: one person's real data, frozen once, shown to two
versions of wow-stats, and every answer compared.

    ab.py record  --vl URL --vm URL (--image IMG | --ref REF) [--char NAME] [--state DIR] [--env K=V]...
    ab.py extend  RECORDING --vl URL --vm URL (--image IMG | --ref REF) --label NAME
    ab.py replay  RECORDING (--image IMG | --ref REF | --here) --label NAME
    ab.py diff    RUN_A RUN_B [--label-a A --label-b B]
    ab.py info    [RECORDING]        dates, and the day each recording must be deleted
    ab.py purge                      deletes recordings past 30 days, and their runs

record runs a version against the real stores through a recording proxy
(tools/ab/proxy.py): every store request it makes is kept with its answer.
Only that version's own queries, read-only, for the default VictoriaLogs
tenant (AccountID 0) alone. replay runs any version against the recording
only: the same answers, the same frozen clock (the recording's instant,
tools/ab/abclock.py), so two versions see exactly the same inputs. diff
writes a Markdown report, a section per feature, regressions first.

A version is an image (`--image`, run with Docker, capped at 4 CPUs and 6
GB; for a multi-platform image, name the platform's own manifest digest),
a git ref of this repository (`--ref`, exported and installed with uv in the
work folder), or this checkout as it is (`--here`).

extend adds a newer version's own store requests to a recording, at the
recording's frozen instant, within 24 hours of it: what the newer version
asks and the old one never did is then answered too, instead of showing as a
recording miss. The driver (tools/ab/driver.py) and its cases always
come from this checkout, so an old version is asked the same questions as a
new one.

Where things go: WOW_AB_HOME, else a `wow-ab` folder next to this checkout.
Never inside a git work tree (refused): recordings hold the person's game
data and Battle.net data, which Blizzard's terms keep 30 days at most. Each
recording carries its date; replay refuses one older than 30 days, and
`purge` deletes them.

--state DIR: a copy of the full view's ANALYSIS_STATE_DIR (kept plans, the
play analysis), kept with the recording; each run gets its own copy, so a
tool that writes changes nothing but that run.

Standard library only (Python 3.11+); Docker for --image, git and uv for --ref.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import diff as diffmod  # noqa: E402
from proxy import Cassette, Proxy  # noqa: E402

MAX_AGE = timedelta(days=30)
EXTEND_WITHIN = timedelta(hours=24)
CASSETTE = "cassette.jsonl.gz"
# Settings a recording keeps and every replay of it reuses: what the app is
# told about the stores, never a secret or an address.
KEPT_ENV = ("LOGS_FILTER", "DEFAULT_CHAR", "VL_ARCHIVE_AFTER_DAYS", "VL_CONCURRENCY", "WOW_TIMEZONE", "PRICE_DAYS",
            "WEIGHTS", "MODEL3D_MODELS")
DOCKER_LIMITS = ("--cpus", "4", "--memory", "6g")


class AbError(Exception):
    pass


def log(msg: str) -> None:
    print(f"ab: {msg}", file=sys.stderr, flush=True)


# ------------------------------------------------------------------ places


def in_git_tree(p: Path) -> bool:
    p = p.resolve()
    return any((d / ".git").exists() for d in (p, *p.parents))


def home(arg: str | None = None) -> Path:
    h = Path(arg or os.environ.get("WOW_AB_HOME") or (REPO.parent / "wow-ab")).expanduser().resolve()
    if in_git_tree(h):
        raise AbError(f"{h} is inside a git work tree: recordings and outputs hold the person's data and must "
                      f"never be committed. Set WOW_AB_HOME (or --home) to a folder outside any repository.")
    for sub in ("recordings", "runs", "reports", "cache"):
        (h / sub).mkdir(parents=True, exist_ok=True)
    h.chmod(0o700)
    return h


def now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def stamp(t: datetime) -> str:
    return t.strftime("%Y%m%dT%H%M%SZ")


def recording_dir(h: Path, name: str) -> Path:
    p = Path(name)
    if not p.is_absolute():
        p = h / "recordings" / name
    if not (p / "meta.json").exists():
        raise AbError(f"no recording at {p}")
    return p


def meta_of(rec: Path) -> dict:
    return json.loads((rec / "meta.json").read_text(encoding="utf-8"))


def check_age(meta: dict, now: datetime | None = None) -> None:
    rec_at = datetime.fromisoformat(meta["recorded_at"].replace("Z", "+00:00"))
    if (now or now_utc()) - rec_at > MAX_AGE:
        raise AbError(f"the recording of {meta['recorded_at']} is older than 30 days: it holds Battle.net data, "
                      f"which must be gone by {meta['delete_by']}. Delete it (`ab.py purge`) and record again.")


# ------------------------------------------------------------------ targets


def _image_platform(image: str) -> str | None:
    p = subprocess.run(["docker", "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image],
                       capture_output=True, text=True, check=False)
    if p.returncode != 0:
        subprocess.run(["docker", "pull", image], check=True, stdout=subprocess.DEVNULL)
        p = subprocess.run(["docker", "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image],
                           capture_output=True, text=True, check=True)
    return p.stdout.strip() or None


def image_id(image: str) -> str:
    _image_platform(image)
    p = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", image], capture_output=True, text=True,
                       check=True)
    return p.stdout.strip()


def ref_source(h: Path, ref: str) -> tuple[Path, str]:
    """The ref's tree, exported and installed once per commit."""
    sha = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--verify", f"{ref}^{{commit}}"], capture_output=True,
                         text=True, check=True).stdout.strip()
    src = h / "cache" / f"src-{sha[:12]}"
    if not (src / ".ab-ready").exists():
        shutil.rmtree(src, ignore_errors=True)
        src.mkdir(parents=True)
        arch = subprocess.run(["git", "-C", str(REPO), "archive", sha], capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(src)], input=arch, check=True)
        subprocess.run(["uv", "sync", "--frozen", "--no-dev", "--extra", "publish", "--extra", "mcp"], cwd=src,
                       check=True)
        (src / ".ab-ready").write_text(sha + "\n")
    return src, sha


def run_target(h: Path, *, image: str | None, ref: str | None, out: Path, env: dict, char: str,
               ports: dict[str, int], skip: str = "") -> int:
    """The driver inside the version under test, pointed at the proxy."""
    state_copy = Path(env.pop("_STATE_COPY"))
    args = ["--out", "/out" if image else str(out), "--char", char] + (["--skip", skip] if skip else [])
    if image:
        plat = _image_platform(image)
        linux = platform.system() == "Linux"
        host = "127.0.0.1" if linux else "host.docker.internal"
        e = {**env, "VL_URL": f"http://{host}:{ports['vl']}", "VL_ARCHIVE_URL": f"http://{host}:{ports['vl']}",
             "VM_URL": f"http://{host}:{ports['vm']}", "ANALYSIS_STATE_DIR": "/state", "HOME": "/tmp"}
        cmd = ["docker", "run", "--rm", *DOCKER_LIMITS, "--user", f"{os.getuid()}:{os.getgid()}",
               "-v", f"{HERE}:/ab:ro", "-v", f"{out}:/out", "-v", f"{state_copy}:/state",
               "--entrypoint", "python3"]
        cmd += ["--network", "host"] if linux else []
        cmd += ["--platform", plat] if plat else []
        envfile = out.parent / f".{out.name}.env"
        envfile.write_text("".join(f"{k}={v}\n" for k, v in e.items()))
        envfile.chmod(0o600)
        try:
            return subprocess.run([*cmd, "--env-file", str(envfile), image, "/ab/driver.py", *args],
                                  check=False).returncode
        finally:
            envfile.unlink(missing_ok=True)
    if ref:
        src, _sha = ref_source(h, ref)
        py = src / ".venv" / "bin" / "python"
    else:  # --here: this checkout, with the interpreter running ab.py
        src, py = REPO, Path(sys.executable)
    e = {**os.environ, **env, "VL_URL": f"http://127.0.0.1:{ports['vl']}",
         "VL_ARCHIVE_URL": f"http://127.0.0.1:{ports['vl']}", "VM_URL": f"http://127.0.0.1:{ports['vm']}",
         "ANALYSIS_STATE_DIR": str(state_copy)}
    for k in ("VIRTUAL_ENV", "PYTHONPATH"):
        e.pop(k, None)
    return subprocess.run([str(py), str(HERE / "driver.py"), *args], env=e, cwd=src, check=False).returncode


def base_env(meta: dict, frozen_at: str) -> dict:
    return {**{k: v for k, v in (meta.get("env") or {}).items() if k in KEPT_ENV}, "AB_FROZEN_AT": frozen_at,
            "AB_SEED": "0", "TZ": "UTC", "PYTHONHASHSEED": "0", "MODEL3D": "off", "FRIENDS_VL_URL": "",
            "WOW_STATE_DIR": "", "WOW_FRIENDS_FILE": "", "FASTMCP_CHECK_FOR_UPDATES": "off"}


def _session(h: Path, rec: Path, meta: dict, proxy: Proxy, *, image, ref, label: str, skip: str = "") -> Path:
    out = h / "runs" / label
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    work = Path(tempfile.mkdtemp(prefix="ab-state-", dir=h / "cache"))
    try:
        state = work / "state"
        if (rec / "state").exists():
            shutil.copytree(rec / "state", state)
        else:
            state.mkdir()
        # The copy holds the person's real data: owner only. The image runs
        # as this user (`docker run --user`, run_target), so that's enough for it
        # to write.
        os.chmod(state, 0o700)
        for d in state.rglob("*"):
            os.chmod(d, 0o700 if d.is_dir() else 0o600)
        ports = {}
        for store in ("vl", "vm"):
            url = proxy.start(store)
            ports[store] = int(url.rsplit(":", 1)[1])
        env = {**base_env(meta, meta["frozen_at"]), "_STATE_COPY": str(state)}
        what = f"image {image}" if image else f"ref {ref}" if ref else "this checkout"
        log(f"run {label}: {what}, clock {meta['frozen_at']}")
        rc = run_target(h, image=image, ref=ref, out=out, env=env, char=meta.get("char") or "", ports=ports,
                        skip=skip)
    finally:
        proxy.stop()
        shutil.rmtree(work, ignore_errors=True)
    rep = proxy.report()
    rep["driver_exit"] = rc
    (out / "_proxy.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    target = {"image": image, "image_id": image_id(image) if image else None,
              "ref": ref or ("(here)" if not image else None)}
    (out / "_target.json").write_text(json.dumps({**target, "recording": rec.name}, indent=1) + "\n")
    log(f"run {label}: driver exit {rc}; store requests {proxy.stats}")
    return out


# ------------------------------------------------------------------ commands


def cmd_record(a) -> int:
    h = home(a.home)
    t0 = now_utc()
    name = a.name or stamp(t0)
    rec = h / "recordings" / name
    if rec.exists():
        raise AbError(f"{rec} exists")
    rec.mkdir(parents=True)
    env = dict(kv.split("=", 1) for kv in a.env)
    unknown = sorted(set(env) - set(KEPT_ENV))
    if unknown:
        raise AbError(f"--env takes only {', '.join(KEPT_ENV)} (not {', '.join(unknown)})")
    meta = {"recorded_at": t0.isoformat().replace("+00:00", "Z"), "frozen_at": t0.isoformat().replace("+00:00", "Z"),
            "delete_by": (t0 + MAX_AGE).date().isoformat(), "char": a.char or env.get("DEFAULT_CHAR", ""),
            "env": env, "target": {"image": a.image, "ref": a.ref}, "tenant": "AccountID 0",
            "note": "Holds game and Battle.net data: never commit, delete by delete_by."}
    if a.state:
        shutil.copytree(a.state, rec / "state")
    (rec / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    cas = Cassette()
    proxy = Proxy("record", cas, {"vl": a.vl, "vm": a.vm})
    try:
        _session(h, rec, meta, proxy, image=a.image, ref=a.ref, label=a.label or f"record-{name}")
    finally:
        cas.save(rec / CASSETTE)
    meta["requests"] = len(cas.entries)
    meta["bytes"] = (rec / CASSETTE).stat().st_size
    (rec / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    log(f"recording {rec}: {len(cas.entries)} requests, {meta['bytes']} bytes gzip; delete by {meta['delete_by']}")
    print(rec)
    return 0


def cmd_extend(a) -> int:
    """Add a newer version's own store requests to a recording, at the
    recording's frozen instant, while the data is still that instant's."""
    h = home(a.home)
    rec = recording_dir(h, a.recording)
    meta = meta_of(rec)
    check_age(meta)
    rec_at = datetime.fromisoformat(meta["recorded_at"].replace("Z", "+00:00"))
    if now_utc() - rec_at > EXTEND_WITHIN:
        raise AbError(f"the recording is from {meta['recorded_at']}: past {EXTEND_WITHIN}, the stores no longer "
                      f"hold that instant's data (new events, Battle.net's retention). Record again instead.")
    cas = Cassette.load(rec / CASSETTE)
    before = len(cas.entries)
    tag = a.image or a.ref or "here"
    proxy = Proxy("extend", cas, {"vl": a.vl, "vm": a.vm}, tag=tag)
    try:
        _session(h, rec, meta, proxy, image=a.image, ref=a.ref, label=a.label)
    finally:
        cas.save(rec / CASSETTE)
    meta.setdefault("extended", []).append({"by": tag, "at": now_utc().isoformat().replace("+00:00", "Z"),
                                            "added": len(cas.entries) - before})
    meta["requests"] = len(cas.entries)
    meta["bytes"] = (rec / CASSETTE).stat().st_size
    (rec / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    log(f"recording {rec.name}: {len(cas.entries) - before} requests added by {tag}")
    return 0


def cmd_replay(a) -> int:
    h = home(a.home)
    rec = recording_dir(h, a.recording)
    meta = meta_of(rec)
    check_age(meta)
    proxy = Proxy("replay", Cassette.load(rec / CASSETTE))
    out = _session(h, rec, meta, proxy, image=a.image, ref=a.ref, label=a.label, skip=a.skip)
    print(out)
    return 0


def cmd_diff(a) -> int:
    h = home(a.home)
    da, db = (Path(x) if Path(x).is_absolute() else h / "runs" / x for x in (a.run_a, a.run_b))
    res = diffmod.diff_runs(da, db)
    la, lb = a.label_a or da.name, a.label_b or db.name
    header = []
    t = da / "_target.json"
    if t.exists():
        rec_name = json.loads(t.read_text()).get("recording")
        p = h / "recordings" / str(rec_name) / "meta.json"
        if rec_name and p.exists():
            m = json.loads(p.read_text())
            header = [f"- Recording {rec_name}: taken {m['recorded_at']}, {m.get('requests', '?')} store requests, "
                      f"delete by {m['delete_by']}"]
    text = diffmod.report(res, la, lb, header)
    out = h / "reports" / f"{da.name}--{db.name}.md"
    out.write_text(text + "\n", encoding="utf-8")
    (out.with_suffix(".json")).write_text(json.dumps({k: v for k, v in res.items() if k != "meta"},
                                                     ensure_ascii=False, indent=1, default=str) + "\n")
    log(f"{len(res['files'])} outputs changed, {len(res['regressions'])} regressions: {out}")
    print(out)
    return 0 if not res["files"] and not res["regressions"] else 3


def cmd_info(a) -> int:
    h = home(a.home)
    recs = [recording_dir(h, a.recording)] if a.recording else sorted((h / "recordings").iterdir())
    for r in recs:
        if (r / "meta.json").exists():
            m = meta_of(r)
            age = now_utc() - datetime.fromisoformat(m["recorded_at"].replace("Z", "+00:00"))
            print(f"{r.name}: recorded {m['recorded_at']}, {m.get('requests', '?')} requests, "
                  f"{m.get('bytes', '?')} bytes, delete by {m['delete_by']}"
                  + (" (EXPIRED: purge it)" if age > MAX_AGE else ""))
    return 0


def cmd_purge(a) -> int:
    h = home(a.home)
    gone = []
    for r in sorted((h / "recordings").iterdir()):
        if not (r / "meta.json").exists():
            continue
        try:
            check_age(meta_of(r))
        except AbError:
            gone.append(r.name)
            shutil.rmtree(r)
    for run in sorted((h / "runs").iterdir()):
        t = run / "_target.json"
        if t.exists() and json.loads(t.read_text()).get("recording") in gone:
            shutil.rmtree(run)
    log(f"purged {len(gone)} recording(s): {', '.join(gone) or 'none past 30 days'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--home", help="where recordings, runs and reports go (WOW_AB_HOME)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def target(p):
        g = p.add_mutually_exclusive_group(required=True)
        g.add_argument("--image", help="a wow-stats image (name:tag@digest)")
        g.add_argument("--ref", help="a git ref of this repository")
        g.add_argument("--here", action="store_true", help="this checkout, with the Python running ab.py")

    r = sub.add_parser("record", help="run a version against the real stores and keep what it asked")
    r.add_argument("--vl", required=True, help="VictoriaLogs base URL (the events)")
    r.add_argument("--vm", required=True, help="VictoriaMetrics base URL (the Battle.net gauges)")
    r.add_argument("--char", default="", help="the character under test (default: DEFAULT_CHAR, else the first)")
    r.add_argument("--state", help="a copy of ANALYSIS_STATE_DIR to keep with the recording")
    r.add_argument("--env", action="append", default=[], help=f"K=V, one of {', '.join(KEPT_ENV)}")
    r.add_argument("--name", help="the recording's name (default: its UTC time)")
    r.add_argument("--label", help="the run's name (default: record-<name>)")
    target(r)
    x = sub.add_parser("extend", help="add a newer version's own store requests to a recording (within 24 h)")
    x.add_argument("recording")
    x.add_argument("--vl", required=True)
    x.add_argument("--vm", required=True)
    x.add_argument("--label", required=True)
    target(x)
    p = sub.add_parser("replay", help="run a version against a recording")
    p.add_argument("recording")
    p.add_argument("--label", required=True)
    p.add_argument("--skip", default="", help="driver parts to skip: web, compute, publish, mcp")
    target(p)
    d = sub.add_parser("diff", help="compare two runs")
    d.add_argument("run_a")
    d.add_argument("run_b")
    d.add_argument("--label-a")
    d.add_argument("--label-b")
    i = sub.add_parser("info", help="recordings and the day each must be deleted")
    i.add_argument("recording", nargs="?")
    sub.add_parser("purge", help="delete recordings past 30 days and their runs")
    a = ap.parse_args(argv)
    try:
        return {"record": cmd_record, "extend": cmd_extend, "replay": cmd_replay, "diff": cmd_diff, "info": cmd_info,
                "purge": cmd_purge}[a.cmd](a)
    except AbError as e:
        log(str(e))
        return 2


if __name__ == "__main__":
    sys.exit(main())
