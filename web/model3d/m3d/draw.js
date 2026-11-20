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
	          maxPixelRatio: 2; seed: the particles' random seed (1)
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

	async function load_scene(ctx, recipe, files) {
		const r = obj(recipe);
		const scene = { char: null, materials: new Map(), attachments: [], collections: [], skinned: [] };

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
			const visuals = (await Promise.all(list(a.visuals).map(async (v) => {
				const vis = await load_model(ctx, v.model, files, new Set());
				if (!vis)
					return null;
				await vis.playAnimationById(0);
				return { renderer: vis, attach: Number(v.attach) || 0 };
			}))).filter(Boolean);
			const scale = Number(a.scale) > 0 ? Number(a.scale) : 1;
			return { renderer: item, attach: Number(a.attach) || 0, scale, visuals };
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

		const [attachments, collections, skinned] = await Promise.all([
			Promise.all(list(r.attachments).map(attached)),
			Promise.all(list(r.collections).map(with_bones('collection'))),
			Promise.all(list(r.skinned).map(with_bones('skinned')))
		]);
		scene.attachments = attachments.filter(Boolean);
		scene.collections = collections.filter(Boolean);
		scene.skinned = skinned.filter(Boolean);

		// a held weapon closes the hand around it (attachment 1 right hand,
		// 2 left hand; a shield, 0, hangs from the wrist)
		const close_r = scene.attachments.some((a) => a.attach === 1);
		const close_l = scene.attachments.some((a) => a.attach === 2);
		if (close_r || close_l) {
			await char.loadHandsClosed();
			char.setHandGrip(close_r, close_l);
		}

		return scene;
	}

	function all_renderers(scene) {
		const all = [scene.char, ...scene.collections, ...scene.skinned];
		for (const a of scene.attachments) {
			all.push(a.renderer);
			for (const v of a.visuals)
				all.push(v.renderer);
		}
		return all.filter(Boolean);
	}

	function dispose_scene(scene) {
		if (!scene)
			return;
		const all = [scene.char, ...scene.collections, ...scene.skinned];
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

	function parse_background(bg) {
		if (Array.isArray(bg) && bg.length >= 3)
			return [Number(bg[0]) || 0, Number(bg[1]) || 0, Number(bg[2]) || 0, bg.length > 3 ? Number(bg[3]) : 1];
		const m = typeof bg === 'string' && /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(bg);
		if (m)
			return [parseInt(m[1], 16) / 255, parseInt(m[2], 16) / 255, parseInt(m[3], 16) / 255, 1];
		return [0, 0, 0, 0];
	}

	// ------------------------------------------------------------------ the frame

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
		}

		turn() {
			if (this.scene && this.scene.char)
				this.scene.char.setTransform([0, 0, 0], [0, this.rotation, 0], [1, 1, 1]);
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
			dispose_scene(this.scene);
			this.scene = scene;
			this.files = files;
			this.box = scene.char ? frame_box(scene.char) : null;
			this.fitted = '';
			this.turn();
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
				char.updateAnimation(dt);
				char.render(view, proj);

				const scale_mat = new Float32Array(16);
				for (const a of scene.attachments) {
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
						v.renderer.setTransformMatrix(a.renderer.getAttachmentTransform(v.attach) || a.renderer.model_matrix);
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

				// particles and ribbons last, over every mesh
				for (const r of all_renderers(scene))
					r.renderParticles(view, proj);
			}

			this.raf = requestAnimationFrame(() => this.frame());
		}

		start() {
			if (!this.raf && !this.stopped)
				this.raf = requestAnimationFrame(() => this.frame());
		}

		stop() {
			this.stopped = true;
			this.gen++;
			if (this.raf)
				cancelAnimationFrame(this.raf);
			this.raf = 0;
			this.controls.dispose();
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
	 * @returns {Promise<{stop: function(), redraw: function(object): Promise, missing: number[], errors: object[]}>}
	 */
	M3D.draw = async function draw(canvas, recipe, get, opts = {}) {
		const viewer = new Viewer(canvas, get, opts || {});
		await viewer.show(recipe);
		viewer.start();

		const handle = {
			stop: () => viewer.stop(),
			redraw: (next) => viewer.show(next).then(() => handle),
			// the files of the current drawing that `get` didn't give
			get missing() { return viewer.files.missing.slice(); },
			// files that came but couldn't be read: [{file, error}]
			get errors() { return viewer.files.errors.slice(); },
			debug: {
				project: (p) => viewer.project(p),
				material: (type) => {
					const m = viewer.scene && viewer.scene.materials.get(type);
					return m ? { width: m.width, height: m.height, data: m.readPixels() } : null;
				},
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
