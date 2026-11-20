"""The one header every page carries, and the files every page loads
(web/base.css, web/theme.js, web/fonts): the character pages (web/index.html,
live and in the static bundles), the person's own pages (wowstats.account),
the help page (wowstats.mcp.helppage), the owner's overview and the static
site's short pages (wowstats.notice).

The crest and name always lead home: the full view's / for a logged-in
reader, the bundle's own page on a static site. The person's pages follow
only where the reader can open them: never on the public page or /zogzog/,
which link to nothing behind the login (owner, 2026-11-20: "each page has a
different header, like go on privacy, you get no link back")."""

from __future__ import annotations

import re
from html import escape

# Every file a page loads from these, relative to web/: one list for the
# full view's routes, /privacy/ (before the login), /_notice/ and the bundles.
FONTS = tuple(f"fonts/{n}.woff2" for n in (
    "cinzel", "alegreya", "alegreya-italic", "alegreya-sans-400", "alegreya-sans-500", "alegreya-sans-700"))
FILES = ("base.css", "theme.js", "favicon.svg", *FONTS)
# The tab icon on the full view: under /privacy/, the one prefix a login
# proxy lets through before the login (the lab's Traefik did answer 401 to
# /favicon.ico on every page, 2026-11-20). The app also answers /favicon.ico
# and /favicon.svg before any login (wowstats.ui), for the browsers that ask
# anyway.
ICON = "/privacy/favicon.svg"

# The logged-in reader's pages, in the bar of every full view page.
NAV = (("/", "My page"), ("/start", "Start"), ("/settings", "Settings"), ("/help/", "Help"), ("/privacy", "Privacy"))

CREST = ('<svg class="bar-crest" viewBox="0 0 32 32" aria-hidden="true">'
         '<path class="shield" d="M4 3h24v12c0 8-6 13-12 15C10 28 4 23 4 15z"/>'
         '<path class="mark" d="M9 18l7-7 7 7"/></svg>')
THEME = '<button id="theme" class="theme" type="button" aria-label="Switch colour theme">Auto</button>'

BLOCK = re.compile(r"<!--bar[^>]*-->\n?(.*?)<!--/bar-->", re.S)


def links(current: str = "", items=NAV) -> str:
    """The bar's links; the page you're on is marked (aria-current)."""
    return "".join(f'<a href="{escape(h)}"' + (' aria-current="page"' if h == current else "") + f">{escape(t)}</a>"
                   for h, t in items)


def header(home: str, nav: str = "", tools: str = "", theme: bool = True) -> str:
    """The bar: home, the character and tabs (`tools`, character pages
    only), the reader's pages (`nav`, from links()) and the theme switch
    (pages that load theme.js)."""
    return ('<header class="bar">'
            f'<a class="bar-home" href="{escape(home)}">{CREST}<span>wow-stats</span></a>'
            f'{tools}'
            + (f'<nav class="bar-nav" aria-label="Your pages">{nav}</nav>' if nav else "")
            + (THEME if theme else "") + "</header>")


def head(base: str, *sheets: str, script: bool = True, icon: str | None = None) -> str:
    """The icon, the <link>s and the theme script every page's <head> starts
    with: base.css, then the page's own sheets. `base`: where FILES are
    served from, relative or absolute, ending in /; `icon`: the tab icon's
    address when not there (ICON on the full view)."""
    return "".join([f'<link rel="icon" href="{icon or base + "favicon.svg"}" type="image/svg+xml">',
                    f'<link rel="stylesheet" href="{base}base.css">',
                    *(f'<link rel="stylesheet" href="{s}">' for s in sheets),
                    f'<script src="{base}theme.js"></script>' if script else ""])


def fill(html: str, home: str, nav: str = "") -> str:
    """web/index.html's <!--bar-->...<!--/bar--> block (the character and
    the tabs) inside the bar."""
    return BLOCK.sub(lambda m: header(home, nav, m.group(1)), html, count=1)


def write(bundle, web) -> None:
    """FILES into a static bundle (wowstats.publish.Bundle), from web/."""
    for rel in FILES:
        bundle.put(rel, (web / rel).read_bytes())
