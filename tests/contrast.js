// WCAG AA contrast of every visible text node under the elements matched by
// a selector (tests/test_contrast_browser.py). The background is worked out
// by walking up the tree: each ancestor's background colour, composited over
// the ones below until an opaque one; a linear gradient counts with each of
// its stops, the worst one wins. Image layers (the grain, the parchment
// fibre) and radial glows are faint overlays and are not counted. Colours go through a 1x1 canvas,
// so whatever the engine computes (color-mix, color(srgb ...)) is read the
// same way.
(selector) => {
  const cv = document.createElement('canvas');
  cv.width = 1; cv.height = 1;
  const ctx = cv.getContext('2d', { willReadFrequently: true });
  const cache = new Map();
  const rgba = (s) => {
    if (cache.has(s)) return cache.get(s);
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = 'rgba(0, 0, 0, 0)';
    ctx.fillStyle = s;
    ctx.fillRect(0, 0, 1, 1);
    const d = ctx.getImageData(0, 0, 1, 1).data;
    const c = [d[0], d[1], d[2], d[3] / 255];
    cache.set(s, c);
    return c;
  };
  const over = (top, under) => {
    const a = top[3];
    return [0, 1, 2].map((i) => top[i] * a + under[i] * (1 - a)).concat(1);
  };
  const lum = (c) => {
    const [r, g, b] = c.slice(0, 3).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  // A linear gradient's stops; a radial one is a glow or a vignette at the
  // edges, drawn like the image layers.
  const stops = (img) => (img.match(/linear-gradient\((?:[^()]|\([^()]*\))*\)/g) || [])
    .flatMap((g) => g.match(/(rgba?|color)\([^()]*\)/g) || []);

  // Every opaque background the element can sit on (several for a gradient).
  const backgrounds = (el) => {
    const layers = [];
    for (let e = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e);
      const bg = rgba(cs.backgroundColor);
      const grad = stops(cs.backgroundImage).map(rgba);
      if (grad.length) layers.push(grad.map((g) => (bg[3] >= 0.999 ? over(g, bg) : g)));
      else if (bg[3] > 0) layers.push([bg]);
      if (layers.length && layers[layers.length - 1].every((c) => c[3] >= 0.999)) break;
    }
    let base = [[255, 255, 255, 1]];  // the canvas
    for (const layer of layers.reverse()) base = layer.flatMap((c) => base.map((b) => over(c, b)));
    return base;
  };

  const out = [];
  const seen = new Set();
  for (const root of document.querySelectorAll(selector)) {
    const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = walk.nextNode(); n; n = walk.nextNode()) {
      const text = n.textContent.replace(/\s+/g, ' ').trim();
      const el = n.parentElement;
      if (!text || !el || seen.has(el) || el.closest('svg, option, [disabled], [aria-hidden="true"]')) continue;
      if (!el.checkVisibility({ opacityProperty: true, visibilityProperty: true })) continue;
      const range = document.createRange();
      range.selectNodeContents(n);
      const box = range.getBoundingClientRect();
      if (box.width < 2 || box.height < 2) continue;  // visually hidden (.sr, .vh)
      seen.add(el);
      const cs = getComputedStyle(el);
      let fg = rgba(cs.color);
      let op = 1;
      for (let e = el; e; e = e.parentElement) op *= parseFloat(getComputedStyle(e).opacity);
      fg = [fg[0], fg[1], fg[2], fg[3] * op];
      const size = parseFloat(cs.fontSize);
      const large = size >= 24 || (size >= 18.66 && parseInt(cs.fontWeight, 10) >= 700);
      const need = large ? 3 : 4.5;
      const worst = Math.min(...backgrounds(el).map((bg) => ratio(over(fg, bg), bg)));
      if (worst < need) {
        const path = [];
        for (let e = el; e && e !== root.parentElement && path.length < 4; e = e.parentElement) {
          path.unshift(e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (e.classList.length ? '.' + [...e.classList].join('.') : ''));
        }
        out.push({ text: text.slice(0, 60), ratio: Math.round(worst * 100) / 100, need, color: cs.color, where: path.join(' > ') });
      }
    }
  }
  return { checked: seen.size, fails: out };
}
