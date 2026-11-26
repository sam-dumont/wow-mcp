// Item icons and the in-game tooltip, for every item on every page.
//
// Cards (name, quality, slot, type, armor, stats, required level, item
// level, set, flavour text) come from the server's item catalog
// (wowstats/itemcard.py): /api/items in the live view, data/items.json in a
// static bundle (sealed in /zogzog/ like every data file). Icons are the
// game's own, as Wowhead renders them: /icons/<size>/<name>.jpg in the live
// full view, data/icon/<size>/<name>.jpg in a bundle. No third-party request
// either way. An item without an icon of its own (or without a card) shows
// the game's question mark, as the client does; where no icon can be had at
// all (the live public view), the caller's slot glyph or a drawn "?" stays.
// Never an empty frame.
//
// window.wowTip.bind(el, build): one tooltip for the whole page. Hover on a
// mouse, tap (or Enter) toggles it, tapping elsewhere or Esc closes it, one
// open at a time; on a narrow screen it is a panel at the bottom.
(function () {
  const root = document.documentElement;
  const STATIC = root.dataset.static === '1';
  const SEALED = root.dataset.sealed === '1';
  const FULL = root.dataset.view === 'full';
  const QUALITY = ['Poor', 'Common', 'Uncommon', 'Rare', 'Epic', 'Legendary'];
  const QUESTION = 'inv_misc_questionmark';  // wowstats/itemcard.py QUESTION

  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'text') n.textContent = v;
      else if (k === 'class') n.className = v;
      else n.setAttribute(k, v === true ? '' : v);
    }
    for (const c of kids.flat()) if (c !== null && c !== undefined && c !== false) n.append(c);
    return n;
  }

  // ------------------------------------------------------------ Wowhead

  // WoW Forever's own database on Wowhead (wowstats/links.py holds the
  // same address): /forever/item=, /quest=, /npc=, /spell=, /zone= open that
  // object (checked in Chromium, 2026-11-18). A plain link in a new tab with
  // no referrer; Wowhead's tooltip script is never loaded.
  const WOWHEAD = 'https://www.wowhead.com/forever';
  function link(kind, id, text, cls) {
    const n = Number(id);
    if (!Number.isInteger(n) || n <= 0) return el('span', { class: cls || null, text });
    return el('a', { class: 'wh' + (cls ? ' ' + cls : ''), href: `${WOWHEAD}/${kind}=${n}`, target: '_blank',
      rel: 'noopener noreferrer', referrerpolicy: 'no-referrer', text });
  }
  window.wowLink = link;

  // ------------------------------------------------------------ cards

  const cards = new Map();
  let bundleCards = null;
  async function readCards(url) {
    // A reset can lose a whole batch, including the only names for worn
    // items. getJSON returns null if the response body was cut short.
    try {
      const data = await window.wowGetJSON(url);
      if (data) return data;  // an empty items list is a complete answer
    } catch (e) {
      if (!(e instanceof TypeError) && !(e.status >= 500)) throw e;
    }
    return window.wowGetJSON(url);
  }
  async function loadCards(ids) {
    if (STATIC) {
      if (!bundleCards) {
        bundleCards = readCards(window.wowApi('items'))
          .then((d) => { for (const c of d.items || []) cards.set(c.id, c); })
          .catch(() => {});
      }
      return bundleCards;
    }
    for (let k = 0; k < ids.length; k += 200) {
      try {
        const d = await readCards('/api/items?ids=' + ids.slice(k, k + 200).join(','));
        for (const c of d.items || []) cards.set(c.id, c);
      } catch (e) { /* no catalog: glyphs and plain names stay */ }
    }
  }
  // One promise per item, asked once; the ids asked in one pass of drawing
  // go out in one request.
  const asked = new Map();
  let queue = [];
  function card(id) {
    if (!Number.isInteger(id) || id <= 0) return Promise.resolve(null);
    if (asked.has(id)) return asked.get(id);
    const p = new Promise((res) => queue.push([id, res]));
    asked.set(id, p);
    if (queue.length === 1) {
      // Start the lookup before the page's next task can mark it drawn.
      queueMicrotask(() => {
        const batch = queue;
        queue = [];
        loadCards(batch.map(([i]) => i)).finally(() => batch.forEach(([i, res]) => res(cards.get(i) || null)));
      });
    }
    return p;
  }
  window.wowCard = card;

  // ------------------------------------------------------------ icons

  const glyphPaths = {
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
  window.wowGlyphPaths = glyphPaths;
  // The hosted demo shares these drawings with the equipment slots. Game
  // icon names are metadata. No art is downloaded to illustrate them.
  const iconFamilies = [
    [/helm|helmet|crown|hat_/, 'head'], [/neck|amulet/, 'neck'], [/shoulder|pauldron/, 'shoulder'],
    [/cape|cloak/, 'back'], [/chest|robe|armor/, 'chest'], [/bracer|wrist/, 'wrist'],
    [/glove|gauntlet/, 'hands'], [/belt|girdle/, 'waist'], [/pants|leg|kilt/, 'legs'],
    [/boot|shoe/, 'feet'], [/ring|jewelry/, 'finger'], [/shield|buckler/, 'off'],
    [/bow|rifle|gun|wand|crossbow/, 'ranged'], [/sword|axe|hammer|mace|staff|dagger|weapon/, 'main'],
  ];
  const spellPaths = {
    fire: 'M13 3c1 5-3 6-1 10 2-1 3-3 3-5 4 4 5 7 3 10-2 4-9 4-12 0-2-4 0-7 3-10 0 3 0 4 1 5 0-5 2-6 3-10z',
    frost: 'M12 3v18 M4 7l16 10 M4 17L20 7 M9 5l3 3 3-3 M9 19l3-3 3 3',
    nature: 'M4 19c0-9 4-14 16-15-1 11-5 16-13 15 M5 20l10-11',
    shadow: 'M16 4A8.5 8.5 0 1 0 20 15 7.5 7.5 0 0 1 16 4z',
    holy: 'M10 3h4v6h6v4h-6v8h-4v-8H4V9h6z',
  };
  const drawings = new Map();
  function drawnIcon(name) {
    const school = name.split('_')[1];
    const family = iconFamilies.find(([pattern]) => pattern.test(name));
    const path = school && Object.hasOwn(spellPaths, school) ? spellPaths[school]
      : glyphPaths[family ? family[1] : 'trinket'];
    if (!drawings.has(path)) {
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect width="24" height="24" rx="3" fill="#292018"/><path d="${path}" fill="none" stroke="#e7c581" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
      drawings.set(path, 'data:image/svg+xml,' + encodeURIComponent(svg));
    }
    return drawings.get(path);
  }

  const icons = new Map();
  function wowIcon(name, size) {
    if (!name || !/^[a-z0-9_-]+$/.test(name)) return Promise.resolve(null);
    if (root.dataset.iconArt === 'drawn') return Promise.resolve(drawnIcon(name));
    if (!STATIC) return Promise.resolve(FULL ? `/icons/${size}/${name}.jpg` : null);
    const path = `data/icon/${size}/${name}.jpg`;
    if (!SEALED) return Promise.resolve(path);
    if (!icons.has(path)) {
      const load = () => window.wowFetchSealed(path)
        .then((bytes) => {
          let s = '';
          for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
          return 'data:image/jpeg;base64,' + btoa(s);  // CSP img-src 'self' data:
        });
      // A page asks for a few hundred icons at once: one dropped connection
      // (a phone changing networks) gets a second try, and a failure isn't
      // kept, or every item with that icon would stay on its "?".
      icons.set(path, load()
        .catch(() => new Promise((r) => setTimeout(r, 500)).then(load))
        .catch(() => { icons.delete(path); return null; }));
    }
    return icons.get(path);
  }
  window.wowIcon = wowIcon;

  // ------------------------------------------------------------ tooltip

  let tip = null;
  let owner = null;
  let pinned = false;
  const narrow = window.matchMedia('(max-width: 640px)');

  function tipEl() {
    if (!tip) {
      tip = el('div', { id: 'wowtip', class: 'wowtip', role: 'tooltip', hidden: true });
      document.body.append(tip);
    }
    return tip;
  }
  function place() {
    if (!tip || tip.hidden || !owner) return;
    tip.classList.toggle('sheet', narrow.matches);
    if (narrow.matches) { tip.style.left = ''; tip.style.top = ''; return; }
    const r = owner.getBoundingClientRect();
    const t = tip.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    const vh = window.innerHeight;
    let left = r.right + 10;
    if (left + t.width > vw - 8) left = r.left - t.width - 10;
    if (left < 8) left = Math.max(8, Math.min(vw - t.width - 8, r.left));
    let top = r.top;
    if (top + t.height > vh - 8) top = Math.max(8, vh - t.height - 8);
    tip.style.left = `${left + window.scrollX}px`;
    tip.style.top = `${top + window.scrollY}px`;
  }
  function close() {
    if (!tip || tip.hidden) return;
    tip.hidden = true;
    if (owner) { owner.removeAttribute('aria-describedby'); owner.setAttribute('aria-expanded', 'false'); }
    owner = null;
    pinned = false;
  }
  async function open(target, build, pin) {
    const t = tipEl();
    owner = target;
    pinned = pin;
    target.setAttribute('aria-describedby', 'wowtip');
    target.setAttribute('aria-expanded', 'true');
    t.replaceChildren(el('p', { class: 'tt-loading', text: '…' }));
    t.hidden = false;
    place();
    let body = null;
    try { body = await build(); } catch (e) { body = null; }
    if (owner !== target) return;  // another one opened meanwhile
    t.replaceChildren(body || el('p', { text: target.getAttribute('aria-label') || '' }));
    if (narrow.matches) {
      t.prepend(el('button', { type: 'button', class: 'tt-close', 'aria-label': 'Close', text: '×' }));
      t.querySelector('.tt-close').addEventListener('click', close);
    }
    place();
  }
  function bind(target, build) {
    target.addEventListener('pointerenter', (e) => {
      // Narrow, the tooltip is a bottom sheet: it opens on a tap only, a
      // hover would cover what the pointer is heading for.
      if (e.pointerType === 'mouse' && !pinned && !narrow.matches) open(target, build, false);
    });
    target.addEventListener('pointerleave', (e) => {
      if (e.pointerType === 'mouse' && !pinned && owner === target) close();
    });
    target.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (owner === target && pinned) close();
      else open(target, build, true);
    });
  }
  document.addEventListener('click', (e) => {
    if (tip && !tip.hidden && !tip.contains(e.target)) close();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && tip && !tip.hidden) {
      const o = owner;
      close();
      if (o) o.focus();
    }
  });
  window.addEventListener('resize', place);
  narrow.addEventListener('change', place);
  window.wowTip = { bind, close };

  // ------------------------------------------------------------ the item tooltip

  // An item no source names (rare: Forever's tables, forever-db and the Era
  // client's all miss it) is called by its slot, never by its id; its
  // tooltip has the Wowhead link.
  function unnamed(item, c) {
    const slot = (item && item.slot_name) || (c && c.s);
    return `${slot ? slot + ' item' : 'An item'}, see on Wowhead`;
  }

  function itemTip(item, c) {
    const q = item.quality !== null && item.quality !== undefined ? item.quality : c && c.q;
    const kids = [el('p', { class: 'tt-name' + (q !== null && q !== undefined ? ` q${q}` : ''),
      // The link's own name first: a random-suffix item ("... of the Elder")
      // is only that in the link, the catalog has the bare name.
      text: item.name || (c && c.n) || unnamed(item, c) })];
    if (!c) {
      if (q !== null && q !== undefined) kids.push(el('p', { text: QUALITY[q] }));
      if (item.item_level) kids.push(el('p', { class: 'tt-ilvl', text: `Item Level ${item.item_level}` }));
      if (item.req_level) kids.push(el('p', { text: `Requires Level ${item.req_level}` }));
      if (item.enchant) kids.push(el('p', { class: 'tt-green', text: `Enchanted: ${item.enchant}` }));
      kids.push(el('p', { class: 'tt-note', text: 'No item data for this one.' }));
      kids.push(el('p', { class: 'tt-link' }, link('item', item.item_id, 'Open on Wowhead')));
      return el('div', { class: 'tt' }, kids);
    }
    if (c.b) kids.push(el('p', { text: c.b }));
    if (c.u) kids.push(el('p', { text: 'Unique' }));
    if (c.s || c.t) kids.push(el('p', { class: 'tt-two' }, el('span', { text: c.s || '' }), el('span', { text: c.t || '' })));
    if (c.dps) {
      kids.push(el('p', { class: 'tt-two' }, el('span', { text: `(${c.dps.toFixed(1)} damage per second)` }),
        el('span', { text: `Speed ${c.sp.toFixed(2)}` })));
    }
    if (c.ar) kids.push(el('p', { text: `${c.ar} Armor` }));
    for (const w of c.w || []) kids.push(el('p', { text: w }));
    if (item.enchant) kids.push(el('p', { class: 'tt-green', text: `Enchanted: ${item.enchant}` }));
    if (c.rl) kids.push(el('p', { text: `Requires Level ${c.rl}` }));
    if (c.il) kids.push(el('p', { class: 'tt-ilvl', text: `Item Level ${c.il}` }));
    for (const g of c.g || []) kids.push(el('p', { class: 'tt-green', text: g }));
    if (c.de) kids.push(el('p', { class: 'tt-flavor', text: `“${c.de}”` }));
    if (c.set) kids.push(el('p', { class: 'tt-set', text: c.set }));
    if (item.extra) for (const x of item.extra) kids.push(el('p', { class: 'tt-note', text: x }));
    kids.push(el('p', { class: 'tt-link' }, link('item', item.item_id, 'Open on Wowhead')));
    return el('div', { class: 'tt' }, kids);
  }

  // A button for one item: its icon (large in a slot, medium in a list) in
  // a dark frame with the quality's border, an enchant mark, and the
  // tooltip. `glyph`: the slot glyph drawn until (or instead of) the icon.
  // `label`: false for an icon alone, else the name next to it; `sub`: a
  // second line under the name (the slot's "Chest, item level 21").
  function chip(item, { size = 'medium', glyph = null, label = true, cls = '', sub = '' } = {}) {
    const id = item && item.item_id;
    const q = item && item.quality !== null && item.quality !== undefined ? Math.max(0, Math.min(5, item.quality)) : null;
    const name = (item && item.name) || (id ? unnamed(item, null) : '');
    const frame = el('span', { class: `ifr ${size}` + (q !== null ? ` q${q}` : '') }, glyph);
    if (item && item.enchant_id > 0) frame.append(el('span', { class: 'ench', title: 'Enchanted', 'aria-hidden': 'true' }));
    const b = el('button', { type: 'button', class: 'itm ' + cls + (q !== null ? ` q${q}` : ''),
      'aria-label': name + (item && item.enchant ? `, enchanted: ${item.enchant}` : ''), 'aria-expanded': 'false' },
    frame, label ? el('span', { class: 'ilabel' },
      el('span', { class: 'iname' + (q !== null ? ` q${q}` : ''), text: name }),
      sub ? el('span', { class: 'imeta', text: sub }) : null) : null);
    if (!id) return b;
    if (!glyph) frame.classList.add('noicon');  // the drawn "?" until the icon comes
    const px = size === 'large' ? 56 : 36;
    const show = (src, fallback) => {
      if (!src) return;
      const img = el('img', { src, alt: '', width: px, height: px, decoding: 'async', loading: 'lazy',
        draggable: 'false' });
      img.addEventListener('load', () => { frame.classList.add('has-icon'); frame.classList.remove('noicon'); });
      img.addEventListener('error', () => {
        img.remove();
        if (fallback) wowIcon(QUESTION, size).then((s2) => show(s2, false));
      });
      frame.querySelectorAll('img').forEach((o) => o.remove());
      frame.prepend(img);
    };
    card(id).then((c) => {
      if (c && q === null && c.q !== undefined) {
        frame.classList.add(`q${c.q}`);
        b.classList.add(`q${c.q}`);
        const n = b.querySelector('.iname');
        if (n) n.classList.add(`q${c.q}`);
      }
      if (c && !(item && item.name)) {
        const n = c.n || unnamed(item, c);
        if (label) b.querySelector('.iname').textContent = n;
        // The doll's BiS label (app.js dollBadges) stays after the name.
        b.setAttribute('aria-label', b.dataset.also ? `${n}, ${b.dataset.also}` : n);
      }
      // No card, or a card without an icon: the game's question mark.
      const icon = (c && c.i) || QUESTION;
      return wowIcon(icon, size).then((src) => show(src, icon !== QUESTION));
    });
    bind(b, () => card(id).then((c) => itemTip(item, c)));
    return b;
  }
  window.wowItem = chip;
})();
