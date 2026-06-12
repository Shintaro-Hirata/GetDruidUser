# src/ui/views/common.py
from __future__ import annotations

from typing import Sequence

import pandas as pd

from src.domain.models import ExcludeRange


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
