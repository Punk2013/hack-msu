"""Regression checks for PR #18: no real provider or personal data."""

import json

import httpx
import pytest

from pii_gateway import demo_agent as demo


def tool():
    return {
        "id": "a",
        "type": "function",
        "function": {
            "name": "write_file",
            "arguments": json.dumps({"path": "out/proof.txt", "content": "SYNTHETIC"}),
        },
    }


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("ending", [None, "length", "content_filter"])
def test_does_not_execute_unfinished_call(tmp_path, monkeypatch, stream, ending):
    def upstream(request):
        if stream:
            chunk = {
                "choices": [
                    {
                        "index": 0,
                        "delta": {"tool_calls": [dict(tool(), index=0)]},
                        "finish_reason": ending,
                    }
                ]
            }
            return httpx.Response(200, text="data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": ending,
                        "message": {"role": "assistant", "content": None, "tool_calls": [tool()]},
                    }
                ]
            },
        )

    client = httpx.Client(base_url="http://synthetic/v1/", transport=httpx.MockTransport(upstream))
    monkeypatch.setattr(demo.httpx, "Client", lambda **kwargs: client)
    try:
        demo.run("synthetic task", "http://synthetic/v1", "m", tmp_path, stream=stream, max_steps=1)
    except RuntimeError:
        pass
    finally:
        client.close()
    assert not (tmp_path / "out/proof.txt").exists(), "incomplete generation executed a write"


@pytest.mark.parametrize("args", [None, [], {"path": 123}, {"path": "out/a.txt", "content": []}])
def test_bad_tool_arguments_return_error_instead_of_crashing(tmp_path, args):
    result = demo.Workspace(tmp_path).run("write_file", args)
    assert result.startswith("ошибка")
    assert not list(tmp_path.iterdir())


def test_complete_call_positive_control(tmp_path, monkeypatch):
    client = httpx.Client(
        base_url="http://synthetic/v1/",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [tool()],
                            },
                        }
                    ]
                },
            )
        ),
    )
    monkeypatch.setattr(demo.httpx, "Client", lambda **kwargs: client)
    try:
        demo.run("synthetic task", "http://synthetic/v1", "m", tmp_path, max_steps=1)
    finally:
        client.close()
    assert (tmp_path / "out/proof.txt").read_text() == "SYNTHETIC"


@pytest.mark.parametrize("stream", [False, True])
def test_completed_tool_roundtrip_preserves_history(tmp_path, monkeypatch, stream):
    requests = []

    def upstream(request):
        body = json.loads(request.content)
        requests.append(body)
        first = len(requests) == 1
        message = (
            {"role": "assistant", "content": None, "tool_calls": [tool()]}
            if first
            else {"role": "assistant", "content": "done"}
        )
        reason = "tool_calls" if first else "stop"
        if stream:
            if first:
                message["tool_calls"][0]["index"] = 0
            chunk = {"choices": [{"index": 0, "delta": message, "finish_reason": reason}]}
            return httpx.Response(200, text="data:" + json.dumps(chunk) + "\n\ndata: [DONE]\n\n")
        return httpx.Response(
            200, json={"choices": [{"message": message, "finish_reason": reason}]}
        )

    client = httpx.Client(base_url="http://synthetic/v1/", transport=httpx.MockTransport(upstream))
    monkeypatch.setattr(demo.httpx, "Client", lambda **kwargs: client)
    try:
        messages = demo.run("synthetic", "http://synthetic/v1", "m", tmp_path, stream=stream)
    finally:
        client.close()
    assert (tmp_path / "out/proof.txt").read_text() == "SYNTHETIC"
    assert len(requests) == 2
    assert requests[1]["messages"][-1]["role"] == "tool"
    assert requests[1]["messages"][-1]["tool_call_id"] == "a"
    assert messages[-1]["content"] == "done"


def test_done_terminates_stream_before_trailing_calls(tmp_path):
    complete = {"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]}
    trailing = {
        "choices": [
            {"delta": {"tool_calls": [dict(tool(), index=0)]}, "finish_reason": "tool_calls"}
        ]
    }
    wire = (
        "data: "
        + json.dumps(complete)
        + "\n\ndata: [DONE]\n\ndata: "
        + json.dumps(trailing)
        + "\n\n"
    )
    with httpx.Client(
        base_url="http://synthetic/v1/",
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=wire)),
    ) as client:
        assert demo._stream_message(client, {}) == {"role": "assistant", "content": "done"}
