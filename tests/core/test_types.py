import pytest

from pii_gateway.core.types import DetectError, EntitySpan, PiiType, check_spans


def span(start: int, end: int, raw: str) -> EntitySpan:
    return EntitySpan(start, end, PiiType.PERSON, raw, detector="test")


def test_code_point_indices_with_emoji():
    text = "🧪 Анне Петровой"
    s = span(2, 15, "Анне Петровой")
    check_spans(text, [s])


def test_range_must_match_raw_text_length():
    with pytest.raises(ValueError):
        span(0, 3, "Иван")


def test_overlap_is_rejected():
    text = "Иван Иванов"
    with pytest.raises(DetectError):
        check_spans(text, [span(0, 11, "Иван Иванов"), span(5, 11, "Иванов")])


def test_slice_mismatch_is_rejected():
    with pytest.raises(DetectError):
        check_spans("Пётр Петров", [span(0, 4, "Иван")])
