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


def _mcap_csv_multi(n=120) -> bytes:
    """値列を2つ持つ GetMcapToCsv 形式 CSV（1つの CSV から複数指標の置き換え用）"""
    t0_ns = int(pd.Timestamp(T0).value)
    rows = ["time_jst,t_sec,t_ns,debug_for_mcap.lateral_error,pose.acceleration_vrf.y"]
    for i in range(n):
        t_ns = t0_ns + i * 500_000_000
        rows.append(f"x,{i * 0.5},{t_ns},{(i % 40) * 0.1:.3f},{(i % 20) * 0.05:.3f}")
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


def test_read_value_csv_generic_multi_value_columns():
    # 汎用形式は 2 列目以降の全列を値列として保持する（複数指標の置き換え用）
    t0 = pd.Timestamp(T0).timestamp()
    body = "t,a,b\n" + "\n".join(f"{t0 + i},{i * 0.1},{i * 0.2}" for i in range(10))
    df = read_value_csv(body.encode())
    assert {"t_ns", "sec_time", "a", "b"}.issubset(df.columns)
    assert df["a"].iloc[1] == pytest.approx(0.1)
    assert df["b"].iloc[1] == pytest.approx(0.2)


def test_one_csv_replaces_multiple_targets():
    # 1つの CSV から複数の指標（q1 と クエリ3 ヒスト）を同じ期間へ置き換えられる
    period = _period()
    config = _config()
    df = read_value_csv(_mcap_csv_multi())
    e1 = OverrideEntry(file_name="a.csv", target="q1",
                       column="debug_for_mcap.lateral_error")
    e2 = OverrideEntry(file_name="a.csv", target=TARGET_Q3,
                       column="pose.acceleration_vrf.y")
    apply_override(period, e1, df, config, state_df=None,
                   positions=None, drive_mode="auto")
    apply_override(period, e2, df, config, state_df=None,
                   positions=None, drive_mode="auto")
    assert "metric:q1" in period.overrides
    assert "hist" in period.overrides
    assert not period.combined_metric_df("q1").empty
    assert period.combined_hist_df()["cnt_auto"].sum() > 0


def test_apply_rows_multiple_targets_from_one_csv():
    # UI 経路 (_apply_rows) でも 1 CSV → 複数指標が適用され、レシピも指標ごとに残る
    from types import SimpleNamespace
    from src.ui.views.override_panel import _apply_rows

    period = _period()
    config = _config()
    files = {"a.csv": _mcap_csv_multi()}
    sb = SimpleNamespace(truck_sources=(), truck_tz="Asia/Tokyo", truck_filter_vehicle=False)
    state = SimpleNamespace(ovr_recipes=[])
    rows = [
        (OverrideEntry(file_name="a.csv", target="q1",
                       column="debug_for_mcap.lateral_error"), "", "auto"),
        (OverrideEntry(file_name="a.csv", target=TARGET_Q3,
                       column="pose.acceleration_vrf.y"), "", "auto"),
    ]
    applied, warns = _apply_rows(rows, files, {}, [period], sb, config, state)
    assert applied == 2
    assert "metric:q1" in period.overrides and "hist" in period.overrides
    assert len(state.ovr_recipes) == 2  # 期間×対象ごとに1件


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
    warns = apply_override(period, entry, df, config, state_df=None,
                           positions=None, drive_mode="auto")
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
    warns = apply_override(period, entry, df, config, state_df=None,
                           positions=None, drive_mode="auto")
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
    apply_override(period, entry, df, config, state_df=None,
                   positions=None, drive_mode="auto")
    hist = period.combined_hist_df()
    assert {"bin_start", "bin_end", "cnt_auto", "cnt_manual"}.issubset(hist.columns)
    assert hist["cnt_auto"].sum() > 0
    assert hist["cnt_manual"].sum() == 0  # drive_mode=auto → 全て自動


def test_apply_override_custom_timeseries_mean_per_second():
    cf = CustomField(key="cf1", label="てすと", table="t", column=".x.y",
                     agg_mode="timeseries")
    period = _period()
    config = _config(custom_fields=(cf,))
    df = read_value_csv(_mcap_csv(col="x.y"))  # 0.5秒周期 → 1秒2点
    entry = OverrideEntry(file_name="a.csv", target="cf1", column="x.y")
    apply_override(period, entry, df, config, state_df=None,
                   positions=None, drive_mode="auto")
    out = period.combined_custom_df("cf1")
    assert not out.empty
    # 1秒1行 (平均) になっている
    assert out["sec_time"].is_unique
    # 2点の平均: (v0 + v1) / 2
    raw = read_value_csv(_mcap_csv(col="x.y"))
    expect = raw.groupby("sec_time")["x.y"].mean().iloc[0]
    assert out["value"].iloc[0] == pytest.approx(expect)
    # BQ の timeseries と同形: 地図用の緯度経度列を持つ (CSV 由来なので NaN)
    assert {"latitude", "longitude"}.issubset(out.columns)
    # timeseries でも自動/手動ヒストが作られる (BQ パイプラインと同じ)
    hist = period.combined_custom_hist_df("cf1")
    assert not hist.empty
    assert hist["cnt_auto"].sum() > 0


def test_timeseries_positions_fill_latlon_for_map():
    cf = CustomField(key="cf1", label="てすと", table="t", column=".x.y",
                     agg_mode="timeseries")
    period = _period()
    config = _config(custom_fields=(cf,))
    df = read_value_csv(_mcap_csv(col="x.y"))
    ts = [pd.Timestamp(T0) + pd.Timedelta(seconds=i) for i in range(60)]
    truck = pd.DataFrame({"ts": ts, "lat": [35.0 + i * 1e-4 for i in range(60)],
                          "lon": [139.0] * 60})
    pos = prepare_positions(truck)
    entry = OverrideEntry(file_name="a.csv", target="cf1", column="x.y")
    apply_override(period, entry, df, config, state_df=None,
                   positions=pos, drive_mode="auto")
    out = period.combined_custom_df("cf1")
    assert out["latitude"].notna().all()   # 地図に載る
    assert out["cum_dist_km"].notna().all()  # 距離X軸も使える


def test_drive_mode_state_without_state_is_not_applied():
    # BQ 欠損期間の穴埋めでは state も無い。勝手に自動と決めつけず、適用を見送る
    # （手動収集の走行が「自動」に逆転する不具合の防止）。
    period = _period()
    config = _config()
    df = read_value_csv(_mcap_csv(col="pose.acceleration_vrf.y"))
    entry = OverrideEntry(file_name="a.csv", target=TARGET_Q3,
                          column="pose.acceleration_vrf.y")
    warns = apply_override(period, entry, df, config, state_df=None,
                           positions=None, drive_mode="state")
    assert not period.overrides
    assert any("state が取得できません" in w for w in warns)


def test_drive_mode_manual_puts_all_in_manual_bucket():
    # 全て手動運転の走行: manual を選べば手動バケットに入り、自動バケットは空
    period = _period()
    config = _config()
    df = read_value_csv(_mcap_csv(col="pose.acceleration_vrf.y"))
    entry = OverrideEntry(file_name="a.csv", target=TARGET_Q3,
                          column="pose.acceleration_vrf.y")
    apply_override(period, entry, df, config, state_df=None,
                   positions=None, drive_mode="manual")
    hist = period.combined_hist_df()
    assert hist["cnt_manual"].sum() > 0
    assert hist["cnt_auto"].sum() == 0


def test_drive_mode_manual_metric_scatter_is_empty():
    # 散布図 (q1/q2) は自動運転のみ。全手動なら 0 点 (BQ と同じ挙動)
    period = _period()
    config = _config()
    df = read_value_csv(_mcap_csv())
    entry = OverrideEntry(file_name="a.csv", target="q1")
    warns = apply_override(period, entry, df, config, state_df=None,
                           positions=None, drive_mode="manual")
    assert not period.overrides  # 自動 0 行 → 空 → 適用されず
    assert any("0 件" in w for w in warns)


def test_positions_from_period_used_as_fallback():
    # Truck が無くても、同一期間の他 DF が持つ緯度経度を位置ソースに使える
    from src.services.metric_override import positions_from_period
    period = _period()
    # 既存 q1 DF に緯度経度入りの行を持たせる (BQ 由来を模擬)
    secs = pd.date_range(T0, periods=60, freq="1s", tz="UTC")
    period.chunks[0].metric_dfs["q1"] = pd.DataFrame({
        "win_1m": secs.floor("min"), "sec_time": secs,
        "latitude": [35.0 + i * 1e-4 for i in range(60)],
        "longitude": [139.0] * 60,
        "lateral_error": [0.1] * 60, "abs_lateral_error": [0.1] * 60,
        "cum_dist_km": [float("nan")] * 60,
    })
    pos = positions_from_period(period)
    assert pos is not None and not pos.empty
    assert pos["cum_dist_km"].iloc[-1] > 0

    cf = CustomField(key="cf1", label="t", table="t", column=".x.y", agg_mode="timeseries")
    config = _config(custom_fields=(cf,))
    df = read_value_csv(_mcap_csv(col="x.y"))
    apply_override(period, OverrideEntry(file_name="a.csv", target="cf1", column="x.y"),
                   df, config, state_df=None, positions=pos, drive_mode="auto")
    out = period.combined_custom_df("cf1")
    assert out["latitude"].notna().any()  # 他指標の位置が結び付いた


def test_apply_override_state_mask_splits_hist():
    period = _period()
    config = _config()
    df = read_value_csv(_mcap_csv(col="pose.acceleration_vrf.y"))
    # 前半は自動(16=202605a以降の kAutonomousDriving)、後半は手動(0) の state 系列
    half_ns = int(df["t_ns"].iloc[len(df) // 2])
    state = pd.DataFrame({
        "t_ns": df["t_ns"],
        "system_state": [16 if t <= half_ns else 0 for t in df["t_ns"]],
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


def test_apply_rows_reapply_does_not_raise():
    # 2回目の適用 (既に overrides がある状態) で dict比較が DataFrame == にならないこと
    # (ValueError: The truth value of a DataFrame is ambiguous の回帰防止)
    from types import SimpleNamespace
    from src.ui.views.override_panel import _apply_rows

    period = _period()
    config = _config()
    files = {"a.csv": _mcap_csv()}
    sb = SimpleNamespace(truck_sources=(), truck_tz="Asia/Tokyo", truck_filter_vehicle=False)
    state = SimpleNamespace(ovr_recipes=[])
    rows = [(OverrideEntry(file_name="a.csv", target="q1"), "", "auto")]

    a1, w1 = _apply_rows(rows, files, {}, [period], sb, config, state)
    a2, w2 = _apply_rows(rows, files, {}, [period], sb, config, state)  # 再適用
    assert a1 == 1 and a2 == 1
    assert len(state.ovr_recipes) == 1  # 同じ期間×対象は上書き (重複しない)


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
