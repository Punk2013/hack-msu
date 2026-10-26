"""Exchange journal for the user's report (PII_JOURNAL=<path to .jsonl>).

One line per model call: what the agent sent, what left the machine, what the
model answered and what the agent received back. It holds the user's real data,
so it is written locally with 0600 permissions and is never sent anywhere.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def summarize_message(message: dict[str, Any] | None) -> dict[str, Any]:
    message = message or {}
    calls = [
        {"name": (c.get("function") or {}).get("name", ""),
         "arguments": (c.get("function") or {}).get("arguments", "")}
        for c in message.get("tool_calls") or []
    ]
    return {"content": message.get("content") or "", "tool_calls": calls}


def summarize_sse(lines: list[str]) -> dict[str, Any]:
    """Collapse SSE chunks (raw upstream lines or emitted events) into one message."""
    content, calls = "", {}
    for line in lines:
        for raw in line.splitlines():
            if not raw.startswith("data:") or raw[5:].strip() == "[DONE]":
                continue
            try:
                chunk = json.loads(raw[5:])
            except json.JSONDecodeError:
                continue
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                content += delta.get("content") or ""
                for tc in delta.get("tool_calls") or []:
                    call = calls.setdefault(tc.get("index", 0), {"name": "", "arguments": ""})
                    fn = tc.get("function") or {}
                    call["name"] += fn.get("name") or ""
                    call["arguments"] += fn.get("arguments") or ""
    return {"content": content, "tool_calls": [calls[i] for i in sorted(calls)]}


class Journal:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.close(fd)

    def write(self, record: dict[str, Any]) -> None:
        record = {"ts": time.time(), **record}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
