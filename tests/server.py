"""Threaded HTTP servers on 127.0.0.1 for the Umbra end-to-end tests.

Each Server listens on a free port, maps paths to handlers and logs every request with a
monotonic timestamp, so tests can assert what Umbra sent and in what order.
"""
import http.server
import socket
import threading
import time
import urllib.parse
from http.cookies import SimpleCookie


class Request:
    def __init__(self, handler, body):
        url = urllib.parse.urlsplit(handler.path)
        self.t = time.monotonic()
        self.method = handler.command
        self.path = url.path
        self.query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        self.headers = handler.headers
        self.body = body
        jar = SimpleCookie(handler.headers.get('Cookie', ''))
        self.cookies = {name: morsel.value for name, morsel in jar.items()}

    def __repr__(self):
        return f'<{self.method} {self.path} {self.query or ""}>'


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        length = int(self.headers.get('Content-Length') or 0)
        self.req = Request(self, self.rfile.read(length) if length else b'')
        owner = self.server.owner
        owner.log.append(self.req)
        route = owner.routes.get(self.req.path)
        try:
            if route is None:
                self.reply(404, 'not found', 'text/plain')
            else:
                route(self)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Umbra hung up first, e.g. at its timeout

    do_POST = do_HEAD = do_GET

    def reply(self, status=200, body=b'', ctype='text/html; charset=utf-8', headers=()):
        """Sends a whole response; headers is a list of pairs so Set-Cookie can repeat."""
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def redirect(self, location, status=302, headers=()):
        self.reply(status, '', 'text/plain', [('Location', location), *headers])

    def stall(self):
        """Never answers (until the server closes)."""
        self.server.owner.closing.wait(120)


class Server:
    def __init__(self):
        self.routes = {}
        self.log = []
        self.closing = threading.Event()
        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.daemon_threads = True
        self.httpd.owner = self
        self.port = self.httpd.server_address[1]
        self.base = f'http://127.0.0.1:{self.port}'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, path, host='127.0.0.1'):
        return f'http://{host}:{self.port}{path}'

    def route(self, path):
        def register(fn):
            self.routes[path] = fn
            return fn
        return register

    def page(self, path, body, status=200, ctype='text/html; charset=utf-8', headers=()):
        self.routes[path] = lambda h: h.reply(status, body, ctype, headers)

    def requests(self, path=None):
        return [r for r in list(self.log) if path is None or r.path == path]

    def wait_for(self, path, timeout=20, count=1):
        """The count-th request for path, once it arrives."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.requests(path)) >= count:
                return self.requests(path)[count - 1]
            time.sleep(0.02)
        raise AssertionError(f'{len(self.requests(path))} of {count} requests for {path} within {timeout}s')

    def close(self):
        self.closing.set()
        self.httpd.shutdown()
        self.httpd.server_close()


def free_port():
    """A port with nothing listening on it, for unreachable-server cases."""
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]
