from __future__ import annotations

from pathlib import Path

import pytest

from pii_gateway.core.types import (
    Context,
    EntitySpan,
    PiiType,
    RestoreStatus,
)
from pii_gateway.pseudo import mask, restore
from pii_gateway.pseudo.morphology import (
    decline_full_name,
    decline_surname,
    generate_realistic_pair,
)


@pytest.fixture(autouse=True)
def vault_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PII_VAULT_DIR", str(tmp_path / "vaults"))


def test_marker_mask_and_restore_roundtrip():
    ctx = Context(project_id="test_roundtrip", mode="marker")
    text = (
        "Клиент Иван Иванов, email ivan@example.test, "
        "телефон +7 000 000-00-01, паспорт 00 00 000001."
    )
    spans = [
        EntitySpan(7, 18, PiiType.PERSON, "Иван Иванов", detector="test"),
        EntitySpan(26, 43, PiiType.EMAIL, "ivan@example.test", detector="test"),
        EntitySpan(53, 69, PiiType.PHONE, "+7 000 000-00-01", detector="test"),
        EntitySpan(79, 91, PiiType.PASSPORT_RU, "00 00 000001", detector="test"),
    ]

    masked = mask(text, spans, ctx)
    assert "[PERSON_1]" in masked.text
    assert "[EMAIL_1]" in masked.text
    assert "[PHONE_1]" in masked.text
    assert "[PASSPORT_RU_1]" in masked.text
    assert "Иван Иванов" not in masked.text

    restored = restore(masked.text, ctx)
    assert restored.status == RestoreStatus.COMPLETE
    assert restored.unresolved == []
    assert restored.text == text


def test_unicode_and_emoji_offsets():
    ctx = Context(project_id="test_unicode", mode="marker")
    text = "🧪 Отправить Анне Петровой на anna@example.test."
    spans = [
        EntitySpan(12, 25, PiiType.PERSON, "Анне Петровой", detector="test"),
        EntitySpan(29, 46, PiiType.EMAIL, "anna@example.test", detector="test"),
    ]

    masked = mask(text, spans, ctx)
    assert "🧪 Отправить [PERSON_1] на [EMAIL_1]." == masked.text

    restored = restore(masked.text, ctx)
    assert restored.text == text
    assert restored.status == RestoreStatus.COMPLETE


def test_unknown_marker_returns_partial_status():
    ctx = Context(project_id="test_unknown", mode="marker")
    text = "Привет [PERSON_999] и [EMAIL_1]!"
    restored = restore(text, ctx)

    assert restored.status == RestoreStatus.PARTIAL
    assert len(restored.unresolved) == 2
    assert restored.unresolved[0].reason == "unknown_marker"
    assert restored.unresolved[0].start == 7
    assert restored.unresolved[0].end == 19
    assert restored.text == text


def test_namesakes_in_same_document():
    ctx = Context(project_id="test_namesakes", mode="marker")
    text = "Запись A: Иван Иванов, a@example.test. Запись B: Иван Иванов, b@example.test."
    spans = [
        EntitySpan(10, 21, PiiType.PERSON, "Иван Иванов", detector="test"),
        EntitySpan(23, 37, PiiType.EMAIL, "a@example.test", detector="test"),
        EntitySpan(49, 60, PiiType.PERSON, "Иван Иванов", detector="test"),
        EntitySpan(62, 76, PiiType.EMAIL, "b@example.test", detector="test"),
    ]

    masked = mask(text, spans, ctx)
    # Decision (ARCHITECTURE.md §4): the same full name in a project is one entity.
    # Namesakes merge; this is a documented MVP limitation.
    assert masked.text.count("[PERSON_1]") == 2
    assert "[PERSON_2]" not in masked.text

    restored = restore(masked.text, ctx)
    assert restored.text == text
    assert restored.status == RestoreStatus.COMPLETE


def test_repeated_mention_with_case_inflection():
    ctx = Context(project_id="test_repeat", mode="marker")
    text = "Иван Иванов подписал. Ивану Иванову отправили копию."
    spans = [
        EntitySpan(0, 11, PiiType.PERSON, "Иван Иванов", detector="test"),
        EntitySpan(22, 35, PiiType.PERSON, "Ивану Иванову", detector="test"),
    ]

    masked = mask(text, spans, ctx)
    assert masked.text == "[PERSON_1] подписал. [PERSON_1] отправили копию."

    restored = restore(masked.text, ctx)
    # In marker mode, [PERSON_1] restores to the canonical surface representation,
    # whereas realistic mode preserves grammatical case inflection.
    assert restored.text == "Иван Иванов подписал. Иван Иванов отправили копию."
    assert restored.status == RestoreStatus.COMPLETE


def test_realistic_mode_morphology_and_cases():
    ctx = Context(project_id="test_realistic", mode="realistic")
    text = "Договор подписан Иваном Ивановым для Ивана Иванова."
    spans = [
        EntitySpan(17, 32, PiiType.PERSON, "Иваном Ивановым", detector="test"),
        EntitySpan(37, 50, PiiType.PERSON, "Ивана Иванова", detector="test"),
    ]

    masked = mask(text, spans, ctx)
    assert "Иваном Ивановым" not in masked.text
    assert "Ивана Иванова" not in masked.text
    assert "[" not in masked.text

    restored = restore(masked.text, ctx)
    assert restored.text == text
    assert restored.status == RestoreStatus.COMPLETE


def test_counterexample_sasha_shevchenko_bijection_fallback():
    pair = generate_realistic_pair(
        "Иван Иванов", "ent_123", target_pseudo_override=("Саша", "Шевченко")
    )
    assert pair is not None
    assert pair.is_bijective is False

    orig_decline = decline_full_name("Иван Иванов", "masc")
    assert orig_decline["nomn"] == "Иван Иванов"
    assert orig_decline["ablt"] == "Иваном Ивановым"

    shevchenko_decline = decline_surname("Шевченко", "masc")
    assert shevchenko_decline["nomn"] == "Шевченко"
    assert shevchenko_decline["ablt"] == "Шевченко"
