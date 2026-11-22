"""The manual's generated parts, from the code and the data the app itself uses.

    uv run python tools/manual/generate.py           # write
    uv run python tools/manual/generate.py --check   # exit 1 if a page is stale

Whole pages:

- docs/reference/environment.md: every variable the programs read, from
  tools/manual/env.toml, checked against the code by tests/test_manual.py;
- docs/reference/mcp-tools.md: every MCP tool and prompt, from the server's
  own registry (what /mcp lists);
- docs/reference/addons.md: every addon, from docs/videos/steps.json, the
  list the start page, the help page and the shipper's preflight use;
- docs/reference/http-endpoints.md: every path the servers answer, by port,
  from ENDPOINTS below (the test checks it against the route code).

Blocks inside hand-written pages, between `<!-- generated:<name> -->` and
`<!-- /generated:<name> -->`: the player's guide and the AI connection page
are rendered from docs/videos/steps.json and wowstats/mcp/helppage.py, the
same sources as /start and /help/, so the manual and the app can't say
different things.
"""

from __future__ import annotations

import ast
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_TOML = ROOT / "tools" / "manual" / "env.toml"
STEPS = ROOT / "docs" / "videos" / "steps.json"
COMPOSE = ROOT / "deploy" / "compose" / "compose.yaml"
ENV_EXAMPLE = ROOT / "deploy" / "compose" / ".env.example"
BASE = "https://wow.example.org"  # what {base} becomes: the server's address in examples

# ---------------------------------------------------------------- environment: the scan

NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
# Set by the OS or by the code for a library, never by an operator.
NOT_SETTINGS = frozenset({"APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME", "HOME", "USERPROFILE", "USERNAME",
                          "USERDOMAIN", "PYTHONIOENCODING", "FASTMCP_CHECK_FOR_UPDATES", "LASTEXITCODE",
                          "SYSTEMROOT"})
SHELL_VAR = re.compile(r"\$(?:\{|env:)?(WOW_STATS_[A-Z0-9_]+)")


def _code_files() -> list[Path]:
    return sorted((ROOT / "wowstats").rglob("*.py")) + [ROOT / "shipper" / "wow_stats.py"]


def _const(n) -> str | None:
    return n.value if isinstance(n, ast.Constant) and isinstance(n.value, str) else None


def scan_env() -> dict[str, dict]:
    """Every variable the programs read: {name: {"files": set, "defaults": set of literal defaults}}.

    An environment read is `<x>.get("NAME"[, default])`, `<x>["NAME"]` or
    `"NAME" in <x>` where <x> names an environment (`env`, `os.environ`...),
    `os.getenv("NAME")`, or a helper whose name says secret or env with a
    constant argument (`_secret(env, "NAME")` also reads NAME_FILE). Plus the
    `$WOW_STATS_*` the installers' shell and PowerShell read."""
    found: dict[str, dict] = {}

    def add(name: str, f: Path, default: str | None = None):
        if not NAME.match(name) or name in NOT_SETTINGS:
            return
        e = found.setdefault(name, {"files": set(), "defaults": set()})
        e["files"].add(str(f.relative_to(ROOT)))
        if default:
            e["defaults"].add(default)

    for f in _code_files():
        text = f.read_text(encoding="utf-8")
        tree = ast.parse(text)
        parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                fn = ast.unparse(n.func)
                consts = [c for a in n.args if (c := _const(a)) is not None]
                if (isinstance(n.func, ast.Attribute) and n.func.attr == "get"
                        and "env" in ast.unparse(n.func.value).lower()):
                    if not consts or consts[0] != _const(n.args[0]):
                        continue
                    default = _const(n.args[1]) if len(n.args) > 1 else None
                    p = parents.get(n)
                    if default is None and isinstance(p, ast.BoolOp) and isinstance(p.op, ast.Or) and p.values[0] is n:
                        default = _const(p.values[1])
                    add(consts[0], f, default)
                elif fn == "os.getenv" or (not isinstance(n.func, ast.Attribute) and
                                           ("secret" in fn.lower() or "env" in fn.lower())):
                    for c in consts:
                        add(c, f)
                        if fn.endswith("_secret") and not c.endswith("_FILE"):
                            add(c + "_FILE", f)
            elif isinstance(n, ast.Subscript) and "env" in ast.unparse(n.value).lower():
                if (c := _const(n.slice)) is not None:
                    add(c, f)
            elif (isinstance(n, ast.Compare) and isinstance(n.ops[0], (ast.In, ast.NotIn))
                  and "env" in ast.unparse(n.comparators[0]).lower() and (c := _const(n.left)) is not None):
                add(c, f)
        if f.name == "account.py":  # the install command's shell and PowerShell bodies
            for m in SHELL_VAR.finditer(text):
                add(m.group(1), f)
    for f in sorted((ROOT / "shipper").glob("install-*")):
        for m in SHELL_VAR.finditer(f.read_text(encoding="utf-8")):
            add(m.group(1), f)
    return found


def compose_settings() -> tuple[set[str], dict[str, set[str]]]:
    """The `${NAME}` the compose file interpolates, and each service's environment keys."""
    import yaml

    text = COMPOSE.read_text(encoding="utf-8")
    overrides = "".join(p.read_text(encoding="utf-8") for p in sorted(COMPOSE.parent.glob("compose.*.yaml")))
    interp = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", text + overrides))
    services = {name: set((s.get("environment") or {}).keys())
                for name, s in yaml.safe_load(text)["services"].items()}
    return interp, services


def env_example_names() -> set[str]:
    return set(re.findall(r"^#?\s?([A-Z][A-Z0-9_]+)=", ENV_EXAMPLE.read_text(encoding="utf-8"), re.M))


# Which program a compose service runs.
COMPOSE_SERVICE = {"ui": "ui", "ui-public": "ui", "publish": "publish", "site": "static", "exporter": "exporter"}


def load_env() -> dict[str, dict]:
    return tomllib.loads(ENV_TOML.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- Markdown helpers

GEN_NOTE = ("<!-- Generated by tools/manual/generate.py from {src}. Edit the source, then run "
            "`uv run python tools/manual/generate.py`. tests/test_manual.py fails while this is stale. -->")


def cell(s) -> str:
    return str(s if s is not None else "").replace("\n", " ").replace("|", "\\|").strip()


def table(head: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(cell(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


# Long paths first, so a path with spaces stays one code span.
_PATHS = sorted([
    "C:\\Program Files (x86)\\World of Warcraft\\_forever_\\Interface\\AddOns",
    "C:\\Program Files (x86)\\World of Warcraft\\_forever_",
    "/Applications/World of Warcraft/_forever_/Interface/AddOns",
    "/Applications/World of Warcraft/_forever_",
], key=len, reverse=True)
_TOKEN = re.compile("|".join([re.escape(p) for p in _PATHS] + [
    r"/psrec on", r"/reload", r"/simx",
    r"(?<![\w/])_forever_(?:[/\\][\w./\\-]*)?",
    r"(?<![\w`])(?:~|%[A-Z]+%|\$env:[A-Z]+)[/\\][\w./\\-]*",
    r"(?<![\w/`])(?:Interface|WTF)/[\w./-]*",
]))


def _span(m: re.Match) -> str:
    """A path in a code span. A sentence's full stop stays outside it: the
    path pattern takes dots, so "...wow-stats.log." would end up in the span."""
    tok = m.group(0)
    if tok.endswith(".") and not tok.endswith(".."):
        return f"`{tok[:-1]}`."
    return f"`{tok}`"


def text(s: str) -> str:
    """steps.json prose as Markdown: paths and slash commands in code spans
    (a bare `_forever_` would turn italic), existing code spans kept."""
    s = s.replace("{base}", BASE)
    parts = re.split(r"(`[^`]*`)", s)
    for i, p in enumerate(parts):
        if i % 2 == 0:
            p = _TOKEN.sub(_span, p.replace("*", "\\*"))
            parts[i] = p
    return "".join(parts)


def code(cmd: str, lang: str) -> str:
    return f"```{lang}\n{cmd.replace('{base}', BASE)}\n```"


# ---------------------------------------------------------------- environment page

GROUPS = [
    ("page", "The page (`wow-stats ui`)", "The live full view, and the old live public view."),
    ("ingest", "Uploads from the shipper", "`POST /ingest`, on the full view's port."),
    ("stores", "The stores", "Where the events and the Battle.net data live."),
    ("items", "Game data and the 3D view", "What the servers download, and the bundled reference data."),
    ("mcp", "The MCP endpoint and its login", "For your AI assistant. Off until `MCP_PUBLIC_URL` is set. "
     "[Connect your AI](../ai/connect.md) and [Set up a login](../self-host/oidc.md) use them."),
    ("analysis", "The play analysis and plans", "Kept per character, judged by the server."),
    ("sites", "The static sites (`wow-stats publish`, `wow-stats static`)",
     "The public site and the sealed `/zogzog/` site. [Share your page](../self-host/sharing.md)."),
    ("render", "Past levels' pictures (`wow-stats render`)", "Optional. A separate image with Chromium."),
    ("people", "Several people on one install", "Off until `WOW_STATE_DIR` is set. "
     "[Add friends](../self-host/friends.md)."),
    ("exporter", "The Battle.net exporter (`wow-stats exporter`)", "Optional. Needs your own API client. "
     "[Battle.net data](../self-host/battlenet.md)."),
    ("shipper", "The player's computer", "Read by the shipper and its installers, not by the server."),
    ("compose", "The compose kit's `.env` only", "Read by `docker compose` in `deploy/compose`, "
     "not by a program. The other compose settings are the variables above, passed through."),
    ("dev", "Tests and development", "Point an upstream at a local server. Leave them alone in production."),
]


def env_page() -> str:
    env = load_env()
    out = [
        "# Environment variables",
        "",
        "**For:** self-hosters setting up or tuning a server. **At the end:** you know every setting, "
        "which program reads it, its default and an example value.",
        "",
        GEN_NOTE.format(src="tools/manual/env.toml and a scan of the code"),
        "",
        "Every program is one command of the same image: `wow-stats ui`, `publish`, `static`, `exporter`, "
        "`render`, `users`. The compose kit (`deploy/compose`) sets most of these for you: in `.env` you "
        "only set what its comments name. \"Read by\" says which command reads a variable; `compose` means "
        "only `docker compose` reads it, from `.env`.",
        "",
        "A variable ending in `_FILE` names a file that holds the value (a secret, a key). Prefer it to "
        "the plain form: a file doesn't show up in `docker inspect` or a process list.",
        "",
    ]
    names = sorted(env)
    for gid, title, intro in GROUPS:
        rows = [[f"`{n}`", ", ".join(env[n]["read_by"]), env[n]["type"], env[n]["default"], env[n]["example"],
                 env[n]["what"]] for n in names if env[n]["group"] == gid]
        if not rows:
            continue
        cols = ["Variable", "Read by", "Type", "Default", "Example", "What it does"]
        out += [f"## {title}", "", intro, "", table(cols, rows), ""]
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------- MCP tools page

def _listing():
    from wowstats.mcp import server, tools

    served, prompts = server.listing()
    return served, prompts, tools.MARKET


def _args(params: dict) -> str:
    props = {k: v for k, v in (params.get("properties") or {}).items() if k != "char"}
    req = set(params.get("required") or [])
    if not props:
        return "none"
    return "; ".join(f"`{k}`{' (required)' if k in req else ''}: {cell(v.get('description') or '')}"
                     for k, v in props.items())


def _kind(t) -> str:
    a = getattr(t, "annotations", None)
    if a is None:
        return "reads"
    hint = a.model_dump()  # field names moved between MCP SDK versions; the dump has both spellings' values
    if hint.get("read_only_hint", hint.get("readOnlyHint")):
        return "reads"
    return "writes, can replace what's kept" if hint.get("destructive_hint", hint.get("destructiveHint")) else "writes"


def mcp_page() -> str:
    from wowstats.mcp import helppage

    served, prompts, market = _listing()
    by = {t.name: t for t in served}
    out = [
        "# MCP tools and prompts",
        "",
        "**For:** anyone connecting an AI assistant, and self-hosters who want to know what it can reach. "
        "**At the end:** you know every tool the server lists, what each answers, its arguments, and the "
        "prompts.",
        "",
        GEN_NOTE.format(src="the MCP server's own registry (what `/mcp` lists)"),
        "",
        "Your assistant reads this list from the server by itself, with every argument described. You "
        "don't need to name tools: ask in plain words. This page is for when you want to know what's "
        "there. Every tool takes `char` (`Name-Surname`); without it, the character the server opens on.",
        "",
        "\"Reads\" tools change nothing. \"Writes\" tools keep something for the character (a plan, an "
        "analysis step); the ones marked \"can replace what's kept\" may overwrite an earlier one.",
        "",
    ]
    for group, what, names in helppage.GROUPS:
        out += [f"## {group}", "", what[0].upper() + what[1:] + ".", ""]
        rows = []
        for n in names:
            t = by[n]
            rows.append([f"`{n}`", t.description, _args(t.parameters), _kind(t)])
        out += [table(["Tool", "What it answers", "Arguments", "Kind"], rows), ""]
    out += [
        "## Battle.net market data (off by default)",
        "",
        "`market` is listed only to a person who turned on \"Battle.net data to my AI\" in their own "
        "Settings, on a shared install. Blizzard's terms keep API data 30 days at most, and a chat keeps "
        "what a tool returned, so it's that person's call. A one-person install never serves it.",
        "",
        table(["Tool", "What it answers", "Arguments", "Kind"],
              [[f"`{market.name}`", market.description, _args(market.input_schema), "reads"]]),
        "",
        "## Prompts",
        "",
        "Ready-made requests. In claude.ai they're in the `+` menu; in Claude Code they're slash commands.",
        "",
        table(["Prompt", "What it does", "Arguments"],
              [[f"`{p.name}`", p.description or p.title or "",
                "; ".join(f"`{a.name}`: {cell(a.description)}" for a in (p.arguments or [])) or "none"]
               for p in prompts]),
        "",
    ]
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------- addons page

def steps() -> dict:
    return json.loads(STEPS.read_text(encoding="utf-8"))


def addons_page() -> str:
    st = steps()
    data_by_addon: dict[str, list[str]] = {}
    for d in st.get("data", []):
        for a in d["addons"]:
            data_by_addon.setdefault(a, []).append(d["what"])
    rows = [[f"[{a['name']}]({a['url']})", "required" if a["required"] else "optional", a["what"],
             a.get("step") or "", f"`{a['dir']}`", ", ".join(data_by_addon.get(a["id"], [])) or "no section of its own"]
            for a in st["addons"]]
    routes = st.get("addon_routes", {})
    out = [
        "# Addons",
        "",
        "**For:** players and self-hosters. **At the end:** you know every addon wow-stats reads, which "
        "ones you need, and what each one adds to your page.",
        "",
        GEN_NOTE.format(src="docs/videos/steps.json, the list the start page and the installer check use"),
        "",
        "wow-stats never ships an addon of its own. It reads the files well-known addons already save "
        "in your `_forever_` folder, when you `/reload` or log out. A missing optional addon leaves its "
        "part of the page empty; nothing breaks.",
        "",
        table(["Addon", "Needed", "What it gives your page", "The one step", "Folder in `Interface/AddOns`", "Fills"],
              rows),
        "",
    ]
    for key in ("app", "manual"):
        r = routes.get(key)
        if r:
            out += [f"## {r['title']}", ""]
            if r.get("url"):
                out += [f"Get it at <{r['url']}>.", ""]
            out += [f"{i}. {text(s)}" for i, s in enumerate(r["steps"], 1)] + [""]
    out += [
        "## Turn them on for each character",
        "",
        "At character select, click **AddOns** (bottom left), pick the character in the drop-down at the "
        "top (or \"All characters\"), tick the addons, then **Okay**. A character whose addons are off "
        "writes nothing wow-stats can read.",
        "",
        "Polestar Recorder starts switched off unless the Polestar Client installed it: type `/psrec on` "
        "once in game.",
        "",
    ]
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------- HTTP endpoints page

# Every path the servers answer, by listener. `route`: what your reverse
# proxy does with it. Patterns: a trailing * is a prefix, <x> a parameter.
ENDPOINTS = [
    ("The full view: `wow-stats ui`, port 8080 (`PORT`)",
     "Your own page. Route it through your login, except the rows that say otherwise.",
     [
         (["/", "/app.js", "/app.css", "/base.css", "/theme.js", "/items.js", "/fonts/*"], "login",
          "the page and its files"),
         (["/api/config", "/api/chars", "/api/char", "/api/journal", "/api/overview", "/api/timeline",
           "/api/snapshot", "/api/people", "/api/loot", "/api/dungeons", "/api/meta", "/api/news", "/api/gear",
           "/api/analysis", "/api/nudge", "/api/items", "/api/plan", "/api/market", "/api/prices", "/api/logsql",
           "/api/*"], "login",
          "the page's JSON answers (`/api/plan` also takes a POST)"),
         (["/model3d/*", "/model3d/file/<id>", "/api/model3d", "/render/<char>.webp",
           "/icons/<size>/<name>.jpg"], "login",
          "the 3D view (its frame, and the game files your shipper sent, by id), "
          "the character's picture (our still of the 3D model), the item icons"),
         (["/full/"], "login", "`FULL_DOOR_PATH`: a redirect to `/`, through your login. Empty: off"),
         (["/healthz"], "public", "health check: `200 ok`, no login. Fine to route; probes need no credentials"),
         (["/ingest"], "public", "`POST` from the shipper, with its own password (`INGEST_PASSWORD`). "
          "Route it **without** your page login, or the shipper can't get through. 8 MB a post. A 502 means "
          "VictoriaLogs refused the lines or couldn't be reached: nothing counts against the daily budget, and "
          "the shipper sends them again later"),
     ]),
    ("Extra full-view paths with several people (`WOW_STATE_DIR`)",
     "Only on a shared install. Same port.",
     [
         (["/start", "/start/print", "/start/media/*", "/settings", "/account.js", "/account.css", "/api/me",
           "/api/me/status", "/api/me/install-code", "/api/me/settings", "/api/me/*",
           "/api/admin/hosts"], "login", "the start page, settings, and each person's own answers"),
         (["/admin", "/api/admin/overview"], "login",
          "the owner's overview ([Admin overview](../self-host/admin.md)); 404 for anyone else"),
         (["/help", "/help/", "/help/help.js", "/help/help.css"], "login",
          "the manual for friends: install steps, the page tour, connecting their AI"),
         (["/install/<code>", "/privacy", "/privacy/*"], "public",
          "the install command (the single-use code is the credential) and the privacy page: no login"),
     ]),
    ("The shipper's auto-update, the 3D view's files and the item facts: inside `wow-stats ui`, same port",
     "With one person or several. Route it like `/ingest`, **without** your page login: the shipper "
     "sends its upload password.",
     [
         (["/install/update/shipper"], "public",
          "`GET`, upload password: the shipper's version and each file's sha256, as JSON"),
         (["/install/update/shipper/<name>"], "public",
          "`GET`, upload password: one of the shipper's files (`wow_stats.py`, the installers)"),
         (["/install/assets/need"], "public",
          "`GET`, upload password, while `MODEL3D` is on: the game files the 3D view still wants from that "
          "person's shipper (client tables by file id, then models and textures), as JSON"),
         (["/install/assets/table/<Name>", "/install/assets/file/<id>"], "public",
          "`POST`, upload password and `/ingest`'s daily budget: one client table, or one model, skin, "
          "skeleton, animation or texture the server asked for (`X-Content-SHA256`), 48 MB at most, "
          "kept in that person's own folder"),
         (["/install/items/need", "/install/items/facts", "/install/items/table/<Name>"], "public",
          "`POST`, upload password (`facts` and `table` within `/ingest`'s daily budget): which items from the "
          "game's hotfix cache the server lacks, then their facts (JSON, 500 items a post) and the client's "
          "item-level budget table (`X-Content-SHA256`), kept in that person's own folder"),
     ]),
    ("The MCP endpoint: inside `wow-stats ui`, port 8090 (`MCP_PORT`)",
     "Only when `MCP_PUBLIC_URL` is set. Route these to 8090 **without** your page login: "
     "the MCP's own tokens protect them. Let cookies through on `/authorize`, `/consent` and "
     "`/oauth/callback`, or every login stops at the Allow Access button.",
     [
         (["/mcp"], "public", "the MCP endpoint (OAuth bearer token)"),
         (["/authorize", "/consent", "/oauth/callback"], "public", "the login pages your browser opens"),
         (["/token", "/register", "/revoke"], "public", "called by the AI client's servers"),
         (["/.well-known/oauth-authorization-server", "/.well-known/oauth-protected-resource",
           "/.well-known/oauth-protected-resource/mcp"], "public", "discovery"),
     ]),
    ("The static sites: `wow-stats static`, port 8080 in the container (8081 in the compose kit)",
     "Files only, no login, no cookie. Safe to put on the internet.",
     [
         (["/", "/zogzog/", "/help/"], "public",
          "the public site, the sealed friends' site (`ZOGZOG_PATH`) and the help page (with `HELP_KEY_FILE`)"),
         (["/healthz"], "public", "health check"),
         (["/js/script.js", "/api/event"], "public",
          "only with `PLAUSIBLE_DOMAIN`: route these two to your Plausible, nothing else (on the full view's name "
          "too, ahead of its login, when the ui has `PLAUSIBLE_DOMAIN`)"),
     ]),
    ("Never route these from outside", "Internal listeners. Keep them on a private network.", [
        (["/bundle", "/bundles"], "never", "the static server's upload port (`UPLOAD_PORT`)"),
        (["/analysis.json", "/users.json", "/hosts.json", "/profile-characters", "/publisher.json",
          "/assets/<slug>/*", "/items/<slug>/facts"], "never",
         "the full view's share port (`ANALYSIS_SHARE_PORT`); the publisher POSTs its pass to `/publisher.json` "
         "and reads each person's 3D files from `/assets/<slug>/` and item facts from `/items/<slug>/facts`"),
        (["/render", "/file/<id>", "/model3d/view", "/healthz"], "never",
         "the renderer, port 8083: the publisher POSTs a recipe to `/render` and the files it lacks to `/file/<id>`"),
        (["/metrics", "/healthz"], "never",
         "the exporter, port 9464 (`LISTEN_PORT`): its own health only, never Blizzard's data"),
        (["/select/*", "/insert/*"], "never", "VictoriaLogs, port 9428: your events, readable without a login"),
    ]),
]
ROUTE_WORDS = {"login": "through your login", "public": "no login", "never": "never"}


def endpoints_page() -> str:
    out = [
        "# HTTP endpoints",
        "",
        "**For:** self-hosters writing reverse proxy rules or network policies. **At the end:** you know "
        "every path each server answers, on which port, and whether it goes through your login, opens "
        "to anyone, or stays private.",
        "",
        GEN_NOTE.format(src="ENDPOINTS in tools/manual/generate.py, checked against the route code"),
        "",
        "To set up a proxy: [Put it behind a reverse proxy](../self-host/reverse-proxy.md).",
        "",
    ]
    for title, intro, rows in ENDPOINTS:
        out += [f"## {title}", "", intro, "",
                table(["Path", "Route", "What"],
                      [[", ".join(f"`{p}`" for p in paths), ROUTE_WORDS[route], what] for paths, route, what in rows]),
                ""]
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------- blocks from steps.json and the help page

OS_LANG = {"mac": "sh", "windows": "powershell"}
MEDIA = ROOT / "docs" / "videos" / "media"


def clip(name: str | None, label: str) -> list[str]:
    """A step's clip (steps.json `media`, "x.mp4" for x-light.mp4 and
    x-dark.mp4) as a paragraph that is only a link: GitHub opens the file,
    the docs site plays it in the reader's theme (site/scripts/prepare.mjs).
    Every block page sits one folder under docs/."""
    if not name:
        return []
    stem = name.removesuffix(".mp4")
    if not (MEDIA / f"{stem}-light.mp4").exists():
        raise SystemExit(f"steps.json names {name}, but there's no docs/videos/media/{stem}-light.mp4")
    mac = " (Mac)" if stem.endswith("-mac") else ""
    return [f"[Watch: {label}{mac}](../videos/media/{stem}-light.mp4)", ""]


def os_block(key: str, lines: list[str]) -> list[str]:
    """One OS's part, in a div the docs site turns into Windows | Mac tabs
    (src/components/Footer.astro); GitHub shows both, one after the other."""
    return [f'<div class="os" data-os="{key}">', "", *lines, "</div>", ""]


def _os_cmds(os_: dict, personal: bool) -> list[str]:
    out = []
    for key in ("windows", "mac"):
        o = os_.get(key)
        if not o:
            continue
        part = []
        part += [f"**{o['label']}.** {text(o.get('how', ''))}".rstrip(), ""]
        if o.get("command"):
            cmd = o["command"]
            if personal and "{code}" in cmd:
                cmd = cmd.replace("{code}", "<your code>")
                part += ["Your own command is on your start page:", ""]
            part += [code(cmd, OS_LANG[key]), ""]
        if o.get("installs"):
            part += ["<details>", "<summary>What it installs</summary>", "", text(o["installs"]), "", "</details>", ""]
        out += os_block(key, part)
    return out


def block_steps() -> str:
    st = steps()
    out = clip(st.get("media"), "the whole setup, from the start page")
    for i, s in enumerate(st["steps"], 1):
        g = s.get("generic") or {}
        # The caption is /start's subtitle; here the body says it already.
        out += [f"### {i}. {s['title']}", ""]
        out += clip(s.get("media"), s["title"].lower())
        for p in g.get("body") or s.get("body", []):
            out += [text(p), ""]
        if s.get("os"):
            out += _os_cmds(s["os"], personal=True)
        # One action per step in sight; how to tell it worked, and the fix
        # when it didn't, a click away (the page stays under ~350 words).
        out += ["<details>", "<summary>Check it worked</summary>", "",
                f"**It worked when:** {text(g.get('check') or s['check'])}", "",
                f"**If not:** {text(g.get('if_not') or s['if_not'])}", ""]
        if s.get("code_note"):
            out += [text(s["code_note"]), ""]
        out += ["</details>", ""]
    return "\n".join(out)


def block_needs() -> str:
    return "\n".join(f"- {text(n)}" for n in steps()["needs"])


def block_update() -> str:
    st = steps()
    out = [f"- {text(u)}" for u in st["update"]]
    upd = next((s["update"] for s in st["steps"] if s.get("update")), {})
    if upd:
        # Only a shipper that didn't update itself needs it: folded.
        out += ["", "<details>", "<summary>If your start page asks for an update</summary>", "",
                *clip(st.get("update_media"), "an update"), "No code needed:", ""]
        for key, label in (("windows", "Windows (PowerShell)"), ("mac", "Mac (Terminal)")):
            if key in upd:
                out += os_block(key, [f"**{label}.**", "", code(upd[key], OS_LANG[key]), ""])
        out += ["</details>", ""]
    return "\n".join(out).rstrip()


def block_uninstall() -> str:
    u = steps()["uninstall"]
    out = clip(u.get("media"), "an uninstall")
    out += [text(p) + "\n" for p in u["body"]]
    out += _os_cmds(u["os"], personal=False)
    return "\n".join(out).rstrip()


def block_shipper() -> str:
    sh = steps()["shipper"]
    out = [text(p) + "\n" for p in sh["body"]]
    out += ["It sends:", ""] + [f"- {text(x)}" for x in sh["sends"]]
    out += ["", "It never sends:", ""] + [f"- {text(x)}" for x in sh["never"]]
    return "\n".join(out)


def block_status() -> str:
    s = steps()["guide"]["status"]
    out = clip(s.get("media"), "reading the log and running it by hand")
    out += [text(p) + "\n" for p in s["body"]]
    out += _os_cmds(s["os"], personal=False)
    return "\n".join(out).rstrip()


def block_trouble(part: str) -> str:
    out = []
    for t in steps()["troubleshooting"]:
        if t.get("part", "install") != part:
            continue
        # The symptoms in sight, each fix one click away: a reader scans for theirs.
        out += [f"### {text(t['title'])}", "", "<details>", "<summary>Why, and the fix</summary>", "",
                text(t["body"]), "", f"**Fix:** {text(t['fix'])}", "", "</details>", ""]
    return "\n".join(out).rstrip()


def block_missing() -> str:
    st = steps()
    names = {a["id"]: a["name"] for a in st["addons"]}
    rows = [[d["what"], " or ".join(names[a] for a in d["addons"]), text(d["fix"])] for d in st.get("data", [])]
    return table(["Missing from your page", "Comes from", "Fix"], rows)


# The guide's sections the manual gives a page of their own (docs/players/):
# "Your page at a glance" links those instead of saying it all twice.
OWN_PAGE = frozenset({"overview", "character", "dungeons", "plan", "timeline"})


def block_page() -> str:
    g = steps()["guide"]
    out = [text(p) + "\n" for p in g["what"]["body"]]
    out += ["The tabs at a glance:", ""] + [f"- **{t}**: {text(d)}" for t, d in g["tabs"]] + [""]
    for sec in g["page"]:
        if sec["id"] in OWN_PAGE:
            continue
        out += [f"## {sec['title']}", ""]
        if sec.get("caption"):
            out += [f"*{text(sec['caption'])}*", ""]
        out += clip(sec.get("media"), sec["title"].lower())
        out += [text(p) + "\n" for p in sec.get("body", [])]
        if sec.get("points"):
            # Folded under their names: the page stays a glance (~350 words),
            # each setting's detail a click away.
            names = ", ".join(label for label, _ in sec["points"])
            out += ["<details>", f"<summary>{names}</summary>", ""]
            out += [f"- **{label}**: {text(d)}" for label, d in sec["points"]]
            out += ["", "</details>", ""]
    return "\n".join(out).rstrip()


def block_addons() -> str:
    """Every addon, what it unlocks and its one step: the same list as /start,
    /help/ and the addons page (tests/test_addon_lists.py)."""
    return "\n".join(f"- **[{a['name']}]({a['url']})** ({'required' if a['required'] else 'optional'}): "
                     f"{text(a['what'])}. {text(a.get('step') or '')}".rstrip() for a in steps()["addons"])


def block_clients() -> str:
    from wowstats.mcp import helppage

    url = BASE + "/mcp"
    out = []
    # The first client (Claude) in full, the others folded under their name:
    # a reader looks for one client, not thirteen.
    for i, c in enumerate(helppage.CLIENTS):
        if i == 1:
            out += ["### Other clients", ""]
        if i:
            out += ["<details>", f"<summary>{c.name}</summary>", ""]
        else:
            out += [f"### {c.name}", ""]
        out += [f"{i}. {text(s)}" for i, s in enumerate(c.steps, 1)] + [""]
        if c.snippet:
            snip = c.snippet.replace("{url}", url)
            lang = "json" if snip.startswith("{") else ("sh" if " " in snip else "text")
            out += [f"```{lang}\n{snip}\n```", ""]
        if c.note:
            out += [f"> {text(c.note)}", ""]
        out += [f"**It worked when:** {text(c.check or helppage.CHECK)}", ""]
        if i:
            out += ["</details>", ""]
    return "\n".join(out).rstrip()


def block_asks() -> str:
    from wowstats.mcp import helppage

    return "\n".join(f"- \"{q}\"" for q, _ in helppage.ASKS)


def block_groups() -> str:
    from wowstats.mcp import helppage

    return "\n".join(f"- **{g}**: {d}." for g, d, _ in helppage.GROUPS)


def block_semantic() -> str:
    """The metric catalogue, from wowstats/analysis/semantic.yaml."""
    from wowstats.analysis import semantic

    cat = semantic.catalogue()

    def cell(t) -> str:
        return str(t or "").replace("|", "\\|").replace("\n", " ")

    def rules(m) -> str:
        """The fields the compiler reads, in words: aggregation, time, basis,
        then the rows a two-sided metric reads or the evidence it needs."""
        tm = m["time_meaning"]
        out = [f"{m['aggregation']}; time: {', '.join(tm) if isinstance(tm, list) else tm}; {m['evidence_basis']}."]
        if m.get("eligibility"):
            out.append(f"Rows: {m['eligibility']['means']}")
        if m.get("min_evidence"):
            out.append(f"Needs {m['min_evidence']['means']} (else NULL; `{m['name']}_evidence` counts them).")
        return cell(" ".join(out))

    out = []
    for model in semantic.spec():
        ms = [m for m in cat["metrics"] if m["model"] == model]
        out += [f"### {model}", "", cell(semantic.spec()[model].get("description")), "",
                "| Metric | Kind | Unit | Definition | Caveats | Read as |", "|---|---|---|---|---|---|"]
        out += [f"| `{m['name']}` | {m['kind']} | {cell(m['unit'])} | {cell(m['description'])} | "
                f"{cell(m['caveats'])} | {rules(m)} |" for m in ms]
        out += ["", "Group or filter by: " + ", ".join(f"`{d}`" for d in ms[0]["group_by"]) + ".", ""]
    cross = [m for m in cat["metrics"] if m["model"] == "cross"]
    if cross:
        out += ["### Across two models", "",
                "Each model aggregates on its own to the question's group_by, the results join on those "
                "columns, then the expression runs on the joined row: no fan-out. They group and filter by the "
                "dimensions their models share.", "",
                "| Metric | Kind | Unit | Expression | Definition | Caveats | Read as | Group or filter by |",
                "|---|---|---|---|---|---|---|---|"]
        out += [f"| `{m['name']}` | {m['kind']} | {cell(m['unit'])} | `{cell(m['expr'])}` | {cell(m['description'])} "
                f"| {cell(m['caveats'])} | {rules(m)} | {', '.join(f'`{d}`' for d in m['group_by'])} |" for m in cross]
        out += [""]
    store = semantic.store_catalogue()["metrics"]
    if store:
        out += ["### Store models", "",
                "Read by the tools that fetch their rows from the store for one answer (players, people, prices, "
                "talent_changes): the rows go into an in-memory copy for that call and nothing is kept, so the "
                "store's own retention (30 days for Battle.net data) is the only one. query_metric doesn't read "
                "them.", "",
                "| Metric | Model | Unit | Definition | Read as |", "|---|---|---|---|---|"]
        out += [f"| `{m['name']}` | {m['model']} | {cell(m['unit'])} | {cell(m['description'])} | {rules(m)} |"
                for m in store]
        out += [""]
    out += ["### Views per grain", "",
            "Each view is the metrics below compiled at one entity, one row per key, added to the character's "
            "DuckDB copy beside the models' views. `play_sql` reads them by name; each metric column is the "
            "metric of that name, its rules included.", "",
            "| View | One row per | Columns | Definition |", "|---|---|---|---|"]
    out += [f"| `{v['name']}` | `{v['entity']}` | {', '.join(f'`{c}`' for c in [x['name'] for x in v['columns']])} "
            f"| {cell(v['description'])} |" for v in semantic.views().values()]
    out += [""]
    out += ["### Dimensions", "", "| Dimension | Models | Definition |", "|---|---|---|"]
    out += [f"| `{d['name']}` | {', '.join(d['models'])} | {cell(d['description'])}"
            + (f" Grains: {', '.join(d['grains'])}." if d.get("grains") else "") + " |" for d in cat["dimensions"]]
    return "\n".join(out)


BLOCKS = {
    "semantic-catalogue": block_semantic,
    "player-needs": block_needs,
    "player-steps": block_steps,
    "player-update": block_update,
    "player-uninstall": block_uninstall,
    "player-shipper": block_shipper,
    "player-status": block_status,
    "player-trouble": lambda: block_trouble("install"),
    "player-missing": block_missing,
    "player-page": block_page,
    "addon-list": block_addons,
    "ai-clients": block_clients,
    "ai-asks": block_asks,
    "ai-groups": block_groups,
    "ai-trouble": lambda: block_trouble("mcp"),
}
BLOCK_SOURCE = {k: "docs/videos/steps.json" for k in BLOCKS}
BLOCK_SOURCE["semantic-catalogue"] = "wowstats/analysis/semantic.yaml"
BLOCK_SOURCE.update({"ai-clients": "wowstats/mcp/helppage.py (CLIENTS)", "ai-asks": "wowstats/mcp/helppage.py (ASKS)",
                     "ai-groups": "wowstats/mcp/helppage.py (GROUPS)"})
BLOCK_RE = re.compile(r"(<!-- generated:([a-z-]+) -->\n)(.*?)(<!-- /generated:\2 -->)", re.S)

PAGES = {
    "docs/reference/environment.md": env_page,
    "docs/reference/mcp-tools.md": mcp_page,
    "docs/reference/addons.md": addons_page,
    "docs/reference/http-endpoints.md": endpoints_page,
}


def fill_blocks(md: str) -> str:
    def sub(m):
        name = m.group(2)
        if name not in BLOCKS:
            raise SystemExit(f"unknown generated block {name}")
        note = f"<!-- From {BLOCK_SOURCE[name]}: edit it there, then run tools/manual/generate.py. -->\n"
        return m.group(1) + note + "\n" + BLOCKS[name]().rstrip() + "\n\n" + m.group(4)

    return BLOCK_RE.sub(sub, md)


def block_pages() -> list[Path]:
    return sorted(p for p in (ROOT / "docs").rglob("*.md") if "<!-- generated:" in p.read_text(encoding="utf-8"))


def render_all() -> dict[Path, str]:
    out = {ROOT / rel: fn() for rel, fn in PAGES.items()}
    for p in block_pages():
        out[p] = fill_blocks(p.read_text(encoding="utf-8"))
    return out


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale = []
    for path, want in render_all().items():
        have = path.read_text(encoding="utf-8") if path.exists() else None
        if have != want:
            stale.append(path.relative_to(ROOT))
            if not check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(want, encoding="utf-8")
    if check and stale:
        print("stale, run `uv run python tools/manual/generate.py`: " + ", ".join(map(str, stale)))
        return 1
    print(("wrote " if stale else "up to date: ") + (", ".join(map(str, stale)) if stale else "every page"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
