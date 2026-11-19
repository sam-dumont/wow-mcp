import json

import pytest

from wowstats import exporter


def test_push_target_keeps_existing_query():
    assert exporter.push_target("http://vm:8428/api/v1/import/prometheus", []) == \
        "http://vm:8428/api/v1/import/prometheus"
    assert exporter.push_target("http://vm:8428/api/v1/import/prometheus?extra_label=site=home", ["job=wow"]) == \
        "http://vm:8428/api/v1/import/prometheus?extra_label=site=home&extra_label=job%3Dwow"
    assert exporter.push_target("http://vm:8428/api/v1/import/prometheus", ["job=wow"]) == \
        "http://vm:8428/api/v1/import/prometheus?extra_label=job%3Dwow"


def test_merge_url_and_watch_items(tmp_path):
    assert exporter.merge_url_for("http://vm:8428/api/v1/import/prometheus?extra_label=a=b") == \
        "http://vm:8428/internal/force_merge"
    assert exporter.watch_items('{"2592": "Wool Cloth"}') == {2592: "Wool Cloth"}
    f = tmp_path / "watch.json"
    f.write_text(json.dumps({"4306": "Silk Cloth"}))
    assert exporter.watch_items(str(f)) == {4306: "Silk Cloth"}


def test_configure_needs_own_client():
    with pytest.raises(SystemExit, match="BNET_CLIENT_ID_FILE"):
        exporter.configure({})
    exporter.configure({"BNET_CLIENT_ID_FILE": "/x", "BNET_CLIENT_SECRET_FILE": "/y",
                        "VM_PUSH_URL": "http://vm:8428/api/v1/import/prometheus", "VM_MERGE_KEY_FILE": "/k"})
    assert exporter.MERGE_URL == "http://vm:8428/internal/force_merge"
    assert exporter.TOKEN_NAMESPACES == {"forever": "dynamic-forever-eu", "retail": "dynamic-eu"}


def test_render_drops_stale_game_data():
    exporter.configure({"BNET_CLIENT_ID_FILE": "/x", "BNET_CLIENT_SECRET_FILE": "/y"})
    exporter.data.clear()
    exporter.put("wow_token_price_gold", {"game": "retail", "region": "eu"}, 300000, "token")
    out = exporter.render_data()
    assert 'wow_token_price_gold{game="retail",region="eu"} 300000.0' in out
    assert exporter.render_data(now=10**12) == ""


PRIEST = {"level": 32, "race": {"id": 7}, "gender": {"type": "MALE"}}
LOOK = {"playable_race": {"id": 7}, "gender": {"type": "MALE"}, "customizations": [
    {"option": {"id": 80, "name": "Skin Color"}, "choice": {"id": 1174}},
    {"option": {"id": 82, "name": "Hair Style"}, "choice": {"id": 1210, "name": "Wild"}}]}
GEAR = {"equipped_items": [{"slot": {"type": "MAIN_HAND"}, "item": {"id": 1317}},
                           {"slot": {"type": "BACK"}, "item": {"id": 7356}, "name": "Elder's Cloak of the Elder",
                            "stats": [{"type": {"type": "INTELLECT", "name": "Intellect"}, "value": 4},
                                      {"type": {"type": "SPIRIT", "name": "Spirit"}, "value": 3}]}]}


def fake_api(answers, calls):
    def api_get(path, namespace, endpoint, reduce=None):
        calls.append((path, namespace))
        return answers.get(path)
    return api_get


def test_profile_keys_forever_characters_without_realm(monkeypatch):
    exporter.configure({"BNET_CLIENT_ID_FILE": "/x", "BNET_CLIENT_SECRET_FILE": "/y",
                        "PROFILE_CHARACTERS": "Testpriest-Fixture, Testhunter-Fixture"})
    exporter.data.clear()
    calls = []
    p = "/profile/wow/character/testpriest-fixture"
    monkeypatch.setattr(exporter, "api_get", fake_api(
        {p: PRIEST, p + "/appearance": LOOK, p + "/equipment": GEAR}, calls))
    assert exporter.poll_profile() == "1 of 2 characters"
    assert ("/profile/wow/character/testhunter-fixture", "profile-forever-eu") in calls
    out = exporter.render_data()
    assert 'wow_character_customization{character="Testpriest-Fixture",option_id="80"} 1174.0' in out
    assert 'wow_character_customization{character="Testpriest-Fixture",option_id="82"} 1210.0' in out
    assert 'wow_character_profile{character="Testpriest-Fixture",gender="male",race_id="7"} 32.0' in out
    assert 'wow_character_equipped{character="Testpriest-Fixture",slot="main_hand"} 1317.0' in out
    # A random suffix's stats are in no item table: the armory's, per slot.
    assert 'wow_character_equipped_stat{character="Testpriest-Fixture",slot="back",stat="INTELLECT"} 4.0' in out
    assert 'wow_character_profile_found{character="Testhunter-Fixture"} 0.0' in out
    assert "Testhunter-Fixture" not in out.replace('wow_character_profile_found{character="Testhunter-Fixture"}', "")

    # Gone from the API: only "not found" stays, the look and gear go.
    monkeypatch.setattr(exporter, "api_get", fake_api({}, calls))
    exporter.poll_profile()
    out = exporter.render_data()
    assert "customization" not in out and "equipped" not in out
    assert 'wow_character_profile_found{character="Testpriest-Fixture"} 0.0' in out


def test_profile_kept_30_days_at_most(monkeypatch):
    exporter.configure({"BNET_CLIENT_ID_FILE": "/x", "BNET_CLIENT_SECRET_FILE": "/y",
                        "PROFILE_CHARACTERS": "Testpriest-Fixture"})
    exporter.data.clear()
    p = "/profile/wow/character/testpriest-fixture"
    monkeypatch.setattr(exporter, "api_get", fake_api({p: PRIEST, p + "/appearance": LOOK}, []))
    exporter.poll_profile()
    assert exporter.data["wow_character_customization"]
    exporter.forget_old(now=exporter.time.time() + 31 * 86400)
    assert not any(exporter.data.get(n) for n in exporter.PROFILE_SERIES)
    assert exporter.char_slug("Ébène-Fixture") == "%C3%A9b%C3%A8ne-fixture"


def _t(name, spell, rank):
    return {"talent": {"name": name, "id": spell + 1}, "spell_tooltip": {"spell": {"name": name, "id": spell}},
            "talent_rank": rank}


# Testpriest's /specializations as Forever answered it on 2026-11-18 (the ids
# are stand-ins; the shape, names, ranks and points are the live ones).
PRIEST_SPECS = {"_links": {}, "character": {"name": "Testpriest Fixture"}, "specialization_groups": [
    {"is_active": True, "specializations": [
        {"specialization_name": "Discipline", "spent_points": 16, "talents": [
            _t("Wand Specialization", 14524, 2), _t("Twin Disciplines", 1225132, 5), _t("Mental Strength", 18551, 3),
            _t("Martyrdom", 14531, 2), _t("Inner Focus", 14751, 1), _t("Meditation", 14521, 3)]},
        {"specialization_name": "Shadow Magic", "spent_points": 7, "talents": [
            _t("Spirit Tap", 15270, 5), _t("Improved Shadow Word: Pain", 15275, 2)]},
        {"specialization_name": "Holy", "spent_points": 0, "talents": []}]},
    {"is_active": False, "specializations": [
        {"specialization_name": "Holy", "spent_points": 23, "talents": [_t("Healing Focus", 14913, 2)]}]}]}


def test_profile_reads_forever_talents(monkeypatch):
    """The active group's points per tree, in the game's order, and each
    talent's rank; the inactive group is not read. Kept like the rest of the
    profile: dropped after 30 days."""
    exporter.configure({"BNET_CLIENT_ID_FILE": "/x", "BNET_CLIENT_SECRET_FILE": "/y",
                        "PROFILE_CHARACTERS": "Testpriest-Fixture"})
    exporter.data.clear()
    p = "/profile/wow/character/testpriest-fixture"
    calls = []
    monkeypatch.setattr(exporter, "api_get", fake_api({p: PRIEST, p + "/specializations": PRIEST_SPECS}, calls))
    exporter.poll_profile()
    assert (p + "/specializations", "profile-forever-eu") in calls
    out = exporter.render_data()
    assert 'wow_character_talent_points{character="Testpriest-Fixture",order="0",tree="Discipline"} 16.0' in out
    assert 'wow_character_talent_points{character="Testpriest-Fixture",order="1",tree="Shadow Magic"} 7.0' in out
    assert 'wow_character_talent_points{character="Testpriest-Fixture",order="2",tree="Holy"} 0.0' in out
    assert 'wow_character_talent_rank{character="Testpriest-Fixture",spell_id="15270",talent="Spirit Tap"} 5.0' in out
    assert out.count("wow_character_talent_rank{") == 8 and "Healing Focus" not in out
    trees, ranks = exporter.talent_readings(PRIEST_SPECS)
    assert [(t["tree"], t["points"]) for t in trees] == [("Discipline", 16), ("Shadow Magic", 7), ("Holy", 0)]
    assert sum(r for r, _ in ranks.values()) == 23
    assert exporter.talent_readings({"specialization_groups": []}) == ([], {})
    assert exporter.talent_readings(None) == ([], {})
    exporter.forget_old(now=exporter.time.time() + 31 * 86400)
    assert not exporter.data.get("wow_character_talent_rank") and not exporter.data.get("wow_character_talent_points")


MEDIA = {"assets": [
    {"key": "avatar", "value": "https://render.worldofwarcraft.com/classic-eu/character/x/1/2-avatar.jpg"},
    {"key": "main-raw", "value": "https://render.worldofwarcraft.com/classic-eu/character/x/1/2-main-raw.png"}]}


def test_profile_keeps_the_armory_item_level_and_render(monkeypatch):
    exporter.configure({"BNET_CLIENT_ID_FILE": "/x", "BNET_CLIENT_SECRET_FILE": "/y",
                        "PROFILE_CHARACTERS": "Testpriest-Fixture"})
    exporter.data.clear()
    p = "/profile/wow/character/testpriest-fixture"
    monkeypatch.setattr(exporter, "api_get", fake_api(
        {p: {**PRIEST, "equipped_item_level": 23, "average_item_level": 24}, p + "/appearance": LOOK,
         p + "/character-media": MEDIA}, []))
    exporter.poll_profile()
    out = exporter.render_data()
    assert 'wow_character_item_level{character="Testpriest-Fixture",kind="equipped"} 23' in out
    assert 'wow_character_item_level{character="Testpriest-Fixture",kind="average"} 24' in out
    assert ('wow_character_render{asset="main-raw",character="Testpriest-Fixture",'
            'url="https://render.worldofwarcraft.com/classic-eu/character/x/1/2-main-raw.png"}') in out
    # Thirty days on, the render's address goes with the rest.
    exporter.forget_old(now=exporter.time.time() + 31 * 86400)
    assert "wow_character_render" not in exporter.render_data(now=exporter.time.time())


@pytest.mark.parametrize("media, want", [
    (MEDIA, "main-raw"),
    ({"assets": [{"key": "main", "value": "https://render.worldofwarcraft.com/a/b-main.jpg"}]}, "main"),
    ({"assets": [{"key": "main-raw", "value": "http://render.worldofwarcraft.com/a.png"}]}, None),
    ({"assets": [{"key": "main-raw", "value": "https://evil.example/a.png"}]}, None),
    ({"assets": [{"key": "main-raw", "value": "https://u@render.worldofwarcraft.com/a.png"}]}, None),
    ({"assets": [{"key": "avatar", "value": "https://render.worldofwarcraft.com/a.jpg"}]}, None),
    (None, None),
])
def test_render_url(media, want):
    got = exporter.render_url(media)
    assert (got[0] if got else None) == want
