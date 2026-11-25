"""The help page friends are sent: /help/#<key> on the static site.

Sealed like /zogzog/ (wowstats.publish.seal: AES-256-GCM, the key in the
link's fragment, which never reaches the server or Plausible), under its own
key (HELP_KEY_FILE), so the help link opens nothing else and the zogzog link
doesn't open this. The bundle holds a shell (SHELL, helppage.js: the same
decryption as web/app.js's openBytes, the switches and the copy buttons, and
helppage.css) and the sealed files: CONTENT_PATH, plus a step's clip when it
has one (media/<name>.enc, sealed under the same key; one per theme when
docs/videos/media has the -dark/-light pair, made by demo/record.sh).

One page, in the order a friend needs it (the reader plays WoW, has never
heard of the addons or the program that sends their data, and is often on a
phone: the first screen answers "how do I use this"):

1. Get started: what you get in one line, then 3 steps with one action each
   (guide.what and guide.quick of docs/videos/steps.json, the addon list,
   the per-OS "open a terminal" lines; the install command itself carries a
   personal code, so it stays on the start page), then the onboarding clip.
2. Your page: the tour clip and one line per tab (guide.tabs); the guide.page
   sections folded under "Every tab in detail".
3. Your AI: the server address, a compact client picker (CLIENTS), 3 things
   to ask (ASKS); what the AI sees (GROUPS) and its troubleshooting folded.
   The tool and prompt reference stays with the server, which hands it to
   the AI: every argument describes itself in its schema (a test checks).
4. Something wrong?: the install questions, collapsed.
5. Everything else, collapsed: the addons step by step, what the install
   puts where, the sending check, update, uninstall, what you need.

Each fact is said once. Keys the steps file doesn't have leave their section
out. Words visible before anything is opened stay under ~350 (a browser test
counts them).

The content is generic: no character or player data. docs/mcp.md stays the
developer doc.
"""

from __future__ import annotations

import functools
import html
import re
from dataclasses import dataclass
from pathlib import Path

from wowstats import bar

from . import tools

DIR = "help"
CONTENT_PATH = "help/content"  # the sealed file's name and its associated data
MEDIA_AD = "help/media/"  # + the file name: a clip's associated data
_HERE = Path(__file__).resolve().parent

_SHELL = """<!doctype html>
<html lang="en" data-busy><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow"><meta name="referrer" content="no-referrer">
<meta name="color-scheme" content="dark light">
<title>{title}</title>{head}</head>
<body class="helpdoc">{bar}
<nav class="hnav" id="hnav" aria-label="On this page"{hidden}><a href="#start" data-jump="start">Start</a>
<a href="#page" data-jump="page">Your page</a><a href="#ai" data-jump="ai">Your AI</a>
<a href="#trouble" data-jump="trouble">Problems</a></nav>
<main id="help" class="hmain"{main}>{content}</main><script src="help.js"></script></body></html>
"""
# The static site's shell: the content arrives sealed (helppage.js opens it).
# Home is the site's own page (its root); nothing behind the login.
SHELL = _SHELL.format(title="Leveling log", head=bar.head("../", "help.css"), bar=bar.header("../"),
                      hidden=" hidden", main="", content="<p class=\"hwait\">Opening…</p>")


def script() -> str:
    """The page's only script (helppage.js), with the associated data."""
    return ((_HERE / "helppage.js").read_text(encoding="utf-8")
            .replace("@AD@", CONTENT_PATH).replace("@MEDIA@", MEDIA_AD))


def stylesheet() -> str:
    return (_HERE / "helppage.css").read_text(encoding="utf-8")


def registry() -> tuple[list[tools.Tool], list[dict]]:
    """The tools and prompts /mcp lists, read from FastMCP's registry
    (wowstats.mcp.server.listing: what a caller without the Battle.net
    setting gets), in the shapes of tools.TOOLS and prompts.PROMPTS. The one
    seam with the server."""
    from .server import listing

    served_tools, served_prompts = listing()
    return ([tools.Tool(t.name, t.title or t.name, t.description or "", t.parameters, None) for t in served_tools],
            [{"name": p.name, "title": p.title, "description": p.description,
              "arguments": [{"name": a.name, "description": a.description, "required": a.required}
                            for a in p.arguments or []]} for p in served_prompts])


# ---------------------------------------------------------------- MCP: clients

@dataclass(frozen=True)
class Client:
    """One client's "how to connect": steps in plain words, then one thing
    to copy, a note for what isn't checked yet, and how to tell it worked
    (CHECK follows it). `{url}` in any of them is the MCP URL. `checked` False
    marks a client whose login wasn't tried end to end."""
    name: str
    steps: tuple[str, ...]
    snippet: str = ""
    note: str = ""
    check: str = ""
    checked: bool = True


CHECK = "Ask \"use wow-stats: what's my character doing?\": the answer names your character and its level."
THEN = "Then a" + CHECK[1:]

# From the shared client table (each client's own source, read 2026-11-18;
# the server side of it is MCP_CIMD_HOSTS and MCP_REDIRECT_URIS, whose
# defaults are in wowstats/mcp/server.py). Claude and ChatGPT first, then
# the coding tools.
CLIENTS: tuple[Client, ...] = (
    Client("Claude (claude.ai, desktop, phone)",
           ("Settings → Connectors → Add custom connector.", "Paste the URL, leave the client id and secret empty.",
            "Connect, then log in when your browser asks. The phone app uses connectors added on the web."),
           "{url}",
           check="The connector shows Connected, and a chat's tools menu lists wow."),
    Client("Claude Code",
           ("Run this in a terminal.", "In Claude Code, /mcp → wow → Authenticate, and log in in your browser."),
           "claude mcp add --transport http wow {url}",
           check="/mcp shows wow as connected with its tools."),
    Client("ChatGPT",
           ("Settings → Connectors, turn on developer mode, then Create.",
            "Paste the URL, pick OAuth, and log in when asked."),
           "{url}",
           check="The connector shows as connected. Pick it for a chat."),
    Client("Codex (CLI and IDE)",
           ("Add the server, then log in: Codex opens your browser.",),
           "codex mcp add wow --url {url} && codex mcp login wow",
           check="codex mcp list shows wow."),
    Client("opencode",
           ("Add this to opencode.json (next to anything already under \"mcp\").",
            "opencode opens your browser to log in the first time it uses the server."),
           '{"mcp": {"wow": {"type": "remote", "url": "{url}"}}}'),
    Client("VS Code (Copilot Chat)",
           ("Add this to .vscode/mcp.json, or to your user mcp.json for every workspace.",
            "Start the server from the file; VS Code opens your browser to log in."),
           '{"servers": {"wow": {"type": "http", "url": "{url}"}}}',
           check="The server shows Running in mcp.json. Use agent mode."),
    Client("Copilot CLI",
           ("In the CLI, /mcp add, pick HTTP and paste the URL.", "Log in when your browser opens."),
           "{url}",
           "Not checked end to end yet: the exact path Copilot CLI's login comes back on. If it's refused, tell "
           "the owner.", checked=False),
    Client("Cursor",
           ("Add this to ~/.cursor/mcp.json (or .cursor/mcp.json in a project).",
            "Cursor shows a Login button next to the server: click it and log in."),
           '{"mcpServers": {"wow": {"url": "{url}"}}}'),
    Client("Gemini CLI",
           ("Add this to settings.json (~/.gemini/settings.json).",
            "Keep scopes set to openid: without it Gemini asks for every scope the server advertises.",
            "Log in when your browser opens."),
           '{"mcpServers": {"wow": {"httpUrl": "{url}", "oauth": {"enabled": true, "scopes": ["openid"]}}}}',
           check="/mcp lists wow with its tools."),
    Client("Zed",
           ("Add this to your Zed settings, under context_servers.", "Log in when Zed opens your browser."),
           '{"context_servers": {"wow": {"url": "{url}"}}}'),
    Client("Goose",
           ("Extensions → Add → Remote (Streamable HTTP).", "Paste the URL, then log in when asked."),
           "{url}"),
    Client("pi",
           ("pi has no MCP of its own: install the pi-mcp-adapter extension (7.0.1 was checked).",
            "Add the URL as a server in the adapter, then log in when your browser opens."),
           "{url}"),
    Client("LM Studio",
           ("Add this to LM Studio's mcp.json (Program → Install → Edit mcp.json).",
            "Log in when your browser opens."),
           '{"mcpServers": {"wow": {"url": "{url}"}}}'),
    Client("Windsurf (Devin)",
           ("Add this to mcp_config.json.", "Log in when your browser opens."),
           '{"mcpServers": {"wow": {"serverUrl": "{url}"}}}',
           "Not checked yet: where Windsurf sends your browser back after the login. If it's refused, tell "
           "the owner.", checked=False),
)

# ---------------------------------------------------------------- MCP: asks and groups

# Things to ask: one short line each, copied as is. Each leads to something
# to do, works for any character, and names the prompt or tools it runs (a
# test checks they exist; the page doesn't show them).
ASKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Plan my next 2 hours, no dungeons.", ("plan", "plan_session", "check_plan")),
    ("Which upgrade should I chase next, and where?", ("gear",)),
    ("Is another run of my last dungeon worth it?", ("dungeon", "dungeons")),
)

# What the data covers, in plain words. Every tool the server lists sits in
# exactly one group (a test checks), so a new tool needs a line here.
GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Levels and pace", "time, XP and gold per level, against the route",
     ("character_summary", "levels", "route_reference")),
    ("Sessions and zones", "each session, and time, XP and deaths per zone", ("sessions", "zones")),
    ("Guide steps", "time per step, skipped steps", ("guide_steps",)),
    ("Deaths", "where, at what level, what killed you", ("deaths",)),
    ("Dungeons", "your runs, and which ones are worth it now", ("dungeons",)),
    ("Gear", "what you wear, what your bags and bank hold, and every way to an upgrade", ("gear", "inventory")),
    ("Talents", "how your talents changed, day by day", ("talent_changes",)),
    ("Gold", "your auction prices, and the next riding tier", ("prices", "mount_goal")),
    ("Plans", "session plans at your pace, and how they went",
     ("plan_session", "check_plan", "how_did_my_plan_go", "next_step", "next_moves")),
    ("Analysis", "findings about how you play, their numbers re-run before they're kept",
     ("analyse", "play_analysis", "whats_new", "sanity_check", "analysis_brief", "play_sql", "list_metrics",
      "query_metric", "propose_finding",
      "analysis_submit", "analysis_progress", "dashboard_refresh")),
    ("People", "players your game showed you", ("players",)),
    ("Game rules", "game facts: quests, items, NPCs, zones, rules, how to level your class and its meta",
     ("kb_search", "kb_get", "kb_lookup", "kb_rule", "kb_topics", "class_guide", "class_meta")),
    ("News", "what's new on Forever this week, and what is coming", ("news",)),
)

def _e(s) -> str:
    return html.escape(str(s or ""), quote=True)


def _t(s) -> str:
    """Text from the steps file: escaped, `backticks` as code."""
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", _e(s))


# ---------------------------------------------------------------- shared bits

def _snip(text: str) -> str:
    """A snippet with its copy button: the button carries the exact text."""
    return (f"<div class='snip'><pre><code>{_e(text)}</code></pre>"
            f"<button type='button' class='copy' data-copy='{_e(text)}'>Copy</button></div>")


def _switch(name: str, label: str, options: list[tuple[str, str]], cls: str = "sw") -> str:
    """Tabs; helppage.js shows the matching data-panel and remembers the pick."""
    btns = "".join(f"<button type='button' role='tab' data-sw='{_e(name)}' data-val='{_e(v)}' "
                   f"aria-selected='false'>{text}</button>" for v, text in options)
    return f"<div class='{cls}' role='tablist' aria-label='{_e(label)}'>{btns}</div>"


def _panel(name: str, val: str, body: str, tag: str = "div", cls: str = "") -> str:
    c = f" class='{cls}'" if cls else ""
    return f"<{tag}{c} role='tabpanel' data-panel='{_e(name)}:{_e(val)}' hidden>{body}</{tag}>"


def _part_of(t: dict) -> str:
    # Until the file says (`part`), the MCP entry is the one about Claude's login.
    return t.get("part") or ("mcp" if t.get("id") == "client-not-registered" else "install")


# ---------------------------------------------------------------- Install

def load_steps() -> dict:
    """The install steps the start page renders too; {} when there are none."""
    try:
        from wowstats import account
        return account.steps()
    except (ImportError, FileNotFoundError, ValueError):
        return {}


def media_names(st: dict) -> list[str]:
    """Every clip the steps file names: the intro, the steps, the guide's
    sections, updating and uninstalling."""
    g = st.get("guide") or {}
    un = st.get("uninstall") if isinstance(st.get("uninstall"), dict) else {}
    items = [st, *(st.get("steps") or []), g.get("what") or {}, g.get("status") or {}, *(g.get("page") or []), un]
    return [str(x["media"]) for x in items if isinstance(x, dict) and x.get("media")] + (
        [str(st["update_media"])] if st.get("update_media") else [])


def media_files(st: dict) -> dict[str, Path]:
    """file name -> file for the clips that exist on disk (the start page's
    media folder, both takes of a themed pair): those get a sealed copy."""
    try:
        from wowstats import account
        d = account._media_dir()
    except (ImportError, AttributeError):
        return {}
    out = {}
    for name in media_names(st):
        for f in account.media_variants(name, lambda n: (d / n).is_file()).values():
            out[f] = d / f
    return out


def _media(s: dict | str | None, have: dict) -> str:
    """A clip's slot: helppage.js decrypts the take for the page's theme
    (data-dark / data-light) or the only one (data-media)."""
    from wowstats import account

    name = s.get("media") if isinstance(s, dict) else s
    files = account.media_variants(name, lambda n: n in have) if name else {}
    if not files:
        return ""
    first = next(iter(files.values()))
    kind = "video" if first.endswith((".mp4", ".webm")) else "img"
    which = (f"data-dark='{_e(files['dark'])}' data-light='{_e(files['light'])}'" if "dark" in files
             else f"data-media='{_e(first)}'")
    return f"<figure class='media' {which} data-kind='{kind}'><p class='hwait'>Loading the clip…</p></figure>"




def _os_block(s: dict, base: str, sub) -> str:
    """A section's per-OS commands (update, uninstall, the sending check),
    Mac and Windows tabs that move together. These need no code: the
    personal install command lives on the start page only."""
    os_ = s.get("os") or {}
    if not os_:
        return ""
    opts = [(k, _e(v.get("label") or k)) for k, v in os_.items()]
    panels = []
    for k, v in os_.items():
        body = f"<p>{_t(sub(v.get('how')))}</p>" if v.get("how") else ""
        cmd = v.get("command") or ""
        if cmd and "{code}" not in cmd:
            body += _snip(sub(cmd))
        panels.append(_panel("os", k, body))
    return f"<div class='variants'>{_switch('os', 'Your computer', opts)}{''.join(panels)}</div>"


def _out(href: str, text: str, cls: str = "hbtn") -> str:
    """A link that leaves the page: named, never a bare address."""
    return (f"<a class='{cls}' href='{_e(href)}' target='_blank' rel='noopener noreferrer' "
            f"referrerpolicy='no-referrer'>{text}</a>")


def _go(links: str, path: str, text: str, cls: str = "hbtn") -> str:
    """A link to one of the reader's own pages (/start, /settings, their
    page). The static copy sits on another host: it opens the full view in a
    new tab. The full view's own copy (links "") stays in the tab."""
    if links:
        return _out(links + path, text, cls)
    return f"<a class='{cls}' href='{_e(path or '/')}'>{text}</a>"


SOURCES = {"github": "GitHub"}


def _chip(a: dict) -> str:
    """An addon: its name, linked to its page. One that isn't on CurseForge
    says where it is."""
    where = SOURCES.get(a.get("source") or "")
    inner = _e(a.get("name")) + (f" <span class='hwhere'>zip on {where}</span>" if where else "")
    return _out(a["url"], inner, "hchip") if a.get("url") else f"<span class='hchip'>{inner}</span>"


def _qa(t: dict, sub) -> str:
    """One question, collapsed: the answer is the why (when there is one)
    and the fix."""
    body = "".join(f"<p>{_t(sub(t.get(k)))}</p>" for k in ("body", "fix") if t.get(k))
    return (f"<details class='hqa' id='tr-{_e(t.get('id'))}'><summary>{_t(sub(t.get('title')))}</summary>"
            f"{body}</details>")


def _more(id_: str, title: str, body: str) -> str:
    return (f"<details class='hmore' id='{_e(id_)}'><summary>{_e(title)}</summary>"
            f"<div class='hmore-in'>{body}</div></details>")


def _paras(xs, sub) -> str:
    return "".join(f"<p>{_t(sub(p))}</p>" for p in xs or [])


# ---------------------------------------------------------------- 1. Get started

def _start(st: dict, links: str, have: dict, sub) -> str:
    """The first screen on a phone: what you get, then the 3 steps, one
    action each. Everything else waits below."""
    g = st.get("guide") or {}
    what, quick = g.get("what") or {}, g.get("quick") or {}
    steps = {s.get("id"): s for s in st.get("steps") or []}
    app = (st.get("addon_routes") or {}).get("app") or {}
    addons = st.get("addons") or []
    out = []
    if what:
        out.append(f"<h1>{_e(what.get('title'))}</h1>"
                   + "".join(f"<p class='lead'>{_t(sub(p))}</p>" for p in what.get("body") or []))
    if quick.get("steps"):
        lis = []
        for q in quick["steps"]:
            body = f"<p>{_t(sub(q.get('body')))}</p>"
            if q.get("id") == "addons":
                # The installer adds the ones off CurseForge (Polestar Recorder): step 2 says so.
                req = [a for a in addons if a.get("required") and not SOURCES.get(a.get("source") or "")]
                opt = [a for a in addons if not a.get("required")]
                act = _out(app["url"], _e(q.get("button"))) if app.get("url") and q.get("button") else ""
                chips = "".join(_chip(a) for a in req)
                more = ""
                if opt:
                    lines = "".join(f"<li>{_chip(a)} {_e(a.get('what'))}."
                                    + (f" <span class='hstep1'>{_e(a['step'])}</span>" if a.get("step") else "")
                                    + "</li>" for a in opt)
                    more = (f"<details class='hopt'><summary>{_e(q.get('optional', '{n} more').format(n=len(opt)))}"
                            f"</summary><ul>{lines}</ul></details>")
                body += ((f"<p class='hact'>{act}</p>" if act else "") + f"<p class='hchips'>{chips}</p>"
                         + (f"<div class='hrow'>{more}</div>" if more else ""))
            elif q.get("id") == "command":
                os_ = (steps.get("install") or {}).get("os") or {}
                if q.get("button"):
                    body += f"<p class='hact'>{_go(links, '/start', _e(q['button']))}</p>"
                if os_:
                    opts = [(k, _e(v.get("label") or k)) for k, v in os_.items()]
                    panels = "".join(_panel("os", k, f"<p>{_t(sub(v.get('how')))}</p>") for k, v in os_.items()
                                     if v.get("how"))
                    body += f"<div class='variants'>{_switch('os', 'Your computer', opts)}{panels}</div>"
            lis.append(f"<li class='hstep' id='q-{_e(q.get('id'))}'><h3>{_e(q.get('title'))}</h3>{body}</li>")
        out.append(f"<h2 class='hh2'>{_e(quick.get('title'))}</h2>"
                   + (f"<p class='hcap'>{_t(sub(quick['caption']))}</p>" if quick.get("caption") else "")
                   + f"<ol class='hsteps'>{''.join(lis)}</ol>")
    if not out:
        return ""
    return f"<section class='hsec hstart' id='start' tabindex='-1'>{''.join(out)}{_media(what, have)}</section>"


# ---------------------------------------------------------------- 2. Your page

def _section(sec: dict, base: str, sub, have_media: dict, skip: frozenset = frozenset()) -> str:
    """One part of the page, in detail: title, caption, paragraphs,
    labelled points, its clip (unless shown already)."""
    pts = "".join(f"<div><dt>{_e(a)}</dt><dd>{_t(sub(b))}</dd></div>" for a, b in sec.get("points") or [])
    clip = "" if sec.get("media") in skip else _media(sec, have_media)
    return (f"<section class='guide' id='g-{_e(sec.get('id'))}' tabindex='-1'>"
            f"<h3>{_e(sec.get('title'))}</h3>"
            + (f"<p class='hcap'>{_t(sub(sec['caption']))}</p>" if sec.get("caption") else "")
            + _paras(sec.get("body"), sub)
            + (f"<dl class='points'>{pts}</dl>" if pts else "") + clip + "</section>")


def _page(st: dict, base: str, have: dict, sub, links: str) -> str:
    """Your page: the tour, one line per tab, the detail folded away."""
    g = st.get("guide") or {}
    secs, tabs = g.get("page") or [], g.get("tabs") or []
    if not secs and not tabs:
        return ""
    tour = next((x.get("media") for x in secs if x.get("media")), None)
    lines = "".join(f"<div><dt>{_e(a)}</dt><dd>{_t(sub(b))}</dd></div>" for a, b in tabs)
    detail = "".join(_section(x, base, sub, have, frozenset({tour})) for x in secs)
    return ("<section class='hsec' id='page' tabindex='-1'><h2 class='hh2'>Your page</h2>"
            f"<p class='hact'>{_go(links, '', 'Open your page')}</p>"
            + _media(tour, have)
            + (f"<dl class='htabs'>{lines}</dl>" if lines else "")
            + (_more("x-page", "Every tab in detail", detail) if detail else "")
            + "</section>")


# ---------------------------------------------------------------- 3. Your AI

def _client(c: Client, url: str) -> str:
    steps = "".join(f"<li>{_e(st.replace('{url}', url))}</li>" for st in c.steps)
    note = f"<p class='helpnote'>{_e(c.note.replace('{url}', url))}</p>" if c.note else ""
    tag = "" if c.checked else " <span class='htag'>not checked end to end</span>"
    check = f"{c.check} {THEN}" if c.check else CHECK
    return (f"<h3>{_e(c.name)}{tag}</h3><ol class='nsteps'>{steps}</ol>"
            + (_snip(c.snippet.replace("{url}", url)) if c.snippet else "") + note
            + f"<p class='ok'><strong>How you know it worked:</strong> {_e(check)}</p>")


OTHER = ("<h3>An app that isn't listed</h3><p>Add the address as a remote MCP server (Streamable HTTP) with OAuth. "
         "An app on your computer opens your browser to log in and gets its access back through a loopback "
         "redirect to your own computer. Refused at login: ask the owner to add it.</p>")


def _mcp(url: str, base: str, st: dict, links: str) -> str:
    """Short on purpose: a friend reads it once to connect their AI. The tool
    and prompt reference is the server's own list, which the AI reads."""
    served_tools, served_prompts = registry()
    opts = "".join(f"<option value='{i}'>{_e(c.name.split(' (')[0])}{'' if c.checked else ' (not checked)'}"
                   "</option>" for i, c in enumerate(CLIENTS))
    panels = "".join(_panel("client", str(i), _client(c, url), cls="hclient") for i, c in enumerate(CLIENTS))
    panels += _panel("client", "other", OTHER + _snip(url), cls="hclient")
    asks = "".join(f"<li class='ask'><span>{_e(q)}</span>"
                   f"<button type='button' class='copy' data-copy='{_e(q)}'>Copy</button></li>" for q, _ in ASKS)
    groups = "".join(f"<li><strong>{_e(g)}</strong>: {_e(d)}</li>" for g, d, _ in GROUPS)
    settings = _go(links, "/settings", "Settings", "hjump")
    prompts = ", ".join(f"<code>/{_e(p['name'])}</code>" for p in served_prompts)
    sees = (f"<ul class='grps'>{groups}</ul>"
            "<p>Everything your AI reads lands in your chat, so your AI provider keeps it under its own terms. That "
            "includes the names of other players your game showed you. Auction house prices reach it only while "
            f"\"Battle.net data to my AI\" is on in your {settings}.</p>"
            "<p>claude.ai has ready-made prompts in its <code>+</code> menu, Claude Code as slash commands: "
            f"{prompts}. "
            f"Your AI lists its {len(served_tools)} tools by itself: ask it what wow-stats can do.</p>")
    sub = lambda s: str(s or "").replace("{base}", base)  # noqa: E731
    trouble = "".join(_qa(t, sub) for t in st.get("troubleshooting") or [] if _part_of(t) == "mcp")
    return f"""<section class='hsec' id='ai' tabindex='-1'><h2 class='hh2'>Your AI</h2>
<p>Connect Claude, ChatGPT or another AI app once, then ask it about your play.</p>
<div class='hfield'><span class='hlabel'>Its address</span>{_snip(url)}</div>
<label class='hpick'><span class='hlabel'>Your app</span><select data-sel='client'><option value=''>Pick yours</option>
{opts}<option value='other'>Another app</option></select></label>
{panels}
<h3 class='hh3'>Things to ask</h3>
<ul class='asks'>{asks}</ul>
{_more("x-sees", "What your AI can see", sees)}
{_more("x-ai-trouble", "Your AI doesn't connect", f"<div class='hqas'>{trouble}</div>") if trouble else ""}
</section>"""


# ---------------------------------------------------------------- 4. Something wrong?

def _trouble(st: dict, base: str, sub) -> str:
    """The questions a friend asks: why the page is empty, where the
    character went, then the install's own failures."""
    steps = {s.get("id"): s for s in st.get("steps") or []}
    by = [t for t in st.get("troubleshooting") or [] if _part_of(t) == "install"]
    see = steps.get("see") or {}
    asks = [("no-character", "My character isn't on my page",
             (see.get("generic") or {}).get("if_not") or see.get("if_not")),
            ("no-addons", "The addons aren't in WoW's AddOns list", (steps.get("addons") or {}).get("if_not"))]
    entries = ([t for t in by if t.get("id") == "no-data"]
               + [{"id": i, "title": q, "fix": a} for i, q, a in asks if a]
               + [t for t in by if t.get("id") != "no-data"])
    if not entries:
        return ""
    return ("<section class='hsec' id='trouble' tabindex='-1'><h2 class='hh2'>Something wrong?</h2>"
            f"<div class='hqas'>{''.join(_qa(t, sub) for t in entries)}</div></section>")


# ---------------------------------------------------------------- 5. Everything else

def _else(st: dict, base: str, have: dict, sub) -> str:
    """Folded: the addons by hand, what the install puts where, the sending
    check, updating, uninstalling, what you need."""
    g = st.get("guide") or {}
    steps = {s.get("id"): s for s in st.get("steps") or []}
    inst = steps.get("install") or {}
    parts = []
    routes = st.get("addon_routes") or {}
    if routes:
        body = ""
        for key in ("app", "manual"):
            x = routes.get(key)
            if x:
                body += (f"<h3>{_e(x.get('title'))}</h3><ol>"
                         + "".join(f"<li>{_t(sub(i))}</li>" for i in x.get("steps") or []) + "</ol>")
        off = [a for a in st.get("addons") or [] if SOURCES.get(a.get("source") or "")]
        if off:
            body = f"<p class='hchips'>{''.join(_chip(a) for a in off)}</p>" + body
        parts.append(_more("x-addons", "The addons, step by step", body))
    sh = st.get("shipper") or {}
    installs = "".join(f"<li><strong>{_e(v.get('label') or k)}</strong>: {_t(sub(v['installs']))}</li>"
                       for k, v in (inst.get("os") or {}).items() if v.get("installs"))
    if sh or installs or inst.get("media"):
        lists = "".join(f"<div><h4>{head}</h4><ul>" + "".join(f"<li>{_e(x)}</li>" for x in sh[k]) + "</ul></div>"
                        for k, head in (("sends", "It sends"), ("never", "It never sends")) if sh.get(k))
        parts.append(_more("x-program", sh.get("title") or "What the install puts on your computer",
                           _paras(sh.get("body"), sub) + (f"<div class='sends'>{lists}</div>" if lists else "")
                           + (f"<ul>{installs}</ul>" if installs else "") + _media(inst, have)))
    if g.get("status"):
        s = g["status"]
        parts.append(_more("x-status", s.get("title") or "Check that it sends your data",
                           _paras(s.get("body"), sub) + _os_block(s, base, sub) + _media(s, have)))
    upd = inst.get("update") or {}
    if st.get("update") or upd:
        labels = inst.get("os") or {}
        cmds = {k: {"label": (labels.get(k) or {}).get("label") or k, "command": v} for k, v in upd.items()}
        parts.append(_more("x-update", "Update", _paras(st.get("update"), sub)
                           + _os_block({"os": cmds}, base, sub) + _media(st.get("update_media"), have)))
    un = st.get("uninstall")
    if un:
        un = un if isinstance(un, dict) else {"body": un}
        parts.append(_more("x-uninstall", "Uninstall", _paras(un.get("body"), sub)
                           + _os_block({"os": un.get("os") or {}}, base, sub) + _media(un, have)))
    if st.get("needs"):
        parts.append(_more("x-needs", "What you need",
                           "<ul>" + "".join(f"<li>{_t(sub(n))}</li>" for n in st["needs"]) + "</ul>"))
    if not parts:
        return ""
    return ("<section class='hsec' id='else' tabindex='-1'><h2 class='hh2'>Everything else</h2>"
            + "".join(parts) + "</section>")


def render(mcp_url: str, st: dict | None = None, media: dict | None = None, here: bool = False) -> str:
    """The content: an HTML fragment for the shell's <main>, in the order a
    friend needs it: get started, your page, your AI, something wrong,
    everything else. Sealed on the static site; `here`: served by the full
    view itself (full_page), so the links to the reader's pages stay on it."""
    url = mcp_url or "https://<your wow-stats site>/mcp"
    base = re.sub(r"/mcp/?$", "", url)
    links = "" if here else base
    st = load_steps() if st is None else st
    media = media_files(st) if media is None else media

    def sub(s):
        return str(s or "").replace("{base}", base)

    return "\n".join(x for x in (_start(st, links, media, sub), _page(st, base, media, sub, links),
                                 _mcp(url, base, st, links),
                                 _trouble(st, base, sub), _else(st, base, media, sub)) if x)

@functools.lru_cache(maxsize=4)
def full_page(mcp_url: str) -> bytes:
    """/help/ on the full view: the same shell, script and content, for a
    reader the login already let in. The seal only keeps the server's address
    (the full view's own, kept out of certificate logs) from whoever finds the
    static copy without its link; this reader is on that host, so the content
    comes as it is, in-page links are plain #anchors, and the clips are the
    start page's own (/start/media/). Made once per address: the steps file
    and the server's tool list only change with the image."""
    st = load_steps()
    return _SHELL.format(
        title="Help", head=bar.head("/", "help.css", icon=bar.ICON), bar=bar.header("/", bar.links("/help/")),
        hidden="",
        main=" data-media-base=\"/start/media/\"", content=render(mcp_url, st, media_files(st), here=True)).encode()


def build(bundle, key: bytes, mcp_url: str) -> None:
    """Writes the help page into a static bundle (wowstats.publish.Bundle,
    unsealed: the shell is public, the content and clips sealed here under
    its own key)."""
    from wowstats.publish import seal

    st = load_steps()
    media = media_files(st)
    bundle.put(f"{DIR}/index.html", SHELL.encode())
    bundle.put(f"{DIR}/help.js", script().encode())
    bundle.put(f"{DIR}/help.css", stylesheet().encode())
    bundle.put(f"{DIR}/content.enc", seal(key, CONTENT_PATH, render(mcp_url, st, media).encode()))
    for name, f in media.items():
        bundle.put(f"{DIR}/media/{name}.enc", seal(key, MEDIA_AD + name, f.read_bytes()))
