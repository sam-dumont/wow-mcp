"""A past level's picture, and the paperdoll's still of the character as it
is now (wowstats.armory), for the static bundles: drawn by the renderer
(`wow-stats render`, wowstats.renderer) at RENDER_URL and kept by what was
drawn. RENDER_URL empty: no pictures, and a past level shows its 3D model.

The publisher sends the 3D view's recipe for that level (wowstats.model3d,
the updated model) and, when the renderer answers 428 with the files it
lacks, those files from the person's own store (POST /file/<id>, with their
SHA-256), then asks again. A file the store doesn't have either goes in
the next request's `absent`, so it is drawn without it. No character name,
no level, no item id, no account: the person's files are kept apart on the
renderer under an opaque `scope` (scope(), a hash of the tenant), so one
person's upload never draws another's picture. The picture is kept under
PUBLISH_DIR/pictures/<sha256 of the recipe and renderer.REV>.webp, beside
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
from collections.abc import Callable
from pathlib import Path

from wowstats import bearer, renderer

MAX_AGE_S = 25 * 86400
RETRY_S = 900
EXT = "webp"
# Rounds of 428 and uploads before giving up: a model, then its skin and
# skeleton, then the parent skeleton and its animations.
ROUNDS = 8


def request(answer: dict | None) -> dict | None:
    """The renderer's request for a 3D answer, or None when there is nothing
    to draw (no recipe: the race unknown, the game files not sent yet)."""
    recipe = (answer or {}).get("recipe")
    return {"recipe": recipe} if isinstance(recipe, dict) and recipe.get("model") else None


def scope(env: dict) -> str:
    """Whose files the renderer keeps these under: a hash of the tenant
    (wowstats.stores.tenant_of), never the account itself. Each person's
    files come from their own upload, checked for their kind, not for being
    the game's (wowstats.assets)."""
    from wowstats.stores import tenant_of

    return hashlib.sha256(f"wow-stats render scope {tenant_of(env)}".encode()).hexdigest()[:32]


def key(req: dict, scope: str = "") -> str:
    """The picture's name: the renderer's revision and the recipe (the race,
    look and gear), plus `scope` when the picture belongs to one character
    (the paperdoll's still: tenant and character, wowstats.publish.put_still)."""
    parts = [renderer.REV, req, scope] if scope else [renderer.REV, req]
    return hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Pictures:
    def __init__(self, env: dict, out: Path, log=print):
        self.url = (env.get("RENDER_URL") or "").strip().rstrip("/")
        self.token = bearer.client_token(env, "RENDER_TOKEN_FILE") if self.url else None
        if self.url and not self.token:
            log("pictures: RENDER_URL is set but RENDER_TOKEN_FILE isn't: the renderer refuses requests without "
                "its token, so no pictures. Set RENDER_TOKEN_FILE to a file holding the renderer's token.")
        self.on = bool(self.url and self.token)
        self.scope = scope(env)
        self.dir = out / "pictures"
        self.budget = float(env.get("RENDER_BUDGET_S") or 60)
        self.timeout = float(env.get("RENDER_TIMEOUT_S") or 120)
        self.log = log
        self.spent = 0.0
        self.drawn = 0
        self.down = False  # the renderer failed this run: no more asks
        self.used: set[str] = set()
        self.pending: set[str] = set()
        self._this_run: dict[str, bytes] = {}  # recipe key -> drawn in this run

    def get(self, answer: dict | None, read: Callable[[int], bytes | None] | None = None,
            scope: str = "") -> bytes | None | str:
        """The picture's bytes, "pending" (not drawn yet, or the renderer
        down), or None (off, or nothing to draw). `read`: file id -> its
        bytes from the person's store, for the renderer's 428s. `scope`:
        see key()."""
        if not self.on:
            return None
        req = request(answer)
        if req is None:
            return None
        k, same = key(req, scope), key(req)
        self.used.add(k)
        f = self.dir / f"{k}.{EXT}"
        # The same drawing kept under another name (a level's picture, the
        # still of the same look): copied, not drawn again.
        for src in dict.fromkeys((f, self.dir / f"{same}.{EXT}")):
            try:
                at = src.stat().st_mtime
                if time.time() - at < MAX_AGE_S:
                    body = src.read_bytes()
                    if src != f:
                        self._keep(f, body, at)  # its age too: MAX_AGE_S counts from the drawing
                    return body
            except OSError:
                pass
        if same in self._this_run:
            self._keep(f, self._this_run[same])
            return self._this_run[same]
        if self.down or self.spent >= self.budget:
            self.pending.add(k)
            return "pending"
        t0 = time.time()
        body = self._ask(req, read or (lambda fdid: None))
        self.spent += time.time() - t0
        if body is None:
            self.pending.add(k)
            return "pending"
        self._keep(f, body)
        self._this_run[same] = body
        self.drawn += 1
        self.pending.discard(k)
        return body

    def _keep(self, f: Path, body: bytes, at: float | None = None) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".part")
        tmp.write_bytes(body)
        if at is not None:
            os.utime(tmp, (at, at))
        os.replace(tmp, f)

    def _post(self, path: str, body: bytes, headers: dict) -> tuple[int, bytes]:
        r = urllib.request.Request(self.url + path, data=body, method="POST",
                                   headers={**headers, "Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(r, timeout=self.timeout) as resp:  # noqa: S310 - our own env's address
                return resp.status, resp.read(8 * 1024 * 1024)
        except urllib.error.HTTPError as e:
            return e.code, e.read(64 * 1024)

    def _ask(self, req: dict, read: Callable[[int], bytes | None]) -> bytes | None:
        """POST /render; on 428, send the files it lacks and ask again."""
        absent: set[int] = set()
        try:
            for _ in range(ROUNDS):
                code, body = self._post("/render", json.dumps({**req, "absent": sorted(absent),
                                                                "scope": self.scope}).encode(),
                                        {"Content-Type": "application/json"})
                if code == 428:
                    try:
                        lack = [int(x) for x in json.loads(body)["files"]]
                    except (ValueError, KeyError, TypeError):
                        lack = []
                    if not lack:
                        break
                    for fdid in lack:
                        data = read(fdid)
                        if data is None:
                            absent.add(fdid)
                            continue
                        sha = hashlib.sha256(data).hexdigest()
                        c, msg = self._post(f"/file/{fdid}", data, {"Content-Type": "application/octet-stream",
                                                                     "X-Content-SHA256": sha,
                                                                     "X-Render-Scope": self.scope})
                        if c != 204:
                            absent.add(fdid)  # refused (not a game file): drawn without it
                            self.log(f"picture: the renderer refused file {fdid} ({c}): "
                                     f"{msg[:200].decode(errors='replace').strip()}")
                    continue
                if code != 200:
                    self.log(f"picture: the renderer answered {code}: {body[:200].decode(errors='replace').strip()}")
                    self.down = code != 400  # a request it refuses stays refused; anything else: try next pass
                    return None
                if body[:4] != b"RIFF" or body[8:12] != b"WEBP":
                    self.log("picture: the renderer didn't answer a WebP image")
                    self.down = True
                    return None
                return body
        except (OSError, TimeoutError) as e:
            self.log(f"picture: the renderer is unreachable: {e}")
            self.down = True
            return None
        self.log(f"picture: the renderer still lacked files after {ROUNDS} rounds")
        return None

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
