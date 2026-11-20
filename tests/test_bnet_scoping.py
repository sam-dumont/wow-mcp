"""Battle.net data per person (security review 0.6.0 S1): the exporter
labels every profile series with the account whose character it is, every
reader asks for its own account, and a name two people claim goes to the
owner or to nobody. Two friends here share one character name; each must
read only what was fetched for them, and neither the other's nor the
owner's."""

from __future__ import annotations

import json
import re
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
        ("wow_character_profile", {**who, "race_id": "7", "gender": "male"}, 30.0),
        ("wow_character_profile_refreshed_timestamp_seconds", who, 1795000000.0),
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
    assert "/1-main-raw" in one.character_armory(SHARED)["render"]["url"]
    assert one.character_look(SHARED)["options"] == [{"optionId": 80, "choiceId": 1101}]
    assert two.character_look(SHARED)["options"] == [{"optionId": 80, "choiceId": 1202}]
    assert one.character_equipment(SHARED)["head"]["item_id"] == 1001
    assert one.character_talents(SHARED)["trees"] == [{"tree": "Holy", "points": 5}]
    assert two.character_talents(SHARED)["trees"] == [{"tree": "Shadow", "points": 5}]
    # Every profile read names the account, the fingerprint too.
    one.profile_fingerprint()
    assert one.queries and all('account_id="1"' in q for q in one.queries), one.queries


def test_a_friend_naming_the_owners_character_reads_nothing():
    friend = FakeVM(1)
    assert friend.character_armory("Ownerchar-Fixture") == {"item_level": None, "render": None}
    assert friend.character_look("Ownerchar-Fixture") is None
    assert friend.character_talents("Ownerchar-Fixture") is None
    assert friend.character_equipment("Ownerchar-Fixture") == {}
    assert FakeVM(0).character_armory("Ownerchar-Fixture")["item_level"] == 40.0
    # A third person with no Battle.net data of their own sees none of theirs.
    assert FakeVM(3).character_look(SHARED) is None
    assert FakeVM(3).profile_fingerprint() == FakeVM(3).profile_fingerprint()


def test_fingerprints_are_per_account():
    assert FakeVM(1).profile_fingerprint() != FakeVM(2).profile_fingerprint()


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
