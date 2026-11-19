Files for the optional parts, one value each, never committed. The `ui`
service sees this folder as `/run/wow-secrets` (read-only).

- `oidc_client_secret`: your identity provider app's client secret, for the
  MCP login (docs/mcp.md).
- `mcp_signing_key`: `head -c 48 /dev/urandom > secrets/mcp_signing_key`.
  Without it every restart logs your Claude connectors out.

For the exporter profile:

- `bnet_client_id`, `bnet_client_secret`: your own Battle.net API client.
- `vm_merge_key`: any random string, for example
  `head -c 24 /dev/urandom | base64 > secrets/vm_merge_key`.

The containers run as uid 10001: make the files readable for it
(`chmod 644`, the folder itself stays yours).
