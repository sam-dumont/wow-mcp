"""Fetch the demo's separately released client artwork into a checksum-keyed cache."""

from __future__ import annotations

import hashlib
import os
import re
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ARCHIVE = "roster-3d.tar.gz"
MAX_BYTES = 256 * 1024 * 1024
PIN = Path(__file__).resolve().parents[2] / "site" / "demo3d-roster.sha256"


def fetch(cache: Path, pin_file: Path = PIN) -> Path:
    repo = os.environ.get("GITHUB_REPOSITORY") or "sam-dumont/wow-mcp"
    url = os.environ.get("DEMO_3D_URL") or f"https://github.com/{repo}/releases/download/demo-3d-4/{ARCHIVE}"
    hint = "Alternatively, set DEMO_3D_STORE to the store from demo3d.py build-roster."
    try:
        match = re.fullmatch(r"([0-9a-f]{64})\s+" + re.escape(ARCHIVE), pin_file.read_text().strip())
        if not match:
            raise ValueError(f"invalid SHA-256 pin in {pin_file}")
        digest = match[1]
        target = cache / digest
        if target.exists():
            if (target / "roster.json").is_file() and (target / "tables" / "build.json").is_file():
                return target
            raise ValueError(f"incomplete cached roster at {target}; remove this directory to download it again")
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache) as tmp:
            archive = Path(tmp) / ARCHIVE
            sha = hashlib.sha256()
            with urllib.request.urlopen(url, timeout=60) as response, archive.open("wb") as out:  # noqa: S310
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError(f"archive exceeds the {MAX_BYTES}-byte limit")
                    out.write(chunk)
                    sha.update(chunk)
            if sha.hexdigest() != digest:
                raise ValueError(f"SHA-256 mismatch at {url}: expected {digest}, got {sha.hexdigest()}")
            with tarfile.open(archive, "r:gz") as tar:
                tar.extractall(tmp, filter="data")
            store = Path(tmp) / "store"
            if not (store / "roster.json").is_file() or not (store / "tables" / "build.json").is_file():
                raise ValueError(f"{ARCHIVE} has no roster manifest or client build")
            try:
                store.rename(target)
            except OSError:
                if not target.is_dir():  # another seed may have installed this exact digest
                    raise
        return target
    except (OSError, ValueError, tarfile.TarError) as exc:
        raise SystemExit(f"demo 3D: could not load {url}: {exc}. {hint}") from exc


if __name__ == "__main__":
    print(fetch(Path(sys.argv[1])).resolve())
