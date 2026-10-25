"""Standalone, stdlib-only TCP/UNIX bridge copied into the sandbox.

This file deliberately imports none of the gateway package. It also runs with
the distro Python (3.10+) when the host project uses a separate Python 3.12 venv.
"""

from __future__ import annotations

import argparse
import selectors
import socket
import socketserver
import subprocess
import threading


def tunnel(left: socket.socket, right: socket.socket) -> None:
    """Bound memory to one 64 KiB block; preserve half-closes and SSE bytes."""
    left.settimeout(30)
    right.settimeout(30)
    with selectors.DefaultSelector() as selector:
        selector.register(left, selectors.EVENT_READ, right)
        selector.register(right, selectors.EVENT_READ, left)
        while selector.get_map():
            ready = selector.select(timeout=300)
            if not ready:
                return
            for key, _ in ready:
                data = key.fileobj.recv(65536)
                if data:
                    key.data.sendall(data)
                else:
                    selector.unregister(key.fileobj)
                    try:
                        key.data.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass


class LocalBridge(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("an agent command is required")

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                    peer.connect(args.socket)
                    tunnel(self.request, peer)
            except OSError:
                # No request contents, command arguments or secrets in logs.
                return

    with LocalBridge(("127.0.0.1", args.port), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            return subprocess.call(command, close_fds=True)
        finally:
            server.shutdown()
            thread.join(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())
