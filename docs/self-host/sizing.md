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

**Since DuckDB and the semantic layer** (the next release after 0.6.1), the analysis holds a
second copy of a character's tables in DuckDB, and the MCP loads DuckDB, ibis and sqlglot the
first time an analysis tool runs. Measured on 2026-11-20, same demo, same calls (the analysis
brief, a `play_sql` count, plus two `query_metric` calls after), amd64 image under emulation:

| Service | Before | After |
|---|---|---|
| `ui` (with the MCP) | 228 MiB | 354 MiB |
| `publish` | 146 MiB | 145 MiB |

The move from Debian slim to Docker's hardened Python base (2026-11-20, amd64 under emulation,
the same calls through `wow-stats mcp-stdio` run inside the `ui` container, so its peak
includes that process): `ui` 485 to 433 MiB, `publish` 160 to 146 MiB. That method counts more than the table
above, so compare the two rows with each other, not with the table.

About 75 MiB of the `ui` difference is loading the libraries (DuckDB 53, ibis and the layer
22); the rest is the copy and the queries.

**Since the pages read the layer** (same release), `publish` loads DuckDB and the layer too: the
character page's numbers come out of it at build time. Measured on 2026-11-20 on the demo, one
process each with the events answered in memory (no VictoriaLogs, no Docker), peak resident
memory on macOS:

| Run | Before | After |
|---|---|---|
| `publish`, both sites, forced | 248 MiB | 314 MiB |
| `ui`, one background build of every answer | 224 MiB | 292 MiB |

About 47 MiB of it is the imports (DuckDB, ibis, the layer), the rest the narrow copy and the
queries. A process measures more on macOS than in the Linux container (the table above has
`publish` at 145 MiB there), so compare the two columns, not the rows across tables. The long
history wasn't re-run: `publish` measured 342 MiB there under a 384 MiB cap before this, so
give it 512 MiB if you set a limit. The long history
wasn't re-run: give `ui` 1 GiB (the compose default) and check `docker stats` after the first
`analysis_brief`.

The compose kit caps `ui` at 1 GiB (`UI_MEM_LIMIT`), about twice its measured peak; the others
run without a limit. A history much longer than a year, or item cards on with a long history:
raise `UI_MEM_LIMIT` and watch `docker stats`.

## Disk

- **Events**: 6.5 MiB for 100,705 events. They're kept 100 years; `VL_MAX_DISK` sets a
  ceiling ([Install, disk space](../install.md#disk-space)).
- **Image**: the main image is about 291 MB (amd64, on Docker's hardened Python base since
  2026-11-20). DuckDB's library is 65 MB of it, ibis 28 MB. It was 173 MB on Alpine before
  DuckDB, then 340 MB on Debian slim. The optional renderer stays on Debian slim, about
  850 MB, and no longer shares base layers with the main image.
- **Downloads cache** (`ui-state`, with items on): game tables and icons.
- **3D files** (`ui-state`, with 3D on): what each person's shipper sends, up to 512 MiB a
  person (`MODEL3D_ASSETS_MB`).
- **The shipper's archive**, on the player's computer: grows with the `WTF` folder. 8 MiB of
  unrelated addon files once made about 16 MiB of archive. `wtf_archive = false` keeps the
  stats only ([The shipper](../shipper.md#stats-only-no-wtf-archive)).

## Not measured

- A Raspberry Pi or another small ARM board (the images are built for `linux/arm64`, but no
  run on one was timed).
- Several people on one install, at once.
- The 3D view on, and the renderer.
- A fresh Windows machine running Docker Desktop.
