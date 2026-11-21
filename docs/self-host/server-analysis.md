# Server-side analysis

**For:** self-hosters whose friends never ask their own AI for an analysis. **At the end:** the
server writes "How you play" itself, with the AI provider you pick, inside a monthly budget.

Optional, and off until you give it a provider. Without it, "How you play" changes only when a
player runs the analysis from their own AI client, as before. `ANALYSIS_AUTO=off` keeps the
provider configured (/admin still shows it) but the server never starts a run.

Experimental. Real runs so far went through the OpenAI-compatible path (OpenAI's gpt-6 models,
z.ai's glm-5.3). Anthropic's path is covered by the test suite; its costs below are estimates. Ollama,
vLLM and LM Studio speak the same API but nobody has tried them yet.

## The server vs the client

| | Who does the analysis | Who pays |
|---|---|---|
| A player's own AI (`analyse`, or `dashboard_refresh`) | their model, through the MCP tools | them (their own AI subscription) |
| The server's automatic refresh (this page) | the provider you configure, after 10 hours of play (`ANALYSIS_STALE_PLAYED_HOURS`) | you (`ANALYSIS_MONTHLY_BUDGET`) |

A player who refreshes from their own AI never costs you anything. The server's runs are there
for the friends who never do: the timer only starts one once a character has 10 hours of play
since anyone last refreshed it. No MCP call starts a server run, `dashboard_refresh` included,
and /admin has no "run now" button either: the timer is the only way in.

## What the server does

The full view plays the MCP client itself: the same analysis tools, the same `analyse` prompt,
the same judge re-running every finding's SQL before it's kept. What it keeps looks like a
client's run, marked `kind: "server"` with the model's name. The page says who wrote it under
"How you play": "As of Thu 19 Nov, by the server.", or "by you." after the player's own AI.

- **When:** once a character has been played 10 hours (`ANALYSIS_STALE_PLAYED_HOURS`) since its
  analysis was last refreshed. Each character has its own timer. Play time is the journal's
  sessions. A character that was never refreshed counts all its play on record, so the alt you
  logged into once to reserve a name sits at 10 minutes and never costs a run; a main with 40
  hours behind it is due on the first check. The check runs after a page rebuild, so a new
  upload triggers it; the run itself goes on in the background, one player at a time, and never
  holds up the page.
- **What:** that player's due characters, the most recently played first, three at most. When
  their last try stopped (a cap, an error, an incomplete run), their timer waits 6 hours before
  trying again.
- **A character's timer resets** whenever its analysis is refreshed, by anyone: this server's
  run, or a full analysis from the player's own AI (its synthesis accepted by
  `analysis_submit`). It counts from that refresh, not from the character's first login.
- **Only the analysis tools run** (no plans, no Battle.net data), on the player's own data, with
  `char` forced to the character being analysed. Tool results go back as data.
- **It runs unattended** and finishes in one go: the run is done when its synthesis is accepted,
  not when the model stops talking. A model that stops to ask something (the `analyse` prompt
  tells a person's AI to offer "continue" in a new chat) is told nobody will reply and to finish,
  twice in a row at most; then the run is **incomplete**. A run that doesn't finish, for that or
  any other reason (budget, a provider error), changes nothing the player sees: their last
  finished analysis stays. One exception: when the player's own AI, the nightly recheck (its
  own process) or a `wow-stats analyse` by hand wrote to the same character while the run was
  going, nothing is put back, so their work isn't erased, and the run's reason ends with "not
  rolled back". The server's partial findings then stay until the next full analysis. Every
  writer takes a lock on the character's folder and moves a revision kept there, so this holds
  across processes on one machine (one state directory, not shared over NFS).
- **/admin** shows the month's spend for the whole install and, per person, each character's
  play since its last refresh against the threshold ("never refreshed" when it has none), the
  latest refresh and who did it, and their own server runs and cost this month
  ([Admin overview](admin.md)). The spend file splits the month per tenant number.

## What the client does

`dashboard_refresh` (an MCP tool) refreshes the caller's dashboard **on the client**: their own
model runs the analysis, the server holds the run and judges each finding. It starts nothing on
the server and reaches no provider. It picks the character whose analysis is most out of date
(due first, then the most play since its refresh; `char` picks another) and returns what
`analyse` returns for it: the procedure (`instructions`), where the run stands (`progress`) and
`do`, the one line to follow: analysis_brief, the passes, then analysis_submit, whose accepted
synthesis refreshes "How you play" and resets that character's timer. `then` lists the caller's
other characters with play since their refresh.

It also shows the server's side under `server_automatic_refresh`, labelled as the automatic
refreshes only: each character's play against the threshold (`timer.chars`: "0 h 10 of play on
record, never refreshed, due at 10 h 00"), the latest refresh and who did it, whether an
automatic refresh of theirs runs or waits (`state`, `position`), how the last one ended, the
install's budget (`budget`, `scope: "install"`) and the caller's own share (`yours`). While the
server's run for them is going, `do` says to let it end first: both would share one run state.

## Where it runs

The run happens in the **full-view process** (`wow-stats ui` with `VIEW=full`): give that
process the credentials and egress to the provider's API, nothing else.

## Pick a provider and a way in

`ANALYSIS_PROVIDER` is `anthropic` (the default) or `openai`, which means any OpenAI-compatible
Chat Completions endpoint: OpenAI, OpenRouter, Ollama, vLLM, LM Studio, z.ai. OpenAI's own API
(`ANALYSIS_BASE_URL` unset, or pointing at `api.openai.com`) goes through the Responses API
instead, because its reasoning models only take tools there; you don't set anything for that. The model has to support tool calling: one that refuses tools fails the run
with that reason on /admin and in `dashboard_refresh` (`last_try`). It never falls back to a run without
tools. Any other failure shows what the provider said.

Credentials, first match wins: workload identity federation, then `ANALYSIS_API_KEY_FILE`, then
(OpenAI-compatible only) no key. Each is handed to the SDK explicitly: a stray
`ANTHROPIC_API_KEY` or `OPENAI_API_KEY` in the environment is never read and can't override
federation.

| Provider | Auth | Set |
|---|---|---|
| `anthropic` | federation (Kubernetes) | `ANALYSIS_WIF_TOKEN_FILE`, `ANALYSIS_WIF_RULE_ID`, `ANALYSIS_WIF_ORGANIZATION_ID`; optional `ANALYSIS_WIF_SERVICE_ACCOUNT_ID`, `ANALYSIS_WIF_WORKSPACE_ID` |
| `anthropic` | key file | `ANALYSIS_API_KEY_FILE` |
| `openai` | federation (Kubernetes) | `ANALYSIS_MODEL`, `ANALYSIS_WIF_TOKEN_FILE`, `ANALYSIS_WIF_PROVIDER_ID`, `ANALYSIS_WIF_SERVICE_ACCOUNT_ID` |
| `openai` | key file | `ANALYSIS_MODEL`, `ANALYSIS_API_KEY_FILE`; `ANALYSIS_BASE_URL` for anything but OpenAI |
| `openai` | none (local server) | `ANALYSIS_MODEL`, `ANALYSIS_BASE_URL` |

Install the matching extra: `analysis` (Anthropic's SDK) or `analysis-openai` (OpenAI's). The
container image has both.

## Money

`ANALYSIS_MONTHLY_BUDGET` is in **US dollars**, what the APIs bill in, per calendar month (UTC).
Default 10. Every response's token usage is priced and added up on the state volume
(`WOW_STATE_DIR`, else `ANALYSIS_STATE_DIR`). A run that would likely cross the budget doesn't
start, and a run stops before a call that would cross it.

- **Anthropic:** prices are built in, from
  [Anthropic's pricing page](https://platform.claude.com/docs/en/about-claude/pricing), cache
  reads and writes included.
- **OpenAI-compatible:** endpoints charge what they like, so set
  `ANALYSIS_PRICE_INPUT_PER_MTOK` and `ANALYSIS_PRICE_OUTPUT_PER_MTOK` (USD per million tokens).
  Without both, tokens are counted, /admin says "cost unknown", and no money cap applies. Set
  `ANALYSIS_PRICE_CACHED_INPUT_PER_MTOK` too: most of a run's prompt is cached, which OpenAI
  bills at a tenth of the input price or less, and without it the meter counts several times
  what you're billed.
- **`ANALYSIS_MONTHLY_TOKENS`** caps tokens instead, or as well: the cap for an endpoint with no
  prices, a local one included if you care about its GPU time.

What one run costs, estimated without a billed call (`uv run --extra analysis python
tools/analysis_cost.py`: the first request and the brief measured on the synthetic test
character, the rest a model of a run's ~94 calls with prompt caching):

| Model | One run | Runs for $50 |
|---|---|---|
| `claude-opus-5-5` (default) | about $3.10 | about 15 |
| `claude-sonnet-5-5` | about $1.60 | about 30 |
| `claude-haiku-5-5` | about $0.20 | about 270 |

A real character with months of play has a bigger database and more to ask about: count on the
top of that range until /admin shows your own numbers. The server uses its own average per
finished run for the budget check once it has one.

## Examples

### Docker with an API key

```bash
mkdir -p secrets && printf '%s' 'sk-ant-...' > secrets/analysis-key && chmod 600 secrets/analysis-key
```

Then in the full view's service (`deploy/compose/compose.yaml` already mounts `./secrets` at
`/run/wow-secrets`):

```yaml
      ANALYSIS_API_KEY_FILE: /run/wow-secrets/analysis-key
      ANALYSIS_MONTHLY_BUDGET: "20"
      # ANALYSIS_MODEL: claude-sonnet-5-5   # half the price of the default
```

OpenAI instead: add `ANALYSIS_PROVIDER: openai` and `ANALYSIS_MODEL: <a model with tool
calling>`, plus the three prices (input, cached input, output) if you want the money cap.

### Ollama on the same host

```yaml
      ANALYSIS_PROVIDER: openai
      ANALYSIS_BASE_URL: http://ollama:11434/v1
      ANALYSIS_MODEL: <a model that supports tools>
      ANALYSIS_MONTHLY_TOKENS: "50000000"
```

No key. A small model tends to hand in findings the judge refuses: the run then keeps less, it
doesn't keep wrong numbers.

### Kubernetes with workload identity federation

No key anywhere: the kubelet projects a short-lived service account token into the full view's
pod, and the server exchanges it for an access token. The SDK rereads the file at each exchange,
so rotation needs nothing.

Give the full view its own ServiceAccount and mount the token with the audience your provider
expects:

```yaml
spec:
  serviceAccountName: wow-ui-full
  containers:
    - name: ui
      env:
        - {name: ANALYSIS_WIF_TOKEN_FILE, value: /var/run/secrets/ai-token/token}
      volumeMounts:
        - {name: ai-token, mountPath: /var/run/secrets/ai-token, readOnly: true}
  volumes:
    - name: ai-token
      projected:
        sources:
          - serviceAccountToken:
              path: token
              audience: https://api.anthropic.com   # OpenAI: https://api.openai.com/v1
              expirationSeconds: 3600
```

The server checks the token's `aud` before it calls anyone, so a wrong volume says so on /admin
instead of an opaque 401. If you configured the provider with another audience, set
`ANALYSIS_WIF_AUDIENCE` to it.

**Anthropic** (Claude Console, Workload identity): a service account; a federation issuer with
your cluster's issuer URL (`kubectl get --raw /.well-known/openid-configuration | jq -r
.issuer`); a federation rule matching `sub` = `system:serviceaccount:<namespace>:wow-ui-full`
and audience `https://api.anthropic.com`. Then set `ANALYSIS_WIF_RULE_ID`,
`ANALYSIS_WIF_ORGANIZATION_ID` and, if the rule doesn't name one, `ANALYSIS_WIF_SERVICE_ACCOUNT_ID`.

**OpenAI** (Organization settings, Security, Workload Identity Provider): a provider with your
cluster's issuer URL and audience `https://api.openai.com/v1`; a service account mapping with
key `sub`, value `system:serviceaccount:<namespace>:wow-ui-full`, to a project service account
with only model access. Then set `ANALYSIS_PROVIDER=openai`, `ANALYSIS_MODEL`,
`ANALYSIS_WIF_PROVIDER_ID` and `ANALYSIS_WIF_SERVICE_ACCOUNT_ID`.

**A cluster whose issuer isn't on the internet** (most self-managed ones, k3s and RKE2
included) works with both: neither needs to reach it. Upload the cluster's keys instead,
`kubectl get --raw /openid/v1/jwks`: Anthropic's issuer in `inline` JWKS mode (the `keys`
array), OpenAI's provider with "Use uploaded JWKS for token verification" (the whole object;
OpenAI supports only that mode for self-hosted clusters). Both still compare the issuer URL with
the token's `iss`. When the cluster rotates its signing key (rare, usually an upgrade), upload
the new keys before tokens signed with it arrive, or every exchange fails.

Allow egress from the full view's pod to `api.anthropic.com` or `api.openai.com` on 443.

## Privacy

What the analysis tools answer about a player's characters goes to the provider you configured
when the server refreshes their analysis: their events and the names of other players their
game showed them, never Battle.net data. The in-app Privacy page says so, with your threshold.
When no provider is configured, or `ANALYSIS_AUTO=off`, it says the server sends nothing. Tell
your friends before you turn it on.
