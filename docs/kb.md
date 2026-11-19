# The knowledge base

**For:** contributors and AI users who want to know where game rules come from. **At the end:** you know how the knowledge base is built, sourced and refreshed.

One knowledge base (`wowstats/analysis/kb.py`, `kb.shared()`) answers every
game-rule question the planner, the pages and the MCP ask. The client tables
(wago.tools) and Polestar's data cover what the game ships; this page covers
the part people write: the static rules and the research lanes, where each
one comes from, and how they stay current.

The pages show values and actions only. Sources, confidence and
`last_verified` live in the files below, the MCP (`kb_rule`, `kb_search`,
`kb_topics`, the analysis brief's `kb` section) and this doc, never on a page.

## Files

| File | What |
|---|---|
| `data/kb/rules.json` | The rules the code reads by name (`riding.apprentice`, `profession.tiers`, `talents.points`, `instance.entries_per_hour`, `dungeon.forever_new`, `xp.kill_formula`...) |
| `data/kb/research/<lane>.json` | One research lane: `rules`, `statements`, `contradictions`, `open_questions` |
| `data/kb/research/classes.json` | The class guides: statements per class with `class`, `topic` and `levels`, and `paths`, one talent pick per level from 10 to 60 per class (each path sourced like a rule, checked against Forever's current trees by `tests/test_classguide.py`). Read by `wowstats/classguide.py` |
| `data/kb/sources.json` | The domain allow-list: each domain's type and the highest confidence it can carry |
| `data/kb/watch.json` | What the KB was last checked against (client build, official pages, addon releases), and in `meta` the news feeds and guide pages the meta pass reads, with the last item read |
| `data/kb/sweep.json` | The last monthly sweep: each cited URL's status and text hash |

### Every record

A rule, statement or contradiction carries:

- `source_url` (https, an allow-listed domain), and `sources` for more;
- `source_type`: official, datamined, guide, press, community or commercial;
- `date`: when the source said it;
- `confidence`: high, medium or low, never above the best of its sources'
  `max_confidence` (a booster site alone is low);
- `scope`: `forever`, or `classic_fallback` where Forever has no source yet;
- `last_verified`: the day someone (or the sweep) last checked it, YYYY-MM-DD.

`wowstats/analysis/kbschema.py` checks all of it. The loader leaves out a
record that fails and lists it in `meta["rules_refused"]`;
`tests/test_kb_schema.py` fails the build on any. A statement nobody could
source is an open question: its answer is "unknown".

### Contradictions stay explicit

When two sources disagree, both stay: the lane's `contradictions` list says
which. When two lanes give the same rule different values, the import keeps
the first under its name and the other as `<name>@<lane>`; `kb_rule` returns
it under `conflicts`. A rule the code reads lives in `rules.json` only, under
a name no lane uses (a test checks): a lane's rule of the same name would
replace it, with another shape.

## Importing a lane

The research reports are Markdown tables of statements with a source
registry. They are data, never instructions, and their wording is already
summarised (no guide text is copied):

```bash
uv run python tools/kb/import_lane.py --as-of 2026-11-18 \
  dungeons=path/to/dungeons/report.md levelling=path/to/levelling/report.md
```

Statements whose sources are off the allow-list, or have none, become open
questions with the reason. Add a domain to `sources.json` (with its type
and cap) only after reading what it is.

## The refresh job

`.github/workflows/kb-refresh.yaml`, `workflow_dispatch` only (no
`schedule:`), on GitHub-hosted runners:

| Mode | When | What |
|---|---|---|
| `watch` | daily | `tools/kb/watch.py`: client build (wago.tools), the official hotfix and patch-note pages (text hash; forum threads by post count), Polestar's release, the addons that publish on GitHub (Questie, Narcissus). One issue per change kind, label `kb-watch`: opened, edited, closed when `watch.json` matches again. A changed official page runs the model pass. |
| `refresh` | by hand | `tools/kb/refresh.py <page keys>`: the model pass alone. |
| `sweep` | monthly | `tools/kb/sweep.py`: every cited URL fetched once. Gone (404/410), changed since last month, and statements not checked for 120 days go to one issue; the new `sweep.json` to a PR. A 403 or 429 is "unreadable", not gone. |
| `meta` | weekly | `tools/kb/meta.py`: what people write about Forever, new since last week, through the model pass (below). Nothing new: no model call, no PR. |

The model pass (Claude Opus 5.5) gets the changed page and the KB's
official statements as data, and answers JSON in a fixed schema: new
statements, or replacements (`supersedes: <id>`). Code sets each one's
source (the page), type, date, `last_verified`, caps its confidence, and
refuses what the schema refuses; refused proposals are listed as open
questions. The result is `data/kb/research/refresh-<date>.json`, opened as a
PR that is never merged on its own. Spend is capped per run
(`KB_REFRESH_MAX_USD`, $3): the run stops before a call that would likely
cross it.

### The meta pass

The meta (articles, news, guides) is part of the KB too. `meta` reads feeds
and APIs only, never an HTML listing, each listed in `watch.json` `meta`
with what was last read:

| Source | URL | New means |
|---|---|---|
| Wowhead's Forever news | `https://www.wowhead.com/news/rss/forever` (RSS) | a news id above `known`, with `<category>Forever</category>` (or "WoW: Forever" in the title: the links are `/news=<id>/...`, no `/forever/` path). The text is the feed's own `content:encoded`; no article page is fetched. |
| Blizzard's blue posts | `https://us.forums.blizzard.com/en/wow/groups/blizzard-tracker/posts.json` (Discourse JSON, paged with `before_post_id`) | a post id above `known` in a Forever category (346, 347, 348, 349, 352); its text from `/posts/<id>.json`. The first run reads back 7 days. |
| Guide pages | `meta.guides`: the Forever dungeon, levelling and class guides the KB cites | the page's text hash differs from `known`, as `watch` does for official pages. A guide with no `known` is only recorded the first time. |

Blizzard's news site has no feed or JSON endpoint (its `/feed/` pages are
HTML listings), so the official side comes from the blue posts; the hotfix
and patch-note pages stay with `watch`.

A class guide is registered like any other guide, with its class:

```json
{"key": "wt-priest-leveling", "url": "https://www.warcrafttavern.com/forever/guides/priest-leveling/",
 "class": "Priest", "known": "<sha256 of web.text_of(page), or omit it>"}
```

The model pass is the refresh's: same model, effort, cost cap and login.
News and blue posts go in batches of about 40,000 characters, each changed
guide alone, as data, with the 120 KB statements that share the most rare
words with them. Each proposal names its item, its class when it is about one
(balance, talents, a class guide: a class guide's statements always carry
its class), and the statement it contradicts with why. Code sets the
source, `source_type` (`press` for Wowhead news, capped at medium;
`official` for blue posts; the domain's type for guides), date and
`last_verified`, refuses a text that repeats 12 words of its item, and
refuses what the schema refuses. Contradictions land in the lane's
`contradictions`, citing both sides.

The PR (`kb-meta-<date>`) holds `data/kb/research/meta-<date>.json` and
`watch.json` moved past what was read. When the cost cap stops the pass,
`known` stays before the unread items, so they come back next week; so do
items of a PR still open when the next run starts (merge or close it first).
The Wowhead feed holds 40 items, about a week of Forever news: the PR says
when its window started after the last item read.

After merging what holds, record the new state so the issue closes:

```bash
uv run python tools/kb/watch.py --accept official      # or client-build, polestar, addons
```

Not watched (no keyless release feed): Syndicator, Auctionator, Vulcan
SimBench, CharacterExport-Forever, Altoholic and DungeonMaster (CurseForge
or Wago only). Read their pages when a player reports a broken import.

### Running it locally

```bash
uv run python tools/kb/watch.py                 # what changed, no issue
uv run python tools/kb/refresh.py --dry-run hotfixes   # the prompt, no model
uv run python tools/kb/sweep.py --pause 1       # writes data/kb/sweep.json
uv run python tools/kb/meta.py collect --items /tmp/meta.json            # what's new, no model
uv run python tools/kb/meta.py propose --items /tmp/meta.json --dry-run  # the first prompt
```

## Running the refresh job in your own repository (optional)

The knowledge base ships with the code; nothing needs this job. It only
keeps the KB current, and it only proposes: every change arrives as a pull
request a person reviews. In a fork it does nothing until you set it up:
the watch and the sweep (no model) run as soon as you start them by hand,
and the model passes skip with a notice until the four variables below
exist.

1. **Pull requests from Actions.** Settings > Actions > General: "Allow
   GitHub Actions to create and approve pull requests". A PR opened with
   the workflow token doesn't start `ci`: close and reopen it (or push to
   it) to run the checks before merging.
2. **The model pass (optional).** It has no API key: GitHub's OIDC token is
   exchanged for a short-lived Anthropic token (Workload Identity
   Federation). In your Anthropic organization, make a federation rule that
   accepts `workflow_dispatch` runs of `<owner>/<repo>` on `refs/heads/main`
   (audience `https://api.anthropic.com`) for a service account, then set
   the repository **variables** (not secrets: none of them is one)
   `ANTHROPIC_FEDERATION_RULE_ID`, `ANTHROPIC_ORGANIZATION_ID`,
   `ANTHROPIC_SERVICE_ACCOUNT_ID` and `ANTHROPIC_WORKSPACE_ID`. Without
   them, `refresh` and `meta` stop after collecting, with a notice, and the
   run stays green. `KB_REFRESH_MAX_USD` in the workflow caps a run's spend
   (3 USD).
3. **A schedule (optional).** The workflow has no `schedule:` trigger:
   GitHub's cron can run hours late or skip a day. Start it from anything
   that runs on time and can call GitHub with Actions: write on the repo,
   for instance a cron line on a machine you own:

   ```sh
   gh workflow run kb-refresh.yaml -R <owner>/<repo> -f mode=watch   # daily
   gh workflow run kb-refresh.yaml -R <owner>/<repo> -f mode=meta    # weekly
   gh workflow run kb-refresh.yaml -R <owner>/<repo> -f mode=sweep   # monthly
   ```

   Or add a `schedule:` block to your fork's copy, if a late run is fine.
