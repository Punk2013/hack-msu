"""Live 7b run: a restored short form came back in history and the detector missed it."""

from pathlib import Path

import pytest

from pii_gateway.core.types import Context
from pii_gateway.gateway.transform import CorePipeline

CARD = (Path(__file__).resolve().parents[2] / "demo" / "clients" / "client1.txt").read_text(
    encoding="utf-8"
)


@pytest.fixture(autouse=True)
def vault_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PII_VAULT_DIR", str(tmp_path / "vault"))


def test_value_restored_earlier_is_masked_even_if_detector_misses_it(monkeypatch):
    pipeline = CorePipeline(Context("known"))
    masked_card = pipeline.mask(CARD)
    fake_surname = [line for line in masked_card.splitlines() if line.startswith("ФИО")][0]
    fake_surname = fake_surname.split()[-1]
    history = pipeline.restore(f"Уважаемый {fake_surname} И.!").text
    assert "Иванов" in history  # what the agent keeps locally

    monkeypatch.setattr("pii_gateway.detect.detector._person_spans", lambda text: [])
    outbound = pipeline.mask(f"{history} Телефон: +7 900 000-00-01.")
    assert "Иванов" not in outbound
    assert "+7 900 000-00-01" not in outbound
    assert pipeline.restore(outbound).text == f"{history} Телефон: +7 900 000-00-01."


def test_short_originals_are_not_masked_everywhere():
    pipeline = CorePipeline(Context("short"))
    pipeline.mask(CARD)
    text = "Статус: ок, до 01.12.2026."
    assert pipeline.mask(text) == text


def test_detector_span_covering_part_of_known_value_does_not_leave_rest_open(monkeypatch):
    # review of #47: detector returned only "Иван Петрович" of a stored full name
    from pii_gateway.core.types import EntitySpan, PiiType

    pipeline = CorePipeline(Context("partial-overlap", "marker"))
    full = "Иван Петрович Иванов"
    pipeline.mask(f"ФИО: {full}.")
    monkeypatch.setattr(
        "pii_gateway.detect.detect",
        lambda text, ctx: [EntitySpan(0, 13, PiiType.PERSON, "Иван Петрович", "test")],
    )
    outbound = pipeline.mask(full)
    assert "Иванов" not in outbound and "Иван" not in outbound
    assert pipeline.restore(outbound).text == full
