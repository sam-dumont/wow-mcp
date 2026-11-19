import React from 'react';
import {AbsoluteFill, interpolate, OffthreadVideo, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {fonts, Mode, palette} from './theme';

export const W = 1280;
export const H = 800;
const STAGE = 720; // the footage area; the caption band is below it

const fade = (frame: number, total: number, edge = 8) =>
	interpolate(frame, [0, edge, total - edge, total], [0, 1, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});

export const Title: React.FC<{mode: Mode; title: string; subtitle: string; frames: number}> = ({mode, title, subtitle, frames}) => {
	const p = palette[mode];
	const f = useCurrentFrame();
	const {fps} = useVideoConfig();
	const rise = spring({frame: f, fps, config: {damping: 200}});
	return (
		<AbsoluteFill style={{background: p.bg, justifyContent: 'center', padding: '0 130px', opacity: fade(f, frames)}}>
			<div style={{fontFamily: fonts.title, fontWeight: 700, fontSize: 62, color: p.accent, letterSpacing: 1, transform: `translateY(${(1 - rise) * 20}px)`}}>
				{title}
			</div>
			<div style={{width: 120, height: 2, background: p.accent, opacity: 0.6, margin: '22px 0'}} />
			<div style={{fontFamily: fonts.body, fontWeight: 500, fontSize: 34, color: p.text, maxWidth: 980, lineHeight: 1.35}}>{subtitle}</div>
		</AbsoluteFill>
	);
};

export type Cue = {at: number; text: string};

// The caption band under the footage: the cue whose time has come, faded in.
const Band: React.FC<{mode: Mode; cues: Cue[]; t: number}> = ({mode, cues, t}) => {
	const p = palette[mode];
	const i = cues.reduce((k, c, j) => (c.at <= t ? j : k), 0);
	const since = t - cues[i].at;
	const o = interpolate(since, [0, 0.25], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
	return (
		<div style={{position: 'absolute', left: 0, right: 0, top: STAGE, height: H - STAGE, background: p.panel, borderTop: `2px solid ${p.accent}`, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '0 40px'}}>
			<div style={{fontFamily: fonts.body, fontWeight: 700, fontSize: 31, color: p.text, opacity: o, textAlign: 'center', lineHeight: 1.2}}>{cues[i].text}</div>
		</div>
	);
};

// A recording, full width above the caption band. `from`/`to` trim it
// (seconds); cue times are seconds into the recording, like the marks.
export const Footage: React.FC<{mode: Mode; src: string; from?: number; to?: number; cues: Cue[]; frames: number; terminal?: boolean}> = ({mode, src, from = 0, to, cues, frames, terminal}) => {
	const p = palette[mode];
	const f = useCurrentFrame();
	const {fps} = useVideoConfig();
	const t = from + f / fps;
	const video = (
		<OffthreadVideo src={staticFile(src)} muted trimBefore={Math.round(from * fps)} trimAfter={to ? Math.round(to * fps) : undefined}
			style={terminal ? {width: 1180, height: 640, borderRadius: 10, border: `1px solid ${p.rule}`} : {width: W, height: STAGE, display: 'block'}} />
	);
	return (
		<AbsoluteFill style={{background: p.bg, opacity: fade(f, frames, 5)}}>
			{terminal ? <div style={{position: 'absolute', left: (W - 1180) / 2, top: (STAGE - 640) / 2}}>{video}</div> : video}
			<Band mode={mode} cues={cues} t={t} />
		</AbsoluteFill>
	);
};
