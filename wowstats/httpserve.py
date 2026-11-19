"""The HTTP server every wow-stats listener uses (ui, static, render).

The standard library's ThreadingHTTPServer has no socket timeout and starts
one thread per connection with no ceiling: a client that sends its headers
one byte at a time, or opens thousands of idle connections, held a thread
and a file descriptor each, before any login was checked (security review
L8). Here:

- a connection that sends or reads nothing for TIMEOUT seconds is closed;
- at most MAX_CONNECTIONS are served at once; one more is closed at once,
  so a flood can't take the process's memory or file descriptors with it.

A reverse proxy in front with its own limits is still the real defence for
anything open to the internet (docs/security.md).
"""

from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer


class Server(ThreadingHTTPServer):
    # A page asks for dozens of files at once (icons, sealed data, the 3D
    # files): the stock listen backlog of 5 made the kernel reset some.
    request_queue_size = 128
    daemon_threads = True
    TIMEOUT = 30.0
    MAX_CONNECTIONS = 256

    def __init__(self, *a, **k):
        self._slots = threading.BoundedSemaphore(self.MAX_CONNECTIONS)
        super().__init__(*a, **k)

    def get_request(self):
        sock, addr = super().get_request()
        sock.settimeout(self.TIMEOUT)
        return sock, addr

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)  # full: closed now, not queued behind the slow ones
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()
