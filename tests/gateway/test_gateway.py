import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from pii_gateway.core.types import MaskError, RestoreResult, RestoreStatus
from pii_gateway.gateway.app import Settings, create_app

REAL, FAKE = "Иван Иванов", "[PERSON_1]"


class FakeTransformer:
    """Stands in for detect+mask/restore until roles 2 and 3 land."""

    def mask(self, text: str) -> str:
        return text.replace(REAL, FAKE)

    def restore(self, text: str) -> RestoreResult:
        status = RestoreStatus.PARTIAL if "[PERSON_9]" in text else RestoreStatus.COMPLETE
        return RestoreResult(text.replace(FAKE, REAL), status, [])


class FailingTransformer(FakeTransformer):
    def mask(self, text: str) -> str:
        raise MaskError("injected failure")


class Upstream:
    """Spy on what leaves the machine; replies with a canned response."""

    def __init__(self, reply=None, stream_chunks=None):
        self.requests: list[dict] = []
        self.reply = reply or {"choices": [{"index": 0, "message": {"role": "assistant",
                                                                    "content": "ok"}}]}
        self.stream_chunks = stream_chunks

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        body = json.loads(request.content)
        self.requests.append(body)
        if body.get("stream"):
            sse = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n"
                          for c in self.stream_chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, text=sse,
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=self.reply)


def client(tmp_path: Path, upstream: Upstream, transformer=None) -> TestClient:
    (tmp_path / "out").mkdir(exist_ok=True)
    settings = Settings(upstream_base_url="http://upstream/v1", workspace=tmp_path,
                        write_roots=[tmp_path / "out"])
    app = create_app(settings, transformer or FakeTransformer(),
                     httpx.MockTransport(upstream.handler))
    return TestClient(app)


def call(name: str, args: dict, call_id: str = "c1") -> dict:
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def test_request_is_masked_in_every_text_field(tmp_path):
    up = Upstream()
    body = {
        "model": "m",
        "messages": [
            {"role": "system", "content": f"Клиент {REAL}"},
            {"role": "user", "content": [{"type": "text", "text": f"Письмо для {REAL}"}]},
            {"role": "assistant", "content": None,
             "tool_calls": [call("write_file", {"path": "out/a.txt", "content": REAL})]},
            {"role": "tool", "tool_call_id": "c1", "content": f"файл: {REAL}, паспорт"},
        ],
        "tools": [{"type": "function", "function": {
            "name": "write_file", "description": f"пример {REAL}",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}],
    }
    resp = client(tmp_path, up).post("/v1/chat/completions", json=body)
    assert resp.status_code == 200
    sent = json.dumps(up.requests[0], ensure_ascii=False)
    assert REAL not in sent
    assert sent.count(FAKE) == 5


def test_response_text_is_restored(tmp_path):
    up = Upstream(reply={"choices": [{"index": 0, "finish_reason": "stop",
                                      "message": {"role": "assistant",
                                                  "content": f"Уважаемый {FAKE}!"}}]})
    resp = client(tmp_path, up).post("/v1/chat/completions",
                                     json={"model": "m", "messages": [{"role": "user",
                                                                       "content": "hi"}]})
    assert resp.json()["choices"][0]["message"]["content"] == f"Уважаемый {REAL}!"


def test_mask_failure_sends_nothing(tmp_path):
    up = Upstream()
    resp = client(tmp_path, up, FailingTransformer()).post(
        "/v1/chat/completions", json={"model": "m", "messages": [{"role": "user",
                                                                  "content": REAL}]})
    assert resp.status_code == 500
    assert up.requests == []
    assert REAL not in resp.text


@pytest.mark.parametrize("message", [
    {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]},
    {"role": "plugin", "content": "x"},
])
def test_unsupported_content_is_refused(tmp_path, message):
    up = Upstream()
    resp = client(tmp_path, up).post("/v1/chat/completions",
                                     json={"model": "m", "messages": [message]})
    assert resp.status_code == 400
    assert up.requests == []


def test_pii_in_tool_schema_values_is_refused(tmp_path):
    up = Upstream()
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}],
            "tools": [{"type": "function", "function": {
                "name": "f", "parameters": {"type": "string", "enum": [REAL]}}}]}
    assert client(tmp_path, up).post("/v1/chat/completions", json=body).status_code == 400
    assert up.requests == []


def test_unknown_endpoint_is_refused(tmp_path):
    up = Upstream()
    assert client(tmp_path, up).post("/v1/embeddings", json={}).status_code == 404
    assert up.requests == []


def _tool_reply(*calls):
    return {"choices": [{"index": 0, "finish_reason": "tool_calls",
                         "message": {"role": "assistant", "content": None,
                                     "tool_calls": list(calls)}}]}


def _post(c):
    return c.post("/v1/chat/completions",
                  json={"model": "m", "messages": [{"role": "user", "content": "x"}]}).json()


def test_allowed_write_gets_real_values(tmp_path):
    up = Upstream(reply=_tool_reply(call("write_file", {"path": "out/a.txt",
                                                        "content": f"Для {FAKE}"})))
    msg = _post(client(tmp_path, up))["choices"][0]["message"]
    args = json.loads(msg["tool_calls"][0]["function"]["arguments"])
    assert args["content"] == f"Для {REAL}"


@pytest.mark.parametrize("tool_call", [
    call("write_file", {"path": "../escape.txt", "content": FAKE}),
    call("write_file", {"path": "notes.txt", "content": FAKE}),        # outside write roots
    call("write_file", {"path": "out/a.txt", "content": "[PERSON_9]"}),  # partial restore
    call("fetch_url", {"url": f"https://example.test/?q={FAKE}"}),     # unknown -> deny
])
def test_policy_violation_removes_call(tmp_path, tool_call):
    up = Upstream(reply=_tool_reply(tool_call))
    choice = _post(client(tmp_path, up))["choices"][0]
    assert "tool_calls" not in choice["message"]
    assert choice["finish_reason"] == "stop"
    assert "заблокирован" in choice["message"]["content"]
    assert REAL not in json.dumps(choice, ensure_ascii=False)


def test_symlink_outside_write_root_is_blocked(tmp_path):
    (tmp_path / "out").mkdir()
    try:
        (tmp_path / "out" / "link").symlink_to(tmp_path)
    except OSError:
        pytest.skip("symlinks not supported or permitted on this platform")
    up = Upstream(reply=_tool_reply(call("write_file", {"path": "out/link/x.txt",
                                                        "content": FAKE})))
    choice = _post(client(tmp_path, up))["choices"][0]
    assert "tool_calls" not in choice["message"]


def _chunk(delta, finish=None):
    return {"id": "s", "object": "chat.completion.chunk", "model": "m",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def _stream(c):
    with c.stream("POST", "/v1/chat/completions",
                  json={"model": "m", "stream": True,
                        "messages": [{"role": "user", "content": "x"}]}) as resp:
        assert resp.status_code == 200
        events = [line[6:] for line in resp.iter_lines() if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    return [json.loads(e) for e in events[:-1]]


def test_stream_restores_marker_split_across_chunks(tmp_path):
    up = Upstream(stream_chunks=[
        _chunk({"role": "assistant", "content": ""}),
        _chunk({"content": "Уважаемый [PER"}),
        _chunk({"content": "SON_1], ваш паспорт готов. "}),
        _chunk({"content": "С уважением."}, "stop"),
    ])
    chunks = _stream(client(tmp_path, up))
    text = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks)
    assert text == f"Уважаемый {REAL}, ваш паспорт готов. С уважением."
    assert "[PER" not in text
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_stream_tool_call_is_collected_and_restored(tmp_path):
    args = json.dumps({"path": "out/a.txt", "content": f"Для {FAKE}"}, ensure_ascii=False)
    up = Upstream(stream_chunks=[
        _chunk({"role": "assistant", "tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                                     "function": {"name": "write_file",
                                                                  "arguments": ""}}]}),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": args[:15]}}]}),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": args[15:]}}]}),
        _chunk({}, "tool_calls"),
    ])
    chunks = _stream(client(tmp_path, up))
    calls = [tc for c in chunks for tc in c["choices"][0]["delta"].get("tool_calls", [])]
    assert len(calls) == 1
    assert calls[0]["id"] == "c1"
    assert json.loads(calls[0]["function"]["arguments"])["content"] == f"Для {REAL}"
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"


def test_stream_blocked_tool_call_becomes_text(tmp_path):
    up = Upstream(stream_chunks=[
        _chunk({"role": "assistant", "tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                                     "function": {"name": "fetch_url",
                                                                  "arguments": "{}"}}]}),
        _chunk({}, "tool_calls"),
    ])
    chunks = _stream(client(tmp_path, up))
    assert not any(c["choices"][0]["delta"].get("tool_calls") for c in chunks)
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_stream_buffers_text_until_choice_completion(tmp_path):
    from pii_gateway.gateway.policy import ToolPolicy
    from pii_gateway.gateway.stream import StreamRestorer

    restorer = StreamRestorer(FakeTransformer(), ToolPolicy(tmp_path, [tmp_path]), hold=4)
    pieces = ["Письмо для [PERSO", "N_1] отправлено, ", "копия [PERSON_1]", " в архиве."]
    events = []
    for piece in pieces:
        events += restorer.feed(_chunk({"content": piece}))
    assert events == []  # no prefix escapes before complete alias is available
    events += restorer.feed(_chunk({}, "stop"))
    deltas = [json.loads(e[6:])["choices"][0]["delta"].get("content") or "" for e in events]
    assert all("[PERSO" not in d and "N_1]" not in d for d in deltas)
    assert len([d for d in deltas if d]) == 1
    assert "".join(deltas) == f"Письмо для {REAL} отправлено, копия {REAL} в архиве."


def test_store_is_accepted_but_always_disabled(tmp_path):
    up = Upstream()
    body = {"model": "m", "store": True, "messages": [{"role": "user", "content": "hi"}]}
    assert client(tmp_path, up).post("/v1/chat/completions", json=body).status_code == 200
    assert up.requests[0]["store"] is False
