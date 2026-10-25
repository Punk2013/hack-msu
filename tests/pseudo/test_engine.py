"""Engine behaviour the gateway relies on: stable, project-wide, realistic by default."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pii_gateway.core.types import Context, EntitySpan, PiiType, RestoreStatus
from pii_gateway.pseudo import mask, restore
from pii_gateway.vault.storage import get_vault_path


@pytest.fixture(autouse=True)
def vault_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PII_VAULT_DIR", str(tmp_path / "vaults"))


def spans_for(text: str, *items: tuple[str, PiiType]) -> list[EntitySpan]:
    out = []
    for raw, kind in items:
        start = text.index(raw)
        out.append(EntitySpan(start, start + len(raw), kind, raw, detector="test"))
    return sorted(out, key=lambda s: s.start)


CARD = ("ФИО: Иван Петрович Иванов. Email: ivan.ivanov@example.test. "
        "Телефон: +7 900 000-00-01. Паспорт: 00 01 000001. Ивану Петровичу напомнить.")
CARD_SPANS = [("Иван Петрович Иванов", PiiType.PERSON), ("ivan.ivanov@example.test", PiiType.EMAIL),
              ("+7 900 000-00-01", PiiType.PHONE), ("00 01 000001", PiiType.PASSPORT_RU),
              ("Ивану Петровичу", PiiType.PERSON)]


def test_realistic_hides_every_value_without_markers():
    ctx = Context("card")
    masked = mask(CARD, spans_for(CARD, *CARD_SPANS), ctx)
    for raw, _ in CARD_SPANS:
        assert raw not in masked.text
    assert "[" not in masked.text
    assert restore(masked.text, ctx).text == CARD


def test_mask_is_stable_across_turns_and_vault_does_not_grow():
    ctx = Context("stable")
    text = "Иван Петрович Иванов подписал. Иван Петрович Иванов получил копию."
    spans = spans_for(text, ("Иван Петрович Иванов", PiiType.PERSON))
    spans.append(EntitySpan(31, 51, PiiType.PERSON, "Иван Петрович Иванов", detector="test"))
    first = mask(text, spans, ctx).text
    db = sqlite3.connect(get_vault_path("stable"))
    size = db.execute("select count(*) from pairs").fetchone()[0]
    for _ in range(3):
        assert mask(text, spans, ctx).text == first
    assert db.execute("select count(*) from pairs").fetchone()[0] == size
    fake = first.split(" подписал")[0]
    assert first.count(fake) == 2


def test_inflected_form_in_another_field_links_to_same_person():
    ctx = Context("fields")
    a = "Клиент: Иван Петрович Иванов."
    b = "Отправить Ивану Петровичу Иванову уведомление."
    ma = mask(a, spans_for(a, ("Иван Петрович Иванов", PiiType.PERSON)), ctx)
    mb = mask(b, spans_for(b, ("Ивану Петровичу Иванову", PiiType.PERSON)), ctx)
    assert ma.replacements[0].entity_id == mb.replacements[0].entity_id
    assert mb.replacements[0].linked_by == "exact"
    assert restore(mb.text, ctx).text == b


def test_model_written_case_is_restored_with_matching_original_case():
    ctx = Context("cases")
    a = "Клиент: Анна Сергеевна Смирнова."
    masked = mask(a, spans_for(a, ("Анна Сергеевна Смирнова", PiiType.PERSON)), ctx).text
    fake = masked.removeprefix("Клиент: ").removesuffix(".")
    first, patr, last = fake.split()
    # the model writes the dative form itself, the gateway never saw it
    from pii_gateway.pseudo.engine import _analyze_name, _inflect
    name = _analyze_name(fake)
    datv = " ".join(_inflect(t.parse, "datv", "femn", w)
                    for t, w in zip(name.tokens, (first, patr, last), strict=True))
    restored = restore(f"Уважаемой {datv} отправлено.", ctx)
    assert restored.text == "Уважаемой Анне Сергеевне Смирновой отправлено."
    assert restored.status == RestoreStatus.COMPLETE


def test_partial_name_seen_first_then_full_name_reuses_entity():
    ctx = Context("partial")
    a = "Ивану Петровичу напомнить."
    b = "ФИО: Иван Петрович Иванов."
    ma = mask(a, spans_for(a, ("Ивану Петровичу", PiiType.PERSON)), ctx)
    mb = mask(b, spans_for(b, ("Иван Петрович Иванов", PiiType.PERSON)), ctx)
    assert ma.replacements[0].entity_id == mb.replacements[0].entity_id
    assert restore(mb.text, ctx).text == b


def test_structured_value_in_other_format_is_same_entity():
    ctx = Context("phones")
    a, b = "тел. +7 900 000-00-01", "тел. 8 900 000 00 01"
    ma = mask(a, spans_for(a, ("+7 900 000-00-01", PiiType.PHONE)), ctx)
    mb = mask(b, spans_for(b, ("8 900 000 00 01", PiiType.PHONE)), ctx)
    assert ma.replacements[0].entity_id == mb.replacements[0].entity_id
    assert restore(ma.text + " / " + mb.text, ctx).text == a + " / " + b


def test_projects_are_isolated():
    text = "Клиент: Иван Петрович Иванов."
    spans = spans_for(text, ("Иван Петрович Иванов", PiiType.PERSON))
    masked = mask(text, spans, Context("p1")).text
    assert restore(masked, Context("p2")).text == masked
