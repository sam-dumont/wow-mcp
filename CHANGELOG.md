# Changelog

## 0.5.0 (2026-11-19)

The first public edition. The start page asks one question at a time, the
install says what is missing, and the advice says where it disagrees with
Polestar.

**The public edition.** This tree now builds the release anyone can run:
the license in every binary, a compose kit that builds from the tree, the
demo's five made-up characters, synthetic test fixtures, `mcp-stdio` for an
AI client on the same machine, docs for running it yourself, and images
for amd64 and arm64. Nothing in it names a private server. `ui` gets a
1 GiB memory limit (`UI_MEM_LIMIT`): it peaked at 472 MiB on 55 weeks of
the demo, 100,705 events, which took 6.5 MiB on disk.

**`/start` asks one thing per visit.** The seven-step wizard showed about
1,650 words every time. Now one state at a time: not installed (the install
command for your system), installed and waiting (play), nothing arrived
(the one fix), or data arriving (when it last did, and "Open my page").
50 to 90 words visible; the rest sits in folds, the full manual at
`/start/print`.

**The install ends with a checklist, the pages say what's missing.** The
installer checks the WoW folder, Polestar, the Recorder and its switch,
Questie or Altoholic, the optional addons, saved data, the server and the
background program: a tick, or a cross with one fix. Only a missing WoW
folder, a refused password or an unregistered background program fail it.
The shipper sends a small setup report, and the full view, `/start` and
the MCP show one "Missing: ... <fix>" line per kind of data you don't send
yet. `docs/videos/steps.json` is the one addon list.

**Dungeon advice follows Polestar, and says why when it doesn't.** At level
32 it said Scarlet Monastery: Library while Polestar said Razorfen Kraul.
Below Polestar's entry level only a measured reason sends you in (its
quests ready, or your runs beating your questing XP per hour); gear alone
never does. Razorfen Kraul and Gnomeregan now show their quests: four
dungeons file them under a second area id.

**Upgrades from the Auction House, and random-suffix items scored on real
stats.** An upgrade on sale in your latest Auctionator scan (today or
yesterday) shows its price, the gain per gold and "cheap" when it costs
what you make in one level; a drop on sale says "or buy it now". A
random-suffix item is scored on its actual suffix when we have the link,
else on its likely one, not on armor alone.

**No more auto-reload.** The full view redrew every section each minute,
closing folds and moving the page. It now checks every 3 minutes while the
tab is visible and shows a "New data · Show" pill.

**The character picture, Forever's weapons in 3D, one item level.** The
picture works with several people on one server, weapons Forever added
show in the 3D view with their own texture, and the panel shows one item
level.

**The MCP login works through a reverse proxy.** `docs/mcp.md` says which
paths must keep their cookies (`/authorize`, `/consent`,
`/oauth/callback`), and a test runs the whole login through a proxy.

**Releases you can check.** Each image carries its build provenance and an
SBOM per platform, and from the public repository GitHub attestations and a
cosign signature: `docs/development.md`, "Verifying an image". CI fails on
a known vulnerability in any locked package, a secret in the history or a
broken workflow; Dependabot proposes updates weekly. SECURITY.md says how
to report a vulnerability privately.

### Upgrading

- The images now have a `HEALTHCHECK` (`python -m wowstats.healthcheck`,
  it picks the port from the mode): `docker ps` and compose show
  `healthy`. Kubernetes ignores it and keeps its own probes.
- New OCI labels on both images: `org.opencontainers.image.version`,
  `revision`, `title` and `description`.

## 0.4.3 (2026-11-19)

Friends get in by the list alone, every past level gets a picture, every
class gets a guide, and quests done before Polestar count.

**Quests done before Polestar count as done.** The Recorder only sees the
quests you hand in while it runs, so a quest finished before you installed
it read as not done: The Stockade's Bazil Thredd showed as blocked by The
Unsent Letter, handed in long before. One answer now serves the Dungeons
panel, its blockers, the plan, the gear advice and the MCP
(`wowstats/quests.py`). A quest is done when the Recorder saw it handed in,
when the game flagged it done to the Recorder, when Questie or Altoholic
saved it in the game's full list of completed quests (new `wow-quests`
feed from the shipper), when you hold gear only that quest gives, or when
a quest that needed it is done (forever-db's prerequisites, down the
chain: this one works with no extra addon). Altoholic saves that list in a
way that loses some low bits; the shipper only sends what it can trust.
The addon list now says Questie or Altoholic gives the quests you did
before Polestar. The MCP's plan facts carry `quests_done_from`, a count per
source.

**The help page starts with what to do.** `/help/` was a wall of text: the
required addons said three times, a raw address in a sentence, three
install routes mixed together, and a friend had to read it all to find the
first step. It is now one page in the order a friend needs it. The first
phone screen says what you get and gives 3 steps with one action each (the
CurseForge app and Polestar, your start page for your own install command
with Mac and Windows tabs, then play). The install and update commands now
put Polestar Recorder in the AddOns folder themselves: the official release
pinned at 0.4.1.1, its SHA-256 checked, never over a newer copy, the game's
saved data untouched; the zip by hand stays as a fold. Uninstall still
leaves every addon in place. Below: your page (the
tour, one line per tab), your AI (its address, an app picker, 3 things to
ask), 6 questions for when something is wrong, and everything else folded
(the addons by hand, what the program sends, the sending check, update,
uninstall). 337 words show before anything is opened; a browser test keeps
it under 350 and the 3 steps inside the first 390x844 screen.

**A guide for every class.** The character page says what the character's
class needs at its level: the trainer spells to buy now and at the next
training level, with the class trainers in the capitals of its side (its
race's capital first, Wowhead links and map coordinates), the next talent
points (from its own talents when an export holds them), and a habit or
two. It shows on the public page, `/zogzog/` and the full view: it is built
from the class, race, side, level and talents only. The MCP's new
`class_guide` tool gives the whole guide for any class and level: spec and
talent path by bracket, stat priority, solo play, dungeon role, class
quests, common mistakes and the respec moment, every statement sourced
(official, datamined, guide authors) with its date, confidence and scope,
and the contradictions between sources. The knowledge base gains the
`classes` lane; every talent path is checked against Forever's current
trees, and the trainer lists and talent names come from Forever's own data
(`tools/class_reference/export.py`).
**The dungeon estimate says the verdict first.** "Worth it: ~35 min for ~17k
XP (27% of a level), 1.4× faster than your questing." Under 0.8× of the
player's questing it reads "Slower than questing: go for the drops only", in
between "About the same as questing" (`FASTER`/`SLOWER` in
`plan/dungeongain.py`). The move and the Dungeons panel now go verdict, quests
to pick up, then the drops, and a quest blocked by another says where to get
that one first ("The Unsent Letter first: An Unsent Letter drops from Edwin
VanCleef"). Same line in the MCP's `dungeon_options`.

**The gear comparison is readable in both themes.** Opened under the doll
by day, it showed cream text on parchment: the character plate kept its
night text colours but took the day page's panel colour. The plate now
takes the whole night palette and the journal's parchment the whole day
palette, so anything opened on them gets its background and its text from
the same set. Item names darken by day through the palette too, not by a
page-wide rule, so they stay bright on the plate. A few day colours went a
shade darker to clear 4.5:1 (muted text, gold, the journal's muted and gold
at night). The comparison also drops the score's arithmetic line: the
header already gives the gain. `tests/test_contrast_browser.py` checks every
visible text in the comparison, the tooltip, the dungeon cards, the
character plate and the journal entries for WCAG AA, Chromium and WebKit,
dark and light.

**Dungeons link their guide.** A dungeon's name sent you to Wowhead's zone
page, a map and a list. It now opens Wowhead's Forever dungeon guide (how
to get in, quests, loot), with a "Dungeon guide" link next to the levels;
Scarlet Monastery, Dire Maul, Stratholme and Blackrock Spire list one guide
per wing. 32 guides, each fetched on 2026-11-19, kept as a static list in
the knowledge base (`dungeon.wowhead_guides`). Krol'dok Stronghold,
Blackmaw Hold and Shaper's Terrace have no guide yet and keep their old
link. The entrance still links the zone, and the MCP's `dungeon_options`
gives each dungeon its link too. `tests/test_links.py` holds every link
builder to the `/forever/` form and every fixed link (guides, addon pages,
the Ask links) to the checked list.

**Dungeon or quest? The dungeon move says.** The Overview's dungeon move,
the Dungeons panel and the MCP's `dungeon_options` now give one more run of
the dungeon at the character's level, from one estimate: the time, the XP,
the share of the level, and the XP per hour against the player's own
questing, then the dungeon's quests to pick up first, who gives them and
where. "One run: ~35 min, ~17k XP (27% of level 32), about 1.4× your
questing pace." The numbers come from the player's own runs there, the kill
XP carried to the level now with Classic's kill formula over the creatures
killed (`xp.kill_formula` in the KB). A dungeon never run gets the KB's
clear time plus the player's own travel time, and the quests' XP alone,
said so: its kill XP is never guessed. `dungeon_options` used the average
of every trip anywhere, so its XP per hour disagreed with the page; it
gives the same `gain` now.

**Friends get in.** A friend on the list got "Not set up yet ... its
organisation isn't allowed" on the full view: the login proxy sent the org
header empty, and the web view refused anyone without it. The friends list
alone decides now, on the web and the MCP alike: the owner's sub, or a sub
on `WOW_FRIENDS_FILE`, nothing else. A listed friend who logs in before the
next pass over the list is set up on the spot. Without the list only the
owner gets in. `WOW_OWNER_ORG`, `OIDC_ALLOWED_ORGS` and `PROXY_ORG_HEADER`
no longer do anything with `WOW_STATE_DIR`, and neither do the MCP's role
and org checks there. A refused login gets a page in the app's own style:
"This account doesn't have access yet. Ask <the owner> to add you.", with a log-out
link to switch accounts.

**A picture for every past level.** Level by level now shows the character
as it looked at that level, in that level's gear, with "View in 3D" next to
it, on the public page and `/zogzog/`. The pictures are drawn by a new
renderer, `wow-stats render`, in its own image
(the renderer image, the Dockerfile's `renderer` target) and its own pod that holds no key:
the publisher sends it the race, sex, look and item display ids and keeps
the picture. A picture not drawn yet says so, and the 3D view works
meanwhile. Set `RENDER_URL` on the publisher (docs/deploy.md).

**A character name can't write outside its bundle.** The publisher used
names from stored events as file names and wrote wherever the decoded path
led: `../../victim/b1/public/data/char/X` landed next to the build. A name
is now letters, space, hyphen, apostrophe and ASCII digits, starting with a
letter (`wowstats/names.py`). `/ingest` refuses a line with any other, the
shipper skips it, and the publisher skips a stored one and logs it. Every
bundle write also resolves its destination and refuses one outside the
bundle.

**`/zogzog/` names nobody in clear.** Its link preview carried the
character's name and title in the page's HTML, even with the public page
off. Sealed pages get a generic preview; the title appears once the data is
open.

**"Make a new link" works for the owner.** His key came from
`ZOGZOG_KEY_FILE` whatever the generation, so he got the same link back. The
file now seeds his first link only; after that his key is derived from the
master like a friend's. No key or link is printed any more, not even the
first one: `wow-stats zogzog-key link` prints it on demand. Install codes
are out of the request log.

**Removing the last friend removes them.** A valid `{}` friends list means
nobody, and offboards everyone listed before. A missing, unreadable or
broken file still changes nothing.

**MCP tools say what they write.** `plan_session`, `analysis_brief`,
`propose_finding` and `analysis_submit` are marked as writers that can
replace data; `play_sql` and the KB lookups as writers that only add to a
run's coverage. A test keeps every tool classified.

**MCP answers that were wrong.** `sessions` mixed creature levels into the
level range (a level-10 hunter showed 3 to 14). `character_summary` counted
deaths by `source_id` and said 0 without it; it now counts them like
`deaths`. XP per hour is rounded, not truncated, everywhere. `mount_goal`
and `dungeon_options` answered with an error when the time to a level was
unknown; they answer with what's known and say what's missing.
`check_plan` passed a dungeon plan after "no dungeons": it now checks the
plan against the constraints and the time budget and names the one broken.

**The friends' manual.** The help page (`/help/`) covers what a friend
needs without asking: what you get, the install, how to tell the shipper
runs, a section per part of your page (Overview, Character, dungeons, the
plan, Timeline, Journal, the zogzog and public pages, Settings), your AI,
updating, uninstalling and how to have your data deleted. Eight clips go
with it, a dark and a light take each, played in the reader's theme: the
onboarding (45 s), a tour of the page (60 s), the zogzog link, Settings,
and the Mac terminal for the install, the shipper's log, the update and the
uninstall. The start page plays the onboarding at the top and the install
clip at step 4, served with byte ranges (Safari needs them). `demo/`
records them on a throwaway lab with invented characters
(`demo/README.md`). Windows has no clip: VHS can't record PowerShell on a
Mac.

**Your AI, in one screen.** The help page's AI part is the server, the
client picker, five one-line asks and what your AI sees. The 34-tool
reference is gone from the page: the AI reads it from the server, and every
tool argument now describes itself in its schema (`market`'s `faction` had
borrowed a levelling route's text). The addon list is a name and a line
each; the Recorder's CurseForge caveat is said once, in the step that needs
it. The level ring's CSS class was `.badge`, so every badge on the help
page drew as an 82 px dark ring over its text: it is `.lvring` now, and a
browser test keeps badges one line tall.

## 0.4.2 (2026-11-19)

The friends list decides who's in, and Forever's new dungeons have the right
levels.

**Friends come and go with the list** (#25). With `WOW_FRIENDS_FILE` (rendered
from your identity provider: user id and slug), the full view checks the list
every 2 minutes. A listed friend gets their tenant and slug before their
first login; a login that isn't listed gets none. A friend who leaves the
list is offboarded on the spot: their events (their VictoriaLogs tenant),
state, registry entry and upload password go, their shipper gets a 401, the
MCP refuses them, and the publisher drops their site from every static pod
(`GET /bundles` on the upload port). A missing, empty or unreadable list
changes nothing, and the owner is never touched. `wow-stats users reconcile`
runs a pass now. The privacy page says how long backups keep events (30 days).

**Forever's dungeon ranges** (#23). Ruins of Lordaeron is 15-22, Excavation
Site: Wetlands 26-33 and City of Dalaran 28-35. They come from the game
client's own table, which Polestar agrees with and the owner checked in game.
Blizzard's announcement gave narrower ranges; the KB keeps that statement
and says why it lost.

## 0.4.1 (2026-11-19)

Bags and bank count, a sourced knowledge base, Windows tested end to end,
and pages that only say what to do.

**Bags and bank** (#17). What you carry now counts. An upgrade you already
hold says "In your bags: equip it" (or "in the bank: take it out first") on
the Character page, the Overview, the plans and the MCP's `gear`, instead of
where it drops. This also finds upgrades that drop nowhere in particular,
such as vendor items and random drops. The Dungeons panel says when a drop
you have is still an upgrade to equip. Emptied bags now send an empty list,
so an old one doesn't linger.

**Knowledge base** (#18). Six research lanes are in `data/kb/research/`:
game rules, dungeons, levelling, economy and professions, PvP and raids.
That's 1,585 statements, 158 contradictions kept side by side and 330 open
questions. Each statement has a source, a date and a confidence. The loader
and the tests refuse a source that isn't on the allow-list. Profession ranks,
training prices, riding, the talent capstone, the instance limit and
Forever's new dungeons come from the KB now, not from constants in the code.
New MCP tool `kb_topics`; `kb_rule` shows a rule's conflicts.

**The KB stays current** (#18, `docs/kb.md`). The `kb-refresh` workflow
watches the game build, the hotfix pages, Polestar and the addons every day
and opens one issue per kind of change. It proposes new statements as a PR,
never merged on its own and capped at $3 a run. Once a month it checks
every cited link.

**Windows, tested for real** (#16). A CI job on Windows runs a friend's
whole path: the `/start` install line in PowerShell 5.1 and 7 under a fresh
Windows's Restricted policy, the scheduled task, a ship to `/ingest`, the
update line, a password reset and the uninstall line. It runs for a plain
profile and for `C:\Users\Zoë Ü` without Git. Fixed along the way: uv's
installer failed under the Restricted policy; the task could pick a
virtualenv's Python; the uninstall line failed in PowerShell 7; an accented
WoW folder came back garbled; the app couldn't lock its user list on
Windows. A git failure in the shipper log now shows git's own message.

**Pages say what to do** (#19). The Overview, Character and Dungeons pages
dropped their explainers and footers. Actions, numbers and Wowhead links
stay. A caveat that changes the action stays in a few words, like "100g
(less with reputation)".

**Shared pages show actions only** (#19). How-you-play patterns stay in the
full view. `/zogzog/` gets the analysis nudges in full, minus any that name
another player, a creature from your records, a time, a date or where you
are right now.

**Empty states** (#19). No data yet, a level-1 character or nothing
equipped: the page names the next install step instead of a spinner or a
blank panel. `/settings` can reset the upload password and shows the new
command right away.

**Every item has a name** (#19). About 12,000 items showed as "Unknown item
(211500)". They have names now. An item with no name anywhere shows as
"Head item, see on Wowhead", never as a number.

**Fixes.** `/ingest` stores `component=wow` as a stream field, so a store
filtered on `{component="wow"}` (a shared `LOGS_FILTER`) sees events sent
through the app, friends' included (#17). A level-1 character with no play
session got the other faction's dungeons (#19).

**CI** runs on pushes to main and on PRs again, without the browser
tests: those run locally before every push and release (#20, #21).

### Upgrading from 0.4.0

- No new or changed variables, ports, volumes or routes (`docs/deploy.md`
  is unchanged for the deployment). Pin image 0.4.1 with its digest on
  every pod.
- Move your own shipper to `/ingest`. In `~/.config/wow-stats/config.toml`,
  set `[ship]` `url = "https://<full host>/ingest"`, `user` to your slug
  and the password to the upload password from `/settings` (reset
  it there to get a fresh one). Your events land in tenant 0, the store the
  pages read, with `component=wow`. Then stop sending to the old receiver,
  so each event arrives once (duplicates are harmless anyway: every event
  has a `source_id`).

## 0.4.0 (2026-11-19)

The big one: friends can have their own wow-stats on the same install, no
model runs on the server anymore, and every page tells you what to do next
instead of just what happened.

**Several people on one install.** Anyone in an allowed org who logs in
gets their own store tenant, state, upload password, zogzog key and site
(`<name>-wow.<domain>`) at first login. Nobody is added by hand. `/start`
walks a new player through the install with a single-use code (15
minutes); updates use one code-free command (`/install/update`). `/settings`,
`/privacy`, and `wow-stats users list|hosts|disable|enable|delete` for the
owner. Two-tenant tests check that one person never sees another's names,
over every page, MCP tool, plan, analysis and built site.

**No model on the server.** The Coach button, `/api/coach`, the nightly
model run and the `wow-stats-agent` image (340 MB of Claude Code CLI) are
gone. The play analysis is now driven by your own assistant over the MCP:
it asks its own questions in SQL (`analysis_brief`, `play_sql`), and the
server re-runs every finding before keeping it (`propose_finding`). Every
finding says what to do, where, what blocks it and the next step; one
without that is kept as an observation. **Ask Claude** and **Ask ChatGPT**
on the character page open a chat with the instruction typed in and no
data in the link.

**MCP on FastMCP**, on
its own port (8090). The login is your identity provider (OIDC) only: the
passphrase login is gone, and the server refuses to start with
`MCP_LOGIN=password`. Works with 14 clients (Claude, Claude Code, ChatGPT,
Codex, Cursor, VS Code, Gemini CLI...). Battle.net market data reaches an
assistant only for a person who turns on "Battle.net data to my AI".

**Overview**: one verdict, four numbers (pace against the route, next
level, guide steps in order, rested XP) and at most 3 moves on show, each
naming the item, quest, dungeon, trainer or zone, with Wowhead Forever
links. /zogzog/ keeps its moves but never one about a creature.

**Character tab**: click a slot to compare what you wear with the best
replacement stat by stat; upgrades by where you get them or by slot; real
gates ("Level 35 + Tailoring 200", not just the level).

**Dungeons**: one block per dungeon that matters to you, with a verdict on
the whole trip (gear, quests, XP, gold), the next step, the entrance from
Polestar's data, the drops scored for your spec, the quests and what a run
is worth. A character below every dungeon gets the first three that open.

**Help page** (`/help/#<key>`, sealed under its own key): an Install part
from the same steps as `/start`, and an MCP part for developers with a
client picker and a card per tool.

**Lighter on VictoriaLogs.** One cache per character serves every endpoint:
a full-view load went from 132 queries to 15 and the store's peak from 691
to 146 MiB on a local copy (vl-wow was OOM-killed three times on
2026-11-18). At most 2 VictoriaLogs requests at once per process
(`VL_CONCURRENCY`).

**Fixed**

- Deaths named "creature 1871" when the recorder had no name for the
  killer: now forever-db's name (13,226 creatures), else "an elite
  creature" or "an unnamed creature". Never an id.
- The Overview crashed for a character with no level anywhere (no
  snapshot, no ding). The level now comes from the newest ding when there's
  no snapshot.
- The help page scrolled sideways at 390 px.

**Upgrading**: docs/deploy.md, "Upgrading from 0.3.x", lists every env
var to remove, change and add.
