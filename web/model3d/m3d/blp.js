/*!
	wow.export (https://github.com/Kruithne/wow.export)
	Authors: Kruithne <kruithne@gmail.com>
	License: MIT (LICENSE-wow.export.txt)
	Ported for wow-stats from casc/blp.js: BLP2 to RGBA in the browser
	(palettized with 0/1/4/8-bit alpha, DXT1/3/5, uncompressed BGRA).
	Differences from wow.export: 4-bit alpha reads the right byte for odd
	pixels (wow.export indexed with a fraction and read 0) and widens the
	nibble to 0-255 (x17, not <<4); no PNG/WebP/canvas output.
 */
'use strict';

(() => {
	const M3D = globalThis.M3D = globalThis.M3D || {};

	const DXT1 = 0x1;
	const DXT3 = 0x2;
	const DXT5 = 0x4;

	const BLP_MAGIC = 0x32504c42;
	// A bigger texture than the game ships is a broken file, not a texture.
	const MAX_SIZE = 8192;

	const unpackColour = (block, index, ofs, colour, colourOfs) => {
		const value = block[index + ofs] | (block[index + 1 + ofs] << 8);

		const r = (value >> 11) & 0x1F;
		const g = (value >> 5) & 0x3F;
		const b = value & 0x1F;

		colour[colourOfs] = (r << 3) | (r >> 2);
		colour[colourOfs + 1] = (g << 2) | (g >> 4);
		colour[colourOfs + 2] = (b << 3) | (b >> 2);
		colour[colourOfs + 3] = 255;

		return value;
	};

	class BLPImage {
		/**
		 * @param {ArrayBuffer|Uint8Array|M3D.Reader} data
		 */
		constructor(data) {
			this.data = data instanceof M3D.Reader ? data : new M3D.Reader(data);
			this.data.seek(0);

			if (this.data.readUInt32LE() !== BLP_MAGIC)
				throw new Error('Provided data is not a BLP file (invalid header magic).');

			const type = this.data.readUInt32LE();
			if (type !== 1)
				throw new Error('Unsupported BLP type: ' + type);

			this.encoding = this.data.readUInt8();
			this.alphaDepth = this.data.readUInt8();
			this.alphaEncoding = this.data.readUInt8();
			this.containsMipmaps = this.data.readUInt8();

			this.width = this.data.readUInt32LE();
			this.height = this.data.readUInt32LE();
			if (!this.width || !this.height || this.width > MAX_SIZE || this.height > MAX_SIZE)
				throw new Error('Bad BLP size: ' + this.width + 'x' + this.height);

			this.mapOffsets = this.data.readUInt32LE(16);
			this.mapSizes = this.data.readUInt32LE(16);

			this.mapCount = 0;
			for (const ofs of this.mapOffsets) {
				if (ofs !== 0)
					this.mapCount++;
			}

			this.palette = [];
			if (this.encoding === 1) {
				for (let i = 0; i < 256; i++)
					this.palette[i] = this.data.readUInt8(4);
			}
		}

		_prepare(mipmap = 0) {
			mipmap = Math.max(0, Math.min(mipmap || 0, this.mapCount - 1));

			this.scale = Math.pow(2, mipmap);
			this.scaledWidth = Math.max(1, Math.floor(this.width / this.scale));
			this.scaledHeight = Math.max(1, Math.floor(this.height / this.scale));
			this.scaledLength = this.scaledWidth * this.scaledHeight;

			this.data.seek(this.mapOffsets[mipmap]);
			this.rawData = this.data.readBytes(this.mapSizes[mipmap]);
		}

		/**
		 * This BLP as RGBA bytes (row 0 at the top).
		 * @param {number} mipmap
		 * @param {number} mask channels to keep: 0b0001 R ... 0b1000 A (alpha 255 when masked out)
		 * @returns {Uint8Array}
		 */
		toUInt8Array(mipmap = 0, mask = 0b1111) {
			this._prepare(mipmap);

			const arr = new Uint8Array(this.scaledWidth * this.scaledHeight * 4);
			switch (this.encoding) {
				case 1: this._getUncompressed(arr, mask); break;
				case 2: this._getCompressed(arr, mask); break;
				case 3: this._marshalBGRA(arr, mask); break;
				default: throw new Error('Unsupported BLP encoding: ' + this.encoding);
			}

			return arr;
		}

		_getAlpha(index) {
			let byte;
			switch (this.alphaDepth) {
				case 1:
					byte = this.rawData[this.scaledLength + Math.floor(index / 8)];
					return (byte & (0x01 << (index % 8))) === 0 ? 0x00 : 0xFF;

				case 4:
					byte = this.rawData[this.scaledLength + (index >> 1)];
					return (index % 2 === 0 ? (byte & 0x0F) : (byte >> 4)) * 17;

				case 8:
					return this.rawData[this.scaledLength + index];

				default:
					return 0xFF;
			}
		}

		_getCompressed(data, mask = 0b1111) {
			const flags = this.alphaDepth > 1 ? (this.alphaEncoding === 7 ? DXT5 : DXT3) : DXT1;
			const raw = this.rawData;

			let pos = 0;
			const blockBytes = (flags & DXT1) !== 0 ? 8 : 16;
			const target = new Array(4 * 16);

			for (let y = 0, sh = this.scaledHeight; y < sh; y += 4) {
				for (let x = 0, sw = this.scaledWidth; x < sw; x += 4) {
					if (raw.length < pos + blockBytes)
						continue;

					let colourIndex = pos;
					if ((flags & (DXT3 | DXT5)) !== 0)
						colourIndex += 8;

					const isDXT1 = (flags & DXT1) !== 0;
					const colours = [];
					const a = unpackColour(raw, colourIndex, 0, colours, 0);
					const b = unpackColour(raw, colourIndex, 2, colours, 4);

					for (let i = 0; i < 3; i++) {
						const c = colours[i];
						const d = colours[i + 4];

						if (isDXT1 && a <= b) {
							colours[i + 8] = (c + d) / 2;
							colours[i + 12] = 0;
						} else {
							colours[i + 8] = (2 * c + d) / 3;
							colours[i + 12] = (c + 2 * d) / 3;
						}
					}

					colours[8 + 3] = 255;
					colours[12 + 3] = (isDXT1 && a <= b) ? 0 : 255;

					const index = [];
					for (let i = 0; i < 4; i++) {
						const packed = raw[colourIndex + 4 + i];
						index[i * 4] = packed & 0x3;
						index[1 + i * 4] = (packed >> 2) & 0x3;
						index[2 + i * 4] = (packed >> 4) & 0x3;
						index[3 + i * 4] = (packed >> 6) & 0x3;
					}

					for (let i = 0; i < 16; i++) {
						const ofs = index[i] * 4;
						target[4 * i] = colours[ofs];
						target[4 * i + 1] = colours[ofs + 1];
						target[4 * i + 2] = colours[ofs + 2];
						target[4 * i + 3] = colours[ofs + 3];
					}

					if ((flags & DXT3) !== 0) {
						for (let i = 0; i < 8; i++) {
							const quant = raw[pos + i];

							const low = (quant & 0x0F);
							const high = (quant & 0xF0);

							target[8 * i + 3] = (low | (low << 4));
							target[8 * i + 7] = (high | (high >> 4));
						}
					} else if ((flags & DXT5) !== 0) {
						const a0 = raw[pos];
						const a1 = raw[pos + 1];

						const alphas = [a0, a1];

						if (a0 <= a1) {
							for (let i = 1; i < 5; i++)
								alphas[i + 1] = (((5 - i) * a0 + i * a1) / 5) | 0;

							alphas[6] = 0;
							alphas[7] = 255;
						} else {
							for (let i = 1; i < 7; i++)
								alphas[i + 1] = (((7 - i) * a0 + i * a1) / 7) | 0;
						}

						const indices = [];
						let blockPos = 2;
						let indicesPos = 0;

						for (let i = 0; i < 2; i++) {
							let value = 0;
							for (let j = 0; j < 3; j++) {
								const byte = raw[pos + blockPos++];
								value |= (byte << 8 * j);
							}

							for (let j = 0; j < 8; j++)
								indices[indicesPos++] = (value >> 3 * j) & 0x07;
						}

						for (let i = 0; i < 16; i++)
							target[4 * i + 3] = alphas[indices[i]];
					}

					let blockPos = 0;
					for (let pY = 0; pY < 4; pY++) {
						for (let pX = 0; pX < 4; pX++) {
							const sX = x + pX;
							const sY = y + pY;

							if (sX < sw && sY < sh) {
								const pixel = 4 * (sw * sY + sX);
								data[pixel + 0] = (mask & 0b1) ? target[blockPos + 0] : 0;
								data[pixel + 1] = (mask & 0b10) ? target[blockPos + 1] : 0;
								data[pixel + 2] = (mask & 0b100) ? target[blockPos + 2] : 0;
								data[pixel + 3] = (mask & 0b1000) ? target[blockPos + 3] : 255;
							}
							blockPos += 4;
						}
					}
					pos += blockBytes;
				}
			}
		}

		_getUncompressed(data, mask) {
			for (let i = 0, n = this.scaledLength; i < n; i++) {
				const ofs = i * 4;
				const colour = this.palette[this.rawData[i]];

				data[ofs] = (mask & 0b1) ? colour[2] : 0;
				data[ofs + 1] = (mask & 0b10) ? colour[1] : 0;
				data[ofs + 2] = (mask & 0b100) ? colour[0] : 0;
				data[ofs + 3] = (mask & 0b1000) ? this._getAlpha(i) : 255;
			}
		}

		_marshalBGRA(out, mask) {
			const data = this.rawData;
			const n = Math.min(data.length >> 2, this.scaledLength);
			for (let i = 0; i < n; i++) {
				const ofs = i * 4;
				out[ofs] = (mask & 0b1) ? data[ofs + 2] : 0;
				out[ofs + 1] = (mask & 0b10) ? data[ofs + 1] : 0;
				out[ofs + 2] = (mask & 0b100) ? data[ofs] : 0;
				out[ofs + 3] = (mask & 0b1000) ? data[ofs + 3] : 255;
			}
		}
	}

	M3D.BLPImage = BLPImage;
})();
