import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";
import fs from "node:fs";
import { BASE, ORIGIN, REPO_URL } from "./scripts/site.mjs";

// Written by scripts/prepare.mjs from its SECTIONS list (the one place that
// orders the site): run prepare first (`npm run build` and `dev` do).
const sidebar = JSON.parse(fs.readFileSync(new URL("./sidebar.json", import.meta.url), "utf8"));
// The version the docs describe (header and footer): the kit's own.
const version = fs.readFileSync(new URL("../pyproject.toml", import.meta.url), "utf8").match(/^version = "([^"]+)"/m)[1];

// A project site on GitHub Pages lives under the repository's name
// (https://<owner>.github.io/<repo>/): every link Starlight writes carries
// the base, and prepare.mjs adds it to the links inside the pages.
export default defineConfig({
  site: ORIGIN,
  base: BASE,
  output: "static",
  trailingSlash: "always",
  vite: { define: { __WOW_VERSION__: JSON.stringify(version) } },
  integrations: [
    starlight({
      title: "wow-stats",
      description: "A levelling log for World of Warcraft: Forever, from the files your game and addons already write.",
      favicon: "/favicon.svg",
      social: [{ icon: "github", label: "GitHub", href: REPO_URL }],
      editLink: { baseUrl: `${REPO_URL}/edit/main/` },
      // The dates come from prepare.mjs (each page's source file in git):
      // Starlight's own would read the generated copies, which git ignores.
      lastUpdated: false,
      // Previous / next follow the sidebar: each section reads as a path.
      pagination: true,
      tableOfContents: { minHeadingLevel: 2, maxHeadingLevel: 3 },
      customCss: ["./src/custom.css"],
      components: {
        Footer: "./src/components/Footer.astro",
        SocialIcons: "./src/components/SocialIcons.astro",
      },
      head: [
        // The pages as Markdown, for an assistant (scripts/prepare.mjs).
        { tag: "link", attrs: { rel: "alternate", type: "text/plain", href: `${BASE}llms.txt`, title: "For AI assistants" } },
        // The link preview: the demo character's Overview (site/scripts/screenshots.py).
        { tag: "meta", attrs: { property: "og:image", content: `${ORIGIN}${BASE}images/social.png` } },
        { tag: "meta", attrs: { property: "og:image:width", content: "1200" } },
        { tag: "meta", attrs: { property: "og:image:height", content: "630" } },
        { tag: "meta", attrs: { property: "og:image:alt", content: "The Overview of a made-up level 34 priest: pace against the route, hours to the next level, next moves" } },
        { tag: "meta", attrs: { name: "twitter:card", content: "summary_large_image" } },
      ],
      sidebar,
    }),
  ],
});
