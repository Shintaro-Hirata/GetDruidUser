# tests/test_time_ranges.py
from datetime import datetime, timedelta, timezone

import pytest

from src.domain.models import TimeRange
from src.domain.time_ranges import parse_ranges, split_range

JST = timezone(timedelta(hours=9))


def test_parse_ranges_basic():
    text = "2025-12-09T01:57:00+09:00, 2025-12-09T05:48:53+09:00, サンプル1"
    out = parse_ranges(text)
    assert len(out) == 1
    r = out[0]
    assert r.label == "サンプル1"
    assert r.start == datetime(2025, 12, 9, 1, 57, 0, tzinfo=JST)
    assert r.end == datetime(2025, 12, 9, 5, 48, 53, tzinfo=JST)


def test_parse_ranges_without_label_and_fullwidth_comma():
    text = "2025-12-09T01:00:00+09:00，2025-12-09T02:00:00+09:00"
    out = parse_ranges(text)
    assert len(out) == 1
    assert out[0].label == ""


def test_parse_ranges_multiple_lines_skip_empty():
    text = (
        "2025-12-09T01:00:00+09:00, 2025-12-09T02:00:00+09:00, A\n"
        "\n"
        "2025-12-10T01:00:00+09:00, 2025-12-10T02:00:00+09:00, B\n"
    )
    out = parse_ranges(text)
    assert [r.label for r in out] == ["A", "B"]


def test_parse_ranges_rejects_reversed():
    with pytest.raises(ValueError):
        parse_ranges("2025-12-09T02:00:00+09:00, 2025-12-09T01:00:00+09:00")


def test_parse_ranges_rejects_empty():
    with pytest.raises(ValueError):
        parse_ranges("\n\n")


def test_split_range_no_split_when_zero():
    s = datetime(2025, 12, 9, 1, 0)
    e = datetime(2025, 12, 9, 3, 0)
    assert split_range(s, e, 0) == [(s, e)]


def test_split_range_exact_chunks():
    s = datetime(2025, 12, 9, 1, 0)
    e = datetime(2025, 12, 9, 3, 0)
    chunks = split_range(s, e, 60)
    assert chunks == [
        (s, s + timedelta(hours=1)),
        (s + timedelta(hours=1), e),
    ]


def test_split_range_last_chunk_clipped():
    s = datetime(2025, 12, 9, 1, 0)
    e = datetime(2025, 12, 9, 2, 30)
    chunks = split_range(s, e, 60)
    assert chunks[-1] == (s + timedelta(hours=1), e)
    assert len(chunks) == 2


def test_time_range_is_frozen_dataclass():
    r = TimeRange(start=datetime(2025, 1, 1), end=datetime(2025, 1, 2), label="x")
    with pytest.raises(Exception):
        r.label = "y"  # type: ignore[misc]
