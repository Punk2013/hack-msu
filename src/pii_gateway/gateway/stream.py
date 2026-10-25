"""Streaming (SSE) response processing for Chat Completions.

Text deltas are restored with a held-back tail so a marker or pseudonym split
across chunks is never emitted half-restored. Tool call deltas are collected
until the choice finishes, then passed through the tool policy as a whole.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from pii_gateway.gateway.adapter import apply_tool_policy
from pii_gateway.gateway.policy import ToolPolicy
from pii_gateway.gateway.transform import Transformer


def _safe_cut(pending: str, hold: int, t: Transformer) -> int:
    """Largest whitespace cut that keeps `hold` chars back and splits no replacement."""
    whole = t.restore(pending).text
    cut = len(pending) - hold
    while cut > 0:
        cut = max(pending.rfind(" ", 0, cut), pending.rfind("\n", 0, cut))
        if cut <= 0:
            return 0
        cut += 1  # keep the whitespace in the emitted part
        if t.restore(pending[:cut]).text + t.restore(pending[cut:]).text == whole:
            return cut
        cut -= 1
    return 0


@dataclass
class _ChoiceState:
    pending: str = ""
    calls: dict[int, dict[str, Any]] = field(default_factory=dict)


class StreamRestorer:
    def __init__(self, t: Transformer, policy: ToolPolicy, hold: int = 64) -> None:
        self.t = t
        self.policy = policy
        self.hold = hold
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
        allowed, note = apply_tool_policy(calls, self.t, self.policy) if calls else ([], "")
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
                cut = _safe_cut(state.pending, self.hold, self.t)
                if cut:
                    delta["content"] = self.t.restore(state.pending[:cut]).text
                    state.pending = state.pending[cut:]
            if delta:  # role, reasoning_content, restored text
                out.append(self._chunk(index, delta))
            finish = choice.get("finish_reason")
            if finish is not None:
                out.extend(self._flush(index, finish))
        return out

    def finish(self) -> list[str]:
        out = []
        for index in list(self.choices):
            out.extend(self._flush(index, None))
        return out


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
