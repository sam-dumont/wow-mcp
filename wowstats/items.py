"""Item names and qualities for the loot history.

Loot records carry item ids only. Forever's own ItemSparse table (wago.tools,
the `wowf` product, as wowstats.character3d reads Item for the 3D view) names
the items Forever changed and gives their quality; kept on disk, checked for a newer build
once a day at most. Without it (no network, wago.tools down, WOW_ITEMS=off),
Polestar's gear data names what it knows (wowstats.gear), then forever-db
(wowstats.forever), with no quality.

Environment: WOW_ITEMS (on | off), MODEL3D_CACHE_DIR (the cache folder, shared
with the 3D view), MODEL3D_WAGO_URL and MODEL3D_PRODUCT (the upstream, tests).
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import tempfile
import threading
import time
from pathlib import Path

DAY = 86400


class Items:
    def __init__(self, wago_url: str, product: str, cache: Path | None, on: bool = True):
        self.wago = wago_url.rstrip("/")
        self.product = product
        self.file = cache / "item-names.json" if cache else None
        self.on = on
        self.table: dict[int, tuple[str, int]] = {}
        self.build = ""
        self.checked = 0.0
        self.lock = threading.Lock()

    def get(self, item_id: int) -> tuple[str | None, int | None]:
        """(name, quality 0-5), either None when unknown."""
        self._load()
        hit = self.table.get(item_id)
        if hit:
            return hit
        from wowstats import forever, gear

        with contextlib.suppress(Exception):
            ref = gear.reference()
            info = ref.item(item_id) or {}
            return ((ref.data.get("names") or {}).get(str(item_id)) or forever.item_name(item_id),
                    info.get("quality"))
        return forever.item_name(item_id), None

    def _load(self) -> None:
        if not self.on or (self.table and time.time() - self.checked < DAY):
            return
        with self.lock:
            if self.table and time.time() - self.checked < DAY:
                return
            if not self.table and self.file and self.file.is_file():
                with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
                    d = json.loads(self.file.read_text(encoding="utf-8"))
                    self.table = {int(k): (v[0], int(v[1])) for k, v in d["items"].items()}
                    self.build, self.checked = d["build"], float(d["checked"])
                if self.table and time.time() - self.checked < DAY:
                    return
            from wowstats.upstream import Upstream, build_of, builds_url, fetch, table_url
            from wowstats.upstream import write_file as _write

            try:
                build = build_of(fetch(builds_url(self.wago, self.product)))
                if build != self.build or not self.table:
                    body = fetch(table_url(self.wago, "ItemSparse", build), timeout=120)
                    if not body:
                        raise Upstream(f"wago.tools has no ItemSparse table for {build}")
                    rows = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
                    self.table = {int(r["ID"]): (r.get("Display_lang") or "", int(r.get("OverallQualityID") or 0))
                                  for r in rows if (r.get("ID") or "").isdigit()}
                    self.build = build
            except (Upstream, ValueError, KeyError, TypeError):
                pass  # keep what we have (or the gear data's names); try again tomorrow
            self.checked = time.time()
            if self.file and self.table:
                with contextlib.suppress(OSError):
                    _write(self.file, json.dumps({"build": self.build, "checked": self.checked,
                                                  "items": {str(k): list(v) for k, v in self.table.items()}},
                                                 separators=(",", ":")).encode())


_shared: Items | None = None
_guard = threading.Lock()


def shared() -> Items:
    global _shared
    with _guard:
        if _shared is None:
            env = os.environ
            cache = Path(env.get("MODEL3D_CACHE_DIR") or Path(tempfile.gettempdir()) / "wow-stats-model3d")
            try:
                cache.mkdir(parents=True, exist_ok=True)
            except OSError:
                cache = None
            from wowstats.upstream import PRODUCT, WAGO_URL, allow

            _shared = Items(allow(env.get("MODEL3D_WAGO_URL", WAGO_URL)), env.get("MODEL3D_PRODUCT", PRODUCT), cache,
                            on=env.get("WOW_ITEMS", "on").strip().lower() not in ("off", "0", "false", "no"))
        return _shared
