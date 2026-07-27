# src/services/csv_periods.py
# mcap 由来 CSV（GetMcapToCsv のトピック別出力）を、BigQuery を介さずに
# 「期間（PeriodResult）」として取り込む。BQ にデータが欠損している運行を
# mcap の生値で補い、既存の散布図/比較/表/ヒストグラムでそのまま統計比較できる。
#
# CSV の前提（GetMcapToCsv の出力形式）:
#   - 先頭列に time_jst / t_sec / t_ns（t_ns = epoch ナノ秒）を持つ
#   - 値はフラット展開された列名（例 "debug_for_mcap.lateral_error"）
#   - 自動運転判定には t2_system_state_manager_state トピックの CSV を併用
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from src.domain.drive_state import auto_mask_for_values, detect_auto_value
from src.domain.models import CustomField, ExcludeRange, RunConfig, TimeRange
from src.domain.results import ChunkData, PeriodResult, add_ratio, merge_auto_manual_hist
from src.domain.x_axis import aware_utc
from src.queries.specs import METRICS, MetricSpec

RESERVED_COLUMNS = {"time_jst", "t_sec", "t_ns", "topic", "file"}

# 状態 CSV の判別（ファイル名に含まれる文字列）と state 列の推定に使う
STATE_FILE_HINT = "system_state_manager_state"
STATE_COLUMN_HINT = "system_state"
# 自動運転値は enum 世代で変わる (202605a で 4→16)。判定は drive_state.py に集約。
AUTO_DRIVE_STATE = 4  # 旧世代の値 (後方互換のため残置。新規コードは drive_state を使う)


@dataclass(frozen=True)
class CsvPeriodEntry:
    """CSV 1ファイル分の取り込み指定（サイドバーの1行）"""
    label: str        # 期間ラベル（同じラベルの複数エントリは1期間に束ねる）
    file_name: str    # アップロードした CSV のファイル名
    target: str       # "q1" | "q2" | CustomField.key
    column: str = ""  # 値列名（空なら対象から自動推定）
    scale: float = 1.0
    offset: float = 0.0


def read_mcap_csv(data: bytes | str) -> pd.DataFrame:
    """GetMcapToCsv の CSV を読み、UTC の sec_time（秒精度）列を付けて返す。"""
    if isinstance(data, bytes):
        df = pd.read_csv(io.BytesIO(data), encoding="utf-8-sig")
    else:
        df = pd.read_csv(data, encoding="utf-8-sig")
    if "t_ns" not in df.columns:
        raise ValueError("t_ns 列がありません (GetMcapToCsv の出力 CSV を指定してください)")
    t_ns = pd.to_numeric(df["t_ns"], errors="coerce")
    df = df[t_ns.notna()].copy()
    df["t_ns"] = t_ns.dropna().astype("int64")
    df["sec_time"] = pd.to_datetime(df["t_ns"], utc=True).dt.floor("s")
    return df.sort_values("t_ns").reset_index(drop=True)


def guess_value_column(df: pd.DataFrame, token: str) -> str | None:
    """値列名を推定する。token（例 "lateral_error"）に一致/末尾一致する列を探す。"""
    candidates = [c for c in df.columns if c not in RESERVED_COLUMNS and c != "sec_time"]
    for c in candidates:
        if c == token:
            return c
    for c in candidates:
        if c.endswith("." + token) or c.split(".")[-1] == token:
            return c
    numeric = [c for c in candidates
               if pd.to_numeric(df[c], errors="coerce").notna().any()]
    return numeric[0] if len(numeric) == 1 else None


def load_state_series(data: bytes | str) -> pd.DataFrame:
    """状態 CSV から (t_ns, system_state) の2列を取り出す。"""
    df = read_mcap_csv(data)
    col = None
    for c in df.columns:
        if c in RESERVED_COLUMNS or c == "sec_time":
            continue
        if STATE_COLUMN_HINT in c.lower():
            col = c
            break
    if col is None:
        col = guess_value_column(df, STATE_COLUMN_HINT)
    if col is None:
        raise ValueError("state CSV から system_state 列を特定できません")
    # 数値 (enum番号) と文字列 (enum名 kAutonomousDriving 等) の両方を保持する。
    # 文字列の state は世代によらずラベルで直接判定できる (drive_state.auto_mask_for_values)。
    raw = df[col]
    num = pd.to_numeric(raw, errors="coerce")
    state_vals = num.where(num.notna(), raw.astype(str).str.strip())
    out = pd.DataFrame({"t_ns": df["t_ns"], "system_state": state_vals})
    keep = num.notna() | raw.astype(str).str.strip().str.startswith("k")
    return out[keep].sort_values("t_ns").reset_index(drop=True)


def attach_auto_mask(df: pd.DataFrame, state_df: pd.DataFrame | None,
                     auto_value: int = AUTO_DRIVE_STATE) -> pd.Series:
    """各行が自動運転 (kAutonomousDriving) かどうかの真偽列を返す。state 不明なら全 True。

    auto_value は録画世代で解決した kAutonomousDriving の数値 (drive_state.auto_state_value)。
    state 値が文字列 (enum名) の場合は値によらずラベルで判定する。
    """
    if state_df is None or state_df.empty:
        return pd.Series(True, index=df.index)
    merged = pd.merge_asof(
        df[["t_ns"]].astype("int64"),
        state_df.assign(t_ns=state_df["t_ns"].astype("int64")),
        on="t_ns",
        direction="nearest",
        tolerance=1_000_000_000,  # 1秒以内の state を採用
    )
    return auto_mask_for_values(merged["system_state"], auto_value).set_axis(df.index)


def apply_excludes(df: pd.DataFrame, excludes: Iterable[ExcludeRange]) -> pd.DataFrame:
    """除外時間帯 [start, end) の行を落とす（SQL 側の除外と同等）。"""
    excludes = list(excludes or [])
    if not excludes or df.empty:
        return df
    keep = pd.Series(True, index=df.index)
    t = df["sec_time"]
    for ex in excludes:
        keep &= ~((t >= aware_utc(ex.start)) & (t < aware_utc(ex.end)))
    return df[keep]


METRIC_DF_COLUMNS = ["win_1m", "sec_time", "latitude", "longitude"]


def build_metric_df(df: pd.DataFrame, values: pd.Series, name: str,
                    threshold: float) -> pd.DataFrame:
    """BQ の metric クエリと同形の DF（1分窓ごとの最大絶対値の行）を作る。

    列: win_1m, sec_time, latitude, longitude, <name>, abs_<name>, cum_dist_km
    緯度経度・距離は CSV に無いので NaN（描画は時刻/経過時間軸へフォールバック）。
    """
    cols = METRIC_DF_COLUMNS + [name, f"abs_{name}", "cum_dist_km"]
    d = pd.DataFrame({"sec_time": df["sec_time"], name: pd.to_numeric(values, errors="coerce")})
    d = d.dropna(subset=[name])
    d[f"abs_{name}"] = d[name].abs()
    d = d[d[f"abs_{name}"] >= float(threshold)]
    if d.empty:
        return pd.DataFrame(columns=cols)
    d["win_1m"] = d["sec_time"].dt.floor("min")
    idx = d.groupby("win_1m")[f"abs_{name}"].idxmax()
    out = d.loc[idx].sort_values("win_1m").reset_index(drop=True)
    out["latitude"] = np.nan
    out["longitude"] = np.nan
    out["cum_dist_km"] = np.nan
    return out[cols]


def build_timeseries_df(df: pd.DataFrame, values: pd.Series) -> pd.DataFrame:
    """BQ の timeseries クエリと同形の DF（1秒平均）を作る。

    緯度経度列も BQ 版と同様に持つ（CSV には無いので NaN。Truck Tracker の位置を
    後から結合すれば地図にも載る）。
    """
    d = pd.DataFrame({"sec_time": df["sec_time"], "value": pd.to_numeric(values, errors="coerce")})
    d = d.dropna(subset=["value"])
    if d.empty:
        return pd.DataFrame(columns=["sec_time", "value", "latitude", "longitude", "cum_dist_km"])
    out = d.groupby("sec_time", as_index=False)["value"].mean()
    out["latitude"] = np.nan
    out["longitude"] = np.nan
    out["cum_dist_km"] = np.nan
    return out


def build_hist_df(auto_values: pd.Series, manual_values: pd.Series,
                  bin_width: float) -> pd.DataFrame:
    """自動/手動別の値分布ヒストグラム（BQ の custom hist と同形）を作る。"""
    def bins(v: pd.Series, cnt_col: str) -> pd.DataFrame:
        v = pd.to_numeric(v, errors="coerce").dropna()
        if v.empty:
            return pd.DataFrame(columns=["bin_start", "bin_end", cnt_col])
        start = (np.floor(v / bin_width) * bin_width).round(9)
        g = start.value_counts().sort_index()
        d = pd.DataFrame({"bin_start": g.index, cnt_col: g.values})
        d["bin_end"] = (d["bin_start"] + bin_width).round(9)
        return d[["bin_start", "bin_end", cnt_col]]

    auto = add_ratio(bins(auto_values, "cnt_auto"), cnt_col="cnt_auto", ratio_col="ratio_auto")
    manual = add_ratio(bins(manual_values, "cnt_manual"), cnt_col="cnt_manual", ratio_col="ratio_manual")
    return merge_auto_manual_hist(auto, manual)


def _spec_by_key(key: str) -> MetricSpec | None:
    for spec in METRICS:
        if spec.key == key:
            return spec
    return None


def build_csv_periods(entries: Iterable[CsvPeriodEntry], files: dict[str, bytes],
                      config: RunConfig, *, state_filter: bool = True,
                      ) -> tuple[list[PeriodResult], list[str]]:
    """CSV エントリ群から PeriodResult のリストを作る。

    同じラベルのエントリは1つの期間（1チャンク）に束ねる。
    戻り値: (期間リスト, 警告メッセージリスト)
    """
    warnings: list[str] = []
    cf_by_key = {cf.key: cf for cf in config.custom_fields}

    state_df = None
    if state_filter:
        state_names = [n for n in files if STATE_FILE_HINT in n]
        if state_names:
            try:
                state_df = load_state_series(files[state_names[0]])
            except Exception as e:
                warnings.append(f"state CSV の読み込みに失敗 ({state_names[0]}): {e}")
        else:
            warnings.append("state CSV (t2_system_state_manager_state) が無いため、"
                            "自動運転区間の絞り込みなしで取り込みます。")

    # ラベル順を保って期間ごとに束ねる
    by_label: dict[str, list[CsvPeriodEntry]] = {}
    for e in entries:
        by_label.setdefault(e.label, []).append(e)

    periods: list[PeriodResult] = []
    for label, group in by_label.items():
        metric_dfs: dict[str, pd.DataFrame] = {}
        custom_dfs: dict[str, pd.DataFrame] = {}
        custom_hist_dfs: dict[str, pd.DataFrame] = {}
        t_min: pd.Timestamp | None = None
        t_max: pd.Timestamp | None = None

        for e in group:
            if e.file_name in files and STATE_FILE_HINT in e.file_name:
                warnings.append(f"{label}: state CSV は値の取り込み対象にできません "
                                f"({e.file_name})")
                continue
            data = files.get(e.file_name)
            if data is None:
                warnings.append(f"{label}: ファイルがアップロードされていません ({e.file_name})")
                continue
            try:
                df = read_mcap_csv(data)
            except Exception as ex:
                warnings.append(f"{label}: CSV 読み込み失敗 ({e.file_name}): {ex}")
                continue
            df = apply_excludes(df, config.excludes)
            if df.empty:
                warnings.append(f"{label}: 有効な行がありません ({e.file_name})")
                continue

            spec = _spec_by_key(e.target)
            cf: CustomField | None = cf_by_key.get(e.target)
            if spec is None and cf is None:
                warnings.append(f"{label}: 対象 {e.target} が不明のためスキップ")
                continue

            token = (spec.column if spec else cf.column).split(".")[-1]
            col = e.column.strip() or guess_value_column(df, token)
            if not col or col not in df.columns:
                warnings.append(
                    f"{label}: 値列を特定できません ({e.file_name}, 指定「{e.column}」, "
                    f"推定キー「{token}」)。CSV の列名を「値列」に入力してください。")
                continue

            raw = pd.to_numeric(df[col], errors="coerce")
            auto_mask = attach_auto_mask(
                df, state_df,
                detect_auto_value(
                    state_df["system_state"] if state_df is not None else None,
                    df["sec_time"].min().to_pydatetime(),
                    config.system_state_gen)[0])

            if spec is not None:  # Q1 / Q2
                values = raw * float(e.scale) + float(e.offset)
                threshold = config.threshold(spec.key, spec.default_threshold)
                metric_dfs[spec.key] = build_metric_df(
                    df[auto_mask], values[auto_mask], spec.name, threshold)
            else:  # 自由フィールド
                values = raw * float(cf.scale) + float(cf.offset)
                if cf.agg_mode == "timeseries":
                    custom_dfs[cf.key] = build_timeseries_df(df, values)
                else:
                    custom_dfs[cf.key] = build_metric_df(
                        df[auto_mask], values[auto_mask], cf.name, cf.threshold)
                    custom_hist_dfs[cf.key] = build_hist_df(
                        values[auto_mask], values[~auto_mask], cf.hist_bin)

            t_min = df["sec_time"].min() if t_min is None else min(t_min, df["sec_time"].min())
            t_max = df["sec_time"].max() if t_max is None else max(t_max, df["sec_time"].max())

        if not (metric_dfs or custom_dfs or custom_hist_dfs) or t_min is None:
            warnings.append(f"{label}: 取り込めるデータがありませんでした。")
            continue

        rng = TimeRange(start=t_min.to_pydatetime(),
                        end=(t_max + pd.Timedelta(seconds=1)).to_pydatetime(),
                        label=label)
        chunk = ChunkData(start=rng.start, end=rng.end,
                          metric_dfs=metric_dfs,
                          custom_dfs=custom_dfs,
                          custom_hist_dfs=custom_hist_dfs)
        periods.append(PeriodResult(label=label, range=rng, chunks=[chunk],
                                    meta={"source": "mcap_csv"}))
    return periods, warnings
