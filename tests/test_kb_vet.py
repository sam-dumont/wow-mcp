"""The refresh job checks a model pass's records before its PR
(tools/kb/vet.py): a record a gate would refuse is dropped from the pass's
file, never committed, and named in the PR under "Dropped"; the rest folds
as before. 2026-11-22 (#99): two flat records replaced shaman class-guide
records, the PR opened red and sat there for hours."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools" / "kb"


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"kbvet_{name}", TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


vet, gates, lifecycle = load("vet"), load("gates"), load("lifecycle")

PAGE = "https://worldofwarcraft.blizzard.com/en-us/news/24315281"
PAGE_TEXT = ("Shaman. Stormstrike now deals 100% weapon damage and its cooldown is 8 seconds, down from 150% "
             "weapon damage on a 6 second cooldown. Hunter pets no longer lose happiness while the hunter is dead.")
DAY = "2026-11-22"


def proposal(i: int, text: str, **kw) -> dict:
    return {"id": f"refresh-{DAY}:hotfixes-{i}", "topic": "game rules", "text": text, "source_url": PAGE,
            "source_type": "official", "date": DAY, "confidence": "high", "scope": "forever", "last_verified": DAY,
            "origin": "model proposal from the official page", **kw}


def test_a_record_the_gates_refuse_is_dropped_and_the_rest_folds(tmp_path):
    kb = tmp_path / "kb"
    shutil.copytree(ROOT / "data" / "kb", kb)
    sources = {PAGE: PAGE_TEXT}
    shaman = next(s for s in json.loads((kb / "research" / "classes.json").read_text())["statements"]
                  if s["id"] == "classes:shaman:3")
    flat = proposal(1, "Since 10 Nov Stormstrike hits for all of the weapon's damage every eight seconds.",
                    topic="shaman", supersedes="classes:shaman:3")
    good = proposal(2, "A dead hunter's pet keeps its happiness since the 10 Nov hotfix.")
    doc = {"schema": 2, "lane": f"refresh-{DAY}", "rules": {}, "statements": [flat, good], "contradictions": [],
           "open_questions": []}
    (kb / "research" / f"refresh-{DAY}.json").write_text(json.dumps(doc))

    dropped = vet.vet(kb, sources, date(2026, 11, 22))

    assert [d["id"] for d in dropped] == [flat["id"]]
    assert "class" in dropped[0]["why"]
    kept = json.loads((kb / "research" / f"refresh-{DAY}.json").read_text())
    assert [s["id"] for s in kept["statements"]] == [good["id"]]
    # What the job then folds and gates: the guide's record untouched, the good one in.
    before = gates.head_files(kb)
    before.pop(f"research/refresh-{DAY}.json")
    tree = lifecycle.load(kb)
    lifecycle.run(tree, date(2026, 11, 22))
    lifecycle.write(tree, kb)
    after = gates.head_files(kb)
    assert gates.record_problems(before, after, sources, gates.kbschema.load_allow(kb / "sources.json")) == []
    assert shaman in after["research/classes.json"]["statements"]
    assert any(s["id"] == good["id"] for d in after.values() for s in d.get("statements") or [])


def test_the_dropped_section_names_each_record_and_why(tmp_path):
    md = vet.markdown([{"id": "refresh-x:1", "file": "research/classes.json",
                        "why": "schema: statement refresh-x:1: class None is not one of the guide's classes"}])
    assert md.startswith("## Dropped\n") and "`refresh-x:1`" in md and "class None" in md
    assert vet.markdown([]) == ""


def test_no_model_pass_nothing_to_vet(tmp_path):
    kb = tmp_path / "kb"
    shutil.copytree(ROOT / "data" / "kb", kb)
    assert vet.vet(kb, {}, date(2026, 11, 22)) == []
