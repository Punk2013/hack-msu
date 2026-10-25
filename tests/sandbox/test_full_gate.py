"""Real agent, filesystem, gateway pipeline and namespaces; scripted HTTP provider."""

import json
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn

from pii_gateway.gateway.app import Settings, create_app
from pii_gateway.sandbox.leaks import Canary, LeakScanner
from pii_gateway.sandbox.runner import GatewayAddress, SandboxConfig, run_sandbox
from pii_gateway.sandbox.upstream_spy import SpyResponse, UpstreamSpy

DEMO = Path(__file__).resolve().parents[2] / "demo/clients"
FIELDS = re.compile(r"^(?:ФИО|Email|Телефон|Паспорт): (.+)$", re.MULTILINE)


@pytest.fixture
def config(monkeypatch):
    # Short paths also fit the UNIX socket limit on macOS (runtime then skips).
    with tempfile.TemporaryDirectory(prefix="pii-gate-", dir="/tmp") as directory:
        root = Path(directory)
        work = root / "workspace"
        shutil.copytree(DEMO, work / "clients")
        output = work / "out"
        output.mkdir()
        monkeypatch.setenv("PII_VAULT_DIR", str(root / "private-vault"))
        yield SandboxConfig(work, output)


def scanner_for(config):
    canaries = []
    for path in sorted((config.workspace / "clients").glob("*.txt")):
        values = FIELDS.findall(path.read_text(encoding="utf-8"))
        assert len(values) == 4
        canaries.extend(Canary(f"{path.stem}-{i}", value) for i, value in enumerate(values))
    assert len(canaries) == 8
    return LeakScanner(canaries)


def as_sse(payload):
    """Emit logical SSE chunks, splitting tool JSON even inside escaped strings."""
    choice = payload["choices"][0]
    message = choice["message"]
    deltas = [{"role": "assistant"}]
    if message.get("content"):
        text = message["content"]
        deltas.extend({"content": text[i:i + 3]} for i in range(0, len(text), 3))
    for index, call in enumerate(message.get("tool_calls", [])):
        args = call["function"]["arguments"]
        for offset in range(0, len(args), 13):
            part = {"index": index, "function": {"arguments": args[offset:offset + 13]}}
            if offset == 0:
                part.update(id=call["id"], type="function")
                part["function"]["name"] = call["function"]["name"]
            deltas.append({"tool_calls": [part]})
    events = [
        {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
        for delta in deltas
    ]
    events.append({"choices": [{"index": 0, "delta": {},
                                "finish_reason": choice["finish_reason"]}]})
    body = "".join("data: " + json.dumps(event, ensure_ascii=False) + "\n\n" for event in events)
    return SpyResponse((body + "data: [DONE]\n\n").encode(), "text/event-stream")


class CopyCards:
    """Provider sees only HTTP requests, echoes received tool text into a write call."""

    def __init__(self, stream=False):
        self.steps = 0
        self.stream = stream

    def __call__(self, body):
        step = self.steps
        self.steps += 1
        assert bool(body.get("stream")) == self.stream
        assert step < 5
        message = {"role": "assistant", "content": None}
        finish = "tool_calls"
        if step == 4:
            # A fifth request proves both writes finished and their results were sent.
            results = [m["content"] for m in body["messages"] if m["role"] == "tool"]
            assert len(results) == 4
            assert results[1] == "записано: out/client1.txt"
            assert results[3] == "записано: out/client2.txt"
            message["content"] = "Два файла готовы."
            finish = "stop"
        else:
            card = step // 2 + 1
            args = {"path": f"clients/client{card}.txt"}
            tool = "read_file"
            if step % 2:
                assert body["messages"][-1]["role"] == "tool"
                tool = "write_file"
                args = {
                    "path": f"out/client{card}.txt",
                    "content": body["messages"][-1]["content"],
                }
            message["tool_calls"] = [{
                "id": f"step-{step}", "type": "function",
                "function": {"name": tool, "arguments": json.dumps(args, ensure_ascii=False)},
            }]
        result = {"id": f"synthetic-{step}", "object": "chat.completion", "model": "synthetic",
                  "choices": [{"index": 0, "finish_reason": finish, "message": message}]}
        return as_sse(result) if self.stream else result


@contextmanager
def running_gateway(settings):
    app = create_app(settings)  # actual CorePipeline, detect, pseudo and SQLite vault
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started
            yield port
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive()


def run_agent(config, port, task="Скопируй обе карточки клиентов в out/.", *, stream=False):
    runtimes = tuple(dict.fromkeys(
        Path(root).resolve() for root in (sys.prefix, sys.base_prefix)
        if not Path(root).resolve().is_relative_to(Path("/usr"))
    ))
    actual = SandboxConfig(
        config.workspace, config.write_root, GatewayAddress("127.0.0.1", port),
        runtime_roots=runtimes, demo_agent=True,
    )
    command = [
        sys.executable, "--base-url", "http://127.0.0.1:8787/v1",
        "--workspace", str(config.workspace), "--model", "synthetic", "--max-steps", "8", task,
    ]
    if stream:
        command.append("--stream")
    return run_sandbox(actual, command, capture_output=True, timeout=60)


@pytest.mark.parametrize("passthrough", [False, True], ids=["filtered", "positive-leak-control"])
@pytest.mark.parametrize("stream", [False, True], ids=["json", "sse"])
def test_real_agent_reads_writes_and_resends_history(working_sandbox, passthrough, stream):
    config = working_sandbox
    provider = CopyCards(stream=stream)
    with UpstreamSpy(scanner_for(config), responder=provider) as spy:
        settings = Settings(
            upstream_base_url=spy.base_url, local_token=config.local_token,
            project_id="sandbox-full-gate", mode="realistic", passthrough=passthrough,
            workspace=config.workspace, write_roots=[config.write_root],
        )
        with running_gateway(settings) as port:
            result = run_agent(config, port, stream=stream)
        assert result.returncode == 0, result.stderr
        assert "Два файла готовы." in result.stdout
        assert provider.steps == 5
        for name in ("client1.txt", "client2.txt"):
            assert (config.write_root / name).read_bytes() == (
                config.workspace / "clients" / name
            ).read_bytes()
        report = spy.report()
        assert report["requests"] == 5
        assert report["requests_with_canaries"] == (4 if passthrough else 0)
        assert report["unexpected_requests"] == report["unscanned_requests"] == 0
        assert report["inflight_requests"] == report["transport_errors"] == 0
        assert report["verdict"] == ("leak" if passthrough else "clean")
        assert len(report["canary_ids"]) == (8 if passthrough else 0)
        if not passthrough:
            vault = config.workspace.parent / "private-vault/sandbox-full-gate.db"
            assert vault.is_file()
            assert not vault.is_relative_to(config.workspace)


@pytest.mark.parametrize("target", [
    "pii_gateway.detect.detector._person_spans", "pii_gateway.pseudo.engine.Vault",
])
@pytest.mark.parametrize("stream", [False, True], ids=["json", "sse"])
def test_real_agent_filter_failure_never_reaches_provider(
    working_sandbox, monkeypatch, target, stream
):
    config = working_sandbox
    original = "Иван Петрович Иванов"

    def broken(*args, **kwargs):
        raise RuntimeError("injected failure: " + original)

    monkeypatch.setattr(target, broken)
    with UpstreamSpy(scanner_for(config), responder=CopyCards(stream=stream)) as spy:
        settings = Settings(
            upstream_base_url=spy.base_url, local_token=config.local_token,
            project_id="sandbox-failure", workspace=config.workspace,
            write_roots=[config.write_root],
        )
        with running_gateway(settings) as port:
            result = run_agent(config, port, "Подготовь письмо: " + original, stream=stream)
        assert result.returncode == 1
        assert "HTTP 500" in result.stderr
        assert original not in result.stderr
        assert not list(config.write_root.iterdir())
        assert spy.report()["requests"] == 0
        assert spy.report()["verdict"] == "no_requests"
