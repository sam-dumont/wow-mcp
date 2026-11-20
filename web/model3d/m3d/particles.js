/*!
	Particle and ribbon emitters for wow-stats' 3D view: item glows,
	enchant glows. wow.export draws none, so this is not a port: written for
	wow-stats from the M2 page of wowdev.wiki (M2Particle, M2Ribbon, the
	particle flags and blend table). Part of wow-stats, under its licence.

	M3D.ParticleSystem(renderer, seed): the emitters of one M2RendererGL.
	update(dt) after the model's bones, render(view, proj) after every mesh
	of the frame (depth test on, depth write off). Every random draw comes
	from a seeded generator, and time only from the caller's dt, so one
	clock gives one picture.
 */
'use strict';

(() => {
	const M3D = globalThis.M3D = globalThis.M3D || {};
	const { IDENTITY_MAT4, find_keyframe, mat4_multiply, mat4_from_translation } = M3D.math;

	// Caps, so a bad or huge file can't hang a phone.
	const MAX_PARTICLES = 384;   // live, per emitter
	const MAX_RATE = 600;        // spawned a second, per emitter
	const MAX_EDGES = 256;       // per ribbon
	const STEP = 1 / 30;         // simulation step, seconds
	const MAX_CATCH_UP = 2;      // seconds simulated after a long pause

	// Particle blend (wowdev.wiki's table, by its GL functions) -> shader mode.
	// 0 opaque, 1 SRC_COLOR+ONE, 2 SRC_ALPHA+ONE_MINUS_SRC_ALPHA, 3 alpha
	// test, 4 SRC_ALPHA+ONE; 5 (ours) ONE+ONE. Unknown values: additive.
	const particle_mode = (b) => (b >= 0 && b <= 4 ? b : 4);
	// M2 material blend (ribbons) -> the same modes.
	const M2_BLEND_MODE = [0, 3, 2, 5, 4, 2, 2, 4];

	// ------------------------------------------------------------------ helpers

	// mulberry32: small, fast, seedable.
	function rng(seed) {
		let a = seed >>> 0;
		return () => {
			a = (a + 0x6D2B79F5) >>> 0;
			let t = a;
			t = Math.imul(t ^ (t >>> 15), t | 1);
			t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
			return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
		};
	}

	function mix_seed(a, b) {
		let h = (Math.imul((a >>> 0) ^ 0x9E3779B9, 0x85EBCA6B) + b) >>> 0;
		h = Math.imul(h ^ (h >>> 13), 0xC2B2AE35) >>> 0;
		return (h ^ (h >>> 16)) >>> 0;
	}

	function mix(a, b, f) {
		if (typeof a === 'number')
			return a + (b - a) * f;
		const out = new Array(a.length);
		for (let i = 0; i < a.length; i++)
			out[i] = a[i] + (b[i] - a[i]) * f;
		return out;
	}

	// An M2Track at the model's time: its sequence's keys, or its global
	// sequence's.
	function sample(track, clock, def) {
		if (!track || !track.timestamps || track.timestamps.length === 0)
			return def;
		let idx = clock.anim, t = clock.ms;
		const gs = track.globalSeq;
		if (gs !== 0xFFFF && gs !== undefined) {
			idx = 0;
			t = clock.global[gs] ?? 0;
		}
		if (idx >= track.timestamps.length)
			idx = 0;
		const times = track.timestamps[idx], values = track.values[idx];
		if (!times || !times.length || !values || !values.length)
			return def;
		const last = Math.min(times.length, values.length) - 1;
		if (last === 0 || t <= times[0])
			return values[0];
		if (t >= times[last])
			return values[last];
		const k = find_keyframe(times, t);
		if (track.interpolation === 0)
			return values[k];
		const span = times[k + 1] - times[k];
		return span > 0 ? mix(values[k], values[k + 1], (t - times[k]) / span) : values[k + 1];
	}

	// A "fake animation block" at a fraction of a particle's life.
	function over_life(block, f, def) {
		const times = block && block.times;
		if (!times || times.length === 0)
			return def;
		const values = block.values;
		if (f <= times[0])
			return values[0];
		for (let i = 1; i < times.length; i++) {
			if (f <= times[i]) {
				const span = times[i] - times[i - 1];
				return span > 0 ? mix(values[i - 1], values[i], (f - times[i - 1]) / span) : values[i];
			}
		}
		return values[values.length - 1];
	}

	// WoW (x forward, y left, z up) -> WebGL (x, y up, z = -wow y)
	const w2g = (v) => [v[0], v[2], -v[1]];

	function xform_point(m, p) {
		return [
			m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12],
			m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13],
			m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14]
		];
	}

	function xform_dir(m, v) {
		return [
			m[0] * v[0] + m[4] * v[1] + m[8] * v[2],
			m[1] * v[0] + m[5] * v[1] + m[9] * v[2],
			m[2] * v[0] + m[6] * v[1] + m[10] * v[2]
		];
	}

	// ------------------------------------------------------------------ GL

	const VERT = `#version 300 es
in vec3 a_pos;
in vec2 a_uv;
in vec4 a_color;
uniform mat4 u_view;
uniform mat4 u_proj;
out vec2 v_uv;
out vec4 v_color;
void main() {
	v_uv = a_uv;
	v_color = a_color;
	gl_Position = u_proj * u_view * vec4(a_pos, 1.0);
}
`;

	// Additive modes write max(rgb) as alpha, so a glow over the
	// transparent background stays a valid premultiplied pixel.
	const FRAG = `#version 300 es
precision highp float;
in vec2 v_uv;
in vec4 v_color;
uniform sampler2D u_tex;
uniform int u_mode;
out vec4 o_color;
void main() {
	vec4 c = texture(u_tex, v_uv) * v_color;
	if (u_mode == 0) {
		o_color = vec4(c.rgb, 1.0);
		return;
	}
	if (u_mode == 3) {
		if (c.a < 0.501960814)
			discard;
		o_color = vec4(c.rgb, 1.0);
		return;
	}
	vec3 rgb;
	if (u_mode == 1)
		rgb = c.rgb * c.rgb;
	else if (u_mode == 5)
		rgb = c.rgb;
	else
		rgb = c.rgb * c.a;
	float a = u_mode == 2 ? c.a : max(rgb.r, max(rgb.g, rgb.b));
	o_color = vec4(rgb, a);
}
`;

	const FLOATS = 9; // x y z u v r g b a

	function program(ctx) {
		if (ctx._particle_program)
			return ctx._particle_program;
		const gl = ctx.gl;
		const compile = (type, src) => {
			const s = gl.createShader(type);
			gl.shaderSource(s, src);
			gl.compileShader(s);
			if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
				const info = gl.getShaderInfoLog(s);
				gl.deleteShader(s);
				throw new Error('particle shader: ' + info);
			}
			return s;
		};
		const prog = gl.createProgram();
		const vs = compile(gl.VERTEX_SHADER, VERT), fs = compile(gl.FRAGMENT_SHADER, FRAG);
		gl.attachShader(prog, vs);
		gl.attachShader(prog, fs);
		gl.linkProgram(prog);
		gl.deleteShader(vs);
		gl.deleteShader(fs);
		if (!gl.getProgramParameter(prog, gl.LINK_STATUS))
			throw new Error('particle shader: ' + gl.getProgramInfoLog(prog));
		ctx._particle_program = {
			prog,
			a_pos: gl.getAttribLocation(prog, 'a_pos'),
			a_uv: gl.getAttribLocation(prog, 'a_uv'),
			a_color: gl.getAttribLocation(prog, 'a_color'),
			u_view: gl.getUniformLocation(prog, 'u_view'),
			u_proj: gl.getUniformLocation(prog, 'u_proj'),
			u_tex: gl.getUniformLocation(prog, 'u_tex'),
			u_mode: gl.getUniformLocation(prog, 'u_mode')
		};
		return ctx._particle_program;
	}

	function set_blend(gl, mode) {
		if (mode === 0 || mode === 3) {
			gl.disable(gl.BLEND);
			return;
		}
		gl.enable(gl.BLEND);
		if (mode === 2) {
			gl.blendEquation(gl.FUNC_ADD);
			gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
		} else {
			gl.blendEquationSeparate(gl.FUNC_ADD, gl.MAX);
			gl.blendFunc(gl.ONE, gl.ONE);
		}
	}

	// uv rectangle of one cell of a rows x columns texture
	function cell_rect(cell, rows, cols) {
		const n = rows * cols;
		if (n <= 1)
			return [0, 0, 1, 1];
		const c = ((Math.floor(cell) % n) + n) % n;
		const col = c % cols, row = Math.floor(c / cols);
		return [col / cols, row / rows, (col + 1) / cols, (row + 1) / rows];
	}

	// ------------------------------------------------------------------ the system

	class ParticleSystem {
		constructor(renderer, seed = 1) {
			this.r = renderer;
			const m2 = renderer.m2;
			this.global = new Float32Array((m2.globalLoops || []).length);
			this.time = 0;
			this.emitters = (m2.particles || []).map((def, i) => ({
				def,
				rand: rng(mix_seed(seed, i + 1)),
				acc: 0,
				parts: [],
				world: (def.flags & 0x10) !== 0,
				mode: particle_mode(def.blend)
			}));
			this.ribbons = (m2.ribbons || []).map((def, i) => {
				const mat = m2.materials && m2.materials[def.materials[0]];
				const blend = mat ? mat.blendingMode : 4;
				return {
					def,
					acc: 0,
					edges: [],
					prev: null,
					rand: rng(mix_seed(seed, 1000 + i)),
					mode: M2_BLEND_MODE[blend] ?? 4
				};
			});
			this.buffer = null;
			this.vao = null;
			this.vbo = null;
		}

		// The tracks' clock `back` seconds before the end of this frame (each
		// simulation step reads its own time): the model's sequence, looped,
		// or our own time when nothing plays; our own global sequences.
		_clock(back = 0) {
			const r = this.r;
			const wrap = (t, len) => (len > 0 ? ((t % len) + len) % len : Math.max(0, t));
			const loops = r.m2.globalLoops || [];
			const global = back > 0 ? Array.from(this.global, (g, i) => wrap(g - back * 1000, loops[i])) : this.global;
			if (r.current_animation !== null && r.current_animation !== undefined) {
				const anim = r._current_anim && r._current_anim();
				const ms = wrap(r.animation_time * 1000 - back * 1000, anim ? anim.duration : 0);
				return { anim: r.current_anim_index ?? 0, ms, global };
			}
			return { anim: 0, ms: Math.max(0, (this.time - back) * 1000), global };
		}

		// model x bone x T(position): the emitter's frame, WoW position converted
		_frame(bone, position) {
			const r = this.r;
			let bone_mat = IDENTITY_MAT4;
			if (r.bone_matrices && r.bones && bone < r.bones.length && bone * 16 + 16 <= r.bone_matrices.length)
				bone_mat = r.bone_matrices.subarray(bone * 16, bone * 16 + 16);
			const p = w2g(position);
			const t = new Float32Array(16), mb = new Float32Array(16), out = new Float32Array(16);
			mat4_from_translation(t, p[0], p[1], p[2]);
			mat4_multiply(mb, r.model_matrix, bone_mat);
			mat4_multiply(out, mb, t);
			return out;
		}

		update(dt) {
			dt = Math.max(0, Math.min(Number(dt) || 0, MAX_CATCH_UP));
			this.time += dt;
			const loops = this.r.m2.globalLoops || [];
			for (let i = 0; i < this.global.length; i++) {
				this.global[i] += dt * 1000;
				if (loops[i] > 0)
					this.global[i] %= loops[i];
			}
			const clock = this._clock();
			for (const e of this.emitters)
				this._update_emitter(e, dt);
			for (const rb of this.ribbons)
				this._update_ribbon(rb, dt, clock);
		}

		_params(e, clock) {
			const d = e.def;
			return {
				enabled: sample(d.enabled, clock, 1),
				rate: Math.min(MAX_RATE, Math.max(0, sample(d.rate, clock, 0) + d.rateVariation * (e.rand() * 2 - 1))),
				speed: sample(d.speed, clock, 0),
				speedVariation: sample(d.speedVariation, clock, 0),
				vr: sample(d.verticalRange, clock, 0),
				hr: sample(d.horizontalRange, clock, 0),
				gravity: w2g(sample(d.gravity, clock, [0, 0, 0])),
				life: sample(d.lifespan, clock, 1),
				width: sample(d.areaWidth, clock, 0),
				length: sample(d.areaLength, clock, 0),
				zSource: sample(d.zSource, clock, 0)
			};
		}

		_update_emitter(e, dt) {
			const d = e.def;
			const frame = this._frame(d.bone, d.position);
			const drag = d.drag > 0 ? d.drag : 0;

			let left = dt;
			while (left > 1e-9) {
				const h = Math.min(STEP, left);
				left -= h;
				// the tracks at the middle of the step
				const prm = this._params(e, this._clock(left + h / 2));

				const parts = e.parts;
				let w = 0;
				for (let i = 0; i < parts.length; i++) {
					const p = parts[i];
					p.age += h;
					if (p.age >= p.life)
						continue;
					p.v[0] += prm.gravity[0] * h;
					p.v[1] += prm.gravity[1] * h;
					p.v[2] += prm.gravity[2] * h;
					if (drag) {
						const k = Math.exp(-drag * h);
						p.v[0] *= k;
						p.v[1] *= k;
						p.v[2] *= k;
					}
					p.p[0] += p.v[0] * h;
					p.p[1] += p.v[1] * h;
					p.p[2] += p.v[2] * h;
					parts[w++] = p;
				}
				parts.length = w;

				if (prm.enabled === 0)
					continue;
				e.acc += prm.rate * h;
				let n = Math.floor(e.acc);
				e.acc -= n;
				n = Math.min(n, MAX_PARTICLES - parts.length);
				if (n <= 0)
					e.acc = Math.min(e.acc, 1);
				for (let i = 0; i < n; i++)
					parts.push(this._spawn(e, prm, frame));
			}
		}

		_spawn(e, prm, frame) {
			const d = e.def, rand = e.rand;
			const u = () => rand() * 2 - 1;
			let pos, dir;
			if (d.emitterType === 2) {
				// sphere: radius between the length (min) and the width (max),
				// elevation up to the vertical range, azimuth up to the horizontal
				const radius = prm.length + (prm.width - prm.length) * rand();
				const el = u() * prm.vr, az = u() * prm.hr;
				const n = [Math.cos(el) * Math.cos(az), Math.cos(el) * Math.sin(az), Math.sin(el)];
				pos = [n[0] * radius, n[1] * radius, n[2] * radius];
				dir = (d.flags & 0x100) ? [0, 0, 1] : n;
			} else {
				// plane (and the spline and bone types, drawn as a plane): the
				// emitter's x/y rectangle, velocity tilted from +z by up to the
				// vertical range, turned by up to the horizontal range
				pos = [u() * prm.width * 0.5, u() * prm.length * 0.5, 0];
				const polar = u() * prm.vr, az = u() * prm.hr;
				dir = [Math.sin(polar) * Math.cos(az), Math.sin(polar) * Math.sin(az), Math.cos(polar)];
			}
			if (prm.zSource > 0) {
				const z = [pos[0], pos[1], pos[2] - prm.zSource];
				const l = Math.hypot(z[0], z[1], z[2]);
				if (l > 1e-6)
					dir = [z[0] / l, z[1] / l, z[2] / l];
			}
			const speed = prm.speed * (1 + u() * prm.speedVariation);
			let p = w2g(pos), v = w2g([dir[0] * speed, dir[1] * speed, dir[2] * speed]);
			if (e.world) {
				p = xform_point(frame, p);
				v = xform_dir(frame, v);
			}
			const sv = d.scaleVariation || [0, 0];
			const sx = Math.max(0, 1 + u() * sv[0]);
			const sy = (d.flags & 0x80000) ? Math.max(0, 1 + u() * sv[1]) : sx;
			let spin = d.spin + u() * d.spinVariation;
			if ((d.flags & 0x200) && rand() < 0.5)
				spin = -spin;
			const cells = Math.max(1, d.rows * d.columns);
			const tw = d.twinkleScale || [0, 0];
			return {
				p, v,
				age: 0,
				life: Math.max(0.01, prm.life + u() * d.lifespanVariation),
				sx, sy,
				angle: d.baseSpin + u() * d.baseSpinVariation,
				spin,
				cell: (d.flags & 0x210000) ? Math.floor(rand() * cells) : 0,
				phase: rand(),
				twinkle: (tw[0] > 0 || tw[1] > 0) ? tw[0] + (tw[1] - tw[0]) * rand() : 1
			};
		}

		_update_ribbon(rb, dt, clock) {
			const d = rb.def;
			const frame = this._frame(d.bone, d.position);
			const center = xform_point(frame, [0, 0, 0]);
			// the ribbon's height runs along the bone's z (WoW up)
			const up = xform_dir(frame, w2g([0, 0, 1]));
			const above = sample(d.above, clock, 0), below = sample(d.below, clock, 0);
			const top = [center[0] + up[0] * above, center[1] + up[1] * above, center[2] + up[2] * above];
			const bot = [center[0] - up[0] * below, center[1] - up[1] * below, center[2] - up[2] * below];
			const visible = sample(d.visibility, clock, 1) !== 0;
			const prev = rb.prev || { top, bot };
			const life = d.edgeLifetime > 0 ? d.edgeLifetime : 0;

			let left = dt, done = 0;
			while (left > 1e-9) {
				const h = Math.min(STEP, left);
				left -= h;
				done += h;
				const f = dt > 0 ? done / dt : 1;
				const edges = rb.edges;
				let w = 0;
				for (let i = 0; i < edges.length; i++) {
					const e = edges[i];
					e.age += h;
					if (e.age >= life)
						continue;
					// gravity sinks (or lifts) the trail along world -y
					e.top[1] -= d.gravity * h;
					e.bot[1] -= d.gravity * h;
					edges[w++] = e;
				}
				edges.length = w;
				if (!visible || life <= 0)
					continue;
				rb.acc += Math.min(MAX_RATE, Math.max(0, d.edgesPerSecond)) * h;
				const n = Math.floor(rb.acc);
				rb.acc -= n;
				// spread over the step: each edge at its own time and place
				for (let j = 1; j <= n; j++) {
					const back = h * (1 - j / n);
					const fj = dt > 0 ? (done - back) / dt : f;
					if (edges.length >= MAX_EDGES)
						edges.shift();
					edges.push({ top: mix(prev.top, top, fj), bot: mix(prev.bot, bot, fj), age: back });
				}
			}
			rb.prev = { top, bot };
			rb.head = visible ? { top, bot } : null;
			rb.color = sample(d.color, clock, [1, 1, 1]);
			rb.alpha = sample(d.alpha, clock, 32767) / 32767;
			rb.slot = sample(d.texSlot, clock, 0);
		}

		// live particles and ribbon edges (tests, debugging)
		stats() {
			let particles = 0, edges = 0;
			for (const e of this.emitters)
				particles += e.parts.length;
			for (const rb of this.ribbons)
				edges += rb.edges.length;
			return { particles, edges };
		}

		_texture(index) {
			const r = this.r;
			return (index !== undefined && r.textures.get(index)) || r.default_texture;
		}

		_ensure_gl(floats) {
			const ctx = this.r.ctx, gl = ctx.gl;
			const p = program(ctx);
			if (!this.vao) {
				this.vao = gl.createVertexArray();
				this.vbo = gl.createBuffer();
				gl.bindVertexArray(this.vao);
				gl.bindBuffer(gl.ARRAY_BUFFER, this.vbo);
				const stride = FLOATS * 4;
				gl.enableVertexAttribArray(p.a_pos);
				gl.vertexAttribPointer(p.a_pos, 3, gl.FLOAT, false, stride, 0);
				gl.enableVertexAttribArray(p.a_uv);
				gl.vertexAttribPointer(p.a_uv, 2, gl.FLOAT, false, stride, 12);
				gl.enableVertexAttribArray(p.a_color);
				gl.vertexAttribPointer(p.a_color, 4, gl.FLOAT, false, stride, 20);
			}
			if (!this.buffer || this.buffer.length < floats)
				this.buffer = new Float32Array(Math.max(floats, 6 * FLOATS * 64));
			return p;
		}

		render(view, proj) {
			const quads = this.emitters.reduce((n, e) => n + e.parts.length, 0)
				+ this.ribbons.reduce((n, rb) => n + rb.edges.length + 1, 0);
			if (quads === 0)
				return;

			const ctx = this.r.ctx, gl = ctx.gl;
			const p = this._ensure_gl(quads * 6 * FLOATS);
			gl.useProgram(p.prog);
			gl.uniformMatrix4fv(p.u_view, false, view);
			gl.uniformMatrix4fv(p.u_proj, false, proj);
			gl.uniform1i(p.u_tex, 0);
			gl.bindVertexArray(this.vao);
			gl.bindBuffer(gl.ARRAY_BUFFER, this.vbo);
			gl.enable(gl.DEPTH_TEST);
			gl.depthMask(false);
			gl.disable(gl.CULL_FACE);
			gl.activeTexture(gl.TEXTURE0);

			const right = [view[0], view[4], view[8]];
			const upv = [view[1], view[5], view[9]];
			const mm = this.r.model_matrix;
			const model_scale = Math.hypot(mm[0], mm[1], mm[2]) || 1;

			for (const e of this.emitters) {
				if (e.parts.length === 0)
					continue;
				const n = this._fill_particles(e, view, right, upv, model_scale);
				this._draw(p, e.mode, this._texture(e.def.flags & 0x10000000 ? e.def.texture & 0x1F : e.def.texture), n);
			}
			for (const rb of this.ribbons) {
				const n = this._fill_ribbon(rb);
				if (n > 0)
					this._draw(p, rb.mode, this._texture(rb.def.textures[0]), n);
			}

			gl.blendEquation(gl.FUNC_ADD);
			ctx.reset_state();
		}

		_draw(p, mode, texture, vertices) {
			const gl = this.r.ctx.gl;
			gl.bindTexture(gl.TEXTURE_2D, texture.texture);
			gl.uniform1i(p.u_mode, mode);
			set_blend(gl, mode);
			gl.bufferData(gl.ARRAY_BUFFER, this.buffer.subarray(0, vertices * FLOATS), gl.DYNAMIC_DRAW);
			gl.drawArrays(gl.TRIANGLES, 0, vertices);
		}

		_fill_particles(e, view, right, up, model_scale) {
			const d = e.def;
			const frame = e.world ? null : this._frame(d.bone, d.position);
			const buf = this.buffer;
			const rows = Math.max(1, d.rows), cols = Math.max(1, d.columns);
			const items = [];
			for (const p of e.parts) {
				const f = p.age / p.life;
				if (d.twinklePercent > 0 && d.twinklePercent < 1 && d.twinkleSpeed > 0
					&& ((p.age * d.twinkleSpeed + p.phase) % 1) >= d.twinklePercent)
					continue;
				const c = frame ? xform_point(frame, p.p) : p.p;
				const depth = view[2] * c[0] + view[6] * c[1] + view[10] * c[2] + view[14];
				items.push([p, f, c, depth]);
			}
			// alpha blending draws back to front; additive needs no order
			if (e.mode === 2)
				items.sort((a, b) => a[3] - b[3]);

			let o = 0;
			for (const [p, f, c] of items) {
				const col = over_life(d.color, f, [255, 255, 255]);
				const alpha = over_life(d.alpha, f, 32767) / 32767;
				const scale = over_life(d.scale, f, [1, 1]);
				const hx = scale[0] * p.sx * p.twinkle * model_scale * 0.5;
				const hy = (scale[1] ?? scale[0]) * p.sy * p.twinkle * model_scale * 0.5;
				const cell = over_life(d.headCell, f, 0) + p.cell;
				const [u0, v0, u1, v1] = cell_rect(cell, rows, cols);
				const angle = p.angle + p.spin * p.age;
				const cs = Math.cos(angle), sn = Math.sin(angle);
				const r = col[0] / 255, g = col[1] / 255, b = col[2] / 255;
				const corner = (sx, sy, u, v) => {
					const ox = sx * hx * cs - sy * hy * sn;
					const oy = sx * hx * sn + sy * hy * cs;
					buf[o++] = c[0] + right[0] * ox + up[0] * oy;
					buf[o++] = c[1] + right[1] * ox + up[1] * oy;
					buf[o++] = c[2] + right[2] * ox + up[2] * oy;
					buf[o++] = u;
					buf[o++] = v;
					buf[o++] = r;
					buf[o++] = g;
					buf[o++] = b;
					buf[o++] = alpha;
				};
				corner(-1, 1, u0, v0);
				corner(1, 1, u1, v0);
				corner(1, -1, u1, v1);
				corner(-1, 1, u0, v0);
				corner(1, -1, u1, v1);
				corner(-1, -1, u0, v1);
			}
			return o / FLOATS;
		}

		_fill_ribbon(rb) {
			const d = rb.def;
			const list = rb.head ? rb.edges.concat([{ top: rb.head.top, bot: rb.head.bot, age: 0 }]) : rb.edges;
			if (list.length < 2)
				return 0;
			const life = d.edgeLifetime > 0 ? d.edgeLifetime : 1;
			const [u0, v0, u1, v1] = cell_rect(rb.slot, Math.max(1, d.rows), Math.max(1, d.columns));
			const c = rb.color, a = rb.alpha;
			const buf = this.buffer;
			let o = 0;
			const put = (pos, u, v) => {
				buf[o++] = pos[0];
				buf[o++] = pos[1];
				buf[o++] = pos[2];
				buf[o++] = u;
				buf[o++] = v;
				buf[o++] = c[0];
				buf[o++] = c[1];
				buf[o++] = c[2];
				buf[o++] = a;
			};
			for (let i = 0; i + 1 < list.length; i++) {
				const e0 = list[i], e1 = list[i + 1];
				const ua = u0 + (u1 - u0) * Math.min(1, e0.age / life);
				const ub = u0 + (u1 - u0) * Math.min(1, e1.age / life);
				put(e0.top, ua, v0);
				put(e1.top, ub, v0);
				put(e1.bot, ub, v1);
				put(e0.top, ua, v0);
				put(e1.bot, ub, v1);
				put(e0.bot, ua, v1);
			}
			return o / FLOATS;
		}

		dispose() {
			const gl = this.r.ctx && this.r.ctx.gl;
			if (gl && this.vao) {
				gl.deleteVertexArray(this.vao);
				gl.deleteBuffer(this.vbo);
			}
			this.vao = null;
			this.vbo = null;
			this.emitters = [];
			this.ribbons = [];
		}
	}

	M3D.ParticleSystem = ParticleSystem;
})();
