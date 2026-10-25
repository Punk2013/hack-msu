import pytest
from pii_gateway.core.types import Context, EntitySpan, PiiType
from pii_gateway.pseudo import mask, restore

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('PII_VAULT_DIR', str(tmp_path/'vault'))

def person(text, ctx):
    return mask(text, [EntitySpan(0,len(text),PiiType.PERSON,text,detector='review')], ctx)

@pytest.mark.parametrize('first,second', [
    ('Иван Петрович Сидоров','Иван Сергеевич Сидоров'),
    ('Иван Петрович Сидоров','Иван Петрович Иванов'),
])
def test_distinct_full_names(first, second):
    ctx=Context('distinct','marker')
    a=person(first,ctx)
    b=person(second,ctx)
    assert a.replacements[0].entity_id != b.replacements[0].entity_id
    assert restore(b.text,ctx).text == second

def test_literal_without_person_span():
    ctx=Context('literal','marker')
    person('Иван Иванов',ctx)
    raw='Пример синтаксиса: [PERSON_1]'
    protected=mask(raw,[],ctx)
    assert restore(protected.text,ctx).text == raw

def test_case_roundtrip():
    ctx=Context('case','marker')
    person('Иван Петрович Иванов',ctx)
    raw='Ивану Петровичу Иванову'
    assert restore(person(raw,ctx).text,ctx).text == raw
