import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from opennote.notebooks import NotebookManager


class _LoopbackHandler(BaseHTTPRequestHandler):
    """Serves whatever the owning server's ``routes`` map says, and records hits."""

    protocol_version = "HTTP/1.1"

    def do_GET(self):
        server = self.server
        server.requests.append(self.path)
        status, headers, body = server.routes.get(self.path, (404, {}, b"not found"))
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, *args):  # silence per-request stderr noise
        pass


class LoopbackServer(ThreadingHTTPServer):
    """127.0.0.1-only HTTP server for exercising real redirect/serving behaviour.

    Binds the loopback interface on an ephemeral port, so it never leaves the
    machine and needs no API key. Use it when a test must exercise a real socket
    (a genuine ``302 Location`` header, real streaming) rather than a stub written
    to match the implementation's own assumption.
    """

    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _LoopbackHandler)
        self.routes = {}  # path -> (status, headers dict, body bytes)
        self.requests = []  # paths requested, in order

    def handle_error(self, request, client_address):
        """Swallow the connection abort caused by a client closing mid-stream.

        ``_next_redirect`` deliberately closes the connection after reading the
        status line and headers, so the server always sees this. It is expected,
        not a failure, and must not spam test output.
        """

    def route(self, path, status=200, headers=None, body=b""):
        self.routes[path] = (status, headers or {}, body)
        return self.url(path)

    def url(self, path="/"):
        host, port = self.server_address[:2]
        return f"http://{host}:{port}{path}"


@pytest.fixture
def loopback_http():
    server = LoopbackServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)



@pytest.fixture
def notebook_manager(tmp_path, monkeypatch):
    home = tmp_path / "opennote_home"
    monkeypatch.setenv("OPENNOTE_HOME", str(home))
    return NotebookManager(home=home)


@pytest.fixture
def stub_embedder(monkeypatch):
    """Replace SentenceTransformer with a tiny deterministic stub.

    Makes store/pipeline tests fast and free of HF model downloads while still
    exercising ChromaDB persistence, manifests, and the mismatch guard.
    """

    class DummySentenceTransformer:
        def __init__(self, model_name, device=None, **kwargs):
            self.model_name = model_name

        def encode(self, texts, **kwargs):
            n = len(texts) if isinstance(texts, list) else 1
            return np.random.RandomState(7).rand(n, 16)

    import sentence_transformers

    monkeypatch.setattr(
        sentence_transformers, "SentenceTransformer", DummySentenceTransformer
    )
    return DummySentenceTransformer