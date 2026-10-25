"""Four-type MVP detector; offsets always refer to the unmodified input."""

from __future__ import annotations

import re
from bisect import bisect_left
from threading import Lock

from pii_gateway.core.types import Context, DetectError, EntitySpan, PiiType, check_spans

# Match from the start of a local-part run only. Allowing a retry at each "!"
# would rescan its suffix and make punctuation-only input quadratic.
_EMAIL_LOCAL = r"\w.!#$%&'*+/=?^`{|}~-"
_EMAIL = re.compile(
    rf"(?<![{_EMAIL_LOCAL}])[{_EMAIL_LOCAL}]+@"
    r"[\w](?:[\w-]*[\w])?(?:\.[\w](?:[\w-]*[\w])?)+(?![\w@-])"
)
_SEP = r"[\s\-–—‑]*"
_NATIONAL_PHONE = rf"(?:\([0-9]{{3}}\)|[0-9]{{3}}){_SEP}[0-9]{{3}}{_SEP}[0-9]{{2}}{_SEP}[0-9]{{2}}"
_PHONE = re.compile(rf"(?<![\w+])(?:\+7|8){_SEP}{_NATIONAL_PHONE}(?!\w)")
_LABELED_PHONE = re.compile(
    rf"\b(?:телефон|тел\.?|phone)\s*[:№]?\s*(?P<value>{_NATIONAL_PHONE})(?!\w)",
    re.IGNORECASE,
)
# A separated 4+6 value is conservatively sensitive even without a label.
_PASSPORT = re.compile(r"(?<!\w)[0-9]{2}\s*[0-9]{2}(?:\s+|\s*№\s*)[0-9]{6}(?!\w)")
_LABELED_PASSPORT = re.compile(
    r"\b(?:паспорт(?:\s+РФ)?|passport)\s*[:№]?\s*(?:серия\s*)?"
    r"(?P<value>[0-9]{2}\s*[0-9]{2}\s*(?:(?:№|номер)\s*)?[0-9]{6})(?!\w)",
    re.IGNORECASE,
)
# Explicit form fields supplement the news-domain NER. Horizontal separators and
# a field terminator prevent consuming the next line or truncating a longer name.
_NAME_WORD = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)*"
_HSPACE = r"[ \t\u00a0]"
_LABELED_PERSON_SOURCE = "regex:person_ru_labeled@1"
_LABELED_PERSON = re.compile(
    rf"\b(?i:фио|клиент|менеджер){_HSPACE}*:{_HSPACE}*"
    rf"(?P<value>{_NAME_WORD}(?:{_HSPACE}+{_NAME_WORD}){{1,2}})"
    rf"(?={_HSPACE}*(?:$|[\r\n,;.!?]))"
)
_RULES = (
    (_EMAIL, PiiType.EMAIL, "regex:email@1", 0),
    (_PHONE, PiiType.PHONE, "regex:phone_ru@1", 0),
    (_LABELED_PHONE, PiiType.PHONE, "regex:phone_ru_labeled@1", "value"),
    (_PASSPORT, PiiType.PASSPORT_RU, "regex:passport_ru@1", 0),
    (_LABELED_PASSPORT, PiiType.PASSPORT_RU, "regex:passport_ru_labeled@1", "value"),
    (_LABELED_PERSON, PiiType.PERSON, _LABELED_PERSON_SOURCE, "value"),
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
            if s.type == "PER" and not _never_a_name(text[s.start : s.stop])
        ]


def _never_a_name(raw: str) -> bool:
    """A single dictionary word that is only a verb form cannot be a person's name.

    NER marks a capitalized imperative at a sentence start ("Упоминай адресата") as PER.
    """
    if len(raw.split()) != 1:
        return False
    from pymorphy3 import MorphAnalyzer

    global _morph
    if _morph is None:
        _morph = MorphAnalyzer()
    parses = _morph.parse(raw.strip())
    # only dictionary words whose every reading is a verb form; unknown words such as
    # rare or foreign surnames ("Нгуен") are kept, missing a name is worse than a marker
    verb_forms = {"VERB", "INFN", "GRND", "PRTS", "PRTF"}
    return all(p.is_known and p.tag.POS in verb_forms for p in parses)


_morph = None


def _resolve(text: str, candidates: list[EntitySpan]) -> list[EntitySpan]:
    # Structured types beat PERSON; explicit person fields beat overlapping NER.
    # Within each tier prefer length, start, type and source, independent of order.
    ranked = sorted(
        candidates,
        key=lambda s: (
            s.type == PiiType.PERSON,
            s.type == PiiType.PERSON and s.detector != _LABELED_PERSON_SOURCE,
            -(s.end - s.start),
            s.start,
            _PRIORITY[s.type],
            s.detector,
        ),
    )
    accepted: list[EntitySpan] = []
    starts: list[int] = []
    for span in ranked:
        check_spans(text, [span])
        position = bisect_left(starts, span.start)
        if position and accepted[position - 1].end > span.start:
            continue
        if position < len(accepted) and accepted[position].start < span.end:
            continue
        starts.insert(position, span.start)
        accepted.insert(position, span)
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
