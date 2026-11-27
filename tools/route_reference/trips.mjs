// A dungeon trip's travel, for the coach (data/route-reference.json `trips`):
// the quests Polestar's dungeon guides pick up before a run and hand in
// after it (lookup/pickup/<key>, lookup/handin/<key>), where each is given and
// taken, and the travel between them, so a trip can be timed from wherever
// the player stands (wowstats/plan/tripcost.py replays the same rules on the
// player's own quests).
//
// Polestar builds those two guides in the game, in Lua, from its Codex pages
// (addon/Polestar/Dungeons/Pickup.lua, Handin.lua); nothing exports them. This
// file ports what decides the trip's length:
//
//   - which quests a pick-up takes (PickupPlan): the dungeon's quests that
//     apply to the side, not done, open by the dungeon's lowest level, given
//     outside by an NPC or object with a place, with their open prerequisite
//     chain in order (an errand of the page done on the way, a prerequisite
//     that can't be started outside, or done only inside an instance, puts
//     the quest off), a page quest whose only reward is money left out;
//   - the breadcrumb rule (dropDetours, DETOUR/NEAR): a breadcrumb to a quest
//     the plan takes anyway is left out when its giver is on a continent no
//     other stop is on, more than 300 s out of the way, or a delivery across
//     the sea more than 150 s out of the way;
//   - the stop order (Dungeons.PlanStops): the stops at hand first (15 s),
//     exact up to 12 stops, else the cheaper of nearest-next and cheapest
//     insertion, each improved by moving runs of one to three stops;
//   - the travel cost (ns.TravelCost, Nav.EstimateTravel): walking at run
//     speed times the continent's detour prior (the planner's), flights at
//     30 yd/s plus 2 s a hop and the planner's flight overhead, boats and
//     zeppelins at their wait plus ride, whichever is cheapest.
//
// Left out (each makes the model's trip a little shorter or longer than the
// game's guide, never different in kind): the 0.4.0 carrying split of a
// prerequisite into accept, objectives and turn-in stops, the quest log's
// room, quests an item starts (from a drop outside), follow-ups the hand-in
// guide adds, exclusive groups, profession quests, the grey breadcrumb check.
//
// The page: forever-db quests whose zone is the dungeon's area
// (data/forever-reference.json areas, by instance map), plus Polestar's
// curated data/codex/journal-quests.json (its `exclude` rows taken off).
//
// Points are world yards { c, wx, wy } (forever-db spawns, TravelData nodes;
// x north, y west). Every number written is rounded, and the order of every
// list is fixed, so a rerun writes the same bytes (export.mjs --check).

import fs from 'node:fs';
import path from 'node:path';

const r1 = (v) => Math.round(v * 10) / 10;

// Pickup.lua
export const RULES = {
	detour_s: 300, // DETOUR
	near_s: 150, // NEAR: a delivery across the sea
	at_hand_s: 15, // AT_HAND
	exact: 12, // EXACT
	step_s: 75, // STEP_SECONDS: a prerequisite quest's own talks, loot and turn-in
	kill_s: 40, // KILL_SECONDS
	use_s: 30, // USE_SECONDS
	min_chance: 2, // MIN_CHANCE
	far: 1e9, // FAR
	cross_continent_s: 3600, // Core/Guides.lua CROSS_CONTINENT
	near_nodes: 6, // network nodes tried from each end of a trip
	walk_detour: 1.25, // Nav/Travel.lua WALK_DETOUR, a continent without a planner prior
};

const SIDE_BIT = { A: 1, H: 2 };
const NEUTRAL = 3;

const sqrt = Math.sqrt;
const dist = (a, b) => {
	const dx = b.wx - a.wx;
	const dy = b.wy - a.wy;
	return sqrt(dx * dx + dy * dy);
};

// --- travel ------------------------------------------------------------------------

// The planner's travel network for one side: every flight master and
// transport end as a node, all-pairs cheapest times between them (flights,
// rides, walks between nodes of one continent).
export function network(graph, params, side) {
	const bit = SIDE_BIT[side];
	const taxi = graph.taxi.filter((n) => (n[4] & bit) !== 0).map((n) => ({ id: n[0], c: n[1], wx: n[2], wy: n[3] }));
	const at = new Map(taxi.map((n, i) => [n.id, i]));
	const nodes = taxi.map((n) => ({ c: n.c, wx: n.wx, wy: n.wy }));
	const rides = [];
	const wait = Number(params.transportWait ?? 1);
	for (const [, fac, a, b, wa, ab, wb, ba] of graph.transports) {
		if ((fac & bit) === 0) continue;
		const ia = nodes.push({ c: a[0], wx: a[1], wy: a[2] }) - 1;
		const ib = nodes.push({ c: b[0], wx: b[1], wy: b[2] }) - 1;
		rides.push([ia, ib, wa * wait + ab]);
		rides.push([ib, ia, wb * wait + ba]);
	}
	const N = nodes.length;
	const INF = Infinity;
	// Flights alone first (a trip's flight overhead is paid once, not per hop).
	const nt = taxi.length;
	const fly = Array.from({ length: nt }, (_, i) => Array.from({ length: nt }, (_, j) => (i === j ? 0 : INF)));
	for (const [from, to, yards] of graph.legs) {
		const a = at.get(from);
		const b = at.get(to);
		if (a == null || b == null) continue;
		const s = (yards || 0) / params.flightSpeed + FLIGHT_STOP;
		if (s < fly[a][b]) fly[a][b] = s;
	}
	floyd(fly);
	const D = Array.from({ length: N }, (_, i) => Array.from({ length: N }, (_, j) => (i === j ? 0 : INF)));
	for (let i = 0; i < nt; i++) for (let j = 0; j < nt; j++) if (i !== j && fly[i][j] < INF) D[i][j] = fly[i][j] + params.flightOverhead;
	for (const [a, b, s] of rides) if (s < D[a][b]) D[a][b] = s;
	for (let i = 0; i < N; i++) {
		for (let j = 0; j < N; j++) {
			if (i === j || nodes[i].c !== nodes[j].c) continue;
			const w = walkSeconds(nodes[i], nodes[j], params);
			if (w < D[i][j]) D[i][j] = w;
		}
	}
	floyd(D);
	return { nodes, D };
}

const FLIGHT_STOP = 2; // timeline.js FLIGHT_STOP: seconds a hop

function floyd(D) {
	const n = D.length;
	for (let k = 0; k < n; k++) {
		const Dk = D[k];
		for (let i = 0; i < n; i++) {
			const dik = D[i][k];
			if (dik === Infinity) continue;
			const Di = D[i];
			for (let j = 0; j < n; j++) {
				const v = dik + Dk[j];
				if (v < Di[j]) Di[j] = v;
			}
		}
	}
}

export function walkSeconds(a, b, params) {
	const detour = a.c === 0 ? params.detour0 : a.c === 1 ? params.detour1 : RULES.walk_detour;
	return (dist(a, b) * detour) / params.runSpeed;
}

// Seconds from a to b ({ c, wx, wy }): a straight walk, or a walk to one of
// the network's nodes nearest a, the network, and a walk from one nearest b.
// Across continents with no way through the network: CROSS_CONTINENT.
export function travelCost(net, params, a, b) {
	if (!a || !b) return null;
	let best = a.c === b.c ? walkSeconds(a, b, params) : Infinity;
	const near = (p) => net.nodes.map((n, i) => [n, i]).filter(([n]) => n.c === p.c)
		.map(([n, i]) => [dist(p, n), i]).sort((x, y) => x[0] - y[0] || x[1] - y[1]).slice(0, RULES.near_nodes).map((x) => x[1]);
	const from = near(a);
	const to = near(b);
	for (const i of from) {
		const wa = walkSeconds(a, net.nodes[i], params);
		for (const j of to) {
			const v = wa + net.D[i][j] + walkSeconds(net.nodes[j], b, params);
			if (v < best) best = v;
		}
	}
	return best === Infinity ? RULES.cross_continent_s : best;
}

// --- stop order: Dungeons.PlanStops (Pickup.lua), without the log's room ---------

// stops = [{ inP, outP, deps: [index] }], from, finish (null: no fixed end),
// T(a, b) -> seconds. -> { order: [index], cost }
export function planStops(stops, from, finish, T) {
	const n = stops.length;
	const FAR = RULES.far;
	const t = (a, b) => {
		if (a == null || b == null) return FAR;
		if (a === b) return 0;
		const v = T(a, b);
		return v == null ? FAR : v;
	};
	const outOf = (i) => (i === 0 ? from : stops[i - 1].outP);
	const inOf = (i) => stops[i - 1].inP;
	const tail = (i) => (finish ? t(outOf(i), finish) : 0);
	const deps = stops.map((s) => new Set(s.deps.map((d) => d + 1)));
	const has = (mask, i) => Math.floor(mask / 2 ** (i - 1)) % 2 === 1;
	const ready = (mask, i) => !has(mask, i) && [...deps[i - 1]].every((d) => has(mask, d));
	const order = [];
	if (n === 0) return { order: [], cost: tail(0) };
	// Stops at hand first, nearest-next among them.
	let mask0 = 0;
	let last0 = 0;
	let cost0 = 0;
	for (;;) {
		const here = outOf(last0);
		let bc = Infinity;
		let bi = 0;
		for (let i = 1; i <= n; i++) {
			if (ready(mask0, i) && t(from, inOf(i)) <= RULES.at_hand_s) {
				const v = t(here, inOf(i));
				if (v < bc) {
					bc = v;
					bi = i;
				}
			}
		}
		if (!bi) break;
		order.push(bi);
		mask0 += 2 ** (bi - 1);
		last0 = bi;
		cost0 += bc;
	}
	const fixed = order.length;
	const prefix = () => order.slice(0, fixed);
	const done = (o) => ({ order: o.map((i) => i - 1), cost: cost(o) });
	const cost = (o) => {
		let c = 0;
		let prev = 0;
		for (const i of o) {
			c += t(outOf(prev), inOf(i));
			prev = i;
		}
		return c + tail(prev);
	};
	if (fixed === n) return done(order);
	if (n <= RULES.exact) {
		const full = 2 ** n - 1;
		const memo = new Map();
		const nextOf = new Map();
		const best = (mask, last) => {
			if (mask === full) return tail(last);
			const id = mask * (n + 1) + last;
			if (memo.has(id)) return memo.get(id);
			const here = outOf(last);
			let bc = Infinity;
			let bi = 0;
			for (let i = 1; i <= n; i++) {
				if (ready(mask, i)) {
					const v = t(here, inOf(i)) + best(mask + 2 ** (i - 1), i);
					if (v < bc) {
						bc = v;
						bi = i;
					}
				}
			}
			memo.set(id, bc);
			nextOf.set(id, bi);
			return bc;
		};
		best(mask0, last0);
		let mask = mask0;
		let last = last0;
		const o = prefix();
		while (mask !== full) {
			const i = nextOf.get(mask * (n + 1) + last);
			if (!i) break;
			o.push(i);
			mask += 2 ** (i - 1);
			last = i;
		}
		if (o.length === n) return done(o);
	}
	const valid = (o) => {
		if (o.length !== n) return false;
		const pos = new Map(o.map((i, p) => [i, p]));
		return o.every((i, p) => [...deps[i - 1]].every((d) => pos.has(d) && pos.get(d) < p));
	};
	const candidates = [];
	// Nearest-next.
	const near = prefix();
	{
		let mask = mask0;
		let last = last0;
		for (let k = fixed; k < n; k++) {
			let bc = Infinity;
			let bi = 0;
			for (let i = 1; i <= n; i++) {
				if (ready(mask, i)) {
					const v = t(outOf(last), inOf(i));
					if (v < bc) {
						bc = v;
						bi = i;
					}
				}
			}
			if (!bi) break;
			near.push(bi);
			mask += 2 ** (bi - 1);
			last = bi;
		}
	}
	candidates.push(near);
	// Cheapest insertion.
	const ins = prefix();
	const placed = new Set(ins);
	for (let k = fixed; k < n; k++) {
		let bi = 0;
		for (let i = 1; i <= n; i++) {
			if (!placed.has(i) && [...deps[i - 1]].every((d) => placed.has(d))) {
				bi = i;
				break;
			}
		}
		if (!bi) break;
		let lo = fixed;
		for (let p = fixed + 1; p <= ins.length; p++) if (deps[bi - 1].has(ins[p - 1])) lo = p;
		let bc = Infinity;
		let bp = -1;
		for (let p = lo; p <= ins.length; p++) {
			const a = p === 0 ? 0 : ins[p - 1];
			const nx = ins[p];
			const v = t(outOf(a), inOf(bi)) + (nx ? t(stops[bi - 1].outP, inOf(nx)) - t(outOf(a), inOf(nx)) : tail(bi) - tail(a));
			if (v < bc) {
				bc = v;
				bp = p;
			}
		}
		if (bp < 0) break;
		ins.splice(bp, 0, bi);
		placed.add(bi);
	}
	candidates.push(ins);
	const improve = (o0) => {
		let o = o0;
		const m = o.length;
		const outAt = (k) => (k <= 0 ? from : stops[o[k - 1] - 1].outP);
		const inAt = (k) => stops[o[k - 1] - 1].inP;
		const tailAt = (k) => tail(k <= 0 ? 0 : o[k - 1]);
		const edge = (k) => (k < m ? t(outAt(k), inAt(k + 1)) : tailAt(k));
		const movable = (a, L, b) => {
			if (b < a) {
				for (let q = b + 1; q <= a - 1; q++) for (let s = a; s <= a + L - 1; s++) if (deps[o[s - 1] - 1].has(o[q - 1])) return false;
			} else {
				for (let q = a + L; q <= b; q++) for (let s = a; s <= a + L - 1; s++) if (deps[o[q - 1] - 1].has(o[s - 1])) return false;
			}
			return true;
		};
		const moved = (a, L, b) => {
			const run = o.slice(a - 1, a - 1 + L);
			const x = [];
			for (let k = 0; k <= m; k++) {
				if (k > 0 && (k < a || k >= a + L)) x.push(o[k - 1]);
				if (k === b) x.push(...run);
			}
			return x;
		};
		let rounds = 0;
		let improved = true;
		while (improved && rounds < 50) {
			improved = false;
			rounds++;
			outer: for (let L = 1; L <= 3; L++) {
				for (let a = fixed + 1; a <= m - L + 1; a++) {
					const z = a + L - 1;
					const out = (z < m ? t(outAt(a - 1), inAt(z + 1)) : tailAt(a - 1)) - t(outAt(a - 1), inAt(a)) - edge(z);
					for (let b = fixed; b <= m; b++) {
						if (b < a - 1 || b > z) {
							const into = t(outAt(b), inAt(a)) + (b < m ? t(outAt(z), inAt(b + 1)) : tail(o[z - 1])) - edge(b);
							if (out + into < -0.5 && movable(a, L, b)) {
								o = moved(a, L, b);
								improved = true;
								break outer;
							}
						}
					}
				}
			}
		}
		return o;
	};
	let bestO = null;
	let bestC = Infinity;
	for (const c of candidates) {
		if (!valid(c)) continue;
		const o = improve(c);
		const v = cost(o);
		if (v < bestC) {
			bestC = v;
			bestO = o;
		}
	}
	return done(bestO || near);
}

// --- the dungeons' quests from forever-db ----------------------------------------------

function jsonl(file) {
	return fs.readFileSync(file, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));
}

// Every quest the trips may name, with what the rules read: side, race and
// class masks, levels, XP, prerequisites, breadcrumbs, objectives' seconds,
// where it is given and taken (each point with the side of the NPC there).
function questData(db, continents) {
	const d = (t) => jsonl(path.join(db, 'data', `${t}.jsonl`));
	const Q = new Map();
	for (const q of d('quest')) {
		Q.set(q.id, {
			id: q.id, title: q.title, level: q.level || 0, min: q.min_level || 0, xp: q.xp ?? null, side: q.side || 0,
			races: q.races || 0, classes: q.classes || 0, area: q.zone_area || null, money: q.money_copper || 0,
			start: { npc: [], object: [], item: [] }, end: { npc: [], object: [] }, pre: [], any: [], crumbs: [],
			objectives: [], rewards: 0,
		});
	}
	for (const s of d('quest_start')) Q.get(s.quest)?.start[s.kind]?.push(s.id);
	for (const s of d('quest_end')) Q.get(s.quest)?.end[s.kind]?.push(s.id);
	for (const p of d('quest_prereq')) {
		const q = Q.get(p.quest);
		if (!q) continue;
		if (p.breadcrumb) q.crumbs.push(p.pre);
		else if (p.mode === 'any') q.any.push(p.pre);
		else q.pre.push(p.pre);
	}
	for (const o of d('quest_objective').sort((a, b) => a.quest - b.quest || a.n - b.n)) Q.get(o.quest)?.objectives.push(o);
	for (const r of d('quest_reward')) if ((r.kind === 'item' || r.kind === 'choice') && Q.has(r.quest)) Q.get(r.quest).rewards++;
	// Spawns on a continent (world yards) and whether anything spawns inside an instance.
	const spawns = { npc: new Map(), object: new Map() };
	const inside = { npc: new Set(), object: new Set() };
	for (const s of d('spawn')) {
		if (!spawns[s.kind]) continue;
		if (continents.has(s.map)) {
			const list = spawns[s.kind].get(s.id) || spawns[s.kind].set(s.id, []).get(s.id);
			list.push({ c: s.map, wx: s.x, wy: s.y, n: s.n });
		} else inside[s.kind].add(s.id);
	}
	for (const list of [...spawns.npc.values(), ...spawns.object.values()]) list.sort((a, b) => a.n - b.n);
	const chance = new Map(); // item -> best NPC drop chance
	const sources = new Map(); // item -> [{ kind, id }]
	for (const s of d('item_source')) {
		if (s.kind === 'npc' && s.chance > 0 && s.chance > (chance.get(s.item) || 0)) chance.set(s.item, s.chance);
		(sources.get(s.item) || sources.set(s.item, []).get(s.item)).push(s);
	}
	return { Q, spawns, inside, chance, sources };
}

// Dungeons.ObjectiveSeconds
function objectiveSeconds(o, chance) {
	const n = Math.max(1, Number(o.count) || 1);
	if (o.kind === 'kill') return n * RULES.kill_s;
	if (o.kind === 'item') {
		const best = chance.get(o.target);
		if (best) return (n * RULES.kill_s * 100) / Math.max(RULES.min_chance, Math.min(100, best));
	}
	return n * RULES.use_s;
}

// insideOnly (Pickup.lua): an objective only an instance gives.
function insideOnly(q, data) {
	const placed = (kind, id) => (data.spawns[kind].get(id) || []).length > 0;
	const within = (kind, id) => data.inside[kind].has(id) && !placed(kind, id);
	for (const o of q.objectives) {
		if ((o.kind === 'kill' || o.kind === 'talk') && o.target != null) {
			if (within('npc', o.target)) return true;
		} else if (o.kind === 'object' && o.target != null) {
			if (within('object', o.target)) return true;
		} else if (o.kind === 'item' && o.target != null) {
			let inn = false;
			let out = false;
			for (const s of data.sources.get(o.target) || []) {
				if (s.kind !== 'npc' && s.kind !== 'object') out = true;
				else if (placed(s.kind, s.id)) out = true;
				else if (data.inside[s.kind].has(s.id)) inn = true;
				else out = true;
			}
			if (inn && !out) return true;
		}
	}
	return false;
}

// A quest's places: every spawn of its givers (or enders) on a continent, each
// [c, wx, wy, side bits of the NPC: 1 A, 2 H, 3 either], at most 8 per NPC/object, nearest
// first from nothing in particular (spawn order); `in`: one only inside.
function points(ids, data, contacts, sole) {
	const out = [];
	let inside = 0;
	let unplaced = 0;
	for (const k of ['npc', 'object']) {
		for (const id of ids[k] || []) {
			const list = data.spawns[k].get(id) || [];
			const side = k === 'object' || id === sole ? NEUTRAL : contacts.npcs[id] || 0;
			if (!list.length) {
				if (data.inside[k].has(id)) inside |= side;
				else if (k === 'npc') unplaced |= side;
			}
			for (const s of list.slice(0, 8)) out.push([s.c, r1(s.wx), r1(s.wy), side, `${k[0]}${id}`]);
		}
	}
	return { pts: out, inside, unplaced };
}

// --- the rules: PickupPlan, dropDetours, crumbsFirst, stopsOf, HandinPlan ---------

// Exported for the tests (tests/plan/test_tripcost.py runs the Python
// port against these through the written tours).
export function makeRules({ quests, T, side, player }) {
	const bit = SIDE_BIT[side];
	const Qr = (id) => quests[id] || null;
	const done = (id) => player.done.has(id);
	const inLog = (id) => player.log.has(id);
	const applies = (q) => {
		if (q.side && !(q.side & bit)) return false;
		if (player.raceBit && q.races && !(q.races & player.raceBit)) return false;
		if (q.classes && !(player.classBit && q.classes & player.classBit)) return false;
		return true;
	};
	const sideOk = (p) => (p[3] & bit) !== 0;
	const P = (p) => ({ c: p[0], wx: p[1], wy: p[2], key: p[4] });
	// Lookup.QuestEnd: the side's place nearest `from` (else any).
	const nearest = (list, from) => {
		const own = list.filter(sideOk);
		const pool = own.length ? own : [];
		let best = null;
		let bt = Infinity;
		for (const p of pool) {
			const v = from ? T(from, P(p)) : 0;
			if (v < bt) {
				bt = v;
				best = p;
			}
		}
		return best ? P(best) : null;
	};
	const giver = (q, from) => (q.item ? null : nearest(q.g || [], from));
	const ender = (q, from) => nearest(q.e || [], from);
	const startsAt = (r, endsOf) => (r.g || []).some((p) => sideOk(p) && (endsOf.e || []).some((e) => sideOk(e) && e[4] === p[4]));
	const errand = (r, from) => {
		if ((r.obj || 0) > 0 || r.item) return false;
		const g = giver(r, from);
		return Boolean(g && ender(r, g));
	};
	const startable = (r, from) => {
		const g = giver(r, from);
		return Boolean(g && ender(r, g));
	};
	const preDone = (r) => (r.pre || []).every(done) && (!(r.any || []).length || r.any.some(done));
	// Lookup.QuestChain: the open prerequisites, oldest first (all of `pre`,
	// the first of `any` that applies when none is done), each once.
	const chain = (id) => {
		const out = [];
		const seen = new Set();
		const walk = (qid) => {
			if (seen.has(qid)) return;
			seen.add(qid);
			const r = Qr(qid);
			if (!r) return;
			for (const p of r.pre || []) if (!done(p)) walk(p);
			const any = r.any || [];
			if (any.length && !any.some(done)) {
				const pick = any.find((p) => Qr(p) && applies(Qr(p)));
				if (pick != null) walk(pick);
				else out.push(any[0]); // pickupPlan rejects the missing/ineligible prerequisite
			}
			out.push(qid);
		};
		walk(id);
		return out.slice(0, -1);
	};
	return { bit, Qr, done, inLog, applies, giver, ender, startsAt, errand, startable, preDone, chain, P };
}

// PickupPlan + dropDetours + crumbsFirst for one dungeon page. -> { take: [{ id, giver, pre: [ids] }], carried: [ids], crumbs }
export function pickupPlan({ page, minLevel, quests, T, side, player, from, entrance }) {
	const R = makeRules({ quests, T, side, player });
	const reach = Math.max(player.level || 1, minLevel || 1);
	const onPage = new Set(page);
	const plan = { take: [], carried: [], closedBy: {} };
	for (const id of page) {
		const q = R.Qr(id);
		if (!q || !R.applies(q)) continue;
		if (R.done(id)) continue;
		if (R.inLog(id)) {
			plan.carried.push(id);
			continue;
		}
		if (q.money_only) continue;
		if ((q.min || 1) > reach) continue;
		const g = R.giver(q, from);
		if (!g) continue;
		let ok = true;
		const pre = [];
		for (const pid of R.chain(id)) {
			const r = R.Qr(pid);
			if (!r) {
				ok = false;
				break;
			}
			if ((onPage.has(pid) && !R.errand(r, from)) || !R.applies(r)) ok = false;
			if ((r.min || 1) > reach) ok = false;
			if (!R.inLog(pid) && !R.startable(r, from)) ok = false;
			if (!R.inLog(pid) && r.inside_only) ok = false;
			pre.push(pid);
		}
		if (!ok) continue;
		plan.take.push({ id, giver: g, pre: withBreadcrumbs(R, q, pre, onPage, from, plan.closedBy) });
	}
	const onTheWay = new Set(plan.take.flatMap((t) => t.pre));
	plan.take = plan.take.filter((t) => !onTheWay.has(t.id));
	plan.carried = plan.carried.filter((id) => !onTheWay.has(id));
	return crumbsFirst(R, dropDetours(R, plan, from, entrance, T));
}

// withBreadcrumbs: the open breadcrumbs of a quest's chain (forever-db's
// breadcrumb prerequisites of it and of its prerequisites), each an errand
// turned in where one of the chain is given, spliced in before that one.
function withBreadcrumbs(R, q, pre, onPage, from, closedBy) {
	if (!pre.length) return pre;
	const named = new Set([q.id, ...pre]);
	for (const id of [q.id, ...pre]) for (const k of ['pre', 'any']) for (const p of (R.Qr(id) || {})[k] || []) named.add(p);
	const before = new Map();
	for (const id of [q.id, ...pre]) {
		for (const b of (R.Qr(id) || {}).crumbs || []) {
			const r = R.Qr(b);
			if (!r || named.has(b) || onPage.has(b) || R.done(b) || R.inLog(b)) continue;
			if (!R.applies(r) || !R.preDone(r) || !R.errand(r, from)) continue;
			for (const pid of pre) {
				if (!R.inLog(pid) && R.startsAt(R.Qr(pid), r)) {
					(before.get(pid) || before.set(pid, []).get(pid)).push(b);
					closedBy[b] = [pid];
					named.add(b);
					break;
				}
			}
		}
	}
	if (!before.size) return pre;
	return pre.flatMap((pid) => [...(before.get(pid) || []), pid]);
}

const continentOf = (p) => (p ? p.c : null);

function dropDetours(R, plan, from, entrance, T) {
	const at = new Map();
	const ids = [];
	const add = (id, pt) => {
		if (at.has(id)) return;
		at.set(id, pt);
		ids.push(id);
	};
	for (const t of plan.take) {
		add(t.id, t.giver);
		for (const q of t.pre) add(q, R.giver(R.Qr(q), from));
	}
	ids.sort((a, b) => a - b);
	const named = new Set();
	for (const id of ids) for (const k of ['pre', 'any']) for (const p of (R.Qr(id) || {})[k] || []) named.add(p);
	const crumbs = new Map();
	for (const id of ids) {
		const r = R.Qr(id);
		if (named.has(id) || R.inLog(id) || (r.obj || 0) > 0 || !at.get(id) || R.startsAt(r, r)) continue;
		for (const tid of ids) {
			if (tid !== id && at.get(tid) && R.startsAt(R.Qr(tid), r)) (crumbs.get(id) || crumbs.set(id, []).get(id)).push(tid);
		}
	}
	plan.crumbs = [];
	if (!crumbs.size) return plan;
	const stops = [];
	const stop = (p) => p && stops.push(p);
	stop(from);
	stop(entrance);
	for (const id of ids) {
		if (crumbs.has(id)) continue;
		stop(at.get(id));
		stop(R.ender(R.Qr(id), at.get(id) || from));
	}
	const afterOwn = (r) => {
		const list = [];
		for (const k of ['pre', 'any']) {
			for (const pid of r[k] || []) {
				if (!at.has(pid)) continue;
				const e = R.ender(R.Qr(pid), at.get(pid) || from);
				if (e) list.push(e);
			}
		}
		if (list.length && entrance) list.push(entrance);
		return list.length ? list : null;
	};
	const drop = new Set();
	for (const id of ids) {
		const targets = crumbs.get(id);
		if (!targets) continue;
		const g = at.get(id);
		const c = continentOf(g);
		let sea = c != null;
		for (const tid of targets) {
			const tc = continentOf(at.get(tid));
			if (tc == null || tc === c) sea = false;
		}
		const near = stops.some((a) => continentOf(a) === c);
		let best = null;
		for (const a of afterOwn(R.Qr(id)) || stops) {
			for (const tid of targets) {
				const x = T(a, g);
				const y = T(g, at.get(tid));
				const z = T(a, at.get(tid));
				if (x != null && y != null && z != null && (best == null || x + y - z < best)) best = x + y - z;
			}
		}
		const far = c != null && !near;
		const dropped = far || (best != null && (best > RULES.detour_s || (sea && best > RULES.near_s)));
		if (dropped) drop.add(id);
		plan.crumbs.push({ id, targets, detour: best == null ? null : Math.round(best), far, sea, taken: !dropped });
	}
	if (!drop.size) return plan;
	plan.take = plan.take.filter((t) => !drop.has(t.id)).map((t) => ({ ...t, pre: t.pre.filter((q) => !drop.has(q)) }));
	return plan;
}

function crumbsFirst(R, plan) {
	const crumb = (r) => r && (r.obj || 0) === 0 && !R.startsAt(r, r);
	const onWay = (id, r) => {
		for (const t of plan.take) {
			if (t.id !== id && !t.crumbOf && R.startsAt(R.Qr(t.id), r)) {
				if (!t.pre.includes(id)) t.pre.push(id);
				plan.closedBy[id] = [t.id];
				return true;
			}
		}
		return false;
	};
	const take = [];
	for (const t of plan.take) {
		if (!t.pre.length && crumb(R.Qr(t.id)) && onWay(t.id, R.Qr(t.id))) t.crumbOf = true;
		else take.push(t);
	}
	plan.take = take;
	plan.carried = plan.carried.filter((id) => !(crumb(R.Qr(id)) && onWay(id, R.Qr(id))));
	return plan;
}

// stopsOf: each open prerequisite once (its giver to its turn-in), each giver
// once per set of prerequisites its quests wait on. A giver whose quests are
// all in the log is no trip (it stands where the player does).
export function pickupStops(R, plan, from) {
	const stops = [];
	const byKey = new Map();
	const add = (s) => {
		stops.push(s);
		byKey.set(s.key, stops.length - 1);
		return s;
	};
	const depend = (s, i) => {
		if (i == null || byKey.get(s.key) === i || s.deps.includes(i)) return;
		s.deps.push(i);
	};
	for (const t of plan.take) {
		let prev = null;
		const deps = [];
		for (const id of t.pre) {
			const k = `q${id}`;
			const r = R.Qr(id);
			if (!byKey.has(k)) {
				const start = R.giver(r, from);
				const finish = R.ender(r, start || from);
				add({ key: k, quest: id, prereq: true, inP: start, outP: finish || start, deps: [], obj_s: r.obj_s || 0 });
			}
			const s = stops[byKey.get(k)];
			for (const p of [...(r.pre || []), ...(r.any || [])]) depend(s, byKey.get(`q${p}`));
			if (prev != null && plan.closedBy[stops[prev].quest]) depend(s, prev);
			prev = byKey.get(k);
			deps.push(k);
		}
		deps.sort();
		const gk = `g${t.giver.key}`;
		const k = deps.length ? `${gk}<${deps.join(',')}` : gk;
		let s = byKey.has(k) ? stops[byKey.get(k)] : null;
		if (!s) s = add({ key: k, inP: t.giver, outP: t.giver, deps: deps.map((d) => byKey.get(d)), accepts: [] });
		if (!s.accepts.includes(t.id)) s.accepts.push(t.id);
	}
	return stops;
}

// HandinPlan, without the follow-ups: every quest the trip ends with in the
// log (taken, carried, or started by an item inside), turned in outside: one
// stop per place.
export function handinStops(R, quests, entrance) {
	const stops = [];
	const at = new Map();
	for (const id of quests) {
		const e = R.ender(R.Qr(id), entrance);
		if (!e) continue;
		const k = `${e.key}@${e.c},${e.wx},${e.wy}`;
		if (!at.has(k)) at.set(k, stops.push({ key: `t${e.key}`, inP: e, outP: e, deps: [], hands: [] }) - 1);
		stops[at.get(k)].hands.push(id);
	}
	return stops;
}

// The quests the run itself starts (an item found inside, a giver inside):
// handed in after it when their ender is outside.
export function runQuests(R, page, minLevel, player) {
	const reach = Math.max(player.level || 1, minLevel || 1);
	return page.filter((id) => {
		const q = R.Qr(id);
		return q && R.applies(q) && !R.done(id) && !R.inLog(id) && (q.item || (q.gi && ((q.gi_sides ?? NEUTRAL) & R.bit))) && (q.min || 1) <= reach && !q.money_only;
	});
}

// The trip's parts in seconds: pickup (from `from` through the stops and
// each prerequisite's own work), travel to the entrance and back to the
// first hand-in, the hand-ins. talk: seconds an accept or turn-in takes.
export function tripSeconds({ R, plan, from, entrance, T, page, minLevel, player, talk }) {
	const pstops = pickupStops(R, plan, from);
	const pick = planStops(pstops, from, entrance, T);
	let pickup = 0;
	let pos = from;
	for (const i of pick.order) {
		const s = pstops[i];
		pickup += (T(pos, s.inP) ?? 0) + (s.prereq ? (T(s.inP, s.outP) ?? 0) + RULES.step_s + s.obj_s : talk * s.accepts.length);
		pos = s.outP;
	}
	const to = pick.order.length ? T(pos, entrance) ?? 0 : T(from, entrance) ?? 0;
	const handing = [...plan.take.map((t) => t.id), ...plan.carried, ...runQuests(R, page, minLevel, player)];
	const hstops = handinStops(R, handing, entrance);
	const hand = planStops(hstops, entrance, null, T);
	let back = 0;
	let handin = 0;
	pos = entrance;
	hand.order.forEach((i, k) => {
		const s = hstops[i];
		const v = T(pos, s.inP) ?? 0;
		if (k === 0) back = v;
		else handin += v;
		handin += talk * s.hands.length;
		pos = s.outP;
	});
	return {
		pickup_s: pickup, travel_to_s: to, travel_back_s: back, handin_s: handin,
		pickup: pick.order.map((i) => pstops[i].key), handin: hand.order.map((i) => hstops[i].key),
		quests: [...new Set(handing)], prereqs: [...new Set(plan.take.flatMap((t) => t.pre))],
	};
}

// --- the export ----------------------------------------------------------------------

export function buildTrips({ ps, travel, params, frames, outDir }) {
	const db = path.join(ps, 'forever-db');
	const uiMap = jsonl(path.join(db, 'data', 'ui_map.jsonl'));
	const continents = new Set(uiMap.map((m) => m.map));
	const data = questData(db, continents);
	const contacts = JSON.parse(fs.readFileSync(path.join(outDir, 'dungeons', 'quests.json'), 'utf8')).contact_sides;
	const fr = JSON.parse(fs.readFileSync(path.join(outDir, 'forever-reference.json'), 'utf8'));
	// instance map -> { area, areas (the instance's and the same-named area
	// the client zones its quests in: Gnomeregan 721/133, Razorfen Kraul
	// 491/1717, Uldaman 1337/1517, Zul'Farrak 1176/978; wowstats/forever.py
	// dungeon_area), name, min }
	// The instance's own area is the map's (fr.maps[map].area): the areas list
	// every room of a dungeon too (Dire Maul's "The Maul UNUSED"). Its twins
	// carry the own area's name, any area on the dungeon's map is the
	// dungeon's, and a world zone's name on that map (the Deadmines map's
	// Westfall) is not a twin.
	const byName = new Map();
	for (const [aid, a] of Object.entries(fr.areas || {})) (byName.get(a.name) || byName.set(a.name, []).get(a.name)).push(Number(aid));
	const areaOf = new Map();
	for (const [mid, m] of Object.entries(fr.maps || {})) {
		const a = m.area ? fr.areas[String(m.area)] : null;
		if (a && a.instance === 'dungeon' && Array.isArray(a.levels)) {
			const areas = new Set(byName.get(a.name));
			for (const [aid, b] of Object.entries(fr.areas)) if (b.instance === 'dungeon' && b.map === Number(mid)) areas.add(Number(aid));
			areaOf.set(Number(mid), { area: m.area, areas, name: a.name, min: a.levels[0] });
		}
	}
	const codex = (f) => JSON.parse(fs.readFileSync(path.join(ps, 'data', 'codex', f), 'utf8'));
	const wingsFile = path.join(outDir, 'dungeons', 'wings.json');
	const wingsOf = fs.existsSync(wingsFile) ? JSON.parse(fs.readFileSync(wingsFile, 'utf8')).dungeons : {};
	const journal = codex('journal-quests.json').quests || [];
	const insideStarts = new Set(journal.filter((q) => !q.exclude && q.inside?.includes('start')).map((q) => q.quest));
	const toWorld = (map, x, y) => {
		const b = frames[map];
		if (!b) return null;
		return { c: b.c, wx: r1(b.top + (b.bottom - b.top) * (y / 100)), wy: r1(b.left + (b.right - b.left) * (x / 100)) };
	};
	const dungeons = {};
	const wanted = new Set();
	const onPage = new Set();
	for (const e of codex('entrances.json').entrances) {
		const a = areaOf.get(Number(e.instance));
		if (!a) continue;
		const page = new Set();
		for (const q of data.Q.values()) if (a.areas.has(q.area)) page.add(q.id);
		for (const j of journal) {
			if (j.instance !== e.key) continue;
			if (j.exclude) page.delete(j.quest);
			else if (data.Q.has(j.quest)) page.add(j.quest);
		}
		const way = toWorld(e.map, e.x, e.y);
		const wayA = e.A ? toWorld(e.A.map, e.A.x, e.A.y) : null;
		if (!way) continue;
		const ids = [...page].sort((x, y) => {
			const qx = data.Q.get(x);
			const qy = data.Q.get(y);
			return qx.level - qy.level || x - y;
		});
		ids.forEach((id) => { wanted.add(id); onPage.add(id); });
		dungeons[e.instance] = { key: e.key, area: a.area, name: a.name, min: a.min, entrance: [way.c, way.wx, way.wy],
			...(wayA ? { entrance_A: [wayA.c, wayA.wx, wayA.wy] } : {}), page: ids };
		// A dungeon run in wings (data/dungeons/wings.json, tools/dungeon_wings/export.py):
		// each wing's page holds the quests whose objectives are in it, in the page's order;
		// a quest done outside every wing is on none.
		const w = wingsOf[String(e.instance)];
		if (w) {
			dungeons[e.instance].wings = Object.fromEntries(Object.keys(w.wings).map((name) => [name, {
				page: ids.filter((id) => ((w.quests[String(id)] || {}).wings || []).includes(name)),
			}]));
		}
	}
	// The quests the rules may read: the pages, their chains and breadcrumbs.
	const queue = [...wanted];
	while (queue.length) {
		const q = data.Q.get(queue.pop());
		if (!q) continue;
		for (const p of [...q.pre, ...q.any, ...q.crumbs]) {
			if (!wanted.has(p) && data.Q.has(p)) {
				wanted.add(p);
				queue.push(p);
			}
		}
	}
	const quests = {};
	for (const id of [...wanted].sort((a, b) => a - b)) {
		const q = data.Q.get(id);
		const g = points(q.start, data, contacts, contacts.sole.start[id]);
		const e = points(q.end, data, contacts, contacts.sole.end[id]);
		const obj = q.objectives.length;
		const out = { level: q.level, min: q.min, xp: q.xp };
		if (q.side === 1 || q.side === 2) out.side = q.side;
		if (q.races) out.races = q.races;
		if (q.classes) out.classes = q.classes;
		if (q.pre.length) out.pre = [...q.pre].sort((a, b) => a - b);
		if (q.any.length) out.any = [...q.any].sort((a, b) => a - b);
		if (q.crumbs.length) out.crumbs = [...q.crumbs].sort((a, b) => a - b);
		if (obj) {
			out.obj = obj;
			out.obj_s = Math.round(q.objectives.reduce((s, o) => s + objectiveSeconds(o, data.chance), 0));
		}
		if (q.start.item.length) out.item = 1;
		if (g.pts.length) out.g = g.pts;
		// A dungeon page's quest whose giver forever-db places nowhere (A Fine
		// Mess, Kernobee in Gnomeregan): the run's, given inside like those
		// whose giver spawns only in the instance.
		else {
			// A recorded pickup inside (e.g. Alcaz Prison) can place a quest
			// without identifying which NPC variant the character spoke to.
			const recorded = insideStarts.has(id) && q.start.npc.length;
			const sides = recorded ? NEUTRAL : (g.inside || (onPage.has(id) && !q.start.item.length && g.unplaced));
			if (sides) out.gi = 1;
			if (sides && sides !== NEUTRAL) out.gi_sides = sides;
		}
		if (e.pts.length) out.e = e.pts;
		else if (e.inside) out.ei = 1;
		if (insideOnly(q, data)) out.inside_only = 1;
		// moneyOnly: money and nothing else (no XP, no item).
		if (q.money > 0 && !q.xp && !q.rewards) out.money_only = 1;
		quests[id] = out;
	}
	// The tours each side gets with nothing done, standing at the entrance:
	// the reference the runtime port (wowstats/plan/tripcost.py) must replay.
	const graph = {
		// A TravelData row without its id (a Lua nil first) reads shifted: no leg names it, so it is left out.
		taxi: (travel.taxi.nodes || []).filter((n) => Number.isInteger(n.id) && Number.isInteger(n.faction))
			.map((n) => [n.id, n.c, r1(n.wx), r1(n.wy), n.faction]).sort((a, b) => a[0] - b[0]),
		legs: (travel.taxi.legs || []).map((l) => [l.from, l.to, Number(l.yards) || 0]),
		transports: (travel.transports || []).filter((t) => t.a && t.b).map((t) => {
			const end = (e) => [e.c, r1(e.board ? e.board.wx : e.wx), r1(e.board ? e.board.wy : e.wy)];
			return [t.id, t.faction, end(t.a), end(t.b), Number(t.waitA) || 0, Number(t.ab) || 0, Number(t.waitB) || 0, Number(t.ba) || 0];
		}),
	};
	const nets = { A: network(graph, params, 'A'), H: network(graph, params, 'H') };
	const talk = params.talkSec;
	for (const d of Object.values(dungeons)) {
		d.tours = {};
		for (const side of ['A', 'H']) {
			const T = (a, b) => travelCost(nets[side], params, a, b);
			const ent = side === 'A' && d.entrance_A ? d.entrance_A : d.entrance;
			const at = { c: ent[0], wx: ent[1], wy: ent[2], key: 'entrance' };
			const player = { level: d.min, done: new Set(), log: new Set() };
			const R = makeRules({ quests, T, side, player });
			const tour = (page) => {
				const plan = pickupPlan({ page, minLevel: d.min, quests, T, side, player, from: at, entrance: at });
				const s = tripSeconds({ R, plan, from: at, entrance: at, T, page, minLevel: d.min, player, talk });
				if (!s.quests.length) return null;
				return {
					pickup: s.pickup, handin: s.handin, quests: s.quests.sort((a, b) => a - b),
					pickup_s: Math.round(s.pickup_s), travel_to_s: Math.round(s.travel_to_s),
					travel_back_s: Math.round(s.travel_back_s), handin_s: Math.round(s.handin_s),
					breadcrumbs: plan.crumbs.map((c) => ({ id: c.id, detour_s: c.detour, far: c.far, taken: c.taken })),
				};
			};
			const whole = tour(d.page);
			if (whole) d.tours[side] = whole;
			for (const w of Object.values(d.wings || {})) {
				w.tours = w.tours || {};
				const t = tour(w.page);
				if (t) w.tours[side] = t;
			}
		}
	}
	const frameOut = {};
	for (const m of uiMap.sort((a, b) => a.ui_map - b.ui_map)) frameOut[m.ui_map] = [m.map, m.left, m.right, m.top, m.bottom];
	return {
		schema: 2, // Quest XP is null when the source omits it; earlier exports wrote zero.
		about: 'A dungeon trip\'s quests and travel, from Polestar\'s pick-up and hand-in rules (addon/Polestar/Dungeons/Pickup.lua, Handin.lua) ported to tools/route_reference/trips.mjs: per dungeon (instance Map id) its page of quests and its entrance in world yards [continent, x north, y west]; per quest what the rules read (g: givers, e: enders, each [c, x, y, side bits, npc/object key]; gi/ei: given/taken inside only (gi_sides: eligible sides when restricted); obj_s: objectives\' seconds); the travel network (flight masters, legs, boats); the uiMap frames [continent, left, right, top, bottom]; and per side the tours with nothing done, standing at the entrance, that wowstats/plan/tripcost.py must replay; a dungeon run in wings has the same page and tours per wing (wings).',
		rules: { ...RULES, run_speed: params.runSpeed, detour: { 0: params.detour0, 1: params.detour1 }, flight_speed: params.flightSpeed,
			flight_stop: FLIGHT_STOP, flight_overhead: params.flightOverhead, transport_wait: params.transportWait ?? 1, talk_s: talk },
		frames: frameOut,
		graph,
		dungeons,
		quests,
	};
}
