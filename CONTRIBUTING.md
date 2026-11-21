# Contributing

Bug reports, fixes and ideas are welcome. Security problems go through
[SECURITY.md](SECURITY.md), never a public issue.

The full guide is [docs/development.md](docs/development.md). The short
version:

1. **Python through [uv](https://docs.astral.sh/uv/) only.**
   `uv sync --frozen --extra publish --extra mcp --extra analysis --extra analysis-openai --group browser`.
2. **Before a pull request**, run what covers your change:
   ```sh
   uv run ruff check .
   make test-changed   # only the tests your diff can break
   uv run python tools/fixtures/generate.py --check
   ```
   Touching `data/`, `pyproject.toml` or the `Dockerfile`? Build both images
   too: `docker build .` and `docker build --target renderer .`.
3. **Made-up data only.** No real account, character, guild, host or address
   in code, tests, docs or screenshots. Fixtures come from
   `tools/fixtures/generate.py`; `tests/test_public_scan.py` checks the rest.
4. **Dependencies**: change `pyproject.toml`, run `uv lock`, commit both.
   CI fails when `uv.lock` is out of step or a locked package has a known
   vulnerability.
5. **One change per pull request**, with a CHANGELOG line when a player or
   an operator would notice it (and an "Upgrading" note for any changed
   variable, port, volume or route).

Optional: `pre-commit install` runs ruff, the fixture check and actionlint
on each commit (`.pre-commit-config.yaml`).

By contributing you agree that your work is released under the project's
license, [GPL-3.0-only](LICENSE), and you follow the
[code of conduct](CODE_OF_CONDUCT.md).
