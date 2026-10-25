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
    fully_scanned: bool
    expected: bool


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
        self._unscanned_requests = 0
        self._unexpected_requests = 0
        self._inflight_requests = 0
        self._transport_errors = 0
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
            if self._leaking_requests:
                verdict = "leak"
            elif (
                self._unscanned_requests
                or self._unexpected_requests
                or self._inflight_requests
                or self._transport_errors
            ):
                verdict = "inconclusive"
            else:
                verdict = "clean" if self._requests else "no_requests"
            return {
                "requests": self._requests,
                "requests_with_canaries": self._leaking_requests,
                "canary_ids": sorted(self._canary_ids),
                "body_bytes": self._body_bytes,
                "unscanned_requests": self._unscanned_requests,
                "unexpected_requests": self._unexpected_requests,
                "inflight_requests": self._inflight_requests,
                "transport_errors": self._transport_errors,
                "verdict": verdict,
            }

    def _begin_request(self):
        with self._lock:
            self._requests += 1
            self._inflight_requests += 1

    def _observe(self, hits, body_bytes, status, *, fully_scanned, expected, started=False):
        with self._lock:
            self._observations.append(
                Observation(frozenset(hits), body_bytes, status, fully_scanned, expected)
            )
            if started:
                self._inflight_requests -= 1
            else:
                self._requests += 1
            self._leaking_requests += bool(hits)
            self._body_bytes += body_bytes
            self._canary_ids.update(hits)
            self._unscanned_requests += not fully_scanned
            self._unexpected_requests += not expected

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, *args):
                return  # no request path, headers, raw body or secrets in server logs

            def __getattr__(self, name):
                if name.startswith("do_"):
                    return self._handle_request  # observe even unknown HTTP methods
                raise AttributeError(name)

            def handle_one_request(self):
                self._observed = False
                self._started = False
                self.raw_requestline = b""
                try:
                    return super().handle_one_request()
                finally:
                    if self.raw_requestline and not self._observed:
                        self._record(b"", 400, fully_scanned=False, expected=False)

            def send_error(self, code, message=None, explain=None):
                self._record(b"", code, fully_scanned=False, expected=False)
                try:
                    super().send_error(code, message, explain)
                except OSError:
                    pass

            def _record(self, body, status, *, fully_scanned, expected):
                if getattr(self, "_observed", False):
                    return
                self._observed = True
                hits = set(owner.scanner.scan(body))
                hits.update(owner.scanner.scan(getattr(self, "raw_requestline", b"")))
                for name, value in getattr(self, "headers", {}).items():
                    # The HTTP parser preserves raw header octets through Latin-1.
                    header = f"{name}: {value}".encode("latin-1", errors="replace")
                    hits.update(owner.scanner.scan(header))
                owner._observe(
                    hits,
                    len(body),
                    status,
                    fully_scanned=fully_scanned,
                    expected=expected,
                    started=getattr(self, "_started", False),
                )

            def _handle_request(self):
                self._started = True
                owner._begin_request()
                expected = self.command == "POST" and self.path == "/v1/chat/completions"
                status = 200 if expected else (404 if self.path != "/v1/chat/completions" else 405)
                body = b""
                lengths = self.headers.get_all("Content-Length", [])
                fully_scanned = not self.headers.get_all("Transfer-Encoding") and len(lengths) <= 1
                try:
                    raw_size = lengths[0].strip() if lengths else "0"
                    size = int(raw_size) if raw_size.isascii() and raw_size.isdigit() else -1
                except ValueError:
                    size = -1
                if self.command == "POST" and not lengths:
                    fully_scanned = False
                fully_scanned = fully_scanned and 0 <= size <= 4 * 1024 * 1024
                if not fully_scanned:
                    status = 400
                else:
                    # Read bounded framed bodies regardless of method/path/status.
                    try:
                        body = self.rfile.read(size)
                    except OSError:
                        fully_scanned = False
                    if len(body) != size:
                        fully_scanned = False
                    if not fully_scanned:
                        status = 400
                if status == 200:
                    try:
                        json.loads(body)
                    except (ValueError, RecursionError):
                        status = 400
                self._record(
                    body, status, fully_scanned=fully_scanned, expected=expected and status == 200
                )
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
                self.close_connection = True
                if self.command != "HEAD":
                    try:
                        self.wfile.write(encoded)
                    except OSError:
                        pass

        class Server(ThreadingHTTPServer):
            def handle_error(self, request, client_address):
                # A receiver/parser failure can never yield a clean report.
                with owner._lock:
                    owner._transport_errors += 1

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
    # Stop accepting connections before taking the final snapshot. Active or
    # failed requests make the verdict inconclusive rather than falsely clean.
    report = spy.report()
    print(json.dumps(report), flush=True)
    return int(report["verdict"] in {"leak", "inconclusive"})


if __name__ == "__main__":
    raise SystemExit(main())
