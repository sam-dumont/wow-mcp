"""`wow-stats <mode>`: one entry point for the image and for `uv run`."""

import argparse
import json
import sys

from wowstats import __version__


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="wow-stats", description="WoW Forever leveling log")
    ap.add_argument("--version", action="version", version=f"wow-stats {__version__}")
    sub = ap.add_subparsers(dest="mode", required=True)
    sub.add_parser("data-status", help="print this install's app version, data sources and freshness as JSON")
    sub.add_parser("ui", help="the web UI, its JSON API and the optional ingest endpoint (env: see README)")
    sub.add_parser("exporter", help="the Battle.net API exporter, pushing to the 30-day VictoriaMetrics")
    pub = sub.add_parser("publish", help="write the static sites (public, and the sealed full content)")
    pub.add_argument("--loop", action="store_true", help="every PUBLISH_INTERVAL seconds, when events arrived")
    pub.add_argument("--force", action="store_true", help="rebuild even without new events")
    sub.add_parser("static", help="serve what publish wrote: files only, no backend")
    sub.add_parser("mcp-stdio", help="the MCP tools over stdin/stdout, for a client on this machine that starts "
                   "this command (no port, no login; docs/mcp.md)")
    sub.add_parser("render", help="draw past levels' pictures for publish (the renderer image only: "
                   "Playwright's Chromium)")
    zk = sub.add_parser("zogzog-key", help="the full-content link's key")
    zk.add_argument("action", choices=["rotate", "link"],
                    help="rotate: a new key (old links stop working) and a rebuild; link: print the link")
    sub.add_parser("analyse", help="the nightly recheck of the play analysis' kept findings, once; "
                   "`wow-stats analyse -h` for its options")
    sub.add_parser("users", help="the people of a shared install (list, hosts, disable, enable, delete): "
                   "`wow-stats users -h`")
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["users"]:
        from wowstats import tenancy

        return tenancy.cli(args[1:])
    if args[:1] == ["analyse"]:
        from wowstats import analysis

        return analysis.main(args[1:])
    a = ap.parse_args(argv)
    if a.mode == "data-status":
        from wowstats import dataversions

        print(json.dumps(dataversions.status(), indent=2))
        return 0
    if a.mode == "publish":
        from wowstats import publish

        return publish.main((["--loop"] if a.loop else []) + (["--force"] if a.force else []))
    if a.mode == "static":
        from wowstats import static

        return static.main()
    if a.mode == "render":
        from wowstats import renderer

        return renderer.main()
    if a.mode == "zogzog-key":
        from wowstats import publish

        return publish.rotate() if a.action == "rotate" else publish.link()
    if a.mode == "mcp-stdio":
        from wowstats import mcp

        return mcp.stdio()
    if a.mode == "ui":
        from wowstats import ui

        return ui.main()
    from wowstats import exporter

    return exporter.main()
