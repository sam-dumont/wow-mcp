# ForeverDB.net's addon data

Data from ForeverDB.net (https://foreverdb.net)

Where each piece of gear comes from, taken from the data files of the
ForeverDB addon. ForeverDB.net publishes them under GPL-3.0 with one
added term (section 7(b)): keep its notices, and show the line above, with
the link, wherever the data's credits are. We do: here, in
[THIRD_PARTY.md](../../THIRD_PARTY.md), in the README's credits, and as
"Source: ForeverDB.net" next to every source the page or the MCP shows.

| File | What |
|---|---|
| `item-sources.json` | Every gear item's sources (quest, vendor, boss and creature drops, rares, crafts, reputation, PvP rank, world drops, containers), each labelled with how ForeverDB knows it: `s` seen in Forever, `f` the Forever client, `k` Classic data (CMaNGOS classic-db), `c` a clue. Plus the vendors' and rares' places and the instances. `_notice` comes first: the data files' notice line and the attribution. |
| `LICENSE.txt`, `NOTICE.txt`, `COPYING.txt` | The addon's own, byte for byte. NOTICE.txt says which data is ForeverDB's, which is Blizzard's and which is CMaNGOS's. |

The stat weights (`data/foreverdb-weights.json`) come from the same
download, from `Data/GearFinder.lua`.

## How it's made and refreshed

`tools/foreverdb_sources/export.py` reads ForeverDB's manifest
(https://foreverdb.net/downloads/addon-manifest.json) and downloads only
`Data/GearSources.lua`, `Data/Loot.lua`, `Data/GearFinder.lua` and
`Data/Meta.lua`, plus the addon zip for the licence files, each checked
against the manifest's SHA-256. The Lua is never run: a strict reader
(`tools/foreverdb_sources/lua.py`) takes literals only and refuses the file
on anything else.

The daily KB watch (`tools/kb/watch.py --kinds versions`, every day since
2026-11-25) compares a hash of the four files' SHA-256s with the one in
`data/kb/watch.json`: ForeverDB moves its data version every few hours, a
change to a file we don't read is no change. When one moved,
`tools/kb/bump.py foreverdb` downloads them, refuses them if a file's format
comments or a licence file changed, reruns both exports (the
`foreverdb-sources` and `foreverdb-weights` entries of
`tools/kb/rebuild.py`), and the gates in `tools/kb/gates.py` check it
before it merges. The merge releases a patch version the same day
(`kb-release.yml`). Releases ship what is committed here and download
nothing; a running server says which data version it has in `/api/config`
and the MCP's `character_summary` (`data_versions.foreverdb`). Details:
`docs/gear-reference.md`, `docs/kb.md` ("The daily rebuild").

ForeverDB.net stays optional for players: this is the site's published
data, bundled with the server; nobody needs its addon installed for it.
