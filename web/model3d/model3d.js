// The 3D character, full view only: the server never sends this file, or
// anything under /model3d/, to the public view.
//
// Adds a "3D" toggle to the middle of the paperdoll, as the armory does:
// the still (Battle.net's render, or the silhouette) swaps in place for a
// frame on /model3d/view, the page that runs Wowhead's model viewer
// (view.js) under its own, wider CSP, and back. Nothing of Wowhead's loads
// until it's toggled. This page keeps the strict CSP.
//
// The frame is sandboxed (allow-scripts only: an opaque origin, no network):
// Wowhead's script can't read this page, its cookies, the /zogzog/ key or
// call /api/* (pre-publication review M2). This file is its only way out:
// it answers the frame's requests for the files on RELAY's list and for
// the answer to draw, and nothing else (bridge() below).
//
// app.js knows nothing of this file: it fires `wow:gear` on the document
// each time it draws the equipment panel.
'use strict';

(() => {
  const st = { char: null, gear: null, open: false, shown: null, run: 0 };
  const STATIC = document.documentElement.dataset.static === '1';
  // The picture or 3D, as last chosen: kept per site (the live view, the
  // public bundle, /zogzog/) across levels, tabs and reloads.
  const MODE_KEY = 'wow-view-mode:' + (!STATIC ? 'full' : document.documentElement.dataset.sealed === '1' ? 'zogzog' : 'public');
  function mode() {
    try { return localStorage.getItem(MODE_KEY) === '3d' ? '3d' : '2d'; } catch (e) { return '2d'; }
  }
  function setMode(m) {
    try { localStorage.setItem(MODE_KEY, m); } catch (e) { /* private mode: this page only */ }
  }
  // The frame's address. It names the character for nothing but a distinct
  // URL: the answer comes from this page (answerFor), never the frame's own
  // reading, and the /zogzog/ key never goes near it.
  function frameQuery(name, level) {
    const p = new URLSearchParams({ name });
    if (level) p.set('level', level);
    if (!STATIC && sexPick(name)) p.set('sex', sexPick(name));
    if (modelsPick()) p.set('models', modelsPick());  // view.js reads it: Updated or Classic
    return '?' + p.toString();
  }

  // Updated or Classic models: this browser's pick, for every character and
  // site; without one the answer's default (the person's setting) applies.
  const MODELS_KEY = 'wow-model3d-models';
  function modelsPick() {
    try { const v = localStorage.getItem(MODELS_KEY); return v === 'sd' || v === 'hd' ? v : ''; } catch (e) { return ''; }
  }
  function setModelsPick(v) {
    try { localStorage.setItem(MODELS_KEY, v); } catch (e) { /* private mode: this page only */ }
  }
  function frameEl(title, name, level) {
    return h('iframe', { class: 'm3dframe', title, src: 'model3d/view' + frameQuery(name, level),
      sandbox: 'allow-scripts', referrerpolicy: 'no-referrer', 'data-name': name, 'data-level': level || '' });
  }

  // ---------------------------------------------------------------- the frame's only way out

  // Wowhead's files the relay serves (wowstats/model3d.py RELAY, under
  // zam/<ZAM_REV>/) and the vendored jQuery: nothing else is fetched for the
  // frame, whatever it asks. Change both lists together.
  const RELAY = new RegExp('^(?:jquery-3\\.7\\.1\\.min\\.js|zam/r2/(?:viewer/viewer\\.min\\.js'
    + '|meta/(?:character|charactercustomization|item|itemvisual|armor/\\d{1,2})/\\d{1,10}\\.json'
    + '|sd/meta/(?:character|charactercustomization)/\\d{1,10}\\.json'
    + '|textures/\\d{1,10}\\.(?:webp|png)'
    + '|(?<k>m2|skin|anim|skel|phys|bone)/\\d{1,10}(?:-\\d{1,6}){0,2}\\.\\k<k>))$');
  window.wowRelayPath = (p) => typeof p === 'string' && RELAY.test(p);

  // The answer a frame draws: this page's own request (the login's, or the
  // sealed file opened with the key here).
  function answerFor(f) {
    const name = f.dataset.name;
    const level = f.dataset.level || null;
    return window.wowGetJSON(window.wowApi('model3d', STATIC ? { name, level } : { name, level, sex: sexPick(name) }));
  }
  function sendAnswer(f) {
    const to = f.contentWindow;
    answerFor(f).then((answer) => to && to.postMessage({ wowM3d: 'answer', answer }, '*'),
      (e) => to && to.postMessage({ wowM3d: 'answer', error: String(e.message || e) }, '*'));
  }

  window.addEventListener('message', (ev) => {
    const d = ev.data;
    if (ev.origin !== 'null' || !d || typeof d.wowM3d !== 'string') return;
    // Only a frame of ours, still on its sandboxed page (a frame that went
    // elsewhere has that page's origin, not 'null').
    const f = [...document.querySelectorAll('iframe.m3dframe')].find((x) => x.contentWindow === ev.source);
    if (!f) return;
    if (d.wowM3d === 'answer') return sendAnswer(f);
    if (d.wowM3d === 'status') return frameStatus(d);
    if (d.wowM3d !== 'get' || !Number.isInteger(d.id)) return;
    const reply = (status, body, type) => ev.source.postMessage({ wowM3d: 'file', id: d.id, status, body, type }, '*',
      body ? [body] : []);
    if (!window.wowRelayPath(d.path)) return reply(404, new ArrayBuffer(0), '');
    fetch('model3d/' + d.path, { credentials: 'same-origin' })
      .then(async (r) => reply(r.status, await r.arrayBuffer(), r.headers.get('Content-Type') || ''))
      .catch(() => reply(0, null, ''));
  });
  // One stage: the still, or the model on the toggle. `src` gives the
  // frame's character and level, read when it opens.
  function toggle(stage, title, src, onOpen) {
    const still = stage.querySelector('.still');
    let btn = stage.querySelector('.m3dtoggle');
    if (!btn) {
      btn = h('button', { type: 'button', class: 'm3dtoggle', 'aria-pressed': 'false' });
      stage.append(btn);
    }
    const set = (on) => {
      btn.textContent = on ? 'Picture' : 'View in 3D';
      btn.setAttribute('aria-pressed', String(on));
      btn.setAttribute('aria-label', on ? 'Back to the picture' : 'View in 3D');
      stage.classList.toggle('in3d', on);
    };
    btn.onclick = () => {
      const frame = stage.querySelector('.m3dframe');
      setMode(frame ? '2d' : '3d');
      if (frame) {
        frame.remove();  // the viewer goes with it
        if (still) still.hidden = false;
        set(false);
        return;
      }
      if (still) still.hidden = true;
      const at = src();
      stage.insertBefore(frameEl(title, at.name, at.level), btn);
      set(true);
      if (onOpen) onOpen();
    };
    set(Boolean(stage.querySelector('.m3dframe')));
    return btn;
  }

  // The character at a past level (a level snapshot, reconstructed gear).
  // Battle.net renders today's gear only: the publisher draws each past
  // level's picture with this same viewer (wowstats/renderer.py,
  // docs/architecture.md "A past level's picture"), and `picture` says if
  // it's there ({ext}), on its way ({pending}), or not made at all (null: the
  // live view, or no renderer), when the level is the 3D model alone. With
  // one, the stage is the picture with the "View in 3D" toggle, as the
  // paperdoll. `holder` keeps the same stage and frame from level to level:
  // the picture is swapped in the same box and the next level sent to the
  // open frame, which dresses the model again without reloading.
  window.wowPastLevel = (holder, name, level, picture) => {
    const title = `${name.replace(/-.*$/, '')} at level ${level} in 3D`;
    const old = holder.querySelector('.snapstage');
    const same = old && holder.dataset.char === name && old.classList.contains('haspic') === Boolean(picture);
    holder.dataset.char = name;
    const moved = holder.dataset.level !== String(level);
    holder.dataset.level = String(level);
    if (same) {
      const box = old.querySelector('.still');
      if (box) {
        const shown = !box.hidden;
        window.wowLevelStill(box, name, level, picture);
        box.hidden = !shown;
      }
      const frame = old.querySelector('.m3dframe');
      if (frame && moved) {
        frame.title = title;
        frame.dataset.level = String(level);
        sendAnswer(frame);  // the open frame dresses the model again
      }
      return;
    }
    if (!picture) {
      holder.replaceChildren(h('div', { class: 'stage snapstage in3d' }, frameEl(title, name, level)));
      return;
    }
    const box = window.wowLevelStill(document.createElement('div'), name, level, picture);
    const stage = h('div', { class: 'stage snapstage haspic' }, box);
    holder.replaceChildren(stage);
    // The frame opens on the level shown when it's toggled, not this one.
    const btn = toggle(stage, title, () => ({ name, level: holder.dataset.level }), () => {
      const f = stage.querySelector('.m3dframe');
      if (f) f.title = `${name.replace(/-.*$/, '')} at level ${holder.dataset.level} in 3D`;
    });
    if (mode() === '3d') btn.click();
  };

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'text') el.textContent = v;
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (const k of kids.flat()) {
      if (k !== null && k !== undefined && k !== false) el.append(k);
    }
    return el;
  }

  // Under the paperdoll: what the model shows and leaves out.
  function box() {
    const gear = document.getElementById('gear');
    if (!gear) return null;
    let n = gear.querySelector('.m3dinfo');
    if (!n) {
      n = h('div', { class: 'm3dinfo', hidden: true });
      const foot = gear.querySelector('.dollfoot') || gear.querySelector('.doll');
      if (foot) foot.before(n); else gear.append(n);
    }
    return n;
  }

  // The game saves a character's sex only through Syndicator. Without it the
  // page lets you pick, per character, kept in this browser only.
  const sexKey = (c) => `wow-model3d-sex:${c}`;
  function sexPick(c) {
    try { return localStorage.getItem(sexKey(c)) || ''; } catch (e) { return ''; }
  }
  function setSexPick(c, v) {
    try { localStorage.setItem(sexKey(c), v); } catch (e) { /* private mode */ }
  }

  function stage() {
    const gear = document.getElementById('gear');
    return gear && gear.querySelector('.doll > .stage');
  }

  function button() {
    const sg = stage();
    if (!sg || !st.gear) return;  // nothing to dress: no toggle
    toggle(sg, `${(st.char || '').replace(/-.*$/, '')} in 3D`, () => ({ name: st.char, level: null }), () => {
      st.open = true;
      draw();
    });
    // Back to the picture: the notes under the doll go too.
    const btn = sg.querySelector('.m3dtoggle');
    btn.addEventListener('click', () => {
      if (!sg.querySelector('.m3dframe')) close();
    });
    // 3D chosen before (this sheet, another level, another visit): open it.
    if (mode() === '3d' && !sg.querySelector('.m3dframe')) btn.click();
  }

  function close() {
    st.open = false;
    st.shown = null;
    st.run++;
    const sec = box();
    if (sec) { sec.hidden = true; sec.replaceChildren(); }
  }

  async function draw() {
    const run = ++st.run;
    const sec = box();
    if (!sec) return;
    st.shown = `${st.char}|${st.gear && st.gear.at}|${sexPick(st.char)}`;
    sec.hidden = false;
    const status = h('p', { class: 'm3dstatus sub', text: 'Dressing the model' });
    const notes = h('div', { class: 'm3dnotes' });
    sec.replaceChildren(status, notes,
      h('p', { class: 'm3dcredit' }, '3D viewer by ',
        h('a', { href: 'https://www.wowhead.com', target: '_blank', rel: 'noopener noreferrer', text: 'Wowhead' })));
    try {
      const d = await window.wowGetJSON(window.wowApi('model3d', STATIC ? { name: st.char } : { name: st.char, sex: sexPick(st.char) }));
      if (run !== st.run) return;
      if (!d.model) {
        status.textContent = '';  // the frame says why
        return;
      }
      const n = d.items.length;
      status.textContent = `${[d.race, d.sex || 'male'].filter(Boolean).join(' ')}, ${n} item${n === 1 ? '' : 's'} shown. Drag to turn it.`;
      const look = d.appearance;
      const when = look && look.refreshed ? new Date(look.refreshed).toLocaleDateString() : '';
      const lines = [h('p', { text: !look ? 'Default look: not on Battle.net yet and no Narcissus save.'
        : look.source === 'battle.net' ? `Look from Battle.net${when ? `, refreshed ${when}` : ''}.`
          : 'Look saved by Narcissus’s barber shop.' })];
      if (d.stand_in) {
        lines.push(h('p', { text: `Wowhead has no ${d.race} model: shown on the ${d.stand_in} body, which has the same build.` }));
      }
      if (!d.sex_known) {
        const pick = h('select', { 'aria-label': 'Shown as' },
          ['male', 'female'].map((v) => h('option', { value: v, text: v, selected: d.sex === v })));
        pick.addEventListener('change', () => {
          setSexPick(st.char, pick.value);
          const f = stage() && stage().querySelector('.m3dframe');
          if (f) f.src = 'model3d/view' + frameQuery(st.char, null);  // a new frame asks for its answer again
          draw();
        });
        lines.push(h('p', { class: 'm3dsex' }, 'The game didn’t save this character’s sex (Syndicator does). Shown as ',
          pick, '.'));
      }
      if (d.missing.length) {
        // No model to draw: the item's icon instead, with why.
        const chip = (m) => (window.wowItem && m.item_id
          ? window.wowItem({ item_id: m.item_id, name: m.name }, { size: 'medium', cls: 'm3dicon' })
          : h('b', { text: m.name || `item ${m.item_id}` }));
        lines.push(h('p', { text: `Not in 3D, shown as icons (${d.missing.length}):` }),
          h('ul', { class: 'm3dmissing' }, d.missing.map((m) => h('li', {}, chip(m),
            h('span', { class: 'sub', text: ` ${m.slot_name}: ${m.why}` })))));
      }
      notes.replaceChildren(modelsSwitch(d), classicNote(d), ...lines);
    } catch (e) {
      if (run === st.run) status.textContent = '';  // the frame says what went wrong
    }
  }

  // Classic only: the choices the original models don't have (a gnome
  // male's "Genius" hair; wowstats/model3d.py classic_gaps), drawn as the
  // nearest one there. One plain line, hidden under Updated.
  function classicNote(d) {
    const gaps = (d.model_sd && d.model_sd.no_classic || []).map((n) => String(n).toLowerCase()
      .replace(/^hair style$/, 'hairstyle')).filter(Boolean);
    if (!gaps.length) return '';
    const one = gaps.length === 1;
    const what = one ? gaps[0] : `${gaps.slice(0, -1).join(', ')} and ${gaps[gaps.length - 1]}`;
    return h('p', { class: 'm3dclassic', hidden: !classicShown(d),
      text: `This ${what} ${one ? 'doesn’t' : 'don’t'} exist on classic models: switch to Updated to see ${one ? 'it' : 'them'}.` });
  }
  function classicShown(d) {
    return (modelsPick() || d.models || 'hd') === 'sd' && !!d.model_sd;
  }

  // Updated or Classic models, as the game's Character Models option. The
  // pick is this browser's (modelsPick); each open frame reloads on its new
  // address, which names it (view.js reads it there).
  function modelsSwitch(d) {
    const cur = classicShown(d) ? 'sd' : 'hd';
    const tips = {
      hd: 'The updated character models.',
      sd: d.model_sd ? 'The original models, like the game’s Standard Definition option. The face and hair are '
        + 'approximate: the game only saves the updated look.'
        : `Wowhead has no original model for ${d.race || 'this race'}.`,
    };
    const btns = [['hd', 'Updated'], ['sd', 'Classic']].map(([v, label]) => {
      const b = h('button', { type: 'button', class: 'm3dmodel', 'aria-pressed': String(v === cur), title: tips[v],
        disabled: v === 'sd' && !d.model_sd, text: label });
      b.addEventListener('click', () => {
        if (b.getAttribute('aria-pressed') === 'true') return;
        setModelsPick(v);
        btns.forEach((o) => o.setAttribute('aria-pressed', String(o === b)));
        document.querySelectorAll('.m3dclassic').forEach((p) => { p.hidden = v !== 'sd'; });
        document.querySelectorAll('iframe.m3dframe').forEach((f) => {
          f.src = 'model3d/view' + frameQuery(f.dataset.name, f.dataset.level || null);
        });
      });
      return b;
    });
    return h('div', { class: 'm3dmodels', role: 'group', 'aria-label': 'Character models' },
      h('span', { class: 'sub', text: 'Models: ' }), ...btns);
  }

  // The frame reports a failure here too, under the panel (its text is set
  // as text: the frame is someone else's code).
  function frameStatus(msg) {
    const status = box() && box().querySelector('.m3dstatus');
    if (status && !msg.ok) {
      status.className = 'm3dstatus err';
      status.textContent = String(msg.text || 'Couldn’t draw the model.').slice(0, 300);
    }
  }

  document.addEventListener('wow:gear', (ev) => {
    const { char, gear } = ev.detail || {};
    st.char = char;
    st.gear = gear;
    if (!gear) {
      if (st.open) close();
      return;
    }
    // A new paperdoll (app.js keeps the old one while its model is open):
    // a new character or a new sheet, so the notes go with the old model.
    if (st.open) close();
    button();
  });
})();
