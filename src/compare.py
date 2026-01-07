# src/compare.py
# 「Excelに入った df をキーで拾って比較用 series を作る」処理
from __future__ import annotations

import pandas as pd


def collect_compare_series_from_excel_sheets(
    *,
    all_excel_sheets: dict[str, pd.DataFrame],
    pair_idx: int,
    num_chunks: int,
    label: str,
) -> tuple[tuple[str, pd.DataFrame], tuple[str, pd.DataFrame]]:
    """
    all_excel_sheets に保存された T{pair}_C{chunk}_Q1/Q2 を元に、
    比較表示用 (label, df) を返す。
    分割ありの場合は全チャンク結合して返す。
    """
    if num_chunks <= 1:
        df1 = all_excel_sheets.get(f"T{pair_idx+1}_C1_Q1", pd.DataFrame())
        df2 = all_excel_sheets.get(f"T{pair_idx+1}_C1_Q2", pd.DataFrame())
        return (label, df1), (label, df2)

    q1_all = []
    q2_all = []
    for chunk_idx in range(num_chunks):
        q1_all.append(all_excel_sheets.get(f"T{pair_idx+1}_C{chunk_idx+1}_Q1", pd.DataFrame()))
        q2_all.append(all_excel_sheets.get(f"T{pair_idx+1}_C{chunk_idx+1}_Q2", pd.DataFrame()))

    df1 = (
        pd.concat([d for d in q1_all if d is not None and not d.empty], ignore_index=True)
        if any(d is not None and not d.empty for d in q1_all)
        else pd.DataFrame()
    )
    df2 = (
        pd.concat([d for d in q2_all if d is not None and not d.empty], ignore_index=True)
        if any(d is not None and not d.empty for d in q2_all)
        else pd.DataFrame()
    )
    return (label, df1), (label, df2)
