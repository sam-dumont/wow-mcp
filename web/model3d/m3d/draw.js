/*!
	wow.export (https://github.com/Kruithne/wow.export)
	Authors: Kruithne <kruithne@gmail.com>
	License: MIT (LICENSE-wow.export.txt)
	Ported for wow-stats: the character scene of modules/tab_characters.js
	(update_textures, update_skinned_models, update_equipment_models) and
	the frame loop of components/model-viewer-gl.js, driven by the server's
	recipe instead of wow.export's client tables.

	M3D.draw(canvas, recipe, get, opts) -> Promise<{stop(), redraw(recipe), missing}>
	  recipe  the server's JSON (wowstats.model3d, "recipe" v1)
	  get     fileId -> Promise<ArrayBuffer|null>; null (or a throw) skips
	          what needs that file, which then shows in `missing`
	  opts    background: null (transparent, the default), [r, g, b, a] in
	          0..1 or "#rrggbb"; clock: () => ms (animation time, tests);
	          maxPixelRatio: 2; seed: the particles' random seed (1);
	          idle: true plays another animation now and then (Idle,
	          below; off by default: the renderer's pictures stay still);
	          random: () => [0, 1) for Idle (tests); reducedMotion:
	          () => bool (default: the prefers-reduced-motion query)
 */
'use strict';

(() => {
	const M3D = globalThis.M3D = globalThis.M3D || {};
	const { Reader, MissingFile, M2RendererGL, CharMaterial, GLContext, PerspectiveCamera, TurntableControls } = M3D;
	const { mat4_multiply, mat4_from_scale } = M3D.math;

	// The character faces the camera (wow.export turns it 90 degrees too).
	const FACING = -Math.PI / 2;
	const FOV = 40;

	// ------------------------------------------------------------------ files

	// One drawing's files: each asked once, the misses and failures noted.
	class Files {
		constructor(get) {
			this.get = get;
			this.cache = new Map();
			this.missing = [];
			this.errors = [];
			this.seed = 1;
		}

		miss(id) {
			if (!this.missing.includes(id))
				this.missing.push(id);
		}

		fail(id, err) {
			this.errors.push({ file: id, error: String((err && err.message) || err) });
		}

		// fileId -> Promise<M3D.Reader|null>, a new reader each call
		fetch(id) {
			id = Number(id);
			if (!Number.isInteger(id) || id <= 0)
				return Promise.resolve(null);

			if (!this.cache.has(id)) {
				this.cache.set(id, Promise.resolve().then(() => this.get(id)).then((buf) => {
					if (buf instanceof ArrayBuffer || buf instanceof Uint8Array)
						return buf;
					if (buf !== null && buf !== undefined)
						this.fail(id, 'not an ArrayBuffer');
					this.miss(id);
					return null;
				}, (err) => {
					this.miss(id);
					this.fail(id, err);
					return null;
				}));
			}

			return this.cache.get(id).then((buf) => (buf ? new Reader(buf) : null));
		}

		// fetch() for a file the drawing can do without (Idle's .anim
		// files): one that doesn't come isn't listed in `missing`.
		quiet(id) {
			id = Number(id);
			if (!Number.isInteger(id) || id <= 0)
				return Promise.resolve(null);
			if (!this.quiet_cache)
				this.quiet_cache = new Map();
			if (!this.quiet_cache.has(id)) {
				this.quiet_cache.set(id, Promise.resolve().then(() => this.get(id)).then(
					(buf) => (buf instanceof ArrayBuffer || buf instanceof Uint8Array ? buf : null), () => null));
			}
			return this.quiet_cache.get(id).then((buf) => (buf ? new Reader(buf) : null));
		}
	}

	// ------------------------------------------------------------------ idle

	const STAND = 0;
	const IDLE_MIN = 8000, IDLE_MAX = 20000;  // ms of Stand between two moves
	const AFTER_DRAG = 3000;  // ms after the person lets go before a move may start

	// Stand, and now and then (every IDLE_MIN-IDLE_MAX ms of the viewer's
	// clock) one of `moves` the model has, played through, then Stand
	// again. A move whose sequence the model lacks, or whose .anim file
	// doesn't come, is never played (no stand-in). Nothing starts while the
	// person turns the model, or under prefers-reduced-motion.
	class Idle {
		constructor(viewer) {
			this.viewer = viewer;
			this.random = typeof viewer.opts.random === 'function' ? viewer.opts.random : Math.random;
			this.reduced = typeof viewer.opts.reducedMotion === 'function' ? viewer.opts.reducedMotion : () => {
				try { return !!(globalThis.matchMedia && globalThis.matchMedia('(prefers-reduced-motion: reduce)').matches); }
				catch (e) { return false; }
			};
			this.reset(viewer.clock());
		}

		// A new drawing (or the tab shown again): Stand, the next move later.
		reset(now) {
			this.gen = (this.gen || 0) + 1;
			this.dragged_at = null;
			this.playing = null;
			this.starting = false;
			this.last_move = null;
			this.available = null;
			this.loading = null;
			this.clips = new Map();
			this.played = [];
			this.schedule(now);
		}

		schedule(now) {
			this.next_at = now + IDLE_MIN + this.random() * (IDLE_MAX - IDLE_MIN);
		}

		// Validate each clip against this model and its available .anim files.
		async moves_for(char, files, moves) {
			const out = [];
			const clips = this.clips;
			const playable = (idx) => {
				if (!clips.has(idx))
					clips.set(idx, char.prepareAnimation(idx, (fid) => files.quiet(fid)).catch(() => false));
				return clips.get(idx);
			};
			for (const m of moves) {
				if (m.spell) {
					out.push(m);
					continue;
				}
				const steps = [];
				for (const [id, times, optional] of m.steps) {
					const all = char.animation_indices(id);
					const variations = [];
					for (const idx of all) {
						if (await playable(idx))
							variations.push(idx);
					}
					if (!variations.length) {
						if (optional) continue;
						steps.length = 0;
						break;
					}
					steps.push({ id, times, variations });
				}
				if (steps.length)
					out.push({ ...m, steps });
			}
			return out;
		}

		prepare() {
			const v = this.viewer, scene = v.scene, gen = this.gen;
			if (!scene?.char || scene.mount) return Promise.resolve([]);
			if (!this.loading) {
				const canHold = slot => scene.attachments.some(a => a.slot === slot && scene.char.getAttachmentTransform(a.attach));
				const moves = M3D.moves(v.attack, scene.spells).filter(m =>
					(m.equipment !== 'ranged' || canHold(18)) &&
					(m.name !== 'attack' || v.attack === 16 || canHold(16) || !v.classId));
				this.loading = this.moves_for(scene.char, v.files, moves).then(moves => {
					if (gen === this.gen && !v.stopped) this.available = moves;
					return moves;
				});
			}
			return this.loading;
		}

		begin(move, now, spell) {
			this.last_move = move.name;
			this.played.push(move.name);
			this.playing = { move, at: -1, left: 0, spell };
			equip(this.viewer.scene, move.equipment, true);
			if (spell) {
				this.viewer.stopSpell();
				this.viewer.spell = spell;
				this.viewer.turn();
				spell.begin();
			} else this.step(this.viewer.scene.char, now);
		}

		// Called on every frame, before the character's bones are updated.
		tick(now, dt) {
			const v = this.viewer;
			const char = v.scene && v.scene.char;
			if (!char)
				return;
			if (this.playing) {
				const p = this.playing;
				if (p.spell) {
					const visible = p.spell.tick(dt);
					if (visible && p.spell.time < p.spell.restAt) return;
					if (!visible) v.stopSpell();
					this.playing = null;
					equip(v.scene, 'stand', true);
					this.schedule(now);
					return;
				}
				p.left -= dt;
				if (p.left > 0)
					return;
				if (v.scene.mount) {
					v.scene.mount.model.playPreparedAnimation(v.scene.mount.gaits.get('idle'), true);
					v.scene.mount.gait = 'idle';
					this.playing = null;
					this.schedule(now);
				} else this.step(char, now);
				return;
			}
			if (v.controls.pointer !== null)
				this.dragged_at = now;
			if (this.starting || now < this.next_at)
				return;
			if (now - (this.dragged_at ?? -Infinity) < AFTER_DRAG || this.reduced()) {
				this.schedule(now);
				return;
			}
			if (v.scene.mount) {
				const mount = v.scene.mount;
				const choices = [...mount.gaits.keys()].filter(name => name !== 'idle');
				const name = choices[Math.floor(this.random() * choices.length)];
				mount.model.playPreparedAnimation(mount.gaits.get(name), true);
				mount.gait = name;
				this.playing = { move: { name }, id: M3D.GAITS[name], left: mount.model.animation_duration(mount.gaits.get(name)) * 3 };
			} else this.start();
		}

		async start() {
			const gen = this.gen;
			this.starting = true;
			try {
				await this.prepare();
				if (gen !== this.gen || this.playing || this.viewer.stopped || !this.available)
					return;
				const pool = this.available.filter((m) => m.name !== this.last_move);
				const list = pool.length ? pool : this.available;
				if (!list.length) {
					this.schedule(this.viewer.clock());
					return;
				}
				const m = list[Math.min(list.length - 1, Math.floor(this.random() * list.length))];
				const spell = m.spell ? await this.viewer.loadSpell(m.spell) : null;
				if (gen !== this.gen || this.viewer.stopped || this.reduced() || this.viewer.controls.pointer !== null) {
					spell?.dispose();
					this.schedule(this.viewer.clock());
					return;
				}
				if (m.spell && !spell) {
					this.available = this.available.filter(candidate => candidate !== m);
					this.schedule(this.viewer.clock());
					return;
				}
				this.begin(m, this.viewer.clock(), spell);
			} finally {
				if (gen === this.gen)
					this.starting = false;
			}
		}

		// The next step of the move playing, or Stand when it's done.
		step(char, now) {
			const p = this.playing;
			p.at++;
			if (p.at >= p.move.steps.length) {
				this.playing = null;
				equip(this.viewer.scene, 'stand', true);
				this.schedule(now);
				char.playPreparedAnimation(char.find_animation(STAND), true);
				return;
			}
			const s = p.move.steps[p.at];
			const idx = s.variations[Math.min(s.variations.length - 1, Math.floor(this.random() * s.variations.length))];
			p.id = s.id;
			char.playPreparedAnimation(idx, true);
			p.duration = p.left = Math.max(0.2, char.animation_duration(idx)) * s.times;
		}

		// What is playing: the AnimationData id, or 0 for Stand (tests).
		state() {
			return { playing: this.playing?.spell?.animation ?? this.playing?.id ?? STAND, move: this.playing ? this.playing.move.name : null,
				next_at: this.next_at, played: this.played.slice(),
				available: this.available ? this.available.map((m) => m.name) : null };
		}
	}

	// ------------------------------------------------------------------ the recipe

	const obj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});
	const list = (v) => (Array.isArray(v) ? v : []);
	const entries = (v) => Object.entries(obj(v)).map(([k, x]) => [Number(k), x]).filter(([k]) => Number.isInteger(k));

	// The geoset rules (recipe "geosets"). The character: wow.export's
	// defaults, then `set`, then `groups`. A skinned model
	// (update_skinned_models): only geoset 0 and what `set` turns on. A
	// collection model (update_equipment_models): everything when the recipe
	// names nothing, else nothing but what `set` and `groups` turn on.
	function apply_geosets(renderer, spec, mode) {
		const set = entries(obj(spec).set);
		const groups = entries(obj(spec).groups);
		const dcs = renderer.draw_calls;

		for (const dc of dcs) {
			const id = dc.geoset;
			if (mode === 'character') {
				const s = String(id);
				let on = id === 0 || s.endsWith('01') || s.startsWith('32');
				if (s.startsWith('17') || s.startsWith('35'))
					on = false;
				dc.visible = on;
			} else if (mode === 'skinned') {
				dc.visible = id === 0;
			} else {
				dc.visible = set.length === 0 && groups.length === 0;
			}
		}

		for (const [id, on] of set) {
			if (id === 0 && mode === 'character')
				continue;
			for (const dc of dcs) {
				if (dc.geoset === id)
					dc.visible = !!on;
			}
		}

		for (const [group, value] of groups) {
			const lo = group * 100;
			const v = Number(value) || 0;
			for (const dc of dcs) {
				if (dc.geoset > lo && dc.geoset < lo + 100)
					dc.visible = v > 0 && dc.geoset === lo + v;
			}
		}
	}

	async function load_model(ctx, id, files, skip_types) {
		const data = await files.fetch(id);
		if (!data)
			return null;

		const r = new M2RendererGL(data, ctx, (fid) => files.fetch(fid), { seed: (Math.imul(files.seed, 31) + id) >>> 0 });
		try {
			r.fileDataID = id;
			await r.load(skip_types);
			if (r.m2.emitterError)
				files.fail(id, 'particles: ' + r.m2.emitterError);
			return r;
		} catch (e) {
			r.dispose();
			if (!(e instanceof MissingFile))
				files.fail(id, e);
			return null;
		}
	}

	async function build_material(ctx, m, files) {
		const width = Number(m.width) | 0, height = Number(m.height) | 0;
		if (width <= 0 || height <= 0 || width > 4096 || height > 4096)
			return null;

		const layers = list(m.layers);
		const blps = await Promise.all(layers.map(async (layer) => {
			const data = await files.fetch(layer.file);
			if (!data)
				return null;
			try {
				return new M3D.BLPImage(data);
			} catch (e) {
				files.fail(Number(layer.file), e);
				return null;
			}
		}));

		const mat = new CharMaterial(ctx, width, height);
		layers.forEach((layer, i) => {
			if (!blps[i])
				return;
			mat.addLayer({
				target: Number(layer.target) || 0,
				x: Number(layer.x) || 0, y: Number(layer.y) || 0,
				w: Number(layer.w) || width, h: Number(layer.h) || height,
				blend: Number(layer.blend) || 0
			}, blps[i]);
		});
		mat.update();
		return mat;
	}

	// Binds a recipe `textures` map: type -> file id, or "material:<type>"
	// (a skinned model wearing the character's composite; the body skin when
	// that type has none, as wow.export's apply_skinned_model_textures does).
	async function bind_textures(renderer, textures, materials) {
		for (const [type, v] of entries(textures)) {
			if (typeof v === 'string' && v.startsWith('material:')) {
				const mt = Number(v.slice('material:'.length));
				const mat = materials.get(mt) || materials.get(1);
				if (mat)
					renderer.overrideTextureTypeWithMaterial(type, mat);
			} else {
				await renderer.overrideTextureType(type, v);
			}
		}
	}

	const skip_for = (textures) => new Set(entries(textures).map(([k]) => k));

	// The mount's primary saddle is attachment 0. Its bone, position, and
	// movement all come from the creature file. No saddle or rider clip
	// means the character stays on foot.
	const SADDLE = 0;
	async function load_mount(ctx, spec, files, rider) {
		const clip = async (r, id) => {
			const index = r.find_animation(id);
			const ready = await r.prepareAnimation(index, (fid) => files.quiet(fid)).catch(() => false);
			return ready ? index : -1;
		};
		const riderClip = await clip(rider, M3D.RIDER_ANIMATION);
		if (riderClip < 0) return null;
		const skins = entries(spec.textures).filter(([, file]) => Number.isInteger(file) && file > 0);
		const model = await load_model(ctx, spec.model, files, new Set(skins.map(([type]) => type)));
		if (!model) return null;
		try {
			if (model.getAttachmentTransform(SADDLE)) {
				const textures = await Promise.all(skins.map(([type, file]) => model.overrideTextureType(type, file)));
				const gaits = new Map();
				for (const [name, id] of Object.entries(M3D.GAITS)) {
					const index = await clip(model, id);
					if (index >= 0) gaits.set(name, index);
				}
				if (textures.every(Boolean) && gaits.has('idle') && gaits.size > 1)
					return { model, riderClip, gaits, scale: Number(spec.scale) > 0 ? Number(spec.scale) : 1 };
			}
		} catch (e) {
			files.fail(spec.model, e);
		}
		model.dispose();
		return null;
	}

	async function load_scene(ctx, recipe, files) {
		const r = obj(recipe);
		const scene = { char: null, materials: new Map(), attachments: [], collections: [], skinned: [] };
		scene.recipe = recipe;
		scene.spells = list(r.actions);
		scene.ranged = obj(r.ranged);
		scene.mounts = list(r.mounts);
		scene.visibleAttachments = [];

		const material_list = list(r.materials);
		const skip = skip_for(r.textures);
		for (const m of material_list)
			skip.add(Number(m.type));

		const [char, mats] = await Promise.all([
			load_model(ctx, r.model, files, skip),
			Promise.all(material_list.map((m) => build_material(ctx, m, files).catch((e) => {
				files.fail(0, e);
				return null;
			})))
		]);

		material_list.forEach((m, i) => {
			if (mats[i])
				scene.materials.set(Number(m.type), mats[i]);
		});

		if (!char)
			return scene;
		scene.char = char;

		await bind_textures(char, r.textures, scene.materials);
		for (const [type, mat] of scene.materials)
			char.overrideTextureTypeWithMaterial(type, mat);
		apply_geosets(char, r.geosets, 'character');
		await char.playAnimationById(Number(r.animation) || 0);

		const attached = async (a) => {
			const item = await load_model(ctx, a.model, files, skip_for(a.textures));
			if (!item)
				return null;
			await bind_textures(item, a.textures, scene.materials);
			await item.playAnimationById(0);
			// An effect goes on one of the item model's own attachment
			// points; one the model lacks shows nothing, as in game (the
			// Dreamstaff has points 1-4, its ItemVisual names 1-5: the fifth
			// was drawn in the hand).
			const visuals = (await Promise.all(list(a.visuals).map(async (v) => {
				if (!item.getAttachmentTransform(Number(v.attach) || 0))
					return null;
				const vis = await load_model(ctx, v.model, files, new Set());
				if (!vis)
					return null;
				await vis.playAnimationById(0);
				return { renderer: vis, attach: Number(v.attach) || 0 };
			}))).filter(Boolean);
			const scale = Number(a.scale) > 0 ? Number(a.scale) : 1;
			return { renderer: item, slot: Number(a.slot), attach: Number(a.attach) || 0, scale, visuals };
		};
		const with_bones = (mode) => async (c) => {
			const model = await load_model(ctx, c.model, files, skip_for(c.textures));
			if (!model)
				return null;
			model.buildBoneRemapTable(char.bones);
			await bind_textures(model, c.textures, scene.materials);
			apply_geosets(model, c.geosets, mode);
			await model.playAnimationById(0);
			return model;
		};

		const load_companion = async (pet) => {
			if (!pet || !pet.model) return null;
			const model = await load_model(ctx, pet.model, files, skip_for(pet.textures));
			if (!model) return null;
			try {
				await bind_textures(model, pet.textures, scene.materials);
				await model.playAnimationById(0);
				return model;
			} catch (e) {
				files.fail(pet.model, e);
				model.dispose();
				return null;
			}
		};
		const [pet, attachments, collections, skinned] = await Promise.all([
			load_companion(r.companion),
			Promise.all(list(r.attachments).map(attached)),
			Promise.all(list(r.collections).map(with_bones('collection'))),
			Promise.all(list(r.skinned).map(with_bones('skinned')))
		]);
		scene.pet = pet;
		scene.attachments = attachments.filter(Boolean);
		scene.collections = collections.filter(Boolean);
		scene.skinned = skinned.filter(Boolean);

		// a held weapon closes the hand around it (attachment 1 right hand,
		// 2 left hand; a shield, 0, hangs from the wrist)
		if (scene.attachments.some((a) => a.attach === 1 || a.attach === 2))
			await char.loadHandsClosed();
		equip(scene);
		return scene;
	}

	function equip(scene, mode = 'stand', blend = false) {
		scene.visibleAttachments = scene.attachments.filter((a) => {
			if (a.slot === 18) return mode === 'ranged' || (mode === 'stand' && scene.ranged.at_rest !== false);
			if (a.slot === 16 || a.slot === 17) return mode !== 'ranged' && mode !== 'empty';
			return true;
		});
		for (const a of scene.attachments) {
			const to = scene.visibleAttachments.includes(a) ? 1 : 0;
			a.wanted = to;
			if (!blend || a.opacity === undefined) a.opacity = to;
		}
		scene.char.setHandGrip(scene.visibleAttachments.some((a) => a.attach === 1),
			scene.visibleAttachments.some((a) => a.attach === 2));
	}

	function all_renderers(scene) {
		const all = [scene.char, scene.mount?.model, scene.petVisible && scene.pet, ...scene.collections, ...scene.skinned];
		for (const a of scene.attachments) {
			if (a.opacity <= 0) continue;
			all.push(a.renderer);
			for (const v of a.visuals)
				all.push(v.renderer);
		}
		return all.filter(Boolean);
	}

	function dispose_scene(scene) {
		if (!scene)
			return;
		const all = [scene.char, scene.mount?.model, scene.pet, ...scene.collections, ...scene.skinned];
		for (const a of scene.attachments) {
			all.push(a.renderer);
			for (const v of a.visuals)
				all.push(v.renderer);
		}
		for (const r of all) {
			if (r)
				r.dispose();
		}
		for (const m of scene.materials.values())
			m.dispose();
	}

	// The box to frame: what is drawn, else the model's own bounds, else
	// its vertices'.
	function frame_box(char) {
		const drawn = char.getDrawnBox && char.getDrawnBox();
		if (drawn && Math.max(drawn.max[0] - drawn.min[0], drawn.max[1] - drawn.min[1]) > 0)
			return drawn;
		const box = char.getBoundingBox();
		const size = box ? Math.max(box.max[0] - box.min[0], box.max[1] - box.min[1], box.max[2] - box.min[2]) : 0;
		if (box && size > 0)
			return box;

		const v = char.m2.vertices;
		if (!v || v.length < 3)
			return { min: [-1, 0, -1], max: [1, 2, 1] };
		const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
		for (let i = 0; i < v.length; i += 3) {
			for (let k = 0; k < 3; k++) {
				min[k] = Math.min(min[k], v[i + k]);
				max[k] = Math.max(max[k], v[i + k]);
			}
		}
		return { min, max };
	}

	// Combine meshes in turntable space. Attachments use their actual bone
	// transform; a mount's display scale moves the saddle but not the rider.
	function scene_box(parts) {
		const points = [], min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
		for (const { box, matrix = M3D.math.IDENTITY_MAT4, scale = 1, offset = [0, 0, 0] } of parts) {
			const vertices = M3D.box_points(box);
			for (let i = 0; i < vertices.length; i += 3) {
				const x = vertices[i] * scale, y = vertices[i + 1] * scale, z = vertices[i + 2] * scale;
				for (let k = 0; k < 3; ++k) {
					const value = x * matrix[k] + y * matrix[k + 4] + z * matrix[k + 8] + matrix[k + 12] + offset[k];
					points.push(value);
					min[k] = Math.min(min[k], value);
					max[k] = Math.max(max[k], value);
				}
			}
		}
		return { min, max, points: new Float32Array(points) };
	}

	function dressed_box(scene) {
		const parts = [{ box: frame_box(scene.char) }];
		for (const part of scene.visibleAttachments) {
			const matrix = scene.char.getAttachmentTransform(part.attach, true);
			if (matrix) parts.push({ box: frame_box(part.renderer), matrix, scale: part.scale });
		}
		for (const model of [...scene.collections, ...scene.skinned]) {
			model.applyExternalBoneMatrices(scene.char.bone_matrices);
			const box = model.getDrawnBox();
			if (box) parts.push({ box });
		}
		return scene_box(parts);
	}

	function parse_background(bg) {
		if (Array.isArray(bg) && bg.length >= 3)
			return [Number(bg[0]) || 0, Number(bg[1]) || 0, Number(bg[2]) || 0, bg.length > 3 ? Number(bg[3]) : 1];
		const m = typeof bg === 'string' && /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(bg);
		if (m)
			return [parseInt(m[1], 16) / 255, parseInt(m[2], 16) / 255, parseInt(m[3], 16) / 255, 1];
		return [0, 0, 0, 0];
	}

	// ------------------------------------------------------------------ the frame

	function draw_actor(scene, dt, view, proj) {
		const char = scene.char;
		char.updateAnimation(dt);
		char.render(view, proj);

		const scale_mat = new Float32Array(16);
		for (const a of scene.attachments) {
			// Crossfade the held model while its hand goes from the previous
			// grip to the new pose, including the return to standing gear.
			const step = Math.min(Math.abs(a.wanted - a.opacity), dt / .15);
			a.opacity += Math.sign(a.wanted - a.opacity) * step;
			a.renderer.opacity = a.opacity * (scene.opacity ?? 1);
			for (const v of a.visuals) v.renderer.opacity = a.renderer.opacity;
			if (a.renderer.opacity <= 0) continue;
			const tf = char.getAttachmentTransform(a.attach);
			if (!tf)
				continue;
			if (a.scale !== 1) {
				mat4_from_scale(scale_mat, a.scale, a.scale, a.scale);
				mat4_multiply(tf, tf.slice(), scale_mat);
			}
			a.renderer.setTransformMatrix(tf);
			a.renderer.updateAnimation(dt);
			a.renderer.render(view, proj);

			for (const v of a.visuals) {
				const at = a.renderer.getAttachmentTransform(v.attach);
				if (!at)
					continue;
				v.renderer.setTransformMatrix(at);
				v.renderer.updateAnimation(dt);
				v.renderer.render(view, proj);
			}
		}

		for (const r of [...scene.collections, ...scene.skinned]) {
			r.updateAnimation(dt);
			r.applyExternalBoneMatrices(char.bone_matrices);
			r.setTransformMatrix(char.model_matrix);
			r.render(view, proj);
		}

	}

	class Viewer {
		constructor(canvas, get, opts) {
			this.canvas = canvas;
			this.get = get;
			this.opts = opts;
			this.clock = typeof opts.clock === 'function' ? opts.clock : () => performance.now();
			this.background = parse_background(opts.background);
			this.ctx = new GLContext(canvas, { alpha: true, preserveDrawingBuffer: true });
			this.camera = new PerspectiveCamera(FOV, 1, 0.1, 100);
			this.rotation = FACING;
			this.scene = null;
			this.seed = Number(opts.seed) >>> 0 || 1;
			this.files = new Files(get);
			this.gen = 0;
			this.stopped = false;
			this.raf = 0;
			this.last = null;
			this.box = null;
			this.fitted = '';
			this.controls = new TurntableControls(canvas, (rot) => {
				this.rotation = rot;
				this.turn();
			}, FACING);
			this.attack = 16;
			this.idle = opts.idle ? new Idle(this) : null;

			// A hidden tab draws nothing and starts nothing: the loop stops,
			// and comes back where it was (no jump: the first frame's dt is 0).
			this.on_visibility = () => {
				if (this.stopped || typeof document === 'undefined')
					return;
				if (document.hidden) {
					if (this.raf)
						cancelAnimationFrame(this.raf);
					this.raf = 0;
				} else if (!this.raf) {
					this.last = null;
					if (this.idle && !this.idle.playing)
						this.idle.schedule(this.clock());
					this.start();
				}
			};
			if (typeof document !== 'undefined')
				document.addEventListener('visibilitychange', this.on_visibility);
		}

		turn() {
			const scene = this.scene;
			if (!scene || !scene.char) return;
			const angle = this.rotation + (scene.mount?.angleOffset || 0), cos = Math.cos(angle), sin = Math.sin(angle);
			const offset = scene.petVisible ? scene.spacing / 2 : 0;
			const k = scene.mount?.scale || 1;
			const position = [-offset * sin, 0, -offset * cos];
			let aim = angle;
			const spell = this.spell;
			if (spell?.other) {
				// From head on, turn toward open space on the stage. When the
				// viewer already shows a profile the shooter keeps that angle.
				const facing = Math.acos(Math.min(1, Math.abs(cos)));
				const direction = (cos < 0 ? -1 : 1) * (sin > 0 ? -1 : 1);
				const yaw = direction * Math.max(0, facing - Math.PI/4);
				const t = spell.time, end = spell.restAt ?? Infinity;
				const f = Math.max(0, Math.min(1, t/.25, (end-t)/.25));
				aim += yaw * f*f*(3-2*f);
				const heading = angle + yaw, range = spell.distance;
				spell.other.char.setTransform([position[0] + range*Math.cos(heading), 0,
					position[2] - range*Math.sin(heading)], [0,heading + Math.PI,0], [1,1,1]);
			}
			(scene.mount?.model || scene.char).setTransform(position, [0, aim, 0], [k, k, k]);
			if (scene.petVisible) {
				const k = scene.petScale;
				scene.pet.setTransform([offset * sin, scene.petGround, offset * cos], [0, angle, 0], [k, k, k]);
			}
		}

		async ride(index) {
			const scene = this.scene, gen = this.gen;
			if (!scene?.char) return false;
			const request = scene.rideRequest = (scene.rideRequest || 0) + 1;
			const spec = scene.mounts[index];
			if (index !== -1 && !spec) return false;
			const mount = spec ? await load_mount(this.ctx, spec, this.files, scene.char) : null;
			if (request !== scene.rideRequest || this.gen !== gen || this.stopped) {
				mount?.model.dispose();
				return false;
			}
			if (spec && !mount) return false;
			// From the untouched front view, show the saddle in three-quarter
			// profile. A visitor who already turned the model keeps that angle.
			if (mount) mount.angleOffset = scene.mount?.angleOffset ?? (this.rotation === FACING ? Math.PI / 6 : 0);
			scene.mount?.model.dispose();
			this.stopSpell();
			scene.mount = mount;
			this.idle?.reset(this.clock());
			equip(scene, mount ? 'empty' : 'stand');
			if (mount) {
				mount.index = index;
				mount.model.playPreparedAnimation(mount.gaits.get('idle'));
				scene.char.playPreparedAnimation(mount.riderClip);
				mount.gait = 'idle';
				// Frame the saddle, the seated pose and the creature as one
				// turntable. The rider keeps its own size on a scaled mount.
				const saddle = mount.model.getAttachmentTransform(SADDLE, true);
				for (let i = 12; i < 15; ++i) saddle[i] *= mount.scale;
				mount.box = scene_box([{ box: frame_box(mount.model), scale: mount.scale },
					{ box: dressed_box(scene), matrix: saddle }]);
			} else {
				scene.char.playPreparedAnimation(scene.char.find_animation(STAND));
			}
			this.frameScene();
			return true;
		}

		async loadSpell(spec) {
			const scene = this.scene, files = this.files;
			const load = (id, textures) => load_model(this.ctx, id, files, skip_for(textures));
			return M3D.SpellPlayback.load(spec, scene, load, files);
		}

		stopSpell() {
			if (!this.spell) return;
			this.spell.dispose();
			this.spell = null;
			this.turn();
		}

		frameScene() {
			const scene = this.scene;
			if (!scene || !scene.char) return;
			scene.petVisible = !!scene.pet;
			const rider = frame_box(scene.char);
			const char = scene.mount?.box || (scene.standingBox ??= dressed_box(scene));
			this.box = char;
			if (scene.petVisible) {
				const pet = frame_box(scene.pet);
				const height = Math.max(0.1, rider.max[1] - rider.min[1]);
				// Fit the creature beside its owner, even for very large tames.
				const k = scene.petScale = Math.min(1, 0.8 * height / Math.max(0.1, pet.max[1] - pet.min[1]));
				scene.petGround = char.min[1] - k * pet.min[1];
				scene.spacing = char.max[2] - k * pet.min[2] + height * 0.12;
				const half = scene.spacing / 2;
				this.box = scene_box([{ box: char, offset: [0, 0, -half] },
					{ box: pet, scale: k, offset: [0, scene.petGround, half] }]);
			}
			this.fitted = '';
			this.turn();
		}

		async show(recipe) {
			if (this.stopped)
				throw new Error('this viewer was stopped');
			const gen = ++this.gen;
			const files = new Files(this.get);
			files.seed = this.seed;
			const scene = await load_scene(this.ctx, recipe, files);
			if (gen !== this.gen || this.stopped) {
				dispose_scene(scene);
				return false;
			}
			this.stopSpell();
			dispose_scene(this.scene);
			this.scene = scene;
			this.files = files;
			this.box = null;
			this.frameScene();
			this.attack = Number(obj(recipe).attack) || 16;
			this.classId = Number(obj(recipe).class_id) || 0;
			this.level = Number(obj(recipe).level) || 1;
			if (this.idle)
				this.idle.reset(this.clock());
			return true;
		}

		size() {
			const c = this.canvas;
			const dpr = Math.min(globalThis.devicePixelRatio || 1, this.opts.maxPixelRatio || 2);
			const cw = c.clientWidth, ch = c.clientHeight;
			if (cw > 0 && ch > 0) {
				const w = Math.max(1, Math.round(cw * dpr)), h = Math.max(1, Math.round(ch * dpr));
				if (c.width !== w || c.height !== h) {
					c.width = w;
					c.height = h;
				}
			}
			return { css_w: cw > 0 ? cw : c.width, css_h: ch > 0 ? ch : c.height };
		}

		frame() {
			this.raf = 0;
			if (this.stopped)
				return;

			const now = this.clock();
			const dt = this.last === null ? 0 : Math.max(0, (now - this.last) / 1000);
			this.last = now;

			const { css_w } = this.size();
			const c = this.canvas;
			const key = c.width + 'x' + c.height + ':' + css_w;
			if (this.box && key !== this.fitted) {
				this.camera.aspect = c.width / Math.max(1, c.height);
				M3D.fit_turntable(this.camera, this.box, css_w);
				this.fitted = key;
			}

			const ctx = this.ctx;
			ctx.set_viewport(c.width, c.height);
			const bg = this.background;
			ctx.set_clear_color(bg[0], bg[1], bg[2], bg[3]);
			ctx.set_depth_write(true);
			ctx.clear(true, true);

			const scene = this.scene;
			const char = scene && scene.char;
			if (char) {
				const view = this.camera.view_matrix, proj = this.camera.projection_matrix;
				const activeSpell = this.idle?.playing?.spell;
				if (this.idle) this.idle.tick(now, dt);
				if (this.spell && this.spell !== activeSpell && !this.idle?.playing?.spell && !this.spell.tick(dt, false))
					this.stopSpell();
				if (this.spell?.other) this.turn();
				if (scene.mount) {
					const { model, scale } = scene.mount;
					model.updateAnimation(dt);
					model.render(view, proj);
					const saddle = model.getAttachmentTransform(SADDLE);
					// The saddle carries the rider's position and rotation.
					// Remove the creature's display scale from the rider's axes.
					for (let i = 0; i < 12; i++) saddle[i] /= scale;
					char.setTransformMatrix(saddle);
				}
				draw_actor(scene, dt, view, proj);
				this.spell?.other?.char.updateAnimation(dt);
				if (scene.petVisible) {
					scene.pet.updateAnimation(dt);
					scene.pet.render(view, proj);
				}
				this.spell?.draw(dt, view, proj);

				// particles and ribbons last, over every mesh
				for (const r of all_renderers(scene)) r.renderParticles(view, proj);
				this.spell?.drawParticles(view, proj);
			}

			this.raf = requestAnimationFrame(() => this.frame());
		}

		start() {
			if (!this.raf && !this.stopped && !(typeof document !== 'undefined' && document.hidden))
				this.raf = requestAnimationFrame(() => this.frame());
		}

		stop() {
			this.stopped = true;
			this.gen++;
			if (this.raf)
				cancelAnimationFrame(this.raf);
			this.raf = 0;
			this.controls.dispose();
			if (typeof document !== 'undefined')
				document.removeEventListener('visibilitychange', this.on_visibility);
			this.stopSpell();
			dispose_scene(this.scene);
			this.scene = null;
		}

		// A point of the character in WoW model coordinates -> CSS pixels on
		// the canvas, as last drawn (tests and debugging).
		project(p) {
			const char = this.scene && this.scene.char;
			if (!char)
				return null;
			const m = char.model_matrix;
			const x = p[0], y = p[2], z = -p[1];
			const w = [
				m[0] * x + m[4] * y + m[8] * z + m[12],
				m[1] * x + m[5] * y + m[9] * z + m[13],
				m[2] * x + m[6] * y + m[10] * z + m[14]
			];
			const c = this.canvas;
			return this.camera.project(w, c.clientWidth || c.width, c.clientHeight || c.height);
		}
	}

	/**
	 * Draw one character recipe on a canvas, and keep drawing it (its
	 * animation, a drag turns it) until stop().
	 * ride(i) seats the character on recipe.mounts[i]. ride(-1) dismounts.
	 * On-foot moves and mounted gaits play occasionally while idle.
	 */
	M3D.draw = async function draw(canvas, recipe, get, opts = {}) {
		const viewer = new Viewer(canvas, get, opts || {});
		await viewer.show(recipe);
		viewer.start();

		const handle = {
			stop: () => viewer.stop(),
			ride: (index) => viewer.ride(index),
			get mounted() { return viewer.scene?.mount?.index ?? -1; },
			redraw: (next) => viewer.show(next).then(() => handle),
			// the files of the current drawing that `get` didn't give
			get missing() { return viewer.files.missing.slice(); },
			// files that came but couldn't be read: [{file, error}]
			get errors() { return viewer.files.errors.slice(); },
			debug: {
				project: (p) => viewer.project(p),
				// turn the model to an angle (radians; FACING faces the camera)
				turn: (rad) => {
					viewer.rotation = viewer.controls.rotation = rad;
					viewer.turn();
				},
				material: (type) => {
					const m = viewer.scene && viewer.scene.materials.get(type);
					return m ? { width: m.width, height: m.height, data: m.readPixels() } : null;
				},
				// Idle's state: {playing (AnimationData id, 0 Stand), move,
				// next_at, played, available}; null when idle is off
				idle: () => (viewer.idle ? viewer.idle.state() : null),
				equippedSlots: () => viewer.scene?.visibleAttachments.map((a) => a.slot) || [],
				// live particles and ribbon edges, over every model
				particles: () => {
					const out = { particles: 0, edges: 0, emitters: 0 };
					for (const r of viewer.scene ? all_renderers(viewer.scene) : []) {
						if (!r.particles)
							continue;
						const st = r.particles.stats();
						out.particles += st.particles;
						out.edges += st.edges;
						out.emitters += r.particles.emitters.length + r.particles.ribbons.length;
					}
					return out;
				}
			}
		};
		return handle;
	};
})();
