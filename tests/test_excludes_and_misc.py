# tests/test_excludes_and_misc.py
from datetime import datetime

import pandas as pd
import pytest

from src.domain.time_ranges import (
    parse_exclude_ranges_text,
    suggested_split_minutes_from_ranges_text,
)
from src.export.excel import to_excel_bytes


def test_parse_exclude_ranges_comma():
    out = parse_exclude_ranges_text(
        "2025-12-15T08:00:00+09:00,2025-12-15T08:10:00+09:00"
    )
    assert len(out) == 1
    assert out[0].start == datetime.fromisoformat("2025-12-15T08:00:00+09:00")
    assert out[0].end == datetime.fromisoformat("2025-12-15T08:10:00+09:00")


def test_parse_exclude_ranges_hyphen_and_comment_and_sort():
    text = (
        "# コメント行\n"
        "2025-12-15T09:00:00+09:00 - 2025-12-15T09:10:00+09:00\n"
        "2025-12-15T08:00:00+09:00, 2025-12-15T08:10:00+09:00\n"
    )
    out = parse_exclude_ranges_text(text)
    assert len(out) == 2
    assert out[0].start < out[1].start  # ソートされる


def test_parse_exclude_ranges_rejects_reversed():
    with pytest.raises(ValueError):
        parse_exclude_ranges_text(
            "2025-12-15T08:10:00+09:00,2025-12-15T08:00:00+09:00"
        )


def test_parse_exclude_ranges_empty():
    assert parse_exclude_ranges_text("") == []


def test_suggested_split_minutes_max_duration():
    text = (
        "2025-12-09T01:00:00+09:00, 2025-12-09T02:00:00+09:00, A\n"
        "2025-12-10T01:00:00+09:00, 2025-12-10T03:30:00+09:00, B\n"
    )
    assert suggested_split_minutes_from_ranges_text(text) == 150


def test_suggested_split_minutes_fallback_60():
    assert suggested_split_minutes_from_ranges_text("garbage") == 60


def test_to_excel_bytes_roundtrip():
    sheets = {
        "T1_C1_Q1": pd.DataFrame({"a": [1, 2], "b": ["x", "y"]}),
        "bad[name]:with*chars?": pd.DataFrame({"c": [3]}),
    }
    data = to_excel_bytes(sheets)
    assert data[:2] == b"PK"  # xlsx (zip) マジック
    from io import BytesIO
    loaded = pd.read_excel(BytesIO(data), sheet_name=None)
    assert "T1_C1_Q1" in loaded
    assert len(loaded) == 2
    assert list(loaded["T1_C1_Q1"]["a"]) == [1, 2]
