"""The zogzog link (demo/record.sh): Settings, copy the link, open it as a
friend would: the page for friends, Overview then Journal. The link's host
is the lab's (<name>-wow.localhost, on the static server's port).

Env: DEMO_STATE, DEMO_THEME, DEMO_URL, DEMO_SITE_PORT.
"""

import os
import re

from take import Take

url = os.environ["DEMO_URL"].rstrip("/")
port = os.environ["DEMO_SITE_PORT"]

with Take("private") as t:
    page = t.page
    page.goto(url + "/settings")
    page.wait_for_selector("h2:has-text('Your zogzog link')")
    t.pause(0.8)
    t.scroll_to("h2:has-text('Your zogzog link')", 140)
    t.mark("link")
    t.pause(3.0)
    href = page.locator("h2:has-text('Your zogzog link') ~ .cmd code").first.inner_text().strip()
    page.locator("h2:has-text('Your zogzog link') ~ .cmd button").first.click()
    t.pause(1.5)
    # The lab serves the sites over plain http on its own port.
    href = re.sub(r"^https://([^/]+)/", rf"http://\1:{port}/", href)
    page.goto(href)
    page.wait_for_selector(".ovmoves, #hero", timeout=30_000)
    t.pause(1.0)
    t.mark("zogzog")
    t.pause(4.5)
    t.scroll_y(500, 30)
    t.pause(3.0)
    t.scroll_y(0, 15)
    page.click("#tab-journal")
    t.pause(1.0)
    t.mark("journal")
    t.pause(4.0)
