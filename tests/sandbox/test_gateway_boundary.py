"""Real HTTP upstream observations; core replacements are deliberate test doubles."""

from fastapi.testclient import TestClient

from pii_gateway.core.types import MaskError, RestoreResult, RestoreStatus
from pii_gateway.gateway.app import Settings, create_app
from pii_gateway.gateway.transform import PassThrough
from pii_gateway.sandbox.leaks import Canary, LeakScanner
from pii_gateway.sandbox.upstream_spy import UpstreamSpy

REAL = "Иван Иванов"
BODY = {"model": "synthetic", "messages": [{"role": "user", "content": REAL}]}


class SyntheticTransformer:
    def mask(self, text):
        return text.replace(REAL, "[PERSON_1]")

    def restore(self, text):
        return RestoreResult(text.replace("[PERSON_1]", REAL), RestoreStatus.COMPLETE, [])


class BrokenTransformer(SyntheticTransformer):
    def mask(self, text):
        raise MaskError("synthetic failure")


def make_client(tmp_path, spy, transformer):
    return TestClient(
        create_app(Settings(upstream_base_url=spy.base_url, workspace=tmp_path), transformer)
    )


def test_real_receiver_sees_masked_request_and_user_gets_restored_response(tmp_path):
    with UpstreamSpy(LeakScanner([Canary("person", REAL)]), reply_text="[PERSON_1]") as spy:
        with make_client(tmp_path, spy, SyntheticTransformer()) as client:
            response = client.post("/v1/chat/completions", json=BODY)
        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == REAL
        assert spy.report()["requests"] == 1  # a disconnected receiver cannot pass
        assert spy.report()["requests_with_canaries"] == 0


def test_filter_failure_reaches_no_upstream(tmp_path):
    with UpstreamSpy(LeakScanner([Canary("person", REAL)])) as spy:
        with make_client(tmp_path, spy, BrokenTransformer()) as client:
            response = client.post("/v1/chat/completions", json=BODY)
        assert response.status_code == 500
        assert spy.report()["requests"] == 0


def test_unknown_attachment_reaches_no_upstream(tmp_path):
    with UpstreamSpy(LeakScanner([Canary("person", REAL)])) as spy:
        with make_client(tmp_path, spy, SyntheticTransformer()) as client:
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "synthetic",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {"url": "synthetic://attachment"},
                                }
                            ],
                        }
                    ],
                },
            )
        assert response.status_code == 400
        assert spy.report()["requests"] == 0


def test_positive_control_detects_deliberate_passthrough_leak(tmp_path):
    with UpstreamSpy(LeakScanner([Canary("person", REAL)])) as spy:
        with make_client(tmp_path, spy, PassThrough()) as client:
            assert client.post("/v1/chat/completions", json=BODY).status_code == 200
        assert spy.report()["requests"] == 1
        assert spy.report()["canary_ids"] == ["person"]
