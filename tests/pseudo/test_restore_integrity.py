import pytest

from pii_gateway.core.types import Context, EntitySpan, MaskError, PiiType
from pii_gateway.pseudo import mask, restore


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PII_VAULT_DIR", str(tmp_path / "vault"))


def masked(text, ctx, kind=PiiType.PERSON):
    return mask(text, [EntitySpan(0, len(text), kind, text, detector="synthetic-review")], ctx)


@pytest.mark.parametrize("mode", ["marker", "realistic"])
def test_distinct_complete_names_do_not_share_entity(mode):
    ctx = Context("names", mode)
    a = masked("Иван Петров", ctx)
    b = masked("Пётр Петров", ctx)
    assert a.replacements[0].entity_id != b.replacements[0].entity_id


@pytest.mark.parametrize(
    "first,second,kind",
    [
        ("Иван Петрович Иванов", "Ивану Петровичу Иванову", PiiType.PERSON),
        ("+7 900 000-00-01", "8 900 000 00 01", PiiType.PHONE),
    ],
)
def test_marker_preserves_each_surface(first, second, kind):
    ctx = Context("forms", "marker")
    a = masked(first, ctx, kind)
    b = masked(second, ctx, kind)
    assert restore(a.text, ctx).text == first
    assert restore(b.text, ctx).text == second


def test_marker_distinct_names_roundtrip():
    ctx = Context("names_rt", "marker")
    masked("Иван Петров", ctx)
    b = masked("Пётр Петров", ctx)
    assert restore(b.text, ctx).text == "Пётр Петров"


@pytest.mark.parametrize("with_spans", [False, True])
def test_marker_literal_is_refused_before_it_can_be_corrupted(with_spans):
    ctx = Context("literal", "marker")
    masked("Иван Иванов", ctx)
    raw = "Literal [PERSON_1]; Иван Иванов"
    start = raw.index("Иван Иванов")
    spans = [
        EntitySpan(start, len(raw), PiiType.PERSON, "Иван Иванов", detector="synthetic-review")
    ]
    with pytest.raises(MaskError, match="reserved marker"):
        mask(raw, spans if with_spans else [], ctx)


@pytest.mark.parametrize("raw", ["[PERSON_999]", "[PERSON_x]", "[PERSON_1", "[PERSON]"])
def test_unallocated_and_malformed_literals_are_also_refused(raw):
    with pytest.raises(MaskError, match="reserved marker"):
        mask(raw, [], Context("literal-empty"))


def test_surface_markers_remain_stable_after_vault_growth():
    ctx = Context("surface-replay", "marker")
    first = masked("Иван Петрович Иванов", ctx)
    second = masked("Ивану Петровичу Иванову", ctx)
    assert first.replacements[0].entity_id == second.replacements[0].entity_id
    assert first.text != second.text
    assert masked("Ивану Петровичу Иванову", ctx).text == second.text
    assert masked("Иван Петрович Иванов", ctx).text == first.text


def test_surname_becomes_ambiguous_between_different_people():
    ctx = Context("ambiguous", "marker")
    a = masked("Иван Петров", ctx)
    b = masked("Пётр Петров", ctx)
    surname = masked("Петров", ctx)
    assert surname.replacements[0].entity_id not in {
        a.replacements[0].entity_id,
        b.replacements[0].entity_id,
    }
    assert restore(surname.text, ctx).text == "Петров"
