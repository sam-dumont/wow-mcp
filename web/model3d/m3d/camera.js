/*!
	wow.export (https://github.com/Kruithne/wow.export)
	Authors: Kruithne <kruithne@gmail.com>
	License: MIT (LICENSE-wow.export.txt)
	Ported for wow-stats from components/model-viewer-gl.js (PerspectiveCamera)
	and 3D/camera/CharacterCameraControlsGL.js (drag turns the model, not
	the camera). Pointer events in place of mouse events, so a finger turns
	it too; no pan, no wheel zoom (the frame sits in a scrolling page). The
	framing is ours: the whole model, head to feet, at any turn.
 */
'use strict';

(() => {
	const M3D = globalThis.M3D = globalThis.M3D || {};

	class PerspectiveCamera {
		constructor(fov, aspect, near, far) {
			this.fov = fov;
			this.aspect = aspect;
			this.near = near;
			this.far = far;

			this.position = [0, 0, 5];
			this.target = [0, 0, 0];
			this.up = [0, 1, 0];

			this.view_matrix = new Float32Array(16);
			this.projection_matrix = new Float32Array(16);

			this.update_projection();
			this.update_view();
		}

		update_projection() {
			const f = 1.0 / Math.tan(this.fov * 0.5 * Math.PI / 180);
			const nf = 1 / (this.near - this.far);
			const m = this.projection_matrix;

			m.fill(0);
			m[0] = f / this.aspect;
			m[5] = f;
			m[10] = (this.far + this.near) * nf;
			m[11] = -1;
			m[14] = 2 * this.far * this.near * nf;
		}

		update_view() {
			const px = this.position[0], py = this.position[1], pz = this.position[2];
			const tx = this.target[0], ty = this.target[1], tz = this.target[2];
			const ux = this.up[0], uy = this.up[1], uz = this.up[2];

			let fx = px - tx, fy = py - ty, fz = pz - tz;
			const fl = Math.sqrt(fx * fx + fy * fy + fz * fz);
			if (fl > 0) { fx /= fl; fy /= fl; fz /= fl; }

			let rx = uy * fz - uz * fy;
			let ry = uz * fx - ux * fz;
			let rz = ux * fy - uy * fx;
			const rl = Math.sqrt(rx * rx + ry * ry + rz * rz);
			if (rl > 0) { rx /= rl; ry /= rl; rz /= rl; }

			const nux = fy * rz - fz * ry;
			const nuy = fz * rx - fx * rz;
			const nuz = fx * ry - fy * rx;

			const v = this.view_matrix;
			v[0] = rx; v[1] = nux; v[2] = fx; v[3] = 0;
			v[4] = ry; v[5] = nuy; v[6] = fy; v[7] = 0;
			v[8] = rz; v[9] = nuz; v[10] = fz; v[11] = 0;
			v[12] = -(rx * px + ry * py + rz * pz);
			v[13] = -(nux * px + nuy * py + nuz * pz);
			v[14] = -(fx * px + fy * py + fz * pz);
			v[15] = 1;
		}

		lookAt(x, y, z) {
			this.target[0] = x;
			this.target[1] = y;
			this.target[2] = z;
			this.update_view();
		}

		// world point -> [x, y] in CSS pixels of a w x h viewport (null behind)
		project(p, w, h) {
			const v = this.view_matrix, m = this.projection_matrix;
			const ex = v[0] * p[0] + v[4] * p[1] + v[8] * p[2] + v[12];
			const ey = v[1] * p[0] + v[5] * p[1] + v[9] * p[2] + v[13];
			const ez = v[2] * p[0] + v[6] * p[1] + v[10] * p[2] + v[14];
			const cx = m[0] * ex + m[8] * ez;
			const cy = m[5] * ey + m[9] * ez;
			const cw = -ez;
			if (cw <= 0)
				return null;
			return [(cx / cw + 1) / 2 * w, (1 - cy / cw) / 2 * h];
		}
	}

	// Below this width the frame is a phone's: more room around the model.
	const NARROW = 560;

	/**
	 * Head-to-feet framing of a turntable: the model turns about the Y axis
	 * through the origin, so its horizontal extent is the radius of the
	 * box's corners about that axis.
	 * @param {PerspectiveCamera} camera
	 * @param {{min: number[], max: number[]}} box WebGL coordinates, unrotated
	 * @param {number} css_width
	 */
	function fit_turntable(camera, box, css_width) {
		const min = box.min, max = box.max;
		const radius = Math.max(
			Math.hypot(min[0], min[2]), Math.hypot(min[0], max[2]),
			Math.hypot(max[0], min[2]), Math.hypot(max[0], max[2]), 0.01);
		const half_h = Math.max((max[1] - min[1]) / 2, 0.01);
		const cy = (min[1] + max[1]) / 2;

		const narrow = css_width < NARROW;
		const margin = narrow ? 1.14 : 1.06;
		const t = Math.tan(camera.fov * 0.5 * Math.PI / 180);

		const dist_v = half_h * margin / t + radius;
		const dist_h = radius * margin / (t * camera.aspect) + radius;
		const d = Math.max(dist_v, dist_h);

		// a little above the middle, looking slightly down, as a character
		// screen does
		const elev = 0.08;
		camera.near = Math.max(0.01, d / 100);
		camera.far = d * 10 + radius * 4;
		camera.update_projection();
		camera.position[0] = 0;
		camera.position[1] = cy + d * elev;
		camera.position[2] = d;
		camera.lookAt(0, cy, 0);
	}

	// Drag (mouse or finger) turns the model about its vertical axis.
	class TurntableControls {
		/**
		 * @param {HTMLElement} dom_element
		 * @param {function(number)} on_rotate called with the new angle (radians)
		 * @param {number} rotation the starting angle
		 */
		constructor(dom_element, on_rotate, rotation = 0) {
			this.dom_element = dom_element;
			this.on_rotate = on_rotate;
			this.rotation = rotation;
			this.pointer = null;
			this.prev_x = 0;

			this._down = (e) => this.on_down(e);
			this._move = (e) => this.on_move(e);
			this._up = (e) => this.on_up(e);

			dom_element.addEventListener('pointerdown', this._down);
			dom_element.addEventListener('pointermove', this._move);
			dom_element.addEventListener('pointerup', this._up);
			dom_element.addEventListener('pointercancel', this._up);
			// vertical swipes still scroll the page around the frame
			if (dom_element.style && !dom_element.style.touchAction)
				dom_element.style.touchAction = 'pan-y';
		}

		on_down(e) {
			if (this.pointer !== null || (e.pointerType === 'mouse' && e.button !== 0))
				return;
			this.pointer = e.pointerId;
			this.prev_x = e.clientX;
			try { this.dom_element.setPointerCapture(e.pointerId); } catch (err) { /* already released */ }
			if (e.pointerType === 'mouse')
				e.preventDefault();
		}

		on_move(e) {
			if (e.pointerId !== this.pointer)
				return;
			const dx = e.clientX - this.prev_x;
			this.prev_x = e.clientX;
			// a finger covers fewer pixels than a mouse for the same turn
			this.rotation += dx * (e.pointerType === 'mouse' ? 0.01 : 0.015);
			this.on_rotate(this.rotation);
		}

		on_up(e) {
			if (e.pointerId !== this.pointer)
				return;
			this.pointer = null;
			try { this.dom_element.releasePointerCapture(e.pointerId); } catch (err) { /* already released */ }
		}

		dispose() {
			const el = this.dom_element;
			el.removeEventListener('pointerdown', this._down);
			el.removeEventListener('pointermove', this._move);
			el.removeEventListener('pointerup', this._up);
			el.removeEventListener('pointercancel', this._up);
		}
	}

	M3D.PerspectiveCamera = PerspectiveCamera;
	M3D.TurntableControls = TurntableControls;
	M3D.fit_turntable = fit_turntable;
})();
