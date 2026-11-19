"""The start page (/start), a new friend in Chromium (demo/record.sh): picks a
name when the list gave none, takes the install command (written to
<state>/install-url for the terminal take), waits while the terminal take
installs and the first evening arrives, watches the page turn green, then
opens their page.

The marks keep the order demo/video/src/Root.tsx cuts on: welcome, name,
addons, install, waiting, data, character, ai, page.

Env: DEMO_STATE, DEMO_THEME, DEMO_URL (the proxy port that logs in as this
mode's friend), DEMO_NAME (the name to pick when the list gave none).
"""

import os

from take import Take

url = os.environ["DEMO_URL"].rstrip("/")
name = os.environ["DEMO_NAME"]
form = '[data-widget="install"] input'
cmd = '.hero-cmd[data-os="mac"] code'

with Take("wizard") as t:
    page = t.page
    page.goto(f"{url}/start")
    page.wait_for_selector(f"{form}, {cmd}")
    t.mark("welcome")
    t.pause(2.5)
    if page.locator(form).count():  # listed without a usable name: pick one
        page.locator(form).press_sequentially(name, delay=120)
        t.pause(0.6)
        page.locator('[data-widget="install"] button').click()
    page.locator('ol.setup [data-pick-os="mac"]').click()
    page.wait_for_selector(cmd)
    t.mark("name")
    t.pause(1.5)
    t.scroll_to("ol.setup > li:nth-child(2)")
    t.mark("addons")
    t.pause(3.5)
    t.scroll_to("ol.setup > li.first")
    t.pause(0.8)
    page.locator('.hero-cmd[data-os="mac"] button').click()
    t.mark("install")
    mac = page.locator(cmd).inner_text()
    (t.state / "install-url").write_text(mac.removeprefix("curl -fsSL ").removesuffix(" | bash").strip())
    t.pause(2.5)
    t.scroll_to("ol.setup > li:nth-child(3)")
    t.mark("waiting")
    page.wait_for_selector('body[data-state="ok"]', timeout=300_000)
    t.scroll_to("main")
    t.mark("data")
    t.pause(2.5)
    page.wait_for_selector('.hero[data-in="ok"] [data-fill="last"]:has-text("for ")', timeout=60_000)
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
