#!/usr/bin/env bash
# Writes a fresh GitHub ID token for Anthropic's workload identity
# federation to $RUNNER_TEMP/anthropic-oidc. GitHub's ID tokens live about
# five minutes and the SDK re-reads the file at every exchange (the access
# token it gets back lives ten): a token minted at the top of the job was
# refused (401) by the community step 6 minutes later (2026-11-23). Run the
# model through tools/kb/with-oidc.sh, which mints before the command and
# again every few minutes while it runs; never call this by itself.
# The file is replaced atomically, so a read never sees half a token.
set -eu
set -o pipefail
# The ID-token endpoint answers 504 now and then (2026-11-23): without pipefail
# the failed curl fed jq nothing, an empty token file was written and the
# community step died with "Identity token file is empty". Retry, and fail here
# if no token came back.
token=""
for attempt in 1 2 3 4 5; do
  token=$(curl -sSf -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
    "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=https://api.anthropic.com" | jq -r '.value // empty') || token=""
  [ -n "$token" ] && break
  echo "oidc-mint: attempt $attempt got no token" >&2
  sleep $((attempt * 5))
done
[ -n "$token" ] || { echo "oidc-mint: no ID token after 5 attempts" >&2; exit 1; }
echo "::add-mask::$token"
umask 077
printf '%s' "$token" > "$RUNNER_TEMP/anthropic-oidc.new"
mv "$RUNNER_TEMP/anthropic-oidc.new" "$RUNNER_TEMP/anthropic-oidc"
