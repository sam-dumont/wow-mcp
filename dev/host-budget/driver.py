"""Fixture generator and real clients, run with the released image's interpreter."""
import base64
import gzip
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path("/review")
STATE = Path("/bench")
sys.path.insert(0, str(ROOT / "tools/fixtures"))
mode = sys.argv[1]

if mode == "prepare":
    import demo3d
    import generate
    from dev.seed import demo_rows
    from tools.fixtures import roster_asset

    recent = demo_rows("2026-11-26")
    previous = [r for r in demo_rows("2025-11-26") if r.get("char") == generate.DEMO]
    for row in previous + recent:
        row["source_id"] += ":" + row["_time"][:4]
    extra = next(r.copy() for r in recent if r.get("char") == "Hesta-Bellforge" and r["feed"] == "wow-character")
    (STATE / "initial.gz").write_bytes(gzip.compress(generate.jsonl([extra]), mtime=0))
    extra["source_id"] += ":overlap"
    rows = previous + recent
    sent = STATE / "archive/sent"
    sent.mkdir(parents=True)
    for name, batch in [("roster", rows), ("overlap", [extra])]:
        (sent / (name + ".gz")).write_bytes(gzip.compress(generate.jsonl(batch), mtime=0))
    ids = defaultdict(set)
    for r in rows + [extra]:
        if r.get("char"):
            ids[r["char"]].add(r["source_id"])
    assert len(ids) == 9
    (STATE / "expected.json").write_text(json.dumps({k: len(v) for k, v in ids.items()}))
    if os.environ["MODEL3D"] == "on":
        art = roster_asset.fetch(STATE / "art")
        (STATE / "models").symlink_to(art, target_is_directory=True)
        series = generate.demo_series(recent, time.time(), demo3d.read_looks(art))
        (STATE / "metrics.ndjson").write_text("".join(json.dumps(s) + "\n" for s in series))
    print("fixture", len(rows), "rows, nine characters", flush=True)

if mode == "oidc":
    from tests.mcp.oidc_standin import Standin

    with Standin(user="owner", port=8441):
        time.sleep(3600)

if mode == "mcp":
    from tests.mcp.oauth_client import Login, result

    client = Login("http://127.0.0.1:8090", "http://127.0.0.1:8090/mcp")
    client.http.timeout = 180
    client.login()
    # The controller waits for a real partial publish before starting both clients.
    (STATE / "logged-in").touch()
    while not (STATE / "go").exists():
        time.sleep(.05)
    start = time.monotonic()
    (STATE / "mcp-start").touch()
    response = client.rpc("tools/call", {"name": "analysis_brief", "arguments": {"char": "Nibbet-Gearholt"}})
    response.raise_for_status()
    r = result(response)
    assert not r.get("error") and not r["result"].get("isError")
    print("cold analysis_brief seconds", round(time.monotonic() - start, 3), flush=True)

if mode == "publish":
    from wowstats.publish import publish

    assert publish(force=True)["built"]

if mode == "check-site":
    from wowstats import publish

    want = set(json.loads((STATE / "expected.json").read_text()))
    key = publish.unb64u((STATE / "state/site.key").read_text().strip())
    cur = STATE / "site/current"
    def read(tier, name):
        if tier == "public":
            return (cur / tier / name).read_bytes()
        return publish.unseal(key, name, (cur / tier / publish.sealed_name(key, name)).read_bytes())
    for tier in ["public", "zogzog"]:
        chars = json.loads(read(tier, "data/chars.json"))["chars"]
        assert {r["char"] for r in chars} == want
        pictures = []
        for name in sorted(want):
            try:
                image = read(tier, f"data/render/{name}.webp")
            except FileNotFoundError:
                continue
            assert image[:4] == b"RIFF" and image[8:12] == b"WEBP"
            pictures.append(name)
        print("published", tier, len(chars), "characters;", len(pictures), "WebP portraits", pictures, flush=True)
        if os.environ["MODEL3D"] == "on":
            assert len(pictures) == 9
