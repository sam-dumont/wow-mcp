# How this was built

**For:** anyone curious why a levelling log ended up with an MCP server, a semantic layer and
signed releases. **At the end:** you know how it started, the turns it took, and the choices
behind what it is now.

This is the project's history, from its first commit on 18 November 2026 to version
{{app_version}}, the one these docs describe. For what it does today, read
[How it works](explanation/how-it-works.md); for the code, [Architecture](architecture.md).

<!-- check: the opening question. The logs only say "started levelling in WoW Forever and wanted the numbers". Put your own words here if you remember them. -->
I started levelling a character in WoW Forever in November 2026 and wanted to know two boring
things: am I slow, and where did the hours go? [Polestar](https://github.com/fitzforge/polestar)
already had the route and a clock. What it didn't have was my history: time per level against
the route, which dungeons were worth the trip, what to wear next. I run a homelab with a
Kubernetes cluster, a log database and a couple of MCP servers, so the answer came out
homelab-shaped.

<!-- check: 17 versions = 0.1.0, 0.2.0-0.2.4, 0.3.0-0.3.1, 0.4.0-0.4.3, 0.5.0, 0.6.0, 0.6.1, 0.7.0, 0.8.0 (tags of both repos); PRs = 167 private + 203 public at 2026-11-23 -->
It took six days: 17 versions, 8 release candidates and about 370 pull requests between
18 and 23 November. I wrote very little of the code by hand. Claude Code sessions did, often
four or five agents in parallel, while I read the plans, made the calls and played the game
that produced the test data. <!-- check: is that how you want the AI part told? -->

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
for friends whose key sits in the link. <!-- check: say where the name "zogzog" comes from, or leave it unexplained? -->
0.3.0 came that evening with the first play analysis: a **Coach** button that ran a model on
the server, on the player's own API key.

## 19 November: friends, and the model leaves the server

0.4.0, just after midnight, made it a place for several people. Anyone in an allowed group who
logs in gets their own store, upload password, sealed key and site, and tests with two people
check that one never sees the other's names, on every page, tool and built site.

The same release took the model off the server: the Coach button, the nightly model run and a
340 MB image of Claude Code's CLI went. Your own assistant drives the analysis over MCP now, and
the server re-runs every finding's query before keeping it. <!-- check: the reason. My reading: a model on the server means the host pays for everyone's tokens and sees everyone's prompts. -->

Real data found the first load problem: the homelab's WoW store was killed for running out of
memory three times on the 18th. One cache per character took a page load from
132 queries to 15, and the store's peak from 691 to 146 MiB.

Dungeon advice learned to follow Polestar. At level 32 it said Scarlet Monastery while
Polestar said Razorfen Kraul, and my ruling was "we need a REASON why it deviates from the
guide". It still says why whenever it disagrees.

That evening 0.5.0 became the public edition, here in sam-dumont/wow-mcp: images for amd64 and
arm64 with their provenance and signatures, the demo's made-up characters, and nothing that
names a private server. <!-- check: the public repo started from a fresh export, not the private history? --> A security review the same day made every deployment shape fail closed:
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

## 23 November: 0.7.0, 0.8.0, and SQLite

0.7.0 and 0.8.0 shipped the same day. 0.8.0 plans a dungeon as a whole trip (pickups, travel,
time inside, hand-ins, at your own pace) and estimates kill XP for dungeons you haven't run,
checked against a recorded Stockade clear (+3%) and two City of Dalaran runs (−10%). Your AI
went from 41 tools to 38, one question each.

The last change before these docs: your events can live in one SQLite file per person instead
of VictoriaLogs. One container fewer and nothing to size, for a host with a handful of
players. VictoriaLogs stays the default. <!-- check: the reason for adding SQLite -->

Then this rewrite of the docs, for 1.0. <!-- check: what 1.0 means to you (frozen tools and env vars? the upgrade promise?) and its date -->

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

**Polestar is the source of truth.** The route clock and the gear scoring are fitzforge's work.
wow-stats follows each release, and only a licence change or a format it can't read stops it.

**Each friend's data apart.** One store per person, one key per person, a friends list only the
host edits, and {{purge_days}} days (`FRIENDS_PURGE_DAYS`) before a removed friend's data goes.

Yes, a hardened image, signed releases and an OAuth server is a lot for levelling stats. The
homelab habits came along for free; the rest of the code is about the game.
