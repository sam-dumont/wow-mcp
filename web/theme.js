// The colour theme every page shares: Auto (the system's), Day or Night,
// kept per browser. Loaded in <head> without defer, so the page never
// flashes the other palette; the switch (#theme, in the bar) is wired once
// the page is there. Pages without the switch still follow the choice.
(() => {
  const THEMES = ['system', 'light', 'dark'];
  const root = document.documentElement;
  const label = () => {
    const b = document.getElementById('theme');
    if (!b) return;
    const t = root.dataset.theme || 'system';
    b.textContent = { system: 'Auto', light: 'Day', dark: 'Night' }[t];
    b.setAttribute('aria-label', `Colour theme: ${{ system: 'auto', light: 'day', dark: 'night' }[t]}. Switch`);
  };
  const apply = (t) => {
    if (t === 'system') delete root.dataset.theme;
    else root.dataset.theme = t;
    label();
  };
  let saved = 'system';
  try { saved = localStorage.getItem('wow-theme') || 'system'; } catch (e) { /* private mode */ }
  apply(THEMES.includes(saved) ? saved : 'system');
  document.addEventListener('DOMContentLoaded', () => {
    const b = document.getElementById('theme');
    if (!b) return;
    label();
    b.addEventListener('click', () => {
      const next = THEMES[(THEMES.indexOf(root.dataset.theme || 'system') + 1) % THEMES.length];
      apply(next);
      try { localStorage.setItem('wow-theme', next); } catch (e) { /* private mode */ }
    });
  });
})();
