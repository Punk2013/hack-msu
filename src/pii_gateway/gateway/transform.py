"""Text transformers used by the protocol adapter.

The adapter only knows `Transformer`; the real pipeline plugs detect() and
mask()/restore() in, tests and Gate 0 use simpler implementations.
"""

from __future__ import annotations

import re
from typing import Protocol

from pii_gateway.core.types import (
    Context,
    EntitySpan,
    PiiGatewayError,
    PiiType,
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
            spans = _with_known_originals(text, spans, self.ctx)
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


def _with_known_originals(text: str, spans: list[EntitySpan], ctx: Context) -> list[EntitySpan]:
    """Add every value the gateway itself restored earlier, whatever the detector says.

    Restored data comes back in the agent's history on the next turn. Detector recall
    on model-written text (odd word order, short forms) must not decide whether it
    leaves the machine: anything in the project vault is masked again.
    """
    from pii_gateway.vault.storage import Vault

    with Vault(ctx.project_id) as vault:
        known = [(o, t) for o, t in vault.known_originals() if len(o) >= 3]
    if not known:
        return spans
    kinds = dict(known)
    alternation = "|".join(re.escape(o) for o in sorted(kinds, key=len, reverse=True))
    extra = [
        EntitySpan(m.start(), m.end(), PiiType(kinds[m.group(0)]), m.group(0), "vault:known@1")
        for m in re.finditer(rf"(?<!\w)(?:{alternation})(?!\w)", text)
        if not any(m.start() < s.end and s.start < m.end() for s in spans)
    ]
    return sorted([*spans, *extra], key=lambda s: s.start)
