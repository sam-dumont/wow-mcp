// Prepare and render the manual's videos.
//
//   node scripts/render.mjs            # every video, both themes -> out/<name>-<mode>.mp4
//   node scripts/render.mjs --prepare  # only the inputs (then `npm run studio`)
//   node scripts/render.mjs --publish  # also copy the videos and the tapes into docs/videos/media
//
// Inputs: demo/record.sh's footage in public/ and the site's fonts
// (web/fonts), copied into public/; the recordings' lengths and the browser
// takes' marks go to src/footage.json. Renders one at a time (the Mac is shared).
import {execFileSync} from 'node:child_process';
import {copyFileSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync} from 'node:fs';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';

const here = dirname(dirname(fileURLToPath(import.meta.url)));
const repo = join(here, '..', '..');
const pub = join(here, 'public');
const media = join(repo, 'docs', 'videos', 'media');

mkdirSync(join(pub, 'fonts'), {recursive: true});
for (const f of ['cinzel.woff2', 'alegreya-sans-500.woff2', 'alegreya-sans-700.woff2']) copyFileSync(join(repo, 'web', 'fonts', f), join(pub, 'fonts', f));

const durations = {};
for (const f of readdirSync(pub).filter((f) => /\.(mp4|webm)$/.test(f))) {
	const out = execFileSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', join(pub, f)]).toString().trim();
	durations[f.replace(/\.(mp4|webm)$/, '')] = Number(out);
}
const marks = {};
for (const f of readdirSync(pub).filter((f) => /-marks-(dark|light)\.json$/.test(f))) {
	marks[f.replace('-marks-', '-').replace('.json', '')] = JSON.parse(readFileSync(join(pub, f), 'utf8'));
}
writeFileSync(join(here, 'src', 'footage.json'), JSON.stringify({durations, marks}, null, 1) + '\n');
console.log('footage:', Object.keys(durations).join(', '));
if (process.argv.includes('--prepare')) process.exit(0);

const only = process.env.VIDEOS ? process.env.VIDEOS.split(',') : ['onboarding', 'tour', 'settings', 'private'];
mkdirSync(join(here, 'out'), {recursive: true});
const mb = (f) => (statSync(f).size / 1e6).toFixed(2);
for (const name of only) {
	for (const mode of ['dark', 'light']) {
		const id = `${name}-${mode}`;
		const file = join(here, 'out', `${id}.mp4`);
		// Mostly still pages: a high CRF keeps them small and the text sharp.
		// The parchment's grain costs bits, so a long video goes up a step
		// until it fits under 4.8 MB (the tour at 33 still reads cleanly).
		for (let crf = 30; ; crf += 3) {
			execFileSync('npx', ['remotion', 'render', 'src/index.ts', id, file, '--codec', 'h264', '--crf', String(crf), '--x264-preset', 'slow', '--concurrency', '4', '--log', 'error'], {cwd: here, stdio: 'inherit'});
			if (statSync(file).size <= 4.8e6 || crf >= 36) break;
		}
		// faststart: a browser plays before the whole file is in.
		execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', file, '-c', 'copy', '-movflags', '+faststart', file + '.tmp.mp4']);
		copyFileSync(file + '.tmp.mp4', file);
		rmSync(file + '.tmp.mp4');
		console.log('rendered', id, `${mb(file)} MB`);
	}
}

if (process.argv.includes('--publish')) {
	// The site's clips: the composed videos, and the terminal tapes as VHS
	// made them (re-muxed for faststart).
	mkdirSync(media, {recursive: true});
	for (const name of only) for (const mode of ['dark', 'light']) copyFileSync(join(here, 'out', `${name}-${mode}.mp4`), join(media, `${name}-${mode}.mp4`));
	for (const f of readdirSync(pub).filter((f) => /^(install|status|update|uninstall)-mac-(dark|light)\.mp4$/.test(f))) {
		execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', join(pub, f), '-c', 'copy', '-movflags', '+faststart', join(media, f)]);
	}
	for (const f of readdirSync(media).filter((f) => f.endsWith('.mp4'))) console.log('media', f, `${mb(join(media, f))} MB`);
}
