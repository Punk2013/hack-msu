"""Core pseudonymization engine: mask and restore (role 3, issue #11).

Provides:
- Deterministic marker assignment ([PERSON_1], [EMAIL_1], etc.)
- Realistic Russian inflection and declension with bijection validation
- Vault persistence outside the workspace with project scoping
- Strict unmasking: unknown/altered markers are never guessed (returns PARTIAL)
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
    parse_and_decline_full_name,
)
from pii_gateway.vault.storage import Vault

MARKER_PATTERN = re.compile(r"\[([A-Z_]+)_([0-9]+)\]")


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
    replacements: list[Replacement] = []
    span_replacements: list[tuple[EntitySpan, str, Replacement]] = []

    # Track entities created or referenced in this batch
    seen_in_batch: dict[str, str] = {}

    for span in spans:
        entity_id: str
        linked_by = "new"
        form_name: str | None = None
        is_inflected_repeat = False

        if span.type == PiiType.PERSON and " " in span.raw_text.strip():
            forms, _, matched_case = parse_and_decline_full_name(span.raw_text)
            form_name = matched_case
            nomn_form = forms.get("nomn", span.raw_text)

            # Inflected repeat of the same person in this document
            if nomn_form in seen_in_batch and span.raw_text != nomn_form:
                entity_id = seen_in_batch[nomn_form]
                linked_by = "exact"
                is_inflected_repeat = True

        if not is_inflected_repeat:
            # Identical exact names in separate records of same text are treated as namesakes
            if span.raw_text in seen_in_batch:
                entity_id = f"ent_{uuid.uuid4().hex[:12]}"
                vault.add_entity(entity_id, span.type, is_ambiguous=True)
                linked_by = "new"
            else:
                existing = vault.find_entity_by_original_text(span.raw_text, span.type)
                if existing:
                    entity_id = existing.entity_id
                    linked_by = "exact"
                else:
                    entity_id = f"ent_{uuid.uuid4().hex[:12]}"
                    vault.add_entity(entity_id, span.type, is_ambiguous=False)
                    linked_by = "new"

                seen_in_batch[span.raw_text] = entity_id
                if span.type == PiiType.PERSON and " " in span.raw_text.strip():
                    forms, _, _ = parse_and_decline_full_name(span.raw_text)
                    seen_in_batch[forms.get("nomn", span.raw_text)] = entity_id

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
            pair = generate_realistic_pair(span.raw_text, entity_id)
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
            else:
                # Non-bijective counterexample fallback to marker mode (Issue #11 requirement)
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

    # Phase 2: Marker replacement with exact boundary detection
    matches = list(MARKER_PATTERN.finditer(current_text))
    if not matches:
        return RestoreResult(text=current_text, status=RestoreStatus.COMPLETE, unresolved=[])

    chars = list(current_text)
    matches_sorted = sorted(matches, key=lambda m: m.start(), reverse=True)

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

    unresolved.reverse()
    result_text = "".join(chars)
    status = RestoreStatus.PARTIAL if unresolved else RestoreStatus.COMPLETE
    return RestoreResult(text=result_text, status=status, unresolved=unresolved)
