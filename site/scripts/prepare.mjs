// Turns the repository's own Markdown (README.md, docs/*.md) into Starlight
// pages, so the site never holds a second copy of the text: edit the
// Markdown, and the next build shows it. The few pages that exist only for
// the site (the demo's page, the recorded MCP session) live in site/pages/.
//
// Output (gitignored, rebuilt on every run):
//   src/content/docs/   the pages, with Starlight's frontmatter
//   sidebar.json        the sidebar, from SECTIONS (astro.config.mjs reads it)
//   public/llms.txt, public/llms-full.txt, public/**/*.md
//                       the pages as Markdown for AI assistants (llmstxt.org)
//
// It fails the build (exit 1) on:
//   - a Markdown file under docs/ or site/pages/ that SECTIONS doesn't place
//     (docs/private/, if one exists, is never published);
//   - a page SECTIONS names that doesn't exist;
//   - a link to a repository path that doesn't exist, or to an anchor that
//     isn't a heading of its page.
// scripts/check-links.mjs then checks every link of the built site.

import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import GithubSlugger from "github-slugger";
import { BASE, ORIGIN, REPO_URL } from "./site.mjs";

const SITE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPO = path.resolve(SITE, "..");
const OUT = path.join(SITE, "src/content/docs");
const PUB = path.join(SITE, "public");

// The site map, by reader and in reading order: each section opens with its
// tutorial, then the how-tos, then what to look up. `slug` defaults to the
// file's name, `label` to the page's H1; `group` nests a sub-list in the
// sidebar; `hidden` keeps a page off it (the home page, the 404); `meta`
// adds Starlight frontmatter (the home page's hero).
const SECTIONS = [
  { dir: "", label: "Start here", pages: [
    { src: "site/pages/index.md", route: "index", label: "Home", hidden: true, meta: {
      template: "splash",
      description: "wow-stats turns the files WoW Forever and your addons already write into a levelling log: your pace, your next upgrade and where it drops, the dungeon worth running, and an AI assistant that reads it all.",
      hero: {
        tagline: "A levelling log for WoW Forever: your pace against the route, your next upgrade and where it drops, the dungeon worth running. Made from the files your game and addons already write.",
        // The tour (docs/videos/media, made by demo/), muted; it plays by
        // itself unless the reader asked for less motion (Footer.astro).
        image: { html: ["light", "dark"].map((t) =>
          `<video class="${t}-only" data-autoplay src="@BASE@videos/tour-${t}.mp4" poster="@BASE@images/overview${t === "dark" ? "-dark" : ""}.webp" width="1280" height="800" muted loop playsinline controls preload="none" aria-label="A one-minute tour of the demo priest's page: the Overview, his gear and upgrades, the dungeons, the climb and the journal"></video>`,
        ).join("") + `<p>A one-minute tour of Nibbet, a made-up priest. <a href="@BASE@demo/">Open his pages</a>.</p>` },
        actions: [
          { text: "How it works", link: "@BASE@how-it-works/", variant: "primary", icon: "right-arrow" },
          { text: "See a live demo", link: "@BASE@demo/", variant: "minimal", icon: "external" },
        ],
      },
    } },
    { src: "docs/explanation/how-it-works.md", slug: "how-it-works", label: "How it works" },
    { src: "docs/faq.md", slug: "faq", label: "Questions and answers" },
    { src: "docs/glossary.md", slug: "glossary", label: "Glossary" },
    { src: "site/pages/demo.md", slug: "try", label: "The live demo" },
    { src: "docs/README.md", slug: "manual", label: "Every page" },
    { src: "site/pages/404.md", route: "404", label: "Not found", hidden: true, meta: { template: "splash", pagefind: false } },
  ] },
  { dir: "play", label: "Play", pages: [
    { src: "docs/players/getting-started.md", label: "Join a friend's server" },
    { src: "docs/players/your-page.md", label: "Your page at a glance" },
    { src: "docs/players/overview.md", label: "Overview and next moves" },
    { src: "docs/players/gear.md", label: "Gear and upgrades" },
    { src: "docs/players/meta.md", label: "Your class's meta" },
    { src: "docs/players/dungeons.md", label: "Dungeons" },
    { src: "docs/players/plan.md", label: "Plan your next hours" },
    { src: "docs/players/history.md", label: "Climb, timeline, journal" },
    { src: "docs/players/troubleshooting.md", label: "Something's wrong" },
    { src: "docs/players/privacy.md", slug: "privacy", label: "What's shared" },
  ] },
  { dir: "ai", label: "Connect your AI", pages: [
    { src: "docs/ai/connect.md", label: "Connect your AI" },
    { src: "docs/ai/asking.md", label: "Ask about your character" },
    { src: "site/pages/mcp-session.md", slug: "session", label: "A session, recorded" },
    { src: "docs/reference/mcp-tools.md", slug: "tools", label: "Tools and prompts" },
    { src: "docs/reference/semantic-layer.md", slug: "semantic-layer", label: "Semantic layer" },
  ] },
  { dir: "self-host", label: "Self-host", pages: [
    { src: "docs/self-host/demo.md", slug: "try-at-home", label: "Try it at home" },
    { src: "docs/install.md", slug: "install", label: "Install it for real" },
    { group: "Set it up", pages: [
      { src: "docs/self-host/production.md", label: "Run it on a server" },
      { src: "docs/self-host/reverse-proxy.md", label: "Reverse proxy and HTTPS" },
      { src: "docs/self-host/sharing.md", label: "Share your page" },
      { src: "docs/self-host/oidc.md", label: "The login for your AI" },
      { src: "docs/self-host/friends.md", label: "Add friends" },
      { src: "docs/self-host/battlenet.md", label: "Battle.net data" },
      { src: "docs/self-host/server-analysis.md", label: "Server-side analysis" },
      { src: "docs/self-host/admin.md", label: "Admin overview" },
      { src: "docs/self-host/how-i-run-it.md", label: "How I run it" },
      { src: "docs/deploy.md", label: "Without Docker Compose" },
    ] },
    { group: "Keep it running", pages: [
      { src: "docs/self-host/troubleshooting.md", label: "When it stops" },
      { src: "docs/self-host/backup.md", label: "Back up and restore" },
      { src: "docs/self-host/upgrade.md", label: "Upgrade" },
      { src: "docs/self-host/monitoring.md", label: "Monitor it" },
      { src: "docs/self-host/hardening.md", label: "Harden it" },
      { src: "docs/self-host/uninstall.md", label: "Uninstall" },
    ] },
    { group: "Reference", pages: [
      { src: "docs/reference/environment.md", label: "Environment variables" },
      { src: "docs/reference/http-endpoints.md", label: "HTTP endpoints" },
      { src: "docs/self-host/sizing.md", label: "Sizing" },
      { src: "docs/shipper.md", label: "The shipper" },
      { src: "docs/contract.md", label: "The shipper's events" },
    ] },
  ] },
  { dir: "game-data", label: "Game data", pages: [
    { src: "docs/reference/addons.md", label: "Addons it reads" },
    { src: "docs/gear-reference.md", label: "Gear upgrades" },
    { src: "docs/route-reference.md", label: "Route times" },
    { src: "docs/forever-reference.md", label: "Forever reference" },
    { src: "docs/kb.md", label: "The knowledge base" },
  ] },
  { dir: "explain", label: "Under the hood", pages: [
    { src: "docs/architecture.md", label: "Architecture" },
    { src: "docs/mcp.md", label: "MCP and the play analysis" },
    { src: "docs/explanation/privacy.md", slug: "privacy-tiers", label: "Privacy tiers" },
    { src: "docs/explanation/data-sources.md", slug: "data-sources", label: "Where the data comes from" },
    { src: "docs/guarantees.md", label: "What the pipeline guarantees" },
    { src: "docs/security.md", label: "Security notes" },
  ] },
  { dir: "project", label: "Project", pages: [
    { src: "CHANGELOG.md", slug: "changelog", label: "Changelog", meta: { description: "What changed in each version of wow-stats, and what to do when you upgrade." } },
    { src: "SECURITY.md", slug: "security", label: "Security policy", meta: { description: "How to report a security problem in wow-stats privately, which versions get fixes, and how to check what you run." } },
  ] },
  { dir: "contribute", label: "Contributing", pages: [
    { src: "CONTRIBUTING.md", slug: "contributing", label: "How to contribute", meta: { description: "How to propose a change to wow-stats: issues, pull requests, and the checks a change has to pass." } },
    { src: "docs/development.md", label: "Development and releases" },
    { src: "docs/evaluation.md", label: "Evaluating the analysis" },
  ] },
];

// Markdown that must be placed: everything under these, but what never ships.
const MUST_PLACE = [/^docs\/.+\.md$/i, /^site\/pages\/.+\.md$/i];
const NEVER = [/^docs\/private\//];

const errors = [];
const fail = (msg) => errors.push(msg);

function tracked() {
  // The repository's files as git sees them (untracked ones too, minus the
  // ignored): a link may only point at something that will be on GitHub.
  const out = execFileSync("git", ["ls-files", "--cached", "--others", "--exclude-standard", "-z"], { cwd: REPO });
  const files = out.toString("utf8").split("\0").filter(Boolean).filter((f) => fs.existsSync(path.join(REPO, f)));
  const dirs = new Set();
  for (const f of files) for (let d = path.posix.dirname(f); d !== "."; d = path.posix.dirname(d)) dirs.add(d);
  return { files: new Set(files), dirs };
}

const routeUrl = (route) => (route === "index" ? BASE : `${BASE}${route}/`);

function mapOutsideFences(md, fn) {
  return md
    .split(/(^ {0,3}(?:```|~~~)[\s\S]*?^ {0,3}(?:```|~~~)[ \t]*$)/m)
    .map((part, i) => (i % 2 ? part : fn(part)))
    .join("");
}

// Inline code spans keep their text: a path in backticks is not a link. A
// match counts as code when it starts inside a span (a link whose label is
// code, [`x`](y), starts outside one).
function replaceOutsideCode(text, re, fn) {
  const spans = [...text.matchAll(/(`+)[\s\S]*?\1/g)].map((m) => [m.index, m.index + m[0].length]);
  return text.replace(re, (...a) => {
    const at = a[a.length - 2];
    return spans.some(([s, e]) => at > s && at < e) ? a[0] : fn(...a);
  });
}

function splitTitle(md, fallback) {
  const m = md.match(/^# +(.+?)\s*$/m);
  if (!m || md.slice(0, m.index).trim() !== "") return { title: fallback, body: md };
  return { title: m[1].replace(/`/g, ""), body: md.slice(m.index + m[0].length) };
}

// The anchors a page has: its headings, slugged as GitHub and Starlight both
// do (github-slugger), so a link written for GitHub works on the site.
function anchors(md) {
  const slugger = new GithubSlugger();
  const out = new Set();
  mapOutsideFences(md, (t) => {
    for (const m of t.matchAll(/^ {0,3}#{1,6} +(.+?)\s*#*\s*$/gm)) {
      const text = m[1]
        .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
        .replace(/<[^>]+>/g, "")
        // Emphasis markers go; an underscore inside a word (WOW_STATE_DIR) stays, as on GitHub.
        .replace(/[`*]/g, "")
        .replace(/(^|[^\w])_+|_+(?=[^\w]|$)/g, "$1");
      out.add(slugger.slug(text));
    }
    for (const m of t.matchAll(/\bid="([^"]+)"/g)) out.add(m[1]);
    return t;
  });
  return out;
}

function lastCommit(src) {
  return execFileSync("git", ["log", "-1", "--format=%cs", "--", src], { cwd: REPO }).toString().trim();
}

// "For: players with a page. At the end: you know X." becomes "For players
// with a page: you know X.", the page's description for search and previews.
function describe(body) {
  const paras = mapOutsideFences(body, (t) => t).replace(/<!--[\s\S]*?-->/g, "").split(/\n\s*\n/);
  const pick = paras.find((p) => /\*\*For:\*\*/.test(p)) ?? paras.find((p) => /^[A-Za-z]/.test(p.trim()));
  if (!pick) return undefined;
  const plain = pick.replace(/\.?\s*\*\*At the\s+end:\*\*\s*/, ": ").replace(/\*\*For:\*\*\s*/, "For ")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1").replace(/[*`_<>]/g, "").replace(/\s+/g, " ").trim();
  return plain.length > 220 ? `${plain.slice(0, 217).replace(/\s+\S*$/, "")}...` : plain;
}

function main() {
  const repo = tracked();
  const placed = new Map(); // src -> { route, label }
  const sidebar = [];
  const place = (s, list) => {
    const items = [];
    for (const p of list) {
      if (p.group) {
        items.push({ label: p.group, items: place(s, p.pages) });
        continue;
      }
      if (!fs.existsSync(path.join(REPO, p.src))) {
        fail(`SECTIONS names ${p.src}, which doesn't exist`);
        continue;
      }
      const slug = p.slug ?? path.basename(p.src, ".md").toLowerCase();
      const route = p.route ?? (s.dir ? `${s.dir}/${slug}` : slug);
      if ([...placed.values()].some((x) => x.route === route)) fail(`two pages at /${route}/`);
      placed.set(p.src, { route, label: p.label, meta: p.meta, section: s });
      if (!p.hidden) items.push({ label: p.label, link: route === "index" ? "/" : `/${route}/` });
    }
    return items;
  };
  for (const s of SECTIONS) sidebar.push({ label: s.label, items: place(s, s.pages) });
  for (const f of repo.files) {
    if (MUST_PLACE.some((r) => r.test(f)) && !NEVER.some((r) => r.test(f)) && !placed.has(f)) {
      fail(`${f} isn't on the site: add it to SECTIONS in site/scripts/prepare.mjs`);
    }
  }

  const text = new Map();
  const ids = new Map();
  for (const src of placed.keys()) {
    const raw = fs.readFileSync(path.join(REPO, src), "utf8").replace(/^---\n[\s\S]*?\n---\n/, "");
    text.set(src, raw);
    ids.set(src, anchors(raw));
  }

  // Where a link in `src` goes: a page by its route, any other file of the
  // repository on GitHub. Anything else is an error.
  const resolver = (src) => (target) => {
    if (/^[a-z][a-z0-9+.-]*:/i.test(target)) return target; // https:, mailto:
    if (target.startsWith("@BASE@")) return target.replace("@BASE@", BASE);
    const [p, anchor] = target.split("#");
    const hash = anchor ? `#${anchor}` : "";
    if (p === "") {
      if (anchor && !ids.get(src).has(anchor)) fail(`${src}: no heading for #${anchor}`);
      return target;
    }
    if (p.startsWith("/")) {
      fail(`${src}: "${target}" is an absolute path: link to the file (docs/x.md) or write it as code`);
      return target;
    }
    const dir = path.posix.dirname(src);
    const rel = path.posix.normalize(path.posix.join(dir === "." ? "" : dir, decodeURI(p))).replace(/\/$/, "");
    if (placed.has(rel)) {
      if (anchor && !ids.get(rel).has(anchor)) fail(`${src}: ${rel} has no heading for #${anchor}`);
      return routeUrl(placed.get(rel).route) + hash;
    }
    if (rel.startsWith("..") || NEVER.some((r) => r.test(rel))) {
      fail(`${src}: "${target}" points outside what is published`);
      return target;
    }
    if (repo.files.has(rel)) return `${REPO_URL}/blob/main/${rel}${hash}`;
    if (repo.dirs.has(rel) || rel === "") return `${REPO_URL}/tree/main/${rel}`;
    fail(`${src}: broken link "${target}" (no ${rel} in the repository)`);
    return target;
  };

  // An image of the repository (docs/images/) is served by the site itself,
  // at /images/<name>: a GitHub blob page is no image.
  const image = (src, resolve) => (target) => {
    const dir = path.posix.dirname(src);
    const rel = path.posix.normalize(path.posix.join(dir === "." ? "" : dir, decodeURI(target)));
    if (!/^[a-z][a-z0-9+.-]*:/i.test(target) && rel.startsWith("docs/images/") && repo.files.has(rel)) {
      const name = rel.slice("docs/images/".length);
      fs.mkdirSync(path.join(PUB, "images"), { recursive: true });
      fs.copyFileSync(path.join(REPO, rel), path.join(PUB, "images", name));
      return `${BASE}images/${name}`;
    }
    return resolve(target);
  };
  const rewrite = (md, src, resolve) =>
    mapOutsideFences(md, (t) => {
      const img = image(src, resolve);
      const links = replaceOutsideCode(t, /(!?)\[((?:[^[\]]|\[[^\]]*\])*)\]\(<?([^)\s>]+)>?(\s+"[^"]*")?\)/g,
        (_, bang, label, to, title) => `${bang}[${label}](${img(to)}${title ?? ""})`);
      // A link to a picture (docs/images/) opens the site's copy too.
      return replaceOutsideCode(links, /\b(href|src|srcset)="([^"]+)"/g, (_, a, to) => `${a}="${img(to)}"`);
    });

  fs.rmSync(OUT, { recursive: true, force: true });
  fs.rmSync(path.join(PUB, "images"), { recursive: true, force: true });
  fs.rmSync(path.join(PUB, "videos"), { recursive: true, force: true });
  fs.mkdirSync(OUT, { recursive: true });
  const llms = [];
  for (const [src, { route, label, meta: extra }] of placed) {
    const { title, body } = splitTitle(text.get(src), label);
    const md = rewrite(body, src, resolver(src)).replaceAll("@BASE@", BASE);
    const meta = {
      title: route === "index" ? "wow-stats" : title,
      editUrl: src.startsWith("site/") ? false : `${REPO_URL}/edit/main/${src}`,
      head: route === "404" ? [] : [{ tag: "link", attrs: { rel: "alternate", type: "text/markdown", href: `${BASE}${route}.md` } }],
      ...JSON.parse(JSON.stringify(extra ?? {}).replaceAll("@BASE@", BASE)),
    };
    // A description per page (search results, link previews): the page's
    // first paragraph after its "For:" line, as plain text.
    meta.description ??= describe(body);
    // JSON is YAML: one key per line, no quoting rules to get wrong.
    // lastUpdated: the source's last commit (a YAML date, unquoted, so
    // Starlight reads a date); a file not committed yet has none.
    const updated = src.startsWith("site/") ? "" : lastCommit(src);
    const fm = `---\n${Object.entries(meta).map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join("\n")}${updated ? `\nlastUpdated: ${updated}` : ""}\n---\n\n`;
    const file = path.join(OUT, `${route}.md`);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, fm + forSite(md).trimStart());
    if (route !== "404") llms.push({ route, title: route === "index" ? "wow-stats" : title, label, md });
  }
  // The home page's hero (SECTIONS): the tour and its posters.
  for (const f of ["docs/videos/media/tour-light.mp4", "docs/videos/media/tour-dark.mp4"]) publish(f, "videos");
  for (const f of ["docs/images/overview.webp", "docs/images/overview-dark.webp"]) publish(f, "images");
  // The link preview every page names (astro.config.mjs): no page shows it.
  const social = path.join(REPO, "docs/images/social.png");
  if (fs.existsSync(social)) {
    fs.mkdirSync(path.join(PUB, "images"), { recursive: true });
    fs.copyFileSync(social, path.join(PUB, "images/social.png"));
  } else fail("docs/images/social.png is missing: the link preview (site/scripts/screenshots.py)");
  fs.writeFileSync(path.join(SITE, "sidebar.json"), JSON.stringify(sidebar, null, 1));
  writeLlms(llms);

  if (errors.length) {
    console.error(`prepare: ${errors.length} problem(s):\n  ${errors.join("\n  ")}`);
    process.exit(1);
  }
  console.log(`prepared ${placed.size} pages under ${BASE}`);
}

// What only the site shows differently from GitHub (the Markdown for an
// assistant keeps the plain form):
//   - a screenshot docs/images/x.webp with an x-dark.webp next to it shows
//     the one for the reader's theme; every screenshot opens full size;
//   - a paragraph that is only a link to docs/videos/media/x-light.mp4 is
//     the clip itself, light or dark with the theme (on GitHub the link
//     opens the file, which plays there);
//   - a ```mermaid block is drawn in the browser (src/components/Footer.astro).
const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const reEsc = (s) => s.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");

function publish(rel, dir) {
  const name = path.posix.basename(rel);
  fs.mkdirSync(path.join(PUB, dir), { recursive: true });
  fs.copyFileSync(path.join(REPO, rel), path.join(PUB, dir, name));
  return `${BASE}${dir}/${name}`;
}

function forSite(md) {
  const mermaid = md.replace(/^```mermaid[ \t]*\n([\s\S]*?)^```[ \t]*$/gm,
    (_, code) => `<pre class="mermaid not-content">${esc(code.trimEnd())}</pre>`);
  const clip = new RegExp(`^\\[([^\\]\\n]+)\\]\\(${reEsc(REPO_URL)}/blob/main/(docs/videos/media/[\\w.-]+)-light\\.mp4\\)[ \\t]*$`, "gm");
  return mapOutsideFences(mermaid, (t) => {
    const clips = t.replace(clip, (all, label, stem) => {
      const light = `${stem}-light.mp4`, dark = `${stem}-dark.mp4`;
      const one = (rel, cls) =>
        // #t=1.5: the still before play is a frame from the clip, not its
        // first one, which is black where the clip fades in.
        `<video class="${cls}" src="${publish(rel, "videos")}#t=1.5" controls muted playsinline preload="metadata" aria-label="${esc(label)}"></video>`;
      const both = fs.existsSync(path.join(REPO, dark)) ? one(light, "light-only") + one(dark, "dark-only") : one(light, "");
      return `<figure class="clip">${both}<figcaption>${esc(label)}</figcaption></figure>`;
    });
    const prefix = `${BASE}images/`;
    return replaceOutsideCode(clips, /!\[([^\]]*)\]\(([^)\s]+)\)/g, (all, alt, to) => {
      if (!to.startsWith(prefix) || /\.svg$/i.test(to)) return all;
      const file = to.slice(prefix.length);
      const darkRel = `docs/images/${file.replace(/(\.\w+)$/, "-dark$1")}`;
      const zoom = (href, cls, a) =>
        `<a class="zoom${cls ? ` ${cls}` : ""}" href="${href}" title="Open the full-size picture"><img src="${href}" alt="${esc(a)}" loading="lazy"></a>`;
      if (!fs.existsSync(path.join(REPO, darkRel))) return zoom(to, "", alt);
      return zoom(to, "light-only", alt) + zoom(publish(darkRel, "images"), "dark-only", alt);
    });
  });
}

// The pages as Markdown for an assistant: each at its address plus .md,
// listed in /llms.txt, all of them in /llms-full.txt. Links made absolute.
function writeLlms(pages) {
  fs.mkdirSync(PUB, { recursive: true });
  for (const f of ["llms.txt", "llms-full.txt"]) fs.rmSync(path.join(PUB, f), { force: true });
  for (const p of pages) fs.rmSync(path.join(PUB, `${p.route}.md`), { force: true });
  const abs = (md) =>
    mapOutsideFences(md, (t) =>
      t.replace(/\]\((\/[^)\s]*)\)/g, (_, u) => `](${ORIGIN}${u})`).replace(/\b(href|src)="(\/[^"]*)"/g, (_, a, u) => `${a}="${ORIGIN}${u}"`),
    );
  const url = (p) => `${ORIGIN}${BASE}${p.route}.md`;
  const full = [];
  for (const p of pages) {
    const md = `# ${p.title}\n\n${abs(p.md).trim()}\n`;
    const file = path.join(PUB, `${p.route}.md`);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, md);
    full.push(`<!-- ${ORIGIN}${routeUrl(p.route)} -->\n\n${md}`);
  }
  const bySection = SECTIONS.map((s) => {
    const mine = pages.filter((p) => (s.dir ? p.route.startsWith(`${s.dir}/`) : !p.route.includes("/")));
    return `## ${s.label}\n\n${mine.map((p) => `- [${p.label}](${url(p)})`).join("\n")}`;
  }).join("\n\n");
  fs.writeFileSync(path.join(PUB, "llms.txt"), `# wow-stats

> A levelling log for World of Warcraft: Forever. A small program on the PC you play on reads what the game and your addons already save, and a web page turns it into your climb: time per level, dungeons, gear upgrades, a journal. An MCP server lets your own AI assistant read it.

Every page is also available as Markdown at its address plus \`.md\` (the links below). All of them in one file: ${ORIGIN}${BASE}llms-full.txt

${bySection}
`);
  fs.writeFileSync(path.join(PUB, "llms-full.txt"), full.join("\n\n"));
}

main();
