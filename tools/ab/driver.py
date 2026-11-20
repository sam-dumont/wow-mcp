"""The A/B check's driver: runs inside the version under test (its image or a
checkout) and writes everything that version says about one character.

    python driver.py --out DIR [--char NAME] [--cases cases.json] [--skip mcp,publish]

The stores are whatever VL_URL and VM_URL name (the A/B proxy); the clock is
AB_FROZEN_AT (abclock). Outputs, one file per answer under DIR:

  api/<name>.json        the full view's JSON routes (status and body), the
                         page itself, gear per spec, a snapshot per level
  plan/compute_<h>h.json the planner's estimate for h hours (compute(), which
                         never saves a plan)
  mcp/<tool>[-n].json    every MCP tool, through `wow-stats mcp-stdio`
  public/...             `wow-stats publish`: the public bundle's pages and data
  zogzog/...             the sealed full-content bundle, opened with the run's
                         own throwaway key
  _run.json              the version, what was skipped or failed, timings

Standard library, plus FastMCP for the MCP part (the `mcp` extra the image
has). Works on the APIs of 0.6 and later: routes over HTTP, tools over the
MCP protocol, the publisher through its CLI; only the planner is imported.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import abclock  # noqa: E402

abclock.install_from_env()

# One fixed key for the run's zogzog bundle: it never leaves the run, and a
# fixed one lets the driver open every sealed file to compare what's inside.
ZZ_KEY = bytes(range(32))
TEXT_SUFFIXES = (".html", ".json", ".txt", ".xml", ".webmanifest", ".md")


def write(out: Path, rel: str, obj) -> None:
    p = out / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)[:120]


class Run:
    def __init__(self, out: Path, cases: dict, char: str | None):
        self.out = out
        self.cases = cases
        self.char = char
        self.meta: dict = {"steps": {}, "errors": []}

    def step(self, name: str, fn) -> None:
        t0 = time.monotonic()
        try:
            fn()
            self.meta["steps"][name] = "ok"
        except Exception as e:  # noqa: BLE001 - one step's failure is a finding, the rest still runs
            self.meta["steps"][name] = f"failed: {e!r}"
            self.meta["errors"].append({"step": name, "error": repr(e), "trace": traceback.format_exc()[-4000:]})
        self.meta.setdefault("seconds", {})[name] = round(time.monotonic() - t0, 1)


# ------------------------------------------------------------------ web


def start_ui(env: dict) -> tuple[str, object]:
    """The full view in this process (no background builder, no nightly
    recheck: every answer is worked out when asked)."""
    os.environ.update(env)
    from wowstats import ui

    app = ui.App(dict(os.environ))
    srv = ui.serve(app, None)
    threading.Thread(target=srv.serve_forever, name="ab-ui", daemon=True).start()
    host, port = srv.server_address[:2]
    return f"http://127.0.0.1:{port}", srv


def get(base: str, path: str, timeout: float = 300) -> dict:
    req = urllib.request.Request(base + path, headers={"Accept": "application/json, text/html"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - our own server
            status, ctype, data = r.status, r.headers.get("Content-Type") or "", r.read()
    except urllib.error.HTTPError as e:
        status, ctype, data = e.code, e.headers.get("Content-Type") or "", e.read()
    text = data.decode("utf-8", "replace")
    if "json" in ctype:
        try:
            return {"status": status, "body": json.loads(text)}
        except ValueError:
            pass
    return {"status": status, "ctype": ctype.split(";")[0], "text": text}


def web(run: Run, base: str) -> None:
    for name, path in run.cases["api_global"]:
        write(run.out, f"api/{name}.json", get(base, path))
    chars = get(base, "/api/chars").get("body") or {}
    names = [c.get("char") for c in (chars.get("chars") or []) if isinstance(c, dict)]
    if not run.char:
        run.char = os.environ.get("DEFAULT_CHAR") or (names[0] if names else "")
    run.meta["char_known"] = run.char in names
    q = urllib.parse.quote(run.char)
    for name, path in run.cases["api_char"]:
        write(run.out, f"api/{name}.json", get(base, path.replace("{char}", q)))
    gear = get(base, f"/api/gear?name={q}").get("body") or {}
    for spec in ((gear.get("spec") or {}).get("choices") or []) if isinstance(gear, dict) else []:
        key = spec.get("key") if isinstance(spec, dict) else None
        if key:
            write(run.out, f"api/gear-spec-{safe(key)}.json",
                  get(base, f"/api/gear?name={q}&spec={urllib.parse.quote(key)}"))
    tl = get(base, f"/api/timeline?name={q}").get("body") or {}
    for lv in (tl.get("levels") or []) if isinstance(tl, dict) else []:
        if isinstance(lv, int) and 1 <= lv <= 60:
            write(run.out, f"api/snapshot-{lv:02d}.json", get(base, f"/api/snapshot?name={q}&level={lv}"))


def compute(run: Run) -> None:
    from wowstats import plan

    env = dict(os.environ)
    for h in run.cases["plan_hours"]:
        try:
            r = plan.compute(env, run.char, float(h))
        except Exception as e:  # noqa: BLE001
            r = {"error": repr(e)}
        write(run.out, f"plan/compute_{h}h.json", r)


# ------------------------------------------------------------------ mcp


def _default_args(schema: dict, char: str) -> dict:
    props = schema.get("properties") or {}
    out = {}
    if "char" in props:
        out["char"] = char
    for k in schema.get("required") or []:
        if k in out:
            continue
        p = props.get(k) or {}
        t = p.get("type")
        out[k] = (p["enum"][0] if p.get("enum") else p.get("minimum", 1) if t in ("integer", "number")
                  else [] if t == "array" else {} if t == "object" else False if t == "boolean" else "")
    return out


def _result(r) -> dict:
    out: dict = {"is_error": bool(getattr(r, "is_error", False))}
    sc = getattr(r, "structured_content", None)
    if sc is not None:
        out["structured"] = sc
    texts = [getattr(c, "text", None) for c in (getattr(r, "content", None) or [])]
    texts = [t for t in texts if t is not None]
    parsed = []
    for t in texts:
        try:
            parsed.append(json.loads(t))
        except ValueError:
            parsed.append(t)
    if sc is None or out["is_error"]:
        out["content"] = parsed
    return out


async def _mcp(run: Run) -> None:
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    env = {k: v for k, v in os.environ.items()}
    tr = StdioTransport(command=sys.executable, args=[str(HERE / "launch.py"), "mcp-stdio"], env=env)
    cfg = run.cases["mcp"]
    async with Client(tr, timeout=300) as c:
        listed = await c.list_tools()
        tools = {t.name: t for t in listed}
        write(run.out, "mcp/_tools.json", sorted(
            [{"name": t.name, "title": getattr(t, "title", None), "description": t.description,
              "schema": getattr(t, "input_schema", None) or t.inputSchema} for t in listed], key=lambda d: d["name"]))
        order = [n for n in sorted(tools) if n not in cfg["last"]] + [n for n in cfg["last"] if n in tools]
        for name in order:
            schema = getattr(tools[name], "input_schema", None) or tools[name].inputSchema or {}
            calls = cfg["calls"].get(name) or cfg["last"].get(name) or [{}]
            for i, extra in enumerate(calls):
                args = {**_default_args(schema, run.char), **extra}
                if name == "check_plan" and not extra.get("text"):
                    args["text"] = ""
                try:
                    r = _result(await c.call_tool(name, args, raise_on_error=False))
                except Exception as e:  # noqa: BLE001
                    r = {"is_error": True, "exception": repr(e)}
                write(run.out, f"mcp/{name}{'' if i == 0 else f'-{i + 1}'}.json", {"args": args, **r})
        missing = sorted(set(cfg["calls"]) | set(cfg["last"]) - set(tools))
        run.meta["mcp_cases_without_tool"] = missing


def mcp(run: Run) -> None:
    asyncio.run(_mcp(run))


# ------------------------------------------------------------------ publish


def publish(run: Run, work: Path) -> None:
    site = work / "site"
    keyf = work / "zogzog.key"
    keyf.write_text(base64.urlsafe_b64encode(ZZ_KEY).rstrip(b"=").decode() + "\n")
    keyf.chmod(0o600)
    env = {**os.environ, "PUBLISH_DIR": str(site), "ZOGZOG_KEY_FILE": str(keyf), "ZOGZOG_PATH": "/zogzog/",
           "PUBLISH_PUSH_URL": "", "RENDER_URL": "", "ANALYSIS_SHARE_URL": "", "HELP_KEY_FILE": "",
           "PLAUSIBLE_DOMAIN": "", "USERS_URL": "", "PUBLISH_REPORT_URL": "", "FRIENDS_VL_URL": ""}
    p = subprocess.run([sys.executable, str(HERE / "launch.py"), "publish", "--force"], env=env,
                       capture_output=True, text=True, timeout=3600, check=False)
    run.meta["publish_exit"] = p.returncode
    if p.returncode != 0:
        raise RuntimeError(f"publish exited {p.returncode}: {p.stderr[-2000:]}")
    cur = (site / "current").resolve()
    named = _sealed_names(cur)
    files = []
    for f in sorted(cur.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(cur).as_posix()
        bundle, _, inner = rel.partition("/")
        if bundle == "zogzog" and inner in named:  # 0.7's keyed name: back to its path
            inner = named[inner] + ".enc"
            rel = "zogzog/" + inner
        files.append(rel)
        data = f.read_bytes()
        if bundle == "zogzog" and inner.endswith(".enc"):
            inner = inner[:-4]
            data = _unseal(inner, data)
        if not inner.endswith(TEXT_SUFFIXES):
            continue
        dest = run.out / ("zogzog" if bundle == "zogzog" else "public") / (inner if bundle in ("public", "zogzog")
                                                                          else rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    write(run.out, "public/_files.json", files)


def _sealed_names(cur: Path) -> dict[str, str]:
    """{keyed name: path} for the zogzog bundle of 0.7 and later, whose
    sealed files are named by an HMAC of their path (wowstats.publish
    sealed_name): every path the public bundle has (the same layout) plus
    the zogzog-only answers. Before 0.7 the names are the paths: {}."""
    try:
        from wowstats.publish import sealed_name
    except ImportError:
        return {}
    pub = cur / "public"
    paths = {f.relative_to(pub).as_posix() for f in (pub / "data").rglob("*") if f.is_file()}
    paths |= {"data/config.json", "data/chars.json", "data/items.json", "data/market.json", "data/prices.json"}
    return {sealed_name(ZZ_KEY, p): p for p in paths}


def _unseal(rel: str, blob: bytes) -> bytes:
    """A zogzog data file opened with the run's key (the path inside the
    bundle is its associated data); unreadable: marked, never guessed."""
    from wowstats.publish import unseal

    try:
        return unseal(ZZ_KEY, rel, blob)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"ab_unseal_failed": repr(e)}).encode()


# ------------------------------------------------------------------ main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--char", default="")
    ap.add_argument("--cases", default=str(HERE / "cases.json"))
    ap.add_argument("--skip", default="", help="comma-separated: web, compute, publish, mcp")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cases = json.loads(Path(a.cases).read_text(encoding="utf-8"))
    skip = {s.strip() for s in a.skip.split(",") if s.strip()}
    run = Run(out, cases, a.char or None)
    try:
        from wowstats import __version__
    except ImportError:
        __version__ = "?"
    run.meta.update({"version": __version__, "frozen_at": os.environ.get("AB_FROZEN_AT"), "skipped": sorted(skip)})
    work = Path(tempfile.mkdtemp(prefix="ab-work-"))
    try:
        base, srv = start_ui({"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "PORT": "0", "MODEL3D": "off",
                              "MCP_PUBLIC_URL": "", "MCP_AUTH": "", "INGEST_PASSWORD": "", "WOW_STATE_DIR": ""})
        if "web" not in skip:
            run.step("web", lambda: web(run, base))
        elif not run.char:
            run.char = os.environ.get("DEFAULT_CHAR", "")
        if "compute" not in skip:
            run.step("compute", lambda: compute(run))
        srv.shutdown()
        if "publish" not in skip:
            run.step("publish", lambda: publish(run, work))
        if "mcp" not in skip:
            run.step("mcp", lambda: mcp(run))
    finally:
        shutil.rmtree(work, ignore_errors=True)
        write(out, "_run.json", run.meta)
    return 1 if run.meta["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
