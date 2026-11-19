// Every link of the built site (dist/) that stays on the site points at a
// file the build wrote, and every #anchor at an id of its page. Run after
// `astro build` (npm run build does). The demo's own files (dist/demo/) are
// the product's page, checked by its own tests: only links *to* them count.
//
// DEMO=skip: a docs-only build (no scripts/build-demo.sh run) accepts the
// links into /demo/ without checking them.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { BASE } from "./site.mjs";

const DIST = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../dist");
const skipDemo = process.env.DEMO === "skip";

function walk(dir, rel = "") {
  return fs.readdirSync(path.join(dir, rel), { withFileTypes: true }).flatMap((d) => {
    const r = path.posix.join(rel, d.name);
    return d.isDirectory() ? walk(dir, r) : [r];
  });
}

const files = walk(DIST);
const have = new Set(files);
const ids = new Map();
const idsOf = (file) => {
  if (!ids.has(file)) ids.set(file, new Set([...fs.readFileSync(path.join(DIST, file), "utf8").matchAll(/\bid="([^"]+)"/g)].map((m) => m[1])));
  return ids.get(file);
};

const bad = [];
const pages = files.filter((f) => f.endsWith(".html") && !f.startsWith("demo/"));
for (const page of pages) {
  const html = fs.readFileSync(path.join(DIST, page), "utf8");
  for (const m of html.matchAll(/\b(?:href|src)="([^"]+)"/g)) {
    const raw = m[1].replace(/&amp;/g, "&");
    if (/^([a-z][a-z0-9+.-]*:|\/\/|data:)/i.test(raw)) continue;
    const [p, frag] = raw.split("#");
    let target;
    if (p === "") target = page;
    else {
      const abs = p.startsWith("/") ? p : path.posix.join("/" + path.posix.dirname(page), p);
      if (!abs.startsWith(BASE)) { bad.push(`${page}: ${raw} is outside the site's base ${BASE}`); continue; }
      const rel = decodeURI(abs.slice(BASE.length).split("?")[0]);
      if (skipDemo && rel.startsWith("demo/")) continue;
      target = rel === "" || rel.endsWith("/") ? `${rel}index.html` : rel;
      if (!have.has(target)) { bad.push(`${page}: ${raw} -> no ${target}`); continue; }
    }
    if (frag && target.endsWith(".html") && !target.startsWith("demo/") && !idsOf(target).has(decodeURIComponent(frag))) {
      bad.push(`${page}: ${raw} -> ${target} has no #${frag}`);
    }
  }
}
if (bad.length) {
  console.error(`check-links: ${bad.length} broken link(s):\n  ${[...new Set(bad)].join("\n  ")}`);
  process.exit(1);
}
console.log(`check-links: ${pages.length} pages, every link resolves`);
