/*!
	wow.export (https://github.com/Kruithne/wow.export)
	Authors: Kruithne <kruithne@gmail.com>, Marlamin <marlamin@marlamin.com>
	License: MIT (LICENSE-wow.export.txt)
	Ported for wow-stats: the shaders wow.export loads from disk
	(src/shaders/m2.vertex.shader, m2.fragment.shader, char.vertex.shader,
	char.fragment.shader), as strings, and 3D/ShaderMapper.js.
 */
'use strict';

(() => {
	const M3D = globalThis.M3D = globalThis.M3D || {};

	const M2_VERT = `#version 300 es
precision highp float;
precision highp int;

// vertex attributes
layout(location = 0) in vec3 a_position;
layout(location = 1) in vec3 a_normal;
layout(location = 2) in uvec4 a_bone_indices;
layout(location = 3) in vec4 a_bone_weights;
layout(location = 4) in vec2 a_texcoord;
layout(location = 5) in vec2 a_texcoord2;

// uniforms
uniform mat4 u_view_matrix;
uniform mat4 u_projection_matrix;
uniform mat4 u_model_matrix;
uniform vec3 u_view_up;
uniform float u_time;

// bone matrices (max 256 bones)
uniform int u_bone_count;
#define MAX_BONES 256
uniform VsBoneUbo {
	mat4 u_bone_matrices[MAX_BONES];
};
// texture transform matrices
uniform mat4 u_tex_matrix1;
uniform mat4 u_tex_matrix2;

// vertex shader mode
uniform int u_vertex_shader;

// outputs
out vec2 v_texcoord;
out vec2 v_texcoord2;
out vec2 v_texcoord3;
out vec3 v_normal;
out vec3 v_position_view;
out float v_edge_fade;

// calculate environment map coords from view position and normal
vec2 calc_env_coord(vec3 pos_view, vec3 normal_view) {
	vec3 r = reflect(normalize(pos_view), normalize(normal_view));
	float m = 2.0 * sqrt(r.x * r.x + r.y * r.y + (r.z + 1.0) * (r.z + 1.0));
	return vec2(r.x / m + 0.5, r.y / m + 0.5);
}

// edge fade calculation
float calc_edge_fade(vec3 pos_view, vec3 normal_view) {
	vec3 view_dir = normalize(-pos_view);
	float n_dot_v = abs(dot(normalize(normal_view), view_dir));
	return clamp(n_dot_v * n_dot_v, 0.0, 1.0);
}

void main() {
	// bone skinning
	mat4 bone_transform = mat4(1.0);

	// note: legacy m2 bone skinning disabled until animation system is fixed
	if (u_bone_count > 0) {
		float total_weight = dot(a_bone_weights, vec4(1.0));
		if (total_weight > 0.0) {
			bone_transform = mat4(0.0);
			bone_transform += a_bone_weights.x * u_bone_matrices[a_bone_indices.x];
			bone_transform += a_bone_weights.y * u_bone_matrices[a_bone_indices.y];
			bone_transform += a_bone_weights.z * u_bone_matrices[a_bone_indices.z];
			bone_transform += a_bone_weights.w * u_bone_matrices[a_bone_indices.w];
		}
	}

	// transform position
	vec4 skinned_pos = bone_transform * vec4(a_position, 1.0);
	vec4 world_pos = u_model_matrix * skinned_pos;
	vec4 view_pos = u_view_matrix * world_pos;

	gl_Position = u_projection_matrix * view_pos;

	// transform normal
	mat3 normal_matrix = transpose(inverse(mat3(u_view_matrix * u_model_matrix * bone_transform)));
	vec3 normal_view = normalize(normal_matrix * a_normal);

	v_normal = normal_view;
	v_position_view = view_pos.xyz;

	// calculate texture coordinates based on vertex shader mode
	vec2 env_coord = calc_env_coord(view_pos.xyz, normal_view);
	float edge_scan = calc_edge_fade(view_pos.xyz, normal_view);
	v_edge_fade = 1.0;

	// apply texture matrix transforms
	mat4 tex_mat1 = u_tex_matrix1;
	mat4 tex_mat2 = u_tex_matrix2;

	v_texcoord = a_texcoord;
	v_texcoord2 = vec2(0.0);
	v_texcoord3 = vec2(0.0);

	switch (u_vertex_shader) {
		case 0: // Diffuse_T1
			v_texcoord = (tex_mat1 * vec4(a_texcoord, 0.0, 1.0)).xy;
			break;

		case 1: // Diffuse_Env
			v_texcoord = env_coord;
			break;

		case 2: // Diffuse_T1_T2
			v_texcoord = (tex_mat1 * vec4(a_texcoord, 0.0, 1.0)).xy;
			v_texcoord2 = (tex_mat2 * vec4(a_texcoord2, 0.0, 1.0)).xy;
			break;

		case 3: // Diffuse_T1_Env
			v_texcoord = (tex_mat1 * vec4(a_texcoord, 0.0, 1.0)).xy;
			v_texcoord2 = env_coord;
			break;

		case 4: // Diffuse_Env_T1
			v_texcoord = env_coord;
			v_texcoord2 = (tex_mat1 * vec4(a_texcoord, 0.0, 1.0)).xy;
			break;

		case 5: // Diffuse_Env_Env
			v_texcoord = env_coord;
			v_texcoord2 = env_coord;
			break;

		case 6: // Diffuse_T1_Env_T1
			v_texcoord = (tex_mat1 * vec4(a_texcoord, 0.0, 1.0)).xy;
			v_texcoord2 = env_coord;
			v_texcoord3 = (tex_mat1 * vec4(a_texcoord, 0.0, 1.0)).xy;
			break;

		case 7: // Diffuse_T1_T1
			v_texcoord = (tex_mat1 * vec4(a_texcoord, 0.0, 1.0)).xy;
			v_texcoord2 = (tex_mat1 * vec4(a_texcoord, 0.0, 1.0)).xy;
			break;

		case 8: // Diffuse_T1_T1_T1
			v_texcoord = (tex_mat1 * vec4(a_texcoord, 0.0, 1.0)).xy;
			v_texcoord2 = (tex_mat1 * vec4(a_texcoord, 0.0, 1.0)).xy;
			v_texcoord3 = (tex_mat1 * vec4(a_texcoord, 0.0, 1.0)).xy;
			break;

		case 9: // Diffuse_EdgeFade_T1
			v_edge_fade = edge_scan;
			v_texcoord = (tex_mat1 * vec4(a_texcoord, 0.0, 1.0)).xy;
			break;

		case 10: // Diffuse_T2
			v_texcoord = (tex_mat2 * vec4(a_texcoord2, 0.0, 1.0)).xy;
			break;

		case 11: // Diffuse_T1_Env_T2
			v_texcoord = (tex_mat1 * vec4(a_texcoord, 0.0, 1.0)).xy;
			v_texcoord2 = env_coord;
			v_texcoord3 = (tex_mat2 * vec4(a_texcoord2, 0.0, 1.0)).xy;
			break;

		case 12: // Diffuse_EdgeFade_T1_T2
			v_edge_fade = edge_scan;
			v_texcoord = (tex_mat1 * vec4(a_texcoord, 0.0, 1.0)).xy;
			v_texcoord2 = (tex_mat2 * vec4(a_texcoord2, 0.0, 1.0)).xy;
			break;

		case 13: // Diffuse_EdgeFade_Env
			v_edge_fade = edge_scan;
			v_texcoord = env_coord;
			break;

		case 14: // Diffuse_T1_T2_T1
			v_texcoord = (tex_mat1 * vec4(a_texcoord, 0.0, 1.0)).xy;
			v_texcoord2 = (tex_mat2 * vec4(a_texcoord2, 0.0, 1.0)).xy;
			v_texcoord3 = (tex_mat1 * vec4(a_texcoord, 0.0, 1.0)).xy;
			break;

		case 15: // Diffuse_T1_T2_T3
			v_texcoord = (tex_mat1 * vec4(a_texcoord, 0.0, 1.0)).xy;
			v_texcoord2 = (tex_mat2 * vec4(a_texcoord2, 0.0, 1.0)).xy;
			v_texcoord3 = a_texcoord2;
			break;

		case 16: // Color_T1_T2_T3
			v_texcoord = (tex_mat2 * vec4(a_texcoord2, 0.0, 1.0)).xy;
			v_texcoord2 = vec2(0.0);
			v_texcoord3 = a_texcoord2;
			break;

		case 17: // BW_Diffuse_T1
		case 18: // BW_Diffuse_T1_T2
			v_texcoord = (tex_mat1 * vec4(a_texcoord, 0.0, 1.0)).xy;
			break;

		default:
			v_texcoord = a_texcoord;
			break;
	}
}
`;

	const M2_FRAG = `#version 300 es
precision highp float;
precision highp int;

// inputs from vertex shader
in vec2 v_texcoord;
in vec2 v_texcoord2;
in vec2 v_texcoord3;
in vec3 v_normal;
in vec3 v_position_view;
in float v_edge_fade;

// textures
uniform sampler2D u_texture1;
uniform sampler2D u_texture2;
uniform sampler2D u_texture3;
uniform sampler2D u_texture4;

// material parameters
uniform int u_pixel_shader;
uniform int u_blend_mode;
uniform vec4 u_mesh_color;        // vertex color + alpha
uniform vec3 u_tex_sample_alpha;  // texture weight values
uniform float u_alpha_test;       // alpha test threshold (0.502 for mode 1)

// lighting (simplified for preview)
uniform vec3 u_ambient_color;
uniform vec3 u_diffuse_color;
uniform vec3 u_light_dir;
uniform int u_apply_lighting;

// wireframe mode
uniform int u_wireframe;
uniform vec4 u_wireframe_color;

// output
out vec4 frag_color;

// calculate diffuse lighting
vec3 calc_lighting(vec3 color, vec3 normal) {
	if (u_apply_lighting == 0)
		return color;

	vec3 n = normalize(normal);
	float n_dot_l = max(dot(n, normalize(-u_light_dir)), 0.0);

	vec3 ambient = u_ambient_color * color;
	vec3 diffuse = u_diffuse_color * color * n_dot_l;

	return ambient + diffuse;
}

void main() {
	if (u_wireframe != 0) {
		frag_color = u_wireframe_color;
		return;
	}

	// use correct UV coordinates based on shader mode
	vec2 uv1 = v_texcoord;
	vec2 uv2 = v_texcoord2;
	vec2 uv3 = v_texcoord3;

	// special case: shaders 26, 27, 28 use uv1 for all texture samples
	if (u_pixel_shader == 26 || u_pixel_shader == 27 || u_pixel_shader == 28) {
		uv2 = uv1;
		uv3 = uv1;
	}

	vec4 tex1 = texture(u_texture1, uv1);
	vec4 tex2 = texture(u_texture2, uv2);
	vec4 tex3 = texture(u_texture3, uv3);
	vec4 tex4 = texture(u_texture4, v_texcoord2);

	vec3 mesh_color = u_mesh_color.rgb;
	float mesh_opacity = u_mesh_color.a * v_edge_fade;

	vec3 mat_diffuse = vec3(0.0);
	vec3 specular = vec3(0.0);
	float discard_alpha = 1.0;
	bool can_discard = false;

	// Blizzard M2 combiner shaders, reference: the M2 page of wowdev.wiki, "Shaders"
	switch (u_pixel_shader) {
		case 0: // Combiners_Opaque
			mat_diffuse = mesh_color * tex1.rgb;
			break;

		case 1: // Combiners_Mod
			mat_diffuse = mesh_color * tex1.rgb;
			discard_alpha = tex1.a;
			can_discard = true;
			break;

		case 2: // Combiners_Opaque_Mod
			mat_diffuse = mesh_color * tex1.rgb * tex2.rgb;
			discard_alpha = tex2.a;
			can_discard = true;
			break;

		case 3: // Combiners_Opaque_Mod2x
			mat_diffuse = mesh_color * tex1.rgb * tex2.rgb * 2.0;
			discard_alpha = tex2.a * 2.0;
			can_discard = true;
			break;

		case 4: // Combiners_Opaque_Mod2xNA
			mat_diffuse = mesh_color * tex1.rgb * tex2.rgb * 2.0;
			break;

		case 5: // Combiners_Opaque_Opaque
			mat_diffuse = mesh_color * tex1.rgb * tex2.rgb;
			break;

		case 6: // Combiners_Mod_Mod
			mat_diffuse = mesh_color * tex1.rgb * tex2.rgb;
			discard_alpha = tex1.a * tex2.a;
			can_discard = true;
			break;

		case 7: // Combiners_Mod_Mod2x
			mat_diffuse = mesh_color * tex1.rgb * tex2.rgb * 2.0;
			discard_alpha = tex1.a * tex2.a * 2.0;
			can_discard = true;
			break;

		case 8: // Combiners_Mod_Add
			mat_diffuse = mesh_color * tex1.rgb;
			discard_alpha = tex1.a + tex2.a;
			can_discard = true;
			specular = tex2.rgb;
			break;

		case 9: // Combiners_Mod_Mod2xNA
			mat_diffuse = mesh_color * tex1.rgb * tex2.rgb * 2.0;
			discard_alpha = tex1.a;
			can_discard = true;
			break;

		case 10: // Combiners_Mod_AddNA
			mat_diffuse = mesh_color * tex1.rgb;
			discard_alpha = tex1.a;
			can_discard = true;
			specular = tex2.rgb;
			break;

		case 11: // Combiners_Mod_Opaque
			mat_diffuse = mesh_color * tex1.rgb * tex2.rgb;
			discard_alpha = tex1.a;
			can_discard = true;
			break;

		case 12: // Combiners_Opaque_Mod2xNA_Alpha
			mat_diffuse = mesh_color * mix(tex1.rgb * tex2.rgb * 2.0, tex1.rgb, vec3(tex1.a));
			break;

		case 13: // Combiners_Opaque_AddAlpha
			mat_diffuse = mesh_color * tex1.rgb;
			specular = tex2.rgb * tex2.a;
			break;

		case 14: // Combiners_Opaque_AddAlpha_Alpha
			mat_diffuse = mesh_color * tex1.rgb;
			specular = tex2.rgb * tex2.a * (1.0 - tex1.a);
			break;

		case 15: // Combiners_Opaque_Mod2xNA_Alpha_Add
			mat_diffuse = mesh_color * mix(tex1.rgb * tex2.rgb * 2.0, tex1.rgb, vec3(tex1.a));
			specular = tex3.rgb * tex3.a * u_tex_sample_alpha.b;
			break;

		case 16: // Combiners_Mod_AddAlpha
			mat_diffuse = mesh_color * tex1.rgb;
			discard_alpha = tex1.a;
			can_discard = true;
			specular = tex2.rgb * tex2.a;
			break;

		case 17: // Combiners_Mod_AddAlpha_Alpha
			mat_diffuse = mesh_color * tex1.rgb;
			discard_alpha = tex1.a + tex2.a * (0.3 * tex2.r + 0.59 * tex2.g + 0.11 * tex2.b);
			can_discard = true;
			specular = tex2.rgb * tex2.a * (1.0 - tex1.a);
			break;

		case 18: // Combiners_Opaque_Alpha_Alpha
			mat_diffuse = mesh_color * mix(mix(tex1.rgb, tex2.rgb, vec3(tex2.a)), tex1.rgb, vec3(tex1.a));
			break;

		case 19: // Combiners_Opaque_Mod2xNA_Alpha_3s
			mat_diffuse = mesh_color * mix(tex1.rgb * tex2.rgb * 2.0, tex3.rgb, vec3(tex3.a));
			break;

		case 20: // Combiners_Opaque_AddAlpha_Wgt
			mat_diffuse = mesh_color * tex1.rgb;
			specular = tex2.rgb * tex2.a * u_tex_sample_alpha.g;
			break;

		case 21: // Combiners_Mod_Add_Alpha
			mat_diffuse = mesh_color * tex1.rgb;
			discard_alpha = tex1.a + tex2.a;
			can_discard = true;
			specular = tex2.rgb * (1.0 - tex1.a);
			break;

		case 22: // Combiners_Opaque_ModNA_Alpha
			mat_diffuse = mesh_color * mix(tex1.rgb * tex2.rgb, tex1.rgb, vec3(tex1.a));
			break;

		case 23: // Combiners_Mod_AddAlpha_Wgt
			mat_diffuse = mesh_color * tex1.rgb;
			discard_alpha = tex1.a;
			can_discard = true;
			specular = tex2.rgb * tex2.a * u_tex_sample_alpha.g;
			break;

		case 24: // Combiners_Opaque_Mod_Add_Wgt
			mat_diffuse = mesh_color * mix(tex1.rgb, tex2.rgb, vec3(tex2.a));
			specular = tex1.rgb * tex1.a * u_tex_sample_alpha.r;
			break;

		case 25: // Combiners_Opaque_Mod2xNA_Alpha_UnshAlpha
			{
				float glow_opacity = clamp(tex3.a * u_tex_sample_alpha.b, 0.0, 1.0);
				mat_diffuse = mesh_color * mix(tex1.rgb * tex2.rgb * 2.0, tex1.rgb, vec3(tex1.a)) * (1.0 - glow_opacity);
				specular = tex3.rgb * glow_opacity;
			}
			break;

		case 26: // Combiners_Mod_Dual_Crossfade
			{
				vec4 mixed = mix(mix(tex1, tex2, vec4(clamp(u_tex_sample_alpha.g, 0.0, 1.0))), tex3, vec4(clamp(u_tex_sample_alpha.b, 0.0, 1.0)));
				mat_diffuse = mesh_color * mixed.rgb;
				discard_alpha = mixed.a;
				can_discard = true;
			}
			break;

		case 27: // Combiners_Opaque_Mod2xNA_Alpha_Alpha
			mat_diffuse = mesh_color * mix(mix(tex1.rgb * tex2.rgb * 2.0, tex3.rgb, vec3(tex3.a)), tex1.rgb, vec3(tex1.a));
			break;

		case 28: // Combiners_Mod_Masked_Dual_Crossfade
			{
				vec4 mixed = mix(mix(tex1, tex2, vec4(clamp(u_tex_sample_alpha.g, 0.0, 1.0))), tex3, vec4(clamp(u_tex_sample_alpha.b, 0.0, 1.0)));
				mat_diffuse = mesh_color * mixed.rgb;
				discard_alpha = mixed.a * tex4.a;
				can_discard = true;
			}
			break;

		case 29: // Combiners_Opaque_Alpha
			mat_diffuse = mesh_color * mix(tex1.rgb, tex2.rgb, vec3(tex2.a));
			break;

		case 30: // Guild
			{
				vec3 generic0 = vec3(1.0);
				vec3 generic1 = vec3(1.0);
				vec3 generic2 = vec3(1.0);
				mat_diffuse = mesh_color * mix(tex1.rgb * mix(generic0, tex2.rgb * generic1, vec3(tex2.a)), tex3.rgb * generic2, vec3(tex3.a));
				discard_alpha = tex1.a;
				can_discard = true;
			}
			break;

		case 31: // Guild_NoBorder
			{
				vec3 generic0 = vec3(1.0);
				vec3 generic1 = vec3(1.0);
				mat_diffuse = mesh_color * tex1.rgb * mix(generic0, tex2.rgb * generic1, vec3(tex2.a));
				discard_alpha = tex1.a;
				can_discard = true;
			}
			break;

		case 32: // Guild_Opaque
			{
				vec3 generic0 = vec3(1.0);
				vec3 generic1 = vec3(1.0);
				vec3 generic2 = vec3(1.0);
				mat_diffuse = mesh_color * mix(tex1.rgb * mix(generic0, tex2.rgb * generic1, vec3(tex2.a)), tex3.rgb * generic2, vec3(tex3.a));
			}
			break;

		case 33: // Combiners_Mod_Depth
			mat_diffuse = mesh_color * tex1.rgb;
			discard_alpha = tex1.a;
			can_discard = true;
			break;

		case 34: // Illum
			discard_alpha = tex1.a;
			can_discard = true;
			break;

		case 35: // Combiners_Mod_Mod_Mod_Const
			{
				vec4 generic0 = vec4(1.0);
				vec4 combined = tex1 * tex2 * tex3 * generic0;
				mat_diffuse = mesh_color * combined.rgb;
				discard_alpha = combined.a;
				can_discard = true;
			}
			break;

		case 36: // Combiners_Mod_Mod_Depth
			mat_diffuse = mesh_color * tex1.rgb * tex2.rgb;
			discard_alpha = tex1.a * tex2.a;
			can_discard = true;
			break;

		default:
			mat_diffuse = mesh_color * tex1.rgb;
			break;
	}

	// calculate final opacity based on blend mode
	float final_opacity;
	bool do_discard = false;

	if (u_blend_mode == 13) {
		// constant alpha blend
		final_opacity = discard_alpha * mesh_opacity;
	} else if (u_blend_mode == 1) {
		// alpha key - hard cutoff
		final_opacity = mesh_opacity;
		if (can_discard && discard_alpha < u_alpha_test)
			do_discard = true;
	} else if (u_blend_mode == 0) {
		// opaque
		final_opacity = mesh_opacity;
	} else if (u_blend_mode == 4 || u_blend_mode == 5) {
		// MOD and MOD2X blend modes: discard low alpha pixels to prevent holdout
		// these modes multiply destination by source, so low alpha = black = destroys destination
		final_opacity = discard_alpha * mesh_opacity;
		if (can_discard && discard_alpha < u_alpha_test)
			do_discard = true;
	} else {
		// other blend modes
		final_opacity = discard_alpha * mesh_opacity;
	}

	if (do_discard)
		discard;

	// apply lighting
	vec3 lit_color = calc_lighting(mat_diffuse, v_normal);

	// what the combiners keep apart (env map, glow): wow.export left it out "for debugging"
	lit_color += specular;

	frag_color = vec4(lit_color, final_opacity);
}
`;

	const CHAR_VERT = `attribute vec4 a_position;
attribute vec2 a_texCoord;

varying vec2 v_texCoord;

void main() {
	gl_Position = a_position;
	v_texCoord = a_texCoord;
}
`;

	const CHAR_FRAG = `precision mediump float;
varying vec2 v_texCoord;
uniform sampler2D u_baseTexture;
uniform sampler2D u_texture;
uniform float u_blendMode;

void main() {
	if(u_blendMode == 0.0 || u_blendMode == 1.0 || u_blendMode == 9.0 || u_blendMode == 15.0) {
		gl_FragColor = texture2D(u_texture, v_texCoord);
	}else if(u_blendMode == 4.0) { // MULTIPLY
		vec4 base = texture2D(u_baseTexture, v_texCoord);
		vec4 blend = texture2D(u_texture, v_texCoord);
		gl_FragColor = base * blend;
	} else if (u_blendMode == 7.0) { // SCREEN
		vec4 base = texture2D(u_baseTexture, v_texCoord);
		vec4 blend = texture2D(u_texture, v_texCoord);
		vec4 result;
		result.rgb = 1.0 - (1.0 - base.rgb) * (1.0 - blend.rgb);
		result.a = blend.a;
		gl_FragColor = result;
	} else if (u_blendMode == 6.0) { // OVERLAY
		vec4 base = texture2D(u_baseTexture, v_texCoord);
		vec4 blend = texture2D(u_texture, v_texCoord);
		vec4 result;
		result.r = (blend.r < 0.5) ? (2.0 * base.r * blend.r) : (1.0 - 2.0 * (1.0 - base.r) * (1.0 - blend.r));
		result.g = (blend.g < 0.5) ? (2.0 * base.g * blend.g) : (1.0 - 2.0 * (1.0 - base.g) * (1.0 - blend.g));
		result.b = (blend.b < 0.5) ? (2.0 * base.b * blend.b) : (1.0 - 2.0 * (1.0 - base.b) * (1.0 - blend.b));
		result.a = blend.a;
		gl_FragColor = result;
	}else{
		gl_FragColor = vec4(1.0, 0.0, 1.0, 1.0);
	}
}
`;

	const SHADER_ARRAY = [
	{ "PS": "Combiners_Opaque_Mod2xNA_Alpha",           "VS": "Diffuse_T1_Env",         "HS": "T1_T2",    "DS": "T1_T2"     },
	{ "PS": "Combiners_Opaque_AddAlpha",                "VS": "Diffuse_T1_Env",         "HS": "T1_T2",    "DS": "T1_T2"     },
	{ "PS": "Combiners_Opaque_AddAlpha_Alpha",          "VS": "Diffuse_T1_Env",         "HS": "T1_T2",    "DS": "T1_T2"     },
	{ "PS": "Combiners_Opaque_Mod2xNA_Alpha_Add",       "VS": "Diffuse_T1_Env_T1",      "HS": "T1_T2_T3", "DS": "T1_T2_T3"  },
	{ "PS": "Combiners_Mod_AddAlpha",                   "VS": "Diffuse_T1_Env",         "HS": "T1_T2",    "DS": "T1_T2"     },
	{ "PS": "Combiners_Opaque_AddAlpha",                "VS": "Diffuse_T1_T1",          "HS": "T1_T2",    "DS": "T1_T2"     },
	{ "PS": "Combiners_Mod_AddAlpha",                   "VS": "Diffuse_T1_T1",          "HS": "T1_T2",    "DS": "T1_T2"     },
	{ "PS": "Combiners_Mod_AddAlpha_Alpha",             "VS": "Diffuse_T1_Env",         "HS": "T1_T2",    "DS": "T1_T2"     },
	{ "PS": "Combiners_Opaque_Alpha_Alpha",             "VS": "Diffuse_T1_Env",         "HS": "T1_T2",    "DS": "T1_T2"     },
	{ "PS": "Combiners_Opaque_Mod2xNA_Alpha_3s",        "VS": "Diffuse_T1_Env_T1",      "HS": "T1_T2_T3", "DS": "T1_T2_T3"  },
	{ "PS": "Combiners_Opaque_AddAlpha_Wgt",            "VS": "Diffuse_T1_T1",          "HS": "T1_T2",    "DS": "T1_T2"     },
	{ "PS": "Combiners_Mod_Add_Alpha",                  "VS": "Diffuse_T1_Env",         "HS": "T1_T2",    "DS": "T1_T2"     },
	{ "PS": "Combiners_Opaque_ModNA_Alpha",             "VS": "Diffuse_T1_Env",         "HS": "T1_T2",    "DS": "T1_T2"     },
	{ "PS": "Combiners_Mod_AddAlpha_Wgt",               "VS": "Diffuse_T1_Env",         "HS": "T1_T2",    "DS": "T1_T2"     },
	{ "PS": "Combiners_Mod_AddAlpha_Wgt",               "VS": "Diffuse_T1_T1",          "HS": "T1_T2",    "DS": "T1_T2"     },
	{ "PS": "Combiners_Opaque_AddAlpha_Wgt",            "VS": "Diffuse_T1_T2",          "HS": "T1_T2",    "DS": "T1_T2"     },
	{ "PS": "Combiners_Opaque_Mod_Add_Wgt",             "VS": "Diffuse_T1_Env",         "HS": "T1_T2",    "DS": "T1_T2"     },
	{ "PS": "Combiners_Opaque_Mod2xNA_Alpha_UnshAlpha", "VS": "Diffuse_T1_Env_T1",      "HS": "T1_T2_T3", "DS": "T1_T2_T3"  },
	{ "PS": "Combiners_Mod_Dual_Crossfade",             "VS": "Diffuse_T1",             "HS": "T1",       "DS": "T1"        },
	{ "PS": "Combiners_Mod_Depth",                      "VS": "Diffuse_EdgeFade_T1",    "HS": "T1",       "DS": "T1"        },
	{ "PS": "Combiners_Opaque_Mod2xNA_Alpha_Alpha",     "VS": "Diffuse_T1_Env_T2",      "HS": "T1_T2_T3", "DS": "T1_T2_T3"  },
	{ "PS": "Combiners_Mod_Mod",                        "VS": "Diffuse_EdgeFade_T1_T2", "HS": "T1_T2",    "DS": "T1_T2"     },
	{ "PS": "Combiners_Mod_Masked_Dual_Crossfade",      "VS": "Diffuse_T1_T2",          "HS": "T1_T2",    "DS": "T1_T2"     },
	{ "PS": "Combiners_Opaque_Alpha",                   "VS": "Diffuse_T1_T1",          "HS": "T1_T2",    "DS": "T1_T2"     },
	{ "PS": "Combiners_Opaque_Mod2xNA_Alpha_UnshAlpha", "VS": "Diffuse_T1_Env_T2",      "HS": "T1_T2_T3", "DS": "T1_T2_T3"  },
	{ "PS": "Combiners_Mod_Depth",                      "VS": "Diffuse_EdgeFade_Env",   "HS": "T1",       "DS": "T1"        },
	{ "PS": "Guild",                                    "VS": "Diffuse_T1_T2_T1",       "HS": "T1_T2_T3", "DS": "T1_T2"     },
	{ "PS": "Guild_NoBorder",                           "VS": "Diffuse_T1_T2",          "HS": "T1_T2",    "DS": "T1_T2_T3"  },
	{ "PS": "Guild_Opaque",                             "VS": "Diffuse_T1_T2_T1",       "HS": "T1_T2_T3", "DS": "T1_T2"     },
	{ "PS": "Illum",                                    "VS": "Diffuse_T1_T1",          "HS": "T1_T2",    "DS": "T1_T2"     },
	{ "PS": "Combiners_Mod_Mod_Mod_Const",              "VS": "Diffuse_T1_T2_T3",       "HS": "T1_T2_T3", "DS": "T1_T2_T3"  },
	{ "PS": "Combiners_Mod_Mod_Mod_Const",              "VS": "Color_T1_T2_T3",         "HS": "T1_T2_T3", "DS": "T1_T2_T3"  },
	{ "PS": "Combiners_Opaque",                         "VS": "Diffuse_T1",             "HS": "T1",       "DS": "T1"        },
	{ "PS": "Combiners_Mod_Mod2x",                      "VS": "Diffuse_EdgeFade_T1_T2", "HS": "T1_T2",    "DS": "T1_T2"     },
	{ "PS": "Combiners_Mod",                            "VS": "Diffuse_EdgeFade_T1",    "HS": "T1_T2",    "DS": "T1_T2"     },
	{ "PS": "Combiners_Mod_Mod_Depth",                  "VS": "Diffuse_EdgeFade_T1_T2", "HS": "T1_T2",    "DS": "T1_T2"     },
];



/**
 * Gets Vertex shader name from shader ID
 */
const getVertexShader = (textureCount, shaderID) => {
	if (shaderID & 0x8000) {
		const vertexShaderId = shaderID & 0x7FFF;
		if (vertexShaderId >= SHADER_ARRAY.length) {
			return null;
		}

		return SHADER_ARRAY[vertexShaderId].VS;
	}
	else {
		if (textureCount == 1) {
			if (shaderID & 0x80) {
				return "Diffuse_Env";
			} else {
				if (shaderID & 0x4000)
					return "Diffuse_T2";
				else
					return "Diffuse_T1";
			}
		} else {
			if (shaderID & 0x80) {
				if (shaderID & 0x8)
					return "Diffuse_Env_Env";
				else 
					return "Diffuse_Env_T1";
			} else {
				if (shaderID & 0x8) {
					return "Diffuse_T1_Env";
				} else {
					if (shaderID & 0x4000)
						return "Diffuse_T1_T2";
					else
						return "Diffuse_T1_T1";
				}
			}
		}
	}
};

/**
 * Gets Pixel shader name from shader ID
 */
const getPixelShader = (textureCount, shaderID) => {
	if (shaderID & 0x8000) {
		const pixelShaderID = shaderID & 0x7FFF;
		if (pixelShaderID >= SHADER_ARRAY.length) {
			return null;
		}

		return SHADER_ARRAY[pixelShaderID].PS;
	}
	else if (textureCount == 1)
	{
		return (shaderID & 0x70) ? "Combiners_Mod" : "Combiners_Opaque";
	}
	else 
	{
		if (shaderID & 0x70) {
			switch (shaderID & 7) {
				case 3:
					return "Combiners_Mod_Add";
				case 4:
					return "Combiners_Mod_Mod2x";
				case 6:
					return "Combiners_Mod_Mod2xNA";
				case 7:
					return "Combiners_Mod_AddNA";
				default:
					return "Combiners_Mod_Mod";
			}
		}
		else {
			switch (shaderID & 7) {
				case 0:
					return "Combiners_Opaque_Opaque";
				case 3:
				case 7:
					return "Combiners_Opaque_AddAlpha";
				case 4:
					return "Combiners_Opaque_Mod2x";
				case 6:
					return "Combiners_Opaque_Mod2xNA";
				default:
					return "Combiners_Opaque_Mod";
			}
		}
	}
};


	M3D.shaders = {
		m2: { vert: M2_VERT, frag: M2_FRAG },
		char: { vert: CHAR_VERT, frag: CHAR_FRAG }
	};
	M3D.ShaderMapper = { getVertexShader, getPixelShader };
})();
