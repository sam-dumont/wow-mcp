"""Battle.net data per person (security review 0.6.0 S1): the exporter
labels every profile series with the account whose character it is, every
reader asks for its own account, and a name two people claim goes to the
owner or to nobody. Two friends here share one character name; each must
read only what was fetched for them, and neither the other's nor the
owner's."""

from __future__ import annotations

import json
import re
import sys
import time
from types import SimpleNamespace

from wowstats import tenancy
from wowstats.stores import Store

SHARED = "Shared-Fixture"
SERIES = re.compile(r"([a-z_]+)\{([^}]*)\}")
LABEL = re.compile(r'([a-z_]+)=("(?:[^"\\]|\\.)*")')


def _series(acct: str, name: str, ilvl: float, option_choice: int, tree: str) -> list[tuple[str, dict, float]]:
    who = {"character": name, "account_id": acct}
    return [
        ("wow_character_item_level", {**who, "kind": "equipped"}, ilvl),
        ("wow_character_render", {**who, "asset": "main-raw",
                                  "url": f"https://render.worldofwarcraft.com/x/{acct}-main-raw.png"}, 1795000000.0),
        ("wow_character_customization", {**who, "option_id": "80"}, float(option_choice)),
        ("wow_character_profile", {**who, "race_id": "7", "gender": "male", "in_guild": "true",
                                   "guild": f"Guild {acct}"}, 30.0),
        ("wow_character_profile_found", who, 1.0),
        ("wow_character_achievement", {**who, "achievement_id": str(61000 + int(acct))}, 1795000000.0),
        ("wow_character_achievements_read", who, 1.0),
        # Read an hour ago: Battle.net data older than 30 days is never shown.
        ("wow_character_profile_refreshed_timestamp_seconds", who, time.time() - 3600),
        ("wow_character_equipped", {**who, "slot": "head"}, 1000.0 + int(acct)),
        ("wow_character_equipped_stat", {**who, "slot": "head", "stat": "INTELLECT"}, float(acct)),
        ("wow_character_talent_points", {**who, "tree": tree, "order": "0"}, 5.0),
        ("wow_character_talent_rank", {**who, "spell_id": "100", "talent": tree}, 1.0),
    ]


# vm-wow as the exporter fills it: friend 1 and friend 2 each with their own
# copy of SHARED (as an older exporter or a forged name could leave), and
# the owner's character.
VM = (_series("1", SHARED, 21.0, 1101, "Holy") + _series("2", SHARED, 22.0, 1202, "Shadow")
      + _series("0", "Ownerchar-Fixture", 40.0, 1000, "Discipline"))


class FakeVM(Store):
    """A Store whose vm-wow answers the selector as VictoriaMetrics does:
    every label matcher must hold."""

    def __init__(self, account_id: int):
        super().__init__("http://vl.invalid", vm_url="http://vm.invalid", account_id=account_id)
        self.queries: list[str] = []

    def vm_query(self, q: str) -> list[dict]:
        self.queries.append(q)
        m = SERIES.search(q)
        assert m, q
        want = {k: json.loads(v) for k, v in LABEL.findall(m.group(2))}
        return [{"metric": {"__name__": n, **lab}, "value": [1795000000, str(v)]}
                for n, lab, v in VM if n == m.group(1) and all(lab.get(k) == v for k, v in want.items())]

    profile_query = vm_query


def test_each_friend_reads_only_their_own_copy_of_a_shared_name():
    one, two = FakeVM(1), FakeVM(2)
    assert one.character_armory(SHARED)["item_level"] == 21.0
    assert two.character_armory(SHARED)["item_level"] == 22.0
    # vm-wow may still hold an older exporter's render addresses (until its
    # retention drops them): never read, the picture is our own still.
    assert one.character_armory(SHARED) == {"item_level": 21.0}
    assert not [q for q in one.queries if "wow_character_render" in q]
    assert one.character_look(SHARED)["options"] == [{"optionId": 80, "choiceId": 1101}]
    assert two.character_look(SHARED)["options"] == [{"optionId": 80, "choiceId": 1202}]
    assert one.character_equipment(SHARED)["head"]["item_id"] == 1001
    assert one.character_talents(SHARED)["trees"] == [{"tree": "Holy", "points": 5}]
    assert two.character_talents(SHARED)["trees"] == [{"tree": "Shadow", "points": 5}]
    # Guild, sex and the Legacy achievements (2026-11-20) too.
    assert one.character_profile(SHARED)["guild"] == "Guild 1"
    assert two.character_profile(SHARED) == {"sex": "male", "guild": "Guild 2", "guild_known": True,
                                             "class": None, "race": "Gnome", "faction": None, "level": 30,
                                             "refreshed": two.character_profile(SHARED)["refreshed"]}
    assert list(one.account_achievements()) == [61001] and list(two.account_achievements()) == [61002]
    # Every profile read names the account, the fingerprint too.
    one.profile_fingerprint()
    assert one.queries and all('account_id="1"' in q for q in one.queries), one.queries


def test_a_friend_naming_the_owners_character_reads_nothing():
    friend = FakeVM(1)
    assert friend.character_armory("Ownerchar-Fixture") == {"item_level": None}
    assert friend.character_look("Ownerchar-Fixture") is None
    assert friend.character_talents("Ownerchar-Fixture") is None
    assert friend.character_equipment("Ownerchar-Fixture") == {}
    assert friend.character_profile("Ownerchar-Fixture") is None
    assert FakeVM(3).account_achievements() is None
    assert FakeVM(0).character_armory("Ownerchar-Fixture")["item_level"] == 40.0
    # A third person with no Battle.net data of their own sees none of theirs.
    assert FakeVM(3).character_look(SHARED) is None
    assert FakeVM(3).profile_fingerprint() == FakeVM(3).profile_fingerprint()


def test_fingerprints_are_per_account():
    assert FakeVM(1).profile_fingerprint() != FakeVM(2).profile_fingerprint()


def test_public_profile_marker_uses_the_profile_store_without_market_access(monkeypatch):
    store = FakeVM(1)
    store.vm_url = ""
    def no_market(q):
        raise AssertionError(q)

    monkeypatch.setattr(store, "vm_query", no_market)
    first = store.profile_fingerprint()
    assert first == store.profile_fingerprint()
    assert store.character_armory(SHARED)["item_level"] == 21
    rows = [(n, lab, 29 if n == "wow_character_item_level" else value) for n, lab, value in VM]
    monkeypatch.setattr(sys.modules[__name__], "VM", rows)
    assert store.character_armory(SHARED)["item_level"] == 29
    assert store.profile_fingerprint() != first


def _people(chars: dict[int, list[str]], owner: int = 0):
    users = [tenancy.User(sub=f"s{a}", account_id=a, owner=a == owner) for a in chars]
    apps = {u.sub: SimpleNamespace(views=SimpleNamespace(chars=lambda a=u.account_id: [
        {"char": c} for c in chars[a]] + [{"char": "Demo-Fixture", "demo": True}])) for u in users}
    return SimpleNamespace(registry=SimpleNamespace(users=lambda: users), app_for=lambda u: apps[u.sub])


def test_the_exporter_list_gives_a_shared_name_to_the_owner_or_to_nobody(capsys):
    t = _people({0: ["Ownerchar-Fixture", "Owneralt-Fixture"],
                 1: [SHARED, "Ownerchar-Fixture", "Friendone-Fixture"],
                 2: [SHARED, "Friendtwo-Fixture"]})
    got = tenancy.Tenancy.profile_characters(t)
    assert got == [(0, "Ownerchar-Fixture"), (0, "Owneralt-Fixture"), (1, "Friendone-Fixture"),
                   (2, "Friendtwo-Fixture")]
    log = capsys.readouterr().out
    assert '"count": 2' in log and SHARED not in log and "Ownerchar" not in log  # no names in the log


def test_one_person_keeps_all_their_names():
    t = _people({0: ["Ownerchar-Fixture", "Owneralt-Fixture"]})
    assert tenancy.Tenancy.profile_characters(t) == [(0, "Ownerchar-Fixture"), (0, "Owneralt-Fixture")]


def test_what_battlenet_never_read_is_unknown_never_0_or_none(monkeypatch):
    """Degraded mode (owner, 2026-11-20): an exporter that never read
    /achievements (an older one, or the API failed) leaves the Legacy count
    unknown, not 0; a profile series without in_guild leaves the guild
    unknown, not "no guild". What Battle.net did answer stays a fact."""
    import sys

    from wowstats import talents

    mod = sys.modules[__name__]

    who = {"character": "Old-Fixture", "account_id": "0"}
    monkeypatch.setattr(mod, "VM", [
        ("wow_character_profile", {**who, "race_id": "7", "gender": "female"}, 30.0),
        ("wow_character_profile_refreshed_timestamp_seconds", who, time.time() - 3600),
        ("wow_character_profile_found", who, 1.0)])
    vm = FakeVM(0)
    assert vm.account_achievements() is None
    assert talents.legacy({"legacy_sources": [{"amount": 1, "requirement": "x", "achievements": [1]}]},
                          vm.account_achievements())["earned_points"] is None
    assert vm.character_profile("Old-Fixture")["guild"] is None
    assert vm.character_profile("Old-Fixture")["guild_known"] is False
    # Read, and nothing there: 0 points, and no guild for a fact.
    monkeypatch.setattr(mod, "VM", [
        ("wow_character_profile", {**who, "race_id": "7", "gender": "female", "in_guild": "false"}, 30.0),
        ("wow_character_profile_refreshed_timestamp_seconds", who, time.time() - 3600),
        ("wow_character_achievements_read", who, 0.0)])
    assert vm.account_achievements() == {}
    assert vm.character_profile("Old-Fixture")["guild_known"] is True
