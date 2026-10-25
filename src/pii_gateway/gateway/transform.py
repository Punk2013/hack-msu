"""Text transformers used by the protocol adapter.

The adapter only knows `Transformer`; the real pipeline plugs detect() and
mask()/restore() in, tests and Gate 0 use simpler implementations.
"""

from __future__ import annotations

from typing import Protocol

from pii_gateway.core.types import (
    Context,
    PiiGatewayError,
    RestoreResult,
    RestoreStatus,
    check_spans,
)


class Transformer(Protocol):
    def mask(self, text: str) -> str: ...

    def restore(self, text: str) -> RestoreResult: ...


class PassThrough:
    """Gate 0: forwards everything unchanged."""

    def mask(self, text: str) -> str:
        return text

    def restore(self, text: str) -> RestoreResult:
        return RestoreResult(text, RestoreStatus.COMPLETE, [])


class CorePipeline:
    """detect() -> mask() on the way out, restore() on the way back."""

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx

    def mask(self, text: str) -> str:
        from pii_gateway.detect import detect
        from pii_gateway.pseudo import mask

        try:
            spans = detect(text, self.ctx)
            check_spans(text, spans)
            return mask(text, spans, self.ctx).text
        except PiiGatewayError:
            raise
        except Exception as exc:  # any failure must stop the request (fail-closed)
            raise PiiGatewayError(f"mask pipeline failed: {type(exc).__name__}") from exc

    def restore(self, text: str) -> RestoreResult:
        from pii_gateway.pseudo import restore

        try:
            return restore(text, self.ctx)
        except PiiGatewayError:
            raise
        except Exception as exc:
            raise PiiGatewayError(f"restore failed: {type(exc).__name__}") from exc
