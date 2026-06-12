# tests/test_exclude_editor.py
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from src.domain.models import ExcludeRange
from src.ui.exclude_editor import selection_sec_times
from src.ui.views.common import split_by_excludes

UTC = timezone.utc


def test_selection_sec_times_extracts_customdata():
    event = SimpleNamespace(
        selection=SimpleNamespace(
            points=[
                {"customdata": ["2025-12-09T01:00:30Z", 35.4, 139.6]},
                {"customdata": ["2025-12-09T01:00:31Z", 35.4, 139.6]},
                {"customdata": None},
                {},
            ]
        )
    )
    assert selection_sec_times(event) == [
        "2025-12-09T01:00:30Z",
        "2025-12-09T01:00:31Z",
    ]


def test_selection_sec_times_handles_no_selection():
    assert selection_sec_times(None) == []
    assert selection_sec_times(SimpleNamespace()) == []


def _df():
    return pd.DataFrame(
        {
            "sec_time": [
                "2025-12-09T01:00:00Z",
                "2025-12-09T01:05:00Z",
                "2025-12-09T01:10:00Z",
            ],
            "v": [1, 2, 3],
        }
    )


def test_split_by_excludes_partitions_rows():
    ex = ExcludeRange(
        start=datetime(2025, 12, 9, 1, 4, tzinfo=UTC),
        end=datetime(2025, 12, 9, 1, 6, tzinfo=UTC),
    )
    active, excluded = split_by_excludes(_df(), [ex])
    assert list(active["v"]) == [1, 3]
    assert list(excluded["v"]) == [2]


def test_split_by_excludes_no_excludes_returns_all_active():
    active, excluded = split_by_excludes(_df(), [])
    assert len(active) == 3
    assert excluded.empty


def test_split_by_excludes_boundary_is_half_open():
    # [start, end) ：start ちょうどは除外、end ちょうどは含まれる
    ex = ExcludeRange(
        start=datetime(2025, 12, 9, 1, 0, tzinfo=UTC),
        end=datetime(2025, 12, 9, 1, 5, tzinfo=UTC),
    )
    active, excluded = split_by_excludes(_df(), [ex])
    assert list(excluded["v"]) == [1]
    assert list(active["v"]) == [2, 3]
