"""Start the presentation showcase on loopback with a disposable, private vault."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser
from tempfile import TemporaryDirectory

import uvicorn

from pii_gateway.web.server import create_app


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
    print(f"Локальная презентация: {url}", flush=True)
    print("Модель не вызывается. Временные соответствия удалятся после остановки.", flush=True)

    if not args.no_browser and not args.reload:
        open_browser_delayed(url)

    previous = os.environ.get("PII_VAULT_DIR")
    with TemporaryDirectory(prefix="pii-playground-") as directory:
        os.environ["PII_VAULT_DIR"] = directory
        try:
            if args.reload:
                uvicorn.run(
                    "pii_gateway.web.server:app",
                    host=args.host,
                    port=args.port,
                    reload=True,
                    access_log=False,
                )
            else:
                uvicorn.run(
                    create_app(),
                    host=args.host,
                    port=args.port,
                    access_log=False,
                )
        finally:
            if previous is None:
                os.environ.pop("PII_VAULT_DIR", None)
            else:
                os.environ["PII_VAULT_DIR"] = previous
    return 0


if __name__ == "__main__":
    sys.exit(main())
