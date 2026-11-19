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
//     (docs/private/ is never published: tools/export-public.sh drops it);
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

// The site map, in reading order: each page's address and sidebar place.
// `slug` defaults to the file's name; `label` to the page's H1.
const SECTIONS = [
  { dir: "", label: "Start here", pages: [
    { src: "README.md", route: "index", label: "What it is" },
    { src: "docs/README.md", slug: "manual", label: "The manual" },
    // /demo/ itself is the demo (scripts/build-demo.sh): its page sits beside it.
    { src: "site/pages/demo.md", slug: "try", label: "Try the demo" },
    { src: "docs/self-host/demo.md", slug: "demo-at-home", label: "The demo on your computer" },
    { src: "docs/install.md", slug: "install", label: "Install" },
  ] },
  { dir: "play", label: "Play", pages: [
    { src: "docs/players/getting-started.md", label: "Join a friend's server" },
    { src: "docs/players/your-page.md", label: "Your page, tab by tab" },
    { src: "docs/players/troubleshooting.md", label: "Something's wrong" },
    { src: "docs/players/privacy.md", slug: "privacy", label: "What's shared" },
  ] },
  { dir: "use", label: "Use it", pages: [
    { src: "docs/ai/connect.md", label: "Connect your AI" },
    { src: "docs/ai/asking.md", label: "Ask about your character" },
    { src: "site/pages/mcp-session.md", slug: "mcp-session", label: "An MCP session, recorded" },
    { src: "docs/mcp.md", label: "MCP and the play analysis" },
    { src: "docs/shipper.md", label: "The shipper" },
  ] },
  { dir: "run", label: "Run it", pages: [
    { src: "docs/self-host/production.md", label: "Run it on a server" },
    { src: "docs/self-host/reverse-proxy.md", label: "Reverse proxy" },
    { src: "docs/self-host/oidc.md", label: "The login for your AI" },
    { src: "docs/self-host/sharing.md", label: "Share your page" },
    { src: "docs/self-host/friends.md", label: "Add friends" },
    { src: "docs/self-host/battlenet.md", label: "Battle.net data" },
    { src: "docs/self-host/backup.md", label: "Back up and restore" },
    { src: "docs/self-host/upgrade.md", label: "Upgrade" },
    { src: "docs/self-host/sizing.md", label: "Sizing" },
    { src: "docs/self-host/monitoring.md", label: "Monitor it" },
    { src: "docs/self-host/hardening.md", label: "Harden it" },
    { src: "docs/security.md", label: "Security notes" },
    { src: "docs/self-host/uninstall.md", label: "Uninstall" },
    { src: "docs/deploy.md", label: "Deploying without compose" },
  ] },
  { dir: "reference", label: "Reference", pages: [
    { src: "docs/reference/environment.md", label: "Environment variables" },
    { src: "docs/reference/mcp-tools.md", label: "MCP tools and prompts" },
    { src: "docs/reference/addons.md", label: "Addons" },
    { src: "docs/reference/http-endpoints.md", label: "HTTP endpoints" },
    { src: "docs/contract.md", label: "The shipper's events" },
    { src: "docs/gear-reference.md", label: "Gear upgrades" },
    { src: "docs/route-reference.md", label: "Route times" },
    { src: "docs/forever-reference.md", label: "Forever reference" },
  ] },
  { dir: "explain", label: "How it works", pages: [
    { src: "docs/architecture.md", label: "Architecture" },
    { src: "docs/explanation/privacy.md", slug: "privacy-tiers", label: "Privacy tiers" },
    { src: "docs/kb.md", label: "The knowledge base" },
  ] },
  { dir: "contribute", label: "Contribute", pages: [
    { src: "docs/development.md", label: "Development" },
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

function main() {
  const repo = tracked();
  const placed = new Map(); // src -> { route, label }
  const sidebar = [];
  for (const s of SECTIONS) {
    const items = [];
    for (const p of s.pages) {
      if (!fs.existsSync(path.join(REPO, p.src))) {
        fail(`SECTIONS names ${p.src}, which doesn't exist`);
        continue;
      }
      const slug = p.slug ?? path.basename(p.src, ".md").toLowerCase();
      const route = p.route ?? (s.dir ? `${s.dir}/${slug}` : slug);
      if ([...placed.values()].some((x) => x.route === route)) fail(`two pages at /${route}/`);
      placed.set(p.src, { route, label: p.label });
      items.push({ label: p.label, link: route === "index" ? "/" : `/${route}/` });
    }
    sidebar.push({ label: s.label, items });
  }
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

  const rewrite = (md, resolve) =>
    mapOutsideFences(md, (t) => {
      const links = replaceOutsideCode(t, /(!?)\[((?:[^[\]]|\[[^\]]*\])*)\]\(<?([^)\s>]+)>?(\s+"[^"]*")?\)/g,
        (_, img, label, to, title) => `${img}[${label}](${resolve(to)}${title ?? ""})`);
      return replaceOutsideCode(links, /\b(href|src)="([^"]+)"/g, (_, a, to) => `${a}="${resolve(to)}"`);
    });

  fs.rmSync(OUT, { recursive: true, force: true });
  fs.mkdirSync(OUT, { recursive: true });
  const llms = [];
  for (const [src, { route, label }] of placed) {
    const { title, body } = splitTitle(text.get(src), label);
    const md = rewrite(body, resolver(src)).replaceAll("@BASE@", BASE);
    const meta = {
      title: route === "index" ? "wow-stats" : title,
      editUrl: src.startsWith("site/") ? false : `${REPO_URL}/edit/main/${src}`,
      head: [{ tag: "link", attrs: { rel: "alternate", type: "text/markdown", href: `${BASE}${route}.md` } }],
    };
    if (route === "index") {
      meta.description = "A levelling log for World of Warcraft: Forever.";
      meta.hero = {
        tagline: "Your WoW Forever climb, level by level, from the files your game and addons already write.",
        actions: [
          { text: "See a demo character", link: `${BASE}try/`, variant: "primary" },
          { text: "Install", link: `${BASE}install/`, variant: "minimal" },
        ],
      };
    }
    // JSON is YAML: one key per line, no quoting rules to get wrong.
    const fm = `---\n${Object.entries(meta).map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join("\n")}\n---\n\n`;
    const file = path.join(OUT, `${route}.md`);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, fm + md.trimStart());
    llms.push({ route, title: route === "index" ? "wow-stats" : title, label, md });
  }
  fs.writeFileSync(path.join(SITE, "sidebar.json"), JSON.stringify(sidebar, null, 1));
  writeLlms(llms);

  if (errors.length) {
    console.error(`prepare: ${errors.length} problem(s):\n  ${errors.join("\n  ")}`);
    process.exit(1);
  }
  console.log(`prepared ${placed.size} pages under ${BASE}`);
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
