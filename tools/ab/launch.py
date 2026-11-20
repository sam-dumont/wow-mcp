"""`python launch.py <wow-stats arguments>`: wow-stats with the A/B check's
frozen clock (abclock, from AB_FROZEN_AT). The driver starts `mcp-stdio` and
`publish` through it, inside the image or the checkout under test."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import abclock  # noqa: E402

abclock.install_from_env()

from wowstats.cli import main  # noqa: E402

sys.exit(main(sys.argv[1:]))
