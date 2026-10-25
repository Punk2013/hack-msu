"""Streaming (SSE) response processing for Chat Completions.

Text is buffered until choice completion: arbitrary-length aliases cannot be
restored safely with a fixed tail. Tool calls are released only after an explicit
tool_calls completion. EOF or DONE without completion discards pending output.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from pii_gateway.core.types import PiiGatewayError
from pii_gateway.gateway.adapter import apply_tool_policy
from pii_gateway.gateway.policy import ToolPolicy
from pii_gateway.gateway.transform import Transformer

MAX_BUFFER_CHARS = 1_048_576


@dataclass
class _ChoiceState:
    pending: str = ""
    buffered_chars: int = 0
    calls: dict[int, dict[str, Any]] = field(default_factory=dict)


class StreamRestorer:
    def __init__(self, t: Transformer, policy: ToolPolicy, hold: int = 64) -> None:
        self.t = t
        self.policy = policy
        _ = hold  # retained for source compatibility; fixed-tail emission is unsafe
        self.choices: dict[int, _ChoiceState] = {}
        self.template: dict[str, Any] = {}

    def _chunk(self, index: int, delta: dict[str, Any], finish: str | None = None) -> str:
        chunk = {
            **self.template,
            "choices": [{"index": index, "delta": delta, "finish_reason": finish}],
        }
        return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    def _flush(self, index: int, finish: str | None) -> list[str]:
        state = self.choices.pop(index, _ChoiceState())
        out, text = [], ""
        if state.pending:
            text = self.t.restore(state.pending).text
        calls = [state.calls[i] for i in sorted(state.calls)]
        allowed, note = [], ""
        if calls:
            if finish == "tool_calls":
                allowed, note = apply_tool_policy(calls, self.t, self.policy)
            else:
                note = "\n[pii-gateway] incomplete tool calls discarded."
        text += note
        if text:
            out.append(self._chunk(index, {"content": text}))
        if allowed:
            deltas = [{**call, "index": i} for i, call in enumerate(allowed)]
            out.append(self._chunk(index, {"tool_calls": deltas}))
        if finish == "tool_calls" and not allowed:
            finish = "stop"
        if finish is not None:
            out.append(self._chunk(index, {}, finish))
        return out

    def feed(self, chunk: dict[str, Any]) -> list[str]:
        self.template = {k: v for k, v in chunk.items() if k != "choices"}
        out: list[str] = []
        if not chunk.get("choices"):
            return [f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"]  # e.g. usage
        for choice in chunk["choices"]:
            index = choice.get("index", 0)
            state = self.choices.setdefault(index, _ChoiceState())
            delta = dict(choice.get("delta") or {})
            text = delta.pop("content", None)
            fragments = [text or ""]
            for tc in delta.get("tool_calls") or []:
                fn = tc.get("function") or {}
                fragments.extend([fn.get("name") or "", fn.get("arguments") or ""])
            state.buffered_chars += sum(len(part) for part in fragments)
            if state.buffered_chars > MAX_BUFFER_CHARS:
                self.choices.clear()
                raise PiiGatewayError("stream choice exceeds local buffer limit")
            for tc in delta.pop("tool_calls", None) or []:
                call = state.calls.setdefault(
                    tc.get("index", 0),
                    {"id": None, "type": "function", "function": {"name": "", "arguments": ""}},
                )
                if tc.get("id"):
                    call["id"] = tc["id"]
                fn = tc.get("function") or {}
                call["function"]["name"] += fn.get("name") or ""
                call["function"]["arguments"] += fn.get("arguments") or ""
            if text:
                state.pending += text
            if delta:  # role, reasoning_content, restored text
                out.append(self._chunk(index, delta))
            finish = choice.get("finish_reason")
            if finish is not None:
                out.extend(self._flush(index, finish))
        return out

    def finish(self) -> list[str]:
        # Transport termination is not a choice completion. Never release calls.
        self.choices.clear()
        return []


async def restore_sse(
    lines: AsyncIterator[str], t: Transformer, policy: ToolPolicy, hold: int = 64
) -> AsyncIterator[bytes]:
    restorer = StreamRestorer(t, policy, hold)
    async for line in lines:
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            for event in restorer.finish():
                yield event.encode()
            yield b"data: [DONE]\n\n"
            return
        for event in restorer.feed(json.loads(data)):
            yield event.encode()
    for event in restorer.finish():
        yield event.encode()
