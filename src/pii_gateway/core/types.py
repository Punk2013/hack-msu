"""Shared types for detect / mask / restore (docs/ARCHITECTURE.md §4).

Indices are Unicode code points, ranges are half-open [start, end).
Invariant: text[span.start:span.end] == span.raw_text.
Only role 1 changes this file; request changes in issue #9.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

Mode = Literal["marker", "realistic"]
LinkedBy = Literal["exact", "partial_single", "new"]
UnresolvedReason = Literal["unknown_marker", "unknown_form", "ambiguous_form"]


class PiiType(StrEnum):
    PERSON = "PERSON"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    PASSPORT_RU = "PASSPORT_RU"
    ADDRESS = "ADDRESS"
    SNILS = "SNILS"
    INN = "INN"
    CARD = "CARD"
    BIRTH_DATE = "BIRTH_DATE"


class PiiGatewayError(Exception):
    """Base error. Any subclass makes the gateway refuse the request (fail-closed)."""


class DetectError(PiiGatewayError):
    pass


class MaskError(PiiGatewayError):
    pass


class VaultError(PiiGatewayError):
    pass


@dataclass(frozen=True)
class Context:
    project_id: str
    mode: Mode = "marker"


@dataclass(frozen=True)
class EntitySpan:
    start: int
    end: int
    type: PiiType
    raw_text: str
    detector: str
    confidence: float | None = None
    grammemes: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.start < self.end:
            raise ValueError(f"invalid span range [{self.start}, {self.end})")
        if self.end - self.start != len(self.raw_text):
            raise ValueError("raw_text length does not match span range")


@dataclass(frozen=True)
class Replacement:
    start: int
    end: int
    entity_id: str
    linked_by: LinkedBy
    form: str | None = None


@dataclass(frozen=True)
class MaskResult:
    text: str
    replacements: list[Replacement]


class RestoreStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"


@dataclass(frozen=True)
class Unresolved:
    start: int
    end: int
    reason: UnresolvedReason


@dataclass(frozen=True)
class RestoreResult:
    text: str
    status: RestoreStatus
    unresolved: list[Unresolved]


def check_spans(text: str, spans: list[EntitySpan]) -> None:
    """Validate the detect() output contract: sorted, non-overlapping, matching the text."""
    prev_end = 0
    for span in spans:
        if span.start < prev_end:
            raise DetectError("spans are unsorted or overlapping")
        if text[span.start : span.end] != span.raw_text:
            raise DetectError("span raw_text does not match text slice")
        prev_end = span.end
