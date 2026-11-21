# Test shortcuts. Python goes through uv only (docs/development.md).
# Half the cores, like a laptop with other agents running; CI uses `-n auto`.
TEST_WORKERS ?= $(shell python3 -c "import os; print(max(1, (os.cpu_count() or 2) // 2))")
BROWSER_WORKERS ?= 4

.PHONY: test test-changed test-browser eval-analysis

# What to run before pushing: only the tests your diff can break.
test-changed:
	uv run python tools/test_changed.py

# The whole fast suite (no browsers, nothing CI-only), in parallel.
test:
	uv run pytest -q -rs -n $(TEST_WORKERS)

# The browser suites: once before a release, not on every change. A whole file
# per worker, so a file's server and browser are launched once.
test-browser:
	uv run pytest -q -rs -m browser -n $(BROWSER_WORKERS) --dist loadfile

# The seeded evaluation of the play analysis (docs/evaluation.md): synthetic
# histories with known effects, a real MCP client (Claude Code by default:
# your own login), every kept line graded. Tens of minutes per scenario, so
# never in CI. One scenario: make eval-analysis SCENARIO=death_spike
eval-analysis:
	uv run --frozen --extra mcp --extra analysis-openai python tools/eval_analysis/run.py \
		$(if $(SCENARIO),--scenario $(SCENARIO)) $(if $(CLIENT),--client $(CLIENT)) $(EVAL_ARGS)
