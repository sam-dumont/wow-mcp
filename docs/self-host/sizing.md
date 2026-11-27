# Sizing

**For:** self-hosters picking a machine. **At the end:** you know what fits when the whole host has two CPUs and 2 GiB of RAM.

Compose caps the UI and its MCP at 1 GiB. VictoriaLogs gets another 1 GiB. The publisher
and static server run alongside them without caps. A shared VictoriaLogs install adds a second
log server for friends. These are service limits. The host also needs memory for the OS,
Docker and anything else it runs. [Where your events live](../install.md#where-your-events-live).

## Nine characters on a 2 GiB host

**Neither profile finished a nine-character publish while the UI, cold MCP analysis and
ingest were active on 2 GiB.** The Linux kernel killed Chromium in the default setup.
SQLite could answer the MCP and take the upload, but its publisher was still running
when its 1,100-second wait expired. Neither guest ran out of disk.

| Whole guest, one run each | VictoriaLogs, items and renderer | SQLite, items and 3D off |
|---|---:|---:|
| RAM used with services ready | 899 MiB | 633 MiB |
| Peak RAM used / least available | 1,966 / 2 MiB | 1,968 / 0 MiB |
| Least free space on the root filesystem | 10.06 GiB | 11.60 GiB |
| Upload 183,816 rows | 14.22 s | 20.91 s |
| Upload one new event during publication | 94 ms | 157 ms |
| First MCP `analysis_brief` after restart | Missed 240 s deadline | 49.79 s |
| Kernel OOM kill | Chromium, host-wide | Python, in a memory-limited container |

Linux reported 1,968 MiB of usable RAM after boot. Peak means the largest sampled
`MemTotal - MemAvailable`. The sampler aimed for four reads per second. Under pressure
the longest gaps were 214 s with Chromium and 733 s with SQLite. The kernel journal
caught the Chromium kill after the sampler’s last read. [Default job](https://github.com/sam-dumont/wow-mcp/actions/runs/45994347943/job/140489861886) · [SQLite job](https://github.com/sam-dumont/wow-mcp/actions/runs/45993666691/job/140487558874).

Both profiles used a fresh Ubuntu 24.04 x86-64 guest with two virtual CPUs, 2 GiB
allocated to the whole machine, zero swap and a 16 GiB virtual disk. Docker, its
containers, the OS and the test clients shared the guest. One owner had the current
nine-character demo plus a second copy of Nibbet’s history dated one year earlier.
The initial upload held 183,816 rows. The concurrent upload added one new Hesta event.
The default profile included VictoriaLogs, synthetic armory samples in VictoriaMetrics,
the checksum-pinned `demo-3d-7` game files, items, and the real Chromium renderer.
The minimal profile used SQLite with items and 3D switched off.

The static server already had a public and a sealed site for Hesta. We killed the
next publisher with SIGKILL after its staging directory appeared. The server still
returned the same bytes for the home page, character list and sealed-site entry page.
The concurrent test then started a fresh publisher and called the MCP over HTTP after
OAuth login. It allowed 900 seconds for pictures, as in the nine-character docs demo.

### What fits on 2 GiB

With the UI and MCP stopped, the SQLite publisher finished in 385.56 s. The public
site held 125 files and the encrypted site held 190. Reading the public roster and
decrypting the sealed roster returned all nine characters. This setup produced no
portraits. On this 2 GiB machine, schedule a SQLite publish for a time when you can
stop the UI and MCP, then restart them after the build. The guest kept at least
485 MiB available through backup, restore, replay
and publication, with zero OOM kills. [Minimal run](https://github.com/sam-dumont/wow-mcp/actions/runs/45997232907/job/140499569452).

We repeated recovery on fresh 2 GiB guests with publication out of the way. We stopped the
writers, archived the event directory, extracted into a new directory and compared every
file by SHA-256. We then ran the real shipper’s `--replay` twice against a second, initially
empty store. Both restored stores and all four replays returned the original distinct event
counts for each of the nine characters (183,816 in total). SQLite passed `integrity_check`
and held exactly 183,817 rows after restore and after each replay.

| Event backup and recovery | VictoriaLogs | SQLite |
|---|---:|---:|
| Event directory on disk / compressed archive | 29.28 / 2.40 MiB | 88.38 / 16.79 MiB |
| Backup / extract | 0.11 / 0.11 s | 1.80 / 0.47 s |
| First / second shipper replay | 5.82 / 5.28 s | 14.71 / 12.42 s |
| Least free disk, including the backup and second store | 10.06 GiB | 11.41 GiB |

The VictoriaLogs recovery guest kept at least 929 MiB available. The measurements include
Docker and the guest OS. The disk figures include the originals, the extracted copies and
the shipper input on the same filesystem. [VictoriaLogs recovery](https://github.com/sam-dumont/wow-mcp/actions/runs/45997232907/job/140499569083) · [Exact measurements and per-character restore counts](https://github.com/sam-dumont/wow-mcp/blob/main/docs/self-host/host-budget-2026-11.json) · [Guest setup, fixture, workload and verification scripts](https://github.com/sam-dumont/wow-mcp/tree/9c8b53a07fbd294b6d53d58a922f25e16358fba8/dev/host-budget).

## Three players on two CPUs

The owner had 89,356 events. Two friends had 79 and 68. Each had a separate account,
page, upload password and store tenant. The owner played on 32 dates in the fixture.
We repeated those dates a year earlier. The friends each had one short outing.

| One run per setup | VictoriaLogs, items and 3D on | SQLite, items and 3D off |
|---|---:|---:|
| Upload the owner's 89,356 events | 1.85 s | 4.62 s |
| First owner character page after restarting the UI | 8.14 s | 12.74 s |
| First character page for each friend | 6.32 s / 1.87 s | 4.68 s / 2.74 s |
| Repeat the owner page | 0.001 s | 0.001 s |
| First MCP `analysis_brief` for the owner during publication | 21.04 s | 20.82 s |
| Read the other friend’s cached character page during publication | 0.038 s | 0.029 s |
| Replay one friend's 79-row upload during publication | 0.056 s | 0.041 s |
| Build the public and encrypted sites for all three players | 86.58 s | 70.46 s |
| UI and MCP peak, including query workers | 1,022 MiB | 987 MiB |
| Publisher peak | 602 MiB | 756 MiB |
| Static server peak, idle | 48 MiB | 47 MiB |

The VictoriaLogs run had the UI within 3 MiB of its cap. It completed the requests
without an out-of-memory kill. The two log servers used 161 and 22 MiB at the end of the
build, measured with `docker stats`. Their peaks weren't recorded. Each service can peak
at a different time. The nine-character run above measures the whole guest.

The publisher had a warm item catalog and icon cache. All six sites built. The owner’s
public site contained 80 JPEG icons. The runs in the table started before the players
supplied files from the game client.

### After the players supplied their 3D files

With the client files present, the same VictoriaLogs workload took 154.02 s to publish.
The owner’s public and encrypted sites each held 101 model and texture files (34.35 MiB)
and 25 past equipment states. The two friends had no recorded armory profile. Their
lookups found no model for the default sex in the supplied files. The publisher peaked at
615.5 MiB. The UI hit its full 1 GiB limit. Linux recorded 121 memory-limit events, zero
out-of-memory kills and zero bytes of swap. The owner’s first page took 10.51 s and
the MCP call took 31.04 s. Other work was running on the host.

We checked the three recorded appearances separately on the Mac, with the restored
SQLite stores, the `demo-3d-5` client files and synthetic armory profiles in
VictoriaMetrics 1.156.0. All three characters had both HD and Classic models. Each
public and friends’ bundle held 115 client files for Nibbet, 64 for Hesta or 75 for
Brakka. Building all six sites took 134.87 s with no CPU or container memory cap.

These three-player runs omitted the renderer. The live Battle.net exporter, reverse
proxy, simultaneous long AI queries and browser traffic still need their own measurements.

### Repeat the workload

Use a disposable install with a fresh event store, a fresh UI state folder, one owner
and two friends. Enable each public site. Run the normal HTTP MCP with a test identity
provider and the normal per-person publisher. Keep the catalog cache when restarting
the UI. [Shared install settings](friends.md).

1. Check out `36f8ae1`. Run `uv run python dev/seed.py --end-day 2026-11-26 --out recent.ndjson`.
   Repeat with `--end-day 2025-11-26 --out older.ndjson`. Keep Nibbet-Gearholt from
   both files for the owner, Hesta-Bellforge from the recent file for one friend, and
   Brakka-Ironvale from the recent file for the other. Append a colon and the four-digit
   year from each event's `_time` to its `source_id` so the repeated month has distinct IDs.
2. POST the files through `/ingest` with the respective upload credentials, up to 2,000
   NDJSON rows per request. Force-flush both VictoriaLogs stores. Restart the UI, sign
   in to the MCP, and request `/api/char?name=...` for the owner, then each friend,
   then the owner again. The page timers include the whole HTTP response.
3. Force one publish for all three people. As it starts, call the owner's `analysis_brief`,
   repeat the first friend's upload and request the second friend's page concurrently.
   Check all three sites and read `/sys/fs/cgroup/memory.peak` for each Python service.
4. Stop the writers and databases. Archive the event directories, extract into new
   directories, and compare the SHA-256 hashes and the restored event counts. For the
   shipper test, put the gzipped inputs in each player’s `sent/` folder and run
   `--replay` against a second empty SQLite store. [Restore from the shipper](backup.md#restore).

The run used arm64 Linux on Docker Desktop 29.9.1 on an Apple silicon Mac. The containers
shared virtual CPUs 0 and 1 of a 14-CPU, 31.3 GiB Docker VM. The UI and both log servers
each had a 1 GiB limit. The components ran from the checkout with Python 3.15.2,
DuckDB 2.0.0, FastMCP 4.2.0 and VictoriaLogs 1.54.0. The requests stayed inside a
private network namespace. Background website work on the Mac can affect timings.
A first request after restarting the UI can also overlap its normal background page preparation.

On a Linux cgroup v2 install, this shows the UI's peak in bytes and how often it hit
its limit. Run it after your longest character page, a publish and an MCP query.

```sh
docker compose exec -T ui python -c 'from pathlib import Path; p = Path("/sys/fs/cgroup"); print((p / "memory.peak").read_text(), (p / "memory.events").read_text())'
```

## Disk and recovery

| Event directories only | VictoriaLogs | SQLite |
|---|---:|---:|
| Sum of file sizes after stopping the stores | 1.42 MiB | 42.63 MiB |
| Gzip archive | 1.42 MiB | 8.16 MiB |

The VictoriaLogs directories held 7,017 files, many small. The sum of their lengths
excludes filesystem allocation. Both restored log servers answered with the original
distinct event counts of 89,356, 79 and 68. The same input had enough repeated events
and field values to compress especially well. For a comparison, the 0.7.0 release
candidate stored a different 100,705-event VictoriaLogs fixture in 6.5 MiB.

All four restored SQLite files passed `PRAGMA integrity_check`. The three event stores
returned the original counts. On a fresh SQLite install, the shipper’s `--replay` restored
the owner’s 89,356 rows in 4.03 s and each friend in 0.23 s over loopback. Sending the
79-row archive a second time left 79 rows on disk.

The figures above exclude downloaded icons, client models, generated sites, the UI state
and the player's shipper archive. Client 3D files have a separate limit of 512 MiB per
person by default (`MODEL3D_ASSETS_MB`). VictoriaLogs keeps events for 100 years unless
a disk ceiling removes the oldest days. SQLite keeps every day in each player's file.
[Disk settings](../install.md#disk-space) · [Back up and restore](backup.md).
