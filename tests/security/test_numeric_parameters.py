"""Numeric protocol fields must cross the same PII boundary as tool values."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from pii_gateway.core.types import RestoreResult, RestoreStatus
from pii_gateway.gateway.app import Settings, create_app

PARAMETERS = {
    "temperature": 0.25,
    "top_p": 0.9,
    "presence_penalty": -1.0,
    "frequency_penalty": 0,
    "max_tokens": 128,
    "max_completion_tokens": 256,
    "seed": 42,
    "n": 1,
}
BASE = {"model": "synthetic", "messages": [{"role": "user", "content": "hello"}]}


class NumericTransformer:
    def __init__(self, canary):
        self.canary = canary

    def mask(self, text):
        return "[PASSPORT_RU_1]" if text == self.canary else text

    def restore(self, text):
        return RestoreResult(text, RestoreStatus.COMPLETE, [])


def send(tmp_path, parameters, canary):
    seen = []

    def upstream(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": []})

    with TestClient(
        create_app(
            Settings(workspace=tmp_path),
            NumericTransformer(canary),
            httpx.MockTransport(upstream),
        )
    ) as client:
        response = client.post("/v1/chat/completions", json={**BASE, **parameters})
    return response, seen


@pytest.mark.parametrize("key", PARAMETERS)
def test_detected_numeric_identifier_never_reaches_upstream(tmp_path, key):
    # n only admits 1; even its allowed value must pass the transformer contract.
    value = 1 if key == "n" else 4501123456
    response, seen = send(tmp_path, {key: value}, str(value))
    assert response.status_code == 400
    assert seen == []
    assert str(value) not in response.text


def test_detected_float_identifier_never_reaches_upstream(tmp_path):
    response, seen = send(tmp_path, {"temperature": 4501123456.0}, "4501123456.0")
    assert response.status_code == 400
    assert seen == []


def test_ordinary_parameters_keep_values_and_numeric_types(tmp_path):
    response, seen = send(tmp_path, PARAMETERS, "4501123456")
    assert response.status_code == 200
    assert len(seen) == 1
    for key, value in PARAMETERS.items():
        assert seen[0][key] == value
        assert type(seen[0][key]) is type(value)
