"""/start in a real browser (Chromium and WebKit, Playwright), in each of
its states, as a friend opens it after Sam sends the link:

- new: not installed. The install command for their computer is the hero,
  already made (one click on Copy), Mac and Windows one switch apart.
- wait: installed in the last 15 minutes, nothing yet: play.
- none: installed, nothing ever arrived: red, and the one fix.
- ok: data arrives: green, when it last did, "Open my page".
- quiet: data arrived, but not for days: amber, and what to do.

Each state shows about 150 words before anything is opened (owner,
2026-11-19: the seven-step wizard was "SO SO SO MUCH information"), fits a
phone, and keeps every older function one fold away: the addon ticks, the
AI address, updating, the reset, uninstalling, the troubleshooting, the
clips and the printable guide.

Needs the `browser` group (`uv run --group browser playwright install
chromium webkit` once). WOW_SHOTS=<dir> saves screenshots of each state,
light and dark, at 1920 and 390 px (the first phone screen too)."""

import json
import os
import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_tenancy import TenantVL  # noqa: E402
from wowstats import account, tenancy, ui  # noqa: E402

WORDS = 150
FRIENDS = {"friend-sub-2": "mira", "friend-3": "pip", "friend-4": "kit", "friend-5": "lou", "friend-6": "zed",
           "friend-7": "ivy", "friend-8": "ada", "friend-9": "bo", "friend-10": "eve", "friend-11": "sam"}
# Not in STATES (the word counts): ada's data starts arriving mid-test, bo hides the tab,
# eve has a main with everything and a fresh alt.
# clash is listed with the owner's own name: no address, the owner's fix.
MOVING, HIDING, ALTS, CLASH = "friend-8", "friend-9", "friend-10", "friend-11"
CTX: dict = {}
STATES = {"new": "friend-3", "ok": "friend-sub-2", "none": "friend-4", "wait": "friend-5", "quiet": "friend-6",
          "partial": "friend-7"}

# The words a reader sees before opening anything: closed folds show their
# summary only, hidden parts nothing.
SEEN_WORDS = r"""() => document.body.innerText.split(/\s+/).filter((w) => /\w/.test(w)).length"""
OVER = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"


def iso(ago: timedelta) -> str:
    return (datetime.now(UTC) - ago).strftime("%Y-%m-%dT%H:%M:%SZ")


def status_rules(last: timedelta, recorder: bool = True) -> list:
    """What the store answers for /api/me/status: three feeds, a /played
    and Questie's quests (so Altoholic and Questie tick)."""
    first, last_ = iso(timedelta(days=9)), iso(last)
    span = {"first": first, "last": last_}
    feeds = (("wow-level", "420"), ("wow-recorder", "3100"), ("wow-gear", "80"))
    return [("stats by (feed) count() as n", [{"feed": f, "n": n, **span} for f, n in feeds
                                              if recorder or f != "wow-recorder"]),
            ("stats min(_time) as first", [span]),
            ("kind:=played", [{"n": "4"}]),
            ('feed:="wow-quests"', [{"source": "questie", "n": "120"}])]


# The shipper's setup report (wowstats.missing): what is installed, with its
# version, and the Recorder's switch. Auctionator isn't installed, so prices
# are missing ("Add Auctionator.").
SETUP = {"polestar": "1.4.2", "recorder": "0.9.1", "syndicator": "0.6", "questie": "9.8.0", "altoholic": "2.4"}


def preflight_rules(char: str, recorder_on: bool | str = True) -> list:
    """The setup report and what arrived per character, for status.setup and status.missing."""
    arrived = [{"char": char, "feed": f, "n": "5"} for f in ("wow-level", "wow-recorder", "wow-bags")]
    arrived += [{"char": char, "feed": "wow-quests", "source": "questie", "n": "120"},
                {"char": char, "feed": "wow-character", "kind": "played", "n": "4"}]
    return [("stats by (char, feed, source, kind)", arrived),
            ("fields _time, addons, recorder_on", [{"_time": iso(timedelta(hours=1)), "addons": json.dumps(SETUP),
                                                    "recorder_on": recorder_on if isinstance(recorder_on, str)
                                                    else json.dumps(recorder_on), "saved_chars": "1",
                                                    "shipper": "0.5.1"}])]


MAIN, ALT = "Testfriend-Otherhall", "Freshalt-Otherhall"


def alt_rules() -> list:
    """The owner's case (2026-11-19): a main with every feed, and a level 1
    alt played a minute (the Recorder's data, bags, no level split, no quest).
    Questie is installed but unticked on both; Syndicator unticked on the alt."""
    main = [{"char": MAIN, "feed": f, "n": "5"} for f in ("wow-level", "wow-dungeon", "wow-recorder", "wow-bags")]
    main += [{"char": MAIN, "feed": "wow-quests", "source": "altoholic", "n": "120"}, {"feed": "wow-prices", "n": "9"}]
    alt = [{"char": ALT, "feed": f, "n": "2"} for f in ("wow-recorder", "wow-gear", "wow-character")]
    rep = {"addons": json.dumps({**SETUP, "auctionator": "349"}), "recorder_on": "true", "saved_chars": "2",
           "off": json.dumps({MAIN: ["questie"], ALT: ["questie", "syndicator"]}), "_time": iso(timedelta(hours=1))}
    return [("stats by (char, feed, source, kind)", main + alt), ("fields _time, addons, recorder_on", [rep])]


@pytest.fixture(scope="module")
def site():
    vl = TenantVL()
    tmp = Path(tempfile.mkdtemp())
    master = tmp / "master.key"
    master.write_text(tenancy.b64u(b"m" * 32))
    friends = tmp / "friends.json"
    friends.write_text(json.dumps(FRIENDS))
    env = {"BIND": "127.0.0.1", "VIEW": "full", "AUTH": "proxy", "PROXY_TRUSTED_CIDRS": "127.0.0.1/32", "VL_URL": vl.url, "VM_URL": vl.url,
           "FRIENDS_VL_URL": vl.url, "WOW_STATE_DIR": str(tmp / "state"), "WOW_OWNER_SUB": "owner-sub-1",
           "WOW_OWNER_SLUG": "sam", "WOW_FRIENDS_FILE": str(friends), "SITE_DOMAIN": "m.example",
           "PUBLIC_URL": "https://wow-full.m.example", "ZOGZOG_MASTER_KEY_FILE": str(master),
           "ZOGZOG_KEY_FILE": str(tmp / "z.key"), "MODEL3D": "off", "MODEL3D_CACHE_DIR": str(tmp / "c"),
           "ANALYSIS_STATE_DIR": str(tmp / "a")}
    app = ui.App(env)
    t = ui.tenancy_of(env, app)
    ids = {sub: t.resolve_tenant(sub).account_id for sub in FRIENDS}
    vl.rules[str(ids[STATES["ok"]])] = (preflight_rules("Testfriend-Otherhall") + status_rules(timedelta(hours=2))
                                        + vl.rules.get(str(ids[STATES["ok"]]), []))
    vl.rules[str(ids[STATES["quiet"]])] = status_rules(timedelta(days=5))
    vl.rules[str(ids[STATES["partial"]])] = (preflight_rules("Testfriend-Otherhall", recorder_on="unknown")
                                             + status_rules(timedelta(minutes=20), recorder=False))
    vl.rules[str(ids[ALTS])] = alt_rules() + status_rules(timedelta(hours=1))
    for sub, ago in ((STATES["ok"], timedelta(days=9)), (STATES["none"], timedelta(days=1)), (ALTS, timedelta(days=9)),
                     (STATES["wait"], timedelta(minutes=2)), (STATES["quiet"], timedelta(days=9)),
                     (STATES["partial"], timedelta(days=9)), (MOVING, timedelta(days=1))):
        t.registry.update(sub, ingest_sha256="x" * 64,
                          ingest_set_at=(datetime.now(UTC) - ago).isoformat(timespec="seconds"))
    CTX.update(vl=vl, ids=ids)
    app.port = 0
    srv = ui.serve(app)
    srv.RequestHandlerClass = ui.make_handler(app, t)
    import threading

    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()
    vl.close()


@pytest.fixture(scope="module")
def pw():
    with sync_api.sync_playwright() as p:
        yield p


def open_start(browser, base, state, width, scheme="light", sub=None):
    c = browser.new_context(viewport={"width": width, "height": 844 if width < 600 else 1080},
                            color_scheme=scheme, extra_http_headers={"X-Forwarded-User": sub or STATES[state]},
                            device_scale_factor=2 if width < 600 else 1)
    pg = c.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"{base}/start")
    pg.wait_for_selector(f"body[data-state={'ok' if state in ('quiet', 'partial') else state}]")
    if state == "new":
        pg.wait_for_selector(".hero-cmd:visible code")
    pg.wait_for_timeout(200)
    return c, pg, errors


def shoot(pg, name):
    """A page (full, plus the first screen on a phone), or one element."""
    out = os.environ.get("WOW_SHOTS")
    if not out:
        return
    Path(out).mkdir(parents=True, exist_ok=True)
    if not hasattr(pg, "viewport_size"):  # a Locator
        pg.screenshot(path=f"{out}/{name}.png")
        return
    pg.screenshot(path=f"{out}/{name}-full.png", full_page=True)
    if pg.viewport_size["width"] < 600:
        pg.screenshot(path=f"{out}/{name}-first.png")


def open_folds(pg):
    pg.evaluate("() => document.querySelectorAll('details.fold:not([hidden])').forEach((d) => { d.open = true; })")


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_start_page_states(site, pw, engine):
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:  # noqa: BLE001 - that browser isn't installed
        pytest.skip(f"no {engine}: {e}")
    schemes = ("light", "dark") if os.environ.get("WOW_SHOTS") else ("light",)
    counts = {}
    for width in (390, 1920):
        for state in STATES:
            for scheme in schemes:
                c, pg, errors = open_start(b, site, state, width, scheme)
                words = pg.evaluate(SEEN_WORDS)
                counts[state] = words
                assert words <= WORDS, (state, width, words, pg.locator("body").inner_text())
                assert pg.evaluate(OVER) <= 0, (state, width)
                shoot(pg, f"after-{state}-{engine}-{width}-{scheme}")
                if state == "ok":  # what the folds hold, for the review
                    open_folds(pg)
                    shoot(pg, f"after-{state}-open-{engine}-{width}-{scheme}")
                c.close()
                assert not errors, errors
    print(engine, "words seen per state:", counts)
    b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_new_friend_gets_the_command_first(site, pw, engine):
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no {engine}: {e}")
    c, pg, errors = open_start(b, site, "new", 390)
    # The command is made as the page opens: Copy is the one action.
    cmd = pg.locator(".hero-cmd:visible code")
    assert cmd.count() == 1
    pg.locator("ol.setup [data-pick-os=windows]").click()
    assert re.fullmatch(r"irm https://wow-full\.m\.example/install/\w+ \| iex", cmd.inner_text().strip())
    assert "PowerShell" in pg.locator("ol.setup h2:visible").first.inner_text()
    pg.locator("ol.setup [data-pick-os=mac]").click()
    assert re.fullmatch(r"curl --proto '=https' --proto-redir '=https' -fsSL https://wow-full\.m\.example/install/\w+ \| bash", cmd.inner_text().strip())
    assert "Terminal" in pg.locator("ol.setup h2:visible").first.inner_text()
    pg.reload()
    pg.wait_for_selector(".hero-cmd:visible code")
    assert pg.locator("ol.setup [data-pick-os=mac]").get_attribute("aria-pressed") == "true"  # the pick stays
    # The rest is one fold away; nothing about updating before an install.
    assert pg.locator(".hero[data-in=new] button:visible", has_text="Copy").count() == 1
    assert not pg.locator("#f-update").is_visible() and not pg.locator("#f-ai").is_visible()
    open_folds(pg)
    text = pg.locator(".folds").inner_text()
    for addon in account.addons():
        assert addon["name"] in text
    assert "WoW folder not found" in text
    assert pg.locator("#f-videos video").count() >= 2
    assert pg.locator("a[href='/start/print']").is_visible()
    c.close()
    assert not errors, errors
    b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_a_name_clash_is_the_owners_fix_not_the_friends(site, pw, engine):
    # Friends never pick their page name (the owner sets it, 2026-11-19): a
    # listed name that is taken leaves nothing to type, only whose fix it is.
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no {engine}: {e}")
    c = b.new_context(viewport={"width": 390, "height": 844}, extra_http_headers={"X-Forwarded-User": CLASH})
    pg = c.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"{site}/start")
    pg.wait_for_selector("body[data-state=name]")
    hero = pg.locator(".hero[data-in=name]")
    # Whose fix, without a name: the owner's slug ("sam") is no display name.
    assert hero.locator("h1").inner_text() == "Your page needs a fix"
    assert "the person who runs this site has to change it" in " ".join(hero.inner_text().split())
    assert "Nothing to do on your side" in hero.inner_text() and "Sam" not in hero.inner_text()
    assert pg.locator("input:visible, .hero-cmd:visible").count() == 0
    assert pg.evaluate(SEEN_WORDS) <= WORDS and pg.evaluate(OVER) <= 0
    shoot(pg, f"name-clash-{engine}")
    pg.goto(f"{site}/settings")
    pg.wait_for_selector(".widget[data-widget=settings] p:has-text('needs a fix from the person who runs this site')")
    assert pg.locator("input:visible").count() == 0
    c.close()
    assert not errors, errors
    b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_working_and_broken_say_so_in_one_line(site, pw, engine):
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no {engine}: {e}")
    c, pg, errors = open_start(b, site, "ok", 390)
    hero = pg.locator(".hero[data-in=ok]")
    assert hero.locator("h1").inner_text().strip().lower() == "it works"
    assert re.search(r"Last data 2 hours ago, for Testfriend", hero.inner_text())
    assert hero.locator("a.btn[href='/']").is_visible()
    assert not hero.locator(".warn").is_visible()
    assert not hero.locator(".once:visible").count()  # its shipper updates itself
    assert re.search(r"Your addons: \d+ sending", pg.locator("#f-addons summary").inner_text())
    open_folds(pg)
    # Every older function, one fold away.
    assert pg.locator("#f-addons li.seen > a", has_text=re.compile("^Questie$")).count() == 1
    assert pg.locator("#f-addons li.seen > a", has_text=re.compile("^Altoholic$")).count() == 1
    assert pg.locator("#f-addons li.unseen > a", has_text=re.compile("^Auctionator$")).count() == 1
    # The setup report: a tick and the version per installed addon, a cross
    # and what it adds otherwise, the Recorder's switch.
    li = pg.locator("#f-addons li[data-addon=polestar]")
    assert li.locator(".inst").inner_text() == "\u2713 1.4.2" and not li.locator(".what").is_visible()
    li = pg.locator("#f-addons li[data-addon=auctionator]")
    # Optional and not installed: a choice, said neutrally, never a red cross.
    assert li.locator(".inst").inner_text() == "optional, not installed" and li.locator(".what").is_visible()
    assert "opt" in li.locator(".inst").get_attribute("class").split()
    assert li.locator(".step1").inner_text() == "Install it, then scan the auction house once."
    # Installed but not sending: only the step left, never "Install it".
    li = pg.locator("#f-addons li[data-addon=syndicator]")
    assert li.locator(".inst").inner_text().startswith("\u2713") and "unseen" in li.get_attribute("class")
    assert li.locator(".step1").inner_text() == "Log in once with each character."
    assert pg.locator("#f-addons li[data-addon=recorder] .note").inner_text() == "Recording is on."
    # What the pages can't show yet, each line with its fix and the addon's link.
    gap = pg.locator("#f-addons ul.gaps li[data-kind=prices]")
    assert gap.inner_text() == "Missing: auction prices. Add Auctionator."
    assert gap.locator("a", has_text="Auctionator").get_attribute("href").startswith("https://www.curseforge.com/")
    assert pg.locator("#f-addons ul.gaps li").count() == 1
    # The summary counts the rows: 4 green, Syndicator quiet, Auctionator and WoWSims Exporter to add.
    assert pg.locator("#f-addons summary").inner_text() == (
        "Your addons: 4 sending \u00b7 1 installed, not sending yet \u00b7 2 optional to add")
    # One block per AI app, picked like Mac/Windows; the address is shared.
    ai = pg.locator("#f-ai")
    assert "https://wow-full.m.example/mcp" in ai.inner_text()
    assert ai.locator("[data-app]:visible").count() == 1 and "Add custom connector" in ai.inner_text()
    ai.locator("[data-pick-app=chatgpt]").click()
    assert "Add custom MCP server" in ai.inner_text() and "Add custom connector" not in ai.inner_text()
    ai.locator("[data-pick-app=claude-code]").click()
    assert "claude mcp add --transport http wow https://wow-full.m.example/mcp" in ai.inner_text()
    ai.locator("[data-pick-app=other]").click()
    assert "remote MCP server" in ai.inner_text()
    ai.locator("[data-pick-app=chatgpt]").click()
    shoot(pg.locator("#f-ai"), f"after-ai-chatgpt-{engine}-390-light")
    pg.locator("[data-pick-app=claude]").click()
    upd = pg.locator("#f-update")
    assert "install/update" in upd.inner_text()
    assert upd.locator("button", has_text="Reset my upload password").is_visible()
    leave = pg.locator("#f-leave").inner_text()
    assert "launchctl bootout" in leave and "friends list" in leave
    pg.locator("#f-leave [data-pick-os=windows]").click()
    assert "schtasks /Delete" in pg.locator("#f-leave").inner_text()
    assert pg.evaluate(OVER) <= 0
    c.close()

    c, pg, errors2 = open_start(b, site, "none", 390)
    h1 = pg.locator(".hero[data-in=none] h1")
    assert h1.inner_text().strip().lower() == "nothing has arrived yet" and "bad" in h1.get_attribute("class")
    assert "/psrec on" in pg.locator(".hero[data-in=none]").inner_text()
    is_open = "(d) => d.open"
    assert not pg.locator("#f-trouble").evaluate(is_open)
    pg.locator("[data-open=tr-no-data]").click()
    # The fold and the answer open together.
    assert pg.locator("#f-trouble").evaluate(is_open) and pg.locator("#tr-no-data").evaluate(is_open)
    assert pg.locator("#tr-no-data p").first.is_visible()
    c.close()

    c, pg, errors3 = open_start(b, site, "wait", 390)
    assert pg.locator(".hero[data-in=wait] h1").inner_text().strip().lower().startswith("installed")
    c.close()

    c, pg, errors4 = open_start(b, site, "quiet", 390)
    h1 = pg.locator(".hero[data-in=ok] h1")
    assert h1.inner_text().strip().lower() == "last data 5 days ago" and "quiet" in h1.get_attribute("class")
    # No setup report with a version: a shipper from before the auto-update.
    once = pg.locator(".hero[data-in=ok] [data-fill=update-once]")
    assert once.is_visible() and "update command once" in once.inner_text()
    once.locator("[data-open=f-update]").click()
    assert pg.locator("#f-update").evaluate("(d) => d.open") and "install/update" in pg.locator("#f-update").inner_text()
    c.close()

    c, pg, errors5 = open_start(b, site, "partial", 390)
    warn = pg.locator(".hero[data-in=ok] .warn")
    # Installed by the installer, never switched on in game (the shipper says "unknown"):
    # the same fix as "off", never just "installed" (a friend's page, 2026-11-20).
    assert warn.is_visible() and "/psrec on" in warn.inner_text() and "not switched on yet" in warn.inner_text()
    open_folds(pg)
    note = pg.locator("#f-addons li[data-addon=recorder] .note")
    assert "not switched on yet" in note.inner_text() and "/psrec on" in note.inner_text()
    c.close()
    assert not (errors + errors2 + errors3 + errors4 + errors5)
    b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_a_status_change_leaves_the_reader_alone(site, pw, engine):
    """The data starts arriving while the reader has a fold open and an AI
    app picked: the lamp turns green, and nothing they touched moves."""
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no {engine}: {e}")
    vl, tid = CTX["vl"], str(CTX["ids"][MOVING])
    vl.rules.pop(tid, None)
    account.STATUS._c.clear()
    c, pg, errors = open_start(b, site, "none", 390, sub=MOVING)
    pg.locator("#f-ai summary").click()
    pg.locator("#f-ai [data-pick-app=chatgpt]").click()
    pg.locator("#f-addons summary").click()
    pg.evaluate("() => { document.querySelector('#f-addons ul.addons').dataset.mark = 'same'; }")
    vl.rules[tid] = status_rules(timedelta(minutes=3))
    account.STATUS._c.clear()
    pg.wait_for_selector("body[data-state=ok]", timeout=25_000)  # the next 10 s poll
    assert pg.locator(".hero[data-in=ok] h1").inner_text().strip().lower() == "it works"
    assert pg.locator("#f-ai").evaluate("(d) => d.open") and pg.locator("#f-addons").evaluate("(d) => d.open")
    assert pg.locator("#f-ai [data-pick-app=chatgpt]").get_attribute("aria-pressed") == "true"
    assert "Add custom MCP server" in pg.locator("#f-ai [data-app]:visible").inner_text()
    assert pg.locator("#f-addons ul.addons").get_attribute("data-mark") == "same"  # not rebuilt
    assert pg.locator("#f-addons li.seen").count() >= 1
    c.close()
    assert not errors, errors
    b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_a_shipper_that_stayed_behind_gets_the_update_command(site, pw, engine, monkeypatch):
    """Its setup report (version 0.5.1, an hour ago) still names an older
    version long after this server started serving a newer one: auto_update
    off, or an update it refused. One line, with the update command's fold."""
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no {engine}: {e}")
    monkeypatch.setattr(account, "STARTED", 0)
    account.STATUS._c.clear()
    try:
        c, pg, errors = open_start(b, site, "ok", 390)
        line = pg.locator(".hero[data-in=ok] [data-fill=update-by-hand]")
        assert line.is_visible() and not pg.locator("[data-fill=update-once]").is_visible()
        server = json.loads(account.shipper_manifest())["version"]
        assert line.inner_text().startswith(f"The program on your computer is version 0.5.1 and this site has {server}")
        line.locator("a", has_text="Run the update command").click()
        assert pg.locator("#f-update").evaluate("(d) => d.open")
        c.close()
        assert not errors, errors
    finally:
        account.STATUS._c.clear()
        b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_a_hidden_tab_asks_nothing(site, pw, engine):
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no {engine}: {e}")
    c, pg, errors = open_start(b, site, "new", 390, sub=HIDING)
    asked = []
    pg.on("request", lambda r: asked.append(r.url) if "/api/me" in r.url else None)
    flip = """(v) => { Object.defineProperty(document, 'visibilityState', {value: v, configurable: true});
                       document.dispatchEvent(new Event('visibilitychange')); }"""
    pg.evaluate(flip, "hidden")
    pg.wait_for_timeout(500)
    asked.clear()
    pg.wait_for_timeout(12_000)  # more than one 10 s poll
    assert asked == [], asked
    pg.evaluate(flip, "visible")  # back: one check at once
    pg.wait_for_timeout(1500)
    assert any("/api/me/status" in u for u in asked), asked
    c.close()
    assert not errors, errors
    b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_the_addon_gaps_name_the_character_and_never_blame_an_installed_addon(site, pw, engine):
    """The owner saw "Missing: level times ... Tick Polestar" and "Add
    Questie or Altoholic" with both installed and his main complete: the
    lines were his level 1 alt's, unnamed, and guessed from the gap alone."""
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no {engine}: {e}")
    for width, scheme in ((390, "dark"), (1280, "dark"), (390, "light")):
        c, pg, errors = open_start(b, site, "ok", width, scheme, sub=ALTS)
        open_folds(pg)
        gaps = pg.locator("#f-addons ul.gaps li")
        assert sorted(gaps.all_inner_texts()) == [
            f"Missing for {ALT}: level times, dungeon runs and gear advice. Play to your next level, then log out.",
            f"Missing for {ALT}: quests you did before installing. Finish a quest with this character, "
            "then log out or type /reload once.",
            f"Missing for {ALT}: your gear and bags. Syndicator is installed but not ticked for this character: "
            "tick it in AddOns at character select, then log in.",
        ]
        assert MAIN not in pg.locator("#f-addons ul.gaps").inner_text()
        assert pg.evaluate(OVER) <= 0
        if engine == "chromium":
            shoot(pg.locator("#f-addons"), f"missing-addons-{width}-{scheme}")
        c.close()
        assert not errors, errors
    b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_the_addon_list_is_steps_json_with_one_step_each(site, pw, engine):
    """Owner, 2026-11-20: each addon with what it unlocks and its one step,
    WoWSims Exporter among them, the same list as /help/ and the docs."""
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no {engine}: {e}")
    for width, scheme in ((390, "light"), (390, "dark"), (1280, "light"), (1280, "dark")):
        c, pg, errors = open_start(b, site, "ok", width, scheme)
        open_folds(pg)
        lis = pg.locator("#f-addons ul.addons li")
        assert [x.get_attribute("data-addon") for x in lis.all()] == [a["id"] for a in account.addons()]
        for a in account.addons():
            li = pg.locator(f"#f-addons li[data-addon={a['id']}]")
            installed = li.locator(".inst").inner_text().startswith("\u2713")
            assert li.locator(".what").text_content() == a["what"]
            assert li.locator(".step1").text_content() == (a["then"] if installed else a["step"]), a["id"]
            # Shown until its data arrives: then the step is done.
            assert li.locator(".step1").is_visible() == ((li.get_attribute("class") or "") != "seen"
                                                         and not (installed and a["id"] == "recorder")), a["id"]
        assert pg.evaluate(OVER) <= 0
        pg.locator("#f-addons").scroll_into_view_if_needed()
        shoot(pg.locator("#f-addons"), f"start-addons-{engine}-{width}-{scheme}")
        c.close()
        assert not errors, errors
    b.close()
