#!/usr/bin/env bash
# Writes a fresh GitHub ID token for Anthropic's workload identity
# federation to $RUNNER_TEMP/anthropic-oidc. GitHub's ID tokens live about
# five minutes, so every step that talks to Anthropic mints its own right
# before it starts: one token minted at the top of the job was refused (401)
# by the community step 6 minutes later. A single step may run longer, as
# the SDK exchanges the token once and caches the access token it gets back.
set -eu
token=$(curl -sSf -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
  "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=https://api.anthropic.com" | jq -r .value)
echo "::add-mask::$token"
umask 077
printf '%s' "$token" > "$RUNNER_TEMP/anthropic-oidc"
