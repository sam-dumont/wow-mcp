# Monitor it

**For:** self-hosters who want to know it's working without opening the page. **At the
end:** you can check each service's health, read its logs and see the disk it uses.

## Health checks

From `deploy/compose`:

```sh
docker compose ps
```

Every service should say `running`. Each server answers `GET /healthz` with `200`, with no
login, so an uptime checker (Uptime Kuma, Gatus, your router's) can poll it:

| Service | Health check |
|---|---|
| The full view (`ui`) | `http://127.0.0.1:8080/healthz`, or `https://wow-full.example.org/healthz` behind the proxy |
| The static sites (`site`) | `http://127.0.0.1:8081/healthz` |
| The exporter | `http://127.0.0.1:9464/healthz` inside its container |
| The renderer | `/healthz` on its port: `200` once Chromium runs |

```sh
curl -s http://127.0.0.1:8080/healthz
```

prints `{"status": "ok", "semantic": "ok", "store": "ok", ...}` on the full view, `ok` on the others.

`semantic` is the one to alert on. Every number on the page comes from one place, the semantic
layer, with no second way to count them. When it fails for a character, the page keeps showing
the last good answers, marked out of date, `semantic` reads `"failing"` (with
`semantic_failing_chars` and `semantic_failed_at`, never a name), and the `ui` log gets one JSON
line per failure: `"event": "semantic_failed"` with the character and the error. The status
stays `200`: the process still serves, so a restart wouldn't help. In Gatus, for example:
`[BODY].semantic != failing`.

`store` is the other one. It reads `"failing"` (with `store_failed_at`) while the event store
doesn't answer (VictoriaLogs, or the SQLite files): uploads get a `502` and the page keeps its
last answers, marked out of date. The `ui` checks once a minute, so it turns within a minute
of the store going down or coming back. Same `200`, for the same reason: restarting `ui`
doesn't bring VictoriaLogs back.
In Gatus: `[BODY].store != failing`.

## Data arriving

The page itself: the newest session in the Journal tab. Or ask VictoriaLogs for the newest
event, through the `ui` container (VictoriaLogs has no published port):

```sh
docker compose exec ui python -c "import urllib.request;print(urllib.request.urlopen('http://victorialogs:9428/select/logsql/query?query=*%20%7C%20sort%20by%20(_time)%20desc%20%7C%20limit%201').read().decode())"
```

It prints the newest event as one line of JSON, with its `_time`. On the SQLite store there's
no VictoriaLogs to ask: the Journal tab, or the page's query box with
`* | sort by (_time) desc | limit 1`: it answers with the newest event, like VictoriaLogs.

The shipper logs each run on the player's computer: `~/Library/Logs/wow-stats.log` (Mac),
`%LOCALAPPDATA%\wow-stats\wow-stats.log` (Windows). A good run ends in
`ship: ... sent (N events)`.

## Logs

```sh
docker compose logs --tail 50 ui
docker compose logs --tail 50 publish
```

`ui` writes one line per request (method, path, status) and never prices or secrets.
`publish` writes one line per build with its time and file counts; a failed build says why and
leaves the previous sites in place. On a shared install, friends list changes are lines with
`"event": "friends"`.

## Disk and memory

```sh
docker system df -v
docker stats --no-stream
```

The first lists each volume's size (`wow-stats_vl-data` holds the events); the second each
container's memory against its limit. What's normal: [Sizing](sizing.md).

## The exporter

The exporter's `/metrics` gives its own health: `wow_api_up` (1 when Blizzard's API answers),
`wow_api_paused` and `wow_exporter_*`. Scrape that for alerts if you like. It never holds
Blizzard's data, so it's safe to keep.
