// Leveling log: one page, no build step, no outside scripts. Every value
// that came from the game (player names, zones, guide names) goes in with
// textContent, never as HTML: other people choose their names.
//
// The whole file is one function: its names (`$` above all) stay out of
// the page's global scope, which other scripts on the full page share.
(() => {
'use strict';

const SVGNS = 'http://www.w3.org/2000/svg';
const $ = (sel) => document.querySelector(sel);
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  setAttrs(el, attrs);
  append(el, kids);
  return el;
}
function s(tag, attrs, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  setAttrs(el, attrs);
  append(el, kids);
  return el;
}
function setAttrs(el, attrs) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'text') el.textContent = v;
    else if (k === 'class') el.setAttribute('class', v);
    else if (k === 'width%') el.style.width = v + '%';
    else el.setAttribute(k, v === true ? '' : v);
  }
}
function append(el, kids) {
  for (const k of kids.flat(Infinity)) {  // nested lists of parts read as one
    if (k === null || k === undefined || k === false) continue;
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
}

// ------------------------------------------------------------ formatting

const nf = new Intl.NumberFormat('en-GB');
const fmtNum = (n) => (n === null || n === undefined ? '?' : nf.format(Math.round(n)));
function fmtK(n) {
  if (n === null || n === undefined) return '?';
  return n >= 10000 ? (n / 1000).toFixed(1).replace(/\.0$/, '') + 'k' : fmtNum(n);
}
function dur(sec) {
  if (sec === null || sec === undefined) return '?';
  sec = Math.round(sec);
  if (sec < 60) return sec + 's';
  const m = Math.round(sec / 60);
  if (m < 60) return m + 'm';
  const hh = Math.floor(m / 60);
  const mm = m % 60;
  return mm ? `${hh}h ${String(mm).padStart(2, '0')}m` : `${hh}h`;
}
const dayFmt = new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
const timeFmt = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit' });
const shortDay = new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric' });
const toDate = (t) => (t ? new Date(t) : null);
function when(t) {
  const d = toDate(t);
  return d ? `${dayFmt.format(d)}, ${timeFmt.format(d)}` : '?';
}
function day(t) {
  const d = toDate(t);
  return d ? dayFmt.format(d) : '?';
}
function ago(t) {
  const d = toDate(t);
  if (!d) return '';
  const min = (Date.now() - d.getTime()) / 60000;
  if (min < 2) return 'just now';
  if (min < 60) return `${Math.round(min)} min ago`;
  if (min < 24 * 60) return `${Math.round(min / 60)} h ago`;
  if (min < 48 * 60) return 'yesterday';
  return `${Math.round(min / 1440)} days ago`;
}
function coins(copper) {
  if (copper === null || copper === undefined) return h('span', { class: 'coins', text: 'unknown' });
  const g = Math.floor(copper / 10000);
  const sv = Math.floor((copper % 10000) / 100);
  const c = Math.round(copper % 100);
  const label = `${g} gold, ${sv} silver, ${c} copper`;
  return h('span', { class: 'coins', 'aria-label': label, title: label },
    g ? h('span', { class: 'coin g', text: fmtNum(g) }) : null,
    g || sv ? h('span', { class: 'coin s', text: sv }) : null,
    h('span', { class: 'coin c', text: c }));
}

// ------------------------------------------------------------ state

const state = { chars: [], char: null, data: null, market: null, config: { view: 'public' }, tab: 'overview', journal: null, advice: null, adviceOrder: 'gain' };
const feat = (k) => Boolean(state.config && state.config[k]);
// The public view: the card, the climb, the dungeons and the level snapshots,
// nothing else (wowstats/redact.py). Its answers carry only those fields.
const PUBLIC = document.documentElement.dataset.view === 'public';

// The public site is a static bundle (wow-stats publish): the same page,
// with every answer saved as a file at /data/<what>/<name>[/<level>].json.
const STATIC = document.documentElement.dataset.static === '1';
function api(what, params) {
  const p = Object.fromEntries(Object.entries(params || {}).filter(([, v]) => v !== null && v !== undefined && v !== ''));
  if (!STATIC) {
    const q = new URLSearchParams(p).toString();
    return '/api/' + what + (q ? '?' + q : '');
  }
  const parts = [what, ...['name', 'level'].filter((k) => k in p).map((k) => encodeURIComponent(p[k]))];
  return 'data/' + parts.join('/') + '.json';  // relative: the bundle may live under a prefix
}
window.wowApi = api;

// The full-content bundle seals its data (AES-256-GCM, wowstats/publish.py):
// the key is the link's fragment (#...), which the browser never sends. It
// stays in the address bar so the link can be bookmarked and shared.
const SEALED = document.documentElement.dataset.sealed === '1';
const KEY = SEALED ? location.hash.slice(1) : '';
window.wowKey = KEY;
// A pasted link that only changes the fragment doesn't reload the page.
if (SEALED) window.addEventListener('hashchange', () => location.reload());
// So the section links (#levels, #dungeons...) jump by script: following one
// would put "levels" where the key was, and the reload above would then
// lock the page ("Nothing here.") on a plain click.
if (SEALED) {
  document.addEventListener('click', (ev) => {
    const a = ev.target.closest && ev.target.closest('a[href^="#"]');
    if (!a || ev.defaultPrevented) return;  // a move's link: goTo already went
    ev.preventDefault();
    const t = document.getElementById(a.getAttribute('href').slice(1));
    if (t) t.scrollIntoView({ block: 'start', behavior: reduceMotion ? 'auto' : 'smooth' });
  });
}
let cryptoKey = null;
async function openBytes(path, buf) {
  if (!cryptoKey) {
    const raw = Uint8Array.from(atob(KEY.replace(/-/g, '+').replace(/_/g, '/')), (c) => c.charCodeAt(0));
    cryptoKey = await crypto.subtle.importKey('raw', raw, 'AES-GCM', false, ['decrypt']);
  }
  const b = new Uint8Array(buf);
  return new Uint8Array(await crypto.subtle.decrypt(
    { name: 'AES-GCM', iv: b.slice(0, 12), additionalData: new TextEncoder().encode(path) }, cryptoKey, b.slice(12)));
}
// The item icons (web/items.js) are sealed data files too.
window.wowOpenBytes = openBytes;
async function openSealed(path, buf) {
  return JSON.parse(new TextDecoder().decode(await openBytes(path, buf)));
}
function locked() {
  document.body.replaceChildren(h('main', { class: 'locked' }, h('p', { text: 'Nothing here.' })));
  document.title = 'Leveling log';
}

async function getJSON(url) {
  if (SEALED) {
    const r = await fetch(url + '.enc', { credentials: 'same-origin' });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return openSealed(url, await r.arrayBuffer());
  }
  const r = await fetch(url, { headers: { Accept: 'application/json' }, credentials: 'same-origin' });
  let body = null;
  try { body = await r.json(); } catch (e) { body = null; }
  if (!r.ok) {
    // A login in front (reverse proxy, OIDC) answers a lapsed session with a
    // redirect fetch can't follow across origins: reload so the browser logs in.
    if (r.type === 'opaqueredirect' || r.status === 0) location.reload();
    throw new Error((body && body.error) || `${r.status} ${r.statusText}`);
  }
  return body;
}
window.wowGetJSON = getJSON;

// replaceChildren, minus the nulls left by conditional pieces.
function fill(el, ...kids) {
  el.replaceChildren();
  append(el, kids);
}
const fill_ = fill;

// A character Polestar doesn't track (only Syndicator or its WTF folder know
// it): every Polestar section says so, instead of "wait for the next run".
function untrackedNote(body) {
  const name = state.data ? state.data.header.name : 'This character';
  fill(body, h('p', { class: 'empty' }, h('strong', { text: 'Polestar doesn’t track ' + name + '.' }), ' ',
    'Tick Polestar and Polestar Recorder for this character in AddOns at character select, play, then /reload.'));
}

// An empty section says what to do next, never where its data comes from
// (owner, 2026-11-19: nobody needs to read that; /help/ and the docs have it).
const RECORDER_ON = 'Turn Polestar Recorder on (/psrec on in game), play, then /reload.';

function emptyNote(body, strong, rest) {
  if (state.data && state.data.header.polestar === false) return untrackedNote(body);
  fill(body, h('p', { class: 'empty' }, h('strong', { text: strong }), rest ? ' ' + rest : null));
}

// ------------------------------------------------------------ missing data

// What this character's pages can't show yet because a source is missing
// (the full view's `missing`, from steps.json's one list): one line at the
// top of each section it empties or thins, with the fix and the addon's
// link; none once the data is there. Sections redraw themselves (every
// minute, the async loaders), so the lines are put back after each change.
const MISSING_AT = { advice: '#advice', moves: '#home .ovmoves' };
const missingTarget = (sec) => document.querySelector(MISSING_AT[sec] || `#${sec}`);

function missingLine(m) {
  // The fix names its addons ("Add Questie or Altoholic."): each name links to the addon's page.
  let rest = m.fix;
  const fix = [];
  for (;;) {
    const hits = (m.addons || []).filter((a) => a.url && rest.includes(a.name));
    if (!hits.length) break;
    const a = hits.reduce((x, y) => (rest.indexOf(y.name) < rest.indexOf(x.name) ? y : x));
    const i = rest.indexOf(a.name);
    fix.push(rest.slice(0, i), h('a', { href: a.url, target: '_blank', rel: 'noopener noreferrer', referrerpolicy: 'no-referrer', text: a.name }));
    rest = rest.slice(i + a.name.length);
  }
  fix.push(rest);
  return h('p', { class: 'missing', 'data-kind': m.id }, h('strong', { text: `Missing: ${m.what}.` }), ' ', fix);
}

let missingQueued = false;
function markMissing() {
  missingQueued = false;
  const list = (state.data && state.data.missing) || [];
  const want = new Map();
  for (const m of list) {
    for (const sec of m.sections || []) {
      const el = missingTarget(sec);
      if (el && !want.has(el)) want.set(el, m);  // once per section: the first kind that names it
    }
  }
  document.querySelectorAll('p.missing').forEach((p) => {
    const host = p.closest('[data-missing-host]');
    const m = host && want.get(host);
    if (!m || m.id !== p.dataset.kind) p.remove();
  });
  document.querySelectorAll('[data-missing-host]').forEach((el) => {
    if (!want.has(el)) { delete el.dataset.missingHost; el.classList.remove('has-missing'); }
  });
  for (const [el, m] of want) {
    el.dataset.missingHost = '';
    el.classList.toggle('has-missing', !m.partial);  // an emptied section: the line replaces its own empty note
    if (el.querySelector(`p.missing[data-kind="${m.id}"]`)) continue;
    const head = el.querySelector('h2, h3');
    const line = missingLine(m);
    if (head) head.after(line); else el.prepend(line);
  }
}
function queueMissing() {
  if (missingQueued) return;
  missingQueued = true;
  requestAnimationFrame(markMissing);
}
new MutationObserver(queueMissing).observe(document.body, { childList: true, subtree: true });

// ------------------------------------------------------------ hero

// The class colours every WoW add-on uses (RAID_CLASS_COLORS).
const CLASS_COLOURS = {
  warrior: '#C69B6D', paladin: '#F48CBA', hunter: '#AAD372', rogue: '#FFF468', priest: '#FFFFFF',
  shaman: '#0070DD', mage: '#3FC7EB', warlock: '#8788EE', druid: '#FF7C0A', 'death knight': '#C41E3A',
};
const classColour = (c) => (c ? CLASS_COLOURS[String(c).toLowerCase()] || null : null);

// A heater shield drawn here, not any game emblem: blue with a chevron and a
// star for the Alliance, red with a saltire for the Horde, plain stone else.
function crest(faction) {
  const f = String(faction || '').toLowerCase();
  const field = f === 'alliance' ? ['#2A5BA8', '#132C57'] : f === 'horde' ? ['#A82A1E', '#4E0E09'] : ['#5A4B3A', '#2A2118'];
  const id = 'cf' + Math.random().toString(36).slice(2, 8);
  const outer = 'M6 6H68V40C68 62 52 76 37 84C22 76 6 62 6 40Z';
  const inner = 'M12 12H62V40C62 58 49 70 37 77C25 70 12 58 12 40Z';
  const gold = '#E2B957';
  const charge = f === 'alliance'
    ? [s('path', { d: 'M20 62L37 45L54 62', fill: 'none', stroke: gold, 'stroke-width': 6, 'stroke-linejoin': 'miter' }),
      s('path', { d: 'M37 17L40 26L49 29L40 32L37 41L34 32L25 29L34 26Z', fill: gold })]
    : f === 'horde'
      ? [s('path', { d: 'M22 22L52 64M52 22L22 64', stroke: gold, 'stroke-width': 6, 'stroke-linecap': 'square' }),
        s('circle', { cx: 37, cy: 43, r: 6, fill: '#4E0E09', stroke: gold, 'stroke-width': 2.5 })]
      : [s('path', { d: 'M37 24L41 37L54 41L41 45L37 58L33 45L20 41L33 37Z', fill: gold })];
  return s('svg', { class: 'crest', viewBox: '0 0 74 88', role: 'img', 'aria-label': faction ? `${faction} crest` : 'Crest' },
    s('defs', {}, s('linearGradient', { id, x1: 0, y1: 0, x2: 0, y2: 1 },
      s('stop', { offset: 0, 'stop-color': field[0] }), s('stop', { offset: 1, 'stop-color': field[1] }))),
    s('path', { d: outer, fill: '#120D09', stroke: '#000', 'stroke-width': 2 }),
    s('path', { d: outer, fill: 'none', stroke: gold, 'stroke-width': 2.5, transform: 'translate(37 45) scale(.94) translate(-37 -45)' }),
    s('path', { d: inner, fill: `url(#${id})`, stroke: '#7A5A22', 'stroke-width': 1.5 }),
    charge,
    s('path', { d: 'M14 14H60V24C46 20 28 20 14 24Z', fill: '#fff', opacity: 0.08 }));
}

// The public card's numbers: time played, item level, and the climb against
// Polestar's route over the finished levels it has a time for.
function routeTotals(d) {
  const lv = (d.levels || []).filter((l) => !l.partial && l.route_s && l.seconds);
  return { n: lv.length, played: lv.reduce((a, b) => a + b.seconds, 0), route: lv.reduce((a, b) => a + b.route_s, 0) };
}
function publicFacts(d) {
  const hd = d.header;
  const played = hd.played_total_s || hd.played_s;
  const r = routeTotals(d);
  const ilvl = d.gear && d.gear.item_level;
  return h('dl', { class: 'facts three' },
    h('div', {}, h('dt', { text: 'Played' }), h('dd', {}, played ? dur(played) : 'not yet',
      h('span', { class: 'sub', text: hd.played_total_s ? 'since level 1' : hd.played_s ? `since level ${hd.first_level}` : 'no split yet' }))),
    h('div', {}, h('dt', { text: 'Item level' }), h('dd', {}, ilvl ? ilvl.toFixed(1) : 'not yet',
      h('span', { class: 'sub', text: ilvl ? 'average of what it wears' : 'no equipment saved' }))),
    h('div', {}, h('dt', { text: 'Against the route' }), h('dd', {}, r.n ? `${(r.played / r.route).toFixed(1)}×` : 'not yet',
      h('span', { class: 'sub', text: r.n ? `Polestar’s time, over ${r.n} level${r.n === 1 ? '' : 's'}` : 'needs a finished level' }))));
}

function renderHero(d) {
  const hd = d.header;
  const hero = $('#hero');
  const pct = hd.xp_max ? Math.max(0, Math.min(1, (hd.xp || 0) / hd.xp_max)) : null;
  const rested = hd.rested_xp && hd.xp_max ? Math.min(hd.rested_xp, hd.xp_max - (hd.xp || 0)) : 0;
  const colour = classColour(hd.class);
  if (colour) hero.style.setProperty('--class', colour); else hero.style.removeProperty('--class');

  // The one title line (views.card_title): class, dominant tree and split,
  // what the gear is levelling for; the class alone without talents.
  const cls = hd.title || hd.class || '';
  const where = [];
  if (hd.zone) where.push(hd.zone);
  if (hd.seen) where.push(`${when(hd.seen)} (${ago(hd.seen)})`);

  const fill = h('i', { class: 'fill' });
  const rest = rested > 0 ? h('i', { class: 'rest' }) : null;
  const bar = h('div', {
    class: 'xpbar' + (rested > 0 ? ' rested' : ''), role: 'progressbar', 'aria-label': 'Experience this level',
    'aria-valuemin': 0, 'aria-valuemax': hd.xp_max || 0, 'aria-valuenow': hd.xp || 0,
  }, rest, fill, h('i', { class: 'ticks' }));

  const facts = PUBLIC ? publicFacts(d) : h('dl', { class: 'facts' + ('money_copper' in hd ? '' : ' three') },
    'money_copper' in hd ? h('div', { title: hd.money_at ? `Saved by the game at logout, ${when(hd.money_at)}` : '' },
      h('dt', { text: 'Gold' }), h('dd', {}, coins(hd.money_copper))) : null,
    h('div', {}, h('dt', { text: 'In this level' }),
      h('dd', {}, hd.time_in_level_s !== null ? dur(hd.time_in_level_s) : 'not yet',
        h('span', { class: 'sub', text: hd.time_in_level_s !== null ? 'played, offline time not counted' : 'no split for this level yet' }))),
    h('div', {}, h('dt', { text: 'Pace' }),
      h('dd', {}, hd.avg_level_s ? dur(hd.avg_level_s) : 'not yet',
        h('span', { class: 'sub', text: hd.avg_level_s ? (hd.avg_levels > 1 ? `a level, over the last ${hd.avg_levels}` : 'for the one finished level') : 'needs a finished level' }))),
    h('div', {}, h('dt', { text: 'Next level' }),
      h('dd', {}, hd.eta_s ? 'in ' + dur(hd.eta_s) : 'not yet',
        h('span', { class: 'sub', text: hd.xp_per_hour ? `at ${fmtK(hd.xp_per_hour)} XP an hour` : 'needs XP and a pace' }))));

  fill_(hero,
    h('div', { class: 'ident' },
      crest(hd.faction),
      h('div', { class: 'who' },
        h('h1', { text: hd.name, title: hd.full_name || hd.name }),
        h('p', { class: 'line' }, hd.race ? `${hd.race} ` : '', cls ? h('b', { text: cls }) : null,
          !hd.race && !cls && hd.polestar !== false ? 'Race and class unknown yet' : null),
        !hd.race && !cls && hd.polestar === false ? h('p', { class: 'where', text:
          'Only its WTF folder knows this character. Log in once with Syndicator enabled, then log out or /reload: race, class, gear and gold fill in.' }) : null,
        hd.guild ? h('p', { class: 'guild', text: `<${hd.guild}>` }) : null,
        where.length ? h('p', { class: 'where', text: where.join(', ') + (hd.resting ? '. Logged out resting.' : '') }) : null),
      h('p', { class: 'lvring', 'aria-label': `Level ${hd.level ?? 'unknown'}` },
        h('b', { text: hd.level ?? '?' }), h('small', { text: 'Level' }))),
    h('div', { class: 'xpwrap' }, bar,
      h('div', { class: 'xpline' },
        h('span', {}, pct !== null ? [h('strong', { text: fmtNum(hd.xp) }), ` / ${fmtNum(hd.xp_max)} XP`] : 'No XP reading yet',
          rested > 0 ? h('span', { class: 'restnote', text: `, ${fmtNum(hd.rested_xp)} rested` }) : null),
        h('span', { text: pct !== null ? `${Math.floor(pct * 100)}% of level ${hd.level}` : '' }))),
    facts);

  // The one moment of motion: the bar fills, then the rested run beyond it.
  const w = pct === null ? 0 : pct * 100;
  const rw = pct === null || !rest ? 0 : Math.min(100, w + (rested / hd.xp_max) * 100);
  const set = () => { fill.style.width = w + '%'; if (rest) rest.style.width = rw + '%'; };
  if (reduceMotion) set(); else requestAnimationFrame(() => requestAnimationFrame(set));
  if (rest) rest.title = `${fmtNum(hd.rested_xp)} rested XP: kills give double until it runs out`;
}

// ------------------------------------------------------------ gear

// Small glyphs per slot, drawn for this page (24x24, stroked in the
// item's quality colour): an empty slot, and the fallback while (or when)
// an item has no icon. The icons themselves are the game's (web/items.js).
const GLYPHS = {
  head: 'M5 15a7 7 0 0 1 14 0v4H5z M5 15h14 M12 8v11',
  neck: 'M6 3c0 6 2.5 9 6 9s6-3 6-9 M12 12v2.5 M9.5 17.5a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0',
  shoulder: 'M3 14c2-5 5.5-7.5 9-7.5s7 2.5 9 7.5l-3 2c-1-3-3-4.5-6-4.5s-5 1.5-6 4.5z',
  back: 'M8 4h8l3.5 16-7.5-3-7.5 3z M8 4l4 4 4-4',
  chest: 'M8 4l4 3 4-3 4 3-2 5v8H6v-8L4 7z',
  shirt: 'M8 4l4 2 4-2 5 4-3 3-1-1v10H7V10l-1 1-3-3z',
  tabard: 'M7 4h10v13l-5 3-5-3z M10 9h4',
  wrist: 'M7 6h10v12H7z M7 10h10 M7 14h10',
  hands: 'M8 21v-7l-2.5-3 1-1 2.5 2V6a1 1 0 0 1 2 0v5 M10.5 5.5a1 1 0 0 1 2 0V11 M12.5 6.5a1 1 0 0 1 2 0V11 M14.5 8a1 1 0 0 1 2 0v6c0 4-2 7-5 7H8',
  waist: 'M3 10h18v4H3z M10 8.5h4v7h-4z',
  legs: 'M7 4h10l1 16h-4l-2-10-2 10H6z',
  feet: 'M8 3h5v10l6 3v4H6v-4l2-2z',
  finger: 'M12 21a6 6 0 1 0 0-12a6 6 0 1 0 0 12z M9.5 9l2.5-5 2.5 5',
  trinket: 'M12 3l2.8 5.7 6.2.9-4.5 4.4 1 6.2-5.5-2.9-5.5 2.9 1-6.2L3 9.6l6.2-.9z',
  main: 'M19 3l2 2-11 11-2-2z M6 13l5 5 M7.5 16.5l-3.5 3.5 M3 21l1-1',
  off: 'M12 3l7 3v6c0 4-3 7-7 9-4-2-7-5-7-9V6z M12 6v12',
  ranged: 'M5 19L16 8 M14 4l2 4 4 2-4 2-2 4-2-4-4-2 4-2z',
};
const SLOT_GLYPH = { 1: 'head', 2: 'neck', 3: 'shoulder', 4: 'shirt', 5: 'chest', 6: 'waist', 7: 'legs', 8: 'feet',
  9: 'wrist', 10: 'hands', 11: 'finger', 12: 'finger', 13: 'trinket', 14: 'trinket', 15: 'back', 16: 'main', 17: 'off',
  18: 'ranged', 19: 'tabard' };
const SLOT_NAME = { 1: 'Head', 2: 'Neck', 3: 'Shoulder', 4: 'Shirt', 5: 'Chest', 6: 'Waist', 7: 'Legs', 8: 'Feet',
  9: 'Wrist', 10: 'Hands', 11: 'Finger', 12: 'Finger', 13: 'Trinket', 14: 'Trinket', 15: 'Back', 16: 'Main hand',
  17: 'Off hand', 18: 'Ranged', 19: 'Tabard' };
const QUALITY = ['Poor', 'Common', 'Uncommon', 'Rare', 'Epic', 'Legendary'];

function glyph(name) {
  return s('svg', { viewBox: '0 0 24 24', 'aria-hidden': 'true', fill: 'none', stroke: 'currentColor',
    'stroke-width': 1.6, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }, s('path', { d: GLYPHS[name] }));
}

// One equipment slot: the item's icon (56 px, the game's) in a dark frame
// with its quality's border and the in-game tooltip on hover, tap or Enter.
function slotRow(slot, item) {
  const q = item && item.quality !== null && item.quality !== undefined ? Math.max(0, Math.min(5, item.quality)) : null;
  const meta = [SLOT_NAME[slot]];
  if (item && item.item_level) meta.push(`item level ${item.item_level}`);
  if (item && item.enchant) meta.push(item.enchant);
  if (!item) {
    return h('li', { class: 'slot empty', title: `${SLOT_NAME[slot]}: nothing equipped`, 'data-slot': slot },
      h('span', { class: 'ifr large' }, glyph(SLOT_GLYPH[slot])),
      h('span', { class: 'ilabel' }, h('span', { class: 'iname', text: 'Empty' }), h('span', { class: 'imeta', text: meta.join(', ') })));
  }
  return h('li', { class: 'slot' + (q !== null ? ` q${q}` : '') + (item.enchant_id > 0 ? ' enchanted' : ''), 'data-slot': slot },
    window.wowItem(item, { size: 'large', glyph: glyph(SLOT_GLYPH[slot]), cls: 'slotitm', sub: meta.join(', ') }));
}

// An item anywhere else (lists, advice, loot, snapshots): the 36 px icon
// and its name, the same tooltip.
function itemChip(i, opts) {
  return window.wowItem(i, { size: 'medium', ...(opts || {}) });
}

// A plain standing figure for the middle of the paperdoll.
function figure() {
  return s('svg', { class: 'figure', viewBox: '0 0 120 230', 'aria-hidden': 'true' },
    s('defs', {}, s('linearGradient', { id: 'fig', x1: 0, y1: 0, x2: 0, y2: 1 },
      s('stop', { offset: 0, 'stop-color': '#4A3B2C' }), s('stop', { offset: 1, 'stop-color': '#1E1610' }))),
    s('g', { fill: 'url(#fig)', stroke: '#8A6A34', 'stroke-width': 1.2, 'stroke-linejoin': 'round' },
      s('circle', { cx: 60, cy: 32, r: 17 }),
      s('path', { d: 'M52 49h16l2 9H50z' }),
      s('path', { d: 'M28 70c6-10 18-14 32-14s26 4 32 14l-6 66c-8 4-17 6-26 6s-18-2-26-6z' }),
      s('path', { d: 'M28 72l-10 62 8 4 10-50z M92 72l10 62-8 4-10-50z' }),
      s('path', { d: 'M38 140l-6 74h22l6-60 6 60h22l-6-74c-7 3-15 4-22 4s-15-1-22-4z' })),
    s('path', { d: 'M40 88c12 5 28 5 40 0', fill: 'none', stroke: '#C99A3B', 'stroke-width': 1, opacity: 0.5 }));
}

window.wowFigure = figure;

// An image the server or the bundle holds: the live server's /<rel>, or the
// bundle's data/<rel> (sealed in /zogzog/, shown as a data: URL: the CSP's
// img-src).
async function bundleImage(rel, mime) {
  if (!STATIC) return '/' + rel;
  const path = 'data/' + rel;
  if (!SEALED) return path;
  const res = await fetch(path + '.enc', { credentials: 'same-origin' });
  if (!res.ok) return null;
  const bytes = await openBytes(path, await res.arrayBuffer());
  let bin = '';
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return `data:${mime};base64,` + btoa(bin);
}
// The armory's full-body render (wowstats/armory.py), Battle.net's image of
// the character: /render/<char>.<ext> live, data/render/<char>.<ext> in a
// bundle. The silhouette stays when there is none.
function renderSrc(char, r) {
  return bundleImage(`render/${encodeURIComponent(char)}.${r.ext}`, r.ext === 'png' ? 'image/png' : 'image/jpeg');
}
// Battle.net's main-raw render is a 1600x1200 landscape canvas with the
// character in a 300x460 box in its middle: scaled to fit a portrait stage
// it came out ~100 px tall at the bottom (0.3.0). So the character's own
// box (the opaque pixels, read once from a small copy on a canvas) is framed
// like the 3D viewer frames its model: about half the stage high, centred,
// feet a little above the toggle (app.css .still.framed), never past one
// image pixel per CSS pixel. A canvas that can't be read keeps the old fit.
function opaqueBox(img) {
  const w = 200;
  const hgt = Math.max(1, Math.round((img.naturalHeight / img.naturalWidth) * w));
  const c = document.createElement('canvas');
  c.width = w; c.height = hgt;
  const cx = c.getContext('2d', { willReadFrequently: true });
  cx.drawImage(img, 0, 0, w, hgt);
  const px = cx.getImageData(0, 0, w, hgt).data;
  let x0 = w, y0 = hgt, x1 = -1, y1 = -1;
  for (let y = 0; y < hgt; y++) {
    for (let x = 0; x < w; x++) {
      if (px[(y * w + x) * 4 + 3] > 16) {
        if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
      }
    }
  }
  if (x1 < 0) return null;
  return { x0: x0 / w, x1: (x1 + 1) / w, y0: y0 / hgt, y1: (y1 + 1) / hgt };
}
function frameRender(box, img) {
  let b = null;
  try { b = opaqueBox(img); } catch (e) { b = null; }  // a tainted canvas: the plain fit
  if (!b || b.y1 - b.y0 < 0.05) return;
  const st = box.style;
  st.setProperty('--bw', (b.x1 - b.x0).toFixed(4));
  st.setProperty('--bh', (b.y1 - b.y0).toFixed(4));
  st.setProperty('--bcx', ((b.x0 + b.x1) / 2).toFixed(4));
  st.setProperty('--bb', b.y1.toFixed(4));
  st.setProperty('--ar', (img.naturalWidth / img.naturalHeight).toFixed(4));
  st.setProperty('--nh', `${img.naturalHeight}px`);
  box.classList.add('framed');
}
function still(char, g) {
  const box = h('div', { class: 'still' }, figure());
  if (g && g.render && /^(png|jpg)$/.test(g.render.ext || '')) {
    renderSrc(char, g.render).then((src) => {
      if (!src) return;
      const img = h('img', { class: 'render', alt: `${char.replace(/-.*$/, '')}, as the armory shows them`, src, decoding: 'async' });
      img.addEventListener('load', () => { frameRender(box, img); box.replaceChildren(img); box.classList.add('has-render'); });
    }).catch(() => {});
  }
  return box;
}

// A past level's picture (wowstats/pictures.py): the bundle's
// data/picture/<char>/<level>.webp, framed like the armory's render, drawn
// into `box` (the stage's .still, kept from level to level: the 3D script's
// wowPastLevel). Not drawn yet: the silhouette, and the 3D toggle under it.
function levelStill(box, char, level, pic) {
  const want = `${char}|${level}`;
  box.dataset.want = want;
  box.className = 'still';
  box.removeAttribute('style');
  box.replaceChildren(figure());
  if (!pic || !pic.ext) {
    box.classList.add('pending');
    box.append(h('p', { class: 'stillnote', text: 'Picture on its way. View it in 3D meanwhile.' }));
    return box;
  }
  bundleImage(`picture/${encodeURIComponent(char)}/${level}.${pic.ext}`, 'image/webp').then((src) => {
    if (!src || box.dataset.want !== want) return;
    const img = h('img', { class: 'render', alt: `${char.replace(/-.*$/, '')} at level ${level}`, src, decoding: 'async' });
    img.addEventListener('load', () => {
      if (box.dataset.want !== want) return;
      frameRender(box, img);
      box.replaceChildren(img);
      box.classList.add('has-render');
    });
  }).catch(() => {});
  return box;
}
window.wowLevelStill = levelStill;

// Tells whoever listens that the equipment panel was drawn (or hidden).
function gearDrawn(gear) {
  document.dispatchEvent(new CustomEvent('wow:gear', { detail: { char: state.char, gear } }));
}

function renderGear(d) {
  const box = $('#gear');
  const g = d.gear;
  if (!g || (!(g.items || []).length && !(g.professions || []).length)) {
    // Every character gets the panel: empty, it says which addon fills it.
    box.hidden = false;
    fill_(box, h('h2', { class: 'rule-title', text: 'Equipment' }),
      h('p', { class: 'empty' }, h('strong', { text: 'No equipment saved yet.' }), ' ',
        'Turn Syndicator on in AddOns, log in with ' + d.header.name + ', then /reload.'));
    if (state.data && state.data.header.polestar === false) box.append(h('div', { id: 'advice', class: 'advice' }));
    gearDrawn(null);
    return;
  }
  box.hidden = false;
  // The 3D model open on the same sheet: a redraw for new data ("Show")
  // leaves the paperdoll as it is (rebuilding it would reload the viewer).
  const open3d = box.querySelector('.doll > .stage.in3d');
  if (open3d && open3d.dataset.char === d.char && open3d.dataset.at === String(g.at || '')) {
    if (state.advice && state.advice.char === d.char) renderAdvice(state.advice);
    return;
  }
  const by = Object.fromEntries((g.items || []).map((i) => [i.slot, i]));
  const col = (slots, cls) => h('ul', { class: 'slots ' + cls }, slots.map((sl) => slotRow(sl, by[sl])));
  const profs = (g.professions || []).map((p) => {
    const max = p.max_rank || 300;
    return h('li', { class: 'prof' },
      h('span', { class: 'pn', text: p.name }),
      h('span', { class: 'pr', text: p.max_rank ? `${p.rank} / ${p.max_rank}` : String(p.rank ?? '?') }),
      h('span', { class: 'track', role: 'img', 'aria-label': `${p.name} ${p.rank} of ${max}` },
        h('i', { 'width%': Math.min(100, ((p.rank || 0) / max) * 100) })));
  });
  const wanted = (g.wanted || []).map((w) => {
    const q = w.quality ?? null;
    return h('li', {}, `${w.slot_name}: `, itemChip({ ...w, quality: q }), w.level ? ` at level ${w.level}` : '');
  });
  // One number, said plainly: the average item level of what is worn. The
  // armory's figure (every slot, an empty one 0) stood above it until
  // 2026-11-19, "23 item level" over "27.6 average": its empty slots are
  // already on the paperdoll ("something to wear"), and Classic's armory
  // counts the off hand a two-hander leaves empty as 0, so it never reaches
  // the gear's level. It stays in the answer and the MCP; the page shows it
  // only when the sheet has no average.
  const armory = g.armory_item_level;
  const ilvl = g.item_level
    ? h('p', { class: 'ilvl' }, h('b', { text: g.item_level.toFixed(1) }), h('span', { text: 'item level' }),
      h('small', { text: 'average of the items you wear' }))
    : armory !== null && armory !== undefined
      ? h('p', { class: 'ilvl' }, h('b', { text: String(armory) }), h('span', { text: 'item level' }),
        h('small', { text: 'empty slots count as 0' }))
      : null;
  fill_(box,
    h('h2', { class: 'rule-title', text: 'Equipment' }),
    // The armory's paperdoll: the character in the middle (its render, or
    // the 3D model on its toggle), the slots around it.
    h('div', { class: 'doll' },
      h('div', { class: 'stage', 'data-char': d.char, 'data-at': String(g.at || '') }, still(d.char, g)),
      col([1, 2, 3, 15, 5, 4, 19, 9], 'left'),
      col([10, 6, 7, 8, 11, 12, 13, 14], 'right'),
      h('ul', { class: 'slots weapons' }, [16, 17, 18].map((sl) => slotRow(sl, by[sl])))),
    h('div', { class: 'dollfoot' },
      ilvl,
      profs.length ? h('ul', { class: 'profs', 'aria-label': 'Professions' }, profs) : null,
      wanted.length ? h('div', { class: 'wanted' }, h('h3', { text: 'Tracked upgrades' }), h('ul', {}, wanted)) : null),
    h('div', { id: 'advice', class: 'advice', 'aria-live': 'polite' }));
  gearDrawn(g);
  if (state.advice && state.advice.char === d.char) renderAdvice(state.advice);
}

// ------------------------------------------------------------ talents and Legacy points

// The card's talents (wowstats/talents.py): Forever's talent frame for the
// class, filled in with the last /simx export (VulcanSimBench), the spec
// and points per tree, one stop per export, and the Legacy points the log
// proves. Icons come through window.wowIcon when the page has it; a public
// bundle also holds them at data/icon/<size>/<name>.jpg.

async function talentIcon(name) {
  if (!name) return null;
  if (window.wowIcon) {
    try { return await window.wowIcon(name, 'medium'); } catch (e) { return null; }
  }
  const rel = `data/icon/medium/${encodeURIComponent(name)}.jpg`;
  if (STATIC && !SEALED) return rel;
  if (SEALED) {  // sealed like the answers: decrypted here, shown as a data: URL (the CSP's img-src)
    try {
      const r = await fetch(rel + '.enc', { credentials: 'same-origin' });
      if (!r.ok) return null;
      const bytes = new Uint8Array(await openBytes(rel, await r.arrayBuffer()));
      let bin = '';
      for (const b of bytes) bin += String.fromCharCode(b);
      return 'data:image/jpeg;base64,' + btoa(bin);
    } catch (e) { return null; }
  }
  return null;  // the live view's strict CSP: no outside images without wowIcon
}

function talentTip(n) {
  const rank = n.max ? `Rank ${n.rank} of ${n.max}` : null;
  return [n.name, rank, n.desc].filter(Boolean);
}

function tipFor(el, lines) {
  el.setAttribute('aria-label', lines.join('. '));
  if (window.wowTip && window.wowTip.bind) {
    try {
      window.wowTip.bind(el, () => h('div', { class: 'ttip' },
        h('b', { text: lines[0] }), lines[1] ? h('span', { class: 'trank', text: lines[1] }) : null,
        lines[2] ? h('p', { text: lines[2] }) : null));
      return;
    } catch (e) { /* fall back to the title */ }
  }
  el.title = lines.join('\n');
}

function talentNode(n) {
  const cls = 'tnode' + (n.rank ? ' learned' : '') + (n.rank && n.rank >= n.max ? ' maxed' : '');
  const face = h('span', { class: 'tface', text: (n.name || '?').slice(0, 1) });
  const el = h('button', { type: 'button', class: cls },
    face, h('span', { class: 'tbadge', text: `${n.rank}/${n.max}` }));
  el.style.gridColumn = String(n.col + 1);
  el.style.gridRow = String(n.row + 1);
  tipFor(el, talentTip(n));
  talentIcon(n.icon).then((src) => {
    if (!src) return;
    const img = h('img', { src, alt: '', loading: 'lazy', decoding: 'async' });
    img.addEventListener('error', () => img.remove());
    face.replaceChildren(img);
  });
  return el;
}

// The prerequisite arrows of one tree (talents.py: TraitEdge), drawn as the
// game does: from the required talent to the one it opens, gold once the
// required one is maxed, grey before. One SVG under the grid, in cell units
// (a cell is 5 wide with a 1 gap, the icon 4 of it: --tsize and the gap keep
// that ratio closely enough on every screen).
function talentArrows(sec, cols, rows) {
  const NS = 'http://www.w3.org/2000/svg';
  const mk = (tag, attrs) => {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
    return e;
  };
  const C = 5, ICON = 4;
  const svg = mk('svg', { class: 'tarrows', viewBox: `0 0 ${cols * C - (C - ICON)} ${rows * C - (C - ICON)}`,
    preserveAspectRatio: 'none', 'aria-hidden': 'true', focusable: 'false' });
  svg.style.width = `calc(var(--tsize) * ${cols} + var(--tsize) / 4 * ${cols - 1})`;
  const at = new Map(sec.nodes.map((n) => [`${n.col},${n.row}`, n]));
  for (const e of sec.edges || []) {
    const from = at.get(`${e.fc},${e.fr}`);
    const met = from && from.rank >= from.max;
    let x1, y1, x2, y2, head;
    if (e.fc === e.tc) {  // down a column
      x1 = x2 = e.fc * C + ICON / 2; y1 = e.fr * C + ICON; y2 = e.tr * C - 0.15;
      head = `${x2 - 0.55},${y2 - 0.7} ${x2 + 0.55},${y2 - 0.7} ${x2},${y2}`;
    } else if (e.fr === e.tr) {  // along a tier
      const dir = e.tc > e.fc ? 1 : -1;
      y1 = y2 = e.fr * C + ICON / 2;
      x1 = e.fc * C + (dir > 0 ? ICON : 0); x2 = e.tc * C + (dir > 0 ? 0 : ICON) - 0.15 * dir;
      head = `${x2 - 0.7 * dir},${y2 - 0.55} ${x2 - 0.7 * dir},${y2 + 0.55} ${x2},${y2}`;
    } else {  // across and down: down first, then along, as the game bends it
      x1 = e.fc * C + ICON / 2; y1 = e.fr * C + ICON;
      const yb = e.tr * C + ICON / 2;
      const dir = e.tc > e.fc ? 1 : -1;
      x2 = e.tc * C + (dir > 0 ? 0 : ICON) - 0.15 * dir; y2 = yb;
      svg.append(mk('polyline', { points: `${x1},${y1} ${x1},${yb} ${x2},${y2}`, class: 'tarrow' + (met ? ' met' : '') }));
      svg.append(mk('polygon', { points: `${x2 - 0.7 * dir},${y2 - 0.55} ${x2 - 0.7 * dir},${y2 + 0.55} ${x2},${y2}`,
        class: 'thead' + (met ? ' met' : '') }));
      continue;
    }
    svg.append(mk('line', { x1, y1, x2, y2, class: 'tarrow' + (met ? ' met' : '') }));
    svg.append(mk('polygon', { points: head, class: 'thead' + (met ? ' met' : '') }));
  }
  return svg;
}

const pointsLine = (pts) => (pts || []).map((p) => `${p.tree || '?'} ${p.points ?? '?'}`).join(' / ');

function talentsAt(level) {
  // The /simx stop at or before a past level, for the level snapshots.
  const t = state.data && state.data.talents;
  if (!t || !(t.history || []).length) return null;
  const stops = t.history.filter((x) => x.level !== null && x.level <= level);
  const at = stops[stops.length - 1];
  if (!at) return h('p', { class: 'sub', text: `Talents: no /simx export at or before level ${level}.` });
  return h('p', {}, h('b', { text: 'Talents: ' }),
    `${at.spec ? at.spec + ', ' : ''}${pointsLine(at.points)}`);
}

function legacyBlock(lg) {
  if (!lg) return null;
  const head = lg.available && lg.derived_min !== null
    ? [h('b', { text: `At least ${lg.derived_min}` }), ` of ${lg.total} Legacy points`]
    : [h('b', { text: 'Legacy points: not saved' })];
  return h('div', { class: 'legacy' },
    h('h3', { text: 'Legacy' }),
    h('p', {}, ...head));
}

function renderTalents(d) {
  let box = $('#talents');
  if (!box) {
    const gear = $('#gear');
    if (!gear) return;
    box = h('section', { id: 'talents', class: 'plate talents', 'aria-label': 'Talents and Legacy points' });
    gear.after(box);
  }
  const t = d.talents;
  if (!t) { box.hidden = true; return; }
  box.hidden = false;
  // Two lines (owner, 2026-11-18): the talents as they are, by points, and
  // what the gear is scored for, the player's choice.
  const sp = d.spec || {};
  const spec = sp.name;
  const w = sp.scored_for;
  const head = h('div', { class: 'tspec' },
    h('span', {}, 'Talents: ', h('b', { class: 'tspecname', text: spec ? `${spec} ${sp.split || ''}`.trim() : 'not known yet',
      title: (sp.points || []).map((x) => `${x.tree} ${x.points}`).join(', ') })),
    w ? h('span', {}, 'Gear scored for: ', h('b', { text: w.label })) : null);
  const notes = [t.cross_check, w && w.sanity_check].filter(Boolean)
    .map((x) => h('p', { class: 'sub tnote', text: x }));
  const kids = [h('h2', { class: 'rule-title', text: 'Talents' }), head, ...notes];
  if (!t.available) {
    kids.push(h('p', { class: 'empty' }, h('strong', { text: 'No talents saved yet.' }),
      ' Type /simx in game with Vulcan SimBench on, then /reload.'));
  } else {
    kids.push(h('div', { class: 'tframe' }, (t.sections || []).map((sec) => {
      // The game's frame: 4 columns by 7 tiers, always (a phone shrinks the
      // icons, never the columns), the prerequisite arrows under the icons.
      const cols = Math.max(4, ...sec.nodes.map((n) => n.col + 1));
      const rows = Math.max(7, ...sec.nodes.map((n) => n.row + 1));
      const grid = h('div', { class: 'tgrid' }, talentArrows(sec, cols, rows), sec.nodes.map(talentNode));
      grid.style.gridTemplateColumns = `repeat(${cols}, var(--tsize))`;
      grid.style.gridTemplateRows = `repeat(${rows}, var(--tsize))`;
      return h('div', { class: 'ttree' + (t.spec && sec.name === t.spec ? ' main' : '') },
        h('h3', {}, h('span', { text: sec.name || 'Tree' }), h('span', { class: 'tpts', text: String(sec.points) })),
        grid);
    })));
  }
  if ((t.history || []).length > 1 || t.available) {
    kids.push(h('div', { class: 'thistory' }, h('h3', { text: 'Build by level' }),
      (t.history || []).length ? h('ol', {}, t.history.map((x) => h('li', {},
        h('b', { text: x.level ? `Level ${x.level}` : 'Level ?' }), ` ${x.spec ? x.spec + ': ' : ''}${pointsLine(x.points)}`)))
        : null));
  }
  kids.push(legacyBlock(d.legacy));
  fill(box, ...kids);
}

// ------------------------------------------------------------ class guide

// "Your class at level N" (wowstats/classguide.py): what to train now and
// next and where (the class trainers of the character's side, its race's
// capital first), the next talent points, a habit or two. Actions only:
// where the advice comes from stays in the data and the MCP's class_guide.
const titleCase = (t) => String(t || '').toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
function cgSpell(sp) {
  return h('li', { class: 'cgspell' }, wh('spell', sp.id, sp.name),
    sp.rank ? h('span', { class: 'sub', text: ` rank ${sp.rank}` }) : null,
    sp.cost_copper ? h('span', { class: 'cgcost', text: coinText(sp.cost_copper) }) : null);
}
function cgTrain(t, head) {
  if (!t || !(t.spells || []).length) return null;
  const total = t.spells.reduce((a, sp) => a + (sp.cost_copper || 0), 0);
  return h('div', { class: 'cgtrain' },
    h('h3', {}, head, total ? h('span', { class: 'cgcost', text: coinText(total) }) : null),
    capped(t.spells.map(cgSpell), 3, (n) => `${n} more spell${n === 1 ? '' : 's'}`, 'cgspells'));
}
function cgTalent(p) {
  return h('li', {}, h('b', { text: `Level ${p.level}: ` }), p.url ? whRef({ url: p.url, name: p.talent }) : p.talent,
    h('span', { class: 'sub', text: ` ${p.rank}/${p.max ?? '?'}, ${p.tree}` }),
    p.now ? h('span', { class: 'pill now', text: 'spend now' }) : null);
}
function renderClassGuide(d) {
  let box = $('#classguide');
  const g = d.class_guide;
  if (!box) {
    const after = $('#talents') || $('#gear');
    if (!after || !g) return;
    box = h('section', { id: 'classguide', class: 'plate classguide', 'aria-label': 'Your class at this level' });
    after.after(box);
  }
  if (!g) { box.hidden = true; return; }
  box.hidden = false;
  const tr = g.train || {};
  const now = tr.now && tr.now.level === g.level ? 'Train now' : (tr.now ? `Train (level ${tr.now.level})` : null);
  const train = [cgTrain(tr.now, now), cgTrain(tr.next, tr.next ? `Next, at level ${tr.next.level}` : null)].filter(Boolean);
  const where = (g.trainers || []).length ? h('div', { class: 'cgwhere' }, h('h3', { text: 'Class trainers' }),
    trainersEl(g.trainers, null)) : null;
  const tal = (g.talents || []).length ? h('div', { class: 'cgcol' },
    h('h3', { text: g.spec ? `${g.spec} talents` : 'Talents' }),
    h('ol', { class: 'cgtalents' }, g.talents.map(cgTalent)),
    g.respec ? h('p', { class: 'cgrespec' }, h('b', { text: `Level ${g.respec.level}: ` }), g.respec.action) : null) : null;
  const habits = (g.habits || []).length ? h('div', { class: 'cgcol' }, h('h3', { text: 'Habits' }),
    h('ul', { class: 'cghabits' }, g.habits.map((x) => h('li', { text: x })))) : null;
  fill(box, h('h2', { class: 'rule-title', text: `${titleCase(g.class)} at level ${g.level}` }),
    h('div', { class: 'cggrid' },
      train.length || where ? h('div', { class: 'cgcol' }, ...train, where) : null, tal, habits));
}

// ------------------------------------------------------------ gear advice

// Polestar's own scoring, run by the server (/api/gear): per slot the best
// item at this level and the next one coming, then the upgrades in order.

const specKey = (c) => `wow-gear-spec:${c}`;
function savedSpec(c) {
  try { return localStorage.getItem(specKey(c)) || ''; } catch (e) { return ''; }
}
function saveSpec(c, v) {
  try { if (v) localStorage.setItem(specKey(c), v); else localStorage.removeItem(specKey(c)); } catch (e) { /* private mode */ }
}


// ------------------------------------------------------------ gear advice: shared pieces

// One ratio format everywhere: 2.0×.
const ratioText = (r) => (r === null || r === undefined ? '?' : `${Number(r).toFixed(1)}×`);
const pts = (n) => `${n >= 0 ? '+' : '−'}${Math.abs(n ?? 0).toFixed(1)}`;
// A Wowhead link (web/items.js holds the address): game names only.
const wh = (kind, id, text, cls) => window.wowLink(kind, id, text, cls);

// What stands between the character and an item (wowstats/gear.py gates):
// the level, the profession's skill; "buy" (a crafter or the AH) is not a
// wait. An older answer without gates: its level.
function feasible(u, level) {
  const gates = u.gates || (u.now ? [] : [{ kind: 'level', need: u.level, have: level }]);
  const lv = gates.find((g) => g.kind === 'level');
  const sk = gates.find((g) => g.kind === 'skill');
  const buy = gates.find((g) => g.kind === 'buy');
  if (!lv && !sk) {
    return { now: true, text: 'Now', title: buy ? `Made by ${buy.profession} ${buy.need}: buy it from a crafter or the auction house.` : 'You can get and wear it now.' };
  }
  const text = [lv ? `Level ${lv.need}` : null, sk ? `${sk.profession} ${sk.need}` : null].filter(Boolean).join(' + ');
  const when = [lv ? `at level ${lv.need}` : null,
    sk ? `once ${sk.profession} reaches ${sk.need} (you have ${sk.have}, ${sk.need - sk.have} to go)` : null].filter(Boolean);
  return { now: false, text, title: `Opens ${when.join(', ')}.`, level: lv && lv.need, skill: sk };
}
function feasEl(u, level) {
  const f = feasible(u, level);
  return h('span', { class: 'pill' + (f.now ? ' now' : ''), text: f.text, title: f.title });
}

// Where an item comes from, as a group: what you already hold, one dungeon
// (or wing), the quests of one zone, crafts of one profession, the auction
// house, world drops.
const HELD = { bags: 'In your bags: equip it', bank: 'In your bank: take it out at a banker and equip it' };
// On sale now (wowstats/ahsource.py): listed in your newest scan, today or
// yesterday. An item with no fresher listing has no "ah" source at all.
// A page built a while ago counts the days again: older than yesterday, not shown.
const ahAge = (s) => (s.seen_day ? Math.floor((Date.now() - new Date(`${s.seen_day}T00:00:00`).getTime()) / 864e5) : s.seen_days_ago);
const ahOf = (u) => ((u && u.sources) || []).find((s) => s.kind === 'ah' && ahAge(s) <= 1) || null;
function primarySource(u) {
  const srcs = u.sources || [];
  return srcs.find((s) => HELD[s.kind]) || srcs.find((s) => s.kind === 'quest') || srcs.find((s) => s.kind === 'craft' && s.can_make)
    || srcs.slice().sort((x, y) => (y.chance_pct || 0) - (x.chance_pct || 0)).find((s) => s.kind === 'drop')
    || ahOf(u) || srcs[0] || null;
}
function sourceGroup(s) {
  if (!s) return { key: 'other', title: 'Elsewhere', kind: 'other' };
  if (HELD[s.kind]) return { key: 'held', title: 'Already yours: bags and bank', kind: 'held' };
  if (s.kind === 'ah') return { key: 'ah', title: 'Auction House', kind: 'ah' };
  if (s.kind === 'drop' && (s.instance || s.dungeon)) return { key: 'd:' + s.zone, title: s.zone, kind: 'dungeon', levels: s.levels };
  if (s.kind === 'drop') return { key: 'world', title: 'World drops', kind: 'world' };
  if (s.kind === 'quest') return { key: 'q:' + (s.zone || '?'), title: `Quests in ${s.zone || 'an unknown zone'}`, kind: 'quest' };
  return { key: 'c:' + s.profession, title: `${s.profession} crafts`, kind: 'craft' };
}

// 12g 40s: gold and silver, copper only under a gold (wowstats/ahsource.py price_text).
function priceText(copper) {
  const g = Math.floor(copper / 10000), sv = Math.floor((copper % 10000) / 100), c = copper % 100;
  return [g ? `${fmtNum(g)}g` : '', sv ? `${sv}s` : '', c && !g ? `${c}c` : ''].filter(Boolean).join(' ') || '0c';
}
const SEEN = ['on sale today', 'on sale yesterday'];
// The price, its share of your gold, when it was listed, and "cheap" when
// it costs at most what you net in a level.
function ahEl(sr) {
  const share = sr.gold_share_pct !== null && sr.gold_share_pct !== undefined
    ? ` (${sr.gold_share_pct < 1 ? '<1' : Math.round(sr.gold_share_pct)}% of your gold)` : '';
  const mount = sr.mount_share_pct ? `${sr.mount_share_pct < 1 ? 'Under 1' : Math.round(sr.mount_share_pct)}% of the gold your riding training still needs` : null;
  return h('span', { class: 'src' }, 'Auction House, ', h('b', { class: 'ahprice', text: priceText(sr.price_copper), title: mount }), share,
    `, ${SEEN[ahAge(sr)] || 'on sale'}`, sr.cheap ? h('span', { class: 'pill cheap', text: 'cheap' }) : null,
    sr.affordable === false ? h('span', { class: 'sub', text: ' (more than you have)' }) : null);
}

// One source in words, with its link and the numbers that decide a trip.
// ah: the item's auction house listing, for "or buy it" after another source.
function sourceEl(sr, level, ah) {
  if (HELD[sr.kind]) return h('span', { class: 'src', text: HELD[sr.kind] });
  if (sr.kind === 'ah') return ahEl(sr);
  if (sr.kind === 'quest') {
    const chain = sr.part && sr.part.length === 2 && sr.part[1] > 1 ? ` · ${sr.part[0]} of ${sr.part[1]}` : '';
    return h('span', { class: 'src' }, 'Quest ', wh('quest', sr.quest_id, sr.name || `quest ${sr.quest_id}`),
      chain, sr.zone ? `, ${sr.zone}` : '', sr.req_level ? `, level ${sr.req_level}` : '',
      sr.choices > 1 ? `, one of ${sr.choices} rewards` : '');
  }
  if (sr.kind === 'drop') {
    const runs = sr.chance_pct ? Math.max(1, Math.round(100 / sr.chance_pct)) : null;
    // Listed now and within your gold: buying beats farming a chance.
    const buy = ah && ah.affordable ? h('span', { class: 'ahor' }, 'or buy it now for ', h('b', { class: 'ahprice', text: priceText(ah.price_copper) }),
      ' instead of farming', ah.cheap ? h('span', { class: 'pill cheap', text: 'cheap' }) : null) : null;
    if (!(sr.instance || sr.dungeon)) {
      return h('span', { class: 'src' }, 'World drop', sr.zone ? ` in ${sr.zone}` : '', sr.chance_pct ? `, ${sr.chance_pct}%` : '', buy);
    }
    return h('span', { class: 'src' }, sr.boss ? wh('npc', sr.npc_id, sr.boss) : 'trash', ' in ', sr.zone || '?',
      sr.chance_pct ? `, ${sr.chance_pct}%` : '', runs ? ` (about ${runs} run${runs > 1 ? 's' : ''})` : '', buy);
  }
  if (sr.kind === 'craft') {
    const own = sr.can_make ? 'you can make it now' : sr.your_rank !== null && sr.your_rank !== undefined
      ? `you have ${sr.your_rank}` : 'a crafter makes it';
    return h('span', { class: 'src' }, 'Craft ', wh('spell', sr.spell_id, `${sr.profession} ${sr.skill}`), ` (${own})`,
      ah ? h('span', {}, ', or ', h('b', { class: 'ahprice', text: priceText(ah.price_copper) }), ' on the Auction House') : '');
  }
  return h('span', { class: 'src', text: '?' });
}

// Gains under 2% of everything worn, or worth under half a point a clear:
// listed under "N small upgrades", not among the choices.
function small(u, a) {
  const share = u.gain_share_pct ?? (a.gear_score ? (u.gain / a.gear_score) * 100 : null);
  if (share !== null && share < 2) return true;
  const s = primarySource(u);
  return Boolean(s && s.kind === 'drop' && s.chance_pct && u.gain * s.chance_pct / 100 < 0.5);
}

const slotKey = (u) => (u.slot_name === 'Main hand' || u.slot_name === 'Off hand' ? 'Weapons' : u.slot_name);

// The display rule (owner, 2026-11-18, "WAY TOO MUCH options"): per slot the
// best, then at most 2 more that differ in a way that matters (a faster or
// different source, available now when the best isn't, a different gate);
// the rest behind "N more". Everything stays in the data and the MCP.
// The upgrade list is capped server side: a slot's own best (the doll's
// mark) joins it when the cap left it out.
function allUps(a) {
  const have = new Set(a.upgrades.map((u) => `${u.item_id}:${u.slot}`));
  const extra = a.slots.flatMap((sl) => [sl.best, sl.next]).filter((u) => u && !have.has(`${u.item_id}:${u.slot}`) && have.add(`${u.item_id}:${u.slot}`));
  return [...a.upgrades, ...extra];
}

function choose(a) {
  const by = new Map();
  for (const u of allUps(a)) {
    if (small(u, a)) continue;
    const k = slotKey(u);
    if (!by.has(k)) by.set(k, []);
    by.get(k).push(u);
  }
  const out = new Map();
  for (const [k, list] of by) {
    list.sort((x, y) => (feasible(y, a.level).now - feasible(x, a.level).now) || (y.gain - x.gain));
    const best = list[0];
    const sig = (u) => {
      const s = primarySource(u) || {};
      const f = feasible(u, a.level);
      return [s.kind, s.zone || s.profession || '', f.now, f.text].join('|');
    };
    const seen = new Set([sig(best)]);
    const alts = [];
    // One you already hold is the quickest alternative there is: it goes first.
    const held = (u) => Boolean(HELD[(primarySource(u) || {}).kind]);
    for (const u of list.slice(1).sort((x, y) => held(y) - held(x))) {
      if (alts.length >= 2) break;
      const g = sig(u);
      if (seen.has(g)) continue;
      seen.add(g);
      alts.push(u);
    }
    out.set(k, { best, alts, more: list.filter((u) => u !== best && !alts.includes(u)) });
  }
  return out;
}

// ------------------------------------------------------------ comparison

const STAT_NAMES = { str: 'Strength', agi: 'Agility', sta: 'Stamina', int: 'Intellect', spi: 'Spirit', armor: 'Armor',
  dps: 'Weapon DPS', rdps: 'Ranged DPS', sp: 'Spell power', heal: 'Healing', mp5: 'Mana per 5 s', hp5: 'Health per 5 s',
  spArcane: 'Arcane power', spFire: 'Fire power', spFrost: 'Frost power', spNature: 'Nature power',
  spShadow: 'Shadow power', spHoly: 'Holy power', spellCrit: 'Spell crit', spellHit: 'Spell hit', crit: 'Crit',
  hit: 'Hit', ap: 'Attack power', rap: 'Ranged AP', def: 'Defense', dodge: 'Dodge', parry: 'Parry', block: 'Block',
  haste: 'Haste', health: 'Health', mana: 'Mana' };

function addStats(...items) {
  const out = {};
  for (const i of items) for (const [k, v] of Object.entries((i && i.stats) || {})) out[k] = (out[k] || 0) + v;
  return out;
}

// The worn side of a comparison: the slot the gear advice replaces (rings
// and trinkets: the weaker one, wowstats/gear.py), and for a weapon setup
// both hands.
function wornFor(u, a) {
  const at = (slot) => (a.slots.find((sl) => sl.slot === slot) || {}).current || null;
  if (u.slot === 16 || u.slot === 17) {
    const hands = [at(16), at(17)].filter(Boolean);
    return { items: hands, score: hands.reduce((x, i) => x + (i.score || 0), 0) };
  }
  const cur = at(u.slot);
  return { items: cur ? [cur] : [], score: cur ? cur.score || 0 : 0 };
}

function statTable(worn, cand, w) {
  const ws = addStats(...worn), cs = addStats(...cand);
  const keys = [...new Set([...Object.keys(ws), ...Object.keys(cs)])]
    .filter((k) => (ws[k] || 0) !== (cs[k] || 0))
    .sort((x, y) => Math.abs(((cs[y] || 0) - (ws[y] || 0)) * (w[y] || 0)) - Math.abs(((cs[x] || 0) - (ws[x] || 0)) * (w[x] || 0)));
  if (!keys.length) return h('p', { class: 'sub', text: 'Same stats.' });
  return h('table', { class: 'cmpstats' },
    h('thead', {}, h('tr', {}, h('th', { text: 'Stat' }), h('th', { text: 'Worn' }), h('th', { text: 'New' }),
      h('th', { text: 'Change' }), h('th', { text: 'Points', title: 'The change times your weight for that stat' }))),
    h('tbody', {}, keys.map((k) => {
      const d = (cs[k] || 0) - (ws[k] || 0);
      const p = d * (w[k] || 0);
      const cls = !w[k] ? 'flat' : p > 0 ? 'better' : 'worse';
      const n = (v) => (k === 'dps' || k === 'rdps' ? v.toFixed(1) : fmtNum(v));
      return h('tr', { class: cls }, h('th', { text: STAT_NAMES[k] || k }), h('td', { text: ws[k] ? n(ws[k]) : '–' }),
        h('td', { text: cs[k] ? n(cs[k]) : '–' }), h('td', { text: (d > 0 ? '+' : '−') + n(Math.abs(d)) }),
        h('td', { text: w[k] ? pts(p) : 'not scored' }));
    })));
}

// The comparison (owner, 2026-11-18): what you wear against the best
// replacement, side by side, the stat changes coloured by your weights,
// where to get it, when it opens, and two alternatives. The gain is in the
// header; the score's arithmetic stays out (owner, 2026-11-19).
function compareEl(u, a, alts, onPick) {
  const worn = wornFor(u, a);
  const cand = [u, u.with && u.with.item_id ? u.with : null].filter(Boolean);
  const keepsOther = u.with && u.with.kind === 'worn';
  const wornNames = worn.items.length ? worn.items : null;
  const f = feasible(u, a.level);
  const side = (title, items, empty) => h('div', { class: 'cmpside' }, h('h4', { text: title }),
    items && items.length ? items.map((i) => h('div', { class: 'cmpitem' }, itemChip(i),
      h('span', { class: 'sub', text: [i.slot_name, i.item_level ? `item level ${i.item_level}` : null].filter(Boolean).join(', ') }),
      wh('item', i.item_id, 'Wowhead', 'whsmall')))
      : h('p', { class: 'sub', text: empty }));
  const kids = [
    h('div', { class: 'cmphead' },
      h('h3', {}, (u.slot === 16 || u.slot === 17) ? 'Weapons' : u.slot_name, ': ', h('span', { class: 'gainbig', text: pts(u.gain) }),
        h('small', { text: u.gain_share_pct !== null && u.gain_share_pct !== undefined ? ` ${Math.round(u.gain_share_pct)}% of your gear` : '' })),
      h('span', { class: 'pill' + (f.now ? ' now' : ''), text: f.now ? 'Now' : f.text, title: f.title }),
      h('button', { type: 'button', class: 'cmpclose', 'aria-label': 'Close the comparison', text: '×' })),
    h('div', { class: 'cmpsides' }, side('You wear', wornNames, 'Nothing in this slot.'), side('Best replacement', cand, '')),
    statTable(worn.items, cand, a.weights || {}),
    keepsOther ? h('p', { class: 'cmpcalc sub', text: 'You keep the other hand as it is.' }) : null,
    h('div', { class: 'cmpwhere' }, h('h4', { text: 'Where to get it' }),
      h('ul', {}, cand.map((i) => (i.sources || []).map((sr) => h('li', {}, i !== u ? `${i.name}: ` : '', sourceEl(sr, a.level)))).flat())),
    f.now ? null : h('p', { class: 'sub', text: f.title }),
  ];
  if (alts && alts.length) {
    kids.push(h('div', { class: 'cmpalts' }, h('h4', { text: 'Also worth a look' }),
      h('ul', {}, alts.map((x) => {
        const s = primarySource(x);
        const b = h('button', { type: 'button', class: 'linkish', text: x.name });
        b.addEventListener('click', () => onPick(x));
        return h('li', {}, b, ` ${pts(x.gain)}, `, feasible(x, a.level).text.toLowerCase(), s ? [', ', sourceEl(s, a.level, ahOf(x))] : null);
      }))));
  }
  const box = h('section', { class: 'cmp', 'aria-label': `Compare ${u.name}`, tabindex: '-1' }, ...kids);
  box.querySelector('.cmpclose').addEventListener('click', () => box.remove());
  return box;
}

// Opens the comparison for u right after `anchor` (the doll, or a row of a
// list), one at a time on the page.
function openCompare(u, anchor, a) {
  document.querySelectorAll('.cmp').forEach((x) => x.remove());
  window.wowTip.close();
  const pick = choose(a).get(slotKey(u));
  // The slot's best (from the doll) and the same item in the upgrade list
  // are two objects: compare by item, partner included, so it never shows
  // as its own alternative.
  const same = (x) => x.item_id === u.item_id && ((x.with && x.with.item_id) || 0) === ((u.with && u.with.item_id) || 0);
  const alts = pick ? [pick.best, ...pick.alts].filter((x) => !same(x)).slice(0, 2) : [];
  const box = compareEl(u, a, alts, (x) => openCompare(x, anchor, a));
  if (anchor.tagName === 'LI') box.classList.add('inrow');
  anchor.after(box);
  box.focus({ preventScroll: true });
  box.scrollIntoView({ block: 'nearest', behavior: reduceMotion ? 'auto' : 'smooth' });
}



function renderAdvice(a) {
  const box = $('#advice');
  if (!box) return;
  state.advice = a;
  if (!a.available) {
    const name = a.name || (state.data && state.data.header.name) || 'This character';
    fill(box, h('h3', { class: 'adv-h', text: 'Upgrades' }),
      a.polestar === false
        ? h('p', { class: 'empty' }, h('strong', { text: 'Gear advice needs Polestar.' }), ' ',
          `Tick Polestar and Polestar Recorder for ${name} in AddOns at character select, then /reload.`)
        : h('p', { class: 'empty' }, h('strong', { text: 'No gear advice yet.' }), ' ',
          `Polestar’s scoring has nothing to work on: ${a.why}.`));
    return;
  }
  const picks = choose(a);
  // The doll: a mark on each slot with something better now; a click on
  // that slot opens the comparison (a hover keeps the plain tooltip).
  document.querySelectorAll('#gear .slot .iup').forEach((el) => el.remove());
  for (const sl of a.slots) {
    const li = document.querySelector(`#gear .slot[data-slot="${sl.slot}"]`);
    if (!li) continue;
    const u = sl.best;
    if (u) li.querySelector('.imeta').after(h('span', { class: 'iup', text: u.fills_empty ? '▲ something to wear' : `▲ ${pts(u.gain)} at hand`, title: 'Click to compare' }));
    if (li.dataset.cmp) continue;
    li.dataset.cmp = '1';
    li.addEventListener('click', (e) => {
      const cur = state.advice && state.advice.slots.find((x) => x.slot === sl.slot);
      if (!cur || !cur.best) return;  // nothing better: the plain tooltip
      e.preventDefault();
      e.stopPropagation();
      openCompare(cur.best, document.querySelector('#gear .doll'), state.advice);
    }, true);
  }
  // "Automatic" drops the page's own choice: Polestar's setting, the
  // player's pick or the class default decide again.
  const auto = a.spec.source !== 'page';
  const sel = h('select', { id: 'gearspec', 'aria-label': 'Score for spec' },
    h('option', { value: '', text: auto ? `Automatic: ${a.spec.label}` : 'Automatic', selected: auto }),
    a.spec.choices.map((c) => h('option', { value: c.key, text: c.label, selected: !auto && c.key === a.spec.key })));
  sel.addEventListener('change', () => {
    saveSpec(state.char, sel.value);
    loadAdvice(state.char);
  });
  const view = state.adviceView || 'where';
  const toggle = h('div', { class: 'order', role: 'group', 'aria-label': 'Show upgrades' },
    [['where', 'By where you get it'], ['slot', 'By slot']].map(([k, t]) => {
      const b = h('button', { type: 'button', 'aria-pressed': String(view === k), text: t });
      b.addEventListener('click', () => { state.adviceView = k; renderAdvice(state.advice); });
      return b;
    }));
  const head = h('div', { class: 'adv-head' },
    h('div', { class: 'nuhead' }, h('h3', { class: 'adv-h', text: 'Upgrades' }), toggle),
    h('label', { class: 'specpick' }, h('span', { text: 'Scored for' }), sel),
    h('p', { class: 'sub', text: 'Click an item to compare it with what you wear.' }));
  const smalls = allUps(a).filter((u) => small(u, a));
  const shown = new Set();
  for (const p of picks.values()) [p.best, ...p.alts].forEach((u) => shown.add(u));
  const more = allUps(a).filter((u) => !small(u, a) && !shown.has(u));
  const body = view === 'slot' ? bySlot(a, picks) : byWhere(a, [...shown]);
  // #up-<slot>: where the Overview's upgrade moves lead. The slot's best
  // carries it in either view (one id per slot: rings and trinkets share a
  // pick, so the first row of that slot takes it).
  for (const p of picks.values()) {
    const id = 'up-' + p.best.slot;
    const row = body.querySelector(`[data-up="${p.best.slot}-${p.best.item_id}"]`);
    if (row && !body.querySelector('#' + id)) row.id = id;
  }
  const none = a.slots.filter((sl) => !sl.best && !sl.next && sl.current).map((sl) => sl.slot_name);
  fill(box, head, a.ah && a.ah.notice ? h('p', { class: 'ahnote', text: a.ah.notice }) : null, body,
    none.length ? h('p', { class: 'sub nothing', text: `Nothing better for: ${[...new Set(none)].join(', ')}.` }) : null,
    more.length ? h('details', { class: 'upmore' }, h('summary', { text: `${more.length} more upgrade${more.length > 1 ? 's' : ''}` }),
      compactList(a, more)) : null,
    smalls.length ? h('details', { class: 'upmore' }, h('summary', { text: `${smalls.length} small upgrade${smalls.length > 1 ? 's' : ''} (under 2% of your gear, or a rare drop)` }),
      compactList(a, smalls)) : null);
}

function candRow(u, a, extra) {
  const li = h('li', { class: 'cand', 'data-slot': slotKey(u), 'data-up': `${u.slot}-${u.item_id}` });
  const partner = u.with && u.with.item_id ? h('span', { class: 'sub', text: ` with ${u.with.name} (${u.with.slot_name.toLowerCase()})` }) : null;
  li.append(
    h('div', { class: 'candmain' }, itemChip(u), partner),
    h('div', { class: 'candmeta' }, h('span', { class: 'slotname', text: slotKey(u) === 'Weapons' ? 'Weapons' : u.slot_name }),
      h('b', { class: 'gain', text: pts(u.gain) }), feasEl(u, a.level), extra || null));
  li.querySelector('.candmain .itm').addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    openCompare(u, li, a);
  }, true);
  return li;
}

// "By where you get it": one dungeon run, the quests of one zone, a
// profession's crafts, the auction house, world drops. Each group with its
// expected gain (a dungeon: per clear, the chance-weighted best per slot),
// ranked by it.
function byWhere(a, list) {
  const groups = new Map();
  for (const u of list) {
    const s = primarySource(u);
    const g = sourceGroup(s);
    if (!groups.has(g.key)) groups.set(g.key, { ...g, items: [] });
    groups.get(g.key).items.push({ u, s });
  }
  for (const g of groups.values()) {
    if (g.kind === 'dungeon' || g.kind === 'world') {
      const bySlot = {};
      for (const { u, s } of g.items) (bySlot[slotKey(u)] = bySlot[slotKey(u)] || []).push([u.gain, (s.chance_pct || 0) / 100]);
      g.value = Object.values(bySlot).reduce((acc, xs) => {
        let left = 1, e = 0;
        for (const [gain, p] of xs.sort((x, y) => y[0] - x[0])) { e += gain * p * left; left *= 1 - p; }
        return acc + e;
      }, 0);
      g.label = `${pts(g.value)} expected per clear`;
    } else {
      g.value = g.items.filter(({ u }) => feasible(u, a.level).now).reduce((x, { u }) => x + u.gain, 0);
      g.label = g.value ? `${pts(g.value)} if you take every one you can now` : 'none open yet';
    }
  }
  // What you already hold first (no trip), then by value.
  for (const g of groups.values()) {
    if (g.kind !== 'ah') continue;
    const now = g.items.filter(({ u }) => feasible(u, a.level).now);
    const cost = now.reduce((x, { u }) => x + ahOf(u).price_copper, 0);
    g.label = now.length ? `${pts(g.value)} for ${priceText(cost)} in all` : 'none you can wear yet';
  }
  const ranked = [...groups.values()].sort((x, y) => (y.kind === 'held') - (x.kind === 'held') || y.value - x.value);
  return h('ol', { class: 'srcgroups' }, ranked.map((g) => h('li', { class: 'srcgroup' },
    h('div', { class: 'sghead' }, h('h4', {}, g.title),
      g.levels && g.levels.length === 2 ? h('span', { class: 'sub', text: `levels ${g.levels[0]}-${g.levels[1]}` }) : null,
      h('span', { class: 'sgval', text: g.label })),
    ...groupRows(g, a))));
}

// A group shows its best 3 (the display rule holds per list, not only per
// slot: one profession can offer seven items); the rest folds under it.
function groupRows(g, a) {
  const row = ({ u, s }) => candRow(u, a, s ? sourceEl(s, a.level, ahOf(u)) : null);
  // The Auction House: the most gain for the gold first.
  const per = ({ u }) => (ahOf(u) || {}).gain_per_gold || 0;
  const items = g.items.sort((x, y) => (g.kind === 'ah' ? per(y) - per(x) : 0) || y.u.gain - x.u.gain);
  const rest = items.slice(3);
  return [h('ul', { class: 'cands' }, items.slice(0, 3).map(row)),
    rest.length ? h('details', { class: 'upmore grpmore' }, h('summary', { text: `${rest.length} more from ${g.title}` }),
      h('ul', { class: 'cands' }, rest.map(row))) : null];
}

// "By slot": one line a slot, the best for it.
function bySlot(a, picks) {
  const rows = [];
  const order = ['Head', 'Neck', 'Shoulder', 'Back', 'Chest', 'Wrist', 'Hands', 'Waist', 'Legs', 'Feet', 'Finger', 'Trinket', 'Weapons', 'Ranged'];
  for (const k of order) {
    const p = picks.get(k);
    if (!p) continue;
    const u = p.best;
    const worn = wornFor(u, a).items;
    const s = primarySource(u);
    const tr = h('tr', { class: 'cand', 'data-slot': k, 'data-up': `${u.slot}-${u.item_id}` },
      h('th', { text: k }),
      h('td', {}, worn.length ? worn.map((i) => itemChip(i, { label: false })) : h('span', { class: 'sub', text: 'empty' })),
      h('td', { class: 'best' }, itemChip(u)),
      h('td', { class: 'gain', text: pts(u.gain) }),
      h('td', {}, feasEl(u, a.level)),
      h('td', { class: 'srccell' }, s ? sourceEl(s, a.level, ahOf(u)) : ''));
    tr.querySelector('.best .itm').addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const row = h('tr', { class: 'cmprow' }, h('td', { colspan: 6 }));
      document.querySelectorAll('.cmprow').forEach((x) => x.remove());
      tr.after(row);
      openCompare(u, row.firstChild.appendChild(h('span')), a);
    }, true);
    rows.push(tr);
  }
  return h('div', { class: 'tablewrap' }, h('table', { class: 'byslot' },
    h('thead', {}, h('tr', {}, ['Slot', 'Worn', 'Best', 'Gain', 'Opens', 'Source'].map((t) => h('th', { text: t })))),
    h('tbody', {}, rows)));
}

function compactList(a, list) {
  return h('ul', { class: 'cands compact' }, list.sort((x, y) => y.gain - x.gain).map((u) => {
    const s = primarySource(u);
    return candRow(u, a, s ? sourceEl(s, a.level, ahOf(u)) : null);
  }));
}

async function loadAdvice(name) {
  const spec = savedSpec(name);
  try {
    const a = await getJSON(api('gear', STATIC ? { name } : { name, spec }));
    if (name === state.char) renderAdvice(a);
  } catch (e) {
    const box = $('#advice');
    if (box && name === state.char) fill(box, h('p', { class: 'err', text: `Couldn’t load gear advice: ${e.message}.` }));
  }
}

// ------------------------------------------------------------ market strip

function sparkline(series, w = 96, hgt = 24) {
  const vals = series.map((p) => p[1]);
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const span = hi - lo || 1;
  const pts = series.map((p, i) => `${(i / Math.max(1, series.length - 1)) * w},${hgt - 2 - ((p[1] - lo) / span) * (hgt - 4)}`);
  return s('svg', { width: w, height: hgt, viewBox: `0 0 ${w} ${hgt}`, 'aria-hidden': 'true' },
    s('polyline', { points: pts.join(' '), fill: 'none', stroke: 'var(--gold)', 'stroke-width': 1.5, 'stroke-linejoin': 'round' }));
}

function renderMarket(m, err) {
  const el = $('#market');
  if (!el) return;
  if (err) {
    fill(el, h('p', { class: 'note' }, `Token and auction numbers are unavailable: ${err}.`));
    return;
  }
  // Not set up, or nothing read yet: nothing to show and nothing the
  // player can do about it here (README, "Battle.net client").
  if (!m || !m.configured) { fill(el); return; }
  const kids = [];
  if (m.token) {
    const ch = m.token.change_7d;
    kids.push(h('div', { class: 'm', title: 'WoW Token on Retail EU, last 7 days. Forever has no token of its own; game time is shared, the gold isn\u2019t.' },
      m.token.label, h('strong', {}, h('span', { class: 'coin g', text: fmtNum(m.token.gold), 'aria-label': `${fmtNum(m.token.gold)} gold` })), sparkline(m.token.series),
      h('span', { text: `${ch >= 0 ? '+' : '\u2212'}${fmtNum(Math.abs(ch))} gold this week` })));
  }
  for (const r of m.realms) {
    kids.push(h('div', { class: 'm', title: 'Forever has no realms; the API reports one status per ruleset.' },
      h('span', { class: r.up ? 'up' : 'down', text: `${r.name} ruleset` }),
      h('span', { text: r.up ? (r.dipped_24h ? 'up, was down today' : 'up') : 'down' })));
  }
  if (m.auctions.length) {
    const item = (a) => h('li', {
      title: a.change_1d !== null ? `${a.change_1d >= 0 ? '+' : '\u2212'}${fmtNum(Math.abs(a.change_1d))} copper since yesterday` : '',
    }, h('span', { text: a.item }), h('span', { class: 'r' }, coins(a.copper),
      a.quantity !== null ? h('small', { text: ` ${fmtNum(a.quantity)} listed` }) : null));
    kids.push(h('details', { class: 'm ahbox' },
      h('summary', {}, `${m.faction} auction house, lowest buyout`,
        h('span', { class: 'ahpeek' }, m.auctions.slice(0, 2).map((a) => h('span', {}, `${a.item} `, coins(a.copper))))),
      h('ul', { class: 'ahlist' }, m.auctions.map(item))));
  }
  fill(el, ...kids);
}

// ------------------------------------------------------------ levels

// The climb's x axis: played time (the default; what pace and ETA use too)
// or calendar time. Remembered per browser.
function climbMode() {
  if (PUBLIC) return 'played';  // no dates in the public climb
  try { return localStorage.getItem('wow-climb') === 'calendar' ? 'calendar' : 'played'; } catch (e) { return 'played'; }
}

// Seconds played before the first tracked level, from Altoholic's /played
// (header.played_total_s, total since level 1); 0 without it.
function playedBefore(hd) {
  if (!hd.played_total_s || hd.played_s === null || hd.played_s === undefined) return 0;
  return Math.max(0, hd.played_total_s - hd.played_s);
}

// Level over played time: each finished level is as wide as its split (time
// logged in at that level), the level in play as wide as the Recorder's
// logged-in time since the ding. Offline time takes no room.
function climbPlayed(d) {
  const lv = d.levels.filter((l) => l.seconds > 0);
  const hd = d.header;
  if (!lv.length) return [];
  const pts = [{ x: 0, level: lv[0].level }];
  let x = 0;
  for (const l of lv) {
    x += l.seconds;
    pts.push({ x, level: l.level + 1, ding: true, at: l.at });
  }
  const top = lv[lv.length - 1].level + 1;
  const frac = hd.xp_max ? Math.min(0.999, (hd.xp || 0) / hd.xp_max) : 0;
  if (hd.level === top && hd.time_in_level_s) pts.push({ x: x + hd.time_in_level_s, level: hd.level + frac, now: true, at: hd.seen });
  return pts;
}

function climbCalendar(d) {
  const lv = d.levels;
  const hd = d.header;
  const pts = [];
  if (lv.length && lv[0].at) {
    // Calendar start of the first tracked level: its ding minus its played
    // time, a lower bound when there was time offline in between.
    const t0 = toDate(lv[0].at);
    if (t0) pts.push({ x: t0.getTime() - lv[0].seconds * 1000, level: lv[0].level });
  }
  for (const c of d.climb) pts.push({ x: toDate(c.t).getTime(), level: c.level, ding: true, at: c.t });
  const seen = toDate(hd.seen);
  const frac = hd.xp_max ? Math.min(0.999, (hd.xp || 0) / hd.xp_max) : 0;
  if (seen && hd.level) pts.push({ x: seen.getTime(), level: hd.level + frac, now: true, at: hd.seen });
  return pts.sort((p, q) => p.x - q.x);
}

function renderClimb(container, d) {
  const hd = d.header;
  if (!(d.climb || []).length && !d.levels.length) return;
  // Drawn at its real width so the labels keep their size: on a hidden tab
  // the width is 0, so wait until it shows (and redraw when it changes).
  if (!container._ro && window.ResizeObserver) {
    let w = 0;
    container._ro = new ResizeObserver(() => {
      const nw = Math.round(container.clientWidth);
      if (nw && Math.abs(nw - w) > 8) { w = nw; renderClimb(container, container._d); }
    });
    container._ro.observe(container);
  }
  container._d = d;
  if (!container.clientWidth) return;
  const mode = climbMode();
  const played = mode === 'played';
  const pts = played ? climbPlayed(d) : climbCalendar(d);
  let routeShown = false;
  if (pts.length < 2) return;

  const W = Math.max(280, container.clientWidth || 600);
  const H = W < 520 ? 190 : 230;
  const m = { l: 34, r: 14, t: 12, b: 26 };
  const x0 = pts[0].x;
  const x1 = pts[pts.length - 1].x;
  // Altoholic's /played: the hours played before tracking began, so the axis
  // and the tooltips read total played since level 1.
  const off = played ? playedBefore(hd) : 0;
  const y0 = Math.floor(Math.min(...pts.map((p) => p.level)));
  const y1 = Math.ceil(Math.max(...pts.map((p) => p.level)) + 0.01);
  const x = (v) => m.l + ((v - x0) / Math.max(1, x1 - x0)) * (W - m.l - m.r);
  const y = (v) => H - m.b - ((v - y0) / Math.max(1, y1 - y0)) * (H - m.t - m.b);

  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img',
    'aria-label': played
      ? `Level over played time: level ${pts[0].level} to ${hd.level}, ${dur(off + x1 - x0)} played`
      : `Level over calendar time, from ${pts[0].level} on ${day(new Date(x0))} to ${hd.level} on ${day(new Date(x1))}` });
  // A label every level while they have 15px of room, else every 2, 5, 10.
  const room = (H - m.t - m.b) / Math.max(1, y1 - y0);
  const stepY = [1, 2, 5, 10].find((st) => room * st >= 15) || 10;
  for (let v = Math.ceil(y0 / stepY) * stepY; v <= y1; v += stepY) {
    svg.append(s('line', { class: 'grid-line', x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }),
      s('text', { class: 'axis', x: m.l - 8, y: y(v) + 4, 'text-anchor': 'end', text: v }));
  }
  const fit = Math.max(1, Math.floor((W - m.l - m.r) / 64));
  if (played) {
    // One tick per round number of hours played, labels never touching.
    const hours = (x1 - x0) / 3600;
    const step = [1, 2, 5, 10, 20, 50, 100].find((h) => hours / h <= fit) || 200;
    for (let hh = Math.floor(off / 3600 / step) * step + step; hh * 3600 <= off + x1 - x0; hh += step) {
      const t = x0 + hh * 3600 - off;
      svg.append(s('line', { class: 'grid-line', x1: x(t), x2: x(t), y1: m.t, y2: H - m.b, 'stroke-dasharray': '2 4' }),
        s('text', { class: 'axis', x: x(t) + 4, y: H - 8, text: `${hh}h` }));
    }
  } else {
    // One tick per local midnight; thinned so labels never touch.
    const days = [];
    const d0 = new Date(x0); d0.setHours(24, 0, 0, 0);
    for (let t = d0.getTime(); t <= x1; t += 86400000) days.push(t);
    const every = Math.max(1, Math.ceil(days.length / fit));
    days.forEach((t, i) => {
      svg.append(s('line', { class: 'grid-line', x1: x(t), x2: x(t), y1: m.t, y2: H - m.b, 'stroke-dasharray': '2 4' }));
      if (i % every === 0) svg.append(s('text', { class: 'axis', x: x(t) + 4, y: H - 8, text: shortDay.format(new Date(t)) }));
    });
  }
  // Stairs: the level holds until the ding.
  let dPath = `M${x(pts[0].x)},${y(pts[0].level)}`;
  for (let i = 1; i < pts.length; i++) {
    const p = pts[i];
    const prev = pts[i - 1];
    dPath += ` H${x(p.x)}`;
    if (!p.now) dPath += ` V${y(p.level)}`;
    else dPath += ` V${y(Math.max(prev.level, Math.floor(p.level)))}`;
  }
  // A wash of gold under the stairs, fading to nothing at the floor.
  svg.prepend(s('defs', {}, s('linearGradient', { id: 'climbfill', x1: 0, y1: 0, x2: 0, y2: 1 },
    s('stop', { offset: 0, class: 'stop-a' }), s('stop', { offset: 1, class: 'stop-b' }))));
  svg.append(s('path', { class: 'area', d: `${dPath} V${H - m.b} H${x(pts[0].x)} Z` }));
  svg.append(s('path', { class: 'line', d: dPath }));
  // Polestar's route from the same start, level by level, where it has a time.
  if (played) {
    const lv = d.levels.filter((l) => l.seconds > 0);
    let rx = x0;
    let rPath = '';
    for (const l of lv) {
      if (!l.route_s) break;
      if (!rPath) rPath = `M${x(rx)},${y(l.level)}`;
      rx += l.route_s;
      if (rx > x1) break;
      rPath += ` H${x(rx)} V${y(l.level + 1)}`;
    }
    if (rPath) {
      svg.append(s('path', { class: 'route', d: rPath }, s('title', { text: 'Polestar’s route' })));
      routeShown = true;
    }
  }
  // Tooltips carry the other axis: the date on the played chart, the played
  // time on the calendar one.
  const playedAt = {};
  for (const p of climbPlayed(d)) playedAt[Math.floor(p.level)] = playedBefore(hd) + p.x;
  for (const p of pts) {
    const lvl = Math.floor(p.level);
    const other = played ? (p.at ? `, ${when(p.at)}` : '') : (playedAt[lvl] !== undefined ? `, ${dur(playedAt[lvl])} played` : '');
    if (p.ding) {
      const cx = x(p.x);
      const cy = y(p.level);
      svg.append(s('path', { class: 'ding', d: `M${cx} ${cy - 6}L${cx + 5} ${cy}L${cx} ${cy + 6}L${cx - 5} ${cy}Z` },
        s('title', { text: `Level ${p.level}` + (played ? ` after ${dur(off + p.x - x0)} played` : '') + other })));
    } else if (p.now) {
      svg.append(s('circle', { class: 'now', cx: x(p.x), cy: y(p.level), r: 5 },
        s('title', { text: `Now: level ${hd.level}, ${Math.floor((p.level % 1) * 100)}% through` + (played ? `, ${dur(off + p.x - x0)} played` : '') + other })));
    }
  }
  const since = off
    ? `Played since level 1 (Altoholic’s /played; recorded from level ${hd.first_level})`
    : hd.first_partial || (hd.first_level && hd.first_level > 1)
      ? `Played since tracking began (level ${hd.first_level})` : 'Played time';
  const toggle = PUBLIC ? null : h('button', { type: 'button', class: 'linkish', text: played ? 'Show dates' : 'Show played time' });
  if (toggle) toggle.addEventListener('click', () => {
    try { localStorage.setItem('wow-climb', played ? 'calendar' : 'played'); } catch (e) { /* private mode */ }
    renderClimb(container, d);
  });
  fill(container, svg, h('p', { class: 'axis-cap' },
    h('span', { text: played ? `${since}; offline time takes no room.` : 'Calendar days; the flat stretches include time offline.' }),
    routeShown ? h('span', { class: 'routekey', text: ' Dashed: Polestar’s route from the same start.' }) : null, ' ', toggle));
}

function renderLevels(d) {
  const body = $('#levels .body');
  const lv = d.levels;
  const hd = d.header;
  if (!lv.length) {
    emptyNote(body, 'No finished level yet.', 'Play to the next level with Polestar on, then /reload.');
    return;
  }
  const done = lv.filter((l) => !l.partial);
  const parts = [`${dur(hd.played_s)} played from level ${hd.first_level} to ${hd.level}`];
  const rt = routeTotals(d);
  if (hd.played_total_s) parts.push(`${dur(hd.played_total_s)} since level 1 (Altoholic’s /played)`);
  if (hd.kills !== null && hd.kills !== undefined) parts.push(`${fmtNum(hd.kills)} kills and ${fmtNum(hd.quests || 0)} quests handed in`);
  const fastest = done.length ? done.reduce((a, b) => (b.seconds < a.seconds ? b : a)) : null;
  const slowest = done.length ? done.reduce((a, b) => (b.seconds > a.seconds ? b : a)) : null;
  let lede = parts.join(', ') + '.';
  if (fastest && slowest && fastest !== slowest) lede += ` Fastest was ${fastest.level} (${dur(fastest.seconds)}), slowest ${slowest.level} (${dur(slowest.seconds)}).`;
  if (rt.n) lede += ` Polestar’s route plans ${dur(rt.route)} for the ${rt.n} finished level${rt.n === 1 ? '' : 's'} it times: ${(rt.played / rt.route).toFixed(1)}× here.`;

  const chart = h('div', { class: 'climb' });
  const max = Math.max(...lv.map((l) => l.seconds), 1);
  const rows = h('ul', { class: 'rows levels' }, [...lv].reverse().map((l) =>
    h('li', { class: l.partial ? 'partial' : '' },
      h('span', { class: 'lv', text: l.level }),
      h('div', {},
        h('div', { class: 'track' }, h('div', { class: 'fill', 'width%': (l.seconds / max) * 100 })),
        l.route_s ? h('div', { class: 'track route', title: `Polestar’s route: ${dur(l.route_s)}` },
          h('div', { class: 'fill', 'width%': Math.min(100, (l.route_s / max) * 100) })) : null,
        h('div', { class: 'meta' },
          'kills' in l ? h('span', { class: 'dot k' }, h('b', { text: l.kills ?? '?' }), ' kills') : null,
          'quests' in l ? h('span', { class: 'dot q' }, h('b', { text: l.quests ?? '?' }), ' quests') : null,
          l.route_s ? h('span', { class: 'dot r' }, 'route ', h('b', { text: dur(l.route_s) })) : null,
          l.at ? h('span', { text: day(l.at) }) : null)),
      h('span', { class: 'val' }, dur(l.seconds),
        h('small', { text: l.partial ? 'partly recorded' : (l.xp_per_hour ? `${fmtK(l.xp_per_hour)} XP/h` : '') })))));
  fill(body, h('p', { class: 'lede', text: lede }), chart,
    h('p', { class: 'legend' }, h('span', { text: 'Time played per level; a striped bar was only partly recorded (the tracking started during it), so it is not counted in the pace.'
      + (rt.n ? ' The thin bar under it is Polestar’s route time for that level.' : '') })),
    rows);
  renderClimb(chart, d);
}

// ------------------------------------------------------------ zones

function barRows(items, { name, sub, value, valueSub, cls }) {
  const max = Math.max(...items.map(value), 1);
  return h('ul', { class: 'rows' }, items.map((it) =>
    h('li', {},
      h('span', { class: 'name', title: name(it) }, name(it), sub ? h('small', { text: sub(it) }) : null),
      h('div', { class: 'track' }, h('div', { class: 'fill' + (cls ? ' ' + cls : ''), 'width%': (value(it) / max) * 100 })),
      h('span', { class: 'val' }, valueSub ? [valueSub(it)[0], h('small', { text: valueSub(it)[1] })] : value(it)))));
}

function renderZones(d) {
  const body = $('#zones .body');
  const z = d.zones;
  if (!z.length) {
    emptyNote(body, 'No zone times yet.', RECORDER_ON);
    return;
  }
  const total = z.reduce((a, b) => a + b.secs, 0);
  const top = z[0];
  fill(body, 
    h('p', { class: 'lede', text: `${top.zone} took ${Math.round((top.secs / total) * 100)}% of ${dur(total)} in ${z.length} zones.` }),
    barRows(z.slice(0, 12), {
      name: (r) => r.zone, value: (r) => r.secs,
      valueSub: (r) => [dur(r.secs), `${r.visits} visit${r.visits === 1 ? '' : 's'}`],
    }),
    z.length > 12 ? h('p', { class: 'empty', text: `And ${z.length - 12} more, ${dur(z.slice(12).reduce((a, b) => a + b.secs, 0))} together.` }) : null);
}

// ------------------------------------------------------------ guide

function renderGuide(d) {
  const body = $('#guide .body');
  const st = d.steps;
  if (!st.total && !st.skipped.length) {
    emptyNote(body, 'No guide steps yet.', 'Follow a Polestar guide with Polestar Recorder on, then /reload.');
    return;
  }
  const kids = [
    h('dl', { class: 'sum' },
      h('div', {}, h('dt', { text: 'Steps done' }), h('dd', { text: fmtNum(st.total) })),
      h('div', {}, h('dt', { text: 'Typical step' }), h('dd', { text: st.median_s ? dur(st.median_s) : '?' })),
      h('div', {}, h('dt', { text: 'Skipped' }), h('dd', { text: fmtNum(st.skipped_total) }))),
    h('h3', { text: 'Slowest steps' }),
    h('ul', { class: 'list' }, st.slowest.slice(0, 8).map((a) =>
      h('li', {},
        h('div', {}, `Step ${a.step}` + (a.title ? `: ${a.title}` : ''),
          st.guides.length > 1 ? h('span', { class: 'tag', text: a.guide_title || a.guide, title: a.guide }) : null,
          h('div', { class: 'sub', text: a.zone || '' })),
        h('div', { class: 'r' }, dur(a.secs),
          h('div', { class: 'sub', text: st.median_s ? `${(a.secs / st.median_s).toFixed(1)}× a typical step` : '' }))))),
  ];
  kids.push(h('h3', { text: 'Skipped' }));
  if (!st.skipped.length) {
    kids.push(h('p', { class: 'empty', text: 'None: every step was reached in order.' }));
  } else {
    kids.push(h('ul', { class: 'list' }, st.skipped.slice(0, 8).map((sk) =>
      h('li', {},
        h('div', {}, sk.steps.length === 1 ? `Step ${sk.steps[0]}` : `Steps ${sk.steps[0]} to ${sk.steps[sk.steps.length - 1]}`,
          st.guides.length > 1 ? h('span', { class: 'tag', text: sk.guide_title || sk.guide, title: sk.guide }) : null,
          h('div', { class: 'sub', text: (sk.how === 'jumped' ? 'jumped past' : 'skipped in the guide') + (sk.zone ? ` in ${sk.zone}` : '') })),
        h('div', { class: 'r sub', text: when(sk.t) })))));
  }
  if ((st.picks || []).length) {
    // Picking or switching guide: not a step, listed on its own.
    kids.push(h('h3', { text: 'Guides picked' }),
      h('ul', { class: 'list' }, st.picks.slice(0, 6).map((pk) =>
        h('li', {}, h('div', {}, `Picked guide ${pk.title}`), h('div', { class: 'r sub', text: when(pk.t) })))));
  }
  fill(body, ...kids);
}

// ------------------------------------------------------------ dungeons

// The public view: which dungeon, when, at what level (redact.PUBLIC).
// The full view and /zogzog/: one block per dungeon that matters to the
// character (wowstats/plan/dungeonview.py), what it holds for him and what a
// trip is worth, coloured by that whole value. The runs show until that
// answer comes (it reads every event, so it can take a while), and stay if
// it can't come.
function renderDungeons(d) {
  const body = $('#dungeons .body');
  const runs = d.dungeons;
  if (!PUBLIC && state.dungeons && state.dungeons.char === state.char) return;  // the panel is drawn already
  $('#dungeons').classList.remove('wide');
  if (!runs.length) {
    if (!PUBLIC) {
      fill(body, h('p', { class: 'sub', text: 'Reading what each dungeon holds for you…' }));
      return;
    }
    emptyNote(body, 'No dungeon runs yet.', 'Polestar records each instance run (time and XP); the first one shows up here once the shipper sends it.');
    return;
  }
  fill(body,
    PUBLIC ? null : h('p', { class: 'sub', text: 'Reading what each dungeon holds for you…' }),
    h('ul', { class: 'list' }, runs.slice(0, 20).map((r) => h('li', {},
      h('div', {}, r.name, r.level ? h('span', { class: 'tag', text: `level ${r.level}` }) : null),
      h('div', { class: 'r sub', text: day(r.t) })))));
}

// It reads the planner's inputs (every event of the character): once on
// load, then at most every ten minutes, not on each "Show" of new data.
const DUNGEONS_EVERY_MS = 10 * 60 * 1000;
async function loadDungeons(name) {
  if (state.dungeons && state.dungeons.char === name && Date.now() - state.dungeonsAt < DUNGEONS_EVERY_MS) return;
  let d = null;
  try { d = await getJSON(api('dungeons', { name })); } catch (e) { d = null; }
  if (name !== state.char || !d || !d.available) return;  // the runs stay
  state.dungeons = d;
  state.dungeonsAt = Date.now();
  renderDungeonPanel(d);
}

// A link to the game database's page (wowstats/links.py builds the URLs,
// only the full view's and /zogzog/'s answers carry them): a new tab, no
// referrer, none of its scripts on this page. The public code never names
// the site (a public-view test checks it).
function whLink(url, text, cls) {
  if (!url) return h('span', { class: cls || null, text });
  return h('a', { href: url, target: '_blank', rel: 'noopener noreferrer', referrerpolicy: 'no-referrer',
    class: 'wh' + (cls ? ' ' + cls : ''), text });
}
// Next to an item (its name is the tooltip's button): a small mark.
function whMark(url, label) {
  if (!url) return null;
  return h('a', { href: url, target: '_blank', rel: 'noopener noreferrer', referrerpolicy: 'no-referrer',
    class: 'whx', title: `${label} on ${new URL(url).hostname.replace(/^www\./, '')}`, 'aria-label': `${label}, its database page` },
  s('svg', { viewBox: '0 0 24 24', 'aria-hidden': 'true', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.8,
    'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, s('path', { d: 'M14 4h6v6 M20 4l-9 9 M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5' })));
}

const WHERE = { worn: 'worn', bags: 'in your bags', bank: 'in your bank', looted: 'looted', reward: 'quest reward' };
const cap = (t) => (t ? t[0].toUpperCase() + t.slice(1) : '');
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;

function dropSource(it, dungeon, quests) {
  const best = it.drops[0];
  if (best) {
    return h('span', { class: 'src' }, best.boss ? whLink(best.url, best.boss) : 'trash',
      best.chance_pct ? ` ${best.chance_pct}%` : '',
      // A wing (Scarlet Monastery's Library), not the dungeon's own name again.
      best.wing && best.wing !== dungeon && best.wing.includes(':') ? h('span', { class: 'sub', text: `, ${best.wing.split(':').pop().trim()}` }) : null,
      it.drops.length > 1 ? h('span', { class: 'sub', text: ` +${it.drops.length - 1} more` }) : null);
  }
  // A quest's reward: which quest, and the next thing to do for it.
  const q = (quests || []).find((x) => it.quests.includes(x.quest_id));
  if (!q) return it.quests.length ? h('span', { class: 'src', text: 'quest reward' }) : null;
  const giver = q.starts[0];
  return h('span', { class: 'src' }, 'reward of ', whLink(q.url, q.name),
    q.state === 'open' ? (q.complete ? ': complete, hand it in' : ': in your log')
      : q.state === 'new' && giver ? [': pick it up from ', npcs([giver])] : '');
}

function dropRow(it, dungeon, quests) {
  return h('li', { class: 'dgdrop ' + it.state },
    h('div', { class: 'dgitem' }, itemChip(it), whMark(it.url, it.name)),
    h('div', { class: 'dgmeta' },
      it.state === 'missing'
        ? h('span', { class: 'gain', title: `+${(it.gain ?? 0).toFixed(1)} points of Polestar’s score over what you wear` },
          h('b', { text: `+${(it.gain ?? 0).toFixed(1)}` }), it.fills_empty ? h('small', { text: 'empty slot' }) : null)
        : h('span', { class: 'own', text: ownText(it) || 'yours' }),
      h('span', { class: 'pill', text: (it.slot_name || '').toLowerCase() + (it.now ? '' : `, level ${it.level}`) }),
      dropSource(it, dungeon, quests)));
}

// An item he has: where, and when it beats what he wears, the step to take.
function ownText(x) {
  const up = x.gain > 0 && HELD[x.where];
  return up ? `${HELD[x.where].toLowerCase()}, +${x.gain.toFixed(1)}${x.now ? '' : ` at level ${x.level}`}` : WHERE[x.where] || '';
}

// A quest's reward is listed under the quests' rewards, not as a drop.
const questOnly = (x) => !x.drops.length && x.quests.length;
function dungeonDrops(b, level, inv) {
  const missing = b.drops.filter((x) => x.state === 'missing' && !questOnly(x));
  const mine = b.drops.filter((x) => x.state === 'obtained');
  const shown = missing.slice(0, b.drops_shown || 3);
  const rest = missing.slice(shown.length);
  if (!b.drops.length || (!missing.length && !mine.length)) {
    return (b.quest_rewards || []).length ? null : h('p', { class: 'sub', text: 'Nothing in its loot beats what you wear.' });
  }
  return h('div', { class: 'dgsec' },
    h('h4', { text: 'Drops for you' }),
    shown.length ? h('ul', { class: 'dgdrops' }, shown.map((x) => dropRow(x, b.name, b.quests))) : h('p', { class: 'sub', text: 'Nothing missing: you have every upgrade it drops.' }),
    rest.length ? h('details', { class: 'dgmore' }, h('summary', { text: `${rest.length} more` }),
      h('ul', { class: 'dgdrops' }, rest.map((x) => dropRow(x, b.name, b.quests)))) : null,
    mine.length ? h('div', { class: 'dgmine' }, h('span', { class: 'sub', text: 'Already yours:' }),
      mine.map((x) => h('span', { class: 'dgown' }, itemChip(x), h('span', { class: 'sub', text: ownText(x) })))) : null,
    mine.some((x) => x.where === 'looted') && !inv
      ? h('p', { class: 'sub note', text: 'Bags and bank weren’t reported, so “looted” may mean sold since.' }) : null);
}

// The upgrades its quests pay (wowstats/plan/dungeonview.py quest_rewards):
// the item and its gain, the quest and who gives it; three in sight.
function rewardRow(r) {
  const q = r.quest;
  const giver = (q.starts || [])[0];
  return h('li', { class: 'dgdrop missing' },
    h('div', { class: 'dgitem' }, itemChip(r), whMark(r.url, r.name)),
    h('div', { class: 'dgmeta' },
      h('span', { class: 'gain', title: `+${(r.gain ?? 0).toFixed(1)} points of Polestar’s score over what you wear` },
        h('b', { text: `+${(r.gain ?? 0).toFixed(1)}` }), r.fills_empty ? h('small', { text: 'empty slot' }) : null),
      h('span', { class: 'pill', text: (r.slot_name || '').toLowerCase() + (r.now ? '' : `, level ${r.level}`) }),
      h('span', { class: 'src' }, 'from ', whLink(q.url, q.name || `quest ${q.quest_id}`),
        q.state === 'open' ? (q.complete ? ': complete, hand it in' : ': in your log')
          : giver ? [': pick it up from ', npcs([giver])] : '',
        r.of > 1 ? h('span', { class: 'sub', text: ` (the best of ${r.of} for you)` }) : null)));
}
function dungeonRewards(b) {
  const rw = b.quest_rewards || [];
  if (!rw.length) return null;
  const shown = rw.slice(0, b.drops_shown || 3);
  const rest = rw.slice(shown.length);
  return h('div', { class: 'dgsec' },
    h('h4', { text: 'Quest rewards you could use' }),
    h('ul', { class: 'dgdrops' }, shown.map(rewardRow)),
    rest.length ? h('details', { class: 'dgmore' }, h('summary', { text: `${rest.length} more` }),
      h('ul', { class: 'dgdrops' }, rest.map(rewardRow))) : null);
}

// Where someone stands, from the sources (wowstats/plan/dungeonview.py
// place()): "Ashenvale 14.5, 14.2", "inside Blackfathom Deeps", or nothing.
function at(p) {
  if (!p) return null;
  if (p.inside) return `inside ${p.inside}`;
  if (!p.zone) return null;
  return `${p.zone} ${p.x}, ${p.y}` + (p.spawns > 1 ? ` (one of ${p.spawns} spots)` : '');
}

function npcs(list) {
  // A quest an item starts: the item, linked; someone: name, link, place.
  return list.flatMap((w, i) => {
    const where = at(w);
    return [i ? ', or ' : '', w.kind === 'item' ? h('span', {}, 'the item ', whLink(w.url, w.name)) : whLink(w.url, w.name),
      where ? h('span', { class: 'at', text: ` in ${where}` }) : null];
  });
}

function questRefs(list) {
  return list.flatMap((q, i) => [i ? (q.any ? ' or ' : ', ') : '', whLink(q.url, q.name || `quest ${q.quest_id}`),
    q.state === 'open' ? ' (in your log)' : '']);
}

// How to get the prerequisite, in one line: who gives it, or the item that
// starts it and what drops that; without data the quest link above stands.
function needHow(needs) {
  const n = needs.find((x) => x.state === 'new' && ((x.starts || []).length || (x.drops || []).length));
  if (!n) return null;
  const item = (n.starts || []).find((w) => w.kind === 'item');
  if (item && (n.drops || []).length) return h('span', {}, ': ', whLink(item.url, item.name), ' drops from ', npcs(n.drops.slice(0, 2)));
  if ((n.starts || []).length) return h('span', {}, ': from ', npcs(n.starts.slice(0, 2)));
  return null;
}

// What stops a quest now: his level, a quest to hand in first.
function questBlock(q, level) {
  const why = [];
  if (q.state === 'new' && q.min_level > level) why.push(`needs level ${q.min_level}`);
  if (q.needs && q.needs.length) {
    why.push(h('span', {}, 'needs ', questRefs(q.needs), ' first', needHow(q.needs)));
  }
  if (!why.length) return null;
  return h('div', { class: 'block-why' }, 'Blocked: ', why.flatMap((w, i) => [i ? '; ' : '', w]));
}

const QSTATE = { open: 'in your log', new: 'not picked up', done: 'done' };
function questRow(q, level) {
  const where = [];
  if (q.starts.length) where.push(h('span', {}, 'from ', npcs(q.starts)));
  if (q.ends.length && !(q.starts.length === 1 && q.ends.length === 1 && q.starts[0].id === q.ends[0].id)) {
    where.push(h('span', {}, q.starts.length ? ', back to ' : 'hand in to ', npcs(q.ends)));
  }
  const label = q.state === 'open' && q.complete ? 'complete' : QSTATE[q.state];
  return h('li', { class: 'dgq ' + q.state },
    h('div', { class: 'dgqhead' },
      h('span', { class: 'qs', text: label }),
      whLink(q.url, q.name || `Unknown quest (${q.quest_id})`, 'qn'),
      h('span', { class: 'sub', text: [q.level ? `level ${q.level}` : null,
        q.xp_now ? `${fmtK(q.xp_now)} XP` : null].filter(Boolean).join(', ') })),
    where.length ? h('div', { class: 'sub where' }, where) : null,
    q.state === 'done' ? null : questBlock(q, level),
    q.route && q.route !== 'route' ? h('div', { class: 'sub note', text: cap(q.route.replace(`${q.name}: `, '')) }) : null);
}

function dungeonQuests(b, level) {
  const todo = b.quests.filter((q) => q.state !== 'done');
  const done = b.quests.filter((q) => q.state === 'done');
  if (!b.quests.length) return null;
  // Three in sight, the rest folded (open ones lead: dungeonview's order).
  const shown = b.state === 'soon' ? todo : todo.slice(0, 3);
  const more = todo.slice(shown.length);
  const list = todo.length ? [h('ul', { class: 'dgqs' }, shown.map((q) => questRow(q, level))),
    more.length ? h('details', { class: 'dgmore' }, h('summary', { text: `${more.length} more` }),
      h('ul', { class: 'dgqs' }, more.map((q) => questRow(q, level)))) : null] : null;
  // A dungeon that hasn't opened yet: its quests folded, for when it does.
  if (b.state === 'soon') {
    return h('div', { class: 'dgsec' }, h('details', { class: 'dgmore' },
      h('summary', { text: `${plural(b.quests.length, 'quest')} there` }), list));
  }
  return h('div', { class: 'dgsec' },
    h('h4', { text: 'Quests' }),
    list,
    done.length ? h('p', { class: 'dgdone' }, h('span', { class: 'sub', text: `Done (${done.length}): ` }),
      done.flatMap((q, i) => [i ? ', ' : '', whLink(q.url, q.name || `quest ${q.quest_id}`)])) : null);
}

function fact(label, value, sub) {
  return h('div', {}, h('dt', { text: label }), h('dd', {}, value, sub ? h('span', { class: 'sub', text: sub }) : null));
}

function dungeonTrip(b) {
  const t = b.trip || {};
  const facts = [];
  // Where the trip's time goes, from his runs there.
  if (t.measured) {
    facts.push(fact('Your trips there', dur(t.total_s), `inside ${dur(t.inside_s)}, travel ${dur(t.travel_s)}, quests ${dur(t.quests_s)}`));
  }
  facts.push(fact('Gear a clear', b.missing_now ? `+${b.expected_gain_per_clear.toFixed(1)}` : 'none',
    b.missing_now ? `${b.chance_one_per_clear_pct}% one of ${plural(b.missing_now, 'upgrade')} drops` : 'nothing missing at your level'));
  if (t.measured) {
    facts.push(fact('Loot a run', coins(t.vendor_copper),
      [t.ah_copper ? `${Math.round(t.ah_copper / 100) / 100}g at your AH prices` : null, t.quest_money_copper ? 'plus quest money' : null].filter(Boolean).join(', ')));
  }
  return h('div', { class: 'dgsec' },
    h('h4', { text: 'What a trip is worth' }),
    h('dl', { class: 'dgfacts' }, facts),
  );
}

function dungeonHistory(b) {
  if (!b.history.length) return null;
  return h('details', { class: 'dghist' }, h('summary', { text: `Your runs (${b.history.length})` }),
    h('ul', { class: 'list' }, b.history.map((r) => h('li', {},
      h('div', {}, day(r.t), r.level ? h('span', { class: 'tag', text: `level ${r.level}` }) : null),
      h('div', { class: 'r sub', text: [`${dur(r.inside_s)} inside`, r.xp_kills ? `${fmtK(r.xp_kills)} XP kills` : null,
        r.xp_quests ? `${fmtK(r.xp_quests)} quests` : null].filter(Boolean).join(', ') })))));
}

// Wowhead's guide for the dungeon, or one per wing (Scarlet Monastery).
function dungeonGuides(b) {
  const gs = b.guides || [];
  if (!gs.length) return null;
  if (gs.length === 1) return h('span', { class: 'sub dgguide' }, whLink(gs[0].url, 'Dungeon guide'));
  return h('span', { class: 'sub dgguide' }, 'Guides: ',
    gs.flatMap((g, i) => [i ? ', ' : '', whLink(g.url, g.name)]));
}

function dungeonBlock(b, d) {
  // "opens at N" is the verdict itself for one he can't enter yet.
  // Under Polestar's floor it waits (next: reach a level): not "at your level".
  const waits = (b.next || {}).do === 'level' || !!(b.vs_guide && b.vs_guide.under_floor);
  const tag = b.state === 'run' ? `ran it ${b.history.length}×` : b.state === 'now' && !waits ? 'at your level' : null;
  return h('article', { class: `dg v-${b.value}` },
    h('header', { class: 'dghead' },
      h('h3', {}, whLink(b.url, b.name)),
      h('span', { class: 'sub', text: `levels ${b.min}-${b.max}` }),
      dungeonGuides(b),
      tag ? h('span', { class: 'pill', text: tag }) : null),
    h('p', { class: 'dgverdict' }, h('strong', { text: cap(b.verdict) }), b.why ? `: ${b.why}` : ''),
    b.vs_guide && b.vs_guide.text ? h('p', { class: 'dgvs' }, vsGuide(b.vs_guide, false)) : null,
    b.gain && b.gain.summary ? h('p', { class: 'dggain' }, gainText(b.gain)) : null,
    dungeonNext(b),
    dungeonWayIn(b),
    dungeonQuests(b, d.level),
    dungeonRewards(b),
    dungeonDrops(b, d.level, d.inventory_known),
    b.state === 'soon' ? null : dungeonTrip(b),
    dungeonHistory(b));
}

// The one next step (dungeonview.next_step), with its place and links.
function dungeonNext(b) {
  const n = b.next || {};
  const q = n.quest_id ? b.quests.find((x) => x.quest_id === n.quest_id) : null;
  const e = b.entrance;
  const goIn = (lead) => (e ? [lead, h('b', { text: placeText(e) }), e.flight ? ` (flight path ${e.flight.name})` : '']
    : [lead.replace(/ at $/, '')]);
  const from = (x) => (x.starts.length ? [' from ', npcs(x.starts)] : '');
  let body = null;
  if (n.do === 'handin' && q) body = ['Hand in ', whLink(q.url, q.name), q.ends.length ? [' to ', npcs(q.ends)] : ''];
  else if (n.do === 'pickup' && q) body = ['Pick up ', whLink(q.url, q.name), from(q), ', then ', goIn('go in at ')];
  else if (n.do === 'level') {
    const m = n.meanwhile ? b.quests.find((x) => x.quest_id === n.meanwhile) : null;
    body = [`Reach level ${n.level} (${plural(n.to_go, 'level')} to go)`,
      m ? ['; meanwhile pick up ', whLink(m.url, m.name), from(m)] : ''];
  } else if (n.do === 'go') body = goIn('Go in at ');
  else if (n.do === 'none') body = ['Nothing to do here: skip it'];
  if (!body) return null;
  return h('p', { class: 'dgnext' }, h('span', { class: 'lbl', text: 'Next' }), h('span', {}, body, '.'));
}

// A place as "Zone x, y", or the zone alone when no source gives the point.
function placeText(e) { return e.x != null ? `${e.zone} ${e.x}, ${e.y}` : e.zone; }

// How to get in: the entrance, the flight path of the character's side
// nearest to it, and the keys. The entrance's source stays in the data.
function dungeonWayIn(b) {
  const e = b.entrance;
  const keys = b.keys || [];
  if (!e && !keys.length) return null;
  return h('div', { class: 'dgway sub' },
    e ? h('div', {}, 'Entrance: ', whLink(b.zone_url || b.url, placeText(e)),
      e.flight ? `; nearest flight path ${e.flight.name}` + (e.flight.x != null ? ` (${e.flight.x}, ${e.flight.y})` : '')
        : '; no flight path of your side on that map',
      e.prep ? `. ${e.prep}` : '') : null,
    keys.map((k) => h('div', {}, cap(k.kind) + ': ', whLink(k.url, k.name),
      k.have ? ', you have it' : k.quests.length ? [', from ', questRefs(k.quests)] : '',
      k.note ? h('span', { class: 'note', text: ` ${k.note}` }) : null)));
}

function renderDungeonPanel(d) {
  const box = $('#dungeons');
  const body = $('#dungeons .body');
  box.classList.add('wide');
  if (!d.dungeons.length) {
    fill(body, h('p', { class: 'empty', text: 'No dungeon in reach yet.' }));
    return;
  }
  fill(body,
    h('div', { class: 'dgs' }, d.dungeons.map((b) => dungeonBlock(b, d))));
}

// ------------------------------------------------------------ deaths

function renderDeaths(d) {
  const body = $('#deaths .body');
  const de = d.deaths;
  if (!de.total) {
    const recorder = d.zones.length || d.steps.total || d.flights.count;
    emptyNote(body, 'No deaths recorded.', recorder ? '' : RECORDER_ON);
    return;
  }
  const levels = Object.keys(de.by_level).length;
  const worst = Object.entries(de.by_level).sort((a, b) => b[1] - a[1])[0];
  fill(body, 
    h('dl', { class: 'sum' },
      h('div', {}, h('dt', { text: 'Deaths' }), h('dd', { text: de.total })),
      worst ? h('div', {}, h('dt', { text: 'Worst level' }), h('dd', { text: `${worst[0]} (${worst[1]})` })) : null,
      levels ? h('div', {}, h('dt', { text: 'Per level' }), h('dd', { text: (de.total / Math.max(1, d.levels.length)).toFixed(1) })) : null),
    barRows(de.by_zone.slice(0, 8), { name: (r) => r.zone, value: (r) => r.n, cls: 'death' }),
    de.recent ? h('h3', { text: 'Most recent' }) : null,
    de.recent ? h('ul', { class: 'list' }, de.recent.slice(0, 6).map((r) =>
      h('li', {}, h('div', {}, r.zone || 'Somewhere', h('div', { class: 'sub', text: r.level ? `level ${r.level}` : '' })),
        h('div', { class: 'r sub', text: when(r.t) })))) : null);
}

// ------------------------------------------------------------ analysis

// The play analysis (wowstats/analysis): the live full view gets every
// finding with its SQL and sources; /zogzog/ gets the shareable lines only
// (no numbers, times, dates or names: verify.shareable) and the time per
// level. The public site has neither the section nor the data.
async function loadAnalysis(name) {
  const box = $('#analysis');
  if (!box) return;
  let a = null;
  a = await getAnalysis(name);
  if (name !== state.char) return;
  const link = document.querySelector('.sections a[href="#analysis"]');
  const ok = Boolean(a && a.available && (!a.char || a.char === name));
  box.hidden = !ok;
  if (link) link.hidden = !ok;
  if (ok) renderAnalysis(a);
}

function budgetTable(rows) {
  const cols = [['online_s', 'Online'], ['planned_s', 'Route plan'], ['instance_s', 'Dungeons'], ['taxi_s', 'Flights'],
    ['dead_s', 'Dead'], ['afk_s', 'AFK']];
  const have = cols.filter(([k]) => rows.some((r) => r[k]));
  return h('div', { class: 'tablewrap' }, h('table', { class: 'budget' },
    h('thead', {}, h('tr', {}, h('th', { text: 'Level' }), have.map(([, t]) => h('th', { text: t })))),
    h('tbody', {}, rows.map((r) => h('tr', {}, h('td', { text: r.level }),
      have.map(([k]) => h('td', { text: r[k] ? dur(r[k]) : '' })))))));
}

function renderAnalysis(a) {
  const body = $('#analysis .body');
  const kids = [];
  // Owner, 2026-11-18: everything shown is actionable. Nudges and the
  // findings' actions come first (what, where, what blocks it, the next
  // step); findings with nothing to act on are observations, apart.
  const steps = (x) => [x.where ? ['Where', x.where] : null, x.blocked_by ? ['Blocked by', x.blocked_by] : null,
    x.next_step ? ['Next step', x.next_step] : null].filter(Boolean);
  const stepList = (x) => {
    const s = steps(x);
    return s.length ? h('dl', { class: 'steps' }, s.flatMap(([k, v]) => [h('dt', { text: k }), h('dd', { text: v })])) : null;
  };
  const nudgeList = (ns) => h('ul', { class: 'nudges' }, ns.map((n) => h('li', {},
    n.verdict === 'at_level' && n.at_level ? h('span', { class: 'tag', text: `from level ${n.at_level}` }) : null, n.text,
    stepList(n))));
  if (!Array.isArray(a.findings)) {
    // /zogzog/: the nudges the analysis screened for it, whole (what,
    // where, what blocks it, the next step), and the time per level.
    // Descriptions (pattern lines) never reach it.
    if ((a.nudges || []).length) kids.push(h('h3', { text: 'What to do' }), nudgeList(a.nudges));
    if ((a.time_budget || []).length) kids.push(h('h3', { text: 'Time per level' }), budgetTable(a.time_budget));
    if (!kids.length) kids.push(h('p', { class: 'empty', text: 'Nothing to act on from the play analysis yet.' }));
    fill(body, kids);
    return;
  }
  const run = a.last_analysis;
  if (!run) kids.push(h('p', { class: 'empty', text: 'No analysis yet: Ask Claude or ChatGPT above.' }));
  if ((a.nudges || []).length) kids.push(h('h3', { text: 'What to do' }), nudgeList(a.nudges));
  const finding = (f) => h('li', {},
    h('div', { class: 'ftitle' }, f.title, f.confirmations ? h('span', { class: 'tag', text: `seen ${f.confirmations + 1}×` }) : null,
      f.doubtful ? h('span', { class: 'tag', text: 'doubtful: another finding disagrees' }) : null),
    f.action ? h('p', { class: 'fdo', text: f.action.do }) : null,
    f.action ? stepList(f.action) : null,
    h('p', { class: f.action ? 'sub' : '', text: f.text }));
  const acts = (a.findings || []).filter((f) => f.action);
  const obs = (a.findings || []).filter((f) => !f.action);
  if (acts.length) kids.push(h('h3', { text: 'Findings to act on' }), h('ol', { class: 'findings' }, acts.map(finding)));
  // Descriptions, folded: the findings with nothing to act on and the
  // analysis' pattern lines. Never on the shared page.
  const pats = (a.patterns || []).map((p) => (typeof p === 'string' ? p : p.text)).filter(Boolean);
  const nobs = obs.length + pats.length;
  if (nobs) kids.push(h('details', { class: 'observations' },
    h('summary', { text: `${nobs} observation${nobs > 1 ? 's' : ''}: what the data shows, nothing to do about it (this view only)` }),
    obs.length ? h('ol', { class: 'findings' }, obs.map(finding)) : null,
    pats.length ? h('ul', { class: 'patterns' }, pats.map((t) => h('li', { text: t }))) : null));
  if ((a.time_budget || []).length) kids.push(h('h3', { text: 'Time per level' }), budgetTable(a.time_budget));
  fill(body, kids);
}

// ------------------------------------------------------------ loot

// What the character looted, per dungeon run and per level, newest first
// (/zogzog/ and the full view). Green and better items stand out; the rest
// are summed in one line.
async function loadLoot(name) {
  const box = $('#loot');
  if (!box) return;
  try {
    const l = await getJSON(api('loot', { name }));
    if (name === state.char) renderLoot(l);
  } catch (e) {
    box.hidden = false;
    fill($('#loot .body'), h('p', { class: 'err', text: `Couldn’t read the loot: ${e.message}.` }));
  }
}

function lootItem(i) {
  const q = i.quality ?? null;
  const extra = [i.zone ? `Looted in ${i.zone}` : null, i.t ? when(i.t) : null].filter(Boolean);
  return h('li', { class: 'litem' + (q !== null ? ` q${q}` : '') },
    itemChip({ ...i, extra: extra.length ? [extra.join(', ')] : null }),
    i.count > 1 ? h('span', { class: 'sub', text: ` ×${i.count}` }) : null);
}

function renderLoot(l) {
  const box = $('#loot');
  const body = $('#loot .body');
  box.hidden = false;
  const groups = l.groups || [];
  if (!groups.length) {
    emptyNote(body, 'No loot recorded.', 'Polestar_Recorder writes a record for each corpse or chest you loot; they show up after the next shipped session.');
    return;
  }
  fill(body,
    h('p', { class: 'lede', text: `${fmtNum(l.total)} items looted, ${l.notable} of them green or better.` }),
    h('ol', { class: 'loot' }, groups.slice(0, 40).map((g, gi) => {
      const notable = g.items.filter((i) => (i.quality ?? 0) >= 2);
      const rest = g.items.filter((i) => (i.quality ?? 0) < 2);
      const title = g.kind === 'dungeon' ? g.name : `Level ${g.level ?? '?'}`;
      const det = h('details', {},
        h('summary', {}, h('span', { class: 'lgroup', text: title }),
          g.kind === 'dungeon' && g.level ? h('span', { class: 'tag', text: `level ${g.level}` }) : null,
          h('span', { class: 'sub', text: ` ${day(g.t)}, ${g.items.length} item${g.items.length === 1 ? '' : 's'}` }),
          notable.length ? h('span', { class: 'lnote', text: `${notable.length} notable` }) : null),
        notable.length ? h('ul', { class: 'lnotable' }, notable.map(lootItem)) : null,
        rest.length ? h('div', { class: 'sub lrest' }, h('span', { text: 'Also: ' }),
          h('ul', { class: 'chips' }, rest.slice(0, 30).map((i) => h('li', {}, itemChip(i),
            i.count > 1 ? h('span', { class: 'sub', text: ` ×${i.count}` }) : null))),
          rest.length > 30 ? h('span', { text: `and ${rest.length - 30} more` }) : null) : null);
      if (gi === 0) det.open = true;
      return h('li', {}, det);
    })));
}

// ------------------------------------------------------------ people

async function loadPeople(name) {
  const el = $('#people-runs');
  if (!el) return;
  try {
    const p = await getJSON(api('people', { name }));
    const runs = p.runs || [];
    fill(el, h('h3', { text: 'Dungeon groups' }),
      runs.length ? h('ul', { class: 'runs' }, runs.map((r) => h('li', {},
        h('b', { text: r.name }), ` at level ${r.level}, ${day(r.t)}: ${dur(r.seconds)}`,
        r.party_size ? `, a group of ${r.party_size}` : '', r.bosses ? `, ${r.bosses} bosses` : '',
        r.wipes ? `, ${r.wipes} wipes` : '', r.deaths ? `, ${r.deaths} deaths` : ''))) : h('p', { class: 'sub', text: 'No dungeon runs yet.' }),
      h('p', { class: 'sub', text: p.roster_note }));
  } catch (e) {
    fill(el, h('p', { class: 'err', text: `Couldn’t read the dungeon groups: ${e.message}.` }));
  }
}

function renderPeople(d) {
  const body = $('#people .body');
  const p = d.players;
  if (!body || !p) return;
  if (!p.total) {
    emptyNote(body, 'Nobody yet.', RECORDER_ON);
    return;
  }
  // The strip: the last 14 days that end on the latest day anyone was seen.
  const last = p.last_day ? new Date(p.last_day + 'T00:00:00Z') : new Date();
  const strip = [];
  for (let i = 13; i >= 0; i--) strip.push(new Date(last.getTime() - i * 86400000).toISOString().slice(0, 10));
  const lede = p.recurring.length
    ? `${p.total} players crossed your path. ${p.recurring.length} of them more than once; the rest only for a day.`
    : `${p.total} players crossed your path, none of them twice yet.`;
  const table = h('table', { class: 'people' },
    h('thead', {}, h('tr', {},
      h('th', { text: 'Player' }),
      h('th', { text: 'Last 14 days' }),
      h('th', { class: 'num', text: 'Grouped' }),
      h('th', { class: 'hide-s', text: 'Last seen' }),
      h('th', { class: 'hide-s', text: 'Zones' }))),
    h('tbody', {}, p.recurring.map((r) => {
      const seenDays = new Set(r.day_list);
      const grpDays = new Set(r.group_days);
      const who = [r.level ? `level ${r.level}` : null, r.race, r.class].filter(Boolean).join(' ');
      const z = r.zones || [];
      return h('tr', {},
        h('td', {}, h('div', { class: 'pname', text: r.player.replace(/-.*$/, '') }),
          h('div', { class: 'sub', text: who || r.player })),
        h('td', {}, h('span', { class: 'days', role: 'img', 'aria-label': `seen on ${r.days} days, grouped on ${r.group_days.length}` },
          strip.map((dd) => h('i', { class: grpDays.has(dd) ? 'grp' : seenDays.has(dd) ? 'on' : '', title: dd, 'aria-hidden': 'true' }))),
          h('div', { class: 'sub', text: `${r.days} day${r.days === 1 ? '' : 's'}` })),
        h('td', { class: 'num' }, r.grouped ? fmtNum(r.grouped) : '–'),
        h('td', { class: 'hide-s' }, h('span', { text: ago(r.last_seen) }), h('div', { class: 'sub', text: when(r.last_seen) })),
        h('td', { class: 'hide-s zones', text: z.slice(0, 3).join(', ') + (z.length > 3 ? `, ${z.length - 3} more` : '') }));
    })));
  fill(body, h('p', { class: 'lede', text: lede }),
    h('p', { class: 'legend' }, h('span', { class: 'dot seen', text: 'seen that day' }), h('span', { class: 'dot grp', text: 'grouped or in a dungeon together' })),
    p.recurring.length ? table : h('p', { class: 'empty', text: 'Once someone shows up on two different days, or groups with you twice, they get a row here.' }));
}

// ------------------------------------------------------------ flights

function renderFlights(d) {
  const body = $('#flights .body');
  const f = d.flights;
  if (!f.count) {
    emptyNote(body, 'No flights yet.', RECORDER_ON);
    return;
  }
  const longest = f.longest ? f.longest.secs : f.longest_s;
  fill(body,
    h('dl', { class: 'sum' },
      h('div', {}, h('dt', { text: 'Flights' }), h('dd', { text: f.count })),
      h('div', {}, h('dt', { text: 'In the air' }), h('dd', { text: dur(f.total_s) })),
      longest ? h('div', {}, h('dt', { text: 'Longest' }), h('dd', { text: dur(longest) })) : null),
    f.recent ? h('ul', { class: 'list' }, f.recent.slice(0, 8).map((r) =>
      h('li', {},
        h('div', {}, `${r.from_zone || '?'} to ${r.to_zone || 'somewhere (no landing seen)'}`,
          h('div', { class: 'sub', text: when(r.t) })),
        h('div', { class: 'r', text: dur(r.secs) })))) : null);
}

// ------------------------------------------------------------ prices (full view)

function renderPrices(p, err) {
  const el = $('#prices');
  if (!el) return;
  el.hidden = false;
  if (err) { fill(el, h('p', { class: 'note', text: `Your Auctionator scans are unavailable: ${err}.` })); return; }
  if (!p || !p.total) {
    fill(el, h('p', { class: 'note', text: 'No prices yet. Scan the auction house with Auctionator, then /reload.' }));
    return;
  }
  const list = h('ul', { class: 'ahlist' });
  const search = h('input', { type: 'search', class: 'psearch', placeholder: 'Find an item', 'aria-label': 'Find an item' });
  const show = () => {
    const q = search.value.trim().toLowerCase();
    const items = p.items.filter((i) => !q || (i.name || i.item_key).toLowerCase().includes(q));
    fill(list, items.slice(0, 60).map((i) => h('li', {
      title: `${i.scans} scan day${i.scans === 1 ? '' : 's'}, lowest ${fmtNum(i.min)} to ${fmtNum(i.max)} copper`,
    }, h('span', { text: i.name || `item ${i.item_key}` }),
      h('span', { class: 'r' }, i.series.length > 1 ? sparkline(i.series.map((x, k) => [k, x.lo]), 56, 16) : null, ' ', coins(i.price),
        h('small', { text: ` ${day(i.day + 'T12:00:00')}` + (i.change ? `, ${i.change > 0 ? '+' : '−'}${fmtNum(Math.abs(i.change))}c` : '') })))));
    if (items.length > 60) list.append(h('li', { class: 'more', text: `${items.length - 60} more: type to narrow it down.` }));
  };
  search.addEventListener('input', show);
  fill(el, h('details', { class: 'm ahbox' },
    h('summary', {}, `Your Auctionator scans: ${fmtNum(p.total)} items, last scan ${day(p.last_scan + 'T12:00:00')}`),
    search, list));
  show();
}

// ------------------------------------------------------------ journal

function renderJournal(j, err) {
  const list = $('#journal-list');
  const lede = $('#journal-lede');
  if (err) { fill(list); lede.textContent = `Couldn’t load the journal: ${err}.`; return; }
  const ss = j.sessions || [];
  if (!ss.length) {
    fill(list);
    lede.textContent = 'No sessions yet. ' + RECORDER_ON;
    return;
  }
  const total = ss.reduce((a, b) => a + b.played_s, 0);
  lede.textContent = `${ss.length} session${ss.length === 1 ? '' : 's'}, ${dur(total)} in all. A session ends when the recorder goes quiet for ${Math.round(j.gap_s / 60)} minutes or you log in again.`;
  fill(list, ss.map((s) => {
    const lv = s.level_start !== null && s.level_end !== null && s.level_end !== s.level_start
      ? `level ${s.level_start} to ${s.level_end}` : (s.level_end !== null ? `level ${s.level_end}` : '');
    const facts = [
      h('div', {}, h('dt', { text: 'Played' }), h('dd', { text: dur(s.played_s) })),
      h('div', {}, h('dt', { text: 'Levels' }), h('dd', { text: s.levels_gained ? `+${s.levels_gained}` : '0' })),
      h('div', {}, h('dt', { text: 'XP' }), h('dd', { text: fmtK(s.xp) })),
      h('div', {}, h('dt', { text: 'Quests' }), h('dd', { text: s.quests.length })),
      h('div', {}, h('dt', { text: 'Deaths' }), h('dd', { text: s.deaths.total })),
    ];
    // No logout snapshot around this day: no Gold line rather than a "?".
    if (s.gold_delta_copper !== undefined && s.gold_delta_copper !== null) {
      const g = s.gold_delta_copper;
      facts.push(h('div', { title: 'From the snapshots the game saves at logout' }, h('dt', { text: 'Gold' }),
        h('dd', {}, [g < 0 ? '−' : '+', coins(Math.abs(g))])));
    }
    const parts = [];
    if (s.zones.length) {
      parts.push(h('h3', { text: 'Zones' }), barRows(s.zones.slice(0, 6), {
        name: (r) => r.zone, value: (r) => r.secs, valueSub: (r) => [dur(r.secs), ''],
      }));
    }
    if (s.quests.length) {
      // The name (wowstats/forever.py fills what the shipper didn't), then
      // the quest's level and where it was handed in, muted. Chain parts
      // share a title ("The Missing Diplomat" 1 to 17): their part when
      // forever-db orders them, else the id, so two lines never read the same.
      const seen = {};
      for (const q of s.quests) if (q.name) seen[q.name] = (seen[q.name] || 0) + 1;
      const tag = (q) => (Array.isArray(q.part) && q.part.length === 2 ? `${q.part[0]} of ${q.part[1]}`
        : q.name && seen[q.name] > 1 ? `#${q.quest_id}` : '');
      parts.push(h('h3', { text: 'Quests handed in' }), h('ul', { class: 'qlist' },
        s.quests.map((q) => h('li', { title: when(q.t) },
          h('span', { class: 'qn' + (q.name ? '' : ' unknown') },
            q.name || `Unknown quest (${q.quest_id})`,
            tag(q) ? h('span', { class: 'qp', text: ` · ${tag(q)}` }) : null),
          (q.level || q.zone) ? h('span', { class: 'qm', text: [q.level ? `level ${q.level}` : null, q.zone].filter(Boolean).join(' · ') }) : null))));
    }
    if (s.dungeons.length) {
      parts.push(h('h3', { text: 'Dungeons' }), h('ul', { class: 'list' }, s.dungeons.map((r) =>
        h('li', {}, h('div', {}, r.name || 'Dungeon', r.level ? h('span', { class: 'tag', text: `level ${r.level}` }) : null),
          h('div', { class: 'r', text: dur(r.seconds) })))));
    }
    if (s.deaths.total) {
      parts.push(h('h3', { text: 'Deaths' }), h('p', { class: 'sub', text: s.deaths.by_zone.map((z) => `${z.zone} ${z.n}`).join(', ') }));
    }
    if (s.players && s.players.length) {
      parts.push(h('h3', { text: 'Players around' }), h('p', { class: 'sub' },
        s.players.slice(0, 20).flatMap((p, i) => [i ? ', ' : '', h(p.grouped ? 'strong' : 'span', { text: p.player.replace(/-.*$/, '') })]),
        s.players.length > 20 ? ` and ${s.players.length - 20} more` : ''));
    }
    const start = toDate(s.start);
    return h('li', { class: 'jentry' },
      h('details', {},
        h('summary', {},
          h('span', { class: 'jday', text: dayFmt.format(start) }),
          h('span', { class: 'jtime', text: `${timeFmt.format(start)} to ${timeFmt.format(toDate(s.end))}` }),
          h('span', { class: 'jlv', text: lv }),
          h('span', { class: 'jzone', text: s.zones.length ? s.zones[0].zone : '' })),
        h('dl', { class: 'sum' }, facts),
        parts));
  }));
  const first = list.querySelector('details');
  if (first) first.open = true;
}

async function loadJournal(quiet) {
  try {
    const j = await getJSON(api('journal', { name: state.char }));
    state.journal = j;
    renderJournal(j, null);
  } catch (e) {
    if (!quiet || !state.journal) renderJournal(null, e.message);
  }
}

// The tabs this page has: the public one has Character and the level slider.
const TABS = Object.fromEntries(Object.entries({ home: '#home', overview: '#main', timeline: '#timeline', journal: '#journal' })
  .filter(([k]) => document.getElementById('tab-' + k)));
const HOME = 'home' in TABS ? 'home' : 'overview';
function setTab(tab) {
  if (!(tab in TABS)) tab = HOME;
  state.tab = tab;
  for (const [k, sel] of Object.entries(TABS)) {
    $(sel).hidden = k !== tab;
    $('#tab-' + k).setAttribute('aria-selected', String(k === tab));
  }
  const u = new URL(location.href);
  if (tab !== HOME) u.searchParams.set('tab', tab); else u.searchParams.delete('tab');
  history.replaceState(null, '', u);
  if (tab === 'journal' && state.char) loadJournal();
  if (tab === 'timeline' && state.char) loadTimeline();
  if (tab === 'home' && state.char) loadOverview();
}

// A section of the Character tab (or another tab): opened, scrolled to and
// marked for a moment. An upgrade's card (#up-<slot>) appears once the gear
// advice is in: a missing one waits for it, then falls back to #advice.
function goTo(link, tries = 0) {
  if (link === '#timeline') { setTab('timeline'); window.scrollTo({ top: 0 }); return; }
  if (link === '#journal') { setTab('journal'); window.scrollTo({ top: 0 }); return; }
  setTab('overview');
  const el = document.querySelector(link);
  if (!el) {
    if (link.startsWith('#up-')) {
      if (tries < 20) setTimeout(() => goTo(link, tries + 1), 150); else goTo('#advice');
    }
    return;
  }
  el.scrollIntoView({ block: link.startsWith('#up-') ? 'center' : 'start', behavior: reduceMotion ? 'auto' : 'smooth' });
  el.classList.remove('lit');
  void el.offsetWidth;  // restart the mark if it was already lit
  el.classList.add('lit');
  setTimeout(() => el.classList.remove('lit'), 2400);
}

// ------------------------------------------------------------ overview (home)

// One way to write a pace ratio, here and on the Character tab: 2.0×.
const fmtX = ratioText;

// A game object's name, linked to its Wowhead Forever page from an address
// the server wrote (wowstats/links.py). No Wowhead script, no referrer.
function whRef(ref, text) {
  if (!ref) return null;
  const a = whLink(ref.url, text || ref.name);
  if (ref.url) a.title = `${text || ref.name} on Wowhead (new tab)`;
  return a;
}
function itemWithLink(i) {
  return h('span', { class: 'mitem' }, itemChip(i),
    i.url ? h('a', { class: 'wh whsmall', href: i.url, target: '_blank', rel: 'noopener noreferrer', referrerpolicy: 'no-referrer',
      'aria-label': `${i.name || 'The item'} on Wowhead (new tab)`, text: 'Wowhead' }) : null);
}

// The play analysis is read by the Character tab too: one request a minute.
let analysisMemo = { name: null, at: 0, p: null };
function getAnalysis(name) {
  if (analysisMemo.name !== name || Date.now() - analysisMemo.at > 55000) {
    analysisMemo = { name, at: Date.now(), p: getJSON(api('analysis', { name })).catch(() => null) };
  }
  return analysisMemo.p;
}

async function loadOverview(quiet) {
  const name = state.char;
  try {
    const [ov, an] = await Promise.all([getJSON(api('overview', { name })), getAnalysis(name)]);
    if (name !== state.char) return;
    state.overview = ov;
    renderOverview(ov, null, an && an.available && (!an.char || an.char === name) ? an : null);
  } catch (e) {
    if (!quiet || !state.overview) renderOverview(null, e.message);
  }
}

// The headline: the pace against Polestar's route and the next level, in
// one sentence. The verdict's word carries the state; the dot repeats it.
function verdictLine(ov) {
  const hd = ov.header || {};
  const tr = ov.on_track || {};
  const n = (tr.route_levels || []).length;
  if (tr.route_ratio) {
    const v = tr.verdict.charAt(0).toUpperCase() + tr.verdict.slice(1);
    return [tr.route_ratio <= 1.5 ? 'good' : tr.route_ratio <= 3 ? 'mid' : 'bad',
      `${v}: the last ${n === 1 ? 'level' : n + ' levels'} took ${fmtX(tr.route_ratio)} Polestar’s route time`
      + (tr.eta_s && hd.level ? `, and level ${hd.level + 1} is about ${dur(tr.eta_s)} of play away.` : '.')];
  }
  if (hd.level) return ['', `Level ${hd.level}. Finish a level with Polestar on to see your pace against its route.`];
  return ['', 'Nothing recorded yet. ' + RECORDER_ON];
}

function overviewTiles(ov) {
  const hd = ov.header || {};
  const tr = ov.on_track || {};
  const g = ov.guide || {};
  const lv = tr.route_levels || [];
  const restLv = hd.rested_xp && hd.xp_max ? hd.rested_xp / hd.xp_max : null;
  // Each tile says what moves it: a number with nothing to do about it
  // isn't worth a tile.
  const tile = (label, value, sub, hint, title) => h('div', { title: title || null },
    h('dt', { text: label }), h('dd', {}, value, h('span', { class: 'sub', text: sub }),
      hint ? h('span', { class: 'hint', text: hint }) : null));
  const slow = g.over_plan_s > 0 ? `Guide steps past 3× their plan cost ${dur(g.over_plan_s)}: skip them sooner` : null;
  return h('dl', { class: 'facts ovtiles' },
    tile('Pace', tr.route_ratio ? fmtX(tr.route_ratio) : 'not yet',
      tr.route_ratio ? `Polestar’s route time, level${lv.length > 1 ? `s ${lv[0]}-${lv[lv.length - 1]}` : ` ${lv[0]}`}` : 'needs a finished level',
      tr.route_ratio ? slow : 'Finish a level to start it',
      tr.route_ratio ? `${dur(tr.played_s)} played against ${dur(tr.route_s)} planned` : null),
    tile('Next level', tr.eta_s ? 'in ' + dur(tr.eta_s) : 'not yet',
      hd.xp_per_hour ? `of play at ${fmtNum(hd.xp_per_hour)} XP an hour` : 'needs XP and a pace',
      null),
    tile('Guide', g.total ? `${Math.round((100 * g.in_order) / g.total)}%` : 'not yet',
      g.total ? `${fmtNum(g.in_order)} of ${fmtNum(g.total)} step${g.total === 1 ? '' : 's'} in order` : 'no guide steps recorded',
      g.total ? `${fmtNum(g.skipped || 0)} skipped by hand, ${fmtNum(g.jumped || 0)} passed over` : 'Follow a Polestar guide to start it'),
    tile('Rested', restLv ? `${restLv.toFixed(2)} level${restLv.toFixed(2) === '1.00' ? '' : 's'}` : 'none',
      restLv ? `${fmtNum(hd.rested_xp)} XP banked, double XP from kills` : 'nothing banked',
      'Grows while logged out in an inn or a city: log out there'));
}

// Time per level against the route's, the last finished levels: a bar for
// the played time, a dashed tick for the route's. Hover or focus a level
// for both numbers; the same rows are in a table for screen readers.
// Drawn at the width it gets (an SVG scaled down to a phone shrank its
// labels to 10 px), again when the window resizes.
let paceRedraw = null;
function drawPace(host, rows) {
  const draw = () => fill(host, paceChart(rows, Math.max(260, Math.round(host.clientWidth || 420))));
  paceRedraw = draw;
  draw();
}
function paceChart(rows, W) {
  const H = Math.round(Math.min(240, Math.max(190, W * 0.5)));
  const m = { l: 40, r: 8, t: 12, b: 26 };
  const max = Math.max(...rows.map((r) => Math.max(r.seconds, r.route_s || 0)));
  const step = [1800, 3600, 7200, 10800, 14400, 21600].find((st) => max / st <= 5) || 21600;
  const top = Math.ceil(max / step) * step;
  const y = (v) => m.t + (H - m.t - m.b) * (1 - v / top);
  const band = (W - m.l - m.r) / rows.length;
  const bw = Math.min(30, band * 0.58);
  const tip = h('div', { class: 'ctip', hidden: true, role: 'status' });
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': `Played time per level against Polestar’s route, levels ${rows[0].level} to ${rows[rows.length - 1].level}` });
  for (let v = 0; v <= top; v += step) {
    svg.append(s('line', { class: 'grid-line', x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }),
      s('text', { class: 'axis', x: m.l - 6, y: y(v) + 4, 'text-anchor': 'end' }, v ? `${v / 3600}h` : '0'));
  }
  const show = (r, cx) => {
    tip.hidden = false;
    fill(tip, h('b', { text: `Level ${r.level}` }), h('span', { text: `${dur(r.seconds)} played` }),
      r.route_s ? h('span', { class: 'sub', text: `${dur(r.route_s)} on the route, ${fmtX(r.seconds / r.route_s)}` }) : null);
    tip.style.left = `${(Math.min(Math.max(cx, 80), W - 80) / W) * 100}%`;  // kept inside the plot
  };
  rows.forEach((r, i) => {
    const cx = m.l + band * (i + 0.5);
    const yt = y(r.seconds);
    const base = y(0);
    const rr = Math.min(4, (base - yt) / 2);
    const g = s('g', { class: 'lvl', tabindex: 0, 'aria-label': `Level ${r.level}: ${dur(r.seconds)} played` + (r.route_s ? `, route ${dur(r.route_s)}` : '') },
      s('rect', { class: 'hit', x: cx - band / 2, y: m.t, width: band, height: base - m.t }),
      s('path', { class: 'bar', d: `M${cx - bw / 2} ${base}V${yt + rr}Q${cx - bw / 2} ${yt} ${cx - bw / 2 + rr} ${yt}H${cx + bw / 2 - rr}Q${cx + bw / 2} ${yt} ${cx + bw / 2} ${yt + rr}V${base}Z` }),
      r.route_s ? s('line', { class: 'route', x1: cx - bw / 2 - 5, x2: cx + bw / 2 + 5, y1: y(r.route_s), y2: y(r.route_s) }) : null,
      s('text', { class: 'axis', x: cx, y: H - 6, 'text-anchor': 'middle' }, String(r.level)));
    for (const ev of ['pointerenter', 'focus']) g.addEventListener(ev, () => show(r, cx));
    for (const ev of ['pointerleave', 'blur']) g.addEventListener(ev, () => { tip.hidden = true; });
    svg.append(g);
  });
  return h('figure', { class: 'pacechart' },
    h('div', { class: 'ckey' }, h('span', { class: 'kplayed', text: 'Played' }), h('span', { class: 'kroute', text: 'Polestar’s route' })),
    h('div', { class: 'cplot' }, svg, tip),
    h('table', { class: 'sr' }, h('caption', { text: 'Time per level' }),
      h('thead', {}, h('tr', {}, h('th', { text: 'Level' }), h('th', { text: 'Played' }), h('th', { text: 'Route' }))),
      h('tbody', {}, rows.map((r) => h('tr', {}, h('td', { text: r.level }), h('td', { text: dur(r.seconds) }), h('td', { text: r.route_s ? dur(r.route_s) : '' }))))));
}

// A move: what kind, what (named and linked), why, and the section that
// holds it. The kinds are wowstats/insights.py's; /zogzog/ gets only those
// of redact.ZOGZOG_MOVE_KINDS.
const MOVE_KIND = {
  upgrade: ['Upgrade', 'See the upgrade'], dungeon: ['Dungeon', 'See the drops'], profession: ['Profession', 'See professions'],
  riding: ['Riding', 'See the gear'],
  deaths_zone: ['Deaths', 'See deaths'], deaths_creature: ['Deaths', 'See deaths'], deaths_level_gap: ['Deaths', 'See deaths'],
  slow_steps: ['Guide', 'See guide steps'], talents: ['Talents', 'See talents'], enchant: ['Enchants', 'See the gear'],
  slow_level: ['Pace', 'See the climb'],
};
function sourceLine(src) {
  if (!src) return null;
  const z = src.zone;
  const lv = z && z.levels ? ` (levels ${z.levels[0]}-${z.levels[1]})` : '';
  const block = src.blocker ? h('span', { class: 'mblock', text: ` (${src.blocker})` }) : null;
  if (src.kind === 'quest' && src.quest) return h('span', {}, 'Quest ', whRef(src.quest), z ? [' in ', whRef(z)] : null, block);
  if (src.kind === 'drop') return h('span', {}, `Drops from ${src.boss || 'a creature'}`, z ? [' in ', whRef(z), lv] : null, block);
  if (src.kind === 'craft') return h('span', { text: `You can make it with ${src.profession || 'a profession'}` });
  if (HELD[src.kind]) return h('span', { text: HELD[src.kind] });
  return null;
}
// Who trains it, where: the trainer (Wowhead), the zone (Wowhead) and the
// map coordinates. The full view puts the ones in the character's zone first;
// /zogzog/ has no zone for the character and keeps the data's order (by
// zone), so the list never says where the character is.
function trainerLine(t, here) {
  return h('li', { class: 'trainer' }, whRef(t), ', ', whRef(t.zone),
    t.x != null ? h('span', { class: 'sub', text: ` (${t.x.toFixed(1)}, ${t.y.toFixed(1)})` }) : null,
    here ? h('span', { class: 'pill', text: 'your zone' }) : null);
}
function trainersEl(ts, nowZone) {
  const here = (t) => !!nowZone && t.zone && t.zone.name === nowZone;
  const sorted = nowZone ? [...ts].sort((a, b) => here(b) - here(a)) : ts;
  return capped(sorted.map((t) => trainerLine(t, here(t))), 3, (n) => `${n} more trainer${n === 1 ? '' : 's'}`, 'trainers');
}
function met(ok) { return ok ? 'met' : 'not yet'; }
function moveEl(mv, nowZone) {
  const [label, go] = MOVE_KIND[mv.kind] || ['Note', 'See it'];
  let what;
  let why = [];
  if (mv.kind === 'upgrade' && mv.item) {
    const it = mv.item;
    what = itemWithLink(it);
    why = [h('span', { text: `${it.slot_name || 'Gear'}, +${(it.gain || 0).toFixed(1)} points (${Math.round(it.gain_share_pct || 0)}% of your gear)` }),
      sourceLine(mv.source)];
  } else if (mv.kind === 'dungeon' && mv.zone) {
    const its = mv.items || [];
    what = h('span', {}, whRef(mv.zone), mv.zone.levels ? h('span', { class: 'pill', text: `levels ${mv.zone.levels[0]}-${mv.zone.levels[1]}` }) : null);
    why = [vsGuide(mv.vs_guide, true), gainLine(mv.gain), gainQuests(mv.gain), moveRewards(mv.quest_rewards),
      h('span', { text: `Drops ${its.length} upgrade${its.length === 1 ? '' : 's'} you could wear now` + (mv.blocker ? ` (${mv.blocker}):` : ':') }),
      h('ul', { class: 'ditems' }, its.slice(0, 3).map((it) => h('li', {}, itemWithLink(it),
        h('span', { class: 'sub', text: `${it.slot_name || 'Gear'}, +${(it.gain || 0).toFixed(1)} points (${Math.round(it.gain_share_pct || 0)}% of your gear)` })))),
      its.length > 3 ? h('span', { class: 'sub', text: `and ${its.length - 3} more in the gear advice` }) : null,
      ];
  } else if ((mv.kind === 'profession' || mv.kind === 'riding') && mv.skill) {
    const sk = mv.skill;
    const ts = sk.trainers || [];
    what = h('span', { class: 'mtitle', text: mv.title });
    const needs = [sk.need_level ? `level ${sk.need_level} (${sk.level_ok ? 'met' : 'you are ' + ((state.overview || {}).header || {}).level})` : null,
      sk.need_skill ? `${sk.name} ${sk.need_skill} (${met(sk.skill_ok)})` : null].filter(Boolean);
    // The bag, in words with units, apart from the price: "You have 3g 40s;
    // 96g 60s to go." (the server rounds the bag to silver, so they add up).
    const have = sk.money_copper != null && sk.cost_copper
      ? h('span', { text: sk.money_copper >= sk.cost_copper ? `You have ${coinText(sk.money_copper)}: enough.`
        : `You have ${coinText(sk.money_copper)}; ${coinText(sk.cost_copper - sk.money_copper)} to go.` }) : null;
    why = [ts.length ? trainersEl(ts, nowZone)
        : h('span', {}, 'Trainer location not in our data: ', sk.search_url ? whRef({ url: sk.search_url, name: 'search Wowhead' }) : null),
      needs.length ? h('span', { text: `Needs ${needs.join(' and ')}.` }) : null,
      // Where a price comes from stays in the data; a caveat that changes
      // what to bring stays, in a few words.
      sk.cost_copper ? h('span', { text: `Costs ${coinText(sk.cost_copper)}${/reputation/i.test(sk.cost_scope || '') ? ' (less with reputation)' : ''}.` }) : null,
      have];
  } else if (mv.kind === 'deaths_zone' && mv.zone) {
    what = h('span', { class: 'mtitle' }, mv.title.replace(mv.zone.name, '').trim() + ' ', whRef(mv.zone));
    why = [mv.detail ? h('span', { text: mv.detail }) : null, mv.action ? h('span', { class: 'mact', text: mv.action }) : null];
  } else if (mv.kind === 'enchant') {
    what = h('span', { class: 'mtitle', text: mv.title });
    why = [h('span', { class: 'mitems' }, (mv.items || []).map(itemWithLink))];
  } else {
    what = h('span', { class: 'mtitle', text: mv.title || mv.text });
    why = [mv.detail ? h('span', { text: mv.detail }) : (mv.title && mv.text !== mv.title ? h('span', { text: mv.text }) : null),
      mv.action ? h('span', { class: 'mact', text: mv.action }) : null];
  }
  const a = h('a', { class: 'mgo', href: mv.link, text: go });
  a.addEventListener('click', (ev) => { ev.preventDefault(); goTo(mv.link); });
  return h('li', { class: 'move k-' + mv.kind },
    h('span', { class: 'mk', text: label }),
    h('div', { class: 'mbody' }, h('div', { class: 'mwhat' }, what), why.filter(Boolean).length ? h('div', { class: 'mwhy' }, why) : null),
    a);
}

// Our dungeon against Polestar's dungeons for the level
// (wowstats/plan/vsguide.py): the server's line when they differ, with its
// measured reason; on a move, a short "on Polestar's list too" when they agree.
function vsGuide(v, agreeLine) {
  if (!v) return null;
  if (v.text) return h('span', { class: 'vsg', text: v.text });
  return agreeLine && v.agrees ? h('span', { class: 'vsg ok', text: `On Polestar’s list for level ${v.numbers.level} too.` }) : null;
}

// A dungeon move's quest rewards: item, gain, the quest that pays it.
function moveRewards(rw) {
  if (!rw || !rw.length) return null;
  return h('div', { class: 'mrewards' }, h('span', { text: 'Quest rewards you could use:' }),
    h('ul', { class: 'ditems' }, rw.slice(0, 3).map((r) => h('li', {}, itemWithLink(r),
      h('span', { class: 'sub' }, `${r.slot_name || 'Gear'}, +${(r.gain || 0).toFixed(1)} points${r.now ? '' : ` at level ${r.level}`}, from `,
        whLink(r.quest.url, r.quest.name || `quest ${r.quest.quest_id}`))))),
    rw.length > 3 ? h('span', { class: 'sub', text: `and ${rw.length - 3} more in Dungeons` }) : null);
}

// One more run of a dungeon (wowstats/plan/dungeongain.py): the line is the
// server's, word for word, so the move, the Dungeons panel and the MCP say
// the same numbers.
function gainText(g) {
  const t = cap(g.summary) + '.';
  return g.lead && t.startsWith(cap(g.lead)) ? [h('b', { text: cap(g.lead) }), t.slice(g.lead.length)] : t;
}
function gainLine(g) {
  if (!g || !g.summary) return null;
  return h('span', { class: 'mgain' }, gainText(g));
}
// The dungeon's quests to take along: who gives each and where, the XP it
// pays now; three in sight, the rest folded. Those already in the log are
// counted, not listed.
function gainQuests(g) {
  if (!g || !g.quests || !g.quests.length) return null;
  const pick = g.quests.filter((q) => q.state === 'new');
  const inLog = g.quests.length - pick.length;
  const log = inLog ? `${plural(inLog, 'quest')} already in your log` : null;
  if (!pick.length) return h('span', { class: 'sub', text: cap(log) + '.' });
  const row = (q) => h('li', {}, whLink(q.url, q.name || `quest ${q.quest_id}`),
    q.starts.length ? [' from ', npcs(q.starts.slice(0, 1))] : null,
    q.xp_now ? h('span', { class: 'sub', text: `, ${fmtK(q.xp_now)} XP` }) : null);
  return h('div', { class: 'mquests' }, h('span', { text: 'Pick up first:' }),
    capped(pick.map(row), g.quests_shown || 3, (n) => `${n} more quest${n === 1 ? '' : 's'}`, 'gquests'),
    log ? h('span', { class: 'sub', text: `Plus ${log}.` }) : null);
}

// Coins as text with their units, for a sentence: 3g 40s, without the empty
// ones (100g, not 100g 0s 0c). Prices, the bag and what is left to go all use
// it, so a move never mixes coin icons with letters.
function coinText(copper) {
  const parts = [[Math.floor(copper / 10000), 'g'], [Math.floor((copper % 10000) / 100), 's'], [copper % 100, 'c']].filter(([n]) => n);
  return parts.length ? parts.map(([n, u]) => `${fmtNum(n)}${u}`).join(' ') : '0c';
}

// At most a few on show: the rest behind one "N more", the data complete.
function capped(items, shown, more, cls) {
  const list = h('ul', { class: cls }, items.slice(0, shown));
  if (items.length <= shown) return list;
  const rest = h('ul', { class: cls + ' more', hidden: true }, items.slice(shown));
  const b = h('button', { type: 'button', class: 'morebtn', 'aria-expanded': 'false', text: more(items.length - shown) });
  b.addEventListener('click', () => {
    const open = rest.hidden;
    rest.hidden = !open;
    b.setAttribute('aria-expanded', String(open));
    b.textContent = open ? 'Show fewer' : more(items.length - shown);
  });
  return h('div', {}, list, rest, b);
}

function renderOverview(ov, err, an) {
  const el = $('#home');
  if (err) { fill(el, h('p', { class: 'err', text: `Couldn’t read the overview: ${err}.` })); return; }
  const hd = ov.header || {};
  const tr = ov.on_track || {};
  const [tone, verdict] = verdictLine(ov);
  const played = hd.played_total_s ? `${dur(hd.played_total_s)} played since level 1`
    : hd.played_s ? `${dur(hd.played_s)} played since level ${hd.first_level}` : null;
  const nowZone = (ov.now || []).find((c) => c.zone);
  const colour = classColour(hd.class);
  const banner = h('section', { class: 'plate banner ovbanner' },
    h('div', { class: 'ovident' },
      h('h1', { text: hd.name || state.char.replace(/-.*$/, '') }),
      h('p', { class: 'line' }, hd.level ? `Level ${hd.level}` : null, hd.race ? ` ${hd.race}` : null,
        hd.class ? [' ', h('b', { text: hd.class })] : null,
        played ? h('span', { class: 'muted', text: `, ${played}` }) : null),
      nowZone ? h('p', { class: 'muted nowline' }, 'Now in ', whRef(nowZone.zone), '.') : null),
    h('p', { class: 'verdict ' + tone, text: verdict }),
    overviewTiles(ov));
  // Through the CSSOM: the page's CSP refuses style attributes.
  if (colour) banner.style.setProperty('--class', colour);

  const kids = [banner];
  // No "observations" block here (owner, 2026-11-18: everything shown is
  // actionable): the analysis' pattern lines describe, and stay folded in
  // the full view's "How you play"; its actions are there and, screened,
  // on /zogzog/.
  const moves = ov.moves || [];
  const recent = tr.per_level || [];
  const paceHost = h('div', { class: 'pacehost' });
  paceRedraw = null;
  kids.push(h('div', { class: 'ovrow' },
    h('section', { class: 'block ovmoves', 'aria-label': 'Next moves' },
      h('h2', { text: 'Next moves' }),
      moves.length ? capped(moves.map((mv) => moveEl(mv, nowZone && nowZone.zone && nowZone.zone.name)), 3, (n) => `${n} more`, 'moves')
        : h('p', { class: 'empty', text: (ov.levels_seen || 0) ? 'Nothing to add to the guide right now: no upgrade in reach, no profession capped, nothing that keeps going wrong.'
          : 'Nothing to suggest yet. Play with Polestar and Polestar Recorder on, then /reload.' }),
    ),
    h('section', { class: 'block ovpace', 'aria-label': 'Time per level' },
      h('h2', { text: 'Time per level' }),
      recent.length ? paceHost : h('p', { class: 'empty', text: 'Finish a level with Polestar on: its time shows here next to the route’s.' }))));
  fill(el, ...kids);
  if (recent.length) drawPace(paceHost, recent);
}

// ------------------------------------------------------------ timeline and back in time

const TL_NAMES = { level: 'Levels', session: 'Sessions', dungeon: 'Dungeons', death: 'Deaths', gear: 'Gear', profession: 'Professions', guide: 'Guides' };
async function loadTimeline() {
  try {
    const t = await getJSON(api('timeline', { name: state.char }));
    state.timeline = t;
    renderTimeline(t, null);
  } catch (e) {
    renderTimeline(null, e.message);
  }
}

function renderTimeline(t, err) {
  const el = $('#timeline');
  if (err) { fill(el, h('p', { class: 'err', text: `Couldn’t read the timeline: ${err}.` })); return; }
  if (!t.events) {
    // The public view: the level slider, the gear and the 3D look at each level.
    fill(el, h('div', { class: 'jhead' }, h('h2', { text: 'Level by level' }),
      h('p', { class: 'lede', text: t.levels.length ? 'Slide through the levels: what it wore at each one, and how it looked when the character sheet of that time was saved.'
        : 'Nothing recorded level by level for this character yet: Polestar’s splits fill this page once it has played with them on.' })),
      t.levels.length ? backInTime(t) : null);
    return;
  }
  state.tlOff = state.tlOff || new Set();
  const chips = h('div', { class: 'chips', role: 'group', 'aria-label': 'Show' }, t.kinds.map((k) => {
    const n = t.events.filter((e) => e.type === k).length;
    const b = h('button', { type: 'button', class: 'chip', 'aria-pressed': String(!state.tlOff.has(k)), text: `${TL_NAMES[k] || k} ${n}` });
    b.addEventListener('click', () => { if (state.tlOff.has(k)) state.tlOff.delete(k); else state.tlOff.add(k); renderTimeline(t, null); });
    return b;
  }));
  const shown = t.events.filter((e) => !state.tlOff.has(e.type));
  const list = shown.length ? h('ol', { class: 'tl' }, shown.slice(0, 300).map((e) => {
    const a = h('a', { href: e.link, text: e.text });
    a.addEventListener('click', (ev) => { ev.preventDefault(); goTo(e.link); });
    return h('li', { class: 'tl-' + e.type }, h('time', { datetime: e.t, text: `${day(e.t)} ${timeFmt.format(new Date(e.t))}` }), a);
  })) : h('p', { class: 'sub', text: t.events.length ? 'Nothing of those kinds.' : 'Nothing recorded yet. ' + RECORDER_ON });
  const kids = [h('div', { class: 'jhead' }, h('h2', { text: 'Timeline' }),
    h('p', { class: 'lede', text: 'Everything recorded, newest first. ' + (t.gear_history_since
      ? `Gear changes are known from ${day(t.gear_history_since)}, the first character sheet the shipper sent.`
      : 'No character sheet yet, so no gear changes.') }))];
  if (t.levels.length) kids.push(backInTime(t));
  kids.push(chips, list);
  fill(el, ...kids);
}

function backInTime(t) {
  const lv = t.levels;
  const cur = state.snapLevel && lv.includes(state.snapLevel) ? state.snapLevel : lv[lv.length - 1];
  // The facts and gear, then the 3D model (kept from level to level: the
  // frame isn't reloaded: the 3D script dresses it again), then the talents.
  const info = h('div', { 'aria-live': 'polite' });
  const holder = h('div', { class: 'snapframe' });
  const tail = h('div');
  const out = h('div', { class: 'snapbody' }, info, holder, tail);
  out.parts = { info, holder, tail };
  const label = h('output', { text: `Level ${cur}` });
  const slider = h('input', { type: 'range', min: 0, max: lv.length - 1, step: 1, value: lv.indexOf(cur), 'aria-label': 'Level' });
  let tm;
  slider.addEventListener('input', () => {
    const l = lv[Number(slider.value)];
    label.textContent = `Level ${l}`;
    clearTimeout(tm);
    tm = setTimeout(() => loadSnapshot(l, out), 200);
  });
  loadSnapshot(cur, out);
  return h('section', { class: 'plate snap' }, h('h2', { class: 'rule-title', text: 'Back in time' }),
    h('div', { class: 'snapctl' }, slider, label), out);
}

async function loadSnapshot(level, out) {
  state.snapLevel = level;
  try {
    const sn = await getJSON(api('snapshot', { name: state.char, level }));
    if (state.snapLevel === level) renderSnapshot(sn, out);
  } catch (e) {
    fill(out.parts.info, h('p', { class: 'err', text: `Couldn’t read level ${level}: ${e.message}.` }));
  }
}

// A past level's gear: "exact" from a character sheet, "likely" from the
// replay of what the recorder saw the character get (wowstats/history.py),
// with how it was got; slots it knows nothing about are listed as unknown.
// A reconstruction is never shown as fact.
const SNAP_SLOTS = [1, 2, 3, 15, 5, 9, 10, 6, 7, 8, 11, 12, 13, 14, 16, 17, 18];
function snapSource(src) {
  const s = src || {};
  if (s.kind === 'quest') return s.quest ? `quest reward from ${s.quest}` : 'quest reward';
  if (s.kind === 'loot') return 'looted' + (s.npc ? ` from ${s.npc}` : '') + (s.zone ? ` in ${s.zone}` : '');
  if (s.kind === 'craft') return 'crafted';
  if (s.kind === 'enchant') return 'enchanted then, so already owned';
  if (s.kind === 'sheet') return 'already had it';
  return 'had it then';
}
function snapGear(sn) {
  const items = (sn.gear.items || []).filter((i) => i.item_id);
  if (!sn.gear_reconstructed) {
    return [h('div', { class: 'worn cert-exact' }, h('b', { text: 'Worn: ' }),
      h('ul', { class: 'chips' }, items.map((i) => h('li', {}, itemChip(i)))))];
  }
  const have = new Set(items.map((i) => i.slot));
  const unknown = SNAP_SLOTS.filter((sl) => !have.has(sl)).map((sl) => SLOT_NAME[sl]);
  // Likely items: the icon and tooltip like any item, the word "likely" and
  // where it came from under the name and in the tooltip, never as fact.
  const chip = (i) => {
    const exact = i.certainty === 'exact';
    const why = exact ? 'worn' : `likely: ${snapSource(i.source)}`;
    return h('li', { class: `cert-${i.certainty || 'likely'}`, 'data-slot': i.slot },
      itemChip({ ...i, extra: [`${i.slot_name || SLOT_NAME[i.slot] || ''}, ${why}`] },
        { sub: `${i.slot_name || SLOT_NAME[i.slot] || ''}, ${exact ? 'worn' : 'likely'}` }));
  };
  return [
    h('p', { class: 'sub partial', text: 'Likely gear: rebuilt from what the character looted.' }),
    h('ul', { class: 'snapgear chips' }, items.map(chip)),
    unknown.length ? h('p', { class: 'sub cert-unknown', text: `Unknown: ${unknown.join(', ')}.` }) : null,
  ].filter(Boolean);
}

function renderSnapshot(sn, out) {
  const { info, holder, tail } = out.parts;
  if (!sn.available) { fill(info, h('p', { class: 'sub', text: sn.why })); holder.replaceChildren(); fill(tail); return; }
  const facts = 'seconds' in sn ? [
    ['Time', sn.seconds ? dur(sn.seconds) + (sn.so_far ? ' so far' : sn.partial_time ? ' (partly recorded)' : '') : sn.to ? 'not recorded' : 'in progress'],
    ['Route plans', sn.route_s ? dur(sn.route_s) : 'no data'],
    ['Quests, kills', sn.counts && Object.keys(sn.counts).length ? `${sn.counts.turnin || 0} quests, ${sn.counts.kill || 0} kills` : 'not recorded'],
    ['Deaths', String(Array.isArray(sn.deaths) ? sn.deaths.length : sn.deaths || 0)],
  ] : [['Level', String(sn.level)]];
  if (sn.gear && sn.gear.item_level) facts.push(['Item level', String(sn.gear.item_level)]);
  const kids = [h('dl', { class: 'snapfacts' }, facts.map(([k, v]) => h('div', {}, h('dt', { text: k }), h('dd', { text: v }))))];
  if ((sn.zones || []).length) kids.push(h('p', {}, h('b', { text: 'Zones: ' }), sn.zones.map((z) => `${z.zone} ${dur(z.secs)}`).join(', ')));
  if (sn.gear) {
    kids.push(...snapGear(sn));
    if (sn.bis && sn.bis.upgrades && sn.bis.upgrades.length) {
      kids.push(h('p', {}, h('b', { text: 'Best upgrades then: ' }), sn.bis.upgrades.slice(0, 3).map((u) => `${u.name} +${u.gain.toFixed(1)}`).join(', ')));
    }
    if (window.wowPastLevel) window.wowPastLevel(holder, state.char, sn.level, sn.picture);
  } else {
    holder.replaceChildren();
    if ((sn.looted_gear || []).length) kids.push(h('p', { class: 'sub partial', text: 'Looted at this level:' }));
    if ((sn.looted_gear || []).length) kids.push(h('ul', { class: 'looted chips' }, sn.looted_gear.map((g) => h('li', {}, itemChip(g)))));
  }
  fill(info, ...kids);
  fill(tail, talentsAt(sn.level));
}

// ------------------------------------------------------------ ask your assistant and debug (full view)

// "Ask Claude" and "Ask ChatGPT": a new chat with an instruction filled in,
// never data. The user's own assistant runs the analysis through the wow
// connector (the MCP's `analyse` tool); the user reads it and sends it.
const ASK = {
  askclaude: 'https://claude.ai/new?q=',
  // ?prompt= fills the box and waits; ?q= sends at once (checked 2026-11-18).
  askchatgpt: 'https://chatgpt.com/?prompt=',
};

function askText(name) {
  return `Using the wow connector, run the analyse prompt for ${name}.`;
}

function setAsk(name) {
  const box = $('#askbox');
  if (!box || !feat('mcp')) return;
  box.hidden = false;
  const q = encodeURIComponent(askText(name)).slice(0, 1900);
  for (const [id, base] of Object.entries(ASK)) {
    const a = document.getElementById(id);
    if (a) a.href = base + q;
  }
}

// The session planner (wowstats/plan): the mount goal, how the last plan
// went, and a plan for the next hours. Full view only.
async function loadPlan(name) {
  const box = $('#planbox');
  if (!box) return;
  let s = null;
  try { s = await getJSON(api('plan', { char: name })); } catch (e) { s = null; }
  if (name !== state.char) return;
  box.hidden = false;
  const out = $('#planstatus');
  if (!s || !s.available) {
    fill(out, h('p', { class: 'sub', text: s && s.why ? `No plan data yet: ${s.why}.` : 'No plan data yet.' }));
    return;
  }
  const tiers = (s.tiers || []).filter((t) => !t.bought).slice(0, 2).map((t) => h('div', { class: 'tier' },
    h('div', { text: `Riding at ${t.level}: ${t.need} needed, ${t.left} to go` +
      (t.eta ? ` (about ${t.eta} of play at ${s.gold_rate}${t.eta_level ? `, level ${t.eta_level}` : ''})` :
        s.gold_rate ? '' : ' (no gold rate measured yet)') + (t.behind ? '. Behind your level pace.' : '.') }),
    h('div', { class: 'bar', role: 'img', 'aria-label': `${t.pct || 0}% saved` },
      h('i', { 'width%': Math.max(0, Math.min(100, t.pct || 0)) }))));
  const r = s.review;
  const rev = r ? h('p', { text: `Last plan (${r.hours} h): ` + (r.ratio ? `you needed ${r.ratio}× the planned time` :
    'not far enough in to compare') + (r.end_actual != null ? `; level ${r.end_actual} at its end, ${r.end_planned} planned.` :
    '.') }) : h('p', { class: 'sub', text: 'No plan compared yet: make one, play past its first checkpoint.' });
  fill(out, h('p', { text: `Gold ${s.gold}` + (s.gold_rate ? `, ${s.gold_rate} net over ${s.gold_rate_n} logouts.` : '.') }),
    ...tiers, rev);
}

// The plan's Markdown (headings, bullets, one nested level, **bold**) as DOM
// nodes built from text: never parsed as HTML.
function planNodes(md) {
  const inline = (s) => s.split(/\*\*(.+?)\*\*/).map((part, i) => (i % 2 ? h('strong', { text: part }) : part));
  const out = [];
  let list = null;
  let last = null;
  for (const line of md.split('\n')) {
    const m = line.match(/^(#{2,3}) (.*)$/);
    if (m) { list = null; out.push(h(m[1] === '##' ? 'h3' : 'h4', { text: m[2] })); continue; }
    const li = line.match(/^( *)- (.*)$/);
    if (li) {
      if (li[1].length && last) {
        let sub = last.querySelector('ul');
        if (!sub) { sub = h('ul'); last.append(sub); }
        sub.append(h('li', {}, ...inline(li[2])));
        continue;
      }
      if (!list) { list = h('ul'); out.push(list); }
      last = h('li', {}, ...inline(li[2]));
      list.append(last);
      continue;
    }
    list = null;
    if (line.trim()) out.push(h('p', {}, ...inline(line)));
  }
  return out;
}

function initPlan() {
  const form = $('#planform');
  if (!form) return;
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const btn = $('#planbtn');
    const out = $('#planout');
    btn.disabled = true;
    fill(out, h('p', { class: 'loading', text: 'Planning. Up to a minute.' }));
    try {
      const r = await fetch('/api/plan', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ char: state.char, hours: Number($('#planhours').value), constraints: $('#planwhat').value }),
      });
      const body = await r.json().catch(() => null);
      if (!r.ok) throw new Error((body && (body.why || body.error)) || `${r.status} ${r.statusText}`);
      fill(out, h('div', { class: 'plantext' }, ...planNodes(body.markdown || '')));
      loadPlan(state.char);
    } catch (e) {
      fill(out, h('p', { class: 'err', text: `No plan: ${e.message}.` }));
    } finally {
      btn.disabled = false;
    }
  });
}

function initDebug() {
  const form = $('#debugform');
  if (!form) return;
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const out = $('#debugout');
    out.textContent = 'Running';
    try {
      const r = await getJSON('/api/logsql?q=' + encodeURIComponent($('#debugq').value));
      out.textContent = r.rows.map((x) => JSON.stringify(x)).join('\n') || 'No rows.';
    } catch (e) {
      out.textContent = e.message;
    }
  });
}

// ------------------------------------------------------------ footer

function renderFoot(d, err) {
  const kids = [];
  if (err) kids.push(h('p', { class: 'warn', text: `Couldn’t refresh: ${err}.` }));
  if (d && d.stale) kids.push(h('p', { class: 'warn', text: `Showing the last good answer: ${d.error}.` }));
  if (d && d.sources && d.sources.archive === 'unreachable') {
    kids.push(h('p', { class: 'warn', text: `History older than ${d.sources.archive_after_days} days is missing: the archive VictoriaLogs didn’t answer.` }));
  }
  if (d) kids.push(h('p', { text: STATIC ? `As of ${day(d.generated_at)} ${timeFmt.format(new Date(d.generated_at))}. Rebuilt after each play session.`
    : `As of ${timeFmt.format(new Date(d.generated_at))}.` }));
  fill($('#foot'), ...kids);
}

// ------------------------------------------------------------ new data

// The live view used to redraw every section each minute: it closed what
// the reader had opened, dropped a comparison and typed text, and moved the
// page under them (owner, 2026-11-19: "it just messes up with what i'm
// doing"). Now it only asks, every few minutes while the tab is in view,
// whether the character's answer changed, and says so in a pill; the page
// changes when the reader clicks it, and keeps their place.
const NEWS_EVERY_MS = 3 * 60 * 1000;
const news = { shown: null, at: 0, timer: null, pill: null };

// The answer without its clock: generated_at moves on every build.
function newsSig(d) {
  if (!d) return null;
  const { generated_at: _g, stale: _s, error: _e, ...rest } = d;
  return JSON.stringify(rest);
}

function newsWatch() {
  news.at = Date.now();
  newsSchedule();
  document.addEventListener('visibilitychange', newsSchedule);
}

// One timer, only while the tab is visible: a hidden tab asks nothing, and
// one that comes back late asks once, right away.
function newsSchedule() {
  clearTimeout(news.timer);
  news.timer = null;
  if (document.visibilityState !== 'visible' || (news.pill && !news.pill.hidden)) return;
  news.timer = setTimeout(newsCheck, Math.max(0, NEWS_EVERY_MS - (Date.now() - news.at)));
}

async function newsCheck() {
  news.timer = null;
  if (document.visibilityState !== 'visible') return;
  news.at = Date.now();
  const name = state.char;
  try {
    // Its own fetch, not getJSON: a lapsed login must not reload the page.
    const r = await fetch(api('char', { name }), { headers: { Accept: 'application/json' }, credentials: 'same-origin' });
    const d = r.ok ? await r.json() : null;
    if (d && name === state.char && news.shown && newsSig(d) !== news.shown) newsShow();
  } catch (e) { /* offline or logged out: the next check asks again */ }
  newsSchedule();
}

function newsShow() {
  if (!news.pill) {
    const b = h('button', { type: 'button', class: 'newsbtn' }, 'New data', h('span', { class: 'newsdot', 'aria-hidden': 'true', text: '·' }), h('b', { text: 'Show' }));
    b.addEventListener('click', newsApply);
    news.pill = h('div', { class: 'newspill', role: 'status', hidden: true }, b);
    document.body.append(news.pill);
  }
  news.pill.hidden = false;
}

function newsHide() {
  if (news.pill) news.pill.hidden = true;
  newsSchedule();
}

// What the reader set, to put back once the new answer is drawn: the
// scroll, the open folds, the fields, the focus. A fold is known by its
// section and its summary without numbers ("3 more upgrades" may be "4").
function foldKey(el) {
  const sec = el.closest('[id]');
  const sum = el.querySelector(':scope > summary');
  return `${sec ? sec.id : ''}|${sum ? sum.textContent.replace(/\d+/g, '#').trim() : ''}`;
}
function fieldKey(el) {
  if (el.id) return '#' + el.id;
  const sec = el.closest('[id]');
  return `${sec ? sec.id : ''}|${el.tagName}.${el.className}|${el.getAttribute('aria-label') || ''}`;
}
function keepView() {
  const open = {};
  for (const d of document.querySelectorAll('details')) {
    const k = foldKey(d);
    (open[k] = open[k] || []).push(d.open);
  }
  const fields = new Map();
  for (const f of document.querySelectorAll('input:not([type=hidden]), select, textarea')) {
    const k = fieldKey(f);
    if (!fields.has(k)) fields.set(k, f.type === 'checkbox' ? f.checked : f.value);
  }
  const act = document.activeElement;
  const focus = act && act !== document.body && act.matches('input, select, textarea') ? fieldKey(act) : null;
  return { x: window.scrollX, y: window.scrollY, open, fields, focus };
}
function restoreView(v) {
  const seen = {};
  for (const d of document.querySelectorAll('details')) {
    const k = foldKey(d);
    const i = (seen[k] = (seen[k] || 0) + 1) - 1;
    if (v.open[k] && i < v.open[k].length && d.open !== v.open[k][i]) d.open = v.open[k][i];
  }
  for (const f of document.querySelectorAll('input:not([type=hidden]), select, textarea')) {
    const k = fieldKey(f);
    if (!v.fields.has(k)) continue;
    const was = v.fields.get(k);
    if (f.type === 'checkbox' ? f.checked === was : f.value === was) continue;
    // A select only takes a value it still offers; the redraw already
    // honours the saved ones ("Scored for" lives in localStorage).
    if (f.tagName === 'SELECT' && ![...f.options].some((o) => o.value === was)) continue;
    if (f.type === 'checkbox') f.checked = was; else f.value = was;
    if (f.tagName !== 'SELECT') f.dispatchEvent(new Event('input', { bubbles: true }));  // a search filters again
  }
  if (v.focus) {
    const f = [...document.querySelectorAll('input, select, textarea')].find((x) => fieldKey(x) === v.focus);
    if (f) f.focus({ preventScroll: true });
  }
  window.scrollTo(v.x, v.y);
}

async function newsApply() {
  const v = keepView();
  if (news.pill) news.pill.hidden = true;
  await loadChar(state.char, true);
  await state.drawn;
  restoreView(v);
  news.at = Date.now();
  newsSchedule();
}

// ------------------------------------------------------------ flow

function renderAll(d) {
  renderHero(d);
  // A section the page doesn't have (the public one has four) isn't drawn.
  const where = { renderZones: '#zones', renderGuide: '#guide', renderDeaths: '#deaths', renderPeople: '#people', renderFlights: '#flights' };
  for (const fn of [renderGear, renderTalents, renderClassGuide, renderLevels, renderZones, renderGuide, renderDungeons, renderDeaths, renderPeople, renderFlights]) {
    if (where[fn.name] && !$(where[fn.name])) continue;
    try { fn(d); } catch (e) { console.error(fn.name, e); }
  }
  // No People in /zogzog/ (other players: the live full view only).
  if ($('#people') && !feat('people')) {
    $('#people').hidden = true;
    const a = document.querySelector('.sections a[href="#people"]');
    if (a) a.hidden = true;
  }
  const c = state.chars.find((x) => x.char === state.char);
  $('#demo').hidden = !(c && c.demo);
  document.title = `${d.header.name} ${d.header.level ?? ''}`.trim() + (d.header.title ? ` · ${d.header.title}` : '');
}

async function loadChar(name, quiet) {
  try {
    const jobs = [getJSON(api('char', { name }))];
    if (feat('market')) jobs.push(getJSON(api('market')));
    if (feat('prices')) jobs.push(getJSON(api('prices')));
    const [d, m, p] = await Promise.allSettled(jobs);
    if (d.status === 'rejected') throw d.reason;
    state.data = d.value;
    renderAll(d.value);
    if (m) renderMarket(m.status === 'fulfilled' ? m.value : null, m.status === 'rejected' ? m.reason.message : null);
    if (p) renderPrices(p.status === 'fulfilled' ? p.value : null, p.status === 'rejected' ? p.reason.message : null);
    state.prices = p && p.status === 'fulfilled' ? p.value : null;
    renderFoot(d.value, null);
    news.shown = newsSig(d.value);
    // The sections drawn after this answer: "Show" puts the reader back
    // only once they are all in.
    const more = [];
    if (!PUBLIC) more.push(loadAdvice(name));
    if (feat('loot')) more.push(loadLoot(name));
    if (!PUBLIC) more.push(loadAnalysis(name));
    if (!PUBLIC) more.push(loadDungeons(name));
    if (!PUBLIC && feat('plan')) more.push(loadPlan(name));
    if (!PUBLIC) setAsk(name);
    if (state.tab === 'journal') more.push(loadJournal(quiet));
    if (state.tab === 'home') more.push(loadOverview(quiet));
    if (state.tab === 'timeline' && !quiet) more.push(loadTimeline());
    if (feat('people')) more.push(loadPeople(name));
    state.drawn = Promise.allSettled(more);
  } catch (e) {
    if (!quiet || !state.data) {
      fill($('#hero'), h('p', { class: 'err', text: `Couldn’t load ${name}: ${e.message}.` }));
    }
    renderFoot(state.data, e.message);
  }
}

// No character yet (a first visit, before the first data arrives): one
// panel with the next install step, wherever the page would have opened,
// instead of a page that keeps "Reading the logs". With several people,
// the step comes from the person's own progress (/api/me).
async function renderNoChars() {
  let me = null;
  if (!STATIC && !PUBLIC) {
    try { me = await getJSON('/api/me'); } catch (e) { me = null; }
  }
  const step = (id, n, what) => h('p', {}, h('a', { class: 'coachbtn', href: `/start#step-${id}`, text: `Step ${n}: ${what}` }));
  let next;
  if (STATIC || PUBLIC) {
    next = [h('p', { text: 'Nothing here yet. The page fills in after the first session played with the addons on.' })];
  } else if (me && !me.slug) {
    next = [h('p', { text: 'Pick your name, then install the shipper.' }), step('name', 2, 'pick your name')];
  } else if (me && !me.shipper_set_at) {
    next = [h('p', { text: 'Next: install the shipper on the computer you play on.' }), step('install', 4, 'run the install command')];
  } else if (me) {
    next = [h('p', { text: 'The shipper is installed. Play a few minutes with Polestar and Polestar Recorder on, then /reload: your character shows up here within 5 minutes.' }),
      step('play', 5, 'play 5 minutes')];
  } else {
    next = [h('p', { text: 'Install the shipper on the computer you play on and point it at this server (README, “Install in 3 steps”). Then play a few minutes with Polestar and Polestar Recorder on and /reload.' })];
  }
  const panel = h('div', { class: 'who nochars' }, h('h1', { text: 'No character yet' }), ...next);
  for (const sel of ['.sections', '.grid', '.tabs']) {
    const el = document.querySelector(sel);
    if (el) el.hidden = true;
  }
  const home = $('#home');
  if (home) {
    // The full view opens on the Overview: the panel goes there.
    home.hidden = false;
    fill(home, h('section', { class: 'plate banner' }, panel));
    $('#main').hidden = true;
  } else {
    $('#main').hidden = false;
    fill($('#hero'), panel);
  }
  for (const id of ['#timeline', '#journal']) {
    const el = $(id);
    if (el) el.hidden = true;
  }
}

async function start() {
  initTheme();
  if (SEALED) {
    // No key, or not this bundle's: the same empty page either way.
    try { state.config = await getJSON(api('config')); } catch (e) { locked(); return; }
  } else {
    try {
      state.config = await getJSON(api('config'));
    } catch (e) {
      state.config = { view: 'public' };
    }
  }
  if (!PUBLIC && feat('plan')) initPlan();
  initDebug();
  for (const k of Object.keys(TABS)) $('#tab-' + k).addEventListener('click', () => setTab(k));
  let chars;
  try {
    chars = (await getJSON(api('chars'))).chars;
  } catch (e) {
    fill($('#hero'), h('p', { class: 'err', text: `Couldn’t reach the logs: ${e.message}.` }));
    return;
  }
  const sel = $('#char');
  if (!chars.length) {
    fill(sel, h('option', { text: 'No characters yet' }));
    await renderNoChars();
    return;
  }
  state.chars = chars;
  fill(sel, ...chars.map((c) => h('option', { value: c.char, text: (c.level ? `${c.char.replace(/-.*$/, '')}, level ${c.level}` : c.char.replace(/-.*$/, '')) + (c.demo ? ' (demo)' : '') })));
  const params = new URLSearchParams(location.search);
  const wanted = params.get('char');
  state.char = chars.some((c) => c.char === wanted) ? wanted : chars[0].char;
  sel.value = state.char;
  sel.addEventListener('change', () => {
    state.char = sel.value;
    state.journal = null;
    state.overview = null;
    state.timeline = null;
    state.snapLevel = null;
    const u = new URL(location.href);
    u.searchParams.set('char', state.char);
    history.replaceState(null, '', u);
    window.scrollTo({ top: 0 });
    newsHide();
    loadChar(state.char);
  });
  if (PUBLIC) $('#tab-timeline').textContent = 'Level by level';
  setTab(params.get('tab') || HOME);
  await loadChar(state.char);
  if (!STATIC) newsWatch();
  let rt;
  window.addEventListener('resize', () => {
    clearTimeout(rt);
    rt = setTimeout(() => {
      if (state.data) renderClimb($('#levels .climb') || h('div'), state.data);
      if (paceRedraw && state.tab === 'home') paceRedraw();
    }, 150);
  });
}

// ------------------------------------------------------------ theme

const THEMES = ['system', 'light', 'dark'];
function initTheme() {
  let t = 'system';
  try { t = localStorage.getItem('wow-theme') || 'system'; } catch (e) { /* private mode */ }
  applyTheme(THEMES.includes(t) ? t : 'system');
  $('#theme').addEventListener('click', () => {
    const cur = document.documentElement.dataset.theme || 'system';
    const next = THEMES[(THEMES.indexOf(cur) + 1) % THEMES.length];
    applyTheme(next);
    try { localStorage.setItem('wow-theme', next); } catch (e) { /* private mode */ }
  });
}
function applyTheme(t) {
  if (t === 'system') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = t;
  $('#theme').textContent = { system: 'Auto', light: 'Day', dark: 'Night' }[t];
  $('#theme').setAttribute('aria-label', `Colour theme: ${{ system: 'auto', light: 'day', dark: 'night' }[t]}. Switch`);
}

start();
})();
