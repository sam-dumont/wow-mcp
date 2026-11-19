"""Review L8: the stdlib servers had no socket timeout and no connection cap,
so a slow or idle client held a thread and a descriptor for good, before any
login. Each listener (ui, static, render) is attacked here for real."""

import socket
import threading
import time
import urllib.request

import pytest

from wowstats import httpserve, renderer, static, ui


@pytest.fixture
def tight(monkeypatch):
    monkeypatch.setattr(httpserve.Server, "TIMEOUT", 0.5)
    monkeypatch.setattr(httpserve.Server, "MAX_CONNECTIONS", 3)


def ui_server():
    app = ui.App({"BIND": "127.0.0.1", "AUTH": "none", "VIEW": "full", "MODEL3D": "off"})
    app.port = 0
    return ui.serve(app)


def static_server(tmp_path):
    return static.Server(("127.0.0.1", 0), static.make_handler(static.Site({"SITE_DIR": str(tmp_path)})))


class NoChromium:
    """The listener alone: the attack never reaches a drawing."""

    def alive(self) -> bool:
        return True


def render_server(tmp_path):
    return renderer.serve(NoChromium(), {"BIND": "127.0.0.1", "PORT": "0", "RENDER_CACHE_DIR": str(tmp_path)})


@pytest.fixture(params=["ui", "static", "render"])
def srv(request, tight, tmp_path):
    s = {"ui": ui_server, "static": lambda: static_server(tmp_path), "render": lambda: render_server(tmp_path)}[request.param]()
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()
    s.server_close()


def closed(c: socket.socket, within: float) -> bool:
    c.settimeout(within)
    try:
        return c.recv(1024) == b""
    except TimeoutError:
        return False
    except ConnectionResetError:
        return True


def test_a_slow_client_is_cut_off(srv):
    c = socket.create_connection(srv.server_address[:2])
    c.sendall(b"GET /healthz HTTP/1.1\r\nHo")  # and then nothing: slowloris
    assert closed(c, 3), "the half-sent request held its connection"
    c.close()


def test_idle_connections_are_capped(srv):
    idle = [socket.create_connection(srv.server_address[:2]) for _ in range(3)]
    time.sleep(0.1)
    extra = socket.create_connection(srv.server_address[:2])
    assert closed(extra, 0.4), "a fourth connection was served past the cap"
    for c in idle + [extra]:
        c.close()
    time.sleep(0.7)  # the idle ones time out and free their slots
    host, port = srv.server_address[:2]
    assert urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=3).status == 200
