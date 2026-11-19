"""/help/#<key>: the MCP help page, generated from the registry, sealed on
the static site under its own key."""

from __future__ import annotations

import html
import io
import json
import re
from contextlib import redirect_stdout
from html.parser import HTMLParser
from pathlib import Path

import pytest

from tests.test_public_view import SECRETS, PoisonStore
from wowstats import publish, static, ui
from wowstats.mcp import helppage, prompts, tools
from wowstats.plan import facts

pytest.importorskip("cryptography")
from cryptography.exceptions import InvalidTag  # noqa: E402

URL = "https://wow.example/mcp"


def test_the_ai_part_is_short_and_leaves_the_reference_to_the_server():
    """A friend reads it once to connect their AI: no tool cards, no
    arguments (the AI reads those from the server), no provenance."""
    page = helppage.render(URL)
    ai = page[page.index("id='ai'"):page.index("id='trouble'")]
    for t in tools.TOOLS:
        assert t.description.strip(), f"{t.name} has no description"
    for p in prompts.PROMPTS:
        assert p.get("description"), f"prompt {p['name']} has no description"
        assert f"<code>/{p['name']}</code>" in ai  # named once, for the + menu and slash commands
    assert "class='tname'" not in ai and "class='args'" not in ai and "class='tool'" not in ai
    assert "market" not in [t.name for t in tools.TOOLS]  # Battle.net market: only behind the person's setting
    assert "Battle.net data to my AI" in ai
    for provenance in ("30-day", "Polestar Recorder", "Syndicator", "Auctionator", "Blizzard"):
        assert provenance not in ai, provenance
    assert f"data-copy='claude mcp add --transport http wow {URL}'" in page
    assert "loopback" in page and "OAuth" in page
    assert "bearer" not in page.lower() and "<script" not in page  # OAuth only; the shell holds the only script
    assert "<table" not in page  # nothing wider than a phone
    assert len(re.sub(r"<[^>]+>", " ", ai[:ai.index("Its address")]).split()) < 30  # the intro stays short


def test_every_argument_has_its_own_meaning_in_the_schema():
    """The schema is what the AI reads: every argument says what it means
    there, in words written for that tool (a shared fallback once told
    market's faction it picked a levelling route)."""
    for t in tools.TOOLS + (tools.MARKET,):
        for name, spec in (t.input_schema.get("properties") or {}).items():
            assert (spec.get("description") or "").strip(), f"{t.name}.{name}: no description"
    for t in tools.TOOLS + (tools.MARKET,):
        if t.name != "route_reference":
            for name in ("faction", "race", "class", "level"):
                spec = (t.input_schema.get("properties") or {}).get(name)
                assert not spec or "route" not in spec["description"], f"{t.name}.{name}"
    assert "auction" in tools.MARKET.input_schema["properties"]["faction"]["description"]


def test_every_tool_has_one_group_and_every_ask_names_real_things():
    grouped = [n for _, _, names in helppage.GROUPS for n in names]
    assert sorted(grouped) == sorted(t.name for t in tools.TOOLS), "a tool without a group, or in two"
    known = {t.name for t in tools.TOOLS} | {p["name"] for p in prompts.PROMPTS}
    assert len(helppage.ASKS) == 3  # three, one line each: the rest the AI suggests itself
    for q, uses in helppage.ASKS:
        assert uses and set(uses) <= known, q
        assert len(q) <= 70 and "\n" not in q, q  # one short line
    page = helppage.render(URL)
    for q, _ in helppage.ASKS:
        assert f"data-copy='{html.escape(q, quote=True)}'" in page


def test_connect_section_comes_from_the_client_list(monkeypatch):
    page = helppage.render(URL)
    for i, c in enumerate(helppage.CLIENTS):
        assert f"<h3>{html.escape(c.name, quote=True)}" in page
        assert f"<option value='{i}'>" in page  # a compact picker, one option each
        if c.snippet:
            assert f"data-copy='{html.escape(c.snippet.replace('{url}', URL), quote=True)}'" in page
    names = [c.name.split(" (")[0] for c in helppage.CLIENTS]
    assert names == ["Claude", "Claude Code", "ChatGPT", "Codex", "opencode", "VS Code", "Copilot CLI", "Cursor",
                     "Gemini CLI", "Zed", "Goose", "pi", "LM Studio", "Windsurf"]
    by = {c.name.split(" (")[0]: c for c in helppage.CLIENTS}
    assert "Not checked" in by["Windsurf"].note and "Not checked" in by["Copilot CLI"].note
    assert [n for n, c in by.items() if not c.checked] == ["Copilot CLI", "Windsurf"]
    assert page.count("not checked end to end</span>") == 2
    assert '"scopes": ["openid"]' in by["Gemini CLI"].snippet and "scopes" in " ".join(by["Gemini CLI"].steps)
    assert all('"wow"]' not in c.snippet for c in helppage.CLIENTS)  # the old `wow` scope is gone
    assert all("{url}" in c.snippet for c in helppage.CLIENTS)  # every client gets the URL to copy
    for c in helppage.CLIENTS:  # JSON snippets are valid JSON once the URL is in
        if c.snippet.startswith('{"'):
            assert json.loads(c.snippet.replace("{url}", URL))
    assert page.count("class='helpnote'") == 2
    assert page.count("How you know it worked:") >= len(helppage.CLIENTS)
    extra = helppage.Client("Some IDE", ("Open <settings>.", "Paste {url}."), "ide mcp add {url}")
    monkeypatch.setattr(helppage, "CLIENTS", helppage.CLIENTS + (extra,))
    page = helppage.render(URL)
    assert "<h3>Some IDE</h3>" in page and f"<li>Paste {URL}.</li>" in page
    assert "<li>Open &lt;settings&gt;.</li>" in page  # steps are text, escaped
    assert f"data-copy='ide mcp add {URL}'" in page
    assert f"data-panel='client:{len(helppage.CLIENTS) - 1}'" in page


BASE = URL.removesuffix("/mcp")


class _Seen(HTMLParser):
    """The text a reader sees before opening anything: a closed <details>
    shows its summary only, a hidden panel nothing (helppage.js then shows
    one OS panel: the browser test counts that one too)."""
    VOID = {"br", "img", "input", "meta", "link", "hr", "source", "wbr"}

    def __init__(self):
        super().__init__()
        self.stack, self.out = [], []

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            return
        a = dict(attrs)
        parent_hides = bool(self.stack) and self.stack[-1][1]
        details_body = bool(self.stack) and self.stack[-1][0] == "details" and tag != "summary"
        unpicked = tag == "option" and a.get("value") != ""  # a select shows its first option
        self.stack.append((tag, parent_hides or "hidden" in a or details_body or unpicked))

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        while self.stack:
            if self.stack.pop()[0] == tag:
                break

    def handle_data(self, data):
        if not (self.stack and self.stack[-1][1]):
            self.out.append(data)


def _visible(fragment: str) -> str:
    p = _Seen()
    p.feed(fragment)
    return " ".join(p.out)


def test_the_page_follows_a_friends_order_and_starts_with_three_steps():
    st = helppage.load_steps()
    page = helppage.render(URL, st, {})
    order = [page.index(f"id='{x}'") for x in ("start", "page", "ai", "trouble", "else")]
    assert order == sorted(order), "get started, your page, your AI, something wrong, everything else"
    start = page[:page.index("id='page'")]
    assert [m for m in re.findall(r"<li class='hstep' id='q-([a-z]+)'", start)] == ["addons", "command", "play"]
    step1 = start[start.index("id='q-addons'"):start.index("id='q-command'")]
    step2 = start[start.index("id='q-command'"):start.index("id='q-play'")]
    for a in st["addons"]:  # every addon is a named link somewhere
        assert f"href='{a['url']}' target='_blank' rel='noopener noreferrer' referrerpolicy='no-referrer'" in page
        chip = re.search(rf"class='hchip'[^>]*>{re.escape(html.escape(a['name'], quote=True))}", step1)
        # Step 1 shows what CurseForge installs; the install command adds the rest (the Recorder).
        assert bool(chip) == (a["source"] == "curseforge"), a["name"]
    assert "Polestar Recorder" in _visible(step2) and "Polestar Recorder" not in _visible(step1)
    by_hand = page[page.index("id='x-addons'"):]
    assert "zip on GitHub" in by_hand and "Polestar_Recorder zip" in by_hand  # the zip path stays, folded
    assert f"href='{BASE}/start'" in start  # the personal command lives on the start page
    assert "{code}" not in page and "{base}" not in page and "/install/{" not in page
    for k in ("mac", "windows"):  # how to open a terminal and paste, per OS
        assert f"data-panel='os:{k}'" in start
    upd = next(s for s in st["steps"] if s["id"] == "install").get("update") or {}
    for cmd in upd.values():  # the update line has no code: copyable as is
        assert f"data-copy='{html.escape(cmd.replace('{base}', BASE), quote=True)}'" in page
    for t in st["troubleshooting"]:
        assert f"id='tr-{t['id']}'" in page
        if t.get("fix"):
            assert html.escape(t["fix"], quote=True).split("`")[0][:40] in page
    trouble = page[page.index("id='trouble'"):page.index("id='else'")]
    assert 4 <= trouble.count("class='hqa'") <= 7  # 7: "My last session is missing" (crash loses unsaved addon data)


def test_what_a_friend_sees_first_is_short_and_in_their_words():
    """The owner, 2026-11-19: a wall of text, the same fact three times, a
    raw URL. Before anything is opened the page stays short, says each
    thing once, names its links, and keeps the plumbing words out."""
    page = helppage.render(URL, helppage.load_steps(), {})
    seen = _visible(page)
    words = len(seen.split())
    assert words <= 380, words  # the browser test holds the real count to ~350
    assert seen.count("Polestar Recorder") == 1 and "required" not in seen.lower()
    main = _visible(page[:page.index("id='else'")])
    for jargon in ("shipper", "tenant", "bundle", "MCP", "SavedVariables", "WTF"):
        assert jargon not in main, jargon
    text = re.sub(r"<(pre|code)>.*?</\1>", " ", page, flags=re.S)  # what you copy may be an address
    text = re.sub(r"<[^>]+>", " ", text)
    assert not re.search(r"https?://|www\.|\b[a-z0-9-]+\.(com|org|guide|io)\b", text), "a raw address in the text"
    for mfa in ("second factor", "two-factor", "2FA", "MFA", "authenticator"):
        assert mfa.lower() not in page.lower(), mfa


def test_missing_keys_leave_their_section_out():
    st = {"steps": [{"id": "play", "audience": "both", "title": "Play", "body": ["Play a bit."], "check": "Ticks."},
                    {"id": "name", "audience": "start", "title": "Pick a name", "body": ["x"]}]}
    page = helppage.render(URL, st, {})
    for absent in ("id='start'", "id='page'", "id='trouble'", "id='else'", "class='hchip'", "x-update", "x-uninstall"):
        assert absent not in page, absent
    assert "id='ai'" in page
    page = helppage.render(URL, {}, {})
    assert "id='start'" not in page and "id='ai'" in page


def test_a_clip_is_sealed_under_the_help_key(tmp_path, monkeypatch):
    from wowstats import account

    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "install-mac.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    monkeypatch.setattr(account, "_media_dir", lambda: tmp_path / "media")
    st = {"steps": [{"id": "install", "audience": "both", "title": "Install", "body": [], "media": "install-mac.mp4"},
                    {"id": "play", "audience": "both", "title": "Play", "body": [], "media": "missing.mp4"}]}
    monkeypatch.setattr(helppage, "load_steps", lambda: st)
    key = publish.unb64u(publish.new_key(str(tmp_path / "k")))
    helppage.build(publish.Bundle(tmp_path / "b"), key, URL)
    blob = (tmp_path / "b" / "help" / "media" / "install-mac.mp4.enc").read_bytes()
    assert publish.unseal(key, helppage.MEDIA_AD + "install-mac.mp4", blob).startswith(b"\x00\x00\x00\x18ftyp")
    plain = publish.unseal(key, helppage.CONTENT_PATH, (tmp_path / "b" / "help" / "content.enc").read_bytes())
    assert plain.decode().count("data-media=") == 1  # the missing clip gets no slot
    assert not (tmp_path / "b" / "help" / "media" / "missing.mp4.enc").exists()


def publish_with(tmp_path, monkeypatch, **extra):
    monkeypatch.setattr(ui, "Store", lambda *a, **k: PoisonStore())
    # The Dungeons panel reads the planner's inputs from a store of its own.
    monkeypatch.setattr(facts, "store_for", lambda env, like=None: like)
    env = {"VL_URL": "http://vl.invalid", "MODEL3D": "off", "PUBLISH_DIR": str(tmp_path / "site"),
           "ZOGZOG_KEY_FILE": str(tmp_path / "zogzog.key"), **extra}
    out = io.StringIO()
    with redirect_stdout(out):
        publish.publish(env)
    zz = Path(env["ZOGZOG_KEY_FILE"])
    return env, zz.read_text().strip() if zz.exists() else None, tmp_path / "site" / "current" / "public"


def test_sealed_under_its_own_key(tmp_path, monkeypatch):
    kf = tmp_path / "help.key"
    help_key = publish.new_key(str(kf))
    _, zz_key, pub = publish_with(tmp_path, monkeypatch, HELP_KEY_FILE=str(kf), HELP_MCP_URL=URL)
    blob = (pub / "help" / "content.enc").read_bytes()
    plain = publish.unseal(publish.unb64u(help_key), helppage.CONTENT_PATH, blob).decode()
    assert "Things to ask" in plain and URL in plain
    for s in SECRETS:  # generic: nothing from any character
        assert s not in plain
    with pytest.raises(InvalidTag):  # the zogzog link doesn't open it
        publish.unseal(publish.unb64u(zz_key), helppage.CONTENT_PATH, blob)
    shell = "".join((pub / "help" / f).read_text() for f in ("index.html", "help.js", "help.css"))
    assert "plan_session" not in shell and "Plausible" not in shell and "stats.js" not in shell
    assert "/help" not in (pub / "index.html").read_text()  # never linked from the public page
    # Every file the help page adds is one the public site may have.
    from tests.test_publish import assert_public_allowlist, files

    assert_public_allowlist(files(pub))
    site = static.Site({"SITE_DIR": str(tmp_path / "site")})
    assert site.resolve("/help/")[0].name == "index.html"
    assert site.resolve("/help/content.enc")[0].name == "content.enc"


def test_no_key_file_no_page_and_no_failure(tmp_path, monkeypatch):
    _, _, pub = publish_with(tmp_path, monkeypatch)
    assert not (pub / "help").exists()
    _, _, pub = publish_with(tmp_path / "b", monkeypatch, HELP_KEY_FILE=str(tmp_path / "missing.key"))
    assert not (pub / "help").exists()


def test_a_themed_clip_is_a_pair_and_the_guide_has_its_part(tmp_path, monkeypatch):
    from wowstats import account

    (tmp_path / "media").mkdir()
    for mode in ("dark", "light"):
        (tmp_path / "media" / f"tour-{mode}.mp4").write_bytes(b"\x00\x00\x00\x18ftyp" + mode.encode())
    monkeypatch.setattr(account, "_media_dir", lambda: tmp_path / "media")
    st = {"steps": [{"id": "play", "audience": "both", "title": "Play", "body": []}],
          "guide": {"page": [{"id": "overview", "title": "Overview", "body": ["Where you stand."], "media": "tour.mp4"},
                             {"id": "settings", "title": "Settings", "body": ["x"],
                              "points": [["Delete your data", "ask the owner."]]}]}}
    monkeypatch.setattr(helppage, "load_steps", lambda: st)
    key = publish.unb64u(publish.new_key(str(tmp_path / "k")))
    helppage.build(publish.Bundle(tmp_path / "b"), key, URL)
    for mode in ("dark", "light"):
        blob = (tmp_path / "b" / "help" / "media" / f"tour-{mode}.mp4.enc").read_bytes()
        assert publish.unseal(key, helppage.MEDIA_AD + f"tour-{mode}.mp4", blob).endswith(mode.encode())
    page = publish.unseal(key, helppage.CONTENT_PATH, (tmp_path / "b" / "help" / "content.enc").read_bytes()).decode()
    assert "id='page'" in page and "id='x-page'" in page  # the tour up front, the tabs' detail folded
    assert page.count("data-dark='tour-dark.mp4'") == 1  # shown once, not again in the detail
    assert "data-dark='tour-dark.mp4'" in page and "data-light='tour-light.mp4'" in page
    assert "Delete your data" in page
