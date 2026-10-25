"""Issue #48: «Игорь Олегович … Игорь» must stay one person within a text."""

from __future__ import annotations

from pathlib import Path

import pytest

from pii_gateway.core.types import Context, EntitySpan, PiiType
from pii_gateway.pseudo import mask, restore


@pytest.fixture(autouse=True)
def vault_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PII_VAULT_DIR", str(tmp_path / "vaults"))


def persons(text: str, *raws: str) -> list[EntitySpan]:
    spans, pos = [], 0
    for raw in raws:
        start = text.index(raw, pos)
        spans.append(EntitySpan(start, start + len(raw), PiiType.PERSON, raw, "test"))
        pos = start + len(raw)
    return spans


@pytest.mark.parametrize("mode", ["realistic", "marker"])
def test_first_name_after_full_name_is_same_person(mode):
    ctx = Context(f"igor-{mode}", mode)
    text = "Игорь Олегович пришёл на встречу. Игорь попросил прислать акт."
    result = mask(text, persons(text, "Игорь Олегович", "Игорь"), ctx)
    assert result.replacements[0].entity_id == result.replacements[1].entity_id
    assert "Игорь" not in result.text
    assert restore(result.text, ctx).text == text
    if mode == "realistic":
        full, short = (result.text[r.start : r.end] for r in result.replacements)
        assert full.split()[0] == short  # «Виктор Алексеевич … Виктор»


def test_inflected_first_names_stay_with_the_person():
    ctx = Context("igor-cases")
    text = "Игорь Олегович пришёл. Игорю отправили акт, с Игорем договорились."
    result = mask(text, persons(text, "Игорь Олегович", "Игорю", "Игорем"), ctx)
    assert len({r.entity_id for r in result.replacements}) == 1
    assert restore(result.text, ctx).text == text


def test_first_name_before_full_name_is_same_person():
    ctx = Context("igor-before")
    text = "Игорь сказал, что Игорь Олегович придёт."
    result = mask(text, persons(text, "Игорь", "Игорь Олегович"), ctx)
    assert result.replacements[0].entity_id == result.replacements[1].entity_id
    assert restore(result.text, ctx).text == text


def test_two_igors_bare_name_is_not_guessed():
    ctx = Context("two-igors")
    text = "Игорь Олегович и Игорь Петрович пришли. Игорь опоздал."
    result = mask(text, persons(text, "Игорь Олегович", "Игорь Петрович", "Игорь"), ctx)
    a, b, short = (r.entity_id for r in result.replacements)
    assert a != b and short not in (a, b)
    assert restore(result.text, ctx).text == text


def test_other_document_in_project_is_not_linked():
    ctx = Context("two-docs")
    first = mask("Игорь Олегович пришёл.", persons("Игорь Олегович пришёл.", "Игорь Олегович"), ctx)
    second = mask("Игорь позвонил.", persons("Игорь позвонил.", "Игорь"), ctx)
    assert first.replacements[0].entity_id != second.replacements[0].entity_id


def test_replay_is_stable():
    ctx = Context("igor-replay")
    text = "Игорь Олегович пришёл на встречу. Игорь попросил прислать акт."
    spans = persons(text, "Игорь Олегович", "Игорь")
    assert len({mask(text, spans, ctx).text for _ in range(3)}) == 1
