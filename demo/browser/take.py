"""One browser take: Chromium recording a 1280x720 page in the mode's
colour scheme, with marks (the second each beat starts) for the
composition. Shared by the scenes in this folder.

    with Take("tour") as t:
        t.page.goto(...)
        t.mark("overview")
        t.pause(3)

Env: DEMO_STATE (where the takes land: <state>/<name>-<mode>.webm and
<name>-marks-<mode>.json), DEMO_THEME (dark | light).
"""

import json
import os
import pathlib
import time

from playwright.sync_api import sync_playwright

SIZE = {"width": 1280, "height": 720}


class Take:
    def __init__(self, name: str):
        self.name = name
        self.state = pathlib.Path(os.environ["DEMO_STATE"])
        self.theme = os.environ.get("DEMO_THEME", "dark")
        self.marks: dict[str, float] = {}

    def __enter__(self):
        self._p = sync_playwright().start()
        self.browser = self._p.chromium.launch()
        self.ctx = self.browser.new_context(viewport=SIZE, color_scheme=self.theme,
                                            record_video_dir=str(self.state / "video"), record_video_size=SIZE)
        self.page = self.ctx.new_page()
        self.t0 = time.time()
        return self

    def mark(self, name: str) -> None:
        self.marks[name] = round(time.time() - self.t0, 2)

    def pause(self, s: float) -> None:
        time.sleep(s)

    def scroll_to(self, selector: str, offset: int = 70, steps: int = 24) -> None:
        """A smooth scroll that ends with the element near the top."""
        y = self.page.locator(selector).first.evaluate("(e) => e.getBoundingClientRect().top + scrollY")
        self.scroll_y(max(0, y - offset), steps)

    def scroll_y(self, y: float, steps: int = 24) -> None:
        start = self.page.evaluate("scrollY")
        for i in range(1, steps + 1):
            k = i / steps
            ease = k * k * (3 - 2 * k)
            self.page.evaluate("(y) => window.scrollTo(0, y)", start + (y - start) * ease)
            time.sleep(0.03)

    def __exit__(self, *exc):
        self.mark("end")
        pages = self.ctx.pages
        videos = [p.video.path() for p in pages if p.video]
        self.ctx.close()
        self.browser.close()
        self._p.stop()
        if exc[0] is None:
            # One take per page; a scene that opened a second page keeps both (name-<i>).
            for i, v in enumerate(videos):
                suffix = "" if i == 0 else f"-{i + 1}"
                os.replace(v, self.state / f"{self.name}{suffix}-{self.theme}.webm")
            (self.state / f"{self.name}-marks-{self.theme}.json").write_text(json.dumps(self.marks, indent=2))
        return False
