"""POST /api/plan {char, hours, constraints}: a session plan for the full
view, the estimator's own text (no model runs on the server; the client's
model writes plans up over MCP). GET /api/plan is
ui.py's (the mount goal and the last plan's review)."""

from __future__ import annotations

import json
import os
import threading

from . import plan

_busy = threading.Lock()


def _reply(h, status: int, obj: dict) -> None:
    body = json.dumps(obj, ensure_ascii=False).encode()
    h.send_response(status)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store")
    h.send_header("X-Content-Type-Options", "nosniff")
    h.end_headers()
    h.wfile.write(body)


def handle(h, store=None, env=os.environ) -> None:
    if h.command != "POST":
        _reply(h, 405, {"error": "POST only"})
        return
    # JSON only, as /api/me/*: a cross-site form can't send it without a
    # preflight, which is never granted (security review L5).
    if not (h.headers.get("Content-Type") or "").lower().startswith("application/json"):
        _reply(h, 415, {"error": "send JSON (Content-Type: application/json)"})
        return
    try:
        n = int(h.headers.get("Content-Length") or "0")
        req = json.loads(h.rfile.read(n) if 0 < n <= 4096 else b"{}")
        if not isinstance(req, dict):
            raise ValueError
        hours = float(req.get("hours") or 3)
    except (TypeError, ValueError):
        _reply(h, 400, {"error": "expected a JSON object like {\"hours\": 3, \"constraints\": \"no dungeons\"}"})
        return
    if not _busy.acquire(blocking=False):
        _reply(h, 429, {"error": "a plan is already being made; wait for it"})
        return
    try:
        out = plan(dict(env), str(req.get("char") or "").replace(" ", "-")[:64] or None, max(0.5, min(8.0, hours)),
                   str(req.get("constraints") or "")[:300], store=store)
    except LookupError as e:
        _reply(h, 404, {"error": "no data", "why": str(e)})
        return
    finally:
        _busy.release()
    e = out["estimate"]
    _reply(h, 200, {"markdown": out["markdown"], "model": out["writer"], "supported": e.get("supported"),
                    "hours": e.get("hours")})
