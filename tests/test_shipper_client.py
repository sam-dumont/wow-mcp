"""Polestar Client's sent.jsonl as a source of Recorder chunks: a chunk gives
the same events whether the shipper read it there or in the SavedVariables."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

T0 = 1794900000


def chunk(cid: str, seq: int, epoch: int, lines: list[str]) -> str:
    return "\n".join([f"chunk\t0\t{seq}\t{epoch}\t{cid}", *lines])


def lua_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


# Two characters on one account, plus one (OLD) whose folder is gone. Each
# chunk opens like the Recorder's: session, zone, xp, step. Zone and step
# durations run across the chunk boundaries, xp_gained chains chunk to chunk.
CHUNKS = {
    ("aaaa", 1): chunk("aaaa", 1, T0, ["session\t0\taaaa\tGnome", "zone\t0\t1415\t10\t20\tElwynn Forest\t",
                                       "xp\t0\t100\t1000\t0", "step\t0\tg\t1\tauto\t1415\t10\t20",
                                       "kill\t50\t99\t5\t\t1415\t11\t21\t40", "xp\t51\t140\t1000\t0"]),
    ("aaaa", 2): chunk("aaaa", 2, T0 + 300, ["session\t0\taaaa\tGnome", "xp\t0\t140\t1000\t0",
                                             "step\t30\tg\t2\tauto\t1415\t12\t22",
                                             "zone\t60\t1416\t1\t2\tWestfall\t", "xp\t70\t990\t1000\t0"]),
    ("aaaa", 3): chunk("aaaa", 3, T0 + 600, ["session\t0\taaaa\tGnome", "xp\t5\t20\t1100\t0",
                                             "taxi\t10\t1\t2", "land\t200\t2\t19\t1416\t3\t4"]),
    ("bbbb", 1): chunk("bbbb", 1, T0 + 100, ["session\t0\tbbbb\tDwarf", "zone\t0\t1416\t5\t5\tWestfall\t",
                                             "xp\t0\t10\t500\t0", "accept\t20\t7\t\t\t\t\t\t5\t100\t0"]),
    ("bbbb", 2): chunk("bbbb", 2, T0 + 4000, ["session\t0\tbbbb\tDwarf", "xp\t0\t60\t500\t0"]),
    ("old0", 9): chunk("old0", 9, T0 + 50, ["session\t0\told0\tHuman", "xp\t0\t1\t400\t0"]),
}
MAPS = {"1415": "1415\t3", "1416": "1416\t3"}


def write_wtf(root: Path, sv_keys) -> Path:
    """A WTF tree: the account's Polestar_Recorder.lua holds sv_keys' chunks."""
    acct = root / "WTF" / "Account" / "1#1"
    (acct / "SavedVariables").mkdir(parents=True)
    body = ",\n".join(f"[{i}] = {lua_str(CHUNKS[k])}" for i, k in enumerate(sv_keys, 1))
    maps = ",\n".join(f"[{k}] = {lua_str(v)}" for k, v in MAPS.items())
    (acct / "SavedVariables" / "Polestar_Recorder.lua").write_text(
        f'PolestarRecorderDB = {{\n["chunks"] = {{\n{body}\n}},\n["maps"] = {{\n{maps}\n}},\n}}\n')
    for name, cid in (("Hero-Alpha", "aaaa"), ("Hero-Beta", "bbbb")):
        d = acct / "Realm" / name / "SavedVariables"
        d.mkdir(parents=True)
        (d / "Polestar_Recorder.lua").write_text(f'PolestarRecorderCharDB = {{\n["id"] = "{cid}",\n}}\n')
    return root / "WTF"


def write_sent(d: Path, keys) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    rows = [{"at": (T0 + 1000) * 1000, "install": "inst" + k[0][:2], "seq": k[1], "text": CHUNKS[k]} for k in keys]
    (d / "sent.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows) + "not json\n")
    return d


def recorder_lines(shipper, capsys, wtf: Path, client_dir: Path | None) -> list[str]:
    shipper.CLIENT.clear()
    if client_dir is not None:
        shipper.CLIENT.update(shipper.client_chunks(client_dir))
    capsys.readouterr()
    shipper.extract_tree(wtf, {}, shipper.Out(dry=True))
    shipper.CLIENT.clear()
    lines = capsys.readouterr().out.splitlines()
    return sorted(ln for ln in lines if '"feed":"wow-recorder"' in ln)


@pytest.fixture
def no_addons(shipper, tmp_path, monkeypatch):
    monkeypatch.setattr(shipper, "WOW", tmp_path / "no-wow")
    monkeypatch.setattr(shipper, "NAMES", {})


def test_client_chunks_equal_savedvariables(shipper, no_addons, tmp_path, capsys):
    """Every chunk in the SavedVariables, or every chunk only in sent.jsonl,
    or a mix (chunk 2 from one source, 1 and 3 from the other): the same
    bytes out, durations across chunk boundaries included."""
    keys = list(CHUNKS)
    sv_all = recorder_lines(shipper, capsys, write_wtf(tmp_path / "a", keys), None)
    client_all = recorder_lines(shipper, capsys, write_wtf(tmp_path / "b", []), write_sent(tmp_path / "cb", keys))
    mixed_keys = [k for k in keys if k[1] == 2]
    mixed = recorder_lines(shipper, capsys, write_wtf(tmp_path / "c", mixed_keys),
                           write_sent(tmp_path / "cc", [k for k in keys if k not in mixed_keys]))
    both = recorder_lines(shipper, capsys, write_wtf(tmp_path / "d", keys), write_sent(tmp_path / "cd", keys))
    assert sv_all == client_all == mixed == both
    evs = {json.loads(ln)["source_id"]: json.loads(ln) for ln in sv_all}
    assert len(evs) == sum(len(t.split("\n")) - 1 for t in CHUNKS.values())
    # Mapped to the character folders; the unknown id keeps its own name.
    assert {e["char"] for e in evs.values()} == {"Hero-Alpha", "Hero-Beta", "charid-old0"}
    # The first zone lasts until Westfall in the next chunk: 300 + 6 s.
    assert evs["rec:aaaa:1:2"]["duration_s"] == 306.0
    assert evs["rec:aaaa:2:3"]["duration_s"] == 303.0  # step 2, since step 1 in chunk 1
    assert evs["rec:aaaa:2:2"]["xp_gained"] == 0 and evs["rec:aaaa:2:5"]["xp_gained"] == 850
    assert evs["rec:aaaa:3:2"]["xp_gained"] == 10 + 20  # a level went by
    assert evs["rec:aaaa:3:3"]["duration_s"] == 19
    assert evs["rec:bbbb:1:4"]["zone"] == "Westfall"
    logins = sorted(s for s, e in evs.items() if e.get("login"))
    assert logins == ["rec:aaaa:1:1", "rec:bbbb:1:1", "rec:bbbb:2:1", "rec:old0:9:1"]


def test_client_chunks_sent_once(shipper, no_addons, tmp_path, capsys):
    """What the SavedVariables path already sent isn't sent again from
    sent.jsonl, and a longer copy there sends only its new lines."""
    keys = list(CHUNKS)
    wtf = write_wtf(tmp_path / "a", [("aaaa", 1)])
    state: dict = {}
    shipper.CLIENT.clear()
    shipper.extract_tree(wtf, state, shipper.Out(dry=True))
    first = {i for ln in capsys.readouterr().out.splitlines() if (i := json.loads(ln)["source_id"]).startswith("rec:")}
    shipper.CLIENT.update(shipper.client_chunks(write_sent(tmp_path / "c", keys)))
    shipper.extract_tree(wtf, state, shipper.Out(dry=True))
    second = {i for ln in capsys.readouterr().out.splitlines() if (i := json.loads(ln)["source_id"]).startswith("rec:")}
    shipper.extract_tree(wtf, state, shipper.Out(dry=True))
    shipper.CLIENT.clear()
    assert '"feed":"wow-recorder"' not in capsys.readouterr().out
    assert first and second and not first & second
    assert len(first | second) == sum(len(t.split("\n")) - 1 for t in CHUNKS.values())


def test_client_missing_or_unreadable_is_empty(shipper, tmp_path):
    assert shipper.client_chunks(tmp_path / "nothing") == {}
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "sent.jsonl").write_text('{"text": "chunk\\tbad"}\n{"text": 5}\n[]\n')
    assert shipper.client_chunks(tmp_path / "x") == {}


def test_client_dir_per_os(shipper, monkeypatch, tmp_path):
    monkeypatch.setattr(shipper, "CONFIG", {})
    monkeypatch.setattr(shipper, "HOME", tmp_path)
    monkeypatch.setattr(shipper, "WINDOWS", True)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert shipper.client_dir() == tmp_path / "Roaming" / "gg.polestar.client"
    monkeypatch.setattr(shipper, "WINDOWS", False)
    monkeypatch.setattr(shipper.sys, "platform", "darwin")
    assert shipper.client_dir() == tmp_path / "Library" / "Application Support" / "gg.polestar.client"
    monkeypatch.setattr(shipper.sys, "platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert shipper.client_dir() == tmp_path / ".local" / "share" / "gg.polestar.client"
    monkeypatch.setattr(shipper, "CONFIG", {"polestar_client_dir": str(tmp_path / "elsewhere")})
    assert shipper.client_dir() == tmp_path / "elsewhere"


def test_ship_resumes_and_keeps_too_old(shipper, tmp_path, monkeypatch):
    """Posts of at most POST_MAX_BYTES; the spool file shrinks after each, so
    an interrupted first run resends nothing; events past the receiver's
    retention are kept aside, not posted into a store that drops them."""
    for name in ("SPOOL", "SENT", "REJECTED", "TOO_OLD"):
        monkeypatch.setattr(shipper, name, tmp_path / name.lower())
    monkeypatch.setattr(shipper, "LOG", tmp_path / "log")
    monkeypatch.setattr(shipper, "CONFIG", {"ship": {"url": "http://127.0.0.1:9/x", "retention_days": 90}})
    monkeypatch.setattr(shipper, "POST_MAX_BYTES", 300)
    monkeypatch.setattr(shipper.socket, "create_connection", lambda *a, **k: type("C", (), {"close": lambda s: None})())
    now = shipper.time.time()
    evs = [{"_time": shipper.iso(now - 100 * 86400), "feed": "wow-recorder", "source_id": "old"}]
    evs += [{"_time": shipper.iso(now - i), "feed": "wow-recorder", "source_id": f"n{i}", "pad": "x" * 60}
            for i in range(12)]
    shipper.SPOOL.mkdir()
    f = shipper.SPOOL / "a.ndjson"
    f.write_text("".join(json.dumps(e) + "\n" for e in evs))
    posted = []

    def post(url, headers, body):
        if len(posted) == 2:
            return None  # the network goes away after two posts
        posted.append(body)
        return 200

    monkeypatch.setattr(shipper, "post", post)
    shipper.ship()
    left = f.read_text().splitlines()
    assert len(posted) == 2 and len(left) == 12 - sum(b.count(b"\n") for b in posted)
    assert json.loads((shipper.TOO_OLD / "a.ndjson").read_text())["source_id"] == "old"
    monkeypatch.setattr(shipper, "post", lambda url, headers, body: posted.append(body) or 200)
    shipper.ship()
    assert not f.exists()
    ids = [json.loads(ln)["source_id"] for b in posted for ln in b.splitlines()]
    assert ids == [f"n{i}" for i in range(12)]


REPO = Path.home() / "WoWArchive" / "wtf"


@pytest.mark.real_home
@pytest.mark.skipif(not (REPO / ".git").is_dir(), reason="no WTF archive on this computer")
def test_real_overlap_archive_vs_client(shipper, no_addons, capsys):
    """On the player's own data: every chunk that both the WTF archive (any
    commit) and Polestar Client's sent.jsonl hold gives byte-identical events.
    Read only: git show into a temp folder."""
    client = shipper.client_chunks()
    if not client:
        pytest.skip("no Polestar Client sent.jsonl on this computer")

    def git(*a):
        return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, check=True)

    arch: dict = {}
    map_types: dict = {}
    for c in git("rev-list", "HEAD").stdout.decode().split():
        names = [p for p in git("ls-tree", "-r", "--name-only", c).stdout.decode().splitlines()
                 if p.count("/") == 3 and p.endswith(("/SavedVariables/Polestar_Recorder.lua",
                                                      "/SavedVariables/Polestar_Recorder.lua.bak"))]
        with tempfile.TemporaryDirectory() as tmp:
            for p in names:
                Path(tmp, os.path.basename(p)).write_bytes(git("show", f"{c}:{p}").stdout)
            chunks, types = shipper.load_chunks([Path(tmp)])
        map_types.update(types)
        shipper.merge_chunks(arch, chunks)
    both = set(arch) & set(client)
    if not both:
        pytest.skip("no chunk in both the archive and sent.jsonl")
    char_ids = {cid: f"char-{cid}" for cid, _ in both}

    def events(chunks):
        capsys.readouterr()
        shipper.extract_recorder(shipper.sessions_of({k: chunks[k] for k in both}), map_types, char_ids, {},
                                 shipper.Out(dry=True))
        return capsys.readouterr().out.encode()

    from_archive, from_client = events(arch), events(client)
    assert from_archive.count(b"\n") >= len(both)
    assert from_archive == from_client


def test_kept_copy_outlives_the_client_pruning(shipper, no_addons, tmp_path, monkeypatch, capsys):
    """The client prunes sent.jsonl to 8 days; our copy keeps every chunk
    once (by install, charId, seq), the archive step leaves it in the repo,
    and a fresh state still sends the pruned chunks from it."""
    client = tmp_path / "client"
    monkeypatch.setattr(shipper, "CONFIG", {"polestar_client_dir": str(client)})
    monkeypatch.setattr(shipper, "REPO", tmp_path / "repo")
    monkeypatch.setattr(shipper, "LOG", tmp_path / "log")
    keys = list(CHUNKS)
    write_sent(client, keys)
    assert shipper.keep_client() == len(keys)
    assert shipper.keep_client() == 0
    write_sent(client, keys[-2:])  # a week later: the older chunks are gone
    assert shipper.keep_client() == 0
    kept = (tmp_path / "repo" / "_polestar-client" / "sent.jsonl").read_text().splitlines()
    assert len(kept) == len(keys)

    # A fresh state: every kept chunk is read, and gives what the full file gave.
    wtf = write_wtf(tmp_path / "w", [])
    state: dict = {}
    shipper.load_client(state)
    assert set(shipper.CLIENT) == set(keys)
    capsys.readouterr()
    shipper.extract_tree(wtf, state, shipper.Out(dry=True))
    pruned = sorted(ln for ln in capsys.readouterr().out.splitlines() if '"feed":"wow-recorder"' in ln)
    assert pruned == recorder_lines(shipper, capsys, write_wtf(tmp_path / "x", keys), None)
    # Once sent, the kept chunks older than the live file aren't read again.
    shipper.load_client(state)
    assert set(shipper.CLIENT) == {k for k in keys if CHUNKS[k].split("\t")[3] >= str(T0 + 50)}
    shipper.CLIENT.clear()


@pytest.mark.skipif(not __import__("shutil").which("git"), reason="no git")
def test_archive_keeps_the_kept_copy(shipper, tmp_path, monkeypatch):
    monkeypatch.setattr(shipper, "WOW", tmp_path / "wow")
    monkeypatch.setattr(shipper, "REPO", tmp_path / "repo")
    monkeypatch.setattr(shipper, "LOG", tmp_path / "log")
    (tmp_path / "wow" / "WTF").mkdir(parents=True)
    (tmp_path / "wow" / "WTF" / "Config.wtf").write_text("x\n")
    (tmp_path / "repo" / "_polestar-client").mkdir(parents=True)
    (tmp_path / "repo" / "_polestar-client" / "sent.jsonl").write_text("{}\n")
    assert shipper.archive()
    tracked = shipper.git("ls-files").stdout.split()
    assert "_polestar-client/sent.jsonl" in tracked and "Config.wtf" in tracked
