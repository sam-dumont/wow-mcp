// Where the site lives, in one place (astro.config.mjs, prepare.mjs and the
// link check read it). The workflow sets SITE_ORIGIN and SITE_BASE from
// actions/configure-pages; the defaults are the public repository's.
export const REPO = process.env.SITE_REPO ?? "sam-dumont/wow-mcp";
export const REPO_URL = `https://github.com/${REPO}`;
export const ORIGIN = (process.env.SITE_ORIGIN ?? `https://${REPO.split("/")[0]}.github.io`).replace(/\/$/, "");
const base = process.env.SITE_BASE ?? `/${REPO.split("/")[1]}`;
// Always "/x/": leading and trailing slash, or "/" alone.
export const BASE = `/${base.replace(/^\/+|\/+$/g, "")}/`.replace(/^\/\/$/, "/");
