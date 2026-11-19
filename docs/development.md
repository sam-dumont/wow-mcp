# Development

**For:** contributors. **At the end:** you can set up the project, run every check, and cut a release.

Everything needed to work on wow-stats and cut a release. The command and the
Python package are `wow-stats`; the images are `wow-mcp` and `wow-mcp-render`.

## Set up

Python goes through [uv](https://docs.astral.sh/uv/) only.

```sh
uv sync --frozen --extra publish --extra mcp --group browser
uv run playwright install chromium webkit
```

The `browser` group and the two browsers are for the browser tests
(`tests/test_browser.py`, `tests/test_advice_browser.py`). Without them those
tests skip; CI doesn't install them, so run them locally before a release.

## The checks

```sh
uv run ruff check .
uv run pytest -q -rs        # about 950 tests, a few minutes; -rs lists what skipped
docker build -t wow-stats:dev .
docker build --target renderer -t wow-stats-render:dev .
```

What skips without what:

| Tests | Need |
|---|---|
| browser tests | the `browser` group and its browsers |
| `tests/test_compose.py` | `docker compose` |
| `tests/test_windows_e2e.py` | Windows (CI runs it on `windows-latest`) |
| `tests/test_renderer_browser.py`, `tests/test_model3d_browser.py` | `WOW_STATS_BROWSER=1`: they fetch Wowhead's viewer over the network |
| `tests/test_gear_parity.py` | `POLESTAR_CHECKOUT`: a Polestar clone at the pinned tag ([docs/gear-reference.md](gear-reference.md)) |
| `tests/test_public_scan.py`, the name check | the denylist file (below) |

Optional: `pre-commit install` runs ruff, the lock and fixture checks,
actionlint and gitleaks before each commit (`.pre-commit-config.yaml`; the
last two when installed).

A pull request is ready when ruff and the tests that cover the change pass,
plus both image builds when it touches `data/`, `pyproject.toml` or the
Dockerfile. The whole list above runs once before a release.

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
- **Moved by hand**: `POLESTAR_TAG` in `route-reference.yml` (kb-refresh's
  watcher reports new Polestar releases), the renderer's Debian snapshot
  date, and the Polestar Recorder version and checksum in the shipper.
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

1. Bump `version` in `pyproject.toml`, write the CHANGELOG entry (what
   changed for a player, then "Upgrading" with every changed variable, port,
   volume or route), merge.
2. Run the full checks above on that commit, browsers included.
3. Tag it `vX.Y.Z` and push the tag. `.github/workflows/release.yml` builds
   both images for linux/amd64 and linux/arm64, pushes
   `ghcr.io/<owner>/wow-mcp:X.Y.Z` and `ghcr.io/<owner>/wow-mcp-render:X.Y.Z`
   and prints each `tag@digest` in the run summary. The tag must match
   `pyproject.toml` or the run stops.

Each image carries BuildKit's SLSA provenance and an SPDX SBOM per
platform, and the run keeps the SBOMs as an artifact. From a public
repository the run also writes GitHub artifact attestations and a keyless
cosign signature (below). From a private one those steps skip: artifact
attestations need GitHub Enterprise Cloud there, and a signature would put
the private repository's name in Sigstore's public log.

The image names live in one place, the `env:` block at the top of
`release.yml`. The owner part comes from the repository, so a fork publishes
to its own namespace. The builds stamp the repository's URL in the
`org.opencontainers.image.source` label.

A package pushed from a private repository is private. Its visibility is
set by hand in the package settings, never by the workflow.

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
(`docker compose up -d --build`), so a clone runs with no registry at all.

## Verifying an image

Releases from the public repository are attested and signed by
`release.yml` in that repository, on a `v*` tag. Replace `<owner>` with the
repository's owner and `X.Y.Z` with the release.

Build provenance (which workflow, commit and tag built it), with the
[GitHub CLI](https://cli.github.com/):

```sh
gh attestation verify oci://ghcr.io/<owner>/wow-mcp:X.Y.Z --repo <owner>/wow-mcp
gh attestation verify oci://ghcr.io/<owner>/wow-mcp-render:X.Y.Z --repo <owner>/wow-mcp
```

The SBOM attestations (one per platform), and the SBOM itself:

```sh
gh attestation verify oci://ghcr.io/<owner>/wow-mcp:X.Y.Z --repo <owner>/wow-mcp \
  --predicate-type https://spdx.dev/Document/v2.3
docker buildx imagetools inspect ghcr.io/<owner>/wow-mcp:X.Y.Z \
  --format '{{ json (index .SBOM "linux/amd64").SPDX }}' > wow-mcp-amd64.spdx.json
```

The cosign signature, tied to the release workflow at a tag:

```sh
cosign verify ghcr.io/<owner>/wow-mcp@sha256:<digest> \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp '^https://github\.com/<owner>/wow-mcp/\.github/workflows/release\.yml@refs/tags/v'
```

Pin what you verified by digest (`ghcr.io/<owner>/wow-mcp:X.Y.Z@sha256:...`):
a tag can move, a digest can't.
