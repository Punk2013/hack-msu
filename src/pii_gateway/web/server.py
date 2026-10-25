"""Same-origin local inspector. No upstream calls and no arbitrary vault access."""
from __future__ import annotations

import secrets
import time
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from pii_gateway.core.types import Context
from pii_gateway.detect import detect
from pii_gateway.pseudo import mask, restore

STATIC = Path(__file__).with_name("static")


class TextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=100_000)
    mode: Literal["realistic", "marker"] = "realistic"


def create_app() -> FastAPI:
    """Run through `python -m pii_gateway.web` for an isolated temporary vault."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    token = secrets.token_urlsafe(32)
    project = "playground-" + secrets.token_hex(16)

    @app.middleware("http")
    async def protect(request: Request, call_next):
        origin = request.headers.get("origin")
        expected_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if (origin is not None and origin != expected_origin) or request.headers.get(
            "sec-fetch-site"
        ) == "cross-site":
            response = JSONResponse({"detail": "Cross-origin access is not allowed"}, 403)
        elif request.url.path.startswith("/api/") and not secrets.compare_digest(
            request.headers.get("authorization", "").encode(), f"Bearer {token}".encode()
        ):
            response = JSONResponse({"detail": "Local session authorization required"}, 401)
        else:
            response = await call_next(request)
        response.headers.update({
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; "
                "style-src 'self'; connect-src 'self'; img-src 'self'; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        })
        return response

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        # Do not echo submitted originals in validation diagnostics.
        return JSONResponse({"detail": "Нужны текст до 100 000 символов и допустимый режим."}, 422)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return STATIC.joinpath("index.html").read_text().replace("__SESSION_TOKEN__", token)

    @app.post("/api/process")
    def process(body: TextRequest):
        started = time.perf_counter()
        ctx = Context(f"{project}-{body.mode}", body.mode)
        try:
            spans = detect(body.text, ctx)
            masked = mask(body.text, spans, ctx)
            restored = restore(masked.text, ctx)
        except Exception:
            # A failure is a failure: no simulator, no input or exception text in logs.
            return JSONResponse({"detail": "Обработка отклонена локальным движком."}, 500)
        return {
            "masked_text": masked.text,
            "restored_text": restored.text,
            "status": restored.status,
            "exact": restored.text == body.text,
            "spans": [asdict(span) for span in spans],
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "scope": "local_transform_only",
        }

    @app.post("/api/restore")
    def restore_answer(body: TextRequest):
        try:
            result = restore(body.text, Context(f"{project}-{body.mode}", body.mode))
        except Exception:
            return JSONResponse({"detail": "Восстановление отклонено локальным движком."}, 500)
        return {"restored_text": result.text, "status": result.status,
                "unresolved": [asdict(item) for item in result.unresolved]}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
