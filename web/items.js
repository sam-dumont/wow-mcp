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
  async function loadCards(ids) {
    if (STATIC) {
      if (!bundleCards) {
        bundleCards = window.wowGetJSON(window.wowApi('items'))
          .then((d) => { for (const c of d.items || []) cards.set(c.id, c); })
          .catch(() => {});
      }
      return bundleCards;
    }
    for (let k = 0; k < ids.length; k += 200) {
      try {
        const d = await window.wowGetJSON('/api/items?ids=' + ids.slice(k, k + 200).join(','));
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
      setTimeout(() => {
        const batch = queue;
        queue = [];
        loadCards(batch.map(([i]) => i)).finally(() => batch.forEach(([i, res]) => res(cards.get(i) || null)));
      }, 0);
    }
    return p;
  }
  window.wowCard = card;

  // ------------------------------------------------------------ icons

  const icons = new Map();
  function wowIcon(name, size) {
    if (!name || !/^[a-z0-9_-]+$/.test(name)) return Promise.resolve(null);
    if (!STATIC) return Promise.resolve(FULL ? `/icons/${size}/${name}.jpg` : null);
    const path = `data/icon/${size}/${name}.jpg`;
    if (!SEALED) return Promise.resolve(path);
    if (!icons.has(path)) {
      icons.set(path, window.wowFetchSealed(path)
        .then((bytes) => {
          let s = '';
          for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
          return 'data:image/jpeg;base64,' + btoa(s);  // CSP img-src 'self' data:
        })
        .catch(() => null));
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
        b.setAttribute('aria-label', n);
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
