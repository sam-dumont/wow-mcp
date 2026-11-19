"""The static site's two short pages, in the page's look (web/notice.css,
the same fonts and colours): a person's public page before their first
character arrives (the publisher writes it as the bundle's index.html), and
the 404 the static server answers for anything else.

Neither carries a name, a setting or anything about the account: the empty
page reads the same for every person, public page on or off, and an unknown
host gets the same 404 as a missing file."""

from __future__ import annotations

from wowstats import ui

CSS = "notice.css"
FONTS = ("fonts/cinzel.woff2", "fonts/alegreya-sans-400.woff2")
# Where the static server serves the 404's stylesheet and fonts for any host
# (an unknown one has no bundle to take them from).
ASSETS = "/_notice/"

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta name="color-scheme" content="dark light">
<meta name="theme-color" content="#16110C" media="(prefers-color-scheme: dark)">
<meta name="theme-color" content="#EFE3C6" media="(prefers-color-scheme: light)">
<meta property="og:type" content="website">
<meta property="og:title" content="Leveling log">
<title>{title}</title>
<link rel="stylesheet" href="{base}notice.css">
</head>
<body>
<main class="notice">
<svg class="crest" viewBox="0 0 32 32" aria-hidden="true">
<path class="shield" d="M4 3h24v12c0 8-6 13-12 15C10 28 4 23 4 15z"/>
<path class="mark" d="M9 18l7-7 7 7"/></svg>
{body}
</main>
</body>
</html>
"""


def empty_page() -> bytes:
    """A person's page while they have no character yet."""
    body = ("<h1>No character yet</h1>\n"
            "<p>Once you play with wow-stats installed and log out or type <code>/reload</code>, "
            "your page appears here within a few minutes.</p>\n"
            '<p class="muted">Public page turned off in Settings? Then it shows at your zogzog link only.</p>')
    return PAGE.format(title="Leveling log", base="", body=body).encode()


def not_found_page() -> bytes:
    body = "<h1>Nothing here</h1>\n<p>This address doesn't lead to a page.</p>"
    return PAGE.format(title="Not found", base=ASSETS, body=body).encode()


def asset(rel: str) -> bytes | None:
    """notice.css or one of its fonts, from the app's own web/ folder."""
    if rel != CSS and rel not in FONTS:
        return None
    return (ui.static_dir() / rel).read_bytes()


def write_empty(b) -> None:
    """The empty page into a bundle (wowstats.publish.Bundle), with its
    stylesheet and fonts."""
    b.put("index.html", empty_page())
    for rel in (CSS, *FONTS):
        b.put(rel, asset(rel))
