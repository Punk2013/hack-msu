"""Pseudonymization: mask and restore (role 3, issue #11)."""

from pii_gateway.core.types import Context, EntitySpan, MaskResult, RestoreResult


def mask(text: str, spans: list[EntitySpan], ctx: Context) -> MaskResult:
    """Replace spans with markers or pseudonyms. Raise MaskError on failure."""
    raise NotImplementedError


def restore(text: str, ctx: Context) -> RestoreResult:
    """Replace known markers/pseudonyms with originals; never guess unknown ones."""
    raise NotImplementedError
