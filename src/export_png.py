# src/export_png.py
from __future__ import annotations

import re
import zipfile
from io import BytesIO
from typing import Optional

import pandas as pd
import matplotlib
import matplotlib.pyplot as plt

from src.ui_view import (
    make_query1_fig,
    make_query2_fig,
    make_query3_fig,
    make_extra_scatter_fig,
    make_scatter_compare_fig,
    make_query3_compare_fig,
)


def _fig_to_png_bytes(fig: matplotlib.figure.Figure, *, dpi: int = 150) -> bytes:
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _add_fig(
    zf: zipfile.ZipFile,
    filename: str,
    fig: Optional[matplotlib.figure.Figure],
    *,
    dpi: int = 150,
) -> None:
    if fig is None:
        return
    png = _fig_to_png_bytes(fig, dpi=dpi)
    zf.writestr(filename, png)


def _sanitize(name: str) -> str:
    return re.sub(r"[^\w\-]", "_", name, flags=re.UNICODE)


def build_png_zip(
    *,
    all_excel_sheets: dict[str, pd.DataFrame],
    ranges: list,
    compare_q1: list[tuple[str, pd.DataFrame]],
    compare_q2: list[tuple[str, pd.DataFrame]],
    compare_q3: list[tuple[str, pd.DataFrame]],
    xlim=None,
    ylim_q1=None,
    ylim_q2=None,
    xlim_q3=None,
    ylim_q3=None,
    smooth_window_q3: int = 1,
    fig_size: tuple[float, float] = (7.0, 4.0),
    fig_size_compare: tuple[float, float] = (9.0, 4.5),
    dpi: int = 150,
) -> bytes:
    """全チャートを PNG 化して ZIP バイト列を返す。"""
    buf = BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # --- 比較（全期間） ---
        if len(ranges) >= 2:
            _add_fig(
                zf, "比較/Q1_lateral_error.png",
                make_scatter_compare_fig(
                    "クエリ1: lateral error（比較）", compare_q1,
                    x_col="cum_dist_km", y_col="lateral_error",
                    x_label="移動距離[km]", y_label="lateral error[m]",
                    xlim=xlim, ylim=ylim_q1, fig_size=fig_size_compare,
                ), dpi=dpi,
            )
            _add_fig(
                zf, "比較/Q2_acceleration.png",
                make_scatter_compare_fig(
                    "クエリ2: acceleration（比較）", compare_q2,
                    x_col="cum_dist_km", y_col="acceleration",
                    x_label="移動距離[km]", y_label="加速度[m/s^2]",
                    xlim=xlim, ylim=ylim_q2, fig_size=fig_size_compare,
                ), dpi=dpi,
            )
            _add_fig(
                zf, "比較/Q3_横G.png",
                make_query3_compare_fig(
                    compare_q3, xlim=xlim_q3, ylim=ylim_q3,
                    smooth_window=smooth_window_q3, fig_size=fig_size_compare,
                ), dpi=dpi,
            )

        # --- 各期間 ---
        for i, r in enumerate(ranges):
            period = i + 1
            label = r.label if getattr(r, "label", None) else f"テスト{period}"
            safe_label = _sanitize(label)
            folder = f"{safe_label}_T{period}"

            # チャンク数を数える
            total_chunks = 0
            while f"T{period}_C{total_chunks + 1}_Q1" in all_excel_sheets:
                total_chunks += 1

            for c in range(1, total_chunks + 1):
                prefix = f"{folder}/区間{c}" if total_chunks > 1 else folder

                df1 = all_excel_sheets.get(f"T{period}_C{c}_Q1", pd.DataFrame())
                df2 = all_excel_sheets.get(f"T{period}_C{c}_Q2", pd.DataFrame())
                df3 = all_excel_sheets.get(f"T{period}_C{c}_Q3", pd.DataFrame())

                _add_fig(zf, f"{prefix}/Q1_lateral_error.png",
                         make_query1_fig(df1, xlim=xlim, ylim=ylim_q1, fig_size=fig_size), dpi=dpi)
                _add_fig(zf, f"{prefix}/Q2_acceleration.png",
                         make_query2_fig(df2, xlim=xlim, ylim=ylim_q2, fig_size=fig_size), dpi=dpi)
                _add_fig(zf, f"{prefix}/Q3_横G.png",
                         make_query3_fig(df3, xlim=xlim_q3, ylim=ylim_q3,
                                         smooth_window=smooth_window_q3, fig_size=fig_size), dpi=dpi)

                # 追加散布図
                ex_prefix = f"T{period}_C{c}_EX_"
                for sheet_key in sorted(all_excel_sheets):
                    if sheet_key.startswith(ex_prefix):
                        ex_label = sheet_key[len(ex_prefix):]
                        ex_df = all_excel_sheets[sheet_key]
                        safe_ex = _sanitize(ex_label)
                        _add_fig(zf, f"{prefix}/EX_{safe_ex}.png",
                                 make_extra_scatter_fig(ex_label, ex_df, xlim=xlim, fig_size=fig_size), dpi=dpi)

    return buf.getvalue()
