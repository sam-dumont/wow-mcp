"""A reverse proxy for the login tests, the way a deployment puts one in
front of the MCP port: it forwards every request with X-Forwarded-*, and can
drop the Cookie header on chosen paths, as a proxy rule that keeps cookies
away from an app does.

    with Proxy(upstream="http://127.0.0.1:8090", port=p, strip_cookies={"/register"}) as px:
        px.url  # http://127.0.0.1:<p>
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx2

HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-authorization",
       "proxy-connection", "host", "content-length"}


class Proxy:
    def __init__(self, upstream: str, port: int, strip_cookies=frozenset()):
        self.upstream, self.strip = upstream.rstrip("/"), frozenset(strip_cookies)
        self.seen: list[tuple[str, str, int]] = []  # (method, path, status)
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _forward(self):
                path = self.path.split("?", 1)[0]
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                headers = [(k, v) for k, v in self.headers.items() if k.lower() not in HOP]
                if path in proxy.strip:
                    headers = [(k, v) for k, v in headers if k.lower() != "cookie"]
                headers += [("X-Forwarded-Proto", "http"), ("X-Forwarded-Host", self.headers.get("Host", "")),
                            ("X-Forwarded-For", self.client_address[0])]
                with httpx2.Client(follow_redirects=False, timeout=15) as c:
                    r = c.request(self.command, proxy.upstream + self.path, headers=headers, content=body)
                proxy.seen.append((self.command, path, r.status_code))
                self.send_response(r.status_code)
                for k, v in r.headers.multi_items():
                    if k.lower() not in HOP and k.lower() != "content-encoding":
                        self.send_header(k, v)
                self.send_header("Content-Length", str(len(r.content)))
                self.end_headers()
                self.wfile.write(r.content)

            do_GET = do_POST = do_DELETE = do_HEAD = _forward

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.url = f"http://127.0.0.1:{port}"

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
