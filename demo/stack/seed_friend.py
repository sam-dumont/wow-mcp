"""A friend's evenings, sent the way the shipper sends them: the docs site's
decoy character (tools/fixtures/generate.py --demo: Nibbet-Gearholt, an
invented Gnome priest, every name made up), shifted so the last evening was
yesterday, POSTed to /ingest with the credentials the installer wrote in the
throwaway HOME. Prints no credential.

    uv run --no-project python demo/stack/seed_friend.py <config.toml> [first|all]

first: the evenings since the last level-up (the last one yesterday) and the
addons report, so the start page turns green with the character on it (data
older than 3 days reads as quiet);
all: the evenings before it, the auction house scans and the quest list (the
tour). The two parts never overlap: each event is sent once.
"""

import base64
import json
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
conf = sys.argv[1]
part = sys.argv[2] if len(sys.argv) > 2 else "all"
yesterday = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()

with tempfile.TemporaryDirectory() as d:
    out = Path(d) / "events.ndjson"
    subprocess.run(["uv", "run", "--frozen", "python", str(REPO / "tools" / "fixtures" / "generate.py"),
                    "--demo", str(out), "--end-day", yesterday], check=True, cwd=REPO, stdout=subprocess.DEVNULL)
    events = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines() if x.strip()]

# The addons report has no character: it goes with the first part. The first
# part starts at the last level-up, so Polestar's data (wow-level) is in it:
# since 0.7.0 the start page says "Polestar sends nothing" without it.
last = max(e["_time"][:10] for e in events if e.get("char") and e.get("feed") != "wow-prices")
ding = max((e["_time"][:10] for e in events if e.get("feed") == "wow-level"), default=last)
cut = min(last, ding)
first = [e["_time"][:10] >= cut or e.get("feed") == "wow-setup" for e in events]
events = [e for e, f in zip(events, first, strict=True) if f == (part == "first")]

with open(conf, "rb") as f:
    ship = tomllib.load(f)["ship"]
auth = base64.b64encode(f"{ship['user']}:{ship['password']}".encode()).decode()
sent = 0
for i in range(0, len(events), 500):
    chunk = events[i:i + 500]
    body = "\n".join(json.dumps(e) for e in chunk).encode() + b"\n"
    req = urllib.request.Request(ship["url"], data=body, method="POST",
                                 headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-ndjson"})
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 - the lab's own /ingest
        r.read()
    sent += len(chunk)
print(f"shipped {sent} events ({part})")
