# Security policy

## Reporting a vulnerability

Report it privately: [open a draft security advisory](https://github.com/sam-dumont/wow-mcp/security/advisories/new)
(the **Report a vulnerability** button under the repository's Security tab).
Please don't open a public issue, pull request or discussion for it.

Include what you can of:

- the version (`wow-stats --version`, or the image tag and digest);
- how it's deployed (the compose kit, Kubernetes, `uv run`) and which views,
  `AUTH` mode and optional parts (MCP, several people, the renderer) are on;
- the steps to reproduce, and what an attacker gets out of it.

Leave out real keys, share links (`/zogzog/#...`), tokens, passwords and
character or account names: a made-up value shows the bug just as well.

This is a one-maintainer project. I aim to acknowledge a report within 7
days and to agree on a disclosure date with you once the fix is understood.
Credit goes in the advisory and the CHANGELOG, unless you'd rather not.

## Supported versions

Only the latest release gets security fixes. There are no backports: the
fix ships in a new release, and the advisory says which one.

| Version | Supported |
|---|---|
| latest release | yes |
| anything older | no: upgrade first (the CHANGELOG's "Upgrading" sections list what changes) |

## In scope

The server (`wow-stats ui`, the MCP endpoint and its OAuth login, `publish`
and the sealed full-content site, `static`, `exporter`, `render`), the
shipper and its one-line installers, the container images and the
release workflow.

Out of scope: the game, Blizzard's APIs, Wowhead, and the third-party
addons the shipper reads (report those upstream), and a full view run with
`AUTH=open` on anything but 127.0.0.1: [docs/deploy.md](docs/deploy.md)
keeps `open` to the public view and loopback.

## Verifying what you run

Release images carry build provenance and an SBOM. How to check them:
[docs/development.md](docs/development.md#verifying-an-image).
