"""Minimal file agent for the demo and Gate 0 (fallback client, issue #9).

Talks OpenAI Chat Completions to any base_url (normally the gateway) and has
three local tools confined to the workspace: list_files, read_file, write_file.

    python -m pii_gateway.demo_agent --base-url http://127.0.0.1:8787/v1 \
        --model qwen2.5:3b --workspace demo "Подготовь уведомления клиентам"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

SYSTEM = (
    "Ты помощник, который работает с файлами в рабочей папке. "
    "Используй инструменты list_files, read_file и write_file. "
    "Пиши итоговые документы в папку out/. Отвечай по-русски. "
    "Не описывай план словами: сразу вызывай инструменты. "
    "Заканчивай ответ без вызова инструмента только когда все файлы записаны."
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "Список файлов в папке рабочей директории.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Папка, по умолчанию ."}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Прочитать текстовый файл.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Записать текстовый файл (перезаписывает).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
]


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, rel: str) -> Path:
        if not isinstance(rel, str) or not rel:
            raise ValueError("path must be a non-empty string")
        path = (self.root / rel).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError(f"path outside workspace: {rel}")
        return path

    def run(self, name: str, args: dict[str, Any]) -> str:
        try:
            if not isinstance(args, dict):
                raise ValueError("tool arguments must be a JSON object")
            if name == "list_files":
                base = self._path(args.get("path", "."))
                files = sorted(
                    p.relative_to(self.root).as_posix() for p in base.rglob("*") if p.is_file()
                )
                return "\n".join(files) or "(пусто)"
            if name == "read_file":
                return self._path(args["path"]).read_text(encoding="utf-8")
            if name == "write_file":
                if not isinstance(args.get("content"), str):
                    raise ValueError("content must be a string")
                path = self._path(args["path"])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(args["content"], encoding="utf-8")
                return f"записано: {args['path']}"
            return f"неизвестный инструмент: {name}"
        except (KeyError, ValueError, OSError) as exc:
            return f"ошибка: {exc}"


def _completed_message(message: Any, finish: Any) -> dict[str, Any]:
    """Only an explicitly completed choice can authorize local tool execution."""
    if not isinstance(message, dict) or finish not in ("stop", "tool_calls"):
        raise RuntimeError("model response did not complete successfully")
    calls = message.get("tool_calls")
    if calls:
        if finish != "tool_calls" or not isinstance(calls, list):
            raise RuntimeError("tool calls lack a valid completion")
        for call in calls:
            if not isinstance(call, dict):
                raise RuntimeError("invalid tool call")
            fn = call.get("function")
            if (
                call.get("type") != "function"
                or not isinstance(call.get("id"), str)
                or not call["id"]
                or not isinstance(fn, dict)
                or not isinstance(fn.get("name"), str)
                or not isinstance(fn.get("arguments"), str)
            ):
                raise RuntimeError("invalid tool call")
    elif finish == "tool_calls":
        raise RuntimeError("tool completion contains no calls")
    return message


def _stream_message(client: httpx.Client, payload: dict[str, Any]) -> dict[str, Any]:
    """Collect a streamed response into one assistant message."""
    content, calls = "", {}
    finish = None
    with client.stream("POST", "chat/completions", json={**payload, "stream": True}) as resp:
        if resp.status_code != 200:
            resp.read()
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")
        for line in resp.iter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            choices = json.loads(data).get("choices", [])
            if len(choices) > 1:
                raise RuntimeError("multiple stream choices are unsupported")
            for choice in choices:
                if finish is not None or choice.get("index", 0) != 0:
                    raise RuntimeError("unexpected stream choice after completion")
                finish = choice.get("finish_reason")
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    content += delta["content"]
                    print(delta["content"], end="", flush=True)
                for tc in delta.get("tool_calls") or []:
                    call = calls.setdefault(tc.get("index", 0), {
                        "id": None, "type": "function", "function": {"name": "", "arguments": ""}})
                    call["id"] = tc.get("id") or call["id"]
                    fn = tc.get("function") or {}
                    call["function"]["name"] += fn.get("name") or ""
                    call["function"]["arguments"] += fn.get("arguments") or ""
    message: dict[str, Any] = {"role": "assistant", "content": content or None}
    if calls:
        message["tool_calls"] = [calls[i] for i in sorted(calls)]
    return _completed_message(message, finish)


def run(task: str, base_url: str, model: str, workspace: Path, *,
        stream: bool = False, max_steps: int = 12, api_key: str | None = None) -> list[dict]:
    ws = Workspace(workspace)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    client = httpx.Client(base_url=base_url.rstrip("/") + "/", headers=headers, timeout=600)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": task},
    ]
    for _ in range(max_steps):
        payload = {"model": model, "messages": messages, "tools": TOOLS}
        if stream:
            message = _stream_message(client, payload)
        else:
            resp = client.post("chat/completions", json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")
            choices = resp.json().get("choices", [])
            if len(choices) != 1:
                raise RuntimeError("expected a single response choice")
            choice = choices[0]
            message = _completed_message(choice.get("message"), choice.get("finish_reason"))
            if message.get("content"):
                print(message["content"])
        messages.append(message)
        if not message.get("tool_calls"):
            break
        for call in message["tool_calls"]:
            fn = call["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            result = ws.run(fn["name"], args)
            print(f"\n[tool] {fn['name']}({json.dumps(args, ensure_ascii=False)[:120]})")
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
    else:
        raise RuntimeError("agent step limit reached before completion")
    return messages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("task")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787/v1")
    parser.add_argument("--model", default="qwen2.5:3b")
    parser.add_argument("--workspace", type=Path, default=Path("demo"))
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--max-steps", type=int, default=12)
    args = parser.parse_args(argv)
    try:
        run(args.task, args.base_url, args.model, args.workspace, stream=args.stream,
            max_steps=args.max_steps, api_key=os.environ.get("PII_LOCAL_TOKEN"))
    except RuntimeError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
