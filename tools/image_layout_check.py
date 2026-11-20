"""Run inside the built image (ci.yml, "Installed layout"): wowstats is in
site-packages there, with its data under wowstats/data/, and a path built
from the checkout's layout (parents[2] / "data") finds nothing. Such a miss
was silent once: /api/dungeons gave no drops for The Drowned City (kbloot).

    docker run --rm -v "$PWD/tools:/src/tools:ro" -v "$PWD/pyproject.toml:/src/pyproject.toml:ro" \
        --entrypoint python wow-stats:ci /src/tools/image_layout_check.py /src/pyproject.toml
"""

import sys
import tomllib
from pathlib import Path

import wowstats
from wowstats.plan import kbloot

MIN_DROPS = 20


def main(pyproject: str) -> int:
    pkg = Path(wowstats.__file__).resolve().parent
    bad: list[str] = []
    # Every file the wheel force-includes lands where the code expects it.
    conf = tomllib.loads(Path(pyproject).read_text(encoding="utf-8"))
    force = conf["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    for dest in force.values():
        if not (pkg.parent / dest).exists():
            bad.append(f"missing from the image: {dest}")
    # Every module-level tuple of candidate paths (DATA_DIRS, BUNDLED, ...) has a hit.
    import importlib
    import pkgutil
    for m in pkgutil.walk_packages(wowstats.__path__, "wowstats."):
        if m.name.endswith("__main__"):
            continue
        try:
            mod = importlib.import_module(m.name)
        except Exception:
            continue  # optional extras; the smoke tests start the real apps
        for name, val in vars(mod).items():
            is_paths = name.isupper() and isinstance(val, tuple) and val and all(isinstance(p, Path) for p in val)
            if is_paths and not any(p.exists() for p in val):
                bad.append(f"{m.name}.{name}: none of {[str(p) for p in val]} exists")
    n = len(kbloot.drops("The Drowned City"))
    if n < MIN_DROPS:
        bad.append(f"kbloot: {n} drops for The Drowned City, expected >= {MIN_DROPS}")
    print("\n".join(bad) or f"installed layout ok ({len(force)} bundled paths, {n} Drowned City drops)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
