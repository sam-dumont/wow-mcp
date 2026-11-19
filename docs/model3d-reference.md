# Model reference (Forever's armor in 3D)

**For:** contributors. **At the end:** you know why the 3D view ships its own armor data, where it comes from and how to remake it.

`data/model3d-reference.json.gz` ships in the image as `wowstats/data/`. It
says how Forever dresses each armor display its items wear, in the terms of
Forever's client tables. `wowstats/model3d.py` reads it (`ArmorReference`,
`armor_meta`).

## Why it exists

Wowhead's viewer dresses a character from one small file per display,
`meta/armor/<inventory type>/<display id>.json`. Forever added armor of its
own, and Wowhead has no such file for it: Violet Sash (display 714142),
Graveweave Bindings (715413) and Ephemeral Grips (737496) answered 404, and
the model was drawn without them.

Those displays reuse the game's older textures and models, which Wowhead
does serve. The file the viewer wants is only Forever's tables put together:
each body section's material, the material's texture files and who they are
for, the models of a helmet or shoulders, what a helmet hides per race, and
the geoset groups. So when Wowhead answers 404, the server writes that file
from this reference. Wowhead's own file always comes first when it exists.

When a texture the reference names isn't on Wowhead's host either, the item
is listed under the model ("Wowhead has no texture for it") and the page
shows its icon instead.

Held items (weapons, shields, off-hands) work differently: their model is a
whole file, so a missing display borrows the meta of another display of the
same model with Forever's texture swapped in (`Model3D.item_meta`).

## Sources

| Part | From | Licence |
| --- | --- | --- |
| `displays`: per armor display, its geoset groups, models and their materials, helmet geoset visibility, each body section's material, and the class and subclass of the first item wearing it | Forever's client tables Item, ItemModifiedAppearance, ItemAppearance, ItemDisplayInfo and ItemDisplayInfoMaterialRes, on [wago.tools](https://wago.tools) (product `wowf`, build `1.60.1.70959`) | Game data © Blizzard Entertainment, used as facts |
| `textures`: per material, its texture files with gender, class and race | TextureFileData, ComponentTextureFileData (same build) | same |
| `models`: per model, its files with gender, class, race and side | ModelFileData, ComponentModelFileData (same build) | same |
| `helmet`: per helmet visibility set, the geoset group hidden for each race | HelmetGeosetData (same build) | same |

Only armor displays are kept (the inventory types drawn from `meta/armor/`),
and only the columns above: 6,068 displays, about 140 KB gzipped. The file
is fetched once, by the tool below, from wago.tools' documented CSV export
(`/db2/<table>/csv?build=<build>`). Nothing is fetched for it at runtime.

## Remaking it

    uv run python tools/model3d_reference/export.py [--build <Forever build>]

The same bytes on every run for one build. When Forever ships a new client
build, run it with the new `--build`, bump `BUILD` in the tool and this
page, and check one new item in the 3D view.
