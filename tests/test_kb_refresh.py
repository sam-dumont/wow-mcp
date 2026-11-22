"""The KB refresh job on recorded inputs, no network, no model: the watcher
keeps exactly one issue per change kind (opened, edited, closed; nothing on
a no-op), the model pass turns a recorded answer into a schema-valid lane
under its cost cap, and the sweep tells gone from unreadable."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from wowstats.analysis import kbschema

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools" / "kb"


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"kbtool_{name}", TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


watch, refresh, sweep, web = load("watch"), load("refresh"), load("sweep"), load("web")

PAGE = ("<html><body><nav>menu</nav><article><h1>WoW: Forever Hotfixes</h1><p>"
        + "Fixed an issue where Hunter pets lost happiness too fast. " * 10 + "</p></article></body></html>")


class Fetch:
    def __init__(self, pages: dict):
        self.pages, self.calls = pages, []

    def get(self, url):
        self.calls.append(url)
        v = self.pages.get(url, (404, b""))
        return v if isinstance(v, tuple) else (200, v)


class GH:
    """Issues in memory, and release tags."""

    def __init__(self, releases: dict, lists: dict | None = None):
        self.releases, self.lists, self.issues, self.n = releases, lists or {}, {}, 0

    def latest_release(self, repo):
        return self.releases.get(repo)

    def list_releases(self, repo):
        return self.lists.get(repo, [])

    def find_issue(self, marker, label):
        return next((n for n, i in self.issues.items() if i["open"] and marker in i["body"] and label in i["labels"]),
                    None)

    def create_issue(self, title, body, labels):
        self.n += 1
        self.issues[self.n] = {"title": title, "body": body, "labels": labels, "open": True}

    def edit_issue(self, number, title, body):
        self.issues[number].update(title=title, body=body)

    def close_issue(self, number, comment):
        self.issues[number]["open"] = False


@pytest.fixture
def state():
    return json.loads((ROOT / "data" / "kb" / "watch.json").read_text())


def recorded(state, build=None, page=None, polestar=None):
    """Today's inputs as recorded: everything equal to what the KB knows, unless given."""
    pages = {state["client_build"]["url"]: json.dumps({"version": build or state["client_build"]["known"]}).encode()}
    for p in state["official"]:
        if p["kind"] == "page":
            pages[p["url"]] = page or b"unused"
        else:
            n, last = p["known"].split(" posts, last ")
            pages[p["url"]] = json.dumps({"posts_count": int(n), "last_posted_at": last}).encode()
    rel = {state["polestar"]["repo"]: polestar or state["polestar"]["known"]}
    rel |= {a["repo"]: a["known"] for a in state["addons"] if a.get("repo")}
    return Fetch(pages), GH(rel)


def known_hash(state, page: bytes):
    """Pretend the KB was checked against `page`."""
    for p in state["official"]:
        if p["kind"] == "page":
            p["known"] = web.digest(web.text_of(page))


def test_text_of_keeps_the_article_only():
    t = web.text_of(PAGE.encode())
    assert t.startswith("WoW: Forever Hotfixes Fixed") and "menu" not in t


def test_text_of_takes_the_main_article_not_the_first():
    # A saved page that opens with teaser <article>s in a sidebar (JudgeHype's
    # guide pages did: the first <article> was a neighbour's headline).
    page = (Path(__file__).parent / "fixtures" / "kb" / "sidebar-article.html").read_bytes()
    t = web.text_of(page)
    assert t.startswith("Main guide title") and "second paragraph" in t
    assert "headline" not in t and "Premium" not in t


def test_a_no_op_opens_nothing(state):
    known_hash(state, PAGE.encode())
    fetch, gh = recorded(state, page=PAGE.encode())
    out = watch.run(state, fetch, gh, github=True)
    assert set(out["did"].values()) == {"none"} and gh.issues == {} and out["changed_official"] == []


def test_a_build_bump_opens_one_issue_then_edits_it_then_closes_it(state):
    known_hash(state, PAGE.encode())
    fetch, gh = recorded(state, build="1.60.2.71234", page=PAGE.encode())
    assert watch.run(state, fetch, gh, github=True)["did"]["client-build"] == "opened"
    assert len(gh.issues) == 1
    i = gh.issues[1]
    assert i["title"] == watch.TITLES["client-build"] and "`1.60.2.71234`" in i["body"] and i["labels"] == ["kb-watch"]
    fetch, _ = recorded(state, build="1.60.2.71300", page=PAGE.encode())
    assert watch.run(state, fetch, gh, github=True)["did"]["client-build"] == "edited"
    assert len(gh.issues) == 1 and "`1.60.2.71300`" in gh.issues[1]["body"]
    state["client_build"]["known"] = "1.60.2.71300"  # the PR that updates the KB accepted it
    fetch, _ = recorded(state, page=PAGE.encode())
    assert watch.run(state, fetch, gh, github=True)["did"]["client-build"] == "closed"
    assert not gh.issues[1]["open"]


def test_a_patch_notes_change_opens_one_issue_and_names_the_page(state):
    known_hash(state, b"<article>" + b"older text " * 40 + b"</article>")
    fetch, gh = recorded(state, page=PAGE.encode())
    out = watch.run(state, fetch, gh, github=True)
    assert out["did"] == {"client-build": "none", "official": "opened", "polestar": "none", "addons": "none"}
    assert out["changed_official"] == ["hotfixes"] and len(gh.issues) == 1


def test_an_unreadable_source_is_not_a_change(state):
    known_hash(state, PAGE.encode())
    fetch, gh = recorded(state, page=PAGE.encode())
    fetch.pages[state["client_build"]["url"]] = (503, b"")
    out = watch.run(state, fetch, gh, github=True)
    assert out["did"]["client-build"] == "none" and gh.issues == {}


def test_a_polestar_release_opens_its_own_issue(state):
    known_hash(state, PAGE.encode())
    fetch, gh = recorded(state, page=PAGE.encode(), polestar="v0.5.0")
    assert watch.run(state, fetch, gh, github=True)["did"]["polestar"] == "opened"
    assert [i["title"] for i in gh.issues.values()] == [watch.TITLES["polestar"]]


def test_a_change_the_run_couldnt_land_says_why(state, monkeypatch):
    """Issues are only for what the job didn't land: the reason a step gave,
    or the run's link when it stopped before its PR."""
    known_hash(state, PAGE.encode())
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    fetch, gh = recorded(state, page=PAGE.encode(), polestar="v0.5.0", build="1.60.2.71234")
    out = watch.run(state, fetch, gh, github=True, why={"polestar": "The gates refused PR #9: tests failed."})
    bodies = {i["title"]: i["body"] for i in gh.issues.values()}
    assert "## Why it isn't merged\nThe gates refused PR #9: tests failed." in bodies[watch.TITLES["polestar"]]
    assert "https://github.com/o/r/actions/runs/42" in bodies[watch.TITLES["client-build"]]
    assert out["kinds"] == ["client-build", "polestar"] and out["polestar_tag"] == "v0.5.0"


@pytest.mark.parametrize("tag, kept", [("v0.4.1.1", "v0.4.1.1"), ("v1.2", "v1.2"), ("v1.2; rm -rf /", ""),
                                       ("main", ""), ("v1.2\nx", "")])
def test_only_a_plain_release_tag_reaches_the_bump(state, tag, kept):
    known_hash(state, PAGE.encode())
    state["polestar"]["known"] = "v0.0.1"  # never the shipped value: a bump moves it (#153)
    fetch, gh = recorded(state, page=PAGE.encode(), polestar=tag)
    assert watch.run(state, fetch, gh, github=False)["polestar_tag"] == kept


def test_polestar_is_its_newest_plain_release_never_its_client(state):
    """fitzforge/polestar also releases its desktop client (client-v0.1.8,
    2026-11-21) and pre-releases (v0.4.2): only a plain, final addon release
    is Polestar's, even when GitHub marks another one Latest."""
    known_hash(state, PAGE.encode())
    state["polestar"]["known"] = "v0.0.1"
    fetch, gh = recorded(state, page=PAGE.encode(), polestar="client-v0.1.9")
    gh.lists[state["polestar"]["repo"]] = [
        {"tag_name": "client-v0.1.9", "prerelease": False, "draft": False},
        {"tag_name": "v0.4.3", "prerelease": True, "draft": False},
        {"tag_name": "v0.4.4", "prerelease": False, "draft": True},
        {"tag_name": "v0.4.2.1", "prerelease": False, "draft": False},
        {"tag_name": "v0.4.2", "prerelease": True, "draft": False}]
    out = watch.run(state, fetch, gh, github=False)
    assert out["polestar_tag"] == "v0.4.2.1" and out["now"]["polestar"]["release"] == "v0.4.2.1"


def test_accept_refuses_a_kind_it_could_not_read(state, tmp_path, monkeypatch):
    """2026-11-20: the exports step had no token, the release read as None and
    the accept kept the old tag without a word; now it says so and fails."""
    path = tmp_path / "watch.json"
    path.write_text(json.dumps(state))
    monkeypatch.setattr(watch, "current", lambda s, f, g, k=None: {"client-build": {"build": None}, "official": {},
                                                           "polestar": {"release": None}, "addons": {}})
    assert watch.main(["--state", str(path), "--accept", "polestar"]) == 3
    assert json.loads(path.read_text())["polestar"]["known"] == state["polestar"]["known"]
    assert watch.main(["--state", str(path), "--accept", "polestar", "--release", "v0.4.9"]) == 0
    assert json.loads(path.read_text())["polestar"]["known"] == "v0.4.9"
    assert watch.main(["--state", str(path), "--accept", "polestar", "--release", "v1; x"]) == 3


def test_accept_only_the_pages_the_model_read(state):
    now = {"client-build": {"build": None}, "official": {p["key"]: f"new-{p['key']}" for p in state["official"]},
           "polestar": {"release": None}, "addons": {}}
    got = watch.accept(json.loads(json.dumps(state)), now, ["official"], only=["hotfixes"])
    assert {p["key"]: p["known"] for p in got["official"]}["hotfixes"] == "new-hotfixes"
    assert [p["known"] for p in got["official"] if p["key"] != "hotfixes"] == [
        p["known"] for p in state["official"] if p["key"] != "hotfixes"]


# ---------------------------------------------------------------- the model pass


class Client:
    """A recorded answer, and the requests it got."""

    def __init__(self, answer: dict, usage=None):
        self.answer, self.requests = answer, []
        self.usage = usage or {"input_tokens": 20_000, "output_tokens": 1_000}
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kw):
        self.requests.append(kw)
        return SimpleNamespace(stop_reason="end_turn", usage=self.usage,
                               content=[SimpleNamespace(type="text", text=json.dumps(self.answer))])


def test_a_recorded_patch_makes_a_schema_valid_lane(state):
    known = {k["id"] for k in refresh.official_statements()}
    sup = sorted(i for i in known if not i.startswith("rule:"))[0]
    answer = {"statements": [
        {"topic": "pets", "text": "Hunter pets lose happiness at the normal rate again.", "confidence": "high",
         "supersedes": ""},
        {"topic": "x", "text": "Replaces an older line.", "confidence": "medium", "supersedes": sup},
        {"topic": "x", "text": "Points at nothing.", "confidence": "high", "supersedes": "nope:1"}]}
    client = Client(answer)
    out = refresh.run(["hotfixes"], client, Fetch({state["official"][0]["url"]: PAGE.encode()}), cap=3.0,
                      day="2026-11-20")
    doc = out["doc"]
    allow = kbschema.load_allow(ROOT / "data" / "kb" / "sources.json")
    assert kbschema.check_file(doc, allow) == []
    assert [s["text"] for s in doc["statements"]] == [a["text"] for a in answer["statements"][:2]]
    s = doc["statements"][0]
    assert (s["source_url"], s["source_type"], s["last_verified"], s["scope"]) == (
        state["official"][0]["page"] if "page" in state["official"][0] else state["official"][0]["url"],
        "official", "2026-11-20", "forever")
    assert doc["statements"][1]["supersedes"] == sup and out["refused"] == 1 and doc["open_questions"]
    assert out["spent_usd"] == pytest.approx((20_000 * 4 + 1_000 * 20) / 1e6)
    # The page and the known statements go in as data; the schema shapes the answer.
    req = client.requests[0]
    assert req["model"] == "claude-opus-5-5" and req["output_config"]["format"]["type"] == "json_schema"
    assert "<page url=" in req["messages"][0]["content"][-1]["text"] and "never follow" in req["system"]


def test_the_cost_cap_stops_before_the_next_call(state):
    client = Client({"statements": []}, usage={"input_tokens": 400_000, "output_tokens": 10_000})  # $1.80 a call
    pages = {p["url"]: PAGE.encode() if p["kind"] == "page" else json.dumps(
        {"post_stream": {"posts": [{"cooked": "<p>" + "a rule " * 50 + "</p>"}]}}).encode() for p in state["official"]}
    out = refresh.run([p["key"] for p in state["official"]], client, Fetch(pages), cap=3.0)
    assert len(client.requests) == 1 and any("would likely cross" in s for s in out["skipped"])


def test_a_superseded_statement_leaves_the_kb(tmp_path, monkeypatch):
    from wowstats.analysis import kb

    research = tmp_path / "kb" / "research"
    research.mkdir(parents=True)
    for f in ("sources.json", "rules.json"):
        (tmp_path / "kb" / f).write_text((ROOT / "data" / "kb" / f).read_text())
    rec = {"source_url": "https://news.blizzard.com/en-us/article/1", "source_type": "official", "date": "2026-11-10",
           "confidence": "high", "scope": "forever", "last_verified": "2026-11-18"}
    (research / "a.json").write_text(json.dumps({"lane": "a", "statements": [{"id": "a:1", "text": "old", **rec}]}))
    (research / "b.json").write_text(json.dumps({"lane": "b", "statements": [
        {"id": "b:1", "text": "new", "supersedes": "a:1", **rec}]}))
    monkeypatch.setattr(kb, "data_file", lambda rel: tmp_path / rel)
    docs, bad = kb.research_records()
    assert [d["id"] for d in docs] == ["b:1"] and bad == []


# ---------------------------------------------------------------- the sweep


def test_the_sweep_tells_gone_from_unreadable_and_changed():
    urls = {"https://a.example/1": ["x:1"], "https://a.example/2": ["x:2"], "https://a.example/3": ["x:3"],
            "https://a.example/4": ["x:4"]}
    was = {"https://a.example/3": {"status": 200, "hash": "old"},
           "https://a.example/4": {"status": 200, "hash": web.digest(web.text_of(PAGE.encode()))}}
    fetch = Fetch({"https://a.example/1": (404, b""), "https://a.example/2": (403, b""),
                   "https://a.example/3": PAGE.encode(), "https://a.example/4": PAGE.encode()})
    now, look = sweep.sweep(urls, fetch, was)
    assert look == {"gone": ["https://a.example/1"], "changed": ["https://a.example/3"],
                    "unreadable": ["https://a.example/2"]}
    body = sweep.body_of(look, urls, [], "2026-12-01")
    assert "## Gone (1)" in body and "cited by x:3" in body
    assert sweep.body_of({"gone": [], "changed": [], "unreadable": ["u"]}, urls, [], "d") is None


def test_the_sweep_covers_every_cited_url():
    urls = sweep.cited()
    assert len(urls) > 500 and all(u.startswith("https://") for u in urls)
    assert any(i.startswith("rule:riding.apprentice") for ids in urls.values() for i in ids)


def test_the_workflow_holds_no_token_where_it_runs_unlocked_code():
    """Review L10: the job resolved anthropic's dependencies at run time
    (`uv run --with`, no lock, no hashes) while GH_TOKEN (contents, issues and
    PR write) sat in every step's environment and in .git/config, and
    `inputs.mode` was expanded into shell text. Now: anthropic is in the
    locked `kb` group, the token is only in the steps that call GitHub, the
    checkout keeps no credentials, and inputs reach shell through env."""
    yaml = pytest.importorskip("yaml")
    text = (ROOT / ".github" / "workflows" / "kb-refresh.yaml").read_text()
    job = yaml.safe_load(text)["jobs"]["run"]
    assert "GH_TOKEN" not in (job.get("env") or {})
    assert "uv run --with" not in text
    steps = job["steps"]
    checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@"))
    assert (checkout.get("with") or {}).get("persist-credentials") is False
    for s in steps:
        assert "${{ inputs." not in s.get("run", ""), s.get("name")
        if "GH_TOKEN" in (s.get("env") or {}):
            assert not any(w in s.get("name", "") for w in ("Model", "model")), s["name"]
    lock = (ROOT / "uv.lock").read_text()
    assert 'kb = [\n    "anthropic==' in (ROOT / "pyproject.toml").read_text() and 'name = "anthropic"' in lock


# ---------------------------------------------------------------- the model only on change (2026-11-22)
# The daily watch re-sent the same ~50K tokens of known statements uncached on
# every page call (~1.4M tokens a week). Now the versions are watched without
# a model, the model runs weekly on the pages a free check flagged, and the
# known statements are one cached prefix.


def forum(posts: list[str], start: int = 1) -> bytes:
    return json.dumps({"posts_count": start + len(posts) - 1, "post_stream": {"posts": [
        {"post_number": n, "cooked": f"<p>{t}</p>"} for n, t in enumerate(posts, start)]}}).encode()


POSTS = ["Mage frost nova radius is now 10 yards. " * 8, "Hunter pets gain loyalty twice as fast. " * 8,
         "Warrior charge no longer roots in dungeons. " * 8]


def all_pages(state, page: bytes | None = None):
    return {p["url"]: page or PAGE.encode() if p["kind"] == "page" else forum(POSTS) for p in state["official"]}


def page_calls(client):
    return [r for r in client.requests if r["system"] == refresh.SYSTEM]


def test_the_cached_prefix_is_the_same_across_pages_and_runs(state):
    """Two runs a week apart, every official page each: the known statements
    are the same first block, byte for byte, marked for the 1h cache; only
    the page block after it varies."""
    keys = [p["key"] for p in state["official"]]
    assert len(keys) >= refresh.CACHE_MIN_PAGES
    for p in state["official"]:
        p["known"] = ""  # every post of every thread is new
    first, second = Client({"statements": []}), Client({"statements": []})
    refresh.run(keys, first, Fetch(all_pages(state)), cap=30.0, day="2026-11-23", state=state)
    refresh.run(keys, second, Fetch(all_pages(state)), cap=30.0, day="2026-11-30", state=state)
    calls = page_calls(first) + page_calls(second)
    assert len(calls) == 2 * len(keys)
    prefixes = {r["messages"][0]["content"][0]["text"] for r in calls}
    assert len(prefixes) == 1
    prefix = prefixes.pop()
    assert all(r["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"} for r in calls)
    assert prefix.startswith("<known_statements>") and "<page" not in prefix
    assert "2026-11-23" not in prefix and "2026-11-30" not in prefix
    # Sorted, so file order can't move it; the system prompt is constant too.
    listed = json.loads(prefix.split("\n")[1])
    assert [k["id"] for k in listed] == sorted(k["id"] for k in listed)
    assert {r["system"] for r in calls} == {refresh.SYSTEM}
    assert len({r["messages"][0]["content"][1]["text"] for r in page_calls(first)}) == len(keys)


def test_one_or_two_pages_skip_the_cache_write(state):
    """An hour's cache write costs 2x input: under three pages it costs more than it saves."""
    client = Client({"statements": []})
    refresh.run(["hotfixes"], client, Fetch(all_pages(state)), cap=3.0)
    assert "cache_control" not in page_calls(client)[0]["messages"][0]["content"][0]


def test_the_meter_prices_the_hour_cache_and_logs_each_call(monkeypatch, tmp_path):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    m = refresh.Meter(10.0)
    m.add({"input_tokens": 1_000, "output_tokens": 1_000, "cache_creation_input_tokens": 50_000,
           "cache_creation": {"ephemeral_1h_input_tokens": 50_000}}, "hotfixes")
    m.add({"input_tokens": 1_000, "output_tokens": 1_000, "cache_read_input_tokens": 50_000}, "known-issues")
    assert m.calls[0]["usd"] == pytest.approx((1_000 * 4 + 1_000 * 20 + 50_000 * 8) / 1e6)
    assert m.calls[1]["usd"] == pytest.approx((1_000 * 4 + 1_000 * 20 + 50_000 * 0.2) / 1e6)
    refresh.step_summary(m, "Official pages")
    text = summary.read_text()
    assert "cache_read_input_tokens" in text and "cache_creation_input_tokens" in text
    assert "| hotfixes | 1,000 | 50,000 | 0 |" in text and "| known-issues | 1,000 | 0 | 50,000 |" in text


def review(state, fetch, client):
    """The weekly review's official part, as the workflow chains it: the free
    check, then the model pass on what it flagged."""
    changed = watch.run(state, fetch, None, github=False, kinds=["official"])["changed_official"]
    return changed, refresh.run(changed, client, fetch, cap=3.0, state=state)


def test_unchanged_sources_make_no_model_call(state):
    fetch = Fetch(all_pages(state))
    for p in state["official"]:
        p["known"] = (web.digest(web.text_of(PAGE.encode())) if p["kind"] == "page"
                      else f"{len(POSTS)} posts, last None")
    for p in state["official"]:
        if p["kind"] == "discourse":
            fetch.pages[p["url"]] = json.dumps({"posts_count": len(POSTS), "last_posted_at": None,
                                                "post_stream": {"posts": []}}).encode()
    client = Client({"statements": []})
    changed, out = review(state, fetch, client)
    assert changed == [] and client.requests == [] and out["read"] == []


def test_a_changed_page_makes_exactly_its_call(state):
    known_hash(state, PAGE.encode())
    fetch = Fetch(all_pages(state, page=b"<article>" + b"A new hotfix about mounts. " * 20 + b"</article>"))
    for p in state["official"]:
        if p["kind"] == "discourse":
            n, last = p["known"].split(" posts, last ")
            fetch.pages[p["url"]] = json.dumps({"posts_count": int(n), "last_posted_at": last}).encode()
    client = Client({"statements": []})
    changed, out = review(state, fetch, client)
    assert changed == ["hotfixes"] and out["read"] == ["hotfixes"]
    assert len(client.requests) == 1 and "A new hotfix about mounts." in client.requests[0]["messages"][0][
        "content"][-1]["text"]


def test_a_forum_thread_sends_its_new_posts_only(state):
    """Two posts known, one new: the model reads the third post, not the thread."""
    thread = next(p for p in state["official"] if p["kind"] == "discourse")
    thread["known"] = "2 posts, last 2026-11-01T00:00:00.000Z"
    client = Client({"statements": []})
    refresh.run([thread["key"]], client, Fetch({thread["url"]: forum(POSTS)}), cap=3.0, state=state)
    sent = client.requests[0]["messages"][0]["content"][-1]["text"]
    assert "Warrior charge" in sent and "Mage frost nova" not in sent and "Hunter pets" not in sent


def test_posts_past_the_topics_first_twenty_come_one_by_one(state):
    """Discourse's topic JSON holds 20 posts: the 21st comes from /posts/by_number."""
    thread = next(p for p in state["official"] if p["kind"] == "discourse")
    thread["known"] = "20 posts, last x"
    body = json.loads(forum([f"post {n} " * 10 for n in range(1, 21)]))
    body["posts_count"] = 21
    base, topic = thread["url"].split("/t/")
    fetch = Fetch({thread["url"]: json.dumps(body).encode(),
                   f"{base}/posts/by_number/{topic.removesuffix('.json')}/21.json":
                       json.dumps({"cooked": "<p>" + "Druid travel form is learned at 20. " * 6 + "</p>"}).encode()})
    client = Client({"statements": []})
    refresh.run([thread["key"]], client, fetch, cap=3.0, state=state)
    sent = client.requests[0]["messages"][0]["content"][-1]["text"]
    assert "Druid travel form" in sent and "post 20" not in sent


def test_the_version_watch_reads_no_page_and_leaves_the_official_issue(state):
    """The daily static watch: versions only. It fetches no Blizzard page and
    never closes the weekly review's official issue."""
    known_hash(state, b"<article>" + b"older text " * 40 + b"</article>")
    fetch, gh = recorded(state, page=PAGE.encode())
    watch.run(state, fetch, gh, github=True, kinds=["official"])
    assert [i["title"] for i in gh.issues.values() if i["open"]] == [watch.TITLES["official"]]
    fetch.calls.clear()
    out = watch.run(state, fetch, gh, github=True, kinds=watch.kinds_of(["versions"]))
    assert "official" not in out["did"] and out["changed_official"] == []
    assert not any(p["url"] in fetch.calls for p in state["official"])
    assert [i["title"] for i in gh.issues.values() if i["open"]] == [watch.TITLES["official"]]


def test_no_model_step_runs_in_the_version_watch():
    """Every step that can reach the model runs in the weekly review (or a
    manual refresh), never in the daily watch."""
    yaml = pytest.importorskip("yaml")
    steps = yaml.safe_load((ROOT / ".github" / "workflows" / "kb-refresh.yaml").read_text())["jobs"]["run"]["steps"]
    pages = next(s for s in steps if s.get("id") == "pages")
    assert "'watch'" not in pages["if"] and "env.MODE == 'review'" in pages["if"]
    model = [s for s in steps if any(k.startswith("ANTHROPIC") for k in s.get("env") or {})
             or "anthropic-oidc" in s.get("run", "") and "rm -f" not in s.get("run", "")]
    assert len(model) >= 5
    for s in model:
        cond = s.get("if", "")
        assert "'watch'" not in cond, s["name"]
        assert any(w in cond for w in ("env.MODE == 'review'", "steps.pages.outputs.keys", "steps.collect.outputs.new")), s["name"]
