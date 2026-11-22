"""The Community corner (tools/kb/community.py, wowstats.news.community):
what players are saying on the official forums and r/wowforever, kept as
titles, links, counts and our own one line, never as facts.

Synthetic forum answers and search results (shaped like the real ones,
2026-11-20); no network, no model."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from wowstats import classmeta as cm
from wowstats import news

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "kb"))
import community  # noqa: E402

TODAY = date(2026, 11, 20)
SINCE = "2026-11-13"
# Names that must never come out: the posters' handles and aliases.
NAMES = ("Zorbulon", "Quietleaf-mankrik", "Grimpaw", "throwaway_tank99")


def topic(tid, title, posts=10, likes=5, created="2026-11-17T10:00:00.000Z", **kw):
    return {"id": tid, "title": title, "fancy_title": title, "slug": title.lower().replace(" ", "-")[:40],
            "posts_count": posts, "reply_count": posts - 1, "like_count": likes, "views": 500,
            "created_at": created, "pinned": False, "pinned_globally": False, "visible": True,
            "archetype": "regular", "excerpt": "I think the change to dungeon experience went too far for "
                                               "people who level in groups with friends every evening.",
            "last_poster_username": "Grimpaw", "topic_creator": {"alias_name": "Quietleaf-mankrik", "alias_id": 7},
            "posters": [{"description": "Original Poster", "user_id": 42}], "first_tracked_post": None, **kw}


US = {"users": [{"id": 42, "username": "Zorbulon", "avatar_template": "/a/{size}.png"}],
      "topic_list": {"topics": [
          topic(101, "Dungeon XP change went too far", posts=300, likes=900),
          topic(102, "Hunter pets feel weak after the patch", posts=40, likes=60),
          # Opened by a Blizzard poster: official, left to the blue-post feed.
          topic(103, "Actions for the exploit", posts=2000, likes=9000,
                first_tracked_post={"group": "community-manager", "post_number": 1}),
          topic(104, "Welcome to the forums", posts=5, likes=1, pinned=True),
          topic(105, "Last month's thread", posts=900, likes=900, created="2026-10-01T10:00:00.000Z"),
          topic(106, "Shout out to u/someone for the guide", posts=80, likes=80),
      ]}}
EU = {"topic_list": {"topics": [topic(201, "EU realms queue again", posts=120, likes=200)]}}


class Fetch:
    def __init__(self, pages=None):
        self.pages = pages if pages is not None else {community.FORUMS[0]["url"]: US, community.FORUMS[1]["url"]: EU}
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        if url not in self.pages:
            return 404, b""
        return 200, json.dumps(self.pages[url]).encode()


def result(url, title, age):
    return SimpleNamespace(type="web_search_result", url=url, title=title, page_age=age, encrypted_content="x")


THREAD = "https://www.reddit.com/r/wowforever/comments/1abcde/onyxia_prep_megathread/"
OLD = "https://www.reddit.com/r/wowforever/comments/0zzzzz/two_years_ago/"
OTHER = "https://www.reddit.com/r/classicwow/comments/1xyz/other_sub/"


class Model:
    """A fake Anthropic client: one search turn with results and a JSON answer."""

    def __init__(self, answer, results=None, searches=3):
        self.answer, self.requests, self.searches = answer, [], searches
        self.results = results if results is not None else [
            result(THREAD, "Onyxia prep megathread : r/wowforever", "November 18, 2026"),
            result(OLD, "Two years ago : r/wowforever", "March 3, 2024"),
            result(OTHER, "Other sub", "November 18, 2026"),
        ]
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kw):
        self.requests.append(kw)
        return SimpleNamespace(
            stop_reason="end_turn",
            usage={"input_tokens": 20000, "output_tokens": 500, "server_tool_use": {"web_search_requests":
                                                                                    self.searches}},
            content=[SimpleNamespace(type="server_tool_use", name="web_search", input={"query": "x"}),
                     SimpleNamespace(type="web_search_tool_result", content=self.results),
                     SimpleNamespace(type="text", text=json.dumps(self.answer))])


# Threads are numbered in the order the model gets them: the heaviest first.
ANSWER = {"forums": [{"n": 1, "line": "Players say the group levelling nerf hurts people who play with friends."},
                     {"n": 2, "line": "Players complain about EU realm queues."},
                     {"n": 3, "line": "Hunters ask whether their pets got weaker."}],
          "reddit": [{"url": THREAD, "line": "Players swap notes on getting ready for the raid."},
                     {"url": OLD, "line": "An old thread."},
                     {"url": OTHER, "line": "Another subreddit."},
                     {"url": "https://www.reddit.com/r/wowforever/comments/9made/up/", "line": "Not a result."}]}


@pytest.fixture
def no_digest(monkeypatch):
    monkeypatch.setattr(cm, "lanes", lambda: {"meta": {}, "weekly": []})


# ---------------------------------------------------------------- what is kept


def test_the_forums_keep_players_threads_of_the_week_only():
    threads, notes = community.forum_threads(Fetch(), SINCE)
    assert notes == []
    ids = [t["id"] for t in threads]
    # Heaviest first; staff-opened, pinned, old and username titles left out.
    assert ids == ["forums-us:101", "forums-eu:201", "forums-us:102"]
    t = threads[0]
    assert t["url"] == "https://us.forums.blizzard.com/en/wow/t/dungeon-xp-change-went-too-far/101"
    assert (t["replies"], t["likes"], t["date"]) == (299, 900, "2026-11-17")


def test_a_fixture_answer_produces_the_stored_fields_only(no_digest):
    model = Model(ANSWER)
    items, out = community.run(Fetch(), model, cap=1.0, today=TODAY, searches=["Onyxia's Lair"])
    assert len(items) <= community.MAX_ITEMS
    by = {i["id"]: i for i in items}
    assert set(by) == {"forums-us:101", "forums-eu:201", "forums-us:102", "reddit:1abcde"}
    allowed = {"id", "source", "title", "url", "date", "replies", "likes", "text", "classes"}
    for it in items:
        assert set(it) <= allowed, set(it) - allowed
    r = by["reddit:1abcde"]
    assert r == {"id": "reddit:1abcde", "source": "reddit", "title": "Onyxia prep megathread", "url": THREAD,
                 "date": "2026-11-18", "text": "Players swap notes on getting ready for the raid.", "classes": []}
    assert by["forums-us:102"]["classes"] == ["HUNTER"]
    # The search: restricted to the subreddit, capped, never a fetch.
    tool = model.requests[0]["tools"][0]
    assert tool["type"].startswith("web_search") and tool["allowed_domains"] == ["reddit.com/r/wowforever"]
    assert tool["max_uses"] == 2
    assert "web_fetch" not in json.dumps(model.requests[0]["tools"])
    # Tokens plus 3 searches at a cent each.
    assert out["spent_usd"] == pytest.approx((20000 * 4 + 500 * 20) / 1e6 + 0.03)


def test_lines_that_copy_or_name_a_user_are_dropped(no_digest):
    answer = {"forums": [{"n": 1, "line": "I think the change to dungeon experience went too far for people"},
                         {"n": 2, "line": "Players thank u/throwaway_tank99 for a pet guide."}],
              "reddit": [{"url": THREAD, "line": "Players thank u/throwaway_tank99 for the list."}]}
    items, _ = community.run(Fetch(), Model(answer), cap=1.0, today=TODAY, searches=[])
    assert all(not i.get("text") for i in items if i["source"].startswith("forums"))
    assert not any(i["source"] == "reddit" for i in items)


def test_missing_model_credentials_keep_forum_titles_and_skip_reddit(no_digest, monkeypatch):
    for k in ("ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert community.has_model() is False
    items, out = community.run(Fetch(), None, cap=1.0, today=TODAY, searches=["Onyxia's Lair"])
    assert "skipped" in out["model"]
    assert items and all(i["source"].startswith("forums") and i["text"] is None for i in items)


def test_a_forum_down_is_a_note_not_a_failure(no_digest):
    items, out = community.run(Fetch(pages={}), None, cap=1.0, today=TODAY, searches=[])
    assert items == [] and out["notes"] == ["forums-us: HTTP 404", "forums-eu: HTTP 404"]


def test_the_week_file_keeps_four_weeks_and_never_repeats_a_thread():
    old = {"weeks": [{"week": "2026-10-01", "items": [{"id": "forums-us:1"}]},
                     {"week": "2026-11-13", "items": [{"id": "forums-us:101", "title": "x"}]}]}
    items = [{"id": "forums-us:101"}, {"id": "forums-us:102"}]
    doc = community.update(old, items, TODAY)
    assert [w["week"] for w in doc["weeks"]] == ["2026-11-20", "2026-11-13"]
    assert [i["id"] for i in doc["weeks"][0]["items"]] == ["forums-us:102"]
    assert doc["schema"] == 1 and "never the source of a date" in doc["about"]


def test_the_searches_come_from_the_digest(monkeypatch):
    monkeypatch.setattr(news, "events", lambda today=None: [
        {"name": "Barrow Deeps, Hyjal Summit and Onyxia's Lair open"}, {"name": "Spring 2027 update"},
        {"name": "Hardcore ruleset"}])
    monkeypatch.setattr(news, "weeks", lambda: [{"week": "2026-11-19", "items": [
        {"category": "class", "classes": ["MAGE"]}, {"category": "hotfix", "classes": ["ROGUE"]}]}])
    assert community.topics(TODAY) == ["Barrow Deeps", "Hyjal Summit", "Onyxia's Lair", "Mage", "Hardcore"]


# ---------------------------------------------------------------- in the digest


def stored(monkeypatch, weeks):
    monkeypatch.setattr(news, "community_weeks", lambda: weeks)


COMMUNITY_WEEK = {"week": "2026-11-19", "from": "2026-11-12", "items": [
    {"id": "forums-us:101", "source": "forums-us", "title": "Dungeon XP change went too far",
     "url": "https://us.forums.blizzard.com/en/wow/t/x/101", "date": "2026-11-17", "replies": 299, "likes": 900,
     "text": "Players say the group levelling nerf hurts.", "classes": []},
    {"id": "reddit:1abcde", "source": "reddit", "title": "Mage AoE spots now", "url": THREAD,
     "date": "2026-11-18", "text": "Mages trade AoE farming spots.", "classes": ["MAGE"]},
]}


SRC = {"source_url": "https://worldofwarcraft.blizzard.com/en-us/news/1", "source_type": "official",
       "confidence": "high", "scope": "forever", "last_verified": "2026-11-20"}
NEWS_WEEK = {"digest": {"week": "2026-11-19", "from": "2026-11-12", "items": [
    {**SRC, "id": "d:1", "category": "hotfix", "title": "Early bags", "text": "Six-slot bags.", "date": "2026-11-18"},
    {**SRC, "id": "d:2", "category": "opening", "title": "Raid", "text": "A raid opens.", "date": "2026-11-18",
     "event": {"key": "raid", "name": "Raid opening", "starts": "2026-12-09", "levels": [60, 60], "prep": []}},
]}}


def test_the_digest_shows_community_apart_ranked_and_never_as_an_event(monkeypatch):
    monkeypatch.setattr(cm, "lanes", lambda: {"meta": {}, "weekly": [NEWS_WEEK]})
    before = news.events(TODAY)
    stored(monkeypatch, [COMMUNITY_WEEK])
    d = news.digest(chars=[{"class": "Mage", "level": 40}], today=TODAY)
    assert all(i["category"] != "community" for i in d["items"])
    said = d["community"]
    # The mage's own class first, then the most discussed.
    assert [c["id"] for c in said] == ["reddit:1abcde", "forums-us:101"]
    assert said[0]["for_you"] is True and said[0]["where"] == "r/wowforever"
    assert said[1]["where"] == "Blizzard forums (US)" and said[1]["url"].startswith("https://")
    assert all(c["category"] == "community" and "event" not in c for c in said)
    assert news.events(TODAY) == before
    only = news.digest(category="community", today=TODAY)
    assert [i["category"] for i in only["items"]] == ["community", "community"] and only["community"] == []
    hot = news.digest(category="hotfix", today=TODAY)
    assert hot["community"] == [] and all(i["category"] == "hotfix" for i in hot["items"])
    with pytest.raises(ValueError):
        news.digest(category="rumours", today=TODAY)


def test_community_goes_stale_and_caps_at_five(monkeypatch, no_digest):
    many = {"week": "2026-11-19", "items": [{**COMMUNITY_WEEK["items"][0], "id": f"forums-us:{n}"}
                                            for n in range(8)]}
    stored(monkeypatch, [many])
    d = news.digest(today=TODAY)
    assert d["available"] is True and len(d["community"]) == news.COMMUNITY_MAX
    assert news.digest(today=date(2026, 12, 20))["community"] == []


def test_the_mcp_news_tool_filters_community(monkeypatch, no_digest):
    from wowstats.mcp import classtools

    stored(monkeypatch, [COMMUNITY_WEEK])
    monkeypatch.setattr(news, "_today", lambda today=None: TODAY)
    ctx = SimpleNamespace(default_char=None)
    out = classtools.t_news(ctx, {"category": "community"})
    assert [i["id"] for i in out["items"]] == ["forums-us:101", "reddit:1abcde"]
    assert "opinions" in out["how"]


def test_no_username_in_any_output(monkeypatch, no_digest, tmp_path):
    items, _ = community.run(Fetch(), Model(ANSWER), cap=1.0, today=TODAY, searches=["x"])
    text = json.dumps(community.update({}, items, TODAY))
    stored(monkeypatch, community.update({}, items, TODAY)["weeks"])
    text += json.dumps(news.digest(today=TODAY))
    for name in NAMES:
        assert name not in text
    for key in ("author", "username", "alias_name", "posters", "user_id", "excerpt", "avatar"):
        assert f'"{key}"' not in text


def test_the_shipped_file_if_any_holds_the_stored_fields_only():
    p = ROOT / "data" / "kb" / "community.json"
    if not p.exists():
        pytest.skip("no community week shipped yet")
    allowed = {"id", "source", "title", "url", "date", "replies", "likes", "text", "classes"}
    for w in json.loads(p.read_text())["weeks"]:
        for it in w["items"]:
            assert set(it) <= allowed and it["url"].startswith("https://")


def test_a_week_with_nothing_new_makes_no_model_call(no_digest):
    """2026-11-22: threads already shown are never sent again, and no new
    thread with last week's searches means no call at all."""
    first = Model(ANSWER)
    items, out = community.run(Fetch(), first, cap=1.0, today=TODAY, searches=["Onyxia's Lair"])
    doc = community.update({}, items, TODAY, out["searches"])
    assert doc["searched"] == ["Onyxia's Lair"] and len(first.requests) == 1
    again = Model(ANSWER)
    items, out = community.run(Fetch(), again, cap=1.0, today=TODAY, searches=["Onyxia's Lair"],
                               seen=community.shown(doc), last=doc["searched"])
    assert again.requests == [] and items == [] and "skipped" in out["model"]
    # A new search topic (an event announced) is a change: one call, without the threads already shown.
    third = Model(ANSWER)
    community.run(Fetch(), third, cap=1.0, today=TODAY, searches=["Onyxia's Lair", "Darkmoon Faire"],
                  seen=community.shown(doc), last=doc["searched"])
    assert len(third.requests) == 1 and "<thread n=" not in third.requests[0]["messages"][0]["content"]
