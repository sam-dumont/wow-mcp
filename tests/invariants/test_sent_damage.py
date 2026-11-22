"""A damaged copy in sent/ (property 20).

The shipper appends one gzip member to sent/<spool file>.gz per POST the
server accepted. A crash in the middle of that append leaves a member cut
short, at the end of the file or, once the next run sends the same spool
file again, in its middle. `--replay` keeps every member that reads whole
and says which file was damaged and by how much, with a non-zero exit:
never "sent again" over silent loss (review 6: one 1 MiB read raised on
the cut member and dropped the whole members before it, then exited 0).

One sent/ file per test, so no other file's events can hide a loss.
Made-up events only.
"""

from __future__ import annotations

import gzip
import json

import pytest

from tests.test_shipper_restore import Store


def events(tag: str, n: int) -> list[bytes]:
    return [json.dumps({"_time": f"2026-11-15T19:0{i % 10}:00.000Z", "feed": "wow-recorder",
                        "char": "Hero-Alpha", "source_id": f"{tag}-{i}"}, separators=(",", ":")).encode() + b"\n"
            for i in range(n)]


def ids(lines: list[bytes]) -> set[str]:
    return {json.loads(ln)["source_id"] for ln in lines}


@pytest.fixture
def replay(shipper, tmp_path, monkeypatch):
    """(store, sent folder, run, log text): a PC whose server is lost."""
    store = Store()
    arch = tmp_path / "arch"
    (arch / "sent").mkdir(parents=True)
    (tmp_path / "config.toml").write_text(
        f"wow_dir = '{tmp_path / 'no-wow'}'\narchive_dir = '{arch}'\nlog_file = '{tmp_path / 'log'}'\n"
        "auto_update = false\n"
        f"[ship]\nurl = '{store.url}'\nuser = 'u'\npassword = 'password-long-enough'\n")
    monkeypatch.setenv("WOW_STATS_CONFIG", str(tmp_path / "config.toml"))

    def run() -> int:
        monkeypatch.setattr(shipper.sys, "argv", ["wow_stats.py", "--replay"])
        return shipper.main()

    yield store, arch / "sent", run, lambda: (tmp_path / "log").read_text()
    store.srv.shutdown()
    store.srv.server_close()


def cut(lines: list[bytes]) -> bytes:
    """One POST's member, cut half way by a crash."""
    whole = gzip.compress(b"".join(lines))
    return whole[: len(whole) // 2]


def test_a_cut_tail_keeps_every_whole_member_and_says_so(replay):
    store, sent, run, log = replay
    a, b, c = events("a", 40), events("b", 30), events("c", 50)
    f = sent / "20261115T190000000000Z.ndjson.gz"
    f.write_bytes(gzip.compress(b"".join(a)) + gzip.compress(b"".join(b)) + cut(c))
    code = run()
    assert {e["source_id"] for e in store.lines} == ids(a + b)
    assert code != 0, "a damaged sent/ file exits 0"
    text = log()
    assert f.name in text and "damaged" in text and f"{len(cut(c))} bytes" in text


def test_a_cut_member_between_two_whole_ones_loses_only_itself(replay):
    """The next run sent the same spool file again after the crash: its
    member comes after the cut one."""
    store, sent, run, log = replay
    a, b, c = events("a", 40), events("b", 30), events("c", 50)
    f = sent / "20261115T190000000000Z.ndjson.gz"
    f.write_bytes(gzip.compress(b"".join(a)) + cut(b) + gzip.compress(b"".join(c)))
    code = run()
    assert {e["source_id"] for e in store.lines} == ids(a + c)
    assert code != 0
    assert f"{len(cut(b))} bytes" in log()


def test_garbage_after_the_members_is_damage_too(replay):
    store, sent, run, log = replay
    a = events("a", 20)
    (sent / "x.ndjson.gz").write_bytes(gzip.compress(b"".join(a)) + b"not gzip at all")
    assert run() != 0
    assert {e["source_id"] for e in store.lines} == ids(a)
    assert "15 bytes" in log()


class Killed(BaseException):
    """kill -9: no handler in the shipper runs."""


def test_a_kill_mid_append_then_the_restart(replay, shipper, monkeypatch):
    """The crash point itself: a spool file goes in three POSTs; the shipper
    dies while it appends the second one's member (half of it reached the
    file), before the spool is shortened. The restart sends the second and
    third parts again. The server has every event; --replay sends every one
    too (the restart's member holds the cut one's events) and still reports
    the cut bytes, since it can't know a later member repeats them."""
    store, sent, run, log = replay
    shipper.configure()
    a = events("a", 60)
    shipper.SPOOL.mkdir(parents=True)
    spool = shipper.SPOOL / "20261115T190000000000Z.ndjson"
    spool.write_bytes(b"".join(a))
    monkeypatch.setattr(shipper, "POST_MAX_BYTES", len(b"".join(a)) // 3 + 1)
    orig, calls = shipper.rewrite, []

    def rewrite(f, lines):  # runs right after an append, before the spool is shortened
        calls.append(f)
        if len(calls) == 2:
            g = sent / (spool.name + ".gz")
            members = len(gzip.compress(b"".join(a[20:40])))  # the second part's member, about
            g.write_bytes(g.read_bytes()[: -(members // 2)])
            raise Killed
        return orig(f, lines)

    monkeypatch.setattr(shipper, "rewrite", rewrite)
    with pytest.raises(Killed):
        shipper.ship()
    monkeypatch.setattr(shipper, "rewrite", orig)
    shipper.ship()
    assert not spool.exists()
    assert {e["source_id"] for e in store.lines} == ids(a)
    store.lines.clear()
    assert run() != 0
    assert {e["source_id"] for e in store.lines} == ids(a)
    assert "damaged" in log()


def test_a_whole_file_replays_with_exit_0(replay):
    store, sent, run, log = replay
    a, b = events("a", 40), events("b", 30)
    (sent / "x.ndjson.gz").write_bytes(gzip.compress(b"".join(a)) + gzip.compress(b"".join(b)) + b"\0" * 8)
    assert run() == 0
    assert sorted(e["source_id"] for e in store.lines) == sorted(ids(a + b))
    assert "damaged" not in log()
