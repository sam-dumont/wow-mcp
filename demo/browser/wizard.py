"""The start page (/start), a new friend in Chromium (demo/record.sh): their
name already set by the friends list, takes the install command (written to
<state>/install-url for the terminal take), waits while the terminal take
installs and the first evening arrives, watches the page turn green, then
opens their page.

The marks keep the order demo/video/src/Root.tsx cuts on: welcome, addons,
install, waiting, data, character, ai, page.

Env: DEMO_STATE, DEMO_THEME, DEMO_URL (the proxy port that logs in as this
mode's friend).
"""

import os

from take import Take

url = os.environ["DEMO_URL"].rstrip("/")
form = '[data-widget="install"] input'
cmd = '.hero-cmd[data-os="mac"] code'

with Take("wizard") as t:
    page = t.page
    page.goto(f"{url}/start")
    page.wait_for_selector(f"{form}, {cmd}")
    t.mark("welcome")
    t.pause(2.5)
    if page.locator(form).count():  # the owner names friends: a form here is a broken lab
        raise SystemExit("the start page asks for a name: the friends list gave this friend none")
    page.locator('ol.setup [data-pick-os="mac"]').click()
    page.wait_for_selector(cmd)
    t.pause(1.5)
    t.scroll_to("ol.setup > li:nth-child(2)")
    t.mark("addons")
    t.pause(3.5)
    t.scroll_to("ol.setup > li.first")
    t.pause(0.8)
    page.locator('.hero-cmd[data-os="mac"] button').click()
    t.mark("install")
    mac = page.locator(cmd).inner_text()
    (t.state / "install-url").write_text(mac.split(" -fsSL ", 1)[-1].removesuffix(" | bash").strip())
    t.pause(2.5)
    t.scroll_to("ol.setup > li:nth-child(3)")
    t.mark("waiting")
    page.wait_for_selector('body[data-state="ok"]', timeout=300_000)
    # The character's name follows once the page's answers are rebuilt
    # (wowstats.prebuilt checks for new data every minute). The video cuts
    # from "waiting" to "data", so the wait never shows.
    page.wait_for_selector('.hero[data-in="ok"] [data-fill="last"]:has-text("for ")', timeout=300_000)
    t.scroll_to("main")
    t.mark("data")
    t.pause(2.5)
    t.mark("character")
    t.pause(2.5)
    page.locator("#f-ai summary").click()
    t.mark("ai")
    t.pause(3.0)
    t.scroll_to("main")
    t.pause(0.5)
    page.locator('.hero[data-in="ok"] a.btn').click()
    page.wait_for_selector(".ovmoves", timeout=30_000)
    t.mark("page")
    t.pause(5.0)
