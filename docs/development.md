# Development

**For:** contributors. **At the end:** you can set up the project, run every check, and cut a release.

Everything needed to work on wow-stats and cut a release. The command and the
Python package are `wow-stats`; the images are `wow-mcp` and `wow-mcp-render`.

## Set up

Python goes through [uv](https://docs.astral.sh/uv/) only.

```sh
uv sync --frozen --extra publish --extra mcp --extra analysis --extra analysis-openai --group browser
uv run playwright install chromium webkit
```

The `browser` group and the two browsers are for the browser tests
(`tests/test_browser.py`, `tests/test_advice_browser.py`). Without them those
tests skip. CI doesn't install them: run them locally when you change a page,
and the release runs the whole browser suite before it publishes anything.

## What to run before pushing

```sh
make test-changed           # only the tests your diff can break
```

`tools/test_changed.py` diffs against the merge-base with `origin/main` and maps
each changed path to test files (the `RULES` table at its top): the knowledge
base runs the KB tests, `shipper/` the shipper tests, `wowstats/<module>.py` the
module's tests and every test that reaches it through its imports (followed
transitively, lazy imports inside a function included: a page test imports `views`,
which imports `analysis.pages`, which imports the semantic layer), a `dev/` script
the tests that name it, `web/` and the page code the browser
tests. A path no rule knows, or `conftest.py`, `pyproject.toml` and `uv.lock`,
runs the whole non-browser suite. `--list` shows the selection without running it.

Browser tests (files named `*browser*`, marked `browser`) are the slow part and
run only when a rule selects them (`pyproject.toml` keeps them out of a plain
`pytest` too). The whole browser suite: `make test-browser`; the release
runs it too (below). The whole fast suite, in parallel: `make test`. Tests marked
`ci_only` (docker compose, the Polestar comparison, Windows) run in CI and at
release: `-m ci_only`. Tests run in parallel with pytest-xdist, so none may lean
on another or on a fixed port or path: servers bind port 0, state lives in `tmp_path`.

## The checks

```sh
uv run ruff check .
make test                   # the fast suite on half the cores; no browsers, nothing CI-only
docker login dhi.io         # once: the main image's base needs a free Docker Hub account
docker build -t wow-stats:dev .
docker build --target renderer -t wow-stats-render:dev .
```

What skips without what:

| Tests | Need |
|---|---|
| browser tests | the `browser` group and its browsers |
| `tests/test_compose.py` | `docker compose` |
| `tests/test_windows_e2e.py` | Windows (CI runs it on `windows-latest`) |
| `tests/test_gear_parity.py` | `POLESTAR_CHECKOUT`: a Polestar clone at the pinned tag ([docs/gear-reference.md](gear-reference.md)) |
| `tests/test_public_scan.py`, the name check | the denylist file (below) |

Optional: `pre-commit install` runs ruff, the lock and fixture checks,
actionlint and gitleaks before each commit (`.pre-commit-config.yaml`; the
last two when installed).

A pull request is ready when ruff and the tests that cover the change pass,
plus both image builds when it touches `data/`, `pyproject.toml` or the
Dockerfile. The whole list above runs once before a release.

## Evaluating the analysis

A change to how the analysis reasons (its prep, passes, judge, semantic layer or prompts) gets a run of the
seeded evaluation before the release: `make eval-analysis`, a real AI on five synthetic histories with known
effects, graded. Not in CI: it needs a model client. See [evaluation.md](evaluation.md).

## The docs site and its demo

The site (`site/`, Starlight) is built from this repository's Markdown: `README.md` stays
GitHub's front page, the site's home page is `site/pages/index.md`, and every page's address
and sidebar place come from `SECTIONS` in `site/scripts/prepare.mjs`. A Markdown file under
`docs/` that isn't placed there, or a broken link, fails the build. Images go in
`docs/images/`: the site serves them itself.

```bash
site/scripts/build-demo.sh        # the demo character's pages; needs uv, Docker (or VL_BIN and VM_BIN)
                                  # and Playwright's Chromium (uv run --extra render playwright install chromium)
npm --prefix site ci && npm --prefix site run build
npm --prefix site run preview     # http://localhost:4321/wow-mcp/
```

`build-demo.sh` plays Nibbet, a made-up priest (`tools/fixtures/generate.py`, `build_demo`,
seeded, so every build plays the same sessions, shifted so his last one is yesterday), then
runs the product's own publisher to build his public and friends' pages under `/demo/`. For
the length of the build it starts an empty VictoriaLogs (his events), an empty VictoriaMetrics
(his Battle.net series, as the exporter writes them: `bnet_series`) and the picture renderer
(`wow-stats render`).

His game files are `tools/fixtures/golden3d.py`: a blocky gnome and every item he wears,
written as the shipper sends a player's files (WDC5 tables, M2 models and skins, BLP
textures), with made-up row and file ids. Nothing comes from Blizzard's files. Only the
generator is committed: its output is about 200 kB and takes a second to make
(`uv run python tools/fixtures/golden3d.py <dir>` writes a store to look at). The same files
are the 3D tests' golden input (`tests/test_golden3d.py`, `tests/test_golden3d_browser.py`).
His portrait is the renderer's still of that model, left in the armory cache under the address
his Battle.net series names, so the publisher's usual path finds it.

`tests/test_demo_complete_browser.py` opens every tab of both pages at 390 px and 1280 px in
Chromium and WebKit and fails on any empty section, fallback line or silhouette, and on a 3D
view that doesn't draw. It builds the demo itself, or reads `WOW_DEMO_DIR`; the pages
workflow runs it after its build. A section that falls back is a gap in the demo's data or in
the publisher: fix that, don't allow it in the test.

Astro's preview server doesn't map `model3d/view` to `model3d/view.html` the way GitHub Pages
does, so View in 3D stays blank there.

The manual's screenshots come from that demo, never from a real player:
`uv run --extra render python site/scripts/screenshots.py <address of the built site>` writes
`docs/images/<name>.webp` (light), `<name>-dark.webp` and the link preview
`docs/images/social.png`; commit them.

Write the pages as plain GitHub Markdown; the site adds four things on top:

- `![alt](../images/x.webp)` shows `x-dark.webp` instead in the dark theme when it exists, and
  every screenshot opens full size.
- A paragraph that is only a link to a clip, `[Watch: ...](../videos/media/x-light.mp4)`, plays
  the clip in the reader's theme (`x-dark.mp4` next to it). On GitHub it stays a link. The
  install steps' clips come from `media` in `docs/videos/steps.json`.
- Consecutive `<div class="os" data-os="windows|mac">` blocks become Windows | Mac tabs, one
  choice for the whole page. `tools/manual/generate.py` writes them for the install steps.
- A ` ```mermaid ` block is drawn in the browser (Mermaid, with ELK as the layout when the
  diagram asks for it). `src/components/Footer.astro` holds these three scripts.

`.github/workflows/pages.yml` builds the site on every push to `main` and deploys it to GitHub
Pages once the repository is public; before that, and on pull requests, the build is a
`site-preview` artifact.

## Made-up data only

No test, fixture, doc or screenshot holds a real account, character, guild,
host or address.

- **Test fixtures** come from `tools/fixtures/generate.py` (seeded, byte for
  byte reproducible; its header lists what it writes). After changing it, or
  the code that derives the planner's inputs: `uv run python
  tools/fixtures/generate.py`, read the diff, commit both. CI runs it with
  `--check`.
- **The demo** is `dev/seed.py`: five made-up characters with a DEMO DATA
  banner. Its header has the commands; the compose kit's header has the
  two-line version.
- **The scan** (`tests/test_public_scan.py`) checks the shapes in git (no
  e-mail address, no private IP, no token-looking string) and, when a
  denylist file exists, every name in it. The denylist is never committed:
  one term per line in `~/.config/wow-stats-public/denylist.txt`, or the
  file `WOW_STATS_DENYLIST` names. Put your own names, characters, guild,
  domains and hosts in it.

## CI in a fork

`.github/workflows/ci.yml` runs on GitHub-hosted runners with no secret: the
tests on Linux, the Windows installer test, the demo seed through
PowerShell's pipe (5.1 and 7), both images with smoke tests (read-only
root, uid 10001), and the `audit` job (below). A fork gets all of it as is.

Three more workflows run only while the repository is public, because a
private repository needs GitHub Advanced Security for them. Elsewhere they
skip, which counts as passed for a required check:

| Workflow | What |
|---|---|
| `codeql.yml` | CodeQL on the Python and the JavaScript, results in the Security tab |
| `dependency-review.yml` | the dependencies a pull request adds or bumps: known vulnerabilities (moderate and up) and licenses that can't ship with GPL-3.0-only |
| `scorecard.yml` | OpenSSF Scorecard, published to api.scorecard.dev and the Security tab |

## Dependencies and supply chain

- **Updates**: Dependabot (`.github/dependabot.yml`), weekly on Monday:
  one grouped pull request per ecosystem (uv, GitHub Actions, the
  Dockerfile, the compose kit, the videos' npm) for minor and patch bumps,
  one per major. A release has to be 7 days old before it's proposed.
  Security updates come as soon as an advisory lands.
- **Pins**: actions by commit SHA with the tag in a comment, base images by
  tag and digest on literal `FROM` lines (Dependabot doesn't follow
  `ARG`s), Python packages by `uv.lock` with hashes. The Python minor
  (3.15) moves by hand, not with the weekly bumps.
- **The main image's base** is a Docker Hardened Image,
  `dhi.io/python:3.15-debian13` (Apache 2.0, glibc for DuckDB's wheels, no
  shell, few known CVEs), built in its `-dev` variant. dhi.io takes no
  anonymous pulls: building it needs `docker login dhi.io` with a free
  Docker Hub account, locally and in CI (`DOCKERHUB_USERNAME` and
  `DOCKERHUB_TOKEN`, a read-only token, in the repository's Actions secrets
  and its Dependabot secrets; Dependabot reads the digests with them too).
- **Moved by kb-refresh**: the Polestar tag (`tools/polestar-tag`) with
  its exports, when a release passes the job's checks (docs/kb.md).
- **Moved by hand**: the renderer's Debian snapshot date, and the Polestar
  Recorder version and checksum in the shipper.
- **The `audit` job** (CI, every push and pull request):
  - `actionlint` on the workflows, with shellcheck on their scripts;
  - `uv lock --check`: `uv.lock` in step with `pyproject.toml`;
  - `tools/audit_deps.py`: pip-audit on every locked package, every extra
    and group. A known vulnerability fails the job;
  - gitleaks on the tree and every commit (`.gitleaks.toml`: the default
    rules plus two proven false positives).

  The same, locally:
  ```sh
  actionlint
  uv lock --check
  uv run --frozen --only-group audit python tools/audit_deps.py
  gitleaks git --redact --config .gitleaks.toml .
  ```
- **A vulnerability that can't be fixed yet** (no release upstream, or the
  vulnerable path is never reached): add it to
  `.github/vulnerability-allowlist.toml` with the advisory id and a reason a
  reviewer can check. An entry without a reason fails the job. Remove it
  when the fix lands.

`route-reference.yml` reruns the route export when its inputs change, with
nothing to set up. `kb-refresh.yaml` needs a schedule and, for its model
pass, an Anthropic federation rule: [docs/kb.md](kb.md), "Running the
refresh job in your own repository". Until then it runs only when you start
it by hand, and its model pass skips with a notice.

## Releasing

One button: **Actions > release > Run workflow**, branch `main`. Four inputs:

| Input | Values | What it does |
|---|---|---|
| `channel` | `rc` (default), `stable` | `rc` cuts a release candidate `X.Y.Z-rc.N`, published as a prerelease; `stable` cuts `X.Y.Z`, marked Latest |
| `bump` | `auto` (default), `patch`, `minor`, `major` | the next `X.Y.Z` from the version in `pyproject.toml`; `auto` is `patch`, or the open candidate series |
| `dry_run` | on (default), off | on: the plan, the notes, the tests and both image builds, nothing pushed, tagged or published |
| `kb` | off (default), on | adds the knowledge base refresh line to the notes; `kb-release.yml` sets it, a person leaves it off |

So a minor release goes like this, from `0.6.1`:

1. `channel=rc`, `bump=minor`, `dry_run` on: read the run summary (the
   version, the release commit's files, the notes, the image tags).
2. The same with `dry_run` off: `0.7.0-rc.1`. Deploy it, run the A/B check
   below against it.
3. Fixes merged since? `channel=rc`, `bump=auto`: `0.7.0-rc.2`.
4. `channel=stable`, `bump=auto`: `0.7.0`, from the last candidate as it is
   or with what merged after it.

A real run (`.github/workflows/release.yml`, its header has the detail):

1. `tools/release/release.py plan` picks the version and refuses what makes
   no sense, saying what to press instead: nothing under "Unreleased", a
   `bump` while a candidate series is open, a `channel` that doesn't match a
   version already prepared.
2. `ci.yml` runs in full, and next to it the whole browser suite
   (Chromium and WebKit) on the release commit's tree, on GitHub's hosted
   runners. A browser that can't start fails it there (`WOW_BROWSER_GATE`),
   never a skip. It's the only place CI runs browsers: never in `ci.yml` or on a
   pull request (an install once hung a CI job for 30 minutes), so the job
   and each install step have their own timeout, and the browsers are cached
   per Playwright version. A dry run runs it too.
3. The release commit: the version in `pyproject.toml`, `wowstats/__init__.py`,
   `shipper/wow_stats.py`, `uv.lock` and the bug template, CHANGELOG.md's
   "Unreleased" under `## X.Y.Z (date)`, and the shipper's signature in
   `shipper/update-signature.json`. The `sign` job makes that one: it runs
   `tools/release/shipper_sign.py sign` on the same tree with the release key,
   the secret `SHIPPER_SIGNING_KEY` of the environment `shipper-signing`
   (limited to `main`, read by that one step, never printed). Every installed
   shipper checks it before it updates itself ([shipper.md](shipper.md#auto-update)),
   and the build refuses to push an image whose signature doesn't match its
   shipper files. Keep a copy of record of the key outside GitHub (the
   owner's is SOPS-encrypted): the GitHub secret is set from it and can't be
   read back. Lose that copy and
   every installed shipper stays on its version for good, short of the
   update line on each PC. Never rotate the key without a transition: one
   release signed with the old key that pins the new one. The commit goes
   through a pull request,
   `Release X.Y.Z`, opened by the CI GitHub App so its checks run, and
   auto-merge squashes it when they pass. A red check stops the run: fix
   main, press again.
4. Both images for linux/amd64 and linux/arm64, from the merged commit:
   `ghcr.io/<owner>/wow-mcp:<version>` and `ghcr.io/<owner>/wow-mcp-render:<version>`,
   checked, attested and signed (below). No `latest` tag.
5. The `v<version>` tag on that commit and the GitHub release: the
   CHANGELOG section (a final also carries its candidates' sections) and
   each image's `tag@digest` to pin.

What an rc changes for the people running it: the shipper on their PCs
updates itself to any higher version, candidates included, and a candidate
sorts below its final (`0.7.0-rc.2` < `0.7.0`). Shippers before 0.7.0 only
read `X.Y.Z`: they log "the server names no version" every 6 hours and move
at the final.

Before pressing for real: the release eval and the A/B check below. The
release runs the rest, browsers included.

**Release eval.** `make eval-release` on your own machine, on the commit
you'll release: the seeded evaluation ([evaluation.md](evaluation.md)) with
Claude Code and with the server's own runner, every scenario three times,
all at once, three server runs at a time (about 25 minutes, up to $10 of the server's model). A scenario
under two passes of three is a reason to wait. Paste the batch's
`release.md` table, per model and scenario, into "Unreleased" so it lands in
the release notes. Never in CI. Write the CHANGELOG as you merge, under
"Unreleased": what changed for a player, then "Upgrading" with every
changed variable, port, volume or route.

An interrupted run (a build failed after the release PR merged) leaves main
carrying an untagged version with an empty "Unreleased". Press again with
the same `channel` and `bump=auto`: it tags and publishes that version as
it is. The same goes for a release PR made by hand
(`uv run python tools/release/release.py apply X.Y.Z`, then a PR).

### Knowledge base releases (automatic)

The knowledge base is baked into the image, so a refresh only reaches the
people running it with a new version. When a kb-refresh PR merges on main,
`.github/workflows/kb-release.yml` presses the button by itself: `channel`
`stable`, `bump` `patch`, a fourth input `kb` on, `dry_run` off. Same run,
same gates (tests, signature, release PR, images, attestations, tag); it
waits for that run and goes red when it fails.

- Only the refresh job's merges count (`tools/release/kb.py guard`: branch
  `kb-<mode>` of this repository, opened by a bot, label `kb-watch`). A
  person's PR that touches `data/kb` rides the next release like any other.
- **No release while a candidate series is open** (a `v*-rc.N` tag newer
  than the last final, or a candidate on main). The KB rides the next
  candidate or the final; the kb-release run's summary says so.
- No release either on an untagged version on main (an interrupted run):
  press the button as above, the KB goes with it.
- The notes get a "Knowledge base" section, one line: `Knowledge base
  refresh (#12 watch): Polestar v0.4.1.1 to v0.4.2; lanes levelling.`
  `kb.py note` builds it from the repository (`data/kb/watch.json` before
  and after, the changed lane files, a Polestar tag that is a plain
  version), never from a PR's title or body or a page's text. Whatever else
  sits under "Unreleased" goes out in the same patch release.
- One kb-release run at a time, the newest waiting: a burst of merges makes
  one release per run that finds something unreleased on main as it is when
  it starts. A merge that lands while a release runs fails that run's "main
  moved" check (red), and the waiting kb-release run releases both.
- Each such release moves every shipper (they update to any higher
  version), with unchanged shipper files.

A pull request that changes the `Dockerfile`, `.dockerignore`,
`release.yml` or `tools/release/` runs the builds as a dry run: both
platforms build, merge in a registry on the runner and pass the same checks.

Only the CI GitHub App can create a `v*` tag: the `release tags` ruleset
refuses everyone else, and the release run makes its tag with the App's
token. The first release candidate after the rule went in is its live test:
if that run stops at "Tag and release" on a refused ref, the rule is what
to read.

Each image carries BuildKit's SLSA provenance and an SPDX SBOM per
platform, and the run keeps the SBOMs as an artifact. From a public
repository the run also writes GitHub artifact attestations and a keyless
cosign signature (below). From a private one those steps skip: artifact
attestations need GitHub Enterprise Cloud there, and a signature would put
the private repository's name in Sigstore's public log.

The image names live in one place, the `env:` block at the top of
`release.yml`. The owner part comes from the repository. The builds stamp
the repository's URL in the `org.opencontainers.image.source` label.

## The CI mirror

The jobs that hold credentials never run in this public repository, where a
fork's pull request could reach them. They run in a private mirror,
`sam-dumont/wow-mcp-ci`:

- `mirror.yml` force-pushes main there on every push to main (and a
  branch on request), with the CI GitHub App's token. Never on a pull
  request. The push overwrites the mirror's main, so nothing is committed
  there: its secrets live in its settings, real data in the owner's stores.
- `kb-refresh.yaml` (docs/kb.md) runs only there: the Anthropic federation
  and the schedule point at the mirror. Its branch, PR and issues land
  here, through the same App, and its PRs pass the same required checks
  as any other.
- Every job of every workflow names the one repository it runs in by id, as
  a top-level `&&` term of its `if:`: `1464458020` (this repository) for
  all but `kb-refresh.yaml`, which names the mirror (`1465633168`).
  `tests/test_workflow_guard.py` fails on a job without it. In a fork,
  replace the id with your repository's (`gh api repos/<owner>/<repo> --jq .id`),
  or nothing runs.

The App (variable `CI_APP_CLIENT_ID`, secret `CI_APP_PRIVATE_KEY` in the
environment `main-only` of both repositories, which only main can deploy
to: a branch pushed to the mirror brings its own workflow files) has Contents, Workflows, Pull requests and Issues write on
these two repositories only. The release's PR, its tag and the mirror push
use it; GITHUB_TOKEN writes only the GitHub release.

## Before a release: the A/B check

`tools/ab/ab.py` shows two versions the same real data and lists every
answer that changed: gear and upgrades, the plan for 1, 2 and 4 hours,
talents, gold and mount, pace, journal, timeline, the analysis, every MCP
tool, the public and zogzog bundles. Errors, empty answers and NaN that the
old version didn't have come first, as regressions.

It records once and replays after. `record` runs the current version against
your stores through a proxy that keeps every request the app makes and its
answer, read-only and for the default VictoriaLogs tenant (AccountID 0)
only. `replay` runs any version against that recording alone, with the clock
frozen at the recording's instant, so the old and the new version see
exactly the same inputs even though you kept playing in between.

```sh
# Today, on the version you run (its image by digest, or a ref):
uv run python tools/ab/ab.py record --vl http://127.0.0.1:9428 --vm http://127.0.0.1:8429 \
  --image ghcr.io/<owner>/wow-mcp@sha256:<platform digest> \
  --env DEFAULT_CHAR=<char> --state <copy of ANALYSIS_STATE_DIR>
uv run python tools/ab/ab.py replay <recording> --image <same> --label baseline
# Within 24 h, optional: add what main asks that the old version never did.
uv run python tools/ab/ab.py extend <recording> --vl ... --vm ... --ref origin/main --label extend
# Later, the release candidate:
uv run python tools/ab/ab.py replay <recording> --ref <rc commit> --label rc
uv run python tools/ab/ab.py diff baseline rc
```

- **Determinism first**: replay the baseline version twice and diff the two
  runs. The diff must be empty; anything in it is noise the report would
  blame on the release.
- **Settings**: `--env` takes the few the app needs to read your stores the
  way your install does (`LOGS_FILTER`, `DEFAULT_CHAR`,
  `VL_ARCHIVE_AFTER_DAYS`...); the recording keeps them for every replay.
- **Recording misses**: a request the recording doesn't have gets an error
  and shows as a regression, because that answer wasn't tested. `extend`,
  run soon after `record`, closes most of them.
- **Where it writes**: `WOW_AB_HOME`, else `wow-ab/` next to your checkout.
  It refuses any folder inside a git work tree: a recording holds your game
  data and Battle.net data.
- **30 days**: Blizzard's terms keep Battle.net data 30 days at most. Each
  recording carries its date and its `delete_by`; `replay` refuses one past
  30 days and `purge` deletes it with its runs. `info` lists the dates.
- **Not covered**: the 3D view and the level pictures (the renderer), both
  off during a run: they draw from game files, not from the stores.
- Heavy: a run takes 1 to 3 minutes and Docker is capped at 4 CPUs and 6 GB.
  Docker runs `--image` with the image's own platform, emulated if needed;
  name a multi-platform image by its platform's manifest digest.

## Using a released image

Pin by digest: `ghcr.io/<owner>/wow-mcp:X.Y.Z@sha256:...`, from the release
run's summary.

While the package is private, every pull needs a token with
`read:packages` (a classic personal access token, or a GitHub App token
with that permission):

- **Docker / compose**: `docker login ghcr.io -u <user>` with the token as
  the password (from stdin, never on the command line). Then in
  `deploy/compose/.env`: `WOW_STATS_IMAGE=ghcr.io/<owner>/wow-mcp:X.Y.Z@sha256:...`,
  and `docker compose pull ui publish site && docker compose up -d`.
- **Kubernetes**: a `kubernetes.io/dockerconfigjson` Secret for `ghcr.io`
  with that token, named in each pod's `imagePullSecrets`. The variables,
  ports and mounts are in [docs/deploy.md](deploy.md).

Without `WOW_STATS_IMAGE` the compose kit builds the image from the tree
(`docker compose up -d --build`). That build pulls the base from dhi.io, so
it needs `docker login dhi.io` once, with a free Docker Hub account.

## Verifying an image

Releases from the public repository are attested and signed by
`release.yml` in that repository: from 0.7.0, a run of the release button
on main; up to 0.6.1, a push of the `v*` tag. Replace `<owner>` with the
repository's owner and `X.Y.Z` with the release.

Build provenance (which workflow, commit and tag built it), with the
[GitHub CLI](https://cli.github.com/):

```sh
for image in wow-mcp wow-mcp-render; do
  gh attestation verify oci://ghcr.io/<owner>/$image:X.Y.Z --repo <owner>/wow-mcp \
    --signer-workflow <owner>/wow-mcp/.github/workflows/release.yml \
    --source-ref refs/heads/main
done
```

Up to 0.6.1, `--source-ref refs/tags/vX.Y.Z`.

The SBOM attestations (one per platform), and the SBOM itself:

```sh
gh attestation verify oci://ghcr.io/<owner>/wow-mcp:X.Y.Z --repo <owner>/wow-mcp \
  --signer-workflow <owner>/wow-mcp/.github/workflows/release.yml \
  --source-ref refs/heads/main --predicate-type https://spdx.dev/Document/v2.3
docker buildx imagetools inspect ghcr.io/<owner>/wow-mcp:X.Y.Z \
  --format '{{ json (index .SBOM "linux/amd64").SPDX }}' > wow-mcp-amd64.spdx.json
```

The cosign signature, tied to the release workflow on main (from 0.7.0) or
on a release tag (up to 0.6.1):

```sh
cosign verify ghcr.io/<owner>/wow-mcp@sha256:<digest> \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp '^https://github\.com/<owner>/wow-mcp/\.github/workflows/release\.yml@refs/(heads/main|tags/v\d+\.\d+\.\d+(-rc\.\d+)?)$'
```

Pin what you verified by digest (`ghcr.io/<owner>/wow-mcp:X.Y.Z@sha256:...`):
a tag can move, a digest can't.
