"""Synthetic-only upstream HTTP receiver, independent of gateway audit logs."""

from __future__ import annotations

import argparse
import json
import threading
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pii_gateway.sandbox.leaks import LeakScanner, load_canaries


@dataclass(frozen=True)
class Observation:
    canary_ids: frozenset[str]
    body_bytes: int
    status: int


class UpstreamSpy:
    """Observe requests in memory and retain counts/IDs, never bodies or keys.

    Not a privacy filter: deliberately leaked positive controls are recorded,
    not silently removed. This receiver never forwards traffic to the internet.
    """

    def __init__(self, scanner: LeakScanner, port: int = 0, reply_text: str = "ok"):
        self.scanner = scanner
        self.port = port
        self.reply_text = reply_text
        self._lock = threading.Lock()
        self._observations: deque[Observation] = deque(maxlen=1000)
        self._requests = 0
        self._leaking_requests = 0
        self._body_bytes = 0
        self._canary_ids: set[str] = set()
        self.server = None
        self.thread = None

    @property
    def observations(self) -> list[Observation]:
        with self._lock:
            return list(self._observations)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def report(self) -> dict:
        with self._lock:
            return {
                "requests": self._requests,
                "requests_with_canaries": self._leaking_requests,
                "canary_ids": sorted(self._canary_ids),
                "body_bytes": self._body_bytes,
            }

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return  # no request path, headers, raw body or secrets in server logs

            def do_POST(self):
                status = 200
                body = b""
                try:
                    size = int(self.headers.get("Content-Length", "-1"))
                except ValueError:
                    size = -1
                if self.headers.get("Transfer-Encoding") or not 0 <= size <= 4 * 1024 * 1024:
                    status = 400
                elif self.path != "/v1/chat/completions":
                    status = 404
                else:
                    self.connection.settimeout(5)
                    try:
                        body = self.rfile.read(size)
                    except OSError:
                        status = 400
                    if len(body) != size:
                        status = 400
                    try:
                        json.loads(body)
                    except (ValueError, RecursionError):
                        status = 400
                hits = set(owner.scanner.scan(body))
                hits.update(owner.scanner.scan(self.path.encode()))
                for value in self.headers.values():
                    hits.update(owner.scanner.scan(value.encode()))
                with owner._lock:
                    owner._observations.append(Observation(frozenset(hits), len(body), status))
                    owner._requests += 1
                    owner._leaking_requests += bool(hits)
                    owner._body_bytes += len(body)
                    owner._canary_ids.update(hits)
                payload = (
                    {
                        "id": "synthetic-spy",
                        "object": "chat.completion",
                        "model": "synthetic",
                        "choices": [
                            {
                                "index": 0,
                                "finish_reason": "stop",
                                "message": {"role": "assistant", "content": owner.reply_text},
                            }
                        ],
                    }
                    if status == 200
                    else {"error": {"message": "unsupported synthetic request"}}
                )
                encoded = json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(encoded)

        class Server(ThreadingHTTPServer):
            def handle_error(self, request, client_address):
                return  # no exception tracebacks containing request data

        self.server = Server(("127.0.0.1", self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18999)
    parser.add_argument("--reply-text", default="[PERSON_1]")
    args = parser.parse_args()
    with UpstreamSpy(LeakScanner(load_canaries(args.manifest)), args.port, args.reply_text) as spy:
        print(json.dumps({"synthetic_upstream": spy.base_url}), flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        finally:
            print(json.dumps(spy.report()), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
