# Server-side analysis

**For:** self-hosters whose friends never ask their own AI for an analysis. **At the end:** the
server writes "How you play" itself, with the AI provider you pick, inside a monthly budget.

Optional, and off until you give it a provider. Without it, "How you play" changes only when a
player runs the analysis from their own AI client, as before.

## What it does

The full view plays the MCP client itself: the same analysis tools, the same `analyse` prompt,
the same judge re-running every finding's SQL before it's kept. What it keeps looks like a
client's run, marked `kind: "server"` with the model's name.

- **When:** once a player has played 10 hours (`ANALYSIS_STALE_PLAYED_HOURS`) since their
  analysis was last refreshed. Play time is the journal's sessions, every character. The check
  runs after a page rebuild, so a new upload triggers it; the run itself goes on in the
  background, one player at a time, and never holds up the page.
- **What:** the characters played since the last refresh, the most recent first, three at most.
- **The timer resets** whenever the player's analysis is refreshed, by anyone: this server's
  run, the player's own AI calling `dashboard_refresh`, or a full analysis from their own client.
- **`dashboard_refresh`** (an MCP tool) runs it now for the caller, whatever the timer says,
  budget permitting. It answers with the last refresh and who did it, the play since, what it
  started or skipped and why, how the last try ended, and the budget left.
- **Only the analysis tools run** (no plans, no Battle.net data), on the player's own data, with
  `char` forced to the character being analysed. Tool results go back as data.
- **/admin** shows the month's spend and, per person, their play since the last refresh and
  who did it ([Admin overview](admin.md)).

The run happens in the **full-view process** (`wow-stats ui` with `VIEW=full`): give that
process the credentials and egress to the provider's API, nothing else.

## Pick a provider and a way in

`ANALYSIS_PROVIDER` is `anthropic` (the default) or `openai`, which means any OpenAI-compatible
Chat Completions endpoint: OpenAI, OpenRouter, Ollama, vLLM, LM Studio. The model has to support
tool calling: one that refuses tools fails the run with that reason on /admin and in
`dashboard_refresh`. It never falls back to a run without tools.

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
  Without both, tokens are counted, /admin says "cost unknown", and no money cap applies.
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
calling>`, plus the two prices if you want the money cap.

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
game showed them, never Battle.net data. The in-app Privacy page says so, and says it's off when
you haven't configured a provider. Tell your friends before you turn it on.
