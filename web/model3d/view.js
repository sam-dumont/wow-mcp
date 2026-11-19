// Draws one character with Wowhead's model viewer (ZamModelViewer, the one
// behind wowhead.com's dressing room and github.com/Miorey/wow-model-viewer).
//
// Runs in /model3d/view, a sandboxed frame of the page (`sandbox=
// "allow-scripts"`, no allow-same-origin): its origin is opaque, so the
// viewer, a third party's script, can't reach the page around it, its
// cookies, the /zogzog/ key or /api/* (wowstats/model3d.py VIEW_POLICY).
// The frame has no network at all (connect-src 'none'): every file comes
// from the page around it (web/model3d/model3d.js) over postMessage, which
// fetches only the relay's allow-listed paths. Its answer (the model, the
// look, the items) comes the same way, already opened in /zogzog/.
//
// jQuery 3.7.1 (vendored next to this file; the viewer is built on it) and
// the viewer arrive as text and are evaluated here, the one place whose
// policy allows it.
//
// The renderer (wowstats/renderer.py, ?render=1) loads this page on its own
// in a headless browser that holds nothing: there it reads its files and
// its answer itself (connect-src 'self').
'use strict';

(() => {
  // The revision is wowstats/model3d.py ZAM_REV: a new one is a new URL for
  // every file, so a browser never mixes files from two (v0.2.0's classic
  // metas with v0.2.1's live ones drew a broken head). Change both together.
  const ZAM = 'zam/r2/';
  const JQUERY = 'jquery-3.7.1.min.js';
  const qs = new URLSearchParams(location.search);
  const RENDER = qs.get('render') === '1' && window.parent === window;
  const status = document.getElementById('status');
  if (RENDER) document.documentElement.classList.add('render');
  const realFetch = window.fetch.bind(window);
  const decode = (buf) => new TextDecoder().decode(buf);

  // ---------------------------------------------------------------- the page around it

  // Messages go to the parent with '*': an opaque origin can't name it, and
  // none carries anything secret (a status, a file path). Replies are taken
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

  // One file under model3d/: {status, body (ArrayBuffer), type}.
  function get(path) {
    if (RENDER) {
      return realFetch(path).then(async (r) => (
        { status: r.status, body: await r.arrayBuffer(), type: r.headers.get('Content-Type') || '' }));
    }
    return new Promise((resolve) => {
      const id = ++seq;
      waiting.set(id, resolve);
      window.parent.postMessage({ wowM3d: 'get', id, path }, '*');
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

  // ---------------------------------------------------------------- the viewer's requests

  // The viewer asks for its files with fetch() and XMLHttpRequest under its
  // contentPath (ZAM). In the frame both go to the page around it instead;
  // anything else is left to the policy, which refuses it.
  const relPath = (url) => {
    let u;
    try { u = new URL(String(url), location.href); } catch (e) { return null; }
    const i = u.pathname.indexOf('/model3d/' + ZAM);
    return i < 0 ? null : u.pathname.slice(i + '/model3d/'.length);
  };

  if (!RENDER) {
    window.fetch = (input, init) => {
      const rel = relPath(typeof input === 'string' ? input : input && input.url);
      if (rel === null) return realFetch(input, init);
      return get(rel).then((r) => {
        if (!r.status) throw new TypeError('Failed to fetch');
        return new Response(r.body, { status: r.status, headers: { 'Content-Type': r.type || 'application/octet-stream' } });
      });
    };

    // What jQuery's transport and the viewer's own binary one use of an XHR.
    class BridgeXHR {
      constructor() {
        this.readyState = 0; this.status = 0; this.statusText = ''; this.response = null;
        this.responseText = ''; this.responseType = ''; this.responseURL = ''; this.timeout = 0;
        this.withCredentials = false; this.upload = { addEventListener() {}, removeEventListener() {} };
        this._on = {}; this._type = ''; this._url = ''; this._aborted = false;
      }
      open(method, url) { this._url = String(url); this.readyState = 1; }
      setRequestHeader() {}
      overrideMimeType() {}
      getResponseHeader(n) { return String(n).toLowerCase() === 'content-type' ? this._type || null : null; }
      getAllResponseHeaders() { return this._type ? 'content-type: ' + this._type + '\r\n' : ''; }
      addEventListener(t, f) { (this._on[t] = this._on[t] || []).push(f); }
      removeEventListener(t, f) { this._on[t] = (this._on[t] || []).filter((g) => g !== f); }
      abort() { this._aborted = true; this.readyState = 4; this._fire('abort'); this._fire('loadend'); }
      _fire(type, n) {
        const ev = { type, target: this, currentTarget: this, lengthComputable: n !== undefined, loaded: n || 0, total: n || 0 };
        if (typeof this['on' + type] === 'function') this['on' + type](ev);
        for (const f of this._on[type] || []) f.call(this, ev);
      }
      send() {
        const rel = relPath(this._url);
        const fail = () => {
          if (this._aborted) return;
          this.readyState = 4; this.status = 0;
          this._fire('readystatechange'); this._fire('error'); this._fire('loadend');
        };
        if (rel === null) { setTimeout(fail, 0); return; }
        get(rel).then((r) => {
          if (this._aborted) return;
          if (!r.status) return fail();
          this.status = r.status;
          this.statusText = r.status === 200 ? 'OK' : 'Not Found';
          this.responseURL = this._url;
          this._type = r.type;
          const t = this.responseType;
          if (t === 'arraybuffer') this.response = r.body;
          else if (t === 'blob') this.response = new Blob([r.body], { type: r.type });
          else if (t === 'json') { try { this.response = JSON.parse(decode(r.body)); } catch (e) { this.response = null; } }
          else { this.responseText = decode(r.body); this.response = this.responseText; }
          this.readyState = 4;
          this._fire('progress', r.body.byteLength);
          this._fire('readystatechange'); this._fire('load'); this._fire('loadend');
        }, fail);
      }
    }
    window.XMLHttpRequest = BridgeXHR;
  }

  // ---------------------------------------------------------------- drawing

  function fail(text) {
    status.hidden = false;
    status.className = 'status err';
    status.textContent = text;
    tell({ ok: false, text });
  }

  async function run(path) {
    const r = await get(path);
    if (r.status !== 200) throw new Error('the viewer didn’t load');
    (0, eval)(decode(r.body));  // global scope; this frame's policy allows it, the page's never does
  }

  let ready = null;
  function libs() {
    ready = ready || (async () => {
      await run(JQUERY);
      // The viewer adds Wowhead's stylesheet to the page (one rule, copied
      // into view.css). The CSP would refuse it: don't ask.
      const append = window.jQuery.fn.append;
      window.jQuery.fn.append = function (...a) {
        if (typeof a[0] === 'string' && a[0].includes('modelviewer/viewer/viewer.css')) return this;
        return append.apply(this, a);
      };
      // What the viewer expects from wowhead.com's own scripts.
      window.WH = { debug() {}, defaultAnimation: 'Stand', WebP: { getImageExtension: () => '.webp' } };
      await run(ZAM + 'viewer/viewer.min.js');
      if (typeof window.ZamModelViewer !== 'function') throw new Error('the viewer didn’t start');
    })();
    return ready;
  }

  function draw(d) {
    const stage = document.getElementById('stage');
    if (window.viewer) {
      // Another level's gear: the same frame and scripts, a new model.
      try { window.viewer.destroy(); } catch (e) { /* Wowhead's, not an API */ }
      window.jQuery(stage).empty();
    }
    window.viewer = new window.ZamModelViewer({
        type: 2,
        contentPath: ZAM,
        container: window.jQuery(stage),
        aspect: window.innerWidth / window.innerHeight,
        hd: false,
        dataEnv: 'classic',
        env: 'classic',
        gameDataEnv: 'classic',
        models: { id: d.model.chr_model, type: 16 },
        items: d.items.map((i) => [i.inventory_type, i.display_id]),
        charCustomization: { options: d.model.options },
    });
  }

  // The viewer fits the model's bounds, then starts zoomed in a step past
  // that (its zoom.current is 1): in a phone's narrow frame the hair and the
  // shoulders touched the edges and the feet were cut. A narrow frame starts
  // a step out instead. Guarded: the viewer is Wowhead's, its fields aren't
  // an API, and without them it simply keeps its own framing.
  function frame() {
    if (window.innerWidth >= 560) return;
    let tries = 0;
    const set = () => {
      const r = window.viewer && window.viewer.renderer;
      if (r && r.zoom && typeof r.zoom.target === 'number') {
        r.zoom.target = -1.5;
        return;
      }
      if (++tries < 50) setTimeout(set, 100);
    };
    set();
  }

  async function show(d) {
    if (!d || !d.model) return fail(`No model: ${(d && d.why) || 'nothing to draw'}.`);
    await libs();
    status.hidden = true;
    draw(d);
    frame();
    tell({ ok: true });
  }

  // The first answer, then any other the page sends (the level-by-level
  // page's next level: the same frame and scripts, the model dressed again).
  (async () => {
    try {
      libs().catch(() => {});  // start loading while the answer comes
      const first = await answer();
      // From here on, every answer the page sends is drawn; one that comes
      // while the first is still loading is drawn after it (show waits on
      // the same libs() promise, in order).
      if (!RENDER) {
        onAnswer = (d) => {
          if (d.error) return fail(`Couldn’t draw the model: ${d.error}.`);
          show(d.answer).catch((e) => fail(`Couldn’t draw the model: ${e.message}.`));
        };
      }
      await show(first);
    } catch (e) {
      fail(`Couldn’t draw the model: ${e.message}.`);
    }
  })();
})();
