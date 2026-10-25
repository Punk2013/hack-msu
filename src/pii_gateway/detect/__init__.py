"""Local PII detection. No network requests or silent regex-only fallback."""

from pii_gateway.detect.detector import detect

__all__ = ["detect"]
