// The help page's only script (wowstats/mcp/helppage.py). On the static
// site it decrypts the sealed content (nonce + AES-GCM, @AD@ as associated
// data, the same scheme as web/app.js) with the key in the fragment; on the
// full view the content is already there. Then it wires the theme, the
// Mac/Windows tabs and the AI app picker (remembered per viewer), in-page
// links, copy buttons and any clips.
// No key, a wrong key or a tampered file: "Nothing here." and nothing else.
(async () => {
  const main = document.getElementById('help');
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
  };

  // The site's theme switch, same key and cycle as web/app.js.
  const THEMES = ['system', 'light', 'dark'];
  const themeBtn = document.getElementById('theme');
  const applyTheme = (t) => {
    if (t === 'system') delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = t;
    themeBtn.textContent = { system: 'Auto', light: 'Day', dark: 'Night' }[t];
    themeBtn.setAttribute('aria-label', `Colour theme: ${{ system: 'auto', light: 'day', dark: 'night' }[t]}. Switch`);
  };
  const saved = store.get('wow-theme');
  applyTheme(THEMES.includes(saved) ? saved : 'system');
  themeBtn.addEventListener('click', () => {
    const next = THEMES[(THEMES.indexOf(document.documentElement.dataset.theme || 'system') + 1) % THEMES.length];
    applyTheme(next);
    store.set('wow-theme', next);
  });

  const nothing = () => {
    main.replaceChildren(Object.assign(document.createElement('p'), { textContent: 'Nothing here.' }));
  };
  // The full view serves the content as it is (its readers are logged in)
  // and its clips unsealed, from mediaBase: no key, and the fragment is an
  // ordinary #anchor. The static copy is sealed: the fragment is the key.
  const mediaBase = main.dataset.mediaBase;
  const sealed = mediaBase === undefined;
  let k;
  if (sealed) {
    window.addEventListener('hashchange', () => location.reload());
    try {
      const key = location.hash.slice(1);
      if (!key) return nothing();
      const raw = Uint8Array.from(atob(key.replace(/-/g, '+').replace(/_/g, '/')), (c) => c.charCodeAt(0));
      k = await crypto.subtle.importKey('raw', raw, 'AES-GCM', false, ['decrypt']);
      const r = await fetch('content.enc', { credentials: 'same-origin', cache: 'no-cache' });
      if (!r.ok) return nothing();
      const b = new Uint8Array(await r.arrayBuffer());
      const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: b.slice(0, 12),
        additionalData: new TextEncoder().encode('@AD@') }, k, b.slice(12));
      const doc = new DOMParser().parseFromString(new TextDecoder().decode(plain), 'text/html');
      main.replaceChildren(...doc.body.childNodes);
      document.title = 'wow-stats: help';
      document.getElementById('hnav').hidden = false;
    } catch (e) { return nothing(); }
  }

  // Tabs: a [data-sw=name] button per value shows [data-panel="name:value"].
  // Every group with the same name moves together (the two OS switches).
  // The AI app picker is a <select data-sel=client> over the same panels;
  // nothing is shown until one is picked.
  const pick = (name, val, save) => {
    main.querySelectorAll(`[data-sw="${name}"]`).forEach((b) => {
      const on = b.dataset.val === val;
      b.setAttribute('aria-selected', String(on));
      b.tabIndex = on ? 0 : -1;
    });
    main.querySelectorAll(`select[data-sel="${name}"]`).forEach((s) => { s.value = val; });
    main.querySelectorAll('[data-panel]').forEach((p) => {
      const [n, v] = p.dataset.panel.split(':');
      if (n === name) p.hidden = v !== val;
    });
    if (save) store.set(`wow-help-${name}`, val);
  };
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');
  const names = [...new Set([...main.querySelectorAll('[data-sw]')].map((b) => b.dataset.sw))];
  for (const name of names) {
    const vals = [...main.querySelectorAll(`[data-sw="${name}"]`)].map((b) => b.dataset.val);
    const want = store.get(`wow-help-${name}`) || (name === 'os' && isMac ? 'mac' : '');
    pick(name, vals.includes(want) ? want : vals[0], false);
  }
  main.querySelectorAll('select[data-sel]').forEach((s) => {
    const want = store.get(`wow-help-${s.dataset.sel}`) || '';
    pick(s.dataset.sel, [...s.options].some((o) => o.value === want) ? want : '', false);
    s.addEventListener('change', () => pick(s.dataset.sel, s.value, true));
  });
  main.addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-sw]');
    if (b) pick(b.dataset.sw, b.dataset.val, true);
  });
  main.addEventListener('keydown', (ev) => {
    const b = ev.target.closest('[data-sw]');
    if (!b || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(ev.key)) return;
    const row = [...b.parentElement.querySelectorAll('[data-sw]')];
    const i = row.indexOf(b);
    const j = { ArrowLeft: i - 1, ArrowRight: i + 1, Home: 0, End: row.length - 1 }[ev.key];
    const t = row[(j + row.length) % row.length];
    ev.preventDefault();
    pick(t.dataset.sw, t.dataset.val, true);
    t.focus();
  });

  // In-page links. On the static copy the fragment holds the key, so
  // following #x would replace it: jump by script instead, opening the
  // folded parts the target sits in (and the target itself when it is one).
  // On the full view the fragment is the target: /help/#tr-no-data from
  // another page lands on that question, opened.
  const jump = (id, smooth) => {
    const t = id && document.getElementById(id);
    if (!t || !main.contains(t)) return false;
    for (let d = t.closest('details'); d; d = d.parentElement.closest('details')) d.open = true;
    const calm = !smooth || matchMedia('(prefers-reduced-motion: reduce)').matches;
    t.scrollIntoView({ block: 'start', behavior: calm ? 'auto' : 'smooth' });
    if (t.tagName === 'DETAILS') t.querySelector('summary').focus({ preventScroll: true });
    else t.focus({ preventScroll: true });
    return true;
  };
  const hashId = () => { try { return decodeURIComponent(location.hash.slice(1)); } catch (e) { return ''; } };
  document.addEventListener('click', (ev) => {
    const a = ev.target.closest('a[data-jump]');
    if (!a) return;
    ev.preventDefault();
    if (jump(a.dataset.jump, true) && !sealed) history.replaceState(null, '', `#${a.dataset.jump}`);
  });
  if (!sealed) {
    window.addEventListener('hashchange', () => jump(hashId(), true));
    jump(hashId(), false);
  }

  // Copy: the clipboard API, else a selected textarea and execCommand,
  // else the text stays selected for Ctrl+C / Cmd+C.
  const fallbackCopy = (text) => {
    const ta = Object.assign(document.createElement('textarea'), { value: text, readOnly: true });
    ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
    document.body.append(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    ta.remove();
    return ok;
  };
  main.addEventListener('click', async (ev) => {
    const b = ev.target.closest('button[data-copy]');
    if (!b) return;
    let ok = false;
    try { await navigator.clipboard.writeText(b.dataset.copy); ok = true; } catch (e) { ok = fallbackCopy(b.dataset.copy); }
    if (!ok) {
      const code = b.parentElement.querySelector('code, blockquote');
      if (code) getSelection().selectAllChildren(code);
    }
    b.textContent = ok ? 'Copied' : (isMac ? 'Press Cmd+C' : 'Press Ctrl+C');
    clearTimeout(b._t);
    b._t = setTimeout(() => { b.textContent = 'Copy'; }, 1800);
  });

  // Clips: sealed like the content, under their own path. A clip with a
  // dark and a light take shows the one for the page's theme, and swaps
  // when the theme changes.
  const dark = () => (document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: dark)').matches
    ? 'dark' : 'light')) === 'dark';
  const clips = new Map();
  const clip = async (name) => {
    if (!sealed) return mediaBase + encodeURIComponent(name);
    if (!clips.has(name)) {
      clips.set(name, (async () => {
        const r = await fetch(`media/${encodeURIComponent(name)}.enc`, { credentials: 'same-origin' });
        if (!r.ok) throw new Error(String(r.status));
        const b = new Uint8Array(await r.arrayBuffer());
        const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: b.slice(0, 12),
          additionalData: new TextEncoder().encode('@MEDIA@' + name) }, k, b.slice(12));
        const type = { mp4: 'video/mp4', webm: 'video/webm', png: 'image/png', webp: 'image/webp', gif: 'image/gif' }[
          name.split('.').pop()] || 'application/octet-stream';
        return URL.createObjectURL(new Blob([plain], { type }));
      })());
    }
    return clips.get(name);
  };
  const show = async (fig) => {
    const name = fig.dataset.media || (dark() ? fig.dataset.dark : fig.dataset.light);
    if (!name || fig.dataset.shown === name) return;
    try {
      const src = await clip(name);
      const el = fig.dataset.kind === 'video'
        // #t=0.4: past the fade-in, so the still before play shows the title.
        ? Object.assign(document.createElement('video'), { src: src + '#t=0.4', controls: true, muted: true, playsInline: true, preload: 'metadata' })
        : Object.assign(document.createElement('img'), { src, alt: '' });
      fig.replaceChildren(el);
      fig.dataset.shown = name;
    } catch (e) { fig.remove(); }
  };
  // A clip is fetched when its figure comes near the screen: a phone that
  // reads one section doesn't download all of them.
  const figs = [...main.querySelectorAll('figure[data-media], figure[data-dark]')];
  const themed = () => figs.filter((f) => f.dataset.dark && f.dataset.shown && f.isConnected).forEach(show);
  themeBtn.addEventListener('click', themed);
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', themed);
  const near = new IntersectionObserver((entries) => entries.filter((e) => e.isIntersecting).forEach((e) => {
    near.unobserve(e.target);
    show(e.target);
  }), { rootMargin: '600px 0px' });
  figs.forEach((f) => near.observe(f));
})();
