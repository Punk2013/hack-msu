import re

import pytest

from pii_gateway.core.types import PiiType
from pii_gateway.pseudo.fakes import fake_value

# Layout of the original: every digit becomes "9", everything else is kept.
SHAPE = re.compile(r"\d")


@pytest.mark.parametrize(
    ("kind", "original"),
    [
        (PiiType.PHONE, "+7 900 000-00-01"),
        (PiiType.PHONE, "8 (900) 000-00-02"),
        (PiiType.PASSPORT_RU, "00 01 000001"),
        (PiiType.PASSPORT_RU, "00 00\n000002"),
    ],
)
def test_structured_fake_keeps_format(kind, original):
    fake = fake_value(kind, original, "ent_1")
    assert fake != original
    assert SHAPE.sub("9", fake) == SHAPE.sub("9", original)


def test_phone_keeps_country_prefix():
    assert fake_value(PiiType.PHONE, "+7 900 000-00-01", "ent_1").startswith("+7 9")
    assert fake_value(PiiType.PHONE, "8 (900) 000-00-02", "ent_2").startswith("8 (9")


def test_email_is_plausible_and_hides_domain():
    fake = fake_value(PiiType.EMAIL, "ivan.ivanov@corp-employer.test", "ent_1")
    assert re.fullmatch(r"[a-z._]+\d{2}@(mail\.ru|yandex\.ru|gmail\.com|inbox\.ru|bk\.ru)", fake)
    assert "corp-employer" not in fake


def test_deterministic_per_entity_and_attempt():
    a = fake_value(PiiType.PHONE, "+7 900 000-00-01", "ent_1")
    assert a == fake_value(PiiType.PHONE, "+7 900 000-00-01", "ent_1")
    assert a != fake_value(PiiType.PHONE, "+7 900 000-00-01", "ent_2")
    assert a != fake_value(PiiType.PHONE, "+7 900 000-00-01", "ent_1", attempt=1)


def test_types_without_generator_fall_back_to_marker():
    assert fake_value(PiiType.PERSON, "Иван Иванов", "ent_1") is None
