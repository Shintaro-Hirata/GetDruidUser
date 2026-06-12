# tests/test_queries.py
from datetime import datetime

import pytest

from src.queries import (
    ExcludeRange,
    build_query1,
    build_query2,
    build_query3,
)

START = "2025-12-09T01:00:00+09:00"
END = "2025-12-09T02:00:00+09:00"

EXCLUDES = (
    ExcludeRange(
        start=datetime.fromisoformat("2025-12-09T01:10:00+09:00"),
        end=datetime.fromisoformat("2025-12-09T01:20:00+09:00"),
    ),
    ExcludeRange(
        start=datetime.fromisoformat("2025-12-09T01:30:00+09:00"),
        end=datetime.fromisoformat("2025-12-09T01:40:00+09:00"),
    ),
)


def _assert_no_leftover_placeholders(sql: str):
    for ph in ("{vehicle_id}", "{start_time}", "{end_time}", "{thr_lat}", "{thr_acc}",
               "{exclude_ctrl}", "{exclude_state}", "{exclude_pose}", "{distance_cte}",
               "{state_condition}", "{metric_col}"):
        assert ph not in sql, f"placeholder {ph} remained"


def test_build_query1_latlon_basic():
    sql = build_query1(
        vehicle_id="giga07", start_time=START, end_time=END,
        thr_lat=0.2, dist_mode="latlon",
    )
    _assert_no_leftover_placeholders(sql)
    assert "'giga07'" in sql
    assert f"'{START}'" in sql and f"'{END}'" in sql
    assert ".debug_for_mcap.lateral_error" in sql
    assert "ABS(\".debug_for_mcap.lateral_error\") >= 0.2" in sql
    # latlon 距離CTE（Haversine）
    assert "distance mode=latlon" in sql
    assert "ASIN(" in sql
    # system_state=4 で自動運転に限定
    assert "s.system_state = 4" in sql


def test_build_query2_speed_mode():
    sql = build_query2(
        vehicle_id="giga07", start_time=START, end_time=END,
        thr_acc=1.0, dist_mode="speed",
    )
    _assert_no_leftover_placeholders(sql)
    assert ".debug_for_mcap.acceleration" in sql
    assert "distance mode=speed" in sql
    assert "t2_localization_compositor_pose" in sql
    assert ".pose.poslv_speed" in sql


def test_build_query1_with_excludes_in_all_sections():
    sql = build_query1(
        vehicle_id="giga07", start_time=START, end_time=END,
        thr_lat=0.2, dist_mode="latlon", excludes=EXCLUDES,
    )
    _assert_no_leftover_placeholders(sql)
    # 2範囲の除外がOR結合で入る
    assert sql.count("2025-12-09T01:10:00+09:00") >= 1
    assert "AND NOT (" in sql
    # 距離CTE側にも本体側にも除外が入る（完全除外）
    # → AND NOT の出現回数が2以上（pos_1s / per_sec / state_per_sec）
    assert sql.count("AND NOT (") >= 3


def test_build_query1_without_excludes_has_no_not_clause():
    sql = build_query1(
        vehicle_id="giga07", start_time=START, end_time=END,
        thr_lat=0.2, dist_mode="latlon",
    )
    assert "AND NOT (" not in sql


def test_build_query3_state_condition_and_pose_excludes():
    sql = build_query3(
        vehicle_id="giga07", start_time=START, end_time=END,
        state_condition="s.system_state = 4", excludes=EXCLUDES,
    )
    _assert_no_leftover_placeholders(sql)
    assert "t2_positioning_driver_pose" in sql
    assert ".pose.linear_acceleration_vrf.y" in sql
    assert "s.system_state = 4" in sql
    # pose 側の除外は p.__time に対して入る
    assert "p.__time >=" in sql


def test_build_query_unknown_dist_mode_raises():
    with pytest.raises(ValueError):
        build_query1(
            vehicle_id="giga07", start_time=START, end_time=END,
            thr_lat=0.2, dist_mode="unknown",  # type: ignore[arg-type]
        )
