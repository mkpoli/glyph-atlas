"""Shared test fixtures.

`http_server` stands in for a remote host: it serves files from a directory, honours `Range` with a
206 and `Content-Range`, and can be told to answer a path with a scripted sequence of responses
(used for 429 and 503 retry tests) or to ignore ranges. No test reaches the network.
"""

from __future__ import annotations

import http.server
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


@dataclass
class Scripted:
    """A canned response used instead of the file on disk."""

    status: int
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)


class TestServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root: Path) -> None:
        super().__init__(("127.0.0.1", 0), Handler)
        self.root = root
        self.script: dict[str, list[Scripted]] = {}
        self.ignore_ranges = False
        self.requests: list[str] = []

    @property
    def base_url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"

    def url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def put(self, path: str, data: bytes) -> Path:
        target = self.root / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def next_scripted(self, path: str) -> Scripted | None:
        queue = self.script.get(path)
        if not queue:
            return None
        item = queue.pop(0)
        if not queue:
            del self.script[path]
        return item


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:  # keep the test output clean
        pass

    def _file_path(self) -> Path | None:
        path = self.path.split("?", 1)[0]
        target = (self.server.root / path.lstrip("/")).resolve()
        if not str(target).startswith(str(self.server.root.resolve())):
            return None
        return target if target.is_file() else None

    def _send(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _respond(self, body_only_status: int = 200) -> None:
        path = self.path.split("?", 1)[0]
        self.server.requests.append(f"{self.command} {path}")
        scripted = self.server.next_scripted(path)
        if scripted is not None:
            self._send(scripted.status, scripted.body, dict(scripted.headers))
            return
        target = self._file_path()
        if target is None:
            self._send(404, b"not found", {"Content-Type": "text/plain"})
            return
        data = target.read_bytes()
        suffix = target.suffix.lower()
        content_type = {
            ".json": "application/json",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".zip": "application/zip",
            ".txt": "text/plain; charset=utf-8",
        }.get(suffix, "application/octet-stream")
        headers = {"Content-Type": content_type, "Accept-Ranges": "bytes", "ETag": f'"{len(data)}"'}
        if self.server.ignore_ranges:
            headers.pop("Accept-Ranges")
            self._send(body_only_status, data, headers)
            return
        match = RANGE_RE.match(self.headers.get("Range", ""))
        if match:
            start_s, end_s = match.groups()
            if start_s == "":
                start = max(0, len(data) - int(end_s or 0))
                end = len(data) - 1
            else:
                start = int(start_s)
                end = min(int(end_s), len(data) - 1) if end_s else len(data) - 1
            if start >= len(data):
                self._send(416, b"", {"Content-Range": f"bytes */{len(data)}"})
                return
            chunk = data[start : end + 1]
            headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
            self._send(206, chunk, headers)
            return
        self._send(200, data, headers)

    def do_GET(self) -> None:
        self._respond()

    def do_HEAD(self) -> None:
        target = self._file_path()
        self.server.requests.append(f"HEAD {self.path.split('?', 1)[0]}")
        if target is None:
            self._send(404, b"", {"Content-Type": "text/plain"})
            return
        self._send(200, b"", {"Content-Type": "application/octet-stream", "Accept-Ranges": "bytes"})


@pytest.fixture
def http_server(tmp_path: Path):
    server = TestServer(tmp_path / "served")
    server.root.mkdir(parents=True, exist_ok=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
