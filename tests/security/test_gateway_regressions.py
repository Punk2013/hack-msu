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
        assert client.post("/v1/chat/completions", json=body).status_code == 400
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


@pytest.mark.parametrize("split", [1, 63, 64, 100, 198])
def test_long_multiword_alias_is_never_split(tmp_path, split):
    fake = " ".join(["Псевдоним"] * 20)

    class LongAlias(T):
        def restore(self, text):
            return RestoreResult(text.replace(fake, "ORIGINAL"), RestoreStatus.COMPLETE, [])

    restorer = StreamRestorer(LongAlias(), ToolPolicy(tmp_path, [tmp_path]))
    events = restorer.feed(chunk({"content": fake[:split]}))
    events += restorer.feed(chunk({"content": fake[split:]}))
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


@pytest.mark.parametrize("ending", [None, "stop", "length", "content_filter"])
def test_nonstream_incomplete_tool_is_discarded(tmp_path, ending):
    from pii_gateway.gateway.adapter import restore_response

    reply = {"choices": [{"finish_reason": ending, "message": tool_delta()}]}
    restored = restore_response(reply, T(), ToolPolicy(tmp_path, [tmp_path]))
    assert "tool_calls" not in restored["choices"][0]["message"]


@pytest.mark.parametrize("field", ["model", "stop", "temperature", "seed", "stream"])
def test_canary_in_control_fields_is_refused(tmp_path, field):
    sent = []

    def upstream(request):
        sent.append(request)
        return httpx.Response(200, json={"choices": []})

    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}], field: REAL}
    with TestClient(
        create_app(Settings(workspace=tmp_path), T(), httpx.MockTransport(upstream))
    ) as c:
        assert c.post("/v1/chat/completions", json=body).status_code == 400
    assert sent == []


@pytest.mark.parametrize(
    "body",
    [
        [],
        None,
        {"model": "m", "messages": [None]},
        {"model": "m", "messages": [{"role": [], "content": "hi"}]},
        {"model": "m", "messages": [], "tools": {}},
        {"model": "m", "messages": [], "stream_options": {"include_usage": REAL}},
        {
            "model": "m",
            "messages": [],
            "tool_choice": {"type": "function", "function": {"name": REAL}},
        },
    ],
)
def test_malformed_envelopes_fail_closed(body):
    from pii_gateway.gateway.adapter import UnsupportedRequest, mask_request

    with pytest.raises(UnsupportedRequest):
        mask_request(body, T())


def test_supported_controls_and_tool_history_survive():
    from pii_gateway.gateway.adapter import mask_request

    body = {
        "model": "m",
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": 0.2,
        "top_p": 0.9,
        "max_tokens": 100,
        "n": 1,
        "stop": ["END"],
        "parallel_tool_calls": False,
        "tool_choice": {"type": "function", "function": {"name": "write_file"}},
        "messages": [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call1",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({"content": REAL}),
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call1", "content": REAL},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "strict": True,
                    "description": REAL,
                    "parameters": {
                        "type": "object",
                        "properties": {"content": {"type": "string", "description": REAL}},
                        "required": ["content"],
                        "additionalProperties": False,
                    },
                },
            }
        ],
    }
    masked = mask_request(body, T())
    assert REAL not in json.dumps(masked)
    assert masked["tool_choice"] == body["tool_choice"]
    assert masked["stream_options"] == body["stream_options"]
    assert masked["tools"][0]["function"]["parameters"]["required"] == ["content"]
    assert masked["messages"][0]["tool_calls"][0]["id"] == "call1"
    assert masked["messages"][1]["content"] == "[EMAIL_1]"
    assert REAL in body["messages"][1]["content"]  # input is not mutated


def test_stream_buffer_limit_releases_no_pending_calls(tmp_path):
    from pii_gateway.gateway.stream import MAX_BUFFER_CHARS

    restorer = StreamRestorer(T(), ToolPolicy(tmp_path, [tmp_path]))
    assert restorer.feed(chunk(tool_delta())) == []
    with pytest.raises(PiiGatewayError, match="buffer limit"):
        restorer.feed(chunk({"content": "x" * (MAX_BUFFER_CHARS + 1)}))
    assert restorer.finish() == []


@pytest.mark.parametrize("stream", [False, True])
def test_ollama_second_turn_history_is_accepted(tmp_path, stream):
    sent = []

    def upstream(request):
        sent.append(json.loads(request.content))
        if stream:
            return httpx.Response(200, text="data: [DONE]\n\n")
        return httpx.Response(200, json={"choices": []})

    history_call = tool_delta()["tool_calls"][0]
    body = {
        "model": "m",
        "stream": stream,
        "messages": [
            {"role": "user", "content": "write file"},
            {
                "role": "assistant",
                "content": "",
                "function_call": None,
                "tool_calls": [history_call],
            },
            {"role": "tool", "tool_call_id": "a", "content": REAL},
        ],
    }
    with TestClient(
        create_app(Settings(workspace=tmp_path), T(), httpx.MockTransport(upstream))
    ) as c:
        assert c.post("/v1/chat/completions", json=body).status_code == 200
    assert len(sent) == 1
    assistant = sent[0]["messages"][1]
    assert "function_call" not in assistant
    assert "index" not in assistant["tool_calls"][0]
    assert assistant["tool_calls"][0]["id"] == "a"
    assert assistant["content"] == ""
    assert sent[0]["messages"][2]["content"] == "[EMAIL_1]"
    assert history_call["index"] == 0


@pytest.mark.parametrize("index", [True, -1, "0", REAL, None, {}])
def test_invalid_history_tool_index_is_refused(index):
    from pii_gateway.gateway.adapter import UnsupportedRequest, mask_request

    call = {**tool_delta()["tool_calls"][0], "index": index}
    with pytest.raises(UnsupportedRequest):
        mask_request({"model": "m", "messages": [{"role": "assistant", "tool_calls": [call]}]}, T())
