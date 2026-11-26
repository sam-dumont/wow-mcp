"""The daily rebuild of every static data file (owner, 2026-11-25): one list of
exports (tools/kb/rebuild.py), forever-db at its own release
(tools/kb/checkout.py, tools/kb/bump.py forever-db), ForeverDB.net every
day, every rebuilt file shipped in the image and released, and the server
saying which versions it serves (wowstats.dataversions)."""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "kb"))

import bump  # noqa: E402
import checkout  # noqa: E402
import rebuild  # noqa: E402
import watch  # noqa: E402


def state() -> dict:
    return json.loads(watch.STATE.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ the one list


def test_every_export_writes_committed_files_under_data():
    names = [e.name for e in rebuild.EXPORTS]
    assert len(names) == len(set(names))
    for e in rebuild.EXPORTS:
        assert e.outputs and all(o.startswith("data/") for o in e.outputs), e.name
        assert all((ROOT / o.rstrip("/")).exists() for o in e.outputs), e.name
        assert (ROOT / e.doc).is_file(), e.name
        assert set(e.inputs) <= set(watch.VERSIONS), e.name
        assert e.daily or e.manual or "{files}" in e.cmd, e.name


def test_every_rebuilt_file_ships_in_the_image():
    """A file the daily run rebuilds reaches people only through the image and
    the wheel: each one is COPYed and force-included."""
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for out in rebuild.outputs():
        parents = [out.rstrip("/")] + ["/".join(out.split("/")[:i]) for i in range(2, out.count("/") + 1)]
        assert any(f"COPY {p} " in docker for p in parents), f"{out}: no COPY in the Dockerfile"
        assert any(f'"{p}" = ' in pyproject for p in parents), f"{out}: not force-included in the wheel"


def test_a_merged_rebuild_releases():
    """kb-release.yml runs on every file a kb-refresh PR writes."""
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "kb-release.yml").read_text())
    paths = wf[True]["push"]["paths"] if True in wf else wf["on"]["push"]["paths"]
    assert "data/**" in paths and "tools/polestar-tag" in paths and "tools/forever-db-tag" in paths
    assert all(o.startswith("data/") for o in rebuild.outputs())


def test_route_reference_checks_every_daily_export():
    """CI rebuilds what the daily run rebuilds, from the same list, and runs
    when any of its outputs or pins change."""
    text = (ROOT / ".github" / "workflows" / "route-reference.yml").read_text(encoding="utf-8")
    assert "tools/kb/rebuild.py run" in text and "--check" in text and "tools/kb/checkout.py" in text
    for e in rebuild.select():
        for o in e.outputs:
            assert f"- {o}" in text, f"{o} ({e.name}) doesn't start route-reference.yml"


def test_the_lane_runs_the_list_not_its_own():
    lane = (ROOT / "tools" / "kb" / "versions.sh").read_text(encoding="utf-8")
    assert "tools/kb/rebuild.py run" in lane
    for e in rebuild.EXPORTS:
        script = next(c for c in e.cmd if c.endswith((".py", ".mjs")))
        # class-reference: the client-build step's one read of the newest build.
        assert script not in lane or e.name == "class-reference", f"versions.sh runs {script} itself"
    # The daily rebuild runs on every watch run, a version moved or not.
    wf = (ROOT / ".github" / "workflows" / "kb-refresh.yaml").read_text(encoding="utf-8")
    step = wf[wf.index("- name: Versions and the daily rebuild"):]
    assert "steps.watch.outputs.kinds != ''" not in step.split("run:")[0]


def test_the_docs_table_is_the_list():
    assert rebuild.table() in (ROOT / "docs" / "kb.md").read_text(encoding="utf-8")


def test_select_reads_the_inputs():
    names = {e.name for e in rebuild.select(["forever-db"])}
    assert {"forever-reference", "dungeon-quests", "kb-reference", "dungeon-wings"} <= names
    # Polestar's planner and rules run on the forever-db its addon carries (#266);
    # the route trips still follow the forever reference and the wings (`reads`).
    assert not names & {"gear-reference", "route-reference"}
    route = next(e for e in rebuild.EXPORTS if e.name == "route-reference")
    assert "{polestar}" in route.cmd and "data/dungeons/wings.json" in route.reads
    assert not [e for e in rebuild.select() if not e.daily]
    order = [e.name for e in rebuild.EXPORTS]
    assert order.index("forever-reference") < order.index("dungeon-quests") < order.index("route-reference")


@pytest.mark.parametrize(("dirty", "exit_code"), [(False, 0), (True, 0), (True, 7)])
def test_check_preserves_the_tree_and_index(tmp_path, monkeypatch, dirty, exit_code):
    tree = tmp_path / "caller"
    tree.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
    output = tree / "data.txt"
    output.write_text("original")
    subprocess.run(["git", "add", "data.txt"], cwd=tree, check=True)
    if dirty:
        output.write_text("staged")
        subprocess.run(["git", "add", "data.txt"], cwd=tree, check=True)
        output.write_text("unstaged")
    # Even a generator that writes a partial file and then exits can't touch the caller.
    (tree / "input").write_text("original")
    cmd = (sys.executable, "-c", "from pathlib import Path; Path('data.txt').write_bytes(Path('input').read_bytes()); "
           f"raise SystemExit({exit_code})")
    export = rebuild.Export("test", cmd, ("data.txt",), (), "doc")
    before = output.read_bytes(), (tree / ".git/index").read_bytes()
    monkeypatch.setattr(rebuild, "ROOT", tree)
    assert rebuild.run([export], tmp_path, "lua", tmp_path / "work", check=True) == int(dirty)
    assert (output.read_bytes(), (tree / ".git/index").read_bytes()) == before


# ------------------------------------------------------------------ forever-db


def test_the_pin_is_the_known_release():
    s = state()
    assert (ROOT / "tools" / "forever-db-tag").read_text().strip() == s["forever_db"]["known"]
    assert watch.DATA_TAG.match(s["forever_db"]["known"])


def test_the_newest_final_data_release_is_taken():
    class GH:
        def list_releases(self, repo):
            return [{"tag_name": "v0.4.2.2"}, {"tag_name": "data-v2026.12.1", "prerelease": True},
                    {"tag_name": "data-v2026.11.10"}, {"tag_name": "data-v2026.11.2"}, {"tag_name": "client-v0.1.8"},
                    {"tag_name": "data-v2026.11.3; rm -rf /"}]

        def release_gone(self, repo, tag):
            return False

    assert watch.forever_db_release(GH(), "fitzforge/polestar") == "data-v2026.11.10"
    s = state()
    now = watch.current(s, None, GH(), ["forever-db"])
    assert now["forever-db"]["release"] == "data-v2026.11.10"
    assert watch.changes(s, now)["forever_db_tag"] == "data-v2026.11.10"


def zip_of(files: dict[str, bytes], version: str = "2026.11.9", prefix: str = "forever-db/") -> bytes:
    pkg = {"version": version, "resources": [{"path": p, "hash": "sha256:" + hashlib.sha256(b).hexdigest()}
                                              for p, b in files.items() if p.startswith("data/")]}
    files = {**files, "datapackage.json": json.dumps(pkg).encode(),
             "index.json": json.dumps({"version": version, "schemaVersion": 1, "clientBuild": "1.60.1.1"}).encode()}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p, b in files.items():
            z.writestr(prefix + p, b)
    return buf.getvalue()


def sums(body: bytes, name: str = "forever-db-2026.11.9.zip") -> str:
    return f"{hashlib.sha256(body).hexdigest()}  {name}\n"


def test_a_release_is_read_only_as_its_checksums_say(tmp_path):
    good = zip_of({"data/quest.jsonl": b'{"id":1}\n', "LICENSE": b"GPL"})
    idx = checkout.unpack(good, sums(good), "forever-db-2026.11.9.zip", "2026.11.9", tmp_path / "f")
    assert idx["version"] == "2026.11.9" and (tmp_path / "f" / "data" / "quest.jsonl").read_bytes() == b'{"id":1}\n'
    with pytest.raises(checkout.Refused, match="SHA256SUMS"):
        checkout.unpack(good, sums(b"other"), "forever-db-2026.11.9.zip", "2026.11.9", tmp_path / "g")
    with pytest.raises(checkout.Refused, match="its tag"):
        checkout.unpack(good, sums(good), "forever-db-2026.11.9.zip", "2026.11.8", tmp_path / "g")
    evil = zip_of({"data/quest.jsonl": b"x"}, prefix="forever-db/../")
    with pytest.raises(checkout.Refused, match="unexpected path"):
        checkout.unpack(evil, sums(evil), "forever-db-2026.11.9.zip", "2026.11.9", tmp_path / "g")
    # A data file that isn't what datapackage.json says.
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(good)) as z, zipfile.ZipFile(buf, "w") as out:
        for i in z.infolist():
            out.writestr(i, b'{"id":2}\n' if i.filename.endswith("quest.jsonl") else z.read(i))
    bad = buf.getvalue()
    with pytest.raises(checkout.Refused, match="datapackage"):
        checkout.unpack(bad, sums(bad), "forever-db-2026.11.9.zip", "2026.11.9", tmp_path / "g")
    assert not (tmp_path / "g").exists()


def test_a_forever_db_release_needs_a_person_when_its_terms_move(tmp_path):
    tp = (ROOT / "THIRD_PARTY.md").read_text(encoding="utf-8")
    approved = bump.approved_notice(tp)
    assert "independent sources" in approved and "Polestar's own notice" not in approved
    old, new = tmp_path / "old", tmp_path / "new"
    for d in (old, new):
        d.mkdir()
        (d / "LICENSE").write_text("GNU GENERAL PUBLIC LICENSE\nVersion 3\n")
        (d / "LICENSE-CC-BY-4.0.txt").write_text("Attribution 4.0 International\n")
    # THIRD_PARTY.md nests the notice's headings one level down: not a change.
    (new / "NOTICE.md").write_text("# NOTICE\n\n" + approved.replace("### ", "## "))
    idx = {"schemaVersion": 1}
    assert bump.check_forever_db(old, new, idx, idx, tp) == []
    (new / "NOTICE.md").write_text(approved + "\nAll rights reserved by a new source.\n")
    assert "NOTICE.md" in " ".join(bump.check_forever_db(old, new, idx, idx, tp))
    (new / "NOTICE.md").write_text(approved)
    (new / "LICENSE").write_text("Proprietary\n")
    assert "LICENSE" in " ".join(bump.check_forever_db(old, new, idx, idx, tp))
    (new / "LICENSE").write_text((old / "LICENSE").read_text())
    assert "schema" in " ".join(bump.check_forever_db(old, new, idx, {"schemaVersion": 2}, tp))


def test_the_bump_moves_the_pin_and_the_credit(tmp_path, monkeypatch):
    (tmp_path / "tools").mkdir()
    (tmp_path / "THIRD_PARTY.md").write_text((ROOT / "THIRD_PARTY.md").read_text(encoding="utf-8"))
    monkeypatch.setattr(bump.checkout, "forever_db", lambda tag, dest: dest.mkdir(parents=True) or {
        "version": tag.removeprefix("data-v"), "clientBuild": "1.60.1.71091", "schemaVersion": 1})
    monkeypatch.setattr(bump, "check_forever_db", lambda *a: [])
    assert bump.forever_db("data-v2026.11.3", None, "data-v2026.11.2", root=tmp_path) == 0
    assert (tmp_path / "tools" / "forever-db-tag").read_text() == "data-v2026.11.3\n"
    assert "`tools/forever-db-tag` (2026.11.3, client build 1.60.1.71091)" in (tmp_path / "THIRD_PARTY.md").read_text()
    assert bump.forever_db("data-v2026.11.4; x", str(tmp_path / "why.json"), "data-v2026.11.2", root=tmp_path) == 3


def test_a_moved_forever_db_is_accepted_at_the_checked_tag(tmp_path):
    p = tmp_path / "watch.json"
    p.write_text(watch.STATE.read_text(encoding="utf-8"))
    assert watch.main(["--state", str(p), "--accept", "forever-db", "--release", "data-v2026.11.2"]) == 0
    assert json.loads(p.read_text())["forever_db"]["known"] == "data-v2026.11.2"
    assert watch.main(["--state", str(p), "--accept", "forever-db", "--release", "v0.4.2.2"]) == 3


# ------------------------------------------------------------------ ForeverDB.net, daily


def test_foreverdb_is_read_every_day():
    """2026-11-25: taken the day before, the weekly hold read the known value
    and the daily watch took nothing while the manifest had moved."""
    s = state()
    s["foreverdb"]["taken"] = watch.today().isoformat()
    fdb = watch.foreverdb()
    files = [{"path": p, "sha256": "c" * 64, "url": "downloads/addon-data/x.lua?v=1"} for p in fdb.READ]
    man = {"data": {"version": "2026-11-25.68f55f62", "files": files}}

    class Fetch:
        def get(self, url):
            return 200, json.dumps(man).encode()

    now = watch.current(s, Fetch(), None, ["foreverdb"])
    assert now["foreverdb"]["data"] == fdb.fingerprint(man) != s["foreverdb"]["known"]
    assert "foreverdb" in watch.changes(s, now)["kinds"]
    assert not hasattr(watch, "FOREVERDB_DAYS")


# ------------------------------------------------------------------ what the server says


def test_the_server_says_what_its_data_was_built_from():
    from wowstats import dataversions, redact

    v = dataversions.versions()
    src = json.loads((ROOT / "data" / "foreverdb" / "item-sources.json").read_text())["source"]
    fref = json.loads((ROOT / "data" / "forever-reference.json").read_text())["source"]
    assert v["foreverdb"]["data_version"] == src["data_version"]
    assert v["forever_db"]["version"] == fref["forever_db"]["version"]
    assert re.fullmatch(r"v\d+(\.\d+)+", v["polestar"])
    assert set(v["client_build"]) == {"zones", "spells", "items"}
    assert v["checked"] == state()["checked"]
    pub = redact.public("config", {"view": "public", "debug": True, "data_versions": v})
    assert pub["data_versions"] == v and "debug" not in pub
    assert redact.zogzog("config", {"view": "zogzog", "data_versions": v})["data_versions"] == v


def test_the_release_note_names_forever_db_and_the_rebuild():
    sys.path.insert(0, str(ROOT / "tools" / "release"))
    import kb

    before, after = state(), state()
    after["forever_db"]["known"] = "data-v2026.11.3"
    after["foreverdb"]["known"] = "x"
    c = kb.Change(1, "watch", before, after, ("data/forever-reference.json", "data/wowsims-talents.json"))
    moved = kb.parts([c])
    assert "forever-db 2026.11.3" in moved and "ForeverDB.net data" in moved
    assert "data rebuilt: forever-reference, wowsims-talents" in moved


def test_reads_name_daily_outputs():
    daily = {o for e in rebuild.select() for o in e.outputs}
    for e in rebuild.EXPORTS:
        assert set(e.reads) <= daily, e.name


def test_an_export_whose_input_moved_runs_again(tmp_path, monkeypatch):
    """forever-db 2026.11.2: the wings moved after the route reference had read
    them, so the route reference runs once more (rebuild.run's passes)."""
    ran: list[str] = []
    state = {"data/dungeons/wings.json": "old"}

    def command(e, values):
        ran.append(e.name)
        if e.name == "dungeon-wings":
            state["data/dungeons/wings.json"] = "new"  # moves once, then stays
        return ["true"]

    monkeypatch.setattr(rebuild.subprocess, "run",
                        lambda cmd, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr(rebuild, "command", command)
    monkeypatch.setattr(rebuild, "_digest", lambda root, paths: tuple(state.get(p, p) for p in paths))
    monkeypatch.setitem(rebuild.FETCH, "cmangos", lambda w: w)
    picked = [e for e in rebuild.EXPORTS if e.name in ("route-reference", "dungeon-wings")]
    assert rebuild.run(picked, tmp_path, "lua5.4", tmp_path / "w") == 0
    assert ran == ["route-reference", "dungeon-wings", "route-reference"]
