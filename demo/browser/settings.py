"""Settings, for a friend with two characters (demo/record.sh): hide the alt
from the public page, switch the public page on, then reset the upload
password and get the new install command.

Env: DEMO_STATE, DEMO_THEME, DEMO_URL.
"""

import os

from take import Take

url = os.environ["DEMO_URL"].rstrip("/")

with Take("settings") as t:
    page = t.page
    page.on("dialog", lambda d: d.accept())
    page.goto(url + "/settings")
    page.wait_for_selector("table.chars")
    t.pause(1.0)
    t.mark("settings")
    t.pause(3.0)
    t.scroll_to("table.chars", 120)
    t.pause(1.0)
    with page.expect_response("**/api/me/settings"):
        page.locator("table.chars input").nth(2).check()  # the second character's "Hide from public page"
    page.wait_for_selector(".msg.ok")
    t.mark("hidden")
    t.pause(3.0)
    t.scroll_y(0)
    with page.expect_response("**/api/me/settings"):
        page.locator("label.toggle input").first.check()
    t.pause(0.5)
    t.mark("public")
    t.pause(3.5)
    t.scroll_to("h2:has-text('Your upload password')", 120)
    t.mark("password")
    t.pause(2.0)
    page.locator("button:has-text('Reset my upload password')").click()
    page.locator(".cmd").nth(1).wait_for(timeout=30_000)
    t.mark("reset")
    t.scroll_to("h2:has-text('Your upload password')", 120)
    t.pause(5.0)
