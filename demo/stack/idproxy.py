"""A local stand-in for Traefik and its login middleware (demo/record.sh):
one port per identity, each forwarding to the app with X-Forwarded-User and
X-Forwarded-Org set, and any the client sent dropped.

    uv run python demo/stack/idproxy.py <app port> <port>:<sub>:<org> ...
"""

import http.client
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DROP = {"x-forwarded-user", "x-forwarded-org", "host", "connection"}
HOP = {"transfer-encoding", "connection", "content-length"}


def handler(upstream: int, sub: str, org: str):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def go(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else None
            hdr = {k: v for k, v in self.headers.items() if k.lower() not in DROP}
            hdr.update({"X-Forwarded-User": sub, "X-Forwarded-Org": org, "Host": f"127.0.0.1:{upstream}"})
            c = http.client.HTTPConnection("127.0.0.1", upstream, timeout=60)
            c.request(self.command, self.path, body, hdr)
            r = c.getresponse()
            data = r.read()
            self.send_response(r.status)
            for k, v in r.getheaders():
                if k.lower() not in HOP:
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        do_GET = do_POST = do_HEAD = do_DELETE = go

    return H


def main() -> None:
    upstream = int(sys.argv[1])
    for arg in sys.argv[2:]:
        port, sub, org = arg.split(":")
        srv = ThreadingHTTPServer(("127.0.0.1", int(port)), handler(upstream, sub, org))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Event().wait()


if __name__ == "__main__":
    main()
