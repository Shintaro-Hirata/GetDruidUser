# src/ui_map.py
"""地図プロット機能 — 散布図データの発生地点を pydeck (WebGL) で高速表示する。"""
from __future__ import annotations

import pandas as pd
import pydeck as pdk
import streamlit as st


# -------------------------------------------------
# 色設定 (RGBA)
# -------------------------------------------------
_COLOR_Q1 = [220, 40, 40, 180]    # 赤 — lateral_error
_COLOR_Q2 = [40, 80, 220, 180]    # 青 — acceleration


def _ensure_numeric(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """指定列を numeric に変換し NaN 行を落とす。"""
    df = df.copy()
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=cols)


def _prepare_layer_df(
    df: pd.DataFrame,
    value_col: str,
    query_label: str,
    color: list[int],
    run_info: str,
) -> pd.DataFrame:
    """pydeck 用に列名を統一した DataFrame を返す。"""
    out = df[["latitude", "longitude"]].copy()
    out["value"] = df[value_col]
    out["time"] = df["sec_time_jst"] if "sec_time_jst" in df.columns else df.get("sec_time", "")
    out["query_label"] = query_label
    out["run_info"] = run_info
    out["color"] = [color] * len(out)
    return out


def _make_run_info(
    range_label: str,
    range_start: str,
    range_end: str,
    thr_lat: float | None = None,
    thr_acc: float | None = None,
) -> str:
    """実行条件の要約文字列を返す。"""
    parts = [
        f"期間: {range_start} 〜 {range_end}",
    ]
    if range_label:
        parts.insert(0, f"凡例: {range_label}")
    if thr_lat is not None:
        parts.append(f"閾値 lateral_error: {thr_lat}")
    if thr_acc is not None:
        parts.append(f"閾値 acceleration: {thr_acc}")
    return " / ".join(parts)


def show_map(
    df1: pd.DataFrame | None,
    df2: pd.DataFrame | None,
    *,
    range_label: str = "",
    range_start: str = "",
    range_end: str = "",
    thr_lat: float | None = None,
    thr_acc: float | None = None,
    map_height: int = 500,
) -> None:
    """Q1 (lateral_error) と Q2 (acceleration) の発生地点を地図にプロットする。"""

    st.markdown("### 地図: 発生地点プロット")

    run_info = _make_run_info(
        range_label, range_start, range_end,
        thr_lat=thr_lat, thr_acc=thr_acc,
    )

    # --- データ準備 ---
    has_q1 = df1 is not None and not df1.empty
    has_q2 = df2 is not None and not df2.empty

    if has_q1:
        df1 = _ensure_numeric(df1, ["latitude", "longitude", "lateral_error"])
        has_q1 = not df1.empty
    if has_q2:
        df2 = _ensure_numeric(df2, ["latitude", "longitude", "acceleration"])
        has_q2 = not df2.empty

    if not has_q1 and not has_q2:
        st.info("地図に表示できるデータがありません（緯度・経度が欠損）。")
        return

    # --- Q1/Q2 表示切り替えチェックボックス ---
    col_chk1, col_chk2, _ = st.columns([1, 1, 3])
    with col_chk1:
        show_q1 = st.checkbox(
            f"Q1: lateral_error ({len(df1) if has_q1 else 0}件)",
            value=True,
            key=f"map_q1_{range_label}_{range_start}",
        )
    with col_chk2:
        show_q2 = st.checkbox(
            f"Q2: acceleration ({len(df2) if has_q2 else 0}件)",
            value=True,
            key=f"map_q2_{range_label}_{range_start}",
        )

    if not (show_q1 and has_q1) and not (show_q2 and has_q2):
        st.info("表示するレイヤーが選択されていません。")
        return

    # --- レイヤー構築 ---
    layers: list[pdk.Layer] = []

    if show_q1 and has_q1:
        q1_data = _prepare_layer_df(df1, "lateral_error", "Q1 (lateral_error)", _COLOR_Q1, run_info)
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                data=q1_data,
                get_position=["longitude", "latitude"],
                get_color="color",
                get_radius=30,
                radius_min_pixels=4,
                radius_max_pixels=12,
                pickable=True,
                auto_highlight=True,
            )
        )

    if show_q2 and has_q2:
        q2_data = _prepare_layer_df(df2, "acceleration", "Q2 (acceleration)", _COLOR_Q2, run_info)
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                data=q2_data,
                get_position=["longitude", "latitude"],
                get_color="color",
                get_radius=30,
                radius_min_pixels=4,
                radius_max_pixels=12,
                pickable=True,
                auto_highlight=True,
            )
        )

    # --- ビューポート（全データが収まるように） ---
    all_lats: list[float] = []
    all_lons: list[float] = []
    if show_q1 and has_q1:
        all_lats.extend(df1["latitude"].tolist())
        all_lons.extend(df1["longitude"].tolist())
    if show_q2 and has_q2:
        all_lats.extend(df2["latitude"].tolist())
        all_lons.extend(df2["longitude"].tolist())

    view_state = pdk.ViewState(
        latitude=sum(all_lats) / len(all_lats),
        longitude=sum(all_lons) / len(all_lons),
        zoom=14,
        pitch=0,
    )

    # --- ツールチップ ---
    tooltip = {
        "html": (
            "<b>{query_label}</b><br>"
            "<b>値</b>: {value}<br>"
            "<b>緯度</b>: {latitude}<br>"
            "<b>経度</b>: {longitude}<br>"
            "<b>発生時刻</b>: {time}<br>"
            "<b>条件</b>: {run_info}"
        ),
        "style": {
            "backgroundColor": "rgba(0,0,0,0.75)",
            "color": "white",
            "fontSize": "13px",
            "padding": "8px",
        },
    }

    # --- 凡例 ---
    legend_parts = []
    if show_q1 and has_q1:
        legend_parts.append('<span style="color:rgb(220,40,40);">&#9679;</span> Q1: lateral_error')
    if show_q2 and has_q2:
        legend_parts.append('<span style="color:rgb(40,80,220);">&#9679;</span> Q2: acceleration')
    if legend_parts:
        legend_html = f'<div style="font-size:13px; margin-bottom:8px;">{"&nbsp;&nbsp;".join(legend_parts)}</div>'
        st.markdown(legend_html, unsafe_allow_html=True)

    # --- 描画 ---
    st.pydeck_chart(
        pdk.Deck(
            layers=layers,
            initial_view_state=view_state,
            tooltip=tooltip,
            map_style=pdk.map_styles.ROAD,
        ),
        height=map_height,
    )
