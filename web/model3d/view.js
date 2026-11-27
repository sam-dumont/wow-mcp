// Draws one character with our viewer (m3d/, M3D.draw), from the recipe
// the server worked out (wowstats/model3d.py, wowstats/character3d.py).
//
// Runs in /model3d/view, a sandboxed frame of the page (`sandbox=
// "allow-scripts"`, no allow-same-origin): its origin is opaque, so it
// can't reach the page around it, its cookies, the /zogzog/ key or /api/*
// (wowstats/model3d.py VIEW_POLICY). The frame has no network at all
// (connect-src 'none'): every file comes from the page around it
// (web/model3d/model3d.js) over postMessage, which fetches only
// model3d/file/<id>. Its answer comes the same way, already opened in
// /zogzog/.
//
// The renderer (wowstats/renderer.py, ?render=1) loads this page on its own
// in a headless browser that holds nothing: there it reads its files and
// its answer itself (connect-src 'self'), and says when it's done in
// <html data-state>.
'use strict';

(() => {
  const qs = new URLSearchParams(location.search);
  const RENDER = qs.get('render') === '1' && window.parent === window;
  const status = document.getElementById('status');
  const canvas = document.getElementById('stage');
  const actions = document.getElementById('actions');
  if (RENDER) document.documentElement.classList.add('render');
  const realFetch = window.fetch.bind(window);

  // ---------------------------------------------------------------- the page around it

  // Messages go to the parent with '*': an opaque origin can't name it, and
  // none carries anything secret (a status, a file id). Replies are taken
  // from the parent window only.
  const tell = (msg) => { if (!RENDER) window.parent.postMessage({ wowM3d: 'status', ...msg }, '*'); };
  let seq = 0;
  const waiting = new Map();
  let onAnswer = null;
  window.addEventListener('message', (ev) => {
    if (RENDER || ev.source !== window.parent || !ev.data || typeof ev.data.wowM3d !== 'string') return;
    const d = ev.data;
    if (d.wowM3d === 'file' && waiting.has(d.id)) {
      waiting.get(d.id)(d);
      waiting.delete(d.id);
    } else if (d.wowM3d === 'answer' && onAnswer) {
      onAnswer(d);
    }
  });

  // One file of the person's own (model3d/file/<id>): its bytes, or null
  // (the viewer then draws without it).
  function get(id) {
    const path = 'file/' + Number(id);
    if (RENDER) return realFetch(path).then((r) => (r.ok ? r.arrayBuffer() : null), () => null);
    return new Promise((resolve) => {
      const n = ++seq;
      waiting.set(n, (r) => resolve(r.status === 200 && r.body instanceof ArrayBuffer ? r.body : null));
      window.parent.postMessage({ wowM3d: 'get', id: n, path }, '*');
    });
  }

  // The answer to draw (wowstats.model3d.Model3D.character).
  function answer() {
    if (RENDER) {
      // The renderer's page server holds one drawing per name.
      return realFetch('../data/model3d/' + encodeURIComponent(qs.get('name') || '') + '.json')
        .then((r) => { if (!r.ok) throw new Error(`${r.status} ${r.statusText}`); return r.json(); });
    }
    return new Promise((resolve, reject) => {
      onAnswer = (d) => (d.error ? reject(new Error(d.error)) : resolve(d.answer));
      window.parent.postMessage({ wowM3d: 'answer' }, '*');
    });
  }

  // ---------------------------------------------------------------- drawing

  function fail(text) {
    drawing++;
    actions.hidden = true;
    status.hidden = false;
    companionControl.hidden = true;
    mountControl.hidden = true;
    mountNote.textContent = '';
    status.className = 'status err';
    status.textContent = text;
    document.documentElement.dataset.state = 'error';
    tell({ ok: false, text });
  }

  // Updated or Classic: the visitor's pick (the frame's address, set by the
  // page's toggle), else the default the answer carries, else Updated. The
  // renderer draws Updated.
  function recipeOf(d) {
    const want = qs.get('models') || d.models || 'hd';
    return !RENDER && want === 'sd' && d.recipe_sd ? d.recipe_sd : d.recipe;
  }

  // A frame with no size yet: opened inside a hidden tab (3D remembered, the
  // full view opening on Home). Wait until the tab is shown: the frame then
  // gets its size and a resize event.
  function sized() {
    if (window.innerWidth > 0 && window.innerHeight > 0) return Promise.resolve();
    return new Promise((resolve) => {
      const on = () => {
        if (window.innerWidth > 0 && window.innerHeight > 0) {
          window.removeEventListener('resize', on);
          resolve();
        }
      };
      window.addEventListener('resize', on);
    });
  }

  const why = (e) => String((e && e.message) || e || 'unknown error');
  let handle = null;
  const companionControl = document.getElementById('companion-control');
  const companionToggle = document.getElementById('companion');
  companionToggle.addEventListener('change', () => handle?.showCompanion(companionToggle.checked));
  const mountControl = document.getElementById('mount-control');
  const mountPick = document.getElementById('mount');
  const mountNote = document.getElementById('mount-note');
  let drawing = 0;
  mountPick.addEventListener('change', async () => {
    const generation = ++drawing;
    const index = Number(mountPick.value);
    const name = mountPick.selectedOptions[0].textContent;
    mountNote.textContent = index < 0 ? '' : 'Loading mount…';
    const changed = await handle.ride(index);
    if (generation !== drawing) return;
    mountPick.value = String(handle.mounted);
    mountNote.textContent = changed ? '' : `Couldn’t ride ${name} with the available game files.`;
    showActions(generation);
  });
  actions.addEventListener('click', async (event) => {
    const button = event.target, name = button.dataset.action;
    if (name && handle && await handle.play(name) && button.hasAttribute('aria-pressed')) {
      for (const item of actions.children) item.setAttribute('aria-pressed', String(item === button));
    }
  });

  async function showActions(generation) {
    const moves = await handle.actions();
    if (generation !== drawing) return;
    actions.replaceChildren(...moves.map(({ name, label, pressed }) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.action = name;
      button.textContent = label;
      if (pressed !== undefined) button.setAttribute('aria-pressed', String(pressed));
      return button;
    }));
    actions.hidden = !moves.length;
  }

  async function show(d) {
    const generation = ++drawing;
    actions.hidden = true;
    companionControl.hidden = true;
    mountControl.hidden = true;
    mountNote.textContent = '';
    const recipe = d && recipeOf(d);
    if (!recipe) return fail(`No model: ${(d && d.why) || 'nothing to draw'}.`);
    await sized();
    status.className = 'status';
    status.textContent = 'Dressing the model';
    status.hidden = false;
    delete document.documentElement.dataset.state;
    // Another level's gear: the same frame and viewer, a new recipe.
    if (handle) await handle.redraw(recipe);
    // Now and then another animation (m3d/draw.js Idle); never in the
    // renderer's pictures, which stay on Stand.
    else handle = await M3D.draw(canvas, recipe, get, {
      idle: !RENDER, effects: RENDER ? null : document.getElementById('effects')
    });
    companionControl.hidden = RENDER || !handle.hasCompanion;
    if (!companionControl.hidden) {
      companionControl.querySelector('span').textContent = recipe.companion.kind === 2 ? 'Show demon' : 'Show pet';
    }
    if (!RENDER && recipe.mounts?.length) {
      mountPick.replaceChildren(new Option('On foot', '-1'), ...recipe.mounts.map((m, i) => new Option(m.name, String(i))));
      mountControl.hidden = false;
    }
    status.hidden = true;
    document.documentElement.dataset.state = 'drawn';
    tell({ ok: true, missing: handle.missing.length });
    if (!RENDER) showActions(generation);
  }

  // The first answer, then any other the page sends (the level-by-level
  // page's next level), drawn one after the other.
  (async () => {
    let queue = Promise.resolve();
    try {
      const first = await answer();
      if (!RENDER) {
        onAnswer = (d) => {
          queue = queue.then(() => (d.error ? fail(`Couldn’t draw the model: ${d.error}.`) : show(d.answer)))
            .catch((e) => fail(`Couldn’t draw the model: ${why(e)}.`));
        };
      }
      queue = queue.then(() => show(first));
      await queue;
    } catch (e) {
      fail(`Couldn’t draw the model: ${why(e)}.`);
    }
  })();
})();
