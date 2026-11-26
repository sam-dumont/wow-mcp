"""Load the nine invented characters from the docs demo, marked demo=true.

    docker compose run --rm demo         # base kit, through /ingest
    uv run dev/seed.py --ingest URL      # INGEST_USER and INGEST_PASSWORD
    uv run dev/seed.py [--vl URL]        # direct to VictoriaLogs
    uv run dev/seed.py --out FILE        # NDJSON only
    ... [--metrics --vm URL]             # optional simulated armory samples
    ... [--fixture-dir DIR]              # game files and offline talent grid
    ... [--end-day YYYY-MM-DD]           # Nibbet's last evening, default yesterday

docs/self-host/demo.md runs the models and metrics in a separate Compose project.
Through /ingest the page lists the roster seconds later; direct VictoriaLogs
writes wait for the UI's next check and need a long enough retention period.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "fixtures"))

import generate  # noqa: E402

from wowstats import ingest  # noqa: E402


def demo_rows(end_day: str | None = None) -> list[dict]:
    """The roster's events, ending `end_day` (default yesterday, UTC), each
    marked demo=true."""
    end = end_day or (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
    return [{**r, "demo": True} for r in generate.demo_roster(end)]


def post(url: str, body: bytes, headers: dict) -> None:
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 - the store or ui the caller named
        r.read()


def to_ingest(url: str, rows: list[dict]) -> None:
    pw = os.environ.get("INGEST_PASSWORD", "")
    if not pw:
        sys.exit("--ingest: set INGEST_PASSWORD (the ui's upload password)")
    auth = "Basic " + base64.b64encode(f"{os.environ.get('INGEST_USER') or 'wow'}:{pw}".encode()).decode()
    # By address: the ui answers only for loopback names, IP addresses and
    # its own (wowstats.guard, Host), and the kit reaches it as `ui`.
    u = urllib.parse.urlsplit(url)
    if u.hostname and u.hostname != "localhost":
        ip = socket.gethostbyname(u.hostname)
        url = u._replace(netloc=f"{ip}:{u.port}" if u.port else ip).geturl()
    lines = generate.jsonl(rows).splitlines()
    # /ingest takes 8 MiB a post: 2,000 events are well under it.
    for i in range(0, len(lines), 2000):
        post(url, b"\n".join(lines[i:i + 2000]) + b"\n",
             {"Content-Type": "application/x-ndjson", "Authorization": auth})


def to_vl(vl: str, rows: list[dict]) -> None:
    # Screened as /ingest would (component=wow, the stream fields).
    lines, bad = ingest.parse(generate.jsonl(rows))
    if bad:
        sys.exit(f"{bad} events the ingest screen refuses")
    q = urllib.parse.urlencode({"_stream_fields": ingest.STREAM_FIELDS, "_time_field": "_time", "_msg_field": "_msg"})
    for i in range(0, len(lines), 5000):
        # Without this content type VictoriaLogs answers 200 and stores nothing.
        post(f"{vl}/insert/jsonline?{q}", b"\n".join(lines[i:i + 5000]) + b"\n",
             {"Content-Type": "application/stream+json"})
    post(f"{vl}/internal/force_flush", b"", {})


def to_vm(vm: str, rows: list[dict], looks: dict[str, list[dict]] | None) -> int:
    series = generate.demo_series(rows, datetime.now(UTC).timestamp(), looks)
    post(f"{vm}/api/v1/import", "".join(json.dumps(s, separators=(",", ":")) + "\n" for s in series).encode(), {})
    return sum(len(s["values"]) for s in series)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Load the nine demo characters (DEMO DATA).")
    ap.add_argument("--ingest", metavar="URL", help="a running ui's /ingest")
    ap.add_argument("--vl", metavar="URL", default="http://127.0.0.1:9428", help="VictoriaLogs, without --ingest")
    ap.add_argument("--out", metavar="FILE", help="write the events (NDJSON), send nothing")
    ap.add_argument("--fixture-dir", metavar="DIR", help="prepare models and an offline talent grid in a demo volume")
    ap.add_argument("--metrics", action="store_true", help="also the Battle.net series into VictoriaMetrics")
    ap.add_argument("--vm", metavar="URL", default="http://127.0.0.1:8428", help="VictoriaMetrics, with --metrics")
    ap.add_argument("--end-day", metavar="YYYY-MM-DD", help="his last evening (default: yesterday, UTC)")
    a = ap.parse_args(argv)
    looks = None
    if a.fixture_dir:
        import demo3d
        import roster
        import roster_asset

        from wowstats import talents

        root = Path(a.fixture_dir)
        root.mkdir(parents=True, exist_ok=True)
        source = os.environ.get("DEMO_3D_STORE")
        store = Path(source).resolve() if source else roster_asset.fetch(root / "roster-archives")
        looks = demo3d.read_looks(store)
        # All three services see an optional host bind at /demo-store. Fetched
        # artwork lives in ui-state, which publish mounts under a different path.
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            ref = Path(tmp) / "store"
            ref.symlink_to(store if source else store.relative_to(root), target_is_directory=True)
            ref.replace(root / "client-assets")
        # If the service already has a newer catalog, keep it. At the first
        # daily refresh it can fetch the full grid and icons as usual.
        path = root / "talent-catalog.json"
        if not path.exists():
            catalog = {"format": talents.FORMAT, "build": "demo-bundled", "catalog": roster.bundled_talent_catalog(),
                       "checked": time.time()}
            with tempfile.NamedTemporaryFile(mode="w", dir=root, delete=False) as f:
                json.dump(catalog, f)
            Path(f.name).replace(path)
    rows = demo_rows(a.end_day)
    if a.out:
        Path(a.out).write_bytes(generate.jsonl(rows))
        print(f"{len(rows)} events written")
        return 0
    if a.ingest:
        to_ingest(a.ingest, rows)
        print(f"{len(rows)} events for 9 demo characters into {a.ingest}")
    else:
        to_vl(a.vl.rstrip("/"), rows)
        print(f"{len(rows)} events for 9 demo characters into {a.vl}")
    if a.metrics:
        print(f"{to_vm(a.vm.rstrip('/'), rows, looks)} simulated armory samples into {a.vm}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
