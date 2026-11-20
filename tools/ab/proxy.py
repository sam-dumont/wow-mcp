"""The A/B check's store proxy: records what the app asks VictoriaLogs and
VictoriaMetrics, then answers the same questions from the recording.

record  forwards each request to the real store and keeps the request and
        the answer (one JSON line each, gzip) in a cassette.
replay  answers from the cassette only: nothing leaves the machine. A request
        the cassette lacks gets a 502 that names it as a miss (the run's
        misses list says which), never a guess.
extend  replay, and a miss is forwarded and added (tagged `added_by`): a
        newer version's own queries, recorded at the same frozen instant soon
        after the recording, so its answers can be compared too.

Read-only and one person only, whatever asks: only the read routes pass
(VictoriaLogs /select/logsql/*, VictoriaMetrics /api/v1/query, query_range,
series, labels, label values), and a request for any VictoriaLogs tenant
but the default one (AccountID 0, ProjectID 0) is refused. Writes and
deletes never reach a store.

Matching: the exact request first (method, path, every parameter, the body,
the tenant). Same request asked again: the next recorded answer for it, then
the last. A request that differs only in its timestamps, by at most
FUZZY_MAX_S in all (a range bound that moved because a clock ticked), gets
the recorded one nearest in time, counted apart as a fuzzy hit; a different
range is a miss.

Standard library only."""

from __future__ import annotations

import base64
import gzip
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ALLOWED = {
    "vl": (re.compile(r"^/select/logsql/[a-z_]+$"),),
    "vm": (re.compile(r"^/api/v1/(query|query_range|series|labels|status/buildinfo)$"),
           re.compile(r"^/api/v1/label/[A-Za-z0-9_.:-]+/values$")),
}
TENANT_HEADERS = ("AccountID", "ProjectID")
TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?")
TIME_PARAMS = ("start", "end", "time")
UPSTREAM_TIMEOUT = 60
# How far apart, in seconds summed over a request's timestamps, a fuzzy
# match may be: a clock that ticked during a run, never another range.
FUZZY_MAX_S = 5
MAX_BODY = 1 << 20


class Refused(Exception):
    pass


def check(store: str, method: str, path: str, tenant: dict) -> None:
    """Raise Refused unless this is a read of the default tenant."""
    if store not in ALLOWED:
        raise Refused(f"unknown store {store!r}")
    if method not in ("GET", "POST"):
        raise Refused(f"{method} is not a read")
    if not any(rx.match(path) for rx in ALLOWED[store]):
        raise Refused(f"{path} is not a read route of {store}")
    for h in TENANT_HEADERS:
        v = tenant.get(h)
        if v not in (None, "", "0"):
            raise Refused(f"{h}={v}: only the default tenant (0) is recorded or replayed")


def key_of(e: dict) -> str:
    """What makes two requests the same. Not the tenant: only the default
    one passes check(), with or without its headers."""
    return json.dumps([e["store"], e["method"], e["path"], sorted(map(list, e["query"])), e.get("body")],
                      ensure_ascii=False)


def _mask(k: str, v: str) -> str:
    if k in TIME_PARAMS and re.fullmatch(r"-?\d+(\.\d+)?", v or ""):
        return "<t>"
    return TS_RE.sub("<t>", v or "")


def fuzzy_key_of(e: dict) -> str:
    q = sorted([k, _mask(k, v)] for k, v in e["query"])
    body = TS_RE.sub("<t>", e["body"]) if e.get("body") else e.get("body")
    return json.dumps([e["store"], e["method"], e["path"], q, body], ensure_ascii=False)


def _times(e: dict) -> list[float]:
    out = []
    for k, v in e["query"]:
        if k in TIME_PARAMS and re.fullmatch(r"-?\d+(\.\d+)?", v or ""):
            out.append(float(v))
            continue
        for m in TS_RE.findall(v or ""):
            try:
                t = datetime.fromisoformat(m.replace("Z", "+00:00").replace(" ", "T"))
            except ValueError:
                continue
            out.append((t if t.tzinfo else t.replace(tzinfo=UTC)).timestamp())
    return out


def _distance(a: dict, b: dict) -> float:
    ta, tb = _times(a), _times(b)
    if len(ta) != len(tb):
        return float("inf")
    return sum(abs(x - y) for x, y in zip(ta, tb, strict=True))


class _Headers:
    """Case-insensitive header lookup over a dict or the request's headers."""

    def __init__(self, h):
        self.h = {str(k).lower(): v for k, v in (h.items() if h is not None else [])}

    def get(self, k: str, default=None):
        return self.h.get(k.lower(), default)


class Cassette:
    """The recorded requests and answers, in order."""

    def __init__(self, entries: list[dict] | None = None):
        self.entries = list(entries or [])
        self._lock = threading.Lock()
        self._index()

    def _index(self) -> None:
        self.exact: dict[str, list[dict]] = {}
        self.fuzzy: dict[str, list[dict]] = {}
        for e in self.entries:
            self.exact.setdefault(key_of(e), []).append(e)
            self.fuzzy.setdefault(fuzzy_key_of(e), []).append(e)
        self.served: dict[str, int] = {}

    @classmethod
    def load(cls, path: Path) -> Cassette:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return cls([json.loads(line) for line in f if line.strip()])

    def save(self, path: Path) -> None:
        tmp = path.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as f:
            for e in self.entries:
                f.write(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n")
        tmp.replace(path)

    def add(self, e: dict) -> None:
        with self._lock:
            self.entries.append(e)
            self.exact.setdefault(key_of(e), []).append(e)
            self.fuzzy.setdefault(fuzzy_key_of(e), []).append(e)

    def find(self, req: dict) -> tuple[dict | None, str]:
        """(entry, "exact" | "fuzzy" | "miss")."""
        with self._lock:
            k = key_of(req)
            hits = self.exact.get(k)
            if hits:
                i = self.served.get(k, 0)
                self.served[k] = i + 1
                return hits[min(i, len(hits) - 1)], "exact"
            near = self.fuzzy.get(fuzzy_key_of(req))
            if near:
                best = min(near, key=lambda e: _distance(e, req))
                if _distance(best, req) <= FUZZY_MAX_S:
                    return best, "fuzzy"
            return None, "miss"


def body_of(e: dict) -> bytes:
    if "resp_b64" in e:
        return base64.b64decode(e["resp_b64"])
    return (e.get("resp") or "").encode("utf-8")


def _answer(status: int, ctype: str, data: bytes) -> dict:
    out = {"status": status, "ctype": ctype}
    try:
        out["resp"] = data.decode("utf-8")
    except UnicodeDecodeError:
        out["resp_b64"] = base64.b64encode(data).decode("ascii")
    return out


class Proxy:
    """One listener per store, all sharing one cassette and one log."""

    def __init__(self, mode: str, cassette: Cassette, upstreams: dict[str, str] | None = None, tag: str = ""):
        if mode not in ("record", "replay", "extend"):
            raise ValueError(mode)
        self.mode = mode
        self.tag = tag
        self.cassette = cassette
        self.upstreams = {k: v.rstrip("/") for k, v in (upstreams or {}).items()}
        self.stats = {"exact": 0, "fuzzy": 0, "miss": 0, "refused": 0, "recorded": 0, "upstream_errors": 0}
        self.misses: list[dict] = []
        self.fuzzies: list[dict] = []
        self.refusals: list[dict] = []
        self._lock = threading.Lock()
        self.servers: dict[str, ThreadingHTTPServer] = {}

    def _bump(self, k: str) -> None:
        with self._lock:
            self.stats[k] += 1

    def handle(self, store: str, method: str, raw_path: str, headers,
               body: bytes | None) -> tuple[int, str, bytes]:
        """headers: case-insensitive (the request's own, or a dict with
        exact names): urllib sends "Accountid", VictoriaLogs reads any case."""
        headers = _Headers(headers)
        u = urllib.parse.urlsplit(raw_path)
        query = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
        tenant = {h: headers.get(h) for h in TENANT_HEADERS if headers.get(h) is not None}
        text_body = body.decode("utf-8", "replace") if body else None
        if method == "POST" and text_body and "x-www-form-urlencoded" in (headers.get("Content-Type") or ""):
            query = query + urllib.parse.parse_qsl(text_body, keep_blank_values=True)
            text_body = None
        req = {"store": store, "method": method, "path": u.path, "query": [list(p) for p in query],
               "body": text_body, "tenant": tenant}
        try:
            check(store, method, u.path, tenant)
        except Refused as e:
            self._bump("refused")
            with self._lock:
                self.refusals.append({**req, "why": str(e)})
            return 403, "text/plain", f"ab-proxy refused: {e}\n".encode()
        if self.mode in ("replay", "extend"):
            hit, how = self.cassette.find(req)
            if hit is None and self.mode == "extend":
                return self._forward(store, req, headers, body)
            self._bump(how)
            if how == "fuzzy":
                with self._lock:
                    self.fuzzies.append({"path": req["path"], "query": fuzzy_key_of(req)[:400],
                                         "seconds_apart": _distance(hit, req)})
            if hit is None:
                with self._lock:
                    self.misses.append(req)
                return 502, "text/plain", b"ab-replay: this request is not in the recording\n"
            return int(hit["status"]), hit.get("ctype") or "application/json", body_of(hit)
        return self._forward(store, req, headers, body)

    def _forward(self, store: str, req: dict, headers: dict, body: bytes | None) -> tuple[int, str, bytes]:
        base = self.upstreams[store]
        qs = urllib.parse.urlencode([tuple(p) for p in req["query"]]) if req["query"] else ""
        url = f"{base}{req['path']}" + (f"?{qs}" if qs else "")
        # check() already holds the path to a store's read routes; the target
        # stays the store this run records, whatever the path held.
        want, got = urllib.parse.urlsplit(base), urllib.parse.urlsplit(url)
        if (got.scheme, got.netloc) != (want.scheme, want.netloc):
            return 403, "text/plain", b"ab-proxy refused: the request left the recorded store\n"
        fwd = {k: headers.get(k) for k in ("Accept", *TENANT_HEADERS) if headers.get(k) is not None}
        # A form body was folded into the query above; send everything as a GET's
        # parameters or the original body, never both.
        data = req["body"].encode("utf-8") if req["body"] is not None else None
        if data is not None and headers.get("Content-Type"):
            fwd["Content-Type"] = headers["Content-Type"]
        r = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET", headers=fwd)
        try:
            with urllib.request.urlopen(r, timeout=UPSTREAM_TIMEOUT) as resp:  # noqa: S310 - the operator's store
                status, ctype, out = resp.status, resp.headers.get("Content-Type") or "", resp.read()
        except urllib.error.HTTPError as e:
            status, ctype, out = e.code, e.headers.get("Content-Type") or "", e.read()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            self._bump("upstream_errors")
            return 502, "text/plain", f"ab-record: upstream {store}: {e}\n".encode()
        self.cassette.add({**req, **_answer(status, ctype, out), **({"added_by": self.tag} if self.tag else {})})
        self._bump("recorded")
        return status, ctype, out

    def start(self, store: str, host: str = "127.0.0.1", port: int = 0) -> str:
        proxy = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):  # the requests carry the person's data: no log lines
                return

            def _go(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(min(n, MAX_BODY)) if n else None
                status, ctype, out = proxy.handle(store, self.command, self.path, self.headers, body)
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            do_GET = do_POST = _go

            def do_PUT(self):
                self._go()

            do_DELETE = do_PATCH = do_PUT

        srv = ThreadingHTTPServer((host, port), H)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, name=f"ab-proxy-{store}", daemon=True).start()
        self.servers[store] = srv
        return f"http://{host}:{srv.server_address[1]}"

    def stop(self) -> None:
        for s in self.servers.values():
            s.shutdown()
            s.server_close()
        self.servers.clear()

    def report(self) -> dict:
        return {"mode": self.mode, **self.stats, "misses": self.misses[:200], "fuzzies": self.fuzzies[:200],
                "refusals": self.refusals[:200]}
