"""A tour of a friend's page after six evenings of play (demo/record.sh):
the Overview, the Character tab (sheet, upgrades, dungeons, a plan for the
next two hours), the Timeline back in time, the Journal.

Env: DEMO_STATE, DEMO_THEME, DEMO_URL (the friend's full view).
"""

import os

from take import Take

url = os.environ["DEMO_URL"].rstrip("/")

with Take("tour") as t:
    page = t.page
    page.goto(url + "/")
    page.wait_for_selector(".ovmoves")
    t.pause(1.0)
    t.mark("overview")
    t.pause(4.0)
    t.scroll_to(".ovmoves", 90)
    t.mark("moves")
    t.pause(5.0)
    t.scroll_y(0)
    page.click("#tab-overview")
    page.wait_for_selector("#gear:not([hidden])")
    t.pause(1.0)
    t.mark("character")
    t.pause(4.0)
    t.scroll_to("#gear", 20)
    t.pause(1.5)
    # The Upgrades heading itself: "text=Upgrades" is a case-insensitive
    # substring and matched "Drops 2 upgrades..." on the hidden Overview.
    upgrades = page.locator("#advice h3.adv-h:text-is('Upgrades')").first
    if upgrades.count():
        t.scroll_y(page.evaluate("(e) => e.getBoundingClientRect().top + scrollY - 80", upgrades.element_handle()))
    t.mark("upgrades")
    t.pause(4.5)
    t.scroll_to("#dungeons", 60, 30)
    t.mark("dungeons")
    t.pause(5.5)
    t.scroll_to("#planbox", 60, 30)
    t.mark("plan")
    t.pause(1.5)
    page.select_option("#planhours", "2")
    t.pause(0.6)
    page.click("#planbtn")
    page.wait_for_selector("#planout .plantext", timeout=60_000)
    t.pause(1.0)
    t.mark("planned")
    t.scroll_to("#planout", 120, 30)
    t.pause(6.0)
    t.scroll_y(0, 20)
    page.click("#tab-timeline")
    page.wait_for_selector("#timeline ol.tl")
    t.pause(1.0)
    t.mark("timeline")
    t.pause(2.5)
    slider = page.locator('#timeline input[type="range"]').first
    if slider.count():
        slider.focus()
        for _ in range(4):
            page.keyboard.press("ArrowLeft")
            t.pause(0.9)
    t.mark("backintime")
    t.pause(3.5)
    page.click("#tab-journal")
    t.pause(1.5)
    t.mark("journal")
    t.pause(4.5)
