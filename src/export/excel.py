# src/export/excel.py
# RunResults → Excel 変換。
# シート名 T{期間}_C{チャンク}_Q{1..3} は従来の Excel 出力と互換。
from __future__ import annotations

import re
from io import BytesIO
from typing import Dict

import pandas as pd
from openpyxl.utils import get_column_letter

from src.domain.results import RunResults
from src.queries.specs import METRICS


def results_to_sheets(results: RunResults) -> Dict[str, pd.DataFrame]:
    """一次データモデルから従来互換のシート辞書を導出する。"""
    sheets: Dict[str, pd.DataFrame] = {}
    for p_idx, period in enumerate(results.periods, start=1):
        for c_idx, chunk in enumerate(period.chunks, start=1):
            for q_idx, spec in enumerate(METRICS, start=1):
                sheets[f"T{p_idx}_C{c_idx}_Q{q_idx}"] = chunk.metric_dfs.get(spec.key, pd.DataFrame())
            sheets[f"T{p_idx}_C{c_idx}_Q3"] = chunk.hist_df
    return sheets


def _autosize_worksheet(ws) -> None:
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            v = cell.value
            max_len = max(max_len, len(str(v)) if v is not None else 0)
        ws.column_dimensions[col_letter].width = min(max(10, max_len + 2), 50)


def to_excel_bytes(sheets: Dict[str, pd.DataFrame]) -> bytes:
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        for name, df in sheets.items():
            safe = re.sub(r"[\[\]\:\*\?\/\\]", "_", name)[:31]
            df.to_excel(writer, sheet_name=safe, index=False)
            _autosize_worksheet(writer.book[safe])
    return bio.getvalue()


def results_to_excel_bytes(results: RunResults) -> bytes:
    return to_excel_bytes(results_to_sheets(results))
