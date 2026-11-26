# Try the demo

**For:** anyone curious, before installing anything for real. **At the end:** wow-stats runs on
your computer with nine made-up characters, you've seen the page, the two sites for friends,
and (if you use Claude Code) your assistant answering from the demo data. About 10 minutes.

You need [Docker](https://docs.docker.com/get-docker/) (Docker Desktop on Mac and Windows,
started once so it says "Engine running") and a copy of this repository: `git clone` it, or
download a release's source zip and unpack it. No game, no Battle.net account, no Docker Hub
login.

## 1. Start the page

From the top of your copy (the folder with `README.md` in it):

```sh
cd deploy/compose
cp .env.example .env
```

Set `INGEST_PASSWORD` in `.env` to a password of at least 12 characters. The demo uses its own Compose project and volumes. Build from this checkout so the page and the fixtures use the same version.

```sh
docker compose -f compose.yaml -f compose.demo.yaml up -d --build ui
```

## 2. Load the nine characters

```sh
docker compose -f compose.yaml -f compose.demo.yaml run --rm demo
```

The seed loads Nibbet's evenings and eight shorter sessions, each with a different race and class. It generates the character and companion models locally. Talent ranks and profiles come from simulated armory samples in the demo's VictoriaMetrics container. It needs no Battle.net account or API key. Nibbet's last session ends yesterday.

## 3. Look around

Open <http://127.0.0.1:8080/>: **Nibbet**, a Gnome priest who goes from level 22 to 46 over
four weeks of evenings, with eleven dungeon runs, gear, talents and professions, Questie's
quest list and two days of auction house scans, under a DEMO DATA banner.

![The Overview of the demo character Nibbet, a level 46 Gnome priest under the DEMO DATA banner: on track with Polestar's route time, the next level's play time, the guide steps done in order, the rested XP, and a waist upgrade in the bags as the first next move](../images/local-demo.webp)

[Your page at a glance](../players/your-page.md) walks through each tab.

Use the character selector for the other eight. The Dwarf hunter and Undead warlock have companions in the 3D view. The local demo uses block models for everyone. The hosted demo can also draw Nibbet from its pinned archive and has a renderer for still pictures at each level.

The shorter command `docker compose run --rm demo` from the base kit loads the same nine histories into that kit. The extra Compose file above gives the demo its own storage, models and armory samples.

Port 8080 already taken on your computer? `docker compose up` says `port is already
allocated`. Set `UI_PUBLISH=127.0.0.1:18080` in `.env`, run step 1's command again, and open
<http://127.0.0.1:18080/>.

## 4. See the sites for friends

The full view on 8080 is yours. Friends get static copies, rebuilt after each play session:
a public one and a sealed one. Start the two services that build and serve them:

```sh
docker compose -f compose.yaml -f compose.demo.yaml up -d publish site
```

Give it a minute (watch `docker compose -f compose.yaml -f compose.demo.yaml logs -f publish` until it says it built), then open
<http://127.0.0.1:8081/>: the public site, with only the parts anyone may see. The sealed site
needs its key in the link; print it with:

```sh
docker compose -f compose.yaml -f compose.demo.yaml run --rm publish zogzog-key link
```

It prints a link like `http://127.0.0.1:8081/zogzog/#<43 characters>`. Open it: the same page
with the journal, gear advice and prices. Open it without the part after `#`, and it says
"Nothing here." [Share your page](sharing.md) explains the two sites.

## 5. Ask your assistant (Claude Code)

With [Claude Code](https://docs.anthropic.com/en/docs/claude-code) on this computer, still in
`deploy/compose`:

```sh
claude mcp add wow-stats -- docker compose -f "$PWD/compose.yaml" -f "$PWD/compose.demo.yaml" exec -T ui wow-stats mcp-stdio
```

Then in Claude Code: "With wow-stats, which level took Nibbet-Gearholt longest, and how long
was Nibbet's Blackfathom Deeps run?" It should come back with level 32, about 9 hours, and 83
minutes in Blackfathom Deeps. Other assistants: [Connect your AI](../ai/connect.md).

## 6. Clean up

```sh
docker compose -f compose.yaml -f compose.demo.yaml down -v
```

That removes the containers and the demo data. `claude mcp remove wow-stats` removes the
Claude Code entry.

## Next

- [Install it for real](../install.md): the server, the addons and the program on the
  computer you play on.
- [The host guide](production.md): a homelab box with HTTPS, a login, and friends.
