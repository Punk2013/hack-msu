"""Format-preserving fake values for structured PII (realistic mode).

The agent must not notice that it works with pseudonymized data, so a fake keeps
the layout of the original: separators, "+7"/"8" prefix, line breaks in a
passport number. Values are deterministic per (entity_id, attempt); the caller
bumps `attempt` when a fake collides with a known original in the vault.
"""

from __future__ import annotations

import hashlib
import random

from pii_gateway.core.types import PiiType

# Public mail providers: a fake address must not point at the person's employer.
_DOMAINS = ("mail.ru", "yandex.ru", "gmail.com", "inbox.ru", "bk.ru")
_FIRST = ("alex", "dmitry", "sergey", "andrey", "maria", "elena", "olga", "pavel", "nikita",
          "irina", "anton", "daria", "kirill", "vera", "roman", "polina")
_LAST = ("sokolov", "morozov", "volkova", "lebedev", "kozlova", "novikov", "orlova", "zaitsev",
         "pavlova", "semenov", "golubeva", "vinogradov", "bogdanova", "frolov")


def _rng(entity_id: str, attempt: int) -> random.Random:
    digest = hashlib.sha256(f"{entity_id}:{attempt}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _replace_digits(original: str, rng: random.Random, keep_prefix: int = 0) -> str:
    out, seen = [], 0
    for ch in original:
        if ch.isdigit():
            out.append(ch if seen < keep_prefix else str(rng.randrange(10)))
            seen += 1
        else:
            out.append(ch)
    return "".join(out)


def fake_phone(original: str, entity_id: str, attempt: int = 0) -> str:
    rng = _rng(entity_id, attempt)
    digits = [c for c in original if c.isdigit()]
    keep = 1 if digits and digits[0] in "78" else 0  # country/trunk prefix stays
    fake = _replace_digits(original, rng, keep)
    # mobile-looking: first national digit is 9
    chars, seen = list(fake), 0
    for i, ch in enumerate(chars):
        if ch.isdigit():
            if seen == keep:
                chars[i] = "9"
                break
            seen += 1
    return "".join(chars)


def fake_passport(original: str, entity_id: str, attempt: int = 0) -> str:
    return _replace_digits(original, _rng(entity_id, attempt))


def fake_email(original: str, entity_id: str, attempt: int = 0) -> str:
    rng = _rng(entity_id, attempt)
    sep = rng.choice((".", "_", ""))
    local = f"{rng.choice(_FIRST)}{sep}{rng.choice(_LAST)}{rng.randrange(10, 99)}"
    return f"{local}@{rng.choice(_DOMAINS)}"


_GENERATORS = {
    PiiType.PHONE: fake_phone,
    PiiType.PASSPORT_RU: fake_passport,
    PiiType.EMAIL: fake_email,
}


def fake_value(kind: PiiType, original: str, entity_id: str, attempt: int = 0) -> str | None:
    """Fake for a structured type, or None if this type has no generator (use a marker)."""
    generator = _GENERATORS.get(kind)
    if generator is None:
        return None
    fake = generator(original, entity_id, attempt)
    if fake == original:  # astronomically rare, but never return the original
        return generator(original, entity_id, attempt + 1)
    return fake
