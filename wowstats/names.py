"""Character names, and the one place a bundle file is written.

A character's name comes from the player's own events and ends up as a file
name in the static bundles (data/char/<name>.json...). The publisher wrote
them after decoding, without checking where the path led: a crafted name
like "../../other/b1/public/data/char/X" wrote into another player's
bundle (outside review, 2026-11-19). Two guards, either one enough:

- valid_char: a name is letters, with the separators real names use
  (space, hyphen, apostrophe) and ASCII digits (the shipper names a
  character it can't map "charid-<hex>"), starting with a letter, at most
  64 characters. Checked where events come in (/ingest, the shipper) and again
  on stored events before a publish (one stored before this rule is
  skipped and logged).
- inside: every bundle write resolves its destination and refuses one
  outside the bundle's folder.

The shipper (shipper/wow_stats.py) is one standalone file and carries its
own copy of valid_char; tests/test_names.py keeps the two equal.
"""

from __future__ import annotations

import unicodedata
import urllib.parse
from pathlib import Path

CHAR_MAX = 64
SEPARATORS = frozenset(" -'")
DIGITS = frozenset("0123456789")  # the shipper's fallback for an unmapped id: charid-3f2a0


def valid_char(name) -> bool:
    """One character's name, safe as a single file name component."""
    if not isinstance(name, str) or not 0 < len(name) <= CHAR_MAX:
        return False
    if not unicodedata.category(name[0]).startswith("L"):
        return False
    return all(c in SEPARATORS or c in DIGITS or unicodedata.category(c).startswith("L") for c in name)


class Escape(ValueError):
    """A bundle path that would land outside the bundle."""


def inside(root: Path, rel: str) -> Path:
    """The file for `rel` (a URL path inside the bundle, percent-encoded as
    the page asks for it), refused unless it resolves under `root`."""
    if not isinstance(rel, str) or not rel:
        raise Escape("empty path")
    plain = urllib.parse.unquote(rel)
    if "\x00" in plain or "\\" in plain or plain.startswith("/"):
        raise Escape(f"bad path {rel!r}")
    parts = plain.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise Escape(f"bad path {rel!r}")
    base = root.resolve()
    dest = (base / plain).resolve()
    if base not in dest.parents:
        raise Escape(f"{rel!r} leaves the bundle")
    return dest
