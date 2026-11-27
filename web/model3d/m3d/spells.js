/* Spell previews for the turntable. Placement, timing and animations come
   from the shipped spell visual kits. Both actors and all effect models
   use the same M2 renderer as the character and its enchanted weapons. */
'use strict';

(() => {
	const M3D = globalThis.M3D;
	const { mat4_multiply } = M3D.math;
	const zero = [0, 0, 0];
	const point = m => Array.from(m.subarray(12, 15));
	const sec = ms => Math.max(0, Number(ms) || 0) / 1000;

	function anchor(actor, attachment) {
		return attachment < 0 ? actor.char.model_matrix : actor.char.getAttachmentTransform(attachment);
	}

	// DB2 offsets are in WoW axes (x forward, y left, z up), in yards.
	function offsetPoint(m, offset = zero) {
		const [x, y, z] = offset;
		return [0, 1, 2].map(i => m[12+i] + x*m[i] + z*m[4+i] - y*m[8+i]);
	}

	function usableKits(spec) {
		return spec.kits.filter(kit => {
			if (kit.phase === 'precast' && !spec.cast_ms && spec.equipment !== 'ranged') return false;
			if (kit.phase === 'aura' || kit.phase === 'impact') {
				const eventTarget = spec.target === 'self' ? 'caster' : 'target';
				if (kit.target === 'target-not-caster') return spec.target !== 'self';
				if (kit.target !== eventTarget && spec.kits.some(k => k.phase === kit.phase && k.target === eventTarget))
					return false;
			}
			return true;
		});
	}

	// SpellChainEffects row 8 has zero wave, arc, joint and flicker counts.
	// Its surface is a camera-facing strip between two attachment points.
	class SpellChain {
		static async load(def, files, ctx) {
			const blob = await files.fetch(def.texture);
			if (!blob) throw new Error('missing chain texture ' + def.texture);
			const chain = new SpellChain();
			chain.def = def;
			chain.ctx = ctx;
			const gl = ctx.gl;
			chain.texture = new M3D.GLTexture(ctx);
			chain.texture.set_blp(new M3D.BLPImage(blob), { flags: 1 });
			if (!ctx.chainShader) ctx.chainShader = new M3D.ShaderProgram(ctx, `#version 300 es
				layout(location=0) in vec3 position;
				layout(location=1) in vec2 uv;
				uniform mat4 view;
				uniform mat4 projection;
				out vec2 coord;
				void main(){ coord=uv; gl_Position=projection*view*vec4(position,1.); }`, `#version 300 es
				precision highp float;
				uniform sampler2D image;
				uniform vec4 color;
				in vec2 coord;
				out vec4 pixel;
				void main(){ pixel=texture(image,coord)*color; }`);
			chain.vao = gl.createVertexArray();
			chain.buffer = gl.createBuffer();
			gl.bindVertexArray(chain.vao);
			gl.bindBuffer(gl.ARRAY_BUFFER, chain.buffer);
			gl.enableVertexAttribArray(0);
			gl.enableVertexAttribArray(1);
			gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 20, 0);
			gl.vertexAttribPointer(1, 2, gl.FLOAT, false, 20, 12);
			gl.bindVertexArray(null);
			return chain;
		}

		render(view, proj, source, dest, time) {
			const { ctx, def } = this, gl = ctx.gl;
			const delta = dest.map((v,i) => v-source[i]);
			const length = Math.hypot(...delta);
			if (!length) return;
			const d = delta.map(v => v/length);
			const eye = [view[2],view[6],view[10]];
			const normal = [d[1]*eye[2]-d[2]*eye[1], d[2]*eye[0]-d[0]*eye[2], d[0]*eye[1]-d[1]*eye[0]];
			const norm = Math.hypot(...normal);
			if (!norm) return;
			const segments = Math.max(1, Math.ceil(length / Math.max(.01, def.avg_seg_len)));
			const vertices = new Float32Array((segments + 1) * 10);
			const period = sec(def.seg_duration);
			const scroll = period ? time / period : 0;
			for (let i = 0, out = 0; i <= segments; i++) {
				const f = i / segments, width = (1-f)*def.start_width + f*def.end_width;
				for (const side of [-1,1]) {
					for (let k = 0; k < 3; k++)
						vertices[out++] = source[k] + f*delta[k] + .5*width*side*normal[k]/norm;
					vertices[out++] = length*f*def.tex_coord_scale + scroll;
					vertices[out++] = side > 0 ? 1 : 0;
				}
			}
			const shader = ctx.chainShader;
			shader.use();
			shader.set_uniform_mat4('view', false, view);
			shader.set_uniform_mat4('projection', false, proj);
			shader.set_uniform_4f('color', ...def.color.map(c => c/255));
			shader.set_uniform_1i('image', 0);
			this.texture.bind(0);
			ctx.apply_blend_mode(def.blend_mode === 3 ? M3D.GLContext.BlendMode.NO_ALPHA_ADD : def.blend_mode);
			ctx.set_cull_face(false);
			ctx.set_depth_write(false);
			gl.bindVertexArray(this.vao);
			gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
			gl.bufferData(gl.ARRAY_BUFFER, vertices, gl.DYNAMIC_DRAW);
			gl.drawArrays(gl.TRIANGLE_STRIP, 0, vertices.length/5);
			gl.bindVertexArray(null);
			ctx.reset_state();
		}

		dispose() {
			this.texture.dispose();
			this.ctx.gl.deleteBuffer(this.buffer);
			this.ctx.gl.deleteVertexArray(this.vao);
		}
	}
	M3D.SpellChain = SpellChain;

	class SpellPlayback {
		constructor(spec, caster) {
			this.spec = spec;
			this.caster = caster;
			this.nodes = [];
			this.clips = [];
			this.chains = [];
			this.tints = [];
			this.active = [];
			this.time = 0;
		}

		static async load(spec, caster, load, files) {
			const spell = new SpellPlayback(spec, caster);
			try {
				if (spec.target === 'other') {
					spell.other = { char: caster.char.skeletonInstance(), collections: [], skinned: [] };
					const box = caster.char.getDrawnBox() || caster.char.getBoundingBox();
					// Beyond the edge of the character stage, at a fixed world
					// distance. Missile speed and attachment heights stay in yards.
					spell.distance = 6 * (box.max[1] - box.min[1]);
				}
				await spell.prepare(load, files);
				return spell;
			} catch (e) {
				files.fail(spec.spell_id, e);
				spell.dispose();
				return null;
			}
		}

		async prepare(load, files) {
			const spec = this.spec, kits = usableKits(spec);
			const victim = this.other || this.caster;
			const clip = async (model, id) => {
				if (!(id >= 0)) return null;
				const index = model.find_animation(id);
				if (index < 0 || !await model.prepareAnimation(index, fid => files.quiet(fid))) return null;
				return { model, id, index, length: model.animation_duration(index) };
			};
			const modelNode = async (def, actor, phase, kit) => {
				const attach = def.attachment;
				if (!anchor(actor, attach)) throw new Error('missing spell attachment ' + attach);
				const model = await load(def.model, def.textures);
				if (!model) throw new Error('missing spell model ' + def.model);
				const node = { def, actor, phase, kit, model };
				this.nodes.push(node);
				for (const [type, file] of Object.entries(def.textures || {}))
					if (!await model.overrideTextureType(Number(type), file)) throw new Error('missing missile texture ' + file);
				if (model.m2.emitterError || model.m2.textures.some((t, i) => t.fileDataID > 0 && !model.textures.has(i)))
					throw new Error('incomplete spell model ' + def.model);
				const animated = id => clip(model, id);
				node.start = await animated(def.start_animation >= 0 ? def.start_animation : 0);
				node.loop = await animated(def.animation >= 0 ? def.animation : 158);
				node.end = await animated(def.end_animation >= 0 ? def.end_animation : 159);
				model.opacity = def.alpha ?? 1;
				const [yaw, pitch, roll] = def.rotation || zero;
				const [x,y,z] = def.offset || zero;
				const k = def.scale ?? 1;
				model.setTransform([x,z,-y], [roll,yaw,-pitch], [k,k,k]);
				node.local = model.model_matrix.slice();
				return node;
			};
			for (const kit of kits) {
				const actor = kit.phase === 'aura' || kit.phase === 'impact' ? victim : this.caster;
				for (const def of kit.effects || []) await modelNode(def, actor, kit.phase, kit);
				for (const def of kit.chains || []) {
					const chain = await M3D.SpellChain.load(def, files, this.caster.char.ctx);
					this.chains.push({ chain, phase: kit.phase, kit });
				}
				for (const procedure of kit.procedures || []) {
					if (procedure.type !== 1) continue;
					const [packed, period, endTime, fade] = procedure.values;
					const rgb = [16,8,0].map(shift => ((packed >> shift) & 255)/255);
					this.tints.push({ actor, kit, phase: kit.phase, rgb, period, endTime, fade });
				}
				const entry = { ...kit, actor };
				for (const [field, id] of [['first',kit.initial_animation], ['loop',kit.animation]]) {
					entry[field] = await clip(actor.char, id);
					// No event-produced animation is a valid visual. A declared
					// caster motion needs a real sequence before this action runs.
					if (!entry[field] && id > 0 && ['cast','precast'].includes(kit.phase))
						throw new Error('missing character animation ' + id);
				}
				if (entry.first || entry.loop) this.clips.push(entry);
			}
			for (const def of spec.missiles || []) {
				const node = await modelNode(def, this.caster, 'missile', {});
				node.destination = anchor(victim, def.destination_attachment);
				if (!node.destination || !(spec.speed > 0)) throw new Error('missing missile trajectory');
			}
			if (!this.nodes.length && !this.clips.length && !this.chains.length) throw new Error('empty spell kit');
		}

		muzzle(def) {
			if (def.attachment >= 0) return anchor(this.caster, def.attachment);
			if (this.releaseEvent) return this.caster.char.bonePointTransform(this.releaseEvent);
			const item = this.caster.attachments.find(a => a.slot === 18);
			return this.spec.equipment === 'ranged' && item ? anchor(this.caster, item.attach) : this.caster.char.model_matrix;
		}

		begin() {
			const spec = this.spec;
			this.begun = true;
			this.cast = sec(spec.cast_ms);
			// Instant ranged abilities still draw the bow or raise the wand
			// using the equipment kit's ready sequence before the release.
			if (spec.equipment === 'ranged') {
				for (const k of this.clips.filter(k => k.phase === 'precast'))
					this.cast = Math.max(this.cast, k.first?.length || k.loop?.length || 0, spec.cast_ms ? 0 : sec(spec.item_delay_ms));
			}
			// The actual release timestamp and point are authored on the
			// character's M2, including separate left and right releases.
			const release = this.clips.find(k => k.phase === 'cast')?.loop;
			const char = this.caster.char;
			const releases = char.m2.castEvents || [];
			this.launch = this.cast + sec(spec.launch_delay_ms);
			if (release) {
				const index = char.m2.animations.findIndex(a => a.id === release.id && a.variationIndex === 0);
				const tags = spec.equipment === 'ranged' ? ['$BWR','$CST','$CSR','$CSL'] : ['$CST','$CSR','$CSL'];
				for (const tag of tags) {
					const e = releases.find(e => e.id === tag && e.times.arr[index]?.length);
					if (!e) continue;
					this.releaseEvent = e;
					this.launch += sec(e.times.arr[index][0]);
					break;
				}
			}
			this.impact = this.launch;
			for (const n of this.nodes.filter(n => n.phase === 'missile')) {
				const start = offsetPoint(this.muzzle(n.def), n.def.offset);
				const end = offsetPoint(anchor(this.other || this.caster, n.def.destination_attachment), n.def.destination_offset);
				n.flight = Math.hypot(...end.map((v,i) => v - start[i])) / spec.speed;
				this.impact = Math.max(this.impact, this.launch + n.flight);
			}
			const channel = this.clips.some(k => k.phase === 'channel') || this.chains.some(k => k.phase === 'channel');
			const natural = n => (n.first?.length || n.start?.length || 0) + (n.loop?.length || 0);
			const timing = n => {
				const phase = n.phase;
				n.at = phase === 'precast' ? 0 : phase === 'missile' ? this.launch : ['impact','aura'].includes(phase) ? this.impact : this.cast;
				n.at += sec(n.kit?.delay_ms || n.delay_ms) + sec((n.kit || n).start_offset_ms?.[0]) + sec(n.def?.delay_ms);
				const duration = phase === 'precast' ? this.cast - n.at : phase === 'missile' ? n.flight :
					(phase === 'aura' || phase === 'channel' || (phase === 'cast' && channel)) ? sec(spec.duration_ms) :
					Math.max(sec(n.kit?.min_ms || n.min_ms), natural(n));
				n.until = n.at + Math.max(0, duration);
			};
			const events = [...this.nodes, ...this.clips, ...this.chains, ...this.tints];
			events.forEach(timing);
			const ends = n => n.until + (n.end?.length || 0);
			this.finish = Math.max(this.impact, ...events.map(ends));
			this.restAt = Math.max(this.impact, ...events.filter(n => n.phase !== 'aura').map(ends));
			this.tick(0);
		}

		tick(dt, animateActors = true) {
			this.time += dt;
			const t = this.time;
			for (const actor of (animateActors ? [this.caster, this.other].filter(Boolean) : [])) {
				// Channel and release win over the victim's optional reaction
				// when both recipients are the same actor in a self-cast.
				let choice = null;
				for (const k of this.clips) {
					if (actor !== k.actor || t < k.at || t >= k.until) continue;
					const c = t - k.at < (k.first?.length || 0) ? k.first : k.loop;
					if (c && (!choice || ['cast','channel'].includes(k.phase))) choice = c;
				}
				const model = actor.char;
				if (choice !== actor.spellClip) {
					model.playPreparedAnimation(choice ? choice.index : model.find_animation(0), true);
					actor.spellClip = choice;
				}
				if (actor === this.caster) this.animation = choice?.id || 0;
			}
			for (const n of this.tints) {
				const elapsed = t - n.at;
				const end = n.endTime > 0 ? Math.min(n.until, n.at + n.endTime) : n.until;
				let strength = elapsed >= 0 && t < end ? 1 : 0;
				if (n.fade > 0) strength *= Math.max(0, Math.min(1, (end-t)/n.fade));
				if (n.period > 0) strength *= .5 - .5*Math.cos(2*Math.PI*elapsed/n.period);
				const tint = n.rgb.map(v => 1 + (v-1)*strength);
				for (const m of [n.actor.char, ...n.actor.collections, ...n.actor.skinned]) m.tint = tint;
			}
			return t < this.finish;
		}

		draw(dt, view, proj) {
			const t = this.time;
			this.active.length = 0;
			for (const n of this.nodes) {
				const age = t - n.at;
				if (age < 0 || t >= n.until + (n.end?.length || 0)) continue;
				const model = n.model;
				const selected = t >= n.until ? n.end : age < (n.start?.length || 0) ? n.start : n.loop || n.start;
				if (selected && selected !== n.clip) {
					model.playPreparedAnimation(selected.index);
					n.clip = selected;
				}
				if (n.phase === 'missile') {
					const source = n.origin ??= offsetPoint(this.muzzle(n.def), n.def.offset);
					const end = offsetPoint(anchor(this.other || this.caster, n.def.destination_attachment), n.def.destination_offset);
					const f = n.flight > 0 ? Math.min(1, age / n.flight) : 1;
					const delta = end.map((v,i) => v-source[i]);
					const pos = source.map((v,i) => v + f*delta[i]);
					const scale = n.def.scale ?? 1;
					model.setTransform(pos, [0, Math.atan2(-delta[2], delta[0]),
						Math.atan2(delta[1], Math.hypot(delta[0], delta[2]))], [scale, scale, scale]);
				} else {
					const at = anchor(n.actor, n.def.attachment);
					mat4_multiply(model.model_matrix, at, n.local);
				}
				model.billboardView = view;
				model.updateAnimation(Math.min(dt, age));
				model.render(view, proj);
				this.active.push(model);
			}
		}

		drawParticles(view, proj) {
			for (const model of this.active) model.renderParticles(view, proj);
			for (const n of this.chains) {
				if (this.time < n.at || this.time >= n.until) continue;
				const hand = this.nodes.find(e => e.phase === 'channel') || this.nodes.find(e => e.phase === 'cast');
				const victim = this.nodes.find(e => e.phase === 'aura');
				if (hand && victim) n.chain.render(view, proj, point(anchor(this.caster, hand.def.attachment)),
					point(anchor(this.other || this.caster, victim.def.attachment)), this.time - n.at);
			}
		}

		dispose() {
			for (const node of this.nodes) node.model.dispose();
			for (const n of this.chains) n.chain.dispose();
			if (this.begun && this.caster.spellClip) {
				this.caster.char.playPreparedAnimation(this.caster.char.find_animation(0), true);
				this.caster.spellClip = null;
			}
			if (this.begun) for (const m of [this.caster.char, ...this.caster.collections, ...this.caster.skinned]) m.tint = [1,1,1];
			this.other?.char.dispose();
		}
	}
	M3D.SpellPlayback = SpellPlayback;
})();
