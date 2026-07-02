# src/export/excel.py
# RunResults → Excel 変換。
# シート名 T{期間}_C{チャンク}_Q{1..3} は従来の Excel 出力と互換。
from __future__ import annotations

import re
from datetime import timedelta, timezone
from io import BytesIO
from typing import Dict

import pandas as pd
from openpyxl.utils import get_column_letter

from src.domain.results import RunResults, rebin_hist
from src.queries.specs import METRICS

JST = timezone(timedelta(hours=9))

# Q3 シートの既定ビン幅。取得は微細な基準ビン（0.05）だが、シートは従来形式・
# 画面表示（表示ビン幅の既定 0.2）と揃える。
DEFAULT_HIST_BIN_Q3 = 0.2


def _strip_timezones(df: pd.DataFrame) -> pd.DataFrame:
    """
    tz付き datetime 列を JST に変換してから tz 情報を外す。
    Excel は tz付き datetime を書き込めない（BigQuery の結果は tz付きで返る）。
    """
    out = df.copy()
    for col in out.columns:
        if isinstance(out[col].dtype, pd.DatetimeTZDtype):
            out[col] = out[col].dt.tz_convert(JST).dt.tz_localize(None)
    return out


def results_to_sheets(
    results: RunResults, *, hist_bin_q3: float = DEFAULT_HIST_BIN_Q3
) -> Dict[str, pd.DataFrame]:
    """一次データモデルから従来互換のシート辞書を導出する。

    Q3 シートは取得時の微細ビン（基準 0.05）を表示ビン幅 hist_bin_q3 へ再集計して
    出力する（画面のヒストグラム・PNG 出力と同じ見た目のデータになる）。
    """
    sheets: Dict[str, pd.DataFrame] = {}
    for p_idx, period in enumerate(results.periods, start=1):
        for c_idx, chunk in enumerate(period.chunks, start=1):
            for q_idx, spec in enumerate(METRICS, start=1):
                sheets[f"T{p_idx}_C{c_idx}_Q{q_idx}"] = chunk.metric_dfs.get(spec.key, pd.DataFrame())
            sheets[f"T{p_idx}_C{c_idx}_Q3"] = rebin_hist(chunk.hist_df, hist_bin_q3)
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
            _strip_timezones(df).to_excel(writer, sheet_name=safe, index=False)
            _autosize_worksheet(writer.book[safe])
    return bio.getvalue()


def results_to_excel_bytes(
    results: RunResults, *, hist_bin_q3: float = DEFAULT_HIST_BIN_Q3
) -> bytes:
    return to_excel_bytes(results_to_sheets(results, hist_bin_q3=hist_bin_q3))
