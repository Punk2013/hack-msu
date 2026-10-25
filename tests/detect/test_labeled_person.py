from itertools import permutations
from pathlib import Path

import pytest

from pii_gateway.core.types import Context, EntitySpan, PiiType, check_spans
from pii_gateway.detect import detect, detector

CTX = Context("synthetic-labeled-person")


@pytest.mark.parametrize(
    "text,raw",
    [
        ("ФИО: Анна Сергеевна Смирнова", "Анна Сергеевна Смирнова"),
        ("клиент: Анна Смирнова.", "Анна Смирнова"),
        ("🧪 Менеджер:\tИван\u00a0Петров; статус: готов", "Иван\u00a0Петров"),
        ("ФИО: Анна-Мария Смирнова-Петрова\nEmail: a@example.test", "Анна-Мария Смирнова-Петрова"),
    ],
)
def test_labeled_person_without_ner_candidates(monkeypatch, text, raw):
    monkeypatch.setattr(detector, "_person_spans", lambda _: [])
    spans = [s for s in detect(text, CTX) if s.type == PiiType.PERSON]
    assert [s.raw_text for s in spans] == [raw]
    assert spans[0].start == text.index(raw)
    check_spans(text, spans)


@pytest.mark.parametrize(
    "text",
    [
        "Клиент: ООО Ромашка",
        "Статус: Готов К Отправке",
        "Анна Сергеевна Смирнова",
        "Неклиент: Анна Смирнова",
        "ФИО: Анна\nСмирнова",
        "ФИО:\nАнна Смирнова",
        "ФИО: Анна Смирнова Петрова Иванова",
        "ФИО: Анна Смирнова42",
        "ФИО: Анна Смирнова@example.test",
        "ФИО: Анна Смирнова-",
    ],
)
def test_labeled_rule_does_not_expand_to_unrelated_fields(text):
    assert not [s for s in detector._regex_spans(text) if s.type == PiiType.PERSON]


def test_labeled_field_wins_over_ner_including_label():
    text = "ФИО: Анна Смирнова"
    labeled = detector._regex_spans(text)
    broad = EntitySpan(0, len(text), PiiType.PERSON, text, "natasha@1.6.0")
    for order in permutations([*labeled, broad]):
        assert [s.raw_text for s in detector._resolve(text, list(order))] == ["Анна Смирнова"]


def test_multiple_fields_do_not_cross_lines_or_swallow_labels(monkeypatch):
    monkeypatch.setattr(detector, "_person_spans", lambda _: [])
    text = "ФИО: Анна Смирнова\nМенеджер: Иван Петров. Клиент: Пётр Иванов."
    assert [s.raw_text for s in detect(text, CTX)] == [
        "Анна Смирнова", "Иван Петров", "Пётр Иванов",
    ]


def test_full_demo_client2_with_live_ner():
    text = (Path(__file__).resolve().parents[2] / "demo/clients/client2.txt").read_text()
    spans = detect(text, CTX)
    assert [(s.type, s.raw_text) for s in spans] == [
        (PiiType.PERSON, "Анна Сергеевна Смирнова"),
        (PiiType.EMAIL, "anna.smirnova@example.test"),
        (PiiType.PHONE, "+7 900 000-00-02"),
        (PiiType.PASSPORT_RU, "00 02 000002"),
        (PiiType.PERSON, "Анне Сергеевне"),
        (PiiType.PERSON, "Иван Петрович Иванов"),
    ]
    check_spans(text, spans)
