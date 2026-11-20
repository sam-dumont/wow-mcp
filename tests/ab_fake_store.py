"""A made-up VictoriaLogs and VictoriaMetrics for the A/B tool's tests
(tests/test_ab.py): fixed answers, every request counted."""

from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CHAR = "Testchar-Example"
EVENTS = [
    {"_time": "2026-01-01T10:00:00Z", "_msg": "level up", "feed": "wow-level", "char": CHAR, "level": 2, "xp": 0},
    {"_time": "2026-01-01T10:30:00Z", "_msg": "level up", "feed": "wow-level", "char": CHAR, "level": 3, "xp": 0},
]


class FakeStore:
    def __init__(self):
        self.requests: list[tuple[str, str, dict]] = []
        store = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                return

            def do_GET(self):
                u = urllib.parse.urlsplit(self.path)
                q = dict(urllib.parse.parse_qsl(u.query))
                store.requests.append((self.command, u.path, {**q, "_tenant": self.headers.get("AccountID")}))
                if u.path.startswith("/select/logsql/"):
                    rows = [] if "|" in q.get("query", "") else EVENTS
                    body = "".join(json.dumps(r) + "\n" for r in rows).encode()
                    ctype = "application/stream+json"
                elif u.path.startswith("/api/v1/"):
                    body = json.dumps({"status": "success", "data": {"resultType": "vector", "result": []}}).encode()
                    ctype = "application/json"
                else:
                    body, ctype = b"no", "text/plain"
                self.send_response(200 if body != b"no" else 404)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_POST = do_DELETE = do_GET

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


if __name__ == "__main__":  # a store to point ab.py at by hand
    import time

    s = FakeStore()
    print(s.url, flush=True)
    while True:
        time.sleep(3600)
