"""The refresh job lands its own changes (owner, 2026-11-20: "the goal is
automated refreshes"), so its gates are what stands between a model's answer
or a new addon release and main: the licence check against the text the
pass read, the multi-source rule, the churn cap, and the addon contract
checks. No network, no model, no GitHub."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from wowstats.analysis import kbschema

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools" / "kb"
TODAY = date(2026, 11, 20)


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"kbgate_{name}", TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


gates, bump, web, land = load("gates"), load("bump"), load("web"), load("land")

PAGE = "https://worldofwarcraft.blizzard.com/en-us/news/24315281"
GUIDE = "https://www.icy-veins.com/wow-forever/x"
SOURCE = ("Hunter pets no longer lose happiness while the hunter is dead, and feeding a pet now restores "
          "happiness at the same rate as in the original game.")


def stmt(i: str, text: str, url: str = PAGE, **kw) -> dict:
    return {"id": i, "text": text, "source_url": url, "source_type": "official", "date": "2026-11-20",
            "confidence": "high", "scope": "forever", "last_verified": "2026-11-20", **kw}


def lane(name: str, statements: list[dict], **kw) -> dict:
    return {"schema": 2, "lane": name, "last_verified": "2026-11-20", "rules": {}, "statements": statements,
            "contradictions": [], "open_questions": [], **kw}


@pytest.fixture
def allow():
    return kbschema.load_allow(ROOT / "data" / "kb" / "sources.json")


def test_own_words_with_their_source_read_pass():
    base = {"research/game-rules.json": lane("game-rules", [stmt("a", "Old fact.")])}
    head = {"research/game-rules.json": lane("game-rules", [stmt("a", "Old fact."), stmt(
        "b", "A dead hunter's pet keeps its happiness; feeding refills it at Classic's pace.")])}
    out, n = gates.licence_gate(base, head, {PAGE: SOURCE})
    assert out == [] and n == 1


def test_a_copied_sentence_is_refused():
    head = {"research/x.json": lane("x", [stmt("b", "Note: hunter pets no longer lose happiness while the hunter "
                                                    "is dead, and feeding a pet now restores it.")])}
    out, _ = gates.licence_gate({}, head, {PAGE: SOURCE})
    assert len(out) == 1 and "consecutive words" in out[0] and "`research/x.json` b" in out[0]


def test_new_wording_with_no_source_read_is_refused():
    head = {"research/x.json": lane("x", [stmt("b", "Something new, from nowhere this run read.")])}
    out, _ = gates.licence_gate({}, head, {})
    assert out and "no source read" in out[0]


def test_a_long_quote_is_refused():
    q = '"' + " ".join(f"w{i}" for i in range(16)) + '"'
    out, _ = gates.licence_gate({}, {"research/x.json": lane("x", [stmt("b", f"They wrote {q}.")])}, {PAGE: SOURCE})
    assert any("quotes 16 words" in o for o in out)


def test_text_already_in_the_kb_is_not_rechecked():
    """The lifecycle moves statements between files and merges them: an old
    wording in a new place is not a new text."""
    old = stmt("a", "Pets keep happiness when their hunter dies.", GUIDE, source_type="guide", confidence="medium")
    base = {"research/meta-2026-11-13.json": lane("meta-2026-11-13", [old])}
    head = {"research/game-rules.json": lane("game-rules", [{**old, "id": "a2"}])}
    assert gates.licence_gate(base, head, {}) == ([], 0)


def test_a_one_source_list_item_needs_single_source(allow):
    item = stmt("e", "Chest: Enchant Chest - Stats", GUIDE, source_type="guide", confidence="medium",
                aspect="enchants")
    head = {"research/class-meta.json": lane("class-meta", [item])}
    assert "one source" in gates.multi_source_gate({}, head, allow)[0]
    ok = {**item, "single_source": True, "confidence": "low"}
    assert gates.multi_source_gate({}, {"research/class-meta.json": lane("class-meta", [ok])}, allow) == []


def test_churn_caps_what_one_run_removes(monkeypatch):
    many = [stmt(f"s{i}", f"fact {i}") for i in range(100)]
    base = {"research/dungeons.json": lane("dungeons", many)}
    # 10 leave: 10% of the lane, fine.
    fine = {"research/dungeons.json": lane("dungeons", many[10:])}
    assert gates.churn_gate(base, fine, TODAY) == ([], 10)
    # 11: over 10% of the lane.
    out, n = gates.churn_gate(base, {"research/dungeons.json": lane("dungeons", many[11:])}, TODAY)
    assert n == 11 and "lose 11 of its 100" in out[0]
    # A broken fetch that empties a lane.
    out, _ = gates.churn_gate({"research/pvp.json": lane("pvp", many[:3])}, {"research/pvp.json": lane("pvp", [])},
                              TODAY)
    assert "all its 3" in out[0]
    monkeypatch.setattr(gates, "CHURN_MAX", 5)
    out, _ = gates.churn_gate({"research/a.json": lane("a", many)}, {"research/a.json": lane("a", many[6:])}, TODAY)
    assert "more than 5 in one run" in out[0]


def test_a_supersede_counts_as_churn_once():
    """#105: a refresh that replaces 6 statements removes 6, which the cap allows."""
    many = [stmt(f"s{i}", f"fact {i}") for i in range(60)]
    new = [stmt(f"r{i}", f"new fact {i}", supersedes=f"s{i}") for i in range(6)]
    base = {"research/game-rules.json": lane("game-rules", many)}
    head = {"research/game-rules.json": lane("game-rules", many),
            "research/refresh-2026-11-20.json": lane("refresh-2026-11-20", new)}
    assert gates.churn_gate(base, head, TODAY) == ([], 6)


def test_the_shipped_kb_passes_its_own_gates_against_itself(allow):
    head = gates.head_files()
    assert gates.schema_gate(head, allow) == []
    assert gates.licence_gate(head, head, {}) == ([], 0)
    assert gates.multi_source_gate({}, head, allow) == []
    assert gates.churn_gate(head, head, TODAY) == ([], 0)


def test_base_files_read_the_committed_kb():
    base = gates.base_files("HEAD")
    assert "research/class-meta.json" in base and "rules.json" in base and "news.json" in base


# ---------------------------------------------------------------- addon releases


class FakeRepo:
    def __init__(self, files: dict, trees: dict | None = None):
        self.files, self.trees = files, trees or {}

    def file(self, repo, path, ref):
        return self.files.get((path, ref))

    def tree(self, repo, ref):
        return self.trees.get(ref)


TOC = "## Interface: 16001\n## SavedVariables: QuestieConfig, QuestieProfilerEnabled\n" \
      "## SavedVariablesPerCharacter: QuestieConfigCharacter\n"
QUESTIE = {"name": "Questie", "repo": "Questie/Questie", "known": "old",
           "contract": {"toc": "Questie_Forever.toc",
                        "files": {"Modules/Quest/QuestieQuest.lua": ["Questie.db.char.complete = "]}}}
LUA = "    Questie.db.char.complete = QuestieCompat.GetQuestsCompleted()\n"


def test_an_addon_release_with_the_same_saved_tables_passes():
    repo = FakeRepo({("Questie_Forever.toc", "old"): TOC, ("Questie_Forever.toc", "new"): TOC.replace("16001", "16002"),
                     ("Modules/Quest/QuestieQuest.lua", "new"): LUA})
    assert bump.check_addon(repo, QUESTIE, "old", "new") == []


def test_a_renamed_saved_table_or_a_gone_line_is_refused():
    repo = FakeRepo({("Questie_Forever.toc", "old"): TOC,
                     ("Questie_Forever.toc", "new"): TOC.replace("QuestieConfig,", "QuestieConfig2,"),
                     ("Modules/Quest/QuestieQuest.lua", "new"): "-- moved\n"})
    why = bump.check_addon(repo, QUESTIE, "old", "new")
    assert len(why) == 2 and "saves other tables" in why[0] and "no longer has" in why[1]


def test_a_polestar_licence_change_is_refused():
    toc = "## SavedVariables: PolestarDB\n"
    files = {(bump.POLESTAR_TOC, "v1.0"): toc, (bump.POLESTAR_TOC, "v1.1"): toc}
    same = {"LICENSE": "a", "forever-db/NOTICE.md": "b", "addon/x.lua": "c"}
    repo = FakeRepo(files, {"v1.0": same, "v1.1": {**same, "addon/x.lua": "d"}})
    assert bump.check_polestar(repo, "fitzforge/polestar", "v1.0", "v1.1") == []
    repo.trees["v1.1"] = {**same, "forever-db/NOTICE.md": "e"}
    assert "forever-db/NOTICE.md" in bump.check_polestar(repo, "fitzforge/polestar", "v1.0", "v1.1")[0]
    repo.trees["v1.1"] = None  # GitHub cut the listing short: can't tell, so no
    assert "couldn't be listed" in bump.check_polestar(repo, "fitzforge/polestar", "v1.0", "v1.1")[0]


def test_the_tag_moves_in_the_tag_file_and_the_clone_lines(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "polestar-tag").write_text("v0.4.1\n")
    (tmp_path / "tools" / "e.py").write_text(
        "    git clone --depth 1 --branch v0.4.1 https://github.com/fitzforge/polestar /tmp/polestar\n")
    (tmp_path / "THIRD_PARTY.md").write_text("forever-db (Polestar v0.4.1, built ...) as Polestar v0.4.1 ships it."
                                             " Polestar v0.4.10 stays.\n")
    (tmp_path / "tests").mkdir()
    quoted = "git clone --depth 1 --branch v0.4.1 https://github.com/fitzforge/polestar x\n"
    (tmp_path / "tests" / "test_x.py").write_text(quoted)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    changed = bump.move_tag("v0.4.1", "v0.4.1.1", root=tmp_path)
    # 2026-11-20: the first bump rewrote this very test's quoted clone line.
    assert (tmp_path / "tests" / "test_x.py").read_text() == quoted
    assert changed == ["THIRD_PARTY.md", "tools/e.py", "tools/polestar-tag"]
    assert (tmp_path / "tools" / "polestar-tag").read_text() == "v0.4.1.1\n"
    assert "--branch v0.4.1.1 https" in (tmp_path / "tools" / "e.py").read_text()
    assert (tmp_path / "THIRD_PARTY.md").read_text().count("Polestar v0.4.1.1") == 2
    assert "Polestar v0.4.10 stays" in (tmp_path / "THIRD_PARTY.md").read_text()


def test_regenerated_data_may_differ_by_the_polestar_version_only():
    import gzip as gz

    old = gz.compress(b'{"tag": "v0.4.1", "polestar": "0.4.1", "src": "Polestar 0.4.1 route planner", "s": 512}')
    same = gz.compress(b'{"tag": "v0.4.1.1", "polestar": "0.4.1.1", "src": "Polestar 0.4.1.1 route planner", "s": 512}')
    moved = gz.compress(b'{"tag": "v0.4.1.1", "polestar": "0.4.1.1", "src": "Polestar 0.4.1.1 route planner", "s": 498}')
    v = ["v0.4.1", "v0.4.1.1"]
    assert bump._normal(old, v) == bump._normal(same, v)
    assert bump._normal(old, v) != bump._normal(moved, v)
    # Another number that merely looks like the version stays a number.
    assert "10.4.1" in bump._normal(b"10.4.1 and 0.4.1", v)


def test_the_shipped_tag_file_names_the_known_release():
    state = json.loads((ROOT / "data" / "kb" / "watch.json").read_text())
    assert (ROOT / "tools" / "polestar-tag").read_text().strip() == state["polestar"]["known"]


def test_a_refused_pr_says_why_and_links_itself():
    t = land.why_text("refused", 12, "", "**Gates: refused.**\n\n- churn: x\n")
    assert t.startswith("PR #12 is open, not merged: the gates refused it.") and "- churn: x" in t
    assert "wasn't merged: boom" in land.why_text("failed", 12, "boom", "")


def test_remembered_sources_join_every_read_of_a_url(tmp_path, monkeypatch):
    p = tmp_path / "s.jsonl"
    monkeypatch.setenv("KB_SOURCES", str(p))
    web.remember(PAGE, "first")
    web.remember(PAGE, "second")
    web.remember(GUIDE, "")
    assert web.remembered(str(p)) == {PAGE: "first\n\nsecond"}
