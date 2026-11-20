/*!
	wow.export (https://github.com/Kruithne/wow.export)
	Authors: Kruithne <kruithne@gmail.com>, Marlamin <marlamin@marlamin.com>
	License: MIT (LICENSE-wow.export.txt)
	Ported for wow-stats: the M3D namespace, a DataView reader in place of
	wow.export's Node Buffer based BufferWrapper (buffer.js), and the matrix
	helpers of 3D/renderers/M2RendererGL.js.
 */
'use strict';

(() => {
	const M3D = globalThis.M3D = globalThis.M3D || {};

	// A little-endian reader over one file. Each read with a count returns an
	// array, as BufferWrapper's do; a read past the end throws (DataView's
	// RangeError), which fails that one file, never the page.
	class Reader {
		constructor(buf) {
			if (buf instanceof ArrayBuffer)
				buf = new Uint8Array(buf);
			if (!(buf instanceof Uint8Array))
				throw new TypeError('Reader takes an ArrayBuffer or a Uint8Array');
			this.bytes = buf;
			this.view = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
			this.offset = 0;
		}

		get byteLength() { return this.view.byteLength; }
		get remainingBytes() { return this.view.byteLength - this.offset; }
		seek(ofs) { this.offset = ofs; }
		move(n) { this.offset += n; }

		_read(size, fn, count) {
			if (count === undefined) {
				const v = fn.call(this.view, this.offset, true);
				this.offset += size;
				return v;
			}
			if (count < 0 || this.offset + count * size > this.view.byteLength)
				throw new RangeError('read past the end of the file');
			const out = new Array(count);
			for (let i = 0; i < count; i++) {
				out[i] = fn.call(this.view, this.offset, true);
				this.offset += size;
			}
			return out;
		}

		readUInt8(n) { return this._read(1, DataView.prototype.getUint8, n); }
		readInt8(n) { return this._read(1, DataView.prototype.getInt8, n); }
		readUInt16LE(n) { return this._read(2, DataView.prototype.getUint16, n); }
		readInt16LE(n) { return this._read(2, DataView.prototype.getInt16, n); }
		readUInt32LE(n) { return this._read(4, DataView.prototype.getUint32, n); }
		readInt32LE(n) { return this._read(4, DataView.prototype.getInt32, n); }
		readFloatLE(n) { return this._read(4, DataView.prototype.getFloat32, n); }

		// The next `len` bytes, as a new Reader's own copy.
		readBytes(len) {
			if (len < 0 || this.offset + len > this.view.byteLength)
				throw new RangeError('read past the end of the file');
			const out = this.bytes.slice(this.offset, this.offset + len);
			this.offset += len;
			return out;
		}

		readString(len) {
			if (len <= 0)
				return '';
			return new TextDecoder('utf-8').decode(this.readBytes(len));
		}
	}

	// ------------------------------------------------------------------ matrices
	// Column-major 4x4, as M2RendererGL.js has them.

	const IDENTITY_MAT4 = new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);

	// binary search for largest index where timestamps[i] <= time
	function find_keyframe(timestamps, time) {
		let lo = 0, hi = timestamps.length - 1;
		while (lo < hi) {
			const mid = (lo + hi + 1) >> 1;
			if (timestamps[mid] <= time)
				lo = mid;
			else
				hi = mid - 1;
		}
		return lo;
	}

	// out = a * b
	function mat4_multiply(out, a, b) {
		const a00 = a[0], a01 = a[1], a02 = a[2], a03 = a[3];
		const a10 = a[4], a11 = a[5], a12 = a[6], a13 = a[7];
		const a20 = a[8], a21 = a[9], a22 = a[10], a23 = a[11];
		const a30 = a[12], a31 = a[13], a32 = a[14], a33 = a[15];

		let b0 = b[0], b1 = b[1], b2 = b[2], b3 = b[3];
		out[0] = b0 * a00 + b1 * a10 + b2 * a20 + b3 * a30;
		out[1] = b0 * a01 + b1 * a11 + b2 * a21 + b3 * a31;
		out[2] = b0 * a02 + b1 * a12 + b2 * a22 + b3 * a32;
		out[3] = b0 * a03 + b1 * a13 + b2 * a23 + b3 * a33;

		b0 = b[4]; b1 = b[5]; b2 = b[6]; b3 = b[7];
		out[4] = b0 * a00 + b1 * a10 + b2 * a20 + b3 * a30;
		out[5] = b0 * a01 + b1 * a11 + b2 * a21 + b3 * a31;
		out[6] = b0 * a02 + b1 * a12 + b2 * a22 + b3 * a32;
		out[7] = b0 * a03 + b1 * a13 + b2 * a23 + b3 * a33;

		b0 = b[8]; b1 = b[9]; b2 = b[10]; b3 = b[11];
		out[8] = b0 * a00 + b1 * a10 + b2 * a20 + b3 * a30;
		out[9] = b0 * a01 + b1 * a11 + b2 * a21 + b3 * a31;
		out[10] = b0 * a02 + b1 * a12 + b2 * a22 + b3 * a32;
		out[11] = b0 * a03 + b1 * a13 + b2 * a23 + b3 * a33;

		b0 = b[12]; b1 = b[13]; b2 = b[14]; b3 = b[15];
		out[12] = b0 * a00 + b1 * a10 + b2 * a20 + b3 * a30;
		out[13] = b0 * a01 + b1 * a11 + b2 * a21 + b3 * a31;
		out[14] = b0 * a02 + b1 * a12 + b2 * a22 + b3 * a32;
		out[15] = b0 * a03 + b1 * a13 + b2 * a23 + b3 * a33;
	}

	function mat4_from_translation(out, x, y, z) {
		out[0] = 1; out[1] = 0; out[2] = 0; out[3] = 0;
		out[4] = 0; out[5] = 1; out[6] = 0; out[7] = 0;
		out[8] = 0; out[9] = 0; out[10] = 1; out[11] = 0;
		out[12] = x; out[13] = y; out[14] = z; out[15] = 1;
	}

	// quaternion (x, y, z, w)
	function mat4_from_quat(out, x, y, z, w) {
		const x2 = x + x, y2 = y + y, z2 = z + z;
		const xx = x * x2, xy = x * y2, xz = x * z2;
		const yy = y * y2, yz = y * z2, zz = z * z2;
		const wx = w * x2, wy = w * y2, wz = w * z2;

		out[0] = 1 - (yy + zz);
		out[1] = xy + wz;
		out[2] = xz - wy;
		out[3] = 0;

		out[4] = xy - wz;
		out[5] = 1 - (xx + zz);
		out[6] = yz + wx;
		out[7] = 0;

		out[8] = xz + wy;
		out[9] = yz - wx;
		out[10] = 1 - (xx + yy);
		out[11] = 0;

		out[12] = 0;
		out[13] = 0;
		out[14] = 0;
		out[15] = 1;
	}

	function mat4_from_scale(out, x, y, z) {
		out[0] = x; out[1] = 0; out[2] = 0; out[3] = 0;
		out[4] = 0; out[5] = y; out[6] = 0; out[7] = 0;
		out[8] = 0; out[9] = 0; out[10] = z; out[11] = 0;
		out[12] = 0; out[13] = 0; out[14] = 0; out[15] = 1;
	}

	function mat4_copy(out, src) {
		out.set(src);
	}

	function lerp(a, b, t) {
		return a + (b - a) * t;
	}

	// spherical linear interpolation, out = [x, y, z, w]
	function quat_slerp(out, ax, ay, az, aw, bx, by, bz, bw, t) {
		let cosom = ax * bx + ay * by + az * bz + aw * bw;

		if (cosom < 0) {
			cosom = -cosom;
			bx = -bx; by = -by; bz = -bz; bw = -bw;
		}

		let scale0, scale1;
		if (1 - cosom > 0.000001) {
			const omega = Math.acos(cosom);
			const sinom = Math.sin(omega);
			scale0 = Math.sin((1 - t) * omega) / sinom;
			scale1 = Math.sin(t * omega) / sinom;
		} else {
			scale0 = 1 - t;
			scale1 = t;
		}

		out[0] = scale0 * ax + scale1 * bx;
		out[1] = scale0 * ay + scale1 * by;
		out[2] = scale0 * az + scale1 * bz;
		out[3] = scale0 * aw + scale1 * bw;
	}

	// A file the model can't be drawn without (its skin) didn't come.
	class MissingFile extends Error {
		constructor(fileId) {
			super('missing file ' + fileId);
			this.fileId = fileId;
		}
	}

	M3D.Reader = Reader;
	M3D.MissingFile = MissingFile;
	M3D.math = {
		IDENTITY_MAT4, find_keyframe, mat4_multiply, mat4_from_translation, mat4_from_quat,
		mat4_from_scale, mat4_copy, lerp, quat_slerp
	};
})();
