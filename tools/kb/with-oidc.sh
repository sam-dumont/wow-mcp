#!/usr/bin/env bash
# Runs a command that talks to Anthropic with a GitHub ID token that is
# always fresh: minted before it starts (tools/kb/oidc-mint.sh) and again
# every REFRESH seconds while it runs. The ID token lives about five minutes
# and the SDK re-reads the file whenever its ten-minute access token needs
# replacing, so one mint before a long step is not enough (2026-11-23: the
# community step got a 401 six minutes after the job's one mint). Without
# ANTHROPIC_IDENTITY_TOKEN_FILE (the federation isn't set up) it just runs.
set -eu
if [ -z "${ANTHROPIC_IDENTITY_TOKEN_FILE:-}" ]; then
  exec "$@"
fi
here=$(dirname "$0")
"$here/oidc-mint.sh"
(
  while sleep "${OIDC_REFRESH_SECONDS:-180}"; do
    # A failed refresh keeps the old file; the next try is in REFRESH seconds.
    "$here/oidc-mint.sh" || echo "with-oidc: refresh failed, keeping the old token" >&2
  done
) &
refresher=$!
trap 'kill "$refresher" 2>/dev/null || true' EXIT
status=0
"$@" || status=$?
exit "$status"
