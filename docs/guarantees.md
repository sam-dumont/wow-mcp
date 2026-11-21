# What the pipeline guarantees

**For:** contributors, and anyone who wants to know which promises are tested. **At the
end:** you know each guarantee from the game files to the page and the AI, and the test that
breaks when one stops holding.

Each line below is a property test in [`tests/invariants/`](../tests/invariants/). The tests
run on made-up histories: the demo character (`tests/degraded.py`) plus seeded variations
(`tests/invariants/histories.py`) that put records on the same millisecond across the 9 to 10
and 99 to 100 record and chunk boundaries. A property that fails is a bug in the product, never
a reason to loosen the test. The folder runs in about 35 seconds with `make test` (140 seconds
on one core).

## The guarantees

| # | Guarantee | Test |
|---|---|---|
| 1 | Server lost, PC kept: `--replay` gives back the same events and the same prepared facts. | `test_restore.py::test_replay_restores_the_same_events_and_facts` |
| 2 | `sent/` lost too: `--reextract` dates every event as the live run did, none approximate (no `at_basis`). | `test_restore.py::test_reextract_keeps_every_observation_time` |
| 3 | One batch, eight batches, batches out of order or a seventh of the rows a day late: the same prepared facts and metric answers. The shipper gives the same events whichever runs read which Recorder chunks, and a zone's or a flight's time doesn't depend on them (a flight that takes off in one chunk and lands in the next). | `test_arrival.py::test_arrival_schedule_changes_no_answer`, `test_arrival.py::test_any_chunk_schedule_gives_the_same_facts` |
| 4 | A batch sent twice changes no total. | `test_arrival.py::test_a_resent_batch_changes_no_total` |
| 5 | Records on the same millisecond keep the Recorder's own order (chunk, then index, as numbers: 10 after 9). | `test_arrival.py::test_equal_times_keep_the_recorders_order` |
| 6 | Every ratio in `semantic.yaml` reads its numerator and its denominator over the same rows, its `eligibility`. Checked for every ratio, with rows added that only one side would count. | `test_paired.py::test_both_sides_read_the_eligible_rows` |
| 7 | A cut list says `more`; a store that doesn't answer or a second too full to read whole gives an error that says so, never a part of the answer; a dimension a metric doesn't have is refused. Every MCP tool and the page's main answers. | `test_incomplete.py` |
| 8 | The page and the MCP give the same deaths, zones, levels, dungeons and sessions, with `WEIGHTS` set and in other timezones. A friend with the same play gets the same numbers as the owner, and neither reads the other's character. | `test_consumers.py` |
| 9 | Sessions, days and ranges follow the time rules below, on a midnight, on both DST changes and over a 3-day break. | `test_time_rules.py` |
| 10 | A finding judged under another prep version, semantic model or reference data is marked "to revalidate" with the reason, every night until judged again; a finding from before manifests is carried over once. | `test_definitions.py` |

## Time rules

- **Sessions.** More than 330 seconds without a Recorder record ends a session; exactly 330
  continues it. The page's journal, the MCP's `sessions` and the analysis split the same way.
- **Durations** are real seconds. An hour of play across a DST change counts one hour,
  whatever the wall clock jumped by (01:30 to 03:30 local is one hour on the night in March).
- **Days** are the player's local days (`WOW_TIMEZONE`, Europe/Brussels by default): from
  00:00:00 included to the next 00:00:00 excluded. That day lasts 23 hours in March and 25 in
  October. Per-day answers add up to the total.
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
- `dashboard_refresh` reported no refresh timer when the store failed, rather than saying it
  couldn't read them.
- The MCP's `sessions` capped its list at 10 without saying so, and described the old
  20-minute session rule.
- A flight's time was the shipper's: it set `duration_s` on the take-off only when the land
  record came in the same run, so a flight across a chunk roll read in two runs had none in the
  analysis. The server now takes it from the stored land record (`stores.taxi_time`), for the
  page's flights and the analysis' records alike (`PREP_VERSION` 3).
