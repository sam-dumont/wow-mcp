# Sizing

**For:** self-hosters picking a machine. **At the end:** you know what wow-stats used in
measured runs, and what to give it.

**Start with 2 vCPU and 2 GiB of RAM** for one player, a Linux VM or a small box. The
numbers below are why. They come from the release candidate's staging tests on 2026-11-19,
with the small preset (`MODEL3D=off`, `WOW_ITEMS=off`) unless a row says otherwise, on an
Apple silicon Mac running Docker Desktop, each service under a memory cap.

## Two histories

| | Demo | Long history |
|---|---|---|
| Data | The demo's week: 1,831 events, 5 characters | The demo's week copied 55 times, each copy a week earlier: 100,705 events |
| Events on disk | | 6.5 MiB, right after a flush |
| Ingest | | 100,705 events in 55 posts, 2.6 s |

A real week of play is in the same range as the demo's week; the long history stands for
about a year of play.

## Time

| Run | Demo | Long history |
|---|---|---|
| Build both static sites, from cold | 0.8 s; 14.4 s with item cards on (downloads included) | 294 s |
| The character page's API, first call | 0.031 s | 1.43 s |
| The same, answered from cache | 0.001 s | |
| MCP `analysis_brief`, first call | 0.44 s | 15.9 s |
| MCP `play_sql`, one count | 0.003 s | |

The long-history rows ran while the publisher built the sites on the same two cores, the
slowest moment.

## Memory

Peak use per service (`memory.peak`), and the limit it ran under:

| Service | Demo | Long history | Limit in the test |
|---|---|---|---|
| `ui` (with the MCP) | 187 MiB | 472 MiB | 512 MiB |
| `publish` | 221 MiB (335 MiB with item cards on) | 342 MiB | 384 MiB |
| `victorialogs` | 19 MiB | 217 MiB | 256 MiB |
| `site` | 22 MiB | 22 MiB | 64 MiB |

After the long-history build, the four together sat at 445 MiB.

The compose kit caps `ui` at 1 GiB (`UI_MEM_LIMIT`), about twice its measured peak; the others
run without a limit. A history much longer than a year, or item cards on with a long history:
raise `UI_MEM_LIMIT` and watch `docker stats`.

## Disk

- **Events**: 6.5 MiB for 100,705 events. They're kept 100 years; `VL_MAX_DISK` sets a
  ceiling ([Install, disk space](../install.md#disk-space)).
- **Image**: the main image is about 190 MB; the optional renderer about 850 MB.
- **Downloads cache** (`ui-state`, with items or 3D on): game tables, icons and model files,
  up to 512 MiB for the models (`MODEL3D_CACHE_MB`).
- **The shipper's archive**, on the player's computer: grows with the `WTF` folder. 8 MiB of
  unrelated addon files once made about 16 MiB of archive. `wtf_archive = false` keeps the
  stats only ([The shipper](../shipper.md#stats-only-no-wtf-archive)).

## Not measured

- A Raspberry Pi or another small ARM board (the images are built for `linux/arm64`, but no
  run on one was timed).
- Several people on one install, at once.
- The 3D view on, and the renderer.
- A fresh Windows machine running Docker Desktop.
