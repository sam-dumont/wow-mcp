"""One addon list everywhere (owner, 2026-11-20): /help/, /start (the live
page draws account.addons(), the printable one from the same list), the
docs and the installers' preflight show exactly steps.json's addons, each
with what it unlocks and its one step, and an addon dropped from the list
(#121) shows nowhere a player reads."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from wowstats import account
from wowstats.mcp import helppage

ROOT = Path(__file__).resolve().parent.parent
REMOVED = ("CharacterExport", "Narcissus", "SimBench")
# What a player reads: the pages, the help, the player docs. contract.md
# and the changelog keep the history on purpose (old events' fields).
USER_FACING = ["README.md", "docs/install.md", "docs/faq.md", "docs/reference/addons.md",
               "docs/videos/steps.json", "docs/explanation/*.md", "docs/players/*.md", "docs/ai/*.md",
               "web/*.js", "web/*.html", "site/pages/*.md"]


def names() -> list[str]:
    return [a["name"] for a in account.steps()["addons"]]


def test_every_addon_has_what_it_unlocks_and_one_step():
    for a in account.steps()["addons"]:
        assert a["what"] and a.get("step") and a.get("then"), a["id"]
        assert "install" not in a["then"].lower(), a["id"]  # what's left once it's installed
        assert a["url"] and a["url"].startswith("https://"), a["id"]
    wse = next(a for a in account.steps()["addons"] if a["id"] == "wowsims")
    assert wse["name"] == "WoWSims Exporter" and not wse["required"]
    assert "AddOns list" in wse["step"] and "switched off" in wse["step"]


@pytest.mark.parametrize("where", ["help", "start-print", "start-live", "docs-addons", "docs-faq", "shipper"])
def test_the_same_addons_everywhere(where):
    from shipper import wow_stats as shipper

    text = {
        "help": lambda: helppage.render("https://wow.example/mcp", here=True),
        "start-print": lambda: account.start_page(printable=True).decode(),
        "start-live": lambda: " ".join(f"{a['name']} {a['what']} {a.get('step')}" for a in account.addons()),
        "docs-addons": lambda: (ROOT / "docs/reference/addons.md").read_text(encoding="utf-8"),
        "docs-faq": lambda: re.search(r"<!-- generated:addon-list -->(.*?)<!-- /generated:addon-list -->",
                                      (ROOT / "docs/faq.md").read_text(encoding="utf-8"), re.S).group(1),
        "shipper": lambda: " ".join(a["name"] for a in shipper.ADDONS),
    }[where]()
    for n in names():
        assert n in text, (where, n)
    for n in REMOVED:
        assert n not in text, (where, n)
    if where in ("help", "start-print", "docs-addons", "docs-faq"):  # each says its one step
        for a in account.steps()["addons"]:
            plain = re.sub(r"</?code>|`", "", text)
            assert a["step"] in plain or (a["required"] and where == "help"), (where, a["id"])


def test_no_dropped_addon_where_players_read():
    hits = []
    for pat in USER_FACING:
        for p in ROOT.glob(pat):
            t = p.read_text(encoding="utf-8")
            hits += [f"{p.relative_to(ROOT)}: {n}" for n in REMOVED if n.lower() in t.lower()]
    assert not hits
