import json
import socket
from itertools import permutations
from pathlib import Path

import pytest

from pii_gateway.core.types import Context, DetectError, EntitySpan, PiiType, check_spans
from pii_gateway.detect import detect, detector

CTX = Context("synthetic-test")
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "text,kind,raw",
    [
        ("Почта: anna+case@example.test.", PiiType.EMAIL, "anna+case@example.test"),
        ("Почта: юзер@пример.рф.", PiiType.EMAIL, "юзер@пример.рф"),
        ("Телефон +7 (000) 000-00-01.", PiiType.PHONE, "+7 (000) 000-00-01"),
        ("Телефон +7\u00a0000\u00a0000-00-01.", PiiType.PHONE, "+7\u00a0000\u00a0000-00-01"),
        ("8 000 000 00 01", PiiType.PHONE, "8 000 000 00 01"),
        ("Тел.: 000 000-00-01", PiiType.PHONE, "000 000-00-01"),
        ("Паспорт РФ: 00 00\n000001", PiiType.PASSPORT_RU, "00 00\n000001"),
        ("Паспорт 0000000001", PiiType.PASSPORT_RU, "0000000001"),
        ("00 00 000001", PiiType.PASSPORT_RU, "00 00 000001"),
    ],
)
def test_structured_fields(text, kind, raw):
    found = detector._resolve(text, detector._regex_spans(text))
    assert [(s.type, s.raw_text) for s in found] == [(kind, raw)]
    check_spans(text, found)


@pytest.mark.parametrize(
    "text",
    [
        "Версия 1.2.3, сумма 12000, заказ 12345, дата 25.10.2026",
        "[PERSON_1] [EMAIL_2] [PASSPORT_RU_4]",
        "id80000000001 800000000012 0000000001",  # no substrings in identifiers
    ],
)
def test_no_structured_false_positive(text):
    assert detector._regex_spans(text) == []


def test_overlap_resolution_is_independent_of_candidate_order():
    text = "aa@example.test ZZ"
    mail = EntitySpan(0, 15, PiiType.EMAIL, text[:15], "regex:email@1")
    bad_ner = EntitySpan(0, len(text), PiiType.PERSON, text, "ner")
    short = EntitySpan(3, 10, PiiType.PHONE, text[3:10], "synthetic")
    for order in permutations([mail, bad_ner, short]):
        assert detector._resolve(text, list(order)) == [mail]


def test_adjacent_mentions_and_tie_break():
    text = "ИванПётр"
    a = EntitySpan(0, 4, PiiType.PERSON, "Иван", "a")
    b = EntitySpan(0, 4, PiiType.PERSON, "Иван", "b")
    c = EntitySpan(4, 8, PiiType.PERSON, "Пётр", "a")
    assert detector._resolve(text, [b, c, a]) == [a, c]


def test_longer_ner_span_wins():
    text = "Иван Иванов"
    short = EntitySpan(0, 4, PiiType.PERSON, text[:4], "a")
    full = EntitySpan(0, 11, PiiType.PERSON, text, "z")
    assert detector._resolve(text, [short, full]) == [full]


def test_ner_error_does_not_return_partial_regex_result(monkeypatch):
    def broken(_text):
        raise RuntimeError("synthetic-secret@example.test")

    monkeypatch.setattr(detector, "_person_spans", broken)
    with pytest.raises(DetectError, match="^local PII detection failed$") as exc:
        detect("synthetic-secret@example.test", CTX)
    assert exc.value.__suppress_context__


def test_invalid_model_span_is_failure(monkeypatch):
    monkeypatch.setattr(
        detector, "_person_spans", lambda _: [EntitySpan(0, 4, PiiType.PERSON, "Пётр", "broken")]
    )
    with pytest.raises(DetectError):
        detect("Иван", CTX)


def test_model_initialization_failure_is_closed(monkeypatch):
    import natasha

    def broken():
        raise OSError("missing model")

    monkeypatch.setattr(detector, "_ner", None)
    monkeypatch.setattr(natasha, "NewsEmbedding", broken)
    with pytest.raises(DetectError):
        detect("test@example.test", CTX)
    assert detector._ner is None


def test_real_model_initializes_and_runs_without_socket_access(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network attempted")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(detector, "_ner", None)
    text = "🧪 Отправить Анне Петровой на anna@example.test."
    spans = detect(text, CTX)
    assert [(s.type, s.raw_text) for s in spans] == [
        (PiiType.PERSON, "Анне Петровой"),
        (PiiType.EMAIL, "anna@example.test"),
    ]
    assert spans[0].start == 12
    assert all(s.confidence is None for s in spans)


def test_repeats_and_namesakes_remain_separate_mentions():
    text = "Иван Иванов написал. Иван Иванов ответил."
    spans = detect(text, CTX)
    assert [s.raw_text for s in spans] == ["Иван Иванов", "Иван Иванов"]
    assert spans[0].start != spans[1].start


@pytest.mark.parametrize("text", ["", " \n\t", "🧪"])
def test_empty_or_tokenless_input(text):
    assert detect(text, CTX) == []


@pytest.mark.parametrize("filename", ["synthetic_v0.jsonl", "synthetic_v1.jsonl"])
def test_corpus_annotations_and_live_output_contract(filename):
    seen = set()
    for line in (FIXTURES / filename).read_text().splitlines():
        row = json.loads(line)
        assert row["synthetic"] is True
        assert row["id"] not in seen
        seen.add(row["id"])
        expected = [EntitySpan(**s, detector="annotation") for s in row["expected"]]
        check_spans(row["text"], expected)
        check_spans(row["text"], detect(row["text"], CTX))
