"""The keyed names of sealed /zogzog/ files (wowstats.publish.sealed_name),
back to their paths, for tests that open a whole bundle. conftest records
every name the publisher computes in this process; a bundle a subprocess
built is mapped with `paths_for`."""

from __future__ import annotations

from wowstats import publish

NAMES: dict[str, str] = {}  # keyed name (data/<hmac>.enc) -> path (data/char/<Name>.json)


def record() -> None:
    real = publish.sealed_name
    if getattr(real, "recording", False):
        return

    def sealed_name(key: bytes, path: str) -> str:
        name = real(key, path)
        NAMES[name] = path
        return name

    sealed_name.recording = True
    publish.sealed_name = sealed_name


def path_of(name: str) -> str:
    """data/<hmac>.enc -> its path; KeyError when no build here wrote it."""
    return NAMES[name]


def plain_names(zz: dict) -> dict:
    """A bundle's {name: bytes} with each keyed name replaced by its path +
    ".enc", as the files were named before 0.7.0."""
    return {(NAMES[p] + publish.ENC if p in NAMES else p): b for p, b in zz.items()}
