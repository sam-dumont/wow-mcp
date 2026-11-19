# Run it on a server

**For:** self-hosters moving from the demo to a server other people (or your own phone, or
your AI) reach over the internet. **At the end:** you know the pieces, in the order to set
them up, and which page covers each.

The compose kit runs everything on one machine: a small Linux VM (2 vCPU, 2 GiB of RAM is a
fine start: [Sizing](sizing.md)) at home or anywhere. Start from a working
[install](../install.md), then add what you need, in this order:

| Step | Why | Page |
|---|---|---|
| 1. HTTPS and two host names | Friends reach the static sites, you reach your page and the shipper reaches `/ingest` from anywhere, encrypted | [Put it behind a reverse proxy](reverse-proxy.md) |
| 2. A login for your page | The full view shows everything, other players included | [Put it behind a reverse proxy](reverse-proxy.md) |
| 3. Share your page | A public site for anyone, a sealed one for friends with the link | [Share your page](sharing.md) |
| 4. Your AI from anywhere | claude.ai and ChatGPT call your server from theirs: they need HTTPS and an OAuth login | [Set up the login for your AI](oidc.md) |
| 5. Friends with their own pages | Several players, each with their own data, link and AI | [Add friends](friends.md) |
| 6. Battle.net data (optional) | The WoW Token, a small auction house watch list, your look in 3D | [Battle.net data](battlenet.md) |

Then keep it running:

- [Back up and restore](backup.md): three volumes and two folders.
- [Upgrade](upgrade.md): between versions, and what each release changed.
- [Monitor it](monitoring.md): health checks, logs, disk.
- [Harden it](hardening.md): what's locked down already, and what's on you.
- [Uninstall](uninstall.md): the server, the player's computer, and the data, separately.

Not using Docker Compose (Kubernetes, Nomad, plain containers)? The same image runs every
part: [Deploy without compose](../deploy.md) has the commands, ports and mounts, and
[Environment variables](../reference/environment.md) every setting.
