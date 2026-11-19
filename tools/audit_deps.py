"""Fails on a known vulnerability in any locked dependency (CI's audit job).

    uv run --frozen --only-group audit python tools/audit_deps.py

Exports uv.lock with every extra and group (hashes included) and runs
pip-audit on it, without installing anything. Advisories listed in
.github/vulnerability-allowlist.toml are let through, and only with a reason:
an entry without one fails the run before the audit starts.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = ROOT / ".github/vulnerability-allowlist.toml"
MIN_REASON = 20  # characters: a sentence, not "n/a"


def allowed(path: Path = ALLOWLIST) -> list[str]:
    """The advisory ids to ignore. Raises ValueError on an entry without an id or a real reason."""
    entries = tomllib.loads(path.read_text(encoding="utf-8")).get("ignore", []) if path.exists() else []
    ids = []
    for n, e in enumerate(entries, 1):
        vid = str(e.get("id", "")).strip()
        reason = str(e.get("reason", "")).strip()
        if not vid:
            raise ValueError(f"{path.name}: entry {n} has no id")
        if len(reason) < MIN_REASON:
            raise ValueError(f"{path.name}: {vid} needs a reason (a sentence a reviewer can check)")
        ids.append(vid)
    return ids


def main() -> int:
    try:
        ids = allowed()
    except ValueError as e:
        print(f"audit: {e}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        req = Path(tmp) / "requirements.txt"
        subprocess.run(["uv", "export", "--frozen", "--all-extras", "--all-groups", "--no-emit-project",
                        "--format", "requirements-txt", "--quiet", "--output-file", str(req)], cwd=ROOT, check=True)
        cmd = [sys.executable, "-m", "pip_audit", "--requirement", str(req), "--disable-pip", "--require-hashes",
               "--progress-spinner", "off", "--desc", "on"]
        for vid in ids:
            cmd += ["--ignore-vuln", vid]
        if ids:
            print(f"audit: ignoring {', '.join(ids)} (.github/vulnerability-allowlist.toml)")
        return subprocess.run(cmd, cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())
