# tests/test_metric_override.py
# mcap CSV による指標置き換え（metric_override）のテスト
from __future__ import annotations

import io
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from src.domain.models import CustomField, RunConfig, TimeRange
from src.domain.results import ChunkData, PeriodResult
from src.services.metric_override import (
    TARGET_Q3,
    OverrideEntry,
    apply_override,
    attach_positions,
    choose_period,
    prepare_positions,
    read_value_csv,
)

UTC = timezone.utc
T0 = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)


def _mcap_csv(n=120, col="debug_for_mcap.lateral_error") -> bytes:
    t0_ns = int(pd.Timestamp(T0).value)
    rows = ["time_jst,t_sec,t_ns," + col]
    for i in range(n):
        # 0.5秒周期 (1秒に2点) で値は i に比例
        t_ns = t0_ns + i * 500_000_000
        rows.append(f"x,{i * 0.5},{t_ns},{(i % 40) * 0.1:.3f}")
    return "\n".join(rows).encode("utf-8")


def _two_col_csv() -> bytes:
    rows = ["time,value"]
    for i in range(60):
        ts = pd.Timestamp(T0).tz_convert("Asia/Tokyo") + pd.Timedelta(seconds=i)
        rows.append(f"{ts:%Y-%m-%d %H:%M:%S},{i * 0.01:.3f}")
    return "\n".join(rows).encode("utf-8")


def _period(label="P1", start=T0, minutes=5) -> PeriodResult:
    rng = TimeRange(start=start, end=start + pd.Timedelta(minutes=minutes), label=label)
    chunk = ChunkData(start=rng.start, end=rng.end,
                      metric_dfs={"q1": pd.DataFrame({"win_1m": [], "sec_time": []})})
    return PeriodResult(label=label, range=rng, chunks=[chunk])


def _config(**kw) -> RunConfig:
    return RunConfig(vehicle_id="GIGA09", split_minutes=60, **kw)


def test_read_value_csv_mcap_format():
    df = read_value_csv(_mcap_csv())
    assert {"t_ns", "sec_time"}.issubset(df.columns)
    assert df["sec_time"].dt.tz is not None
    assert len(df) == 120


def test_read_value_csv_two_col_jst():
    df = read_value_csv(_two_col_csv())
    assert len(df) == 60
    # JST 文字列 → UTC 変換されている (12:00 JST = 03:00 UTC ではなく、T0 は UTC 定義なので
    # JST 表示 21:00 → UTC 12:00 に戻ること)
    assert df["sec_time"].iloc[0] == pd.Timestamp(T0)
    assert df.columns[1] == "value"


def test_read_value_csv_two_col_epoch_seconds():
    t0 = pd.Timestamp(T0).timestamp()
    body = "t,v\n" + "\n".join(f"{t0 + i},{i}" for i in range(10))
    df = read_value_csv(body.encode())
    assert df["sec_time"].iloc[0] == pd.Timestamp(T0)


def test_choose_period_by_overlap():
    p1 = _period("P1", T0)
    p2 = _period("P2", T0 + pd.Timedelta(hours=1))
    df = read_value_csv(_mcap_csv())  # T0 から 60 秒
    assert choose_period(df, [p1, p2]).label == "P1"
    assert choose_period(df, [p1, p2], label="P2").label == "P2"


def test_apply_override_metric_replaces_all_tabs_df():
    period = _period()
    config = _config()
    df = read_value_csv(_mcap_csv())
    entry = OverrideEntry(file_name="a.csv", target="q1")
    warns = apply_override(period, entry, df, config, state_df=None, positions=None)
    # state 無し警告 + 位置無し警告
    assert any("自動運転" in w for w in warns)
    out = period.combined_metric_df("q1")
    assert not out.empty
    # 1分窓ごとに1点 (60秒ぶん → 1〜2窓)
    assert out["win_1m"].is_unique
    assert {"lateral_error", "abs_lateral_error"}.issubset(out.columns)
    # 解除で元に戻る
    period.clear_overrides()
    assert period.combined_metric_df("q1").empty


def test_apply_override_all_filtered_is_not_stored():
    period = _period()
    config = _config(thresholds={"q1": 100.0})  # 全部落ちるしきい値
    df = read_value_csv(_mcap_csv())
    entry = OverrideEntry(file_name="a.csv", target="q1", scale=1.0)
    warns = apply_override(period, entry, df, config, state_df=None, positions=None)
    # 0 件の置き換えは適用されず、内訳付きの警告が出る
    assert not period.overrides
    assert any("0 件のため適用しませんでした" in w and "内訳" in w for w in warns)


def test_state_df_from_sql_result_microsecond_unit():
    # BigQuery が datetime64[us] で返しても ns の t_ns になること
    # (µs のままだと時刻突き合わせが全て外れ、置き換えが 0 件になる回帰の防止)
    from src.services.metric_override import state_df_from_sql_result
    from src.services.csv_periods import attach_auto_mask

    sec = pd.date_range(T0, periods=60, freq="1s", tz="UTC").as_unit("us")
    raw = pd.DataFrame({"sec_time": sec, "system_state": [4] * 60})
    state = state_df_from_sql_result(raw)
    assert state is not None
    assert state["t_ns"].iloc[0] == pd.Timestamp(T0).value  # ns スケール

    df = read_value_csv(_mcap_csv())
    mask = attach_auto_mask(df, state)
    assert mask.all()  # 全行 state=4 なので全て自動扱いになる


def test_apply_override_q3_hist():
    period = _period()
    config = _config()
    df = read_value_csv(_mcap_csv(col="pose.acceleration_vrf.y"))
    entry = OverrideEntry(file_name="a.csv", target=TARGET_Q3,
                          column="pose.acceleration_vrf.y")
    apply_override(period, entry, df, config, state_df=None, positions=None)
    hist = period.combined_hist_df()
    assert {"bin_start", "bin_end", "cnt_auto", "cnt_manual"}.issubset(hist.columns)
    assert hist["cnt_auto"].sum() > 0
    assert hist["cnt_manual"].sum() == 0  # state 無し → 全て自動扱い


def test_apply_override_custom_timeseries_mean_per_second():
    cf = CustomField(key="cf1", label="てすと", table="t", column=".x.y",
                     agg_mode="timeseries")
    period = _period()
    config = _config(custom_fields=(cf,))
    df = read_value_csv(_mcap_csv(col="x.y"))  # 0.5秒周期 → 1秒2点
    entry = OverrideEntry(file_name="a.csv", target="cf1", column="x.y")
    apply_override(period, entry, df, config, state_df=None, positions=None)
    out = period.combined_custom_df("cf1")
    assert not out.empty
    # 1秒1行 (平均) になっている
    assert out["sec_time"].is_unique
    # 2点の平均: (v0 + v1) / 2
    raw = read_value_csv(_mcap_csv(col="x.y"))
    expect = raw.groupby("sec_time")["x.y"].mean().iloc[0]
    assert out["value"].iloc[0] == pytest.approx(expect)


def test_apply_override_state_mask_splits_hist():
    period = _period()
    config = _config()
    df = read_value_csv(_mcap_csv(col="pose.acceleration_vrf.y"))
    # 前半は自動(4)、後半は手動(0) の state 系列
    half_ns = int(df["t_ns"].iloc[len(df) // 2])
    state = pd.DataFrame({
        "t_ns": df["t_ns"],
        "system_state": [4 if t <= half_ns else 0 for t in df["t_ns"]],
    })
    entry = OverrideEntry(file_name="a.csv", target=TARGET_Q3,
                          column="pose.acceleration_vrf.y")
    apply_override(period, entry, df, config, state_df=state, positions=None)
    hist = period.combined_hist_df()
    assert hist["cnt_auto"].sum() > 0
    assert hist["cnt_manual"].sum() > 0


def test_positions_join_and_cumdist():
    # 10秒ぶん、北向きに一定速度で進む truck 位置
    ts = [pd.Timestamp(T0) + pd.Timedelta(seconds=i) for i in range(10)]
    truck = pd.DataFrame({
        "ts": ts,
        "lat": [35.0 + i * 1e-4 for i in range(10)],
        "lon": [139.0] * 10,
    })
    pos = prepare_positions(truck)
    assert pos is not None
    assert pos["cum_dist_km"].iloc[-1] > 0

    out = pd.DataFrame({
        "win_1m": [pd.Timestamp(T0).floor("min")],
        "sec_time": [pd.Timestamp(T0) + pd.Timedelta(seconds=5)],
        "latitude": [np.nan], "longitude": [np.nan],
        "v": [1.0], "abs_v": [1.0], "cum_dist_km": [np.nan],
    })
    joined = attach_positions(out, pos)
    assert joined["latitude"].notna().all()
    assert joined["cum_dist_km"].notna().all()


def test_out_of_period_rows_are_dropped():
    period = _period(minutes=5)
    config = _config()
    # 期間の 10 分後から始まる CSV → 重なりゼロ
    late = _mcap_csv()
    df = read_value_csv(late)
    df["sec_time"] = df["sec_time"] + pd.Timedelta(minutes=10)
    df["t_ns"] = df["t_ns"] + 10 * 60 * 1_000_000_000
    entry = OverrideEntry(file_name="a.csv", target="q1")
    warns = apply_override(period, entry, df, config, state_df=None, positions=None)
    assert any("重なる行がありません" in w for w in warns)
    assert not period.overrides
