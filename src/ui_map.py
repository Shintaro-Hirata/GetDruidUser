# src/ui_map.py
"""地図プロット機能 — 散布図データの発生地点を folium で表示する。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

try:
    import folium
    from streamlit_folium import st_folium

    _HAS_FOLIUM = True
except ImportError:
    _HAS_FOLIUM = False


# -------------------------------------------------
# 色設定
# -------------------------------------------------
_COLOR_Q1 = "red"       # lateral_error
_COLOR_Q2 = "blue"      # acceleration


def _ensure_numeric(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """指定列を numeric に変換し NaN 行を落とす。"""
    df = df.copy()
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=cols)


def _build_popup_html(
    row: pd.Series,
    value_col: str,
    value_label: str,
    *,
    query_label: str = "",
    run_info: str = "",
) -> str:
    """マーカーのポップアップに表示する HTML を組み立てる。"""
    lat = row.get("latitude", "")
    lon = row.get("longitude", "")
    val = row.get(value_col, "")
    sec_time = row.get("sec_time_jst", row.get("sec_time", ""))

    lines = [
        f"<b>{value_label}</b>: {val}",
        f"<b>緯度</b>: {lat}",
        f"<b>経度</b>: {lon}",
        f"<b>発生時刻</b>: {sec_time}",
    ]
    if query_label:
        lines.append(f"<b>クエリ</b>: {query_label}")
    if run_info:
        lines.append(f"<b>条件</b>: {run_info}")
    return "<br>".join(lines)


def _add_markers(
    fg: folium.FeatureGroup,
    df: pd.DataFrame,
    value_col: str,
    value_label: str,
    color: str,
    *,
    query_label: str = "",
    run_info: str = "",
) -> None:
    """DataFrame の各行を CircleMarker として FeatureGroup に追加する。"""
    for _, row in df.iterrows():
        lat = row["latitude"]
        lon = row["longitude"]
        popup_html = _build_popup_html(
            row, value_col, value_label,
            query_label=query_label,
            run_info=run_info,
        )
        folium.CircleMarker(
            location=[lat, lon],
            radius=6,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.7,
            popup=folium.Popup(popup_html, max_width=320),
            tooltip=f"{value_label}: {row.get(value_col, '')}",
        ).add_to(fg)


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

    if not _HAS_FOLIUM:
        st.warning(
            "地図表示には folium と streamlit-folium が必要です。\n\n"
            "```\npip install folium streamlit-folium\n```"
        )
        return

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

    # どちらも非表示なら地図だけ表示
    if not (show_q1 and has_q1) and not (show_q2 and has_q2):
        st.info("表示するレイヤーが選択されていません。")
        return

    # --- 地図の中心を決定 ---
    all_lats: list[float] = []
    all_lons: list[float] = []
    if show_q1 and has_q1:
        all_lats.extend(df1["latitude"].tolist())
        all_lons.extend(df1["longitude"].tolist())
    if show_q2 and has_q2:
        all_lats.extend(df2["latitude"].tolist())
        all_lons.extend(df2["longitude"].tolist())

    center_lat = sum(all_lats) / len(all_lats)
    center_lon = sum(all_lons) / len(all_lons)

    m = folium.Map(location=[center_lat, center_lon], zoom_start=15)

    # --- Q1 マーカー ---
    if show_q1 and has_q1:
        fg1 = folium.FeatureGroup(name=f"Q1: lateral_error ({len(df1)}件)")
        _add_markers(
            fg1, df1,
            value_col="lateral_error",
            value_label="lateral_error [m]",
            color=_COLOR_Q1,
            query_label="Q1 (lateral_error)",
            run_info=run_info,
        )
        fg1.add_to(m)

    # --- Q2 マーカー ---
    if show_q2 and has_q2:
        fg2 = folium.FeatureGroup(name=f"Q2: acceleration ({len(df2)}件)")
        _add_markers(
            fg2, df2,
            value_col="acceleration",
            value_label="acceleration [m/s²]",
            color=_COLOR_Q2,
            query_label="Q2 (acceleration)",
            run_info=run_info,
        )
        fg2.add_to(m)

    # --- 地図全体がデータ範囲に収まるようにフィット ---
    m.fit_bounds([[min(all_lats), min(all_lons)], [max(all_lats), max(all_lons)]])

    # --- 凡例 ---
    legend_parts = []
    if show_q1 and has_q1:
        legend_parts.append('<span style="color:red;">&#9679;</span> Q1: lateral_error')
    if show_q2 and has_q2:
        legend_parts.append('<span style="color:blue;">&#9679;</span> Q2: acceleration')
    if legend_parts:
        legend_html = f'<div style="font-size:13px; margin-bottom:8px;">{"&nbsp;&nbsp;".join(legend_parts)}</div>'
        st.markdown(legend_html, unsafe_allow_html=True)

    st_folium(m, width=None, height=map_height, returned_objects=[])
