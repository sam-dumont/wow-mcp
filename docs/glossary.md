# Glossary

**For:** anyone who hit a word on these pages and isn't sure what it means. **At the end:**
you know the word, and the page that says more.

Players' words first, then the ones people who run a server need.

## Playing

**Polestar.** The levelling addon wow-stats is built around: guides and a route with a time for
every step. Your pace is measured against it.
[Addons it reads](reference/addons.md).

**Polestar Recorder.** Polestar's companion addon. It writes down your play sessions, zones,
deaths and loot. It starts switched off: type `/psrec on` once in game (the Polestar Client
turns it on for you). The install command puts it in your AddOns folder.

**SavedVariables, the `WTF` folder.** Where every addon keeps its data: plain text files in
your WoW folder. WoW writes them only on `/reload`, logout or a character switch.

**`/reload`.** Reloads the game's interface, and saves every addon's files on the way. A crash
or Alt+F4 loses what happened since the last save, so `/reload` after a dungeon, an auction
house scan, or before a break.

**`_forever_`.** WoW Forever's own folder, inside the World of Warcraft folder Battle.net
installed. The `WTF` and `Interface/AddOns` folders the docs talk about are in it.

**The little program, the shipper.** What the install command puts on the computer you play
on. Every 5 minutes it reads what changed in `WTF`, sends it to your server with your upload
password, and keeps a copy in `WoWArchive` in your home folder.
[What it does](players/privacy.md#what-leaves-your-computer).

**Start page.** `/start` on your friend's server: your personal install command, and a tick
for each step once it works.

**Install command.** The one line you paste in PowerShell or Terminal. It works once, for 15
minutes. Updates later use a line without a code.

**Upload password.** What the little program uses to send your data. Settings can reset it;
the old one stops at once.

**Your page.** Everything about your characters, behind your login: the Overview, the
Character tab, the Timeline, the Journal. [Your page at a glance](players/your-page.md).

**Pace.** Your played time per level against Polestar's route, over your last 5 levels. 1.0×
is the route's pace, 2.0× twice as long. [Overview and next moves](players/overview.md).

**Next moves.** Up to 3 things to do now, on the Overview: an upgrade and where it drops, a
dungeon worth a run, a talent point or profession rank to sort out.

**Points.** An item's score for your spec on [ForeverDB.net](https://foreverdb.net)'s weights, in
the mode you pick on the gear page: Leveling (the default), PvP or PvE. The same scale in every
slot, so a ring and a helmet compare. Every score links to ForeverDB.net. [Gear and upgrades](players/gear.md#the-upgrades-list).

**BiS, the badge.** Best in slot: the best item you can get and wear at your level for that slot
(while you level, long shots aside). The badge on an item's icon says how close it is: BiS (orange), 2nd, or a share like 84% (purple
from 85%, blue from 50%). "70% → 91% on your chest" is that slot's share with what you wear
now, then with the item. [Gear and upgrades](players/gear.md#your-gear).

**Rested.** The rested XP you have banked. It grows while you're logged out in an inn or a
city.

**"Missing:" line.** What shows under a part of your page that an addon would fill, with the
fix. [A part of my page is empty](players/troubleshooting.md#a-part-of-my-page-is-empty).

**Zogzog link.** Your page for the friends you pick, at `/zogzog/`: the journal, the gear
advice, prices and deaths per zone. Its files are encrypted, and the key sits in the link after
the `#`, which a browser never sends to a server. Anyone with the whole link can open it.
[What's shared](players/privacy.md).

**Public page.** A cut-down card of your characters (climb, gear, dungeons) for anyone with
the address. Off until you switch it on in Settings.

**MCP, connector.** MCP is the protocol Claude, ChatGPT and other assistants use to call
outside tools. You add wow-stats to your assistant once, as a connector, and it can read your
data. [Connect your AI](ai/connect.md).

## Running a server

**The owner.** Whoever runs the server. On a friend's server, that's your friend: they make
your account and pick your page's address.

**Full view.** The page behind the login (your page, above), served by `wow-stats ui`. The
zogzog link and the public page are static sites that `wow-stats publish` builds from the same
data. [Deploy without compose](deploy.md).

**Friends list.** A JSON file that maps each friend's login to their page's name. Who isn't on
it doesn't get in. [Add friends](self-host/friends.md#the-friends-list).

**Events.** What the shipper sends: one line per thing that happened (a level, a quest, a
death, a loot). The server keeps them and works out everything else from them.
[The shipper's events](contract.md).

**Event store.** Where the events live: VictoriaLogs (the default, a log database the compose
kit runs for you) or one SQLite file per person, read by the app itself.
[Where your events live](install.md#where-your-events-live).

**Battle.net data.** Optional: with your own Battle.net API client, the server fetches your
characters' look (for the 3D view), gear and talents, and the WoW Token price. Blizzard's terms
cap it at 30 days. [Battle.net data](self-host/battlenet.md).

**Server-side analysis.** Optional: the server has an AI provider the host picks write a
character's "How you play" section once it has {{analysis_hours}} hours of play (`ANALYSIS_STALE_PLAYED_HOURS`)
since its last refresh. Off until the host configures a provider.
[Server-side analysis](self-host/server-analysis.md).
