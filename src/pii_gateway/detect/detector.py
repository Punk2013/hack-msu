"""Four-type MVP detector; offsets always refer to the unmodified input."""

from __future__ import annotations

import re
from threading import Lock

from pii_gateway.core.types import Context, DetectError, EntitySpan, PiiType, check_spans

# Bounded fields avoid consuming adjacent numbers or sentence punctuation.
_EMAIL = re.compile(
    r"(?<![\w.+%-])[\w.!#$%&'*+/=?^`{|}~-]+@"
    r"[\w](?:[\w-]*[\w])?(?:\.[\w](?:[\w-]*[\w])?)+(?![\w@-])"
)
_SEP = r"[\s-]?"
_NATIONAL_PHONE = rf"(?:\([0-9]{{3}}\)|[0-9]{{3}}){_SEP}[0-9]{{3}}{_SEP}[0-9]{{2}}{_SEP}[0-9]{{2}}"
_PHONE = re.compile(rf"(?<![\w+])(?:\+7|8){_SEP}{_NATIONAL_PHONE}(?!\w)")
_LABELED_PHONE = re.compile(
    rf"\b(?:телефон|тел\.?|phone)\s*[:№]?\s*(?P<value>{_NATIONAL_PHONE})(?!\w)",
    re.IGNORECASE,
)
# A separated 4+6 value is conservatively sensitive even without a label.
_PASSPORT = re.compile(r"(?<!\w)[0-9]{2}\s?[0-9]{2}\s+[0-9]{6}(?!\w)")
_LABELED_PASSPORT = re.compile(
    r"\b(?:паспорт(?:\s+РФ)?|passport)\s*[:№]?\s*"
    r"(?P<value>[0-9]{2}\s?[0-9]{2}\s*[0-9]{6})(?!\w)",
    re.IGNORECASE,
)
_RULES = (
    (_EMAIL, PiiType.EMAIL, "regex:email@1", 0),
    (_PHONE, PiiType.PHONE, "regex:phone_ru@1", 0),
    (_LABELED_PHONE, PiiType.PHONE, "regex:phone_ru_labeled@1", "value"),
    (_PASSPORT, PiiType.PASSPORT_RU, "regex:passport_ru@1", 0),
    (_LABELED_PASSPORT, PiiType.PASSPORT_RU, "regex:passport_ru_labeled@1", "value"),
)
_PRIORITY = {PiiType.EMAIL: 0, PiiType.PASSPORT_RU: 1, PiiType.PHONE: 2, PiiType.PERSON: 3}
_ner_lock = Lock()
_ner = None


def _regex_spans(text: str) -> list[EntitySpan]:
    return [
        EntitySpan(*match.span(group), kind, match.group(group), detector=source)
        for pattern, kind, source, group in _RULES
        for match in pattern.finditer(text)
    ]


def _person_spans(text: str) -> list[EntitySpan]:
    # Natasha ships its News weights in the wheel. Neither construction nor inference
    # downloads a model. Serialize both lazy initialization and inference: one model
    # instance per process, with no assumptions about underlying model thread safety.
    global _ner
    from natasha import Doc, NewsEmbedding, NewsNERTagger, Segmenter

    with _ner_lock:
        if _ner is None:
            _ner = Segmenter(), NewsNERTagger(NewsEmbedding())
        segmenter, tagger = _ner
        doc = Doc(text)
        doc.segment(segmenter)
        if not doc.tokens:
            return []
        doc.tag_ner(tagger)
        return [
            EntitySpan(s.start, s.stop, PiiType.PERSON, text[s.start : s.stop], "natasha@1.6.0")
            for s in doc.spans
            if s.type == "PER"
        ]


def _resolve(text: str, candidates: list[EntitySpan]) -> list[EntitySpan]:
    # Structured types beat PERSON; within either group prefer the longer span,
    # then the earlier start, type order and source name. Independent of input order.
    ranked = sorted(
        candidates,
        key=lambda s: (
            s.type == PiiType.PERSON,
            -(s.end - s.start),
            s.start,
            _PRIORITY[s.type],
            s.detector,
        ),
    )
    accepted: list[EntitySpan] = []
    for span in ranked:
        check_spans(text, [span])
        if not any(span.start < other.end and other.start < span.end for other in accepted):
            accepted.append(span)
    accepted.sort(key=lambda s: s.start)
    check_spans(text, accepted)
    return accepted


def detect(text: str, ctx: Context) -> list[EntitySpan]:
    """Return validated spans or raise DetectError with no input/model error text.

    Context is retained for the shared API; no project data is cached here. A model
    failure aborts the entire call, even when regex candidates were found already.
    Successful empty results mean no detections, not a proof that text is PII-free.
    """
    if not isinstance(text, str) or not isinstance(ctx, Context):
        raise DetectError("invalid detector input")
    try:
        return _resolve(text, _regex_spans(text) + _person_spans(text))
    except Exception:
        # Third-party exceptions can contain input. Do not expose them to callers.
        raise DetectError("local PII detection failed") from None
