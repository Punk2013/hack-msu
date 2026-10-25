"""Pseudonymization engine: mask and restore (role 3, issue #11).

- One entity per normalized value in a project: the same full name always gets
  the same replacement, in any case and in any text field, on every turn.
- realistic (default): inflected fake names via pymorphy3 and format-preserving
  fakes for structured values; marker ([PERSON_1]) only as a fallback.
- restore() replaces only replacements recorded in the vault and never guesses.
- Corrupted/malformed markers ([PERSON_1, [PERSON_x], [PERSON_1 ]) return PARTIAL.
- Reserved marker syntax in raw input is refused rather than guessed or reinterpreted.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations

from pii_gateway.core.types import (
    Context,
    EntitySpan,
    MaskError,
    MaskResult,
    PiiType,
    Replacement,
    RestoreResult,
    RestoreStatus,
    Unresolved,
    check_spans,
)
from pii_gateway.pseudo.fakes import fake_value
from pii_gateway.vault.storage import Vault

KNOWN_PII_TYPES = (
    "PERSON",
    "EMAIL",
    "PHONE",
    "PASSPORT_RU",
    "ADDRESS",
    "SNILS",
    "INN",
    "CARD",
    "BIRTH_DATE",
)
TYPES_PATTERN = "|".join(KNOWN_PII_TYPES)

MARKER_PATTERN = re.compile(rf"\[({TYPES_PATTERN})_([0-9]+)\]")
RESERVED_MARKER_PREFIX = re.compile(rf"\[(?:{TYPES_PATTERN})(?=[_\]\s]|$)")

# Matches malformed or corrupted markers:
# 1. Non-numeric or whitespace inside tag: [PERSON_x], [PERSON_1 ]
# 2. Incomplete tag without index: [PERSON]
# 3. Unclosed bracket before whitespace or end of string: [PERSON_1
CORRUPTED_MARKER_PATTERN = re.compile(
    rf"\[(?:{TYPES_PATTERN})"
    rf"(?:_[^\]\n0-9][^\]\n]*\]|_[0-9]+[ ]+\]|\]|_[0-9]+\b(?!\s*\])|_[a-zA-Z]+\b(?!\s*\]))"
)

CASES = ("nomn", "gent", "datv", "accs", "ablt", "loct")
ROLES = ("Name", "Patr", "Surn")
MAX_ATTEMPTS = 8

# Regular, declinable names only: their case forms never collapse where an
# original's forms differ (see the "Саша Шевченко" counterexample).
POOLS = {
    ("Name", "masc"): (
        "Дмитрий",
        "Алексей",
        "Сергей",
        "Андрей",
        "Михаил",
        "Павел",
        "Николай",
        "Роман",
        "Олег",
        "Виктор",
        "Антон",
        "Кирилл",
    ),
    ("Name", "femn"): (
        "Елена",
        "Ольга",
        "Татьяна",
        "Ирина",
        "Марина",
        "Светлана",
        "Ксения",
        "Вера",
        "Полина",
        "Дарья",
        "Алина",
        "Галина",
    ),
    ("Patr", "masc"): (
        "Андреевич",
        "Викторович",
        "Николаевич",
        "Олегович",
        "Павлович",
        "Романович",
        "Алексеевич",
        "Дмитриевич",
    ),
    ("Patr", "femn"): (
        "Андреевна",
        "Викторовна",
        "Николаевна",
        "Олеговна",
        "Павловна",
        "Романовна",
        "Алексеевна",
        "Дмитриевна",
    ),
    ("Surn", "masc"): (
        "Соколов",
        "Морозов",
        "Лебедев",
        "Новиков",
        "Орлов",
        "Зайцев",
        "Фролов",
        "Голубев",
        "Виноградов",
        "Богданов",
        "Комаров",
        "Белов",
    ),
}
POOLS[("Surn", "femn")] = tuple(s + "а" for s in POOLS[("Surn", "masc")])


@lru_cache(maxsize=1)
def _morph():
    import pymorphy3

    return pymorphy3.MorphAnalyzer()


@dataclass(frozen=True)
class _Token:
    role: str  # Name | Patr | Surn
    normal: str
    parse: object


@dataclass(frozen=True)
class _Name:
    tokens: tuple[_Token, ...]
    gender: str  # masc | femn
    case: str  # nomn | gent | ...

    def key(self, roles: tuple[str, ...]) -> str:
        parts = [f"{t.role}:{t.normal}" for t in self.tokens if t.role in roles]
        return f"PERSON|{self.gender}|{'|'.join(parts)}"


def _analyze_name(raw: str) -> _Name | None:
    """Extract Name/Patr/Surn roles, gender, and case via pymorphy3, or None."""
    words = raw.split()
    if not (1 <= len(words) <= 3):
        return None
    analyzer = _morph()
    word_parses = [analyzer.parse(w) for w in words]

    for gender in ("masc", "femn"):
        chosen = []
        cases = set(CASES)
        for parses in word_parses:
            fit = [
                p
                for p in parses
                if {gender, "ms-f"} & set(p.tag.grammemes) and p.tag.case in CASES
            ]
            if not fit:
                break
            cases &= {p.tag.case for p in fit}
            chosen.append(fit)
        else:
            if not cases:
                continue
            case = next(c for c in CASES if c in cases)
            tokens = []
            for fit in chosen:
                p = next(p for p in fit if p.tag.case == case)
                role = next(r for r in ROLES if r in p.tag.grammemes)
                tokens.append(_Token(role, p.normal_form, p))
            roles = [t.role for t in tokens]
            if len(set(roles)) != len(roles):
                return None
            return _Name(tuple(tokens), gender, case)
    return None


def _inflect(parse, case: str, gender: str, like: str) -> str | None:
    form = parse.inflect({case, gender}) or parse.inflect({case})
    if form is None:
        return None
    word = form.word
    return word[:1].upper() + word[1:] if like[:1].isupper() else word


def _pseudo_parse(role: str, gender: str, entity_id: str, attempt: int):
    pool = POOLS[(role, gender)]
    digest = hashlib.sha256(f"{entity_id}:{role}:{attempt}".encode()).digest()
    word = pool[int.from_bytes(digest[:4], "big") % len(pool)]
    parses = [p for p in _morph().parse(word) if role in p.tag.grammemes]
    return parses[0] if parses else None


def _person_pairs(
    name: _Name, raw: str, entity_id: str, attempt: int, vault: Vault
) -> dict[str, str] | None:
    """{fake_form: original_form} for all cases of this mention, or None if unsafe."""
    words = raw.split()
    pairs: dict[str, str] = {}
    for case in CASES:
        orig, fake = [], []
        for token, word in zip(name.tokens, words, strict=True):
            o = _inflect(token.parse, case, name.gender, word)
            pp = _pseudo_parse(token.role, name.gender, entity_id, attempt)
            f = _inflect(pp, case, name.gender, word) if pp is not None else None
            if o is None or f is None:
                return None
            orig.append(o)
            fake.append(f)
        o_text, f_text = " ".join(orig), " ".join(fake)
        if case == name.case:
            o_text = raw  # keep the exact spelling the user wrote
        if pairs.get(f_text, o_text) != o_text:
            return None  # one fake form would restore to two originals
        known = vault.get_pair(f_text)
        if f_text == o_text or vault.is_known_original(f_text) or known not in (None, o_text):
            return None
        pairs[f_text] = o_text
    return pairs


def _normalize(kind: PiiType, raw: str) -> str:
    if kind is PiiType.EMAIL:
        return raw.strip().lower()
    if kind in (PiiType.PHONE, PiiType.PASSPORT_RU):
        digits = re.sub(r"\D", "", raw)
        if kind is PiiType.PHONE and len(digits) == 11 and digits[0] == "8":
            digits = "7" + digits[1:]
        return digits
    return " ".join(raw.split())


def _resolve_entity(
    vault: Vault, kind: PiiType, keys: list[str], is_partial: bool = False
) -> tuple[str, str]:
    """keys[0] is the full key, the rest are partial ones; returns (entity_id, linked_by)."""
    found, entity_id = vault.get_key(keys[0])
    if found and entity_id is not None:
        return entity_id, "partial_single" if is_partial else "exact"
    # Do not promote a match on a subset of roles to a match on a full name.
    # A full mention registers partial keys for subsequent shorter mentions;
    # an earlier short mention alone is insufficient evidence to merge people.
    if found:  # partial name shared by several people: its own stable entity
        keys = [f"ambiguous|{keys[0]}"]
        a_found, entity_id = vault.get_key(keys[0])
        if a_found and entity_id is not None:
            return entity_id, "new"
    entity_id = f"ent_{uuid.uuid4().hex[:12]}"
    vault.add_entity(entity_id, kind, is_ambiguous=found)
    for key in keys:
        vault.add_key(key, entity_id)
    return entity_id, "new"


def _marker(vault: Vault, kind: PiiType, entity_id: str, raw: str) -> str:
    marker = vault.get_marker_for_surface(entity_id, raw)
    if marker is None:
        marker = f"[{kind}_{vault.get_next_marker_index(kind)}]"
        vault.add_marker(marker, entity_id, kind)
    vault.add_pair(marker, raw, entity_id)
    return marker


def _replace_one(vault: Vault, span: EntitySpan, ctx: Context) -> tuple[str, Replacement]:
    raw, kind = span.raw_text, span.type
    name = _analyze_name(raw) if kind is PiiType.PERSON else None
    if name is not None:
        roles = tuple(t.role for t in name.tokens)
        is_partial = len(roles) == 1
        partial = [
            name.key(sub)
            for n in range(len(roles) - 1, 0, -1)
            for sub in combinations(roles, n)
            if sub not in (("Name",), ("Patr",))
        ]
        keys = [name.key(roles), *partial]
    else:
        is_partial = False
        keys = [f"{kind}|{_normalize(kind, raw)}"]
    entity_id, linked_by = _resolve_entity(vault, kind, keys, is_partial=is_partial)
    rep = Replacement(span.start, span.end, entity_id, linked_by, name.case if name else None)

    if ctx.mode == "realistic":
        for attempt in range(MAX_ATTEMPTS):
            if name is not None:
                pairs = _person_pairs(name, raw, entity_id, attempt, vault)
                if pairs is None:
                    continue
                for fake, orig in pairs.items():
                    vault.add_pair(fake, orig, entity_id)
                return next(f for f, o in pairs.items() if o == raw), rep
            fake = fake_value(kind, raw, entity_id, attempt)
            if fake is None:
                break
            if vault.is_known_original(fake) or vault.get_pair(fake) not in (None, raw):
                continue
            vault.add_pair(fake, raw, entity_id)
            return fake, rep
    return _marker(vault, kind, entity_id, raw), rep


def mask(text: str, spans: list[EntitySpan], ctx: Context) -> MaskResult:
    check_spans(text, spans)
    if RESERVED_MARKER_PREFIX.search(text):
        raise MaskError("input contains reserved marker syntax")
    if not spans:
        return MaskResult(text=text, replacements=[])
    try:
        with Vault(ctx.project_id) as vault:
            planned = [_replace_one(vault, span, ctx) for span in spans]
    except MaskError:
        raise
    except Exception as exc:  # never leak input text through third-party errors
        raise MaskError(f"mask failed: {type(exc).__name__}") from None

    out, replacements, pos, shift = [], [], 0, 0
    for span, (value, rep) in zip(spans, planned, strict=True):
        out.append(text[pos : span.start])
        out.append(value)
        start = span.start + shift
        replacements.append(
            Replacement(start, start + len(value), rep.entity_id, rep.linked_by, rep.form)
        )
        shift += len(value) - (span.end - span.start)
        pos = span.end
    out.append(text[pos:])
    return MaskResult(text="".join(out), replacements=replacements)


def restore(text: str, ctx: Context) -> RestoreResult:
    with Vault(ctx.project_id) as vault:
        pairs = vault.get_pairs()
    if pairs:
        alternation = "|".join(re.escape(r) for r in sorted(pairs, key=len, reverse=True))
        pattern = re.compile(rf"(?<!\w)(?:{alternation})(?!\w)|{MARKER_PATTERN.pattern}")
    else:
        pattern = MARKER_PATTERN
    result: list[str] = []
    unresolved: list[Unresolved] = []

    # Check for corrupted or malformed markers first
    for m in CORRUPTED_MARKER_PATTERN.finditer(text):
        unresolved.append(Unresolved(m.start(), m.end(), "unknown_marker"))

    pos = out_len = 0
    for match in pattern.finditer(text):
        token = match.group(0)
        before = text[pos : match.start()]
        result.append(before)
        out_len += len(before)
        if token in pairs:
            value = pairs[token]
        else:
            value = token
            unresolved.append(Unresolved(out_len, out_len + len(token), "unknown_marker"))
        result.append(value)
        out_len += len(value)
        pos = match.end()
    result.append(text[pos:])

    unresolved.sort(key=lambda u: u.start)
    status = RestoreStatus.PARTIAL if unresolved else RestoreStatus.COMPLETE
    return RestoreResult(text="".join(result), status=status, unresolved=unresolved)
