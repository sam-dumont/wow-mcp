"""DEMO DATA: the local demo, the docs site's demo character. Nibbet-Gearholt,
an invented Gnome priest, from tools/fixtures/generate.py's build_demo: the
same history the docs site's demo plays (site/scripts/demo.py), shifted so
his last evening was yesterday (UTC). Every event also carries demo=true, so
the page shows its DEMO DATA banner and nobody mistakes it for a real
character.

    docker compose run --rm demo        # the compose kit (deploy/compose): through the ui's /ingest
    uv run dev/seed.py --ingest URL     # a running ui's /ingest (user wow, or INGEST_USER;
                                        # password INGEST_PASSWORD, from the environment)
    uv run dev/seed.py [--vl URL]       # straight into VictoriaLogs (default http://127.0.0.1:9428)
    uv run dev/seed.py --out FILE       # write the events, send nothing
    ... [--metrics [--vm URL]]          # also his Battle.net series into VictoriaMetrics
                                        # (default http://127.0.0.1:8428), as the exporter writes them
    ... [--end-day YYYY-MM-DD]          # his last evening (default: yesterday, UTC)

Through /ingest the page lists him seconds later; a write straight to
VictoriaLogs waits for the ui's next check, a minute or more, and needs
-retentionPeriod=100y (the default 7 days drops older events on arrival).

What the docs site's demo has and this doesn't: the game files for the 3D
view (golden3d.py, or the demo-3d release's model), the renderer's pictures,
and, without --metrics, the Battle.net series. The full view says so where
they'd show, as for a player without them; a silhouette stands in for the
pictures (tests/test_local_demo_complete_browser.py).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import sys
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
    """build_demo's events, ending `end_day` (default yesterday, UTC), each
    marked demo=true."""
    end = end_day or (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
    return [{**r, "demo": True} for r in generate.build_demo(end)]


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


def to_vm(vm: str, rows: list[dict]) -> int:
    series = generate.bnet_series(rows, datetime.now(UTC).timestamp())
    post(f"{vm}/api/v1/import", "".join(json.dumps(s, separators=(",", ":")) + "\n" for s in series).encode(), {})
    return sum(len(s["values"]) for s in series)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Load the demo character (DEMO DATA).")
    ap.add_argument("--ingest", metavar="URL", help="a running ui's /ingest")
    ap.add_argument("--vl", metavar="URL", default="http://127.0.0.1:9428", help="VictoriaLogs, without --ingest")
    ap.add_argument("--out", metavar="FILE", help="write the events (NDJSON), send nothing")
    ap.add_argument("--metrics", action="store_true", help="also the Battle.net series into VictoriaMetrics")
    ap.add_argument("--vm", metavar="URL", default="http://127.0.0.1:8428", help="VictoriaMetrics, with --metrics")
    ap.add_argument("--end-day", metavar="YYYY-MM-DD", help="his last evening (default: yesterday, UTC)")
    a = ap.parse_args(argv)
    rows = demo_rows(a.end_day)
    if a.out:
        Path(a.out).write_bytes(generate.jsonl(rows))
        print(f"{len(rows)} events written")
        return 0
    if a.ingest:
        to_ingest(a.ingest, rows)
        print(f"{len(rows)} events for {generate.DEMO} into {a.ingest}")
    else:
        to_vl(a.vl.rstrip("/"), rows)
        print(f"{len(rows)} events for {generate.DEMO} into {a.vl}")
    if a.metrics:
        print(f"{to_vm(a.vm.rstrip('/'), rows)} Battle.net samples into {a.vm}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
