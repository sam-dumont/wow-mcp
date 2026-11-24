# Evaluating the play analysis

**For:** contributors changing the analysis (its prep, passes, judge, semantic layer or prompts). **At the end:** you can check that an AI running the analysis finds a known effect in a history and invents nothing on a healthy one.

The analysis is run by the player's own AI, through MCP ([mcp.md](mcp.md), "The play analysis"). The judge checks
that every number a finding shows comes from its query. It can't check that the finding is the right one to make:
a query can count the wrong thing, and an AI can read a gap in the records as a habit. This evaluation does that
part, on synthetic histories where the right answer is known because we put it there.

## Run it

```sh
make eval-analysis                          # all five scenarios, at the same time
make eval-analysis EVAL_ARGS="--jobs 1"     # one after the other
make eval-analysis SCENARIO=death_spike     # one
make eval-analysis EVAL_ARGS="--judge"      # plus a model's opinion of the wording
```

The default client is Claude Code (`claude -p`), on your own login: a Max or Pro plan works, no API key needed.
`ANTHROPIC_API_KEY` is removed from its environment, so it never bills a key by surprise. `EVAL_CLAUDE_MODEL`
picks the model (the default is whatever your Claude Code uses).

Any OpenAI-compatible endpoint (OpenRouter, Ollama, vLLM, LM Studio, OpenAI) works too:

```sh
EVAL_OPENAI_BASE_URL=http://localhost:11434/v1 EVAL_OPENAI_MODEL=qwen3:32b make eval-analysis CLIENT=openai
```

`EVAL_OPENAI_KEY_FILE` names a file with the key when the endpoint wants one (a file, never an environment
variable). The model needs tool calling. The evaluation never reads a deployment's `ANALYSIS_*` settings or its
federated credentials: point it at an endpoint you pay for yourself, never at your server's.

`CLIENT=server` runs the server's own analysis runner instead, the code path behind "by the server" on a
self-hosted page ([self-host/server-analysis.md](self-host/server-analysis.md)), unchanged, one process per run
(`server_run.py`). Like the server, it gets no goals. It needs your own key, never a deployment's:
`EVAL_SERVER_KEY_CMD` is a command that prints it (your password manager, `sops decrypt ...`), run for each run
and handed to the runner's process in its environment only, never written anywhere; `EVAL_SERVER_KEY_FILE`
names a file holding it instead.

```sh
EVAL_SERVER_KEY_CMD='op read op://private/openai-eval/key' make eval-analysis CLIENT=server
```

`EVAL_SERVER_MODEL` defaults to `gpt-6.1-sol` and `EVAL_SERVER_PROVIDER` to `openai`; `EVAL_SERVER_BASE_URL`
points it at another endpoint. Prices are built in for `gpt-6.1-sol` ($2 per million prompt tokens, $0.10 cached,
$10 output), `gpt-6.1-terra` ($1 / $0.05 / $5), `gpt-6-luna` ($0.10 / $0.01 / $0.50) and `glm-5.3` ($1.40 / $0.26
/ $4.40, z.ai). Another model needs its prices (`EVAL_SERVER_PRICE_INPUT_PER_MTOK`, `..._OUTPUT_...`,
`..._CACHED_INPUT_...`; a local model can say 0), or it refuses to start: without them the cap can't work. Three
server runs go at once (`--server-jobs`, every server model together): one run sends about a million tokens a
minute, cached prompt included, and fifteen at once crossed OpenAI's tokens-per-minute limit within a minute. Each
server model of a batch spends from its own cap, `--max-usd` (12 dollars by default), checked before each model
call, like the server's own monthly budget: the batch can pass it by one call per run going at once, and a run it
stops counts as an error. A release batch of `gpt-6.1-sol` (15 runs) came to $10.70; raise the cap with
`EVAL_ARGS="--max-usd 15"` if one gets cut.

### A client matrix

The analysis runs in the player's own AI: the server makes no model call of its own except the stale-dashboard
refresh. So the question is whether what MCP hands a client is enough for the clients people use, not only which
model a server should run. `--client kind:model` gives each model a column of its own, in one batch:

```sh
make eval-analysis EVAL_ARGS="--client claude:claude-sonnet-5-5 --client claude:claude-haiku-5-5 \
  --client server:gpt-6.1-terra --client server:gpt-6-luna"
```

A server model on another endpoint gets its own batch, with that endpoint's `EVAL_SERVER_BASE_URL` and key
command (z.ai's `glm-5.3`; a local model on oMLX, Ollama or LM Studio, priced at 0). A local model on one GPU runs
one request at a time: `--jobs 1 --server-jobs 1`.

### Repeats

A model doesn't answer the same way twice, so one run per scenario says little. `--repeats N` runs each scenario N
times per client, and a scenario passes when at least two thirds of its runs pass (2 of 3); below that, the table
flags it. The batch's `release.md` gives the pass rate per client and scenario.

The evaluation is an optional tool, not a release gate (owner, 2026-11-22): the server hands every client good
data, and a model that doesn't act on it is accepted. A scenario below two thirds on most clients of a matrix (more
than half, three clients at least) is still worth a look: it points at the brief or at what the MCP tools return,
not at one model, and `release.md` flags it in bold.

A scenario is a whole analysis: about 25 to 40 questions, a dozen lanes, a few hundred tool calls, 7 to 13
minutes with Claude Code. Side by side, the batch takes about as long as its slowest scenario. `--timeout-min`
(20 by default, per scenario) stops a stuck one; a 10-minute cap cut two normal runs.

## What it does

Every scenario runs at once by default (`--jobs`), each in a process of its own with its own server, client and
folder: a run mostly waits on the model, and its local load is one small server on synthetic data. For each
scenario, `tools/eval_analysis/run.py`:

1. builds the history: the docs site's demo character (Nibbet-Gearholt, levels 22 to 46 over thirty-three evenings,
   `tools/fixtures/generate.py --demo`) with one effect injected (`scenarios.py`);
2. starts wow-stats' own MCP server over stdio on those events, with a throwaway state folder and nothing else:
   no VictoriaLogs (its address is a closed local port), no provider settings (`server.py`);
3. runs the client with the analysis tools only, the `analyse` prompt and the player's goals, told that nobody
   will answer. A conversation that stops early is followed by a new one (`analysis_progress` picks the run up),
   six at most. The run counts as finished when the server says so: the synthesis accepted;
4. grades what the analysis kept (`grade.py`).

## The scenarios

| Scenario | What's injected | The right answer |
|---|---|---|
| `death_spike` | extra deaths in the Wetlands, until its rate is twice the rest's or more (8 deaths in 11 hours against 0.32 an hour elsewhere) | ties the deaths to the Wetlands |
| `recorder_off` | the Recorder off for four evenings (levels 26 to 30); level-ups, snapshots and gear sheets still arrive | says the records are missing, never "you died less" or "you took a break" |
| `offline_gap` | a 21-day break in the middle of level 33; the played time is the demo's (on the guide's pace) | names the break, never calls level 33 slow |
| `wrong_spec_gear` | gear scored for Holy (`WEIGHTS`), and the character just put on the Filigreed Shadow Circlet, a Shadow item worth next to nothing for Holy | a kept finding names the circlet (or the head slot) and Holy; points the head slot at a Holy-scored upgrade, never tells the player to get or keep a Shadow head item |
| `healthy_control` | nothing | no death hotspot, no missing records: no pattern established |

The demo isn't a perfect player: level 32 took about four times the guide's plan, and the gear advice has real
upgrades. A line about those is fine in every scenario, the control included. What the control rules out is a
pattern the data doesn't hold: a dozen deaths spread over 36 hours make no hotspot.

Each scenario is a JSON file in `tools/eval_analysis/scenarios/`. The tests (`tests/test_eval_analysis.py`)
check that each injection did what its file says, measured with the product's own prep: the Wetlands spike is
significant (one-sided Poisson test, p < 0.05: the injection adds deaths until it is) and no zone in the control is, level 33 is on pace in played
time, levels 27 to 29 have played time and no records, the three head items the truth names are the gear
advice's best for Holy.

## How it grades

Deterministic checks, in order:

- **finished**: the synthesis was accepted. If not, the verdict is `error`: the client or the harness stopped,
  and nothing else is graded.
- **queries**: every kept finding's saved query runs again on a fresh build of the scenario's database, outside
  the server, and gives the values the finding was judged on.
- **must_find**: each expected line is there. A rule is groups of regular expressions; a kept finding, nudge or
  pattern matches when each group has a match in it. `"in": "actions"` reads only what tells the player to do
  something; `"kinds": ["finding"]` only the findings the judge kept (a nudge has no query behind it).
- **must_not_claim**: no kept line says what the truth rules out. `unless` excuses a line that says why (the
  records are missing, there was a break).

Words are a blunt check on purpose: the same answer grades the same way every time, and a miss quotes the exact
line. `--judge` asks Claude Code about the wording too; its note goes in the report and never changes the verdict.

A failed check is one of two things. When the kept lines say something wrong, it's a finding about the analysis:
fix the analysis, never loosen the rule until it passes. When the rule missed a right answer worded another way,
widen the rule, and add that wording to `test_the_shipped_truths_grade_a_right_and_a_wrong_answer`. `run.py --regrade eval-analysis-out/<time>` grades a finished batch again with the new rules, no model needed.

## The output

Everything lands in `eval-analysis-out/<time>/` (ignored by git; the data is synthetic), one folder per run under
`<client>/<scenario>/r<N>/`: the
events, the server's settings and state, `transcript.jsonl` (what the client did), `report.md` and `report.json`
(verdict, every check with its evidence, every kept line, the client, its model, tokens, minutes, and the
facts measured on the injected history), then `summary.md` for the batch (per run the minutes, the tool calls, the
pass it reached and the server's model cost) and `release.md`, the pass rates. A run that hits the cap still reports its calls per tool: one tool called over and over
is a loop.

## Adding a scenario

Write `scenarios/<name>.json`: `about`, `goals`, `inject` (`death_spike`, `recorder_off`, `offline_gap`,
`wear`, or a new kind added to `scenarios.INJECT`), `env` for server settings such as `WEIGHTS`, and the
`truth`: `expected` in words, then the rules. Then a test that measures the injection with `scenarios.facts()`,
and a right and a wrong answer in the shipped-truths test.

## Why not in CI

A run needs a model client, and hosted CI has none. The scenario loader and the grader are tested in CI; the
runs themselves are for a contributor's machine, when a change to how the analysis reasons is worth a check. A
server-runner batch also spends real money on the server's model: never in `ci.yml`, never per pull request.
