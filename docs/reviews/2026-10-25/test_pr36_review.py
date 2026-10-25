"""Run against PR #36 with PYTHONPATH=src pytest <this-file>. Synthetic names only."""
from types import SimpleNamespace

import pytest

from pii_gateway.core.types import Context, EntitySpan, PiiType
from pii_gateway.pseudo import engine


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PII_VAULT_DIR", str(tmp_path / "vault"))
    # Two actual generator IDs which yield the surname Голубев on attempt 0.
    ids = iter([2, 8])
    monkeypatch.setattr(
        engine.uuid, "uuid4", lambda: SimpleNamespace(hex=f"{next(ids):032x}"[::-1])
    )


def person(text, ctx):
    return engine.mask(text, [EntitySpan(0, len(text), PiiType.PERSON, text, "review")], ctx)


def test_surname_alias_never_restores_to_another_person():
    ctx = Context("distinct-surnames", "realistic")
    person("Иван Петрович Иванов", ctx)
    second = person("Пётр Александрович Петров", ctx)
    assert engine.restore(second.text, ctx).text == "Пётр Александрович Петров"
    # Either allocate a non-conflicting alias or refuse ambiguous restoration.
    restored = engine.restore(second.text.split()[-1], ctx)
    assert restored.text != "Иванов", "second client's surname was restored as the first"


def test_same_original_surname_does_not_abort_masking():
    ctx = Context("shared-original-surname", "realistic")
    person("Иван Петрович Иванов", ctx)
    second = person("Пётр Александрович Иванов", ctx)
    assert engine.restore(second.text, ctx).text == "Пётр Александрович Иванов"
