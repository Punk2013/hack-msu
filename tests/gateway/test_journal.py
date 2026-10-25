import json

import httpx
from fastapi.testclient import TestClient

from pii_gateway.core.types import RestoreResult, RestoreStatus
from pii_gateway.gateway.app import Settings, create_app
from pii_gateway.journal_report import build


class Fake:
    def mask(self, text):
        return text.replace("Иван Иванов", "Пётр Петров")

    def restore(self, text):
        return RestoreResult(text.replace("Пётр Петров", "Иван Иванов"), RestoreStatus.COMPLETE, [])


def upstream(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    reply = {"role": "assistant", "content": "Уважаемый Пётр Петров!"}
    if body.get("stream"):
        sse = ('data: {"choices":[{"index":0,"delta":{"content":"Уважаемый Пётр Петров!"},'
               '"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
    return httpx.Response(200, json={"choices": [{"index": 0, "finish_reason": "stop",
                                                  "message": reply}]})


def test_journal_records_both_directions_and_report_shows_them(tmp_path):
    journal = tmp_path / "r" / "journal.jsonl"
    settings = Settings(upstream_base_url="http://up/v1", workspace=tmp_path, journal=journal)
    client = TestClient(create_app(settings, Fake(), httpx.MockTransport(upstream)))
    body = {"model": "m", "messages": [{"role": "user", "content": "Письмо для Иван Иванов"}]}
    assert client.post("/v1/chat/completions", json=body).status_code == 200
    with client.stream("POST", "/v1/chat/completions", json={**body, "stream": True}) as r:
        r.read()
    records = [json.loads(line) for line in journal.read_text().splitlines()]
    assert len(records) == 2
    for rec in records:
        assert rec["request_in"][0]["content"] == "Письмо для Иван Иванов"
        assert rec["request_out"][0]["content"] == "Письмо для Пётр Петров"
        assert rec["model_said"]["content"] == "Уважаемый Пётр Петров!"
        assert rec["agent_got"]["content"] == "Уважаемый Иван Иванов!"
    assert oct(journal.stat().st_mode)[-3:] == "600"
    page = build(journal)
    assert "<mark class=real>Иван</mark>" in page and "<mark class=fake>Пётр</mark>" in page
    assert "<b>2</b>обращений к модели" in page
