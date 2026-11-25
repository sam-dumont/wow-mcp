"""The upstream checkouts the exports read: Polestar at tools/polestar-tag
(DEST, as its addon release ships it), and next to it DEST-forever-db, the
same checkout with forever-db's own release at tools/forever-db-tag in place
of the copy the addon bundles.

    python3 tools/kb/checkout.py /tmp/polestar                  # the pinned tags
    python3 tools/kb/checkout.py /tmp/polestar --forever-db data-v2026.11.3
    python3 tools/kb/checkout.py /tmp/polestar --keep          # reuse a clone already at the tag

The route and gear references run Polestar's own planner and rules, on the
forever-db its addon release carries: mixing in another release would
change Polestar's numbers (#266). Every other export that reads forever-db
reads the overlay. The overlay links each entry of the clone but
`forever-db/` (the clone is about 500 MB), so it costs one unpacked release.

forever-db (Polestar's quest database) is released on its own
(`data-v<YYYY.MM.N>`), days or weeks before an addon release bundles it: on
2026-11-25 Polestar's newest addon release (v0.4.2.2) still carried
2026.11.1, while 2026.11.2 had held two quests of the Drowned City for four
days. The exports take forever-db at its own newest release and Polestar's
own data (routes, guides, gear, trainers) at the addon's (owner, 2026-11-25:
static data tracks the latest of each source).

The release's zip is checked twice before anything reads it: its SHA-256
against the release's SHA256SUMS, then every data file against the hash
forever-db's own datapackage.json gives it. Paths are taken only under
`forever-db/`; nothing in it runs (the exports read JSON lines).

Stdlib only (route-reference.yml runs it with the runner's python3).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO = "fitzforge/polestar"
POLESTAR_TAG = ROOT / "tools" / "polestar-tag"
FOREVER_DB_TAG = ROOT / "tools" / "forever-db-tag"
TAG = re.compile(r"^v\d+(\.\d+){1,4}$")
DATA_TAG = re.compile(r"^data-v(\d{4}\.\d{1,2}\.\d{1,3})$")
ASSET = "https://github.com/{repo}/releases/download/{tag}/{name}"
UA = {"User-Agent": "wow-mcp data export"}
MAX_ZIP = 64 << 20  # 2026.11.2's is 2.2 MB


class Refused(Exception):
    """The release isn't what its own checksums say."""


def pinned(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def get(url: str, limit: int = MAX_ZIP) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
        body = r.read(limit + 1)
    if len(body) > limit:
        raise Refused(f"{url}: over {limit} bytes")
    return body


def unpack(zip_bytes: bytes, sums: str, name: str, version: str, dest: Path) -> dict:
    """The checked forever-db folder written to `dest`; returns its index.json."""
    want = {n: h for h, n in (line.split(None, 1) for line in sums.splitlines() if line.strip())}
    if want.get(name, "").lower() != hashlib.sha256(zip_bytes).hexdigest():
        raise Refused(f"{name}: SHA-256 isn't the one SHA256SUMS gives")
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            parts = Path(info.filename).parts
            if len(parts) < 2 or parts[0] != "forever-db" or ".." in parts or info.filename.startswith("/"):
                raise Refused(f"unexpected path in the zip: {info.filename[:80]!r}")
            files["/".join(parts[1:])] = z.read(info)
    try:
        pkg = json.loads(files["datapackage.json"])
        index = json.loads(files["index.json"])
    except (KeyError, ValueError) as e:
        raise Refused(f"no readable datapackage.json or index.json ({e})") from e
    if str(pkg.get("version")) != version or str(index.get("version")) != version:
        raise Refused(f"the release says version {pkg.get('version')!r}, its tag {version}")
    for res in pkg.get("resources") or []:
        rel, h = str(res.get("path") or ""), str(res.get("hash") or "")
        if rel not in files:
            raise Refused(f"{rel}: listed in datapackage.json, not in the zip")
        if h.removeprefix("sha256:") != hashlib.sha256(files[rel]).hexdigest():
            raise Refused(f"{rel}: its hash isn't datapackage.json's")
    if dest.exists():
        shutil.rmtree(dest)
    for rel, body in files.items():
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(body)
    return index


def forever_db(tag: str, dest: Path) -> dict:
    """forever-db's release `tag` into `dest` (a checkout's forever-db/)."""
    m = DATA_TAG.match(tag)
    if not m:
        raise Refused(f"not a forever-db release tag: {tag[:40]!r}")
    name = f"forever-db-{m.group(1)}.zip"
    sums = get(ASSET.format(repo=REPO, tag=tag, name="SHA256SUMS"), 1 << 16).decode("utf-8")
    return unpack(get(ASSET.format(repo=REPO, tag=tag, name=name)), sums, name, m.group(1), dest)


def overlay_path(dest: Path) -> Path:
    return dest.with_name(dest.name + "-forever-db")


def overlay(dest: Path, tag: str) -> dict:
    """DEST-forever-db: the clone's entries linked, forever-db's release `tag` unpacked."""
    out = overlay_path(dest)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for entry in dest.iterdir():
        if entry.name not in (".git", "forever-db"):
            (out / entry.name).symlink_to(entry.resolve(), target_is_directory=entry.is_dir())
    return forever_db(tag, out / "forever-db")


def polestar(tag: str, dest: Path, keep: bool = False) -> None:
    if not TAG.match(tag):
        raise Refused(f"not a Polestar release tag: {tag[:40]!r}")
    if keep and (dest / ".git").is_dir():
        at = subprocess.run(["git", "-C", str(dest), "describe", "--tags", "--exact-match"],
                            capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(dest), "status", "--porcelain"],
                               capture_output=True, text=True).stdout.strip()
        if at == tag and not dirty:  # the addon's own files, nothing laid over them
            return
    if dest.exists():
        shutil.rmtree(dest)
    subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", tag, f"https://github.com/{REPO}", str(dest)],
                   check=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dest", type=Path)
    ap.add_argument("--polestar", default=None, help="the addon's tag (default: tools/polestar-tag)")
    ap.add_argument("--forever-db", default=None, help="forever-db's release (default: tools/forever-db-tag)")
    ap.add_argument("--keep", action="store_true", help="keep a clone already at the Polestar tag")
    a = ap.parse_args(argv)
    try:
        polestar(a.polestar or pinned(POLESTAR_TAG), a.dest, a.keep)
        idx = overlay(a.dest, a.forever_db or pinned(FOREVER_DB_TAG))
    except (Refused, OSError, subprocess.CalledProcessError) as e:
        print(f"checkout: {e}", file=sys.stderr)
        return 1
    print(f"checkout: Polestar {a.polestar or pinned(POLESTAR_TAG)}, forever-db {idx.get('version')} "
          f"(client {idx.get('clientBuild')}) in {a.dest} and {overlay_path(a.dest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
