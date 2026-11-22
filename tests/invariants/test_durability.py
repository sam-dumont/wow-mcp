"""What reaches the disk, and when (property 12, the shipper's half).

A rename is an entry in a directory, not data in the file: fsync on the
file keeps its bytes, and only fsync on the directory keeps the rename.
Without it a power cut can keep the state's rename and lose the spool's
(two directories, either order), and the state then says events are
spooled that no file holds. An append to sent/ that isn't flushed can be
lost after the spool was shortened. Review 6.

os.fsync and the renames are recorded here; nothing proves a real power
cut (docs/guarantees.md, "What the crash tests prove"). Made-up events
only.
"""

from __future__ import annotations

import gzip
import json
import os
import stat
from pathlib import Path

import pytest

from tests.test_recorder_install import pin, release_zip
from tests.test_shipper_restore import Store

POSIX = pytest.mark.skipif(os.name == "nt", reason="no directory fsync on Windows (fsync_dir says why)")


@pytest.fixture
def calls(shipper, monkeypatch):
    """Every fsync (file or directory, by inode) and rename, in order."""
    out: list[tuple] = []
    real_fsync, real_replace, real_rename = os.fsync, os.replace, Path.rename

    def fsync(fd):
        st = os.fstat(fd)
        out.append(("fsync", "dir" if stat.S_ISDIR(st.st_mode) else "file", st.st_ino))
        return real_fsync(fd)

    def moved(src, dst, real):
        ino = os.stat(src).st_ino
        res = real(src, dst)
        out.append(("rename", Path(src).name, ino, os.stat(os.path.dirname(os.path.abspath(dst))).st_ino))
        return res

    monkeypatch.setattr(shipper.os, "fsync", fsync)
    monkeypatch.setattr(shipper.os, "replace", lambda a, b: moved(a, b, real_replace))
    monkeypatch.setattr(Path, "rename", lambda a, b: moved(a, b, real_rename))
    return out


def ino(p: Path) -> int:
    return os.stat(p).st_ino


def assert_durable(calls: list[tuple], kept: tuple[str, ...] = ()) -> None:
    """Each rename: its file flushed before it (unless it was on the disk
    already: `kept`), its directory flushed after it."""
    renames = [(i, c) for i, c in enumerate(calls) if c[0] == "rename"]
    assert renames, calls
    for i, (_, name, file_ino, dir_ino) in renames:
        if name not in kept:  # a file, or a folder (the Recorder's) after its files
            assert [c for c in calls[:i] if c[0] == "fsync" and c[2] == file_ino], \
                f"{name} renamed before its data was flushed: {calls}"
        assert ("fsync", "dir", dir_ino) in calls[i + 1:], f"{name}'s rename never flushed to its directory: {calls}"


def event(n: int) -> dict:
    return {"_time": f"2026-11-17T15:14:{n % 60:02d}.120Z", "feed": "wow-level", "char": "Hero-Alpha",
            "level": n, "source_id": f"level:Hero-Alpha:{n}"}


@POSIX
def test_the_spool_rename_reaches_the_disk_before_the_state(shipper, tmp_path, monkeypatch, calls):
    """Two directories: the spool's rename must be on the disk before the
    state's, or a power cut can keep the state alone."""
    monkeypatch.setattr(shipper, "SPOOL", tmp_path / "spool")
    monkeypatch.setattr(shipper, "STATE", tmp_path / "state.json")
    out = shipper.Out(dry=False)
    out.emit(event(3))
    assert out.close() is not None
    shipper.save_state({"rec": {"aaaa:1": 3}})
    shipper.save_state({"rec": {"aaaa:1": 4}})
    assert_durable(calls, kept=("state.json",))
    spool_dir = ("fsync", "dir", ino(tmp_path / "spool"))
    first_state = next(i for i, c in enumerate(calls) if c[:2] == ("rename", ".state.json.tmp"))
    assert spool_dir in calls[:first_state], f"the state counts on a spool rename not on the disk: {calls}"


@POSIX
def test_a_shortened_spool_file_reaches_the_disk(shipper, tmp_path, calls):
    f = tmp_path / "x.ndjson"
    f.write_bytes(b"a\nb\n")
    shipper.rewrite(f, [b"b\n"])
    assert f.read_bytes() == b"b\n"
    assert_durable(calls)


@POSIX
def test_every_sent_append_reaches_the_disk(shipper, tmp_path, monkeypatch, calls):
    """One fsync of the sent/ file per POST, after its member is written and
    before the spool is shortened; the new file's directory entry too."""
    store = Store()
    try:
        arch = tmp_path / "arch"
        (tmp_path / "config.toml").write_text(
            f"wow_dir = '{tmp_path / 'no-wow'}'\narchive_dir = '{arch}'\nlog_file = '{tmp_path / 'log'}'\n"
            f"[ship]\nurl = '{store.url}'\nuser = 'u'\npassword = 'password-long-enough'\n")
        monkeypatch.setenv("WOW_STATS_CONFIG", str(tmp_path / "config.toml"))
        shipper.configure()
        lines = [json.dumps(event(n), separators=(",", ":")).encode() + b"\n" for n in range(60)]
        shipper.SPOOL.mkdir(parents=True)
        spool = shipper.SPOOL / "20261117T151403000000Z.ndjson"
        spool.write_bytes(b"".join(lines))
        monkeypatch.setattr(shipper, "POST_MAX_BYTES", len(b"".join(lines)) // 3 + 1)
        real_post = shipper.post
        monkeypatch.setattr(shipper, "post", lambda *a: (calls.append(("post",)), real_post(*a))[1])
        shipper.ship()
        sent = shipper.SENT / (spool.name + ".gz")
        assert gzip.decompress(sent.read_bytes()) == b"".join(lines)
        posts = [i for i, c in enumerate(calls) if c == ("post",)]
        assert len(posts) > 1
        flushed = [i for i, c in enumerate(calls) if c == ("fsync", "file", ino(sent))]
        assert len(flushed) == len(posts), f"one fsync per POST, not {len(flushed)}: {calls}"
        for p, nxt in zip(posts, [*posts[1:], len(calls)], strict=True):
            f = [i for i in flushed if p < i < nxt]
            assert f, f"the member after POST {p} never flushed: {calls}"
            shortened = [i for i, c in enumerate(calls) if c[0] == "rename" and p < i < nxt]
            assert all(f[0] < s for s in shortened), f"the spool shortened before the member reached the disk: {calls}"
        assert ("fsync", "dir", ino(shipper.SENT)) in calls[posts[0]:posts[1]], "the new sent/ file's entry"
    finally:
        store.srv.shutdown()
        store.srv.server_close()


@POSIX
def test_an_update_reaches_the_disk_before_it_replaces_the_script(shipper, tmp_path, monkeypatch, calls):
    me = tmp_path / "wow_stats.py"
    me.write_text("# old\n")
    monkeypatch.setattr(shipper, "SELF", me)
    shipper.apply_update(b"# new\n")
    assert me.read_text() == "# new\n"
    assert ("fsync", "file", ino(me.with_name(me.name + ".prev"))) in calls, "the kept copy"
    assert_durable(calls)


@POSIX
def test_a_recorder_install_reaches_the_disk_before_the_swap(shipper, tmp_path, monkeypatch, calls):
    wow = tmp_path / "_forever_"
    (wow / "WTF").mkdir(parents=True)
    fetch, _ = pin(shipper, monkeypatch, release_zip("9.9.9"))
    shipper.install_recorder(wow, fetch)
    calls.clear()
    fetch, _ = pin(shipper, monkeypatch, release_zip("9.9.10"), version="9.9.10")
    shipper.install_recorder(wow, fetch)
    addon = wow / "Interface" / "AddOns" / "Polestar_Recorder"
    for f in addon.iterdir():
        assert ("fsync", "file", ino(f)) in calls, f"{f.name} not on the disk before the swap"
    # The old folder's rename out of the way needs no flush of its own: its files are on the disk.
    assert_durable([c for c in calls if c[:2] != ("rename", "Polestar_Recorder") or c[2] == ino(addon)])


def test_windows_skips_the_directory_fsync(shipper, tmp_path, monkeypatch, calls):
    """No portable directory fsync there: os.open on a folder fails. The
    rename still happens and nothing raises."""
    monkeypatch.setattr(shipper, "WINDOWS", True)
    opened: list = []
    real_open = os.open
    monkeypatch.setattr(shipper.os, "open", lambda p, *a, **k: (opened.append(p), real_open(p, *a, **k))[1])
    monkeypatch.setattr(shipper, "STATE", tmp_path / "state.json")
    shipper.save_state({"a": 1})
    assert json.loads((tmp_path / "state.json").read_text()) == {"a": 1}
    assert not [c for c in calls if c[:2] == ("fsync", "dir")]
    assert not [p for p in opened if Path(p).is_dir()]
