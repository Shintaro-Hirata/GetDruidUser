# src/ui/views/common.py
from __future__ import annotations

from datetime import timedelta, timezone
from typing import Sequence

import pandas as pd

from src.domain.models import ExcludeRange

JST = timezone(timedelta(hours=9))


def jst_display_series(series: pd.Series) -> pd.Series:
    """
    時刻列（UTCのISO文字列 or tz付きdatetime）を表示用のJST文字列にする。
    変換できない値は元の文字列のまま返す。
    """
    t = pd.to_datetime(series, utc=True, errors="coerce")
    jst = t.dt.tz_convert(JST).dt.strftime("%Y-%m-%d %H:%M:%S")
    return jst.where(t.notna(), series.astype(str))


def df_times_to_jst(df: pd.DataFrame, cols: Sequence[str] = ("win_1m", "sec_time")) -> pd.DataFrame:
    """表示用：時刻列を JST（+09:00）に変換したコピーを返す（元データは不変）。"""
    out = df.copy()
    for c in cols:
        if c not in out.columns:
            continue
        t = pd.to_datetime(out[c], utc=True, errors="coerce")
        if t.notna().any():
            out[c] = t.dt.tz_convert(JST)
    return out


def split_by_excludes(
    df: pd.DataFrame,
    excludes: Sequence[ExcludeRange],
    *,
    time_col: str = "sec_time",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    df を（除外時間帯の外, 中）に分割する。
    「実行」前の除外プレビュー（除外予定の点をグレー表示）に使う。
    """
    if df is None or df.empty or not excludes or time_col not in df.columns:
        return df, pd.DataFrame()

    t = pd.to_datetime(df[time_col], utc=True, errors="coerce")
    mask = pd.Series(False, index=df.index)
    for r in excludes:
        start = pd.Timestamp(r.start).tz_convert("UTC") if r.start.tzinfo else pd.Timestamp(r.start, tz="UTC")
        end = pd.Timestamp(r.end).tz_convert("UTC") if r.end.tzinfo else pd.Timestamp(r.end, tz="UTC")
        mask |= (t >= start) & (t < end)

    return df[~mask], df[mask]
