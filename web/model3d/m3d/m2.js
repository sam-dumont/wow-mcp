/*!
	wow.export (https://github.com/Kruithne/wow.export)
	Authors: Kruithne <kruithne@gmail.com>, Marlamin <marlamin@marlamin.com>
	License: MIT (LICENSE-wow.export.txt)
	Ported for wow-stats from 3D/loaders/M2Generics.js, M2Loader.js,
	SKELLoader.js, ANIMLoader.js and 3D/Skin.js. Files come from the
	`fetch(fileId) -> Promise<M3D.Reader|null>` the caller hands in, not
	from CASC; no listfile, no log, no BFID .bone files.

	The particle and ribbon emitters (read_particle, read_ribbon, the
	"fake animation blocks", spline keys, compressed gravity) are not in
	wow.export: they are written for wow-stats from the M2 page of
	wowdev.wiki, version 264+ layouts (a particle is 0x1EC bytes, a ribbon
	0xB0).
 */
'use strict';

(() => {
	const M3D = globalThis.M3D = globalThis.M3D || {};
	const Reader = M3D.Reader;

	const MAGIC_MD21 = 0x3132444D;
	const MAGIC_MD20 = 0x3032444D;
	const MAGIC_SKIN = 0x4E494B53;

	const CHUNK_SFID = 0x44494653;
	const CHUNK_TXID = 0x44495854;
	const CHUNK_SKID = 0x44494B53;
	const CHUNK_BFID = 0x44494642;
	const CHUNK_AFID = 0x44494641;

	const CHUNK_SKB1 = 0x31424B53;
	const CHUNK_SKPD = 0x44504B53;
	const CHUNK_SKS1 = 0x31534B53;
	const CHUNK_SKA1 = 0x31414B53;

	const CHUNK_AFM2 = 0x324D4641;
	const CHUNK_AFSA = 0x41534641;
	const CHUNK_AFSB = 0x42534641;

	// ------------------------------------------------------------------ M2Generics

	class M2Track {
		constructor(globalSeq, interpolation, timestamps, values, timestampOffsets = null, valueOffsets = null) {
			this.globalSeq = globalSeq;
			this.interpolation = interpolation;
			this.timestamps = timestamps;
			this.values = values;
			this.timestampOffsets = timestampOffsets;
			this.valueOffsets = valueOffsets;
		}
	}

	const compquat = (e) => (e - 32767) / 32768;

	// Compressed particle gravity (particle flag 0x800000): int8 x, int8 y,
	// int16 z -> a direction and a length (wowdev.wiki's decoder).
	function read_gravity(data) {
		const x = data.readInt8() / 128, y = data.readInt8() / 128;
		let mag = data.readInt16LE() * 0.04238648;
		let z = Math.sqrt(Math.max(0, 1 - (x * x + y * y)));
		if (mag < 0) {
			z = -z;
			mag = -mag;
		}
		return [x * mag, y * mag, z * mag];
	}

	function read_value(data, dataType) {
		// a bezier / hermite key (interpolation 2, 3): value, in and out
		// tangents; the tangents are skipped (sampled linearly)
		if (dataType.endsWith('~')) {
			const base = dataType.slice(0, -1);
			const v = read_value(data, base);
			data.move(2 * value_size(base));
			return v;
		}
		switch (dataType) {
			case 'uint32': return data.readUInt32LE();
			case 'int16': return data.readInt16LE();
			case 'uint16': return data.readUInt16LE();
			case 'float': return data.readFloatLE();
			case 'float2': return data.readFloatLE(2);
			case 'float3': return data.readFloatLE(3);
			case 'float4': return data.readFloatLE(4);
			case 'compquat': return data.readUInt16LE(4).map(compquat);
			case 'uint8': return data.readUInt8();
			// an uncompressed particle gravity is a pull along -z
			case 'gravity': return [0, 0, -data.readFloatLE()];
			case 'gravity_c': return read_gravity(data);
			default: throw new Error('Unknown data type: ' + dataType);
		}
	}

	// See the M2 page of wowdev.wiki, "Standard animation block". The values of a
	// sequence whose data lives in an external .anim (flags without 0x20)
	// are left empty here and patched in by patch_track_animation.
	function read_m2_array_array(data, ofs, dataType, storeOffsets = false, sequences = null) {
		const arrCount = data.readUInt32LE();
		const arrOfs = data.readUInt32LE();

		const base = data.offset;
		data.seek(ofs + arrOfs);

		const arr = Array(arrCount);
		const offsets = storeOffsets ? Array(arrCount) : null;

		for (let i = 0; i < arrCount; i++) {
			const subArrCount = data.readUInt32LE();
			const subArrOfs = data.readUInt32LE();
			const subBase = data.offset;

			if (storeOffsets)
				offsets[i] = { count: subArrCount, offset: subArrOfs };

			if (sequences && i < sequences.length && (sequences[i].flags & 0x20) === 0) {
				arr[i] = [];
				data.seek(subBase);
				continue;
			}

			data.seek(ofs + subArrOfs);

			arr[i] = Array(subArrCount);
			for (let j = 0; j < subArrCount; j++)
				arr[i][j] = read_value(data, dataType);

			data.seek(subBase);
		}

		data.seek(base);

		if (storeOffsets)
			return { arr, offsets };

		return arr;
	}

	function read_m2_track(data, ofs, dataType, storeOffsets = false, sequences = null) {
		const interpolation = data.readUInt16LE();
		const globalSeq = data.readUInt16LE();

		if (storeOffsets) {
			const ts = read_m2_array_array(data, ofs, 'uint32', true, sequences);
			const val = read_m2_array_array(data, ofs, dataType, true, sequences);
			return new M2Track(globalSeq, interpolation, ts.arr, val.arr, ts.offsets, val.offsets);
		}

		const timestamps = read_m2_array_array(data, ofs, 'uint32', false, sequences);
		const values = read_m2_array_array(data, ofs, dataType, false, sequences);
		return new M2Track(globalSeq, interpolation, timestamps, values);
	}

	const VALUE_SIZES = {
		uint32: 4, int16: 2, uint16: 2, float: 4, float2: 8, float3: 12, float4: 16, compquat: 8, uint8: 1,
		gravity: 4, gravity_c: 4
	};

	function value_size(dataType) {
		if (dataType.endsWith('~'))
			return 3 * (VALUE_SIZES[dataType.slice(0, -1)] ?? 0);
		return VALUE_SIZES[dataType] ?? 0;
	}

	function patch_track_animation(track, animIndex, animBuffer, valueType) {
		if (!track.timestampOffsets || !track.valueOffsets)
			return;

		if (animIndex >= track.timestampOffsets.length)
			return;

		const tsInfo = track.timestampOffsets[animIndex];
		const valInfo = track.valueOffsets[animIndex];

		const valElemSize = value_size(valueType);
		const tsEnd = tsInfo.offset + tsInfo.count * 4;
		const valEnd = valInfo.offset + valInfo.count * valElemSize;

		if (tsEnd > animBuffer.byteLength || valEnd > animBuffer.byteLength)
			return;

		const timestamps = Array(tsInfo.count);
		animBuffer.seek(tsInfo.offset);
		for (let j = 0; j < tsInfo.count; j++)
			timestamps[j] = animBuffer.readUInt32LE();
		track.timestamps[animIndex] = timestamps;

		const values = Array(valInfo.count);
		animBuffer.seek(valInfo.offset);
		for (let j = 0; j < valInfo.count; j++)
			values[j] = read_value(animBuffer, valueType);

		track.values[animIndex] = values;
	}

	function read_caa_bb(data) {
		return { min: data.readFloatLE(3), max: data.readFloatLE(3) };
	}

	// WoW is Z-up (X forward, Y left); WebGL here is Y-up.
	function convert_bone(bone) {
		for (const set of bone.translation.values) {
			for (const v of set) {
				const dy = v[1], dz = v[2];
				v[2] = dy * -1;
				v[1] = dz;
			}
		}
		for (const set of bone.rotation.values) {
			for (const v of set) {
				const dy = v[1], dz = v[2];
				v[2] = dy * -1;
				v[1] = dz;
			}
		}
		for (const set of bone.scale.values) {
			for (const v of set) {
				const dy = v[1], dz = v[2];
				v[2] = dy;
				v[1] = dz;
			}
		}
		const p = bone.pivot;
		const py = p[1], pz = p[2];
		p[2] = py * -1;
		p[1] = pz;
	}

	function convert_patched(bone, animIndex) {
		const translations = bone.translation.values[animIndex];
		if (translations) {
			for (const v of translations) {
				const dy = v[1], dz = v[2];
				v[2] = dy * -1;
				v[1] = dz;
			}
		}
		const rotations = bone.rotation.values[animIndex];
		if (rotations) {
			for (const v of rotations) {
				const dy = v[1], dz = v[2];
				v[2] = dy * -1;
				v[1] = dz;
			}
		}
		const scale = bone.scale.values[animIndex];
		if (scale) {
			for (const v of scale) {
				const dy = v[1], dz = v[2];
				v[2] = dy;
				v[1] = dz;
			}
		}
	}

	function read_bones(data, ofs, count, sequences) {
		const bones = Array(count);
		for (let i = 0; i < count; i++) {
			const bone = {
				boneID: data.readInt32LE(),
				flags: data.readUInt32LE(),
				parentBone: data.readInt16LE(),
				subMeshID: data.readUInt16LE(),
				boneNameCRC: data.readUInt32LE(),
				translation: read_m2_track(data, ofs, 'float3', true, sequences),
				rotation: read_m2_track(data, ofs, 'compquat', true, sequences),
				scale: read_m2_track(data, ofs, 'float3', true, sequences),
				pivot: data.readFloatLE(3)
			};
			convert_bone(bone);
			bones[i] = bone;
		}
		return bones;
	}

	function read_sequences(data, count) {
		const animations = new Array(count);
		for (let i = 0; i < count; i++) {
			animations[i] = {
				id: data.readUInt16LE(),
				variationIndex: data.readUInt16LE(),
				duration: data.readUInt32LE(),
				movespeed: data.readFloatLE(),
				flags: data.readUInt32LE(),
				frequency: data.readInt16LE(),
				padding: data.readUInt16LE(),
				replayMin: data.readUInt32LE(),
				replayMax: data.readUInt32LE(),
				blendTimeIn: data.readUInt16LE(),
				blendTimeOut: data.readUInt16LE(),
				boxPosMin: data.readFloatLE(3),
				boxPosMax: data.readFloatLE(3),
				boxRadius: data.readFloatLE(),
				variationNext: data.readInt16LE(),
				aliasNext: data.readUInt16LE()
			};
		}
		return animations;
	}

	function read_afid(data, chunkSize) {
		const entryCount = Math.floor(chunkSize / 8);
		const entries = new Array(entryCount);
		for (let i = 0; i < entryCount; i++) {
			entries[i] = {
				animID: data.readUInt16LE(),
				subAnimID: data.readUInt16LE(),
				fileDataID: data.readUInt32LE()
			};
		}
		return entries;
	}

	function get_attachment_by_id(lookup, attachments, attachmentId) {
		if (!lookup || attachmentId >= lookup.length)
			return null;

		const index = lookup[attachmentId];
		if (index < 0 || index >= attachments.length)
			return null;

		return attachments[index];
	}

	// Shared by M2Loader and SKELLoader: find the .anim of one sequence
	// (aliases resolved), load it and patch it into the bones.
	async function load_anims_for_index(owner, animation_index, fetch, isChunked) {
		if (owner.animFiles.has(animation_index))
			return true;

		if (!owner.animFileIDs || !owner.animations || animation_index >= owner.animations.length)
			return false;

		let animation = owner.animations[animation_index];
		let guard = 0;
		while ((animation.flags & 0x40) === 0x40 && guard++ < owner.animations.length)
			animation = owner.animations[animation.aliasNext];
		if (!animation)
			return false;

		// in the model or skeleton itself, not in an .anim
		if ((animation.flags & 0x20) === 0x20)
			return false;

		for (const entry of owner.animFileIDs) {
			if (entry.animID !== animation.id || entry.subAnimID !== animation.variationIndex)
				continue;

			if (entry.fileDataID === 0)
				return false;

			const file = await fetch(entry.fileDataID);
			if (!file)
				return false;

			const loader = new ANIMLoader(file);
			loader.load(isChunked);

			const bytes = loader.skeletonBoneData !== undefined ? loader.skeletonBoneData : loader.animData;
			if (!bytes)
				return false;

			owner.animFiles.set(animation_index, new Reader(bytes));
			owner._patch_bone_animation(animation_index);
			return true;
		}

		return false;
	}

	function patch_bones(bones, animIndex, animBuffer) {
		if (!animBuffer || !bones)
			return;

		for (const bone of bones) {
			patch_track_animation(bone.translation, animIndex, animBuffer, 'float3');
			patch_track_animation(bone.rotation, animIndex, animBuffer, 'compquat');
			patch_track_animation(bone.scale, animIndex, animBuffer, 'float3');
			convert_patched(bone, animIndex);
		}
	}

	// ------------------------------------------------------------------ emitters

	// An M2Track whose values may be spline keys (interpolation 2 or 3).
	// Sequences in an .anim are patched in later: the track and its value
	// type go into `ext`.
	function read_track_ext(data, ofs, dataType, sequences, ext) {
		const interpolation = data.view.getUint16(data.offset, true);
		const globalSeq = data.view.getUint16(data.offset + 2, true);
		const type = interpolation >= 2 ? dataType + '~' : dataType;
		// a global sequence's keys are read from the model itself, whatever
		// sequence 0's flags say (an assumption: wowdev.wiki is silent)
		if (globalSeq !== 0xFFFF)
			return read_m2_track(data, ofs, type, false, null);
		const track = read_m2_track(data, ofs, type, true, sequences);
		ext.push({ track, type });
		return track;
	}

	// A "fake animation block" (FBlock / M2PartTrack): fixed16 times over
	// a particle's life (0..32767) and their values, no sequences.
	function read_fblock(data, ofs, dataType) {
		const nTimes = data.readUInt32LE(), ofsTimes = data.readUInt32LE();
		const nKeys = data.readUInt32LE(), ofsKeys = data.readUInt32LE();
		const base = data.offset;
		const n = Math.min(nTimes, nKeys);
		const times = new Array(n), values = new Array(n);
		if (n > 0) {
			data.seek(ofs + ofsTimes);
			for (let i = 0; i < n; i++)
				times[i] = data.readInt16LE() / 32767;
			data.seek(ofs + ofsKeys);
			for (let i = 0; i < n; i++)
				values[i] = read_value(data, dataType);
		}
		data.seek(base);
		return { times, values };
	}

	const PARTICLE_SIZE = 0x1EC;
	const RIBBON_SIZE = 0xB0;

	function read_particle(data, ofs, sequences, ext) {
		const start = data.offset;
		const tr = (type) => read_track_ext(data, ofs, type, sequences, ext);
		const p = {};
		p.id = data.readInt32LE();
		p.flags = data.readUInt32LE();
		p.position = data.readFloatLE(3);           // WoW coordinates, relative to the bone
		p.bone = data.readUInt16LE();
		p.texture = data.readUInt16LE();            // 3 x 5 bits with flag 0x10000000
		data.move(16);                              // model / child emitter file names: not drawn
		p.blend = data.readUInt8();
		p.emitterType = data.readUInt8();           // 1 plane, 2 sphere, 3 spline, 4 bone
		p.colorIndex = data.readUInt16LE();         // ParticleColor.dbc: not applied
		p.multiScale = data.readUInt8(2).map(v => v / 32);
		p.priorityPlane = data.readInt16LE();
		p.rows = data.readUInt16LE();
		p.columns = data.readUInt16LE();
		p.speed = tr('float');
		p.speedVariation = tr('float');
		p.verticalRange = tr('float');
		p.horizontalRange = tr('float');
		p.gravity = tr((p.flags & 0x800000) ? 'gravity_c' : 'gravity');
		p.lifespan = tr('float');
		p.lifespanVariation = data.readFloatLE();
		p.rate = tr('float');
		p.rateVariation = data.readFloatLE();
		p.areaWidth = tr('float');
		p.areaLength = tr('float');
		p.zSource = tr('float');
		p.color = read_fblock(data, ofs, 'float3');  // 0..255
		p.alpha = read_fblock(data, ofs, 'int16');   // 0..32767
		p.scale = read_fblock(data, ofs, 'float2');
		p.scaleVariation = data.readFloatLE(2);
		p.headCell = read_fblock(data, ofs, 'uint16');
		p.tailCell = read_fblock(data, ofs, 'uint16');
		p.tailLength = data.readFloatLE();
		p.twinkleSpeed = data.readFloatLE();
		p.twinklePercent = data.readFloatLE();
		p.twinkleScale = data.readFloatLE(2);
		p.inheritVelocity = data.readFloatLE();
		p.drag = data.readFloatLE();
		p.baseSpin = data.readFloatLE();
		p.baseSpinVariation = data.readFloatLE();
		p.spin = data.readFloatLE();
		p.spinVariation = data.readFloatLE();
		data.move(24);                              // tumble: model particles only
		p.wind = data.readFloatLE(3);
		p.windTime = data.readFloatLE();
		data.move(16);                              // follow speed / scale
		data.move(8);                               // spline points
		p.enabled = tr('uint8');
		const fixed = () => data.readInt16LE(4).map(v => v / 512);
		p.scroll = fixed();
		p.scrollVariation = fixed();
		if (data.offset !== start + PARTICLE_SIZE)
			throw new Error('particle emitter size ' + (data.offset - start));
		return p;
	}

	function read_ribbon(data, ofs, sequences, ext) {
		const start = data.offset;
		const tr = (type) => read_track_ext(data, ofs, type, sequences, ext);
		const arr = () => {
			const n = data.readUInt32LE(), o = data.readUInt32LE();
			const base = data.offset;
			data.seek(ofs + o);
			const out = n > 0 ? data.readUInt16LE(Math.min(n, 64)) : [];
			data.seek(base);
			return out;
		};
		const r = {};
		r.id = data.readInt32LE();
		r.bone = data.readUInt32LE();
		r.position = data.readFloatLE(3);
		r.textures = arr();
		r.materials = arr();
		r.color = tr('float3');
		r.alpha = tr('int16');
		r.above = tr('float');
		r.below = tr('float');
		r.edgesPerSecond = data.readFloatLE();
		r.edgeLifetime = data.readFloatLE();
		r.gravity = data.readFloatLE();
		r.rows = data.readUInt16LE();
		r.columns = data.readUInt16LE();
		r.texSlot = tr('uint16');
		r.visibility = tr('uint8');
		r.priorityPlane = data.readInt16LE();
		data.move(2);                               // colour index, texture transform
		if (data.offset !== start + RIBBON_SIZE)
			throw new Error('ribbon emitter size ' + (data.offset - start));
		return r;
	}

	// ------------------------------------------------------------------ ANIMLoader

	// See the M2 page of wowdev.wiki, ".anim files"
	class ANIMLoader {
		constructor(data) {
			this.data = data;
		}

		load(isChunked = true) {
			const data = this.data;
			data.seek(0);

			if (!isChunked) {
				this.animData = data.readBytes(data.remainingBytes);
				return;
			}

			while (data.remainingBytes >= 8) {
				const chunkID = data.readUInt32LE();
				const chunkSize = data.readUInt32LE();
				const nextChunkPos = data.offset + chunkSize;

				switch (chunkID) {
					case CHUNK_AFM2: this.animData = data.readBytes(chunkSize); break;
					case CHUNK_AFSA: this.skeletonAttachmentData = data.readBytes(chunkSize); break;
					case CHUNK_AFSB: this.skeletonBoneData = data.readBytes(chunkSize); break;
				}

				data.seek(nextChunkPos);
			}
		}
	}

	// ------------------------------------------------------------------ Skin

	class Skin {
		constructor(fileDataID) {
			this.fileDataID = fileDataID;
			this.isLoaded = false;
		}

		load(data) {
			data.seek(0);
			const magic = data.readUInt32LE();
			if (magic !== MAGIC_SKIN)
				throw new Error('Invalid skin magic: ' + magic);

			const indicesCount = data.readUInt32LE();
			const indicesOfs = data.readUInt32LE();
			const trianglesCount = data.readUInt32LE();
			const trianglesOfs = data.readUInt32LE();
			const propertiesCount = data.readUInt32LE();
			const propertiesOfs = data.readUInt32LE();
			const subMeshesCount = data.readUInt32LE();
			const subMeshesOfs = data.readUInt32LE();
			const textureUnitsCount = data.readUInt32LE();
			const textureUnitsOfs = data.readUInt32LE();
			this.bones = data.readUInt32LE();

			data.seek(indicesOfs);
			this.indices = data.readUInt16LE(indicesCount);

			data.seek(trianglesOfs);
			this.triangles = data.readUInt16LE(trianglesCount);

			data.seek(propertiesOfs);
			this.properties = data.readUInt8(propertiesCount);

			data.seek(subMeshesOfs);
			this.subMeshes = new Array(subMeshesCount);
			for (let i = 0; i < subMeshesCount; i++) {
				const sm = this.subMeshes[i] = {
					submeshID: data.readUInt16LE(),
					level: data.readUInt16LE(),
					vertexStart: data.readUInt16LE(),
					vertexCount: data.readUInt16LE(),
					triangleStart: data.readUInt16LE(),
					triangleCount: data.readUInt16LE(),
					boneCount: data.readUInt16LE(),
					boneStart: data.readUInt16LE(),
					boneInfluences: data.readUInt16LE(),
					centerBoneIndex: data.readUInt16LE(),
					centerPosition: data.readFloatLE(3),
					sortCenterPosition: data.readFloatLE(3),
					sortRadius: data.readFloatLE()
				};

				sm.triangleStart += sm.level << 16;
			}

			data.seek(textureUnitsOfs);
			this.textureUnits = new Array(textureUnitsCount);
			for (let i = 0; i < textureUnitsCount; i++) {
				this.textureUnits[i] = {
					flags: data.readUInt8(),
					priority: data.readInt8(),
					shaderID: data.readUInt16LE(),
					skinSectionIndex: data.readUInt16LE(),
					flags2: data.readUInt16LE(),
					colorIndex: data.readUInt16LE(),
					materialIndex: data.readUInt16LE(),
					materialLayer: data.readUInt16LE(),
					textureCount: data.readUInt16LE(),
					textureComboIndex: data.readUInt16LE(),
					textureCoordComboIndex: data.readUInt16LE(),
					textureWeightComboIndex: data.readUInt16LE(),
					textureTransformComboIndex: data.readUInt16LE()
				};
			}

			this.isLoaded = true;
		}
	}

	// ------------------------------------------------------------------ SKELLoader

	// See the M2/.skel page of wowdev.wiki
	class SKELLoader {
		constructor(data) {
			this.data = data;
			this.animFiles = new Map();
		}

		load() {
			const data = this.data;
			data.seek(0);

			// SKB1/SKA1 are read after SKS1, so the sequences are known
			let skb1Ofs = -1;
			let ska1Ofs = -1;

			while (data.remainingBytes >= 8) {
				const chunkID = data.readUInt32LE();
				const chunkSize = data.readUInt32LE();
				const nextChunkPos = data.offset + chunkSize;

				switch (chunkID) {
					case CHUNK_SKB1: skb1Ofs = data.offset; break;
					case CHUNK_SKA1: ska1Ofs = data.offset; break;
					case CHUNK_SKPD: this.parse_chunk_skpd(); break;
					case CHUNK_SKS1: this.parse_chunk_sks1(); break;
					case CHUNK_AFID: this.animFileIDs = read_afid(data, chunkSize); break;
					case CHUNK_BFID: this.boneFileIDs = data.readUInt32LE(Math.floor(chunkSize / 4)); break;
				}

				data.seek(nextChunkPos);
			}

			if (skb1Ofs >= 0) {
				data.seek(skb1Ofs);
				this.parse_chunk_skb1();
			}

			if (ska1Ofs >= 0) {
				data.seek(ska1Ofs);
				this.parse_chunk_ska1();
			}

			this.globalLoops = this.globalLoops || [];
			this.animations = this.animations || [];
		}

		parse_chunk_skpd() {
			this.data.move(8);
			this.parent_skel_file_id = this.data.readUInt32LE();
			this.data.move(4);
		}

		parse_chunk_skb1() {
			const data = this.data;
			const chunk_ofs = data.offset;

			const bone_count = data.readUInt32LE();
			const bone_ofs = data.readUInt32LE();

			const base_ofs = data.offset;
			data.seek(chunk_ofs + bone_ofs);
			this.bones = read_bones(data, chunk_ofs, bone_count, this.animations);
			data.seek(base_ofs);
		}

		parse_chunk_sks1() {
			const data = this.data;
			const chunk_ofs = data.offset;

			const globalLoopCount = data.readUInt32LE();
			const globalLoopOfs = data.readUInt32LE();
			let prevPos = data.offset;
			data.seek(globalLoopOfs + chunk_ofs);
			this.globalLoops = data.readInt32LE(globalLoopCount);
			data.seek(prevPos);

			const animationCount = data.readUInt32LE();
			const animationOfs = data.readUInt32LE();
			prevPos = data.offset;
			data.seek(animationOfs + chunk_ofs);
			this.animations = read_sequences(data, animationCount);
			data.seek(prevPos);

			const animationLookupCount = data.readUInt32LE();
			const animationLookupOfs = data.readUInt32LE();
			prevPos = data.offset;
			data.seek(animationLookupOfs + chunk_ofs);
			this.animationLookup = data.readInt16LE(animationLookupCount);
			data.seek(prevPos);

			data.move(8);
		}

		parse_chunk_ska1() {
			const data = this.data;
			const chunk_ofs = data.offset;

			const attachmentCount = data.readUInt32LE();
			const attachmentOfs = data.readUInt32LE();
			const attachmentLookupCount = data.readUInt32LE();
			const attachmentLookupOfs = data.readUInt32LE();

			let prevPos = data.offset;
			data.seek(attachmentOfs + chunk_ofs);
			this.attachments = new Array(attachmentCount);
			for (let i = 0; i < attachmentCount; i++) {
				this.attachments[i] = {
					id: data.readUInt32LE(),
					bone: data.readUInt16LE(),
					unknown: data.readUInt16LE(),
					position: data.readFloatLE(3),
					animateAttached: read_m2_track(data, chunk_ofs, 'uint8', false, this.animations)
				};
			}
			data.seek(prevPos);

			prevPos = data.offset;
			data.seek(attachmentLookupOfs + chunk_ofs);
			this.attachmentLookup = data.readInt16LE(attachmentLookupCount);
			data.seek(prevPos);
		}

		getAttachmentById(attachmentId) {
			return get_attachment_by_id(this.attachmentLookup, this.attachments || [], attachmentId);
		}

		loadAnimsForIndex(animation_index, fetch) {
			return load_anims_for_index(this, animation_index, fetch, true);
		}

		_patch_bone_animation(animIndex) {
			patch_bones(this.bones, animIndex, this.animFiles.get(animIndex));
		}
	}

	// ------------------------------------------------------------------ M2Loader

	class M2Loader {
		constructor(data) {
			this.data = data;
			this.animFiles = new Map();
			this.skins = [];
			this.lodSkins = [];
		}

		load() {
			const data = this.data;
			data.seek(0);

			let found = false;
			while (data.remainingBytes >= 8) {
				const chunkID = data.readUInt32LE();
				const chunkSize = data.readUInt32LE();
				const nextChunkPos = data.offset + chunkSize;

				switch (chunkID) {
					case MAGIC_MD21: this.parseChunk_MD21(); found = true; break;
					case CHUNK_SFID: this.parseChunk_SFID(chunkSize); break;
					case CHUNK_TXID: this.parseChunk_TXID(); break;
					case CHUNK_SKID: this.skeletonFileID = data.readUInt32LE(); break;
					case CHUNK_BFID: this.boneFileIDs = data.readUInt32LE(Math.floor(chunkSize / 4)); break;
					case CHUNK_AFID: this.animFileIDs = read_afid(data, chunkSize); break;
				}

				data.seek(nextChunkPos);
			}

			if (!found)
				throw new Error('Not a chunked (MD21) M2 model');
		}

		loadAnimsForIndex(animationIndex, fetch) {
			const chunked = (this.flags & 0x200000) === 0x200000 || this.skeletonFileID > 0;
			return load_anims_for_index(this, animationIndex, fetch, chunked);
		}

		_patch_bone_animation(animIndex) {
			const buf = this.animFiles.get(animIndex);
			patch_bones(this.bones, animIndex, buf);
			if (buf) {
				for (const e of this.castEvents || []) {
					const entry = e.times.offsets[animIndex];
					if (!entry?.count || entry.offset + 4*entry.count > buf.byteLength) continue;
					buf.seek(entry.offset);
					e.times.arr[animIndex] = buf.readUInt32LE(entry.count);
				}
				for (const { track, type } of this.emitterTracks || [])
					patch_track_animation(track, animIndex, buf, type);
			}
		}

		parseChunk_SFID(chunkSize) {
			if (this.viewCount === undefined)
				throw new Error('Cannot parse SFID chunk in M2 before MD21 chunk!');

			const lodSkinCount = Math.max(0, Math.floor(chunkSize / 4) - this.viewCount);
			this.skins = new Array(this.viewCount);
			this.lodSkins = new Array(lodSkinCount);

			for (let i = 0; i < this.viewCount; i++)
				this.skins[i] = new Skin(this.data.readUInt32LE());

			for (let i = 0; i < lodSkinCount; i++)
				this.lodSkins[i] = new Skin(this.data.readUInt32LE());
		}

		parseChunk_TXID() {
			if (this.textures === undefined)
				throw new Error('Cannot parse TXID chunk in M2 before MD21 chunk!');

			for (let i = 0, n = this.textures.length; i < n; i++)
				this.textures[i].fileDataID = this.data.readUInt32LE();
		}

		parseChunk_MD21() {
			const ofs = this.data.offset;

			const magic = this.data.readUInt32LE();
			if (magic !== MAGIC_MD20)
				throw new Error('Invalid M2 magic: ' + magic);

			this.version = this.data.readUInt32LE();
			this.parseChunk_MD21_modelName(ofs);
			this.flags = this.data.readUInt32LE();
			this.globalLoops = this.read_array(ofs, (n) => this.data.readInt32LE(n));
			this.animations = this.read_array(ofs, (n) => read_sequences(this.data, n));
			this.animationLookup = this.read_array(ofs, (n) => this.data.readInt16LE(n));
			this.md21Ofs = ofs;
			this.bones = this.read_array(ofs, (n) => read_bones(this.data, ofs, n, this.animations));
			this.data.move(8); // key bone lookup
			this.parseChunk_MD21_vertices(ofs);
			this.viewCount = this.data.readUInt32LE();
			this.colors = this.read_array(ofs, (n) => {
				const colors = new Array(n);
				for (let i = 0; i < n; i++) {
					colors[i] = {
						color: read_m2_track(this.data, ofs, 'float3', false, this.animations),
						alpha: read_m2_track(this.data, ofs, 'int16', false, this.animations)
					};
				}
				return colors;
			});
			this.parseChunk_MD21_textures(ofs);
			this.textureWeights = this.read_array(ofs, (n) => {
				const weights = new Array(n);
				for (let i = 0; i < n; i++)
					weights[i] = read_m2_track(this.data, ofs, 'int16', false, this.animations);
				return weights;
			});
			this.textureTransforms = this.read_array(ofs, (n) => {
				const transforms = new Array(n);
				for (let i = 0; i < n; i++) {
					transforms[i] = {
						translation: read_m2_track(this.data, ofs, 'float3', false, this.animations),
						rotation: read_m2_track(this.data, ofs, 'float4', false, this.animations),
						scaling: read_m2_track(this.data, ofs, 'float3', false, this.animations)
					};
				}
				return transforms;
			});
			this.replaceableTextureLookup = this.read_array(ofs, (n) => this.data.readInt16LE(n));
			this.materials = this.read_array(ofs, (n) => {
				const materials = new Array(n);
				for (let i = 0; i < n; i++)
					materials[i] = { flags: this.data.readUInt16LE(), blendingMode: this.data.readUInt16LE() };
				return materials;
			});
			this.data.move(2 * 4); // boneCombos
			this.textureCombos = this.read_array(ofs, (n) => this.data.readUInt16LE(n));
			this.data.move(8); // textureTransformBoneMap
			this.transparencyLookup = this.read_array(ofs, (n) => this.data.readUInt16LE(n));
			this.textureTransformsLookup = this.read_array(ofs, (n) => this.data.readUInt16LE(n));
			this.parseChunk_MD21_collision(ofs);
			this.attachments = this.read_array(ofs, (n) => {
				const entries = new Array(n);
				for (let i = 0; i < n; i++) {
					entries[i] = {
						id: this.data.readUInt32LE(),
						bone: this.data.readUInt16LE(),
						unknown: this.data.readUInt16LE(),
						position: this.data.readFloatLE(3),
						animateAttached: read_m2_track(this.data, ofs, 'uint8', false, this.animations)
					};
				}
				return entries;
			});
			this.attachmentLookup = this.read_array(ofs, (n) => this.data.readInt16LE(n));
			this.castEvents = this.read_array(ofs, count => {
				const d = this.data, events = [];
				for (let i = 0; i < count; i++) {
					const id = d.readString(4);
					d.move(4);
					const bone = d.readUInt32LE(), position = d.readFloatLE(3);
					d.move(4); // interpolation, global sequence
					if (['$BWR', '$CST', '$CSR', '$CSL'].includes(id)) {
						const times = read_m2_array_array(d, ofs, 'uint32', true, this.animations);
						events.push({ id, bone, position, times });
					} else d.move(8);
				}
				return events;
			});
			this.data.move(3 * 8); // lights, cameras, camera lookup
			this.parseChunk_MD21_emitters(ofs);
		}

		// A broken emitter costs the model its emitters, never the model.
		parseChunk_MD21_emitters(ofs) {
			this.ribbons = [];
			this.particles = [];
			this.emitterTracks = [];
			const ext = [];
			try {
				const read = (fn, cap) => this.read_array(ofs, (n) => {
					const out = [];
					for (let i = 0; i < Math.min(n, cap); i++)
						out.push(fn(this.data, ofs, this.animations, ext));
					return out;
				});
				const ribbons = read(read_ribbon, 32);
				const particles = read(read_particle, 64);
				this.ribbons = ribbons;
				this.particles = particles;
				this.emitterTracks = ext;
			} catch (e) {
				this.emitterError = String((e && e.message) || e);
			}
		}

		// An M2Array (count, offset from the MD20 header) read by `fn(count)`.
		read_array(ofs, fn) {
			const count = this.data.readUInt32LE();
			const offset = this.data.readUInt32LE();
			const base = this.data.offset;
			this.data.seek(offset + ofs);
			const out = fn(count);
			this.data.seek(base);
			return out;
		}

		parseChunk_MD21_modelName(ofs) {
			const len = this.data.readUInt32LE();
			const nameOfs = this.data.readUInt32LE();
			const base = this.data.offset;
			this.data.seek(nameOfs + ofs);
			// followed by one NUL
			this.name = this.data.readString(len - 1);
			this.data.seek(base);
		}

		parseChunk_MD21_vertices(ofs) {
			const verticesCount = this.data.readUInt32LE();
			const verticesOfs = this.data.readUInt32LE();

			const base = this.data.offset;
			this.data.seek(verticesOfs + ofs);
			if (verticesOfs + ofs + verticesCount * 48 > this.data.byteLength)
				throw new RangeError('vertices past the end of the file');

			const d = this.data;
			const vertices = this.vertices = new Float32Array(verticesCount * 3);
			const normals = this.normals = new Float32Array(verticesCount * 3);
			const uv = this.uv = new Float32Array(verticesCount * 2);
			const uv2 = this.uv2 = new Float32Array(verticesCount * 2);
			const boneWeights = this.boneWeights = new Uint8Array(verticesCount * 4);
			const boneIndices = this.boneIndices = new Uint8Array(verticesCount * 4);

			for (let i = 0; i < verticesCount; i++) {
				vertices[i * 3] = d.readFloatLE();
				vertices[i * 3 + 2] = d.readFloatLE() * -1;
				vertices[i * 3 + 1] = d.readFloatLE();

				for (let x = 0; x < 4; x++)
					boneWeights[i * 4 + x] = d.readUInt8();

				for (let x = 0; x < 4; x++)
					boneIndices[i * 4 + x] = d.readUInt8();

				normals[i * 3] = d.readFloatLE();
				normals[i * 3 + 2] = d.readFloatLE() * -1;
				normals[i * 3 + 1] = d.readFloatLE();

				uv[i * 2] = d.readFloatLE();
				uv[i * 2 + 1] = d.readFloatLE();

				uv2[i * 2] = d.readFloatLE();
				uv2[i * 2 + 1] = d.readFloatLE();
			}

			this.data.seek(base);
		}

		parseChunk_MD21_textures(ofs) {
			const texturesCount = this.data.readUInt32LE();
			const texturesOfs = this.data.readUInt32LE();

			const base = this.data.offset;
			this.data.seek(texturesOfs + ofs);

			const textures = this.textures = new Array(texturesCount);
			const textureTypes = this.textureTypes = new Array(texturesCount);

			// The legacy file name (type 0, name offset set) needs a listfile:
			// skipped, as the TXID chunk gives every id.
			for (let i = 0; i < texturesCount; i++) {
				textureTypes[i] = this.data.readUInt32LE();
				textures[i] = { flags: this.data.readUInt32LE(), fileDataID: 0 };
				this.data.move(8);
			}

			this.data.seek(base);
		}

		parseChunk_MD21_collision(ofs) {
			this.boundingBox = read_caa_bb(this.data);
			this.boundingSphereRadius = this.data.readFloatLE();
			this.collisionBox = read_caa_bb(this.data);
			this.collisionSphereRadius = this.data.readFloatLE();
			// indices, positions, normals: not drawn
			this.data.move(3 * 8);
			void ofs;
		}

		getAttachmentById(attachmentId) {
			return get_attachment_by_id(this.attachmentLookup, this.attachments || [], attachmentId);
		}
	}

	M3D.m2 = { M2Track, read_m2_track, read_m2_array_array, patch_track_animation, read_caa_bb };
	M3D.ANIMLoader = ANIMLoader;
	M3D.Skin = Skin;
	M3D.SKELLoader = SKELLoader;
	M3D.M2Loader = M2Loader;
})();
