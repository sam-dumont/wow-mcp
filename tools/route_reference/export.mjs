#!/usr/bin/env node
// Polestar's route clock, exported for the coach: per leveling guide its
// expected time and each step's, and per level the expected time to ding,
// written to data/route-reference.json.
//
//   node tools/route_reference/export.mjs --polestar <checkout> [--out FILE] [--lua BIN] [--verbose] [--check]
//
// --verbose prints each run's planner warnings, grouped. --check writes
// nothing: it fails when the file it would write differs from FILE's bytes
// (the same checkout writes the same bytes).
//
// Also the dungeon trips (`trips`, tools/route_reference/trips.mjs): which
// quests Polestar's pick-up and hand-in guides take and the travel between
// them. They read data/forever-reference.json (tools/forever_reference) for
// the dungeons' areas.
//
// <checkout> is a clone of fitzforge/polestar at a release tag. Nothing in it
// is built: Polestar's planner (tools/lib/planner/timeline.js, through the
// command registry tools/planner/commands.js, `guides_list` and
// `timeline_get`) plays the guides the release ships (the compiled
// addon/Polestar_Guides*/Guides.lua), on reference data made here from the
// checkout's own files:
//
//   - quests, NPCs, objects, items, spawns: forever-db/data/*.jsonl (Polestar's
//     open database, GPL-3.0), in place of data/forever, which only
//     `forge data build` makes (Rust, plus a few hundred MB of sources);
//   - map frames: the guide pack's MapFrames.lua (Era) and forever-db's ui_map
//     (Forever);
//   - flights, boats, inns: addon/Polestar/Nav/TravelData.lua.
//
// No navigation grid (`nav build`, 750 MB): every walk is the planner's
// straight-line model, distance x detour / run speed. The baked walks the
// release ships (Legs.lua) are measured against that model and the result is
// written under `bias`. The simulator's calibration (data/sim) is local to
// Polestar's maintainers too: the clock runs on its priors.
//
// The .lua files are read by tools/route_reference/dump.lua in an empty Lua
// environment (no io, os or load): they are data from a download.

import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { buildTrips } from './trips.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SYNTH = 9000000; // synthetic map ids, one per continent, for forever-db's world-yard spawns
const SYNTH_HALF = 40000;
const RACE_DIRS = {
	Alliance: { dwarf: 'Dwarf', gnome: 'Gnome', human: 'Human', nightelf: 'NightElf', skyborne: 'Skyborne' },
	Horde: { orc: 'Orc', tauren: 'Tauren', troll: 'Troll', undead: 'Scourge', skyborne: 'Skyborne' },
};

function args(argv) {
	const o = { out: path.join(HERE, '..', '..', 'data', 'route-reference.json'), lua: process.env.LUA || 'lua' };
	for (let i = 0; i < argv.length; i++) {
		const k = argv[i];
		if (k === '--polestar') o.polestar = argv[++i];
		else if (k === '--out') o.out = argv[++i];
		else if (k === '--lua') o.lua = argv[++i];
		else if (k === '--verbose') o.verbose = true;
		else if (k === '--check') o.check = true;
		else throw new Error(`unknown argument ${k}`);
	}
	if (!o.polestar) throw new Error('usage: export.mjs --polestar <checkout> [--out FILE] [--lua BIN]');
	o.polestar = path.resolve(o.polestar);
	return o;
}

function lua(bin, addon, mode) {
	const r = spawnSync(bin, [path.join(HERE, 'dump.lua'), addon, mode], { maxBuffer: 1 << 30, encoding: 'utf8' });
	if (r.status !== 0) throw new Error(`${bin} dump.lua ${mode}: ${r.stderr || r.error}`);
	return JSON.parse(r.stdout);
}

function jsonl(file) {
	return fs.readFileSync(file, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));
}

const r1 = (v) => Math.round(v * 10) / 10;
const median = (xs) => {
	const s = [...xs].sort((a, b) => a - b);
	if (!s.length) return null;
	const m = s.length >> 1;
	return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};
const asList = (v) => (Array.isArray(v) ? v : v && typeof v === 'object' ? Object.values(v) : []);

// --- reference data from forever-db ------------------------------------------

function referenceData(db) {
	const d = (t) => jsonl(path.join(db, 'data', `${t}.jsonl`));
	const quests = {};
	for (const q of d('quest')) {
		quests[q.id] = {
			id: q.id, title: q.title, level: q.level || 0, minLevel: q.min_level || 0,
			races: q.races || null, classes: q.classes || null, side: q.side || 0,
			start: { npc: [], object: [], item: [] }, end: { npc: [], object: [] },
			pre: [], objectives: [], objectiveText: '', xp: q.xp || null, repeatable: !!q.repeatable,
			// maskExcludes reads Forever's race bits (Skyborne) only from a Forever source.
			source: ['questie-forever-db'],
		};
	}
	for (const s of d('quest_start')) quests[s.quest] && quests[s.quest].start[s.kind] && quests[s.quest].start[s.kind].push(s.id);
	for (const s of d('quest_end')) quests[s.quest] && quests[s.quest].end[s.kind] && quests[s.quest].end[s.kind].push(s.id);
	for (const p of d('quest_prereq')) if (quests[p.quest] && !p.breadcrumb) quests[p.quest].pre.push(p.pre);
	const objs = d('quest_objective').sort((a, b) => a.quest - b.quest || a.n - b.n);
	for (const o of objs) quests[o.quest] && quests[o.quest].objectives.push({ type: o.kind, id: o.target ?? null, count: o.count ?? null, text: o.text || '' });

	// An NPC's side, for the planner's "kill objective on a friendly giver is a
	// talk" rule: the sides of the quests it gives or ends (forever-db keeps
	// the faction template, not the side).
	const sideOf = new Map();
	for (const q of Object.values(quests)) {
		for (const id of [...q.start.npc, ...q.end.npc]) sideOf.set(id, (sideOf.get(id) || 0) | (q.side === 1 ? 1 : q.side === 2 ? 2 : 3));
	}
	const spawns = { npc: new Map(), object: new Map() };
	for (const s of d('spawn')) {
		const byMap = spawns[s.kind];
		if (!byMap) continue;
		const per = byMap.get(s.id) || {};
		const key = String(SYNTH + s.map);
		// x north, y west (world yards) -> percent on the synthetic frame below.
		(per[key] = per[key] || { f: 'e', pts: [] }).pts.push([r1(((SYNTH_HALF - s.y) / (2 * SYNTH_HALF)) * 1e4) / 100, r1(((SYNTH_HALF - s.x) / (2 * SYNTH_HALF)) * 1e4) / 100]);
		byMap.set(s.id, per);
	}
	const npcs = {};
	for (const n of d('npc')) {
		const side = sideOf.get(n.id);
		npcs[n.id] = {
			id: n.id, name: n.name, level: n.level_min, levelMax: n.level_max, rank: n.rank || 0,
			faction: side === 1 ? 'A' : side === 2 ? 'H' : side === 3 ? 'AH' : null, spawns: spawns.npc.get(n.id) || {},
		};
	}
	const objects = {};
	for (const o of d('object')) objects[o.id] = { id: o.id, name: o.name, spawns: spawns.object.get(o.id) || {} };
	const items = {};
	for (const i of d('item')) items[i.id] = { id: i.id, name: i.name };
	const forever = {};
	for (const m of d('ui_map')) forever[m.ui_map] = { c: m.map, left: m.left, right: m.right, top: m.top, bottom: m.bottom };
	const continents = new Set([...spawns.npc.values(), ...spawns.object.values()].flatMap((p) => Object.keys(p)));
	const synth = {};
	for (const k of continents) synth[k] = { c: Number(k) - SYNTH, left: SYNTH_HALF, right: -SYNTH_HALF, top: SYNTH_HALF, bottom: -SYNTH_HALF };
	return { quests, npcs, objects, items, forever, synth, index: JSON.parse(fs.readFileSync(path.join(db, 'index.json'), 'utf8')) };
}

// --- places: where a quest's giver and ender stand, where a dungeon opens ---------
//
// For wow-stats' "where" (the Dungeons panel: "from <NPC>, <zone> x, y").
// Every point comes from a source, never a guess:
//   - quest givers and enders: where Polestar's guides send the player to
//     them (an accept, turn-in or vendor goal's npc and live-frame map
//     point); an NPC no guide visits gets forever-db's spawn only when exactly
//     one zone map holds every spawn (a map rectangle overlaps its
//     neighbours: Auberdine sits inside Felwood's rectangle too, so the
//     smallest one isn't the zone), or `instance` (the Map id) when its spawns
//     are in a dungeon; otherwise nothing;
//   - dungeon entrances: Polestar's curated data/codex/entrances.json (each
//     with its own source), keys and attunements from data/codex/attunements.json;
//   - flight paths: TravelData.lua's taxi nodes on the entrance's map, the
//     nearest to the entrance for each side (straight line on the map).

function places(ps, ref, geo, zones, travel, guides) {
	const db = path.join(ps, 'forever-db');
	const spawns = { npc: new Map(), object: new Map() };
	for (const s of jsonl(path.join(db, 'data', 'spawn.jsonl'))) {
		const by = spawns[s.kind];
		if (!by) continue;
		(by.get(s.id) || by.set(s.id, []).get(s.id)).push(s);
	}
	const continents = new Set([...geo.live.frames.values()].map((b) => b.c));
	const named = (m) => zones[String(m)] && zones[String(m)].name;
	const used = new Set();
	// Guide points, counted per NPC: the point the guides use most.
	const seen = { npc: new Map(), object: new Map() };
	const vote = (kind, id, map, x, y) => {
		if (id == null || map == null || x == null || y == null || !named(map)) return;
		const k = `${map},${r1(x)},${r1(y)}`;
		const per = seen[kind].get(id) || seen[kind].set(id, new Map()).get(id);
		per.set(k, (per.get(k) || 0) + 1);
	};
	for (const g of guides) {
		for (const st of g.steps) {
			for (const goal of st.goals) {
				if (goal.f === 'e') continue; // an Era-frame point: the live map may differ
				if (goal.npc != null) vote('npc', goal.npc, goal.map, goal.x, goal.y);
				if (goal.object != null) vote('object', goal.object, goal.map, goal.x, goal.y);
				for (const c of goal.candidates || []) if (c.f !== 'e' && c.npc != null) vote('npc', c.npc, c.map, c.x, c.y);
			}
		}
	}
	const placeOf = (kind, id) => {
		const g = seen[kind].get(id);
		const list = spawns[kind].get(id) || [];
		if (g) {
			const [k] = [...g.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))[0];
			const [map, x, y] = k.split(',').map(Number);
			used.add(map);
			return list.length > 1 ? [map, x, y, list.length] : [map, x, y];
		}
		if (!list.length) return null;
		const sorted = [...list].sort((a, b) => a.n - b.n);
		if (sorted.every((sp) => !continents.has(sp.map))) {
			const maps = new Set(sorted.map((sp) => sp.map));
			return maps.size === 1 ? { instance: sorted[0].map } : null;
		}
		let only = null;
		for (const sp of sorted) {
			const hits = geo.containing(sp.map, sp.x, sp.y, 'l').filter((z) => named(z.map));
			if (hits.length !== 1 || (only && only.map !== hits[0].map)) return null;
			only = only || hits[0];
		}
		used.add(only.map);
		const out = [only.map, r1(only.x), r1(only.y)];
		return list.length > 1 ? [...out, list.length] : out;
	};
	const npc = {};
	const object = {};
	for (const q of Object.values(ref.quests)) {
		for (const id of [...q.start.npc, ...q.end.npc]) if (!(id in npc)) npc[id] = placeOf('npc', id);
		for (const id of [...q.start.object, ...q.end.object]) if (!(id in object)) object[id] = placeOf('object', id);
	}
	const clean = (o) => Object.fromEntries(Object.entries(o).filter(([, v]) => v).sort((a, b) => a[0] - b[0]));

	const codex = (f) => JSON.parse(fs.readFileSync(path.join(ps, 'data', 'codex', f), 'utf8'));
	const nodes = travel.taxi.nodes || [];
	// The nearest taxi node of each side on the entrance's map (TravelData's
	// faction: 1 Alliance, 2 Horde, anything else both).
	const flights = (map, x, y) => {
		const out = {};
		for (const side of ['A', 'H']) {
			const own = nodes.filter((n) => n.map === map && (n.faction === (side === 'A' ? 1 : 2) || (n.faction !== 1 && n.faction !== 2)))
				.sort((p, q) => Math.hypot(p.x - x, p.y - y) - Math.hypot(q.x - x, q.y - y) || p.id - q.id);
			if (own.length) out[side] = { name: own[0].short, x: r1(own[0].x), y: r1(own[0].y) };
		}
		return out;
	};
	const entrances = {};
	for (const e of codex('entrances.json').entrances) {
		const point = (p) => {
			used.add(p.map);
			return { zone: p.map, x: p.x, y: p.y, flights: flights(p.map, p.x, p.y) };
		};
		const src = typeof e.source === 'string' ? e.source : e.source ? `${e.source.kind}: ${e.source.ref}` : null;
		const out = { key: e.key, ...point(e), source: src };
		if (e.A) out.A = { ...point(e.A), source: typeof e.A.source === 'string' ? e.A.source : src };
		entrances[e.instance] = out;
	}
	const keys = {};
	for (const a of codex('attunements.json').attunements) {
		(keys[a.key] = keys[a.key] || []).push({
			name: a.name, kind: a.kind, need: a.need || null, item: a.item || null, goal: a.goal || [],
			faction: a.faction || null, note: a.note || null,
		});
	}
	for (const e of Object.values(entrances)) if (keys[e.key]) e.keys = keys[e.key];
	return {
		about: 'Quest givers and enders ([uiMap, x, y, spawns when more than one], from the guides first, else an unambiguous spawn; or {instance: Map id}), dungeon entrances by instance Map id (Polestar data/codex, each with its source), keys and attunements, the nearest flight path of each side on an entrance\'s map.',
		zones: Object.fromEntries([...used].sort((a, b) => a - b).map((m) => [m, named(m)])),
		npc: clean(npc),
		object: clean(object),
		entrances,
	};
}

// --- travel.json from TravelData.lua -------------------------------------------

function travelData(t) {
	const nodes = asList(t.taxi).map((r) => ({ id: r[0], c: r[1], wx: r[2], wy: r[3], map: r[4], x: r[5], y: r[6], faction: r[7], name: r[8], short: String(r[8]).split(',')[0].trim() }));
	const legs = [];
	const fl = asList(t.flights);
	for (let i = 0; i + 2 < fl.length; i += 3) legs.push({ from: fl[i], to: fl[i + 1], yards: fl[i + 2], path: null });
	const end = (e) => {
		const out = { name: e[0], c: e[1], wx: e[2], wy: e[3], map: e[4], x: e[5], y: e[6], f: e[7], area: e[8] };
		if (e[9] && typeof e[9] === 'object') out.board = { wx: e[9].wx, wy: e[9].wy };
		return out;
	};
	const transports = asList(t.transports).map((x) => ({ ...x, a: end(x.a), b: end(x.b) }));
	return { build: t.build, taxi: { nodes, legs }, transports, inns: asList(t.inns) };
}

// --- compiled guides -------------------------------------------------------------

const SPOT_KEYS = ['x', 'y', 'z', 'r', 'n', 'alt']; // tools/lib/dsl/emit.js
function unpackSpots(s) {
	return String(s).split(';').map((part) => {
		const f = part.split(',');
		const o = {};
		SPOT_KEYS.forEach((k, i) => {
			if (f[i] !== undefined && f[i] !== '') o[k] = Number(f[i]);
		});
		return o;
	}).filter((o) => Number.isFinite(o.x) && Number.isFinite(o.y));
}

function prepareGuide(g) {
	const steps = asList(g.steps).map((s) => ({
		...s,
		goals: asList(s.goals).map((goal) => {
			const out = { ...goal };
			if (goal.spotsPacked) out.spots = unpackSpots(goal.spotsPacked);
			if (goal.candidates) out.candidates = asList(goal.candidates);
			return out;
		}),
	}));
	return { ...g, steps, races: g.races ? asList(g.races) : null, classes: g.classes ? asList(g.classes) : null, nextChoices: g.nextChoices ? asList(g.nextChoices) : null };
}

// "{area:87}" -> the guide's own name for it.
function words(text, names) {
	return String(text || '').replace(/\{(\w+):(\d+)\}/g, (m, k, id) => (names && names[`${k}:${id}`]) || m);
}

// --- the bias of straight-line walks ---------------------------------------------

// Baked legs (pack format 7): the walk into a step on Leffe's maps or
// Polestar's grid. Length along the points / straight line between the ends.
function measureBias(legs, guides, decodeLeg, frames, params) {
	const cont = new Map(); // guide id -> step -> continent
	for (const g of guides) {
		cont.set(g.id, g.steps.map((s) => {
			const goal = s.goals.find((x) => x.map != null);
			const f = goal && (frames.era[goal.map] || frames.forever[goal.map]);
			return f ? f.c : null;
		}));
	}
	const acc = {};
	let n = 0;
	for (const [id, steps] of Object.entries(legs)) {
		for (const [k, leg] of Object.entries(steps)) {
			const pts = decodeLeg(String(leg));
			if (!pts || pts.length < 2) continue;
			let along = 0;
			for (let i = 1; i < pts.length; i++) along += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
			const a = pts[0];
			const b = pts[pts.length - 1];
			const straight = Math.hypot(b[0] - a[0], b[1] - a[1]);
			if (straight < 50) continue; // a walk around a hub says nothing about detours
			const c = ((cont.get(id) || [])[Number(k) - 1]) ?? 'other';
			const key = c === 0 ? 'eastern_kingdoms' : c === 1 ? 'kalimdor' : 'other';
			const e = (acc[key] = acc[key] || { legs: 0, along: 0, straight: 0, ratios: [] });
			e.legs++;
			e.along += along;
			e.straight += straight;
			e.ratios.push(along / straight);
			n++;
		}
	}
	const out = { legs: n, by_continent: {} };
	// A fixed order: the legs come out of Lua's pairs(), whose order changes
	// from run to run (5.4 seeds its string hash), and the file must not.
	const order = ['eastern_kingdoms', 'kalimdor', 'other'];
	for (const [k, e] of Object.entries(acc).sort((x, y) => order.indexOf(x[0]) - order.indexOf(y[0]))) {
		const prior = k === 'eastern_kingdoms' ? params.detour0 : k === 'kalimdor' ? params.detour1 : null;
		out.by_continent[k] = {
			legs: e.legs, detour_measured: Math.round((e.along / e.straight) * 1000) / 1000,
			detour_median: Math.round(median(e.ratios) * 1000) / 1000, detour_prior: prior,
		};
	}
	return out;
}

// --- main ------------------------------------------------------------------------

function main() {
	const o = args(process.argv.slice(2));
	const ps = o.polestar;
	const req = createRequire(path.join(ps, 'package.json'));
	const pkg = req('./package.json');
	const { buildTimeline, routeOf, loadParams } = req('./tools/lib/planner/timeline.js');
	const commands = req('./tools/planner/commands.js');
	const { Geo } = req('./tools/lib/planner/geo.js');
	const { Data } = req('./tools/lib/dsl/data.js');
	const { decodeLeg } = (() => {
		try {
			return req('./tools/lib/flow/data.js');
		} catch {
			return {};
		}
	})();

	const dumped = lua(o.lua, path.join(ps, 'addon'), 'guides');
	const ref = referenceData(path.join(ps, 'forever-db'));
	const frames = { era: dumped.frames.era || {}, forever: { ...ref.forever, ...ref.synth } };
	// Synthetic frames in the Era set too: forever-db spawns are tagged f "e".
	frames.era = { ...frames.era, ...ref.synth };
	const zones = {};
	for (const g of dumped.guides) {
		for (const [k, v] of Object.entries(g.names || {})) {
			const m = /^zone:(\d+)$/.exec(k);
			if (m) zones[m[1]] = { id: Number(m[1]), name: v, type: 3 };
		}
	}
	const travel = travelData(dumped.travel);
	const data = new Data({ quests: ref.quests, npcs: ref.npcs, objects: ref.objects, items: ref.items, zones, 'map-frames': frames, meta: {}, travel }, { dir: null, missing: [] });
	const geo = new Geo(frames, zones);
	const guides = dumped.guides.map(prepareGuide);
	const byId = new Map(guides.map((g) => [g.id, g]));
	const lazy = { get: (id) => byId.get(id), has: (id) => byId.has(id) };
	const noSim = path.join(ps, 'data', 'sim-absent'); // the priors, as a checkout without data/sim has them

	// The planner's workspace, as much of it as guides_list and timeline_get use.
	const timelines = new Map();
	const ws = {
		list: () => guides.map((g) => ({
			id: g.id, pack: g.pack, title: words(g.title, g.names), faction: g.faction || null, races: g.races, classes: g.classes,
			levels: { min: g.minLevel ?? null, max: g.maxLevel ?? null }, zone: g.zoneName || null, category: g.category || null,
			next: g.next || null, nextChoices: g.nextChoices,
		})),
		route: (a) => routeOf({ guides: lazy, start: a.start, character: a.character, choices: a.choices || {} }),
		timeline: (a) => {
			const key = JSON.stringify(a);
			if (!timelines.has(key)) {
				timelines.set(key, buildTimeline({
					guides: lazy, data, geo, params: loadParams({ simDir: noSim, cls: a.character.cls }), travel,
					start: a.start, character: a.character, choices: a.choices || {}, startLevel: a.startLevel ?? null,
					optional: !!a.optional, questXp: undefined, routes: null, nav: null, flightPaths: null,
				}));
			}
			return timelines.get(key);
		},
	};
	const run = (name, a) => commands.run(ws, name, a);

	return (async () => {
		const list = await run('guides_list', {});
		const leveling = list.filter((g) => g.category === 'Leveling');
		// Classes per side: those the leveling guides' class training names.
		const classes = { Alliance: new Set(), Horde: new Set() };
		for (const g of guides) {
			if (g.category !== 'Leveling' || !classes[g.faction]) continue;
			for (const s of g.steps) for (const goal of s.goals) if (goal.t === 'train' && goal.only && goal.only.classes) asList(goal.only.classes).forEach((c) => classes[g.faction].add(c));
		}
		// Race starts: the leveling guide no other guide hands over to, per race (the planner's defaultStart).
		const named = new Set(list.flatMap((g) => g.nextChoices || (g.next ? [g.next] : [])));
		const runs = [];
		for (const [faction, races] of Object.entries(RACE_DIRS)) {
			for (const race of Object.values(races)) {
				const start = leveling.find((g) => !named.has(g.id) && g.faction === faction && g.races && g.races.includes(race));
				if (!start) throw new Error(`no start guide for ${faction} ${race}`);
				for (const cls of [...classes[faction]].sort()) {
					const character = { race, cls, faction };
					const tl = await run('timeline_get', { start: start.id, character, view: 'full' });
					const steps = await run('timeline_get', { start: start.id, character, view: 'steps' });
					runs.push({ character, start: start.id, tl, steps });
					process.stderr.write(`${faction} ${race} ${cls}: ${(tl.totals.seconds / 3600).toFixed(1)} h, level ${tl.totals.level}, ${tl.guides.length} guides, ${tl.warnings.length} warnings\n`);
					if (o.verbose) {
						const kinds = new Map();
						for (const w of tl.warnings) {
							const k = w.message.replace(/\d+(\.\d+)?/g, 'N').replace(/"[^"]*"/g, '"…"');
							kinds.set(k, (kinds.get(k) || 0) + 1);
						}
						for (const [k, n] of [...kinds].sort((a, b) => b[1] - a[1])) process.stderr.write(`  ${n} x ${k}\n`);
						process.stderr.write(`  split ${JSON.stringify(tl.totals.split)} legs ${tl.totals.legs} hops ${tl.totals.hops}\n`);
					}
				}
			}
		}

		// Per guide and step: the median over every run that plays it.
		const perGuide = new Map();
		for (const r of runs) {
			for (const g of r.steps.guides) {
				const e = perGuide.get(g.id) || { seconds: [], levelStart: [], levelEnd: [], steps: new Map() };
				e.seconds.push(g.seconds);
				e.levelStart.push(g.levelStart);
				e.levelEnd.push(g.levelEnd);
				perGuide.set(g.id, e);
			}
			const firstOf = new Map();
			for (const s of r.steps.steps) {
				if (!firstOf.has(s.guide)) firstOf.set(s.guide, s.i);
				const e = perGuide.get(s.guide);
				const k = s.i - firstOf.get(s.guide) + 1; // 1-based, as the addon's step index
				const st = e.steps.get(k) || { title: s.title, seconds: [], applies: 0, runs: 0, levels: [] };
				st.runs++;
				if (s.levelStart != null) st.levels.push(s.levelStart);
				if (s.applies) {
					st.applies++;
					st.seconds.push(s.seconds);
				}
				e.steps.set(k, st);
			}
		}
		const guideOut = {};
		for (const g of leveling) {
			const e = perGuide.get(g.id);
			if (!e) continue;
			const src = byId.get(g.id);
			guideOut[g.id] = {
				title: g.title, faction: g.faction, levels: [g.levels.min, g.levels.max],
				seconds: Math.round(median(e.seconds)), min_s: Math.round(Math.min(...e.seconds)), max_s: Math.round(Math.max(...e.seconds)),
				level_start: r1(median(e.levelStart)), level_end: r1(median(e.levelEnd)), runs: e.seconds.length,
				// [step (1-based), seconds (median of the runs that play it), title]
				steps: [...e.steps.entries()].sort((a, b) => a[0] - b[0]).map(([k, st]) => [k, st.seconds.length ? Math.round(median(st.seconds)) : 0, words(src.steps[k - 1] ? src.steps[k - 1].title : st.title, src.names).slice(0, 80)]),
			};
		}

		// Where each guide hands each quest in, for the gear advice's quest
		// sources (wowstats.questwork): a quest on the route counts by where
		// the guide does it, not by its elite or its level. Per guide, in step
		// order: [quest id, step (1-based), the level the runs reach that step
		// at (median; levelling guides only, else null), 1 when the step is for
		// a group only (`when group`: the guide skips it alone, King
		// Bangalash's Big Game Hunter)]. Off the levelling route (Library
		// Books) the quests a guide hands in before a step are its work.
		const levelling = new Set(leveling.map((g) => g.id));
		const turnins = {};
		const sideTitles = {};
		for (const g of [...guides].sort((a, b) => (a.id < b.id ? -1 : 1))) {
			const e = perGuide.get(g.id);
			const rows = [];
			g.steps.forEach((s, i) => {
				const group = asList(s.when || []).some((w) => w && w.k === 'group') ? 1 : 0;
				const st = e && e.steps.get(i + 1);
				const level = levelling.has(g.id) && st && st.levels.length ? r1(median(st.levels)) : null;
				for (const goal of s.goals) {
					if (goal.t !== 'turnin' || goal.quest == null || rows.some((r) => r[0] === goal.quest && r[1] === i + 1)) continue;
					rows.push([goal.quest, i + 1, level, group]);
				}
			});
			if (!rows.length) continue;
			turnins[g.id] = rows;
			if (!levelling.has(g.id)) sideTitles[g.id] = words(g.title, g.names);
		}

		// Per run: seconds at each ding, and the guide it happens in.
		const routes = runs.map((r) => {
			// The start level is "reached" at 0 s, in the first guide.
			const dings = [[r.tl.character.startLevel, 0, r.start], ...r.tl.events.filter((e) => e.type === 'ding').map((e) => [e.level, Math.round(e.t), r.tl.guides[r.tl.steps[e.s].gi].id])];
			return {
				faction: r.character.faction, race: r.character.race, class: r.character.cls, start: r.start,
				total_s: Math.round(r.tl.totals.seconds), level_end: r.tl.totals.level, dings: dings.map(([l, t]) => [l, t]),
				ding_guides: dings.map((d) => d[2]),
			};
		});
		// Per side and level: the time spent in the level (ding L -> ding L+1), median over races and classes.
		const levels = {};
		for (const side of ['Alliance', 'Horde']) {
			const by = new Map();
			for (const r of routes.filter((x) => x.faction === side)) {
				const at = new Map(r.dings.map(([l, t], i) => [l, [t, r.ding_guides[i]]]));
				for (const [l, [t, gid]] of at) {
					const nextDing = at.get(l + 1);
					if (!nextDing) continue;
					const e = by.get(l) || { s: [], cum: [], guides: new Map(), byClass: {} };
					e.s.push(nextDing[0] - t);
					e.cum.push(t);
					// The guide the level is mostly spent in: where its ding to the next level happens.
					e.guides.set(nextDing[1], (e.guides.get(nextDing[1]) || 0) + 1);
					(e.byClass[r.class] = e.byClass[r.class] || []).push(nextDing[0] - t);
					by.set(l, e);
				}
			}
			levels[side] = {};
			for (const [l, e] of [...by.entries()].sort((a, b) => a[0] - b[0])) {
				levels[side][l] = {
					seconds: Math.round(median(e.s)), min_s: Math.round(Math.min(...e.s)), max_s: Math.round(Math.max(...e.s)),
					reached_s: Math.round(median(e.cum)), guide: [...e.guides.entries()].sort((a, b) => b[1] - a[1])[0][0],
					by_class: Object.fromEntries(Object.entries(e.byClass).sort().map(([c, v]) => [c, Math.round(median(v))])),
				};
			}
		}
		for (const r of routes) delete r.ding_guides;

		const params = loadParams({ simDir: noSim, cls: null });
		let bias = null;
		if (decodeLeg) bias = measureBias(lua(o.lua, path.join(ps, 'addon'), 'legs').legs, guides, decodeLeg, frames, params);

		const commit = spawnSync('git', ['-C', ps, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).stdout.trim() || null;
		const tag = spawnSync('git', ['-C', ps, 'describe', '--tags', '--exact-match'], { encoding: 'utf8' }).stdout.trim() || null;
		const doc = {
			schema: 1,
			source: `Polestar ${pkg.version} route planner (tools/lib/planner/timeline.js), played on the release's compiled guides`,
			polestar: { version: pkg.version, tag, commit, license: pkg.license, forever_db: ref.index.version || null },
			model: {
				walks: 'straight line x detour prior / run speed (no navigation grid)',
				params: 'simulator priors, uncalibrated (no data/sim)',
				reference_data: 'forever-db (Polestar\'s open database), not a forge data build',
				runs: runs.length,
				classes: Object.fromEntries(Object.entries(classes).map(([k, v]) => [k, [...v].sort()])),
				step_index: '1-based, as the addon numbers steps (Polestar_Recorder `step`)',
			},
			bias,
			levels,
			guides: guideOut,
			routes,
			turnins,
			side_guides: sideTitles,
			places: places(ps, ref, geo, zones, travel, guides),
			trips: buildTrips({ ps, travel, params, frames: ref.forever, outDir: path.join(HERE, '..', '..', 'data') }),
		};
		const text = `${JSON.stringify(doc)}\n`;
		if (o.check) {
			const have = fs.existsSync(o.out) ? fs.readFileSync(o.out, 'utf8') : '';
			if (have !== text) {
				process.stderr.write(`${o.out} is not what this checkout exports: run without --check and commit it\n`);
				process.exit(1);
			}
			process.stderr.write(`${o.out}: up to date\n`);
			return;
		}
		fs.mkdirSync(path.dirname(path.resolve(o.out)), { recursive: true });
		fs.writeFileSync(o.out, text);
		process.stderr.write(`wrote ${o.out}: ${Object.keys(guideOut).length} guides, ${runs.length} runs, ${fs.statSync(o.out).size} bytes\n`);
	})();
}

main().catch((e) => {
	process.stderr.write(`${e.stack || e}\n`);
	process.exit(1);
});
