"""Real file tools and HTTP gateway; deterministic upstream, no live LLM claim."""

import json
import re
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from test_pipeline_e2e import DEMO, originals

from pii_gateway.demo_agent import run
from pii_gateway.gateway.app import Settings, create_app


class FileScenario:
    def __init__(self):
        self.sent = []
        self.cards = []

    def handler(self, request):
        body = json.loads(request.content)
        self.sent.append(body)
        step = len(self.sent)
        if step == 1:
            commands = [("read_file", {"path": f"clients/client{i}.txt"}) for i in (1, 2)]
        elif step == 2:
            self.cards = [m["content"] for m in body["messages"] if m["role"] == "tool"]
            assert len(self.cards) == 2
            commands = [
                ("write_file", {"path": f"out/client{i}.txt", "content": "Уведомление\n" + card})
                for i, card in enumerate(self.cards, 1)
            ]
        elif step == 3:
            commands = [("read_file", {"path": f"out/client{i}.txt"}) for i in (1, 2)]
        else:
            assert step == 4
            reread = [m["content"] for m in body["messages"] if m["role"] == "tool"][-2:]
            assert reread == ["Уведомление\n" + card for card in self.cards]
            commands = []
        calls = [
            {
                "id": f"step{step}_{i}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
            }
            for i, (name, args) in enumerate(commands)
        ]
        finish = "tool_calls" if calls else "stop"
        message = {"role": "assistant", "content": None if calls else "Готово."}
        if calls:
            message["tool_calls"] = calls
        if not body.get("stream"):
            return httpx.Response(
                200, json={"choices": [{"index": 0, "message": message, "finish_reason": finish}]}
            )
        deltas = []
        for i, call in enumerate(calls):
            args = call["function"]["arguments"]
            deltas.append(
                {
                    "tool_calls": [
                        {
                            **call,
                            "index": i,
                            "function": {"name": call["function"]["name"], "arguments": args[:11]},
                        }
                    ]
                }
            )
            deltas.append({"tool_calls": [{"index": i, "function": {"arguments": args[11:]}}]})
        if not calls:
            deltas.append({"content": message["content"]})
        chunks = [{"choices": [{"index": 0, "delta": d, "finish_reason": None}]} for d in deltas]
        chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n",
        )


def test_literal_marker_failure_never_reaches_upstream(tmp_path, monkeypatch):
    monkeypatch.setenv("PII_VAULT_DIR", str(tmp_path / "vault"))
    scenario = FileScenario()
    app = create_app(Settings(workspace=tmp_path), transport=httpx.MockTransport(scenario.handler))
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "synthetic",
                "messages": [{"role": "user", "content": "[PERSON_1]"}],
            },
        )
    assert response.status_code == 500
    assert scenario.sent == []
    assert "[PERSON_1]" not in response.text


@pytest.mark.parametrize("mode", ["marker", "realistic"])
@pytest.mark.parametrize("stream", [False, True])
def test_real_agent_reads_writes_and_resends_restored_files(tmp_path, monkeypatch, mode, stream):
    workspace = tmp_path / "workspace"
    (workspace / "clients").mkdir(parents=True)
    monkeypatch.setenv("PII_VAULT_DIR", str(tmp_path / "vault"))
    cards = [(DEMO / f"client{i}.txt").read_text() for i in (1, 2)]
    for i, card in enumerate(cards, 1):
        (workspace / f"clients/client{i}.txt").write_text(card)
    scenario = FileScenario()
    settings = Settings(
        workspace=workspace,
        write_roots=[workspace / "out"],
        project_id="file-loop",
        mode=mode,
        local_token="synthetic-local-token",
    )
    app = create_app(settings, transport=httpx.MockTransport(scenario.handler))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started, "local gateway did not start"
            messages = run(
                "Подготовь уведомления по двум карточкам.",
                f"http://127.0.0.1:{port}/v1",
                "synthetic-scripted",
                workspace,
                stream=stream,
                api_key="synthetic-local-token",
                max_steps=6,
            )
        finally:
            server.should_exit = True
            thread.join(timeout=5)
        assert not thread.is_alive()
    assert len(scenario.sent) == 4
    assert messages[-1]["content"] == "Готово."
    for i, card in enumerate(cards, 1):
        output = (workspace / f"out/client{i}.txt").read_text()
        assert output == "Уведомление\n" + card
        assert not re.search(r"\[(?:PERSON|EMAIL|PHONE|PASSPORT_RU)_", output)
        for body in scenario.sent:
            # Decode JSON string values recursively: tool arguments are JSON-in-JSON.
            pending = [body]
            while pending:
                value = pending.pop()
                if isinstance(value, dict):
                    pending.extend(value.values())
                elif isinstance(value, list):
                    pending.extend(value)
                elif isinstance(value, str):
                    assert all(raw not in value for raw in originals(card))
                    try:
                        nested = json.loads(value)
                    except ValueError:
                        continue
                    if isinstance(nested, (dict, list)):
                        pending.append(nested)
