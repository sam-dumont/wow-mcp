from pathlib import Path

import pytest

from wowstats import exporter


def test_push_target_keeps_existing_query():
    assert exporter.push_target("http://vm:8428/api/v1/import/prometheus", []) == \
        "http://vm:8428/api/v1/import/prometheus"
    assert exporter.push_target("http://vm:8428/api/v1/import/prometheus?extra_label=site=home", ["job=wow"]) == \
        "http://vm:8428/api/v1/import/prometheus?extra_label=site=home&extra_label=job%3Dwow"
    assert exporter.push_target("http://vm:8428/api/v1/import/prometheus", ["job=wow"]) == \
        "http://vm:8428/api/v1/import/prometheus?extra_label=job%3Dwow"


def test_merge_url():
    assert exporter.merge_url_for("http://vm:8428/api/v1/import/prometheus?extra_label=a=b") == \
        "http://vm:8428/internal/force_merge"


def test_no_auction_house_api():
    """The owner's rule (2026-11-20): no Auction House API. Prices come from
    the player's own Auctionator scans (wowstats/ahsource.py)."""
    assert set(exporter.TASKS) == {"token", "realm", "profile"}
    src = Path(exporter.__file__).read_text(encoding="utf-8")
    assert "/auctions" not in src and "commodities" not in src


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
    assert 'wow_character_customization{account_id="0",character="Testpriest-Fixture",option_id="80"} 1174.0' in out
    assert 'wow_character_customization{account_id="0",character="Testpriest-Fixture",option_id="82"} 1210.0' in out
    assert ('wow_character_profile{account_id="0",character="Testpriest-Fixture",gender="male",in_guild="false",'
            'race_id="7"} 32.0') in out
    # No /achievements answer: no "read" marker, so the Legacy count stays unknown.
    assert "wow_character_achievements_read" not in out
    assert 'wow_character_equipped{account_id="0",character="Testpriest-Fixture",slot="main_hand"} 1317.0' in out
    # A random suffix's stats are in no item table: the armory's, per slot.
    assert 'wow_character_equipped_stat{account_id="0",character="Testpriest-Fixture",slot="back",stat="INTELLECT"} 4.0' in out
    assert 'wow_character_profile_found{account_id="0",character="Testhunter-Fixture"} 0.0' in out
    assert "Testhunter-Fixture" not in out.replace('wow_character_profile_found{account_id="0",character="Testhunter-Fixture"}', "")

    # Gone from the API: only "not found" stays, the look and gear go.
    monkeypatch.setattr(exporter, "api_get", fake_api({}, calls))
    exporter.poll_profile()
    out = exporter.render_data()
    assert "customization" not in out and "equipped" not in out
    assert 'wow_character_profile_found{account_id="0",character="Testpriest-Fixture"} 0.0' in out


def test_profile_reads_guild_and_achievements(monkeypatch):
    """Guild and completed achievements come from Battle.net (owner,
    2026-11-20): the guild on the profile series, each completed
    achievement with when, an unfinished one left out. Kept 30 days at most."""
    exporter.configure({"BNET_CLIENT_ID_FILE": "/x", "BNET_CLIENT_SECRET_FILE": "/y",
                        "PROFILE_CHARACTERS": "Testpriest-Fixture"})
    exporter.data.clear()
    p = "/profile/wow/character/testpriest-fixture"
    calls = []
    ach = {"achievements": [
        {"id": 61994, "achievement": {"id": 61994, "name": "Legacy: Mage 25"}, "completed_timestamp": 1763460000000},
        {"achievement": {"id": 63970}, "criteria": {"is_completed": True}},
        {"id": 62000, "criteria": {"is_completed": False}}]}
    monkeypatch.setattr(exporter, "api_get", fake_api(
        {p: {**PRIEST, "guild": {"name": "Test Guild", "id": 1}}, p + "/achievements": ach}, calls))
    exporter.poll_profile()
    assert (p + "/achievements", "profile-forever-eu") in calls
    out = exporter.render_data()
    assert ('wow_character_profile{account_id="0",character="Testpriest-Fixture",gender="male",guild="Test Guild",'
            'in_guild="true",race_id="7"} 32.0') in out
    assert 'wow_character_achievements_read{account_id="0",character="Testpriest-Fixture"} 2' in out
    assert 'wow_character_achievement{account_id="0",achievement_id="61994",character="Testpriest-Fixture"} 1763460000.0' in out
    assert 'achievement_id="63970"' in out and 'achievement_id="62000"' not in out
    assert exporter.completed_achievements(None) == {}
    exporter.forget_old(now=exporter.time.time() + 31 * 86400)
    assert not exporter.data.get("wow_character_achievement")


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
    assert 'wow_character_talent_points{account_id="0",character="Testpriest-Fixture",order="0",tree="Discipline"} 16.0' in out
    assert 'wow_character_talent_points{account_id="0",character="Testpriest-Fixture",order="1",tree="Shadow Magic"} 7.0' in out
    assert 'wow_character_talent_points{account_id="0",character="Testpriest-Fixture",order="2",tree="Holy"} 0.0' in out
    assert 'wow_character_talent_rank{account_id="0",character="Testpriest-Fixture",spell_id="15270",talent="Spirit Tap"} 5.0' in out
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
    assert 'wow_character_item_level{account_id="0",character="Testpriest-Fixture",kind="equipped"} 23' in out
    assert 'wow_character_item_level{account_id="0",character="Testpriest-Fixture",kind="average"} 24' in out
    assert ('wow_character_render{account_id="0",asset="main-raw",character="Testpriest-Fixture",'
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


class _Answer:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self.body


def test_profile_list_is_per_account_capped_and_first_claim_wins(monkeypatch):
    """Security review 0.6.0 S1: every series names whose character it is,
    a name is read for one account only, and a friend's list is capped."""
    from wowstats import guard

    exporter.configure({"BNET_CLIENT_ID_FILE": "/x", "BNET_CLIENT_SECRET_FILE": "/y",
                        "PROFILE_CHARACTERS": "Ownerchar-Fixture",
                        "PROFILE_CHARACTERS_URL": "http://full.invalid:9/profile-characters"})
    lines = ["0\tOwnerchar-Fixture", "1\tOwnerchar-Fixture", "1\tShared-Fixture", "2\tShared-Fixture",
             "bad line", "x\tNope-Fixture", "2\tFriendtwo-Fixture"]
    lines += [f"3\tMany{i}-Fixture" for i in range(exporter.PER_ACCOUNT + 5)]
    lines += [f"0\tAlt{i}-Fixture" for i in range(exporter.PER_ACCOUNT + 1)]
    monkeypatch.setattr(guard, "share_open", lambda url: _Answer("\n".join(lines).encode()))
    got = exporter.profile_chars()
    assert got[0] == ("0", "Ownerchar-Fixture")
    assert ("1", "Ownerchar-Fixture") not in got  # the env list (account 0) had it first
    assert ("1", "Shared-Fixture") in got and ("2", "Shared-Fixture") not in got
    assert ("2", "Friendtwo-Fixture") in got
    assert sum(1 for a, _ in got if a == "3") == exporter.PER_ACCOUNT
    assert sum(1 for a, n in got if a == "0" and n.startswith("Alt")) == exporter.PER_ACCOUNT + 1  # owner: no cap
    assert not any(n == "Nope-Fixture" for _, n in got)

    exporter.data.clear()
    calls = []
    monkeypatch.setattr(guard, "share_open", lambda url: _Answer(b"1\tTestpriest-Fixture\n"))
    p = "/profile/wow/character/testpriest-fixture"
    monkeypatch.setattr(exporter, "api_get", fake_api({p: PRIEST, p + "/appearance": LOOK}, calls))
    exporter.poll_profile()
    out = exporter.render_data()
    assert 'wow_character_profile{account_id="1",character="Testpriest-Fixture",' in out
    assert 'account_id="0",character="Testpriest-Fixture"' not in out


def test_the_log_never_carries_the_client_secret(tmp_path, monkeypatch, capsys):
    """The Battle.net client's id and secret, and the merge key, are read from
    files and sent in a header: no log line (start, a refused token, an
    unreachable API) prints them."""
    import urllib.error

    secret, key = "s3cret-client-value-0123456789", "merge-key-value-9876543210"
    (tmp_path / "id").write_text("client-id-value\n")
    (tmp_path / "secret").write_text(secret + "\n")
    (tmp_path / "key").write_text(key + "\n")
    exporter.configure({"BNET_CLIENT_ID_FILE": str(tmp_path / "id"), "BNET_CLIENT_SECRET_FILE": str(tmp_path / "secret"),
                        "VM_PUSH_URL": "http://vm:8428/api/v1/import/prometheus",
                        "VM_MERGE_KEY_FILE": str(tmp_path / "key")})
    monkeypatch.setitem(exporter._token, "value", "")
    exporter.log(f"listening on :{exporter.LISTEN}, region {exporter.REGION}")

    def refused(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", req.headers, None)
    monkeypatch.setattr(exporter.urllib.request, "urlopen", refused)
    assert exporter.run_task("token") is False

    def down(req, timeout=None):
        raise urllib.error.URLError(f"no route to {req.full_url}")
    monkeypatch.setattr(exporter.urllib.request, "urlopen", down)
    assert exporter.run_task("token") is False

    out = capsys.readouterr().out
    assert "region eu" in out and "token:" in out
    assert secret not in out and key not in out and "client-id-value" not in out
