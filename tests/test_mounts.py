"""Resolve a mount from this character's local records and this client's displays."""

from types import SimpleNamespace

from wowstats import mounts
from wowstats.stores import SourceError


def test_owned_mounts_use_character_evidence_and_unambiguous_client_skins():
    tables = {
        "Mount": {i: {"Name_lang": f"Mount {i}", "SourceSpellID": 100 + i} for i in range(1, 8)},
        "MountXDisplay": {i: {"MountID": i, "CreatureDisplayInfoID": 200 + i, "PlayerConditionID": i == 4}
                          for i in range(1, 8)},
    }
    tables["MountXDisplay"][8] = {"MountID": 5, "CreatureDisplayInfoID": 215, "PlayerConditionID": 0}
    query_log = []

    def read(q, *args):
        query_log.append(q)
        assert 'char:="A-B"' in q
        if 'kind:=spells' in q:
            # The archive can return a second result. Only the newest book counts.
            return [{"_time": "2026-11-26", "spells": "[[101,0],[102,40],[103,40],[104,0],[105,0],[33391,0]]"},
                    {"_time": "2026-11-23", "spells": "[[106,0]]"}]
        assert 'kind:=learn' in q and 'spell_id:in(' in q
        return [{"spell_id": "102", "last": "2026-11-24"}, {"spell_id": "103", "last": "2026-11-27"},
                {"spell_id": "107", "last": "2026-11-24"}, {"spell_id": "33391", "last": "2026-11-24"}]

    app = SimpleNamespace(views=SimpleNamespace(char_filter=lambda name: f'char:="{name}"'),
                          store=SimpleNamespace(stream="*", vl=read))
    assert mounts.owned(app, "A-B", tables) == [{"name": f"Mount {i}", "display_id": 200 + i} for i in (1, 3, 7)]
    assert len(query_log) == 2
    assert mounts.owned(app, "A-B", {}) == []

    def offline(*_):
        raise SourceError("store unavailable")

    app.store.vl = offline
    assert mounts.owned(app, "A-B", tables) == []
