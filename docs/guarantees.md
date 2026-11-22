# What the pipeline guarantees

**For:** contributors, and anyone who wants to know which promises are tested. **At the
end:** you know each guarantee from the game files to the page and the AI, and the test that
breaks when one stops holding.

Each line below is a property test in [`tests/invariants/`](../tests/invariants/). The tests
run on made-up histories: the demo character (`tests/degraded.py`) plus seeded variations
(`tests/invariants/histories.py`) that put records on the same millisecond across the 9 to 10
and 99 to 100 record and chunk boundaries. A property that fails is a bug in the product, never
a reason to loosen the test. The folder runs in about 45 seconds with `make test` (160 seconds
on one core).

## The guarantees

| # | Guarantee | Test |
|---|---|---|
| 1 | Server lost, PC kept: `--replay` gives back the same events and the same prepared facts. | `test_restore.py::test_replay_restores_the_same_events_and_facts` |
| 2 | `sent/` lost too: `--reextract` dates every event as the live run did, none approximate (no `at_basis`), from an archive made by 0.7.0 or later (older commits have no capture manifest: [Recovery drill](#recovery-drill)). | `test_restore.py::test_reextract_keeps_every_observation_time` |
| 3 | One batch, eight batches, batches out of order or a seventh of the rows a day late: the same prepared facts and metric answers. The shipper gives the same events whichever runs read which Recorder chunks, and a zone's or a flight's time doesn't depend on them (a flight that takes off in one chunk and lands in the next). | `test_arrival.py::test_arrival_schedule_changes_no_answer`, `test_arrival.py::test_any_chunk_schedule_gives_the_same_facts` |
| 4 | A batch sent twice changes no total. | `test_arrival.py::test_a_resent_batch_changes_no_total` |
| 5 | Records on the same millisecond keep the Recorder's own order (chunk, then index, as numbers: 10 after 9). | `test_arrival.py::test_equal_times_keep_the_recorders_order` |
| 6 | Every ratio in `semantic.yaml` reads its numerator and its denominator over the same rows, its `eligibility`. Checked for every ratio, with rows added that only one side would count. | `test_paired.py::test_both_sides_read_the_eligible_rows` |
| 7 | A cut list says `more`; a store that doesn't answer or a second too full to read whole gives an error that says so, never a part of the answer; a dimension a metric doesn't have is refused. Every MCP tool and the page's main answers. | `test_incomplete.py` |
| 8 | The page and the MCP give the same deaths, zones, levels, dungeons and sessions, with `WEIGHTS` set and in other timezones. A friend with the same play gets the same numbers as the owner, and neither reads the other's character. | `test_consumers.py` |
| 9 | Sessions, levels, days and ranges follow the time rules below, on a midnight, on both DST changes, on a ding and over a 3-day break. | `test_time_rules.py`, `tests/test_timebounds.py` |
| 10 | A finding judged under another prep version, semantic model, reference data, timezone or gap, or gear scoring (when it reads the upgrade candidates) is marked "to revalidate" with the reason, every night until judged again; a finding from before manifests is carried over once. | `test_definitions.py` |
| 11 | A crash or a failed write at any step between the game files and the server's store (the crash points below) loses no event and stores none twice in another form: after a restart, every event a run without the crash sends is stored, every copy of one `source_id` is the same line, and the prepared facts are the same. | `test_crash.py::test_a_crash_loses_and_doubles_nothing` |
| 12 | `/ingest` takes an upload whole or not at all: one that ends before its `Content-Length` stores nothing and isn't answered 2xx, even when every line that came is whole. The shipper's spool and `state.json` reach the disk before the rename that makes them count, the spool before the state, and each rename reaches its directory on the disk after it (not on Windows: [What the crash tests prove](#what-the-crash-tests-prove)). The same for the spool file shortened after a POST, the update's new script and the Recorder's files. The copy in `sent/` is on the disk after each POST, one fsync per POST, before the spool is shortened. | `test_crash.py::test_a_cut_upload_stores_nothing`, `test_crash.py::test_the_spool_and_the_state_reach_the_disk_before_they_count`, `test_durability.py` |
| 13 | A snapshot sent again keeps its observation time: gear, bags, quest sets, character sheets and Battle.net's read. The same content seen again later is a second observation and never moves the first. | `test_evidence.py::test_a_*`, `test_bnet_a_*` |
| 14 | A snapshot dated only approximately (`at_basis`: an archive commit, or the shipper's clock) never replaces one with an exact time; a Battle.net read cut half way never replaces the last complete one. | `test_evidence.py::test_b_*`, `test_bnet_b_d_*` |
| 15 | Two conflicting snapshots on one second give the same answer in either arrival order, on the page, the MCP and the analysis' tables. | `test_evidence.py::test_c_*` |
| 16 | A snapshot that lacks a part (the bank, the equipped items, the money, quests a smaller set doesn't list, a Battle.net part that failed) doesn't erase it from an earlier complete one. | `test_evidence.py::test_d_*` |
| 17 | What no snapshot says stays unknown (`null`), never 0 or empty: a bank never opened, a sheet without items, money never read, a Battle.net read never complete. | `test_evidence.py::test_e_*` |
| 18 | However many partial snapshots follow a complete one, the page, the MCP and the analysis give the same equipment, bank and money, each with its own observation time (`money_at`, the bags' `at`, and `parts` in the `gear` and `polestar` coverage): a part read from an older snapshot keeps that snapshot's time. | `test_snapshot_parts.py` |
| 19 | With an archive (`VL_ARCHIVE_URL`), a read whose range reaches the archive's days while the archive doesn't answer fails with an error that says so (`ArchiveUnavailable`), never the recent days alone as the whole history; the event cache keeps what it had and nothing reads the missing days as deleted. Both on the first read and after a complete history was cached. | `test_archive.py` |
| 20 | `--replay` sends every whole gzip member of a damaged `sent/` file (one cut short by a crash, at the end or between two whole ones, or garbage after them), names the file and the bytes it couldn't read, and exits 1. A damaged file never ends in "sent again" and exit 0. | `test_sent_damage.py` |
| 21 | A definition change never adds weight: a finding gains support only from source events its last support didn't see (their ids, times and revisions), never from tables a definition change moved. Another `WEIGHTS` (Holy to Shadow), a prep version, the semantic model or a setting, with the events held fixed: the affected findings are to revalidate, the others still, none confirmed, and proposing one again on the same play gains nothing. | `test_definitions.py::test_changing_weights_*`, `test_definitions.py::test_a_definition_that_moves_prepared_rows_adds_no_weight` |
| 22 | Advice never outlives its finding's check: a nudge or pattern line citing a finding to revalidate, doubtful or gone is held on every reader (the page, `play_analysis`, `whats_new`, the plan's facts, `/zogzog/` and the share port), from the findings as they are at answer time, and comes back once they are judged again. | `test_dependents.py` |
| 23 | The Deaths section's "Per level" reads deaths and levels over the same levels: the ones the Recorder saw whole (from the ding into a level, on or after its first record, to the ding out, with no gap). The level in play, the levels before the Recorder and a level it missed part of leave both sides; the full view's answer names the levels, `/zogzog/` says how many; none of them gives no ratio, never 0. | `test_deaths_per_level.py` |

## Crash points

An event goes from the game files through the shipper's spool, the network, the server's store
and the server's 200 (sent only once the store has the lines) to the shipper's checkpoint (the
spool file shortened or removed, the copy in `sent/`). These are separate steps, not one
transaction, so each boundary is safe on its own. Property 11 kills the shipper (or fails the
I/O) at each one, on the first run and on the last of a few play days, then restarts it:

| Crash point | What happens on the restart |
|---|---|
| After the spool file is written, before `state.json` | The run extracts the same records again: the same events under the same `source_id`, which queries keep once. |
| Mid-upload | The server stores nothing of the cut upload (property 12); the spool file is still whole and goes again. |
| After the server's store, before its 200 | The shipper sees no answer and sends the lines again: the same lines, stored twice, kept once. |
| After the 200, before the shipper's checkpoint | As above: the spool file still holds the lines, they go again. |
| Mid-append to `sent/` (after the 200) | The spool file still holds the lines and they go again, so the server has them. The copy in `sent/` keeps a member cut short, then the member the restart appends with the same lines. `--replay` skips the cut one and sends every event, and still says the file is damaged and exits 1: it can't know a later member repeats the cut one (property 20, `test_sent_damage.py::test_a_kill_mid_append_then_the_restart`). |
| Mid-write of `state.json` | The new state is written to a temporary file and renamed in place; the old one is still whole. |
| `state.json` cut short on the disk (a power cut that kept the rename, not the data) | The shipper goes on from the state before it (`state.json.prev`) and extracts the last run's records again. Starting over from nothing sent the snapshots of earlier days again under their ids with a later time. |

## What the crash tests prove

They prove what happens at the points they choose. Property 11 raises an exception the shipper
can't catch (a kill) or fails an I/O at each crash point above: between a file's write and its
rename, mid-upload, after the server's store and before its 200, after the 200 and before the
checkpoint, half way through `state.json`. Two more cases build the disk a power cut can leave
by hand: a `state.json` cut short after its rename, and a member of `sent/` cut short.
`test_durability.py` records every `os.fsync` and rename, and checks the order: a file flushed
before its rename, its directory flushed after it, one flush per POST for `sent/`.

They don't prove:

- **A real power cut.** The tests run in one process on a healthy disk, and the operating
  system's cache never goes away under them. A missing fsync shows up only as a missing call
  in `test_durability.py`.
- **The drive's own write cache.** fsync hands the data to the drive; a drive that acknowledges
  writes it still holds in its cache can lose them. On macOS the shipper asks the drive to empty
  that cache (`F_FULLFSYNC`, falling back to plain fsync where the filesystem refuses it); on
  Linux and Windows it relies on fsync doing its job.
- **Filesystem bugs**, or a network share that refuses to flush a folder: the shipper goes on
  without it.
- **Windows' folders.** Python can't open a folder there to flush it (`os.open` fails), and
  there is no portable way to do it. The renames rely on NTFS journaling its metadata and on
  `os.replace` being `MoveFileEx`. The files themselves are flushed as on the Mac.

## Time rules

One module holds the edges: [`wowstats/timebounds.py`](../wowstats/timebounds.py). Prep, the
semantic layer, the pages and the MCP call it instead of writing `<=` or `<` by hand; its own
tests ([`tests/test_timebounds.py`](../tests/test_timebounds.py)) sit on the edges: equal
timestamps, midnight, both DST changes, an empty history.

- **Windows are half-open**, `[start, end)`: the start is in, the end is not. Two windows that
  touch share no instant, so a record on the edge counts once. A level is `[its ding, the next
  ding)`: a death on the ding's own second belongs to the new level, on the page, in Back in
  time and in the analysis.
- **A session is closed**, `[first record, last record]`: both ends are records. What happens
  on its last record's time (a death, a dungeon entry, a grouped player) is in it, on the page
  and in the MCP.
- **State at a time** is the latest one written at or before it (`t <= x`): a ding, a gear read
  or a level written at `x` already holds at `x`. Before the first one there is none.
- **Sessions.** More than 330 seconds without a Recorder record ends a session; exactly 330
  continues it. The page's journal, the MCP's `sessions` and the analysis split the same way.
- **Durations** are real seconds. An hour of play across a DST change counts one hour,
  whatever the wall clock jumped by (01:30 to 03:30 local is one hour on the night in March).
- **Days** are the player's local days (`WOW_TIMEZONE`, Europe/Brussels by default): from
  00:00:00 included to the next 00:00:00 excluded. That day lasts 23 hours in March and 25 in
  October. Per-day answers add up to the total. **Weeks** are ISO weeks, Monday 00:00 local,
  167 to 169 hours. The nightly recheck runs once per local day.
- **Ranges** in `query_metric`: `since` is included. `until` as a date includes that whole
  day; as a date and time it is included too.
- **Offline time** counts nowhere: time in a zone and online time stop at a session's last
  record, and the next login starts a new zone visit.

The page and the MCP's answers carry UTC times; only the analysis groups by `WOW_TIMEZONE`.

## Bugs these tests found

Fixed in the same change as the tests:

- The shipper gave a Recorder record its zone, a guide step its duration and a session its
  login flag from what one run had read. A run that started in the middle of a session (the
  previous chunks sent the day before) gave its first records no zone and called them a
  login. It now carries the
  last zone and step from the chunk before (`rec_ctx` in `state.json`).
- `play_sql` and `query_metric` cut a long answer at its size limit and still said
  `more: false`.
- `route_reference` dropped the character's side and class without a word when the store
  failed, and answered for every side.
- `talent_changes` told a player who has WoWSims Exporter to install it when the store
  failed to read its saves.
- Back in time counted a death on a ding's own second in both levels (review 5). It's the
  new level's now, as the page's deaths by level and the analysis already said.
- The MCP's `sessions` left out a dungeon entered and a player grouped with on the session's
  last record (review 5); the page's journal had them.
- `dashboard_refresh` reported no refresh timer when the store failed, rather than saying it
  couldn't read them.
- The MCP's `sessions` capped its list at 10 without saying so, and described the old
  20-minute session rule.
- A flight's time was the shipper's: it set `duration_s` on the take-off only when the land
  record came in the same run, so a flight across a chunk roll read in two runs had none in the
  analysis. The server now takes it from the stored land record (`stores.taxi_time`), for the
  page's flights and the analysis' records alike (`PREP_VERSION` 3).
- `/ingest` stored the lines of an upload that ended before its `Content-Length` (the shipper
  killed or the connection lost mid-upload) and answered 200 to nobody.
- A `state.json` cut short on the disk made the shipper start over from nothing: it sent the
  roster and Questie snapshots of earlier days again with the newest file time, so the server
  dated them days late. Neither the spool nor the state was flushed to the disk before its
  rename, and the state had no copy to fall back on.
- `test_consumers.py` left the analysis' workspace cache (built under the Auckland case's
  timezone) for the owner-and-friend test on the same worker.

Evidence properties (13 to 17), fixed in the same change as their tests:

- Readers picked "the newest" gear sheet, bags and character sheet by time alone: an
  archive copy dated by its commit (an upper bound) beat the exact sheet before it, and two
  on the same second gave whichever arrived first. One rule now (`stores.SNAPSHOT_SORT`,
  `stores.snapshot_rank`).
- Bags, quest sets and the roster's sheets have ids made of their content: the same content
  seen again later replaced the first copy, so quest evidence moved to the later day.
  `stores.record_id` keys them by time too.
- The shipper sent an empty bank for a bank Syndicator never saw, and no items for a
  character Syndicator didn't know: both read as "holds nothing" and hid the earlier read.
  They are left out now, and readers take them from the last snapshot that has them
  (`stores.newest_with`), the money too.
- The analysis took the newest gear sheet by time, not the one the page shows.

The shipper's disk writes and its copy in `sent/` (properties 12 and 20, review 6), fixed in the same change as their tests:

- `--replay` read a `sent/` file as one stream in 1 MiB reads. A member cut short by a crash
  raised in the read that also held the whole members before it, so a file of two whole
  members and a cut one sent nothing, and the run still exited 0 (review 6). The old test
  had other files in `sent/`, whose events hid the loss. It now reads member by member.
- The spool and `state.json` were flushed before their renames, but the renames weren't: no
  fsync of the folder. They sit in two folders, so a power cut could keep the state's rename
  and lose the spool's: the state then counted on a spool file that wasn't there, and those
  events never went.
- The copy appended to `sent/` after each POST was never flushed. Neither was the spool file
  shortened after a POST (written and renamed with no fsync at all: a power cut could leave it
  empty and lose the events still to send), the update's new script (an empty script runs
  nothing, so it can't update itself again), or the Recorder's files before the swap.

Snapshot parts (property 18), fixed in the same change as its test (review 6):

- The page and the MCP read the newest 10 gear sheets, bags and character sheets
  (`SNAPSHOT_TAKE`) and took each part from the newest of those that had it. Eleven partial
  snapshots after a complete one (a week of runs with Syndicator's cache lost) pushed the
  complete one out: no equipment, no bank and no money, while the analysis, which reads every
  event, still had them. The readers now ask, for each part, for the newest snapshot that
  carries it (`stores.in_force`), however old. The hero line's `money_at` was the newest
  sheet's time even when the money came from an older one; it is the money's own time now.

An archive that doesn't answer (property 19), fixed in the same change as its test (review 6):

- With `VL_ARCHIVE_URL` set, an archive that didn't answer was skipped without a word: the
  reader answered from the recent store alone. The event cache then took the missing old hours
  for retention and dropped them, and the analysis counted 1 death of 2 with the Recorder's
  coverage still `complete`. A read that needs the archive now fails (`stores.ArchiveUnavailable`,
  "no answer rather than one without the older days"); the page's prebuilt answers then keep
  the last whole ones, marked stale. A failed archive check is asked again after 30 seconds,
  not 10 minutes. The MCP's own store already raised.

## Every consumer, every guarantee

The guarantees hold system-wide, not only where the table above checks them (owner,
2026-11-21). Each cell names the test that proves it for that consumer, or why it doesn't
apply. The public view and `/zogzog/` count on their own: they are separate builds, the full
view's answers cut to their tier's shape (`wowstats/redact.py`). Paths are under `tests/`;
`matrix` is [`invariants/test_matrix.py`](../tests/invariants/test_matrix.py), `cov`
`test_coverage.py`, `inc` `invariants/test_incomplete.py`, `cons`
`invariants/test_consumers.py`.

| Guarantee | Full view | Public view | `/zogzog/` | MCP tools | Server analysis | Nightly recheck | Exporter readers |
|---|---|---|---|---|---|---|---|
| **Freshness shown** | `test_coverage_browser::test_the_lines_where_they_change_the_meaning`, `test_prebuilt::test_a_store_down_keeps_the_last_answers_marked_stale` | `matrix::test_the_static_answers_say_when_they_were_built` | `matrix::test_the_static_answers_say_when_they_were_built` | `cov::test_the_mcp_answer_carries_the_coverage` (`as_of` per source), `test_talents::test_mcp_reads_the_current_talents_from_battlenet` | `test_analysis_date_browser::test_how_you_play_says_when_and_who`, `analysis/test_analysis::test_a_finding_keeps_the_build_it_was_judged_under` | n/a: writes no answer; its findings carry the build they were judged under (`invariants/test_definitions.py`) | `test_bnet_freshness.py`, `test_battlenet_fill::test_battlenet_data_is_never_shown_past_30_days` |
| **Unknown is not zero** | `cov::test_back_in_time_deaths_without_the_recorder_are_not_recorded`, `matrix::test_deaths_without_the_recorder_are_unknown_in_every_view` | n/a: shows no count the Recorder makes (no deaths, kills or quests) | `matrix::test_deaths_without_the_recorder_are_unknown_in_every_view` | `cov::test_mcp_deaths_without_the_recorder_are_unknown`, `analysis/test_semantic_evidence::test_one_snapshot_is_an_unknown_change_not_a_zero` | `analysis/test_semantic_evidence::test_no_snapshot_is_unavailable` | n/a: re-runs the findings' SQL on the same layer as the server analysis | `test_bnet_scoping::test_what_battlenet_never_read_is_unknown_never_0_or_none`, `test_bnet_freshness::test_a_first_read_that_never_completed_shows_nothing` |
| **Coverage attached** | `cov::test_the_page_says_what_the_recorder_missed` | **open**: not in the public shape | **open**: not in the `/zogzog/` shape | `cov::test_the_mcp_answer_carries_the_coverage` | `cov::test_query_metric_and_play_sql_carry_the_coverage` | n/a: writes findings, not answers; the answer that shows them carries it (server analysis) | `cov::test_battlenet_states` |
| **Source order kept** | `test_zone_time::test_a_tie_goes_to_the_later_record_by_number_not_text` | n/a: the full view's answer, cut; no order of its own | n/a: as the public view | `test_zone_time::test_a_tie_goes_to_the_later_record_by_number_not_text` | `invariants/test_arrival::test_equal_times_keep_the_recorders_order` | same test: the recheck builds through `prep.build` | n/a: metric series, one value per time |
| **Paired eligibility** | `test_layer_pages::test_the_pages_say_the_same_numbers_from_the_layer`, `invariants/test_deaths_per_level.py` | n/a: no ratio | `invariants/test_deaths_per_level.py` (the full view's answer, cut) | `invariants/test_paired::test_both_sides_read_the_eligible_rows` | `invariants/test_paired::test_both_sides_read_the_eligible_rows`, `analysis/test_semantic_evidence::test_vs_route_pairs_the_levels_both_sides_read` | same: the same semantic layer | n/a: no ratio |
| **Completeness** | `inc::test_the_page_refuses_rather_than_shows_a_part`, `test_bounded_reads::test_more_same_second_events_than_a_page_is_an_explicit_failure` | `test_publish::test_vm_wow_down_neither_rebuilds_nor_fails`; live, the full view's answer (`inc`) | `test_publish::test_vm_wow_down_neither_rebuilds_nor_fails`, `test_publish_cleanup::test_a_failed_build_keeps_the_site_and_leaves_no_folder` | `inc::test_every_tool_refuses_or_answers_whole`, `inc::test_a_cut_text_says_more` | `inc::test_more_is_true_exactly_when_rows_are_left_out`, `inc::test_a_dimension_outside_a_metric_is_refused` | `matrix::test_one_characters_store_failure_leaves_the_others_rechecked` | `test_bnet_freshness::test_after_an_exporter_restart_a_half_read_falls_back_to_the_last_complete_one`, `test_bnet_freshness::test_a_vm_wow_error_is_never_never_read` |
| **Same config as the UI** | `cons::test_page_and_mcp_agree`, `test_mcp_weights::test_one_settings_builder` | `test_mcp_weights::test_one_settings_builder` (every view's `App` calls `settings_from_env`) | as the public view | `cons::test_page_and_mcp_agree`, `test_mcp_weights::test_page_and_mcp_score_the_weights_spec` | `cons::test_page_and_mcp_agree` (per local day), `matrix::test_the_analysis_scores_gear_for_the_pages_weights` | `invariants/test_definitions.py` (`settings`: another timezone, or another `WEIGHTS` for a finding on the upgrade candidates, marks it to revalidate), `analysis/test_analysis::test_nightly_schedule` | n/a: reads no setting; WEIGHTS apply after, in the views |
| **Tenant permissions** | `test_tenancy::test_friend_sees_only_their_own_data`, `cons::test_neither_reads_the_others_character` | `test_public_view::test_public_never_leaks`, `test_publish::test_public_bundle_holds_only_the_allowlist` | `test_publish::test_zogzog_bundle_holds_only_its_allowlist`, `test_tenancy::test_bundles_are_per_person` | `test_tenancy::test_every_mcp_tool_as_the_friend` | `analysis/test_auto::test_a_run_reads_and_writes_only_its_own_tenant`, `analysis/test_semantic::test_a_friend_cant_query_the_owners_character` | same reader and state folders as the server analysis | `test_bnet_scoping::test_a_friend_naming_the_owners_character_reads_nothing`, `test_tenancy::test_battlenet_data_is_each_persons_own` |

Found in review 6, fixed with their test:

- The run manifest left out the gear scoring. With only `WEIGHTS` changed (Holy to Shadow)
  the upgrade candidates moved, the support mark counted the prepared tables' rows, so it
  moved too, and the nightly recheck took the same play for new play: confirmed, weight 1.0
  to 1.5. The manifest now holds the resolved spec and the player's weight edits, and the
  support mark reads the source events (`prep.input_mark`). A finding proposed again after a
  prep or semantic change gained weight the same way; it doesn't now.
- A definition change marked a finding "to revalidate" and left the advice on it up: verify a
  finding, keep a nudge and a pattern on it, bump `PREP_VERSION`, run the night. The full
  view's nudge (verdict "now"), the pattern and the `/zogzog/` nudge stayed, as did the plan's
  facts and `whats_new`: the line screen only refused findings missing or doubtful, and only
  when a run saved the lines. Every reader now goes through `analysis.standing`.
- The Deaths section's "Per level" divided every Recorder death by Polestar's level count. With
  the Recorder on from the 12th, the demo read 3 deaths / 12 levels = 0.25; it died once in the
  one level the Recorder saw whole (33), so the answer is 1.0. With an evening off in level 28,
  0.9 (9 deaths over 10 levels), where the page said 11 / 12. The server works it out now
  (`views.deaths_per_level`) and the page shows over how many levels.

Gaps the matrix found, fixed with their test (review 5):

- `/api/char` and `/zogzog/` said 0 deaths for a character without the Recorder (the MCP
  already said unknown). Now `null`.
- One character's failed store read (`IncompleteRead`) stopped the nightly recheck for every
  character after it, and the night still counted as a success. The others are rechecked now,
  the failed one keeps its last answer and is named in the log, and the night exits 1.
- A finding judged under another timezone (or session or trip gap) wasn't marked to
  revalidate: its hours and days meant other hours. The analysis also read `WEIGHTS` with its
  own call; it goes through `settings_from_env` like the page and the MCP.
- The exporter readers turned a vm-wow error into the "never read" answer: coverage said
  `unknown`, the talents card and the MCP said "No talents known yet ... once it knows the
  character", the Legacy points "Battle.net hasn't read this account's characters". Now
  coverage is `unavailable` as of the last read this process saw (`Store.bnet_last_read`), and
  the talents and Legacy points say the store didn't answer.

Open, not fixed:

- **Coverage on the public view and `/zogzog/`.** Neither shape carries it, so they never say
  "the Recorder missed 1h" or "equipment last read 5 h ago". Adding it widens what leaves the
  server: the owner's call.

## Recovery drill

The tests above play made-up histories. Before 0.7.0 the restore also ran once on a real one:
a copy of the maintainer's own archive (`state.json`, `sent/`, the WTF archive), into servers
on 127.0.0.1 only (VictoriaLogs and `wow-stats ui`). The shipper ran in a throwaway HOME, with a
WoW folder rebuilt from the archive's own copy, never the game's.

- **Date:** 2026-11-22.
- **Procedure:** [Back up and restore](self-host/backup.md#restore) as on main `f458c4d`
  (0.7.0-rc.8 plus #86 to #93), followed as written.
- **Reference:** an empty server filled by a normal shipper run (the 24 files of `sent/` put
  back in the spool), then 34 answers recorded: per character (two), the page's `char`,
  `journal`, `overview`, `timeline`, `loot`, `dungeons`, `people` and `gear`, and the MCP's
  `character_summary`, `levels`, `deaths`, `zones`, `sessions`, `dungeons`, `guide_steps`,
  `gear` and `players`.
- **Restore:** the server's data folder deleted, `--replay`, the same 34 answers again.
  Then `--reextract` with `sent/` deleted, into a third empty server.

| | Events | Same fields | Same answers | Time |
|---|---|---|---|---|
| `--replay` | 25,880 of 25,880 ids | 25,880 | 34 of 34 | 1.6 s to empty and restart the server, 0.6 s to send, 8 s until the page listed the characters |
| `--reextract` | 24,516 ids; every one of the 23,017 Recorder records | 22,933 | 33 of 34 | 10.6 s |

A second `--replay` on top stored 51,760 rows for the same 25,880 ids and changed no answer.

What `--reextract` gives differently, and why:

- **Auction scans:** the server held 1,347 scan rows twice, under two realm keys an older
  shipper used. The re-read sends each once.
- **Names:** 1,347 scan rows and 219 Recorder records differ in their item, NPC or quest name.
  Names come from the data addons in the game folder, which the drill didn't read, so most came
  back empty; 56 quest names the very first run (11-18, an early version) didn't send came back
  from the Recorder's own records.
- **Logins:** 5 Recorder records gained the login mark the live runs missed when a session
  spanned two runs (fixed in #86).
- **Snapshots:** 14 gear, bag and character sheets only in the reference, 7 only in the
  re-read: older versions gave them other ids, and the archive holds a gear sheet no live run
  sent. That sheet is the one answer that differs: the timeline has 2 more gear lines.
- **`at_basis`:** 5 snapshots say `commit`. Every commit of this archive predates the capture
  manifest (#75), so a version the current files don't cover gets the commit's time. 3 of them
  share an id with a snapshot the live run dated exactly, which wins (guarantee 14).
- **Setup reports:** one per run (11 in the reference, 1 in the re-read), never history.

What the drill found:

- `--replay` stopped at "no WTF folder" without the game installed and exited 0 having sent
  nothing (#94).
- The MCP's `players` gave another top 20 on each call when players tied, and `zones` failed on
  a `NaN` from the store (#95).
- The restore page said a second replay "isn't stored twice" (it is, counted once): fixed in
  the page.
- `--reextract` ended with a normal run that advanced `state.json` (the bags, gear and quest
  checkpoints, the Recorder context, the setup report), against #77's own-checkpoint design: a
  background run after it skipped play only the recovery had sent. It now leaves `state.json`
  byte for byte as it was.
