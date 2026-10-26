"""FastAPI app: OpenAI-compatible endpoint in front of the upstream provider.

Run:  PII_UPSTREAM_BASE_URL=http://localhost:11434/v1 \
      uvicorn pii_gateway.gateway.app:app --port 8787
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from pii_gateway.core.types import Context, PiiGatewayError
from pii_gateway.gateway.adapter import UnsupportedRequest, mask_request, restore_response
from pii_gateway.gateway.journal import Journal, summarize_message, summarize_sse
from pii_gateway.gateway.policy import ToolPolicy
from pii_gateway.gateway.stream import restore_sse
from pii_gateway.gateway.transform import CorePipeline, PassThrough, Transformer


@dataclass
class Settings:
    upstream_base_url: str = "http://localhost:11434/v1"
    upstream_api_key: str | None = None
    local_token: str | None = None
    project_id: str = "default"
    mode: str = "realistic"
    passthrough: bool = False
    workspace: Path = field(default_factory=Path.cwd)
    write_roots: list[Path] = field(default_factory=list)
    dump_dir: Path | None = None  # synthetic data only: full request/response dumps
    journal: Path | None = None  # user's local report: original vs masked, both directions

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ.get
        workspace = Path(env("PII_WORKSPACE", ".")).resolve()
        roots = env("PII_WRITE_ROOTS")
        return cls(
            upstream_base_url=env("PII_UPSTREAM_BASE_URL", cls.upstream_base_url),
            upstream_api_key=env("PII_UPSTREAM_API_KEY"),
            local_token=env("PII_LOCAL_TOKEN"),
            project_id=env("PII_PROJECT_ID", "default"),
            mode=env("PII_MODE", "realistic"),
            passthrough=env("PII_PASSTHROUGH") == "1",
            workspace=workspace,
            write_roots=[Path(p) for p in roots.split(":")] if roots else [workspace],
            dump_dir=Path(env("PII_DUMP_DIR")) if env("PII_DUMP_DIR") else None,
            journal=Path(env("PII_JOURNAL")) if env("PII_JOURNAL") else None,
        )


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": message, "type": "pii_gateway_error"}}, status_code=status
    )


def create_app(
    settings: Settings,
    transformer: Transformer | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    if transformer is None:
        transformer = (
            PassThrough()
            if settings.passthrough
            else CorePipeline(Context(settings.project_id, settings.mode))  # type: ignore[arg-type]
        )
    policy = ToolPolicy(settings.workspace, settings.write_roots or [settings.workspace])
    client = httpx.AsyncClient(
        base_url=settings.upstream_base_url.rstrip("/") + "/",
        transport=transport,
        timeout=httpx.Timeout(300.0, connect=10.0),
    )
    app = FastAPI(title="pii-gateway")

    journal = Journal(settings.journal) if settings.journal else None

    def record(body: dict, outbound: dict, upstream: dict, restored: dict) -> None:
        if journal is not None:
            journal.write({"request_in": body.get("messages", []),
                           "request_out": outbound.get("messages", []),
                           "model_said": upstream, "agent_got": restored})

    def dump(kind: str, payload: Any) -> None:
        if settings.dump_dir is None:
            return
        settings.dump_dir.mkdir(parents=True, exist_ok=True)
        name = f"{time.time_ns()}-{kind}.json"
        (settings.dump_dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    def upstream_headers(request: Request) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if settings.upstream_api_key:
            headers["Authorization"] = f"Bearer {settings.upstream_api_key}"
        elif settings.local_token is None and request.headers.get("authorization"):
            headers["Authorization"] = request.headers["authorization"]
        return headers

    def authorized(request: Request) -> bool:
        if settings.local_token is None:
            return True
        return request.headers.get("authorization") == f"Bearer {settings.local_token}"

    @app.get("/v1/models")
    async def models(request: Request):
        if not authorized(request):
            return _error(401, "invalid local token")
        resp = await client.get("models", headers=upstream_headers(request))
        return JSONResponse(resp.json(), status_code=resp.status_code)

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        if not authorized(request):
            return _error(401, "invalid local token")
        try:
            body = await request.json()
            outbound = mask_request(body, transformer)
        except UnsupportedRequest as exc:
            return _error(400, f"request refused by pii-gateway: {exc}")
        except PiiGatewayError as exc:
            return _error(500, f"pii-gateway filter failed, request not sent: {exc}")
        except ValueError:
            return _error(400, "request body is not valid JSON")
        dump("request", outbound)
        headers = upstream_headers(request)

        if not outbound.get("stream"):
            resp = await client.post("chat/completions", json=outbound, headers=headers)
            if resp.status_code != 200:
                return JSONResponse(resp.json(), status_code=resp.status_code)
            upstream_body = resp.json()
            dump("response", upstream_body)
            try:
                restored = restore_response(upstream_body, transformer, policy)
            except PiiGatewayError as exc:
                return _error(502, f"pii-gateway restore failed: {exc}")
            first = lambda b: ((b.get("choices") or [{}])[0]).get("message")  # noqa: E731
            record(body, outbound, summarize_message(first(upstream_body)),
                   summarize_message(first(restored)))
            return JSONResponse(restored)

        req = client.build_request("POST", "chat/completions", json=outbound, headers=headers)
        resp = await client.send(req, stream=True)
        if resp.status_code != 200:
            content = await resp.aread()
            await resp.aclose()
            return JSONResponse(json.loads(content or b"{}"), status_code=resp.status_code)

        upstream_lines: list[str] = []
        emitted: list[str] = []

        async def tee():
            async for line in resp.aiter_lines():
                upstream_lines.append(line)
                yield line

        async def events():
            try:
                async for event in restore_sse(tee(), transformer, policy):
                    emitted.append(event.decode() if isinstance(event, bytes) else event)
                    yield event
            finally:
                await resp.aclose()
                record(body, outbound, summarize_sse(upstream_lines), summarize_sse(emitted))

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def unsupported(path: str):
        return _error(404, f"endpoint /{path} is not supported by pii-gateway")

    return app


def __getattr__(name: str):
    # lazy module-level app for `uvicorn pii_gateway.gateway.app:app`
    if name == "app":
        return create_app(Settings.from_env())
    raise AttributeError(name)
