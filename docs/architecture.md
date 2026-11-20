# Architecture

**For:** contributors and curious self-hosters. **At the end:** you know the parts, how an event travels from the game to the page, and how the public view is kept apart.

```
 the PC you play on            server (one image, two commands)
 ┌──────────────────┐          ┌──────────────────────────────────────────┐
 │ shipper          │  POST    │ wow-stats ui (:8080)                     │
 │ wow_stats.py     │ ───────► │   /ingest ──► VictoriaLogs (events)      │
 │ reads WTF/, Logs/│  NDJSON  │   /api/*  ◄── VictoriaLogs               │
 │ git archive,spool│          │           ◄── VictoriaMetrics (full only)│
 └──────────────────┘          │                                          │
                               │ + FastMCP (:8090, full only): /mcp, OAuth│
                               │                                          │
                               │ wow-stats exporter (:9464)               │
                               │   Battle.net API ──push──► VictoriaMetrics│
                               │   (23-day retention, daily force-merge)  │
                               └──────────────────────────────────────────┘
```

Everything at runtime is the Python standard library, but two extras: the
`publish` seal (pyca/cryptography) and the MCP endpoint (FastMCP, `mcp`).
uv manages the project and the dev tools (pytest, ruff); the image holds a
venv with the `wowstats` package and those two extras.

## Parts

| Path | What |
|---|---|
| `shipper/wow_stats.py` | The player's script (a uv inline script, stdlib only): archive, extract, ship. Its own installers for macOS (launchd) and Windows (Task Scheduler). `docs/contract.md` lists what it sends, `docs/shipper.md` how it runs. |
| `wowstats/cli.py` | `wow-stats ui` and `wow-stats exporter`. |
| `wowstats/ui.py` | The HTTP server: config checks, logins, routing, the in-memory cache, `/ingest`. |
| `wowstats/prebuilt.py` | The page's answers, built in the background once per new data and served as they are ("Caching" below). |
| `wowstats/stores.py` | Reads from VictoriaLogs (LogsQL) and VictoriaMetrics (PromQL), plus the helpers that merge and dedupe rows (newest `_time` per `source_id`, then highest `rev`). Every VictoriaLogs request goes through one gate (`VL_GATE`). |
| `wowstats/eventcache.py`, `wowstats/logsql.py` | One character's events kept in the process and refreshed by the hour that changed; the views' LogsQL answered over them ("Caching" below). |
| `wowstats/views.py` | Builds each JSON answer from the stores: characters, header, climb, levels, zones, guide steps, dungeons, deaths, players, flights, prices, market. Treats `Name Surname` and `Name-Surname` as one character. |
| `wowstats/journal.py` | Splits the play history into sessions (20 minutes without a Recorder record, or the shipper's `login` marker) and builds one journal entry per session. |
| `wowstats/redact.py` | The public and `/zogzog/` tiers: allow lists of what may leave the server. |
| `wowstats/items.py` | Item names and qualities for the loot history (Forever's ItemSparse via wago.tools). |
| `wowstats/exporter.py` | The Battle.net exporter. |
| `wowstats/mcp/` | The tools and prompts, and the MCP endpoint on FastMCP (`server.py`: `/mcp` and the OIDC proxy that logs claude.ai, ChatGPT and Claude Code in through your identity provider, on `MCP_PORT`). `docs/mcp.md`. |
| `wowstats/plan/` | Session plans: `facts.py` gathers game data (never the Battle.net store), `estimate.py` computes every number, `store.py` keeps plans and compares them with what happened, `write.py` the checks on a client's write-up, `http.py` the full view's plan box. No model. |
| `wowstats/plan/dungeonview.py`, `wowstats/links.py` | The Dungeons panel of the full view and `/zogzog/`: one block per dungeon in reach (drops for the character's weights, obtained or missing; quests; what a trip is worth; a verdict on the whole value), from the planner's inputs, with Forever links to Wowhead. |
| `wowstats/plan/dungeongain.py` | One more run of a dungeon: XP at the character's level now (its own runs there, kill XP carried by the KB's `xp.kill_formula` over the creatures it killed; else the quests alone), the time (its runs, else the KB's clear time plus its own travel), the share of the level, XP per hour against its own questing, the quests to pick up. One estimate for the Overview's dungeon move, the Dungeons panel and the MCP's `dungeon_options`. |
| `wowstats/analysis/drive.py` | The play analysis, driven by the user's own model over MCP: the brief in pages, read-only SQL, the judge (`verify.check`), the pass state machine (survey, lanes, follow-ups, coverage, synthesis) with the clash and feasibility checks. `passes.py` holds the instructions; `__init__.py` the per-character state, the nightly recheck and the shared page's lines. |
| `wowstats/mcp/plantools.py`, `analysistools.py`, `helppage.py` | The plan, KB and analysis tools on the MCP; the sealed `/help/` page built from the registry. |
| `data/route-reference.json` | Polestar's route clock (GPL-3.0), packaged as `wowstats/data/`. `docs/route-reference.md`; remade by `tools/route_reference/`. |
| `data/gear-reference.json` | Polestar's gear data: items, sources, quests, stat weights (GPL-3.0 with MIT parts, `THIRD_PARTY.md`), packaged as `wowstats/data/`. `docs/gear-reference.md`; remade by `tools/gear_reference/`, which also holds `parity.lua`, Polestar's own scoring run under Lua for the tests. |
| `data/forever-reference.json` | Forever's quest and item names, quest levels and zones, and which zones are dungeons with their level range (forever-db, GPL-3.0; Forever's client tables), packaged as `wowstats/data/`. `docs/forever-reference.md`; remade by `tools/forever_reference/`. |
| `data/model3d-reference.json.gz` | How Forever dresses each armor display (Forever's client tables via wago.tools), for the 3D view's armor Wowhead lacks, packaged as `wowstats/data/`. `docs/model3d-reference.md`; remade by `tools/model3d_reference/`. |
| `data/trainers-reference.json` | Who trains each profession rank and riding on Forever, where (zone and map coordinates), the level a rank needs and the price the guides give, with whose price it is (Polestar's shipped profession guides and riding reminder, GPL-3.0), packaged as `wowstats/data/`. Read by `wowstats/trainers.py` for the Overview's profession and riding moves; remade by `tools/trainers/export.py`. A rank with no trainer in it (Artisan Enchanting, the secondary skills past Journeyman) shows "trainer location not in our data". |
| `data/class-reference.json`, `wowstats/classguide.py` | The class guide: per class the class trainers (capitals and starting towns, with side and map coordinates), what they teach at each level (rank, cost, the races a spell is for) and Forever's talent names and arrows (Polestar's `ClassTrainers.lua` and `ClassSpells.lua`, GPL-3.0, and Forever's client tables), remade by `tools/class_reference/export.py`; with the KB's `classes` lane (`data/kb/research/classes.json`: sourced statements per class and topic, one talent path per class), the character page's "your class at level N" block and the MCP's `class_guide`. |
| `wowstats/gear.py` | Gear advice: Polestar's scoring (`Gear.lua`, `Weights.lua`, `Adjust.lua`) ported, per character, spec and level; behind `/api/gear` (both views) and the MCP `gear` tool. |
| `web/` | The page: `index.html`, `app.js`, `app.css`, no build step, no third-party code. Fonts are vendored in `web/fonts` (Cinzel, Alegreya, Alegreya Sans: SIL Open Font License, the licence files next to them) and served by the UI itself, so the CSP stays `font-src 'self'`. The crest, slot glyphs and frames are drawn in `app.js` and `app.css`: no game art. Packaged inside the wheel as `wowstats/web`. |
| `deploy/compose/` | The standalone kit. |
| `dev/seed.py` | Made-up data (`demo: true`, Testchar/Testalt) for working on the page. |

## Two views, one per process

`VIEW` is read once at startup and a process serves one view. A Kubernetes
setup runs two Deployments of the same image; the compose kit runs `ui` (full)
and, with the `public` profile, `ui-public`.

The public view is enforced in the server, in three places:

1. **Answers are allow-listed.** `redact.py` copies only listed fields into
   the public answer (`PUBLIC`): the character card, the climb with each
   level's route time, the dungeons cleared, the level slider. A field added
   to a full answer stays out of the public one until someone adds it to the
   list. The `/zogzog/` bundle has its own list (`ZOGZOG`): no other players,
   no location now, deaths as counts, the Overview's moves minus those about creatures.
2. **Full-only routes don't exist.** `/api/market`, `/api/prices`,
   `/api/logsql`, `/model3d/*`, `/api/model3d` and the login door
   (`FULL_DOOR_PATH`, `/full/`) answer 404, a public server starts no MCP
   endpoint (no `MCP_PORT` listener), and it gets no `VM_URL` at all, even if one is set. (The static public
   site does carry the character's look from Battle.net in its 3D view:
   `wow-stats publish` builds it from the full view, owner's call of
   2026-11-18. Market data stays out of it; `tests/test_publish.py`.)
3. **The page has no full-only markup.** `index.html` wraps those sections in
   `<!--full-->...<!--/full-->`, which the server strips before sending.
   `/api/config` tells the page which features exist and it renders what is
   there; a missing section leaves no gap.

`tests/test_public_view.py` checks all three with a store whose every row
carries another player's name, gold, coordinates, prices and token values,
then fails on any of those bytes in any public answer, for GET and POST.

## The 3D view

The paperdoll's middle has a "View in 3D" toggle (`web/model3d/model3d.js`,
cut from the live public page with its `<!--model3d-->` markers); it swaps
the still (the armory's render, `wowstats/armory.py`, or a silhouette) for
the model in place, and back. Nothing of Wowhead's loads before the toggle.
Then:

- A frame opens on `/model3d/view`, a page of its own. Wowhead's viewer
  (`ZamModelViewer`, the one behind wowhead.com's dressing room) is a
  WebAssembly build that needs `'unsafe-eval' 'wasm-unsafe-eval'` and `blob:`
  images: only that page gets them (`model3d.VIEW_POLICY`). The main page
  keeps its CSP plus `frame-src 'self'`. jQuery 3.7.1, which the viewer is
  built on, is vendored in `web/model3d/`.
- The viewer is someone else's script, so the frame is a box: `sandbox=
  "allow-scripts"` with no `allow-same-origin` (an opaque origin: no
  cookies, storage, /api/* or the page around it) and `connect-src 'none'`.
  It asks the page for every file over postMessage, and the page fetches
  only what the relay's allow list names (`RELAY` in `model3d.js`, the same
  list as `wowstats/model3d.py`) and the answer to draw (in /zogzog/,
  opened with the key there: the key never reaches the frame). Its own CSS
  and script are inlined and allowed by hash (`model3d.view_page`): a
  sandboxed frame's requests carry no cookies, so behind a login they would
  get the login page. Only one reviewed build of the viewer is relayed
  (`VIEWER_SHA256`, `MODEL3D_VIEWER_SHA256` to accept a new one): a changed
  script is refused and the last good copy kept.
- `/api/model3d` turns the character's gear into what the viewer takes:
  the race and sex's model id (ChrModel: the classic eight plus Forever's
  Skyborne, shown on the Blood Elf body Wowhead has, which shares its
  skeleton), and each item's inventory type and display id. Display ids come
  from Forever's own client tables on wago.tools (Item,
  ItemModifiedAppearance, ItemAppearance), so any item works. Neck, rings,
  trinkets, ammo, quivers, relics and wands aren't drawn, like in game. When
  a ranged weapon and a melee one want the same hand, a hunter holds the
  ranged one. Armor Forever added, which Wowhead's data lacks, is dressed
  from Forever's own tables, shipped in `data/model3d-reference.json.gz`
  ([Model reference](model3d-reference.md)); a weapon it added borrows the
  meta of another display of its model. Items still left out are listed
  under the model with the reason, each as its icon.
- **Updated or Classic.** A switch under the model draws the original
  models (Wowhead's classic data, `sd/meta/character/*` through the relay)
  or the updated ones, like the game's Character Models option. The look is
  matched option by option (`model3d.classic_look`), so the face and hair
  are approximate there. The visitor's pick is kept in the browser; without
  one the page opens on the person's Settings choice (`models`, or
  `MODEL3D_MODELS` with one person), else Updated. The past levels'
  pictures are drawn Updated.
- `wow.zamimg.com` answers 403 to any page that isn't Wowhead's, so the
  server fetches the viewer and the model files (`/model3d/zam/*`, an allow
  list of paths) and keeps them on disk.
- Sex comes from Syndicator. When it isn't saved, the page offers a
  male/female pick for that character, kept in the browser only.

### A past level's picture (built 2026-11-19, owner's call)

Battle.net renders today's gear only, so each past level's picture is drawn
here, with the same viewer as the 3D view, in a headless Chromium. Not in the
publisher: that pod holds every zogzog key (the owner's and the master key
the friends' derive from), and Chromium runs Wowhead's script without its
sandbox (an unprivileged container has no user namespaces for it). Third-party
code there is the one thing the publisher/static split exists to keep out.
So the drawing has a process, an image and a pod of its own:

- **`wow-stats render`** (`wowstats/renderer.py`, the image's `renderer`
  target, image `wow-mcp-render`): `POST /render` takes the
  model id (race and sex), the look's option and choice ids and each drawn
  item's inventory type and display id, and answers a 720x960 WebP with the
  character on a clear background. It holds nothing: no key, no store, no
  Secret, no service-account token, no character name. Chromium loads the 3D
  view's own page (`view.html?render=1`) from a server on 127.0.0.1 inside
  the process, with the relay's allow-list of Wowhead paths behind it; any
  other request it tries is refused before it leaves (`page.route`). One
  render at a time. Out: `wow.zamimg.com` and DNS, nothing else. In: the
  publisher only.
- **The publisher** (`wowstats/pictures.py`, `RENDER_URL`) cuts that request
  from the level's 3D answer and keeps the picture under
  `PUBLISH_DIR/pictures/<sha256 of the request>.webp`. A level is drawn once,
  and two levels in the same gear and look share one picture. Both bundles
  get `data/picture/<char>/<level>.webp` (sealed in `/zogzog/`), and the
  snapshot's answer says `picture: {ext}` or `{pending}`.
- **Cost.** About 4 s and 4.5 CPU-seconds a picture at one core (SwiftShader,
  measured on 2026-11-19 in the image), 700-850 MB at the peak, 30 KB a
  file. A build spends at most `RENDER_BUDGET_S` (60) drawing; the rest stay
  pending and the page shows the silhouette with "Picture on its way" and the
  3D toggle. The next pass builds again while pictures are pending (at once
  if the last build drew one, 15 minutes later if it drew none). A level-32
  character is drawn in a few passes, then never again unless its gear or
  look changes.
- **The 30-day rule.** The look comes from Battle.net's profile, so a
  picture is drawn again once it is 25 days old, and one the last build
  didn't use is deleted.

The live full view still shows a past level as the 3D model: the pictures
live with the publisher. `RENDER_URL` empty does the same in the bundles.

## The MCP seam

`wowstats/mcp/` (the MCP endpoint and its OAuth server) ships in the image
and only loads in the full view:

```python
self.mcp = self._mcp(env) if full else None   # None also while MCP_PUBLIC_URL is unset
```

The MCP's settings are checked when the server starts (`mcp.endpoint(env,
bind)`), so a bad `MCP_*` setting stops the server with a message instead of
failing later. It stays off until `MCP_PUBLIC_URL` says where clients reach
it (or `MCP_AUTH=none` on a loopback `BIND`). `main()` then starts it on its
own port, `MCP_PORT` (8090), in a thread of the same process: FastMCP serves
`/mcp` and the OAuth paths there, and the UI's port never answers them.
claude.ai and Claude Code connect from their own servers or your terminal:
they can't send the page's basic auth or a proxy's cookie, so a reverse
proxy routes those paths to 8090 without its login, and FastMCP checks its
own tokens (docs/mcp.md).

Routing on the UI's port, in `_route`, in this order:

1. `/healthz`, then `/ingest` (its own basic login).
2. The page login (`AUTH=basic`; with `AUTH=proxy` the proxy did it).
3. The page and its API.

In the public view `app.mcp` is None: no MCP port. The MCP tools query the
stores through `wowstats.mcp.store`, built from the same `VL_URL`,
`VL_ARCHIVE_URL`, `VM_URL`, `LOGS_FILTER`, in the caller's tenant.
`/api/config` reports `"mcp": true` when it is on, and the character page
then shows the Ask Claude and Ask ChatGPT links.

**No model runs on the server** (owner, 2026-11-18): not for the analysis,
not for plans, not for anyone. The user's own client does the reasoning;
the server prepares data and judges. Blizzard API data (the market strip:
token price, ruleset status) reaches a model only
through the MCP's `market` tool, and only for a person who turned on
"Battle.net data to my AI" in their settings (their own data to their own
assistant, owner ruling 2026-11-18); otherwise it is neither listed nor
callable. `tests/route/test_market_gating.py` holds that line, and the
analysis' allowlist (`wowstats/analysis/TABLES`) has no Battle.net source.

## Data retention

A character's look comes from Battle.net's profile API through the exporter
and its VictoriaMetrics, never from VictoriaLogs (`docs/deploy.md`, "Appearance and the
30-day rule"); Narcissus's barber shop saves, the player's own file, are the
fallback and do go through VictoriaLogs.

| Store | Holds | Retention |
|---|---|---|
| VictoriaLogs | the shipper's events (the player's own game files) | as long as the player wants (compose: 100 years) |
| VictoriaMetrics | the exporter's Battle.net data, characters' looks included | 23 days, daily force-merge, never copied (§2.18) |
| shipper archive | git history of WTF, sent files | on the player's PC, until they delete it |

## Caching

Three layers, all in the UI process:

- **The page's answers** (`wowstats/prebuilt.py`): everything a plain open
  of the page asks for (the character, gear for each spec, overview,
  dungeons, loot, people, plan, journal, the timeline and each level's
  snapshot, the market and prices) is built by one background thread, with
  the routes' own code, and served as it is. Opening the page reads no
  store. The thread checks each minute, and 3 seconds after an upload to
  `/ingest` ends, whether anything moved: one stats
  query (the newest event's time and the number of events, so a session
  sent late counts too) and vm-wow's Battle.net fingerprint. Only then does
  it build the set again, and the routes answer the old set until the new
  one is complete. The market comes from VictoriaMetrics on its own clock,
  so its answer alone is rebuilt every 5 minutes. Everything is rebuilt at
  least daily. A store that is down keeps the last set, marked stale. With
  several people each person has their own set, from their own stores,
  behind the same login checks. Sets live only in memory, never on disk or
  on the share port, and the owner's comes first. After a restart, the
  first build takes a few seconds per character; until then the answers
  are worked out when asked, as below.

- **Events** (`wowstats/eventcache.py`): every query scoped to one character
  is answered in Python (`wowstats/logsql.py`, the part of LogsQL the views
  use) from that character's events, read once and kept. The first read
  asks for an hourly count, then reads the events in pages of at most
  `VL_PAGE` (5,000) rows, each hour once. After that, each refresh (at most
  every 30 s) asks for the hourly count again and re-reads only the hours
  whose count moved: the shipper sends a session after it ends, with the
  events' own times, so a "since the last time" cursor would miss them.
  Parallel requests for one character share one read. The auction scans and
  raw combat log lines stay out; queries that reach them, or no single
  character (the character list, the roster, prices), go to VictoriaLogs.
- **Answers worked out when asked** (before the first build, a spec or
  faction a plain open doesn't ask for, the publisher, the MCP): each one
  kept for `CACHE_TTL` (45 s).

New events reach the page within about a minute. An open page asks every 3
minutes, only while its tab is in view, whether its character's answer
changed, and then shows a "New data · Show" pill: nothing on the page moves
until the reader clicks it.

Every request to VictoriaLogs from the process, whoever makes it (the page,
the planner, the MCP, the publisher), goes through one gate of
`VL_CONCURRENCY` (2) at a time. A raw answer that fills its row limit is
read again in hourly-planned pages, never by halving the range from 2004
(which re-read the same full page about ten times).

What that bought (2026-11-18, one full-view load of a character with 23k
events, against a copy of a real store in VictoriaLogs v1.54.0 with 1 GiB): 130
LogsQL queries and a 650-700 MiB peak in VictoriaLogs before, 15 queries and
no rise over its idle memory after; the cache holds those 23k events in
about 17 MiB of the UI's memory.
