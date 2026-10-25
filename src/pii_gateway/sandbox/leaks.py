"""Canary matching at the upstream boundary; reports never contain raw values."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote


@dataclass(frozen=True)
class Canary:
    id: str
    value: str = field(repr=False)
    variants: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self):
        if any(
            not isinstance(value, str) or not value
            for value in (self.id, self.value, *self.variants)
        ):
            raise ValueError("canary IDs and values must be nonempty")


def load_canaries(path: Path) -> list[Canary]:
    data = json.loads(path.read_text())
    if data.get("synthetic") is not True:
        raise ValueError("only explicitly synthetic manifests are accepted")
    result = [
        Canary(row["id"], row["value"], tuple(row.get("variants", []))) for row in data["canaries"]
    ]
    if not result or len({canary.id for canary in result}) != len(result):
        raise ValueError("manifest must contain distinct canary IDs")
    return result


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


class LeakScanner:
    def __init__(self, canaries: list[Canary]):
        if not canaries:
            raise ValueError("at least one canary is required")
        self.canaries = tuple(canaries)

    def scan(self, payload: bytes) -> frozenset[str]:
        raw = payload.decode("utf-8", errors="replace")
        texts = [raw]
        try:
            texts.extend(_strings(json.loads(raw)))
        except (ValueError, RecursionError):
            pass
        # Cover URL arguments and percent-encoded names inside JSON strings.
        # Arbitrary encodings/encryption are outside this finite canary check.
        texts += [unquote(text) for text in texts]
        return frozenset(
            canary.id
            for canary in self.canaries
            if any(value in text for value in (canary.value, *canary.variants) for text in texts)
        )
