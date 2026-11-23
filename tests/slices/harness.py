"""One golden character served the way a player's data really travels:
shipper-shaped events POSTed to the app's own /ingest, stored by a
VictoriaLogs stand-in (tests/degraded.py VL, answering with wowstats.logsql),
then read back over HTTP by the page's API and in process by the MCP's tools
(`call(ctx, ...)`), each through its own code path.

The stand-in stores what /ingest forwards as VictoriaLogs does: every value
a string, empty ones dropped, so a reader that only works on the fixtures'
Python types fails here as it would in production.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from tests import degraded as D
from wowstats import ui

PW = "slices-ingest-password"
AUTH = {"Authorization": ui._basic("wow", PW).decode()}
BATCH = 4000  # lines per upload: well under INGEST_MAX_BYTES


def as_stored(r: dict) -> dict:
    """A row as VictoriaLogs answers it: strings only, no empty field."""
    out = {}
    for k, v in r.items():
        if v is None or v == "":
            continue
        if isinstance(v, bool):
            v = "true" if v else "false"
        elif isinstance(v, (int, float)):
            v = json.dumps(v)
        elif isinstance(v, (list, dict)):
            v = json.dumps(v, separators=(",", ":"))
        out[k] = v
    return out


@dataclass
class Served:
    char: str
    vl: D.VL
    app: ui.App
    srv: object
    base: str
    env: dict
    calls: dict = field(default_factory=dict)

    def get(self, path: str) -> dict:
        """GET a page API route; its JSON."""
        req = urllib.request.Request(self.base + path)
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())

    def page(self, route: str, **q) -> dict:
        qs = urllib.parse.urlencode({"name": self.char, **q})
        return self.get(f"/api/{route}?{qs}")

    def mcp(self, name: str, **args) -> dict:
        """One MCP tool on the golden character, as a client calls it."""
        from wowstats.mcp import tools as T
        from wowstats.mcp.store import Store as MStore

        key = (name, json.dumps(args, sort_keys=True))
        if key not in self.calls:
            ctx = T.Ctx(store=MStore(vl_url=self.vl.url, vm_url=self.vl.url), default_char=self.char, env=self.env)
            self.calls[key] = T.call(ctx, name, {"char": self.char, **args})
        return self.calls[key]

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        self.vl.close()


def ingest(base: str, rows: list[dict]) -> None:
    """The shipper's uploads: NDJSON to /ingest with its password."""
    for i in range(0, len(rows), BATCH):
        body = b"".join(json.dumps(r, separators=(",", ":")).encode() + b"\n" for r in rows[i:i + BATCH])
        req = urllib.request.Request(base + "/ingest", data=body, method="POST",
                                     headers={**AUTH, "Content-Type": "application/x-ndjson"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                assert r.status == 200, r.status
        except urllib.error.HTTPError as e:  # pragma: no cover - the message is the point
            raise AssertionError(f"/ingest refused an upload: {e.code} {e.read()[:300]!r}") from e


def serve(char: str, rows: list[dict], tmp: Path) -> Served:
    """The app with an empty store, the rows sent through /ingest, ready to read."""
    from wowstats import plan as planmod
    from wowstats.analysis import drive
    from wowstats.mcp import tools as T

    planmod._CACHE.clear()
    T._VIEWS.clear()
    T._MISSING.clear()
    drive._WS.clear()
    vl = D.VL([])
    env = {"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": vl.url, "VM_URL": vl.url,
           "MODEL3D": "off", "DEFAULT_CHAR": char, "HOME": str(tmp / "home"), "WOW_ITEMS": "off",
           "ANALYSIS_STATE_DIR": str(tmp / "analysis"), "INGEST_PASSWORD": PW, "MCP_PUBLIC_URL": ""}
    app = ui.App(env)
    app.port = 0
    srv = ui.serve(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    ingest(base, rows)
    # What VictoriaLogs would hand back: strings, no empty fields.
    vl.rows = [as_stored(r) for r in vl.rows]
    return Served(char, vl, app, srv, base, env)
