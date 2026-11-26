"""Every static data file built from an outside source, and how to build it
again: the one list (EXPORTS) the daily refresh, route-reference.yml and the
docs read. A new exporter goes here, nowhere else, and the daily run picks
it up.

    python3 tools/kb/rebuild.py run --polestar /tmp/polestar          # every daily export, in order
    python3 tools/kb/rebuild.py run --polestar /tmp/polestar --inputs forever-db   # those that read it
    python3 tools/kb/rebuild.py run --polestar /tmp/polestar --check  # exit 1 when a committed file differs
    python3 tools/kb/rebuild.py list                                  # the table docs/kb.md shows

The checkouts are tools/kb/checkout.py's: Polestar at tools/polestar-tag
(`{polestar}`: the route and gear references, Polestar's own planner on the
forever-db its addon carries) and the same with forever-db's own release at
tools/forever-db-tag (`{latest}`: every other export that reads forever-db).
Other pinned inputs (the cMaNGOS dump,
the WoWSims Exporter addon at the release data/kb/watch.json knows) are
fetched into `--work` when an export needs them.

The daily watch (tools/kb/versions.sh, kb-refresh.yaml) reruns every
`daily` export once a day after the version kinds moved their pins (owner,
2026-11-25: "a daily rebuild of static files"), so what ships is always
the newest of each source and a file that drifted from its exporter is
rebuilt too. Each export is deterministic: the same inputs give the same
bytes, so a day where nothing moved makes no commit and no PR.

An export that isn't `daily` says why in `manual`: it needs something CI
doesn't have (a game client's hotfix cache, research rosters), and a person
reruns it with the command in its module's docstring.

`inputs` are the watch kinds (tools/kb/watch.py) whose move changes the
file; `--inputs` runs only those, for a kind's own commit. ForeverDB.net's
two exports have no pinned version to rerun at (its manifest only serves
the newest data): tools/kb/bump.py foreverdb runs them from its checked
download (`{files}`), never `run`.

Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checkout  # noqa: E402 - tools/kb/checkout.py

ROOT = Path(__file__).resolve().parents[2]
PY = ("uv", "run", "--frozen", "python")


@dataclass(frozen=True)
class Export:
    name: str
    cmd: tuple[str, ...]  # placeholders: {polestar} {latest} {fdb} {lua} {cmangos} {wowsims} {files}
    outputs: tuple[str, ...]
    inputs: tuple[str, ...]  # watch kinds whose move changes the outputs
    doc: str  # where the export is described
    manual: str = ""  # why a person runs it, not the daily job
    needs: tuple[str, ...] = field(default=())  # fetched inputs beyond the checkout: cmangos, wowsims
    reads: tuple[str, ...] = field(default=())  # other exports' outputs it reads: rerun when one moves

    @property
    def daily(self) -> bool:
        return not self.manual and "{files}" not in self.cmd


# In the order they run. `reads` names the other outputs an export reads:
# when one of them moves in a run, the export runs again. The route
# reference and the dungeon wings read each other (the trips' wings, the
# wings' trips), so a forever-db that moves one moves the other on the next
# pass: `run` goes round until a pass moves nothing (2026-11-25, forever-db
# 2026.11.2: a second pass took quest 5089 off Lower Blackrock Spire's page).
EXPORTS: tuple[Export, ...] = (
    Export("forever-reference", (*PY, "tools/forever_reference/export.py", "--polestar", "{latest}",
                                 "--forever-db-cache", "{fdb}"),
           ("data/forever-reference.json",), ("polestar", "forever-db", "client-build"),
           "docs/forever-reference.md", reads=("data/gear-reference.json",)),
    Export("dungeon-quests", (*PY, "tools/dungeon_quests/export.py", "--polestar", "{latest}",
                              "--forever-db-cache", "{fdb}"),
           ("data/dungeons/quests.json",), ("polestar", "forever-db", "client-build"), "docs/dungeon-xp.md",
           reads=("data/forever-reference.json",)),
    Export("route-reference",
           ("node", "tools/route_reference/export.mjs", "--polestar", "{polestar}", "--lua", "{lua}"),
           ("data/route-reference.json",), ("polestar", "client-build"), "docs/route-reference.md",
           reads=("data/forever-reference.json", "data/dungeons/wings.json")),
    Export("gear-reference", ("node", "tools/gear_reference/export.mjs", "--polestar", "{polestar}", "--lua", "{lua}"),
           ("data/gear-reference.json",), ("polestar",), "docs/route-reference.md"),
    Export("kb-reference", (*PY, "tools/kb/export.py", "--polestar", "{latest}"),
           ("data/kb-reference.json.gz",), ("polestar", "forever-db"), "docs/kb.md"),
    Export("class-reference", (*PY, "tools/class_reference/export.py", "--polestar", "{polestar}"),
           ("data/class-reference.json",), ("polestar", "client-build"), "docs/kb.md"),
    Export("trainers", (*PY, "tools/trainers/export.py", "--polestar", "{polestar}", "--lua", "{lua}"),
           ("data/trainers-reference.json",), ("polestar",), "docs/kb.md"),
    # Polestar's profession guides and the client tables at data/game-items.json's
    # build (#267): it moves with Polestar here, and with the item table by hand.
    Export("craft-reference", (*PY, "tools/craft_reference/export.py", "--polestar", "{polestar}", "--lua", "{lua}"),
           ("data/craft-reference.json",), ("polestar",), "docs/architecture.md"),
    Export("dungeon-wings",
           (*PY, "tools/dungeon_wings/export.py", "--cmangos", "{cmangos}", "--polestar", "{latest}"),
           ("data/dungeons/wings.json",), ("polestar", "forever-db"), "docs/dungeon-xp.md", needs=("cmangos",),
           reads=("data/forever-reference.json", "data/route-reference.json")),
    Export("wowsims-talents", (*PY, "tools/wowsims_talents.py", "{wowsims}"),
           ("data/wowsims-talents.json",), ("addons",), "docs/reference/addons.md", needs=("wowsims",)),
    Export("foreverdb-sources", (*PY, "-I", "tools/foreverdb_sources/export.py", "--files", "{files}"),
           ("data/foreverdb/item-sources.json",), ("foreverdb",), "data/foreverdb/README.md"),
    Export("foreverdb-weights", (*PY, "-I", "tools/foreverdb_weights/export.py", "--files", "{files}"),
           ("data/foreverdb-weights.json",), ("foreverdb",), "data/foreverdb/README.md"),
    Export("game-items", (*PY, "tools/game_items/export.py", "--dbcache", "<DBCache.bin>"),
           ("data/game-items.json",), ("client-build",), "tools/game_items/export.py",
           manual="needs a Forever client's hotfix cache (DBCache.bin, same build): without it about half of "
                  "Forever's changed items keep their old numbers"),
    Export("battlenet-look", (*PY, "tools/battlenet_look/export.py", "--retail", "<build>", "--forever", "<build>"),
           ("data/battlenet-look.json",), (), "tools/battlenet_look/export.py",
           manual="pinned at a retail and a Forever build named in the file; Battle.net's look ids only move "
                  "with the retail game"),
    Export("dungeon-xp", (*PY, "tools/dungeon_xp/export.py", "--cmangos", "{cmangos}", "--rosters", "<rosters>"),
           ("data/dungeons/kill-xp.json", "data/dungeons/forever-spawns/forever-spawns.json"), (),
           "docs/dungeon-xp.md", manual="reads research rosters that aren't in the repository"),
    Export("model3d-dbd", (*PY, "tools/model3d_dbd.py", "<WoWDBDefs/definitions>"), ("data/dbd/",), (),
           "tools/model3d_dbd.py", manual="table layouts for the 3D view, from a WoWDBDefs checkout; they only "
                                          "move with a new client layout, which the 3D tests catch"),
)

CMANGOS = "https://github.com/cmangos/classic-db/raw/{commit}/{file}"
WOWSIMS = "https://github.com/{repo}"
WATCH = ROOT / "data" / "kb" / "watch.json"


def select(inputs: list[str] | None = None) -> list[Export]:
    """The daily exports, those reading one of `inputs` when given."""
    return [e for e in EXPORTS if e.daily and (not inputs or set(inputs) & set(e.inputs))]


def outputs(daily_only: bool = False) -> list[str]:
    return [o for e in EXPORTS if e.daily or not daily_only for o in e.outputs]


def _pins(path: Path) -> dict[str, str]:
    """A module's top-level string constants, read without importing it: every
    exporter is a module named `export`, and importing one by that name
    shadows the next."""
    import ast  # noqa: PLC0415

    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign)
            and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)
            for t in n.targets if isinstance(t, ast.Name)}


def fetch_cmangos(work: Path) -> Path:
    pin = _pins(ROOT / "tools" / "dungeon_xp" / "export.py")
    dxp = type("Pin", (), {"CMANGOS_COMMIT": pin["CMANGOS_COMMIT"], "CMANGOS_FILE": pin["CMANGOS_FILE"]})
    out = work / f"cmangos-{dxp.CMANGOS_COMMIT[:12]}.sql.gz"
    if not out.exists():
        req = urllib.request.Request(CMANGOS.format(commit=dxp.CMANGOS_COMMIT, file=dxp.CMANGOS_FILE),
                                     headers={"User-Agent": "wow-mcp data export"})
        with urllib.request.urlopen(req, timeout=300) as r:
            out.write_bytes(r.read(64 << 20))
    return out


def fetch_wowsims(work: Path) -> Path:
    """WoWSims Exporter at the release data/kb/watch.json knows (the addons kind moves it)."""
    addon = next(a for a in json.loads(WATCH.read_text(encoding="utf-8"))["addons"] if a["name"] == "WoWSims Exporter")
    out = work / f"wowsims-{addon['known']}"
    if not out.exists():
        subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", addon["known"],
                        WOWSIMS.format(repo=addon["repo"]), str(out)], check=True)
    return out


FETCH = {"cmangos": fetch_cmangos, "wowsims": fetch_wowsims}


def command(e: Export, values: dict[str, str]) -> list[str]:
    return [part.format(**values) for part in e.cmd]


PASSES = 4


def _digest(root: Path, paths: tuple[str, ...]) -> tuple[str, ...]:
    out = []
    for rel in paths:
        p = root / rel
        out.append(hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "")
    return tuple(out)


def run(exports: list[Export], polestar: Path, lua: str, work: Path, check: bool = False) -> int:
    """Check a disposable copy of the working files, including uncommitted inputs."""
    if not check:
        return _build(exports, polestar.resolve(), lua, work.resolve(), ROOT)
    with tempfile.TemporaryDirectory(prefix="wow-rebuild-check-") as tmp:
        tree = Path(tmp)
        paths = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                                        cwd=ROOT)
        for name in os.fsdecode(paths).split("\0"):
            src = ROOT / name
            if name and src.is_file():
                dest = tree / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
        return _build(exports, polestar.resolve(), lua, work.resolve(), tree, compare=ROOT)


def _build(exports: list[Export], polestar: Path, lua: str, work: Path, root: Path, compare: Path | None = None) -> int:
    work.mkdir(parents=True, exist_ok=True)
    # {polestar}: the addon's checkout, for Polestar's own planner and rules;
    # {latest}: the overlay with forever-db's own release (tools/kb/checkout.py).
    values = {"polestar": str(polestar), "latest": str(checkout.overlay_path(polestar)), "lua": lua,
              "fdb": str(work / "forever-db")}
    ran: list[Export] = []
    todo = list(exports)
    for n in range(PASSES):
        moved: set[str] = set()
        for e in todo:
            print(f"rebuild: {e.name}" + (f" (pass {n + 1})" if n else ""), flush=True)
            for need in e.needs:
                if need not in values:
                    values[need] = str(FETCH[need](work))
            before = _digest(root, e.outputs)
            r = subprocess.run(command(e, values), cwd=root, capture_output=True, text=True)
            if r.returncode:
                sys.stderr.write((r.stdout or "")[-2000:] + (r.stderr or "")[-2000:])
                last = ((r.stderr or "").strip() or (r.stdout or "").strip() or f"exit {r.returncode}").splitlines()[-1]
                print(f"rebuild: {e.name} failed ({last[:200]})", file=sys.stderr)
                return 1
            if _digest(root, e.outputs) != before:
                moved |= set(e.outputs)
            ran += [e] if e not in ran else []
        todo = [e for e in select() if moved & set(e.reads)]
        if not todo:
            break
    else:
        print(f"rebuild: still moving after {PASSES} passes: {', '.join(e.name for e in todo)}", file=sys.stderr)
        return 1
    if compare is None:
        return 0
    differ = False
    for export in ran:
        for path in export.outputs:
            if _digest(root, (path,)) != _digest(compare, (path,)):
                print(f"::error::{path} is not what {export.name} builds from these inputs: "
                      f"rerun it ({export.doc}) and commit the result")
                differ = True
    return int(differ)


def table() -> str:
    rows = ["| Export | Writes | Moves with | Daily |", "|---|---|---|---|"]
    for e in EXPORTS:
        how = "yes" if e.daily else ("with its kind (tools/kb/bump.py foreverdb)" if "{files}" in e.cmd
                                     else f"no: {e.manual}")
        rows.append(f"| {e.name} | {', '.join(f'`{o}`' for o in e.outputs)} | {', '.join(e.inputs) or '-'} | {how} |")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="what", required=True)
    r = sub.add_parser("run")
    r.add_argument("--polestar", type=Path, required=True, help="tools/kb/checkout.py's checkout")
    r.add_argument("--lua", default="lua5.4")
    r.add_argument("--work", type=Path, default=Path("/tmp/wow-mcp-rebuild"), help="where fetched inputs go")
    r.add_argument("--inputs", nargs="*", metavar="KIND", help="only the exports that read these watch kinds")
    r.add_argument("--check", action="store_true",
                   help="exit 1 when a committed file differs; leaves the tree as it was")
    sub.add_parser("list")
    sub.add_parser("outputs")
    a = ap.parse_args(argv)
    if a.what == "list":
        print(table())
        return 0
    if a.what == "outputs":
        print("\n".join(outputs(daily_only=True)))
        return 0
    return run(select(a.inputs), a.polestar, a.lua, a.work, a.check)


if __name__ == "__main__":
    raise SystemExit(main())
