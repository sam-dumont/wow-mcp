/*!
	wow.export (https://github.com/Kruithne/wow.export)
	Authors: Kruithne <kruithne@gmail.com>
	License: MIT (LICENSE-wow.export.txt)
	Ported for wow-stats from 3D/renderers/M2RendererGL.js. Files come from
	the caller's `fetch(fileId) -> Promise<M3D.Reader|null>`; no Vue state,
	no texture ribbon, no wireframe, no baked geometry export. Animations are
	picked by AnimationData id (playAnimationById). Submesh colours start at
	1 (wow.export leaves them at 0 until an animation plays, which hides
	coloured submeshes of a model that has none).
 */
'use strict';

(() => {
	const M3D = globalThis.M3D = globalThis.M3D || {};
	const {
		IDENTITY_MAT4, find_keyframe, mat4_multiply, mat4_from_translation, mat4_from_quat,
		mat4_from_scale, mat4_copy, lerp, quat_slerp
	} = M3D.math;
	const { GLContext, GLTexture, ShaderProgram, VertexArray, create_bones_ubo, ShaderMapper } = M3D;

	const VERTEX_SHADER_IDS = {
		'Diffuse_T1': 0,
		'Diffuse_Env': 1,
		'Diffuse_T1_T2': 2,
		'Diffuse_T1_Env': 3,
		'Diffuse_Env_T1': 4,
		'Diffuse_Env_Env': 5,
		'Diffuse_T1_Env_T1': 6,
		'Diffuse_T1_T1': 7,
		'Diffuse_T1_T1_T1': 8,
		'Diffuse_EdgeFade_T1': 9,
		'Diffuse_T2': 10,
		'Diffuse_T1_Env_T2': 11,
		'Diffuse_EdgeFade_T1_T2': 12,
		'Diffuse_EdgeFade_Env': 13,
		'Diffuse_T1_T2_T1': 14,
		'Diffuse_T1_T2_T3': 15,
		'Color_T1_T2_T3': 16,
		'BW_Diffuse_T1': 17,
		'BW_Diffuse_T1_T2': 18
	};

	// must match MAX_BONES in the m2 vertex shader
	const MAX_BONES = 256;

	const PIXEL_SHADER_IDS = {
		'Combiners_Opaque': 0,
		'Combiners_Mod': 1,
		'Combiners_Opaque_Mod': 2,
		'Combiners_Opaque_Mod2x': 3,
		'Combiners_Opaque_Mod2xNA': 4,
		'Combiners_Opaque_Opaque': 5,
		'Combiners_Mod_Mod': 6,
		'Combiners_Mod_Mod2x': 7,
		'Combiners_Mod_Add': 8,
		'Combiners_Mod_Mod2xNA': 9,
		'Combiners_Mod_AddNA': 10,
		'Combiners_Mod_Opaque': 11,
		'Combiners_Opaque_Mod2xNA_Alpha': 12,
		'Combiners_Opaque_AddAlpha': 13,
		'Combiners_Opaque_AddAlpha_Alpha': 14,
		'Combiners_Opaque_Mod2xNA_Alpha_Add': 15,
		'Combiners_Mod_AddAlpha': 16,
		'Combiners_Mod_AddAlpha_Alpha': 17,
		'Combiners_Opaque_Alpha_Alpha': 18,
		'Combiners_Opaque_Mod2xNA_Alpha_3s': 19,
		'Combiners_Opaque_AddAlpha_Wgt': 20,
		'Combiners_Mod_Add_Alpha': 21,
		'Combiners_Opaque_ModNA_Alpha': 22,
		'Combiners_Mod_AddAlpha_Wgt': 23,
		'Combiners_Opaque_Mod_Add_Wgt': 24,
		'Combiners_Opaque_Mod2xNA_Alpha_UnshAlpha': 25,
		'Combiners_Mod_Dual_Crossfade': 26,
		'Combiners_Opaque_Mod2xNA_Alpha_Alpha': 27,
		'Combiners_Mod_Masked_Dual_Crossfade': 28,
		'Combiners_Opaque_Alpha': 29,
		'Guild': 30,
		'Guild_NoBorder': 31,
		'Guild_Opaque': 32,
		'Combiners_Mod_Depth': 33,
		'Illum': 34,
		'Combiners_Mod_Mod_Mod_Const': 35,
		'Combiners_Mod_Mod_Depth': 36
	};

	const M2BLEND_TO_EGX = [
		GLContext.BlendMode.OPAQUE,
		GLContext.BlendMode.ALPHA_KEY,
		GLContext.BlendMode.ALPHA,
		GLContext.BlendMode.NO_ALPHA_ADD,
		GLContext.BlendMode.ADD,
		GLContext.BlendMode.MOD,
		GLContext.BlendMode.MOD2X,
		GLContext.BlendMode.BLEND_ADD
	];

	// One program per GL context, shared by every model drawn in it.
	function m2_shader(ctx) {
		if (!ctx._m2_shader)
			ctx._m2_shader = new ShaderProgram(ctx, M3D.shaders.m2.vert, M3D.shaders.m2.frag);
		return ctx._m2_shader;
	}

	class M2RendererGL {
		/**
		 * @param {M3D.Reader} data the .m2 file
		 * @param {M3D.GLContext} gl_context
		 * @param {function(number): Promise<M3D.Reader|null>} fetch
		 */
		constructor(data, gl_context, fetch, opts = {}) {
			this.data = data;
			// particles and ribbons (M3D.ParticleSystem), seeded for repeatable pictures
			this.seed = Number(opts.seed) >>> 0 || 1;
			this.particles = null;
			this.ctx = gl_context;
			this.gl = gl_context.gl;
			this.fetch = fetch;

			this.m2 = null;
			this.skelLoader = null;
			this.childSkelLoader = null;

			this.vaos = [];
			this.ubos = [];
			this.textures = new Map();
			this.default_texture = null;
			this.draw_calls = [];
			this.indices_data = null;

			this.bones = null;
			this.bone_matrices = null;
			this.current_animation = null;
			this.current_anim_source = null;
			this.current_anim_index = null;
			this.animation_time = 0;
			this.animation_paused = false;
			this.tex_matrices = null;
			this.submesh_colors = new Float32Array();
			this.tex_weights = new Float32Array();

			this._scratch_local = new Float32Array(16);
			this._scratch_trans = new Float32Array(16);
			this._scratch_rot = new Float32Array(16);
			this._scratch_scale = new Float32Array(16);
			this._scratch_pivot = new Float32Array(16);
			this._scratch_neg_pivot = new Float32Array(16);
			this._scratch_result = new Float32Array(16);
			this._scratch_calculated = new Uint8Array(MAX_BONES);

			this.global_seq_times = new Float32Array();

			// hand grip for held weapons: finger bones use HandsClosed (id 15)
			this.close_right_hand = false;
			this.close_left_hand = false;
			this.hands_closed_anim_idx = null;

			// collection / skinned models: the character's bones
			this.bone_remap_table = null;
			this.use_external_bones = false;

			this.model_matrix = new Float32Array(IDENTITY_MAT4);
			this.position = [0, 0, 0];
			this.rotation = [0, 0, 0];
			this.scale = [1, 1, 1];
		}

		/**
		 * @param {Set<number>} [skip_types] texture types the caller binds
		 *   itself (replaceable textures, composited materials): their TXID
		 *   file, if any, is not fetched.
		 */
		async load(skip_types = new Set()) {
			this.m2 = new M3D.M2Loader(this.data);
			this.m2.load();

			this.shader = m2_shader(this.ctx);

			this._create_tex_matrices();
			this._create_default_texture();

			await this._load_textures(skip_types);
			this.global_seq_times = new Float32Array(this.m2.globalLoops.length);
			this.submesh_colors = new Float32Array(this.m2.colors.length * 4).fill(1);
			this.tex_weights = new Float32Array(this.m2.textureWeights.length).fill(1);

			if (this.m2.vertices.length > 0) {
				await this.loadSkin(0);
			} else {
				// a glow model can be emitters alone: no mesh, but bones to follow
				await this._create_skeleton();
				this._create_bones_ubo();
			}

			if ((this.m2.particles.length || this.m2.ribbons.length) && M3D.ParticleSystem)
				this.particles = new M3D.ParticleSystem(this, this.seed);

			this.data = undefined;
		}

		_create_default_texture() {
			const pixels = new Uint8Array([87, 175, 226, 255]); // 0x57afe2 blue
			this.default_texture = new GLTexture(this.ctx);
			this.default_texture.set_rgba(pixels, 1, 1, { has_alpha: false });
		}

		async _load_textures(skip_types) {
			const textures = this.m2.textures;
			await Promise.all(textures.map(async (texture, i) => {
				if (!(texture.fileDataID > 0) || skip_types.has(this.m2.textureTypes[i]))
					return;
				await this._set_texture_file(i, texture.fileDataID);
			}));
		}

		async _set_texture_file(i, fileDataID) {
			const data = await this.fetch(fileDataID);
			if (!data)
				return false;
			const blp = new M3D.BLPImage(data);
			const gl_tex = new GLTexture(this.ctx);
			gl_tex.set_blp(blp, { flags: this.m2.textures[i].flags });
			const old = this.textures.get(i);
			if (old)
				old.dispose();
			this.textures.set(i, gl_tex);
			return true;
		}

		async loadSkin(index) {
			this._dispose_skin();

			const m2 = this.m2;
			const skin = m2.skins[index];
			if (!skin)
				throw new Error('model has no skin ' + index);
			const skin_data = await this.fetch(skin.fileDataID);
			if (!skin_data)
				throw new M3D.MissingFile(skin.fileDataID);
			skin.load(skin_data);
			const gl = this.gl;

			await this._create_skeleton();

			// position(3f) + normal(3f) + bone_idx(4ub) + bone_weight(4ub) + uv(2f) + uv2(2f) = 48 bytes
			const vertex_count = m2.vertices.length / 3;
			const stride = 48;
			const vertex_data = new ArrayBuffer(vertex_count * stride);
			const vertex_view = new DataView(vertex_data);

			for (let i = 0; i < vertex_count; i++) {
				const offset = i * stride;
				const v_idx = i * 3;
				const uv_idx = i * 2;
				const bone_idx = i * 4;

				vertex_view.setFloat32(offset + 0, m2.vertices[v_idx], true);
				vertex_view.setFloat32(offset + 4, m2.vertices[v_idx + 1], true);
				vertex_view.setFloat32(offset + 8, m2.vertices[v_idx + 2], true);

				vertex_view.setFloat32(offset + 12, m2.normals[v_idx], true);
				vertex_view.setFloat32(offset + 16, m2.normals[v_idx + 1], true);
				vertex_view.setFloat32(offset + 20, m2.normals[v_idx + 2], true);

				vertex_view.setUint8(offset + 24, m2.boneIndices[bone_idx]);
				vertex_view.setUint8(offset + 25, m2.boneIndices[bone_idx + 1]);
				vertex_view.setUint8(offset + 26, m2.boneIndices[bone_idx + 2]);
				vertex_view.setUint8(offset + 27, m2.boneIndices[bone_idx + 3]);

				vertex_view.setUint8(offset + 28, m2.boneWeights[bone_idx]);
				vertex_view.setUint8(offset + 29, m2.boneWeights[bone_idx + 1]);
				vertex_view.setUint8(offset + 30, m2.boneWeights[bone_idx + 2]);
				vertex_view.setUint8(offset + 31, m2.boneWeights[bone_idx + 3]);

				vertex_view.setFloat32(offset + 32, m2.uv[uv_idx], true);
				vertex_view.setFloat32(offset + 36, m2.uv[uv_idx + 1], true);

				// texcoord2 as stored, like texcoord: the textures are uploaded top row
				// first. wow.export flipped it, unseen while its shader dropped the
				// specular; an updated model's eye adds its second texture through it
				// and came out white (tests/test_m3d_viewer_browser.py).
				vertex_view.setFloat32(offset + 40, m2.uv2[uv_idx], true);
				vertex_view.setFloat32(offset + 44, m2.uv2[uv_idx + 1], true);
			}

			const index_data = new Uint16Array(skin.triangles.length);
			for (let i = 0; i < skin.triangles.length; i++) {
				const v = skin.indices[skin.triangles[i]];
				if (v === undefined || v >= vertex_count)
					throw new RangeError('skin index out of range');
				index_data[i] = v;
			}

			this.indices_data = index_data;
			this.skin = skin;

			const vao = new VertexArray(this.ctx);
			vao.bind();

			const vbo = gl.createBuffer();
			gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
			gl.bufferData(gl.ARRAY_BUFFER, vertex_data, gl.STATIC_DRAW);
			vao.vbo = vbo;

			const ebo = gl.createBuffer();
			gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ebo);
			gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, index_data, gl.STATIC_DRAW);
			vao.ebo = ebo;

			vao.setup_m2_vertex_format();
			this.ctx.bind_vao(null);

			this.vaos.push(vao);

			this._create_bones_ubo();

			this.draw_calls = [];

			for (let i = 0; i < skin.subMeshes.length; i++) {
				const submesh = skin.subMeshes[i];
				const tex_unit = skin.textureUnits.find(tu => tu.skinSectionIndex === i);

				const tex_indices = [null, null, null, null];
				let vertex_shader = 0;
				let pixel_shader = 0;
				let blend_mode = 0;
				let flags = 0;
				let texture_count = 1;
				const tex_mtx_idxs = [-1, -1];
				let prio = 0;
				let layer = 0;
				let coloridx = -1;
				let tex_weight_idx = -1;

				if (tex_unit) {
					texture_count = tex_unit.textureCount;
					prio = tex_unit.priority;
					layer = tex_unit.materialLayer;
					if (tex_unit.colorIndex < m2.colors.length)
						coloridx = tex_unit.colorIndex;

					for (let j = 0; j < Math.min(texture_count, 4); j++) {
						const combo_idx = tex_unit.textureComboIndex + j;
						if (combo_idx < m2.textureCombos.length)
							tex_indices[j] = m2.textureCombos[combo_idx];
					}

					const vertex_shader_name = ShaderMapper.getVertexShader(tex_unit.textureCount, tex_unit.shaderID);
					vertex_shader = VERTEX_SHADER_IDS[vertex_shader_name] ?? 0;

					const pixel_shader_name = ShaderMapper.getPixelShader(tex_unit.textureCount, tex_unit.shaderID);
					pixel_shader = PIXEL_SHADER_IDS[pixel_shader_name] ?? 0;

					const mat = m2.materials[tex_unit.materialIndex];
					if (mat) {
						if (M2BLEND_TO_EGX.length > mat.blendingMode)
							blend_mode = M2BLEND_TO_EGX[mat.blendingMode];
						else
							blend_mode = mat.blendingMode;
						flags = mat.flags;
					}

					if (tex_unit.textureTransformComboIndex < m2.textureTransformsLookup.length) {
						const idx = m2.textureTransformsLookup[tex_unit.textureTransformComboIndex];
						if (idx < m2.textureTransforms.length)
							tex_mtx_idxs[0] = idx;
					}
					if (tex_unit.textureTransformComboIndex + 1 < m2.textureTransformsLookup.length) {
						const idx = m2.textureTransformsLookup[tex_unit.textureTransformComboIndex + 1];
						if (idx < m2.textureTransforms.length)
							tex_mtx_idxs[1] = idx;
					}

					if (tex_unit.textureWeightComboIndex < m2.transparencyLookup.length) {
						const idx = m2.transparencyLookup[tex_unit.textureWeightComboIndex];
						if (idx < m2.textureWeights.length)
							tex_weight_idx = idx;
					}
				}

				this.draw_calls.push({
					vao: vao,
					start: submesh.triangleStart,
					count: submesh.triangleCount,
					geoset: submesh.submeshID,
					tex_indices: tex_indices,
					texture_count: texture_count,
					vertex_shader: vertex_shader,
					pixel_shader: pixel_shader,
					blend_mode: blend_mode,
					flags: flags,
					visible: true,
					tex_matrix_idxs: tex_mtx_idxs,
					prio: prio,
					layer: layer,
					color_idx: coloridx,
					tex_weight_idx: tex_weight_idx
				});
			}
		}

		async _create_skeleton() {
			const m2 = this.m2;
			let bone_data = m2.bones;

			if (m2.skeletonFileID) {
				const skel_file = await this.fetch(m2.skeletonFileID);
				if (skel_file) {
					const skel = new M3D.SKELLoader(skel_file);
					skel.load();

					if (skel.parent_skel_file_id > 0) {
						const parent_file = await this.fetch(skel.parent_skel_file_id);
						if (parent_file) {
							const parent_skel = new M3D.SKELLoader(parent_file);
							parent_skel.load();

							// the child is authoritative for the animations it has,
							// the parent for the rest (and for the bone hierarchy)
							this.childSkelLoader = skel;
							this.skelLoader = parent_skel;
							bone_data = parent_skel.bones;
						} else {
							this.skelLoader = skel;
							bone_data = skel.bones;
						}
					} else {
						this.skelLoader = skel;
						bone_data = skel.bones;
					}
				}
			}

			if (!bone_data || bone_data.length === 0) {
				this.bones = null;
				return;
			}

			this.bones = bone_data;

			const anim_source = this.skelLoader || this.m2;
			if (anim_source.animations) {
				for (let i = 0; i < anim_source.animations.length; i++) {
					if (anim_source.animations[i].id === 15) {
						this.hands_closed_anim_idx = i;
						break;
					}
				}
			}
		}

		_create_bones_ubo() {
			const bone_count = this.bones ? this.bones.length : 0;
			this.bone_matrices = create_bones_ubo(this.shader, this.gl, this.ctx, this.ubos, bone_count);
		}

		_create_tex_matrices() {
			const tt = this.m2.textureTransforms;
			if (tt.length <= 0)
				return;

			this.tex_matrices = new Float32Array(tt.length * 16);
			for (let i = 0; i < tt.length; i++)
				this.tex_matrices.set(IDENTITY_MAT4, i * 16);
		}

		// The index of an AnimationData id in this model's (or skeleton's)
		// sequences, its first variation; -1 when it has none.
		find_animation(anim_id) {
			const anims = (this.skelLoader || this.m2).animations || [];
			let idx = anims.findIndex(a => a.id === anim_id && a.variationIndex === 0);
			if (idx < 0)
				idx = anims.findIndex(a => a.id === anim_id);
			return idx;
		}

		// Every sequence of an AnimationData id (its variations), in order.
		animation_indices(anim_id) {
			const anims = (this.skelLoader || this.m2).animations || [];
			const out = [];
			anims.forEach((a, i) => {
				if (a.id === anim_id)
					out.push(i);
			});
			return out;
		}

		// A sequence's length in seconds (0 when it has none).
		animation_duration(index) {
			const a = ((this.skelLoader || this.m2).animations || [])[index];
			return a ? a.duration / 1000 : 0;
		}

		// Whether a sequence can be played: its data is in the model (or
		// skeleton) itself, or its .anim file comes through `fetch` (the
		// caller's, quiet about a file that doesn't come: the shipper sends
		// the .anim files of Stand and HandsClosed only). A loaded .anim is
		// kept, so playAnimation finds it.
		async prepareAnimation(index, fetch) {
			let source = this.skelLoader || this.m2;
			const a = (source.animations || [])[index];
			if (!a)
				return false;
			let source_index = index;
			if (this.childSkelLoader) {
				const child = (this.childSkelLoader.animations || []).findIndex(
					(c) => c.id === a.id && c.variationIndex === a.variationIndex);
				if (child >= 0) {
					source = this.childSkelLoader;
					source_index = child;
				}
			}
			const list = source.animations || [];
			let r = list[source_index];
			let guard = 0;
			while (r && (r.flags & 0x40) === 0x40 && guard++ < list.length)
				r = list[r.aliasNext];
			if (!r)
				return false;
			if ((r.flags & 0x20) === 0x20)
				return true;
			if (!source.loadAnimsForIndex || !source.animFileIDs)
				return false;
			if (source.animFiles && source.animFiles.has(source_index))
				return true;
			return !!(await source.loadAnimsForIndex(source_index, fetch));
		}

		// Plays an AnimationData id (falls back to Stand, 0). Resolves to
		// false when the model has neither.
		async playAnimationById(anim_id) {
			let idx = this.find_animation(anim_id);
			if (idx < 0 && anim_id !== 0)
				idx = this.find_animation(0);
			if (idx < 0)
				return false;
			await this.playAnimation(idx);
			return true;
		}

		async playAnimation(index) {
			let anim_source = this.skelLoader || this.m2;
			let anim_index = index;

			if (this.childSkelLoader && anim_source.animations?.[index]) {
				const anim = anim_source.animations[index];
				const child_anims = this.childSkelLoader.animations;
				if (child_anims) {
					for (let i = 0; i < child_anims.length; i++) {
						if (child_anims[i].id === anim.id && child_anims[i].variationIndex === anim.variationIndex) {
							anim_source = this.childSkelLoader;
							anim_index = i;
							break;
						}
					}
				}
			}

			if (anim_source.loadAnimsForIndex)
				await anim_source.loadAnimsForIndex(anim_index, this.fetch);

			this.current_anim_source = anim_source;
			this.current_anim_index = anim_index;
			this.current_animation = index;
			this.animation_time = 0;
			this.global_seq_times = new Float32Array((anim_source.globalLoops || []).length);
			this.updateAnimation(0);
		}

		// The HandsClosed .anim, for finger bones while a hand holds something.
		async loadHandsClosed() {
			const src = this.skelLoader || this.m2;
			if (this.hands_closed_anim_idx !== null && src.loadAnimsForIndex)
				await src.loadAnimsForIndex(this.hands_closed_anim_idx, this.fetch);
		}

		/**
		 * @param {number} delta_time seconds
		 */
		updateAnimation(delta_time) {
			this._update_animation(delta_time);
			// after the bones: emitters follow them
			if (this.particles)
				this.particles.update(delta_time);
		}

		// Particles and ribbons, after every mesh of the frame.
		renderParticles(view_matrix, projection_matrix) {
			if (this.particles)
				this.particles.render(view_matrix, projection_matrix);
		}

		_update_animation(delta_time) {
			if (this.current_animation === null || !this.bones)
				return;

			const anim_source = this.current_anim_source || this.skelLoader || this.m2;
			const anim_idx = this.current_anim_index ?? this.current_animation;
			const anim = anim_source.animations?.[anim_idx];
			if (!anim)
				return;

			if (!this.animation_paused) {
				this.animation_time += delta_time;

				for (let i = 0; i < this.global_seq_times.length; ++i) {
					this.global_seq_times[i] += (delta_time * 1000);
					const ts = anim_source.globalLoops[i];
					if (ts > 0)
						this.global_seq_times[i] %= ts;
				}
			}

			const duration_sec = anim.duration / 1000;
			if (duration_sec > 0)
				this.animation_time = this.animation_time % duration_sec;

			this._update_bone_matrices();
			this._update_tex_matrices();
			this._update_submesh_colors();
			this._update_tex_weights();
		}

		_update_bone_matrices() {
			const time_ms = this.animation_time * 1000;

			// animation data may come from the child skeleton, the hierarchy
			// and pivots from the parent
			const anim_bones = this.current_anim_source?.bones || this.bones;
			const bones = this.bones;
			const bone_count = Math.min(bones.length, this.bone_matrices.length / 16, MAX_BONES);
			const anim_idx = this.current_anim_index ?? this.current_animation;

			const hands_closed_idx = this.hands_closed_anim_idx;
			const close_r = this.close_right_hand && hands_closed_idx !== null;
			const close_l = this.close_left_hand && hands_closed_idx !== null;

			const local_mat = this._scratch_local;
			const trans_mat = this._scratch_trans;
			const rot_mat = this._scratch_rot;
			const scale_mat = this._scratch_scale;
			const pivot_mat = this._scratch_pivot;
			const neg_pivot_mat = this._scratch_neg_pivot;
			const temp_result = this._scratch_result;

			const calculated = this._scratch_calculated;
			calculated.fill(0);

			// T(pivot) * T(anim) * R(anim) * S(anim) * T(-pivot), parent first
			const calc_bone = (idx, depth) => {
				if (calculated[idx])
					return;

				const bone = bones[idx];
				const anim_bone = anim_bones[idx];
				const parent_idx = bone.parentBone;

				if (parent_idx >= 0 && parent_idx < bone_count && parent_idx !== idx && depth < bone_count)
					calc_bone(parent_idx, depth + 1);

				const pivot = bone.pivot;
				const px = pivot[0], py = pivot[1], pz = pivot[2];

				// right finger bone ids 8-12, left 13-17
				const bone_id = bone.boneID;
				const is_right_finger = bone_id >= 8 && bone_id <= 12;
				const is_left_finger = bone_id >= 13 && bone_id <= 17;
				const use_closed_hand = (is_right_finger && close_r) || (is_left_finger && close_l);
				const effective_anim_idx = use_closed_hand ? hands_closed_idx : anim_idx;
				const effective_time_ms = use_closed_hand ? 0 : time_ms;
				const hand_bone = use_closed_hand ? (this.skelLoader || this.m2).bones?.[idx] || anim_bone : anim_bone;

				const has_trans = hand_bone?.translation?.timestamps?.[effective_anim_idx]?.length > 0;
				const has_rot = hand_bone?.rotation?.timestamps?.[effective_anim_idx]?.length > 0;
				const has_scale = hand_bone?.scale?.timestamps?.[effective_anim_idx]?.length > 0;
				const has_scale_fallback = !has_scale && effective_anim_idx !== 0 && hand_bone?.scale?.timestamps?.[0]?.length > 0;
				const has_animation = has_trans || has_rot || has_scale || has_scale_fallback;

				mat4_copy(local_mat, IDENTITY_MAT4);

				if (has_animation) {
					mat4_from_translation(pivot_mat, px, py, pz);
					mat4_multiply(temp_result, local_mat, pivot_mat);
					mat4_copy(local_mat, temp_result);

					if (has_trans) {
						const ts = hand_bone.translation.timestamps[effective_anim_idx];
						const vals = hand_bone.translation.values[effective_anim_idx];
						const [tx, ty, tz] = this._sample_raw_vec3(ts, vals, effective_time_ms);

						mat4_from_translation(trans_mat, tx, ty, tz);
						mat4_multiply(temp_result, local_mat, trans_mat);
						mat4_copy(local_mat, temp_result);
					}

					if (has_rot) {
						const ts = hand_bone.rotation.timestamps[effective_anim_idx];
						const vals = hand_bone.rotation.values[effective_anim_idx];
						const [qx, qy, qz, qw] = this._sample_raw_quat(ts, vals, effective_time_ms);

						mat4_from_quat(rot_mat, qx, qy, qz, qw);
						mat4_multiply(temp_result, local_mat, rot_mat);
						mat4_copy(local_mat, temp_result);
					}

					if (has_scale || has_scale_fallback) {
						const scale_anim_idx = has_scale ? effective_anim_idx : 0;
						const ts = hand_bone.scale.timestamps[scale_anim_idx];
						const vals = hand_bone.scale.values[scale_anim_idx];
						const scale_time = has_scale ? effective_time_ms : 0;
						const [sx, sy, sz] = this._sample_raw_vec3(ts, vals, scale_time, [1, 1, 1]);

						mat4_from_scale(scale_mat, sx, sy, sz);
						mat4_multiply(temp_result, local_mat, scale_mat);
						mat4_copy(local_mat, temp_result);
					}

					mat4_from_translation(neg_pivot_mat, -px, -py, -pz);
					mat4_multiply(temp_result, local_mat, neg_pivot_mat);
					mat4_copy(local_mat, temp_result);
				}

				const offset = idx * 16;
				if (parent_idx >= 0 && parent_idx < bone_count && parent_idx !== idx) {
					const parent_offset = parent_idx * 16;
					const parent_mat = this.bone_matrices.subarray(parent_offset, parent_offset + 16);
					mat4_multiply(this.bone_matrices.subarray(offset, offset + 16), parent_mat, local_mat);
				} else {
					this.bone_matrices.set(local_mat, offset);
				}

				calculated[idx] = 1;
			};

			for (let i = 0; i < bone_count; i++)
				calc_bone(i, 0);
		}

		_find_time_index(currtime, times) {
			if (times.length > 1) {
				if (currtime > times[times.length - 1])
					return times.length - 1;
				return find_keyframe(times, currtime);
			} else if (times.length === 1) {
				return 0;
			}
			return -1;
		}

		_animate_track(anim, animblock, def, lerpfunc) {
			const m2 = this.m2;
			const gl = m2.globalLoops;
			let at = (this.animation_time * 1000);
			let ai = this.current_anim_index;
			let maxtime = anim.duration;

			const gs = animblock.globalSeq;
			if (gs < this.global_seq_times.length) {
				at = this.global_seq_times[gs];
				maxtime = gl[gs];
			}

			if (animblock.timestamps.length === 0)
				return def;

			if (animblock.timestamps.length <= ai)
				ai = 0;

			if (!animblock.timestamps[ai] || animblock.timestamps[ai].length === 0)
				return def;

			const times = animblock.timestamps[ai];
			const values = animblock.values[ai];
			const intertype = animblock.interpolation;

			let ti = 0;
			if (maxtime !== 0)
				ti = this._find_time_index(at, times);

			if (ti === times.length - 1)
				return values[ti];
			else if (ti >= 0) {
				const v1 = values[ti];
				const v2 = values[ti + 1];
				const t1 = times[ti];
				const t2 = times[ti + 1];

				const dt = t2 - t1;
				if (intertype === 0 || dt <= 0)
					return v1;
				return lerpfunc(v1, v2, Math.min((at - t1) / dt, 1));
			}
			return values[0];
		}

		_current_anim() {
			const anim_source = this.current_anim_source || this.skelLoader || this.m2;
			const anim_idx = this.current_anim_index ?? this.current_animation;
			return anim_source.animations?.[anim_idx];
		}

		_update_tex_matrices() {
			const anim = this._current_anim();
			if (!anim || !this.tex_matrices)
				return;

			const m2 = this.m2;
			const tm = this.tex_matrices;
			const temp_result = this._scratch_result;
			const local_mat = this._scratch_local;
			const lerp4 = (a, b, c) => [lerp(a[0], b[0], c), lerp(a[1], b[1], c), lerp(a[2], b[2], c), lerp(a[3], b[3], c)];

			for (let i = 0; i < m2.textureTransforms.length; ++i) {
				const tt = m2.textureTransforms[i];
				mat4_copy(local_mat, IDENTITY_MAT4);

				const transmat = [0.5, 0.5, 0, 0];
				const pivotpoint = [-0.5, -0.5, 0, 0];

				if (tt.rotation.values.length) {
					const [qx, qy, qz, qw] = this._animate_track(anim, tt.rotation, [1, 0, 0, 0], (a, b, c) => {
						const out = [0, 0, 0, 1];
						quat_slerp(out, a[0], a[1], a[2], a[3], b[0], b[1], b[2], b[3], c);
						return out;
					});
					mat4_from_translation(temp_result, transmat[0], transmat[1], transmat[2]);
					mat4_multiply(local_mat, local_mat, temp_result);
					mat4_from_quat(temp_result, qx, qy, qz, qw);
					mat4_multiply(local_mat, local_mat, temp_result);
					mat4_from_translation(temp_result, pivotpoint[0], pivotpoint[1], pivotpoint[2]);
					mat4_multiply(local_mat, local_mat, temp_result);
				}
				if (tt.scaling.values.length) {
					const [sx, sy, sz] = this._animate_track(anim, tt.scaling, [1, 1, 1, 1], lerp4);
					mat4_from_translation(temp_result, transmat[0], transmat[1], transmat[2]);
					mat4_multiply(local_mat, local_mat, temp_result);
					mat4_from_scale(temp_result, sx, sy, sz);
					mat4_multiply(local_mat, local_mat, temp_result);
					mat4_from_translation(temp_result, pivotpoint[0], pivotpoint[1], pivotpoint[2]);
					mat4_multiply(local_mat, local_mat, temp_result);
				}
				if (tt.translation.values.length) {
					const [tx, ty, tz] = this._animate_track(anim, tt.translation, [0, 0, 0, 0], lerp4);
					mat4_from_translation(temp_result, tx, ty, tz);
					mat4_multiply(local_mat, local_mat, temp_result);
				}

				tm.set(local_mat, i * 16);
			}
		}

		_update_submesh_colors() {
			const anim = this._current_anim();
			if (!anim)
				return;

			const m2 = this.m2;
			for (let i = 0; i < m2.colors.length; ++i) {
				const rgb = this._animate_track(anim, m2.colors[i].color, [1, 1, 1], (a, b, c) => [
					lerp(a[0], b[0], c), lerp(a[1], b[1], c), lerp(a[2], b[2], c)
				]);

				this.submesh_colors[(i * 4) + 0] = rgb[0];
				this.submesh_colors[(i * 4) + 1] = rgb[1];
				this.submesh_colors[(i * 4) + 2] = rgb[2];

				const a = this._animate_track(anim, m2.colors[i].alpha, 32767, (x, y, c) => lerp(x, y, c));
				this.submesh_colors[(i * 4) + 3] = a / 32768;
			}
		}

		_update_tex_weights() {
			const anim = this._current_anim();
			if (!anim)
				return;

			const m2 = this.m2;
			for (let i = 0; i < m2.textureWeights.length; ++i) {
				const w = this._animate_track(anim, m2.textureWeights[i], 32767, (a, b, c) => lerp(a, b, c));
				this.tex_weights[i] = w / 32768;
			}
		}

		_sample_raw_vec3(timestamps, values, time_ms, default_value = [0, 0, 0]) {
			if (!timestamps || timestamps.length === 0 || !values || values.length === 0)
				return default_value;

			if (timestamps.length === 1 || time_ms <= timestamps[0]) {
				const v = values[0];
				return [v[0], v[1], v[2]];
			}

			if (time_ms >= timestamps[timestamps.length - 1]) {
				const v = values[values.length - 1];
				return [v[0], v[1], v[2]];
			}

			const frame = find_keyframe(timestamps, time_ms);
			const t0 = timestamps[frame];
			const t1 = timestamps[frame + 1];
			const dt = t1 - t0;
			const alpha = dt > 0 ? Math.min((time_ms - t0) / dt, 1) : 0;

			const v0 = values[frame];
			const v1 = values[frame + 1] || v0;

			return [lerp(v0[0], v1[0], alpha), lerp(v0[1], v1[1], alpha), lerp(v0[2], v1[2], alpha)];
		}

		_sample_raw_quat(timestamps, values, time_ms) {
			if (!timestamps || timestamps.length === 0 || !values || values.length === 0)
				return [0, 0, 0, 1];

			if (timestamps.length === 1 || time_ms <= timestamps[0]) {
				const v = values[0];
				return [v[0], v[1], v[2], v[3]];
			}

			if (time_ms >= timestamps[timestamps.length - 1]) {
				const v = values[values.length - 1];
				return [v[0], v[1], v[2], v[3]];
			}

			const frame = find_keyframe(timestamps, time_ms);
			const t0 = timestamps[frame];
			const t1 = timestamps[frame + 1];
			const dt = t1 - t0;
			const alpha = dt > 0 ? Math.min((time_ms - t0) / dt, 1) : 0;

			const q0 = values[frame];
			const q1 = values[frame + 1] || q0;

			const out = [0, 0, 0, 1];
			quat_slerp(out, q0[0], q0[1], q0[2], q0[3], q1[0], q1[1], q1[2], q1[3], alpha);
			return out;
		}

		// Bone correspondence by pivot position and boneNameCRC (like
		// wowmodelviewer), then by pivot alone; unmatched bones keep their index.
		buildBoneRemapTable(char_bones) {
			if (!this.bones || !char_bones) {
				this.bone_remap_table = null;
				return;
			}

			const epsilon = 0.0001;
			const remap = new Int16Array(this.bones.length);
			const near = (a, b) => Math.abs(a[0] - b[0]) < epsilon && Math.abs(a[1] - b[1]) < epsilon && Math.abs(a[2] - b[2]) < epsilon;

			for (let i = 0; i < this.bones.length; i++) {
				const coll_bone = this.bones[i];
				let found = char_bones.findIndex(cb => near(coll_bone.pivot, cb.pivot) && coll_bone.boneNameCRC === cb.boneNameCRC);
				if (found < 0)
					found = char_bones.findIndex(cb => near(coll_bone.pivot, cb.pivot));
				remap[i] = found >= 0 ? found : i;
			}

			this.bone_remap_table = remap;
			this.use_external_bones = true;
		}

		applyExternalBoneMatrices(char_bone_matrices) {
			if (!this.bone_remap_table || !this.bone_matrices || !char_bone_matrices)
				return;

			for (let i = 0; i < this.bone_remap_table.length; i++) {
				const char_offset = this.bone_remap_table[i] * 16;
				if (char_offset + 16 <= char_bone_matrices.length && i * 16 + 16 <= this.bone_matrices.length)
					this.bone_matrices.set(char_bone_matrices.subarray(char_offset, char_offset + 16), i * 16);
			}
		}

		setTransform(position, rotation, scale) {
			this.position = position;
			this.rotation = rotation;
			this.scale = scale;
			this._update_model_matrix();
		}

		setTransformMatrix(matrix) {
			this.model_matrix.set(matrix);
		}

		setHandGrip(close_right, close_left) {
			this.close_right_hand = close_right;
			this.close_left_hand = close_left;
		}

		_update_model_matrix() {
			const m = this.model_matrix;
			const [px, py, pz] = this.position;
			const [rx, ry, rz] = this.rotation;
			const [sx, sy, sz] = this.scale;

			const cx = Math.cos(rx), sinx = Math.sin(rx);
			const cy = Math.cos(ry), siny = Math.sin(ry);
			const cz = Math.cos(rz), sinz = Math.sin(rz);

			m[0] = cy * cz * sx;
			m[1] = cy * sinz * sx;
			m[2] = -siny * sx;
			m[3] = 0;

			m[4] = (sinx * siny * cz - cx * sinz) * sy;
			m[5] = (sinx * siny * sinz + cx * cz) * sy;
			m[6] = sinx * cy * sy;
			m[7] = 0;

			m[8] = (cx * siny * cz + sinx * sinz) * sz;
			m[9] = (cx * siny * sinz - sinx * cz) * sz;
			m[10] = cx * cy * sz;
			m[11] = 0;

			m[12] = px;
			m[13] = py;
			m[14] = pz;
			m[15] = 1;
		}

		render(view_matrix, projection_matrix) {
			if (!this.shader || this.draw_calls.length === 0)
				return;

			const gl = this.gl;
			const ctx = this.ctx;
			const shader = this.shader;

			shader.use();

			shader.set_uniform_mat4('u_view_matrix', false, view_matrix);
			shader.set_uniform_mat4('u_projection_matrix', false, projection_matrix);
			shader.set_uniform_mat4('u_model_matrix', false, this.model_matrix);
			shader.set_uniform_3f('u_view_up', 0, 1, 0);
			shader.set_uniform_1f('u_time', performance.now() * 0.001);

			const ubo = this.ubos[0];
			const bone_count = this.bones ? Math.min(this.bones.length, this.bone_matrices.length / 16) : 0;
			shader.set_uniform_1i('u_bone_count', bone_count);
			if (bone_count)
				ubo.ubo.upload_range(ubo.offsets[0], bone_count * 16 * 4);

			// light direction in view space: from the front, above and the picture's right, as Blizzard's
			// character renders light them (tools/3dcases, 2026-11-20: weapons 58 -> 22 colour distance,
			// the body's regions 16 -> 8). wow.export's light came from the left, almost level, and left a
			// blade turned that way with the ambient alone, half as bright.
			const lx = -2, ly = -1, lz = -2;
			const light_view_x = view_matrix[0] * lx + view_matrix[4] * ly + view_matrix[8] * lz;
			const light_view_y = view_matrix[1] * lx + view_matrix[5] * ly + view_matrix[9] * lz;
			const light_view_z = view_matrix[2] * lx + view_matrix[6] * ly + view_matrix[10] * lz;

			shader.set_uniform_3f('u_ambient_color', 0.5, 0.5, 0.5);
			shader.set_uniform_3f('u_diffuse_color', 0.7, 0.7, 0.7);
			shader.set_uniform_3f('u_light_dir', light_view_x, light_view_y, light_view_z);

			shader.set_uniform_1i('u_wireframe', 0);
			shader.set_uniform_4f('u_wireframe_color', 1, 1, 1, 1);
			shader.set_uniform_1f('u_alpha_test', 0.501960814);

			shader.set_uniform_1i('u_texture1', 0);
			shader.set_uniform_1i('u_texture2', 1);
			shader.set_uniform_1i('u_texture3', 2);
			shader.set_uniform_1i('u_texture4', 3);
			shader.set_uniform_3f('u_tex_sample_alpha', 1, 1, 1);

			const sorted_calls = [...this.draw_calls].sort((a, b) => {
				if (a.prio !== b.prio)
					return a.prio - b.prio;
				if (a.layer !== b.layer)
					return a.layer - b.layer;
				if (a.blend_mode !== b.blend_mode)
					return a.blend_mode - b.blend_mode;
				return 0;
			});

			const get_tex_matrix = (idx) => {
				if (this.tex_matrices === null)
					return IDENTITY_MAT4;
				return this.tex_matrices.subarray(idx * 16, (idx + 1) * 16);
			};

			for (const dc of sorted_calls) {
				if (!dc.visible)
					continue;

				shader.set_uniform_1i('u_vertex_shader', dc.vertex_shader);
				shader.set_uniform_1i('u_pixel_shader', dc.pixel_shader);
				shader.set_uniform_1i('u_blend_mode', dc.blend_mode);

				const tmi0 = dc.tex_matrix_idxs[0];
				const tmi1 = dc.tex_matrix_idxs[1];
				shader.set_uniform_mat4('u_tex_matrix1', false, tmi0 === -1 ? IDENTITY_MAT4 : get_tex_matrix(tmi0));
				shader.set_uniform_mat4('u_tex_matrix2', false, tmi1 === -1 ? IDENTITY_MAT4 : get_tex_matrix(tmi1));

				let color = [1, 1, 1, 1];
				if (dc.color_idx !== -1)
					color = this.submesh_colors.subarray(dc.color_idx * 4, (dc.color_idx * 4) + 4);

				let alpha = color[3];
				if (dc.tex_weight_idx !== -1)
					alpha *= this.tex_weights[dc.tex_weight_idx];

				if (alpha <= 0)
					continue;

				shader.set_uniform_4f('u_mesh_color', color[0], color[1], color[2], alpha);
				shader.set_uniform_1i('u_apply_lighting', dc.flags & 0x1 ? 0 : 1);

				ctx.apply_blend_mode(dc.blend_mode);

				if (dc.flags & 0x04) {
					ctx.set_cull_face(false);
				} else {
					ctx.set_cull_face(true);
					ctx.set_cull_mode(gl.BACK);
				}

				ctx.set_depth_test(!(dc.flags & 0x08));
				ctx.set_depth_write(!(dc.flags & 0x10));

				for (let t = 0; t < 4; t++) {
					const tex_idx = dc.tex_indices[t];
					const texture = (tex_idx !== null) ? (this.textures.get(tex_idx) || this.default_texture) : this.default_texture;
					texture.bind(t);
				}

				ubo.ubo.bind(0);
				dc.vao.bind();

				gl.drawElements(gl.TRIANGLES, dc.count, gl.UNSIGNED_SHORT, dc.start * 2);
			}

			ctx.set_blend(false);
			ctx.set_depth_test(true);
			ctx.set_depth_write(true);
			ctx.set_cull_face(false);
		}

		// Binds one texture file to every texture of a type. Resolves to
		// false when the file is missing.
		async overrideTextureType(type, fileDataID) {
			let ok = true;
			const types = this.m2.textureTypes;
			for (let i = 0; i < types.length; i++) {
				if (types[i] === type)
					ok = (await this._set_texture_file(i, fileDataID)) && ok;
			}
			return ok;
		}

		// Binds a composited character material (M3D.CharMaterial) to every
		// texture of a type, with each texture's own wrap flags.
		overrideTextureTypeWithMaterial(type, material) {
			const types = this.m2.textureTypes;
			for (let i = 0; i < types.length; i++) {
				if (types[i] !== type)
					continue;

				const flags = this.m2.textures[i].flags;
				const gl_tex = material.make_texture(
					(flags & 0x1) ? this.gl.REPEAT : this.gl.CLAMP_TO_EDGE,
					(flags & 0x2) ? this.gl.REPEAT : this.gl.CLAMP_TO_EDGE
				);

				const old = this.textures.get(i);
				if (old)
					old.dispose();

				this.textures.set(i, gl_tex);
			}
		}

		// The world transform of one of this model's attachment points.
		getAttachmentTransform(attachmentId) {
			if (!this.m2)
				return null;

			let attachment = this.m2.getAttachmentById(attachmentId);
			if (!attachment && this.skelLoader?.getAttachmentById)
				attachment = this.skelLoader.getAttachmentById(attachmentId);

			if (!attachment)
				return null;

			const bone_idx = attachment.bone;
			let bone_mat = IDENTITY_MAT4;
			if (this.bone_matrices && this.bones && bone_idx < this.bones.length && bone_idx * 16 + 16 <= this.bone_matrices.length)
				bone_mat = this.bone_matrices.subarray(bone_idx * 16, bone_idx * 16 + 16);

			// WoW (X right, Y forward, Z up) to WebGL (X right, Y up, Z -forward)
			const pos = attachment.position;
			const att_mat = new Float32Array([
				1, 0, 0, 0,
				0, 1, 0, 0,
				0, 0, 1, 0,
				pos[0], pos[2], -pos[1], 1
			]);

			const result = new Float32Array(16);
			mat4_multiply(result, bone_mat, att_mat);
			mat4_multiply(result, this.model_matrix, result);
			return result;
		}

		// The box of what is drawn (visible submeshes, bind pose) in WebGL
		// coordinates, or null. A character's own bounds cover every
		// animation (an HD gnome's spans 4.3 by 4.3 yards for a 1-yard
		// model): framed by them, it drew a tenth of the frame's height.
		getDrawnBox() {
			const v = this.m2 && this.m2.vertices, idx = this.indices_data;
			if (!v || !idx || this.draw_calls.length === 0)
				return null;
			const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
			for (const dc of this.draw_calls) {
				if (!dc.visible)
					continue;
				for (let i = dc.start, end = Math.min(dc.start + dc.count, idx.length); i < end; i++) {
					const o = idx[i] * 3;  // the loader's vertices are WebGL's axes already
					for (let k = 0; k < 3; k++) {
						if (v[o + k] < min[k]) min[k] = v[o + k];
						if (v[o + k] > max[k]) max[k] = v[o + k];
					}
				}
			}
			return min[0] <= max[0] ? { min, max } : null;
		}

		// The model's bounding box in WebGL coordinates.
		getBoundingBox() {
			if (!this.m2 || !this.m2.boundingBox)
				return null;

			const src_min = this.m2.boundingBox.min;
			const src_max = this.m2.boundingBox.max;
			return {
				min: [src_min[0], src_min[2], -src_max[1]],
				max: [src_max[0], src_max[2], -src_min[1]]
			};
		}

		_dispose_skin() {
			for (const vao of this.vaos)
				vao.dispose();
			for (const ubo of this.ubos)
				ubo.ubo.dispose();

			this.ubos = [];
			this.vaos = [];
			this.draw_calls = [];
			this.indices_data = null;
		}

		dispose() {
			this._dispose_skin();

			if (this.particles) {
				this.particles.dispose();
				this.particles = null;
			}

			for (const tex of this.textures.values())
				tex.dispose();
			this.textures.clear();

			if (this.default_texture) {
				this.default_texture.dispose();
				this.default_texture = null;
			}
		}
	}

	M3D.M2RendererGL = M2RendererGL;
})();
