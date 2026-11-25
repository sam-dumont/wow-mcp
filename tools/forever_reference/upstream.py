"""The upstream versions the forever reference is exported from, and their
data: forever-db as Polestar publishes it (its own `data-v*` releases, a zip
and SHA256SUMS each, ahead of Polestar's addon releases) and Forever's
client build (its tables on wago.tools).

Two pins say which: tools/forever-db-tag (forever-db's release) and
data/kb/watch.json `client_build.known` (the newest client build, moved
daily by the watch's client-build kind). `pinned()` reads them: every
export and check rebuilds the same bytes from them. `latest_forever_db()`
asks GitHub for forever-db's newest release, which the export's `--latest`
writes to the pin. Stdlib only.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import urllib.request
import zipfile
from pathlib import Path

REPO = "fitzforge/polestar"
RELEASES = f"https://api.github.com/repos/{REPO}/releases?per_page=100"
ASSET = f"https://github.com/{REPO}/releases/download/data-v{{v}}/{{name}}"
TAG = re.compile(r"^data-v(\d{4}\.\d{1,2}\.\d+)$")
ROOT = Path(__file__).resolve().parents[2]
FOREVER_DB_TAG = ROOT / "tools" / "forever-db-tag"
WATCH = ROOT / "data" / "kb" / "watch.json"


def _get(url: str, timeout: int = 120) -> bytes:
    headers = {"User-Agent": "wow-stats export"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
        return r.read()


def _key(v: str) -> tuple[int, ...]:
    return tuple(int(n) for n in v.split("."))


def latest_forever_db() -> str:
    """forever-db's newest published release (data-vYYYY.M.N, neither a draft
    nor a pre-release), as its version."""
    rel = json.loads(_get(RELEASES))
    vs = [m.group(1) for r in rel if not r.get("draft") and not r.get("prerelease")
          for m in [TAG.match(str(r.get("tag_name") or ""))] if m]
    if not vs:
        raise SystemExit(f"no forever-db release (data-v*) in {REPO}")
    return max(vs, key=_key)


def pinned() -> dict:
    """The versions the exports read: forever-db's release and the client build."""
    tag = FOREVER_DB_TAG.read_text(encoding="utf-8").strip()
    m = TAG.match(tag)
    if not m:
        raise SystemExit(f"{FOREVER_DB_TAG}: {tag!r} isn't a forever-db release tag (data-vYYYY.M.N)")
    build = json.loads(WATCH.read_text(encoding="utf-8"))["client_build"]["known"]
    return {"forever_db": m.group(1), "client_build": build}


def pin_forever_db(version: str) -> None:
    FOREVER_DB_TAG.write_text(f"data-v{version}\n", encoding="utf-8")


def forever_db(version: str, dest: Path, checkout: Path | None = None) -> Path:
    """forever-db `version` unpacked under `dest` (its `forever-db/` directory),
    the zip checked against the release's SHA256SUMS. Reused when there, or
    taken from a Polestar checkout whose forever-db is that version already
    (its addon release bundles it, or the checkout carries the release)."""
    for root in ((checkout / "forever-db") if checkout else None, dest / f"forever-db-{version}" / "forever-db"):
        try:
            if root and json.loads((root / "index.json").read_text(encoding="utf-8")).get("version") == version:
                return root
        except (OSError, ValueError):
            pass
    root = dest / f"forever-db-{version}" / "forever-db"
    name = f"forever-db-{version}.zip"
    sums = _get(ASSET.format(v=version, name="SHA256SUMS")).decode()
    want = next((ln.split()[0] for ln in sums.splitlines() if ln.split()[-1:] == [name]), None)
    body = _get(ASSET.format(v=version, name=name), timeout=300)
    if not want or hashlib.sha256(body).hexdigest() != want:
        raise SystemExit(f"{name}: not the SHA-256 its release's SHA256SUMS gives")
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        bad = [n for n in z.namelist() if not n.startswith("forever-db/") or ".." in Path(n).parts]
        if bad:
            raise SystemExit(f"{name}: unexpected paths {bad[:3]}")
        z.extractall(dest / f"forever-db-{version}")
    got = json.loads((root / "index.json").read_text(encoding="utf-8")).get("version")
    if got != version:
        raise SystemExit(f"{name}: its index.json says {got}")
    return root
