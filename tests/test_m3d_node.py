"""The 3D renderer's scripts (web/model3d/m3d) without a browser: each file
parses, the load order lists them all, nothing the sandboxed frame refuses,
the file readers on the synthetic files of tools/model3d_fixtures.py (BLP
decoding pixel for pixel, the M2/skin/skel/anim fields, the particle and
ribbon emitters), and the particle simulation itself (spawn areas,
velocities, gravity, caps, the seed). Needs node; CI only runs
`node --check` on the older viewer files."""

import importlib.util
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
M3D = ROOT / "web" / "model3d" / "m3d"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def fixtures():
    spec = importlib.util.spec_from_file_location("model3d_fixtures", ROOT / "tools" / "model3d_fixtures.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# m3d files that are not wow.export ports
OWN = {"particles.js"}


def order():
    return [n.strip() for n in (M3D / "ORDER.txt").read_text().splitlines() if n.strip()]


def test_order_lists_every_script_once():
    names = order()
    assert len(names) == len(set(names))
    assert sorted(names) == sorted(p.name for p in M3D.glob("*.js"))
    assert names[0] == "core.js" and names[-1] == "draw.js"


@pytest.mark.parametrize("name", sorted(p.name for p in M3D.glob("*.js")))
def test_script_is_frame_safe(name):
    text = (M3D / name).read_text()
    # view_page inlines the files and refuses these; the frame's CSP would
    # refuse eval and WebAssembly once the viewer drops 'unsafe-eval'
    for bad in ("</script", "</style", "<!--"):
        assert bad not in text.lower(), (name, bad)
    for bad in ("eval(", "new Function", "WebAssembly", "import(", "XMLHttpRequest", "globalThis.fetch"):
        assert bad not in text, (name, bad)
    head = text.split("*/", 1)[0]
    if name == "spells.js":
        assert "shipped spell visual kits" in head
        return
    if name == "actions.js":
        assert "TrinityCore" in head and "this viewer" in head
        return
    if name in OWN:
        # written for wow-stats, not a port: say where it comes from
        assert "wowdev.wiki" in head and "wow.export" in head and "not a port" in head
        return
    assert "Kruithne <kruithne@gmail.com>" in head, "keep wow.export's notice at the top, as upstream has it"
    assert re.search(r"[Pp]orted for wow-stats", head)


@needs_node
@pytest.mark.parametrize("name", sorted(p.name for p in M3D.glob("*.js")))
def test_node_check(name):
    subprocess.run([NODE, "--check", str(M3D / name)], check=True, capture_output=True, timeout=60)


@needs_node
def test_spell_moves_require_recipe_rows():
    script = """
    globalThis.M3D = {};
    require(process.argv[1]);
    const names = spells => M3D.moves(17, spells).map(m => m.name);
    console.log(JSON.stringify([names([]), names([{name:'ranged'}, {name:'shield'}])]));
    """
    out = subprocess.run([NODE, "-e", script, str(M3D / "actions.js")], check=True, capture_output=True, timeout=10)
    without, with_spells = json.loads(out.stdout)
    assert without == ["attack", "roar", "talk", "exclaim", "question", "bow", "cheer", "laugh"]
    assert with_spells == ["attack", "ranged", "shield", *without[1:]]


@needs_node
def test_renderer_plays_client_animation_aliases_but_rejects_stand_and_broken_aliases():
    script = """
    const base = process.argv[1];
    for (const file of ['core', 'gl', 'm2renderer']) require(base + '/' + file + '.js');
    const model = Object.create(M3D.M2RendererGL.prototype);
    model.updateAnimation = () => {};
    model.m2 = { animations: [
        {id: 0, flags: 32, duration: 500}, {id: 51, flags: 32, duration: 1000},
        {id: 52, flags: 64, aliasNext: 1}, {id: 95, flags: 64, aliasNext: 0},
        {id: 46, flags: 64, aliasNext: 4}, {id: 49, flags: 64, aliasNext: 9}
    ] };
    (async () => {
        const playable = await Promise.all([2,3,4,5].map(index => model.prepareAnimation(index)));
        await model.playAnimation(2);
        console.log(JSON.stringify([playable, model._current_anim().id, model.animation_duration(2)]));
    })();
    """
    out = subprocess.run([NODE, "-e", script, str(M3D)], check=True, capture_output=True, timeout=10)
    assert json.loads(out.stdout) == [[True, False, False, False], 51, 1]


NODE_READERS = r"""
const fs = require('fs'), path = require('path'), vm = require('vm');
const [m3d, dir] = process.argv.slice(2);
for (const f of ['core.js', 'blp.js', 'm2.js'])
	vm.runInThisContext(fs.readFileSync(path.join(m3d, f), 'utf8'), { filename: f });
const rd = (id) => {
	const p = path.join(dir, String(id));
	return fs.existsSync(p) ? new M3D.Reader(new Uint8Array(fs.readFileSync(p))) : null;
};
const ids = JSON.parse(fs.readFileSync(path.join(dir, 'ids.json'), 'utf8'));
(async () => {
	const out = { blp: {}, m2: {}, skel: {} };
	for (const id of ids.blp) {
		const b = new M3D.BLPImage(rd(id));
		const px = Array.from(b.toUInt8Array(0, 0b1111));
		out.blp[id] = { w: b.scaledWidth, h: b.scaledHeight, px };
	}
	for (const id of ids.m2) {
		const m = new M3D.M2Loader(rd(id));
		m.load();
		const s = m.skins[0];
		s.load(rd(s.fileDataID));
		if (m.bones.length)
			await m.loadAnimsForIndex(0, async (f) => rd(f));
		out.m2[id] = {
			vertices: m.vertices.length / 3, txid: m.textures.map(t => t.fileDataID), types: m.textureTypes,
			bones: m.bones.map(b => ({ parent: b.parentBone, crc: b.boneNameCRC, pivot: Array.from(b.pivot),
				trans: b.translation.values[0] || [] })),
			attachments: m.attachments.map(a => ({ id: a.id, bone: a.bone, position: a.position })),
			skel: m.skeletonFileID || 0, anims: m.animations.map(a => a.id),
			geosets: s.subMeshes.map(x => x.submeshID), triangles: s.triangles.length,
			units: s.textureUnits.map(t => [t.skinSectionIndex, t.textureComboIndex])
		};
	}
	for (const id of ids.skel) {
		const k = new M3D.SKELLoader(rd(id));
		k.load();
		await k.loadAnimsForIndex(0, async (f) => rd(f));
		out.skel[id] = {
			parent: k.parent_skel_file_id || 0, anims: k.animations.map(a => [a.id, a.flags]),
			bones: k.bones.map(b => ({ parent: b.parentBone, crc: b.boneNameCRC, rot: b.rotation.values[0] || [] })),
			attachments: (k.attachments || []).map(a => ({ id: a.id, bone: a.bone, position: a.position }))
		};
	}
	process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


@pytest.fixture(scope="module")
def read(tmp_path_factory):
    if NODE is None:
        pytest.skip("node is not installed")
    fx = fixtures()
    s = fx.scene()
    d = tmp_path_factory.mktemp("m3d-files")
    for fid, data in s["files"].items():
        (d / str(fid)).write_bytes(data)
    ids = {"blp": sorted(s["blps"]),
           "m2": [fx.CHAR_M2, fx.ITEM_M2, fx.VISUAL_M2, fx.BELT_M2, fx.SKINNED_M2],
           "skel": [fx.CHILD_SKEL, fx.PARENT_SKEL]}
    (d / "ids.json").write_text(json.dumps(ids))
    out = subprocess.run([NODE, "-", str(M3D), str(d)], input=NODE_READERS, text=True, capture_output=True,
                         timeout=120)
    assert out.returncode == 0, out.stderr
    return fx, s, json.loads(out.stdout)


def test_blp_decodes_every_encoding(read):
    fx, s, got = read
    assert len(s["blps"]) >= 12
    for fid, (w, h, expect, _) in s["blps"].items():
        b = got["blp"][str(fid)]
        assert (b["w"], b["h"]) == (w, h), fid
        px = [tuple(b["px"][i:i + 4]) for i in range(0, len(b["px"]), 4)]
        assert px == [tuple(p) for p in expect], fid


def test_blp_4bit_alpha_reads_each_nibble(read):
    # wow.export read odd texels' nibble at a fractional index and scaled by
    # 16: texel 5 (half alpha, 8) must come out as 136, its neighbours 255
    fx, _, got = read
    a = got["blp"][str(fx.TEX_PURPLE)]["px"][3::4]
    assert a[4:7] == [255, 136, 255]


def test_m2_fields(read):
    fx, _, got = read
    char = got["m2"][str(fx.CHAR_M2)]
    assert char["skel"] == fx.CHILD_SKEL and char["bones"] == []
    assert char["txid"] == [0, fx.TEX_RED, fx.TEX_ALPHA, 0] and char["types"] == [1, 0, 0, 6]
    assert char["geosets"] == [0, 301, 401, 402, 501, 503, 1701] and char["triangles"] == 7 * 36
    assert char["units"] == [[0, 0], [1, 1], [2, 2], [3, 3], [4, 1], [5, 1], [6, 2]]

    item = got["m2"][str(fx.ITEM_M2)]
    assert item["anims"] == [0]
    assert item["attachments"] == [{"id": 1, "bone": 0, "position": [0, 0, pytest.approx(0.2)]},
                                   {"id": 2, "bone": 0, "position": [0, 0, pytest.approx(-0.4)]}]
    # its raw .anim (no chunks), WoW z up -> GL y up
    assert [v[1] for v in item["bones"][0]["trans"]] == [0, pytest.approx(0.15), 0]

    belt = got["m2"][str(fx.BELT_M2)]
    assert [b["crc"] for b in belt["bones"]] == [fx.CRC_HEAD, fx.CRC_ROOT]
    assert belt["bones"][0]["pivot"] == [0, 2, 0]  # WoW (0, 0, 2) -> GL (0, 2, -0)


def test_skeletons(read):
    fx, _, got = read
    child, parent = got["skel"][str(fx.CHILD_SKEL)], got["skel"][str(fx.PARENT_SKEL)]
    assert child["parent"] == fx.PARENT_SKEL and parent["parent"] == 0
    assert child["anims"] == [[0, 0]] and parent["anims"] == [[0, 0]]
    assert parent["attachments"] == [{"id": 1, "bone": 0, "position": [0, pytest.approx(0.7), pytest.approx(0.8)]}]
    # Stand tilts the head: the child's AFSB (+30 degrees) and the parent's
    # AFM2 (-30); compressed quaternions back to floats, x stays x
    s = 0.2588
    assert [round(q[0], 3) for q in child["bones"][1]["rot"]] == [0, round(s, 3), 0]
    assert [round(q[0], 3) for q in parent["bones"][1]["rot"]] == [0, round(-s, 3), 0]
    assert child["bones"][0]["rot"] == []


NODE_PARTICLES = r"""
const fs = require('fs'), path = require('path'), vm = require('vm');
const [m3d, dir] = process.argv.slice(2);
for (const f of ['core.js', 'm2.js', 'particles.js'])
	vm.runInThisContext(fs.readFileSync(path.join(m3d, f), 'utf8'), { filename: f });
const ids = JSON.parse(fs.readFileSync(path.join(dir, 'ids.json'), 'utf8'));
const load = (id) => {
	const m = new M3D.M2Loader(new M3D.Reader(new Uint8Array(fs.readFileSync(path.join(dir, String(id))))));
	m.load();
	return m;
};
// a renderer without GL: identity bones and model, the clock moved by hand
function run(id, seed, steps, dt, sys) {
	const m = load(id);
	const r = sys ? sys.r : {
		m2: m, bones: m.bones, model_matrix: new Float32Array(M3D.math.IDENTITY_MAT4), textures: new Map(),
		bone_matrices: new Float32Array(16 * Math.max(1, m.bones.length)).map((_, i) => (i % 16) % 5 === 0 ? 1 : 0),
		current_animation: m.animations.length ? 0 : null, current_anim_index: 0, animation_time: 0,
		_current_anim() { return m.animations[0]; }
	};
	const ps = sys || new M3D.ParticleSystem(r, seed);
	for (let i = 0; i < steps; i++) {
		const len = m.animations.length ? m.animations[0].duration / 1000 : 0;
		r.animation_time = len ? (r.animation_time + dt) % len : r.animation_time + dt;
		ps.update(dt);
	}
	return ps;
}
const parts = (ps, i) => ps.emitters[i].parts.map(p => ({ p: Array.from(p.p), v: Array.from(p.v), age: p.age, life: p.life }));
const out = { parse: {} };
for (const id of [ids.glow, ids.glow_off, ids.spread, ids.ribbon]) {
	const m = load(id);
	out.parse[id] = {
		error: m.emitterError || null,
		particles: m.particles.map(p => ({ blend: p.blend, type: p.emitterType, bone: p.bone, texture: p.texture,
			rate: p.rate.values[0] || null, enabled: p.enabled.values[0] || null, color: p.color, alpha: p.alpha,
			scale: p.scale, width: p.areaWidth.values[0], length: p.areaLength.values[0],
			gravity: p.gravity.values[0], lifespan: p.lifespan.values[0], twinklePercent: p.twinklePercent })),
		ribbons: m.ribbons.map(r => ({ bone: r.bone, position: r.position, textures: r.textures, materials: r.materials,
			color: r.color.values[0], above: r.above.values[0], below: r.below.values[0],
			edgesPerSecond: r.edgesPerSecond, edgeLifetime: r.edgeLifetime }))
	};
}
let g = run(ids.glow, 1, 1, 0.1);
out.burst = parts(g, 0);
g = run(ids.glow, 1, 1, 0.8, g);
out.burst_later = parts(g, 0);
out.off = parts(run(ids.glow_off, 1, 1, 0.1), 0);
const first = run(ids.spread, 1, 1, 1 / 30);
out.sphere = parts(first, 0);
out.plane = parts(first, 1);
out.falling = parts(run(ids.spread, 1, 15, 1 / 30), 1);
const full = run(ids.spread, 1, 90, 1 / 30);
out.capped = [full.emitters[0].parts.length, full.emitters[1].parts.length];
out.seed_a = parts(run(ids.spread, 7, 6, 1 / 30), 0);
out.seed_b = parts(run(ids.spread, 7, 6, 1 / 30), 0);
out.seed_c = parts(run(ids.spread, 8, 6, 1 / 30), 0);
// one long frame (a hidden tab) simulates at most 2 s
out.catch_up = run(ids.spread, 1, 1, 3600).stats();
const rb = run(ids.ribbon, 1, 30, 1 / 30);
out.ribbon = { edges: rb.ribbons[0].edges.map(e => ({ top: e.top, bot: e.bot, age: e.age })) };
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def sim(tmp_path_factory):
    if NODE is None:
        pytest.skip("node is not installed")
    fx = fixtures()
    s = fx.scene()
    d = tmp_path_factory.mktemp("m3d-emitters")
    for fid, data in s["files"].items():
        (d / str(fid)).write_bytes(data)
    ids = {"glow": fx.GLOW_M2, "glow_off": fx.GLOW_OFF_M2, "spread": fx.SPREAD_M2, "ribbon": fx.RIBBON_M2}
    (d / "ids.json").write_text(json.dumps(ids))
    out = subprocess.run([NODE, "-", str(M3D), str(d)], input=NODE_PARTICLES, text=True, capture_output=True,
                         timeout=120)
    assert out.returncode == 0, out.stderr
    return fx, json.loads(out.stdout)


def test_emitters_read_back_as_written(sim):
    fx, got = sim
    for model in (fx.GLOW_M2, fx.GLOW_OFF_M2, fx.SPREAD_M2, fx.RIBBON_M2):
        assert got["parse"][str(model)]["error"] is None
    glow = got["parse"][str(fx.GLOW_M2)]["particles"]
    assert len(glow) == 1
    p = glow[0]
    assert (p["blend"], p["type"], p["bone"], p["texture"]) == (4, 1, 0, 0)
    assert p["rate"] == [100, 0] and p["enabled"] is None and p["lifespan"] == [1]
    # the fake animation blocks: fixed16 life fractions, colours 0..255
    assert p["color"] == {"times": [0, 1], "values": [[255, 0, 0], [0, 0, 255]]}
    assert p["alpha"] == {"times": [0], "values": [32767]}
    assert p["scale"]["values"] == [[pytest.approx(0.3), pytest.approx(0.3)]]
    assert p["gravity"] == [[0, 0, -0.0]] and p["twinklePercent"] == 1
    assert got["parse"][str(fx.GLOW_OFF_M2)]["particles"][0]["enabled"] == [0]
    sphere, plane = got["parse"][str(fx.SPREAD_M2)]["particles"]
    assert sphere["type"] == 2 and plane["type"] == 1
    assert sphere["width"] == [0.5] and plane["length"] == [pytest.approx(0.4)]
    assert plane["gravity"] == [[0, 0, -4]]
    (r,) = got["parse"][str(fx.RIBBON_M2)]["ribbons"]
    assert r["bone"] == 0 and r["position"] == [0, 0, pytest.approx(1.6)]
    assert r["textures"] == [0] and r["materials"] == [0]
    assert r["color"] == [[1, 0.5, 0]] and r["above"] == [pytest.approx(0.08)]
    assert (r["edgesPerSecond"], r["edgeLifetime"]) == (60, 0.5)


def test_glow_bursts_on_its_rate_track_and_ages(sim):
    _, got = sim
    # 100 a second until 50 ms, stepped, read mid-step: the first step spawns 3
    assert len(got["burst"]) == 3
    assert all(abs(p["age"] - 2 / 30) < 1e-6 for p in got["burst"])
    assert all(p["p"] == [0, 0, 0] and p["v"] == [0, 0, 0] for p in got["burst"])
    assert len(got["burst_later"]) == 3 and all(abs(p["age"] - (0.8 + 2 / 30)) < 1e-5 for p in got["burst_later"])
    assert got["off"] == [], "an enabled track at 0 spawns nothing"


def test_sphere_and_plane_spawn_areas(sim):
    _, got = sim
    assert len(got["sphere"]) == 6 and len(got["plane"]) == 6  # 200 a second, one 1/30 s step
    for p in got["sphere"]:
        x, y, z = p["p"]
        r = math.hypot(x, y, z)
        assert 0.3 - 1e-6 <= r <= 0.5 + 1e-6
        # elevation (WoW z = GL y) within the vertical range, pi/4
        assert abs(y) <= r * math.sin(math.pi / 4) + 1e-6
        # moving outward
        assert sum(a * b for a, b in zip(p["p"], p["v"], strict=True)) > 0
    for p in got["plane"]:
        x, y, z = p["p"]
        assert abs(x) <= 0.5 and abs(z) <= 0.2 and y == 0
        # vertical range 0: straight up (WoW z, GL y)
        assert p["v"] == [0, pytest.approx(2.0), pytest.approx(0)]


def test_gravity_pulls_down(sim):
    _, got = sim
    assert got["falling"]
    for p in got["falling"]:
        assert p["v"][1] == pytest.approx(2.0 - 4.0 * p["age"], abs=1e-4)


def test_caps(sim):
    _, got = sim
    # 200 a second for 2 s lives would be 400: capped at 384 per emitter
    assert got["capped"] == [384, 384]
    assert got["catch_up"]["particles"] <= 2 * 384


def test_the_seed_decides_everything(sim):
    _, got = sim
    assert got["seed_a"] == got["seed_b"]
    assert got["seed_a"] != got["seed_c"]


def test_ribbon_keeps_its_lifetime_of_edges(sim):
    _, got = sim
    edges = got["ribbon"]["edges"]
    # 60 edges a second that live 0.5 s
    assert 29 <= len(edges) <= 31
    assert len({round(e["age"], 6) for e in edges}) == len(edges), "one edge per instant"
    assert all(e["age"] < 0.5 for e in edges)
    for e in edges:
        # identity bones: the ribbon sits at WoW (0, 0, 1.6), 0.08 above and below
        assert e["top"][1] == pytest.approx(1.68) and e["bot"][1] == pytest.approx(1.52)
