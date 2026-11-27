# The live demo

Nibbet is a made-up Gnome priest. The journal records 34 sessions from level 22 to 46, including 11 runs across eight dungeons. The same publisher that builds a player's dashboard turns that history into two demo pages.

<div class="demo-links">
<a href="@BASE@demo/">Open the public page</a>
<a href="@BASE@demo/friends/">Open the friends' link</a>
</div>

## The nine characters

Pick a character in the selector. Each wears gear they can use and has one talent point per level from 10 on. Sleetpin’s journal starts at level 1 in Durotar. Brakka’s starts at 55 in the Western Plaguelands and carries on at 60, including runs in Scholomance and Blackrock Depths. The climb shows time played over the recorded levels. When Altoholic has a lifetime `/played`, you see that alongside it. [Blizzard’s Forever race and class table](https://worldofwarcraft.blizzard.com/en-us/news/24304075/)

| Level | Character | Race and spec |
| ---: | --- | --- |
| 10 | Sleetpin | Troll Frost mage |
| 16 | Valeleaf | Skyborne Combat rogue |
| 23 | Duskwick | Undead Affliction warlock, with a Voidwalker |
| 29 | Flintstep | Tauren Enhancement shaman |
| 34 | Flintwick | Dwarf Beast Mastery hunter, with a bear |
| 39 | Mossbranch | Night Elf Feral druid |
| 46 | Nibbet | Gnome Shadow priest |
| 53 | Hesta | Human Retribution paladin, with a Warhorse |
| 60 | Brakka | Orc Arms warrior, with a Swift Timber Wolf |

Open **Character** and choose **View in 3D**. Leave Nibbet on foot for a bit and he may cast a spell or shoot his wand. Choose **On mount** to see Hesta ride her Warhorse or Brakka ride her epic wolf. Choose **On foot** to dismount. Flintwick’s bear and Duskwick’s Voidwalker appear alongside them.

The models, gear, companions, mounts and spell effects come from the installed Forever client. The item and talent icons come from the same Wowhead cache as Nibbet’s. Both pages serve their own copies. [Artwork and credits](../../THIRD_PARTY.md)

## The two pages

**The public page** is what anyone with the address sees: the card, the gear, the climb level by level, the dungeons cleared and the gear at each past level. [Privacy tiers](../../docs/explanation/privacy.md) lists what stays out.

**The friends' link** is the one you hand to friends. It adds the Overview (what to do next, with the quests to pick up and the drops worth having), the gear advice with what's on the auction house today, the journal, the timeline and the guide steps. Its files are encrypted: the key sits in the link after the `#`, which a browser never sends to a server. This demo's key is in its link on purpose.

The Battle.net side is made up too: a profile with a guild, the look, the talents read once a day (a Discipline start, then a respec into Shadow at 30) and one Legacy point. Nibbet's player has WoWSims Exporter, so the talent changes go back to level 22, not only 3 weeks.

Open **View in 3D** on the character's gear panel to turn the model around. Nibbet wears Blizzard's models and textures from the Forever client. His play history is invented. [Artwork and credits](../../THIRD_PARTY.md).

## What the demo can't do

The pages are plain files on GitHub Pages, so anything that needs the server is off:

- no login, no full view (other players, gold over time, the live timeline);
- no MCP: [the 0.13.0 recording](mcp-session.md) shows six calls for Nibbet, using the single-character demo.

The dates move: every build shifts the play so the last session is the day before. The auction house only lists what was on sale today or yesterday, so a demo frozen in October would have an empty auction house.

## Get this for your character

- A friend runs wow-stats for you: [Join a friend's server](../../docs/players/getting-started.md), about 5 minutes.
- Your AI on the same data: [Connect your AI](../../docs/ai/connect.md).
- Run it yourself: [the host guide](../../docs/self-host/production.md).

## Build it yourself

Each release rebuilds the demo from the same script. [Build the demo locally](../../docs/development.md#the-docs-site-and-its-demo).
