import contextlib
import importlib.util
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# Fixtures only: the shipper (and every subprocess a test starts) refuses a
# WoW folder outside a temp directory, never the real install of the machine
# running the tests (2026-11-20).
os.environ["NO_REAL_WOW"] = "1"


@pytest.fixture(scope="session", autouse=True)
def sealed_names():
    """Every sealed /zogzog/ file's keyed name, kept with its path
    (tests/sealed.py), so a test can open a bundle by path."""
    from tests import sealed

    sealed.record()


# What the SQLite event store couldn't answer (wowstats.eventstore.Unanswerable),
# even where the app caught it: that store has no VictoriaLogs to fall back on,
# so every query the app issues must fit wowstats.logsql's subset.
UNANSWERED: list[str] = []


@pytest.fixture(scope="session", autouse=True)
def _record_unanswered():
    from wowstats import eventstore

    orig = eventstore.SQLiteEvents.query

    def query(self, *a, **k):
        try:
            return orig(self, *a, **k)
        except eventstore.Unanswerable as e:
            UNANSWERED.append(str(e))
            raise

    eventstore.SQLiteEvents.query = query
    yield
    eventstore.SQLiteEvents.query = orig


@pytest.fixture(autouse=True)
def sqlite_answers_every_query(request):
    """Fails the test (or the first test after a module fixture) during
    which the SQLite store met a query outside the subset."""
    yield
    got, UNANSWERED[:] = list(UNANSWERED), []
    if not request.node.get_closest_marker("unanswerable_ok"):
        assert not got, f"the SQLite event store couldn't answer {len(got)} queries: {got[:3]}"


@pytest.fixture(autouse=True)
def wow_folder_is_a_fixture():
    yield
    mod = sys.modules.get("wow_stats_shipper")
    if mod is not None:
        assert mod.SANDBOX and mod.in_temp(mod.WOW), f"a test pointed the shipper at {mod.WOW}"


@pytest.fixture(autouse=True)
def shipper_never_reads_the_machines_client(request, monkeypatch, tmp_path_factory):
    """The shipper's default Polestar client folder is under HOME: on a dev
    Mac with the client installed, a test's main() loaded its real chunks
    into CLIENT, and they leaked into every later extract_tree (2026-11-22).
    HOME is a temp dir for the shipper, and CLIENT starts and ends empty."""
    mod = sys.modules.get("wow_stats_shipper")
    if mod is not None:
        if not request.node.get_closest_marker("real_home"):  # the opt-in real-data test reads it
            monkeypatch.setattr(mod, "HOME", tmp_path_factory.mktemp("home"))
        mod.CLIENT.clear()
    yield
    if mod is not None:
        mod.CLIENT.clear()


@pytest.fixture(autouse=True)
def no_item_tables(monkeypatch):
    """The loot history's item table comes from wago.tools: never in tests."""
    from wowstats import itemcard, items, talents

    monkeypatch.setenv("WOW_ITEMS", "off")
    # The item catalog too: one made by a module-scoped fixture, before this
    # one turns WOW_ITEMS off, stayed on for the rest of the worker.
    monkeypatch.setattr(itemcard, "_shared", None)
    monkeypatch.setattr(items, "_shared", None)
    monkeypatch.setattr(talents, "_shared", None)
    monkeypatch.setattr(talents, "_icons", {})


class _SharedBrowser:
    """What a test's `launch()` returns: the module's one real browser, whose
    close() does nothing (the module's teardown closes it). Launching a
    Chromium per test was most of the browser suites' time (2026-11-20)."""

    def __init__(self, real):
        self._real = real

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture(scope="module")
def demo_clock():
    """The clock at the demo's own "now" for a whole module (tests/degraded.py
    clock()): `pytestmark = pytest.mark.usefixtures("demo_clock")` in every
    module that serves the fixed demo days, so its module fixtures are made
    under it too."""
    from tests import degraded

    with degraded.clock():
        yield


@pytest.fixture(autouse=True, scope="module")
def one_browser_per_module():
    """Tests launch browsers as they like (one per test, closed on their last
    line, which an assertion before it skips: 71 Chromium and dozens of WebKit
    left open on the owner's Mac, 2026-11-20). Behind it, each Playwright
    instance (one per module) launches each engine once, and every module
    closes what it opened, failed tests included."""
    try:
        from playwright.sync_api import BrowserType
    except ImportError:
        yield
        return
    opened: dict = {}
    launch = BrowserType.launch

    def shared(self, *a, **k):
        key = (id(self), repr(a), repr(sorted(k.items())))
        if key not in opened:
            opened[key] = launch(self, *a, **k)
        return _SharedBrowser(opened[key])

    BrowserType.launch = shared
    try:
        yield
    finally:
        BrowserType.launch = launch
        for b in opened.values():
            with contextlib.suppress(Exception):  # its driver may already be stopped
                b.close()


@pytest.fixture(scope="session")
def shipper():
    """shipper/wow_stats.py is a standalone script, not part of the package."""
    spec = importlib.util.spec_from_file_location("wow_stats_shipper", ROOT / "shipper" / "wow_stats.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["wow_stats_shipper"] = mod
    spec.loader.exec_module(mod)
    return mod


class Running:
    def __init__(self, app):
        from wowstats import ui

        app.port = 0
        self.srv = ui.serve(app)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def get(self, path, headers=None, method="GET", data=None):
        """(status, headers, body) without following redirects."""

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        req = urllib.request.Request(self.base + path, headers=headers or {}, method=method, data=data)
        try:
            # Generous: a route works for real here (the planner's first
            # gather, references read cold), and CI runs 4 workers on 4 slow
            # cores; 10 s timed out once there (#11). A hung server still fails.
            with opener.open(req, timeout=60) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def run_app():
    started = []

    def start(app):
        r = Running(app)
        started.append(r)
        return r

    yield start
    for r in started:
        r.close()


# Need docker compose, a Polestar checkout or Windows: CI's jobs and the release run them.
CI_ONLY = {"test_compose.py", "test_windows_e2e.py"}


def pytest_addoption(parser):
    parser.addoption("--shard", default=None, metavar="K/N",
                     help="run the K-th of N parts of the suite, whole test files each (ci.yml's test matrix)")


# Seconds each test file takes, summed over its tests (setup and teardown
# included), from a run of the whole suite: tools/shard_times.py writes it.
SHARD_TIMES = ROOT / "tests" / "shard_times.json"


def _shards(paths: set[str], n: int) -> dict[str, int]:
    """Each file's part, 1 to N: the slowest file first, each to the part
    with the fewest seconds so far (ties to the lowest part, then by path, so
    every job of the matrix computes the same split). A file the table
    doesn't know weighs the table's median until it's measured. The browser
    files are split on their own: a run keeps either them or the rest, so
    each pool is balanced by itself."""
    import json
    import statistics

    times = json.loads(SHARD_TIMES.read_text()) if SHARD_TIMES.exists() else {}
    default = statistics.median(times.values()) if times else 1.0
    out: dict[str, int] = {}
    for pool in (sorted(p for p in paths if "browser" in Path(p).name),
                 sorted(p for p in paths if "browser" not in Path(p).name)):
        load = [0.0] * n
        for path in sorted(pool, key=lambda p: -float(times.get(p, default))):
            k = min(range(n), key=lambda i: load[i])
            load[k] += float(times.get(path, default))
            out[path] = k + 1
    return out


def pytest_collection_modifyitems(config, items):
    """Every test in a *browser* file carries the `browser` marker, so the
    default run (pyproject's `-m "not browser and not ci_only"`) is the fast
    suite and `-m browser` the heavy one (tools/test_changed.py picks between
    them from the diff).

    `--shard K/N` keeps the K-th of N parts: CI runs the suite as N jobs side
    by side (one job on 4 cores reached its 20 minutes, 2026-11-22). Whole
    files, so a module's fixtures are built in one job only, split by their
    measured time (_shards). The parts used to be the hash of the path: on
    2026-11-24 part 2 got 1,568 tests and 17 minutes, part 1 995 and 5, and a
    slower runner cancelled part 2 at its 20 minutes (#235)."""
    for item in items:
        name = Path(str(item.fspath)).name
        if "browser" in name:
            item.add_marker(pytest.mark.browser)
        if name in CI_ONLY:
            item.add_marker(pytest.mark.ci_only)
    shard = config.getoption("--shard")
    if shard:
        k, n = (int(x) for x in shard.split("/"))
        if not 1 <= k <= n:
            raise pytest.UsageError(f"--shard {shard}: K from 1 to N")
        keep, drop = [], []
        rel = {item: Path(str(item.fspath)).resolve().relative_to(ROOT).as_posix() for item in items}
        part = _shards(set(rel.values()), n)
        for item in items:
            (keep if part[rel[item]] == k else drop).append(item)
        if drop:
            config.hook.pytest_deselected(items=drop)
            items[:] = keep


# release.yml's browser gate sets WOW_BROWSER_GATE: there a browser test that
# skips (no Playwright, an engine that won't start, a fixture missing) fails
# instead, so a green gate means every page was really opened in Chromium and
# WebKit. Locally they still skip without their browsers.
BROWSER_GATE = os.environ.get("WOW_BROWSER_GATE") == "1"


def _gate(report, what):
    if BROWSER_GATE and report.skipped and "browser" in Path(str(report.fspath)).name:
        reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else report.longrepr
        report.outcome = "failed"
        report.longrepr = f"WOW_BROWSER_GATE: {what} skipped: {reason}"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if not hasattr(report, "wasxfail"):
        _gate(report, item.nodeid)


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector):
    outcome = yield
    _gate(outcome.get_result(), str(collector.nodeid))
