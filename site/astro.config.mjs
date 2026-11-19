import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";
import fs from "node:fs";
import { BASE, ORIGIN, REPO_URL } from "./scripts/site.mjs";

// Written by scripts/prepare.mjs from its SECTIONS list (the one place that
// orders the site): run prepare first (`npm run build` and `dev` do).
const sidebar = JSON.parse(fs.readFileSync(new URL("./sidebar.json", import.meta.url), "utf8"));

// A project site on GitHub Pages lives under the repository's name
// (https://<owner>.github.io/<repo>/): every link Starlight writes carries
// the base, and prepare.mjs adds it to the links inside the pages.
export default defineConfig({
  site: ORIGIN,
  base: BASE,
  output: "static",
  trailingSlash: "always",
  integrations: [
    starlight({
      title: "wow-stats",
      description: "A levelling log for World of Warcraft: Forever, from the files your game and addons already write.",
      social: [{ icon: "github", label: "GitHub", href: REPO_URL }],
      editLink: { baseUrl: `${REPO_URL}/edit/main/` },
      lastUpdated: false,
      pagination: false,
      tableOfContents: { minHeadingLevel: 2, maxHeadingLevel: 3 },
      customCss: ["./src/custom.css"],
      head: [
        // The pages as Markdown, for an assistant (scripts/prepare.mjs).
        { tag: "link", attrs: { rel: "alternate", type: "text/plain", href: `${BASE}llms.txt`, title: "For AI assistants" } },
      ],
      sidebar,
    }),
  ],
});
