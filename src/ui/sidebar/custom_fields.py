# src/ui/sidebar/custom_fields.py
# 自由フィールド（任意テーブル×列）の入力UI。
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.domain.models import CustomField
from src.ui.state import AppState

# 集計方法の表示名 ⇔ 内部値
_AGG_DISPLAY = {"既存指標と同じ": "metric", "汎用時系列": "timeseries"}
_AGG_DISPLAY_INV = {v: k for k, v in _AGG_DISPLAY.items()}

_COLUMNS = ["ラベル", "テーブル", "フィールド", "集計", "|値|>=", "ビン幅", "係数(×)", "加算(+)"]


def _rows_to_df(rows: list[dict]) -> pd.DataFrame:
    data = [
        {
            "ラベル": r.get("label", ""),
            "テーブル": r.get("table", ""),
            "フィールド": r.get("column", ""),
            "集計": _AGG_DISPLAY_INV.get(r.get("agg_mode", "metric"), "既存指標と同じ"),
            "|値|>=": float(r.get("threshold", 0.0)),
            "ビン幅": float(r.get("hist_bin", 0.2)),
            "係数(×)": float(r.get("scale", 1.0)),
            "加算(+)": float(r.get("offset", 0.0)),
        }
        for r in rows
    ]
    return pd.DataFrame(data, columns=_COLUMNS)


def _df_to_rows(df: pd.DataFrame) -> list[dict]:
    rows: list[dict] = []
    for _, row in df.iterrows():
        label = str(row.get("ラベル") or "").strip()
        table = str(row.get("テーブル") or "").strip()
        column = str(row.get("フィールド") or "").strip()
        if not (label and table and column):
            continue  # 3つ揃っていない行は無視
        rows.append(
            {
                "label": label,
                "table": table,
                "column": column,
                "agg_mode": _AGG_DISPLAY.get(str(row.get("集計") or "").strip(), "metric"),
                "threshold": _safe_float(row.get("|値|>="), 0.0),
                "hist_bin": _safe_float(row.get("ビン幅"), 0.2) or 0.2,
                "scale": _safe_float(row.get("係数(×)"), 1.0),
                "offset": _safe_float(row.get("加算(+)"), 0.0),
            }
        )
    return rows


def _safe_float(v, default: float = 0.0) -> float:
    try:
        if v is None or pd.isna(v):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def rows_to_custom_fields(rows: list[dict]) -> tuple[CustomField, ...]:
    """入力行（dict のリスト）を CustomField のタプルに変換する。"""
    out: list[CustomField] = []
    for i, r in enumerate(rows, start=1):
        out.append(
            CustomField(
                key=f"cf{i}",
                label=str(r["label"]),
                table=str(r["table"]),
                column=str(r["column"]),
                agg_mode=("timeseries" if r.get("agg_mode") == "timeseries" else "metric"),
                threshold=_safe_float(r.get("threshold"), 0.0),
                hist_bin=_safe_float(r.get("hist_bin"), 0.2) or 0.2,
                scale=_safe_float(r.get("scale"), 1.0),
                offset=_safe_float(r.get("offset"), 0.0),
            )
        )
    return tuple(out)


def render_custom_fields(state: AppState) -> tuple[CustomField, ...]:
    """自由フィールドの編集UI（表）を描画し、CustomField のタプルを返す。"""
    with st.expander("自由フィールド（任意テーブル×列）"):
        st.caption(
            "テーブル名・フィールド（列）名を指定すると、散布図/画像/地図/表/"
            "ヒストグラムを生成します。集計『既存指標と同じ』は自動運転中・1分窓ごとの"
            "|最大値|（X=移動距離）、『汎用時系列』は1秒平均（X=時刻）。"
            "緯度経度の無いテーブルは地図を自動でスキップします（反映には実行が必要）。"
        )
        st.caption(
            "表示値 = 取得値 × 係数(×) + 加算(+)。例: 係数 -1 で符号反転、係数 3.6 で m/s→km/h。"
            "しきい値・ヒストグラムのビン・最大値抽出も変換後の値で扱います。"
        )
        edited = st.data_editor(
            _rows_to_df(state.custom_field_rows),
            num_rows="dynamic",
            width="stretch",
            key="custom_fields_editor",
            column_config={
                "ラベル": st.column_config.TextColumn("ラベル", help="表示名（タブ・凡例）"),
                "テーブル": st.column_config.TextColumn("テーブル", help="データソース名"),
                "フィールド": st.column_config.TextColumn("フィールド", help="列名（例 .pose.angular_velocity_vrf.z）"),
                "集計": st.column_config.SelectboxColumn(
                    "集計", options=list(_AGG_DISPLAY), default="既存指標と同じ"
                ),
                "|値|>=": st.column_config.NumberColumn("|値|>=", help="既存指標と同じ集計時の下限", default=0.0),
                "ビン幅": st.column_config.NumberColumn("ビン幅", help="ヒストグラムのビン幅", default=0.2),
                "係数(×)": st.column_config.NumberColumn(
                    "係数(×)", help="取得値に掛ける係数（例: -1 で符号反転）", default=1.0
                ),
                "加算(+)": st.column_config.NumberColumn(
                    "加算(+)", help="係数を掛けた後に足す値", default=0.0
                ),
            },
        )
        state.custom_field_rows = _df_to_rows(edited)

    return rows_to_custom_fields(state.custom_field_rows)
