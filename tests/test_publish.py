"""wow-stats publish and wow-stats static: the public bundle holds no full
data, the full-content bundle holds only ciphertext, and the static server
answers files and nothing else."""

import hashlib
import hmac
import io
import json
import re
import shutil
import socket
import threading
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from tests import sealed
from tests.test_public_view import (
    SECRET_MONEY,
    SECRET_PLAYER,
    SECRET_PRICE,
    SECRET_TOKEN,
    SECRET_X,
    SECRET_Y,
    SECRETS,
    PoisonStore,
)
from wowstats import bar, publish, static, ui
from wowstats.redact import ZOGZOG_MOVE_KINDS

pytest.importorskip("cryptography")
from cryptography.exceptions import InvalidTag  # noqa: E402


@pytest.fixture(autouse=True)
def own_planner_inputs(monkeypatch):
    """Every publish here reads the planner's inputs from its own fake store,
    never a cached gather. The Dungeons panel asks plan.inputs, which keeps
    a gather two minutes per character in the process: run one after the
    other, the publishes below `site` found its poisoned store's gather in
    that cache, and on their own (a pytest-xdist worker that ran no `site`
    test first) they built a real store and asked vl.invalid."""
    from wowstats import plan
    from wowstats.plan import facts

    monkeypatch.setattr(plan, "_CACHE", {})
    monkeypatch.setattr(facts, "store_for", lambda env, like=None: like)


@pytest.fixture
def site(tmp_path, monkeypatch):
    def store(vl, archive="", vm="", *a, **k):
        s = PoisonStore()
        s.vm_url = vm
        return s

    monkeypatch.setattr(ui, "Store", store)
    # The Dungeons panel reads the planner's inputs from a store of its own
    # (no vm-wow): here, the same poisoned one.
    from wowstats.plan import facts

    monkeypatch.setattr(facts, "store_for", lambda env, like=None: like)
    env = {"VL_URL": "http://vl.invalid", "VM_URL": "http://vm.invalid", "MODEL3D": "off",
           "PUBLISH_DIR": str(tmp_path / "site"), "ZOGZOG_KEY_FILE": str(tmp_path / "zogzog.key")}
    out = io.StringIO()
    with redirect_stdout(out):
        res = publish.publish(env)
    key = (tmp_path / "zogzog.key").read_text().strip()
    assert key not in out.getvalue()  # never in the publisher's output
    return env, res, key, tmp_path / "site" / "current"


def files(root):
    """Every file under root; a sealed file under its path + .enc (the keyed
    name it has on disk mapped back: tests/sealed.py)."""
    return sealed.plain_names({p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()})


def test_sealed_files_are_named_by_a_keyed_hash(site):
    """On disk, /zogzog/'s data files say nothing of the characters: one
    flat folder of HMAC names (security review 2026-11-20, finding 4), each
    the HMAC-SHA256 of its path under a key derived from the link's key."""
    _, _, key, cur = site
    k = publish.unb64u(key)
    on_disk = [p.relative_to(cur / "zogzog").as_posix() for p in (cur / "zogzog" / "data").rglob("*") if p.is_file()]
    assert on_disk and all(re.fullmatch(r"data/[A-Za-z0-9_-]{43}\.enc", p) for p in on_disk), on_disk[:5]
    names_key = hmac.new(k, b"wow-stats zogzog file names v1", hashlib.sha256).digest()
    want = "data/" + publish.b64u(hmac.new(names_key, b"data/chars.json", hashlib.sha256).digest()) + ".enc"
    assert publish.sealed_name(k, "data/chars.json") == want and want in on_disk
    chars = json.loads(publish.unseal(k, "data/chars.json", (cur / "zogzog" / want).read_bytes()))["chars"]
    for c in chars:
        assert c["char"].split("-")[0] not in "".join(on_disk)
    # Another key, other names: a name says nothing across links.
    assert publish.sealed_name(b"\x01" * 32, "data/chars.json") != want


def test_public_bundle_has_no_full_data(site):
    _, res, _, cur = site
    assert res["built"] and res["public_files"] > 10
    pub = files(cur / "public")
    assert "index.html" in pub and "data/config.json" in pub
    assert not any(p.startswith(("data/people/", "data/market", "data/prices")) for p in pub)
    for path, body in pub.items():
        for s in SECRETS:
            assert s.encode() not in body, f"{path} leaks {s}"
    page = pub["index.html"].decode()
    assert 'data-static="1"' in page and 'id="people"' not in page and 'data-sealed' not in page
    cfg = json.loads(pub["data/config.json"])
    assert cfg == {"view": "public", "static": True}


def opened(key: str, zz: dict) -> dict:
    """The zogzog bundle's data files, decrypted: path -> plain bytes."""
    k = publish.unb64u(key)
    return {p.removesuffix(".enc"): publish.unseal(k, p.removesuffix(".enc"), b)
            for p, b in zz.items() if p.startswith("data/") and not p.startswith("data/icon/")}


def test_the_three_dashboard_paths_agree_on_the_climb_and_worn_items(site, run_app):
    env, _, key, current = site
    name = json.loads((current / "public/data/chars.json").read_bytes())["chars"][0]["char"]
    path = publish.data_path("char", name)
    public = json.loads((current / "public" / path).read_bytes())
    friends = json.loads(opened(key, files(current / "zogzog"))[path])
    owner = run_app(ui.App({**env, "VIEW": "full", "BIND": "127.0.0.1", "AUTH": "none"}))
    status, _, body = owner.get("/api/char?" + urllib.parse.urlencode({"name": name}))
    assert status == 200
    full = json.loads(body)

    def splits(answer):
        return [(row["level"], row["seconds"], row["partial"], row.get("route_s")) for row in answer["levels"]]

    def worn(answer):
        return [(row["slot"], row["item_id"]) for row in answer["gear"]["items"]]

    assert any(row.get("route_s") for row in public["levels"])
    assert public["route_source"] and worn(public)
    for answer in (friends, full):
        assert splits(answer) == splits(public)
        assert answer["route_source"] == public["route_source"]
        assert worn(answer) == worn(public)
    assert "money_copper" not in public["header"]
    assert "players" not in friends


def test_zogzog_bundle_is_sealed_full_content(site):
    _, res, key, cur = site
    zz = files(cur / "zogzog")
    data = {p: b for p, b in zz.items() if p.startswith("data/")}
    assert data and all(p.endswith(".json.enc") for p in data), "only ciphertext under data/"
    for body in zz.values():
        for s in SECRETS:
            assert s.encode() not in body
    k = publish.unb64u(key)
    chars = json.loads(publish.unseal(k, "data/chars.json", data["data/chars.json.enc"]))
    name = chars["chars"][0]["char"]
    rel = publish.data_path("char", name)
    full = publish.unseal(k, rel, data[rel + ".enc"]).decode()
    assert SECRET_MONEY in full, "the full content, once opened: gold stays"
    assert SECRET_PRICE in publish.unseal(k, "data/prices.json", data["data/prices.json.enc"]).decode()
    # The path is the associated data: a file moved elsewhere doesn't open.
    with pytest.raises(InvalidTag):
        publish.unseal(k, "data/chars.json", data[rel + ".enc"])
    with pytest.raises(InvalidTag):
        publish.unseal(publish.unb64u("A" * 43), "data/chars.json", data["data/chars.json.enc"])
    cfg = json.loads(publish.unseal(k, "data/config.json", data["data/config.json.enc"]))
    assert cfg["view"] == "full" and "coach" not in cfg and not cfg["mcp"] and not cfg["debug"]
    assert not cfg["people"] and cfg["loot"]
    page = zz["index.html"].decode()
    assert 'data-sealed="1"' in page and 'id="askbox"' not in page and 'id="debug"' not in page


def test_zogzog_has_no_other_players_and_no_location_now(site):
    """Owner, 2026-11-18: other players are /full/ only; /zogzog/ also leaves
    out where the character is now and the deaths' killers."""
    _, _, key, cur = site
    plain = opened(key, files(cur / "zogzog"))
    assert not any(p.startswith("data/people/") for p in plain)
    assert any(p.startswith("data/loot/") for p in plain)
    for path, body in plain.items():
        for s in (SECRET_PLAYER, "Secretfriend", SECRET_X, SECRET_Y):
            assert s.encode() not in body, f"{path} leaks {s}"
        bad = {"players", "roster", "party_size", "killer", "npc_name", "here", "deaths_detail",
               "clusters", "runs", "recent"} & {p.rsplit(".", 1)[-1].removesuffix("[]")
                                                 for p in key_paths(json.loads(body))}
        if path.startswith("data/char/"):
            bad -= {"recent"}  # flights.recent: where it flew from and to, history
        if path == "data/config.json":
            bad -= {"players"}  # the feature flag, false
        assert not bad, f"{path}: {sorted(bad)}"
    char = json.loads(plain[next(p for p in plain if p.startswith("data/char/"))])
    assert "zone" not in char["header"] and "seen" not in char["header"]
    assert set(char["deaths"]) == {"total", "by_zone", "by_level", "per_level"}
    # How many levels the ratio read, never which: that is coverage (review 6).
    assert char["deaths"]["per_level"] is None or set(char["deaths"]["per_level"]) == {"value", "deaths", "n"}
    ov = json.loads(plain[next(p for p in plain if p.startswith("data/overview/"))])
    assert set(ov["next_moves"]) == {"checks", "not_possible_yet"}
    assert all(m["kind"] in ZOGZOG_MOVE_KINDS for m in ov["moves"])
    assert "now" not in ov and "deaths" not in ov and "current" not in ov["guide"]


def test_zogzog_bundle_holds_only_its_allowlist(site):
    """Every key of every decrypted file is in redact.ZOGZOG's shape for it:
    nothing reaches the bundle around the allow-list."""
    from wowstats import redact

    _, _, key, cur = site
    for path, body in opened(key, files(cur / "zogzog")).items():
        if image_file(path, body):
            continue
        what = path.split("/")[1].removesuffix(".json")
        assert what in redact.ZOGZOG and what in publish.FILES["zogzog"], path
        extra = set(key_paths(json.loads(body))) - set(shape_paths(redact.ZOGZOG[what]))
        # {} shapes (counts, weights, deaths by level) hold any plain key.
        extra = {e for e in extra if not any(e.startswith(m + ".") for m in shape_maps(redact.ZOGZOG[what]))}
        assert not extra, f"{path}: {sorted(extra)}"


def shape_paths(spec, prefix=""):
    if isinstance(spec, list):
        yield from shape_paths(spec[0], prefix[:-1] + "[]." if prefix else "[].")
    elif isinstance(spec, dict):
        for k, sub in spec.items():
            yield prefix + k
            yield from shape_paths(sub, prefix + k + ".")


def shape_maps(spec, prefix=""):
    if isinstance(spec, list):
        yield from shape_maps(spec[0], prefix[:-1] + "[]." if prefix else "[].")
    elif isinstance(spec, dict):
        if not spec and prefix:
            yield prefix[:-1]
        for k, sub in spec.items():
            yield from shape_maps(sub, prefix + k + ".")


def test_rebuilds_only_on_new_events(site):
    env, *_ = site
    assert publish.publish(env)["built"] is False


def test_late_level_refreshes_both_published_timelines(tmp_path):
    from wowstats import eventstore

    where = f"sqlite:{tmp_path / 'events'}"
    name = "Hero-Test"
    env = {"EVENTS_STORE": where, "PUBLISH_DIR": str(tmp_path / "site"), "MODEL3D": "off", "WOW_ITEMS": "off"}
    es = eventstore.get(where)
    def ding(day, level):
        return {"_time": f"2026-11-{day}T12:00:00Z", "char": name, "feed": "wow-recorder", "kind": "level",
                "level": str(level), "source_id": f"ding:{level}"}

    def levels():
        rel = publish.data_path("timeline", name)
        root = tmp_path / "site" / "current"
        key = publish.read_key(tmp_path / "zogzog.key")
        enc = (root / "zogzog" / publish.sealed_name(key, rel)).read_bytes()
        return json.loads((root / "public" / rel).read_bytes())["levels"], json.loads(publish.unseal(key, rel, enc))["levels"]

    es.append(0, [ding(17, 12)])
    assert publish.publish(env)["built"] and levels() == ([12], [12])
    # The UI receives the upload in a different process from the publisher.
    import subprocess
    import sys

    subprocess.run([sys.executable, "-c", "import json,sys; from wowstats import eventstore; "
                    "eventstore.get(sys.argv[1]).append(0,[json.load(sys.stdin)])", where],
                   input=json.dumps(ding(16, 11)), text=True, check=True)
    assert publish.publish(env)["why"] == "new events" and levels() == ([11, 12], [11, 12])
    assert not publish.publish(env)["built"]


def test_settings_only_edit_rebuilds_sealed_gear(site, tmp_path):
    from wowstats import gearmode

    env, _, key, cur = site
    owner = {"ANALYSIS_STATE_DIR": str(tmp_path / "analysis")}
    env = {**env, "WEIGHTS": "Hero-Test:holy", "PUBLISH_GEAR_SETTINGS_DIR": owner["ANALYSIS_STATE_DIR"]}
    gearmode.save(owner, "Hero-Test", "pve")
    rel = publish.data_path("gear", "Hero-Test")
    def gear():
        return json.loads(opened(key, files(cur / "zogzog"))[rel])

    assert publish.publish(env)["built"]
    g = gear()
    assert (g["spec"]["key"], g["mode"]["key"], g["pvp_rewards"]["on"]) == ("holy", "pve", False)
    gearmode.save_pvp(owner, "Hero-Test", True)
    assert publish.publish(env)["built"] and gear()["pvp_rewards"]["on"]
    assert not publish.publish(env)["built"]


# The public bundle, key by key: Sam's list of 2026-11-18 (the card, the
# climb, the dungeons, the level snapshots; the doll's BiS badges since
# 2026-11-23), written out here on its own
# rather than read from redact.PUBLIC, so widening one without the other
# fails. "a[]" is a list's items, "a.b" an object's key.
ITEM = ("slot", "slot_name", "item_id", "name", "quality", "item_level", "req_level", "enchant_id", "enchant")
CARD = ("id", "n", "q", "i", "b", "u", "s", "t", "ar", "sp", "dps", "rl", "il", "set", "de", "w", "g")
M3D_ITEM = ("slot", "slot_name", "item_id", "name", "inventory_type", "display_id", "why")


def keys(prefix, names):
    return {f"{prefix}{n}" for n in names}


# The card's spec, talents and Legacy points (owner, 2026-11-18): the frame
# and points per tree (Battle.net's), the Legacy floor. Nothing of the
# sheets.
TREE = ("tree", "points")
TALENT_KEYS = {
    "spec", "talents", "legacy", *keys("spec.", ("name", "source", "split", "points", "scored_for")),
    *keys("spec.points[].", TREE), *keys("spec.scored_for.", ("key", "label", "source")),
    *keys("talents.", ("available", "source", "at", "level", "spec", "why", "unmatched", "points",
                       "sections")),
    *keys("talents.points[].", TREE), *keys("talents.sections[].", ("name", "points", "nodes", "edges")),
    *keys("talents.sections[].nodes[].", ("col", "row", "name", "icon", "rank", "max", "desc")),
    *keys("talents.sections[].edges[].", ("fc", "fr", "tc", "tr")),
    *keys("legacy.", ("available", "earned_points", "total", "spent", "why", "earned")),
}
# "Your class at level N" (owner, 2026-11-19): what to train and where (the
# capitals of the character's side), the next talent points, habits. From the
# class, race, side, level and talents: nothing about where it is or plays.
CG_SPELL = ("id", "name", "rank", "level", "cost_copper", "url", "needs_talent")
CLASS_GUIDE_KEYS = {
    "class_guide", *keys("class_guide.", ("class", "level", "spec", "train", "trainers", "talents", "habits",
                                          "respec")),
    "class_guide.train.now", "class_guide.train.next",
    *{f"class_guide.train.{w}.{k}" for w in ("now", "next") for k in ("level", "spells")},
    *{f"class_guide.train.{w}.spells[].{k}" for w in ("now", "next") for k in CG_SPELL},
    *keys("class_guide.trainers[].", ("npc", "name", "url", "x", "y", "zone")),
    *keys("class_guide.trainers[].zone.", ("name", "id", "url")),
    *keys("class_guide.talents[].", ("level", "talent", "tree", "rank", "max", "url", "now")),
    *keys("class_guide.respec.", ("level", "action")),
}


ALLOWED = {
    "config": {"view", "static"},
    "chars": {"chars", *keys("chars[].", ("char", "level", "demo"))},
    "char": {"char", "generated_at", "stale", "route_source", "header", "levels", "dungeons", "gear",
             *keys("header.", ("char", "name", "race", "class", "faction", "level", "xp", "xp_max", "polestar",
                               "played_s", "played_total_s", "first_level", "first_partial", "time_in_level_s", "title")),
             *keys("levels[].", ("level", "seconds", "partial", "route_s")),
             *keys("dungeons[].", ("name", "level", "t")),
             "gear.item_level", "gear.items", "gear.professions", *keys("gear.items[].", ITEM),
             # Each worn slot's BiS standing, the doll's badge (owner, 2026-11-23).
             "gear.standing", *keys("gear.standing[].", ("slot", "rank", "pct", "label")),
             "gear.armory_item_level", "gear.armory_item_level_source", "gear.render", "gear.render.asset",
             "gear.render.refreshed", "gear.render.ext", "gear.items_from", "gear.items_note",
             *keys("gear.professions[].", ("skill_id", "name", "rank", "max_rank", "max_rank_from")), *TALENT_KEYS,
             *CLASS_GUIDE_KEYS,
             # Whether the equipment may be out of date: a state, never a time
             # (owner, 2026-11-22).
             "coverage", "coverage.battlenet"},
    "timeline": {"char", "generated_at", "levels"},
    # A past level's gear says exact (a sheet) or likely (the replay), and
    # how a likely item was got by kind only: no quest, zone or creature.
    "snapshot": {"char", "level", "available", "why", "generated_at", "gear", "gear.item_level", "gear.items",
                 "gear_reconstructed", *keys("gear.items[].", (*ITEM, "certainty", "source")),
                 "gear.items[].source.kind", "picture", "picture.ext", "picture.pending"},
    "items": {"items", *keys("items[].", CARD)},
    # The recipe is file ids and numbers only (redact.M3D_RECIPE); its
    # inside is checked by shape, not listed key by key here.
    "model3d": {"race", "sex", "sex_known", "class", "level", "why", "appearance", "items", "missing",
                "appearance.source", "appearance.refreshed", "recipe", "recipe_sd", "no_classic", "models",
                *keys("items[].", M3D_ITEM), *keys("missing[].", M3D_ITEM)},
}
# Besides data/: the page itself, its script, style, fonts and the 3D viewer.
PAGE_FILES = re.compile(r"^(index\.html|app\.(js|css)|base\.css|theme\.js|favicon\.svg|items\.js|stats\.js|fonts/[\w-]+\.woff2|model3d/[^/]+|"
                        r"model3d/file/\d{1,10}|help/(index\.html|help\.(js|css)|content\.enc|media/[^/]+\.enc))$")
# Icons the publisher fetched for the page: items' (wowstats.itemcard) and
# talents' (wowstats.talents.icon_path), JPEGs named by the game's icon file.
ICON_FILES = re.compile(r"^data/icon/(small|medium|large)/[a-z0-9_-]+\.jpg$")
# The armory's render of a character (wowstats.armory): Battle.net's image.
RENDER_FILE = re.compile(r"^data/render/[^/]+\.(png|jpg)$")
# A past level's picture (wowstats.pictures): drawn by the renderer, WebP.
PICTURE_FILE = re.compile(r"^data/picture/[^/]+/\d{1,2}\.webp$")


def image_file(path: str, body: bytes) -> bool:
    """An icon or a render: checked as an image, not as an answer."""
    if ICON_FILES.match(path):
        assert body[:2] == b"\xff\xd8", f"{path}: not a JPEG"
        return True
    if RENDER_FILE.match(path):
        assert body[:3] == b"\xff\xd8\xff" or body[:8] == b"\x89PNG\r\n\x1a\n", f"{path}: not an image"
        return True
    if PICTURE_FILE.match(path):
        assert body[:4] == b"RIFF" and body[8:12] == b"WEBP", f"{path}: not a WebP image"
        return True
    return False


def key_paths(v, prefix=""):
    if isinstance(v, dict):
        for k, x in v.items():
            yield prefix + k
            yield from key_paths(x, prefix + k + ".")
    elif isinstance(v, list):
        for x in v:
            yield from key_paths(x, prefix[:-1] + "[]." if prefix else "[].")


def leaves(v):
    if isinstance(v, dict):
        for x in v.values():
            yield from leaves(x)
    elif isinstance(v, list):
        for x in v:
            yield from leaves(x)
    elif v is not None:
        yield v


def assert_public_allowlist(pub: dict) -> set:
    """Every file of the public bundle is the page or an allowed answer, and
    every key in every answer is on ALLOWED. Returns the answers seen."""
    seen = set()
    for path, body in pub.items():
        if image_file(path, body):
            continue
        if not path.startswith("data/"):
            assert PAGE_FILES.match(path), f"{path}: not a file the public site has"
            continue
        assert not path.startswith("data/icon/"), f"{path}: not an icon the page has"
        what = path.split("/")[1].removesuffix(".json")
        assert what in ALLOWED, f"{path}: the public site has no {what} answer"
        d = json.loads(body)
        extra = set(key_paths(d)) - ALLOWED[what]
        if what == "model3d":
            # A recipe holds numbers and true/false only: no name can hide in it.
            for r in (d.get("recipe"), d.get("recipe_sd")):
                assert not any(isinstance(x, str) for x in leaves(r)), f"{path}: text in a recipe"
            extra = {k for k in extra if not k.startswith(("recipe.", "recipe_sd."))}
        assert not extra, f"{path}: {sorted(extra)} not on the public allow-list"
        seen.add(what)
    return seen


def test_public_bundle_holds_only_the_allowlist(site):
    _, _, _, cur = site
    seen = assert_public_allowlist(files(cur / "public"))
    assert seen == {"config", "chars", "char", "timeline", "snapshot", "items"}  # 3D off in this fixture


def test_allowlist_walk_catches_a_new_field(site, monkeypatch):
    """The walk itself works: a field slipped past redact fails it."""
    _, _, _, cur = site
    pub = files(cur / "public")
    rel = next(p for p in pub if p.startswith("data/char/"))
    d = json.loads(pub[rel])
    d["header"]["zone"] = "Elwynn Forest"
    with pytest.raises(AssertionError, match="header.zone"):
        assert_public_allowlist({rel: json.dumps(d).encode()})
    with pytest.raises(AssertionError, match="no journal answer"):
        assert_public_allowlist({"data/journal/x.json": b"{}"})


class VmWow(PoisonStore):
    """vm-wow holding one character's look; `choice` is what the exporter's
    last daily refresh wrote."""

    choice = "12"

    def profile_query(self, q):
        if "wow_character_customization" in q:
            return [{"metric": {"character": "Hero-Test", "option_id": "1"}, "value": [1, VmWow.choice]}]
        return []


def test_rebuilds_when_battle_net_data_changes(tmp_path, monkeypatch):
    """The exporter refreshes the look once a day with no game session: the
    events alone never said so, and the site kept the old look (2026-11-18)."""

    def store(vl, archive="", vm="", *a, **k):
        s = VmWow()
        s.vm_url = vm
        return s

    monkeypatch.setattr(ui, "Store", store)
    env = {"VL_URL": "http://vl.invalid", "VM_URL": "http://vm.invalid", "MODEL3D": "off",
           "PUBLISH_DIR": str(tmp_path / "site"), "ZOGZOG_PATH": ""}
    VmWow.choice = "12"
    with redirect_stdout(io.StringIO()):
        assert publish.publish(env)["built"]
        assert publish.publish(env)["built"] is False, "nothing changed"
        VmWow.choice = "14"
        res = publish.publish(env)
    assert res["built"] and res["why"] == "Battle.net data changed"
    with redirect_stdout(io.StringIO()):
        assert publish.publish(env)["built"] is False


def test_vm_wow_down_neither_rebuilds_nor_fails(tmp_path, monkeypatch):
    def store(vl, archive="", vm="", *a, **k):
        s = VmWow()
        s.vm_url = vm
        return s

    monkeypatch.setattr(ui, "Store", store)
    env = {"VL_URL": "http://vl.invalid", "VM_URL": "http://vm.invalid", "MODEL3D": "off",
           "PUBLISH_DIR": str(tmp_path / "site"), "ZOGZOG_PATH": ""}
    with redirect_stdout(io.StringIO()):
        assert publish.publish(env)["built"]

    def down(self):
        raise publish.ui.SourceError("vm.invalid: down")

    monkeypatch.setattr(VmWow, "profile_fingerprint", down)
    with redirect_stdout(io.StringIO()):
        assert publish.publish(env)["built"] is False


def test_rebuilds_every_24_hours_whatever_happened(site):
    """Battle.net data in the bundle is never older than a day, so the
    30-day limit of Blizzard's API terms holds in the published copies even
    with no session and no exporter change."""
    env, *_ = site
    state_f = Path(env["PUBLISH_DIR"]) / "state.json"
    state = json.loads(state_f.read_text())
    state["built_at"] -= publish.MAX_AGE_S - 60
    state_f.write_text(json.dumps(state))
    with redirect_stdout(io.StringIO()):
        assert publish.publish(env)["built"] is False, "23 h 59 min: not yet"
    state["built_at"] -= 120
    state_f.write_text(json.dumps(state))
    with redirect_stdout(io.StringIO()):
        res = publish.publish(env)
    assert res["built"] and res["why"] == "older than 24 h"
    assert publish.MAX_AGE_S <= 86400


@pytest.fixture
def server(site):
    env, _, key, cur = site
    s = static.Site({"SITE_DIR": str(cur.parent), "ZOGZOG_PATH": "/zogzog/"})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), static.make_handler(s))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def get(path, method="GET"):
        req = urllib.request.Request(base + path, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    get.key = key
    yield get
    httpd.shutdown()


def test_static_server_serves_files_and_nothing_else(server):
    code, headers, _ = server("/")
    assert code == 200 and "default-src 'none'" in headers["Content-Security-Policy"]
    assert "http" not in headers["Content-Security-Policy"] and headers["Referrer-Policy"] == "no-referrer"
    assert server("/zogzog/")[0] == 200
    assert server("/zogzog/" + publish.sealed_name(publish.unb64u(server.key), "data/chars.json"))[0] == 200
    assert server("/zogzog/data/chars.json.enc")[0] == 404  # the name before 0.7.0: gone
    assert server("/zogzog/data/chars.json")[0] == 404
    for path in ("/api/char?name=x", "/api/config", "/mcp", "/full/", "/ingest", "/../state.json", "/%2e%2e/state.json",
                 "/data/people/x.json", "/.build-1/x", "/zogzog/../../zogzog.key"):
        assert server(path)[0] == 404, path
    for method in ("POST", "PUT", "DELETE"):
        assert server("/", method)[0] == 405
    code, headers, _ = server("/model3d/view")
    csp = headers["Content-Security-Policy"]
    assert code == 200 and "sandbox allow-scripts" in csp and "eval" not in csp


def test_the_key_never_reaches_the_server(server, capsys):
    # A browser never sends the fragment; even a client that put it in the
    # path gets a 404 and the log shows the path only.
    server("/zogzog/")
    out = capsys.readouterr().out
    assert "GET /zogzog/ 200" in out


def upload_server(tmp_path, name):
    (tmp_path / "token").write_text("t" * 40)
    s = static.Site({"SITE_DIR": str(tmp_path / name), "UPLOAD_PORT": "1",
                     "UPLOAD_TOKEN_FILE": str(tmp_path / "token")})
    up = ThreadingHTTPServer(("127.0.0.1", 0), static.make_upload_handler(s))
    threading.Thread(target=up.serve_forever, daemon=True).start()
    return s, up


def test_push_reaches_every_pod(site, tmp_path, monkeypatch):
    """The 0.2.1 rollout: through a load-balanced Service the check and the
    push each landed on a random pod, and the new one stayed unready (no
    bundle, 404 on /) until chance sent it a build. Every address the name
    resolves to (a headless Service: every pod, ready or not) gets it, on
    every pass."""
    env, _, _, cur = site
    a, up_a = upload_server(tmp_path, "pod-a")
    b, up_b = upload_server(tmp_path, "pod-b")
    ports = [up_a.server_address[1], up_b.server_address[1]]
    asked = []
    real = publish.socket.getaddrinfo

    def getaddrinfo(host, port, *args, **kw):
        if host != "wow-site-upload.wow.svc.cluster.local.":
            return real(host, port, *args, **kw)  # urllib's own connect
        asked.append((host, port))
        # Two pods behind one name; here one address and two ports.
        return [(2, 1, 6, "", ("127.0.0.1", p)) for p in ports]

    monkeypatch.setattr(publish.socket, "getaddrinfo", getaddrinfo)
    env = {**env, "PUBLISH_PUSH_URL": "http://wow-site-upload.wow.svc.cluster.local.:8081",
           "PUBLISH_PUSH_TOKEN_FILE": str(tmp_path / "token")}
    assert publish.push_targets(env["PUBLISH_PUSH_URL"]) == [f"http://127.0.0.1:{p}" for p in ports]
    assert asked[0] == ("wow-site-upload.wow.svc.cluster.local.", 8081)
    out = io.StringIO()
    with redirect_stdout(out):
        publish.push(env, cur.resolve())
    assert a.live() == b.live() == cur.resolve().name
    assert out.getvalue().count("pushed ") == 2
    # A pod restarted (an emptyDir: it starts with nothing) gets the build
    # again at the next pass; the one that has it isn't sent it twice.
    up_b.shutdown()
    up_b.server_close()
    b2, up_b2 = upload_server(tmp_path, "pod-b2")
    ports[1] = up_b2.server_address[1]
    assert b2.live() == ""
    out = io.StringIO()
    with redirect_stdout(out):
        publish.push(env, cur.resolve())
    assert b2.live() == cur.resolve().name and out.getvalue().count("pushed ") == 1
    # One pod down doesn't keep the other waiting; the error still shows.
    up_b2.shutdown()
    up_b2.server_close()
    # The dead pod's port must stay refused: under xdist another worker can
    # bind a freed port. A socket bound and never listening is held for the
    # rest of the test, so connecting is refused and nobody else can take it.
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    ports[1] = dead.getsockname()[1]
    shutil.rmtree(tmp_path / "pod-a")
    a3, up_a3 = upload_server(tmp_path, "pod-a3")
    ports[0] = up_a3.server_address[1]
    with redirect_stdout(io.StringIO()), pytest.raises(OSError):
        publish.push(env, cur.resolve())
    assert a3.live() == cur.resolve().name
    dead.close()
    for up in (up_a, up_a3):
        up.shutdown()
        up.server_close()


def test_push_keeps_the_name_for_https(monkeypatch):
    monkeypatch.setattr(publish.socket, "getaddrinfo", lambda *a, **k: pytest.fail("resolved an https name"))
    assert publish.push_targets("https://site.example/") == ["https://site.example"]


@pytest.fixture
def site_stats(tmp_path, monkeypatch):
    def store(vl, archive="", vm="", *a, **k):
        s = PoisonStore()
        s.vm_url = vm
        return s

    monkeypatch.setattr(ui, "Store", store)
    env = {"VL_URL": "http://vl.invalid", "VM_URL": "http://vm.invalid", "MODEL3D": "off",
           "PUBLISH_DIR": str(tmp_path / "site-stats"), "ZOGZOG_KEY_FILE": str(tmp_path / "zogzog-stats.key"),
           "PLAUSIBLE_DOMAIN": "wow.example.com"}
    out = io.StringIO()
    with redirect_stdout(out):
        res = publish.publish(env)
    key = Path(env["ZOGZOG_KEY_FILE"]).read_text().strip()  # never printed
    return env, res, key, tmp_path / "site-stats" / "current"


def test_analytics_off_by_default(site):
    cur = site[3]
    for bundle in ("public", "zogzog"):
        assert not (cur / bundle / "stats.js").exists()
        html = (cur / bundle / "index.html").read_text()
        assert "stats.js" not in html and "data-domain" not in html and "script.js" not in html


def test_analytics_on_the_static_sites_only(site_stats, monkeypatch):
    """Pageviews on / and /zogzog/, each with its own path and nothing more;
    never in the 3D frame. The live full view's index.html stays bare: its
    server adds the tags when it sends a page (tests/test_full_stats.py)."""
    env, _, _, cur = site_stats
    for bundle, path in (("public", "/"), ("zogzog", "/zogzog/")):
        html = (cur / bundle / "index.html").read_text()
        assert f'<script src="stats.js" data-path="{path}" defer></script>' in html
        assert ('<script src="/js/script.js" data-domain="wow.example.com" data-api="/api/event" defer></script>'
                in html)
        assert html.index("stats.js") < html.index("/js/script.js") < html.index("</head>")
        assert (cur / bundle / "stats.js").is_file()
        view = (cur / bundle / "model3d" / "view.html").read_text()
        assert "stats.js" not in view and "data-domain" not in view
    js = (cur / "public" / "stats.js").read_text()
    assert "location.href" not in js.split("*/")[-1].split("\n", 5)[-1] and "location.origin + path" in js
    monkeypatch.setattr(ui, "Store", lambda *a, **k: PoisonStore())
    full = ui.App({**env, "VIEW": "full", "AUTH": "none", "MCP_PUBLIC_URL": "", "BIND": "127.0.0.1"})
    html = full.index_html().decode()
    assert "stats.js" not in html and "data-domain" not in html and "plausible" not in html.lower()
    assert "/stats.js" not in ui.STATIC


@pytest.mark.parametrize("env", [
    {"PLAUSIBLE_DOMAIN": "wow.example.com", "PLAUSIBLE_SCRIPT": "https://plausible.io/js/script.js"},
    {"PLAUSIBLE_DOMAIN": "wow.example.com", "PLAUSIBLE_SCRIPT": "//plausible.io/js/script.js"},
    {"PLAUSIBLE_DOMAIN": "wow.example.com", "PLAUSIBLE_API": "https://plausible.io/api/event"},
    {"PLAUSIBLE_DOMAIN": "wow.example.com", "PLAUSIBLE_SCRIPT": '/js/x.js" onload="alert(1)'},
    {"PLAUSIBLE_DOMAIN": 'wow.example.com" onload="x'},
])
def test_analytics_refuses_another_origin(env):
    with pytest.raises(SystemExit):
        publish.analytics(env)


def test_analytics_setting_rebuilds(site):
    env, *_ = site
    with redirect_stdout(io.StringIO()):
        assert publish.publish(env)["built"] is False
        res = publish.publish({**env, "PLAUSIBLE_DOMAIN": "wow.example.com"})
    assert res["built"] and res["why"] == "new version or page settings"


def test_push_to_the_upload_port(site, tmp_path):
    env, _, _, cur = site
    (tmp_path / "token").write_text("t" * 40)
    s = static.Site({"SITE_DIR": str(tmp_path / "pod"), "UPLOAD_PORT": "1",
                     "UPLOAD_TOKEN_FILE": str(tmp_path / "token")})
    up = ThreadingHTTPServer(("127.0.0.1", 0), static.make_upload_handler(s))
    threading.Thread(target=up.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{up.server_address[1]}"
    env = {**env, "PUBLISH_PUSH_URL": url, "PUBLISH_PUSH_TOKEN_FILE": str(tmp_path / "token")}
    publish.push(env, cur.resolve())
    assert s.live() == cur.resolve().name
    assert (tmp_path / "pod" / "current" / "public" / "index.html").is_file()
    assert sorted(files(tmp_path / "pod" / "current")) == sorted(files(cur))
    (tmp_path / "bad").write_text("x" * 40)
    with pytest.raises(urllib.error.HTTPError) as e:
        publish.push({**env, "PUBLISH_PUSH_TOKEN_FILE": str(tmp_path / "bad")}, cur.resolve())
    assert e.value.code == 401
    up.shutdown()


def test_pages_load_nothing_from_elsewhere(site, site_stats):
    # Every script, style, font and frame the pages name is in the bundle:
    # relative paths, no scheme, no leading slash (the bundle may sit under a
    # prefix). Links people click (the Wowhead credit) aren't loads. With
    # analytics on, one more: Plausible's script, a path on the site's own
    # origin that the proxy in front sends to Plausible.
    for cur, own in ((site[3], set()), (site_stats[3], {"/js/script.js"})):
        check_loads(cur, own)


def check_loads(cur, own):
    for bundle in ("public", "zogzog"):
        root = cur / bundle
        for page in ("index.html", "model3d/view.html"):
            html = (root / page).read_text()
            for ref in re.findall(r'<(?:script|link|img|iframe)[^>]*?(?:src|href)="([^"]+)"', html):
                if ref.startswith("data:"):  # inline (the favicon): no request
                    continue
                if page == "index.html" and ref in own:
                    continue
                assert "://" not in ref and not ref.startswith("/"), (bundle, page, ref)
                assert (root / Path(page).parent / ref).is_file(), (bundle, page, ref)
        css = (root / "app.css").read_text()
        for ref in re.findall(r'url\("?([^")]+)"?\)', css):
            assert ref.startswith(("data:", "#")) or (root / ref).is_file(), ref


def test_builds_without_any_3d_file(tmp_path, monkeypatch):
    """3D on, but no game file came from the shipper yet (a fresh install):
    the site builds, the answers say why, no file is copied."""

    def store(vl, archive="", vm="", *a, **k):
        s = PoisonStore()
        s.vm_url = ""
        return s

    monkeypatch.setattr(ui, "Store", store)
    env = {"VL_URL": "http://vl.invalid", "MODEL3D": "on", "MODEL3D_CACHE_DIR": str(tmp_path / "cache"),
           "MODEL3D_ASSETS_DIR": str(tmp_path / "assets"),
           "PUBLISH_DIR": str(tmp_path / "site"), "ZOGZOG_KEY_FILE": str(tmp_path / "zogzog.key")}
    with redirect_stdout(io.StringIO()):
        res = publish.publish(env)
    assert res["built"] and res["files_3d"] == 0
    pub = files(tmp_path / "site" / "current" / "public")
    ans = json.loads(pub[publish.data_path("model3d", "Hero-Test")])
    assert ans["recipe"] is None and ans["why"] == "the shipper sends the game files from your computer on its next run"
    assert (tmp_path / "site" / "current" / "zogzog").is_dir()


class LookStore(PoisonStore):
    """Battle.net's look and sex for the character; the market poisoned."""

    def vm_query(self, q):
        if "wow_character_customization" in q:
            return [{"metric": {"option_id": "87"}, "value": [1, "1261"]}]
        if "wow_character_profile_refreshed" in q:
            return [{"metric": {}, "value": [1, "1795413600"]}]
        if "wow_character_profile{" in q:
            return [{"metric": {"gender": "female", "race_id": "7"}, "value": [1, "32"]}]
        return super().vm_query(q)  # the market: SECRET_TOKEN everywhere


def look_world(tmp_path, monkeypatch, fill=True):
    from tests import model3d_world as w

    def store(vl, archive="", vm="", *a, **k):
        s = LookStore()
        s.vm_url = vm
        return s

    monkeypatch.setattr(ui, "Store", store)
    w.patch_tables(monkeypatch)
    if fill:
        w.fill(tmp_path / "assets")
    return {"VL_URL": "http://vl.invalid", "VM_URL": "http://vm.invalid", "MODEL3D": "on",
            "MODEL3D_CACHE_DIR": str(tmp_path / "cache"), "MODEL3D_ASSETS_DIR": str(tmp_path / "assets"),
            "PUBLISH_DIR": str(tmp_path / "site"), "ZOGZOG_KEY_FILE": str(tmp_path / "zogzog.key")}


def test_public_bundle_has_the_battle_net_look_but_no_market(tmp_path, monkeypatch):
    """Owner's call (2026-11-18): the character's look from Battle.net's
    profile API may be public, in the 3D view too; market data (prices,
    token, auction house) never is. Both are API data, so both stay under
    the 30-day rule: the bundle is rebuilt daily and the look is read with
    last_over_time(...[2d]) from a store that keeps 23 days."""
    from tests import model3d_world as w

    env = look_world(tmp_path, monkeypatch)
    out = io.StringIO()
    with redirect_stdout(out):
        assert publish.publish(env)["built"]
    pub = files(tmp_path / "site" / "current" / "public")
    ans = json.loads(pub[publish.data_path("model3d", "Hero-Test")])
    assert ans["recipe"]["model"] == w.FX.CHAR_M2 and ans["appearance"]["source"] == "battle.net"
    assert ans["recipe"]["textures"] == {"6": w.FX.TEX_PURPLE}, "Battle.net's hair (choice 1261), not the default"
    for path, body in pub.items():
        for s in (SECRET_PRICE, SECRET_TOKEN):
            assert s.encode() not in body, f"{path} leaks market data ({s})"
    assert "model3d" in assert_public_allowlist(pub)
    # The files: what the character's recipes need, from the person's store,
    # nothing else, byte for byte; the same set in /zogzog/.
    got = {int(p.rsplit("/", 1)[1]) for p in pub if p.startswith("model3d/file/")}
    want = set()
    for r in (ans["recipe"], ans["recipe_sd"]):
        want |= w.assets.closure(w.character3d.needs(r), w.files().get)[0].keys() if r else set()
    store = w.assets.Store(tmp_path / "assets")
    want = {f for f in want if store.read(f) is not None}
    assert got and got <= want and w.FX.CHAR_M2 in got, (got, want)
    for f in got:
        assert pub[f"model3d/file/{f}"] == store.read(f)
    zz = files(tmp_path / "site" / "current" / "zogzog")
    assert {p for p in zz if p.startswith("model3d/file/")} == {f"model3d/file/{f}" for f in got}
    assert "3D files for Hero-Test in public:" in out.getvalue()


def test_a_bundle_carries_only_its_characters_files(tmp_path, monkeypatch):
    """A file the person's store holds that no recipe names (another
    character's, a texture the shipper sent for an old look) stays out."""
    from tests import model3d_world as w

    env = look_world(tmp_path, monkeypatch)
    stray = tmp_path / "assets" / "files" / "777777.blp"
    stray.write_bytes(b"BLP2" + bytes(60))
    with redirect_stdout(io.StringIO()):
        assert publish.publish(env)["built"]
    for bundle in ("public", "zogzog"):
        names = {p for p in files(tmp_path / "site" / "current" / bundle) if p.startswith("model3d/file/")}
        assert "model3d/file/777777" not in names and f"model3d/file/{w.FX.CHAR_M2}" in names


def test_static_3d_cache_rules_and_missing_file_log(tmp_path, capsys):
    """Served from a bundle: a game file by id keeps a day (an id's content
    never changes), the frame's page revalidates, and a 404 under /model3d/
    says so in the log."""
    root = tmp_path / "b1" / "public" / "model3d"
    (root / "file").mkdir(parents=True)
    (root / "file" / "100001").write_bytes(b"MD21")
    (root / "view.html").write_text("<!doctype html>")
    (tmp_path / "b1" / "public" / "index.html").write_text("<!doctype html>")
    (tmp_path / "current").symlink_to("b1")
    s = static.Site({"SITE_DIR": str(tmp_path), "ZOGZOG_PATH": ""})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), static.make_handler(s))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/model3d/"

    def get(rel):
        try:
            with urllib.request.urlopen(base + rel) as r:
                return r.status, r.headers.get("Cache-Control")
        except urllib.error.HTTPError as e:
            return e.code, None

    try:
        assert get("file/100001") == (200, "public, max-age=86400")
        assert get("view")[0] == 200 and get("view")[1] == "no-cache"
        capsys.readouterr()
        assert get("file/119159")[0] == 404
        assert "missing 3D file: /model3d/file/119159" in capsys.readouterr().out
    finally:
        httpd.shutdown()


class FakeCatalog:
    """Every item has a card and an icon; one extra card that no answer
    names must never reach a bundle."""

    JPEG = b"\xff\xd8\xff\xe0fake-icon"

    def many(self, ids):
        return {str(i): {"n": f"Item {i}", "q": 2, "i": f"inv_test_{i}", "w": ["+1 Stamina"]} for i in ids}

    def icon(self, name, size):
        return self.JPEG + name.encode() + size.encode()

    def enchant(self, eid):
        return {66: "Stamina +3"}.get(eid)


@pytest.fixture
def site_items(tmp_path, monkeypatch):
    from wowstats import itemcard
    from wowstats.plan import facts

    monkeypatch.setattr(itemcard, "shared", lambda: FakeCatalog())
    # The Dungeons panel reads through its own Store (facts.store_for), which
    # would query vl.invalid: alone, with no earlier test's answer cached,
    # the fixture failed (an xdist worker can run it first).
    monkeypatch.setattr(facts, "store_for", lambda env, like=None: like)

    def store(vl, archive="", vm="", *a, **k):
        s = PoisonStore()
        s.vm_url = vm
        return s

    monkeypatch.setattr(ui, "Store", store)
    env = {"VL_URL": "http://vl.invalid", "VM_URL": "http://vm.invalid", "MODEL3D": "off",
           "PUBLISH_DIR": str(tmp_path / "site-items"), "ZOGZOG_KEY_FILE": str(tmp_path / "zogzog-items.key")}
    out = io.StringIO()
    with redirect_stdout(out):
        publish.publish(env)
    key = Path(env["ZOGZOG_KEY_FILE"]).read_text().strip()  # never printed
    return key, tmp_path / "site-items" / "current"


def named_ids(answers: dict) -> set[int]:
    from wowstats import itemcard

    ids = set()
    for path, body in answers.items():
        if path.startswith("data/") and path.endswith(".json") and path != "data/items.json":
            ids |= itemcard.item_ids(json.loads(body))
    return ids


def test_bundles_carry_cards_and_icons_of_their_own_items_only(site_items):
    key, cur = site_items
    pub = files(cur / "public")
    assert_public_allowlist(pub)
    cards = json.loads(pub["data/items.json"])["items"]
    assert cards and {c["id"] for c in cards} == named_ids(pub)
    for c in cards:
        for size in ("large", "medium"):
            assert pub[f"data/icon/{size}/{c['i']}.jpg"].startswith(FakeCatalog.JPEG)
    # /zogzog/: the cards and the icons sealed, like every data file. Which
    # icons it holds would list the loot.
    zz = files(cur / "zogzog")
    assert not any(p.endswith(".jpg") for p in zz), "an icon in clear in /zogzog/"
    icons = [p for p in zz if p.startswith("data/icon/")]
    assert icons and all(p.endswith(".jpg.enc") for p in icons)
    k = publish.unb64u(key)
    p = icons[0].removesuffix(".enc")
    assert publish.unseal(k, p, zz[icons[0]]).startswith(FakeCatalog.JPEG)
    plain = opened(key, zz)
    zcards = json.loads(plain["data/items.json"])["items"]
    assert {c["id"] for c in zcards} == named_ids(plain)
    assert named_ids(plain) >= named_ids(pub)


def test_bundles_always_carry_items_json(site):
    """No catalog (WOW_ITEMS=off here): the file is there, empty, so the
    page's request never 404s."""
    key, cur = site[2], site[3]
    assert json.loads(files(cur / "public")["data/items.json"]) == {"items": []}
    assert json.loads(opened(key, files(cur / "zogzog"))["data/items.json"]) == {"items": []}


def test_static_page_loads_items_js(site):
    cur = site[3]
    for bundle in ("public", "zogzog"):
        html = (cur / bundle / "index.html").read_text()
        assert '<script src="items.js" defer></script>' in html
        assert html.index("items.js") < html.index('"app.js"')


RENDER_URL = "https://render.worldofwarcraft.com/classic-eu/character/x/1/2-main-raw.png"


def publish_with(tmp_path, monkeypatch, armory_answer):
    """A publish whose vm-wow holds `armory_answer`'s item level, and the
    render address an older exporter wrote (`render`), read through the real
    Store.character_armory over HTTP."""
    from datetime import datetime

    from wowstats import stores

    def series(q):
        r, il = armory_answer.get("render"), armory_answer.get("item_level")
        if "wow_character_render" in q and r:
            t = datetime.fromisoformat(r["refreshed"].replace("Z", "+00:00")).timestamp()
            return [{"metric": {"character": "x", "asset": r["asset"], "url": r["url"]}, "value": [t, str(t)]}]
        if "wow_character_item_level" in q and il is not None:
            return [{"metric": {"character": "x", "kind": "equipped"}, "value": [0, str(il)]}]
        return []

    vm_asked = []

    def http_get(url, params=None, *a, **k):
        if url.startswith("http://vm.invalid/"):
            vm_asked.append((params or {}).get("query", ""))
            return json.dumps({"data": {"result": series((params or {}).get("query", ""))}}).encode()
        raise AssertionError(f"unexpected request {url}")

    def store(vl, archive="", vm="", *a, profile_vm_url=None, **k):
        s = PoisonStore()
        s.vm_url = vm
        s.profile_vm_url = (vm if profile_vm_url is None else profile_vm_url).rstrip("/")
        s._http_profile = True  # the real reader, over the faked HTTP
        return s

    monkeypatch.setattr(stores, "http_get", http_get)
    monkeypatch.setattr(ui, "Store", store)
    # The Dungeons panel reads the planner's inputs from a store of its own:
    # here, the same faked one (as the site fixture does).
    from wowstats.plan import facts

    monkeypatch.setattr(facts, "store_for", lambda env, like=None: like)
    env = {"VL_URL": "http://vl.invalid", "VM_URL": "http://vm.invalid", "MODEL3D": "off",
           "PUBLISH_DIR": str(tmp_path / "site"), "ZOGZOG_KEY_FILE": str(tmp_path / "zz.key"),
           "MODEL3D_CACHE_DIR": str(tmp_path / "cache")}
    out = io.StringIO()
    with redirect_stdout(out):
        publish.publish(env)
    key = Path(env["ZOGZOG_KEY_FILE"]).read_text().strip()  # never printed
    assert vm_asked, "vm-wow was never asked"
    return key, tmp_path / "site" / "current", vm_asked


def test_bundles_never_carry_blizzards_render(tmp_path, monkeypatch):
    """Blizzard's renders sit on paths shared by character id across the
    Classic games: on 2026-11-21 one showed a stranger in the owner's place.
    vm-wow still holding an older exporter's render address changes nothing:
    it isn't read, nothing is downloaded, no PNG or address in a bundle, and
    the old cache of downloads goes."""
    from tests.test_public_view import CHAR

    old = tmp_path / "cache" / "render"
    old.mkdir(parents=True)
    (old / "0123.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"stranger")
    key, cur, vm_asked = publish_with(tmp_path, monkeypatch, {"item_level": 23.0, "render": {
        "url": RENDER_URL, "asset": "main-raw", "refreshed": "2026-11-18T10:00:00Z"}})
    assert not old.exists()
    assert not [q for q in vm_asked if "wow_character_render" in q]
    pub = files(cur / "public")
    assert not any(p.startswith("data/render/") for p in pub)
    g = json.loads(pub[f"data/char/{CHAR}.json"] if f"data/char/{CHAR}.json" in pub
                   else next(v for k, v in pub.items() if k.startswith("data/char")))["gear"]
    assert g["armory_item_level"] == 23 and g["armory_item_level_source"] == "battle.net"
    # No renderer here: no picture, the page keeps the 3D toggle.
    assert g["render"] is None
    assert b"render.worldofwarcraft" not in b"".join(pub.values())
    assert not any(p.startswith("data/render/") for p in opened(key, files(cur / "zogzog")))


def test_og_tags_carry_the_title_line(site):
    """The public page's link preview says what the window title says: the
    character's name and views.card_title."""
    import html as _html
    import json as _json

    cur = site[3]
    pub = _json.loads(next((cur / "public" / "data" / "char").glob("*.json")).read_text())
    hd = pub["header"]
    assert hd.get("title"), hd
    page = (cur / "public" / "index.html").read_text()
    m = re.search(r'<meta property="og:title" content="([^"]*)">', page)
    assert m and _html.unescape(m.group(1)) == f"{hd['name']} · {hd['title']}", page[:600]
    assert page.index("og:title") < page.index("</head>")


def clear_files(root: Path) -> dict:
    """Every file of a sealed bundle that isn't sealed: what anyone gets."""
    return {k: v for k, v in files(root).items() if not k.endswith(".enc")}


@pytest.mark.parametrize("public_on", [True, False])
def test_a_sealed_page_names_nobody_in_clear(tmp_path, monkeypatch, public_on):
    """/zogzog/'s HTML (head and body) and every unsealed file carry no
    character data: the preview is generic, the title comes after the data
    is opened, with the public page on or off (outside review, 2026-11-19)."""
    import html as _html

    from tests.test_public_view import CHAR

    monkeypatch.setattr(ui, "Store", lambda *a, **k: PoisonStore())
    from wowstats.plan import facts

    monkeypatch.setattr(facts, "store_for", lambda env, like=None: like)
    env = {"VL_URL": "http://vl.invalid", "VM_URL": "http://vm.invalid", "MODEL3D": "off",
           "PUBLISH_DIR": str(tmp_path / "site"), "ZOGZOG_KEY_FILE": str(tmp_path / "zogzog.key")}
    with redirect_stdout(io.StringIO()):
        publish.publish(env, public=public_on)
    cur = tmp_path / "site" / "current"
    app = ui.App({**env, "VIEW": "full", "BIND": "127.0.0.1", "AUTH": "none"}, store=PoisonStore())
    hd = app.char(CHAR)["header"]
    assert hd.get("title"), hd
    page = (cur / "zogzog" / "index.html").read_text()
    assert '<meta property="og:title" content="Leveling log">' in page
    assert 'content="profile"' not in page and "og:description" not in page
    # The page's own words: the name alone ("Hero") is also a word of app.js.
    assert not re.search(rf"\b{re.escape(hd['name'])}\b", _html.unescape(page)), page
    for path, body in clear_files(cur / "zogzog").items():
        text = _html.unescape(body.decode("utf-8", "replace"))
        for w in (CHAR, hd["title"]):
            assert w not in text, f"zogzog/{path} names {w!r} in clear"


def test_og_tags_escape_and_skip():
    assert ui.og_tags("<head></head>", None) == "<head></head>"
    out = ui.og_tags("<head></head>", {"name": 'A"b', "title": "Priest · <x>"})
    assert 'content="A&quot;b · Priest · &lt;x&gt;"' in out and "<x>" not in out


# ------------------------------------------------------------ the play analysis


def share_server(answer: dict):
    from http.server import BaseHTTPRequestHandler

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps(answer["now"]).encode()
            self.send_response(answer.get("status", 200))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_zogzog_gets_the_screened_analysis_actions(site):
    env, _, key, cur = site
    zz = opened(key, files(cur / "zogzog"))
    name = json.loads(zz["data/chars.json"])["chars"][0]["char"]
    rel = publish.data_path("analysis", name)
    assert json.loads(zz[rel]) == {"available": False, "char": None, "nudges": [], "time_budget": []}
    # The public bundle has no analysis at all.
    assert not any("analysis" in p for p in files(cur / "public"))

    go = {"text": "Run The Deadmines", "where": "Westfall", "blocked_by": "", "verdict": "now", "at_level": None,
          "next_step": "Pick up its quests in Sentinel Hill", "findings": ["k"], "money": SECRET_MONEY}
    answer = {"now": {"available": True, "chars": {name: {
        "available": True, "char": name, "patterns": ["Dungeons paid well."],
        # A description, a short gist from an older full view, one with a
        # clock time, one with no next step: none reaches the bundle.
        "nudges": [go, "Try a dungeon.", {**go, "next_step": "Log in at 21:30 in Westfall"},
                   {**go, "next_step": ""}], "secret": SECRET_MONEY,
        "time_budget": [{"level": 10, "online_s": 3600, "zone": "Elwynn"}]}}}}
    srv = share_server(answer)
    try:
        env = {**env, "ANALYSIS_SHARE_URL": f"http://127.0.0.1:{srv.server_address[1]}/analysis.json"}
        res = publish.publish(env)
        assert res["built"] and res["why"] == "analysis changed"
        zz = opened(key, files(cur / "zogzog"))
        got = json.loads(zz[rel])
        assert "patterns" not in got
        assert got["nudges"] == [{k: go[k] for k in ("text", "where", "blocked_by", "next_step", "verdict",
                                                      "at_level")}]
        assert got["time_budget"] == [{"level": 10, "online_s": 3600, "instance_s": None, "taxi_s": None,
                                       "dead_s": None, "afk_s": None, "planned_s": None}]
        assert SECRET_MONEY not in zz[rel].decode()
        assert publish.publish(env)["built"] is False
        answer["status"] = 503
        assert not publish.publish(env)["built"]
        assert publish.publish(env, force=True)["built"]
        assert json.loads(opened(key, files(cur / "zogzog"))[rel]) == got
        # Hiding then revealing a character during the outage cannot bring
        # back the old analysis from a previous, more permissive build.
        assert publish.publish(env, hide_zogzog=frozenset({name}))["built"]
        assert rel not in opened(key, files(cur / "zogzog"))
        assert publish.publish(env)["built"]
        assert not json.loads(opened(key, files(cur / "zogzog"))[rel])["available"]
        answer["status"] = 200
        # Identical upstream data must reappear when the source recovers.
        publish.publish(env)
        assert json.loads(opened(key, files(cur / "zogzog"))[rel]) == got
        answer["now"] = {"available": False, "chars": {}}
        assert publish.publish(env)["why"] == "analysis changed"
        assert not json.loads(opened(key, files(cur / "zogzog"))[rel])["available"]
        assert not publish.publish(env)["built"]
    finally:
        srv.shutdown()


class GappyCatalog(FakeCatalog):
    """One item the client tables give no icon (the question mark), one
    whose icon can't be fetched."""

    def many(self, ids):
        out = super().many(ids)
        if "5820" in out:
            del out["5820"]["i"]
        if "12998" in out:
            out["12998"]["i"] = "inv_not_on_zamimg"
        return out

    def icon(self, name, size):
        return None if name == "inv_not_on_zamimg" else super().icon(name, size)


def publish_items(tmp_path, monkeypatch, catalog, store_cls=None):
    from wowstats import itemcard

    monkeypatch.setattr(itemcard, "shared", lambda: catalog)

    def store(vl, archive="", vm="", *a, **k):
        s = (store_cls or PoisonStore)()
        s.vm_url = vm
        return s

    monkeypatch.setattr(ui, "Store", store)
    env = {"VL_URL": "http://vl.invalid", "VM_URL": "http://vm.invalid", "MODEL3D": "off",
           "PUBLISH_DIR": str(tmp_path / "site-gap"), "ZOGZOG_KEY_FILE": str(tmp_path / "zz-gap.key")}
    with redirect_stdout(io.StringIO()):
        return publish.publish(env)


def test_an_icon_that_cant_be_had_fails_the_publish_with_the_list(tmp_path, monkeypatch):
    """0.2.3 published empty frames for Forever's new items and said nothing."""
    with pytest.raises(publish.PublishError, match=r"12998 \(inv_not_on_zamimg\)"):
        publish_items(tmp_path, monkeypatch, GappyCatalog())
    assert not (tmp_path / "site-gap" / "current").exists(), "nothing published"


def test_no_item_catalog_publishes_nothing(tmp_path, monkeypatch):
    """wago.tools down on a fresh cache (2026-11-18): every item would have
    come out as the question mark. Nothing is published; the last bundle
    stays up with its icons."""
    class Down(FakeCatalog):
        cards: dict = {}

        def many(self, ids):
            return {}

    with pytest.raises(publish.PublishError, match="item catalog couldn't be read"):
        publish_items(tmp_path, monkeypatch, Down())
    assert not (tmp_path / "site-gap" / "current").exists(), "nothing published"


def test_an_item_without_an_icon_gets_the_question_mark(tmp_path, monkeypatch):
    class NoIcon(GappyCatalog):
        def icon(self, name, size):
            return FakeCatalog.icon(self, name, size)

    publish_items(tmp_path, monkeypatch, NoIcon())
    pub = files(tmp_path / "site-gap" / "current" / "public")
    assert pub["data/icon/medium/inv_misc_questionmark.jpg"].startswith(FakeCatalog.JPEG)
    assert pub["data/icon/large/inv_misc_questionmark.jpg"].startswith(FakeCatalog.JPEG)


class UnnamedQuestStore(PoisonStore):
    """Turn-ins shipped before the shipper named quests (0.2.3's journal
    printed "quest 631"): an id only, which forever-db names."""

    def vl(self, query, keys=(), ops=None, limit=0):
        rows = super().vl(query, keys, ops, limit)
        for r in rows:
            if r.get("kind") == "turnin":
                r["quest_id"], r["quest_name"] = "631", ""
        return rows


def test_quests_shipped_without_a_name_are_named_from_forever_db(tmp_path, monkeypatch):
    publish_items(tmp_path, monkeypatch, FakeCatalog(), UnnamedQuestStore)
    key = (tmp_path / "zz-gap.key").read_text().strip()
    plain = opened(key, files(tmp_path / "site-gap" / "current" / "zogzog"))
    j = json.loads(plain["data/journal/Hero-Test.json"])
    qs = [q for s in j["sessions"] for q in s["quests"]]
    assert qs and all(q["name"] == "The Thandol Span" and q["level"] == 31 for q in qs), qs


def test_a_view_that_skips_the_quest_lookup_fails_the_publish(tmp_path, monkeypatch):
    from wowstats import forever

    monkeypatch.setattr(forever, "name_quests", lambda rows, titles=None: rows)
    with pytest.raises(publish.PublishError, match="631"):
        publish_items(tmp_path, monkeypatch, FakeCatalog(), UnnamedQuestStore)


def test_zogzog_has_the_dungeons_panel(site):
    """The Dungeons panel ships in /zogzog/ (dungeons are a zogzog part),
    never in the public bundle, and holds no other player."""
    _, _, key, cur = site
    assert not any(p.startswith("data/dungeons/") for p in files(cur / "public"))
    plain = opened(key, files(cur / "zogzog"))
    got = [json.loads(b) for p, b in plain.items() if p.startswith("data/dungeons/")]
    assert got and all("dungeons" in d and "available" in d for d in got)


@pytest.mark.parametrize("bad", ["../../../../victim/b1/public/data/char/Victim", "..%2f..%2f..%2fvictim",
                                 "/tmp/victim", "a\\..\\..\\victim", "Grim\x00ble", "．．／victim"])
def test_a_stored_crafted_name_is_skipped_never_written(tmp_path, monkeypatch, capsys, bad):
    """Events stored before /ingest checked names: the publisher skips the
    character, says so, and writes nothing outside the bundle."""
    from wowstats import views

    real = views.Views.chars
    monkeypatch.setattr(views.Views, "chars", lambda self: [*real(self), {"char": bad, "events": 1}])
    monkeypatch.setattr(ui, "Store", lambda *a, **k: PoisonStore())
    from wowstats.plan import facts

    monkeypatch.setattr(facts, "store_for", lambda env, like=None: like)
    site_dir = tmp_path / "users" / "sam"
    env = {"VL_URL": "http://vl.invalid", "VM_URL": "http://vm.invalid", "MODEL3D": "off",
           "PUBLISH_DIR": str(site_dir), "ZOGZOG_KEY_FILE": str(tmp_path / "zogzog.key")}
    res = publish.publish(env)
    assert res["built"] and res["chars"] == 1
    out = capsys.readouterr().out
    assert "skipped a character whose name can't be a file name" in out
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert written and all(site_dir in p.parents or p.name == "zogzog.key" for p in written)
    assert not any("victim" in p.as_posix().lower() for p in written)


class EmptyStore(PoisonStore):
    """A new account: the store answers, with nothing in it yet."""

    def vl(self, query, keys=(), ops=None, limit=0):
        return []

    def vl_raw(self, base, query, start, end, limit=0):
        return []

    def vm_query(self, q):
        return []

    def vm_range(self, q, start, end, step):
        return []


def multi_get(site_dir: Path):
    """A several-people static server on site_dir; get(path, host)."""
    s = static.Site({"SITE_DIR": str(site_dir), "ZOGZOG_PATH": "/zogzog/", "SITE_DOMAIN": "m.example"})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), static.make_handler(s))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def get(path, host):
        req = urllib.request.Request(base + path, headers={"Host": host})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    return httpd, get


@pytest.mark.parametrize("public_on", [False, True])
def test_a_person_without_characters_gets_the_empty_page(tmp_path, monkeypatch, public_on):
    """A new account had a bare "Not found" at its own address until its
    first character arrived (samwow, 2026-11-19). Now the empty page, public
    page on or off, naming nobody; an unknown slug still gets a 404; the
    build that brings the first character replaces it with the real page."""
    from wowstats.plan import facts

    store = {"cls": EmptyStore}
    monkeypatch.setattr(ui, "Store", lambda *a, **k: store["cls"]())
    monkeypatch.setattr(facts, "store_for", lambda env, like=None: like)
    site_dir = tmp_path / "site"
    env = {"VL_URL": "http://vl.invalid", "VM_URL": "http://vm.invalid", "MODEL3D": "off",
           "PUBLISH_DIR": str(site_dir / "newbie"), "ZOGZOG_KEY_FILE": str(tmp_path / "zogzog.key")}
    with redirect_stdout(io.StringIO()):
        res = publish.publish(env, public=public_on, slug="newbie")
    assert res["built"] and res["chars"] == 0 and res["public_files"] == 1 + len(bar.FILES)  # the page, base.css, theme.js, fonts
    httpd, get = multi_get(site_dir)
    try:
        code, headers, body = get("/", "newbie-wow.m.example")
        page = body.decode()
        assert code == 200 and headers["Content-Type"].startswith("text/html")
        assert "No character yet" in page and "/reload" in page and "<script" not in page
        assert "newbie" not in page.lower()
        for rel in ("base.css", "theme.js", "fonts/cinzel.woff2", "fonts/alegreya-sans-400.woff2"):
            assert get("/" + rel, "newbie-wow.m.example")[0] == 200, rel
        # Nothing else of the page's: no data, no app.
        for path in ("/data/config.json", "/app.js", "/data/chars.json"):
            assert get(path, "newbie-wow.m.example")[0] == 404, path
        # An unknown slug, another host: the styled 404, its assets served for any host.
        for host in ("nobody-wow.m.example", "evil.example"):
            code, headers, body = get("/", host)
            assert code == 404 and b"Nothing here" in body and headers["Content-Type"].startswith("text/html")
            assert b"No character yet" not in body
            assert get("/_notice/base.css", host)[0] == 200
        for path in ("/_notice/../state.json", "/_notice/app.js", "/_notice/theme.js", "/_notice/notice.css"):
            assert get(path, "nobody-wow.m.example")[0] == 404, path

        # The first character arrives: the next build is the normal one.
        store["cls"] = PoisonStore
        with redirect_stdout(io.StringIO()):
            res = publish.publish(env, public=public_on, slug="newbie")
        assert res["built"] and res["chars"] == 1
        code, _, body = get("/", "newbie-wow.m.example")
        if public_on:
            assert code == 200 and b'data-static="1"' in body and b"No character yet" not in body
            assert get("/data/config.json", "newbie-wow.m.example")[0] == 200
        else:  # the page stays off: the 404, as for anyone
            assert code == 404 and b"Nothing here" in body
        assert get("/zogzog/", "newbie-wow.m.example")[0] == 200
    finally:
        httpd.shutdown()
        httpd.server_close()
