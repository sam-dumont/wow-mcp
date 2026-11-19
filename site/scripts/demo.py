"""The docs site's demo: the invented character's pages, built by the
product's own publisher, as plain files for a static host.

    uv run --frozen --extra publish python site/scripts/demo.py <VictoriaLogs URL> <out dir> [YYYY-MM-DD]

Run by site/scripts/build-demo.sh, which starts an empty, throwaway
VictoriaLogs first. Steps:

1. tools/fixtures/generate.py's demo profile (build_demo): the events,
   shifted so the last evening is the given day (default: yesterday, UTC);
2. into VictoriaLogs through wowstats.ingest's screening, as /ingest would;
3. wowstats.publish, as `wow-stats publish --force` (item cards and icons
   from wago.tools, the 3D viewer and models from Wowhead's CDN, copied in);
4. <out>/ the public bundle, <out>/zogzog/ the sealed one, <out>/friends/
   a page that opens it with the demo's key.

The demo's key is no secret: anyone may open the demo's friends' page, so it
is derived from a fixed string, never generated or stored.
"""

from __future__ import annotations

import base64
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


def friends_page(key: str) -> bytes:
    # The friends' link with the key in its fragment, which never reaches a server.
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Nibbet's friends' page</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<script>location.replace("../zogzog/#{key}");</script></head>
<body><p><a href="../zogzog/#{key}">Open the friends' page</a></p></body></html>
""".encode()


def main(argv: list[str]) -> int:
    vl, out = argv[0], Path(argv[1])
    end = argv[2] if len(argv) > 2 else (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
    rows = generate.build_demo(end)
    n = send(vl, rows)
    print(f"demo: {n} events for {generate.DEMO}, last evening {end}")
    with tempfile.TemporaryDirectory() as tmp:
        key_file = Path(tmp) / "zogzog.key"
        key_file.write_text(DEMO_KEY + "\n")
        env = {**os.environ, "VL_URL": vl, "VL_ARCHIVE_URL": "", "VM_URL": "", "PUBLISH_DIR": f"{tmp}/site",
               "ZOGZOG_KEY_FILE": str(key_file), "PLAUSIBLE_DOMAIN": "", "ANALYSIS_SHARE_URL": "",
               "HELP_KEY_FILE": "", "RENDER_URL": "", "PUBLISH_PUSH_URL": "", "USERS_URL": "",
               "DEFAULT_CHAR": generate.DEMO}
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
