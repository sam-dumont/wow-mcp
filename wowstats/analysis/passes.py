"""The play analysis' passes, as instructions for whatever model drives it.

No model runs on the server (owner, 2026-11-18). The user's own MCP client
(claude.ai, ChatGPT, Claude Code, a local model...) reads these through
`analysis_brief` and does the reasoning; the server holds the run's state
and judges every finding (wowstats.analysis.drive):

  survey     reads the schema, the neutral profile of every table and the
             knowledge base's contents, and writes 25-40 open questions
             tied to the player's goals; the server adds one neutral
             question for any table no question names and cuts the
             questions into lanes;
  lanes      a few questions each: SQL and the knowledge base, findings to
             propose_finding, which questions the data answered, and what
             to ask next;
  follow-ups the most promising "ask next" items, picked by the model,
             get a second round of lanes;
  coverage   a table no query has read yet gets a lane of its own;
  synthesis  the run's verified findings checked against each other
             (clashes get a reconciling query, then one merged finding or
             both marked doubtful), ranked, and nudges (actions, the
             only part the shared page shows) and pattern lines (full
             view) written, each nudge with its feasibility.

Every pass gets the same neutral material: the schema with the dictionary,
the tools, the player's goals in their own words and what the pass must
hand back. No hypothesis, no category, no earlier result (owner,
2026-11-18: the model asks its own questions). The brief's `facts` are
measurements the same for every run (a level's calendar beside its played
time, the worn gear against the spec it's scored for), not conclusions:
the seeded evaluation showed a client reading a three-week break as slow
play and a Shadow item on a Holy priest as fine when those two facts sat
only in columns it had to think of joining (docs/evaluation.md).
"""

from __future__ import annotations

import re

INTRO = """\
You are looking at one World of Warcraft: Forever character's play history, recorded in game by the Polestar \
addon and loaded into a DuckDB database (the schema section describes it; DuckDB's SQL: 7/2 is 3.5, `//` \
divides whole numbers, COUNT(*) FILTER (WHERE ...) counts a condition). The player wants to understand how \
they play. You run the analysis; the server keeps the run's state and judges every finding.

Your tools (the wow MCP server): `play_sql` runs one read-only SELECT on the database (paged): read the \
views per grain first (per_level, per_session, per_zone, per_dungeon, per_slot: the named metrics, one row per \
key, top of the schema section), the raw tables when no view has what you need; \
`list_metrics` and `query_metric` answer with named metrics (each with its definition and caveats) compiled to \
SQL, and a finding may cite a query_metric call as its evidence (metric_query) instead of SQL; `kb_search`, \
`kb_get` and `kb_lookup` reach a knowledge base: game reference (spells, items, quests, NPCs, zones and talents, \
by id or name), levelling strategy documents and the research catalogue's sourced statements, contradictions \
and open questions (kinds kb-statement, kb-contradiction, kb-open-question), each with its source, scope and \
last_verified; `propose_finding` hands one finding to \
the judge; `analysis_submit` hands in what a pass produced and answers with the next step; `analysis_progress` \
says where the run stands, so a new chat can go on with it. Pass the character's name as `char` on every \
call: the run, the database and the judge are that character's.

Two rules for everything the player sees (owner): it is actionable (what to do, where, what blocks it, the \
next step; a fact with nothing to do about it is an observation, kept apart), and it is gender-neutral (the \
character's name, "the character" or "you", never he or she)."""

FINDING_RULES = """\
A finding is something the data shows, with the SQL that shows it. Its `claim` is plain words for the player \
in which every number is a placeholder naming a column of the FIRST row that `sql` returns: {col}, {col:dur} \
(seconds as hours/minutes), {col:pct} (a ratio as a percent), {col:int}, {col:1f}. Never write a number above \
100 yourself. `expect` holds the values of those columns exactly as your query returned them: the judge runs \
the SQL again and refuses the finding if they differ. The SQL must read the character's tables: a constant \
(`SELECT 3 AS n`) is refused. For common counts and times (deaths, kills, time played) prefer a query_metric \
call as evidence. Describe what the player chose to do by what it cost \
and what it paid, never as a fault; describe interruptions neutrally and never guess why a session stopped. \
List in `kb` the ids of the knowledge base documents a finding relies on.

Two measurements to read before judging (the brief's `facts` lists where they stand out). Pace: a level's \
pace is its played time (levels.played_s against planned_s, or the metric vs_route); its calendar time \
(calendar_s) also counts the days the game was closed (offline_s, longest_gap_s). A finding about a slow, \
long or stalled level accounts for the offline time first, and says when a level's calendar was mostly a \
break; when recorder_missed_s is above 0, part of a silence was play the Recorder missed, not time away. \
Gear: the advice scores every item for one spec (gear.scored_for: the player's setting, else the \
talents); gear.bis_pct and gear.fit say how each worn item does for it. A finding that judges a worn item \
names that spec, and says when the item is built for another spec or class; its action points the slot \
at an upgrade for that spec (the brief's gear_fit lists each listed slot's best, or query_metric's \
`upgrades` model: upgrade_gain by slot and item, rank_now <= 3).

Every finding carries an `action`: `do` (what to do, concretely: with which item, quest, place, skill or \
habit), `where` (in game, or when), `blocked_by` (what stands in the way: a level, gold, a skill, a group; \
empty if nothing) and `next_step` (the first thing to do, where). Placeholders work there too. When the data \
shows something with nothing to act on, send `action: null`: it is kept as an observation and shown apart. \
The judge refuses an action too vague to act on. Write about the character by name, as "the character" or \
as "you", never he or she."""

FINDING = {
    "type": "object",
    "properties": {
        "key": {"type": "string", "description": "a short stable slug, lowercase and dashes"},
        "title": {"type": "string"},
        "claim": {"type": "string"},
        "sql": {"type": "string"},
        "expect": {"type": "object"},
        "holds": {"type": "string", "description": "optional condition over the row's columns that stays true "
                  "while the finding holds"},
        "effect": {"type": "string", "description": "optional: the column with the played seconds concerned"},
        "sample": {"type": "string", "description": "optional: the column with the number of observations"},
        "relevance": {"type": "integer", "enum": [1, 2, 3]},
        "kb": {"type": "array", "items": {"type": "string"}},
        "action": {"type": ["object", "null"], "description": "what to do about it; null: an observation",
                   "properties": {"do": {"type": "string"}, "where": {"type": "string"},
                                  "blocked_by": {"type": "string"}, "next_step": {"type": "string"}},
                   "required": ["do", "where", "blocked_by", "next_step"], "additionalProperties": False},
    },
    "required": ["key", "title", "claim", "sql", "expect", "relevance", "action"],
    "additionalProperties": False,
}
QUESTION = {"type": "object", "properties": {
    "id": {"type": "string"}, "question": {"type": "string"},
    "tables": {"type": "array", "items": {"type": "string"}}},
    "required": ["question", "tables"], "additionalProperties": False}
SURVEY_SCHEMA = {"type": "object", "properties": {
    "questions": {"type": "array", "items": QUESTION, "minItems": 10, "maxItems": 40}},
    "required": ["questions"], "additionalProperties": False}
LANE_SCHEMA = {"type": "object", "properties": {
    "lane": {"type": "string", "description": "the lane's id, as the server gave it"},
    "answers": {"type": "array", "items": {"type": "object", "properties": {
        "question_id": {"type": "string"}, "answered": {"type": "boolean"}, "note": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"},
                     "description": "the keys of the findings this answer rests on, as propose_finding stored them"}},
        "required": ["question_id", "answered", "note", "findings"], "additionalProperties": False}},
    "ask_next": {"type": "array", "items": {"type": "object", "properties": {
        "question": {"type": "string"}, "why": {"type": "string"},
        "tables": {"type": "array", "items": {"type": "string"}}},
        "required": ["question", "why", "tables"], "additionalProperties": False}, "maxItems": 5}},
    "required": ["lane", "answers", "ask_next"], "additionalProperties": False}
PICK_SCHEMA = {"type": "object", "properties": {"picks": {"type": "array", "items": {"type": "integer"}}},
               "required": ["picks"], "additionalProperties": False}
LINE = {"type": "object", "properties": {
    "text": {"type": "string"}, "findings": {"type": "array", "items": {"type": "string"}}},
    "required": ["text", "findings"], "additionalProperties": False}
SYNTH_SCHEMA = {"type": "object", "properties": {
    "keep": {"type": "array", "items": {"type": "string"}},
    "drop": {"type": "array", "description": "each finding of this run left out, with why; one named nowhere stays",
             "items": {"type": "object", "properties": {"key": {"type": "string"}, "why": {"type": "string"}},
                       "required": ["key", "why"], "additionalProperties": False}},
    "findings": {"type": "array", "items": FINDING, "maxItems": 10},
    "nudges": {"type": "array", "items": {"type": "object", "properties": {
        "text": {"type": "string",
                 "description": "what to do; values that change as the character plays are {column} "
                                "placeholders from a cited finding's row"},
        "gist": {"type": "string"},
        "where": {"type": "string"}, "blocked_by": {"type": "string"},
        "next_step": {"type": "string", "description": "the first thing to do, where"},
        "findings": {"type": "array", "items": {"type": "string"}},
        "verdict": {"type": "string", "enum": ["now", "at_level", "not_relevant"]},
        "at_level": {"type": ["integer", "null"], "description": "at_level: the level it becomes possible"},
        "constraints": {"type": "array", "items": {"type": "object", "properties": {
            "what": {"type": "string", "description": "the requirement, and the player's value against it"},
            "kb": {"type": "array", "items": {"type": "string"}, "description": "KB ids it comes from"}},
            "required": ["what", "kb"], "additionalProperties": False}}},
        "required": ["text", "gist", "where", "blocked_by", "next_step", "findings", "verdict", "at_level",
                     "constraints"],
        "additionalProperties": False}, "maxItems": 8},
    "patterns": {"type": "array", "items": LINE, "maxItems": 6},
    "clashes": {"type": "array", "items": {"type": "object", "properties": {
        "subject": {"type": "string", "description": "the thing both findings describe"},
        "findings": {"type": "array", "items": {"type": "string"}, "minItems": 2},
        "query": {"type": "string", "description": "the SQL you ran to reconcile them"},
        "outcome": {"type": "string", "enum": ["merged", "doubtful"]},
        "merged_key": {"type": "string", "description": "merged: the key of the new finding in `findings`"}},
        "required": ["subject", "findings", "query", "outcome"], "additionalProperties": False}}},
    "required": ["keep", "drop", "nudges", "patterns", "clashes"], "additionalProperties": False}

SURVEY_TASK = """\
Read the schema, the profile and the knowledge base contents (analysis_brief sections `schema`, `profile` and \
`kb`), and write between 25 and 40 open questions about how this player plays that bear on their goals. Every \
table should appear in at least one question's `tables`. Questions, not answers: each will be investigated \
separately with SQL and the knowledge base. You may run a few queries and knowledge base searches to see what \
the data holds before you write them. Hand them in with analysis_submit(pass="survey", answer={"questions": \
[...]}); the server cuts them into lanes."""

LANE_TASK = """\
Investigate the lane's questions with SQL (play_sql, about 20 queries per lane) and the knowledge base. Hand \
each finding to propose_finding as soon as you have it: the judge re-runs the SQL and stores it or says why \
not; fix a refused one and try once more, then move on. When the lane is done, hand in with \
analysis_submit(pass="lane", answer={"lane": ..., "answers": [...], "ask_next": [...]}): for each question \
whether the data answered it, with a one-line note and the keys of the findings the answer rests on, and up \
to five questions you would ask next. Only findings reach the player, never a note: whatever answers a question \
is a finding, an observation (action null) when there is nothing to act on. An answered question with no \
finding behind it comes back once.

""" + FINDING_RULES

PICK_TASK = """\
Below are questions that earlier lanes proposed to ask next. Pick the ones most worth a second round for the \
player's goals (at most {n}), by their number, and hand them in with analysis_submit(pass="pick", \
answer={{"picks": [...]}}). No queries are needed."""

COVERAGE_TASK = """\
Tables no query has read yet get a lane of their own, with the survey's question that named them (or a \
neutral one). Run them like any lane."""

SYNTH_TASK = """\
Below are the verified findings of this run (each re-run by the judge) and the questions asked.

First, check them against each other. Group the findings by the thing they describe (for example a place, a \
level, an equipment slot, a session) and look for claims about the same thing that disagree. For each such \
clash, run a query that reconciles them, then report it in `clashes`: either `merged`, with one new finding in \
`findings` that replaces the clashing ones and states the definition it uses (what is counted, over which \
span), its key in `merged_key`; or `doubtful`, when the query doesn't settle it. Doubtful findings stay out of \
nudges and patterns.

Then list in `keep` the keys worth showing the player, most important first, and in `drop` each finding you \
leave out, with why (a duplicate of a kept one, contradicted by a reconciling query...): a finding named in \
neither stays. You may write new findings that merge or sharpen kept ones (same \
rules; they are verified again). Then write at most five nudges. A nudge is an action, not a description: \
`text` says what to do, `where` where in game, `blocked_by` what stands in the way (empty if nothing), \
`next_step` the first thing to do, and `gist` the same in one short line; a nudge with no next step is \
dropped. Nudges are also what the page the player shares with friends shows, whole: there a nudge that names \
another player, a creature from the records, a clock time or a date, or says where the character is now ("you \
are in ..."), stays in the full view only; zones, dungeons, items and levels are fine. Then up to six \
`patterns`: plain lines that describe how the player plays, for the full view only. Every nudge and pattern \
names the finding keys it rests on. The text says "you", "the character" or the name, never he or she.

Check every nudge is something the player can act on. Look up its constraints in the KB: a required level, \
the level range of a place or instance against the player's current level, a skill requirement, a cost \
against the gold the data shows. List each in `constraints` with the KB ids it comes from, and set `verdict`: \
`now`, `at_level` (with `at_level`, the level it opens at) or `not_relevant` (the player has passed it, or it \
doesn't apply). A nudge with no game constraint has none listed and is `now`. A finding about the past stays \
a finding; a nudge built on it points at what fits the player now (the same kind of thing, in their range), \
or is not written. A nudge that names one option (a place, an instance, an item, a route) is checked against \
the others of the same kind the data holds: if one of them is strictly better on what the nudge argues (more \
upgrades for the player, more XP per hour, less time, in range now), name that one instead, or say why not. \
A nudge's text follows the finding rules for numbers: one above 100 typed by hand drops the nudge. \
Anything in a nudge or pattern that changes as the character plays (a level, a count, a percentage, a \
duration) is a `{column}` placeholder naming a column that a cited finding's query returns, never typed: \
the server fills it from the finding's row and renders it again every night. A line that types the level it \
was written at ("well behind level 32") is dropped once the character is past it. The \
server's answer lists every finding, nudge and pattern it dropped, with why.

Hand it in with analysis_submit(pass="synthesis", answer={...}). The server re-runs every reconciling query \
and merged finding, checks the nudges' verdicts against the player's level and their KB ids, and screens the \
nudges for the shared page.

""" + FINDING_RULES

PROCEDURE = """\
How a run goes (analysis_progress says where it stands; `next` in every answer says what to do):
1. survey: read the sections `schema`, `profile` and `kb` (one page at a time if your context is small), then \
`survey`, and hand in the questions.
2. lanes: read the section `lane` once; then for each lane the server hands you, investigate and hand it in.
3. follow-ups: read the section `followups` (the "ask next" list), hand in your picks; their lanes follow.
4. coverage: lanes for the tables still unread, if any.
5. synthesis: read the section `synthesis` (this run's verified findings, paged), hand in the synthesis.
Then tell the player in a few lines what was kept, what the nudges say, and what was refused and why.
A new chat goes on where the last stopped: call analysis_progress first."""

GOALS_UNKNOWN = ("not given: if you can, ask the player in one line what they want from the game (pace, gear, "
                 "dungeons, gold...) and pass their words as `goals` to analysis_brief; otherwise go on without")


def lanes_of(questions: list[dict], tables: list[str], per: int) -> list[list[dict]]:
    """Questions grouped by their first table, a few per lane."""
    by: dict[str, list[dict]] = {}
    for q in questions:
        t = next((t for t in q.get("tables") or [] if t in tables), "")
        by.setdefault(t, []).append(q)
    out = []
    for qs in by.values():
        out += [qs[i:i + per] for i in range(0, len(qs), per)]
    return out


def tables_in(sql: str, tables: list[str]) -> set[str]:
    words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", sql or ""))
    return {t for t in tables if t in words}
