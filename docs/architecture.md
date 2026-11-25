# Architecture

**For:** contributors and curious self-hosters. **At the end:** you know the parts, how an event travels from the game to the page, and how the public view is kept apart.

Yes, this is absolute overkill for a WoW stats page. There are two databases,
an OAuth server and a headless Chromium whose only job is drawing your
character, all to say how fast you levelled.

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
                               │   (every 2 h, 23 days, daily force-merge)│
                               └──────────────────────────────────────────┘
```

Two optional parts sit beside it. The renderer (`wow-stats render`, its own
image) draws the paperdoll's pictures when the publisher asks. An AI provider
the host configures runs the play analysis on the server, on a timer
("The MCP seam" below). Without either, every page still works.

The runtime is the Python standard library plus the semantic layer: DuckDB
runs the metrics' SQL and the Boring Semantic Layer (on ibis and sqlglot)
compiles the metric catalogue to it, since every number on a page goes
through it. The rest are extras: the `publish` seal (pyca/cryptography), the
MCP endpoint (FastMCP, `mcp`), and one SDK per analysis provider
(`analysis`, `analysis-openai`). uv manages the project and the dev tools
(pytest, ruff); the image holds a venv with the `wowstats` package and those
four extras. The renderer's image is its own: Playwright, without DuckDB or
the semantic layer.

## Parts

| Path | What |
|---|---|
| `shipper/wow_stats.py` | The player's script (a uv inline script, stdlib only): archive, extract, ship. It cuts other players down to a name, when, where and how you met them before anything leaves the PC, and reads the game's hotfix cache for the item facts the server asks for. Its own installers for macOS (launchd) and Windows (Task Scheduler). `docs/contract.md` lists what it sends, `docs/shipper.md` how it runs. |
| `wowstats/cli.py` | `wow-stats ui` and `wow-stats exporter`. |
| `wowstats/ui.py` | The HTTP server: config checks, logins, routing, the in-memory cache, `/ingest`. |
| `wowstats/prebuilt.py` | The page's answers, built in the background once per new data and served as they are ("Caching" below). |
| `wowstats/stores.py` | Reads from VictoriaLogs (LogsQL) and VictoriaMetrics (PromQL), plus the helpers that merge and dedupe rows (newest `_time` per `source_id`, then highest `rev`). Every VictoriaLogs request goes through one gate (`VL_GATE`). |
| `wowstats/eventstore.py` | Where the events live: VictoriaLogs' HTTP API, or one SQLite file per person (`EVENTS_STORE=sqlite:<folder>`) answered by `wowstats/logsql.py`. Every read, write (`/ingest`), deletion and health check of the events goes through it. |
| `wowstats/eventcache.py`, `wowstats/logsql.py` | One character's events kept in the process and refreshed by the hour that changed; the views' LogsQL answered over them ("Caching" below). |
| `wowstats/views.py` | Builds each JSON answer from the stores: characters, header, climb, levels, zones, guide steps, dungeons, deaths, players, flights, prices, market. Treats `Name Surname` and `Name-Surname` as one character. |
| `wowstats/journal.py` | Splits the play history into sessions (more than 330 s without a Recorder record, the semantic layer's rule: `stores.split_sessions`) and builds one journal entry per session. |
| `wowstats/coverage.py` | What an answer stood on: one value per source (complete, partial, unavailable, unknown, with its time, the Recorder's gaps and the fix), from the missing list, Polestar's level splits and the Battle.net read. On `/api/char`, every MCP answer on one character, `query_metric` and `play_sql`. |
| `wowstats/redact.py` | The public and `/zogzog/` tiers: allow lists of what may leave the server. |
| `wowstats/items.py` | Item names and qualities for the loot history (Forever's ItemSparse via wago.tools). |
| `wowstats/itemfacts.py` | Item facts (name, slot, item level, stats) the shipper reads from the player's own client for items our reference data lacks yet; kept per person, the reference always wins. |
| `wowstats/otherplayers.py` | The same cut of other players' data as the shipper, applied at `/ingest` to what an older shipper still sends. |
| `wowstats/quests.py` | Which quests a character has done: the whole history (Recorder, quest addons, every gear and bags sheet) plus what Battle.net says it wears, read live and never kept. One answer for every page and tool. |
| `wowstats/exporter.py` | The Battle.net exporter: every character every 2 hours (`PROFILE_INTERVAL`). |
| `wowstats/armory.py`, `wowstats/pictures.py`, `wowstats/renderer.py` | The paperdoll's picture and each past level's: our renderer's still of the 3D model ("Pictures" below). |
| `wowstats/mcp/` | The tools and prompts, and the MCP endpoint on FastMCP (`server.py`: `/mcp` and the OIDC proxy that logs claude.ai, ChatGPT and Claude Code in through your identity provider, on `MCP_PORT`). `docs/mcp.md`. |
| `wowstats/plan/` | Session plans: `facts.py` gathers game data (never the Battle.net store), `estimate.py` computes every number, `store.py` keeps plans and compares them with what happened, `write.py` the checks on a client's write-up, `http.py` the full view's plan box. No model. |
| `wowstats/plan/dungeonview.py`, `wowstats/links.py` | The Dungeons panel of the full view and `/zogzog/`: one block per dungeon in reach (drops for the character's weights, obtained or missing; quests; what a trip is worth; a verdict on the whole value), from the planner's inputs, with Forever links to Wowhead. |
| `wowstats/plan/dungeongain.py` | One more run of a dungeon: XP at the character's level now (its own runs there, kill XP carried by the KB's `xp.kill_formula` over the creatures it killed; else the quests alone), the time (its runs, else the KB's clear time plus its own travel), the share of the level, XP per hour against its own questing, the quests to pick up. One estimate for the Overview's dungeon move, the Dungeons panel and the MCP's `dungeons` (`options`). |
| `wowstats/analysis/drive.py`, `auto.py`, `providers.py` | The play analysis, driven over MCP by the user's own model, or by the server's (`auto.py`, on its timer, through `providers.py`: Anthropic, OpenAI or any OpenAI-compatible endpoint) when the host configured one: the brief in pages, read-only SQL, the judge (`verify.check`), the pass state machine (survey, lanes, follow-ups, coverage, synthesis) with the clash and feasibility checks. `passes.py` holds the instructions; `__init__.py` the per-character state, the nightly recheck and the shared page's lines; `manifest.py` what each build was computed with, so the recheck knows when a finding needs judging again. |
| `wowstats/mcp/plantools.py`, `analysistools.py`, `helppage.py` | The plan, KB and analysis tools on the MCP; the sealed `/help/` page built from the registry. |
| `data/route-reference.json` | Polestar's route clock (GPL-3.0), packaged as `wowstats/data/`. `docs/route-reference.md`; remade by `tools/route_reference/`. |
| `data/gear-reference.json` | Polestar's gear data: items, sources, quests (GPL-3.0 with MIT parts, `THIRD_PARTY.md`), packaged as `wowstats/data/`. `docs/gear-reference.md`; remade by `tools/gear_reference/`. |
| `data/foreverdb/` | Where each piece of gear comes from, from ForeverDB.net's addon data (GPL-3.0 with an attribution term; its LICENSE, NOTICE and COPYING beside it), read first by `wowstats/gear.py`. Remade by `tools/foreverdb_sources/export.py`, refreshed by the KB watch; `docs/gear-reference.md`. |
| `data/foreverdb-weights.json` | The stat weights per class, spec and mode, from ForeverDB.net (credited on every score), packaged as `wowstats/data/`. Remade by `tools/foreverdb_weights/export.py`; `docs/gear-reference.md`. |
| `data/dungeons/quests.json` | Which dungeon each quest belongs to (forever-db's zone, a boss it kills, Polestar's cited dungeon journal and its dungeon runs), so a dungeon quest handed in outside counts toward its run (`wowstats/dungeonquests.py`). `docs/dungeon-xp.md`; remade by `tools/dungeon_quests/`. |
| `data/forever-reference.json` | Forever's quest and item names, quest levels and zones, every area and map, and which areas are dungeons with their level range (forever-db at its own release, GPL-3.0; Forever's client tables at the newest build), packaged as `wowstats/data/`. `docs/forever-reference.md`; remade by `tools/forever_reference/`. |
| `data/battlenet-look.json` | The geosets each Battle.net look choice shows, for the options Forever's own tables don't have (a gnome's Beard and Mustache are one "Facial Hair" in Forever): numbers only, from the current game's client tables (as facts), remade by `tools/battlenet_look/export.py`; read by `wowstats/character3d.py`. |
| `data/trainers-reference.json` | Who trains each profession rank and riding on Forever, where (zone and map coordinates), the level a rank needs and the price the guides give, with whose price it is (Polestar's shipped profession guides and riding reminder, GPL-3.0), packaged as `wowstats/data/`. Read by `wowstats/trainers.py` for the Overview's profession and riding moves; remade by `tools/trainers/export.py`. A rank with no trainer in it (Artisan Enchanting, the secondary skills past Journeyman) shows "trainer location not in our data". |
| `data/craft-reference.json` | What a craft takes: Polestar's profession guides (each step's crafts with counts or a target skill, colours and reagents, the recipes and trainer ranks on the way, the prices its notes give for trainer recipes, pattern vendors and vendor goods) and Forever's client tables (every recipe's colours, reagents and made item, which recipe item teaches which recipe, which crafted items bind when picked up), packaged as `wowstats/data/`. Read by `wowstats/crafting.py` for the craft lines of the gear advice and the `professions` tool; remade by `tools/craft_reference/export.py` (`--check` offline). |
| `data/class-reference.json`, `wowstats/classguide.py` | The class guide: per class the class trainers (capitals and starting towns, with side and map coordinates), what they teach at each level (rank, cost, the races a spell is for) and Forever's talent names and arrows (Polestar's `ClassTrainers.lua` and `ClassSpells.lua`, GPL-3.0, and Forever's client tables), remade by `tools/class_reference/export.py`; with the KB's `classes` lane (`data/kb/research/classes.json`: sourced statements per class and topic, one talent path per class), the character page's "your class at level N" block and the MCP's `class_guide`. |
| `data/kb/research/class-meta.json`, `wowstats/classmeta.py` | The state of the meta per class and spec (the KB's `class-meta` lane plus the weekly meta pass's class statements): the card's short paragraph (`char.meta`, also on `/zogzog/`), and on the full view `/api/meta`: the full description and the character's adherence (talents against the class path, gear against Polestar's best-at-level, enchants), with its gaps as moves; the MCP's `class_meta`. |
| `wowstats/gear.py` | Gear advice: ForeverDB.net's weights and formula (`data/foreverdb-weights.json`), per character, spec and mode (Leveling, PvP, PvE); behind `/api/gear` (both views) and the MCP `gear` tool. |
| `web/` | The pages: `index.html`, `app.js`, `app.css`, and `base.css` + `theme.js`, which every page loads (fonts, palettes, the header from `wowstats/bar.py`, the theme switch); no build step, no third-party code. Fonts are vendored in `web/fonts` (Cinzel, Alegreya, Alegreya Sans: SIL Open Font License, the licence files next to them) and served by the UI itself, so the CSP stays `font-src 'self'`. The crest, slot glyphs and frames are drawn in `app.js` and `app.css`: no game art. Packaged inside the wheel as `wowstats/web`. |
| `deploy/compose/` | The standalone kit. |
| `dev/seed.py` | The demo: the docs site's made-up character (`tools/fixtures/generate.py`, `build_demo`), marked `demo: true`, into a store or a ui's `/ingest`; the compose kit's `demo` service runs it. |

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
   no location now, deaths as counts, the Overview's moves minus those about creatures,
   and the coverage the full view's lines read. The public answer's coverage is one state
   per source its page draws a line for (`coverage.public`), never a time or a gap.
2. **Full-only routes don't exist.** `/api/market`, `/api/prices`,
   `/api/logsql`, `/model3d/*`, `/api/model3d` and the login door
   (`FULL_DOOR_PATH`, `/full/`) answer 404, a public server starts no MCP
   endpoint (no `MCP_PORT` listener), and it gets no `VM_URL` at all, even if one is set. (The static public
   site does carry the character's look from Battle.net in its 3D view and
   picture: `wow-stats publish` builds it from the full view. Market data
   stays out of it; `tests/test_publish.py`.)
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
the still (our renderer's picture of the same model, `wowstats/armory.py`, or a silhouette) for
the model in place, and back. Everything it draws comes from the player's
own game files: nothing is fetched from anyone else.

- **The files.** The shipper sends what the server asks for
  (`/install/assets/need`): Forever's client tables, then each model,
  skin, skeleton, animation and texture the character's recipes name, by
  file id, into that person's own folder (`wowstats/assets.py`,
  `MODEL3D_ASSETS_DIR`, `users/<id>/assets` with several people). What it
  asks for (`Assets.wanted`) is the union of every recipe's files over the
  person's characters, now, at each past level and in every outfit of the
  history (each gear sheet sent, each point the Recorder's replay put an
  item on), both models, both sexes when the sex isn't known, each outfit
  once; worked out again every 10 minutes. The shipper sends only what the
  store lacks, so each file goes once.
- **The answer.** `/api/model3d` (`wowstats/model3d.py`) reads the person's
  tables (`character3d.load_tables`) and assembles a recipe
  (`character3d.assemble`): the race and sex's model (ChrRaces and
  ChrModel: the classic eight and Forever's Skyborne, its own model), the
  saved look from Battle.net (`find_look`; a choice Forever's tables
  lack becomes the Forever choice showing the same geosets,
  `data/battlenet-look.json`), each drawn item's models,
  textures and geosets, the enchant's glow. Neck, rings, trinkets, ammo,
  quivers, relics and wands aren't drawn, like in game; a hunter holds the
  ranged weapon. Items left out are listed under the model with the
  reason, each as its icon. No tables yet, or the model's file not here
  yet: no recipe, and `why` says so in one line on the still.
- **The frame.** `/model3d/view` is a page of its own: our viewer
  (`web/model3d/m3d/`, a port of wow.export's renderer to plain WebGL) and
  `view.js`, inlined and allowed by hash (`model3d.view_page`). It needs no
  eval and no WebAssembly. It is still a box: `sandbox="allow-scripts"`
  with no `allow-same-origin` (an opaque origin: no cookies, storage,
  /api/* or the page around it) and `connect-src 'none'`. It asks the page
  for each file over postMessage, and the page fetches only
  `model3d/file/<id>` (the person's own store, behind the same login, or
  the bundle's copy) and the answer to draw (in /zogzog/, opened with the
  key there: the key never reaches the frame).
- **Updated or Classic.** A switch under the model draws the original
  models (`recipe_sd`, when the race has one in the files) or the updated
  ones (`recipe`), like the game's Character Models option. The game only
  saves the updated look, so the original one takes the nearest choice of
  each option, and the choices it lacks are named (`no_classic`). The
  visitor's pick is kept in the browser; without one the page opens on the
  person's Settings choice (`models`, or `MODEL3D_MODELS` with one person),
  else Updated. The past levels' pictures are drawn Updated.
- **The sites.** The publisher puts each character's answers in the bundles
  (sealed in /zogzog/) and, under `model3d/file/<id>`, only the files that
  character's recipes need, from that person's store; the log says how
  many and how big for each character.
- Sex comes from Battle.net's profile (the exporter). Without it, the page
  offers a male/female pick for that character, kept in the browser only.

### Pictures

Every picture on the paperdoll is drawn here: the character as it is now and
each past level, with the same viewer as the 3D view, in a headless Chromium.
Nothing comes from Blizzard's render host. Not in the
publisher: that pod holds every zogzog key (the owner's, and
`ZOGZOG_MASTER_KEY_FILE` that the friends' keys derive from), and Chromium runs without its own sandbox there
(an unprivileged container has no user namespaces for it). The drawing has
a process, an image and a pod of its own:

- **`wow-stats render`** (`wowstats/renderer.py`, the image's `renderer`
  target, image `wow-mcp-render`): `POST /render` takes a
  recipe (file ids, geosets, texture layers: `renderer.parse_recipe` checks
  every field and its size) and answers a 720x960 WebP with the character
  on a clear background. When it lacks files, it answers 428 with their ids;
  the publisher sends each from the person's store (`POST /file/<id>`, the
  token, `X-Content-SHA256`, 48 MB at most, game files only) and asks
  again. It keeps them by id within `RENDER_CACHE_MB`. It holds nothing
  else: no key, no store, no Secret, no service-account token, no character
  name. Chromium loads the 3D view's own page (`view.html?render=1`) from a
  server on 127.0.0.1 inside the process; any other request it tries is
  refused before it leaves (`page.route`). One render at a time. Out:
  nothing. In: the publisher only.
- **The publisher** (`wowstats/pictures.py`, `RENDER_URL`) sends the
  3D answer's recipe (now, or a past level) and keeps the picture under
  `PUBLISH_DIR/pictures/<sha256 of the request>.webp`. A level is drawn once,
  and two levels in the same gear and look share one picture. Both bundles
  get `data/picture/<char>/<level>.webp` (sealed in `/zogzog/`), and the
  snapshot's answer says `picture: {ext}` or `{pending}`. Today's still goes
  to `data/render/<char>.webp` (`wowstats/armory.py`), drawn again when the
  gear or look changes.
- **Cost.** About 4 s and 4.5 CPU-seconds a picture at one core (SwiftShader,
  measured in the image), 700-850 MB at the peak, 30 KB a
  file. A build spends at most `RENDER_BUDGET_S` (60) drawing; the rest stay
  pending and the page shows the silhouette with "Picture on its way. View
  it in 3D meanwhile." and the 3D toggle. The next pass builds again while pictures are pending (at once
  if the last build drew one, 15 minutes later if it drew none). A level-32
  character is drawn in a few passes, then never again unless its gear or
  look changes.
- **The 30-day rule.** The look comes from Battle.net's profile, so a
  picture is drawn again once it is 25 days old, and one the last build
  didn't use is deleted.

The live full view has no renderer. It shows a past level as the 3D model,
and today's still from the static site (`RENDER_FROM`, served at
`/render/<char>.webp`, WebP only). `RENDER_URL` empty: no pictures in the
bundles either, and the page keeps the silhouette and the 3D toggle.

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

**The user's own client does the reasoning**: for plans, always; for the
play analysis, whenever the user asks (`analyse`, `dashboard_refresh`), at no
cost to the host. When the host configured a provider, the full view also
runs the analysis itself (`wowstats/analysis/auto.py`, playing the MCP
client with the same tools and judge) for a character with
`ANALYSIS_STALE_PLAYED_HOURS` (10 by default) of play since its last
refresh, inside a monthly budget; `ANALYSIS_AUTO=off` keeps the provider
configured and the timer off. Only that timer starts a server run: no MCP
call does, and plans never call a model. See [Server-side analysis](self-host/server-analysis.md).
The server prepares data and judges: a kept finding is one whose numbers match
its saved query on a re-run, not one proven right. Blizzard API data reaches a model
through three doors only: the MCP's `market` tool (token price, ruleset
status), the character's current talents in `character_summary`,
`class_guide`, `class_meta` and `sanity_check`, and what the character
wears now, as quests it proves done (`gear`, `next_moves`, the planner).
All three are read live from vm-wow and kept nowhere. On a multi-user install all three follow the
person's "Battle.net data to my AI" setting (their own data to their own
assistant); a one-person install never serves
`market` and gives the rest to the owner, who set `VM_URL`. `tests/route/test_market_gating.py` holds that line, and the
analysis' allowlist (`wowstats/analysis/TABLES`) has no Battle.net source.

## The semantic layer

Every number the page and the MCP show goes through one model file,
`wowstats/analysis/semantic.yaml` ([Semantic layer](reference/semantic-layer.md)). The tables
under it are DuckDB, a columnar engine made for these GROUP BYs and the dialect the layer
compiles to; they were SQLite before the layer. The compiler is
[Boring Semantic Layer](https://github.com/boringdata/boring-semantic-layer) (MIT): measures,
dimensions, time grains and ratios in YAML, compiled to SQL through ibis and sqlglot. Only the
compiler runs here (no ibis backend, so no pandas or pyarrow in the image). The others looked
at:

- **dbt MetricFlow** (Apache-2.0): the reference design, but 0.214 caps Python at 3.14 and
  the image runs 3.15. It also wants a dbt manifest and a time spine table.
- **Cube**: a Node server next to the app, heavier than the whole image.
- **Malloy**: its own query language to learn, and the Python package was last released in
  September 2024.
- **Wren Engine**: pulls pandas, pyarrow and boto3.

## Data retention

A character's look comes from Battle.net's profile API through the exporter
and its VictoriaMetrics, never from VictoriaLogs (`docs/deploy.md`, "Appearance and the
30-day rule"), and so do the talents. No add-on stands in for either: a
character the API doesn't know gets its race's default look.

| Store | Holds | Retention |
|---|---|---|
| VictoriaLogs (or the SQLite files) | the shipper's events (the player's own game files) | as long as the player wants (compose: 100 years; the files: until deleted) |
| VictoriaMetrics | the exporter's Battle.net data, characters' looks included | 23 days, daily force-merge, never copied (Blizzard's API terms allow 30) |
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
  A read is whole or it says it isn't: an hour over a page is counted
  again by the minute, a minute over a page by the second, and a page that
  comes back fuller than its count (events landed in between) is read again
  finer. A second still over a page can't be split, so the character's
  answers fail with "no answer rather than a partial one" until it fits.
  Same rule for any other paged VictoriaLogs read (`stores.paged_read`).
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

What that bought (one full-view load of a character with 23k events, against a copy of a real store in VictoriaLogs v1.54.0 with 1 GiB): 130
LogsQL queries and a 650-700 MiB peak in VictoriaLogs before, 15 queries and
no rise over its idle memory after; the cache holds those 23k events in
about 17 MiB of the UI's memory.
