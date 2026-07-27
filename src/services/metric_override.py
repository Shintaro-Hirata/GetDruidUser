# src/services/metric_override.py
# 取得済みの期間 (PeriodResult) の特定指標を、mcap 由来 CSV の値で「置き換える」。
# BQ にデータが欠けている指標だけを mcap の生値で穴埋めし、既存の全タブ
# （散布図/地図/表/ヒストグラム/比較）でそのまま比較できるようにする。
#
# csv_periods.py（CSV を新しい期間として"追加"する）とは役割が異なり、
# こちらは既存期間の中身を差し替える。集計は現行パイプラインと同一:
#   - metric 系 (q1/q2/自由 metric): 秒丸め → 自動運転に絞り → 1分窓の |最大| 1点
#   - 自由 timeseries: 1秒平均そのまま（フィルタなし）
#   - ヒスト (q3/自由): 自動/手動別の分布
# 位置 (緯度経度・累積距離) は CSV に無いため Truck Tracker の位置を時刻で結合する。
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from src.domain.drive_state import auto_note, auto_state_value
from src.domain.models import CustomField, RunConfig
from src.domain.results import PeriodResult
from src.domain.x_axis import aware_utc
from src.queries.builder import (
    Q3_HIST_BASE_BIN,
    Dialect,
    QueryParams,
    build_state_series_query,
)
from src.queries.specs import METRICS, MetricSpec
from src.services.csv_periods import (
    apply_excludes,
    attach_auto_mask,
    build_hist_df,
    build_metric_df,
    build_timeseries_df,
    guess_value_column,
    load_state_series,
    read_mcap_csv,
)

TARGET_Q3 = "q3"  # 横G ヒストグラム (MetricSpec ではないので専用キー)


@dataclass(frozen=True)
class OverrideEntry:
    """CSV 1ファイル分の置き換え指定"""
    file_name: str
    target: str            # "q1" | "q2" | TARGET_Q3 | CustomField.key
    column: str = ""       # 値列名 (空なら自動推定)
    scale: float = 1.0
    offset: float = 0.0
    period_label: str = ""  # 適用先期間。空なら時刻の重なりで自動判定


# ------------------------------------------------------------------
# CSV 読み込み (GetMcapToCsv 形式 + 2列形式)
# ------------------------------------------------------------------
def _parse_time_column(t: pd.Series) -> pd.Series:
    """2列形式の時間列を UTC の Timestamp に変換する。

    epoch 数値 (秒/ミリ/マイクロ/ナノを桁で判別) と日時文字列 (タイムゾーン無しは
    JST とみなす) の両方を受け付ける。
    """
    num = pd.to_numeric(t, errors="coerce")
    if num.notna().sum() >= max(1, int(len(t) * 0.9)):
        v = num.astype("float64")
        med = float(v.dropna().abs().median())
        if med >= 1e17:      # ナノ秒
            ns = v
        elif med >= 1e14:    # マイクロ秒
            ns = v * 1e3
        elif med >= 1e11:    # ミリ秒
            ns = v * 1e6
        else:                # 秒
            ns = v * 1e9
        return pd.to_datetime(ns, utc=True, errors="coerce")
    ts = pd.to_datetime(t, errors="coerce")
    try:
        if ts.dt.tz is None:
            ts = ts.dt.tz_localize("Asia/Tokyo")
    except (TypeError, AttributeError):
        pass
    return ts.dt.tz_convert("UTC")


def read_value_csv(data: bytes) -> pd.DataFrame:
    """置き換え用 CSV を読む。

    - GetMcapToCsv の出力 (t_ns 列あり) はそのまま (基本形式)
    - それ以外は「1列目=時間、2列目=値」の2列形式とみなす
    戻り値はどちらも sec_time (UTC 秒精度) と t_ns を持つ。
    """
    head = pd.read_csv(io.BytesIO(data), encoding="utf-8-sig", nrows=1)
    if "t_ns" in head.columns:
        return read_mcap_csv(data)

    df = pd.read_csv(io.BytesIO(data), encoding="utf-8-sig")
    if df.shape[1] < 2:
        raise ValueError("2列形式 (時間, 値) には最低2列必要です")
    ts = _parse_time_column(df.iloc[:, 0]).dt.as_unit("ns")  # int64 化を ns に固定
    keep = ts.notna()
    if not keep.any():
        raise ValueError("時間列 (1列目) を解釈できません")
    out = pd.DataFrame({
        "t_ns": ts[keep].astype("int64"),
        str(df.columns[1]): pd.to_numeric(df.iloc[:, 1], errors="coerce")[keep],
        "sec_time": ts[keep].dt.floor("s"),
    })
    return out.sort_values("t_ns").reset_index(drop=True)


# ------------------------------------------------------------------
# 適用先期間の自動判定
# ------------------------------------------------------------------
def overlap_seconds(df: pd.DataFrame, period: PeriodResult) -> float:
    """CSV の時刻範囲と期間の重なり (秒)。"""
    if df.empty:
        return 0.0
    s = max(df["sec_time"].min(), pd.Timestamp(aware_utc(period.range.start)))
    e = min(df["sec_time"].max(), pd.Timestamp(aware_utc(period.range.end)))
    return max(0.0, (e - s).total_seconds())


def choose_period(df: pd.DataFrame, periods: Iterable[PeriodResult],
                  label: str = "") -> PeriodResult | None:
    """適用先期間を決める。label 指定があればそれ、無ければ重なり最大の期間。"""
    periods = list(periods)
    if label:
        return next((p for p in periods if p.label == label), None)
    best, best_ov = None, 0.0
    for p in periods:
        ov = overlap_seconds(df, p)
        if ov > best_ov:
            best, best_ov = p, ov
    return best


# ------------------------------------------------------------------
# 自動運転マスク (BQ/Druid state 流用 → state CSV → 全て自動扱い)
# ------------------------------------------------------------------
def state_df_from_sql_result(df: pd.DataFrame) -> pd.DataFrame | None:
    """state クエリ結果 (sec_time, system_state) を attach_auto_mask 用の形に整える。

    BigQuery はマイクロ秒単位 (datetime64[us]) で返すことがある。ns に揃えないと
    astype(int64) がマイクロ秒値になり、CSV 側 (ns) との時刻突き合わせが全て外れて
    「全行手動扱い」→ 置き換え結果 0 件になってしまう。
    """
    if df is None or df.empty:
        return None
    sec = pd.to_datetime(df["sec_time"], utc=True, errors="coerce").dt.as_unit("ns")
    out = pd.DataFrame({
        "t_ns": sec.astype("int64"),
        "system_state": pd.to_numeric(df["system_state"], errors="coerce"),
    })
    out = out.dropna().sort_values("t_ns").reset_index(drop=True)
    return out if not out.empty else None


def fetch_state_series(backend, config: RunConfig, period: PeriodResult) -> pd.DataFrame | None:
    """取得元 (BQ/Druid) から期間の system_state 系列を取り直す。失敗時 None。"""
    try:
        p = QueryParams(
            vehicle_id=config.vehicle_id,
            start_time=period.range.start.isoformat(),
            end_time=period.range.end.isoformat(),
            excludes=config.excludes,
            tables=config.tables,
            dialect=Dialect(kind=config.backend, bq_prefix=config.bq_table_prefix),
        )
        return state_df_from_sql_result(backend.sql(build_state_series_query(p)))
    except Exception:
        return None


def state_from_csv(files: dict[str, bytes], state_file: str) -> pd.DataFrame | None:
    if not state_file or state_file not in files:
        return None
    try:
        return load_state_series(files[state_file])
    except Exception:
        return None


# ------------------------------------------------------------------
# 位置 (Truck Tracker) の結合
# ------------------------------------------------------------------
_EARTH_R_KM = 6371.0


def _haversine_km(lat1, lon1, lat2, lon2):
    la1, lo1, la2, lo2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return 2 * _EARTH_R_KM * np.arcsin(np.sqrt(a))


def prepare_positions(truck_df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Truck Tracker の位置 (ts/lat/lon) から、時刻順の位置+累積距離 DF を作る。"""
    if truck_df is None or truck_df.empty or not {"ts", "lat", "lon"}.issubset(truck_df.columns):
        return None
    return _positions_from_latlon_rows(truck_df["ts"], truck_df["lat"], truck_df["lon"])


def _positions_from_latlon_rows(sec: pd.Series, lat: pd.Series, lon: pd.Series
                                ) -> pd.DataFrame | None:
    """(sec_time, lat, lon) 群から、時刻順の位置+累積距離 DF を作る（内部共通）。"""
    d = pd.DataFrame({
        "_tt": pd.to_datetime(sec, utc=True, errors="coerce").dt.as_unit("ns"),
        "lat": pd.to_numeric(lat, errors="coerce"),
        "lon": pd.to_numeric(lon, errors="coerce"),
    }).dropna(subset=["_tt", "lat", "lon"])
    d = d.drop_duplicates(subset=["_tt"]).sort_values("_tt").reset_index(drop=True)
    if d.empty:
        return None
    step = _haversine_km(d["lat"].shift(), d["lon"].shift(), d["lat"], d["lon"])
    d["cum_dist_km"] = pd.Series(step).fillna(0.0).cumsum()
    return d[["_tt", "lat", "lon", "cum_dist_km"]]


def positions_from_period(period: PeriodResult) -> pd.DataFrame | None:
    """取得済み期間が既に持つ緯度経度（同一期間の他指標）から位置ソースを作る。

    Truck Tracker が無くても、同じ期間の BQ/Druid 由来 DF（lateral error・
    自由フィールド等）が lat/lon を持っていれば、それを時刻で流用できる。
    密なソース（timeseries=1秒）を優先し、metric（1分窓）も併せて集める。
    """
    secs, lats, lons = [], [], []
    for c in period.chunks:
        dfs = list(c.metric_dfs.values()) + list(c.custom_dfs.values())
        for d in dfs:
            if d is None or d.empty:
                continue
            if not {"sec_time", "latitude", "longitude"}.issubset(d.columns):
                continue
            m = d["latitude"].notna() & d["longitude"].notna()
            if not m.any():
                continue
            secs.append(d.loc[m, "sec_time"])
            lats.append(d.loc[m, "latitude"])
            lons.append(d.loc[m, "longitude"])
    if not secs:
        return None
    return _positions_from_latlon_rows(
        pd.concat(secs, ignore_index=True),
        pd.concat(lats, ignore_index=True),
        pd.concat(lons, ignore_index=True),
    )


def _auto_tolerance_s(positions: pd.DataFrame) -> float:
    """位置ソースの密度から結合許容差（秒）を決める。密なら小さく、疎なら大きく。"""
    if positions is None or len(positions) < 2:
        return 65.0
    gaps = positions["_tt"].sort_values().diff().dropna().dt.total_seconds()
    med = float(gaps.median()) if not gaps.empty else 2.0
    return float(min(65.0, max(2.0, med * 1.5)))


def attach_positions(out: pd.DataFrame, positions: pd.DataFrame | None,
                     tolerance_s: float | None = None) -> pd.DataFrame:
    """metric/timeseries DF の latitude/longitude/cum_dist_km を位置ソースで埋める。

    tolerance_s=None のときは位置ソースの密度から自動決定する（1分窓の疎なソースでも
    結び付くように、疎なら許容差を広げる）。
    """
    if positions is None or positions.empty or out.empty or "sec_time" not in out.columns:
        return out
    tol = _auto_tolerance_s(positions) if tolerance_s is None else tolerance_s
    d = out.copy().sort_values("sec_time")
    left = pd.DataFrame({"_tt": pd.to_datetime(d["sec_time"], utc=True).dt.as_unit("ns")})
    merged = pd.merge_asof(
        left, positions, on="_tt", direction="nearest",
        tolerance=pd.Timedelta(seconds=tol),
    )
    if "latitude" in d.columns:
        d["latitude"] = merged["lat"].to_numpy()
        d["longitude"] = merged["lon"].to_numpy()
    d["cum_dist_km"] = merged["cum_dist_km"].to_numpy()
    return d.reset_index(drop=True)


# ------------------------------------------------------------------
# 置き換えの適用
# ------------------------------------------------------------------
def _spec_by_key(key: str) -> MetricSpec | None:
    return next((s for s in METRICS if s.key == key), None)


def resolve_auto_mask(df: pd.DataFrame, drive_mode: str,
                      state_df: pd.DataFrame | None,
                      auto_value: int) -> tuple[pd.Series | None, str]:
    """運転モードから自動運転マスクを決める。戻り値: (mask, 警告)。

    drive_mode:
      - "auto"   : 全行を自動運転扱い
      - "manual" : 全行を手動運転扱い
      - "state"  : state (BQ/Druid か state CSV) で判定。state が無ければ mask=None
                   （呼び出し側で「判定不能」として扱い、勝手に自動と決めつけない）
    """
    if drive_mode == "auto":
        return pd.Series(True, index=df.index), ""
    if drive_mode == "manual":
        return pd.Series(False, index=df.index), ""
    if state_df is None or state_df.empty:
        return None, ("自動運転の判定に使う state が取得できません。"
                      "state CSV を一緒にアップロードするか、運転モードで"
                      "「すべて自動」「すべて手動」を明示してください。")
    return attach_auto_mask(df, state_df, auto_value), ""


def apply_override(period: PeriodResult, entry: OverrideEntry, df: pd.DataFrame,
                   config: RunConfig, state_df: pd.DataFrame | None,
                   positions: pd.DataFrame | None,
                   drive_mode: str = "state") -> list[str]:
    """1エントリ分の置き換えを period に適用する。戻り値: 警告メッセージ。

    drive_mode: "state"（既定・state で自動/手動を判定）/ "auto" / "manual"。
    """
    warnings: list[str] = []
    df = apply_excludes(df, config.excludes)
    # 期間の時間範囲内に絞る (期間外の行が統計へ混ざるのを防ぐ)
    t = df["sec_time"]
    df = df[(t >= pd.Timestamp(aware_utc(period.range.start)))
            & (t < pd.Timestamp(aware_utc(period.range.end)))]
    if df.empty:
        return [f"{entry.file_name}: 期間 {period.label} と重なる行がありません。"]

    spec = _spec_by_key(entry.target)
    cf: CustomField | None = next(
        (c for c in config.custom_fields if c.key == entry.target), None)
    if entry.target != TARGET_Q3 and spec is None and cf is None:
        return [f"{entry.file_name}: 対象 {entry.target} が不明のためスキップしました。"]

    token = (spec.column if spec else cf.column if cf else "lateral_acceleration").split(".")[-1]
    col = entry.column.strip() or guess_value_column(df, token)
    if not col or col not in df.columns:
        return [f"{entry.file_name}: 値列を特定できません (指定「{entry.column}」)。"
                "値列を選択してください。"]

    values = pd.to_numeric(df[col], errors="coerce") * float(entry.scale) + float(entry.offset)
    auto_value = auto_state_value(period.range.start, config.system_state_gen)
    auto_mask, mask_warn = resolve_auto_mask(df, drive_mode, state_df, auto_value)
    if auto_mask is None:
        # 勝手に自動/手動を決めず、適用を見送る（逆転誤判定を防ぐ）
        return [f"{entry.file_name}: {mask_warn}"]

    note = f"{entry.file_name} → {col}"
    n_period = len(df)
    n_auto = int(auto_mask.sum())

    def _empty_reason(threshold: float) -> str:
        n_th = int((values[auto_mask].abs() >= float(threshold)).sum())
        return (f"{entry.file_name}: 置き換え結果が 0 件のため適用しませんでした。"
                f"内訳: 期間内 {n_period} 行 → 自動運転({auto_note(auto_value)}) {n_auto} 行 → "
                f"|値|≥{threshold:g} {n_th} 行。自動運転が 0 行の場合は state の"
                "取得/内容を、しきい値で 0 行の場合はしきい値・scale を確認してください。")

    if entry.target == TARGET_Q3:
        hist = build_hist_df(values[auto_mask], values[~auto_mask], Q3_HIST_BASE_BIN)
        if hist.empty:
            return warnings + [_empty_reason(0.0)]
        period.set_override("hist", hist, note)
    elif spec is not None:  # q1 / q2
        threshold = config.threshold(spec.key, spec.default_threshold)
        out = build_metric_df(df[auto_mask], values[auto_mask], spec.name, threshold)
        if out.empty:
            return warnings + [_empty_reason(threshold)]
        out = attach_positions(out, positions)
        period.set_override(f"metric:{spec.key}", out, note)
    else:  # 自由フィールド
        if cf.agg_mode == "timeseries":
            out = build_timeseries_df(df, values)  # 1秒平均・フィルタなし (現行仕様)
            if out.empty:
                return warnings + [f"{entry.file_name}: 有効な値がなく 0 件のため適用しませんでした。"]
            out = attach_positions(out, positions)
            period.set_override(f"custom:{cf.key}", out, note)
            # BQ パイプラインは timeseries モードでも自動/手動別ヒストを作るので合わせる
            hist = build_hist_df(values[auto_mask], values[~auto_mask], cf.hist_bin)
            if not hist.empty:
                period.set_override(f"customhist:{cf.key}", hist, note)
        else:
            out = build_metric_df(df[auto_mask], values[auto_mask], cf.name, cf.threshold)
            if out.empty:
                return warnings + [_empty_reason(cf.threshold)]
            out = attach_positions(out, positions)
            period.set_override(f"custom:{cf.key}", out, note)
            hist = build_hist_df(values[auto_mask], values[~auto_mask], cf.hist_bin)
            period.set_override(f"customhist:{cf.key}", hist, note)
    if positions is None and entry.target != TARGET_Q3:
        warnings.append(f"{entry.file_name}: Truck Tracker の位置が無いため地図・距離軸には"
                        "載りません (値の統計・時刻軸グラフは有効)。")
    return warnings
