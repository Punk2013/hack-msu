"""OpenAI Chat Completions adapter (docs/ARCHITECTURE.md §5).

Requests are walked field by field; nothing is replaced in serialized JSON.
Anything the adapter does not understand is refused (fail-closed).
"""

from __future__ import annotations

import json
import math
from typing import Any

from pii_gateway.core.types import PiiGatewayError
from pii_gateway.gateway.policy import ToolPolicy
from pii_gateway.gateway.transform import Transformer

ROLES = {"system", "developer", "user", "assistant", "tool"}
REQUEST_FIELDS = {
    "model",
    "messages",
    "tools",
    "tool_choice",
    "stream",
    "stream_options",
    "temperature",
    "top_p",
    "max_tokens",
    "max_completion_tokens",
    "n",
    "stop",
    "presence_penalty",
    "frequency_penalty",
    "seed",
    "parallel_tool_calls",
    "store",  # pi sends store=false: asks the provider not to keep the request
}
BLOCKED_NOTE = "\n[pii-gateway] вызов `{name}` заблокирован политикой: {reason}."


class UnsupportedRequest(PiiGatewayError):
    """The request contains something the gateway cannot check; it is not forwarded."""


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, str):
        raise UnsupportedRequest("tool call arguments must be a JSON string")
    try:
        value = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        raise UnsupportedRequest("tool call arguments are not valid JSON") from exc
    if not isinstance(value, dict):
        raise UnsupportedRequest("tool call arguments must be a JSON object")
    return value


def _dump(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False)


def _object(value: Any, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or value.keys() - fields:
        # Do not echo unknown keys: they may themselves contain personal data.
        raise UnsupportedRequest("unsupported object fields or type")
    return value


def _string(value: Any) -> str:
    if not isinstance(value, str):
        raise UnsupportedRequest("expected a string")
    return value


def _control(value: Any, t: Transformer) -> Any:
    """Preserve protocol identifiers only when detection leaves them unchanged."""
    value = _string(value)
    if t.mask(value) != value:
        raise UnsupportedRequest("personal data in protocol identifiers or schema")
    return value


def _mask_json(value: Any, t: Transformer, *, schema: bool = False) -> Any:
    """Keys cannot be renamed without changing tool semantics: refuse PII keys."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            _control(key, t)
            if schema and key == "description" and isinstance(item, str):
                out[key] = t.mask(item)
            else:
                out[key] = _mask_json(item, t, schema=schema)
        return out
    if isinstance(value, list):
        return [_mask_json(v, t, schema=schema) for v in value]
    if isinstance(value, str):
        return _control(value, t) if schema else t.mask(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if (isinstance(value, float) and not math.isfinite(value)) or t.mask(str(value)) != str(
            value
        ):
            raise UnsupportedRequest("unsafe numeric JSON value")
    return value


def _mask_content(content: Any, t: Transformer) -> Any:
    if content is None:
        return None
    if isinstance(content, str):
        return t.mask(content)
    if isinstance(content, list):
        parts = []
        for part in content:
            part = _object(part, {"type", "text"})
            if part.get("type") != "text":
                raise UnsupportedRequest("unsupported content part")
            parts.append({"type": "text", "text": t.mask(_string(part.get("text")))})
        return parts
    raise UnsupportedRequest("unsupported message content")


def _function(value: Any, t: Transformer, *, definition: bool) -> dict[str, Any]:
    fields = (
        {"name", "description", "parameters", "strict"} if definition else {"name", "arguments"}
    )
    fn = _object(value, fields)
    out = {"name": _control(fn.get("name"), t)}
    if definition:
        if "description" in fn:
            out["description"] = t.mask(_string(fn["description"]))
        if "parameters" in fn:
            if not isinstance(fn["parameters"], dict):
                raise UnsupportedRequest("parameters must be an object")
            out["parameters"] = _mask_json(fn["parameters"], t, schema=True)
        if "strict" in fn:
            if type(fn["strict"]) is not bool:
                raise UnsupportedRequest("strict must be boolean")
            out["strict"] = fn["strict"]
    else:
        out["arguments"] = _dump(_mask_json(_parse_arguments(fn.get("arguments")), t))
    return out


def _tools(value: Any, t: Transformer, *, definition: bool) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise UnsupportedRequest("tools must be a list")
    out = []
    for item in value:
        fields = {"type", "function"} if definition else {"id", "type", "function", "index"}
        tool = _object(item, fields)
        if tool.get("type") != "function":
            raise UnsupportedRequest("only function tools are supported")
        result = {
            "type": "function",
            "function": _function(tool.get("function"), t, definition=definition),
        }
        if not definition:
            # Ollama includes this stream-only field in ordinary response history.
            # Validate and drop it; never copy arbitrary extension values upstream.
            if "index" in tool and (type(tool["index"]) is not int or tool["index"] < 0):
                raise UnsupportedRequest("tool call index must be a non-negative integer")
            result["id"] = _control(tool.get("id"), t)
        out.append(result)
    return out


def mask_request(body: dict[str, Any], t: Transformer) -> dict[str, Any]:
    body = _object(body, REQUEST_FIELDS)
    out = {"model": _control(body.get("model"), t)}
    if "n" in body and (type(body["n"]) is not int or body["n"] != 1):
        raise UnsupportedRequest("only n=1 is supported")
    for key in (
        "temperature",
        "top_p",
        "presence_penalty",
        "frequency_penalty",
        "max_tokens",
        "max_completion_tokens",
        "seed",
        "n",
    ):
        if key in body:
            value = body[key]
            integer = key in {"max_tokens", "max_completion_tokens", "seed", "n"}
            if type(value) not in ((int,) if integer else (int, float)) or (
                isinstance(value, float) and not math.isfinite(value)
            ):
                raise UnsupportedRequest("invalid numeric parameter")
            out[key] = _mask_json(value, t)
    for key in ("stream", "parallel_tool_calls", "store"):
        if key in body:
            if type(body[key]) is not bool:
                raise UnsupportedRequest("invalid boolean parameter")
            out[key] = body[key]
    if "stream_options" in body:
        options = _object(body["stream_options"], {"include_usage"})
        if any(type(v) is not bool for v in options.values()):
            raise UnsupportedRequest("invalid stream options")
        out["stream_options"] = dict(options)
    if "stop" in body:
        stop = body["stop"]
        if stop is None:
            out["stop"] = None
        elif isinstance(stop, list):
            out["stop"] = [_control(v, t) for v in stop]
        else:
            out["stop"] = _control(stop, t)
    if "tool_choice" in body:
        choice = body["tool_choice"]
        if isinstance(choice, str):
            if choice not in {"auto", "none", "required"}:
                raise UnsupportedRequest("unsupported tool choice")
            out["tool_choice"] = choice
        else:
            choice = _object(choice, {"type", "function"})
            fn = _object(choice.get("function"), {"name"})
            if choice.get("type") != "function":
                raise UnsupportedRequest("unsupported tool choice")
            out["tool_choice"] = {
                "type": "function",
                "function": {"name": _control(fn.get("name"), t)},
            }
    messages = body.get("messages")
    if not isinstance(messages, list):
        raise UnsupportedRequest("messages must be a list")
    out["messages"] = []
    for msg in messages:
        msg = _object(
            msg,
            {"role", "content", "name", "refusal", "tool_calls", "tool_call_id", "function_call"},
        )
        if msg.get("function_call") is not None:
            raise UnsupportedRequest("legacy function calls are not supported")
        role = msg.get("role")
        if not isinstance(role, str) or role not in ROLES:
            raise UnsupportedRequest("unsupported message role")
        m = {"role": role, "content": _mask_content(msg.get("content"), t)}
        for key in ("name", "tool_call_id"):
            if key in msg:
                m[key] = _control(msg[key], t)
        if "refusal" in msg:
            m["refusal"] = None if msg["refusal"] is None else t.mask(_string(msg["refusal"]))
        if "tool_calls" in msg:
            m["tool_calls"] = _tools(msg["tool_calls"], t, definition=False)
        out["messages"].append(m)
    if "tools" in body:
        out["tools"] = _tools(body["tools"], t, definition=True)
    return out


def apply_tool_policy(
    calls: list[dict[str, Any]], t: Transformer, policy: ToolPolicy
) -> tuple[list[dict[str, Any]], str]:
    """Return allowed calls with restored arguments and a note about blocked ones."""
    allowed, note = [], ""
    for call in calls:
        fn = call.get("function") or {}
        name = fn.get("name", "")
        try:
            args = _parse_arguments(fn.get("arguments", ""))
        except UnsupportedRequest:
            note += BLOCKED_NOTE.format(name=name, reason="invalid arguments")
            continue
        decision = policy.decide(name, args, t)
        if not decision.allowed:
            note += BLOCKED_NOTE.format(name=name, reason=decision.reason)
            continue
        allowed.append({**call, "function": {**fn, "arguments": _dump(decision.arguments)}})
    return allowed, note


def restore_response(body: dict[str, Any], t: Transformer, policy: ToolPolicy) -> dict[str, Any]:
    out = dict(body)
    choices = []
    for choice in body.get("choices", []):
        c = dict(choice)
        msg = dict(choice.get("message") or {})
        for key in ("content", "refusal"):
            if isinstance(msg.get(key), str):
                msg[key] = t.restore(msg[key]).text
        if msg.get("tool_calls"):
            if choice.get("finish_reason") == "tool_calls":
                allowed, note = apply_tool_policy(msg["tool_calls"], t, policy)
            else:
                allowed, note = [], "\n[pii-gateway] incomplete tool calls discarded."
            if note:
                msg["content"] = (msg.get("content") or "") + note
            if allowed:
                msg["tool_calls"] = allowed
            else:
                msg.pop("tool_calls")
                c["finish_reason"] = "stop"
        c["message"] = msg
        choices.append(c)
    out["choices"] = choices
    return out
