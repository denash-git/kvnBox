"""Временный сервер замера скорости через 443 (веб-лаунчер): за скрытым путём Caddy.

GET  /down?mb=N — N МБ случайных данных (не сжимаются); GET /ping — пустой ответ;
POST /up        — тело читается и отбрасывается, ответ — число принятых байт.
Только 127.0.0.1; запускается на время замера (`systemd-run`, ограничение по времени).
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

CHUNK = os.urandom(256 * 1024)
MAX_MB = 1000


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path.rstrip("/") == "/ping":  # задержка: пустой ответ
            self.send_response(204)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if url.path.rstrip("/") != "/down":
            self.send_error(404)
            return
        try:
            mb = max(1, min(int(parse_qs(url.query).get("mb", ["100"])[0]), MAX_MB))
        except ValueError:
            mb = 100
        total = mb * 1024 * 1024
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(total))
        self.end_headers()
        sent = 0
        try:
            while sent < total:
                part = CHUNK[: min(len(CHUNK), total - sent)]
                self.wfile.write(part)
                sent += len(part)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self) -> None:
        if urlparse(self.path).path.rstrip("/") != "/up":
            self.send_error(404)
            return
        left = int(self.headers.get("Content-Length") or 0)
        got = 0
        while left > 0:
            data = self.rfile.read(min(left, 256 * 1024))
            if not data:
                break
            got += len(data)
            left -= len(data)
        body = json.dumps({"bytes": got}).encode()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def main(argv: list[str]) -> int:
    port = int(argv[1]) if len(argv) > 1 else 10020
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
