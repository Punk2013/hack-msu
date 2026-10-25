"""PII detection (role 2, issue #10)."""

from pii_gateway.core.types import Context, EntitySpan


def detect(text: str, ctx: Context) -> list[EntitySpan]:
    """Return sorted, non-overlapping spans. Raise DetectError on failure."""
    raise NotImplementedError
