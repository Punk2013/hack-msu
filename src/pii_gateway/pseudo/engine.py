"""Core pseudonymization engine: mask and restore (role 3, issue #11).

Provides:
- Deterministic marker assignment ([PERSON_1], [EMAIL_1], etc.)
- Replay stability across turns (same person/structured field gets same marker)
- Idempotent Vault persistence outside workspace
- Realistic Russian inflection and declension with collision prevention
- Strict unmasking: unknown or corrupted markers return PARTIAL
"""

from __future__ import annotations

import re
import uuid

from pii_gateway.core.types import (
    Context,
    EntitySpan,
    MaskResult,
    PiiType,
    Replacement,
    RestoreResult,
    RestoreStatus,
    Unresolved,
    check_spans,
)
from pii_gateway.pseudo.morphology import (
    generate_realistic_pair,
    lemmatize_surname,
    parse_and_decline_full_name,
)
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

VALID_MARKER_PATTERN = re.compile(rf"\[({TYPES_PATTERN})_([0-9]+)\]")

# Matches malformed or corrupted markers:
# 1. Non-numeric or whitespace inside tag: [PERSON_x], [PERSON_1 ]
# 2. Incomplete tag without index: [PERSON]
# 3. Unclosed bracket before whitespace or end of string: [PERSON_1
CORRUPTED_MARKER_PATTERN = re.compile(
    rf"\[(?:{TYPES_PATTERN})"
    rf"(?:_[^\]\n0-9][^\]\n]*\]|_[0-9]+[ ]+\]|\]|_[0-9]+\b(?!\s*\])|_[a-zA-Z]+\b(?!\s*\]))"
)


def mask(text: str, spans: list[EntitySpan], ctx: Context) -> MaskResult:
    """Replace entity spans in text with markers or realistic pseudonyms.

    Raises:
        DetectError: if spans are invalid or overlapping.
        MaskError: on transformation failure.
    """
    check_spans(text, spans)
    if not spans:
        return MaskResult(text=text, replacements=[])

    vault = Vault(ctx.project_id)

    # 1. Prevent collision with literal markers already existing in the input text:
    # If the text originally contains e.g. "[PERSON_1]", reserve at least 1 for PERSON
    # so that newly assigned markers will start from [PERSON_2].
    for m in VALID_MARKER_PATTERN.finditer(text):
        m_type, m_id = m.group(1), m.group(2)
        try:
            val = int(m_id)
            vault.reserve_marker_index_at_least(m_type, val)
        except ValueError:
            pass

    replacements: list[Replacement] = []
    span_replacements: list[tuple[EntitySpan, str, Replacement]] = []

    # Map raw_text -> entity_id within this batch to ensure replay stability
    seen_in_batch: dict[str, str] = {}

    # Gather full person entities in this batch for partial_single resolution
    full_persons_in_batch: dict[str, str] = {}  # surname_lemma -> entity_id
    surname_counts: dict[str, int] = {}
    for span in spans:
        if span.type == PiiType.PERSON and " " in span.raw_text.strip():
            forms, gender, _ = parse_and_decline_full_name(span.raw_text)
            parts = span.raw_text.strip().split()
            sn = parts[1] if len(parts) == 2 else parts[-1]
            sn_lemma = lemmatize_surname(sn, gender)
            surname_counts[sn_lemma] = surname_counts.get(sn_lemma, 0) + 1

    used_pseudos = vault.get_all_used_pseudonyms()

    for span in spans:
        entity_id: str
        linked_by = "new"
        form_name: str | None = None

        if span.raw_text in seen_in_batch:
            # Reusing exact mention in this batch
            entity_id = seen_in_batch[span.raw_text]
            linked_by = "exact"
        elif span.type == PiiType.PERSON:
            is_full_name = " " in span.raw_text.strip()
            if is_full_name:
                forms, gender, matched_case = parse_and_decline_full_name(span.raw_text)
                form_name = matched_case
                nomn_form = forms.get("nomn", span.raw_text)

                if nomn_form in seen_in_batch:
                    entity_id = seen_in_batch[nomn_form]
                    linked_by = "exact"
                else:
                    # Search vault by exact text or canonical nominative form
                    existing = vault.find_entity_by_original_text(
                        span.raw_text, span.type
                    ) or vault.find_entity_by_original_text(nomn_form, span.type)
                    if existing:
                        entity_id = existing.entity_id
                        linked_by = "exact"
                    else:
                        entity_id = f"ent_{uuid.uuid4().hex[:12]}"
                        vault.add_entity(entity_id, span.type, is_ambiguous=False)
                        linked_by = "new"

                    seen_in_batch[nomn_form] = entity_id
                    # Record canonical surface
                    vault.add_surface(
                        surface_id=f"surf_canon_{entity_id}",
                        entity_id=entity_id,
                        side="canonical",
                        text=nomn_form,
                        form="nomn",
                    )

                seen_in_batch[span.raw_text] = entity_id
                parts = span.raw_text.strip().split()
                sn = parts[1] if len(parts) == 2 else parts[-1]
                sn_lemma = lemmatize_surname(sn, gender)
                full_persons_in_batch[sn_lemma] = entity_id
            else:
                # Partial mention (single word surname like "Иванов")
                sn_lemma = lemmatize_surname(span.raw_text, "masc")
                if surname_counts.get(sn_lemma) == 1 and sn_lemma in full_persons_in_batch:
                    entity_id = full_persons_in_batch[sn_lemma]
                    linked_by = "partial_single"
                else:
                    existing = vault.find_entity_by_original_text(span.raw_text, span.type)
                    if existing:
                        entity_id = existing.entity_id
                        linked_by = "exact"
                    else:
                        entity_id = f"ent_{uuid.uuid4().hex[:12]}"
                        vault.add_entity(entity_id, span.type, is_ambiguous=True)
                        linked_by = "new"
                seen_in_batch[span.raw_text] = entity_id
        else:
            # Structured types (EMAIL, PHONE, PASSPORT_RU)
            existing = vault.find_entity_by_original_text(span.raw_text, span.type)
            if existing:
                entity_id = existing.entity_id
                linked_by = "exact"
            else:
                entity_id = f"ent_{uuid.uuid4().hex[:12]}"
                vault.add_entity(entity_id, span.type, is_ambiguous=False)
                linked_by = "new"
            seen_in_batch[span.raw_text] = entity_id

        # Record original surface
        vault.add_surface(
            surface_id=f"surf_{uuid.uuid4().hex[:12]}",
            entity_id=entity_id,
            side="original",
            text=span.raw_text,
            form=form_name,
        )

        # Record mention
        vault.add_mention(
            mention_id=f"ment_{uuid.uuid4().hex[:12]}",
            entity_id=entity_id,
            source_id="doc",
            start_idx=span.start,
            end_idx=span.end,
        )

        replacement_str: str

        if ctx.mode == "realistic" and span.type == PiiType.PERSON and " " in span.raw_text.strip():
            pair = generate_realistic_pair(span.raw_text, entity_id, used_pseudos=used_pseudos)
            if pair is not None and pair.is_bijective:
                case_key = form_name or "nomn"
                replacement_str = pair.pseudo_forms.get(case_key, pair.pseudo_forms["nomn"])

                # Store all case surfaces for realistic unmasking
                for c_name, p_text in pair.pseudo_forms.items():
                    o_text = pair.original_forms.get(c_name, span.raw_text)
                    vault.add_surface(
                        surface_id=f"surf_{uuid.uuid4().hex[:12]}",
                        entity_id=entity_id,
                        side="original",
                        text=o_text,
                        form=c_name,
                    )
                    vault.add_surface(
                        surface_id=f"surf_{uuid.uuid4().hex[:12]}",
                        entity_id=entity_id,
                        side="pseudo",
                        text=p_text,
                        form=c_name,
                    )
                    used_pseudos.add(p_text)
            else:
                # Fallback to marker mode if pair is non-bijective or pool collided
                existing_marker = vault.get_marker_for_entity(entity_id)
                if existing_marker:
                    replacement_str = existing_marker
                else:
                    marker_idx = vault.get_next_marker_index(span.type)
                    replacement_str = f"[{span.type}_{marker_idx}]"
                    vault.add_marker(replacement_str, entity_id, span.type)
        else:
            # Marker mode
            existing_marker = vault.get_marker_for_entity(entity_id)
            if existing_marker:
                replacement_str = existing_marker
            else:
                marker_idx = vault.get_next_marker_index(span.type)
                replacement_str = f"[{span.type}_{marker_idx}]"
                vault.add_marker(replacement_str, entity_id, span.type)

        rep = Replacement(
            start=span.start,
            end=span.end,
            entity_id=entity_id,
            linked_by=linked_by,  # type: ignore[arg-type]
            form=form_name,
        )
        span_replacements.append((span, replacement_str, rep))

    # Apply replacements from right to left to preserve offsets
    span_replacements.sort(key=lambda item: item[0].start, reverse=True)
    out_chars = list(text)

    for span, rep_str, rep in span_replacements:
        out_chars[span.start : span.end] = list(rep_str)
        replacements.append(rep)

    replacements.reverse()
    return MaskResult(text="".join(out_chars), replacements=replacements)


def restore(text: str, ctx: Context) -> RestoreResult:
    """Restore known markers and realistic pseudonyms back to original text.

    Unknown or corrupted markers are never guessed and are returned as Unresolved.
    Status is PARTIAL if any marker or form could not be resolved.
    """
    vault = Vault(ctx.project_id)
    marker_map = vault.get_all_marker_mappings()
    pseudo_map = vault.get_all_pseudo_mappings()

    unresolved: list[Unresolved] = []

    # Phase 1: Realistic pseudonym replacement (longest match first)
    current_text = text
    if pseudo_map:
        for pseudo_text, orig_text, _ in pseudo_map:
            if pseudo_text in current_text:
                current_text = current_text.replace(pseudo_text, orig_text)

    # Phase 2: Check for malformed or corrupted markers
    for m in CORRUPTED_MARKER_PATTERN.finditer(current_text):
        unresolved.append(
            Unresolved(start=m.start(), end=m.end(), reason="unknown_marker")
        )

    # Phase 3: Valid marker replacement
    valid_matches = list(VALID_MARKER_PATTERN.finditer(current_text))
    if not valid_matches and not unresolved:
        return RestoreResult(text=current_text, status=RestoreStatus.COMPLETE, unresolved=[])

    chars = list(current_text)
    # Process from end to beginning to preserve replacement offsets
    matches_sorted = sorted(valid_matches, key=lambda m: m.start(), reverse=True)

    for m in matches_sorted:
        m_str = m.group(0)
        start, end = m.start(), m.end()

        if m_str in marker_map:
            orig = marker_map[m_str]
            chars[start:end] = list(orig)
        else:
            unresolved.append(
                Unresolved(start=start, end=end, reason="unknown_marker")
            )

    unresolved.sort(key=lambda u: u.start)
    result_text = "".join(chars)
    status = RestoreStatus.PARTIAL if unresolved else RestoreStatus.COMPLETE
    return RestoreResult(text=result_text, status=status, unresolved=unresolved)
