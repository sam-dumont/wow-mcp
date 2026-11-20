"""A person's page settings, as the full view's share port lists them
(/users.json, wowstats.tenancy.Tenancy.users_json): the public page on or off
and the characters hidden from each bundle (Settings, wowstats.account).

The publisher builds each site with them (publish.publish_all). The live
public view (`VIEW=public`, the compose kit's `ui-public`) answers with them
too when it has `USERS_URL`: the public page off answers 404 everywhere, as
the static server does for a site built with it off, and a hidden character
doesn't exist there (2026-11-20 server review, LOW-2: it showed both).
Without `USERS_URL` there are no settings (one person, no Settings page) and
the live view shows every character, as before.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import dataclass, field

REFRESH_S = 30.0


class SettingsError(Exception):
    pass


@dataclass(frozen=True)
class PageSettings:
    public: bool = True
    hide_public: frozenset = field(default_factory=frozenset)
    hide_zogzog: frozenset = field(default_factory=frozenset)

    @classmethod
    def of(cls, u: dict) -> PageSettings:
        """One line of /users.json. An inactive person has no site at all."""
        return cls(public=bool(u.get("public_on")) and u.get("status", "active") == "active",
                   hide_public=frozenset(u.get("hidden_public") or ()),
                   hide_zogzog=frozenset(u.get("hidden_zogzog") or ()))


OFF = PageSettings(public=False)


def fetch_users(env: dict, timeout: float = 15) -> list[dict]:
    """The people with a site, from the full view's share port (USERS_URL):
    wowstats.tenancy.Tenancy.users_json. No hashes or keys come over it."""
    from wowstats import guard

    with guard.share_open(env["USERS_URL"], env, timeout=timeout) as r:
        data = json.loads(r.read(4_000_000))
    users = data.get("users") if isinstance(data, dict) else None
    if not isinstance(users, list):
        raise SettingsError("USERS_URL: no users list")
    return users


class Live:
    """The settings of one person (their tenant's account id) for the live
    public view, read again every REFRESH_S. A failed read keeps the last
    answer; with none yet the page counts as off: a hidden character never
    shows because the full view was restarting."""

    def __init__(self, env: dict, account_id: int, refresh_s: float = REFRESH_S):
        self.env, self.account_id, self.refresh_s = env, int(account_id), refresh_s
        self._got: PageSettings | None = None
        self._at = 0.0
        self._lock = threading.Lock()

    def get(self) -> PageSettings:
        with self._lock:
            if self._at and time.monotonic() - self._at < self.refresh_s:
                return self._got or OFF
            try:
                line = next((u for u in fetch_users(self.env, timeout=5)
                             if str(u.get("account_id")) == str(self.account_id)), None)
                self._got = OFF if line is None else PageSettings.of(line)
            except Exception as e:  # noqa: BLE001 - the share port down: the last answer, else off
                print(f"public view: the settings at USERS_URL didn't answer ({type(e).__name__}); "
                      f"{'keeping the last ones' if self._got else 'page off until they do'}",
                      file=sys.stderr, flush=True)
            self._at = time.monotonic()
            return self._got or OFF


def live(env: dict, account_id: int) -> Live | None:
    """Live settings when the view has USERS_URL, else None (show everything)."""
    return Live(env, account_id) if (env.get("USERS_URL") or "").strip() else None
