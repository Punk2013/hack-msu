"""scripts/pi.sh: the provider key reaches the gateway, never the pi process."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SENTINEL = "sk-synthetic-sentinel-0000"


@pytest.mark.skipif(not shutil.which("bash") or sys.platform == "win32", reason="bash only")
def test_key_goes_to_gateway_only(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_pi = bin_dir / "pi"
    # the stub pi reports which credential variables it can see, never their values
    fake_pi.write_text(
        "#!/bin/sh\n"
        "for v in OPENAI_API_KEY PII_UPSTREAM_API_KEY OPENROUTER_API_KEY; do\n"
        '  eval "x=\\${$v:-}"; [ -n "$x" ] && echo "LEAKED:$v"\n'
        "done\n"
        'grep -q "$PII_SENTINEL_CHECK" "$PI_CODING_AGENT_DIR/models.json" && echo LEAKED:models\n'
        "echo PI_RAN\n"
    )
    fake_pi.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "OPENAI_API_KEY": SENTINEL,
        "PII_UPSTREAM_API_KEY": SENTINEL,
        "PII_SENTINEL_CHECK": SENTINEL,
    }
    done = subprocess.run(
        [str(REPO / "scripts" / "pi.sh"), "openai:gpt-test"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
    )
    assert "PI_RAN" in done.stdout, done.stderr
    assert "LEAKED" not in done.stdout
    assert SENTINEL not in done.stdout + done.stderr
