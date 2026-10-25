"""Gate 1 on the real pipeline: detect + pseudo + vault behind the gateway.

The upstream is a spy (httpx.MockTransport) that records everything that would
leave the machine and echoes the masked card back inside a write_file call.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from pii_gateway.gateway.app import Settings, create_app

DEMO = Path(__file__).resolve().parents[2] / "demo" / "clients"
FIELDS = re.compile(r"^(?:ФИО|Email|Телефон|Паспорт): (.+)$", re.MULTILINE)


def originals(card: str) -> list[str]:
    values = FIELDS.findall(card)
    assert len(values) == 4
    return values


class EchoSpy:
    """Records outbound requests; replies with write_file(<masked tool content>)."""

    def __init__(self, stream: bool = False):
        self.sent: list[str] = []
        self.stream = stream

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request.content.decode())
        body = json.loads(request.content)
        content = next(m["content"] for m in reversed(body["messages"]) if m["role"] == "tool")
        args = json.dumps({"path": "out/client.txt", "content": content}, ensure_ascii=False)
        call = {"id": "c1", "type": "function", "function": {"name": "write_file",
                                                               "arguments": args}}
        if not body.get("stream"):
            return httpx.Response(200, json={"choices": [{
                "index": 0, "finish_reason": "tool_calls",
                "message": {"role": "assistant", "content": None, "tool_calls": [call]}}]})
        chunks = [
            {"choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [
                {"index": 0, "id": "c1", "type": "function",
                 "function": {"name": "write_file", "arguments": args[:20]}}]},
                "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": args[20:]}}]}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        ]
        sse = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks)
        return httpx.Response(200, text=sse + "data: [DONE]\n\n",
                              headers={"content-type": "text/event-stream"})


@pytest.fixture
def make_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PII_VAULT_DIR", str(tmp_path / "vault"))
    (tmp_path / "out").mkdir()

    def make(spy: EchoSpy) -> TestClient:
        settings = Settings(upstream_base_url="http://upstream/v1", project_id="e2e",
                            workspace=tmp_path, write_roots=[tmp_path / "out"])
        return TestClient(create_app(settings, transport=httpx.MockTransport(spy.handler)))

    return make


def request_for(card: str, stream: bool = False) -> dict:
    return {
        "model": "m", "stream": stream,
        "messages": [
            {"role": "user", "content": "Напиши уведомление клиенту."},
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "r1", "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "clients/c.txt"}'}}]},
            {"role": "tool", "tool_call_id": "r1", "content": card},
        ],
    }


@pytest.mark.parametrize("name", ["client1.txt", "client2.txt"])
@pytest.mark.parametrize("stream", [False, True])
def test_card_never_leaves_in_clear_and_file_gets_real_values(make_client, name, stream):
    card = (DEMO / name).read_text(encoding="utf-8")
    spy = EchoSpy()
    client = make_client(spy)
    if stream:
        with client.stream("POST", "/v1/chat/completions", json=request_for(card, True)) as r:
            assert r.status_code == 200
            events = [json.loads(line[6:]) for line in r.iter_lines()
                      if line.startswith("data: ") and line != "data: [DONE]"]
        calls = [tc for e in events for tc in e["choices"][0]["delta"].get("tool_calls", [])]
    else:
        resp = client.post("/v1/chat/completions", json=request_for(card))
        assert resp.status_code == 200
        calls = resp.json()["choices"][0]["message"]["tool_calls"]

    outbound = spy.sent[0]
    for value in originals(card):
        assert value not in outbound, value
        assert json.dumps(value, ensure_ascii=False)[1:-1] not in outbound
    assert "[PERSON_" not in outbound and "[EMAIL_" not in outbound  # realistic, no markers

    written = json.loads(calls[0]["function"]["arguments"])["content"]
    assert written == card  # the agent's file gets the real data back


def test_replay_of_history_is_masked_identically(make_client):
    card = (DEMO / "client1.txt").read_text(encoding="utf-8")
    spy = EchoSpy()
    client = make_client(spy)
    for _ in range(3):
        assert client.post("/v1/chat/completions", json=request_for(card)).status_code == 200
    assert len(set(spy.sent)) == 1


@pytest.mark.parametrize("target", [
    "pii_gateway.detect.detector._person_spans",
    "pii_gateway.pseudo.engine.Vault",
])
def test_component_failure_sends_nothing(make_client, monkeypatch, target):
    def boom(*args, **kwargs):
        raise RuntimeError("injected failure with Иван Петрович Иванов")

    monkeypatch.setattr(target, boom)
    card = (DEMO / "client1.txt").read_text(encoding="utf-8")
    spy = EchoSpy()
    resp = make_client(spy).post("/v1/chat/completions", json=request_for(card))
    assert resp.status_code == 500
    assert spy.sent == []
    assert "Иван" not in resp.text
