// Page counts on the docs site, the same way as immich-memories' docs: the
// tracker is served by the site itself (public/js/app.js, a pinned Plausible
// build, its source and licence in the header) and only events leave, to the
// maintainer's self-hosted Plausible. Both values come from repository
// variables at build time (pages.yml); with neither set (local builds, forks)
// the pages load no tracker. They are public: anyone reads them in the HTML.
export function analyticsHead(base, env = process.env) {
  const domain = env.DOCS_ANALYTICS_DOMAIN?.trim();
  const endpoint = env.DOCS_ANALYTICS_ENDPOINT?.trim();
  if (!domain && !endpoint) return [];
  if (!domain || !endpoint) {
    throw new Error("Set both DOCS_ANALYTICS_DOMAIN and DOCS_ANALYTICS_ENDPOINT, or leave both unset.");
  }
  return [{
    tag: "script",
    attrs: { defer: true, "data-domain": domain, "data-api": endpoint, src: `${base}js/app.js` },
  }];
}
