"""Synthetic regression reproductions for gateway merged in c35470e.
Run from repository: .venv/bin/pytest tests/security -q
All upstream requests are intercepted by httpx.MockTransport. No external I/O.
"""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from pii_gateway.core.types import PiiGatewayError, RestoreResult, RestoreStatus
from pii_gateway.gateway.app import Settings, create_app
from pii_gateway.gateway.policy import ToolPolicy
from pii_gateway.gateway.stream import StreamRestorer, restore_sse

REAL = "SYNTHETIC-CANARY@example.test"


class T:
    def mask(self, text):
        return text.replace(REAL, "[EMAIL_1]")

    def restore(self, text):
        return RestoreResult(text, RestoreStatus.COMPLETE, [])


def cases():
    base = {"model": "m", "messages": [{"role": "user", "content": "hello"}]}
    return [
        {**base, "metadata": {"client": REAL}},
        {**base, "user": REAL},
        {
            **base,
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "hello", "extra": REAL}]}
            ],
        },
        {
            **base,
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "function_call": {
                        "name": "write_file",
                        "arguments": json.dumps({"content": REAL}),
                    },
                }
            ],
        },
        {
            **base,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "f",
                        "parameters": {"type": "object", "properties": {REAL: {"type": "string"}}},
                    },
                }
            ],
        },
        {
            **base,
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "a",
                            "type": "function",
                            "function": {"name": "f", "arguments": json.dumps({REAL: "value"})},
                        }
                    ],
                }
            ],
        },
    ]


@pytest.mark.parametrize(
    "body",
    cases(),
    ids=["metadata", "user", "text-extra", "legacy-function-call", "schema-key", "argument-key"],
)
def test_canary_never_reaches_upstream(tmp_path, body):
    sent = []

    def upstream(req):
        sent.append(req.content.decode())
        return httpx.Response(200, json={"choices": []})

    with TestClient(
        create_app(Settings(workspace=tmp_path), T(), httpx.MockTransport(upstream))
    ) as client:
        client.post("/v1/chat/completions", json=body)
    assert all(REAL not in request for request in sent), "raw canary sent to upstream"


def chunk(delta, finish=None):
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def tool_delta():
    return {
        "tool_calls": [
            {
                "index": 0,
                "id": "a",
                "type": "function",
                "function": {
                    "name": "write_file",
                    "arguments": json.dumps({"path": "a.txt", "content": "synthetic"}),
                },
            }
        ]
    }


@pytest.mark.parametrize("ending", ["eof", "done", "length", "content_filter"])
def test_incomplete_or_filtered_stream_never_releases_tool(tmp_path, ending):
    async def lines():
        yield "data: " + json.dumps(chunk(tool_delta()))
        if ending in ("length", "content_filter"):
            yield "data: " + json.dumps(chunk({}, ending))
        if ending != "eof":
            yield "data: [DONE]"

    async def collect():
        events = []
        try:
            async for event in restore_sse(lines(), T(), ToolPolicy(tmp_path, [tmp_path])):
                events.append(event)
        except PiiGatewayError:
            pass  # Explicit failure is safe only if no call was emitted before it.
        return b"".join(events)

    assert b'"tool_calls"' not in asyncio.run(collect()), "executable call released"


def test_long_multiword_alias_is_never_split(tmp_path):
    fake = " ".join(["Псевдоним"] * 20)

    class LongAlias(T):
        def restore(self, text):
            return RestoreResult(text.replace(fake, "ORIGINAL"), RestoreStatus.COMPLETE, [])

    restorer = StreamRestorer(LongAlias(), ToolPolicy(tmp_path, [tmp_path]))
    events = restorer.feed(chunk({"content": fake[:100]}))
    events += restorer.feed(chunk({"content": fake[100:]}))
    events += restorer.feed(chunk({}, "stop"))
    result = "".join(json.loads(e[6:])["choices"][0]["delta"].get("content", "") for e in events)
    assert result == "ORIGINAL"


def test_positive_control_regular_content_is_masked(tmp_path):
    sent = []

    def upstream(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": []})

    with TestClient(
        create_app(Settings(workspace=tmp_path), T(), httpx.MockTransport(upstream))
    ) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": [{"role": "user", "content": REAL}]},
        )
    assert response.status_code == 200
    assert sent[0]["messages"][0]["content"] == "[EMAIL_1]"


def test_positive_control_completed_tool_call_is_released(tmp_path):
    restorer = StreamRestorer(T(), ToolPolicy(tmp_path, [tmp_path]))
    assert restorer.feed(chunk(tool_delta())) == []
    events = restorer.feed(chunk({}, "tool_calls"))
    calls = [
        call
        for event in events
        for call in json.loads(event[6:])["choices"][0]["delta"].get("tool_calls", [])
    ]
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "write_file"
