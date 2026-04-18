"""Stdlib HTTP exporter for the Prometheus registry.

Runs a background HTTP server on a configurable port and serves
/metrics by delegating to ``REGISTRY.render_prometheus()``.
No modification to the registry itself.
"""
from __future__ import annotations

import http.server
import threading
from typing import Optional

from python_brain.core.metrics import REGISTRY


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (stdlib API)
        if self.path not in ("/", "/metrics"):
            self.send_response(404)
            self.end_headers()
            return
        body = REGISTRY.render_prometheus().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:  # noqa: N802
        return  # silence per-request stderr logging


_server: Optional[http.server.ThreadingHTTPServer] = None
_thread: Optional[threading.Thread] = None


def start(port: int = 9100, host: str = "0.0.0.0") -> None:
    """Start the exporter. Idempotent; repeat calls are no-ops."""
    global _server, _thread
    if _server is not None:
        return
    _server = http.server.ThreadingHTTPServer((host, port), _Handler)
    _thread = threading.Thread(target=_server.serve_forever, name="metrics-http", daemon=True)
    _thread.start()


def stop() -> None:
    global _server, _thread
    if _server is None:
        return
    _server.shutdown()
    _server.server_close()
    _server = None
    _thread = None
