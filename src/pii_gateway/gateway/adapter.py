"""OpenAI Chat Completions adapter (docs/ARCHITECTURE.md §5).

Requests are walked field by field; nothing is replaced in serialized JSON.
Anything the adapter does not understand is refused (fail-closed).
"""

from __future__ import annotations

import json
from typing import Any

from pii_gateway.core.types import PiiGatewayError
from pii_gateway.gateway.policy import ToolPolicy, map_strings
from pii_gateway.gateway.transform import Transformer

ROLES = {"system", "developer", "user", "assistant", "tool"}
UNSUPPORTED_PARAMS = ("logit_bias", "prediction", "audio", "modalities")
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


def _mask_content(content: Any, t: Transformer) -> Any:
    if content is None:
        return None
    if isinstance(content, str):
        return t.mask(content)
    if isinstance(content, list):
        parts = []
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "text":
                kind = part.get("type") if isinstance(part, dict) else type(part).__name__
                raise UnsupportedRequest(f"unsupported content part: {kind}")
            parts.append({**part, "text": t.mask(part.get("text", ""))})
        return parts
    raise UnsupportedRequest("unsupported message content")


def _mask_schema(schema: Any, t: Transformer) -> Any:
    """Mask descriptions; refuse PII in any other schema string (enum, default, ...)."""
    if isinstance(schema, dict):
        out = {}
        for key, value in schema.items():
            if key == "description" and isinstance(value, str):
                out[key] = t.mask(value)
            else:
                out[key] = _mask_schema(value, t)
        return out
    if isinstance(schema, list):
        return [_mask_schema(v, t) for v in schema]
    if isinstance(schema, str) and t.mask(schema) != schema:
        raise UnsupportedRequest("personal data in tool schema values")
    return schema


def mask_request(body: dict[str, Any], t: Transformer) -> dict[str, Any]:
    if body.get("n", 1) != 1:
        raise UnsupportedRequest("n > 1 is not supported")
    for param in UNSUPPORTED_PARAMS:
        if param in body:
            raise UnsupportedRequest(f"parameter {param!r} is not supported")
    messages = body.get("messages")
    if not isinstance(messages, list):
        raise UnsupportedRequest("messages must be a list")

    out = dict(body)
    out["messages"] = []
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") not in ROLES:
            raise UnsupportedRequest("unsupported message role")
        m = dict(msg)
        m["content"] = _mask_content(msg.get("content"), t)
        for key in ("name", "refusal"):
            if isinstance(msg.get(key), str):
                m[key] = t.mask(msg[key])
        if msg.get("tool_calls"):
            calls = []
            for call in msg["tool_calls"]:
                fn = dict(call.get("function") or {})
                args = map_strings(_parse_arguments(fn.get("arguments", "")), t.mask)
                fn["arguments"] = _dump(args)
                calls.append({**call, "function": fn})
            m["tool_calls"] = calls
        out["messages"].append(m)

    if body.get("tools"):
        tools = []
        for tool in body["tools"]:
            fn = dict(tool.get("function") or {})
            if isinstance(fn.get("description"), str):
                fn["description"] = t.mask(fn["description"])
            if "parameters" in fn:
                fn["parameters"] = _mask_schema(fn["parameters"], t)
            tools.append({**tool, "function": fn})
        out["tools"] = tools
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


def restore_response(
    body: dict[str, Any], t: Transformer, policy: ToolPolicy
) -> dict[str, Any]:
    out = dict(body)
    choices = []
    for choice in body.get("choices", []):
        c = dict(choice)
        msg = dict(choice.get("message") or {})
        for key in ("content", "refusal"):
            if isinstance(msg.get(key), str):
                msg[key] = t.restore(msg[key]).text
        if msg.get("tool_calls"):
            allowed, note = apply_tool_policy(msg["tool_calls"], t, policy)
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
