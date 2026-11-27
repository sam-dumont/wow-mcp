"""Nothing private in the repository: every tracked file (data/ too, and the
members of .gz and .zip files) is read line by line.

1. The denylist, kept out of git: one case-insensitive literal per line, `#`
   starts a comment, in ~/.config/wow-stats-public/denylist.txt or the file
   WOW_STATS_DENYLIST names. Real names, accounts, domains, hosts, addresses:
   whatever must never be published. A literal matches as a whole word (not
   inside a longer one, so a real name doesn't trip on a game item that
   contains it). A hit names the file, the line and the entry's number,
   never the entry. Skipped where the file is absent (CI, a fresh clone).

2. Shapes that are private whatever they say, always run: email addresses,
   URLs and host names outside the reserved example domains and the public
   upstreams below, private IPv4 addresses, `"site":` labels in fixtures,
   and share-link fragments (`#` + 43 base64url characters, a zogzog key).

The allowlists are explicit: a new upstream goes in with a comment saying
what uses it."""

from __future__ import annotations

import gzip
import io
import ipaddress
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DENYLIST = Path(os.environ.get("WOW_STATS_DENYLIST") or Path.home() / ".config/wow-stats-public/denylist.txt")

# Lab-only files: tracked, but the public export (tools/export-public.sh)
# leaves them out.
PRIVATE_DIRS = ("docs/private/", "tools/export-public.sh")
# The release's own identity (wowstats.SOURCE_URL, the image names): the
# public export rewrites these to the public repository and registry, so in
# the private repo the denylist looks past them. WOW_STATS_SCAN_STRICT=1
# (the export's own check) turns that off: there, any left is a miss.
RELEASE_IDS = re.compile(r"(?:github\.com|ghcr\.io)/sam-dumont/wow-mcp(?:-render)?\b", re.I)
STRICT = os.environ.get("WOW_STATS_SCAN_STRICT", "").strip() == "1"
# This file: its private networks and its own tests' examples are the shapes.
SELF = "tests/test_public_scan.py"

# Binary files with nothing to read as text.
BINARY = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".woff", ".woff2", ".ttf", ".otf", ".mp4", ".webm",
          ".glb", ".blp", ".m2", ".skin", ".pdf"}

# --------------------------------------------------------------- allowlists

# Reserved names (RFC 2606, RFC 6761) and names that only resolve inside a
# machine or a cluster: never someone's site.
RESERVED_SUFFIXES = (".example", ".invalid", ".test", ".localhost", ".local", ".local.", ".internal")
RESERVED_HOSTS = {"example.org", "example.com", "example.net", "localhost"}

UPSTREAMS = {
    # The game, its maker and its data: links, item renders, the API.
    "blizzard.com", "battle.net", "worldofwarcraft.com", "blizzard.net", "blizzplanet.com", "zamimg.com",
    "wowhead.com", "allakhazam.com", "wago.tools", "wago.io", "foreverdb.net", "classicdb.ch",
    "polestar.guide", "curseforge.com", "warcraft.wiki.gg", "wowforever.ninja",
    # ForeverDB’s upstream NOTICE credits the public Everloot corpus for community drop counts.
    "everloot.app",
    # Code and packages: sources, licences, the tools the docs install.
    "github.com", "githubusercontent.com", "pypi.org", "pythonhosted.org", "npmjs.org", "astral.sh",
    "gnu.org", "fsf.org", "creativecommons.org", "sil.org", "purl.org", "w3.org", "openjsf.org", "jquery.com",
    "opencollective.com", "tidelift.com", "patreon.com", "git-scm.com", "docker.com", "debian.org",
    "renovatebot.com", "dotenvx.com", "gofastmcp.com", "remotion.dev", "plausible.io",
    # THIRD_PARTY.md: the Ed25519 code the shipper's update check comes from (RFC 8032).
    "rfc-editor.org",
    # CODE_OF_CONDUCT.md adopts the Contributor Covenant by link.
    "contributor-covenant.org",
    # docs/development.md: the SBOM attestation's predicate type (an SPDX URI).
    "spdx.dev",
    # Funding links of npm packages in site/package-lock.json (the docs site).
    "paulmillr.com", "locize.com", "i18next.com",
    # What the manual sets up next to it: an identity provider, a DNS-over-HTTPS resolver.
    "keycloak.org", "cloudflare-dns.com",
    # docs/self-host/how-i-run-it.md: the identity provider of the worked example.
    "zitadel.com",
    # The public edition's own site: the manual and the live demo.
    "sam-dumont.github.io",
    # The MCP clients the OAuth redirect list and the docs name.
    "claude.ai", "claude.com", "anthropic.com", "chatgpt.com", "vscode.dev", "cursor.com", "apple.com",
    # The AI providers the server-side analysis can call (docs/self-host/server-analysis.md).
    "openai.com",
    # Sources the KB cites, fact by fact (data/kb/research): public guides,
    # forums, news and wikis about the game.
    "warcrafttavern.com", "method.gg", "conquestcapped.com", "kami-labs.fr", "wow-professions.com",
    "mein-mmo.de", "rankedboost.com", "wow.gg", "lfcarry.com", "allthings.how", "expcarry.com", "timesaver.gg",
    "wowforevertierlist.com", "wowtbc.gg", "skill-capped.com", "metabot.gg", "blizzardwatch.com",
    "gfinityesports.com", "zockify.com", "icy-veins.com", "leprestore.com", "mmonster.co", "magicgameworld.com",
    "gamerant.com", "iggm.com", "tentonhammer.com", "forg.gg", "zygorguides.com", "overgear.com",
    "devtrackers.gg", "boostroom.com", "wowforevertalents.com", "wowisclassic.com", "pcgames.de", "engadget.com",
    "mobalytics.gg", "kotaku.com", "dotesports.com", "reddit.com", "ezg.com", "seemeta.com", "efficient.games",
    "judgehype.com",  # corroboration only (data/kb/sources.json)
    "forever.chronicleclassic.com",  # Public combat reports cited by the rogue guide.
    # Public sightings and quest references cited by the item-by-item acquisition audits.
    "foreverchanges.pro", "questing.wiki", "azeroth.wiki",
    # Gear tables the fit model is checked against (tests/data/gearfit-refs.json:
    # item IDs, ranks and the page each came from).
    "wowforeverbislist.com", "noobtoboss.com",
    # README.md, "Similar projects": a hosted service credited there.
    "wowcoach.gg",
}
# Hosts that stand for an attacker in the security tests (open redirects,
# lookalike origins): never contacted.
TEST_VECTORS = {"evil.com"}

# Emails: the reserved domains, no-reply senders, and public upstream
# addresses the product or THIRD_PARTY.md quotes (wow.export's MIT notice
# names its two authors by address, and the notice is kept as written).
EMAIL_OK = re.compile(r"(^noreply@|^no-reply@|@(?:[a-z0-9-]+\.)*(?:example\.(?:org|com|net)|example|invalid|test)$"
                      r"|@render\.worldofwarcraft\.com$|@users\.noreply\.github\.com$|@fsf\.org$|@gnu\.org$"
                      r"|^kruithne@gmail\.com$|^marlamin@marlamin\.com$)", re.I)

# Private networks: RFC 1918, shared address space (RFC 6598, VPN overlays)
# and link-local.
PRIVATE = [ipaddress.IPv4Network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10",
                                                "169.254.0.0/16")]

# --------------------------------------------------------------- reading

URL = re.compile(r"\bhttps?://(?:[^/\s@\"'<>]*@)?([A-Za-z0-9.-]+)", re.I)
EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
IPV4 = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.]*\d)")
SITE = re.compile(r"\"site\"\s*:")
SHARE = re.compile(r"#[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])")


def tracked() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        pytest.skip(f"no git checkout: {e}")
    return [p for p in out.decode().split("\0") if p]


def texts():
    """(name, text) of every tracked file, archives opened."""
    for rel in tracked():
        if rel.startswith(PRIVATE_DIRS) or rel == SELF:
            continue
        path = ROOT / rel
        if not path.is_file() or path.suffix.lower() in BINARY:
            continue
        data = path.read_bytes()
        if rel.endswith(".gz"):
            yield from _blob(rel, gzip.decompress(data))
        elif rel.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for m in z.namelist():
                    if not m.endswith("/") and Path(m).suffix.lower() not in BINARY:
                        yield from _blob(f"{rel}!{m}", z.read(m))
        else:
            yield from _blob(rel, data)


def _blob(name: str, data: bytes):
    if b"\0" in data[:4096]:
        return
    yield name, data.decode("utf-8", "replace")


def lines():
    for name, text in texts():
        for i, line in enumerate(text.splitlines(), 1):
            yield name, i, line


# --------------------------------------------------------------- checks


def host_ok(host: str) -> bool:
    h = host.lower().rstrip(".")
    if "." not in h:  # a service name inside compose or a cluster (vl, victorialogs)
        return True
    if any(h == d or h.endswith("." + d) for d in RESERVED_HOSTS) or h.endswith(RESERVED_SUFFIXES):
        return True
    if re.fullmatch(r"[\d.]+", h):
        return True  # an address: the IPv4 check judges it
    return any(h == d or h.endswith("." + d) for d in UPSTREAMS | TEST_VECTORS)


def ip_private(s: str) -> bool:
    """An address in a private network. 127.0.0.1 and 0.0.0.0 are binds,
    and four-part versions (0.4.1.1) are not addresses: neither is listed."""
    try:
        ip = ipaddress.IPv4Address(s)
    except ValueError:
        return False  # a version number, not an address
    return any(ip in n for n in PRIVATE)


def is_fixture(name: str) -> bool:
    """Recorded-looking data: tests/ and demo/ files that aren't code."""
    base = name.split("!")[0]
    return base.startswith(("tests/", "demo/")) and not base.endswith((".py", ".js", ".ps1", ".sh", ".md"))


def shape_hits(name: str, i: int, line: str) -> list[str]:
    hits = []
    for m in URL.finditer(line):
        if not host_ok(m.group(1)):
            hits.append(f"{name}:{i}: host {m.group(1)}")
    for m in EMAIL.finditer(line):
        if not EMAIL_OK.search(m.group(0)) and not host_ok(m.group(0).rsplit("@", 1)[1]):
            hits.append(f"{name}:{i}: email @{m.group(0).rsplit('@', 1)[1]}")
    for m in IPV4.finditer(line):
        if ip_private(m.group(1)):
            hits.append(f"{name}:{i}: private address")
    if is_fixture(name) and SITE.search(line):
        hits.append(f"{name}:{i}: site label")
    if SHARE.search(line):
        hits.append(f"{name}:{i}: share-link fragment")
    return hits


def test_no_private_shapes():
    hits = [h for n, i, line in lines() for h in shape_hits(n, i, line)]
    assert not hits, f"{len(hits)} private-looking value(s):\n" + "\n".join(hits[:200])


def denylist(logins: bool = False) -> list[str]:
    """The denylist's entries. `login:<name>` lines are logins (the owner's
    own, a friend's): too short to look for everywhere (a first name is in
    item and NPC names), so they are looked for only where a screenshot's
    people come from (test_screenshots_show_invented_people)."""
    if not DENYLIST.is_file():
        pytest.skip(f"no denylist at {DENYLIST} (kept out of git on purpose)")
    out = []
    for raw in DENYLIST.read_text(encoding="utf-8").splitlines():
        s = raw.split("#", 1)[0].strip()
        if s and s.lower().startswith("login:") == logins:
            out.append(s.split(":", 1)[1].strip() if logins else s)
    return [s for s in out if s]


def test_nothing_on_the_denylist():
    entries = denylist()
    if not entries:
        pytest.skip("the denylist is empty")
    pats = [re.compile(r"(?<![A-Za-z0-9])" + re.escape(e) + r"(?![A-Za-z0-9])", re.I) for e in entries]
    hits = []
    for name, i, line in lines():
        if not STRICT:
            line = RELEASE_IDS.sub("", line)
        for n, p in enumerate(pats, 1):
            if p.search(line):
                hits.append(f"{name}:{i}: denylist entry {n}")  # never the entry itself
    assert not hits, f"{len(hits)} denylisted value(s):\n" + "\n".join(hits[:200])


# --------------------------------------------------------------- screenshots

# Every image the docs show and what makes it: made-up data only (the demo
# character, the demo lab's invented people), never a real install. A new
# image goes in with its producer; an image nobody can remake fails.
SHOTS = ("overview", "next-moves", "gear", "upgrades", "craft", "dungeons", "climb", "model3d", "timeline", "journal")
IMAGES = {
    **{f"{n}{d}.webp": "site/scripts/screenshots.py" for n in SHOTS for d in ("", "-dark")},
    "social.png": "site/scripts/screenshots.py",
    "admin.webp": "demo/browser/admin_shot.py",
    # By hand, from the local demo (docs/self-host/demo.md): Nibbet, made up, at 1100x713.
    "local-demo.webp": "dev/seed.py",
    # Drawn by hand: no data in it.
    "how-it-works.svg": "docs/images/how-it-works.svg",
}
# The people the demo lab's pages show (demo/stack/up.sh, demo/lab): the
# owner, two friends and their logins at the stand-in identity provider.
INVENTED_PEOPLE = {"owner", "frostbolt", "moonfire", "demo-owner", "demo-owners", "demo-friend-dark",
                   "demo-friend-light", "demo-friends"}
LAB = ("demo/stack/up.sh", "demo/stack/env.sh", "demo/lab", "demo/browser/admin_shot.py")
IMAGE_REF = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)")


def test_every_screenshot_has_a_producer():
    images = sorted(p.name for p in (ROOT / "docs" / "images").iterdir() if p.is_file())
    assert [n for n in images if n not in IMAGES] == [], "an image without a producer: add it to IMAGES"
    for name, src in IMAGES.items():
        assert (ROOT / src).is_file(), (name, src)
        if src.endswith(".py") and src != "dev/seed.py":  # a script takes it: it names it
            stem = name.rsplit(".", 1)[0].removesuffix("-dark")
            assert stem in (ROOT / src).read_text(encoding="utf-8"), (name, src)


def lab_people() -> set[str]:
    """Every identity the demo lab sets: the owner's sub and slug, the
    friends list, the identity proxies' sub:org pairs."""
    text = "\n".join((ROOT / f).read_text(encoding="utf-8") for f in LAB)
    out = set(re.findall(r"WOW_OWNER_(?:SUB|SLUG)=([\w.-]+)", text))
    for m in re.finditer(r"echo '(\{[^']*\})' > \"\$state/friends\.json\"", text):
        for sub, name in json.loads(m.group(1)).items():
            out |= {sub, name}
    for m in re.finditer(r'"\$\w+:([\w.-]+):([\w.-]+)"', text):
        out |= {m.group(1), m.group(2)}
    out |= set(re.findall(r"Standin\(user='([\w.-]+)', org='([\w.-]+)'", text)[0]) if "Standin(" in text else set()
    return out


def test_demo_lab_people_are_invented():
    people = lab_people()
    assert {"owner", "frostbolt", "moonfire"} <= people  # the parse still finds them
    assert people <= INVENTED_PEOPLE, sorted(people - INVENTED_PEOPLE)


def test_screenshots_show_invented_people():
    """No real login (the denylist's `login:` lines) in what a screenshot is
    made from (the demo lab's files, the screenshot scripts) or in any
    image's alt text in the docs."""
    logins = denylist(logins=True)
    if not logins:
        pytest.skip("no login: lines in the denylist")
    pats = [re.compile(r"(?<![A-Za-z0-9])" + re.escape(e) + r"(?![A-Za-z0-9])", re.I) for e in logins]
    hits = []
    for rel in sorted(set(LAB) | {v for v in IMAGES.values() if v.endswith(".py")}):
        for i, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
            hits += [f"{rel}:{i}: login {n}" for n, p in enumerate(pats, 1) if p.search(RELEASE_IDS.sub("", line))]
    for name, text in texts():
        if not name.endswith(".md"):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for m in IMAGE_REF.finditer(line):
                hits += [f"{name}:{i}: login {n} in alt text" for n, p in enumerate(pats, 1) if p.search(m.group(1))]
    assert not hits, "\n".join(hits)


# --------------------------------------------------------------- the checks' own tests


@pytest.mark.parametrize(("line", "found"), [
    ("see https://www.wowhead.com/forever/item=7001", False),
    ("VL_URL=http://victorialogs:9428", False),
    ("http://wow-site-upload.wow.svc.cluster.local.:8081", False),
    ("https://wow.private-site.be/zogzog/", True),
    ("mail me at someone@private-site.be", True),
    ("noreply@anthropic.com and a@example.org", False),
    ("BIND=127.0.0.1 and 0.0.0.0", False),
    ("the NAS at 192.168.1.20", True),
    ("10.1.2.60:5514", True),
    ("client build 1.60.1.70959 and 10.0.1", False),
    ("https://wow.example.org/zogzog/#" + "A" * 43, True),
])
def test_the_shapes_are_caught(line, found):
    assert bool(shape_hits("README.md", 1, line)) is found


def test_site_labels_only_in_fixtures():
    assert shape_hits("tests/data/x.jsonl", 1, '{"site": "home"}')
    assert not shape_hits("wowstats/ui.py", 1, 'site = {"site": slug}')
