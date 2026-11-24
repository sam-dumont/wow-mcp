"""Who was in each dungeon run, on the Dungeons panel (owner, 2026-11-24).
The parties come from storemodels.run_party; here, that each past trip gets
the parties of the runs inside it, and that the friends' and public tiers
never carry them."""

from wowstats import insights, redact

MATE = {"player": "Strangerbar-Madeup", "level": 18, "class": "Priest", "race": "Dwarf", "role": "healer"}


def panel():
    return {"char": "Testpriest-Fixture", "dungeons": [
        {"name": "The Deadmines", "history": [
            {"t": "2026-11-01T09:55:00Z", "level": 18, "inside_s": 1700, "total_s": 2400},
            {"t": "2026-11-03T09:55:00Z", "level": 19, "inside_s": 1700, "total_s": 2400}]}]}


def test_each_trip_gets_the_party_of_its_runs():
    runs = [{"t": "2026-11-01T10:00:00Z", "party": [MATE], "names_known": "1 of 4 names known"},
            {"t": "2026-11-02T10:00:00Z", "party": [{**MATE, "player": "Other-Madeup"}], "names_known": None}]
    got = insights.with_parties(panel(), runs)
    first, second = got["dungeons"][0]["history"]
    assert first["party"] == [MATE] and first["names_known"] == "1 of 4 names known"
    assert "party" not in second  # no run of its own in it


def test_the_party_stays_in_the_full_view():
    got = insights.with_parties(panel(), [{"t": "2026-11-01T10:00:00Z", "party": [MATE], "names_known": None}])
    assert got["dungeons"][0]["history"][0]["party"]
    z = redact.zogzog("dungeons", {**got, "available": True})
    assert "Strangerbar" not in repr(z) and "party" not in repr(z)
