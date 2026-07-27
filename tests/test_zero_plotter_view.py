# tests/test_zero_plotter_view.py
# Zero-Plotter 点群ビュー（SQL・図・日付境界）のテスト。
from datetime import datetime, timedelta, timezone

import pandas as pd

from src.queries.builder import Dialect, QueryParams, build_zp_track_query
from src.domain.drive_state import GEN_LEGACY, state_labels
from src.ui.views.zero_plotter import (
    SYSTEM_STATE_COLORS,
    jst_day_bounds,
    zp_track_fig,
)

JST = timezone(timedelta(hours=9))


def test_build_zp_track_query_bq_dialect():
    p = QueryParams(
        vehicle_id="giga07",
        start_time="2025-12-08T15:00:00+00:00",
        end_time="2025-12-09T15:00:00+00:00",
        dialect=Dialect(kind="bq", bq_prefix="t2-integration.zero_plotter"),
    )
    sql = build_zp_track_query(p)
    # 5秒バケット（zero-plotter の granularity duration=5000ms と同じ）
    assert "TIMESTAMP_SECONDS(DIV(UNIX_SECONDS(`#timestamp`), 5) * 5)" in sql
    assert "`t2-integration.zero_plotter.t2_system_state_manager_state`" in sql
    # (5秒バケット × system_state) ごとに doubleAny 相当（ANY_VALUE）で集約
    assert "`:system_state` AS system_state" in sql
    assert "ANY_VALUE(`#latitude`)" in sql and "ANY_VALUE(`#longitude`)" in sql
    assert "ANY_VALUE(`#t2kp`)" in sql
    assert "GROUP BY 1, 2" in sql


def test_build_zp_track_query_druid_dialect():
    p = QueryParams(
        vehicle_id="giga07", start_time="a", end_time="b",
        dialect=Dialect(kind="druid"),
    )
    sql = build_zp_track_query(p)
    assert "TIME_FLOOR(__time, 'PT5S')" in sql
    assert '"t2_system_state_manager_state"' in sql


def test_jst_day_bounds():
    # UTC 2025-12-08 16:30 = JST 2025-12-09 01:30 → JST 12/9 の1日
    dt = datetime(2025, 12, 8, 16, 30, tzinfo=timezone.utc)
    start, end = jst_day_bounds(dt)
    assert start == datetime(2025, 12, 9, 0, 0, tzinfo=JST)
    assert end == datetime(2025, 12, 10, 0, 0, tzinfo=JST)


_LEGACY_LABELS = state_labels(None, GEN_LEGACY)  # 旧世代 (4=kAutonomousDriving) のデータでテスト


def _track_df():
    return pd.DataFrame(
        {
            "sec_time": [
                "2025-12-09T01:00:00Z",
                "2025-12-09T01:00:05Z",
                "2025-12-09T01:00:10Z",
            ],
            "system_state": [4, 0, None],
            "latitude": [35.43, 35.44, 35.45],
            "longitude": [139.62, 139.63, 139.64],
        }
    )


def test_zp_track_fig_colors_match_zero_plotter():
    fig = zp_track_fig(_track_df(), labels=_LEGACY_LABELS)
    assert fig is not None
    by_name = {t.name: t for t in fig.data}
    # zero-plotter の COLOR_MAP_SYSTEM_STATE と同一色
    assert by_name["kAutonomousDriving"].marker.color == "#3d37f9"
    assert by_name["kStandBy"].marker.color == "#ea1e3a"
    assert by_name["null"].marker.color == "#000000"
    assert all(t.type == "scattermap" for t in fig.data)


def test_zp_track_fig_customdata_raw_and_jst():
    fig = zp_track_fig(_track_df(), labels=_LEGACY_LABELS)
    auto = next(t for t in fig.data if t.name == "kAutonomousDriving")
    cd = auto.customdata[0]
    assert cd[0] == "2025-12-09T01:00:00Z"  # 除外選択イベント用の生値
    assert cd[1] == "2025-12-09 10:00:00"   # ホバーはJST
    assert "時刻(JST)" in auto.hovertemplate


def test_zp_track_fig_empty_returns_none():
    assert zp_track_fig(pd.DataFrame()) is None
    assert zp_track_fig(pd.DataFrame({"latitude": [], "longitude": []})) is None


def test_zp_track_fig_without_state_column_falls_back_to_null():
    df = _track_df().drop(columns=["system_state"])
    fig = zp_track_fig(df)
    assert [t.name for t in fig.data] == ["null"]


def test_zp_track_fig_shows_t2kp_in_hover():
    df = _track_df().assign(t2kp=[12.345, 67.0, None])
    fig = zp_track_fig(df, labels=_LEGACY_LABELS)
    auto = next(t for t in fig.data if t.name == "kAutonomousDriving")
    # customdata[2] が t2kp（整形済み文字列）、hovertemplate に t2kp 行
    assert auto.customdata[0][2] == "12.345"
    assert "t2kp: %{customdata[2]}" in auto.hovertemplate


def test_zp_track_fig_without_t2kp_column_shows_dash():
    fig = zp_track_fig(_track_df(), labels=_LEGACY_LABELS)  # t2kp 列なし
    auto = next(t for t in fig.data if t.name == "kAutonomousDriving")
    assert auto.customdata[0][2] == "-"
