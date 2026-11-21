"""Battle.net freshness and removals, end to end: the exporter's pushes into
a vm-wow that answers `last_over_time(...[2d])` like VictoriaMetrics, read
back through the Store (review 3, item 3).

- A refresh that fails half way (a 503 on /equipment) must not make the
  character read as fresh: each part keeps its own last success, and the
  character's "as of" is the oldest of them.
- What the latest complete read no longer has (a talent dropped in a
  respec, a slot emptied) is gone for the readers at once, not after the
  2-day window the readers use to ride out an exporter restart."""

from __future__ import annotations

import json
import re
import time
from types import SimpleNamespace

import pytest

from wowstats import exporter
from wowstats.stores import Store

from .test_exporter import GEAR, LOOK, PRIEST, PRIEST_SPECS, _t, fake_api

P = "/profile/wow/character/testpriest-fixture"
CHAR = "Testpriest-Fixture"
LINE = re.compile(r'^([a-z_]+)\{(.*)\} (\S+)$')
LABEL = re.compile(r'([a-z_]+)="((?:[^"\\]|\\.)*)"')
QUERY = re.compile(r"^(t?last_over_time)\(([a-z_]+)\{([^}]*)\}\[2d\]\)$")


class FakeVMWow:
    """vm-wow as VictoriaMetrics would hold the exporter's pushes: samples
    stamped at push time, `last_over_time(name{matchers}[2d])` answering the
    newest value of every series with a sample in the window."""

    def __init__(self):
        self.samples: dict[tuple, list[tuple[float, float]]] = {}

    def push(self, now: float) -> None:
        for line in exporter.render_data(now=now).splitlines():
            m = LINE.match(line)
            assert m, line
            labels = tuple(sorted((k, v) for k, v in LABEL.findall(m.group(2))))
            self.samples.setdefault((m.group(1), labels), []).append((now, float(m.group(3))))

    def query(self, q: str, now: float) -> list[dict]:
        m = QUERY.match(q)
        assert m, q
        want = {k: json.loads(v) for k, v in re.findall(r'([a-z_]+)=("(?:[^"\\]|\\.)*")', m.group(3))}
        out = []
        for (name, labels), pts in self.samples.items():
            lab = dict(labels)
            if name != m.group(2) or any(lab.get(k) != v for k, v in want.items()):
                continue
            inside = [p for p in pts if now - 2 * 86400 < p[0] <= now]
            if inside:
                t, v = inside[-1]
                out.append({"metric": lab, "value": [now, str(t if m.group(1) == "tlast_over_time" else v)]})
        return out


class Reader(Store):
    def __init__(self, vm: FakeVMWow, clock: list[float]):
        super().__init__("http://vl.invalid", vm_url="http://vm.invalid", account_id=0)
        self.vm, self.clock = vm, clock

    def vm_query(self, q: str) -> list[dict]:
        return self.vm.query(q, self.clock[0])

    profile_query = vm_query


@pytest.fixture
def world(monkeypatch):
    exporter.configure({"BNET_CLIENT_ID_FILE": "/x", "BNET_CLIENT_SECRET_FILE": "/y",
                        "PROFILE_CHARACTERS": CHAR})
    exporter.data.clear()
    # Close to now: the readers hide anything read 30 days ago or more.
    clock = [float(int(time.time()) - 6 * 3600)]
    monkeypatch.setattr(exporter, "time", SimpleNamespace(time=lambda: clock[0], strftime=time.strftime,
                                                          gmtime=time.gmtime))
    vm = FakeVMWow()

    def run(minutes: int) -> None:
        """The exporter's push loop for `minutes`, one push a minute."""
        for _ in range(minutes):
            clock[0] += 60
            vm.push(clock[0])

    return SimpleNamespace(clock=clock, vm=vm, run=run, reader=Reader(vm, clock))


ALL = {P: PRIEST, P + "/appearance": LOOK, P + "/equipment": GEAR, P + "/specializations": PRIEST_SPECS,
       P + "/achievements": {"achievements": []}}


def test_a_503_on_equipment_keeps_the_character_as_of_its_oldest_part(world, monkeypatch):
    monkeypatch.setattr(exporter, "api_get", fake_api(ALL, []))
    t0 = world.clock[0]
    assert exporter.poll_profile() == "1 of 1 characters"
    world.run(120)
    assert world.reader.bnet_read_at(CHAR) == t0

    # Two hours later: the profile answers, /equipment fails with a 503.
    def flaky(path, namespace, endpoint, reduce=None):
        if path.endswith("/equipment"):
            raise exporter.ApiError(503, endpoint)
        return ALL.get(path)

    monkeypatch.setattr(exporter, "api_get", flaky)
    assert exporter.run_task("profile") is False
    world.run(5)
    # The equipment is still the one read at t0: the character is "as of" t0.
    assert world.reader.bnet_read_at(CHAR) == t0
    assert world.reader.character_profile(CHAR)["refreshed"] == world.reader.character_talents(CHAR)["refreshed"]
    assert world.reader.character_equipment(CHAR)["main_hand"]["item_id"] == 1317
    # Equipment answers again: the whole character is fresh.
    monkeypatch.setattr(exporter, "api_get", fake_api(ALL, []))
    t1 = world.clock[0]
    exporter.poll_profile()
    world.run(1)
    assert world.reader.bnet_read_at(CHAR) == t1


def test_a_failed_part_keeps_its_own_series(world, monkeypatch):
    """The parts read before the failure are fresh, the failed one keeps
    what it had (still pushed while it is recent), none is dropped."""
    monkeypatch.setattr(exporter, "api_get", fake_api(ALL, []))
    exporter.poll_profile()
    world.run(120)

    def flaky(path, namespace, endpoint, reduce=None):
        if path.endswith("/equipment"):
            raise exporter.ApiError(503, endpoint)
        return ALL.get(path)

    monkeypatch.setattr(exporter, "api_get", flaky)
    exporter.run_task("profile")
    out = exporter.render_data()
    assert 'slot="main_hand"' in out and 'stat="INTELLECT"' in out
    assert 'wow_character_talent_rank{' in out


def test_what_the_latest_read_dropped_is_gone_for_the_readers(world, monkeypatch):
    monkeypatch.setattr(exporter, "api_get", fake_api(ALL, []))
    exporter.poll_profile()
    world.run(120)
    eq = world.reader.character_equipment(CHAR)
    assert set(eq) == {"main_hand", "back"} and eq["back"]["stats"] == {"INTELLECT": 4.0, "SPIRIT": 3.0}
    assert any(r["name"] == "Spirit Tap" for r in world.reader.character_talents(CHAR)["ranks"])

    # A respec (Spirit Tap gone) and the cloak taken off.
    respec = json.loads(json.dumps(PRIEST_SPECS))
    shadow = respec["specialization_groups"][0]["specializations"][1]
    shadow["talents"] = [_t("Improved Shadow Word: Pain", 15275, 2)]
    shadow["spent_points"] = 2
    gear = {"equipped_items": [GEAR["equipped_items"][0]]}
    monkeypatch.setattr(exporter, "api_get", fake_api(
        {**ALL, P + "/specializations": respec, P + "/equipment": gear}, []))
    exporter.poll_profile()
    world.run(5)
    assert set(world.reader.character_equipment(CHAR)) == {"main_hand"}
    tal = world.reader.character_talents(CHAR)
    assert not any(r["name"] == "Spirit Tap" for r in tal["ranks"])
    assert [t["points"] for t in tal["trees"]] == [16, 2, 0]


def test_a_reader_still_finds_the_last_read_while_the_exporter_is_down(world, monkeypatch):
    """The 2-day window is there for an exporter restart or outage: with
    nothing pushed for a day, the last complete read is still what shows."""
    monkeypatch.setattr(exporter, "api_get", fake_api(ALL, []))
    exporter.poll_profile()
    world.run(30)
    world.clock[0] += 86400  # nothing pushed for a day
    assert set(world.reader.character_equipment(CHAR)) == {"main_hand", "back"}
    assert world.reader.character_talents(CHAR)["trees"][0]["points"] == 16


def test_after_an_exporter_restart_a_half_read_falls_back_to_the_last_complete_one(world, monkeypatch):
    """An exporter restart empties its memory. If the first read after it
    fails half way (a 503 on /equipment), the new snapshot holds only the
    parts that answered and no refreshed time: the readers keep the last
    complete snapshot (still in vm-wow's 2-day window), flagged with its
    age, instead of showing no Battle.net data at all."""
    monkeypatch.setattr(exporter, "api_get", fake_api(ALL, []))
    t0 = world.clock[0]
    exporter.poll_profile()
    world.run(120)

    exporter.data.clear()  # the restart
    exporter.snapshots.clear()

    def flaky(path, namespace, endpoint, reduce=None):
        if path.endswith("/equipment"):
            raise exporter.ApiError(503, endpoint)
        return ALL.get(path)

    monkeypatch.setattr(exporter, "api_get", flaky)
    assert exporter.run_task("profile") is False
    world.run(5)
    r = world.reader
    assert r.bnet_read_at(CHAR) == t0
    assert set(r.character_equipment(CHAR)) == {"main_hand", "back"}
    assert r.character_talents(CHAR)["trees"][0]["points"] == 16
    assert r.character_profile(CHAR) is not None
    st = r.bnet_state(CHAR)
    assert st == {"as_of": t0, "fallback": True}

    # The next complete read is the one shown again, no longer a fallback.
    monkeypatch.setattr(exporter, "api_get", fake_api(ALL, []))
    t1 = world.clock[0]
    exporter.poll_profile()
    world.run(1)
    assert r.bnet_read_at(CHAR) == t1
    assert r.bnet_state(CHAR) == {"as_of": t1, "fallback": False}


def test_a_first_read_that_never_completed_shows_nothing(world, monkeypatch):
    """No complete snapshot at all (the very first read failed half way):
    nothing to fall back to, so no Battle.net data, as before."""
    def flaky(path, namespace, endpoint, reduce=None):
        if path.endswith("/equipment"):
            raise exporter.ApiError(503, endpoint)
        return ALL.get(path)

    monkeypatch.setattr(exporter, "api_get", flaky)
    exporter.run_task("profile")
    world.run(5)
    assert world.reader.bnet_read_at(CHAR) is None
    assert world.reader.bnet_state(CHAR) is None


def test_a_vm_wow_error_is_never_never_read(world, monkeypatch):
    """vm-wow failing after a good read is an error or a stale state, never
    "Battle.net hasn't read this character" (#92's matrix: the exporter
    readers turned the error into the never-read answer). Coverage says
    unavailable as of the last good read, the talents and the Legacy points
    say the store didn't answer, and none of them asks to wait for a first read."""
    from datetime import UTC, datetime

    from wowstats import coverage, talents
    from wowstats import views as V
    from wowstats.stores import SourceError

    monkeypatch.setattr(exporter, "api_get", fake_api(ALL, []))
    t0 = world.clock[0]
    exporter.poll_profile()
    world.run(5)
    r = world.reader
    r.vm_url = r.profile_vm_url = "http://vm.invalid"
    assert coverage.battlenet(coverage.bnet_of(r, CHAR))["state"] == "complete"
    v = V.Views(r)
    cat = talents.shared().get()
    assert v.bnet_talents(CHAR)["trees"][0]["points"] == 16

    def down(q):
        raise SourceError("vm.invalid: 503")

    monkeypatch.setattr(r, "vm_query", down)
    monkeypatch.setattr(r, "profile_query", down)
    cov = coverage.battlenet(coverage.bnet_of(r, CHAR))
    assert cov["state"] in ("unavailable", "partial"), cov
    assert cov.get("as_of") == datetime.fromtimestamp(t0, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert cov.get("fix") != coverage.BNET_NEVER
    tal = talents.from_battlenet(cat, "PRIEST", v.bnet_talents(CHAR))[0]
    assert not tal["available"] and tal["why"] != talents.NO_TALENTS and "answer" in tal["why"], tal["why"]
    leg = talents.legacy({"legacy_sources": [{"amount": 1, "requirement": "x", "achievements": [1]}]},
                         v.bnet_achievements())
    assert leg["why"] != talents.LEGACY_UNKNOWN and "answer" in leg["why"], leg["why"]
