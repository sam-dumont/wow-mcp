"""The weights' mode each character's gear is scored in, kept on the server.

Leveling, PvP or PvE (wowstats.gear.MODES), Leveling when nothing is saved.
The gear page's picker writes it (POST /api/gear-mode); the page, the MCP's
`gear` tool (unless its `mode` argument says otherwise for that call) and the
sealed site's gear answer read it. The planner, next_moves, the analysis and
the public doll's badges stay on Leveling: they rank leveling moves.

One file per person, STATE/gear-modes.json, STATE being ANALYSIS_STATE_DIR
(each person's own folder on a multi-user install, wowstats.tenancy) or
WOW_STATE_DIR. Without either nothing is kept: every answer is Leveling and
the page's pick lasts until it is reloaded.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
from pathlib import Path

from wowstats import gear

FILE = "gear-modes.json"
SCHEMA = 1
MAX_CHARS = 500  # a person's characters; a bigger file is somebody else's
_LOCK = threading.Lock()


class ModeError(Exception):
    pass


def path(env: dict) -> Path | None:
    d = (env.get("ANALYSIS_STATE_DIR") or env.get("WOW_STATE_DIR") or "").strip()
    return Path(d) / FILE if d else None


def _name(char: str) -> str:
    return str(char or "").strip().replace(" ", "-")


def _load(env: dict) -> dict[str, str]:
    p = path(env)
    if p is None:
        return {}
    with contextlib.suppress(OSError, ValueError):
        s = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(s, dict) and s.get("schema") == SCHEMA and isinstance(s.get("chars"), dict):
            return {str(k): gear.mode_key(v) for k, v in s["chars"].items()}
    return {}


def saved(env: dict, char: str) -> str:
    """The character's mode: what the page saved, else Leveling."""
    return _load(env).get(_name(char), gear.DEFAULT_MODE)


def save(env: dict, char: str, mode) -> str:
    """Keep `mode` for the character; Leveling (the default) clears it.
    -> the mode kept. ModeError when there is no state folder to keep it in."""
    p = path(env)
    if p is None:
        raise ModeError("this install keeps no settings (no ANALYSIS_STATE_DIR or WOW_STATE_DIR)")
    if str(mode or "").strip().lower() not in gear.MODE_NAMES:
        raise ModeError("mode: lvl, pvp or pve")
    key, name = gear.mode_key(mode), _name(char)
    with _LOCK:
        chars = _load(env)
        if key == gear.DEFAULT_MODE:
            chars.pop(name, None)
        else:
            chars[name] = key
        if len(chars) > MAX_CHARS:
            raise ModeError("too many characters")
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".gear-modes-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"schema": SCHEMA, "chars": dict(sorted(chars.items()))}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
    return key
