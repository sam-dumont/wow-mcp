# Try the demo

**For:** anyone trying the demo at home. **At the end:** you can open Nibbet's page and the
two sites for friends, or ask Claude Code about a dungeon run. Allow about 10 minutes.

Start [Docker](https://docs.docker.com/get-docker/) and extract the
[release v{{app_version}} kit](https://github.com/sam-dumont/wow-mcp/archive/refs/tags/v{{app_version}}.zip).
The public GHCR image works without a Docker Hub login, a game install or a Battle.net account.

## 1. Start the page

On Mac, Linux or Git Bash for Windows, run [Quick start, step 1](../quickstart.md#1-start-the-demo),
then skip to [Look around](#3-look-around). For PowerShell, run these commands in the extracted folder.

```powershell
cd deploy/compose
$files = if (Test-Path compose.demo.yaml) { 'compose.yaml,compose.demo.yaml' } else { 'compose.yaml' }
@(
  'COMPOSE_PROJECT_NAME=wow-stats-demo'
  'COMPOSE_PATH_SEPARATOR=,'
  "COMPOSE_FILE=$files"
  'INGEST_PASSWORD=demo-password-only'
  'WOW_STATS_IMAGE={{release_image}}'
) | Set-Content -Encoding ascii .env.demo
docker compose --env-file .env.demo pull ui
docker compose --env-file .env.demo up -d --no-build --wait ui
```

The first run downloads the released image. The demo has its own Compose project and volumes.

## 2. Load the characters

```sh
docker compose --env-file .env.demo run --rm demo
```

The seed sends Nibbet's evenings to the local page. His last session ends yesterday.
The v0.13.0 kit ships Nibbet alone. Kits with `compose.demo.yaml` add eight characters at levels 10 through 60. Each has its own sessions, gear, talent picks and simulated armory reads. The first seed downloads the roster’s 189 MB client
artwork archive, checks its SHA-256 and keeps it in the demo volume for later runs.

## 3. Look around

Open <http://127.0.0.1:8080/>: **Nibbet**, a Gnome priest who goes from level 22 to 46 over
four weeks of evenings, with eleven dungeon runs, gear, talents and professions, Questie's
quest list and two days of auction house scans, under a DEMO DATA banner.

![The Overview of the demo character Nibbet, a level 46 Gnome priest under the DEMO DATA banner: on track with Polestar's route time, the next level's play time, the guide steps done in order, the rested XP, and a waist upgrade in the bags as the first next move](../images/local-demo.webp)

[Your page at a glance](../players/your-page.md) walks through each tab.

In the nine-character demo, choose **Hesta** (53) or **Brakka** (60) and open **View in 3D**.
Pick the Warhorse or Swift Timber Wolf under **Mount**, then try **Walk** or **Run**.
Choose **On foot** to dismount. Flintwick has a bear, and Duskwick has a Voidwalker.
The hosted demo lists all nine characters. Open **Back in time** to see what each one wore as they levelled. [Demo roster](../../site/pages/demo.md)

If port 8080 is taken, add `UI_PUBLISH=127.0.0.1:18080` to `.env.demo`, rerun the
`up` command and open <http://127.0.0.1:18080/>.

## 4. See the sites for friends

The full view on 8080 is yours. Friends get static copies, rebuilt after each play session:
a public one and a sealed one. Start the two services that build and serve them:

```sh
docker compose --env-file .env.demo up -d --no-build publish site
```

Give it a minute (watch `docker compose --env-file .env.demo logs -f publish` until it says it built), then open
<http://127.0.0.1:8081/>: the public site, with only the parts anyone may see. The sealed site
needs its key in the link; print it with:

```sh
docker compose --env-file .env.demo run --rm publish zogzog-key link
```

It prints a link like `http://127.0.0.1:8081/zogzog/#<43 characters>`. Open it: the same page
with the journal, gear advice and prices. Open it without the part after `#`, and it says
"Nothing here." [Share your page](sharing.md) explains the two sites.

## 5. Ask your assistant (Claude Code)

With [Claude Code](https://docs.anthropic.com/en/docs/claude-code) on this computer, still in
`deploy/compose`:

```sh
claude mcp add wow-stats -- docker compose --env-file "$PWD/.env.demo" -f "$PWD/compose.yaml" exec -T ui wow-stats mcp-stdio
```

Then in Claude Code: "With wow-stats, which level took Nibbet-Gearholt longest, and how long
was Nibbet's Blackfathom Deeps run?" It should come back with level 32, about 9 hours, and 83
minutes in Blackfathom Deeps. Other assistants: [Connect your AI](../ai/connect.md).

## 6. Clean up

```sh
docker compose --env-file .env.demo --profile demo down -v
```

That removes the containers and the demo data. `claude mcp remove wow-stats` removes the
Claude Code entry.

## Run the nine-character demo from a development checkout

Use a checkout that contains `deploy/compose/compose.demo.yaml`. The build pulls its base
from `dhi.io`, which requires a free Docker Hub account. From that checkout's
`deploy/compose` folder, create `.env.demo` with these four lines:

```dotenv
COMPOSE_PROJECT_NAME=wow-stats-demo
COMPOSE_PATH_SEPARATOR=,
COMPOSE_FILE=compose.yaml,compose.demo.yaml
INGEST_PASSWORD=demo-password-only
```

```sh
docker login dhi.io
docker compose --env-file .env.demo build ui
docker compose --env-file .env.demo up -d --no-build --wait ui
docker compose --env-file .env.demo run --rm demo
```

Open <http://127.0.0.1:8080/> and pick a character. The seed loads the client artwork and
writes the armory samples locally. The Battle.net exporter stays off. Steps 4 to 6 also
work with this checkout.

To use your own Forever install, run this from the repository root before starting Compose:

```sh
uv run python tools/fixtures/demo3d.py build-roster .demo3d --wow-root "/Applications/World of Warcraft"
```

Set `DEMO_3D_STORE` in `.env.demo` to the absolute path of `.demo3d/store`. Compose mounts
it read-only. The UI, seed and publisher use those same files.

## Next

- [Install it for real](../install.md): the server, the addons and the program on the
  computer you play on.
- [The host guide](production.md): a homelab box with HTTPS, a login, and friends.
