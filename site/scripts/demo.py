"""The docs site's demo: nine invented characters' pages, built by the
product's own publisher, as plain files for a static host.

    uv run --frozen --extra publish python site/scripts/demo.py <VictoriaLogs URL> <out dir> [YYYY-MM-DD]

Run by site/scripts/build-demo.sh, which starts an empty, throwaway
VictoriaLogs and VictoriaMetrics and the picture renderer first, and passes
them as VM_URL, RENDER_URL and RENDER_TOKEN_FILE. Steps:

1. tools/fixtures/generate.py's demo roster (demo_roster): the events,
   shifted so the last session is on the given day (default: yesterday, UTC);
2. into VictoriaLogs through wowstats.ingest's screening, as /ingest would;
3. the Battle.net exporter's series for each character (demo_series:
   profile, look, equipment, talents, the Legacy achievement) into
   VictoriaMetrics, as vm-wow holds them;
4. the game files, as the person's store (MODEL3D_ASSETS_DIR): Nibbet's
   real model and gear when DEMO_3D_STORE names a store from the `demo-3d`
   release (tools/fixtures/demo3d.py, fetched by site/scripts/demo3d-asset.sh),
   else the made-up gnome. roster3d adds eight synthetic characters and two
   companions, in the formats
   the shipper sends (WDC5 tables, M2, skins, BLP);
5. wowstats.publish, as `wow-stats publish --force`: item cards and icons
   from wago.tools, the 3D view from the store, each level's picture from
   the renderer, and the paperdoll's still too;
6. <out>/ the public bundle, <out>/zogzog/ the sealed one, <out>/friends/
   a page that opens it with the demo's key.

The demo's key is no secret: anyone may open the demo's friends' page, so it
is derived from a fixed string, never generated or stored.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "fixtures"))

import generate  # noqa: E402
import roster3d  # noqa: E402

from wowstats import ingest, publish  # noqa: E402

DEMO_KEY = base64.urlsafe_b64encode(hashlib.sha256(b"wow-stats public demo").digest()).rstrip(b"=").decode()


def send(vl: str, rows: list[dict]) -> int:
    lines, bad = ingest.parse(generate.jsonl(rows))
    if bad:
        raise SystemExit(f"demo: {bad} events the ingest screen refuses")
    q = urllib.parse.urlencode({"_stream_fields": ingest.STREAM_FIELDS, "_time_field": "_time", "_msg_field": "_msg"})
    for i in range(0, len(lines), 5000):
        req = urllib.request.Request(f"{vl}/insert/jsonline?{q}", data=b"\n".join(lines[i:i + 5000]) + b"\n",
                                     method="POST", headers={"Content-Type": "application/stream+json"})
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 - the local store this script was given
            r.read()
    # VictoriaLogs makes new rows searchable within a second or so: wait for
    # all of them, so the publisher never reads half a history.
    for _ in range(60):
        body = urllib.parse.urlencode({"query": "* | stats count() as n"}).encode()
        with urllib.request.urlopen(f"{vl}/select/logsql/query", data=body, timeout=30) as r:  # noqa: S310
            got = int(json.loads(r.read().decode().splitlines()[0])["n"])
        if got >= len(lines):
            return got
        time.sleep(1)
    raise SystemExit(f"demo: VictoriaLogs holds {got} of {len(lines)} events after a minute")


def send_metrics(vm: str, series: list[dict], probe: str = "wow_character_talent_rank") -> int:
    """The exporter's series into VictoriaMetrics (/api/v1/import, a JSON
    line per series), then wait until a query sees the newest."""
    body = "".join(json.dumps(s, separators=(",", ":")) + "\n" for s in series).encode()
    req = urllib.request.Request(f"{vm}/api/v1/import", data=body, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 - the local store this script was given
        r.read()
    q = urllib.parse.urlencode({"query": f"count(last_over_time({probe}[1d]))"})
    for _ in range(60):
        # A forced flush: VictoriaMetrics makes rows searchable within seconds.
        with contextlib.suppress(OSError):
            urllib.request.urlopen(f"{vm}/internal/force_flush", timeout=10).read()  # noqa: S310
        with urllib.request.urlopen(f"{vm}/api/v1/query?{q}", timeout=30) as r:  # noqa: S310
            res = json.loads(r.read())["data"]["result"]
        if res and int(float(res[0]["value"][1])) > 0:
            return sum(len(s["values"]) for s in series)
        time.sleep(1)
    raise SystemExit("demo: VictoriaMetrics shows none of the Battle.net series after a minute")


def friends_page(key: str) -> bytes:
    # The friends' link with the key in its fragment, which never reaches a server.
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Dawnwatch friends' page</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<script>location.replace("../zogzog/#{key}");</script></head>
<body><p><a href="../zogzog/#{key}">Open the friends' page</a></p></body></html>
""".encode()


def real_store() -> Path | None:
    """DEMO_3D_STORE: the real model's store (tables/, files/, look.json),
    or None for golden3d's."""
    p = Path(os.environ.get("DEMO_3D_STORE") or "")
    if os.environ.get("DEMO_3D_STORE") and (p / "look.json").is_file() and (p / "tables" / "build.json").is_file():
        return p
    return None


def main(argv: list[str]) -> int:
    vl, out = argv[0], Path(argv[1])
    real = real_store()
    look = json.loads((real / "look.json").read_text()) if real else None
    end = argv[2] if len(argv) > 2 else (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
    vm = os.environ.get("VM_URL", "").rstrip("/")
    rows = generate.demo_roster(end)
    n = send(vl, rows)
    print(f"demo: {n} events for nine characters, last session {end}")
    if vm:
        n = send_metrics(vm, generate.demo_series(rows, time.time(), look=look))
        print(f"demo: {n} Battle.net samples")
    else:
        print("demo: no VM_URL, so no Battle.net profile, talents or look")
    with tempfile.TemporaryDirectory() as tmp:
        key_file = Path(tmp) / "zogzog.key"
        key_file.write_text(DEMO_KEY + "\n")
        g = roster3d.write_store(Path(tmp) / "assets", real)
        print(f"demo: synthetic roster models and {len(g['tables'])} tables, Nibbet from {real or 'golden3d'}")
        env = {**os.environ, "VL_URL": vl, "VL_ARCHIVE_URL": "", "VM_URL": vm, "PUBLISH_DIR": f"{tmp}/site",
               "ZOGZOG_KEY_FILE": str(key_file), "PLAUSIBLE_DOMAIN": "", "ANALYSIS_SHARE_URL": "",
               "HELP_KEY_FILE": "", "PUBLISH_PUSH_URL": "", "USERS_URL": "", "DEFAULT_CHAR": generate.DEMO,
               "MODEL3D": "on", "MODEL3D_CACHE_DIR": f"{tmp}/model3d", "MODEL3D_ASSETS_DIR": f"{tmp}/assets",
               # Every level's picture in this one build: the publisher's
               # budget is for a server that builds again in ten minutes.
               "RENDER_URL": os.environ.get("RENDER_URL", ""), "RENDER_BUDGET_S": "900"}
        if not env["RENDER_URL"]:
            print("demo: no RENDER_URL, so no picture: the paperdoll and each level show the 3D toggle")
        res = publish.publish(env, force=True)
        if not res.get("built"):
            raise SystemExit(f"demo: the publisher built nothing: {res}")
        cur = Path(tmp) / "site" / "current"
        shutil.rmtree(out, ignore_errors=True)
        shutil.copytree(cur / "public", out)
        shutil.copytree(cur / "zogzog", out / "zogzog")
    (out / "friends").mkdir()
    (out / "friends" / "index.html").write_bytes(friends_page(DEMO_KEY))
    print(f"demo: {sum(1 for _ in out.rglob('*'))} files in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
