/* Character actions for the interactive viewer. AnimationData IDs come from
   the client (TrinityCore SharedDefines.h, enum Anim). Spell names follow
   data/class-reference.json. Spell effects below are drawn by this viewer. */
'use strict';

(() => {
	const M3D = globalThis.M3D;
	const READY = { 16: 25, 17: 26, 18: 27, 19: 28 };
	const DIRECTED = [[51, 1], [53, 1]], OMNI = [[52, 1], [54, 1]];
	const RANGED = {
		2:  { label: 'Shoot bow', steps: [[29, 1, true], [46, 1]] },
		3:  { label: 'Fire gun', steps: [[48, 1, true], [49, 1]] },
		16: { label: 'Throw weapon', steps: [[108, 1, true], [107, 1]] },
		18: { label: 'Fire crossbow', steps: [[48, 1, true], [49, 1]] },
		// The model has no dedicated wand clip. Use its directed release
		// while holding the actual wand, and draw a spark at the hand.
		19: { label: 'Shoot wand', steps: [[53, 1]], effect: 'wand' }
	};
	const CLASS = {
		1: { name: 'battle-shout', label: 'Battle Shout', steps: [[55, 1]], effect: 'shout', equipment: 'melee' },
		2: { name: 'holy-light', label: 'Holy Light', steps: OMNI, effect: 'light' },
		3: { name: 'hunters-mark', label: "Hunter's Mark", minLevel: 6, steps: [[53, 1]], effect: 'mark' },
		4: { name: 'kick', label: 'Kick', minLevel: 12, steps: [[95, 1]], equipment: 'melee' },
		5: { name: 'power-word-shield', label: 'Power Word: Shield', minLevel: 6, steps: [[54, 1]], effect: 'shield' },
		7: { name: 'lightning-bolt', label: 'Lightning Bolt', steps: DIRECTED, effect: 'lightning' },
		8: { name: 'frostbolt', label: 'Frostbolt', minLevel: 4, steps: DIRECTED, effect: 'frost' },
		9: { name: 'drain-life', label: 'Drain Life', minLevel: 14, steps: [[124, 2]], effect: 'drain' },
		11: { name: 'rejuvenation', label: 'Rejuvenation', minLevel: 4, steps: OMNI, effect: 'leaves' }
	};
	const GENERIC_CASTS = [
		{ name: 'cast', steps: [[51, 2], [53, 1]] }, { name: 'cast-omni', steps: [[52, 2], [54, 1]] },
		{ name: 'channel', steps: [[124, 4]] }, { name: 'channel-omni', steps: [[125, 4]] }
	];
	const EMOTES = [
		{ name: 'roar', steps: [[55, 1]] }, { name: 'talk', steps: [[60, 1]] },
		{ name: 'exclaim', steps: [[64, 1]] }, { name: 'question', steps: [[65, 1]] },
		{ name: 'bow', steps: [[66, 1]] }, { name: 'cheer', steps: [[68, 1]] }, { name: 'laugh', steps: [[70, 1]] }
	];

	M3D.moves = function moves(attack, classId, rangedKind, level = 1) {
		const swing = READY[attack] ? attack : 16;
		const moves = [{ name: 'attack', label: swing === 16 ? 'Unarmed attack' : 'Melee attack', equipment: 'melee',
			steps: [[READY[swing], 1, true], [swing, 1], [swing, 1], [READY[swing], 1, true]] }];
		const ranged = RANGED[rangedKind];
		if (ranged)
			moves.push({ ...ranged, name: 'ranged', equipment: 'ranged' });
		if (CLASS[classId]) {
			const ability = CLASS[classId];
			if (level >= (ability.minLevel || 1)) moves.push({ equipment: 'empty', ...ability });
			if (classId === 3 && level >= 6 && [2, 3, 18].includes(rangedKind))
				moves.push({ name: 'arcane-shot', label: 'Arcane Shot', equipment: 'ranged', steps: ranged.steps, effect: 'arcane' });
		} else {
			moves.push(...GENERIC_CASTS);
		}
		return moves.concat(EMOTES);
	};

	// Every clip the shipper may request. No other character sequences
	// need to leave the player's install.
	// On a ground mount the rider uses Mount (91). The creature plays
	// its own Stand, Walk or Run and carries the rider with its saddle.
	M3D.RIDER_ANIMATION = 91;
	M3D.GAITS = Object.freeze({ idle: 0, walk: 4, run: 5 });
	const ids = new Set([15, M3D.RIDER_ANIMATION, ...Object.values(M3D.GAITS)]);
	for (const moves of [
		...Object.keys(READY).map((attack) => M3D.moves(Number(attack), 0, 0)),
		...Object.keys(CLASS).map((id) => M3D.moves(16, Number(id), 0, 60)),
		...Object.keys(RANGED).map((kind) => M3D.moves(16, 3, Number(kind), 60))
	]) {
		for (const move of moves)
			for (const [id] of move.steps) ids.add(id);
	}
	M3D.ANIMATIONS = Object.freeze([...ids].sort((x, y) => x - y));

	// Original, screen-space effects anchored to the model. A turn of the
	// camera moves the hand, chest and target together with the character.
	M3D.drawAbilityEffect = function drawAbilityEffect(ctx, viewer, playing) {
		const canvas = ctx.canvas, w = viewer.canvas.clientWidth, h = viewer.canvas.clientHeight;
		if (canvas.width !== w || canvas.height !== h) {
			canvas.width = w;
			canvas.height = h;
		} else {
			ctx.clearRect(0, 0, w, h);
		}
		if (!playing?.move.effect || playing.at !== playing.move.steps.length - 1 || !Number.isFinite(playing.left))
			return false;
		const box = viewer.box;
		const height = box.max[1] - box.min[1], z = box.min[1] + height * 0.57;
		const center = viewer.project([0, 0, z]), top = viewer.project([0, 0, box.max[1]]);
		const target = viewer.project([height * 0.74, 0, z + height * 0.1]);
		if (!center || !top || !target) return false;
		const size = Math.max(12, Math.abs(center[1] - top[1]));
		const hand = viewer.scene.char.getAttachmentTransform(1);
		const source = hand ? viewer.camera.project(hand.subarray(12, 15), w, h) : null;
		const [sx, sy] = source || center, [tx, ty] = target;
		const t = Math.min(1, Math.max(0, 1 - playing.left / playing.duration));
		const effect = playing.move.effect;
		const colors = { shout: '#ffc16c', light: '#ffe99b', mark: '#ff6b56', shield: '#ffe9aa',
			lightning: '#a8d7ff', frost: '#95edff', drain: '#70eb95', leaves: '#a4ef82', arcane: '#dc9aff', wand: '#d1eafa' };
		const line = (points) => {
			ctx.beginPath();
			points.forEach(([x,y], i) => { if (i) ctx.lineTo(x,y); else ctx.moveTo(x,y); });
			ctx.stroke();
		};
		const oval = (x, y, rx, ry) => {
			ctx.beginPath();
			ctx.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2);
			ctx.stroke();
		};
		ctx.save();
		ctx.globalAlpha = Math.min(1, t * 12, (1 - t) * 7);
		ctx.strokeStyle = ctx.fillStyle = ctx.shadowColor = colors[effect];
		ctx.shadowBlur = Math.max(3, size * 0.08);
		ctx.lineWidth = Math.max(2, size * 0.025);
		ctx.lineCap = ctx.lineJoin = 'round';
		if (effect === 'shield') {
			oval(...center, size * 0.75, size * 1.24);
			ctx.globalAlpha *= 0.15;
			ctx.fill();
		} else if (effect === 'shout' || effect === 'light') {
			for (let i = 0; i < 3; i++) {
				const phase = (t + i / 3) % 1;
				const radius = size * (0.23 + phase * (effect === 'shout' ? 1.15 : 0.29));
				const y = center[1] + size * (effect === 'shout' ? 0.14 : 1 - phase * 1.9);
				oval(center[0], y, radius, radius * 0.3);
			}
		} else if (effect === 'leaves') {
			for (let i = 0; i < 9; i++) {
				const phase = (t + i / 9) % 1, angle = i * 2.4 + t * 3;
				const x = center[0] + Math.cos(angle) * size * 0.6, y = center[1] + (1 - 2 * phase) * size;
				ctx.beginPath();
				ctx.ellipse(x, y, size * 0.065, size * 0.025, angle, 0, Math.PI * 2);
				ctx.fill();
			}
		} else if (effect === 'mark') {
			const s = size * 0.16, y = ty - size * (0.6 + 0.13 * (1 - t));
			line([[tx, y - s * 2], [tx, y]]);
			line([[tx - s, y - s], [tx, y], [tx + s, y - s]]);
		} else if (effect === 'lightning' || effect === 'drain') {
			const points = [];
			for (let i = 0; i <= 9; i++) {
				const f = i / 9, pulse = Math.sin(Math.PI * f);
				const wave = Math.sin((effect === 'drain' ? i * 0.85 + 20 * t : i * 13 + Math.floor(t * 9)) + 1);
				points.push([sx + (tx - sx) * f + wave * pulse * size * 0.13, sy + (ty - sy) * f]);
			}
			line(points);
			if (effect === 'drain') {
				const [x,y] = points[8 - Math.floor((t * 3 % 1) * 8)];
				oval(x,y, size * 0.043, size * 0.043);
			}
		} else {
			const x = sx + (tx - sx) * t, y = sy + (ty - sy) * t;
			const trail = Math.max(0, t - 0.24), r = size * (effect === 'wand' ? 0.04 : 0.07);
			line([[sx + (tx - sx) * trail, sy + (ty - sy) * trail], [x,y]]);
			if (effect === 'frost')
				line([[x, y-r*1.6], [x+r, y], [x, y+r*1.6], [x-r, y], [x, y-r*1.6]]);
			else
				oval(x,y,r,r);
		}
		ctx.restore();
		return true;
	};
})();
