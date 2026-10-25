"""Start the inspector on loopback with a disposable, private vault."""
from __future__ import annotations

import argparse
import os
from tempfile import TemporaryDirectory

import uvicorn

from pii_gateway.web.server import create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    previous = os.environ.get("PII_VAULT_DIR")
    with TemporaryDirectory(prefix="pii-playground-") as directory:
        os.environ["PII_VAULT_DIR"] = directory
        try:
            print(f"Локальная песочница: http://127.0.0.1:{args.port}", flush=True)
            print("Модель не вызывается. Временные соответствия удалятся после остановки.",
                  flush=True)
            uvicorn.run(create_app(), host="127.0.0.1", port=args.port,
                        access_log=False, log_level="warning")
        finally:
            if previous is None:
                os.environ.pop("PII_VAULT_DIR", None)
            else:
                os.environ["PII_VAULT_DIR"] = previous


if __name__ == "__main__":
    main()
