"""The person's own pages on the full view, when several people share an
install (wowstats.tenancy): the welcome wizard and manual (/start, and
/start/print to print or save as PDF), the settings (/settings), the privacy
note (/privacy), the API those pages call (/api/me...), the one-line
installer (/install/<code>) and the owner's host list (/api/admin/hosts).

The start page and the manual share one text: docs/videos/steps.json
(packaged as wowstats/data/start-steps.json), which the videos (demo/) read
too. /start is short on purpose (owner, 2026-11-19: the seven-step wizard was
"SO SO SO MUCH information"): it answers the one question of each visit, set
up (the install command) or "is it working?" (a status), and folds the rest.
/start/print keeps the whole manual. The pages are rendered here;
web/account.js adds the live parts (the state, the install code, the addon
ticks). No inline script or style: the CSP stays the full view's.

Writes (POST /api/me/...) take JSON only: a cross-site form can't send
application/json without a preflight this server never grants, so the
login cookie alone can't change anyone's settings.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from wowstats import __version__, bar, missing, source_tag_url
from wowstats import tenancy as T
from wowstats.admin import vtuple

_HERE = Path(__file__).resolve().parent

def addons() -> list[dict]:
    """steps.json's addons: names, links, what each adds, its AddOns folder
    (`dir`, what the installers' preflight looks for) and `feeds`, the
    feeds (or queries) that show its data arrived; an addon without one
    stays unticked. url is null until the link is verified (owner,
    2026-11-18: CurseForge links, checked in a browser). The one list /start,
    /help/, the pages' missing-data lines (wowstats.missing) and the
    shipper's preflight (its ADDONS, kept equal by a test) all read."""
    return [{**a, "feeds": a.get("feeds") or []} for a in steps().get("addons", [])]


ICONS = {
    "key": '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9m-4 4l3 3m-6 0l2 2"/>',
    "tag": '<path d="M3 3h8l10 10-8 8L3 11z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
    "puzzle": '<path d="M4 8h4a2 2 0 1 1 4 0h4v4a2 2 0 1 1 0 4v4H4z"/>',
    "terminal": '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M6 9l3 3-3 3m5 0h6"/>',
    "sword": '<path d="M14 3h7v7L9 22l-3-3L18 7"/><path d="M5 14l5 5M3 21l3-3"/>',
    "shield": '<path d="M4 3h16v8c0 6-4 9-8 11-4-2-8-5-8-11z"/><path d="M8 12l3 3 5-6"/>',
    "chat": '<path d="M4 4h16v12H9l-5 4z"/><path d="M8 9h8M8 12h5"/>',
}


def icon(name: str) -> str:
    return (f'<svg class="ico" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" '
            f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{ICONS.get(name, "")}</svg>')


def steps() -> dict:
    """The manual: the packaged copy, else the repo's (a checkout)."""
    for p in (_HERE / "data" / "start-steps.json", _HERE.parent / "docs" / "videos" / "steps.json"):
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    raise FileNotFoundError("start-steps.json")


def shipper_file(name: str) -> bytes:
    """A file the installers carry: the shipper's, or LICENSE (the GPL's text,
    installed next to the program for whoever gets it this way)."""
    if name == "LICENSE":
        cands = (_HERE / "data" / "LICENSE", _HERE.parent / "LICENSE")
    else:
        cands = (_HERE / "data" / "shipper" / name, _HERE.parent / "shipper" / name)
    for p in cands:
        if p.is_file():
            return p.read_bytes()
    raise FileNotFoundError(name)


# What the shipper's auto-update may fetch (GET /install/update/shipper/<name>):
# the program, the platforms' installers and agent template, and what the
# installers copy next to them.
SHIPPER_FILES = ("wow_stats.py", "install-mac.sh", "wow-stats.plist", "install-windows.ps1",
                 "config.example.toml", "LICENSE")
SIGNATURE_FILE = "update-signature.json"  # tools/release/shipper_sign.py; served inside the manifest only
_SHIPPER_VERSION = re.compile(rb'(?m)^VERSION = "(\d+\.\d+\.\d+(?:-rc\.\d+)?)"$')  # X.Y.Z or X.Y.Z-rc.N


def shipper_signature(version: str | None, files: dict) -> dict | None:
    """The release's signature (shipper/update-signature.json, written by
    tools/release/shipper_sign.py in release.yml), when its statement names
    exactly the files this server serves. The shipper checks it with the key
    it pins and refuses an update without it. A build from a tree between
    releases, or a shipper file changed after signing, has none: logged once,
    and the shippers keep their version (the update line still works)."""
    try:
        sig = json.loads(shipper_file(SIGNATURE_FILE))
        rel = json.loads(sig["statement"])
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        sys_log("shipper: no release signature in this build; installed shippers won't auto-update from it")
        return None
    want = {n: {"sha256": hashlib.sha256(b).hexdigest(), "size": len(b)} for n, b in files.items()}
    if rel.get("version") != version or rel.get("files") != want:
        sys_log("shipper: the release signature doesn't match the shipper files; installed shippers won't "
                "auto-update from this build")
        return None
    return {"statement": sig["statement"], "signature": sig.get("signature")}


@functools.cache
def shipper_manifest() -> bytes:
    """GET /install/update/shipper: the version of the shipper this server
    carries (the VERSION line of its wow_stats.py), each file's sha256, and
    the release's signature over both (shipper_signature), which the shipper
    checks before it replaces itself. Shippers before 0.7.0 read only
    `version` and `files`. The files don't change while the server runs:
    built once."""
    files = {n: shipper_file(n) for n in SHIPPER_FILES}
    m = _SHIPPER_VERSION.search(files["wow_stats.py"])
    version = m.group(1).decode() if m else None
    out = {"version": version,
           "files": {n: {"sha256": hashlib.sha256(b).hexdigest(), "size": len(b)} for n, b in files.items()}}
    sig = shipper_signature(version, files)
    if sig:
        out["signature"] = sig
    return json.dumps(out, separators=(",", ":")).encode()


def handle_shipper(h, rest: str) -> None:
    """/install/update/shipper[/<name>], for a caller the router already
    authenticated with an upload password: the manifest, or one file."""
    if rest == "":
        return h.send(200, shipper_manifest(), "application/json")
    name = rest[1:] if rest.startswith("/") else ""
    if name not in SHIPPER_FILES:
        return h.not_found()
    return h.send(200, shipper_file(name), "application/octet-stream")


def license_note(comment: str = "#") -> str:
    """The installers' second line: what the recipient got, under which
    license, and where its exact source is (GPL-3.0 section 6)."""
    return (f"{comment} wow-stats {__version__}, GPL-3.0-only (LICENSE goes next to the program). "
            f"Source of this version: {source_tag_url()}")


# ---------------------------------------------------------------- pages

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta name="color-scheme" content="dark light">
<title>{title}</title>
{head}
</head>
<body class="{cls}">
{bar}
<main class="doc">
{body}
</main>
</body>
</html>
"""

# The bar's links before the login (the privacy note opened logged out):
# the way in, and the note itself.
LOGGED_OUT = (("/", "Log in"), ("/privacy", "Privacy"))


def page(title: str, body: str, cls: str = "", script: bool = True, links: str | None = None,
         assets: str = "/", current: str = "") -> bytes:
    """A one-column page in the shared look (wowstats.bar). `links`: the
    bar's links, the reader's pages by default. `assets`: where base.css,
    account.css and theme.js come from, /privacy/ for the pages a login
    can't open yet."""
    head = bar.head(assets, f"{assets}account.css", icon=bar.ICON) + (
        '<script src="/account.js" defer></script>' if script else "")
    nav = bar.links(current) if links is None else links
    return PAGE.format(title=escape(title), head=head, cls=cls, bar=bar.header("/", nav), body=body).encode()


MEDIA_TYPES = {".mp4": "video/mp4", ".webm": "video/webm", ".png": "image/png", ".webp": "image/webp",
               ".gif": "image/gif"}


def media_variants(name: str, have) -> dict[str, str]:
    """A clip named in steps.json, as the files that exist: {"": name} for
    one file, {"dark": ..., "light": ...} when name.mp4 stands for the
    themed pair name-dark.mp4 and name-light.mp4 (docs/videos/media); {}
    when neither is there. `have(file_name) -> bool`."""
    name = Path(str(name or "")).name
    if not name:
        return {}
    if have(name):
        return {"": name}
    stem, dot, ext = name.rpartition(".")
    pair = {m: f"{stem}-{m}.{ext}" for m in ("dark", "light")} if dot else {}
    return pair if pair and all(have(f) for f in pair.values()) else {}


def _media(item: dict | str | None, slot: str = "") -> str:
    """A step's (or the intro's) clip, when it has one: a video follows the
    reader's colour scheme when it comes in a dark and a light take. A step
    without one keeps an empty slot."""
    m = item.get("media") if isinstance(item, dict) else item
    sid = escape(item["id"]) if isinstance(item, dict) and item.get("id") else escape(slot)
    files = media_variants(m, lambda f: (_media_dir() / f).is_file()) if m else {}
    if not files:
        return f'<figure class="media empty" data-step="{sid}" hidden></figure>' if isinstance(item, dict) else ""
    src = {k: "/start/media/" + urllib.parse.quote(v) for k, v in files.items()}
    first = next(iter(src.values()))
    if not first.endswith((".mp4", ".webm")):
        return f'<figure class="media" data-step="{sid}"><img src="{first}" alt=""></figure>'
    # #t=0.4: the clips fade in from the page colour, so their first frame is
    # blank; a little in, the still before play shows the title.
    sources = (f'<source src="{src["dark"]}#t=0.4" media="(prefers-color-scheme: dark)">'
               f'<source src="{src["light"]}#t=0.4">' if "dark" in src else f'<source src="{first}#t=0.4">')
    return (f'<figure class="media" data-step="{sid}"><video controls muted playsinline preload="metadata">'
            f'{sources}</video></figure>')


def fill(text: str, base: str, code: str = "<code>") -> str:
    """steps.json's placeholders: {base} the full view's address, {code} the
    install code (the manual shows <code>: the wizard's widget has the real one)."""
    return text.replace("{base}", base).replace("{code}", code)


def _os_blocks(st: dict, base: str, printable: bool) -> str:
    """The per-OS lines of a step. On the live wizard the commands come from
    the widgets (the person's own code and address); everything else is static."""
    out = []
    for variant in (st.get("os") or {}).values():
        cmd = fill(variant.get("command", ""), base)
        show_cmd = cmd and printable  # live: the widget shows them, with Copy buttons
        out.append(f'<div class="os-block"><p class="os">{escape(variant["label"])}</p>'
                   f'<p>{escape(variant.get("how", ""))}</p>'
                   + (f'<p><code class="inline">{escape(cmd)}</code></p>' if show_cmd else "")
                   + (f'<p class="pending"><strong>Installs:</strong> {escape(variant["installs"])}</p>'
                      if variant.get("installs") else "")
                   + "</div>")
    if st.get("code_note"):
        out.append(f'<p class="pending">{escape(st["code_note"])}</p>')
    return "".join(out)


def start_page(printable: bool = False, base: str = "") -> bytes:
    """/start (live: account.js picks the state) or the manual to print."""
    return manual_page(base) if printable else live_start_page(base)


def manual_page(base: str = "") -> bytes:
    """The whole manual, every step with its check and fix: /start/print."""
    s = steps()
    parts = [f'<h1>{escape(s["title"])}</h1>', f'<p class="lead">{escape(s["intro"])}</p>']
    for i, st in enumerate(s["steps"], 1):
        body = "".join(f"<p>{escape(fill(p, base))}</p>" for p in st["body"])
        if_not = (f'<p class="check if-not"><strong>Didn\'t work?</strong> {escape(st["if_not"])}</p>'
                  if st.get("if_not") else "")
        parts.append(
            f'<section class="step" id="step-{escape(st["id"])}" data-step="{escape(st["id"])}">'
            f'<h2><span class="num">{i}</span>{icon(st.get("icon", ""))}{escape(st["title"])}</h2>'
            f'{body}{_os_blocks(st, base, True)}{_media(st)}'
            f'<p class="check"><strong>How do I know it worked?</strong> {escape(st["check"])}</p>{if_not}</section>')
    parts.append('<section class="trouble" id="trouble"><h2>Something went wrong</h2>')
    for t in s["troubleshooting"]:
        parts.append(f'<h3 id="trouble-{escape(t["id"])}">{escape(t["title"])}</h3><p>{escape(t["body"])}</p>'
                     f'<p><strong>Fix:</strong> {escape(t["fix"])}</p>')
    parts.append("</section>")
    parts.append('<section class="step"><h2>Addons</h2><ul class="addons">' + "".join(
        f'<li><strong>{escape(a["name"])}</strong>{" (required)" if a["required"] else ""}: '
        f'{escape(a["what"])}.' + (f' {escape(a["step"])}' if a.get("step") else "")
        + (f'<br><span class="url">{escape(a["url"])}</span>' if a.get("url") else "")
        + "</li>" for a in addons())
        + "</ul></section>")
    return page(s["title"], "\n".join(parts), "print", script=False)


def _t(s: str) -> str:
    """Text from the steps file: escaped, `backticks` as code."""
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", escape(str(s or "")))


def _ext(href: str, text: str, cls: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    return (f'<a{c} href="{escape(href)}" target="_blank" rel="noopener noreferrer" '
            f'referrerpolicy="no-referrer">{text}</a>')


def _os(key: str, inner: str, tag: str = "div") -> str:
    """A part for one OS: account.js shows the reader's (data-os)."""
    return f'<{tag} data-os="{escape(key)}">{inner}</{tag}>'


OS_SWITCH = ('<div class="osw" role="group" aria-label="Your computer">'
             '<button type="button" data-pick-os="mac" aria-pressed="false">Mac</button>'
             '<button type="button" data-pick-os="windows" aria-pressed="false">Windows</button></div>')


def _fold(id_: str, summary: str, body: str, states: str) -> str:
    """A closed part of the page. states: the page states it shows in."""
    return (f'<details class="fold" id="{escape(id_)}" data-in="{escape(states)}" hidden>'
            f'<summary>{summary}</summary><div class="fold-in">{body}</div></details>')


def _cmds(cmds: dict, labels: dict) -> str:
    """Code-free commands (update, uninstall), one per OS."""
    return "".join(_os(k, f'<div class="cmd"><code>{escape(v)}</code>'
                          f'<button class="ghost" type="button" data-copy="{escape(v)}">Copy</button></div>')
                   for k, v in cmds.items() if k in labels)


# Connecting an AI app: one short block per app, picked like Mac/Windows.
# ChatGPT's path and plans are OpenAI's own (developers.openai.com
# /api/docs/guides/developer-mode and help article 12584461, read
# 2026-11-19): web only, Plugins, "Add custom MCP server".
ASK = "With the wow connector, what is my character doing and what should my next session be?"
AI_APPS = (
    ("claude", "Claude", ("On claude.ai or the desktop app: Settings, Connectors, Add custom connector. Paste the "
                          "address, save, then Connect and log in with this page's account. The phone app uses "
                          "the connectors you add on the web.",), ""),
    ("chatgpt", "ChatGPT", ("On chatgpt.com, not the phone app: open Plugins, select the plus button, then Add "
                            "custom MCP server. Name it wow-stats, paste the address as the Server URL, pick OAuth "
                            "for authentication, accept the warning and select Create as a plugin. Install it, "
                            "then log in with this page's account when asked.",
                            "OpenAI offers this on Business, Enterprise and Edu plans, and on Pro with read-only "
                            "tools. Not on your plan: the plus button has no Add custom MCP server."), ""),
    ("claude-code", "Claude Code", ("Run this in a terminal, then /mcp in Claude Code, pick wow and log in.",),
     "claude mcp add --transport http wow {mcp}"),
    ("other", "Another app", ("Any app that supports MCP: add the address as a remote MCP server (Streamable "
                              "HTTP). It asks you to log in: use this page's account.",), ""),
)
APP_SWITCH = ('<div class="osw apps" role="group" aria-label="Your AI app">' + "".join(
    f'<button type="button" data-pick-app="{k}" aria-pressed="false">{escape(n)}</button>' for k, n, _, _ in AI_APPS)
    + "</div>")


def _copy(text: str) -> str:
    return (f'<div class="cmd"><code>{escape(text)}</code>'
            f'<button class="ghost" type="button" data-copy="{escape(text)}">Copy</button></div>')


def _ai(ai: dict, base: str) -> str:
    """The AI fold: what it does, the address, then the reader's app."""
    if not base:
        return ""
    mcp = f"{base}/mcp"
    panels = []
    for key, _, lines, snippet in AI_APPS:
        body = "".join(f"<p>{escape(x)}</p>" for x in lines)
        if snippet:
            body += _copy(snippet.replace("{mcp}", mcp))
        if key == "claude":
            ask = _ext("https://claude.ai/new?q=" + urllib.parse.quote(ASK), "Ask Claude about my character")
            body += f"<p>{ask}</p>"
        panels.append(f'<div data-app="{key}">{body}</div>')
    return ((f"<p>{escape(ai['body'][0])}</p>" if ai.get("body") else "")
            + f'<h3>The address</h3>{_copy(mcp)}<h3>Your app</h3>{APP_SWITCH}{"".join(panels)}')


def live_start_page(base: str = "") -> bytes:
    """/start: one hero per state (account.js shows one), the rest folded.

    name    no address yet (the listed name clashed, or none): whose fix it is.
    new     not installed: the install command is the hero, then the
            addon and the first play, one line each.
    wait    installed in the last 15 minutes, nothing yet: play.
    none    installed, nothing ever arrived: red, and the one fix.
    ok      data arrives: green, when it last did, "Open my page"
            (amber when the last data is days old).
    About 150 words show in each before anything is opened (a browser
    test counts them)."""
    s = steps()
    st = {x["id"]: x for x in s["steps"]}
    inst, ai = st["install"], st["ai"]
    os_ = inst.get("os") or {}
    app = (s.get("addon_routes") or {}).get("app") or {}
    polestar = next((a for a in addons() if a["id"] == "polestar"), {})
    cf = _ext(app["url"], "CurseForge app") if app.get("url") else "CurseForge app"
    ps = _ext(polestar["url"], "Polestar") if polestar.get("url") else "Polestar"
    where = {"mac": "Terminal", "windows": "PowerShell"}
    title = "".join(_os(k, f"Paste this in {where.get(k, v['label'])}", "span") for k, v in os_.items())
    how = "".join(_os(k, escape(v.get("how", "")), "p") for k, v in os_.items())
    new = f"""<section class="hero" data-in="new" hidden>
<h1>Set up wow-stats</h1>
<p class="lead">On the computer you play WoW on. About 5 minutes.</p>
<ol class="setup">
<li class="first"><div class="sh"><h2>{title}</h2>{OS_SWITCH}</div>
<div class="how">{how}</div>
<div class="widget" data-widget="install"></div></li>
<li><h2>Install Polestar</h2><p>In the {cf}: World of Warcraft, then Forever. Search {ps}, click Install.</p></li>
<li><h2>Play</h2><p>Type <kbd>/psrec on</kbd> once, then <kbd>/reload</kbd>. This page turns green by itself.</p></li>
</ol>
</section>"""
    # No address: the friends list gave this person a name that is taken or
    # reserved, or none and their login brought no BattleTag to make one
    # from. Friends never pick one (the owner sets it, 2026-11-19), so the
    # page says whose fix it is and waits (the owner's own page: account.js).
    name = """<section class="hero" data-in="name" hidden>
<h1 class="lamp wait" data-fill="name-head">Your page needs a fix</h1>
<p class="lead" data-fill="name-lead">Your page has no address yet, and the person who runs this site has to
give it a name. Nothing to do on your side: this page carries on by itself once it's fixed.</p>
</section>"""
    wait = """<section class="hero" data-in="wait" hidden>
<h1 class="lamp wait">Installed. Now play</h1>
<p class="lead">In WoW, type <kbd>/psrec on</kbd> once, then <kbd>/reload</kbd>. This turns green within 5 minutes.</p>
</section>"""
    none = """<section class="hero" data-in="none" hidden>
<h1 class="lamp bad">Nothing has arrived yet</h1>
<p class="lead">In WoW, type <kbd>/psrec on</kbd>, then <kbd>/reload</kbd>. This turns green within 5 minutes.</p>
<p><a href="#tr-no-data" data-open="tr-no-data">Still nothing after 10 minutes?</a></p>
</section>"""
    ok = """<section class="hero" data-in="ok" hidden>
<h1 class="lamp good" data-fill="headline">It works</h1>
<p class="lead" data-fill="last"></p>
<p class="warn" data-fill="warn" hidden></p>
<p class="once" data-fill="update-by-hand" hidden></p>
<p class="once" data-fill="update-once" hidden>The program on your computer can't update itself yet.
<a href="#f-update" data-open="f-update">Run the update command once</a>: after that it updates by itself.</p>
<p><a class="btn" href="/">Open my page</a></p>
</section>"""
    labels = {k: v for k, v in os_.items()}
    upd = inst.get("update") or {}
    update = (OS_SWITCH + "<p>The program that sends your data updates itself, within a few hours of a new "
              "version. Installed before it could: run this once. It keeps your settings and updates "
              "Polestar Recorder too.</p>"
              + _cmds({k: fill(v, base) for k, v in upd.items()}, labels)
              + '<div class="widget" data-widget="reset"></div>')
    ai_body = _ai(ai, base)
    trouble = "".join(
        f'<details class="qa" id="tr-{escape(t["id"])}"><summary>{_t(t["title"])}</summary>'
        f'<p>{_t(t["body"])}</p><p>{_t(t["fix"])}</p></details>' for t in s.get("troubleshooting") or [])
    for sid in ("addons", "see"):  # the two step fixes that have no entry of their own
        x = st.get(sid) or {}
        q = {"addons": "The addons aren't in WoW's AddOns list", "see": "My character isn't on my page"}[sid]
        if x.get("if_not"):
            trouble += (f'<details class="qa" id="tr-{sid}"><summary>{escape(q)}</summary>'
                        f'<p>{_t(x["if_not"])}</p></details>')
    un = s.get("uninstall") if isinstance(s.get("uninstall"), dict) else {}
    leave = (OS_SWITCH + "".join(f"<p>{_t(fill(p, base))}</p>" for p in un.get("body") or [])
             + _cmds({k: v.get("command", "") for k, v in (un.get("os") or {}).items()}, labels))
    clips = "".join(f"<h3>{head}</h3>{clip}" for head, clip in (
        ("From the start to your page", _media(s.get("media"), "intro")),
        ("The install command", _media(inst)), ("Uninstalling", _media(un.get("media"), "uninstall")))
        if clip and "empty" not in clip)
    folds = "".join([
        _fold("f-ai", "Connect your AI", ai_body, "wait none ok") if ai_body else "",
        _fold("f-addons", '<span data-fill="addons-sum">The addons</span>',
              '<div class="widget" data-widget="addons"></div>', "new wait none ok"),
        _fold("f-trouble", "Something wrong?", trouble, "new wait none ok"),
        _fold("f-update", "Update, or move to another computer", update, "wait none ok"),
        _fold("f-videos", "Watch the setup", clips, "new wait none ok") if clips else "",
        _fold("f-leave", "Uninstall, or delete my data", leave, "wait none ok"),
    ])
    body = (f'<p class="checking" data-in="loading">Checking your setup</p>{name}{new}{wait}{none}{ok}'
            f'<div class="folds">{folds}</div>'
            '<p class="foot"><a href="/start/print">The whole guide, to print</a></p>')
    return page("Start", body, "start", current="/start")


def settings_page() -> bytes:
    body = """<h1>Settings</h1>
<p class="lead">What others can see of your characters. Your own page always shows everything.</p>
<div class="widget" data-widget="settings"><p class="loading">Reading your settings</p></div>"""
    return page("Settings", body, "settings", current="/settings")


PRIVACY = """<h1>Privacy</h1>
<p class="lead">What wow-stats keeps about you, for how long, and who can see it. Short version: your game
data, for as long as you have an account. You see it on your page. The owner, who runs this server, can
see it too, plus a few logs listed below.</p>
<h2>What is stored</h2>
<ul>
<li>What your addons save in your WoW folder (Polestar, Polestar Recorder and the optional ones you
installed): your characters, their levels, quests, zones, deaths, dungeons, gear, gold, and the auction
prices you scanned. Also the names of other players your game showed you, with when and where: in
groups, nearby in your combat logs if you turned combat logging on, and the ones you targeted. Never their
realm or game id; your combat log names only you and the monsters.</li>
<li>What you or your AI write down: the goals you gave the play analysis (up to 600 characters), what you
asked a plan to work around (up to 300 characters), and the findings the analysis kept about your play.</li>
<li>Your login's id (a number from the login service) and your page's name, set by the owner. No email address, no
password: the login service keeps those.</li>
<li>Battle.net data about your characters (their look, item level, gear, talents), fetched every 2 hours from
Blizzard's public API, for your own characters only: a character name someone else here also sends
gets none, unless it is the owner's. It is deleted after 30 days at most, as Blizzard's API terms
require.</li>
</ul>
<h2>Who sees it</h2>
<ul>
<li>You, on your page, after logging in. The app has no view of other people's data: not for friends,
not for the owner.</li>
<li>Your public page, only if you turn it on (Settings), and your /zogzog/ link, which anyone you give it
to can open. Only what they show: never other players' names, never where your character is now.</li>
<li>The owner, and your AI assistant when you connect it: both below.</li>
<li>Installing and updating wow-stats runs what this server sends on your computer. The program updates
itself from this server, without asking, within a few hours of a new version: you trust the owner the
way you trust anyone whose program you install.</li>
</ul>
<h2>What the owner can see</h2>
<ul>
<li>Everything stored above. The owner runs the server and its database, so they can read any of it.
Nothing in the app shows it to them.</li>
<li>The web server's log. Each time your browser, your AI or the little program that sends your data
asks this server for something, the web server writes a line: the time, your IP address, your browser,
the page asked for (without the part after <code>?</code>) and who you are logged in as. @LOGS@</li>
<li>The app's own log: the page asked for, when your AI logs in (with your login id), the name of an AI
tool that failed, and when you install or update the program, reset your upload password, or get added
or removed (with your account number and page name), and a character's name in a few lines (a picture
asked for, an analysis that failed). No other game content.</li>
<li>The login service. You log in through a service the owner runs or manages. It has your email address,
your name and when you logged in.</li>
<li>The owner's overview page shows, for each person: your page's name, when you last opened your page, used your AI
assistant and sent data (with how many events, the version of the little program that sends it, and
Mac or Windows), how many characters you have (a number, no names), whether your public page is on and
when it was last built, and whether the addons are all there. Never a character's name, a zone, your gold
or anything you played.</li>@VISITS@
</ul>
<h2>Your AI assistant</h2>
<p>Your AI reads your data with your login. This server gets the tool your AI picked (like "show my gear")
and what your AI filled in for it: a character's name, a number of hours, your goals. It never gets your
conversation with your AI. What the tools answer goes back to your AI provider, the names of other players
your game showed you included. Battle.net data goes to it only if "Battle.net data to my AI" is on in
Settings.</p>
<p>@SERVER_AI@</p>
<h2>How long</h2>
<p>As long as you're on the owner's friends list. When the owner removes you, everything stored goes within
minutes: your events, your settings, your goals, plans and findings, your upload password, your pages.
@BACKUPS@ @LOGS_GONE@ Nobody deletes backups or logs by hand: they expire on their own.
Battle.net data about your characters stops updating that day and is gone within 30 days.
Your login account is separate: ask the owner to delete it too.</p>
<p>Your own computer keeps its copy (the WoWArchive folder): uninstalling wow-stats doesn't delete it.</p>
<h2>Blizzard</h2>
<p>Battle.net data comes from Blizzard's API under its Developer API Terms: kept 30 days at most, shown
only to you (and on your public page if you turn it on), never sold or shared. wow-stats is not made by
or affiliated with Blizzard Entertainment.</p>
"""


def _days(v: str | None) -> int | None:
    v = (v or "").strip()
    return int(v) if v.isdigit() else None


def privacy_page(logged_in: bool, env: dict) -> bytes:
    """Served without the login: everything it loads sits under /privacy/
    too (privacy_asset), so the login-free routes stay /privacy and
    /install/ only.

    What only the operator knows comes from the install's settings, never
    as a fact for every install (security review L12): visit counts only
    with PLAUSIBLE_DOMAIN set, how long backups keep events with
    PRIVACY_BACKUP_DAYS, how long the logs keep a request with
    PRIVACY_LOG_DAYS; without those the page says to ask the owner."""
    # The same counting on every wow page: this full view (ui send()), the
    # public page and /zogzog/ (wowstats.publish), all with stats.js.
    visits = ("\n<li>Visit counts (Plausible, on this server's own address). Each page you open here, on your "
              "public page or on your /zogzog/ page sends one pageview: the page's address without the part "
              "after <code>?</code> or <code>#</code>, the site that sent you there when your browser says, "
              "your browser, device type and country. No cookies, nothing about your game.</li>") if (
        env.get("PLAUSIBLE_DOMAIN") or "").strip() else ""
    days = _days(env.get("PRIVACY_BACKUP_DAYS"))
    backups = (f"Backups of your events expire {days} days later." if days is not None else
               "If the owner keeps backups, your events stay in them until those expire: ask the owner how long.")
    log_days = _days(env.get("PRIVACY_LOG_DAYS"))
    logs = (f"These lines are kept {log_days} days." if log_days is not None else
            "Ask the owner how long they keep these lines.")
    logs_gone = (f"Log lines about you expire {log_days} days after each visit." if log_days is not None else
                 "Log lines about you stay until the logs expire: ask the owner how long.")
    from wowstats.analysis import auto

    # The server's own analysis (wowstats.analysis.auto): only when the host configured an AI provider
    # and left ANALYSIS_AUTO on. The timer is the only way a server run starts (no MCP call does), so
    # configured but off sends nothing: review 2 of 0.7.0 found the page said it ran anyway.
    cfg = auto.Config.from_env(env)
    every = f"{cfg.stale_s / 3600:g} hours"
    if cfg.on and cfg.auto:
        server_ai = ("This server also runs the play analysis itself, with the AI provider the host configured, "
                     f"once a character has {every} of play since its analysis was last refreshed (by your own AI "
                     "or the server): what the analysis tools answer about that character (your events, the names "
                     "of other players your game showed you included, never Battle.net data) goes to that "
                     "provider then. Your AI never starts one.")
    elif cfg.on:
        server_ai = ("This server doesn't run the play analysis itself: the host configured an AI provider but "
                     "turned the server's own analysis off, so nothing goes to it from here. If they turn it on, "
                     "this page says so.")
    else:
        server_ai = ("This server doesn't run the play analysis itself: the host hasn't configured an AI provider "
                     "for it, so nothing goes to one from here.")
    body = (PRIVACY.replace("@SERVER_AI@", server_ai).replace("@VISITS@", visits).replace("@BACKUPS@", backups)
            .replace("@LOGS_GONE@", logs_gone).replace("@LOGS@", logs))
    return page("Privacy", body, "privacy", script=False, assets="/privacy/", current="/privacy",
                links=None if logged_in else bar.links("/privacy", LOGGED_OUT))


def refused_page(why: str) -> bytes:
    """A login the app doesn't take: why, in the person's terms, and the way
    out (another account). Styled from the login-free /privacy/ assets: the
    refused person can't load anything else."""
    body = (f'<h1>No access yet</h1><p class="lead">{escape(why)}</p>'
            '<p>Wrong account? <a href="/oauth2/logout">Log out</a> and sign in with another one.</p>')
    return page("No access yet", body, "refused", script=False, assets="/privacy/",
                links=bar.links(items=(("/privacy", "Privacy"),)))


def not_found_page() -> bytes:
    """The full view's 404, for a person's browser (ui's not_found). Styled
    from the login-free /privacy/ assets: some 404s come before the login
    (a stale /install/ link opened in a browser)."""
    body = ('<h1>Nothing here</h1><p class="lead">This address doesn\'t lead to a page. It may be an old link.</p>'
            '<p><a class="btn" href="/">My page</a></p>')
    return page("Not found", body, "notfound", script=False, assets="/privacy/")


def privacy_asset(static: Path, name: str) -> tuple[bytes, str] | None:
    """/privacy/<name>: what the pages before the login load (bar.FILES and
    account.css); None for anything else."""
    if name != "account.css" and name not in bar.FILES:
        return None
    ctype = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml"}.get(
        Path(name).suffix, "font/woff2")
    return (static / name).read_bytes(), ctype


# ---------------------------------------------------------------- status checks


class Status:
    """What arrived for a person: per feed counts, characters, first event.
    Kept 10 s: the start page asks every 10 s."""

    def __init__(self):
        self._c: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def of(self, app, sub: str) -> dict:
        with self._lock:
            hit = self._c.get(sub)
            if hit and time.time() - hit[0] < 10:
                return hit[1]
        st = app.views.stream
        # The shipper's own setup report (wow-setup) is not game data: it
        # arrives at the first run, before anyone played.
        game = f'{st} -feed:="{missing.SETUP_FEED}"'
        feeds = {r.get("feed"): int(float(r.get("n") or 0))
                 for r in app.views.vl(f"{game} | stats by (feed) count() as n, min(_time) as first",
                                       ("feed",), {"n": "sum", "first": "mintime"})}
        span = app.views.vl(f"{game} | stats min(_time) as first, max(_time) as last")
        first = min((r.get("first") for r in span if r.get("first")), default=None)
        # The newest event: "is it working?" on a later visit (the start page's status).
        last = max((r.get("last") for r in span if r.get("last")), default=None)
        played = sum(int(float(r.get("n") or 0)) for r in app.views.vl(
            f'{st} feed:="wow-character" kind:=played | stats count() as n', (), {"n": "sum"}))
        done = {f"quests:{r.get('source')}": int(float(r.get("n") or 0)) for r in app.views.vl(
            f'{st} feed:="wow-quests" | stats by (source) count() as n', ("source",), {"n": "sum"})}
        seen = {**feeds, **done, "played": played}
        out = {"events": sum(feeds.values()), "first_event": first, "last_event": last,
               "feeds": {k: v for k, v in feeds.items() if k},
               "addons": {a["id"]: any(seen.get(f) for f in a["feeds"]) for a in addons() if a["feeds"]},
               "chars": [{"char": c["char"], "level": c.get("level")} for c in app.views.chars()],
               # The shipper's setup report (which addons are installed, the
               # Recorder's switch) and what each character still lacks, with
               # the fix (wowstats.missing): the start page's checklist.
               **missing.for_account(missing.snapshot(lambda q: app.views.vl(f"{st} {q}")))}
        # Data arrives from a shipper whose setup report names no version: one
        # from before the auto-update, which needs the update command once.
        out["update_once"] = bool(out["events"]) and not (out.get("setup") or {}).get("shipper")
        out["update_by_hand"] = update_by_hand(out.get("setup"))
        with self._lock:
            self._c[sub] = (time.time(), out)
        return out


STATUS = Status()

# When this server started serving its shipper. A shipper checks for an
# update every 2 hours after a run (every 6 before 0.7.0), so one that still
# reports an older version 7 hours later didn't take it: `auto_update =
# false` in its config, or an update it refused.
STARTED = time.time()
UPDATE_GRACE_S = 7 * 3600


def update_by_hand(setup: dict | None, started: float | None = None) -> dict | None:
    """{have, server} when the person's shipper is behind this server's and
    should have caught up by now: /start then gives the update command. None
    for a release candidate: that's the owner's test, nobody is asked to
    update by hand for one (and a 0.6 shipper can't read its version)."""
    have = (setup or {}).get("shipper")
    if not vtuple(have):
        return None
    try:
        server = json.loads(shipper_manifest()).get("version")
    except OSError:  # an install without the shipper's files: nothing to offer
        return None
    h, s = vtuple(have), vtuple(server)
    if not (h and s) or h >= s or s[3] == 0:
        return None
    try:
        at = datetime.fromisoformat(str((setup or {}).get("at") or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
    if at < (STARTED if started is None else started) + UPDATE_GRACE_S:
        return None
    return {"have": have, "server": server}


# ---------------------------------------------------------------- dns check (the public host)

_DNS: dict[str, tuple[float, bool]] = {}


def host_live(host: str, doh_url: str) -> bool | None:
    """Whether the host resolves, asked over DNS-over-HTTPS (never the
    cluster's resolver: a lookup before the record exists would cache the
    NXDOMAIN there for an hour). None: no DOH_URL, can't tell."""
    if not doh_url or not host:
        return None
    hit = _DNS.get(host)
    if hit and time.time() - hit[0] < (600 if hit[1] else 120):
        return hit[1]
    try:
        req = urllib.request.Request(f"{doh_url}?{urllib.parse.urlencode({'name': host, 'type': 'A'})}",
                                     headers={"Accept": "application/dns-json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            ok = bool(json.loads(r.read(65536)).get("Answer"))
    except (OSError, ValueError):
        return None
    _DNS[host] = (time.time(), ok)
    return ok


# ---------------------------------------------------------------- api


def me(t: T.Tenancy, u: T.User, app) -> dict:
    """The wizard's and the settings' own data. The store down: no
    characters, the rest still answers (a new person can still install)."""
    try:
        chars = [c["char"] for c in app.views.chars() if not c.get("demo")]
    except Exception as e:  # noqa: BLE001 - the store unreachable, or answering badly
        sys_log(f"me: no characters ({type(e).__name__})")
        chars = []
    host = t.canonical_host(u) if u.slug else ""
    live = host_live(host, t.env.get("DOH_URL", "")) if u.public_on and host else None
    return {"slug": u.slug, "owner": u.owner, "public_on": u.public_on, "bnet_to_model": u.bnet_to_model,
            "models": u.models, "hidden_public": u.hidden_public, "hidden_zogzog": u.hidden_zogzog, "chars": chars,
            "site": f"https://{host}/" if host else None, "site_live": live,
            "zogzog": t.zogzog_link(u) if u.slug else None,
            "mcp_url": f"{t.public_url}/mcp" if t.public_url else None,
            "shipper_set_at": u.ingest_set_at or None, "addons": addons(),
            "update": update_commands(t) if t.public_url else None,
            "domain": t.domain}


def set_settings(t: T.Tenancy, u: T.User, body: dict, chars: list[str]) -> T.User:
    upd: dict = {}
    for k in ("public_on", "bnet_to_model"):
        if k in body:
            if not isinstance(body[k], bool):
                raise T.TenancyError(f"{k}: true or false")
            upd[k] = body[k]
    if "models" in body:
        if body["models"] not in ("hd", "sd"):
            raise T.TenancyError("models: hd or sd")
        upd["models"] = body["models"]
    for k in ("hidden_public", "hidden_zogzog"):
        if k in body:
            v = body[k]
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise T.TenancyError(f"{k}: a list of character names")
            # Only the person's own characters: a name from elsewhere means nothing here.
            upd[k] = sorted({x for x in v if x in chars})
    if body.get("new_zogzog_link") is True:
        upd["zogzog_gen"] = u.zogzog_gen + 1
    if not upd:
        raise T.TenancyError("nothing to change")
    return t.registry.update(u.sub, **upd)


def curl(url: str) -> str:
    """The Mac line's curl: https only, redirects included, when the server
    is on https (client review 2026-11-20); a server on this computer (http
    on 127.0.0.1, tenancy.check_public_url) keeps the plain line."""
    tls = "--proto '=https' --proto-redir '=https' " if url.startswith("https://") else ""
    return f"curl {tls}-fsSL {url} | bash"


def install_commands(t: T.Tenancy, code: str) -> dict:
    url = f"{t.public_url}/install/{code}"
    return {"windows": f"irm {url} | iex", "mac": curl(url), "expires_in": T.CODE_TTL}


def update_commands(t: T.Tenancy) -> dict:
    """The same line for everyone, no code: it reads the install's own config
    and authenticates with its upload password (handle_update)."""
    url = f"{t.public_url}/install/update"
    return {"windows": f"irm {url} | iex", "mac": curl(url)}


# ---------------------------------------------------------------- installers

# The installers' last line, after the checklist (wow_stats.py --preflight).
INSTALLED = ("wow-stats is installed. Fix what the list marks with a cross, then play a bit and log out: "
             "your page fills in within 5 minutes.")


def _toml_str(s: str) -> str:
    return json.dumps(s)


# uv, which runs Python for the shipper, when a friend's machine has none: a
# pinned release checked against its SHA-256 (the release's own .sha256
# files), never a script piped into a shell. Bump the four together.
UV_VERSION = "0.13.9"
UV_SHA256 = {
    "aarch64-apple-darwin": "5aeca67f2283f75235de40d0bd9c0a5bbc5fce23b6d48b3bf9fa1b2db08382ca",
    "x86_64-apple-darwin": "617a3211261b0fc739562f0071aa34e9739797c510208ede8e2977c30903d041",
    "x86_64-pc-windows-msvc": "edd61f7f5728a56cc62455386661860bdfc7bab65872f39b0c25bdcc1d8e1beb",
    "aarch64-pc-windows-msvc": "72eed595983a4d7d22d79f21065b0f4479cc7e5c7a89104bb410c8f47559a878",
}
UV_URL = "https://github.com/astral-sh/uv/releases/download/" + UV_VERSION + "/uv-{target}"

# The config's wow_dir line, as a TOML basic string with \ and " escaped:
# a folder like D:\Jeux d'été\... broke the Windows config (a literal
# string can't hold the apostrophe), and the Mac installer left the line out,
# so the shipper looked in the default folders (client review 2026-11-20, L2).
# tests/test_shipper_robust.py runs the bash one and parses the result.
MAC_WOW_DIR_LINE = r'''printf 'wow_dir = "%s"\n' "$(printf '%s' "$wow" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')"'''
WIN_WOW_DIR_LINE = r'''('wow_dir = "' + $wow.Replace('\', '\\').Replace('"', '\"') + '"' + "`n")'''


def mac_installer(t: T.Tenancy, u: T.User, token: str | None) -> bytes:
    """A bash script with the shipper, the stock installer and the person's
    config inside. The token is written to the config (0600) and never
    printed. WOW_STATS_WOW_DIR overrides the search; WOW_STATS_NO_LOAD=1
    leaves launchd alone (the stock installer's own switch).

    token None: an update. The config (folder, password) stays as it is;
    only the program and the agent are replaced."""
    if token is None:
        return _mac_update(t, u)
    files = {n: shipper_file(n).decode() for n in ("wow_stats.py", "install-mac.sh", "wow-stats.plist",
                                                   "config.example.toml", "LICENSE")}
    for n, body in files.items():
        if "\nWOWSTATS_EOF\n" in "\n" + body + "\n":
            raise RuntimeError(f"{n} holds the heredoc marker")
    heredocs = "\n".join(f"cat > \"$tmp/{n}\" <<'WOWSTATS_EOF'\n{body.rstrip(chr(10))}\nWOWSTATS_EOF"
                         for n, body in files.items())
    ship = (f"[ship]\nurl = {_toml_str(t.public_url + '/ingest')}\nuser = {_toml_str(u.slug)}\n"
            f"password = {_toml_str(token)}\n")
    return f"""#!/bin/bash
# wow-stats installer for {u.slug} ({datetime.now(UTC):%Y-%m-%d}). It works once; get a new one on
# {t.public_url}/start. It installs the shipper for this macOS user only (no admin).
{license_note()}
set -euo pipefail
echo "Installing wow-stats for {u.slug}..."
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
{heredocs}
chmod 755 "$tmp/install-mac.sh"

# Python, through uv: a pinned release for this user when missing, checked
# against its SHA-256.
if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ] && [ ! -x /opt/homebrew/bin/uv ]; then
  echo "Installing uv {UV_VERSION} (runs Python for wow-stats)..."
  if [ "$(uname -m)" = arm64 ]; then
    uvt=aarch64-apple-darwin uvsum={UV_SHA256["aarch64-apple-darwin"]}
  else
    uvt=x86_64-apple-darwin uvsum={UV_SHA256["x86_64-apple-darwin"]}
  fi
  curl -fsSL -o "$tmp/uv.tar.gz" "{UV_URL.format(target="$uvt.tar.gz")}"
  echo "$uvsum  $tmp/uv.tar.gz" | shasum -a 256 -c - >/dev/null \
    || {{ echo "uv's download doesn't match its checksum: stopping, nothing installed." >&2; exit 1; }}
  tar xzf "$tmp/uv.tar.gz" -C "$tmp"
  mkdir -p "$HOME/.local/bin"
  install -m 755 "$tmp/uv-$uvt/uv" "$tmp/uv-$uvt/uvx" "$HOME/.local/bin/"
fi

# The WoW Forever folder.
wow="${{WOW_STATS_WOW_DIR:-}}"
if [ -z "$wow" ]; then
  shopt -s nullglob
  for d in "/Applications/World of Warcraft/_forever_" "$HOME/Applications/World of Warcraft/_forever_" \\
           /Applications/*/_forever_ "/Volumes/"*"/World of Warcraft/_forever_" \\
           "/Volumes/"*"/Applications/World of Warcraft/_forever_"; do
    if [ -d "$d/WTF" ]; then wow="$d"; break; fi
  done
  shopt -u nullglob
fi
if [ -z "$wow" ] || [ ! -d "$wow/WTF" ]; then
  echo "WoW folder not found. Log in to WoW Forever once (so its WTF folder exists), then run this again." >&2
  echo "Still nothing? Send the owner the folder where WoW is installed." >&2
  exit 1
fi

conf="${{WOW_STATS_CONFIG:-$HOME/.config/wow-stats/config.toml}}"
mkdir -p "$(dirname "$conf")"
if [ -f "$conf" ]; then cp -p "$conf" "$conf.bak-$(date +%Y%m%d%H%M%S)"; fi
umask 077
{{
  echo "# wow-stats for {u.slug}, written by the installer. Keep it private: it holds your password."
  {MAC_WOW_DIR_LINE}
  cat <<'WOWSTATS_EOF'
{ship}WOWSTATS_EOF
}} > "$conf"
chmod 600 "$conf"

# Its only output: the checklist (what works, the fix for what doesn't).
WOW_STATS_QUIET=1 WOW_STATS_CONFIG="$conf" "$tmp/install-mac.sh"
echo
echo "{INSTALLED}"
""".encode()


def windows_installer(t: T.Tenancy, u: T.User, token: str | None) -> bytes:
    """The same for PowerShell (irm ... | iex): the files travel as base64,
    the stock install-windows.ps1 does the scheduled task. No `exit`: under
    iex it would close the person's window. token None: an update (the
    config stays)."""
    if token is None:
        return _windows_update(t, u)
    def b64(n: str) -> str:
        return base64.b64encode(shipper_file(n)).decode()

    ship = (f"[ship]\nurl = {_toml_str(t.public_url + '/ingest')}\nuser = {_toml_str(u.slug)}\n"
            f"password = {_toml_str(token)}\n")
    ship_b64 = base64.b64encode(ship.encode()).decode()
    return f"""# wow-stats installer for {u.slug} ({datetime.now(UTC):%Y-%m-%d}). It works once; get a new one on
# {t.public_url}/start. It installs the shipper for this Windows user only (no admin).
{license_note()}
& {{
$ErrorActionPreference = 'Stop'
Write-Host "Installing wow-stats for {u.slug}..."
$tmp = Join-Path $env:TEMP ("wow-stats-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
try {{
  function Put($name, $b64) {{ [IO.File]::WriteAllBytes((Join-Path $tmp $name), [Convert]::FromBase64String($b64)) }}
  Put 'wow_stats.py' '{b64("wow_stats.py")}'
  Put 'install-windows.ps1' '{b64("install-windows.ps1")}'
  Put 'config.example.toml' '{b64("config.example.toml")}'
  Put 'LICENSE' '{b64("LICENSE")}'

  $uvExe = Join-Path $env:USERPROFILE '.local\\bin\\uv.exe'
  if (-not (Get-Command uv -ErrorAction SilentlyContinue) -and -not (Test-Path $uvExe)) {{
    Write-Host 'Installing uv {UV_VERSION} (runs Python for wow-stats)...'
    # A pinned release, checked against its SHA-256: no script runs.
    $uvSums = @{{
      'aarch64-pc-windows-msvc' = '{UV_SHA256["aarch64-pc-windows-msvc"]}'
      'x86_64-pc-windows-msvc' = '{UV_SHA256["x86_64-pc-windows-msvc"]}'
    }}
    $uvT = 'x86_64-pc-windows-msvc'
    if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {{ $uvT = 'aarch64-pc-windows-msvc' }}
    $uvZip = Join-Path $tmp 'uv.zip'
    $uvUrl = '{UV_URL.format(target="")}' + $uvT + '.zip'
    # Windows PowerShell's progress bar makes a 30 MB download take minutes.
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -UseBasicParsing -Uri $uvUrl -OutFile $uvZip
    if ((Get-FileHash $uvZip -Algorithm SHA256).Hash -ne $uvSums[$uvT]) {{
      throw "uv's download doesn't match its checksum: nothing installed"
    }}
    $uvDir = Split-Path $uvExe -Parent
    New-Item -ItemType Directory -Force -Path $uvDir | Out-Null
    Expand-Archive -Path $uvZip -DestinationPath $uvDir -Force
  }}

  $wow = $env:WOW_STATS_WOW_DIR
  if (-not $wow) {{
    $cands = @()
    foreach ($k in 'HKLM:\\SOFTWARE\\WOW6432Node\\Blizzard Entertainment\\World of Warcraft',
                   'HKLM:\\SOFTWARE\\Blizzard Entertainment\\World of Warcraft') {{
      $p = (Get-ItemProperty -Path $k -ErrorAction SilentlyContinue).InstallPath
      if ($p) {{ $cands += (Join-Path (Split-Path ($p.TrimEnd('\\')) -Parent) '_forever_') }}
    }}
    foreach ($d in (Get-PSDrive -PSProvider FileSystem).Root) {{
      foreach ($sub in 'Program Files (x86)\\World of Warcraft', 'Program Files\\World of Warcraft',
                       'World of Warcraft', 'Games\\World of Warcraft', 'Battle.net\\World of Warcraft',
                       'Blizzard\\World of Warcraft') {{
        $cands += (Join-Path (Join-Path $d $sub) '_forever_')
      }}
    }}
    $wow = $cands | Where-Object {{ Test-Path (Join-Path $_ 'WTF') }} | Select-Object -First 1
  }}
  if (-not $wow -or -not (Test-Path (Join-Path $wow 'WTF'))) {{
    throw ('WoW folder not found. Log in to WoW Forever once (so its WTF folder exists), then run this ' +
           'again. Still nothing? Send the owner the folder where WoW is installed.')
  }}

  $confDir = Join-Path $env:APPDATA 'wow-stats'
  $conf = Join-Path $confDir 'config.toml'
  New-Item -ItemType Directory -Force -Path $confDir | Out-Null
  if (Test-Path $conf) {{ Copy-Item $conf ($conf + '.bak-' + (Get-Date -Format 'yyyyMMddHHmmss')) }}
  $ship = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{ship_b64}'))
  $text = "# wow-stats for {u.slug}, written by the installer. Keep it private: it holds your password.`n" +
          {WIN_WOW_DIR_LINE} + $ship
  [IO.File]::WriteAllText($conf, $text, (New-Object Text.UTF8Encoding $false))

  $env:Path = (Join-Path $env:USERPROFILE '.local\\bin') + ';' + $env:Path
  # Its only output: the checklist (what works, the fix for what doesn't).
  $env:WOW_STATS_QUIET = '1'
  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $tmp 'install-windows.ps1')
  if ($LASTEXITCODE) {{ throw "the installer stopped (code $LASTEXITCODE): see the list above" }}
  Write-Host ''
  Write-Host '{INSTALLED}'
}} finally {{
  Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
  Remove-Item Env:WOW_STATS_QUIET -ErrorAction SilentlyContinue
}}
}}
""".encode()


def _shipper_heredocs(names) -> str:
    files = {n: shipper_file(n).decode() for n in names}
    for n, body in files.items():
        if "\nWOWSTATS_EOF\n" in "\n" + body + "\n":
            raise RuntimeError(f"{n} holds the heredoc marker")
    return "\n".join(f"cat > \"$tmp/{n}\" <<'WOWSTATS_EOF'\n{body.rstrip(chr(10))}\nWOWSTATS_EOF"
                     for n, body in files.items())


def _mac_update(t: T.Tenancy, u: T.User) -> bytes:
    """The update payload (Mac): the new program and agent, the config untouched."""
    heredocs = _shipper_heredocs(("wow_stats.py", "install-mac.sh", "wow-stats.plist", "config.example.toml",
                                  "LICENSE"))
    return f"""#!/bin/bash
# wow-stats update for {u.slug} ({datetime.now(UTC):%Y-%m-%d}): the program and the agent; the config stays.
{license_note()}
set -euo pipefail
conf="${{WOW_STATS_CONFIG:-$HOME/.config/wow-stats/config.toml}}"
echo "Updating wow-stats for {u.slug}..."
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
{heredocs}
chmod 755 "$tmp/install-mac.sh"
WOW_STATS_QUIET=1 WOW_STATS_CONFIG="$conf" "$tmp/install-mac.sh"
echo
echo "wow-stats is updated. Your config and upload password are unchanged ($conf)."
""".encode()


def _windows_update(t: T.Tenancy, u: T.User) -> bytes:
    def b64(n: str) -> str:
        return base64.b64encode(shipper_file(n)).decode()

    return f"""# wow-stats update for {u.slug} ({datetime.now(UTC):%Y-%m-%d}): program and task; config kept.
{license_note()}
& {{
$ErrorActionPreference = 'Stop'
Write-Host "Updating wow-stats for {u.slug}..."
$tmp = Join-Path $env:TEMP ("wow-stats-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
try {{
  function Put($name, $b64) {{ [IO.File]::WriteAllBytes((Join-Path $tmp $name), [Convert]::FromBase64String($b64)) }}
  Put 'wow_stats.py' '{b64("wow_stats.py")}'
  Put 'install-windows.ps1' '{b64("install-windows.ps1")}'
  Put 'config.example.toml' '{b64("config.example.toml")}'
  Put 'LICENSE' '{b64("LICENSE")}'
  $env:Path = (Join-Path $env:USERPROFILE '.local\\bin') + ';' + $env:Path
  $env:WOW_STATS_QUIET = '1'
  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $tmp 'install-windows.ps1')
  if ($LASTEXITCODE) {{ throw "the installer stopped (code $LASTEXITCODE): see the list above" }}
  Write-Host ''
  Write-Host 'wow-stats is updated. Your config and upload password are unchanged.'
}} finally {{
  Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
  Remove-Item Env:WOW_STATS_QUIET -ErrorAction SilentlyContinue
}}
}}
""".encode()


MAC_UPDATE = r"""#!/bin/bash
# wow-stats update: keeps your config and upload password. A first install needs the
# command on your start page (@START@).
set -euo pipefail
conf="${WOW_STATS_CONFIG:-$HOME/.config/wow-stats/config.toml}"
if [ ! -f "$conf" ]; then
  echo "No wow-stats install found ($conf). A first install needs the command on your start page:" >&2
  echo "  @START@" >&2
  exit 1
fi
val() { sed -n "s/^$1 = \"\(.*\)\"\$/\1/p" "$conf" | head -n 1; }
user=$(val user); pass=$(val password); base=$(val url); base=${base%/ingest}
if [ -z "$user" ] || [ -z "$pass" ] || [ -z "$base" ]; then
  echo "Your config ($conf) has no [ship] user, password and url: run the install command from your start page." >&2
  exit 1
fi
# The password goes to curl on stdin (--config -), never on a command line.
if ! script=$(printf 'user = "%s:%s"\n' "$user" "$pass" | curl -fsS --config - "$base/install/update/payload"); then
  echo "The server refused the update. If you reset your upload password, run the new install command" >&2
  echo "from your start page (@START@) instead." >&2
  exit 1
fi
printf '%s\n' "$script" | bash
"""


def mac_update_bootstrap(t: T.Tenancy) -> bytes:
    """GET /install/update (curl): public and the same for everyone. Reads the
    install's config and fetches the payload with its own upload password."""
    return MAC_UPDATE.replace("@START@", f"{t.public_url}/start").encode()


def windows_update_bootstrap(t: T.Tenancy) -> bytes:
    return f"""# wow-stats update: keeps your config and upload password. A first install needs the
# command on your start page ({t.public_url}/start).
& {{
$ErrorActionPreference = 'Stop'
$conf = Join-Path $env:APPDATA 'wow-stats\\config.toml'
if (-not (Test-Path $conf)) {{
  throw "No wow-stats install found ($conf). A first install needs the command on your start page: {t.public_url}/start"
}}
$text = Get-Content -Raw $conf
function Val($k) {{ [regex]::Match($text, '(?m)^' + $k + ' = "(.*)"\\s*$').Groups[1].Value }}
$user = Val 'user'; $pass = Val 'password'; $base = (Val 'url') -replace '/ingest$', ''
if (-not $user -or -not $pass -or -not $base) {{
  throw "Your config ($conf) has no [ship] user/password/url: run the install command from your start page."
}}
$auth = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($user + ':' + $pass))
try {{
  $script = Invoke-RestMethod -Uri ($base + '/install/update/payload') -UserAgent 'WindowsPowerShell wow-stats' `
              -Headers @{{ Authorization = 'Basic ' + $auth }}
}} catch {{
  throw ("The server refused the update. If you reset your upload password, run the new install " +
         "command from your start page ({t.public_url}/start) instead.")
}}
Invoke-Expression $script
}}
""".encode()


def wants_powershell(ua: str) -> bool:
    return bool(re.search(r"PowerShell", ua or "", re.I))


# ---------------------------------------------------------------- routing (called by wowstats.ui)


def basic_auth(h) -> tuple[str, str]:
    raw = h.headers.get("Authorization") or ""
    if not raw.lower().startswith("basic "):
        return "", ""
    try:
        user, _, pw = base64.b64decode(raw[6:].strip()).decode("utf-8").partition(":")
    except (ValueError, UnicodeDecodeError):
        return "", ""
    return user, pw


def handle_update(h, t: T.Tenancy, payload: bool) -> None:
    """GET /install/update: the bootstrap, public. GET /install/update/payload:
    the program, for an install whose upload password (basic auth) is valid."""
    ps = wants_powershell(h.headers.get("User-Agent", ""))
    if not payload:
        return h.send(200, windows_update_bootstrap(t) if ps else mac_update_bootstrap(t), "text/plain; charset=utf-8")
    user, pw = basic_auth(h)
    u = t.registry.check_ingest(user, pw) if user else None
    if u is None:
        return h.send(401, b"unknown install\n", "text/plain; charset=utf-8")
    sys_log(f"update fetched by account {u.account_id}")
    t.activity.installed(u, "Windows" if ps else "macOS", __version__)
    return h.send(200, windows_installer(t, u, None) if ps else mac_installer(t, u, None), "text/plain; charset=utf-8")


# The two clients the install commands run (curl on a Mac, irm in PowerShell).
INSTALL_CLIENT = re.compile(r"^curl/|PowerShell", re.I)
NOT_A_TERMINAL = (b"# wow-stats installer. It runs from a terminal: paste the whole command from your start\n"
                  b"# page into Terminal (Mac) or PowerShell (Windows).\nexit 1\n")


def handle_install(h, t: T.Tenancy, code: str) -> None:
    """GET /install/<code>: no login (curl and irm carry no cookie); the code
    is the credential, used once. /install/update[/payload]: handle_update.

    Only curl and PowerShell redeem it, and never with HEAD: a code pasted in
    a chat is fetched by the chat's link preview (Slack, Discord, iMessage)
    and a link checker sends HEAD, and either used to burn the code and take
    the fresh ingest token with it (security review L3). They get a note
    instead. The command lines stay what they were."""
    if code in ("update", "update/payload"):
        return handle_update(h, t, code == "update/payload")
    if h.command != "GET" or not INSTALL_CLIENT.search(h.headers.get("User-Agent", "")):
        return h.send(200, NOT_A_TERMINAL, "text/plain; charset=utf-8")
    hit = t.registry.redeem(code)
    if hit is None:
        msg = "echo 'This install code expired or was used already: get a new one on your start page.' >&2; exit 1"
        if wants_powershell(h.headers.get("User-Agent", "")):
            msg = "throw 'This install code expired or was used already: get a new one on your start page.'"
        return h.send(404, (msg + "\n").encode(), "text/plain; charset=utf-8")
    u, token = hit
    sys_log(f"install code used by account {u.account_id}")
    t.activity.installed(u, "Windows" if wants_powershell(h.headers.get("User-Agent", "")) else "macOS", __version__)
    if wants_powershell(h.headers.get("User-Agent", "")):
        return h.send(200, windows_installer(t, u, token), "text/plain; charset=utf-8")
    return h.send(200, mac_installer(t, u, token), "text/plain; charset=utf-8")


def sys_log(msg: str) -> None:
    import sys

    sys.stdout.write(f"tenancy: {msg}\n")
    sys.stdout.flush()


def json_body(h) -> dict | None:
    if not (h.headers.get("Content-Type") or "").lower().startswith("application/json"):
        return None
    raw = h.read_body(16384)
    if raw is None:
        return None
    try:
        v = json.loads(raw or b"{}")
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def route(h, t: T.Tenancy, u: T.User, app, path: str) -> bool:
    """The person's own pages and API; False when the path isn't one."""
    get = h.command in ("GET", "HEAD")
    if path in ("/start", "/start/") and get:
        h.send(200, start_page(base=t.public_url), "text/html; charset=utf-8", "no-cache")
        return True
    if path == "/start/print" and get:
        h.send(200, start_page(printable=True, base=t.public_url), "text/html; charset=utf-8", "no-cache")
        return True
    if path.startswith("/start/media/") and get:
        name = Path(urllib.parse.unquote(path.rsplit("/", 1)[1])).name
        media = _media_dir().resolve()
        f = (media / name).resolve()
        # A file directly in the media folder, nothing else (no dotfile, no
        # link out of it).
        if not name or name.startswith(".") or f.parent != media or not f.is_file():
            h.not_found()
            return True
        send_media(h, f.read_bytes(), MEDIA_TYPES.get(f.suffix, "application/octet-stream"))
        return True
    if path == "/help" and get:
        h.send(301, b"", "text/plain", headers={"Location": "/help/"})
        return True
    if path == "/help/" and get:
        try:
            from wowstats.mcp import helppage
        except ImportError:  # an install without the mcp extra: no help page
            h.not_found()
            return True
        h.send(200, helppage.full_page(f"{t.public_url}/mcp" if t.public_url else ""),
               "text/html; charset=utf-8", "no-cache")
        return True
    if path in ("/help/help.js", "/help/help.css") and get:
        try:
            from wowstats.mcp import helppage
        except ImportError:
            h.not_found()
            return True
        if path.endswith(".js"):
            h.send(200, helppage.script().encode(), "text/javascript; charset=utf-8", "no-cache")
        else:
            h.send(200, helppage.stylesheet().encode(), "text/css; charset=utf-8", "no-cache")
        return True
    if path == "/settings" and get:
        h.send(200, settings_page(), "text/html; charset=utf-8", "no-cache")
        return True
    if path in ("/account.css", "/account.js") and get:
        name = path[1:]
        ctype = "text/css; charset=utf-8" if name.endswith(".css") else "text/javascript; charset=utf-8"
        h.send(200, (app.static / name).read_bytes(), ctype, "no-cache")
        return True
    if path == "/api/me" and get:
        h.json(200, me(t, u, app))
        return True
    if path == "/api/me/status" and get:
        h.json(200, STATUS.of(app, u.sub))
        return True
    if path in ("/admin", "/admin/", "/api/admin/overview") and get:
        from wowstats import admin  # the owner's overview: 404 for anyone else

        admin.route(h, t, u, app, path)
        return True
    if path == "/api/admin/hosts" and get:
        if not u.owner:
            h.not_found()
        else:
            h.json(200, {"hosts": t.host_list()})
        return True
    if path.startswith("/api/me/") and h.command == "POST":
        body = json_body(h)
        if body is None:
            h.json(415, {"error": "send JSON (Content-Type: application/json)"})
            return True
        try:
            if path == "/api/me/install-code":
                if not u.slug:  # a taken or reserved name, or none at all
                    raise T.TenancyError(f"your page's address needs a fix from {T.RUNS_IT} first")
                if not t.public_url:
                    raise T.TenancyError("this install has no PUBLIC_URL: ask the owner")
                if u.ingest_sha256 and body.get("reset") is not True:
                    raise T.TenancyError("wow-stats is installed already: run the update command, or reset your "
                                         "upload password")
                if u.ingest_sha256:  # a deliberate reset: the old password stops now
                    t.registry.update(u.sub, ingest_sha256="", ingest_set_at="")
                    sys_log(f"upload password reset by account {u.account_id}")
                h.json(200, install_commands(t, t.registry.new_code(u.sub)))
            elif path == "/api/me/settings":
                chars = [c["char"] for c in app.views.chars()]
                nu = set_settings(t, u, body, chars)
                h.json(200, {"ok": True, "public_on": nu.public_on, "bnet_to_model": nu.bnet_to_model,
                             "models": nu.models,
                             "hidden_public": nu.hidden_public, "hidden_zogzog": nu.hidden_zogzog,
                             "zogzog": t.zogzog_link(nu)})
            else:
                h.not_found()
        except T.TenancyError as e:
            h.json(400, {"error": str(e)})
        return True
    return False


RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def send_media(h, body: bytes, ctype: str) -> None:
    """A clip, with byte ranges: Safari plays a <video> only from a server
    that answers Range with 206 (one range; anything else gets the whole file)."""
    cache = "public, max-age=3600"
    m = RANGE.match((h.headers.get("Range") or "").strip())
    n = len(body)
    if m and (m.group(1) or m.group(2)):
        if m.group(1):
            a = int(m.group(1))
            b = min(int(m.group(2)), n - 1) if m.group(2) else n - 1
        else:  # bytes=-N: the last N bytes
            a, b = max(0, n - int(m.group(2))), n - 1
        if a >= n or a > b:
            h.send(416, b"", ctype, cache, headers={"Content-Range": f"bytes */{n}", "Accept-Ranges": "bytes"})
            return
        h.send(206, body[a:b + 1], ctype, cache,
               headers={"Content-Range": f"bytes {a}-{b}/{n}", "Accept-Ranges": "bytes"})
        return
    h.send(200, body, ctype, cache, headers={"Accept-Ranges": "bytes"})


def _media_dir() -> Path:
    for p in (_HERE / "data" / "start-media", _HERE.parent / "docs" / "videos" / "media"):
        if p.is_dir():
            return p
    return _HERE / "data" / "start-media"
