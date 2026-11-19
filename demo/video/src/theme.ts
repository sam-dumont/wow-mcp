// The site's palette and fonts (web/app.css, web/fonts): Cinzel for titles,
// Alegreya Sans for text, the same night and day as the page and the tapes.
import {loadFont} from '@remotion/fonts';
import {staticFile} from 'remotion';

export type Mode = 'dark' | 'light';

export const palette = {
	dark: {bg: '#16110C', panel: '#251B13', rule: '#4A3826', text: '#EFE4CA', muted: '#B09C7A', accent: '#C99A3B'},
	light: {bg: '#E6D5AE', panel: '#F1E4C4', rule: '#C9AF7C', text: '#2C1F12', muted: '#6A5234', accent: '#8A5F12'},
} as const;

export const fonts = {title: 'Cinzel', body: 'Alegreya Sans'};

export const loadFonts = () =>
	Promise.all([
		loadFont({family: 'Cinzel', url: staticFile('fonts/cinzel.woff2'), weight: '400 900'}),
		loadFont({family: 'Alegreya Sans', url: staticFile('fonts/alegreya-sans-500.woff2'), weight: '500'}),
		loadFont({family: 'Alegreya Sans', url: staticFile('fonts/alegreya-sans-700.woff2'), weight: '700'}),
	]);
