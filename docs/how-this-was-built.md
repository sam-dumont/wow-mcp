# How this was built

**For:** anyone curious why a levelling log ended up with an MCP server, a semantic layer and
signed releases. **At the end:** you know how it started, the turns it took, and the choices
behind what it is now.

This is the project's history, from its first commit on 18 November 2026 to version
{{app_version}}, the one these docs describe. For what it does today, read
[How it works](explanation/how-it-works.md); for the code, [Architecture](architecture.md).

I started levelling in WoW Forever and wanted the numbers: am I slow, and where did the hours
go? [Polestar](https://github.com/fitzforge/polestar)
already had the route and a clock. What it didn't have was my history: time per level against
the route, which dungeons were worth the trip, what to wear next. I run a homelab with a
Kubernetes cluster, a log database and a couple of MCP servers, so the answer came out
homelab-shaped.

I wrote very little of the code by hand. Claude Code sessions wrote the code, often four or
five agents in parallel, while I read the plans, made the calls and played the game that
produced the test data.

The work ran as many small pull requests, each through the same checks before it merged:
about 370 between 18 and 23 November. That's how the work was cut, and says nothing about
whether it's any good. What says more is further down: the review rounds and what they found,
and the tests that caught bugs on their first run.

## 18 November: a Mac, a folder and a log database

<!-- TODO(media): a screenshot of 0.1.0's armory page or the first Grafana dashboard, if one survives (no real character names). -->

The first version wasn't an app. A small agent on my Mac copied the game's `WTF` folder into a
local git repository every 5 minutes and at each logout, and turned the addons' saves into
events: 3,952 on the first run, 3,846 of them from Polestar's Recorder. They went into the
homelab's VictoriaLogs as one more feed next to the firewall logs, and a Grafana dashboard
read them.

By noon the code had its own private repository, wow-stats, and 0.1.0: an armory-style page, the
route times from Polestar's planner, and an MCP endpoint with its own OAuth so claude.ai could
reach it. Taking Polestar's route times put GPL-3.0 content in the image, so the whole kit has
been GPL-3.0 since that morning.

Three rules from that first day are still here:

- **Known addons only.** It reads Polestar and well-known addons; it never asks anyone to
  install one of mine.
- **Battle.net data lives 30 days at most**, because Blizzard's API terms say so. It got its
  own store, with 23 days of retention.
- **The public page shows nothing personal.** No gold, no other players, no market data. It
  became a static bundle built by its own service, served by a pod with no network access.

0.2.0 came that afternoon with the static sites: the public one, and `/zogzog/`, a sealed copy
for friends whose key sits in the link.
0.3.0 came that evening with the first play analysis: a **Coach** button that ran a model on
the server, on the player's own API key.

## 19 November: friends, and the model leaves the server

0.4.0, just after midnight, made it a place for several people. Anyone in an allowed group who
logs in gets their own store, upload password, sealed key and site, and tests with two people
check that one never sees the other's names, on every page, tool and built site.

The same release took the model off the server: the Coach button, the nightly model run and a
340 MB image of Claude Code's CLI went. Your own assistant drives the analysis over MCP now, and
the server re-runs every finding's query before keeping it. With the model on the server, the
host paid for everyone's tokens and saw everyone's prompts. Neither was a good deal for a
friend's install.

Real data found the first load problem: the homelab's WoW store was killed for running out of
memory three times on the 18th. One cache per character took a page load from
132 queries to 15, and the store's peak from 691 to 146 MiB.

Dungeon advice learned to follow Polestar. At level 32 it said Scarlet Monastery while
Polestar said Razorfen Kraul, and my ruling was "we need a REASON why it deviates from the
guide". It still says why whenever it disagrees.

That evening 0.5.0 became the public edition, here in sam-dumont/wow-mcp. It started fresh
instead of carrying the private history over: that history was a bit of a mess, and I wanted a
clean point in time to start from. Images for amd64 and arm64 with their provenance and
signatures, the demo's made-up characters, and nothing that names a private server. A security review the same day made every deployment shape fail closed:
the server refuses to start without a login and a list of the host names it answers to.

## 20 November: development moves to the public repository

Development moved here for good: one button cuts a release, and a private mirror runs the
heavier CI on my own runners. A second security review brought signed shipper updates and kept
other players' data on the player's PC unless a feature needs it. Shippers learned to update
themselves, and CI now installs the Windows one into a user folder called `Zoë Ü` without Git,
under PowerShell's strictest script policy, on every change.

The knowledge base, the game rules your assistant quotes with their sources, went on a weekly
refresh that merges by itself when its checks pass. My words: "the goal is automated
refreshes".

## 21 and 22 November: review rounds, and one way to count

The server's own analysis came back on the 21st, differently. It's off until the host
configures an AI provider and a monthly budget, and before it runs on the host's credit your
page offers the prompt to run it with your own assistant instead. It exists for friends who
never ask an assistant anything and still want to read "How you play".

Then six rounds of review in two days, each with fixes merged the same day. A few of the
catches:

- A server emptied by accident and refilled with `--backfill` got back 0 of 21 Recorder
  records. `--replay` and `--reextract` exist because of it.
- A rebuild from the archive dated old snapshots at rebuild time. Each archive commit now
  carries its capture time.
- The shipper writes its state with an fsync of the folder after each rename, and
  `F_FULLFSYNC` on macOS, because a crash at the wrong moment could resend old snapshots with
  new dates.
- An archive that doesn't answer fails the read instead of quietly shortening your history.

The worst one was simpler: the page said 1,188 kills and 94 quests, the MCP said 1,262 and 99,
for the same character. Two code paths counted differently. The fix was a semantic layer, a
catalogue of named metrics compiled to SQL, that the page, the tools and the analysis all read.
"Use the semantic model all the time." Since 0.7.0, one number means one thing everywhere.

On the 22nd I also settled who's right about the route: "Polestar is the source of truth and we
follow it". A new Polestar release is taken even when it moves route times or gear numbers.

## 23 November: three releases, and SQLite

0.7.0, 0.8.0 and 0.9.0 shipped the same day. 0.8.0 plans a dungeon as a whole trip (pickups, travel,
time inside, hand-ins, at your own pace) and estimates kill XP for dungeons you haven't run,
checked against a recorded Stockade clear (+3%) and two City of Dalaran runs (−10%). Your AI
went from 41 tools to 38, one question each.

0.9.0 let your events live in one SQLite file per person instead of VictoriaLogs. VictoriaLogs was already in my homelab, so I reused it, but it's not the
most common or convenient thing for anyone else to run. SQLite makes it a one-container install:
nothing to size, a folder to back up. VictoriaLogs stays the default.

The same day, integration slices went in: five made-up characters sent through the real upload
path, then read back through the page and the MCP tools, checking that one number means one
thing on every surface. On their first run they caught four bugs that the other tests had
missed, among them the page and the MCP disagreeing on the time spent in the current level,
and the dungeon list offering Alliance characters the Horde city's dungeon.

## 23 and 24 November: the gear turn

The scores didn't match the BiS everyone else uses, so I switched to
[ForeverDB.net](https://foreverdb.net)'s weights: the community's stat weights per spec, with
three modes. Leveling is the default, and there Stamina, armour and a caster's wand count for a
lot more than in a raid. Checked against ForeverDB's own ladders: 36,674 of 36,746 scores
equal. Polestar's Adjust weights and the rest of its gear settings stopped counting a few hours
later, and your pick of mode is kept per character, so the page and your AI score the same
way.

The items themselves now come from Forever's own client tables, not a Classic item list: Staff
of the Shade does 37 Shadow damage in Forever, not Classic's 21. Every score links to
ForeverDB.net, so anyone can check it.

Next came which upgrade to chase. My rule: "Convenience and easiness before a quarter of a
point." A 0.03% drop is never the pick; a quest reward, a vendor or an auction listing beats a
rare drop unless the drop scores clearly more (17.6% for a hard one).

On the morning of the 24th, next moves stopped telling an Expert enchanter to train Expert.
The Recorder had missed the trainer visit, so its last record (150/150) stood. Profession caps and riding
now also come from Altoholic's copy of the game's skill list and spellbook. When nothing saw
the training, the move says so and ends "if not done yet" instead of insisting. 0.10.0
shipped all of it that afternoon.

Then this rewrite of the docs, for 1.0. 1.0 means stable-ish: the tools your AI calls and the
settings you put in `.env` stay put, and anything that does change gets an upgrade note in the
[changelog](../CHANGELOG.md). It's a hobby project, so that's an intention, not a guarantee. It
ships when it's ready.

## Why it ended up like this

**No addon of my own.** Every addon is something a friend has to install and trust, and
something that breaks on a game patch. Polestar, Questie, Syndicator and the others are
already installed and maintained by people who know the game's API. The cost: what they don't
save, wow-stats can't know, and every page says so instead of guessing.

**Code first, models optional.** The page and the MCP tools work out every number in code. The
same data gives the same answer, with or without an AI. When an assistant writes a finding,
the server re-runs its query and writes the measured numbers into the text itself: a number
typed by the model is refused.

**Your AI, your bill.** Your assistant runs in your own account. The server's own analysis is
the host's choice, off by default, with a monthly cap, and your page says when it's on.

**VictoriaLogs by default, SQLite if you'd rather not.** VictoriaLogs is what my homelab
already runs for every other log, and my WoW events stay there forever. A host who doesn't want
a database to run picks SQLite before the first event.

**Polestar is the source of truth for the route.** The route clock is fitzforge's work. wow-stats
follows each release, and only a licence change or a format it can't read stops it. Gear scores
are ForeverDB.net's, and every score says so.

**Each friend's data apart.** One store per person, one key per person, a friends list only the
host edits, and {{purge_days}} days (`FRIENDS_PURGE_DAYS`) before a removed friend's data goes.

Yes, a hardened image, signed releases and an OAuth server is a lot for levelling stats. The
homelab habits came along for free; the rest of the code is about the game.
