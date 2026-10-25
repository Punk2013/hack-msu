import shutil
import sys

import pytest

from pii_gateway.sandbox.runner import run_sandbox


@pytest.fixture
def working_sandbox(config):
    if sys.platform != "linux" or not shutil.which("bwrap"):
        pytest.skip("Linux with bubblewrap required; isolation was not tested")
    try:
        result = run_sandbox(config, ["/usr/bin/true"], capture_output=True, timeout=15)
    except OSError as exc:
        pytest.skip(f"sandbox unavailable in this execution environment: {exc.errno}")
    if result.returncode:
        if any(
            message in result.stderr
            for message in (
                "Operation not permitted",
                "No permissions to create",
                "not allowed",
            )
        ):
            pytest.skip("kernel namespace restrictions prevent bubblewrap; isolation NOT tested")
        pytest.fail(f"sandbox preflight failed unexpectedly: {result.stderr}")
    return config
