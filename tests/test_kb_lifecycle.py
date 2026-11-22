"""The knowledge base's lifecycle (tools/kb/lifecycle.py): a pass's dated file
folds into the lanes, superseded and expired statements leave, one claim from
two sources becomes one statement with both citations, the digests keep 8
weeks and a one-line history, stale statements are re-verified, and the
shipped KB is already what the weekly job would make of it."""

from __future__ import annotations

import copy
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "kb"))

import lifecycle  # noqa: E402
import meta  # noqa: E402
import refresh  # noqa: E402

from wowstats.analysis import kbschema  # noqa: E402

DATA = ROOT / "data" / "kb"
ALLOW = kbschema.load_allow(DATA / "sources.json")
TODAY = date(2026, 11, 20)
OFFICIAL = "https://us.forums.blizzard.com/en/wow/t/wow-forever-hotfixes/1"
PRESS = "https://www.wowhead.com/news=1/x"
GUIDE = "https://www.warcrafttavern.com/forever/guides/dungeons/"


def st(i, text, url=GUIDE, typ="guide", conf="medium", day="2026-11-10", **kw):
    return {"id": i, "text": text, "source_url": url, "source_type": typ, "date": day, "confidence": conf,
            "scope": "forever", "last_verified": "2026-11-18", **kw}


def lane(name, statements=(), contradictions=(), **kw):
    return {"schema": 2, "lane": name, "title": name, "about": "", "last_verified": "2026-11-18", "rules": {},
            "statements": list(statements), "contradictions": list(contradictions), "open_questions": [], **kw}


def tree(lanes=(), dated=(), digests=(), history=()):
    return {"lanes": {d["lane"]: d for d in lanes}, "dated": {d["lane"]: d for d in dated},
            "news": {"about": "", "digests": list(digests)}, "history": {"about": "", "weeks": list(history)},
            "community": None}


def ids(t, name, key="statements"):
    return [r["id"] for r in t["lanes"][name][key]]


DUNGEON = "Wailing Caverns quest items drop for every party member once the boss is down."
MAELSTROM = "Maelstrom Weapon procs 6/7/8/9/10 times per minute by rank, instead of 20 per minute."


def test_a_weekly_pass_folds_into_its_lanes_and_its_file_goes():
    weekly = lane("meta-2026-11-19", [
        st("meta-2026-11-19:n-1", "Dungeon quest items now drop round-robin among party members.", PRESS, "press",
           topic="dungeons", origin="model proposal"),
        st("meta-2026-11-19:n-2", MAELSTROM, PRESS, "press", topic="class balance", **{"class": "Shaman"}),
        st("meta-2026-11-19:n-3", "Might of Stormwind now ends on death for every player.", OFFICIAL, "official",
           "high", topic="world buffs"),
        st("meta-2026-11-19:n-4", "Combat rules: stun diminishing returns now apply to creatures too.", OFFICIAL,
           "official", "high", topic="Combat rules")],
        [st("meta-2026-11-19:n-1:contradiction", "Round-robin. This contradicts dungeons:1.", PRESS, "press",
            contradicts="dungeons:1")],
        digest={"week": "2026-11-19", "from": "2026-11-12", "items": [
            {"id": "digest-2026-11-19:1", "category": "hotfix", "title": "Round-robin", "text": "x",
             "statements": ["meta-2026-11-19:n-1"]}]})
    weekly["open_questions"] = [{"id": "meta-2026-11-19:refused-1", "text": "Refused proposal: x (copies)"}]
    refreshed = lane("refresh-2026-11-19", [
        st("refresh-2026-11-19:hotfixes-1", "Deadmines now needs level 17 to enter.", OFFICIAL, "official", "high",
           topic="dungeons", supersedes="dungeons:2")])
    t = tree([lane("dungeons", [st("dungeons:1", DUNGEON), st("dungeons:2", "Deadmines needs level 15.")]),
              lane("class-meta"), lane("raids-endgame"), lane("game-rules")], [weekly, refreshed])
    r = lifecycle.run(t, TODAY)
    assert r["folded"] == ["meta-2026-11-19", "refresh-2026-11-19"] and t["folded"] == r["folded"]
    # The replacement takes the old one's place; the topic picks the lane.
    assert ids(t, "dungeons") == ["dungeons:1", "refresh-2026-11-19:hotfixes-1", "meta-2026-11-19:n-1"]
    assert "supersedes" not in t["lanes"]["dungeons"]["statements"][1]
    assert "origin" not in t["lanes"]["dungeons"]["statements"][2]
    [shift] = t["lanes"]["class-meta"]["statements"]
    assert shift["id"] == "meta-2026-11-19:n-2" and shift["aspect"] == "shift"  # wowstats.classmeta's shifts
    assert ids(t, "raids-endgame") == ["meta-2026-11-19:n-3"]
    assert ids(t, "game-rules") == ["meta-2026-11-19:n-4"]  # no lane's word: the catch-all
    assert ids(t, "dungeons", "contradictions") == ["meta-2026-11-19:n-1:contradiction"]
    assert [w["week"] for w in t["news"]["digests"]] == ["2026-11-19"]
    assert r["refused"] == ["Refused proposal: x (copies)"] and r["superseded"] == ["dungeons:2"]


def test_superseded_and_expired_statements_leave():
    t = tree([lane("dungeons", [
        st("dungeons:1", "Old rule.", superseded_by="dungeons:2"),
        st("dungeons:2", "New rule."),
        st("dungeons:3", "Waits for a replacement that isn't there yet.", superseded_by="dungeons:9"),
        st("dungeons:4", "Hallow's End runs to the first of November.", expires="2026-11-01"),
        st("dungeons:5", "Inside the grace week.", expires="2026-11-15"),
        st("dungeons:6", "Known issue: a cauldron in Razorfen Kraul can trap players.", day="2026-09-01"),
        st("dungeons:7", "Known issue: a door in Gnomeregan stays shut.", day="2026-11-19")])])
    r = lifecycle.run(t, TODAY)
    assert ids(t, "dungeons") == ["dungeons:2", "dungeons:3", "dungeons:5", "dungeons:7"]
    assert r["superseded"] == ["dungeons:1"] and r["expired"] == ["dungeons:4", "dungeons:6"]
    # A known issue lives 8 weeks unless the next pass sees it again.
    assert t["lanes"]["dungeons"]["statements"][3]["expires"] == "2027-01-14"
    assert kbschema.expired({"expires": "2026-11-12"}, TODAY) and not kbschema.expired({"expires": "2026-11-13"},
                                                                                      TODAY)


def test_one_claim_from_two_sources_becomes_one_statement():
    t = tree([lane("class-meta", [
        st("meta-2026-11-19:n-2", MAELSTROM, PRESS, "press", "medium", **{"class": "Shaman", "aspect": "shift"}),
        st("meta-2026-11-19:b-7", "Each stack: Maelstrom Weapon procs 6/7/8/9/10 times per minute by rank, "
           "instead of 20 per minute.", OFFICIAL, "official", "high", **{"class": "SHAMAN", "aspect": "shift"}),
        # Same words, other numbers: a change, never a duplicate.
        st("class-meta:old", MAELSTROM.replace("20", "15"), PRESS, "press", **{"class": "Shaman", "aspect": "shift"}),
    ]), lane("raids-endgame", [st("raids:1", "Lockout is weekly."), st("raids:2", "Lockout is weekly.")])],
        digests=[{"week": "2026-11-19", "items": [{"id": "d:1", "statements": ["meta-2026-11-19:n-2", "gone:1"]}]}])
    r = lifecycle.run(t, TODAY)
    assert r["merged"] == ["meta-2026-11-19:n-2 -> meta-2026-11-19:b-7"]
    kept = t["lanes"]["class-meta"]["statements"][0]
    assert kept["id"] == "meta-2026-11-19:b-7" and kept["sources"] == [OFFICIAL, PRESS] and kept["confidence"] == "high"
    assert ids(t, "class-meta") == ["meta-2026-11-19:b-7", "class-meta:old"]
    # Too short to name its subject: two raids, two statements.
    assert ids(t, "raids-endgame") == ["raids:1", "raids:2"]
    # References follow the merge; a reference to nothing goes.
    assert t["news"]["digests"][0]["items"][0]["statements"] == ["meta-2026-11-19:b-7"]
    assert kbschema.check_file(t["lanes"]["class-meta"], ALLOW) == []


def test_a_contradiction_with_nothing_left_to_contradict_goes():
    t = tree([lane("dungeons", [st("dungeons:1", "Old.", superseded_by="dungeons:2"), st("dungeons:2", "New.")],
                   [st("c:1", "x contradicts dungeons:1", contradicts="dungeons:1"),
                    st("c:2", "y contradicts dungeons:2", contradicts="dungeons:2"), st("c:3", "an imported one")])])
    r = lifecycle.run(t, TODAY)
    assert ids(t, "dungeons", "contradictions") == ["c:2", "c:3"] and r["resolved_contradictions"] == ["c:1"]


def week(day, *items):
    return {"week": day, "from": day, "items": [{"id": f"d-{day}:{i}", **it} for i, it in enumerate(items, 1)]}


def test_digests_keep_eight_weeks_and_a_line_for_older_ones():
    weeks = [week(f"2026-{m:02d}-01", {"category": "patch", "title": f"Patch of month {m}", "date": f"2026-{m:02d}-01"},
                  {"category": "known-issue", "title": "A bug", "date": f"2026-{m:02d}-01"})
             for m in range(1, 12)]
    weeks[0]["items"].append({"category": "event", "title": "Winter raid", "date": "2026-01-01",
                              "event": {"key": "winter-raid", "name": "Winter raid", "starts": "2026-12-09"}})
    weeks[1]["items"] = [{"id": "d:x", "category": "known-issue", "title": "Only a bug", "date": "2026-02-01"}]
    t = tree(digests=weeks, history=[{"week": "2025-12-01", "line": "Older."}])
    r = lifecycle.run(t, TODAY)
    kept = t["news"]["digests"]
    assert [w["week"] for w in kept] == [f"2026-{m:02d}-01" for m in range(11, 3, -1)] + ["2026-01-01"]
    # Past the 8 weeks, only the event still to come stays (wowstats.news shows it).
    assert [it["title"] for it in kept[-1]["items"]] == ["Winter raid"]
    assert r["compacted"] == ["2026-03-01", "2026-02-01", "2026-01-01"]
    lines = t["history"]["weeks"]
    # A week of known issues alone says nothing lasting: no line.
    assert [x["week"] for x in lines] == ["2026-03-01", "2026-01-01", "2025-12-01"]
    assert lines[0]["line"] == "Patch of month 3."
    t["history"]["weeks"] = [{"week": f"2020-{i:03d}", "line": "x"} for i in range(80)]
    lifecycle.window(t, TODAY, lifecycle.new_report())
    assert len(t["history"]["weeks"]) == lifecycle.HISTORY_LINES


def test_a_history_line_stays_short():
    w = week("2026-11-19", *[{"category": "hotfix", "title": f"A fairly long hotfix title number {i}"}
                             for i in range(20)])
    line = lifecycle.history_line(w)
    assert len(line) <= lifecycle.HISTORY_CHARS and line.endswith("more.")


def test_community_keeps_eight_weeks_when_it_is_there():
    t = tree()
    t["community"] = {"weeks": [{"week": f"2026-{m:02d}-01", "items": [{"title": "t"}]} for m in range(1, 12)]}
    lifecycle.window(t, TODAY, lifecycle.new_report())
    assert len(t["community"]["weeks"]) == 8 and t["community"]["weeks"][0]["week"] == "2026-11-01"


class Fetch:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def get(self, url):
        self.asked.append(url)
        return self.pages.get(url, (0, b""))


class Usage:
    input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens = 2000, 200, 0, 0


class Client:
    def __init__(self, verdicts):
        self.messages, self.verdicts, self.prompts = self, verdicts, []

    def create(self, **kw):
        self.prompts.append(kw["messages"][0]["content"])

        class R:
            stop_reason, usage = "end_turn", Usage()
            content = [type("B", (), {"type": "text", "text": json.dumps({"verdicts": self.verdicts})})()]
        return R()


def stale_tree():
    old = {"last_verified": "2026-05-01"}
    return tree([lane("dungeons", [
        st("d:gone", "A page that is gone.", "https://www.warcrafttavern.com/gone/", **old),
        st("d:holds", "Still true on the page.", GUIDE, **old),
        st("d:wrong", "Now wrong on the page.", GUIDE, **old),
        st("d:unsure", "The page no longer speaks of it.", GUIDE, **old),
        st("d:fresh", "Checked last week.", GUIDE)])])


def test_stale_statements_are_reverified_by_their_source():
    t = stale_tree()
    fetch = Fetch({"https://www.warcrafttavern.com/gone/": (404, b""),
                   GUIDE: (200, b"<main>Dungeon guide text</main>")})
    client = Client([{"id": "d:holds", "verdict": "holds", "why": ""},
                     {"id": "d:wrong", "verdict": "contradicted", "why": "level 20 now"},
                     {"id": "d:unsure", "verdict": "not_found", "why": ""}])
    r = lifecycle.run(t, TODAY, fetch, client, refresh.Meter(3.0))
    assert ids(t, "dungeons") == ["d:holds", "d:unsure", "d:fresh"]
    assert t["lanes"]["dungeons"]["statements"][0]["last_verified"] == "2026-11-20"
    assert t["lanes"]["dungeons"]["statements"][1]["last_verified"] == "2026-05-01"
    assert sorted(r["retired"]) == ["d:gone: every source gone", "d:wrong: its source now says otherwise"]
    assert r["reverified"] == ["d:holds"] and r["unverified"] == ["d:unsure"]
    assert len(client.prompts) == 1 and "Dungeon guide text" in client.prompts[0]  # one call per page
    assert "Checked last week." not in client.prompts[0]


def test_without_a_model_stale_statements_are_kept_and_listed():
    t = stale_tree()
    r = lifecycle.run(t, TODAY, Fetch({GUIDE: (200, b"text")}))
    assert ids(t, "dungeons") == ["d:gone", "d:holds", "d:wrong", "d:unsure", "d:fresh"]
    # A page that didn't answer (0: network, 403, 429) is not a page that's gone.
    assert r["unverified"] == ["d:gone", "d:holds", "d:wrong", "d:unsure"] and r["retired"] == []


def test_one_record_per_line_reads_back_the_same():
    for p in sorted(DATA.rglob("*.json")):
        doc = json.loads(p.read_text(encoding="utf-8"))
        assert json.loads(lifecycle.dumps(doc)) == doc, p.name
    doc = json.loads((DATA / "research" / "dungeons.json").read_text(encoding="utf-8"))
    text = lifecycle.dumps(doc)
    assert sum(1 for line in text.splitlines() if line.lstrip().startswith('{"id":')) == \
        len(doc["statements"]) + len(doc["contradictions"]) + len(doc["open_questions"])


def test_the_shipped_kb_is_what_the_weekly_job_makes_of_it(tmp_path):
    """No dated file, no duplicate, nothing expired or dangling, every file
    in the job's format: running it changes nothing."""
    assert not [p.name for p in (DATA / "research").glob("*.json") if lifecycle.DATED.match(p.stem)]
    t = lifecycle.load(DATA)
    before = copy.deepcopy(t)
    r = lifecycle.run(t, TODAY)
    assert {k: v for k, v in r.items() if v} == {}
    assert t["lanes"] == before["lanes"] and t["news"] == before["news"]
    for name, doc in t["lanes"].items():
        assert (DATA / "research" / f"{name}.json").read_text(encoding="utf-8") == lifecycle.dumps(doc) + "\n"
    assert (DATA / "news.json").read_text(encoding="utf-8") == lifecycle.dumps(t["news"]) + "\n"


def test_news_json_is_sourced_like_a_lane():
    doc = json.loads((DATA / "news.json").read_text(encoding="utf-8"))
    assert doc["digests"] and len(doc["digests"]) <= lifecycle.DIGEST_WEEKS
    assert kbschema.check_file(doc, ALLOW) == []


def test_the_meta_pass_keeps_an_expiry_only_when_it_is_a_real_day():
    it = {"url": PRESS, "source": "wowhead-news", "source_type": "press", "date": "2026-11-18",
          "max_confidence": "medium", "title": "t", "text": "An item about a weekend bonus."}

    def rec(expires):
        p = {"item": 1, "topic": "levelling", "text": "Bonus experience runs this weekend.", "class": "",
             "aspect": "", "spec": "", "confidence": "medium", "contradicts": "", "why": "", "expires": expires}
        return meta.to_records([p], [it], ALLOW, "2026-11-23", {}, {"n": 0})[0][0]

    assert rec("2026-11-24")["expires"] == "2026-11-24"
    for bad in ("", "next week", "2026-11-01", "2030-01-01"):
        assert "expires" not in rec(bad), bad


@pytest.mark.parametrize("topic,lane_", [("dungeons", "dungeons"), ("Dungeon quests", "dungeons"),
                                         ("raid attunement", "raids-endgame"), ("PvP honor", "pvp"),
                                         ("Professions", "economy-professions"), ("bags", "levelling"),
                                         ("Combat rules", "game-rules"), ("", "game-rules")])
def test_a_topic_finds_its_lane(topic, lane_):
    assert lifecycle.lane_for({"topic": topic}, lifecycle.LANES) == lane_


def test_a_page_unchanged_since_it_held_is_reverified_without_the_model():
    """2026-11-22: re-verification asks the model only about a page that
    changed since the model last said it holds the statement."""
    body = b"<main>Dungeon guide text</main>"
    t = stale_tree()
    client = Client([{"id": "d:holds", "verdict": "holds", "why": ""},
                     {"id": "d:wrong", "verdict": "holds", "why": ""},
                     {"id": "d:unsure", "verdict": "not_found", "why": ""}])
    memo = {}
    lifecycle.run(t, TODAY, Fetch({GUIDE: (200, body)}), client, refresh.Meter(3.0), memo)
    assert len(client.prompts) == 1 and memo[GUIDE]["ids"] == ["d:holds", "d:wrong"]
    # 120 days on, same page: no call for what held; the one it never confirmed is asked again.
    t = stale_tree()
    client = Client([{"id": "d:unsure", "verdict": "holds", "why": ""}])
    r = lifecycle.run(t, TODAY, Fetch({GUIDE: (200, body)}), client, refresh.Meter(3.0), memo)
    assert sorted(r["unchanged"]) == ["d:holds", "d:wrong"] and r["reverified"] == ["d:unsure"]
    assert len(client.prompts) == 1 and "d:holds" not in json.dumps(client.prompts[0])
    assert memo[GUIDE]["ids"] == ["d:holds", "d:unsure", "d:wrong"]
    # Nothing left to ask: no call at all.
    t = stale_tree()
    client = Client([])
    r = lifecycle.run(t, TODAY, Fetch({GUIDE: (200, body)}), client, refresh.Meter(3.0), memo)
    assert client.prompts == [] and len(r["unchanged"]) == 3
    # The page changed: the model reads it again.
    t = stale_tree()
    client = Client([{"id": i, "verdict": "holds", "why": ""} for i in ("d:holds", "d:wrong", "d:unsure")])
    r = lifecycle.run(t, TODAY, Fetch({GUIDE: (200, b"<main>Rewritten guide</main>")}), client,
                      refresh.Meter(3.0), memo)
    assert len(client.prompts) == 1 and r["unchanged"] == []
