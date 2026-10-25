"""Independent acceptance tests for issue #48, reviewed at PR #50 / 626310f.

Synthetic data only. Run with the reviewed checkout's src on PYTHONPATH.
"""
from types import SimpleNamespace
from uuid import UUID

import pytest

from pii_gateway.core.types import Context, EntitySpan, PiiType, RestoreStatus
from pii_gateway.detect import detect
from pii_gateway.gateway.transform import CorePipeline
from pii_gateway.pseudo import engine, mask, restore
from pii_gateway.vault import Vault


@pytest.fixture(autouse=True)
def isolated_vault(tmp_path, monkeypatch):
    monkeypatch.setenv("PII_VAULT_DIR", str(tmp_path / "vault"))


def spans_for(text, *names):
    spans, pos = [], 0
    for name in names:
        start = text.index(name, pos)
        spans.append(EntitySpan(start, start + len(name), PiiType.PERSON, name, "test"))
        pos = start + len(name)
    return spans


def surfaces(result):
    return [result.text[r.start:r.end] for r in result.replacements]


@pytest.mark.parametrize("mode", ["realistic", "marker"])
@pytest.mark.parametrize("reverse", [False, True])
def test_real_detector_basic_link_and_replay(mode, reverse):
    ctx = Context("basic", mode)
    sentences = ["Игорь Олегович пришёл на встречу.", "Игорь попросил прислать акт."]
    text = " ".join(reversed(sentences) if reverse else sentences)
    spans = detect(text, ctx)
    assert len(spans) == 2
    first = mask(text, spans, ctx)
    assert len({r.entity_id for r in first.replacements}) == 1
    assert restore(first.text, ctx).text == text
    if mode == "realistic":
        assert len({s.split()[0] for s in surfaces(first)}) == 1
    second = mask(text, spans, ctx)
    assert second.text == first.text
    assert [r.entity_id for r in second.replacements] == [r.entity_id for r in first.replacements]


@pytest.mark.parametrize("mode", ["realistic", "marker"])
def test_ambiguous_short_name_is_marked(mode):
    ctx = Context("ambiguous", mode)
    text = "Игорь Олегович и Игорь Петрович пришли. Игорь опоздал."
    result = mask(text, detect(text, ctx), ctx)
    assert len({r.entity_id for r in result.replacements}) == 3
    assert restore(result.text, ctx).text == text
    with Vault(ctx.project_id) as vault:
        by_id = {e["entity_id"]: e for e in vault.list_entities()}
    assert by_id[result.replacements[-1].entity_id]["is_ambiguous"]


@pytest.mark.parametrize("mode", ["realistic", "marker"])
def test_same_person_word_order_does_not_create_false_ambiguity(mode):
    ctx = Context("order", mode)
    names = ("Игорь Олегович Иванов", "Иванов Игорь Олегович", "Игорь")
    text = ". ".join(names) + "."
    result = mask(text, spans_for(text, *names), ctx)
    assert restore(result.text, ctx).text == text
    assert len({r.entity_id for r in result.replacements}) == 1


@pytest.mark.parametrize("mode", ["realistic", "marker"])
def test_patronymic_short_form_before_full_name(mode):
    ctx = Context("patronymic", mode)
    names = ("Игорь Олегович", "Игорь Олегович Иванов", "Игорь")
    text = ". ".join(names) + "."
    result = mask(text, spans_for(text, *names), ctx)
    assert restore(result.text, ctx).text == text
    assert len({r.entity_id for r in result.replacements}) == 1


def test_collision_keeps_one_fake_first_name(monkeypatch):
    # Freeze only entity identity; detection, morphology and collision handling are real.
    seed = "111111111111" + "0" * 20
    monkeypatch.setattr(engine, "uuid", SimpleNamespace(uuid4=lambda: UUID(hex=seed)))
    eid = "ent_111111111111"
    ctx = Context("collision")
    first_fake = engine._pseudo_parse("Name", "masc", eid, 0).word.capitalize()
    with Vault(ctx.project_id) as vault:
        vault.add_entity("other", PiiType.PERSON)
        vault.add_pair(first_fake, "Борис", "other")
    text = "Игорь Олегович пришёл на встречу. Игорь попросил прислать акт."
    result = mask(text, detect(text, ctx), ctx)
    assert len({r.entity_id for r in result.replacements}) == 1
    assert restore(result.text, ctx).text == text
    full, short = surfaces(result)
    assert full.split()[0] == short, (full, short)


def test_same_surname_replay_is_stable(monkeypatch):
    seed = "749c5ccf8b9b" + "0" * 20
    monkeypatch.setattr(engine, "uuid", SimpleNamespace(uuid4=lambda: UUID(hex=seed)))
    ctx = Context("surname_replay")
    pipeline = CorePipeline(ctx)
    text = "Алексей Дмитриевич Соколов"
    first, second = pipeline.mask(text), pipeline.mask(text)
    assert pipeline.restore(first).text == text
    assert pipeline.restore(second).text == text
    assert first == second


@pytest.mark.parametrize("mode", ["realistic", "marker"])
def test_independent_fields_do_not_link(mode):
    ctx = Context("independent", mode)
    first = "Игорь Олегович пришёл. Игорь попросил акт."
    second = "Игорь Петрович пришёл. Игорь попросил счёт."
    a = mask(first, detect(first, ctx), ctx)
    b = mask(second, detect(second, ctx), ctx)
    assert len({r.entity_id for r in a.replacements}) == 1
    assert len({r.entity_id for r in b.replacements}) == 1
    assert a.replacements[0].entity_id != b.replacements[0].entity_id
    assert mask(first, detect(first, ctx), ctx).text == a.text
    assert mask(second, detect(second, ctx), ctx).text == b.text
    for original, result in ((first, a), (second, b)):
        restored = restore(result.text, ctx)
        assert restored.status == RestoreStatus.COMPLETE
        assert restored.text == original
