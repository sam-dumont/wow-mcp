"""A past level's picture, for the static bundles: drawn by the renderer
(`wow-stats render`, wowstats.renderer) at RENDER_URL and kept by what was
drawn. RENDER_URL empty: no pictures, and a past level shows its 3D model.

The publisher sends what a drawing needs, cut from the 3D view's answer for
that level (wowstats.model3d): the model (race and sex), the look and each
drawn item's inventory type and display id. No character name, no level, no
item id, no account. The picture is kept under
PUBLISH_DIR/pictures/<sha256 of that request and renderer.REV>.webp, beside
the builds, so a level is drawn once, and a level that looks like another
(the same gear and look) is never drawn twice.

Drawing costs seconds of CPU (software WebGL): a build spends at most
RENDER_BUDGET_S (60) on it, and the levels left are "pending" in the
snapshot's answer (the page says the picture is on its way). publish()
builds again at its next pass while some are pending: at once when the last
build drew one, RETRY_S later when it drew none (the renderer down).

The look comes from Battle.net's profile (vm-wow, the 30-day rule): a
picture is drawn again once it is MAX_AGE_S old, and one the last build
didn't use is removed.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from wowstats import bearer, renderer

MAX_AGE_S = 25 * 86400
RETRY_S = 900
EXT = "webp"


def request(answer: dict | None, metas: dict | None = None) -> dict | None:
    """The renderer's request for a 3D answer, or None when there is nothing
    to draw (no model: an unknown race, the viewer's files missing). `metas`:
    the items' metas Wowhead lacks (Model3D.stand_ins), sent along
    only when there are some, so every other picture keeps its key."""
    model = (answer or {}).get("model")
    if not isinstance(model, dict) or not model.get("chr_model"):
        return None
    req = {"model": {"chr_model": model["chr_model"], "gender": model.get("gender", 0),
                     "options": [{"optionId": o["optionId"], "choiceId": o["choiceId"]}
                                 for o in model.get("options") or []]},
           "items": [[i["inventory_type"], i["display_id"]] for i in answer.get("items") or []
                     if i.get("inventory_type") and i.get("display_id")]}
    if metas:
        req["metas"] = {str(k): v for k, v in metas.items()}
    return req


def key(req: dict) -> str:
    return hashlib.sha256(json.dumps([renderer.REV, req], sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Pictures:
    def __init__(self, env: dict, out: Path, log=print):
        self.url = (env.get("RENDER_URL") or "").strip().rstrip("/")
        self.token = bearer.client_token(env, "RENDER_TOKEN_FILE") if self.url else None
        if self.url and not self.token:
            log("pictures: RENDER_URL is set but RENDER_TOKEN_FILE isn't: the renderer refuses requests without "
                "its token, so no pictures. Set RENDER_TOKEN_FILE to a file holding the renderer's token.")
        self.on = bool(self.url and self.token)
        self.dir = out / "pictures"
        self.budget = float(env.get("RENDER_BUDGET_S") or 60)
        self.timeout = float(env.get("RENDER_TIMEOUT_S") or 120)
        self.log = log
        self.spent = 0.0
        self.drawn = 0
        self.down = False  # the renderer failed this run: no more asks
        self.used: set[str] = set()
        self.pending: set[str] = set()

    def get(self, answer: dict | None, metas: dict | None = None) -> bytes | None | str:
        """The picture's bytes, "pending" (not drawn yet, or the renderer
        down), or None (off, or nothing to draw)."""
        if not self.on:
            return None
        req = request(answer, metas)
        if req is None:
            return None
        k = key(req)
        self.used.add(k)
        f = self.dir / f"{k}.{EXT}"
        try:
            if time.time() - f.stat().st_mtime < MAX_AGE_S:
                return f.read_bytes()
        except OSError:
            pass
        if self.down or self.spent >= self.budget:
            self.pending.add(k)
            return "pending"
        t0 = time.time()
        body = self._ask(req)
        self.spent += time.time() - t0
        if body is None:
            self.pending.add(k)
            return "pending"
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".part")
        tmp.write_bytes(body)
        os.replace(tmp, f)
        self.drawn += 1
        self.pending.discard(k)
        return body

    def _ask(self, req: dict) -> bytes | None:
        r = urllib.request.Request(self.url + "/render", data=json.dumps(req).encode(), method="POST",
                                   headers={"Content-Type": "application/json",
                                            "Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(r, timeout=self.timeout) as resp:  # noqa: S310 - our own env's address
                body = resp.read(8 * 1024 * 1024)
        except urllib.error.HTTPError as e:
            self.log(f"picture: the renderer answered {e.code}: {e.read(200).decode(errors='replace').strip()}")
            self.down = e.code != 400  # a request it refuses stays refused; anything else: try next pass
            return None
        except (OSError, TimeoutError) as e:
            self.log(f"picture: the renderer is unreachable: {e}")
            self.down = True
            return None
        if body[:4] != b"RIFF" or body[8:12] != b"WEBP":
            self.log("picture: the renderer didn't answer a WebP image")
            self.down = True
            return None
        return body

    def prune(self) -> int:
        """Remove the pictures no answer of this build asked for."""
        n = 0
        # A build that asked for none (an empty store, every character
        # hidden) keeps them: the next one likely wants them back.
        if not self.on or not self.used or not self.dir.is_dir():
            return n
        for f in self.dir.iterdir():
            if (f.suffix == f".{EXT}" and f.stem not in self.used) or f.suffix == ".part":
                try:
                    f.unlink()
                    n += 1
                except OSError:
                    pass
        return n

    def state(self) -> dict:
        """What publish() keeps between passes: how many are pending and
        when to try again."""
        if not self.pending:
            return {"pictures_pending": 0, "pictures_retry_at": 0}
        return {"pictures_pending": len(self.pending),
                "pictures_retry_at": time.time() + (0 if self.drawn else RETRY_S)}
