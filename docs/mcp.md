# Ask your assistant about your character (MCP)

**For:** self-hosters and contributors who want the details behind the MCP. **At the end:** you know how the tools, the plans, the play analysis and the login work inside. To connect only: [Connect your AI](ai/connect.md).

The full view can answer questions from your own assistant: "why was level
12 so slow?", "plan my next hour", "analyse how I play". Add `/mcp` as a
connector in claude.ai (web, desktop, phone), Claude Code, ChatGPT or any
MCP client, and the model calls the tools below itself, in your own account.
Nothing to pay beyond your plan.

**The reasoning is your client's.** The server prepares the
data, holds the analysis' state and judges every number: a kept finding is
one whose numbers match its saved query on a re-run, and the query is kept
with it for you to read. That checks the numbers, not the reading. The one
exception is opt-in: a host who configures an AI provider has the server
run the analysis itself after `ANALYSIS_STALE_PLAYED_HOURS` of play without
a refresh ([Server-side analysis](self-host/server-analysis.md)); no MCP
call starts one. The character page's **Ask Claude** and **Ask ChatGPT** links
open a new chat (`https://claude.ai/new?q=`, `https://chatgpt.com/?prompt=`: ChatGPT's `?q=` would send it at once) with
one instruction typed in, "Using the wow connector, run the analyse prompt
for <character>.", and nothing else: no data in the link, nothing sent
until you press send.

It is off in `VIEW=public`: every path below answers 404 there. In the
full view the MCP is off too until `MCP_PUBLIC_URL` is set.

## Tools

| Tool | What it answers |
| --- | --- |
| `character_summary` | Level, XP, gold, time in this level, pace over the last 3 levels, ETA to the next |
| `levels` | Time per level, slowest and median, from Polestar's splits |
| `sessions` | Play sessions (a 20-minute gap ends one), with XP, deaths, quests, dungeons, who you grouped with |
| `zones` | Time, XP and deaths per zone, XP per hour |
| `guide_steps` | Time per guide step, steps you jumped or skipped, and the guide's expected time on Polestar's route |
| `deaths` | Deaths by zone and what killed you |
| `dungeons` | Runs, length, XP per hour |
| `players` | People you grouped with, how often |
| `prices` | Your own Auctionator scans (feed `wow-prices`) |
| `gear` | Per slot what is worn, the best item at this level and the next within 3 levels, then the next upgrades with where to get them; Polestar's scoring for the character's spec (or the one asked for). docs/gear-reference.md |
| `route_reference` | Expected time per level, per guide and per step on Polestar's route (see "Route times") |
| `next_moves` | "Besides following Polestar, what should I do now?" Upgrades in reach, dungeons that drop them, profession trainer visits due, rested XP, time to the next level; each point with the data it came from |
| `sanity_check` | "Is there anything stupid I'm doing?" Death clusters (zone, creature, level gap) with the likely killer, guide steps far over plan, steps passed over, upgrades not taken, a capped profession, unspent talent points, slow levels; each finding with its data |
| `people` | Players seen (Polestar's sightings) and dungeon runs with group size, bosses, wipes and deaths. No addon saves who was in a group, and the tool says so |
| `plan_session` | A time-boxed plan for the next `hours` (0.5 to 8), with `constraints` in plain text ("no dungeons", "level tailoring") and `context` (`levelling`; `max-level-pve` and `pvp` answer "not supported yet" with KB pointers). Returns the estimator's `facts` (every number), its own `markdown`, the `blocks` and a `brief` for the client's model. See "Session plans" |
| `check_plan` | A write-up of the latest plan against its facts and the player's own constraints: lists every number in the text that no fact holds, and every sentence that breaks a constraint from `plan_session` (an activity ruled out, such as "no dungeons", or more time than the plan has), naming it |
| `how_did_my_plan_go` | The latest plans against what happened: per checkpoint, the online time planned and needed; the calibration factor the next plans use. Deterministic |
| `next_step` | The next 30 to 45 minutes: the first block of a short plan, the town errands, what to drop |
| `gear_options` | Per slot, every source kind with its cost: quest (where), dungeon (whole-trip time / drop chance), world drop (luck, AH), craft (skill needed), and any other kind a source carries |
| `dungeon_options` | Dungeons ranked: fits now or opens at level N; one more run at your level now (`gain`: time, XP, share of the level, XP/h against your questing, the quests to pick up; the same line as the Overview and the Dungeons panel); expected gear per clear, top items; for done ones a redo verdict and the loop numbers |
| `mount_goal` | Each riding tier (from `data/kb/rules.json`): gold needed, gold left, net gold per hour, ETA in played time and level, priced options to speed it up; what isn't known (the time to a level past the route's clock) is listed under `unknown` |
| `whats_new` | Verified analysis findings new or changed in the last week |
| `kb_search` | The knowledge base, full text, each hit with its source URL, type, confidence and scope (`forever` or `classic_fallback`); `scope` filters |
| `kb_rule` | One static rule (`riding.apprentice`, `rested.accrual`...) with its value, source and `last_verified`, plus any other lane's differing version under `conflicts`, or "unknown" |
| `kb_topics` | The research catalogue by lane (game rules, dungeons, levelling, economy and professions, PvP, raids): counts and the day each was checked; with `lane`, its documents, contradictions (with sources) and open questions |
| `class_guide` | How to level the character's class at its level (or `class` and `level`): the levelling spec, the talent picks for this bracket and the next (from the character's current talents on Battle.net when the MCP may read them, see below), the trainer spells to buy now and next with the class trainers in the capitals of its side, stat priority, solo play, dungeon role, class quests, mistakes and the respec moment; every statement with its source, date, confidence and scope, and the contradictions between sources (the KB's `classes` lane) |
| `class_meta` | The state of the meta for the character's class and spec (or `class`, `spec`, `level`): how each spec stands, the build, stat priority, gear, enchants by slot, consumables, professions and what changed lately, every statement with its source, date and confidence (the KB's `class-meta` lane and the weekly meta pass); for the character, how close it is: a 0-100 score from its talents, gear and enchants, and the top gaps with where to fix each and what is coming that concerns it, with the prep it needs (see "How close am I to the meta" in the player's guide) |
| `talent_changes` | The character's talent changes: every one from WoWSims Exporter's saves (`source`) when the player has it, else the last 3 weeks of Battle.net's reads. The day, the points per tree before and after, the talents gained or dropped with their ranks; `not_recorded` names talents the addon can't save. Newest first; `changes` is null with a `why` when there's neither (see below) |
| `news` | What's new on Forever: this week's digest from the weekly KB pass (or everything `since` a day, or an earlier `week`), one line per item in our words with its category, day, source and who it concerns (classes, levels, roles), the character's first (or filtered by `class`, `level`, `role`); the events to come with their announced day, or "announced, no date"; and `community`, what players are saying on the forums and r/wowforever (opinions, never facts; `category=community` for those alone) |
| `play_analysis` | The character's kept analysis: findings with their SQL, nudges with feasibility, the shared lines, the last run and recheck |
| `analyse` | The `analyse` prompt as a tool (ChatGPT has no prompts): the procedure and where the run stands |
| `analysis_brief` | The play analysis' brief in sections sized for a 32k context: `start`, `schema`, `profile` and `kb` (paged, 12,000 characters a page), `survey`, `lane`, `followups`, `coverage`, `synthesis` |
| `play_sql` | One read-only SELECT on that database, paged (40 rows by default, 60 at most, 1,000 deep, 6,000 characters a page). It runs in a child process with 512 MB over the data and the query's timeout: a query past either fails alone, with a plain error, and the server carries on |
| `propose_finding` | The judge: re-runs the finding's SQL, checks every number, `expect`, `holds` and KB ids, then stores it for the character or refuses it with the reason |
| `analysis_submit` | Hands in a pass (`survey`, `lane`, `pick`, `synthesis`); the server checks it and answers with the next step |
| `analysis_progress` | Where the run stands (pass, lanes, queries, tables untouched, proposed, verified, refused with why), the next step, the last run and the nightly recheck |
| `kb_get`, `kb_lookup` | Knowledge base documents (up to 10) and names (up to 100) by id |
| `dashboard_refresh` | Refreshes the caller's play analysis on the client: their own model runs it, so it costs the server nothing and starts nothing there ([Server-side analysis](self-host/server-analysis.md)). Picks the character whose analysis is most out of date (or `char`) and returns `analyse`'s procedure for it, plus `do`: analysis_brief, the passes, then analysis_submit, which refreshes the dashboard and resets that character's timer. Under `server_automatic_refresh`, the server's automatic refreshes only: each character's play since its refresh against the threshold, the last refresh and who did it, whether an automatic refresh of theirs runs or waits, how the last one ended, the install's budget and the caller's own share |

Every tool takes `char` (default: `DEFAULT_CHAR`, else the character seen
last). Time ranges take `7d`, `36h`, `2w` or an ISO date. Rates per hour are
rounded to a whole number, the same in every tool and page.

Each tool's annotations say what it does: read-only, except the ones that
write the character's analysis run or plans. `play_sql`, `kb_search`,
`kb_get` and `kb_lookup` only add to a run's coverage (with `char`, during a
run); `analysis_brief` (a new run, `restart`, new goals), `propose_finding`
(a finding under a kept key), `analysis_submit` and `plan_session` can
replace what is kept, so they are marked destructive. `dashboard_refresh`
reads only: it hands the run to the caller's own model and starts nothing on
the server.

Every object in an answer that carries a game id (`item_id`, `quest_id`,
`npc_id`, `spell_id`, `object_id`, `zone_id`/`area_id`, `quest_ids`) also
carries its Wowhead Forever page, `https://www.wowhead.com/forever/<kind>=<id>`
(`wowstats/links.py`): `wowhead` for the row's own id, `<field>_wowhead` for
the others (a quest reward's `quest_wowhead`). `gear` adds `where_wowhead`,
one link per `where` entry (the quest, the boss, the craft's spell). Added on
the way out of `/mcp` (`wowstats/mcp/wowhead.py`), so a new id field gets its
link without touching the tool.

## Prompts

In claude.ai's `+` menu and as Claude Code slash commands. Each takes at
most one optional argument and answers in a phone-sized format. The
examples show the shape of what the tools return for a level 32 character;
the client's model writes them up.

| Prompt | Argument | Asks | Example |
| --- | --- | --- | --- |
| `plan` | `hours` (3) | `plan_session`, `check_plan` | "start to +47 min · Southshore, steps 40 to 42 → 32.3; Polestar plans 21 min, at your pace about 47 min (questing 1.86x over 143 steps)" ... "about 32.9 at the end" |
| `now` | | `next_step` | "start to +27 min · steps 40 to 41 (Southshore to Bartolo Ginsetti) → 32.2; class trainer: last visit at level 31" |
| `recap` | | `how_did_my_plan_go` | "no plan to compare yet: make one (plan_session), play past its first checkpoint, then ask again" |
| `gear` | | `gear_options` | "Off hand: Orb of Mystic Insight +33.1, craft, you can make it now; Traitor's Finger +32.6, a quest in Desolace" |
| `dungeon` | | `dungeon_options` | "1. City of Dalaran (28-35), +43.2 gear per clear, Ponderous Orb 33% ... The Stockade: worth looping for gold toward the mount, about 6g 93s/h (≈ 39 runs)" |
| `check` | | `sanity_check`, analysis findings | death clusters, steps passed over, upgrades not taken |
| `week` | | `whats_new` | "no play analysis yet" |
| `analyse` | `char`, `goals` | the analysis tools, pass by pass | see "The play analysis" |
| `mount` | | `mount_goal` | "Riding at 40: 100g needed, 72g 15s to go at 30s/h net: about 240h30 of play" |
| `coach` | `char`, `focus` | the whole set | the long walk-through: summary, route, levels, steps, sessions, gear, next session |

The old `next_moves` and `sanity_check` prompts are gone (the tools stay):
`now` and `check` replace them.

## Session plans

`plan_session` never calls a model. A deterministic estimator
(`wowstats/plan/estimate.py`) computes every number from your own play:
pace per kind of step (questing, travel, town, dungeon: your time over the
route's planned time, from 5 steps up, else your overall factor, else the
route's own), XP per hour per kind, rested burn (measured), unplanned stops
(the AFK share of your recent sessions), your dungeon trips, gear sources,
gold per hour and the mount tiers from the KB's sourced rules. A missing
rule is "unknown", never a guess. A character with no finished level
measured gets the route's clock instead.

Plans are kept in `ANALYSIS_STATE_DIR/plans.json` (30 per character, game
data only: the planner never reads Battle.net data, so nothing there needs
the 30-day expiry). `how_did_my_plan_go` lays each plan over the XP that
followed; the median of time needed over time planned becomes the
calibration the next plans divide their XP rate by (0.5 to 2, with its n).

The client's model writes the plan up from `facts` per the `brief`, then
calls `check_plan`, which lists any number not in the facts and any sentence
that breaks the player's constraints from the request (an activity they ruled
out, more time than they gave). The full view's
plan box shows the estimator's own text.

## The play analysis

Your assistant runs it; the server keeps the run and judges. `analyse` (the
prompt, or the tool) gives the procedure, and each answer's `next` says what
to do. `dashboard_refresh` is the same entry with the character picked for
you (the one most out of date) and the server's automatic-refresh timers:

1. **survey**: read `schema`, `profile` and `kb` (page by page on a small
   context), then hand in 25 to 40 open questions. The server adds one
   neutral question for any table no question names, and cuts them into
   lanes of three.
2. **lanes**: per lane, `play_sql` and the KB, each finding to
   `propose_finding`, then the lane's answers and up to five "ask next".
3. **follow-ups**: pick the "ask next" items worth a second round (8 at
   most); they get lanes of two.
4. **coverage**: a table no query has read yet gets its own lane.
5. **synthesis**: the run's verified findings, against each other. A clash
   gets a reconciling query (the server re-runs it) and either one merged
   finding (verified like any) or both marked doubtful. `keep` decides what
   stays; nudges carry a verdict (`now`, `at_level`, `not_relevant`) and the
   KB ids of their constraints, which the server checks against the
   character's level; the nudges meant for the shared page are screened
   (below).

The brief is neutral on purpose: the schema, a profile of every table, the
tools, the player's goals in their own words and the pass' task. No
hypothesis, no category: the model asks its own questions.

Everything shown says what to do. A finding carries an
`action`: what to do, where, what blocks it, the next step. The judge
refuses one too vague to act on (`do` and `next_step` need a few real
words); a finding sent with `action: null` is kept as an **observation**,
shown apart in the full view. A nudge needs a `next_step` too, or it is
dropped. Pattern lines describe how the player plays: the full view keeps
them folded with the observations, and they never reach `/zogzog/`.
`/zogzog/` gets the nudges whole (what, where, what blocks it, the next
step), each screened for the tier: zones, dungeons, items and levels are
fine there; another player, a creature from the records, a clock time, a
date, a number above 100 typed by hand or where the character is now ("you
are in ...") keeps it in the full view only. Text is gender-neutral: a title, claim, action, nudge or pattern
that says he or she is refused or dropped, with the reason. The synthesis'
answer lists every finding, nudge and pattern dropped, and why. Goals given once
(`goals`) stay with the character. A finding's claim names its numbers as
placeholders over the first row of its SQL; the judge refuses any number
above 100 typed by hand, a re-run that disagrees with `expect`, a `holds`
that fails, and lists KB ids it doesn't know.

State is per character, in `ANALYSIS_STATE_DIR/chars/<character>.<hash>/`
(the name in lower case, letters outside a-z as `_`, then 10 hex characters
of the full name's hash, so two names that differ only by an accent never
share a folder; `analysis.json` for what is kept, `drive.json` for the open
run), so
`analysis_progress` lets "continue" pick up in a new chat. The full view
reads it; `/zogzog/` gets the screened nudges only. Each night
(`ANALYSIS_HOUR`, 4, so from 04:00 local time, `WOW_TIMEZONE`, by default) a deterministic recheck re-runs every
kept finding on the fresh data. One that still holds stays; it gains weight only
when there's play since the last time it did, so the same data checked again
adds nothing. One that no longer holds fades and goes after three nights. No model.

## The knowledge base

One KB (`wowstats/analysis/kb.py`, `kb.shared()`) serves the analysis, the
planner and the MCP. Every document carries its source:
Forever's client tables (wago.tools, high), Polestar's data (GitHub at the
commit, medium), research notes (per statement), and the static rules in
`data/kb/rules.json`, and the six research lanes under `data/kb/research/`
(statements, contradictions, open questions). Every rule, statement and
contradiction needs `source_url`, `source_type`, `date`, `confidence`,
`scope` and `last_verified`, cites only allow-listed domains, and is never
surer than its sources, or it is refused (`docs/kb.md`). Commands cite a KB
id as "rule as of <last_verified>, <source>" or say "unknown". The pages
never show any of it: sources live here, in the KB and in the docs.

## The help page for friends

`wow-stats publish` writes `/help/#<key>` on the static site when
`HELP_KEY_FILE` holds a key (32 random bytes, base64url, 43 characters).
It is the friends' manual: no login, one link the owner sends, so it works
before anything else does. Three parts behind one sticky switch:

- **Install**, rendered from `docs/videos/steps.json`, the same file as the
  start page's wizard: what you get, the steps everyone can follow
  (`audience: both`, or a step's `generic` text), the addons with their
  links, the shipper and how to tell it runs, updating (the code-free
  update line), uninstalling and troubleshooting. The personal install
  command stays on `/start`; the page points there.
- **Your page**, from the same file's `guide.page`: the Overview, the
  Character tab, dungeons, the plan, the Timeline, the Journal, the private
  and public pages, Settings.
- **Your AI** (MCP), short on purpose: the server URL, a client picker
  ("How to connect" is `CLIENTS` in `wowstats/mcp/helppage.py`: name, steps,
  a snippet to copy, how to tell it worked, unchecked clients marked:
  Claude, Claude Code, ChatGPT, Codex, opencode, VS Code, Copilot CLI,
  Cursor, Gemini CLI, Zed, Goose, pi with its MCP adapter, LM Studio,
  Windsurf), four or five one-line things to ask (`ASKS`) and what the AI
  sees (`GROUPS`), in plain words. No tool reference: the AI reads it from
  the server, so every argument describes itself in its schema. Tests fail
  on a tool without a group or an argument without a description.

A step or section's `media` names a clip in `docs/videos/media`, one per
theme (`<name>-dark.mp4`, `<name>-light.mp4`, made by `demo/`). The clips
are sealed with the page under the same key and play in the reader's theme.

The page only says how; the server still needs each client's CIMD host or
redirect in `MCP_CIMD_HOSTS` / `MCP_REDIRECT_URIS`. Sealed like `/zogzog/`
but under its own key, so neither link opens the other; no character data
in it. The viewer's picks (part, client, OS) stay in their browser
(`localStorage`).
`HELP_MCP_URL` (else `MCP_PUBLIC_URL`) is the address it shows.

The full view serves the same page at `/help/` to anyone its login lets in,
linked as Help in every page's nav: same shell, script, stylesheet and
`render()`, without the seal (the reader is on that host already), the
full view's own `PUBLIC_URL` + `/mcp` as the address, the clips from
`/start/media/`. There the fragment is an ordinary anchor: `/help/#ai`,
`/help/#tr-no-data` or `/help/#x-update` lands on that part with its folds
open. It needs nothing besides the full view's catch-all route.

`next_moves` and `sanity_check` run the same rules as the Overview page
(`wowstats/insights.py`), so the page and your assistant never disagree. Both
list what they checked and what the saved data can't support yet:

- Checked by `sanity_check`: death clusters by zone, creature and level gap
  (killer marked "likely": Polestar_Recorder's death record names the last
  hostile creature that targeted you within 10 s, and the units and combat
  records before it give the fight's length and the attackers); guide steps
  over 3x their planned time; steps skipped or passed over; upgrades you
  could wear now; a profession capped at its trainer tier; unspent talent points (from Battle.net's current talents,
  when the MCP may read them: see below);
  levels much slower than your own.
- Not possible yet: bags full and vendor trash (the shipper sends which
  items the bags hold, not how full they are or what each sells for), flights not taken (no list of known flight paths),
  low-value grinding (kill records carry no XP), dungeon deaths blow by blow
  (no combat log).
- Checked by `next_moves`: upgrades in reach, dungeons with upgrades,
  profession trainer visits, rested XP, time to the next level.
- Not possible yet: class trainer visits (the recorder logs visits, not
  which spells are still unlearned), cheap auction-house wins.

## How login works, and why it isn't the page login

claude.ai and Claude Code connect to `/mcp` from their own servers or from
your terminal, not from your browser. They can't carry a browser cookie, so
a login that sits in front of the site as a cookie (basic auth, an OIDC
proxy) can't let them through. They speak OAuth: a 401 on `/mcp` points them
at `/.well-known/oauth-protected-resource/mcp`, which names this server as
its own authorization server, and they open `/authorize` in your browser.
You log in there once, at your identity provider, and they get a token for
`/mcp` and refresh it by themselves.

The endpoint is [FastMCP](https://gofastmcp.com) (the `mcp` extra, pinned)
with its OIDC proxy. It runs in the same process as the page but on its own port, `MCP_PORT`
(8090). Your reverse proxy sends these paths there, without the page login
in front (the tokens protect them):

- `/mcp`
- `/authorize`, `/token`, `/register`, `/revoke`, `/consent`
- `/oauth/callback` (your identity provider sends you back here)
- `/.well-known/oauth-authorization-server` and
  `/.well-known/oauth-protected-resource/mcp`

Everything else stays on the page's port (8080).

Let cookies through on `/authorize`, `/consent` and `/oauth/callback`: your
browser opens those pages, and the consent page ties itself to your browser
with a cookie. A proxy rule that drops the `Cookie` header there stops
every login at the Approve button with "Authorization session mismatch". The
other paths are called by the client's servers and need no cookie.

What the server does:

- Discovery: protected resource metadata (RFC 9728) and authorization server
  metadata (RFC 8414).
- Clients: a URL client id (a client metadata document, CIMD) is fetched
  only from the hosts in `MCP_CIMD_HOSTS` (Claude, ChatGPT/Codex, opencode,
  VS Code, Copilot CLI, Zed, Goose by default); a refused one is logged.
  Others register (`/register`): the whole registration is refused if one
  redirect isn't in `MCP_REDIRECT_URIS` (the web callbacks of claude.ai,
  ChatGPT, VS Code and Cursor, plus loopback on any port).
  `/help/` lists the clients and how to add each.
- PKCE S256, a consent page before the first login of each client, tokens
  bound to this `/mcp` URL. The login asks the provider for its account
  picker (`prompt=select_account`), so a browser signed in to several
  accounts never logs a connector in as the wrong one without asking; the
  Battle.net button skips it.
- Who gets in is checked at the login and again at every refresh, against
  the identity provider's own answer (the ID token and userinfo). With
  several people (wowstats.tenancy) the sub alone decides, by the web
  view's rule: the owner, or a sub on the friends list; a person who left
  the list or was switched off is refused on the next call. With one
  person per install the account must be in `OIDC_ALLOWED_SUBS`, and
  `OIDC_REQUIRED_ROLE` and `OIDC_ALLOWED_ORGS` add a project role and an
  org (read from the claims `OIDC_ROLES_CLAIM` and `OIDC_ORG_CLAIM` name): a role removed in the provider ends that login at
  its next refresh.

The only login is your identity provider (`MCP_LOGIN=oidc`). The old
passphrase login (`MCP_LOGIN=password`) is gone: the server refuses to
start with it. Make an app at your provider with the code flow, a client
secret, refresh tokens, and the callback `https://<your host>/oauth/callback`.

## Settings

| Variable | Default | What |
| --- | --- | --- |
| `MCP_PUBLIC_URL` | (required) | The URL clients use, `https://wow.example.com/mcp` |
| `MCP_PORT` | `8090` | The endpoint's port, on `BIND` |
| `MCP_LOGIN` | `oidc` | The only choice left |
| `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET_FILE` | | Your identity provider and its app |
| `OIDC_ALLOWED_SUBS` | | Comma separated account ids. Required unless several people (tenancy) |
| `OIDC_REQUIRED_ROLE`, `OIDC_ALLOWED_ORGS` | | One person per install: a project role and the orgs allowed, from userinfo. Ignored with several people (the friends list decides) |
| `OIDC_ORG_CLAIM`, `OIDC_ROLES_CLAIM` | ZITADEL's (`urn:zitadel:iam:...`) | The claim names that hold the org id and the roles: set your provider's |
| `OIDC_SCOPES` | `openid offline_access` (+ the org scope with `OIDC_ALLOWED_ORGS`) | What the app asks the provider for |
| `OIDC_BATTLENET_ORG_ID`, `OIDC_BATTLENET_IDP_ID` | | ZITADEL: a "Sign in with Battle.net" button on the consent page, for that org and identity provider. Both or neither |
| `MCP_SIGNING_KEY_FILE` | random per start | 32+ random bytes; signs the tokens and encrypts the stored logins. Without it every restart logs every connector out |
| `MCP_STATE_DIR` | | Client registrations and logins across restarts (encrypted, in `oauth/`) |
| `MCP_REDIRECT_URIS`, `MCP_CIMD_HOSTS` | the client list above | Replace the lists |
| `MCP_ALLOWED_ORIGINS` | | Extra browser origins allowed on `/mcp` |
| `MCP_AUTH` | `oauth` | `none` only works when `BIND` is loopback: for a laptop |
| `ROUTE_REFERENCE_FILE` | the bundled file | Another route times file, or `off` |

Make the signing key with `head -c 48 /dev/urandom > signing.key`.

## Connecting

- **claude.ai** (web or desktop): Settings > Connectors > Add custom
  connector, URL `https://<your host>/mcp`, no client id or secret. Connect,
  log in, done. It then shows up on the phone too: the mobile apps use
  connectors added on the web, they can't add one themselves.
- **Claude Code**: `claude mcp add --transport http wow https://<your host>/mcp`,
  then `/mcp` in a session (or `claude mcp login wow`) to log in.

### On your own machine, no login

`wow-stats mcp-stdio` serves the same tools over stdin/stdout, for an MCP
client on the computer that runs the server: no port, no login, nothing
anyone else can reach. It runs inside the `ui` container, so it reads the
same store and keeps plans and findings in the same `ui-state` volume. It
refuses `VIEW=public` and several-people installs (`WOW_STATE_DIR`): there,
use the HTTPS endpoint, which knows who is asking.

Claude Code:

```sh
claude mcp add wow-stats -- docker compose -f /path/to/deploy/compose/compose.yaml exec -T ui wow-stats mcp-stdio
```

Any other stdio client: the command `docker`, with the arguments
`compose -f /path/to/deploy/compose/compose.yaml exec -T ui wow-stats mcp-stdio`.

Outside Docker, `wow-stats ui` with `BIND=127.0.0.1` and `MCP_AUTH=none`
also serves `/mcp` over HTTP without a login, on loopback only (the server
refuses `MCP_AUTH=none` on any other bind, which is why the compose
container, bound to `0.0.0.0` inside Docker, can't).

## Route times (Polestar)

"Was that level slow?" needs to know how long it should take. The kit ships
that clock: `data/route-reference.json`, made by Polestar's own route planner
from Polestar v0.4.1's guides (packaged as `wowstats/data/route-reference.json`,
so it is in the image). Per side and level, per guide and per guide step, for
every starting race and class. `route_reference` reads it, and the `coach`
prompt compares your levels with it ("level 28 took you 4h10m, the route
plans 1h39m").

The numbers are Polestar's, GPL-3.0 like Polestar, and so is this kit.
[docs/route-reference.md](route-reference.md) says how the file is made, how
to remake it after a Polestar release, and how far to trust it (a model of a
quick player: real players take 1.3 to 3.3 times as long).

`ROUTE_REFERENCE_FILE` points at another file (the export's shape, or a
hand-made `{"levels": {"12": 2400}, "guides": {...}}`), `off` turns the
comparison off and the `coach` prompt compares you with your own past levels instead.

## Battle.net data reaches the MCP only when you turn it on

Your character's current talents come from Battle.net:
`character_summary` gives the spec, the points per tree and when Battle.net
read them, `talent_changes` what changed over the last 3 weeks (every change with WoWSims Exporter), and
`class_guide`, `class_meta` and `sanity_check` work from them. The MCP reads them live from vm-wow, the store the dashboard reads
(23 days), and keeps nothing. On a multi-user install the same "Battle.net
data to my AI" setting as below decides; without it `talents` is null and
`talents_why` says so. A one-person install has no such setting: the
person who set `VM_URL` gets their talents. No Battle.net client: no
talents, and the class guide follows the class path.

The WoW Token price and the ruleset status come from Blizzard's API (the
project reads no Auction House API: auction prices are your own Auctionator
scans). Blizzard's terms keep API data for 30 days at most and
don't allow passing it on, and a claude.ai chat keeps what a tool returned
for as long as the chat exists. So the `market` tool is off by default:
the MCP neither lists nor runs it, whatever the env. A person on a
multi-user install can turn on "Battle.net data to my AI" in Settings
(their own data to their own assistant); then
their MCP lists `market`, and only theirs. A one-person install has no
such setting and never serves it. The server's own analysis has no
Battle.net source, so without that setting Battle.net data reaches no model at all; the full view's
market strip shows it to you only. `prices`, your own Auctionator scans,
is game data, not API data: the MCP always has it.
