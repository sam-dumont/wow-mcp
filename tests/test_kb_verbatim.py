"""The knowledge base never copies its sources' wording (docs/kb.md,
"Sources and licensing"): the measure itself, the model passes' guard, and
the shipped files."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from wowstats.analysis import kbschema, verbatim

ROOT = Path(__file__).resolve().parents[1]
KB = ROOT / "data" / "kb"
sys.path.insert(0, str(ROOT / "tools" / "kb"))
_spec = importlib.util.spec_from_file_location("kb_refresh_v", ROOT / "tools" / "kb" / "refresh.py")
refresh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(refresh)

SOURCE = ("Dungeon mobs now give no experience once they are trivial to the highest level player in the party, "
          "which stops boosting low characters through dungeons they could not survive alone.")
# Provenance, not KB text: `origin` names the cited item by its own title.
NOT_TEXT = {"origin", "source_url", "sources", "url", "source_type", "date", "last_verified", "id"}


def test_a_run_of_twelve_words_is_a_copy():
    n, run = verbatim.longest_run("Patch note: dungeon mobs now give no experience once they are trivial to the "
                                  "highest level player.", SOURCE)
    assert n == 15 and run.startswith("dungeon mobs now give")
    assert verbatim.copied("They said dungeon mobs now give no experience once they are trivial to the "
                           "highest level player.", SOURCE).startswith("repeats 15")


def test_a_patchwork_of_phrases_is_a_copy_too():
    # No run reaches 12 words, but most 8-word phrases are the source's.
    text = ("Dungeon mobs now give no experience once they are trivial; this stops boosting low characters "
            "through dungeons they could not survive.")
    assert verbatim.longest_run(text, SOURCE)[0] < verbatim.RUN
    assert verbatim.shingle_share(text, SOURCE) > verbatim.SHARE
    assert verbatim.copied(text, SOURCE).startswith("shares ")


def test_own_words_with_the_same_facts_pass():
    text = "A mob grey to the party's top level grants no XP to anyone, so boosted dungeon runs give nothing."
    assert verbatim.copied(text, SOURCE) == ""
    assert verbatim.copied("", SOURCE) == "" and verbatim.copied(text, "") == ""


def test_quoted_passages_are_found_in_both_quote_styles():
    assert verbatim.quoted_runs('He said "ten words" and “four more here”.') == ["ten words",
                                                                                         "four more here"]


def _texts(node, path="$"):
    if isinstance(node, dict):
        for k, v in node.items():
            if k not in NOT_TEXT:
                yield from _texts(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _texts(v, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def test_no_kb_text_quotes_more_than_fifteen_words():
    long = []
    for f in sorted(KB.rglob("*")):
        if f.suffix == ".json":
            found = _texts(json.loads(f.read_text(encoding="utf-8")))
        elif f.suffix == ".md":
            found = [("$", f.read_text(encoding="utf-8"))]
        else:
            continue
        for path, text in found:
            for q in verbatim.quoted_runs(text):
                if len(verbatim.words(q)) > verbatim.QUOTE_MAX:
                    long.append(f"{f.relative_to(ROOT)} {path}: {len(verbatim.words(q))} words quoted")
    assert long == []


class Client:
    def __init__(self, answers: list[dict]):
        self.answers, self.requests = answers, []
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kw):
        self.requests.append(kw)
        a = self.answers[min(len(self.requests), len(self.answers)) - 1]
        return SimpleNamespace(stop_reason="end_turn", usage={"input_tokens": 1_000, "output_tokens": 100},
                               content=[SimpleNamespace(type="text", text=json.dumps(a))])


class Fetch:
    def __init__(self, body: bytes):
        self.body = body

    def get(self, url):
        return 200, self.body


def test_the_refresh_pass_rewrites_a_copy_once_then_drops_it():
    state = json.loads((KB / "watch.json").read_text(encoding="utf-8"))
    # Over 200 characters of text: a shorter body is an error page to the
    # watcher and the pass (web.official_version), never read.
    page = (f"<html><body><article><p>{SOURCE}</p><p>Hotfixes, November 19, 2026. More notes follow "
            "once the realms restart.</p></article></body></html>").encode()
    copy_a = "Dungeon mobs now give no experience once they are trivial to the highest level player in the party."
    copy_b = "Fixed: dungeon mobs now give no experience once they are trivial to the highest level player."
    answers = [
        {"statements": [{"topic": "xp", "text": copy_a, "confidence": "high", "supersedes": ""},
                        {"topic": "xp", "text": copy_b, "confidence": "high", "supersedes": ""}]},
        # The rewrite: the first restated, the second copied again.
        {"texts": [{"n": 0, "text": "A grey mob for the party's top level gives no XP to anyone."},
                   {"n": 1, "text": copy_b}]}]
    client = Client(answers)
    out = refresh.run([state["official"][0]["key"]], client, Fetch(page), cap=3.0, day="2026-11-20")
    doc = out["doc"]
    assert [s["text"] for s in doc["statements"]] == ["A grey mob for the party's top level gives no XP to anyone."]
    assert kbschema.check_file(doc, kbschema.load_allow(KB / "sources.json")) == []
    assert len(client.requests) == 2 and client.requests[1]["system"] == refresh.REWORD_SYSTEM
    # The dropped copy is an open question with its link and reason, never its words.
    [q] = doc["open_questions"]
    assert "copies its source" in q["text"] and "trivial to the highest" not in q["text"]
