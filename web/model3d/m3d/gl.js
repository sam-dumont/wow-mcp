/*!
	wow.export (https://github.com/Kruithne/wow.export)
	Authors: Kruithne <kruithne@gmail.com>
	License: MIT (LICENSE-wow.export.txt)
	Ported for wow-stats from 3D/gl/GLContext.js, GLTexture.js,
	ShaderProgram.js, UniformBuffer.js, VertexArray.js and
	3D/renderers/renderer_utils.js: the parts the M2 renderer uses.
	Textures are decoded on the CPU (no S3TC upload path).
 */
'use strict';

(() => {
	const M3D = globalThis.M3D = globalThis.M3D || {};
	const { IDENTITY_MAT4 } = M3D.math;

	const BlendMode = {
		OPAQUE: 0,
		ALPHA_KEY: 1,
		ALPHA: 2,
		ADD: 3,
		MOD: 4,
		MOD2X: 5,
		MOD_ADD: 6,
		INV_SRC_ALPHA_ADD: 7,
		INV_SRC_ALPHA_OPAQUE: 8,
		SRC_ALPHA_OPAQUE: 9,
		NO_ALPHA_ADD: 10,
		CONSTANT_ALPHA: 11,
		SCREEN: 12,
		BLEND_ADD: 13
	};

	class GLContext {
		constructor(canvas, options = {}) {
			this.canvas = canvas;

			const gl_options = {
				antialias: options.antialias ?? true,
				alpha: options.alpha ?? true,
				premultipliedAlpha: options.premultipliedAlpha ?? true,
				preserveDrawingBuffer: options.preserveDrawingBuffer ?? true,
				powerPreference: options.powerPreference ?? 'default'
			};

			this.gl = canvas.getContext('webgl2', gl_options);
			if (!this.gl)
				throw new Error('WebGL2 not supported');

			this.ext_aniso = this.gl.getExtension('EXT_texture_filter_anisotropic');
			this.max_anisotropy = this.ext_aniso ? this.gl.getParameter(this.ext_aniso.MAX_TEXTURE_MAX_ANISOTROPY_EXT) : 1;

			this.reset_state();
		}

		// The default state, and a state cache that matches it. Called again
		// after anything that sets GL state behind the cache's back (the
		// character material compositor).
		reset_state() {
			const gl = this.gl;

			this._depth_test = true;
			this._depth_write = true;
			this._depth_func = gl.LEQUAL;
			this._cull_face = false;
			this._cull_mode = gl.BACK;
			this._blend = false;
			this._blend_src = gl.ONE;
			this._blend_dst = gl.ZERO;
			this._current_program = null;
			this._current_vao = null;
			this._bound_textures = new Array(16).fill(null);
			this._active_texture_unit = 0;

			gl.bindFramebuffer(gl.FRAMEBUFFER, null);
			gl.bindVertexArray(null);
			gl.useProgram(null);
			gl.activeTexture(gl.TEXTURE0);
			for (let i = 0; i < 4; i++) {
				gl.activeTexture(gl.TEXTURE0 + i);
				gl.bindTexture(gl.TEXTURE_2D, null);
			}
			gl.activeTexture(gl.TEXTURE0);
			gl.enable(gl.DEPTH_TEST);
			gl.depthFunc(gl.LEQUAL);
			gl.depthMask(true);
			gl.disable(gl.CULL_FACE);
			gl.cullFace(gl.BACK);
			gl.disable(gl.BLEND);
			gl.blendFunc(gl.ONE, gl.ZERO);
			gl.clearColor(0, 0, 0, 0);
		}

		set_viewport(width, height) {
			this.gl.viewport(0, 0, width, height);
			this.viewport_width = width;
			this.viewport_height = height;
		}

		clear(color = true, depth = true) {
			const gl = this.gl;
			let bits = 0;
			if (color)
				bits |= gl.COLOR_BUFFER_BIT;
			if (depth)
				bits |= gl.DEPTH_BUFFER_BIT;
			if (bits)
				gl.clear(bits);
		}

		set_clear_color(r, g, b, a = 1) {
			this.gl.clearColor(r, g, b, a);
		}

		set_depth_test(enable) {
			if (this._depth_test === enable)
				return;
			const gl = this.gl;
			if (enable)
				gl.enable(gl.DEPTH_TEST);
			else
				gl.disable(gl.DEPTH_TEST);
			this._depth_test = enable;
		}

		set_depth_write(enable) {
			if (this._depth_write === enable)
				return;
			this.gl.depthMask(enable);
			this._depth_write = enable;
		}

		set_cull_face(enable) {
			if (this._cull_face === enable)
				return;
			const gl = this.gl;
			if (enable)
				gl.enable(gl.CULL_FACE);
			else
				gl.disable(gl.CULL_FACE);
			this._cull_face = enable;
		}

		set_cull_mode(mode) {
			if (this._cull_mode === mode)
				return;
			this.gl.cullFace(mode);
			this._cull_mode = mode;
		}

		set_blend(enable) {
			if (this._blend === enable)
				return;
			const gl = this.gl;
			if (enable)
				gl.enable(gl.BLEND);
			else
				gl.disable(gl.BLEND);
			this._blend = enable;
		}

		set_blend_func(src, dst) {
			if (this._blend_src === src && this._blend_dst === dst)
				return;
			this.gl.blendFunc(src, dst);
			this._blend_src = src;
			this._blend_dst = dst;
		}

		set_blend_func_separate(src_rgb, dst_rgb, src_alpha, dst_alpha) {
			this.gl.blendFuncSeparate(src_rgb, dst_rgb, src_alpha, dst_alpha);
			// the cache can't tell a separate func from a plain one with the
			// same rgb factors: never skip the next set_blend_func
			this._blend_src = -1;
			this._blend_dst = -1;
		}

		apply_blend_mode(blend_mode) {
			const gl = this.gl;

			switch (blend_mode) {
				case BlendMode.OPAQUE:
				case BlendMode.ALPHA_KEY:
					this.set_blend(false);
					this.set_depth_write(true);
					break;

				case BlendMode.ALPHA:
					this.set_blend(true);
					this.set_blend_func_separate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
					this.set_depth_write(false);
					break;

				case BlendMode.ADD:
					this.set_blend(true);
					this.set_blend_func_separate(gl.SRC_ALPHA, gl.ONE, gl.ZERO, gl.ONE);
					this.set_depth_write(false);
					break;

				case BlendMode.NO_ALPHA_ADD:
					this.set_blend(true);
					this.set_blend_func_separate(gl.ONE, gl.ONE, gl.ZERO, gl.ONE);
					this.set_depth_write(false);
					break;

				case BlendMode.MOD:
					this.set_blend(true);
					this.set_blend_func(gl.DST_COLOR, gl.ZERO);
					this.set_depth_write(false);
					break;

				case BlendMode.MOD2X:
					this.set_blend(true);
					this.set_blend_func(gl.DST_COLOR, gl.SRC_COLOR);
					this.set_depth_write(false);
					break;

				case BlendMode.MOD_ADD:
					this.set_blend(true);
					this.set_blend_func_separate(gl.DST_COLOR, gl.ONE, gl.DST_ALPHA, gl.ONE);
					this.set_depth_write(false);
					break;

				case BlendMode.INV_SRC_ALPHA_ADD:
					this.set_blend(true);
					this.set_blend_func(gl.ONE_MINUS_SRC_ALPHA, gl.ONE);
					this.set_depth_write(false);
					break;

				case BlendMode.INV_SRC_ALPHA_OPAQUE:
					this.set_blend(true);
					this.set_blend_func(gl.ONE_MINUS_SRC_ALPHA, gl.ZERO);
					this.set_depth_write(true);
					break;

				case BlendMode.SRC_ALPHA_OPAQUE:
					this.set_blend(true);
					this.set_blend_func(gl.SRC_ALPHA, gl.ZERO);
					this.set_depth_write(true);
					break;

				case BlendMode.CONSTANT_ALPHA:
					this.set_blend(true);
					this.set_blend_func(gl.CONSTANT_ALPHA, gl.ONE_MINUS_CONSTANT_ALPHA);
					this.set_depth_write(false);
					break;

				case BlendMode.SCREEN:
					this.set_blend(true);
					this.set_blend_func(gl.ONE, gl.ONE_MINUS_SRC_COLOR);
					this.set_depth_write(false);
					break;

				case BlendMode.BLEND_ADD:
					this.set_blend(true);
					this.set_blend_func_separate(gl.ONE, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
					this.set_depth_write(false);
					break;

				default:
					this.set_blend(false);
					this.set_depth_write(true);
			}
		}

		use_program(program) {
			if (this._current_program === program)
				return;
			this.gl.useProgram(program);
			this._current_program = program;
		}

		bind_vao(vao) {
			if (this._current_vao === vao)
				return;
			this.gl.bindVertexArray(vao);
			this._current_vao = vao;
		}

		active_texture(unit) {
			if (this._active_texture_unit === unit)
				return;
			this.gl.activeTexture(this.gl.TEXTURE0 + unit);
			this._active_texture_unit = unit;
		}

		bind_texture(unit, texture) {
			if (this._bound_textures[unit] === texture)
				return;
			this.active_texture(unit);
			this.gl.bindTexture(this.gl.TEXTURE_2D, texture);
			this._bound_textures[unit] = texture;
		}

		// After a raw gl.bindTexture outside the cache (texture uploads).
		forget_texture_bindings() {
			this._bound_textures.fill(null);
			this._active_texture_unit = -1;
		}
	}

	GLContext.BlendMode = BlendMode;

	class GLTexture {
		constructor(ctx) {
			this.ctx = ctx;
			this.gl = ctx.gl;
			this.texture = this.gl.createTexture();
			this.width = 0;
			this.height = 0;
			this.has_alpha = false;
		}

		bind(unit) {
			this.ctx.bind_texture(unit, this.texture);
		}

		set_rgba(pixels, width, height, options = {}) {
			const gl = this.gl;
			this.width = width;
			this.height = height;
			this.has_alpha = options.has_alpha ?? true;

			gl.bindTexture(gl.TEXTURE_2D, this.texture);
			gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
			gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, width, height, 0, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
			this._apply_wrap(options.wrap_s, options.wrap_t);
			this._apply_filter(options.generate_mipmaps ? gl.LINEAR_MIPMAP_LINEAR : options.min_filter, options.mag_filter);

			if (options.generate_mipmaps)
				gl.generateMipmap(gl.TEXTURE_2D);
			this.ctx.forget_texture_bindings();
		}

		_apply_wrap(wrap_s, wrap_t) {
			const gl = this.gl;
			gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, wrap_s ?? gl.CLAMP_TO_EDGE);
			gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, wrap_t ?? gl.CLAMP_TO_EDGE);
		}

		_apply_filter(min_filter, mag_filter) {
			const gl = this.gl;
			min_filter = min_filter ?? gl.LINEAR;
			mag_filter = mag_filter ?? gl.LINEAR;
			gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, min_filter);
			gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, mag_filter);

			if (this.ctx.ext_aniso && min_filter !== gl.NEAREST)
				gl.texParameterf(gl.TEXTURE_2D, this.ctx.ext_aniso.TEXTURE_MAX_ANISOTROPY_EXT, Math.min(8, this.ctx.max_anisotropy));
		}

		// wow.export's fallback path (decode on the CPU, mipmaps by GL).
		set_blp(blp, flags = {}) {
			const gl = this.gl;
			const wrap_s = (flags.flags & 0x1) ? gl.REPEAT : gl.CLAMP_TO_EDGE;
			const wrap_t = (flags.flags & 0x2) ? gl.REPEAT : gl.CLAMP_TO_EDGE;

			this.has_alpha = blp.alphaDepth > 0;
			const pixels = blp.toUInt8Array(0, 0b1111);
			this.set_rgba(pixels, blp.scaledWidth, blp.scaledHeight, {
				wrap_s,
				wrap_t,
				has_alpha: this.has_alpha,
				generate_mipmaps: true
			});
		}

		dispose() {
			if (this.texture) {
				this.gl.deleteTexture(this.texture);
				this.texture = null;
			}
		}
	}

	class ShaderProgram {
		constructor(ctx, vert_source, frag_source) {
			this.ctx = ctx;
			this.gl = ctx.gl;
			this.program = null;
			this.uniform_locations = new Map();
			this.uniform_block_indices = new Map();
			this._compile(vert_source, frag_source);
		}

		_compile(vert_source, frag_source) {
			const gl = this.gl;
			const vert_shader = this._compile_shader(gl.VERTEX_SHADER, vert_source);
			const frag_shader = this._compile_shader(gl.FRAGMENT_SHADER, frag_source);

			const program = gl.createProgram();
			gl.attachShader(program, vert_shader);
			gl.attachShader(program, frag_shader);
			gl.linkProgram(program);
			gl.deleteShader(vert_shader);
			gl.deleteShader(frag_shader);

			if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
				const info = gl.getProgramInfoLog(program);
				gl.deleteProgram(program);
				throw new Error('Shader program link error: ' + info);
			}

			this.program = program;
		}

		_compile_shader(type, source) {
			const gl = this.gl;
			const shader = gl.createShader(type);
			gl.shaderSource(shader, source);
			gl.compileShader(shader);

			if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
				const info = gl.getShaderInfoLog(shader);
				gl.deleteShader(shader);
				throw new Error('Shader compile error (' + (type === gl.VERTEX_SHADER ? 'vertex' : 'fragment') + '): ' + info);
			}

			return shader;
		}

		use() {
			this.ctx.use_program(this.program);
		}

		get_uniform_location(name) {
			if (this.uniform_locations.has(name))
				return this.uniform_locations.get(name);
			const location = this.gl.getUniformLocation(this.program, name);
			this.uniform_locations.set(name, location);
			return location;
		}

		get_uniform_block_index(name) {
			if (this.uniform_block_indices.has(name))
				return this.uniform_block_indices.get(name);
			const index = this.gl.getUniformBlockIndex(this.program, name);
			this.uniform_block_indices.set(name, index);
			return index;
		}

		get_uniform_block_param(name, pname) {
			const index = this.get_uniform_block_index(name);
			if (index !== this.gl.INVALID_INDEX)
				return this.gl.getActiveUniformBlockParameter(this.program, index, pname);
			return null;
		}

		get_active_uniform_offsets(names) {
			const indices = this.gl.getUniformIndices(this.program, names);
			return this.gl.getActiveUniforms(this.program, indices, this.gl.UNIFORM_OFFSET);
		}

		bind_uniform_block(name, binding_point) {
			const index = this.get_uniform_block_index(name);
			if (index !== this.gl.INVALID_INDEX)
				this.gl.uniformBlockBinding(this.program, index, binding_point);
		}

		set_uniform_1i(name, value) {
			const loc = this.get_uniform_location(name);
			if (loc !== null)
				this.gl.uniform1i(loc, value);
		}

		set_uniform_1f(name, value) {
			const loc = this.get_uniform_location(name);
			if (loc !== null)
				this.gl.uniform1f(loc, value);
		}

		set_uniform_3f(name, x, y, z) {
			const loc = this.get_uniform_location(name);
			if (loc !== null)
				this.gl.uniform3f(loc, x, y, z);
		}

		set_uniform_4f(name, x, y, z, w) {
			const loc = this.get_uniform_location(name);
			if (loc !== null)
				this.gl.uniform4f(loc, x, y, z, w);
		}

		set_uniform_mat4(name, transpose, value) {
			const loc = this.get_uniform_location(name);
			if (loc !== null)
				this.gl.uniformMatrix4fv(loc, transpose, value);
		}

		dispose() {
			if (this.program) {
				this.gl.deleteProgram(this.program);
				this.program = null;
			}
			this.uniform_locations.clear();
			this.uniform_block_indices.clear();
		}
	}

	class UniformBuffer {
		constructor(ctx, size) {
			this.ctx = ctx;
			this.gl = ctx.gl;
			this.size = size;
			this.buffer = this.gl.createBuffer();
			this.data = new ArrayBuffer(size);

			this.gl.bindBuffer(this.gl.UNIFORM_BUFFER, this.buffer);
			this.gl.bufferData(this.gl.UNIFORM_BUFFER, size, this.gl.DYNAMIC_DRAW);
		}

		bind(binding_point) {
			this.gl.bindBufferBase(this.gl.UNIFORM_BUFFER, binding_point, this.buffer);
		}

		get_float32_view(offset, length) {
			return new Float32Array(this.data, offset, length);
		}

		upload_range(offset, size) {
			this.gl.bindBuffer(this.gl.UNIFORM_BUFFER, this.buffer);
			this.gl.bufferSubData(this.gl.UNIFORM_BUFFER, offset, new Uint8Array(this.data, offset, size));
		}

		dispose() {
			if (this.buffer) {
				this.gl.deleteBuffer(this.buffer);
				this.buffer = null;
			}
			this.data = null;
		}
	}

	const AttributeLocation = {
		POSITION: 0,
		NORMAL: 1,
		BONE_INDICES: 2,
		BONE_WEIGHTS: 3,
		TEXCOORD: 4,
		TEXCOORD2: 5
	};

	class VertexArray {
		constructor(ctx) {
			this.ctx = ctx;
			this.gl = ctx.gl;
			this.vao = this.gl.createVertexArray();
			this.vbo = null;
			this.ebo = null;
		}

		bind() {
			this.ctx.bind_vao(this.vao);
		}

		// position(3f) normal(3f) bone_idx(4ub) bone_weight(4ub, normalized) uv(2f) uv2(2f)
		setup_m2_vertex_format() {
			const gl = this.gl;
			const stride = 48;

			this.bind();
			gl.bindBuffer(gl.ARRAY_BUFFER, this.vbo);

			gl.enableVertexAttribArray(AttributeLocation.POSITION);
			gl.vertexAttribPointer(AttributeLocation.POSITION, 3, gl.FLOAT, false, stride, 0);

			gl.enableVertexAttribArray(AttributeLocation.NORMAL);
			gl.vertexAttribPointer(AttributeLocation.NORMAL, 3, gl.FLOAT, false, stride, 12);

			gl.enableVertexAttribArray(AttributeLocation.BONE_INDICES);
			gl.vertexAttribIPointer(AttributeLocation.BONE_INDICES, 4, gl.UNSIGNED_BYTE, stride, 24);

			gl.enableVertexAttribArray(AttributeLocation.BONE_WEIGHTS);
			gl.vertexAttribPointer(AttributeLocation.BONE_WEIGHTS, 4, gl.UNSIGNED_BYTE, true, stride, 28);

			gl.enableVertexAttribArray(AttributeLocation.TEXCOORD);
			gl.vertexAttribPointer(AttributeLocation.TEXCOORD, 2, gl.FLOAT, false, stride, 32);

			gl.enableVertexAttribArray(AttributeLocation.TEXCOORD2);
			gl.vertexAttribPointer(AttributeLocation.TEXCOORD2, 2, gl.FLOAT, false, stride, 40);
		}

		dispose() {
			const gl = this.gl;
			if (this.ctx._current_vao === this.vao)
				this.ctx.bind_vao(null);
			if (this.vao) {
				gl.deleteVertexArray(this.vao);
				this.vao = null;
			}
			if (this.vbo) {
				gl.deleteBuffer(this.vbo);
				this.vbo = null;
			}
			if (this.ebo) {
				gl.deleteBuffer(this.ebo);
				this.ebo = null;
			}
		}
	}

	VertexArray.AttributeLocation = AttributeLocation;

	function create_bones_ubo(shader, gl, ctx, ubos, bone_count = 0) {
		shader.bind_uniform_block('VsBoneUbo', 0);
		const ubosize = shader.get_uniform_block_param('VsBoneUbo', gl.UNIFORM_BLOCK_DATA_SIZE);
		const offsets = shader.get_active_uniform_offsets(['u_bone_matrices']);
		const ubo = new UniformBuffer(ctx, ubosize);
		ubos.push({ ubo, offsets });

		const matrices = ubo.get_float32_view(offsets[0], (ubosize - offsets[0]) / 4);
		const count = Math.min(bone_count, matrices.length / 16);
		for (let i = 0; i < count; i++)
			matrices.set(IDENTITY_MAT4, i * 16);

		return matrices;
	}

	M3D.GLContext = GLContext;
	M3D.GLTexture = GLTexture;
	M3D.ShaderProgram = ShaderProgram;
	M3D.UniformBuffer = UniformBuffer;
	M3D.VertexArray = VertexArray;
	M3D.create_bones_ubo = create_bones_ubo;
})();
