# tests/test_truck_tracker.py
import io

import pandas as pd

from src.services.truck_tracker import load_truck_log, parse_line, vehicle_number
from src.ui.views.zero_plotter import zp_track_fig

# gnss_receiver.py / flask_mqtt_server.py が実際に書き出す形式: "<datetime>: <python-dict-repr>"
# 位置行 / status 行 / performance_metrics 行が混在する。
SAMPLE_LOG = "\n".join(
    [
        "2026/02/04 12:34:56.005000: {'truck-id': 't2-isuzugiga-9', 'lat': 35.6811, 'lon': 139.7671, 'speed': 12.3, 'datetime': '2026/02/04 12:34:56.005000'}",
        "2026/02/04 12:34:57.001000: {'truck-id': 't2-isuzugiga-9', 'status': {'general_status_C': {'GeneralStatus-C': 4}}, 'datetime': '2026/02/04 12:34:57.001000'}",
        "2026/02/04 12:34:58.002000: {'truck-id': 't2-isuzugiga-9', 'performance_metrics': {'NorthPositionRMSError': 0.1}, 'datetime': '2026/02/04 12:34:58.002000'}",
        "2026/02/04 12:34:59.003000: {'truck-id': 't2-isuzugiga-9', 'lat': 35.6822, 'lon': 139.7682, 'speed': 11.0, 'datetime': '2026/02/04 12:34:59.003000'}",
        "broken line without colon-space payload",
        "2026/02/04 12:35:00.000000: {not: a, valid: dict}",
    ]
)


def test_vehicle_number_variants():
    assert vehicle_number("giga07") == 7
    assert vehicle_number("giga09") == 9
    assert vehicle_number("t2-isuzugiga-9") == 9
    assert vehicle_number("truck_t2-isuzugiga-9_2026-02-04.log") == 9
    assert vehicle_number(None) is None


def test_parse_line_only_position_rows():
    lines = SAMPLE_LOG.splitlines()
    assert parse_line(lines[0])["lat"] == 35.6811  # 位置行
    assert parse_line(lines[1]) is None            # status 行（lat/lon 無し）
    assert parse_line(lines[2]) is None            # performance_metrics 行
    assert parse_line("broken line") is None


def test_load_truck_log_extracts_positions_only():
    df = load_truck_log(SAMPLE_LOG, assume_tz="UTC")
    assert list(df.columns) == ["ts", "lat", "lon", "speed", "truck_id", "vehicle_num"]
    assert len(df) == 2
    assert df["lat"].tolist() == [35.6811, 35.6822]
    assert df["speed"].tolist() == [12.3, 11.0]
    assert df["vehicle_num"].tolist() == [9, 9]


def test_load_truck_log_timezone_interpretation():
    utc = load_truck_log(SAMPLE_LOG, assume_tz="UTC")
    assert str(utc["ts"].dt.tz) == "UTC"
    assert utc["ts"].iloc[0].hour == 12
    # Asia/Tokyo として解釈すると UTC では -9h
    jst = load_truck_log(SAMPLE_LOG, assume_tz="Asia/Tokyo")
    assert jst["ts"].iloc[0].hour == 3


def test_load_truck_log_vehicle_filter_and_fallback():
    assert len(load_truck_log(SAMPLE_LOG, vehicle_id="giga09", match_vehicle=True)) == 2
    # 一致皆無のときは単一車両ログ想定で全件フォールバック
    assert len(load_truck_log(SAMPLE_LOG, vehicle_id="giga07", match_vehicle=True)) == 2


def test_load_truck_log_time_window():
    start = pd.Timestamp("2026-02-04T12:34:57+09:00")
    end = pd.Timestamp("2026-02-04T12:35:30+09:00")
    df = load_truck_log(SAMPLE_LOG, start=start, end=end, assume_tz="Asia/Tokyo")
    assert len(df) == 1 and df["lat"].iloc[0] == 35.6822


def test_load_truck_log_file_like_reread_with_seek():
    buf = io.BytesIO(SAMPLE_LOG.encode("utf-8"))
    first = load_truck_log([buf])
    second = load_truck_log([buf])  # seek されないと 2 回目は 0 件になる
    assert len(first) == 2 and len(second) == 2


def test_load_truck_log_empty_source():
    df = load_truck_log("")
    assert df.empty and "lat" in df.columns


# ---- Zero-Plotter 地図への Truck 重畳/置換 ----

def _zp_df():
    return pd.DataFrame(
        {
            "sec_time": ["2026-02-04T03:34:56Z", "2026-02-04T03:34:59Z"],
            "system_state": [4, 4],
            "latitude": [35.70, 35.71],
            "longitude": [139.78, 139.79],
        }
    )


def _truck_df():
    return load_truck_log(SAMPLE_LOG, assume_tz="Asia/Tokyo")


def test_zp_fig_overlay_adds_truck_trace():
    fig = zp_track_fig(_zp_df(), truck_df=_truck_df(), truck_mode="overlay")
    assert fig is not None
    names = [t.name for t in fig.data]
    assert "Truck Tracker (GNSS/INS)" in names
    assert "kAutonomousDriving" in names  # zp 点も残る
    assert all(t.type == "scattermap" for t in fig.data)


def test_zp_fig_replace_shows_truck_only():
    fig = zp_track_fig(_zp_df(), truck_df=_truck_df(), truck_mode="replace")
    names = [t.name for t in fig.data]
    assert names == ["Truck Tracker (GNSS/INS)"]


def test_zp_fig_replace_without_truck_falls_back_to_zp():
    fig = zp_track_fig(_zp_df(), truck_df=None, truck_mode="replace")
    names = [t.name for t in fig.data]
    assert "kAutonomousDriving" in names and "Truck Tracker (GNSS/INS)" not in names


def test_zp_fig_truck_only_when_zp_empty():
    fig = zp_track_fig(pd.DataFrame(), truck_df=_truck_df(), truck_mode="overlay")
    assert fig is not None
    assert [t.name for t in fig.data] == ["Truck Tracker (GNSS/INS)"]


def test_zp_fig_truck_customdata_raw_is_iso_for_exclude_selection():
    # 除外編集の selection_sec_times は customdata[0] を isoparse する。
    fig = zp_track_fig(pd.DataFrame(), truck_df=_truck_df(), truck_mode="replace")
    raw0 = fig.data[0].customdata[0][0]
    assert pd.to_datetime(raw0) is not None  # ISO としてパースできる


def test_zp_fig_all_empty_returns_none():
    assert zp_track_fig(pd.DataFrame(), truck_df=None) is None
