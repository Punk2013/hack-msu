from __future__ import annotations

import concurrent.futures
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
from pii_gateway.vault import Vault


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


def test_corrupted_markers_return_partial_status():
    ctx = Context(project_id="test_corrupt", mode="marker")
    # Missing closing bracket
    r1 = restore("Текст [PERSON_1 далее", ctx)
    assert r1.status == RestoreStatus.PARTIAL
    assert len(r1.unresolved) == 1
    assert r1.unresolved[0].reason == "unknown_marker"

    # Non-numeric index
    r2 = restore("Текст [PERSON_x] далее", ctx)
    assert r2.status == RestoreStatus.PARTIAL
    assert len(r2.unresolved) == 1
    assert r2.unresolved[0].reason == "unknown_marker"

    # Trailing space inside bracket
    r3 = restore("Текст [PERSON_1 ] далее", ctx)
    assert r3.status == RestoreStatus.PARTIAL
    assert len(r3.unresolved) == 1
    assert r3.unresolved[0].reason == "unknown_marker"

    # Incomplete tag without index
    r4 = restore("Текст [PERSON] далее", ctx)
    assert r4.status == RestoreStatus.PARTIAL
    assert len(r4.unresolved) == 1
    assert r4.unresolved[0].reason == "unknown_marker"


def test_identical_full_name_reuses_entity_and_is_idempotent():
    ctx = Context(project_id="test_same_name", mode="marker")
    text = "Запись A: Иван Иванов, a@example.test. Запись B: Иван Иванов, b@example.test."
    spans = [
        EntitySpan(10, 21, PiiType.PERSON, "Иван Иванов", detector="test"),
        EntitySpan(23, 37, PiiType.EMAIL, "a@example.test", detector="test"),
        EntitySpan(49, 60, PiiType.PERSON, "Иван Иванов", detector="test"),
        EntitySpan(62, 76, PiiType.EMAIL, "b@example.test", detector="test"),
    ]

    masked1 = mask(text, spans, ctx)
    assert masked1.text == "Запись A: [PERSON_1], [EMAIL_1]. Запись B: [PERSON_1], [EMAIL_2]."
    assert masked1.text.count("[PERSON_1]") == 2
    assert "[PERSON_2]" not in masked1.text

    vault = Vault(ctx.project_id)
    counts1 = vault.count_rows()

    # Replay on the next turn with identical history: must be 100% idempotent
    masked2 = mask(text, spans, ctx)
    assert masked2.text == masked1.text

    counts2 = vault.count_rows()
    assert counts1 == counts2, "Vault must not grow when replaying same history"


def test_repeated_mention_across_different_calls_and_inflections():
    ctx = Context(project_id="test_cross_call", mode="marker")

    # Call 1: Nominative form
    t1 = "Менеджер Иван Петрович Иванов."
    s1 = [EntitySpan(9, 29, PiiType.PERSON, "Иван Петрович Иванов", detector="test")]
    m1 = mask(t1, s1, ctx)
    assert m1.text == "Менеджер [PERSON_1]."

    # Call 2: Inflected form in a different field/call
    t2 = "Ивану Петровичу Иванову отправить копию."
    s2 = [EntitySpan(0, 23, PiiType.PERSON, "Ивану Петровичу Иванову", detector="test")]
    m2 = mask(t2, s2, ctx)
    # Must link to the same entity and receive [PERSON_1]!
    assert m2.text == "[PERSON_1] отправить копию."

    # Call 3: Replay both
    m1_replay = mask(t1, s1, ctx)
    m2_replay = mask(t2, s2, ctx)
    assert m1_replay.text == "Менеджер [PERSON_1]."
    assert m2_replay.text == "[PERSON_1] отправить копию."


def test_repeated_identical_email_gets_same_marker():
    ctx = Context(project_id="test_repeat_email", mode="marker")
    text = "Писать на a@example.test и копию на a@example.test"
    spans = [
        EntitySpan(10, 24, PiiType.EMAIL, "a@example.test", detector="test"),
        EntitySpan(36, 50, PiiType.EMAIL, "a@example.test", detector="test"),
    ]

    masked = mask(text, spans, ctx)
    assert masked.text == "Писать на [EMAIL_1] и копию на [EMAIL_1]"


def test_literal_marker_in_original_text_does_not_collide():
    ctx = Context(project_id="test_literal", mode="marker")
    text = "Literal [PERSON_1]; Иван Иванов"
    spans = [
        EntitySpan(20, 31, PiiType.PERSON, "Иван Иванов", detector="test"),
    ]

    masked = mask(text, spans, ctx)
    # Since [PERSON_1] was in the original text, the new entity must receive [PERSON_2]
    assert masked.text == "Literal [PERSON_1]; [PERSON_2]"

    restored = restore(masked.text, ctx)
    # [PERSON_2] unmasks to Иван Иванов, while literal [PERSON_1] was not created by the gateway
    # so it is returned unresolved/partial without corrupting user literal text!
    assert "Иван Иванов" in restored.text
    assert restored.status == RestoreStatus.PARTIAL


def test_partial_single_mention_linking():
    ctx = Context(project_id="test_partial_single", mode="marker")
    text = "Подписал Иван Иванов. Передать Иванову."
    spans = [
        EntitySpan(9, 20, PiiType.PERSON, "Иван Иванов", detector="test"),
        EntitySpan(31, 38, PiiType.PERSON, "Иванову", detector="test"),
    ]

    masked = mask(text, spans, ctx)
    # Single surname in document links to the sole full candidate
    assert masked.replacements[1].linked_by == "partial_single"
    assert masked.replacements[1].entity_id == masked.replacements[0].entity_id
    assert masked.text == "Подписал [PERSON_1]. Передать [PERSON_1]."


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


def test_realistic_pool_collision_handling():
    ctx = Context(project_id="test_pool_collision", mode="realistic")
    names = [
        "Иван Иванов",
        "Иван Петров",
        "Иван Сидоров",
        "Иван Волков",
        "Иван Орлов",
        "Иван Соколов",
    ]
    all_masked = []
    for name in names:
        span = EntitySpan(0, len(name), PiiType.PERSON, name, detector="test")
        res = mask(name, [span], ctx)
        all_masked.append(res.text)

    # Every masked representation must be distinct (no duplicate pseudonyms across distinct people)
    assert len(set(all_masked)) == len(names)

    # Restoring each individually must return the exact original person without cross-contamination
    for original, masked_str in zip(names, all_masked, strict=True):
        rest = restore(masked_str, ctx)
        assert rest.status == RestoreStatus.COMPLETE
        assert rest.text == original


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


def test_vault_concurrent_counter_atomicity(tmp_path: Path):
    db_file = tmp_path / "concurrent.db"
    # Pre-initialize schema so worker threads only perform atomic counter increments
    with Vault("concurrent_test", db_path=db_file):
        pass

    def increment_counter():
        with Vault("concurrent_test", db_path=db_file) as v:
            return v.get_next_marker_index(PiiType.PERSON)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(increment_counter) for _ in range(50)]
        results = [f.result() for f in futures]

    # All 50 increments must be unique and span 1..50
    assert len(set(results)) == 50
    assert sorted(results) == list(range(1, 51))


def test_different_full_names_with_same_surname_do_not_link():
    ctx = Context(project_id="test_diff_names_same_surn", mode="marker")
    s1 = [EntitySpan(0, 11, PiiType.PERSON, "Иван Петров", detector="test")]
    m1 = mask("Иван Петров", s1, ctx)

    s2 = [EntitySpan(0, 11, PiiType.PERSON, "Пётр Петров", detector="test")]
    m2 = mask("Пётр Петров", s2, ctx)

    assert m1.replacements[0].entity_id != m2.replacements[0].entity_id
    assert m1.replacements[0].linked_by == "new"
    assert m2.replacements[0].linked_by == "new"
    assert m1.text != m2.text

    # Restoring each returns the exact original person without cross-contamination
    r1 = restore(m1.text, ctx)
    r2 = restore(m2.text, ctx)
    assert r1.text == "Иван Петров"
    assert r2.text == "Пётр Петров"


def test_realistic_mode_preserves_distinct_surface_cases():
    ctx = Context(project_id="test_realistic_surfaces", mode="realistic")
    t1 = "Иван Петрович Иванов"
    s1 = [EntitySpan(0, 20, PiiType.PERSON, t1, detector="test")]
    m1 = mask(t1, s1, ctx)

    t2 = "Ивану Петровичу Иванову"
    s2 = [EntitySpan(0, 23, PiiType.PERSON, t2, detector="test")]
    m2 = mask(t2, s2, ctx)

    # Both refer to the same entity
    assert m1.replacements[0].entity_id == m2.replacements[0].entity_id

    # In realistic mode, both are inflected and restore losslessly to their exact original cases
    assert restore(m1.text, ctx).text == t1
    assert restore(m2.text, ctx).text == t2


def test_existing_literal_marker_is_not_corrupted_on_roundtrip():
    ctx = Context(project_id="test_literal_existing_marker", mode="marker")
    # Step 1: establish [PERSON_1] in vault for Ivan Ivanov
    s1 = [EntitySpan(0, 11, PiiType.PERSON, "Иван Иванов", detector="test")]
    m1 = mask("Иван Иванов", s1, ctx)
    assert m1.text == "[PERSON_1]"

    # Step 2: input document literally contains "[PERSON_1]" as user text,
    # with a span only on Petr Petrov
    text2 = "Literal [PERSON_1]; Пётр Петров"
    s2 = [EntitySpan(20, 31, PiiType.PERSON, "Пётр Петров", detector="test")]
    m2 = mask(text2, s2, ctx)

    # The newly issued marker must not collide with the literal [PERSON_1]
    assert "[PERSON_2]" in m2.text

    # Restoration must preserve the literal [PERSON_1] exactly,
    # without corrupting it into Ivan Ivanov
    r2 = restore(m2.text, ctx)
    assert r2.text == text2
    assert r2.status == RestoreStatus.COMPLETE
