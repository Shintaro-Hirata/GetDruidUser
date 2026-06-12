# tests/test_legs.py
import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.services.legs import (
    Leg,
    _to_dt,
    dates_for_vehicle,
    fetch_legs_from_jsonl,
    legs_for,
    vehicles,
)

UTC = timezone.utc
JST = timezone(timedelta(hours=9))


def test_to_dt_epoch_seconds():
    assert _to_dt(1765244220) == datetime(2025, 12, 9, 1, 37, tzinfo=UTC)


def test_to_dt_epoch_millis():
    assert _to_dt(1765244220000) == datetime(2025, 12, 9, 1, 37, tzinfo=UTC)


def test_to_dt_numeric_string():
    assert _to_dt("1765244220") == datetime(2025, 12, 9, 1, 37, tzinfo=UTC)


def test_to_dt_iso_string():
    assert _to_dt("2025-12-09T10:37:00+09:00") == datetime(2025, 12, 9, 10, 37, tzinfo=JST)


def test_to_dt_invalid():
    assert _to_dt(None) is None
    assert _to_dt("garbage") is None


def _leg(vehicle="giga07", name="昼勤", start_h=1, day=9) -> Leg:
    return Leg(
        vehicle_id=vehicle,
        display_name=name,
        start=datetime(2025, 12, day, start_h, 0, tzinfo=UTC),
        end=datetime(2025, 12, day, start_h + 2, 0, tzinfo=UTC),
        version="v1.2.3",
    )


def test_fetch_legs_from_jsonl_parses_and_sorts():
    rows = [
        {"vehicle_id": "giga07", "display_name": "A",
         "data_start_time": 1765244220, "data_end_time": 1765247820,
         "version": "v1", "guid": "g1"},
        {"vehicle_id": "giga08", "display_name": "B",
         "data_start_time": 1765330620, "data_end_time": 1765334220},
        {"vehicle_id": "bad", "display_name": "範囲が逆",
         "data_start_time": 100, "data_end_time": 50},
    ]
    text = "\n".join(json.dumps(r) for r in rows) + "\n\nnot-json\n"

    resp = MagicMock()
    resp.text = text
    resp.raise_for_status = MagicMock()
    with patch("src.services.legs.requests.get", return_value=resp) as mock_get:
        legs = fetch_legs_from_jsonl("http://example/legs_index.jsonl")

    mock_get.assert_called_once()
    assert [l.display_name for l in legs] == ["B", "A"]  # 新しい順
    assert legs[1].version == "v1"
    assert legs[1].vehicle_id == "giga07"


def test_leg_to_range_line_jst():
    leg = _leg()
    line = leg.to_range_line()
    # UTC 01:00 → JST 10:00
    assert line == "2025-12-09T10:00:00+09:00, 2025-12-09T12:00:00+09:00, 昼勤"


def test_leg_meta():
    assert _leg().meta["version"] == "v1.2.3"


def test_ui_helpers_filtering():
    legs = [
        _leg(vehicle="giga07", name="A", day=9),
        _leg(vehicle="giga07", name="B", day=10),
        _leg(vehicle="giga08", name="C", day=9),
    ]
    assert vehicles(legs) == ["giga07", "giga08"]
    assert dates_for_vehicle(legs, "giga07") == [date(2025, 12, 10), date(2025, 12, 9)]
    day_legs = legs_for(legs, "giga07", date(2025, 12, 9))
    assert [l.display_name for l in day_legs] == ["A"]
