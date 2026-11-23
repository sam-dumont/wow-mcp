# Test shortcuts. Python goes through uv only (docs/development.md).
# Half the cores, like a laptop with other agents running; CI uses `-n auto`.
TEST_WORKERS ?= $(shell python3 -c "import os; print(max(1, (os.cpu_count() or 2) // 2))")
BROWSER_WORKERS ?= 4

.PHONY: test test-changed test-browser test-vl eval-analysis eval-mcp

# What to run before pushing: only the tests your diff can break.
test-changed:
	uv run python tools/test_changed.py

# The whole fast suite (no browsers, nothing CI-only), in parallel.
test:
	uv run pytest -q -rs -n $(TEST_WORKERS)

# tests/test_logsql.py: the Python LogsQL engine against a real VictoriaLogs,
# the image the compose kit pins (a container started here, stopped after).
VL_IMAGE := $(shell sed -n 's|^ *image: \(docker.io/victoriametrics/victoria-logs:.*\)$$|\1|p' deploy/compose/compose.yaml)
test-vl:
	docker run -d --rm --name wow-test-vl -p 127.0.0.1:19428:9428 $(VL_IMAGE) -retentionPeriod=100y >/dev/null
	for _ in $$(seq 1 30); do curl -fsS http://127.0.0.1:19428/health >/dev/null 2>&1 && break; sleep 1; done
	WOW_TEST_VL_URL=http://127.0.0.1:19428 uv run pytest -q -rs tests/test_logsql.py; s=$$?; docker stop wow-test-vl >/dev/null; exit $$s

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

# The MCP check (docs/development.md, "The MCP check"): ten questions about the
# demo character, one exact answer each, answered by Claude Haiku through the
# MCP tools alone (your Claude Code login), graded without a model. About a
# minute; not in CI, not a release gate. One: make eval-mcp EVAL_ARGS="--only top_dungeon"
eval-mcp:
	uv run --frozen --extra mcp python tools/eval_mcp/run.py $(EVAL_ARGS)
