## What and why

<!-- What changes for a player or an operator, and why. Link the issue if there is one. -->

## Checked

- [ ] `uv run ruff check .` and the tests that cover the change (`uv run pytest -q -rs ...`)
- [ ] `uv run python tools/fixtures/generate.py --check` (if fixtures or the planner changed)
- [ ] Both image builds (if `data/`, `pyproject.toml` or the `Dockerfile` changed)
- [ ] Browser tests, Chromium and WebKit (if a page changed): screenshots below
- [ ] CHANGELOG entry, with an "Upgrading" note for any changed variable, port, volume or route
- [ ] Made-up data only: no real names, accounts, hosts, keys or share links

## Not checked

<!-- What you couldn't run, and why. -->
