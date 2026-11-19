import React from 'react';
import {Composition, continueRender, delayRender, Series} from 'remotion';
import footage from './footage.json';
import {Cue, Footage, H, Title, W} from './parts';
import {loadFonts, Mode} from './theme';

// footage.json: each recording's length in seconds and each browser take's
// marks (the second each beat starts), written by scripts/render.mjs from
// demo/record.sh's output.
type Marks = Record<string, number>;
const F = footage as unknown as {durations: Record<string, number>; marks: Record<string, Marks>};

const FPS = 30;
const TITLE = 60;
const s = (sec: number) => Math.round(sec * FPS);
const dur = (name: string) => F.durations[name] ?? 10;
const marks = (take: string, mode: Mode): Marks => F.marks[`${take}-${mode}`] ?? {end: 10};

type Scene = {frames: number; el: React.ReactNode};

// A stretch of a browser take, from one mark to another (seconds of slack
// either side), captioned at the marks.
const cut = (mode: Mode, take: string, a: number, b: number, cues: Cue[]): Scene => ({
	frames: s(b - a),
	el: <Footage mode={mode} src={`${take}-${mode}.webm`} from={a} to={b} cues={cues} frames={s(b - a)} />,
});

const onboarding = (mode: Mode): Scene[] => {
	const m = marks('wizard', mode);
	const tape = dur(`install-mac-${mode}`);
	return [
		{frames: TITLE, el: <Title mode={mode} title="Your WoW page in 15 minutes" subtitle="The addons, one command, a few minutes of play. Then your page tells you what to do next." frames={TITLE} />},
		cut(mode, 'wizard', Math.max(0, m.welcome - 0.3), m.install + 2.2, [
			{at: 0, text: 'Log in with the account you were sent. Your page\'s address comes with it'},
			{at: m.addons, text: 'Install Polestar from the CurseForge app. The command adds Polestar Recorder'},
			{at: m.install - 1.2, text: 'Copy your install command: it works once, for 15 minutes'},
		]),
		{frames: s(tape), el: <Footage mode={mode} terminal src={`install-mac-${mode}.mp4`} cues={[
			{at: 0, text: 'Paste it in Terminal (Mac) or PowerShell (Windows). No admin rights'},
			{at: Math.max(1, tape - 6), text: 'It checks your setup, then sends your data every 5 minutes'},
		]} frames={s(tape)} />},
		cut(mode, 'wizard', m.data - 1.2, m.end, [
			{at: 0, text: 'Play a few minutes, then /reload: the data arrives by itself'},
			{at: m.character - 0.5, text: 'Your characters show up'},
			{at: m.ai - 0.3, text: 'Optional: connect Claude or ChatGPT to your page'},
			{at: m.page - 0.6, text: 'Your page: where you stand, and what to do next'},
		]),
	];
};

const tour = (mode: Mode): Scene[] => {
	const m = marks('tour', mode);
	return [
		{frames: TITLE, el: <Title mode={mode} title="Your page" subtitle="Where you stand against Polestar's route, what to do next, and everything you played." frames={TITLE} />},
		cut(mode, 'tour', Math.max(0, m.overview - 0.4), m.end, [
			{at: 0, text: 'Overview: your pace against the route, and the next level'},
			{at: m.moves, text: 'Next moves: what to do now, where, and what blocks it'},
			{at: m.character, text: 'Character: your gear, professions and talents'},
			{at: m.upgrades, text: 'Upgrades per slot, and where each one drops'},
			{at: m.dungeons, text: 'Dungeons: the drops you would wear, and your runs'},
			{at: m.plan, text: 'Plan your next hours: pick 1 to 6, click Plan it'},
			{at: m.planned, text: 'The plan follows your guide at your own pace'},
			{at: m.timeline, text: 'Timeline: every level, death, dungeon and session'},
			{at: m.backintime, text: 'Back in time: your gear and zones at each level'},
			{at: m.journal, text: 'Journal: one entry per play session'},
		]),
	];
};

const settings = (mode: Mode): Scene[] => {
	const m = marks('settings', mode);
	return [
		cut(mode, 'settings', Math.max(0, m.settings - 0.3), m.end, [
			{at: 0, text: 'Settings: what others see. Your own page always shows everything'},
			{at: m.hidden - 1.2, text: 'Hide a character from the public page or the zogzog link'},
			{at: m.public - 1.4, text: 'Switch your public page on'},
			{at: m.password, text: 'New computer? Reset the upload password'},
			{at: m.reset, text: 'Then run the new install command on that computer'},
		]),
	];
};

const priv = (mode: Mode): Scene[] => {
	const m = marks('private', mode);
	return [
		cut(mode, 'private', Math.max(0, m.link - 0.3), m.end, [
			{at: 0, text: 'Your zogzog link is in Settings: copy it, send it to friends'},
			{at: m.zogzog - 0.6, text: 'They see your page, your gear advice and your deaths per zone'},
			{at: m.journal - 0.3, text: 'And your journal. Never other players, never where you are'},
		]),
	];
};

const VIDEOS = {onboarding, tour, settings, private: priv};

const Video: React.FC<{mode: Mode; video: string}> = ({mode, video}) => {
	const scenes = VIDEOS[video as keyof typeof VIDEOS];
	const [handle] = React.useState(() => delayRender('fonts'));
	React.useEffect(() => {
		loadFonts().then(() => continueRender(handle));
	}, [handle]);
	return (
		<Series>
			{scenes(mode).map((sc, i) => (
				<Series.Sequence key={i} durationInFrames={sc.frames}>
					{sc.el}
				</Series.Sequence>
			))}
		</Series>
	);
};

const total = (fn: (m: Mode) => Scene[], mode: Mode) => fn(mode).reduce((a, sc) => a + sc.frames, 0);

export const Root: React.FC = () => (
	<>
		{(Object.keys(VIDEOS) as (keyof typeof VIDEOS)[]).flatMap((name) =>
			(['dark', 'light'] as Mode[]).map((mode) => (
				<Composition key={`${name}-${mode}`} id={`${name}-${mode}`} component={Video} defaultProps={{mode, video: name}} durationInFrames={Math.max(1, total(VIDEOS[name], mode))} fps={FPS} width={W} height={H} />
			)),
		)}
	</>
);
