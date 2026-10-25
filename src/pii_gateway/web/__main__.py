"""CLI entrypoint for running the interactive showcase dashboard."""

from __future__ import annotations

import argparse
import sys
import threading
import time
import webbrowser

import uvicorn


def open_browser_delayed(url: str, delay: float = 1.0) -> None:
    def _worker():
        time.sleep(delay)
        webbrowser.open(url)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pii-showcase",
        description="Launch the PII Gateway interactive presentation dashboard.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8790, help="Port to listen (default: 8790)")
    parser.add_argument(
        "--no-browser", action="store_true", help="Do not automatically open browser"
    )
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")

    args = parser.parse_args(argv)

    url = f"http://{args.host}:{args.port}"
    print("\n=======================================================")
    print("🚀 PII Gateway Presentation Dashboard")
    print(f"📡 Serving at: {url}")
    print("🛡️  Zero-Trust Privacy & Pseudonymization Engine")
    print("=======================================================\n")

    if not args.no_browser:
        open_browser_delayed(url)

    from pii_gateway.web.server import app

    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
