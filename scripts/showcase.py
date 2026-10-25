#!/usr/bin/env python3
"""Run the interactive PII Gateway presentation dashboard."""

import sys
from pathlib import Path

# Add src to sys.path
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "src"))

from pii_gateway.web.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
