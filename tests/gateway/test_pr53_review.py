import json
import subprocess
import sys
from pathlib import Path

from pii_gateway.gateway.adapter import mask_request
from pii_gateway.gateway.journal import Journal
from pii_gateway.gateway.transform import PassThrough
from pii_gateway.journal_report import build

SNAPSHOT = Path(__file__).parents[2]


def test_launcher_keeps_provider_credentials_out_of_agent(tmp_path):
    """Real shell control flow; HOME paths redirected, processes stubbed; no real keys."""
    repo = tmp_path / 'repo'
    scripts = repo / 'scripts'
    runtime = repo / '.venv/bin'
    bins = tmp_path / 'bin'
    for p in (scripts, runtime, bins):
        p.mkdir(parents=True)
    text = (SNAPSHOT / 'scripts/pi.sh').read_text()
    # Redirect only artifact destinations; never set/change HOME or user config.
    text = text.replace('$HOME/pii-gateway-reports', str(tmp_path / 'reports'))
    text = text.replace('$HOME/.local/share/pii-gateway/pi-agent', str(tmp_path / 'pi-config'))
    launcher = scripts / 'pi.sh'
    launcher.write_text(text)
    (runtime / 'python').symlink_to(sys.executable)
    uvicorn = runtime / 'uvicorn'
    uvicorn.write_text(f'#!{sys.executable}\nimport time\ntime.sleep(30)\n')
    uvicorn.chmod(0o700)
    agent_result = tmp_path / 'agent.json'
    pi = bins / 'pi'
    pi.write_text(f'#!{sys.executable}\nimport os,json\nfrom pathlib import Path\n'
                  f'Path({str(agent_result)!r}).write_text(json.dumps({{'
                  '"provider_key_present":bool(os.environ.get("OPENAI_API_KEY")),'
                  '"upstream_key_present":bool(os.environ.get("PII_UPSTREAM_API_KEY"))}))\n')
    pi.chmod(0o700)
    for name, body in [('curl', 'exit 0'), ('sha256sum', 'cat >/dev/null; echo 123456789abc')]:
        p = bins / name
        p.write_text('#!/bin/sh\n' + body + '\n')
        p.chmod(0o700)
    result = subprocess.run(['/bin/bash', str(launcher), 'openai:test-model'],
                            cwd=tmp_path, env={'PATH':f'{bins}:/usr/bin:/bin',
                            'OPENAI_API_KEY':'synthetic-test-only',
                            'PII_UPSTREAM_API_KEY':'synthetic-test-only'},
                            capture_output=True, timeout=10)
    assert result.returncode == 0
    observed = json.loads(agent_result.read_text())
    assert observed == {'provider_key_present':False, 'upstream_key_present':False}


def test_existing_journal_is_private(tmp_path):
    path = tmp_path / 'journal.jsonl'
    path.touch(mode=0o644)
    path.chmod(0o644)
    journal = Journal(path)
    journal.write({'request_in':'synthetic'})
    assert path.stat().st_mode & 0o777 == 0o600


def test_report_keeps_same_length_changed_request(tmp_path):
    path = tmp_path / 'journal.jsonl'
    records = []
    for word in ('FIRST_REQUEST', 'SECOND_REQUEST'):
        records.append({'ts':1,'request_in':[{'role':'user','content':word}],
                        'request_out':[{'role':'user','content':word}],
                        'model_said':{},'agent_got':{}})
    path.write_text('\n'.join(json.dumps(r) for r in records))
    page = build(path)
    assert 'FIRST_REQUEST' in page
    assert 'SECOND_REQUEST' in page


def test_store_true_preserves_main_privacy_policy():
    result = mask_request({'model':'m','messages':[{'role':'user','content':'hello'}],
                           'store':True}, PassThrough())
    assert result['store'] is False
