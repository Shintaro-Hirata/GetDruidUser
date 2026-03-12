# src/export_excel.py
import re
from io import BytesIO
from typing import Dict

import pandas as pd
from openpyxl.utils import get_column_letter


def _autosize_worksheet(ws):
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
            # Excel does not support timezone-aware datetimes
            dt_cols = df.select_dtypes(include=["datetimetz"]).columns
            if len(dt_cols) > 0:
                df = df.copy()
                for c in dt_cols:
                    df[c] = df[c].dt.tz_localize(None)
            df.to_excel(writer, sheet_name=safe, index=False)
            _autosize_worksheet(writer.book[safe])
    return bio.getvalue()