# Battle.net data

**For:** self-hosters who want the WoW Token price, a small auction house watch list and their
characters' look from the armory. **At the end:** the exporter runs with your own Battle.net
API client, and you know what its data may and may not be used for.

Optional. Everything else works without it: the game's own files give the climb, the gear,
the journal and your own Auctionator prices. The exporter adds:

- the WoW Token price, the Forever ruleset's status and a short auction house watch list, in
  your full view and on the sealed friends' site;
- your characters' look, level, gear and talents from the Profile API once a day, for the 3D
  view and the character card (this part may show on the public site too).

## Blizzard's rules, and what the kit does about them

Blizzard's [Developer API Terms](https://www.blizzard.com/en-us/legal/a2989b50-5f16-43b1-abec-2ae17cc09dd6/blizzard-developer-api-terms-of-use)
apply to everything the API returns:

| Rule | What the kit does |
|---|---|
| Your client secret stays yours (§2.12) | Each player makes their own client. Never use someone else's |
| The data is for your own use (§2.9) | Market data (token, auctions, realm status) stays in your full view and the sealed site, never the public one. Your character's look is the one piece the public site shows |
| Data from the API is kept 30 days at most (§2.18) | It lives in its own VictoriaMetrics, which keeps 23 days and is force-merged daily, so expired data is really gone before day 30. Never in VictoriaLogs, never in backups. The static sites rebuild at least daily, so no copy there outlives it either |
| | It reaches no AI by default: the MCP's `market` tool is off unless a person turns on "Battle.net data to my AI" on a shared install |

Your game files (SavedVariables, the combat log, Auctionator scans) aren't API data: they're
yours and stay until you delete them.

## 1. Make your API client

1. Log in at [develop.battle.net/access/clients](https://develop.battle.net/access/clients)
   with your Battle.net account and click **Create Client**.
2. Any name ("wow-stats"), no redirect URL, tick that you have no service URL, intended use
   "personal stats page". Save.
3. Copy the **Client ID** and **Client Secret** into two files in `deploy/compose/secrets/`,
   one value each:

   ```sh
   printf '%s' 'your client id' > secrets/bnet_client_id
   printf '%s' 'your client secret' > secrets/bnet_client_secret
   ```

   On Windows with Notepad, pick "All files (\*.\*)" when saving, or it adds `.txt`.

## 2. Configure and start the exporter

From `deploy/compose`:

1. One more secret, any random string (VictoriaMetrics' force-merge key):

   ```sh
   head -c 24 /dev/urandom | base64 > secrets/vm_merge_key
   chmod 644 secrets/*
   ```

   On Windows, put any 30 random letters in `secrets\vm_merge_key`.
2. In `.env`: uncomment `VM_URL=http://victoriametrics:8428`, set `BNET_REGION` (`eu` or
   `us`), `AH_FACTION`, and your characters as their WTF folders name them:
   `PROFILE_CHARACTERS=Testchar-Demo,Testalt-Demo`. `WATCH_ITEMS` holds the watch list.
3. Start it:

   ```sh
   docker compose --profile exporter up -d
   ```

## 3. Check it

```sh
docker compose --profile exporter logs exporter | tail -5
docker compose --profile exporter exec exporter python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:9464/metrics').read().decode())" | grep wow_api_up
```

`wow_api_up 1` means the client works. A character the Profile API doesn't know yet (new or
low level) answers 404: it shows up by itself later, and until then the 3D view uses a
[Narcissus](../reference/addons.md) look or the race's default.

The exporter's `/metrics` holds its own health only, never Blizzard's data: don't point a
long-term scraper (Prometheus, vmagent) at it expecting game data, and never keep the
exporter's VictoriaMetrics longer than its 23 days.

Every exporter setting: [Environment variables, the Battle.net exporter](../reference/environment.md#the-battlenet-exporter-wow-stats-exporter).
