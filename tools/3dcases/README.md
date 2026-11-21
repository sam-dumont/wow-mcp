# 3D test cases

Real characters, Blizzard's own render as the reference, our assembly and
viewer drawing the same look, and a grade per case: pass, warn or fail, with
the reason ("cloak absent", "weapon in the wrong hand", "head: wrong colour").
It answers one question: where does character assembly work, and where not.

Everything runs on your machine. The game files come from your own install,
read-only (the shipper's CASC reader). The look comes from Battle.net's
public profile API. Nothing is fetched from any other site.

## What lives where

| What | Where | Why |
|---|---|---|
| The case specs | `tools/3dcases/cases.json` (git) | race, body type, class, customisation ids, worn items and enchants, the cells the case fills. No name, realm, guild or picture. |
| Which character a case is | `<store>/sources.json` | names stay on your disk |
| Battle.net's answers and renders | `<store>/fetch/<case>/<YYYY-MM-DD>/` | Blizzard's API terms: 30 days at most |
| The runs (contact sheets, report) | `<store>/runs/<date>/` | they hold Blizzard's renders too |

`<store>` is `--store`, else `$WOW_3D_CASES`, else `~/wow-3d-cases`. A fetch
older than 30 days is refused (the run says to `refresh`), and `prune` deletes
every fetch and run past 30 days.

## Run it

You need a Battle.net API client (the same one the exporter uses,
[Battle.net data](../../docs/self-host/battlenet.md)): `BNET_CLIENT_ID_FILE` and `BNET_CLIENT_SECRET_FILE`,
or `BNET_CREDS_CMD`, a command that prints `{"client_id", "client_secret"}`
as JSON (for example `sops -d --output-type json <file>`). The credentials
are read into memory, never printed or written.

```sh
uv run python tools/3dcases/run.py add <name-surname>      # one of your characters
uv run python tools/3dcases/run.py discover --guild <slug> --guild <slug> --exclude-guild <yours>
uv run python tools/3dcases/run.py refresh                 # dated fetch, writes cases.json
uv run --group browser python tools/3dcases/run.py run     # draw, compare, grade
uv run python tools/3dcases/run.py coverage
```

`--wow-root` (or `$WOW_ROOT`) is the folder holding `.build.info`; the Mac
and Windows defaults are built in. `run` is a heavy job (Chromium): one at a
time. 21 cases take about 4 minutes.

`discover` reads public guild rosters (slugs you give it, never written to
git) and picks characters level 25+ that fill a coverage cell nobody fills
yet: every race in both body types, each armour type, cloak, tabard, helm,
shield, two-hander, dual wield, a ranged weapon, a weapon glow, facial hair,
piercings, tattoos. It skips every name and surname in the public-scan
denylist (`~/.config/wow-stats-public/denylist.txt`) and in `--exclude`.
Never point it at a guild of friends or relatives: their characters don't
belong in a test suite. At most `--budget` requests (400), four a second.

## What a run does

For each case and each model Blizzard renders (`main-raw`, the Updated
model, and `main-sd-raw`, the Classic one):

1. Assemble the recipe from the fetched look (`wowstats.character3d`).
   Items Battle.net names as the game's cosmetic "Hidden Helm"-style items
   are hidden slots.
2. Read every file it names from the install. A case whose model or item
   models aren't there is **skipped: not on this disk**, with the list.
3. Draw it in headless Chromium with our viewer, on the Stand animation's
   first frame, turned to the angle (around 9 degrees off facing) whose
   silhouette matches the reference best, scaled to the reference's body
   height. Draw it once more without what the hands hold: the difference is
   our weapons' mask.
4. Compare: the silhouette's IoU after aligning feet and torso; per region
   (head, torso, legs, feet, hands, weapon) the IoU, the mean colour and a
   4x4x4 colour histogram over the pixels both cover, ours brightness-matched
   first; which side the weapons are on.
5. Check the recipe: every worn helm, cloak and weapon placed, every
   non-default customisation changing the model.
6. Write the contact sheet: reference | ours | diff (red: only theirs,
   blue: only ours, orange: colour differs).

A reference that isn't the character is skipped, not graded. Blizzard
renders a character at login (Last-Modified within seconds of
`last_login_timestamp`); a render written long after the last login is
someone else's on the same path, and a Classic render whose body height is
far from the Updated one's is stale. Three of the first 21 cases had one.
Not every foreign render is caught that way (one was older than the
character's last login and still showed a troll for a human): when the
contact sheet shows someone else, `run.py mark <case> "<what you saw>"`
skips it from then on, and `discover` stops counting its cells.

## Reading the grade

Thresholds are in `checks.py`, set on the first run. The two renderers light
differently and frame a little differently, so a correct assembly reads a
silhouette IoU of about 0.82-0.92 and colour distances of 20-40 per region.
A fail is worth a look at the contact sheet; a warn usually is lighting.

The offline tests (`tests/test_3dcases.py`) use synthetic pictures and specs
only.
