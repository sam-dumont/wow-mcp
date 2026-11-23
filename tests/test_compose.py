"""The compose kit (deploy/compose) as Docker resolves it: a durable MCP
state, the download switches in both services that fetch, ZOGZOG_PATH's
empty value, the optional disk ceiling, the ui's memory limit. Skipped without `docker compose`."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "deploy" / "compose"


def config(*profiles: str, **env) -> dict:
    if not shutil.which("docker"):
        pytest.skip("no docker")
    clean = {k: v for k, v in os.environ.items()
             if k not in ("ZOGZOG_PATH", "MODEL3D", "WOW_ITEMS", "VL_MAX_DISK", "UI_MEM_LIMIT", "VL_MEM_LIMIT", "COMPOSE_PROJECT_NAME")}
    flags = [x for p in profiles for x in ("--profile", p)]
    r = subprocess.run(["docker", "compose", "--env-file", os.devnull, *flags, "config", "--format", "json"], cwd=KIT,
                       env=clean | {"INGEST_PASSWORD": "x" * 12} | env, capture_output=True, text=True)
    if r.returncode and "is not a docker command" in r.stderr:
        pytest.skip("no docker compose")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["services"]


def test_mcp_state_survives_a_restart():
    ui = config()["ui"]
    env = ui["environment"]
    assert env["ANALYSIS_STATE_DIR"].startswith("/data/") and env["MCP_STATE_DIR"].startswith("/data/")
    vols = {v["target"]: v for v in ui["volumes"]}
    assert vols["/data"]["type"] == "volume" and not vols["/data"].get("read_only")


def test_download_switches_reach_ui_and_publisher():
    s = config(MODEL3D="off", WOW_ITEMS="off")
    for name in ("ui", "publish"):
        assert s[name]["environment"]["MODEL3D"] == "off" and s[name]["environment"]["WOW_ITEMS"] == "off", name
    s = config()
    for name in ("ui", "publish"):
        assert s[name]["environment"]["MODEL3D"] == "on" and s[name]["environment"]["WOW_ITEMS"] == "on", name


def test_an_empty_zogzog_path_turns_the_friends_site_off():
    for env, want in (({}, "/zogzog/"), ({"ZOGZOG_PATH": ""}, "")):
        s = config(**env)
        assert s["publish"]["environment"]["ZOGZOG_PATH"] == want
        assert s["site"]["environment"]["ZOGZOG_PATH"] == want


def test_disk_ceiling_is_off_unless_set():
    flag = "-retention.maxDiskSpaceUsageBytes="
    assert flag + "0" in config()["victorialogs"]["command"]
    assert flag + "20GiB" in config(VL_MAX_DISK="20GiB")["victorialogs"]["command"]


def test_victorialogs_runs_with_the_limits_security_md_asks_for():
    """docs/security.md accepts /api/logsql (L7) because VictoriaLogs caps the
    cost of a query: the kit applies it."""
    s = config()["victorialogs"]
    for flag in ("-search.maxQueryDuration=30s", "-search.maxConcurrentRequests=4", "-search.maxQueueDuration=10s"):
        assert flag in s["command"]
    assert int(s["mem_limit"]) == 1 << 30
    assert int(config(VL_MEM_LIMIT="2g")["victorialogs"]["mem_limit"]) == 2 << 30
    security = (ROOT / "docs" / "security.md").read_text()
    assert "-search.maxQueryDuration" in security and "-search.maxConcurrentRequests" in security


def test_the_ui_memory_limit_has_room_over_the_measured_peak():
    # 472 MiB peak on 55 weeks of the demo (100,705 events), 2026-11-19:
    # the default keeps about twice that, and UI_MEM_LIMIT raises it.
    assert int(config()["ui"]["mem_limit"]) == 1 << 30
    assert int(config(UI_MEM_LIMIT="2g")["ui"]["mem_limit"]) == 2 << 30
    docs = (ROOT / "docs" / "install.md").read_text()
    assert "472 MiB" in docs and "1 GiB" in docs and "UI_MEM_LIMIT" in docs


def test_the_demo_service_loads_the_demo_through_the_uis_ingest():
    """One line on every system (`docker compose run --rm demo`): the image
    has no generator, so the service reads this repository, read-only, and
    sends the docs site's demo character to /ingest as a shipper would."""
    assert "demo" not in config()  # a profile: never started by `up`
    d = config("demo")["demo"]
    assert d["entrypoint"] == ["python", "/src/dev/seed.py"]
    assert d["command"] == ["--ingest", "http://ui:8080/ingest"]
    assert d["environment"]["INGEST_PASSWORD"] == "x" * 12
    (src,) = [v for v in d["volumes"] if v["target"] == "/src"]
    assert src["type"] == "bind" and src["read_only"] is True and Path(src["source"]) == ROOT
    assert d["read_only"] is True and d["user"] == "10001:10001" and d["restart"] == "no"
    assert (ROOT / "dev" / "seed.py").is_file() and (ROOT / "tools" / "fixtures" / "generate.py").is_file()


def kit_app(**env):
    """The ui as the kit starts it: its resolved environment, plus what the
    image sets (BIND=0.0.0.0: Dockerfile)."""
    from wowstats import ui

    e = {k: str(v) for k, v in config(**env)["ui"]["environment"].items() if v is not None}
    return ui.App({**e, "BIND": "0.0.0.0", "ANALYSIS_STATE_DIR": "", "MCP_STATE_DIR": ""})


def test_the_kit_without_a_login_starts_only_on_127_0_0_1():
    """Review L6: the kit's no-login default is safe because the port is
    published on 127.0.0.1; widening UI_PUBLISH without a login stops it."""
    assert kit_app().auth_mode == "open"
    from wowstats import ui

    for wide in ("0.0.0.0:8080", "8080", "192.0.2.20:8080"):
        with pytest.raises(ui.ConfigError, match="PUBLISHED_ON"):
            kit_app(UI_PUBLISH=wide)
    assert kit_app(UI_PUBLISH="0.0.0.0:8080", UI_AUTH="basic", BASIC_AUTH_USER="me",
                   BASIC_AUTH_PASSWORD="a-long-password").auth_mode == "basic"


def test_publish_reads_the_share_token_the_ui_makes():
    """Review M3: the share port wants a token on every request; the ui makes
    it on its volume and publish mounts that volume read-only."""
    s = config()
    ui, pub = s["ui"], s["publish"]
    made = ui["environment"]["ANALYSIS_SHARE_TOKEN_FILE"]
    ui_vol = next(v for v in ui["volumes"] if v["target"] == "/data")
    pub_vol = next(v for v in pub["volumes"] if v.get("source") == ui_vol["source"])
    assert pub_vol.get("read_only") is True
    assert pub["environment"]["ANALYSIS_SHARE_TOKEN_FILE"] == pub_vol["target"] + made[len("/data"):]
    assert pub["environment"]["ANALYSIS_SHARE_URL"] == f"http://ui:{ui['environment']['ANALYSIS_SHARE_PORT']}/analysis.json"
    # Never published on the host.
    assert all(str(p.get("target")) != ui["environment"]["ANALYSIS_SHARE_PORT"] for p in ui.get("ports", []))
