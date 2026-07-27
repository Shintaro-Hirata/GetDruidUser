# src/domain/x_axis.py
# 散布図・時系列の横軸モード（移動距離/経過時間/時刻）の共通ロジック。
# 画面（Plotly, src/ui/views/scatter.py）と画像出力（matplotlib, src/export/images.py）の
# 両方がここを使うことで、同じ x_axis_mode 設定に対して描画が食い違わないようにする。
from __future__ import annotations

from datetime import timedelta, timezone

import pandas as pd

JST = timezone(timedelta(hours=9))

X_LABEL_DIST = "移動距離[km]"
X_LABEL_TIME = "時刻(JST)"
X_LABEL_ELAPSED = "経過時間[分]"
X_LABELS = {"distance": X_LABEL_DIST, "elapsed": X_LABEL_ELAPSED, "time": X_LABEL_TIME}

Series = list[tuple[str, pd.DataFrame]]


def aware_utc(dt) -> pd.Timestamp:
    """datetime を tz-aware（UTC）な Timestamp に揃える（naive は UTC とみなす）。"""
    ts = pd.Timestamp(dt)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def uses_distance_x(series: Series) -> bool:
    """系列のどれかに有効な cum_dist_km があれば移動距離をX軸にできる。

    列が存在しても全行 NULL（例: 距離ソースの制御テーブルにデータが無い時間帯の
    汎用時系列）は「距離なし」と判定し、時刻軸へのフォールバックを許す。
    """
    for _, df in series:
        if df is None or df.empty or "cum_dist_km" not in df.columns:
            continue
        if pd.to_numeric(df["cum_dist_km"], errors="coerce").notna().any():
            return True
    return False


def effective_x_mode(series: Series, x_mode: str) -> str:
    """要求された横軸モードを、実データで描けるモードへ解決する。

    - distance: どれかに有効な cum_dist_km があれば distance、無ければ time
    - elapsed:  どれかに sec_time があれば elapsed、無ければ time
    - time:     time
    """
    if x_mode == "distance":
        return "distance" if uses_distance_x(series) else "time"
    if x_mode == "elapsed":
        has_time = any(
            df is not None and not df.empty and "sec_time" in df.columns for _, df in series
        )
        return "elapsed" if has_time else "time"
    return "time"


def clean_xy_df(df: pd.DataFrame, value_col: str, *, mode: str, period_start=None) -> pd.DataFrame:
    """数値化と NaN 除去。X軸用の列 _x を作る（mode は解決済み: distance/elapsed/time）。"""
    if df is None or df.empty or value_col not in df.columns:
        return pd.DataFrame()
    d = df.copy()
    d[value_col] = pd.to_numeric(d[value_col], errors="coerce")
    if mode == "distance":
        if "cum_dist_km" not in d.columns:
            return pd.DataFrame()
        d["_x"] = pd.to_numeric(d["cum_dist_km"], errors="coerce")
    elif mode == "elapsed":
        if "sec_time" not in d.columns or period_start is None:
            return pd.DataFrame()
        t = pd.to_datetime(d["sec_time"], utc=True, errors="coerce")
        d["_x"] = (t - aware_utc(period_start)).dt.total_seconds() / 60.0
    else:  # time
        if "sec_time" not in d.columns:
            return pd.DataFrame()
        d["_x"] = pd.to_datetime(d["sec_time"], utc=True, errors="coerce").dt.tz_convert(JST)
    return d.dropna(subset=["_x", value_col])
