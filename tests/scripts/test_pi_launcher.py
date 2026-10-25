"""Hermetic launcher regression: no installed pi, repo venv, or cloud requests."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
KEYS = ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY",
        "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "PII_UPSTREAM_API_KEY")
SENTINEL = "synthetic-test-only"


@pytest.mark.skipif(not shutil.which("bash") or sys.platform == "win32", reason="bash only")
@pytest.mark.parametrize("provider", ["openai", "openrouter", "deepseek"])
def test_key_goes_to_gateway_only(tmp_path, provider):
    """Real shell control flow; HOME paths redirected, processes stubbed; no real keys."""
    repo = tmp_path / 'repo'
    scripts = repo / 'scripts'
    runtime = repo / '.venv/bin'
    bins = tmp_path / 'bin'
    for p in (scripts, runtime, bins):
        p.mkdir(parents=True)
    text = (REPO / 'scripts/pi.sh').read_text()
    # Redirect only artifact destinations; never set/change HOME or user config.
    text = text.replace('$HOME/pii-gateway-reports', str(tmp_path / 'reports'))
    text = text.replace('$HOME/.local/share/pii-gateway/pi-agent', str(tmp_path / 'pi-config'))
    launcher = scripts / 'pi.sh'
    launcher.write_text(text)
    (runtime / 'python').symlink_to(sys.executable)
    uvicorn = runtime / 'uvicorn'
    gateway_result = tmp_path / 'gateway.json'
    uvicorn.write_text(
        f'#!{sys.executable}\nimport os,json,time\nfrom pathlib import Path\n'
        f'Path({str(gateway_result)!r}).write_text(json.dumps('
        f'os.environ.get("PII_UPSTREAM_API_KEY") == {SENTINEL!r}))\n'
        'time.sleep(30)\n'
    )
    uvicorn.chmod(0o700)
    agent_result = tmp_path / 'agent.json'
    pi = bins / 'pi'
    pi.write_text(f'#!{sys.executable}\nimport os,json\nfrom pathlib import Path\n'
                  f'Path({str(agent_result)!r}).write_text(json.dumps('
                  f'[k for k in {KEYS!r} if os.environ.get(k)]))\n')
    pi.chmod(0o700)
    for name, body in [('curl', f'while [ ! -f "{gateway_result}" ]; do sleep 0.02; done'),
                       ('sha256sum', 'cat >/dev/null; echo 123456789abc')]:
        p = bins / name
        p.write_text('#!/bin/sh\n' + body + '\n')
        p.chmod(0o700)
    result = subprocess.run(['/bin/bash', str(launcher), f'{provider}:test-model'],
                            cwd=tmp_path, env={'PATH':f'{bins}:/usr/bin:/bin',
                                              **dict.fromkeys(KEYS, SENTINEL)},
                            capture_output=True, timeout=10)
    assert result.returncode == 0
    observed = json.loads(agent_result.read_text())
    assert observed == []
    assert json.loads(gateway_result.read_text()) is True
    assert SENTINEL.encode() not in result.stdout + result.stderr
    config = (tmp_path / 'pi-config/models.json').read_text()
    assert SENTINEL not in config
    assert json.loads(config)['providers']['pii-gateway']['apiKey']
