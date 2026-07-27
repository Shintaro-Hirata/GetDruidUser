# tests/test_csv_periods.py
# mcap CSV 期間取り込み（src/services/csv_periods.py）のテスト。
from __future__ import annotations

import datetime as dt

import pandas as pd

from src.domain.models import CustomField, ExcludeRange, RunConfig
from src.services.csv_periods import (
    CsvPeriodEntry,
    build_csv_periods,
    build_hist_df,
    guess_value_column,
    read_mcap_csv,
)

JST = dt.timezone(dt.timedelta(hours=9))
T0 = dt.datetime(2026, 7, 1, 20, 40, 0, tzinfo=JST)
T0_NS = int(T0.timestamp() * 1e9)

CONTROL_CSV = "20260701_t2_control_debug.csv"
STATE_CSV = "20260701_t2_system_state_manager_state.csv"


def _csv_bytes(col: str, values, t0_ns: int = T0_NS, step_s: float = 1.0) -> bytes:
    rows = ["time_jst,t_sec,t_ns," + col]
    for i, v in enumerate(values):
        t_ns = t0_ns + int(i * step_s * 1e9)
        rows.append(f"x,{i * step_s},{t_ns},{v}")
    return ("\n".join(rows) + "\n").encode("utf-8-sig")


def _state_bytes(states, t0_ns: int = T0_NS) -> bytes:
    return _csv_bytes("system_state", states, t0_ns=t0_ns)


def _config(**kw) -> RunConfig:
    defaults = dict(vehicle_id="giga09", split_minutes=60, thresholds={"q1": 0.2, "q2": 1.0})
    defaults.update(kw)
    return RunConfig(**defaults)


def test_read_mcap_csv_parses_t_ns():
    df = read_mcap_csv(_csv_bytes("debug_for_mcap.lateral_error", [0.1, 0.2]))
    assert list(df["t_ns"]) == [T0_NS, T0_NS + 10**9]
    assert str(df["sec_time"].dt.tz) == "UTC"


def test_guess_value_column_matches_suffix():
    df = read_mcap_csv(_csv_bytes("debug_for_mcap.lateral_error", [0.1]))
    assert guess_value_column(df, "lateral_error") == "debug_for_mcap.lateral_error"


def test_q1_metric_period_with_state_filter():
    # 3分間 1Hz。1分目 |0.5|、2分目 |0.9|、3分目 |1.5| だが 3分目は手動運転。
    # T0 は 202605a 以降なので自動運転 kAutonomousDriving=16 (4 は kControlOk)。
    values = [0.5] * 60 + [-0.9] * 60 + [1.5] * 60
    states = [16] * 120 + [4] * 60
    files = {
        CONTROL_CSV: _csv_bytes("debug_for_mcap.lateral_error", values),
        STATE_CSV: _state_bytes(states),
    }
    entries = [CsvPeriodEntry(label="P1", file_name=CONTROL_CSV, target="q1")]
    periods, warnings = build_csv_periods(entries, files, _config())
    assert not warnings
    assert len(periods) == 1
    p = periods[0]
    assert p.meta["source"] == "mcap_csv"
    df = p.combined_metric_df("q1")
    assert list(df.columns) == ["win_1m", "sec_time", "latitude", "longitude",
                                "lateral_error", "abs_lateral_error", "cum_dist_km"]
    # 手動運転の3分目は除外され、1分窓 2 行
    assert len(df) == 2
    assert sorted(df["abs_lateral_error"]) == [0.5, 0.9]
    assert df["lateral_error"].iloc[1] == -0.9  # 符号は保持
    assert df["cum_dist_km"].isna().all()
    # 期間の時間範囲は CSV の実データ範囲
    assert p.range.start == T0.astimezone(dt.timezone.utc).replace(tzinfo=dt.timezone.utc)


def test_threshold_filters_small_values():
    values = [0.05] * 60 + [0.6] * 60  # 1分目はしきい値 0.2 未満
    files = {CONTROL_CSV: _csv_bytes("debug_for_mcap.lateral_error", values)}
    entries = [CsvPeriodEntry(label="P", file_name=CONTROL_CSV, target="q1")]
    periods, warnings = build_csv_periods(entries, files, _config(), state_filter=False)
    df = periods[0].combined_metric_df("q1")
    assert len(df) == 1
    assert df["abs_lateral_error"].iloc[0] == 0.6


def test_custom_field_metric_and_hist():
    cf = CustomField(key="cf1", label="ヨーレート", table="t2_x", column=".foo.yaw_rate",
                     agg_mode="metric", threshold=0.0, hist_bin=0.5, scale=2.0)
    values = [0.4] * 60 + [-0.6] * 60
    states = [16] * 60 + [4] * 60
    files = {
        "a_yaw.csv": _csv_bytes("foo.yaw_rate", values),
        STATE_CSV: _state_bytes(states),
    }
    entries = [CsvPeriodEntry(label="P", file_name="a_yaw.csv", target="cf1")]
    periods, warnings = build_csv_periods(entries, files, _config(custom_fields=(cf,)))
    assert not warnings
    df = periods[0].combined_custom_df("cf1")
    assert len(df) == 1  # 自動運転は1分目のみ
    assert df["value"].iloc[0] == 0.8  # scale=2.0 適用
    hist = periods[0].combined_custom_hist_df("cf1")
    # 自動: 0.8 (bin 0.5), 手動: -1.2 (bin -1.5)
    assert hist["cnt_auto"].sum() == 60
    assert hist["cnt_manual"].sum() == 60
    auto_bin = hist[hist["cnt_auto"] > 0].iloc[0]
    assert auto_bin["bin_start"] == 0.5
    manual_bin = hist[hist["cnt_manual"] > 0].iloc[0]
    assert manual_bin["bin_start"] == -1.5


def test_custom_field_timeseries():
    cf = CustomField(key="cf1", label="速度", table="t", column=".v", agg_mode="timeseries")
    files = {"v.csv": _csv_bytes("v", [10.0, 20.0, 30.0])}
    entries = [CsvPeriodEntry(label="P", file_name="v.csv", target="cf1")]
    periods, _ = build_csv_periods(entries, files, _config(custom_fields=(cf,)),
                                   state_filter=False)
    df = periods[0].combined_custom_df("cf1")
    assert list(df["value"]) == [10.0, 20.0, 30.0]
    assert "sec_time" in df.columns


def test_same_label_merges_into_one_period():
    files = {
        CONTROL_CSV: _csv_bytes("debug_for_mcap.lateral_error", [0.5] * 60),
        "accel.csv": _csv_bytes("debug_for_mcap.acceleration", [1.5] * 60),
    }
    entries = [
        CsvPeriodEntry(label="P", file_name=CONTROL_CSV, target="q1"),
        CsvPeriodEntry(label="P", file_name="accel.csv", target="q2"),
    ]
    periods, _ = build_csv_periods(entries, files, _config(), state_filter=False)
    assert len(periods) == 1
    assert not periods[0].combined_metric_df("q1").empty
    assert not periods[0].combined_metric_df("q2").empty


def test_unknown_column_warns_and_skips():
    files = {CONTROL_CSV: _csv_bytes("something_else,extra", ["0.5,1.0"] * 3)}
    entries = [CsvPeriodEntry(label="P", file_name=CONTROL_CSV, target="q1",
                              column="not_a_column")]
    periods, warnings = build_csv_periods(entries, files, _config(), state_filter=False)
    assert periods == []
    assert any("値列を特定できません" in w for w in warnings)


def test_excludes_drop_rows():
    values = [0.5] * 120
    ex = ExcludeRange(start=T0, end=T0 + dt.timedelta(minutes=1))
    files = {CONTROL_CSV: _csv_bytes("debug_for_mcap.lateral_error", values)}
    entries = [CsvPeriodEntry(label="P", file_name=CONTROL_CSV, target="q1")]
    periods, _ = build_csv_periods(entries, files, _config(excludes=(ex,)),
                                   state_filter=False)
    df = periods[0].combined_metric_df("q1")
    assert len(df) == 1  # 除外された1分目が消え、2分目だけ残る


def test_hist_df_empty_inputs():
    hist = build_hist_df(pd.Series(dtype=float), pd.Series(dtype=float), 0.5)
    assert hist.empty or hist["cnt_auto"].sum() == 0
