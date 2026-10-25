import json
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

from pii_gateway.sandbox.leaks import Canary, LeakScanner, load_canaries
from pii_gateway.sandbox.upstream_spy import UpstreamSpy

REAL = "Иван Иванов"


@pytest.mark.parametrize(
    "payload",
    [
        REAL.encode(),
        json.dumps({"content": REAL}, ensure_ascii=True).encode(),
        json.dumps({"url": "https://example.test/?q=" + quote(REAL)}).encode(),
        json.dumps({"content": "Письмо Ивану Иванову"}, ensure_ascii=False).encode(),
    ],
)
def test_matches_utf8_json_escapes_url_and_explicit_variants(payload):
    scanner = LeakScanner([Canary("client", REAL, ("Ивану Иванову",))])
    assert scanner.scan(payload) == {"client"}


def test_rejects_manifest_without_synthetic_flag(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"canaries": [{"id": "client", "value": REAL}]}))
    with pytest.raises(ValueError, match="synthetic"):
        load_canaries(path)


def test_report_does_not_retain_request_body_or_secret():
    with UpstreamSpy(LeakScanner([Canary("client", REAL)])) as spy:
        response = httpx.post(
            spy.base_url + "/chat/completions",
            json={"content": REAL},
            headers={"Authorization": "Bearer synthetic-secret"},
            trust_env=False,
        )
        assert response.status_code == 200
        report = spy.report()
        assert report["requests"] == report["requests_with_canaries"] == 1
        assert report["canary_ids"] == ["client"]
        assert REAL not in repr(spy.observations)
        assert "synthetic-secret" not in repr(spy.observations)


def test_positive_control_and_sanitized_request_are_distinguished():
    with UpstreamSpy(LeakScanner([Canary("client", REAL)])) as spy:
        for text in ("[PERSON_1]", REAL):
            httpx.post(spy.base_url + "/chat/completions", json={"content": text}, trust_env=False)
        assert [row.canary_ids for row in spy.observations] == [frozenset(), {"client"}]
        assert spy.report()["requests"] == 2


def test_repository_manifest_contains_four_synthetic_types():
    path = Path(__file__).parents[2] / "bench/fixtures/synthetic_canaries.json"
    assert len(load_canaries(path)) == 4
