"""The published quick start hands out the released image, never a placeholder
(external review of 0.11.0: the live page said `@sha256:<digest from the release
notes>`). tools/check_live_quickstart.py reads the live pages after each deploy
(pages.yml); these cases pin what it accepts, on pages written by hand in the
shape the site renders (a code block's data-code attribute, inline code)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import check_live_quickstart as live  # noqa: E402
import docs_values  # noqa: E402

DIGEST = "8de69a493690ad70ca1f08d31d83e7be4c9d77951cdced27b09d7ae7075683eb"
IMAGE = f"ghcr.io/sam-dumont/wow-mcp:0.11.0@sha256:{DIGEST}"
BLOCK = ('<div class="copy"><button data-code="cd deploy/compose &amp;&amp; printf &#x27;%s\\n&#x27; '
         "INGEST_PASSWORD=demo \\  &#x27;WOW_STATS_IMAGE={img}&#x27; > .env\"></button></div>"
         "<span>'</span><span>WOW_STATS_IMAGE={img}</span><span>'</span>")
INLINE = "<p>Set <code>WOW_STATS_IMAGE={img}</code> too: the latest release.</p>"


def test_the_released_image_passes():
    assert live.problems(BLOCK.format(img=IMAGE), IMAGE) == []
    assert live.problems(INLINE.format(img=IMAGE), IMAGE) == []


def test_the_placeholder_fails():
    page = BLOCK.format(img="ghcr.io/sam-dumont/wow-mcp:0.11.0@sha256:&lt;digest from the release notes&gt;")
    assert live.problems(page, IMAGE), "the 0.11.0 page passed"


def test_another_digest_or_version_fails():
    other = IMAGE.replace(DIGEST, "0" * 64)
    assert live.problems(INLINE.format(img=other), IMAGE) == [f"{other} isn't the release's {IMAGE}"]
    older = IMAGE.replace("0.11.0", "0.10.0")
    assert live.problems(INLINE.format(img=older), IMAGE)


def test_a_page_without_the_command_fails():
    assert live.problems("<p>Quick start</p>", IMAGE) == ["no WOW_STATS_IMAGE= on the page"]


NOTES = f"""### Images

Pin by digest:

- `{IMAGE}`
- `ghcr.io/sam-dumont/wow-mcp-render:0.11.0@sha256:{"3" * 64}`
"""


def test_the_image_comes_from_its_own_versions_line():
    assert docs_values.image_in_notes(NOTES, "0.11.0") == IMAGE
    assert docs_values.image_in_notes(NOTES, "0.12.0") is None
    assert docs_values.image_in_notes(NOTES.replace(DIGEST, "<digest>"), "0.11.0") is None
