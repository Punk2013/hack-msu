"""Package entry point for `python -m pii_gateway.vault`."""

import sys

from pii_gateway.vault.cli import main

if __name__ == "__main__":
    sys.exit(main())
