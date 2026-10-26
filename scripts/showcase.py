#!/usr/bin/env python3
"""Run the interactive PII Gateway presentation dashboard and local inspector."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pii_gateway.web.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
