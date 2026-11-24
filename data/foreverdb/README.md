# ForeverDB.net API data

`api.json` lands here at release time and is never committed: the release
workflow's `foreverdb` job fetches it with the project's API key
(`tools/foreverdb_api/export.py`) and the image bundles it, as
[ForeverDB.net](https://foreverdb.net/developers) asks ("fetch the data
yourself and bundle it with your releases"). Nothing at runtime needs the key.

Without the file (a checkout, a fork's build, a release whose fetch failed)
the gear advice works as before on Polestar's and forever-db's sources: no
vendors, no quest givers.

How it is made and refreshed: [docs/gear-reference.md](../../docs/gear-reference.md),
"Vendors and quest givers: the ForeverDB.net API".
