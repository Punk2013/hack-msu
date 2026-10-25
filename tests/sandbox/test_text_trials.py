"""The CLI must reject an identity UI even when its roundtrip is exact."""

import json
import shutil
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from pii_gateway.core.types import Context, PiiGatewayError
from pii_gateway.gateway.transform import CorePipeline

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "bench/fixtures/local_texts"


@contextmanager
def serve_ui(identity):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            pipeline = CorePipeline(Context(body["project_id"], body["mode"]))
            status = 200
            try:
                if self.path == "/api/process":
                    text = body["text"] if identity else pipeline.mask(body["text"])
                    result = {"masked_text": text}
                else:
                    text = body["text"] if identity else pipeline.restore(body["text"]).text
                    result = {"restored_text": text, "status": "complete"}
            except PiiGatewayError:
                status, result = 500, {"error": "synthetic filter refusal"}
            encoded = json.dumps(result, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join(timeout=5)


@pytest.mark.parametrize("name", ["email.txt", "05_limits.txt", "06_reserved_marker.txt"])
@pytest.mark.parametrize("identity", [True, False], ids=["identity-ui", "real-pipeline-ui"])
def test_cli_distinguishes_identity_from_filtering_ui(tmp_path, monkeypatch, name, identity):
    # Only synthetic, isolated databases; the child CLI creates its own second vault.
    monkeypatch.setenv("PII_VAULT_DIR", str(tmp_path / "ui-vault"))
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    if name == "email.txt":
        # Isolate UI validation from random PERSON candidate selection in the engine.
        case = {"file": name, "sensitive": ["synthetic.identity@example.test"]}
        (fixtures / name).write_text("Email: synthetic.identity@example.test\n")
    else:
        case = next(case for case in manifest["cases"] if case["file"] == name)
        shutil.copyfile(FIXTURES / name, fixtures / name)
    (fixtures / "manifest.json").write_text(json.dumps({"synthetic": True, "cases": [case]}))
    output = tmp_path / "results"
    with serve_ui(identity) as url:
        result = subprocess.run([
            sys.executable, str(ROOT / "bench/run_text_trials.py"), "--fixtures", str(fixtures),
            "--out", str(output), "--ui-url", url,
        ], capture_output=True, text=True, timeout=60)
    assert result.returncode == int(identity), result.stdout + result.stderr
    rows = json.loads((output / "results.json").read_text())["results"]
    assert len(rows) == 2  # both realistic and marker
    if name == "06_reserved_marker.txt":
        assert all(row["http_status"] == 500 and row["upstream"]["requests"] == 0 for row in rows)
    else:
        assert all(row["exact"] and row["stable"] and row["http_restored_exact"] for row in rows)
    assert all(row["passed"] is not identity for row in rows)
    if identity:
        assert all(row["ui_process_status"] == 200 for row in rows)
        assert all(row["ui_restored_exact"] for row in rows)
        assert all(not row["ui_checks_passed"] for row in rows)
    if name == "05_limits.txt" and not identity:
        assert all(row["upstream"]["verdict"] == "leak" for row in rows)
        assert all(len(row["ui_remaining_values"]) == 4 for row in rows)
        assert all(row["ui_unexpected_remaining_values"] == [] for row in rows)
