// The 3D character, full view only: the server never sends this file, or
// anything under /model3d/, to the public view.
//
// Adds a "3D" toggle to the middle of the paperdoll, as the armory does:
// the still (our picture of this same model, or the silhouette) swaps in place for a
// frame on /model3d/view, the page that runs our viewer (view.js, m3d/)
// under its own CSP, and back. When the answer has nothing to draw (the
// player's game files haven't come yet), the still stays, with why in one
// line; with no answer at all (404), no toggle. This page keeps the strict
// CSP.
//
// The frame is sandboxed (allow-scripts only: an opaque origin, no network):
// it can't read this page, its cookies, the /zogzog/ key or call /api/*
// (pre-publication review M2). This file is its only way out: it answers
// the frame's requests for model3d/file/<id> and for the answer to draw,
// and nothing else (the message listener below).
//
// app.js knows nothing of this file: it fires `wow:gear` on the document
// each time it draws the equipment panel.
'use strict';

(() => {
  const st = { char: null, gear: null, open: false, shown: null, run: 0 };
  let ids = 0;  // the why lines' ids and the toggles' answers (askFirst)
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

  // The person's game files by id (wowstats/model3d.py FILE): nothing else
  // is fetched for the frame, whatever it asks.
  const FILE = /^file\/\d{1,10}$/;
  window.wowM3dPath = (p) => typeof p === 'string' && FILE.test(p);

  // The answer for a character (and level): this page's own request (the
  // login's, or the sealed file opened with the key here), asked once per
  // address while the page lives.
  const answers = new Map();
  function answerOf(name, level) {
    const url = window.wowApi('model3d', STATIC ? { name, level } : { name, level, sex: sexPick(name) });
    if (!answers.has(url)) {
      answers.set(url, window.wowGetJSON(url).catch((e) => { answers.delete(url); throw e; }));
      setTimeout(() => answers.delete(url), 60000);  // the live view's files arrive: ask again later
    }
    return answers.get(url);
  }
  function answerFor(f) {
    return answerOf(f.dataset.name, f.dataset.level || null);
  }
  function sendAnswer(f) {
    const to = f.contentWindow;
    answerFor(f).then((answer) => to && to.postMessage({ wowM3d: 'answer', answer }, '*'),
      (e) => to && to.postMessage({ wowM3d: 'answer', error: missing(e) ? 'there’s none for this character' : 'its file didn’t load' }, '*'));
  }

  // The answer's file isn't there (404): a bundle published without the 3D,
  // or a level it has no model for. That's no model at all, so no line and
  // no toggle: a button that can only fail isn't offered. Any other failure
  // (network, 5xx) is one plain line; a status code never reaches the page
  // (it did: "No 3D model yet: 404 Not Found.", 2026-11-22).
  const missing = (e) => Boolean(e) && e.status === 404;
  const FAILED = 'The 3D model didn’t load: reload the page to try again.';
  const noModel = (why) => `No 3D model yet: ${why}.`;
  // What the stage shows instead of a model: { d } with a recipe to draw,
  // else { line } (the sentence on the stage) or { gone } (nothing at all).
  function outcome(name, level) {
    return answerOf(name, level).then((d) => (d && d.recipe ? { d } : { line: noModel((d && d.why) || 'nothing to draw') }),
      (e) => (missing(e) ? { gone: true } : { line: FAILED }));
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
    if (!window.wowM3dPath(d.path)) return reply(404, new ArrayBuffer(0), '');
    fetch('model3d/' + d.path, { credentials: 'same-origin' })
      .then(async (r) => reply(r.status, await r.arrayBuffer(), r.headers.get('Content-Type') || ''))
      .catch(() => reply(0, null, ''));
  });
  // One line on the stage saying why there's no 3D model (a whole
  // sentence: noModel or FAILED), or none.
  function whyLine(stage, text) {
    let p = stage.querySelector('.m3dwhy');
    if (!text) { if (p) p.remove(); return; }
    if (!p) {
      p = h('p', { class: 'm3dwhy', id: 'm3dwhy-' + (++ids) });
      stage.append(p);
    }
    p.textContent = text;
    return p;
  }

  // Before any click: with nothing to draw, the toggle is off and says why
  // (the line above it), rather than a button that opens nothing.
  function askFirst(stage, btn, src) {
    const at = src();
    if (!at || !at.name) return;
    const ask = ++ids;
    btn.dataset.ask = String(ask);
    outcome(at.name, at.level).then((o) => {
      if (btn.dataset.ask !== String(ask) || stage.querySelector('.m3dframe')) return;
      offer(stage, btn, o);
    });
  }

  // The toggle for an outcome: on with a model, off with the line as its
  // reason, gone with nothing to draw at all.
  function offer(stage, btn, o) {
    const line = whyLine(stage, o.line || '');
    btn.hidden = Boolean(o.gone);
    btn.disabled = !o.d;
    if (line) btn.setAttribute('aria-describedby', line.id);
    else btn.removeAttribute('aria-describedby');
  }

  // One stage: the still, or the model on the toggle. `src` gives the
  // frame's character and level, read when it opens. The answer comes
  // first: with nothing to draw, the still stays and says why.
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
    btn.onclick = async (ev) => {
      const frame = stage.querySelector('.m3dframe');
      // A click the visitor made changes the remembered choice; the page
      // opening 3D by itself (mode() already '3d') doesn't.
      if (ev && ev.isTrusted !== false) setMode(frame ? '2d' : '3d');
      if (frame) {
        frame.remove();  // the viewer goes with it
        if (still) still.hidden = false;
        set(false);
        return;
      }
      const at = src();
      const o = await outcome(at.name, at.level);
      if (stage.querySelector('.m3dframe')) return;  // a second click while asking
      if (!o.d) {
        if (still) still.hidden = false;
        set(false);
        offer(stage, btn, o);
        return;
      }
      whyLine(stage, '');
      if (still) still.hidden = true;
      stage.insertBefore(frameEl(title, at.name, at.level), btn);
      set(true);
      if (onOpen) onOpen();
    };
    set(Boolean(stage.querySelector('.m3dframe')));
    btn.disabled = false;
    btn.hidden = false;
    if (!stage.querySelector('.m3dframe')) askFirst(stage, btn, src);
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
      // No picture service: the level is its 3D model alone, or, with
      // nothing to draw, the silhouette and why.
      const stage = h('div', { class: 'stage snapstage in3d' }, frameEl(title, name, level));
      holder.replaceChildren(stage);
      outcome(name, level).then((o) => {
        if (o.d || holder.dataset.level !== String(level) || !stage.isConnected) return;
        const box = window.wowLevelStill(document.createElement('div'), name, level, null);
        box.querySelectorAll('.stillnote').forEach((n) => n.remove());
        stage.classList.remove('in3d');
        stage.replaceChildren(box);
        whyLine(stage, o.line || '');
      });
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
    sec.replaceChildren(status, notes);
    try {
      const d = await answerOf(st.char, null);
      if (run !== st.run) return;
      if (!d.recipe) {
        status.textContent = '';  // the stage says why
        return;
      }
      const n = d.items.length;
      status.textContent = `${[d.race, d.sex || 'male'].filter(Boolean).join(' ')}, ${n} item${n === 1 ? '' : 's'} shown. Drag to turn it.`;
      const look = d.appearance;
      const when = look && look.refreshed ? new Date(look.refreshed).toLocaleDateString() : '';
      const lines = [h('p', { text: look ? (when ? `Look as of ${when}.` : '')
        : 'Default look: Battle.net doesn’t know this character yet.' })];
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
  // male's "Genius" hair; wowstats/character3d.py classic_gaps), drawn as
  // the nearest one there. One plain line, hidden under Updated.
  function classicNote(d) {
    const gaps = (d.no_classic || []).map((n) => String(n).toLowerCase()
      .replace(/^hair style$/, 'hairstyle')).filter(Boolean);
    if (!gaps.length) return '';
    const one = gaps.length === 1;
    const what = one ? gaps[0] : `${gaps.slice(0, -1).join(', ')} and ${gaps[gaps.length - 1]}`;
    return h('p', { class: 'm3dclassic', hidden: !classicShown(d),
      text: `This ${what} ${one ? 'doesn’t' : 'don’t'} exist on classic models: switch to Updated to see ${one ? 'it' : 'them'}.` });
  }
  function classicShown(d) {
    return (modelsPick() || d.models || 'hd') === 'sd' && !!d.recipe_sd;
  }

  // Updated or Classic models, as the game's Character Models option. The
  // pick is this browser's (modelsPick); each open frame reloads on its new
  // address, which names it (view.js reads it there).
  function modelsSwitch(d) {
    const cur = classicShown(d) ? 'sd' : 'hd';
    const tips = {
      hd: 'The updated character models.',
      sd: d.recipe_sd ? 'The original models, like the game’s Standard Definition option. The face and hair are '
        + 'approximate: the game only saves the updated look.'
        : `No original model for ${d.race || 'this race'} in the game files yet.`,
    };
    const btns = [['hd', 'Updated'], ['sd', 'Classic']].map(([v, label]) => {
      const b = h('button', { type: 'button', class: 'm3dmodel', 'aria-pressed': String(v === cur), title: tips[v],
        disabled: v === 'sd' && !d.recipe_sd, text: label });
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
  // as text: whatever the frame says stays text).
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
