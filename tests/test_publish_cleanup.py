"""The publisher's build folders: a failed build gives its disk back and
leaves the site as it was; a killed run's leftovers go at the next run; an
active build and the live bundle stay (the demo kept two partial builds
after a successful retry, 2026-11-19)."""

import io
import os
import time
from contextlib import redirect_stdout

import pytest

from tests.test_publish import FakeCatalog, publish_items, site  # noqa: F401 - the fixture
from wowstats import publish

pytest.importorskip("cryptography")


def run(env, force=False):
    with redirect_stdout(io.StringIO()):
        return publish.publish(env, force=force)


def age(path, seconds):
    """Every entry of `path` last written `seconds` ago."""
    t = time.time() - seconds
    for p in [path, *path.rglob("*")]:
        os.utime(p, (t, t), follow_symlinks=False)


def test_a_failed_build_keeps_the_site_and_leaves_no_folder(site, monkeypatch):  # noqa: F811
    env, _, _, cur = site
    out = cur.parent
    live = cur.resolve()
    before = sorted(p.name for p in out.iterdir())

    def boom(*a, **k):
        raise publish.PublishError("store went away mid-build")

    monkeypatch.setattr(publish, "check_names", boom)
    with pytest.raises(publish.PublishError):
        run(env, force=True)
    assert cur.resolve() == live and (cur / "public" / "index.html").is_file()
    assert not list(out.glob(".build-*"))
    assert sorted(p.name for p in out.iterdir()) == before


def test_a_failed_first_build_leaves_nothing(tmp_path, monkeypatch):
    class Down(FakeCatalog):
        cards: dict = {}

        def many(self, ids):
            return {}

    with pytest.raises(publish.PublishError):
        publish_items(tmp_path, monkeypatch, Down())
    assert not list((tmp_path / "site-gap").glob(".build-*"))


def test_the_next_run_removes_a_dead_build_and_keeps_an_active_one(site):  # noqa: F811
    env, _, _, cur = site
    out = cur.parent
    live = cur.resolve()
    dead, active = out / ".build-100", out / ".build-200"
    for b in (dead, active):
        (b / "public" / "data").mkdir(parents=True)
        (b / "public" / "data" / "chars.json").write_text("{}")
    age(dead, publish.STALE_BUILD_S + 60)
    # Old folders, but a file written a minute ago: another publisher at work.
    age(active, publish.STALE_BUILD_S + 60)
    (active / "public" / "data" / "char.json").write_text("{}")

    res = run(env)  # nothing new: no build, the sweep still runs
    assert not res["built"]
    assert not dead.exists()
    assert active.is_dir()
    assert cur.resolve() == live and (cur / "public" / "index.html").is_file()


def test_sweep_never_touches_bundles_or_current(tmp_path):
    out = tmp_path / "site"
    (out / "b1" / "public").mkdir(parents=True)
    (out / "current").symlink_to("b1")
    (out / ".build-5").mkdir()
    for p in (out / "b1", out / ".build-5"):
        age(p, publish.STALE_BUILD_S * 10)
    assert publish.sweep_builds(out) == [".build-5"]
    assert (out / "b1" / "public").is_dir() and (out / "current").resolve() == (out / "b1").resolve()


def test_a_new_build_never_takes_an_active_builds_name(site, monkeypatch):  # noqa: F811
    env, _, _, cur = site
    out = cur.parent
    now = int(time.time()) + 5
    active = out / f".build-{now}"
    active.mkdir()
    (active / "marker").write_text("busy")
    monkeypatch.setattr(publish.time, "time", lambda: float(now))
    res = run(env, force=True)
    assert res["build"] == f"b{now + 1}"
    assert (active / "marker").read_text() == "busy"
