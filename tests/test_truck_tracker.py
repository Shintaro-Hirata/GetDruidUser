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


# ---- メトリクス地図（lateral error / acceleration / 自由フィールド）への Truck 適用 ----

from src.queries.specs import LATERAL_ERROR  # noqa: E402
from src.ui.views.map import _remap_to_truck, metric_map_fig  # noqa: E402


def _metric_df():
    # localization 由来の（ズレた）位置に lateral_error イベントが乗っている想定。
    return pd.DataFrame(
        {
            "sec_time": ["2026-02-04T03:34:56Z", "2026-02-04T03:34:59Z"],
            "latitude": [10.0, 10.0],      # わざと Truck と大きく離す
            "longitude": [10.0, 10.0],
            "lateral_error": [0.5, -0.3],
            "cum_dist_km": [1.0, 1.2],
        }
    )


def _truck_for_metric():
    # 03:34:56Z / 03:34:59Z 近傍に Truck 位置がある（JST 12:34:5x をUTCへ）。
    return load_truck_log(SAMPLE_LOG, assume_tz="Asia/Tokyo")


def test_remap_moves_points_to_truck_positions():
    out = _remap_to_truck(_metric_df(), _truck_for_metric())
    assert len(out) == 2
    # 位置が Truck 側（35.68.., 139.76..）に移設され、値は保持される
    assert all(34.0 < v < 36.0 for v in out["latitude"])
    assert list(out["lateral_error"]) == [0.5, -0.3]


def test_remap_keeps_original_when_no_truck_match():
    # この系列に合致する Truck 点が無い（別日など）→ 元位置のまま返す（地図から消さない）
    df = _metric_df().assign(sec_time=["2030-01-01T00:00:00Z", "2030-01-01T00:00:01Z"])
    out = _remap_to_truck(df, _truck_for_metric())
    assert len(out) == 2
    assert all(abs(v - 10.0) < 1e-6 for v in out["latitude"])  # 移設されず元位置


def test_metric_map_replace_keeps_period_without_truck_match():
    # 比較タブ相当: union truck はあるが当該系列に合致が無い場合、replace でも
    # 元位置（Zero-Plotter 由来）で描画し、地図を空にしない。
    df = _metric_df().assign(sec_time=["2030-01-01T00:00:00Z", "2030-01-01T00:00:01Z"])
    fig = metric_map_fig(
        LATERAL_ERROR, [("B", df)], colors={"B": "#ff0000"},
        truck_df=_truck_for_metric(), truck_mode="replace",
    )
    assert fig is not None
    metric_trace = next(t for t in fig.data if t.name == "B")
    assert all(abs(v - 10.0) < 1e-6 for v in metric_trace.lat)


def test_metric_map_replace_uses_truck_positions():
    fig = metric_map_fig(
        LATERAL_ERROR,
        [("A", _metric_df())],
        colors={"A": "#ff0000"},
        color_by="period",
        truck_df=_truck_for_metric(),
        truck_mode="replace",
    )
    assert fig is not None
    # 置換後の点は Truck 位置（元の 10,10 ではない）
    assert all(34.0 < v < 36.0 for v in fig.data[0].lat)


def test_metric_map_overlay_adds_truck_track():
    fig = metric_map_fig(
        LATERAL_ERROR,
        [("A", _metric_df())],
        colors={"A": "#ff0000"},
        color_by="period",
        truck_df=_truck_for_metric(),
        truck_mode="overlay",
    )
    names = [t.name for t in fig.data]
    assert "Truck Tracker (GNSS/INS)" in names
    # 重畳ではイベント点は元位置のまま（10,10）
    metric_trace = next(t for t in fig.data if t.name == "A")
    assert all(abs(v - 10.0) < 1e-6 for v in metric_trace.lat)


def test_metric_map_without_truck_unchanged():
    fig = metric_map_fig(LATERAL_ERROR, [("A", _metric_df())], colors={"A": "#ff0000"})
    assert [t.name for t in fig.data] == ["A"]


def test_remap_handles_mixed_datetime_resolutions():
    # merge_asof は結合キーの解像度一致が必須。sec_time=us / truck ts=ns の混在でも落ちないこと。
    metric = pd.DataFrame(
        {
            "sec_time": pd.to_datetime(
                ["2026-02-04T03:34:56Z", "2026-02-04T03:34:59Z"], utc=True
            ).as_unit("us"),
            "latitude": [10.0, 10.0],
            "longitude": [10.0, 10.0],
            "lateral_error": [0.1, 0.2],
            "cum_dist_km": [1.0, 1.1],
        }
    )
    truck = pd.DataFrame(
        {
            "ts": pd.to_datetime(
                ["2026-02-04T03:34:55Z", "2026-02-04T03:34:58Z"], utc=True
            ).as_unit("ns"),
            "lat": [35.68, 35.69],
            "lon": [139.76, 139.77],
        }
    )
    out = _remap_to_truck(metric, truck)  # 修正前は MergeError
    assert len(out) == 2
    assert all(34.0 < v < 36.0 for v in out["latitude"])
