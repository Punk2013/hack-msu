import json
import socket
import time
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


@pytest.mark.parametrize("content,verdict", [(REAL, "leak"), ("masked", "inconclusive")])
def test_broken_scripted_provider_cannot_hide_a_received_request(content, verdict):
    def broken(body):
        raise RuntimeError("synthetic responder failed")

    with UpstreamSpy(LeakScanner([Canary("client", REAL)]), responder=broken) as spy:
        response = httpx.post(
            spy.base_url + "/chat/completions", json={"content": content}, trust_env=False
        )
        assert response.status_code == 500
        assert spy.report()["requests"] == 1
        assert spy.report()["unexpected_requests"] == 1
        assert spy.report()["verdict"] == verdict


def test_repository_manifest_contains_four_synthetic_types():
    path = Path(__file__).parents[2] / "bench/fixtures/synthetic_canaries.json"
    assert len(load_canaries(path)) == 4


def test_wrong_path_body_is_still_inspected():
    with UpstreamSpy(LeakScanner([Canary("client", REAL)])) as spy:
        response = httpx.post(spy.base_url + "/wrong-path", json={"content": REAL}, trust_env=False)
        assert response.status_code == 404
        assert spy.report()["requests"] == 1
        assert spy.report()["requests_with_canaries"] == 1
        assert spy.report()["body_bytes"] > 0


@pytest.mark.parametrize("method", ["GET", "BREW"])
def test_unsupported_method_url_and_headers_are_observed(method):
    with UpstreamSpy(LeakScanner([Canary("client", REAL)])) as spy:
        response = httpx.request(
            method,
            spy.base_url + "/chat/completions?q=" + quote(REAL),
            headers={"X-Canary": quote(REAL)},
            trust_env=False,
        )
        assert response.status_code >= 400
        assert spy.report()["requests"] == 1
        assert spy.report()["canary_ids"] == ["client"]


def test_get_with_framed_body_does_not_hide_received_bytes():
    with UpstreamSpy(LeakScanner([Canary("client", REAL)])) as spy:
        response = httpx.request(
            "GET",
            spy.base_url + "/chat/completions",
            content=json.dumps({"content": REAL}).encode(),
            trust_env=False,
        )
        assert response.status_code >= 400
        assert spy.report()["requests_with_canaries"] == 1
        assert spy.report()["body_bytes"] > 0


@pytest.mark.parametrize(
    "framing",
    [
        b"Transfer-Encoding: chunked\r\n",
        b"Content-Length: 1\r\nContent-Length: 2\r\n",
        b"Content-Length: 5000000\r\n",
        b"",  # POST with no supported body framing
    ],
)
def test_unscanned_framing_is_inconclusive_not_clean(framing):
    with UpstreamSpy(LeakScanner([Canary("client", REAL)])) as spy:
        port = spy.server.server_port
        with socket.create_connection(("127.0.0.1", port), timeout=2) as peer:
            peer.sendall(
                b"POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\n"
                + framing
                + b"Connection: close\r\n\r\n"
            )
            assert b"400" in peer.recv(4096)
        report = spy.report()
        assert report["requests"] == 1
        assert report["unscanned_requests"] == 1
        assert report["verdict"] == "inconclusive"


def test_unsupported_clean_request_cannot_make_a_clean_report():
    with UpstreamSpy(LeakScanner([Canary("client", REAL)])) as spy:
        assert spy.report()["verdict"] == "no_requests"
        httpx.get(spy.base_url + "/wrong-path", trust_env=False)
        assert spy.report()["requests"] == 1
        assert spy.report()["unexpected_requests"] == 1
        assert spy.report()["verdict"] == "inconclusive"


def test_truncated_body_is_scanned_and_marked_incomplete():
    value = "SYNTHETIC_REVIEW_4501123456"
    body = value.encode()
    with UpstreamSpy(LeakScanner([Canary("client", value)])) as spy:
        with socket.create_connection(("127.0.0.1", spy.server.server_port), timeout=2) as peer:
            peer.sendall(
                b"POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\n"
                + f"Content-Length: {len(body) + 10}\r\n\r\n".encode()
                + body
            )
            peer.shutdown(socket.SHUT_WR)
            assert b"400" in peer.recv(4096)
        report = spy.report()
        assert report["requests"] == 1
        assert report["unscanned_requests"] == 1
        assert report["canary_ids"] == ["client"]
        assert report["verdict"] == "leak"


def test_inflight_body_cannot_make_a_clean_or_zero_request_report():
    with UpstreamSpy(LeakScanner([Canary("client", REAL)])) as spy:
        with socket.create_connection(("127.0.0.1", spy.server.server_port), timeout=2) as peer:
            peer.sendall(
                b"POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\n"
                b"Content-Length: 100\r\n\r\n"
            )
            deadline = time.monotonic() + 2
            while spy.report()["requests"] == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
            report = spy.report()
            assert report["requests"] == report["inflight_requests"] == 1
            assert report["verdict"] == "inconclusive"
