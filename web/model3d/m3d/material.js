/*!
	wow.export (https://github.com/Kruithne/wow.export)
	Authors: Kruithne <kruithne@gmail.com>, Marlamin <marlamin@marlamin.com>
	License: MIT (LICENSE-wow.export.txt)
	Ported for wow-stats from 3D/renderers/CharMaterialRenderer.js: one
	character texture (a ChrModelMaterial) composited from its layers.
	Same shader, same order (ascending target), same section placement and
	the same GL blend state per ChrModelTextureLayer blend mode. It draws
	into a framebuffer of the page's own GL context instead of a canvas
	of its own, kept image-side up (row 0 = the top, as
	CharComponentTextureSections count y), so the multiply / overlay /
	screen modes read their base from the section itself; wow.export read
	it with readPixels from GL's bottom-left origin, i.e. from the section
	mirrored top to bottom.
 */
'use strict';

(() => {
	const M3D = globalThis.M3D = globalThis.M3D || {};
	const { GLTexture } = M3D;

	const UV_BUFFER_DATA = new Float32Array([
		0, 1,
		1, 1,
		0, 0,
		0, 0,
		1, 1,
		1, 0
	]);

	function char_program(gl) {
		const compile = (type, src) => {
			const s = gl.createShader(type);
			gl.shaderSource(s, src);
			gl.compileShader(s);
			if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
				const info = gl.getShaderInfoLog(s);
				gl.deleteShader(s);
				throw new Error('Character shader failed to compile: ' + info);
			}
			return s;
		};

		const prog = gl.createProgram();
		const vs = compile(gl.VERTEX_SHADER, M3D.shaders.char.vert);
		const fs = compile(gl.FRAGMENT_SHADER, M3D.shaders.char.frag);
		gl.attachShader(prog, vs);
		gl.attachShader(prog, fs);
		gl.linkProgram(prog);
		gl.deleteShader(vs);
		gl.deleteShader(fs);
		if (!gl.getProgramParameter(prog, gl.LINK_STATUS))
			throw new Error('Unable to link character shader: ' + gl.getProgramInfoLog(prog));

		return {
			program: prog,
			a_position: gl.getAttribLocation(prog, 'a_position'),
			a_texCoord: gl.getAttribLocation(prog, 'a_texCoord'),
			u_texture: gl.getUniformLocation(prog, 'u_texture'),
			u_baseTexture: gl.getUniformLocation(prog, 'u_baseTexture'),
			u_blendMode: gl.getUniformLocation(prog, 'u_blendMode')
		};
	}

	class CharMaterial {
		/**
		 * @param {M3D.GLContext} ctx
		 * @param {number} width ChrModelMaterial width
		 * @param {number} height ChrModelMaterial height
		 */
		constructor(ctx, width, height) {
			this.ctx = ctx;
			this.gl = ctx.gl;
			this.width = width;
			this.height = height;
			this.textureTargets = [];

			const gl = this.gl;
			if (!ctx._char_program)
				ctx._char_program = char_program(gl);
			this.prog = ctx._char_program;

			this.target = gl.createTexture();
			gl.bindTexture(gl.TEXTURE_2D, this.target);
			gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, width, height, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
			gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
			gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
			gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
			gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);

			this.fbo = gl.createFramebuffer();
			gl.bindFramebuffer(gl.FRAMEBUFFER, this.fbo);
			gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, this.target, 0);
			const status = gl.checkFramebufferStatus(gl.FRAMEBUFFER);
			gl.bindFramebuffer(gl.FRAMEBUFFER, null);
			ctx.forget_texture_bindings();
			if (status !== gl.FRAMEBUFFER_COMPLETE)
				throw new Error('Character material framebuffer incomplete: ' + status);

			this.vbuf = gl.createBuffer();
			this.uvbuf = gl.createBuffer();
		}

		/**
		 * One layer.
		 * @param {{target: number, x: number, y: number, w: number, h: number, blend: number}} layer
		 * @param {M3D.BLPImage} blp
		 */
		addLayer(layer, blp) {
			const gl = this.gl;
			const texture = gl.createTexture();
			const pixels = blp.toUInt8Array(0, 0b1111);
			gl.bindTexture(gl.TEXTURE_2D, texture);
			gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
			gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, blp.scaledWidth, blp.scaledHeight, 0, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
			gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
			gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
			gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
			this.ctx.forget_texture_bindings();

			this.textureTargets.push({
				id: layer.target,
				section: { X: layer.x, Y: layer.y, Width: layer.w, Height: layer.h },
				blend: layer.blend,
				textureID: texture
			});
		}

		/**
		 * Composite every layer into this material's texture.
		 */
		update() {
			const gl = this.gl;
			const p = this.prog;
			const W = this.width, H = this.height;

			gl.bindFramebuffer(gl.FRAMEBUFFER, this.fbo);
			gl.bindVertexArray(null);
			gl.viewport(0, 0, W, H);
			gl.disable(gl.DEPTH_TEST);
			gl.disable(gl.CULL_FACE);
			gl.disable(gl.BLEND);
			gl.blendFunc(gl.ONE, gl.ZERO);
			gl.clearColor(0, 0, 0, 1);
			gl.clear(gl.COLOR_BUFFER_BIT);

			gl.useProgram(p.program);

			// stable sort: same target keeps the recipe's order
			const layers = this.textureTargets.map((t, i) => [t, i])
				.sort((a, b) => (a[0].id - b[0].id) || (a[1] - b[1])).map(x => x[0]);

			const base = gl.createTexture();

			for (const layer of layers) {
				const midX = W / 2;
				const midY = H / 2;
				const s = layer.section;

				// wow.export's corners; clip y negated, so the image's top row
				// lands on the framebuffer's row 0
				const left = (s.X - midX) / midX;
				const right = (s.X + s.Width - midX) / midX;
				const bottom = (s.Y + s.Height - midY) / midY;
				const top = (s.Y - midY) / midY;

				gl.bindBuffer(gl.ARRAY_BUFFER, this.vbuf);
				gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
					left, bottom, 0.0,
					right, bottom, 0.0,
					left, top, 0.0,
					left, top, 0.0,
					right, bottom, 0.0,
					right, top, 0.0
				]), gl.DYNAMIC_DRAW);
				gl.vertexAttribPointer(p.a_position, 3, gl.FLOAT, false, 0, 0);
				gl.enableVertexAttribArray(p.a_position);

				gl.bindBuffer(gl.ARRAY_BUFFER, this.uvbuf);
				gl.bufferData(gl.ARRAY_BUFFER, UV_BUFFER_DATA, gl.STATIC_DRAW);
				gl.vertexAttribPointer(p.a_texCoord, 2, gl.FLOAT, false, 0, 0);
				gl.enableVertexAttribArray(p.a_texCoord);

				gl.uniform1i(p.u_texture, 0);
				gl.uniform1f(p.u_blendMode, layer.blend);

				gl.activeTexture(gl.TEXTURE0);
				gl.bindTexture(gl.TEXTURE_2D, layer.textureID);
				gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
				gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);

				switch (layer.blend) {
					case 0: // None
					case 1: // Blit: straight copy, keeps albedo rgb + alpha mask
						gl.disable(gl.BLEND);
						gl.blendFunc(gl.ONE, gl.ZERO);
						break;
					case 4: // Multiply
					case 6: // Overlay
					case 7: // Screen
					case 15: // Infer alpha blend
						gl.enable(gl.BLEND);
						gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
						break;
					case 9: // Alpha Straight
						gl.enable(gl.BLEND);
						gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
						break;
					// not used by character customization: wow.export leaves the
					// blend state as it was (and its shader draws magenta)
					case 2: // Blit Alphamask
					case 3: // Add
					case 5: // Mod2x
					case 8: // Hardlight
					case 10: // Blend black
					case 11: // Mask greyscale
					case 12: // Mask greyscale using color as alpha
					case 13: // Generate greyscale
					case 14: // Colorize
						break;
					case 16: // Unknown, only TaunkaMale.m2
					default:
						gl.enable(gl.BLEND);
						gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
						break;
				}

				if (layer.blend === 4 || layer.blend === 6 || layer.blend === 7) {
					// the section as composited so far, as the base
					gl.activeTexture(gl.TEXTURE1);
					gl.bindTexture(gl.TEXTURE_2D, base);
					const x0 = Math.max(0, Math.min(W, s.X | 0));
					const y0 = Math.max(0, Math.min(H, s.Y | 0));
					const w = Math.max(1, Math.min(W - x0, s.Width | 0));
					const h = Math.max(1, Math.min(H - y0, s.Height | 0));
					gl.copyTexImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, x0, y0, w, h, 0);
					gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
					gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
					gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
					gl.uniform1i(p.u_baseTexture, 1);
					gl.activeTexture(gl.TEXTURE0);
				}

				gl.drawArrays(gl.TRIANGLES, 0, 6);
			}

			gl.deleteTexture(base);
			gl.disableVertexAttribArray(p.a_position);
			gl.disableVertexAttribArray(p.a_texCoord);
			this.ctx.reset_state();
		}

		// A copy of the composite as an M2 texture with its own wrap modes
		// (wow.export uploads the read-back pixels once per texture slot).
		make_texture(wrap_s, wrap_t) {
			const gl = this.gl;
			const tex = new GLTexture(this.ctx);
			tex.width = this.width;
			tex.height = this.height;
			tex.has_alpha = true;

			gl.bindFramebuffer(gl.FRAMEBUFFER, this.fbo);
			gl.bindTexture(gl.TEXTURE_2D, tex.texture);
			gl.copyTexImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, 0, 0, this.width, this.height, 0);
			gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, wrap_s);
			gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, wrap_t);
			gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
			gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
			gl.bindFramebuffer(gl.FRAMEBUFFER, null);
			this.ctx.forget_texture_bindings();
			return tex;
		}

		// RGBA of the composite, row 0 at the top (tests and debugging).
		readPixels() {
			const gl = this.gl;
			const out = new Uint8Array(this.width * this.height * 4);
			gl.bindFramebuffer(gl.FRAMEBUFFER, this.fbo);
			gl.readPixels(0, 0, this.width, this.height, gl.RGBA, gl.UNSIGNED_BYTE, out);
			gl.bindFramebuffer(gl.FRAMEBUFFER, null);
			return out;
		}

		dispose() {
			const gl = this.gl;
			for (const t of this.textureTargets)
				gl.deleteTexture(t.textureID);
			this.textureTargets = [];
			gl.deleteFramebuffer(this.fbo);
			gl.deleteTexture(this.target);
			gl.deleteBuffer(this.vbuf);
			gl.deleteBuffer(this.uvbuf);
		}
	}

	M3D.CharMaterial = CharMaterial;
})();
