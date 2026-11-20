"""FastMCP's consent page, in wow-stats' look, with a Battle.net button.

The OIDC proxy (wowstats.mcp.server) shows FastMCP's consent page before it
sends the person to the login. Two changes, both on the page FastMCP has
already built (its state, CSRF token and cookies stay FastMCP's own):

- the look: wow-stats' night and day palette (web/app.css), the short
  pages' panel (web/notice.css) and button (web/account.css). Fonts fall
  back to the system's: the page's CSP has no font-src, and stays as is.
- with OIDC_BATTLENET_ORG_ID and OIDC_BATTLENET_IDP_ID set, a "Sign in with
  Battle.net" button. It submits the same consent form (same transaction,
  same CSRF token) to /consent?login=battlenet; the server then adds the two
  ZITADEL scopes from its own config to that one authorize request, which
  sends the person straight to Battle.net. Without the two settings there is
  no button and the query is ignored.

If FastMCP's markup ever changes so the anchors below aren't found, the page
is served as FastMCP built it (no button, its own look) and the server logs
it: the normal sign-in keeps working.
"""

from __future__ import annotations

# The query the Battle.net button posts to. Nothing else from the request
# reaches the scopes: they come from the server's config only.
LOGIN_PARAM, BATTLENET = "login", "battlenet"

_APPROVE = '<button type="submit" name="action" value="approve" class="btn-approve">'
_BUTTONS_END = "</form>"

BATTLENET_BUTTON = (
    '<div class="wow-bnet">'
    f'<button type="submit" name="action" value="approve" class="btn-bnet" formaction="?{LOGIN_PARAM}={BATTLENET}">'
    "Sign in with Battle.net</button></div>"
)

# The tokens are web/app.css's, by name and value; the panel and the button
# are notice.css's and account.css's. Day by the system's setting: no script
# may run here to read the full view's saved choice.
CSS = """
:root {
  --display: "Cinzel", "Trajan Pro", "Times New Roman", serif;
  --sans: "Alegreya Sans", "Gill Sans", "Segoe UI", system-ui, sans-serif;
  --page: #16110C; --panel: #251B13; --raised: #31241A; --rule: #4A3826; --frame: #6B5032;
  --gold: #C99A3B; --gold-hi: #F0CF7A; --ink: #EFE4CA; --muted: #B09C7A; --focus: #F0CF7A;
  color-scheme: dark;
}
@media (prefers-color-scheme: light) {
  :root {
    --page: #E6D5AE; --panel: #F1E4C4; --raised: #EADAB4; --rule: #C9AF7C; --frame: #A9874F;
    --gold: #704D0F; --gold-hi: #6E4A0B; --ink: #2C1F12; --muted: #675032; --focus: #8A5F12;
    color-scheme: light;
  }
}
body { background: var(--page); color: var(--ink); font: 17px/1.55 var(--sans); padding: 24px 16px; }
.container { background: var(--panel); border: 1px solid var(--rule); outline: 1px solid var(--frame);
  outline-offset: 4px; border-radius: 4px; box-shadow: none; margin: 0; padding: 32px 24px; max-width: 30rem; }
.logo { display: none; }
h1 { font: 700 24px/1.2 var(--display); color: var(--gold-hi); letter-spacing: .02em; margin: 0 0 16px; }
.info-box, .redirect-section, .detail-box, .cimd-badge { background: var(--raised); border: 1px solid var(--rule);
  color: var(--ink); border-radius: 8px; }
.redirect-section .label, .detail-label, details summary, .help-link { color: var(--muted); }
.redirect-section .value, .detail-value { color: var(--ink); }
.info-box strong, a, .tooltip-link { color: var(--gold-hi); }
.tooltip, .help-link { background: var(--raised); color: var(--muted); border: 1px solid var(--rule);
  box-shadow: none; }
.button-group { flex-wrap: wrap; }
button { font: 700 17px/1.3 var(--sans); border-radius: 8px; padding: 12px 20px; }
button:hover { transform: none; box-shadow: none; }
.btn-approve, .btn-bnet { background: var(--gold); color: var(--page); border: 1px solid var(--gold); }
.btn-approve:hover, .btn-bnet:hover { background: var(--gold-hi); border-color: var(--gold-hi); }
.btn-deny { background: transparent; color: var(--ink); border: 1px solid var(--frame); }
.btn-deny:hover { border-color: var(--gold); }
button:focus-visible, a:focus-visible { outline: 2px solid var(--focus); outline-offset: 3px; }
.wow-bnet { margin-top: 16px; }
.wow-bnet .btn-bnet { display: block; width: 100%; }
"""


def restyle(html: str, battlenet: bool) -> tuple[str, bool]:
    """The page in wow-stats' look, plus the Battle.net button when
    `battlenet`. Returns the page and whether every anchor was found (when
    not, the page comes back unchanged)."""
    if "</head>" not in html or (battlenet and (_APPROVE not in html or _BUTTONS_END not in html)):
        return html, False
    out = html.replace("</head>", f"<style>{CSS}</style></head>", 1)
    if battlenet:
        # Inside the consent form, after FastMCP's two buttons: the same
        # hidden txn_id and csrf_token go with it.
        out = out.replace(_BUTTONS_END, BATTLENET_BUTTON + _BUTTONS_END, 1)
    return out, True
