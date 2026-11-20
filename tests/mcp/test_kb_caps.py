"""Every MCP tool that answers from the knowledge base answers with a slice,
never more than tools.KB_CHARS characters, for every class, level, lane and
broad query; what fit() cuts, it says."""

from __future__ import annotations

import json

import pytest

from wowstats import classguide, classmeta
from wowstats.mcp import tools


@pytest.fixture(scope="module")
def ctx():
    return tools.Ctx(store=None, env={"KB_GAME_TABLES": "off"})


def size(out) -> int:
    return len(json.dumps(out, ensure_ascii=False, default=str))


def calls(ctx):
    yield "kb_topics", {}
    for lane in tools.call(ctx, "kb_topics", {})["lanes"]:
        yield "kb_topics", {"lane": lane, "limit": 50}
    yield "kb_rule", {"name": "nope"}
    for q in ("dungeon", "the", "mage talents level 30", "gold", "raid"):
        yield "kb_search", {"query": q, "limit": 25}
    yield "news", {}
    yield "news", {"since": "2020-01-01"}
    for c in classguide.CLASSES:
        yield "news", {"class": c}
        for lv in (1, 10, 30, 45, 60):
            yield "class_guide", {"class": c, "level": lv}
        for spec in classmeta.trees(classguide.token(c)) + [None]:
            for lv in (20, 60):
                yield "class_meta", {"class": c, **({"spec": spec} if spec else {}), "level": lv}


def test_every_kb_answer_is_under_the_cap(ctx):
    seen = set()
    for name, args in calls(ctx):
        out = tools.call(ctx, name, args)
        assert size(out) <= tools.KB_CHARS, (name, args, size(out))
        seen.add(name)
    assert seen == tools.KB_TOOLS - {"kb_get", "kb_lookup"}


def test_kb_get_is_under_the_cap_with_its_longest_documents(ctx):
    from wowstats.analysis import kb

    k = kb.shared(ctx.env)
    ids = [r[0] for r in k.db.execute("SELECT id FROM docs ORDER BY length(body) DESC LIMIT 10")]
    assert size(tools.call(ctx, "kb_get", {"ids": ids})) <= tools.KB_CHARS
    many = [r[0] for r in k.db.execute("SELECT id FROM docs LIMIT 100")]
    assert size(tools.call(ctx, "kb_lookup", {"ids": many})) <= tools.KB_CHARS


def test_fit_cuts_side_lists_first_citations_last_and_says_so():
    out = {"meta": {"enchants": [{"text": "e" * 300}] * 8, "coming_up": [{"text": "c" * 300}] * 20},
           "contradictions": [{"text": "x" * 300}] * 6, "kb": {"levelling_ids": [f"KB-{i}" for i in range(20)]}}
    got = tools.fit(out, cap=6000)
    assert size(got) <= 6000
    assert got["meta"]["enchants"] == out["meta"]["enchants"]  # the answer's own list stays
    assert set(got["trimmed"]["cut"]) == {"meta.coming_up"} and "narrower" in got["trimmed"]["note"]
    # Tighter: the answer's own lists go before its citations and contradictions.
    got = tools.fit(out, cap=4500)
    assert size(got) <= 4500
    assert got["contradictions"] == out["contradictions"] and got["kb"] == out["kb"]
    assert "meta.enchants" in got["trimmed"]["cut"]
    # Only when nothing else is left do they shrink, and the note says so.
    got = tools.fit(out, cap=1500)
    assert size(got) <= 1500 and "contradictions" in got["trimmed"]["cut"]
    assert tools.fit({"a": [1, 2]}, cap=100) == {"a": [1, 2]}  # under the cap: untouched, no note
    long = tools.fit({"text": "y" * 10_000}, cap=3000)
    assert size(long) <= 3000 and long["text"].endswith("[cut]")


def test_the_class_guide_sends_what_the_next_bracket_repeats_once(ctx):
    g = tools.call(ctx, "class_guide", {"class": "Paladin", "level": 30})
    assert g["next"].get("same_as_now")
    assert not set(g["next"]["same_as_now"]) & (set(g["next"]) - {"same_as_now"})
    lo, hi = g["now"]["levels"][0], g["next"]["levels"][-1]
    assert all(c["levels"][0] <= hi and c["levels"][-1] >= lo for c in g["contradictions"] if c.get("levels"))


def test_the_class_guide_keeps_its_contradictions_and_citations(ctx):
    """0.7.0's A/B check: the cap had cut 5 of a Priest's 6 contradictions
    and 20 of its 21 levelling ids. They stay; what next repeats goes by id."""
    from wowstats.analysis import kb  # noqa: F401  (the KB the guide reads)

    for c in classguide.CLASSES:
        full = classguide.guide(c, 33)
        g = tools.call(ctx, "class_guide", {"class": c, "level": 33})
        cut = (g.get("trimmed") or {}).get("cut") or {}
        assert not any(k.startswith(("contradictions", "kb.levelling_ids")) for k in cut), (c, cut)
        assert g["kb"]["levelling_ids"] == full["kb"]["levelling_ids"]
        lo, hi = g["now"]["levels"][0], g["next"]["levels"][-1]
        assert g["contradictions"] == [x for x in full["contradictions"] if not x.get("levels")
                                       or (x["levels"][0] <= hi and x["levels"][-1] >= lo)]
        ids = set(g["next"].get("same_ids_as_now") or [])
        assert not ids & {x.get("id") for v in g["next"].values() if isinstance(v, list)
                          for x in v if isinstance(x, dict)}
