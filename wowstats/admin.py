"""The owner's overview at /admin: who uses this install, when they last
did, and whether its parts answer. Not what anyone plays.

Only the owner (WOW_OWNER_SUB) gets it; anyone else gets the 404 every
unknown address gets, so the page isn't advertised. It needs several people
(WOW_STATE_DIR): an install for one person has nobody else to look at.

One row per person, the owner included: the slug, whether they're on the
friends list, the last full-view visit and MCP use (wowstats.activity), the
last upload (time, events, shipper version, OS), how many characters (a
count: never a name, a level, a zone or gold), the public page on or off, the
publisher's last build of their site and its error line, the setup report
as one badge, and two "stale" flags. Above it, the install: the server's
version, the page answers' last build (wowstats.prebuilt), the publisher's
last pass (it POSTs it to the share port: take_report), the Battle.net
exporter's last success (vm-wow), the state folder's size, and the server's
own checks of its stores, the static sites and the renderer, green or red
with their latency. Nothing calls a third party.

Worked out at most every CACHE_S seconds, on a page load: no auto-refresh,
the page has a Reload button. Nothing of it is logged.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from wowstats import __version__
from wowstats import tenancy as T

CACHE_S = 30
STALE_UPLOAD_S = 3 * 86400
PUBLISHER_LATE_S = 3600
EXPORTER_LATE_S = 6 * 3600
REPORT_MAX_BYTES = 256 * 1024
REPORT_FILE = "publisher.json"
DISK_MAX_ENTRIES = 200_000
SETUP_Q = 'feed:="wow-setup" | fields _time, addons, recorder_on, shipper | sort by (_time desc) | limit 1'

_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------- the publisher's report


def take_report(t: T.Tenancy, raw: bytes) -> bool:
    """The publisher's last pass (wowstats.publish.send_report), kept as
    WOW_STATE_DIR/publisher.json, overwritten each pass. Only the known
    fields, each checked and capped: the share port's token holder is
    trusted, but the page prints what it sent."""
    try:
        d = json.loads(raw)
    except ValueError:
        return False
    if not isinstance(d, dict) or not isinstance(d.get("sites"), list):
        return False

    def check(v) -> dict | None:
        if not isinstance(v, dict):
            return None
        out = {"ok": v.get("ok") is True}
        if isinstance(v.get("ms"), int | float):
            out["ms"] = int(v["ms"])
        if v.get("error"):
            out["error"] = str(v["error"])[:200]
        return out

    sites = []
    for s in d["sites"][:500]:
        if not isinstance(s, dict) or not T.SLUG_RE.match(str(s.get("slug") or "")):
            continue
        at = s.get("built_at")
        sites.append({"slug": s["slug"], "built": s.get("built") is True,
                      "built_at": float(at) if isinstance(at, int | float) and at > 0 else None,
                      "error": str(s.get("error") or "")[:200]})
    rep = {"at": time.time(), "sent_at": str(d.get("at") or "")[:40], "version": str(d.get("version") or "")[:20],
           "seconds": float(d["seconds"]) if isinstance(d.get("seconds"), int | float) else None,
           "renderer": check(d.get("renderer")), "upload": check(d.get("upload")), "sites": sites}
    p = t.state / REPORT_FILE
    tmp = p.with_name(f".{REPORT_FILE}.{os.getpid()}.tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(rep, f)
        os.replace(tmp, p)
    except OSError:
        return False
    return True


def read_report(t: T.Tenancy) -> dict | None:
    try:
        d = json.loads((t.state / REPORT_FILE).read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


# ---------------------------------------------------------------- checks


def ping(url: str, timeout: float = 5) -> dict:
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - an address from our own env
            r.read(4096)
        return {"ok": True, "ms": round((time.monotonic() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001 - any failure is the answer
        return {"ok": False, "ms": round((time.monotonic() - t0) * 1000), "error": type(e).__name__}


def checks(env: dict, report: dict | None) -> list[dict]:
    """The server's own checks: each store and service it is set up with,
    green or red, with the time it took. Not set: not shown."""
    def base(k: str) -> str:
        return (env.get(k) or "").strip().rstrip("/")

    out = []
    todo = [("Store (VictoriaLogs)", base("VL_URL"), "/health")]
    if base("FRIENDS_VL_URL") and base("FRIENDS_VL_URL") != base("VL_URL"):
        todo.append(("Friends' store", base("FRIENDS_VL_URL"), "/health"))
    if base("VM_URL"):
        todo.append(("vm-wow", base("VM_URL"), "/health"))
    if base("RENDER_FROM"):
        todo.append(("Static sites", base("RENDER_FROM"), "/healthz"))
    if base("RENDER_URL"):
        todo.append(("Renderer", base("RENDER_URL"), "/healthz"))
    for name, url, path in todo:
        if url:
            out.append({"name": name, **ping(url + path)})
    rep = report or {}
    # What this pod can't reach itself, as the publisher saw it at its last pass.
    if not base("RENDER_URL") and rep.get("renderer"):
        out.append({"name": "Renderer", "via": "publisher", **rep["renderer"]})
    if rep.get("upload"):
        out.append({"name": "Site upload", "via": "publisher", **rep["upload"]})
    return out


def disk_use(path: Path) -> dict:
    """The state folder's size, walked (no symlinks), capped at DISK_MAX_ENTRIES."""
    total = files = 0
    stack = [path]
    capped = False
    while stack and not capped:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    files += 1
                    if files > DISK_MAX_ENTRIES:
                        capped = True
                        break
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return {"bytes": total, "files": min(files, DISK_MAX_ENTRIES), "capped": capped}


def exporter_last(app) -> float | None:
    """The Battle.net exporter's newest success (any task), from vm-wow."""
    if not app.store.profile_vm_url:
        return None
    try:
        rows = app.store.profile_query("max(wow_exporter_last_success_timestamp_seconds)")
        return max((float(r["value"][1]) for r in rows if r.get("value")), default=None)
    except Exception:  # noqa: BLE001 - vm-wow down: unknown, its check says why
        return None


# ---------------------------------------------------------------- people


def vtuple(v) -> tuple[int, ...] | None:
    """X.Y.Z or a release candidate X.Y.Z-rc.N, below its final (the
    shipper's version_tuple orders them the same)."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:-rc\.(\d+))?", str(v or "").strip())
    if not m:
        return None
    a, b, c, rc = m.groups()
    return (int(a), int(b), int(c), 0, int(rc)) if rc else (int(a), int(b), int(c), 1, 0)


def _epoch(s) -> float | None:
    if isinstance(s, int | float):
        return float(s) or None
    if not s:
        return None
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=UTC)).timestamp()


def addons_badge(rep: dict | None) -> dict:
    """The setup report as one badge: the required addons (steps.json) all
    there and the Recorder on, or how many are missing. Never the list."""
    from wowstats import account

    if rep is None:
        return {"state": "none", "text": "no report"}
    try:
        got = json.loads(rep.get("addons") or "{}") if isinstance(rep.get("addons"), str) else rep.get("addons")
    except ValueError:
        got = {}
    got = got if isinstance(got, dict) else {}
    lack = [a["id"] for a in account.addons() if a.get("required") and a["id"] not in got]
    if lack:
        return {"state": "bad", "text": f"{len(lack)} missing"}
    if "recorder" in got and str(rep.get("recorder_on")).lower() != "true":
        return {"state": "bad", "text": "Recorder off" if str(rep.get("recorder_on")).lower() == "false"
                else "Recorder not switched on yet"}
    return {"state": "ok", "text": "addons OK"}


def update_refused(rep: dict | None) -> str:
    """The shipper's last auto-update refusal (its setup report's
    `update_refused`, shipper/wow_stats.py check_update): a bad or missing
    signature, an unreachable server. "" when its last check went through."""
    why = str((rep or {}).get("update_refused") or "").strip()
    return f"auto-update refused: {why[:160]}" if why else ""


def person(t: T.Tenancy, u: T.User, listed: dict | None, sites: dict, now: float) -> dict:
    act = t.activity.get(u)
    upload, install = act.get("upload") or {}, act.get("install") or {}
    row = {"slug": u.slug or "", "owner": u.owner, "status": u.status,
           "admitted": True if u.owner else (u.active and (u.sub in listed if listed is not None else True)),
           "full": act.get("full"), "mcp": act.get("mcp"),
           "upload": {"at": upload.get("at"), "events": upload.get("events")},
           "os": install.get("os"), "version": install.get("version"),
           "chars": None, "public_on": u.public_on, "build": sites.get(u.slug) if u.slug else None,
           "addons": None, "stale": [], "error": "", "name_conflict": T.name_conflict(t, u, listed)}
    newest = None
    if u.active:
        try:
            app = t.app_for(u)
            chars = [c for c in app.views.chars() if not c.get("demo")]
            row["chars"] = len(chars)
            newest = max((_epoch(c.get("last_seen")) or 0 for c in chars), default=0) or None
            rows = app.views.vl(f"{app.views.stream} {SETUP_Q}")
            rep = max(rows, key=lambda r: str(r.get("_time") or ""), default=None)
            row["addons"] = addons_badge(rep)
            if rep and vtuple(rep.get("shipper")):
                row["version"] = rep["shipper"]
            refused = update_refused(rep)
            if refused:
                row["stale"].append(refused)
        except Exception as e:  # noqa: BLE001 - one store down: that row says so
            row["error"] = f"store: {type(e).__name__}"
    row["analysis"] = analysis_row(t, u, row) if u.active else None
    last = _epoch(upload.get("at")) or newest
    if row["chars"] and (last is None or now - last >= STALE_UPLOAD_S):
        days = int((now - last) // 86400) if last else None
        row["stale"].append(f"no upload for {days} days" if days else "no upload yet")
    if vtuple(row["version"]) and vtuple(row["version"]) < vtuple(__version__):
        row["stale"].append("shipper older than the server")
    return row


def analysis_row(t: T.Tenancy, u: T.User, row: dict) -> dict | None:
    """The person's timer for the server's own analysis
    (wowstats.analysis.auto): their play since the last refresh, who did it,
    and how the server's last try ended. None when the feature is off."""
    from wowstats.analysis import auto

    svc = auto.service()
    if not svc.cfg.on:
        return None
    env = t.env_for(u)
    out = {"played_since": None, "due": False, "at": None, "by": None, "last_try": svc.last_try(env) or None}
    try:
        tm = svc.timer(env, t.app_for(u).store)
        last = tm["last_refresh"] or {}
        out.update(played_since=tm["played_since"], due=tm["due"], at=last.get("at"), by=last.get("by"))
    except Exception as e:  # noqa: BLE001 - one store down: that cell says so
        out["error"] = type(e).__name__
    line = last_try_line(out["last_try"])
    if line:
        row["stale"].append(line)
    return out


def last_try_line(lt: dict | None) -> str:
    """How the server's last try ended, when it didn't: stopped (a cap, an
    error) or incomplete (the model quit before the synthesis), with why."""
    lt = lt or {}
    if lt.get("status") in ("stopped", "incomplete") and lt.get("why"):
        return f"server analysis {lt['status']}: {str(lt['why'])[:160]}"
    return ""


def analysis_tile(env: dict) -> dict | None:
    """The month's use of the server's own analysis, or None when it's off."""
    from wowstats.analysis import auto

    svc = auto.service()
    return auto.budget(env, svc.spend) if svc.cfg.on else None


# ---------------------------------------------------------------- the overview


def overview(t: T.Tenancy, fresh: bool = False) -> dict:
    with _lock:
        hit = _cache.get("o")
        if hit and not fresh and time.time() - hit[0] < CACHE_S:
            return hit[1]
    now = time.time()
    report = read_report(t)
    sites = {s["slug"]: s for s in (report or {}).get("sites") or []}
    listed = T.read_friends(t.friends_file) if t.friends_file else None
    users = sorted(t.registry.users(), key=lambda u: (not u.owner, u.slug or "~", u.account_id))
    people = [person(t, u, listed, sites, now) for u in users]
    owner = t.registry.get(t.owner_sub)
    with t._lock:
        apps = list(t._apps.values())
    built = [a.prebuilt.built_at for a in apps if a.prebuilt.built_at]
    errors = [a.prebuilt.error for a in apps if a.prebuilt.error]
    out = {"checked_at": now, "version": __version__,
           "answers": {"built_at": max(built) if built else None, "error": errors[0] if errors else ""},
           "publisher": ({"at": report.get("at"), "seconds": report.get("seconds"),
                          "failed": sum(1 for s in report.get("sites") or [] if s.get("error")),
                          "late": now - float(report.get("at") or 0) > PUBLISHER_LATE_S}
                         if report else None),
           "exporter": exporter_last(t.app_for(owner)) if owner else None,
           "disk": disk_use(t.state), "checks": checks(t.env, report), "people": people,
           "analysis": analysis_tile(t.env)}
    with _lock:
        _cache["o"] = (now, out)
    return out


# ---------------------------------------------------------------- the page


def ago(ts, now: float) -> str:
    s = _epoch(ts)
    if s is None:
        return "never"
    d = max(0, now - s)
    if d < 90:
        return "just now"
    if d < 90 * 60:
        return f"{round(d / 60)} min ago"
    if d < 36 * 3600:
        return f"{round(d / 3600)} h ago"
    return f"{round(d / 86400)} days ago"


def _when(ts, now: float) -> str:
    s = _epoch(ts)
    if s is None:
        return '<span class="dim">never</span>'
    iso = datetime.fromtimestamp(s, UTC).strftime("%Y-%m-%d %H:%M UTC")
    return f'<time datetime="{iso}" title="{iso}">{ago(s, now)}</time>'


def _size(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TiB"


def _tile(label: str, value: str, state: str = "") -> str:
    return (f'<div class="tile {state}"><span class="k">{escape(label)}</span>'
            f'<span class="v">{value}</span></div>')


def _row(p: dict, now: float, has_analysis: bool = False) -> str:
    def td(label: str, inner: str, cls: str = "") -> str:
        return f'<td data-label="{label}"' + (f' class="{cls}"' if cls else "") + f"><div>{inner}</div></td>"

    clash = p.get("name_conflict")
    # A clash is the owner's fix (friends never pick a name): say which name, and why.
    name = ("no name: none in the list, no BattleTag at sign-in" if clash and not clash["slug"] else
            f'name conflict: <code>{escape(clash["slug"])}</code> ({escape(clash["why"])})' if clash else
            escape(p["slug"] or "(no name yet)")) + (' <span class="tag">owner</span>' if p["owner"] else "")
    flags = "".join(f'<span class="flag">{escape(f)}</span>' for f in p["stale"])
    if p["error"]:
        flags += f'<span class="flag">{escape(p["error"])}</span>'
    admitted = ("owner" if p["owner"] else "yes" if p["admitted"] else
                "switched off" if p["status"] != "active" else "no")
    up = p["upload"] or {}
    upload = _when(up.get("at"), now)
    if up.get("events"):
        n = int(up["events"])
        upload += f' <span class="dim">{n:,} event{"" if n == 1 else "s"}</span>'
    ship = " · ".join(escape(str(x)) for x in (p["version"] and f"v{p['version']}", p["os"]) if x)
    if ship:
        upload += f'<br><span class="dim">{ship}</span>'
    b = p["build"]
    if b is None:
        build = '<span class="dim">no report</span>'
    else:
        build = _when(b.get("built_at"), now) + (
            f'<br><span class="bad">{escape(b["error"])}</span>' if b.get("error") else ' <span class="ok">ok</span>')
    badge = p["addons"]
    addons = (f'<span class="badge {badge["state"]}">{escape(badge["text"])}</span>' if badge
              else '<span class="dim">-</span>')
    return ("<tr" + (' class="stale"' if p["stale"] or p["error"] else "") + ">"
            + td("Person", f'<strong>{name}</strong>{flags and "<br>" + flags}', "who")
            + td("On the list", admitted)
            + td("Last visit", _when(p["full"], now))
            + td("Last AI use", _when(p["mcp"], now))
            + td("Last upload", upload)
            + td("Characters", "-" if p["chars"] is None else str(p["chars"]), "n")
            + td("Public page", "on" if p["public_on"] else '<span class="dim">off</span>')
            + td("Last build", build)
            + td("Addons", addons)
            + (td("AI analysis", _analysis_cell(p["analysis"], now)) if "analysis" in p and has_analysis else "")
            + "</tr>")


def _analysis_cell(a: dict | None, now: float) -> str:
    """Play since the last refresh of the person's analysis, and who did it."""
    if a is None:
        return '<span class="dim">-</span>'
    if a.get("error"):
        return f'<span class="dim">store: {escape(a["error"])}</span>'
    by = {"server": "the server", "player": "them"}.get(str(a.get("by") or ""), "")
    played = escape(str(a.get("played_since") or "0 h 00")) + " played since"
    last = (_when(a["at"], now) + (f" by {by}" if by else "")) if a.get("at") else '<span class="dim">never</span>'
    due = ' <span class="tag">due</span>' if a.get("due") else ""
    return f"{played}{due}<br>last refresh {last}"


def page_body(o: dict) -> str:
    now = o["checked_at"]
    a, pub, disk = o["answers"], o["publisher"], o["disk"]
    tiles = [_tile("Server", f"v{escape(o['version'])}")]
    tiles.append(_tile("Page answers", _when(a["built_at"], now) + (" (stale)" if a["error"] else ""),
                       "bad" if a["error"] else ""))
    if pub is None:
        tiles.append(_tile("Publisher", '<span class="dim">no report yet</span>'))
    else:
        txt = _when(pub["at"], now) + (f', {pub["failed"]} failed' if pub["failed"] else ", ok")
        tiles.append(_tile("Publisher", txt, "bad" if pub["late"] or pub["failed"] else "good"))
    if o["exporter"] is not None:
        late = now - o["exporter"] > EXPORTER_LATE_S
        tiles.append(_tile("Battle.net", _when(o["exporter"], now), "bad" if late else "good"))
    tiles.append(_tile("State folder", _size(disk["bytes"]) + ("+" if disk["capped"] else "")))
    an = o.get("analysis")
    if an is not None:
        if an["cost_known"]:
            txt, state = f'${an["spent"]:.2f} of ${an["cap"]:g}, ${an["left"]:.2f} left', (
                "bad" if an["left"] <= 0 else "")
        else:
            txt, state = f'cost unknown, {an["tokens"]:,} tokens', ""
        if an.get("tokens_cap"):
            txt += f' <span class="dim">{an["tokens"]:,} of {an["tokens_cap"]:,} tokens</span>'
        txt += f' <span class="dim">{an["runs"]} run{"" if an["runs"] == 1 else "s"}, {escape(an["month"])}</span>'
        tiles.append(_tile("AI analysis", txt, state))
    pings = "".join(
        f'<li class="{"good" if c["ok"] else "bad"}" title="{escape(c.get("error") or "")}">'
        f'<span class="dot"></span>{escape(c["name"])}'
        f'<span class="dim">{" " + str(c["ms"]) + " ms" if "ms" in c else ""}'
        f'{" (publisher)" if c.get("via") else ""}</span></li>' for c in o["checks"])
    on = o.get("analysis") is not None
    ai_th = "<th>AI analysis</th>" if on else ""
    rows = "".join(_row(p, now, on) for p in o["people"])
    return f"""<div class="admin-head"><h1>Admin</h1>
<a class="btn ghost" href="/admin">Reload</a></div>
<p class="dim checked">Checked {ago(now, time.time())}. Times, counts and versions only.</p>
<section class="strip" aria-label="Install">{"".join(tiles)}</section>
<ul class="pings" aria-label="Checks">{pings}</ul>
<div class="table-wrap"><table class="people">
<thead><tr><th>Person</th><th>On the list</th><th>Last visit</th><th>Last AI use</th><th>Last upload</th>
<th>Characters</th><th>Public page</th><th>Last build</th><th>Addons</th>{ai_th}</tr></thead>
<tbody>{rows}</tbody></table></div>"""


def page(o: dict) -> bytes:
    from wowstats import account

    return account.page("Admin", page_body(o), "admin", script=False)


def route(h, t: T.Tenancy, u: T.User, app, path: str) -> None:
    """GET /admin, /api/admin/overview: the owner's; 404 for
    anyone else, like any address that leads nowhere."""
    if not u.owner:
        return h.not_found()
    if path == "/admin/":
        return h.send(301, b"", "text/plain", headers={"Location": "/admin"})
    o = overview(t)
    if path == "/api/admin/overview":
        return h.json(200, o)
    return h.send(200, page(o), "text/html; charset=utf-8", "no-store")
